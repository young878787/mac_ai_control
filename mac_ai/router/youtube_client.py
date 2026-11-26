"""YouTube 搜尋（點歌用）：InnerTube（一般搜尋、YouTube Music 搜尋，兩邊並行），都沒有可播的就開搜尋頁。不用金鑰、不扣配額。
InnerTube 請求只帶搜尋字、client 名稱與版本、hl／gl，不帶 cookie、金鑰或使用者資訊。
錯誤只回分類（http_500、timeout…），例外訊息、回傳值與紀錄一律不帶網址或回應內容（ADR 0019）。
本模組只做網路與解析：不碰資料庫（搜尋在執行緒裡跑）、不執行子程序（開啟分頁是 actions 的事）；videoId 是不可信輸入，一律先過 valid_video_id。"""
import contextlib
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from mac_ai import config
from mac_ai.router.pinyin import pinyin_units

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Version/18.0 Safari/605.1.15")
_WEB_PARAMS = "EgIQAQ%3D%3D"  # 一般搜尋只要「影片」（P0 實測用的值）
_WEB_ORIGIN, _MUSIC_ORIGIN = "https://www.youtube.com", "https://music.youtube.com"
_MAX_BODY = 4_000_000  # 一般搜尋回應約 300 KB；上限只是防呆
_HOME_READ = 600_000  # 首頁約 0.5～1 MB，INNERTUBE_CLIENT_VERSION 在前 100 KB 內
WEB_RESULTS = 5  # 一般搜尋取前幾筆
MAX_CANDIDATES = 6

_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_VERSION_RE = re.compile(r"[0-9]\.[0-9]{8}\.[0-9]{2}\.[0-9]{2}")
_VERSION_FIND_RE = re.compile(r'INNERTUBE_CLIENT_VERSION\\?"\s*:\s*\\?"([^"\\]{0,60})\\?"')  # 值要有頭有尾（含跳脫過的 JSON），中間不能有引號或反斜線


def valid_video_id(s: Any) -> bool:
    """YouTube videoId 的格式（11 個字元）。fullmatch：結尾的換行也不放行"""
    return isinstance(s, str) and _VIDEO_ID_RE.fullmatch(s) is not None


def valid_client_version(s: Any) -> bool:
    return isinstance(s, str) and _VERSION_RE.fullmatch(s) is not None


def search_url(query: str) -> str:
    """退回用：YouTube 搜尋結果頁"""
    return "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query})


# ---------- 資料類別 ----------
@dataclass
class Video:
    video_id: str
    title: str
    channel: str = ""
    channel_id: str = ""


@dataclass
class MusicHit:
    """YouTube Music 搜尋的第一個結果。kind：song／artist／album／playlist／video／other；artist 與非影片項目的 video_id 是 None"""
    kind: str
    video_id: Optional[str]
    title: str
    artists: List[str] = field(default_factory=list)  # 只收 pageType＝MUSIC_PAGE_TYPE_ARTIST 的 run（不用「•」切字串）
    top_song_id: Optional[str] = None  # 只有 artist：歌手卡片底下第一首可播的歌（歌手本人的熱門歌曲）
    top_song_title: str = ""


