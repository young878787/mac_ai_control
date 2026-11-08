"""AI 軌道（Slow Path）：規劃工具呼叫、驗證、執行與暖機。"""
import json
import re
import time
from typing import List, Optional, Tuple

from mac_ai.config import PLAN_TIMEOUT_S, WARMUP_TIMEOUT_S
from mac_ai.router.actions import ExecContext, chat_reply, run_tool
from mac_ai.router.fast_path import _NARR_RE, _NEG_RE
from mac_ai.router.lexicon import build_hint_lines
from mac_ai.router.llm_client import LLMError, _llm, reset_llm_breaker
from mac_ai.router.tools import validate_tool_call


SYSTEM_PROMPT = (
    "你是運行在 macOS 上的本地語音控制助手。依使用者的繁體中文指令呼叫工具，可一次呼叫多個。"
    "若指令與任何工具無關，不要呼叫工具，直接簡短回覆。"
    "『打開瀏覽器查某事』只需呼叫 web_search_or_open_url，不要另外開啟瀏覽器。"
    "app_name 必須使用標準英文名稱。"
)


# 搜尋意圖：句子裡要有這些詞（動詞，或天氣、新聞這類 prompt 明確要求要搜尋的即時資訊），LLM 才能開搜尋頁。
# 實測：『沒有我說的是這網頁』這類糾正句，小模型會編出『台北天氣 2025-09-27』之類的 query 亂開搜尋頁
_SEARCH_INTENT_RE = re.compile(r"搜|查|找|google|谷歌|上網|天氣|下雨|氣溫|新聞|股價|股票|匯率", re.IGNORECASE)
_URL_RE = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:[/?#]\S*)?$", re.IGNORECASE)


def _search_intent_error(utterance: str, query: str) -> Optional[str]:
    """web_search_or_open_url 的語意守門：沒有搜尋意圖字，且 query 不是原句裡字面出現的網址，就拒絕"""
    if _SEARCH_INTENT_RE.search(utterance):
        return None
    q = query.strip().lower()
    if _URL_RE.match(q) and re.sub(r"^https?://", "", q).rstrip("/") in utterance.lower():
        return None
    return "web_search_or_open_url 被略過：這句話沒有搜尋的意思（要搜尋請說『搜尋』『查』『找』…）"


# 『要看畫面』的說法：LLM 沒呼叫 read_screen 時，chat call 看不到畫面卻會編內容（實測回過「畫面上寫『Hello…』」），所以直接讀畫面。
# 否定、敘述、提問（『什麼』除外，『寫什麼』本身就是看畫面的問法）不算，避免『網頁是什麼』『怎麼看螢幕亮度』被當成要讀畫面
_LOOK_AT_SCREEN_RE = re.compile(
    r"(?:看|讀|念|辨識|解讀|截).{0,6}(?:畫面|螢幕|網頁|頁面|圖片)|(?:畫面|螢幕|網頁|頁面|圖片)(?:上)?(?:在)?(?:寫|說|講|有|顯示)(?:了)?什麼")
_NOT_A_REQUEST_RE = re.compile(r"嗎|怎麼|為什麼|為何|如何|是不是|有沒有|哪|幾|多少|\?")


def _is_look_at_screen(text: str) -> bool:
    return bool(_LOOK_AT_SCREEN_RE.search(text)) and not any(p.search(text) for p in (_NEG_RE, _NARR_RE, _NOT_A_REQUEST_RE))


def plan_slow_path(utterance: str, meta: Optional[dict] = None) -> Tuple[List[Tuple[str, dict]], Optional[str], List[str]]:
    """送 LLM 規劃工具呼叫。回傳 (通過驗證的 [(工具, 參數)], LLM 純文字回覆, 被拒絕的原因)。
    meta 若有傳入，回填 llm（server timings）與 validate_ms；LLM 不可用時拋出 LLMError"""
    hints = build_hint_lines(utterance)
    system = SYSTEM_PROMPT + (f"\n已知詞庫：{'；'.join(hints)}" if hints else "")
    llm_meta: dict = {}
    message = _llm(
        [{"role": "system", "content": system}, {"role": "user", "content": utterance}],
        timeout=PLAN_TIMEOUT_S, meta=llm_meta,
    )

    t = time.perf_counter()
    calls: List[Tuple[str, dict]] = []
    rejected: List[str] = []
    for call in message.get("tool_calls") or []:
        func = call.get("function") if isinstance(call, dict) else None
        if not isinstance(func, dict):
            rejected.append("tool_call 格式錯誤（缺少 function 物件）")
            continue
        raw_args = func.get("arguments", {})
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            rejected.append(f"{func.get('name')} 參數不是合法 JSON")
            continue
        clean, err = validate_tool_call(func.get("name", ""), args)
        if not err and func["name"] == "web_search_or_open_url":
            err = _search_intent_error(utterance, clean["query"])
        if err:
            rejected.append(err)
        else:
            calls.append((func["name"], clean))
    if meta is not None:
        meta["llm"] = llm_meta
        meta["validate_ms"] = (time.perf_counter() - t) * 1000
    return calls, message.get("content"), rejected


def execute_slow_path_llm(utterance: str, timing: Optional[dict] = None, ctx: Optional[ExecContext] = None) -> List[str]:
    """將未命中的自然語言指令送入本地 LLM 進行多工具拆解與執行；
    若傳入 timing，會回填 llm_ms（規劃耗時）、llm（server timings）、validate_ms。
    LLM 不可用時拋出 LLMError，由呼叫端決定補救"""
    step = timing if timing is not None else {}
    ctx = ctx or ExecContext()
    ctx.utterance = utterance
    t0 = time.perf_counter()
    try:
        calls, reply, rejected = plan_slow_path(utterance, step)
    finally:
        step["llm_ms"] = (time.perf_counter() - t0) * 1000
    llm = step.get("llm") or {}
    if llm.get("prefill_ms") is not None and llm.get("decode_ms") is not None:
        llm["overhead_ms"] = max(0.0, step["llm_ms"] - llm["prefill_ms"] - llm["decode_ms"])

    logs = [f"⚠️ 已拒絕不合法的工具呼叫：{r}" for r in rejected]
    if not calls and not rejected:
        if _is_look_at_screen(utterance):
            return [f"[Slow Path - 補救] read_screen({{}}) -> {run_tool('read_screen', {}, ctx)}"]
        # 沒有工具可呼叫：規劃呼叫的 content 不採用，另外觸發 chat call
        return [chat_reply(ctx)]
    if rejected and not calls:
        ctx.status = "rejected"
    for name, args in calls:
        res = run_tool(name, args, ctx)
        logs.append(f"[Slow Path - LLM] {name}({args}) -> {res}")
    return logs


def warmup_llm() -> Optional[float]:
    """啟動時送一次極短請求，把首次載入／編譯成本吃掉；回傳耗時秒數。
    LLM 連不上時只印警告並回傳 None，不讓程式結束（Fast Path 照常可用）"""
    start = time.perf_counter()
    try:  # 帶上與正式請求相同的 system prompt，prefix cache 才會對到
        _llm([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "你好"}], timeout=WARMUP_TIMEOUT_S)
    except LLMError as e:
        print(f"⚠️ LLM 暖機失敗：{e}（Fast Path 仍可使用）")
        return None
    reset_llm_breaker()
    return time.perf_counter() - start
