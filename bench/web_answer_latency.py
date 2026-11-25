"""依摘錄回答那次呼叫的延遲拆解（計畫 docs/plans/completed/2026-11-24-weather-city-and-answer-timeout.md §3.8、§8.2 R1）。
每題用真的 Brave 搜尋一次，同一組摘錄重送回答呼叫 N 次（與 local_actions.web_answer 相同的訊息、上限與推理強度），
用串流拆成首位元組、推理段、答案段，印推理 tokens 與速度，最後算各逾時上限的失敗率。
需要 .env 同時有 AI_API_KEY 與 BRAVE_API_KEY；會連網、會產生費用（每題 1 次搜尋＋N 次雲端呼叫）。
一律帶 cache no-cache（避免 proxy 回快取）。Brave 條款：不存檔，也不印網址、摘錄與回答內容（只印有沒有回「查不到」）。

    python bench/web_answer_latency.py              # 6 題 × 5 次
    python bench/web_answer_latency.py --runs 8 --only D,E
    python bench/web_answer_latency.py --effort none
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mac_ai import config  # noqa: E402
from mac_ai.router import cloud_client, search_client  # noqa: E402
from mac_ai.router.fast_path import FastPathMatcher  # noqa: E402
from mac_ai.router.local_actions import today_taipei  # noqa: E402

# 計畫 §7 的 6 題（2026-11-24 log 的原句）。查詢字 None＝用現在的 Fast Path 算（與正式流程相同）；D 當天走雲端規劃，用 log 裡模型給的查詢字
QUESTIONS = [
    ("A", "幫我搜尋台灣的股票明天會怎麼樣", None),
    ("B", "幫我查詢明天的台灣股票會怎麼樣", None),
    ("C", "幫我查一下明天的台股會怎麼樣", None),
    ("D", "我住台北幫我查台北的天氣", "台北 天氣 未來一週 氣溫"),
    ("E", "杰倫是誰", None),
    ("F", "周杰倫是誰", None),
]
STREAM_TIMEOUT_S = 30.0  # 單次讀取逾時（量尾端用，比正式的 WEB_ANSWER_TIMEOUT_S 長）
LIMITS = (6.0, 8.0, 10.0, 12.0)


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def fast_query(utterance: str) -> str:
    m = FastPathMatcher().match(utterance)
    calls = [a for a in (m.actions if m else []) if a[0] == "web_answer"]
    if not calls:
        raise SystemExit(f"「{utterance}」現在不走 web_answer，請改成明給查詢字")
    return calls[0][1]["query"]


def stream_once(url: str, model: str, key: str, messages: list, effort: str) -> dict:
    """送一次串流請求；回傳 ttfb、推理段、答案段（秒）、推理 tokens、completion tokens、答案是不是「查不到」、finish_reason"""
    body = {"model": model, "messages": messages, "max_tokens": config.WEB_ANSWER_MAX_TOKENS, "reasoning_effort": effort,
            "cache": {"no-cache": True}, "stream": True, "stream_options": {"include_usage": True}}
    req = urllib.request.Request(f"{url}/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": config.CLOUD_USER_AGENT})
    t0 = time.perf_counter()
    first = first_reason = first_answer = last_reason = None
    answer, usage, finish, reason_chunks = [], {}, None, 0
    with urllib.request.urlopen(req, timeout=STREAM_TIMEOUT_S) as resp:
        for raw in resp:
            now = time.perf_counter()
            first = first or now
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            data = json.loads(line[5:])
            usage = data.get("usage") or usage
            for choice in data.get("choices") or []:
                delta = choice.get("delta") or {}
                finish = choice.get("finish_reason") or finish
                if delta.get("reasoning_content"):
                    reason_chunks += 1
                    first_reason = first_reason or now
                    last_reason = now
                if delta.get("content"):
                    first_answer = first_answer or now
                    answer.append(delta["content"])
    end = time.perf_counter()
    details = usage.get("completion_tokens_details") or {}
    reason_tokens = details.get("reasoning_tokens")
    reason_s = ((last_reason or first_answer or end) - (first_reason or first or end)) if first_reason else 0.0
    return {"total": end - t0, "ttfb": (first or end) - t0, "reason_s": reason_s,
            "answer_s": end - (first_answer or end), "reason_tokens": reason_tokens if reason_tokens is not None else reason_chunks,
            "reason_exact": reason_tokens is not None, "completion_tokens": usage.get("completion_tokens"),
            "no_answer": cloud_client.is_no_answer(cloud_client.clean_text("".join(answer))), "finish": finish}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5, help="每題重送幾次（同一組摘錄）")
    ap.add_argument("--only", default="", help="只跑這幾題，例如 A,D")
    ap.add_argument("--effort", default=config.CLOUD_REASONING_EFFORT, help="推理強度（預設與正式設定相同）")
    args = ap.parse_args()
    s = cloud_client.settings()
    if s is None or not search_client.available():
        print("需要 .env 同時有 AI_API_KEY（雲端）與 BRAVE_API_KEY（搜尋）")
        return 2
    url, model, key = s
    only = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    today = today_taipei()
    print(f"{time.strftime('%Y-%m-%d %H:%M')}｜推理 {args.effort}｜每題 {args.runs} 次｜正式逾時上限 {config.WEB_ANSWER_TIMEOUT_S:.0f} s")
    samples = []
    for tag, utterance, query in QUESTIONS:
        if only and tag not in only:
            continue
        query = query or fast_query(utterance)
        try:
            results = search_client.search(query, freshness=search_client.freshness_for(utterance))
        except search_client.SearchError as e:
            if e.kind != "no_results":
                print(f"{tag} 搜尋失敗：{e.kind}")
                continue
            results = search_client.search(query)
        messages = cloud_client.web_answer_messages(utterance, results, today)
        print(f"\n{tag} {utterance}（摘錄 {len(results)} 筆）")
        for i in range(args.runs):
            try:
                r = stream_once(url, model, key, messages, args.effort)
            except Exception as e:  # noqa: BLE001 — 只留分類，不印回應內容
                print(f"  {i + 1}. 失敗：{type(e).__name__}")
                continue
            r["tag"] = tag
            samples.append(r)
            speed = r["reason_tokens"] / r["reason_s"] if r["reason_s"] > 0 else 0
            print(f"  {i + 1}. 總 {r['total']:5.2f} s｜首位元組 {r['ttfb']:4.2f} s｜推理 {r['reason_s']:5.2f} s、"
                  f"{r['reason_tokens']} {'tok' if r['reason_exact'] else '段'}（{speed:.0f}/s）｜答案 {r['answer_s']:4.2f} s｜"
                  f"completion {r['completion_tokens']}｜{'查不到' if r['no_answer'] else '有答案'}｜{r['finish']}")
    if not samples:
        return 1
    total = [r["total"] for r in samples]
    print("\n" + "=" * 80)
    print(f"{len(samples)} 次：p50 {statistics.median(total):.2f} s、p90 {pct(total, 90):.2f} s、p95 {pct(total, 95):.2f} s、最慢 {max(total):.2f} s")
    print(f"首位元組 p50 {statistics.median([r['ttfb'] for r in samples]):.2f} s；超過 3 s {sum(r['ttfb'] > 3 for r in samples)} 次")
    print(f"推理 p50 {statistics.median([r['reason_tokens'] for r in samples])}、最多 {max(r['reason_tokens'] for r in samples)}"
          f"（{'tokens' if all(r['reason_exact'] for r in samples) else '串流段數；伺服器沒回 reasoning_tokens'}）")
    print("逾時率：" + "、".join(f"{lim:.0f} s {sum(t > lim for t in total)}/{len(total)}（{sum(t > lim for t in total) / len(total):.1%}）" for lim in LIMITS))
    print(f"回「查不到」：{sum(r['no_answer'] for r in samples)}/{len(samples)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