@dataclass
class Pick:
    """要播的那一支。source：mv／ytm／web。ytm 時 title／channel 是 YouTube Music 的歌名與歌手（「、」連接）。
    candidates：videoId 清單，選中的排第一，後面接一般搜尋前 5 筆其餘的（去重），最多 6 筆（「換一首」用）"""
    video_id: str
    title: str
    channel: str
    source: str
    candidates: List[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """search() 的結果。pick 是 None 時 source＝page，呼叫端開 url 並回 ⚠️。
    errors：失敗的來源（web／ytm）→ 分類字串（給事件記錄）；stale：回 4xx、需要更新 clientVersion 的端點（web／ytm）"""
    pick: Optional[Pick]
    source: str
    errors: Dict[str, str] = field(default_factory=dict)
    stale: List[str] = field(default_factory=list)
    hit: Optional[MusicHit] = None  # YouTube Music 的第一筆（沒查到是 None）
    accepted: bool = False  # 接受規則的結果
    web: List[Video] = field(default_factory=list)
    url: str = ""  # source＝page 時的搜尋頁
    elapsed_s: float = 0.0


class YtError(Exception):
    """搜尋失敗。訊息就是分類（kind），不含網址或回應"""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def _classify(e: BaseException) -> str:
    if isinstance(e, YtError):
        return e.kind
    if isinstance(e, urllib.error.HTTPError):
        return f"http_{e.code}"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(e, urllib.error.URLError):
        return "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "network"
    if isinstance(e, (ValueError, UnicodeError)):  # JSONDecodeError、UnicodeDecodeError 都是 ValueError
        return "bad_json"
    if isinstance(e, OSError) or e.__class__.__module__ == "http.client":
        return "network"
    return "bad_format"


# ---------- 請求 ----------
def _read(req: urllib.request.Request, timeout: float, limit: int) -> bytes:
    # 呼叫當下才取 urllib.request.urlopen（離線測試會把它換成假的）
    with contextlib.closing(urllib.request.urlopen(req, timeout=timeout)) as resp:
        return resp.read(limit)


def _innertube(url: str, origin: str, client: str, version: str, query: str, params: Optional[str], timeout: float) -> Any:
    body: Dict[str, Any] = {"context": {"client": {"clientName": client, "clientVersion": version, "hl": "zh-TW", "gl": "TW"}},
                            "query": query}
    if params:
        body["params"] = params
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"User-Agent": UA, "Content-Type": "application/json", "Origin": origin})
    kind = None
    try:
        data = json.loads(_read(req, timeout, _MAX_BODY))
    except Exception as e:  # noqa: BLE001  只留分類，不讓網址或回應進例外
        kind = _classify(e)
    if kind:
        raise YtError(kind)
    if not isinstance(data, dict):
        raise YtError("bad_format")
    return data


# ---------- 解析 ----------
_NO_DESCEND = {"adSlotRenderer", "promotedSparklesWebRenderer", "searchPyvRenderer", "reelShelfRenderer", "shortsLockupViewModel",
               "playlistRenderer", "radioRenderer", "channelRenderer", "lockupViewModel"}  # 廣告、Shorts、播放清單、頻道：裡面的東西不算結果


def _walk(o: Any, key: str, skip: frozenset = frozenset(_NO_DESCEND)) -> Iterator[Any]:
    """依文件順序找出所有名為 key 的值；不進入 skip 裡的區塊（預設：廣告、Shorts、播放清單、頻道）"""
    if isinstance(o, dict):
        if key in o:
            yield o[key]
        for k, v in o.items():
            if k not in skip:
                yield from _walk(v, key, skip)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, key, skip)


def _runs_text(x: Any) -> str:
    if not isinstance(x, dict):
        return ""
    if isinstance(x.get("simpleText"), str):
        return x["simpleText"]
    return "".join(r.get("text", "") for r in x.get("runs") or [] if isinstance(r, dict) and isinstance(r.get("text"), str))


def _is_short_or_ad(v: dict) -> bool:
    nav = v.get("navigationEndpoint") or {}
    url = ((nav.get("commandMetadata") or {}).get("webCommandMetadata") or {}).get("url") or ""
    if "reelWatchEndpoint" in nav or str(url).startswith("/shorts/"):
        return True
    for b in v.get("badges") or []:
        style = ((b or {}).get("metadataBadgeRenderer") or {}).get("style") or ""
        if style.endswith("_AD"):
            return True
    return False


