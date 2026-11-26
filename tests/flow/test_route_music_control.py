"""播放控制（下一首、上一首、暫停、繼續；music-play 計畫 §4.3、§7.1 K1～K5、K9）：前景 App、Safari 音樂分頁、媒體鍵的控制鏈。
底層全部是假的：subprocess（pgrep、osascript）、輔助使用 API、媒體鍵；不得真的對 Safari 按按鈕或送媒體鍵"""
import re

import pytest

import mac_ai.router as r
from mac_ai.router import context, front_tools, music_tools
from mac_ai.router.fast_path import FastPathMatcher
from support.expect import fast_matches
from support.llm import forbid_llm
from support.music import SAFARI_PID, WATCH, _control
from support.music import bg  # noqa: F401  fixture

P_NEXT, P_PREV = r"\(SHIFT\+n\)$", r"\(SHIFT\+p\)$"
P_PAUSE, P_PLAY = r"^暫停 \(k\)$|^Pause \(k\)$", r"播放.*k$|^Play \(k\)$"
P_TOGGLE = r"^暫停 \(k\)$|^Pause \(k\)$|播放.*k$|^Play \(k\)$"
LEGACY_YT_BUTTON = r"^(?:播放|暫停|Play|Pause)\s*(?:\(k\)|鍵盤快速鍵\s*k|keyboard shortcut k)$"


# ---------- K1：路由 ----------
K1_CASES = [
    ("下一首", "next_track", None), ("下一首歌", "next_track", None), ("切下一首", "next_track", None), ("跳下一首", "next_track", None),
    ("上一首", "prev_track", None), ("上一首歌", "prev_track", None), ("切到上一首", "prev_track", None),
    ("暫停", "play_pause", "pause"), ("暫停播放", "play_pause", "pause"), ("暫停影片", "play_pause", "pause"),
    ("繼續", "play_pause", "resume"), ("繼續播放", "play_pause", "resume"),
    ("播放", "play_pause", None), ("播放暫停", "play_pause", None),
]


@pytest.mark.parametrize("text,sid,direction", K1_CASES, ids=[c[0] for c in K1_CASES])
def test_k1_fast_path_hits_front_action(text, sid, direction):
    matches = fast_matches(text)
    assert len(matches) == 1 and matches[0] is not None
    expected = {"id": sid, **({"direction": direction} if direction else {})}
    assert matches[0].actions == [("front_action", expected)]


@pytest.mark.parametrize("text", [c[0] for c in K1_CASES])
def test_k1_routes_without_llm(monkeypatch, bg, text):
    forbid_llm(monkeypatch)
    step = r.process_voice_command(text)["steps"][0]
    assert step["status"] == "ok"


@pytest.mark.parametrize("text", [
    "不要下一首", "別暫停", "我不想繼續", "剛剛暫停了", "已經下一首了", "下一首是什麼", "要繼續嗎", "怎麼暫停", "下一",
    "繼續往下滑", "繼續聊", "暫停一下再說",
])
def test_k1_guards_and_near_misses_do_not_control_music(text):
    m = FastPathMatcher().match(text)
    ids = [a[1].get("id") for a in (m.actions if m else []) if a[0] == "front_action"]
    assert not set(ids) & {"next_track", "prev_track", "play_pause"}, text


def test_k1_continue_scroll_is_unchanged():
    m = FastPathMatcher().match("繼續往下滑")
    assert m is not None and m.actions == [("front_action", {"id": "scroll_down"})]


def test_k1_directions_are_phrases_of_their_entry_and_background_plans_are_complete():
    for e in context.SHORTCUTS.values():
        for phrases in e.get("directions", {}).values():
            assert set(phrases) <= set(e["phrases"]), e["id"]
        for name, plan in e.get("background", {}).items():
            assert name in ("default", *e.get("directions", {})), (e["id"], name)
            assert {"label", "button", "key"} <= set(plan) and plan["key"] in ("play", "next", "previous"), (e["id"], name)
            for k in ("button", "done_when"):
                if k in plan:
                    re.compile(plan[k])
        assert "default" in e.get("background", {"default": 0})


