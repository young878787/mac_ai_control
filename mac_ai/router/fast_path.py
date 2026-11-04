"""文本斷句器與快速軌道（Fast Path）。"""
import re
from typing import Any, Dict, List, Optional, Tuple

from mac_ai.router.actions import ExecContext, run_tool
from mac_ai.router.lexicon import (
    APP_KIND, _FILLERS, _LONG_VERB, _OPEN, _QUIT, _VERB, _alt, _compact, resolve_app, resolve_project,
)


_PREFIX_RE = re.compile(rf"^(?:{_alt(_FILLERS.get('prefix', []))})\s*") if _FILLERS.get("prefix") else None
_SUFFIX_RE = re.compile(rf"\s*(?:{_alt(_FILLERS.get('suffix', []))})$") if _FILLERS.get("suffix") else None
_ACTION_WORDS_RE = re.compile(rf"{_VERB}|搜尋|搜索|查|調|切換|鎖|辨識|看|念")
_BA_RE = re.compile(rf"^把\s*(?P<x>.+?)\s*(?P<v>{_VERB})$")
_TOPIC_RE = re.compile(rf"^(?P<x>.+?)\s*(?P<v>{_LONG_VERB})$")

# 安全守門：否定、敘述、提問一律不走 Fast（交給 LLM）
_NEG_RE = re.compile(r"不要|不用|不必|不想|不能|不可以|不行|不需要|別|勿|沒有|先不")
_NARR_RE = re.compile(rf"昨天|前天|剛剛|剛才|之前|已經|以前|上次|曾經|本來|原本|(?:{_VERB})了")
_QUESTION_RE = re.compile(r"嗎|什麼|怎麼|為什麼|為何|如何|是不是|有沒有|哪|幾|多少|\?")


def canonicalize_clause(clause: str) -> str:
    """只給 Fast Path 用：去疊用的前綴填充詞與語尾助詞，並把『把 X 打開』『X 打開』改寫成『打開 X』。
    送給 LLM 的仍是原句（填充詞要留給 LLM 判斷語氣）"""
    s = clause.strip()
    while True:
        t = s
        if _PREFIX_RE:
            t = _PREFIX_RE.sub("", t, count=1).strip()
        if _SUFFIX_RE:
            t = _SUFFIX_RE.sub("", t, count=1).strip()
        if t == s:
            break
        s = t
    for pattern in (_BA_RE, _TOPIC_RE):
        m = pattern.match(s)
        if m and not _ACTION_WORDS_RE.search(m["x"]):
            return f"{m['v']}{m['x']}"
    return s


class Match:
    """Fast Path 比對結果：可呼叫（執行全部動作）。比對與執行分開，路由器才能依說話順序決定何時執行"""

    def __init__(self, actions: List[Tuple[str, dict]], confidence: str = "exact", note: str = ""):
        self.actions = actions
        self.confidence = confidence  # exact／fuzzy／candidate
        self.note = note

    def run(self, ctx: Optional[ExecContext] = None) -> List[str]:
        ctx = ctx or ExecContext()
        return [run_tool(name, args, ctx) for name, args in self.actions]

    def __call__(self, ctx: Optional[ExecContext] = None) -> str:
        return "；".join(self.run(ctx))


_SEARCH = _alt(["搜尋", "搜索", "查詢", "查一下", "查查", "查"])
_DARK = "(?:深色|暗黑|夜間|暗色|黑暗)"
_LIGHT = "(?:淺色|亮色|日間)"
_VOL_SET = r"(?:調到|調成|調整到|調整為|設為|設定為|設成|設定成|開到|改成|改為|到|成|為)?"
_I = re.IGNORECASE


