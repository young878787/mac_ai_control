"""LLM 這一側（A6）：web_search 依原句導向本機、App 名稱必須查得到、內部工具叫不到。"""
import subprocess

import pytest

import mac_ai.router as r
from mac_ai.router import llm_client


def _tc(name, args):
    return {"type": "function", "function": {"name": name, "arguments": args}}


def llm_returns(monkeypatch, name, args):
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"tool_calls": [_tc(name, args)]})


@pytest.fixture
def xcode(installed_apps):
    installed_apps({"xcode": "Xcode"})


def test_reroute_find_to_app(monkeypatch, xcode):
    llm_returns(monkeypatch, "web_search_or_open_url", '{"query":"Xcode"}')
    meta = {}
    calls, _, rejected = r.plan_slow_path("幫我把那個 Xcode 找出來", meta)
    assert rejected == [] and calls == [("control_mac_app", {"app_name": "Xcode", "action": "open"})]
    assert meta["reroutes"] == ["[Domain 導向] 搜尋「Xcode」→ app"] and meta["intent"]["domain"] == "app"


def test_reroute_find_to_folder_and_file(monkeypatch, xcode):
    llm_returns(monkeypatch, "web_search_or_open_url", '{"query":"下載"}')
    calls, _, _ = r.plan_slow_path("找一下下載資料夾")
    assert calls == [("open_folder", {"path_key": "Downloads"})]
    llm_returns(monkeypatch, "web_search_or_open_url", '{"query":"履歷.pdf"}')
    calls, _, _ = r.plan_slow_path("找一下履歷.pdf")
    assert calls == [("find_file", {"name": "履歷", "kinds": ["pdf"], "filename": "履歷.pdf", "local_only": True, "action": "reveal"})]


@pytest.mark.parametrize("utterance,query", [
    ("搜尋 Xcode", "Xcode"),  # 『搜尋／查』沒有範圍詞：維持網頁搜尋
    ("查一下 Xcode", "Xcode"),
    ("幫我找一下台北天氣", "台北天氣"),  # 即時資訊維持網頁
    ("網路上找 Xcode", "Xcode"),
    ("不要找 Xcode", "Xcode"),  # 否定句不導向（維持 LLM 原本的決定）
    ("找 Xcode 嗎", "Xcode"),  # 提問不導向
    ("找一下 Xcode", "完全不同的東西"),  # 受詞和 LLM 的 query 對不上：不導向
])
def test_web_search_stays_web(monkeypatch, xcode, utterance, query):
    llm_returns(monkeypatch, "web_search_or_open_url", '{"query":"%s"}' % query)
    calls, _, rejected = r.plan_slow_path(utterance)
    assert rejected == [] and calls == [("web_search_or_open_url", {"query": query})]


def test_reroute_is_logged_when_executed(monkeypatch, sh, xcode):
    llm_returns(monkeypatch, "web_search_or_open_url", '{"query":"Xcode"}')
    step = {}
    logs = r.execute_slow_path_llm("幫我把那個 Xcode 找出來", step)
    assert sh == [["open", "-a", "Xcode"]]
    assert logs[0] == "[Domain 導向] 搜尋「Xcode」→ app" and "control_mac_app" in logs[1]
    assert step["intent"]["domain"] == "app"


def test_llm_app_not_installed_is_rejected_and_opens_nothing(monkeypatch, sh):
    llm_returns(monkeypatch, "control_mac_app", '{"app_name":"Photoshop","action":"open"}')
    out = r.process_voice_command("打開 Photoshop")
    assert sh == []  # 沒有任何 open -a
    (step,) = out["steps"]
    assert step["kind"] == "slow" and step["status"] == "rejected"
    assert "找不到 App：Photoshop（沒有安裝）" in step["logs"][0]


def test_llm_installed_app_is_opened(monkeypatch, sh, installed_apps):
    installed_apps({"chatgpt": "ChatGPT"})
    llm_returns(monkeypatch, "control_mac_app", '{"app_name":"chatgpt","action":"open"}')
    r.process_voice_command("我想用 ChatGPT")
    assert sh == [["open", "-a", "ChatGPT"]]


@pytest.mark.parametrize("name,args", [
    ("find_file", '{"name":"履歷"}'), ("open_folder", '{"path_key":"Home"}'), ("pick_choice", '{"n":1}'),
])
def test_llm_cannot_call_internal_tools(monkeypatch, sh, name, args):
    llm_returns(monkeypatch, name, args)
    out = r.process_voice_command("我想用電腦做點事")
    assert sh == [] and out["steps"][0]["status"] == "rejected"
    assert f"未知工具：{name}" in out["steps"][0]["logs"][0]



