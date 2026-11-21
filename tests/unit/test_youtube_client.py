"""youtube_client 的純函式與單一端點（解析、接受規則、挑 MV、請求內容、videoId 驗證、clientVersion 格式）。
並行、截止時間、退回順序（要開執行緒）在 tests/runtime/test_youtube_search.py。計畫 §7.1 的 Y1～Y5、Y10（格式）、Y11、Y14（client 層）。

fixture（tests/fixtures/youtube/）是 2026-11-21 用真的 InnerTube 抓的回應（不需要金鑰、不扣配額）：
  - 一般搜尋 web_*.json：POST https://www.youtube.com/youtubei/v1/search，client WEB／2.20261119.07.00、hl=zh-TW、gl=TW、只要影片
  - YouTube Music ytm_*.json：POST https://music.youtube.com/youtubei/v1/search，client WEB_REMIX／1.20261117.06.00
  - home_*.txt：兩個首頁裡含 INNERTUBE_CLIENT_VERSION 的一小段（前後約 30／60 字元）
  - index.json：slug → 當時的搜尋字（tests/support/youtube.py 的 QUERY）
縮小的方式（scripts/capture_youtube_fixtures.py 的 shrink；規則以腳本為準）：只留 contents 骨架；移除 trackingParams／clickTrackingParams、縮圖、accessibility、menu、buttons、
badges、重複的長網址與參數；一般搜尋只留第一個 itemSection 的前 12 項、YouTube Music 留頂端卡片與前 10 個 itemSection。
解析需要的路徑（videoRenderer 的 videoId／title／ownerText／browseId、musicResponsiveListItemRenderer 的 playlistItemData／flexColumns、
navigationEndpoint…browseEndpointContextMusicConfig.pageType）都原樣保留。
邊界情況（Shorts、廣告、播放清單、不合法的 videoId、名字含「•」的歌手、karaoke 版）真實回應裡沒有，測試裡複製真實 fixture 再改欄位產生（標示「衍生」）。"""
import ast
import json
import socket
import urllib.error
from pathlib import Path

import pytest

from mac_ai import config
from mac_ai.router import youtube_client as yt
from mac_ai.router.youtube_client import MusicHit, Video, YtError
from support.youtube import FIX, QUERY, home, load, music_items, net, video, web_items, web_response  # noqa: F401

BAD_IDS = ['abc"; do shell script "x', "A" * 10, "A" * 12, "", "ABCDEFGHIJ\n", "ABCDE FGHIJ", "ABCDEFGH'IJ", "ABCDEFGHIJ;", "ＡＢＣＤＥＦＧＨＩＪＫ"]
GOOD = "dQw4w9WgXcQ"


def raw_videos(slug):
    """不經過 yt 的獨立讀法：fixture 裡依序的 videoRenderer（id、標題、頻道、頻道 id）"""
    out = []
    for it in web_items(load("web", slug)):
        v = it.get("videoRenderer")
        if v:
            run = v["ownerText"]["runs"][0]
            out.append((v["videoId"], v["title"]["runs"][0]["text"], run["text"],  # 合作影片（多個頻道）的 run 沒有 browseEndpoint：channel_id 是空字串
                        run.get("navigationEndpoint", {}).get("browseEndpoint", {}).get("browseId", "")))
    return out


# ---------- videoId 與請求外觀 ----------
def test_valid_video_id():
    for ok in ("dQw4w9WgXcQ", "a-_0A9z8Y7x", "-" * 11, "_" * 11):
        assert yt.valid_video_id(ok)
    for bad in BAD_IDS + [None, 123, b"dQw4w9WgXcQ", ["dQw4w9WgXcQ"]]:
        assert not yt.valid_video_id(bad), bad


def test_search_url():
    assert yt.search_url("周杰倫 晴天") == "https://www.youtube.com/results?search_query=%E5%91%A8%E6%9D%B0%E5%80%AB+%E6%99%B4%E5%A4%A9"
    assert yt.search_url('a&b="x"') == "https://www.youtube.com/results?search_query=a%26b%3D%22x%22"


