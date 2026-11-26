"""Blanket 核心边界逻辑单元测试（纯标准库，不触碰真实配置、注册表与网络）。"""
import ctypes
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

# 确保以任意工作目录运行 unittest 都能导入项目模块
PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import desktop_recovery
import local_weather
import preferences
import sys_monitor
import wallpaper_clock
import win32


class TestPreferences(unittest.TestCase):
    """配置读取容错、旧版迁移、原子写入与开机自启命令行解析。"""

    def test_load_missing_and_corrupted_config_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, 'config.json')
            with mock.patch.object(preferences, 'APP_DIR', tmp), \
                 mock.patch.object(preferences, 'CONFIG_PATH', cfg_path):
                # 缺失文件返回默认值
                cfg = preferences.load_config()
                self.assertEqual(cfg['auto'], True)
                self.assertEqual(cfg['zone'], 'top')
                self.assertEqual(cfg['delay'], '10')
                self.assertEqual(cfg['widgets'], {})

                # JSON 损坏同样返回默认值，不抛异常
                Path(cfg_path).write_text('{bad json', encoding='utf-8')
                cfg = preferences.load_config()
                self.assertEqual(cfg['zone'], 'top')

    def test_load_sanitizes_invalid_field_types_and_migrates_legacy_widgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, 'config.json')
            legacy_widgets = os.path.join(tmp, 'desktop_widgets.json')
            Path(cfg_path).write_text(json.dumps({
                'auto': 'yes',              # 非 bool
                'zone': 'bottom',           # 非法热区
                'delay': 99,                # 非法延时
                'last_visible': 1,          # 非 bool，应被移除
            }), encoding='utf-8')
            Path(legacy_widgets).write_text(json.dumps({'clock': False}), encoding='utf-8')

            with mock.patch.object(preferences, 'APP_DIR', tmp), \
                 mock.patch.object(preferences, 'CONFIG_PATH', cfg_path):
                cfg = preferences.load_config()
                self.assertIs(cfg['auto'], True)
                self.assertEqual(cfg['zone'], 'top')
                self.assertEqual(cfg['delay'], '10')
                self.assertNotIn('last_visible', cfg)
                self.assertEqual(cfg['widgets'], {'clock': False})

    def test_save_config_atomic_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, 'config.json')
            sample = {'auto': False, 'zone': 'corner', 'delay': '20', 'widgets': {'clock': True}}
            with mock.patch.object(preferences, 'APP_DIR', tmp), \
                 mock.patch.object(preferences, 'CONFIG_PATH', cfg_path):
                preferences.save_config(sample)
                loaded = preferences.load_config()
                self.assertEqual(loaded['auto'], False)
                self.assertEqual(loaded['zone'], 'corner')
                self.assertEqual(loaded['delay'], '20')
                self.assertEqual(loaded['widgets'], {'clock': True})
                # 确认没有残留临时文件
                self.assertEqual(os.listdir(tmp), ['config.json'])

    def test_command_parts_detects_python_and_script_changes(self):
        c1 = r'"C:\Program Files\Python311\pythonw.exe" "C:\Users\demo\Blanket\launch.pyw"'
        c2 = r'"C:\Program Files\Python312\pythonw.exe" "C:\Users\demo\Blanket\launch.pyw"'
        c3 = r'"C:\Program Files\Python311\pythonw.exe" "D:\Tools\Blanket\launch.pyw"'
        c1_case = r'"c:\program files\python311\pythonw.exe" "c:\users\demo\blanket\launch.pyw"'

        self.assertEqual(preferences._command_parts(c1), preferences._command_parts(c1_case))
        self.assertNotEqual(preferences._command_parts(c1), preferences._command_parts(c2))
        self.assertNotEqual(preferences._command_parts(c1), preferences._command_parts(c3))
        self.assertEqual(preferences._command_parts('only_one_arg'), ('', ''))

    def test_focus_guard_config_sanitization_and_legacy_autostart_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            cn_dir = Path(tmp) / '中文配置目录'
            cn_dir.mkdir()
            cfg_path = str(cn_dir / 'config.json')
            Path(cfg_path).write_text(json.dumps({'focus_guard': 'invalid'}), encoding='utf-8')
            with mock.patch.object(preferences, 'APP_DIR', str(cn_dir)), \
                 mock.patch.object(preferences, 'CONFIG_PATH', cfg_path):
                cfg = preferences.load_config()
                self.assertIs(cfg['focus_guard'], True)

        # 模拟旧版 JingZhuoMian 注册表项平滑迁移到 Blanket
        target_cmd = preferences.autostart_command()
        store = {
            (preferences.RUN_KEY, 'JingZhuoMian'): target_cmd,
        }
        deleted = []
        written = {}

        class FakeKey:
            def __init__(self, subkey):
                self.subkey = subkey
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        def fake_open(_root, subkey, *_args):
            return FakeKey(subkey)

        def fake_query(key_obj, name):
            if (key_obj.subkey, name) in store:
                return store[(key_obj.subkey, name)], 1
            raise FileNotFoundError(name)

        def fake_set(key_obj, name, _res, _typ, val):
            written[(key_obj.subkey, name)] = val

        def fake_del(key_obj, name):
            deleted.append((key_obj.subkey, name))

        with mock.patch.object(preferences.winreg, 'OpenKey', side_effect=fake_open), \
             mock.patch.object(preferences.winreg, 'QueryValueEx', side_effect=fake_query), \
             mock.patch.object(preferences.winreg, 'SetValueEx', side_effect=fake_set), \
             mock.patch.object(preferences.winreg, 'DeleteValue', side_effect=fake_del):
            self.assertTrue(preferences.autostart_enabled())
            self.assertTrue(preferences.repair_autostart())
            self.assertEqual(written.get((preferences.RUN_KEY, 'Blanket')), target_cmd)
            self.assertIn((preferences.RUN_KEY, 'JingZhuoMian'), deleted)
            self.assertIn((preferences.STARTUP_APPROVED_KEY, 'JingZhuoMian'), deleted)


