"""桌面音乐胶囊的绘制与图像渲染模块（从 media_widget.py 拆分以保持单文件简洁）。"""
from io import BytesIO

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

KEY_COLOR = '#010203'
CARD_BORDER = '#334155'
TEXT_SECONDARY = '#94A3B8'
ACCENT_PAUSE = '#94A3B8'


def hex_to_rgba(hex_color, alpha=255):
    s = hex_color.lstrip('#')
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)


def fmt_mmss(sec):
    s = max(0, int(sec))
    return f'{s // 60:02d}:{s % 60:02d}'


def make_card_bg(w, h, radius, bg_hex, border_hex, style):
    """用 Pillow 2x 超采样绘制锐利抗锯齿 Fluent 圆角卡片背景。"""
    if not HAS_PIL or style == '极简透明' or w < 10 or h < 10:
        return None
    try:
        ss = 2
        sw, sh = w * ss, h * ss
        rad = 0 if style == '方形卡片' else max(0, min(int(radius * ss), min(sw, sh) // 2))
        im = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)
        fill = hex_to_rgba(bg_hex, 255)
        outline = hex_to_rgba(border_hex, 255)
        if rad > 0:
            draw.rounded_rectangle((1, 1, sw - 2, sh - 2), radius=rad, fill=fill, outline=outline, width=ss)
        else:
            draw.rectangle((1, 1, sw - 2, sh - 2), fill=fill, outline=outline, width=ss)
        im = im.resize((w, h), Image.LANCZOS)
        return ImageTk.PhotoImage(im)
    except Exception:
        return None


def make_rounded_cover(raw_bytes, size, radius=10):
    if not HAS_PIL or not raw_bytes:
        return None
    try:
        size = max(24, int(size))
        radius = max(0, min(int(radius), size // 2))
        im = Image.open(BytesIO(raw_bytes)).convert('RGBA')
        w, h = im.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        im = im.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
        mask = Image.new('L', (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
        out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        out.paste(im, (0, 0), mask)
        return ImageTk.PhotoImage(out)
    except Exception:
        return None


def draw_rounded_rect(c, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    if r <= 1:
        return c.create_rectangle(x1, y1, x2, y2, **kw)
    pts = (
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    )
    return c.create_polygon(pts, smooth=True, splinesteps=20, **kw)


def draw_vector_icon(c, action, cx, cy, r, fill, playing=False, locked=False):
    """纯矢量绘制媒体控制图标，任意缩放下都居中锐利对称。"""
    if action == 'toggle':
        if playing:
            bar_w = max(2, round(r * 0.32))
            gap = max(2, round(r * 0.24))
            hh = max(4, round(r * 0.72))
            c.create_rectangle(cx - gap - bar_w, cy - hh, cx - gap, cy + hh, fill=fill, outline='')
            c.create_rectangle(cx + gap, cy - hh, cx + gap + bar_w, cy + hh, fill=fill, outline='')
        else:
            off = max(1, round(r * 0.14))
            c.create_polygon(
                cx - r * 0.48 + off, cy - r * 0.72,
                cx - r * 0.48 + off, cy + r * 0.72,
                cx + r * 0.78 + off, cy,
                fill=fill, outline='',
            )
    elif action == 'prev':
        bar_w = max(2, round(r * 0.22))
        hh = max(4, round(r * 0.62))
        c.create_rectangle(cx - r * 0.68, cy - hh, cx - r * 0.68 + bar_w, cy + hh, fill=fill, outline='')
        c.create_polygon(cx - r * 0.38, cy, cx + r * 0.62, cy - hh, cx + r * 0.62, cy + hh, fill=fill, outline='')
    elif action == 'next':
        bar_w = max(2, round(r * 0.22))
        hh = max(4, round(r * 0.62))
        c.create_polygon(cx + r * 0.38, cy, cx - r * 0.62, cy - hh, cx - r * 0.62, cy + hh, fill=fill, outline='')
        c.create_rectangle(cx + r * 0.68 - bar_w, cy - hh, cx + r * 0.68, cy + hh, fill=fill, outline='')
    elif action == 'pin':
        pr = max(3, round(r * 0.42))
        if locked:
            c.create_oval(cx - pr, cy - pr, cx + pr, cy + pr, fill=fill, outline='')
        else:
            c.create_oval(cx - pr, cy - pr, cx + pr, cy + pr, fill='', outline=fill, width=max(1.5, r * 0.15))


def _draw_text(c, style, x, y, text, font_obj, fill, anchor='nw'):
    if style == '极简透明':
        c.create_text(x + 1, y + 1, anchor=anchor, text=text, fill='#0b0f19', font=font_obj)
    c.create_text(x, y, anchor=anchor, text=text, fill=fill, font=font_obj)


def _draw_progress_bar(widget, c, rx, cy, w, right_pad, cover, content_w, style, fg, accent):
    bar_y = cy + round(cover * 0.59)
    bar_h = max(3, round(cover * 0.045))
    pos_s = (widget._seek_preview if widget._seeking and widget._seek_preview is not None
             else widget.worker.estimated_position())
    dur_s = widget._state.get('duration', 0.0)
    ratio = max(0.0, min(1.0, pos_s / dur_s)) if dur_s > 0 else 0.0
    time_str = f'{fmt_mmss(pos_s)} / {fmt_mmss(dur_s)}' if dur_s > 0 else ''
    time_w = (widget._time_font.measure(time_str) + max(8, round(cover * 0.12))) if time_str else 0
    bar_w = max(28, content_w - time_w)
    x0, x1 = rx, rx + bar_w
    track_color = '#475569' if style == '极简透明' else '#334155'
    c.create_line(x0, bar_y, x1, bar_y, fill=track_color, width=bar_h, capstyle='round')
    fill_x = x0 + round(bar_w * ratio)
    if ratio > 0.005:
        c.create_line(x0, bar_y, max(x0 + 1, fill_x), bar_y, fill=accent, width=bar_h, capstyle='round')
    if widget._seeking or widget._hover_btn == 'bar':
        knob_r = max(4, round(bar_h * 1.35))
        c.create_oval(fill_x - knob_r, bar_y - knob_r, fill_x + knob_r, bar_y + knob_r,
                      fill=fg, outline=accent, width=1.5)
    if dur_s > 0:
        hit_pad = max(7, round(cover * 0.10))
        widget._bar_rect = (x0 - 4, bar_y - hit_pad, x1 + 4, bar_y + hit_pad, x0, x1)
    if time_str:
        _draw_text(c, style, w - right_pad, bar_y, time_str,
                   widget._time_font, '#CBD5E1' if style == '极简透明' else TEXT_SECONDARY, anchor='e')


def _draw_controls(widget, c, rx, cy, cover, content_w, style, fg, accent):
    ctrl_y = cy + round(cover * 0.85)
    mid_x = rx + content_w // 2
    gap_x = max(28, round(cover * 0.42))
    main_r = max(11, round(cover * 0.155))
    sub_r = max(10, round(cover * 0.135))
    icon_r = max(5.5, round(cover * 0.085))
    playing = bool(widget._state.get('playing'))

    if style == '极简透明':
        if widget._hover_btn == 'toggle':
            c.create_oval(mid_x - main_r, ctrl_y - main_r, mid_x + main_r, ctrl_y + main_r,
                          fill='#263449', outline='')
        draw_vector_icon(c, 'toggle', mid_x, ctrl_y, icon_r * 1.15,
                         accent if widget._hover_btn == 'toggle' else fg, playing=playing)
    else:
        main_fill = accent if widget._hover_btn == 'toggle' else '#263449'
        main_icon_fill = '#0F172A' if widget._hover_btn == 'toggle' else fg
        c.create_oval(mid_x - main_r, ctrl_y - main_r, mid_x + main_r, ctrl_y + main_r,
                      fill=main_fill, outline='', width=0)
        draw_vector_icon(c, 'toggle', mid_x, ctrl_y, icon_r, main_icon_fill, playing=playing)
    widget._btn_rects['toggle'] = (mid_x - main_r, ctrl_y - main_r, mid_x + main_r, ctrl_y + main_r)

    for action, bx in (('prev', mid_x - gap_x), ('next', mid_x + gap_x)):
        if widget._hover_btn == action:
            c.create_oval(bx - sub_r, ctrl_y - sub_r, bx + sub_r, ctrl_y + sub_r,
                          fill='#263449', outline='')
        draw_vector_icon(c, action, bx, ctrl_y, icon_r * 0.9,
                         accent if (style == '极简透明' and widget._hover_btn == action) else fg)
        widget._btn_rects[action] = (bx - sub_r, ctrl_y - sub_r, bx + sub_r, ctrl_y + sub_r)


def render_media_card(widget, fit_text_width_fn):
    """渲染桌面音乐胶囊主画布。"""
    widget._ensure_cover()
    c = widget.canvas
    k = widget._k()
    w = widget._current_width()
    h = widget._card_h()
    cover = widget._cover_size()
    pad = max(8, (h - cover) // 2)
    style = widget.prefs['style']
    bg = widget.prefs['bg']
    fg = widget.prefs['fg']
    accent = widget.prefs['accent']

    alpha = widget.prefs['opacity'] / 100.0
    if alpha != widget._last_alpha:
        widget._last_alpha = alpha
        widget.win.attributes('-alpha', alpha)

    c.configure(width=w, height=h)
    c.delete('all')
    widget._btn_rects.clear()
    widget._bar_rect = None

    # 1. 背景
    rad = round(widget.prefs['radius'] * k)
    if style != '极简透明':
        widget._ensure_bg(w, h, rad, bg, style)
        if widget._bg_photo is not None:
            c.create_image(0, 0, anchor='nw', image=widget._bg_photo)
        elif style == '圆角胶囊':
            draw_rounded_rect(c, 1, 1, w - 1, h - 1, rad, fill=bg, outline=CARD_BORDER)
        else:
            c.create_rectangle(1, 1, w - 1, h - 1, fill=bg, outline=CARD_BORDER)

    # 2. 封面
    cx, cy = pad, pad
    if widget._cover_photo is not None:
        c.create_image(cx, cy, anchor='nw', image=widget._cover_photo)
    else:
        crad = 4 if style == '方形卡片' else max(6, round(cover * 0.13))
        draw_rounded_rect(c, cx, cy, cx + cover, cy + cover, crad, fill='#233044', outline='')
        c.create_text(cx + cover // 2, cy + cover // 2, text='♫',
                      fill=accent, font=('Segoe UI Symbol', -max(18, round(cover * 0.36))))

    if widget._progress < 0.5:
        dot_r = max(4, round(cover * 0.065))
        dot_color = accent if widget._state['playing'] else ACCENT_PAUSE
        c.create_oval(cx + cover - dot_r * 2 - 2, cy + cover - dot_r * 2 - 2,
                      cx + cover - 2, cy + cover - 2,
                      fill=dot_color, outline=bg if style != '极简透明' else KEY_COLOR, width=2)

    # 3. 展开态信息、进度条与控制区
    if widget._progress <= 0.35:
        return
    rx = cx + cover + max(12, round(cover * 0.18))
    right_pad = max(12, round(cover * 0.18))
    content_w = w - rx - right_pad
    if content_w <= 36:
        return

    title_px = max(13, round(cover * 0.22))
    artist_px = max(11, round(cover * 0.155))
    time_px = max(10, round(cover * 0.13))
    widget._ensure_fonts(title_px, artist_px, time_px)

    pin_cx = w - right_pad - max(6, round(cover * 0.08))
    pin_cy = cy + max(9, round(cover * 0.12))
    title_avail = content_w - (max(20, round(cover * 0.28)) if widget._progress > 0.8 else 0)
    title = fit_text_width_fn(widget._state['title'] or '未命名曲目', widget._title_font.measure, title_avail)
    artist = fit_text_width_fn(widget._state['artist'] or '正在播放', widget._artist_font.measure, content_w)

    if title:
        _draw_text(c, style, rx, cy + round(cover * 0.02), title, widget._title_font, fg)
    if artist:
        _draw_text(c, style, rx, cy + round(cover * 0.30), artist, widget._artist_font,
                   TEXT_SECONDARY if style != '极简透明' else '#CBD5E1')

    if widget._progress > 0.82:
        pin_hit = max(9, round(cover * 0.12))
        if widget._hover_btn == 'pin':
            c.create_oval(pin_cx - pin_hit, pin_cy - pin_hit, pin_cx + pin_hit, pin_cy + pin_hit,
                          fill='#2A3648', outline='')
        draw_vector_icon(c, 'pin', pin_cx, pin_cy, max(6, round(cover * 0.095)),
                         accent if widget.locked else TEXT_SECONDARY, locked=widget.locked)
        widget._btn_rects['pin'] = (pin_cx - pin_hit, pin_cy - pin_hit, pin_cx + pin_hit, pin_cy + pin_hit)

        _draw_progress_bar(widget, c, rx, cy, w, right_pad, cover, content_w, style, fg, accent)
        _draw_controls(widget, c, rx, cy, cover, content_w, style, fg, accent)
