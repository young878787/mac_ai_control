"""守門整條路由：G1 否定／敘述不呼叫 LLM、資訊提問丟掉改變狀態的工具、不該擋的不擋、搜尋意圖守門"""
import pytest

import mac_ai.router as r
from mac_ai.router import slow_path
from support.llm import FakeLLM, llm_always, tc


@pytest.fixture
def llm(monkeypatch):
    """假 LLM：規劃請求回 llm.plan 的工具呼叫（預設鎖定螢幕）；chat 請求回固定文字。記錄在 llm.plans／llm.chats"""
    return FakeLLM(plan=[tc("system_power_action", {"action": "lock_screen"})], chat="好的，我不會這樣做。").install(monkeypatch)


# ---------- 否定／敘述：不呼叫 LLM、不執行、回固定文字 ----------
@pytest.mark.parametrize("text", [
    "不要鎖定螢幕", "別讓電腦睡眠", "剛剛鎖定螢幕了", "電腦剛剛自己睡眠了", "剛才關掉Safari了", "不要開小算盤",
    "要不要鎖定螢幕", "不要鎖定螢幕，幫我想個名字",
])
def test_negative_or_narrative_never_plans_or_runs(llm, sh, text):
    llm.plan = [tc("system_power_action", {"action": "lock_screen"}), tc("system_power_action", {"action": "sleep"})]
    out = r.process_voice_command(text)
    (step,) = out["steps"]
    assert sh == [] and llm.plans == [] and llm.chats == []  # 規劃與 chat 都不送（chat 實測會謊稱已執行）
    assert step["kind"] == "slow" and step["guard"] == "no_action" and step["status"] == "ok"
    assert step["logs"][0] == "🛡️ 這句是否定或敘述，不執行任何電腦操作"
    assert out["replies"] == [slow_path.NO_ACTION_REPLY] and "llm_ms" not in step


def test_negative_sentence_works_when_llm_is_down(sh):
    """LLM 不可用（conftest 預設）也一樣：不執行、固定回覆、狀態 ok"""
    out = r.process_voice_command("剛剛鎖定螢幕了")
    assert sh == [] and out["steps"][0]["status"] == "ok" and out["replies"] == [slow_path.NO_ACTION_REPLY]


# ---------- 資訊提問：丟掉會改變狀態的工具 ----------
def test_info_question_drops_state_tools_then_chats(llm, sh):
    out = r.process_voice_command("鎖定螢幕是什麼意思")
    (step,) = out["steps"]
    assert sh == [] and len(llm.plans) == 1 and len(llm.chats) == 1
    assert step["guard"] == "info_question" and step["guard_dropped"] == ["system_power_action"]
    assert step["logs"][0].startswith("🛡️ 提問句不執行會改變狀態的動作：system_power_action")
    assert slow_path.NO_ACTION_HINT in llm.chats[0][0]["content"]


