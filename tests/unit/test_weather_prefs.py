"""weather_prefs：記錄明說的縣市、統計常問縣市（計畫 §4.7、驗收 A11）。暫存資料庫（conftest 複製的 head 空庫）、注入 now。"""
import pytest

from mac_ai import config
from mac_ai.router import weather_prefs as wp

DAY = 86400.0
NOW = 1_000_000_000.0


@pytest.fixture
def conn(music_db):
    return music_db.conn()


def record(conn, city, days_ago=0.0, n=1):
    for _ in range(n):
        wp.record_city(conn, city, NOW - days_ago * DAY)


def test_empty_database_is_cold_start(conn):
    assert wp.default_city(conn, NOW) is None


def test_one_record_is_enough(conn):
    record(conn, "臺北市")
    assert wp.default_city(conn, NOW) == "臺北市"


def test_most_frequent_wins(conn):
    record(conn, "高雄市", 1, n=2)
    record(conn, "臺北市", 0.5)
    assert wp.default_city(conn, NOW) == "高雄市"


def test_tie_goes_to_the_most_recent(conn):
    record(conn, "高雄市", 5, n=2)
    record(conn, "臺北市", 1, n=2)
    assert wp.default_city(conn, NOW) == "臺北市"


def test_old_records_do_not_beat_recent_ones(conn):
    record(conn, "高雄市", 200, n=10)  # 窗口（90 天）外
    record(conn, "臺北市", 10)
    assert wp.default_city(conn, NOW) == "臺北市"


def test_window_edge(conn):
    record(conn, "高雄市", 89)
    record(conn, "臺北市", 91, n=5)
    assert wp.default_city(conn, NOW) == "高雄市"
    assert config.WEATHER_DEFAULT_WINDOW_S == 90 * DAY


def test_falls_back_to_all_history_when_the_window_is_empty(conn):
    record(conn, "高雄市", 400)
    record(conn, "臺北市", 300, n=2)
    assert wp.default_city(conn, NOW) == "臺北市"


def test_record_is_append_only_one_row_per_call(conn):
    record(conn, "臺北市", n=3)
    assert conn.execute("SELECT COUNT(*), MIN(city), MAX(city) FROM weather_queries").fetchone() == (3, "臺北市", "臺北市")


def test_cold_start_message_is_the_agreed_cute_sentence():
    m = wp.COLD_START_MESSAGE
    assert m.startswith("⚠️") and "縣市" in m and "台北天氣" in m and "住哪裡" in m
