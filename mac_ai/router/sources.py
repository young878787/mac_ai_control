"""長尾回答後的「要打開來源嗎？」：待決的來源網址，跨語句保存（ExecContext 刻意不跨語句，所以放在這裡，比照 choices.STORE）。
網址只放記憶體：不進對話記憶（ADR 0009 第 6 條）、不送 LLM、不寫紀錄。
每一句開頭 take()（取出並清掉）：下一句不是肯定的回答就作廢，過了 SOURCE_FOLLOWUP_TTL_S 也作廢；所以只有剛回答完的那一句才會把「好」當成開來源，
平常說「好」的行為不變。"""
import re
import threading
import time
from typing import Callable, Optional

from mac_ai.config import SOURCE_FOLLOWUP_TTL_S

FOLLOWUP = "要打開來源嗎？"
# 整句只有肯定的回答才算（「好，順便調音量」這種多子句不算）
_YES_RE = re.compile(r"^(?:好|好的|好啊|好喔|好呀|要|要啊|要的|可以|可以啊|(?:幫我)?(?:要)?(?:打開|開啟|開)(?:一下)?(?:來源|那個來源|來源網頁)?)(?:啦|喔|吧|呀)?$")


def is_yes(clause: str) -> bool:
    return bool(_YES_RE.match(clause.strip()))


class SourceStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl_s: float = SOURCE_FOLLOWUP_TTL_S):
        self._clock = clock
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._url: Optional[str] = None
        self._expires_at = 0.0

    def put(self, url: str) -> None:
        """新的回答直接取代舊的待決來源"""
        with self._lock:
            self._url, self._expires_at = url, self._clock() + self._ttl

    def take(self) -> Optional[str]:
        """取出並清掉；過期回傳 None"""
        with self._lock:
            url, expires = self._url, self._expires_at
            self._url = None
            return url if url is not None and self._clock() < expires else None

    def clear(self) -> None:
        with self._lock:
            self._url = None


STORE = SourceStore()
