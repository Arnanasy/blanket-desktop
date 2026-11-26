"""通用界面辅助工具：可用字体筛选与下拉框（ttk.Combobox）悬停防抖实时预览。"""
import tkinter as tk
from tkinter import font as tkfont

_SKIP_FONT_KEYWORDS = ('wingdings', 'webdings', 'marlett', 'mdl2', 'fluent icons', 'mt extra', '汉仪中黑')
_SKIP_FONT_EXACT = {'symbol', 'ms outlook', 'ms reference specialty', 'bookshelf symbol 7'}
_DEFAULT_PREFERRED = (
    'Bahnschrift', 'Segoe UI Variable Display', 'Segoe UI',
    'Microsoft YaHei UI', 'SimHei', 'KaiTi', 'FangSong', 'Consolas', 'Arial',
)


def usable_fonts(preferred=_DEFAULT_PREFERRED):
    """返回排除 Windows 竖排专用（@ 开头）、纯符号图标及排版异常字体后的有序字体列表。"""
    def _ok(name):
        if not name or name.startswith('@'):
            return False
        low = name.lower()
        return low not in _SKIP_FONT_EXACT and not any(k in low for k in _SKIP_FONT_KEYWORDS)

    all_families = sorted({f for f in tkfont.families() if _ok(f)})
    pref_list = list(preferred or ())
    return [f for f in pref_list if f in all_families] + [f for f in all_families if f not in pref_list]


def attach_hover_preview(window, combobox, get_current, on_preview, on_restore, on_commit, delay_ms=90):
    """为 ttk.Combobox 挂载下拉列表鼠标滑过防抖实时预览（取消选择收起时自动还原）。"""
    state = {'last': None, 'committed': False, 'bound': False, 'timer': None}

    def cancel_timer():
        if state['timer'] is not None:
            try:
                window.after_cancel(state['timer'])
            except tk.TclError:
                pass
            state['timer'] = None

    def fire_preview(val):
        state['timer'] = None
        if val == state['last'] or not window.winfo_exists():
            return
        state['last'] = val
        on_preview(val)

    def schedule_preview(val):
        if val == state['last']:
            return
        cancel_timer()
        state['timer'] = window.after(delay_ms, lambda v=val: fire_preview(v))

    def on_selected(_e=None):
        cancel_timer()
        state['committed'] = True
        on_commit()
        state['last'] = get_current()

    combobox.bind('<<ComboboxSelected>>', on_selected)

    def bind_popdown():
        cancel_timer()
        state['last'] = get_current()
        state['committed'] = False

        def attach():
            if state['bound'] or not window.winfo_exists():
                return
            try:
                pop = combobox.tk.call('ttk::combobox::PopdownWindow', combobox)
                lb_path = f'{pop}.f.l'
                if not combobox.tk.call('winfo', 'exists', lb_path):
                    return
            except tk.TclError:
                return
            state['bound'] = True

            def on_motion_y(y_str):
                try:
                    idx = int(combobox.tk.call(lb_path, 'nearest', int(y_str)))
                    size = int(combobox.tk.call(lb_path, 'size'))
                    if 0 <= idx < size:
                        schedule_preview(str(combobox.tk.call(lb_path, 'get', idx)))
                except (tk.TclError, ValueError):
                    pass

            def restore_if_uncommitted():
                if not state['committed'] and window.winfo_exists():
                    state['last'] = None
                    on_restore()

            def on_unmap():
                cancel_timer()
                if window.winfo_exists():
                    window.after(40, restore_if_uncommitted)

            motion_cb = window.register(on_motion_y)
            unmap_cb = window.register(on_unmap)
            combobox.tk.call('bind', lb_path, '<Motion>', f'+{motion_cb} %y')
            combobox.tk.call('bind', lb_path, '<Unmap>', f'+{unmap_cb}')

        window.after(15, attach)

    combobox.configure(postcommand=bind_popdown)