def test_module_has_no_db_or_subprocess():
    """搜尋執行緒不碰資料庫、不執行子程序；金鑰不進 os.environ"""
    src = Path(yt.__file__).read_text(encoding="utf-8")
    imported = {n.module if isinstance(n, ast.ImportFrom) else a.name for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in (n.names if isinstance(n, ast.Import) else [n])}
    assert not {"sqlite3", "subprocess", "os"} & {i.split(".")[0] for i in imported if i}
    assert "os.environ" not in src and "print(" not in src and "logging" not in src


# ---------- Y1：一般搜尋解析 ----------
@pytest.mark.parametrize("slug", ["qingtian", "daoxiang", "shape_of_you", "typo_gaobai", "rainy", "sleep", "mayday_qingtian"])
def test_parse_web_first_five_match_fixture(slug):
    got = yt.parse_web(load("web", slug))
    want = raw_videos(slug)[:5]
    assert len(got) == 5
    assert [(v.video_id, v.title, v.channel, v.channel_id) for v in got] == want


def test_parse_web_anchor_values():
    v = yt.parse_web(load("web", "qingtian"))[0]
    assert v == Video("DYptgVvkVLQ", "周杰倫 Jay Chou【晴天 Sunny Day】-Official Music Video", "周杰倫 Jay Chou", "UC8CU5nVhCQIdAGrFFp4loOQ")


def test_parse_web_skips_non_videos_derived():
    """衍生：在真實回應前面塞 Shorts、廣告、播放清單、頻道、不合法 id 的項目；前 5 筆仍和原本相同"""
    base = yt.parse_web(load("web", "qingtian"))
    data = load("web", "qingtian")
    short = video("SHORTSAAAAA", "短片 #shorts")
    short["videoRenderer"]["navigationEndpoint"] = {"reelWatchEndpoint": {"videoId": "SHORTSAAAAA"}}
    short_url = video("SHORTSBBBBB", "短片（網址）")
    short_url["videoRenderer"]["navigationEndpoint"]["commandMetadata"] = {"webCommandMetadata": {"url": "/shorts/SHORTSBBBBB"}}
    ad_badge = video("ADBADGEAAAA", "贊助商影片")
    ad_badge["videoRenderer"]["badges"] = [{"metadataBadgeRenderer": {"style": "BADGE_STYLE_TYPE_AD"}}]
    extras = [
        short, short_url, ad_badge,
        {"adSlotRenderer": {"fulfillmentContent": {"fulfilledLayout": {"inFeedAdLayoutRenderer": video("ADSLOTAAAAA", "廣告")}}}},
        {"reelShelfRenderer": {"items": [video("REELSHELFAA", "Shorts 貨架")]}},
        {"playlistRenderer": {"playlistId": "PLxxxxxxxxxxxxxxxxxxxxxxxx", "videos": [video("PLAYLISTAAA", "清單裡的影片")]}},
        {"lockupViewModel": {"contentId": "PLxxxxxxxxxxxxxxxxxxxxxxxx", "contentType": "LOCKUP_CONTENT_TYPE_PLAYLIST"}},
        {"channelRenderer": {"channelId": "UCxxxxxxxxxxxxxxxxxxxxxx", "title": {"simpleText": "某頻道"}}},
        {"didYouMeanRenderer": {}},
    ] + [video(b, "壞 id") for b in BAD_IDS]
    web_items(data)[0:0] = extras
    got = yt.parse_web(data)
    assert got == base
    assert not {v.video_id for v in got} & {"SHORTSAAAAA", "SHORTSBBBBB", "ADBADGEAAAA", "ADSLOTAAAAA", "REELSHELFAA", "PLAYLISTAAA"}


