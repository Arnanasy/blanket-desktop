"""专用鼠标钩子线程：回调只把事件放进队列，绝不阻塞在资源管理器或 Tk 上。"""
import ctypes
from ctypes import wintypes
import queue
import threading
import time

from win32 import (user32, kernel32, HOOKPROC, MSLLHOOKSTRUCT, WH_MOUSE_LL,
                   WM_LBUTTONDOWN, WM_LBUTTONUP, WM_QUIT)


class MouseEvents:
    def __init__(self):
        self.events = queue.SimpleQueue()
        self.hook = None
        self.thread_id = 0
        self.last_down = 0.0          # 钩子最近一次收到左键按下的时刻，供健康检查使用
        self._ready = threading.Event()
        self._callback = HOOKPROC(self._on_event)   # 保持引用，防止被回收导致崩溃
        self.thread = threading.Thread(target=self._run, name='mouse-hook', daemon=True)
        self.thread.start()
        self._ready.wait(1)

    def _on_event(self, code, msg, data):
        if code >= 0 and msg in (WM_LBUTTONDOWN, WM_LBUTTONUP):
            now = time.monotonic()
            point = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents.pt
            if msg == WM_LBUTTONDOWN:
                self.last_down = now
            self.events.put((now, msg, point.x, point.y))
        return user32.CallNextHookEx(None, code, msg, data)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._callback, kernel32.GetModuleHandleW(None), 0)
        self.hook = hook
        self._ready.set()
        if not hook:
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hook)
            self.hook = None          # 主线程据此自动退回轮询模式

    def close(self):
        """结束钩子线程并等待其卸载钩子。"""
        if self.thread_id and self.thread.is_alive():
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.thread.join(1)
