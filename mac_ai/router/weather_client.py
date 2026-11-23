"""天氣查詢（回答用，不開網頁）：有 CWA_API_KEY 先查中央氣象署，沒有金鑰、失敗或逾時就改查 Open-Meteo（免金鑰），
兩個都失敗回 None（呼叫端回查不到）。計畫 docs/plans/completed/2026-11-21-web-answer.md §4.6。

兩個來源的資料先整理成同一種格式（Period：氣象署的 12 小時時段，白天 06–18、晚上 18–06），再套同一個模板，
所以「明天白天」不管哪個來源都是同一段時間。金鑰放在請求標頭（不放網址）；錯誤只留分類（timeout、http_500…），
例外訊息、回傳值與紀錄一律不帶網址、金鑰或回應內容（比照 youtube_client、ADR 0010）。
本模組只做網路與解析：不碰資料庫與子程序；縣市名稱是呼叫端驗證過的標準名稱（詞庫 weather.cities）。"""
import contextlib
import functools
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from mac_ai import config
from mac_ai.router.lexicon import WEATHER_CITY_COORDS

UA = "mac-ai-control/1.0"
SOURCE_CWA, SOURCE_OPEN_METEO = "氣象署", "Open-Meteo"
_MAX_BODY = 2_000_000
_DAY_START, _NIGHT_START = 6, 18  # 時段切法：06–18 白天、18–06 晚上（同氣象署）
_DAY, _NIGHT = "day", "night"

# WMO weather_code → 中文（Open-Meteo 文件列出的全部代碼）；氣象署用語盡量對齊（1 晴時多雲、2 多雲時晴）
WMO_TEXT: Dict[int, str] = {
    0: "晴天", 1: "晴時多雲", 2: "多雲時晴", 3: "陰天", 45: "起霧", 48: "霧凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "凍毛毛雨", 57: "強凍毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "凍雨", 67: "強凍雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒", 80: "小陣雨", 81: "陣雨", 82: "強陣雨", 85: "小陣雪", 86: "強陣雪",
    95: "雷雨", 96: "雷雨夾小冰雹", 99: "雷雨夾大冰雹",
}
_PRECIP_CODES = frozenset(c for c in WMO_TEXT if c >= 51)
_PRECIP_MIN_HOURS = 3  # 一個時段內至少這麼多小時在下雨（或雪），才以降水天氣當作這個時段的天氣


class WeatherError(Exception):
    """查詢失敗。只帶分類 kind（timeout、network、http_500、bad_json、bad_format、no_data），不帶網址或回應內容"""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class Period:
    start: datetime  # 台北當地時間（不帶時區）
    end: datetime
    wx: str
    pop: Optional[int] = None  # 降雨機率（%）
    tmin: Optional[int] = None
    tmax: Optional[int] = None


@dataclass
class Forecast:
    city: str
    source: str
    periods: List[Period] = field(default_factory=list)


@dataclass
class Lookup:
    """text 是 None＝所有來源都查不到。source：回答用的來源；tried：沒成功的來源與原因（例如 ['cwa:timeout']），給紀錄用"""
    text: Optional[str]
    source: str = ""
    tried: List[str] = field(default_factory=list)


def _classify(e: BaseException) -> str:
    if isinstance(e, WeatherError):
        return e.kind
    if isinstance(e, urllib.error.HTTPError):
        return f"http_{e.code}"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(e, urllib.error.URLError):
        return "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "network"
    if isinstance(e, (ValueError, UnicodeError)):  # JSONDecodeError、UnicodeDecodeError 都是 ValueError
        return "bad_json"
    if isinstance(e, OSError) or e.__class__.__module__ == "http.client":
        return "network"
    return "bad_format"


def _strict(parse: Callable[[Any, str], "Forecast"]) -> Callable[[Any, str], "Forecast"]:
    """解析函式只丟 WeatherError：欄位缺漏、型別不對一律是 bad_format（回應長怎樣不外漏）"""
    @functools.wraps(parse)
    def wrapper(data: Any, city: str) -> "Forecast":
        try:
            return parse(data, city)
        except WeatherError:
            raise
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            raise WeatherError("bad_format") from None
    return wrapper


def _get_json(req: urllib.request.Request, timeout: float) -> Any:
    # 呼叫當下才取 urllib.request.urlopen（離線測試會把它換成假的）
    with contextlib.closing(urllib.request.urlopen(req, timeout=timeout)) as resp:
        return json.loads(resp.read(_MAX_BODY))


