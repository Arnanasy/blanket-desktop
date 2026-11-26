"""可选实时桌面同步歌词条：支持独立拖动 / 紧贴音乐胶囊下方、单行 / 双行与时间轴偏移微调。"""
import bisect
import json
import logging
import re
from threading import Thread
import tkinter as tk
from tkinter import font as tkfont
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from widget_layout import capture_anchor, clamp_to_work_area, resolve_anchor
from win32 import (toplevel_hwnd, add_exstyle, set_click_through, place_on_desktop,
                   WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW)

log = logging.getLogger('desktop')

KEY_COLOR = '#010203'
DRAG_THRESHOLD = 5
LYRICS_MODES = ('独立拖动', '紧贴胶囊下方')
LYRICS_LINE_OPTIONS = ('双行', '单行')

LYRICS_DEFAULTS = dict(
    lyrics_enabled=True,
    lyrics_mode='独立拖动',          # 独立拖动 / 紧贴胶囊下方
    lyrics_lines='双行',             # 双行 / 单行
    lyrics_size=22,                 # 字号（14..42）
    lyrics_offset=0.0,              # 时间轴偏移秒数（-3.0..3.0）
    lyrics_font='Microsoft YaHei UI',  # 歌词字体
    lyrics_color='#F8FAFC',         # 当前句文字颜色
    lyrics_next_color='#CBD5E1',    # 下一句文字颜色
    lyrics_bold=True,               # 当前句是否加粗
)

_LRC_TAG_RE = re.compile(r'\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]')
_CLEAN_RE = re.compile(r'\s*[\(\[（【].*?[\)\]）】]\s*')


def parse_lrc(lrc_text):
    """解析标准 LRC 文本，返回按时间升序排列的 [(seconds, text), ...]。"""
    if not isinstance(lrc_text, str) or not lrc_text.strip():
        return []
    rows = []
    for raw in lrc_text.splitlines():
        tags = list(_LRC_TAG_RE.finditer(raw))
        if not tags:
            continue
        content = _LRC_TAG_RE.sub('', raw).strip()
        if not content:
            continue
        for m in tags:
            minutes = int(m.group(1))
            seconds = int(m.group(2))
            frac = m.group(3) or '0'
            ms = int(frac.ljust(3, '0')[:3])
            rows.append((minutes * 60.0 + seconds + ms / 1000.0, content))
    rows.sort(key=lambda item: item[0])
    return rows


def current_lyric_lines(lines, pos_sec):
    """根据当前播放秒数返回 (当前句, 下一句)；尚未到第一句时返回 ('', 第一句)。"""
    if not lines:
        return '', ''
    times = [t for t, _ in lines]
    idx = bisect.bisect_right(times, float(pos_sec)) - 1
    if idx < 0:
        return '', lines[0][1]
    cur = lines[idx][1]
    nxt = lines[idx + 1][1] if idx + 1 < len(lines) else ''
    return cur, nxt


def _http_json(url, timeout=5.0, headers=None):
    hdrs = {'User-Agent': 'Blanket/1.0 (Desktop Lyrics)'}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(250000).decode('utf-8', 'replace'))


def _fetch_netease_lyrics(title, artist, duration=0.0):
    """国内免费歌词源：网易云音乐公开搜索 + 歌词接口，中文歌命中率高。"""
    hdrs = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://music.163.com/'}
    short_title = _CLEAN_RE.sub('', title).strip() or title
    first_artist = re.split(r'[,/&、，]', artist)[0].strip() if artist else ''
    queries = []
    for q in (f'{title} {first_artist}'.strip(), f'{short_title} {first_artist}'.strip(), short_title):
        if q and q not in queries:
            queries.append(q)

    for q in queries:
        try:
            data = _http_json(
                'https://music.163.com/api/search/get/web?' + urlencode({'s': q, 'type': 1, 'limit': 5}),
                timeout=4.5, headers=hdrs,
            )
            songs = data.get('result', {}).get('songs') if isinstance(data, dict) else None
            if not isinstance(songs, list) or not songs:
                continue
            candidates = [s for s in songs if isinstance(s, dict) and s.get('id')]
            if not candidates:
                continue
            if duration and duration > 10:
                candidates.sort(key=lambda s: abs(float(s.get('duration') or 0) / 1000.0 - duration))
            for song in candidates[:2]:
                lrc_data = _http_json(
                    f"https://music.163.com/api/song/lyric?id={song['id']}&lv=1&kv=1&tv=-1",
                    timeout=4.5, headers=hdrs,
                )
                lrc_text = lrc_data.get('lrc', {}).get('lyric', '') if isinstance(lrc_data, dict) else ''
                parsed = parse_lrc(lrc_text)
                if parsed:
                    return parsed
        except Exception:
            continue
    return []


