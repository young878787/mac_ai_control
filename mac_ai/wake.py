"""喚醒詞閘門：啟用時句首必須是喚醒詞才執行；喚醒後進入會話，會話內每一句免喚醒詞、每句重新計時；停用時每一句都當指令。"""
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from mac_ai.config import WAKE_SESSION_S, WAKE_WORD_ENABLED
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
    status: str  # command＝要執行 command；armed＝只聽到喚醒詞，開啟會話；ignored＝未喚醒，不執行
    command: str = ""


class WakeGate:
    def __init__(self, words: Optional[Sequence[str]] = None, session_s: float = WAKE_SESSION_S,
                 clock: Callable[[], float] = time.monotonic, enabled: bool = WAKE_WORD_ENABLED):
        self.enabled = enabled
        self.words = list(words) if words is not None else lexicon_wake_words()
        if enabled and not self.words:
            raise ValueError("沒有設定喚醒詞（vocab/lexicon.json 的 wake_words）")
        self.session_s = session_s
        self._clock = clock
        self._session_at: Optional[float] = None  # 會話最後一次開始或延長的時間；None＝不在會話中

    def start_session(self) -> None:
        """開始（或重新計時）會話；UI 點擊角色時使用"""
        self._session_at = self._clock()

    def end_session(self) -> None:
        self._session_at = None

    def session_remaining(self) -> float:
        """會話剩餘秒數；不在會話中或已逾時為 0"""
        if self._session_at is None:
            return 0.0
        return max(0.0, self.session_s - (self._clock() - self._session_at))

    def _in_session(self) -> bool:
        return self._session_at is not None and self._clock() - self._session_at <= self.session_s

    def feed(self, text: str) -> WakeResult:
        """處理 STT 辨識出的一句話。會話內每個有內容的句子都重新計時；空白或只有標點的句子不算"""
        rest = split_wake(text, self.words)
        if not self.enabled:  # 常啟動：整句就是指令；句首若仍帶喚醒詞就去掉
            command = text if rest is None else rest
            if all(_skippable(ch) for ch in unicodedata.normalize("NFKC", command)):
                return WakeResult("ignored")
            return WakeResult("command", command.strip())
        if rest is not None:
            self.start_session()
            return WakeResult("command", rest) if rest else WakeResult("armed")
        if all(_skippable(ch) for ch in unicodedata.normalize("NFKC", text)):  # 空白或只有標點
            return WakeResult("ignored")
        if self._in_session():
            self.start_session()
            return WakeResult("command", text.strip())
        return WakeResult("ignored")
