"""自动日夜主题切换（固定时间段 / 当地日出日落）与电脑闲置挂件压暗计算。"""
from datetime import datetime, timedelta
import math
import re

_TIME_RE = re.compile(r'^([01]?\d|2[0-3]):([0-5]?\d)$')

DEFAULT_THEME_SCHEDULE = {
    'enabled': False,
    'mode': 'fixed',          # 'fixed'（固定时间段）或 'solar'（跟随当地日出日落）
    'dark_time': '19:00',
    'light_time': '07:00',
    'solar_point': None,      # [lat, lon]
    'solar_place': '',
    'manual_window': '',      # 最近一次手动切换所在窗口，跨过下一个切换点自动解除
}

DEFAULT_IDLE_DIM = {
    'enabled': False,
    'minutes': 5,             # 闲置几分钟后压暗桌面时钟与系统监控（1..60）
    'opacity': 35,            # 压暗后的不透明度百分比（15..80）
}


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def normalize_hhmm(value, fallback):
    """校验并规范化 HH:MM 时间字符串（例如 '7:5' -> '07:05'），非法返回 fallback。"""
    if not isinstance(value, str):
        return fallback
    m = _TIME_RE.fullmatch(value.strip())
    if not m:
        return fallback
    return f'{int(m.group(1)):02d}:{int(m.group(2)):02d}'


def parse_hhmm_minutes(hhmm):
    """把规范化的 HH:MM 转换为距当日零点的分钟数。"""
    h, m = hhmm.split(':', 1)
    return int(h) * 60 + int(m)


def _valid_point(pt):
    if isinstance(pt, (list, tuple)) and len(pt) == 2 and all(_is_number(v) for v in pt):
        lat, lon = float(pt[0]), float(pt[1])
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return [round(lat, 2), round(lon, 2)]
    return None


def sanitize_theme_schedule(raw):
    cfg = dict(DEFAULT_THEME_SCHEDULE)
    if not isinstance(raw, dict):
        return cfg
    if isinstance(raw.get('enabled'), bool):
        cfg['enabled'] = raw['enabled']
    if raw.get('mode') in ('fixed', 'solar'):
        cfg['mode'] = raw['mode']
    cfg['dark_time'] = normalize_hhmm(raw.get('dark_time'), DEFAULT_THEME_SCHEDULE['dark_time'])
    cfg['light_time'] = normalize_hhmm(raw.get('light_time'), DEFAULT_THEME_SCHEDULE['light_time'])
    cfg['solar_point'] = _valid_point(raw.get('solar_point'))
    if isinstance(raw.get('solar_place'), str):
        cfg['solar_place'] = raw['solar_place'].strip()[:40]
    if isinstance(raw.get('manual_window'), str):
        cfg['manual_window'] = raw['manual_window'].strip()[:40]
    return cfg


def sanitize_idle_dim(raw):
    cfg = dict(DEFAULT_IDLE_DIM)
    if not isinstance(raw, dict):
        return cfg
    if isinstance(raw.get('enabled'), bool):
        cfg['enabled'] = raw['enabled']
    mins = raw.get('minutes')
    if isinstance(mins, int) and not isinstance(mins, bool):
        cfg['minutes'] = max(1, min(60, mins))
    op = raw.get('opacity')
    if isinstance(op, int) and not isinstance(op, bool):
        cfg['opacity'] = max(15, min(80, op))
    return cfg


def _local_tz_offset_hours(target_date):
    dt = datetime(target_date.year, target_date.month, target_date.day, 12, 0)
    offset = dt.astimezone().utcoffset()
    return (offset.total_seconds() / 3600.0) if offset is not None else 8.0