def test_info_question_keeps_search(llm, sh):
    llm.plan = [tc("system_power_action", {"action": "dark_mode_on"}), tc("web_search_or_open_url", {"query": "台北天氣"})]
    out = r.process_voice_command("台北天氣怎麼這麼熱")
    assert sh == [["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]]
    assert out["steps"][0]["guard_dropped"] == ["system_power_action"] and llm.chats == []


# ---------- 不擋的 ----------
@pytest.mark.parametrize("text,plan,expected", [
    ("可以幫我開小算盤嗎", [tc("control_mac_app", {"app_name": "Calculator", "action": "open"})], [["open", "-a", "Calculator"]]),
    ("你可以幫我鎖定螢幕嗎", [tc("system_power_action", {"action": "lock_screen"})],
     [["osascript", "-e", 'tell application "System Events" to keystroke "q" using {control down, command down}']]),
    ("今天台北天氣會很熱嗎", [tc("web_search_or_open_url", {"query": "台北天氣"})],
     [["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]]),
    ("不是啦我是說把音量調到30", [tc("set_system_volume", {"level": 30})], [["osascript", "-e", "set volume output volume 30"]]),
])
def test_polite_request_yes_no_question_and_correction_are_unchanged(llm, sh, text, plan, expected):
    llm.plan = plan
    out = r.process_voice_command(text)
    assert sh == expected and "guard" not in out["steps"][0] and len(llm.plans) == 1


def test_correction_with_comma_still_reads_screen(monkeypatch, llm, sh):
    llm.plan = [tc("read_screen", {})]
    called = []
    monkeypatch.setattr(r.actions, "read_screen", lambda ctx=None: called.append(1) or "✅ 畫面內容：x")
    out = r.process_voice_command("沒有，我說的是這個畫面")
    assert called == [1] and all("guard" not in s for s in out["steps"])


# ---------- 搜尋意圖守門：沒有搜尋的意思，LLM 就不能開搜尋頁 ----------
@pytest.mark.parametrize("utterance,query", [
    ("幫我找一下台北天氣", "台北天氣"),
    ("台北天氣怎麼樣", "台北天氣"),
    ("今天會下雨嗎", "今天台北天氣"),
    ("最新新聞", "最新新聞"),
    ("台積電股價", "台積電 股價"),
    ("匯率是多少", "美元匯率"),
    ("上網看一下", "latest news"),
    ("Google 一下 Python", "Python"),
    ("打開 github.com", "github.com"),
    ("打開 github.com", "https://github.com"),  # 網址只要字面出現在原句（不計 https://）
])
def test_search_with_intent_is_allowed(monkeypatch, utterance, query):
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query": "%s"}' % query)])
    calls, _, rejected = r.plan_slow_path(utterance)
    assert rejected == [] and calls == [("web_search_or_open_url", {"query": query})]


@pytest.mark.parametrize("utterance,query", [
    ("沒有我說的是這網頁", "台北天氣 2025-09-27"),  # 實測：新版 tools 的幻覺 query
    ("沒有我說的是這網頁", "https://www.example.com"),  # 實測：舊版 tools 的幻覺網址（原句沒有這個網址）
    ("網頁", "台北天氣"),
    ("我是說這網頁啦", "台北天氣"),
    ("不是這個網頁", "這網頁"),
    ("我想看貓咪影片", "貓咪影片"),  # 已知取捨：開放式資訊需求要明說『搜尋／查』
])
def test_search_without_intent_is_rejected(monkeypatch, utterance, query):
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query": "%s"}' % query)])
    calls, _, rejected = r.plan_slow_path(utterance)
    assert calls == [] and len(rejected) == 1 and "沒有搜尋的意思" in rejected[0]


def test_rejected_search_opens_nothing(monkeypatch, sh):
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query": "台北天氣 2025-09-27"}')])
    out = r.process_voice_command("沒有我說的是這網頁")
    assert sh == []  # 沒有 open、osascript 等任何系統呼叫
    (step,) = out["steps"]
    assert step["kind"] == "slow" and step["status"] == "rejected"
    assert step["logs"] and step["logs"][0].startswith("⚠️") and "沒有搜尋的意思" in step["logs"][0]


@pytest.mark.parametrize("utterance,name,args,expected", [
    ("找一下終端機", "control_mac_app", '{"app_name": "Terminal", "action": "open"}', ("control_mac_app", {"app_name": "Terminal", "action": "open"})),
    ("沒有我說的是這個畫面", "read_screen", "{}", ("read_screen", {})),
    ("不是啦把音量調到30", "set_system_volume", '{"level": 30}', ("set_system_volume", {"level": 30})),
])
def test_intent_guard_only_applies_to_web_search(monkeypatch, utterance, name, args, expected):
    llm_always(monkeypatch, [tc(name, args)])
    calls, _, rejected = r.plan_slow_path(utterance)
    assert rejected == [] and calls == [expected]
