"""文本斷句器與快速軌道（Fast Path）。"""
import re
from typing import Any, Dict, List, Optional, Tuple

from mac_ai.router import catalog as catalog_mod
from mac_ai.router import context
from mac_ai.router.actions import ExecContext, run_tool
from mac_ai.router.domain import _REALTIME_RE, SCOPE_WORDS, Intent, parse_find, resolve_intent, starts_with_find_verb
from mac_ai.router.lexicon import (
    APP_KIND, LEXICON, WEATHER_CITY_ALIASES, _FILLERS, _LONG_VERB, _OPEN, _QUIT, _VERB, _alt, _compact, resolve_project,
)
from mac_ai.router.local_actions import plan_actions


_PREFIX_RE = re.compile(rf"^(?:{_alt(_FILLERS.get('prefix', []))})\s*") if _FILLERS.get("prefix") else None
_SUFFIX_RE = re.compile(rf"\s*(?:{_alt(_FILLERS.get('suffix', []))})$") if _FILLERS.get("suffix") else None
_ACTION_WORDS_RE = re.compile(rf"{_VERB}|搜尋|搜索|查|調|切換|鎖|辨識|看|念")
_BA_RE = re.compile(rf"^把\s*(?P<x>.+?)\s*(?P<v>{_VERB})$")
_TOPIC_RE = re.compile(rf"^(?P<x>.+?)\s*(?P<v>{_LONG_VERB})$")

# 安全守門：否定、敘述、提問一律不走 Fast（交給 LLM）
_NEG_RE = re.compile(r"不要|不用|不必|不想|不能|不可以|不行|不需要|別|勿|沒有|先不")
_NARR_RE = re.compile(rf"昨天|前天|剛剛|剛才|之前|已經|以前|上次|曾經|本來|原本|(?:{_VERB})了")
_QUESTION_RE = re.compile(r"嗎|什麼|怎麼|為什麼|為何|如何|是不是|有沒有|哪|幾|多少|\?")

# 點歌句型（詞庫 music；規則見 docs/plans/completed/2026-11-21-music-play.md §4.1）：只比對原句，不碰 IO
_MUSIC = LEXICON["music"]
_M_PREFIX = rf"(?:(?:{_alt(_MUSIC['prefix'])})\s*)*"
_M_QTY = _alt(_MUSIC["quantity"])
_MUSIC_RES = (
    ("listen", re.compile(rf"^{_M_PREFIX}我?\s*(?:{_alt(_MUSIC['listen'])})\s*(?P<x>.*)$")),
    ("play", re.compile(rf"^{_M_PREFIX}(?:{_alt(_MUSIC['play'])})\s*(?P<x>.+)$")),
    ("pick", re.compile(rf"^{_M_PREFIX}(?:{_alt(_MUSIC['put'] + _MUSIC['bring'])})(?:{_M_QTY})\s*(?P<x>.*)$")),
    ("pick", re.compile(rf"^{_M_PREFIX}(?:{_alt(_MUSIC['put'])})\s*(?P<x>{_alt(_MUSIC['generic'])})$")),  # 『放音樂』
    ("tail", re.compile(rf"^(?P<x>.+?)的歌來(?:{_M_QTY})$")),  # 『五月天的歌來一首』
    ("bare", re.compile(r"^聽(?P<x>.+)的歌$")),  # 單獨的『聽 X』不收，一定要『聽 X 的歌』
)
_MUSIC_CONTROL = tuple(_MUSIC["play_control"])
_MUSIC_INNER_RE = re.compile(_alt(_MUSIC["inner_command"]))  # 歌名裡出現另一個指令（『我想聽音樂幫我開 Spotify』）：不收，整句交給分流
_MUSIC_GENERIC = frozenset(_MUSIC["generic"])
_MUSIC_LEAD_RE = re.compile(rf"^(?:{_alt(_MUSIC['lead_clean'])})")
_MUSIC_TAIL_RE = re.compile(rf"(?:{_alt(_MUSIC['tail_clean'])})$")
_MUSIC_PENDING = frozenset(["", *_MUSIC["quantity"]])


