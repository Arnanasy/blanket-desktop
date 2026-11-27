"""按需呼出的 Windows 系统性能监控挂件（无常驻轮询线程，按启用项采集；支持真逐像素透明与游戏一键置顶）。"""
import ctypes as c
from ctypes import wintypes as w
import os
import queue
import socket
from threading import Thread
import time
import tkinter as tk
from tkinter import ttk, colorchooser, font

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    HAS_PIL = True
except Exception:
    HAS_PIL = False

from win32 import (user32, kernel32, iphlpapi, ws2_32, toplevel_hwnd, add_exstyle, set_click_through,
                   update_layered_rgba, cursor_pos, GetWindowLongPtr,
                   GWL_EXSTYLE, GW_HWNDPREV, HWND_TOPMOST, SW_SHOWNOACTIVATE,
                   SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE, SWP_SHOWWINDOW,
                   WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
                   INVALID_HANDLE_VALUE, IcmpEchoReply, Memory,
                   Interface, InterfaceTable)

DEFAULT_PING_HOST = '223.5.5.5'
PING_INTERVAL = 2.0          # Ping 采样间隔（秒），在后台线程执行不阻塞界面
PING_TIMEOUT_MS = 1000
NVML_TEMPERATURE_GPU = 0

_FONT_CACHE = {}
_FONT_PATH_PROBED = False
_FONT_PATH = None


def _find_monitor_font_path():
    """查找系统清晰中英文字体（优先微软雅黑），避免透明窗口 ClearType 彩边与缺字。"""
    global _FONT_PATH_PROBED, _FONT_PATH
    if _FONT_PATH_PROBED:
        return _FONT_PATH
    _FONT_PATH_PROBED = True
    fonts_dir = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
    for name in ('msyh.ttc', 'msyhbd.ttc', 'simhei.ttf', 'segoeui.ttf'):
        candidate = os.path.join(fonts_dir, name)
        if os.path.isfile(candidate):
            _FONT_PATH = candidate
            return _FONT_PATH
    return None


def _load_pil_font(px_size):
    path = _find_monitor_font_path()
    if not HAS_PIL or not path:
        return None
    key = (path, max(10, int(px_size)))
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        loaded = ImageFont.truetype(path, key[1])
        _FONT_CACHE[key] = loaded
        return loaded
    except OSError:
        return None


def _parse_rgb(widget, color_str, fallback=(241, 245, 249)):
    try:
        r16, g16, b16 = widget.winfo_rgb(color_str)
        return (r16 >> 8, g16 >> 8, b16 >> 8)
    except Exception:
        s = str(color_str or '').lstrip('#')
        if len(s) == 6:
            try:
                return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
            except ValueError:
                pass
        return fallback


