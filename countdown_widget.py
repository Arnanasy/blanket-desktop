"""独立可自由移动的桌面番茄钟 / 倒计时挂件：
- 平时完全隐身，开启倒计时后自动浮现，到点随机显示一句提示语并推送托盘通知；
- 左键按住可自由拖动到任意屏幕位置（跨分辨率相对锚点自动记忆）；
- Ctrl + 滚轮调字号大小，Shift + 滚轮调透明度；
- 右键支持暂停/继续、打开外观与提示语设置（字号、透明度、样式、文字/背景颜色、自定义随机结束语）与关闭；
- 游戏/窗口防误触开启时自动转为鼠标穿透，绝不抢点击。
"""
import random
import re
import time
import tkinter as tk
from tkinter import colorchooser, font as tkfont, ttk

from display_geometry import bounds
from quick_hub import format_countdown
from widget_layout import capture_anchor, clamp_to_work_area, resolve_anchor
from win32 import (user32, toplevel_hwnd, add_exstyle, set_click_through, place_on_desktop,
                   WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, MF_STRING, MF_SEPARATOR,
                   TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD, WM_NULL)

KEY_COLOR = '#010203'
DRAG_THRESHOLD = 5
DEFAULT_POS = [40, 200]
STYLES = ('圆角卡片', '极简透明')
DEFAULTS = dict(
    size=22,
    opacity=92,
    style='圆角卡片',
    fg='#F8FAFC',
    bg='#18202D',
    done_fg='#FACC15',
)

DEFAULT_FINISH_QUOTES = (
    '时间到啦，起来伸个懒腰喝口水吧～',
    '这一轮专注完成！看看远处放松眼睛 20 秒',
    '叮！休息一下，好灵感常常在走神时冒出来',
    '干得漂亮，活动一下肩膀和脖子吧',
    '倒计时结束，站起来走两步给大脑充个电',
    '本轮达成！记得喝口温水，眨眨眼睛',
    '辛苦啦，稍微喘口气再继续下一程',
    '铃声响啦，此刻最适合放空一分钟',
    '专注完成！离开屏幕看点远处的风景吧',
    '叮咚，你已经稳稳拿下这一段时间啦',
    '时间到！伸展双手，慢慢深呼吸三次',
    '小小里程碑达成，歇一会儿再出发',
)

_HEX_COLOR_RE = re.compile(r'#[0-9a-fA-F]{6}')


def sanitize_quotes(raw):
    """清洗自定义结束语列表（单句上限 48 字、最多 50 句；为空时回退内置默认句子）。"""
    if isinstance(raw, list):
        cleaned = [str(s).strip()[:48] for s in raw if isinstance(s, str) and s.strip()]
        if cleaned:
            return cleaned[:50]
    return list(DEFAULT_FINISH_QUOTES)


def pick_finish_quote(quotes, last_quote=''):
    """从结束语列表中随机抽取一句（多于 1 句时自动避开与上次重复）。"""
    pool = sanitize_quotes(quotes)
    if len(pool) > 1 and last_quote in pool:
        pool = [q for q in pool if q != last_quote]
    return random.choice(pool)


def sanitize_countdown_cfg(raw):
    cfg = DEFAULTS.copy()
    if isinstance(raw, dict):
        if isinstance(raw.get('size'), int) and not isinstance(raw.get('size'), bool):
            cfg['size'] = max(14, min(56, raw['size']))
        if isinstance(raw.get('opacity'), int) and not isinstance(raw.get('opacity'), bool):
            cfg['opacity'] = max(35, min(100, raw['opacity']))
        if raw.get('style') in STYLES:
            cfg['style'] = raw['style']
        for k in ('fg', 'bg', 'done_fg'):
            val = raw.get(k)
            if isinstance(val, str) and _HEX_COLOR_RE.fullmatch(val):
                cfg[k] = val
    return cfg


