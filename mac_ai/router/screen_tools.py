"""讀畫面（read_screen）：等網頁載入 → 截圖 → 縮圖 → 本機 VL 或雲端辨識。"""
import base64
import json
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

from mac_ai import config
from mac_ai.config import CLOUD_IMAGE_MAX_BYTES, CLOUD_VISION_TIMEOUT_S, VISION_TIMEOUT_S
from mac_ai.router import cloud_client
from mac_ai.router.browser_tools import TAB_BROWSERS
from mac_ai.router.exec_context import EXTERNAL_TIMEOUT_S, POLL_TIMEOUT_S, ExecContext
from mac_ai.router.llm_client import LLMError, LLMUnavailable, _llm
from mac_ai.router.normalize import to_traditional
from mac_ai.router.proc import _run


PAGE_SETTLE_S = 1.0  # 開網頁後至少等這麼久才開始問「載入完了嗎」（避免讀到舊頁面的 loading=false）
PAGE_LOAD_MAX_S = 8.0
PAGE_FIXED_WAIT_S = 2.5  # 非 Chrome 無法查載入狀態，固定等待
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
# 條列指示放在使用者訊息：實測只放 system 時 VL 模型不再條列、漏掉數字
SCREEN_QUESTION = "這個畫面在顯示什麼？用繁體中文條列重點，最多 5 點。"
SCREEN_SYSTEM_PROMPT = (
    "# 角色\n"
    "螢幕截圖辨識助手。\n"
    "\n"
    "# 規則\n"
    "- 只描述畫面上看得到的內容，不推測、不編造。"
)


def _wait_for_page(ctx: ExecContext) -> None:
    pending = ctx.page_pending
    if not pending:
        return
    elapsed = time.monotonic() - pending["t"]
    # 以下 sleep 是例外（no-sleep-in-router）：同句剛開網頁、接著要讀畫面時才會等，而且有上限（PAGE_SETTLE_S＋PAGE_LOAD_MAX_S 或 PAGE_FIXED_WAIT_S）
    if ctx.last_browser == "Google Chrome":
        if PAGE_SETTLE_S - elapsed > 0:
            # ast-grep-ignore: no-sleep-in-router
            time.sleep(PAGE_SETTLE_S - elapsed)
        deadline = time.monotonic() + PAGE_LOAD_MAX_S
        while time.monotonic() < deadline:
            ok, out = _run(["osascript", "-e", 'tell application "Google Chrome" to return loading of active tab of front window'], POLL_TIMEOUT_S)
            if not ok or out.lower() != "true":
                break
            # ast-grep-ignore: no-sleep-in-router
            time.sleep(0.3)
    elif PAGE_FIXED_WAIT_S - elapsed > 0:
        # ast-grep-ignore: no-sleep-in-router
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
        if step.get("route") == "cloud" and config.CLOUD_VISION_ENABLED and cloud_client.available():
            res = _cloud_read_screen(ctx, step, small, tmp, target)
            if res is not None:
                return res
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


def _cloud_read_screen(ctx: ExecContext, step: dict, small: Path, tmp: Path, target: str) -> Optional[str]:
    """雲端看畫面（計畫 §6.4）：縮好的 PNG 轉 JPEG（sips 不能寫 WebP，實測）送雲端，問題用使用者原句。
    太大或雲端失敗回傳 None，呼叫端改用本機 VL。只記大小與耗時，不記圖片"""
    jpg = tmp / "screen.jpg"
    for quality in (80, 60):
        ok, _ = _run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality), str(small), "--out", str(jpg)], EXTERNAL_TIMEOUT_S)
        size = jpg.stat().st_size if ok and jpg.exists() else 0
        step["image"] = {"bytes": size, "quality": quality}
        if 0 < size <= CLOUD_IMAGE_MAX_BYTES:
            break
    else:
        return None
    meta: dict = {}
    messages = [
        {"role": "system", "content": cloud_client.CLOUD_VISION_SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode()}},
            {"type": "text", "text": ctx.utterance or SCREEN_QUESTION},
        ]},
    ]
    try:
        message = cloud_client.chat(messages, timeout=CLOUD_VISION_TIMEOUT_S, meta=meta)
    except cloud_client.CloudError as e:
        print(f"☁️ 雲端看畫面失敗（{e}），改用本機")
        return None
    finally:
        step["vision_cloud"] = meta
    text = message.get("content") or ""
    if not text:
        return None
    ctx.replies.append(text)
    return f"✅ 畫面內容（{target}，雲端）：{text}"