def _int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _round(x: float) -> int:
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)  # 四捨五入（不用 round()：它是銀行家捨入）


# ---------- 中央氣象署 ----------
@_strict
def parse_cwa(data: Any, city: str) -> Forecast:
    """F-C0032-001（今明 36 小時天氣預報）→ Forecast。欄位缺漏拋 WeatherError('bad_format')；不認得的縣市拋 no_data"""
    if not isinstance(data, dict) or str(data.get("success")).lower() != "true":
        raise WeatherError("bad_format")
    locs = [loc for loc in data["records"]["location"] if loc.get("locationName") == city]
    if not locs:
        raise WeatherError("no_data")
    elements = {e["elementName"]: e["time"] for e in locs[0]["weatherElement"]}
    # 各項目都以 startTime 對齊（同一個時段的 Wx、PoP、MinT、MaxT）
    values = {name: {row["startTime"]: _int(row["parameter"]["parameterName"]) for row in elements.get(name) or []}
              for name in ("PoP", "MinT", "MaxT")}
    fmt = "%Y-%m-%d %H:%M:%S"
    periods: List[Period] = []
    for wx in elements["Wx"]:
        start, end = datetime.strptime(wx["startTime"], fmt), datetime.strptime(wx["endTime"], fmt)
        text = wx["parameter"]["parameterName"]
        if start.hour not in (_DAY_START, _NIGHT_START) or not text:
            continue  # 不是 06／18 開頭的時段（偶爾有）：切法對不上，不用
        t = wx["startTime"]
        periods.append(Period(start, end, text, values["PoP"].get(t), values["MinT"].get(t), values["MaxT"].get(t)))
    if not periods:
        raise WeatherError("bad_format")
    return Forecast(city, SOURCE_CWA, sorted(periods, key=lambda p: p.start))


def fetch_cwa(city: str, key: str, timeout: float) -> Forecast:
    qs = urllib.parse.urlencode({"format": "JSON", "locationName": city})
    req = urllib.request.Request(f"{config.CWA_FORECAST_URL}?{qs}", headers={"Authorization": key, "User-Agent": UA})
    return parse_cwa(_get_json(req, timeout), city)


# ---------- Open-Meteo ----------
def _dominant(codes: List[int]) -> str:
    """一個時段的天氣：下雨（雪）的小時夠多就用降水天氣裡最常見的，否則用最常見的；同數量取較嚴重（代碼大）的"""
    precip = [c for c in codes if c in _PRECIP_CODES]
    pool = precip if len(precip) >= _PRECIP_MIN_HOURS else codes
    code = max(Counter(pool).items(), key=lambda kv: (kv[1], kv[0]))[0]
    return WMO_TEXT.get(code, "天氣不明")


@_strict
def parse_open_meteo(data: Any, city: str) -> Forecast:
    """hourly（台北時間，timezone=Asia/Taipei）→ 氣象署那種 12 小時時段。時段內 12 小時要齊全才算（不足的丟掉，例如最後一天的晚上）"""
    if not isinstance(data, dict) or data.get("error"):
        raise WeatherError("bad_format")
    h = data["hourly"]
    hours: Dict[datetime, tuple] = {}
    for t, temp, pop, code in zip(h["time"], h["temperature_2m"], h["precipitation_probability"], h["weather_code"]):
        if temp is not None and code is not None:
            hours[datetime.strptime(t, "%Y-%m-%dT%H:%M")] = (float(temp), pop, int(code))
    periods: List[Period] = []
    day = min(hours).replace(hour=0, minute=0) if hours else None
    while day is not None and day <= max(hours):
        for begin in (_DAY_START, _NIGHT_START):
            start = day + timedelta(hours=begin)
            end = start + timedelta(hours=12)
            rows = [hours.get(start + timedelta(hours=i)) for i in range(12)]
            if any(r is None for r in rows):
                continue
            temps = [r[0] for r in rows]
            pops = [r[1] for r in rows if r[1] is not None]
            periods.append(Period(start, end, _dominant([r[2] for r in rows]), int(max(pops)) if pops else None,
                                  _round(min(temps)), _round(max(temps))))
        day += timedelta(days=1)
    if not periods:
        raise WeatherError("bad_format")
    return Forecast(city, SOURCE_OPEN_METEO, periods)


