"""music_prefs 的資料庫部分（計畫 §4.4、§4.5、§7.1 S1～S10、T1～T7）：事件、快取、學習、計分、常聽的歌、矯正、定時整理。
全部用 tmp_path 的真 sqlite（schema 來自 ensure_schema，和正式一致）；時間、亂數一律注入，不連網。
D8（rebuild）在 test_music_db.py，T8（STT 提示詞）在 tests/runtime/test_stt_client.py。"""
import queue
import random
import threading
import urllib.request

import pytest

from mac_ai import config, db
from mac_ai.router import music_prefs as mp, pinyin

DAY = 86400.0
T0 = 1_800_000_000.0


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "m.sqlite3"
    assert db.ensure_schema(path).ok
    c = db.connect(path)
    yield c
    c.close()


def play(c, vid, now, *, query="", source="web", title="", channel="", artists=None, ok=True):
    """呼叫端（play_media）在播放成功時做的三件事：存影片、寫 play 事件、學習"""
    mp.upsert_video(c, vid, title, channel, None, now)
    mp.record_event(c, "play", now=now, query=query, video_id=vid, source=source, ok=ok)
    if ok:
        mp.learn_play(c, query=query, source=source, title=title, channel=channel, artists=artists, now=now, video_id=vid)


def raw_play(c, vid, now, source="cache"):
    """只寫事件（計分測試用，不碰 terms）"""
    mp.record_event(c, "play", now=now, video_id=vid, source=source)


def count(c, table):
    return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def term(c, name):
    return c.execute("SELECT kind, pinyin, weight, plays, liked, updated FROM terms WHERE term=?", (name,)).fetchone()


# ---------- 事件與影片 ----------
def test_record_event_and_upsert_video(conn):
    eid = mp.record_event(conn, "play", now=T0, utterance="我想聽晴天", query="晴天", corrected=None, video_id="abcdefghijk",
                          source="ytm", latency_ms=512.5, ok=False)
    row = conn.execute("SELECT id, ts, kind, utterance, query, corrected, video_id, source, latency_ms, ok FROM events").fetchone()
    assert row == (eid, T0, "play", "我想聽晴天", "晴天", None, "abcdefghijk", "ytm", 512.5, 0)
    mp.upsert_video(conn, "abcdefghijk", "晴天", "周杰倫", "UC1", T0)
    mp.upsert_video(conn, "abcdefghijk", "晴天 v2", "", None, T0 + 5)  # 空字串、None 不覆蓋
    assert conn.execute("SELECT * FROM videos").fetchall() == [("abcdefghijk", "晴天 v2", "周杰倫", "UC1", T0, T0 + 5)]


def test_special_characters_roundtrip_parameterised(conn):
    nasty = "it's \"x\"; DROP TABLE events; -- 🎵"
    mp.upsert_video(conn, "abcdefghijk", nasty, nasty, None, T0)
    mp.record_event(conn, "play", now=T0, query=nasty, video_id="abcdefghijk", source="web")
    play(conn, "abcdefghijk", T0, query=nasty, title=nasty, channel=nasty)
    assert conn.execute("SELECT title FROM videos").fetchone()[0] == nasty
    assert conn.execute("SELECT query FROM events").fetchone()[0] == nasty
    assert count(conn, "events") == 2 and term(conn, nasty) is not None


# ---------- 快取 ----------
def test_cache_roundtrip_ttl_and_sliding_refresh(conn):
    cands = [f"vvvvvvvvvv{i}" for i in range(9)]
    mp.cache_put(conn, "Shape  of You", "vvvvvvvvvv3", cands, T0)
    hit = mp.cache_get(conn, "shape of you", T0 + 1)  # 正規化：大小寫、空白
    assert hit.video_id == "vvvvvvvvvv3" and not hit.pinned
    assert hit.candidates[0] == "vvvvvvvvvv3" and len(hit.candidates) == mp.CACHE_RESULTS_MAX == 6
    assert len(set(hit.candidates)) == 6
    ttl = config.MUSIC_CACHE_TTL_S
    assert mp.cache_get(conn, "shape of you", T0 + 1 + ttl - 1) is not None   # 剛好還沒過期；命中並刷新 updated
    assert mp.cache_get(conn, "shape of you", T0 + 1 + 2 * ttl - 2) is not None  # 距離上一次使用不到 ttl，仍命中（滑動）
    assert mp.cache_get(conn, "shape of you", T0 + 1 + 3 * ttl) is None            # 超過 ttl 沒用過
    assert mp.cache_get(conn, "沒有這個", T0) is None


