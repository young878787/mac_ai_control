"""第二層：依 Intent 執行本機動作（資料夾、檔案搜尋、選擇清單）。只讀 Intent.slots，不看原句。

檔案搜尋用 Spotlight（mdfind），只搜家目錄且排除 ~/Library；預設只在 Finder 中顯示，
使用者明說『打開』才開檔，而且可執行檔一律只顯示。這裡的工具只放在 LOCAL_TOOLS，
不在 LLM 的 TOOLS_SCHEMA 裡，所以 LLM 叫不到它們。"""
import os
import re
import time
import urllib.parse
from typing import Callable, Dict, List, Optional, Tuple

from mac_ai.config import FILE_SEARCH_TIMEOUT_S, FIND_RESULTS_MAX
from mac_ai.router import choices, context, native
from mac_ai.router.actions import (
    LOCAL_TOOLS, POLL_TIMEOUT_S, ExecContext, _dispatch, _skipped, front_context, front_site, mark_page, _run,
)
from mac_ai.router.domain import Intent
from mac_ai.router.lexicon import LEXICON

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
    count = len(lst.paths)
    if n == -1:
        n = count
    if not 1 <= n <= count:
        return f"⚠️ 清單只有 {count} 筆"
    ok, msg = _act(lst.paths[n - 1], action or lst.default_action)
    if ok:
        choices.STORE.clear()
        if ctx is not None and ctx.step is not None:
            ctx.step["choices"] = {"list_id": lst.list_id, "closed": True}
    return msg


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
    if "script" in b:  # App 自己的指令（Music、Spotify 的 playpause）；指令字只來自詞庫
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


def front_action(id: str, fallback: Optional[List[list]] = None, ctx: Optional[ExecContext] = None) -> str:
    """對最前面的 App 執行詞庫 app_shortcuts 定義的常用功能（底層做法見 _run_binding）。這個功能不支援目前的 App 時：
    有 fallback（加上前景判斷之前的原動作，例如『開終端機』→ 開 Terminal.app）就執行它，否則回報、什麼都不做"""
    ctx = ctx or ExecContext()
    entry = context.SHORTCUTS[id]
    front = front_context(ctx)
    site = None
    if front is not None and front.kind == "browser" and context.needs_site(entry):
        hit = front_site(ctx)
        site = hit["name"] if hit else None
    binding = context.pick_binding(entry, front, site)
    if binding is None:
        if ctx.step is not None:
            ctx.step["context"]["used"] = False
        if fallback:
            return "；".join(_dispatch(name, dict(args), ctx) for name, args in fallback)
        if front is None:
            return f"⚠️ 不知道要操作哪個視窗（讀不到最前面的 App），沒有執行「{entry['label']}」"
        return f"⚠️ 目前最前面的是 {front.app}，「{entry['label']}」只支援{entry['scope']}"
    status, method = _run_binding(front, binding)
    if ctx.step is not None:
        ctx.step["context"]["method"] = method
    if status == "ok":
        return f"✅ {front.app}：{entry['label']}"
    if status == "disabled":
        return f"⚠️ {front.app} 目前不能「{entry['label']}」（選單項目停用）"
    if status == "missing":
        return f"❌ {entry['label']}失敗：{front.app} 找不到可操作的{method}"
    if status == "front_changed":
        return f"❌ 最前面的 App 已經不是 {front.app}，沒有執行「{entry['label']}」"
    return f"❌ {entry['label']}失敗：{front.app}（{method}；請確認已授權輔助使用）"


LOCAL_TOOLS.update({
    "open_folder": open_folder, "find_file": find_file, "pick_choice": pick_choice,
    "context_search": context_search, "front_action": front_action,
})
