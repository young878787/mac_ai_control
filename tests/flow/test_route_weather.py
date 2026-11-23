"""天氣回答路由：Fast Path 窄規則 → get_weather → 氣象署（有金鑰）→ 失敗改查 Open-Meteo → 都失敗回查不到。
計畫 docs/plans/completed/2026-11-21-web-answer.md §4.1、§4.6、§4.7；驗收 A1～A5、A11～A13。語料可以加、不能刪。
全部離線：網路用 tests/support/weather.py 的假 urlopen；時間固定在台北 2026-11-23 10:00。"""
import json
import sqlite3
import subprocess
import urllib.error
import urllib.request

import pytest

import mac_ai.router as r
from mac_ai.router import local_actions, weather_prefs
from support.expect import fast_matches
from support.llm import FakeLLM, forbid_llm
from support.weather import CWA_TODAY, CWA_TOMORROW, KEY, METEO_TODAY, METEO_TOMORROW, NOW_EPOCH, net  # noqa: F401

COLD = weather_prefs.COLD_START_MESSAGE
TAIPEI, KAOHSIUNG = "臺北市", "高雄市"

# 正例：原句 → (city, day, part)。city 空字串＝沒說縣市（用常問的縣市）
POSITIVE = [
    ("台北天氣", (TAIPEI, "today", "")), ("臺北天氣", (TAIPEI, "today", "")), ("明天天氣", ("", "tomorrow", "")),
    ("天氣", ("", "today", "")), ("今天天氣", ("", "today", "")), ("台北的天氣", (TAIPEI, "today", "")),
    ("查一下台北天氣", (TAIPEI, "today", "")), ("幫我找一下台北天氣", (TAIPEI, "today", "")), ("查天氣", ("", "today", "")),
    ("查詢明天天氣", ("", "tomorrow", "")), ("幫我查一下今天的天氣", ("", "today", "")),
    ("告訴我台北天氣", (TAIPEI, "today", "")), ("跟我說明天天氣", ("", "tomorrow", "")), ("請問台北天氣怎麼樣", (TAIPEI, "today", "")),
    ("今天台北會下雨嗎", (TAIPEI, "today", "")), ("台北明天會下雨嗎", (TAIPEI, "tomorrow", "")), ("會下雨嗎", ("", "today", "")),
    ("明天會不會下雨", ("", "tomorrow", "")), ("今晚會下雨嗎", ("", "today", "night")), ("明晚會下雨嗎", ("", "tomorrow", "night")),
    ("明天早上天氣", ("", "tomorrow", "day")), ("今天白天會下雨嗎", ("", "today", "day")), ("高雄明天天氣怎麼樣", (KAOHSIUNG, "tomorrow", "")),
    ("明天高雄天氣如何", (KAOHSIUNG, "tomorrow", "")), ("台北天氣明天怎麼樣", (TAIPEI, "tomorrow", "")),
    ("今天幾度", ("", "today", "")), ("台北今天幾度", (TAIPEI, "today", "")), ("現在幾度", ("", "today", "")),
    ("高雄氣溫多少", (KAOHSIUNG, "today", "")), ("台中降雨機率多少", ("臺中市", "today", "")), ("新竹縣天氣", ("新竹縣", "today", "")),
    ("新竹天氣", ("新竹市", "today", "")), ("嘉義天氣", ("嘉義市", "today", "")), ("台東明天天氣", ("臺東縣", "tomorrow", "")),
    ("馬祖天氣", ("連江縣", "today", "")), ("今天天氣怎麼樣呢", ("", "today", "")),
]

# 反例：不能產生 get_weather（分類只為了讀得懂）
NEGATIVE = {
    "開 App 與其他詞": ["打開天氣", "開天氣", "天氣之子是什麼電影", "天氣之子", "天氣預報App", "打開天氣預報"],
    "敘述與感受": ["今天天氣好熱", "今天天氣很好", "天氣真好", "台北天氣不錯", "天氣好熱喔", "今天會下雨", "聽說明天會下雨"],
    "否定": ["不要查天氣", "不用告訴我天氣", "別查台北天氣", "先不要看天氣", "我沒有要查天氣"],
    "過去": ["昨天天氣很好", "昨天台北下雨了嗎", "剛剛下雨了", "上次的天氣"],
    "明說要看畫面": ["在瀏覽器查台北天氣", "打開網頁看台北天氣", "打開瀏覽器查台北天氣", "用 Google 查台北天氣", "上網查台北天氣", "給我看台北天氣", "顯示台北天氣", "開 Safari 查天氣", "google 一下台北天氣"],
    "超過 36 小時": ["後天天氣", "後天台北天氣", "週末天氣", "週末台北會下雨嗎", "下週天氣", "這個禮拜天氣", "未來一週天氣"],
    "兩個地點或時間": ["台北高雄天氣", "今天明天天氣", "台北和高雄的天氣"],
    "沒有主題": ["幾度", "今天", "台北", "明天", "多少度", "調高溫度", "冷氣溫度調低", "室溫幾度"],
    "別的東西": ["台北車站天氣", "天氣好嗎的歌", "我想聽天氣之子", "播放下雨天", "關掉下雨的聲音"],
}
NEGATIVE_ALL = [t for texts in NEGATIVE.values() for t in texts]
# 否定與敘述（守門 G1）：交給 Slow 後也不呼叫任何模型
GUARDED = NEGATIVE["否定"] + ["昨天天氣很好", "剛剛下雨了", "上次的天氣", "昨天台北下雨了嗎"]


