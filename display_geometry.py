"""按鼠标所在点查询显示器边界（支持多显示器与任务栏工作区）。"""
import ctypes

from win32 import user32, MONITORINFO, POINT, MONITOR_DEFAULTTONEAREST, SM_CXSCREEN, SM_CYSCREEN


def bounds(x, y, work=False):
    """返回 (left, top, right, bottom)；work=True 时返回去掉任务栏的工作区。"""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    monitor = user32.MonitorFromPoint(POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return 0, 0, user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)
    r = info.rcWork if work else info.rcMonitor
    return r.left, r.top, r.right, r.bottom
