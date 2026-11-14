import ctypes
from ctypes import wintypes
from desktop_widgets import bounds

SW_HIDE=0
SW_SHOW=5
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



def invalidate_cache():
    global _desktop_cache
    _desktop_cache = (None, None)

class LVHITTESTINFO(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT), ("flags", wintypes.UINT),
        ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
    ]


class DesktopClick:
    """共享落点检测；仅在鼠标钩子未安装时通过 check() 轮询左键。"""

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
