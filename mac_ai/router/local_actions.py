"""第二層：依 Intent 執行本機動作（資料夾、檔案搜尋、選擇清單）。只讀 Intent.slots，不看原句。

檔案搜尋用 Spotlight（mdfind），只搜家目錄且排除 ~/Library；預設只在 Finder 中顯示，
使用者明說『打開』才開檔，而且可執行檔一律只顯示。這裡的工具只放在 LOCAL_TOOLS，
不在 LLM 的 TOOLS_SCHEMA 裡，所以 LLM 叫不到它們。"""
import datetime
import os
import random
import re
import sqlite3
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

from mac_ai import config
from mac_ai.config import CONTEXT_TIMEOUT_S, FILE_SEARCH_TIMEOUT_S, FIND_RESULTS_MAX
from mac_ai.router import choices, context, native
from mac_ai.router.actions import (
    LOCAL_TOOLS, POLL_TIMEOUT_S, _FAIL_PREFIXES, ExecContext, _dispatch, _skipped, front_app, front_context, front_site, mark_page, _run,
    google_search_url, open_music_video, open_search_page, web_search_or_open_url,
)
from mac_ai.router.domain import Intent
from mac_ai.router.lexicon import LEXICON
from mac_ai import db as music_db
from mac_ai.router import (
    cloud_client, music_prefs, search_client, search_prefs, sources, weather_client, weather_prefs, youtube_client,
)

_FOLDERS = {f["key"]: f for f in LEXICON["known_folders"]}
_KINDS = LEXICON["file_kinds"]
_SINCE_ZH = {"today": "今天", "yesterday": "昨天", "this_week": "這週", "recent": "最近"}
_SINCE_PREDICATE = {
    "today": "kMDItemFSContentChangeDate >= $time.today",
    "yesterday": "kMDItemFSContentChangeDate >= $time.today(-1) && kMDItemFSContentChangeDate < $time.today",
    "this_week": "kMDItemFSContentChangeDate >= $time.this_week",
    "recent": "kMDItemFSContentChangeDate >= $time.today(-7)",
}
# 開檔等於執行：這些類型（以及沒有副檔名但有執行權限的檔案）一律只在 Finder 中顯示
DANGEROUS_EXT = {
    ".app", ".command", ".tool", ".sh", ".terminal", ".pkg", ".mpkg", ".workflow", ".scpt", ".scptd",
    ".applescript", ".jar", ".webloc", ".inetloc", ".fileloc",
}
_EXCLUDE_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "site-packages"}
_BUNDLE_SUFFIXES = (".app", ".framework", ".bundle")
_MAX_SCAN = 20000  # 一次最多處理這麼多筆 mdfind 輸出（只比對類型的搜尋可能有幾十萬筆）


# ---------- 對應表：(domain, 動詞) → 工具呼叫 ----------
def _plan_app(i: Intent) -> List[Tuple[str, dict]]:
    return [("control_mac_app", {"app_name": i.slots["app"], "action": "open"})]


def _plan_project(i: Intent) -> List[Tuple[str, dict]]:
    return [("open_project", {"project_name": i.slots["project_name"]})]


def _plan_folder(i: Intent) -> List[Tuple[str, dict]]:
    return [("open_folder", {"path_key": i.slots["path_key"]})]


def _plan_web(i: Intent) -> List[Tuple[str, dict]]:
    return [("web_search_or_open_url", {"query": i.slots["query"]})]


def _plan_file(action: str) -> Callable[[Intent], List[Tuple[str, dict]]]:
    def plan(i: Intent) -> List[Tuple[str, dict]]:
        args = {k: v for k, v in i.slots.items() if v}
        args["action"] = action
        return [("find_file", args)]
    return plan


ACTION_TABLE: Dict[Tuple[str, str], Callable[[Intent], List[Tuple[str, dict]]]] = {}
for _verb in ("find", "open", "search"):
    ACTION_TABLE[("app", _verb)] = _plan_app
    ACTION_TABLE[("project", _verb)] = _plan_project
    ACTION_TABLE[("folder", _verb)] = _plan_folder
    ACTION_TABLE[("web", _verb)] = _plan_web
    ACTION_TABLE[("file", _verb)] = _plan_file("open" if _verb == "open" else "reveal")  # 只有明說『打開』才開檔


def plan_actions(intent: Intent) -> List[Tuple[str, dict]]:
    return ACTION_TABLE[(intent.domain, intent.verb)](intent)


# ---------- 資料夾 ----------
def _folder_path(key: str) -> Optional[str]:
    f = _FOLDERS.get(key)
    return os.path.expanduser(f["path"]) if f else None


def open_folder(path_key: str, ctx: Optional[ExecContext] = None) -> str:
    path = _folder_path(path_key)
    if not path or not os.path.isdir(path):
        return f"❌ 資料夾不存在：{path or path_key}"
    ok, err = _run(["open", path])
    label = _FOLDERS[path_key]["names"][0]
    return f"✅ 已開啟資料夾：{label}（{path}）" if ok else f"❌ 開啟資料夾失敗：{label}（{err}）"


