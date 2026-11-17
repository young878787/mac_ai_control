"""Slow Path 基本流程（LLM 以假回應取代）：規劃解析、拒絕、雙軌整合、依說話順序執行與計時"""
import pytest

import mac_ai.router as r
from mac_ai.router import llm_client, slow_path
from support.llm import FakeLLM, llm_always, tc


# ---------- Slow Path（LLM 以假回應取代）----------
def test_plan_slow_path_parses_json_string_and_dict(monkeypatch):
    msg = {"tool_calls": [
        tc("control_mac_app", '{"action": "open", "app_name": "Cursor"}'),
        {"type": "function", "function": {"name": "adjust_screen_brightness", "arguments": {"direction": "down", "steps": 3}}},  # arguments 直接是 dict
    ]}
    llm_always(monkeypatch, msg)
    calls, reply, rejected = r.plan_slow_path("x")
    assert calls == [("control_mac_app", {"action": "open", "app_name": "Cursor"}),
                     ("adjust_screen_brightness", {"direction": "down", "steps": 3})]
    assert rejected == []


def test_plan_slow_path_rejects_bad_calls(monkeypatch):
    msg = {"tool_calls": [
        tc("rm_rf", "{}"),
        tc("system_power_action", "{not json"),
        tc("system_power_action", '{"action": "lock_screen"}'),
    ]}
    llm_always(monkeypatch, msg)
    calls, _, rejected = r.plan_slow_path("x")
    assert calls == [("system_power_action", {"action": "lock_screen"})]
    assert len(rejected) == 2


def test_execute_slow_path_does_not_run_rejected(monkeypatch, sh):
    llm_always(monkeypatch, {"tool_calls": [tc("rm_rf", "{}")]})
    logs = r.execute_slow_path_llm("x")
    assert sh == [] and "已拒絕" in logs[0]


def test_execute_slow_path_plain_reply(monkeypatch, sh):
    llm_always(monkeypatch, {"content": "你好"})
    assert r.execute_slow_path_llm("x") == ["🤖 AI 回覆：你好"]
    assert sh == []


def test_hint_injected_only_when_alias_present(monkeypatch):
    """詞庫提示放在使用者訊息；system 逐字固定（且與暖機相同），工具定義的前綴快取才會命中"""
    seen = []
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: seen.append(messages) or {})
    r.plan_slow_path("開小算盤然後查天氣")
    r.plan_slow_path("查天氣")
    r.warmup_llm()
    assert seen[0][0]["content"] == seen[1][0]["content"] == seen[2][0]["content"] == slow_path.SYSTEM_PROMPT
    assert seen[0][1]["content"] == "開小算盤然後查天氣\n\n## 名稱對照\n- 小算盤→Calculator"
    assert seen[1][1]["content"] == "查天氣"


# ---------- 雙軌整合 ----------


def test_pipeline_mixed_routes_unmatched_to_llm(monkeypatch, sh):
    llm = FakeLLM([tc("adjust_screen_brightness", '{"direction":"down","steps":3}'),
                   tc("web_search_or_open_url", '{"query":"台北天氣"}')]).install(monkeypatch)
    r.process_voice_command("打開終端機，順便螢幕太亮了，還有今天台北會下雨嗎")
    assert sh[0] == ["open", "-a", "Terminal"]
    assert llm.texts == ["螢幕太亮了，今天台北會下雨嗎"]
    assert sh[2] == ["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]


# ---------- 依說話順序執行與計時 ----------
def test_match_does_not_execute(sh):
    run = r.FastPathMatcher().match("開 Chrome")
    assert sh == []
    run()
    assert sh == [["open", "-a", "Google Chrome"]]


def test_pipeline_keeps_spoken_order(monkeypatch, sh, installed_apps):
    """未命中的『我想用 Slack，用 Notion』在前、Fast Path 的音量在後 → 執行順序仍照說話順序"""
    installed_apps({"slack": "Slack", "notion": "Notion"})  # LLM 只能開詞庫或已安裝的 App
    llm = FakeLLM(by_text={"我想用 Slack，用 Notion": [
        tc("control_mac_app", '{"app_name":"Slack","action":"open"}'),
        tc("control_mac_app", '{"app_name":"Notion","action":"open"}'),
    ]}).install(monkeypatch)
    r.process_voice_command("我想用 Slack，用 Notion，把音量調到20")
    assert sh == [["open", "-a", "Slack"], ["open", "-a", "Notion"],
                  ["osascript", "-e", "set volume output volume 20"]]
    assert llm.texts == ["我想用 Slack，用 Notion"]  # 連續未命中合併成一次 LLM 呼叫


def test_pipeline_slow_fast_slow_splits_segments(monkeypatch, sh):
    llm = FakeLLM(by_text={
        "螢幕太亮了": [tc("adjust_screen_brightness", '{"direction":"down","steps":3}')],
        "今天台北會下雨嗎": [tc("web_search_or_open_url", '{"query":"台北天氣"}')],
    }).install(monkeypatch)
    out = r.process_voice_command("螢幕太亮了，靜音，今天台北會下雨嗎")
    assert llm.texts == ["螢幕太亮了", "今天台北會下雨嗎"]  # 被 Fast Path 的『靜音』隔開 → 兩次 LLM
    assert [c[2] if c[0] == "osascript" else c[0] for c in sh] == [
        sh[0][2], "set volume output muted true", "open"]
    assert "key code 145" in sh[0][2]
    assert [s["kind"] for s in out["steps"]] == ["slow", "fast", "slow"]


def test_pipeline_returns_timing(monkeypatch, sh):
    FakeLLM(by_text={"螢幕太亮了": [tc("adjust_screen_brightness", '{"direction":"down","steps":3}')]}).install(monkeypatch)
    out = r.process_voice_command("開 Chrome，螢幕太亮了")
    fast, slow = out["steps"]
    assert (fast["kind"], slow["kind"]) == ("fast", "slow")
    assert "llm_ms" not in fast and 0 <= slow["llm_ms"] <= slow["ms"]
    assert out["total_ms"] >= fast["ms"] + slow["ms"]


# ---------- 去掉模型洩漏的思考內容 ----------


@pytest.mark.parametrize("content,expected", [
    ("畫面上寫「A」。有其他問題嗎？</think>畫面上寫「A」。", "畫面上寫「A」。"),
    ("只有思考</think>", "無法理解指令"),
])
def test_chat_reply_strips_leaked_think(monkeypatch, content, expected):
    from mac_ai.router.actions import chat_reply
    llm_always(monkeypatch, {"content": content})
    ctx = r.ExecContext()
    ctx.utterance = "Python 是什麼"
    chat_reply(ctx)
    assert ctx.replies == [expected]
