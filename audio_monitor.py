"""零依赖 Windows Core Audio 音频输出峰值监听模块。

通过纯 ctypes 调用 IMMDeviceEnumerator -> IMMDevice -> IAudioMeterInformation::GetPeakValue，
判断系统默认播放设备当前是否有真实声音输出，用于辅助桌面音乐胶囊在无 SMTC 会话或状态滞后时自动显隐。
"""
import ctypes
import logging
from threading import Event, Thread
import time
import uuid

log = logging.getLogger('desktop')

CLSCTX_ALL = 23
E_RENDER = 0
E_MULTIMEDIA = 1
COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = -2147417850

PEAK_THRESHOLD = 0.002       # 过滤极微弱底噪的峰值阈值（0.0..1.0）
POLL_SECONDS = 0.4           # 音频峰值采样间隔
SILENCE_GRACE = 2.5          # 短暂歌间停顿宽限秒数


class _GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.c_uint32),
        ('Data2', ctypes.c_uint16),
        ('Data3', ctypes.c_uint16),
        ('Data4', ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, text):
        u = uuid.UUID(text)
        d4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])
        return cls(u.time_low, u.time_mid, u.time_hi_version, d4)


CLSID_MMDeviceEnumerator = _GUID.from_str('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
IID_IMMDeviceEnumerator = _GUID.from_str('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
IID_IAudioMeterInformation = _GUID.from_str('{C02216F6-8C67-4B5B-9D00-D008E73E0064}')

_RELEASE_FN = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_GET_DEFAULT_FN = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
)
_ACTIVATE_FN = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.c_uint32,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
)
_GET_PEAK_FN = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float))


def _vtable(ptr):
    return ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


def _release(ptr):
    if ptr and ptr.value:
        try:
            _RELEASE_FN(_vtable(ptr)[2])(ptr)
        except Exception:
            pass
        ptr.value = None


def _make_ole32():
    dll = ctypes.WinDLL('ole32')
    dll.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    dll.CoInitializeEx.restype = ctypes.c_long
    dll.CoUninitialize.argtypes = []
    dll.CoUninitialize.restype = None
    dll.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    ]
    dll.CoCreateInstance.restype = ctypes.c_long
    return dll


def merge_media_and_audio_state(smtc_state, audio_active, audio_assist=True, prev_fallback=False):
    """融合 SMTC 媒体状态与系统音频监听状态（纯函数，便于测试）。

    - 未开启 audio_assist：原样返回 SMTC 状态；
    - SMTC 正在播放：以 SMTC 为准；
    - SMTC 暂停但系统仍有声音：保持 playing=True，防止状态滞后导致误隐藏；
    - 无 SMTC 会话但系统有声音：返回兜底活跃态，让音乐胶囊自动显示；
    - 无 SMTC 会话且系统静音：若上一帧处于兜底态则转暂停态（交由 pause_hide 平滑隐藏）。
    """
    if not audio_assist:
        return smtc_state
    if smtc_state.get('active'):
        if not smtc_state.get('playing') and audio_active:
            return dict(smtc_state, playing=True)
        return smtc_state
    if audio_active:
        return {
            'active': True,
            'playing': True,
            'title': '系统音频播放中',
            'artist': '音频监听',
            'thumb': None,
            'key': ('__audio__', ''),
            'pos_base': 0.0,
            'pos_at': 0.0,
            'duration': 0.0,
            'audio_fallback': True,
        }
    if prev_fallback:
        return {
            'active': True,
            'playing': False,
            'title': '系统音频已暂停',
            'artist': '音频监听',
            'thumb': None,
            'key': ('__audio__', ''),
            'pos_base': 0.0,
            'pos_at': 0.0,
            'duration': 0.0,
            'audio_fallback': True,
        }
    return smtc_state


class AudioActivityMonitor:
    """后台轻量化音频峰值监听线程（自动应对默认播放设备热插拔切换）。"""

    def __init__(self, threshold=PEAK_THRESHOLD, poll_seconds=POLL_SECONDS, silence_grace=SILENCE_GRACE):
        self.threshold = float(threshold)
        self.poll_seconds = float(poll_seconds)
        self.silence_grace = float(silence_grace)
        self._stop = Event()
        self._thread = None
        self._peak = 0.0
        self._last_sound_at = 0.0

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        stop_ev = Event()
        self._stop = stop_ev
        self._thread = Thread(target=self._run, args=(stop_ev,), name='audio-monitor', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None

    @property
    def peak(self):
        return self._peak

    def is_active(self, now=None):
        """最近 silence_grace 秒内检测到系统有声音输出则返回 True。"""
        if self._last_sound_at <= 0.0:
            return False
        t = time.monotonic() if now is None else now
        return (t - self._last_sound_at) <= self.silence_grace

    def _run(self, stop_ev=None):
        if stop_ev is None:
            stop_ev = self._stop
        try:
            ole32 = _make_ole32()
        except OSError:
            return
        hr_init = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        if hr_init not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
            return
        enum_ptr = ctypes.c_void_p()
        dev_ptr = ctypes.c_void_p()
        meter_ptr = ctypes.c_void_p()
        get_peak_fn = None

        def reset_meter():
            nonlocal get_peak_fn
            get_peak_fn = None
            _release(meter_ptr)
            _release(dev_ptr)

        try:
            hr = ole32.CoCreateInstance(
                ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum_ptr),
            )
            if hr != S_OK or not enum_ptr.value:
                return
            get_default_fn = _GET_DEFAULT_FN(_vtable(enum_ptr)[4])
            peak_val = ctypes.c_float()
            while not stop_ev.is_set():
                try:
                    if get_peak_fn is None:
                        hr_dev = get_default_fn(enum_ptr, E_RENDER, E_MULTIMEDIA, ctypes.byref(dev_ptr))
                        if hr_dev == S_OK and dev_ptr.value:
                            activate_fn = _ACTIVATE_FN(_vtable(dev_ptr)[3])
                            hr_m = activate_fn(
                                dev_ptr, ctypes.byref(IID_IAudioMeterInformation),
                                CLSCTX_ALL, None, ctypes.byref(meter_ptr),
                            )
                            if hr_m == S_OK and meter_ptr.value:
                                get_peak_fn = _GET_PEAK_FN(_vtable(meter_ptr)[3])
                            else:
                                reset_meter()
                    if get_peak_fn is not None:
                        hr_p = get_peak_fn(meter_ptr, ctypes.byref(peak_val))
                        if hr_p == S_OK:
                            val = float(peak_val.value)
                            self._peak = val
                            if val >= self.threshold:
                                self._last_sound_at = time.monotonic()
                        else:
                            # 默认音频设备切换或失效，重置以便下次重新绑定
                            self._peak = 0.0
                            reset_meter()
                except Exception:
                    log.debug('音频峰值采样失败', exc_info=True)
                    reset_meter()
                stop_ev.wait(self.poll_seconds)
        finally:
            reset_meter()
            _release(enum_ptr)
            if hr_init in (S_OK, S_FALSE):
                ole32.CoUninitialize()
