"""AI 軌道（Slow Path）：規劃工具呼叫、驗證、執行與暖機。"""
import json
import re
import time
from typing import List, Optional, Set, Tuple
from xml.sax.saxutils import escape

from mac_ai.config import PLAN_TIMEOUT_S, WARMUP_TIMEOUT_S
from mac_ai.router import cloud_client, context, memory, search_client
from mac_ai.router import local_actions  # noqa: F401  匯入即登錄本機工具（LOCAL_TOOLS），本模組的 run_tool 要靠它
from mac_ai.router.actions import run_tool
from mac_ai.router.answer_tools import today_taipei
from mac_ai.router.chat_tools import chat_reply
from mac_ai.router.cloud_client import CloudError
from mac_ai.router.exec_context import _FAIL_PREFIXES, ExecContext, front_context
from mac_ai.router.fast_path import _NARR_RE, _NEG_RE, FastPathMatcher, Match, answer_mode, canonicalize_clause
from mac_ai.router.lexicon import QUERY_CUES, _OPEN, _alias_index, _alt, _compact, build_hint_lines, project_index
from mac_ai.router.llm_client import LLMError, _llm, reset_llm_breaker
from mac_ai.router.query_intent import verify_query
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
_SEARCH_INTENT_RE = re.compile(_alt(QUERY_CUES["search_intent"]), re.IGNORECASE)
_URL_RE = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:[/?#]\S*)?$", re.IGNORECASE)


_WEB_TOOLS = ("web_answer", "web_search_or_open_url", "site_search")  # 開網頁或回答由原句決定的幾個工具（validate_calls）

# 『打開 YouTube』『上 YouTube』『去 GitHub』：句子明說了詞庫裡的網站、也有開啟的動詞，模型給的網址又屬於那個網站時，放行並改成網站首頁
# （實測雲端對這類說法都回正確的網址，卻被 _search_intent_error 全部擋下：9 種說法 0 通過）。改成首頁而不用模型的網址：模型可能編出深層連結
_SITE_OPEN_RE = re.compile(rf"(?:{_OPEN}|上|去|前往|進入|連到|連上|逛)")


def site_open_home(utterance: str, query: str) -> Optional[str]:
    """放行條件（全部成立）：句子沒有搜尋字詞（有的維持模型的搜尋網址）、不是資訊提問、有開啟動詞、句子只明說一個詞庫網站（名稱或別名，
    『YouTube Music』不算）、query 是那個網站網域的網址。回傳要開的首頁；不符合回傳 None（交給 _search_intent_error）"""
    if _SEARCH_INTENT_RE.search(utterance) or _INFO_QUESTION_RE.search(utterance) or not _SITE_OPEN_RE.search(utterance):
        return None
    q = query.strip()
    host = context.parse_host(q if "://" in q else "https://" + q)
    site = context.site_for(host) if host else None
    if site is None or not site.get("home") or context.site_in(utterance) is not site:
        return None
    return site["home"]


def _search_intent_error(utterance: str, query: str, name: str = "web_search_or_open_url") -> Optional[str]:
    """web_search_or_open_url（與雲端的 site_search）的語意守門：沒有搜尋意圖字，且 query 不是原句裡字面出現的網址，就拒絕"""
    if _SEARCH_INTENT_RE.search(utterance):
        return None
    q = query.strip().lower()
    if _URL_RE.match(q) and re.sub(r"^https?://", "", q).rstrip("/") in utterance.lower():
        return None
    return f"{name} 被略過：這句話沒有搜尋的意思（要搜尋請說『搜尋』『查』『找』…）"


# 『要看畫面』的說法：LLM 沒呼叫 read_screen 時，chat call 看不到畫面卻會編內容（實測回過「畫面上寫『Hello…』」），所以直接讀畫面。
# 否定、敘述、提問（『什麼』除外，『寫什麼』本身就是看畫面的問法）不算，避免『網頁是什麼』『怎麼看螢幕亮度』被當成要讀畫面
_LOOK_AT_SCREEN_RE = re.compile(
    r"(?:看|讀|念|辨識|解讀|截).{0,6}(?:畫面|螢幕|網頁|頁面|圖片)|(?:畫面|螢幕|網頁|頁面|圖片)(?:上)?(?:在)?(?:寫|說|講|有|顯示)(?:了)?什麼")
_NOT_A_REQUEST_RE = re.compile(_alt(QUERY_CUES["not_a_request"]))


def _is_look_at_screen(text: str) -> bool:
    return bool(_LOOK_AT_SCREEN_RE.search(text)) and not any(p.search(text) for p in (_NEG_RE, _NARR_RE, _NOT_A_REQUEST_RE))


# G1（計劃書 §15）：Fast Path 守門擋下的句子交給 LLM 後，4bit 模型幾乎不理會否定（實測 30 句×3 次：
# 『不要鎖定螢幕』『別讓電腦睡眠』『剛剛鎖定螢幕了』都照做，63 次不該動作中 27 次是睡眠／鎖定／關 App；改提示詞無效）。
# 所以否定／敘述句不讓 LLM 規劃工具，只回話；資訊提問只保留不改變狀態的工具。
# 『可以幫我開小算盤嗎』『今天台北會下雨嗎』這類只有『嗎』的是非問句／客氣請求不擋（實測 AI 都做對）。
_INFO_QUESTION_RE = re.compile(_alt(QUERY_CUES["info_question"]))
# front_action 只有雲端會呼叫（『怎麼全螢幕』『關閉視窗要按什麼』是在問，不是要做）
# 說『關掉／關閉…』就一定是關閉：雲端的 front_action 不能把它換成最小化（『收起來』→ 最小化可以）
_CLOSE_RE = re.compile(r"關掉|關閉|關起來|關上|退出|結束|關$")
STATE_TOOLS = frozenset({"control_mac_app", "set_system_volume", "adjust_screen_brightness", "system_power_action", "open_project",
                         "front_action"})
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


# 「它／剛剛那個」：句子沒明說對象。模型（本機或雲端）依記憶決定對象時，對象必須真的出現在最近成功的操作裡（計畫 §6.5 對象守門）
_PRONOUN_RE = re.compile(r"它|牠|剛剛那個|剛才那個|剛剛開的|剛才開的|上一個")
_TARGET_ARGS = {"control_mac_app": "app_name", "open_project": "project_name"}


def _mentioned(name: str, target: str, utterance: str) -> bool:
    """對象（或它的別名）有沒有直接出現在句子裡"""
    u = _compact(utterance)
    if _compact(target) in u:
        return True
    if name == "control_mac_app":
        return any(k and k in u for k, v in _alias_index().items() if v == target)
    return any(k and k in u for k, (n, _) in project_index().items() if n == target)


def target_error(name: str, args: dict, utterance: str, targets: Set[str]) -> Optional[str]:
    key = _TARGET_ARGS.get(name)
    if key is None or not _PRONOUN_RE.search(utterance):
        return None
    target = args.get(key, "")
    if target in targets or _mentioned(name, target, utterance):
        return None
    return f"{name} 被略過：不確定「它」指的是什麼（{target} 不在最近成功的操作裡）"


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


def validate_calls(tool_calls: list, utterance: str, local_miss: Optional[dict], meta: Optional[dict],
                   targets: Set[str], schemas: Optional[dict] = None) -> Tuple[List[Tuple[str, dict]], List[str], List[str]]:
    """模型回傳的 tool_calls（本機、雲端共用）→ (通過驗證的 [(工具, 參數)], 被拒絕的原因, 導向本機的紀錄)。
    依序：格式 → validate_tool_call → 對象守門 → 搜尋意圖守門 → 轉交時用原話 → 本機優先導向。
    schemas：雲端傳 CLOUD_SCHEMAS（多了 front_action、site_search；有搜尋金鑰時再多 web_answer）；site_search 通過後換成本機的 context_search 執行。
    開網頁或回答由原句決定、不由模型決定（answer_mode，計畫 §4.1）：模型呼叫 web_answer 但原句明說要看畫面 → 改成開網頁；
    模型要開網頁搜尋（web_search_or_open_url、site_search）但原句是在問事情，而且這次有 web_answer → 改成 web_answer。query 沿用模型給的"""
    calls: List[Tuple[str, dict]] = []
    rejected: List[str] = []
    reroutes: List[str] = []
    for call in tool_calls:
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
        name = func.get("name", "")
        clean, err = validate_tool_call(name, args, schemas)
        if not err:
            err = target_error(name, clean, utterance, targets)
        if not err and name in _WEB_TOOLS and schemas and "web_answer" in schemas:
            mode = answer_mode(utterance)
            if name == "web_answer" and mode == "open":
                name = "web_search_or_open_url"
                reroutes.append(f"[Answer 導向] web_answer「{clean['query']}」→ 開網頁（原句要看畫面）")
            elif name != "web_answer" and mode == "answer":
                reroutes.append(f"[Answer 導向] {name}「{clean['query']}」→ web_answer（原句是在問事情）")
                name = "web_answer"
        if not err and name == "web_search_or_open_url":
            err = _search_intent_error(utterance, clean["query"])
            home = site_open_home(utterance, clean["query"]) if err else None  # 只放寬原本會拒絕的；原本放行的（有搜尋字詞、字面網址）維持不變
            if home:
                clean, err = {**clean, "query": home}, None
        if not err and name == "front_action" and clean["id"] == "minimize" and _CLOSE_RE.search(utterance):
            err = "front_action 被略過：句子說的是關閉，不是最小化"
        if not err and name == "site_search":
            err = _search_intent_error(utterance, clean["query"], "site_search")
        if err:
            rejected.append(err)
            continue
        if name == "web_search_or_open_url" and local_miss is not None and local_miss.get("query"):
            # 轉交：模型只決定要不要上網，關鍵字用使用者的原話（實測模型會加年份、翻成英文、丟字）
            if meta is not None:
                meta["model_query"], meta["used_query"] = clean["query"], local_miss["query"]
            clean = {**clean, "query": local_miss["query"]}
            if (name, clean) in calls:
                continue
        if name in ("web_search_or_open_url", "site_search") and local_miss is None:
            local = _local_reroute(utterance, clean["query"])
            if local is not None:
                calls.extend(local.actions)
                reroutes.append(f"[Domain 導向] 搜尋「{clean['query']}」→ {local.intent.domain}")
                if meta is not None:
                    meta["intent"] = local.intent.to_log()
                continue
        calls.append((cloud_client.CLOUD_TOOL_TARGETS.get(name, name), clean))
    return calls, rejected, reroutes


def plan_slow_path(utterance: str, meta: Optional[dict] = None, local_miss: Optional[dict] = None,
                   front: Optional[str] = None, targets: Optional[Set[str]] = None) -> Tuple[List[Tuple[str, dict]], Optional[str], List[str]]:
    """送 LLM 規劃工具呼叫。回傳 (通過驗證的 [(工具, 參數)], LLM 純文字回覆, 被拒絕的原因)。
    meta 若有傳入，回填 llm（server timings）與 validate_ms；LLM 不可用時拋出 LLMError。
    local_miss：本機搜尋 0 筆而轉交過來（{"label", "scope"}）→ 提示模型本機結果，且不再導回本機（避免繞圈）。
    targets：「它」可以指的對象（預設＝記憶裡最近成功的操作）"""
    llm_meta: dict = {}
    message = _llm(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_user_message(utterance, local_miss, front)}],
        # 192：工具呼叫每個約 23 tokens（4 個工具實測 94）；模型對提問偶爾直接寫長篇 content（不採用），
        # 實測 261 tokens／6.5 秒、甚至寫到逾時，上限讓它最多約 5 秒就收手
        timeout=PLAN_TIMEOUT_S, max_tokens=192, meta=llm_meta,
    )
    t = time.perf_counter()
    calls, rejected, reroutes = validate_calls(message.get("tool_calls") or [], utterance, local_miss, meta,
                                               memory.STORE.targets() if targets is None else targets)
    if meta is not None:
        meta["llm"] = llm_meta
        meta["validate_ms"] = (time.perf_counter() - t) * 1000
        meta["reroutes"] = reroutes
    return calls, message.get("content"), rejected


