"""工具的分派：AVAILABLE_FUNCTIONS（TOOLS_SCHEMA 的工具）、LOCAL_TOOLS（本機工具，由 local_actions 登錄）、
_dispatch（維護 ExecContext：記住剛開的瀏覽器／編輯器、剛開的網頁；前置步驟失敗時略過）與 run_tool。"""
import time
from typing import Callable, Dict

from mac_ai.router.browser_tools import browser_tab, web_search_or_open_url
from mac_ai.router.chat_tools import chat_reply
from mac_ai.router.exec_context import _FAIL_PREFIXES, ExecContext, _skipped, front_app, mark_page
from mac_ai.router.lexicon import APP_KIND, MAC_APP_ALIASES
from mac_ai.router.screen_tools import read_screen
from mac_ai.router.system_tools import (
    adjust_screen_brightness, control_mac_app, open_project, set_system_volume, system_power_action,
)


AVAILABLE_FUNCTIONS = {
    "control_mac_app": control_mac_app,
    "set_system_volume": set_system_volume,
    "adjust_screen_brightness": adjust_screen_brightness,
    "web_search_or_open_url": web_search_or_open_url,
    "browser_tab": browser_tab,
    "system_power_action": system_power_action,
    "open_project": open_project,
    "read_screen": read_screen,
    "chat_reply": chat_reply,
}


# 本機範圍的內部工具（find_file 等）由 local_actions 在載入時登錄；不在 TOOLS_SCHEMA，所以 LLM 叫不到
LOCAL_TOOLS: Dict[str, Callable[..., str]] = {}


def _dispatch(name: str, args: dict, ctx: ExecContext) -> str:
    """執行單一工具，並維護 ExecContext：記住剛開的瀏覽器／編輯器、剛開的網頁；前置步驟失敗時略過依賴它的步驟"""
    if name == "control_mac_app":
        res = control_mac_app(**args)
        target = MAC_APP_ALIASES.get(args["app_name"].strip().lower(), args["app_name"])
        kind = APP_KIND.get(target, "other")
        if args.get("action", "open") == "open" and kind in ("browser", "editor"):
            if res.startswith(_FAIL_PREFIXES):
                ctx.failed.add(kind)
            else:
                ctx.failed.discard(kind)
                ctx.front_loaded = False  # 前景換了，下次用到重讀
                if kind == "browser":
                    ctx.last_browser = target
                else:
                    ctx.last_editor = target
        return res
    if name == "web_search_or_open_url":
        if "browser" in ctx.failed:
            return _skipped(name)
        res = web_search_or_open_url(**args, browser=ctx.last_browser or front_app(ctx, "browser"))
        mark_page(ctx, res)
        return res
    if name == "browser_tab":
        if "browser" in ctx.failed:
            return _skipped(name)
        return browser_tab(args["action"], ctx.last_browser)
    if name == "open_project":
        if "editor" in ctx.failed:
            return _skipped(name)
        return open_project(args["project_name"], args.get("editor") or ctx.last_editor or front_app(ctx, "editor"))
    if name == "read_screen":
        if ctx.failed & {"browser", "page"}:
            return _skipped(name)
        return read_screen(ctx)
    if name == "chat_reply":
        return chat_reply(ctx)
    if name in LOCAL_TOOLS:
        return LOCAL_TOOLS[name](ctx=ctx, **args)
    return AVAILABLE_FUNCTIONS[name](**args)


def run_tool(name: str, args: dict, ctx: ExecContext) -> str:
    """執行一個工具並記錄耗時（ctx.step["exec_ms"]）與結果（ctx.results）"""
    t = time.perf_counter()
    res = _dispatch(name, dict(args), ctx)
    if ctx.step is not None:
        ctx.step.setdefault("exec_ms", []).append((time.perf_counter() - t) * 1000)
    ctx.results.append(res)
    ctx.calls.append((name, dict(args), not res.startswith(_FAIL_PREFIXES)))
    return res
