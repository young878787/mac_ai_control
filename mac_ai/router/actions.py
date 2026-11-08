"""macOS 底層控制執行器、執行上下文、讀畫面與 chat call。"""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from mac_ai.config import CHAT_TIMEOUT_S, VISION_TIMEOUT_S
from mac_ai.router.lexicon import APP_KIND, MAC_APP_ALIASES, project_index, resolve_project
from mac_ai.router.llm_client import LLMError, LLMUnavailable, _llm
from mac_ai.router.normalize import to_traditional


_FAIL_PREFIXES = ("❌", "⚠️", "⏭️")


def _run(cmd: List[str], timeout: Optional[float] = None) -> Tuple[bool, str]:
    """執行外部指令並檢查結束碼。回傳 (成功?, 成功時的 stdout／失敗時 stderr 最後一行)；
    timeout 用於可能卡在授權對話框的指令（逾時視為失敗）"""
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"逾時 {timeout:g}s（可能在等待授權對話框）"
    except OSError as e:  # 指令不存在或無法執行
        return False, str(e)[:120]
    if p.returncode == 0:
        return True, (p.stdout or "").strip()
    lines = (p.stderr or "").strip().splitlines()
    return False, (lines[-1][:120] if lines else f"結束碼 {p.returncode}")


def control_mac_app(app_name: str, action: str = "open") -> str:
    """透過 open -a 或 osascript 開關 macOS 應用程式"""
    normalized_key = app_name.strip().lower()
    target_app = MAC_APP_ALIASES.get(normalized_key, app_name)

    if action == "open":
        ok, err = _run(["open", "-a", target_app])
        return f"✅ 已開啟應用程式：{target_app}" if ok else f"❌ 開啟失敗：{target_app}（{err}）"
    elif action == "quit":
        script = f'tell application "{target_app}" to quit'
        ok, err = _run(["osascript", "-e", script])
        return f"✅ 已關閉應用程式：{target_app}" if ok else f"❌ 關閉失敗：{target_app}（{err}）"
    return f"⚠️ 未知的動作：{action}"


def set_system_volume(level: Optional[int] = None, mute: Optional[bool] = None) -> str:
    """透過 AppleScript 控制 macOS 系統音量"""
    results = []
    if level is not None:
        clamped_level = max(0, min(100, int(level)))
        ok, err = _run(["osascript", "-e", f"set volume output volume {clamped_level}"])
        if not ok:
            return f"❌ 設定音量失敗（{err}）"
        results.append(f"音量設為 {clamped_level}%")
    if mute is not None:
        mute_str = "true" if mute else "false"
        ok, err = _run(["osascript", "-e", f"set volume output muted {mute_str}"])
        if not ok:
            return f"❌ 設定靜音失敗（{err}）"
        results.append("已靜音" if mute else "已取消靜音")
    return "✅ " + "、".join(results)


def adjust_screen_brightness(direction: str, steps: int = 3) -> str:
    """透過模擬 macOS 亮度鍵 (Key Code 144=調亮, 145=調暗) 調整螢幕亮度"""
    key_code = 144 if direction == "up" else 145
    steps = max(1, min(16, int(steps)))
    script = f'tell application "System Events" to repeat {steps} times\nkey code {key_code}\ndelay 0.05\nend repeat'
    ok, err = _run(["osascript", "-e", script])
    action_text = "調亮" if direction == "up" else "調暗"
    return f"✅ 已將螢幕{action_text} {steps} 格" if ok else f"❌ 調整亮度失敗（{err}）"


def web_search_or_open_url(query: str, browser: Optional[str] = None) -> str:
    """使用瀏覽器開啟網址或進行 Google 搜尋；browser 由執行上下文帶入（剛開啟的瀏覽器），未指定則用預設瀏覽器"""
    if query.startswith("http://") or query.startswith("https://"):
        url = query
    else:
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    ok, err = _run(["open", "-a", browser, url] if browser else ["open", url])
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
        return f"❌ 找不到可操作的瀏覽器（支援 Safari、Google Chrome，請先開啟）" + (f"（{err}）" if err else "")
    ok, err = _run(["osascript", "-e", _TAB_SCRIPTS[target][action]], EXTERNAL_TIMEOUT_S)
    return f"✅ {target}：{_TAB_DONE_TEXT[action]}" if ok else f"❌ 分頁操作失敗：{target}（{err}）"


