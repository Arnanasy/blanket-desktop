"""集中声明本程序用到的全部 Win32 API 签名、结构体与常量。

设计要点：
- 使用私有的 ctypes.WinDLL 实例，而不是进程级单例 ctypes.windll.*，
  避免不同模块（或第三方库）反复改写同一函数的 argtypes / restype 互相覆盖；
- 每个函数都显式声明参数与返回类型，64 位句柄不会被截断成 int；
- 用具名常量代替散落各处的魔法数字。
其他模块只从这里取 DLL 函数，不要再自行声明签名。
"""
import ctypes
from ctypes import wintypes

# ---------------------------------------------------------------- 基础类型
LRESULT = ctypes.c_ssize_t
LONG_PTR = ctypes.c_ssize_t
HRESULT = ctypes.c_long          # 用 c_long 保留原始返回码，不让 ctypes 自动抛异常
POINT = wintypes.POINT
RECT = wintypes.RECT

# ---------------------------------------------------------------- 私有 DLL 实例
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
shell32 = ctypes.WinDLL('shell32', use_last_error=True)
wtsapi32 = ctypes.WinDLL('wtsapi32', use_last_error=True)
ole32 = ctypes.WinDLL('ole32')
oleaut32 = ctypes.WinDLL('oleaut32')
oleacc = ctypes.WinDLL('oleacc')
dwmapi = ctypes.WinDLL('dwmapi')
iphlpapi = ctypes.WinDLL('iphlpapi')
ws2_32 = ctypes.WinDLL('ws2_32')

# ---------------------------------------------------------------- 常量
# 窗口样式与位置
GWL_STYLE = -16
GWL_EXSTYLE = -20
GWLP_WNDPROC = -4
WS_CAPTION = 0x00C00000              # 标题栏（用于区分普通最大化窗口与真正全屏）
WS_EX_TOPMOST = 0x00000008           # 置顶窗口扩展样式
WS_EX_TRANSPARENT = 0x00000020       # 鼠标点击穿透（直达下层游戏或桌面）
WS_EX_TOOLWINDOW = 0x00000080        # 不在任务栏 / Alt+Tab 中出现
WS_EX_LAYERED = 0x00080000           # 分层窗口（配合穿透与透明色键）
WS_EX_NOACTIVATE = 0x08000000        # 点击不抢焦点
DWMWA_EXTENDED_FRAME_BOUNDS = 9      # DWM 真实可见窗口边界（不含隐形阴影）
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
HWND_BOTTOM = 1
GW_HWNDPREV = 3
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_RESTORE = 9
GA_ROOT = 2

# 输入
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_CONTROL = 0x11
VK_MENU = 0x12                       # Alt
VK_H = 0x48
VK_T = 0x54
KEY_DOWN = 0x8000
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

# 消息
WM_NULL = 0x0000
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_SETTINGCHANGE = 0x001A
WM_DISPLAYCHANGE = 0x007E
WM_SYSCOMMAND = 0x0112
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
WM_HOTKEY = 0x0312
WM_APP = 0x8000
SC_MONITORPOWER = 0xF170
PBT_APMRESUMESUSPEND = 7
PBT_APMRESUMEAUTOMATIC = 18
WTS_SESSION_UNLOCK = 8
NOTIFY_FOR_THIS_SESSION = 0
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_CONTINUOUS = 0x80000000
CF_UNICODETEXT = 13
CF_HDROP = 15
DROPEFFECT_COPY = 1
GMEM_MOVEABLE = 0x0002

# 系统度量
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_CXSMICON = 49
SM_CYSMICON = 50
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MONITOR_DEFAULTTONEAREST = 2

# 托盘 / 菜单 / 图标
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4
NIF_INFO = 0x10
NIIF_INFO = 0x1
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100
MF_STRING = 0x0000
MF_CHECKED = 0x0008
MF_SEPARATOR = 0x0800
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
NULL_PEN = 8
MB_ICONERROR = 0x10

# 内核对象
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0
STD_INPUT_HANDLE = -10
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# COM / 辅助功能
COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = -2147417850     # 线程已在其他套间初始化，仍可使用
VT_I4 = 3
ROLE_SYSTEM_LIST = 0x21


