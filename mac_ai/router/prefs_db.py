"""偏好資料庫（點歌、常搜名詞、天氣的縣市與居住地共用）與可注入的時間、亂數。
其他模組一律寫 prefs_db.MUSIC_DB、prefs_db._now()、prefs_db._rng（不要 from import）：測試與 conftest 會整個換掉它們。"""
import random
import sqlite3
import time
from typing import Callable

from mac_ai import db as music_db


MUSIC_DB = music_db.MusicDb()  # 偏好資料庫；main.py 啟動時 start()（背景準備），準備好之前 conn() 是 None：照常播放、不寫紀錄（D4、D7）。測試由 conftest 換成暫存目錄
_now: Callable[[], float] = time.time  # 時間與亂數都能注入（測試 monkeypatch）
_rng = random.Random()


def _db_call(info: dict, fn: Callable, *args, **kw):
    """資料庫操作失敗（磁碟滿、被鎖…）不能讓點歌失敗：記下例外類別（不記內容）後回 None。全部在呼叫 play_media 的執行緒（Y16）"""
    try:
        return fn(*args, **kw)
    except sqlite3.Error as e:
        info["db_error"] = type(e).__name__
        return None