def _music_split(text: str) -> Optional[Tuple[str, str]]:
    for kind, pattern in _MUSIC_RES:
        m = pattern.match(text)
        if m:
            return kind, m["x"].strip()
    return None


def music_pending(text: str) -> bool:
    """點歌動詞後面還沒說出歌名（『我想聽』『來一首』『放首』『我想聽一首』）：給完整度判斷用，不等於命中點歌規則"""
    parts = _music_split(text.strip())
    return parts is not None and parts[0] in ("listen", "pick") and parts[1] in _MUSIC_PENDING


# 常用功能說法前後的程度詞（『再往下滑』『多往下滑』『往下滑一點』）：拿掉後整句仍要等於詞庫說法才算
_DEGREE_PREFIX_RE = re.compile(r"^(?:再多|再|多|繼續|稍微)")
_DEGREE_SUFFIX_RE = re.compile(r"(?:多一點|多一些|一點點|一點|一些)$")


def _narrative_exempt(s: str) -> bool:
    """『找昨天下載的 PDF』的『昨天』是檔案的時間條件，不是敘述過去：句首是『找』、沒有『了』、
    敘述詞只有『昨天』才放行（『我昨天找過了』不會）"""
    return starts_with_find_verb(s) and "了" not in s and all(m.group(0) == "昨天" for m in _NARR_RE.finditer(s))


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


# ---- 查資訊：開網頁還是直接回答（計畫 docs/plans/completed/2026-11-21-web-answer.md §4.1）。純比對，不碰網路、LLM、子程序，也不讀前景 ----
# 明說要看畫面（瀏覽器、網站、Google、上網、給我看、顯示、在 X 搜尋、句首的搜尋）：開網頁，優先於一切（明說優先，同 0006）
_OPEN_CUE_RE = re.compile(
    rf"瀏覽器|browser|safari|chrome|網站|網頁|官網|{_alt(SCOPE_WORDS['web'])}|給我看|讓我看|給我們看|秀出|秀給我|顯示|在.{{1,12}}?(?:搜尋|搜索)", re.IGNORECASE)
_LEAD_SEARCH_RE = re.compile(r"^(?:搜尋|搜索)")
# 問句或要求告知：直接回答（「誰」不只收「是誰」：只收「是誰」時「勇士隊最近一場比賽誰贏了」被當成開網頁，2026-11-23 實測）
_ASK_RE = re.compile(r"嗎|什麼|怎麼|怎樣|如何|多少|幾|會不會|誰|是不是|有沒有|哪|\?|？|告訴我|跟我說|和我說|報一下|播報|想知道|知道")
# 有接 API 的高頻類別（目前只有天氣）；說了類別、沒說要看畫面，查／找也是要答案
_CATEGORY_RE = re.compile(r"天氣|下雨|降雨|氣溫|溫度")


def answer_mode(utterance: str) -> str:
    """『open』（開網頁）或『answer』（直接回答）。依序：明說要看畫面→open；問句或要求告知→answer；
    查／找＋高頻類別、只有類別名詞→answer；其餘（查周杰倫、查一下最近的新聞）→open（現況）。守門（否定、敘述）不在這裡，由呼叫端先擋"""
    raw = utterance.strip()
    s = canonicalize_clause(raw)
    if _OPEN_CUE_RE.search(raw) or _LEAD_SEARCH_RE.match(s):
        return "open"
    if _ASK_RE.search(s) or _CATEGORY_RE.search(s):
        return "answer"
    return "open"


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


class Match:
    """Fast Path 比對結果：可呼叫（執行全部動作）。比對與執行分開，路由器才能依說話順序決定何時執行"""

    def __init__(self, actions: List[Tuple[str, dict]], confidence: str = "exact", note: str = "",
                 intent: Optional[Intent] = None):
        self.actions = actions
        self.confidence = confidence  # exact／fuzzy／candidate
        self.note = note
        self.intent = intent  # 範圍判斷的結果（找／搜尋／打開走這條路時才有），給 step["intent"] 追查用

    def run(self, ctx: Optional[ExecContext] = None) -> List[str]:
        ctx = ctx or ExecContext()
        return [run_tool(name, args, ctx) for name, args in self.actions]

    def __call__(self, ctx: Optional[ExecContext] = None) -> str:
        return "；".join(self.run(ctx))