def render_monitor_rgba(rows, size_pt, shape, fg_rgb, bg_rgb, ui_scale=1.0):
    """用 Pillow 2x 超采样渲染真逐像素 Alpha 监控帧，返回 (width, height, premul_bgra_bytes)；不可用时返回 None。"""
    if not HAS_PIL:
        return None
    scale = max(1.0, float(ui_scale))
    ss = 2
    px_size = max(10, round(int(size_pt) * scale * 4 / 3))
    pil_font = _load_pil_font(px_size * ss)
    if pil_font is None:
        return None

    rows = rows or [('—', '')]
    dummy = ImageDraw.Draw(Image.new('RGBA', (4, 4), (0, 0, 0, 0)))

    # 精确测量每行标签列与数值列尺寸，使中英文混排工整对齐
    ascent, descent = pil_font.getmetrics()
    line_h = max(px_size * ss, ascent + descent)
    line_gap = max(4 * ss, round(line_h * 0.22))
    col_gap = max(14 * ss, round(px_size * ss * 0.85))
    pad_x = max(12 * ss, round(px_size * ss * 0.65))
    pad_y = max(9 * ss, round(px_size * ss * 0.48))

    max_label_w = 0
    max_val_w = 0
    has_val = False
    for label, val in rows:
        lb = dummy.textbbox((0, 0), label, font=pil_font)
        max_label_w = max(max_label_w, max(0, lb[2] - lb[0]))
        if val:
            has_val = True
            vb = dummy.textbbox((0, 0), val, font=pil_font)
            max_val_w = max(max_val_w, max(0, vb[2] - vb[0]))

    content_w = max_label_w + (col_gap + max_val_w if has_val else 0)
    content_h = len(rows) * line_h + max(0, len(rows) - 1) * line_gap
    sw = max(40 * ss, content_w + pad_x * 2)
    sh = max(24 * ss, content_h + pad_y * 2)
    # 保证 2x 尺寸是偶数，缩放回逻辑像素尺寸零舍入偏差
    if sw % ss:
        sw += 1
    if sh % ss:
        sh += 1
    w_px, h_px = sw // ss, sh // ss

    is_transparent = (shape == '透明')
    # 透明模式底色 Alpha=1/255（肉眼 100% 透明，但桌面可交互时整个卡片区域都能顺畅拖动/滚轮/右键）
    base_fill = (0, 0, 0, 1) if is_transparent else (0, 0, 0, 0)
    im = Image.new('RGBA', (sw, sh), base_fill)
    draw = ImageDraw.Draw(im)

    if shape == '圆角':
        rad = min(10 * ss, sw // 3, sh // 3)
        draw.rounded_rectangle((0, 0, sw - 1, sh - 1), radius=rad, fill=(*bg_rgb, 255))
    elif shape == '方形':
        draw.rectangle((0, 0, sw - 1, sh - 1), fill=(*bg_rgb, 255))

    val_x = pad_x + max_label_w + col_gap
    fg_rgba = (*fg_rgb, 255)

    if is_transparent:
        # 透明文字加一层细腻柔和的半透明微阴影（绝非硬黑边或 ClearType 彩边），白底网页/深色桌面/游戏画面均清晰可读
        lum = 0.299 * fg_rgb[0] + 0.587 * fg_rgb[1] + 0.114 * fg_rgb[2]
        shadow_rgba = (12, 16, 28, 175) if lum >= 120 else (255, 255, 255, 190)
        shadow_layer = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow_layer)
        y = pad_y
        for label, val in rows:
            for dx, dy in ((0, ss), (ss, ss), (0, 0)):
                sdraw.text((pad_x + dx, y + dy), label, font=pil_font, fill=shadow_rgba)
                if val:
                    sdraw.text((val_x + dx, y + dy), val, font=pil_font, fill=shadow_rgba)
            y += line_h + line_gap
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=1.6 * ss))
        im = Image.alpha_composite(im, shadow_layer)
        draw = ImageDraw.Draw(im)

    y = pad_y
    for label, val in rows:
        draw.text((pad_x, y), label, font=pil_font, fill=fg_rgba)
        if val:
            draw.text((val_x, y), val, font=pil_font, fill=fg_rgba)
        y += line_h + line_gap

    out = im.resize((w_px, h_px), Image.LANCZOS)
    premul = out.convert('RGBa')
    r_ch, g_ch, b_ch, a_ch = premul.split()
    bgra = Image.merge('RGBA', (b_ch, g_ch, r_ch, a_ch)).tobytes()
    return w_px, h_px, bgra


class GpuTempReader:
    """通过 NVIDIA 驱动自带的 nvml.dll 直接读取显卡温度（零权限、耗时 < 0.2ms）。"""

    _DLL_PATHS = (
        'nvml.dll',
        r'C:\Windows\System32\nvml.dll',
        r'C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll',
    )

    def __init__(self):
        self._nvml = None
        self._device = None
        self._probed = False

    def _init(self):
        self._probed = True
        for path in self._DLL_PATHS:
            try:
                dll = c.CDLL(path)
                dll.nvmlInit_v2.restype = c.c_int
                dll.nvmlShutdown.restype = c.c_int
                dll.nvmlDeviceGetCount_v2.argtypes = [c.POINTER(c.c_uint)]
                dll.nvmlDeviceGetCount_v2.restype = c.c_int
                dll.nvmlDeviceGetHandleByIndex_v2.argtypes = [c.c_uint, c.POINTER(c.c_void_p)]
                dll.nvmlDeviceGetHandleByIndex_v2.restype = c.c_int
                dll.nvmlDeviceGetTemperature.argtypes = [c.c_void_p, c.c_int, c.POINTER(c.c_uint)]
                dll.nvmlDeviceGetTemperature.restype = c.c_int
                if dll.nvmlInit_v2() != 0:
                    continue
                count = c.c_uint()
                if dll.nvmlDeviceGetCount_v2(c.byref(count)) == 0 and count.value > 0:
                    dev = c.c_void_p()
                    if dll.nvmlDeviceGetHandleByIndex_v2(0, c.byref(dev)) == 0:
                        self._nvml = dll
                        self._device = dev
                        return
                dll.nvmlShutdown()
            except OSError:
                continue

    def read(self):
        if not self._probed:
            self._init()
        if not self._nvml or not self._device:
            return None
        temp = c.c_uint()
        if self._nvml.nvmlDeviceGetTemperature(self._device, NVML_TEMPERATURE_GPU, c.byref(temp)) == 0:
            return int(temp.value)
        return None


