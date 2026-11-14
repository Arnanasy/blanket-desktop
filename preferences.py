import json
import os
import sys
import winreg

# ---------------------------------------------------------------- 开机自启
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "JingZhuoMian"


def _pythonw_path():
    """返回与当前解释器对应的 pythonw.exe（无控制台）路径。"""
    exe = sys.executable
    if os.path.basename(exe).lower() == "python.exe":
        return os.path.join(os.path.dirname(exe), "pythonw.exe")
    return exe


def autostart_enabled():
    """是否已开启开机自启。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except (FileNotFoundError, OSError):
        return False


def set_autostart(enable):
    """开启/关闭开机自启，成功返回 True。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enable:
                cmd = '"{}" "{}"'.format(_pythonw_path(),
                                         os.path.join(os.path.dirname(__file__), "launch.pyw"))
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 配置持久化
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "config.json")
DEFAULT_CONFIG = {
    "auto": True,
    "zone": "top",
    "delay": "10",
}


def load_config():
    """读取配置，缺失或损坏时返回默认值。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        if not isinstance(cfg, dict):
            return merged
        if isinstance(cfg.get("last_visible"),bool):
            merged["last_visible"]=cfg["last_visible"]
        if isinstance(cfg.get("auto"), bool):
            merged["auto"] = cfg["auto"]
        if cfg.get("zone") in ("top", "any", "corner"):
            merged["zone"] = cfg["zone"]
        if str(cfg.get("delay")) in ("5", "10", "20"):
            merged["delay"] = str(cfg["delay"])
        return merged
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """保存配置，失败静默。"""
    try:
        with open(CONFIG_PATH + ".tmp", "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(CONFIG_PATH + ".tmp", CONFIG_PATH)
    except OSError:
        pass
