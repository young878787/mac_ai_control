"""常駐資料（profile 表）：使用者明說過的個人資料，目前只有 home_city（居住地）。
只記使用者明說的（Fast Path 規則認得的句型），不從統計推出、也不由模型決定要記什麼；資料只在本機，不送雲端、不進對話記憶
（計畫 docs/plans/completed/2026-11-24-weather-city-and-answer-timeout.md §3.1）。
規範同 weather_prefs：函式收呼叫端（command_worker 執行緒）的 sqlite3.Connection 與注入的 now（秒），不自己連線；
每個公開函式自己 commit；SQL 全部參數化。"""
import sqlite3
from typing import Optional

HOME_CITY = "home_city"


def get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM profile WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set(conn: sqlite3.Connection, key: str, value: str, source: str, now: float) -> None:
    """覆寫：新的說法直接取代舊值。source＝said／cold_start_reply"""
    with conn:
        conn.execute("INSERT INTO profile (key, value, source, updated_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, source = excluded.source, updated_at = excluded.updated_at",
                     (key, value, source, now))