def test_parse_web_fewer_than_five_and_garbage():
    assert [v.video_id for v in yt.parse_web(web_response([video(GOOD, "t"), video(BAD_IDS[0], "x")]))] == [GOOD]
    for junk in ({}, [], None, "x", {"contents": 1}, web_response([]), web_response([{"videoRenderer": "oops"}, {"videoRenderer": {}}])):
        assert yt.parse_web(junk) == []
    assert yt.parse_web(web_response([video(GOOD, "")])) == []  # 沒有標題的丟棄


# ---------- Y2：YouTube Music 解析 ----------
def test_parse_music_song_anchor():
    hit = yt.parse_music(load("ytm", "qingtian"))
    assert hit == MusicHit("song", "SJKoWAd5ySo", "晴天", ["周杰倫"])  # 頂端卡片是影片（OMV）；清單第一筆有 videoId 的才是「第一筆」


def test_parse_music_skips_artist_row_before_song():
    """稻香：清單第一列是歌手卡（沒有 videoId），第二列才是歌曲；歌手列略過"""
    hit = yt.parse_music(load("ytm", "daoxiang"))
    assert (hit.kind, hit.video_id, hit.title, hit.artists) == ("song", "l6a5D6yxqEU", "稻香", ["周杰倫"])


def test_parse_music_artist_only_query_is_artist():
    """只說歌手：頂端卡片是歌手、清單從播放清單與專輯開始 → 類型是歌手，不是歌曲；卡片底下的熱門歌曲不算結果，第一首另外記在 top_song"""
    hit = yt.parse_music(load("ytm", "jay_only"))
    assert hit == MusicHit("artist", None, "周杰倫", ["周杰倫"], "oXlA7LjkE0Q", "女兒殿下")


def test_parse_music_card_artist_but_list_starts_with_song_is_song():
    hit = yt.parse_music(load("ytm", "sleep"))  # 卡片是歌手（睡眠音乐），清單第一筆是歌曲
    assert hit.kind == "song" and hit.title.startswith("【極致舒眠】")


def test_parse_music_video_and_other_kinds():
    assert yt.parse_music(load("ytm", "rainy")).kind == "video"
    assert yt.parse_music(load("ytm", "study")).kind == "video"
    # 衍生：把第一筆的類型標籤改成播放清單、專輯、看不懂的標籤
    for label, kind in (("播放清單", "playlist"), ("專輯", "album"), ("單曲", "album"), ("Playlist", "playlist"), ("Song", "song"),
                        ("Video", "video"), ("單集", "other"), ("???", "other")):
        data = load("ytm", "qingtian")
        music_items(data)[0]["itemSectionRenderer"]["contents"][0]["musicResponsiveListItemRenderer"]["flexColumns"][1][
            "musicResponsiveListItemFlexColumnRenderer"]["text"]["runs"][0]["text"] = label
        assert yt.parse_music(data).kind == kind, label


def test_parse_music_multiple_artists():
    hit = yt.parse_music(load("ytm", "despacito"))
    assert hit.kind == "song" and hit.artists == ["Luis Fonsi", "Daddy Yankee"]  # 「 和 」的連接字不算歌手


def test_parse_music_artist_name_containing_bullet_derived():
    """衍生：歌手名稱本身含「•」，不能用「•」切字串"""
    data = load("ytm", "qingtian")
    runs = music_items(data)[0]["itemSectionRenderer"]["contents"][0]["musicResponsiveListItemRenderer"]["flexColumns"][1][
        "musicResponsiveListItemFlexColumnRenderer"]["text"]["runs"]
    runs[2]["text"] = "Ashes • Remain"
    runs.extend([{"text": "、"}, {"text": "A • B", "navigationEndpoint": runs[2]["navigationEndpoint"]}])
    hit = yt.parse_music(data)
    assert hit.artists == ["Ashes • Remain", "A • B"] and hit.title == "晴天"


