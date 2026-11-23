"""多筆搜尋結果的選擇清單：跨語句保存（ExecContext 刻意不跨語句，所以放在這裡）。
語音『第二個』與桌面角色的點選都從這裡取回完整路徑；清單過期或被新搜尋取代就失效。
聯合搜尋的清單另有網路項目（WebItem）：標題與網址只活在記憶體的這份清單裡，不寫紀錄、資料庫（Brave 條款）"""
import itertools
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Union
from urllib.parse import urlsplit

from mac_ai.config import FIND_CHOICE_TTL_S


@dataclass(frozen=True)
class WebItem:
    title: str
    url: str  # 只會是 http／https


Item = Union[str, WebItem]  # 本機項目是完整路徑


@dataclass(frozen=True)
class ChoiceList:
    list_id: int
    query: str
    default_action: str  # reveal／open（本機項目）
    items: List[Item]
    total: int
    expires_at: float
    term: str = ""  # 聯合搜尋的查詢字：選了之後記統計（search_prefs）；空字串＝不記

    @property
    def paths(self) -> List[str]:
        return [i for i in self.items if isinstance(i, str)]


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

    def put(self, query: str, default_action: str, items: List[Item], total: int, term: str = "") -> ChoiceList:
        """新的搜尋結果直接取代舊清單"""
        with self._lock:
            self._current = ChoiceList(next(self._ids), query, default_action, list(items), total, self._clock() + self._ttl, term)
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
        """result 事件的 choices 欄位（不含完整路徑；網路項目只有標題與網域，不含網址與摘錄）。
        commands.jsonl 不寫 choices（assistant.command_record）"""
        return {
            "list_id": lst.list_id, "query": lst.query, "total": lst.total, "default_action": lst.default_action,
            "expires_in_s": max(0.0, lst.expires_at - self._clock()),
            "items": [_item_payload(n, item) for n, item in enumerate(lst.items, 1)],
        }


def _item_payload(n: int, item: Item) -> dict:
    if isinstance(item, WebItem):
        return {"n": n, "name": item.title, "dir": urlsplit(item.url).hostname or "", "kind": "web"}
    return {"n": n, "name": os.path.basename(item), "dir": short_dir(item),
            "kind": "folder" if os.path.isdir(item) else (os.path.splitext(item)[1].lstrip(".").lower() or "file")}


STORE = ChoiceStore()
