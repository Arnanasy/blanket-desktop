"""配置持久化与开机自启。"""
import json
import logging
import os
import sys
import winreg

log = logging.getLogger('desktop')

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 开机自启
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
STARTUP_APPROVED_KEY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
RUN_VALUE = 'JingZhuoMian'
_STARTUP_ENABLED_BYTES = bytes([0x02] + [0x00] * 11)


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


def _command_parts(command):
    """从注册表命令行里取出规范化后的 (解释器路径, 脚本路径)（兼容带引号与无引号格式）。"""
    import shlex
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    cleaned = [p.strip('"') for p in parts if p.strip('"')]
    if len(cleaned) < 2:
        return '', ''
    return (
        os.path.normcase(os.path.abspath(cleaned[0])),
        os.path.normcase(os.path.abspath(cleaned[-1])),
    )


def _startup_approved():
    """检查启动项是否未被 Windows「任务管理器 / 系统设置」禁用。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY) as key:
            data, _ = winreg.QueryValueEx(key, RUN_VALUE)
        if isinstance(data, bytes) and data:
            # 首字节偶数（如 0x02）表示已启用，奇数（如 0x03）表示已禁用
            return (data[0] & 1) == 0
    except OSError:
        pass
    return True


def _approve_startup():
    """在 StartupApproved\\Run 中将本程序标记为已启用，解除历史禁用状态。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_BINARY, _STARTUP_ENABLED_BYTES)
    except OSError:
        pass


def autostart_enabled():
    """开机自启是否已开启、未被系统禁用，并且指向当前解释器与这份程序。"""
    command = _registered_command()
    return (command is not None
            and _command_parts(command) == _command_parts(autostart_command())
            and _startup_approved())


def repair_autostart():
    """自启项存在但指向旧位置（文件夹移动或 Python 解释器路径变更）时，改写为当前路径。返回是否改写。"""
    command = _registered_command()
    if command is None or _command_parts(command) == _command_parts(autostart_command()):
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
        return True
    except OSError:
        return False


def set_autostart(enable):
    """开启/关闭开机自启，成功返回 True。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
                _approve_startup()
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
    """原子写入配置（按 PID 命名临时文件再替换，避免双进程并发冲突）。"""
    temp = f'{CONFIG_PATH}.{os.getpid()}.tmp'
    try:
        with open(temp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(temp, CONFIG_PATH)
    except (OSError, TypeError, ValueError) as err:
        log.warning('保存配置失败: %s', err)
        try:
            os.remove(temp)
        except OSError:
            pass