class CountdownWidget:
    def __init__(self, widgets, cfg, on_save):
        self.widgets = widgets
        self.root = widgets.root
        self.on_save = on_save
        cfg = cfg if isinstance(cfg, dict) else {}

        self.prefs = sanitize_countdown_cfg(cfg)
        self.quotes = sanitize_quotes(cfg.get('quotes'))
        pos = cfg.get('position')
        if not (isinstance(pos, list) and len(pos) == 2 and all(type(v) is int for v in pos)):
            pos = list(DEFAULT_POS)
        self.pos = list(pos)
        self.anchor = cfg.get('anchor') if isinstance(cfg.get('anchor'), dict) else None

        # 计时状态
        self.end_at = 0.0
        self.paused_rem = 0.0
        self.label = '专注'
        self.done_until = 0.0
        self._finish_msg = ''
        self._preview_until = 0.0
        self._preview_msg = ''
        self._last_text = ''
        self._last_size = (0, 0)
        self._last_alpha = None
        self._guarded = False
        self._font_key = None
        self._font = None

        # 拖动与设置窗状态
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False
        self.menu_open = False
        self.dialog = None
        self._dlg_vars = {}

        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=KEY_COLOR)
        self.win.wm_attributes('-transparentcolor', KEY_COLOR)

        self.canvas = tk.Canvas(self.win, width=180, height=48, bg=KEY_COLOR,
                                highlightthickness=0, cursor='fleur')
        self.canvas.pack()
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<ButtonRelease-3>', self._on_right_click)
        self.canvas.bind('<MouseWheel>', self._on_wheel)

        self.hwnd = toplevel_hwnd(self.win)
        add_exstyle(self.hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

    def config(self):
        return {
            'position': list(self.pos),
            'anchor': self.anchor,
            'quotes': list(self.quotes),
            **self.prefs,
        }

    # ------------------------------------------------------------ 计时控制
    @property
    def active(self):
        return self.end_at > 0.0 or self.paused_rem > 0.0

    def start(self, minutes, label='专注'):
        mins = max(1, min(360, int(minutes)))
        self.label = (label or '专注').strip()[:10]
        self.end_at = time.monotonic() + mins * 60.0
        self.paused_rem = 0.0
        self.done_until = 0.0
        self._preview_until = 0.0
        self._last_text = ''

    def toggle_pause(self):
        now = time.monotonic()
        if self.end_at > now:
            self.paused_rem = max(1.0, self.end_at - now)
            self.end_at = 0.0
        elif self.paused_rem > 0:
            self.end_at = now + self.paused_rem
            self.paused_rem = 0.0
        self._preview_until = 0.0
        self._last_text = ''

    def cancel(self):
        self.end_at = 0.0
        self.paused_rem = 0.0
        self.done_until = 0.0
        self._preview_until = 0.0
        self._last_text = ''
        if self.win.winfo_viewable():
            self.win.withdraw()

    def current_text(self, now=None):
        now_t = time.monotonic() if now is None else now
        if self._preview_until > now_t and self._preview_msg:
            return f'🔔 {self._preview_msg}'
        if self.paused_rem > 0:
            return format_countdown(self.paused_rem, f'{self.label}(暂停)')
        if self.end_at > 0:
            rem = self.end_at - now_t
            if rem > 0:
                return format_countdown(rem, self.label)
        if self.done_until > now_t:
            return f'🔔 {self._finish_msg or (self.label + "时间到！")}'
        return ''

    # ------------------------------------------------------------ 绘制与布局
    def _ensure_font(self, size_override=None):
        key = size_override or self.prefs['size']
        if key != self._font_key:
            self._font = tkfont.Font(family='Microsoft YaHei UI', size=key, weight='bold')
            self._font_key = key

    def _render(self, text):
        base_size = self.prefs['size']
        self._ensure_font(base_size)
        l, _t, r, _b = bounds(self.pos[0] + 40, self.pos[1] + 20, work=True)
        max_w = max(220, (r - l) - 24)
        pad_x = max(14, round(base_size * 0.75))
        pad_y = max(8, round(base_size * 0.42))
        tw = self._font.measure(text)
        # 超长句子在大字号下超出单屏工作区宽度时，自动自适应微缩字号以保证整句完整显示不溢出
        if tw + pad_x * 2 > max_w and base_size > 14:
            ratio = (max_w - pad_x * 2) / max(1, tw)
            fit_size = max(14, int(base_size * ratio))
            self._ensure_font(fit_size)
            pad_x = max(12, round(fit_size * 0.75))
            pad_y = max(7, round(fit_size * 0.42))
            tw = self._font.measure(text)
        th = self._font.metrics('linespace')
        w = min(max_w, max(120, tw + pad_x * 2))
        h = th + pad_y * 2

        alpha = self.prefs['opacity'] / 100.0
        if alpha != self._last_alpha:
            self._last_alpha = alpha
            self.win.attributes('-alpha', alpha)

        c = self.canvas
        c.configure(width=w, height=h)
        c.delete('all')

        is_done = text.startswith('🔔')
        fg = self.prefs['done_fg'] if is_done else self.prefs['fg']
        bg = self.prefs['bg']
        if self.prefs['style'] == '圆角卡片':
            rad = min(16, h // 2)
            c.create_polygon(
                rad, 0, w - rad, 0, w, 0, w, rad, w, h - rad, w, h,
                w - rad, h, rad, h, 0, h, 0, h - rad, 0, rad, 0, 0,
                smooth=True, splinesteps=20, fill=bg, outline='#475569',
            )
            c.create_text(w / 2, h / 2, text=text, font=self._font, fill=fg, anchor='center')
        else:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
                c.create_text(w / 2 + dx, h / 2 + dy, text=text, font=self._font, fill='#0b0f19', anchor='center')
            c.create_text(w / 2, h / 2, text=text, font=self._font, fill=fg, anchor='center')

        size_changed = (w, h) != self._last_size
        self._last_size = (w, h)
        return size_changed

    def relayout(self, on_desktop=False):
        if not self.win.winfo_viewable():
            return
        w, h = self._last_size if self._last_size[0] > 0 else (self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight())
        x, y = resolve_anchor(self.anchor, self.pos, w, h, margin=8)
        if self.anchor is not None:
            self.pos = [x, y]
        place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop)

    # ------------------------------------------------------------ 交互（自由拖动 / 滚轮 / 右键 / 设置窗）
    def _on_press(self, e):
        if self._guarded:
            return
        self._drag_origin = (e.x_root, e.y_root)
        self._drag_offset = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())
        self._dragging = False

    def _on_motion(self, e):
        if self._drag_origin is None or self._drag_offset is None:
            return
        if not self._dragging:
            if abs(e.x_root - self._drag_origin[0]) + abs(e.y_root - self._drag_origin[1]) <= DRAG_THRESHOLD:
                return
            self._dragging = True
        w, h = self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight()
        x, y = clamp_to_work_area(e.x_root - self._drag_offset[0],
                                  e.y_root - self._drag_offset[1], w, h, margin=8)
        self.pos = [x, y]
        place_on_desktop(self.hwnd, x, y, on_desktop=True)

    def _on_release(self, _e):
        was_drag = self._dragging
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False
        now = time.monotonic()
        if was_drag:
            w, h = self._last_size if self._last_size[0] > 0 else (self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight())
            self.anchor = capture_anchor(self.pos[0], self.pos[1], w, h)
            self.on_save()
        elif self._preview_until > now:
            self._preview_until = 0.0
            self._last_text = ''
        elif not self.active and self.done_until > now:
            # 到点提示语出现期间，轻点一下即可提前关闭
            self.done_until = 0.0
            self._last_text = ''

    def _on_wheel(self, event):
        if self._guarded or not event.delta:
            return
        direction = 1 if event.delta > 0 else -1
        if event.state & 4:  # Ctrl + 滚轮调大小
            val = max(14, min(56, self.prefs['size'] + direction * 2))
            self.prefs['size'] = val
            if self.dialog and self.dialog.winfo_exists() and 'size' in self._dlg_vars:
                self._dlg_vars['size'].set(val)
            self._last_text = ''
            self.widgets.queue_save()
            return 'break'
        if event.state & 1:  # Shift + 滚轮调透明度
            val = max(35, min(100, self.prefs['opacity'] + direction * 5))
            self.prefs['opacity'] = val
            if self.dialog and self.dialog.winfo_exists() and 'opacity' in self._dlg_vars:
                self._dlg_vars['opacity'].set(val)
            self._last_text = ''
            self.widgets.queue_save()
            return 'break'

    def _toggle_style(self):
        self.prefs['style'] = STYLES[1] if self.prefs['style'] == STYLES[0] else STYLES[0]
        if self.dialog and self.dialog.winfo_exists() and 'style' in self._dlg_vars:
            self._dlg_vars['style'].set(self.prefs['style'])
        self._last_text = ''
        self.on_save()

    def open_settings(self):
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        w = self.dialog = tk.Toplevel(self.root)
        w.title('番茄钟外观与提示语设置')
        ui = max(1.0, getattr(self.widgets, 'ui_scale', 1.0))
        w.geometry(f'{round(430 * ui)}x{round(480 * ui)}')
        w.minsize(380, 420)

        pane = ttk.Frame(w, padding=14)
        pane.pack(fill='both', expand=True)
        ttk.Label(pane, text='番茄钟外观 · 修改实时生效', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w', pady=(0, 8))

        vars_ = self._dlg_vars = {}

        def apply(*_args):
            self.prefs['size'] = int(vars_['size'].get())
            self.prefs['opacity'] = int(vars_['opacity'].get())
            self.prefs['style'] = vars_['style'].get()
            self.prefs = sanitize_countdown_cfg(self.prefs)
            self._last_text = ''
            self.widgets.queue_save()

        def row(lbl):
            f = ttk.Frame(pane)
            f.pack(fill='x', pady=3)
            ttk.Label(f, text=lbl, width=10).pack(side='left')
            return f

        for k, lbl, lo, hi in (('size', '字号大小', 14, 56), ('opacity', '不透明度%', 35, 100)):
            v = vars_[k] = tk.IntVar(value=self.prefs[k])
            tk.Scale(row(lbl), from_=lo, to=hi, orient='horizontal', variable=v,
                     command=apply, showvalue=True).pack(side='left', fill='x', expand=True)

        st_v = vars_['style'] = tk.StringVar(value=self.prefs['style'])
        cb = ttk.Combobox(row('背景样式'), textvariable=st_v, values=list(STYLES), state='readonly', width=18)
        cb.pack(side='left')
        cb.bind('<<ComboboxSelected>>', apply)

        def pick_col(key, title):
            col = colorchooser.askcolor(self.prefs[key], parent=w, title=title)[1]
            if col:
                self.prefs[key] = col
                apply()

        c_row = row('自定义颜色')
        ttk.Button(c_row, text='文字色', command=lambda: pick_col('fg', '选择倒计时文字颜色')).pack(side='left', padx=2)
        ttk.Button(c_row, text='卡片底色', command=lambda: pick_col('bg', '选择卡片背景颜色')).pack(side='left', padx=2)
        ttk.Button(c_row, text='到点高亮色', command=lambda: pick_col('done_fg', '选择到点提醒文字颜色')).pack(side='left', padx=2)

        ttk.Separator(pane, orient='horizontal').pack(fill='x', pady=8)
        ttk.Label(pane, text='倒计时结束随机提示语（每行一句，留空则使用默认句子）：').pack(anchor='w', pady=(0, 4))

        txt = tk.Text(pane, height=8, font=('Microsoft YaHei UI', 9), wrap='word')
        txt.pack(fill='both', expand=True)
        txt.insert('1.0', '\n'.join(self.quotes))

        def save_quotes():
            lines = txt.get('1.0', 'end').splitlines()
            self.quotes = sanitize_quotes(lines)
            self.on_save()

        def reset_all():
            self.prefs = DEFAULTS.copy()
            self.quotes = list(DEFAULT_FINISH_QUOTES)
            vars_['size'].set(self.prefs['size'])
            vars_['opacity'].set(self.prefs['opacity'])
            vars_['style'].set(self.prefs['style'])
            txt.delete('1.0', 'end')
            txt.insert('1.0', '\n'.join(self.quotes))
            self._last_text = ''
            self.on_save()

        def test_quote():
            save_quotes()
            self._preview_msg = pick_finish_quote(self.quotes, self._preview_msg or self._finish_msg)
            self._preview_until = time.monotonic() + 5.0
            self._last_text = ''

        def on_close():
            save_quotes()
            w.destroy()

        w.protocol('WM_DELETE_WINDOW', on_close)

        btns = ttk.Frame(pane)
        btns.pack(fill='x', pady=(8, 0))
        ttk.Button(btns, text='恢复默认', command=reset_all).pack(side='left')
        ttk.Button(btns, text='随机试看一句', command=test_quote).pack(side='left', padx=6)
        ttk.Button(btns, text='保存并关闭', command=on_close).pack(side='right')

    def _on_right_click(self, e):
        if self._guarded:
            return
        ID_PAUSE, ID_SETTINGS, ID_STYLE, ID_CANCEL = 4101, 4102, 4103, 4104
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        owner = getattr(getattr(self.widgets.app, 'tray', None), 'hwnd', None) or toplevel_hwnd(self.root)
        cmd = 0
        self.menu_open = True
        try:
            if self.active:
                lbl = '继续倒计时' if self.paused_rem > 0 else '暂停倒计时'
                user32.AppendMenuW(menu, MF_STRING, ID_PAUSE, lbl)
            user32.AppendMenuW(menu, MF_STRING, ID_SETTINGS, '外观与提示语设置…')
            next_style = STYLES[1] if self.prefs['style'] == STYLES[0] else STYLES[0]
            user32.AppendMenuW(menu, MF_STRING, ID_STYLE, f'快速切换为{next_style}')
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, ID_CANCEL, '关闭倒计时')
            user32.SetForegroundWindow(owner)
            cmd = user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                int(e.x_root), int(e.y_root), 0, owner, None,
            )
            user32.PostMessageW(owner, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)
            self.menu_open = False
            self.widgets.app.discard_mouse()

        if cmd == ID_PAUSE:
            self.toggle_pause()
        elif cmd == ID_SETTINGS:
            self.open_settings()
        elif cmd == ID_STYLE:
            self._toggle_style()
        elif cmd == ID_CANCEL:
            self.widgets.cancel_countdown()

    # ------------------------------------------------------------ 主循环调度
    def tick(self, now, blocked, on_desktop=False, force_restack=False, guard=False):
        finished_now = False
        finish_msg = ''
        if self.end_at > 0 and now >= self.end_at:
            self.end_at = 0.0
            self._finish_msg = pick_finish_quote(self.quotes, self._finish_msg)
            finish_msg = self._finish_msg
            self.done_until = now + 12.0
            self._last_text = ''
            finished_now = True

        if bool(guard) != self._guarded:
            self._guarded = bool(guard)
            set_click_through(self.hwnd, self._guarded)

        previewing = self._preview_until > now and bool(self._preview_msg)
        text = self.current_text(now)
        should_show = bool(text) and not blocked
        visible = bool(self.win.winfo_viewable())

        if should_show:
            size_changed = False
            if text != self._last_text:
                self._last_text = text
                size_changed = self._render(text)
            if not visible or force_restack or (size_changed and not self._dragging):
                w, h = self._last_size if self._last_size[0] > 0 else (self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight())
                x, y = resolve_anchor(self.anchor, self.pos, w, h, margin=8)
                if self.anchor is not None:
                    self.pos = [x, y]
                if not visible:
                    self.win.geometry(f'{x:+d}{y:+d}')
                place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop or previewing, show=not visible or force_restack)
                set_click_through(self.hwnd, self._guarded)
        elif visible:
            self.win.withdraw()

        return finished_now, text, finish_msg
