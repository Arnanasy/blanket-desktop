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

# ---------------------------------------------------------------- 常量
# 窗口样式与位置
GWL_EXSTYLE = -20
GWLP_WNDPROC = -4
WS_EX_TOOLWINDOW = 0x00000080        # 不在任务栏 / Alt+Tab 中出现
WS_EX_NOACTIVATE = 0x08000000        # 点击不抢焦点
HWND_TOPMOST = -1
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
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
WH_MOUSE_LL = 14

# 消息
WM_NULL = 0x0000
WM_QUIT = 0x0012
WM_SETTINGCHANGE = 0x001A
WM_DISPLAYCHANGE = 0x007E
WM_COMMAND = 0x0111
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
WM_APP = 0x8000
PBT_APMRESUMESUSPEND = 7
PBT_APMRESUMEAUTOMATIC = 18
WTS_SESSION_UNLOCK = 8
NOTIFY_FOR_THIS_SESSION = 0
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002

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
NIM_DELETE = 2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4
TPM_RIGHTBUTTON = 0x0002
MF_STRING = 0x0000
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


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('pt', POINT), ('mouseData', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


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


# ---------------------------------------------------------------- 回调类型
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


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
_sig(user32.IsWindow, BOOL, HWND)
_sig(user32.IsWindowVisible, BOOL, HWND)
_sig(user32.GetSystemMetrics, INT, INT)
_sig(user32.GetCursorPos, BOOL, ctypes.POINTER(POINT))
_sig(user32.GetAsyncKeyState, ctypes.c_short, INT)
_sig(user32.GetLastInputInfo, BOOL, ctypes.POINTER(LASTINPUTINFO))
_sig(user32.WindowFromPoint, HWND, POINT)
_sig(user32.GetClassNameW, INT, HWND, wintypes.LPWSTR, INT)
_sig(user32.GetAncestor, HWND, HWND, UINT)
_sig(user32.GetForegroundWindow, HWND)
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
_sig(user32.SetWindowsHookExW, H, INT, HOOKPROC, wintypes.HINSTANCE, DWORD)
_sig(user32.CallNextHookEx, LRESULT, H, INT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.UnhookWindowsHookEx, BOOL, H)
_sig(user32.GetMessageW, BOOL, ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT)
_sig(user32.TranslateMessage, BOOL, ctypes.POINTER(wintypes.MSG))
_sig(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wintypes.MSG))
_sig(user32.PostMessageW, BOOL, HWND, UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.PostThreadMessageW, BOOL, DWORD, UINT, wintypes.WPARAM, wintypes.LPARAM)

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
_sig(kernel32.GetCurrentThreadId, DWORD)
_sig(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
_sig(kernel32.GetStdHandle, H, DWORD)

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
_sig(wtsapi32.WTSRegisterSessionNotification, BOOL, HWND, DWORD)
_sig(wtsapi32.WTSUnRegisterSessionNotification, BOOL, HWND)
_sig(ole32.CoInitializeEx, HRESULT, wintypes.LPVOID, DWORD)
_sig(ole32.CoUninitialize, None)
_sig(oleaut32.VariantClear, HRESULT, ctypes.POINTER(VARIANT))
_sig(oleacc.AccessibleObjectFromPoint, HRESULT, POINT, ctypes.POINTER(ctypes.c_void_p),
     ctypes.POINTER(VARIANT))
_sig(dwmapi.DwmGetColorizationColor, HRESULT, ctypes.POINTER(DWORD), ctypes.POINTER(BOOL))


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


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & KEY_DOWN)


def cursor_pos():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p
