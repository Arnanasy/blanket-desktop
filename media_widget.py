"""三态自动隐身桌面音乐胶囊：基于 Windows 原生 SMTC，支持封面提取、缓动展开与切歌控制。

三态交互（参考 Wallpaper Engine 媒体组件设计）：
1. 无活跃媒体（或长时间暂停且未锁定）→ 自动隐藏；
2. 播放中（紧凑态）→ 仅显示圆角专辑封面；
3. 鼠标移入 / 锁定（展开态）→ 缓动展开歌名、歌手与上一首/播放暂停/下一首按钮（动画中途可打断）。
"""
import asyncio
from io import BytesIO
import logging
import queue
from threading import Event, Thread
import time
import tkinter as tk

from widget_layout import capture_anchor, clamp_to_work_area, resolve_anchor
from win32 import (user32, toplevel_hwnd, add_exstyle, HWND_BOTTOM, SW_SHOWNOACTIVATE,
                   SWP_NOSIZE, SWP_NOACTIVATE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW)

log = logging.getLogger('desktop')

KEY_COLOR = '#010203'
CARD_BG = '#18202D'
CARD_BORDER = '#334155'
TEXT_PRIMARY = '#F8FAFC'
TEXT_SECONDARY = '#94A3B8'
ACCENT_PLAY = '#38BDF8'
ACCENT_PAUSE = '#94A3B8'

COVER_SIZE = 52
COMPACT_W = 64
EXPANDED_W = 286
CARD_H = 64
POLL_INTERVAL = 1.2          # SMTC 状态轮询间隔（秒）
PAUSE_HIDE_SECONDS = 12.0    # 暂停后多久自动隐身（未锁定时）
HOVER_LINGER = 0.45          # 鼠标移开后延迟收起时间（秒）
ANIM_DURATION = 0.22         # 展开/收起缓动时长（秒）
DRAG_THRESHOLD = 5
DEFAULT_MEDIA_POS = [40, 260]

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