class TestLocalWeather(unittest.TestCase):
    """天气缓存命中、日期匹配、国内源解析与海外回退逻辑。"""

    def test_is_number_rejects_bool(self):
        self.assertTrue(local_weather._is_number(0))
        self.assertTrue(local_weather._is_number(123.45))
        self.assertFalse(local_weather._is_number(True))
        self.assertFalse(local_weather._is_number(False))
        self.assertFalse(local_weather._is_number('123'))

    def test_refresh_returns_fresh_cache_without_network(self):
        fresh = {'line': '晴  25°', 'updated': time.time() - 60, 'place': '南宁'}
        with mock.patch.object(local_weather, 'domestic_weather') as dom, \
             mock.patch.object(local_weather, 'global_weather') as glb:
            result = local_weather.refresh(fresh)
            self.assertEqual(result, fresh)
            dom.assert_not_called()
            glb.assert_not_called()

    def test_refresh_ignores_bool_timestamp_and_expired_cache(self):
        bad_cache = {'line': '晴  25°', 'updated': True}
        expected = {'line': '多云  22°', 'updated': time.time(), 'place': '北京'}
        with mock.patch.object(local_weather, 'domestic_weather', return_value=expected) as dom:
            self.assertEqual(local_weather.refresh(bad_cache), expected)
            dom.assert_called_once()

    def test_domestic_weather_matches_unpadded_cn_date(self):
        fake_now = time.mktime((2026, 5, 3, 12, 0, 0, 0, 0, -1))
        fake_tm = time.localtime(fake_now)

        def fake_fetch(url):
            if 'wgeo.weather.com.cn/ip/' in url:
                return 'var id="101300101";var addr="广西,南宁";'
            if 'sk_2d/101300101.html' in url:
                return 'var dataSK={"city":"101300101","cityname":"南宁","temp":"27","weather":"晴"};'
            if 'cityinfo/101300101.html' in url:
                return json.dumps({
                    'weatherinfo': {
                        'date_y': '2026年5月3日',   # 不带前导零
                        'temp1': '20℃',
                        'temp2': '31℃',
                    }
                })
            raise AssertionError(f'Unexpected URL: {url}')

        with mock.patch.object(local_weather, 'domestic_text', side_effect=fake_fetch), \
             mock.patch.object(local_weather.time, 'time', return_value=fake_now), \
             mock.patch.object(local_weather.time, 'localtime', return_value=fake_tm):
            res = local_weather.domestic_weather({})
            self.assertEqual(res['place'], '南宁')
            self.assertEqual(res['line'], '晴  27° · 20℃–31℃')

    def test_refresh_falls_back_to_global_when_domestic_fails(self):
        fallback = {'line': '晴  18°  ·  12°–21°', 'place': 'Tokyo', 'source': 'Open-Meteo', 'updated': time.time()}
        with mock.patch.object(local_weather, 'domestic_weather', side_effect=ValueError('No domestic station')), \
             mock.patch.object(local_weather, 'global_weather', return_value=fallback) as glb:
            self.assertEqual(local_weather.refresh({}), fallback)
            glb.assert_called_once()

    def test_coords_validation(self):
        self.assertEqual(local_weather.coords(22.8167, 108.3669), (22.82, 108.37))
        with self.assertRaises(ValueError):
            local_weather.coords(95, 0)
        with self.assertRaises(ValueError):
            local_weather.coords(0, 181)