def test_cache_pinned_never_expires_and_put_cannot_unpin(conn):
    mp.cache_put(conn, "晴天", "aaaaaaaaaaa", [], T0, pinned=True)
    far = T0 + 10 * config.MUSIC_CACHE_TTL_S
    assert mp.cache_get(conn, "晴天", far).pinned
    mp.cache_put(conn, "晴天", "bbbbbbbbbbb", ["aaaaaaaaaaa"], far)  # 一般覆蓋不會把釘選拿掉
    assert mp.cache_get(conn, "晴天", far + 10 * config.MUSIC_CACHE_TTL_S).pinned


def test_cache_repin_points_to_new_video_keeps_candidates(conn):
    mp.cache_put(conn, "晴天", "aaaaaaaaaaa", ["bbbbbbbbbbb", "ccccccccccc"], T0)
    mp.cache_repin(conn, "晴天", "bbbbbbbbbbb", T0 + 5)
    hit = mp.cache_get(conn, "晴天", T0 + 6)
    assert hit.video_id == "bbbbbbbbbbb" and hit.pinned
    assert hit.candidates == ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]  # 清單原樣，呼叫端自己記到第幾個
    mp.cache_repin(conn, "新的", "ddddddddddd", T0)  # 沒有這一列：新增
    assert mp.cache_get(conn, "新的", T0).candidates == ["ddddddddddd"]


def test_cache_get_survives_bad_results_json(conn):
    conn.execute("INSERT INTO queries (query, video_id, results, updated, pinned) VALUES ('壞','aaaaaaaaaaa','{oops',?,0)", (T0,))
    assert mp.cache_get(conn, "壞", T0).candidates == ["aaaaaaaaaaa"]


# ---------- 清理與學習 ----------
@pytest.mark.parametrize("raw,clean", [
    ("周杰倫 Jay Chou【晴天 Sunny Day】Official MV", "周杰倫 Jay Chou 晴天 Sunny Day"),
    ("晴天 (Official Music Video)", "晴天"),
    ("告白氣球 動態歌詞 Lyrics", "告白氣球"),
    ("Shape of You [Official Video]", "Shape of You"),
    ("MVP 之歌", "MVP 之歌"),  # 英數字邊界：MVP 不是 MV
    ("晴天", "晴天"),
])
def test_clean_title(raw, clean):
    assert mp.clean_title(raw) == clean
    assert mp.clean_title(clean) == clean  # 冪等


@pytest.mark.parametrize("raw,clean", [
    ("周杰倫 Jay Chou Official", "周杰倫 Jay Chou"),
    ("Ed Sheeran - Topic", "Ed Sheeran"),
    ("五月天官方頻道", "五月天"),
    ("TaylorSwiftVEVO", "TaylorSwiftVEVO"),
    ("Adele VEVO", "Adele"),
])
def test_clean_channel(raw, clean):
    assert mp.clean_channel(raw) == clean
    assert mp.clean_channel(clean) == clean


def test_learn_ytm_uses_youtube_music_fields_directly(conn):
    play(conn, "aaaaaaaaaaa", T0, query="周杰倫的晴天", source="ytm", title="晴天 (Live)", channel="周杰倫、方文山",
         artists=["周杰倫", "方文山"])
    # ytm 的歌名不清理（只有括號相連的標記不會被 clean_title 動；這裡證明「晴天 (Live)」原樣進去）
    rows = {r[0]: r[1:] for r in conn.execute("SELECT term, kind, weight, plays, liked FROM terms")}
    assert rows["周杰倫"] == ("artist", 1.0, 1, 0) and rows["方文山"] == ("artist", 1.0, 1, 0)
    assert rows["晴天 (Live)"] == ("title", 1.0, 1, 0)
    assert rows["周杰倫的晴天"] == ("query", 1.0, 1, 0)
    assert conn.execute("SELECT pinyin FROM terms WHERE term='周杰倫'").fetchone()[0] == mp.pinyin_key("周杰倫") == "zoujielun"
    assert conn.execute("SELECT title, channel FROM videos").fetchone() == ("晴天 (Live)", "周杰倫、方文山")


