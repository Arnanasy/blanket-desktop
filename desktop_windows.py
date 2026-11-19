import ctypes
from ctypes import wintypes
from display_geometry import bounds

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

class Variant(ctypes.Structure):
    # VARIANT's union also holds a record consisting of two pointers.
    _fields_ = [("vt", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3),
                ("payload", ctypes.c_void_p * 2)]


def accessible_blank(point):
    """Inspect the accessibility object under a confirmed desktop list view."""
    ole32 = ctypes.windll.ole32
    status = ole32.CoInitializeEx(None, 2)
    if status not in (0, 1, -2147417850):  # already initialized in another apartment is also usable
        return None
    try:
        return _accessible_list_background(point)
    finally:
        if status in (0, 1):
            ole32.CoUninitialize()


def _accessible_list_background(point):
    oleacc = ctypes.windll.oleacc
    get_object = oleacc.AccessibleObjectFromPoint
    ptr = ctypes.c_void_p
    get_object.argtypes = [wintypes.POINT, ctypes.POINTER(ptr), ctypes.POINTER(Variant)]
    obj = ptr()
    child = Variant()
    role = Variant()
    if get_object(point, ctypes.byref(obj), ctypes.byref(child)) != 0 or not obj:
        return None
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ptr))).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ptr)(table[2])
    get_role = ctypes.WINFUNCTYPE(ctypes.c_long, ptr, Variant, ctypes.POINTER(Variant))(table[13])
    try:
        if get_role(obj, child, ctypes.byref(role)) != 0 or role.vt != 3:
            return None
        # ROLE_SYSTEM_LIST is the background; list items and unknown roles never toggle.
        return role.payload[0] == 0x21
    finally:
        ctypes.windll.oleaut32.VariantClear(ctypes.byref(role))
        ctypes.windll.oleaut32.VariantClear(ctypes.byref(child))
        release(obj)


class DesktopClick:
    """共享落点检测；仅在鼠标钩子未安装时通过 check() 轮询左键。"""

    VK_LBUTTON = 0x01
    LVM_HITTEST = 0x1000 + 18
    SMTO_ABORTIFHUNG = 0x0002
    DRAG_THRESHOLD = 6   # 按下到松开位移超过此值视为框选，不触发

    def __init__(self, on_unavailable=None):
        self.on_unavailable = on_unavailable
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
        shell, lv = find_desktop_windows()
        if hwnd and hwnd == shell:
            return True
        if hwnd and hwnd == lv:
            result = accessible_blank(wintypes.POINT(x, y))
            if result is None and not getattr(self, "_warned", False):
                self._warned = True
                if self.on_unavailable:
                    self.on_unavailable()
            return result is True
        return False
