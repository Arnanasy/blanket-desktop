"""自动化（日夜主题调度、闲置压暗挂件）与全局快捷键设置窗口。"""
import tkinter as tk
from tkinter import ttk

from hotkeys import (DEFAULT_HOTKEYS, HOTKEY_ACTIONS, HOTKEY_IDS, HOTKEY_LABELS,
                     format_hotkey_from_vk, sanitize_hotkeys)
from theme_schedule import normalize_hhmm, resolve_schedule_state
from win32 import VK_CONTROL, VK_LWIN, VK_MENU, VK_RWIN, VK_SHIFT, key_down


def open_automation_dialog(app):
    """打开或前置「自动日夜、闲置压暗与快捷键」设置弹窗。"""
    existing = getattr(app, 'automation_dialog', None)
    if existing and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        return existing

    w = tk.Toplevel(app.root)
    app.automation_dialog = w
    w.title('自动护眼与全局快捷键')
    w.resizable(False, False)
    w.transient(app.root)

    pane = ttk.Frame(w, padding=16)
    pane.pack(fill='both', expand=True)

    # ------------------------------------------------------------ 1. 自动日夜切换
    sched = app.theme_schedule
    ttk.Label(pane, text='自动切换 Windows 深色 / 浅色', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w')

    sched_enabled = tk.BooleanVar(value=bool(sched.get('enabled', False)))
    sched_mode = tk.StringVar(value=sched.get('mode', 'fixed'))
    light_var = tk.StringVar(value=sched.get('light_time', '07:00'))
    dark_var = tk.StringVar(value=sched.get('dark_time', '19:00'))
    sched_status_var = tk.StringVar(value='')

    ttk.Checkbutton(pane, text='启用定时自动切换深浅色（跨过切换点生效，不覆盖中途手动切换）',
                    variable=sched_enabled, command=lambda: commit_schedule(apply_now=True)).pack(anchor='w', pady=(6, 4))

    mode_row = ttk.Frame(pane)
    mode_row.pack(fill='x', pady=2)
    ttk.Label(mode_row, text='切换方式：', width=10).pack(side='left')
    ttk.Radiobutton(mode_row, text='固定时间段', variable=sched_mode, value='fixed',
                    command=lambda: commit_schedule(apply_now=True)).pack(side='left', padx=(0, 12))
    ttk.Radiobutton(mode_row, text='跟随当地日出日落', variable=sched_mode, value='solar',
                    command=lambda: commit_schedule(apply_now=True, probe_loc=True)).pack(side='left')

    time_row = ttk.Frame(pane)
    time_row.pack(fill='x', pady=4)
    ttk.Label(time_row, text='浅色 / 备用：', width=10).pack(side='left')
    light_entry = ttk.Entry(time_row, textvariable=light_var, width=8)
    light_entry.pack(side='left')
    ttk.Label(time_row, text='  深色 / 备用：').pack(side='left')
    dark_entry = ttk.Entry(time_row, textvariable=dark_var, width=8)
    dark_entry.pack(side='left')

    loc_row = ttk.Frame(pane)
    loc_row.pack(fill='x', pady=(2, 8))
    ttk.Label(loc_row, textvariable=sched_status_var).pack(side='left', fill='x', expand=True)
    loc_btn = ttk.Button(loc_row, text='刷新定位', command=lambda: app.refresh_solar_location(on_done=refresh_status))
    loc_btn.pack(side='right')

    def refresh_status():
        if not w.winfo_exists():
            return
        weather_cache = getattr(app, 'widgets', None) and app.widgets.weather
        st = resolve_schedule_state(app.theme_schedule, weather_cache=weather_cache)
        place = app.theme_schedule.get('solar_place') or (weather_cache or {}).get('place') or ''
        if app.theme_schedule['mode'] == 'solar':
            loc_btn.state(['!disabled'])
            if st['using_solar']:
                prefix = f'{place} · ' if place else ''
                sched_status_var.set(f"{prefix}今日日出 {st['light_hhmm']} · 日落 {st['dark_hhmm']}")
            else:
                sched_status_var.set(f"暂未获取到城市坐标，先按备用时间 {st['light_hhmm']} / {st['dark_hhmm']} 切换")
        else:
            loc_btn.state(['disabled'])
            sched_status_var.set(f"当前按固定时间：浅色 {st['light_hhmm']} · 深色 {st['dark_hhmm']}")

    def commit_schedule(_evt=None, apply_now=False, probe_loc=False):
        cur_sched = getattr(app, 'theme_schedule', {})
        norm_light = normalize_hhmm(light_var.get(), cur_sched.get('light_time', '07:00'))
        norm_dark = normalize_hhmm(dark_var.get(), cur_sched.get('dark_time', '19:00'))
        light_var.set(norm_light)
        dark_var.set(norm_dark)
        app.update_theme_schedule(
            enabled=sched_enabled.get(),
            mode=sched_mode.get(),
            light_time=norm_light,
            dark_time=norm_dark,
            apply_now=apply_now,
            probe_loc=probe_loc,
            on_loc=refresh_status,
        )
        refresh_status()

    for entry in (light_entry, dark_entry):
        entry.bind('<Return>', lambda e: commit_schedule(apply_now=True))
        entry.bind('<FocusOut>', lambda e: commit_schedule(apply_now=False))

    ttk.Separator(pane, orient='horizontal').pack(fill='x', pady=6)

    # ------------------------------------------------------------ 2. 电脑闲置自动压暗
    dim = app.idle_dim
    ttk.Label(pane, text='电脑闲置自动压暗（桌面时钟与系统监控）', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w')
    dim_enabled = tk.BooleanVar(value=bool(dim.get('enabled', False)))
    dim_mins = tk.IntVar(value=int(dim.get('minutes', 5)))
    dim_op = tk.IntVar(value=int(dim.get('opacity', 35)))

    def commit_dim(*_args):
        try:
            mins = max(1, min(60, int(dim_mins.get())))
        except (tk.TclError, ValueError):
            mins = 5
        try:
            op = max(15, min(80, int(dim_op.get())))
        except (tk.TclError, ValueError):
            op = 35
        app.update_idle_dim(enabled=dim_enabled.get(), minutes=mins, opacity=op)

    ttk.Checkbutton(pane, text='无键鼠操作超过设定时间后自动压暗（动鼠标立即恢复）',
                    variable=dim_enabled, command=commit_dim).pack(anchor='w', pady=(6, 4))

    dim_row = ttk.Frame(pane)
    dim_row.pack(fill='x', pady=(0, 6))
    ttk.Label(dim_row, text='闲置分钟：').pack(side='left')
    mins_sb = ttk.Spinbox(dim_row, from_=1, to=60, textvariable=dim_mins, width=5, command=commit_dim)
    mins_sb.pack(side='left', padx=(0, 14))
    ttk.Label(dim_row, text='压暗透明度(%)：').pack(side='left')
    op_sb = ttk.Spinbox(dim_row, from_=15, to=80, increment=5, textvariable=dim_op, width=5, command=commit_dim)
    op_sb.pack(side='left')
    for sb in (mins_sb, op_sb):
        sb.bind('<Return>', commit_dim)
        sb.bind('<FocusOut>', commit_dim)

    wake_move_var = tk.BooleanVar(value=bool(getattr(app, 'cfg', {}).get('screen_off_wake_move', False)))
    ttk.Checkbutton(
        pane,
        text='一键息屏后允许大幅晃动鼠标唤醒（默认仅点击鼠标或按键盘唤醒，最防误触）',
        variable=wake_move_var,
        command=lambda: app.update_idle_dim(wake_on_move=wake_move_var.get()),
    ).pack(anchor='w', pady=(0, 8))

    ttk.Separator(pane, orient='horizontal').pack(fill='x', pady=6)

    # ------------------------------------------------------------ 3. 全局快捷键
    ttk.Label(pane, text='全局快捷键（点输入框直接按组合键录入，BackSpace 清空）',
              font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w')

    hk_notice_var = tk.StringVar(value='')
    hk_vars = {}
    hk_status_vars = {}
    hk_entries = {}

    def refresh_hotkey_statuses():
        conflicts = getattr(app.tray, 'hotkey_conflicts', {})
        registered = getattr(app.tray, 'hotkeys_registered', set())
        for action in HOTKEY_ACTIONS:
            spec = app.hotkeys.get(action, '')
            hk_vars[action].set(spec or '未设置')
            if not spec:
                hk_status_vars[action].set('未启用')
            elif action in conflicts:
                hk_status_vars[action].set('⚠ 已被其他程序占用')
            elif HOTKEY_IDS[action] in registered:
                hk_status_vars[action].set('✓ 已生效')
            else:
                hk_status_vars[action].set('轮询监听')

    def set_action_hotkey(action, new_spec):
        if new_spec:
            for other in HOTKEY_ACTIONS:
                if other != action and app.hotkeys.get(other) == new_spec:
                    hk_notice_var.set(f'组合键 {new_spec} 已被「{HOTKEY_LABELS[other]}」使用，请先清空或换一个组合')
                    refresh_hotkey_statuses()
                    return
        hk_notice_var.set('')
        updated = dict(app.hotkeys)
        updated[action] = new_spec
        app.update_hotkeys(sanitize_hotkeys(updated), notify_conflict=False)
        refresh_hotkey_statuses()

    def _begin_capture(action):
        if hasattr(app.tray, 'suspend_hotkeys'):
            app.tray.suspend_hotkeys()
        hk_vars[action].set('请按下组合键…')

    def _end_capture():
        if hasattr(app.tray, 'resume_hotkeys'):
            app.tray.resume_hotkeys()
        refresh_hotkey_statuses()

    def on_key_capture(action, event):
        keysym = event.keysym or ''
        if keysym in ('Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Shift_L', 'Shift_R', 'Win_L', 'Win_R'):
            return 'break'
        ctrl = key_down(VK_CONTROL) or bool(event.state & 0x4)
        alt = key_down(VK_MENU) or bool(event.state & 0x20000)
        shift = key_down(VK_SHIFT) or bool(event.state & 0x1)
        win = key_down(VK_LWIN) or key_down(VK_RWIN)

        if not (ctrl or alt or shift or win):
            if keysym in ('BackSpace', 'Delete'):
                set_action_hotkey(action, '')
                w.focus_set()
                return 'break'
            if keysym == 'Escape':
                hk_notice_var.set('')
                _end_capture()
                w.focus_set()
                return 'break'

        formatted = format_hotkey_from_vk(ctrl=ctrl, alt=alt, shift=shift, win=win,
                                          vk=getattr(event, 'keycode', 0), keysym=keysym)
        if not formatted:
            hk_notice_var.set('请使用「Ctrl/Alt/Shift/Win + 字母/数字/功能键」，或单独按 F1–F12')
            return 'break'
        set_action_hotkey(action, formatted)
        w.focus_set()
        return 'break'

    for action in HOTKEY_ACTIONS:
        row = ttk.Frame(pane)
        row.pack(fill='x', pady=3)
        ttk.Label(row, text=HOTKEY_LABELS[action], width=14).pack(side='left')
        var = hk_vars[action] = tk.StringVar(value=app.hotkeys.get(action, '') or '未设置')
        ent = ttk.Entry(row, textvariable=var, width=18, justify='center')
        ent.pack(side='left', padx=(0, 6))
        ent.bind('<KeyPress>', lambda e, a=action: on_key_capture(a, e))
        ent.bind('<FocusIn>', lambda _e, a=action: _begin_capture(a))
        ent.bind('<FocusOut>', lambda _e: _end_capture())
        hk_entries[action] = ent

        ttk.Button(row, text='清空', width=5, command=lambda a=action: set_action_hotkey(a, '')).pack(side='left', padx=(0, 8))
        st_var = hk_status_vars[action] = tk.StringVar(value='')
        ttk.Label(row, textvariable=st_var, width=18).pack(side='left')

    ttk.Label(pane, textvariable=hk_notice_var).pack(anchor='w', pady=(4, 4))

    footer = ttk.Frame(pane)
    footer.pack(fill='x', pady=(6, 0))

    def reset_default_hotkeys():
        hk_notice_var.set('')
        app.update_hotkeys(dict(DEFAULT_HOTKEYS), notify_conflict=False)
        refresh_hotkey_statuses()

    def close_dialog():
        if hasattr(app.tray, 'resume_hotkeys'):
            app.tray.resume_hotkeys()
        commit_schedule(apply_now=False)
        commit_dim()
        if w.winfo_exists():
            w.destroy()

    w.bind('<Destroy>', lambda e: app.tray.resume_hotkeys()
           if (e.widget is w and hasattr(app.tray, 'resume_hotkeys')) else None, add='+')
    w.protocol('WM_DELETE_WINDOW', close_dialog)
    ttk.Button(footer, text='恢复默认热键', command=reset_default_hotkeys).pack(side='left')
    ttk.Button(footer, text='完成（已自动保存）', command=close_dialog).pack(side='right')

    refresh_status()
    refresh_hotkey_statuses()
    return w
