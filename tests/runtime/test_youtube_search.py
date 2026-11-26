"""youtube_client.search() 與 fetch_client_versions()：兩條執行緒並行、共用截止時間、退回順序（InnerTube 兩邊 → 搜尋頁）。
假 urlopen 見 tests/support/youtube.py（會卡住的假網路要開執行緒，所以放 runtime 層）；真實回應 fixture 的來源見 tests/unit/test_youtube_client.py。
計畫 §7.1：Y1～Y6、Y8～Y11、Y14（search 層；Y7 Data API 退回已刪除）。截止時間用 config.YT_TIMEOUT_S 縮成 0.25 s 來測（產品值 2.0 s），不 sleep 等真的 2 秒。"""
import json
import socket
import threading
import time
import urllib.error

import pytest

from mac_ai import config
from mac_ai.router import pinyin, youtube_client as yt
from support.youtube import QUERY, Block, home, load, music_items, net, set_real, video, web_response  # noqa: F401

T = 0.25  # 測試用的 YT_TIMEOUT_S
SLACK = 0.1
GOOD = "dQw4w9WgXcQ"


@pytest.fixture
def short_timeout(monkeypatch):
    monkeypatch.setattr(config, "YT_TIMEOUT_S", T)


def timed(*a, **kw):
    t0 = time.monotonic()
    r = yt.search(*a, **kw)
    return r, time.monotonic() - t0


# ---------- Y3～Y5：真實 fixture 走完整條（每個端點恰好 1 次請求，不碰 googleapis）----------
REAL = [  # slug → (source, 播放的 videoId)；2026-11-21 抓到的真實回應的決策，改規則造成不同時要先看過再改這裡
    ("qingtian", "mv", "DYptgVvkVLQ"), ("jay_qingtian", "mv", "DYptgVvkVLQ"), ("qingtian_jay", "mv", "DYptgVvkVLQ"),
    ("daoxiang", "mv", "sHD_z90ZKV0"), ("shape_of_you", "mv", "JGwWNGJdvx8"), ("gaobai", "mv", "bu7nU9Mhpyo"),
    ("typo_daoxiang", "mv", "sHD_z90ZKV0"), ("typo_gaobai", "mv", "bu7nU9Mhpyo"), ("typo_jay_qingtian", "mv", "DYptgVvkVLQ"),
    ("despacito", "mv", "kJQP7kiw5Fk"), ("shinian", "mv", "JM9rx_hN1Ko"),
    ("jay_only", "ytm", "oXlA7LjkE0Q"),  # 只說歌手：歌手卡片第一首（使用者 2026-11-21）
    ("mayday_qingtian", "web", "g8upcg_IP3M"), ("rainy", "web", "9du5LTfkv7Y"),
    ("study", "web", "COGXH8sZZ2k"), ("sleep", "web", "WVE2BeNl5s0"),
]


@pytest.mark.parametrize("slug,source,vid", REAL)
def test_search_real_fixtures(net, slug, source, vid):
    set_real(net, slug)
    r = yt.search(QUERY[slug])
    assert (r.source, r.pick.video_id) == (source, vid)
    assert r.accepted is (source == "mv")
    assert r.errors == {} and r.stale == [] and r.url == ""
    assert net.count("web") == net.count("ytm") == 1 and net.count("api") == 0
    assert r.pick.candidates[0] == vid and len(r.pick.candidates) == len(set(r.pick.candidates)) <= 6


# ---------- Y5④、Y6：並行與截止時間 ----------
def test_web_fails_ytm_accepted_plays_ytm(net):
    net.set("web", urllib.error.HTTPError("https://x", 500, "e", {}, None)).set("ytm", load("ytm", "qingtian"))
    r = yt.search("晴天")
    assert (r.source, r.pick.video_id, r.pick.title, r.pick.channel) == ("ytm", "SJKoWAd5ySo", "晴天", "周杰倫")
    assert r.errors == {"web": "http_500"} and r.stale == [] and net.count("api") == 0


def test_ytm_fails_plays_web_first(net):
    net.set("web", load("web", "qingtian")).set("ytm", socket.timeout("t"))
    r = yt.search("晴天")
    assert (r.source, r.pick.video_id) == ("web", "DYptgVvkVLQ") and r.errors == {"ytm": "timeout"} and net.count("api") == 0