def system_power_action(action: str) -> str:
    """控制深色模式、睡眠或鎖定螢幕"""
    if action == "toggle_dark_mode":
        script = 'tell application "System Events" to tell appearance preferences to set dark mode to not dark mode'
        ok, err = _run(["osascript", "-e", script])
        return "✅ 已切換深色/淺色模式" if ok else f"❌ 切換深色模式失敗（{err}）"
    elif action in ("dark_mode_on", "dark_mode_off"):
        on = action == "dark_mode_on"
        script = f'tell application "System Events" to tell appearance preferences to set dark mode to {"true" if on else "false"}'
        ok, err = _run(["osascript", "-e", script])
        if not ok:
            return f"❌ 設定深色模式失敗（{err}）"
        return "✅ 已開啟深色模式" if on else "✅ 已關閉深色模式（淺色）"
    elif action == "sleep":
        ok, err = _run(["pmset", "sleepnow"])
        return "✅ 系統進入睡眠" if ok else f"❌ 睡眠失敗（{err}）"
    elif action == "lock_screen":
        script = 'tell application "System Events" to keystroke "q" using {control down, command down}'
        ok, err = _run(["osascript", "-e", script])
        return "✅ 已鎖定螢幕" if ok else f"❌ 鎖定螢幕失敗（{err}）"
    return f"⚠️ 不支援的系統動作：{action}"


def open_project(project_name: str, editor: Optional[str] = None) -> str:
    """用編輯器開啟白名單內的專案資料夾（不接受任意路徑）；editor 預設 Visual Studio Code"""
    hit = resolve_project(project_name)
    if hit is None:
        known = "、".join(sorted({name for name, _ in project_index().values()})[:10]) or "（無）"
        return f"❌ 找不到專案：{project_name}（已知專案：{known}）"
    (name, path), _, _ = hit
    if not os.path.isdir(path):
        return f"❌ 專案路徑不存在：{path}"
    target = editor or "Visual Studio Code"
    ok, err = _run(["open", "-a", target, path])
    return f"✅ 已用 {target} 開啟專案：{name}" if ok else f"❌ 開啟專案失敗：{name}（{err}）"


# ---------- 執行上下文與讀畫面 ----------
@dataclass
class ExecContext:
    """同一次 process_voice_command 內共用的狀態（不跨語句）"""
    last_editor: Optional[str] = None
    last_browser: Optional[str] = None
    page_pending: Optional[dict] = None  # {"url", "t"}：剛開的網頁，讀畫面前要先等載入
    failed: Set[str] = field(default_factory=set)  # browser／editor／page：這幾類前置步驟失敗過
    replies: List[str] = field(default_factory=list)
    # 以下為「目前這一步」的狀態，由 router 在每步開始時重設
    step: Optional[dict] = None
    utterance: str = ""
    results: List[str] = field(default_factory=list)
    status: Optional[str] = None
    chat_done: bool = False
    escalate: Optional[dict] = None  # 這一步的本機搜尋 0 筆、要轉交模型判斷：{"label", "scope"}（由 find_file 設定）
    chat_hint: Optional[str] = None  # 轉交時給 chat call 的本機結果，避免模型假裝找到
    misheard: List[Tuple[str, str]] = field(default_factory=list)  # 整句的誤聽詞替換紀錄 (替換後, 原字)，還原原話查詢用
    normalized: str = ""  # 正規化後的整句（還原誤聽詞時要在裡面定位）


