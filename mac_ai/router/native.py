"""直接呼叫 macOS 底層 API（ctypes，不經 osascript、不需額外套件）對指定行程的 App 操作：
視窗（關閉／最小化／全螢幕）、按選單項目、按視窗裡的按鈕、送按鍵。

為什麼不用模擬快捷鍵當主要做法：模擬按鍵會先經過輸入法（實測注音把 ⌃` 當成打字，打出「、」）。
輔助使用 API（AX）直接對 App 的元件下指令，不經過鍵盤與輸入法，也不受介面語言以外的因素影響。
所有函式都不拋例外：回傳狀態字串 ok／missing（找不到元件）／disabled（選單項目停用）／error（API 失敗、沒有權限）。"""
import ctypes
import ctypes.util
import re
import time
from collections import deque
from ctypes import POINTER, byref, c_bool, c_char_p, c_float, c_int32, c_long, c_longlong, c_uint16, c_uint32, c_uint64, c_void_p
from typing import Dict, Iterable, Optional, Sequence, Tuple

_UTF8 = 0x08000100
_AX_TIMEOUT_S = 1.0  # 每次 AX 呼叫最多等這麼久（App 沒回應時不卡住指令執行緒）
_SEARCH_BUDGET_S = 1.5  # 找按鈕最多花這麼久
_SEARCH_MAX_NODES = 3000
_MENU_DEPTH = 3
# AXMenuItemCmdModifiers：0＝只有 ⌘；1＝⇧；2＝⌥；4＝⌃；8＝沒有 ⌘
_MENU_MOD_BITS = {"shift": 1, "option": 2, "control": 4}
_KEY_FLAGS = {"command": 0x100000, "shift": 0x20000, "control": 0x40000, "option": 0x80000}
_TITLE_SUFFIX_RE = re.compile(r"\s*\[[^\]]*\]$|…$")  # VS Code 把快捷鍵寫在標題後面（『縮小 [⇧⌘ㄦ]』）

_lib: Optional[tuple] = None
_names: Dict[str, int] = {}
_menu_cache: Dict[tuple, int] = {}  # (pid, 規格) → 選單項目（保留參考）；用之前會重新確認還有效


def _load() -> tuple:
    global _lib
    if _lib is None:
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        ax = ctypes.CDLL(ctypes.util.find_library("ApplicationServices"))
        cf.CFStringCreateWithCString.restype, cf.CFStringCreateWithCString.argtypes = c_void_p, [c_void_p, c_char_p, c_uint32]
        cf.CFStringGetCString.restype, cf.CFStringGetCString.argtypes = c_bool, [c_void_p, c_char_p, c_long, c_uint32]
        cf.CFGetTypeID.restype, cf.CFGetTypeID.argtypes = c_long, [c_void_p]
        for fn in (cf.CFStringGetTypeID, cf.CFArrayGetTypeID, cf.CFNumberGetTypeID, cf.CFBooleanGetTypeID):
            fn.restype = c_long
        cf.CFArrayGetCount.restype, cf.CFArrayGetCount.argtypes = c_long, [c_void_p]
        cf.CFArrayGetValueAtIndex.restype, cf.CFArrayGetValueAtIndex.argtypes = c_void_p, [c_void_p, c_long]
        cf.CFNumberGetValue.argtypes = [c_void_p, c_long, c_void_p]
        cf.CFBooleanGetValue.restype, cf.CFBooleanGetValue.argtypes = c_bool, [c_void_p]
        cf.CFRetain.restype, cf.CFRetain.argtypes = c_void_p, [c_void_p]
        cf.CFRelease.argtypes = [c_void_p]
        ax.AXIsProcessTrusted.restype = c_bool
        ax.AXUIElementCreateApplication.restype, ax.AXUIElementCreateApplication.argtypes = c_void_p, [c_int32]
        ax.AXUIElementSetMessagingTimeout.argtypes = [c_void_p, c_float]
        ax.AXUIElementCopyAttributeValue.restype = c_int32
        ax.AXUIElementCopyAttributeValue.argtypes = [c_void_p, c_void_p, POINTER(c_void_p)]
        ax.AXUIElementSetAttributeValue.restype, ax.AXUIElementSetAttributeValue.argtypes = c_int32, [c_void_p, c_void_p, c_void_p]
        ax.AXUIElementPerformAction.restype, ax.AXUIElementPerformAction.argtypes = c_int32, [c_void_p, c_void_p]
        ax.CGEventCreateKeyboardEvent.restype, ax.CGEventCreateKeyboardEvent.argtypes = c_void_p, [c_void_p, c_uint16, c_bool]
        ax.CGEventSetFlags.argtypes = [c_void_p, c_uint64]
        ax.CGEventPost.argtypes = [c_uint32, c_void_p]
        _lib = (cf, ax, c_void_p.in_dll(cf, "kCFBooleanTrue"), c_void_p.in_dll(cf, "kCFBooleanFalse"))
    return _lib


