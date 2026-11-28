"""按鼠标所在点查询显示器边界与枚举多显示器信息（支持多显示器与任务栏工作区）。"""
import ctypes

from win32 import (user32, MONITORINFO, MONITORINFOF_PRIMARY, MONITORENUMPROC, RECT,
                   POINT, MONITOR_DEFAULTTONEAREST, SM_CXSCREEN, SM_CYSCREEN)


def bounds(x, y, work=False):
    """返回 (left, top, right, bottom)；work=True 时返回去掉任务栏的工作区。"""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    monitor = user32.MonitorFromPoint(POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return 0, 0, user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)
    r = info.rcWork if work else info.rcMonitor
    return r.left, r.top, r.right, r.bottom


def list_monitors(work=True):
    """枚举当前全部显示器，主屏排在最前，返回 [{'index', 'primary', 'rect', 'full_rect', 'label'}, ...]。"""
    items = []

    def callback(hmon, _hdc, _lprect, _lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcWork if work else info.rcMonitor
            fr = info.rcMonitor
            items.append({
                'primary': bool(info.dwFlags & MONITORINFOF_PRIMARY),
                'rect': (int(r.left), int(r.top), int(r.right), int(r.bottom)),
                'full_rect': (int(fr.left), int(fr.top), int(fr.right), int(fr.bottom)),
            })
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    if not items:
        fb = bounds(0, 0, work=work)
        ff = bounds(0, 0, work=False)
        items.append({'primary': True, 'rect': fb, 'full_rect': ff})

    items.sort(key=lambda m: (not m['primary'], m['full_rect'][0], m['full_rect'][1]))
    for idx, item in enumerate(items, start=1):
        fl, ft, fr, fb = item['full_rect']
        tag = '主屏 · ' if item['primary'] else ''
        item['index'] = idx
        item['label'] = f'显示器 {idx}（{tag}{fr - fl}×{fb - ft}）'
    return items


def primary_work_area():
    """返回主显示器的工作区矩形 (left, top, right, bottom)。"""
    monitors = list_monitors(work=True)
    for m in monitors:
        if m['primary']:
            return m['rect']
    return monitors[0]['rect']
