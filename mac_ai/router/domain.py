"""第一層：範圍判斷（Domain）。把『找／搜尋／打開 X』判成 App、專案、資料夾、檔案或網頁。

純函式、沒有副作用：不執行任何動作，也不 import subprocess／actions／local_actions；
只透過傳入的 catalog 唯讀查詢（resolve_app、resolve_project）。結果是 Intent，
第二層（local_actions）只讀 Intent.slots，不看原句。"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from mac_ai.router.lexicon import LEXICON, OPEN_VERBS, _alt, _compact

FIND_VERBS: List[str] = LEXICON["find_verbs"]
SEARCH_VERBS: List[str] = LEXICON["search_verbs"]
SCOPE_WORDS: Dict[str, List[str]] = LEXICON["scope_words"]
_TIMES: Dict[str, str] = LEXICON["time_words"]
_FOLDERS: List[dict] = LEXICON["known_folders"]
_KINDS: Dict[str, dict] = LEXICON["file_kinds"]
_I = re.IGNORECASE

_VERB_CLASS = {**{v: "open" for v in OPEN_VERBS}, **{v: "search" for v in SEARCH_VERBS}, **{v: "find" for v in FIND_VERBS}}
_LEAD_RE = re.compile(
    rf"^(?:(?P<web>{_alt(SCOPE_WORDS['web'])})|(?:在|於|用)?(?P<local>{_alt(SCOPE_WORDS['local'])}))?(?:的)?\s*"
    rf"(?P<verb>{_alt(list(_VERB_CLASS))})(?:一下下|一下|看看|個)?\s*(?P<target>.+)$", _I)
# 『找到了』『找不到我的鑰匙』『找得到嗎』：在陳述或提問，不是叫助理找東西
_FOUND_RE = re.compile(r"^(?:到|不到|得到|不著|著)")
_BA_FIND_RE = re.compile(r"^把\s*(?:那個|這個|那份|那張)?\s*(?P<target>.+?)\s*(?:找出來|找出|找)$")
_LOCAL_INLINE_RE = re.compile(rf"(?:{_alt(SCOPE_WORDS['local'])})(?:的)?", _I)
_PROJECT_TAIL_RE = re.compile(r"\s*(?:專案|項目|project)$", _I)
_FOLDER_TAIL_RE = re.compile(rf"(?:這個|那個|的)?(?:{_alt(SCOPE_WORDS['folder'])})$")
_APP_TAIL_RE = re.compile(rf"\s*(?:這個|那個|這款|的)?\s*(?:{_alt(SCOPE_WORDS['app'])})$", _I)
_FILE_TAIL_RE = re.compile(rf"(?:這個|那個|的)?(?:{_alt(SCOPE_WORDS['file'])})$")

_TIME_RE = re.compile(_alt(list(_TIMES)))
_REALTIME_RE = re.compile(_alt(LEXICON["realtime_words"]))
_NOISE_RE = re.compile(r"^(?:那個|那份|那張|那些|這個|這份|我的|一個|一份|一張|叫做|叫作|名叫|名為|名字是|名稱是|關於|有關)+")
_EXT_RE = re.compile(r"^(?P<stem>.+)\.(?P<ext>[A-Za-z0-9]{1,8})$")
_FOLDER_NAMES: Dict[str, Tuple[str, bool]] = {
    _compact(n): (f["key"], bool(f.get("folder_only"))) for f in _FOLDERS for n in f["names"]
}
_KIND_WORD_RES: List[Tuple[str, "re.Pattern"]] = [
    (kind, re.compile(_alt(spec["words"]), _I)) for kind, spec in _KINDS.items() if spec["words"]
]
_BARE_SCOPE_WORDS = {_compact(w) for k in ("file", "folder") for w in SCOPE_WORDS[k]}
_ALL_KIND_WORDS = {_compact(w) for spec in _KINDS.values() for w in spec["words"]}
_EXT_KIND: Dict[str, str] = {e: kind for kind, spec in _KINDS.items() for e in spec["ext"]}
_OTHER_EXT = set(LEXICON["other_ext"])


@dataclass(frozen=True)
class FindRequest:
    verb_class: str  # find／search／open
    verb: str
    target: str  # 清掉範圍詞後的受詞
    scope: FrozenSet[str]  # web／local／app／file／folder
    project_target: Optional[str]  # 受詞結尾是『專案』時，去掉它之後的名稱
    raw: str


@dataclass(frozen=True)
class Intent:
    """兩層之間唯一的介面。slots：app{app}、project{project_name}、folder{path_key}、
    file{name,kinds,since,location,filename,local_only}、web{query}。
    local_only＝使用者明確要找本機（副檔名、『檔案』『電腦裡』、位置、只說類型或時間）：0 筆就回『本機沒有』，不轉交模型"""
    domain: str  # app／project／folder／file／web
    verb: str  # find／search／open
    target: str
    slots: Dict[str, Any] = field(default_factory=dict)
    source: str = "default"  # scope_word／catalog／file_hint／realtime／default：判斷的依據
    confidence: str = "exact"
    note: str = ""

    def to_log(self) -> dict:
        return {"domain": self.domain, "verb": self.verb, "slots": self.slots, "source": self.source}


@dataclass(frozen=True)
class FileTerms:
    name: str
    kinds: List[str]
    since: Optional[str]
    location: Optional[str]
    has_ext: bool
    filename: Optional[str]


def starts_with_find_verb(clause: str) -> bool:
    return any(clause.startswith(v) for v in FIND_VERBS)


# ---------- 解析 ----------
def parse_find(clause: str) -> Optional[FindRequest]:
    """『找 X』『搜尋 X』『打開 X』→ FindRequest；不是這類句型或沒有受詞回傳 None。clause 要先過 canonicalize_clause"""
    s = clause.strip()
    scope = set()
    m = _BA_FIND_RE.match(s)
    if m:
        verb, verb_class, target = "找", "find", m["target"]
    else:
        m = _LEAD_RE.match(s)
        if not m:
            return None
        if m["web"]:
            scope.add("web")
        if m["local"]:
            scope.add("local")
        verb, target = m["verb"], m["target"]
        verb_class = _VERB_CLASS[verb]
    target = target.strip()
    if verb_class == "search" and target.startswith("看"):  # 『查看…』不是搜尋
        return None
    if verb_class == "find" and _FOUND_RE.match(target):
        return None
    if _LOCAL_INLINE_RE.search(target):
        scope.add("local")
        target = _LOCAL_INLINE_RE.sub("", target).strip()
    project_target = None
    pm = _PROJECT_TAIL_RE.search(target)
    if pm and target[:pm.start()].strip():
        project_target = target[:pm.start()].strip()
    for tail, label in ((_FOLDER_TAIL_RE, "folder"), (_APP_TAIL_RE, "app"), (_FILE_TAIL_RE, "file")):
        tm = tail.search(target)
        if tm and target[:tm.start()].strip():
            scope.add(label)
            target = target[:tm.start()].strip()
    if not target or _compact(target) in _BARE_SCOPE_WORDS:  # 『找檔案』『找資料夾』：沒說要找什麼
        return None
    return FindRequest(verb_class, verb, target, frozenset(scope), project_target, s)


def _clean_name(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    while True:
        n = re.sub(r"^的+|的+$", "", _NOISE_RE.sub("", t).strip()).strip()
        if n == t:
            return t
        t = n


def known_folder(name: str, explicit: bool) -> Optional[str]:
    """受詞整個就是已知資料夾 → 資料夾代號。folder_only（『文件』）要使用者明說『資料夾』才算"""
    hit = _FOLDER_NAMES.get(_compact(name))
    if hit is None or (hit[1] and not explicit):
        return None
    return hit[0]


def _find_location(t: str) -> Tuple[Optional[str], str]:
    """『下載的』『桌面上的』『在下載資料夾裡』→ (資料夾代號, 去掉這段之後的字串)。
    名稱後面一定要有資料夾／裡／上／的 之類的標記（或在句尾），『下載報告』這種檔名不算位置"""
    for f in _FOLDERS:
        for n in f["names"]:
            m = re.search(rf"(?:在|從)?{re.escape(n)}(?P<res>資料夾)?(?P<mark>裡面|裡|中|內|底下|上)?(?P<de>的)?", t, _I)
            if not m:
                continue
            if f.get("folder_only"):
                ok = bool(m["res"] or m["mark"] in ("裡面", "裡", "中", "內", "底下"))
            else:
                ok = bool(m["res"] or m["mark"] or m["de"] or m.end() == len(t))
            if ok:
                return f["key"], t[:m.start()] + t[m.end():]
    return None, t


def file_terms(target: str) -> FileTerms:
    """從受詞抽出檔案條件：時間、位置、類型（PDF、圖片…）、副檔名，剩下的當檔名"""
    t, since = target, None
    m = _TIME_RE.search(t)
    if m:
        since = _TIMES[m.group(0)]
        t = t[:m.start()] + t[m.end():]
    location, t = _find_location(t)
    name = _clean_name(t)
    em = _EXT_RE.match(name)
    ext = em["ext"].lower() if em else ""
    if ext in _EXT_KIND:  # 『report.pdf』：有副檔名就是字面檔名，不再拆類型詞
        return FileTerms(em["stem"].strip(), [_EXT_KIND[ext]], since, location, True, name)
    if ext in _OTHER_EXT:  # 『驗收腳本.command』：副檔名留在檔名裡一起比對
        return FileTerms(name, [], since, location, True, name)
    kinds: List[str] = []
    for kind, pattern in _KIND_WORD_RES:
        if pattern.search(t):
            kinds.append(kind)
            t = pattern.sub(" ", t)
    return FileTerms(_clean_name(t), kinds, since, location, False, None)


# ---------- 判斷 ----------
def _is_kind_word(t: str) -> bool:
    return _compact(t) in _ALL_KIND_WORDS


def resolve_intent(req: FindRequest, catalog, candidates: bool = False) -> Optional[Intent]:
    """範圍判斷順序（第一個成立的就採用）：網頁範圍詞 → 專案 → 資料夾 → App → 即時資訊 → 檔案 → 預設。
    預設：『找』→ 先搜本機檔案（0 筆再轉交模型）；『搜尋／查』→ 網頁；『打開』→ None（交給 LLM）"""
    v, t, sc = req.verb_class, req.target, req.scope
    if "web" in sc:
        return Intent("web", v, t, {"query": t}, "scope_word")
    if req.project_target:
        hit = catalog.resolve_project(req.project_target, candidates)
        if hit is not None:
            return Intent("project", v, req.project_target, {"project_name": hit[0][0]}, "catalog", hit[1], hit[2])
    folder = known_folder(t, "folder" in sc)
    if folder:
        return Intent("folder", v, t, {"path_key": folder}, "scope_word" if "folder" in sc else "catalog")
    # 『搜尋／查』沒有範圍詞時不判成 App（維持網頁搜尋）；『找照片』不是找 App
    app_allowed = v != "search" or bool(sc & {"app", "local"})
    kind_only = v == "find" and "app" not in sc and _is_kind_word(t)
    if app_allowed and not kind_only and not sc & {"file", "folder"}:
        hit = catalog.resolve_app(t, candidates)
        if hit is not None:
            return Intent("app", v, t, {"app": hit[0]}, "scope_word" if sc & {"app", "local"} else "catalog", hit[1], hit[2])
    ft = file_terms(t)
    if _REALTIME_RE.search(t) and not (ft.has_ext or sc & {"file", "local"}):
        return Intent("web", v, t, {"query": t}, "realtime")
    # 本機證據：副檔名、『檔案／電腦裡／資料夾』、位置；或只說類型、時間（『找照片』『找昨天的』）
    strong = ft.has_ext or bool(sc & {"file", "local", "folder"}) or bool(ft.location)
    weak = bool(ft.kinds or ft.since)
    if v != "search" and weak and ft.name and not strong:
        return None  # 『找最近的電影』『找影片教學』：類型、時間詞只是話題的一部分，交給模型判斷
    if strong or (v != "search" and weak) or v == "find":
        kinds = list(ft.kinds)
        if "folder" in sc and "folder" not in kinds:
            kinds.append("folder")
        if not (ft.name or kinds or ft.since or ft.location):
            return None  # 沒有任何可以搜尋的條件
        local_only = strong or weak
        source = "scope_word" if sc & {"file", "local", "folder"} else ("file_hint" if local_only else "default")
        slots = {"name": ft.name, "kinds": kinds, "since": ft.since, "location": ft.location, "filename": ft.filename,
                 "local_only": local_only}
        return Intent("file", v, t, slots, source)
    if v == "search":
        return Intent("web", v, t, {"query": t}, "default")
    return None
