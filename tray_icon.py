from pathlib import Path
import ctypes
from ctypes import wintypes

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
IDM_THEME = 2003
TRAY_ICON_ID = 1

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
        self.on_theme = None
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

        load = self._u.LoadImageW
        load.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        load.restype = wintypes.HICON
        self._icon = load(None, str(Path(__file__).with_name("时隙.ico")), 1,
                          self._u.GetSystemMetrics(49), self._u.GetSystemMetrics(50), 0x10) or self._make_icon()
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
        if self.on_theme:
            try:
                from theme_switch import is_dark
                label = "☀ 切换为浅色主题" if is_dark() else "☾ 切换为深色主题"
            except ImportError:
                label = "切换深浅主题"
            u.AppendMenuW(menu, MF_STRING, IDM_THEME, label)
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
            if cmd == IDM_THEME and self.on_theme:
                self.on_theme()
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
