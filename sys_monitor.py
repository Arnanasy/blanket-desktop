"""按需呼出的 Windows 系统性能监控挂件（无常驻轮询线程，按启用项采集）。"""
import ctypes as c
from ctypes import wintypes as w
import queue
import socket
from threading import Thread
import tkinter as tk
from tkinter import ttk, colorchooser, font

from win32 import (user32, kernel32, iphlpapi, ws2_32, toplevel_hwnd, add_exstyle, set_click_through,
                   HWND_TOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE, WS_EX_NOACTIVATE,
                   WS_EX_TOOLWINDOW, INVALID_HANDLE_VALUE, IcmpEchoReply, Memory,
                   Interface, InterfaceTable)

DEFAULT_PING_HOST = '223.5.5.5'
PING_INTERVAL = 2.0          # Ping 采样间隔（秒），在后台线程执行不阻塞界面
PING_TIMEOUT_MS = 1000
NVML_TEMPERATURE_GPU = 0


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
        add_exstyle(self.hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
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
        self.edge_start = None
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

    def render(self):
        cfg = self.cfg
        items = [
            ('cpu', 'CPU', cfg['cpu']),
            ('memory', '内存', cfg['memory']),
            ('temp', '温度', cfg['temp']),
            ('down', '↓', cfg['net']),
            ('up', '↑', cfg['net']),
            ('ping', '延迟', cfg['ping']),
        ]
        self.text = '\n'.join(self.readings.get(k, label + '    —') for k, label, enabled in items if enabled)
        self.font.configure(size=cfg['size'])
        lines = self.text.splitlines() or ['—']
        width = max(self.font.measure(t) for t in lines) + 32
        height = len(lines) * self.font.metrics('linespace') + 24
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
        cv.create_text(16, 12, anchor='nw', font=self.font, fill=cfg['fg'], text=self.text or '—')

    def place(self, x, y):
        w, h = self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight()
        # 用窗口中心点查询显示器边界，避免多屏向右拖动时被主屏右边缘卡住
        l, t, r, b = self.bounds(x + w // 2, y + h // 2, work=True)
        x = max(l, min(x, r - w))
        y = max(t, min(y, b - h))
        self.win.geometry(f'+{x}+{y}')
        return [x, y]

    def press(self, event):
        if self._click_through:
            return
        self.drag_offset = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

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
        key, step, lo, hi = ('size', 1, 9, 48) if event.state & 4 else ('alpha', 5, 30, 100)
        self.cfg[key] = max(lo, min(hi, self.cfg[key] + (step if event.delta > 0 else -step)))
        if key in self.controls:
            self.controls[key].set(self.cfg[key])
        self.apply()
        if self.dialog is None:
            self.queue_save()
        return 'break'

    def apply(self):
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
                l, t, r, b = self.bounds(self.win.winfo_x(), self.win.winfo_y(), work=True)
                nx = l + 5 if self.cfg['edge'] == '左' else r - self.canvas.winfo_reqwidth() - 5
                self.place(nx, self.win.winfo_y())
            if accept:
                if self.save_timer is not None:
                    self.win.after_cancel(self.save_timer)
                self.flush_save()

        d.protocol('WM_DELETE_WINDOW', lambda: finish(False))
        d.bind('<Escape>', lambda _e: finish(False))

        def change(*_args):
            if self.dialog is not d:
                return
            for k, var in variables.items():
                self.cfg[k] = var.get()
            if not any(self.cfg[k] for k in ('cpu', 'memory', 'temp', 'net', 'ping')):
                variables['cpu'].set(True)
                self.cfg['cpu'] = True
            self.apply()

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

        ttk.Label(box, text='与启动栏共用右边缘时，监控占上半段。').pack(pady=5)
        actions = ttk.Frame(box)
        actions.pack(fill='x', pady=6)
        ttk.Button(actions, text='取消', command=lambda: finish(False)).pack(side='right')
        ttk.Button(actions, text='确定', command=lambda: finish(True)).pack(side='right', padx=5)

    def raise_(self):
        """将监控窗口保持在所有普通/无边框全屏窗口最顶层，且不抢焦点。"""
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def show(self, x, y, now):
        self.visible = True
        self.sample(now)
        self.last_sample = self.last_inside = now
        self.render()
        l, t, r, b = self.bounds(x, y, work=True)
        pos = self.cfg.get('position', (l + 5 if self.cfg['edge'] == '左' else r - self.canvas.winfo_reqwidth() - 5, y))
        self.place(*pos)
        self.win.attributes('-alpha', 0)
        self.win.deiconify()
        self.raise_()

        def brighten(step):
            if not self.visible or not self.win.winfo_exists():
                self.fade = None
                return
            try:
                self.win.attributes('-alpha', self.cfg['alpha'] / 100 * step / 5)
            except tk.TclError:
                self.fade = None
                return
            if step < 5:
                self.fade = self.win.after(20, brighten, step + 1)
            else:
                self.fade = None
                self.raise_()

        if self.fade:
            self.win.after_cancel(self.fade)
        brighten(1)

    def tick(self, now, x, y, monitor, blocked, bar_enabled, suppress_edge=False, foreground=None, guard=False):
        l, t, r, b = monitor
        hide_blocked = blocked and not self.cfg['over_fullscreen']
        # 游戏/窗口防误触开启时，不因鼠标甩到边缘而突然弹出监控框（若勾选“固定显示”则保留并转为鼠标穿透）
        edge = not suppress_edge and not guard and not blocked and (
            x < l + 3 if self.cfg['edge'] == '左' else x >= r - 3 and (not bar_enabled or y < (t + b) // 2)
        )
        if hide_blocked or not edge:
            self.edge_start = None
        elif self.edge_start is None or self.edge_monitor != monitor:
            self.edge_start = now
            if not self.visible:
                self.sample(now)
            self.edge_monitor = monitor

        if not self.visible and not hide_blocked and (
            self.cfg['pinned'] or (self.edge_start is not None and now - self.edge_start >= 0.25)
        ):
            self.show(x, y, now)
            self._last_foreground = foreground

        if self.visible:
            editing = bool(self.dialog and self.dialog.winfo_exists())
            want_passthrough = bool((guard or blocked) and not editing)
            if want_passthrough != self._click_through:
                self._click_through = want_passthrough
                set_click_through(self.hwnd, want_passthrough)

            wx, wy = self.win.winfo_x(), self.win.winfo_y()
            inside = (not want_passthrough) and (wx <= x < wx + self.win.winfo_width() and wy <= y < wy + self.win.winfo_height())
            if inside or edge or editing or self.drag_offset:
                self.last_inside = now
            if not editing and (hide_blocked or (not self.cfg['pinned'] and (guard or now - self.last_inside >= 1.5))):
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
            elif now - self.last_sample >= 1:
                self.sample(now)
                self.render()
            if self.visible and self.cfg['over_fullscreen'] and foreground != self._last_foreground:
                # 仅在前台窗口切换时重新置顶，避免每 150ms 无谓触碰 DWM 合成树
                self._last_foreground = foreground
                self.raise_()
        return edge and self.cfg['edge'] == '右'
