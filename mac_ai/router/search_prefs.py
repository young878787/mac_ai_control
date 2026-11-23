"""常搜名詞（查／找／搜尋）：記下使用者明說的範圍與從聯合清單選的那一邊，決定下次先列哪一邊、要不要直接用
（計畫 docs/plans/completed/2026-11-23-query-routing.md §4.4、U5、U6、U8）。
本模組不碰網路與子程序。函式一律收呼叫端（command_worker 執行緒）的 sqlite3.Connection 與注入的 now（秒），
不自己連線、不讀 time 全域；每個公開函式自己 commit；SQL 全部參數化。
只記使用者說的詞與選了哪一邊：網路那邊不記網址、標題（Brave 條款不准把搜尋結果存成資料庫）。
用統計或預設自動決定的結果不記（否則越記越偏，同 weather_prefs）"""
import sqlite3
from dataclasses import dataclass
from typing import Optional

from mac_ai import config
from mac_ai.router.lexicon import _compact

SIDES = ("local", "web")
SIGNALS = ("explicit", "picked")  # explicit＝句子明說範圍（找履歷檔案、上網查履歷）；picked＝從聯合清單選了那一邊


@dataclass(frozen=True)
class Lean:
    side: Optional[str] = None  # 窗口內次數較多的一邊（決定清單先列哪一邊）；同票或沒有紀錄是 None
    direct: bool = False  # U8：同一邊至少 SEARCH_LEAN_MIN 次、另一邊 0 次 → 只查這一邊
    path: Optional[str] = None  # 偏本機時最常選的完整路徑（U6 的「直接用」對象）


def term_of(query: str) -> str:
    """統計用的詞：小寫、去空白與 _-（『Python 教學』與『python教學』算同一個）"""
    return _compact(query.strip())


def record(conn: sqlite3.Connection, query: str, side: str, signal: str, now: float, path: Optional[str] = None) -> None:
    """記一筆。path 只有本機項目才記；網路一律 NULL"""
    if side not in SIDES or signal not in SIGNALS:
        raise ValueError(f"side／signal 不對：{side}／{signal}")
    term = term_of(query)
    if not term:
        return
    with conn:
        conn.execute("INSERT INTO search_terms (ts, term, side, signal, path) VALUES (?, ?, ?, ?, ?)",
                     (now, term, side, signal, path if side == "local" else None))


def lean(conn: sqlite3.Connection, query: str, now: float) -> Lean:
    """最近 SEARCH_LEAN_WINDOW_S 內這個詞偏哪一邊；沒有紀錄回 Lean()"""
    term, since = term_of(query), now - config.SEARCH_LEAN_WINDOW_S
    counts = dict(conn.execute("SELECT side, COUNT(*) FROM search_terms WHERE term = ? AND ts >= ? GROUP BY side",
                               (term, since)).fetchall())
    local, web = counts.get("local", 0), counts.get("web", 0)
    side = "local" if local > web else "web" if web > local else None
    direct = side is not None and max(local, web) >= config.SEARCH_LEAN_MIN and min(local, web) == 0
    path = None
    if side == "local":
        row = conn.execute("SELECT path FROM search_terms WHERE term = ? AND side = 'local' AND path IS NOT NULL AND ts >= ? "
                           "GROUP BY path ORDER BY COUNT(*) DESC, MAX(ts) DESC LIMIT 1", (term, since)).fetchone()
        path = row[0] if row else None
    return Lean(side, direct, path)
