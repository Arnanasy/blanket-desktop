# -*- coding: utf-8 -*-
"""
净桌面 - 鼠标热区自动隐藏 / 显示桌面图标
纯 Python 标准库实现（tkinter + ctypes），无需安装任何第三方库。

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

import os
import sys
import time
import json
import winreg
import ctypes
from ctypes import wintypes
import tkinter as tk
from desktop_widgets import Widgets, bounds
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

# 系统托盘（Shell_NotifyIcon）
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
GWLP_WNDPROC = -4
TPM_RIGHTBUTTON = 0x0002
MF_STRING = 0x0000
IDM_OPEN = 2001
IDM_QUIT = 2002
TRAY_ICON_ID = 1

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

# 高 DPI 适配，避免在高缩放下界面模糊；同时让 GetCursorPos 与屏幕尺寸自洽
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32

# 显式声明签名，防止 64 位窗口句柄被截断
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                 wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.EnumWindows.restype = wintypes.BOOL
# EnumWindows 的 argtypes 待 WNDENUMPROC 定义后再设置
user32.ShowWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

# 窗口枚举回调类型（wintypes 未提供，需自行定义）
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]


# ---------------------------------------------------------------- 核心 API
_desktop_cache = (None, None)
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL

def find_desktop_windows():
    global _desktop_cache
    if _desktop_cache[1] and user32.IsWindow(_desktop_cache[1]):
        return _desktop_cache
    _desktop_cache = _find_desktop_windows()
    return _desktop_cache

def _find_desktop_windows():
    """返回 (SHELLDLL_DefView, SysListView32) 句柄，找不到对应项为 None。"""
    progman = user32.FindWindowW("Progman", None)
    if progman:
        shell = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if shell:
            lv = user32.FindWindowExW(shell, None, "SysListView32", None)
            if lv:
                return shell, lv

    # 兼容桌面被 WorkerW 重定向的情况：枚举所有顶层窗口
    found_shell = []

    def _cb(hwnd, _lparam):
        shell = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if shell:
            lv = user32.FindWindowExW(shell, None, "SysListView32", None)
            if lv:
                found_shell.append((shell, lv))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    if found_shell:
        return found_shell[0]
    return None, None


def find_desktop_listview():
    """返回桌面图标列表 SysListView32 的句柄，找不到返回 None。"""
    _shell, lv = find_desktop_windows()
    return lv


def icons_visible():
    """桌面图标当前是否可见；无法获取时返回 None。"""
    lv = find_desktop_listview()
    if not lv:
        return None
    return bool(user32.IsWindowVisible(lv))


def set_icons_visible(show):
    """显示(True)/隐藏(False)桌面图标，成功返回 True。"""
    lv = find_desktop_listview()
    if not lv:
        return False
    user32.ShowWindow(lv, SW_SHOW if show else SW_HIDE)
    return True


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL
ctypes.windll.kernel32.GetTickCount.restype = wintypes.DWORD

def input_idle_seconds():
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    # DWORD clock wraps every 49.7 days.
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xffffffff) / 1000.0


def get_cursor():
    """返回当前鼠标坐标 (x, y)。"""
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def in_hotzone(x, y, mode):
    """判断鼠标是否处于热区。边缘用极值判断，不受 DPI 缩放影响。"""
    left, top, right, bottom = bounds(x, y)
    edge = 4
    if mode == "top":
        return y <= top + edge
    if mode == "any":
        return x <= left + edge or y <= top + edge or x >= right - edge or y >= bottom - edge
    if mode == "corner":
        return x <= left + 12 and y <= top + 12
    return False


# ---------------------------------------------------------------- 开机自启
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "JingZhuoMian"


def _pythonw_path():
    """返回与当前解释器对应的 pythonw.exe（无控制台）路径。"""
    exe = sys.executable
    if os.path.basename(exe).lower() == "python.exe":
        return os.path.join(os.path.dirname(exe), "pythonw.exe")
    return exe


def autostart_enabled():
    """是否已开启开机自启。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except (FileNotFoundError, OSError):
        return False


