"""常搜名詞統計 search_prefs（查詢路由計畫 docs/plans/completed/2026-11-23-query-routing.md §4.4、U8，§7.1 A9）。
用暫存目錄的真資料庫（ensure_schema 建到 head），不寫專案的 data/"""
import pytest

from mac_ai import config, db
from mac_ai.router import search_prefs as sp

DAY = 86400.0
NOW = 1_800_000_000.0


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "m.sqlite3"
    assert db.ensure_schema(path).ok
    c = db.connect(path)
    yield c
    c.close()


def rows(conn):
    return conn.execute("SELECT term, side, signal, path FROM search_terms ORDER BY id").fetchall()


def test_record_keeps_local_path_and_never_a_web_url(conn):
    sp.record(conn, "履歷", "local", "picked", NOW, path="/Users/x/履歷.pdf")
    sp.record(conn, "履歷", "web", "picked", NOW, path="https://example.com/should-not-be-saved")
    sp.record(conn, "上網查的詞", "web", "explicit", NOW)
    assert rows(conn) == [("履歷", "local", "picked", "/Users/x/履歷.pdf"), ("履歷", "web", "picked", None),
                          ("上網查的詞", "web", "explicit", None)]


def test_term_ignores_case_and_spaces(conn):
    sp.record(conn, "Python 教學", "local", "explicit", NOW)
    sp.record(conn, "python教學", "local", "explicit", NOW)
    assert sp.lean(conn, "PYTHON 教學", NOW) == sp.Lean("local", True, None)


@pytest.mark.parametrize("side, signal", [("both", "picked"), ("local", "default"), ("web", "")])
def test_unknown_side_or_signal_is_rejected(conn, side, signal):
    with pytest.raises(ValueError):
        sp.record(conn, "履歷", side, signal, NOW)
    assert rows(conn) == []


def test_empty_term_is_not_recorded(conn):
    sp.record(conn, "  ", "local", "picked", NOW)
    assert rows(conn) == []


@pytest.mark.parametrize("local, web, expected", [
    (0, 0, sp.Lean()),                       # 沒有歷史
    (1, 0, sp.Lean("local", False, None)),   # 1 次：只決定順序，不直接用
    (2, 0, sp.Lean("local", True, None)),    # U8：同一邊 2 次、另一邊 0 次
    (3, 1, sp.Lean("local", False, None)),   # 另一邊有 1 次就不直接用
    (0, 2, sp.Lean("web", True, None)),
    (1, 1, sp.Lean()),                       # 同票：沒有偏好
])
def test_lean_thresholds(conn, local, web, expected):
    for _ in range(local):
        sp.record(conn, "履歷", "local", "picked", NOW - DAY)
    for _ in range(web):
        sp.record(conn, "履歷", "web", "picked", NOW - DAY)
    assert sp.lean(conn, "履歷", NOW) == expected


def test_records_older_than_the_window_are_ignored(conn):
    old = NOW - config.SEARCH_LEAN_WINDOW_S - 1
    for _ in range(3):
        sp.record(conn, "履歷", "web", "picked", old)
    sp.record(conn, "履歷", "local", "picked", NOW)
    assert sp.lean(conn, "履歷", NOW) == sp.Lean("local", False, None)


def test_lean_returns_the_most_picked_local_path(conn):
    for path in ("/a/履歷v1.pdf", "/a/履歷v2.pdf", "/a/履歷v2.pdf"):
        sp.record(conn, "履歷", "local", "picked", NOW, path=path)
    sp.record(conn, "履歷", "local", "explicit", NOW)  # 沒有路徑的紀錄只算次數
    assert sp.lean(conn, "履歷", NOW) == sp.Lean("local", True, "/a/履歷v2.pdf")


def test_other_terms_do_not_count(conn):
    sp.record(conn, "履歷表", "local", "picked", NOW)
    sp.record(conn, "履歷表", "local", "picked", NOW)
    assert sp.lean(conn, "履歷", NOW) == sp.Lean()