PAGE_SETTLE_S = 1.0  # 開網頁後至少等這麼久才開始問「載入完了嗎」（避免讀到舊頁面的 loading=false）
PAGE_LOAD_MAX_S = 8.0
PAGE_FIXED_WAIT_S = 2.5  # 非 Chrome 無法查載入狀態，固定等待
EXTERNAL_TIMEOUT_S = 15.0  # screencapture／sips 逾時
POLL_TIMEOUT_S = 5.0  # 查 Chrome 載入狀態的 osascript 逾時（第一次可能跳出自動化授權）
SCREEN_MAX_PX = 1280
# 說到「頁面／網頁」就只截前景瀏覽器的視窗（被其他視窗擋住也截得到）；其餘（螢幕、截圖、畫面）截整個主螢幕
_PAGE_WORDS_RE = re.compile(r"頁|網站")
# CGWindowList 由前到後排序：第一個符合的就是最前面的瀏覽器視窗（layer 0＝一般視窗，排除選單列、浮動面板）
_FRONT_BROWSER_WINDOW_SCRIPT = (
    'ObjC.import("CoreGraphics");'
    "const owners = __OWNERS__;"
    "const wins = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly, 0)));"
    "const w = wins.find(w => w.kCGWindowLayer === 0 && owners.includes(w.kCGWindowOwnerName)"
    " && w.kCGWindowBounds.Width > 300 && w.kCGWindowBounds.Height > 200);"
    'w ? [w.kCGWindowNumber, w.kCGWindowOwnerName, (w.kCGWindowName || "").replace(/\\s+/g, " ")].join("\\t") : ""'
)
SCREEN_QUESTION = "這個畫面在顯示什麼？用繁體中文條列重點。"
SCREEN_SYSTEM_PROMPT = "你是螢幕內容辨識助手。用繁體中文條列重點，最多 5 點，只描述畫面上看得到的內容，不要編造。"
CHAT_SYSTEM_PROMPT = (
    "你是運行在 macOS 上的本地語音助手。用繁體中文簡短回答（兩句以內）。"
    "句子中的電腦操作（開關程式、音量、亮度、搜尋等）已由其他程式處理，不要描述或重複，只回答提問或閒聊的部分。"
)


def _wait_for_page(ctx: ExecContext) -> None:
    pending = ctx.page_pending
    if not pending:
        return
    elapsed = time.monotonic() - pending["t"]
    if ctx.last_browser == "Google Chrome":
        if PAGE_SETTLE_S - elapsed > 0:
            time.sleep(PAGE_SETTLE_S - elapsed)
        deadline = time.monotonic() + PAGE_LOAD_MAX_S
        while time.monotonic() < deadline:
            ok, out = _run(["osascript", "-e", 'tell application "Google Chrome" to return loading of active tab of front window'], POLL_TIMEOUT_S)
            if not ok or out.lower() != "true":
                break
            time.sleep(0.3)
    elif PAGE_FIXED_WAIT_S - elapsed > 0:
        time.sleep(PAGE_FIXED_WAIT_S - elapsed)
    ctx.page_pending = None


def _front_browser_window(browser: Optional[str]) -> Optional[Tuple[str, str, str]]:
    """最前面的瀏覽器視窗 → (視窗編號, 瀏覽器, 標題)；browser 是這次指令剛開的瀏覽器，有的話只找它。
    沒有瀏覽器視窗（沒開、最小化、在別的桌面）或查詢失敗回傳 None"""
    owners = [browser] if browser in TAB_BROWSERS else list(TAB_BROWSERS)
    script = _FRONT_BROWSER_WINDOW_SCRIPT.replace("__OWNERS__", json.dumps(owners))
    ok, out = _run(["osascript", "-l", "JavaScript", "-e", script], POLL_TIMEOUT_S)
    parts = out.split("\t") if ok else []
    if len(parts) < 2 or not parts[0].isdigit():
        return None
    return parts[0], parts[1], parts[2] if len(parts) > 2 else ""


