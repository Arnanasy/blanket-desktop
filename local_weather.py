"""当地天气查询（所有阻塞网络与定位调用均在后台线程执行）。"""
import json
import math
import re
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CACHE_SECONDS = 900          # 天气缓存有效期（15 分钟）
_TEMP_NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format_temp_range(t1, t2):
    """将接口返回的两个温度按低温在前、高温在后排列（兼容负温与单位后缀）。"""
    s1, s2 = str(t1 or '').strip(), str(t2 or '').strip()
    m1, m2 = _TEMP_NUM_RE.search(s1), _TEMP_NUM_RE.search(s2)
    if m1 and m2 and float(m1.group()) > float(m2.group()):
        s1, s2 = s2, s1
    return f'{s1}–{s2}'


def _today_cn_dates():
    """返回当日中文日期的带/不带前导零两种格式（兼容中国天气网接口）。"""
    tm = time.localtime()
    return (
        f'{tm.tm_year}年{tm.tm_mon}月{tm.tm_mday}日',
        f'{tm.tm_year}年{tm.tm_mon:02d}月{tm.tm_mday:02d}日',
    )

LOCATION_SCRIPT = """
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher
try {
    [void]$w.TryStart($true, [TimeSpan]::FromSeconds(4))
    $p = $w.Position
    if (!$p.Location.IsUnknown -and $p.Timestamp.UtcDateTime -gt [DateTime]::UtcNow.AddMinutes(-10)) {
        @($p.Location.Latitude,$p.Location.Longitude) | ConvertTo-Json -Compress
    }
} finally { $w.Dispose() }
"""


def read_json(url):
    req = Request(url, headers={'User-Agent': 'Blanket-Weather/1.0'})
    with urlopen(req, timeout=8) as response:
        raw = response.read(200000)
        return json.loads(raw.decode('utf-8-sig'))


def coords(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Invalid coordinates')
    return round(lat, 2), round(lon, 2)


def location():
    try:
        run = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', LOCATION_SCRIPT],
            capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return (*coords(*json.loads(run.stdout)), '系统定位')
    except (ValueError, TypeError, OSError, subprocess.TimeoutExpired):
        result = read_json('https://ipwho.is/')
        if not result.get('success'):
            raise ValueError('Location unavailable')
        return (*coords(result['latitude'], result['longitude']), result.get('city') or '网络定位')


def conditions(code):
    table = [
        ((0,), '晴'), ((1, 2), '少云'), ((3,), '阴'), ((45, 48), '雾'),
        ((51, 53, 55), '毛毛雨'), ((56, 57, 66, 67), '冻雨'), ((61, 63, 65), '雨'),
        ((71, 73, 75, 77), '雪'), ((80, 81, 82), '阵雨'), ((85, 86), '阵雪'),
        ((95, 96, 99), '雷雨'),
    ]
    for codes, label in table:
        if code in codes:
            return label
    return '天气'


def domestic_text(url):
    request = Request(url, headers={'Referer': 'https://www.weather.com.cn/', 'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=6) as response:
        return response.read(200000).decode('utf-8-sig')


def domestic_weather(previous):
    """通过中国天气网 IP 接口获取国内实况与当日温度范围。"""
    geo = domestic_text('https://wgeo.weather.com.cn/ip/')
    station = re.search(r'\bid\s*=\s*"(101\d{6})"', geo)
    address = re.search(r'\baddr\s*=\s*"([^"]+)"', geo)
    if not station or not address or any(k in address[1] for k in ('国外', '未知', '海外')):
        raise ValueError('No domestic station')
    code = station[1]
    now = time.time()
    prev_updated = previous.get('updated', 0)
    if previous.get('station') == code and _is_number(prev_updated) \
            and 0 <= now - prev_updated < CACHE_SECONDS:
        return previous
    raw = domestic_text(f'https://d1.weather.com.cn/sk_2d/{code}.html?_={int(now)}')
    data = json.loads(raw.split('=', 1)[1].strip().rstrip(';'))
    city = data['cityname'].strip()
    temp = float(data['temp'])
    if not city or str(data['city']) != code or not -90 <= temp <= 65:
        raise ValueError('Invalid station observation')
    line = f"{data['weather']}  {temp:.0f}°"
    try:
        forecast = json.loads(domestic_text(f'https://www.weather.com.cn/data/cityinfo/{code}.html'))['weatherinfo']
        # 该接口偶尔返回历史归档预报，仅当日匹配时才拼接高低温
        if forecast.get('date_y') in _today_cn_dates():
            line += f" · {_format_temp_range(forecast['temp1'], forecast['temp2'])}"
    except (OSError, ValueError, KeyError, IndexError):
        pass
    return dict(place=city, line=line, station=code, source='中国天气网', updated=now)


def global_weather(previous):
    """海外或国内接口不可用时的回退方案：系统/IP 定位 + Open-Meteo。"""
    lat, lon, place = location()
    now = time.time()
    prev_updated = previous.get('updated', 0)
    if previous and previous.get('point') and _is_number(prev_updated) \
            and 0 <= now - prev_updated < CACHE_SECONDS:
        a, b = previous['point']
        if math.hypot(lat - a, (lon - b) * math.cos(math.radians(lat))) < 0.07:
            return previous if place == '系统定位' else dict(previous, place=place)
    if place == '系统定位':
        try:
            city = read_json('https://api.bigdatacloud.net/data/reverse-geocode-client?' + urlencode(
                dict(latitude=lat, longitude=lon, localityLanguage='zh')))
            place = city.get('city') or city.get('locality') or place
        except (OSError, ValueError):
            pass
    params = urlencode(dict(
        latitude=lat, longitude=lon,
        current='temperature_2m,weather_code',
        daily='temperature_2m_min,temperature_2m_max',
        forecast_days=1, timezone='auto',
    ))
    data = read_json('https://api.open-meteo.com/v1/forecast?' + params)
    current, daily = data['current'], data['daily']
    line = (f"{conditions(current['weather_code'])}  {current['temperature_2m']:.0f}°  ·  "
            f"{daily['temperature_2m_min'][0]:.0f}°–{daily['temperature_2m_max'][0]:.0f}°")
    return dict(line=line, place=place, point=[lat, lon], source='Open-Meteo', updated=now)


def refresh(previous):
    previous = previous if isinstance(previous, dict) else {}
    updated = previous.get('updated', 0)
    if previous.get('line') and _is_number(updated) and 0 <= time.time() - updated < CACHE_SECONDS:
        return previous
    # 优先尝试国内接口（单次直连，无需启动 PowerShell 定位），非国内 IP 或失败再回退全球源
    try:
        return domestic_weather(previous)
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return global_weather(previous)
