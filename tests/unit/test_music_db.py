"""偏好資料庫 schema 管理（計畫 §4.4、§7.1 D1～D5）。全部用 tmp_path，不寫專案的 data/。
D6（不載入 alembic）、D7（背景模式）要開子程序／執行緒，在 tests/runtime/test_music_db_runtime.py。"""
import shutil
import sqlite3
from pathlib import Path

import pytest

from mac_ai import db
from support.youtube import net, set_real  # noqa: F401

REAL_ENV = Path(db.__file__).parent / "migrations" / "env.py"

# §4.4 schema：(name, type, notnull, default, pk)
EXPECTED = {
    "events": [("id", "INTEGER", 0, None, 1), ("ts", "REAL", 1, None, 0), ("kind", "TEXT", 1, None, 0),
               ("utterance", "TEXT", 0, None, 0), ("query", "TEXT", 0, None, 0), ("corrected", "TEXT", 0, None, 0),
               ("video_id", "TEXT", 0, None, 0), ("source", "TEXT", 0, None, 0),
               ("latency_ms", "REAL", 0, None, 0), ("ok", "INTEGER", 1, None, 0)],
    "videos": [("video_id", "TEXT", 0, None, 1), ("title", "TEXT", 0, None, 0), ("channel", "TEXT", 0, None, 0),
               ("channel_id", "TEXT", 0, None, 0), ("first_seen", "REAL", 0, None, 0),
               ("last_seen", "REAL", 0, None, 0)],
    "queries": [("query", "TEXT", 0, None, 1), ("video_id", "TEXT", 1, None, 0), ("results", "TEXT", 0, None, 0),
                ("updated", "REAL", 1, None, 0), ("pinned", "INTEGER", 1, "0", 0)],
    "terms": [("term", "TEXT", 0, None, 1), ("kind", "TEXT", 1, None, 0), ("pinyin", "TEXT", 1, None, 0),
              ("weight", "REAL", 1, None, 0), ("plays", "INTEGER", 1, None, 0), ("liked", "INTEGER", 1, "0", 0),
              ("updated", "REAL", 1, None, 0)],
    "maintenance": [("key", "TEXT", 0, None, 1), ("value", "TEXT", 0, None, 0)],
}


def _dump(path: Path):
    c = sqlite3.connect(str(path))
    try:
        return list(c.iterdump())
    finally:
        c.close()


def _scalar(path: Path, sql: str):
    c = sqlite3.connect(str(path))
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def _fake_revisions(tmp_path: Path, **revs: str) -> Path:
    """假的 migration 目錄：用真的 env.py，versions/ 底下依 revs（檔名 → 內容）寫入"""
    loc = tmp_path / "fake_migrations"
    (loc / "versions").mkdir(parents=True, exist_ok=True)
    shutil.copy(REAL_ENV, loc / "env.py")
    for name, body in revs.items():
        (loc / "versions" / f"{name}.py").write_text(body)
    return loc


R1 = '''
from alembic import op
revision = "r1"
down_revision = None
def upgrade():
    op.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL)")
    op.execute("PRAGMA user_version = 1")
def downgrade():
    op.execute("DROP TABLE events")
'''
R2_OK = '''
from alembic import op
revision = "r2"
down_revision = "r1"
def upgrade():
    op.execute("ALTER TABLE events ADD COLUMN utterance TEXT")
    op.execute("PRAGMA user_version = 2")
def downgrade():
    pass
'''
R2_BOOM = '''
from alembic import op
revision = "r2"
down_revision = "r1"
def upgrade():
    op.execute("ALTER TABLE events ADD COLUMN utterance TEXT")   # 先有改動，再失敗
    op.execute("CREATE TABLE half_done (x)")
    raise RuntimeError("boom")
def downgrade():
    pass
'''


def _seed_v1_with_events(tmp_path: Path, path: Path, n: int = 10) -> Path:
    loc = _fake_revisions(tmp_path, r1=R1)
    assert db.ensure_schema(path, script_location=loc).ok
    c = sqlite3.connect(str(path))
    c.executemany("INSERT INTO events (ts, kind) VALUES (?, ?)", [(float(i), "play") for i in range(n)])
    c.commit()
    c.close()
    return loc


def test_head_version_matches_migration():
    assert db.HEAD_VERSION == 4