def test_parse_music_non_artist_runs_not_counted():
    hit = yt.parse_music(load("ytm", "typo_jay_qingtian"))
    assert hit.artists == ["周杰倫"]
    data = load("ytm", "qingtian")  # 衍生：歌手 run 沒有 pageType（例如「Casey Breves, Jasmine Clarke」那種沒有連結的）→ 沒有歌手
    run = music_items(data)[0]["itemSectionRenderer"]["contents"][0]["musicResponsiveListItemRenderer"]["flexColumns"][1][
        "musicResponsiveListItemFlexColumnRenderer"]["text"]["runs"][2]
    del run["navigationEndpoint"]
    assert yt.parse_music(data).artists == []


def test_parse_music_invalid_video_id_discarded_derived():
    """Y11（YouTube Music）：第一筆 videoId 不合法 → 丟棄，換下一筆"""
    for bad in BAD_IDS:
        data = load("ytm", "qingtian")
        first = music_items(data)[0]["itemSectionRenderer"]["contents"][0]["musicResponsiveListItemRenderer"]
        first["playlistItemData"]["videoId"] = bad
        hit = yt.parse_music(data)
        assert hit.video_id == "ijgst6J7zVI" and hit.title == "絶対晴天！！", bad  # 清單裡下一筆有合法 id 的（歌手列沒有 id 略過）


def test_parse_music_garbage():
    for junk in ({}, [], None, "x", {"contents": {"tabbedSearchResultsRenderer": {"tabs": []}}}):
        assert yt.parse_music(junk) is None


# ---------- Y3、Y4：接受規則 ----------
# 真實 fixture：query → slug。Y3 要接受
ACCEPT = [("晴天", "qingtian"), ("周杰倫的晴天", "jay_qingtian"), ("晴天 周杰倫", "qingtian_jay"), ("稻香", "daoxiang"),
          ("Shape of You", "shape_of_you"), ("告白氣球", "gaobai"), ("到香", "typo_daoxiang"), ("告白汽球", "typo_gaobai"),
          ("周杰論的晴天", "typo_jay_qingtian"),
          ("日不落 蔡依林", "ribuluo"), ("江南 林俊傑", "jiangnan"), ("Coldplay Yellow", "yellow"), ("Despacito", "despacito"),
          ("十年 陳奕迅", "shinian"), ("不想長大 S.H.E", "buxiangzhangda")]


@pytest.mark.parametrize("query,slug", ACCEPT)
def test_accept_real_fixtures(query, slug):
    assert QUERY[slug] == query
    assert yt.accept_music(query, yt.parse_music(load("ytm", slug))) is True


# Y4 要拒絕（真實 fixture 的部分）
REJECT = [("周杰倫", "jay_only", "類型是歌手（只說歌手）"),
          ("睡眠音樂", "sleep", "歌名不在 query 裡"),
          ("五月天的晴天", "mayday_qingtian", "剩下的字對不到歌手"),
          ("適合下雨天", "rainy", "類型是影片（情境句）"),
          ("讀書用的輕音樂", "study", "類型是影片（情境句）"),
          ("Take Me Home Country Roads", "country_roads", "清單第一筆是影片")]


@pytest.mark.parametrize("query,slug,why", REJECT)
def test_reject_real_fixtures(query, slug, why):
    assert QUERY[slug] == query
    assert yt.accept_music(query, yt.parse_music(load("ytm", slug))) is False, why


def test_reject_karaoke_derived():
    """衍生：把歌名或歌手改成伴奏類（Sunfly 這類）。真實回應裡沒有第一筆是 karaoke 的 query"""
    base = yt.parse_music(load("ytm", "qingtian"))
    assert yt.accept_music("晴天", base)
    for title, artists in (("晴天 (Karaoke Version)", ["Sunfly Karaoke"]), ("晴天", ["Sunfly Karaoke"]), ("晴天 (伴奏)", ["周杰倫"]),
                           ("晴天 [Instrumental]", ["周杰倫"]), ("晴天 卡拉OK", ["周杰倫"]), ("晴天 KTV", ["周杰倫"]), ("晴天 (Cover)", ["周杰倫"]),
                           ("晴天 翻唱", ["周杰倫"]), ("晴天", ["某某 Cover Band"]), ("晴天", ["伴奏工作室"])):
        assert not yt.accept_music("晴天", MusicHit("song", base.video_id, title, artists)), (title, artists)


