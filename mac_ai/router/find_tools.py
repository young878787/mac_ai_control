"""找東西：資料夾、檔案搜尋（find_file）、選擇清單（pick_choice）、聯合搜尋（federated_search）。

檔案搜尋用 Spotlight（mdfind），只搜家目錄且排除 ~/Library；預設只在 Finder 中顯示，
使用者明說『打開』才開檔，而且可執行檔一律只顯示。這裡的工具只放在 LOCAL_TOOLS（由 local_actions 登錄），
不在 LLM 的 TOOLS_SCHEMA 裡，所以 LLM 叫不到它們。"""
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from mac_ai import config
from mac_ai.config import FILE_SEARCH_TIMEOUT_S, FIND_RESULTS_MAX
from mac_ai.router import choices, prefs_db, search_client, search_prefs
from mac_ai.router.actions import _dispatch
from mac_ai.router.browser_tools import google_search_url
from mac_ai.router.exec_context import EXTERNAL_TIMEOUT_S, _FAIL_PREFIXES, ExecContext, front_app, front_site, mark_page
from mac_ai.router.front_tools import context_search
from mac_ai.router.lexicon import LEXICON
from mac_ai.router.prefs_db import _db_call
from mac_ai.router.proc import _run

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


# ---------- 資料夾 ----------
def _folder_path(key: str) -> Optional[str]:
    f = _FOLDERS.get(key)
    return os.path.expanduser(f["path"]) if f else None


def open_folder(path_key: str, ctx: Optional[ExecContext] = None) -> str:
    path = _folder_path(path_key)
    if not path or not os.path.isdir(path):
        return f"❌ 資料夾不存在：{path or path_key}"
    ok, err = _run(["open", path], EXTERNAL_TIMEOUT_S)
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
        ok, err = _run(["open", path], EXTERNAL_TIMEOUT_S)
        return ok, f"✅ 已開啟：{name}（{where}）" if ok else f"❌ 開啟失敗：{name}（{err}）"
    ok, err = _run(["open", "-R", path], EXTERNAL_TIMEOUT_S)
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


# ---------- 聯合搜尋（沒說範圍的找／搜尋，計畫 docs/plans/completed/2026-11-23-query-routing.md §4.3）----------
def record_search(query: str, side: str, signal: str, ctx: Optional[ExecContext] = None, path: Optional[str] = None) -> None:
    """記一筆常搜名詞（search_prefs）。資料庫還沒準備好就不記；失敗只記例外類別，不影響指令"""
    conn = prefs_db.MUSIC_DB.conn()
    if conn is None:
        return
    info = ctx.step.setdefault("search_prefs", {}) if ctx is not None and ctx.step is not None else {}
    _db_call(info, search_prefs.record, conn, query, side, signal, prefs_db._now(), path=path)


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
    ok, err = _run(["open", "-a", browser, item.url] if browser else ["open", item.url], EXTERNAL_TIMEOUT_S)
    res = "✅ 已用瀏覽器打開網路結果" if ok else f"❌ 開啟瀏覽器失敗（{err}）"
    if ctx is not None:
        mark_page(ctx, res)
    return res


def federated_search(query: str, ctx: Optional[ExecContext] = None) -> str:
    """沒說範圍的『找周杰倫』『搜尋履歷』：本機（mdfind）與網路（Brave；沒有金鑰就是一筆 Google 搜尋）並行，合併成一份清單（U1、U5、U6、U9）。
    前景瀏覽器停在詞庫網站、或同句剛開了瀏覽器 → 照舊站內搜尋（context_search）。
    統計（search_prefs.lean）：U8 成立時只查偏好的那一邊，偏本機而且記錄的路徑還在 → 直接顯示那個路徑；清單依偏好決定先列哪一邊。
    只有一邊有結果而且只有 1 筆 → 直接執行；本機 1 筆而網路只剩 Google 搜尋（沒有金鑰或搜尋失敗）也直接顯示。各階段記在 ctx.step["federated"]（筆數、毫秒、lean；不記標題與網址）"""
    ctx = ctx or ExecContext()
    if ctx.last_browser or front_site(ctx) is not None:
        return context_search(query, ctx)
    info: dict = ctx.step.setdefault("federated", {}) if ctx.step is not None else {}
    _close_previous(ctx)
    conn = prefs_db.MUSIC_DB.conn()
    lean = (_db_call(info, search_prefs.lean, conn, query, prefs_db._now()) if conn is not None else None) or search_prefs.Lean()
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
    if len(local_items) == 1 and web_error:  # 本機 1 筆、網路只剩 Google 搜尋：直接顯示（同以前的找檔案，使用者 2026-11-23 決定）
        return _act(local_items[0], "reveal")[1]
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