def test_learn_mv_overwrites_video_row_with_youtube_music_fields(conn):
    mp.upsert_video(conn, "mvmvmvmvmvm", "周杰倫 Jay Chou【晴天】Official MV", "周杰倫 Jay Chou Official", "UC9", T0)
    mp.learn_play(conn, query="晴天", source="mv", title="晴天", channel="周杰倫 Jay Chou Official", artists=["周杰倫"],
                  now=T0, video_id="mvmvmvmvmvm")
    assert conn.execute("SELECT title, channel, channel_id FROM videos").fetchone() == ("晴天", "周杰倫", "UC9")
    assert term(conn, "周杰倫")[0] == "artist" and term(conn, "晴天")[0] == "title"


def test_learn_web_cleans_title_and_channel(conn):
    play(conn, "aaaaaaaaaaa", T0, query="晴天", source="web", title="晴天【Official MV】", channel="周杰倫 Jay Chou Official")
    assert term(conn, "周杰倫 Jay Chou")[0] == "artist"
    assert term(conn, "晴天")[:1] == ("title",) and term(conn, "晴天")[3] == 1  # 歌名與說法同一個詞：一次播放只算 1 次
    assert term(conn, "晴天【Official MV】") is None


def test_learn_term_kind_keeps_most_specific_and_accumulates(conn):
    play(conn, "aaaaaaaaaaa", T0, query="周杰倫", source="ytm", title="晴天", channel="周杰倫", artists=["周杰倫"])
    play(conn, "bbbbbbbbbbb", T0, query="周杰倫", source="ytm", title="稻香", channel="周杰倫", artists=["周杰倫"])
    kind, _, weight, plays, liked, _ = term(conn, "周杰倫")
    assert (kind, plays, liked) == ("artist", 2, 0) and weight == pytest.approx(2.0)


def test_learn_ignores_page_and_unknown_sources_and_empty_terms(conn):
    mp.learn_play(conn, query="晴天", source="page", title="", channel="", artists=None, now=T0)
    assert count(conn, "terms") == 0
    mp.learn_play(conn, query="", source="cache", title="", channel="", artists=None, now=T0)  # 空的 query（常聽的歌）也沒有東西可學
    assert count(conn, "terms") == 0
    mp.learn_play(conn, query="!!!", source="web", title="", channel="", artists=None, now=T0)  # 沒有拼音的詞不存
    assert count(conn, "terms") == 0


def test_like_dislike_replace_update_terms_and_write_events(conn):
    play(conn, "aaaaaaaaaaa", T0, query="晴天", source="ytm", title="晴天", channel="周杰倫", artists=["周杰倫"])
    mp.on_replace(conn, "晴天", T0 + 1, video_id="aaaaaaaaaaa")
    assert term(conn, "晴天")[2] == pytest.approx(1.0 - 0.5, abs=1e-6)
    mp.on_like(conn, "aaaaaaaaaaa", "晴天", T0 + 2)
    kind, _, weight, plays, liked, _ = term(conn, "晴天")
    assert liked == 1 and plays == 1 and weight == pytest.approx(0.5 + 3.0, abs=1e-4)
    mp.cache_put(conn, "晴天", "aaaaaaaaaaa", [], T0)
    mp.on_dislike(conn, "aaaaaaaaaaa", "晴天", T0 + 3)
    assert term(conn, "晴天") is None and term(conn, "周杰倫") is not None  # 只刪歌名
    assert mp.cache_get(conn, "晴天", T0 + 3) is None                         # 快取列跟著刪
    kinds = [r[0] for r in conn.execute("SELECT kind FROM events WHERE kind IN ('replace','like','dislike') ORDER BY id")]
    assert kinds == ["replace", "like", "dislike"]