_SEARCH = "(?:" + _alt(["搜尋", "搜索", "查詢", "查一下", "查查", "查"]) + ")(?:一下下|一下|看看)?"  # 動詞後的語氣詞不算關鍵字
_TAB = r"(?:分頁|標籤頁|標籤|頁籤)"
_THIS_TAB = r"(?:這個|目前的?|現在的?|當前的?)?"
# 疑問句尾：『搜尋嗎』這種不是搜尋指令
_QUESTION_END_RE = re.compile(r"[嗎呢?]$")
_WEB_LEAD_RE = re.compile(rf"^(?:{_alt(SCOPE_WORDS['web'])})", re.IGNORECASE)
_DARK = "(?:深色|暗黑|夜間|暗色|黑暗)"
_LIGHT = "(?:淺色|亮色|日間)"
_VOL_SET = r"(?:調到|調成|調整到|調整為|設為|設定為|設成|設定成|開到|改成|改為|到|成|為)?"
_I = re.IGNORECASE


_SNAP = r"(?:螢幕|畫面)?(?:截圖|截屏|截個圖|截張圖|截一張圖|截一下圖|擷取畫面|截取畫面|截(?:一下)?(?:這張|這個|目前的?|現在的?)(?:圖片|圖|畫面|螢幕))"
_LOOK = r"(?:並且|並|然後|再|後)?(?:看看|看|說明|解讀|分析|辨識|辨認|描述|讀|念)?"
_THING = r"(?:這張|這個|這幅|目前的?|現在的?)?(?:圖片|圖|畫面|螢幕|內容)"
_WHAT = r"(?:上)?(?:在)?(?:說|寫|講|有|顯示|是)(?:了)?什麼"


