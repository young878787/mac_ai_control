"""點歌偏好：事件紀錄、計分、點歌字詞（terms）矯正。拼音比對在 pinyin。
事件、快取、terms 增刪改查、計分、矯正與定時整理（計畫 §4.4、§4.5）。本模組不碰網路與子程序。
資料庫函式一律收呼叫端（command_worker 執行緒）的 sqlite3.Connection、注入的 now（秒）與 rng：不自己連線、不開執行緒、
不讀 time／random 全域；每個公開函式自己 commit（一個函式一個交易）；SQL 全部參數化。"""
import json
import random
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from mac_ai import config
from mac_ai.router.pinyin import pinyin_key

# ======================================================================================
# 資料庫（P4）：以下函式都在 command_worker 執行緒呼叫
# ======================================================================================
HALF_LIFE_S = 30 * 86400.0  # 計分與 terms 權重的半衰期
LIKE_SCORE = 3.0
SKIP_PENALTY = 0.3  # 播放後 SKIP_WINDOW_S 內說「下一首」扣的分
SKIP_WINDOW_S = 30.0
FAVORITE_POOL = 20  # 常聽的歌：從前 20 名抽
FAVORITE_EXCLUDE_RECENT = 10  # 排除最近 10 首
TOP_N = 5
CORRECT_MIN_PLAYS = 2  # 矯正門檻：播過至少 2 次（或按過喜歡）
REPLACE_PENALTY = 0.5
TERM_MIN_WEIGHT = 0.05
CACHE_RESULTS_MAX = 6

NO_HISTORY_MESSAGE = "⚠️ 還沒有聽歌紀錄，說『我想聽』加歌名"  # favorite() 回 None 時，呼叫端回這句（S6）

# maintenance 表的 key
KEY_WEB_VERSION = "client_version_web"
KEY_YTM_VERSION = "client_version_ytm"
KEY_CLIENT_UPDATE_NEEDED = "client_update_needed"  # "1"＝InnerTube 回 4xx、需要更新 clientVersion
KEY_LAST_RUN = "last_run"

LEARN_SOURCES = ("cache", "mv", "ytm", "web")  # 真的播出去的來源（page 是退回搜尋頁，不算）
_KIND_RANK = {"artist": 0, "title": 1, "query": 2}  # 同一個詞同時是歌手、歌名、說法時留最具體的
_VERSION_RE = re.compile(r"[0-9]\.[0-9]{8}\.[0-9]{2}\.[0-9]{2}")  # 和 youtube_client 同一個格式（它 import 本模組，不能反過來）


def _decay(dt: float) -> float:
    return 0.5 ** (max(dt, 0.0) / HALF_LIFE_S)


