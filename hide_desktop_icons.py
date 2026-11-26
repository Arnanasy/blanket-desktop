# -*- coding: utf-8 -*-
"""
Blanket - 鼠标热区自动隐藏 / 显示桌面图标
核心功能使用 Python 标准库；壁纸亮度自适应使用可选依赖 Pillow。

功能：
- 点桌面空白：在桌面空白处左键单击即切换图标显示/隐藏，点图标不受影响
- 系统托盘：左键单击切换图标显示/隐藏，右键菜单（打开设置、切换主题、退出程序）
- 自动模式：鼠标移到屏幕热区（默认顶部边缘）只唤出桌面图标，不弹设置窗口，
            鼠标静止无操作达设定时间后自动隐藏，桌面只剩壁纸
- 手动切换：Ctrl + Alt + H 切换图标，Ctrl + Alt + T 切换深浅主题
- 打开设置：右键托盘选“打开设置”，或再次双击快捷方式；单实例不重复开进程
- 开机自启：写入当前用户注册表 Run 项，无需管理员权限；程序文件夹移动后自动修正
- 不重启资源管理器，图标切换即时生效、完全可逆
"""
import sys
sys.dont_write_bytecode = True

import ctypes
import time
import tkinter as tk
from pathlib import Path

import desktop_recovery
from desktop_widgets import Widgets
from desktop_windows import (DesktopClick, get_cursor, icons_visible, set_icons_visible,
                             in_hotzone, input_idle_seconds, invalidate_cache)
from preferences import load_config, save_config, autostart_enabled, set_autostart, repair_autostart
from tray_icon import TrayIcon, HOTKEY_ICONS, HOTKEY_THEME
from win32 import (kernel32, wtsapi32, toplevel_hwnd, key_down, ERROR_ALREADY_EXISTS,
                   EVENT_MODIFY_STATE, WAIT_OBJECT_0, NOTIFY_FOR_THIS_SESSION,
                   VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_CONTROL, VK_MENU, VK_H, VK_T)

# 单实例与“双击快捷方式唤起主窗口”的跨进程内核对象（兼容旧版运行实例唤起）
MUTEX_NAME = 'Local\\Blanket_SingleInstance'
LEGACY_MUTEX_NAME = 'Local\\JingZhuoMian_SingleInstance'
EVENT_NAME = 'Local\\Blanket_ShowRequest'
LEGACY_EVENT_NAME = 'Local\\JingZhuoMian_ShowRequest'

POLL_MS = 150            # 主轮询间隔
MOUSE_POLL_MS = 20       # 鼠标左键轮询间隔（不装全局低级钩子，避免拖慢系统鼠标）

# 配色（Windows 11 简洁浅色风格）
BG = '#F4F7FC'
BLUE = '#2563EB'
BLUE_D = '#1D4ED8'
GREEN = '#16A34A'
GREEN_D = '#15803D'
GRAY = '#94A3B8'
RED = '#DC2626'
RED_D = '#B91C1C'
TEXT = '#1E293B'
SUBTEXT = '#64748B'
WHITE = '#FFFFFF'

# 主窗口深色配色（跟随系统主题一键切换）
DARK_BG = '#0F172A'
DARK_TEXT = '#E2E8F0'
DARK_SUBTEXT = '#94A3B8'
DARK_PANEL = '#1E293B'
DARK_PANEL_HI = '#334155'
DARK_SELECT = '#1E293B'


def _system_dark():
    try:
        from theme_switch import is_dark
        return is_dark()
    except ImportError:
        return False


