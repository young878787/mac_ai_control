"""點歌路由：Fast Path 命中『我想聽／播放／放一首／來一首 X』→ play_media(query)，守門不放寬、match() 保持純粹。
語料見 docs/plans/completed/2026-11-21-music-play.md §7.1（C1、C2、C4）。語料可以加、不能刪。"""
import re
import sqlite3
import subprocess
import types
import urllib.request

import pytest

import mac_ai.router as r
from mac_ai.router import actions, context, front_tools, music_tools, prefs_db
from mac_ai.router.fast_path import FastPathMatcher
from mac_ai.router.slow_path import NO_ACTION_REPLY
from support.expect import fast_matches
from support.llm import FakeLLM, forbid_llm

# C1 正例（原句 → query；"" 表示播常聽的歌）
C1_POSITIVE = [
    ("我想聽周杰倫", "周杰倫"), ("我要聽周杰倫", "周杰倫"),
    ("我想要聽晴天", "晴天"), ("想聽稻香", "稻香"),
    ("我想聽周杰倫的晴天", "周杰倫的晴天"), ("我想聽伍佰的挪威的森林", "伍佰的挪威的森林"),
    ("我想聽一首晴天", "晴天"), ("我想聽晴天吧", "晴天"),
    ("我想聽周杰倫的晴天給我聽", "周杰倫的晴天"), ("幫我放一首晴天", "晴天"),
    ("播放晴天", "晴天"), ("請播放晴天", "晴天"),
    ("幫我播放周杰倫", "周杰倫"), ("播放周杰倫的稻香", "周杰倫的稻香"),
    ("播放周杰倫的歌", "周杰倫"), ("播放 Shape of You", "Shape of You"),
    ("播放 Lo-fi hip hop", "Lo-fi hip hop"), ("放一首晴天", "晴天"),
    ("放首稻香", "稻香"), ("放一首周杰倫的歌", "周杰倫"),
    ("放點輕音樂", "輕音樂"), ("放一首適合下雨天的歌", "適合下雨天"),
    ("來一首告白氣球", "告白氣球"), ("來首五月天", "五月天"),
    ("來點爵士樂", "爵士樂"), ("來一首鄧紫棋的光年之外", "鄧紫棋的光年之外"),
    ("聽五月天的歌", "五月天"), ("五月天的歌來一首", "五月天"),
    ("我想聽 Taylor Swift", "Taylor Swift"), ("我想聽 lofi", "lofi"),
    ("我想聽讀書用的輕音樂", "讀書用的輕音樂"), ("我想聽 podcast", "podcast"),
    ("我想聽這首歌晴天", "晴天"), ("播放告白氣球一下", "告白氣球"),
    ("我想聽歌", ""), ("想聽歌", ""),
    ("放音樂", ""), ("來點音樂", ""),
    ("我想聽音樂", ""), ("播放音樂", ""),
    ("來一首", ""), ("放首歌", ""),
]

# C2 反例：不能產生 play_media。分類只為了讀得懂，測試不依賴分類名稱
C2_NEGATIVE = {
    "否定": ["我不想聽這首", "不要播放", "別放這首歌", "先不要放音樂", "我沒有要聽歌"],
    "敘述": ["聽說明天會下雨", "我剛剛在聽周杰倫", "昨天聽了晴天", "我以前很常聽五月天", "他想聽周杰倫", "她說想聽晴天"],
    "對話": ["我想聽聽你的看法", "我想聽你說", "我想聽你解釋一下", "想聽你唱歌", "聽我說", "聽好了", "我想聽聽晴天"],
    "提問": ["播放清單在哪", "你想聽什麼歌", "可以放周杰倫的歌嗎", "有沒有好聽的歌", "聽得到嗎"],
    "放的其他意思": ["放大", "放大一點", "放到桌面", "放進資料夾", "放假", "放心", "放棄", "放鬆一下"],
    "播放接控制詞": ["播放速度調快", "播放設定", "播放器"],
    "聲音問題": ["聽不到聲音", "聽不清楚"],
    "夾帶另一個指令": ["我想聽音樂幫我開 Spotify", "播放晴天打開小算盤"],
}
# 既有功能：維持 front_action(play_pause)，不是點歌
C2_PLAY_PAUSE = ["播放", "暫停", "繼續播放", "暫停播放"]
# 否定與敘述（守門 G1）：交給 Slow 後也不呼叫任何模型。
# 使用者 2026-11-21 決定：守門不變嚴。下面三句沒有 _NEG_RE／_NARR_RE 的詞，現行守門不會擋、會交給 LLM；
# 這三句只要求「不產生 play_media」（允許呼叫 LLM），不要求 LLM 0 次
C2_GUARD_GAP = ["聽說明天會下雨", "他想聽周杰倫", "她說想聽晴天"]
C2_GUARDED = [t for t in C2_NEGATIVE["否定"] + C2_NEGATIVE["敘述"] if t not in C2_GUARD_GAP]
C2_ALL = [t for texts in C2_NEGATIVE.values() for t in texts]


def _play_media_actions(matches):
    return [a for m in matches if m is not None for a in m.actions if a[0] == "play_media"]


def test_corpus_sizes():
    assert len(C1_POSITIVE) >= 42 and len(C2_ALL) + len(C2_PLAY_PAUSE) >= 40 and len({t for t, _ in C1_POSITIVE}) == len(C1_POSITIVE)


# ---------- C1：正例 ----------
@pytest.mark.parametrize("text,query", C1_POSITIVE, ids=[t for t, _ in C1_POSITIVE])
def test_c1_positive_matches_one_play_media(text, query):
    matches = fast_matches(text)
    assert len(matches) == 1 and matches[0] is not None
    assert matches[0].actions == [("play_media", {"query": query})]


@pytest.mark.parametrize("text,query", C1_POSITIVE, ids=[t for t, _ in C1_POSITIVE])
def test_c1_positive_routes_without_llm(monkeypatch, sh, text, query):
    """整條路由：Fast Path → play_media(query)，LLM 0 次。play_media 本身換成記錄用的假函式（它的行為在 Y 段落測）"""
    forbid_llm(monkeypatch)
    calls = []
    monkeypatch.setitem(actions.LOCAL_TOOLS, "play_media", lambda query="", ctx=None: calls.append(query) or f"✅ 播放：{query}")
    out = r.process_voice_command(text)
    assert len(out["steps"]) == 1 and out["steps"][0]["logs"] == [f"✅ 播放：{query}"]
    assert calls == [query] and sh == []


# ---------- C2：反例 ----------
@pytest.mark.parametrize("text", C2_ALL)
def test_c2_negative_never_play_media(text):
    assert _play_media_actions(fast_matches(text)) == []


