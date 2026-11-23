"""會變的事實一律查證（查詢路由計畫 docs/plans/completed/2026-11-23-query-routing.md U12、U13，§7.1 A15／A16）。
人物、新聞、比分、營業時間、價格由 Fast Path 直接送 web_answer（不經規劃、模型不能選憑記憶回答）；天氣走 get_weather。
有雲端沒搜尋金鑰 → 開搜尋頁＋⚠️＋step["web_fallback"]；兩把都沒有 → 交給本機 AI（與沒有雲端時相同）。"""
import json
import urllib.parse

import pytest

import mac_ai.router as r
from mac_ai.assistant import command_record
from mac_ai.router.fast_path import FastPathMatcher
from mac_ai.router.normalize import normalize_utterance
from support.cloud import KEY as AI_KEY
from support.llm import FakeLLM, forbid_llm
from support.web import brave  # noqa: F401

CATEGORIES = {
    "人物": ["周杰倫是誰", "查一下周杰倫是誰", "搜尋周杰倫是誰", "誰是蔡英文", "告訴我五月天是誰"],
    "新聞": ["最新新聞", "查一下最近的新聞", "告訴我最近的新聞", "最近有什麼新聞", "搜尋今天的新聞"],
    "比分": ["湖人贏了嗎", "勇士隊最近一場比賽誰贏了", "搜尋勇士隊最近一場比賽誰贏了", "中華隊比分多少", "今年世界大賽冠軍是誰"],
    "營業時間": ["故宮博物院開放時間", "故宮幾點關門", "全聯營業到幾點", "查一下故宮幾點關門", "好市多幾點開門"],
    "價格": ["台積電股價", "查台積電股價", "台積電股價多少", "美元匯率多少", "iPhone 17 多少錢"],
}
WEATHER = ["台北天氣", "搜尋台北天氣", "告訴我台北天氣", "今天台北會下雨嗎", "搜索一下台北天氣"]
NOT_VERIFY = {  # 不是會變的事實、或明說要看畫面／是指令：不走 web_answer
    "你是誰": None, "Python 是什麼": None, "這個多少錢": None, "新聞": None,
    "打開台積電股價網站": "not_web_answer", "上網查台積電股價": "web_search_or_open_url",
    "播放新聞": "not_web_answer", "打開股票": "not_web_answer",
}


def fast(text):
    m = FastPathMatcher()
    normalized = normalize_utterance(text)
    hits = [m.match(c, whole=normalized) for c in m.split_clauses(normalized)]
    return [a for h in hits if h is not None for a in h.actions] if all(hits) else None


def say(text):
    return r.process_voice_command(text)


@pytest.mark.parametrize("text", [t for ts in CATEGORIES.values() for t in ts])
def test_changing_facts_go_straight_to_web_answer(text):
    actions = fast(text)
    assert actions is not None and [name for name, _ in actions] == ["web_answer"], (text, actions)
    assert actions[0][1]["query"]  # 只帶查詢字串


def test_each_category_has_at_least_five_sentences():
    assert all(len(v) >= 5 for v in CATEGORIES.values()) and len(WEATHER) >= 5


@pytest.mark.parametrize("text", WEATHER)
def test_weather_goes_to_get_weather(text):
    actions = fast(text)
    assert actions is not None and [name for name, _ in actions] == ["get_weather"], (text, actions)


@pytest.mark.parametrize("text, expect", NOT_VERIFY.items())
def test_not_changing_facts_are_untouched(text, expect):
    actions = fast(text)
    names = [name for name, _ in actions or []]
    assert "web_answer" not in names, (text, actions)
    if expect == "web_search_or_open_url":
        assert names == [expect]


# ---------- 執行（A15：0 次規劃呼叫；A16：金鑰組合）----------
def test_with_both_keys_answer_without_any_planning_call(cloud, brave, sh, monkeypatch):  # noqa: F811
    forbid_llm(monkeypatch)
    cloud.reply = lambda body: {"content": "台積電今天收在 1,000 元。"}
    out = say("台積電股價")
    (step,) = out["steps"]
    assert step["kind"] == "fast" and step["status"] == "ok" and brave.calls[0]["query"] == "台積電股價"
    assert [b for b in cloud.requests if b.get("tools")] == []  # 沒有規劃呼叫，模型沒有機會選憑記憶回答
    assert out["replies"][0].startswith("台積電今天收在 1,000 元。")


def test_cloud_without_search_key_opens_google_and_warns(cloud, sh, capsys, monkeypatch):
    forbid_llm(monkeypatch)
    out = say("周杰倫是誰")
    (step,) = out["steps"]
    assert step["web_fallback"] == "no_search_key" and any(log.startswith("⚠️") for log in step["logs"])
    assert [c for c in sh if c and c[0] == "open"][-1][-1].startswith("https://www.google.com/search?q=")
    assert cloud.requests == []  # 沒有搜尋就不問雲端（不讓模型憑記憶回答）
    printed = capsys.readouterr().out
    assert "BRAVE_API_KEY" in printed  # 後端提醒只寫變數名稱
    records = json.dumps([command_record({"command": "周杰倫是誰", "ok": True, "total_ms": 1, "replies": out["replies"]}, out)],
                         ensure_ascii=False)
    assert AI_KEY not in printed + records


def test_without_any_key_hands_over_to_local_ai(sh, monkeypatch):
    """兩把都沒有：原本就交給 Slow Path 的句子照舊交給本機 AI"""
    local = FakeLLM(plan={"tool_calls": []}, chat="周杰倫是臺灣歌手。").install(monkeypatch)
    out = say("周杰倫是誰")
    assert local.plans and [c for c in sh if c and c[0] == "open"] == []
    assert out["steps"][0].get("web_fallback") is None


@pytest.mark.parametrize("text, query", [("查一下周杰倫是誰", "周杰倫是誰"), ("查一下最近的新聞", "最近的新聞"),
                                         ("搜尋勇士隊最近一場比賽誰贏了", "勇士隊最近一場比賽誰贏了")])
def test_without_any_key_sentences_that_opened_google_still_do(text, query, sh, monkeypatch):
    """A16：兩把都沒有時與現況相同。原本開 Google 的『查／搜尋＋會變的事實』照舊開網頁，不交給本機模型憑記憶回答"""
    forbid_llm(monkeypatch)
    out = say(text)
    (cmd,) = [c for c in sh if c and c[0] == "open"]
    assert cmd[-1] == "https://www.google.com/search?q=" + urllib.parse.quote(query)
    assert out["steps"][0]["status"] == "ok" and out["steps"][0].get("web_fallback") is None