# 有本機證據的句子：就算 0 筆也不呼叫 LLM（只回『本機沒有』）；網頁類由 Fast Path 直接搜尋
NO_LLM_CASES = [
    ("欸你幫我在電腦裡面找那個叫做履歷的檔案", "local"),
    ("找 report.pdf", "local"),
    ("找昨天下載的 PDF", "local"),
    ("搜尋一下 Xcode 是什麼", "web"),
    ("找一下台北天氣", "web"),
]


@pytest.mark.parametrize("utterance,kind", NO_LLM_CASES, ids=[c[0] for c in NO_LLM_CASES])
def test_requests_with_clear_scope_never_call_the_llm(monkeypatch, find_env, utterance, kind):
    def no_llm(*a, **kw):
        raise AssertionError("不該呼叫 LLM")

    monkeypatch.setattr(llm_client, "_chat", no_llm)
    (find_env.home / "Downloads").mkdir()
    out = r.process_voice_command(utterance)
    (step,) = out["steps"]
    assert step["kind"] == "fast" and "llm_ms" not in step and "escalated" not in step
    google = any("google.com/search" in " ".join(c) for c in find_env.opens())
    if kind == "local":
        assert find_env.mdfinds() and not google and "本機沒有" in step["logs"][0]
    else:
        assert google and not find_env.mdfinds()


# ---------- 閉環：本機 0 筆、沒有本機證據 → 轉交模型判斷 → 開網頁／確認找不到 ----------
class FakeLLM:
    """依序回傳預先排好的回覆；記錄每次呼叫的 messages。拋 LLMUnavailable 用 error=True"""

    def __init__(self, *replies, error=False):
        self.replies, self.error, self.calls = list(replies), error, []

    def __call__(self, messages, **kw):
        self.calls.append(messages)
        if self.error:
            raise llm_client.LLMUnavailable("連不上")
        return self.replies.pop(0) if self.replies else {"content": "好的"}


def test_escalated_query_can_become_a_web_search(monkeypatch, find_env):
    llm = FakeLLM({"tool_calls": [_tc("web_search_or_open_url", '{"query":"python 教學"}')]})
    monkeypatch.setattr(llm_client, "_chat", llm)
    out = r.process_voice_command("找 python 教學")
    (step,) = out["steps"]
    assert len(find_env.mdfinds()) == 1  # 先搜本機一次；模型的搜尋不會再被導回本機（不繞圈）
    assert any("google.com/search" in " ".join(c) for c in find_env.opens())
    assert step["escalated"] == {"label": "python 教學", "scope": "家目錄，不含 ~/Library"}
    assert step["status"] == "ok" and step["llm_ms"] >= 0
    assert step["logs"][0].startswith("🔎 本機沒有符合「python 教學」")
    system, user = llm.calls[0][0]["content"], llm.calls[0][-1]["content"]
    assert "本機沒有名為" not in system  # 放在使用者訊息：system＋工具定義的前綴快取才不會失效（實測差 2 秒）
    assert user == "找 python 教學\n（本機沒有名為「python 教學」的檔案。）"


def test_escalated_query_without_any_action_confirms_not_found(monkeypatch, find_env):
    llm = FakeLLM({"content": "好"})  # 模型沒有選任何工具
    monkeypatch.setattr(llm_client, "_chat", llm)
    out = r.process_voice_command("找履歷")
    (step,) = out["steps"]
    assert len(llm.calls) == 1  # 不另外觸發 chat call 閒聊
    assert out["replies"] == ["⚠️ 找不到「履歷」：本機沒有符合的檔案（範圍：家目錄，不含 ~/Library）；AI 沒有選擇上網搜尋。要上網查請說「搜尋 履歷」"]
    assert step["status"] == "exec_failed" and find_env.opens() == []


def test_escalation_when_llm_is_unavailable_still_answers(monkeypatch, find_env):
    monkeypatch.setattr(llm_client, "_chat", FakeLLM(error=True))
    out = r.process_voice_command("找 python 教學")
    (step,) = out["steps"]
    assert step["status"] == "llm_unavailable" and find_env.opens() == []
    assert out["replies"][0].startswith("⚠️ 找不到「python 教學」：本機沒有符合的檔案") and "AI 目前無法判斷要不要上網找" in out["replies"][0] and out["replies"][0].endswith("要上網查請說「搜尋 python 教學」")


