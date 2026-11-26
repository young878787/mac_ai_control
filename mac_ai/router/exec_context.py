"""執行基礎：同一句共用的 ExecContext、前景的讀取（用到才讀、同句快取）、開網頁後的標記、外部指令的逾時。
所有工具模組都依賴這裡；這裡不引用任何工具。"""
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from mac_ai.router import context
from mac_ai.router.proc import _run


_FAIL_PREFIXES = ("❌", "⚠️", "⏭️")
EXTERNAL_TIMEOUT_S = 15.0  # 一般外部指令（open、osascript、pmset、screencapture、sips）的逾時上限；正常 1 秒內結束，卡住（如等授權對話框）時回失敗而不是一直擋住 command_worker
POLL_TIMEOUT_S = 5.0  # 查 Chrome 載入狀態的 osascript 逾時（第一次可能跳出自動化授權）


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
    front: Optional[context.FrontContext] = None  # 前景 App（第一次用到才讀，同句快取；見 front_context）
    front_loaded: bool = False
    front_host: Optional[str] = None  # 前景瀏覽器目前分頁的 host（None＝還沒讀；見 front_site）
    calls: List[Tuple[str, dict, bool]] = field(default_factory=list)  # 這一句執行過的工具 (名稱, 參數, 成功)；句尾寫進對話記憶
    ai_reply: bool = False  # 這一句採用了模型寫的回覆（本機 chat call 或雲端文字）；聊天室主動展開的依據（見 pipeline.expand_reason）


def front_context(ctx: ExecContext, used: bool = True) -> Optional[context.FrontContext]:
    """最前面的 App（讀不到為 None）。對象的優先順序由呼叫端決定：句子明說的 → 同句剛開的 → 前景 → 預設，
    所以只在前兩者都沒有時才呼叫。結果記在 step["context"]（只記 App 與 host，不記網址、視窗標題）"""
    log: dict = {}
    if not ctx.front_loaded:
        t = time.perf_counter()
        ctx.front, ctx.front_loaded, ctx.front_host = context.read_front(_run), True, None
        log["ms"] = (time.perf_counter() - t) * 1000
    if ctx.step is not None:  # 同一步多次查詢：保留已記下的讀取時間與網站
        prev = {k: v for k, v in ctx.step.get("context", {}).items() if k not in ("app", "used")}
        ctx.step["context"] = {"app": ctx.front.app if ctx.front else None, "used": used, **prev, **log}
    return ctx.front


def front_app(ctx: ExecContext, kind: str) -> Optional[str]:
    """前景 App 是這一類（browser／editor）就回傳名稱，否則 None"""
    front = front_context(ctx, used=False)
    if front is None or front.kind != kind:
        return None
    if ctx.step is not None:
        ctx.step["context"]["used"] = True
    return front.app


def front_site(ctx: ExecContext) -> Optional[dict]:
    """前景是瀏覽器而且目前分頁是詞庫 sites 裡的網站 → 該條目；其餘 None。host 同句快取"""
    app = front_app(ctx, "browser")
    if app is None:
        return None
    if ctx.front_host is None:
        t = time.perf_counter()
        ctx.front_host = context.read_host(_run, app)
        if ctx.step is not None:
            ctx.step["context"]["host_ms"] = (time.perf_counter() - t) * 1000
    site = context.site_for(ctx.front_host) if ctx.front_host else None
    if ctx.step is not None:
        ctx.step["context"].update({"host": ctx.front_host, "site": site["name"] if site else None})
    return site


def mark_page(ctx: ExecContext, res: str) -> None:
    """開網頁之後：失敗就記下（依賴它的讀畫面要略過），成功就記下要等載入"""
    if res.startswith(_FAIL_PREFIXES):
        ctx.failed.add("page")
    else:
        ctx.failed.discard("page")
        ctx.page_pending = {"t": time.monotonic()}


def _skipped(name: str) -> str:
    return f"⏭️ 略過 {name}：前一步失敗"