# ---------- 事件與影片 ----------
def record_event(conn: sqlite3.Connection, kind: str, *, now: float, utterance: Optional[str] = None,
                 query: Optional[str] = None, corrected: Optional[str] = None, video_id: Optional[str] = None,
                 source: Optional[str] = None, latency_ms: Optional[float] = None, ok: bool = True) -> int:
    """追加一筆事件，回傳 id。like／dislike／replace 事件的 query 欄位存歌名（rebuild 重算 terms 用）"""
    with conn:
        cur = conn.execute(
            "INSERT INTO events (ts, kind, utterance, query, corrected, video_id, source, latency_ms, ok) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (now, kind, utterance, query, corrected, video_id, source, latency_ms, 1 if ok else 0))
    return int(cur.lastrowid)


def _upsert_video(conn, video_id, title, channel, channel_id, now) -> None:
    conn.execute(
        "INSERT INTO videos (video_id, title, channel, channel_id, first_seen, last_seen) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(video_id) DO UPDATE SET title=COALESCE(NULLIF(excluded.title,''), title), "
        "channel=COALESCE(NULLIF(excluded.channel,''), channel), "
        "channel_id=COALESCE(NULLIF(excluded.channel_id,''), channel_id), last_seen=excluded.last_seen",
        (video_id, title, channel, channel_id, now, now))


def upsert_video(conn: sqlite3.Connection, video_id: str, title: str, channel: str, channel_id: Optional[str],
                 now: float) -> None:
    """新增或更新影片資料：first_seen 保留第一次；空字串不覆蓋已有的值"""
    with conn:
        _upsert_video(conn, video_id, title, channel, channel_id, now)


# ---------- 快取（queries 表；§4.2 第 2 點）----------
@dataclass
class CacheHit:
    video_id: str
    candidates: List[str] = field(default_factory=list)  # 選中的排第一，其餘是「換一首」的候選（最多 6 筆）
    pinned: bool = False


def _nq(query: str) -> str:
    """快取的 key：NFKC、小寫、空白收成一個（「Shape of You」和「shape  of you」同一筆）"""
    return " ".join(unicodedata.normalize("NFKC", query).lower().split())


def cache_get(conn: sqlite3.Connection, query: str, now: float, ttl: float = config.MUSIC_CACHE_TTL_S) -> Optional[CacheHit]:
    """未釘選且 updated 超過 ttl 視為沒命中（不刪，之後 cache_put 會覆蓋）；釘選的不過期。
    命中時把 updated 刷新成 now（滑動期限：「30 天內沒用過」才過期，常聽的歌不會因為第一次查了 30 天就重查）"""
    key = _nq(query)
    row = conn.execute("SELECT video_id, results, updated, pinned FROM queries WHERE query=?", (key,)).fetchone()
    if row is None:
        return None
    video_id, results, updated, pinned = row
    if not pinned and now - updated > ttl:
        return None
    try:
        cands = [c for c in json.loads(results) if isinstance(c, str)] if results else []
    except (ValueError, TypeError):
        cands = []
    if video_id not in cands:
        cands.insert(0, video_id)
    if now > updated:
        with conn:
            conn.execute("UPDATE queries SET updated=? WHERE query=?", (now, key))
    return CacheHit(video_id, cands[:CACHE_RESULTS_MAX], bool(pinned))


def cache_put(conn: sqlite3.Connection, query: str, video_id: str, candidates: Sequence[str], now: float,
              pinned: bool = False) -> None:
    """寫入或覆蓋。candidates 最多留 6 筆（video_id 排第一）；已經釘選的列不會被降成未釘選"""
    cands = [video_id] + [c for c in candidates if c != video_id]
    with conn:
        conn.execute(
            "INSERT INTO queries (query, video_id, results, updated, pinned) VALUES (?,?,?,?,?) "
            "ON CONFLICT(query) DO UPDATE SET video_id=excluded.video_id, results=excluded.results, "
            "updated=excluded.updated, pinned=MAX(pinned, excluded.pinned)",
            (_nq(query), video_id, json.dumps(cands[:CACHE_RESULTS_MAX]), now, 1 if pinned else 0))


def cache_repin(conn: sqlite3.Connection, query: str, video_id: str, now: float) -> None:
    """換一首：這個 query 改指向新影片並釘選（候選清單原樣保留；沒有這一列就新增，候選只有它）"""
    key = _nq(query)
    with conn:
        cur = conn.execute("UPDATE queries SET video_id=?, pinned=1, updated=? WHERE query=?", (video_id, now, key))
        if cur.rowcount == 0:
            conn.execute("INSERT INTO queries (query, video_id, results, updated, pinned) VALUES (?,?,?,?,1)",
                         (key, video_id, json.dumps([video_id]), now))


# ---------- 清理 YouTube 標題與頻道名稱（web 來源才需要）----------
_NOISE_EN = ("official music video", "official lyric video", "official video", "official audio", "official mv",
             "music video", "lyric video", "lyrics video", "lyrics", "lyric", "official", "mv")
_NOISE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(t) for t in _NOISE_EN) + r")(?![A-Za-z0-9])|動態歌詞|歌詞版|歌詞|官方版",
    re.I)
_EMPTY_BRACKETS_RE = re.compile(r"[【\[(（][\s\-|/·]*[】\])）]")
_BRACKET_CHARS_RE = re.compile(r"[【】\[\]()（）]")
_CHANNEL_NOISE_RE = re.compile(
    r"\s*[-–]\s*topic\s*$|官方頻道|官方频道|official\s+artist\s+channel|official\s+channel|(?<![A-Za-z0-9])official(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9])vevo(?![A-Za-z0-9])|官方", re.I)


def _tidy(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s or "").split()).strip(" -–|｜/·:：")