def plan_cloud(utterance: str, ctx: ExecContext, step: dict, targets: Set[str],
               local_miss: Optional[dict] = None, note: Optional[str] = None) -> Tuple[List[Tuple[str, dict]], str, List[str]]:
    """雲端規劃：工具＝TOOLS_SCHEMA（chat_reply 多一個 text 參數）＋front_action、site_search（有 BRAVE_API_KEY 再加 web_answer），同一套驗證（validate_calls）。user 訊息＝本機的 build_user_message＋最近對話（有 web_answer 時再加今天日期）。
    local_miss：本機 0 筆轉交過來（U4）→ 與本機規劃相同：附本機搜尋結果、網頁搜尋用原話、不導回本機。
    note：程式已經在背景做了的事（「我住 X」記下居住地），放在 user 訊息最後的 <note>；system 與工具清單不變（紅線 2）。
    回傳 (通過驗證的呼叫, 文字回覆, 被拒絕的原因)；雲端不可用時拋 CloudError（呼叫端退回本機）"""
    user = build_user_message(utterance, local_miss, front=front_hint(ctx))
    mem = memory.STORE.render(ctx.calls)
    if mem:
        user += "\n\n## 最近對話\n" + mem
        step["memory_turns"] = len(memory.STORE.recent())
    meta: dict = {}
    web = search_client.available()  # 有搜尋金鑰才多給 web_answer；沒有時工具清單與以前逐字相同
    if web:  # 不給日期時模型會把「今年」「最近」編成訓練資料的年份（2026-11-23 實測，計畫 §9.5）；沒有金鑰時訊息不變
        user += "\n\n## 今天日期\n" + today_taipei()  # 與回答那次同一個時鐘（prefs_db._now，測試可注入）
    if note:
        user += f"\n\n<note>{escape(note)}</note>"
    try:
        message = cloud_client.chat([{"role": "system", "content": cloud_client.CLOUD_SYSTEM_PROMPT},
                                     {"role": "user", "content": user}], tools=cloud_client.cloud_tools(web), meta=meta)
    finally:
        step["cloud"] = meta
    tool_calls, say = cloud_client.split_chat_texts(message.get("tool_calls") or [])  # chat_reply(text)：伴隨工具的文字回覆
    calls, rejected, reroutes = validate_calls(tool_calls, utterance, local_miss, step, targets, cloud_client.cloud_schemas(web))
    step["reroutes"] = reroutes
    return calls, message.get("content") or " ".join(say), rejected


