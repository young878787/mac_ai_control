"""不需模型、不動系統設定的離線測試：subprocess.run 與 LLM 皆以假物件取代。"""
import subprocess
import sys

import pytest

import build_vocab
import mac_ai.router as r
from mac_ai.router import llm_client, slow_path

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
    """詞庫提示放在使用者訊息；system 逐字固定（且與暖機相同），工具定義的前綴快取才會命中"""
    seen = []
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: seen.append(messages) or {})
    r.plan_slow_path("開小算盤然後查天氣")
    r.plan_slow_path("查天氣")
    r.warmup_llm()
    assert seen[0][0]["content"] == seen[1][0]["content"] == seen[2][0]["content"] == slow_path.SYSTEM_PROMPT
    assert seen[0][1]["content"] == "開小算盤然後查天氣\n\n## 名稱對照\n- 小算盤→Calculator"
    assert seen[1][1]["content"] == "查天氣"


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
    assert (captured["temperature"], captured["top_k"], captured["repetition_penalty"]) == (0.4, 50, 1.1)


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
    r.process_voice_command("打開終端機，順便螢幕太亮了，還有今天台北會下雨嗎")
    assert sh[0] == ["open", "-a", "Terminal"]
    assert seen == ["螢幕太亮了，今天台北會下雨嗎"]
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


def test_pipeline_keeps_spoken_order(monkeypatch, sh, installed_apps):
    """未命中的『我想用 Slack，用 Notion』在前、Fast Path 的音量在後 → 執行順序仍照說話順序"""
    installed_apps({"slack": "Slack", "notion": "Notion"})  # LLM 只能開詞庫或已安裝的 App
    fake, seen = _scripted_chat({"我想用 Slack，用 Notion": [
        ("control_mac_app", '{"app_name":"Slack","action":"open"}'),
        ("control_mac_app", '{"app_name":"Notion","action":"open"}'),
    ]})
    monkeypatch.setattr(llm_client, "_chat", fake)
    r.process_voice_command("我想用 Slack，用 Notion，把音量調到20")
    assert sh == [["open", "-a", "Slack"], ["open", "-a", "Notion"],
                  ["osascript", "-e", "set volume output volume 20"]]
    assert seen == ["我想用 Slack，用 Notion"]  # 連續未命中合併成一次 LLM 呼叫


def test_pipeline_slow_fast_slow_splits_segments(monkeypatch, sh):
    fake, seen = _scripted_chat({
        "螢幕太亮了": [("adjust_screen_brightness", '{"direction":"down","steps":3}')],
        "今天台北會下雨嗎": [("web_search_or_open_url", '{"query":"台北天氣"}')],
    })
    monkeypatch.setattr(llm_client, "_chat", fake)
    out = r.process_voice_command("螢幕太亮了，靜音，今天台北會下雨嗎")
    assert seen == ["螢幕太亮了", "今天台北會下雨嗎"]  # 被 Fast Path 的『靜音』隔開 → 兩次 LLM
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


# ---------- 搜尋意圖守門：沒有搜尋的意思，LLM 就不能開搜尋頁 ----------
def _llm_returns(monkeypatch, name, args):
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"tool_calls": [
        {"type": "function", "function": {"name": name, "arguments": args}}]})


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
    _llm_returns(monkeypatch, "web_search_or_open_url", '{"query": "%s"}' % query)
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
    _llm_returns(monkeypatch, "web_search_or_open_url", '{"query": "%s"}' % query)
    calls, _, rejected = r.plan_slow_path(utterance)
    assert calls == [] and len(rejected) == 1 and "沒有搜尋的意思" in rejected[0]


def test_rejected_search_opens_nothing(monkeypatch, sh):
    _llm_returns(monkeypatch, "web_search_or_open_url", '{"query": "台北天氣 2025-09-27"}')
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
    _llm_returns(monkeypatch, name, args)
    calls, _, rejected = r.plan_slow_path(utterance)
    assert rejected == [] and calls == [expected]


# ---------- 去掉模型洩漏的思考內容 ----------
@pytest.mark.parametrize("raw,expected", [
    ("思考過程</think>答案", "答案"),  # 實測：沒有開頭標籤也會洩漏
    ("<think>想一想</think>答案", "答案"),
    ("a</think>b</think>答案", "答案"),
    ("答案", "答案"),
    ("<think>沒有結束就被截斷", ""),
    ("只有思考</think>", ""),
])
def test_strip_think(raw, expected):
    from mac_ai.router.actions import _strip_think
    assert _strip_think(raw) == expected


@pytest.mark.parametrize("content,expected", [
    ("畫面上寫「A」。有其他問題嗎？</think>畫面上寫「A」。", "畫面上寫「A」。"),
    ("只有思考</think>", "無法理解指令"),
])
def test_chat_reply_strips_leaked_think(monkeypatch, content, expected):
    from mac_ai.router.actions import chat_reply
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"content": content})
    ctx = r.ExecContext()
    ctx.utterance = "Python 是什麼"
    chat_reply(ctx)
    assert ctx.replies == [expected]
