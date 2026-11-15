"""G1 守門（計劃書 §15）：否定／敘述句不讓 LLM 規劃工具、只回話；資訊提問只保留不改變狀態的工具；
是非問句／客氣請求、糾正句不擋；常見的『否定即指令』由 Fast Path 窄規則處理。LLM 一律是假的。"""
import json

import pytest

import mac_ai.router as r
from mac_ai.router import llm_client, slow_path
from mac_ai.router.fast_path import FastPathMatcher


def _tc(name, args):
    return {"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def llm(monkeypatch):
    """假 LLM：規劃請求（tools=True）回 llm.plan 的工具呼叫；chat 請求（tools=False）回固定文字。記錄兩種請求"""
    class Fake:
        plan = [_tc("system_power_action", {"action": "lock_screen"})]
        plans, chats = [], []

        def __call__(self, messages, tools=True, **kw):
            if tools:
                self.plans.append(messages)
                return {"tool_calls": self.plan}
            self.chats.append(messages)
            return {"content": "好的，我不會這樣做。"}
    fake = Fake()
    fake.plans, fake.chats = [], []
    monkeypatch.setattr(llm_client, "_chat", fake)
    return fake


# ---------- 判斷 ----------
@pytest.mark.parametrize("text,guard", [
    ("不要鎖定螢幕", "no_action"), ("我不想鎖定螢幕", "no_action"), ("別讓電腦睡眠", "no_action"),
    ("剛剛鎖定螢幕了", "no_action"), ("我已經打開小算盤了", "no_action"), ("要不要鎖定螢幕", "no_action"),
    ("不要鎖定螢幕，幫我想個名字", "no_action"),          # 混合：任一子句是否定就整步只回話
    ("不是，不要鎖定螢幕", "no_action"),
    ("鎖定螢幕是什麼意思", "info_question"), ("怎麼開深色模式", "info_question"), ("台北天氣怎麼樣", "info_question"),
    ("可以幫我開小算盤嗎", None), ("今天台北會下雨嗎", None),  # 是非問句／客氣請求：不擋
    ("螢幕太亮了，Python 是什麼", None),                       # 不是每個子句都是資訊提問
    ("沒有我說的是這網頁", None), ("沒有，我說的是這個畫面", None), ("不是啦我是說把音量調到30", None),  # 糾正句
    ("今天心情不錯", None),
])
def test_intent_guard(text, guard):
    assert slow_path.intent_guard(r.normalize_utterance(text)) == guard


# ---------- 否定／敘述：不呼叫 LLM、不執行、回固定文字 ----------
@pytest.mark.parametrize("text", [
    "不要鎖定螢幕", "別讓電腦睡眠", "剛剛鎖定螢幕了", "電腦剛剛自己睡眠了", "剛才關掉Safari了", "不要開小算盤",
    "要不要鎖定螢幕", "不要鎖定螢幕，幫我想個名字",
])
def test_negative_or_narrative_never_plans_or_runs(llm, sh, text):
    llm.plan = [_tc("system_power_action", {"action": "lock_screen"}), _tc("system_power_action", {"action": "sleep"})]
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
    llm.plan = [_tc("system_power_action", {"action": "dark_mode_on"}), _tc("web_search_or_open_url", {"query": "台北天氣"})]
    out = r.process_voice_command("台北天氣怎麼樣")
    assert sh == [["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]]
    assert out["steps"][0]["guard_dropped"] == ["system_power_action"] and llm.chats == []


# ---------- 不擋的 ----------
@pytest.mark.parametrize("text,plan,expected", [
    ("可以幫我開小算盤嗎", [_tc("control_mac_app", {"app_name": "Calculator", "action": "open"})], [["open", "-a", "Calculator"]]),
    ("你可以幫我鎖定螢幕嗎", [_tc("system_power_action", {"action": "lock_screen"})],
     [["osascript", "-e", 'tell application "System Events" to keystroke "q" using {control down, command down}']]),
    ("今天台北會下雨嗎", [_tc("web_search_or_open_url", {"query": "台北天氣"})],
     [["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]]),
    ("不是啦我是說把音量調到30", [_tc("set_system_volume", {"level": 30})], [["osascript", "-e", "set volume output volume 30"]]),
])
def test_polite_request_yes_no_question_and_correction_are_unchanged(llm, sh, text, plan, expected):
    llm.plan = plan
    out = r.process_voice_command(text)
    assert sh == expected and "guard" not in out["steps"][0] and len(llm.plans) == 1


def test_correction_with_comma_still_reads_screen(monkeypatch, llm, sh):
    llm.plan = [_tc("read_screen", {})]
    called = []
    monkeypatch.setattr(r.actions, "read_screen", lambda ctx=None: called.append(1) or "✅ 畫面內容：x")
    out = r.process_voice_command("沒有，我說的是這個畫面")
    assert called == [1] and all("guard" not in s for s in out["steps"])


# ---------- 否定即指令：Fast Path 窄規則 ----------
@pytest.mark.parametrize("text,action", [
    ("不要靜音了", ("set_system_volume", {"mute": False})),
    ("別再靜音了", ("set_system_volume", {"mute": False})),
    ("螢幕不要那麼亮", ("adjust_screen_brightness", {"direction": "down", "steps": 3})),
    ("不要這麼暗", ("adjust_screen_brightness", {"direction": "up", "steps": 3})),
    ("把螢幕別那麼亮了", ("adjust_screen_brightness", {"direction": "down", "steps": 3})),
    ("不要深色模式了", ("system_power_action", {"action": "dark_mode_off"})),
    ("我不想用深色模式", ("system_power_action", {"action": "dark_mode_off"})),
    ("我不要深色模式", ("system_power_action", {"action": "dark_mode_off"})),
    ("幫我不要深色模式了", ("system_power_action", {"action": "dark_mode_off"})),
])
def test_negated_command_rules(text, action):
    m = FastPathMatcher().match(r.normalize_utterance(text))
    assert m is not None and m.actions == [action]


@pytest.mark.parametrize("text", [
    "不要靜音", "不要打開深色模式", "不要開深色模式", "不要鎖定螢幕", "別讓電腦睡眠", "不要調暗", "螢幕不要",
    "不要那麼亮的螢幕", "不想要深色模式的網站",
])
def test_true_negations_are_not_negated_commands(text):
    assert FastPathMatcher().match(r.normalize_utterance(text)) is None