def test_marker_needs_word_boundary():
    """cover、ktv 等英文標記要整字；Recovery、Discovery 這類歌名不能被擋"""
    for title in ("Recovery", "Discovery", "Uncovered", "Ktvx"):
        assert yt.accept_music(title, MusicHit("song", GOOD, title, ["X"])), title
    assert not yt.accept_music("Recovery", MusicHit("song", GOOD, "Recovery (cover)", ["X"]))


def test_reject_non_song_kinds_derived():
    base = yt.parse_music(load("ytm", "qingtian"))
    for kind in ("video", "playlist", "artist", "album", "other"):
        assert not yt.accept_music("晴天", MusicHit(kind, base.video_id, base.title, base.artists)), kind
    assert not yt.accept_music("晴天", None)
    assert not yt.accept_music("晴天", MusicHit("song", None, "晴天", ["周杰倫"]))
    assert not yt.accept_music("晴天", MusicHit("song", BAD_IDS[0], "晴天", ["周杰倫"]))


def test_accept_title_brackets_removed():
    for title in ("十年 (國)", "十年（國語版）", "十年【國】", "十年 [國]", "十年 (a (b)) [c]"):
        assert yt.accept_music("十年", MusicHit("song", GOOD, title, ["陳奕迅"])), title
    assert not yt.accept_music("國", MusicHit("song", GOOD, "十年 (國)", ["陳奕迅"]))  # 括號裡的字不算歌名
    assert not yt.accept_music("十年", MusicHit("song", GOOD, "(十年)", ["陳奕迅"]))  # 去掉括號後沒有歌名


def test_accept_leftover_rules():
    s = lambda q, title="晴天", artists=("周杰倫",): yt.accept_music(q, MusicHit("song", GOOD, title, list(artists)))  # noqa: E731
    assert s("周杰倫的晴天") and s("晴天 周杰倫") and s("周杰倫 晴天") and s("晴天的周杰倫")
    assert s("周杰論的晴天") and s("走杰倫的晴天") and s("周杰倫的情天") and s("周杰倫的青天")  # 拼音放寬
    assert s("  晴天！ ") and s("周杰倫，晴天。")  # 標點、空白
    assert not s("五月天的晴天")  # 剩下的字對不到歌手
    assert not s("晴天娃娃")  # 剩下的「娃娃」
    assert not s("周杰倫的晴天 mv")
    assert s("Taylor Swift Love Story", "Love Story", ("Taylor Swift",)) and s("Taylor Love Story", "Love Story", ("Taylor Swift",))
    assert s("Love Story", "Love Story", ()) and not s("Taylor Love Story", "Love Story", ())  # 沒有歌手資料：有剩下的字就不接受
    assert s("方文山的晴天", artists=("周杰倫", "方文山")) and not s("方文山的晴天", artists=("周杰倫",))
    assert s("告白气球", "告白氣球") and s("告白氣球", "告白气球")  # 簡繁
    assert not s("")  # 空 query


# ---------- Y5：挑 MV ----------
YM = MusicHit("song", "YTMYTMYTM01", "晴天", ["周杰倫"])


def vids(*rows):
    """rows：(id, title, channel)"""
    return [Video(i, t, c) for i, t, c in rows]


def test_pick_real_fixtures():
    web = yt.parse_web(load("web", "qingtian"))
    p = yt.pick("晴天", yt.parse_music(load("ytm", "qingtian")), web)
    assert (p.source, p.video_id, p.channel) == ("mv", "DYptgVvkVLQ", "周杰倫 Jay Chou")
    assert p.candidates[0] == "DYptgVvkVLQ" and len(p.candidates) == 5 and set(p.candidates) == {v.video_id for v in web}


