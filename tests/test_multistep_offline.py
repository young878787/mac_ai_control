"""S1～S8 的離線驗收：執行結果如實回報（S1）、LLM 保護（B5）、多段意圖（B6）、計時結構（B7）、chat call（B8）。
subprocess、截圖、LLM 全部是假物件；LLM 預設為離線（conftest）。"""
import base64
import io
import json
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import REAL_URLOPEN

import mac_ai.router as r
from mac_ai import config
from mac_ai.router import actions, lexicon, llm_client, slow_path, tools
from test_fast_offline import APP, CHROME, MUTE, SEARCH, VOL, VSCODE
from test_router_offline import real_run


def _tc(name, args):
    return {"type": "function", "function": {"name": name, "arguments": args}}


def _failing_run(failing, calls, stderr="boom"):
    """假的 subprocess.run：指令在 failing（list 的 list）內就回傳結束碼 1"""
    def fake(cmd, **kw):
        calls.append(cmd)
        if cmd in failing:
            return subprocess.CompletedProcess(cmd, 1, "", stderr)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return fake


# ---------- S1：執行結果如實回報 ----------
def test_executors_report_failure_with_stderr(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(
        cmd, 1, "", "Unable to find application named 'NoSuchApp'"))
    res = r.control_mac_app("NoSuchApp", "open")
    assert res.startswith("❌") and "Unable to find application" in res
    assert r.control_mac_app("Notes", "quit").startswith("❌")
    assert r.set_system_volume(level=30).startswith("❌")
    assert r.set_system_volume(mute=True).startswith("❌")
    assert r.adjust_screen_brightness("up", 2).startswith("❌")
    assert r.web_search_or_open_url("天氣").startswith("❌")
    assert r.system_power_action("lock_screen").startswith("❌")
    assert r.system_power_action("dark_mode_on").startswith("❌")
    assert r.control_mac_app("Notes", "delete").startswith("⚠️")


def test_successful_executors_still_report_ok(sh):
    assert r.control_mac_app("Notes").startswith("✅")
    assert r.system_power_action("dark_mode_off") == "✅ 已關閉深色模式（淺色）"


def test_pipeline_marks_failed_step(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _failing_run([["open", "-a", "Spotify"]], calls))
    out = r.process_voice_command("開 Spotify，把音量調到30")
    assert [s["status"] for s in out["steps"]] == ["exec_failed", "ok"]
    assert out["steps"][0]["logs"][0].startswith("❌")
    assert calls[-1] == VOL(30)  # 失敗不影響後面不相依的步驟


# ---------- S3：_chat 的 timings 與 tools 開關 ----------
def _fake_urlopen(captured, payload):
    def fake(req, timeout):
        captured.update(json.loads(req.data))
        captured["timeout"] = timeout
        return io.BytesIO(json.dumps(payload).encode())
    return fake


def test_chat_fills_meta_from_server_timings(monkeypatch):
    captured, meta = {}, {}
    payload = {"choices": [{"message": {"content": "ok"}}],
               "timings": {"prompt_n": 600, "cache_n": 500, "predicted_n": 20, "prompt_ms": 120.5, "predicted_ms": 480.0}}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(captured, payload))
    assert llm_client._chat([{"role": "user", "content": "hi"}], meta=meta) == {"content": "ok"}
    assert meta == {"prefill_ms": 120.5, "decode_ms": 480.0, "prompt_n": 600, "cache_n": 500, "predicted_n": 20}


def test_chat_without_tools_omits_them_and_sets_max_tokens(monkeypatch):
    captured = {}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(captured, {"choices": [{"message": {"content": "ok"}}]}))
    llm_client._chat([{"role": "user", "content": "hi"}], tools=False, max_tokens=128, timeout=7)
    assert "tools" not in captured and captured["max_tokens"] == 128 and captured["timeout"] == 7
    assert captured["enable_thinking"] is False


