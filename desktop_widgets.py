"""桌面挂件总控：透明时钟 + 当地天气，并调度主题按钮、右侧启动栏与系统监控。

只依赖标准库；“按壁纸亮度配色”需要可选依赖 Pillow。
"""
from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import re
from threading import Thread
import time
import tkinter as tk
from tkinter import colorchooser, ttk, font as tkfont

from clock_extras import (DATE_ADDON_OPTIONS, DATE_FORMAT_PRESETS, DEFAULT_DATE_FORMAT,
                          format_clock_text, format_date_text)
from clock_face import ClockFace
from desktop_windows import invalidate_cache
from display_geometry import bounds
from launcher_bar import LauncherBar
from local_weather import refresh as refresh_weather, CACHE_SECONDS
from media_widget import MediaWidget
from preferences import save_config
from sys_monitor import SystemMonitor
from theme_button import ThemeButton
from wallpaper_clock import sample
from widget_layout import AnchorRegistry, capture_anchor, resolve_anchor
from win32 import (user32, dwmapi, RECT, GetWindowLongPtr, class_name, cursor_pos, toplevel_hwnd,
                   add_exstyle, place_on_desktop, GWL_STYLE, WS_CAPTION, DWMWA_EXTENDED_FRAME_BOUNDS,
                   HWND_BOTTOM, SW_SHOWNOACTIVATE, SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE,
                   WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
                   SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN)

KEY_COLOR = '#010203'          # 透明色键
TICK_MS = 150
THEME_CHECK_SECONDS = 30       # 系统强调色 / 深浅色的检查间隔
SNAP = 18                      # 拖动时钟时吸附到边缘的距离
SNAP_CENTER = 22               # 吸附到水平居中的距离
DRAG_THRESHOLD = 5             # 位移超过此值才视为拖动，避免单击/双击误触发吸附
DEFAULT_POSITION = [40, 70]


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def resolve_on_desktop(foreground, fg_class, prev_on_desktop=False):
    """根据当前前景窗口判断桌面挂件是否应置于桌面之上。

    - 前景为桌面（WorkerW / Progman）：返回 True；
    - 前景为任务栏或空句柄：沿用上一帧状态，避免软件最大化时点任务栏把时钟/音乐顶到最上层，
      也避免在桌面上点任务栏时挂件沉到壁纸下方；
    - 前景为普通软件窗口：返回 False，挂件沉底。
    """
    if fg_class in ('WorkerW', 'Progman'):
        return True
    if not foreground or fg_class in ('Shell_TrayWnd', 'Shell_SecondaryTrayWnd'):
        return bool(prev_on_desktop)
    return False


SHELL_CLASSES = ('Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd')
STYLE_DEFAULTS = dict(size=52, date_size=18, font='Segoe UI', color='#FFFFFF', background='#18202D',
                      shape='透明', layout='上下排列', bold=False, date=True, seconds=False,
                      hour12=False, date_format=DEFAULT_DATE_FORMAT, date_addon='无',
                      follow=False, opacity=100, padding=20)
STYLE_RANGES = [('size', 16, 160), ('date_size', 10, 60), ('opacity', 30, 100), ('padding', 5, 60)]