def test_pick_1_official_channel_third():
    web = vids(("AAAAAAAAAA1", "晴天 周杰倫 歌詞版", "某歌詞頻道"), ("AAAAAAAAAA2", "周杰倫 晴天 Live", "某人"),
               ("AAAAAAAAAA3", "【晴天】Official MV", "周杰倫 Jay Chou"), ("AAAAAAAAAA4", "晴天 周杰倫 MV", "某某"), ("AAAAAAAAAA5", "x", "y"))
    p = yt.pick("晴天", YM, web)
    assert (p.source, p.video_id) == ("mv", "AAAAAAAAAA3")  # ① 頻道名稱對到歌手、標題含歌名（第二筆標題雖然也有歌手與歌名，但頻道不是）
    assert p.candidates == ["AAAAAAAAAA3", "AAAAAAAAAA1", "AAAAAAAAAA2", "AAAAAAAAAA4", "AAAAAAAAAA5"]


def test_pick_2_title_has_song_and_artist():
    web = vids(("AAAAAAAAAA1", "晴天 歌詞 Lyrics 周杰倫", "歌詞頻道"), ("AAAAAAAAAA2", "周杰倫 晴天 現場", "某人"), ("AAAAAAAAAA3", "別的歌", "某某"))
    p = yt.pick("晴天", YM, web)
    assert (p.source, p.video_id) == ("mv", "AAAAAAAAAA2")  # 第一筆有歌詞標記，排除


def test_pick_3_only_lyrics_and_karaoke_plays_ytm():
    web = vids(("AAAAAAAAAA1", "晴天 周杰倫 歌詞版", "歌詞頻道"), ("AAAAAAAAAA2", "晴天 周杰倫 Karaoke", "K"), ("AAAAAAAAAA3", "周杰倫 晴天 伴奏", "B"),
               ("AAAAAAAAAA4", "周杰倫 晴天 Lyrics", "L"), ("AAAAAAAAAA5", "周杰倫 晴天 (cover)", "C"))
    p = yt.pick("晴天", YM, web)
    assert (p.source, p.video_id, p.title, p.channel) == ("ytm", "YTMYTMYTM01", "晴天", "周杰倫")  # ytm：歌名與歌手用 YouTube Music 的
    assert p.candidates == ["YTMYTMYTM01"] + [v.video_id for v in web] and len(p.candidates) == 6


def test_pick_4_web_failed_plays_ytm():
    p = yt.pick("晴天", YM, [])
    assert (p.source, p.video_id, p.candidates) == ("ytm", "YTMYTMYTM01", ["YTMYTMYTM01"])
    two = YM.__class__("song", "YTMYTMYTM01", "Despacito", ["Luis Fonsi", "Daddy Yankee"])
    assert yt.pick("Despacito", two, []).channel == "Luis Fonsi、Daddy Yankee"


def test_pick_not_accepted_plays_web_first():
    web = yt.parse_web(load("web", "mayday_qingtian"))
    p = yt.pick("五月天的晴天", yt.parse_music(load("ytm", "mayday_qingtian")), web)
    assert (p.source, p.video_id) == ("web", web[0].video_id) and p.candidates == [v.video_id for v in web]
    assert yt.pick("適合下雨天", yt.parse_music(load("ytm", "rainy")), web).source == "web"
    assert yt.pick("晴天", None, web).source == "web"  # YouTube Music 失敗


def test_pick_nothing_playable_is_none():
    assert yt.pick("晴天", None, []) is None
    assert yt.pick("五月天的晴天", yt.parse_music(load("ytm", "mayday_qingtian")), []) is None  # 沒接受、一般搜尋也沒有：呼叫端開搜尋頁


