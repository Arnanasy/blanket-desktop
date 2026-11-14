# -*- coding: utf-8 -*-
"""
净桌面 - 鼠标热区自动隐藏 / 显示桌面图标
核心功能使用 Python 标准库；壁纸亮度自适应使用可选依赖 Pillow。

功能：
- 点桌面空白：在桌面空白处左键单击即切换图标显示/隐藏，点图标不受影响
- 系统托盘：左键单击切换图标显示/隐藏，右键菜单（打开设置、退出程序）
- 自动模式：鼠标移到屏幕热区（默认顶部边缘）只唤出桌面图标，不弹设置窗口，
            鼠标静止无操作达设定时间后自动隐藏，桌面只剩壁纸
- 手动切换：按快捷键 Ctrl + Alt + H（GetAsyncKeyState 轮询，可靠）
- 打开设置：右键托盘选“打开设置”，或双击“净桌面”快捷方式；单实例不重复开进程
- 开机自启：可在程序内开关，写入当前用户注册表 Run 项，无需管理员权限
- 退出方便：托盘右键或窗口内点“退出程序”，也可“隐藏窗口”仅保留后台运行
- 不重启资源管理器，图标切换即时生效、完全可逆
"""

import sys
sys.dont_write_bytecode = True
import time
import ctypes
from ctypes import wintypes
import tkinter as tk
from desktop_widgets import Widgets
from tray_icon import TrayIcon
from preferences import load_config, save_config, autostart_enabled, set_autostart
from desktop_windows import DesktopClick, user32, get_cursor, icons_visible, set_icons_visible, in_hotzone, input_idle_seconds, invalidate_cache
from mouse_events import MouseEvents
import queue

# ---------------------------------------------------------------- 常量
SW_HIDE = 0
SW_SHOW = 5

# 虚拟键码
VK_CONTROL = 0x11
VK_MENU = 0x12      # Alt
VK_H = 0x48

# 单实例与“双击快捷方式唤起主窗口”的跨进程内核对象
MUTEX_NAME = "Local\\JingZhuoMian_SingleInstance"
EVENT_NAME = "Local\\JingZhuoMian_ShowRequest"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0

# 配色（Windows 11 简洁浅色风格）
BG = "#F4F7FC"
BLUE = "#2563EB"
BLUE_D = "#1D4ED8"
GREEN = "#16A34A"
GREEN_D = "#15803D"
GRAY = "#94A3B8"
RED = "#DC2626"
RED_D = "#B91C1C"
TEXT = "#1E293B"
SUBTEXT = "#64748B"
WHITE = "#FFFFFF"

