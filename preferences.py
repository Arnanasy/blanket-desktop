"""配置持久化与开机自启。"""
import json
import logging
import os
import sys
import winreg

log = logging.getLogger('desktop')

IS_FROZEN = getattr(sys, 'frozen', False)
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable))
           if IS_FROZEN else os.path.dirname(os.path.abspath(__file__)))
RESOURCE_DIR = getattr(sys, '_MEIPASS', APP_DIR) if IS_FROZEN else APP_DIR


def resource_path(name):
    """返回打包或源码模式下只读资源文件（图标/图片）的绝对路径。"""
    return os.path.join(RESOURCE_DIR, name)

# ---------------------------------------------------------------- 开机自启
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
STARTUP_APPROVED_KEY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
RUN_VALUE = 'Blanket'
LEGACY_RUN_VALUE = 'JingZhuoMian'
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
    if getattr(sys, 'frozen', False):
        return '"{}"'.format(os.path.abspath(sys.executable))
    return '"{}" "{}"'.format(_pythonw_path(), os.path.join(APP_DIR, 'launch.pyw'))


def _registered_command(value_name=RUN_VALUE):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, value_name)[0])
    except OSError:
        return None


def _delete_reg_value(subkey, value_name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except OSError:
        pass


def _command_parts(command):
    """从注册表命令行里取出规范化后的 (解释器/exe路径, 脚本路径)（兼容打包 exe 与源码脚本）。"""
    import shlex
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    cleaned = [p.strip('"') for p in parts if p.strip('"')]
    if not cleaned:
        return '', ''
    if len(cleaned) == 1:
        if cleaned[0].lower().endswith('.exe'):
            return os.path.normcase(os.path.abspath(cleaned[0])), ''
        return '', ''
    return (
        os.path.normcase(os.path.abspath(cleaned[0])),
        os.path.normcase(os.path.abspath(cleaned[-1])),
    )


def _startup_approved(value_name=RUN_VALUE):
    """检查启动项是否未被 Windows「任务管理器 / 系统设置」禁用。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY) as key:
            data, _ = winreg.QueryValueEx(key, value_name)
        if isinstance(data, bytes) and data:
            # 首字节偶数（如 0x02）表示已启用，奇数（如 0x03）表示已禁用
            return (data[0] & 1) == 0
    except OSError:
        pass
    return True


def _approve_startup(value_name=RUN_VALUE):
    """在 StartupApproved\\Run 中将本程序标记为已启用，解除历史禁用状态。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY, _STARTUP_ENABLED_BYTES)
    except OSError:
        pass


def autostart_enabled():
    """开机自启是否已开启、未被系统禁用，并且指向当前解释器与这份程序。"""
    for name in (RUN_VALUE, LEGACY_RUN_VALUE):
        command = _registered_command(name)
        if command is not None:
            return (_command_parts(command) == _command_parts(autostart_command())
                    and _startup_approved(name))
    return False


def repair_autostart():
    """修正移动后的自启路径，并将旧版 JingZhuoMian 注册表键无缝迁移为 Blanket。"""
    cur_cmd = _registered_command(RUN_VALUE)
    legacy_cmd = _registered_command(LEGACY_RUN_VALUE)
    if cur_cmd is None and legacy_cmd is None:
        return False

    need_migrate = legacy_cmd is not None
    active_name = RUN_VALUE if cur_cmd is not None else LEGACY_RUN_VALUE
    active_cmd = cur_cmd if cur_cmd is not None else legacy_cmd
    target_cmd = autostart_command()

    if not need_migrate and _command_parts(active_cmd) == _command_parts(target_cmd):
        return False

    approved = _startup_approved(active_name)
    try:
        if approved:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, target_cmd)
            _approve_startup(RUN_VALUE)
        if need_migrate:
            _delete_reg_value(RUN_KEY, LEGACY_RUN_VALUE)
            _delete_reg_value(STARTUP_APPROVED_KEY, LEGACY_RUN_VALUE)
        return True
    except OSError:
        return False


def set_autostart(enable):
    """开启/关闭开机自启，成功返回 True（关闭时一并清理新旧自启项与启停记录）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
                _approve_startup(RUN_VALUE)
                _delete_reg_value(RUN_KEY, LEGACY_RUN_VALUE)
                _delete_reg_value(STARTUP_APPROVED_KEY, LEGACY_RUN_VALUE)
            else:
                for name in (RUN_VALUE, LEGACY_RUN_VALUE):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                    _delete_reg_value(STARTUP_APPROVED_KEY, name)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 配置持久化
CONFIG_PATH = os.path.join(APP_DIR, 'config.json')
DEFAULT_CONFIG = {
    'auto': True,
    'zone': 'top',
    'delay': '10',
    'focus_guard': True,
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
    if not isinstance(merged.get('focus_guard'), bool):
        merged['focus_guard'] = DEFAULT_CONFIG['focus_guard']
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