class App:
    def __init__(self, root, show_event=None):
        self.root = root
        self.cfg = load_config()
        self._show_event = show_event
        # 启动时要恢复的图标状态必须先取出来：恢复完成前 refresh_ui 不得覆盖它
        self._restore_target = self.cfg.get('last_visible')
        self._restoring = True
        self._ui_state = 'unset'
        self._dark = None
        self._quitting = False
        self.recovery_guard = None
        try:
            from preferences import resource_path
            root.iconbitmap(default=resource_path('Blanket.ico'))
        except tk.TclError:
            pass
        root.title('Blanket')
        root.configure(bg=BG)
        root.resizable(False, False)

        f_title = ('Microsoft YaHei UI', 16, 'bold')
        f_sub = ('Microsoft YaHei UI', 9)
        f_btn = ('Microsoft YaHei UI', 12, 'bold')
        f_state = ('Microsoft YaHei UI', 10, 'bold')
        f_normal = ('Microsoft YaHei UI', 9)
        f_small = ('Microsoft YaHei UI', 8)

        self.subtext_labels = set()
        self.neutral_buttons = set()

        # 顶部极简标题栏：左侧品牌名 + 右侧日夜主题切换小按钮
        header = tk.Frame(root, bg=BG)
        header.pack(fill='x', padx=22, pady=(16, 10))
        tk.Label(header, text='Blanket', font=f_title, bg=BG, fg=TEXT).pack(side='left')
        self.theme_button = tk.Button(
            header, text='☾', command=self.toggle_theme, font=('Segoe UI Symbol', 11),
            bg='#E2E8F0', fg=TEXT, activebackground='#CBD5E1',
            relief='flat', borderwidth=0, cursor='hand2', width=3, pady=2,
        )
        self.theme_button.pack(side='right')
        self.neutral_buttons.add(self.theme_button)

        # 主切换按钮
        self.btn = tk.Button(root, text='立即隐藏', font=f_btn,
                             bg=BLUE, fg=WHITE, activebackground=BLUE_D,
                             activeforeground=WHITE, relief='flat',
                             borderwidth=0, cursor='hand2',
                             width=18, pady=9, command=self.manual_toggle)
        self.btn.pack(fill='x', padx=22)

        # 状态行
        state_frame = tk.Frame(root, bg=BG)
        state_frame.pack(pady=(9, 4))
        self.dot_canvas = tk.Canvas(state_frame, width=12, height=12, bg=BG, highlightthickness=0)
        self.dot_canvas.pack(side='left', padx=(0, 6))
        self.dot = self.dot_canvas.create_oval(1, 1, 11, 11, fill=GREEN, outline=GREEN)
        self.state_label = tk.Label(state_frame, text='桌面图标显示中', font=f_state, bg=BG, fg=TEXT)
        self.state_label.pack(side='left')

        # 精简设置区
        opt = tk.Frame(root, bg=BG)
        opt.pack(fill='x', padx=22, pady=(6, 2))

        def check(text, variable, command, **pack):
            tk.Checkbutton(opt, text=text, variable=variable, font=f_normal, bg=BG, fg=TEXT,
                           activebackground=BG, selectcolor=WHITE, anchor='w',
                           command=command).pack(anchor='w', **pack)

        self.focus_guard_var = tk.BooleanVar(value=bool(self.cfg.get('focus_guard', True)))
        check('游戏 / 窗口防误触', self.focus_guard_var, self._persist)

        repair_autostart()     # 程序文件夹移动过或存在旧注册表名时，自动迁移修正
        self.startup_var = tk.BooleanVar(value=autostart_enabled())
        check('开机自启', self.startup_var, self._toggle_autostart, pady=(4, 0))

        self.auto_var = tk.BooleanVar(value=bool(self.cfg['auto']))
        check('鼠标移到边缘自动隐藏', self.auto_var, self._on_auto_changed, pady=(4, 0))

        # 仅在勾选「自动隐藏」时展开的紧凑两行细项（未勾选时自动收起，保持主窗口极致清爽）
        self.auto_detail_frame = tk.Frame(opt, bg=BG)
        self.zone_var = tk.StringVar(value=self.cfg['zone'])
        zone_row = tk.Frame(self.auto_detail_frame, bg=BG)
        zone_row.pack(fill='x', pady=(4, 2))
        tk.Label(zone_row, text='热区：', font=f_small, bg=BG, fg=SUBTEXT).pack(side='left')
        for text, val in [('顶部', 'top'), ('任意边', 'any'), ('左上角', 'corner')]:
            tk.Radiobutton(zone_row, text=text, variable=self.zone_var, value=val,
                           font=f_small, bg=BG, fg=TEXT, activebackground=BG,
                           selectcolor=WHITE, command=self._persist).pack(side='left', padx=(0, 4))

        self.delay_var = tk.StringVar(value=str(self.cfg['delay']))
        delay_row = tk.Frame(self.auto_detail_frame, bg=BG)
        delay_row.pack(fill='x', pady=(0, 2))
        tk.Label(delay_row, text='延时：', font=f_small, bg=BG, fg=SUBTEXT).pack(side='left')
        for text, val in [('5秒', '5'), ('10秒', '10'), ('20秒', '20')]:
            tk.Radiobutton(delay_row, text=text, variable=self.delay_var, value=val,
                           font=f_small, bg=BG, fg=TEXT, activebackground=BG,
                           selectcolor=WHITE, command=self._persist).pack(side='left', padx=(0, 6))

        if self.auto_var.get():
            self.auto_detail_frame.pack(fill='x', padx=(16, 0), pady=(2, 2))

        # 底部一行三按钮
        actions = tk.Frame(root, bg=BG)
        actions.pack(fill='x', padx=22, pady=(10, 4))
        actions.columnconfigure((0, 1, 2), weight=1, uniform='action')

        def action(label, command, column, danger=False):
            button = tk.Button(actions, text=label, command=command, font=f_normal,
                               bg=RED if danger else '#E2E8F0', fg=WHITE if danger else TEXT,
                               activebackground=RED_D if danger else '#CBD5E1',
                               relief='flat', borderwidth=0, cursor='hand2', pady=7)
            if not danger:
                self.neutral_buttons.add(button)
            button.grid(row=0, column=column, sticky='ew', padx=3)
            return button

        action('组件设置', lambda: self.widgets.settings(), 0)
        action('藏到托盘', self.hide_window, 1)
        action('退出', self.quit_app, 2, danger=True)

        hint_label = tk.Label(root, text='Ctrl+Alt+H 切图标 · Ctrl+Alt+T 切主题',
                              font=f_small, bg=BG, fg=SUBTEXT)
        hint_label.pack(pady=(4, 12))
        self.subtext_labels.add(hint_label)

        self.pause_button = None
        self._update_theme_label(_system_dark())
        self._paused = False

        # 运行状态
        self._in_zone = False
        self._hk_held = False
        self._theme_held = False
        self._last_x, self._last_y = get_cursor()
        self._last_activity = time.monotonic()
        self._grace_until = time.monotonic() + 5  # 启动 5 秒宽限，先看清界面

        root.protocol('WM_DELETE_WINDOW', self.hide_window)

        # 系统托盘图标：WNDPROC 只入队，由 poll_mouse 在正常 Tk 上下文里消费，彻底杜绝 Tcl 重入崩溃
        top_hwnd = toplevel_hwnd(root)
        self.tray = TrayIcon(top_hwnd, self._tray_toggle, self._tray_open, self.quit_app)
        self.tray.on_resume = self.resume_desktop
        self.tray.on_theme = self.toggle_theme
        self.tray.get_focus_guard = lambda: self.focus_guard_var.get()
        self.tray.on_toggle_focus_guard = self.toggle_focus_guard
        self.tray.on_hotkey_icons = self.manual_toggle
        self.tray.on_hotkey_theme = self.toggle_theme
        self.mouse_cutoff = 0
        self.tray.menu_closed = self.discard_mouse
        self.tray.install()
        wtsapi32.WTSRegisterSessionNotification(top_hwnd, NOTIFY_FOR_THIS_SESSION)

        # 点桌面空白处切换图标：使用轻量 20ms 轮询，不安装 WH_MOUSE_LL 全局钩子，
        # 避免 Python GIL 阻塞系统级鼠标消息导致任务栏/游戏里鼠标迟钝
        self.desktop_click = DesktopClick(self._click_unavailable)
        self.widgets = Widgets(self)
        self.restore_visibility()

        self._persist()
        self.refresh_ui()
        self.sync_theme(_system_dark())
        self.root.after(POLL_MS, self.poll)
        self.root.after(10, self.poll_mouse)

    # ------------------------------------------------------------ 鼠标
    def _click_unavailable(self):
        from tkinter import messagebox
        self.root.after_idle(lambda: messagebox.showinfo(
            '桌面点击', 'Windows 暂时无法识别桌面空白。可用托盘图标或 Ctrl + Alt + H 切换。', parent=self.root))

    def discard_mouse(self):
        """菜单或对话框关闭后短暂忽略点击，避免松手瞬间误触发桌面切换。"""
        self.mouse_cutoff = time.monotonic() + 0.12
        self.desktop_click.reset()

    def poll_mouse(self):
        try:
            self.tray.process_events()
            if self._quitting:
                return
            if self.widgets.busy or time.monotonic() <= self.mouse_cutoff:
                self.desktop_click.reset()
            elif self.desktop_click.check(suppress=self.widgets.guard_active):
                self._blank_click()
        finally:
            if not self._quitting:
                self.root.after(MOUSE_POLL_MS, self.poll_mouse)

    # ------------------------------------------------------------ 托盘回调
    def _tray_toggle(self):
        self.manual_toggle()

    def open_settings(self):
        self._grace_until = time.monotonic() + 5
        self.root.deiconify()
        self.root.lift()
        self._last_activity = time.monotonic()

    _tray_open = open_settings

    def _blank_click(self):
        """点桌面空白处：切换图标显示/隐藏。"""
        self.manual_toggle()

    # ------------------------------------------------------------ 操作
    def manual_toggle(self):
        vis = icons_visible()
        if vis is None:
            self.state_label.config(text='无法获取桌面状态')
            return
        set_icons_visible(not vis)
        # 刷新活动计时，显示后给用户完整的无操作宽限，避免太快又隐藏
        self._last_activity = time.monotonic()
        self.refresh_ui()

    def _on_auto_changed(self):
        if self.auto_var.get():
            self.auto_detail_frame.pack(fill='x', padx=(16, 0), pady=(2, 2))
        else:
            self.auto_detail_frame.pack_forget()
        self.root.update_idletasks()
        w = max(self.root.winfo_reqwidth(), round(300 * self.root.winfo_fpixels('1i') / 96))
        h = min(self.root.winfo_reqheight() + 8, self.root.winfo_screenheight() - 40)
        self.root.geometry(f'{w}x{h}')
        self._persist()

    def pause_auto(self):
        self._paused = not self._paused
        if self.pause_button is not None:
            self.pause_button.config(text='恢复自动隐藏' if self._paused else '暂停自动隐藏')
        self._last_activity = time.monotonic()

    def _update_theme_label(self, dark):
        self.theme_button.config(text='☀' if dark else '☾')

    def sync_theme(self, dark):
        """当外部或程序内切换了系统深浅色时，同步更新主窗口配色与按钮文字。"""
        dark = bool(dark)
        if dark == self._dark:
            return
        self._dark = dark
        self._restyle(dark)
        self._update_theme_label(dark)

    def toggle_theme(self):
        """一键切换 Windows 深色/浅色主题，应用和浏览器同时生效。"""
        try:
            import theme_switch
            dark = theme_switch.toggle()
        except OSError as error:
            from tkinter import messagebox
            messagebox.showerror('主题切换失败', str(error), parent=self.root)
            return
        self.sync_theme(dark)
        self.widgets.theme()               # 刷新时钟配色与主题按钮

    def _restyle(self, dark):
        """让主窗口配色跟随系统深/浅色主题。"""
        bg = DARK_BG if dark else BG
        text = DARK_TEXT if dark else TEXT
        sub = DARK_SUBTEXT if dark else SUBTEXT
        select = DARK_SELECT if dark else WHITE

        def walk(w):
            cls = w.winfo_class()
            if cls == 'Toplevel':
                return  # 挂件窗口各自管理配色，不递归
            try:
                if cls == 'Label':
                    fg = sub if w in self.subtext_labels else text
                    w.configure(bg=bg, fg=fg)
                elif cls in ('Frame', 'Canvas'):
                    w.configure(bg=bg)
                elif cls in ('Checkbutton', 'Radiobutton'):
                    w.configure(bg=bg, activebackground=bg, activeforeground=text, fg=text, selectcolor=select)
                elif cls == 'Button' and w in self.neutral_buttons:
                    w.configure(bg=DARK_PANEL if dark else '#E2E8F0',
                                activebackground=DARK_PANEL_HI if dark else '#CBD5E1',
                                fg=text, activeforeground=text)
                for child in w.winfo_children():
                    walk(child)
            except tk.TclError:
                pass

        # ttk 控件与 tk.Scale 走全局样式，设置窗口等弹窗同步生效
        from tkinter import ttk
        style = ttk.Style(self.root)
        if style.theme_use() != 'clam':
            style.theme_use('clam')
        style.configure('.', background=bg, foreground=text, fieldbackground=select,
                        troughcolor=bg, selectbackground=BLUE, selectforeground=WHITE)
        for name in ('TButton', 'TCheckbutton', 'TRadiobutton', 'TCombobox'):
            style.map(name, foreground=[('disabled', sub), ('active', text), ('!disabled', text)],
                      background=[('active', select)], fieldbackground=[('readonly', select)])
        self.root.option_add('*Scale.background', bg)
        self.root.option_add('*Scale.foreground', text)
        self.root.option_add('*Scale.troughColor', select)
        self.root.option_add('*Toplevel.background', bg)

        def scales(parent):
            for child in parent.winfo_children():
                if isinstance(child, tk.Scale):
                    child.configure(bg=bg, fg=text, troughcolor=select)
                scales(child)

        scales(self.root)
        self.root.configure(bg=bg)
        walk(self.root)

    def desktop_icons_visible(self):
        return icons_visible()

    def hide_window(self):
        self.root.withdraw()

    def _toggle_autostart(self):
        if not set_autostart(self.startup_var.get()):
            # 写入失败则回退为实际状态
            self.startup_var.set(autostart_enabled())

    def toggle_focus_guard(self):
        self.focus_guard_var.set(not self.focus_guard_var.get())
        self._persist()

    def _persist(self):
        self.cfg.update({
            'auto': self.auto_var.get(),
            'focus_guard': self.focus_guard_var.get(),
            'zone': self.zone_var.get(),
            'delay': self.delay_var.get(),
        })
        save_config(self.cfg)

    def restore_visibility(self, attempt=0):
        """恢复上次退出前的图标状态；资源管理器尚未就绪时每 0.5 秒重试，最多 10 秒。"""
        if self._restore_target is not None:
            state = icons_visible()
            if state is None and attempt < 20:
                self.root.after(500, lambda: self.restore_visibility(attempt + 1))
                return
            if state is not None and state != self._restore_target:
                set_icons_visible(self._restore_target)
        self._restoring = False
        self.refresh_ui()

    def resume_desktop(self):
        """睡眠唤醒、解锁、显示器变化或资源管理器重启后，重新定位桌面窗口并刷新挂件。"""
        invalidate_cache()
        self.widgets.reset_refresh()
        self.refresh_ui()

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        # 先立刻隐藏所有窗口并移除托盘图标，避免清理 I/O 与钩子卸载造成“退出卡一下”
        try:
            self.root.withdraw()
            self.widgets.hide_all()
            self.root.update_idletasks()
        except tk.TclError:
            pass
        self.tray.destroy()

        state = icons_visible()
        if state is not None:
            self.cfg['last_visible'] = state
        self.widgets.monitor.cancel_save()
        self.widgets.save()          # 会一并保存 self.cfg
        self.widgets.worker.shutdown(wait=False, cancel_futures=True)
        wtsapi32.WTSUnRegisterSessionNotification(self.tray.hwnd)
        # 退出前确保桌面图标恢复显示；仅在确认恢复成功后才通知守护进程撤退，
        # 若资源管理器暂时无响应或恢复失败，留给守护进程在管道 EOF 后继续重试兜底
        restored = state is True
        if state is False:
            restored = bool(set_icons_visible(True) and icons_visible() is True)
        if self.recovery_guard:
            try:
                if restored:
                    self.recovery_guard.stdin.write(b'restored\n')
                self.recovery_guard.stdin.close()
            except OSError:
                pass
        self.root.destroy()

    # ------------------------------------------------------------ 显示恢复
    def _ensure_shown(self):
        # 热区只唤出桌面图标，不自动弹出设置主窗口（避免打扰）
        if icons_visible() is False:
            set_icons_visible(True)
        # 显示时重置活动计时，给用户完整的无操作宽限
        self._last_activity = time.monotonic()
        self.refresh_ui()

    # ------------------------------------------------------------ 快捷键
    def check_hotkey(self):
        # 已通过 RegisterHotKey 注册的热键由系统事件分发，仅对被其他程序占用的热键回退轮询
        need_icons = HOTKEY_ICONS not in self.tray.hotkeys_registered
        need_theme = HOTKEY_THEME not in self.tray.hotkeys_registered
        if not (need_icons or need_theme):
            return
        combo = key_down(VK_CONTROL) and key_down(VK_MENU)
        if need_icons:
            down = combo and key_down(VK_H)
            if down and not self._hk_held:
                self.manual_toggle()
            self._hk_held = down
        if need_theme:
            theme_down = combo and key_down(VK_T)
            if theme_down and not self._theme_held:
                self.toggle_theme()
            self._theme_held = theme_down

    # ------------------------------------------------------------ 主轮询
    def poll(self):
        try:
            self._poll_once()
        finally:
            # 即使本轮出错也继续调度，避免一次异常让整个程序停止响应
            if not self._quitting:
                self.root.after(POLL_MS, self.poll)

    def _poll_once(self):
        self.check_hotkey()

        # 用户双击快捷方式（第二个实例）请求打开设置窗口
        if self._show_event is not None and \
                kernel32.WaitForSingleObject(self._show_event, 0) == WAIT_OBJECT_0:
            self._tray_open()

        if self.auto_var.get() and not self._paused and time.monotonic() >= self._grace_until:
            x, y = get_cursor()

            # 鼠标位置变化 = 用户在活动，刷新最后活动时间
            if (x, y) != (self._last_x, self._last_y):
                self._last_x, self._last_y = x, y
                self._last_activity = time.monotonic()

            in_zone = in_hotzone(x, y, self.zone_var.get())
            entered = in_zone and not self._in_zone
            self._in_zone = in_zone
            if entered:
                self._ensure_shown()
            elif not in_zone and icons_visible():
                # 图标显示中：仅当鼠标静止、无操作超过设定时间才隐藏（按住鼠标拖拽图标期间持续刷新活动时间）
                if any(key_down(key) for key in (VK_LBUTTON, VK_RBUTTON, VK_MBUTTON)):
                    self._last_activity = time.monotonic()
                    idle = 0
                else:
                    idle = min(time.monotonic() - self._last_activity, input_idle_seconds())
                if idle >= int(self.delay_var.get()):
                    set_icons_visible(False)

        self.refresh_ui()

    def refresh_ui(self):
        vis = icons_visible()
        # 记住最近状态供下次启动恢复；启动恢复完成前不覆盖，否则会冲掉要恢复的目标
        if vis is not None and not self._restoring and self.cfg.get('last_visible') != vis:
            self.cfg['last_visible'] = vis
            self.widgets.queue_save()
        if self._ui_state == vis:
            return
        self._ui_state = vis
        if vis is None:
            self.state_label.config(text='无法获取桌面状态')
            return
        if vis:
            self.btn.config(text='立即隐藏', bg=BLUE, activebackground=BLUE_D)
            self.state_label.config(text='桌面图标显示中')
            self.dot_canvas.itemconfig(self.dot, fill=GREEN, outline=GREEN)
        else:
            self.btn.config(text='立即显示', bg=GREEN, activebackground=GREEN_D)
            self.state_label.config(text='桌面图标已隐藏 · 只剩壁纸')
            self.dot_canvas.itemconfig(self.dot, fill=GRAY, outline=GRAY)