@pytest.mark.parametrize("pattern,yes,no", [
    (P_NEXT, ["下一個 (SHIFT+n)", "Next (SHIFT+n)"], ["前往下一頁", "下一個", "Next"]),
    (P_PREV, ["上一部 (SHIFT+p)", "Previous (SHIFT+p)"], ["返回上一頁", "上一部"]),
    (P_PAUSE, ["暫停 (k)", "Pause (k)"], ["播放鍵盤快速鍵k", "Play (k)", "暫停影片"]),
    (P_PLAY, ["播放鍵盤快速鍵k", "Play (k)"], ["暫停 (k)", "Pause (k)"]),
])
def test_k3_button_patterns(pattern, yes, no):
    assert all(re.search(pattern, t) for t in yes) and not any(re.search(pattern, t) for t in no)


# ---------- K2：前景是 Music／Spotify：App 指令，不送媒體鍵 ----------
@pytest.mark.parametrize("text,app,script,op", [
    ("下一首", "Music", "next track", "next"), ("上一首", "Spotify", "previous track", "prev"),
    # 使用者 2026-11-21 決定：暫停／繼續有方向（pause／play）；沒有方向的『播放』『播放暫停』維持 playpause
    ("暫停", "Music", "pause", "pause"), ("暫停播放", "Spotify", "pause", "pause"),
    ("繼續播放", "Music", "play", "resume"), ("繼續", "Spotify", "play", "resume"),
    ("播放", "Music", "playpause", "toggle"), ("播放暫停", "Spotify", "playpause", "toggle"),
])
def test_k2_music_and_spotify_use_app_command(bg, native_calls, front, text, app, script, op):
    front(app)
    step, control = _control(text)
    assert bg.cmds == [["osascript", "-e", f'tell application "{app}" to {script}']] and native_calls == []
    assert step["status"] == "ok" and control == {"op": op, "source": "app"}


# ---------- K3：Safari 的音樂分頁在背景視窗：按那個視窗的按鈕 ----------
@pytest.mark.parametrize("text,pattern,op", [
    ("下一首", P_NEXT, "next"), ("上一首", P_PREV, "prev"), ("暫停", P_PAUSE, "pause"), ("繼續", P_PLAY, "resume"),
    ("播放", P_TOGGLE, "toggle"),
])
def test_k3_background_window_button(bg, native_calls, front, text, pattern, op):
    front("Visual Studio Code")
    step, control = _control(text)
    assert native_calls == [f"button_in_window {pattern}"] and bg.pids == [SAFARI_PID]  # 只按一次、對 Safari 的行程、不送媒體鍵
    assert step["status"] == "ok" and control == {"op": op, "source": "ax"} and step["logs"][0].startswith("✅")


def test_k3_never_launches_or_activates_anything(bg, front):
    front("Visual Studio Code")
    r.process_voice_command("下一首")
    assert [c[0] for c in bg.cmds] == ["pgrep", "osascript"]
    assert bg.cmds[0] == ["pgrep", "-x", "Safari"]
    assert bg.cmds[1][2] == music_tools._SAFARI_TABS_SCRIPT and bg.cmds[1][2].startswith('if application "Safari" is running then')  # 競態時也不會把 Safari 開起來
    assert "activate" not in bg.cmds[1][2] and "open" not in {c[0] for c in bg.cmds}


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc",
    "https://music.youtube.com/watch?v=abc&list=RDabc",
    "https://youtube.com/watch?v=abc&t=10s",
])
def test_k3_music_tab_urls(bg, native_calls, front, url):
    front("Visual Studio Code")
    bg.url = url
    _control("下一首")
    assert native_calls == [f"button_in_window {P_NEXT}"]


NON_MUSIC_URLS = [
    "https://www.youtube.com/", "https://www.youtube.com/shorts/abc", "https://www.youtube.com/results?search_query=x",
    "https://www.youtube.com/watch", "https://notyoutube.com/watch?v=abc", "https://example.com/watch?v=abc",
    "https://example.com/?u=https://www.youtube.com/watch?v=abc", "file:///watch?v=abc", "",
]


