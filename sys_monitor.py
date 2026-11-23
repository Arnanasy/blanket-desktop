"""按需呼出的 Windows 系统性能监控挂件（无常驻轮询线程，按启用项采集）。"""
import ctypes as c
from ctypes import wintypes as w
import tkinter as tk
from tkinter import ttk, colorchooser, font

DWORD = w.DWORD


class Memory(c.Structure):
    _fields_ = [('length', DWORD), ('load', DWORD), ('sizes', c.c_uint64 * 7)]


class Interface(c.Structure):
    # 对应 Windows SDK netioapi.h 中的 MIB_IF_ROW2；flags 的 bit 0 为 HardwareInterface
    _fields_ = [
        ('luid', c.c_uint64), ('index', DWORD), ('guid', c.c_byte * 16),
        ('alias', w.WCHAR * 257), ('description', w.WCHAR * 257),
        ('address_len', DWORD), ('address', c.c_byte * 32), ('permanent', c.c_byte * 32),
        ('mtu', DWORD), ('kind', DWORD), ('tunnel', DWORD), ('media', DWORD),
        ('physical_medium', DWORD), ('access', DWORD), ('direction', DWORD),
        ('flags', c.c_ubyte), ('oper', DWORD), ('admin', DWORD), ('connected', DWORD),
        ('network_guid', c.c_byte * 16), ('connection', DWORD),
        ('tx_speed', c.c_uint64), ('rx_speed', c.c_uint64),
        ('received', c.c_uint64), ('in_stats', c.c_uint64 * 8),
        ('sent', c.c_uint64), ('out_stats', c.c_uint64 * 8),
    ]


class InterfaceTable(c.Structure):
    _fields_ = [('count', DWORD), ('first', Interface)]


# 使用私有 WinDLL 实例，不污染进程级 ctypes.windll 单例
_kernel = c.WinDLL('kernel32')
_kernel.GetSystemTimes.argtypes = [c.POINTER(w.FILETIME)] * 3
_kernel.GetSystemTimes.restype = w.BOOL
_kernel.GlobalMemoryStatusEx.argtypes = [c.POINTER(Memory)]
_kernel.GlobalMemoryStatusEx.restype = w.BOOL

_ip = c.WinDLL('iphlpapi')
_ip.GetIfTable2.argtypes = [c.POINTER(c.POINTER(InterfaceTable))]
_ip.GetIfTable2.restype = w.DWORD
_ip.FreeMibTable.argtypes = [c.c_void_p]
_ip.FreeMibTable.restype = None


def network():
    table = c.POINTER(InterfaceTable)()
    if _ip.GetIfTable2(c.byref(table)):
        return None
    try:
        rows = (Interface * table.contents.count).from_address(c.addressof(table.contents.first))
        return {
            row.luid: (row.received, row.sent)
            for row in rows
            if (row.flags & 1) and row.oper == 1 and row.kind in (6, 71, 243, 244)
        }
    finally:
        _ip.FreeMibTable(table)


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

    def read(self, cfg, now):
        """按配置仅采集启用的监控项，返回键值映射。"""
        readings = {}
        if cfg.get('cpu'):
            times = [w.FILETIME() for _ in range(3)]
            percent = '—'
            if _kernel.GetSystemTimes(*(c.byref(t) for t in times)):
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
            value = f'{mem.load}%' if _kernel.GlobalMemoryStatusEx(c.byref(mem)) else '—'
            readings['memory'] = '内存    ' + value

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

        return readings


class SystemMonitor:
    defaults = dict(edge='左', size=15, alpha=90, fg='#F1F5F9', bg='#243040',
                    shape='圆角', cpu=True, memory=True, net=True, pinned=False)
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
        self.font = font.Font(family='Segoe UI', size=self.cfg['size'])
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Button-3>', self.settings)

        self.visible = False
        self.dialog = None
        self.drag_offset = None
        self.edge_start = None
        self.edge_monitor = None
        self.last_inside = 0.0
        self.last_sample = 0.0
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
            ('down', '↓', cfg['net']),
            ('up', '↑', cfg['net']),
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
            r = 10
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
        self.drag_offset = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

    def drag(self, event):
        if self.drag_offset:
            self.cfg['position'] = self.place(event.x_root - self.drag_offset[0], event.y_root - self.drag_offset[1])

    def release(self, _event):
        self.drag_offset = None
        if self.dialog is None:
            self.queue_save()

    def wheel(self, event):
        if not event.delta:
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

    def flush_save(self):
        self.save_timer = None
        self.widgets.save()

    def sample(self, now):
        self.readings = self.sampler.read(self.cfg, now)
        self.last_sample = now

    def settings(self, _event=None):
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
                if not any(self.cfg[key] for key in ('cpu', 'memory', 'net')):
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
            if not any(self.cfg[k] for k in ('cpu', 'memory', 'net')):
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

        for key, title in [('cpu', 'CPU'), ('memory', '内存'), ('net', '上传与下载'), ('pinned', '固定显示')]:
            variables[key] = tk.BooleanVar(value=self.cfg[key])
            ttk.Checkbutton(box, text=title, variable=variables[key], command=change).pack(anchor='w')

        ttk.Label(box, text='与启动栏共用右边缘时，监控占上半段。').pack(pady=5)
        actions = ttk.Frame(box)
        actions.pack(fill='x', pady=6)
        ttk.Button(actions, text='取消', command=lambda: finish(False)).pack(side='right')
        ttk.Button(actions, text='确定', command=lambda: finish(True)).pack(side='right', padx=5)

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

        def brighten(step):
            if not self.visible or not self.win.winfo_exists():
                self.fade = None
                return
            try:
                self.win.attributes('-alpha', self.cfg['alpha'] / 100 * step / 5)
            except tk.TclError:
                self.fade = None
                return
            self.fade = self.win.after(20, brighten, step + 1) if step < 5 else None

        if self.fade:
            self.win.after_cancel(self.fade)
        brighten(1)

    def tick(self, now, x, y, monitor, blocked, bar_enabled, suppress_edge=False):
        l, t, r, b = monitor
        edge = not suppress_edge and (
            x < l + 3 if self.cfg['edge'] == '左' else x >= r - 3 and (not bar_enabled or y < (t + b) // 2)
        )
        if blocked or not edge:
            self.edge_start = None
        elif self.edge_start is None or self.edge_monitor != monitor:
            self.edge_start = now
            if not self.visible:
                self.sample(now)
            self.edge_monitor = monitor

        if not self.visible and not blocked and (
            self.cfg['pinned'] or (self.edge_start is not None and now - self.edge_start >= 0.25)
        ):
            self.show(x, y, now)

        if self.visible:
            wx, wy = self.win.winfo_x(), self.win.winfo_y()
            inside = wx <= x < wx + self.win.winfo_width() and wy <= y < wy + self.win.winfo_height()
            editing = bool(self.dialog and self.dialog.winfo_exists())
            if inside or edge or editing or self.drag_offset:
                self.last_inside = now
            if not editing and (blocked or (not self.cfg['pinned'] and now - self.last_inside >= 1.5)):
                if self.fade:
                    self.win.after_cancel(self.fade)
                    self.fade = None
                self.win.withdraw()
                self.visible = False
            elif now - self.last_sample >= 1:
                self.sample(now)
                self.render()
        return edge and self.cfg['edge'] == '右'
