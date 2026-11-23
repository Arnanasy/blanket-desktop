"""静默启动器：捕获未处理异常并写入循环日志文件（带防弹窗风暴节流）。"""
import ctypes
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

sys.dont_write_bytecode = True

folder = Path(__file__).resolve().parent
sys.path.insert(0, str(folder))

log = logging.getLogger('desktop')
log.setLevel(logging.ERROR)
handler = RotatingFileHandler(
    folder / '净桌面错误.log',
    maxBytes=512000,
    backupCount=1,
    delay=True,
    encoding='utf-8',
)
handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
log.addHandler(handler)

_notified = False            # 单次运行最多弹窗提醒一次，防止轮询异常引发弹窗风暴


def report(exc_type, exc, tb):
    global _notified
    log.error('运行出错', exc_info=(exc_type, exc, tb))
    if _notified:
        return
    _notified = True
    ctypes.WinDLL('user32').MessageBoxW(
        None,
        '净桌面遇到错误，详细信息已保存到程序文件夹内的“净桌面错误.log”。\n请把日志发来排查。',
        '净桌面',
        0x10,
    )


try:
    import tkinter as tk
    tk.Tk.report_callback_exception = lambda self, t, e, tb: report(t, e, tb)
    from hide_desktop_icons import main
    main()
except Exception:
    report(*sys.exc_info())
