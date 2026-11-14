"""Recover desktop icons if the application disappears before restoring them."""
import subprocess
import sys
import time
from pathlib import Path

def start():
    return subprocess.Popen(
        [sys.executable, '-B', str(Path(__file__).resolve()), '--watch'],
        stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
    )

def watch(stream, recover):
    # EOF also arrives when Windows terminates the writer without running finally.
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
    # pythonw sets sys.stdin to None even when a pipe is supplied.
    import ctypes
    import msvcrt
    import os
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.restype = ctypes.c_void_p
    handle = kernel.GetStdHandle(-10)
    with os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), 'rb') as pipe:
        watch(pipe, recover_icons)
