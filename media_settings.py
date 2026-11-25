"""桌面音乐胶囊与同步歌词专属设置窗口（从 media_widget.py 拆分）。"""
import tkinter as tk
from tkinter import colorchooser, ttk

from lyrics_widget import LYRICS_LINE_OPTIONS, LYRICS_MODES


def open_media_settings(widget, media_defaults, media_styles, sanitize_fn):
    """打开音乐胶囊与同步歌词专属设置窗口（修改实时生效）。"""
    if widget.dialog and widget.dialog.winfo_exists():
        widget.dialog.lift()
        return
    w = tk.Toplevel(widget.root)
    widget.dialog = w
    w.title('桌面音乐胶囊与歌词设置')
    ui = max(1.0, getattr(widget.widgets, 'ui_scale', 1.0))
    w.geometry(f'{round(500 * ui)}x{min(round(680 * ui), w.winfo_screenheight() - 60)}')
    w.minsize(450, 580)
    pane = ttk.Frame(w, padding=16)
    pane.pack(fill='both', expand=True)
    ttk.Label(pane, text='音乐胶囊与同步歌词 · 修改立即生效',
              font=('Microsoft YaHei UI', 12)).pack(anchor='w', pady=(0, 8))

    box = ttk.Frame(pane)
    box.pack(fill='x')
    vars_ = widget._dlg_vars = {}

    def apply(*_args):
        for k, var in vars_.items():
            if k == 'locked':
                widget.locked = bool(var.get())
                widget._start_anim(1.0 if widget.locked or widget._hover else 0.0)
            else:
                widget.prefs[k] = var.get()
        widget.prefs = sanitize_fn(widget.prefs)
        widget._sync_audio_monitor()
        widget._render()
        widget.relayout(on_desktop=True)
        widget.widgets.queue_save()

    def row(label):
        f = ttk.Frame(box)
        f.pack(fill='x', pady=3)
        ttk.Label(f, text=label, width=15).pack(side='left')
        return f

    for key, label, lo, hi in (
        ('scale', '胶囊大小(%)', 80, 220),
        ('opacity', '不透明度(%)', 35, 100),
        ('radius', '圆角半径', 4, 32),
        ('pause_hide', '暂停隐藏(秒,0不藏)', 0, 60),
    ):
        var = vars_[key] = tk.IntVar(value=widget.prefs[key])
        tk.Scale(row(label), from_=lo, to=hi, orient='horizontal', variable=var,
                 command=apply, showvalue=True, length=240).pack(side='left', fill='x', expand=True)

    style_var = vars_['style'] = tk.StringVar(value=widget.prefs['style'])
    cb = ttk.Combobox(row('卡片样式'), textvariable=style_var, values=list(media_styles),
                      state='readonly', width=22)
    cb.pack(side='left')
    cb.bind('<<ComboboxSelected>>', apply)

    def pick_color(key, title):
        color = colorchooser.askcolor(widget.prefs[key], parent=w, title=title)[1]
        if color:
            widget.prefs[key] = color
            apply()

    colors = row('颜色')
    ttk.Button(colors, text='背景色', command=lambda: pick_color('bg', '选择背景色')).pack(side='left', padx=3)
    ttk.Button(colors, text='文字色', command=lambda: pick_color('fg', '选择文字色')).pack(side='left', padx=3)
    ttk.Button(colors, text='强调色', command=lambda: pick_color('accent', '选择强调色')).pack(side='left', padx=3)

    ttk.Separator(box, orient='horizontal').pack(fill='x', pady=8)

    l_checks = ttk.Frame(box)
    l_checks.pack(fill='x', pady=2)
    lyr_en = vars_['lyrics_enabled'] = tk.BooleanVar(value=widget.prefs['lyrics_enabled'])
    ttk.Checkbutton(l_checks, text='启用桌面同步歌词', variable=lyr_en, command=apply).pack(side='left', padx=(0, 14))
    lock_var = vars_['locked'] = tk.BooleanVar(value=widget.locked)
    ttk.Checkbutton(l_checks, text='胶囊常驻展开', variable=lock_var, command=apply).pack(side='left')

    a_checks = ttk.Frame(box)
    a_checks.pack(fill='x', pady=(2, 4))
    audio_var = vars_['audio_assist'] = tk.BooleanVar(value=widget.prefs['audio_assist'])
    ttk.Checkbutton(
        a_checks,
        text='启用音频监听辅助（无系统媒体信息时按真实声音自动显隐）',
        variable=audio_var,
        command=apply,
    ).pack(side='left')

    for key, label, values in (
        ('lyrics_mode', '歌词位置', list(LYRICS_MODES)),
        ('lyrics_lines', '歌词行数', list(LYRICS_LINE_OPTIONS)),
    ):
        var = vars_[key] = tk.StringVar(value=widget.prefs[key])
        cbox = ttk.Combobox(row(label), textvariable=var, values=values, state='readonly', width=22)
        cbox.pack(side='left')
        cbox.bind('<<ComboboxSelected>>', apply)

    lsize = vars_['lyrics_size'] = tk.IntVar(value=widget.prefs['lyrics_size'])
    tk.Scale(row('歌词字号'), from_=14, to=42, orient='horizontal', variable=lsize,
             command=apply, showvalue=True, length=240).pack(side='left', fill='x', expand=True)

    loff = vars_['lyrics_offset'] = tk.DoubleVar(value=widget.prefs['lyrics_offset'])
    tk.Scale(row('歌词时间偏移(秒)'), from_=-3.0, to=3.0, resolution=0.2, orient='horizontal',
             variable=loff, command=apply, showvalue=True, length=240).pack(side='left', fill='x', expand=True)

    ttk.Label(pane, text='提示：Ctrl+滚轮调大小 · 滚轮调透明度 · 双击锁定展开 · 独立歌词条可直接拖动',
              foreground='#64748B').pack(anchor='w', pady=(8, 10))

    btns = ttk.Frame(pane)
    btns.pack(fill='x')

    def reset_defaults():
        widget.prefs = media_defaults.copy()
        for k, var in vars_.items():
            if k in widget.prefs:
                var.set(widget.prefs[k])
        apply()

    ttk.Button(btns, text='恢复默认', command=reset_defaults).pack(side='left')
    ttk.Button(btns, text='打开主设置', command=widget.on_settings).pack(side='left', padx=8)
    ttk.Button(btns, text='完成', command=w.destroy).pack(side='right')
