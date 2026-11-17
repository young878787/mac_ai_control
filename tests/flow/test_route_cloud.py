"""端雲協同整條路由（雲端與本機 LLM 皆為假）：沒金鑰＝現狀、熱路徑不碰雲端、G1、同一套驗證、失敗退回本機、金鑰不外洩、記錄欄位、記憶、對象守門、看畫面、開網站"""
import io
import json
import urllib.error

import pytest

import mac_ai.router as r
from mac_ai import config
from mac_ai.assistant import command_record
from mac_ai.router import cloud_client, memory, slow_path
from mac_ai.router.actions import ExecContext
from mac_ai.stt import completeness
from support.clock import Clock
from support.cloud import KEY
from support.llm import FakeLLM, tc
from support.screen import Screen

BRIGHT = "螢幕太亮了眼睛好痛"  # Fast Path 未命中（route_corpus 驗證過），會交給 Slow Path


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://cloud.test", code, "err", {}, io.BytesIO(body))


@pytest.fixture
def local(monkeypatch):
    """假本機 LLM：local.plan 是規劃請求（帶 tools）要回的 message；chat 請求回「本機聊天」。local.calls 記錄每次的 messages"""
    return FakeLLM(plan={"content": "本機回覆"}, chat="本機聊天").install(monkeypatch)


# ---------- 沒有金鑰＝現狀 ----------
def test_without_key_slow_path_is_exactly_the_local_path(local, sh):
    local.plan = {"tool_calls": [tc("adjust_screen_brightness", {"direction": "down", "steps": 3})]}
    out = r.process_voice_command(BRIGHT)
    (step,) = out["steps"]
    assert step["route"] == "local" and "cloud" not in step
    messages, = local.calls
    assert messages[0] == {"role": "system", "content": slow_path.SYSTEM_PROMPT} and "最近對話" not in messages[1]["content"]
    assert any("osascript" == c[0] for c in sh)


# ---------- Fast Path／熱路徑不碰雲端 ----------
def test_fast_path_and_completeness_never_call_cloud(cloud, local, sh):
    out = r.process_voice_command("打開小算盤，音量調到 30")
    assert [s["kind"] for s in out["steps"]] == ["fast", "fast"]
    from support.typo_corpus import CASES
    for _, text, _, _ in CASES:
        completeness.classify(text)
    assert cloud.requests == [] and local.calls == []


# ---------- G1 ----------
@pytest.mark.parametrize("text", ["不要鎖定螢幕", "我不想鎖定螢幕", "剛剛鎖定螢幕了", "別存擋", "不要關閉試窗"])
def test_negative_and_narrative_sentences_reach_no_model(cloud, local, sh, text):
    cloud.reply = {"tool_calls": [tc("system_power_action", {"action": "lock_screen"})]}
    out = r.process_voice_command(text)
    assert cloud.requests == [] and local.calls == [] and out["replies"] == [slow_path.NO_ACTION_REPLY]
    assert [c for c in sh if c[0] in ("open", "osascript")] == []


# ---------- 雲端規劃：同一套驗證 ----------
def test_cloud_plan_runs_same_commands_as_local_plan(cloud, local, sh):
    call = tc("adjust_screen_brightness", {"direction": "down", "steps": 3})
    cloud.reply = {"tool_calls": [call], "content": "好的～"}
    out = r.process_voice_command(BRIGHT)
    cloud_cmds = list(sh)
    assert out["steps"][0]["route"] == "cloud" and local.calls == []
    sh.clear()
    cloud.reply = http_error(500)  # 雲端失敗 → 退回本機，用同一個呼叫
    local.plan = {"tool_calls": [call]}
    cloud_client.reset()
    r.process_voice_command(BRIGHT)
    assert sh == cloud_cmds and cloud_cmds