# ---------------------------------------------------------------- 结构体
class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', RECT),
                ('rcWork', RECT), ('dwFlags', wintypes.DWORD)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND), ('uID', wintypes.UINT),
        ('uFlags', wintypes.UINT), ('uCallbackMessage', wintypes.UINT), ('hIcon', wintypes.HICON),
        ('szTip', wintypes.WCHAR * 128), ('dwState', wintypes.DWORD), ('dwStateMask', wintypes.DWORD),
        ('szInfo', wintypes.WCHAR * 256), ('uVersion', wintypes.UINT),
        ('szInfoTitle', wintypes.WCHAR * 64), ('dwInfoFlags', wintypes.DWORD),
        ('guidItem', wintypes.BYTE * 16), ('hBalloonIcon', wintypes.HICON),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [('fIcon', wintypes.BOOL), ('xHotspot', wintypes.DWORD), ('yHotspot', wintypes.DWORD),
                ('hbmMask', wintypes.HBITMAP), ('hbmColor', wintypes.HBITMAP)]


class VARIANT(ctypes.Structure):
    # VARIANT 的联合体最大成员是两个指针组成的记录
    _fields_ = [('vt', ctypes.c_ushort), ('reserved', ctypes.c_ushort * 3),
                ('payload', ctypes.c_void_p * 2)]


class IpOptionInformation(ctypes.Structure):
    _fields_ = [
        ('ttl', ctypes.c_ubyte), ('tos', ctypes.c_ubyte), ('flags', ctypes.c_ubyte),
        ('options_size', ctypes.c_ubyte), ('options_data', ctypes.c_void_p),
    ]


class IcmpEchoReply(ctypes.Structure):
    _fields_ = [
        ('address', ctypes.c_uint32), ('status', ctypes.c_uint32), ('rtt', ctypes.c_uint32),
        ('data_size', ctypes.c_uint16), ('reserved', ctypes.c_uint16),
        ('data', ctypes.c_void_p), ('options', IpOptionInformation),
    ]


class Memory(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD), ('sizes', ctypes.c_uint64 * 7)]


class Interface(ctypes.Structure):
    # 对应 Windows SDK netioapi.h 中的 MIB_IF_ROW2；
    # InterfaceAndOperStatusFlags 为 8 个 BOOLEAN 1-bit 位域（共 1 字节），bit 0 为 HardwareInterface
    _fields_ = [
        ('luid', ctypes.c_uint64), ('index', wintypes.DWORD), ('guid', ctypes.c_byte * 16),
        ('alias', wintypes.WCHAR * 257), ('description', wintypes.WCHAR * 257),
        ('address_len', wintypes.DWORD), ('address', ctypes.c_byte * 32), ('permanent', ctypes.c_byte * 32),
        ('mtu', wintypes.DWORD), ('kind', wintypes.DWORD), ('tunnel', wintypes.DWORD), ('media', wintypes.DWORD),
        ('physical_medium', wintypes.DWORD), ('access', wintypes.DWORD), ('direction', wintypes.DWORD),
        ('flags', ctypes.c_ubyte), ('oper', wintypes.DWORD), ('admin', wintypes.DWORD), ('connected', wintypes.DWORD),
        ('network_guid', ctypes.c_byte * 16), ('connection', wintypes.DWORD),
        ('tx_speed', ctypes.c_uint64), ('rx_speed', ctypes.c_uint64),
        ('received', ctypes.c_uint64), ('in_stats', ctypes.c_uint64 * 8),
        ('sent', ctypes.c_uint64), ('out_stats', ctypes.c_uint64 * 8),
    ]


assert ctypes.sizeof(Interface) == 1352, f'MIB_IF_ROW2 大小不匹配: {ctypes.sizeof(Interface)}'


class InterfaceTable(ctypes.Structure):
    _fields_ = [('count', wintypes.DWORD), ('first', Interface)]


# ---------------------------------------------------------------- 回调类型
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def _sig(func, restype, *argtypes):
    func.restype = restype
    func.argtypes = list(argtypes)
    return func