def test_default_plan_timeout_is_10s(monkeypatch):
    captured = {}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(captured, {"choices": [{"message": {}}]}))
    llm_client._llm([{"role": "user", "content": "x"}], timeout=config.PLAN_TIMEOUT_S)
    assert captured["timeout"] == 10.0


# ---------- B5：LLM 保護 ----------
def test_llm_down_other_steps_still_run(sh):
    t = time.perf_counter()
    out = r.process_voice_command("開 Chrome，幫我整理一下桌面，把音量調到30")
    assert sh == [APP(CHROME), VOL(30)]
    assert [s["status"] for s in out["steps"]] == ["ok", "llm_unavailable", "ok"]
    assert any("這句沒有處理" in m and "幫我整理一下桌面" in m for m in out["replies"])
    assert (time.perf_counter() - t) * 1000 < 100


def test_llm_timeout_ends_within_budget(monkeypatch, sh):
    """連得上但不回應的 server：逾時設 0.5 s，該段必須在 0.7 s 內結束"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        monkeypatch.setattr(llm_client, "MLX_VLM_URL", f"http://127.0.0.1:{srv.getsockname()[1]}")
        monkeypatch.setattr(slow_path, "PLAN_TIMEOUT_S", 0.5)
        monkeypatch.setattr(urllib.request, "urlopen", REAL_URLOPEN)
        out = r.process_voice_command("幫我整理一下桌面")
    finally:
        srv.close()
    step = out["steps"][0]
    assert step["status"] == "llm_unavailable"
    assert 400 <= step["ms"] < 700, step["ms"]


def test_circuit_breaker_skips_second_llm_segment(monkeypatch, sh):
    calls = []

    def down(*a, **kw):
        calls.append(1)
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(llm_client, "_chat", down)
    out = r.process_voice_command("幫我整理桌面，靜音，幫我整理資料夾")
    assert [s["kind"] for s in out["steps"]] == ["slow", "fast", "slow"]
    assert len(calls) == 1  # 第二個 S 段直接熔斷，不再等待
    assert [s["status"] for s in out["steps"]] == ["llm_unavailable", "ok", "llm_unavailable"]
    assert sh == [MUTE(True)]


def test_breaker_reopens_after_cooldown(monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **kw: (_ for _ in ()).throw(urllib.error.URLError("x")))
    with pytest.raises(r.LLMUnavailable):
        llm_client._llm([], timeout=1)
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **kw: {"content": "ok"})
    with pytest.raises(r.LLMUnavailable, match="熔斷"):
        llm_client._llm([], timeout=1)
    llm_client._llm_down_until = time.monotonic() - 1  # 冷卻結束
    assert llm_client._llm([], timeout=1) == {"content": "ok"}


@pytest.mark.parametrize("body", [b'{"nochoices": 1}', b"not json", b'{"choices": []}'])
def test_malformed_response_is_llm_error_and_does_not_trip_breaker(monkeypatch, sh, body):
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: calls.append(1) or io.BytesIO(body))
    out = r.process_voice_command("幫我整理桌面，靜音，幫我整理資料夾")
    assert [s["status"] for s in out["steps"]] == ["llm_error", "ok", "llm_error"]
    assert len(calls) == 2  # 格式錯誤不熔斷，第二段仍會嘗試
    assert sh == [MUTE(True)]


def test_http_500_is_llm_error(monkeypatch, sh):
    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "err", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = r.process_voice_command("幫我整理桌面")
    assert out["steps"][0]["status"] == "llm_error"
    assert any("HTTP 500" in m for m in out["replies"])


@pytest.fixture
def ambiguous_apps(monkeypatch):
    monkeypatch.setitem(r.MAC_APP_ALIASES, "slackapp1", "Slack One")
    monkeypatch.setitem(r.MAC_APP_ALIASES, "slackapp2", "Slack Two")


def test_fallback_runs_fast_candidate_when_llm_down(sh, ambiguous_apps):
    out = r.process_voice_command("開 slackapp，把音量調到30")
    assert sh == [APP("Slack One"), VOL(30)]
    assert out["steps"][0]["status"] == "fallback"
    assert "slackapp→Slack One" in out["steps"][0]["logs"][0]


def test_fallback_never_runs_guarded_sentence(sh, ambiguous_apps):
    out = r.process_voice_command("不要開 slackapp")
    assert sh == []
    assert out["steps"][0]["status"] == "llm_unavailable"
    assert any("這句沒有處理" in m for m in out["replies"])


def test_candidate_not_used_when_llm_is_up(monkeypatch, sh, ambiguous_apps):
    """LLM 可用時，歧義句交給 LLM 判斷，不走 Fast 候選"""
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"tool_calls": [
        _tc("control_mac_app", '{"app_name":"Slack Two","action":"open"}')]})
    out = r.process_voice_command("開 slackapp")
    assert sh == [APP("Slack Two")] and out["steps"][0]["kind"] == "slow"


def test_warmup_failure_does_not_raise(capsys):
    assert r.warmup_llm() is None
    assert "暖機失敗" in capsys.readouterr().out


def test_warmup_success_returns_seconds(monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **kw: {"content": "hi"})
    assert r.warmup_llm() >= 0


# ---------- B6：多段意圖 ----------
def test_open_editor_then_project(sh, projects):
    r.process_voice_command("開vscode，開mac_ai_control專案")
    assert sh == [APP(VSCODE), ["open", "-a", VSCODE, str(projects / "mac_ai_control")]]


def test_project_uses_the_editor_opened_earlier(sh, projects):
    r.process_voice_command("開 cursor，開 mac_ai_control 專案")
    assert sh == [APP("Cursor"), ["open", "-a", "Cursor", str(projects / "mac_ai_control")]]


def test_unknown_project_is_reported_not_guessed(sh, projects):
    out = r.process_voice_command("開 nosuchproj 專案")
    assert sh == []
    assert out["steps"][0]["status"] == "exec_failed"
    assert "找不到專案" in out["steps"][0]["logs"][0] and "mac_ai_control" in out["steps"][0]["logs"][0]


def test_validate_open_project_whitelist(projects):
    assert r.validate_tool_call("open_project", {"project_name": "/etc"})[1]
    assert r.validate_tool_call("open_project", {"project_name": "../../etc"})[1]
    assert r.validate_tool_call("open_project", {"project_name": "mac_ai_control", "editor": "Chrome"})[1]
    clean, err = r.validate_tool_call("open_project", {"project_name": "語音助手", "editor": "cursor"})
    assert err is None and clean == {"project_name": "mac_ai_control", "editor": "Cursor"}
    assert r.validate_tool_call("open_project", {})[1]


def test_open_project_missing_path(sh, monkeypatch, tmp_path):
    monkeypatch.setitem(r.LEXICON, "projects", [{"name": "ghost", "aliases": [], "path": str(tmp_path / "ghost")}])
    monkeypatch.setattr(lexicon, "PROJECT_ROOTS", [])
    assert r.open_project("ghost").startswith("❌ 專案路徑不存在")
    assert sh == []


class Screen:
    """假的螢幕環境：截圖與縮圖會真的寫出檔案；Chrome 的 loading 依序回傳 loading_seq"""

    def __init__(self, monkeypatch, tmp_path, loading_seq=("false",), vl_reply="天氣：晴，氣溫 25 度"):
        self.calls, self.sleeps, self.vl_messages = [], [], []
        self.loading_seq = list(loading_seq)
        self.vl_reply = vl_reply
        monkeypatch.setattr(subprocess, "run", self.run)
        monkeypatch.setattr(time, "sleep", self.sleeps.append)
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        monkeypatch.setattr(llm_client, "_chat", self.chat)

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if cmd[0] == "screencapture":
            Path(cmd[-1]).write_bytes(b"BIG")
        elif cmd[0] == "sips":
            Path(cmd[cmd.index("--out") + 1]).write_bytes(b"SMALL")
        elif cmd[0] == "osascript" and "loading of active tab" in cmd[2]:
            out = self.loading_seq.pop(0) if len(self.loading_seq) > 1 else self.loading_seq[0]
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def chat(self, messages, **kw):
        self.vl_messages.append((messages, kw))
        if isinstance(self.vl_reply, Exception):
            raise self.vl_reply
        return {"content": self.vl_reply}

    @property
    def names(self):
        return [c[0] for c in self.calls]


@pytest.mark.parametrize("text", [
    "開google，搜尋台北天氣，辨識這頁",
    "開 google 搜尋台北天氣 然後辨識這頁",
])
def test_browse_search_then_read_screen(monkeypatch, tmp_path, text):
    s = Screen(monkeypatch, tmp_path, loading_seq=("true", "true", "false"))
    out = r.process_voice_command(text)
    assert s.calls[:2] == [APP(CHROME), SEARCH("台北天氣", CHROME)]
    assert s.names == ["open", "open", "osascript", "osascript", "osascript", "screencapture", "sips"]  # 輪詢 3 次後才截圖
    assert s.calls[5][:3] == ["screencapture", "-x", "-m"]
    assert s.calls[6][:3] == ["sips", "-Z", "1280"]
    # VL 請求：不帶 tools、max_tokens 256、含縮圖的 data URI
    (messages, kw), = s.vl_messages
    assert kw["tools"] is False and kw["max_tokens"] == 256 and kw["timeout"] == config.VISION_TIMEOUT_S
    image = messages[-1]["content"][0]["image_url"]["url"]
    assert image == "data:image/png;base64," + base64.b64encode(b"SMALL").decode()
    assert out["replies"] == ["天氣：晴，氣溫 25 度"]
    assert all(st["status"] == "ok" for st in out["steps"])
    assert list(tmp_path.glob("macvoice_*")) == []  # 暫存檔已刪除
    last = out["steps"][-1]
    assert {"wait_ms", "capture_ms", "resize_ms", "vl_ms"} <= set(last)


def test_read_screen_waits_fixed_time_for_non_chrome(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path)
    r.process_voice_command("開 Safari，搜尋台北天氣，辨識這頁")
    assert "osascript" not in s.names  # Safari 無法查載入狀態
    assert sum(s.sleeps) >= actions.PAGE_FIXED_WAIT_S - 0.5


def test_read_screen_without_page_does_not_wait(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path)
    out = r.process_voice_command("辨識這頁")
    assert s.names == ["screencapture", "sips"] and s.sleeps == []
    assert out["replies"] == ["天氣：晴，氣溫 25 度"]


def test_read_screen_cleans_tmp_when_vl_fails(monkeypatch, tmp_path):
    Screen(monkeypatch, tmp_path, vl_reply=urllib.error.URLError("down"))
    out = r.process_voice_command("辨識這頁")
    assert out["steps"][0]["status"] == "llm_unavailable"
    assert out["replies"] == []
    assert list(tmp_path.glob("macvoice_*")) == []


def test_read_screen_reports_capture_failure(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1 if cmd[0] == "screencapture" else 0, "", "no permission"))
    out = r.process_voice_command("辨識這頁")
    assert out["steps"][0]["status"] == "exec_failed"
    assert "螢幕錄製" in out["steps"][0]["logs"][0] and s.vl_messages == []
    assert list(tmp_path.glob("macvoice_*")) == []


def test_read_screen_traditionalizes_reply(monkeypatch, tmp_path):
    Screen(monkeypatch, tmp_path, vl_reply="这个网页在显示天气")
    assert r.process_voice_command("辨識這頁")["replies"] == ["這個網頁在顯示天氣"]


def test_failed_browser_skips_dependent_steps(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _failing_run([APP(CHROME)], calls))
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    out = r.process_voice_command("開google，搜尋台北天氣，辨識這頁")
    assert [s["status"] for s in out["steps"]] == ["exec_failed", "skipped", "skipped"]
    assert calls == [APP(CHROME)]  # 搜尋、截圖都沒有執行


def test_failed_browser_does_not_block_unrelated_steps(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _failing_run([APP(CHROME)], calls))
    out = r.process_voice_command("開 Chrome，把音量調到30，靜音")
    assert [s["status"] for s in out["steps"]] == ["exec_failed", "ok", "ok"]
    assert calls[1:] == [VOL(30), MUTE(True)]


def test_failed_editor_skips_project(monkeypatch, projects):
    calls = []
    monkeypatch.setattr(subprocess, "run", _failing_run([APP(VSCODE)], calls))
    out = r.process_voice_command("開vscode，開mac_ai_control專案")
    assert [s["status"] for s in out["steps"]] == ["exec_failed", "skipped"]
    assert calls == [APP(VSCODE)]


def test_llm_search_uses_browser_opened_earlier(monkeypatch, sh):
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"tool_calls": [
        _tc("web_search_or_open_url", '{"query":"台北天氣"}')]})
    r.process_voice_command("開 Safari，幫我找一下台北天氣")
    assert sh == [APP("Safari"), SEARCH("台北天氣", "Safari")]


def test_llm_cannot_pass_browser_argument():
    assert r.validate_tool_call("web_search_or_open_url", {"query": "x", "browser": "Safari"})[1]


# ---------- B7：計時結構 ----------
def test_timing_structure(monkeypatch, sh):
    def fake(messages, **kw):
        kw["meta"].update(prefill_ms=100.0, decode_ms=50.0, prompt_n=600, cache_n=500, predicted_n=20)
        return {"tool_calls": [_tc("adjust_screen_brightness", '{"direction":"down","steps":3}'),
                               _tc("web_search_or_open_url", '{"query":"台北天氣"}')]}

    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("開 Chrome，螢幕太亮了，還有幫我找台北天氣，靜音")
    assert set(out) == {"stages", "steps", "replies", "total_ms"}
    assert set(out["stages"]) == {"normalize_ms", "split_ms", "match_ms"}
    assert all(v >= 0 for v in out["stages"].values())
    assert [s["kind"] for s in out["steps"]] == ["fast", "slow", "fast"]
    assert all(s["status"] == "ok" and s["ms"] >= 0 for s in out["steps"])
    fast, slow, _ = out["steps"]
    assert fast["match"] == {"confidence": "exact", "note": ""} and len(fast["exec_ms"]) == 1
    assert "llm" not in fast and "llm_ms" not in fast
    assert slow["llm"]["prefill_ms"] == 100.0 and slow["llm"]["cache_n"] == 500
    assert slow["llm"]["overhead_ms"] == pytest.approx(max(0.0, slow["llm_ms"] - 150.0))  # llm_ms − prefill − decode，不為負
    assert slow["validate_ms"] >= 0 and len(slow["exec_ms"]) == 2
    assert 0 <= slow["llm_ms"] <= slow["ms"]
    assert out["total_ms"] >= sum(s["ms"] for s in out["steps"])


def test_compound_match_records_one_exec_time_per_action(sh):
    out = r.process_voice_command("開 google 搜尋台北天氣")
    assert len(out["steps"]) == 1 and len(out["steps"][0]["exec_ms"]) == 2


PERF_CORPUS = [
    "開 Chrome", "打開chrome吧", "請幫我把 Chrome 打開", "音量調到 五十", "把螢幕調暗一點", "開 chorme",
    "開 google 搜尋台北天氣", "開vscode，開mac_ai_control專案", "我昨天打開 Chrome", "今天心情不錯",
    "幫我打开谷哥浏览器，然后把声音调到五十", "辨識這頁",
]


def test_front_stages_are_fast():
    """normalize＋split＋match（Fast Path 的決策時間）：12 句 × 20 次 = 240 個樣本，p95 < 5 ms"""
    matcher = r.FastPathMatcher()
    samples = []
    for _ in range(20):
        for text in PERF_CORPUS:
            t = time.perf_counter()
            for clause in matcher.split_clauses(r.normalize_utterance(text)):
                matcher.match(clause)
            samples.append((time.perf_counter() - t) * 1000)
    samples.sort()
    p50, p95 = statistics.median(samples), samples[int(len(samples) * 0.95) - 1]
    print(f"\nnormalize+split+match: p50={p50:.3f}ms p95={p95:.3f}ms max={samples[-1]:.3f}ms (n={len(samples)})")
    assert len(samples) >= 200 and p95 < 5.0


# ---------- B8：chat call ----------
def _scripted(plan, chat_reply="這是回覆"):
    """第一次（帶 tools）回 plan，之後（不帶 tools 的 chat call）回 chat_reply；記錄每次呼叫"""
    seen = []

    def fake(messages, **kw):
        seen.append((messages, kw))
        return plan if kw.get("tools", True) else {"content": chat_reply}

    return fake, seen


def test_chat_reply_tool_triggers_chat_call_without_tools(monkeypatch, sh):
    fake, seen = _scripted({"tool_calls": [_tc("chat_reply", "{}")]}, "Python 是一種程式語言")
    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("Python 是什麼")
    assert len(seen) == 2
    plan_kw, chat_kw = seen[0][1], seen[1][1]
    assert plan_kw.get("tools", True) is True
    assert chat_kw["tools"] is False and chat_kw["max_tokens"] == 128 and chat_kw["timeout"] == config.CHAT_TIMEOUT_S
    assert seen[1][0][-1] == {"role": "user", "content": "Python 是什麼"}  # 原句直接送入模型
    assert seen[1][0][0]["content"] == actions.CHAT_SYSTEM_PROMPT
    assert out["replies"] == ["Python 是一種程式語言"]
    assert out["steps"][0]["status"] == "ok" and out["steps"][0]["chat_ms"] >= 0
    assert sh == []


def test_content_only_also_triggers_chat_call(monkeypatch, sh):
    fake, seen = _scripted({"content": "（規劃呼叫的文字，不採用）"}, "你好呀")
    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("今天心情不錯")
    assert len(seen) == 2 and seen[1][1]["tools"] is False
    assert seen[1][0][-1]["content"] == "今天心情不錯"
    assert out["replies"] == ["你好呀"]


def test_chat_reply_is_traditionalized(monkeypatch, sh):
    fake, _ = _scripted({}, "这是浏览器，请打开设置")
    monkeypatch.setattr(llm_client, "_chat", fake)
    assert r.process_voice_command("今天心情不錯")["replies"] == ["這是瀏覽器，請打開設定"]


def test_chat_call_failure_is_recorded_not_raised(monkeypatch, sh):
    def fake(messages, **kw):
        if kw.get("tools", True):
            return {"tool_calls": [_tc("chat_reply", "{}")]}
        raise urllib.error.URLError("down")

    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("Python 是什麼")
    assert out["steps"][0]["status"] == "llm_unavailable"
    assert out["replies"][0].startswith("⚠️ AI 回覆失敗")


def test_mixed_action_and_question(monkeypatch, sh):
    fake, seen = _scripted({"tool_calls": [_tc("chat_reply", "{}")]}, "Python 是程式語言")
    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("開 Chrome，順便告訴我 Python 是什麼")
    assert sh == [APP(CHROME)]
    assert [s["kind"] for s in out["steps"]] == ["fast", "slow"]
    assert seen[1][0][-1]["content"] == "告訴我 Python 是什麼"
    assert out["replies"] == ["Python 是程式語言"]


def test_duplicate_chat_reply_calls_chat_once(monkeypatch, sh):
    fake, seen = _scripted({"tool_calls": [_tc("chat_reply", "{}"), _tc("chat_reply", "{}")]})
    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("Python 是什麼")
    assert len(seen) == 2 and out["replies"] == ["這是回覆"]


def test_tools_and_chat_run_in_listed_order(monkeypatch, sh):
    fake, seen = _scripted({"tool_calls": [_tc("control_mac_app", '{"app_name":"Slack","action":"open"}'),
                                           _tc("chat_reply", "{}")]})
    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("開 Slack 並且跟我說聲嗨")
    assert sh == [APP("Slack")] and out["replies"] == ["這是回覆"]


def test_new_tools_in_schema():
    names = [t["function"]["name"] for t in r.TOOLS_SCHEMA]
    assert names[-3:] == ["open_project", "read_screen", "chat_reply"] and len(names) == 8
    enum = tools._SCHEMA_BY_NAME["system_power_action"]["properties"]["action"]["enum"]
    assert {"dark_mode_on", "dark_mode_off", "toggle_dark_mode"} <= set(enum)
    assert r.validate_tool_call("read_screen", {}) == ({}, None)
    assert r.validate_tool_call("chat_reply", {}) == ({}, None)


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osacompile")
def test_dark_mode_scripts_compile(tmp_path, sh):
    """新增的深色模式開／關腳本只編譯、不執行"""
    r.system_power_action("dark_mode_on")
    r.system_power_action("dark_mode_off")
    assert [c[2].rsplit(" ", 1)[1] for c in sh] == ["true", "false"]
    for i, cmd in enumerate(sh):
        p = real_run(["osacompile", "-e", cmd[2], "-o", str(tmp_path / f"{i}.scpt")], capture_output=True, text=True, timeout=15)
        assert p.returncode == 0, p.stderr


def test_hanging_command_times_out_instead_of_blocking(monkeypatch):
    """osascript 卡在授權對話框時，_run 逾時回報失敗，不讓整條流程卡住"""
    def hang(cmd, **kw):
        assert kw["timeout"] == 0.1
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    ok, msg = actions._run(["osascript", "-e", "x"], timeout=0.1)
    assert ok is False and "逾時" in msg


def test_page_poll_timeout_does_not_block_read_screen(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path)
    real_run = s.run

    def run(cmd, **kw):
        if cmd[0] == "osascript":
            assert kw["timeout"] == actions.POLL_TIMEOUT_S
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return real_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    out = r.process_voice_command("開google，搜尋台北天氣，辨識這頁")
    assert [st["status"] for st in out["steps"]] == ["ok", "ok", "ok"]  # 輪詢失敗就直接截圖
    assert out["replies"]


# ---------- LLM 回傳畸形內容時不得崩潰 ----------
@pytest.mark.parametrize("tool_calls", [
    [None],
    ["not a dict"],
    [{"type": "function"}],
    [{"function": "x"}],
    [{"function": {"arguments": "{}"}}],            # 缺 name
    [{"function": {"name": "rm_rf", "arguments": "{}"}}],
])
def test_malformed_tool_calls_are_rejected_not_crashed(monkeypatch, sh, tool_calls):
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"tool_calls": tool_calls})
    out = r.process_voice_command("幫我整理一下桌面")
    assert sh == []
    assert out["steps"][0]["status"] == "rejected"
    assert out["steps"][0]["logs"][0].startswith("⚠️ 已拒絕")


@pytest.mark.parametrize("message", [None, "text", ["x"]])
def test_non_object_message_is_llm_error(monkeypatch, sh, message):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode()))
    out = r.process_voice_command("幫我整理一下桌面")
    assert out["steps"][0]["status"] == "llm_error" and sh == []


def test_missing_command_is_reported_not_raised(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    monkeypatch.setattr(subprocess, "run", boom)
    assert r.control_mac_app("Notes").startswith("❌")
    out = r.process_voice_command("開 Safari，把音量調到30")
    assert [s["status"] for s in out["steps"]] == ["exec_failed", "exec_failed"]
