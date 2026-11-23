"""長尾問題的網頁搜尋（Brave Search API）：query → 最多幾筆摘錄。計畫 docs/plans/active/2026-11-21-web-answer.md §4.3。

金鑰（BRAVE_API_KEY，經 config.env_value）只在這裡讀、只放 X-Subscription-Token 標頭（不放網址）；錯誤只留分類
（timeout、network、http_429、bad_json、bad_format、no_results），例外訊息與回傳值不帶網址、金鑰或回應內容（比照 weather_client、ADR 0010）。
搜尋服務只收到 query 關鍵字（與依原句決定的時間範圍 freshness），不收原句與上下文。本模組只做網路與解析：不碰資料庫與子程序。
Brave 條款（Search API Terms of Use，2026-09-01 版）：搜尋結果只能暫時留在記憶體（不快取、不建資料庫）、不能拿去訓練模型，
所以摘錄只活在這次呼叫裡：不寫紀錄、不進對話記憶、不存檔。"""
import contextlib
import html
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, List, Optional

from mac_ai import config

UA = "mac-ai-control/1.0"
_MAX_BODY = 2_000_000
_MAX_QUERY_CHARS = 200  # Brave 的 q 上限是 400 字元、50 個詞；語音問句遠短於此
_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"\s+")


class SearchError(Exception):
    """搜尋失敗。只帶分類 kind，不帶網址或回應內容"""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# 原句有時間性時，請 Brave 只回近期的網頁（freshness）。純比對原句，不由模型決定（同 answer_mode）。
# 「現在、目前」不算：「台灣現在的總統是誰」的權威頁面常沒有日期，過濾會把它濾掉
_FRESH_YEAR_RE = re.compile(r"今年|本年度")
_FRESH_WEEK_RE = re.compile(r"最近|最新|剛剛|剛才|昨天|昨晚|昨日|今天|今日|今晚|這週|這周|本週|本周|這禮拜|這星期")


@dataclass(frozen=True)
class Result:
    title: str
    url: str  # 只會是 http／https
    snippet: str
    date: str = ""  # 網頁日期 YYYY-MM-DD（Brave 的 page_age）；沒有或格式不對就是空字串


def key() -> str:
    return config.env_value("BRAVE_API_KEY").strip()


def available() -> bool:
    return bool(key())


def _classify(e: BaseException) -> str:
    if isinstance(e, SearchError):
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


def freshness_for(utterance: str) -> Optional[str]:
    """原句 → Brave 的 freshness：「今年」→ py（一年內），「最近、最新、今天、昨天、這週…」→ pw（一週內），其餘 None（不過濾）。
    兩種都有時取範圍大的 py（「今年最新的…」不該只看一週）"""
    if _FRESH_YEAR_RE.search(utterance):
        return "py"
    if _FRESH_WEEK_RE.search(utterance):
        return "pw"
    return None


def _date(value: Any) -> str:
    m = _DATE_RE.match(value) if isinstance(value, str) else None
    return m.group(0) if m else ""


def _text(value: Any) -> str:
    """去掉 HTML 標籤與實體、收斂空白（Brave 的 description 可能帶 <strong>）"""
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub("", value))).strip() if isinstance(value, str) else ""


def parse(data: Any, limit: int = config.WEB_SEARCH_COUNT) -> List[Result]:
    """Brave 回應 → 摘錄。只收 http／https 的網址；沒有標題與摘要的筆數丟掉。沒有任何可用的結果是 no_results，格式不對是 bad_format"""
    if not isinstance(data, dict):
        raise SearchError("bad_format")
    web = data.get("web")
    if web is None:  # 沒有網頁結果時，Brave 回應裡沒有 web 欄位
        raise SearchError("no_results")
    items = web.get("results") if isinstance(web, dict) else None
    if not isinstance(items, list):
        raise SearchError("bad_format")
    out: List[Result] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        title, snippet = _text(item.get("title")), _text(item.get("description"))[:config.WEB_SNIPPET_CHARS]
        if isinstance(url, str) and url.startswith(("http://", "https://")) and (title or snippet):
            out.append(Result(title, url, snippet, _date(item.get("page_age"))))
        if len(out) >= limit:
            break
    if not out:
        raise SearchError("no_results")
    return out


def search(query: str, timeout: Optional[float] = None, freshness: Optional[str] = None) -> List[Result]:
    """query → 摘錄（最多 WEB_SEARCH_COUNT 筆）；任何失敗只拋 SearchError（含沒有金鑰：no_key）。freshness：freshness_for 的結果，None 不過濾"""
    k = key()
    q = _SPACE_RE.sub(" ", query).strip()[:_MAX_QUERY_CHARS]
    if not k or not q:
        raise SearchError("no_key" if not k else "bad_format")
    params = {"q": q, "count": config.WEB_SEARCH_COUNT, "country": config.BRAVE_COUNTRY, "search_lang": config.BRAVE_SEARCH_LANG,
              "text_decorations": "false"}
    if freshness:
        params["freshness"] = freshness
    req = urllib.request.Request(f"{config.BRAVE_SEARCH_URL}?{urllib.parse.urlencode(params)}",
                                 headers={"X-Subscription-Token": k, "Accept": "application/json", "User-Agent": UA})
    try:
        # 呼叫當下才取 urllib.request.urlopen（離線測試會把它換成假的）
        with contextlib.closing(urllib.request.urlopen(req, timeout=config.WEB_SEARCH_TIMEOUT_S if timeout is None else timeout)) as resp:
            data = json.loads(resp.read(_MAX_BODY))
        return parse(data)
    except Exception as e:  # noqa: BLE001 — 任何失敗都只留分類
        raise SearchError(_classify(e)) from None