def _weather_actions(matches):
    return [a for m in matches if m is not None for a in m.actions if a[0] == "get_weather"]


def test_corpus_sizes():
    assert len(POSITIVE) >= 35 and len(NEGATIVE_ALL) >= 45 and len({t for t, _ in POSITIVE}) == len(POSITIVE)


# ---------- A1／A3：命中與不命中 ----------
@pytest.mark.parametrize("text,expected", POSITIVE, ids=[t for t, _ in POSITIVE])
def test_positive_matches_exactly_one_get_weather(text, expected):
    matches = fast_matches(text)
    assert len(matches) == 1 and matches[0] is not None
    city, day, part = expected
    assert matches[0].actions == [("get_weather", {"city": city, "day": day, "part": part})]


@pytest.mark.parametrize("text", NEGATIVE_ALL)
def test_negative_never_produces_get_weather(text):
    assert _weather_actions(fast_matches(text)) == []


@pytest.mark.parametrize("text", GUARDED)
def test_guarded_sentences_never_call_the_llm_or_fetch(monkeypatch, sh, net, text):
    forbid_llm(monkeypatch)
    r.process_voice_command(text)
    assert sh == [] and net.calls == []


def test_match_has_no_io(monkeypatch):
    """match() 保持純粹：不碰網路、資料庫、子程序，也不讀環境金鑰"""
    calls = []
    for owner, name in ((urllib.request, "urlopen"), (sqlite3, "connect"), (subprocess, "run")):
        monkeypatch.setattr(owner, name, lambda *a, _n=name, **k: calls.append(_n))
    for text in [t for t, _ in POSITIVE] + NEGATIVE_ALL:
        fast_matches(text)
    assert calls == []


