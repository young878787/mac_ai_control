"""chat call（chat_reply）：原句直接送本機模型、不帶工具，回覆寫進 ctx.replies。"""
import re
import time
from typing import Optional

from mac_ai.config import CHAT_TIMEOUT_S
from mac_ai.router.exec_context import ExecContext
from mac_ai.router.llm_client import LLMError, LLMUnavailable, _llm
from mac_ai.router.normalize import to_traditional


# 角色寫成名詞片語、不用『本助手』：實測模型會照抄成『我是本助手』
CHAT_SYSTEM_PROMPT = (
    "# 角色\n"
    "在 macOS 本機執行的語音助手，負責回答使用者的提問與閒聊。\n"
    "\n"
    "# 規則\n"
    "- 以繁體中文簡短回答，最多兩句。\n"
    "- 句中的電腦操作（開關程式、音量、亮度、搜尋等）已由其他程式處理；不描述、不重複，只回答提問或閒聊的部分。"
)


def _strip_think(text: str) -> str:
    """去掉模型洩漏的思考內容：只留最後一個 </think> 之後的文字（實測沒有開頭標籤也會洩漏）；沒結束的 <think> 整段丟掉"""
    return re.sub(r"<think>.*", "", text.rsplit("</think>", 1)[-1], flags=re.DOTALL).strip()


def chat_reply(ctx: Optional[ExecContext] = None, *, fallback: Optional[str] = None) -> str:
    """chat call：把這一步的原句直接當成使用者訊息送入模型（不帶 tools），回覆寫入 ctx.replies。
    同一步只回覆一次；失敗只記錄，不拋例外。fallback：模型失敗或沒給文字時改回這句（只有程式內部傳，「我住 X」用）"""
    ctx = ctx or ExecContext()
    if ctx.chat_done:
        return "✅ 已回覆"
    ctx.chat_done = True
    step = ctx.step if ctx.step is not None else {}
    meta: dict = {}
    t = time.perf_counter()
    try:
        message = _llm(
            [{"role": "system", "content": CHAT_SYSTEM_PROMPT + (f"\n\n# 補充資訊\n{ctx.chat_hint}" if ctx.chat_hint else "")},
             {"role": "user", "content": ctx.utterance}],
            timeout=CHAT_TIMEOUT_S, tools=False, max_tokens=128, meta=meta,
        )
    except LLMError as e:
        if fallback:
            step["chat_error"] = str(e)
            ctx.replies.append(fallback)
            return f"💬 {fallback}（AI 回覆失敗：{e}）"
        ctx.status = "llm_unavailable" if isinstance(e, LLMUnavailable) else "llm_error"
        text = f"⚠️ AI 回覆失敗（{e}）"
        ctx.replies.append(text)
        return text
    finally:
        step["chat_ms"] = (time.perf_counter() - t) * 1000
        step["chat"] = meta
    reply = to_traditional(_strip_think(message.get("content") or ""))
    if not reply and fallback:
        ctx.replies.append(fallback)
        return f"💬 {fallback}"
    reply = reply or "無法理解指令"
    ctx.replies.append(reply)
    ctx.ai_reply = True
    return f"🤖 AI 回覆：{reply}"
