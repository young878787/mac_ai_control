"""播放事件與回饋：換一首（K7）、喜歡／不要再放（K8）、我最常聽什麼（S8）、播放控制寫事件與 S4（music-play 計畫 §4.3、§4.4）。
網路是 tests/support/youtube.py 的假 urlopen，瀏覽器是假 subprocess；資料庫是暫存目錄的真 SQLite（music_db fixture）"""
import json
import re
import subprocess

import pytest

import mac_ai.router as r
from mac_ai.router import music_prefs, music_tools, prefs_db
from support.llm import forbid_llm
from support.music import DAY, T0, Y_ID, Y_QUERY, _control, _events, _rows, _step
from support.music import bg, mclock, tabs  # noqa: F401  fixture
from support.youtube import set_real
from support.youtube import net  # noqa: F401  fixture


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
