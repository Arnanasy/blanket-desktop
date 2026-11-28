"""无闪烁全屏息屏遮罩：以纯黑置顶幕 + WMI 笔记本背光归零替代易导致显卡驱动/DWM 闪烁的 SC_MONITORPOWER。"""
import ctypes
import subprocess
import threading
import time
import tkinter as tk

from win32 import (GW_HWNDPREV, LASTINPUTINFO, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN,
                   SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, VK_CONTROL, VK_ESCAPE, VK_LBUTTON,
                   VK_LWIN, VK_MBUTTON, VK_MENU, VK_RBUTTON, VK_RETURN, VK_RWIN, VK_SHIFT,
                   VK_SPACE, VK_TAB, cursor_pos, kernel32, key_down, toplevel_hwnd, user32)

_WAKE_VKS = (
    VK_LBUTTON, VK_RBUTTON, VK_MBUTTON,
    VK_ESCAPE, VK_SPACE, VK_RETURN, VK_TAB,
    VK_LWIN, VK_RWIN, VK_CONTROL, VK_MENU, VK_SHIFT,
)

_DIM_PS = (
    "$b=(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
    "-ErrorAction SilentlyContinue | Select-Object -First 1).CurrentBrightness;"
    "if($null -ne $b -and $b -gt 0){"
    "$m=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
    "-ErrorAction SilentlyContinue | Select-Object -First 1;"
    "if($null -ne $m){"
    "$null=Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness "
    "-Arguments @{Timeout=[uint32]1;Brightness=[byte]0} -ErrorAction SilentlyContinue;"
    "Write-Output $b}}"
)


def _restore_ps(level):
    pct = max(1, min(100, int(level)))
    return (
        "$m=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
        "-ErrorAction SilentlyContinue | Select-Object -First 1;"
        f"if($null -ne $m){{$null=Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness "
        f"-Arguments @{{Timeout=[uint32]1;Brightness=[byte]{pct}}} -ErrorAction SilentlyContinue}}"
    )


def _run_ps(script, timeout=4.0):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    return subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True, text=True, timeout=timeout, creationflags=flags,
    )


def dim_backlight_wmi():
    """读取当前大于 0 的笔记本背光百分比并降至 0%，不支持、原本就是 0 或失败返回 None。"""
    try:
        res = _run_ps(_DIM_PS, timeout=4.0)
        if res.returncode == 0:
            raw = (res.stdout or '').strip().splitlines()
            if raw:
                val = int(raw[-1].strip())
                if 1 <= val <= 100:
                    return val
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def restore_backlight_wmi(level):
    """恢复笔记本背光亮度到指定百分比。"""
    if level is None or int(level) <= 0:
        return False
    try:
        res = _run_ps(_restore_ps(level), timeout=4.0)
        return res.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def last_input_tick():
    """返回系统最近一次硬件键鼠输入的毫秒时间戳（不受后台软件弹窗影响），失败返回 None。"""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return int(info.dwTime)


def _force_foreground(hwnd):
    """安全将黑幕窗口置为前台焦点，防止唤醒那一下空格/按键穿透误操作底下的软件。"""
    if not hwnd:
        return
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(fg_tid, cur_tid, False)