# ---------------------------------------------------------------- 界面
class App:
    def __init__(self, root, show_event=None):
        self.root = root
        self.cfg = load_config()
        self._show_event = show_event
        root.title("净桌面")
        root.configure(bg=BG)
        root.resizable(False, False)

        f_title = ("Microsoft YaHei UI", 16, "bold")
        f_sub = ("Microsoft YaHei UI", 9)
        f_btn = ("Microsoft YaHei UI", 12, "bold")
        f_state = ("Microsoft YaHei UI", 10, "bold")
        f_normal = ("Microsoft YaHei UI", 9)
        f_small = ("Microsoft YaHei UI", 8)

        tk.Label(root, text="净桌面", font=f_title, bg=BG, fg=TEXT).pack(pady=(18, 2))
        tk.Label(root, text="鼠标移到屏幕边缘自动显示，移开只剩壁纸",
                 font=f_sub, bg=BG, fg=SUBTEXT).pack(pady=(0, 12))

        # 主切换按钮
        self.btn = tk.Button(root, text="立即隐藏", font=f_btn,
                             bg=BLUE, fg=WHITE, activebackground=BLUE_D,
                             activeforeground=WHITE, relief="flat",
                             borderwidth=0, cursor="hand2",
                             width=20, pady=10, command=self.manual_toggle)
        self.btn.pack(padx=24)

        # 状态行
        state_frame = tk.Frame(root, bg=BG)
        state_frame.pack(pady=(12, 4))
        self.dot_canvas = tk.Canvas(state_frame, width=14, height=14,
                                    bg=BG, highlightthickness=0)
        self.dot_canvas.pack(side="left", padx=(0, 8))
        self.dot = self.dot_canvas.create_oval(1, 1, 13, 13,
                                               fill=GREEN, outline=GREEN)
        self.state_label = tk.Label(state_frame, text="桌面图标显示中",
                                    font=f_state, bg=BG, fg=TEXT)
        self.state_label.pack(side="left")

        # 设置区
        opt = tk.Frame(root, bg=BG)
        opt.pack(fill="x", padx=24, pady=(10, 2))

        self.auto_var = tk.BooleanVar(value=bool(self.cfg["auto"]))
        tk.Checkbutton(opt, text="自动模式（鼠标热区驱动）",
                       variable=self.auto_var, font=f_normal, bg=BG,
                       fg=TEXT, activebackground=BG, selectcolor=WHITE,
                       anchor="w", command=self._persist
                       ).pack(anchor="w")

        self.startup_var = tk.BooleanVar(value=autostart_enabled())
        tk.Checkbutton(opt, text="开机自启（登录后自动运行）",
                       variable=self.startup_var, font=f_normal, bg=BG,
                       fg=TEXT, activebackground=BG, selectcolor=WHITE,
                       anchor="w", command=self._toggle_autostart
                       ).pack(anchor="w", pady=(6, 0))

        tk.Label(opt, text="热区位置：", font=f_normal, bg=BG,
                 fg=TEXT, anchor="w").pack(anchor="w", pady=(8, 0))
        self.zone_var = tk.StringVar(value=self.cfg["zone"])
        zones = [("顶部边缘", "top"), ("屏幕任意边缘", "any"), ("左上角", "corner")]
        for text, val in zones:
            tk.Radiobutton(opt, text=text, variable=self.zone_var, value=val,
                           font=f_normal, bg=BG, fg=TEXT, activebackground=BG,
                           selectcolor=WHITE, anchor="w",
                           command=self._persist).pack(anchor="w")

        tk.Label(opt, text="无操作后自动隐藏：", font=f_normal, bg=BG,
                 fg=TEXT, anchor="w").pack(anchor="w", pady=(8, 0))
        delay_row = tk.Frame(opt, bg=BG)
        delay_row.pack(anchor="w")
        self.delay_var = tk.StringVar(value=str(self.cfg["delay"]))
        for text, val in [("5秒", "5"), ("10秒", "10"), ("20秒", "20")]:
            tk.Radiobutton(delay_row, text=text, variable=self.delay_var,
                           value=val, font=f_normal, bg=BG, fg=TEXT,
                           activebackground=BG, selectcolor=WHITE,
                           command=self._persist
                           ).pack(side="left")

        tk.Label(root, text="快捷键 Ctrl + Alt + H 立即切换",
                 font=f_small, bg=BG, fg=SUBTEXT).pack(pady=(10, 2))

        # 底部操作：隐藏窗口 / 退出程序
        bottom = tk.Frame(root, bg=BG)
        bottom.pack(pady=(8, 16))
        tk.Button(bottom, text="隐藏窗口", font=f_normal, bg="#E2E8F0",
                  fg=TEXT, activebackground="#CBD5E1", relief="flat",
                  borderwidth=0, cursor="hand2", width=10, pady=8,
                  command=self.hide_window).pack(side="left", padx=6)
        tk.Button(bottom, text="退出程序", font=f_normal, bg=RED,
                  fg=WHITE, activebackground=RED_D, relief="flat",
                  borderwidth=0, cursor="hand2", width=10, pady=8,
                  command=self.quit_app).pack(side="left", padx=6)

        tk.Button(root, text="桌面组件设置", command=lambda: self.widgets.settings()).pack(pady=4)
        self.pause_button = tk.Button(root, text="暂停自动隐藏", command=self.pause_auto)
        self.pause_button.pack(pady=4)
        self._paused = False

        # 运行状态
        self._in_zone = False
        self._hk_held = False
        _x0, _y0 = get_cursor()
        self._last_x, self._last_y = _x0, _y0
        self._last_activity = time.monotonic()
        self._grace_until = time.monotonic() + 5  # 启动 5 秒宽限，先看清界面

        root.protocol("WM_DELETE_WINDOW", self.hide_window)

        # 系统托盘图标：左键切换显示/隐藏，右键菜单（打开设置、退出）
        root.update_idletasks()
        # winfo_id 是 Tk 内部子窗口(TkChild)，需向上找到真正的顶层窗口
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        top_hwnd = root.winfo_id()
        while True:
            parent = user32.GetParent(top_hwnd)
            if not parent:
                break
            top_hwnd = parent
        self.tray = TrayIcon(top_hwnd,
                             self._tray_toggle,
                             self._tray_open,
                             self.quit_app)
        self.tray.on_resume=lambda:self.root.after_idle(self.resume_desktop)
        self.tray.install()
        ctypes.windll.wtsapi32.WTSRegisterSessionNotification(wintypes.HWND(top_hwnd),0)

        # 点桌面空白处切换图标显示/隐藏（优先使用钩子队列，安装失败时轮询）
        self.desktop_click = DesktopClick()
        self.mouse_events = MouseEvents()
        self._mouse_down = None
        self.widgets = Widgets(self)
        self.restore_visibility(0)

        self._persist()
        self.refresh_ui()
        self.root.after(150, self.poll)
        self.root.after(10, self.poll_mouse)

    # 每轮只消费钩子队列或执行左键轮询，两者互斥。
    def poll_mouse(self):
        if self.mouse_events.hook:
            for _ in range(100):
                try:
                    msg, x, y = self.mouse_events.events.get_nowait()
                except queue.Empty:
                    break
                if msg == 0x201:
                    self._mouse_down = (x, y, self.desktop_click._is_blank_click(x,y))
                elif self._mouse_down:
                    dx,dy,blank = self._mouse_down
                    self._mouse_down = None
                    if blank and abs(x-dx)+abs(y-dy)<=6 and self.desktop_click._is_blank_click(x,y):
                        self._blank_click()
        elif self.desktop_click.check():
            self._blank_click()
        self.root.after(20, self.poll_mouse)

    # 托盘回调
    def _tray_toggle(self):
        vis = icons_visible()
        if vis is None:
            return
        set_icons_visible(not vis)
        self._last_activity = time.monotonic()
        self.refresh_ui()

    def _tray_open(self):
        self._grace_until = time.monotonic() + 5
        self.root.deiconify()
        self.root.lift()
        self._last_activity = time.monotonic()

    # 点桌面空白处：切换图标显示/隐藏
    def _blank_click(self):
        vis = icons_visible()
        if vis is None:
            return
        set_icons_visible(not vis)
        # 刷新活动计时，显示后给用户完整的无操作宽限，避免太快又隐藏
        self._last_activity = time.monotonic()
        self.refresh_ui()

    # ------------------------------------------------------------ 操作
    def manual_toggle(self):
        vis = icons_visible()
        if vis is None:
            self.state_label.config(text="无法获取桌面状态")
            return
        set_icons_visible(not vis)
        self._last_activity = time.monotonic()
        self.refresh_ui()

    def pause_auto(self):
        self._paused = not self._paused
        self.pause_button.config(text="恢复自动隐藏" if self._paused else "暂停自动隐藏")
        self._last_activity = time.monotonic()

    def desktop_icons_visible(self):
        return icons_visible()

    def hide_window(self):
        self.root.withdraw()

    def _toggle_autostart(self):
        ok = set_autostart(self.startup_var.get())
        if not ok:
            # 写入失败则回退为实际状态
            self.startup_var.set(autostart_enabled())

    def _persist(self):
        self.cfg.update({
            "auto": self.auto_var.get(),
            "zone": self.zone_var.get(),
            "delay": self.delay_var.get(),
        })
        save_config(self.cfg)

    def restore_visibility(self,attempt):
        target=self.cfg.get('last_visible')
        if target is None:return
        if icons_visible() is None:
            if attempt<20:self.root.after(500,lambda:self.restore_visibility(attempt+1))
        else:set_icons_visible(target)

    def invalidate_desktop(self):
        invalidate_cache()

    def resume_desktop(self):
        self.invalidate_desktop()
        self.widgets.last_theme=0
        self.widgets.last_position_check=0
        self.widgets.last_clock=''
        self.refresh_ui()

    def quit_app(self):
        state=icons_visible()
        if state is not None:
            self.cfg['last_visible']=state
            save_config(self.cfg)
        self.widgets.save()
        self.widgets.worker.shutdown(wait=False,cancel_futures=True)
        ctypes.windll.wtsapi32.WTSUnRegisterSessionNotification(wintypes.HWND(self.tray.hwnd))
        if getattr(self,"mouse_events",None):
            self.mouse_events.close()
        # 退出前确保桌面图标恢复显示，避免用户找不到图标
        if icons_visible() is False:
            set_icons_visible(True)
        if getattr(self, "tray", None):
            self.tray.destroy()
        self.root.destroy()

    # ------------------------------------------------------------ 显示恢复
    def _ensure_shown(self):
        # 热区只唤出桌面图标，不自动弹出设置主窗口（避免打扰）
        if icons_visible() is False:
            set_icons_visible(True)
        # 显示时重置活动计时，给用户完整的无操作宽限
        self._last_activity = time.monotonic()
        self.refresh_ui()

    # ------------------------------------------------------------ 快捷键
    def check_hotkey(self):
        ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
        alt = user32.GetAsyncKeyState(VK_MENU) & 0x8000
        h = user32.GetAsyncKeyState(VK_H) & 0x8000
        down = bool(ctrl and alt and h)
        if down and not self._hk_held:
            self._hk_held = True
            self.manual_toggle()
        elif not down:
            self._hk_held = False

    # ------------------------------------------------------------ 主轮询
    def poll(self):
        self.check_hotkey()

        # 用户双击快捷方式（第二个实例）请求打开设置窗口
        if self._show_event is not None:
            if ctypes.windll.kernel32.WaitForSingleObject(
                    self._show_event, 0) == WAIT_OBJECT_0:
                self._tray_open()

        if self.auto_var.get() and not self._paused and time.monotonic() >= self._grace_until:
            x, y = get_cursor()

            # 鼠标位置变化 = 用户在活动，刷新最后活动时间
            if x != self._last_x or y != self._last_y:
                self._last_x, self._last_y = x, y
                self._last_activity = time.monotonic()

            in_zone = in_hotzone(x, y, self.zone_var.get())
            entered = in_zone and not self._in_zone
            self._in_zone = in_zone
            if entered:
                self._ensure_shown()
            elif not in_zone and icons_visible():
                # 图标显示中：仅当鼠标静止、无操作超过设定时间才隐藏
                idle = min(time.monotonic() - self._last_activity, input_idle_seconds())
                if any(user32.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4)):
                    idle = 0
                if idle >= int(self.delay_var.get()):
                    set_icons_visible(False)

        self.refresh_ui()
        self.root.after(150, self.poll)

    def refresh_ui(self):
        vis = icons_visible()
        if getattr(self, "_ui_state", "unset") == vis:
            return
        self._ui_state = vis
        if vis is not None and self.cfg.get("last_visible") != vis:
            self.cfg["last_visible"]=vis
            save_config(self.cfg)
        if vis is None:
            self.state_label.config(text="无法获取桌面状态")
            return
        if vis:
            self.btn.config(text="立即隐藏", bg=BLUE, activebackground=BLUE_D)
            self.state_label.config(text="桌面图标显示中")
            self.dot_canvas.itemconfig(self.dot, fill=GREEN, outline=GREEN)
        else:
            self.btn.config(text="立即显示", bg=GREEN, activebackground=GREEN_D)
            self.state_label.config(text="桌面图标已隐藏 · 只剩壁纸")
            self.dot_canvas.itemconfig(self.dot, fill=GRAY, outline=GRAY)


