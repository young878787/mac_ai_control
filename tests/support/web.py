"""長尾回答（搜尋 → 雲端摘要）測試的共用物件：假 Brave（取代 urllib.request.urlopen）、固定的樣本回應。
樣本 tests/fixtures/web/brave_sample.json 依官方文件的欄位手寫（web.results[].title／url／description，description 帶 <strong> 與實體），
不是從真實回應存下來的；Brave 條款不准把搜尋結果存成資料庫，所以不放真實回應進版控。"""
import copy
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

import pytest

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "web"
KEY = "BSA-TEST-9d41c0ffee"  # 假的 Brave 金鑰：只能出現在 X-Subscription-Token 標頭，不得出現在網址與任何輸出、雲端請求


def load(name: str = "brave_sample") -> Dict[str, Any]:
    return copy.deepcopy(json.loads((FIX / f"{name}.json").read_text(encoding="utf-8")))


class _Resp:
    def __init__(self, body: bytes):
        self._buf = io.BytesIO(body)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def close(self) -> None:
        self._buf.close()


class FakeBrave:
    """取代 urllib.request.urlopen。value 可以是 dict（JSON）、bytes、Exception（丟出）、int（HTTP 錯誤碼）、callable(req)→以上任一種。
    calls 記錄每次請求：{"url", "query"（q 參數）, "params", "timeout", "headers"（鍵小寫）}"""

    def __init__(self, value: Any = None):
        self.value = load() if value is None else value
        self.calls: List[dict] = []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.calls.append({"url": url, "query": params.get("q"), "params": params, "timeout": timeout,
                           "headers": {k.lower(): v for k, v in (req.headers.items() if hasattr(req, "headers") else [])}})
        value = self.value(req) if callable(self.value) else self.value
        if isinstance(value, Exception):
            raise value
        if isinstance(value, int):
            raise urllib.error.HTTPError(url, value, "error", {}, io.BytesIO(b""))
        return _Resp(value if isinstance(value, bytes) else json.dumps(value).encode("utf-8"))


@pytest.fixture
def brave(monkeypatch):
    """假 Brave：回樣本；測試改 brave.value。金鑰用 BRAVE_API_KEY 環境變數（env_value 先看環境變數）"""
    monkeypatch.setenv("BRAVE_API_KEY", KEY)
    fake = FakeBrave()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake
