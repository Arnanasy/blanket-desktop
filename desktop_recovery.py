"""异常退出守护：若主程序未恢复桌面图标就崩溃或被强杀，自动把桌面图标恢复显示。"""
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time


def start():
    return subprocess.Popen(
        [sys.executable, '-B', str(Path(__file__).resolve()), '--watch'],
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def watch(stream, recover):
    # 当主进程被强杀时管道直接 EOF，不经过 finally，同样会触发恢复
    for line in stream:
        if line.strip() == b'restored':
            return
    recover()


def recover_icons():
    from desktop_windows import icons_visible, set_icons_visible
    from preferences import load_config, save_config

    for _ in range(30):
        state = icons_visible()
        if state is False:
            set_icons_visible(True)
        if icons_visible() is True:
            config = load_config()
            config['last_visible'] = True
            save_config(config)
            return
        time.sleep(0.5)


if __name__ == '__main__' and sys.argv[1:] == ['--watch']:
    # pythonw 下即使传入管道，sys.stdin 也为 None，需直接取 Win32 标准输入句柄
    from win32 import kernel32, STD_INPUT_HANDLE, INVALID_HANDLE_VALUE

    handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    if handle and handle != INVALID_HANDLE_VALUE:
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            sys.exit(0)
        with os.fdopen(fd, 'rb') as pipe:
            watch(pipe, recover_icons)