def parse_web(data: Any) -> List[Video]:
    """一般搜尋：前 WEB_RESULTS 筆有效的 videoRenderer（Shorts、廣告、播放清單、頻道不算；videoId 不合法的丟棄換下一筆）"""
    out: List[Video] = []
    for v in _walk(data, "videoRenderer"):
        if not isinstance(v, dict) or _is_short_or_ad(v) or not valid_video_id(v.get("videoId")):
            continue
        title = _runs_text(v.get("title"))
        if not title:
            continue
        owner = (v.get("ownerText") or {}).get("runs") or [{}]
        channel_id = (((owner[0] or {}).get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("browseId") or ""
        out.append(Video(v["videoId"], title, _runs_text(v.get("ownerText")), channel_id if isinstance(channel_id, str) else ""))
        if len(out) >= WEB_RESULTS:
            break
    return out


_MUSIC_SKIP = frozenset(_NO_DESCEND | {"musicCardShelfRenderer"})
_KINDS = {"歌曲": "song", "song": "song", "songs": "song", "影片": "video", "video": "video", "videos": "video",
          "藝人": "artist", "歌手": "artist", "artist": "artist", "專輯": "album", "單曲": "album", "album": "album",
          "single": "album", "ep": "album", "播放清單": "playlist", "playlist": "playlist"}
_ARTIST_PAGE = "MUSIC_PAGE_TYPE_ARTIST"


def _page_type(run: Any) -> str:
    nav = (run or {}).get("navigationEndpoint") or {}
    cfg = ((nav.get("browseEndpoint") or {}).get("browseEndpointContextSupportedConfigs") or {}).get("browseEndpointContextMusicConfig") or {}
    return cfg.get("pageType") or ""


def _music_kind(label: str, nav: Any) -> str:
    kind = _KINDS.get(label.strip().lower())
    if kind:
        return kind
    if _page_type({"navigationEndpoint": nav}) == _ARTIST_PAGE:  # 標籤看不懂時（語言不同）退回用項目本身的 pageType
        return "artist"
    return "other"


def _parse_music_item(it: dict) -> Tuple[MusicHit, bool]:
    """一個 musicResponsiveListItemRenderer → (MusicHit, videoId 是否可播)。videoId 不合法的回 (hit, False)：呼叫端丟棄"""
    cols = [(c or {}).get("musicResponsiveListItemFlexColumnRenderer") or {} for c in it.get("flexColumns") or []]
    title_runs = ((cols[0].get("text") or {}).get("runs") or []) if cols else []
    sub_runs = ((cols[1].get("text") or {}).get("runs") or []) if len(cols) > 1 else []
    label = (sub_runs[0] or {}).get("text", "") if sub_runs else ""
    vid = (it.get("playlistItemData") or {}).get("videoId")
    if vid is None and title_runs:
        vid = (((title_runs[0] or {}).get("navigationEndpoint") or {}).get("watchEndpoint") or {}).get("videoId")
    artists = [r["text"].strip() for r in sub_runs if isinstance(r, dict) and _page_type(r) == _ARTIST_PAGE and isinstance(r.get("text"), str)]
    hit = MusicHit(_music_kind(label, it.get("navigationEndpoint")), vid if valid_video_id(vid) else None, _runs_text({"runs": title_runs}),
                   [a for a in artists if a])
    return hit, vid is not None and valid_video_id(vid)


def parse_music(data: Any) -> Optional[MusicHit]:
    """YouTube Music 搜尋的第一個結果＝清單裡第一個有（合法）videoId 的項目（歌曲或影片）；路過的歌手、專輯、播放清單項目沒有 videoId，略過。
    例外：頂端卡片是歌手、而且清單開頭不是歌曲或影片（例如只說「周杰倫」：清單從播放清單、專輯開始）→ 回 kind＝artist，表示使用者說的是歌手；
    卡片底下第一首可播的歌放進 top_song_id／top_song_title（只說歌手時播它，使用者 2026-11-21）。
    沒有結果回 None"""
    # 頂端卡片自己帶一串項目（歌手卡片底下是該歌手的熱門歌曲），那不是搜尋結果清單，不算
    items = [it for it in _walk(data, "musicResponsiveListItemRenderer", _MUSIC_SKIP) if isinstance(it, dict)]
    parsed = [_parse_music_item(it) for it in items]
    card = next((c for c in _walk(data, "musicCardShelfRenderer") if isinstance(c, dict)), None)
    if card is not None and (not parsed or parsed[0][0].kind not in ("song", "video")):
        sub = ((card.get("subtitle") or {}).get("runs") or [{}])
        label = (sub[0] or {}).get("text", "") if sub else ""
        if _KINDS.get(label.strip().lower()) == "artist":
            name = _runs_text(card.get("title"))
            top = next((h for h, ok in (_parse_music_item(it) for it in _walk(card, "musicResponsiveListItemRenderer") if isinstance(it, dict))
                        if ok and h.kind == "song" and h.title), None)
            return MusicHit("artist", None, name, [name] if name else [], top.video_id if top else None, top.title if top else "")
    for hit, playable in parsed:
        if playable:
            return hit
    return None


# ---------- 各端點 ----------
def search_web(query: str, client_version: str, timeout: float) -> List[Video]:
    """YouTube 一般搜尋（InnerTube，client WEB）。失敗丟 YtError；0 筆回 []"""
    return parse_web(_innertube(config.YT_WEB_SEARCH_URL, _WEB_ORIGIN, "WEB", client_version, query, _WEB_PARAMS, timeout))


def search_music(query: str, client_version: str, timeout: float) -> Optional[MusicHit]:
    """YouTube Music 搜尋（InnerTube，client WEB_REMIX）。失敗丟 YtError；沒有結果回 None"""
    return parse_music(_innertube(config.YT_MUSIC_SEARCH_URL, _MUSIC_ORIGIN, "WEB_REMIX", client_version, query, None, timeout))


# ---------- 接受規則與挑 MV ----------
_MARK_RE = re.compile(r"(?<![a-z0-9])(?:karaoke|instrumental|cover|ktv)(?![a-z0-9])|伴奏|卡拉ok|翻唱")  # 伴奏類
_LYRIC_RE = re.compile(r"歌詞|歌词|(?<![a-z0-9])lyrics?(?![a-z0-9])")  # 挑 MV 的第 ② 步另外排除
_BRACKET_RE = re.compile(r"\([^()]*\)|（[^（）]*）|【[^【】]*】|\[[^\[\]]*\]")
Units = List[Tuple[str, str]]


def _has_mark(text: str, lyric: bool = False) -> bool:
    low = text.lower()
    return bool(_MARK_RE.search(low) or (lyric and _LYRIC_RE.search(low)))


def _core_title(title: str) -> str:
    """歌名去掉括號內容（() （） 【】 []）"""
    prev = None
    while prev != title:
        prev, title = title, _BRACKET_RE.sub(" ", title)
    return title


def _find(hay: Units, needle: Units) -> int:
    """needle 在 hay 裡連續出現的位置（先比字面，再比拼音音節）；沒有回 -1"""
    if not needle:
        return -1
    for idx in (0, 1):
        h, n = [u[idx] for u in hay], [u[idx] for u in needle]
        for i in range(len(h) - len(n) + 1):
            if h[i:i + len(n)] == n:
                return i
    return -1


def _contains(hay: Units, needle: Units) -> bool:
    return _find(hay, needle) >= 0


def _title_span(q: Units, t: Units) -> Tuple[int, int]:
    """歌名 t 在 query q 裡的位置（頭、尾），先字面再拼音；沒有 (−1, −1)"""
    i = _find(q, t)
    return (i, i + len(t)) if i >= 0 else (-1, -1)


def accept_music(query: str, hit: Optional[MusicHit]) -> bool:
    """§4.2 第 4 點：要同時成立 ① 類型是歌曲 ② 歌名與歌手沒有伴奏類標記 ③ 歌名（去括號）是 query 的連續子字串（字面或拼音）
    ④ query 去掉歌名、「的」與空白後如果還有字，那些字要（字面或拼音）對到某位歌手"""
    if hit is None or hit.kind != "song" or not valid_video_id(hit.video_id):
        return False
    if _has_mark(hit.title) or any(_has_mark(a) for a in hit.artists):
        return False
    q, t = pinyin_units(query), pinyin_units(_core_title(hit.title))
    a, b = _title_span(q, t)
    if a < 0:
        return False
    rest = [u for u in q[:a] + q[b:] if u[0] != "的"]
    return not rest or any(_contains(pinyin_units(artist), rest) for artist in hit.artists)


def _candidates(selected: str, web_ids: Sequence[str]) -> List[str]:
    out = [selected]
    for i in web_ids:
        if i not in out:
            out.append(i)
    return out[:MAX_CANDIDATES]


def pick(query: str, hit: Optional[MusicHit], web_videos: Sequence[Video]) -> Optional[Pick]:
    """§4.2 第 5 點。接受了 YouTube Music：在一般搜尋前 5 筆依序找 ① 頻道名稱對到歌手、標題含歌名 ② 標題同時含歌名與歌手、且不含伴奏類與歌詞標記
    （source＝mv）；都沒有播 YouTube Music 的 ID（ytm）。只說歌手（kind＝artist）而且卡片有歌：播卡片第一首（ytm，官方的歌，不播一般搜尋第一筆的串燒、
    團員個人作品）。其餘：播一般搜尋第一筆（web）。沒有任何可播的回 None（呼叫端開搜尋頁）"""
    vids = [v for v in web_videos if valid_video_id(v.video_id)][:WEB_RESULTS]
    web_ids = [v.video_id for v in vids]
    if hit is not None and accept_music(query, hit):
        title = pinyin_units(_core_title(hit.title))
        artists = [pinyin_units(a) for a in hit.artists]
        for v in vids:
            if _contains(pinyin_units(v.title), title) and any(_contains(pinyin_units(v.channel), a) for a in artists if a):
                return Pick(v.video_id, v.title, v.channel, "mv", _candidates(v.video_id, web_ids))
        for v in vids:
            vt = pinyin_units(v.title)
            if _contains(vt, title) and any(_contains(vt, a) for a in artists if a) and not _has_mark(v.title, lyric=True):
                return Pick(v.video_id, v.title, v.channel, "mv", _candidates(v.video_id, web_ids))
        return Pick(hit.video_id, hit.title, "、".join(hit.artists), "ytm", _candidates(hit.video_id, web_ids))
    if hit is not None and hit.kind == "artist" and valid_video_id(hit.top_song_id) and hit.top_song_title:
        return Pick(hit.top_song_id, hit.top_song_title, "、".join(hit.artists), "ytm", _candidates(hit.top_song_id, web_ids))
    if vids:
        return Pick(vids[0].video_id, vids[0].title, vids[0].channel, "web", _candidates(vids[0].video_id, web_ids))
    return None


# ---------- 並行與截止時間 ----------
class _Job:
    """在 daemon 執行緒裡跑 fn。呼叫端用 wait(deadline) 等到截止時間為止；卡住的執行緒不會被等，它之後才結束也不會影響結果"""

    def __init__(self, fn: Callable[[], Any]):
        self.value: Any = None
        self.error: Optional[str] = None
        self._done = threading.Event()
        self._fn = fn
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.value = self._fn()
        except Exception as e:  # noqa: BLE001
            self.error = _classify(e)
        finally:
            self._done.set()

    def wait(self, deadline: float) -> bool:
        return self._done.wait(max(0.0, deadline - time.monotonic()))


def _version(versions: Optional[Dict[str, str]], name: str, default: str) -> str:
    v = (versions or {}).get(name)
    return v if valid_client_version(v) else default


def search(query: str, versions: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> SearchResult:
    """§4.2 第 3～5 點的整條搜尋。versions：{"web": …, "ytm": …}（呼叫端從 maintenance 表讀；沒有的用 config 預設值）。
    InnerTube 兩邊並行、共用同一個截止時間（YT_TIMEOUT_S）；兩邊都沒給出可播的影片就回 source＝page（呼叫端開搜尋頁）。
    InnerTube 回 4xx 只標進 stale、不當場重試（每個端點只送一次請求）"""
    t0 = time.monotonic()
    timeout = config.YT_TIMEOUT_S if timeout is None else timeout
    cv_web = _version(versions, "web", config.YT_WEB_CLIENT_VERSION)
    cv_ytm = _version(versions, "ytm", config.YT_MUSIC_CLIENT_VERSION)
    errors: Dict[str, str] = {}
    jobs = {"web": _Job(lambda: search_web(query, cv_web, timeout)), "ytm": _Job(lambda: search_music(query, cv_ytm, timeout))}
    deadline = t0 + timeout
    for name, job in jobs.items():
        if not job.wait(deadline):
            errors[name] = "timeout"
        elif job.error:
            errors[name] = job.error
    web: List[Video] = jobs["web"].value if "web" not in errors and jobs["web"].value else []
    hit: Optional[MusicHit] = jobs["ytm"].value if "ytm" not in errors else None
    if "web" not in errors and not web:
        errors["web"] = "empty"
    if "ytm" not in errors and hit is None:
        errors["ytm"] = "empty"
    stale = [n for n in ("web", "ytm") if errors.get(n, "").startswith("http_4")]
    chosen = pick(query, hit, web)
    result = SearchResult(chosen, chosen.source if chosen else "page", errors, stale, hit, accept_music(query, hit), web)
    if chosen is None:
        result.url = search_url(query)
    result.elapsed_s = time.monotonic() - t0
    return result


# ---------- clientVersion ----------
def parse_client_version(text: str) -> Optional[str]:
    """從首頁文字讀 INNERTUBE_CLIENT_VERSION；格式不符（空、含引號、太長…）回 None"""
    m = _VERSION_FIND_RE.search(text)
    return m.group(1) if m and valid_client_version(m.group(1)) else None


def _fetch_home(url: str, timeout: float) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW"})
    return parse_client_version(_read(req, timeout, _HOME_READ).decode("utf-8", "replace"))


def fetch_client_versions(timeout: float = 2.0) -> Dict[str, str]:
    """抓兩個首頁讀新的 clientVersion（定時整理用，在背景執行緒呼叫）。回 {"web": …, "ytm": …}，讀不到或格式不符的不放進去。
    兩個首頁並行、共用截止時間，所以最久約 timeout"""
    jobs = {"web": _Job(lambda: _fetch_home(config.YT_WEB_HOME_URL, timeout)),
            "ytm": _Job(lambda: _fetch_home(config.YT_MUSIC_HOME_URL, timeout))}
    deadline = time.monotonic() + timeout
    out: Dict[str, str] = {}
    for name, job in jobs.items():
        if job.wait(deadline) and not job.error and job.value:
            out[name] = job.value
    return out
