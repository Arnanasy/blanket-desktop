"""通用界面辅助工具：可用字体筛选、全局复选框对勾样式与下拉框（ttk.Combobox）悬停防抖实时预览。"""
import tkinter as tk
from tkinter import font as tkfont

try:
    from PIL import Image, ImageDraw, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

_SKIP_FONT_KEYWORDS = ('wingdings', 'webdings', 'marlett', 'mdl2', 'fluent icons', 'mt extra', '汉仪中黑')
_SKIP_FONT_EXACT = {'symbol', 'ms outlook', 'ms reference specialty', 'bookshelf symbol 7'}
_DEFAULT_PREFERRED = (
    'Bahnschrift', 'Segoe UI Variable Display', 'Segoe UI',
    'Microsoft YaHei UI', 'SimHei', 'KaiTi', 'FangSong', 'Consolas', 'Arial',
)


def _hex_rgba(color, alpha=255):
    s = color.lstrip('#')
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)


def _make_checkbox_image(root, size, checked, border_hex, fill_hex, check_hex):
    """生成带右侧留白的圆角复选框图标（开启为 ✓，关闭为空白框 □；有 Pillow 时 2x 超采样抗锯齿）。"""
    box = max(12, int(size))
    gap = max(4, round(box * 0.35))
    total_w = box + gap
    if _HAS_PIL:
        ss = 2
        sw, sh = total_w * ss, box * ss
        bs = box * ss
        im = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)
        rad = max(2 * ss, round(bs * 0.22))
        bw = max(ss, round(bs * 0.085))
        pad = ss // 2
        rect = (pad, pad, bs - 1 - pad, bs - 1 - pad)
        draw.rounded_rectangle(rect, radius=rad, fill=_hex_rgba(fill_hex), outline=_hex_rgba(border_hex), width=bw)
        if checked:
            # 清晰干脆的对勾 ✓（左短折线 + 右上长折线）
            p1 = (round(bs * 0.22), round(bs * 0.52))
            p2 = (round(bs * 0.43), round(bs * 0.72))
            p3 = (round(bs * 0.78), round(bs * 0.30))
            lw = max(2 * ss, round(bs * 0.13))
            chk = _hex_rgba(check_hex)
            draw.line([p1, p2, p3], fill=chk, width=lw, joint='curve')
            # 补圆头让折点与端点更圆润
            r_cap = max(1, lw // 2)
            for px, py in (p1, p2, p3):
                draw.ellipse((px - r_cap, py - r_cap, px + r_cap, py + r_cap), fill=chk)
        im = im.resize((total_w, box), Image.LANCZOS)
        return ImageTk.PhotoImage(im, master=root)

    # 无 Pillow 纯标准库回退：使用 tk.PhotoImage 像素绘出清晰方框与 ✓
    img = tk.PhotoImage(master=root, width=total_w, height=box)
    img.put(fill_hex, to=(1, 1, box - 1, box - 1))
    img.put(border_hex, to=(0, 0, box, 1))
    img.put(border_hex, to=(0, box - 1, box, box))
    img.put(border_hex, to=(0, 0, 1, box))
    img.put(border_hex, to=(box - 1, 0, box, box))
    if checked:
        # 14x14 基准折线按比例映射到任意 box 尺寸
        pts = []
        x1, y1 = int(box * 0.22), int(box * 0.52)
        x2, y2 = int(box * 0.43), int(box * 0.72)
        x3, y3 = int(box * 0.78), int(box * 0.28)
        steps1 = max(1, x2 - x1)
        for i in range(steps1 + 1):
            pts.append((x1 + i, round(y1 + (y2 - y1) * i / steps1)))
        steps2 = max(1, x3 - x2)
        for i in range(steps2 + 1):
            pts.append((x2 + i, round(y2 + (y3 - y2) * i / steps2)))
        for px, py in pts:
            for dy in (0, 1):
                if 1 <= px < box - 1 and 1 <= py + dy < box - 1:
                    img.put(check_hex, to=(px, py + dy, px + 1, py + dy + 1))
    return img


def install_checkmark_style(root, style, dark=False, ui_scale=1.0):
    """将 clam 主题下反直觉的 [×] 复选框全局替换为标准圆角 [✓] / 空白框 [□]（同步适配深浅主题与高 DPI）。"""
    mode = 'dark' if dark else 'light'
    elem_name = f'Blanket.Checkbutton.indicator.{mode}'
    cache = getattr(root, '_blanket_chk_cache', None)
    if cache is None:
        cache = root._blanket_chk_cache = {}

    if mode not in cache:
        size = max(14, round(15 * max(1.0, float(ui_scale))))
        if dark:
            off_border, off_fill = '#64748B', '#1E293B'
            off_hover_border, off_hover_fill = '#38BDF8', '#263349'
            on_border, on_fill = '#2563EB', '#2563EB'
            on_hover_border, on_hover_fill = '#3B82F6', '#3B82F6'
            dis_border, dis_fill = '#475569', '#1E293B'
            dis_on_fill = '#475569'
            chk_fg = '#FFFFFF'
        else:
            off_border, off_fill = '#94A3B8', '#FFFFFF'
            off_hover_border, off_hover_fill = '#2563EB', '#EFF6FF'
            on_border, on_fill = '#2563EB', '#2563EB'
            on_hover_border, on_hover_fill = '#1D4ED8', '#1D4ED8'
            dis_border, dis_fill = '#CBD5E1', '#F1F5F9'
            dis_on_fill = '#94A3B8'
            chk_fg = '#FFFFFF'

        imgs = {
            'off': _make_checkbox_image(root, size, False, off_border, off_fill, chk_fg),
            'off_hover': _make_checkbox_image(root, size, False, off_hover_border, off_hover_fill, chk_fg),
            'on': _make_checkbox_image(root, size, True, on_border, on_fill, chk_fg),
            'on_hover': _make_checkbox_image(root, size, True, on_hover_border, on_hover_fill, chk_fg),
            'off_dis': _make_checkbox_image(root, size, False, dis_border, dis_fill, chk_fg),
            'on_dis': _make_checkbox_image(root, size, True, dis_border, dis_on_fill, chk_fg),
        }
        cache[mode] = imgs

        if elem_name not in style.element_names():
            style.element_create(
                elem_name, 'image', imgs['off'],
                ('selected', 'disabled', imgs['on_dis']),
                ('disabled', imgs['off_dis']),
                ('selected', 'active', imgs['on_hover']),
                ('selected', imgs['on']),
                ('active', imgs['off_hover']),
                sticky='',
            )

    style.layout('TCheckbutton', [
        ('Checkbutton.padding', {'sticky': 'nswe', 'children': [
            (elem_name, {'side': 'left', 'sticky': ''}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                ('Checkbutton.label', {'sticky': 'nswe'}),
            ]}),
        ]}),
    ])
    return elem_name


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