def test_like_without_existing_term_creates_it_and_title_none_reads_videos(conn):
    mp.upsert_video(conn, "aaaaaaaaaaa", "稻香", "周杰倫", None, T0)
    mp.on_like(conn, "aaaaaaaaaaa", None, T0)
    assert term(conn, "稻香")[:2] == ("title", mp.pinyin_key("稻香")) and term(conn, "稻香")[4] == 1
    mp.on_like(conn, "zzzzzzzzzzz", None, T0)  # 讀不到歌名：只寫事件
    assert count(conn, "events") == 2 and count(conn, "terms") == 1


def test_replace_unknown_title_does_nothing_to_terms(conn):
    mp.on_replace(conn, "不存在的歌", T0)
    assert count(conn, "terms") == 0 and count(conn, "events") == 1


# ---------- S1～S4：計分 ----------
def test_s1_decay_half_life_30_days(conn):
    raw_play(conn, "aaaaaaaaaaa", T0)
    assert mp.video_score(conn, "aaaaaaaaaaa", T0 + 30 * DAY) == pytest.approx(0.5, abs=1e-9)
    assert mp.video_score(conn, "aaaaaaaaaaa", T0 + 60 * DAY) == pytest.approx(0.25, abs=1e-9)
    assert mp.video_score(conn, "aaaaaaaaaaa", T0) == pytest.approx(1.0, abs=1e-9)


def test_s2_like_adds_three(conn):
    raw_play(conn, "aaaaaaaaaaa", T0)
    before = mp.video_score(conn, "aaaaaaaaaaa", T0)
    mp.on_like(conn, "aaaaaaaaaaa", None, T0)
    assert mp.video_score(conn, "aaaaaaaaaaa", T0) - before == pytest.approx(3.0, abs=1e-9)


def test_s3_disliked_video_is_never_picked(conn):
    for i in range(25):
        for _ in range(3 if i else 50):  # v0 是分數最高的那支，也是被 dislike 的
            raw_play(conn, f"v{i:010d}", T0 - 100 - i)
    mp.on_dislike(conn, "v0000000000", "x", T0)
    rng = random.Random(1)
    for _ in range(1000):
        assert mp.favorite(conn, T0, rng).video_id != "v0000000000"
    assert "v0000000000" not in [s.video_id for s in mp.top(conn, T0, 50).songs]


@pytest.mark.parametrize("gap,penalty", [(29.0, 0.3), (30.0, 0.3), (31.0, 0.0)])
def test_s4_quick_skip_penalty(conn, gap, penalty):
    raw_play(conn, "aaaaaaaaaaa", T0)
    before = mp.video_score(conn, "aaaaaaaaaaa", T0 + gap)  # 這時間點還沒有 next：只有 play 本身（衰減幾秒，約 1）
    mp.record_event(conn, "next", now=T0 + gap)
    assert before - mp.video_score(conn, "aaaaaaaaaaa", T0 + gap) == pytest.approx(penalty, abs=1e-9)


def test_s4_skip_applies_to_the_most_recent_play_and_only_once(conn):
    raw_play(conn, "aaaaaaaaaaa", T0)
    raw_play(conn, "bbbbbbbbbbb", T0 + 100)
    now = T0 + 115
    base = mp.video_scores(conn, now)[0]
    mp.record_event(conn, "next", now=T0 + 110)  # 扣 b
    mp.record_event(conn, "next", now=T0 + 115)  # 同一首不重複扣
    s, _ = mp.video_scores(conn, now)
    assert s["aaaaaaaaaaa"] == pytest.approx(base["aaaaaaaaaaa"], abs=1e-12)
    assert base["bbbbbbbbbbb"] - s["bbbbbbbbbbb"] == pytest.approx(0.3, abs=1e-5)  # 扣分自己也衰減了 5 秒


def test_failed_and_page_plays_do_not_count(conn):
    mp.record_event(conn, "play", now=T0, video_id="aaaaaaaaaaa", source="page", ok=False)
    mp.record_event(conn, "play", now=T0, video_id="bbbbbbbbbbb", source="web", ok=False)
    assert mp.video_scores(conn, T0)[0] == {}


