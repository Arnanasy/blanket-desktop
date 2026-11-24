"""统一组件锚点与多分辨率防出界布局管理。"""
from display_geometry import bounds


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def clamp_to_work_area(x, y, width, height, bounds_fn=bounds, margin=0):
    """将窗口坐标约束在中心点所在显示器的工作区内，避免分辨率切换或拔屏后跑出屏幕。"""
    w = max(1, int(width))
    h = max(1, int(height))
    l, t, r, b = bounds_fn(int(x) + w // 2, int(y) + h // 2, work=True)
    lo_x = l + margin
    hi_x = max(lo_x, r - w - margin)
    lo_y = t + margin
    hi_y = max(lo_y, b - h - margin)
    return max(lo_x, min(int(x), hi_x)), max(lo_y, min(int(y), hi_y))


def capture_anchor(x, y, width, height, bounds_fn=bounds):
    """将窗口当前绝对像素位置转换为所在显示器的相对锚点，便于跨分辨率自适应。"""
    w = max(1, int(width))
    h = max(1, int(height))
    l, t, r, b = bounds_fn(int(x) + w // 2, int(y) + h // 2, work=True)
    span_x = max(1, (r - l) - w)
    span_y = max(1, (b - t) - h)
    rx = max(0.0, min(1.0, (int(x) - l) / span_x))
    ry = max(0.0, min(1.0, (int(y) - t) / span_y))
    return {
        'rx': round(rx, 4),
        'ry': round(ry, 4),
        'mon': [int(l), int(t), int(r), int(b)],
    }


def resolve_anchor(anchor, fallback_pos, width, height, bounds_fn=bounds, margin=0):
    """优先按相对锚点还原坐标（分辨率变化时自动按比例适配），兼容旧版纯像素配置。"""
    w = max(1, int(width))
    h = max(1, int(height))
    fx, fy = int(fallback_pos[0]), int(fallback_pos[1])
    if isinstance(anchor, dict) and _is_number(anchor.get('rx')) and _is_number(anchor.get('ry')):
        rx = max(0.0, min(1.0, float(anchor['rx'])))
        ry = max(0.0, min(1.0, float(anchor['ry'])))
        mon = anchor.get('mon')
        if isinstance(mon, list) and len(mon) == 4 and all(_is_number(v) for v in mon):
            probe_x = int((mon[0] + mon[2]) / 2)
            probe_y = int((mon[1] + mon[3]) / 2)
        else:
            probe_x = fx + w // 2
            probe_y = fy + h // 2
        l, t, r, b = bounds_fn(probe_x, probe_y, work=True)
        span_x = max(0, (r - l) - w - margin * 2)
        span_y = max(0, (b - t) - h - margin * 2)
        x = l + margin + round(rx * span_x)
        y = t + margin + round(ry * span_y)
        return clamp_to_work_area(x, y, w, h, bounds_fn=bounds_fn, margin=margin)
    return clamp_to_work_area(fx, fy, w, h, bounds_fn=bounds_fn, margin=margin)


class AnchorRegistry:
    """统一管理挂件重排回调，在显示器布局变化或系统唤醒时一次性归位全部挂件。"""

    def __init__(self):
        self._callbacks = []

    def register(self, callback):
        if callable(callback) and callback not in self._callbacks:
            self._callbacks.append(callback)

    def relayout_all(self):
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception:
                pass
