"""三态自动隐身桌面音乐胶囊：基于 Windows 原生 SMTC，支持封面提取、缓动展开、切歌控制与完整外观自定义。

三态交互：
1. 无活跃媒体（或暂停超过设定秒数且未锁定）→ 自动隐藏；
2. 播放中（紧凑态）→ 仅显示圆角专辑封面；
3. 鼠标移入 / 锁定（展开态）→ 缓动展开歌名、歌手与上一首/播放暂停/下一首按钮（动画中途可打断）。
支持：Ctrl+滚轮调大小、滚轮调不透明度、左键拖动、双击锁定、右键打开音乐胶囊设置。
"""
import asyncio
from io import BytesIO
import logging
import queue
import re
from threading import Event, Thread
import time
import tkinter as tk
from tkinter import colorchooser, ttk

from widget_layout import capture_anchor, clamp_to_work_area, resolve_anchor
from win32 import (user32, toplevel_hwnd, add_exstyle, HWND_BOTTOM, SW_SHOWNOACTIVATE,
                   SWP_NOSIZE, SWP_NOACTIVATE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW)

log = logging.getLogger('desktop')

KEY_COLOR = '#010203'
CARD_BORDER = '#334155'
TEXT_SECONDARY = '#94A3B8'
ACCENT_PAUSE = '#94A3B8'

BASE_COVER = 56
BASE_COMPACT_W = 68
BASE_EXPANDED_W = 308
BASE_CARD_H = 68
POLL_INTERVAL = 1.2
HOVER_LINGER = 0.45
ANIM_DURATION = 0.22
DRAG_THRESHOLD = 5
DEFAULT_MEDIA_POS = [40, 260]

MEDIA_STYLES = ('圆角胶囊', '方形卡片', '极简透明')
MEDIA_DEFAULTS = dict(
    scale=135,          # 缩放百分比（80..220），默认比初版更大更清晰
    opacity=94,         # 不透明度（35..100）
    bg='#18202D',       # 卡片背景色
    fg='#F8FAFC',       # 主文字颜色
    accent='#38BDF8',   # 播放态指示与按钮强调色
    style='圆角胶囊',     # 卡片样式
    pause_hide=12,      # 暂停后自动隐藏秒数（0 表示暂停时不自动隐藏）
    radius=16,          # 圆角半径（4..32）
)
MEDIA_RANGES = (
    ('scale', 80, 220),
    ('opacity', 35, 100),
    ('pause_hide', 0, 60),
    ('radius', 4, 32),
)

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SmtcManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _PlaybackStatus,
    )
    from winrt.windows.storage.streams import DataReader as _DataReader
    HAS_WINRT = True
except Exception:
    HAS_WINRT = False


def ease_out_cubic(t):
    """三次缓出曲线（0..1 -> 0..1）。"""
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


def truncate_text(text, max_chars=18):
    s = (text or '').strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars - 1] + '…'


def sanitize_media_prefs(raw):
    """清洗并补全音乐胶囊的外观与行为配置。"""
    prefs = MEDIA_DEFAULTS.copy()
    if isinstance(raw, dict):
        for k, default in MEDIA_DEFAULTS.items():
            v = raw.get(k)
            if type(v) is type(default):
                prefs[k] = v
    for k, lo, hi in MEDIA_RANGES:
        prefs[k] = max(lo, min(hi, int(prefs[k])))
    if prefs['style'] not in MEDIA_STYLES:
        prefs['style'] = MEDIA_DEFAULTS['style']
    for k in ('bg', 'fg', 'accent'):
        if not (isinstance(prefs[k], str) and re.fullmatch(r'#[0-9a-fA-F]{6}', prefs[k])):
            prefs[k] = MEDIA_DEFAULTS[k]
    return prefs


def _make_rounded_cover(raw_bytes, size, radius=10):
    """将封面二进制转为圆角 Tk PhotoImage；缺省或失败时返回 None。"""
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