@pytest.mark.parametrize("text", C2_GUARDED)
def test_c2_guarded_sentences_never_call_llm(monkeypatch, sh, text):
    forbid_llm(monkeypatch)
    r.process_voice_command(text)
    assert sh == []


@pytest.mark.parametrize("text", C2_GUARD_GAP)
def test_c2_guard_gap_sentences_may_reach_llm_but_never_play_media(monkeypatch, sh, text):
    played = []
    monkeypatch.setitem(actions.LOCAL_TOOLS, "play_media", lambda *a, **k: played.append((a, k)) or "⏭️")
    FakeLLM().install(monkeypatch)  # 允許交給 LLM（預設只回聊天內容）
    r.process_voice_command(text)
    assert played == [] and sh == []


@pytest.mark.parametrize("text", C2_PLAY_PAUSE)
def test_c2_play_pause_stays_front_action(text):
    matches = fast_matches(text)
    assert len(matches) == 1 and matches[0] is not None
    assert [(name, args["id"]) for name, args in matches[0].actions] == [("front_action", "play_pause")]


# ---------- C4：match() 保持純粹 ----------
def test_c4_match_has_no_io(monkeypatch):
    calls = []
    for owner, name in ((urllib.request, "urlopen"), (sqlite3, "connect"), (subprocess, "run")):
        monkeypatch.setattr(owner, name, lambda *a, _n=name, **k: calls.append(_n))
    for text in [t for t, _ in C1_POSITIVE] + C2_ALL + C2_PLAY_PAUSE:
        fast_matches(text)
    assert calls == []


# ---------- K：播放控制（下一首、上一首、暫停、繼續；§4.3、§7.1 K1～K5、K9）----------
# 底層全部是假的：subprocess（pgrep、osascript）、輔助使用 API、媒體鍵；不得真的對 Safari 按按鈕或送媒體鍵
WATCH = "https://www.youtube.com/watch?v=l6a5D6yxqEU&list=RDl6a5D6yxqEU"
SAFARI_PID = 777
P_NEXT, P_PREV = r"\(SHIFT\+n\)$", r"\(SHIFT\+p\)$"
P_PAUSE, P_PLAY = r"^暫停 \(k\)$|^Pause \(k\)$", r"播放.*k$|^Play \(k\)$"
P_TOGGLE = r"^暫停 \(k\)$|^Pause \(k\)$|播放.*k$|^Play \(k\)$"
LEGACY_YT_BUTTON = r"^(?:播放|暫停|Play|Pause)\s*(?:\(k\)|鍵盤快速鍵\s*k|keyboard shortcut k)$"


