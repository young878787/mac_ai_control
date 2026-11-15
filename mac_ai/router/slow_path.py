"""AI 軌道（Slow Path）：規劃工具呼叫、驗證、執行與暖機。"""
import json
import re
import time
from typing import List, Optional, Tuple

from mac_ai.config import PLAN_TIMEOUT_S, WARMUP_TIMEOUT_S
from mac_ai.router.actions import ExecContext, chat_reply, front_context, run_tool
from mac_ai.router.fast_path import _NARR_RE, _NEG_RE, FastPathMatcher, Match, canonicalize_clause
from mac_ai.router.lexicon import _compact, build_hint_lines
from mac_ai.router.llm_client import LLMError, _llm, reset_llm_breaker
from mac_ai.router.tools import validate_tool_call


# 快取前綴：必須逐字穩定，動態資訊（詞庫提示、本機搜尋結果）一律放使用者訊息（見 build_user_message）
# 措辭刻意維持散文、第二人稱：實測（20 句×4 次）改成 Markdown 標題／條列或第三人稱，正確率由 68/80 降到 60/80；
# 加『不要直接寫（長篇）回覆』可縮短提問句的 content，但正確率由 90/96 降到 84～87/96
SYSTEM_PROMPT = (
    "你是運行在 macOS 上的本地語音控制助手。依使用者的繁體中文指令呼叫工具，可一次呼叫多個。"
    "需要回覆使用者時（提問、閒聊、與其他工具無關的話），呼叫 chat_reply。"
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


# G1（計劃書 §15）：Fast Path 守門擋下的句子交給 LLM 後，4bit 模型幾乎不理會否定（實測 30 句×3 次：
# 『不要鎖定螢幕』『別讓電腦睡眠』『剛剛鎖定螢幕了』都照做，63 次不該動作中 27 次是睡眠／鎖定／關 App；改提示詞無效）。
# 所以否定／敘述句不讓 LLM 規劃工具，只回話；資訊提問只保留不改變狀態的工具。
# 『可以幫我開小算盤嗎』『今天台北會下雨嗎』這類只有『嗎』的是非問句／客氣請求不擋（實測 AI 都做對）。
_INFO_QUESTION_RE = re.compile(r"什麼|怎麼|為什麼|為何|如何|哪|幾|多少|是不是|有沒有")
STATE_TOOLS = frozenset({"control_mac_app", "set_system_volume", "adjust_screen_brightness", "system_power_action", "open_project"})
NO_ACTION_HINT = "這句話沒有執行任何電腦操作；回覆不得聲稱已經執行。"
# 否定／敘述句連 chat call 也不送：實測（19 句×3 次）chat 回覆有 7 句謊稱已執行（『昨天把音量調到50』→『音量已調整至50』、
# 『電腦剛剛自己睡眠了』→『睡眠模式已啟動』），system 裡寫明不得聲稱已執行也沒用；固定回覆誠實、也省掉約 1 秒
NO_ACTION_REPLY = "好，我沒有執行任何動作。"
# 糾正句開頭的『沒有／不是』是在糾正上一句，不是否定動作（實測：『沒有我說的是這網頁』『沒有，我說的是這個畫面』）
_CORRECTION_RE = re.compile(r"^(?:沒有|不是|不對)(?:啦|喔|啊|耶|欸)?")


def _strip_correction(clause: str) -> str:
    rest = _CORRECTION_RE.sub("", clause, count=1)
    return rest if rest != clause and (not rest or rest.startswith(("我說", "我是說", "我要", "是"))) else clause


def intent_guard(utterance: str) -> Optional[str]:
    """no_action：有任一子句是否定／敘述（整步只回話，寧可少做）；info_question：每個子句都是資訊提問；其餘 None"""
    m = FastPathMatcher()
    clauses = [c for c in (_strip_correction(canonicalize_clause(c)) for c in m.split_clauses(utterance)) if c]
    if any(m._blocked(c) for c in clauses):
        return "no_action"
    if clauses and all(_INFO_QUESTION_RE.search(c) for c in clauses):
        return "info_question"
    return None


def _local_reroute(utterance: str, query: str) -> Optional[Match]:
    """LLM 要開網頁搜尋時，依原句重新判斷範圍：原句是『找／打開』（或帶本機範圍詞），而且受詞是 App、專案、資料夾、
    檔案時，改走本機（本機優先）。『搜尋／查』沒帶範圍詞的維持網頁；否定、敘述、提問句也不導向"""
    matcher = FastPathMatcher()
    q = _compact(query)
    for clause in matcher.split_clauses(utterance):
        m = matcher.match_local(clause)
        if m is not None and m.intent.domain == "file" and not m.intent.slots.get("local_only"):
            continue  # 沒有本機證據：模型判斷要上網，就尊重它（本機已由 Fast Path 先找過）
        if m is not None and m.intent is not None and q and (q in _compact(m.intent.target) or _compact(m.intent.target) in q):
            return m
    return None


def local_miss_hint(miss: dict) -> str:
    """只陳述事實，不指示模型怎麼做。實測（34 句）：陳述事實 23/26 該上網的上網、7/8 其他沒亂上網；
    加上『除非…否則請搜尋』會變成一律上網（0/8）；原本的長指示放在使用者訊息只剩 17/26"""
    return f"本機沒有名為「{miss['label']}」的檔案。"


def local_miss_reply(miss: dict, reason: str) -> str:
    """轉交模型後仍沒有任何動作時的保底回覆：告訴使用者找不到、為什麼，以及下一步（要上網查就明說『搜尋』）"""
    return f"⚠️ 找不到「{miss['label']}」：本機沒有符合的檔案（範圍：{miss['scope']}）；{reason}。要上網查請說「搜尋 {miss['label']}」"


def front_hint(ctx: ExecContext) -> Optional[str]:
    """給模型的『目前視窗』：最前面的 App，是瀏覽器而且這一句已經讀過網址時加上 host（不為了提示另外花 200 ms 問網址）"""
    front = front_context(ctx, used=False)
    if front is None:
        return None
    return front.app + (f"：{ctx.front_host}" if front.kind == "browser" and ctx.front_host else "")


def build_user_message(utterance: str, local_miss: Optional[dict] = None, front: Optional[str] = None) -> str:
    """規劃呼叫的使用者訊息：原句在最前面（不改字），有詞庫提示、本機搜尋結果、目前視窗時才附上 Markdown 段落。
    動態資訊放這裡而不是 system：實測放 system 時工具定義的前綴快取失效，prefill 約 2 秒（沒放約 0.2 秒）。
    標題用『名稱對照』：實測用『已知詞庫』時，『幫我開檔案總管』會被規劃成 open_project"""
    parts = [utterance]
    hints = build_hint_lines(utterance)
    if hints:
        parts.append("## 名稱對照\n" + "\n".join(f"- {h}" for h in hints))
    if local_miss is not None:
        parts.append("## 本機搜尋結果\n" + local_miss_hint(local_miss))
    if front:
        parts.append("## 目前視窗\n" + front)
    return "\n\n".join(parts)


def plan_slow_path(utterance: str, meta: Optional[dict] = None, local_miss: Optional[dict] = None,
                   front: Optional[str] = None) -> Tuple[List[Tuple[str, dict]], Optional[str], List[str]]:
    """送 LLM 規劃工具呼叫。回傳 (通過驗證的 [(工具, 參數)], LLM 純文字回覆, 被拒絕的原因)。
    meta 若有傳入，回填 llm（server timings）與 validate_ms；LLM 不可用時拋出 LLMError。
    local_miss：本機搜尋 0 筆而轉交過來（{"label", "scope"}）→ 提示模型本機結果，且不再導回本機（避免繞圈）"""
    llm_meta: dict = {}
    message = _llm(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_user_message(utterance, local_miss, front)}],
        # 192：工具呼叫每個約 23 tokens（4 個工具實測 94）；模型對提問偶爾直接寫長篇 content（不採用），
        # 實測 261 tokens／6.5 秒、甚至寫到逾時，上限讓它最多約 5 秒就收手
        timeout=PLAN_TIMEOUT_S, max_tokens=192, meta=llm_meta,
    )

    t = time.perf_counter()
    calls: List[Tuple[str, dict]] = []
    rejected: List[str] = []
    reroutes: List[str] = []
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
            continue
        if func["name"] == "web_search_or_open_url" and local_miss is not None and local_miss.get("query"):
            # 轉交：模型只決定要不要上網，關鍵字用使用者的原話（實測模型會加年份、翻成英文、丟字）
            if meta is not None:
                meta["model_query"], meta["used_query"] = clean["query"], local_miss["query"]
            clean = {**clean, "query": local_miss["query"]}
            if (func["name"], clean) in calls:
                continue
        if func["name"] == "web_search_or_open_url" and local_miss is None:
            local = _local_reroute(utterance, clean["query"])
            if local is not None:
                calls.extend(local.actions)
                reroutes.append(f"[Domain 導向] 搜尋「{clean['query']}」→ {local.intent.domain}")
                if meta is not None:
                    meta["intent"] = local.intent.to_log()
                continue
        calls.append((func["name"], clean))
    if meta is not None:
        meta["llm"] = llm_meta
        meta["validate_ms"] = (time.perf_counter() - t) * 1000
        meta["reroutes"] = reroutes
    return calls, message.get("content"), rejected


