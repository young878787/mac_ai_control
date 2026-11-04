"""不需模型、不動系統設定的離線測試：subprocess.run 與 LLM 皆以假物件取代。"""
import subprocess
import sys

import pytest

import build_vocab
import mac_ai.router as r
from mac_ai.router import llm_client

real_run = subprocess.run  # conftest 的 sh fixture 會換掉 subprocess.run，需要真實版本時用這個


# ---------- 詞庫 ----------
def test_lexicon_no_alias_collision():
    seen = {}
    for app in r.LEXICON["apps"]:
        for key in [app["name"], *app["aliases"], *app["misheard"]]:
            k = key.lower()
            assert seen.setdefault(k, app["name"]) == app["name"], f"{key} 同時屬於多個 App"


def test_misheard_not_in_aliases():
    assert not set(r.APP_MISHEARD) & set(r.MAC_APP_ALIASES)


@pytest.mark.parametrize("raw,expected", [
    ("打開科索", "打開Cursor"),
    ("開谷哥瀏覽器", "開Google Chrome"),
    ("打開終端機", "打開終端機"),
])
def test_normalize_utterance(raw, expected):
    assert r.normalize_utterance(raw) == expected


def test_build_vocab_outputs(tmp_path):
    build_vocab.build(build_vocab.LEXICON, tmp_path)
    hints = (tmp_path / "stt_hints.txt").read_text(encoding="utf-8").splitlines()
    assert "Cursor" in hints and "Google Chrome" in hints and "小算盤" in hints
    assert "科索" not in hints  # 誤聽詞不可餵給 STT 當提示
    userdict = (tmp_path / "userdict.txt").read_text(encoding="utf-8")
    assert "科索 100000 n" in userdict
    assert all(len(line.split(" ")) == 3 for line in userdict.splitlines())  # 無含空白的詞

    jieba = pytest.importorskip("jieba")
    tk = jieba.Tokenizer()
    tk.load_userdict(str(tmp_path / "userdict.txt"))
    assert "科索" in tk.lcut("打開科索")
    assert "谷歌瀏覽器" in tk.lcut("開谷歌瀏覽器")


# ---------- 子句切分 ----------
@pytest.mark.parametrize("text,expected", [
    ("幫我打開小算盤，然後把音量調到 40", ["幫我打開小算盤", "把音量調到 40"]),
    ("打開 VS Code", ["打開 VS Code"]),
    ("開終端機順便把螢幕調暗一點還有開瀏覽器查天氣", ["開終端機", "把螢幕調暗一點", "開瀏覽器查天氣"]),
])
def test_split_clauses(text, expected):
    assert r.FastPathMatcher().split_clauses(text) == expected


# ---------- Fast Path ----------
@pytest.mark.parametrize("clause,expected", [
    ("幫我打開小算盤", [["open", "-a", "Calculator"]]),
    ("開 Chrome", [["open", "-a", "Google Chrome"]]),
    ("開Google Chrome", [["open", "-a", "Google Chrome"]]),
    ("開 VS Code", [["open", "-a", "Visual Studio Code"]]),
    ("關掉備忘錄", [["osascript", "-e", 'tell application "Notes" to quit']]),
    ("把音量調到 50", [["osascript", "-e", "set volume output volume 50"]]),
    ("把音量調到 150", [["osascript", "-e", "set volume output volume 100"]]),
    ("靜音", [["osascript", "-e", "set volume output muted true"]]),
    ("取消靜音", [["osascript", "-e", "set volume output muted false"]]),
])
def test_fast_path_hit(sh, clause, expected):
    assert r.FastPathMatcher().try_execute(clause) is not None
    assert sh == expected


@pytest.mark.parametrize("clause", [
    "把音量調大一點", "今天天氣如何", "",
])
def test_fast_path_miss(sh, clause):
    assert r.FastPathMatcher().try_execute(clause) is None
    assert sh == []


