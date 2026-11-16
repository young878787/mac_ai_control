"""對話記憶（端雲協同計畫 §6.5）：最近幾輪的原句、執行過的工具與回覆摘要。跨語句，所以不放在 ExecContext。
給雲端規劃參考（user 訊息，不放 system），以及「它／剛剛那個」的對象守門（slow_path.target_error）。"""
import time
from typing import Callable, List, Optional, Set, Tuple
from xml.sax.saxutils import escape

from mac_ai.config import MEMORY_TURNS, WAKE_SESSION_S
from mac_ai.router.tools import TOOLS_SCHEMA

Call = Tuple[str, dict, bool]  # (工具, 參數, 是否成功)
_SCHEMA_TOOLS = {t["function"]["name"] for t in TOOLS_SCHEMA}
_TARGET_ARGS = {"control_mac_app": "app_name", "open_project": "project_name"}
REPLY_MAX_CHARS = 80


def call_text(name: str, args: dict, ok: bool) -> str:
    """給模型看的一筆工具紀錄。不在 TOOLS_SCHEMA 的本機工具只留名稱（參數可能有路徑、搜尋字）"""
    shown = ", ".join(f"{k}={v}" for k, v in args.items()) if name in _SCHEMA_TOOLS else ""
    return f"{name}({shown}) {'✅' if ok else '❌'}"


def targets_of(calls: List[Call]) -> Set[str]:
    """成功的工具呼叫裡的對象（App、專案）"""
    return {args[_TARGET_ARGS[n]] for n, args, ok in calls if ok and n in _TARGET_ARGS and args.get(_TARGET_ARGS[n])}


class MemoryStore:
    def __init__(self, turns: int = MEMORY_TURNS, ttl_s: float = WAKE_SESSION_S, clock: Callable[[], float] = time.monotonic):
        self.turns, self.ttl_s, self.clock = turns, ttl_s, clock
        self._items: List[dict] = []

    def clear(self) -> None:
        self._items = []

    def add(self, user: str, calls: List[Call], replies: List[str]) -> None:
        reply = " ".join(replies).strip()
        self._items.append({"t": self.clock(), "user": user, "calls": list(calls),
                            "reply": reply[:REPLY_MAX_CHARS] + ("…" if len(reply) > REPLY_MAX_CHARS else "")})
        self._items = self._items[-self.turns:]

    def recent(self) -> List[dict]:
        """有效的幾輪（舊→新）；距離最後一輪超過 ttl 就整個清空（同喚醒會話：停下來太久就是新話題）"""
        if self._items and self.clock() - self._items[-1]["t"] > self.ttl_s:
            self._items = []
        return list(self._items)

    def targets(self) -> Set[str]:
        out: Set[str] = set()
        for item in self.recent():
            out |= targets_of(item["calls"])
        return out

    def render(self, current: Optional[List[Call]] = None) -> Optional[str]:
        """XML 片段（雲端 user 訊息用）；current＝這一句前面幾步已經執行的工具。沒有內容回傳 None"""
        now = self.clock()
        turns = []
        for item in self.recent():
            calls = "；".join(call_text(*c) for c in item["calls"])
            turns.append(f'<turn seconds_ago="{int(now - item["t"])}"><user>{escape(item["user"])}</user>'
                         + (f"<actions>{escape(calls)}</actions>" if calls else "")
                         + (f"<reply>{escape(item['reply'])}</reply>" if item["reply"] else "") + "</turn>")
        parts = ["<recent_turns>\n" + "\n".join(turns) + "\n</recent_turns>"] if turns else []
        if current:
            parts.append(f"<this_sentence_done>{escape('；'.join(call_text(*c) for c in current))}</this_sentence_done>")
        return "\n".join(parts) or None


STORE = MemoryStore()
