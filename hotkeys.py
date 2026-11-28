"""全局快捷键解析、Win32 虚拟键映射与防冲突规范化（纯标准库）。"""
from win32 import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN

DEFAULT_HOTKEYS = {
    'icons': 'Ctrl+Alt+H',
    'theme': 'Ctrl+Alt+T',
    'screen_off': '',
    'awake': '',
}

HOTKEY_ACTIONS = ('icons', 'theme', 'screen_off', 'awake')

HOTKEY_LABELS = {
    'icons': '切换桌面图标',
    'theme': '切换深浅主题',
    'screen_off': '一键息屏',
    'awake': '切换屏幕常亮',
}

HOTKEY_IDS = {
    'icons': 1,
    'theme': 2,
    'screen_off': 3,
    'awake': 4,
}

_MOD_ALIASES = {
    'ctrl': ('Ctrl', MOD_CONTROL),
    'control': ('Ctrl', MOD_CONTROL),
    'alt': ('Alt', MOD_ALT),
    'menu': ('Alt', MOD_ALT),
    'shift': ('Shift', MOD_SHIFT),
    'win': ('Win', MOD_WIN),
    'super': ('Win', MOD_WIN),
}

_MOD_ORDER = ('Ctrl', 'Alt', 'Shift', 'Win')

_KEY_VK = {}
for _ch in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
    _KEY_VK[_ch] = (_ch, ord(_ch))
for _d in '0123456789':
    _KEY_VK[_d] = (_d, ord(_d))
for _fn in range(1, 13):
    _KEY_VK[f'F{_fn}'] = (f'F{_fn}', 0x70 + (_fn - 1))

_EXTRA_KEYS = {
    'SPACE': ('Space', 0x20),
    'TAB': ('Tab', 0x09),
    'ENTER': ('Enter', 0x0D),
    'RETURN': ('Enter', 0x0D),
    'INSERT': ('Insert', 0x2D),
    'DELETE': ('Delete', 0x2E),
    'DEL': ('Delete', 0x2E),
    'HOME': ('Home', 0x24),
    'END': ('End', 0x23),
    'PAGEUP': ('PageUp', 0x21),
    'PRIOR': ('PageUp', 0x21),
    'PAGEDOWN': ('PageDown', 0x22),
    'NEXT': ('PageDown', 0x22),
    'UP': ('Up', 0x26),
    'DOWN': ('Down', 0x28),
    'LEFT': ('Left', 0x25),
    'RIGHT': ('Right', 0x27),
    'MINUS': ('-', 0xBD),
    '-': ('-', 0xBD),
    'EQUAL': ('=', 0xBB),
    '=': ('=', 0xBB),
    'BRACKETLEFT': ('[', 0xDB),
    '[': ('[', 0xDB),
    'BRACKETRIGHT': (']', 0xDD),
    ']': (']', 0xDD),
    'SEMICOLON': (';', 0xBA),
    ';': (';', 0xBA),
    'APOSTROPHE': ("'", 0xDE),
    "'": ("'", 0xDE),
    'COMMA': (',', 0xBC),
    ',': (',', 0xBC),
    'PERIOD': ('.', 0xBE),
    '.': ('.', 0xBE),
    'SLASH': ('/', 0xBF),
    '/': ('/', 0xBF),
    'BACKSLASH': ('\\', 0xDC),
    '\\': ('\\', 0xDC),
    'GRAVE': ('`', 0xC0),
    '`': ('`', 0xC0),
}
_KEY_VK.update(_EXTRA_KEYS)

_VK_TO_NAME = {}
for _canon, _vk in _KEY_VK.values():
    _VK_TO_NAME.setdefault(_vk, _canon)


def parse_hotkey(spec):
    """解析快捷键字符串 -> (win32_mods_with_norepeat, vk, canonical_str)；空串或非法返回 None。

    安全限制：非 F1-F12 键至少需要包含一个修饰键（Ctrl/Alt/Shift/Win），避免单字母占用日常打字。
    """
    if not isinstance(spec, str):
        return None
    text = spec.strip()
    if not text:
        return None
    parts = [p.strip() for p in text.split('+') if p.strip()]
    if not parts:
        return None

    mods_found = {}
    key_info = None
    for part in parts:
        low = part.lower()
        if low in _MOD_ALIASES:
            canon_mod, flag = _MOD_ALIASES[low]
            mods_found[canon_mod] = flag
        else:
            if key_info is not None:
                return None
            key_info = _KEY_VK.get(part.upper())
            if key_info is None:
                return None

    if key_info is None:
        return None
    canon_key, vk = key_info
    is_func = bool(canon_key.startswith('F') and canon_key[1:].isdigit())
    if not mods_found and not is_func:
        return None

    mod_names = [m for m in _MOD_ORDER if m in mods_found]
    mod_bits = MOD_NOREPEAT
    for m in mod_names:
        mod_bits |= mods_found[m]
    canonical = '+'.join(mod_names + [canon_key])
    return mod_bits, vk, canonical


def format_hotkey_from_vk(ctrl=False, alt=False, shift=False, win=False, vk=0, keysym=''):
    """将键盘录制得到的修饰键与虚拟键/keysym 格式化为规范快捷键串；无效返回 None。"""
    canon_key = _VK_TO_NAME.get(int(vk))
    if canon_key is None and isinstance(keysym, str) and keysym:
        found = _KEY_VK.get(keysym.upper())
        if found:
            canon_key = found[0]
    if not canon_key:
        return None
    mods = []
    if ctrl:
        mods.append('Ctrl')
    if alt:
        mods.append('Alt')
    if shift:
        mods.append('Shift')
    if win:
        mods.append('Win')
    candidate = '+'.join(mods + [canon_key])
    parsed = parse_hotkey(candidate)
    return parsed[2] if parsed else None


def sanitize_hotkeys(raw):
    """清洗全局快捷键配置；合法空串保留（表示禁用），重复组合仅保留最先出现的动作。"""
    cfg = dict(DEFAULT_HOTKEYS)
    if isinstance(raw, dict):
        for action in HOTKEY_ACTIONS:
            if action not in raw:
                continue
            val = raw[action]
            if isinstance(val, str) and not val.strip():
                cfg[action] = ''
                continue
            parsed = parse_hotkey(val)
            if parsed is not None:
                cfg[action] = parsed[2]
    seen = set()
    for action in HOTKEY_ACTIONS:
        val = cfg[action]
        if not val:
            continue
        if val in seen:
            cfg[action] = ''
        else:
            seen.add(val)
    return cfg
