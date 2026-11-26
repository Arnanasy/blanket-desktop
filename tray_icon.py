"""系统托盘图标：左键切换图标显示/隐藏，右键弹出菜单。"""
import ctypes
from pathlib import Path
import queue

from win32 import (user32, gdi32, shell32, NOTIFYICONDATAW, ICONINFO, WNDPROC, SetWindowLongPtr,
                   GWLP_WNDPROC, WM_NULL, WM_QUERYENDSESSION, WM_ENDSESSION, WM_APP, WM_LBUTTONUP,
                   WM_RBUTTONUP, WM_DISPLAYCHANGE, WM_POWERBROADCAST, WM_WTSSESSION_CHANGE,
                   WM_HOTKEY, PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC, WTS_SESSION_UNLOCK,
                   NIM_ADD, NIM_MODIFY, NIM_DELETE, NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO,
                   NIIF_INFO, TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD, MF_STRING, MF_CHECKED,
                   MF_SEPARATOR, IMAGE_ICON, LR_LOADFROMFILE, NULL_PEN, SM_CXSMICON, SM_CYSMICON,
                   MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_H, VK_T, cursor_pos)

WM_TRAY = WM_APP + 1
IDM_OPEN = 2001
IDM_QUIT = 2002
IDM_THEME = 2003
IDM_GUARD = 2004
HOTKEY_ICONS = 1
HOTKEY_THEME = 2
TRAY_ICON_ID = 1
from preferences import resource_path

ICON_FILE = Path(resource_path('Blanket.ico'))


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
        self.get_focus_guard = None
        self.on_toggle_focus_guard = None
        self.on_hotkey_icons = None
        self.on_hotkey_theme = None
        self.hotkeys_registered = set()
        # WNDPROC 处于 Tcl DispatchMessage 重入栈内，绝不能直接调 Tk 的 after/after_idle；
        # 只把事件放进纯 Python 队列，由主循环在正常 Tk 上下文里调用 process_events() 消费。
        self._events = queue.SimpleQueue()
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
        # 优先使用系统原生 RegisterHotKey（零漏检且不向前台软件透传）；被占用时由主循环回退轮询
        mods = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
        for hk_id, vk in ((HOTKEY_ICONS, VK_H), (HOTKEY_THEME, VK_T)):
            if user32.RegisterHotKey(self.hwnd, hk_id, mods, vk):
                self.hotkeys_registered.add(hk_id)

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
        nid.szTip = 'Blanket（左键切换，右键菜单）'
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def notify(self, title, message):
        """发送非抢焦点的系统托盘轻提示通知（如倒计时结束提醒）。"""
        if self._proc_ref is None:
            return
        nid = self._base_data()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = str(title or 'Blanket')[:60]
        nid.szInfo = str(message or '')[:240]
        nid.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _show_menu(self):
        if self._proc_ref is None:    # 已销毁
            return
        pt = cursor_pos()
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, IDM_OPEN, '打开设置')
        if self.on_toggle_focus_guard:
            checked = bool(self.get_focus_guard()) if self.get_focus_guard else True
            flags = MF_STRING | (MF_CHECKED if checked else 0)
            user32.AppendMenuW(menu, flags, IDM_GUARD, '游戏 / 窗口防误触')
        if self.on_theme:
            try:
                from theme_switch import is_dark
                label = '☀ 切换为浅色主题' if is_dark() else '☾ 切换为深色主题'
            except ImportError:
                label = '切换深浅主题'
            user32.AppendMenuW(menu, MF_STRING, IDM_THEME, label)
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, IDM_QUIT, '退出程序')
        user32.SetForegroundWindow(self.hwnd)   # 托盘菜单必需，否则点别处菜单不消失
        cmd = 0
        try:
            # 用 TPM_RETURNCMD 直接拿选中项，省去 WM_COMMAND 往返与二次排队延迟
            cmd = user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                pt.x, pt.y, 0, self.hwnd, None,
            )
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)
            self.menu_closed()
        if cmd == IDM_OPEN:
            self._on_open()
        elif cmd == IDM_GUARD and self.on_toggle_focus_guard:
            self.on_toggle_focus_guard()
        elif cmd == IDM_THEME and self.on_theme:
            self.on_theme()
        elif cmd == IDM_QUIT:
            self._on_quit()

    def process_events(self):
        """在主线程正常 Tk 回调中调用，消费 WNDPROC 排入的托盘与系统恢复事件。"""
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            if event == 'left':
                self._on_left()
            elif event == 'right':
                self._show_menu()
            elif event == 'resume':
                self.on_resume()
            elif event == 'hotkey_icons' and self.on_hotkey_icons:
                self.on_hotkey_icons()
            elif event == 'hotkey_theme' and self.on_hotkey_theme:
                self.on_hotkey_theme()
            elif event == 'quit':
                self._on_quit()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        # 响应系统关机/注销及安装包重启管理器（Restart Manager）优雅退出，提前恢复桌面图标防误藏
        if msg == WM_QUERYENDSESSION:
            return 1
        if msg == WM_ENDSESSION:
            if wparam:
                try:
                    from desktop_windows import set_icons_visible
                    set_icons_visible(True)
                except Exception:
                    pass
                self._events.put('quit')
            return 0
        if msg == WM_HOTKEY:
            if wparam == HOTKEY_ICONS:
                self._events.put('hotkey_icons')
                return 0
            if wparam == HOTKEY_THEME:
                self._events.put('hotkey_theme')
                return 0
        resumed = ((msg == WM_WTSSESSION_CHANGE and wparam == WTS_SESSION_UNLOCK)
                   or (msg == WM_POWERBROADCAST and wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC))
                   or msg == WM_DISPLAYCHANGE)
        if resumed:
            self._events.put('resume')
        if self._taskbar_created and msg == self._taskbar_created:
            # 资源管理器重启后需要重新添加托盘图标
            self._events.put('resume')
            self._add_icon()
            return 0
        if msg == WM_TRAY:
            mouse = lparam & 0xFFFF
            if mouse == WM_LBUTTONUP:
                self._events.put('left')
                return 0
            if mouse == WM_RBUTTONUP:
                self._events.put('right')
                return 0
        return user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)

    def destroy(self):
        for hk_id in list(self.hotkeys_registered):
            user32.UnregisterHotKey(self.hwnd, hk_id)
        self.hotkeys_registered.clear()
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._base_data()))
        if self._old_proc is not None:
            SetWindowLongPtr(self.hwnd, GWLP_WNDPROC, self._old_proc)
            self._old_proc = None
        if self._icon:
            user32.DestroyIcon(self._icon)
            self._icon = None
        self._proc_ref = None
