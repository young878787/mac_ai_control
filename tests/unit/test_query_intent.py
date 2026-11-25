"""查詢分類 classify_query（計畫 docs/plans/completed/2026-11-23-query-routing.md §4.1、§7.1 A2／A3）。
P2：分類與既有規則相同，這裡鎖住每種結果的 target／mode／evidence；整條路由的行為由 test_routing_snapshot 鎖住"""
import json
import sqlite3
import subprocess
import urllib.request
from pathlib import Path

import pytest

from mac_ai.router import catalog
from mac_ai.router.fast_path import canonicalize_clause
from mac_ai.router.query_intent import classify_query

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "routing" / "corpus.json"


def classify(text, stage="lead"):
    raw = text.strip()
    return classify_query(canonicalize_clause(raw), catalog.DEFAULT, raw=raw, stage=stage)


@pytest.mark.parametrize("text, target, mode, evidence, extra", [
    ("查一下台北天氣", "knowledge", "answer", "category", {"category": "weather", "slots": {"city": "臺北市", "day": "today", "part": ""}}),
    ("明天會下雨嗎", "knowledge", "answer", "category", {"category": "weather"}),
    ("搜尋履歷", "either", "show", "verb", {"slots": {"where": "context"}, "query": "履歷"}),  # 沒說範圍：聯合搜尋（U5）
    ("查周杰倫", "either", "show", "verb", {"query": "周杰倫"}),
    ("查一下周杰倫是誰", "knowledge", "answer", "verify", {"category": "verify", "query": "周杰倫是誰"}),
    ("周杰倫是誰", "knowledge", "answer", "verify", {"query": "周杰倫是誰"}),
    ("台積電股價", "knowledge", "answer", "verify", {"query": "台積電股價"}),
    ("搜尋周杰倫是誰", "knowledge", "answer", "verify", {"query": "周杰倫是誰"}),
    ("找一下 Python 是什麼", "knowledge", "answer", "question", {"query": "Python 是什麼"}),
    ("查一下 Python 怎麼安裝", "knowledge", "answer", "question", {"query": "Python 怎麼安裝"}),
    ("查股票走勢", "knowledge", "show", "realtime", {"slots": {"where": "google"}}),
    ("上網查周杰倫", "knowledge", "show", "explicit_web", {"slots": {"where": "google"}}),
    ("google 一下周杰倫", "knowledge", "show", "explicit_web", {"slots": {"where": "google"}}),
    ("查台積電股價", "knowledge", "answer", "verify", {"query": "台積電股價"}),
    ("打開瀏覽器查台北天氣", "knowledge", "show", "explicit_web", {"slots": {"where": "google"}}),
    ("打開 Safari 搜尋貓咪", "knowledge", "show", "explicit_web", {"slots": {"where": "google", "app": "Safari"}}),
    ("搜尋 report.pdf", "local", "show", "file_hint", {}),
    ("在電腦裡搜尋履歷", "local", "show", "scope_word", {}),
])
def test_lead_stage_labels(text, target, mode, evidence, extra):
    q = classify(text)
    assert (q.target, q.mode, q.evidence) == (target, mode, evidence)
    for k, v in extra.items():
        assert getattr(q, k) == v


@pytest.mark.parametrize("text, target, domain", [
    ("找一下我的履歷", "local", "file"),
    ("找昨天下載的 PDF", "local", "file"),
    ("找一下周杰倫", "either", "file"),  # 沒有本機證據：聯合搜尋（U6）
    ("找 python 教學", "either", "file"),
    ("找一下我的履歷", "local", "file"),  # 『我的』算本機證據
    ("找履歷檔案", "local", "file"),
    ("上網找履歷範本", "knowledge", "web"),
])
def test_find_stage_labels(text, target, domain):
    q = classify(text, stage="find")
    assert (q.target, q.mode, q.intent.domain) == (target, "show", domain)


@pytest.mark.parametrize("text, stage", [
    ("打開小算盤", "lead"), ("音量調到 30", "lead"), ("Python 是什麼", "lead"), ("你是誰", "lead"), ("這個多少錢", "lead"),
    ("新聞", "lead"), ("播放新聞", "lead"), ("打開股票", "lead"),
    ("搜尋嗎", "lead"), ("google 周杰倫", "lead"),  # 現況：google 後面要接「一下」或搜尋動詞
    ("找一下周杰倫", "lead"),  # 「找」不在 lead 階段判斷
    ("搜尋履歷", "find"), ("打開小算盤", "find"),
])
def test_not_a_query_at_this_stage(text, stage):
    assert classify(text, stage) is None


def test_classify_query_is_pure(monkeypatch):
    """A3：整份語料（兩個階段）跑完都不碰子程序、網路、資料庫"""
    def boom(*a, **kw):
        raise AssertionError("classify_query 不得做 IO")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(sqlite3, "connect", boom)
    sentences = [s for g in json.loads(CORPUS.read_text())["groups"].values() for s in g]
    for text in sentences:
        classify(text)
        classify(text, stage="find")