def calc_sun_times(lat, lon, target_date=None, tz_offset_hours=None):
    """用纯标准库 NOAA 太阳位置公式计算指定日期的本地日出/日落时间。

    极昼/极夜等无日出日落情况返回 (None, None)。
    """
    if target_date is None:
        target_date = datetime.now().date()
    if tz_offset_hours is None:
        tz_offset_hours = _local_tz_offset_hours(target_date)

    lat_f = max(-89.9, min(89.9, float(lat)))
    lon_f = float(lon)
    doy = target_date.timetuple().tm_yday
    # 太阳在一年中的弧度角（NOAA 近似）
    gamma = 2.0 * math.pi / 365.0 * (doy - 1)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    lat_rad = math.radians(lat_f)
    zenith_rad = math.radians(90.833)
    denom = math.cos(lat_rad) * math.cos(decl)
    if abs(denom) < 1e-9:
        return None, None
    cos_ha = (math.cos(zenith_rad) / denom) - math.tan(lat_rad) * math.tan(decl)
    if cos_ha < -1.0 or cos_ha > 1.0:
        return None, None
    ha_deg = math.degrees(math.acos(cos_ha))

    sunrise_min = 720.0 - 4.0 * (lon_f + ha_deg) - eqtime + tz_offset_hours * 60.0
    sunset_min = 720.0 - 4.0 * (lon_f - ha_deg) - eqtime + tz_offset_hours * 60.0
    base = datetime(target_date.year, target_date.month, target_date.day)
    return base + timedelta(minutes=round(sunrise_min)), base + timedelta(minutes=round(sunset_min))


def _minutes_to_hhmm(total_minutes):
    m = int(round(total_minutes)) % 1440
    return f'{m // 60:02d}:{m % 60:02d}'


def resolve_schedule_state(cfg, now_dt=None, tz_offset_hours=None, weather_cache=None):
    """计算当前时刻的目标主题（深色/浅色）以及当前周期窗口标识。"""
    s_cfg = sanitize_theme_schedule(cfg)
    now_dt = now_dt or datetime.now()
    today = now_dt.date()
    yesterday = today - timedelta(days=1)

    point = None
    if isinstance(weather_cache, dict):
        point = _valid_point(weather_cache.get('point'))
    if point is None:
        point = s_cfg.get('solar_point')

    using_solar = False
    light_hhmm = s_cfg['light_time']
    dark_hhmm = s_cfg['dark_time']
    if s_cfg['mode'] == 'solar' and point is not None:
        sr, ss = calc_sun_times(point[0], point[1], target_date=today, tz_offset_hours=tz_offset_hours)
        if sr is not None and ss is not None and 0 < (ss - sr).total_seconds() < 86400:
            light_hhmm = _minutes_to_hhmm(sr.hour * 60 + sr.minute)
            dark_hhmm = _minutes_to_hhmm(ss.hour * 60 + ss.minute)
            using_solar = True

    light_min = parse_hhmm_minutes(light_hhmm)
    dark_min = parse_hhmm_minutes(dark_hhmm)
    now_min = now_dt.hour * 60 + now_dt.minute

    if light_min == dark_min:
        target_dark = False
        window_id = f'{today.isoformat()}:same'
    elif light_min < dark_min:
        # 常规昼浅夜深：例如 07:00 浅色，19:00 深色
        if now_min < light_min:
            target_dark = True
            window_id = f'{yesterday.isoformat()}:dark'
        elif now_min < dark_min:
            target_dark = False
            window_id = f'{today.isoformat()}:light'
        else:
            target_dark = True
            window_id = f'{today.isoformat()}:dark'
    else:
        # 跨零点倒置配置：例如 02:00 浅色，22:00 深色 -> dark_min(1320) > light_min(120) 不会走这里，
        # 这里对应 dark_min < light_min（如 01:00 深色，07:00 浅色）
        if now_min < dark_min:
            target_dark = False
            window_id = f'{yesterday.isoformat()}:light'
        elif now_min < light_min:
            target_dark = True
            window_id = f'{today.isoformat()}:dark'
        else:
            target_dark = False
            window_id = f'{today.isoformat()}:light'

    return {
        'target_dark': target_dark,
        'window_id': window_id,
        'light_hhmm': light_hhmm,
        'dark_hhmm': dark_hhmm,
        'using_solar': using_solar,
        'solar_point': point,
    }


def compute_idle_dim_alpha(idle_cfg, idle_seconds, normal_opacity=100):
    """根据电脑闲置秒数返回 (有效不透明度 0..1, 是否处于压暗状态)。"""
    cfg = sanitize_idle_dim(idle_cfg)
    base_pct = max(10, min(100, int(normal_opacity)))
    if not cfg['enabled']:
        return base_pct / 100.0, False
    threshold_sec = cfg['minutes'] * 60.0
    if float(idle_seconds) < threshold_sec:
        return base_pct / 100.0, False
    dimmed_pct = min(base_pct, cfg['opacity'])
    return dimmed_pct / 100.0, True
