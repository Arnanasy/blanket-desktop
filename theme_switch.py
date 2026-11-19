"""Windows 深色/浅色主题一键切换。修改注册表并广播通知，应用和浏览器同时生效。"""
import ctypes
from ctypes import wintypes
import winreg

_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"

_u = ctypes.windll.user32
_u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPCWSTR, wintypes.UINT, wintypes.UINT,
                                    ctypes.POINTER(wintypes.DWORD)]


def is_dark():
    """当前是否为深色模式。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def set_theme(dark):
    """切换到深色(True)或浅色(False)，广播通知所有窗口（含浏览器）。"""
    val = 0 if dark else 1
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            # AppsUseLightTheme 控制应用和浏览器，SystemUsesLightTheme 控制任务栏和开始菜单
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, val)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, val)
    except OSError:
        return False
    # 广播 WM_SETTINGCHANGE + ImmersiveColorSet，让所有应用和浏览器立即响应
    result = wintypes.DWORD()
    _u.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet",
                           0x0002, 1000, ctypes.byref(result))
    return True


def toggle():
    """一键切换深色/浅色，返回切换后是否为深色（以实际状态为准）。"""
    dark = not is_dark()
    set_theme(dark)
    return is_dark()
