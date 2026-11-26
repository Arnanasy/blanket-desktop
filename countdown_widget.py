"""独立可自由移动的桌面番茄钟 / 倒计时挂件：
- 平时完全隐身，开启倒计时后自动浮现，到点醒目提示 8 秒并推送托盘通知后自动隐藏；
- 左键按住可自由拖动到任意屏幕位置（跨分辨率相对锚点自动记忆）；
- Ctrl + 滚轮调字号大小，Shift + 滚轮调透明度，右键菜单支持暂停/继续、切换卡片/透明样式与取消；
- 游戏/窗口防误触开启时自动转为鼠标穿透，绝不抢点击。
"""
import time
import tkinter as tk
from tkinter import font as tkfont

from quick_hub import format_countdown
from widget_layout import capture_anchor, clamp_to_work_area, resolve_anchor
from win32 import (toplevel_hwnd, add_exstyle, set_click_through, place_on_desktop,
                   WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW)

KEY_COLOR = '#010203'
DRAG_THRESHOLD = 5
DEFAULT_POS = [40, 200]
STYLES = ('圆角卡片', '极简透明')
DEFAULTS = dict(size=22, opacity=92, style='圆角卡片')


def sanitize_countdown_cfg(raw):
    cfg = DEFAULTS.copy()
    if isinstance(raw, dict):
        if isinstance(raw.get('size'), int) and not isinstance(raw.get('size'), bool):
            cfg['size'] = max(14, min(56, raw['size']))
        if isinstance(raw.get('opacity'), int) and not isinstance(raw.get('opacity'), bool):
            cfg['opacity'] = max(35, min(100, raw['opacity']))
        if raw.get('style') in STYLES:
            cfg['style'] = raw['style']
    return cfg


class CountdownWidget:
    def __init__(self, widgets, cfg, on_save):
        self.widgets = widgets
        self.root = widgets.root
        self.on_save = on_save
        cfg = cfg if isinstance(cfg, dict) else {}

        self.prefs = sanitize_countdown_cfg(cfg)
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
        self._last_text = ''
        self._last_alpha = None
        self._guarded = False
        self._font_key = None
        self._font = None
        self._hint_timer = None

        # 拖动状态
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False

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
        self._last_text = ''

    def toggle_pause(self):
        now = time.monotonic()
        if self.end_at > now:
            self.paused_rem = max(1.0, self.end_at - now)
            self.end_at = 0.0
        elif self.paused_rem > 0:
            self.end_at = now + self.paused_rem
            self.paused_rem = 0.0
        self._last_text = ''

    def cancel(self):
        self.end_at = 0.0
        self.paused_rem = 0.0
        self.done_until = 0.0
        self._last_text = ''
        if self.win.winfo_viewable():
            self.win.withdraw()

    def current_text(self, now=None):
        now_t = time.monotonic() if now is None else now
        if self.paused_rem > 0:
            return format_countdown(self.paused_rem, f'{self.label}(暂停)')
        if self.end_at > 0:
            rem = self.end_at - now_t
            if rem > 0:
                return format_countdown(rem, self.label)
        if self.done_until > now_t:
            return f'🔔 {self.label}时间到！'
        return ''

    # ------------------------------------------------------------ 绘制与布局
    def _ensure_font(self):
        key = self.prefs['size']
        if key != self._font_key:
            self._font = tkfont.Font(family='Microsoft YaHei UI', size=key, weight='bold')
            self._font_key = key

    def _render(self, text):
        self._ensure_font()
        pad_x = max(14, round(self.prefs['size'] * 0.75))
        pad_y = max(8, round(self.prefs['size'] * 0.42))
        tw = self._font.measure(text)
        th = self._font.metrics('linespace')
        w = max(120, tw + pad_x * 2)
        h = th + pad_y * 2

        alpha = self.prefs['opacity'] / 100.0
        if alpha != self._last_alpha:
            self._last_alpha = alpha
            self.win.attributes('-alpha', alpha)

        c = self.canvas
        c.configure(width=w, height=h)
        c.delete('all')

        is_done = text.startswith('🔔')
        fg = '#FACC15' if is_done else '#F8FAFC'
        if self.prefs['style'] == '圆角卡片':
            r = min(16, h // 2)
            bg = '#78350F' if is_done else '#18202D'
            c.create_polygon(
                r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h,
                w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0,
                smooth=True, splinesteps=20, fill=bg, outline='#334155',
            )
            c.create_text(w / 2, h / 2, text=text, font=self._font, fill=fg, anchor='center')
        else:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
                c.create_text(w / 2 + dx, h / 2 + dy, text=text, font=self._font, fill='#0b0f19', anchor='center')
            c.create_text(w / 2, h / 2, text=text, font=self._font, fill=fg, anchor='center')

    def relayout(self, on_desktop=False):
        if not self.win.winfo_viewable():
            return
        w, h = self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight()
        x, y = resolve_anchor(self.anchor, self.pos, w, h, margin=8)
        self.pos = [x, y]
        place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop)

    # ------------------------------------------------------------ 交互（自由拖动 / 滚轮 / 右键）
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
        if was_drag:
            w, h = self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight()
            self.anchor = capture_anchor(self.pos[0], self.pos[1], w, h)
            self.on_save()

    def _on_wheel(self, event):
        if self._guarded or not event.delta:
            return
        direction = 1 if event.delta > 0 else -1
        if event.state & 4:  # Ctrl + 滚轮调大小
            self.prefs['size'] = max(14, min(56, self.prefs['size'] + direction * 2))
            self._last_text = ''
            self.widgets.queue_save()
            return 'break'
        if event.state & 1:  # Shift + 滚轮调透明度
            self.prefs['opacity'] = max(35, min(100, self.prefs['opacity'] + direction * 5))
            self._last_text = ''
            self.widgets.queue_save()
            return 'break'

    def _toggle_style(self):
        self.prefs['style'] = STYLES[1] if self.prefs['style'] == STYLES[0] else STYLES[0]
        self._last_text = ''
        self.on_save()

    def _on_right_click(self, e):
        if self._guarded:
            return
        menu = tk.Menu(self.win, tearoff=0)
        if self.active:
            menu.add_command(label='继续倒计时' if self.paused_rem > 0 else '暂停倒计时',
                             command=self.toggle_pause)
        next_style = STYLES[1] if self.prefs['style'] == STYLES[0] else STYLES[0]
        menu.add_command(label=f'切换为{next_style}', command=self._toggle_style)
        menu.add_separator()
        menu.add_command(label='关闭倒计时', command=self.widgets.cancel_countdown)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------ 主循环调度
    def tick(self, now, blocked, on_desktop=False, force_restack=False, guard=False):
        finished_now = False
        if self.end_at > 0 and now >= self.end_at:
            self.end_at = 0.0
            self.done_until = now + 8.0
            self._last_text = ''
            finished_now = True

        if bool(guard) != self._guarded:
            self._guarded = bool(guard)
            set_click_through(self.hwnd, self._guarded)

        text = self.current_text(now)
        should_show = bool(text) and not blocked
        visible = bool(self.win.winfo_viewable())

        if should_show:
            if text != self._last_text:
                self._last_text = text
                self._render(text)
            if not visible or force_restack:
                w, h = self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight()
                x, y = resolve_anchor(self.anchor, self.pos, w, h, margin=8)
                self.pos = [x, y]
                if not visible:
                    self.win.geometry(f'{x:+d}{y:+d}')
                place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop, show=True)
                set_click_through(self.hwnd, self._guarded)
        elif visible:
            self.win.withdraw()

        return finished_now, text
