"""时钟自定义格式与日期小彩蛋（农历、二十四节气、星座），纯标准库零依赖。"""
from datetime import date, datetime
import math
import re

DEFAULT_DATE_FORMAT = 'MM月dd日  EEEE'
DATE_FORMAT_PRESETS = (
    'MM月dd日  EEEE',
    'yyyy年MM月dd日  EEEE',
    'yyyy/MM/dd  EEE',
    'MM-dd  ddd',
    'yyyy.MM.dd',
)
DATE_ADDON_OPTIONS = ('无', '农历', '节气', '星座', '农历·节气')

_CN_WEEKDAYS = ('一', '二', '三', '四', '五', '六', '日')
_EN_WEEKDAYS_SHORT = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
_EN_WEEKDAYS_FULL = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')

# 1900-2100 农历压缩表（每项 20 位：高 4 位闰月，中 12 位大小月，低 4 位闰月大小）
_LUNAR_INFO = (
    0x04BD8, 0x04AE0, 0x0A570, 0x054D5, 0x0D260, 0x0D950, 0x16554, 0x056A0, 0x09AD0, 0x055D2,
    0x04AE0, 0x0A5B6, 0x0A4D0, 0x0D250, 0x1D255, 0x0B540, 0x0D6A0, 0x0ADA2, 0x095B0, 0x14977,
    0x04970, 0x0A4B0, 0x0B4B5, 0x06A50, 0x06D40, 0x1AB54, 0x02B60, 0x09570, 0x052F2, 0x04970,
    0x06566, 0x0D4A0, 0x0EA50, 0x06E95, 0x05AD0, 0x02B60, 0x186E3, 0x092E0, 0x1C8D7, 0x0C950,
    0x0D4A0, 0x1D8A6, 0x0B550, 0x056A0, 0x1A5B4, 0x025D0, 0x092D0, 0x0D2B2, 0x0A950, 0x0B557,
    0x06CA0, 0x0B550, 0x15355, 0x04DA0, 0x0A5B0, 0x14573, 0x052B0, 0x0A9A8, 0x0E950, 0x06AA0,
    0x0AEA6, 0x0AB50, 0x04B60, 0x0AAE4, 0x0A570, 0x05260, 0x0F263, 0x0D950, 0x05B57, 0x056A0,
    0x096D0, 0x04DD5, 0x04AD0, 0x0A4D0, 0x0D4D4, 0x0D250, 0x0D558, 0x0B540, 0x0B6A0, 0x195A6,
    0x095B0, 0x049B0, 0x0A974, 0x0A4B0, 0x0B27A, 0x06A50, 0x06D40, 0x0AF46, 0x0AB60, 0x09570,
    0x04AF5, 0x04970, 0x064B0, 0x074A3, 0x0EA50, 0x06B58, 0x05AC0, 0x0AB60, 0x096D5, 0x092E0,
    0x0C960, 0x0D954, 0x0D4A0, 0x0DA50, 0x07552, 0x056A0, 0x0ABB7, 0x025D0, 0x092D0, 0x0CAB5,
    0x0A950, 0x0B4A0, 0x0BAA4, 0x0AD50, 0x055D9, 0x04BA0, 0x0A5B0, 0x15176, 0x052B0, 0x0A930,
    0x07954, 0x06AA0, 0x0AD50, 0x05B52, 0x04B60, 0x0A6E6, 0x0A4E0, 0x0D260, 0x0EA65, 0x0D530,
    0x05AA0, 0x076A3, 0x096D0, 0x04AFB, 0x04AD0, 0x0A4D0, 0x1D0B6, 0x0D250, 0x0D520, 0x0DD45,
    0x0B5A0, 0x056D0, 0x055B2, 0x049B0, 0x0A577, 0x0A4B0, 0x0AA50, 0x1B255, 0x06D20, 0x0ADA0,
    0x14B63, 0x09370, 0x049F8, 0x04970, 0x064B0, 0x168A6, 0x0EA50, 0x06B20, 0x1A6C4, 0x0AAE0,
    0x092E0, 0x0D2E3, 0x0C960, 0x0D557, 0x0D4A0, 0x0DA50, 0x05D55, 0x056A0, 0x0A6D0, 0x055D4,
    0x052D0, 0x0A9B8, 0x0A950, 0x0B4A0, 0x0B6A6, 0x0AD50, 0x055A0, 0x0ABA4, 0x0A5B0, 0x052B0,
    0x0B273, 0x06930, 0x07337, 0x06AA0, 0x0AD50, 0x14B55, 0x04B60, 0x0A570, 0x054E4, 0x0D160,
    0x0E968, 0x0D520, 0x0DAA0, 0x16AA6, 0x056D0, 0x04AE0, 0x0A9D4, 0x0A2D0, 0x0D150, 0x0F252,
    0x0D520,
)
_LUNAR_MONTHS = ('正', '二', '三', '四', '五', '六', '七', '八', '九', '十', '冬', '腊')
_LUNAR_DAYS_PREFIX = ('初', '十', '廿', '三')
_LUNAR_DIGITS = ('一', '二', '三', '四', '五', '六', '七', '八', '九', '十')