def test_ytm_not_accepted_and_web_failed_is_page(net):
    """YouTube Music 不接受、一般搜尋又失敗：沒有可播的影片 → 開搜尋頁"""
    net.set("ytm", load("ytm", "rainy")).set("web", b"{bad")
    r = yt.search("適合下雨天")
    assert (r.source, r.pick, r.url) == ("page", None, yt.search_url("適合下雨天")) and r.errors == {"web": "bad_json"}


@pytest.mark.parametrize("stall", ["web", "ytm", "both"])
def test_stalled_endpoint_decides_by_deadline(net, short_timeout, stall):
    net.set("web", load("web", "qingtian")).set("ytm", load("ytm", "qingtian"))
    for name in ("web", "ytm") if stall == "both" else (stall,):
        net.set(name, Block())
    r, dt = timed("晴天")
    assert dt <= T + SLACK, dt  # 從開始到決定
    assert dt >= T - 0.05  # 確實等了截止時間（沒有提早放棄另一邊）
    assert all(c["timeout"] <= T for c in net.calls)  # 傳給 urlopen 的 timeout 都不超過 YT_TIMEOUT_S
    expect = {"web": ("ytm", "SJKoWAd5ySo"), "ytm": ("web", "DYptgVvkVLQ"), "both": ("page", None)}[stall]
    assert (r.source, r.pick.video_id if r.pick else None) == expect
    assert r.errors == {n: "timeout" for n in (("web", "ytm") if stall == "both" else (stall,))}
    assert net.count("api") == 0
    assert net.count("web") == net.count("ytm") == 1


def test_fast_when_both_answer(net, short_timeout):
    set_real(net, "qingtian")
    r, dt = timed("晴天")
    assert dt < 0.1 and r.elapsed_s < 0.1  # 兩邊都回了就不等截止時間


def test_all_stalled_is_page_within_deadline(net, short_timeout):
    net.set("web", Block()).set("ytm", Block())
    r, dt = timed("晴天")
    assert dt <= T + SLACK and (r.source, r.pick) == ("page", None) and net.count("api") == 0


# ---------- Y8：InnerTube 兩邊失敗 → 搜尋頁 ----------
# ---------- Y7：InnerTube 兩邊失敗 → API ----------
FAILURES = {
    "http_400": urllib.error.HTTPError("https://x", 400, "e", {}, None),
    "http_500": urllib.error.HTTPError("https://x", 500, "e", {}, None),
    "timeout": socket.timeout("timed out"),
    "bad_json": b"{not json",
    "bad_format": [],
    "empty": {},  # 解析得到 0 筆
}


@pytest.mark.parametrize("kind", list(FAILURES))
def test_innertube_both_fail_is_page(net, kind):
    net.set("web", FAILURES[kind]).set("ytm", FAILURES[kind])
    r = yt.search("晴天 周杰倫")
    assert (r.source, r.pick, r.url) == ("page", None, yt.search_url("晴天 周杰倫")) and r.errors == {"web": kind, "ytm": kind}
    assert net.count("web") == net.count("ytm") == 1 and net.count("api") == 0


def test_web_empty_and_ytm_none_is_page(net):
    net.set("web", web_response([])).set("ytm", {"contents": {}})
    r = yt.search("x")
    assert r.source == "page" and r.errors == {"web": "empty", "ytm": "empty"}


# ---------- Y9：不讀金鑰、不打 googleapis（Data API 退回已刪除，使用者 2026-11-21）----------
def test_never_reads_key_or_calls_googleapis(net, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "env_value", lambda name: calls.append(name) or "x")
    for sc in ({"web": load("web", "qingtian"), "ytm": load("ytm", "qingtian")}, {"web": FAILURES["http_500"], "ytm": FAILURES["timeout"]}):
        for k, v in sc.items():
            net.set(k, v)
        yt.search("晴天")
    assert calls == [] and net.count("api") == 0 and not any("googleapis" in u for u in net.urls())