class FastPathMatcher:
    # --- 讀畫面（含『什麼』問句，所以在問句守門之前判斷）---
    _read_screen_re = (
        re.compile(r"^(?:辨識|辨認|識別|解讀|分析|看看|看|讀|念)(?:一下)?(?:目前的?|現在的?|這個?)?(?:(?:頁面|網頁|畫面|頁|螢幕)(?:上)?(?:的)?(?:內容)?|內容)$"),
        re.compile(r"^(?:這個?)?(?:頁面|網頁|畫面|頁)(?:上)?(?:在)?(?:寫|說|講|有|顯示)(?:了)?什麼$"),
        re.compile(r"^(?:螢幕|畫面)上有什麼$"),
    )
    # --- 其餘規則 ---
    _open_search_re = re.compile(rf"^(?P<v>{_OPEN})(?:一下|個)?\s*(?P<app>.+?)\s*(?:並且|並|然後|再|且)?\s*(?:{_SEARCH})\s*(?P<q>.+)$", _I)
    _search_res = (
        re.compile(rf"^(?:上網|網路上|網上|線上)?(?:{_SEARCH})\s*(?P<q>.+)$", _I),
        re.compile(rf"^(?:用\s*)?(?:google|谷歌)\s*(?:一下|{_SEARCH})\s*(?P<q>.+)$", _I),
    )
    _project_re = re.compile(rf"^(?:(?:用|使用|在)\s*(?P<ed>.+?)\s*)?(?:{_OPEN})(?:一下|個)?\s*(?P<proj>.+?)\s*(?:的)?(?:專案|項目|project)$", _I)
    _vol_level_re = re.compile(rf"^(?:把)?(?:系統)?(?:音量|聲音)\s*{_VOL_SET}\s*(?P<n>\d{{1,3}})\s*(?:%|趴|度)?$")
    _vol_word_re = re.compile(rf"^(?:把)?(?:系統)?(?:音量|聲音)\s*{_VOL_SET}\s*(?P<w>最大|最小|最高|最低|一半)$")
    _mute_re = re.compile(r"^(?:靜音|(?:關掉|關閉|關)(?:系統)?(?:聲音|音量))$")
    _unmute_re = re.compile(r"^(?:取消靜音|解除靜音|(?:打開|開啟|開)(?:系統)?(?:聲音|音量))$")
    _bright_re = re.compile(
        r"^(?:把)?(?P<subj>(?:螢幕|屏幕|畫面)?(?:的)?(?:亮度)?)\s*(?:稍微|再)?(?P<verb>調整|調|弄|開|變|設定|設)?(?:得|到|成)?(?P<most>最)?(?P<d>暗|亮|低|高)\s*(?P<rest>.*)$"
    )
    _dark_on_re = re.compile(rf"^(?:打開|開啟|開|啟用|切換到|切換成|切到|切成|換成|改成|轉成)(?:一下)?{_DARK}模式$")
    _dark_off_re = re.compile(rf"^(?:(?:關掉|關閉|關|停用|取消|退出)(?:一下)?{_DARK}模式|(?:切換到|切換成|切到|切成|換成|改成|轉成)(?:一下)?{_LIGHT}模式|(?:打開|開啟|開|啟用){_LIGHT}模式)$")
    _dark_toggle_re = re.compile(rf"^切換{_DARK}模式$")
    _lock_res = (re.compile(r"^(?:鎖定|鎖)(?:螢幕|屏幕|畫面|電腦|屏)$"), re.compile(r"^(?:把)?(?:螢幕|電腦)(?:鎖定|鎖起來|鎖住)$"))
    _app_re = re.compile(rf"^(?P<v>{_VERB})(?:一下|個|一個)?\s*(?:那個|這個)?\s*(?P<app>.+)$", _I)

    def split_clauses(self, text: str) -> List[str]:
        """將 STT 輸出的長句依據標點符號與中文口語連接詞切分為獨立子句（不以空白切分，以免拆壞「VS Code」「調到 50」）"""
        pattern = r"[，。、；,;！!？?]+|(?:然後|接著|再幫我|順便|還有|並且|以及)"
        return [c.strip() for c in re.split(pattern, text) if c.strip()]

    def match(self, clause: str, candidates: bool = False) -> Optional[Match]:
        """以 Fast Path 比對單一子句；命中回傳「尚未執行」的 Match，未命中回傳 None。
        candidates=True 時，歧義（兩個 App 相似度太接近）的第 1 名也會回傳（信心 candidate），
        供 LLM 不可用時補救；被安全守門擋下的句子永遠回傳 None。"""
        raw = clause.strip()
        if raw == "讓電腦睡眠":  # 睡眠誤觸成本高：只接受這個固定句，不做放寬
            return Match([("system_power_action", {"action": "sleep"})])
        s = canonicalize_clause(raw)
        if not s or _NEG_RE.search(s) or _NARR_RE.search(s):
            return None
        for pattern in self._read_screen_re:
            if pattern.match(s):
                return Match([("read_screen", {})])
        if _QUESTION_RE.search(s):
            return None
        for rule in (
            self._r_open_search, self._r_search, self._r_project, self._r_volume, self._r_mute,
            self._r_brightness, self._r_dark, self._r_lock, self._r_app,
        ):
            m = rule(s, candidates)
            if m is not None:
                return m
        return None

    # ---- 規則：命中回傳 Match，否則 None ----
    def _r_open_search(self, s: str, candidates: bool) -> Optional[Match]:
        """『開 google 搜尋 X』→ 開瀏覽器 + 搜尋；『開瀏覽器查 X』→ 只搜尋，不另外開 App"""
        m = self._open_search_re.match(s)
        if not m or not m["q"].strip():
            return None
        query = m["q"].strip()
        search = ("web_search_or_open_url", {"query": query})
        if _compact(m["app"]) in ("瀏覽器", "browser"):
            return Match([search])
        res = resolve_app(m["app"], candidates)
        if res is None or APP_KIND.get(res[0]) != "browser":
            return None
        name, conf, note = res
        return Match([("control_mac_app", {"app_name": name, "action": "open"}), search], conf, note)

    def _r_search(self, s: str, candidates: bool) -> Optional[Match]:
        for pattern in self._search_res:
            m = pattern.match(s)
            if m:
                query = m["q"].strip()
                if query and not query.startswith("看"):  # 『查看…』不是搜尋
                    return Match([("web_search_or_open_url", {"query": query})])
        return None

    def _r_project(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._project_re.match(s)
        if not m:
            return None
        args: Dict[str, Any] = {}
        conf, note = "exact", ""
        if m["ed"]:
            ed = resolve_app(m["ed"], candidates)
            if ed is None or APP_KIND.get(ed[0]) != "editor":
                return None
            args["editor"], conf, note = ed
        hit = resolve_project(m["proj"], candidates)
        if hit is not None:
            args["project_name"] = hit[0][0]
            if hit[1] != "exact":
                conf, note = hit[1], hit[2]
        else:  # 不在白名單：照樣比對成功，由 open_project 回報「找不到專案」，不交給 LLM 亂猜
            args["project_name"] = m["proj"].strip()
        return Match([("open_project", args)], conf, note)

    def _r_volume(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._vol_level_re.match(s)
        if m:
            return Match([("set_system_volume", {"level": int(m["n"])})])
        m = self._vol_word_re.match(s)
        if m:
            level = {"最大": 100, "最高": 100, "最小": 0, "最低": 0, "一半": 50}[m["w"]]
            return Match([("set_system_volume", {"level": level})])
        return None

    def _r_mute(self, s: str, candidates: bool) -> Optional[Match]:
        if self._mute_re.match(s):
            return Match([("set_system_volume", {"mute": True})])
        if self._unmute_re.match(s):
            return Match([("set_system_volume", {"mute": False})])
        return None

    def _r_brightness(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._bright_re.match(s)
        if not m:
            return None
        has_subject = bool(m["subj"])
        if m["d"] in ("低", "高") and not has_subject:
            return None  # 『調高』可能是音量，必須指明螢幕或亮度
        if not has_subject and not (m["most"] and m["verb"]):
            return None
        direction = "down" if m["d"] in ("暗", "低") else "up"
        rest = m["rest"].strip()
        if m["most"]:
            return Match([("adjust_screen_brightness", {"direction": direction, "steps": 16})]) if not rest else None
        if rest in ("", "一點", "一點點", "一些", "些", "稍微"):
            steps = 3
        else:
            n = re.fullmatch(r"(\d+)\s*格", rest)
            if not n:
                return None
            steps = int(n[1])
        return Match([("adjust_screen_brightness", {"direction": direction, "steps": steps})])

    def _r_dark(self, s: str, candidates: bool) -> Optional[Match]:
        if self._dark_on_re.match(s):
            return Match([("system_power_action", {"action": "dark_mode_on"})])
        if self._dark_off_re.match(s):
            return Match([("system_power_action", {"action": "dark_mode_off"})])
        if self._dark_toggle_re.match(s):
            return Match([("system_power_action", {"action": "toggle_dark_mode"})])
        return None

    def _r_lock(self, s: str, candidates: bool) -> Optional[Match]:
        if any(p.match(s) for p in self._lock_res):
            return Match([("system_power_action", {"action": "lock_screen"})])
        return None

    def _r_app(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._app_re.match(s)
        if not m:
            return None
        res = resolve_app(m["app"], candidates)
        if res is None:
            return None
        name, conf, note = res
        action = "quit" if re.fullmatch(_QUIT, m["v"]) else "open"
        return Match([("control_mac_app", {"app_name": name, "action": action})], conf, note)

    def try_execute(self, clause: str) -> Optional[str]:
        """嘗試以 Fast Path 匹配並立即執行單一子句，若未命中則回傳 None"""
        run = self.match(clause)
        return run() if run else None
