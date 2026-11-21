"""前景視窗上下文整條路由：對象優先順序、站內搜尋分流、常用功能（front_action）、Slow Path 的使用者訊息附視窗"""
from urllib.parse import quote

import pytest

import mac_ai.router as r
from mac_ai.router import context, llm_client
from mac_ai.router.fast_path import FastPathMatcher
from support.expect import URL, VSCODE
from support.llm import FakeLLM, forbid_llm


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
    forbid_llm(monkeypatch)
    front(front_app)
    r.process_voice_command(text)
    assert sh[-1] == ["open", "-a", expected_editor, str(projects / "mac_ai_control")]


@pytest.mark.parametrize("text,front_app,expected", [
    ("打開 chrome 搜尋貓咪", "Safari", ["open", "-a", "Google Chrome", URL("貓咪")]),  # 明說＋同句剛開的
    ("搜尋貓咪", "Safari", ["open", "-a", "Safari", URL("貓咪")]),                    # 前景
    ("搜尋貓咪", "Finder", ["open", URL("貓咪")]),                                    # 前景不是瀏覽器 → 預設
    ("搜尋貓咪", None, ["open", URL("貓咪")]),
])
def test_search_browser_priority(monkeypatch, sh, front, text, front_app, expected):
    forbid_llm(monkeypatch)
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