@pytest.mark.parametrize("calls", [
    [tc("rm_rf", {})],
    [tc("control_mac_app", {"app_name": "FooBarApp", "action": "open"})],
    [tc("set_system_volume", {"level": 30, "bogus": 1})],
    [tc("front_action", {"id": "rm_everything"})],      # 雲端可用，但 id 必須是詞庫常用功能
    [tc("front_action", {"id": "close_window", "fallback": [["control_mac_app", {"app_name": "Safari", "action": "quit"}]]})],  # fallback 只給 Fast Path
    [tc("front_action", {"id": "terminal_new_tab"})],   # 沒有說法的內部 id 不給雲端
    [tc("site_search", {"query": ""})],
    [tc("context_search", {"query": "x"})],             # 本機工具名稱：雲端看不到
    [tc("find_file", {"query": "x"})],
])
def test_cloud_tool_calls_pass_the_same_trust_boundary(cloud, sh, calls):
    cloud.reply = {"tool_calls": calls, "content": "已經幫你做好了"}
    out = r.process_voice_command(BRIGHT)
    (step,) = out["steps"]
    assert [c for c in sh if c[0] in ("open", "osascript", "pkill")] == [] and step["status"] == "rejected"
    assert out["replies"] == []  # 被拒絕時不採用雲端的文字（不謊稱已執行）


def test_cloud_search_page_on_correction_sentence_is_refused(cloud, sh):
    cloud.reply = {"tool_calls": [tc("web_search_or_open_url", {"query": "台北天氣"})]}
    out = r.process_voice_command("沒有我說的是這網頁")
    assert [c for c in sh if c[0] == "open"] == [] and out["steps"][0]["status"] == "rejected"


def test_cloud_state_tool_dropped_for_information_question(cloud, sh):
    cloud.reply = {"tool_calls": [tc("system_power_action", {"action": "dark_mode_on"})], "content": "深色模式是一種介面配色～"}
    out = r.process_voice_command("什麼是深色模式")
    assert [c for c in sh if c[0] == "osascript"] == [] and out["steps"][0]["guard_dropped"] == ["system_power_action"]
    assert out["replies"] == []  # 有工具被丟棄時不採用文字


def test_cloud_chat_reply_is_cleaned_and_local_chat_is_not_called(cloud, local):
    cloud.reply = {"content": "\n\n## 標題\n**遞迴**是自己呼叫自己，今天适合學\n</thinking>"}
    out = r.process_voice_command("今天心情不錯")
    assert out["replies"] == ["標題\n遞迴是自己呼叫自己，今天適合學"] and local.calls == []
    assert out["steps"][0]["status"] == "ok"


def test_cloud_reply_adopted_only_when_every_tool_succeeds(cloud, sh):
    ok = tc("adjust_screen_brightness", {"direction": "up", "steps": 3})
    cloud.reply = {"tool_calls": [ok], "content": "好的～"}
    assert r.process_voice_command(BRIGHT)["replies"] == ["好的～"]
    cloud.reply = {"tool_calls": [ok, tc("control_mac_app", {"app_name": "FooBarApp", "action": "open"})], "content": "好的～"}
    assert r.process_voice_command(BRIGHT)["replies"] == []


CLAIM = __import__("re").compile(r"已(?:經)?(?:為你|幫你)?(?:鎖定|睡眠|關閉|關掉|開啟|打開|調整|調到)")