# ---------- S5～S6、S8：常聽的歌與排行 ----------
def _library(c):
    """v00..v29：vNN 播過 (40-NN) 次；最後再依序播 v10..v19（最近 10 首）。前 20 名是 v00..v19，扣掉最近 10 首剩 v00..v09"""
    for i in range(30):
        mp.upsert_video(c, f"v{i:010d}", f"歌{i:02d}", "歌手", None, T0)
        for k in range(40 - i):
            raw_play(c, f"v{i:010d}", T0 - 1000 - k)
    for i in range(10, 20):
        raw_play(c, f"v{i:010d}", T0 - 10 + (i - 10))


def test_s5_favorite_top20_minus_recent10_deterministic_and_offline(conn, monkeypatch):
    _library(conn)
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    eligible = {f"v{i:010d}" for i in range(10)}
    picks = [mp.favorite(conn, T0, random.Random(7)) for _ in range(2)]
    assert picks[0] == picks[1]                                  # 固定種子：結果固定
    rng = random.Random(3)
    seen = [mp.favorite(conn, T0, rng).video_id for _ in range(500)]
    assert set(seen) <= eligible                                 # 只從前 20 名、且不含最近 10 首
    assert seen.count("v0000000000") > seen.count("v0000000009")  # 權重高的比較常被抽到
    assert picks[0].title.startswith("歌") and picks[0].channel == "歌手"
    assert calls == []                                           # 0 次連網


def test_s5_favorite_small_library_falls_back_instead_of_nothing(conn):
    for i in range(3):
        raw_play(conn, f"v{i:010d}", T0 + i)  # 只有 3 首：全部都是「最近 10 首」
    got = {mp.favorite(conn, T0 + 10, random.Random(s)).video_id for s in range(40)}
    assert got == {"v0000000000", "v0000000001"}                 # 只排除最近一首（v2）


def test_s5_favorite_single_video_is_still_returned(conn):
    raw_play(conn, "onlyvideo01", T0)
    assert mp.favorite(conn, T0, random.Random(0)).video_id == "onlyvideo01"


def test_s6_empty_database(conn):
    assert mp.favorite(conn, T0, random.Random(0)) is None
    assert mp.NO_HISTORY_MESSAGE == "⚠️ 還沒有聽歌紀錄，說『我想聽』加歌名"
    assert mp.top(conn, T0) == mp.Top([], [])


def test_s8_top_matches_hand_computed_scores(conn):
    def vid(n):
        return f"top{n:08d}"
    for n in range(1, 8):
        mp.upsert_video(conn, vid(n), f"歌{n}", f"手{n}", None, T0)
    raw_play(conn, vid(1), T0)
    raw_play(conn, vid(1), T0 - 30 * DAY)           # 1 + 0.5 = 1.5
    mp.on_like(conn, vid(2), "歌2", T0)             # 3.0（只有 like）
    raw_play(conn, vid(3), T0)
    raw_play(conn, vid(3), T0)                      # 2.0
    raw_play(conn, vid(4), T0 - 60 * DAY)           # 0.25
    raw_play(conn, vid(5), T0 - 40)
    mp.record_event(conn, "next", now=T0 - 30)      # 1 - 0.3 = 0.7
    raw_play(conn, vid(6), T0 - 90 * DAY)           # 0.125（第 6 名，n=5 不收）
    raw_play(conn, vid(7), T0 - 20)
    mp.on_dislike(conn, vid(7), "歌7", T0)          # 被排除
    got = mp.top(conn, T0, 5).songs
    assert [s.video_id for s in got] == [vid(2), vid(3), vid(1), vid(5), vid(4)]
    assert [s.score for s in got] == pytest.approx([3.0, 2.0, 1.5, 0.7, 0.25], abs=1e-4)  # 秒級的衰減
    assert got[0].title == "歌2" and got[0].channel == "手2"