H = wintypes.HANDLE
HWND = wintypes.HWND
BOOL = wintypes.BOOL
UINT = wintypes.UINT
DWORD = wintypes.DWORD
INT = ctypes.c_int

# ---------------------------------------------------------------- user32
_sig(user32.FindWindowW, HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
_sig(user32.FindWindowExW, HWND, HWND, HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
_sig(user32.EnumWindows, BOOL, WNDENUMPROC, wintypes.LPARAM)
_sig(user32.ShowWindow, BOOL, HWND, INT)
_sig(user32.ShowWindowAsync, BOOL, HWND, INT)
_sig(user32.IsIconic, BOOL, HWND)
_sig(user32.IsWindow, BOOL, HWND)
_sig(user32.IsWindowVisible, BOOL, HWND)
_sig(user32.GetSystemMetrics, INT, INT)
_sig(user32.GetCursorPos, BOOL, ctypes.POINTER(POINT))
_sig(user32.GetAsyncKeyState, ctypes.c_short, INT)
_sig(user32.GetLastInputInfo, BOOL, ctypes.POINTER(LASTINPUTINFO))
_sig(user32.WindowFromPoint, HWND, POINT)
_sig(user32.GetClassNameW, INT, HWND, wintypes.LPWSTR, INT)
_sig(user32.GetAncestor, HWND, HWND, UINT)
_sig(user32.GetWindow, HWND, HWND, UINT)
_sig(user32.GetForegroundWindow, HWND)
_sig(user32.GetWindowThreadProcessId, DWORD, HWND, ctypes.POINTER(DWORD))
_sig(user32.SetForegroundWindow, BOOL, HWND)
_sig(user32.SetWindowPos, BOOL, HWND, HWND, INT, INT, INT, INT, UINT)
_sig(user32.GetWindowRect, BOOL, HWND, ctypes.POINTER(RECT))
_sig(user32.CallWindowProcW, LRESULT, LONG_PTR, HWND, UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.CreatePopupMenu, wintypes.HMENU)
_sig(user32.AppendMenuW, BOOL, wintypes.HMENU, UINT, ctypes.c_size_t, wintypes.LPCWSTR)
_sig(user32.TrackPopupMenu, BOOL, wintypes.HMENU, UINT, INT, INT, INT, HWND, wintypes.LPVOID)
_sig(user32.DestroyMenu, BOOL, wintypes.HMENU)
_sig(user32.CreateIconIndirect, wintypes.HICON, ctypes.POINTER(ICONINFO))
_sig(user32.DestroyIcon, BOOL, wintypes.HICON)
_sig(user32.LoadImageW, H, wintypes.HINSTANCE, wintypes.LPCWSTR, UINT, INT, INT, UINT)
_sig(user32.GetDC, wintypes.HDC, HWND)
_sig(user32.ReleaseDC, INT, HWND, wintypes.HDC)
_sig(user32.RegisterWindowMessageW, UINT, wintypes.LPCWSTR)
_sig(user32.MonitorFromPoint, H, POINT, DWORD)
_sig(user32.GetMonitorInfoW, BOOL, H, ctypes.POINTER(MONITORINFO))
_sig(user32.SendMessageTimeoutW, LRESULT, HWND, UINT, wintypes.WPARAM, wintypes.LPCWSTR,
     UINT, UINT, ctypes.POINTER(ctypes.c_size_t))
_sig(user32.MessageBoxW, INT, HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, UINT)
_sig(user32.PostMessageW, BOOL, HWND, UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.RegisterHotKey, BOOL, HWND, INT, UINT, UINT)
_sig(user32.UnregisterHotKey, BOOL, HWND, INT)
_sig(user32.OpenClipboard, BOOL, HWND)
_sig(user32.EmptyClipboard, BOOL)
_sig(user32.SetClipboardData, H, UINT, H)
_sig(user32.CloseClipboard, BOOL)
_sig(user32.RegisterClipboardFormatW, UINT, wintypes.LPCWSTR)

try:
    GetWindowLongPtr = _sig(user32.GetWindowLongPtrW, LONG_PTR, HWND, INT)
    SetWindowLongPtr = _sig(user32.SetWindowLongPtrW, LONG_PTR, HWND, INT, LONG_PTR)
except AttributeError:
    # 32 位 user32 只导出非 Ptr 版本，此时 LONG_PTR 与 LONG 同宽
    GetWindowLongPtr = _sig(user32.GetWindowLongW, LONG_PTR, HWND, INT)
    SetWindowLongPtr = _sig(user32.SetWindowLongW, LONG_PTR, HWND, INT, LONG_PTR)

# ---------------------------------------------------------------- kernel32
_sig(kernel32.CreateMutexW, H, wintypes.LPVOID, BOOL, wintypes.LPCWSTR)
_sig(kernel32.CreateEventW, H, wintypes.LPVOID, BOOL, BOOL, wintypes.LPCWSTR)
_sig(kernel32.OpenEventW, H, DWORD, BOOL, wintypes.LPCWSTR)
_sig(kernel32.SetEvent, BOOL, H)
_sig(kernel32.CloseHandle, BOOL, H)
_sig(kernel32.WaitForSingleObject, DWORD, H, DWORD)
_sig(kernel32.GetTickCount, DWORD)
_sig(kernel32.SetThreadExecutionState, DWORD, DWORD)
_sig(kernel32.GlobalAlloc, H, UINT, ctypes.c_size_t)
_sig(kernel32.GlobalLock, wintypes.LPVOID, H)
_sig(kernel32.GlobalUnlock, BOOL, H)
_sig(kernel32.GlobalFree, H, H)
_sig(kernel32.GetStdHandle, H, DWORD)
_sig(kernel32.GetSystemTimes, BOOL, ctypes.POINTER(wintypes.FILETIME),
     ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME))
_sig(kernel32.GlobalMemoryStatusEx, BOOL, ctypes.POINTER(Memory))

# ---------------------------------------------------------------- gdi32
_sig(gdi32.GetStockObject, wintypes.HGDIOBJ, INT)
_sig(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_sig(gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, wintypes.HDC, INT, INT)
_sig(gdi32.CreateBitmap, wintypes.HBITMAP, INT, INT, UINT, UINT, wintypes.LPVOID)
_sig(gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_sig(gdi32.CreateSolidBrush, wintypes.HBRUSH, wintypes.COLORREF)
_sig(gdi32.RoundRect, BOOL, wintypes.HDC, INT, INT, INT, INT, INT, INT)
_sig(gdi32.Rectangle, BOOL, wintypes.HDC, INT, INT, INT, INT)
_sig(gdi32.DeleteObject, BOOL, wintypes.HGDIOBJ)
_sig(gdi32.DeleteDC, BOOL, wintypes.HDC)

# ---------------------------------------------------------------- 其他
_sig(shell32.Shell_NotifyIconW, BOOL, DWORD, ctypes.POINTER(NOTIFYICONDATAW))
_sig(shell32.ILCreateFromPathW, ctypes.c_void_p, wintypes.LPCWSTR)
_sig(shell32.ILFree, None, ctypes.c_void_p)
_sig(shell32.SHOpenFolderAndSelectItems, HRESULT, ctypes.c_void_p, UINT, ctypes.c_void_p, DWORD)
_sig(wtsapi32.WTSRegisterSessionNotification, BOOL, HWND, DWORD)
_sig(wtsapi32.WTSUnRegisterSessionNotification, BOOL, HWND)
_sig(ole32.CoInitializeEx, HRESULT, wintypes.LPVOID, DWORD)
_sig(ole32.CoUninitialize, None)
_sig(oleaut32.VariantClear, HRESULT, ctypes.POINTER(VARIANT))
_sig(oleacc.AccessibleObjectFromPoint, HRESULT, POINT, ctypes.POINTER(ctypes.c_void_p),
     ctypes.POINTER(VARIANT))
_sig(dwmapi.DwmGetColorizationColor, HRESULT, ctypes.POINTER(DWORD), ctypes.POINTER(BOOL))
_sig(dwmapi.DwmGetWindowAttribute, HRESULT, HWND, DWORD, wintypes.LPVOID, DWORD)
_sig(iphlpapi.GetIfTable2, DWORD, ctypes.POINTER(ctypes.POINTER(InterfaceTable)))
_sig(iphlpapi.FreeMibTable, None, ctypes.c_void_p)
_sig(iphlpapi.IcmpCreateFile, ctypes.c_void_p)
_sig(iphlpapi.IcmpCloseHandle, BOOL, ctypes.c_void_p)
_sig(iphlpapi.IcmpSendEcho, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
     ctypes.c_void_p, ctypes.c_uint16, ctypes.c_void_p, ctypes.c_void_p,
     ctypes.c_uint32, ctypes.c_uint32)
_sig(ws2_32.inet_addr, ctypes.c_uint32, ctypes.c_char_p)


# ---------------------------------------------------------------- 小工具
def class_name(hwnd):
    """返回窗口类名；句柄无效时返回空串。"""
    if not hwnd:
        return ''
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, len(buf))
    return buf.value


def toplevel_hwnd(widget):
    """Tk 的 winfo_id 是内部子窗口，向上取真正的顶层窗口句柄。"""
    widget.update_idletasks()
    child = widget.winfo_id()
    return user32.GetAncestor(child, GA_ROOT) or child


def add_exstyle(hwnd, bits):
    SetWindowLongPtr(hwnd, GWL_EXSTYLE, GetWindowLongPtr(hwnd, GWL_EXSTYLE) | bits)


def set_click_through(hwnd, enabled):
    """动态开启或关闭窗口鼠标穿透（穿透时鼠标左右键直达下层游戏/桌面，绝不抢点击）。"""
    if not hwnd:
        return
    cur = GetWindowLongPtr(hwnd, GWL_EXSTYLE)
    target = (cur | WS_EX_LAYERED | WS_EX_TRANSPARENT) if enabled else (cur & ~WS_EX_TRANSPARENT)
    if target != cur:
        SetWindowLongPtr(hwnd, GWL_EXSTYLE, target)


def is_own_window(hwnd, own_pid=None):
    """判断指定窗口是否属于本程序进程（例如设置窗口或主面板）。"""
    if not hwnd:
        return False
    pid = DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False
    if own_pid is None:
        import os
        own_pid = os.getpid()
    return pid.value == own_pid


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & KEY_DOWN)


def cursor_pos():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p


def _desktop_insert_after(hwnd):
    """在非桌面激活态下，优先将挂件垫在最底层普通窗口之下、桌面/Wallpaper Engine 之上。"""
    try:
        from desktop_windows import find_desktop_windows
        shell, _lv = find_desktop_windows()
    except Exception:
        shell = None
    if not shell:
        return HWND_BOTTOM
    desk_root = user32.GetAncestor(shell, GA_ROOT) or shell
    if not desk_root:
        return HWND_BOTTOM
    prev = user32.GetWindow(desk_root, GW_HWNDPREV)
    if not prev:
        return HWND_BOTTOM
    if prev == hwnd:
        return None
    if GetWindowLongPtr(prev, GWL_EXSTYLE) & WS_EX_TOPMOST:
        return HWND_BOTTOM
    return prev


def place_on_desktop(hwnd, x=None, y=None, on_desktop=False, show=False):
    """把桌面挂件稳稳放在壁纸（含 Wallpaper Engine 动态壁纸）之上、普通软件之下；切回桌面或 Win+D 后自动恢复可见。"""
    if not hwnd:
        return
    if user32.IsIconic(hwnd) or show:
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    flags = SWP_NOSIZE | SWP_NOACTIVATE
    if x is None or y is None:
        flags |= SWP_NOMOVE
        x, y = 0, 0
    if show:
        flags |= SWP_SHOWWINDOW
    # 无论当前焦点是否在桌面，始终插在桌面容器（含动态壁纸）紧上方、普通软件窗口之下，
    # 避免半屏窗口旁点击桌面时挂件抬到 HWND_TOP 遮挡普通软件并吃掉点击。
    z = _desktop_insert_after(hwnd)
    if z is None:
        flags |= SWP_NOZORDER
        z = HWND_BOTTOM
    user32.SetWindowPos(hwnd, z, int(x), int(y), 0, 0, flags)
