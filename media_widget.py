"""三态自动隐身桌面音乐胶囊：基于 Windows 原生 SMTC + 零依赖音频峰值监听，支持封面提取、缓动展开、切歌控制与同步歌词。

三态交互：
1. 无活跃媒体（或暂停超过设定秒数且未锁定）→ 自动隐藏；
2. 播放中（紧凑态）→ 仅显示圆角专辑封面；
3. 鼠标移入 / 锁定（展开态）→ 缓动展开歌名、歌手、进度条与控制按钮（动画中途可打断）。
支持：Ctrl+滚轮调大小、滚轮调不透明度、左键拖动、双击锁定、右键打开音乐胶囊与歌词设置。
"""
import asyncio
from datetime import datetime, timezone
import logging
import queue
import re
from threading import Event, Thread
import time
import tkinter as tk
from tkinter import font as tkfont

from audio_monitor import AudioActivityMonitor, merge_media_and_audio_state
from lyrics_widget import LYRICS_DEFAULTS, LYRICS_LINE_OPTIONS, LYRICS_MODES, LyricsOverlay
from media_renderer import (CARD_BORDER, KEY_COLOR, make_card_bg, make_rounded_cover,
                            render_media_card)
from media_settings import open_media_settings
from widget_layout import capture_anchor, clamp_to_work_area, resolve_anchor
from win32 import (toplevel_hwnd, add_exstyle, place_on_desktop,
                   WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW)

log = logging.getLogger('desktop')

BASE_COVER = 76
BASE_COMPACT_W = 100
BASE_EXPANDED_W = 336
BASE_CARD_H = 100
POLL_INTERVAL = 1.2
HOVER_LINGER = 0.45
ANIM_DURATION = 0.22
DRAG_THRESHOLD = 5
DEFAULT_MEDIA_POS = [40, 260]

MEDIA_STYLES = ('圆角胶囊', '方形卡片', '极简透明')
MEDIA_DEFAULTS = dict(
    scale=115,          # 缩放百分比（80..220）
    opacity=94,         # 不透明度（35..100）
    bg='#18202D',       # 卡片背景色
    fg='#F8FAFC',       # 主文字颜色
    accent='#38BDF8',   # 播放态指示与按钮强调色
    style='圆角胶囊',     # 卡片样式
    pause_hide=12,      # 暂停后自动隐藏秒数（0 表示暂停时不自动隐藏）
    radius=14,          # 圆角半径（4..32）
    audio_assist=True,  # 启用零依赖系统音频峰值监听辅助显隐
    **LYRICS_DEFAULTS,
)
MEDIA_RANGES = (
    ('scale', 80, 220),
    ('opacity', 35, 100),
    ('pause_hide', 0, 60),
    ('radius', 4, 32),
    ('lyrics_size', 14, 42),
)

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


def fit_text_width(text, measure_fn, max_px):
    """按实际像素宽度安全截断文本，杜绝中英文混排时文字与右侧按钮重合。"""
    s = (text or '').strip()
    if not s or max_px <= 12:
        return ''
    if measure_fn(s) <= max_px:
        return s
    ellipsis = '…'
    lo, hi = 0, len(s)
    best = ''
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = s[:mid] + ellipsis if mid > 0 else ellipsis
        if measure_fn(cand) <= max_px:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def sanitize_media_prefs(raw):
    """清洗并补全音乐胶囊与同步歌词的外观与行为配置。"""
    prefs = MEDIA_DEFAULTS.copy()
    if isinstance(raw, dict):
        for k, default in MEDIA_DEFAULTS.items():
            v = raw.get(k)
            if isinstance(default, float) and isinstance(v, (int, float)) and not isinstance(v, bool):
                prefs[k] = float(v)
            elif type(v) is type(default):
                prefs[k] = v
    for k, lo, hi in MEDIA_RANGES:
        prefs[k] = max(lo, min(hi, int(prefs[k])))
    prefs['lyrics_offset'] = round(max(-3.0, min(3.0, float(prefs['lyrics_offset']))), 1)
    if prefs['style'] not in MEDIA_STYLES:
        prefs['style'] = MEDIA_DEFAULTS['style']
    if prefs['lyrics_mode'] not in LYRICS_MODES:
        prefs['lyrics_mode'] = MEDIA_DEFAULTS['lyrics_mode']
    if prefs['lyrics_lines'] not in LYRICS_LINE_OPTIONS:
        prefs['lyrics_lines'] = MEDIA_DEFAULTS['lyrics_lines']
    for k in ('bg', 'fg', 'accent', 'lyrics_color', 'lyrics_next_color'):
        if not (isinstance(prefs[k], str) and re.fullmatch(r'#[0-9a-fA-F]{6}', prefs[k])):
            prefs[k] = MEDIA_DEFAULTS[k]
    lfont = prefs.get('lyrics_font', '')
    if not isinstance(lfont, str) or not lfont.strip() or lfont.startswith('@') or '汉仪中黑' in lfont:
        prefs['lyrics_font'] = MEDIA_DEFAULTS['lyrics_font']
    return prefs


