"""静默启动器：捕获未处理异常并写入循环日志文件。"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

sys.dont_write_bytecode = True

folder = Path(__file__).resolve().parent
sys.path.insert(0, str(folder))

from win32 import user32, MB_ICONERROR

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


def report(exc_type, exc, tb):
    log.error('运行出错', exc_info=(exc_type, exc, tb))
    user32.MessageBoxW(
        None,
        '净桌面遇到错误，详细信息已保存到程序文件夹内的“净桌面错误.log”。\n请把日志发来排查。',
        '净桌面',
        MB_ICONERROR,
    )


try:
    import tkinter as tk
    tk.Tk.report_callback_exception = lambda self, t, e, tb: report(t, e, tb)
    from hide_desktop_icons import main
    main()
except Exception:
    report(*sys.exc_info())