def test_s8_top_artists_by_decayed_weight(conn):
    for i, (name, n) in enumerate([("甲", 3), ("乙", 1), ("丙", 2)]):
        for k in range(n):
            play(conn, f"a{i}{k:09d}", T0, query="", source="ytm", title=f"歌{i}{k}", channel=name, artists=[name])
    assert [(a.name, round(a.weight, 6)) for a in mp.top(conn, T0).artists] == [("甲", 3.0), ("丙", 2.0), ("乙", 1.0)]
    # 衰減：甲 30 天前的 3 分比現在才聽的 乙 1.5 分還高，但 60 天後甲 0.75、乙 0.25，順序不變；數值衰減到 now
    got = mp.top(conn, T0 + 30 * DAY).artists
    assert [round(a.weight, 6) for a in got] == [1.5, 1.0, 0.5]


# ---------- T：矯正 ----------
def _songs(c, extra=()):
    """terms：周杰倫、稻香、晴天、告白氣球、光年之外、鄧紫棋 播過 3 次；七里香 1 次；extra 是另外要加的 (歌名, 歌手, 次數)"""
    corpus = [("晴天", "周杰倫", 3), ("稻香", "周杰倫", 3), ("告白氣球", "周杰倫", 3), ("光年之外", "鄧紫棋", 3),
              ("七里香", "周杰倫", 1), *extra]
    n = 0
    for title, artist, times in corpus:
        for _ in range(times):
            n += 1
            play(c, f"s{n:010d}", T0 + n, query=title, source="ytm", title=title, channel=artist, artists=[artist])


@pytest.fixture
def songs(conn):
    _songs(conn)
    return conn


def test_t_fixture_has_the_expected_play_counts(songs):
    assert term(songs, "周杰倫")[3] == 10 and term(songs, "鄧紫棋")[3] == 3
    assert term(songs, "七里香")[3] == 1 and term(songs, "晴天")[3] == 3


@pytest.mark.parametrize("heard,fixed", [
    ("到香", "稻香"), ("周杰論", "周杰倫"), ("情天", "晴天"), ("告白汽球", "告白氣球"),
    ("光念之外", "光年之外"), ("鄧子棋", "鄧紫棋"), ("走杰倫", "周杰倫"),
])
def test_t1_homophone_is_corrected(songs, heard, fixed):
    assert mp.correct(songs, heard, T0 + 999) == fixed


def test_t1_all_seven_at_once(songs):
    heard = ["到香", "周杰論", "情天", "告白汽球", "光念之外", "鄧子棋", "走杰倫"]
    assert [mp.correct(songs, h, T0 + 999) for h in heard] == ["稻香", "周杰倫", "晴天", "告白氣球", "光年之外", "鄧紫棋", "周杰倫"]


def test_t2_segments_are_corrected_independently(songs):
    assert mp.correct(songs, "周杰論的到香", T0 + 999) == "周杰倫的稻香"
    assert mp.correct(songs, "周杰論 到香", T0 + 999) == "周杰倫 稻香"  # 空白也是分段，分隔符原樣保留


def test_t3_term_played_once_is_not_used(songs):
    assert mp.correct(songs, "起里香", T0 + 999) == "起里香"
    assert songs.execute("SELECT COUNT(*) FROM events WHERE kind='correct'").fetchone()[0] == 0


def test_t3_but_liked_once_is_enough(songs):
    mp.on_like(songs, "s0000000005", "七里香", T0 + 500)
    assert mp.correct(songs, "起里香", T0 + 999) == "七里香"


def test_t4_ambiguous_pinyin_is_not_corrected(conn):
    _songs(conn, extra=[("情天", "某人", 3)])  # 晴天、情天同時存在
    assert mp.correct(conn, "青天", T0 + 999) == "青天"


def test_t5_term_already_present_is_not_corrected(conn):
    _songs(conn, extra=[("情天", "某人", 3)])
    assert mp.correct(conn, "情天", T0 + 999) == "情天"
    assert mp.correct(conn, "晴天", T0 + 999) == "晴天"