def _fetch_lrclib_lyrics(title, artist, duration=0.0):
    """海外免费歌词源 lrclib.net（作为国内源未命中时的回退）。"""
    # 1. 精确查询
    try:
        params = {'track_name': title}
        if artist:
            params['artist_name'] = artist
        if duration and duration > 10:
            params['duration'] = round(duration)
        data = _http_json('https://lrclib.net/api/get?' + urlencode(params))
        if isinstance(data, dict) and data.get('syncedLyrics'):
            parsed = parse_lrc(data['syncedLyrics'])
            if parsed:
                return parsed
    except Exception:
        pass

    # 2. 模糊搜索（去掉括号副标题再试，优先选带 syncedLyrics 且时长最接近的）
    short_title = _CLEAN_RE.sub('', title).strip() or title
    first_artist = re.split(r'[,/&、，]', artist)[0].strip() if artist else ''
    for q in (f'{short_title} {first_artist}'.strip(), short_title):
        try:
            items = _http_json('https://lrclib.net/api/search?' + urlencode({'q': q}))
            if not isinstance(items, list):
                continue
            candidates = [it for it in items if isinstance(it, dict) and it.get('syncedLyrics')]
            if not candidates:
                continue
            if duration and duration > 10:
                candidates.sort(key=lambda it: abs(float(it.get('duration') or 0) - duration))
            parsed = parse_lrc(candidates[0]['syncedLyrics'])
            if parsed:
                return parsed
        except Exception:
            continue
    return []


def fetch_synced_lyrics(title, artist, duration=0.0):
    """优先从国内免费源查询同步 LRC，未命中再回退到 lrclib.net；失败返回空列表。"""
    clean_title = (title or '').strip()
    clean_artist = (artist or '').strip()
    if not clean_title or clean_title in ('系统音频播放中', '系统音频已暂停'):
        return []
    parsed = _fetch_netease_lyrics(clean_title, clean_artist, duration)
    if parsed:
        return parsed
    return _fetch_lrclib_lyrics(clean_title, clean_artist, duration)


