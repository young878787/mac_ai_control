"""前景視窗上下文的離線驗收（docs/前景視窗上下文與常用功能：實作計畫.md §9.1 A1、A2）。
前景一律是假的（conftest 的 front fixture）；subprocess 用 sh fixture 記錄，不真的執行。"""
import urllib.parse

import pytest
from conftest import REAL_READ_FRONT, REAL_READ_HOST

import mac_ai.router as r
from mac_ai import config
from mac_ai.router import context, llm_client
from mac_ai.router.fast_path import FastPathMatcher

VSCODE = "Visual Studio Code"


def _google(q):
    return f"https://www.google.com/search?q={urllib.parse.quote(q)}"


def _info(path):
    return f'"Code" ASN:0x0-0x1459458: (in front) \n    bundle path="{path}"\n'


def _runner(front_ok=True, info_ok=True, asn="ASN:0x0-0x1459458:", info=_info("/Applications/Safari.app")):
    calls = []

    def run(cmd, timeout=None):
        calls.append((cmd, timeout))
        if cmd[:2] == ["lsappinfo", "front"]:
            return front_ok, asn if front_ok else "逾時"
        return info_ok, info if info_ok else "err"
    return run, calls


# ---------- A1：讀取前景 ----------
@pytest.mark.parametrize("path,app,kind", [
    ("/Applications/Safari.app", "Safari", "browser"),
    ("/System/Volumes/Preboot/Cryptexes/App/System/Applications/Safari.app", "Safari", "browser"),
    ("/Applications/Visual Studio Code.app", VSCODE, "editor"),  # lsappinfo 的 name 是 "Code"，要用 bundle 檔名
    ("/System/Applications/Utilities/Terminal.app", "Terminal", "other"),
    ("/Applications/沒在詞庫.app", "沒在詞庫", "other"),
])
def test_parse_front_uses_bundle_filename(path, app, kind):
    assert context.parse_front(_info(path)) == context.FrontContext(app, kind)


def test_parse_front_ignores_own_desktop_character():
    own = config.ROOT / "desktop" / "node_modules" / "electron" / "dist" / "Electron.app"
    assert context.parse_front(_info(str(own))) is None


@pytest.mark.parametrize("info", ["", '"Code" ASN:0x0-0x1: \n    bundle path=[ NULL ] \n'])
def test_parse_front_without_bundle_path(info):
    assert context.parse_front(info) is None


def test_read_front_calls_lsappinfo_with_timeout():
    run, calls = _runner()
    assert REAL_READ_FRONT(run) == context.FrontContext("Safari", "browser")
    assert calls == [
        (["lsappinfo", "front"], config.CONTEXT_TIMEOUT_S),
        (["lsappinfo", "info", "-only", "bundlepath", "ASN:0x0-0x1459458:"], config.CONTEXT_TIMEOUT_S),
    ]


@pytest.mark.parametrize("kw", [{"front_ok": False}, {"asn": "  "}, {"info_ok": False}, {"info": ""}])
def test_read_front_failure_is_none(kw):
    run, _ = _runner(**kw)
    assert REAL_READ_FRONT(run) is None


def test_default_front_is_unreadable_in_offline_tests(sh):
    """conftest：沒有用 front fixture 時讀不到前景，而且不會真的跑 lsappinfo"""
    r.process_voice_command("搜尋貓咪")
    assert sh == [["open", _google("貓咪")]]


# ---------- A2：對象優先順序（明說 → 同句剛開的 → 前景 → 預設）----------
@pytest.mark.parametrize("text,front_app,expected_editor", [
    ("用 cursor 開 mac_ai_control 專案", VSCODE, "Cursor"),   # 明說
    ("打開 cursor，開 mac_ai_control 專案", VSCODE, "Cursor"),  # 同句剛開的
    ("開 mac_ai_control 專案", "Cursor", "Cursor"),            # 前景
    ("開 mac_ai_control 專案", VSCODE, VSCODE),
    ("開 mac_ai_control 專案", "Finder", VSCODE),              # 前景不是編輯器 → 預設
    ("開 mac_ai_control 專案", None, VSCODE),                  # 讀不到 → 預設
])
def test_project_editor_priority(monkeypatch, sh, projects, front, text, front_app, expected_editor):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    front(front_app)
    r.process_voice_command(text)
    assert sh[-1] == ["open", "-a", expected_editor, str(projects / "mac_ai_control")]