@pytest.fixture
def bg(monkeypatch, native_calls):
    """背景控制的假環境。bg.pid＝Safari 的行程編號（0＝沒在執行）、bg.url＝它最近使用的視窗目前分頁的網址（None＝osascript 失敗）、
    bg.tabs＝其他視窗／背景分頁的網址（目前分頁自己一定也在『所有分頁』裡，和真實腳本的輸出一致）、
    bg.ax／bg.key＝輔助使用 API 按鈕、媒體鍵的回傳狀態、bg.label＝讀按鈕文字的結果；bg.cmds＝所有 subprocess 指令，bg.pids＝按按鈕的對象行程。
    native_calls 照 conftest 的格式記錄（button_in_window／mediakey／label）"""
    from mac_ai.router import native
    env = types.SimpleNamespace(pid=SAFARI_PID, url=WATCH, tabs=[], ax="ok", key="ok", label=None, cmds=[], pids=[])

    def fake_run(cmd, **kw):
        env.cmds.append(cmd)
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(cmd, 0 if env.pid else 1, f"{env.pid}\n" if env.pid else "", "")
        if cmd[0] == "osascript" and "Safari" in cmd[2]:
            urls = ([env.url] if env.url else []) + env.tabs
            out = "\n".join([f"C:{env.url or ''}"] + [f"T:{u}" for u in urls])
            return subprocess.CompletedProcess(cmd, 0 if env.url is not None else 1, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def press(pid, pattern):
        env.pids.append(pid)
        native_calls.append(f"button_in_window {pattern}")
        return env.ax

    def media(key):
        native_calls.append(f"mediakey {key}")
        return env.key

    def label(pid, pattern):
        native_calls.append(f"label {pattern}")
        return env.label

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(native, "press_button_in_window", press)
    monkeypatch.setattr(native, "post_media_key", media)
    monkeypatch.setattr(native, "button_label", label)
    return env


def _control(text):
    step = r.process_voice_command(text)["steps"][0]
    return step, step.get("control")


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


# ---------- Y：play_media 整條（計畫 §7.1 Y10、Y12～Y16、S6、D4／D7 的 play_media 那半）----------
# 網路是 tests/support/youtube.py 的假 urlopen（真實 fixture），瀏覽器是假 subprocess；資料庫是暫存目錄的真 SQLite（music_db fixture）
import json
import threading
import urllib.error

from mac_ai import config
from mac_ai.router import music_prefs
from support.clock import Clock
from support.youtube import QUERY, load, net, set_real, video, web_response  # noqa: F401

Y_QUERY = "晴天"
Y_ID = "DYptgVvkVLQ"  # fixture「晴天」挑中的官方 MV（tests/runtime/test_youtube_search.py 的 REAL）
Y_URL = f"https://www.youtube.com/watch?v={Y_ID}&list=RD{Y_ID}"
T0 = 1_800_000_000.0
DAY = 86400.0


@pytest.fixture
def tabs(monkeypatch):
    """假 subprocess：osascript（找分頁改網址）回 tabs.osa（'reused'＝有音樂分頁、'none'＝沒有），pgrep 回找到，其餘回 0。
    記錄 tabs.cmds（指令）、tabs.timeouts、tabs.envs；tabs.fail(程式名) 讓該程式回結束碼 1"""
    env = types.SimpleNamespace(cmds=[], timeouts=[], envs=[], osa="none", failing=set())
    env.fail = lambda *names: env.failing.update(names)
    env.of = lambda name: [c for c in env.cmds if c[0] == name]

    def fake_run(cmd, **kw):
        env.cmds.append(list(cmd))
        env.timeouts.append(kw.get("timeout"))
        env.envs.append(kw.get("env"))
        if cmd[0] in env.failing:
            return subprocess.CompletedProcess(cmd, 1, "", "boom")
        return subprocess.CompletedProcess(cmd, 0, env.osa if cmd[0] == "osascript" else "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return env


@pytest.fixture
def mclock(monkeypatch):
    clock = Clock(T0)
    monkeypatch.setattr(prefs_db, "_now", clock)
    return clock


def _step(text):
    return r.process_voice_command(text)["steps"][0]


def _rows(music_db, sql, *args):
    return music_db.conn().execute(sql, args).fetchall()


def _events(music_db, kind=None):
    sql = "SELECT kind, utterance, query, corrected, video_id, source, latency_ms, ok FROM events"
    return _rows(music_db, sql + (" WHERE kind=?" if kind else "") + " ORDER BY id", *([kind] if kind else []))


def test_y_play_whole_route_writes_everything(net, tabs, music_db, mclock, monkeypatch):
    forbid_llm(monkeypatch)
    set_real(net, "qingtian")
    step = _step("我想聽晴天")
    assert step["logs"][0].startswith("✅ 播放：") and step["status"] == "ok"
    assert [c[:3] for c in tabs.of("open")] == [["open", "-a", "Safari"]] and tabs.of("open")[0][3] == Y_URL
    ev = _events(music_db)
    assert len(ev) == 1 and ev[0][:6] == ("play", "我想聽晴天", Y_QUERY, None, Y_ID, "mv") and ev[0][6] > 0 and ev[0][7] == 1
    assert _rows(music_db, "SELECT video_id FROM queries WHERE query=?", Y_QUERY) == [(Y_ID,)]
    assert _rows(music_db, "SELECT COUNT(*) FROM videos WHERE video_id=?", Y_ID) == [(1,)]
    assert _rows(music_db, "SELECT plays FROM terms WHERE term=?", Y_QUERY)[0][0] == 1
    m = step["music"]
    assert m["source"] == "mv" and m["tab"] == "new" and {"search_ms", "open_ms", "db_ms"} <= set(m)


def test_y_no_db_still_plays_and_writes_nothing(net, tabs, tmp_path):
    """資料庫還沒準備好（autouse 的 MUSIC_DB 沒啟動，conn() 是 None）：照常搜尋、播放、回 ✅（D7 的 play_media 那半）"""
    set_real(net, "qingtian")
    step = _step("我想聽晴天")
    assert step["logs"][0].startswith("✅ 播放：") and tabs.of("open")[0][3] == Y_URL
    assert not (tmp_path / "data").exists()  # 沒有建立任何檔案


# ---------- Y12：快取 ----------
def test_y12_second_request_hits_cache_without_network(net, tabs, music_db, mclock):
    set_real(net, "qingtian")
    _step("我想聽晴天")
    net.calls.clear()
    mclock.now += 60
    step = _step("我想聽晴天")
    assert net.calls == [] and step["music"]["source"] == "cache" and "search_ms" not in step["music"]
    assert step["logs"][0] == "✅ 播放：晴天（周杰倫）" and tabs.of("open")[-1][3] == Y_URL  # 快取命中用 videos 表存的（mv 學到的是 YouTube Music 的歌名與歌手）
    assert [e[5] for e in _events(music_db, "play")] == ["mv", "cache"]


def test_y12_unpinned_cache_expires_after_ttl_but_use_refreshes_it(net, tabs, music_db, mclock):
    set_real(net, "qingtian")
    _step("我想聽晴天")
    for _ in range(3):  # 每隔 20 天用一次：滑動期限，一直不過期
        net.calls.clear()
        mclock.now += 20 * DAY
        assert _step("我想聽晴天")["music"]["source"] == "cache" and net.calls == []
    mclock.now += 31 * DAY  # 超過 30 天沒用過：重新查詢
    step = _step("我想聽晴天")
    assert step["music"]["source"] == "mv" and net.count("web") == 1 and net.count("ytm") == 1


def test_y12_pinned_cache_never_expires(net, tabs, music_db, mclock):
    set_real(net, "qingtian")
    _step("我想聽晴天")
    music_prefs.cache_repin(music_db.conn(), Y_QUERY, "abcdefghijk", mclock.now)
    net.calls.clear()
    mclock.now += 400 * DAY
    step = _step("我想聽晴天")
    assert net.calls == [] and step["music"]["source"] == "cache"
    assert tabs.of("open")[-1][3] == "https://www.youtube.com/watch?v=abcdefghijk&list=RDabcdefghijk"


def test_y12_cache_key_is_the_corrected_query(net, tabs, music_db, mclock):
    """矯正後的 query 才是快取的 key，事件記錄原本的與矯正後的"""
    c = music_db.conn()
    for _ in range(2):
        music_prefs.learn_play(c, query="稻香", source="ytm", title="稻香", channel="周杰倫", artists=["周杰倫"], now=T0 - DAY, video_id="sHD_z90ZKV0")
    set_real(net, "daoxiang")
    _step("我想聽到香")
    play = _events(music_db, "play")[0]
    assert play[2:4] == ("到香", "稻香") and _rows(music_db, "SELECT query FROM queries") == [("稻香",)]
    assert [e[2:4] for e in _events(music_db, "correct")] == [("到香", "稻香")]
    assert json.loads(net.calls[0]["data"])["query"] == "稻香"  # 送去搜尋的也是矯正後的


# ---------- Y13：重複使用分頁 ----------
def test_y13_reuses_music_tab_with_one_osascript_and_no_new_tab(net, tabs, mclock):
    tabs.osa = "reused"
    set_real(net, "qingtian")
    step = _step("我想聽晴天")
    assert step["logs"][0].startswith("✅") and step["music"]["tab"] == "reuse"
    assert [c[0] for c in tabs.cmds] == ["pgrep", "osascript"]  # 沒有 open
    osa = tabs.of("osascript")[0]
    assert osa[1] == "-e" and "list=RD" in osa[2] and "youtube.com/watch" in osa[2] and osa[3] == Y_URL and len(osa) == 4
    assert Y_URL not in osa[2]  # 網址是 argv，沒有拼進腳本


def test_y13_no_music_tab_opens_new_tab_after_one_osascript(net, tabs, mclock):
    set_real(net, "qingtian")
    step = _step("我想聽晴天")
    assert [c[0] for c in tabs.cmds] == ["pgrep", "osascript", "open"] and step["music"]["tab"] == "new"
    assert len(tabs.of("osascript")) == 1


def test_y13_every_subprocess_has_a_timeout(net, tabs, mclock):
    set_real(net, "qingtian")
    _step("我想聽晴天")
    tabs.osa = "reused"
    _step("我想聽晴天")
    assert tabs.cmds and all(t is not None and t > 0 for t in tabs.timeouts)


def test_y13_osascript_failure_falls_back_to_new_tab(net, tabs, mclock):
    tabs.fail("osascript")
    set_real(net, "qingtian")
    step = _step("我想聽晴天")
    assert step["logs"][0].startswith("✅") and tabs.of("open")[0][3] == Y_URL


def test_y13_open_failure_is_reported_as_failure(net, tabs, music_db, mclock):
    tabs.fail("open")
    set_real(net, "qingtian")
    step = _step("我想聽晴天")
    assert step["logs"][0].startswith("❌") and step["status"] == "exec_failed"
    assert [(e[0], e[5], e[7]) for e in _events(music_db)] == [("play", "mv", 0)]
    assert _rows(music_db, "SELECT COUNT(*) FROM queries") == [(0,)] and _rows(music_db, "SELECT COUNT(*) FROM terms") == [(0,)]  # 沒播成功就不學、不快取


def test_y13_chrome_is_used_when_safari_is_not_running(net, tabs, monkeypatch, mclock):
    base = subprocess.run  # tabs 的假 subprocess：Safari 沒在執行（pgrep 回 1），其餘照舊

    def run(cmd, **kw):
        if cmd[:3] == ["pgrep", "-x", "Safari"]:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return base(cmd, **kw)
    monkeypatch.setattr(subprocess, "run", run)
    tabs.osa = "reused"
    set_real(net, "qingtian")
    _step("我想聽晴天")
    osa = tabs.of("osascript")[0]
    assert 'application "Google Chrome"' in osa[2] and 'application "Safari"' not in osa[2] and osa[3] == Y_URL


def test_y13_script_guards_zero_tab_ghost_windows():
    """P0-8：關窗後 windows 清單可能留 0 個分頁的幽靈視窗，URL of every tab 對它回空字串（不是 list）。腳本要先包成 list、略過空值。
    （腳本只能在真的 Safari 上執行；這裡只檢查防護寫法與 osacompile，實機行為見計畫 §9 P0-8）"""
    sc = music_tools._SAFARI_MUSIC_TAB_SCRIPT
    assert "if class of u is not list then set u to {u}" in sc and "if s is not missing value then" in sc
    assert sc.index("try") < sc.index('tell application "Safari"') < sc.index("set URL of tab j of window i")  # 整段搜尋包在 try 裡，錯誤時回 none


# ---------- Y10：clientVersion ----------
def test_y10_stored_version_is_used_by_next_request(net, tabs, music_db, mclock):
    set_real(net, "qingtian")
    _step("我想聽晴天")
    sent = {c["name"]: json.loads(c["data"])["context"]["client"]["clientVersion"] for c in net.calls}
    assert sent == {"web": config.YT_WEB_CLIENT_VERSION, "ytm": config.YT_MUSIC_CLIENT_VERSION}
    assert music_prefs.store_client_versions(music_db.conn(), {"web": "2.20270101.00.00", "ytm": "1.20270101.00.01"})
    net.calls.clear()
    _step("我想聽稻香")
    sent = {c["name"]: json.loads(c["data"])["context"]["client"]["clientVersion"] for c in net.calls}
    assert sent == {"web": "2.20270101.00.00", "ytm": "1.20270101.00.01"}


def test_y10_4xx_sets_update_flag_and_does_not_retry(net, tabs, music_db, mclock):
    net.set("web", urllib.error.HTTPError("https://x", 400, "bad", {}, None)).set("ytm", load("ytm", "qingtian"))
    step = _step("我想聽晴天")
    assert step["logs"][0].startswith("✅") and net.count("web") == 1 and net.count("ytm") == 1
    assert music_prefs.maintenance_get(music_db.conn(), music_prefs.KEY_CLIENT_UPDATE_NEEDED) == "1"
    music_prefs.store_client_versions(music_db.conn(), {"web": "2.20270101.00.00"})
    assert music_prefs.maintenance_get(music_db.conn(), music_prefs.KEY_CLIENT_UPDATE_NEEDED) == "0"


# ---------- Y8、Y9：退回搜尋頁 ----------
def test_y8_everything_fails_opens_search_page_with_warning(net, tabs, music_db, mclock):
    net.set("web", urllib.error.HTTPError("https://x", 500, "e", {}, None)).set("ytm", urllib.error.URLError("down"))
    step = _step("我想聽晴天")
    assert step["logs"][0].startswith("⚠️") and step["status"] == "exec_failed" and step["music"]["source"] == "page"
    assert net.count("api") == 0  # 不打 googleapis（Y9）
    opened = tabs.of("open")[0]
    assert opened[:3] == ["open", "-a", "Safari"] and opened[3] == youtube_search_url(Y_QUERY)
    assert [(e[0], e[5], e[7]) for e in _events(music_db)] == [("play", "page", 1), ("fallback", "web=http_500;ytm=network", 0)]
    assert _rows(music_db, "SELECT COUNT(*) FROM terms") == [(0,)] and _rows(music_db, "SELECT COUNT(*) FROM queries") == [(0,)]


def youtube_search_url(q):
    from mac_ai.router import youtube_client
    return youtube_client.search_url(q)


# ---------- Y11：不合法的 videoId 不進 subprocess ----------
@pytest.mark.parametrize("bad", ['abc"; do shell script "x', "abcdefghij", "abcdefghijkl", "abcdefghij\n", ""])
def test_y11_invalid_ids_never_reach_osascript_or_open(net, tabs, bad):
    assert music_tools.open_music_video(bad)[0] is False and tabs.cmds == []


def test_y11_invalid_id_is_skipped_end_to_end(net, tabs, mclock):
    evil = 'abc"; do shell script "x'
    net.set("web", web_response([video(evil, "晴天 壞"), video("GOODGOOD_01", "晴天 好")]))
    net.set("ytm", urllib.error.URLError("down"))
    _step("我想聽晴天")
    argv = [a for c in tabs.cmds for a in c[1:] if c[0] in ("osascript", "open")]
    assert not any(evil in a or "do shell script" in a for a in argv)
    assert [c[3] for c in tabs.of("open")] == ["https://www.youtube.com/watch?v=GOODGOOD_01&list=RDGOODGOOD_01"]


# ---------- Y14：隱私 ----------
def test_y14_requests_carry_no_user_info_across_scenarios(net, tabs, music_db, mclock):
    scenarios = [
        lambda: set_real(net, "qingtian"),                                                                      # 全部成功
        lambda: (net.set("web", urllib.error.HTTPError("https://x", 400, "b", {}, None)), set_real(net, "daoxiang")),  # 4xx
        lambda: (net.set("web", urllib.error.URLError("d")), net.set("ytm", urllib.error.URLError("d"))),       # 全部失敗 → 搜尋頁
        lambda: (net.set("web", "not json"), net.set("ytm", "{}")),                                             # 壞回應
    ]
    for i, setup in enumerate(scenarios):
        net.routes.clear()
        setup()
        _step(f"我想聽測試歌{i}")
    assert net.calls and all(c["name"] in ("web", "ytm") for c in net.calls)  # 只打 InnerTube，不打 googleapis
    for c in net.calls:
        assert "cookie" not in c["headers"] and "authorization" not in c["headers"]


# ---------- 只說歌手：播歌手卡片第一首（使用者 2026-11-21）----------
def test_artist_only_plays_card_top_song_and_learns_song_title(net, tabs, music_db, mclock):
    set_real(net, "jay_only")
    step = _step("我想聽周杰倫")
    assert step["logs"][0] == "✅ 播放：女兒殿下（周杰倫）" and step["music"]["source"] == "ytm"
    assert [c[3] for c in tabs.of("open")] == ["https://www.youtube.com/watch?v=oXlA7LjkE0Q&list=RDoXlA7LjkE0Q"]
    assert _rows(music_db, "SELECT title, channel FROM videos WHERE video_id='oXlA7LjkE0Q'") == [("女兒殿下", "周杰倫")]
    kinds = dict(_rows(music_db, "SELECT term, kind FROM terms"))
    assert kinds.get("女兒殿下") == "title" and kinds.get("周杰倫") != "title"  # 歌名是卡片第一首，不是歌手名


# ---------- Y15：特殊字元 ----------
SPECIAL = ["O'Brien \"quoted\"; DROP TABLE events; --", "--;'\"\\ \U0001F3B5 emoji \U0001F600", "'); DELETE FROM videos; --"]


@pytest.mark.parametrize("title", SPECIAL)
def test_y15_special_characters_round_trip(net, tabs, music_db, mclock, title):
    net.set("web", web_response([video("AAAAAAAAAA1", title, title[::-1])])).set("ytm", urllib.error.URLError("d"))
    q = title + " 歌"
    out = music_tools.play_media(q)
    assert out == f"✅ 播放：{title}（{title[::-1]}）"
    assert _rows(music_db, "SELECT title, channel FROM videos") == [(title, title[::-1])]
    ev = _events(music_db, "play")[0]
    assert ev[2] == q and ev[1] == q and ev[4] == "AAAAAAAAAA1"
    assert _rows(music_db, "SELECT query FROM queries") == [(music_prefs._nq(q),)]
    for name in ("events", "videos", "queries", "terms", "maintenance"):  # 資料表都還在
        assert _rows(music_db, f"SELECT COUNT(*) FROM {name}")


# ---------- Y16：搜尋執行緒不碰資料庫 ----------
def test_y16_all_connects_and_writes_happen_on_the_calling_thread(net, tabs, music_db, mclock, monkeypatch):
    import sqlite3
    log = {"connect": [], "write": [], "net": []}
    real_connect = sqlite3.connect

    def connect(*a, **kw):
        log["connect"].append(threading.current_thread())
        c = real_connect(*a, **kw)
        c.set_trace_callback(lambda sql: log["write"].append(threading.current_thread()) if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) else None)
        return c

    def serve(name):
        def route(req):
            log["net"].append(threading.current_thread())
            return load(name, "qingtian")
        return route
    net.set("web", serve("web")).set("ytm", serve("ytm"))
    monkeypatch.setattr(sqlite3, "connect", connect)
    done = {}
    t = threading.Thread(target=lambda: done.update(step=_step("我想聽晴天")), name="fake-command-worker")
    t.start()
    t.join(15)
    assert done["step"]["logs"][0].startswith("✅")
    me = {th for th in log["connect"] + log["write"]}
    assert log["connect"] and log["write"] and me == {t}  # 連線與寫入全在呼叫 play_media 的執行緒
    assert len(log["net"]) == 2 and t not in log["net"] and threading.current_thread() not in log["connect"]  # 網路在別的執行緒


# ---------- S6：常聽的歌 ----------
def test_s6_empty_history_says_so_and_opens_nothing(net, tabs, music_db, mclock, monkeypatch):
    forbid_llm(monkeypatch)
    step = _step("我想聽歌")
    assert step["logs"] == [music_prefs.NO_HISTORY_MESSAGE] and step["status"] == "exec_failed"
    assert net.calls == [] and tabs.of("open") == [] and tabs.of("osascript") == [] and _events(music_db) == []


def test_s6_favorite_plays_cached_song_without_network_and_is_recorded(net, tabs, music_db, mclock):
    set_real(net, "qingtian")
    _step("我想聽晴天")
    net.calls.clear()
    mclock.now += 5
    step = _step("放音樂")
    assert step["logs"][0].startswith("✅ 播放：") and net.calls == [] and tabs.of("open")[-1][3] == Y_URL
    assert step["music"]["source"] == "cache"
    ev = _events(music_db, "play")
    assert [(e[2], e[4], e[5]) for e in ev] == [(Y_QUERY, Y_ID, "mv"), ("", Y_ID, "cache")]


def test_s6_without_db_favorite_says_db_not_ready(net, tabs):
    step = _step("我想聽歌")
    assert step["logs"] == [music_tools.NO_DB_FAVORITE_MESSAGE] and net.calls == [] and tabs.cmds == []


def test_y_db_errors_never_break_playing(net, tabs, music_db, mclock, monkeypatch):
    """資料庫操作丟 sqlite3.Error（磁碟滿之類）：照常播放、回 ✅，錯誤類別記在 step"""
    import sqlite3

    def boom(*a, **kw):
        raise sqlite3.OperationalError("disk full")
    for name in ("record_event", "cache_put", "learn_play", "upsert_video"):
        monkeypatch.setattr(music_prefs, name, boom)
    set_real(net, "qingtian")
    step = _step("我想聽晴天")
    assert step["logs"][0].startswith("✅") and step["music"]["db_error"] == "OperationalError" and tabs.of("open")


def test_y_step_music_is_written_to_commands_jsonl_keys():
    from mac_ai import assistant
    assert "music" in assistant._LOGGED_STEP_KEYS


# ---------- P5 後段：換一首（K7）、喜歡／不要再放（K8）、我最常聽什麼（S8）、播放控制寫事件（S4 flow 版）----------
# 規則：換一首／不是這首、我喜歡這首、不要再放這首、我最常聽什麼 都整句完全相同才收；其餘含『不要』『別』『你／他』的句子守門不變
P5_ROUTES = [
    ("換一首", ("music_replace", {})), ("換一首歌", ("music_replace", {})), ("不是這首", ("music_replace", {})), ("不是這首歌", ("music_replace", {})),
    ("幫我換一首", ("music_replace", {})), ("我想換一首", ("music_replace", {})),
    ("我喜歡這首", ("music_feedback", {"kind": "like"})), ("我喜歡這首歌", ("music_feedback", {"kind": "like"})),
    ("不要再放這首", ("music_feedback", {"kind": "dislike"})), ("不要再放這首歌", ("music_feedback", {"kind": "dislike"})),
    ("別再放這首", ("music_feedback", {"kind": "dislike"})),
    ("我最常聽什麼", ("music_top", {})), ("我最常聽什麼歌", ("music_top", {})), ("我最常聽誰", ("music_top", {})),
]
P5_TOOLS = {"music_replace", "music_feedback", "music_top"}
P5_NEAR_MISSES = [
    "不要播放", "別放這首歌", "我不想聽這首", "我不喜歡這首", "我不要喜歡這首", "不要再放這首歌了", "不要再放", "別再放這首歌曲",
    "你最常聽什麼", "他最常聽什麼", "我最常聽什麼嗎", "我最常聽周杰倫", "我昨天最常聽什麼", "你喜歡這首嗎", "他喜歡這首",
    "我喜歡這首歌嗎", "換一首歌給我", "換一個", "不是這個", "這不是這首", "不要換一首", "我喜歡這個",
]


@pytest.mark.parametrize("text,action", P5_ROUTES, ids=[t for t, _ in P5_ROUTES])
def test_p5_routes_hit_one_local_tool(text, action):
    matches = fast_matches(text)
    assert len(matches) == 1 and matches[0] is not None and matches[0].actions == [action]


@pytest.mark.parametrize("text", P5_NEAR_MISSES)
def test_p5_near_misses_never_hit_music_feedback_rules(text):
    m = FastPathMatcher().match(text)
    assert m is None or not ({a[0] for a in m.actions} & P5_TOOLS)


@pytest.mark.parametrize("text", ["不要播放", "別放這首歌", "我不想聽這首", "不要再放這首歌了", "不要換一首"])
def test_p5_negations_still_call_no_model_and_write_nothing(monkeypatch, sh, music_db, text):
    forbid_llm(monkeypatch)
    step = r.process_voice_command(text)["steps"][0]
    assert step["kind"] == "slow" and step["logs"][-1] == f"💬 {NO_ACTION_REPLY}"
    assert _events(music_db) == [] and list(sh) == []


def test_p5_routes_use_no_io_in_match(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("match() 不得碰 IO")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(sqlite3, "connect", boom)
    for text, _ in P5_ROUTES:
        FastPathMatcher().match(text)


@pytest.mark.parametrize("text", [t for t, _ in P5_ROUTES])
def test_p5_routes_complete_for_endpoint(text):
    from mac_ai.stt.completeness import COMPLETE, classify
    assert classify(text, wake_enabled=False) == COMPLETE


def test_p5_music_tools_are_local_only():
    from mac_ai.router import tools
    names = {t["function"]["name"] for t in tools.TOOLS_SCHEMA}
    assert P5_TOOLS <= set(actions.LOCAL_TOOLS) and not (P5_TOOLS & names)


# ---------- K7：換一首 ----------
def _play_qingtian(net, mclock):
    set_real(net, "qingtian")
    step = _step("我想聽晴天")
    assert step["logs"][0].startswith("✅ 播放：")
    net.calls.clear()
    mclock.now += 10
    return music_prefs.cache_get(prefs_db.MUSIC_DB.conn(), Y_QUERY, mclock.now).candidates


def _open_ids(tabs):
    return [c[3].split("v=")[1].split("&")[0] for c in tabs.of("open")]


def test_k7_replace_walks_the_candidates_pins_and_runs_out(net, tabs, music_db, mclock, monkeypatch):
    cands = _play_qingtian(net, mclock)
    assert cands[0] == Y_ID and len(cands) == 5
    forbid_llm(monkeypatch)
    seen = []
    for k in (1, 2, 3, 4):
        step = _step("換一首")
        assert step["status"] == "ok" and step["logs"][0].startswith(f"✅ 換一首（第 {k + 1}／5 個候選）：")
        assert _open_ids(tabs)[-1] == cands[k]
        q = _rows(music_db, "SELECT video_id, pinned, results FROM queries WHERE query=?", Y_QUERY)[0]
        assert q[0] == cands[k] and q[1] == 1 and json.loads(q[2]) == cands  # 指向新影片並釘選；候選清單不重排
        seen.append(cands[k])
        mclock.now += 10
    opened = len(tabs.of("open"))
    step = _step("不是這首")  # 5 個都播過了
    assert step["logs"] == [music_tools.NO_CANDIDATE_MESSAGE] and step["status"] == "exec_failed"
    assert len(tabs.of("open")) == opened and net.calls == []  # 不再開、不連網
    assert seen == cands[1:]


def test_k7_replace_writes_replace_and_play_events(net, tabs, music_db, mclock):
    cands = _play_qingtian(net, mclock)
    _step("換一首")
    rep = _events(music_db, "replace")
    assert len(rep) == 1 and rep[0][2] == "晴天" and rep[0][4] == Y_ID  # 舊歌名（videos 的 YouTube Music 歌名）與舊影片
    play = _events(music_db, "play")
    assert [(e[4], e[5], e[7]) for e in play] == [(Y_ID, "mv", 1), (cands[1], "replace", 1)]
    assert play[1][1] == "換一首" and play[1][2] == Y_QUERY  # query 沿用原本的，第二次換才找得到同一份候選
    assert net.calls == []


def test_k7_replaced_song_is_what_the_same_request_plays_next(net, tabs, music_db, mclock):
    cands = _play_qingtian(net, mclock)
    _step("換一首")
    step = _step("我想聽晴天")
    assert step["music"]["source"] == "cache" and net.calls == [] and _open_ids(tabs)[-1] == cands[1]


def test_k7_replace_penalizes_old_title_and_rebuild_matches_live_terms(net, tabs, music_db, mclock):
    """舊歌名扣 0.5；source＝replace 的 play 不更新 terms（不在 LEARN_SOURCES），所以從事件重算的 terms 和即時更新的完全相同"""
    from mac_ai.db import rebuild
    _play_qingtian(net, mclock)
    c = music_db.conn()
    before = _rows(music_db, "SELECT weight FROM terms WHERE term='晴天'")[0][0]
    _step("換一首")
    after = _rows(music_db, "SELECT weight FROM terms WHERE term='晴天'")[0][0]
    assert before - after == pytest.approx(music_prefs.REPLACE_PENALTY, abs=1e-3)  # 距離上次更新只有 10 秒，衰減可忽略
    live = sorted(_rows(music_db, "SELECT term, kind, round(weight, 6), plays, liked FROM terms"))
    rebuild.rebuild(c)
    assert sorted(_rows(music_db, "SELECT term, kind, round(weight, 6), plays, liked FROM terms")) == live


def test_k7_replaced_play_counts_in_video_scores(net, tabs, music_db, mclock):
    cands = _play_qingtian(net, mclock)
    _step("換一首")
    scores, _ = music_prefs.video_scores(music_db.conn(), mclock.now)
    assert scores[cands[1]] == pytest.approx(music_prefs._decay(0), abs=1e-6) and scores[Y_ID] > 0


def test_k7_open_failure_changes_nothing(net, tabs, music_db, mclock):
    cands = _play_qingtian(net, mclock)
    tabs.fail("open")
    step = _step("換一首")
    assert step["logs"][0].startswith("❌ 開啟 YouTube 失敗") and step["status"] == "exec_failed"
    assert _rows(music_db, "SELECT video_id, pinned FROM queries WHERE query=?", Y_QUERY) == [(Y_ID, 0)]
    assert _events(music_db, "replace") == [] and [e[5] for e in _events(music_db, "play")] == ["mv"]
    tabs.failing.clear()
    assert _step("換一首")["logs"][0].startswith("✅ 換一首（第 2／5")  # 失敗沒有吃掉候選
    assert cands[1] == _open_ids(tabs)[-1]


def test_k7_nothing_played_yet(music_db, tabs, monkeypatch):
    forbid_llm(monkeypatch)
    step = _step("換一首")
    assert step["logs"] == [music_tools.NO_RECENT_PLAY_MESSAGE] and tabs.cmds == [] and _events(music_db) == []


def test_k7_favorite_play_has_no_candidates(net, tabs, music_db, mclock):
    _play_qingtian(net, mclock)
    _step("放音樂")  # 常聽的歌：query 是空字串
    opened = len(tabs.of("open"))
    step = _step("換一首")
    assert step["logs"] == [music_tools.NO_RECENT_PLAY_MESSAGE] and len(tabs.of("open")) == opened


def test_k7_expired_unpinned_cache_has_no_candidates(net, tabs, music_db, mclock):
    _play_qingtian(net, mclock)
    mclock.now += 31 * DAY
    assert _step("換一首")["logs"] == [music_tools.NO_CANDIDATE_MESSAGE]


def test_k7_without_db_says_so_and_runs_nothing(tabs, tmp_path):
    step = _step("換一首")
    assert step["logs"] == [music_tools.NO_DB_MUSIC_MESSAGE] and tabs.cmds == [] and not (tmp_path / "data").exists()


# ---------- K8：喜歡、不要再放 ----------
VID_A, VID_B = "l6a5D6yxqEU", "aaaaaaaaaaa"
RD = lambda v: f"https://www.youtube.com/watch?v={v}&list=RD{v}"  # noqa: E731


def _seed_song(music_db, vid=VID_A, title="晴天", artist="周杰倫", plays=1):
    c = music_db.conn()
    for _ in range(plays):
        music_prefs.learn_play(c, query=title, source="ytm", title=title, channel=artist, artists=[artist], now=T0, video_id=vid)
        music_prefs.record_event(c, "play", now=T0, query=title, video_id=vid, source="ytm")
    music_prefs.cache_put(c, title, vid, [vid], T0)


def test_k8_like_reads_the_music_tab_with_one_osascript_and_writes_one_event(bg, music_db, mclock, monkeypatch):
    forbid_llm(monkeypatch)
    _seed_song(music_db)
    timeouts = []
    fake = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (timeouts.append(kw.get("timeout")), fake(cmd, **kw))[1])
    before = _rows(music_db, "SELECT weight FROM terms WHERE term='晴天'")[0][0]
    bg.url = RD(VID_A)
    step = _step("我喜歡這首")
    assert step["logs"] == ["✅ 記下了，你喜歡：晴天"] and step["status"] == "ok"
    assert bg.cmds == [["pgrep", "-x", "Safari"], ["osascript", "-e", music_tools._SAFARI_TABS_SCRIPT]]  # 一支 osascript、沒有啟動任何東西
    assert all(t for t in timeouts)  # 每個子程序都有逾時
    likes = _events(music_db, "like")
    assert len(likes) == 1 and likes[0][2] == "晴天" and likes[0][4] == VID_A and likes[0][7] == 1
    assert _rows(music_db, "SELECT liked, weight FROM terms WHERE term='晴天'")[0][0] == 1
    assert _rows(music_db, "SELECT weight FROM terms WHERE term='晴天'")[0][0] > before
    assert music_prefs.video_score(music_db.conn(), VID_A, mclock.now) > music_prefs.video_score(music_db.conn(), VID_B, mclock.now)


def test_k8_dislike_writes_event_removes_cache_and_excludes_the_song(bg, music_db, mclock, monkeypatch):
    forbid_llm(monkeypatch)
    _seed_song(music_db)
    bg.url = RD(VID_A)
    step = _step("不要再放這首")
    assert step["logs"] == ["✅ 記下了，之後不會再自動放：晴天"]
    ev = _events(music_db, "dislike")
    assert len(ev) == 1 and ev[0][4] == VID_A and ev[0][2] == "晴天"
    assert _rows(music_db, "SELECT COUNT(*) FROM queries WHERE video_id=?", VID_A) == [(0,)]
    assert _rows(music_db, "SELECT COUNT(*) FROM terms WHERE term='晴天' AND kind='title'") == [(0,)]
    assert music_prefs.favorite(music_db.conn(), mclock.now, prefs_db._rng) is None
    assert [c[0] for c in bg.cmds] == ["pgrep", "osascript"]  # 不會順便跳下一首（沒有任何控制動作）


def test_k8_playing_video_comes_from_the_url_not_from_the_last_play(bg, music_db, mclock):
    """合輯自動播到下一首時網址會變：記的是分頁現在的影片"""
    _seed_song(music_db, VID_A)
    bg.url = RD(VID_B)
    _step("我喜歡這首")
    assert [e[4] for e in _events(music_db, "like")] == [VID_B]


@pytest.mark.parametrize("url,tabs_,expect", [
    (RD(VID_A), [RD(VID_B)], VID_A),  # 目前分頁就是合輯：用它
    ("https://www.youtube.com/watch?v=ccccccccccc", [RD(VID_B)], VID_B),  # 目前分頁不是合輯：用其他視窗的合輯分頁
    ("https://example.com/", [RD(VID_B), RD(VID_A)], VID_B),  # 多個合輯分頁：第一個
    ("https://www.youtube.com/watch?v=ccccccccccc", [], "ccccccccccc"),  # 沒有合輯分頁：退而用目前的 YouTube watch 分頁
    ("", [RD(VID_A)], VID_A),  # 最近使用的視窗沒有分頁（幽靈視窗）：不報錯，照樣找到
])
def test_k8_picks_the_rd_tab_first(bg, music_db, url, tabs_, expect):
    bg.url, bg.tabs = url, tabs_
    step = _step("我喜歡這首")
    assert step["status"] == "ok" and [e[4] for e in _events(music_db, "like")] == [expect]


@pytest.mark.parametrize("kind_text", ["我喜歡這首", "不要再放這首"])
@pytest.mark.parametrize("url,tabs_", [
    ("https://www.youtube.com/", []), ("https://example.com/", ["https://www.youtube.com/shorts/abc"]), ("", []),
    ("https://www.youtube.com/watch?v=bad&list=RDbad", []), ("https://www.youtube.com/watch?v=abcdefghij%22%3Bx&list=RDx", []),
    (None, []),  # osascript 失敗
])
def test_k8_unreadable_tab_warns_and_writes_nothing(bg, music_db, monkeypatch, kind_text, url, tabs_):
    forbid_llm(monkeypatch)
    _seed_song(music_db)
    n = len(_events(music_db))
    bg.url, bg.tabs = url, tabs_
    step = _step(kind_text)
    assert step["logs"] == [music_tools.NO_MUSIC_TAB_MESSAGE] and step["status"] == "exec_failed"
    assert len(_events(music_db)) == n and _rows(music_db, "SELECT COUNT(*) FROM queries") == [(1,)]


@pytest.mark.parametrize("text", ["我喜歡這首", "不要再放這首"])
def test_k8_safari_not_running_runs_no_osascript(bg, music_db, text):
    bg.pid = 0
    step = _step(text)
    assert bg.cmds == [["pgrep", "-x", "Safari"]] and step["logs"] == [music_tools.NO_MUSIC_TAB_MESSAGE] and _events(music_db) == []


@pytest.mark.parametrize("text", ["我喜歡這首", "不要再放這首"])
def test_k8_without_db_runs_nothing(bg, tmp_path, text):
    step = _step(text)
    assert step["logs"] == [music_tools.NO_DB_MUSIC_MESSAGE] and bg.cmds == [] and not (tmp_path / "data").exists()


def test_k8_unknown_kind_is_rejected(bg, music_db):
    assert music_tools.music_feedback("delete").startswith("❌") and bg.cmds == [] and _events(music_db) == []


# ---------- S8：我最常聽什麼 ----------
def _seed_ranked(music_db, n=7):
    """第 i 首（0 起算）播 n-i 次：歌曲分數與歌手權重都是 A > B > C…"""
    for i in range(n):
        vid = f"vid{i:08d}"
        _seed_song(music_db, vid, f"歌{i}", f"歌手{i}", plays=n - i)


@pytest.mark.parametrize("text", ["我最常聽什麼", "我最常聽什麼歌", "我最常聽誰"])
def test_s8_lists_top_five_in_score_order_without_llm_or_network(net, tabs, music_db, mclock, monkeypatch, text):
    forbid_llm(monkeypatch)
    _seed_ranked(music_db)
    out = r.process_voice_command(text)
    step = out["steps"][0]
    reply = out["replies"][0]
    assert step["status"] == "ok" and step["logs"] == [f"✅ {reply}"] and out["replies"] == [reply]
    songs = reply.split("歌曲：")[1]
    artists = reply.split("歌手：")[1].split("\n")[0]
    expected = music_prefs.top(music_db.conn(), mclock.now, 5)
    assert [s.title for s in expected.songs] == [f"歌{i}" for i in range(5)] and len(expected.artists) == 5  # 手算：播放次數 7、6、5、4、3
    assert re.findall(r"\d\. (歌\d)", songs) == [f"歌{i}" for i in range(5)] and "歌5" not in songs
    assert re.findall(r"\d\. (歌手\d)", artists) == [f"歌手{i}" for i in range(5)]
    assert net.calls == [] and tabs.cmds == []


def test_s8_empty_database_says_no_history(music_db, monkeypatch):
    forbid_llm(monkeypatch)
    out = r.process_voice_command("我最常聽什麼")
    assert out["steps"][0]["logs"] == [music_prefs.NO_HISTORY_MESSAGE] and out["steps"][0]["status"] == "exec_failed" and out["replies"] == []


def test_s8_without_db_says_so(tmp_path):
    out = r.process_voice_command("我最常聽什麼")
    assert out["steps"][0]["logs"] == [music_tools.NO_DB_MUSIC_MESSAGE] and not (tmp_path / "data").exists()


def test_s8_disliked_song_is_not_listed(music_db, mclock):
    _seed_ranked(music_db, 3)
    music_prefs.on_dislike(music_db.conn(), "vid00000000", "歌0", mclock.now)
    reply = r.process_voice_command("我最常聽什麼")["replies"][0]
    assert "歌0" not in reply.split("歌曲：")[1] and "歌1" in reply


# ---------- 播放控制寫事件（next／prev／pause／resume）與 S4 flow 版 ----------
CONTROL_EVENTS = [("下一首", "next"), ("上一首", "prev"), ("暫停", "pause"), ("繼續", "resume")]


@pytest.mark.parametrize("text,kind", CONTROL_EVENTS)
def test_control_success_writes_one_event_with_its_source(bg, native_calls, front, music_db, mclock, monkeypatch, text, kind):
    forbid_llm(monkeypatch)
    front("Visual Studio Code")
    _control(text)
    assert [(e[0], e[1], e[4], e[5], e[7]) for e in _events(music_db)] == [(kind, text, None, "ax", 1)]


def test_control_event_sources_mediakey_and_app(bg, native_calls, front, music_db):
    front("Visual Studio Code")
    bg.ax = "missing"
    _control("下一首")
    front("Music")
    _control("暫停")
    assert [(e[0], e[5]) for e in _events(music_db)] == [("next", "mediakey"), ("pause", "app")]


def test_control_without_action_writes_no_event(bg, native_calls, front, music_db):
    front("Visual Studio Code")
    bg.ax, bg.label = "missing", "播放鍵盤快速鍵k"
    _control("暫停")  # 已經是暫停：沒有動作
    bg.ax, bg.label = "ok", None
    _control("播放")  # toggle：播放或暫停取決於當時狀態，讀不到，不記
    bg.pid = 0
    _control("下一首")  # 找不到音樂：⚠️
    assert _events(music_db) == []


def test_control_without_db_is_unchanged(bg, native_calls, front, tmp_path):
    front("Visual Studio Code")
    step, control = _control("下一首")
    assert control == {"op": "next", "source": "ax"} and step["status"] == "ok" and "music" not in step and not (tmp_path / "data").exists()


@pytest.mark.parametrize("dt,penalized", [(29, True), (31, False)])
def test_s4_skip_penalty_through_the_real_route(net, bg, native_calls, front, music_db, mclock, dt, penalized):
    """播放後 29 秒說『下一首』扣 0.3、31 秒不扣：點歌與控制都走 process_voice_command，時間用注入的時鐘"""
    front("Visual Studio Code")
    set_real(net, "qingtian")
    assert _step("我想聽晴天")["logs"][0].startswith("✅ 播放：")
    mclock.now = T0 + dt
    assert _control("下一首")[1] == {"op": "next", "source": "ax"}
    nxt = _events(music_db, "next")
    assert len(nxt) == 1 and nxt[0][1] == "下一首"
    score = music_prefs.video_score(music_db.conn(), Y_ID, mclock.now)
    expected = music_prefs._decay(dt) - (music_prefs.SKIP_PENALTY if penalized else 0.0)
    assert score == pytest.approx(expected, abs=1e-6)