def test_escalation_chat_reply_is_told_nothing_was_found(monkeypatch, find_env):
    llm = FakeLLM({"tool_calls": [_tc("chat_reply", "{}")]}, {"content": "電腦裡沒有這份檔案喔"})
    monkeypatch.setattr(llm_client, "_chat", llm)
    out = r.process_voice_command("找履歷")
    assert out["replies"] == ["電腦裡沒有這份檔案喔"]
    chat_system = llm.calls[1][0]["content"]
    assert "本機沒有名為「履歷」的檔案" in chat_system and "不要說已經找到" in chat_system


def test_escalation_with_rejected_tool_still_confirms_not_found(monkeypatch, find_env):
    monkeypatch.setattr(llm_client, "_chat", FakeLLM({"tool_calls": [_tc("control_mac_app", '{"app_name":"Photoshop","action":"open"}')]}))
    out = r.process_voice_command("找履歷")
    (step,) = out["steps"]
    assert any("找不到 App：Photoshop" in log for log in step["logs"])
    assert out["replies"][-1].startswith("⚠️ 找不到「履歷」") and find_env.opens() == []


def test_found_locally_does_not_call_the_llm(monkeypatch, find_env):
    def no_llm(*a, **kw):
        raise AssertionError("本機找得到就不該呼叫 LLM")

    monkeypatch.setattr(llm_client, "_chat", no_llm)
    path = find_env.touch("docs/履歷.pdf")
    find_env.mdfind_paths = [path]
    out = r.process_voice_command("找履歷")
    assert find_env.opens() == [["open", "-R", str(path)]] and "escalated" not in out["steps"][0]


def test_search_failure_is_reported_not_escalated(monkeypatch, find_env):
    def no_llm(*a, **kw):
        raise AssertionError("搜尋失敗不該轉交 LLM")

    monkeypatch.setattr(llm_client, "_chat", no_llm)
    find_env.mdfind_error = subprocess.TimeoutExpired("mdfind", 3)
    out = r.process_voice_command("找履歷")
    assert out["steps"][0]["logs"][0].startswith("❌ 檔案搜尋失敗") and out["steps"][0]["status"] == "exec_failed"


@pytest.mark.parametrize("utterance", ["找到了", "找不到我的鑰匙", "找最近的電影", "找影片教學"])
def test_exception_sentences_go_straight_to_the_model(monkeypatch, find_env, utterance):
    llm = FakeLLM({"content": "x"}, {"content": "這是回覆"})
    monkeypatch.setattr(llm_client, "_chat", llm)
    out = r.process_voice_command(utterance)
    (step,) = out["steps"]
    assert step["kind"] == "slow" and find_env.mdfinds() == []  # 不搜檔案
    assert out["replies"] == ["這是回覆"]


def test_escalation_keeps_spoken_order_with_other_clauses(monkeypatch, find_env):
    monkeypatch.setattr(llm_client, "_chat", FakeLLM({"tool_calls": [_tc("web_search_or_open_url", '{"query":"python 教學"}')]}))
    out = r.process_voice_command("找 python 教學，靜音")
    assert [s["kind"] for s in out["steps"]] == ["fast", "fast"]
    kinds = [c[0] for c in find_env.calls]
    assert kinds == ["mdfind", "open", "osascript"]  # 搜本機 → 轉交後開網頁 → 靜音，照說話順序


def test_model_web_decision_without_local_evidence_is_not_rerouted_back_to_files(monkeypatch):
    """沒有本機證據的『找 X』：模型判斷要上網就尊重它（本機已由 Fast Path 先找過），不再被導回檔案搜尋"""
    llm_returns(monkeypatch, "web_search_or_open_url", '{"query":"swift 入門教材"}')
    calls, _, _ = r.plan_slow_path("找 swift 入門教材")
    assert calls == [("web_search_or_open_url", {"query": "swift 入門教材"})]


def test_the_suggested_next_step_really_searches_the_web(monkeypatch, find_env):
    """保底訊息說『要上網查請說「搜尋 履歷」』：照著說就真的開網頁搜尋，不再問模型"""
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不該呼叫 LLM")))
    r.process_voice_command("搜尋 履歷")
    assert [c for c in find_env.opens()] == [["open", "https://www.google.com/search?q=%E5%B1%A5%E6%AD%B7"]]