# 二十四节气（从 1 月小寒起）
_SOLAR_TERMS = (
    '小寒', '大寒', '立春', '雨水', '惊蛰', '春分',
    '清明', '谷雨', '立夏', '小满', '芒种', '夏至',
    '小暑', '大暑', '立秋', '处暑', '白露', '秋分',
    '寒露', '霜降', '立冬', '小雪', '大雪', '冬至',
)
_TERM_C_21C = (
    5.4055, 20.12, 3.87, 18.73, 5.63, 20.646,
    4.81, 20.1, 5.52, 21.04, 5.678, 21.37,
    7.108, 22.83, 7.5, 23.13, 7.646, 23.042,
    8.318, 23.438, 7.438, 22.36, 7.18, 21.94,
)

# 12 星座：(起始月, 起始日, 名称, 符号)
_ZODIAC = (
    (1, 20, '水瓶座', '♒'), (2, 19, '双鱼座', '♓'), (3, 21, '白羊座', '♈'),
    (4, 20, '金牛座', '♉'), (5, 21, '双子座', '♊'), (6, 22, '巨蟹座', '♋'),
    (7, 23, '狮子座', '♌'), (8, 23, '处女座', '♍'), (9, 23, '天秤座', '♎'),
    (10, 24, '天蝎座', '♏'), (11, 23, '射手座', '♐'), (12, 22, '摩羯座', '♑'),
)

_TOKEN_RE = re.compile(r'yyyy|yy|MM|M|dd|d|EEEE|EEE|dddd|ddd')


def _lunar_year_days(year):
    info = _LUNAR_INFO[year - 1900]
    days = 348
    mask = 0x8000
    while mask > 0x8:
        if info & mask:
            days += 1
        mask >>= 1
    leap = info & 0xF
    if leap:
        days += 30 if (info & 0x10000) else 29
    return days