def ping_once(host):
    """使用 Windows 原生 ICMP API 发一次回显测试，返回往返毫秒数，失败或超时返回 None。"""
    target = (host or DEFAULT_PING_HOST).strip() or DEFAULT_PING_HOST
    try:
        ip_str = socket.gethostbyname(target)
    except OSError:
        return None
    addr = ws2_32.inet_addr(ip_str.encode('ascii', 'ignore'))
    if addr == 0xFFFFFFFF:
        return None
    handle = iphlpapi.IcmpCreateFile()
    if not handle or handle == INVALID_HANDLE_VALUE:
        return None
    try:
        buf = c.create_string_buffer(c.sizeof(IcmpEchoReply) + 64)
        count = iphlpapi.IcmpSendEcho(handle, addr, b'ping', 4, None, buf, len(buf), PING_TIMEOUT_MS)
        if not count:
            return None
        reply = IcmpEchoReply.from_buffer_copy(buf)
        return int(reply.rtt) if reply.status == 0 else None
    finally:
        iphlpapi.IcmpCloseHandle(handle)


# MIB_IF_ROW2 物理网卡类型：以太网(6)、Wi-Fi(71)、WWAN(243/244)
PHYSICAL_IF_TYPES = (6, 71, 243, 244)
VALID_EDGES = ('左', '右')
VALID_SHAPES = ('圆角', '方形', '透明')


def network():
    table = c.POINTER(InterfaceTable)()
    if iphlpapi.GetIfTable2(c.byref(table)):
        return None
    try:
        rows = (Interface * table.contents.count).from_address(c.addressof(table.contents.first))
        return {
            row.luid: (row.received, row.sent)
            for row in rows
            if (row.flags & 1) and row.oper == 1 and row.kind in PHYSICAL_IF_TYPES
        }
    finally:
        iphlpapi.FreeMibTable(table)


def speed(value):
    if value < 1024:
        return f'{value:.0f} B/s'
    if value < 1048576:
        return f'{value / 1024:.1f} KiB/s'
    return f'{value / 1048576:.1f} MiB/s'


class Sampler:
    def __init__(self):
        self.cpu = None
        self.net = None
        self.last = 0.0
        self.gpu = GpuTempReader()
        self._ping_text = '—'
        self._ping_host = ''
        self._ping_at = 0.0
        self._ping_seq = 0
        self._ping_active_seq = None
        self._ping_results = queue.SimpleQueue()

    def _drain_ping_results(self):
        """在主线程消费后台 Ping 线程回传的结果，避免跨线程直接改写实例属性。"""
        while True:
            try:
                seq, rtt = self._ping_results.get_nowait()
            except queue.Empty:
                return
            if seq == self._ping_seq:
                self._ping_text = f'{rtt} ms' if rtt is not None else '超时'
            if seq == self._ping_active_seq:
                self._ping_active_seq = None

    def _schedule_ping(self, host, now):
        self._drain_ping_results()
        target = (host or DEFAULT_PING_HOST).strip() or DEFAULT_PING_HOST
        if target != self._ping_host:
            self._ping_host = target
            self._ping_text = '—'
            self._ping_at = 0.0
            self._ping_seq += 1
        seq = self._ping_seq
        if self._ping_active_seq == seq or now - self._ping_at < PING_INTERVAL:
            return
        self._ping_active_seq = seq
        self._ping_at = now
        results = self._ping_results

        def worker():
            try:
                rtt = ping_once(target)
            except Exception:
                rtt = None
            results.put((seq, rtt))

        Thread(target=worker, name='monitor-ping', daemon=True).start()

    def read(self, cfg, now):
        """按配置仅采集启用的监控项，返回键值映射。"""
        readings = {}
        if cfg.get('cpu'):
            times = [w.FILETIME() for _ in range(3)]
            percent = '—'
            if kernel32.GetSystemTimes(*(c.byref(t) for t in times)):
                idle, kernel_time, user = [(t.dwHighDateTime << 32) | t.dwLowDateTime for t in times]
                total = kernel_time + user
                if self.cpu and total > self.cpu[1]:
                    usage = 1 - (idle - self.cpu[0]) / (total - self.cpu[1])
                    percent = f'{100 * max(0, min(1, usage)):.0f}%'
                self.cpu = (idle, total)
            readings['cpu'] = 'CPU    ' + percent
        else:
            self.cpu = None

        if cfg.get('memory'):
            mem = Memory(length=c.sizeof(Memory))
            value = f'{mem.load}%' if kernel32.GlobalMemoryStatusEx(c.byref(mem)) else '—'
            readings['memory'] = '内存    ' + value

        if cfg.get('temp'):
            temp = self.gpu.read()
            readings['temp'] = f'温度    {temp}°C' if temp is not None else '温度    —'

        if cfg.get('net'):
            current = network()
            rates = ['—', '—']
            if current and self.net and now > self.last:
                common = current.keys() & self.net.keys()
                if common:
                    dt = now - self.last
                    rates = [
                        speed(sum(max(0, current[i][j] - self.net[i][j]) for i in common) / dt)
                        for j in (0, 1)
                    ]
            readings['down'] = '↓    ' + rates[0]
            readings['up'] = '↑    ' + rates[1]
            self.net = current
            self.last = now
        else:
            self.net = None

        if cfg.get('ping'):
            self._schedule_ping(cfg.get('ping_host', DEFAULT_PING_HOST), now)
            readings['ping'] = '延迟    ' + self._ping_text

        return readings