# ---------- 居住地與天氣句首（計畫 2026-11-24-weather-city-and-answer-timeout.md §3.2～§3.5；A7、A9～A11）----------
from mac_ai.router.query_intent import parse_city_reply  # noqa: E402

TAIPEI = "臺北市"


def _actions(text):
    """同 pipeline：每個子句比對時帶整句（問號在切句時被切掉，要看整句）"""
    import mac_ai.router as r
    whole = r.normalize_utterance(text)
    matcher = r.FastPathMatcher()
    return [m.actions if m is not None else None for m in (matcher.match(c, whole=whole) for c in matcher.split_clauses(whole))]


@pytest.mark.parametrize("text, city", [
    ("我住台北", TAIPEI), ("我住在台中", "臺中市"), ("我家在高雄", "高雄市"), ("我們家住新北", "新北市"), ("我在台北喔", TAIPEI),
    ("我現在住台南", "臺南市"), ("我住台北啦", TAIPEI), ("那我住台北", TAIPEI),
])
def test_i_live_in_x_remembers_the_home_city(text, city):
    assert _actions(text) == [[("remember_home", {"city": city})]]


@pytest.mark.parametrize("text, day", [
    ("我住台北幫我查台北的天氣", "today"), ("我住台北明天天氣如何", "tomorrow"), ("我住台北天氣", "today"),
    ("我住台北幫我查明天的天氣", "tomorrow"),
])
def test_i_live_in_x_plus_weather_sets_home_and_answers(text, day):
    assert _actions(text) == [[("get_weather", {"city": TAIPEI, "day": day, "part": "", "set_home": "said"})]]


def test_i_live_in_x_comma_weather_is_two_steps():
    assert _actions("我住台北，明天天氣如何") == [[("remember_home", {"city": TAIPEI})],
                                               [("get_weather", {"city": "", "day": "tomorrow", "part": ""})]]


@pytest.mark.parametrize("text", ["我住台北幫我查高雄天氣", "我住台北查高雄明天天氣"])
def test_i_live_in_x_but_asks_another_city_is_not_fast(text):
    assert _actions(text) == [None]


# §3.3 誤觸發清單（A10）：一句都不能產生 remember_home 或 get_weather
FALSE_TRIGGERS = ["我不住台北", "我以前住台北", "我住台北很多年了", "我住台北的朋友說明天會下雨", "我朋友住台北", "你住台北嗎", "我住台北嗎",
                  "我要去台北", "我明天去台中", "我在台北出差", "台北", "他住台北", "我之前住在高雄", "我曾經住台中", "我沒住台北",
                  "我住台北？", "我們不住新北"]


@pytest.mark.parametrize("text", FALSE_TRIGGERS)
def test_false_triggers_never_remember_or_answer_weather(text):
    names = [a[0] for acts in _actions(text) if acts for a in acts]
    assert "remember_home" not in names and "get_weather" not in names


@pytest.mark.parametrize("text, day", [
    ("那幫我查詢明天的天氣", "tomorrow"), ("好那幫我查明天天氣", "tomorrow"), ("嗯台北天氣", "today"), ("對了明天會下雨嗎", "tomorrow"),
    ("那麼明天天氣如何", "tomorrow"), ("欸明天天氣", "tomorrow"), ("那台北天氣", "today"), ("好幫我查天氣", "today"),
])
def test_weather_lead_allows_interjections(text, day):
    (acts,) = _actions(text)
    assert acts is not None and acts[0][0] == "get_weather" and acts[0][1]["day"] == day


@pytest.mark.parametrize("text", ["好天氣", "那天氣", "好熱天氣", "好那今天天氣很好", "那天氣之子", "對了天氣之子是什麼電影", "那打開天氣"])
def test_interjection_does_not_turn_exclamations_into_weather(text):
    names = [a[0] for acts in _actions(text) if acts for a in acts]
    assert "get_weather" not in names


@pytest.mark.parametrize("text, city", [
    ("台北", TAIPEI), ("台北啦", TAIPEI), ("我住台北", TAIPEI), ("在台中", "臺中市"), ("我家在高雄", "高雄市"), ("是台南", "臺南市"),
    ("我住在新竹", "新竹市"), ("嗯台北喔", TAIPEI), ("臺北市", TAIPEI),
])
def test_city_reply(text, city):
    assert parse_city_reply(text) == city


@pytest.mark.parametrize("text", ["台北，順便調音量", "我不住台北", "台北嗎", "好啊", "明天天氣", "打開台北", "台北車站", "台北高雄", "我以前住台北", ""])
def test_not_a_city_reply(text):
    assert parse_city_reply(text) is None
