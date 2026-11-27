"""右侧屏幕边缘呼出的快捷中枢抽屉：
1. 桌面快捷开关（☕ 屏幕常亮防休眠、🌙 一键息屏、☀/☾ 深浅主题、🧰 联动 DeskKit 本地工具箱）
2. 🍅 极简倒计时 / 番茄钟（直接融入桌面大时钟显示）
3. ⚡ 最新文件速取（自动列出「下载 / 桌面」最新产生的 5 个文件，左键打开、右键定位）
4. 📝 随手便签 / 待办（左键勾选划掉、右键删除、回车秒加）
5. 可折叠的自定义分组与主设置入口
"""
import os
from pathlib import Path
import re
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from display_geometry import bounds
from preferences import APP_DIR
from quick_hub import (MAX_NOTES, MAX_NOTE_LEN, KeepAwake, copy_file_to_clipboard,
                       copy_path_to_clipboard, launch_deskkit, reveal_in_explorer,
                       sanitize_notes, scan_recent_files, turn_off_monitors)
from win32 import (user32, GetWindowLongPtr, SetWindowLongPtr, add_exstyle, is_own_window,
                   toplevel_hwnd, GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW)

BAR_BG = '#0F172A'
CARD_BG = '#1E293B'
ITEM_BG = '#263349'
ACTIVE_BG = '#334155'
GREEN_ACTIVE = '#15803D'
MUTED_FG = '#94A3B8'
TEXT_FG = '#F1F5F9'

_HEX_RE = re.compile(r'#[0-9a-fA-F]{6}')
BAR_STYLE_DEFAULTS = dict(
    font_size=9,
    opacity=96,
    bg='#0F172A',
    card_bg='#1E293B',
    item_bg='#263349',
    fg='#F1F5F9',
    muted_fg='#94A3B8',
    accent='#38BDF8',
)
BAR_STYLE_PRESETS = {
    '深海暗夜': dict(bg='#0F172A', card_bg='#1E293B', item_bg='#263349', fg='#F1F5F9', muted_fg='#94A3B8', accent='#38BDF8'),
    '磨砂纯白': dict(bg='#F1F5F9', card_bg='#FFFFFF', item_bg='#E2E8F0', fg='#0F172A', muted_fg='#64748B', accent='#2563EB'),
    '极简炭黑': dict(bg='#111111', card_bg='#1C1C1E', item_bg='#2C2C2E', fg='#F5F5F7', muted_fg='#98989D', accent='#0A84FF'),
}