def lunar_date_text(day):
    """将公历 date 转为农历月日文本（如“十月十五”、“闰四月初三”）。"""
    if day.year < 1900 or day.year > 2100:
        return ''
    offset = (day - date(1900, 1, 31)).days
    if offset < 0:
        return ''
    year = 1900
    while year <= 2100:
        ydays = _lunar_year_days(year)
        if offset < ydays:
            break
        offset -= ydays
        year += 1
    if year > 2100:
        return ''
    info = _LUNAR_INFO[year - 1900]
    leap = info & 0xF
    is_leap = False
    month = 1
    while month <= 12:
        if leap and month == leap + 1 and not is_leap:
            month -= 1
            is_leap = True
            mdays = 30 if (info & 0x10000) else 29
        else:
            mdays = 30 if (info & (0x10000 >> month)) else 29
        if offset < mdays:
            break
        offset -= mdays
        if is_leap and month == leap:
            is_leap = False
        month += 1
    d = offset + 1
    if d == 10:
        day_str = '初十'
    elif d == 20:
        day_str = '二十'
    elif d == 30:
        day_str = '三十'
    else:
        day_str = _LUNAR_DAYS_PREFIX[(d - 1) // 10] + _LUNAR_DIGITS[(d - 1) % 10]
    prefix = '闰' if is_leap else ''
    return f'{prefix}{_LUNAR_MONTHS[month - 1]}月{day_str}'


def _term_day(year, idx):
    """估算 21 世纪公历年份第 idx 个节气的日期（1..31）。"""
    y = year % 100
    c = _TERM_C_21C[idx]
    leap_count = (y - 1) // 4 if idx < 2 else y // 4
    return int(math.floor(y * 0.2422 + c) - leap_count)


def solar_term_text(day):
    """返回当日或当前所处节气（当日显示“今日·小雪”，否则显示当前节气名）。"""
    if not (2000 <= day.year <= 2099):
        return ''
    terms = []
    for idx, name in enumerate(_SOLAR_TERMS):
        month = idx // 2 + 1
        d = max(1, min(28 if month == 2 else 31, _term_day(day.year, idx)))
        terms.append((date(day.year, month, d), name))
    current = '冬至'
    for tdate, name in terms:
        if day == tdate:
            return f'今日·{name}'
        if day > tdate:
            current = name
    return current


def zodiac_text(day):
    """返回公历日期对应的星座名称与符号。"""
    md = (day.month, day.day)
    name, sym = '摩羯座', '♑'
    for m, d, zname, zsym in _ZODIAC:
        if md >= (m, d):
            name, sym = zname, zsym
    return f'{name} {sym}'


def date_addon_text(day, mode):
    if mode == '农历':
        return lunar_date_text(day)
    if mode == '节气':
        return solar_term_text(day)
    if mode == '星座':
        return zodiac_text(day)
    if mode == '农历·节气':
        parts = [p for p in (lunar_date_text(day), solar_term_text(day)) if p]
        return ' · '.join(parts)
    return ''


def format_clock_text(now=None, *, seconds=False, hour12=False):
    """格式化时间文本，支持 24 小时 / 12 小时制与秒数切换。"""
    dt = now if isinstance(now, datetime) else datetime.now()
    if hour12:
        h = dt.hour % 12 or 12
        meridiem = 'AM' if dt.hour < 12 else 'PM'
        core = f'{h:02d}:{dt.minute:02d}:{dt.second:02d}' if seconds else f'{h:02d}:{dt.minute:02d}'
        return f'{core} {meridiem}'
    return dt.strftime('%H:%M:%S' if seconds else '%H:%M')


def format_date_text(now=None, *, fmt=DEFAULT_DATE_FORMAT, addon='无'):
    """格式化日期文本，支持自定义占位符模板与日期小彩蛋。"""
    dt = now if isinstance(now, datetime) else datetime.now()
    template = fmt if isinstance(fmt, str) and fmt.strip() else DEFAULT_DATE_FORMAT
    wday = dt.weekday()
    mapping = {
        'yyyy': f'{dt.year:04d}',
        'yy': f'{dt.year % 100:02d}',
        'MM': f'{dt.month:02d}',
        'M': str(dt.month),
        'dd': f'{dt.day:02d}',
        'd': str(dt.day),
        'EEEE': f'星期{_CN_WEEKDAYS[wday]}',
        'EEE': f'周{_CN_WEEKDAYS[wday]}',
        'dddd': _EN_WEEKDAYS_FULL[wday],
        'ddd': _EN_WEEKDAYS_SHORT[wday],
    }
    base = _TOKEN_RE.sub(lambda m: mapping[m.group(0)], template)
    extra = date_addon_text(dt.date(), addon)
    return f'{base}  ·  {extra}' if extra else base
