"""瀏覽器分頁整條路由：分頁片語走 Fast Path、瀏覽器選擇、失敗回報、LLM 看不到也叫不動 browser_tab"""
import subprocess

import pytest

import mac_ai.router as r
from mac_ai.router import browser_tools
from support.expect import APP, CHROME, SAFARI, fast_matches
from support.llm import forbid_llm, llm_always, tc


# ---------- 4. 瀏覽器分頁 ----------
def NEW_TAB(browser):
    return browser_tools._TAB_SCRIPTS[browser]["new"]


def TAB(browser, action):
    return ["osascript", "-e", browser_tools._TAB_SCRIPTS[browser][action]]


QUERY = ["osascript", "-e", browser_tools._FRONT_AND_RUNNING_SCRIPT]


class Browsers:
    """假的 macOS：System Events 回報最前景 App 與執行中的 App；其餘 osascript 一律成功並記錄"""

    def __init__(self, monkeypatch, front="Safari", running=("Safari",), fail=()):
        self.calls, self.front, self.running, self.fail = [], front, list(running), list(fail)
        monkeypatch.setattr(subprocess, "run", self.run)

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd in self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", "execution error: Can't get window 1. (-1728)")
        if cmd == QUERY:
            return subprocess.CompletedProcess(cmd, 0, f"{self.front}\n{','.join(self.running)}\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


TAB_PHRASES = [
    ("幫我開一個新的分頁", "new"),  # 實測：原本被 LLM 亂呼叫 open_project
    ("幫我再開一個新的分頁", "new"),  # 實測：原本被 LLM 拿去 Google 搜尋「如何開新分頁」
    ("開新分頁", "new"), ("打開新的分頁", "new"), ("開啟一個新分頁", "new"), ("新增分頁", "new"),
    ("新增一個新的分頁", "new"), ("開一個分頁", "new"), ("新分頁", "new"), ("開新標籤頁", "new"),
    ("關掉這個分頁", "close"), ("關閉分頁", "close"), ("把這個分頁關掉", "close"), ("關閉目前的分頁", "close"),
    ("重新整理", "reload"), ("重新整理這個分頁", "reload"), ("重新載入頁面", "reload"), ("刷新", "reload"),
    ("下一個分頁", "next"), ("切換到下一個分頁", "next"), ("切到下個分頁", "next"),
    ("上一個分頁", "previous"), ("切換到上一個分頁", "previous"), ("跳到上個分頁", "previous"),
]


@pytest.mark.parametrize("text, action", TAB_PHRASES, ids=[t[0] for t in TAB_PHRASES])
def test_tab_phrases_run_browser_tab_without_llm(monkeypatch, text, action):
    forbid_llm(monkeypatch)
    env = Browsers(monkeypatch, front="Safari", running=["Safari", "Finder"])
    out = r.process_voice_command(text)
    assert env.calls == [QUERY, TAB(SAFARI, action)]
    assert [st["kind"] for st in out["steps"]] == ["fast"] and out["steps"][0]["status"] == "ok"
    assert out["steps"][0]["logs"][0].startswith("✅ Safari")


@pytest.mark.parametrize("text", [
    "不要開新分頁", "剛剛開了新分頁", "怎麼開新分頁", "分頁", "新增", "關掉所有分頁", "關掉其他分頁", "開會",
    "重新整理電腦",  # 『上一頁』『下一頁』自 2026-11-14 起是常用功能（瀏覽器後退／前進），見 test_context_offline
])
def test_tab_negative_phrases_do_not_touch_browser(monkeypatch, text):
    assert all(m is None for m in fast_matches(text)), text
    env = Browsers(monkeypatch)
    llm_always(monkeypatch, {"content": "好"})
    r.process_voice_command(text)
    assert env.calls == []


def test_tab_browser_choice_front_then_running(monkeypatch):
    env = Browsers(monkeypatch, front="Google Chrome", running=["Safari", "Google Chrome"])
    assert r.browser_tab("new").startswith("✅ Google Chrome")
    assert env.calls[-1] == TAB("Google Chrome", "new")
    env = Browsers(monkeypatch, front="Finder", running=["Finder", "Google Chrome", "Safari"])  # 前景不是瀏覽器 → 依序挑 Safari
    assert r.browser_tab("close").startswith("✅ Safari") and env.calls[-1] == TAB(SAFARI, "close")
    env = Browsers(monkeypatch, front="Finder", running=["Finder", "Google Chrome"])
    assert r.browser_tab("reload").startswith("✅ Google Chrome") and env.calls[-1] == TAB("Google Chrome", "reload")


def test_tab_without_browser_reports_failure(monkeypatch):
    env = Browsers(monkeypatch, front="Finder", running=["Finder", "Notes"])
    res = r.browser_tab("new")
    assert res.startswith("❌ 找不到可操作的瀏覽器") and env.calls == [QUERY]
    out = r.process_voice_command("開新分頁")
    assert out["steps"][0]["status"] == "exec_failed"


def test_tab_hint_from_this_command_beats_frontmost(monkeypatch):
    """『開 Chrome 然後開新分頁』：剛開的瀏覽器優先，不必（也不該）去問最前景是誰"""
    forbid_llm(monkeypatch)
    env = Browsers(monkeypatch, front="Safari", running=["Safari", "Google Chrome"])
    out = r.process_voice_command("開 Chrome，然後開新分頁")
    assert env.calls == [APP(CHROME), TAB("Google Chrome", "new")]
    assert all(st["status"] == "ok" for st in out["steps"])


def test_tab_skipped_when_browser_failed_to_open(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(
        cmd, 1 if cmd[:2] == ["open", "-a"] else 0, "", "boom"))
    out = r.process_voice_command("開 Chrome，然後開新分頁")
    assert calls == [APP(CHROME)]
    assert out["steps"][1]["logs"] == ["⏭️ 略過 browser_tab：前一步失敗"]


def test_tab_script_failure_reports_stderr(monkeypatch):
    Browsers(monkeypatch, fail=[TAB(SAFARI, "close")])
    res = r.browser_tab("close")
    assert res.startswith("❌ 分頁操作失敗：Safari") and "Can't get window 1" in res


def test_tab_query_failure_reports_reason(monkeypatch):
    Browsers(monkeypatch, fail=[QUERY])
    assert r.browser_tab("new").startswith("❌ 找不到可操作的瀏覽器") and "Can't get window 1" in r.browser_tab("new")


def test_tab_unknown_action_is_rejected(monkeypatch):
    env = Browsers(monkeypatch)
    assert r.browser_tab("explode").startswith("⚠️") and env.calls == []


# ---------- 工具 schema 與 LLM 路徑 ----------


def test_llm_planned_browser_tab_is_rejected(monkeypatch):
    env = Browsers(monkeypatch, front="Safari", running=["Safari"])
    llm_always(monkeypatch, {"tool_calls": [tc("browser_tab", '{"action":"new"}')]})
    out = r.process_voice_command("幫我弄一個空白的頁籤出來")  # Fast 規則外的說法，交給 LLM
    assert env.calls == []
    assert out["steps"][0]["status"] == "rejected" and "未知工具：browser_tab" in out["steps"][0]["logs"][0]
