"""音樂：點歌（play_media）、開啟／重複使用 YouTube 合輯分頁、背景音樂控制、播放事件與回饋（換一首、喜歡／不要再放、我最常聽什麼）。"""
import sqlite3
import time
import urllib.parse
from typing import List, Optional, Tuple

from mac_ai import config
from mac_ai.config import CONTEXT_TIMEOUT_S
from mac_ai.router import context, music_prefs, native, prefs_db, youtube_client
from mac_ai.router.browser_tools import TAB_BROWSERS
from mac_ai.router.exec_context import POLL_TIMEOUT_S, ExecContext
from mac_ai.router.prefs_db import _db_call
from mac_ai.router.proc import _run


# ---------- 點歌：開啟／重複使用 YouTube 合輯分頁（計畫 §4.2 第 7 點）----------
# 一支 osascript 在所有視窗找「YouTube 合輯分頁」（網址含 youtube.com/watch 與 list=RD）並當場改網址、切成該視窗的目前分頁；
# 找不到回 none，由呼叫端另外開新分頁。網址一律當 argv 傳（不拼進腳本）。Safari 沒在執行時直接回 none（不會把它開起來）。
# 0 個分頁的幽靈視窗（P0-8：關窗後 windows 清單還留一個）：URL of every tab 對它回空字串，不是 list，包成單一元素後略過
MUSIC_TAB_TIMEOUT_S = 3.0  # 找分頁改網址（實測 p50 約 0.2 s）與 open 的逾時
_MUSIC_WATCH_URL = "https://www.youtube.com/watch?v={id}&list=RD{id}"
_SAFARI_MUSIC_TAB_SCRIPT = (
    "on run argv\n"
    "set theUrl to item 1 of argv\n"
    'if application "Safari" is running then\n'
    "try\n"
    'tell application "Safari"\n'
    "set allUrls to URL of every tab of every window\n"
    "repeat with i from 1 to count of allUrls\n"
    "set u to item i of allUrls\n"
    "if class of u is not list then set u to {u}\n"
    "repeat with j from 1 to count of u\n"
    "set s to item j of u\n"
    "if s is not missing value then\n"
    'if s contains "youtube.com/watch" and s contains "list=RD" then\n'
    "set URL of tab j of window i to theUrl\n"
    "set current tab of window i to tab j of window i\n"
    'return "reused"\n'
    "end if\n"
    "end if\n"
    "end repeat\n"
    "end repeat\n"
    "end tell\n"
    "end try\n"
    "end if\n"
    'return "none"\n'
    "end run"
)
_CHROME_MUSIC_TAB_SCRIPT = (
    "on run argv\n"
    "set theUrl to item 1 of argv\n"
    'if application "Google Chrome" is running then\n'
    "try\n"
    'tell application "Google Chrome"\n'
    "repeat with w in windows\n"
    "repeat with i from 1 to count of tabs of w\n"
    "set s to URL of tab i of w\n"
    'if s contains "youtube.com/watch" and s contains "list=RD" then\n'
    "set URL of tab i of w to theUrl\n"
    "set active tab index of w to i\n"
    'return "reused"\n'
    "end if\n"
    "end repeat\n"
    "end repeat\n"
    "end tell\n"
    "end try\n"
    "end if\n"
    'return "none"\n'
    "end run"
)
_MUSIC_TAB_SCRIPTS = {"Safari": _SAFARI_MUSIC_TAB_SCRIPT, "Google Chrome": _CHROME_MUSIC_TAB_SCRIPT}


def music_watch_url(video_id: str) -> Optional[str]:
    """YouTube 合輯網址；video_id 不是合法的 11 字元編號回 None（外部輸入不可信，不能進 osascript 或 open 的參數）"""
    return _MUSIC_WATCH_URL.format(id=video_id) if youtube_client.valid_video_id(video_id) else None


