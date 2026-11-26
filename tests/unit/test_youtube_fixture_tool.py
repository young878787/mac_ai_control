"""scripts/capture_youtube_fixtures.py：縮小規則與 tests/fixtures/youtube/ 一致（離線；fetch 的真實網路不在自動測試裡，用法見腳本開頭）。
最有力的檢查是『冪等』：已提交的 fixture 再縮一次要完全不變，代表腳本的規則就是當初縮 fixture 的規則"""
import importlib.util
import json
import urllib.request

import pytest

from mac_ai import config
from mac_ai.router import youtube_client as yt
from support.youtube import FIX, QUERY

_spec = importlib.util.spec_from_file_location("capture_youtube_fixtures", config.ROOT / "scripts" / "capture_youtube_fixtures.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

FIXTURES = sorted(p for p in FIX.glob("*.json") if p.name != "index.json")


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_shrinking_a_committed_fixture_changes_nothing(path):
    kind = path.name.split("_", 1)[0]
    text = path.read_text(encoding="utf-8")
    assert tool.dump_fixture(tool.SHRINKERS[kind](json.loads(text))) == text


def _keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _keys(v)
    elif isinstance(o, list):
        for x in o:
            yield from _keys(x)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_no_fixture_contains_a_dropped_field(path):
    """冪等只抓得到『規則變強』；這個抓『規則變弱』：該刪的欄位（追蹤碼、縮圖…）不能出現在已提交的 fixture 裡"""
    assert not set(_keys(json.loads(path.read_text(encoding="utf-8")))) & tool.DROP


def test_fixture_set_matches_slug_tables():
    index = json.loads((FIX / "index.json").read_text(encoding="utf-8"))
    assert {slug: m["query"] for slug, m in index.items()} == QUERY == {slug: q for q, slug in tool.SLUGS.items()}
    expected = {f"{k}_{slug}.json" for slug in tool.SLUGS.values() for k in tool.kinds_of(slug)}
    assert {p.name for p in FIXTURES} == expected
    assert {slug for slug, m in index.items() if m.get("web")} == set(tool.WEB_SLUGS) <= set(tool.SLUGS.values())
    assert {p.name for p in FIX.iterdir()} == expected | {"index.json", "home_web.txt", "home_ytm.txt"}


def test_strip_drops_tracking_and_thumbnails_but_keeps_parse_paths():
    raw = {"videoRenderer": {"videoId": "AAAAAAAAAA1", "trackingParams": "x", "thumbnail": {"thumbnails": [1]},
                             "title": {"runs": [{"text": "晴天", "navigationEndpoint": {"clickTrackingParams": "y", "watchEndpoint": {"videoId": "AAAAAAAAAA1"}}}]},
                             "ownerText": {"runs": [{"text": "頻道"}]}, "menu": {"a": 1}}}
    assert tool.strip(raw) == {"videoRenderer": {"videoId": "AAAAAAAAAA1",
                                                 "title": {"runs": [{"text": "晴天", "navigationEndpoint": {"watchEndpoint": {"videoId": "AAAAAAAAAA1"}}}]},
                                                 "ownerText": {"runs": [{"text": "頻道"}]}}}
    # 解析要用的欄位不能被規則吃掉
    needed = {"videoId", "title", "ownerText", "runs", "text", "navigationEndpoint", "browseEndpoint", "browseId", "watchEndpoint", "playlistItemData",
              "flexColumns", "flexColumnItems", "musicResponsiveListItemFlexColumnRenderer", "pageType", "browseEndpointContextSupportedConfigs",
              "browseEndpointContextMusicConfig", "contents", "subtitle", "onTap", "videoRenderer", "musicResponsiveListItemRenderer",
              "musicCardShelfRenderer", "itemSectionRenderer"}
    assert not needed & tool.DROP


def test_home_snippets_keep_the_client_version():
    for name in ("home_web", "home_ytm"):
        assert yt.parse_client_version((FIX / f"{name}.txt").read_text(encoding="utf-8"))
    html = "x" * 100 + 'INNERTUBE_CLIENT_NAME":"WEB","INNERTUBE_CLIENT_VERSION":"2.20261119.07.00","INNERTUBE_CONTEXT' + "y" * 100
    assert yt.parse_client_version(tool.home_snippet(html)) == "2.20261119.07.00"


def test_fetch_rejects_unknown_query_without_touching_the_network(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("不該連網")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert tool.fetch(tmp_path / "raw", ["不存在的歌"]) == 2
    assert not any((tmp_path / "raw").iterdir())
