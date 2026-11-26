"""右侧屏幕边缘呼出的快捷中枢抽屉：
1. 桌面快捷开关（☕ 屏幕常亮防休眠、🌙 一键息屏、☀/☾ 深浅主题、🧰 联动 DeskKit 本地工具箱）
2. 🍅 极简倒计时 / 番茄钟（直接融入桌面大时钟显示）
3. ⚡ 最新文件速取（自动列出「下载 / 桌面」最新产生的 5 个文件，左键打开、右键定位）
4. 📝 随手便签 / 待办（左键勾选划掉、右键删除、回车秒加）
5. 可折叠的自定义分组与主设置入口
"""
import os
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from display_geometry import bounds
from preferences import APP_DIR
from quick_hub import (MAX_NOTES, MAX_NOTE_LEN, KeepAwake, launch_deskkit,
                       reveal_in_explorer, sanitize_notes, scan_recent_files,
                       turn_off_monitors)
from win32 import (GetWindowLongPtr, SetWindowLongPtr, add_exstyle, toplevel_hwnd,
                   GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW)

BAR_BG = '#0F172A'
CARD_BG = '#1E293B'
ITEM_BG = '#263349'
ACTIVE_BG = '#334155'
GREEN_ACTIVE = '#15803D'
MUTED_FG = '#94A3B8'
TEXT_FG = '#F1F5F9'

OPEN_DELAY = 0.18          # 鼠标贴右边缘多久后弹出（配合双阈值防回弹，轻靠即出）
CLOSE_DELAY = 0.95         # 鼠标离开多久后收起
RECENT_SCAN_TTL = 2.5      # 最新文件扫描最小间隔（秒）
MAX_RECENT = 8
DEFAULT_GROUPS = ('常用工具', '临时文件')