# ---------- Y10：clientVersion ----------
def test_4xx_marks_stale_and_does_not_retry(net):
    for failing, expect in (("web", ["web"]), ("ytm", ["ytm"]), ("both", ["web", "ytm"])):
        net.calls.clear()
        err = urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None)
        net.set("web", load("web", "qingtian")).set("ytm", load("ytm", "qingtian"))
        for n in ("web", "ytm") if failing == "both" else (failing,):
            net.set(n, err)
        r = yt.search("晴天")
        assert r.stale == expect and (r.pick is not None) is (failing != "both")
        assert net.count("web") == net.count("ytm") == 1, failing  # 每個端點只送一次，沒有當場重試
    net.set("web", urllib.error.HTTPError("https://x", 500, "e", {}, None))
    net.set("ytm", urllib.error.HTTPError("https://x", 429, "e", {}, None))
    assert yt.search("晴天").stale == ["ytm"]  # 5xx 不算；4xx 都算


def test_versions_come_from_caller(net):
    set_real(net, "qingtian")
    yt.search("晴天", {"web": "2.20271231.01.00", "ytm": "1.20271230.02.03"})
    bodies = {c["name"]: json.loads(c["data"]) for c in net.calls}
    assert bodies["web"]["context"]["client"]["clientVersion"] == "2.20271231.01.00"
    assert bodies["ytm"]["context"]["client"]["clientVersion"] == "1.20271230.02.03"
    net.calls.clear()
    yt.search("晴天")  # 沒有傳 → config 預設值
    bodies = {c["name"]: json.loads(c["data"]) for c in net.calls}
    assert bodies["web"]["context"]["client"]["clientVersion"] == config.YT_WEB_CLIENT_VERSION
    assert bodies["ytm"]["context"]["client"]["clientVersion"] == config.YT_MUSIC_CLIENT_VERSION
    net.calls.clear()
    yt.search("晴天", {"web": '2.2026"}; evil', "ytm": ""})  # 格式不符的不使用，退回預設值
    bodies = {c["name"]: json.loads(c["data"]) for c in net.calls}
    assert bodies["web"]["context"]["client"]["clientVersion"] == config.YT_WEB_CLIENT_VERSION
    assert bodies["ytm"]["context"]["client"]["clientVersion"] == config.YT_MUSIC_CLIENT_VERSION


def test_fetch_client_versions(net):
    net.set("home_web", home("web")).set("home_ytm", home("ytm"))
    assert yt.fetch_client_versions() == {"web": "2.20261119.07.00", "ytm": "1.20261117.06.00"}
    assert [c["url"] for c in net.calls if c["name"].startswith("home")] and all(c["timeout"] == 2.0 for c in net.calls)
    for c in net.calls:
        assert not {"cookie", "authorization"} & set(c["headers"]) and c["headers"]["user-agent"].startswith("Mozilla/5.0") and c["data"] is None
    assert {c["url"] for c in net.calls} == {config.YT_WEB_HOME_URL, config.YT_MUSIC_HOME_URL}


def test_fetch_client_versions_rejects_bad_format_and_failures(net):
    net.set("home_web", 'INNERTUBE_CLIENT_VERSION":"2.2026.evil"').set("home_ytm", home("ytm"))
    assert yt.fetch_client_versions() == {"ytm": "1.20261117.06.00"}  # 格式不符的不回傳
    net.set("home_web", urllib.error.HTTPError("https://x", 500, "e", {}, None)).set("home_ytm", b"<html>no version</html>")
    assert yt.fetch_client_versions() == {}
    net.set("home_web", socket.timeout("t")).set("home_ytm", "")
    assert yt.fetch_client_versions() == {}


def test_fetch_client_versions_deadline(net):
    net.set("home_web", Block()).set("home_ytm", home("ytm"))
    t0 = time.monotonic()
    assert yt.fetch_client_versions(timeout=0.2) == {"ytm": "1.20261117.06.00"}
    assert time.monotonic() - t0 <= 0.2 + SLACK and all(c["timeout"] <= 0.2 for c in net.calls)


# ---------- Y11：videoId 不合法 ----------
BAD = ['abc"; do shell script "x', "A" * 10, "A" * 12]