def set_autostart(enable):
    """开启/关闭开机自启，成功返回 True。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enable:
                cmd = '"{}" "{}"'.format(_pythonw_path(),
                                         os.path.abspath(__file__))
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 配置持久化
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "config.json")
DEFAULT_CONFIG = {
    "auto": True,
    "zone": "top",
    "delay": "10",
}


def load_config():
    """读取配置，缺失或损坏时返回默认值。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        if not isinstance(cfg, dict):
            return merged
        if isinstance(cfg.get("last_visible"),bool):
            merged["last_visible"]=cfg["last_visible"]
        if isinstance(cfg.get("auto"), bool):
            merged["auto"] = cfg["auto"]
        if cfg.get("zone") in ("top", "any", "corner"):
            merged["zone"] = cfg["zone"]
        if str(cfg.get("delay")) in ("5", "10", "20"):
            merged["delay"] = str(cfg["delay"])
        return merged
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """保存配置，失败静默。"""
    try:
        with open(CONFIG_PATH + ".tmp", "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(CONFIG_PATH + ".tmp", CONFIG_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------- 系统托盘
class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.ULONG),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", wintypes.BYTE * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)


class TrayIcon:
    """系统托盘图标：左键切换显示/隐藏，右键菜单（打开设置、退出）。"""

    def __init__(self, hwnd, on_left, on_open, on_quit):
        self.hwnd = hwnd
        self._on_left = on_left
        self._on_open = on_open
        self._on_quit = on_quit
        self._old_proc = None
        self._proc_ref = None      # 保持回调引用，防止被垃圾回收导致崩溃
        self._icon = None
        self._u = ctypes.windll.user32
        self._g = ctypes.windll.gdi32
        self._s = ctypes.windll.shell32
        self._setup_signatures()
        self._u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        self._u.RegisterWindowMessageW.restype = wintypes.UINT
        self._taskbar_created = self._u.RegisterWindowMessageW("TaskbarCreated")

    def _setup_signatures(self):
        u, g, s = self._u, self._g, self._s
        u.SetWindowLongPtrW.restype = ctypes.c_longlong
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                        ctypes.c_longlong]
        u.CallWindowProcW.restype = ctypes.c_ssize_t
        u.CallWindowProcW.argtypes = [ctypes.c_longlong, wintypes.HWND,
                                      wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM]
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPCWSTR]
        u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.HWND, wintypes.LPVOID]
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.CreateIconIndirect.restype = wintypes.HICON
        u.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
        u.DestroyIcon.argtypes = [wintypes.HICON]
        u.GetDC.restype = wintypes.HDC
        u.GetDC.argtypes = [wintypes.HWND]
        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        g.GetStockObject.restype = wintypes.HGDIOBJ
        g.GetStockObject.argtypes = [ctypes.c_int]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int,
                                             ctypes.c_int]
        g.CreateBitmap.restype = wintypes.HBITMAP
        g.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT,
                                   wintypes.UINT, wintypes.LPVOID]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.CreateSolidBrush.restype = wintypes.HBRUSH
        g.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        g.RoundRect.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int]
        g.Rectangle.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int]
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.DeleteDC.argtypes = [wintypes.HDC]
        s.Shell_NotifyIconW.restype = wintypes.BOOL
        s.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                        ctypes.POINTER(NOTIFYICONDATAW)]

    # 用 GDI 自绘一个蓝色圆角 + 白色小窗口的图标
    def _make_icon(self):
        u, g = self._u, self._g
        size = 32
        screen_dc = u.GetDC(None)
        color_bmp = g.CreateCompatibleBitmap(screen_dc, size, size)
        mask_bmp = g.CreateBitmap(size, size, 1, 1, None)
        mem_dc = g.CreateCompatibleDC(screen_dc)
        old_obj = g.SelectObject(mem_dc, color_bmp)

        g.SelectObject(mem_dc, g.GetStockObject(5))   # NULL_PEN，无边框
        blue = g.CreateSolidBrush(0x00EB6325)         # #2563EB
        g.SelectObject(mem_dc, blue)
        g.RoundRect(mem_dc, 0, 0, size, size, 10, 10)

        white = g.CreateSolidBrush(0x00FFFFFF)
        g.SelectObject(mem_dc, white)
        g.Rectangle(mem_dc, 9, 10, 23, 23)            # 白色小窗口
        g.SelectObject(mem_dc, blue)
        g.Rectangle(mem_dc, 12, 7, 20, 10)            # 窗口标题栏

        info = ICONINFO()
        info.fIcon = True
        info.hbmMask = mask_bmp
        info.hbmColor = color_bmp
        hicon = u.CreateIconIndirect(ctypes.byref(info))

        g.SelectObject(mem_dc, old_obj)
        u.ReleaseDC(None, screen_dc)
        g.DeleteDC(mem_dc)
        g.DeleteObject(color_bmp)
        g.DeleteObject(mask_bmp)
        g.DeleteObject(blue)
        g.DeleteObject(white)
        return hicon

    def install(self):
        self._proc_ref = WNDPROCTYPE(self._wndproc)
        addr = ctypes.cast(self._proc_ref, ctypes.c_void_p).value
        self._old_proc = self._u.SetWindowLongPtrW(
            self.hwnd, GWLP_WNDPROC, addr)

        self._icon = self._make_icon()
        self._add_icon()

    def _add_icon(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = TRAY_ICON_ID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon
        nid.szTip = "净桌面（左键切换，右键菜单）"
        self._s.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _show_menu(self):
        u = self._u
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        menu = u.CreatePopupMenu()
        u.AppendMenuW(menu, MF_STRING, IDM_OPEN, "打开设置")
        u.AppendMenuW(menu, MF_STRING, IDM_QUIT, "退出程序")
        u.SetForegroundWindow(self.hwnd)   # 托盘菜单必需，否则可能不消失
        u.TrackPopupMenu(menu, TPM_RIGHTBUTTON, pt.x, pt.y,
                         0, self.hwnd, None)
        u.DestroyMenu(menu)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if (msg==0x02B1 and wparam==8) or (msg==0x0218 and wparam in (7,18)) or msg==0x007E:
            self.on_resume()
        if self._taskbar_created and msg == self._taskbar_created:
            self.on_resume()
            self._add_icon()
            return 0
        if msg == WM_TRAY:
            mouse = lparam & 0xFFFF
            if mouse == WM_LBUTTONUP:
                self._on_left()
                return 0
            if mouse == WM_RBUTTONUP:
                self._show_menu()
                return 0
        elif msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == IDM_OPEN:
                self._on_open()
                return 0
            if cmd == IDM_QUIT:
                self._on_quit()
                return 0
        return self._u.CallWindowProcW(self._old_proc, hwnd, msg,
                                       wparam, lparam)

    def destroy(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = TRAY_ICON_ID
        self._s.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        if self._old_proc is not None:
            self._u.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC,
                                      self._old_proc)
        if self._icon:
            self._u.DestroyIcon(self._icon)
        self._proc_ref = None


class LVHITTESTINFO(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT), ("flags", wintypes.UINT),
        ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
    ]