def music_browser() -> str:
    """點歌用的瀏覽器：TAB_BROWSERS 裡第一個正在執行的（pgrep，不會啟動任何 App）；都沒開就用 Safari（要播放，啟動它是預期行為）"""
    for name in TAB_BROWSERS:
        if _run(["pgrep", "-x", name], config.CONTEXT_TIMEOUT_S)[0]:
            return name
    return TAB_BROWSERS[0]


def open_music_video(video_id: str) -> Tuple[bool, str, str]:
    """開啟點歌的影片 → (成功?, 做法 reuse／new 或失敗原因, 瀏覽器)。
    一支 osascript 找＋改網址（回 reused）；沒有音樂分頁、osascript 失敗或逾時就用 open -a 開新分頁（Safari 沒開時也會被啟動）"""
    url = music_watch_url(video_id)
    if url is None:
        return False, "影片編號格式不合法", ""
    browser = music_browser()
    ok, out = _run(["osascript", "-e", _MUSIC_TAB_SCRIPTS[browser], url], MUSIC_TAB_TIMEOUT_S)
    if ok and out.strip() == "reused":
        return True, "reuse", browser
    ok, err = _run(["open", "-a", browser, url], MUSIC_TAB_TIMEOUT_S)
    return (True, "new", browser) if ok else (False, err, browser)


def open_search_page(url: str) -> Tuple[bool, str]:
    """退回用：在點歌用的瀏覽器開 YouTube 搜尋結果頁（網址由 youtube_client.search_url 組出）→ (成功?, 失敗原因)"""
    ok, err = _run(["open", "-a", music_browser(), url], MUSIC_TAB_TIMEOUT_S)
    return ok, "" if ok else err


# ---------- 背景音樂控制（下一首、上一首、暫停、繼續；前景不是 YouTube／Music／Spotify 時）----------
# 只有 Safari 的 YouTube watch 頁（含 music.youtube.com、list=RD 合輯）算「音樂分頁」：點歌（play_media）開的就是這種網址。
# 先確認 Safari 正在執行再讀網址（script 裡也擋一次），不會因為檢查而把 Safari 開起來；只回傳網址，不記錄。
# 一支腳本一次讀回：第一行 "C:" ＝最近使用的視窗目前分頁（讀不到就是空的），其後每行 "T:" ＝所有視窗的所有分頁。
# 逐個視窗各自 try：0 個分頁的幽靈視窗只會被略過，不會讓整支腳本失敗
_SAFARI_TABS_SCRIPT = (
    'if application "Safari" is running then\n'
    'tell application "Safari"\n'
    'set out to "C:"\n'
    'try\n'
    'set out to out & (URL of current tab of front window)\n'
    'end try\n'
    'try\n'
    'repeat with i from 1 to (count of windows)\n'
    'try\n'
    'repeat with u in (URL of every tab of window i)\n'
    'try\n'
    'set out to out & linefeed & "T:" & (u as text)\n'
    'end try\n'
    'end repeat\n'
    'end try\n'
    'end repeat\n'
    'end try\n'
    'return out\n'
    'end tell\n'
    'end if\n'
    'return ""'
)


def _safari_pid() -> int:
    """Safari 的行程編號；沒在執行回傳 0（pgrep 不會啟動任何 App）"""
    ok, out = _run(["pgrep", "-x", "Safari"], CONTEXT_TIMEOUT_S)
    first = out.split()[0] if ok and out.split() else ""
    return int(first) if first.isdigit() else 0


def _is_music_url(url: str) -> bool:
    """YouTube（含子網域，例如 music.youtube.com）的 /watch?v=…"""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    site = context.site_for((parts.hostname or "").lower()) if parts.scheme in ("http", "https") else None
    return bool(site and site["name"] == "YouTube" and parts.path == "/watch" and urllib.parse.parse_qs(parts.query).get("v"))


def _safari_music_state(out: str) -> Tuple[bool, bool]:
    """解析 _SAFARI_TABS_SCRIPT 的輸出 → (最近使用的視窗目前分頁是音樂分頁?, 任何視窗的任何分頁是音樂分頁?)"""
    lines = out.splitlines()
    current = bool(lines) and lines[0].startswith("C:") and _is_music_url(lines[0][2:])
    return current, current or any(l.startswith("T:") and _is_music_url(l[2:]) for l in lines[1:])