def test_d1_first_run_creates_folder_file_and_schema(tmp_path):
    path = tmp_path / "nested" / "data" / "music.sqlite3"
    r = db.ensure_schema(path)
    assert r.ok and r.message == "" and r.backup is None and r.corrupt is None
    assert path.exists()
    c = sqlite3.connect(str(path))
    try:
        for table, cols in EXPECTED.items():
            got = [(n, t, nn, d, pk) for _, n, t, nn, d, pk in c.execute(f"PRAGMA table_info({table})")]
            assert got == cols, table
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"events_ts", "terms_pinyin"} <= names
        assert c.execute("SELECT version_num FROM alembic_version").fetchall() == [("0004",)]
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.HEAD_VERSION == 4
    finally:
        c.close()
    assert not list(path.parent.glob("*.bak-*"))


def test_v1_database_upgrades_to_weather_queries_with_backup(tmp_path):
    """版本 1（只有點歌的舊庫）→ 2：先備份成 .bak-1、既有資料不變、多一張 weather_queries（計畫 §4.7、A13）"""
    path = tmp_path / "music.sqlite3"
    assert db.ensure_schema(path).ok
    c = sqlite3.connect(str(path))
    c.executescript("DROP TABLE profile; DROP TABLE search_terms; DROP TABLE weather_queries; UPDATE alembic_version SET version_num='0001'; PRAGMA user_version=1;")
    c.executemany("INSERT INTO events (ts, kind, ok) VALUES (?, 'play', 1)", [(float(i),) for i in range(5)])
    c.commit()
    c.close()
    before = _scalar(path, "SELECT COUNT(*) FROM events")
    r = db.ensure_schema(path)
    assert r.ok and r.backup is not None and r.backup.name == "music.sqlite3.bak-1"
    assert _scalar(r.backup, "PRAGMA user_version") == 1 and _scalar(r.backup, "SELECT COUNT(*) FROM events") == before == 5
    assert _scalar(path, "PRAGMA user_version") == 4 and _scalar(path, "SELECT COUNT(*) FROM events") == 5
    assert _scalar(path, "SELECT version_num FROM alembic_version") == "0004"
    c = sqlite3.connect(str(path))
    try:
        assert [(n, t, nn, pk) for _, n, t, nn, d, pk in c.execute("PRAGMA table_info(weather_queries)")] == [
            ("id", "INTEGER", 0, 1), ("ts", "REAL", 1, 0), ("city", "TEXT", 1, 0)]
        assert "weather_queries_ts" in {x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        c.close()


def test_v2_database_upgrades_to_search_terms_with_backup(tmp_path):
    """版本 2 → 3：先備份成 .bak-2、既有資料（點歌、天氣）不變、多一張 search_terms（查詢路由計畫 §4.4、A13）"""
    path = tmp_path / "music.sqlite3"
    assert db.ensure_schema(path).ok
    c = sqlite3.connect(str(path))
    c.executescript("DROP TABLE profile; DROP TABLE search_terms; UPDATE alembic_version SET version_num='0002'; PRAGMA user_version=2;")
    c.executemany("INSERT INTO events (ts, kind, ok) VALUES (?, 'play', 1)", [(float(i),) for i in range(5)])
    c.executemany("INSERT INTO weather_queries (ts, city) VALUES (?, '臺北市')", [(float(i),) for i in range(3)])
    c.commit()
    c.close()
    r = db.ensure_schema(path)
    assert r.ok and r.backup is not None and r.backup.name == "music.sqlite3.bak-2"
    assert _scalar(r.backup, "PRAGMA user_version") == 2 and _scalar(r.backup, "SELECT COUNT(*) FROM weather_queries") == 3
    assert _scalar(path, "PRAGMA user_version") == 4 and _scalar(path, "SELECT version_num FROM alembic_version") == "0004"
    assert _scalar(path, "SELECT COUNT(*) FROM events") == 5 and _scalar(path, "SELECT COUNT(*) FROM weather_queries") == 3
    c = sqlite3.connect(str(path))
    try:
        assert [(n, t, nn, pk) for _, n, t, nn, d, pk in c.execute("PRAGMA table_info(search_terms)")] == [
            ("id", "INTEGER", 0, 1), ("ts", "REAL", 1, 0), ("term", "TEXT", 1, 0), ("side", "TEXT", 1, 0),
            ("signal", "TEXT", 1, 0), ("path", "TEXT", 0, 0)]
        assert "search_terms_term" in {x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        c.close()


def test_v3_database_upgrades_to_profile_with_backup(tmp_path):
    """版本 3 → 4：先備份成 .bak-3、既有資料（點歌、天氣、常搜名詞）不變、多一張 profile（計畫 2026-11-24-weather-city-and-answer-timeout.md §3.1、A4）"""
    path = tmp_path / "music.sqlite3"
    assert db.ensure_schema(path).ok
    c = sqlite3.connect(str(path))
    c.executescript("DROP TABLE profile; UPDATE alembic_version SET version_num='0003'; PRAGMA user_version=3;")
    c.executemany("INSERT INTO events (ts, kind, ok) VALUES (?, 'play', 1)", [(float(i),) for i in range(5)])
    c.executemany("INSERT INTO weather_queries (ts, city) VALUES (?, '臺北市')", [(float(i),) for i in range(3)])
    c.executemany("INSERT INTO search_terms (ts, term, side, signal) VALUES (?, '履歷', 'local', 'explicit')", [(float(i),) for i in range(2)])
    c.commit()
    c.close()
    before = {t: _dump_table(path, t) for t in ("events", "weather_queries", "search_terms")}
    r = db.ensure_schema(path)
    assert r.ok and r.backup is not None and r.backup.name == "music.sqlite3.bak-3"
    assert _scalar(r.backup, "PRAGMA user_version") == 3 and _scalar(r.backup, "SELECT COUNT(*) FROM search_terms") == 2
    assert _scalar(path, "PRAGMA user_version") == 4 and _scalar(path, "SELECT version_num FROM alembic_version") == "0004"
    assert {t: _dump_table(path, t) for t in before} == before
    c = sqlite3.connect(str(path))
    try:
        assert [(n, t, nn, pk) for _, n, t, nn, d, pk in c.execute("PRAGMA table_info(profile)")] == [
            ("key", "TEXT", 0, 1), ("value", "TEXT", 1, 0), ("source", "TEXT", 1, 0), ("updated_at", "REAL", 1, 0)]
        assert c.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 0
    finally:
        c.close()


def _dump_table(path: Path, table: str):
    c = sqlite3.connect(str(path))
    try:
        return c.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    finally:
        c.close()


def test_d2_already_head_no_backup_and_data_unchanged(tmp_path):
    path = tmp_path / "music.sqlite3"
    assert db.ensure_schema(path).ok
    c = db.connect(path)
    c.executemany("INSERT INTO events (ts, kind, ok) VALUES (?, 'play', 1)", [(float(i),) for i in range(5)])
    c.execute("INSERT INTO terms VALUES ('周杰倫','artist','zhou jie lun',1.0,3,0,1.0)")
    c.commit()
    c.close()
    before = _dump(path)
    r = db.ensure_schema(path)
    assert r.ok and r.backup is None and r.message == ""
    assert _dump(path) == before
    assert [p.name for p in tmp_path.iterdir()] == ["music.sqlite3"]  # 沒有備份、沒有殘留的 -wal／.upgrading


def test_d3_upgrade_backs_up_first_and_keeps_rows(tmp_path):
    path = tmp_path / "music.sqlite3"
    loc = _seed_v1_with_events(tmp_path, path, n=10)
    before = _dump(path)
    (loc / "versions" / "r2.py").write_text(R2_OK)
    r = db.ensure_schema(path, script_location=loc)
    bak = tmp_path / "music.sqlite3.bak-1"
    assert r.ok and r.backup == bak and bak.exists()
    assert _dump(bak) == before                      # 備份和升級前相同
    assert _scalar(path, "SELECT COUNT(*) FROM events") == 10
    assert _scalar(path, "PRAGMA user_version") == 2
    assert _scalar(path, "SELECT version_num FROM alembic_version") == "r2"
    assert "utterance" in [row[1] for row in sqlite3.connect(str(path)).execute("PRAGMA table_info(events)")]
    assert not (tmp_path / "music.sqlite3.upgrading").exists()


def test_d4_upgrade_failure_leaves_original_bytes_untouched(tmp_path, monkeypatch, net, sh):
    path = tmp_path / "music.sqlite3"
    loc = _seed_v1_with_events(tmp_path, path)
    original = path.read_bytes()
    (loc / "versions" / "r2.py").write_text(R2_BOOM)
    r = db.ensure_schema(path, script_location=loc)
    assert not r.ok and r.message.startswith("⚠️") and "boom" in r.message
    assert path.read_bytes() == original             # 位元組完全相同
    assert r.backup is not None and r.backup.exists()
    assert sorted(p.name for p in tmp_path.glob("music.sqlite3*")) == ["music.sqlite3", "music.sqlite3.bak-1"]
    # play_media 照常回 ✅（用假網路與假瀏覽器），但資料庫沒有任何改動（db.conn() 為 None 的路徑）
    from mac_ai.router import music_tools, prefs_db
    mdb = db.MusicDb(path, ensure=lambda p: db.ensure_schema(p, script_location=loc))
    mdb.start()
    assert mdb.wait_ready(30) and mdb.conn() is None and mdb.message.startswith("⚠️")
    monkeypatch.setattr(prefs_db, "MUSIC_DB", mdb)
    set_real(net, "qingtian")
    assert music_tools.play_media("晴天").startswith("✅ 播放：") and any(c[0] == "open" for c in sh)
    assert path.read_bytes() == original


def test_d4_unknown_revision_is_failure_not_overwrite(tmp_path):
    """資料庫比程式新（revision 不認得）：不動原檔、回 ⚠️"""
    path = tmp_path / "music.sqlite3"
    _seed_v1_with_events(tmp_path, path)
    original = path.read_bytes()
    other = _fake_revisions(tmp_path / "other", x1=R1.replace('"r1"', '"x1"'))
    r = db.ensure_schema(path, script_location=other)
    assert not r.ok and r.message.startswith("⚠️")
    assert path.read_bytes() == original


def test_d5_corrupt_file_is_renamed_and_recreated(tmp_path):
    path = tmp_path / "music.sqlite3"
    garbage = b"this is definitely not a sqlite database\x00\xff" * 200
    path.write_bytes(garbage)
    r = db.ensure_schema(path)
    assert r.ok and r.message.startswith("⚠️")
    assert r.corrupt is not None and r.corrupt.name.startswith("music.sqlite3.corrupt-")
    assert r.corrupt.read_bytes() == garbage         # 原檔沒刪、沒改
    assert _scalar(path, "PRAGMA user_version") == db.HEAD_VERSION  # 新庫可用
    assert _scalar(path, "SELECT COUNT(*) FROM events") == 0


def test_empty_file_is_treated_as_new(tmp_path):
    path = tmp_path / "music.sqlite3"
    path.write_bytes(b"")
    r = db.ensure_schema(path)
    assert r.ok and r.message == "" and r.corrupt is None
    assert _scalar(path, "PRAGMA user_version") == db.HEAD_VERSION


def test_connect_uses_wal(tmp_path):
    path = tmp_path / "music.sqlite3"
    db.ensure_schema(path)
    c = db.connect(path)
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        c.close()


def test_musicdb_before_start_has_no_connection(tmp_path):
    m = db.MusicDb(tmp_path / "music.sqlite3")
    assert m.conn() is None and not m.ready and m.result is None and m.message == ""


# ---------- D8：從事件重算 terms ----------
def _live_session(conn):
    """模擬 play_media 與播放控制逐步即時更新 terms（呼叫端做的事：存影片、寫 play 事件、learn_play；like／replace／dislike 各自寫事件）"""
    from mac_ai.router import music_prefs as mp

    def play(vid, now, source, query, title, channel, artists=None):
        mp.upsert_video(conn, vid, title, channel, None, now)
        mp.record_event(conn, "play", now=now, query=query, video_id=vid, source=source)
        mp.learn_play(conn, query=query, source=source, title=title, channel=channel, artists=artists, now=now, video_id=vid)

    def replay_cached(vid, now, query):  # source=cache：歌名與頻道取自 videos
        title, channel = conn.execute("SELECT title, channel FROM videos WHERE video_id=?", (vid,)).fetchone()
        mp.record_event(conn, "play", now=now, query=query, video_id=vid, source="cache")
        mp.learn_play(conn, query=query, source="cache", title=title, channel=channel, artists=None, now=now)

    t = 1_800_000_000.0
    day = 86400.0
    play("ytm00000001", t, "ytm", "周杰倫的晴天", "晴天", "周杰倫", ["周杰倫"])
    play("mv000000001", t + day, "mv", "稻香", "稻香", "周杰倫 Jay Chou Official", ["周杰倫"])  # mv：影片原本的標題頻道會被 YouTube Music 的欄位取代
    play("web0000001", t + 2 * day, "web", "挪威的森林", "伍佰 & China Blue【挪威的森林】Official MV", "伍佰 Official Channel")
    play("web0000002", t + 3 * day, "web", "Shape of You", "Ed Sheeran - Shape Of You (Official Music Video)", "Ed Sheeran - Topic")
    play("ytm00000002", t + 4 * day, "ytm", "光年之外", "光年之外", "鄧紫棋、G.E.M.", ["鄧紫棋", "G.E.M."])
    replay_cached("ytm00000001", t + 5 * day, "晴天")
    replay_cached("web0000001", t + 6 * day, "挪威的森林")
    mp.record_event(conn, "request", now=t + 6 * day, query="不相干")  # 其他種事件不影響 terms
    mp.on_like(conn, "ytm00000001", "晴天", t + 7 * day)
    mp.on_replace(conn, "稻香", t + 8 * day, video_id="mv000000001")
    play("ytm00000003", t + 9 * day, "ytm", "晴天", "晴天", "周杰倫", ["周杰倫"])
    mp.on_dislike(conn, "api0000001", "Ed Sheeran - Shape Of You (Official Music Video)", t + 10 * day)
    mp.record_event(conn, "play", now=t + 11 * day, query="找不到", source="page", ok=False)  # 退回搜尋頁：不學
    return t + 30 * day


def _snapshot(conn):
    return conn.execute("SELECT term, kind, pinyin, weight, plays, liked, updated FROM terms ORDER BY term").fetchall()


def test_d8_rebuild_equals_incremental_updates(tmp_path):
    from mac_ai.db import rebuild as rb
    path = tmp_path / "m.sqlite3"
    assert db.ensure_schema(path).ok
    conn = db.connect(path)
    try:
        _live_session(conn)
        live = _snapshot(conn)
        events_before = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
        assert len(live) >= 8
        terms = {r[0]: r for r in live}
        assert terms["周杰倫"][1] == "artist" and terms["晴天"][5] == 1            # 內容合理：歌手、liked
        assert "Ed Sheeran" in terms and "Ed Sheeran - Shape Of You" not in terms   # web 清理過；dislike 的歌名已刪
        assert terms["稻香"][3] < 2.0                                              # replace 扣過
        n = rb.rebuild(conn)
        assert n == 11                                                              # 8 個 play（退回搜尋頁的不算）＋like＋replace＋dislike
        assert _snapshot(conn) == live                                              # 逐欄位完全相同（浮點也相同）
        assert conn.execute("SELECT * FROM events ORDER BY id").fetchall() == events_before  # 事件不動
        assert rb.rebuild(conn) == n and _snapshot(conn) == live                   # 冪等
    finally:
        conn.close()


def test_d8_rebuild_with_now_equals_live_maintenance(tmp_path):
    from mac_ai.db import rebuild as rb
    from mac_ai.router import music_prefs as mp
    path = tmp_path / "m.sqlite3"
    assert db.ensure_schema(path).ok
    conn = db.connect(path)
    try:
        now = _live_session(conn) + 400 * 86400.0  # 久到一部分詞衰減光
        n_before = len(_snapshot(conn))
        mp.maintain(conn, now)
        live = _snapshot(conn)
        assert 0 < len(live) < n_before                                             # 有詞被衰減刪掉，liked 的留著
        rb.rebuild(conn, now)
        got = _snapshot(conn)
        assert [r[0] for r in got] == [r[0] for r in live]                          # 留下的詞一樣
        for a, b in zip(got, live):
            assert a[:3] == b[:3] and a[4:] == b[4:] and a[3] == pytest.approx(b[3], abs=1e-9)
    finally:
        conn.close()


def test_d8_rebuild_cli(tmp_path, capsys):
    from mac_ai.db import rebuild as rb
    path = tmp_path / "m.sqlite3"
    assert db.ensure_schema(path).ok
    conn = db.connect(path)
    _live_session(conn)
    conn.execute("DELETE FROM terms")
    conn.commit()
    conn.close()
    assert rb.main(["--db", str(path)]) == 0
    assert "✅" in capsys.readouterr().out
    assert _scalar(path, "SELECT COUNT(*) FROM terms") > 0
    assert rb.main(["--db", str(tmp_path / "nope.sqlite3")]) == 1
    assert not (tmp_path / "nope.sqlite3").exists()                                 # 不替不存在的路徑建檔
