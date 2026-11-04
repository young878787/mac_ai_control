"""mlx_vlm.server 的 OpenAI 相容 API 呼叫、錯誤分類與熔斷。"""
import http.client
import json
import time
import urllib.error
import urllib.request
from typing import List, Optional

from mac_ai.config import BREAKER_COOLDOWN_S, MLX_VLM_URL, MODEL_ID, PLAN_TIMEOUT_S
from mac_ai.router.tools import TOOLS_SCHEMA


class LLMError(Exception):
    """LLM 回應無法使用（格式錯誤、HTTP 錯誤）"""


class LLMUnavailable(LLMError):
    """LLM 連不上或逾時（含熔斷中）"""


_llm_down_until = 0.0  # time.monotonic() 時間點；之前不再呼叫 LLM


def reset_llm_breaker() -> None:
    global _llm_down_until
    _llm_down_until = 0.0


def _chat(messages: List[dict], timeout: float = PLAN_TIMEOUT_S, tools: bool = True,
          max_tokens: int = 512, meta: Optional[dict] = None) -> dict:
    """呼叫 mlx_vlm.server 的 OpenAI 相容 /chat/completions；回傳第一個 choice 的 message。
    meta 若有傳入，回填 server 的 timings（prefill_ms／decode_ms／prompt_n／cache_n／predicted_n）"""
    body = {
        "model": MODEL_ID,
        "messages": messages,
        # 官方建議取樣參數；enable_thinking=False 關閉 <think>，否則延遲倍增
        "temperature": 0.1,
        "top_p": 1.0,
        "top_k": 50,
        "repetition_penalty": 1.1,
        "max_tokens": max_tokens,
        "enable_thinking": False,
    }
    if tools:
        body["tools"] = TOOLS_SCHEMA
    req = urllib.request.Request(
        f"{MLX_VLM_URL}/chat/completions",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if meta is not None:
        t = data.get("timings") or {}
        meta.update(
            prefill_ms=t.get("prompt_ms"), decode_ms=t.get("predicted_ms"),
            prompt_n=t.get("prompt_n"), cache_n=t.get("cache_n"), predicted_n=t.get("predicted_n"),
        )
    message = data["choices"][0]["message"]
    if not isinstance(message, dict):
        raise TypeError("message 不是物件")
    return message


def _llm(messages: List[dict], *, timeout: float, tools: bool = True, max_tokens: int = 512,
         meta: Optional[dict] = None) -> dict:
    """所有 LLM 呼叫的入口：把連線／逾時／格式錯誤轉成 LLMError，並在連不上時啟動熔斷"""
    global _llm_down_until
    if time.monotonic() < _llm_down_until:
        raise LLMUnavailable("AI 服務暫時無法使用（熔斷中）")
    try:
        return _chat(messages, timeout=timeout, tools=tools, max_tokens=max_tokens, meta=meta)
    except urllib.error.HTTPError as e:  # server 有回應但出錯：不熔斷
        raise LLMError(f"HTTP {e.code}") from e
    except (OSError, http.client.HTTPException) as e:  # 連線被拒、逾時、中斷
        _llm_down_until = time.monotonic() + BREAKER_COOLDOWN_S
        raise LLMUnavailable(str(e) or type(e).__name__) from e
    except (KeyError, IndexError, TypeError, ValueError) as e:  # 含 JSONDecodeError
        raise LLMError(f"回應格式錯誤：{type(e).__name__}") from e