def _execute_cloud(utterance: str, step: dict, ctx: ExecContext, guard: Optional[str], targets: Set[str],
                   local_miss: Optional[dict] = None) -> Optional[List[str]]:
    """雲端規劃並在本機執行；雲端不可用回傳 None（呼叫端改走本機）。
    文字回覆只在「沒有工具」或「工具全部通過驗證且執行成功」時採用（避免回『已幫你打開』但其實被拒絕）。
    U12 安全網：雲端只回文字、原句卻在問會變的事實 → 不採用那段文字，改成 web_answer（查詢字用原話）。
    local_miss（U4）：模型沒有選任何動作 → 保底的『找不到』（與本機相同）"""
    step["route"] = "cloud"
    t0 = time.perf_counter()
    try:
        calls, content, rejected = plan_cloud(utterance, ctx, step, targets, local_miss)
    except CloudError as e:
        step["route"], step["cloud_fallback"] = "local", str(e)
        print(f"☁️ 雲端無法使用（{e}），改用本機")
        return None
    finally:
        step["llm_ms"] = (time.perf_counter() - t0) * 1000
    logs = [f"⚠️ 已拒絕不合法的工具呼叫：{r}" for r in rejected] + list(step.get("reroutes") or [])
    if not calls and not rejected and content and local_miss is None:
        query = verify_query(utterance.strip(), canonicalize_clause(utterance.strip()))
        if query:
            calls, content = [("web_answer", {"query": query})], None
            note = f"[Route 導向] 不憑記憶回答：「{query}」→ web_answer"
            step.setdefault("reroutes", []).append(note)
            logs.append(note)
    if local_miss is not None and not calls:  # 模型判斷不是網路查詢、也沒有別的事要做：確認找不到（同本機）
        text = local_miss_reply(local_miss, "AI 沒有選擇上網搜尋")
        ctx.replies.append(text)
        ctx.results.append(text)
        return logs + [text]
    dropped = False
    if guard == "info_question":
        drop = [(n, a) for n, a in calls if n in STATE_TOOLS]
        if drop:
            calls = [(n, a) for n, a in calls if n not in STATE_TOOLS]
            step["guard_dropped"] = [n for n, _ in drop]
            logs.append(f"🛡️ 提問句不執行會改變狀態的動作：{', '.join(f'{n}({a})' for n, a in drop)}")
            dropped = True
    if rejected and not calls:
        ctx.status = "rejected"
    for name, args in calls:
        logs.append(f"[Slow Path - 雲端] {name}({args}) -> {run_tool(name, args, ctx)}")
    if not calls and not rejected and not content:
        if _is_look_at_screen(utterance):
            return logs + [f"[Slow Path - 補救] read_screen({{}}) -> {run_tool('read_screen', {}, ctx)}"]
        return logs + [chat_reply(ctx)]
    all_ok = not rejected and not dropped and not any(r.startswith(_FAIL_PREFIXES) for r in ctx.results)
    if content and all_ok and not {"read_screen", "web_answer"} & {n for n, _ in calls}:  # 看畫面、長尾回答的結果本身就是回覆
        ctx.replies.append(content)
        ctx.ai_reply = True
        logs.append(f"☁️ AI 回覆：{content}")
    return logs