@pytest.mark.parametrize("q", ["晴天娃娃", "Jay Chou", "lofi", "輕音樂", "", "   ", "的"])
def test_t6_only_whole_segment_pinyin_equality(songs, q):
    assert mp.correct(songs, q, T0 + 999) == q


def test_t7_every_correction_writes_one_correct_event(songs):
    mp.correct(songs, "周杰論的到香", T0 + 999, utterance="我想聽周杰論的到香")
    mp.correct(songs, "起里香", T0 + 1000)  # 沒改：不寫
    rows = songs.execute("SELECT ts, kind, utterance, query, corrected, ok FROM events WHERE kind='correct'").fetchall()
    assert rows == [(T0 + 999, "correct", "我想聽周杰論的到香", "周杰論的到香", "周杰倫的稻香", 1)]


def test_correct_skips_no_pinyin_segments_and_matches_case_insensitively(conn):
    for i in range(2):
        play(conn, f"e{i:010d}", T0 + i, query="Shape of You", source="ytm", title="Shape of You", channel="Ed Sheeran",
             artists=["Ed Sheeran"])
    assert mp.correct(conn, "shape of you", T0 + 9) == "shape of you"  # 已在 terms（大小寫不計）：不改
    assert mp.correct(conn, "ed sheeran", T0 + 9) == "ed sheeran"


# ---------- 定時整理：maintain、版本、排程 ----------
def test_maintain_decays_deletes_low_keeps_liked(conn):
    for name, _vid in (("稻香", "a"), ("晴天", "b"), ("七里香", "c")):
        mp.learn_play(conn, query=name, source="cache", title="", channel="", artists=None, now=T0)
    mp.on_like(conn, "x", "七里香", T0)                     # 七里香 liked
    mp._bump_term(conn, "晴天", "title", T0, dweight=-0.99)  # 晴天 只剩 0.01
    conn.commit()
    deleted = mp.maintain(conn, T0 + 30 * DAY)
    assert deleted == 1 and term(conn, "晴天") is None
    assert term(conn, "稻香")[2] == pytest.approx(0.5, abs=1e-9) and term(conn, "稻香")[5] == T0 + 30 * DAY
    assert term(conn, "七里香")[4] == 1                      # liked 衰減後仍低於 0.05 也不刪
    far = mp.maintain(conn, T0 + 400 * DAY)                  # 0.5 * 0.5**(370/30) 很小：稻香被刪、七里香留下
    assert far == 1 and term(conn, "稻香") is None and term(conn, "七里香") is not None
    assert mp.maintenance_get(conn, mp.KEY_LAST_RUN) == repr(T0 + 400 * DAY)


def test_maintain_trims_to_max_from_lowest_weight_unliked(conn, monkeypatch):
    monkeypatch.setattr(config, "MUSIC_TERMS_MAX", 3)
    for i, name in enumerate(["甲一", "乙二", "丙三", "丁四", "戊五"]):
        mp._bump_term(conn, name, "query", T0, dweight=1.0 + i)
    mp._bump_term(conn, "甲一", "query", T0, dweight=0.0, liked=True)  # 最低權重但 liked：不能刪
    conn.commit()
    assert mp.maintain(conn, T0) == 2
    left = {r[0] for r in conn.execute("SELECT term FROM terms")}
    assert left == {"甲一", "丁四", "戊五"}


def test_s7_events_are_untouched_by_maintain(conn):
    _songs(conn)
    mp.correct(conn, "到香", T0)
    before = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    assert before
    mp.maintain(conn, T0 + 10_000 * DAY)  # 所有 terms 都衰減光
    assert count(conn, "terms") == 0
    assert conn.execute("SELECT * FROM events ORDER BY id").fetchall() == before


