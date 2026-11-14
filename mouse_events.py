"""Dedicated hook thread: callback only queues events, never blocks on Explorer/Tk."""
import ctypes
from ctypes import wintypes
import queue
import threading

class MouseEvents:
    def __init__(self):
        self.events=queue.SimpleQueue();self.thread_id=0;self.ready=threading.Event();self.hook=None
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start();self.ready.wait(1)
    def run(self):
        u=ctypes.WinDLL('user32',use_last_error=True);k=ctypes.WinDLL('kernel32',use_last_error=True)
        cbtype=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM)
        class Data(ctypes.Structure):
            _fields_=[('pt',wintypes.POINT),('mouseData',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
        u.SetWindowsHookExW.argtypes=[ctypes.c_int,cbtype,wintypes.HINSTANCE,wintypes.DWORD];u.SetWindowsHookExW.restype=wintypes.HANDLE
        u.CallNextHookEx.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM];u.CallNextHookEx.restype=ctypes.c_ssize_t
        u.UnhookWindowsHookEx.argtypes=[wintypes.HANDLE]
        k.GetModuleHandleW.argtypes=[wintypes.LPCWSTR];k.GetModuleHandleW.restype=wintypes.HMODULE
        def callback(code,msg,data):
            if code>=0 and msg in (0x201,0x202):
                p=ctypes.cast(data,ctypes.POINTER(Data)).contents.pt
                self.events.put((msg,p.x,p.y))
            return u.CallNextHookEx(None,code,msg,data)
        self.callback=cbtype(callback)
        self.thread_id=k.GetCurrentThreadId()
        self.hook=u.SetWindowsHookExW(14,self.callback,k.GetModuleHandleW(None),0)
        self.ready.set()
        if not self.hook:return
        msg=wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg),None,0,0)>0:
                u.TranslateMessage(ctypes.byref(msg));u.DispatchMessageW(ctypes.byref(msg))
        finally:u.UnhookWindowsHookEx(self.hook)
    def close(self):
        if self.thread_id:ctypes.windll.user32.PostThreadMessageW(self.thread_id,0x12,0,0)