def execute_chat_only(utterance: str, step: dict, ctx: ExecContext, note: str, fallback: str) -> List[str]:
    """已在背景處理、只需要聊天的一步（pipeline 的 escalate kind＝chat：「我住 X」，計畫 docs/plans/completed/2026-11-24-weather-city-and-answer-timeout.md §3.3）。
    原句照原樣送出；模型提出的任何工具呼叫都不執行，只採用文字回覆（2026-11-24 熔斷時本機模型把「我住台北」選成 read_screen）。
    有雲端走雲端規劃（user 訊息最後附 <note>）；沒有雲端、熔斷或雲端失敗走本機 chat call（chat_hint＝note，不帶工具）。
    模型沒給文字或失敗回 fallback（固定句）"""
    ctx.utterance = utterance
    if cloud_client.available():
        step["route"] = "cloud"
        t0 = time.perf_counter()
        try:
            calls, content, rejected = plan_cloud(utterance, ctx, step, set(), note=note)
        except CloudError as e:
            step["route"], step["cloud_fallback"] = "local", str(e)
            print(f"☁️ 雲端無法使用（{e}），改用本機")
        finally:
            step["llm_ms"] = (time.perf_counter() - t0) * 1000
        if step["route"] == "cloud":
            logs = []
            if calls or rejected:
                step["chat_dropped"] = [n for n, _ in calls] + ["rejected"] * len(rejected)
                logs.append(f"🛡️ 只聊天，不執行模型提出的工具：{', '.join(step['chat_dropped'])}")
            text = content or fallback
            ctx.replies.append(text)
            ctx.ai_reply = ctx.ai_reply or bool(content)
            return logs + [f"☁️ AI 回覆：{text}" if content else f"💬 {text}"]
    step.setdefault("route", "local")
    ctx.chat_hint = note
    return [chat_reply(ctx, fallback=fallback)]


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
    if guard == "no_action":  # 不呼叫 LLM（規劃與 chat 都不送，本機雲端都一樣），回固定文字
        ctx.replies.append(NO_ACTION_REPLY)
        return ["🛡️ 這句是否定或敘述，不執行任何電腦操作", f"💬 {NO_ACTION_REPLY}"]
    targets = memory.STORE.targets() | memory.targets_of(ctx.calls)  # 「它」可以指的對象：最近幾輪＋這一句前面幾步
    # 雲端優先（計畫 §13：P0 實測雲端規劃比本機快又準）；轉交（local_miss）也走雲端，有搜尋金鑰時可以直接回答（查詢路由 U4）
    if cloud_client.available():
        logs = _execute_cloud(utterance, step, ctx, guard, targets, local_miss)
        if logs is not None:
            return logs
    step.setdefault("route", "local")
    t0 = time.perf_counter()
    try:
        calls, reply, rejected = plan_slow_path(utterance, step, local_miss, front_hint(ctx), targets)
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