def sanitize_bar_style(raw):
    style = BAR_STYLE_DEFAULTS.copy()
    if isinstance(raw, dict):
        fs = raw.get('font_size')
        if isinstance(fs, int) and not isinstance(fs, bool):
            style['font_size'] = max(8, min(14, fs))
        op = raw.get('opacity')
        if isinstance(op, int) and not isinstance(op, bool):
            style['opacity'] = max(45, min(100, op))
        for k in ('bg', 'card_bg', 'item_bg', 'fg', 'muted_fg', 'accent'):
            val = raw.get(k)
            if isinstance(val, str) and _HEX_RE.fullmatch(val):
                style[k] = val
    return style

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
        self.style = sanitize_bar_style(cfg.get('bar_style'))
        self.style_dialog = None
        self._style_vars = {}
        self._last_alpha = None

        self.enabled = tk.BooleanVar(value=bool(cfg.get('bar', True)))
        self.expanded = set()
        self.groups_section_open = False
        self.busy = False            # 模态对话框打开期间为 True：不收起、不响应桌面点击
        self._note_focused = False   # 正在便签输入框打字时保持抽屉展开，绝不吞字
        self._note_focus_at = 0.0    # 点击激活便签输入框的时刻（提供前台切换宽限期）
        self._note_last_input = 0.0  # 最近一次在便签输入框敲字或上屏的时刻
        self._note_draft = tk.StringVar(master=widgets.root, value='')
        self._note_draft.trace_add('write', lambda *_: setattr(self, '_note_last_input', time.monotonic()))

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
        self.scroll_canvas = None
        self.scrollbar = None
        self.body = None
        self.foot = None
        self._body_win_id = None
        self._scrollable = False

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
    def _c(self, key):
        return self.style[key]

    def _f(self, delta=0, bold=False, overstrike=False):
        sz = max(7, self.style['font_size'] + delta)
        parts = ['Microsoft YaHei UI', sz]
        styles = []
        if bold:
            styles.append('bold')
        if overstrike:
            styles.append('overstrike')
        if styles:
            parts.append(' '.join(styles))
        return tuple(parts)

    def _card(self, title=None, action_text=None, action_cmd=None):
        cbg = self._c('card_bg')
        parent = self.body if self.body is not None else self.win
        frame = tk.Frame(parent, bg=cbg, padx=10, pady=8)
        frame.pack(fill='x', padx=8, pady=(6, 0))
        if title:
            hdr = tk.Frame(frame, bg=cbg)
            hdr.pack(fill='x', pady=(0, 4))
            tk.Label(hdr, text=title, bg=cbg, fg=self._c('muted_fg'),
                     font=self._f(-1, bold=True), anchor='w').pack(side='left', fill='x', expand=True)
            if action_text and action_cmd:
                tk.Button(
                    hdr, text=action_text, command=action_cmd,
                    bg=cbg, fg=self._c('accent'), activebackground=self._c('item_bg'),
                    activeforeground=self._c('fg'), font=self._f(-1), relief='flat',
                    borderwidth=0, padx=4, pady=0, cursor='hand2',
                ).pack(side='right')
        return frame

    def _on_mousewheel(self, event):
        if self._scrollable and self.scroll_canvas is not None and event.delta:
            self.scroll_canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
            return 'break'

    def _bind_scroll_tree(self, widget):
        try:
            widget.bind('<MouseWheel>', self._on_mousewheel, add='+')
            for child in widget.winfo_children():
                self._bind_scroll_tree(child)
        except tk.TclError:
            pass

    def _pill_btn(self, parent, text, command, bg=None, fg=None):
        bg = self._c('item_bg') if bg is None else bg
        fg = self._c('fg') if fg is None else fg
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=self._c('accent'), activeforeground='white',
            font=self._f(0), relief='flat', borderwidth=0,
            cursor='hand2', pady=5, padx=6,
        )

    def render(self):
        bg = self._c('bg')
        cbg = self._c('card_bg')
        ibg = self._c('item_bg')
        fg = self._c('fg')
        mfg = self._c('muted_fg')
        acc = self._c('accent')

        self.win.configure(bg=bg, highlightbackground=ibg)
        alpha = self.style['opacity'] / 100.0
        if alpha != self._last_alpha:
            self._last_alpha = alpha
            self.win.attributes('-alpha', alpha)

        for child in self.win.winfo_children():
            child.destroy()

        # 底部紧凑控制入口固定吸底，保证字号调大或便签较多时永远不会被挤出窗口外
        foot = self.foot = tk.Frame(self.win, bg=bg, padx=8, pady=8)
        foot.pack(side='bottom', fill='x')
        foot.columnconfigure((0, 1, 2), weight=1, uniform='fbtn')
        self._pill_btn(foot, '⚙️ 主面板', self.app.open_settings).grid(row=0, column=0, sticky='ew', padx=(0, 2))
        self._pill_btn(foot, '🎨 组件', self.widgets.settings).grid(row=0, column=1, sticky='ew', padx=2)
        self._pill_btn(foot, '🖌️ 外观', self.open_style_settings).grid(row=0, column=2, sticky='ew', padx=(2, 0))

        # 中部自适应滚动区（未超高时不显示滚动条，超高时支持滚轮与窄滚动条浏览）
        scroll_wrap = tk.Frame(self.win, bg=bg)
        scroll_wrap.pack(side='top', fill='both', expand=True)
        self.scrollbar = tk.Scrollbar(
            scroll_wrap, orient='vertical', width=8,
            bg=ibg, troughcolor=bg, activebackground=acc, highlightthickness=0, bd=0,
        )
        self.scroll_canvas = tk.Canvas(
            scroll_wrap, bg=bg, highlightthickness=0, bd=0,
            yscrollcommand=self.scrollbar.set, yscrollincrement=24,
        )
        self.scrollbar.configure(command=self.scroll_canvas.yview)
        self.scroll_canvas.pack(side='left', fill='both', expand=True)
        self.body = tk.Frame(self.scroll_canvas, bg=bg)
        self._body_win_id = self.scroll_canvas.create_window((0, 0), window=self.body, anchor='nw')

        # 1. 顶部快捷控制开关（2x2 网格）
        top_card = self._card('快捷开关')
        grid = tk.Frame(top_card, bg=cbg)
        grid.pack(fill='x')
        grid.columnconfigure((0, 1), weight=1, uniform='qbtn')

        awake_on = self.awake.active
        self.awake_btn = self._pill_btn(
            grid,
            '☕ 常亮开启中' if awake_on else '☕ 屏幕常亮',
            self._toggle_awake,
            bg=GREEN_ACTIVE if awake_on else ibg,
            fg='white' if awake_on else fg,
        )
        self.awake_btn.grid(row=0, column=0, sticky='ew', padx=(0, 3), pady=(0, 4))

        off_btn = self._pill_btn(grid, '🌙 一键息屏', self._sleep_screen)
        off_btn.grid(row=0, column=1, sticky='ew', padx=(3, 0), pady=(0, 4))

        self.theme_item = self._pill_btn(grid, '', self.app.toggle_theme)
        self.theme_item.grid(row=1, column=0, sticky='ew', padx=(0, 3))
        self.set_theme_label(self.widgets.theme_button.dark)

        dk_btn = self._pill_btn(grid, '🧰 本地工具箱', self._open_deskkit, fg=acc)
        dk_btn.grid(row=1, column=1, sticky='ew', padx=(3, 0))
        dk_btn.bind('<Button-3>', lambda _e: self._pick_deskkit_path())

        # 2. 独立可拖动番茄钟 / 倒计时（附带快捷外观与提示语设置入口）
        cd_widget = getattr(self.widgets, 'countdown', None)
        cd_card = self._card(
            '🍅 番茄钟（开启后可拖动）',
            action_text='⚙ 设置' if cd_widget is not None else None,
            action_cmd=(cd_widget.open_settings if cd_widget is not None else None),
        )
        if cd_widget is not None and cd_widget.active:
            row = tk.Frame(cd_card, bg=cbg)
            row.pack(fill='x')
            txt = self.widgets.current_countdown_text() or '⏳ 倒计时进行中'
            self.countdown_label = tk.Label(
                row, text=txt, bg=cbg, fg=acc,
                font=self._f(0, bold=True), anchor='w',
            )
            self.countdown_label.pack(side='left', fill='x', expand=True)
            self._pill_btn(row, '取消', self._cancel_timer, bg='#7F1D1D', fg='white').pack(side='right')
        else:
            self.countdown_label = None
            t_row = tk.Frame(cd_card, bg=cbg)
            t_row.pack(fill='x')
            t_row.columnconfigure((0, 1, 2, 3), weight=1, uniform='tbtn')
            for idx, (lbl, mins, tag) in enumerate([
                ('15分', 15, '倒计时'), ('25分', 25, '专注'), ('45分', 45, '专注'), ('自定义', 0, '倒计时'),
            ]):
                cmd = self._custom_timer if mins == 0 else (lambda m=mins, tg=tag: self._start_timer(m, tg))
                b = self._pill_btn(t_row, lbl, cmd)
                b.grid(row=0, column=idx, sticky='ew', padx=2)

        # 3. 最新文件速取（自动扫描下载与桌面 Top 5，左键点复制可粘贴到聊天/网页/AI，右键点复制取路径）
        files_card = self._card('⚡ 最新文件（左键打开 · 右键定位 · 点复制去粘贴）')
        self.files_box = tk.Frame(files_card, bg=cbg)
        self.files_box.pack(fill='x')
        self._populate_files_box()

        # 4. 随手便签 / 待办
        note_card = self._card('📝 随手便签（左键划掉 · 右键删除）')
        for idx, note in enumerate(self.notes):
            done = note.get('done', False)
            prefix = '☑ ' if done else '☐ '
            fg_col = mfg if done else fg
            nb = tk.Button(
                note_card, text=prefix + _clip(note['text'], 22),
                command=lambda i=idx: self._toggle_note(i),
                bg=cbg, fg=fg_col, activebackground=ibg, activeforeground=fg,
                font=self._f(0, overstrike=done), relief='flat', borderwidth=0, anchor='w', padx=4, pady=2, cursor='hand2',
            )
            nb.pack(fill='x')
            nb.bind('<Button-3>', lambda _e, i=idx: self._delete_note(i))

        if len(self.notes) < MAX_NOTES:
            in_row = tk.Frame(note_card, bg=cbg)
            in_row.pack(fill='x', pady=(4, 0))
            entry = tk.Entry(
                in_row, textvariable=self._note_draft, bg=bg, fg=fg,
                insertbackground=fg, relief='flat', font=self._f(0),
            )
            entry.pack(side='left', fill='x', expand=True, ipady=4, padx=(0, 4))
            entry.bind('<Button-1>', lambda _e, w=entry: self._focus_note_entry(w))
            entry.bind('<Key>', lambda _e: setattr(self, '_note_last_input', time.monotonic()))
            entry.bind('<KeyRelease>', lambda _e: setattr(self, '_note_last_input', time.monotonic()))
            entry.bind('<FocusIn>', lambda _e: self._on_note_focus_in())
            entry.bind('<FocusOut>', lambda _e: setattr(self, '_note_focused', False))
            entry.bind('<Return>', lambda _e: self._add_note())
            self._pill_btn(in_row, '＋添加', self._add_note, bg='#2563EB', fg='white').pack(side='right')

        # 5. 可折叠的自定义分组（按需展开，平时仅占 1 行保持清爽）
        grp_card = self._card()
        toggle_txt = ('▾ ' if self.groups_section_open else '▸ ') + '自定义常用分组'
        tk.Button(
            grp_card, text=toggle_txt, command=self._toggle_groups_section,
            bg=cbg, fg=acc, activebackground=ibg, activeforeground=fg,
            font=self._f(0, bold=True), relief='flat', anchor='w', padx=2, pady=2, cursor='hand2',
        ).pack(fill='x')

        if self.groups_section_open:
            for name, paths in self.groups.items():
                hdr = tk.Button(
                    grp_card, text=('  ▾ ' if name in self.expanded else '  ▸ ') + name,
                    command=lambda n=name: self.fold(n),
                    bg=cbg, fg=fg, activebackground=ibg, activeforeground=fg,
                    font=self._f(0), relief='flat', anchor='w', pady=2, cursor='hand2',
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
                            bg=ibg, fg=fg, activebackground=acc, activeforeground='white',
                            font=self._f(-1), relief='flat', anchor='w', padx=8, pady=3, cursor='hand2',
                        )
                        b.pack(fill='x', pady=1)
                        b.bind('<Button-3>', lambda _e, n=name, p=path: self.remove(n, p))
                    act_r = tk.Frame(grp_card, bg=cbg)
                    act_r.pack(fill='x', pady=2)
                    self._pill_btn(act_r, '＋软件/文件', lambda n=name: self.add(n, False)).pack(side='left', expand=True, fill='x', padx=(0, 2))
                    self._pill_btn(act_r, '＋文件夹', lambda n=name: self.add(n, True)).pack(side='left', expand=True, fill='x', padx=(2, 0))
            self._pill_btn(grp_card, '＋ 新建分组', self.create_group).pack(fill='x', pady=(4, 0))

        self._bind_scroll_tree(self.scroll_canvas)
        self.win.update_idletasks()

    def open_style_settings(self):
        """打开右侧快捷中枢专属外观与颜色设置窗口（实时预览生效）。"""
        if self.style_dialog and self.style_dialog.winfo_exists():
            self.style_dialog.lift()
            return
        w = self.style_dialog = tk.Toplevel(self.widgets.root)
        w.title('快捷中枢外观与颜色')
        ui = max(1.0, getattr(self.widgets, 'ui_scale', 1.0))
        w.geometry(f'{round(410 * ui)}x{round(360 * ui)}')
        w.minsize(360, 320)

        pane = ttk.Frame(w, padding=14)
        pane.pack(fill='both', expand=True)
        ttk.Label(pane, text='右侧快捷中枢外观 · 修改实时生效',
                  font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w', pady=(0, 8))

        vars_ = self._style_vars = {}

        def apply(*_args):
            self.style['font_size'] = int(vars_['font_size'].get())
            self.style['opacity'] = int(vars_['opacity'].get())
            self.style = sanitize_bar_style(self.style)
            self.render()
            self._refresh_if_visible()
            self.widgets.queue_save()

        def row(lbl):
            f = ttk.Frame(pane)
            f.pack(fill='x', pady=4)
            ttk.Label(f, text=lbl, width=10).pack(side='left')
            return f

        for k, lbl, lo, hi in (('font_size', '字号大小', 8, 14), ('opacity', '不透明度%', 45, 100)):
            v = vars_[k] = tk.IntVar(value=self.style[k])
            tk.Scale(row(lbl), from_=lo, to=hi, orient='horizontal', variable=v,
                     command=apply, showvalue=True).pack(side='left', fill='x', expand=True)

        def apply_preset(name):
            self.style.update(BAR_STYLE_PRESETS[name])
            apply()

        p_row = row('一键配色')
        for pname in BAR_STYLE_PRESETS:
            ttk.Button(p_row, text=pname, command=lambda n=pname: apply_preset(n)).pack(side='left', padx=2)

        def pick_col(key, title):
            col = colorchooser.askcolor(self.style[key], parent=w, title=title)[1]
            if col:
                self.style[key] = col
                apply()

        c1 = row('背景配色')
        ttk.Button(c1, text='面板底色', command=lambda: pick_col('bg', '选择面板底色')).pack(side='left', padx=2)
        ttk.Button(c1, text='卡片底色', command=lambda: pick_col('card_bg', '选择分区卡片颜色')).pack(side='left', padx=2)
        ttk.Button(c1, text='按钮颜色', command=lambda: pick_col('item_bg', '选择按钮背景颜色')).pack(side='left', padx=2)

        c2 = row('文字配色')
        ttk.Button(c2, text='主文字色', command=lambda: pick_col('fg', '选择主文字颜色')).pack(side='left', padx=2)
        ttk.Button(c2, text='次要字色', command=lambda: pick_col('muted_fg', '选择标题/次要文字颜色')).pack(side='left', padx=2)
        ttk.Button(c2, text='强调点缀色', command=lambda: pick_col('accent', '选择强调色')).pack(side='left', padx=2)

        def reset_default():
            self.style = BAR_STYLE_DEFAULTS.copy()
            vars_['font_size'].set(self.style['font_size'])
            vars_['opacity'].set(self.style['opacity'])
            apply()

        btns = ttk.Frame(pane)
        btns.pack(fill='x', pady=(14, 0))
        ttk.Button(btns, text='恢复默认', command=reset_default).pack(side='left')
        ttk.Button(btns, text='完成', command=w.destroy).pack(side='right')

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
        # 启动番茄钟后立即自动收起侧边栏，避免挡视线
        self._dismiss()

    def _cancel_timer(self):
        self.widgets.cancel_countdown()

    def _custom_timer(self):
        self.busy = True
        started = False
        try:
            mins = simpledialog.askinteger(
                '自定义倒计时', '请输入倒计时分钟数（1 ~ 360）：',
                parent=self.win, initialvalue=20, minvalue=1, maxvalue=360,
            )
            if mins:
                self.widgets.start_countdown(mins, '倒计时')
                started = True
        finally:
            self.app.discard_mouse()
            self.busy = False
            self.last_inside = time.monotonic()
        if started:
            self._dismiss()

    def refresh_countdown_ui(self, text=None):
        """同步侧边栏倒计时标签或切换按钮态。"""
        cd_widget = getattr(self.widgets, 'countdown', None)
        active = bool(cd_widget and cd_widget.active)
        if active and self.countdown_label is not None and self.countdown_label.winfo_exists():
            self.countdown_label.configure(text=text or self.widgets.current_countdown_text())
        elif (not active and self.countdown_label is not None) or (active and self.countdown_label is None):
            self.render()
            self._refresh_if_visible()

    # ------------------------------------------------------------ 便签动作
    def _on_note_focus_in(self):
        now = time.monotonic()
        self._note_focused = True
        self._note_focus_at = now
        self._note_last_input = now

    def _focus_note_entry(self, entry_widget):
        now = time.monotonic()
        self._note_focused = True
        self._note_focus_at = now
        self._note_last_input = now
        self.last_inside = now
        self._set_noactivate(False)
        if self.hwnd:
            user32.SetForegroundWindow(self.hwnd)
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
    def _copy_file_item(self, path_str, btn_widget):
        if not copy_file_to_clipboard(path_str, self.hwnd):
            if not os.path.exists(path_str):
                self._show_error(f'无法复制文件（文件已被移动或删除）：\n{path_str}')
                self._refresh_recent_files(force=True)
            else:
                self._show_error('系统剪贴板正被其他软件占用，请稍后再试')
            return
        if btn_widget and btn_widget.winfo_exists():
            btn_widget.configure(text='✓ 已复制', bg=GREEN_ACTIVE, fg='white')
            self.win.after(1200, lambda: (
                btn_widget.configure(text='复制', bg=self._c('item_bg'), fg=self._c('fg'))
                if btn_widget.winfo_exists() else None
            ))

    def _copy_path_item(self, path_str, btn_widget):
        if not copy_path_to_clipboard(path_str, self.hwnd):
            if not os.path.exists(path_str):
                self._show_error(f'无法复制路径（文件已被移动或删除）：\n{path_str}')
                self._refresh_recent_files(force=True)
            else:
                self._show_error('系统剪贴板正被其他软件占用，请稍后再试')
            return
        if btn_widget and btn_widget.winfo_exists():
            btn_widget.configure(text='✓ 路径', bg=GREEN_ACTIVE, fg='white')
            self.win.after(1200, lambda: (
                btn_widget.configure(text='复制', bg=self._c('item_bg'), fg=self._c('fg'))
                if btn_widget.winfo_exists() else None
            ))

    def _populate_files_box(self):
        if self.files_box is None or not self.files_box.winfo_exists():
            return
        cbg = self._c('card_bg')
        ibg = self._c('item_bg')
        fg = self._c('fg')
        mfg = self._c('muted_fg')
        acc = self._c('accent')
        for child in self.files_box.winfo_children():
            child.destroy()
        if not self.fresh_files:
            lbl = tk.Label(self.files_box, text='桌面与下载暂无近期新文件', bg=cbg, fg=mfg,
                           font=self._f(-1), anchor='w')
            lbl.pack(fill='x', pady=2)
            self._bind_scroll_tree(lbl)
            return
        for item in self.fresh_files:
            row = tk.Frame(self.files_box, bg=cbg)
            row.pack(fill='x', pady=1)
            disp = f"{item['icon']} [{item['source']}] {_clip(item['name'], 14)}"
            fb = tk.Button(
                row, text=disp, command=lambda p=item['path']: self.open(p, record_recent=False),
                bg=ibg, fg=fg, activebackground=acc, activeforeground='white',
                font=self._f(0), relief='flat', anchor='w', padx=6, pady=4, cursor='hand2',
            )
            fb.pack(side='left', fill='x', expand=True, padx=(0, 3))
            fb.bind('<Button-3>', lambda _e, p=item['path']: self._reveal_file(p))

            cp_btn = tk.Button(
                row, text='复制', bg=ibg, fg=fg,
                activebackground=GREEN_ACTIVE, activeforeground='white',
                font=self._f(-1), relief='flat', padx=6, pady=4, cursor='hand2',
            )
            cp_btn.configure(command=lambda p=item['path'], b=cp_btn: self._copy_file_item(p, b))
            cp_btn.bind('<Button-3>', lambda _e, p=item['path'], b=cp_btn: self._copy_path_item(p, b))
            cp_btn.pack(side='right')
            self._bind_scroll_tree(row)

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
        """快捷中枢使用自身独立外观配色（self.style），忽略系统强调色轮询以避免重建打断便签输入。"""
        return

    def _reposition(self, anchor_x, anchor_y):
        """按所在显示器工作区重新计算启动栏尺寸、自适应启用滚动并贴右边缘居中定位。"""
        self.win.update_idletasks()
        wl, wt, wr, wb = bounds(anchor_x, anchor_y, work=True)
        max_win_h = max(220, wb - wt - 16)
        min_w = round(300 * self.widgets.ui_scale)

        if self.body is not None and self.foot is not None and self.scroll_canvas is not None:
            body_w = self.body.winfo_reqwidth()
            body_h = self.body.winfo_reqheight()
            foot_w = self.foot.winfo_reqwidth()
            foot_h = self.foot.winfo_reqheight()
            total_h = body_h + foot_h + 4
            need_scroll = total_h > max_win_h
            self._scrollable = need_scroll
            bar_w = 10 if need_scroll else 0
            width = max(body_w + bar_w, foot_w, min_w)
            height = min(total_h, max_win_h)
            if need_scroll:
                if not self.scrollbar.winfo_ismapped():
                    self.scrollbar.pack(side='right', fill='y', padx=(0, 2), pady=(6, 0))
            else:
                if self.scrollbar.winfo_ismapped():
                    self.scrollbar.pack_forget()
                self.scroll_canvas.yview_moveto(0.0)
            inner_w = max(120, width - bar_w - 2)
            self.scroll_canvas.itemconfigure(self._body_win_id, width=inner_w)
            self.scroll_canvas.configure(scrollregion=(0, 0, inner_w, body_h))
        else:
            width = max(self.win.winfo_reqwidth(), min_w)
            height = min(self.win.winfo_reqheight(), max_win_h)

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
            editing_style = bool(self.style_dialog and self.style_dialog.winfo_exists())
            x, y = self.win.winfo_x(), self.win.winfo_y()
            inside = x <= p.x < x + self.win.winfo_width() and y <= p.y < y + self.win.winfo_height()
            if inside or editing_style:
                self.last_inside = now
            if self._note_focused:
                # 防止无边框窗口收不到 FocusOut 导致永远不隐藏，同时保护中文输入法选词与思考停顿：
                # 激活后 0.6s 内提供焦点建立宽限；有草稿文字时不因鼠标移出而超时收起；点击窗外或切走前台立即收起
                fg = user32.GetForegroundWindow()
                grace = (now - self._note_focus_at) < 0.6
                fg_lost = (not grace) and bool(fg) and (fg != self.hwnd) and (not is_own_window(fg))
                clicked_out = (not inside) and mouse_held
                has_draft = bool(self._note_draft.get().strip())
                idle_typing = (not inside) and (not has_draft) and (now - self._note_last_input > 30.0)
                if fg_lost or clicked_out or idle_typing:
                    self._note_focused = False
                    self._set_noactivate(True)
                else:
                    self.last_inside = now
            if not self.busy and not editing_style and (not enabled or blocked or now - self.last_inside > CLOSE_DELAY):
                self._note_focused = False
                self._set_noactivate(True)
                self.win.withdraw()
        return opened
