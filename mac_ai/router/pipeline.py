"""雙軌整合路由器：正規化 → 切句 → Fast／Slow 依說話順序執行。"""
import time
from typing import Any, Dict, List, Optional, Tuple

from mac_ai.router.actions import ExecContext
from mac_ai.router.fast_path import FastPathMatcher, Match
from mac_ai.router.llm_client import LLMError, LLMUnavailable
from mac_ai.router.normalize import log_normalization, normalize_utterance
from mac_ai.router.slow_path import execute_slow_path_llm


def _step_status(ctx: ExecContext) -> str:
    if ctx.status:
        return ctx.status
    if any(r.startswith(("❌", "⚠️")) for r in ctx.results):
        return "exec_failed"
    if any(r.startswith("⏭️") for r in ctx.results):
        return "skipped"
    return "ok"


def _recover_slow_segment(clauses: List[str], matcher: FastPathMatcher, ctx: ExecContext, err: LLMError) -> List[str]:
    """LLM 不可用時的補救：有 Fast 候選（歧義而被擋下的第 1 名）就執行；沒有就告知使用者。
    被安全守門擋下的句子沒有候選，不會執行"""
    ctx.status = "llm_unavailable" if isinstance(err, LLMUnavailable) else "llm_error"
    logs = []
    for clause in clauses:
        cand = matcher.match(clause, candidates=True)
        if cand is not None:
            logs += [f"[Fallback | {cand.note}] {r}" for r in cand.run(ctx)]
            ctx.status = "fallback"
        else:
            msg = f"⚠️ AI 服務無法使用（{err}），這句沒有處理：「{clause}」"
            ctx.replies.append(msg)
            logs.append(msg)
    return logs


def process_voice_command(stt_text: str) -> dict:
    """依說話順序執行。命中 Fast Path 的子句各自成一步；連續未命中的子句合併成一步送 LLM。
    回傳 {"stages", "steps": [{kind, text, ms, status, logs, ...}], "replies", "total_ms"}，ms 含該步的執行時間"""
    print(f"\n🎙️ 收到語音辨識文字：「{stt_text}」")
    start_time = time.perf_counter()

    t = time.perf_counter()
    normalized = normalize_utterance(stt_text)
    stages = {"normalize_ms": (time.perf_counter() - t) * 1000}
    log_normalization(stt_text, normalized)
    stt_text = normalized

    matcher = FastPathMatcher()
    t = time.perf_counter()
    clauses = matcher.split_clauses(stt_text)
    stages["split_ms"] = (time.perf_counter() - t) * 1000
    print(f"✂️ 子句切分結果：{clauses}")

    t = time.perf_counter()
    segments: List[Tuple[str, List[str], Optional[Match]]] = []
    for clause in clauses:
        run = matcher.match(clause)
        if run:
            segments.append(("fast", [clause], run))
        elif segments and segments[-1][0] == "slow":
            segments[-1][1].append(clause)
        else:
            segments.append(("slow", [clause], None))
    stages["match_ms"] = (time.perf_counter() - t) * 1000

    ctx = ExecContext()
    steps = []
    for kind, texts, run in segments:
        text = "，".join(texts)
        step_start = time.perf_counter()
        step: Dict[str, Any] = {"kind": kind, "text": text}
        ctx.step, ctx.utterance, ctx.results, ctx.status, ctx.chat_done = step, text, [], None, False
        if kind == "fast":
            logs = run.run(ctx)
            step["match"] = {"confidence": run.confidence, "note": run.note}
            step["ms"] = (time.perf_counter() - step_start) * 1000
            for log in logs:
                print(f"⚡ [Fast Path | {step['ms']:.2f}ms] 「{text}」 -> {log}")
        else:
            print(f"🧠 [轉交 Slow Path AI 軌道] 待解析語句：「{text}」")
            try:
                logs = execute_slow_path_llm(text, step, ctx)
            except LLMError as e:
                logs = _recover_slow_segment(texts, matcher, ctx, e)
            step["ms"] = (time.perf_counter() - step_start) * 1000
            print(f"🤖 [Slow Path | {step['ms']:.0f}ms，其中 LLM {step.get('llm_ms', 0):.0f}ms]")
            for log in logs:
                print(f"   {log}")
        step["status"] = _step_status(ctx)
        step["logs"] = logs
        steps.append(step)
    ctx.step = None

    total_ms = (time.perf_counter() - start_time) * 1000
    for reply in ctx.replies:
        print(f"💬 回覆：{reply}")
    print(f"⏱️ 階段：normalize {stages['normalize_ms']:.2f}ms｜split {stages['split_ms']:.2f}ms｜match {stages['match_ms']:.2f}ms")
    print(f"⏱️ 全部完成：{total_ms:.0f}ms（{len(steps)} 步）")
    return {"stages": stages, "steps": steps, "replies": ctx.replies, "total_ms": total_ms}
