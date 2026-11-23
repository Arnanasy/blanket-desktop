"""贴在屏幕右边缘的 ☀/☾ 主题切换小按钮：平时半隐藏，鼠标靠近滑出置顶，可上下拖动。"""
from fractions import Fraction
from pathlib import Path
import time
import tkinter as tk

from display_geometry import bounds
from theme_switch import is_dark
from win32 import (user32, toplevel_hwnd, add_exstyle, HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE,
                   SWP_NOACTIVATE, SWP_SHOWWINDOW, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW)

KEY_COLOR = '#010203'      # 透明色键
BASE_SIZE = 48             # 图片原始尺寸（100% 缩放）
PEEK = 6                   # 平时贴边半隐藏露出的细边宽度
MARGIN = 6                 # 展开时距离屏幕右边缘的距离
EDGE_GAP = 8               # 与工作区上下边缘的最小距离
HOVER_LINGER = 0.35        # 鼠标离开后多久收回
CLICK_SLOP = 6             # 按下到松开位移不超过此值视为点击
DRAG_START = 5             # 纵向位移超过此值开始拖动


class ThemeButton:
    def __init__(self, root, cfg, ui_scale, on_toggle, on_settings, on_save):
        self.root = root
        self.on_toggle = on_toggle
        self.on_save = on_save
        cfg = cfg if isinstance(cfg, dict) else {}
        self.always = tk.BooleanVar(value=bool(cfg.get('always', True)))
        pos = cfg.get('position')
        if not (isinstance(pos, list) and len(pos) == 2 and all(type(v) is int for v in pos)):
            pos = [max(0, root.winfo_screenwidth() - 54), 100]
        self.pos = list(pos)
        self.size = round(BASE_SIZE * ui_scale)
        self.cur_x = self.target_x = float(pos[0])
        self.cur_y = float(pos[1])
        self.expanded = False
        self.last_hover = 0.0
        self.animating = False
        self.dark = None
        self._press_pt = None
        self._press_y = self.cur_y
        self._dragging = False

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.geometry(f'{self.size}x{self.size}+0+0')
        self.win.configure(cursor='hand2', bg=KEY_COLOR)
        self.win.attributes('-transparentcolor', KEY_COLOR, '-topmost', True)
        self.images = self._load_images()
        self.label = tk.Label(self.win, bg=KEY_COLOR, fg='#F59E0B', borderwidth=0, highlightthickness=0,
                              font=('Segoe UI Symbol', max(12, self.size // 2)))
        self.label.pack(fill='both', expand=True)
        self.win.bind('<Enter>', self._on_enter)
        self.win.bind('<ButtonPress-1>', self._on_press)
        self.win.bind('<B1-Motion>', self._on_motion)
        self.win.bind('<ButtonRelease-1>', self._on_release)
        self.win.bind('<ButtonRelease-3>', lambda e: on_settings())
        self.hwnd = toplevel_hwnd(self.win)
        add_exstyle(self.hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.refresh(force=True)

    def _load_images(self):
        """加载日/月图标；图片缺失或损坏时返回空字典，退回文字符号显示。"""
        images = {}
        try:
            for dark, name in ((True, 'theme-moon.png'), (False, 'theme-sun.png')):
                images[dark] = tk.PhotoImage(master=self.root, file=str(Path(__file__).with_name(name)))
        except tk.TclError:
            return {}
        ratio = Fraction(self.size, BASE_SIZE).limit_denominator(8)
        if ratio.numerator != ratio.denominator:
            images = {k: im.zoom(ratio.numerator).subsample(ratio.denominator) for k, im in images.items()}
        return images

    def config(self):
        return {'always': self.always.get(), 'position': self.pos}

    def visible(self):
        return bool(self.win.winfo_viewable())

    # ------------------------------------------------------------ 外观
    def refresh(self, force=False):
        """按系统深浅色更新图标，返回当前是否为深色。"""
        dark = is_dark()
        if force or dark != self.dark:
            self.dark = dark
            if self.images:
                self.label.configure(image=self.images[dark], text='')
            else:
                self.label.configure(image='', text='☾' if dark else '☀')
        return dark

    def raise_(self):
        """保持在启动栏 / 监控等置顶窗口之上，不改变位置和焦点。"""
        if self.visible():
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    # ------------------------------------------------------------ 位置与动画
    def _move(self, extra=0):
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, round(self.cur_x), round(self.cur_y),
                            self.size, self.size, SWP_NOSIZE | SWP_NOACTIVATE | extra)

    def _edges(self):
        l, t, r, b = bounds(int(self.cur_x), int(self.cur_y), work=True)
        return t, r, b, r - PEEK, r - self.size - MARGIN

    def place(self):
        t, r, b, x_hidden, x_shown = self._edges()
        self.cur_y = max(t + EDGE_GAP, min(self.pos[1], b - self.size - EDGE_GAP))
        self.cur_x = self.target_x = x_shown if self.expanded else x_hidden
        # 横坐标也一并记住，多显示器下重启后仍回到同一块屏幕
        self.pos[:] = [int(self.cur_x), int(self.cur_y)]
        self._move(SWP_SHOWWINDOW)

    def _expand(self):
        self.expanded = True
        self.target_x = self._edges()[4]
        self.last_hover = time.monotonic()
        self.raise_()
        self._start_anim()

    def _start_anim(self):
        if not self.animating:
            self.animating = True
            self._anim_step()

    def _anim_step(self):
        if not self.visible():
            self.animating = False
            return
        diff = self.target_x - self.cur_x
        if abs(diff) > 0.5:
            self.cur_x += diff * 0.35
            self._move()
            self.root.after(16, self._anim_step)
        else:
            self.cur_x = self.target_x
            self._move()
            self.animating = False

    # ------------------------------------------------------------ 鼠标事件
    def _on_enter(self, _event):
        if self.expanded:
            self.last_hover = time.monotonic()
        else:
            self._expand()

    def _on_press(self, e):
        self._press_pt = (e.x_root, e.y_root)
        self._press_y = self.cur_y
        self._dragging = False
        self.last_hover = time.monotonic()

    def _on_motion(self, e):
        if not self._press_pt:
            return
        dy = e.y_root - self._press_pt[1]
        if abs(dy) > DRAG_START or self._dragging:
            self._dragging = True
            t, _r, b, _h, _s = self._edges()
            self.cur_y = max(t + EDGE_GAP, min(b - self.size - EDGE_GAP, self._press_y + dy))
            self.last_hover = time.monotonic()
            self._move()

    def _on_release(self, e):
        if self._dragging:
            self._dragging = False
            self.pos[:] = [int(self.cur_x), int(self.cur_y)]
            self.on_save()
        elif self._press_pt:
            dx = abs(e.x_root - self._press_pt[0])
            dy = abs(e.y_root - self._press_pt[1])
            if dx <= CLICK_SLOP and dy <= CLICK_SLOP:
                self.on_toggle()
        self._press_pt = None
        self.last_hover = time.monotonic()

    # ------------------------------------------------------------ 主循环
    def tick(self, now, p, fullscreen, icons_visible):
        """返回 True 表示鼠标正在按钮附近，主循环不应再弹出右侧启动栏。"""
        always = self.always.get()
        blocked = fullscreen and not always
        if not (always or icons_visible is False) or blocked:
            if self.visible():
                self.win.withdraw()
            return False
        if not self.visible():
            self.win.deiconify()
            self.place()
            if not self.visible():
                return False

        t, r, b, x_hidden, _x_shown = self._edges()
        in_y_zone = self.cur_y - 16 <= p.y <= self.cur_y + self.size + 16
        if self.expanded:
            if self.cur_x - 14 <= p.x <= r + 4 and in_y_zone:
                self.last_hover = now
            elif not self._dragging and now - self.last_hover > HOVER_LINGER:
                self.expanded = False
                self.target_x = x_hidden
                self._start_anim()
        return self.expanded or (r - 12 <= p.x < r and in_y_zone)
