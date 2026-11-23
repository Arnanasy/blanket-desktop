"""Windows 深色/浅色主题一键切换。修改注册表并广播通知，应用和浏览器同时生效。"""
import ctypes
from threading import Thread
import winreg

from win32 import user32, HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG

_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize'


def is_dark():
    """当前是否为深色模式。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as key:
            return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except OSError:
        return False


def set_theme(dark):
    """切换到深色(True)或浅色(False)，并广播通知所有窗口（含浏览器）。"""
    value = 0 if dark else 1
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_SET_VALUE) as key:
            # AppsUseLightTheme 控制应用和浏览器，SystemUsesLightTheme 控制任务栏和开始菜单
            winreg.SetValueEx(key, 'AppsUseLightTheme', 0, winreg.REG_DWORD, value)
            winreg.SetValueEx(key, 'SystemUsesLightTheme', 0, winreg.REG_DWORD, value)
    except OSError:
        return False
    # 广播可能被无响应的窗口拖慢，放到后台线程避免卡住界面
    Thread(target=_notify, name='theme-broadcast', daemon=True).start()
    return True


def _notify():
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, 'ImmersiveColorSet',
                               SMTO_ABORTIFHUNG, 1000, ctypes.byref(result))


def toggle():
    """一键切换深色/浅色，返回切换后是否为深色（以实际状态为准）。"""
    if not set_theme(not is_dark()):
        raise OSError('Windows 拒绝写入主题设置。')
    return is_dark()
