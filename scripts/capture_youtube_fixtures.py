"""重新抓取並縮小 tests/fixtures/youtube/ 的真實 InnerTube 回應（點歌用；只打 InnerTube，不用金鑰、不扣配額）。

用法（YouTube 改版、或要補新的搜尋字時）：
  python scripts/capture_youtube_fixtures.py fetch RAW_DIR [搜尋字 ...]   抓原始回應存進 RAW_DIR；不給搜尋字＝SLUGS 全部
  python scripts/capture_youtube_fixtures.py shrink RAW_DIR [--out DIR]   把 RAW_DIR 的原始回應縮小成 fixture（預設 tests/fixtures/youtube/）

RAW_DIR 放在 repo 外面（例如 /tmp 底下）：原始回應每個約 300 KB，不入版控。
請求格式與正式程式相同（直接呼叫 youtube_client 的 _innertube），所以 fixture 反映的就是產品實際會收到的回應。
抓完要重跑 pytest（尤其是 tests/runtime/test_youtube_search.py 的 REAL 表與 tests/unit/test_youtube_client.py）：
真實內容變了，某些決策（挑到哪支影片）可能跟著變，要先看過再改測試裡的預期值。
新增搜尋字：加進 SLUGS（需要一般搜尋版再加進 WEB_SLUGS），然後 fetch、shrink。"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mac_ai import config  # noqa: E402
from mac_ai.router import youtube_client as yt  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "youtube"
FETCH_TIMEOUT_S = 8.0
FETCH_PAUSE_S = 0.35  # 兩次請求之間的間隔，不要連珠砲

# 搜尋字 → slug（fixture 檔名 web_{slug}.json／ytm_{slug}.json；tests/support/youtube.py 的 QUERY 也是這張表）
SLUGS: Dict[str, str] = {
    "晴天": "qingtian", "周杰倫的晴天": "jay_qingtian", "晴天 周杰倫": "qingtian_jay", "稻香": "daoxiang", "Shape of You": "shape_of_you",
    "告白氣球": "gaobai", "到香": "typo_daoxiang", "告白汽球": "typo_gaobai", "周杰論的晴天": "typo_jay_qingtian", "周杰倫": "jay_only",
    "五月天的晴天": "mayday_qingtian", "適合下雨天": "rainy", "讀書用的輕音樂": "study", "睡眠音樂": "sleep",
    "不想長大 S.H.E": "buxiangzhangda", "日不落 蔡依林": "ribuluo", "十年 陳奕迅": "shinian", "江南 林俊傑": "jiangnan", "聽海 張惠妹": "tinghai",
    "七里香": "qilixiang", "夜曲": "yequ", "Coldplay Yellow": "yellow", "Despacito": "despacito", "Uptown Funk": "uptown_funk",
    "See You Again": "see_you_again", "Bohemian Rhapsody": "bohemian", "Take Me Home Country Roads": "country_roads", "Sweet Caroline": "sweet_caroline",
    "Hotel California": "hotel_california", "Let It Be": "let_it_be", "Yesterday": "yesterday", "月亮代表我的心": "moon", "死了都要愛": "siledouyao",
    "Total Eclipse of the Heart": "eclipse", "Rolling in the Deep karaoke": "rolling_karaoke",
}
# 除了 YouTube Music（ytm）還需要一般搜尋（web）版的 slug：挑 MV 與完整搜尋流程的測試用到的
WEB_SLUGS = frozenset({
    "qingtian", "jay_qingtian", "qingtian_jay", "daoxiang", "shape_of_you", "gaobai", "typo_daoxiang", "typo_gaobai", "typo_jay_qingtian",
    "jay_only", "mayday_qingtian", "rainy", "study", "sleep", "shinian", "despacito", "moon",
})
HOMES = {"home_web": config.YT_WEB_HOME_URL, "home_ytm": config.YT_MUSIC_HOME_URL}

# ---------- 縮小規則 ----------
# 解析需要的路徑（videoRenderer 的 videoId／title／ownerText／browseId、musicResponsiveListItemRenderer 的 playlistItemData／flexColumns、
# navigationEndpoint…browseEndpointContextMusicConfig.pageType）都不在這張表裡，原樣保留
DROP = frozenset({
    "trackingParams", "clickTrackingParams", "thumbnail", "thumbnails", "thumbnailOverlays", "thumbnailOverlay", "accessibility", "accessibilityData",
    "accessibilityPlayData", "accessibilityPauseData", "menu", "badges", "ownerBadges", "richThumbnail", "inlinePlaybackEndpoint", "descriptionSnippet",
    "detailedMetadataSnippets", "expandableMetadata", "channelThumbnailSupportedRenderers", "avatar", "searchVideoResultEntityKey", "loggingContext",
    "commandMetadata", "watchEndpointSupportedOnesieConfig", "params", "playerParams", "overlay", "buttons", "endIcon", "viewCountText", "shortViewCountText",
    "publishedTimeText", "lengthText", "shortBylineText", "longBylineText", "playlistId", "index", "hack", "displayPriority", "flexColumnDisplayStyle",
    "itemHeight", "videoCountText", "videoCountShortText", "subscriberCountText", "subtitleBadges", "headerButton", "movingThumbnail",
    "rendererContext", "style", "contentPosition", "icon", "bylineText", "sideAccessibility", "additionalMetadatas", "headerStyle", "entityKey",
    "contentImage", "subscribeButton", "subscriptionButton", "sources", "enableDisplayCompactDismissButton",
})
WEB_ITEMS_KEPT = 12  # 一般搜尋：第一個 itemSection 的前幾項
YTM_SECTIONS_KEPT = 10  # YouTube Music：頂端卡片＋前幾個 itemSection


def strip(o: Any) -> Any:
    """遞迴拿掉 DROP 裡的欄位（追蹤碼、縮圖、無障礙、選單、按鈕…）"""
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k not in DROP}
    if isinstance(o, list):
        return [strip(x) for x in o]
    return o


def shrink_web(d: dict) -> dict:
    """一般搜尋回應：只留第一個 itemSection（結果主體）的前 WEB_ITEMS_KEPT 項"""
    sections = d["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"]
    out: List[dict] = []
    for s in sections:
        if "itemSectionRenderer" in s:
            out.append({"itemSectionRenderer": {"contents": [strip(i) for i in s["itemSectionRenderer"]["contents"]][:WEB_ITEMS_KEPT]}})
        break
    return {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": out}}}}}


def shrink_ytm(d: dict) -> dict:
    """YouTube Music 回應：頂端卡片＋前 YTM_SECTIONS_KEPT 個 itemSection"""
    sections = d["contents"]["tabbedSearchResultsRenderer"]["tabs"][0]["tabRenderer"]["content"]["sectionListRenderer"]["contents"]
    out: List[dict] = []
    for s in sections:
        if "musicCardShelfRenderer" in s:
            out.append(strip(s))
        elif len([x for x in out if "itemSectionRenderer" in x]) < YTM_SECTIONS_KEPT:
            out.append(strip(s))
    return {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {"contents": out}}}}]}}}


SHRINKERS = {"web": shrink_web, "ytm": shrink_ytm}


def dump_fixture(small: dict) -> str:
    return json.dumps(small, ensure_ascii=False, separators=(",", ":"))


def home_snippet(html: str) -> str:
    """首頁裡含 INNERTUBE_CLIENT_VERSION 的一小段（前 30、後 60 字元）"""
    i = html.index("INNERTUBE_CLIENT_VERSION")
    return html[max(0, i - 30): i + 60]


def kinds_of(slug: str) -> List[str]:
    return ["web", "ytm"] if slug in WEB_SLUGS else ["ytm"]


# ---------- fetch ----------
def fetch(raw_dir: Path, queries: List[str]) -> int:
    raw_dir.mkdir(parents=True, exist_ok=True)
    unknown = [q for q in queries if q not in SLUGS]
    if unknown:
        print(f"不在 SLUGS 裡：{unknown}（先加進 SLUGS）", file=sys.stderr)
        return 2
    n = 0
    for q in queries or list(SLUGS):
        for kind in kinds_of(SLUGS[q]):
            path = raw_dir / f"{kind}_{SLUGS[q]}.json"
            if path.exists():
                continue
            if kind == "web":
                data = yt._innertube(config.YT_WEB_SEARCH_URL, yt._WEB_ORIGIN, "WEB", config.YT_WEB_CLIENT_VERSION, q, yt._WEB_PARAMS, FETCH_TIMEOUT_S)
            else:
                data = yt._innertube(config.YT_MUSIC_SEARCH_URL, yt._MUSIC_ORIGIN, "WEB_REMIX", config.YT_MUSIC_CLIENT_VERSION, q, None, FETCH_TIMEOUT_S)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            n += 1
            print(kind, q, path.stat().st_size, flush=True)
            time.sleep(FETCH_PAUSE_S)
    for name, url in HOMES.items():
        path = raw_dir / f"{name}.html"
        if not path.exists():
            req = urllib.request.Request(url, headers={"User-Agent": yt.UA, "Accept-Language": "zh-TW"})
            path.write_bytes(yt._read(req, FETCH_TIMEOUT_S, yt._HOME_READ))
            n += 1
            print(name, path.stat().st_size, flush=True)
            time.sleep(FETCH_PAUSE_S)
    print("requests", n)
    return 0


# ---------- shrink ----------
def shrink(raw_dir: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    index: Dict[str, dict] = {}
    for query, slug in SLUGS.items():
        for kind in kinds_of(slug):
            src = raw_dir / f"{kind}_{slug}.json"
            if not src.exists():
                continue
            small = SHRINKERS[kind](json.loads(src.read_text(encoding="utf-8")))
            (out_dir / f"{kind}_{slug}.json").write_text(dump_fixture(small), encoding="utf-8")
            index.setdefault(slug, {"query": query})[kind] = True
    for name in HOMES:
        src = raw_dir / f"{name}.html"
        if src.exists():
            (out_dir / f"{name}.txt").write_text(home_snippet(src.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(index)} 組 → {out_dir}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="抓原始回應")
    f.add_argument("raw_dir", type=Path)
    f.add_argument("queries", nargs="*")
    s = sub.add_parser("shrink", help="縮小成 fixture")
    s.add_argument("raw_dir", type=Path)
    s.add_argument("--out", type=Path, default=FIXTURE_DIR)
    args = p.parse_args(argv)
    return fetch(args.raw_dir, args.queries) if args.cmd == "fetch" else shrink(args.raw_dir, args.out)


if __name__ == "__main__":
    sys.exit(main())