def _control_op(id: str, direction: Optional[str]) -> str:
    """寫進 ctx.step["control"]["op"] 的操作名稱：next／prev／pause／resume／toggle（沒有方向的播放／暫停）"""
    return direction or {"next_track": "next", "prev_track": "prev"}.get(id, "toggle")


def _background_control(entry: dict, direction: Optional[str], ctx: ExecContext) -> str:
    """前景不是媒體 App 時控制背景的音樂。依序嘗試，前一個成功就停：
    ① Safari 正在執行、最近使用的視窗目前顯示的是音樂分頁 → 輔助使用 API 按那個視窗的播放器按鈕（source=ax）
       （按不到時，如果代表『已經是目標狀態』的按鈕還在，例如要暫停而畫面上是『播放』，就不再動作）
    ② Safari 任何視窗的任何分頁是音樂分頁 → 送一次媒體鍵（source=mediakey；媒體鍵 play 是切換，所以①能確認狀態時絕不送）。
       沒有音樂分頁就不送：媒體鍵會被別的『正在播放』來源（Music 等）接走，等於誤控制
    ③ 都不成立或失敗 → ⚠️。結果記在 ctx.step["control"]＝{op, source}。
    只讀一次 Safari（_SAFARI_TABS_SCRIPT，同時給①②用）；最差耗時＝pgrep 1 s＋osascript 5 s＋輔助使用 API"""
    spec = entry["background"]
    plan = spec.get(direction or "default") or spec["default"]
    op = _control_op(entry["id"], direction)

    def done(source: str, how: str, already: bool = False) -> str:
        if ctx.step is not None:
            ctx.step["control"] = {"op": op, "source": source, **({"already": True} if already else {})}
            ctx.step["context"]["method"] = how
        return f"✅ {plan['label']}" + ("：已經是這個狀態" if already else "") + f"（{how}）"

    pid = _safari_pid()
    has_music_tab = False
    if pid:
        ok, out = _run(["osascript", "-e", _SAFARI_TABS_SCRIPT], POLL_TIMEOUT_S)
        current_is_music, has_music_tab = _safari_music_state(out) if ok else (False, False)
        if current_is_music:
            status = native.press_button_in_window(pid, plan["button"])
            if status == "ok":
                return done("ax", "Safari 音樂分頁的按鈕")
            if status == "missing" and plan.get("done_when") and native.button_label(pid, plan["done_when"]):
                return done("ax", "Safari 音樂分頁的按鈕", already=True)
    if has_music_tab and native.post_media_key(plan["key"]) == "ok":
        return done("mediakey", "媒體鍵")
    return "⚠️ 找不到正在播放的音樂"


# ---------- 點歌（play_media，計畫 §4.2）----------
NO_DB_FAVORITE_MESSAGE = "⚠️ 偏好資料庫還沒準備好，沒辦法播常聽的歌；說『我想聽』加歌名"


def _video_row(conn: sqlite3.Connection, video_id: str) -> Tuple[str, str]:
    row = conn.execute("SELECT title, channel FROM videos WHERE video_id=?", (video_id,)).fetchone()
    return ((row[0] or ""), (row[1] or "")) if row else ("", "")


def _played(title: str, channel: str, video_id: str) -> str:
    return f"✅ 播放：{title or video_id}" + (f"（{channel}）" if channel else "")


