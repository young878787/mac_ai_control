"""前景視窗上下文：句子沒指定對象時，用使用者正在看的 App 補上。
只在執行階段讀取（FastPathMatcher.match 與收音熱路徑不得呼叫）。"""
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from mac_ai.config import CONTEXT_TIMEOUT_S, ROOT
from mac_ai.router.lexicon import APP_KIND, LEXICON, _compact

Runner = Callable[[List[str], Optional[float]], Tuple[bool, str]]

_BUNDLE_PATH_RE = re.compile(r'bundle path="(?P<path>[^"]+\.app)"')
_PID_RE = re.compile(r"\bpid\s*=\s*(\d+)")


@dataclass(frozen=True)
class FrontContext:
    app: str  # .app 的檔名（去掉副檔名），與詞庫的標準名稱同一套
    kind: str  # browser／editor／other
    pid: int = 0  # 行程編號（native 的 AX 操作直接對這個行程下指令）；0＝不知道


def parse_front(info: str) -> Optional[FrontContext]:
    """lsappinfo info 的輸出 → FrontContext。名稱取 bundle 路徑的檔名：lsappinfo 的 name 是程序名
    （實測 VS Code 回報 "Code"），對不上詞庫。本專案目錄底下的 App（桌面角色的 Electron）視為沒有上下文"""
    m = _BUNDLE_PATH_RE.search(info)
    if not m:
        return None
    path = m["path"]
    if os.path.commonpath([os.path.abspath(path), str(ROOT)]) == str(ROOT):
        return None
    app = os.path.basename(path)[:-4]
    pid = _PID_RE.search(info)
    return FrontContext(app, APP_KIND.get(app, "other"), int(pid[1]) if pid else 0)


def read_front(run: Runner) -> Optional[FrontContext]:
    """讀最前面的 App。用 lsappinfo（實測兩次呼叫合計約 15 ms；osascript 問 System Events 要 220 ms 以上）。
    讀不到、逾時一律回傳 None（呼叫端退回預設行為，不猜）"""
    ok, asn = run(["lsappinfo", "front"], CONTEXT_TIMEOUT_S)
    if not ok or not asn.strip():
        return None
    ok, info = run(["lsappinfo", "info", "-only", "bundlepath", "-only", "pid", asn.strip()], CONTEXT_TIMEOUT_S)
    return parse_front(info) if ok else None


# ---------- 網站（站內搜尋）----------
SITES: List[dict] = LEXICON.get("sites", [])
# 只支援這兩個瀏覽器讀網址（與 actions.TAB_BROWSERS 一致）；實測 Safari 約 200 ms
_URL_SCRIPTS = {
    "Safari": 'tell application "Safari" to return URL of current tab of front window',
    "Google Chrome": 'tell application "Google Chrome" to return URL of active tab of front window',
}


def parse_host(url: str) -> str:
    """網址 → 小寫 host（不含埠號）；不是 http(s) 網址回傳空字串。只留 host：路徑與查詢字串是瀏覽紀錄，不往外帶"""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return ""
    return (parts.hostname or "").lower() if parts.scheme in ("http", "https") else ""


def site_for(host: str) -> Optional[dict]:
    """host（含子網域）→ 詞庫 sites 的條目"""
    for site in SITES:
        if any(host == h or host.endswith("." + h) for h in site["hosts"]):
            return site
    return None


def read_host(run: Runner, app: str) -> str:
    """前景瀏覽器目前分頁的 host；不支援的瀏覽器、沒有視窗、逾時（含等待自動化授權）都回傳空字串"""
    script = _URL_SCRIPTS.get(app)
    if script is None:
        return ""
    ok, out = run(["osascript", "-e", script], CONTEXT_TIMEOUT_S)
    return parse_host(out) if ok else ""


# ---------- 常用功能（詞庫 app_shortcuts；執行方式見 local_actions.front_action）----------
SHORTCUTS: Dict[str, dict] = {e["id"]: e for e in LEXICON.get("app_shortcuts", [])}
SHORTCUT_PHRASES: Dict[str, str] = {_compact(p): e["id"] for e in SHORTCUTS.values() for p in e["phrases"]}


def needs_site(entry: dict) -> bool:
    return any("site" in b["when"] for b in entry["bindings"])


def pick_binding(entry: dict, front: Optional[FrontContext], site: Optional[str] = None) -> Optional[dict]:
    """依前景 App 選按鍵（第一個符合的）；沒有符合的回傳 None（這個功能不支援目前的 App）"""
    if front is None:
        return None
    for b in entry["bindings"]:
        when = b["when"]
        if "kind" in when and when["kind"] != front.kind:
            continue
        if "apps" in when and front.app not in when["apps"]:
            continue
        if "site" in when and when["site"] != site:
            continue
        return b
    return None
