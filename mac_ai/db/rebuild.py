"""從 events 重算 terms（計畫 §4.4「擴充」、§7.1 D8）：`python -m mac_ai.db.rebuild [--db 路徑]`。
重算用的是和即時更新同一組 apply_* 函式，依事件 id 順序、以事件的 ts 當時間重播，所以結果和逐步即時更新完全相同。
需要的資訊都從 events 與 videos 取得：play 的 query／source 在事件上，歌名與頻道（mv／ytm 是 YouTube Music 的歌名與「、」連接的歌手）在 videos；
like／dislike／replace 的歌名存在事件的 query 欄位。事件不會被改動。"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

from mac_ai import config
from mac_ai.router import music_prefs as mp


def rebuild(conn: sqlite3.Connection, now: Optional[float] = None) -> int:
    """清掉 terms 後從 events 重播；回傳重播的事件數。給了 now 就再衰減到 now 並刪除過低的詞（和定時整理同一個步驟）；
    不給就保留重播結果（每個 term 的 updated 是最後一次被事件更新的時間）。"""
    count = 0
    with conn:
        conn.execute("DELETE FROM terms")
        rows = conn.execute(
            "SELECT e.ts, e.kind, e.query, e.source, v.title, v.channel FROM events e "
            "LEFT JOIN videos v ON v.video_id = e.video_id "
            "WHERE e.kind IN ('play','like','dislike','replace') AND e.ok=1 ORDER BY e.id").fetchall()
        for ts, kind, query, source, title, channel in rows:
            if kind == "play":
                if source not in mp.LEARN_SOURCES:
                    continue
                artists = [a for a in (channel or "").split("、") if a] if source in ("mv", "ytm") else None
                mp.apply_play(conn, query=query or "", source=source, title=title or "", channel=channel or "",
                              artists=artists, now=ts)
            elif query:
                {"like": lambda: mp.apply_like(conn, query, ts), "dislike": lambda: mp.apply_dislike(conn, query),  # noqa: B023  # lambda 建立後立刻呼叫
                 "replace": lambda: mp.apply_replace(conn, query, ts)}[kind]()  # noqa: B023
            count += 1
        if now is not None:
            mp.decay_terms(conn, now)
    return count


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="從 events 重算 music 資料庫的 terms")
    ap.add_argument("--db", default=str(config.MUSIC_DB_PATH), help="資料庫路徑（預設 config.MUSIC_DB_PATH）")
    args = ap.parse_args(argv)
    path = Path(args.db)
    if not path.is_file():
        print(f"❌ 找不到資料庫：{path}")
        return 1
    from mac_ai import db  # 延後 import，只在命令列用
    conn = db.connect(path)
    try:
        n = rebuild(conn, time.time())
        terms = conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0]
    except sqlite3.DatabaseError as e:
        print(f"❌ 重算失敗：{e}")
        return 1
    finally:
        conn.close()
    print(f"✅ 重播 {n} 筆事件，terms 現有 {terms} 筆")
    return 0


if __name__ == "__main__":
    sys.exit(main())