def _signal_running_instance(mutex, event_names=(EVENT_NAME, LEGACY_EVENT_NAME)):
    """已有实例在运行：通知它显示设置窗口，然后本进程退出。"""
    existing_event = None
    for _ in range(20):
        for ev_name in event_names:
            existing_event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, ev_name)
            if existing_event:
                break
        if existing_event:
            break
        time.sleep(0.1)
    if mutex:
        kernel32.CloseHandle(mutex)
    if not existing_event:
        raise RuntimeError('Blanket 已经运行，但未能唤出设置。请从托盘退出旧版本后重试。')
    kernel32.SetEvent(existing_event)
    kernel32.CloseHandle(existing_event)


def main():
    legacy_mutex = kernel32.CreateMutexW(None, False, LEGACY_MUTEX_NAME)
    if legacy_mutex and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _signal_running_instance(legacy_mutex, (LEGACY_EVENT_NAME, EVENT_NAME))
        return
    if legacy_mutex:
        kernel32.CloseHandle(legacy_mutex)

    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _signal_running_instance(mutex)
        return

    # 主实例：创建自动复位的唤起事件（manualReset=False）
    show_event = kernel32.CreateEventW(None, False, False, EVENT_NAME)
    if not show_event:
        error = ctypes.get_last_error()
        kernel32.CloseHandle(mutex)
        raise ctypes.WinError(error)

    try:
        root = tk.Tk()
        guard = desktop_recovery.start()
        app = App(root, show_event)
        app.recovery_guard = guard

        root.update_idletasks()
        w = max(root.winfo_reqwidth(), round(300 * root.winfo_fpixels('1i') / 96))
        # 高度随内容自适应，确保所有选项都完整可见
        screen_h = root.winfo_screenheight()
        h = min(root.winfo_reqheight() + 8, screen_h - 40)
        x = (root.winfo_screenwidth() - w) // 2
        y = (screen_h - h) // 4
        root.geometry(f'{w}x{h}+{x}+{y}')
        root.mainloop()
    finally:
        kernel32.CloseHandle(show_event)
        kernel32.CloseHandle(mutex)


if __name__ == '__main__':
    main()
