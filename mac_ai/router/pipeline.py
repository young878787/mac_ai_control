"""雙軌整合路由器：正規化 → 切句 → Fast／Slow 依說話順序執行。"""
import time
from typing import Any, Dict, List, Optional, Tuple

from mac_ai.router import catalog
from mac_ai.router.actions import ExecContext, run_tool
from mac_ai.router.fast_path import FastPathMatcher, Match
from mac_ai.router.llm_client import LLMError, LLMUnavailable
from mac_ai.router.normalize import log_normalization, normalize_utterance, restore_misheard
from mac_ai.router.slow_path import execute_slow_path_llm, local_miss_reply


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


def _escalate_to_llm(text: str, step: dict, ctx: ExecContext, run: Match) -> List[str]:
    """本機搜尋 0 筆、又沒有明確的本機證據（例如『找 python 教學』）：同一步把原句交給模型判斷。
    模型只決定要不要上網；網頁搜尋的關鍵字一律用使用者的原話（不用模型改寫的字）。
    沒有選任何動作或 LLM 不可用，都回保底的『找不到』並說明原因"""
    miss, ctx.escalate = ctx.escalate, None
    t = time.perf_counter()
    query = run.intent.query if run.intent is not None and run.intent.query else miss["label"]
    miss["query"] = restore_misheard(ctx.normalized, query, ctx.misheard, getattr(run, "start", 0))
    step["extract_ms"] = (time.perf_counter() - t) * 1000  # 原話查詢（含誤聽還原）的運算時間
    step["escalated"] = miss
    print(f"🧠 [本機沒有「{miss['label']}」，轉交 AI 判斷]")
    try:
        return execute_slow_path_llm(text, step, ctx, local_miss=miss)
    except LLMError as e:
        ctx.status = "llm_unavailable" if isinstance(e, LLMUnavailable) else "llm_error"
        msg = local_miss_reply(miss, f"AI 目前無法判斷要不要上網找（{e}）")
        ctx.replies.append(msg)
        return [msg]


def process_voice_command(stt_text: str) -> dict:
    """依說話順序執行。命中 Fast Path 的子句各自成一步；連續未命中的子句合併成一步送 LLM。
    回傳 {"stages", "steps": [{kind, text, ms, status, logs, ...}], "replies", "total_ms"}，ms 含該步的執行時間"""
    print(f"\n🎙️ 收到語音辨識文字：「{stt_text}」")
    start_time = time.perf_counter()
    catalog.DEFAULT.refresh_if_stale()  # 已安裝 App 清單超過一段時間就重掃（在指令執行緒做，不影響收音）

    t = time.perf_counter()
    misheard: List[Tuple[str, str]] = []
    normalized = normalize_utterance(stt_text, misheard)
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
    cursor = 0
    for clause in clauses:
        pos = stt_text.find(clause, cursor)  # 子句在整句中的位置（還原誤聽詞時用）
        if pos >= 0:
            cursor = pos + len(clause)
        run = matcher.match(clause)
        if run:
            run.start = max(pos, 0)
            segments.append(("fast", [clause], run))
        elif segments and segments[-1][0] == "slow":
            segments[-1][1].append(clause)
        else:
            segments.append(("slow", [clause], None))
    stages["match_ms"] = (time.perf_counter() - t) * 1000

    ctx = ExecContext(misheard=misheard, normalized=stt_text)
    steps = []
    for kind, texts, run in segments:
        text = "，".join(texts)
        step_start = time.perf_counter()
        step: Dict[str, Any] = {"kind": kind, "text": text}
        ctx.step, ctx.utterance, ctx.results, ctx.status, ctx.chat_done = step, text, [], None, False
        ctx.escalate, ctx.chat_hint = None, None
        if kind == "fast":
            logs = run.run(ctx)
            step["match"] = {"confidence": run.confidence, "note": run.note}
            if run.intent is not None:
                step["intent"] = run.intent.to_log()
            for log in logs:
                print(f"⚡ [Fast Path | {(time.perf_counter() - step_start) * 1000:.2f}ms] 「{text}」 -> {log}")
            if ctx.escalate is not None:
                more = _escalate_to_llm(text, step, ctx, run)
                for log in more:
                    print(f"   {log}")
                logs = logs + more
            step["ms"] = (time.perf_counter() - step_start) * 1000
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


def process_choice(msg: dict) -> dict:
    """桌面角色點選清單：{"list_id", "n", "action"}。和語音『第 N 個』走同一個 pick_choice，回傳格式同 process_voice_command"""
    start = time.perf_counter()
    ctx = ExecContext()
    step: Dict[str, Any] = {"kind": "fast", "text": f"選擇第 {msg['n']} 筆（{'打開' if msg['action'] == 'open' else '顯示'}）"}
    ctx.step, ctx.utterance = step, step["text"]
    logs = [run_tool("pick_choice", {"n": msg["n"], "action": msg["action"], "list_id": msg["list_id"]}, ctx)]
    step["ms"] = (time.perf_counter() - start) * 1000
    step["status"] = _step_status(ctx)
    step["logs"] = logs
    ctx.step = None
    print(f"\n🖱️ {step['text']} -> {logs[0]}")
    return {"stages": {}, "steps": [step], "replies": ctx.replies, "total_ms": step["ms"]}