def clean_title(title: str) -> str:
    """去掉 Official／MV／Lyrics／歌詞／Official Music Video 等標記，以及【】[]() 括號本身（括號內有用的字保留）。
    盡力而為：「周杰倫 Jay Chou【晴天 Sunny Day】Official MV」→「周杰倫 Jay Chou 晴天 Sunny Day」。重複清理結果不變"""
    s = _NOISE_RE.sub(" ", title or "")
    s = _EMPTY_BRACKETS_RE.sub(" ", s)
    return _tidy(_BRACKET_CHARS_RE.sub(" ", s))


def clean_channel(channel: str) -> str:
    """去掉「- Topic」「官方頻道」「Official」「VEVO」等字尾。重複清理結果不變"""
    return _tidy(_CHANNEL_NOISE_RE.sub(" ", channel or ""))


# ---------- terms：只改 terms 表的函式（不寫事件、不 commit；rebuild 也用它們）----------
def _decayed(weight: float, updated: float, now: float) -> float:
    return weight * _decay(now - updated)


def _bump_term(conn, term: str, kind: str, now: float, *, dweight: float, dplays: int = 0, liked: bool = False) -> None:
    term = _tidy(term)
    key = pinyin_key(term) if term else ""
    if not key:
        return
    row = conn.execute("SELECT kind, weight, plays, liked, updated FROM terms WHERE term=?", (term,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO terms (term, kind, pinyin, weight, plays, liked, updated) VALUES (?,?,?,?,?,?,?)",
                     (term, kind, key, dweight, dplays, 1 if liked else 0, now))
        return
    k, w, p, l, u = row
    conn.execute("UPDATE terms SET kind=?, weight=?, plays=?, liked=?, updated=? WHERE term=?",
                 (kind if _KIND_RANK[kind] < _KIND_RANK[k] else k, _decayed(w, u, now) + dweight, p + dplays,
                  1 if (l or liked) else 0, max(u, now), term))


def _play_terms(source: str, title: str, channel: str, artists: Optional[Sequence[str]]) -> Tuple[List[str], str]:
    """（歌手們, 歌名）。mv／ytm 給了歌手就直接用 YouTube Music 的欄位；其餘從標題與頻道清理（頻道可以是「、」連接的多位歌手）"""
    if source in ("mv", "ytm") and artists:
        return [a for a in (_tidy(x) for x in artists) if a], _tidy(title)
    return [a for a in (clean_channel(p) for p in (channel or "").split("、")) if a], clean_title(title)


def apply_play(conn, *, query: str, source: str, title: str, channel: str, artists: Optional[Sequence[str]],
               now: float) -> None:
    """播放成功的 terms 更新：歌手、歌名、使用者說法各 plays+1、weight+1（同一個詞在一次播放只算一次，留最具體的 kind）"""
    singers, song = _play_terms(source, title, channel, artists)
    wanted: Dict[str, str] = {}
    for kind, term in [("artist", a) for a in singers] + [("title", song), ("query", query)]:
        term = _tidy(term)
        if term and (term not in wanted or _KIND_RANK[kind] < _KIND_RANK[wanted[term]]):
            wanted[term] = kind
    for term, kind in wanted.items():
        _bump_term(conn, term, kind, now, dweight=1.0, dplays=1)


def _find_title_term(conn, title: str) -> Optional[str]:
    """歌名對應到的 term：原樣或清理後的寫法，哪個已經存在就用哪個"""
    for cand in dict.fromkeys((_tidy(title), clean_title(title))):
        if cand and conn.execute("SELECT 1 FROM terms WHERE term=?", (cand,)).fetchone():
            return cand
    return None


def apply_like(conn, title: str, now: float) -> None:
    term = _find_title_term(conn, title) or clean_title(title)
    _bump_term(conn, term, "title", now, dweight=LIKE_SCORE, liked=True)


def apply_dislike(conn, title: str) -> None:
    term = _find_title_term(conn, title)
    if term:
        conn.execute("DELETE FROM terms WHERE term=? AND kind='title'", (term,))


def apply_replace(conn, title: str, now: float) -> None:
    term = _find_title_term(conn, title)
    if term:
        _bump_term(conn, term, "title", now, dweight=-REPLACE_PENALTY)


# ---------- 學習（§4.5「新增」「更新」）----------
def learn_play(conn: sqlite3.Connection, *, query: str, source: str, title: str, channel: str,
               artists: Optional[List[str]], now: float, video_id: Optional[str] = None) -> None:
    """播放成功後呼叫。source：cache／mv／ytm／web。
    mv／ytm 時 title、artists 是 YouTube Music 的歌名與歌手（不清理）；web 時 title、channel 是 YouTube 的標題與頻道（清理後才存）；
    cache 時 title、channel 是 videos 裡存的值。
    給了 video_id 而且是 mv／ytm（artists 非空）時，會把 videos 那一列的 title、channel 存成 YouTube Music 的歌名與「、」連接的歌手
    ——這是 rebuild 能從 events＋videos 重算出相同 terms 的條件（mv 的影片自己的標題不再保留）。"""
    if source not in LEARN_SOURCES:
        return
    with conn:
        if video_id and source in ("mv", "ytm") and artists:
            _upsert_video(conn, video_id, title, "、".join(artists), None, now)
        apply_play(conn, query=query, source=source, title=title, channel=channel, artists=artists, now=now)


def _video_title(conn, video_id: str) -> str:
    row = conn.execute("SELECT title FROM videos WHERE video_id=?", (video_id,)).fetchone()
    return (row[0] or "") if row else ""


def on_like(conn: sqlite3.Connection, video_id: str, title: Optional[str], now: float) -> None:
    """寫 like 事件；歌名的 term liked=1、weight+3（沒有就新增）。title 是 None 時從 videos 讀；讀不到歌名只寫事件"""
    title = title if title is not None else _video_title(conn, video_id)
    with conn:
        conn.execute("INSERT INTO events (ts, kind, query, video_id, ok) VALUES (?,?,?,?,1)", (now, "like", title, video_id))
        if title:
            apply_like(conn, title, now)


def on_dislike(conn: sqlite3.Connection, video_id: str, title: Optional[str], now: float) -> None:
    """寫 dislike 事件；刪除歌名的 term，並刪掉指向這支影片的快取列（否則同一句還是會播它）。這支影片從此不會被常聽的歌選到"""
    title = title if title is not None else _video_title(conn, video_id)
    with conn:
        conn.execute("INSERT INTO events (ts, kind, query, video_id, ok) VALUES (?,?,?,?,1)",
                     (now, "dislike", title, video_id))
        if title:
            apply_dislike(conn, title)
        conn.execute("DELETE FROM queries WHERE video_id=?", (video_id,))


def on_replace(conn: sqlite3.Connection, old_title: str, now: float, video_id: Optional[str] = None) -> None:
    """換一首：寫 replace 事件（query 欄位存歌名），原歌名的 term weight −0.5（沒有這個 term 就不動）"""
    with conn:
        conn.execute("INSERT INTO events (ts, kind, query, video_id, ok) VALUES (?,?,?,?,1)",
                     (now, "replace", old_title, video_id))
        apply_replace(conn, old_title, now)


# ---------- 計分（§4.4）：影片分數從 events 算，不另存 ----------
def video_scores(conn: sqlite3.Connection, now: float) -> Tuple[Dict[str, float], set]:
    """（{video_id: 分數}, 被 dislike 的 video_id 集合）。每個訊號依自己發生的時間做半衰期 30 天的衰減：
    play（ok）＋1；like ＋3；play 之後 SKIP_WINDOW_S（含）內的第一個 next 對那支影片扣 0.3（31 秒不扣）；dislike 永遠排除"""
    scores: Dict[str, float] = {}
    disliked = set()
    last_play: Optional[Tuple[float, str]] = None  # 還沒被 next 扣過的最近一次播放
    for ts, kind, vid, source, ok in conn.execute(
            "SELECT ts, kind, video_id, source, ok FROM events WHERE kind IN ('play','like','dislike','next') ORDER BY id"):
        if kind == "play":
            if ok and vid and source != "page":
                scores[vid] = scores.get(vid, 0.0) + _decay(now - ts)
                last_play = (ts, vid)
        elif kind == "like":
            if vid:
                scores[vid] = scores.get(vid, 0.0) + LIKE_SCORE * _decay(now - ts)
        elif kind == "dislike":
            if vid:
                disliked.add(vid)
        elif kind == "next" and last_play is not None:
            if 0 <= ts - last_play[0] <= SKIP_WINDOW_S:
                scores[last_play[1]] = scores.get(last_play[1], 0.0) - SKIP_PENALTY * _decay(now - ts)
            last_play = None
    return scores, disliked


def video_score(conn: sqlite3.Connection, video_id: str, now: float) -> float:
    return video_scores(conn, now)[0].get(video_id, 0.0)


class Favorite(NamedTuple):
    video_id: str
    title: str
    channel: str  # ytm／mv 學來的影片是「、」連接的歌手
    score: float


def _recent_videos(conn, limit: int) -> List[str]:
    out: List[str] = []
    for (vid,) in conn.execute("SELECT video_id FROM events WHERE kind='play' AND ok=1 AND video_id IS NOT NULL "
                               "AND source<>'page' ORDER BY id DESC"):
        if vid not in out:
            out.append(vid)
            if len(out) >= limit:
                break
    return out


def favorite(conn: sqlite3.Connection, now: float, rng: random.Random) -> Optional[Favorite]:
    """常聽的歌：分數前 20 名（分數 > 0、沒被 dislike）排除最近 10 首後，依分數當權重抽一首。不連網。
    沒有任何紀錄回 None（呼叫端回 NO_HISTORY_MESSAGE）。排除之後一首不剩（總共不到 11 首）時，退而只排除最近一首，
    還是不剩（只有一首）就用它。"""
    scores, disliked = video_scores(conn, now)
    ranked = sorted(((v, s) for v, s in scores.items() if s > 0 and v not in disliked), key=lambda x: (-x[1], x[0]))
    pool = ranked[:FAVORITE_POOL]
    if not pool:
        return None
    recent = _recent_videos(conn, FAVORITE_EXCLUDE_RECENT)
    cands = [x for x in pool if x[0] not in recent]
    if not cands:
        cands = [x for x in pool if x[0] != recent[0]] or pool
    vid, score = rng.choices(cands, weights=[s for _, s in cands])[0]
    row = conn.execute("SELECT title, channel FROM videos WHERE video_id=?", (vid,)).fetchone()
    return Favorite(vid, (row[0] or "") if row else "", (row[1] or "") if row else "", score)


class RankedSong(NamedTuple):
    video_id: str
    title: str
    channel: str
    score: float


class RankedArtist(NamedTuple):
    name: str
    weight: float  # 衰減到 now 的權重


class Top(NamedTuple):
    artists: List[RankedArtist]
    songs: List[RankedSong]


def top(conn: sqlite3.Connection, now: float, n: int = TOP_N) -> Top:
    """「我最常聽什麼」：前 n 名歌手（terms 的 artist，衰減到 now 的權重）與歌曲（影片分數 > 0、沒被 dislike）。沒有紀錄時兩個清單都是空的"""
    scores, disliked = video_scores(conn, now)
    best = sorted(((v, s) for v, s in scores.items() if s > 0 and v not in disliked), key=lambda x: (-x[1], x[0]))[:n]
    songs = []
    for vid, s in best:
        row = conn.execute("SELECT title, channel FROM videos WHERE video_id=?", (vid,)).fetchone()
        songs.append(RankedSong(vid, (row[0] or "") if row else "", (row[1] or "") if row else "", s))
    arts = sorted(((t, _decayed(w, u, now)) for t, w, u in
                   conn.execute("SELECT term, weight, updated FROM terms WHERE kind='artist'")), key=lambda x: (-x[1], x[0]))
    return Top([RankedArtist(t, w) for t, w in arts if w > 0][:n], songs)


# ---------- 矯正（§4.5「矯正規則」）----------
_SEG_RE = re.compile(r"(的|\s+)")  # 用「的」與空白切段（括號保留分隔符，改寫後原樣接回去）


def _correct_segment(conn, seg: str) -> Optional[str]:
    key = pinyin_key(seg)
    if not key:
        return None
    rows = conn.execute("SELECT term, plays, liked FROM terms WHERE pinyin=?", (key,)).fetchall()
    low = seg.lower()
    if any(t == seg or t.lower() == low for t, _, _ in rows):  # 條件 1：本身不在 terms 裡
        return None
    if len(rows) != 1:  # 條件 2：整段拼音只對到一個 term
        return None
    term, plays, liked = rows[0]
    if plays < CORRECT_MIN_PLAYS and not liked:  # 條件 3：播過至少 2 次，或按過喜歡
        return None
    return term


def correct(conn: sqlite3.Connection, query: str, now: float, utterance: Optional[str] = None) -> str:
    """拼音矯正：用「的」與空白切段，每段三個條件全部成立才改寫，其餘原樣。有改寫就寫一筆 correct 事件
    （query＝矯正前、corrected＝矯正後、utterance＝原句）。回傳矯正後的 query（沒改就是原字串）"""
    parts = _SEG_RE.split(query)
    changed = False
    for i in range(0, len(parts), 2):  # 偶數位是內容、奇數位是分隔符
        if not parts[i].strip():
            continue
        new = _correct_segment(conn, parts[i])
        if new is not None and new != parts[i]:
            parts[i] = new
            changed = True
    if not changed:
        return query
    result = "".join(parts)
    record_event(conn, "correct", now=now, utterance=utterance, query=query, corrected=result)
    return result


# ---------- 定時整理（§4.5）----------
def maintenance_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM maintenance WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set_maintenance(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO maintenance (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def maintenance_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        _set_maintenance(conn, key, value)


def client_versions(conn: sqlite3.Connection) -> Dict[str, str]:
    """給 youtube_client.search(versions=...)：maintenance 裡有（而且格式合法）的用它，否則用 config 預設"""
    out = {"web": config.YT_WEB_CLIENT_VERSION, "ytm": config.YT_MUSIC_CLIENT_VERSION}
    for name, key in (("web", KEY_WEB_VERSION), ("ytm", KEY_YTM_VERSION)):
        v = maintenance_get(conn, key)
        if v and _VERSION_RE.fullmatch(v):
            out[name] = v
    return out


def store_client_versions(conn: sqlite3.Connection, versions: Dict[str, str]) -> bool:
    """把背景執行緒讀到的新版本（{"web": …, "ytm": …}）寫進 maintenance；格式不符的不寫。有寫進任何一個就清掉「需要更新」旗標，回傳是否有寫入"""
    wrote = False
    with conn:
        for name, key in (("web", KEY_WEB_VERSION), ("ytm", KEY_YTM_VERSION)):
            v = versions.get(name)
            if isinstance(v, str) and _VERSION_RE.fullmatch(v):
                _set_maintenance(conn, key, v)
                wrote = True
        if wrote:
            _set_maintenance(conn, KEY_CLIENT_UPDATE_NEEDED, "0")
    return wrote


def decay_terms(conn, now: float) -> int:
    """terms 權重衰減到 now，刪掉 weight < 0.05 而且沒按過喜歡的，超過 MUSIC_TERMS_MAX 從權重最低的未 liked 開始刪。回傳刪除數（不 commit）"""
    for term, w, u in conn.execute("SELECT term, weight, updated FROM terms").fetchall():
        conn.execute("UPDATE terms SET weight=?, updated=? WHERE term=?", (_decayed(w, u, now), max(u, now), term))
    deleted = conn.execute("DELETE FROM terms WHERE weight < ? AND liked=0", (TERM_MIN_WEIGHT,)).rowcount
    over = conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0] - config.MUSIC_TERMS_MAX
    if over > 0:
        victims = conn.execute("SELECT term FROM terms WHERE liked=0 ORDER BY weight, updated, term LIMIT ?", (over,)).fetchall()
        conn.executemany("DELETE FROM terms WHERE term=?", victims)
        deleted += len(victims)
    return deleted


def maintain(conn: sqlite3.Connection, now: float) -> int:
    """定時整理的資料庫部分：terms 衰減與刪除、記錄 last_run。events 完全不動（永久保存）。回傳刪掉的 terms 數。
    clientVersion 的網路部分在背景執行緒，結果由呼叫端用 store_client_versions 寫回"""
    with conn:
        deleted = decay_terms(conn, now)
        _set_maintenance(conn, KEY_LAST_RUN, repr(now))
    return deleted


class MaintenanceSchedule:
    """純邏輯的排程（時鐘由呼叫端注入）：剛建立時 due() 一定是 True（啟動時跑一次）；mark(now) 之後要滿 interval 才又 due。
    排進指令佇列由 assistant 做；這裡不開執行緒、不計時"""

    def __init__(self, interval: float = config.MUSIC_MAINT_INTERVAL_S):
        self.interval = interval
        self.last: Optional[float] = None

    def due(self, now: float) -> bool:
        return self.last is None or now - self.last >= self.interval

    def mark(self, now: float) -> None:
        self.last = now
