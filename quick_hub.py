"""右侧快捷中枢核心能力：最新文件自动速取、一键复制文件到剪贴板、随手便签清洗、倒计时格式化、屏幕常亮/息屏与本地工具箱联动。"""
import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser
import winreg

from win32 import (user32, kernel32, HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER,
                   ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED,
                   CF_HDROP, GMEM_MOVEABLE)

_SHELL_FOLDERS_KEY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
_DOWNLOADS_GUID = '{374DE290-123F-4565-9164-39C4925E467B}'
_FILE_ATTR_HIDDEN = 0x2
_FILE_ATTR_SYSTEM = 0x4

_SKIP_NAMES = {'desktop.ini', 'thumbs.db'}
_SKIP_EXTS = {'.lnk', '.url', '.tmp', '.crdownload', '.part', '.opdownload', '.download'}

_EXT_ICONS = {
    '.png': '🖼️', '.jpg': '🖼️', '.jpeg': '🖼️', '.webp': '🖼️', '.gif': '🖼️', '.heic': '🖼️', '.bmp': '🖼️', '.svg': '🖼️',
    '.mp4': '🎬', '.mkv': '🎬', '.mov': '🎬', '.avi': '🎬', '.flv': '🎬', '.webm': '🎬',
    '.mp3': '🎵', '.wav': '🎵', '.flac': '🎵', '.m4a': '🎵', '.ogg': '🎵',
    '.pdf': '📕', '.doc': '📘', '.docx': '📘', '.xls': '📗', '.xlsx': '📗', '.csv': '📗', '.ppt': '📙', '.pptx': '📙',
    '.zip': '📦', '.rar': '📦', '.7z': '📦', '.tar': '📦', '.gz': '📦',
    '.exe': '💿', '.msi': '💿',
    '.txt': '📝', '.md': '📝', '.json': '📝',
}

MAX_NOTES = 10
MAX_NOTE_LEN = 60


