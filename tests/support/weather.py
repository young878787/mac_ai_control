"""天氣測試的共用物件：真實回應的 fixture（2026-11-23 抓的臺北市）、假 urlopen（依網址分流、記錄請求）、固定的現在時間。
fixture 來源：氣象署 F-C0032-001（locationName＝臺北市）、Open-Meteo（hourly 三項，timezone＝Asia/Taipei，3 天），都沒有金鑰在內。
時間固定在台北 2026-11-23 10:00（fixture 的第一個氣象署時段 06:00 開始）。"""
import copy
import datetime
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

import pytest

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "weather"
NOW = datetime.datetime(2026, 11, 23, 10, 0)  # 台北當地時間
NOW_EPOCH = NOW.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp()
KEY = "CWA-TEST-0123456789-ABCDEF"  # 假的授權碼：測試用來確認它只出現在標頭、不會出現在網址與任何輸出

# 固定的預期文字（以 NOW 為現在；Open-Meteo 的數字另外在 test_weather_client.py 用原始資料獨立驗算過）
CWA_TODAY = "臺北市今天白天：晴時多雲，17～26 度，降雨機率 20%；今晚：多雲短暫陣雨，20～22 度，降雨機率 80%。（氣象署）"
CWA_TOMORROW = "臺北市明天白天：陰短暫陣雨，18～20 度，降雨機率 90%。（氣象署）"
METEO_TODAY = "臺北市今天白天：晴時多雲，18～27 度，降雨機率 0%；今晚：晴時多雲，18～22 度，降雨機率 41%。（Open-Meteo）"
METEO_TOMORROW = "臺北市明天白天：小毛毛雨，17～19 度，降雨機率 86%；明晚：小毛毛雨，16～17 度，降雨機率 78%。（Open-Meteo）"


def load(name: str) -> Dict[str, Any]:
    """fixture 的深拷貝（cwa_taipei／open_meteo_taipei），測試可以放心改來產生衍生案例"""
    return copy.deepcopy(json.loads((FIX / f"{name}.json").read_text(encoding="utf-8")))


class _Resp:
    def __init__(self, body: bytes):
        self._buf = io.BytesIO(body)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def close(self) -> None:
        self._buf.close()


class FakeNet:
    """取代 urllib.request.urlopen。端點：cwa（opendata.cwa.gov.tw）、meteo（api.open-meteo.com）。
    設定值可以是：dict（轉成 JSON）、bytes、Exception（丟出）、int（HTTP 錯誤碼）、callable(req)→以上任一種。沒有設定的端點丟 URLError。
    calls 記錄每次請求：{"name", "url", "timeout", "headers"}（headers 的鍵一律小寫）"""

    def __init__(self) -> None:
        self.routes: Dict[str, Any] = {}
        self.calls: List[dict] = []

    def set(self, name: str, value: Any) -> "FakeNet":
        self.routes[name] = value
        return self

    def names(self) -> List[str]:
        return [c["name"] for c in self.calls]

    @staticmethod
    def name_of(url: str) -> str:
        if "opendata.cwa.gov.tw" in url:
            return "cwa"
        if "api.open-meteo.com" in url:
            return "meteo"
        return "other"

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        name = self.name_of(url)
        self.calls.append({"name": name, "url": url, "timeout": timeout,
                           "headers": {k.lower(): v for k, v in (req.headers.items() if hasattr(req, "headers") else [])}})
        value = self.routes.get(name)
        if callable(value):
            value = value(req)
        if value is None:
            raise urllib.error.URLError(f"no route for {name}")
        if isinstance(value, Exception):
            raise value
        if isinstance(value, int):
            raise urllib.error.HTTPError(url, value, "error", {}, io.BytesIO(b""))
        return _Resp(value if isinstance(value, bytes) else json.dumps(value).encode("utf-8"))


@pytest.fixture
def net(monkeypatch):
    """假 urlopen：兩個來源預設都回真實 fixture，測試再 net.set(...) 改。"""
    fake = FakeNet().set("cwa", load("cwa_taipei")).set("meteo", load("open_meteo_taipei"))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake
