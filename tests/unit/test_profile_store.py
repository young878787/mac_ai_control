"""profile_store：常駐資料的讀寫（計畫 2026-11-24-weather-city-and-answer-timeout.md §3.1、A5）。暫存資料庫（conftest 複製的 head 空庫）、注入 now。"""
import pytest

from mac_ai.router import profile_store as ps

NOW = 1_000_000_000.0


@pytest.fixture
def conn(music_db):
    return music_db.conn()


def test_missing_key_is_none(conn):
    assert ps.get(conn, ps.HOME_CITY) is None


def test_set_then_get(conn):
    ps.set(conn, ps.HOME_CITY, "臺北市", "said", NOW)
    assert ps.get(conn, ps.HOME_CITY) == "臺北市"
    assert conn.execute("SELECT key, value, source, updated_at FROM profile").fetchall() == [("home_city", "臺北市", "said", NOW)]


def test_saying_it_again_overwrites(conn):
    ps.set(conn, ps.HOME_CITY, "臺北市", "said", NOW)
    ps.set(conn, ps.HOME_CITY, "高雄市", "cold_start_reply", NOW + 5)
    assert conn.execute("SELECT key, value, source, updated_at FROM profile").fetchall() == [
        ("home_city", "高雄市", "cold_start_reply", NOW + 5)]


def test_set_commits_for_other_connections(conn, music_db):
    """每個公開函式自己 commit：另一條連線馬上讀得到"""
    import sqlite3
    ps.set(conn, ps.HOME_CITY, "臺中市", "said", NOW)
    other = sqlite3.connect(str(music_db.path))
    try:
        assert other.execute("SELECT value FROM profile WHERE key='home_city'").fetchone() == ("臺中市",)
    finally:
        other.close()