def fetch_open_meteo(city: str, timeout: float) -> Forecast:
    lat, lon = WEATHER_CITY_COORDS[city]
    qs = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "hourly": "temperature_2m,precipitation_probability,weather_code",
                                 "timezone": "Asia/Taipei", "forecast_days": 3})
    req = urllib.request.Request(f"{config.OPEN_METEO_URL}?{qs}", headers={"User-Agent": UA})
    return parse_open_meteo(_get_json(req, timeout), city)


# ---------- 選時段與模板 ----------
def select(periods: List[Period], day: str, part: str, now: datetime) -> List[Period]:
    """依說法挑時段，最多 2 個。day：today／tomorrow；part：空字串（不指定）／day（白天）／night（晚上）。
    今天不指定時段：只取還沒結束的（現在是晚上就不回白天；凌晨會包含還沒結束的昨晚到清晨那段）；
    明確說了白天或晚上就照回，即使已經過了"""
    today = now.date()
    want = _DAY_START if part == _DAY else _NIGHT_START if part == _NIGHT else None
    out = []
    for p in periods:
        d = p.start.date()
        if day == "tomorrow":
            ok = d == today + timedelta(days=1)
        elif want is None:
            ok = (d == today or (d == today - timedelta(days=1) and p.start.hour == _NIGHT_START)) and p.end > now
        else:
            ok = d == today
        if ok and (want is None or p.start.hour == want):
            out.append(p)
    return out[:2]


def _label(p: Period, today) -> str:
    offset = (p.start.date() - today).days
    if offset < 0:
        return "今天凌晨"
    prefix = "今" if offset == 0 else "明"
    if p.start.hour == _DAY_START:
        return f"{prefix}天白天"
    return "今晚" if offset == 0 else "明晚"


def _segment(p: Period, today) -> str:
    bits = [p.wx]
    if p.tmin is not None and p.tmax is not None:
        bits.append(f"{p.tmin} 度" if p.tmin == p.tmax else f"{p.tmin}～{p.tmax} 度")
    elif p.tmax is not None or p.tmin is not None:
        bits.append(f"{p.tmax if p.tmax is not None else p.tmin} 度")
    if p.pop is not None:
        bits.append(f"降雨機率 {p.pop}%")
    return f"{_label(p, today)}：" + "，".join(bits)


def render(city: str, periods: List[Period], source: str, now: datetime) -> str:
    """固定模板，不經過 LLM。例：臺北市今天白天：晴時多雲，17～26 度，降雨機率 20%；今晚：多雲短暫陣雨，20～22 度，降雨機率 80%。（氣象署）"""
    return city + "；".join(_segment(p, now.date()) for p in periods) + f"。（{source}）"


# ---------- 查詢（氣象署 → Open-Meteo → 查不到）----------
def lookup(city: str, day: str, part: str, now: datetime, key: str = "",
           timeout: Optional[float] = None, fetch_cwa_fn: Optional[Callable] = None,
           fetch_open_meteo_fn: Optional[Callable] = None) -> Lookup:
    """有金鑰先查氣象署；沒有金鑰、失敗、逾時、或這個說法在它的資料裡找不到時段，改查 Open-Meteo；再失敗就是查不到（text＝None）。
    每個來源各自的逾時是 timeout（預設 config.WEATHER_TIMEOUT_S），最差兩個都逾時約 2 倍。不丟例外。
    now：台北當地時間（不帶時區）。fetch_*_fn 只給測試注入"""
    timeout = config.WEATHER_TIMEOUT_S if timeout is None else timeout
    cwa = fetch_cwa_fn or fetch_cwa
    meteo = fetch_open_meteo_fn or fetch_open_meteo
    tried: List[str] = []
    attempts = ([("cwa", lambda: cwa(city, key, timeout))] if key else []) + [("open_meteo", lambda: meteo(city, timeout))]
    for name, run in attempts:
        kind = None
        try:
            forecast = run()
            periods = select(forecast.periods, day, part, now)
            if periods:
                return Lookup(render(forecast.city, periods, forecast.source, now), forecast.source, tried)
            kind = "no_data"
        except Exception as e:  # noqa: BLE001  只留分類，不讓網址、金鑰或回應進例外
            kind = _classify(e)
        tried.append(f"{name}:{kind}")
    return Lookup(None, "", tried)