# ---------- A3：搜尋分流（計劃書 §9.2 B 表）----------
FB, YT, GH = "www.facebook.com", "www.youtube.com", "github.com"
SEARCH_CASES = [
    ("B1", "Safari", FB, "查貓咪專頁", [["open", "-a", "Safari", f"https://www.facebook.com/search/top?q={quote('貓咪專頁')}"]], "✅ 已在 Facebook 搜尋：貓咪專頁"),
    ("B2", "Safari", YT, "搜尋鋼琴教學", [["open", "-a", "Safari", f"https://www.youtube.com/results?search_query={quote('鋼琴教學')}"]], "✅ 已在 YouTube 搜尋：鋼琴教學"),
    ("B3", "Safari", GH, "查 mlx", [["open", "-a", "Safari", "https://github.com/search?q=mlx"]], "✅ 已在 GitHub 搜尋：mlx"),
    ("B4", "Safari", "www.google.com", "查貓咪", [["open", "-a", "Safari", URL("貓咪")]], "✅ 已在 Google 搜尋：貓咪"),
    ("B5", "Safari", "example.com", "查貓咪", [["open", "-a", "Safari", URL("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B6", "Safari", FB, "查台北天氣", [["open", "-a", "Safari", URL("台北天氣")]], "✅ 已開啟瀏覽器搜尋：台北天氣"),
    ("B7", "Safari", FB, "上網查貓咪", [["open", "-a", "Safari", URL("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B7b", "Safari", FB, "google 一下貓咪", [["open", "-a", "Safari", URL("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B8", "Finder", "", "查貓咪", [["open", URL("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B9", None, "", "查貓咪", [["open", URL("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
    ("B10", "Safari", FB, "打開 chrome 搜尋貓咪",
     [["open", "-a", "Google Chrome"], ["open", "-a", "Google Chrome", URL("貓咪")]], "✅ 已開啟瀏覽器搜尋：貓咪"),
]


@pytest.mark.parametrize("case,front_app,host,text,expected_calls,expected_log", SEARCH_CASES, ids=[c[0] for c in SEARCH_CASES])
def test_search_routing_table(monkeypatch, sh, front, case, front_app, host, text, expected_calls, expected_log):
    forbid_llm(monkeypatch)
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
    forbid_llm(monkeypatch)
    front("Safari", host="example.com")
    step = r.process_voice_command("找 python 教學")["steps"][0]
    assert find_env.opens() == [["open", "-a", "Safari", URL("python 教學")]]
    assert step["escalated"]["to"] == "web" and "llm_ms" not in step and step["status"] == "ok"


def test_b12_find_miss_on_known_site_uses_site_search(monkeypatch, find_env, front):
    forbid_llm(monkeypatch)
    front("Safari", host=YT)
    r.process_voice_command("找鋼琴教學")
    assert find_env.opens() == [["open", "-a", "Safari", f"https://www.youtube.com/results?search_query={quote('鋼琴教學')}"]]


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
    forbid_llm(monkeypatch)
    front("Safari", host="example.com")
    step = r.process_voice_command("找驗收不存在.pdf")["steps"][0]
    assert find_env.opens() == [] and "本機沒有" in step["logs"][0] and "escalated" not in step


# ---------- A4：常用功能（底層執行：native 的呼叫由 conftest 攔下記錄）----------
ACTION_CASES = [
    # (語句, 前景, host, 預期的底層呼叫)
    ("關閉視窗", "Finder", "", ["window close"]),
    ("關掉這個視窗", "Safari", "", ["window close"]),         # 一律按視窗的關閉鈕：各 App 行為一致（不再有 ⌘W／⇧⌘W 之分）
    ("把視窗關掉", VSCODE, "", ["window close"]),
    ("關閉視窗", "Terminal", "", ["window close"]),
    ("開新視窗", "Safari", "", ["menu N command"]),
    ("新視窗", VSCODE, "", ["menu N command+shift"]),          # 編輯器 ⌘N 是新檔案
    ("最小化", "Finder", "", ["window minimize"]),
    ("全螢幕", "Safari", "", ["window fullscreen"]),
    ("離開全螢幕", "Safari", "", ["window fullscreen"]),
    ("放大", "Safari", "", ["menu += command"]),
    ("幫我縮小一點", VSCODE, "", ["menu - command"]),
    ("上一頁", "Safari", "", ["menu [ command"]),
    ("回上一頁", "Safari", "", ["menu [ command"]),
    ("下一頁", "Safari", "", ["menu ] command"]),
    ("回到頂端", "Safari", "", ["key 126 command"]),             # 沒有選單項目：送按鍵（⌘ 組合鍵不受輸入法影響）
    ("往下捲", "Safari", "", ["key 121"]),
    ("往上滑", "Safari", "", ["key 116"]),
    ("暫停", "Safari", YT, [r"button ^暫停 \(k\)$|^Pause \(k\)$"]),  # 按播放器的按鈕，不送 k（會被注音吃掉）；使用者 2026-11-21 決定：暫停只按暫停鈕
    ("繼續播放", "Safari", YT, [r"button 播放.*k$|^Play \(k\)$"]),
    ("播放", "Safari", YT, [r"button ^(?:播放|暫停|Play|Pause)\s*(?:\(k\)|鍵盤快速鍵\s*k|keyboard shortcut k)$"]),  # 沒有方向：維持切換
    ("開終端機", VSCODE, "", ["menu 檢視/終端"]),                # 按選單，不送 ⌃`（會被注音吃掉）
    ("幫我打開終端機", VSCODE, "", ["menu 檢視/終端"]),
    ("關掉終端機", VSCODE, "", ["menu 檢視/終端"]),
    ("開側邊欄", VSCODE, "", ["menu B command"]),
    ("存檔", VSCODE, "", ["menu S command"]),
    ("新分頁", "Terminal", "", ["menu T command"]),
    ("開一個新的分頁", "iTerm", "", ["menu T command"]),
    ("清除畫面", "Terminal", "", ["menu K command"]),
]


@pytest.mark.parametrize("text,front_app,host,expected", ACTION_CASES, ids=[f"{c[0]}@{c[1]}" for c in ACTION_CASES])
def test_front_action_runs_native_operation(monkeypatch, sh, native_calls, front, text, front_app, host, expected):
    forbid_llm(monkeypatch)
    front(front_app, host=host)
    step = r.process_voice_command(text)["steps"][0]
    assert native_calls == expected and sh == []  # 沒有任何 osascript／模擬按鍵腳本
    assert step["status"] == "ok" and step["logs"][0].startswith(f"✅ {front_app}：")
    assert step["context"]["app"] == front_app and step["context"]["used"] is True and step["context"]["method"]


@pytest.mark.parametrize("text,app,script", [
    ("播放", "Music", "playpause"), ("播放暫停", "Spotify", "playpause"),  # 沒有方向：切換
    ("暫停", "Music", "pause"), ("繼續播放", "Music", "play"), ("暫停", "Spotify", "pause"), ("繼續", "Spotify", "play"),  # 使用者 2026-11-21 決定
])
def test_music_play_pause_uses_the_apps_own_command(sh, native_calls, front, text, app, script):
    front(app)
    step = r.process_voice_command(text)["steps"][0]
    assert sh == [["osascript", "-e", f'tell application "{app}" to {script}']] and native_calls == []
    assert step["context"]["method"] == "App 指令" and step["status"] == "ok"


def test_menu_missing_falls_back_to_key_only_when_front_unchanged(sh, native_calls, front):
    reads = front("Safari")
    native_calls.result = "missing"  # 選單裡找不到 ⌘[ 的項目（例如 App 改了快捷鍵）
    step = r.process_voice_command("上一頁")["steps"][0]
    assert native_calls == ["menu [ command", "key 33 command"] and reads.count("Safari") == 2  # 送按鍵前再讀一次前景
    assert step["context"]["method"] == "按鍵"


def test_front_changed_before_key_is_reported(monkeypatch, sh, native_calls, front):
    front("Safari")
    native_calls.result = "missing"
    seq = iter([context.FrontContext("Safari", "browser", 4242), context.FrontContext("Notes", "other", 777)])
    monkeypatch.setattr(context, "read_front", lambda run: next(seq))
    step = r.process_voice_command("上一頁")["steps"][0]
    assert native_calls == ["menu [ command"]  # 前景變了：不送按鍵
    assert step["logs"][0] == "❌ 最前面的 App 已經不是 Safari，沒有執行「上一頁」" and step["status"] == "exec_failed"


@pytest.mark.parametrize("result,log", [
    ("missing", "❌ 關閉視窗失敗：Finder 找不到可操作的視窗"),
    ("error", "❌ 關閉視窗失敗：Finder（視窗；請確認已授權輔助使用）"),
])
def test_native_failure_is_reported(sh, native_calls, front, result, log):
    front("Finder")
    native_calls.result = result
    step = r.process_voice_command("關閉視窗")["steps"][0]
    assert step["logs"] == [log] and step["status"] == "exec_failed"


def test_disabled_menu_item_is_reported(monkeypatch, sh, front):
    from mac_ai.router import native
    monkeypatch.setattr(native, "press_menu", lambda pid, chars="", mods=(), path=None: ("disabled", "上一頁"))
    front("Safari")
    step = r.process_voice_command("上一頁")["steps"][0]
    assert step["logs"] == ["⚠️ Safari 目前不能「上一頁」（選單項目停用）"] and step["status"] == "exec_failed"


def test_missing_pid_does_nothing(monkeypatch, sh, native_calls):
    monkeypatch.setattr(context, "read_front", lambda run: context.FrontContext("Safari", "browser", 0))
    step = r.process_voice_command("上一頁")["steps"][0]
    assert native_calls == [] and step["status"] == "exec_failed"


@pytest.mark.parametrize("text,front_app,host,expected", [
    ("上一頁", "Finder", "", "⚠️ 目前最前面的是 Finder，「上一頁」只支援瀏覽器"),
    ("存檔", "Safari", "", "⚠️ 目前最前面的是 Safari，「存檔」只支援編輯器"),
    ("清除畫面", VSCODE, "", f"⚠️ 目前最前面的是 {VSCODE}，「清除畫面」只支援終端機"),
    ("關閉視窗", None, "", "⚠️ 不知道要操作哪個視窗（讀不到最前面的 App），沒有執行「關閉視窗」"),
])
def test_front_action_out_of_scope_does_nothing(monkeypatch, sh, native_calls, front, text, front_app, host, expected):
    forbid_llm(monkeypatch)
    front(front_app, host=host)
    step = r.process_voice_command(text)["steps"][0]
    assert sh == [] and native_calls == [] and step["logs"] == [expected] and step["status"] == "exec_failed"


@pytest.mark.parametrize("text", [
    "不要關閉視窗", "別存檔", "剛剛全螢幕了", "已經存檔", "可以存檔嗎", "怎麼全螢幕", "上一頁是什麼",
    "關閉", "視窗", "存", "上一", "關閉所有視窗", "全螢幕截圖教學",
])
def test_front_action_guards_and_near_misses(text):
    m = FastPathMatcher().match(text)
    assert m is None or all(name != "front_action" for name, _ in m.actions), text


@pytest.mark.parametrize("text,sid", [
    ("再往下滑", "scroll_down"), ("多往下滑", "scroll_down"), ("繼續往下滑", "scroll_down"), ("往下滑一點", "scroll_down"),
    ("往下滑多一點", "scroll_down"), ("往下翻", "scroll_down"), ("滑下去", "scroll_down"), ("捲下去", "scroll_down"),
    ("再往上滑一點", "scroll_up"), ("往上翻", "scroll_up"), ("再放大一點", "zoom_in"), ("稍微縮小", "zoom_out"),
    ("幫我再往下滑一下", "scroll_down"),
])
def test_front_action_with_degree_words(text, sid):
    """常用功能說法前後多了『再／多／繼續／稍微』『一點／多一點』：拿掉後整句等於詞庫說法才命中"""
    m = FastPathMatcher().match(text)
    assert m is not None and m.actions == [("front_action", {"id": sid})], text


@pytest.mark.parametrize("text", [
    "往下", "往上", "再往下", "再一點", "多", "再", "不要再往下滑", "別再往下滑了", "再往下滑嗎", "怎麼再放大一點", "再往下滑到底",
])
def test_front_action_degree_words_near_misses(text):
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
def test_terminal_and_new_tab_fall_back_to_previous_behaviour(monkeypatch, sh, native_calls, front, text, front_app, expected):
    forbid_llm(monkeypatch)
    front(front_app)
    step = r.process_voice_command(text)["steps"][0]
    assert native_calls == []
    if expected is None:
        assert step["logs"][0].startswith("❌ 找不到可操作的瀏覽器")  # sh 的假輸出裡沒有執行中的瀏覽器
    else:
        assert sh == expected and step["status"] == "ok"


def test_new_tab_in_browser_unchanged(monkeypatch, native_calls, front):
    import subprocess
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "Safari\nSafari,Finder\n", "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    front("Safari")
    step = r.process_voice_command("開新分頁")["steps"][0]
    assert step["logs"] == ["✅ Safari：已開新分頁"] and native_calls == []


_KEY_ONLY_OK = {"page_top", "scroll_down", "scroll_up"}  # 翻頁鍵、⌘↑：沒有選單項目，注音下實測不受影響


def test_every_shortcut_phrase_matches_and_match_is_pure(sh, native_calls, front):
    """A5：詞庫裡每個說法都命中 front_action，而且比對不讀前景、不跑任何外部指令；
    每個綁定都要有底層做法，送按鍵只允許 ⌘ 組合鍵或沒有字元的鍵（不會被輸入法吃掉）"""
    reads = front("Safari", host=FB)
    m = FastPathMatcher()
    assert len(context.SHORTCUTS) == 19  # 含 next_track、prev_track（音樂播放控制）
    for entry in context.SHORTCUTS.values():
        assert entry["bindings"]
        for b in entry["bindings"]:
            ways = [k for k in ("window", "path", "button", "script", "char", "code") if k in b]
            assert ways, (entry["id"], b)
            if "code" in b and "char" not in b:
                assert entry["id"] in _KEY_ONLY_OK, entry["id"]
            if "code" in b:
                assert "command" in b.get("mods", []) or entry["id"] in _KEY_ONLY_OK, entry["id"]
        for phrase in entry["phrases"]:
            hit = m.match(phrase)
            assert hit is not None and hit.actions[0][0] == "front_action" and hit.actions[0][1]["id"] == entry["id"], phrase
    for text in ("查貓咪專頁", "找 python 教學", "新分頁"):
        assert m.match(text) is not None
    assert reads == [] and sh == [] and native_calls == []


# ---------- Phase 4：Slow Path 的使用者訊息附上目前視窗（system 與 tools 不變，保住 prefix cache）----------
def test_slow_path_user_message_carries_front_app_not_system(monkeypatch, sh, front):
    from mac_ai.router import slow_path
    seen = FakeLLM(plan="好", chat="好").install(monkeypatch).calls
    front("Visual Studio Code")
    r.process_voice_command("今天心情不錯")
    plan = seen[0]
    assert plan[0] == {"role": "system", "content": slow_path.SYSTEM_PROMPT}  # 前綴逐字不變
    assert plan[1]["content"] == "今天心情不錯\n\n## 目前視窗\nVisual Studio Code"


def test_slow_path_hint_does_not_ask_for_the_url(monkeypatch, sh, front):
    seen = FakeLLM(plan="好", chat="好").install(monkeypatch).calls
    reads = front("Safari", host="www.facebook.com")
    r.process_voice_command("今天心情不錯")
    assert seen[0][1]["content"].endswith("## 目前視窗\nSafari") and ("host", "Safari") not in reads


def test_slow_path_hint_includes_host_already_read_in_this_utterance(monkeypatch, sh, front):
    seen = FakeLLM(plan="好", chat="好").install(monkeypatch).calls
    front("Safari", host="www.facebook.com")
    r.process_voice_command("查貓咪專頁，今天心情不錯")
    assert seen[0][1]["content"].endswith("## 目前視窗\nSafari：www.facebook.com")


def test_slow_path_without_front_sends_the_utterance_unchanged(monkeypatch, sh, front):
    seen = FakeLLM(plan="好", chat="好").install(monkeypatch).calls
    front(None)
    r.process_voice_command("今天心情不錯")
    assert seen[0][1]["content"] == "今天心情不錯"
