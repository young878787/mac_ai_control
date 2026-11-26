"""點歌執行：play_media 整條（music-play 計畫 §7.1 Y10、Y12～Y16、S6、D4／D7 的 play_media 那半）。
網路是 tests/support/youtube.py 的假 urlopen（真實 fixture），瀏覽器是假 subprocess；資料庫是暫存目錄的真 SQLite（music_db fixture）"""
import json
import subprocess
import threading
import urllib.error

import pytest

from mac_ai import config
from mac_ai.router import music_prefs, music_tools
from support.llm import forbid_llm
from support.music import DAY, T0, Y_ID, Y_QUERY, Y_URL, _events, _rows, _step
from support.music import mclock, tabs  # noqa: F401  fixture
from support.youtube import load, set_real, video, web_response
from support.youtube import net  # noqa: F401  fixture


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