def _clip(text, limit=22):
    s = str(text or '')
    return s if len(s) <= limit else s[:limit - 1] + '…'


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
        self.notes = sanitize_notes(cfg.get('notes', []))
        self.deskkit_path = str(cfg.get('deskkit_path', '') or '').strip()

        self.enabled = tk.BooleanVar(value=bool(cfg.get('bar', True)))
        self.expanded = set()
        self.groups_section_open = False
        self.busy = False            # 模态对话框打开期间为 True：不收起、不响应桌面点击
        self._note_focused = False   # 正在便签输入框打字时保持抽屉展开，绝不吞字
        self._note_draft = tk.StringVar(master=widgets.root, value='')

        self.awake = KeepAwake()
        self.fresh_files = []
        self._last_file_scan = 0.0

        self.last_inside = time.monotonic()
        self.edge_since = None
        self.edge_y = 0
        self.edge_monitor = None
        self.theme_item = None
        self.awake_btn = None
        self.countdown_label = None
        self.files_box = None

        self.win = tk.Toplevel(widgets.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=BAR_BG, highlightbackground='#334155', highlightthickness=1)
        self.win.attributes('-topmost', True)
        self.win.bind('<Escape>', lambda _e: self._dismiss())
        self.hwnd = toplevel_hwnd(self.win)
        # 默认不抢前台窗口焦点（点开关不打断游戏/软件）；点击便签输入框时才临时激活输入焦点
        add_exstyle(self.hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.render()

    def _set_noactivate(self, noactivate):
        if not self.hwnd:
            return
        cur = GetWindowLongPtr(self.hwnd, GWL_EXSTYLE)
        target = (cur | WS_EX_NOACTIVATE) if noactivate else (cur & ~WS_EX_NOACTIVATE)
        if target != cur:
            SetWindowLongPtr(self.hwnd, GWL_EXSTYLE, target)

    def close(self):
        """程序退出时释放屏幕常亮状态。"""
        self.awake.release()

    def _dismiss(self):
        self._note_focused = False
        self._set_noactivate(True)
        self.win.withdraw()

    # ------------------------------------------------------------ 视觉小组件构建
    def _card(self, title=None):
        frame = tk.Frame(self.win, bg=CARD_BG, padx=10, pady=8)
        frame.pack(fill='x', padx=8, pady=(6, 0))
        if title:
            tk.Label(frame, text=title, bg=CARD_BG, fg=MUTED_FG,
                     font=('Microsoft YaHei UI', 8, 'bold'), anchor='w').pack(fill='x', pady=(0, 4))
        return frame

    def _pill_btn(self, parent, text, command, bg=ITEM_BG, fg=TEXT_FG):
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=ACTIVE_BG, activeforeground='white',
             font=('Microsoft YaHei UI', 9), relief='flat', borderwidth=0,
            cursor='hand2', pady=5, padx=6,
        )

    def render(self):
        for child in self.win.winfo_children():
            child.destroy()

        # 1. 顶部快捷控制开关（2x2 网格）
        top_card = self._card('快捷开关')
        grid = tk.Frame(top_card, bg=CARD_BG)
        grid.pack(fill='x')
        grid.columnconfigure((0, 1), weight=1, uniform='qbtn')

        awake_on = self.awake.active
        self.awake_btn = self._pill_btn(
            grid,
            '☕ 常亮开启中' if awake_on else '☕ 屏幕常亮',
            self._toggle_awake,
            bg=GREEN_ACTIVE if awake_on else ITEM_BG,
        )
        self.awake_btn.grid(row=0, column=0, sticky='ew', padx=(0, 3), pady=(0, 4))

        off_btn = self._pill_btn(grid, '🌙 一键息屏', self._sleep_screen)
        off_btn.grid(row=0, column=1, sticky='ew', padx=(3, 0), pady=(0, 4))

        self.theme_item = self._pill_btn(grid, '', self.app.toggle_theme)
        self.theme_item.grid(row=1, column=0, sticky='ew', padx=(0, 3))
        self.set_theme_label(self.widgets.theme_button.dark)

        dk_btn = self._pill_btn(grid, '🧰 本地工具箱', self._open_deskkit, fg=self.widgets.accent)
        dk_btn.grid(row=1, column=1, sticky='ew', padx=(3, 0))
        dk_btn.bind('<Button-3>', lambda _e: self._pick_deskkit_path())

        # 2. 极简倒计时 / 番茄钟（联动桌面大时钟）
        cd_card = self._card('🍅 桌面时钟倒计时')
        if self.widgets.timer_end > time.monotonic():
            row = tk.Frame(cd_card, bg=CARD_BG)
            row.pack(fill='x')
            txt = self.widgets.current_countdown_text() or '⏳ 倒计时进行中'
            self.countdown_label = tk.Label(
                row, text=txt, bg=CARD_BG, fg=self.widgets.accent,
                font=('Microsoft YaHei UI', 9, 'bold'), anchor='w',
            )
            self.countdown_label.pack(side='left', fill='x', expand=True)
            self._pill_btn(row, '取消', self._cancel_timer, bg='#7F1D1D').pack(side='right')
        else:
            self.countdown_label = None
            t_row = tk.Frame(cd_card, bg=CARD_BG)
            t_row.pack(fill='x')
            t_row.columnconfigure((0, 1, 2, 3), weight=1, uniform='tbtn')
            for idx, (lbl, mins, tag) in enumerate([
                ('15分', 15, '倒计时'), ('25分', 25, '专注'), ('45分', 45, '专注'), ('自定义', 0, '倒计时'),
            ]):
                cmd = self._custom_timer if mins == 0 else (lambda m=mins, tg=tag: self._start_timer(m, tg))
                b = self._pill_btn(t_row, lbl, cmd)
                b.grid(row=0, column=idx, sticky='ew', padx=2)

        # 3. 最新文件速取（自动扫描下载与桌面 Top 5，独立局部容器刷新不打断便签输入）
        files_card = self._card('⚡ 最新文件（左键打开 · 右键定位）')
        self.files_box = tk.Frame(files_card, bg=CARD_BG)
        self.files_box.pack(fill='x')
        self._populate_files_box()

        # 4. 随手便签 / 待办
        note_card = self._card('📝 随手便签（左键划掉 · 右键删除）')
        for idx, note in enumerate(self.notes):
            done = note.get('done', False)
            prefix = '☑ ' if done else '☐ '
            fg_col = MUTED_FG if done else TEXT_FG
            font_spec = ('Microsoft YaHei UI', 9, 'overstrike') if done else ('Microsoft YaHei UI', 9)
            nb = tk.Button(
                note_card, text=prefix + _clip(note['text'], 22),
                command=lambda i=idx: self._toggle_note(i),
                bg=CARD_BG, fg=fg_col, activebackground=ACTIVE_BG, activeforeground='white',
                font=font_spec, relief='flat', borderwidth=0, anchor='w', padx=4, pady=2, cursor='hand2',
            )
            nb.pack(fill='x')
            nb.bind('<Button-3>', lambda _e, i=idx: self._delete_note(i))

        if len(self.notes) < MAX_NOTES:
            in_row = tk.Frame(note_card, bg=CARD_BG)
            in_row.pack(fill='x', pady=(4, 0))
            entry = tk.Entry(
                in_row, textvariable=self._note_draft, bg=BAR_BG, fg=TEXT_FG,
                insertbackground=TEXT_FG, relief='flat', font=('Microsoft YaHei UI', 9),
            )
            entry.pack(side='left', fill='x', expand=True, ipady=4, padx=(0, 4))
            entry.bind('<Button-1>', lambda _e, w=entry: self._focus_note_entry(w))
            entry.bind('<FocusIn>', lambda _e: setattr(self, '_note_focused', True))
            entry.bind('<FocusOut>', lambda _e: setattr(self, '_note_focused', False))
            entry.bind('<Return>', lambda _e: self._add_note())
            self._pill_btn(in_row, '＋添加', self._add_note, bg='#2563EB').pack(side='right')

        # 5. 可折叠的自定义分组（按需展开，平时仅占 1 行保持清爽）
        grp_card = self._card()
        toggle_txt = ('▾ ' if self.groups_section_open else '▸ ') + '自定义常用分组'
        tk.Button(
            grp_card, text=toggle_txt, command=self._toggle_groups_section,
            bg=CARD_BG, fg=self.widgets.accent, activebackground=ACTIVE_BG, activeforeground='white',
            font=('Microsoft YaHei UI', 9, 'bold'), relief='flat', anchor='w', padx=2, pady=2, cursor='hand2',
        ).pack(fill='x')

        if self.groups_section_open:
            for name, paths in self.groups.items():
                hdr = tk.Button(
                    grp_card, text=('  ▾ ' if name in self.expanded else '  ▸ ') + name,
                    command=lambda n=name: self.fold(n),
                    bg=CARD_BG, fg=TEXT_FG, activebackground=ACTIVE_BG, activeforeground='white',
                    font=('Microsoft YaHei UI', 9), relief='flat', anchor='w', pady=2, cursor='hand2',
                )
                hdr.pack(fill='x')
                if name not in DEFAULT_GROUPS:
                    hdr.bind('<Button-3>', lambda _e, n=name: self.delete_group(n))
                if name in self.expanded:
                    for path in paths:
                        icon = '📁 ' if os.path.isdir(path) else '📄 '
                        label_txt = Path(path).name if os.path.isdir(path) else Path(path).stem
                        b = tk.Button(
                            grp_card, text='    ' + icon + _clip(label_txt, 18),
                            command=lambda p=path: self.open(p),
                            bg=ITEM_BG, fg='white', activebackground=ACTIVE_BG,
                            font=('Microsoft YaHei UI', 8), relief='flat', anchor='w', padx=8, pady=3, cursor='hand2',
                        )
                        b.pack(fill='x', pady=1)
                        b.bind('<Button-3>', lambda _e, n=name, p=path: self.remove(n, p))
                    act_r = tk.Frame(grp_card, bg=CARD_BG)
                    act_r.pack(fill='x', pady=2)
                    self._pill_btn(act_r, '＋软件/文件', lambda n=name: self.add(n, False)).pack(side='left', expand=True, fill='x', padx=(0, 2))
                    self._pill_btn(act_r, '＋文件夹', lambda n=name: self.add(n, True)).pack(side='left', expand=True, fill='x', padx=(2, 0))
            self._pill_btn(grp_card, '＋ 新建分组', self.create_group).pack(fill='x', pady=(4, 0))

        # 6. 底部紧凑控制入口
        foot = tk.Frame(self.win, bg=BAR_BG, padx=8, pady=8)
        foot.pack(fill='x')
        foot.columnconfigure((0, 1), weight=1, uniform='fbtn')
        self._pill_btn(foot, '⚙️ 主面板', self.app.open_settings).grid(row=0, column=0, sticky='ew', padx=(0, 3))
        self._pill_btn(foot, '🎨 组件设置', self.widgets.settings).grid(row=0, column=1, sticky='ew', padx=(3, 0))

        self.win.update_idletasks()

    # ------------------------------------------------------------ 快捷开关与倒计时动作
    def _toggle_awake(self):
        self.awake.toggle()
        self.render()
        self._refresh_if_visible()

    def _sleep_screen(self):
        self.win.withdraw()
        # 延迟 350ms 再广播息屏，避免鼠标松开时的微小位移立刻又把屏幕唤醒
        self.widgets.root.after(350, turn_off_monitors)

    def _open_deskkit(self):
        ok, resolved = launch_deskkit(self.deskkit_path, APP_DIR)
        if ok:
            if resolved and resolved != self.deskkit_path:
                self.deskkit_path = resolved
                self.widgets.save()
            self.win.withdraw()
            return
        self._pick_deskkit_path(launch_after=True)

    def _pick_deskkit_path(self, launch_after=False):
        self.busy = True
        try:
            chosen = filedialog.askopenfilename(
                parent=self.win,
                title='选择本地工具箱启动文件（DeskKit.exe 或 DeskKit.cmd）',
                filetypes=[('DeskKit 启动项', 'DeskKit.exe DeskKit.cmd launch.pyw *.exe *.cmd'), ('所有文件', '*.*')],
            )
            if chosen:
                self.deskkit_path = chosen
                self.widgets.save()
                if launch_after:
                    launch_deskkit(self.deskkit_path, APP_DIR)
                    self.win.withdraw()
        finally:
            self.app.discard_mouse()
            self.busy = False
            self.last_inside = time.monotonic()

    def _start_timer(self, minutes, label='专注'):
        self.widgets.start_countdown(minutes, label)

    def _cancel_timer(self):
        self.widgets.cancel_countdown()

    def _custom_timer(self):
        self.busy = True
        try:
            mins = simpledialog.askinteger(
                '自定义倒计时', '请输入倒计时分钟数（1 ~ 360）：',
                parent=self.win, initialvalue=20, minvalue=1, maxvalue=360,
            )
            if mins:
                self.widgets.start_countdown(mins, '倒计时')
        finally:
            self.app.discard_mouse()
            self.busy = False
            self.last_inside = time.monotonic()

    def refresh_countdown_ui(self, text=None):
        """同步侧边栏倒计时标签或切换按钮态。"""
        active = self.widgets.timer_end > time.monotonic()
        if active and self.countdown_label is not None and self.countdown_label.winfo_exists():
            self.countdown_label.configure(text=text or self.widgets.current_countdown_text())
        else:
            self.render()
            self._refresh_if_visible()

    # ------------------------------------------------------------ 便签动作
    def _focus_note_entry(self, entry_widget):
        self._note_focused = True
        self.last_inside = time.monotonic()
        self._set_noactivate(False)
        try:
            entry_widget.focus_force()
        except tk.TclError:
            pass

    def _add_note(self):
        txt = self._note_draft.get().strip()[:MAX_NOTE_LEN]
        if not txt or len(self.notes) >= MAX_NOTES:
            return
        self.notes.append({'text': txt, 'done': False})
        self._note_draft.set('')
        self._note_focused = False
        self.widgets.save()
        self.render()
        self._refresh_if_visible()

    def _toggle_note(self, idx):
        if 0 <= idx < len(self.notes):
            self.notes[idx]['done'] = not self.notes[idx].get('done', False)
            self.widgets.save()
            self.render()
            self._refresh_if_visible()

    def _delete_note(self, idx):
        if 0 <= idx < len(self.notes):
            self.notes.pop(idx)
            self.widgets.save()
            self.render()
            self._refresh_if_visible()

    # ------------------------------------------------------------ 文件与分组操作
    def _populate_files_box(self):
        if self.files_box is None or not self.files_box.winfo_exists():
            return
        for child in self.files_box.winfo_children():
            child.destroy()
        if not self.fresh_files:
            tk.Label(self.files_box, text='桌面与下载暂无近期新文件', bg=CARD_BG, fg=MUTED_FG,
                     font=('Microsoft YaHei UI', 8), anchor='w').pack(fill='x', pady=2)
            return
        for item in self.fresh_files:
            disp = f"{item['icon']} [{item['source']}] {_clip(item['name'], 18)}"
            fb = tk.Button(
                self.files_box, text=disp, command=lambda p=item['path']: self.open(p, record_recent=False),
                bg=ITEM_BG, fg=TEXT_FG, activebackground=ACTIVE_BG, activeforeground='white',
                font=('Microsoft YaHei UI', 9), relief='flat', anchor='w', padx=8, pady=4, cursor='hand2',
            )
            fb.pack(fill='x', pady=1)
            fb.bind('<Button-3>', lambda _e, p=item['path']: self._reveal_file(p))

    def _refresh_recent_files(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_file_scan < RECENT_SCAN_TTL:
            return
        self._last_file_scan = now
        latest = scan_recent_files(limit=5)
        old_paths = [x['path'] for x in self.fresh_files]
        new_paths = [x['path'] for x in latest]
        self.fresh_files = latest
        if old_paths != new_paths:
            self._populate_files_box()
            self.win.update_idletasks()
            self._refresh_if_visible()

    def _reveal_file(self, path_str):
        if not reveal_in_explorer(path_str):
            self._show_error(f'文件已移动或不存在：{path_str}')
            self._refresh_recent_files(force=True)
            return
        self.win.withdraw()

    def set_theme_label(self, dark):
        if self.theme_item is not None and self.theme_item.winfo_exists():
            self.theme_item.configure(text=('☀ 切浅色' if dark else '☾ 切深色'))

    def recolor(self, accent):
        self.render()
        self._refresh_if_visible()

    def _reposition(self, anchor_x, anchor_y):
        """按所在显示器工作区重新计算启动栏尺寸并贴右边缘居中定位。"""
        wl, wt, wr, wb = bounds(anchor_x, anchor_y, work=True)
        width = max(self.win.winfo_reqwidth(), round(300 * self.widgets.ui_scale))
        height = min(self.win.winfo_reqheight(), max(200, wb - wt - 16))
        self.win.geometry(f'{width}x{height}+{wr - width}+{wt + max(0, (wb - wt - height) // 2)}')

    def _refresh_if_visible(self):
        if self.win.winfo_viewable():
            self._reposition(self.win.winfo_x() + self.win.winfo_width() // 2,
                             self.win.winfo_y() + self.win.winfo_height() // 2)

    def _toggle_groups_section(self):
        self.groups_section_open = not self.groups_section_open
        self.render()
        self._refresh_if_visible()

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

    def open(self, path, record_recent=True):
        if not os.path.exists(path):
            self._show_error(f'路径不存在：{path}')
            self._refresh_recent_files(force=True)
            return
        try:
            os.startfile(path)
        except OSError as e:
            self._show_error(str(e))
            return
        if record_recent:
            key = os.path.normcase(path)
            self.recent = ([path] + [p for p in self.recent if os.path.normcase(p) != key])[:MAX_RECENT]
            self.widgets.save()
        self.win.withdraw()
        if record_recent:
            self.render()

    # ------------------------------------------------------------ 主循环
    def tick(self, now, p, monitor, blocked, monitor_edge, theme_edge, mouse_held=False):
        """返回本轮是否刚弹出（双阈值防回弹：6px 触发、18px 保持，按住鼠标键或拉滚动条时不误弹）。"""
        l, t, r, b = monitor
        enabled = self.enabled.get()
        scale = max(1.0, getattr(self.widgets, 'ui_scale', 1.0))
        enter_px = max(6, round(6 * scale))
        hold_px = max(18, round(18 * scale))
        max_slide_y = round(48 * scale)

        dist = r - 1 - p.x
        threshold = hold_px if self.edge_since is not None else enter_px
        edge = (
            enabled
            and dist <= threshold
            and not blocked
            and not mouse_held
            and not monitor_edge
            and not theme_edge
        )
        if not edge:
            self.edge_since = None
        elif self.edge_since is None or self.edge_monitor != monitor:
            self.edge_since = now
            self.edge_y = p.y
            self.edge_monitor = monitor
        elif abs(p.y - self.edge_y) > max_slide_y:
            # 鼠标在右边缘快速上下拖动滚动条时重置计时，稳停后才呼出抽屉
            self.edge_since = now
            self.edge_y = p.y

        opened = False
        if edge and now - self.edge_since >= OPEN_DELAY:
            if not self.win.winfo_viewable():
                self._set_noactivate(True)
                self._refresh_recent_files()
                self._reposition(p.x, p.y)
                self.win.deiconify()
                opened = True
            self.last_inside = now

        if self.win.winfo_viewable():
            x, y = self.win.winfo_x(), self.win.winfo_y()
            if x <= p.x < x + self.win.winfo_width() and y <= p.y < y + self.win.winfo_height():
                self.last_inside = now
            if self._note_focused:
                self.last_inside = now
            if not self.busy and (not enabled or blocked or now - self.last_inside > CLOSE_DELAY):
                self._note_focused = False
                self._set_noactivate(True)
                self.win.withdraw()
        return opened
