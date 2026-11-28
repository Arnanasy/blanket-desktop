"""异常退出守护：若主程序未恢复桌面图标或屏幕背光就崩溃/被强杀，自动恢复桌面图标与笔记本背光。"""
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time


def start():
    if getattr(sys, 'frozen', False):
        cmd = [sys.executable, '--recovery-watch']
    else:
        cmd = [sys.executable, '-B', str(Path(__file__).resolve()), '--watch']
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def watch(stream, recover, restore_brightness=None):
    # 当主进程被强杀时管道直接 EOF，不经过 finally，同样会触发恢复图标与背光
    pending_brightness = None
    for raw in stream:
        line = raw.strip()
        if line == b'restored':
            return
        if line.startswith(b'brightness:'):
            val = line.split(b':', 1)[1].strip()
            if val == b'clear':
                pending_brightness = None
            else:
                try:
                    pct = int(val)
                    if 1 <= pct <= 100:
                        pending_brightness = pct
                except ValueError:
                    pass
    if pending_brightness is not None:
        if restore_brightness is None:
            from screen_blackout import restore_backlight_wmi
            restore_brightness = restore_backlight_wmi
        try:
            restore_brightness(pending_brightness)
        except Exception:
            pass
    recover()


def _log_recovery_failure(last_state):
    try:
        from preferences import APP_DIR
        log_path = Path(APP_DIR) / 'Blanket_error.log'
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f'{stamp} [WARNING] 异常退出守护重试 15 秒后仍未能恢复桌面图标 (last_state={last_state})\n')
    except OSError:
        pass


def recover_icons():
    from desktop_windows import icons_visible, set_icons_visible

    last_state = None
    for _ in range(30):
        state = icons_visible()
        last_state = state
        if state is False:
            set_icons_visible(True)
        if icons_visible() is True:
            # 异常恢复仅保证桌面图标回到可见，不覆盖用户 config.json 里记住的 last_visible 偏好
            return
        time.sleep(0.5)
    _log_recovery_failure(last_state)


def run_watcher():
    # pythonw / 打包 exe 下即使传入管道，sys.stdin 也为 None，需直接取 Win32 标准输入句柄
    from win32 import kernel32, STD_INPUT_HANDLE, INVALID_HANDLE_VALUE

    handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    if handle and handle != INVALID_HANDLE_VALUE:
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            return
        with os.fdopen(fd, 'rb') as pipe:
            watch(pipe, recover_icons)


if __name__ == '__main__' and sys.argv[1:] == ['--watch']:
    run_watcher()
