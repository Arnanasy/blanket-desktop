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

    def test_real_winreg_autostart_set_and_repair_in_isolated_key(self):
        """在真实 Windows 注册表的独立临时测试子键下实测开机自启写入、路径自修复、旧键迁移与清理（零触碰真实系统自启项）。"""
        import winreg
        test_root = f'Software\\Blanket_UnitTest_{os.getpid()}'
        run_sub = f'{test_root}\\Run'
        app_sub = f'{test_root}\\StartupApproved'

        def cleanup_tree():
            for sub in (run_sub, app_sub, test_root):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                except OSError:
                    pass

        winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_sub).Close()
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, app_sub).Close()
        try:
            with mock.patch.object(preferences, 'RUN_KEY', run_sub), \
                 mock.patch.object(preferences, 'STARTUP_APPROVED_KEY', app_sub):
                # 1. 初始未开启
                self.assertFalse(preferences.autostart_enabled())
                self.assertFalse(preferences.repair_autostart())

                # 2. 模拟存在旧版 JingZhuoMian 指向旧文件夹路径 -> repair_autostart 真实迁移并修复路径
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_sub, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, 'JingZhuoMian', 0, winreg.REG_SZ, r'"C:\OldPython\pythonw.exe" "D:\OldDir\launch.pyw"')
                self.assertFalse(preferences.autostart_enabled())
                self.assertTrue(preferences.repair_autostart())
                self.assertTrue(preferences.autostart_enabled())
                self.assertIsNone(preferences._registered_command('JingZhuoMian'))
                self.assertEqual(preferences._registered_command('Blanket'), preferences.autostart_command())

                # 3. 关闭自启后，Run 与 StartupApproved 下的记录均应被真实清空
                self.assertTrue(preferences.set_autostart(False))
                self.assertFalse(preferences.autostart_enabled())
                self.assertIsNone(preferences._registered_command('Blanket'))
        finally:
            cleanup_tree()


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
             mock.patch.object(dc, '_is_desktop_hwnd', return_value=True), \
             mock.patch.object(dc, 'is_blank_click', return_value=True) as blank_fn:
            self.assertFalse(dc.check(suppress=True))
            self.assertFalse(dc.check(suppress=True))
            blank_fn.assert_not_called()

        # 按下鼠标并拖动桌面图标（位移 > 6px）：从按下到松手全程严禁调用 is_blank_click（零跨进程插队！）
        with mock.patch.object(dwin, 'key_down', side_effect=[True, True, False]), \
             mock.patch.object(dwin, 'get_cursor', side_effect=[(100, 100), (140, 120), (220, 180)]), \
             mock.patch.object(dc, '_is_desktop_hwnd', return_value=True), \
             mock.patch.object(dc, 'is_blank_click', return_value=True) as blank_drag:
            self.assertFalse(dc.check(suppress=False))  # 按下瞬间：零查询
            blank_drag.assert_not_called()
            self.assertFalse(dc.check(suppress=False))  # 拖拽中途：零查询
            self.assertFalse(dc.check(suppress=False))  # 拖完松手：因位移 > 6px 仍零查询
            blank_drag.assert_not_called()

        # 已在桌面（suppress=False）且为短促原地单击：按下时不查，松手后才查一次 is_blank_click
        with mock.patch.object(dwin, 'key_down', side_effect=[True, False]), \
             mock.patch.object(dwin, 'get_cursor', return_value=(100, 100)), \
             mock.patch.object(dc, '_is_desktop_hwnd', return_value=True), \
             mock.patch.object(dc, 'is_blank_click', return_value=True) as blank_click:
            self.assertFalse(dc.check(suppress=False))
            blank_click.assert_not_called()             # 按下瞬间绝不调用 AccessibleObjectFromPoint！
            self.assertTrue(dc.check(suppress=False))
            blank_click.assert_called_once_with(100, 100)

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
            self.assertEqual(found.resolve(), dk_cmd.resolve())

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

        # 验证 CF_HDROP 文件剪贴板二进制结构与内存句柄释放安全性
        with tempfile.TemporaryDirectory() as tmp2:
            sample_f = Path(tmp2) / '微信待发合同.pdf'
            sample_f.write_bytes(b'%PDF-1.4')
            blob = qh.build_cf_hdrop_bytes(sample_f)
            # 前 20 字节为 DROPFILES，pFiles=20, fWide=1
            self.assertEqual(int.from_bytes(blob[0:4], 'little'), 20)
            self.assertEqual(int.from_bytes(blob[16:20], 'little'), 1)
            self.assertTrue(blob.endswith(b'\x00\x00\x00\x00'))
            decoded_path = blob[20:-4].decode('utf-16-le')
            self.assertEqual(decoded_path, str(sample_f.resolve()))

            # SetClipboardData 成功时不得误调 GlobalFree；失败时必须调用 GlobalFree 防泄漏
            freed = []
            buf = ctypes.create_string_buffer(len(blob) + 16)
            addr = ctypes.addressof(buf)
            with mock.patch.object(qh.kernel32, 'GlobalAlloc', return_value=addr), \
                 mock.patch.object(qh.kernel32, 'GlobalLock', return_value=addr), \
                 mock.patch.object(qh.kernel32, 'GlobalUnlock', return_value=1), \
                 mock.patch.object(qh.kernel32, 'GlobalFree', side_effect=lambda h: freed.append(h) or 0), \
                 mock.patch.object(qh.user32, 'OpenClipboard', return_value=1), \
                 mock.patch.object(qh.user32, 'EmptyClipboard', return_value=1), \
                 mock.patch.object(qh.user32, 'CloseClipboard', return_value=1):
                with mock.patch.object(qh.user32, 'SetClipboardData', return_value=addr):
                    self.assertTrue(qh.copy_file_to_clipboard(sample_f))
                    self.assertEqual(freed, [])
                with mock.patch.object(qh.user32, 'SetClipboardData', return_value=0):
                    self.assertFalse(qh.copy_file_to_clipboard(sample_f))
                    self.assertEqual(freed, [addr])

        # 验证独立番茄钟配置清洗、自定义颜色与随机结束语（防连续重复）
        import countdown_widget as cw
        c_cfg = cw.sanitize_countdown_cfg({'size': 999, 'opacity': 10, 'style': '未知', 'fg': 'bad', 'bg': '#112233'})
        self.assertEqual(c_cfg['size'], 56)
        self.assertEqual(c_cfg['opacity'], 35)
        self.assertEqual(c_cfg['style'], '圆角卡片')
        self.assertEqual(c_cfg['fg'], cw.DEFAULTS['fg'])
        self.assertEqual(c_cfg['bg'], '#112233')
        self.assertEqual(cw.sanitize_quotes([]), list(cw.DEFAULT_FINISH_QUOTES))
        self.assertEqual(cw.sanitize_quotes(['  喝口水  ', '']), ['喝口水'])
        self.assertEqual(cw.pick_finish_quote(['句A', '句B'], last_quote='句A'), '句B')

        # 验证右侧快捷中枢外观样式清洗边界
        import launcher_bar as lb
        bs = lb.sanitize_bar_style({'font_size': 99, 'opacity': 10, 'bg': 'bad', 'card_bg': '#FFFFFF'})
        self.assertEqual(bs['font_size'], 14)
        self.assertEqual(bs['opacity'], 45)
        self.assertEqual(bs['bg'], lb.BAR_STYLE_DEFAULTS['bg'])
        self.assertEqual(bs['card_bg'], '#FFFFFF')

    def test_audit_fixes_clipboard_formats_dual_monitor_weather_lyrics_and_autostart(self):
        """覆盖深度体检修复项：多格式文件剪贴板(网页/AI软件兼容)、双屏防溢出、20端口探针、静默自启、歌词断网重试与温度排序。"""
        import countdown_widget as cw
        import lyrics_widget as lw
        import quick_hub as qh
        import theme_button as tb
        import widget_layout as wl

        # 1. 开机自启默认携带 --minimized，且旧版无 --minimized 注册表项会自动修复补齐
        cmd_auto = preferences.autostart_command()
        self.assertIn('--minimized', cmd_auto)
        exe_only = r'"C:\Users\demo\AppData\Local\Programs\Blanket\Blanket.exe" --minimized'
        self.assertEqual(
            preferences._command_parts(exe_only),
            (os.path.normcase(r'C:\Users\demo\AppData\Local\Programs\Blanket\Blanket.exe'), ''),
        )
        old_no_min = cmd_auto.replace(' --minimized', '')
        reg_store = {(preferences.RUN_KEY, 'Blanket'): old_no_min}
        written = {}

        class _RegKey:
            def __init__(self, sub): self.sub = sub
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with mock.patch.object(preferences.winreg, 'OpenKey', side_effect=lambda _r, s, *_a: _RegKey(s)), \
             mock.patch.object(preferences.winreg, 'QueryValueEx',
                               side_effect=lambda k, n: (reg_store[(k.sub, n)], 1) if (k.sub, n) in reg_store else (_ for _ in ()).throw(FileNotFoundError(n))), \
             mock.patch.object(preferences.winreg, 'SetValueEx', side_effect=lambda k, n, _r, _t, v: written.__setitem__((k.sub, n), v)):
            self.assertFalse(preferences.autostart_enabled())
            self.assertTrue(preferences.repair_autostart())
            self.assertIn('--minimized', written[(preferences.RUN_KEY, 'Blanket')])

        # 2. 多格式文件剪贴板（CF_HDROP + Preferred DropEffect + FileNameW）与路径纯文本复制
        with tempfile.TemporaryDirectory() as tmp:
            cn_sub = Path(tmp) / '中文 AI 测试目录'
            cn_sub.mkdir()
            f_comma = cn_sub / '2026,Q1汇报,终版.pdf'
            f_comma.write_bytes(b'%PDF-1.4')

            hdrop_b, extras = qh.build_file_clipboard_payloads(f_comma)
            extra_map = dict(extras)
            self.assertIn('Preferred DropEffect', extra_map)
            self.assertEqual(int.from_bytes(extra_map['Preferred DropEffect'], 'little'), 1)
            self.assertIn('FileNameW', extra_map)
            self.assertEqual(
                extra_map['FileNameW'][:-2].decode('utf-16-le'),
                str(f_comma.resolve()),
            )

            # 模拟剪贴板同时注册并写入多格式；扩展格式写入失败时释放自身句柄且主格式仍返回成功
            set_calls = []
            freed_ext = []
            bufs = [ctypes.create_string_buffer(512) for _ in range(6)]
            addrs = [ctypes.addressof(b) for b in bufs]
            alloc_iter = iter(addrs)

            def fake_set_clip(fmt_id, handle):
                set_calls.append((fmt_id, handle))
                return handle if fmt_id != 9002 else 0  # 模拟第 2 个扩展格式失败

            fmt_ids = {'Preferred DropEffect': 9001, 'FileNameW': 9002, 'FileName': 9003}
            with mock.patch.object(qh.kernel32, 'GlobalAlloc', side_effect=lambda _f, _s: next(alloc_iter)), \
                 mock.patch.object(qh.kernel32, 'GlobalLock', side_effect=lambda h: h), \
                 mock.patch.object(qh.kernel32, 'GlobalUnlock', return_value=1), \
                 mock.patch.object(qh.kernel32, 'GlobalFree', side_effect=lambda h: freed_ext.append(h) or 0), \
                 mock.patch.object(qh.user32, 'OpenClipboard', return_value=1), \
                 mock.patch.object(qh.user32, 'EmptyClipboard', return_value=1), \
                 mock.patch.object(qh.user32, 'CloseClipboard', return_value=1), \
                 mock.patch.object(qh.user32, 'RegisterClipboardFormatW', side_effect=lambda n: fmt_ids.get(n, 0)), \
                 mock.patch.object(qh.user32, 'SetClipboardData', side_effect=fake_set_clip):
                self.assertTrue(qh.copy_file_to_clipboard(f_comma))
                written_fmts = [f for f, _ in set_calls]
                self.assertIn(qh.CF_HDROP, written_fmts)
                self.assertIn(9001, written_fmts)
                self.assertIn(9002, written_fmts)
                self.assertEqual(len(freed_ext), 1)  # 仅失败的 9002 句柄被释放

                # 复制完整路径纯文本（CF_UNICODETEXT）
                set_calls.clear()
                self.assertTrue(qh.copy_path_to_clipboard(f_comma))
                self.assertEqual(set_calls[-1][0], qh.CF_UNICODETEXT)

            self.assertFalse(qh.copy_path_to_clipboard(cn_sub / '不存在.pdf'))

            # 含英文逗号文件名定位：优先走 SHOpenFolderAndSelectItems 且释放 PIDL
            freed_pidls = []
            with mock.patch.object(qh.ole32, 'CoInitializeEx', return_value=0), \
                 mock.patch.object(qh.ole32, 'CoUninitialize'), \
                 mock.patch.object(qh.shell32, 'ILCreateFromPathW', return_value=777), \
                 mock.patch.object(qh.shell32, 'SHOpenFolderAndSelectItems', return_value=0) as sel_mock, \
                 mock.patch.object(qh.shell32, 'ILFree', side_effect=lambda p: freed_pidls.append(p)):
                self.assertTrue(qh.reveal_in_explorer(f_comma))
                sel_mock.assert_called_once_with(777, 0, None, 0)
                self.assertEqual(freed_pidls, [777])

        # 3. DeskKit 20 端口探针：忽略非 DeskKit 的 200 响应，精准命中高序号端口（如 18658）
        class _FakeResp:
            def __init__(self, status, body):
                self.status = status
                self._body = body
            def read(self, *_a): return self._body
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(url, timeout=0.1):
            if ':18642/' in url:
                return _FakeResp(200, b'<html>not deskkit</html>')
            if ':18643/' in url:
                return _FakeResp(200, b'{"ok": false}')
            if ':18658/' in url:  # 第 17 个端口（超出原 span=6）
                return _FakeResp(200, b'{"ok": true}')
            raise OSError('refused')

        with mock.patch.object(qh, 'urlopen', side_effect=fake_urlopen):
            self.assertEqual(qh._running_deskkit_port(), 18658)

        # 4. 双显示器右边缘主题按钮裁剪：半隐藏在主屏右边缘 (1914) 时宽度裁为 6px，绝不越过 1920 戳入右副屏
        ix, iy, vw, vh = tb.calc_clipped_button_rect(1914, 100, 48, 1920)
        self.assertEqual((ix, iy, vw, vh), (1914, 100, 6, 48))
        self.assertLessEqual(ix + vw, 1920)
        ix2, iy2, vw2, vh2 = tb.calc_clipped_button_rect(1866, 100, 48, 1920)
        self.assertEqual((ix2, vw2), (1866, 48))

        # 5. 多屏右边缘番茄钟长句防溢出 & 不误跳入右侧副屏 (1920..3840)
        dual_bounds = lambda x, y, work=True: (0, 0, 1920, 1040) if x < 1920 else (1920, 0, 3840, 1040)
        rx, ry = wl.resolve_anchor(None, [1780, 200], 520, 60, bounds_fn=dual_bounds, margin=8)
        self.assertEqual(rx, 1920 - 520 - 8)
        self.assertEqual(ry, 200)

        # 6. 国内天气温度高低反向修正（含负温）
        self.assertEqual(local_weather._format_temp_range('31℃', '20℃'), '20℃–31℃')
        self.assertEqual(local_weather._format_temp_range('-2℃', '-10℃'), '-10℃–-2℃')
        self.assertEqual(local_weather._format_temp_range('18℃', '26℃'), '18℃–26℃')

        # 7. Wallpaper Engine 动态壁纸层级辅助判定
        with mock.patch('desktop_windows.find_desktop_windows', return_value=(1001, 1002)), \
             mock.patch.object(win32.user32, 'GetAncestor', return_value=1000), \
             mock.patch.object(win32.user32, 'GetWindow', return_value=2000), \
             mock.patch.object(win32, 'GetWindowLongPtr', return_value=0):
            # 存在非置顶普通下层窗口 2000 -> 插在 2000 之后（即桌面 1000 正上方）
            self.assertEqual(win32._desktop_insert_after(9999), 2000)
            # 自身已在桌面正上方 -> 返回 None 保持原位
            self.assertIsNone(win32._desktop_insert_after(2000))
            # 即使 on_desktop=True 也不抬到 HWND_TOP，避免压住半屏普通窗口
            with mock.patch.object(win32.user32, 'IsIconic', return_value=False), \
                 mock.patch.object(win32.user32, 'SetWindowPos') as swp:
                win32.place_on_desktop(9999, 10, 20, on_desktop=True)
                self.assertEqual(swp.call_args[0][1], 2000)

        # 7b. SMTC 启动失败时按退避冷却，避免每 60ms 重建线程
        import media_widget as mw
        smtc_w = mw._SmtcWorker()
        smtc_w._retry_after = time.monotonic() + 20.0
        with mock.patch.object(mw, 'HAS_WINRT', True), \
             mock.patch.object(mw, 'Thread') as smtc_th:
            smtc_w.start()
            smtc_th.assert_not_called()

        # 8. 歌词断网异常时不写入永久空缓存，正常无歌词时才缓存空列表
        dummy_overlay = lw.LyricsOverlay.__new__(lw.LyricsOverlay)
        dummy_overlay._cache = {}
        dummy_overlay._retry_after = {}
        dummy_overlay._fetching = set()

        def failing_net(url, *a, **kw):
            stats = getattr(lw._REQ_STATE, 'stats', None)
            if isinstance(stats, dict):
                stats['errors'] = stats.get('errors', 0) + 1
            raise OSError('offline')

        with mock.patch.object(lw, '_http_json', side_effect=failing_net), \
             mock.patch.object(lw, 'Thread') as th_mock:
            th_mock.side_effect = lambda target, **kw: type('T', (), {'start': staticmethod(target)})()
            dummy_overlay._request_lyrics(('断网歌', '歌手'), '断网歌', '歌手', 200.0, now=10.0)
            self.assertNotIn(('断网歌', '歌手'), dummy_overlay._cache)
            self.assertGreater(dummy_overlay._retry_after.get(('断网歌', '歌手'), 0), 10.0)

    def test_real_tk_widgets_scroll_footer_countdown_preview_and_monitor_wheel(self):
        """在隐藏 Tk 根窗口沙盒中实测：抽屉超高自动滚动且底部三按钮不裁切、番茄钟试看优先级与长句收拢、系统监控 Shift+滚轮。"""
        if os.environ.get('CI') or os.environ.get('GITHUB_ACTIONS'):
            self.skipTest('云端无交互式桌面环境跳过真实 Tk 窗口渲染测试（本机完整执行）')
        import tkinter as tk
        import countdown_widget as cw
        import launcher_bar as lb
        import sys_monitor as sm

        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'当前环境无法初始化 Tk 窗口：{exc}')
        root.withdraw()
        try:
            class _FakeThemeBtn:
                dark = True

            class _FakeApp:
                _quitting = False
                def toggle_theme(self): pass
                def open_settings(self): pass
                def discard_mouse(self): pass

            class _FakeWidgets:
                def __init__(self, r):
                    self.root = r
                    self.app = _FakeApp()
                    self.ui_scale = 1.25
                    self.theme_button = _FakeThemeBtn()
                    self.cfg = {}
                    self.countdown = None
                def settings(self): pass
                def save(self): pass
                def queue_save(self): pass
                def start_countdown(self, m, l): pass
                def cancel_countdown(self): pass
                def current_countdown_text(self, now=None): return ''

            fw = _FakeWidgets(root)

            # 1. 番茄钟：倒计时运行中点“试看一句”不被倒计时挡死，且右边缘长句重排不出右边界
            with mock.patch.object(cw, 'place_on_desktop') as pod_mock:
                cd = cw.CountdownWidget(fw, {'position': [1550, 180]}, on_save=lambda: None)
                fw.countdown = cd
                cd.start(25, '专注')
                self.assertIn('25:00', cd.current_text())
                cd._preview_msg = '这是一句非常长非常长的番茄钟结束试看提示语用来测试屏幕右边界收拢效果'
                cd._preview_until = time.monotonic() + 5.0
                self.assertTrue(cd.current_text().startswith('🔔 这是一句'))

                with mock.patch.object(cw, 'bounds', return_value=(0, 0, 1707, 1020)), \
                     mock.patch('widget_layout.bounds', return_value=(0, 0, 1707, 1020)):
                    cd.tick(time.monotonic(), blocked=False, on_desktop=True)
                    placed_x = pod_mock.call_args[0][1]
                    req_w = cd._last_size[0]
                    self.assertLessEqual(placed_x + req_w, 1707 - 8)

            # 2. 右侧快捷抽屉：14 号大字 + 9 条便签 + 展开分组在矮屏幕 (520px 高) 下自动启用滚动且吸底栏完整保留
            bar_cfg = {
                'bar_style': {'font_size': 14},
                'notes': [f'待办便签测试条目第 {i} 条' for i in range(9)],
                'groups': {'常用工具': ['C:\\Windows\\notepad.exe'] * 5},
            }
            bar = lb.LauncherBar(fw, bar_cfg)
            bar.groups_section_open = True
            bar.expanded.add('常用工具')
            bar.render()
            with mock.patch.object(lb, 'bounds', return_value=(0, 0, 1920, 520)):
                bar._reposition(1910, 260)
                self.assertTrue(bar._scrollable)
                self.assertGreater(bar.foot.winfo_reqheight(), 20)
                # recolor 不应销毁重建控件
                old_body = bar.body
                bar.recolor('#123456')
                self.assertIs(bar.body, old_body)
            bar.close()

            # 3. 系统监控：裸滚轮不误改透明度，Shift+滚轮改透明度，Ctrl+滚轮改字号
            mon = sm.SystemMonitor(fw, lambda x, y, work=True: (0, 0, 1920, 1040))
            orig_alpha = mon.cfg['alpha']
            orig_size = mon.cfg['size']
            Evt = lambda st, d: type('E', (), {'state': st, 'delta': d})()
            mon.wheel(Evt(0, 120))          # 裸滚轮：忽略
            self.assertEqual(mon.cfg['alpha'], orig_alpha)
            self.assertEqual(mon.cfg['size'], orig_size)
            mon.wheel(Evt(1, -120))         # Shift + 向下滚：透明度 -5
            self.assertEqual(mon.cfg['alpha'], orig_alpha - 5)
            mon.wheel(Evt(4, 120))          # Ctrl + 向上滚：字号 +1
            self.assertEqual(mon.cfg['size'], orig_size + 1)
            mon.cancel_save()
        finally:
            root.destroy()

    def test_monitor_layered_render_pin_toggle_game_raise_and_checkmark_style(self):
        """实测本次 3 项升级：真逐像素透明监控渲染、全软件圆角 ✓/□ 复选框样式、快捷面板与托盘一键置顶及游戏重申置顶。"""
        import sys_monitor as sm
        import tray_icon as ti
        import ui_helpers as uh

        # 1. 真逐像素透明渲染（透明 / 圆角 / 方形 / 空数据 / 中文与符号）
        if sm.HAS_PIL and sm._find_monitor_font_path():
            rows = [('CPU', '4%'), ('内存', '59%'), ('温度', '49°C'), ('↓', '1.2 MiB/s'), ('延迟', '46 ms')]
            for shape in ('透明', '圆角', '方形'):
                frame = sm.render_monitor_rgba(rows, 19, shape, (238, 213, 251), (36, 48, 64), ui_scale=1.25)
                self.assertIsNotNone(frame)
                w_px, h_px, bgra = frame
                self.assertGreater(w_px, 60)
                self.assertGreater(h_px, 40)
                self.assertEqual(len(bgra), w_px * h_px * 4)
                if shape == '透明':
                    # 左上角背景像素应为肉眼不可见的 alpha=1 占位（无黑边/绿幕残渣），且预乘 RGB 为 0
                    self.assertEqual((bgra[0], bgra[1], bgra[2], bgra[3]), (0, 0, 0, 1))
                elif shape == '圆角':
                    # 圆角矩形最外角应完全透明（alpha=0），无黑色锯齿
                    self.assertEqual(bgra[3], 0)
            # 空列表兜底不崩
            empty_frame = sm.render_monitor_rgba([], 15, '透明', (241, 245, 249), (36, 48, 64))
            self.assertIsNotNone(empty_frame)

        # 2. 托盘菜单包含“置顶系统监控”且选中后触发回调
        appended = []
        toggled_pin = []
        tray = ti.TrayIcon(100, lambda: None, lambda: None, lambda: None)
        tray._proc_ref = object()
        tray.get_monitor_pinned = lambda: True
        tray.on_toggle_monitor_pin = lambda: toggled_pin.append(True)
        with mock.patch.object(ti.user32, 'CreatePopupMenu', return_value=500), \
             mock.patch.object(ti.user32, 'AppendMenuW', side_effect=lambda m, f, cmd, txt: appended.append((f, cmd, txt)) or 1), \
             mock.patch.object(ti.user32, 'SetForegroundWindow', return_value=1), \
             mock.patch.object(ti.user32, 'TrackPopupMenu', return_value=ti.IDM_MONITOR_PIN), \
             mock.patch.object(ti.user32, 'PostMessageW', return_value=1), \
             mock.patch.object(ti.user32, 'DestroyMenu', return_value=1):
            tray._show_menu()
        pin_entries = [e for e in appended if e[1] == ti.IDM_MONITOR_PIN]
        self.assertEqual(len(pin_entries), 1)
        self.assertTrue(pin_entries[0][0] & ti.MF_CHECKED)
        self.assertEqual(toggled_pin, [True])

        if os.environ.get('CI') or os.environ.get('GITHUB_ACTIONS'):
            return

        import tkinter as tk
        from tkinter import ttk
        import launcher_bar as lb

        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'当前环境无法初始化 Tk 窗口：{exc}')
        root.withdraw()
        try:
            # 3. 全局 clam 复选框替换为圆角 ✓ / □（浅色/深色反复切换不报 Duplicate element，无 Pillow 回退也可用）
            style = ttk.Style(root)
            style.theme_use('clam')
            e_light = uh.install_checkmark_style(root, style, dark=False, ui_scale=1.25)
            e_dark = uh.install_checkmark_style(root, style, dark=True, ui_scale=1.25)
            e_light2 = uh.install_checkmark_style(root, style, dark=False, ui_scale=1.25)
            self.assertEqual(e_light, e_light2)
            self.assertNotEqual(e_light, e_dark)
            self.assertIn(e_light, str(style.layout('TCheckbutton')))
            # 模拟无 Pillow 环境直接构造兜底图标
            with mock.patch.object(uh, '_HAS_PIL', False):
                fb_on = uh._make_checkbox_image(root, 16, True, '#2563EB', '#2563EB', '#FFFFFF')
                fb_off = uh._make_checkbox_image(root, 16, False, '#94A3B8', '#FFFFFF', '#FFFFFF')
                self.assertGreater(fb_on.width(), 16)
                self.assertEqual(fb_off.height(), 16)

            # 4. 真实 Tk + Win32 UpdateLayeredWindow 监控置顶联动与游戏 Z-order 检查
            class _FakeThemeBtn:
                dark = False

            class _FakeApp:
                _quitting = False
                def toggle_theme(self): pass
                def open_settings(self): pass
                def discard_mouse(self): pass

            class _FakeWidgets:
                def __init__(self, r):
                    self.root = r
                    self.app = _FakeApp()
                    self.ui_scale = 1.25
                    self.theme_button = _FakeThemeBtn()
                    self.cfg = {'monitor': {'pinned': False, 'over_fullscreen': False, 'shape': '透明', 'fg': '#eed5fb'}}
                    self.countdown = None
                    self.bar = None
                    self.monitor = None
                    self.saved = 0
                def settings(self): pass
                def save(self): self.saved += 1
                def queue_save(self): pass
                def start_countdown(self, m, l): pass
                def cancel_countdown(self): pass
                def current_countdown_text(self, now=None): return ''

            fw = _FakeWidgets(root)
            fw.bar = lb.LauncherBar(fw, fw.cfg)
            fw.monitor = sm.SystemMonitor(fw, lambda x, y, work=True: (0, 0, 1920, 1040))
            self.assertIn('监控置顶', fw.bar.monitor_pin_btn.cget('text'))

            # 点一键置顶：开启 pinned + 自动确保 over_fullscreen=True + 立即显示且分层推送成功 + 快捷开关变已置顶
            fw.bar._toggle_monitor_pin()
            self.assertTrue(fw.monitor.is_pinned())
            self.assertTrue(fw.monitor.cfg['over_fullscreen'])
            self.assertTrue(fw.monitor.visible)
            self.assertIn('已置顶', fw.bar.monitor_pin_btn.cget('text'))
            if fw.monitor._layered:
                self.assertTrue(fw.monitor._commit_layered(x=fw.monitor._pos[0], y=fw.monitor._pos[1]))

            # 模拟全屏游戏窗口 (hwnd=8888) 把监控压在下面：_needs_raise 应立即检出返回 True
            fw.monitor._last_foreground = 8888
            with mock.patch.object(sm, 'GetWindowLongPtr', return_value=sm.WS_EX_TOPMOST), \
                 mock.patch.object(sm.user32, 'GetWindow', side_effect=[7777, 8888]):
                self.assertTrue(fw.monitor._needs_raise(8888))
            # 监控已在最顶层（GW_HWNDPREV 为 0）：不无谓触发 raise_
            with mock.patch.object(sm, 'GetWindowLongPtr', return_value=sm.WS_EX_TOPMOST), \
                 mock.patch.object(sm.user32, 'GetWindow', return_value=0):
                self.assertFalse(fw.monitor._needs_raise(8888))

            # 再次点击取消置顶：恢复隐藏且按钮恢复未置顶文字
            fw.bar._toggle_monitor_pin()
            self.assertFalse(fw.monitor.is_pinned())
            self.assertFalse(fw.monitor.visible)
            self.assertIn('监控置顶', fw.bar.monitor_pin_btn.cget('text'))

            fw.monitor.cancel_save()
            fw.bar.close()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
