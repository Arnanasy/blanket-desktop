"""右侧屏幕边缘呼出的快捷启动栏：最近启动 + 可折叠分组。"""
import os
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from display_geometry import bounds

BAR_BG = '#18202D'
ITEM_BG = '#233044'
ACTIVE_BG = '#28364A'
OPEN_DELAY = 0.35          # 鼠标贴右边缘多久后弹出
CLOSE_DELAY = 0.9          # 鼠标离开多久后收起
MAX_RECENT = 8
DEFAULT_GROUPS = ('常用工具', '临时文件')


class LauncherBar:
    def __init__(self, widgets, cfg):
        self.widgets = widgets
        self.app = widgets.app
        groups = cfg.get('groups', {})
        if not isinstance(groups, dict):
            groups = {}
        self.groups = {k: [p for p in v if isinstance(p, str)]
                       for k, v in groups.items() if isinstance(k, str) and isinstance(v, list)}
        for name in DEFAULT_GROUPS:
            self.groups.setdefault(name, [])
        recent = cfg.get('recent', [])
        self.recent = [v for v in recent if isinstance(v, str)][:MAX_RECENT] if isinstance(recent, list) else []
        self.enabled = tk.BooleanVar(value=bool(cfg.get('bar', True)))
        self.expanded = set()
        self.busy = False            # 文件对话框打开期间为 True：不收起、不响应桌面点击
        self.last_inside = time.monotonic()
        self.edge_since = None
        self.edge_monitor = None
        self.theme_item = None

        self.win = tk.Toplevel(widgets.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=BAR_BG)
        self.win.attributes('-topmost', True)
        self.render()

    # ------------------------------------------------------------ 界面
    def _button(self, text, command):
        b = tk.Button(self.win, text=text, command=command, bg=BAR_BG, fg=self.widgets.accent,
                      activebackground=ACTIVE_BG, activeforeground='white', relief='flat',
                      anchor='w', padx=16, pady=7)
        b.pack(fill='x')
        return b

    def render(self):
        for child in self.win.winfo_children():
            child.destroy()
        self._button('Blanket · 快捷分组', self.app.open_settings)
        if self.recent:
            tk.Label(self.win, text='最近启动', bg=BAR_BG, fg='white').pack(anchor='w', padx=14)
            for path in self.recent:
                self._button(Path(path).name, lambda p=path: self.open(p))
        for name, paths in self.groups.items():
            hdr = self._button(('▾ ' if name in self.expanded else '▸ ') + name, lambda n=name: self.fold(n))
            if name not in DEFAULT_GROUPS:
                hdr.bind('<Button-3>', lambda e, n=name: self.delete_group(n))
            if name in self.expanded:
                for path in paths:
                    icon = '📁 ' if os.path.isdir(path) else '📄 '
                    label_txt = Path(path).name if os.path.isdir(path) else Path(path).stem
                    if len(label_txt) > 22:
                        label_txt = label_txt[:21] + '…'
                    b = tk.Button(self.win, text='  ' + icon + label_txt, command=lambda p=path: self.open(p),
                                  bg=ITEM_BG, fg='white', relief='flat', anchor='w', padx=15)
                    b.pack(fill='x')
                    b.bind('<Button-3>', lambda e, n=name, p=path: self.remove(n, p))
                self._button('  ＋ 添加软件 / 文件', lambda n=name: self.add(n, False))
                self._button('  ＋ 添加文件夹', lambda n=name: self.add(n, True))
        self._button('＋ 新建快捷分组', self.create_group)
        self._button('暂停 / 恢复自动隐藏', self.app.pause_auto)
        self.theme_item = self._button('', self.app.toggle_theme)
        self.set_theme_label(self.widgets.theme_button.dark)
        self._button('桌面组件设置', self.widgets.settings)
        self.win.update_idletasks()

    def set_theme_label(self, dark):
        if self.theme_item is not None:
            self.theme_item.configure(text=('☀' if dark else '☾') + ' 一键切换深浅主题')

    def recolor(self, accent):
        for child in self.win.winfo_children():
            if isinstance(child, tk.Button) and child.cget('bg') == BAR_BG:
                child.configure(fg=accent)

    # ------------------------------------------------------------ 操作
    def _reposition(self, anchor_x, anchor_y):
        """按所在显示器工作区重新计算启动栏尺寸并贴右边缘居中定位。"""
        wl, wt, wr, wb = bounds(anchor_x, anchor_y, work=True)
        width = max(self.win.winfo_reqwidth(), round(260 * self.widgets.ui_scale))
        height = min(self.win.winfo_reqheight(), wb - wt)
        self.win.geometry(f'{width}x{height}+{wr - width}+{wt + max(0, (wb - wt - height) // 2)}')

    def _refresh_if_visible(self):
        if self.win.winfo_viewable():
            self._reposition(self.win.winfo_x() + self.win.winfo_width() // 2,
                             self.win.winfo_y() + self.win.winfo_height() // 2)

    def fold(self, name):
        self.expanded.symmetric_difference_update({name})
        self.render()
        self._refresh_if_visible()

    def add(self, name, folder):
        self.busy = True
        try:
            path = filedialog.askdirectory(parent=self.win) if folder else filedialog.askopenfilename(parent=self.win)
            if path and path not in self.groups[name]:
                self.groups[name].append(path)
                self.widgets.save()
                self.render()
                self._refresh_if_visible()
        finally:
            self.app.discard_mouse()
            self.busy = False
            self.last_inside = time.monotonic()

    def create_group(self):
        self.busy = True
        try:
            raw = simpledialog.askstring('新建分组', '请输入新快捷分组名称：', parent=self.win)
            name = (raw or '').strip()[:16]
            if name and name not in self.groups:
                self.groups[name] = []
                self.expanded.add(name)
                self.widgets.save()
                self.render()
                self._refresh_if_visible()
        finally:
            self.app.discard_mouse()
            self.busy = False
            self.last_inside = time.monotonic()

    def delete_group(self, name):
        if name in DEFAULT_GROUPS or name not in self.groups:
            return
        self.busy = True
        try:
            if messagebox.askyesno('删除分组', f'确定要删除自定义分组「{name}」吗？', parent=self.win):
                self.groups.pop(name, None)
                self.expanded.discard(name)
                self.widgets.save()
                self.render()
                self._refresh_if_visible()
        finally:
            self.app.discard_mouse()
            self.busy = False
            self.last_inside = time.monotonic()

    def remove(self, name, path):
        items = self.groups.get(name, [])
        if path in items:
            items.remove(path)
            self.widgets.save()
            self.render()
            self._refresh_if_visible()

    def _show_error(self, message):
        self.busy = True
        try:
            messagebox.showerror('无法打开', message, parent=self.win)
        finally:
            self.app.discard_mouse()
            self.busy = False
            self.last_inside = time.monotonic()

    def open(self, path):
        if not os.path.exists(path):
            self._show_error(f'路径不存在：{path}')
            return
        try:
            os.startfile(path)
        except OSError as e:
            self._show_error(str(e))
            return
        key = os.path.normcase(path)
        self.recent = ([path] + [p for p in self.recent if os.path.normcase(p) != key])[:MAX_RECENT]
        self.widgets.save()
        self.win.withdraw()
        self.render()

    # ------------------------------------------------------------ 主循环
    def tick(self, now, p, monitor, blocked, monitor_edge, theme_edge, guard=False):
        """返回本轮是否刚弹出。"""
        l, t, r, b = monitor
        enabled = self.enabled.get()
        edge = enabled and p.x >= r - 3 and not blocked and not guard and not monitor_edge and not theme_edge
        if not edge:
            self.edge_since = None
        elif self.edge_since is None or self.edge_monitor != monitor:
            self.edge_since = now
            self.edge_monitor = monitor

        opened = False
        if edge and now - self.edge_since >= OPEN_DELAY:
            self._reposition(p.x, p.y)
            if not self.win.winfo_viewable():
                self.win.deiconify()
                opened = True
            self.last_inside = now

        if self.win.winfo_viewable():
            x, y = self.win.winfo_x(), self.win.winfo_y()
            if not guard and (x <= p.x < x + self.win.winfo_width() and y <= p.y < y + self.win.winfo_height()):
                self.last_inside = now
            if not self.busy and (not enabled or blocked or guard or now - self.last_inside > CLOSE_DELAY):
                self.win.withdraw()
        return opened