def _name(s: str) -> int:
    if s not in _names:
        _names[s] = _load()[0].CFStringCreateWithCString(None, s.encode(), _UTF8)
    return _names[s]


def _get(el: int, name: str):
    """讀一個屬性 → str／int／bool／[元件參考…]／元件參考；讀不到回傳 None。字串、數字讀完就釋放；元件由呼叫端 _free"""
    cf, ax = _load()[:2]
    out = c_void_p()
    if ax.AXUIElementCopyAttributeValue(el, _name(name), byref(out)) != 0 or not out.value:
        return None
    v, t = out.value, cf.CFGetTypeID(out.value)
    if t == cf.CFStringGetTypeID():
        buf = ctypes.create_string_buffer(2048)
        ok = cf.CFStringGetCString(v, buf, len(buf), _UTF8)
        cf.CFRelease(v)
        return buf.value.decode("utf-8", "replace") if ok else ""
    if t == cf.CFNumberGetTypeID():
        n = c_longlong()
        cf.CFNumberGetValue(v, 11, byref(n))
        cf.CFRelease(v)
        return n.value
    if t == cf.CFBooleanGetTypeID():
        b = bool(cf.CFBooleanGetValue(v))
        cf.CFRelease(v)
        return b
    if t == cf.CFArrayGetTypeID():
        items = [cf.CFRetain(cf.CFArrayGetValueAtIndex(v, i)) for i in range(cf.CFArrayGetCount(v))]
        cf.CFRelease(v)
        return items
    return v


def _free(*els) -> None:
    cf = _load()[0]
    for el in els:
        for e in (el if isinstance(el, list) else [el]):
            if e:
                cf.CFRelease(e)


def _app(pid: int) -> int:
    ax = _load()[1]
    app = ax.AXUIElementCreateApplication(pid)
    ax.AXUIElementSetMessagingTimeout(app, _AX_TIMEOUT_S)
    return app


def _press(el: int) -> bool:
    return _load()[1].AXUIElementPerformAction(el, _name("AXPress")) == 0


def _window(app: int) -> Optional[int]:
    """目前操作中的視窗：焦點視窗 → 主視窗"""
    for name in ("AXFocusedWindow", "AXMainWindow"):
        w = _get(app, name)
        if w:
            return w
    return None


def trusted() -> bool:
    """這個行程有沒有輔助使用權限（系統設定 → 隱私權與安全性 → 輔助使用）"""
    try:
        return bool(_load()[1].AXIsProcessTrusted())
    except (OSError, AttributeError):
        return False


def window_op(pid: int, op: str) -> str:
    """對 App 目前的視窗：close（按紅色關閉鈕，整個視窗含分頁）／minimize／fullscreen（切換）"""
    try:
        cf, ax, true, false = _load()
        app = _app(pid)
        win = _window(app)
        if not win:
            _free(app)
            return "missing"
        try:
            if op == "close":
                btn = _get(win, "AXCloseButton")
                if not btn:
                    return "missing"
                ok = _press(btn)
                _free(btn)
            elif op == "minimize":
                ok = ax.AXUIElementSetAttributeValue(win, _name("AXMinimized"), true) == 0
            elif op == "fullscreen":
                now = _get(win, "AXFullScreen")
                if now is None:
                    return "missing"
                ok = ax.AXUIElementSetAttributeValue(win, _name("AXFullScreen"), false if now else true) == 0
            else:
                return "error"
            return "ok" if ok else "error"
        finally:
            _free(win, app)
    except (OSError, AttributeError, ValueError):
        return "error"


def _clean(title: Optional[str]) -> str:
    return _TITLE_SUFFIX_RE.sub("", title or "").strip()


def _find_by_key(menu_bar: int, chars: str, mask: int) -> Optional[int]:
    """走訪選單找『快捷鍵是這個字元＋修飾鍵』的項目（和介面語言無關）。跳過第一個（蘋果選單）"""
    tops = _get(menu_bar, "AXChildren") or []
    found = None
    queue = deque((t, 0) for t in tops[1:])
    _free(tops[:1])
    while queue:
        el, depth = queue.popleft()
        if found is None:
            ch = _get(el, "AXMenuItemCmdChar")
            if ch and ch.upper() in chars and (_get(el, "AXMenuItemCmdModifiers") or 0) == mask:
                found = el
                continue
            if depth < _MENU_DEPTH:
                queue.extend((c, depth + 1) for c in _get(el, "AXChildren") or [])
        _free(el)
    return found