def test_open_the_web_sentences_keep_their_current_behaviour(monkeypatch, sh):
    """明說要看畫面的句子：照舊開 Google 搜尋頁，天氣規則不攔（句首的「搜尋」不再算明說，U7）"""
    forbid_llm(monkeypatch)
    r.process_voice_command("上網查台北天氣")
    assert sh == [["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]]


# ---------- 執行：固定時間、有金鑰 ----------
@pytest.fixture
def at_noon(monkeypatch):
    monkeypatch.setattr(local_actions, "_now", lambda: NOW_EPOCH)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("CWA_API_KEY", KEY)


def say(text):
    return r.process_voice_command(text)


def test_answer_with_key_uses_cwa_and_opens_nothing(monkeypatch, sh, net, at_noon, with_key):
    forbid_llm(monkeypatch)
    out = say("台北天氣")
    step = out["steps"][0]
    assert out["replies"] == [CWA_TODAY] and step["logs"] == [f"✅ {CWA_TODAY}"] and step["status"] == "ok"
    assert net.names() == ["cwa"] and sh == []
    assert step["weather"]["source"] == "氣象署" and step["weather"]["tried"] == [] and step["weather"]["city"] == TAIPEI


def test_tomorrow_questions(monkeypatch, sh, net, at_noon, with_key):
    forbid_llm(monkeypatch)
    assert say("台北明天會下雨嗎")["replies"] == [CWA_TOMORROW]


def test_without_key_uses_open_meteo(monkeypatch, sh, net, at_noon):
    forbid_llm(monkeypatch)
    out = say("台北天氣")
    assert out["replies"] == [METEO_TODAY] and net.names() == ["meteo"] and out["steps"][0]["weather"]["source"] == "Open-Meteo"
    assert say("台北明天天氣")["replies"] == [METEO_TOMORROW]


@pytest.mark.parametrize("failure", [urllib.error.URLError(TimeoutError()), 500, b"<html>", {"success": "true", "records": {"location": []}}])
def test_cwa_failure_falls_back_to_open_meteo_and_is_still_a_success(monkeypatch, sh, net, at_noon, with_key, failure):
    forbid_llm(monkeypatch)
    net.set("cwa", failure)
    out = say("台北天氣")
    step = out["steps"][0]
    assert out["replies"] == [METEO_TODAY] and step["status"] == "ok" and net.names() == ["cwa", "meteo"]
    assert len(step["weather"]["tried"]) == 1 and step["weather"]["tried"][0].startswith("cwa:") and sh == []


def test_both_sources_down_replies_not_found_without_opening_anything(monkeypatch, sh, net, at_noon, with_key):
    forbid_llm(monkeypatch)
    net.set("cwa", urllib.error.URLError(TimeoutError())).set("meteo", urllib.error.URLError(TimeoutError()))
    out = say("台北天氣")
    step = out["steps"][0]
    assert step["logs"] == ["❌ 嗚…我查不到臺北市的天氣耶，氣象服務好像沒有回應，等一下再問我一次好嗎？"]
    assert step["status"] == "exec_failed" and out["replies"] == [] and sh == []
    assert step["weather"]["tried"] == ["cwa:timeout", "open_meteo:timeout"] and not any("✅" in x for x in step["logs"])


def test_no_key_and_open_meteo_down_is_not_found(monkeypatch, sh, net, at_noon):
    forbid_llm(monkeypatch)
    net.set("meteo", 503)
    out = say("今天台北天氣")
    assert out["steps"][0]["logs"][0].startswith("❌ 嗚…我查不到臺北市的天氣") and net.names() == ["meteo"]


def test_an_exception_in_the_client_never_escapes(monkeypatch, sh, net, at_noon, with_key):
    forbid_llm(monkeypatch)
    net.set("cwa", MemoryError("boom"))
    assert say("台北天氣")["steps"][0]["status"] == "ok"  # 當成失敗改查 Open-Meteo


def test_key_never_appears_in_outputs_or_urls(monkeypatch, capsys, sh, net, at_noon, with_key):
    forbid_llm(monkeypatch)
    net.set("cwa", urllib.error.HTTPError(f"https://x/?Authorization={KEY}", 401, "bad", {}, None))
    outputs = [say("台北天氣"), say("高雄明天會下雨嗎")]
    shown = json.dumps(outputs, ensure_ascii=False, default=str) + capsys.readouterr().out
    assert KEY not in shown and all(KEY not in c["url"] for c in net.calls)
    assert all(c["headers"].get("authorization") == KEY for c in net.calls if c["name"] == "cwa")


# ---------- A11／A13：預設地點（資料庫）與冷啟動 ----------
def rows(music_db):
    return music_db.conn().execute("SELECT city FROM weather_queries ORDER BY id").fetchall()


def test_cold_start_asks_for_a_city_without_any_network(monkeypatch, sh, net, at_noon, music_db):
    forbid_llm(monkeypatch)
    out = say("明天天氣")
    assert out["steps"][0]["logs"] == [COLD] and out["replies"] == [] and net.calls == [] and sh == [] and rows(music_db) == []


def test_database_not_ready_is_cold_start_but_explicit_city_still_answers(monkeypatch, sh, net, at_noon):
    """資料庫還沒準備好（music_db fixture 沒啟動）：說了縣市照常回答、不記；沒說縣市當冷啟動"""
    forbid_llm(monkeypatch)
    assert say("台北天氣")["replies"] == [METEO_TODAY]
    assert say("明天天氣")["steps"][0]["logs"] == [COLD]


def test_explicit_cities_are_recorded_and_default_uses_the_most_asked(monkeypatch, sh, net, at_noon, music_db):
    forbid_llm(monkeypatch)
    for text in ("高雄天氣", "台北天氣", "高雄明天會下雨嗎"):
        say(text)
    assert rows(music_db) == [(KAOHSIUNG,), (TAIPEI,), (KAOHSIUNG,)]
    out = say("明天天氣")
    assert out["replies"][0].startswith("高雄市明天白天：") and out["steps"][0]["weather"] == {
        **out["steps"][0]["weather"], "city": KAOHSIUNG, "explicit": False}
    assert len(rows(music_db)) == 3  # 用預設補上的縣市不記，預設才不會自己越記越多


def test_a_city_is_recorded_even_when_the_lookup_fails(monkeypatch, sh, net, at_noon, music_db):
    forbid_llm(monkeypatch)
    net.set("meteo", 500)
    say("台中天氣")
    assert rows(music_db) == [("臺中市",)]


def test_database_error_does_not_break_the_answer(monkeypatch, sh, net, at_noon, music_db):
    forbid_llm(monkeypatch)
    music_db.conn().execute("DROP TABLE weather_queries")
    out = say("台北天氣")
    assert out["replies"] == [METEO_TODAY] and out["steps"][0]["weather"]["db_error"] == "OperationalError"


def test_clock_is_taipei_time_even_if_machine_timezone_differs(monkeypatch, sh, net, with_key):
    """氣象署時段是台北時間：現在時間用 UTC+8 換算，不依賴這台電腦的時區（洛杉磯此刻還是 11-22 18:00）"""
    import os
    import time
    forbid_llm(monkeypatch)
    monkeypatch.setattr(local_actions, "_now", lambda: NOW_EPOCH)
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    try:
        assert say("台北天氣")["replies"] == [CWA_TODAY]
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


# ---------- 超過 36 小時、LLM 路徑不受影響 ----------
def test_far_future_goes_where_it_went_before(monkeypatch, sh, net, at_noon):
    """後天天氣不在窄規則內：Fast Path 不命中，照舊交給 Slow Path（這裡 LLM 預設不可用 → 告知沒處理），不碰天氣來源"""
    out = say("後天天氣")
    assert out["steps"][0]["kind"] == "slow" and "cwa" not in net.names() and "meteo" not in net.names()


def test_weather_sentences_do_not_reach_the_llm_or_cloud(monkeypatch, sh, net, at_noon, with_key):
    llm = FakeLLM().install(monkeypatch)
    for text, _ in POSITIVE[:12]:
        say(text)
    assert llm.plans == [] and llm.chats == []
