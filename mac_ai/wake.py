"""喚醒詞閘門：啟用時句首必須是喚醒詞才執行，只說喚醒詞則短暫待命、下一句免喚醒詞；停用時每一句都當指令。"""
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from mac_ai.config import WAKE_WINDOW_S, WAKE_WORD_ENABLED
from mac_ai.router.lexicon import LEXICON


def _skippable(ch: str) -> bool:
    """空白與標點符號不參與喚醒詞比對（STT 可能在詞中或詞後加逗號、空白）"""
    return ch.isspace() or unicodedata.category(ch)[0] in "PS"


def lexicon_wake_words() -> List[str]:
    """詞庫的標準喚醒詞加上誤聽變體（含簡體寫法）"""
    wake = LEXICON.get("wake_words", {})
    return [*wake.get("names", []), *wake.get("misheard", [])]


def split_wake(text: str, words: Sequence[str]) -> Optional[str]:
    """句首是喚醒詞 → 回傳去掉喚醒詞與其後標點、空白的剩餘文字（可能為空字串）；否則回傳 None。
    只做句首的完全比對（忽略大小寫、全半形、標點與空白），不做模糊比對"""
    t = unicodedata.normalize("NFKC", text)
    chars = [(ch, i) for i, ch in enumerate(t) if not _skippable(ch)]
    compact = "".join(ch for ch, _ in chars).lower()
    for word in sorted(words, key=len, reverse=True):
        key = "".join(ch for ch in unicodedata.normalize("NFKC", word) if not _skippable(ch)).lower()
        if key and compact.startswith(key):
            rest = t[chars[len(key) - 1][1] + 1:]
            skip = next((i for i, ch in enumerate(rest) if not _skippable(ch)), len(rest))
            return rest[skip:]
    return None


@dataclass
class WakeResult:
    status: str  # command＝要執行 command；armed＝只聽到喚醒詞，進入待命；ignored＝未喚醒，不執行
    command: str = ""


class WakeGate:
    def __init__(self, words: Optional[Sequence[str]] = None, window_s: float = WAKE_WINDOW_S,
                 clock: Callable[[], float] = time.monotonic, enabled: bool = WAKE_WORD_ENABLED):
        self.enabled = enabled
        self.words = list(words) if words is not None else lexicon_wake_words()
        if enabled and not self.words:
            raise ValueError("沒有設定喚醒詞（vocab/lexicon.json 的 wake_words）")
        self.window_s = window_s
        self._clock = clock
        self._armed_at: Optional[float] = None

    def feed(self, text: str) -> WakeResult:
        """處理 STT 辨識出的一句話。待命只管一次：下一個指令送出後即結束待命"""
        rest = split_wake(text, self.words)
        if not self.enabled:  # 常啟動：整句就是指令；句首若仍帶喚醒詞就去掉
            command = text if rest is None else rest
            if all(_skippable(ch) for ch in unicodedata.normalize("NFKC", command)):
                return WakeResult("ignored")
            return WakeResult("command", command.strip())
        if rest is not None:
            if rest:
                self._armed_at = None
                return WakeResult("command", rest)
            self._armed_at = self._clock()
            return WakeResult("armed")
        if all(_skippable(ch) for ch in unicodedata.normalize("NFKC", text)):  # 空白或只有標點
            return WakeResult("ignored")
        if self._armed_at is not None and self._clock() - self._armed_at <= self.window_s:
            self._armed_at = None
            return WakeResult("command", text.strip())
        return WakeResult("ignored")