def _make_rounded_cover(raw_bytes, size, radius=10):
    """将封面二进制转为圆角 Tk PhotoImage；缺省或失败时返回 None。"""
    if not HAS_PIL or not raw_bytes:
        return None
    try:
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
            new_state = {'active': False, 'playing': False, 'title': '', 'artist': '', 'thumb': None, 'key': None}
            if new_state['active'] != self._state['active']:
                self._state = new_state
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

        if (not self._state['active'] or self._state['playing'] != playing
                or self._state['key'] != key):
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
        pos = cfg.get('position')
        if not (isinstance(pos, list) and len(pos) == 2 and all(type(v) is int for v in pos)):
            pos = list(DEFAULT_MEDIA_POS)
        self.pos = list(pos)
        self.anchor = cfg.get('anchor') if isinstance(cfg.get('anchor'), dict) else None

        self.worker = _SmtcWorker()
        self._seen_version = -1
        self._state = {'active': False, 'playing': False, 'title': '', 'artist': '', 'thumb': None, 'key': None}
        self._cover_key = None
        self._cover_photo = None
        self._paused_since = None

        # 可打断的展开/收起缓动状态
        self._progress = 1.0 if self.locked else 0.0
        self._target = self._progress
        self._anim_from = self._progress
        self._anim_start = 0.0
        self._animating = False
        self._hover = False
        self._leave_at = 0.0

        # 拖动状态
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False
        self._btn_rects = {}

        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=KEY_COLOR)
        self.win.wm_attributes('-transparentcolor', KEY_COLOR)
        self.win.attributes('-alpha', 0.94)

        self.canvas = tk.Canvas(self.win, width=COMPACT_W, height=CARD_H,
                                bg=KEY_COLOR, highlightthickness=0, cursor='hand2')
        self.canvas.pack()
        self.canvas.bind('<Enter>', self._on_enter)
        self.canvas.bind('<Leave>', self._on_leave)
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Double-Button-1>', self._on_double_click)
        self.canvas.bind('<ButtonRelease-3>', lambda e: self.on_settings())

        self.hwnd = toplevel_hwnd(self.win)
        add_exstyle(self.hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

        if self.enabled.get():
            self.worker.start()

    def config(self):
        return {
            'enabled': self.enabled.get(),
            'locked': self.locked,
            'position': list(self.pos),
            'anchor': self.anchor,
        }

    def close(self):
        self.worker.stop()

    def _current_width(self):
        return round(COMPACT_W + (EXPANDED_W - COMPACT_W) * self._progress)

    def relayout(self):
        """按锚点将挂件归位到当前屏幕工作区内。"""
        w = self._current_width()
        x, y = resolve_anchor(self.anchor, self.pos, w, CARD_H, margin=8)
        self.pos = [x, y]
        if self.win.winfo_viewable():
            user32.SetWindowPos(self.hwnd, HWND_BOTTOM, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

    def _start_anim(self, target):
        """可打断缓动：从当前进度平滑过渡到目标进度（0 紧凑 / 1 展开）。"""
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
        r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
        pts = (
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        )
        return c.create_polygon(pts, smooth=True, splinesteps=20, **kw)

    def _render(self):
        c = self.canvas
        w = self._current_width()
        c.configure(width=w, height=CARD_H)
        c.delete('all')
        self._btn_rects.clear()

        # 背景圆角胶囊
        self._draw_rounded_rect(c, 1, 1, w - 1, CARD_H - 1, 14, fill=CARD_BG, outline=CARD_BORDER)

        # 左侧专辑封面（或音符占位）
        cx, cy = 6, 6
        if self._cover_photo is not None:
            c.create_image(cx, cy, anchor='nw', image=self._cover_photo)
        else:
            self._draw_rounded_rect(c, cx, cy, cx + COVER_SIZE, cy + COVER_SIZE, 10, fill='#233044', outline='')
            c.create_text(cx + COVER_SIZE // 2, cy + COVER_SIZE // 2, text='♫',
                          fill=ACCENT_PLAY, font=('Segoe UI Symbol', 18))

        # 封面右下角播放状态微圆点
        dot_color = ACCENT_PLAY if self._state['playing'] else ACCENT_PAUSE
        c.create_oval(cx + COVER_SIZE - 10, cy + COVER_SIZE - 10,
                      cx + COVER_SIZE - 1, cy + COVER_SIZE - 1,
                      fill=dot_color, outline=CARD_BG, width=2)

        # 展开区域：歌名、歌手与控制按钮
        if self._progress > 0.35:
            tx = cx + COVER_SIZE + 10
            title = truncate_text(self._state['title'] or '未命名曲目', 15)
            artist = truncate_text(self._state['artist'] or '正在播放', 17)
            c.create_text(tx, 14, anchor='nw', text=title, fill=TEXT_PRIMARY,
                          font=('Microsoft YaHei UI', 9, 'bold'))
            c.create_text(tx, 33, anchor='nw', text=artist, fill=TEXT_SECONDARY,
                          font=('Microsoft YaHei UI', 8))

        if self._progress > 0.82:
            # 右侧控制按钮：上一首 / 播放暂停 / 下一首 / 锁定
            play_icon = '⏸' if self._state['playing'] else '▶'
            pin_color = ACCENT_PLAY if self.locked else TEXT_SECONDARY
            buttons = [
                ('prev', '⏮', w - 92, TEXT_PRIMARY),
                ('toggle', play_icon, w - 68, TEXT_PRIMARY),
                ('next', '⏭', w - 44, TEXT_PRIMARY),
                ('pin', '📌', w - 20, pin_color),
            ]
            for action, sym, bx, color in buttons:
                c.create_text(bx, CARD_H // 2, text=sym, fill=color, font=('Segoe UI Symbol', 10))
                self._btn_rects[action] = (bx - 11, 14, bx + 11, CARD_H - 14)

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
                                  self._current_width(), CARD_H, margin=8)
        self.pos = [x, y]
        user32.SetWindowPos(self.hwnd, HWND_BOTTOM, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

    def _on_release(self, e):
        was_drag = self._dragging
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False
        if was_drag:
            self.anchor = capture_anchor(self.pos[0], self.pos[1], self._current_width(), CARD_H)
            self.on_save()
            return
        action = self._hit_button(e.x, e.y)
        if action == 'pin':
            self.locked = not self.locked
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
            if st['active'] and st['key'] != self._cover_key:
                self._cover_key = st['key']
                self._cover_photo = _make_rounded_cover(st['thumb'], COVER_SIZE)
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

        # 判断是否应该显示：仅在图标隐藏（与桌面时钟一致）、无全屏程序、且有活跃媒体时显示
        paused_too_long = (
            self._paused_since is not None
            and not self.locked
            and not self._hover
            and (now - self._paused_since) > PAUSE_HIDE_SECONDS
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
                x, y = resolve_anchor(self.anchor, self.pos, self._current_width(), CARD_H, margin=8)
                self.pos = [x, y]
                self.win.geometry(f'{x:+d}{y:+d}')
                self._render()
                user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(self.hwnd, HWND_BOTTOM, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
        elif visible:
            self.win.withdraw()