def test_invalid_ids_dropped_in_every_source(net):
    # 一般搜尋：不合法的 id 排在前面，只留合法的
    web = web_response([video(b, f"壞{i}") for i, b in enumerate(BAD)] + [video("AAAAAAAAAA1", "好 1"), video("AAAAAAAAAA2", "好 2")])
    net.set("web", web).set("ytm", load("ytm", "rainy"))
    r = yt.search("適合下雨天")
    assert r.pick.video_id == "AAAAAAAAAA1" and r.pick.candidates == ["AAAAAAAAAA1", "AAAAAAAAAA2"]
    # YouTube Music：第一筆 id 不合法 → 換下一筆（接受規則看的是下一筆）
    data = load("ytm", "qingtian")
    music_items(data)[0]["itemSectionRenderer"]["contents"][0]["musicResponsiveListItemRenderer"]["playlistItemData"]["videoId"] = BAD[0]
    net.set("web", load("web", "qingtian")).set("ytm", data)
    r = yt.search("晴天")
    assert r.hit.video_id == "ijgst6J7zVI" and r.accepted is False and r.source == "web"
    # YouTube Music 接受、而且是它的 id 不合法：絕對不會播它
    data = load("ytm", "qingtian")
    music_items(data)[0]["itemSectionRenderer"]["contents"][0]["musicResponsiveListItemRenderer"]["playlistItemData"]["videoId"] = BAD[0]
    for sec in music_items(data)[1:]:
        for it in sec["itemSectionRenderer"]["contents"]:
            it["musicResponsiveListItemRenderer"].get("playlistItemData", {}).pop("videoId", None)
    net.set("web", web_response([])).set("ytm", data)
    r = yt.search("晴天")
    assert (r.source, r.pick) == ("page", None)


def test_no_result_ever_carries_unvalidated_id(net):
    for slug, _, _ in REAL:
        set_real(net, slug)
        r = yt.search(QUERY[slug])
        assert yt.valid_video_id(r.pick.video_id) and all(yt.valid_video_id(i) for i in r.pick.candidates)


# ---------- Y14：隱私 ----------
def test_requests_carry_no_user_info_and_module_is_silent(net, capsys, caplog):
    """每種路徑（成功、失敗、4xx、卡住以外）都跑一遍：InnerTube 請求沒有 cookie、授權或使用者資訊；模組不印東西、不寫 log"""
    scenarios = [
        {"web": load("web", "qingtian"), "ytm": load("ytm", "qingtian")},
        {"web": FAILURES["http_400"], "ytm": FAILURES["timeout"]},
        {"web": FAILURES["http_500"], "ytm": FAILURES["bad_json"]},
        {"web": FAILURES["bad_format"], "ytm": FAILURES["empty"]},
    ]
    for sc in scenarios:
        net.calls.clear()
        for k, v in sc.items():
            net.set(k, v)
        yt.search("晴天 周杰倫")
        for c in net.calls:
            assert c["name"] in ("web", "ytm")
            assert not {"cookie", "authorization", "x-goog-api-key"} & set(c["headers"])
            body = json.loads(c["data"])
            assert set(body["context"]["client"]) == {"clientName", "clientVersion", "hl", "gl"} and body["query"] == "晴天 周杰倫"
    out = capsys.readouterr()
    assert out.out == "" and out.err == "" and caplog.text == ""


# ---------- 拼音在多執行緒下一致（搜尋執行緒與 command_worker 可能同時用）----------
def test_pinyin_thread_safe():
    pinyin._han_syllable.cache_clear()  # 從冷快取開始，讓各執行緒都真的去問 ICU
    texts = ["周杰倫", "稻香", "晴天", "告白氣球", "光年之外", "鄧紫棋", "七里香", "夜曲", "Shape of You", "五月天的晴天"]
    want = {t: pinyin.pinyin_syllables(t) for t in texts}
    pinyin._han_syllable.cache_clear()
    errors, results = [], []

    def work():
        try:
            for _ in range(200):
                for t in texts:
                    results.append((t, pinyin.pinyin_syllables(t)))
                pinyin._han_syllable.cache_clear() if _ % 50 == 0 else None
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert not errors and len(results) == 8 * 200 * len(texts)
    assert all(syl == want[t] for t, syl in results)