def execute_slow_path_llm(utterance: str, timing: Optional[dict] = None, ctx: Optional[ExecContext] = None,
                          local_miss: Optional[dict] = None) -> List[str]:
    """將未命中的自然語言指令送入本地 LLM 進行多工具拆解與執行；
    若傳入 timing，會回填 llm_ms（規劃耗時）、llm（server timings）、validate_ms。
    LLM 不可用時拋出 LLMError，由呼叫端決定補救。
    local_miss：本機搜尋 0 筆轉交過來；模型沒有選任何動作時回保底的『找不到』，不另外閒聊"""
    step = timing if timing is not None else {}
    ctx = ctx or ExecContext()
    ctx.utterance = utterance
    if local_miss is not None:
        ctx.chat_hint = local_miss_hint(local_miss) + "回覆不得聲稱已經找到。"
    guard = intent_guard(utterance) if local_miss is None else None
    if guard is not None:
        step["guard"] = guard
    if guard == "no_action":  # 不呼叫 LLM（規劃與 chat 都不送），回固定文字
        ctx.replies.append(NO_ACTION_REPLY)
        return ["🛡️ 這句是否定或敘述，不執行任何電腦操作", f"💬 {NO_ACTION_REPLY}"]
    t0 = time.perf_counter()
    try:
        calls, reply, rejected = plan_slow_path(utterance, step, local_miss, front_hint(ctx))
    finally:
        step["llm_ms"] = (time.perf_counter() - t0) * 1000
    llm = step.get("llm") or {}
    if llm.get("prefill_ms") is not None and llm.get("decode_ms") is not None:
        llm["overhead_ms"] = max(0.0, step["llm_ms"] - llm["prefill_ms"] - llm["decode_ms"])

    logs = [f"⚠️ 已拒絕不合法的工具呼叫：{r}" for r in rejected] + list(step.get("reroutes") or [])
    if guard == "info_question":
        dropped = [(n, a) for n, a in calls if n in STATE_TOOLS]
        if dropped:
            calls = [(n, a) for n, a in calls if n not in STATE_TOOLS]
            step["guard_dropped"] = [n for n, _ in dropped]
            logs.append(f"🛡️ 提問句不執行會改變狀態的動作：{', '.join(f'{n}({a})' for n, a in dropped)}")
            ctx.chat_hint = NO_ACTION_HINT
    if local_miss is not None and not calls:  # 模型判斷不是網路查詢、也沒有別的事要做：確認找不到
        text = local_miss_reply(local_miss, "AI 沒有選擇上網搜尋")
        ctx.replies.append(text)
        ctx.results.append(text)  # 這一步的結果是『找不到』（狀態算失敗，UI 顯示錯誤泡泡）
        return logs + [text]
    if not calls and not rejected:
        if _is_look_at_screen(utterance):
            return logs + [f"[Slow Path - 補救] read_screen({{}}) -> {run_tool('read_screen', {}, ctx)}"]
        # 沒有工具可呼叫：規劃呼叫的 content 不採用，另外觸發 chat call
        return logs + [chat_reply(ctx)]
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