def _play_favorite(conn: Optional[sqlite3.Connection], utterance: str, now: float, t0: float, info: dict) -> str:
    """query 是空字串：播常聽的歌（只讀資料庫、不連網）。沒有紀錄回 NO_HISTORY_MESSAGE（S6）"""
    if conn is None:
        return NO_DB_FAVORITE_MESSAGE
    t = time.perf_counter()
    fav = _db_call(info, music_prefs.favorite, conn, now, prefs_db._rng)
    info["db_ms"] = (time.perf_counter() - t) * 1000
    if fav is None or not youtube_client.valid_video_id(fav.video_id):
        return music_prefs.NO_HISTORY_MESSAGE
    info["source"] = "cache"
    t = time.perf_counter()
    ok, how, browser = open_music_video(fav.video_id)
    info.update(open_ms=(time.perf_counter() - t) * 1000, tab=how if ok else "", browser=browser)
    latency = (time.perf_counter() - t0) * 1000
    t = time.perf_counter()
    _db_call(info, music_prefs.record_event, conn, "play", now=now, utterance=utterance, query="", video_id=fav.video_id,
             source="cache", latency_ms=latency, ok=ok)
    if ok:
        _db_call(info, music_prefs.learn_play, conn, query="", source="cache", title=fav.title, channel=fav.channel,
                 artists=None, now=now)
    info["db_ms"] += (time.perf_counter() - t) * 1000
    return _played(fav.title, fav.channel, fav.video_id) if ok else f"❌ 開啟 YouTube 失敗：{fav.title or fav.video_id}（{how}）"


def _learn_after_play(conn: sqlite3.Connection, info: dict, *, utterance: str, query: str, corrected: str, now: float,
                      latency: float, res: Optional["youtube_client.SearchResult"], video_id: str, source: str,
                      title: str, channel: str, candidates: List[str]) -> None:
    """播放成功之後的寫入：play 事件、videos、terms、快取（§4.2 第 9 點）"""
    _db_call(info, music_prefs.record_event, conn, "play", now=now, utterance=utterance, query=query,
             corrected=corrected if corrected != query else None, video_id=video_id, source=source, latency_ms=latency)
    if source == "cache":
        _db_call(info, music_prefs.learn_play, conn, query=corrected, source=source, title=title, channel=channel,
                 artists=None, now=now)
        return
    channel_id = next((v.channel_id for v in (res.web if res else []) if v.video_id == video_id), None)
    _db_call(info, music_prefs.upsert_video, conn, video_id, title, channel, channel_id or None, now)
    if source in ("mv", "ytm") and res is not None and res.hit is not None:  # terms 用 YouTube Music 的歌名與歌手（乾淨，不用清理）
        # ytm 的歌名取 pick：只說歌手時 hit.title 是歌手名，播的是卡片第一首
        _db_call(info, music_prefs.learn_play, conn, query=corrected, source=source, title=title if source == "ytm" else res.hit.title,
                 channel="、".join(res.hit.artists), artists=res.hit.artists, now=now, video_id=video_id)
    else:
        _db_call(info, music_prefs.learn_play, conn, query=corrected, source=source, title=title, channel=channel,
                 artists=None, now=now)
    _db_call(info, music_prefs.cache_put, conn, corrected, video_id, candidates, now)