class LyricsOverlay:
    """透明桌面同步歌词浮窗，支持独立拖动或紧贴音乐胶囊下方、单行/双行。"""

    def __init__(self, media_widget, cfg):
        self.media = media_widget
        self.root = media_widget.root
        cfg = cfg if isinstance(cfg, dict) else {}

        pos = cfg.get('lyrics_position')
        if not (isinstance(pos, list) and len(pos) == 2 and all(type(v) is int for v in pos)):
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            pos = [max(40, (sw - 520) // 2), max(40, sh - 160)]
        self.pos = list(pos)
        self.anchor = cfg.get('lyrics_anchor') if isinstance(cfg.get('lyrics_anchor'), dict) else None

        self._cache = {}           # (title, artist) -> [(sec, text), ...]
        self._fetching = set()
        self._active_key = None
        self._lines = []
        self._last_cur = ''
        self._last_nxt = ''
        self._last_drawn = None
        self._last_alpha = None
        self._font_key = None
        self._main_font = None
        self._sub_font = None

        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False
        self._guarded = False

        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=KEY_COLOR)
        self.win.wm_attributes('-transparentcolor', KEY_COLOR)

        self.canvas = tk.Canvas(self.win, width=300, height=48,
                                bg=KEY_COLOR, highlightthickness=0, cursor='hand2')
        self.canvas.pack()
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<ButtonRelease-3>', lambda e: None if self._guarded else self.media.open_settings())

        self.hwnd = toplevel_hwnd(self.win)
        add_exstyle(self.hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

    def config(self):
        return {
            'lyrics_position': list(self.pos),
            'lyrics_anchor': self.anchor,
        }

    def _request_lyrics(self, key, title, artist, duration):
        if key in self._cache or key in self._fetching:
            return
        self._fetching.add(key)

        def job():
            try:
                lines = fetch_synced_lyrics(title, artist, duration)
            except Exception:
                lines = []
            self._cache[key] = lines
            self._fetching.discard(key)

        Thread(target=job, name='lyrics', daemon=True).start()

    def _ensure_fonts(self, size, family='Microsoft YaHei UI', bold=True):
        size = max(14, min(42, int(size)))
        sub_size = max(11, round(size * 0.72))
        family = (family or 'Microsoft YaHei UI').strip() or 'Microsoft YaHei UI'
        weight = 'bold' if bold else 'normal'
        key = (size, sub_size, family, weight)
        if key != self._font_key:
            self._main_font = tkfont.Font(family=family, size=size, weight=weight)
            self._sub_font = tkfont.Font(family=family, size=sub_size)
            self._font_key = key

    def _render(self, cur, nxt, prefs):
        size = prefs['lyrics_size']
        two_lines = prefs['lyrics_lines'] == '双行'
        fg = prefs.get('lyrics_color') or prefs['fg']
        next_fg = prefs.get('lyrics_next_color') or '#CBD5E1'
        accent = prefs['accent']
        self._ensure_fonts(size, prefs.get('lyrics_font', 'Microsoft YaHei UI'),
                           prefs.get('lyrics_bold', True))

        main_text = cur or nxt
        sub_text = nxt if (two_lines and cur and nxt) else ''
        if not main_text:
            return False

        pad_x = 18
        pad_y = 8
        mw = self._main_font.measure(main_text)
        mh = self._main_font.metrics('linespace')
        sw = self._sub_font.measure(sub_text) if sub_text else 0
        sh = self._sub_font.metrics('linespace') if sub_text else 0
        w = max(160, max(mw, sw) + pad_x * 2)
        h = mh + (sh + 4 if sub_text else 0) + pad_y * 2

        alpha = prefs['opacity'] / 100.0
        if alpha != self._last_alpha:
            self._last_alpha = alpha
            self.win.attributes('-alpha', alpha)

        c = self.canvas
        c.configure(width=w, height=h)
        c.delete('all')

        def draw_outlined(x, y, text, fnt, fill):
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
                c.create_text(x + dx, y + dy, text=text, font=fnt, fill='#0b0f19', anchor='n')
            c.create_text(x, y, text=text, font=fnt, fill=fill, anchor='n')

        draw_outlined(w // 2, pad_y, main_text, self._main_font, fg if cur else accent)
        if sub_text:
            draw_outlined(w // 2, pad_y + mh + 4, sub_text, self._sub_font, next_fg)
        return True

    def _target_xy(self, prefs):
        w = self.canvas.winfo_reqwidth()
        h = self.canvas.winfo_reqheight()
        if prefs['lyrics_mode'] == '紧贴胶囊下方':
            mx, my = self.media.pos[0], self.media.pos[1]
            mh = self.media._card_h()
            return clamp_to_work_area(mx, my + mh + 8, w, h, margin=8)
        return resolve_anchor(self.anchor, self.pos, w, h, margin=8)

    def refresh_now(self):
        """设置变更后立即重绘当前桌面歌词，不等下一帧或下一句切换。"""
        self._last_drawn = None
        if not self.win.winfo_viewable():
            return
        cur, nxt = self._last_cur, self._last_nxt
        if not (cur or nxt):
            return
        prefs = self.media.prefs
        if self._render(cur, nxt, prefs):
            x, y = self._target_xy(prefs)
            if prefs['lyrics_mode'] == '独立拖动':
                self.pos = [x, y]
            self.win.geometry(f'{x:+d}{y:+d}')
            place_on_desktop(self.hwnd, x, y, on_desktop=True, show=True)

    def relayout(self, on_desktop=False):
        if not self.win.winfo_viewable():
            return
        x, y = self._target_xy(self.media.prefs)
        if self.media.prefs['lyrics_mode'] == '独立拖动':
            self.pos = [x, y]
        place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop)

    def _on_press(self, e):
        if self._guarded or self.media.prefs['lyrics_mode'] != '独立拖动':
            return
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
        w, h = self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight()
        x, y = clamp_to_work_area(e.x_root - self._drag_offset[0],
                                  e.y_root - self._drag_offset[1], w, h, margin=8)
        self.pos = [x, y]
        place_on_desktop(self.hwnd, x, y, on_desktop=True)

    def _on_release(self, _e):
        was_drag = self._dragging
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False
        if was_drag:
            w, h = self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight()
            self.anchor = capture_anchor(self.pos[0], self.pos[1], w, h)
            self.media.on_save()

    def tick(self, state, pos_sec, should_show, on_desktop, force_restack=False, guard=False):
        if bool(guard) != self._guarded:
            self._guarded = bool(guard)
            set_click_through(self.hwnd, self._guarded)
        prefs = self.media.prefs
        if not (should_show and prefs['lyrics_enabled'] and state['active']):
            if self.win.winfo_viewable():
                self.win.withdraw()
            return

        key = state['key']
        if key != self._active_key:
            self._active_key = key
            self._last_drawn = None
            if key in self._cache:
                self._lines = self._cache[key]
            else:
                self._lines = []
                self._request_lyrics(key, state['title'], state['artist'], state.get('duration', 0.0))
        elif not self._lines and key in self._cache:
            self._lines = self._cache[key]

        if not self._lines:
            if self.win.winfo_viewable():
                self.win.withdraw()
            return

        adj_pos = max(0.0, float(pos_sec) + float(prefs['lyrics_offset']))
        cur, nxt = current_lyric_lines(self._lines, adj_pos)
        self._last_cur, self._last_nxt = cur, nxt
        draw_sig = (
            cur, nxt, prefs['lyrics_size'], prefs['lyrics_lines'],
            prefs.get('lyrics_font'), prefs.get('lyrics_color'),
            prefs.get('lyrics_next_color'), prefs.get('lyrics_bold'),
            prefs['fg'], prefs['accent'], prefs['opacity'], prefs['lyrics_mode'],
        )
        visible = bool(self.win.winfo_viewable())

        if draw_sig != self._last_drawn:
            self._last_drawn = draw_sig
            if not self._render(cur, nxt, prefs):
                if visible:
                    self.win.withdraw()
                return
            x, y = self._target_xy(prefs)
            if prefs['lyrics_mode'] == '独立拖动':
                self.pos = [x, y]
            self.win.geometry(f'{x:+d}{y:+d}')
            place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop, show=True)
            set_click_through(self.hwnd, self._guarded)
        elif not visible or force_restack:
            x, y = self._target_xy(prefs)
            if prefs['lyrics_mode'] == '独立拖动':
                self.pos = [x, y]
            if not visible:
                self.win.geometry(f'{x:+d}{y:+d}')
            place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop, show=True)
            set_click_through(self.hwnd, self._guarded)
        elif prefs['lyrics_mode'] == '紧贴胶囊下方' and not self._dragging:
            x, y = self._target_xy(prefs)
            if abs(x - self.win.winfo_x()) > 1 or abs(y - self.win.winfo_y()) > 1:
                place_on_desktop(self.hwnd, x, y, on_desktop=on_desktop)