@pytest.mark.parametrize("url", NON_MUSIC_URLS + [None])
@pytest.mark.parametrize("text", ["下一首", "暫停", "繼續"])
def test_k3_no_music_tab_anywhere_skips_ax_and_media_key(bg, native_calls, front, url, text):
    """使用者 2026-11-21 決定：Safari 沒有任何 YouTube 影片分頁（或讀不到）→ 不按、不送媒體鍵，回 ⚠️"""
    front("Visual Studio Code")
    bg.url = url
    step, control = _control(text)
    assert native_calls == [] and control is None
    assert step["logs"] == ["⚠️ 找不到正在播放的音樂"] and step["status"] == "exec_failed"


def test_k3_all_tabs_are_non_music_across_windows(bg, native_calls, front):
    front("Visual Studio Code")
    bg.url, bg.tabs = "https://example.com/", NON_MUSIC_URLS
    assert _control("下一首")[0]["logs"] == ["⚠️ 找不到正在播放的音樂"] and native_calls == []


# ---------- H4：音樂分頁在背景分頁（目前分頁不是音樂）→ 不按按鈕，送媒體鍵 ----------
@pytest.mark.parametrize("text,key,op", [("下一首", "next", "next"), ("上一首", "previous", "prev"),
                                         ("暫停", "play", "pause"), ("繼續", "play", "resume"), ("播放", "play", "toggle")])
@pytest.mark.parametrize("current", ["https://example.com/", "", "https://www.youtube.com/"])
def test_h4_music_tab_in_background_tab_sends_media_key(bg, native_calls, front, text, key, op, current):
    front("Visual Studio Code")
    bg.url, bg.tabs = current, ["https://example.com/a", "https://music.youtube.com/watch?v=abc&list=RDabc"]
    step, control = _control(text)
    assert native_calls == [f"mediakey {key}"] and control == {"op": op, "source": "mediakey"} and step["status"] == "ok"


def test_h4_unreadable_current_tab_still_finds_music_tab_elsewhere(bg, native_calls, front):
    front("Visual Studio Code")
    bg.url, bg.tabs = "", [WATCH]  # 最近使用的視窗是 0 個分頁的幽靈視窗（目前分頁讀不到），音樂分頁在別的視窗
    assert _control("下一首")[1] == {"op": "next", "source": "mediakey"} and native_calls == ["mediakey next"]


def test_h4_media_key_failure_is_reported(bg, native_calls, front):
    front("Visual Studio Code")
    bg.url, bg.tabs, bg.key = "https://example.com/", [WATCH], "error"
    step, control = _control("下一首")
    assert step["logs"] == ["⚠️ 找不到正在播放的音樂"] and native_calls == ["mediakey next"] and control is None


# ---------- K4：AX 按不到 → 一次媒體鍵 ----------
@pytest.mark.parametrize("text,pattern,key,op", [
    ("下一首", P_NEXT, "next", "next"), ("上一首", P_PREV, "previous", "prev"),
])
def test_k4_ax_missing_sends_one_media_key(bg, native_calls, front, text, pattern, key, op):
    front("Visual Studio Code")
    bg.ax = "missing"
    step, control = _control(text)
    assert native_calls == [f"button_in_window {pattern}", f"mediakey {key}"]
    assert control == {"op": op, "source": "mediakey"} and step["status"] == "ok"


def test_k4_ax_error_also_falls_back_to_media_key(bg, native_calls, front):
    front("Visual Studio Code")
    bg.ax = "error"
    assert _control("下一首")[1]["source"] == "mediakey" and native_calls[-1] == "mediakey next"


@pytest.mark.parametrize("text", ["下一首", "上一首", "暫停", "繼續", "播放"])
def test_k4_safari_not_running_does_nothing(bg, native_calls, front, text):
    """使用者 2026-11-21 決定：Safari 沒開 → 只查 pgrep；沒有 osascript（Safari 不會被啟動）、不送媒體鍵"""
    front("Visual Studio Code")
    bg.pid = 0
    step, control = _control(text)
    assert bg.cmds == [["pgrep", "-x", "Safari"]] and native_calls == []
    assert step["logs"] == ["⚠️ 找不到正在播放的音樂"] and control is None