def test_pick_artist_only_plays_card_top_song():
    """只說歌手（使用者 2026-11-21）：播歌手卡片第一首（官方的歌），不播一般搜尋第一筆（串燒、團員個人作品）"""
    web = yt.parse_web(load("web", "jay_only"))
    p = yt.pick("周杰倫", yt.parse_music(load("ytm", "jay_only")), web)
    assert (p.source, p.video_id, p.title, p.channel) == ("ytm", "oXlA7LjkE0Q", "女兒殿下", "周杰倫")
    assert p.candidates == ["oXlA7LjkE0Q"] + [v.video_id for v in web]
    assert yt.pick("周杰倫", yt.parse_music(load("ytm", "jay_only")), []).video_id == "oXlA7LjkE0Q"  # 一般搜尋失敗也照播


def test_pick_artist_without_card_song_plays_web_first():
    web = yt.parse_web(load("web", "jay_only"))
    for top in ((None, ""), ("oXlA7LjkE0Q", ""), (BAD_IDS[0], "女兒殿下")):  # 卡片沒有歌、沒有歌名、videoId 不合法
        p = yt.pick("周杰倫", MusicHit("artist", None, "周杰倫", ["周杰倫"], *top), web)
        assert (p.source, p.video_id) == ("web", web[0].video_id)


def test_parse_music_artist_card_without_songs_derived():
    """衍生：歌手卡片底下的歌全部拿掉 → 還是歌手，沒有 top_song"""
    data = load("ytm", "jay_only")
    card = next(yt._walk(data, "musicCardShelfRenderer"))
    card["contents"] = [c for c in card.get("contents") or [] if "musicResponsiveListItemRenderer" not in json.dumps(c)]
    hit = yt.parse_music(data)
    assert (hit.kind, hit.top_song_id, hit.top_song_title) == ("artist", None, "")


def test_pick_dedupes_candidates_and_drops_bad_ids():
    web = vids(("AAAAAAAAAA1", "x", "y"), (BAD_IDS[0], "壞", "y"), ("AAAAAAAAAA1", "重複", "y"), ("YTMYTMYTM01", "和 ytm 同一支", "y"), ("AAAAAAAAAA2", "z", "y"))
    p = yt.pick("五月天的晴天", None, web)
    assert p.candidates == ["AAAAAAAAAA1", "YTMYTMYTM01", "AAAAAAAAAA2"] and p.video_id == "AAAAAAAAAA1"
    p = yt.pick("晴天", YM, vids(("AAAAAAAAAA1", "x", "y"), ("YTMYTMYTM01", "y", "z")))  # ytm id 在一般搜尋裡也有：只出現一次，排第一
    assert p.candidates == ["YTMYTMYTM01", "AAAAAAAAAA1"]
    assert yt.pick("x", None, [Video(b, "t") for b in BAD_IDS]) is None  # 全部不合法


def test_pick_uses_pinyin_for_title_and_channel():
    """簡體標題、頻道名稱用拼音也對得上"""
    web = vids(("AAAAAAAAAA1", "周杰伦【晴天】Official MV", "周杰伦官方"))
    assert yt.pick("晴天", YM, web).source == "mv"


# ---------- search_web／search_music 的請求內容（Y14：client 層）----------
def test_innertube_request_content(net):
    net.set("web", load("web", "qingtian")).set("ytm", load("ytm", "qingtian"))
    assert len(yt.search_web("晴天", "2.20261119.07.00", 1.5)) == 5
    assert yt.search_music("晴天", "1.20261117.06.00", 1.5).title == "晴天"
    web, ytm = net.calls
    assert web["url"] == config.YT_WEB_SEARCH_URL and ytm["url"] == config.YT_MUSIC_SEARCH_URL
    assert web["timeout"] == ytm["timeout"] == 1.5
    for call, client, version in ((web, "WEB", "2.20261119.07.00"), (ytm, "WEB_REMIX", "1.20261117.06.00")):
        body = json.loads(call["data"])
        assert body["context"] == {"client": {"clientName": client, "clientVersion": version, "hl": "zh-TW", "gl": "TW"}}
        assert body["query"] == "晴天" and set(body) <= {"context", "query", "params"}
        h = call["headers"]
        assert h["user-agent"].startswith("Mozilla/5.0") and "Python-urllib" not in h["user-agent"]
        assert h["content-type"] == "application/json" and h["origin"].startswith("https://")
        assert not {"cookie", "authorization", "x-goog-api-key", "x-goog-authuser"} & set(h)
    assert json.loads(web["data"])["params"] == "EgIQAQ%3D%3D" and "params" not in json.loads(ytm["data"])  # 一般搜尋只要影片