class Widgets:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.cfg = app.cfg.setdefault('widgets', {})
        self.face = ClockFace()
        self.accent = '#89B4FA'
        self.ui_scale = max(1, self.root.winfo_fpixels('1i') / 96)
        self.clock_on = tk.BooleanVar(value=self.cfg.get('clock', True))
        self.style = self._load_style()
        self.adaptive = tk.BooleanVar(value=self.cfg.get('adaptive', False))
        self.palette = None
        self.palettes = self._load_palettes()
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.sampling = None
        self.sample_again = False
        self.offset = None
        self._drag_origin = None
        self._dragging = False
        self.save_timer = None
        self.hint_timer = None
        self.control_vars = {}
        self.settings_window = None
        self.preview = None
        self._last_alpha = None
        self._font_choices = None

        # 天气
        self.weather_on = tk.BooleanVar(value=self.cfg.get('weather_enabled', True))
        cached = self.cfg.get('weather_cache')
        valid = isinstance(cached, dict) and isinstance(cached.get('line'), str) \
            and _is_number(cached.get('updated'))
        self.weather = cached if valid else {}
        self.weather_status = '等待更新'
        self.weather_job = None
        self.weather_due = 0
        self.weather_tip = None

        # 时钟窗口
        self.clock = tk.Toplevel(self.root)
        self.clock.withdraw()
        self.clock.overrideredirect(True)
        self.clock.configure(bg=KEY_COLOR)
        self.clock.wm_attributes('-transparentcolor', KEY_COLOR)
        self.clock_label = tk.Canvas(self.clock, bg=KEY_COLOR, highlightthickness=0)
        self.clock_label.pack()
        bind = self.clock_label.bind
        bind('<Enter>', self.show_weather_tip)
        bind('<Leave>', self.hide_weather_tip)
        bind('<ButtonPress-1>', self.drag_start)
        bind('<B1-Motion>', self.drag)
        bind('<ButtonRelease-1>', lambda e: self.finish_drag())
        bind('<Double-Button-1>', lambda e: self.app.toggle_theme())
        bind('<Button-3>', lambda e: self.settings())
        bind('<MouseWheel>', self.wheel)
        bind('<Button-2>', lambda e: self.adjust('seconds', not self.style['seconds']))
        self.clock_hwnd = toplevel_hwnd(self.clock)
        add_exstyle(self.clock_hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.draw_clock()

        # 其他挂件与统一锚点管理
        self.anchors = AnchorRegistry()
        self.theme_button = ThemeButton(self.root, self.cfg.get('theme_btn', {}), self.ui_scale,
                                        on_toggle=self.app.toggle_theme, on_settings=self.settings,
                                        on_save=self.save)
        self.bar = LauncherBar(self, self.cfg)
        self.monitor = SystemMonitor(self, bounds)
        self.media = MediaWidget(self, self.cfg.get('media', {}), on_save=self.save, on_settings=self.settings)
        self.anchors.register(self._relayout_clock)
        self.anchors.register(lambda: self.theme_button.place() if self.theme_button.visible() else None)
        self.anchors.register(self.media.relayout)
        self.last_theme = 0
        self.last_clock = ''
        self.last_desktop_foreground = None
        self._was_on_desktop = False
        self.last_position_check = 0
        self.root.after(TICK_MS, self.tick)

    # ------------------------------------------------------------ 配置
    def _load_style(self):
        style = STYLE_DEFAULTS.copy()
        saved = self.cfg.get('appearance', {})
        if isinstance(saved, dict):
            for k, v in saved.items():
                if k in STYLE_DEFAULTS and type(v) is type(STYLE_DEFAULTS[k]):
                    style[k] = v
        for k, lo, hi in STYLE_RANGES:
            style[k] = max(lo, min(hi, style[k]))
        for k in ('color', 'background'):
            try:
                self.root.winfo_rgb(style[k])
            except tk.TclError:
                style[k] = STYLE_DEFAULTS[k]
        return style

    def _load_palettes(self):
        palettes = {'dark': ['#F8FAFC', '#64748B'], 'light': ['#18202D', '#525866']}
        supplied = self.cfg.get('wallpaper_palettes', {})
        for mode in palettes:
            pair = supplied.get(mode) if isinstance(supplied, dict) else None
            if isinstance(pair, list) and len(pair) == 2 and \
                    all(isinstance(c, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', c) for c in pair):
                palettes[mode] = pair
        return palettes

    @property
    def busy(self):
        return self.bar.busy

    def hide_all(self):
        """退出时立即隐藏全部挂件和弹窗，做到点击即消失。"""
        self.hide_weather_tip()
        self.media.close()
        for win in (self.clock, self.theme_button.win, self.bar.win, self.monitor.win,
                    self.media.win, self.media.lyrics.win, self.media.dialog,
                    self.settings_window, self.monitor.dialog):
            if win is not None:
                try:
                    win.withdraw()
                except tk.TclError:
                    pass

    def save(self):
        if self.save_timer:
            self.root.after_cancel(self.save_timer)
            self.save_timer = None
        self.cfg['weather_enabled'] = self.weather_on.get()
        if self.weather:
            self.cfg['weather_cache'] = {k: self.weather[k] for k in
                                         ('line', 'updated', 'place', 'source', 'station', 'point')
                                         if k in self.weather}
        self.cfg.update(groups=self.bar.groups, clock=self.clock_on.get(), bar=self.bar.enabled.get(),
                        appearance=self.style, adaptive=self.adaptive.get(), recent=self.bar.recent,
                        wallpaper_palettes=self.palettes, theme_btn=self.theme_button.config(),
                        media=self.media.config())
        save_config(self.app.cfg)

    def queue_save(self):
        if self.save_timer:
            self.root.after_cancel(self.save_timer)
        self.save_timer = self.root.after(400, self.save)

    # ------------------------------------------------------------ 时钟外观
    def wheel(self, event):
        if not event.delta:
            return
        direction = 1 if event.delta > 0 else -1
        if event.state & 4:     # Ctrl + 滚轮调字号
            self.adjust('size', max(16, min(160, self.style['size'] + direction * 2)))
        else:
            self.adjust('opacity', max(30, min(100, self.style['opacity'] + direction * 5)))
        return 'break'

    def adjust(self, key, value):
        self.style[key] = value
        if self.settings_window and self.settings_window.winfo_exists() and key in self.control_vars:
            self.control_vars[key].set(value)
        self.redraw()
        label = {'opacity': f'透明度 {value}%', 'size': f'字号 {value}',
                 'seconds': '显示秒' if value else '隐藏秒'}[key]
        self.clock_label.create_text(4, 2, anchor='nw', text=label, fill='orange', tags='hint')
        if self.hint_timer:
            self.root.after_cancel(self.hint_timer)
        self.hint_timer = self.root.after(750, lambda: self.clock_label.delete('hint'))
        self.queue_save()

    def redraw(self):
        self.draw_clock()
        if self.preview and self.preview.winfo_exists():
            self.draw_clock(self.preview)

    def draw_clock(self, canvas=None):
        weather = ''
        if self.weather_on.get():
            weather = self.weather.get('line') or \
                ('天气暂不可用' if self.weather_status == '更新失败' else '正在获取当地天气…')
            city = self.weather.get('place')
            if city and city not in ('系统定位', '网络定位'):
                weather = f'{city} · {weather}'
            if self.weather and (self.weather_status == '更新失败'
                                 or not (0 <= time.time() - self.weather['updated'] <= 1800)):
                weather += '（上次）'
        palette = self.palettes.get(self.palette) if self.adaptive.get() else None
        self.face.draw(canvas or self.clock_label, self.style, self.accent, palette, weather)
        if canvas is None:
            alpha = self.style['opacity'] / 100
            if alpha != self._last_alpha:
                self._last_alpha = alpha
                self.clock.attributes('-alpha', alpha)

    # ------------------------------------------------------------ 壁纸取色
    def sample_wallpaper(self):
        if not self.adaptive.get():
            self.palette = None
            self.draw_clock()
            return
        if self.sampling:
            self.sample_again = True
            return
        x, y = self._saved_position()
        w, h = self.clock.winfo_reqwidth(), self.clock.winfo_reqheight()
        l, t, vw, vh = [user32.GetSystemMetrics(i) for i in
                        (SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN)]
        self.sampling = self.worker.submit(sample, (x, y, w, h), bounds(x + w // 2, y + h // 2),
                                           (l, t, l + vw, t + vh))
        self.root.after(80, self.finish_sample)

    def finish_sample(self):
        if not self.sampling.done():
            self.root.after(80, self.finish_sample)
            return
        try:
            self.palette = self.sampling.result() if self.adaptive.get() else None
        except Exception:
            self.palette = None
        self.sampling = None
        self.redraw()
        if self.sample_again:
            self.sample_again = False
            self.sample_wallpaper()

    # ------------------------------------------------------------ 天气
    def hide_weather_tip(self, event=None):
        if self.weather_tip:
            self.weather_tip.destroy()
            self.weather_tip = None

    def show_weather_tip(self, event):
        self.hide_weather_tip()
        if not self.weather_on.get():
            return
        details = self.weather_status
        if self.weather:
            try:
                stamp = time.strftime('%m-%d %H:%M', time.localtime(self.weather['updated']))
            except (OSError, OverflowError, ValueError, TypeError):
                stamp = '未知时间'
            details = f"{self.weather.get('place', '')} · {stamp} 更新 · {details}"
        self.weather_tip = tk.Toplevel(self.clock)
        self.weather_tip.withdraw()
        self.weather_tip.overrideredirect(True)
        self.weather_tip.attributes('-topmost', True)
        source = self.weather.get('source')
        tip_text = f'{details} · {source}' if source else details
        tk.Label(self.weather_tip, text=tip_text, padx=5, pady=2).pack()
        self.weather_tip.update_idletasks()
        tw, th = self.weather_tip.winfo_reqwidth(), self.weather_tip.winfo_reqheight()
        l, t, r, b = bounds(event.x_root, event.y_root, work=True)
        tx = max(l, min(event.x_root + 15, r - tw - 4))
        ty = max(t, min(event.y_root + 20, b - th - 4))
        self.weather_tip.geometry(f'+{tx}+{ty}')
        self.weather_tip.deiconify()
        self.weather_tip.after(2200, self.hide_weather_tip)

    def update_weather(self, now):
        if self.weather_job and self.weather_job.done():
            try:
                result = self.weather_job.result()
                if self.weather_on.get():
                    self.weather = result
                    self.weather_status = '当地天气'
                    self.save()
                # 与天气缓存有效期对齐：缓存过期时再去刷新，避免空跑
                updated = result.get('updated', 0)
                age = time.time() - updated if _is_number(updated) else CACHE_SECONDS
                delay = max(60, min(CACHE_SECONDS, CACHE_SECONDS - age))
            except Exception:
                self.weather_status = '更新失败'
                delay = 120
            self.weather_job = None
            self.weather_due = now + delay
            self.last_clock = ''
        if self.weather_on.get() and self.weather_job is None and now >= self.weather_due:
            self.weather_status = '正在更新'
            future = Future()
            self.weather_job = future
            previous = self.weather.copy()

            def fetch():
                try:
                    future.set_result(refresh_weather(previous))
                except Exception as error:
                    future.set_exception(error)

            Thread(target=fetch, name='weather', daemon=True).start()

    def toggle_weather(self):
        self.weather_due = 0
        self.last_clock = ''
        self.hide_weather_tip()
        self.save()

    # ------------------------------------------------------------ 设置窗口
    def settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        w = tk.Toplevel(self.root)
        self.settings_window = w
        w.title('时钟与桌面外观')
        w.geometry(f'{round(610 * self.ui_scale)}x{min(round(700 * self.ui_scale), w.winfo_screenheight() - 60)}')
        w.minsize(520, 550)
        toolbar = ttk.Frame(w)
        toolbar.pack(fill='x', padx=16, pady=(8, 0))
        ttk.Button(toolbar, text='音乐胶囊设置…', command=self.media.open_settings).pack(side='left')
        ttk.Button(toolbar, text='退出 Blanket', command=self.app.quit_app).pack(side='right')
        pane = ttk.Frame(w, padding=16)
        pane.pack(fill='both', expand=True)
        ttk.Label(pane, text='时钟外观 · 修改立即生效', font=('Microsoft YaHei UI', 14)).pack(anchor='w')
        box = ttk.Frame(pane)
        box.pack(fill='x', pady=12)
        variables = self.control_vars = {}

        def apply(*_args):
            for key, var in variables.items():
                self.style[key] = var.get()
            self.redraw()
            self.queue_save()

        def row(label):
            f = ttk.Frame(box)
            f.pack(fill='x', pady=3)
            ttk.Label(f, text=label, width=12).pack(side='left')
            return f

        for key, label, lo, hi in [('size', '时间字号', 16, 160), ('date_size', '日期字号', 10, 60),
                                   ('padding', '留白大小', 5, 60), ('opacity', '不透明度', 30, 100)]:
            var = variables[key] = tk.IntVar(value=self.style[key])
            tk.Scale(row(label), from_=lo, to=hi, orient='horizontal', variable=var, command=apply,
                     showvalue=True, length=340).pack(side='left', fill='x', expand=True)

        if self._font_choices is None:
            all_families = sorted(set(tkfont.families()))
            preferred = ['Bahnschrift', 'Segoe UI Variable Display', 'Segoe UI', 'Microsoft YaHei UI', 'Consolas', 'Arial']
            self._font_choices = [f for f in preferred if f in all_families] + [f for f in all_families if f not in preferred]
        for key, label, values in [('font', '字体', self._font_choices),
                                   ('shape', '形状', ['透明', '矩形卡片', '圆角卡片', '圆形']),
                                   ('layout', '排列', ['上下排列', '左右排列']),
                                   ('date_addon', '日期彩蛋', list(DATE_ADDON_OPTIONS))]:
            var = variables[key] = tk.StringVar(value=self.style[key])
            cb = ttk.Combobox(row(label), textvariable=var, values=values, state='readonly', width=30)
            cb.pack(side='left')
            cb.bind('<<ComboboxSelected>>', apply)

        fmt_var = variables['date_format'] = tk.StringVar(value=self.style['date_format'])
        fmt_cb = ttk.Combobox(row('日期格式'), textvariable=fmt_var, values=list(DATE_FORMAT_PRESETS), width=30)
        fmt_cb.pack(side='left')
        fmt_cb.bind('<<ComboboxSelected>>', apply)
        fmt_cb.bind('<Return>', apply)
        fmt_cb.bind('<FocusOut>', apply)

        def choose(key):
            color = colorchooser.askcolor(self.style[key], parent=w, title='选择颜色')[1]
            if color:
                self.style[key] = color
                if key == 'color':
                    variables['follow'].set(False)
                apply()

        colors = row('颜色')
        ttk.Button(colors, text='文字颜色', command=lambda: choose('color')).pack(side='left', padx=3)
        ttk.Button(colors, text='背景颜色', command=lambda: choose('background')).pack(side='left', padx=3)
        checks = ttk.Frame(box)
        checks.pack(fill='x', pady=7)
        for key, title in [('bold', '加粗'), ('date', '显示日期'), ('seconds', '显示秒'),
                           ('hour12', '12小时制'), ('follow', '跟随系统颜色')]:
            var = variables[key] = tk.BooleanVar(value=self.style[key])
            ttk.Checkbutton(checks, text=title, variable=var, command=apply).pack(side='left')

        def toggle_adaptive():
            self.sample_wallpaper()
            self.save()

        ttk.Checkbutton(box, text='透明时钟按壁纸亮度配色', variable=self.adaptive,
                        command=toggle_adaptive).pack(anchor='w')
        switches = ttk.Frame(pane)
        switches.pack(fill='x')
        for text, var, command in [('当地天气', self.weather_on, self.toggle_weather),
                                   ('图标隐藏时显示时钟', self.clock_on, self.save),
                                   ('桌面音乐胶囊', self.media.enabled, self.save),
                                   ('启用右侧启动栏', self.bar.enabled, self.save),
                                   ('主题按钮始终置顶', self.theme_button.always, self.save)]:
            ttk.Checkbutton(switches, text=text, variable=var, command=command).pack(side='left')
        ttk.Label(pane, text='图标隐藏时才显示时钟 · 可直接拖动 · 双击时钟或点 ☀/☾ 按钮一键切换深浅主题（右键设置）'
                  ).pack(anchor='w', pady=(10, 4))
        frame = ttk.Frame(pane)
        frame.pack(fill='both', expand=True)
        viewport = tk.Canvas(frame, bg='#334155', highlightthickness=0)
        viewport.grid(row=0, column=0, sticky='nsew')
        sx = ttk.Scrollbar(frame, orient='horizontal', command=viewport.xview)
        sx.grid(row=1, column=0, sticky='ew')
        sy = ttk.Scrollbar(frame, command=viewport.yview)
        sy.grid(row=0, column=1, sticky='ns')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        viewport.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.preview = tk.Canvas(viewport, bg='#334155', highlightthickness=0)
        viewport.create_window(0, 0, window=self.preview, anchor='nw')
        self.preview.bind('<Configure>', lambda e: viewport.configure(scrollregion=viewport.bbox('all')))
        self.draw_clock(self.preview)
        ttk.Button(pane, text='完成（已自动保存）', command=w.destroy).pack(pady=(10, 0))

    # ------------------------------------------------------------ 时钟位置
    def _saved_position(self):
        pos = self.cfg.get('position')
        if isinstance(pos, list) and len(pos) == 2 and all(type(v) is int for v in pos):
            return pos
        return list(DEFAULT_POSITION)

    def place_clock(self, x, y, snap=False):
        width, height = self.clock.winfo_reqwidth(), self.clock.winfo_reqheight()
        # 用窗口中心点查询所在显示器，避免多屏向右拖动时被主屏右边界卡住
        l, t, r, b = bounds(x + width // 2, y + height // 2, work=True)
        x = max(l, min(x, r - width))
        y = max(t, min(y, b - height))
        if snap:
            if abs(x - l) < SNAP:
                x = l
            elif abs(x + width - r) < SNAP:
                x = max(l, r - width)
            elif abs(x + width // 2 - (l + r) // 2) < SNAP_CENTER:
                x = (l + r) // 2 - width // 2
            if abs(y - t) < SNAP:
                y = t
            elif abs(y + height - b) < SNAP:
                y = max(t, b - height)
        user32.SetWindowPos(self.clock_hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        self.cfg['position'] = [x, y]

    def drag_start(self, e):
        self._drag_origin = (e.x_root, e.y_root)
        self._dragging = False
        self.offset = (e.x_root - self.clock.winfo_x(), e.y_root - self.clock.winfo_y())

    def drag(self, e):
        if self.offset is None or self._drag_origin is None:
            return
        if not self._dragging:
            dx = abs(e.x_root - self._drag_origin[0])
            dy = abs(e.y_root - self._drag_origin[1])
            if dx + dy <= DRAG_THRESHOLD:
                return
            self._dragging = True
        self.place_clock(e.x_root - self.offset[0], e.y_root - self.offset[1])

    def finish_drag(self):
        was_dragging = self._dragging
        self.offset = None
        self._drag_origin = None
        self._dragging = False
        if not was_dragging:
            return
        self.place_clock(self.clock.winfo_x(), self.clock.winfo_y(), snap=True)
        pos = self._saved_position()
        self.cfg['anchor'] = capture_anchor(pos[0], pos[1], self.clock.winfo_reqwidth(), self.clock.winfo_reqheight())
        user32.SetWindowPos(self.clock_hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        self.save()
        self.sample_wallpaper()

    def _relayout_clock(self):
        width, height = self.clock.winfo_reqwidth(), self.clock.winfo_reqheight()
        x, y = resolve_anchor(self.cfg.get('anchor'), self._saved_position(), width, height)
        self.cfg['position'] = [x, y]
        if self.clock.winfo_viewable():
            user32.SetWindowPos(self.clock_hwnd, HWND_BOTTOM, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

    def _show_clock(self, on_desktop=False):
        width, height = self.clock.winfo_reqwidth(), self.clock.winfo_reqheight()
        x, y = resolve_anchor(self.cfg.get('anchor'), self._saved_position(), width, height)
        self.cfg['position'] = [x, y]
        self.clock.geometry(f'{x:+d}{y:+d}')
        place_on_desktop(self.clock_hwnd, x, y, on_desktop=on_desktop, show=True)

    def fullscreen_active(self, hwnd, name):
        if not hwnd or name in SHELL_CLASSES:
            return False
        # 带标题栏的普通最大化窗口不算全屏（避免在自动隐藏任务栏或无任务栏副屏上误判）
        if (GetWindowLongPtr(hwnd, GWL_STYLE) & WS_CAPTION) == WS_CAPTION:
            return False
        rect = RECT()
        # 优先取 DWM 真实可见边界，排除 7~8px 的隐形缩放阴影边框
        hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                          ctypes.byref(rect), ctypes.sizeof(rect))
        if hr != 0 and not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        l, t, r, b = bounds((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        return rect.left <= l and rect.top <= t and rect.right >= r and rect.bottom >= b

    # ------------------------------------------------------------ 主题
    def theme(self):
        """同步壁纸配色、系统强调色与深浅色图标。"""
        self.sample_wallpaper()
        color = wintypes.DWORD()
        opaque = wintypes.BOOL()
        if dwmapi.DwmGetColorizationColor(ctypes.byref(color), ctypes.byref(opaque)) == 0:
            rgb = [(color.value >> shift) & 255 for shift in (16, 8, 0)]
            accent = '#' + ''.join(f'{max(120, c):02x}' for c in rgb)
            if accent != self.accent:
                self.accent = accent
                self.draw_clock()
                self.bar.recolor(accent)
        self.refresh_theme_button()

    def refresh_theme_button(self):
        dark = self.theme_button.refresh()
        self.bar.set_theme_label(dark)
        self.app.sync_theme(dark)

    def reset_refresh(self):
        """系统恢复或显示器变化后，触发下一帧立刻刷新主题、位置和时钟。"""
        self.last_theme = 0
        self.last_position_check = 0
        self.last_clock = ''
        self.anchors.relayout_all()

    # ------------------------------------------------------------ 主循环
    def tick(self):
        try:
            self._tick()
        finally:
            # 即使本轮出错也继续调度，避免一次异常让时钟和挂件全部停摆
            if not self.app._quitting:
                self.root.after(TICK_MS, self.tick)

    def _tick(self):
        now = time.monotonic()
        p = cursor_pos()
        self.update_weather(now)
        if now - self.last_theme > THEME_CHECK_SECONDS:
            self.theme()
            self.last_theme = now
        text = (
            format_clock_text(seconds=bool(self.style['seconds']), hour12=bool(self.style['hour12']))
            + '|' + format_date_text(fmt=self.style['date_format'], addon=self.style['date_addon'])
        )
        if text != self.last_clock:
            self.redraw()
            self.last_clock = text

        foreground = user32.GetForegroundWindow()
        name = class_name(foreground)
        desktop_foreground = foreground if name in ('WorkerW', 'Progman') else None
        if desktop_foreground and desktop_foreground != self.last_desktop_foreground:
            invalidate_cache()       # 桌面窗口可能被重建，重新查找
        self.last_desktop_foreground = desktop_foreground
        on_desktop = resolve_on_desktop(foreground, name, self._was_on_desktop)
        restack = (on_desktop != self._was_on_desktop) or bool(user32.IsIconic(self.clock_hwnd))
        self._was_on_desktop = on_desktop
        blocked = self.fullscreen_active(foreground, name)
        icons = self.app.desktop_icons_visible()

        # 时钟：仅在图标隐藏、且没有全屏程序时显示；退回桌面或 Win+D 时自动拉回桌面之上
        visible = bool(self.clock.winfo_viewable())
        if self.clock_on.get() and icons is False and not blocked:
            if not visible:
                self._show_clock(on_desktop=on_desktop)
            elif restack and self.offset is None:
                place_on_desktop(self.clock_hwnd, on_desktop=on_desktop, show=True)
            elif self.offset is None and now - self.last_position_check > 1:
                # 显示器布局变化后把时钟拉回可见区域；拖动中不干预
                self.place_clock(self.clock.winfo_x(), self.clock.winfo_y())
                self.last_position_check = now
        elif visible:
            self.clock.withdraw()

        theme_edge = self.theme_button.tick(now, p, blocked, icons)
        monitor = bounds(p.x, p.y)
        monitor_edge = self.monitor.tick(now, p.x, p.y, monitor, blocked, self.bar.enabled.get(),
                                         suppress_edge=theme_edge, foreground=foreground)
        self.media.tick(now, blocked, icons, on_desktop=on_desktop, force_restack=restack)
        bar_opened = self.bar.tick(now, p, monitor, blocked, monitor_edge, theme_edge)
        if bar_opened:
            self.theme_button.raise_()