def test_maintenance_kv_and_client_versions(conn):
    assert mp.maintenance_get(conn, "x") is None
    mp.maintenance_set(conn, "x", "1")
    mp.maintenance_set(conn, "x", "2")
    assert mp.maintenance_get(conn, "x") == "2"
    assert mp.client_versions(conn) == {"web": config.YT_WEB_CLIENT_VERSION, "ytm": config.YT_MUSIC_CLIENT_VERSION}
    mp.maintenance_set(conn, mp.KEY_CLIENT_UPDATE_NEEDED, "1")
    assert mp.store_client_versions(conn, {"web": "2.20261201.01.00", "ytm": "1.2026"}) is True  # ytm 格式不符：不寫
    assert mp.client_versions(conn) == {"web": "2.20261201.01.00", "ytm": config.YT_MUSIC_CLIENT_VERSION}
    assert mp.maintenance_get(conn, mp.KEY_CLIENT_UPDATE_NEEDED) == "0"
    assert mp.store_client_versions(conn, {"web": '2.20261201.01.00"', "ytm": ""}) is False
    mp.maintenance_set(conn, mp.KEY_YTM_VERSION, "bad")  # 資料庫裡壞掉的值也不會被拿去用
    assert mp.client_versions(conn)["ytm"] == config.YT_MUSIC_CLIENT_VERSION


def test_s10_schedule_runs_at_start_then_every_six_hours():
    s = mp.MaintenanceSchedule()
    assert s.interval == config.MUSIC_MAINT_INTERVAL_S == 6 * 3600
    assert s.due(T0)                                  # 啟動時跑一次
    s.mark(T0)
    assert not s.due(T0)
    assert not s.due(T0 + 5 * 3600 + 59 * 60)         # 5 小時 59 分：不跑
    assert s.due(T0 + 6 * 3600)                       # 6 小時：跑
    s.mark(T0 + 6 * 3600 + 7)
    assert not s.due(T0 + 6 * 3600 + 7 + 3600)        # 以實際跑的時間重新計
    assert s.due(T0 + 12 * 3600 + 7)


# ---------- S9：一個寫入者 ----------
def test_s9_all_database_work_happens_on_the_callers_thread(tmp_path):
    """假的 command_worker：自己的執行緒建立連線、逐一執行排進來的工作；記錄每一條 SQL 執行時的執行緒。
    prefs 的函式不開執行緒、不在別的執行緒碰資料庫；定時整理只是被排進佇列的一件工作"""
    path = tmp_path / "m.sqlite3"
    assert db.ensure_schema(path).ok
    jobs: "queue.Queue" = queue.Queue()
    stmts, spawned = [], []

    def worker():
        c = db.connect(path)
        c.set_trace_callback(lambda sql: stmts.append((threading.get_ident(), sql)))
        while True:
            job = jobs.get()
            if job is None:
                break
            before = threading.active_count()
            job(c)
            spawned.append(threading.active_count() - before)
        c.close()

    t = threading.Thread(target=worker)
    t.start()
    done = threading.Event()
    schedule = mp.MaintenanceSchedule()
    if schedule.due(T0):  # assistant 的計時器只做這件事：到時候把整理排進佇列
        schedule.mark(T0)
        jobs.put(lambda c: mp.maintain(c, T0))
    jobs.put(lambda c: play(c, "aaaaaaaaaaa", T0, query="晴天", source="ytm", title="晴天", channel="周杰倫", artists=["周杰倫"]))
    jobs.put(lambda c: mp.cache_put(c, "晴天", "aaaaaaaaaaa", [], T0))
    jobs.put(lambda c: mp.correct(c, "情天", T0))
    jobs.put(lambda c: mp.favorite(c, T0, random.Random(0)))
    jobs.put(lambda c: mp.on_like(c, "aaaaaaaaaaa", "晴天", T0))
    jobs.put(lambda c: done.set())
    assert done.wait(10)
    jobs.put(None)
    t.join(10)
    worker_ident = t.ident
    writes = [(i, s) for i, s in stmts if s.split()[0].upper() in ("INSERT", "UPDATE", "DELETE")]
    assert len(writes) >= 6
    assert {i for i, _ in stmts} == {worker_ident}
    assert threading.get_ident() != worker_ident
    assert spawned and all(n == 0 for n in spawned)


def test_warm_runs_and_module_has_no_global_clock_or_random():
    pinyin.warm()
    import inspect
    src = inspect.getsource(mp) + inspect.getsource(pinyin)
    assert "time.time" not in src and "random.random" not in src and "random.choice" not in src
    assert "threading.Thread" not in src