def _user_folder(reg_name, fallback_sub):
    """读取当前用户的真实系统文件夹路径（兼容用户将桌面/下载迁移到 D 盘的场景）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SHELL_FOLDERS_KEY) as key:
            raw, _ = winreg.QueryValueEx(key, reg_name)
        if isinstance(raw, str) and raw.strip():
            expanded = Path(os.path.expandvars(raw.strip()))
            if expanded.is_dir():
                return expanded
    except OSError:
        pass
    fb = Path.home() / fallback_sub
    return fb if fb.is_dir() else None


def default_watch_dirs():
    """返回 [(来源标签, Path), ...]，默认扫描下载与桌面文件夹。"""
    pairs = []
    seen = set()
    for label, reg_name, sub in (
        ('下载', _DOWNLOADS_GUID, 'Downloads'),
        ('桌面', 'Desktop', 'Desktop'),
    ):
        p = _user_folder(reg_name, sub)
        if p is not None:
            norm = os.path.normcase(str(p.resolve()))
            if norm not in seen:
                seen.add(norm)
                pairs.append((label, p))
    return pairs


def file_icon(name):
    return _EXT_ICONS.get(Path(name).suffix.lower(), '📄')


def scan_recent_files(limit=5, dir_pairs=None):
    """极速扫描指定目录顶层最新产生的文件（自动过滤隐藏文件、Office 锁文件、快捷方式与未下完临时文件）。"""
    if dir_pairs is None:
        dir_pairs = default_watch_dirs()
    candidates = []
    seen_paths = set()

    for source, folder in dir_pairs:
        p_dir = Path(folder)
        if not p_dir.is_dir():
            continue
        try:
            with os.scandir(p_dir) as it:
                for entry in it:
                    name = entry.name
                    low = name.lower()
                    if not name or name.startswith(('.', '~$')) or low in _SKIP_NAMES:
                        continue
                    ext = os.path.splitext(low)[1]
                    if ext in _SKIP_EXTS:
                        continue
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    attrs = getattr(st, 'st_file_attributes', 0)
                    if attrs & (_FILE_ATTR_HIDDEN | _FILE_ATTR_SYSTEM):
                        continue
                    norm = os.path.normcase(entry.path)
                    if norm in seen_paths:
                        continue
                    seen_paths.add(norm)
                    candidates.append({
                        'name': name,
                        'path': entry.path,
                        'mtime': st.st_mtime,
                        'source': source,
                        'icon': file_icon(name),
                    })
        except OSError:
            continue

    candidates.sort(key=lambda item: item['mtime'], reverse=True)
    return candidates[:max(1, int(limit))]


def reveal_in_explorer(path_str):
    """在资源管理器中打开并高亮选中目标文件。"""
    p = Path(path_str)
    if p.exists():
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        subprocess.Popen(['explorer.exe', f'/select,{p}'], creationflags=creationflags)
        return True
    return False


class _DROPFILES(ctypes.Structure):
    _fields_ = [
        ('pFiles', wintypes.DWORD),
        ('pt', wintypes.POINT),
        ('fNC', wintypes.BOOL),
        ('fWide', wintypes.BOOL),
    ]


def build_cf_hdrop_bytes(path_str):
    """构造 Windows 标准 CF_HDROP 剪贴板内存载荷（UTF-16LE 双空结尾），支持微信/QQ/资源管理器直接 Ctrl+V 粘贴文件。"""
    resolved = str(Path(path_str).resolve())
    header = _DROPFILES()
    header.pFiles = ctypes.sizeof(_DROPFILES)
    header.fWide = True
    # 单个 UTF-16LE 路径以 \x00\x00 结尾，文件列表再以额外 \x00\x00 结束
    path_bytes = resolved.encode('utf-16-le') + b'\x00\x00\x00\x00'
    return bytes(header) + path_bytes


def copy_file_to_clipboard(path_str, owner_hwnd=None):
    """将本地文件本体（CF_HDROP）写入系统剪贴板，成功返回 True。"""
    p = Path(path_str)
    if not p.is_file():
        return False
    payload = build_cf_hdrop_bytes(p)
    h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not h_global:
        return False
    ptr = kernel32.GlobalLock(h_global)
    if not ptr:
        kernel32.GlobalFree(h_global)
        return False
    ctypes.memmove(ptr, payload, len(payload))
    kernel32.GlobalUnlock(h_global)

    opened = False
    for _ in range(3):
        if user32.OpenClipboard(owner_hwnd):
            opened = True
            break
        time.sleep(0.01)
    if not opened:
        kernel32.GlobalFree(h_global)
        return False

    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_HDROP, h_global):
            kernel32.GlobalFree(h_global)
            return False
        # SetClipboardData 成功后 h_global 所有权归属系统剪贴板，严禁再调用 GlobalFree
        return True
    finally:
        user32.CloseClipboard()


def sanitize_notes(raw):
    """清洗随手便签列表，过滤损坏项并限制条数与字数。"""
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw:
        if isinstance(item, dict):
            txt = str(item.get('text', '')).strip()[:MAX_NOTE_LEN]
            if txt:
                cleaned.append({'text': txt, 'done': bool(item.get('done', False))})
        elif isinstance(item, str) and item.strip():
            cleaned.append({'text': item.strip()[:MAX_NOTE_LEN], 'done': False})
        if len(cleaned) >= MAX_NOTES:
            break
    return cleaned


def format_countdown(remaining_seconds, label='倒计时'):
    """将剩余秒数格式化为桌面时钟附属展示文本（如 '⏳ 倒计时 24:59'）。"""
    if remaining_seconds is None or remaining_seconds <= 0:
        return ''
    total = max(1, int(math.ceil(float(remaining_seconds))))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    time_str = f'{h}:{m:02d}:{s:02d}' if h > 0 else f'{m:02d}:{s:02d}'
    prefix = f'⏳ {label} ' if label else '⏳ '
    return prefix + time_str


class KeepAwake:
    """基于 SetThreadExecutionState 的零权限屏幕常亮控制（退出自动释放）。"""

    def __init__(self):
        self.active = False

    def set_enabled(self, enable):
        enable = bool(enable)
        flags = (ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED) if enable else ES_CONTINUOUS
        if kernel32.SetThreadExecutionState(flags):
            self.active = enable
        return self.active

    def toggle(self):
        return self.set_enabled(not self.active)

    def release(self):
        if self.active:
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            self.active = False


def turn_off_monitors():
    """向系统广播关闭显示器电源消息（动一下鼠标或按键即可唤醒）。"""
    user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def _running_deskkit_port(start_port=18642, span=6):
    for port in range(start_port, start_port + span):
        try:
            with urlopen(f'http://127.0.0.1:{port}/api/health', timeout=0.15) as resp:
                if resp.status == 200:
                    return port
        except OSError:
            continue
    return None


def find_deskkit_entry(saved_path='', app_dir=''):
    """按已保存路径 -> 同级目录 -> 桌面目录自动搜寻 DeskKit 启动入口（零硬编码本机路径）。"""
    if saved_path:
        p = Path(saved_path)
        if p.is_file():
            return p
        if p.is_dir():
            for name in ('DeskKit.exe', 'DeskKit.cmd', 'launch.pyw'):
                cand = p / name
                if cand.is_file():
                    return cand

    roots = []
    if app_dir:
        roots.append(Path(app_dir).resolve().parent)
    desk = _user_folder('Desktop', 'Desktop')
    if desk and desk not in roots:
        roots.append(desk)

    for root in roots:
        for folder_name in ('本地工具箱', 'DeskKit', 'deskkit'):
            d = root / folder_name
            if not d.is_dir():
                continue
            for entry_name in ('DeskKit.exe', 'DeskKit.cmd', 'launch.pyw'):
                cand = d / entry_name
                if cand.is_file():
                    return cand
    return None


def launch_deskkit(saved_path='', app_dir=''):
    """一键打开或拉起本地工具箱 DeskKit。返回 (是否成功, 解析到的入口路径字符串)。"""
    port = _running_deskkit_port()
    if port:
        webbrowser.open(f'http://127.0.0.1:{port}/')
        return True, saved_path

    entry = find_deskkit_entry(saved_path, app_dir)
    if entry is None:
        return False, ''

    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    ext = entry.suffix.lower()
    if ext == '.exe':
        subprocess.Popen([str(entry)], cwd=str(entry.parent))
    elif ext == '.cmd':
        subprocess.Popen(['cmd.exe', '/c', str(entry)], cwd=str(entry.parent), creationflags=creationflags)
    elif ext == '.pyw':
        pyw = Path(sys.executable)
        if pyw.name.lower() == 'python.exe':
            cand_pyw = pyw.with_name('pythonw.exe')
            if cand_pyw.is_file():
                pyw = cand_pyw
        if not getattr(sys, 'frozen', False):
            subprocess.Popen([str(pyw), '-B', str(entry)], cwd=str(entry.parent), creationflags=creationflags)
        else:
            os.startfile(str(entry))
    else:
        os.startfile(str(entry))
    return True, str(entry)
