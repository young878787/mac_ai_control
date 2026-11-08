"""多筆搜尋結果的選擇清單：跨語句保存（ExecContext 刻意不跨語句，所以放在這裡）。
語音『第二個』與桌面角色的點選都從這裡取回完整路徑；清單過期或被新搜尋取代就失效。"""
import itertools
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from mac_ai.config import FIND_CHOICE_TTL_S


@dataclass(frozen=True)
class ChoiceList:
    list_id: int
    query: str
    default_action: str  # reveal／open
    paths: List[str]
    total: int
    expires_at: float


def short_dir(path: str) -> str:
    """給 UI 顯示的資料夾：家目錄縮成 ~，完整路徑不離開本程序"""
    home = os.path.expanduser("~")
    d = os.path.dirname(path)
    return "~" + d[len(home):] if d == home or d.startswith(home + os.sep) else d


class ChoiceStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl_s: float = FIND_CHOICE_TTL_S):
        self._clock = clock
        self._ttl = ttl_s
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._current: Optional[ChoiceList] = None

    def put(self, query: str, default_action: str, paths: List[str], total: int) -> ChoiceList:
        """新的搜尋結果直接取代舊清單"""
        with self._lock:
            self._current = ChoiceList(next(self._ids), query, default_action, list(paths), total, self._clock() + self._ttl)
            return self._current

    def get(self) -> Optional[ChoiceList]:
        with self._lock:
            if self._current is not None and self._clock() >= self._current.expires_at:
                self._current = None
            return self._current

    def clear(self) -> None:
        with self._lock:
            self._current = None

    def payload(self, lst: ChoiceList) -> dict:
        """result 事件的 choices 欄位（不含完整路徑）"""
        return {
            "list_id": lst.list_id, "query": lst.query, "total": lst.total, "default_action": lst.default_action,
            "expires_in_s": max(0.0, lst.expires_at - self._clock()),
            "items": [{"n": i, "name": os.path.basename(p), "dir": short_dir(p),
                       "kind": "folder" if os.path.isdir(p) else (os.path.splitext(p)[1].lstrip(".").lower() or "file")}
                      for i, p in enumerate(lst.paths, 1)],
        }


STORE = ChoiceStore()
