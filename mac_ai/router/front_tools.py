"""前景視窗的工具：站內搜尋（context_search）與常用功能（front_action，詞庫 app_shortcuts）。
播放控制在前景不支援時改控制背景的音樂（music_tools）。"""
import urllib.parse
from typing import List, Optional, Tuple

from mac_ai.router import context, native
from mac_ai.router.actions import _dispatch
from mac_ai.router.exec_context import EXTERNAL_TIMEOUT_S, POLL_TIMEOUT_S, ExecContext, _skipped, front_context, front_site, mark_page
from mac_ai.router.music_tools import _background_control, _control_op, _record_control
from mac_ai.router.proc import _run


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
            ok, err = _run(["open", "-a", ctx.front.app, url], EXTERNAL_TIMEOUT_S)
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