class DesktopClick:
    """点桌面空白处切换图标显示/隐藏（GetAsyncKeyState 轮询，无全局钩子）。

    早期用 WH_MOUSE_LL 全局钩子会拦截所有鼠标事件、在后台线程与 Tk 抢 GIL，
    导致系统鼠标卡顿。改为在主线程轮询鼠标左键（与快捷键同款机制），开销极小，
    也不需要后台线程。判断规则：
    - 图标隐藏时，桌面点 WindowFromPoint 返回 SHELLDLL_DefView -> 视为空白
    - 图标显示时，点 WindowFromPoint 返回 SysListView32，再用带超时的
      LVM_HITTEST 判断是否点在空白，点图标不触发
    """

    VK_LBUTTON = 0x01
    LVM_HITTEST = 0x1000 + 18
    SMTO_ABORTIFHUNG = 0x0002
    DRAG_THRESHOLD = 6   # 按下到松开位移超过此值视为框选，不触发

    def __init__(self):
        self._u = ctypes.windll.user32
        self._held = False
        self._down_blank = False
        self._down_x = None
        self._down_y = None
        self._setup_signatures()

    def _setup_signatures(self):
        u = self._u
        u.WindowFromPoint.restype = wintypes.HWND
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                    ctypes.c_int]
        u.ScreenToClient.argtypes = [wintypes.HWND,
                                     ctypes.POINTER(wintypes.POINT)]
        u.SendMessageTimeoutW.restype = wintypes.BOOL
        u.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]

    def check(self):
        """主线程每次 poll 调用一次，返回 True 表示这次点了桌面空白。"""
        down = bool(self._u.GetAsyncKeyState(self.VK_LBUTTON) & 0x8000)
        if down and not self._held:
            self._held = True
            self._down_x, self._down_y = get_cursor()
            self._down_blank = self._is_blank_click(self._down_x, self._down_y)
        elif not down and self._held:
            self._held = False
            x, y = get_cursor()
            moved = abs(x - self._down_x) + abs(y - self._down_y)
            # 仅当是单击（非拖拽框选）才判断落点
            if self._down_blank and moved <= self.DRAG_THRESHOLD:
                return self._is_blank_click(x, y)
        return False

    def _is_blank_click(self, x, y):
        hwnd = self._u.WindowFromPoint(wintypes.POINT(x, y))
        buf = ctypes.create_unicode_buffer(256)
        self._u.GetClassNameW(hwnd, buf, 256)
        cls = buf.value
        if cls == "SHELLDLL_DefView":
            return True   # 图标隐藏状态，点桌面即空白
        if cls == "SysListView32" and hwnd == find_desktop_listview():
            return self._hittest_blank(hwnd, x, y)
        return False

    def _hittest_blank(self, lv, x, y):
        p = wintypes.POINT(x, y)
        self._u.ScreenToClient(lv, ctypes.byref(p))
        # LVM_HITTEST is not marshalled by Windows: allocate in Explorer.
        k = ctypes.windll.kernel32
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
        k.VirtualAllocEx.restype = ctypes.c_void_p
        k.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        k.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self._u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        pid = wintypes.DWORD()
        self._u.GetWindowThreadProcessId(lv, ctypes.byref(pid))
        process = k.OpenProcess(0x0028, False, pid.value)
        if not process:
            return False
        remote = None
        try:
            hti = LVHITTESTINFO()
            hti.pt = p
            remote = k.VirtualAllocEx(process, None, 64, 0x3000, 0x04)
            if not remote or not k.WriteProcessMemory(process, remote, ctypes.byref(hti), ctypes.sizeof(hti), None):
                return False
            result = ctypes.c_size_t()
            ok = self._u.SendMessageTimeoutW(lv, self.LVM_HITTEST, 0, remote,
                                            self.SMTO_ABORTIFHUNG, 40, ctypes.byref(result))
            return bool(ok and ctypes.c_int32(result.value & 0xffffffff).value == -1)
        finally:
            if remote:
                k.VirtualFreeEx(process, remote, 0, 0x8000)
            k.CloseHandle(process)



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

        # 点桌面空白处切换图标显示/隐藏（主线程轮询，无全局钩子、不卡顿）
        self.desktop_click = DesktopClick()
        self.mouse_events = MouseEvents()
        self._mouse_down = None
        self.widgets = Widgets(self)
        self.restore_visibility(0)

        self._persist()
        self.refresh_ui()
        self.root.after(150, self.poll)
        self.root.after(10, self.poll_mouse)

    # 高频鼠标检测：40ms 一次，确保短点击也能捕获，开销极小
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
        global _desktop_cache
        _desktop_cache=(None,None)

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