def play_media(query: str = "", ctx: Optional[ExecContext] = None) -> str:
    """點歌（計畫 §4.2）：拼音矯正 → 快取 → InnerTube 搜尋（youtube_client.search）→ 開啟（找分頁改網址，沒有就開新分頁）→ 寫紀錄。
    query 空字串＝常聽的歌。資料庫準備好之前（prefs_db.MUSIC_DB.conn() 是 None）照常搜尋、播放，只是不矯正、不查快取、不寫任何紀錄。
    所有資料庫讀寫都在這個函式的呼叫端執行緒（command_worker）；搜尋的網路在 youtube_client 自己的執行緒，不碰資料庫。
    各階段耗時與來源記在 ctx.step["music"]：source、search_ms、open_ms、db_ms、tab、browser、errors"""
    t0 = time.perf_counter()
    step = ctx.step if ctx is not None and ctx.step is not None else {}
    info = step["music"] = {}
    utterance = (ctx.utterance if ctx is not None and ctx.utterance else query) or ""
    now = prefs_db._now()
    conn = prefs_db.MUSIC_DB.conn()
    query = query.strip()
    if not query:
        return _play_favorite(conn, utterance, now, t0, info)

    corrected, hit, versions = query, None, None
    t = time.perf_counter()
    if conn is not None:
        corrected = _db_call(info, music_prefs.correct, conn, query, now, utterance) or query
        hit = _db_call(info, music_prefs.cache_get, conn, corrected, now)
        versions = _db_call(info, music_prefs.client_versions, conn)
    info["db_ms"] = (time.perf_counter() - t) * 1000

    res: Optional[youtube_client.SearchResult] = None
    if hit is not None and youtube_client.valid_video_id(hit.video_id):  # 快取命中：不連網
        source, video_id, candidates = "cache", hit.video_id, hit.candidates
        title, channel = _db_call(info, _video_row, conn, video_id) or ("", "")
    else:
        t = time.perf_counter()
        res = youtube_client.search(corrected, versions=versions)
        info["search_ms"] = (time.perf_counter() - t) * 1000
        info["errors"] = dict(res.errors)
        if res.stale and conn is not None:  # InnerTube 回 4xx：標記需要更新 clientVersion（定時整理會抓）
            _db_call(info, music_prefs.maintenance_set, conn, music_prefs.KEY_CLIENT_UPDATE_NEEDED, "1")
        if res.pick is None:
            return _play_search_page(conn, res, info, utterance=utterance, query=query, corrected=corrected, now=now, t0=t0)
        source, video_id, candidates = res.pick.source, res.pick.video_id, res.pick.candidates
        title, channel = res.pick.title, res.pick.channel
    info["source"] = source
    if corrected != query:
        info["corrected"] = corrected

    t = time.perf_counter()
    ok, how, browser = open_music_video(video_id)
    info.update(open_ms=(time.perf_counter() - t) * 1000, tab=how if ok else "", browser=browser)
    latency = (time.perf_counter() - t0) * 1000
    if conn is not None:
        t = time.perf_counter()
        if ok:
            _learn_after_play(conn, info, utterance=utterance, query=query, corrected=corrected, now=now, latency=latency,
                              res=res, video_id=video_id, source=source, title=title, channel=channel, candidates=candidates)
        else:
            _db_call(info, music_prefs.record_event, conn, "play", now=now, utterance=utterance, query=query,
                     corrected=corrected if corrected != query else None, video_id=video_id, source=source,
                     latency_ms=latency, ok=False)
        info["db_ms"] += (time.perf_counter() - t) * 1000
    return _played(title, channel, video_id) if ok else f"❌ 開啟 YouTube 失敗：{title or video_id}（{how}）"


def _play_search_page(conn: Optional[sqlite3.Connection], res: "youtube_client.SearchResult", info: dict, *, utterance: str,
                      query: str, corrected: str, now: float, t0: float) -> str:
    """搜尋全部失敗：開 YouTube 搜尋結果頁並回 ⚠️（source＝page）。事件：play（source＝page）＋fallback（source 欄存各來源的失敗分類）"""
    info["source"] = "page"
    t = time.perf_counter()
    ok, err = open_search_page(res.url)
    info["open_ms"] = (time.perf_counter() - t) * 1000
    if conn is not None:
        latency = (time.perf_counter() - t0) * 1000
        _db_call(info, music_prefs.record_event, conn, "play", now=now, utterance=utterance, query=query,
                 corrected=corrected if corrected != query else None, source="page", latency_ms=latency, ok=ok)
        _db_call(info, music_prefs.record_event, conn, "fallback", now=now, query=query,
                 source=";".join(f"{k}={v}" for k, v in res.errors.items()), latency_ms=latency, ok=False)
    if not ok:
        return f"❌ 開啟 YouTube 搜尋頁失敗（{err}）"
    return f"⚠️ 找不到可以直接播放的結果，已開啟 YouTube 搜尋頁：{query}"


# ---------- 播放控制事件、換一首、喜歡／不要再放、我最常聽什麼（計畫 §4.3、§4.4）----------
_CONTROL_EVENTS = ("next", "prev", "pause", "resume")  # toggle（沒有方向的『播放』）不記：播放或暫停取決於當時狀態，讀不到，而且 events 沒有對應的 kind
NO_DB_MUSIC_MESSAGE = "⚠️ 偏好資料庫還沒準備好，這句沒有處理"
NO_RECENT_PLAY_MESSAGE = "⚠️ 找不到最近播放的歌，說『我想聽』加歌名"
NO_CANDIDATE_MESSAGE = "⚠️ 這首沒有其他候選可以換了，說『我想聽』加歌名重新找"
NO_MUSIC_TAB_MESSAGE = "⚠️ 讀不到正在播放的音樂分頁，沒有記下"


