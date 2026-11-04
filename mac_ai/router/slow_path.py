"""AI 軌道（Slow Path）：規劃工具呼叫、驗證、執行與暖機。"""
import json
import time
from typing import List, Optional, Tuple

from mac_ai.config import PLAN_TIMEOUT_S, WARMUP_TIMEOUT_S
from mac_ai.router.actions import ExecContext, chat_reply, run_tool
from mac_ai.router.lexicon import build_hint_lines
from mac_ai.router.llm_client import LLMError, _llm, reset_llm_breaker
from mac_ai.router.tools import validate_tool_call


SYSTEM_PROMPT = (
    "你是運行在 macOS 上的本地語音控制助手。依使用者的繁體中文指令呼叫工具，可一次呼叫多個。"
    "若指令與任何工具無關，不要呼叫工具，直接簡短回覆。"
    "『打開瀏覽器查某事』只需呼叫 web_search_or_open_url，不要另外開啟瀏覽器。"
    "app_name 必須使用標準英文名稱。"
)


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
