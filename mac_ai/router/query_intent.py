"""查詢分類（計畫 docs/plans/active/2026-11-23-query-routing.md §4.1）：一句話要找什麼（target）、怎麼給（mode）。
純函式：不碰網路、LLM、子程序、資料庫，也不讀前景；catalog 只做唯讀查詢（與 domain.resolve_intent 相同）。
分類結果由 fast_path._route_query 依對應表換成動作。P2 只搬家：分類與既有規則逐句相同（行為快照 0 差異）"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from mac_ai.router.domain import _REALTIME_RE, SCOPE_WORDS, Intent, parse_find, resolve_intent
from mac_ai.router.lexicon import APP_KIND, QUERY_CUES, WEATHER_CITY_ALIASES, _OPEN, _alt, _compact

_I = re.IGNORECASE
_LOCAL_DOMAINS = ("app", "project", "folder", "file")

# ---- 查資訊：開網頁還是直接回答（計畫 docs/plans/completed/2026-11-21-web-answer.md §4.1）。純比對，不碰網路、LLM、子程序，也不讀前景 ----
# 明說要看畫面（瀏覽器、網站、Google、上網、給我看、顯示、在 X 搜尋、句首的搜尋）：開網頁，優先於一切（明說優先，同 0006）
_OPEN_CUE_RE = re.compile(
    rf"{_alt(QUERY_CUES['open_cue'] + SCOPE_WORDS['web'])}|在.{{1,12}}?(?:{_alt(QUERY_CUES['lead_search'])})", re.IGNORECASE)
_LEAD_SEARCH_RE = re.compile(rf"^(?:{_alt(QUERY_CUES['lead_search'])})")
# 問句或要求告知：直接回答（「誰」不只收「是誰」：只收「是誰」時「勇士隊最近一場比賽誰贏了」被當成開網頁，2026-11-23 實測）
_ASK_RE = re.compile(_alt(QUERY_CUES["ask"]))
# 有接 API 的高頻類別（目前只有天氣）；說了類別、沒說要看畫面，查／找也是要答案
_CATEGORY_RE = re.compile(_alt(QUERY_CUES["answer_category"]))


# 天氣窄規則：整句要完全符合「（查／告訴我…）（縣市）（時間）（縣市）（的）主題（時間）（語尾）」才收，其餘（天氣之子是什麼電影、今天天氣好熱、打開天氣）一律不收
_W_WHEN = {
    "今天": ("today", ""), "今日": ("today", ""), "現在": ("today", ""), "目前": ("today", ""),
    "今晚": ("today", "night"), "今夜": ("today", "night"), "今天晚上": ("today", "night"), "今日晚上": ("today", "night"),
    "今早": ("today", "day"), "今天早上": ("today", "day"), "今天白天": ("today", "day"), "今天上午": ("today", "day"), "今天下午": ("today", "day"),
    "明天": ("tomorrow", ""), "明日": ("tomorrow", ""), "隔天": ("tomorrow", ""),
    "明晚": ("tomorrow", "night"), "明天晚上": ("tomorrow", "night"),
    "明早": ("tomorrow", "day"), "明天早上": ("tomorrow", "day"), "明天白天": ("tomorrow", "day"), "明天上午": ("tomorrow", "day"), "明天下午": ("tomorrow", "day"),
}
_W_CITY = rf"(?:{_alt(list(WEATHER_CITY_ALIASES))})"
_W_WHEN_RE = rf"(?:{_alt(list(_W_WHEN))})"
_W_LEAD = r"(?:(?:查詢|查查|查|找找|找|看看|看|問|告訴我|跟我說|和我說|報一下|報|播報|知道|說|講)(?:一下下|一下|看看)?)?"
# 主題：strong 單獨就算天氣（天氣、會下雨嗎、氣溫）；weak（幾度）一定要同時說了縣市或時間，不然太容易誤收
_W_STRONG = r"(?P<strong>天氣|會不會(?:下雨|降雨)|(?:會|有)?(?:下雨|降雨)嗎|(?:下雨|降雨)機率|氣溫|溫度)"  # 下雨一定要帶疑問（嗎、會不會、機率）：『明天會下雨』可能是敘述，不收
_W_WEAK = r"(?P<weak>幾度|多少度)"
_W_TAIL = r"(?:預報|預測|狀況|情況|資訊|如何|怎麼樣|怎樣|嗎|多少|多高|是多少|有多高|幾度)?"
_WEATHER_RE = re.compile(
    rf"^{_W_LEAD}(?P<c1>{_W_CITY})?(?P<w1>{_W_WHEN_RE})?(?P<c2>{_W_CITY})?(?:的)?(?:{_W_STRONG}|{_W_WEAK})(?P<w2>{_W_WHEN_RE})?{_W_TAIL}$")


def answer_mode_of(raw: str, s: str) -> str:
    """『open』（開網頁）或『answer』（直接回答）。raw＝原句（去頭尾空白），s＝canonicalize_clause(raw)。
    依序：明說要看畫面→open；問句或要求告知→answer；查／找＋高頻類別、只有類別名詞→answer；其餘（查周杰倫、查一下最近的新聞）→open（現況）。
    守門（否定、敘述）不在這裡，由呼叫端先擋"""
    if _OPEN_CUE_RE.search(raw) or _LEAD_SEARCH_RE.match(s):
        return "open"
    if _ASK_RE.search(s) or _CATEGORY_RE.search(s):
        return "answer"
    return "open"


_SEARCH = "(?:" + _alt(["搜尋", "搜索", "查詢", "查一下", "查查", "查"]) + ")(?:一下下|一下|看看)?"  # 動詞後的語氣詞不算關鍵字
# 疑問句尾：『搜尋嗎』這種不是搜尋指令
_QUESTION_END_RE = re.compile(rf"(?:{_alt(QUERY_CUES['question_end'])})$")
_WEB_LEAD_RE = re.compile(rf"^(?:{_alt(SCOPE_WORDS['web'])})", re.IGNORECASE)
_OPEN_SEARCH_RE = re.compile(rf"^(?P<v>{_OPEN})(?:一下|個)?\s*(?P<app>.+?)\s*(?:並且|並|然後|再|且)?\s*(?:{_SEARCH})\s*(?P<q>.+)$", _I)
_SEARCH_RES = (
    re.compile(rf"^(?:上網|網路上|網上|線上)?(?:{_SEARCH})\s*(?P<q>.+)$", _I),
    re.compile(rf"^(?:用\s*)?(?:google|谷歌)\s*(?:一下|{_SEARCH})\s*(?P<q>.+)$", _I),
)


@dataclass(frozen=True)
class QueryIntent:
    """target：knowledge（世界知識）／local（本機的 App、專案、資料夾、檔案）；mode：show（開給使用者看）／answer（直接回答）。
    evidence：判斷依據（category／explicit_web／realtime／verb，或範圍判斷的 source）。category：weather。
    slots：天氣的 city／day／part；show 的 where（google＝直接開 Google、context＝依前景網站決定）與 app（要先開的瀏覽器）。
    intent：範圍判斷（domain.resolve_intent）的結果；有它就照 local_actions 的對應表執行"""
    target: str
    mode: str
    evidence: str
    query: str = ""
    category: str = ""
    slots: Dict[str, Any] = field(default_factory=dict)
    intent: Optional[Intent] = None
    confidence: str = "exact"
    note: str = ""


def _scope(s: str, catalog, candidates: bool, verbs: Tuple[str, ...], domains: Tuple[str, ...] = ()) -> Optional[QueryIntent]:
    """範圍判斷（domain.resolve_intent）。verbs＝只處理這幾類動詞；domains＝只接受這幾種範圍"""
    req = parse_find(s)
    if req is None or req.verb_class not in verbs:
        return None
    intent = resolve_intent(req, catalog, candidates)
    if intent is None or (domains and intent.domain not in domains):
        return None
    return QueryIntent("knowledge" if intent.domain == "web" else "local", "show", intent.source, intent=intent)


def _weather(raw: str, s: str) -> Optional[QueryIntent]:
    """『台北天氣』『明天會下雨嗎』『查一下台北天氣』『今天幾度』→ 天氣（city 空字串＝用常問的縣市，執行時才查資料庫）。
    只讀資料、不改變狀態，是 0007（提問不走 Fast）的例外；否定、敘述仍由 Fast Path 的守門先擋。
    明說要看畫面（answer_mode＝open）、說了兩個不同縣市或時間、後天／週末（超過 36 小時）都不收，照舊走搜尋或交給 Slow Path"""
    if not (_CATEGORY_RE.search(s) or "度" in s):  # 便宜的前置檢查
        return None
    m = _WEATHER_RE.match(re.sub(r"\s+", "", s))
    if m is None:
        return None
    cities = {WEATHER_CITY_ALIASES[c] for c in (m["c1"], m["c2"]) if c}
    whens = {_W_WHEN[w] for w in (m["w1"], m["w2"]) if w}
    if len(cities) > 1 or len(whens) > 1 or (m["weak"] and not (cities or whens)) or answer_mode_of(raw, s) != "answer":
        return None
    day, part = next(iter(whens), ("today", ""))
    return QueryIntent("knowledge", "answer", "category", category="weather",
                       slots={"city": next(iter(cities), ""), "day": day, "part": part})


def _open_search(s: str, catalog, candidates: bool) -> Optional[QueryIntent]:
    """『開 google 搜尋 X』→ 開瀏覽器 + 搜尋；『開瀏覽器查 X』→ 只搜尋，不另外開 App"""
    m = _OPEN_SEARCH_RE.match(s)
    if not m or not m["q"].strip():
        return None
    query = m["q"].strip()
    if _QUESTION_END_RE.search(query):
        return None
    if _compact(m["app"]) in ("瀏覽器", "browser"):
        return QueryIntent("knowledge", "show", "explicit_web", query, slots={"where": "google"})
    res = catalog.resolve_app(m["app"], candidates)
    if res is None or APP_KIND.get(res[0]) != "browser":
        return None
    name, conf, note = res
    return QueryIntent("knowledge", "show", "explicit_web", query, slots={"where": "google", "app": name}, confidence=conf, note=note)


def _search(s: str, catalog, candidates: bool) -> Optional[QueryIntent]:
    for i, pattern in enumerate(_SEARCH_RES):
        m = pattern.match(s)
        if m:
            query = m["q"].strip()
            if query and not query.startswith("看") and not _QUESTION_END_RE.search(query):  # 『查看…』不是搜尋
                if i == 0:  # 『搜尋 Xcode 這個 App』『查 X 專案』『搜尋 a.pdf』走本機；其餘維持網頁
                    local = _scope(s, catalog, candidates, ("search",), _LOCAL_DOMAINS)
                    if local is not None:
                        return local
                # 明說上網／google，或查即時資訊（天氣、新聞…）：一律 Google；其餘看前景（停在 FB 就站內搜尋）
                if i == 1 or _WEB_LEAD_RE.match(s):
                    return QueryIntent("knowledge", "show", "explicit_web", query, slots={"where": "google"})
                if _REALTIME_RE.search(query):
                    return QueryIntent("knowledge", "show", "realtime", query, slots={"where": "google"})
                return QueryIntent("knowledge", "show", "verb", query, slots={"where": "context"})
    req = parse_find(s)
    if req is not None and req.verb_class == "search" and "local" in req.scope:  # 『在電腦裡搜尋 X』：句首不是搜尋動詞
        return _scope(s, catalog, candidates, ("search",), _LOCAL_DOMAINS)
    return None


def classify_query(s: str, catalog, *, raw: str = "", candidates: bool = False, stage: str = "lead") -> Optional[QueryIntent]:
    """s＝canonicalize_clause 之後的子句，raw＝原句（天氣要看原句有沒有明說要看畫面；預設同 s）。判斷不出是查詢回傳 None。
    stage＝lead：句首就看得出是查詢的說法（天氣、開瀏覽器搜尋、搜尋／查），在 Fast Path 的問句守門之前判斷（關鍵字裡的疑問詞是搜尋內容）；
    stage＝find：『找 X』，排在其他指令規則之後（沒有更具體的指令才當成找東西），本機優先"""
    if stage == "find":
        return _scope(s, catalog, candidates, ("find",))
    return _weather(raw or s, s) or _open_search(s, catalog, candidates) or _search(s, catalog, candidates)
