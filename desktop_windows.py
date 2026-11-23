"""桌面图标窗口的查找、显示/隐藏，以及“点在桌面空白处”的判定。"""
import ctypes

from display_geometry import bounds
from win32 import (user32, kernel32, ole32, oleaut32, oleacc, LASTINPUTINFO, POINT, VARIANT,
                   WNDENUMPROC, SW_HIDE, SW_SHOW, VK_LBUTTON, COINIT_APARTMENTTHREADED, S_OK,
                   S_FALSE, RPC_E_CHANGED_MODE, VT_I4, ROLE_SYSTEM_LIST, key_down, cursor_pos)

# 高 DPI 适配，避免高缩放下界面模糊；同时让 GetCursorPos 与屏幕尺寸自洽。
# 必须在创建任何 Tk 窗口之前执行，因此放在模块导入时。
try:
    ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)
except (OSError, AttributeError):
    try:
        ctypes.WinDLL('user32').SetProcessDPIAware()
    except (OSError, AttributeError):
        pass


# ---------------------------------------------------------------- 桌面窗口
_desktop_cache = (None, None)


def find_desktop_windows():
    """返回 (SHELLDLL_DefView, SysListView32) 句柄，找不到对应项为 None。结果会缓存。"""
    global _desktop_cache
    if _desktop_cache[1] and user32.IsWindow(_desktop_cache[1]):
        return _desktop_cache
    _desktop_cache = _find_desktop_windows()
    return _desktop_cache


def _find_desktop_windows():
    progman = user32.FindWindowW('Progman', None)
    if progman:
        shell = user32.FindWindowExW(progman, None, 'SHELLDLL_DefView', None)
        if shell:
            lv = user32.FindWindowExW(shell, None, 'SysListView32', None)
            if lv:
                return shell, lv

    # 兼容桌面被 WorkerW 重定向的情况：枚举所有顶层窗口
    found = []

    def callback(hwnd, _lparam):
        shell = user32.FindWindowExW(hwnd, None, 'SHELLDLL_DefView', None)
        if shell:
            lv = user32.FindWindowExW(shell, None, 'SysListView32', None)
            if lv:
                found.append((shell, lv))
                return False        # 找到即停止枚举
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found[0] if found else (None, None)


def invalidate_cache():
    global _desktop_cache
    _desktop_cache = (None, None)


def icons_visible():
    """桌面图标当前是否可见；无法获取时返回 None。"""
    _shell, lv = find_desktop_windows()
    if not lv:
        return None
    return bool(user32.IsWindowVisible(lv))


def set_icons_visible(show, async_=False):
    """显示(True)/隐藏(False)桌面图标，成功返回 True。"""
    _shell, lv = find_desktop_windows()
    if not lv:
        return False
    flag = SW_SHOW if show else SW_HIDE
    if async_:
        user32.ShowWindowAsync(lv, flag)
    else:
        user32.ShowWindow(lv, flag)
    return True


# ---------------------------------------------------------------- 输入状态
def input_idle_seconds():
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    # DWORD 毫秒计数每 49.7 天回绕一次
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def get_cursor():
    """返回当前鼠标坐标 (x, y)。"""
    p = cursor_pos()
    return p.x, p.y


def in_hotzone(x, y, mode):
    """判断鼠标是否处于热区。边缘用显示器极值判断，不受 DPI 缩放影响。"""
    left, top, right, bottom = bounds(x, y)
    edge = 4
    if mode == 'top':
        return y <= top + edge
    if mode == 'any':
        return x <= left + edge or y <= top + edge or x >= right - edge or y >= bottom - edge
    if mode == 'corner':
        return x <= left + 12 and y <= top + 12
    return False


# ---------------------------------------------------------------- 辅助功能判定
_RELEASE_FN = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_GET_ROLE_FN = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, VARIANT, ctypes.POINTER(VARIANT))


def accessible_blank(point):
    """用 MSAA 判断坐标下的对象是否为列表背景；无法判断时返回 None。"""
    status = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    if status not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
        return None
    try:
        return _accessible_list_background(point)
    finally:
        if status in (S_OK, S_FALSE):
            ole32.CoUninitialize()


def _accessible_list_background(point):
    obj = ctypes.c_void_p()
    child = VARIANT()
    role = VARIANT()
    if oleacc.AccessibleObjectFromPoint(point, ctypes.byref(obj), ctypes.byref(child)) != S_OK or not obj:
        return None
    # IAccessible 虚表：[2] Release，[13] get_accRole
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = _RELEASE_FN(table[2])
    get_role = _GET_ROLE_FN(table[13])
    try:
        if get_role(obj, child, ctypes.byref(role)) != S_OK or role.vt != VT_I4:
            return None
        # VT_I4 仅低 32 位有效；只有列表背景才算空白，列表项和未知角色一律不切换
        return ((role.payload[0] or 0) & 0xFFFFFFFF) == ROLE_SYSTEM_LIST
    finally:
        oleaut32.VariantClear(ctypes.byref(role))
        oleaut32.VariantClear(ctypes.byref(child))
        release(obj)


class DesktopClick:
    """基于轻量化左键轮询的桌面空白点击检测（不挂全局鼠标钩子，零鼠标延迟）。"""

    DRAG_THRESHOLD = 6   # 按下到松开位移超过此值视为框选，不触发

    def __init__(self, on_unavailable=None):
        self.on_unavailable = on_unavailable
        self._held = False
        self._down_blank = False
        self._down_x = self._down_y = 0
        self._warned = False

    def reset(self):
        self._held = key_down(VK_LBUTTON)
        self._down_blank = False

    def check(self):
        """主线程轮询调用，返回 True 表示这次点了桌面空白。"""
        down = key_down(VK_LBUTTON)
        if down and not self._held:
            self._held = True
            self._down_x, self._down_y = get_cursor()
            self._down_blank = self.is_blank_click(self._down_x, self._down_y)
        elif not down and self._held:
            self._held = False
            x, y = get_cursor()
            moved = abs(x - self._down_x) + abs(y - self._down_y)
            # 仅单击（非拖拽框选）才判断落点
            if self._down_blank and moved <= self.DRAG_THRESHOLD:
                return self.is_blank_click(x, y)
        return False

    def is_blank_click(self, x, y):
        hwnd = user32.WindowFromPoint(POINT(x, y))
        shell, lv = find_desktop_windows()
        if hwnd and hwnd == shell:
            return True
        if hwnd and hwnd == lv:
            result = accessible_blank(POINT(x, y))
            if result is None and not self._warned:
                self._warned = True
                if self.on_unavailable:
                    self.on_unavailable()
            return result is True
        return False