@pytest.mark.parametrize("text,front_app,expected", [
    ("打開 chrome 搜尋貓咪", "Safari", ["open", "-a", "Google Chrome", _google("貓咪")]),  # 明說＋同句剛開的
    ("搜尋貓咪", "Safari", ["open", "-a", "Safari", _google("貓咪")]),                    # 前景
    ("搜尋貓咪", "Finder", ["open", _google("貓咪")]),                                    # 前景不是瀏覽器 → 預設
    ("搜尋貓咪", None, ["open", _google("貓咪")]),
])
def test_search_browser_priority(monkeypatch, sh, front, text, front_app, expected):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    front(front_app)
    r.process_voice_command(text)
    assert sh[-1] == expected


def test_front_read_once_per_utterance_and_logged(sh, front):
    reads = front("Safari")
    out = r.process_voice_command("搜尋貓咪，搜尋狗狗")
    assert reads == ["Safari", ("host", "Safari")]  # App 與網址各讀一次
    first, second = (s["context"] for s in out["steps"])
    assert first["app"] == "Safari" and first["used"] is True and first["ms"] >= 0 and first["host_ms"] >= 0
    assert second == {"app": "Safari", "used": True, "host": "", "site": None}  # 第二步用快取，沒有讀取時間


def test_front_not_read_when_not_needed(sh, front):
    reads = front("Safari")
    out = r.process_voice_command("音量調到 30，打開小算盤")
    assert reads == [] and all("context" not in s for s in out["steps"])


def test_front_reread_after_opening_app(sh, projects, front):
    """同句開了編輯器之後前景已經換了：快取作廢（對象仍以同句剛開的為準）"""
    reads = front("Safari")
    r.process_voice_command("搜尋貓咪，打開 cursor，搜尋狗狗")
    assert reads.count("Safari") == 2


def test_match_never_reads_front(sh, front):
    reads = front("Safari")
    m = FastPathMatcher()
    for text in ("搜尋貓咪", "開 mac_ai_control 專案", "找履歷", "打開終端機"):
        m.match(text)
    assert reads == [] and sh == []


# ---------- A1（網址）：只留 host ----------
@pytest.mark.parametrize("url,host", [
    ("https://m.facebook.com/x?y=1", "m.facebook.com"),
    ("https://WWW.YouTube.com:443/watch?v=abc", "www.youtube.com"),
    ("http://github.com", "github.com"),
    ("file:///Users/me/a.html", ""), ("favorites://", ""), ("", ""), ("missing value", ""),
])
def test_parse_host(url, host):
    assert context.parse_host(url) == host


@pytest.mark.parametrize("host,site", [
    ("m.facebook.com", "Facebook"), ("www.facebook.com", "Facebook"), ("facebook.com", "Facebook"),
    ("www.youtube.com", "YouTube"), ("github.com", "GitHub"), ("gist.github.com", "GitHub"),
    ("www.google.com", "Google"), ("www.google.com.tw", "Google"),
    ("notfacebook.com", None), ("facebook.com.evil.example", None), ("example.com", None), ("", None),
])
def test_site_for(host, site):
    hit = context.site_for(host)
    assert (hit["name"] if hit else None) == site


def test_read_host_only_for_supported_browsers():
    calls = []

    def run(cmd, timeout=None):
        calls.append((cmd, timeout))
        return True, "https://www.facebook.com/groups/1?ref=x"
    assert REAL_READ_HOST(run, "Safari") == "www.facebook.com"
    assert calls[0][0][0] == "osascript" and calls[0][1] == config.CONTEXT_TIMEOUT_S
    assert REAL_READ_HOST(run, "Arc") == "" and len(calls) == 1  # 不支援的瀏覽器：不問
    assert REAL_READ_HOST(lambda cmd, timeout=None: (False, "逾時"), "Safari") == ""


# ---------- A3：搜尋分流（計劃書 §9.2 B 表）----------
def _q(s):
    return urllib.parse.quote(s)