def _empty_state():
    return {
        'active': False, 'playing': False, 'title': '', 'artist': '',
        'thumb': None, 'key': None, 'pos_base': 0.0, 'pos_at': 0.0, 'duration': 0.0,
    }


class _SmtcWorker:
    """后台线程维护单个 asyncio 事件循环，安全读取 SMTC 状态、进度与执行切歌指令。"""

    def __init__(self):
        self._stop = Event()
        self._cmds = queue.SimpleQueue()
        self._state = _empty_state()
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

    def send(self, action, arg=None):
        if HAS_WINRT:
            self._cmds.put((action, arg))

    def snapshot(self):
        return self._version, self._state

    def estimated_position(self, now_mono=None):
        st = self._state
        if not st['active']:
            return 0.0
        base = st.get('pos_base', 0.0)
        if not st['playing']:
            return base
        t = time.monotonic() if now_mono is None else now_mono
        elapsed = max(0.0, t - st.get('pos_at', t))
        pos = base + elapsed
        dur = st.get('duration', 0.0)
        return min(dur, pos) if dur > 0 else pos

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

    def _read_timeline(self, session, playing):
        try:
            tl = session.get_timeline_properties()
            if tl is None:
                return 0.0, time.monotonic(), 0.0
            pos = max(0.0, tl.position.total_seconds())
            dur = max(0.0, tl.end_time.total_seconds())
            now_mono = time.monotonic()
            if playing and tl.last_updated_time is not None:
                upd = tl.last_updated_time
                if upd.tzinfo is None:
                    upd = upd.replace(tzinfo=timezone.utc)
                delta = (datetime.now(timezone.utc) - upd).total_seconds()
                if 0.0 <= delta < 3600.0:
                    pos += delta
            if dur > 0:
                pos = min(dur, pos)
            return pos, now_mono, dur
        except Exception:
            return 0.0, time.monotonic(), 0.0

    async def _poll_once(self, mgr):
        session = mgr.get_current_session()
        if session is None:
            if self._state['active']:
                self._state = _empty_state()
                self._version += 1
            return

        pb = session.get_playback_info()
        status = pb.playback_status if pb else None
        playing = status == _PlaybackStatus.PLAYING
        paused = status == _PlaybackStatus.PAUSED
        if not (playing or paused):
            if self._state['active']:
                self._state = _empty_state()
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

        pos_base, pos_at, duration = self._read_timeline(session, playing)
        changed = (not self._state['active'] or self._state['playing'] != playing
                   or self._state['key'] != key)
        self._state = {
            'active': True, 'playing': playing, 'title': title, 'artist': artist,
            'thumb': self._last_thumb, 'key': key,
            'pos_base': pos_base, 'pos_at': pos_at, 'duration': duration,
        }
        if changed:
            self._version += 1

    async def _drain_cmds(self, mgr):
        while True:
            try:
                item = self._cmds.get_nowait()
            except queue.Empty:
                break
            action, arg = item if isinstance(item, tuple) else (item, None)
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
                elif action == 'seek' and arg is not None:
                    sec = max(0.0, float(arg))
                    dur = self._state.get('duration', 0.0)
                    if dur > 0:
                        sec = min(dur, sec)
                    await session.try_change_playback_position_async(int(sec * 10_000_000))
                    self._state = dict(self._state, pos_base=sec, pos_at=time.monotonic())
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
    """三态自动隐身桌面音乐胶囊（含可选同步歌词条与零依赖音频监听）。"""

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
        self.audio_monitor = AudioActivityMonitor()
        self._state = _empty_state()
        self._last_state_sig = None
        self._cover_cache_key = None
        self._cover_photo = None
        self._bg_cache_key = None
        self._bg_photo = None
        self._paused_since = None
        self._last_alpha = None
        self._last_progress_sec = -1
        self._hint_timer = None
        self._font_key = None
        self._title_font = None
        self._artist_font = None
        self._time_font = None
        self._hover_btn = None

        # 可打断的展开/收起缓动状态
        self._progress = 1.0 if self.locked else 0.0
        self._target = self._progress
        self._anim_from = self._progress
        self._anim_start = 0.0
        self._animating = False
        self._hover = False
        self._leave_at = 0.0

        # 拖动、进度条拖拽与设置弹窗
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False
        self._seeking = False
        self._seek_preview = None
        self._bar_rect = None
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
        self.canvas.bind('<Motion>', self._on_hover_motion)
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Double-Button-1>', self._on_double_click)
        self.canvas.bind('<ButtonRelease-3>', lambda e: self.open_settings())
        self.canvas.bind('<MouseWheel>', self._on_wheel)

        self.hwnd = toplevel_hwnd(self.win)
        add_exstyle(self.hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

        self.lyrics = LyricsOverlay(self, cfg)

        if self.enabled.get():
            self.worker.start()
            self._sync_audio_monitor()

    def _sync_audio_monitor(self):
        if self.enabled.get() and self.prefs.get('audio_assist', True):
            self.audio_monitor.start()
        else:
            self.audio_monitor.stop()

    def config(self):
        data = {
            'enabled': self.enabled.get(),
            'locked': self.locked,
            'position': list(self.pos),
            'anchor': self.anchor,
        }
        data.update(self.prefs)
        data.update(self.lyrics.config())
        return data

    def close(self):
        self.worker.stop()
        self.audio_monitor.stop()

    # ------------------------------------------------------------ 尺寸、字体与缓存
    def _k(self):
        return (self.prefs['scale'] / 100.0) * max(1.0, getattr(self.widgets, 'ui_scale', 1.0))

    def _cover_size(self):
        return max(32, round(BASE_COVER * self._k()))

    def _compact_w(self):
        return max(44, round(BASE_COMPACT_W * self._k()))

    def _expanded_w(self):
        return max(240, round(BASE_EXPANDED_W * self._k()))

    def _card_h(self):
        return max(44, round(BASE_CARD_H * self._k()))

    def _current_width(self):
        cw, ew = self._compact_w(), self._expanded_w()
        return round(cw + (ew - cw) * self._progress)

    def _ensure_fonts(self, title_px, artist_px, time_px):
        # 使用负像素字号，避免 Tkinter 在高 DPI 下与 ui_scale 双重放大
        key = (title_px, artist_px, time_px)
        if key != self._font_key:
            self._title_font = tkfont.Font(family='Microsoft YaHei UI', size=-title_px, weight='bold')
            self._artist_font = tkfont.Font(family='Microsoft YaHei UI', size=-artist_px)
            self._time_font = tkfont.Font(family='Segoe UI', size=-time_px)
            self._font_key = key

    def relayout(self, on_desktop=False):
        w, h = self._current_width(), self._card_h()
        x, y = resolve_anchor(self.anchor, self.pos, w, h, margin=8)
        self.pos = [x, y]
        if self.win.winfo_viewable():
            place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop)
        self.lyrics.relayout(on_desktop=on_desktop)

    def _ensure_cover(self):
        size = self._cover_size()
        rad = 4 if self.prefs['style'] == '方形卡片' else max(6, round(size * 0.13))
        cache_key = (self._state['key'], size, rad)
        if cache_key != self._cover_cache_key:
            self._cover_cache_key = cache_key
            self._cover_photo = make_rounded_cover(self._state['thumb'], size, radius=rad)

    def _ensure_bg(self, w, h, rad, bg, style):
        key = (w, h, rad, bg, style)
        if key != self._bg_cache_key:
            self._bg_cache_key = key
            self._bg_photo = make_card_bg(w, h, rad, bg, CARD_BORDER, style)

    # ------------------------------------------------------------ 缓动、渲染与设置
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

    def _render(self):
        render_media_card(self, fit_text_width)

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
            self.relayout(on_desktop=True)
            self._show_hint(f'大小 {val}%')
            self.widgets.queue_save()
            return 'break'
        if event.state & 1:  # Shift + 滚轮调不透明度（防止裸滚轮误触）
            val = max(35, min(100, self.prefs['opacity'] + direction * 5))
            self.prefs['opacity'] = val
            if self.dialog and self.dialog.winfo_exists() and 'opacity' in self._dlg_vars:
                self._dlg_vars['opacity'].set(val)
            self._render()
            self._show_hint(f'不透明度 {val}%')
            self.widgets.queue_save()
            return 'break'

    def open_settings(self):
        open_media_settings(self, MEDIA_DEFAULTS, MEDIA_STYLES, sanitize_media_prefs)

    # ------------------------------------------------------------ 交互事件
    def _on_enter(self, _e=None):
        self._hover = True
        self._leave_at = 0.0
        self._start_anim(1.0)

    def _on_leave(self, _e=None):
        self._hover = False
        self._leave_at = time.monotonic()
        if self._hover_btn is not None:
            self._hover_btn = None
            self._render()

    def _hit_bar(self, x, y):
        if not self._bar_rect:
            return False
        bx1, by1, bx2, by2, _x0, _x1 = self._bar_rect
        return bx1 <= x <= bx2 and by1 <= y <= by2

    def _seek_sec_from_x(self, x):
        if not self._bar_rect:
            return None
        _bx1, _by1, _bx2, _by2, x0, x1 = self._bar_rect
        dur = self._state.get('duration', 0.0)
        if dur <= 0 or x1 <= x0:
            return None
        ratio = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
        return ratio * dur

    def _on_hover_motion(self, e):
        if self._dragging or self._seeking or self._progress <= 0.82:
            return
        hit = 'bar' if self._hit_bar(e.x, e.y) else self._hit_button(e.x, e.y)
        if hit != self._hover_btn:
            self._hover_btn = hit
            self._render()

    def _on_double_click(self, _e=None):
        if self._seeking:
            return
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
        if self._progress > 0.82 and self._hit_bar(e.x, e.y):
            sec = self._seek_sec_from_x(e.x)
            if sec is not None:
                self._seeking = True
                self._seek_preview = sec
                self._hover_btn = 'bar'
                self._render()
                return
        self._drag_origin = (e.x_root, e.y_root)
        self._drag_offset = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())
        self._dragging = False

    def _on_motion(self, e):
        if self._seeking:
            sec = self._seek_sec_from_x(e.x)
            if sec is not None:
                self._seek_preview = sec
                self._render()
            return
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
        place_on_desktop(self.hwnd, x, y, on_desktop=True)
        if self.prefs['lyrics_mode'] == '紧贴胶囊下方':
            self.lyrics.relayout(on_desktop=True)

    def _on_release(self, e):
        if self._seeking:
            target = self._seek_preview
            self._seeking = False
            self._seek_preview = None
            if target is not None:
                self.worker.send('seek', target)
                self._state = dict(self._state, pos_base=target, pos_at=time.monotonic())
            self._render()
            return
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
    def tick(self, now, blocked, icons_visible, on_desktop=False, force_restack=False):
        audio_assist = bool(self.prefs.get('audio_assist', True))
        if not self.enabled.get() or (not HAS_WINRT and not audio_assist):
            self.audio_monitor.stop()
            if self.win.winfo_viewable():
                self.win.withdraw()
            if self.lyrics.win.winfo_viewable():
                self.lyrics.win.withdraw()
            return
        self.worker.start()
        self._sync_audio_monitor()

        _ver, smtc_st = self.worker.snapshot()
        audio_active = self.audio_monitor.is_active(now) if audio_assist else False
        prev_fallback = bool(self._state.get('audio_fallback'))
        st = merge_media_and_audio_state(smtc_st, audio_active, audio_assist=audio_assist, prev_fallback=prev_fallback)

        sig = (st['active'], st['playing'], st['key'], st['title'], st['artist'])
        self._state = st
        if sig != self._last_state_sig:
            self._last_state_sig = sig
            if st['active'] and not st['playing']:
                if self._paused_since is None:
                    self._paused_since = now
            else:
                self._paused_since = None
            self._render()

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
        # 音频监听兜底态在超时隐藏后重置兜底标记，避免下次仍停留在“已暂停”
        if paused_too_long and self._state.get('audio_fallback') and not audio_active:
            self._state = _empty_state()
            self._last_state_sig = None

        should_show = (
            icons_visible is False
            and not blocked
            and self._state['active']
            and not paused_too_long
        )
        visible = bool(self.win.winfo_viewable())
        est_pos = self.worker.estimated_position(now)
        if should_show:
            if not visible or force_restack:
                x, y = resolve_anchor(self.anchor, self.pos, self._current_width(), self._card_h(), margin=8)
                self.pos = [x, y]
                if not visible:
                    self.win.geometry(f'{x:+d}{y:+d}')
                    self._render()
                place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop, show=True)
            elif self._progress > 0.82 and not self._animating:
                cur_sec = int(est_pos)
                if cur_sec != self._last_progress_sec:
                    self._last_progress_sec = cur_sec
                    self._render()
        elif visible:
            self.win.withdraw()

        self.lyrics.tick(self._state, est_pos, should_show,
                         on_desktop=on_desktop, force_restack=force_restack)
