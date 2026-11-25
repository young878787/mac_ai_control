"""天氣冷啟動後的接續（計畫 docs/plans/completed/2026-11-24-weather-city-and-answer-timeout.md §3.2、C1）：
冷啟動回「跟我說一下縣市嘛」時，記下原本要問的 day、part；下一句只說縣市（台北、我住台北…）就設為居住地並回答。
比照 sources.STORE：只放記憶體、每一句開頭 take()（取出並清掉），下一句不是縣市回答就作廢，過了 WEATHER_CITY_FOLLOWUP_TTL_S 也作廢。"""
import threading
import time
from typing import Callable, Optional, Tuple

from mac_ai.config import WEATHER_CITY_FOLLOWUP_TTL_S


class FollowupStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl_s: float = WEATHER_CITY_FOLLOWUP_TTL_S):
        self._clock = clock
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._pending: Optional[Tuple[str, str]] = None
        self._expires_at = 0.0

    def put(self, day: str, part: str) -> None:
        """新的冷啟動直接取代舊的待答"""
        with self._lock:
            self._pending, self._expires_at = (day, part), self._clock() + self._ttl

    def take(self) -> Optional[Tuple[str, str]]:
        """取出並清掉 (day, part)；過期回傳 None"""
        with self._lock:
            pending, expires = self._pending, self._expires_at
            self._pending = None
            return pending if pending is not None and self._clock() < expires else None

    def clear(self) -> None:
        with self._lock:
            self._pending = None


STORE = FollowupStore()