class SystemMonitor:
    defaults = dict(edge='左', size=15, alpha=90, fg='#F1F5F9', bg='#243040',
                    shape='圆角', cpu=True, memory=True, temp=True, net=True,
                    ping=True, ping_host=DEFAULT_PING_HOST, pinned=False, over_fullscreen=True)
    keycolor = '#010203'

    def __init__(self, widgets, bounds):
        self.widgets = widgets
        self.bounds = bounds
        self.cfg = self.defaults.copy()
        saved = widgets.cfg.get('monitor', {})
        if isinstance(saved, dict):
            for k, v in self.defaults.items():
                if type(saved.get(k)) is type(v):
                    self.cfg[k] = saved[k]
            pos = saved.get('position')
            if isinstance(pos, list) and len(pos) == 2 and all(type(i) is int for i in pos):
                self.cfg['position'] = pos
        self.cfg['size'] = max(9, min(48, self.cfg['size']))
        self.cfg['alpha'] = max(30, min(100, self.cfg['alpha']))
        if self.cfg['edge'] not in VALID_EDGES:
            self.cfg['edge'] = self.defaults['edge']
        if self.cfg['shape'] not in VALID_SHAPES:
            self.cfg['shape'] = self.defaults['shape']

        self.win = tk.Toplevel(widgets.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self._layered = bool(HAS_PIL and _find_monitor_font_path())
        if self._layered:
            # 逐像素 Alpha 分层模式：绝不同时设置 -transparentcolor / -alpha，避免与 UpdateLayeredWindow 冲突
            self.win.attributes('-topmost', True)
        else:
            self.win.attributes('-topmost', True, '-transparentcolor', self.keycolor)
        for key in ('fg', 'bg'):
            try:
                self.win.winfo_rgb(self.cfg[key])
            except tk.TclError:
                self.cfg[key] = self.defaults[key]

        self.canvas = tk.Canvas(self.win, bg=self.keycolor, highlightthickness=0)
        self.canvas.pack()
        # 工具窗口 + 不抢焦点，保证浮在游戏/全屏窗口上时不会把游戏切出去
        self.hwnd = toplevel_hwnd(self.win)
        extra_bits = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | (WS_EX_LAYERED if self._layered else 0)
        add_exstyle(self.hwnd, extra_bits)
        self.font = font.Font(family='Segoe UI', size=self.cfg['size'])
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Button-3>', self.settings)

        self.visible = False
        self.dialog = None
        self.drag_offset = None
        self._click_through = False
        self._layered_frame = None
        self._size = (120, 48)
        self._pos = list(self.cfg.get('position', [0, 0]))
        self._alpha_scale = 1.0
        self.edge_start = None
        self.edge_y = 0
        self.edge_monitor = None
        self.last_inside = 0.0
        self.last_sample = 0.0
        self._last_foreground = None
        self.fade = None
        self.sampler = Sampler()
        self.text = ''
        self.readings = {}
        self.save_timer = None
        self.controls = {}
        widgets.cfg['monitor'] = self.cfg

    def _window_size(self):
        if self._layered and self._size[0] > 0 and self._size[1] > 0:
            return self._size
        return max(1, self.canvas.winfo_reqwidth()), max(1, self.canvas.winfo_reqheight())

    def _commit_layered(self, opacity=None, x=None, y=None):
        if not self._layered or not self._layered_frame or not self.hwnd:
            return False
        w_px, h_px, bgra = self._layered_frame
        if opacity is None:
            opacity = round(255 * (self.cfg['alpha'] / 100.0) * self._alpha_scale)
        return update_layered_rgba(self.hwnd, w_px, h_px, bgra, x=x, y=y, opacity=opacity)

    def _notify_pin_ui(self):
        bar = getattr(self.widgets, 'bar', None)
        if bar is not None and hasattr(bar, 'refresh_monitor_pin_ui'):
            bar.refresh_monitor_pin_ui()

    def is_pinned(self):
        return bool(self.cfg.get('pinned', False))

    def set_pinned(self, pinned):
        """一键开启或关闭系统监控固定置顶（开启时自动确保全屏/游戏时保持置顶，打游戏直接可见）。"""
        target = bool(pinned)
        committed = self.widgets.cfg.get('monitor')
        if not isinstance(committed, dict):
            committed = self.cfg
        self.cfg['pinned'] = target
        committed['pinned'] = target
        if target:
            # 一键置顶默认同时启用“全屏/游戏时保持置顶”，避免打游戏仍被隐藏
            self.cfg['over_fullscreen'] = True
            committed['over_fullscreen'] = True
        if 'pinned' in self.controls:
            self.controls['pinned'].set(target)
        if target and 'over_fullscreen' in self.controls:
            self.controls['over_fullscreen'].set(True)

        now = time.monotonic()
        if target:
            if not self.visible:
                pt = cursor_pos()
                self.show(pt.x, pt.y, now)
            else:
                self.sample(now)
                self.render()
                self.raise_()
        else:
            editing = bool(self.dialog and self.dialog.winfo_exists())
            if self.visible and not editing:
                self._hide()

        self._notify_pin_ui()
        if self.save_timer is not None:
            self.win.after_cancel(self.save_timer)
            self.save_timer = None
        self.flush_save()
        return target

    def toggle_pin(self, enable=None):
        target = (not self.is_pinned()) if enable is None else bool(enable)
        return self.set_pinned(target)

    def _hide(self):
        if self.fade:
            self.win.after_cancel(self.fade)
            self.fade = None
        self.win.withdraw()
        self.visible = False
        self._click_through = False
        set_click_through(self.hwnd, False)
        # 隐藏后清空旧采样基准，避免重新出现时第一帧算出整段隐藏期的平均假值
        self.sampler.cpu = None
        self.sampler.net = None

    def _build_rows(self):
        cfg = self.cfg
        items = [
            ('cpu', 'CPU', cfg['cpu']),
            ('memory', '内存', cfg['memory']),
            ('temp', '温度', cfg['temp']),
            ('down', '↓', cfg['net']),
            ('up', '↑', cfg['net']),
            ('ping', '延迟', cfg['ping']),
        ]
        rows = []
        lines = []
        for k, label, enabled in items:
            if not enabled:
                continue
            raw = self.readings.get(k, label + '    —')
            lines.append(raw)
            if '    ' in raw:
                left, right = raw.split('    ', 1)
                rows.append((left, right))
            else:
                rows.append((raw, ''))
        return rows, '\n'.join(lines)

    def render(self):
        cfg = self.cfg
        rows, text = self._build_rows()
        self.text = text

        if self._layered:
            fg_rgb = _parse_rgb(self.win, cfg['fg'], (241, 245, 249))
            bg_rgb = _parse_rgb(self.win, cfg['bg'], (36, 48, 64))
            scale = max(1.0, getattr(self.widgets, 'ui_scale', 1.0))
            frame = render_monitor_rgba(rows, cfg['size'], cfg['shape'], fg_rgb, bg_rgb, ui_scale=scale)
            if frame is not None:
                width, height, bgra = frame
                size_changed = (width, height) != self._size
                self._size = (width, height)
                self._layered_frame = frame
                if size_changed:
                    self.canvas.configure(width=width, height=height)
                if self.visible:
                    if size_changed:
                        self.win.geometry(f'{width}x{height}+{self._pos[0]}+{self._pos[1]}')
                    self._commit_layered(x=self._pos[0], y=self._pos[1])
                return

        # 无 Pillow 回退：使用 Tk Canvas 绘制，并在透明模式下添加柔和对比微影避免纯白底难以辨认
        self.font.configure(size=cfg['size'])
        lines = self.text.splitlines() or ['—']
        width = max(self.font.measure(t) for t in lines) + 32
        height = len(lines) * self.font.metrics('linespace') + 24
        self._size = (width, height)
        cv = self.canvas
        cv.configure(width=width, height=height)
        cv.delete('all')
        if cfg['shape'] == '圆角':
            r = min(10, width // 3, height // 3)
            cv.create_polygon(
                r, 0, width - r, 0, width, 0, width, r, width, height - r, width, height,
                width - r, height, r, height, 0, height, 0, height - r, 0, r, 0, 0,
                smooth=True, fill=cfg['bg'],
            )
        elif cfg['shape'] == '方形':
            cv.create_rectangle(0, 0, width, height, fill=cfg['bg'], outline='')
        elif cfg['shape'] == '透明':
            fg_rgb = _parse_rgb(self.win, cfg['fg'], (241, 245, 249))
            lum = 0.299 * fg_rgb[0] + 0.587 * fg_rgb[1] + 0.114 * fg_rgb[2]
            shadow = '#0b0f19' if lum >= 120 else '#ffffff'
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
                cv.create_text(16 + dx, 12 + dy, anchor='nw', font=self.font, fill=shadow, text=self.text or '—')
        cv.create_text(16, 12, anchor='nw', font=self.font, fill=cfg['fg'], text=self.text or '—')

    def place(self, x, y):
        w_px, h_px = self._window_size()
        # 用窗口中心点查询显示器边界，避免多屏向右拖动时被主屏右边缘卡住
        l, t, r, b = self.bounds(x + w_px // 2, y + h_px // 2, work=True)
        x = max(l, min(int(x), r - w_px))
        y = max(t, min(int(y), b - h_px))
        self._pos = [x, y]
        self.win.geometry(f'{w_px}x{h_px}+{x}+{y}')
        if self._layered and self.visible and self._layered_frame:
            self._commit_layered(x=x, y=y)
        return [x, y]

    def press(self, event):
        if self._click_through:
            return
        wx = self._pos[0] if self._layered else self.win.winfo_x()
        wy = self._pos[1] if self._layered else self.win.winfo_y()
        self.drag_offset = (event.x_root - wx, event.y_root - wy)

    def drag(self, event):
        if self.drag_offset and not self._click_through:
            self.cfg['position'] = self.place(event.x_root - self.drag_offset[0], event.y_root - self.drag_offset[1])

    def release(self, _event):
        self.drag_offset = None
        if self.dialog is None:
            self.queue_save()

    def wheel(self, event):
        if self._click_through or not event.delta:
            return
        if event.state & 4:       # Ctrl + 滚轮调字号
            key, step, lo, hi = ('size', 1, 9, 48)
        elif event.state & 1:     # Shift + 滚轮调不透明度（防止裸滚轮误触）
            key, step, lo, hi = ('alpha', 5, 30, 100)
        else:
            return
        self.cfg[key] = max(lo, min(hi, self.cfg[key] + (step if event.delta > 0 else -step)))
        if key in self.controls:
            self.controls[key].set(self.cfg[key])
        self.apply()
        if self.dialog is None:
            self.queue_save()
        return 'break'

    def apply(self):
        if not self._layered:
            self.win.attributes('-alpha', self.cfg['alpha'] / 100)
        self.render()

    def queue_save(self):
        if self.save_timer is not None:
            self.win.after_cancel(self.save_timer)
        self.save_timer = self.win.after(300, self.flush_save)

    def cancel_save(self):
        if self.save_timer is not None:
            self.win.after_cancel(self.save_timer)
            self.save_timer = None

    def flush_save(self):
        self.save_timer = None
        self.widgets.save()

    def sample(self, now):
        self.readings = self.sampler.read(self.cfg, now)
        self.last_sample = now

    def settings(self, _event=None):
        if _event is not None and self._click_through:
            return
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        d = self.dialog = tk.Toplevel(self.widgets.root)
        d.title('系统监控外观')
        box = ttk.Frame(d, padding=14)
        box.pack()
        committed = self.widgets.cfg['monitor']
        self.cfg = committed.copy()
        variables = self.controls = {}

        def finish(accept):
            if accept:
                old_edge = committed.get('edge')
                for key, var in variables.items():
                    self.cfg[key] = var.get()
                self.cfg['ping_host'] = self.cfg.get('ping_host', '').strip() or DEFAULT_PING_HOST
                if not any(self.cfg[key] for key in ('cpu', 'memory', 'temp', 'net', 'ping')):
                    self.cfg['cpu'] = True
                if self.cfg.get('edge') != old_edge:
                    # 切换呼出边缘后清除旧的固定坐标，避免下次仍弹在原来的另一侧
                    self.cfg.pop('position', None)
                    committed.pop('position', None)
                committed.update(self.cfg)
            self.cfg = committed
            self.dialog = None
            self.controls = {}
            d.destroy()
            self.apply()
            if accept and self.visible and 'position' not in self.cfg:
                wx, wy = self._pos if self._layered else (self.win.winfo_x(), self.win.winfo_y())
                l, t, r, b = self.bounds(wx, wy, work=True)
                w_px, _h_px = self._window_size()
                nx = l + 5 if self.cfg['edge'] == '左' else r - w_px - 5
                self.place(nx, wy)
            self._notify_pin_ui()
            if accept:
                if self.save_timer is not None:
                    self.win.after_cancel(self.save_timer)
                self.flush_save()

        # 与其余设置窗口一致：右上角关闭自动保留当前实时调整后的外观
        d.protocol('WM_DELETE_WINDOW', lambda: finish(True))
        d.bind('<Escape>', lambda _e: finish(True))

        def change(*_args):
            if self.dialog is not d:
                return
            for k, var in variables.items():
                self.cfg[k] = var.get()
            if not any(self.cfg[k] for k in ('cpu', 'memory', 'temp', 'net', 'ping')):
                variables['cpu'].set(True)
                self.cfg['cpu'] = True
            self.apply()
            self._notify_pin_ui()

        for key, title, choices in [('edge', '呼出边缘', ['左', '右']),
                                    ('shape', '背景样式', ['圆角', '方形', '透明'])]:
            row = ttk.Frame(box)
            row.pack(fill='x')
            ttk.Label(row, text=title, width=12).pack(side='left')
            variables[key] = tk.StringVar(value=self.cfg[key])
            cb = ttk.Combobox(row, textvariable=variables[key], values=choices, state='readonly', width=9)
            cb.pack()
            cb.bind('<<ComboboxSelected>>', change)

        for key, title, lo, hi in [('size', '字号', 9, 48), ('alpha', '不透明度', 30, 100)]:
            variables[key] = tk.IntVar(value=self.cfg[key])
            tk.Scale(box, label=title, from_=lo, to=hi, orient='horizontal',
                     variable=variables[key], command=change).pack(fill='x')

        def choose(key):
            color = colorchooser.askcolor(self.cfg[key], parent=d)[1]
            if color:
                variables[key].set(color)
                change()

        for key, title in [('fg', '文字颜色'), ('bg', '卡片颜色')]:
            variables[key] = tk.StringVar(master=d, value=self.cfg[key])
            ttk.Button(box, text=title, command=lambda k=key: choose(k)).pack(fill='x')

        for key, title in [('cpu', 'CPU'), ('memory', '内存'), ('temp', '显卡温度'),
                           ('net', '上传与下载'), ('ping', '网络延迟 (Ping)'),
                           ('pinned', '固定显示'), ('over_fullscreen', '全屏 / 游戏时保持置顶')]:
            variables[key] = tk.BooleanVar(value=self.cfg[key])
            ttk.Checkbutton(box, text=title, variable=variables[key], command=change).pack(anchor='w')

        ping_row = ttk.Frame(box)
        ping_row.pack(fill='x', pady=(4, 0))
        ttk.Label(ping_row, text='延迟目标', width=10).pack(side='left')
        variables['ping_host'] = tk.StringVar(value=self.cfg['ping_host'])
        ttk.Entry(ping_row, textvariable=variables['ping_host'], width=14).pack(side='left', fill='x', expand=True)

        def reset_default():
            for k, val in self.defaults.items():
                if k in variables:
                    variables[k].set(val)
            change()

        ttk.Label(box, text='提示：Ctrl+滚轮调大小 · Shift+滚轮调不透明度').pack(pady=(5, 2))
        ttk.Label(box, text='与启动栏共用右边缘时，监控占上半段。').pack(pady=(0, 4))
        actions = ttk.Frame(box)
        actions.pack(fill='x', pady=6)
        ttk.Button(actions, text='恢复默认', command=reset_default).pack(side='left')
        ttk.Button(actions, text='完成（已自动保存）', command=lambda: finish(True)).pack(side='right')

    def raise_(self):
        """将监控窗口保持在所有普通/无边框全屏窗口最顶层，且不抢焦点。"""
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _needs_raise(self, foreground):
        """判断是否被前景游戏窗口压到下方或丢失置顶样式（平时零 DWM 触碰，被压住立刻温和抬回）。"""
        if not self.hwnd:
            return False
        if foreground != self._last_foreground:
            return True
        if not (GetWindowLongPtr(self.hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST):
            return True
        if foreground and foreground != self.hwnd:
            cur = user32.GetWindow(self.hwnd, GW_HWNDPREV)
            for _ in range(12):
                if not cur:
                    break
                if cur == foreground:
                    return True
                cur = user32.GetWindow(cur, GW_HWNDPREV)
        return False

    def show(self, x, y, now):
        self.visible = True
        self._alpha_scale = 0.0
        self.sample(now)
        self.last_sample = self.last_inside = now
        self.render()
        l, t, r, b = self.bounds(x, y, work=True)
        w_px, _h_px = self._window_size()
        pos = self.cfg.get('position', (l + 5 if self.cfg['edge'] == '左' else r - w_px - 5, y))
        px, py = self.place(*pos)
        if self._layered:
            self._commit_layered(opacity=0, x=px, y=py)
            self.win.deiconify()
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.raise_()
        else:
            self.win.attributes('-alpha', 0)
            self.win.deiconify()
            self.raise_()

        def brighten(step):
            if not self.visible or not self.win.winfo_exists():
                self.fade = None
                return
            self._alpha_scale = step / 5.0
            try:
                if self._layered:
                    self._commit_layered(x=self._pos[0], y=self._pos[1])
                else:
                    self.win.attributes('-alpha', self.cfg['alpha'] / 100 * self._alpha_scale)
            except tk.TclError:
                self.fade = None
                return
            if step < 5:
                self.fade = self.win.after(20, brighten, step + 1)
            else:
                self.fade = None
                self._alpha_scale = 1.0
                self.raise_()

        if self.fade:
            self.win.after_cancel(self.fade)
        brighten(1)

    def tick(self, now, x, y, monitor, blocked, bar_enabled, suppress_edge=False, foreground=None, guard=False):
        l, t, r, b = monitor
        hide_blocked = blocked and not self.cfg['over_fullscreen']
        scale = max(1.0, getattr(self.widgets, 'ui_scale', 1.0))
        enter_px = max(6, round(6 * scale))
        hold_px = max(18, round(18 * scale))
        max_slide_y = round(48 * scale)

        # 双阈值防回弹：6px 进边缘即触发计时，18px 内微小手抖回弹不断触；按住鼠标键或全屏时不误弹
        dist = (x - l) if self.cfg['edge'] == '左' else (r - 1 - x)
        half_ok = (self.cfg['edge'] == '左') or (not bar_enabled or y < (t + b) // 2)
        threshold = hold_px if self.edge_start is not None else enter_px
        edge = (not suppress_edge) and (not blocked) and half_ok and (dist <= threshold)

        if hide_blocked or not edge:
            self.edge_start = None
        elif self.edge_start is None or self.edge_monitor != monitor:
            self.edge_start = now
            self.edge_y = y
            if not self.visible:
                self.sample(now)
            self.edge_monitor = monitor
        elif abs(y - self.edge_y) > max_slide_y:
            # 沿边缘大幅度上下拉滚动条时重置计时，稳停再呼出
            self.edge_start = now
            self.edge_y = y

        if not self.visible and not hide_blocked and (
            self.cfg['pinned'] or (self.edge_start is not None and now - self.edge_start >= 0.16)
        ):
            self.show(x, y, now)
            self._last_foreground = foreground

        if self.visible:
            editing = bool(self.dialog and self.dialog.winfo_exists())
            # 外部软件或半屏/全屏游戏在前台时开启鼠标穿透：既能贴边呼出看一眼温度，游戏右键走位也 100% 穿透不弹设置窗
            want_passthrough = bool((guard or blocked) and not editing)
            if want_passthrough != self._click_through:
                self._click_through = want_passthrough
                set_click_through(self.hwnd, want_passthrough)

            wx, wy = (self._pos[0], self._pos[1]) if self._layered else (self.win.winfo_x(), self.win.winfo_y())
            w_px, h_px = self._window_size()
            in_rect = wx <= x < wx + w_px and wy <= y < wy + h_px
            inside = in_rect and (not blocked)
            if inside or edge or editing or self.drag_offset:
                self.last_inside = now
            if not editing and (hide_blocked or (not self.cfg['pinned'] and now - self.last_inside >= 1.5)):
                self._hide()
            elif now - self.last_sample >= 1:
                self.sample(now)
                self.render()
            if self.visible and self.cfg['over_fullscreen'] and self._needs_raise(foreground):
                self._last_foreground = foreground
                self.raise_()
        return edge and self.cfg['edge'] == '右'