def _record_control(ctx: ExecContext) -> None:
    """控制鏈成功之後（ctx.step["control"] 有值）寫 next／prev／pause／resume 事件，source＝app／ax／mediakey。
    『已經是這個狀態』沒有動作、toggle 讀不到結果，都不記。播放後 30 秒內的第一個 next 會被 video_scores 扣分（S4）"""
    control = ctx.step.get("control") if ctx.step is not None else None
    if not control or control.get("already") or control["op"] not in _CONTROL_EVENTS:
        return
    conn = prefs_db.MUSIC_DB.conn()
    if conn is not None:
        _db_call(ctx.step.setdefault("music", {}), music_prefs.record_event, conn, control["op"], now=prefs_db._now(),
                 utterance=ctx.utterance or None, source=control["source"])


def music_replace(ctx: Optional[ExecContext] = None) -> str:
    """換一首／不是這首（K7）：最近一次 play 事件給 query 與目前的影片；該 query 的候選清單（queries.results）裡，
    目前影片的下一筆就是要換的（第一次換第 2 筆、再換第 3 筆）。成功後快取改指向它並釘選、寫 replace 事件（舊歌名扣分）
    與 source＝replace 的 play 事件（query 沿用原本的，下一次換才找得到同一份候選）。常聽的歌（query 是空字串）沒有候選。不連網"""
    t0 = time.perf_counter()
    step = ctx.step if ctx is not None and ctx.step is not None else {}
    info = step["music"] = {}
    conn = prefs_db.MUSIC_DB.conn()
    if conn is None:
        return NO_DB_MUSIC_MESSAGE
    now = prefs_db._now()
    last = _db_call(info, lambda: conn.execute(
        "SELECT query, corrected, video_id FROM events WHERE kind='play' AND ok=1 AND video_id IS NOT NULL AND source<>'page' "
        "ORDER BY id DESC LIMIT 1").fetchone())
    key = ((last[1] or last[0] or "") if last else "").strip()
    if not key:
        return NO_RECENT_PLAY_MESSAGE
    hit = _db_call(info, music_prefs.cache_get, conn, key, now)
    cands = [c for c in (hit.candidates if hit else []) if youtube_client.valid_video_id(c)]
    current = last[2]
    nxt = cands[cands.index(current) + 1] if current in cands and cands.index(current) + 1 < len(cands) else None
    if nxt is None:
        return NO_CANDIDATE_MESSAGE
    info["source"] = "replace"
    t = time.perf_counter()
    ok, how, browser = open_music_video(nxt)
    info.update(open_ms=(time.perf_counter() - t) * 1000, tab=how if ok else "", browser=browser)
    title, channel = _db_call(info, _video_row, conn, nxt) or ("", "")
    if not ok:
        return f"❌ 開啟 YouTube 失敗：{title or nxt}（{how}）"
    t = time.perf_counter()
    old_title = (_db_call(info, _video_row, conn, current) or ("", ""))[0]
    _db_call(info, music_prefs.cache_repin, conn, key, nxt, now)
    _db_call(info, music_prefs.on_replace, conn, old_title, now, current)
    _db_call(info, music_prefs.record_event, conn, "play", now=now, utterance=ctx.utterance if ctx is not None else None,
             query=last[0], corrected=last[1], video_id=nxt, source="replace", latency_ms=(time.perf_counter() - t0) * 1000)
    info["db_ms"] = (time.perf_counter() - t) * 1000
    return f"✅ 換一首（第 {cands.index(nxt) + 1}／{len(cands)} 個候選）：{title or nxt}" + (f"（{channel}）" if channel else "")


