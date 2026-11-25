"""桌面音乐胶囊与同步歌词专属设置窗口（从 media_widget.py 拆分）。"""
import tkinter as tk
from tkinter import colorchooser, font as tkfont, ttk

from lyrics_widget import LYRICS_LINE_OPTIONS, LYRICS_MODES


def _usable_fonts():
    skip_keywords = ('wingdings', 'webdings', 'marlett', 'mdl2', 'fluent icons', 'mt extra', '汉仪中黑')
    skip_exact = {'symbol', 'ms outlook', 'ms reference specialty', 'bookshelf symbol 7'}
    preferred = ['Microsoft YaHei UI', 'Segoe UI', 'Bahnschrift', 'SimHei', 'KaiTi', 'FangSong', 'Consolas', 'Arial']
    all_f = sorted({
        f for f in tkfont.families()
        if f and not f.startswith('@')
        and f.lower() not in skip_exact
        and not any(k in f.lower() for k in skip_keywords)
    })
    return [f for f in preferred if f in all_f] + [f for f in all_f if f not in preferred]


def open_media_settings(widget, media_defaults, media_styles, sanitize_fn):
    """打开音乐胶囊与同步歌词专属设置窗口（修改实时生效，附带歌词实时预览区）。"""
    if widget.dialog and widget.dialog.winfo_exists():
        widget.dialog.lift()
        return
    w = tk.Toplevel(widget.root)
    widget.dialog = w
    w.title('桌面音乐胶囊与歌词设置')
    ui = max(1.0, getattr(widget.widgets, 'ui_scale', 1.0))
    w.geometry(f'{round(520 * ui)}x{min(round(820 * ui), w.winfo_screenheight() - 60)}')
    w.minsize(470, 680)
    pane = ttk.Frame(w, padding=16)
    pane.pack(fill='both', expand=True)
    ttk.Label(pane, text='音乐胶囊与同步歌词 · 修改立即生效',
              font=('Microsoft YaHei UI', 12)).pack(anchor='w', pady=(0, 8))

    box = ttk.Frame(pane)
    box.pack(fill='x')
    vars_ = widget._dlg_vars = {}
    preview_overrides = {}
    preview_canvas = None

    def draw_lyrics_preview():
        if preview_canvas is None or not preview_canvas.winfo_exists():
            return
        p = {**widget.prefs, **preview_overrides}
        c = preview_canvas
        cw = max(240, c.winfo_width())
        ch = max(84, c.winfo_height())
        c.delete('all')

        cur = getattr(widget.lyrics, '_last_cur', '') or '这一句是当前播放的桌面歌词预览'
        nxt = getattr(widget.lyrics, '_last_nxt', '') or '下一句歌词将紧随其后柔和呈现'
        two_lines = p.get('lyrics_lines') == '双行'
        family = p.get('lyrics_font') or 'Microsoft YaHei UI'
        bold = bool(p.get('lyrics_bold', True))
        fg = p.get('lyrics_color') or p.get('fg') or '#F8FAFC'
        next_fg = p.get('lyrics_next_color') or '#CBD5E1'

        size = max(14, min(42, int(p.get('lyrics_size', 22))))
        sub_size = max(11, round(size * 0.72))
        weight = 'bold' if bold else 'normal'

        # 超出预览框尺寸时按比例自适应缩小，保证完整居中不裁切
        max_w, max_h = cw - 24, ch - 16
        for _ in range(2):
            mf = tkfont.Font(family=family, size=size, weight=weight)
            sf = tkfont.Font(family=family, size=sub_size)
            tw = max(mf.measure(cur), sf.measure(nxt) if two_lines else 0)
            th = mf.metrics('linespace') + (sf.metrics('linespace') + 4 if two_lines else 0)
            if tw <= max_w and th <= max_h:
                break
            scale = min(max_w / max(1, tw), max_h / max(1, th)) * 0.94
            size = max(10, int(size * scale))
            sub_size = max(8, int(sub_size * scale))
        else:
            mf = tkfont.Font(family=family, size=size, weight=weight)
            sf = tkfont.Font(family=family, size=sub_size)

        mh = mf.metrics('linespace')
        sh = sf.metrics('linespace') if two_lines else 0
        total_h = mh + (sh + 4 if two_lines else 0)
        y0 = (ch - total_h) / 2

        def draw_outlined(y, text, fnt, fill):
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
                c.create_text(cw / 2 + dx, y + dy, text=text, font=fnt, fill='#0b0f19', anchor='n')
            c.create_text(cw / 2, y, text=text, font=fnt, fill=fill, anchor='n')

        draw_outlined(y0, cur, mf, fg)
        if two_lines:
            draw_outlined(y0 + mh + 4, nxt, sf, next_fg)

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
        widget.lyrics.refresh_now()
        draw_lyrics_preview()
        widget.widgets.queue_save()

    def attach_hover_preview(cb, key):
        """下拉框鼠标滑过实时预览（90ms 防抖，仅刷新预览框，取消自动还原）。"""
        state = {'last': None, 'committed': False, 'bound': False, 'timer': None}

        def cancel_timer():
            if state['timer'] is not None:
                try:
                    w.after_cancel(state['timer'])
                except tk.TclError:
                    pass
                state['timer'] = None

        def do_preview(val):
            state['timer'] = None
            if val == state['last'] or not w.winfo_exists():
                return
            state['last'] = val
            preview_overrides[key] = val
            draw_lyrics_preview()

        def schedule_preview(val):
            if val == state['last']:
                return
            cancel_timer()
            state['timer'] = w.after(90, lambda v=val: do_preview(v))

        def on_selected(_e=None):
            cancel_timer()
            state['committed'] = True
            state['last'] = vars_[key].get()
            preview_overrides.pop(key, None)
            apply()

        cb.bind('<<ComboboxSelected>>', on_selected)

        def bind_popdown():
            cancel_timer()
            state['last'] = widget.prefs.get(key)
            state['committed'] = False

            def attach():
                if state['bound'] or not w.winfo_exists():
                    return
                try:
                    pop = cb.tk.call('ttk::combobox::PopdownWindow', cb)
                    lb_path = f'{pop}.f.l'
                    if not cb.tk.call('winfo', 'exists', lb_path):
                        return
                except tk.TclError:
                    return
                state['bound'] = True

                def on_motion_y(y_str):
                    try:
                        idx = int(cb.tk.call(lb_path, 'nearest', int(y_str)))
                        size = int(cb.tk.call(lb_path, 'size'))
                        if 0 <= idx < size:
                            schedule_preview(str(cb.tk.call(lb_path, 'get', idx)))
                    except (tk.TclError, ValueError):
                        pass

                def restore_if_uncommitted():
                    if not state['committed'] and w.winfo_exists():
                        preview_overrides.pop(key, None)
                        state['last'] = None
                        draw_lyrics_preview()

                def on_unmap():
                    cancel_timer()
                    if w.winfo_exists():
                        w.after(40, restore_if_uncommitted)

                motion_cb = w.register(on_motion_y)
                unmap_cb = w.register(on_unmap)
                cb.tk.call('bind', lb_path, '<Motion>', f'+{motion_cb} %y')
                cb.tk.call('bind', lb_path, '<Unmap>', f'+{unmap_cb}')

            w.after(15, attach)

        cb.configure(postcommand=bind_popdown)

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
        ('lyrics_font', '歌词字体', _usable_fonts()),
    ):
        var = vars_[key] = tk.StringVar(value=widget.prefs[key])
        cbox = ttk.Combobox(row(label), textvariable=var, values=values, state='readonly', width=22)
        cbox.pack(side='left')
        if key in ('lyrics_lines', 'lyrics_font'):
            attach_hover_preview(cbox, key)
        else:
            cbox.bind('<<ComboboxSelected>>', apply)

    lstyle = row('歌词文字')
    ttk.Button(lstyle, text='当前句颜色', command=lambda: pick_color('lyrics_color', '选择当前句歌词颜色')).pack(side='left', padx=3)
    ttk.Button(lstyle, text='下一句颜色', command=lambda: pick_color('lyrics_next_color', '选择下一句歌词颜色')).pack(side='left', padx=3)
    lbold = vars_['lyrics_bold'] = tk.BooleanVar(value=widget.prefs['lyrics_bold'])
    ttk.Checkbutton(lstyle, text='加粗', variable=lbold, command=apply).pack(side='left', padx=(8, 0))

    lsize = vars_['lyrics_size'] = tk.IntVar(value=widget.prefs['lyrics_size'])
    tk.Scale(row('歌词字号'), from_=14, to=42, orient='horizontal', variable=lsize,
             command=apply, showvalue=True, length=240).pack(side='left', fill='x', expand=True)

    loff = vars_['lyrics_offset'] = tk.DoubleVar(value=widget.prefs['lyrics_offset'])
    tk.Scale(row('歌词时间偏移(秒)'), from_=-3.0, to=3.0, resolution=0.2, orient='horizontal',
             variable=loff, command=apply, showvalue=True, length=240).pack(side='left', fill='x', expand=True)

    # 歌词实时预览区
    preview_canvas = tk.Canvas(pane, bg='#334155', highlightthickness=0, height=round(92 * ui))
    preview_canvas.pack(fill='both', expand=True, pady=(8, 4))
    preview_canvas.bind('<Configure>', lambda _e: draw_lyrics_preview())
    w.after(20, draw_lyrics_preview)

    ttk.Label(pane, text='提示：Ctrl+滚轮调大小 · Shift+滚轮调透明度 · 双击锁定展开 · 独立歌词条可直接拖动',
              foreground='#64748B').pack(anchor='w', pady=(4, 8))

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
