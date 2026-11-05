"""語意端點用：判斷 partial 文字是不是一句講完的指令。

complete＝每個子句都命中 Fast Path 且句尾不是接續詞（可以早點收）；
incomplete＝句尾是接續詞或缺受詞的動詞（多等一下）；unknown＝其他（例如要交給 LLM 的句子）。
只做比對，不執行任何動作。"""
import re
import unicodedata
from functools import lru_cache
from typing import Optional

from mac_ai import config
from mac_ai.router.fast_path import FastPathMatcher
from mac_ai.router.normalize import normalize_utterance
from mac_ai.wake import lexicon_wake_words, split_wake

COMPLETE, INCOMPLETE, UNKNOWN = "complete", "incomplete", "unknown"

# 句尾若是這些，話多半還沒說完（連接詞、介系詞、助動詞）
_TRAILING_CONNECTOR_RE = re.compile(r"(?:然後|接著|再幫我|順便|還有|並且|以及|而且|和|跟|與|並|再|且|把|將|的|到|成|為|在|用|去|幫我|幫|請|要|想|可以)$")
# 只有動詞、沒有受詞
_BARE_VERBS = frozenset({
    "打開", "開啟", "開", "啟動", "執行", "運行", "關掉", "關閉", "關", "退出", "結束", "離開",
    "調", "調整", "設定", "設為", "切換", "切到", "搜尋", "搜索", "查", "查詢", "播放", "放",
    "音量", "聲音", "亮度", "幫我打開", "幫我關掉",
})
_PUNCT_TAIL_RE = re.compile(r"[\s\W_]+$")


@lru_cache(maxsize=1)
def _matcher() -> FastPathMatcher:
    return FastPathMatcher()


def _strip_tail(text: str) -> str:
    return _PUNCT_TAIL_RE.sub("", text)


def classify(text: Optional[str], wake_enabled: bool = config.WAKE_WORD_ENABLED) -> str:
    t = unicodedata.normalize("NFKC", text or "").strip()
    if not t:
        return UNKNOWN
    rest = split_wake(t, lexicon_wake_words())  # 與 WakeGate 一致：停用時句首的喚醒詞同樣會被去掉
    if rest is not None:
        if not _strip_tail(rest):
            return INCOMPLETE if wake_enabled else UNKNOWN  # 啟用時只說了喚醒詞，指令還在後面
        t = rest
    t = normalize_utterance(t)
    matcher = _matcher()
    clauses = matcher.split_clauses(t)
    body = _strip_tail(t)
    if not clauses or not body:
        return UNKNOWN
    last = _strip_tail(clauses[-1])
    # 句尾的連接詞會被 split_clauses 當分隔詞吃掉（「打開小算盤然後」→ 只剩「打開小算盤」），所以看原句句尾
    if _TRAILING_CONNECTOR_RE.search(body) or last in _BARE_VERBS:
        return INCOMPLETE
    if all(matcher.match(c) is not None for c in clauses):
        return COMPLETE
    return UNKNOWN