class TestDesktopRecovery(unittest.TestCase):
    """异常退出守护：正常退出不触发恢复，崩溃/强杀 EOF 触发恢复。"""

    def test_normal_exit_does_not_recover(self):
        called = []
        desktop_recovery.watch(io.BytesIO(b'restored\n'), lambda: called.append(True))
        self.assertEqual(called, [])

    def test_unexpected_eof_triggers_recover(self):
        called = []
        desktop_recovery.watch(io.BytesIO(b''), lambda: called.append(True))
        self.assertEqual(called, [True])

    def test_garbage_then_eof_still_recovers(self):
        called = []
        desktop_recovery.watch(io.BytesIO(b'ping\n'), lambda: called.append(True))
        self.assertEqual(called, [True])


class TestMonitorAndWallpaper(unittest.TestCase):
    """网速格式化、Win32 结构体布局与壁纸采样边界。"""

    def test_speed_formatting(self):
        self.assertEqual(sys_monitor.speed(0), '0 B/s')
        self.assertEqual(sys_monitor.speed(512), '512 B/s')
        self.assertEqual(sys_monitor.speed(1024), '1.0 KiB/s')
        self.assertEqual(sys_monitor.speed(1048576), '1.0 MiB/s')

    def test_mib_if_row2_layout(self):
        self.assertEqual(ctypes.sizeof(win32.Interface), 1352)
        # InterfaceTable.first 必须 8 字节对齐（count 为 4 字节 + 4 字节填充）
        self.assertEqual(win32.InterfaceTable.first.offset, 8)

    def test_sampler_ping_queue_ignores_stale_target(self):
        sampler = sys_monitor.Sampler()
        sampler._ping_host = '1.1.1.1'
        sampler._ping_seq = 2
        sampler._ping_active_seq = 2
        # 模拟旧目标 (seq=1) 与当前目标 (seq=2) 依次回传
        sampler._ping_results.put((1, 999))
        sampler._ping_results.put((2, 18))
        sampler._drain_ping_results()
        self.assertEqual(sampler._ping_text, '18 ms')
        self.assertIsNone(sampler._ping_active_seq)

    def test_wallpaper_sample_dark_light_and_edge_overlap(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('未安装可选依赖 Pillow')

        with tempfile.TemporaryDirectory() as tmp:
            dark_img = Path(tmp) / 'dark.png'
            light_img = Path(tmp) / 'light.png'
            Image.new('RGB', (200, 100), (15, 20, 30)).save(dark_img)
            Image.new('RGB', (200, 100), (240, 245, 250)).save(light_img)

            def run_with_wallpaper(img_path, rect):
                fake_values = {'WallpaperStyle': '6', 'TileWallpaper': '0', 'Wallpaper': str(img_path)}

                class DummyKey:
                    def __enter__(self): return self
                    def __exit__(self, *args): return False

                with mock.patch.object(wallpaper_clock.winreg, 'OpenKey', return_value=DummyKey()), \
                     mock.patch.object(wallpaper_clock.winreg, 'QueryValueEx',
                                       side_effect=lambda _k, name: (fake_values[name], 1)), \
                     mock.patch.dict(os.environ, {'APPDATA': tmp}):
                    return wallpaper_clock.sample(rect, (0, 0, 200, 200), (0, 0, 200, 200))

            # 适应模式（200x100 居中放在 200x200 屏幕）：壁纸纵向范围 y=50..150
            self.assertEqual(run_with_wallpaper(dark_img, (20, 60, 80, 40)), 'dark')
            self.assertEqual(run_with_wallpaper(light_img, (20, 60, 80, 40)), 'light')
            # 部分落在上方黑边（y=30..80 与 50..150 有交集），应按交集正常采样而非返回 None
            self.assertEqual(run_with_wallpaper(light_img, (20, 30, 80, 50)), 'light')
            # 完全落在黑边内（y=0..40 无交集），应返回 None
            self.assertIsNone(run_with_wallpaper(light_img, (20, 0, 80, 40)))


class TestClockExtrasAndLayout(unittest.TestCase):
    def test_clock_and_date_formatting_and_addons(self):
        from datetime import date, datetime
        import clock_extras as ce

        dt = datetime(2026, 11, 24, 20, 5, 9)
        self.assertEqual(ce.format_clock_text(dt, seconds=False, hour12=False), '20:05')
        self.assertEqual(ce.format_clock_text(dt, seconds=True, hour12=True), '08:05:09 PM')
        self.assertEqual(ce.format_date_text(dt, fmt='yyyy/MM/dd  EEE', addon='无'), '2026/11/24  周二')
        self.assertIn('射手座', ce.zodiac_text(date(2026, 11, 24)))
        self.assertEqual(ce.lunar_date_text(date(2026, 2, 17)), '正月初一')
        self.assertTrue(ce.solar_term_text(date(2026, 11, 24)))
        self.assertIn('·', ce.format_date_text(dt, fmt='MM-dd', addon='农历·节气'))

    def test_anchor_layout_cross_resolution_and_clamp(self):
        import widget_layout as wl

        # 在 2560x1440 右下区域保存锚点，切换到 1920x1080 时应按比例归位且不出界
        anchor = wl.capture_anchor(2100, 1100, 200, 100, bounds_fn=lambda x, y, work=True: (0, 0, 2560, 1440))
        nx, ny = wl.resolve_anchor(anchor, [2100, 1100], 200, 100, bounds_fn=lambda x, y, work=True: (0, 0, 1920, 1080))
        self.assertTrue(0 <= nx <= 1720)
        self.assertTrue(0 <= ny <= 980)
        self.assertGreater(nx, 1200)

        # 旧版无 anchor 时，越界坐标应被安全拉回屏幕内
        cx, cy = wl.resolve_anchor(None, [5000, -200], 200, 100, bounds_fn=lambda x, y, work=True: (0, 0, 1920, 1080))
        self.assertEqual((cx, cy), (1720, 0))

    def test_media_widget_helpers(self):
        import media_widget as mw

        self.assertAlmostEqual(mw.ease_out_cubic(0.0), 0.0)
        self.assertAlmostEqual(mw.ease_out_cubic(1.0), 1.0)
        self.assertGreater(mw.ease_out_cubic(0.5), 0.8)
        self.assertEqual(mw.truncate_text('短歌名', 10), '短歌名')
        self.assertEqual(len(mw.truncate_text('这是一首非常非常长的中文歌名测试', 8)), 8)

        prefs = mw.sanitize_media_prefs({'scale': 999, 'opacity': 10, 'bg': 'bad', 'style': '未知', 'pause_hide': -5})
        self.assertEqual(prefs['scale'], 220)
        self.assertEqual(prefs['opacity'], 35)
        self.assertEqual(prefs['bg'], mw.MEDIA_DEFAULTS['bg'])
        self.assertEqual(prefs['style'], mw.MEDIA_DEFAULTS['style'])
        self.assertEqual(prefs['pause_hide'], 0)

        # 按像素宽度截断测试：确保中英文混排绝不超宽
        measure = lambda s: sum(14 if ord(ch) > 127 else 7 for ch in s)
        fitted = mw.fit_text_width('这是一首特别长的中文歌曲标题测试', measure, 90)
        self.assertLessEqual(measure(fitted), 90)
        self.assertTrue(fitted.endswith('…'))

    def test_lrc_parser_and_current_lines(self):
        import lyrics_widget as lw

        sample = '[00:05.50]第一句歌词\n[00:10.00][00:20.00]副歌重复\n[00:15.25]第二句歌词\n'
        parsed = lw.parse_lrc(sample)
        self.assertEqual(len(parsed), 4)
        self.assertEqual(lw.current_lyric_lines(parsed, 2.0), ('', '第一句歌词'))
        self.assertEqual(lw.current_lyric_lines(parsed, 6.0), ('第一句歌词', '副歌重复'))
        self.assertEqual(lw.current_lyric_lines(parsed, 21.0), ('副歌重复', ''))

    def test_resolve_on_desktop_does_not_raise_on_taskbar_click_from_app(self):
        import desktop_widgets as dw

        # 软件最大化/激活中点击任务栏或短暂无前景窗口：保持 False，绝不把时钟顶到软件最上层
        self.assertFalse(dw.resolve_on_desktop(100, 'Shell_TrayWnd', prev_on_desktop=False))
        self.assertFalse(dw.resolve_on_desktop(101, 'Shell_SecondaryTrayWnd', prev_on_desktop=False))
        self.assertFalse(dw.resolve_on_desktop(0, '', prev_on_desktop=False))
        # 切到桌面：返回 True
        self.assertTrue(dw.resolve_on_desktop(200, 'WorkerW', prev_on_desktop=False))
        self.assertTrue(dw.resolve_on_desktop(201, 'Progman', prev_on_desktop=False))
        # 本来就在桌面时点任务栏：保持 True，不让挂件沉到壁纸后面
        self.assertTrue(dw.resolve_on_desktop(100, 'Shell_TrayWnd', prev_on_desktop=True))
        # 点回普通软件：立刻返回 False 沉底
        self.assertFalse(dw.resolve_on_desktop(300, 'Chrome_WidgetWin_1', prev_on_desktop=True))

    def test_focus_guard_external_app_detection_and_click_through(self):
        import desktop_widgets as dw
        import desktop_windows as dwin

        # 半屏游戏或外部软件在前台 -> 激活防误触锁定
        self.assertTrue(dw.resolve_external_app_active(500, 'UnrealWindow', own_window=False, prev_external=False))
        # Blanket 自己的设置窗口在前台 -> 允许交互调整挂件位置
        self.assertFalse(dw.resolve_external_app_active(501, 'Toplevel', own_window=True, prev_external=True))
        # 回到桌面 -> 解除锁定，恢复全部交互
        self.assertFalse(dw.resolve_external_app_active(200, 'WorkerW', own_window=False, prev_external=True))
        self.assertFalse(dw.resolve_external_app_active(201, 'Progman', own_window=False, prev_external=True))
        # 短暂点击任务栏或焦点过渡空句柄 -> 鼠标不在桌面时沿用上一帧，鼠标已在桌面时立即解锁
        self.assertTrue(dw.resolve_external_app_active(100, 'Shell_TrayWnd', own_window=False, prev_external=True, cursor_on_desktop=False))
        self.assertFalse(dw.resolve_external_app_active(100, 'Shell_TrayWnd', own_window=False, prev_external=True, cursor_on_desktop=True))
        self.assertFalse(dw.resolve_external_app_active(0, '', own_window=False, prev_external=True, cursor_on_desktop=True))
        self.assertTrue(dw.resolve_external_app_active(0, '', own_window=False, prev_external=True, cursor_on_desktop=False))

        # 半屏游戏防误触开启时，失手点到窗外桌面空白不触发图标显隐切换
        dc = dwin.DesktopClick()
        with mock.patch.object(dwin, 'key_down', side_effect=[True, False]), \
             mock.patch.object(dwin, 'get_cursor', return_value=(100, 100)), \
             mock.patch.object(dc, 'is_blank_click', return_value=True):
            self.assertFalse(dc.check(suppress=True))
            self.assertFalse(dc.check(suppress=True))

        # 已在桌面（suppress=False）时单击桌面空白正常切换图标
        with mock.patch.object(dwin, 'key_down', side_effect=[True, False]), \
             mock.patch.object(dwin, 'get_cursor', return_value=(100, 100)), \
             mock.patch.object(dc, 'is_blank_click', return_value=True):
            self.assertFalse(dc.check(suppress=False))
            self.assertTrue(dc.check(suppress=False))

        # 验证 set_click_through 仅切换 WS_EX_TRANSPARENT 且保留原有扩展样式
        base_style = win32.WS_EX_NOACTIVATE | win32.WS_EX_TOOLWINDOW
        recorded = []
        with mock.patch.object(win32, 'GetWindowLongPtr', return_value=base_style), \
             mock.patch.object(win32, 'SetWindowLongPtr', side_effect=lambda h, idx, val: recorded.append(val)):
            win32.set_click_through(12345, True)
            self.assertEqual(recorded[-1], base_style | win32.WS_EX_LAYERED | win32.WS_EX_TRANSPARENT)

        layered_through = base_style | win32.WS_EX_LAYERED | win32.WS_EX_TRANSPARENT
        with mock.patch.object(win32, 'GetWindowLongPtr', return_value=layered_through), \
             mock.patch.object(win32, 'SetWindowLongPtr', side_effect=lambda h, idx, val: recorded.append(val)):
            win32.set_click_through(12345, False)
            self.assertEqual(recorded[-1], base_style | win32.WS_EX_LAYERED)

    def test_audio_monitor_state_merge_and_lyrics_fallback(self):
        import audio_monitor as am
        import lyrics_widget as lw

        idle = {'active': False, 'playing': False, 'title': '', 'artist': '', 'key': None}
        paused_smtc = {'active': True, 'playing': False, 'title': '晴天', 'artist': '周杰伦', 'key': ('晴天', '周杰伦')}

        # 无 SMTC 但检测到系统声音 -> 兜底活跃态
        st = am.merge_media_and_audio_state(idle, audio_active=True, audio_assist=True)
        self.assertTrue(st['active'] and st['playing'] and st.get('audio_fallback'))

        # 声音停止后，原兜底态转为暂停态等待 pause_hide 隐身
        st2 = am.merge_media_and_audio_state(idle, audio_active=False, audio_assist=True, prev_fallback=True)
        self.assertTrue(st2['active'] and not st2['playing'])

        # SMTC 暂停但系统仍有声音 -> 保持 playing=True 防止误隐藏
        st3 = am.merge_media_and_audio_state(paused_smtc, audio_active=True, audio_assist=True)
        self.assertTrue(st3['playing'])

        # 关闭音频监听辅助 -> 完全按原 SMTC 状态返回
        st4 = am.merge_media_and_audio_state(idle, audio_active=True, audio_assist=False)
        self.assertFalse(st4['active'])

        # 歌词源：优先国内源，未命中自动回退 lrclib；兜底虚拟歌名不请求网络
        with mock.patch.object(lw, '_fetch_netease_lyrics', return_value=[(1.0, '国内歌词')]) as ne, \
             mock.patch.object(lw, '_fetch_lrclib_lyrics', return_value=[(1.0, '海外歌词')]) as lr:
            self.assertEqual(lw.fetch_synced_lyrics('晴天', '周杰伦'), [(1.0, '国内歌词')])
            ne.assert_called_once()
            lr.assert_not_called()

        with mock.patch.object(lw, '_fetch_netease_lyrics', return_value=[]) as ne, \
             mock.patch.object(lw, '_fetch_lrclib_lyrics', return_value=[(2.0, '海外回退')]) as lr:
            self.assertEqual(lw.fetch_synced_lyrics('Song', 'Artist'), [(2.0, '海外回退')])
            ne.assert_called_once()
            lr.assert_called_once()

        with mock.patch.object(lw, '_fetch_netease_lyrics') as ne, \
             mock.patch.object(lw, '_fetch_lrclib_lyrics') as lr:
            self.assertEqual(lw.fetch_synced_lyrics('系统音频播放中', '音频监听'), [])
            ne.assert_not_called()
            lr.assert_not_called()

    def test_quick_hub_recent_files_notes_countdown_awake_and_deskkit(self):
        import quick_hub as qh

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dl_dir = base / '下载测试目录'
            desk_dir = base / '桌面测试目录'
            dl_dir.mkdir()
            desk_dir.mkdir()

            # 应被过滤的噪音文件：快捷方式、未下完临时文件、Office 锁文件、系统配置
            (desk_dir / '快捷方式.lnk').write_bytes(b'1')
            (dl_dir / '未下完视频.mp4.crdownload').write_bytes(b'1')
            (desk_dir / '~$正在编辑的合同.docx').write_bytes(b'1')
            (desk_dir / 'desktop.ini').write_text('x', encoding='utf-8')

            # 正常新文件（中文命名）
            f1 = dl_dir / '报销发票.pdf'
            f2 = desk_dir / '屏幕截图_01.png'
            f1.write_bytes(b'pdf')
            f2.write_bytes(b'png')
            os.utime(f1, (1000, 1000))
            os.utime(f2, (2000, 2000))

            items = qh.scan_recent_files(limit=5, dir_pairs=[('下载', dl_dir), ('桌面', desk_dir)])
            self.assertEqual([x['name'] for x in items], ['屏幕截图_01.png', '报销发票.pdf'])
            self.assertEqual([x['source'] for x in items], ['桌面', '下载'])
            self.assertEqual(items[0]['icon'], '🖼️')
            self.assertEqual(items[1]['icon'], '📕')

            # 空目录边界
            empty_dir = base / '空文件夹'
            empty_dir.mkdir()
            self.assertEqual(qh.scan_recent_files(limit=5, dir_pairs=[('桌面', empty_dir)]), [])

            # 同级目录下自动探测 DeskKit（无需硬编码路径）
            app_dir = base / '白'
            dk_dir = base / '本地工具箱'
            app_dir.mkdir()
            dk_dir.mkdir()
            dk_cmd = dk_dir / 'DeskKit.cmd'
            dk_cmd.write_text('@echo off\n', encoding='utf-8')
            found = qh.find_deskkit_entry('', str(app_dir))
            self.assertEqual(found, dk_cmd)

        # 随手便签清洗与条数/长度边界
        notes = qh.sanitize_notes([
            '  取快递 5-2-108  ',
            {'text': '下午三点开会', 'done': 1},
            {'text': '   '},
            None,
            123,
        ])
        self.assertEqual(notes, [
            {'text': '取快递 5-2-108', 'done': False},
            {'text': '下午三点开会', 'done': True},
        ])
        self.assertEqual(qh.sanitize_notes('bad_type'), [])

        # 倒计时文本格式化边界
        self.assertEqual(qh.format_countdown(0, '专注'), '')
        self.assertEqual(qh.format_countdown(-5, '专注'), '')
        self.assertEqual(qh.format_countdown(1499.2, '专注'), '⏳ 专注 25:00')
        self.assertEqual(qh.format_countdown(65, '泡面'), '⏳ 泡面 01:05')
        self.assertEqual(qh.format_countdown(3661, '学习'), '⏳ 学习 1:01:01')

        # 屏幕常亮状态机与退出释放
        calls = []
        with mock.patch.object(qh.kernel32, 'SetThreadExecutionState', side_effect=lambda f: calls.append(f) or 1):
            awake = qh.KeepAwake()
            self.assertTrue(awake.toggle())
            self.assertEqual(calls[-1], qh.ES_CONTINUOUS | qh.ES_SYSTEM_REQUIRED | qh.ES_DISPLAY_REQUIRED)
            awake.release()
            self.assertFalse(awake.active)
            self.assertEqual(calls[-1], qh.ES_CONTINUOUS)

        # 验证 ui_helpers 统一去重后的可用字体过滤
        import ui_helpers as uh
        fake_fams = ['@SimSun', 'Wingdings', 'Symbol', '汉仪中黑简', 'Segoe UI', 'Microsoft YaHei UI']
        with mock.patch.object(uh.tkfont, 'families', return_value=fake_fams):
            filtered = uh.usable_fonts(('Microsoft YaHei UI', 'Segoe UI'))
            self.assertEqual(filtered, ['Microsoft YaHei UI', 'Segoe UI'])


if __name__ == '__main__':
    unittest.main()
