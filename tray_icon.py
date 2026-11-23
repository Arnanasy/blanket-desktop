"""系统托盘图标：左键切换图标显示/隐藏，右键弹出菜单。"""
import ctypes
from ctypes import wintypes
from pathlib import Path

from win32 import (user32, gdi32, shell32, NOTIFYICONDATAW, ICONINFO, WNDPROC, SetWindowLongPtr,
                   GWLP_WNDPROC, WM_NULL, WM_APP, WM_COMMAND, WM_LBUTTONUP, WM_RBUTTONUP, WM_DISPLAYCHANGE,
                   WM_POWERBROADCAST, WM_WTSSESSION_CHANGE, PBT_APMRESUMESUSPEND,
                   PBT_APMRESUMEAUTOMATIC, WTS_SESSION_UNLOCK, NIM_ADD, NIM_DELETE, NIF_MESSAGE,
                   NIF_ICON, NIF_TIP, TPM_RIGHTBUTTON, MF_STRING, IMAGE_ICON, LR_LOADFROMFILE,
                   NULL_PEN, SM_CXSMICON, SM_CYSMICON, cursor_pos)

WM_TRAY = WM_APP + 1
IDM_OPEN = 2001
IDM_QUIT = 2002
IDM_THEME = 2003
TRAY_ICON_ID = 1
ICON_FILE = Path(__file__).with_name('时隙.ico')


class TrayIcon:
    def __init__(self, hwnd, on_left, on_open, on_quit):
        self.hwnd = hwnd
        self._on_left = on_left
        self._on_open = on_open
        self._on_quit = on_quit
        self._old_proc = None
        self._proc_ref = None        # 保持回调引用，防止被垃圾回收导致崩溃
        self._icon = None
        self.on_resume = lambda: None
        self.menu_closed = lambda: None
        self.on_theme = None
        # 弹出菜单的调度器。TrackPopupMenu 是模态循环，不应在窗口过程里同步执行，
        # 由 App 设为 root.after_idle，让菜单在 Tk 空闲时再弹出。
        self.schedule = lambda func: func()
        self._taskbar_created = user32.RegisterWindowMessageW('TaskbarCreated')

    # 图标文件缺失时，用 GDI 自绘一个蓝色圆角 + 白色小窗口的图标
    def _make_icon(self):
        size = 32
        screen_dc = user32.GetDC(None)
        color_bmp = gdi32.CreateCompatibleBitmap(screen_dc, size, size)
        mask_bits = ctypes.create_string_buffer(((size + 15) // 16) * 2 * size)
        mask_bmp = gdi32.CreateBitmap(size, size, 1, 1, mask_bits)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        old_obj = gdi32.SelectObject(mem_dc, color_bmp)

        old_pen = gdi32.SelectObject(mem_dc, gdi32.GetStockObject(NULL_PEN))   # 无边框
        blue = gdi32.CreateSolidBrush(0x00EB6325)         # #2563EB（COLORREF 为 BGR）
        white = gdi32.CreateSolidBrush(0x00FFFFFF)
        old_brush = gdi32.SelectObject(mem_dc, blue)
        gdi32.RoundRect(mem_dc, 0, 0, size, size, 10, 10)
        gdi32.SelectObject(mem_dc, white)
        gdi32.Rectangle(mem_dc, 9, 10, 23, 23)            # 白色小窗口
        gdi32.SelectObject(mem_dc, blue)
        gdi32.Rectangle(mem_dc, 12, 7, 20, 10)            # 窗口标题栏

        gdi32.SelectObject(mem_dc, old_brush)
        gdi32.SelectObject(mem_dc, old_pen)
        gdi32.SelectObject(mem_dc, old_obj)

        info = ICONINFO(fIcon=True, hbmMask=mask_bmp, hbmColor=color_bmp)
        hicon = user32.CreateIconIndirect(ctypes.byref(info))

        user32.ReleaseDC(None, screen_dc)
        gdi32.DeleteDC(mem_dc)
        for obj in (color_bmp, mask_bmp, blue, white):
            gdi32.DeleteObject(obj)
        return hicon

    def install(self):
        self._proc_ref = WNDPROC(self._wndproc)
        addr = ctypes.cast(self._proc_ref, ctypes.c_void_p).value
        self._old_proc = SetWindowLongPtr(self.hwnd, GWLP_WNDPROC, addr)
        self._icon = user32.LoadImageW(None, str(ICON_FILE), IMAGE_ICON,
                                       user32.GetSystemMetrics(SM_CXSMICON),
                                       user32.GetSystemMetrics(SM_CYSMICON),
                                       LR_LOADFROMFILE) or self._make_icon()
        self._add_icon()

    def _base_data(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = TRAY_ICON_ID
        return nid

    def _add_icon(self):
        nid = self._base_data()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon
        nid.szTip = '净桌面（左键切换，右键菜单）'
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _show_menu(self):
        if self._proc_ref is None:    # 已销毁
            return
        pt = cursor_pos()
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, IDM_OPEN, '打开设置')
        if self.on_theme:
            try:
                from theme_switch import is_dark
                label = '☀ 切换为浅色主题' if is_dark() else '☾ 切换为深色主题'
            except ImportError:
                label = '切换深浅主题'
            user32.AppendMenuW(menu, MF_STRING, IDM_THEME, label)
        user32.AppendMenuW(menu, MF_STRING, IDM_QUIT, '退出程序')
        user32.SetForegroundWindow(self.hwnd)   # 托盘菜单必需，否则点别处菜单不消失
        try:
            user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, pt.x, pt.y, 0, self.hwnd, None)
            # 微软文档建议：TrackPopupMenu 返回后投递 WM_NULL，避免下次右键失灵
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)
            self.menu_closed()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        resumed = ((msg == WM_WTSSESSION_CHANGE and wparam == WTS_SESSION_UNLOCK)
                   or (msg == WM_POWERBROADCAST and wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC))
                   or msg == WM_DISPLAYCHANGE)
        if resumed:
            self.on_resume()
        if self._taskbar_created and msg == self._taskbar_created:
            # 资源管理器重启后需要重新添加托盘图标
            self.on_resume()
            self._add_icon()
            return 0
        if msg == WM_TRAY:
            mouse = lparam & 0xFFFF
            if mouse == WM_LBUTTONUP:
                self._on_left()
                return 0
            if mouse == WM_RBUTTONUP:
                self.schedule(self._show_menu)
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
        return user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)

    def destroy(self):
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._base_data()))
        if self._old_proc is not None:
            SetWindowLongPtr(self.hwnd, GWLP_WNDPROC, self._old_proc)
            self._old_proc = None
        if self._icon:
            user32.DestroyIcon(self._icon)
            self._icon = None
        self._proc_ref = None