@pytest.mark.parametrize("failure,kind", [
    (urllib.error.HTTPError("https://x", 400, "Bad", {}, None), "http_400"),
    (urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None), "http_403"),
    (urllib.error.HTTPError("https://x", 500, "ISE", {}, None), "http_500"),
    (socket.timeout("timed out"), "timeout"),
    (TimeoutError(), "timeout"),
    (urllib.error.URLError(socket.timeout("timed out")), "timeout"),
    (urllib.error.URLError("dns"), "network"),
    (ConnectionResetError(), "network"),
    (b"{not json", "bad_json"),
    (b"\xff\xfe", "bad_json"),
    ([], "bad_format"),
    ("null", "bad_format"),
])
def test_innertube_failures_are_classified(net, failure, kind):
    net.set("web", failure).set("ytm", failure)
    for fn in (lambda: yt.search_web("晴天", "2.20261119.07.00", 1.0), lambda: yt.search_music("晴天", "1.20261117.06.00", 1.0)):
        with pytest.raises(YtError) as e:
            fn()
        assert e.value.kind == kind and str(e.value) == kind and e.value.__context__ is None


def test_search_music_no_results_is_none_and_web_empty(net):
    net.set("ytm", {"contents": {}}).set("web", web_response([]))
    assert yt.search_music("x", "1.20261117.06.00", 1.0) is None and yt.search_web("x", "2.20261119.07.00", 1.0) == []


# ---------- Y10：clientVersion 格式 ----------
def test_parse_client_version_from_home_fixtures():
    assert yt.parse_client_version(home("web")) == "2.20261119.07.00"
    assert yt.parse_client_version(home("ytm")) == "1.20261117.06.00"
    assert yt.parse_client_version(home("web").replace('"INNERTUBE', '\\"INNERTUBE').replace('":"', '\\":\\"')) == "2.20261119.07.00"  # 首頁裡有些是跳脫過的 JSON


@pytest.mark.parametrize("text", [
    "", "no version here", 'INNERTUBE_CLIENT_VERSION":""', 'INNERTUBE_CLIENT_VERSION":"2.2026"',
    'INNERTUBE_CLIENT_VERSION":"2.20261119.07.00.1"', 'INNERTUBE_CLIENT_VERSION":"2.20261119.07.0"',
    'INNERTUBE_CLIENT_VERSION":"x.20261119.07.00"', 'INNERTUBE_CLIENT_VERSION":"2.20261119.07.00;evil"',
    'INNERTUBE_CLIENT_VERSION":"2.20261119.07.00' + "9" * 100 + '"', 'INNERTUBE_CLIENT_VERSION":"２.２０２６１１１９.０７.００"',
    'INNERTUBE_CLIENT_VERSION":"2.20261119.07.00\\n"',
])
def test_parse_client_version_rejects_bad_format(text):
    assert yt.parse_client_version(text) is None


def test_valid_client_version():
    assert yt.valid_client_version("2.20261119.07.00") and yt.valid_client_version("1.20261117.06.00")
    for bad in ("", None, 5, "2.20261119.07.00\n", "2.20261119.07.00 ", '2.20261119.07."', "2.2026111.07.00", "22.20261119.07.00", "2.20261119.7.00"):
        assert not yt.valid_client_version(bad), bad


def test_fixture_directory_size():
    total = sum(f.stat().st_size for f in FIX.iterdir())
    assert total <= 1_500_000, total