def test_k4_unreadable_front_still_controls_background(bg, native_calls, front):
    front(None)
    assert _control("下一首")[1] == {"op": "next", "source": "ax"} and native_calls == [f"button_in_window {P_NEXT}"]


# ---------- 暫停／繼續的方向：媒體鍵 play 是切換，看得出已經是目標狀態就不送 ----------
def test_pause_when_already_paused_does_not_toggle_back_to_playing(bg, native_calls, front):
    front("Visual Studio Code")
    bg.ax, bg.label = "missing", "播放鍵盤快速鍵k"  # 找不到『暫停』鈕，但畫面上是『播放』鈕：已經暫停了
    step, control = _control("暫停")
    assert native_calls == [f"button_in_window {P_PAUSE}", f"label {P_PLAY}"]  # 沒有任何媒體鍵
    assert control == {"op": "pause", "source": "ax", "already": True} and step["status"] == "ok" and "已經" in step["logs"][0]


def test_resume_when_already_playing_does_not_toggle_back_to_paused(bg, native_calls, front):
    front("Visual Studio Code")
    bg.ax, bg.label = "missing", "暫停 (k)"
    step, control = _control("繼續播放")
    assert native_calls == [f"button_in_window {P_PLAY}", f"label {P_PAUSE}"]
    assert control == {"op": "resume", "source": "ax", "already": True}


@pytest.mark.parametrize("text,key", [("暫停", P_PAUSE), ("繼續", P_PLAY)])
def test_unknown_state_on_music_tab_sends_media_key_once(bg, native_calls, front, text, key):
    front("Visual Studio Code")
    bg.ax = "missing"  # 兩種按鈕都看不到（頁面還沒載好等）：狀態不明，照計畫送一次
    step, control = _control(text)
    assert [c for c in native_calls if c.startswith("mediakey")] == ["mediakey play"] and control["source"] == "mediakey"


def test_pause_without_direction_toggles_with_both_buttons(bg, native_calls, front):
    front("Visual Studio Code")
    assert _control("播放暫停")[1] == {"op": "toggle", "source": "ax"} and native_calls == [f"button_in_window {P_TOGGLE}"]


# ---------- K5：找不到 ----------
@pytest.mark.parametrize("text,key", [("下一首", "next"), ("上一首", "previous"), ("暫停", "play"), ("繼續", "play")])
def test_k5_music_tab_exists_but_media_key_fails(bg, native_calls, front, text, key):
    front("Visual Studio Code")
    bg.url, bg.tabs, bg.ax, bg.key = "https://example.com/", [WATCH], "missing", "error"
    step, control = _control(text)
    assert step["logs"] == ["⚠️ 找不到正在播放的音樂"] and step["status"] == "exec_failed" and control is None
    assert native_calls == [f"mediakey {key}"]  # 只試一次


def test_k5_music_tab_ax_missing_and_media_key_fails(bg, native_calls, front):
    front("Visual Studio Code")
    bg.ax, bg.key = "missing", "error"
    step = r.process_voice_command("下一首")["steps"][0]
    assert step["logs"] == ["⚠️ 找不到正在播放的音樂"] and native_calls == [f"button_in_window {P_NEXT}", "mediakey next"]


# ---------- K9：前景是 YouTube 分頁 ----------
# 使用者 2026-11-21 決定：暫停／繼續有方向（和背景鏈同一組 pattern）；沒有方向的『播放』維持切換 pattern；下一首／上一首不變
@pytest.mark.parametrize("text,pattern,op", [
    ("暫停", P_PAUSE, "pause"), ("暫停影片", P_PAUSE, "pause"), ("繼續播放", P_PLAY, "resume"), ("繼續", P_PLAY, "resume"),
    ("播放", LEGACY_YT_BUTTON, "toggle"), ("播放暫停", LEGACY_YT_BUTTON, "toggle"),
    ("下一首", P_NEXT, "next"), ("上一首", P_PREV, "prev"),
])
def test_k9_front_youtube_tab_presses_front_window_button(bg, native_calls, front, text, pattern, op):
    front("Safari", host="www.youtube.com")
    step, control = _control(text)
    assert native_calls == [f"button {pattern}"] and bg.cmds == []  # 前景按鈕（press_button），不走背景控制鏈
    assert step["status"] == "ok" and control == {"op": op, "source": "ax"} and step["logs"][0].startswith("✅ Safari：")