def main():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 显式声明签名，避免 64 位句柄被截断
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                 wintypes.LPCWSTR]
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                 wintypes.BOOL, wintypes.LPCWSTR]
    k32.OpenEventW.restype = wintypes.HANDLE
    k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL,
                               wintypes.LPCWSTR]
    k32.SetEvent.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    # 若已有实例在运行，它的“唤起事件”应当存在
    mutex = k32.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        existing_event = None
        for _ in range(20):
            existing_event = k32.OpenEventW(EVENT_MODIFY_STATE, False, EVENT_NAME)
            if existing_event:
                break
            time.sleep(0.1)
        # 第二个实例：通知主实例显示窗口，然后自己退出（不重复运行）
        if existing_event:
            k32.SetEvent(existing_event)
            k32.CloseHandle(existing_event)
        k32.CloseHandle(mutex)
        if not existing_event:
            raise RuntimeError("净桌面已经运行，但未能唤出设置。请从托盘退出旧版本后重试。")
        return

    # 主实例：创建自动复位的唤起事件（manualReset=False）
    show_event = k32.CreateEventW(None, False, False, EVENT_NAME)

    if not show_event:
        k32.CloseHandle(mutex)
        raise ctypes.WinError(ctypes.get_last_error())

    root = tk.Tk()
    App(root, show_event)

    root.update_idletasks()
    w = 340
    # 高度随内容自适应，确保所有选项都完整可见
    h = root.winfo_reqheight() + 24
    screen_h = root.winfo_screenheight()
    if h > screen_h - 40:
        h = screen_h - 40
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 4
    root.geometry(f"{w}x{h}+{x}+{y}")

    try:
        root.mainloop()
    finally:
        k32.CloseHandle(show_event)
        k32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
