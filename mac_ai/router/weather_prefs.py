"""天氣偏好：記下使用者明說過的縣市，沒說縣市時用統計決定（計畫 docs/plans/completed/2026-11-21-web-answer.md §4.7）。
本模組不碰網路與子程序。函式一律收呼叫端（command_worker 執行緒）的 sqlite3.Connection 與注入的 now（秒），
不自己連線、不讀 time 全域；每個公開函式自己 commit；SQL 全部參數化。"""
import sqlite3
from typing import Optional

from mac_ai import config

COLD_START_MESSAGE = "⚠️ 欸～我還不知道你住哪裡耶！跟我說一下縣市嘛，像「台北天氣」這樣，下次我就記住囉～"  # 資料庫沒有紀錄、句子也沒講縣市（W15）


def record_city(conn: sqlite3.Connection, city: str, now: float) -> None:
    """記一筆「使用者明說了這個縣市」。用預設補上的縣市不能記（否則預設會自己越記越多，改不掉）"""
    with conn:
        conn.execute("INSERT INTO weather_queries (ts, city) VALUES (?, ?)", (now, city))


def default_city(conn: sqlite3.Connection, now: float) -> Optional[str]:
    """最近 WEATHER_DEFAULT_WINDOW_S 內明說次數最多的縣市；同票取最近問的。窗口內沒有就看全部歷史；都沒有回 None（冷啟動）"""
    sql = "SELECT city FROM weather_queries WHERE ts >= ? GROUP BY city ORDER BY COUNT(*) DESC, MAX(ts) DESC, city LIMIT 1"
    row = conn.execute(sql, (now - config.WEATHER_DEFAULT_WINDOW_S,)).fetchone()
    if row is None:
        row = conn.execute(sql, (float("-inf"),)).fetchone()
    return row[0] if row else None
