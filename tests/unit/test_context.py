"""前景視窗上下文：lsappinfo／瀏覽器 host 的解析與讀取（不經完整路由）"""
import pytest

import mac_ai.router as r
from mac_ai import config
from mac_ai.router import context
from support.expect import URL, VSCODE
from support.real import REAL_READ_FRONT, REAL_READ_HOST


def _info(path):
    return f'"Code" ASN:0x0-0x1459458: (in front) \n    bundle path="{path}"\n'


def _runner(front_ok=True, info_ok=True, asn="ASN:0x0-0x1459458:", info=_info("/Applications/Safari.app")):
    calls = []

    def run(cmd, timeout=None):
        calls.append((cmd, timeout))
        if cmd[:2] == ["lsappinfo", "front"]:
            return front_ok, asn if front_ok else "逾時"
        return info_ok, info if info_ok else "err"
    return run, calls


# ---------- A1：讀取前景 ----------
@pytest.mark.parametrize("path,app,kind", [
    ("/Applications/Safari.app", "Safari", "browser"),
    ("/System/Volumes/Preboot/Cryptexes/App/System/Applications/Safari.app", "Safari", "browser"),
    ("/Applications/Visual Studio Code.app", VSCODE, "editor"),  # lsappinfo 的 name 是 "Code"，要用 bundle 檔名
    ("/System/Applications/Utilities/Terminal.app", "Terminal", "other"),
    ("/Applications/沒在詞庫.app", "沒在詞庫", "other"),
])
def test_parse_front_uses_bundle_filename(path, app, kind):
    assert context.parse_front(_info(path)) == context.FrontContext(app, kind)


def test_parse_front_ignores_own_desktop_character():
    own = config.ROOT / "desktop" / "node_modules" / "electron" / "dist" / "Electron.app"
    assert context.parse_front(_info(str(own))) is None


@pytest.mark.parametrize("info", ["", '"Code" ASN:0x0-0x1: \n    bundle path=[ NULL ] \n'])
def test_parse_front_without_bundle_path(info):
    assert context.parse_front(info) is None


def test_read_front_calls_lsappinfo_with_timeout():
    run, calls = _runner()
    assert REAL_READ_FRONT(run) == context.FrontContext("Safari", "browser", 0)
    assert calls == [
        (["lsappinfo", "front"], config.CONTEXT_TIMEOUT_S),
        (["lsappinfo", "info", "-only", "bundlepath", "-only", "pid", "ASN:0x0-0x1459458:"], config.CONTEXT_TIMEOUT_S),
    ]


def test_parse_front_reads_pid():
    info = '"Code" ASN:0x0-0x1: (in front) \n    bundle path="/Applications/Safari.app"\n    pid = 90352 !cgsConnection\n'
    assert context.parse_front(info) == context.FrontContext("Safari", "browser", 90352)


@pytest.mark.parametrize("kw", [{"front_ok": False}, {"asn": "  "}, {"info_ok": False}, {"info": ""}])
def test_read_front_failure_is_none(kw):
    run, _ = _runner(**kw)
    assert REAL_READ_FRONT(run) is None


def test_default_front_is_unreadable_in_offline_tests(sh):
    """conftest：沒有用 front fixture 時讀不到前景，而且不會真的跑 lsappinfo"""
    r.process_voice_command("搜尋貓咪")
    assert sh == [["open", URL("貓咪")]]


# ---------- A1（網址）：只留 host ----------
@pytest.mark.parametrize("url,host", [
    ("https://m.facebook.com/x?y=1", "m.facebook.com"),
    ("https://WWW.YouTube.com:443/watch?v=abc", "www.youtube.com"),
    ("http://github.com", "github.com"),
    ("file:///Users/me/a.html", ""), ("favorites://", ""), ("", ""), ("missing value", ""),
])
def test_parse_host(url, host):
    assert context.parse_host(url) == host


@pytest.mark.parametrize("host,site", [
    ("m.facebook.com", "Facebook"), ("www.facebook.com", "Facebook"), ("facebook.com", "Facebook"),
    ("www.youtube.com", "YouTube"), ("github.com", "GitHub"), ("gist.github.com", "GitHub"),
    ("www.google.com", "Google"), ("www.google.com.tw", "Google"),
    ("notfacebook.com", None), ("facebook.com.evil.example", None), ("example.com", None), ("", None),
])
def test_site_for(host, site):
    hit = context.site_for(host)
    assert (hit["name"] if hit else None) == site


def test_read_host_only_for_supported_browsers():
    calls = []

    def run(cmd, timeout=None):
        calls.append((cmd, timeout))
        return True, "https://www.facebook.com/groups/1?ref=x"
    assert REAL_READ_HOST(run, "Safari") == "www.facebook.com"
    assert calls[0][0][0] == "osascript" and calls[0][1] == config.CONTEXT_TIMEOUT_S
    assert REAL_READ_HOST(run, "Arc") == "" and len(calls) == 1  # 不支援的瀏覽器：不問
    assert REAL_READ_HOST(lambda cmd, timeout=None: (False, "逾時"), "Safari") == ""