def test_fast_path_brightness_and_search_now_fast(sh):
    """亮度與搜尋已納入 Fast Path（原本交給 LLM）"""
    assert r.FastPathMatcher().try_execute("把螢幕調暗一點") is not None
    assert "key code 145" in sh[0][2]
    assert r.FastPathMatcher().try_execute("開瀏覽器查一下台北天氣") is not None
    assert sh[1] == ["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]


def test_fast_path_dark_mode(sh):
    assert r.FastPathMatcher().try_execute("打開深色模式")
    assert "dark mode" in sh[0][2]


# ---------- 工具參數驗證 ----------
@pytest.mark.parametrize("name,args,ok", [
    ("control_mac_app", {"app_name": "Notes", "action": "open"}, True),
    ("control_mac_app", {"app_name": "Notes", "action": "delete"}, False),   # enum
    ("control_mac_app", {"app_name": "Notes"}, False),                        # 缺必填
    ("control_mac_app", {"app_name": "Notes", "action": "open", "x": 1}, False),  # 未定義參數
    ("set_system_volume", {"level": "50"}, True),                             # 字串數字可轉 int
    ("set_system_volume", {"level": "很大"}, False),
    ("set_system_volume", {"level": True}, False),
    ("set_system_volume", {"mute": "true"}, False),                           # 非布林
    ("adjust_screen_brightness", {"direction": "down", "steps": 3}, True),
    ("adjust_screen_brightness", {"direction": "left", "steps": 3}, False),
    ("web_search_or_open_url", {"query": "  "}, False),
    ("rm_rf", {}, False),                                                     # 未知工具
    ("system_power_action", ["sleep"], False),                                # 參數非物件
])
def test_validate_tool_call(name, args, ok):
    clean, err = r.validate_tool_call(name, args)
    assert (err is None) == ok and (clean is not None) == ok


def test_open_project_schema_does_not_lure_llm_into_opening_folders():
    """實測：2B LLM 看到『專案資料夾』，把『開資料夾』誤判成開 VS Code 專案；
    範例 'mac_ai_control' 不能拿掉（拿掉後『語音助手的專案』會開成清單第一個專案），但要限定為『使用者說的』"""
    fn = next(t["function"] for t in r.TOOLS_SCHEMA if t["function"]["name"] == "open_project")
    project_name = fn["parameters"]["properties"]["project_name"]["description"]
    assert "資料夾" not in fn["description"] + project_name
    assert "明確說『專案』" in fn["description"]
    assert project_name.startswith("使用者說的")


def test_validate_mute_drops_level():
    clean, _ = r.validate_tool_call("set_system_volume", {"level": 30, "mute": True})
    assert clean == {"mute": True}
    clean, _ = r.validate_tool_call("set_system_volume", {"level": 30, "mute": False})
    assert clean == {"level": 30, "mute": False}


# ---------- Slow Path（LLM 以假回應取代）----------
def _tc(name, args):
    return {"type": "function", "function": {"name": name, "arguments": args}}


def test_plan_slow_path_parses_json_string_and_dict(monkeypatch):
    msg = {"tool_calls": [
        _tc("control_mac_app", '{"action": "open", "app_name": "Cursor"}'),
        _tc("adjust_screen_brightness", {"direction": "down", "steps": 3}),
    ]}
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: msg)
    calls, reply, rejected = r.plan_slow_path("x")
    assert calls == [("control_mac_app", {"action": "open", "app_name": "Cursor"}),
                     ("adjust_screen_brightness", {"direction": "down", "steps": 3})]
    assert rejected == []


def test_plan_slow_path_rejects_bad_calls(monkeypatch):
    msg = {"tool_calls": [
        _tc("rm_rf", "{}"),
        _tc("system_power_action", "{not json"),
        _tc("system_power_action", '{"action": "lock_screen"}'),
    ]}
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: msg)
    calls, _, rejected = r.plan_slow_path("x")
    assert calls == [("system_power_action", {"action": "lock_screen"})]
    assert len(rejected) == 2


def test_execute_slow_path_does_not_run_rejected(monkeypatch, sh):
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"tool_calls": [_tc("rm_rf", "{}")]})
    logs = r.execute_slow_path_llm("x")
    assert sh == [] and "已拒絕" in logs[0]


def test_execute_slow_path_plain_reply(monkeypatch, sh):
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"content": "你好"})
    assert r.execute_slow_path_llm("x") == ["🤖 AI 回覆：你好"]
    assert sh == []


def test_hint_injected_only_when_alias_present(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: seen.append(messages[0]["content"]) or {})
    r.plan_slow_path("開小算盤然後查天氣")
    r.plan_slow_path("查天氣")
    assert "小算盤→Calculator" in seen[0] and "備忘錄" not in seen[0]
    assert "已知詞庫" not in seen[1]


def test_chat_request_body(monkeypatch):
    """請求必須帶 tools、關閉 thinking、使用官方建議取樣參數"""
    import io, json, urllib.request
    captured = {}

    def fake_urlopen(req, timeout):
        captured.update(json.loads(req.data))
        captured["url"] = req.full_url
        return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm_client._chat([{"role": "user", "content": "hi"}]) == {"content": "ok"}
    assert captured["url"].endswith("/chat/completions")
    assert captured["enable_thinking"] is False
    assert len(captured["tools"]) == 8
    assert (captured["temperature"], captured["top_k"], captured["repetition_penalty"]) == (0.1, 50, 1.1)


