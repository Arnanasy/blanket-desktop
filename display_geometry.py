import ctypes
from ctypes import wintypes

u = ctypes.windll.user32
class MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('monitor', wintypes.RECT), ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
u.MonitorFromPoint.restype = wintypes.HANDLE
u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]

def bounds(x, y, work=False):
    info = MonitorInfo(); info.cbSize = ctypes.sizeof(info)
    if not u.GetMonitorInfoW(u.MonitorFromPoint(wintypes.POINT(x,y),2), ctypes.byref(info)):
        return (0,0,u.GetSystemMetrics(0),u.GetSystemMetrics(1))
    r = info.work if work else info.monitor
    return r.left,r.top,r.right,r.bottom