class FastPathMatcher:
    # --- 讀畫面（含『什麼』問句，所以在問句守門之前判斷）---
    _read_screen_re = (
        re.compile(r"^(?:辨識|辨認|識別|解讀|分析|看看|看|讀|念)(?:一下)?(?:目前的?|現在的?|這個?)?(?:(?:頁面|網頁|畫面|頁|螢幕)(?:上)?(?:的)?(?:內容)?|內容)$"),
        re.compile(r"^(?:這個?)?(?:頁面|網頁|畫面|頁)(?:上)?(?:在)?(?:寫|說|講|有|顯示)(?:了)?什麼$"),
        re.compile(r"^(?:螢幕|畫面)上(?:寫|說|講|有|顯示)(?:了)?什麼$"),
        # 『看看畫面上寫什麼』『看一下這個網頁在寫什麼』：動詞＋頁面／畫面＋什麼問句
        re.compile(r"^(?:看看|看一下|看|讀|念|辨識|解讀)(?:一下)?(?:目前的?|現在的?|這個?)?(?:頁面|網頁|畫面|螢幕|頁)(?:上)?(?:在)?(?:寫|說|講|有|顯示)(?:了)?什麼$"),
        # 截圖：『截圖』『截個圖看看』『截圖看這張圖片在說什麼』『截這張圖』
        re.compile(rf"^{_SNAP}{_LOOK}(?:{_THING}(?:{_WHAT})?)?$"),
        # 『看這張圖片』『這張圖在說什麼』
        re.compile(rf"^(?:看看|看|讀|念|辨識|分析|解讀|說明)(?:一下)?(?:這張|這個|這幅)(?:圖片|圖)(?:{_WHAT})?$"),
        re.compile(rf"^(?:這張|這個|這幅)(?:圖片|圖){_WHAT}$"),
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
    _tab_new_re = re.compile(rf"^(?:再)?(?:{_OPEN}|新增|新建|建立|增加)(?:一下|一個|個|一張)?\s*(?:新的?|空白)?{_TAB}$|^新的?{_TAB}$")
    _tab_close_re = re.compile(rf"^(?:{_QUIT})(?:一下)?\s*{_THIS_TAB}{_TAB}$")
    _tab_reload_re = re.compile(rf"^(?:重新整理|重新載入|重新讀取|重整|刷新)(?:一下)?{_THIS_TAB}(?:{_TAB}|頁面|網頁|頁)?$")
    _tab_next_re = re.compile(rf"^(?:(?:切換|跳|切|換)(?:到|去)?)?(?:下一個|下個|下一張|後一個|右邊的?){_TAB}$")
    _tab_prev_re = re.compile(rf"^(?:(?:切換|跳|切|換)(?:到|去)?)?(?:上一個|上個|上一張|前一個|左邊的?){_TAB}$")
    _app_re = re.compile(rf"^(?P<v>{_VERB})(?:一下|個|一個)?\s*(?:那個|這個)?\s*(?P<app>.+)$", _I)
    # 開已知網站：『打開 YouTube』『上 YouTube』『開瀏覽器看 YouTube』『去油管』『開 GitHub 首頁』。整句只能是「動詞（＋瀏覽器）＋網站名稱（＋網站）」，
    # 名稱必須整段等於詞庫 sites 的名稱或別名；多的受詞（『去 YouTube 看影片』）不收，交給 LLM（再由 slow_path.site_open_home 放行）
    _site_re = re.compile(
        rf"^(?:{_OPEN}|上|去|前往|進入|連到|連上)(?:一下|個)?\s*(?:瀏覽器\s*(?:看|上|去|到|開|打開)?\s*)?(?P<site>.+?)\s*(?P<w>的?(?:網站|網頁|官網|首頁))?$", _I)
    # 選擇多筆結果：『第二個』『打開第三個』『在 Finder 顯示第一個』『最後一個』（一定要有序號，不會和『下一個分頁』混淆）
    _pick_re = re.compile(
        rf"^(?:(?P<open>{_OPEN})|(?P<reveal>顯示|在\s*Finder\s*(?:中)?\s*(?:顯示|打開)))?(?:就是)?"
        r"(?:第(?P<n>[一二三四五1-5])(?:個|項|筆)?|(?P<last>最後(?:一)?(?:個|項|筆)))$", _I)
    _PICK_N = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
    # 否定即指令：說的是『不要現在這個狀態』。『不要打開深色模式』『不要鎖定螢幕』是真否定，不在這裡（交給守門）
    _neg_unmute_re = re.compile(r"^(?:不要|別)(?:再)?靜音了$")  # 要有『了』：『不要靜音』可能是真否定（別幫我靜音）
    _neg_bright_re = re.compile(r"^(?:把)?(?:螢幕|畫面)?(?:不要|別)(?:那麼|這麼)(?P<d>亮|暗)(?:了)?$")
    _neg_dark_re = re.compile(rf"^(?:我)?(?:不要|不想要?|別)(?:再)?(?:用)?{_DARK}模式(?:了)?$")

    # 點歌回饋（整句完全相同才收）：換一首／不是這首、我喜歡這首、不要再放這首（含否定詞，在 _r_negated_command 比對）、我最常聽什麼
    _music_replace_re = re.compile(r"^(?:換一首|不是這首)歌?$")
    _music_like_re = re.compile(r"^我喜歡這首歌?$")
    _neg_music_dislike_re = re.compile(r"^(?:不要再放|別再放)這首歌?$")
    _music_top_re = re.compile(r"^我最常聽(?:什麼歌?|誰)$")

    def __init__(self, catalog=None):
        self.catalog = catalog if catalog is not None else catalog_mod.DEFAULT

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
        neg = self._r_negated_command(s)  # 『不要靜音了』這類是要求改變狀態：在否定守門之前比對（只收窄句型）
        if neg is not None:
            return neg
        if not s or self._blocked(s):
            return None
        for pattern in self._read_screen_re:
            if pattern.match(s):
                return Match([("read_screen", {})])
        if self._music_top_re.match(s):  # 『我最常聽什麼』含『什麼』：要在問句守門之前判斷，只收這幾句
            return Match([("music_top", {})])
        m = self._r_weather(raw, s)  # 天氣問句（含『會下雨嗎』『幾度』）：在問句守門與搜尋規則之前；明說要看畫面的句子這條不收，照舊走搜尋
        if m is not None:
            return m
        # 句首是明確的搜尋動詞：關鍵字裡的疑問詞（『搜尋台北天氣怎麼樣』）是搜尋內容，不是在問助理，所以先於問句守門
        for rule in (self._r_open_search, self._r_search):
            m = rule(s, candidates)
            if m is not None:
                return m
        if _QUESTION_RE.search(s):
            return None
        for rule in (
            self._r_front, self._r_music_feedback, lambda _s, _c: self._r_music(raw),  # 點歌看原句：canonicalize 會吃掉『我想』『來』等開頭；常用功能的整句說法（播放、暫停）先比對
            self._r_tab, self._r_project, self._r_volume, self._r_mute,
            self._r_brightness, self._r_dark, self._r_lock, self._r_find, self._r_pick, self._r_site, self._r_app,
            self._r_front_degree,
        ):
            m = rule(s, candidates)
            if m is not None:
                return m
        return None

    def _r_negated_command(self, s: str) -> Optional[Match]:
        if self._neg_unmute_re.match(s):
            return Match([("set_system_volume", {"mute": False})])
        m = self._neg_bright_re.match(s)
        if m:
            return Match([("adjust_screen_brightness", {"direction": "down" if m["d"] == "亮" else "up", "steps": 3})])
        if self._neg_dark_re.match(s):
            return Match([("system_power_action", {"action": "dark_mode_off"})])
        if self._neg_music_dislike_re.match(s):
            return Match([("music_feedback", {"kind": "dislike"})])
        return None

    @staticmethod
    def _blocked(s: str) -> bool:
        return bool(_NEG_RE.search(s)) or (bool(_NARR_RE.search(s)) and not _narrative_exempt(s))

    def match_local(self, clause: str) -> Optional[Match]:
        """給 LLM 導向用：只做『找／打開』的範圍判斷（守門規則與 Fast Path 相同）。
        命中 App、專案、資料夾、檔案才回傳；網頁（維持 LLM 的搜尋）與不確定都回傳 None。
        『搜尋／查』沒帶 App 或本機範圍詞時也不處理（決策：維持網頁搜尋）"""
        s = canonicalize_clause(clause.strip())
        if not s or self._blocked(s) or _QUESTION_RE.search(s):
            return None
        req = parse_find(s)
        if req is None or (req.verb_class == "search" and not req.scope & {"app", "local"}):
            return None
        intent = resolve_intent(req, self.catalog)
        if intent is None or intent.domain == "web":
            return None
        return Match(plan_actions(intent), intent.confidence, intent.note, intent)

    def _intent_match(self, s: str, candidates: bool, verbs: Tuple[str, ...], domains: Tuple[str, ...] = ()) -> Optional[Match]:
        """範圍判斷（第一層）→ 對應表（第二層）。verbs＝只處理這幾類動詞；domains＝只接受這幾種範圍"""
        req = parse_find(s)
        if req is None or req.verb_class not in verbs:
            return None
        intent = resolve_intent(req, self.catalog, candidates)
        if intent is None or (domains and intent.domain not in domains):
            return None
        return Match(plan_actions(intent), intent.confidence, intent.note, intent)

    # ---- 規則：命中回傳 Match，否則 None ----
    @staticmethod
    def _r_weather(raw: str, s: str) -> Optional[Match]:
        """『台北天氣』『明天會下雨嗎』『查一下台北天氣』『今天幾度』→ get_weather(city, day, part)；city 空字串＝用常問的縣市（執行時才查資料庫）。
        只讀資料、不改變狀態，是 0007（提問不走 Fast）的例外；否定、敘述仍由 _blocked 先擋。
        明說要看畫面（answer_mode＝open）、說了兩個不同縣市或時間、後天／週末（超過 36 小時）都不收，照舊走搜尋或交給 Slow Path"""
        if not (_CATEGORY_RE.search(s) or "度" in s):  # 便宜的前置檢查
            return None
        m = _WEATHER_RE.match(re.sub(r"\s+", "", s))
        if m is None:
            return None
        cities = {WEATHER_CITY_ALIASES[c] for c in (m["c1"], m["c2"]) if c}
        whens = {_W_WHEN[w] for w in (m["w1"], m["w2"]) if w}
        if len(cities) > 1 or len(whens) > 1 or (m["weak"] and not (cities or whens)) or answer_mode(raw) != "answer":
            return None
        day, part = next(iter(whens), ("today", ""))
        return Match([("get_weather", {"city": next(iter(cities), ""), "day": day, "part": part})])

    def _r_open_search(self, s: str, candidates: bool) -> Optional[Match]:
        """『開 google 搜尋 X』→ 開瀏覽器 + 搜尋；『開瀏覽器查 X』→ 只搜尋，不另外開 App"""
        m = self._open_search_re.match(s)
        if not m or not m["q"].strip():
            return None
        query = m["q"].strip()
        if _QUESTION_END_RE.search(query):
            return None
        search = ("web_search_or_open_url", {"query": query})
        if _compact(m["app"]) in ("瀏覽器", "browser"):
            return Match([search])
        res = self.catalog.resolve_app(m["app"], candidates)
        if res is None or APP_KIND.get(res[0]) != "browser":
            return None
        name, conf, note = res
        return Match([("control_mac_app", {"app_name": name, "action": "open"}), search], conf, note)

    def _r_search(self, s: str, candidates: bool) -> Optional[Match]:
        for pattern in self._search_res:
            m = pattern.match(s)
            if m:
                query = m["q"].strip()
                if query and not query.startswith("看") and not _QUESTION_END_RE.search(query):  # 『查看…』不是搜尋
                    if pattern is self._search_res[0]:  # 『搜尋 Xcode 這個 App』『查 X 專案』『搜尋 a.pdf』走本機；其餘維持網頁
                        local = self._intent_match(s, candidates, ("search",), ("app", "project", "folder", "file"))
                        if local is not None:
                            return local
                    # 明說上網／google，或查即時資訊（天氣、新聞…）：一律 Google；其餘看前景（停在 FB 就站內搜尋）
                    if pattern is self._search_res[1] or _WEB_LEAD_RE.match(s) or _REALTIME_RE.search(query):
                        return Match([("web_search_or_open_url", {"query": query})])
                    return Match([("context_search", {"query": query})])
        req = parse_find(s)
        if req is not None and req.verb_class == "search" and "local" in req.scope:  # 『在電腦裡搜尋 X』：句首不是搜尋動詞
            return self._intent_match(s, candidates, ("search",), ("app", "project", "folder", "file"))
        return None

    def _r_tab(self, s: str, candidates: bool) -> Optional[Match]:
        for pattern, action in (
            (self._tab_new_re, "new"), (self._tab_close_re, "close"), (self._tab_reload_re, "reload"),
            (self._tab_next_re, "next"), (self._tab_prev_re, "previous"),
        ):
            if pattern.match(s):
                tab = ("browser_tab", {"action": action})
                if action == "new":  # 前景是終端機就開它的新分頁；其餘照舊開瀏覽器分頁
                    return Match([("front_action", {"id": "terminal_new_tab", "fallback": [list(tab)]})])
                return Match([tab])
        return None

    def _r_front(self, s: str, candidates: bool) -> Optional[Match]:
        """常用功能（詞庫 app_shortcuts 的說法，整句完全相同才算）：執行時才看前景 App 決定按鍵。
        原本就有動作的說法（『開終端機』→ 開 Terminal.app）保留為 fallback，前景不適用時照舊"""
        sid = context.SHORTCUT_PHRASES.get(_compact(s))
        if sid is None:
            return None
        args: Dict[str, Any] = {"id": sid}
        direction = context.SHORTCUT_DIRECTIONS.get(_compact(s))
        if direction:
            args["direction"] = direction
        prior = self._r_app(s, candidates)
        if prior is not None:
            args["fallback"] = [list(a) for a in prior.actions]
        return Match([("front_action", args)], prior.confidence if prior else "exact", prior.note if prior else "")

    def _r_music_feedback(self, s: str, candidates: bool) -> Optional[Match]:
        """『換一首』『不是這首』→ music_replace；『我喜歡這首』→ music_feedback(like)。純比對；『不要再放這首』在 _r_negated_command"""
        if self._music_replace_re.match(s):
            return Match([("music_replace", {})])
        if self._music_like_re.match(s):
            return Match([("music_feedback", {"kind": "like"})])
        return None

    @staticmethod
    def _r_music(text: str) -> Optional[Match]:
        """『我想聽 X』『播放 X』『放一首 X』『來點 X』『聽 X 的歌』『X 的歌來一首』→ play_media(query)；query 空字串＝常聽的歌。
        受詞是你／我、疊字『聽聽』、『聽說』、『播放』後接控制詞（播放速度、播放器…）、後面還接另一個指令（『…幫我開 Spotify』）不收。純比對，不讀前景、不做 IO"""
        parts = _music_split(text)
        if parts is None:
            return None
        kind, x = parts
        if _MUSIC_INNER_RE.search(x):
            return None
        if kind in ("listen", "bare") and x.startswith(("你", "我", "聽")):
            return None
        if kind == "listen" and x.startswith("說"):
            return None
        if kind == "play" and x.startswith(_MUSIC_CONTROL):
            return None
        x = _MUSIC_LEAD_RE.sub("", x, count=1).strip()
        while True:
            y = _MUSIC_TAIL_RE.sub("", x).strip()
            if y == x:
                break
            x = y
        if x in _MUSIC_GENERIC:
            x = ""
        if kind == "bare" and not x:
            return None
        return Match([("play_media", {"query": x})])

    def _r_front_degree(self, s: str, candidates: bool) -> Optional[Match]:
        """常用功能的說法前後多了程度詞（『再往下滑』『往下滑一點』）。放在規則最後：只補原本沒命中的句子"""
        t = _DEGREE_SUFFIX_RE.sub("", _DEGREE_PREFIX_RE.sub("", s, count=1), count=1).strip()
        return self._r_front(t, candidates) if t and t != s else None

    def _r_project(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._project_re.match(s)
        if not m:
            return None
        args: Dict[str, Any] = {}
        conf, note = "exact", ""
        if m["ed"]:
            ed = self.catalog.resolve_app(m["ed"], candidates)
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

    def _r_find(self, s: str, candidates: bool) -> Optional[Match]:
        """『找 X』：先判斷範圍（App／專案／資料夾／檔案／網頁），本機優先"""
        return self._intent_match(s, candidates, ("find",))

    def _r_pick(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._pick_re.match(s)
        if not m:
            return None
        n = -1 if m["last"] else self._PICK_N.get(m["n"]) or int(m["n"])
        args: Dict[str, Any] = {"n": n}
        if m["open"]:
            args["action"] = "open"
        elif m["reveal"]:
            args["action"] = "reveal"
        return Match([("pick_choice", args)], intent=Intent("choice", "pick", s, dict(args), "scope_word"))

    def _r_site(self, s: str, candidates: bool) -> Optional[Match]:
        """開詞庫裡的網站首頁。本機優先：同名 App 已存在（『打開 Google』→ Chrome）就讓給 _r_app，除非句子明說『網站／網頁／官網／首頁』。
        純比對：不讀前景、不做 IO（開在哪個瀏覽器由執行階段依對象優先順序決定）"""
        m = self._site_re.match(s)
        if not m:
            return None
        site = context.site_exact(m["site"])
        if site is None or not site.get("home"):
            return None
        if not m["w"] and self.catalog.resolve_app(m["site"]) is not None:
            return None
        intent = Intent("web", "open", site["name"], {"query": site["home"]}, "site")
        return Match([("web_search_or_open_url", {"query": site["home"]})], "exact", f"開網站：{site['name']}", intent)

    def _r_app(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._app_re.match(s)
        if not m:
            return None
        res = self.catalog.resolve_app(m["app"], candidates)
        if res is None:
            if re.fullmatch(_QUIT, m["v"]):
                return None
            # 不是 App：『打開下載資料夾』『打開履歷.pdf』。判斷不出範圍就交給 LLM
            return self._intent_match(s, candidates, ("open",), ("app", "project", "folder", "file"))
        name, conf, note = res
        action = "quit" if re.fullmatch(_QUIT, m["v"]) else "open"
        intent = Intent("app", action, m["app"], {"app": name}, "catalog", conf, note)
        return Match([("control_mac_app", {"app_name": name, "action": action})], conf, note, intent)

    def try_execute(self, clause: str) -> Optional[str]:
        """嘗試以 Fast Path 匹配並立即執行單一子句，若未命中則回傳 None"""
        run = self.match(clause)
        return run() if run else None
