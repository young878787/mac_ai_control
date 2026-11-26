"""點歌路由：Fast Path 命中『我想聽／播放／放一首／來一首 X』→ play_media(query)，守門不放寬、match() 保持純粹。
語料見 docs/plans/completed/2026-11-21-music-play.md §7.1（C1、C2、C4）。語料可以加、不能刪。
另有換一首／喜歡／我最常聽什麼的路由（P5）。播放控制、點歌執行、回饋執行分別在 test_route_music_control.py、test_route_music_play.py、test_route_music_feedback.py。"""
import sqlite3
import subprocess
import urllib.request

import pytest

import mac_ai.router as r
from mac_ai.router import actions
from mac_ai.router.fast_path import FastPathMatcher
from mac_ai.router.slow_path import NO_ACTION_REPLY
from support.expect import fast_matches
from support.llm import FakeLLM, forbid_llm
from support.music import _events

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