class _SmtcWorker:
    """后台线程维护单个 asyncio 事件循环，安全读取 SMTC 状态并执行切歌指令。"""

    def __init__(self):
        self._stop = Event()
        self._cmds = queue.SimpleQueue()
        self._state = {'active': False, 'playing': False, 'title': '', 'artist': '', 'thumb': None, 'key': None}
        self._version = 0
        self._last_thumb_key = None
        self._last_thumb = None
        self._thread = None

    def start(self):
        if not HAS_WINRT or self._thread is not None:
            return
        self._thread = Thread(target=self._run, name='smtc', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def send(self, action):
        if HAS_WINRT:
            self._cmds.put(action)

    def snapshot(self):
        return self._version, self._state

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._loop())
        except Exception:
            log.debug('SMTC 轮询线程退出', exc_info=True)
        finally:
            loop.close()

    async def _read_thumb(self, ref):
        if ref is None:
            return None
        try:
            stream = await ref.open_read_async()
            size = int(stream.size)
            if size <= 0 or size > 4 * 1024 * 1024:
                return None
            reader = _DataReader(stream)
            await reader.load_async(size)
            buf = bytearray(size)
            reader.read_bytes(buf)
            return bytes(buf)
        except Exception:
            return None

    async def _poll_once(self, mgr):
        session = mgr.get_current_session()
        if session is None:
            if self._state['active']:
                self._state = {'active': False, 'playing': False, 'title': '', 'artist': '', 'thumb': None, 'key': None}
                self._version += 1
            return

        pb = session.get_playback_info()
        status = pb.playback_status if pb else None
        playing = status == _PlaybackStatus.PLAYING
        paused = status == _PlaybackStatus.PAUSED
        if not (playing or paused):
            if self._state['active']:
                self._state = {'active': False, 'playing': False, 'title': '', 'artist': '', 'thumb': None, 'key': None}
                self._version += 1
            return

        info = await session.try_get_media_properties_async()
        title = (info.title or '').strip() if info else ''
        artist = (info.artist or '').strip() if info else ''
        if not title and not artist:
            return
        key = (title, artist)
        if key != self._last_thumb_key:
            self._last_thumb = await self._read_thumb(info.thumbnail if info else None)
            self._last_thumb_key = key

        if not self._state['active'] or self._state['playing'] != playing or self._state['key'] != key:
            self._state = {
                'active': True,
                'playing': playing,
                'title': title,
                'artist': artist,
                'thumb': self._last_thumb,
                'key': key,
            }
            self._version += 1

    async def _drain_cmds(self, mgr):
        while True:
            try:
                action = self._cmds.get_nowait()
            except queue.Empty:
                break
            session = mgr.get_current_session()
            if session is None:
                continue
            try:
                if action == 'toggle':
                    await session.try_toggle_play_pause_async()
                elif action == 'next':
                    await session.try_skip_next_async()
                elif action == 'prev':
                    await session.try_skip_previous_async()
            except Exception:
                log.debug('SMTC 控制指令失败: %s', action, exc_info=True)

    async def _loop(self):
        mgr = await _SmtcManager.request_async()
        while not self._stop.is_set():
            try:
                await self._drain_cmds(mgr)
                await self._poll_once(mgr)
            except Exception:
                log.debug('SMTC 单次轮询失败', exc_info=True)
            for _ in range(int(POLL_INTERVAL / 0.15)):
                if self._stop.is_set() or not self._cmds.empty():
                    break
                await asyncio.sleep(0.15)


