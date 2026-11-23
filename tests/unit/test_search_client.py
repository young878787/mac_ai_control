"""Brave 搜尋 client：解析（樣本手寫自官方文件欄位）、請求（金鑰只在標頭）、錯誤分類（不帶網址、金鑰、回應內容）。全部離線。"""
import socket
import urllib.error

import pytest

from mac_ai import config
from mac_ai.router import search_client as sc
from support.web import KEY, brave, load  # noqa: F401


# ---------- 解析 ----------
def test_parse_sample_strips_markup_and_keeps_only_http_urls():
    res = sc.parse(load())
    assert [r.url for r in res] == ["https://zh.wikipedia.org/wiki/%E5%91%A8%E6%9D%B0%E5%80%AB", "https://www.jaychou.example/about",
                                    "http://baike.example/item/jay"]
    assert res[0].title == "周杰倫 - 維基百科，自由的百科全書"
    assert "<strong>" not in res[0].snippet and "&nbsp;" not in res[0].snippet and "\xa0" not in res[0].snippet
    assert res[0].snippet.startswith("周杰倫（Jay Chou，1979年1月18日－）")


def test_parse_drops_unsafe_or_empty_items_and_applies_limit():
    data = {"web": {"results": [
        {"title": "js", "url": "javascript:alert(1)", "description": "x"},
        {"title": "ftp", "url": "ftp://host/file", "description": "x"},
        {"title": "沒網址", "description": "x"},
        {"title": "", "url": "https://a.example/", "description": ""},  # 沒標題也沒摘要
        {"title": "只有標題", "url": "https://b.example/"},
        {"title": "ok1", "url": "https://c.example/", "description": "d1"},
        {"title": "ok2", "url": "https://d.example/", "description": "d2"},
        "not a dict", None, 5,
    ]}}
    assert [r.title for r in sc.parse(data, limit=2)] == ["只有標題", "ok1"]
    assert [r.title for r in sc.parse(data)] == ["只有標題", "ok1", "ok2"]


def test_parse_truncates_long_snippets():
    data = {"web": {"results": [{"title": "t", "url": "https://a.example/", "description": "字" * 1000}]}}
    assert len(sc.parse(data)[0].snippet) == config.WEB_SNIPPET_CHARS


@pytest.mark.parametrize("data,kind", [
    ({}, "no_results"), ({"web": None}, "no_results"), ({"web": {"results": []}}, "no_results"),
    ({"web": {"results": [{"title": "x", "url": "javascript:1"}]}}, "no_results"),
    ([], "bad_format"), ("text", "bad_format"), (None, "bad_format"),
    ({"web": []}, "bad_format"), ({"web": {"results": "x"}}, "bad_format"), ({"web": {"results": None}}, "bad_format"),
])
def test_parse_failure_kinds(data, kind):
    with pytest.raises(sc.SearchError) as e:
        sc.parse(data)
    assert e.value.kind == kind


# ---------- 請求 ----------
def test_request_carries_key_only_in_header_and_the_configured_params(brave):
    res = sc.search("周杰倫  是誰\n")
    (call,) = brave.calls
    assert call["url"].startswith(config.BRAVE_SEARCH_URL + "?")
    assert call["query"] == "周杰倫 是誰"  # 空白收斂
    assert call["params"] == {"q": "周杰倫 是誰", "count": str(config.WEB_SEARCH_COUNT), "country": config.BRAVE_COUNTRY,
                              "search_lang": config.BRAVE_SEARCH_LANG, "text_decorations": "false"}
    assert call["headers"]["x-subscription-token"] == KEY and call["headers"]["accept"] == "application/json"
    assert KEY not in call["url"] and call["timeout"] == config.WEB_SEARCH_TIMEOUT_S
    assert len(res) == 3


def test_query_is_truncated_and_explicit_timeout_wins(brave):
    sc.search("字" * 1000, timeout=1.25)
    (call,) = brave.calls
    assert len(call["query"]) == 200 and call["timeout"] == 1.25