@pytest.mark.parametrize("text,pattern,done,op,label", [
    ("暫停", P_PAUSE, P_PLAY, "pause", "暫停"), ("繼續播放", P_PLAY, P_PAUSE, "resume", "繼續播放"),
])
def test_k9_front_youtube_already_in_target_state_presses_nothing(bg, native_calls, front, text, pattern, done, op, label):
    front("Safari", host="www.youtube.com")
    native_calls.result, bg.label = "missing", "x"  # 要按的鈕不在，但對立的鈕在：已經是目標狀態
    step, control = _control(text)
    assert native_calls == [f"button {pattern}", f"label {done}"]  # 只有一次按、一次讀文字，沒有任何第二次按、媒體鍵
    assert control == {"op": op, "source": "ax", "already": True} and step["status"] == "ok"
    assert step["logs"] == [f"✅ Safari：{label}：已經是這個狀態"]


@pytest.mark.parametrize("text,pattern", [("暫停", P_PAUSE), ("繼續", P_PLAY)])
def test_k9_front_youtube_neither_button_found_is_a_failure(bg, native_calls, front, text, pattern):
    front("Safari", host="www.youtube.com")
    native_calls.result, bg.label = "missing", None
    step, control = _control(text)
    assert step["status"] == "exec_failed" and step["logs"][0].startswith("❌") and control is None


def test_k9_toggle_without_direction_never_claims_already(bg, native_calls, front):
    front("Safari", host="www.youtube.com")
    native_calls.result = "missing"
    step, _ = _control("播放")
    assert native_calls == [f"button {LEGACY_YT_BUTTON}"] and step["logs"][0].startswith("❌")  # 沒有 done_when：不讀按鈕文字、不謊稱成功


def test_k9_by_direction_entries_match_background_plans():
    """by_direction 的鈕與背景鏈同一組 pattern；Music／Spotify 只用 App 指令 pause／play"""
    entry = context.SHORTCUTS["play_pause"]
    yt = next(b for b in entry["bindings"] if b["when"].get("site") == "YouTube")
    for d in ("pause", "resume"):
        assert {k: yt["by_direction"][d][k] for k in ("button", "done_when")} == {k: entry["background"][d][k] for k in ("button", "done_when")}
    app = next(b for b in entry["bindings"] if "apps" in b["when"])
    assert app["script"] == "playpause" and {d: v["script"] for d, v in app["by_direction"].items()} == {"pause": "pause", "resume": "play"}
    for e in context.SHORTCUTS.values():
        for b in e["bindings"]:
            assert set(b.get("by_direction", {})) <= set(e.get("directions", {})), e["id"]
    assert not any("by_direction" in b for i in ("next_track", "prev_track") for b in context.SHORTCUTS[i]["bindings"])


def test_k9_cloud_style_call_without_direction_keeps_toggle(bg, native_calls, front):
    """雲端只傳 id、不帶 direction：維持切換（使用者已決定，不改）"""
    front("Safari", host="www.youtube.com")
    assert front_tools.front_action("play_pause").startswith("✅")
    assert native_calls == [f"button {LEGACY_YT_BUTTON}"]


# ---------- 既有行為的改變（§4.3）：前景不是 YouTube／Music／Spotify 時說『暫停』，原本回報不支援，現在控制背景音樂 ----------
def test_pause_with_unrelated_front_app_now_controls_background(bg, native_calls, front):
    front("Safari", host="example.com")
    step, control = _control("暫停")
    assert "只支援" not in step["logs"][0] and native_calls == [f"button_in_window {P_PAUSE}"] and control["source"] == "ax"
