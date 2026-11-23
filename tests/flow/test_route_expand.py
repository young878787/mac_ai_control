"""聊天室主動展開（result 事件的 expand）整條路由：看畫面 screen → 模型回覆 chat → 動作數／回覆字數 complex，其餘不帶欄位。
規格見 docs/plans/completed/2026-11-18-chat-window-expand.md §4.1"""
import pytest

import mac_ai.router as r
from mac_ai import config
from mac_ai.router import llm_client, slow_path
from mac_ai.router.actions import ExecContext
from mac_ai.router.pipeline import expand_reason
from support.llm import FakeLLM, forbid_llm, tc
from support.screen import Screen

BRIGHT = "螢幕太亮了眼睛好痛"  # Fast Path 未命中，會交給 Slow Path
DIM = tc("adjust_screen_brightness", {"direction": "down", "steps": 3})


def expand(out):
    return out.get("expand")


# ---------- 單元：判斷順序與門檻 ----------
def ctx_with(calls=(), replies=(), ai_reply=False):
    ctx = ExecContext()
    ctx.calls, ctx.replies, ctx.ai_reply = list(calls), list(replies), ai_reply
    return ctx


def test_expand_reason_priority_and_thresholds():
    ok = lambda name: (name, {}, True)  # noqa: E731
    assert expand_reason(ctx_with()) is None
    assert expand_reason(ctx_with([("read_screen", {}, True)], ["a" * 99], ai_reply=True)) == "screen"
    assert expand_reason(ctx_with([("read_screen", {}, False)])) is None  # 看畫面失敗不算
    assert expand_reason(ctx_with([ok("a"), ok("b")], ai_reply=True)) == "chat"
    assert expand_reason(ctx_with([ok("a")])) is None
    assert expand_reason(ctx_with([ok("a"), ok("b")])) == "complex"
    assert expand_reason(ctx_with([("a", {}, False), ("b", {}, False)])) == "complex"  # 失敗、略過也算執行過的動作
    assert expand_reason(ctx_with([ok("a"), ok("chat_reply")])) is None  # chat_reply 是回覆不是動作
    assert config.EXPAND_REPLY_CHARS == 20 and config.EXPAND_MIN_ACTIONS == 2
    assert expand_reason(ctx_with(replies=["字" * 20])) is None  # 剛好 20 字不展開
    assert expand_reason(ctx_with(replies=["字" * 21])) == "complex"
    assert expand_reason(ctx_with(replies=["字" * 10, " " + "字" * 11 + " "])) == "complex"  # 多段回覆合計、去掉頭尾空白


# ---------- Fast Path ----------
def test_single_fast_action_does_not_expand(sh):
    out = r.process_voice_command("打開小算盤")
    assert expand(out) is None and "expand" not in out


def test_two_actions_in_one_sentence_expand_as_complex(sh):
    assert expand(r.process_voice_command("打開小算盤，音量調到 30")) == "complex"


def test_open_safari_then_search_is_complex(sh, monkeypatch):
    forbid_llm(monkeypatch)
    assert expand(r.process_voice_command("開 Safari 然後搜尋天氣")) == "complex"


def test_many_local_matches_reply_is_long_enough_to_expand(find_env):
    t = 1_700_000_000
    find_env.mdfind_paths = [find_env.touch(f"macai_find_test/驗收報告{c}.pdf", mtime=t - i) for i, c in enumerate("ABC")]
    out = r.process_voice_command("找驗收報告")
    assert expand(out) == "complex" and out["steps"][0]["choices"]["total"] == 4  # 本機 3 筆＋Google 1 筆（聯合搜尋 U9）；清單仍在角色視窗，聊天室只是多一處看


# ---------- 看畫面 ----------
def test_read_screen_expands_as_screen(monkeypatch, tmp_path):
    Screen(monkeypatch, tmp_path)
    assert expand(r.process_voice_command("截圖")) == "screen"


def test_failed_read_screen_does_not_expand(monkeypatch, tmp_path):
    Screen(monkeypatch, tmp_path, vl_reply=llm_client.LLMUnavailable("連不上"))
    out = r.process_voice_command("截圖")
    assert out["steps"][0]["status"] == "llm_unavailable" and expand(out) is None


# ---------- 本機模型 ----------
def test_local_chat_reply_expands_as_chat(monkeypatch, sh):
    FakeLLM(plan={"tool_calls": [tc("chat_reply")]}, chat="哈哈").install(monkeypatch)
    out = r.process_voice_command("講個笑話")
    assert out["replies"] == ["哈哈"] and expand(out) == "chat"


def test_local_short_chat_still_expands_even_if_under_20_chars(monkeypatch, sh):
    FakeLLM(plan={"content": "嗯"}, chat="你好").install(monkeypatch)  # 沒有工具 → 另外觸發 chat call
    assert expand(r.process_voice_command("你好呀")) == "chat"


def test_local_action_plus_chat_is_chat_not_complex(monkeypatch, sh):
    FakeLLM(plan={"tool_calls": [DIM, tc("chat_reply")]}, chat="好").install(monkeypatch)
    assert expand(r.process_voice_command("把螢幕調暗再陪我聊聊")) == "chat"


def test_local_single_tool_does_not_expand(monkeypatch, sh):
    FakeLLM(plan={"tool_calls": [DIM]}).install(monkeypatch)
    assert expand(r.process_voice_command(BRIGHT)) is None


def test_local_two_tools_expand_as_complex(monkeypatch, sh):
    FakeLLM(plan={"tool_calls": [DIM, tc("system_power_action", {"action": "dark_mode_on"})]}).install(monkeypatch)
    out = r.process_voice_command(BRIGHT)
    assert out["steps"][0]["status"] == "ok" and expand(out) == "complex"


def test_llm_unavailable_message_is_long_so_it_expands(monkeypatch, sh):
    FakeLLM(error=True).install(monkeypatch)
    out = r.process_voice_command(BRIGHT)
    assert out["steps"][0]["status"] == "llm_unavailable" and expand(out) == "complex"


# ---------- 不該展開 ----------
def test_no_action_reply_does_not_expand(monkeypatch, sh):
    forbid_llm(monkeypatch)
    out = r.process_voice_command("不要鎖定螢幕")
    assert out["replies"] == [slow_path.NO_ACTION_REPLY] and expand(out) is None  # 12 字


# ---------- 雲端 ----------
def test_cloud_text_reply_expands_as_chat(cloud, sh):
    cloud.reply = {"content": "好的～"}
    assert expand(r.process_voice_command("你好呀")) == "chat"


def test_cloud_action_with_adopted_text_expands_as_chat(cloud, sh):
    cloud.reply = {"tool_calls": [DIM], "content": "好"}
    assert expand(r.process_voice_command(BRIGHT)) == "chat"


def test_cloud_text_dropped_after_rejected_tool_does_not_expand(cloud, sh):
    cloud.reply = {"tool_calls": [tc("not_a_tool")], "content": "已幫你處理好了"}  # 工具被拒絕 → 文字回覆不採用
    out = r.process_voice_command(BRIGHT)
    assert out["steps"][0]["status"] == "rejected" and out["replies"] == [] and expand(out) is None


@pytest.mark.parametrize("text", ["看一下畫面上有什麼"])
def test_cloud_read_screen_expands_as_screen(cloud, monkeypatch, tmp_path, text):
    Screen(monkeypatch, tmp_path)
    cloud.reply = {"tool_calls": [tc("read_screen")]}
    assert expand(r.process_voice_command(text)) in ("screen",)