def test_freshness_is_sent_only_when_given(brave):
    sc.search("勇士 比賽", freshness="pw")
    sc.search("勇士 比賽")
    first, second = brave.calls
    assert first["params"]["freshness"] == "pw" and "freshness" not in second["params"]


# ---------- 網頁日期與時間範圍 ----------
def test_page_age_becomes_the_result_date_only_when_it_is_a_date():
    data = {"web": {"results": [
        {"title": "a", "url": "https://a.example/", "description": "x", "page_age": "2026-11-23T04:09:28"},
        {"title": "b", "url": "https://b.example/", "description": "x"},
        {"title": "c", "url": "https://c.example/", "description": "x", "page_age": "1 hour ago"},
        {"title": "d", "url": "https://d.example/", "description": "x", "page_age": 1732000000},
        {"title": "e", "url": "https://e.example/", "description": "x", "page_age": '2026-11-23"><tool>'},
    ]}}
    assert [r.date for r in sc.parse(data, limit=10)] == ["2026-11-23", "", "", "", "2026-11-23"]


@pytest.mark.parametrize("text,fresh", [
    ("勇士隊最近一場比賽誰贏了", "pw"), ("最近有什麼新聞", "pw"), ("最新的 iPhone 是哪一代", "pw"), ("昨天湖人贏了嗎", "pw"),
    ("今天股市怎麼樣", "pw"), ("這週有什麼電影", "pw"), ("剛剛地震幾級", "pw"),
    ("今年金馬獎最佳影片是哪一部", "py"), ("今年最新的 iPhone 是哪一代", "py"),  # 兩種都有取範圍大的
    ("台灣現在的總統是誰", None), ("目前的匯率多少", None), ("周杰倫是誰", None), ("全聯營業到幾點", None),
])
def test_freshness_follows_time_words_in_the_original_sentence(text, fresh):
    assert sc.freshness_for(text) == fresh


def test_no_key_means_no_network_call(monkeypatch, brave):
    monkeypatch.delenv("BRAVE_API_KEY")
    assert sc.available() is False
    with pytest.raises(sc.SearchError) as e:
        sc.search("x")
    assert e.value.kind == "no_key" and brave.calls == []


def test_blank_query_means_no_network_call(brave):
    with pytest.raises(sc.SearchError) as e:
        sc.search("  \n ")
    assert e.value.kind == "bad_format" and brave.calls == []


def test_available_follows_the_key(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "  ")
    assert sc.available() is False
    monkeypatch.setenv("BRAVE_API_KEY", KEY)
    assert sc.available() is True


# ---------- 錯誤分類：只留分類，不帶網址、金鑰、回應內容 ----------
@pytest.mark.parametrize("failure,kind", [
    (urllib.error.URLError(TimeoutError()), "timeout"), (urllib.error.URLError(socket.timeout()), "timeout"),
    (TimeoutError(), "timeout"), (socket.timeout(), "timeout"),
    (urllib.error.URLError("dns failed"), "network"), (ConnectionResetError(), "network"),
    (401, "http_401"), (403, "http_403"), (422, "http_422"), (429, "http_429"), (500, "http_500"),
    (b"{", "bad_json"), (b"\xff\xfe", "bad_json"), (b"[]", "bad_format"), (b'{"web": {"results": 5}}', "bad_format"),
    ({"web": {"results": []}}, "no_results"),
])
def test_failures_are_classified_and_never_leak(brave, failure, kind):
    brave.value = failure
    with pytest.raises(sc.SearchError) as e:
        sc.search("周杰倫")
    assert e.value.kind == kind
    leaked = f"{e.value!s}{e.value!r}{e.value.__cause__!r}{e.value.__context__!r}"
    assert KEY not in leaked and "api.search.brave.com" not in leaked and "周杰倫" not in leaked


def test_non_search_exceptions_become_bad_format(brave):
    brave.value = lambda req: (_ for _ in ()).throw(RuntimeError(f"boom {KEY}"))
    with pytest.raises(sc.SearchError) as e:
        sc.search("x")
    assert e.value.kind == "bad_format" and KEY not in repr(e.value)