FB, YT, GH = "www.facebook.com", "www.youtube.com", "github.com"
SEARCH_CASES = [
    ("B1", "Safari", FB, "查貓咪專頁", [["open", "-a", "Safari", f"https://www.facebook.com/search/top?q={_q('貓咪專頁')}"]], "✅ 已在 Facebook 搜尋：貓咪專頁"),
    ("B2", "Safari", YT, "搜尋鋼琴教學", [["open", "-a", "Safari", f"https://www.youtube.com/results?search_query={_q('鋼琴教學')}"]], "✅ 已在 YouTube 搜尋：鋼琴教學"),
    ("B3", "Safari", GH, "查 mlx", [["open", "-a", "Safari", "https://github.com/search?q=mlx"]], "✅ 已在 GitHub 搜尋：mlx"),
    ("B4", "Safari", "www.google.com", "查貓咪", [["open", "-a", "Safari", _google("貓咪")]], "✅ 已在 Google 搜尋：貓咪"),
    ("B5", "Safari", "example.com", "查貓咪", [["open", "-a", "Safari", _google("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B6", "Safari", FB, "查台北天氣", [["open", "-a", "Safari", _google("台北天氣")]], "✅ 已開啟瀏覽器搜尋：台北天氣"),
    ("B7", "Safari", FB, "上網查貓咪", [["open", "-a", "Safari", _google("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B7b", "Safari", FB, "google 一下貓咪", [["open", "-a", "Safari", _google("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B8", "Finder", "", "查貓咪", [["open", _google("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B9", None, "", "查貓咪", [["open", _google("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B10", "Safari", FB, "打開 chrome 搜尋貓咪",
     [["open", "-a", "Google Chrome"], ["open", "-a", "Google Chrome", _google("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
]


@pytest.mark.parametrize("case,front_app,host,text,expected_calls,expected_log", SEARCH_CASES, ids=[c[0] for c in SEARCH_CASES])
def test_search_routing_table(monkeypatch, sh, front, case, front_app, host, text, expected_calls, expected_log):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    front(front_app, host=host)
    out = r.process_voice_command(text)
    assert sh == expected_calls
    assert out["steps"][-1]["logs"][-1] == expected_log and out["steps"][-1]["status"] == "ok"


def test_b6_b7_do_not_read_the_url(sh, front):
    """明說上網或查即時資訊：不需要知道現在在哪個網站，不花 200 ms 問網址"""
    reads = front("Safari", host=FB)
    r.process_voice_command("查台北天氣，上網查貓咪")
    assert ("host", "Safari") not in reads


def test_b11_search_with_app_scope_word_stays_local(monkeypatch, sh, front, installed_apps):
    installed_apps({"Xcode": "Xcode"})
    front("Safari", host=FB)
    r.process_voice_command("搜尋 Xcode 這個 App")
    assert sh == [["open", "-a", "Xcode"]]


def test_site_search_logs_host_only(sh, front):
    front("Safari", host=FB)
    step = r.process_voice_command("查貓咪專頁")["steps"][0]
    assert {k: v for k, v in step["context"].items() if not k.endswith("ms")} == {
        "app": "Safari", "used": True, "host": FB, "site": "Facebook"}


def test_site_search_failure_is_reported(monkeypatch, front):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    front("Safari", host=FB)
    step = r.process_voice_command("查貓咪專頁")["steps"][0]
    assert step["logs"] == ["❌ 開啟瀏覽器失敗（boom）"] and step["status"] == "exec_failed"


def test_b12_find_miss_with_browser_in_front_searches_web_without_llm(monkeypatch, find_env, front):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    front("Safari", host="example.com")
    step = r.process_voice_command("找 python 教學")["steps"][0]
    assert find_env.opens() == [["open", "-a", "Safari", _google("python 教學")]]
    assert step["escalated"]["to"] == "web" and "llm_ms" not in step and step["status"] == "ok"


def test_b12_find_miss_on_known_site_uses_site_search(monkeypatch, find_env, front):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    front("Safari", host=YT)
    r.process_voice_command("找鋼琴教學")
    assert find_env.opens() == [["open", "-a", "Safari", f"https://www.youtube.com/results?search_query={_q('鋼琴教學')}"]]


def test_b13_find_miss_without_browser_still_asks_the_model(monkeypatch, find_env, front):
    asked = []

    def fake_chat(*a, **k):
        asked.append(1)
        raise llm_client.LLMUnavailable("offline")
    monkeypatch.setattr(llm_client, "_chat", fake_chat)
    front("Finder")
    step = r.process_voice_command("找 python 教學")["steps"][0]
    assert asked and find_env.opens() == [] and "to" not in step["escalated"]


def test_b14_find_miss_with_local_evidence_never_goes_online(monkeypatch, find_env, front):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    front("Safari", host="example.com")
    step = r.process_voice_command("找驗收不存在.pdf")["steps"][0]
    assert find_env.opens() == [] and "本機沒有" in step["logs"][0] and "escalated" not in step


# ---------- A4：常用功能 ----------
def _keys(calls):
    """sh 記錄裡送鍵的腳本 → [(App, 'key code N using {...}')]"""
    out = []
    for cmd in calls:
        if cmd[0] == "osascript" and "key code" in cmd[-1]:
            lines = cmd[-1].splitlines()
            app = lines[3].split('"/')[1].split('.app"')[0]
            out.append((app, lines[4]))
    return out


CMD, SHIFT_CMD = " using {command down}", " using {command down, shift down}"
ACTION_CASES = [
    ("關閉視窗", "Finder", "", "key code 13" + CMD),
    ("關掉這個視窗", "Safari", "", "key code 13" + SHIFT_CMD),   # 瀏覽器 ⌘W 只關分頁
    ("把視窗關掉", VSCODE, "", "key code 13" + SHIFT_CMD),       # 編輯器 ⌘W 只關檔案
    ("關閉視窗", "Terminal", "", "key code 13" + CMD),         # Terminal 單一分頁時『關閉視窗』是 ⌘W（實機選單），⇧⌘W 沒有綁定
    ("關閉視窗", "iTerm", "", "key code 13" + SHIFT_CMD),
    ("開新視窗", "Safari", "", "key code 45" + CMD),
    ("新視窗", VSCODE, "", "key code 45" + SHIFT_CMD),            # 編輯器 ⌘N 是新檔案
    ("最小化", "Finder", "", "key code 46" + CMD),
    ("全螢幕", "Safari", "", "key code 3 using {command down, control down}"),
    ("離開全螢幕", "Safari", "", "key code 3 using {command down, control down}"),
    ("放大", "Safari", "", "key code 24" + CMD),
    ("幫我縮小一點", VSCODE, "", "key code 27" + CMD),
    ("上一頁", "Safari", "", "key code 33" + CMD),
    ("回上一頁", "Safari", "", "key code 33" + CMD),
    ("下一頁", "Safari", "", "key code 30" + CMD),
    ("回到頂端", "Safari", "", "key code 126" + CMD),
    ("往下捲", "Safari", "", "key code 121"),
    ("往上滑", "Safari", "", "key code 116"),
    ("暫停", "Safari", YT, "key code 40"),
    ("播放", "Music", "", "key code 49"),
    ("開終端機", VSCODE, "", "key code 50 using {control down}"),
    ("幫我打開終端機", VSCODE, "", "key code 50 using {control down}"),
    ("關掉終端機", VSCODE, "", "key code 50 using {control down}"),
    ("開側邊欄", VSCODE, "", "key code 11" + CMD),
    ("存檔", VSCODE, "", "key code 1" + CMD),
    ("新分頁", "Terminal", "", "key code 17" + CMD),
    ("開一個新的分頁", "iTerm", "", "key code 17" + CMD),
    ("清除畫面", "Terminal", "", "key code 40" + CMD),
]


@pytest.mark.parametrize("text,front_app,host,press", ACTION_CASES, ids=[f"{c[0]}@{c[1]}" for c in ACTION_CASES])
def test_front_action_sends_expected_key(monkeypatch, front, text, front_app, host, press):
    import subprocess
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    front(front_app, host=host)
    step = r.process_voice_command(text)["steps"][0]
    assert _keys(calls) == [(front_app, press)] and len(calls) == 1
    assert step["status"] == "ok" and step["logs"][0].startswith(f"✅ {front_app}：")
    assert step["context"]["app"] == front_app and step["context"]["used"] is True


def test_keystroke_script_checks_front_app_before_pressing():
    script = context.keystroke_script("Safari", {"code": 33, "mods": ["command"]})
    lines = script.splitlines()
    assert lines[3] == 'if f does not end with "/Safari.app" and f does not end with "/Safari.app/" then return "front:" & f'
    assert lines.index("key code 33 using {command down}") > 3  # 檢查在送鍵之前
    assert '\\"' in context.keystroke_script('a"b', {"code": 1, "mods": []})  # 名稱裡的引號要跳脫


def test_front_changed_before_keystroke_is_reported(monkeypatch, front):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "front:/Applications/Notes.app\n", ""))
    front("Safari")
    step = r.process_voice_command("上一頁")["steps"][0]
    assert step["logs"][0].startswith("❌ 最前面的 App 已經不是 Safari") and step["status"] == "exec_failed"


def test_keystroke_failure_is_reported(monkeypatch, front):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "not allowed"))
    front("Safari")
    step = r.process_voice_command("上一頁")["steps"][0]
    assert step["logs"][0].startswith("❌ 上一頁失敗：Safari（not allowed") and step["status"] == "exec_failed"


@pytest.mark.parametrize("text,front_app,host,expected", [
    ("上一頁", "Finder", "", "⚠️ 目前最前面的是 Finder，「上一頁」只支援瀏覽器"),
    ("存檔", "Safari", "", "⚠️ 目前最前面的是 Safari，「存檔」只支援編輯器"),
    ("清除畫面", VSCODE, "", f"⚠️ 目前最前面的是 {VSCODE}，「清除畫面」只支援終端機"),
    ("暫停", "Safari", "example.com", "⚠️ 目前最前面的是 Safari，「播放／暫停」只支援YouTube、Music、Spotify"),
    ("關閉視窗", None, "", "⚠️ 不知道要操作哪個視窗（讀不到最前面的 App），沒有執行「關閉視窗」"),
])
def test_front_action_out_of_scope_sends_nothing(monkeypatch, sh, front, text, front_app, host, expected):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    front(front_app, host=host)
    step = r.process_voice_command(text)["steps"][0]
    assert sh == [] and step["logs"] == [expected] and step["status"] == "exec_failed"


@pytest.mark.parametrize("text", [
    "不要關閉視窗", "別存檔", "剛剛全螢幕了", "已經存檔", "可以存檔嗎", "怎麼全螢幕", "上一頁是什麼",
    "關閉", "視窗", "存", "上一", "關閉所有視窗", "全螢幕截圖教學",
])
def test_front_action_guards_and_near_misses(text):
    m = FastPathMatcher().match(text)
    assert m is None or all(name != "front_action" for name, _ in m.actions), text


@pytest.mark.parametrize("text,front_app,expected", [
    ("開終端機", "Finder", [["open", "-a", "Terminal"]]),        # 前景不是編輯器：照舊開 Terminal.app
    ("開終端機", None, [["open", "-a", "Terminal"]]),
    ("關掉終端機", "Safari", [["osascript", "-e", 'tell application "Terminal" to quit']]),
    ("開終端機程式", VSCODE, [["open", "-a", "Terminal"]]),      # 明說要獨立的終端機
    ("打開獨立終端機", VSCODE, [["open", "-a", "Terminal"]]),
    ("打開 terminal", VSCODE, [["open", "-a", "Terminal"]]),
    ("新分頁", "Finder", None),                                   # 照舊走 browser_tab
])
def test_terminal_and_new_tab_fall_back_to_previous_behaviour(monkeypatch, sh, front, text, front_app, expected):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    front(front_app)
    step = r.process_voice_command(text)["steps"][0]
    assert _keys(sh) == []
    if expected is None:
        assert step["logs"][0].startswith("❌ 找不到可操作的瀏覽器")  # sh 的假輸出裡沒有執行中的瀏覽器
    else:
        assert sh == expected and step["status"] == "ok"


def test_new_tab_in_browser_unchanged(monkeypatch, front):
    import subprocess
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "Safari\nSafari,Finder\n", "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    front("Safari")
    step = r.process_voice_command("開新分頁")["steps"][0]
    assert step["logs"] == ["✅ Safari：已開新分頁"] and _keys(calls) == []


def test_every_shortcut_phrase_matches_and_match_is_pure(sh, front):
    """A5：詞庫裡每個說法都命中 front_action，而且比對不讀前景、不跑任何外部指令"""
    reads = front("Safari", host=FB)
    m = FastPathMatcher()
    assert len(context.SHORTCUTS) == 17
    for entry in context.SHORTCUTS.values():
        assert entry["bindings"] and all({"code", "when"} <= set(b) for b in entry["bindings"])
        for phrase in entry["phrases"]:
            hit = m.match(phrase)
            assert hit is not None and hit.actions[0][0] == "front_action" and hit.actions[0][1]["id"] == entry["id"], phrase
    for text in ("查貓咪專頁", "找 python 教學", "新分頁"):
        assert m.match(text) is not None
    assert reads == [] and sh == []


# ---------- Phase 4：Slow Path 的使用者訊息附上目前視窗（system 與 tools 不變，保住 prefix cache）----------
def _capture_llm(monkeypatch):
    seen = []

    def fake_chat(messages, **kw):
        seen.append(messages)
        return {"content": "好"}
    monkeypatch.setattr(llm_client, "_chat", fake_chat)
    return seen


def test_slow_path_user_message_carries_front_app_not_system(monkeypatch, sh, front):
    from mac_ai.router import slow_path
    seen = _capture_llm(monkeypatch)
    front("Visual Studio Code")
    r.process_voice_command("今天心情不錯")
    plan = seen[0]
    assert plan[0] == {"role": "system", "content": slow_path.SYSTEM_PROMPT}  # 前綴逐字不變
    assert plan[1]["content"] == "今天心情不錯\n\n## 目前視窗\nVisual Studio Code"


def test_slow_path_hint_does_not_ask_for_the_url(monkeypatch, sh, front):
    seen = _capture_llm(monkeypatch)
    reads = front("Safari", host="www.facebook.com")
    r.process_voice_command("今天心情不錯")
    assert seen[0][1]["content"].endswith("## 目前視窗\nSafari") and ("host", "Safari") not in reads


def test_slow_path_hint_includes_host_already_read_in_this_utterance(monkeypatch, sh, front):
    seen = _capture_llm(monkeypatch)
    front("Safari", host="www.facebook.com")
    r.process_voice_command("查貓咪專頁，今天心情不錯")
    assert seen[0][1]["content"].endswith("## 目前視窗\nSafari：www.facebook.com")


def test_slow_path_without_front_sends_the_utterance_unchanged(monkeypatch, sh, front):
    seen = _capture_llm(monkeypatch)
    front(None)
    r.process_voice_command("今天心情不錯")
    assert seen[0][1]["content"] == "今天心情不錯"


# ---------- 取代人工驗收 U2：語音流程（假 STT server → 閘門 → 佇列 → 執行）也用前景視窗 ----------
def test_u2_voice_loop_uses_front_window(sh, front):
    import threading

    from fake_stt import FakeAudio, FakeSttServer, wait_until

    from mac_ai.assistant import run_voice_loop
    from mac_ai.wake import WakeGate
    front("Safari", host="www.facebook.com")
    server = FakeSttServer(events=[{"type": "final", "text": "查貓咪專頁"}, {"type": "final", "text": "金音"}])
    done, stop = [], threading.Event()
    t = threading.Thread(target=run_voice_loop, args=(FakeAudio(), lambda text: done.append(r.process_voice_command(text)), stop),
                         kwargs={"url": server.url, "say": lambda s: None, "gate": WakeGate(enabled=False)}, daemon=True)
    t.start()
    try:
        assert wait_until(lambda: len(done) == 2)
    finally:
        stop.set()
        t.join(timeout=5)
        server.stop()
    assert sh == [["open", "-a", "Safari", f"https://www.facebook.com/search/top?q={_q('貓咪專頁')}"],
                  ["osascript", "-e", "set volume output muted true"]]  # 第二句是錯字『金音』→ 靜音


# ---------- 取代人工驗收 U3：常用功能的前半句不會被提早收句（真的 classify ＋ 注入時鐘）----------
@pytest.mark.parametrize("head,full", [("關掉這個", "關掉這個分頁"), ("關閉這個", "關閉這個視窗"), ("開新", "開新視窗"), ("打開新", "打開新分頁")])
def test_u3_half_sentence_waits_then_full_sentence_flushes(head, full):
    from test_endpoint_offline import Sim

    from mac_ai.stt.completeness import classify
    s = Sim(cls=classify)
    s.speech(0.8)
    s.partial(head)
    s.silence(1.0)  # 句中停頓 1 秒：完整句 0.35～0.6 秒就會收，半句要等 2 秒
    assert s.decisions == [], f"「{head}」被提早收句"
    s.speech(0.5)
    s.partial(full)
    waited = s.silence_until_decision()
    assert [(d.partial, d.cls) for d in s.decisions] == [(full, "complete")] and waited <= 0.7


@pytest.mark.parametrize("text", ["關閉視窗", "上一頁", "全螢幕", "存檔", "上一業"])
def test_u3_complete_shortcut_flushes_early(text):
    from test_endpoint_offline import Sim

    from mac_ai.stt.completeness import classify
    s = Sim(cls=classify)
    s.speech(0.6)
    s.partial(text)
    assert s.silence_until_decision() <= 0.7 and (s.decisions[0].partial, s.decisions[0].cls) == (text, "complete")