def _find_by_path(menu_bar: int, path: Sequence[Sequence[str]]) -> Optional[int]:
    """依標題一層一層找（每層可給多個語言的名稱），例如 [["檢視", "View"], ["終端", "Terminal"]]"""
    level = _get(menu_bar, "AXChildren") or []
    for i, names in enumerate(path):
        hit = None
        for el in level:
            if hit is None and _clean(_get(el, "AXTitle")) in names:
                hit = el
            else:
                _free(el)
        if hit is None or i == len(path) - 1:
            return hit
        menus = _get(hit, "AXChildren") or []  # 選單列項目 → 選單 → 項目
        level = (_get(menus[0], "AXChildren") or []) if menus else []
        _free(hit, menus)
    return None


def menu_mask(mods: Iterable[str]) -> int:
    """快捷鍵修飾鍵 → AXMenuItemCmdModifiers 的值（⌘ 是預設，沒有 ⌘ 要加 8）"""
    mods = list(mods)
    return sum(_MENU_MOD_BITS.get(m, 0) for m in mods) | (0 if "command" in mods else 8)


def press_menu(pid: int, chars: str = "", mods: Iterable[str] = (), path: Optional[Sequence[Sequence[str]]] = None) -> Tuple[str, str]:
    """按 App 的選單項目：path（標題路徑）或 chars＋mods（快捷鍵，chars 可列多個候選字元如 "+="）。
    回傳 (狀態, 項目標題)。找到的項目會快取（下次不必再走訪整個選單）"""
    try:
        mask = menu_mask(mods)
        key = (pid, tuple(tuple(p) for p in path) if path else (chars.upper(), mask))
        item = _menu_cache.get(key)
        if item is not None and _get(item, "AXRole") is None:  # App 重開或選單重建：快取失效
            _free(_menu_cache.pop(key))
            item = None
        if item is None:
            app = _app(pid)
            bar = _get(app, "AXMenuBar")
            if not bar:
                _free(app)
                return "error", ""
            item = _find_by_path(bar, path) if path else _find_by_key(bar, chars.upper(), mask)
            _free(bar, app)
            if item is None:
                return "missing", ""
            _menu_cache[key] = item
        title = _clean(_get(item, "AXTitle"))
        if _get(item, "AXEnabled") is False:
            return "disabled", title
        return ("ok" if _press(item) else "error"), title
    except (OSError, AttributeError, ValueError):
        return "error", ""


def _find_button(app: int, pattern: str) -> Optional[int]:
    win = _window(app)
    if not win:
        return None
    regex = re.compile(pattern)
    deadline = time.monotonic() + _SEARCH_BUDGET_S
    queue, seen, found = deque([win]), 0, None
    while queue:
        el = queue.popleft()
        if found is None and seen < _SEARCH_MAX_NODES and time.monotonic() < deadline:
            seen += 1
            if _get(el, "AXRole") == "AXButton" and regex.search(_get(el, "AXDescription") or _get(el, "AXTitle") or ""):
                found = el
                continue
            queue.extend(_get(el, "AXChildren") or [])
        _free(el)
    return found


def button_label(pid: int, pattern: str) -> Optional[str]:
    """目前視窗裡，說明文字符合 pattern 的按鈕的文字（沒有回傳 None）"""
    try:
        app = _app(pid)
        btn = _find_button(app, pattern)
        label = (_get(btn, "AXDescription") or _get(btn, "AXTitle") or "") if btn else None
        _free(btn, app)
        return label
    except (OSError, AttributeError, ValueError, re.error):
        return None


def press_button(pid: int, pattern: str) -> str:
    """按目前視窗裡說明文字符合 pattern 的按鈕（例如 YouTube 播放器的『暫停 (k)』）"""
    try:
        app = _app(pid)
        btn = _find_button(app, pattern)
        if not btn:
            _free(app)
            return "missing"
        ok = _press(btn)
        _free(btn, app)
        return "ok" if ok else "error"
    except (OSError, AttributeError, ValueError, re.error):
        return "error"


def post_key(code: int, mods: Iterable[str] = ()) -> str:
    """對最前面的 App 送一次按鍵（key code）。會經過輸入法：只用在 ⌘ 組合鍵與翻頁鍵，
    沒有 ⌘ 的字元鍵（⌃`、k）不可以走這裡。呼叫端要先確認最前面的 App 沒變"""
    try:
        ax = _load()[1]
        flags = sum(_KEY_FLAGS[m] for m in mods)
        for down in (True, False):
            ev = ax.CGEventCreateKeyboardEvent(None, int(code), down)
            if not ev:
                return "error"
            ax.CGEventSetFlags(ev, flags)
            ax.CGEventPost(0, ev)  # kCGHIDEventTap
            _load()[0].CFRelease(ev)
        return "ok"
    except (OSError, AttributeError, KeyError, ValueError):
        return "error"