class ScreenBlackout:
    """全虚拟桌面纯黑息屏遮罩（默认仅点击鼠标或按键盘唤醒，防误触；可选开启大晃动唤醒）。"""

    ARM_DELAY = 1.2          # 启动后 1.2 秒内忽略手抖/松键，结束后再记录输入基准
    WAKE_MOVE_PX = 260       # 开启“晃动鼠标唤醒”时需移动超过 260 像素才唤醒

    def __init__(self, root, wake_on_move=False, wake_move_px=WAKE_MOVE_PX,
                 on_brightness_guard=None, on_wake=None):
        self.root = root
        self.wake_on_move = bool(wake_on_move)
        self.wake_move_px = max(80, int(wake_move_px))
        self.on_brightness_guard = on_brightness_guard
        self.on_wake = on_wake
        self.active = False
        self.win = None
        self._hint_label = None
        self._hint_timer = None
        self._lock = threading.Lock()
        self._session_id = 0
        self._saved_brightness = None
        self._arm_at = 0.0
        self._origin_pt = None
        self._last_pt = None
        self._last_tick = None
        self._initial_down = set()
        self._poll_id = None

    def _notify_guard(self, brightness_or_none):
        cb = self.on_brightness_guard
        if callable(cb):
            try:
                cb(brightness_or_none)
            except Exception:
                pass

    def _ensure_window(self):
        if self.win is not None and self.win.winfo_exists():
            return
        w = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        w.configure(bg='#000000', cursor='none')
        w.attributes('-topmost', True)
        w.bind('<KeyPress>', self._on_input_wake)
        w.bind('<ButtonPress>', self._on_input_wake)
        w.protocol('WM_DELETE_WINDOW', self.wake)
        hint = tk.Label(
            w,
            text='',
            fg='#475569',
            bg='#000000',
            font=('Microsoft YaHei UI', 10),
        )
        self._hint_label = hint
        self.win = w

    def _virtual_rect(self):
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        vh = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        return vx, vy, vw, vh

    def _show_brief_hint(self):
        if self._hint_timer is not None:
            try:
                self.root.after_cancel(self._hint_timer)
            except tk.TclError:
                pass
            self._hint_timer = None
        if self._hint_label is None or not self._hint_label.winfo_exists():
            return
        msg = '已息屏 · 点击鼠标、按任意键或大幅晃动鼠标唤醒' if self.wake_on_move else '已息屏 · 点击鼠标或按任意键唤醒'
        self._hint_label.config(text=msg)
        self._hint_label.place(relx=0.5, rely=0.92, anchor='center')
        self._hint_timer = self.root.after(1800, self._hide_hint)

    def _hide_hint(self):
        self._hint_timer = None
        if self._hint_label is not None and self._hint_label.winfo_exists():
            self._hint_label.place_forget()

    def start(self):
        """开启纯黑息屏遮罩并异步将笔记本背光降至 0%。"""
        if self.active:
            return
        self._ensure_window()
        vx, vy, vw, vh = self._virtual_rect()
        self.win.geometry(f'{vw}x{vh}{vx:+d}{vy:+d}')
        self.win.deiconify()
        self.win.lift()
        try:
            _force_foreground(toplevel_hwnd(self.win))
            self.win.focus_force()
        except tk.TclError:
            pass
        self._show_brief_hint()

        self._origin_pt = None
        self._last_pt = None
        self._last_tick = None
        self._initial_down = set()
        self._arm_at = time.monotonic() + self.ARM_DELAY
        self.active = True

        with self._lock:
            self._session_id += 1
            sid = self._session_id
            self._saved_brightness = None

        def worker():
            prev = dim_backlight_wmi()
            if prev is None or prev <= 0:
                return
            need_immediate_restore = False
            with self._lock:
                if sid == self._session_id and self.active:
                    self._saved_brightness = prev
                    self._notify_guard(prev)
                else:
                    need_immediate_restore = True
            if need_immediate_restore:
                restore_backlight_wmi(prev)
                self._notify_guard(None)

        threading.Thread(target=worker, name='blackout-dim', daemon=True).start()
        self._schedule_poll()

    def ensure_top(self):
        if self.active and self.win is not None and self.win.winfo_exists():
            try:
                hwnd = toplevel_hwnd(self.win)
                if hwnd and user32.GetWindow(hwnd, GW_HWNDPREV):
                    self.win.lift()
            except tk.TclError:
                pass

    def _schedule_poll(self):
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        if self.active:
            self._poll_id = self.root.after(70, self._poll_wake)

    def _poll_wake(self):
        self._poll_id = None
        if not self.active:
            return
        if time.monotonic() >= self._arm_at:
            pt = cursor_pos()
            cur_xy = (pt.x, pt.y)
            cur_tick = last_input_tick()
            if self._origin_pt is None:
                # 宽限期结束首帧才建立基准，彻底排除点息屏时手未松开的尾随滑动或长按未松键
                self._origin_pt = cur_xy
                self._last_pt = cur_xy
                self._last_tick = cur_tick
                self._initial_down = {vk for vk in _WAKE_VKS if key_down(vk)}
            else:
                cur_down = {vk for vk in _WAKE_VKS if key_down(vk)}
                self._initial_down &= cur_down
                # 1. 任意新按下的鼠标点击或常用唤醒键（即使焦点被系统截走也 100% 响应）
                if cur_down - self._initial_down:
                    self.wake()
                    return
                # 2. 开启移动唤醒时：相对起点位移超过阈值才醒
                if self.wake_on_move and (
                    abs(cur_xy[0] - self._origin_pt[0]) + abs(cur_xy[1] - self._origin_pt[1]) >= self.wake_move_px
                ):
                    self.wake()
                    return
                # 3. 硬件输入时间戳推进且光标未移动 -> 必为任意键盘按键（含字母/数字/功能键/多媒体键）或滚轮
                if (cur_tick is not None and self._last_tick is not None
                        and cur_tick != self._last_tick and cur_xy == self._last_pt):
                    self.wake()
                    return
                self._last_pt = cur_xy
                self._last_tick = cur_tick
        self._schedule_poll()

    def _on_input_wake(self, _event=None):
        if time.monotonic() >= self._arm_at:
            self.wake()
            return 'break'

    def wake(self):
        """立即退出纯黑息屏遮罩并恢复原始背光亮度。"""
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        if self._hint_timer is not None:
            try:
                self.root.after_cancel(self._hint_timer)
            except tk.TclError:
                pass
            self._hint_timer = None
        self._hide_hint()
        was_active = self.active
        self.active = False
        self._origin_pt = None
        self._last_pt = None
        self._last_tick = None
        self._initial_down.clear()
        if self.win is not None and self.win.winfo_exists():
            try:
                self.win.withdraw()
            except tk.TclError:
                pass
        with self._lock:
            self._session_id += 1
            saved = self._saved_brightness
            self._saved_brightness = None
        self._notify_guard(None)
        if saved is not None:
            threading.Thread(
                target=restore_backlight_wmi, args=(saved,),
                name='blackout-restore', daemon=True,
            ).start()
        if was_active and callable(self.on_wake):
            try:
                self.on_wake()
            except Exception:
                pass
        return was_active

    def close(self):
        """程序退出兜底：同步恢复原背光并销毁黑幕窗口。"""
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        if self._hint_timer is not None:
            try:
                self.root.after_cancel(self._hint_timer)
            except tk.TclError:
                pass
            self._hint_timer = None
        self.active = False
        self._origin_pt = None
        self._last_pt = None
        self._last_tick = None
        self._initial_down.clear()
        with self._lock:
            self._session_id += 1
            saved = self._saved_brightness
            self._saved_brightness = None
        self._notify_guard(None)
        if saved is not None:
            restore_backlight_wmi(saved)
        if self.win is not None and self.win.winfo_exists():
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None
        self._hint_label = None