# ---------- 檔案搜尋 ----------
def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_predicate(name: str = "", kinds: Optional[List[str]] = None, since: Optional[str] = None) -> str:
    """Spotlight 查詢字串。檔名每個詞各自比對（大小寫不分、包含即可）；使用者字串裡的 * ? 先拿掉，" \\ 跳脫"""
    parts = [f'kMDItemFSName == "*{_esc(t)}*"cd' for t in re.sub(r"[*?]", "", name).split()]
    utis = [u for k in (kinds or []) for u in _KINDS[k]["uti"]]
    if utis:
        parts.append("(" + " || ".join(f'kMDItemContentTypeTree == "{u}"' for u in utis) + ")")
    if since:
        parts.append(_SINCE_PREDICATE[since])
    return " && ".join(parts) or 'kMDItemFSName == "*"'


def is_dangerous(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in DANGEROUS_EXT:
        return True
    return not ext and os.path.isfile(path) and os.access(path, os.X_OK)


def _filter_results(lines: List[str], home: str) -> List[str]:
    """只留家目錄底下、不在 ~/Library、隱藏資料夾、node_modules 之類、套件（.app 等）內部的結果"""
    home_real = os.path.realpath(home)
    kept = []
    for line in lines[:_MAX_SCAN]:
        path = line.strip()
        if not path.startswith(home + os.sep):
            continue
        parts = path[len(home) + 1:].split(os.sep)
        if parts[0] == "Library" or any(p.startswith(".") or p in _EXCLUDE_DIRS for p in parts):
            continue
        if any(p.endswith(_BUNDLE_SUFFIXES) for p in parts[:-1]):
            continue
        real = os.path.realpath(path)
        if real != home_real and not real.startswith(home_real + os.sep):
            continue
        kept.append(path)
    return kept


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _rank(paths: List[str], name: str, filename: Optional[str]) -> List[str]:
    """檔名吻合程度（完全相同 > 開頭相同 > 包含）優先，相同時修改時間新的在前"""
    tokens = [t.lower() for t in re.sub(r"[*?]", "", name).split()]
    joined = "".join(tokens)

    def key(path: str):
        base = os.path.basename(path).lower()
        stem = os.path.splitext(base)[0]
        if (filename and base == filename.lower()) or (joined and stem.replace(" ", "") == joined):
            tier = 0
        elif tokens and stem.startswith(tokens[0]):
            tier = 1
        else:
            tier = 2
        return tier, -_mtime(path)

    return sorted(paths, key=key)


def _label(name: str, kinds: List[str], since: Optional[str]) -> str:
    kind_words = [_KINDS[k]["words"][0] for k in kinds if _KINDS[k]["words"]]
    return " ".join(p for p in (_SINCE_ZH.get(since or ""), *kind_words, name) if p) or "所有檔案"


def _act(path: str, action: str) -> Tuple[bool, str]:
    """reveal＝在 Finder 中顯示；open＝開檔（可執行檔改成只顯示）。回傳 (成功?, 訊息)"""
    name, where = os.path.basename(path), choices.short_dir(path)
    if action == "open" and not is_dangerous(path):
        ok, err = _run(["open", path])
        return ok, f"✅ 已開啟：{name}（{where}）" if ok else f"❌ 開啟失敗：{name}（{err}）"
    ok, err = _run(["open", "-R", path])
    if not ok:
        return False, f"❌ 在 Finder 中顯示失敗：{name}（{err}）"
    if action == "open":
        return True, f"✅ 這是可執行檔，只在 Finder 中顯示：{name}（{where}）"
    return True, f"✅ 已在 Finder 中顯示：{name}（{where}）"


def _close_previous(ctx: Optional[ExecContext]) -> None:
    """新的搜尋會取代舊清單；舊清單若還開著，通知 UI 關掉"""
    prev = choices.STORE.get()
    choices.STORE.clear()
    if prev is not None and ctx is not None and ctx.step is not None:
        ctx.step["choices"] = {"list_id": prev.list_id, "closed": True}


def find_file(name: str = "", kinds: Optional[List[str]] = None, since: Optional[str] = None,
              location: Optional[str] = None, filename: Optional[str] = None, action: str = "reveal",
              local_only: bool = False, ctx: Optional[ExecContext] = None) -> str:
    """0 筆：local_only（使用者明確要找本機）→ 回『本機沒有』；否則設定 ctx.escalate，由 pipeline 轉交模型判斷
    （可能是要查網路，例如『找 python 教學』）。搜尋本身失敗（逾時等）一律回報原因，不轉交"""
    kinds = kinds or []
    home = os.path.expanduser("~")
    scope = _folder_path(location) if location else home
    scope_label = _FOLDERS[location]["names"][0] if location else "家目錄，不含 ~/Library"
    _close_previous(ctx)
    if not scope or not os.path.isdir(scope):
        return f"❌ 找不到資料夾：{location}"
    step = ctx.step if ctx is not None and ctx.step is not None else {}
    t = time.perf_counter()
    ok, out = _run(["mdfind", "-onlyin", scope, build_predicate(name, kinds, since)], FILE_SEARCH_TIMEOUT_S)
    step["find_ms"] = (time.perf_counter() - t) * 1000
    if not ok:
        return f"❌ 檔案搜尋失敗（{out}）"
    ranked = _rank(_filter_results(out.splitlines(), home), name, filename)
    if filename:  # 說出完整檔名（含副檔名）而且剛好只有一個完全相同的，就不用選
        exact = [p for p in ranked if os.path.basename(p).lower() == filename.lower()]
        if len(exact) == 1:
            ranked = exact
    total = len(ranked)
    label = _label(name, kinds, since)
    if total == 0:
        if local_only or ctx is None:
            return f"⚠️ 找不到「{label}」：本機沒有符合的檔案（範圍：{scope_label}）"
        ctx.escalate = {"label": label, "scope": scope_label}
        return f"🔎 本機沒有符合「{label}」的檔案（範圍：{scope_label}），交給 AI 判斷"
    if total == 1:
        return _act(ranked[0], action)[1]
    shown = ranked[:FIND_RESULTS_MAX]
    lst = choices.STORE.put(label, action, shown, total)
    step["choices"] = choices.STORE.payload(lst)
    how = "開啟" if action == "open" else "顯示"
    summary = f"找到 {total} 筆「{label}」" + (f"，列出前 {len(shown)} 筆" if total > len(shown) else "")
    if ctx is not None:
        ctx.replies.append(f"{summary}。說「第二個」或點選清單即可{how}")
    lines = "\n".join(f"{i}. {os.path.basename(p)}（{choices.short_dir(p)}）" for i, p in enumerate(shown, 1))
    return f"✅ {summary}（說「第 N 個」選擇，預設{how}）：\n{lines}"


def pick_choice(n: int, action: Optional[str] = None, list_id: Optional[int] = None,
                ctx: Optional[ExecContext] = None) -> str:
    """從最近一次的多筆結果選一筆（n＝-1 表示最後一筆）。清單過期、被取代或序號超出都回報，不執行任何動作"""
    lst = choices.STORE.get()
    if lst is None:
        return "⚠️ 沒有可選的清單（已過期或尚未搜尋）"
    if list_id is not None and list_id != lst.list_id:
        return "⚠️ 清單已更新，請重選"
    count = len(lst.items)
    if n == -1:
        n = count
    if not 1 <= n <= count:
        return f"⚠️ 清單只有 {count} 筆"
    item = lst.items[n - 1]
    if isinstance(item, choices.WebItem):  # 聯合搜尋的網路項目：用瀏覽器開（U11），不顯示、不開檔
        msg = _open_web(item, ctx)
        ok = not msg.startswith(_FAIL_PREFIXES)
    else:
        ok, msg = _act(item, action or lst.default_action)
    if ok and lst.term:  # 聯合清單選了哪一邊（本機連同路徑；網路不記網址）
        record_search(lst.term, "web" if isinstance(item, choices.WebItem) else "local", "picked", ctx,
                      path=None if isinstance(item, choices.WebItem) else item)
    if ok:
        choices.STORE.clear()
        if ctx is not None and ctx.step is not None:
            ctx.step["choices"] = {"list_id": lst.list_id, "closed": True}
    return msg


# ---------- 聯合搜尋（沒說範圍的找／搜尋，計畫 docs/plans/active/2026-11-23-query-routing.md §4.3）----------
def record_search(query: str, side: str, signal: str, ctx: Optional[ExecContext] = None, path: Optional[str] = None) -> None:
    """記一筆常搜名詞（search_prefs）。資料庫還沒準備好就不記；失敗只記例外類別，不影響指令"""
    conn = MUSIC_DB.conn()
    if conn is None:
        return
    info = ctx.step.setdefault("search_prefs", {}) if ctx is not None and ctx.step is not None else {}
    _db_call(info, search_prefs.record, conn, query, side, signal, _now(), path=path)


def _local_hits(query: str) -> Tuple[bool, List[str]]:
    """家目錄 mdfind（範圍、排除、排序同 find_file）→ (成功?, 排好的路徑)"""
    home = os.path.expanduser("~")
    ok, out = _run(["mdfind", "-onlyin", home, build_predicate(query)], FILE_SEARCH_TIMEOUT_S)
    return (True, _rank(_filter_results(out.splitlines(), home), query, None)) if ok else (False, [])


def _web_hits(query: str) -> Tuple[List[choices.WebItem], str]:
    """有 Brave 金鑰 → 搜尋結果的標題與網址；沒有金鑰或失敗 → 一筆「在 Google 搜尋」（U9）。回傳 (項目, 錯誤分類)"""
    google = [choices.WebItem(f"在 Google 搜尋「{query}」", google_search_url(query))]
    if not search_client.available():
        return google, "no_key"
    try:
        results = search_client.search(query)
    except search_client.SearchError as e:
        return google, e.kind
    if not results:
        return google, "no_results"
    return [choices.WebItem(r.title, r.url) for r in results], ""


def _open_web(item: choices.WebItem, ctx: Optional[ExecContext]) -> str:
    """網路項目：前景是瀏覽器就用它開，否則交給預設瀏覽器（U11）。結果文字不帶標題與網址（會寫進紀錄）"""
    browser = (ctx.last_browser or front_app(ctx, "browser")) if ctx is not None else None
    ok, err = _run(["open", "-a", browser, item.url] if browser else ["open", item.url])
    res = "✅ 已用瀏覽器打開網路結果" if ok else f"❌ 開啟瀏覽器失敗（{err}）"
    if ctx is not None:
        mark_page(ctx, res)
    return res


def federated_search(query: str, ctx: Optional[ExecContext] = None) -> str:
    """沒說範圍的『找周杰倫』『搜尋履歷』：本機（mdfind）與網路（Brave；沒有金鑰就是一筆 Google 搜尋）並行，合併成一份清單（U1、U5、U6、U9）。
    前景瀏覽器停在詞庫網站、或同句剛開了瀏覽器 → 照舊站內搜尋（context_search）。
    統計（search_prefs.lean）：U8 成立時只查偏好的那一邊，偏本機而且記錄的路徑還在 → 直接顯示那個路徑；清單依偏好決定先列哪一邊。
    只有一邊有結果而且只有 1 筆 → 直接執行。各階段記在 ctx.step["federated"]（筆數、毫秒、lean；不記標題與網址）"""
    ctx = ctx or ExecContext()
    if ctx.last_browser or front_site(ctx) is not None:
        return context_search(query, ctx)
    info: dict = ctx.step.setdefault("federated", {}) if ctx.step is not None else {}
    _close_previous(ctx)
    conn = MUSIC_DB.conn()
    lean = (_db_call(info, search_prefs.lean, conn, query, _now()) if conn is not None else None) or search_prefs.Lean()
    if lean.side:
        info["lean"] = lean.side + ("（直接）" if lean.direct else "")
    if lean.direct and lean.side == "local" and lean.path and os.path.exists(lean.path):
        info["used"] = "path"
        return _act(lean.path, "reveal")[1]
    if lean.direct and lean.side == "web":
        info["used"] = "web"
        return context_search(query, ctx)
    t = time.perf_counter()
    only_local = lean.direct and lean.side == "local"  # 偏本機但記錄的路徑不在了：先查本機，0 筆才查網路
    with ThreadPoolExecutor(max_workers=2) as pool:
        local_future = pool.submit(_local_hits, query)
        web_future = None if only_local else pool.submit(_web_hits, query)
        local_ok, paths = local_future.result()
        web, web_error = web_future.result() if web_future is not None else ([], "")
    if only_local and not paths:
        web, web_error = _web_hits(query)
    info.update(ms=(time.perf_counter() - t) * 1000, local=len(paths), web=len(web))
    if not local_ok:
        info["local_error"] = True
    if web_error:
        info["web_error"] = web_error
    local_items, web_items = paths[:config.FEDERATED_LOCAL_MAX], web[:config.FEDERATED_WEB_MAX]
    if len(local_items) + len(web_items) == 1:  # 只有一邊、只有 1 筆：直接執行
        if local_items:
            return _act(local_items[0], "reveal")[1]
        if web_error:  # 只剩 Google 搜尋：與沒有這個功能時逐字相同
            return _dispatch("web_search_or_open_url", {"query": query}, ctx)
        return _open_web(web_items[0], ctx)
    items: List[choices.Item] = [*web_items, *local_items] if lean.side == "web" else [*local_items, *web_items]
    items = items[:FIND_RESULTS_MAX]
    lst = choices.STORE.put(query, "reveal", items, len(paths) + len(web), term=query)
    if ctx.step is not None:
        ctx.step["choices"] = choices.STORE.payload(lst)
    web_label = "Google 搜尋" if web_error else f"網路 {len(web)} 筆"
    summary = f"找到「{query}」：本機 {len(paths)} 筆、{web_label}"
    ctx.replies.append(f"{summary}。說「第二個」或點選清單")
    lines = "\n".join(f"{n}. {os.path.basename(i)}（{choices.short_dir(i)}）" if isinstance(i, str) else f"{n}. （網路）"
                      for n, i in enumerate(items, 1))
    return f"✅ {summary}（說「第 N 個」選擇）：\n{lines}"


def record_explicit_search(query_intent, ctx: Optional[ExecContext] = None) -> None:
    """Fast Path 執行成功後：句子明說範圍的找／搜尋記一筆 explicit（找履歷檔案 → 本機、上網查履歷 → 網路）。
    統計或預設決定的（聯合搜尋、沒有證據的找）不記。query_intent 是 query_intent.QueryIntent"""
    q = query_intent
    if q.target == "local" and q.intent is not None and q.intent.domain == "file" and q.evidence in ("scope_word", "file_hint"):
        term = q.intent.slots.get("name") or q.intent.slots.get("filename") or ""
        if term:
            record_search(term, "local", "explicit", ctx)
    elif q.target == "knowledge" and q.mode == "show" and q.evidence == "explicit_web" and q.query:
        record_search(q.query, "web", "explicit", ctx)


# ---------- 前景視窗：站內搜尋、常用功能 ----------
def context_search(query: str, ctx: Optional[ExecContext] = None) -> str:
    """沒指定瀏覽器的搜尋：前景瀏覽器停在詞庫 sites 的網站就用站內搜尋（開新分頁），其餘照一般的網頁搜尋。
    同句剛開的瀏覽器優先於前景（『打開 Chrome 搜尋 X』不會被前景的網站帶走）"""
    ctx = ctx or ExecContext()
    if "browser" in ctx.failed:
        return _skipped("context_search")
    if not ctx.last_browser and not query.startswith(("http://", "https://")):
        site = front_site(ctx)
        if site is not None:
            url = site["search_url"].replace("{q}", urllib.parse.quote(query))
            ok, err = _run(["open", "-a", ctx.front.app, url])
            res = f"✅ 已在 {site['name']} 搜尋：{query}" if ok else f"❌ 開啟瀏覽器失敗（{err}）"
            mark_page(ctx, res)
            return res
    return _dispatch("web_search_or_open_url", {"query": query}, ctx)


def _run_binding(front: context.FrontContext, b: dict) -> Tuple[str, str]:
    """依詞庫規格對前景 App 的行程直接下指令（不經過鍵盤與輸入法）→ (狀態, 做法)。
    狀態：ok／missing（找不到視窗、選單項目或按鈕）／disabled（選單項目停用）／error／front_changed"""
    if not front.pid:
        return "error", "沒有行程編號"
    if "window" in b:
        return native.window_op(front.pid, b["window"]), "視窗"
    if "path" in b:
        return native.press_menu(front.pid, path=b["path"])[0], "選單"
    if "button" in b:
        return native.press_button(front.pid, b["button"]), "按鈕"
    if "script" in b:  # App 自己的指令（Music、Spotify 的 playpause／pause／play）；指令字只來自詞庫
        ok, _ = _run(["osascript", "-e", f'tell application "{front.app}" to {b["script"]}'], POLL_TIMEOUT_S)
        return ("ok" if ok else "error"), "App 指令"
    if "char" in b:  # 有快捷鍵的功能：按 App 自己的選單項目（依快捷鍵找，和介面語言無關）
        status, _ = native.press_menu(front.pid, b["char"], b.get("mods", []))
        if status != "missing":
            return status, "選單"
    # 沒有對應選單項目（翻頁鍵、⌘↑）才送按鍵：會經過輸入法，所以詞庫只放 ⌘ 組合鍵與翻頁鍵；送之前確認前景沒變
    now = context.read_front(_run)
    if now is None or now.pid != front.pid:
        return "front_changed", "按鍵"
    return native.post_key(b["code"], b.get("mods", [])), "按鍵"


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


def front_action(id: str, fallback: Optional[List[list]] = None, direction: Optional[str] = None,
                 ctx: Optional[ExecContext] = None) -> str:
    """執行常用功能（見 _front_action）；播放控制成功後另外寫一筆事件（_record_control），不影響控制鏈的結果"""
    res = _front_action(id, fallback, direction, ctx)
    if ctx is not None:
        _record_control(ctx)
    return res


def _front_action(id: str, fallback: Optional[List[list]] = None, direction: Optional[str] = None,
                  ctx: Optional[ExecContext] = None) -> str:
    """對最前面的 App 執行詞庫 app_shortcuts 定義的常用功能（底層做法見 _run_binding）。這個功能不支援目前的 App 時：
    有 fallback（加上前景判斷之前的原動作，例如『開終端機』→ 開 Terminal.app）就執行它；有 background（播放控制）
    就改控制背景的音樂（見 _background_control；direction＝pause／resume，只有 Fast Path 的說法會帶）；否則回報、什麼都不做"""
    ctx = ctx or ExecContext()
    entry = context.SHORTCUTS[id]
    front = front_context(ctx)
    site = None
    if front is not None and front.kind == "browser" and context.needs_site(entry):
        hit = front_site(ctx)
        site = hit["name"] if hit else None
    binding = context.pick_binding(entry, front, site)
    if binding is not None and direction:  # 暫停／繼續：前景是 Music／Spotify／YouTube 分頁時依方向下指令，不切換
        binding = {**binding, **binding.get("by_direction", {}).get(direction, {})}
    if binding is None:
        if ctx.step is not None:
            ctx.step["context"]["used"] = False
        if "background" in entry:
            return _background_control(entry, direction, ctx)
        if fallback:
            return "；".join(_dispatch(name, dict(args), ctx) for name, args in fallback)
        if front is None:
            return f"⚠️ 不知道要操作哪個視窗（讀不到最前面的 App），沒有執行「{entry['label']}」"
        return f"⚠️ 目前最前面的是 {front.app}，「{entry['label']}」只支援{entry['scope']}"
    status, method = _run_binding(front, binding)
    if ctx.step is not None:
        ctx.step["context"]["method"] = method
    if status == "ok":
        if ctx.step is not None and "background" in entry:  # 播放控制：記下做法（前景是 Music／Spotify 用 App 指令，YouTube 分頁用按鈕）
            ctx.step["control"] = {"op": _control_op(id, direction), "source": "app" if method == "App 指令" else "ax"}
        return f"✅ {front.app}：{entry['label']}"
    if status == "disabled":
        return f"⚠️ {front.app} 目前不能「{entry['label']}」（選單項目停用）"
    if status == "missing":
        if direction and binding.get("done_when") and native.button_label(front.pid, binding["done_when"]):  # 要暫停而畫面上是『播放』：已經是目標狀態，不動作
            if ctx.step is not None:
                ctx.step["control"] = {"op": _control_op(id, direction), "source": "ax", "already": True}
            return f"✅ {front.app}：{entry['background'][direction]['label']}：已經是這個狀態"
        return f"❌ {entry['label']}失敗：{front.app} 找不到可操作的{method}"
    if status == "front_changed":
        return f"❌ 最前面的 App 已經不是 {front.app}，沒有執行「{entry['label']}」"
    return f"❌ {entry['label']}失敗：{front.app}（{method}；請確認已授權輔助使用）"


# ---------- 點歌（play_media，計畫 §4.2）----------
MUSIC_DB = music_db.MusicDb()  # 偏好資料庫；main.py 啟動時 start()（背景準備），準備好之前 conn() 是 None：照常播放、不寫紀錄（D4、D7）。測試由 conftest 換成暫存目錄
_now: Callable[[], float] = time.time  # 時間與亂數都能注入（測試 monkeypatch）
_rng = random.Random()
NO_DB_FAVORITE_MESSAGE = "⚠️ 偏好資料庫還沒準備好，沒辦法播常聽的歌；說『我想聽』加歌名"


def _db_call(info: dict, fn: Callable, *args, **kw):
    """資料庫操作失敗（磁碟滿、被鎖…）不能讓點歌失敗：記下例外類別（不記內容）後回 None。全部在呼叫 play_media 的執行緒（Y16）"""
    try:
        return fn(*args, **kw)
    except sqlite3.Error as e:
        info["db_error"] = type(e).__name__
        return None


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
    fav = _db_call(info, music_prefs.favorite, conn, now, _rng)
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
    query 空字串＝常聽的歌。資料庫準備好之前（MUSIC_DB.conn() 是 None）照常搜尋、播放，只是不矯正、不查快取、不寫任何紀錄。
    所有資料庫讀寫都在這個函式的呼叫端執行緒（command_worker）；搜尋的網路在 youtube_client 自己的執行緒，不碰資料庫。
    各階段耗時與來源記在 ctx.step["music"]：source、search_ms、open_ms、db_ms、tab、browser、errors"""
    t0 = time.perf_counter()
    step = ctx.step if ctx is not None and ctx.step is not None else {}
    info = step["music"] = {}
    utterance = (ctx.utterance if ctx is not None and ctx.utterance else query) or ""
    now = _now()
    conn = MUSIC_DB.conn()
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
    conn = MUSIC_DB.conn()
    if conn is not None:
        _db_call(ctx.step.setdefault("music", {}), music_prefs.record_event, conn, control["op"], now=_now(),
                 utterance=ctx.utterance or None, source=control["source"])


def music_replace(ctx: Optional[ExecContext] = None) -> str:
    """換一首／不是這首（K7）：最近一次 play 事件給 query 與目前的影片；該 query 的候選清單（queries.results）裡，
    目前影片的下一筆就是要換的（第一次換第 2 筆、再換第 3 筆）。成功後快取改指向它並釘選、寫 replace 事件（舊歌名扣分）
    與 source＝replace 的 play 事件（query 沿用原本的，下一次換才找得到同一份候選）。常聽的歌（query 是空字串）沒有候選。不連網"""
    t0 = time.perf_counter()
    step = ctx.step if ctx is not None and ctx.step is not None else {}
    info = step["music"] = {}
    conn = MUSIC_DB.conn()
    if conn is None:
        return NO_DB_MUSIC_MESSAGE
    now = _now()
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
    conn = MUSIC_DB.conn()
    if conn is None:
        return NO_DB_MUSIC_MESSAGE
    t = time.perf_counter()
    video_id = _music_tab_video_id()
    info["read_ms"] = (time.perf_counter() - t) * 1000
    if video_id is None:
        return NO_MUSIC_TAB_MESSAGE
    title = (_db_call(info, _video_row, conn, video_id) or ("", ""))[0]
    _db_call(info, music_prefs.on_like if kind == "like" else music_prefs.on_dislike, conn, video_id, None, _now())
    if "db_error" in info:
        return f"❌ 沒有記下（資料庫錯誤：{info['db_error']}）"
    info["feedback"] = {"kind": kind, "video_id": video_id}
    what = title or video_id
    return f"✅ 記下了，你喜歡：{what}" if kind == "like" else f"✅ 記下了，之後不會再自動放：{what}"


def music_top(ctx: Optional[ExecContext] = None) -> str:
    """我最常聽什麼（S8）：前 5 名歌手與歌曲，文字放進 ctx.replies（不呼叫 LLM）。沒有紀錄回 NO_HISTORY_MESSAGE"""
    conn = MUSIC_DB.conn()
    if conn is None:
        return NO_DB_MUSIC_MESSAGE
    ranked = _db_call({}, music_prefs.top, conn, _now(), music_prefs.TOP_N)
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


# ---------- 天氣（get_weather，計畫 docs/plans/completed/2026-11-21-web-answer.md）----------
_TAIPEI = datetime.timezone(datetime.timedelta(hours=8))  # 台灣沒有日光節約時間，不需要 zoneinfo


def today_taipei() -> str:
    """台北時間的今天（YYYY-MM-DD）；長尾回答的規劃與回答兩次呼叫都用它（模型不知道今天幾號）"""
    return datetime.datetime.fromtimestamp(_now(), _TAIPEI).strftime("%Y-%m-%d")


def get_weather(city: str = "", day: str = "today", part: str = "", ctx: Optional[ExecContext] = None) -> str:
    """天氣回答（只讀資料：不開網頁、不呼叫 LLM）。city 空字串＝用資料庫統計的常問縣市，沒有紀錄就請使用者說縣市（不猜、不定位）；
    句子明說的縣市記進資料庫（不管查不查得到）。有 CWA_API_KEY 先查氣象署，沒有金鑰或失敗改查 Open-Meteo，兩個都失敗回 ❌ 查不到。
    資料庫還沒準備好（conn() 是 None）：明說縣市照常回答、只是不記；沒說縣市當冷啟動。結果文字放進 ctx.replies（泡泡）"""
    t0 = time.perf_counter()
    info: dict = ctx.step.setdefault("weather", {}) if ctx is not None and ctx.step is not None else {}
    now = _now()
    conn = MUSIC_DB.conn()
    explicit = bool(city)
    if explicit:
        if conn is not None:
            _db_call(info, weather_prefs.record_city, conn, city, now)
    else:
        city = (_db_call(info, weather_prefs.default_city, conn, now) if conn is not None else None) or ""
        if not city:
            info.update(city="", cold_start=True)
            return weather_prefs.COLD_START_MESSAGE
    res = weather_client.lookup(city, day, part, datetime.datetime.fromtimestamp(now, _TAIPEI).replace(tzinfo=None),
                                config.env_value("CWA_API_KEY").strip())
    info.update(city=city, explicit=explicit, source=res.source, tried=res.tried, ms=(time.perf_counter() - t0) * 1000)
    if res.text is None:
        return f"❌ 嗚…我查不到{city}的天氣耶，氣象服務好像沒有回應，等一下再問我一次好嗎？"
    if ctx is not None:
        ctx.replies.append(res.text)
    return f"✅ {res.text}"


# ---------- 長尾回答（搜尋 → 雲端依摘錄回答）----------
_WHY = {"search": "搜尋服務沒有回應", "cloud": "AI 沒有回應", "no_answer": "搜尋結果裡沒有答案", "truncated": "AI 的回答不完整",
        "no_search_key": "沒有設定搜尋金鑰"}


def _web_fallback(query: str, ctx: Optional[ExecContext], why: str) -> str:
    """搜尋、雲端摘要任何一步失敗或沒有答案：開 Google 搜尋頁（與沒有這個功能時一樣）並回 ⚠️，不謊稱查到（W4、紅線 9、11）"""
    browser = (ctx.last_browser or front_app(ctx, "browser")) if ctx is not None else None
    res = web_search_or_open_url(query, browser=browser)
    if ctx is not None:
        mark_page(ctx, res)
    if res.startswith(_FAIL_PREFIXES):
        return f"❌ 沒辦法直接回答（{_WHY[why]}），開啟瀏覽器也失敗了：{query}"
    return f"⚠️ 沒辦法直接回答（{_WHY[why]}），已經幫你開 Google 搜尋「{query}」"


def web_answer(query: str, ctx: Optional[ExecContext] = None) -> str:
    """長尾問題：Brave 搜尋（只送 query）→ 雲端依摘錄回答 1～2 句（不帶工具）→ 放進 ctx.replies，並記下第一筆來源供「要打開來源嗎？」。
    Fast Path 也會直接送來（會變的事實、查／找＋問句，計畫 docs/plans/active/2026-11-23-query-routing.md U12）：
    沒有雲端 → ctx.escalate 交給本機 Slow Path；有雲端、沒有搜尋金鑰 → 開搜尋頁並記 step["web_fallback"]（U13）。
    搜尋失敗、雲端失敗、摘錄裡沒有答案都退回開搜尋頁（⚠️）。摘錄只活在這個函式裡：不寫紀錄、不進對話記憶（Brave 條款）。
    原句有時間性時只搜近期網頁（search_client.freshness_for）；近期沒有結果就不過濾再搜一次。
    各階段記在 ctx.step["web"]：search_ms、results、freshness、fresh_retry、answer_ms、cloud（雲端呼叫的 meta）、error（分類）"""
    if cloud_client.settings() is None:  # 沒有雲端（Fast Path 直接送來的才會遇到）：交給本機 AI，與沒有雲端時相同（U13）
        if ctx is None:
            return "⚠️ 沒有雲端，沒辦法直接回答"
        ctx.escalate = {"kind": "slow"}
        return "🔎 沒有雲端，交給本機 AI"
    info: dict = ctx.step.setdefault("web", {}) if ctx is not None and ctx.step is not None else {}
    if not search_client.available():  # 有雲端、沒有搜尋金鑰：不讓模型憑記憶回答，開搜尋頁並在後端提醒（U13）
        info["error"] = "no_search_key"
        if ctx is not None and ctx.step is not None:
            ctx.step["web_fallback"] = "no_search_key"
        print("⚠️ [沒有搜尋金鑰（BRAVE_API_KEY），已退回開搜尋頁]")
        return _web_fallback(query, ctx, "no_search_key")
    utterance = (ctx.utterance if ctx is not None else "") or query
    fresh = search_client.freshness_for(utterance)
    if fresh:
        info["freshness"] = fresh
    t = time.perf_counter()
    try:
        try:
            results = search_client.search(query, freshness=fresh)
        except search_client.SearchError as e:
            if not (fresh and e.kind == "no_results"):
                raise
            info["fresh_retry"] = True
            results = search_client.search(query)
    except search_client.SearchError as e:
        info.update(search_ms=(time.perf_counter() - t) * 1000, error=f"search:{e.kind}")
        return _web_fallback(query, ctx, "search")
    info.update(search_ms=(time.perf_counter() - t) * 1000, results=len(results))
    today = today_taipei()
    meta: dict = {}
    t = time.perf_counter()
    try:
        message = cloud_client.chat(cloud_client.web_answer_messages(utterance, results, today), tools=None,
                                    timeout=config.WEB_ANSWER_TIMEOUT_S, max_tokens=config.WEB_ANSWER_MAX_TOKENS, meta=meta)
    except cloud_client.CloudError as e:
        info.update(error=f"cloud:{e}")
        return _web_fallback(query, ctx, "cloud")
    finally:
        info.update(answer_ms=(time.perf_counter() - t) * 1000, cloud=meta)
    answer = message.get("content") or ""
    if meta.get("finish_reason") == "length":  # 碰到上限：內容可能只有半句，不當成答案
        info["error"] = "truncated"
        return _web_fallback(query, ctx, "truncated")
    if cloud_client.is_no_answer(answer):
        info["error"] = "no_answer"
        return _web_fallback(query, ctx, "no_answer")
    sources.STORE.put(results[0].url)
    if ctx is not None:
        ctx.replies.append(f"{answer} {sources.FOLLOWUP}")
        ctx.ai_reply = True
    return f"✅ {answer}"


def open_source(url: str, ctx: Optional[ExecContext] = None) -> str:
    """「要打開來源嗎？」說好：開 web_answer 記下的那筆來源。網址來自搜尋服務、不是模型編的，仍照紅線 5 只收 http／https"""
    if not url.startswith(("http://", "https://")):
        return "❌ 來源網址不合法，沒有開啟"
    browser = (ctx.last_browser or front_app(ctx, "browser")) if ctx is not None else None
    res = web_search_or_open_url(url, browser=browser)
    if ctx is not None:
        mark_page(ctx, res)
    host = urllib.parse.urlsplit(url).hostname or ""
    return f"❌ 開啟來源失敗（{host}）" if res.startswith(_FAIL_PREFIXES) else f"✅ 已打開來源：{host}"


LOCAL_TOOLS.update({
    "open_folder": open_folder, "find_file": find_file, "pick_choice": pick_choice,
    "context_search": context_search, "front_action": front_action, "play_media": play_media,
    "music_replace": music_replace, "music_feedback": music_feedback, "music_top": music_top, "get_weather": get_weather,
    "web_answer": web_answer, "open_source": open_source, "federated_search": federated_search,
})
