"""配置持久化与开机自启。"""
import json
import os
import sys
import winreg

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 开机自启
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'JingZhuoMian'


def _pythonw_path():
    """返回与当前解释器对应的 pythonw.exe（无控制台）路径。"""
    exe = sys.executable
    if os.path.basename(exe).lower() == 'python.exe':
        candidate = os.path.join(os.path.dirname(exe), 'pythonw.exe')
        if os.path.exists(candidate):
            return candidate
    return exe


def autostart_command():
    return '"{}" "{}"'.format(_pythonw_path(), os.path.join(APP_DIR, 'launch.pyw'))


def _registered_command():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, RUN_VALUE)[0])
    except OSError:
        return None


def _script_of(command):
    """从注册表命令行里取出被启动的脚本路径（第二个带引号的参数）。"""
    parts = [p for p in command.split('"') if p.strip()]
    return os.path.normcase(os.path.abspath(parts[-1])) if len(parts) >= 2 else ''


def autostart_enabled():
    """开机自启是否已开启，并且指向当前这份程序。"""
    command = _registered_command()
    return command is not None and _script_of(command) == _script_of(autostart_command())


def repair_autostart():
    """自启项存在但指向旧位置（例如程序文件夹被移动过）时，改写为当前路径。返回是否改写。"""
    command = _registered_command()
    if command is None or autostart_enabled():
        return False
    return set_autostart(True)


def set_autostart(enable):
    """开启/关闭开机自启，成功返回 True。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 配置持久化
CONFIG_PATH = os.path.join(APP_DIR, 'config.json')
DEFAULT_CONFIG = {
    'auto': True,
    'zone': 'top',
    'delay': '10',
}


def load_config():
    """读取配置，缺失或损坏时返回默认值。"""
    def read_dict(path):
        try:
            with open(path, encoding='utf-8-sig') as source:
                data = json.load(source)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    merged = {**DEFAULT_CONFIG, **read_dict(CONFIG_PATH)}
    if not isinstance(merged.get('widgets'), dict):
        # 兼容旧版本单独保存的挂件配置
        merged['widgets'] = read_dict(os.path.join(APP_DIR, 'desktop_widgets.json'))
    if not isinstance(merged.get('last_visible'), bool):
        merged.pop('last_visible', None)
    if not isinstance(merged['auto'], bool):
        merged['auto'] = DEFAULT_CONFIG['auto']
    if merged['zone'] not in ('top', 'any', 'corner'):
        merged['zone'] = DEFAULT_CONFIG['zone']
    delay = str(merged['delay'])
    merged['delay'] = delay if delay in ('5', '10', '20') else DEFAULT_CONFIG['delay']
    return merged


def save_config(cfg):
    """原子写入配置（先写临时文件再替换），失败静默。"""
    temp = CONFIG_PATH + '.tmp'
    try:
        with open(temp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(temp, CONFIG_PATH)
    except (OSError, TypeError, ValueError):
        try:
            os.remove(temp)
        except OSError:
            pass