def test_failed_tool_never_leaves_a_claim_in_replies(cloud, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    cloud.reply = {"tool_calls": [tc("control_mac_app", {"app_name": "Safari", "action": "open"})], "content": "已經幫你打開 Safari 囉～"}
    out = r.process_voice_command("幫我弄一個瀏覽器出來")
    assert not any(CLAIM.search(x) for x in out["replies"])
    assert out["steps"][0]["status"] == "exec_failed"


def test_cloud_request_shape(cloud):
    cloud.reply = {"content": "好～"}
    r.process_voice_command("今天心情不錯")
    (body,) = cloud.requests
    assert body["model"] == "TEST-LLM" and body["reasoning_effort"] == config.CLOUD_REASONING_EFFORT
    assert body["tools"] == cloud_client.CLOUD_TOOLS and body["max_tokens"] == config.CLOUD_MAX_TOKENS
    assert body["messages"][0] == {"role": "system", "content": cloud_client.CLOUD_SYSTEM_PROMPT}
    assert body["messages"][1]["content"].startswith("今天心情不錯")  # 原句在最前面、不改字


def test_cloud_tools_differ_from_local_schema_only_in_chat_reply_text_and_cloud_only_tools():
    n = len(r.TOOLS_SCHEMA)
    pairs = [(c, t) for c, t in zip(cloud_client.CLOUD_TOOLS[:n], r.TOOLS_SCHEMA) if c != t]
    assert [t["function"]["name"] for _, t in pairs] == ["chat_reply"]
    assert [t["function"]["name"] for t in cloud_client.CLOUD_TOOLS[n:]] == ["front_action", "site_search"]
    assert "front_action" not in {t["function"]["name"] for t in r.TOOLS_SCHEMA}  # 本機模型看不到
    assert r.TOOLS_SCHEMA[-1]["function"]["parameters"]["properties"] == {}  # 本機（prefix cache）的 schema 不動
    assert cloud_client.CLOUD_TOOLS[n - 1]["function"]["parameters"]["required"] == ["text"]


def test_chat_reply_text_rides_along_with_tools_and_is_adopted_when_all_succeed(cloud, sh):
    cloud.reply = {"content": "", "tool_calls": [tc("set_system_volume", {"mute": True}), tc("chat_reply", {"text": "好喔～**想聊什麼**呢？"})]}
    out = r.process_voice_command("把喇叭靜音，然後陪我聊一下，順便說說笑話")
    assert out["replies"] == ["好喔～想聊什麼呢？"] and out["steps"][-1]["status"] == "ok"
    assert any(c[0] == "osascript" for c in sh) and not any("chat_reply" in l for l in out["steps"][-1]["logs"])  # chat_reply 不會被當成工具執行


def test_chat_reply_text_is_dropped_when_a_tool_fails(cloud, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    cloud.reply = {"tool_calls": [tc("set_system_volume", {"mute": True}), tc("chat_reply", {"text": "已經幫你靜音了喔～"})]}
    out = r.process_voice_command("把喇叭靜音，然後陪我聊一下，順便說說笑話")
    assert out["replies"] == [] and out["steps"][-1]["status"] == "exec_failed"


def test_chat_reply_alone_with_text_is_a_plain_reply_and_without_text_falls_to_local_chat(cloud, local):
    cloud.reply = {"tool_calls": [tc("chat_reply", {"text": "你好呀～"})]}
    assert r.process_voice_command("今天心情不錯")["replies"] == ["你好呀～"] and local.calls == []
    cloud.reply = {"tool_calls": [tc("chat_reply", {})]}
    assert r.process_voice_command("今天心情不錯")["replies"] == ["本機聊天"]


def test_split_chat_texts_ignores_garbage():
    rest, texts = cloud_client.split_chat_texts([
        tc("chat_reply", {"text": "  "}), {"type": "function", "function": {"name": "chat_reply", "arguments": "{壞掉"}},
        {"function": {"name": "chat_reply", "arguments": {"text": "直接是物件"}}}, tc("set_system_volume", {"mute": True}), "亂七八糟"])
    assert texts == ["直接是物件"] and [c if isinstance(c, str) else c["function"]["name"] for c in rest] == ["set_system_volume", "亂七八糟"]


def test_cloud_system_prompt_is_xml_third_person_and_stable():
    p = cloud_client.CLOUD_SYSTEM_PROMPT
    assert p.startswith("<assistant>") and p.endswith("</assistant>")
    assert "<persona>" in p and "<tools>" in p and "可愛" in p and "熱情" in p and "簡潔" in p
    assert not any(w in p for w in ("你是", "你的", "你會", "我是"))  # 第三人稱：不用「你」「我」指助手


# ---------- 失敗退回本機 ----------
def test_cloud_failure_falls_back_to_local_then_breaker_skips_cloud(cloud, local, sh):
    cloud.reply = urllib.error.URLError("down")
    local.plan = {"tool_calls": [tc("adjust_screen_brightness", {"direction": "down", "steps": 3})]}
    out = r.process_voice_command(BRIGHT)
    (step,) = out["steps"]
    assert step["route"] == "local" and step["cloud_fallback"] == "連線失敗" and step["cloud"]["status"] == "unavailable"
    assert len(local.calls) == 1 and any(c[0] == "osascript" for c in sh)
    r.process_voice_command(BRIGHT)
    assert len(cloud.requests) == 1 and len(local.calls) == 2  # 熔斷中：雲端不再被呼叫
    cloud_client._down_until = 0.0  # 冷卻結束
    cloud.reply = {"content": "好～"}
    assert r.process_voice_command("今天心情不錯")["steps"][0]["route"] == "cloud"


@pytest.mark.parametrize("err,status", [(OSError("timed out"), "timeout"), (http_error(429), "http_429"), (http_error(503), "http_503")])
def test_timeout_429_5xx_trip_breaker(cloud, local, err, status):
    cloud.reply = err
    out = r.process_voice_command("今天心情不錒")
    assert out["steps"][0]["cloud"]["status"] == status and not cloud_client.available()


def test_bad_response_falls_back_without_breaker(cloud, local, monkeypatch):
    monkeypatch.setattr(cloud_client, "_post", lambda *a: {"oops": 1})
    out = r.process_voice_command("今天心情不錯")
    assert out["steps"][0]["cloud"]["status"] == "bad_response" and cloud_client.available() and len(local.calls) >= 1


def test_auth_error_disables_cloud_for_the_run_and_warns_once(cloud, local, capsys):
    cloud.reply = http_error(401, f'{{"error":"invalid key {KEY}"}}'.encode())
    r.process_voice_command("今天心情不錯")
    r.process_voice_command("今天心情不錯")
    assert len(cloud.requests) == 1 and cloud_client.settings() is None
    out = capsys.readouterr().out
    assert out.count("金鑰錯誤或被拒") == 1 and KEY not in out


def test_local_miss_escalation_stays_local(cloud, local):
    local.plan = {"content": "x"}
    r.execute_slow_path_llm("找 python 教學", {}, ExecContext(), local_miss={"label": "python 教學", "scope": "家目錄", "query": "python 教學"})
    assert cloud.requests == [] and len(local.calls) >= 1


# ---------- 金鑰不外洩 ----------
LEAK_CASES = [
    ("ok", {"content": "好～"}),
    ("401", http_error(401, f"bad key {KEY}".encode())),
    ("429", http_error(429, f"rate limit for {KEY}".encode())),
    ("500", http_error(500, f"echo {KEY}".encode())),
    ("timeout", OSError(f"timed out {KEY}")),
    ("badjson", ValueError(f"bad json {KEY}")),
]


@pytest.mark.parametrize("name,reply", LEAK_CASES, ids=[c[0] for c in LEAK_CASES])
def test_key_never_appears_in_output_results_or_logs(cloud, local, capsys, name, reply):
    cloud.reply = reply
    out = r.process_voice_command("今天心情不錯")
    step = out["steps"][0]
    event = {"command": "x", "ok": True, "total_ms": 1.0, "replies": out["replies"]}
    blob = capsys.readouterr().out + json.dumps(out, ensure_ascii=False, default=str) + json.dumps(command_record(event, out), ensure_ascii=False)
    assert KEY not in blob and "Bearer" not in blob
    assert set(step.get("cloud", {})) <= {"model", "status", "total_ms", "prompt_tokens", "completion_tokens"}


# ---------- 記錄欄位 ----------
def test_command_log_records_route_and_cloud_meta_without_images(cloud, sh):
    cloud.reply = {"content": "好～"}
    out = r.process_voice_command("今天心情不錯")
    rec = command_record({"command": "c", "ok": True, "total_ms": 1.0, "replies": out["replies"]}, out)
    (step,) = rec["steps"]
    assert step["route"] == "cloud" and step["cloud"]["status"] == "ok" and step["cloud"]["prompt_tokens"] == 100
    assert "base64" not in json.dumps(rec)


# ---------- 記憶 ----------
def test_memory_keeps_five_turns_and_expires_after_idle():
    now = Clock(0.0)
    m = memory.MemoryStore(clock=now)
    for i in range(6):
        now.now += 1
        m.add(f"第{i}句", [("control_mac_app", {"app_name": "Safari", "action": "open"}, True)], [])
    assert [t["user"] for t in m.recent()] == [f"第{i}句" for i in range(1, 6)]
    now.now += 61
    assert m.recent() == [] and m.targets() == set() and m.render() is None


def test_memory_render_is_xml_escaped_and_hides_paths_of_local_tools():
    m = memory.MemoryStore(clock=lambda: 10.0)
    m.add("找 <報告>", [("find_file", {"query": "報告", "path": "/Users/x/secret"}, True),
                       ("control_mac_app", {"app_name": "Safari", "action": "open"}, False)], ["找到了 & 顯示"])
    text = m.render([("set_system_volume", {"level": 30}, True)])
    assert "&lt;報告&gt;" in text and "secret" not in text and "find_file() ✅" in text
    assert "control_mac_app(app_name=Safari, action=open) ❌" in text and "&amp;" in text
    assert "<this_sentence_done>set_system_volume(level=30) ✅</this_sentence_done>" in text


def test_memory_reply_is_truncated():
    m = memory.MemoryStore(clock=lambda: 0.0)
    m.add("問", [], ["字" * 200])
    assert len(m.recent()[0]["reply"]) == memory.REPLY_MAX_CHARS + 1


def test_pipeline_records_every_sentence_including_fast_steps(sh):
    r.process_voice_command("打開 Safari")
    (turn,) = memory.STORE.recent()
    assert turn["user"] == "打開 Safari" and turn["calls"][0][0] == "control_mac_app" and turn["calls"][0][2] is True


def test_cloud_request_carries_recent_turns_but_local_request_does_not(cloud, local, sh):
    r.process_voice_command("打開 Safari")
    cloud.reply = {"content": "好～"}
    r.process_voice_command("今天心情不錯")
    user = cloud.requests[-1]["messages"][1]["content"]
    assert "## 最近對話" in user and "control_mac_app(app_name=Safari, action=open) ✅" in user and "打開 Safari" in user
    cloud.reply = OSError("x")
    r.process_voice_command("今天心情不錯")
    assert all("最近對話" not in m[1]["content"] for m in local.calls)  # 本機規劃不附記憶（前綴快取與 prefill 不變）


def test_cloud_request_includes_calls_already_done_in_the_same_sentence(cloud, sh):
    cloud.reply = {"content": "好～"}
    r.process_voice_command("打開 Safari，今天心情不錯")
    assert "<this_sentence_done>control_mac_app(app_name=Safari, action=open) ✅</this_sentence_done>" in cloud.requests[-1]["messages"][1]["content"]


# ---------- 「它」的對象守門 ----------
def _after_opening_safari(sh):
    r.process_voice_command("打開 Safari")
    sh.clear()


def test_pronoun_resolved_from_memory_runs(cloud, sh):
    _after_opening_safari(sh)
    cloud.reply = {"tool_calls": [tc("control_mac_app", {"app_name": "Safari", "action": "quit"})]}
    out = r.process_voice_command("把它關掉")
    assert out["steps"][0]["kind"] == "slow" and out["steps"][0]["status"] == "ok" and any("Safari" in " ".join(c) for c in sh)


def test_pronoun_with_target_not_in_memory_is_refused(cloud, sh):
    _after_opening_safari(sh)
    cloud.reply = {"tool_calls": [tc("control_mac_app", {"app_name": "Spotify", "action": "quit"})]}
    out = r.process_voice_command("把它關掉")
    assert sh == [] and out["steps"][0]["status"] == "rejected" and any("不確定" in x for x in out["steps"][0]["logs"])


def test_pronoun_refused_when_the_remembered_open_failed(cloud, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    r.process_voice_command("打開 Safari")  # ❌：失敗的操作不算對象
    cloud.reply = {"tool_calls": [tc("control_mac_app", {"app_name": "Safari", "action": "quit"})]}
    assert r.process_voice_command("把它關掉")["steps"][0]["status"] == "rejected"


def test_pronoun_refused_when_memory_expired(cloud, sh, monkeypatch):
    now = Clock(0.0)
    monkeypatch.setattr(memory.STORE, "clock", now)  # 可控時鐘（測試結束自動還原）
    r.process_voice_command("打開 Safari")
    cloud.reply = {"tool_calls": [tc("control_mac_app", {"app_name": "Safari", "action": "quit"})]}
    now.now += 61
    sh.clear()
    assert r.process_voice_command("把它關掉")["steps"][0]["status"] == "rejected" and sh == []


def test_pronoun_with_explicit_target_is_not_affected():
    call = ("control_mac_app", {"app_name": "Spotify", "action": "quit"})
    assert slow_path.target_error(*call, "把它關掉，還有 Spotify 也關掉", set()) is None
    assert slow_path.target_error(*call, "把它關掉", set()) is not None
    assert slow_path.target_error(*call, "把它關掉", {"Spotify"}) is None
    assert slow_path.target_error(*call, "把 Spotify 關掉", set()) is None  # 沒有代名詞：不受守門影響
    assert slow_path.target_error("set_system_volume", {"level": 3}, "把它調小", set()) is None


def test_pronoun_resolved_from_a_step_earlier_in_the_same_sentence(cloud, sh):
    cloud.reply = {"tool_calls": [tc("control_mac_app", {"app_name": "Safari", "action": "quit"})]}
    out = r.process_voice_command("打開 Safari，然後把它關掉")
    assert [s["status"] for s in out["steps"]] == ["ok", "ok"]


def test_local_plan_gets_the_same_pronoun_guard(local, sh):
    _after_opening_safari(sh)
    local.plan = {"tool_calls": [tc("control_mac_app", {"app_name": "Spotify", "action": "quit"})]}
    out = r.process_voice_command("把它關掉")
    assert sh == [] and out["steps"][0]["status"] == "rejected"


# ---------- 雲端看畫面 ----------
def _vision_reply(body):
    last = body["messages"][-1]["content"]
    if isinstance(last, list):
        return {"content": "畫面上是一個 TypeError 錯誤～"}
    return {"tool_calls": [tc("read_screen", {})]}


SCREEN_TEXT = "把畫面上的英文翻成中文"


def test_cloud_planned_read_screen_sends_one_jpeg_to_cloud(cloud, monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path)
    cloud.reply = _vision_reply
    out = r.process_voice_command(SCREEN_TEXT)
    assert out["replies"] == ["畫面上是一個 TypeError 錯誤～"] and s.vl_messages == []
    plan, vision = cloud.requests
    assert "tools" in plan and "tools" not in vision
    parts = vision["messages"][1]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,") and parts[1]["text"] == SCREEN_TEXT
    assert vision["messages"][0]["content"] == cloud_client.CLOUD_VISION_SYSTEM_PROMPT
    step = out["steps"][0]
    assert step["image"]["quality"] == 80 and "vision_cloud" in step and not list(tmp_path.glob("macvoice_*"))  # 暫存檔已刪


def test_vision_flag_off_uses_local_vl_and_sends_no_image(cloud, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CLOUD_VISION_ENABLED", False)
    s = Screen(monkeypatch, tmp_path)
    cloud.reply = _vision_reply
    out = r.process_voice_command(SCREEN_TEXT)
    assert len(s.vl_messages) == 1 and out["replies"] == ["天氣：晴，氣溫 25 度"]
    assert "image_url" not in json.dumps(cloud.requests)


def test_oversized_image_falls_back_to_local_vl(cloud, monkeypatch, tmp_path):
    monkeypatch.setattr("mac_ai.router.actions.CLOUD_IMAGE_MAX_BYTES", 3)
    s = Screen(monkeypatch, tmp_path)
    cloud.reply = _vision_reply
    out = r.process_voice_command(SCREEN_TEXT)
    assert len(s.vl_messages) == 1 and out["steps"][0]["image"]["quality"] == 60 and "image_url" not in json.dumps(cloud.requests)
    assert not list(tmp_path.glob("macvoice_*")) and out["steps"][0]["status"] == "ok"


def test_cloud_vision_failure_falls_back_to_local_vl(cloud, monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path)
    cloud.reply = lambda body: _vision_reply(body) if not isinstance(body["messages"][-1]["content"], list) else OSError("down")
    out = r.process_voice_command(SCREEN_TEXT)
    assert len(s.vl_messages) == 1 and out["steps"][0]["vision_cloud"]["status"] == "unavailable"


def test_fast_path_read_screen_stays_local(cloud, monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path)
    out = r.process_voice_command("截圖看一下")
    assert out["steps"][0]["kind"] == "fast" and len(s.vl_messages) == 1 and cloud.requests == []


# ---------- 開網站：守門認得詞庫裡的網站名稱（site_open_home）----------
def _validate(utterance, query):
    calls, rejected, _ = slow_path.validate_calls([tc("web_search_or_open_url", {"query": query})], utterance, None, None, set())
    return calls, rejected


YT_DEEP = "https://www.youtube.com/watch?v=亂碼abc"
SITE_OPEN_CASES = [
    ("打開 YouTube", YT_DEEP, "https://www.youtube.com"), ("開 Facebook", "https://www.facebook.com/", "https://www.facebook.com"),
    ("去 GitHub", "https://github.com/anything/deep", "https://github.com"), ("上 YouTube", "youtube.com", "https://www.youtube.com"),
    ("幫我開瀏覽器看 YouTube", "https://www.youtube.com", "https://www.youtube.com"),
    ("幫我開瀏覽氣看 YouTube", "https://m.youtube.com/x", "https://www.youtube.com"),   # 子網域、錯字
    ("去 YouTube 看影片", YT_DEEP, "https://www.youtube.com"), ("開油管", "https://www.youtube.com", "https://www.youtube.com"),
    ("打開臉書", "https://www.facebook.com", "https://www.facebook.com"), ("幫我開 FB", "https://www.facebook.com", "https://www.facebook.com"),
    ("可以幫我打開 YouTube 嗎", "https://www.youtube.com", "https://www.youtube.com"), ("開估狗", "https://www.google.com.tw/", "https://www.google.com"),
]


@pytest.mark.parametrize("utterance,query,home", SITE_OPEN_CASES, ids=[c[0] for c in SITE_OPEN_CASES])
def test_site_open_is_allowed_and_rewritten_to_the_home_page(utterance, query, home):
    calls, rejected = _validate(utterance, query)
    assert rejected == [] and calls == [("web_search_or_open_url", {"query": home})]  # 模型編的深層網址不會被打開


@pytest.mark.parametrize("utterance,query", [
    ("沒有我說的是這網頁", "https://www.youtube.com"),          # 糾正句，沒有網站名稱
    ("打開那個影片網站", "youtube.com"),                          # 句子沒說是哪個網站
    ("打開 evil", "https://evil.com"),                           # 網域不在詞庫
    ("打開 YouTube", "https://github.com"),                      # 模型給的是別的網站
    ("打開 YouTube", "https://youtube.com.evil.com/login"),      # 網域後綴偽裝
    ("YouTube 是什麼", "https://www.youtube.com"),               # 資訊提問
    ("YouTube 好用嗎", "https://www.youtube.com"),               # 沒有開啟動詞
    ("我說的是 YouTube", "https://www.youtube.com"),             # 沒有開啟動詞（糾正句）
    ("打開 YouTube Music", "https://music.youtube.com"),         # 不是 YouTube
    ("開 YouTube 跟 Facebook", "https://www.youtube.com"),       # 兩個網站：對象不明
    ("打開 YouTube", "找貓咪"),                                   # query 不是網址
])
def test_site_open_still_refuses_what_it_should(utterance, query):
    calls, rejected = _validate(utterance, query)
    assert calls == [] and len(rejected) == 1 and "沒有搜尋的意思" in rejected[0]


def test_site_open_never_changes_what_was_already_allowed():
    assert _validate("上 YouTube 找 lofi", "https://www.youtube.com/results?search_query=lofi")[0][0][1]["query"].endswith("lofi")  # 有搜尋字詞：保留模型的搜尋網址
    assert _validate("打開 github.com/ml-explore", "github.com/ml-explore")[0] == [("web_search_or_open_url", {"query": "github.com/ml-explore"})]  # 字面網址：原樣
    assert _validate("幫我找一下台北天氣", "台北天氣")[0] == [("web_search_or_open_url", {"query": "台北天氣"})]


def test_site_open_end_to_end_cloud_and_local_run_the_same_command(cloud, local, sh):
    call = tc("web_search_or_open_url", {"query": YT_DEEP})
    cloud.reply = {"tool_calls": [call], "content": "好喔～"}
    out = r.process_voice_command("去 YouTube 看影片")
    assert out["steps"][0]["route"] == "cloud" and out["steps"][0]["status"] == "ok"
    cloud_cmds = [c for c in sh if c[0] == "open"]
    assert cloud_cmds == [["open", "https://www.youtube.com"]]
    sh.clear()
    cloud.reply = OSError("down")
    local.plan = {"tool_calls": [call]}
    r.process_voice_command("去 YouTube 看影片")
    assert [c for c in sh if c[0] == "open"] == cloud_cmds


def test_negated_site_sentence_never_reaches_a_model(cloud, local, sh):
    cloud.reply = {"tool_calls": [tc("web_search_or_open_url", {"query": "https://www.youtube.com"})]}
    for text in ("不要開 YouTube", "剛剛開了 YouTube"):
        out = r.process_voice_command(text)
        assert out["replies"] == [slow_path.NO_ACTION_REPLY]
    assert cloud.requests == [] and local.calls == [] and [c for c in sh if c[0] == "open"] == []


def test_context_site_name_matching():
    from mac_ai.router import context
    names = {t: (context.site_in(t) or {}).get("name") for t in ["打開 YouTube", "開油管", "去 FB", "幫我開 yt", "打開 YouTube Music", "myyoutube", "開 Facebook 跟 YouTube"]}
    assert names == {"打開 YouTube": "YouTube", "開油管": "YouTube", "去 FB": "Facebook", "幫我開 yt": "YouTube",
                     "打開 YouTube Music": None, "myyoutube": None, "開 Facebook 跟 YouTube": None}
    assert context.site_exact(" youtube ")["name"] == "YouTube" and context.site_exact("YouTube Music") is None


# ---------- 雲端的目前視窗工具（front_action、site_search）----------
def test_cloud_front_action_runs_on_the_front_window(cloud, sh, native_calls, front):
    front("Safari")
    cloud.reply = {"tool_calls": [tc("front_action", {"id": "scroll_down"})], "content": "好喔～"}
    out = r.process_voice_command("再往下")
    (step,) = out["steps"]
    assert step["route"] == "cloud" and step["status"] == "ok" and native_calls == ["key 121"]
    assert step["logs"][-2].endswith("✅ Safari：往下捲") and out["replies"] == ["好喔～"]


def test_cloud_front_action_out_of_scope_does_nothing(cloud, sh, native_calls, front):
    front("Finder")
    cloud.reply = {"tool_calls": [tc("front_action", {"id": "scroll_down"})], "content": "好喔～"}
    out = r.process_voice_command("再往下")
    assert native_calls == [] and out["steps"][0]["status"] == "exec_failed" and out["replies"] == []


def test_memory_tells_cloud_which_front_action_ran_last(cloud, sh, native_calls, front):
    front("Safari")
    r.process_voice_command("往下滑")
    cloud.reply = {"content": "好～"}
    r.process_voice_command("再往下")
    assert "front_action(id=scroll_down) ✅" in cloud.requests[-1]["messages"][1]["content"]


def test_cloud_front_action_dropped_for_information_question(cloud, sh, native_calls, front):
    front("Safari")
    cloud.reply = {"tool_calls": [tc("front_action", {"id": "fullscreen"})], "content": "按綠色按鈕就可以喔"}
    out = r.process_voice_command("要怎麼讓視窗變成全螢幕")
    assert native_calls == [] and out["steps"][0]["guard_dropped"] == ["front_action"]


def test_local_model_cannot_call_cloud_only_tools(local, sh, native_calls, front):
    front("Safari")
    local.plan = {"tool_calls": [tc("front_action", {"id": "scroll_down"}), tc("site_search", {"query": "貓"})]}
    out = r.process_voice_command("再往下")
    assert native_calls == [] and sh == [] and out["steps"][0]["status"] == "rejected"


def test_cloud_site_search_uses_the_site_in_front(cloud, sh, front):
    front("Safari", host="www.youtube.com")
    cloud.reply = {"tool_calls": [tc("site_search", {"query": "貓咪影片"})]}
    out = r.process_voice_command("在這裡幫我找貓咪影片好不好")
    assert sh[-1] == ["open", "-a", "Safari", "https://www.youtube.com/results?search_query=%E8%B2%93%E5%92%AA%E5%BD%B1%E7%89%87"]
    assert out["steps"][0]["status"] == "ok"


def test_cloud_site_search_without_site_falls_back_to_web_search(cloud, sh, front):
    front("Finder")
    cloud.reply = {"tool_calls": [tc("site_search", {"query": "貓咪影片"})]}
    r.process_voice_command("在這裡幫我找貓咪影片好不好")
    assert sh[-1][0] == "open" and "google.com/search" in sh[-1][-1]


def test_cloud_site_search_needs_search_intent(cloud, sh, front):
    front("Safari", host="www.youtube.com")
    cloud.reply = {"tool_calls": [tc("site_search", {"query": "貓咪"})]}
    out = r.process_voice_command("沒有我說的是這網頁")
    assert [c for c in sh if c[0] == "open"] == [] and out["steps"][0]["status"] == "rejected"
    assert any("site_search 被略過" in l for l in out["steps"][0]["logs"])


@pytest.mark.parametrize("text", ["把它關掉", "它關掉", "幫我把它關閉", "把它關起來", "把它退出", "把它關"])
def test_close_words_never_become_minimize(cloud, sh, native_calls, front, text):
    front("Safari")
    cloud.reply = {"tool_calls": [tc("front_action", {"id": "minimize"})]}
    out = r.process_voice_command(text)
    assert native_calls == [] and out["steps"][0]["status"] == "rejected"
    assert any("不是最小化" in l for l in out["steps"][0]["logs"])


def test_put_away_may_minimize(cloud, sh, native_calls, front):
    front("Safari")
    cloud.reply = {"tool_calls": [tc("front_action", {"id": "minimize"})]}
    assert r.process_voice_command("把它收起來")["steps"][0]["status"] == "ok" and native_calls == ["window minimize"]
