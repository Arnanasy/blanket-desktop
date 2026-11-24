"""净桌面核心边界逻辑单元测试（纯标准库，不触碰真实配置、注册表与网络）。"""
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
        c1 = r'"C:\Program Files\Python311\pythonw.exe" "C:\Users\demo\清桌面\launch.pyw"'
        c2 = r'"C:\Program Files\Python312\pythonw.exe" "C:\Users\demo\清桌面\launch.pyw"'
        c3 = r'"C:\Program Files\Python311\pythonw.exe" "D:\Tools\清桌面\launch.pyw"'
        c1_case = r'"c:\program files\python311\pythonw.exe" "c:\users\demo\清桌面\launch.pyw"'

        self.assertEqual(preferences._command_parts(c1), preferences._command_parts(c1_case))
        self.assertNotEqual(preferences._command_parts(c1), preferences._command_parts(c2))
        self.assertNotEqual(preferences._command_parts(c1), preferences._command_parts(c3))
        self.assertEqual(preferences._command_parts('only_one_arg'), ('', ''))


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


if __name__ == '__main__':
    unittest.main()
