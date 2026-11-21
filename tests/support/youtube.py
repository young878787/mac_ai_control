"""youtube_client 測試的共用物件：真實 InnerTube fixture 的載入、假 urlopen（依網址分流、記錄請求、可卡住）。
fixture 的來源與縮法見 tests/unit/test_youtube_client.py 的 docstring。"""
import copy
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

import pytest


FIX = Path(__file__).resolve().parent.parent / "fixtures" / "youtube"

_INDEX = json.loads((FIX / "index.json").read_text(encoding="utf-8"))
QUERY = {slug: m["query"] for slug, m in _INDEX.items()}  # slug → 抓 fixture 時的搜尋字


def load(kind: str, slug: str) -> Dict[str, Any]:
    """真實回應（縮小版）的深拷貝，測試可以放心改來產生衍生案例。kind：web／ytm"""
    return copy.deepcopy(json.loads((FIX / f"{kind}_{slug}.json").read_text(encoding="utf-8")))


def home(kind: str) -> str:
    return (FIX / f"home_{kind}.txt").read_text(encoding="utf-8")


# ---------- 衍生案例用的小工具 ----------
def web_items(data: dict) -> list:
    """一般搜尋回應的結果清單（可以直接 insert／改）"""
    return data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"][0]["itemSectionRenderer"]["contents"]


def music_items(data: dict) -> list:
    """YouTube Music 回應的 itemSectionRenderer 清單（每個是 {"itemSectionRenderer": {"contents": [項目]}}）"""
    secs = data["contents"]["tabbedSearchResultsRenderer"]["tabs"][0]["tabRenderer"]["content"]["sectionListRenderer"]["contents"]
    return [s for s in secs if "itemSectionRenderer" in s]


def video(video_id: str, title: str, channel: str = "某頻道", channel_id: str = "UCxxxxxxxxxxxxxxxxxxxxxx") -> dict:
    """一個 videoRenderer 項目（格式與縮小後的真實回應相同）"""
    return {"videoRenderer": {"videoId": video_id, "title": {"runs": [{"text": title}]},
                              "navigationEndpoint": {"watchEndpoint": {"videoId": video_id}},
                              "ownerText": {"runs": [{"text": channel, "navigationEndpoint": {"browseEndpoint": {"browseId": channel_id}}}]}}}


def web_response(videos: List[dict]) -> dict:
    return {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": [
        {"itemSectionRenderer": {"contents": videos}}]}}}}}


# ---------- 假網路 ----------
class Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n is None or n < 0 else self._body[:n]

    def close(self) -> None:
        pass


class Block:
    """urlopen 卡住：等到測試結束（FakeNet.release）才丟 URLError，讓執行緒收尾"""


class FakeNet:
    """取代 urllib.request.urlopen。端點名稱：web／ytm（InnerTube）、api（googleapis，只用來驗證永遠不會被打）、home_web／home_ytm（首頁）。
    設定值可以是：dict／list（轉成 JSON）、bytes／str、Exception（丟出）、Block()、或 callable(req)→以上任一種。
    沒有設定的端點丟 URLError。calls 記錄每次請求：{"name", "url", "timeout", "data", "headers"}"""

    def __init__(self) -> None:
        self.routes: Dict[str, Any] = {}
        self.calls: List[dict] = []
        self._lock = threading.Lock()
        self._release = threading.Event()

    def set(self, name: str, value: Any) -> "FakeNet":
        self.routes[name] = value
        return self

    def release(self) -> None:
        self._release.set()

    @staticmethod
    def name_of(url: str) -> str:
        if "googleapis.com" in url:
            return "api"
        if "/youtubei/v1/search" in url:
            return "ytm" if "music.youtube.com" in url else "web"
        if url.startswith("https://music.youtube.com"):
            return "home_ytm"
        if url.startswith("https://www.youtube.com"):
            return "home_web"
        return "other"

    def count(self, name: str) -> int:
        return sum(1 for c in self.calls if c["name"] == name)

    def urls(self) -> List[str]:
        return [c["url"] for c in self.calls]

    def __call__(self, req, timeout=None, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        name = self.name_of(url)
        with self._lock:
            self.calls.append({"name": name, "url": url, "timeout": timeout, "data": getattr(req, "data", None),
                               "headers": {k.lower(): v for k, v in getattr(req, "headers", {}).items()}})
        value = self.routes.get(name, urllib.error.URLError("no route"))
        if callable(value) and not isinstance(value, (Exception, Block)):
            value = value(req)
        if isinstance(value, Block):
            self._release.wait(30)
            raise urllib.error.URLError("released")
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            value = value.encode("utf-8")
        return Resp(value)


@pytest.fixture
def net(monkeypatch):
    """假 urlopen（FakeNet）。測試結束時放開卡住的執行緒"""
    fake = FakeNet()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    yield fake
    fake.release()


def set_real(net: FakeNet, slug: str) -> None:
    """兩邊都回某個 slug 的真實 fixture（沒有 web 的 slug 只設 ytm）"""
    net.set("ytm", load("ytm", slug))
    if (FIX / f"web_{slug}.json").exists():
        net.set("web", load("web", slug))