def _music_tab_video_id() -> Optional[str]:
    """Safari 音樂分頁目前播的 videoId（讀不到回 None）。重用 _SAFARI_TABS_SCRIPT：pgrep 確認 Safari 在執行才跑一支 osascript
    （不會啟動 Safari；幽靈視窗由腳本略過）。優先找 YouTube watch 網址帶 list=RD 的分頁（點歌開的合輯，播到下一首時網址會跟著變）：
    最近使用的視窗目前分頁是的話用它，否則第一個；都沒有才退而用最近使用視窗目前的 YouTube watch 分頁"""
    if not _safari_pid():
        return None
    ok, out = _run(["osascript", "-e", _SAFARI_TABS_SCRIPT], POLL_TIMEOUT_S)
    if not ok:
        return None
    lines = out.splitlines()
    current = lines[0][2:] if lines and lines[0].startswith("C:") else ""
    urls = [l[2:] for l in lines[1:] if l.startswith("T:")]

    def vid(url: str, rd: bool) -> Optional[str]:
        if not _is_music_url(url):
            return None
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url.strip()).query)
        v = (q.get("v") or [""])[0]
        if rd and not any(x.startswith("RD") for x in q.get("list", [])):
            return None
        return v if youtube_client.valid_video_id(v) else None

    return next((v for v in [vid(current, True)] + [vid(u, True) for u in urls] + [vid(current, False)] if v), None)


def music_feedback(kind: str, ctx: Optional[ExecContext] = None) -> str:
    """我喜歡這首（like）／不要再放這首（dislike）（K8）：讀 Safari 音樂分頁目前的 videoId，交給 on_like／on_dislike（它們自己寫事件）。
    讀不到就回 ⚠️、不寫事件。dislike 之後不會自動跳下一首"""
    if kind not in ("like", "dislike"):
        return f"❌ 不支援的回饋：{kind}"
    step = ctx.step if ctx is not None and ctx.step is not None else {}
    info = step["music"] = {}
    conn = prefs_db.MUSIC_DB.conn()
    if conn is None:
        return NO_DB_MUSIC_MESSAGE
    t = time.perf_counter()
    video_id = _music_tab_video_id()
    info["read_ms"] = (time.perf_counter() - t) * 1000
    if video_id is None:
        return NO_MUSIC_TAB_MESSAGE
    title = (_db_call(info, _video_row, conn, video_id) or ("", ""))[0]
    _db_call(info, music_prefs.on_like if kind == "like" else music_prefs.on_dislike, conn, video_id, None, prefs_db._now())
    if "db_error" in info:
        return f"❌ 沒有記下（資料庫錯誤：{info['db_error']}）"
    info["feedback"] = {"kind": kind, "video_id": video_id}
    what = title or video_id
    return f"✅ 記下了，你喜歡：{what}" if kind == "like" else f"✅ 記下了，之後不會再自動放：{what}"


def music_top(ctx: Optional[ExecContext] = None) -> str:
    """我最常聽什麼（S8）：前 5 名歌手與歌曲，文字放進 ctx.replies（不呼叫 LLM）。沒有紀錄回 NO_HISTORY_MESSAGE"""
    conn = prefs_db.MUSIC_DB.conn()
    if conn is None:
        return NO_DB_MUSIC_MESSAGE
    ranked = _db_call({}, music_prefs.top, conn, prefs_db._now(), music_prefs.TOP_N)
    if ranked is None or not (ranked.artists or ranked.songs):
        return music_prefs.NO_HISTORY_MESSAGE
    lines = ["你最常聽的："]
    if ranked.artists:
        lines.append("歌手：" + "、".join(f"{i}. {a.name}" for i, a in enumerate(ranked.artists, 1)))
    if ranked.songs:
        lines.append("歌曲：" + "、".join(f"{i}. {x.title or x.video_id}" + (f"（{x.channel}）" if x.channel else "")
                                          for i, x in enumerate(ranked.songs, 1)))
    text = "\n".join(lines)
    if ctx is not None:
        ctx.replies.append(text)
    return f"✅ {text}"