def read_screen(ctx: Optional[ExecContext] = None) -> str:
    """截圖 → sips 縮到 1280 → 送 VL 模型辨識；辨識結果寫入 ctx.replies。暫存檔一律刪除。
    語句提到頁面／網頁時只截前景瀏覽器視窗（找不到或截失敗就退回整個主螢幕）；結果會註明截的是什麼"""
    ctx = ctx or ExecContext()
    step = ctx.step if ctx.step is not None else {}
    t = time.perf_counter()
    _wait_for_page(ctx)
    step["wait_ms"] = (time.perf_counter() - t) * 1000
    window = None
    if _PAGE_WORDS_RE.search(ctx.utterance):
        t = time.perf_counter()
        window = _front_browser_window(ctx.last_browser)
        step["window_ms"] = (time.perf_counter() - t) * 1000

    tmp = Path(tempfile.mkdtemp(prefix="macvoice_"))
    try:
        raw, small = tmp / "screen.png", tmp / "screen_small.png"
        t = time.perf_counter()
        target = "整個螢幕"
        ok = False
        if window:
            ok, err = _run(["screencapture", "-x", "-o", "-l", window[0], str(raw)], EXTERNAL_TIMEOUT_S)
            ok = ok and raw.exists() and raw.stat().st_size > 0
            if ok:
                target = f"{window[1]}視窗" + (f"「{window[2]}」" if window[2] else "")
        if not ok:
            ok, err = _run(["screencapture", "-x", "-m", str(raw)], EXTERNAL_TIMEOUT_S)
        step["capture_ms"] = (time.perf_counter() - t) * 1000
        step["capture_target"] = target
        if not ok or not raw.exists() or raw.stat().st_size == 0:
            return f"❌ 截圖失敗（{err or '沒有輸出檔'}；請確認已授權螢幕錄製）"
        t = time.perf_counter()
        ok, err = _run(["sips", "-Z", str(SCREEN_MAX_PX), str(raw), "--out", str(small)], EXTERNAL_TIMEOUT_S)
        step["resize_ms"] = (time.perf_counter() - t) * 1000
        if not ok or not small.exists():
            return f"❌ 縮圖失敗（{err}）"
        data_uri = "data:image/png;base64," + base64.b64encode(small.read_bytes()).decode()
        messages = [
            {"role": "system", "content": SCREEN_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": SCREEN_QUESTION},
            ]},
        ]
        meta: dict = {}
        t = time.perf_counter()
        try:
            message = _llm(messages, timeout=VISION_TIMEOUT_S, tools=False, max_tokens=256, meta=meta)
        except LLMError as e:
            ctx.status = "llm_unavailable" if isinstance(e, LLMUnavailable) else "llm_error"
            return f"❌ AI 無法辨識畫面（{e}）"
        finally:
            step["vl_ms"] = (time.perf_counter() - t) * 1000
            step["vl"] = meta
        text = to_traditional((message.get("content") or "").strip())
        if not text:
            return "❌ 畫面辨識沒有輸出內容"
        ctx.replies.append(text)
        return f"✅ 畫面內容（{target}）：{text}"
    except OSError as e:
        return f"❌ 讀取截圖失敗（{e}）"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _strip_think(text: str) -> str:
    """去掉模型洩漏的思考內容：只留最後一個 </think> 之後的文字（實測沒有開頭標籤也會洩漏）；沒結束的 <think> 整段丟掉"""
    return re.sub(r"<think>.*", "", text.rsplit("</think>", 1)[-1], flags=re.DOTALL).strip()


def chat_reply(ctx: Optional[ExecContext] = None) -> str:
    """chat call：把這一步的原句直接當成使用者訊息送入模型（不帶 tools），回覆寫入 ctx.replies。
    同一步只回覆一次；失敗只記錄，不拋例外"""
    ctx = ctx or ExecContext()
    if ctx.chat_done:
        return "✅ 已回覆"
    ctx.chat_done = True
    step = ctx.step if ctx.step is not None else {}
    meta: dict = {}
    t = time.perf_counter()
    try:
        message = _llm(
            [{"role": "system", "content": CHAT_SYSTEM_PROMPT + (f"\n{ctx.chat_hint}" if ctx.chat_hint else "")},
             {"role": "user", "content": ctx.utterance}],
            timeout=CHAT_TIMEOUT_S, tools=False, max_tokens=128, meta=meta,
        )
    except LLMError as e:
        ctx.status = "llm_unavailable" if isinstance(e, LLMUnavailable) else "llm_error"
        text = f"⚠️ AI 回覆失敗（{e}）"
        ctx.replies.append(text)
        return text
    finally:
        step["chat_ms"] = (time.perf_counter() - t) * 1000
        step["chat"] = meta
    reply = to_traditional(_strip_think(message.get("content") or "")) or "無法理解指令"
    ctx.replies.append(reply)
    return f"🤖 AI 回覆：{reply}"


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


def _skipped(name: str) -> str:
    return f"⏭️ 略過 {name}：前一步失敗"


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
                if kind == "browser":
                    ctx.last_browser = target
                else:
                    ctx.last_editor = target
        return res
    if name == "web_search_or_open_url":
        if "browser" in ctx.failed:
            return _skipped(name)
        res = web_search_or_open_url(**args, browser=ctx.last_browser)
        if res.startswith(_FAIL_PREFIXES):
            ctx.failed.add("page")
        else:
            ctx.failed.discard("page")
            ctx.page_pending = {"t": time.monotonic()}
        return res
    if name == "browser_tab":
        if "browser" in ctx.failed:
            return _skipped(name)
        return browser_tab(args["action"], ctx.last_browser)
    if name == "open_project":
        if "editor" in ctx.failed:
            return _skipped(name)
        return open_project(args["project_name"], args.get("editor") or ctx.last_editor)
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
    return res