class MediaWidget:
    """三态自动隐身桌面音乐胶囊。"""

    def __init__(self, widgets, cfg, on_save, on_settings):
        self.widgets = widgets
        self.root = widgets.root
        self.on_save = on_save
        self.on_settings = on_settings
        cfg = cfg if isinstance(cfg, dict) else {}

        self.enabled = tk.BooleanVar(value=bool(cfg.get('enabled', True)))
        self.locked = bool(cfg.get('locked', False))
        self.prefs = sanitize_media_prefs(cfg)
        pos = cfg.get('position')
        if not (isinstance(pos, list) and len(pos) == 2 and all(type(v) is int for v in pos)):
            pos = list(DEFAULT_MEDIA_POS)
        self.pos = list(pos)
        self.anchor = cfg.get('anchor') if isinstance(cfg.get('anchor'), dict) else None

        self.worker = _SmtcWorker()
        self._seen_version = -1
        self._state = {'active': False, 'playing': False, 'title': '', 'artist': '', 'thumb': None, 'key': None}
        self._cover_cache_key = None
        self._cover_photo = None
        self._paused_since = None
        self._last_alpha = None
        self._hint_timer = None

        # 可打断的展开/收起缓动状态
        self._progress = 1.0 if self.locked else 0.0
        self._target = self._progress
        self._anim_from = self._progress
        self._anim_start = 0.0
        self._animating = False
        self._hover = False
        self._leave_at = 0.0

        # 拖动与设置弹窗
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False
        self._btn_rects = {}
        self.dialog = None
        self._dlg_vars = {}

        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=KEY_COLOR)
        self.win.wm_attributes('-transparentcolor', KEY_COLOR)

        self.canvas = tk.Canvas(self.win, width=self._compact_w(), height=self._card_h(),
                                bg=KEY_COLOR, highlightthickness=0, cursor='hand2')
        self.canvas.pack()
        self.canvas.bind('<Enter>', self._on_enter)
        self.canvas.bind('<Leave>', self._on_leave)
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Double-Button-1>', self._on_double_click)
        self.canvas.bind('<ButtonRelease-3>', lambda e: self.open_settings())
        self.canvas.bind('<MouseWheel>', self._on_wheel)

        self.hwnd = toplevel_hwnd(self.win)
        add_exstyle(self.hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

        if self.enabled.get():
            self.worker.start()

    def config(self):
        data = {
            'enabled': self.enabled.get(),
            'locked': self.locked,
            'position': list(self.pos),
            'anchor': self.anchor,
        }
        data.update(self.prefs)
        return data

    def close(self):
        self.worker.stop()

    # ------------------------------------------------------------ 尺寸计算
    def _k(self):
        return (self.prefs['scale'] / 100.0) * max(1.0, getattr(self.widgets, 'ui_scale', 1.0))

    def _cover_size(self):
        return max(32, round(BASE_COVER * self._k()))

    def _compact_w(self):
        return max(44, round(BASE_COMPACT_W * self._k()))

    def _expanded_w(self):
        return max(200, round(BASE_EXPANDED_W * self._k()))

    def _card_h(self):
        return max(44, round(BASE_CARD_H * self._k()))

    def _current_width(self):
        cw, ew = self._compact_w(), self._expanded_w()
        return round(cw + (ew - cw) * self._progress)

    def relayout(self):
        """按锚点将挂件归位到当前屏幕工作区内。"""
        w, h = self._current_width(), self._card_h()
        x, y = resolve_anchor(self.anchor, self.pos, w, h, margin=8)
        self.pos = [x, y]
        if self.win.winfo_viewable():
            user32.SetWindowPos(self.hwnd, HWND_BOTTOM, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

    def _ensure_cover(self):
        size = self._cover_size()
        rad = 4 if self.prefs['style'] == '方形卡片' else max(4, round(self.prefs['radius'] * 0.65 * self._k()))
        cache_key = (self._state['key'], size, rad)
        if cache_key != self._cover_cache_key:
            self._cover_cache_key = cache_key
            self._cover_photo = _make_rounded_cover(self._state['thumb'], size, radius=rad)

    # ------------------------------------------------------------ 缓动与渲染
    def _start_anim(self, target):
        target = 1.0 if target else 0.0
        if abs(target - self._target) < 1e-4 and (self._animating or abs(self._progress - target) < 1e-4):
            return
        self._anim_from = self._progress
        self._target = target
        self._anim_start = time.monotonic()
        if not self._animating:
            self._animating = True
            self.root.after(16, self._step_anim)

    def _step_anim(self):
        if not self._animating or self.widgets.app._quitting:
            self._animating = False
            return
        elapsed = time.monotonic() - self._anim_start
        ratio = ease_out_cubic(elapsed / ANIM_DURATION)
        self._progress = self._anim_from + (self._target - self._anim_from) * ratio
        self._render()
        if elapsed < ANIM_DURATION:
            self.root.after(16, self._step_anim)
        else:
            self._progress = self._target
            self._animating = False
            self._render()

    def _draw_rounded_rect(self, c, x1, y1, x2, y2, r, **kw):
        r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
        if r <= 1:
            return c.create_rectangle(x1, y1, x2, y2, **kw)
        pts = (
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        )
        return c.create_polygon(pts, smooth=True, splinesteps=20, **kw)

    def _render(self):
        self._ensure_cover()
        c = self.canvas
        k = self._k()
        w = self._current_width()
        h = self._card_h()
        cover = self._cover_size()
        pad = max(4, (h - cover) // 2)
        style = self.prefs['style']
        bg = self.prefs['bg']
        fg = self.prefs['fg']
        accent = self.prefs['accent']

        alpha = self.prefs['opacity'] / 100.0
        if alpha != self._last_alpha:
            self._last_alpha = alpha
            self.win.attributes('-alpha', alpha)

        c.configure(width=w, height=h)
        c.delete('all')
        self._btn_rects.clear()

        # 背景
        if style == '圆角胶囊':
            rad = round(self.prefs['radius'] * k)
            self._draw_rounded_rect(c, 1, 1, w - 1, h - 1, rad, fill=bg, outline=CARD_BORDER)
        elif style == '方形卡片':
            c.create_rectangle(1, 1, w - 1, h - 1, fill=bg, outline=CARD_BORDER)

        # 封面
        cx, cy = pad, pad
        if self._cover_photo is not None:
            c.create_image(cx, cy, anchor='nw', image=self._cover_photo)
        else:
            rad = 4 if style == '方形卡片' else max(4, round(self.prefs['radius'] * 0.65 * k))
            self._draw_rounded_rect(c, cx, cy, cx + cover, cy + cover, rad, fill='#233044', outline='')
            c.create_text(cx + cover // 2, cy + cover // 2, text='♫',
                          fill=accent, font=('Segoe UI Symbol', max(14, round(20 * k))))

        # 播放状态指示点
        dot_r = max(4, round(5 * k))
        dot_color = accent if self._state['playing'] else ACCENT_PAUSE
        c.create_oval(cx + cover - dot_r * 2 - 1, cy + cover - dot_r * 2 - 1,
                      cx + cover - 1, cy + cover - 1,
                      fill=dot_color, outline=bg if style != '极简透明' else KEY_COLOR, width=2)

        def draw_text(x, y, text, font_spec, fill, anchor='nw'):
            if style == '极简透明':
                c.create_text(x + 1, y + 1, anchor=anchor, text=text, fill='#0b0f19', font=font_spec)
            c.create_text(x, y, anchor=anchor, text=text, fill=fill, font=font_spec)

        # 展开区域：歌名与歌手
        if self._progress > 0.35:
            tx = cx + cover + max(8, round(10 * k))
            title_pt = max(9, round(10.5 * k))
            artist_pt = max(8, round(8.5 * k))
            title = truncate_text(self._state['title'] or '未命名曲目', 14)
            artist = truncate_text(self._state['artist'] or '正在播放', 16)
            draw_text(tx, round(h * 0.22), title, ('Microsoft YaHei UI', title_pt, 'bold'), fg)
            draw_text(tx, round(h * 0.56), artist, ('Microsoft YaHei UI', artist_pt),
                      TEXT_SECONDARY if style != '极简透明' else fg)

        # 右侧控制按钮
        if self._progress > 0.82:
            play_icon = '⏸' if self._state['playing'] else '▶'
            pin_color = accent if self.locked else TEXT_SECONDARY
            btn_pt = max(10, round(11 * k))
            step = max(22, round(26 * k))
            right_margin = max(16, round(18 * k))
            hit = max(11, step // 2)
            buttons = [
                ('prev', '⏮', w - right_margin - step * 3, fg),
                ('toggle', play_icon, w - right_margin - step * 2, fg),
                ('next', '⏭', w - right_margin - step, fg),
                ('pin', '📌', w - right_margin, pin_color),
            ]
            for action, sym, bx, color in buttons:
                draw_text(bx, h // 2, sym, ('Segoe UI Symbol', btn_pt), color, anchor='center')
                self._btn_rects[action] = (bx - hit, max(6, h // 6), bx + hit, h - max(6, h // 6))

    # ------------------------------------------------------------ 快捷滚轮与设置弹窗
    def _show_hint(self, text):
        self.canvas.delete('hint')
        self.canvas.create_text(6, 4, anchor='nw', text=text, fill='#F59E0B',
                                font=('Microsoft YaHei UI', 8, 'bold'), tags='hint')
        if self._hint_timer:
            self.root.after_cancel(self._hint_timer)
        self._hint_timer = self.root.after(800, lambda: self.canvas.delete('hint'))

    def _on_wheel(self, event):
        if not event.delta:
            return
        direction = 1 if event.delta > 0 else -1
        if event.state & 4:  # Ctrl + 滚轮调缩放
            val = max(80, min(220, self.prefs['scale'] + direction * 10))
            self.prefs['scale'] = val
            if self.dialog and self.dialog.winfo_exists() and 'scale' in self._dlg_vars:
                self._dlg_vars['scale'].set(val)
            self._render()
            self.relayout()
            self._show_hint(f'大小 {val}%')
        else:
            val = max(35, min(100, self.prefs['opacity'] + direction * 5))
            self.prefs['opacity'] = val
            if self.dialog and self.dialog.winfo_exists() and 'opacity' in self._dlg_vars:
                self._dlg_vars['opacity'].set(val)
            self._render()
            self._show_hint(f'不透明度 {val}%')
        self.widgets.queue_save()
        return 'break'

    def open_settings(self):
        """打开音乐胶囊专属外观与行为设置窗口（修改实时生效）。"""
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        w = tk.Toplevel(self.root)
        self.dialog = w
        w.title('桌面音乐胶囊设置')
        ui = max(1.0, getattr(self.widgets, 'ui_scale', 1.0))
        w.geometry(f'{round(470 * ui)}x{round(460 * ui)}')
        w.minsize(420, 410)
        pane = ttk.Frame(w, padding=16)
        pane.pack(fill='both', expand=True)
        ttk.Label(pane, text='音乐胶囊外观与行为 · 修改立即生效',
                  font=('Microsoft YaHei UI', 12)).pack(anchor='w', pady=(0, 10))

        box = ttk.Frame(pane)
        box.pack(fill='x')
        vars_ = self._dlg_vars = {}

        def apply(*_args):
            for k, var in vars_.items():
                if k == 'locked':
                    self.locked = bool(var.get())
                    self._start_anim(1.0 if self.locked or self._hover else 0.0)
                else:
                    self.prefs[k] = var.get()
            self.prefs = sanitize_media_prefs(self.prefs)
            self._render()
            self.relayout()
            self.widgets.queue_save()

        def row(label):
            f = ttk.Frame(box)
            f.pack(fill='x', pady=4)
            ttk.Label(f, text=label, width=14).pack(side='left')
            return f

        for key, label, lo, hi in (
            ('scale', '大小缩放(%)', 80, 220),
            ('opacity', '不透明度(%)', 35, 100),
            ('radius', '圆角半径', 4, 32),
            ('pause_hide', '暂停隐藏(秒,0不藏)', 0, 60),
        ):
            var = vars_[key] = tk.IntVar(value=self.prefs[key])
            tk.Scale(row(label), from_=lo, to=hi, orient='horizontal', variable=var,
                     command=apply, showvalue=True, length=240).pack(side='left', fill='x', expand=True)

        style_var = vars_['style'] = tk.StringVar(value=self.prefs['style'])
        cb = ttk.Combobox(row('卡片样式'), textvariable=style_var, values=list(MEDIA_STYLES),
                          state='readonly', width=22)
        cb.pack(side='left')
        cb.bind('<<ComboboxSelected>>', apply)

        def pick_color(key, title):
            color = colorchooser.askcolor(self.prefs[key], parent=w, title=title)[1]
            if color:
                self.prefs[key] = color
                apply()

        colors = row('颜色')
        ttk.Button(colors, text='背景色', command=lambda: pick_color('bg', '选择背景色')).pack(side='left', padx=3)
        ttk.Button(colors, text='文字色', command=lambda: pick_color('fg', '选择文字色')).pack(side='left', padx=3)
        ttk.Button(colors, text='强调色', command=lambda: pick_color('accent', '选择强调色')).pack(side='left', padx=3)

        checks = ttk.Frame(pane)
        checks.pack(fill='x', pady=(10, 6))
        ttk.Checkbutton(checks, text='启用桌面音乐胶囊', variable=self.enabled,
                        command=self.on_save).pack(side='left', padx=(0, 12))
        lock_var = vars_['locked'] = tk.BooleanVar(value=self.locked)
        ttk.Checkbutton(checks, text='常驻展开（不自动收起）', variable=lock_var,
                        command=apply).pack(side='left')

        ttk.Label(pane, text='提示：在音乐胶囊上 Ctrl+滚轮调大小 · 滚轮调透明度 · 双击锁定展开 · 左键拖动位置',
                  foreground='#64748B').pack(anchor='w', pady=(6, 12))

        btns = ttk.Frame(pane)
        btns.pack(fill='x')

        def reset_defaults():
            self.prefs = MEDIA_DEFAULTS.copy()
            for k, var in vars_.items():
                if k in self.prefs:
                    var.set(self.prefs[k])
            apply()

        ttk.Button(btns, text='恢复默认', command=reset_defaults).pack(side='left')
        ttk.Button(btns, text='打开主设置', command=self.on_settings).pack(side='left', padx=8)
        ttk.Button(btns, text='完成', command=w.destroy).pack(side='right')

    # ------------------------------------------------------------ 交互事件
    def _on_enter(self, _e=None):
        self._hover = True
        self._leave_at = 0.0
        self._start_anim(1.0)

    def _on_leave(self, _e=None):
        self._hover = False
        self._leave_at = time.monotonic()

    def _on_double_click(self, _e=None):
        self.locked = not self.locked
        if self.dialog and self.dialog.winfo_exists() and 'locked' in self._dlg_vars:
            self._dlg_vars['locked'].set(self.locked)
        self._start_anim(1.0 if self.locked or self._hover else 0.0)
        self._render()
        self.on_save()

    def _hit_button(self, x, y):
        for action, (x1, y1, x2, y2) in self._btn_rects.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return action
        return None

    def _on_press(self, e):
        self._drag_origin = (e.x_root, e.y_root)
        self._drag_offset = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())
        self._dragging = False

    def _on_motion(self, e):
        if self._drag_origin is None or self._drag_offset is None:
            return
        if not self._dragging:
            if abs(e.x_root - self._drag_origin[0]) + abs(e.y_root - self._drag_origin[1]) <= DRAG_THRESHOLD:
                return
            self._dragging = True
        x, y = clamp_to_work_area(e.x_root - self._drag_offset[0],
                                  e.y_root - self._drag_offset[1],
                                  self._current_width(), self._card_h(), margin=8)
        self.pos = [x, y]
        user32.SetWindowPos(self.hwnd, HWND_BOTTOM, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

    def _on_release(self, e):
        was_drag = self._dragging
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False
        if was_drag:
            self.anchor = capture_anchor(self.pos[0], self.pos[1], self._current_width(), self._card_h())
            self.on_save()
            return
        action = self._hit_button(e.x, e.y)
        if action == 'pin':
            self.locked = not self.locked
            if self.dialog and self.dialog.winfo_exists() and 'locked' in self._dlg_vars:
                self._dlg_vars['locked'].set(self.locked)
            self._render()
            self.on_save()
        elif action in ('prev', 'toggle', 'next'):
            self.worker.send(action)

    # ------------------------------------------------------------ 主循环调度
    def tick(self, now, blocked, icons_visible):
        if not self.enabled.get() or not HAS_WINRT:
            if self.win.winfo_viewable():
                self.win.withdraw()
            return
        self.worker.start()

        ver, st = self.worker.snapshot()
        if ver != self._seen_version:
            self._seen_version = ver
            self._state = st
            if st['active'] and not st['playing']:
                if self._paused_since is None:
                    self._paused_since = now
            else:
                self._paused_since = None
            self._render()

        # 延迟收起
        if not self._hover and not self.locked and self._target > 0.5:
            if self._leave_at and now - self._leave_at >= HOVER_LINGER:
                self._start_anim(0.0)

        pause_limit = self.prefs['pause_hide']
        paused_too_long = (
            pause_limit > 0
            and self._paused_since is not None
            and not self.locked
            and not self._hover
            and (now - self._paused_since) > pause_limit
        )
        should_show = (
            icons_visible is False
            and not blocked
            and self._state['active']
            and not paused_too_long
        )
        visible = bool(self.win.winfo_viewable())
        if should_show:
            if not visible:
                x, y = resolve_anchor(self.anchor, self.pos, self._current_width(), self._card_h(), margin=8)
                self.pos = [x, y]
                self.win.geometry(f'{x:+d}{y:+d}')
                self._render()
                user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(self.hwnd, HWND_BOTTOM, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
        elif visible:
            self.win.withdraw()