# ---------- 雙軌整合 ----------
def test_pipeline_all_fast_never_calls_llm(monkeypatch, sh):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    r.process_voice_command("幫我打開小算盤，然後把音量調到 40")
    assert sh == [["open", "-a", "Calculator"], ["osascript", "-e", "set volume output volume 40"]]


def test_pipeline_mixed_routes_unmatched_to_llm(monkeypatch, sh):
    seen = []

    def fake_chat(messages, **kw):
        seen.append(messages[-1]["content"])
        return {"tool_calls": [_tc("adjust_screen_brightness", '{"direction":"down","steps":3}'),
                               _tc("web_search_or_open_url", '{"query":"台北天氣"}')]}

    monkeypatch.setattr(llm_client, "_chat", fake_chat)
    r.process_voice_command("打開終端機，順便螢幕太亮了，還有幫我找一下台北天氣")
    assert sh[0] == ["open", "-a", "Terminal"]
    assert seen == ["螢幕太亮了，幫我找一下台北天氣"]
    assert sh[2] == ["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]


def test_pipeline_misheard_goes_fast_after_normalize(monkeypatch, sh):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    r.process_voice_command("打開科索")
    assert sh == [["open", "-a", "Cursor"]]


# ---------- 依說話順序執行與計時 ----------
def _scripted_chat(replies):
    """依 user 訊息內容回傳預先寫好的 tool_calls，並記錄每次送進 LLM 的語句"""
    seen = []

    def fake(messages, **kw):
        text = messages[-1]["content"]
        seen.append(text)
        return {"tool_calls": [_tc(n, a) for n, a in replies[text]]}

    return fake, seen


def test_match_does_not_execute(sh):
    run = r.FastPathMatcher().match("開 Chrome")
    assert sh == []
    run()
    assert sh == [["open", "-a", "Google Chrome"]]


def test_pipeline_keeps_spoken_order(monkeypatch, sh):
    """未命中的『開 Slack，開 Notion』在前、Fast Path 的音量在後 → 執行順序仍照說話順序"""
    fake, seen = _scripted_chat({"開 Slack，開 Notion": [
        ("control_mac_app", '{"app_name":"Slack","action":"open"}'),
        ("control_mac_app", '{"app_name":"Notion","action":"open"}'),
    ]})
    monkeypatch.setattr(llm_client, "_chat", fake)
    r.process_voice_command("開 Slack，開 Notion，把音量調到20")
    assert sh == [["open", "-a", "Slack"], ["open", "-a", "Notion"],
                  ["osascript", "-e", "set volume output volume 20"]]
    assert seen == ["開 Slack，開 Notion"]  # 連續未命中合併成一次 LLM 呼叫


def test_pipeline_slow_fast_slow_splits_segments(monkeypatch, sh):
    fake, seen = _scripted_chat({
        "螢幕太亮了": [("adjust_screen_brightness", '{"direction":"down","steps":3}')],
        "幫我找一下台北天氣": [("web_search_or_open_url", '{"query":"台北天氣"}')],
    })
    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("螢幕太亮了，靜音，幫我找一下台北天氣")
    assert seen == ["螢幕太亮了", "幫我找一下台北天氣"]  # 被 Fast Path 的『靜音』隔開 → 兩次 LLM
    assert [c[2] if c[0] == "osascript" else c[0] for c in sh] == [
        sh[0][2], "set volume output muted true", "open"]
    assert "key code 145" in sh[0][2]
    assert [s["kind"] for s in out["steps"]] == ["slow", "fast", "slow"]


def test_pipeline_returns_timing(monkeypatch, sh):
    fake, _ = _scripted_chat({"螢幕太亮了": [("adjust_screen_brightness", '{"direction":"down","steps":3}')]})
    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("開 Chrome，螢幕太亮了")
    fast, slow = out["steps"]
    assert (fast["kind"], slow["kind"]) == ("fast", "slow")
    assert "llm_ms" not in fast and 0 <= slow["llm_ms"] <= slow["ms"]
    assert out["total_ms"] >= fast["ms"] + slow["ms"]


# ---------- AppleScript 語法（只編譯、不執行）----------
@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osacompile")
def test_applescripts_compile(tmp_path, sh):
    r.control_mac_app("Notes", "quit")
    r.set_system_volume(level=30, mute=False)
    r.adjust_screen_brightness("up", 2)
    r.system_power_action("toggle_dark_mode")
    r.system_power_action("lock_screen")
    scripts = [c[2] for c in sh if c[0] == "osascript"]
    assert len(scripts) == 6
    for i, script in enumerate(scripts):
        p = real_run(["osacompile", "-e", script, "-o", str(tmp_path / f"{i}.scpt")],
                           capture_output=True, text=True, timeout=15)
        assert p.returncode == 0, f"{script!r}: {p.stderr}"
