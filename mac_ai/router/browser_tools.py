"""瀏覽器工具：開網址或 Google 搜尋、Safari／Chrome 的分頁操作。TAB_BROWSERS 也給讀畫面、點歌用。"""
import urllib.parse
from typing import Optional, Tuple

from mac_ai.router.exec_context import EXTERNAL_TIMEOUT_S, POLL_TIMEOUT_S
from mac_ai.router.proc import _run


def google_search_url(query: str) -> str:
    return f"https://www.google.com/search?q={urllib.parse.quote(query)}"


def web_search_or_open_url(query: str, browser: Optional[str] = None) -> str:
    """使用瀏覽器開啟網址或進行 Google 搜尋；browser 由執行上下文帶入（剛開啟的瀏覽器），未指定則用預設瀏覽器"""
    if query.startswith("http://") or query.startswith("https://"):
        url = query
    else:
        url = google_search_url(query)
    ok, err = _run(["open", "-a", browser, url] if browser else ["open", url], EXTERNAL_TIMEOUT_S)
    return f"✅ 已開啟瀏覽器搜尋：{query}" if ok else f"❌ 開啟瀏覽器失敗（{err}）"


TAB_BROWSERS = ("Safari", "Google Chrome")  # 支援分頁操作的瀏覽器；沒有明確目標時依此順序挑
_FRONT_AND_RUNNING_SCRIPT = (
    "set AppleScript's text item delimiters to \",\"\n"
    'tell application "System Events"\n'
    "set frontName to name of first application process whose frontmost is true\n"
    "set appNames to (name of every application process whose background only is false) as text\n"
    "end tell\n"
    "return frontName & linefeed & appNames"
)
_SAFARI_TAB_STEP = (
    'tell application "Safari"\ntell front window\n{body}\nend tell\nend tell'
)
_CHROME_TAB_STEP = (
    'tell application "Google Chrome"\ntell front window\n{body}\nend tell\nend tell'
)
_TAB_SCRIPTS = {
    "Safari": {
        "new": (
            'tell application "Safari"\nactivate\nif (count of windows) = 0 then\nmake new document\nelse\n'
            "tell front window to set current tab to (make new tab)\nend if\nend tell"
        ),
        "close": 'tell application "Safari" to close current tab of front window',
        "reload": 'tell application "Safari" to set URL of current tab of front window to (URL of current tab of front window)',
        "next": _SAFARI_TAB_STEP.format(body="set n to count of tabs\nset i to index of current tab\nset current tab to tab (i mod n + 1)"),
        "previous": _SAFARI_TAB_STEP.format(body="set n to count of tabs\nset i to index of current tab\nset current tab to tab ((i - 2 + n) mod n + 1)"),
    },
    "Google Chrome": {
        "new": (
            'tell application "Google Chrome"\nactivate\nif (count of windows) = 0 then\nmake new window\nelse\n'
            "tell front window\nmake new tab\nset active tab index to (count of tabs)\nend tell\nend if\nend tell"
        ),
        "close": 'tell application "Google Chrome" to close active tab of front window',
        "reload": 'tell application "Google Chrome" to reload active tab of front window',
        "next": _CHROME_TAB_STEP.format(body="set active tab index to (active tab index mod (count of tabs)) + 1"),
        "previous": _CHROME_TAB_STEP.format(body="set active tab index to ((active tab index - 2 + (count of tabs)) mod (count of tabs)) + 1"),
    },
}
_TAB_DONE_TEXT = {"new": "已開新分頁", "close": "已關閉目前分頁", "reload": "已重新整理", "next": "已切到下一個分頁", "previous": "已切到上一個分頁"}


def _pick_tab_browser(hint: Optional[str]) -> Tuple[Optional[str], str]:
    """決定要操作哪個瀏覽器：hint（這次指令剛開的瀏覽器）→ 最前景的瀏覽器 → 正在執行的瀏覽器。
    回傳 (瀏覽器名稱或 None, 查詢失敗的原因)"""
    if hint in TAB_BROWSERS:
        return hint, ""
    ok, out = _run(["osascript", "-e", _FRONT_AND_RUNNING_SCRIPT], POLL_TIMEOUT_S)
    if not ok:
        return None, out
    lines = out.splitlines()
    front = lines[0].strip() if lines else ""
    if front in TAB_BROWSERS:
        return front, ""
    running = [n.strip() for n in lines[1].split(",")] if len(lines) > 1 else []
    return next((b for b in TAB_BROWSERS if b in running), None), ""


def browser_tab(action: str, browser: Optional[str] = None) -> str:
    """對 Safari／Google Chrome 的分頁操作（new／close／reload／next／previous）；browser 由執行上下文帶入"""
    if action not in _TAB_DONE_TEXT:
        return f"⚠️ 未知的分頁動作：{action}"
    target, err = _pick_tab_browser(browser)
    if target is None:
        return "❌ 找不到可操作的瀏覽器（支援 Safari、Google Chrome，請先開啟）" + (f"（{err}）" if err else "")
    ok, err = _run(["osascript", "-e", _TAB_SCRIPTS[target][action]], EXTERNAL_TIMEOUT_S)
    return f"✅ {target}：{_TAB_DONE_TEXT[action]}" if ok else f"❌ 分頁操作失敗：{target}（{err}）"
