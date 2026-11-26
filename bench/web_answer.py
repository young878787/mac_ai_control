"""長尾回答實測（計畫 docs/plans/completed/2026-11-21-web-answer.md §7.2 R2、R3、R4）：用真的 Brave 與真的雲端跑 20 題固定題目。
需要 .env 同時有 AI_API_KEY 與 BRAVE_API_KEY。會連網、會產生雲端與搜尋費用（每題 1 次搜尋＋2 次雲端呼叫）。
不真的開瀏覽器（退回開搜尋頁時只記數）；每題之間清掉對話記憶與待決來源，題目彼此獨立。
Brave 條款不准把搜尋結果存成資料庫：本腳本不寫任何檔案；--show 只把摘錄印在終端機，給人逐題核對 R3。

    python bench/web_answer.py          # 只印每題的工具、延遲、回答
    python bench/web_answer.py --show   # 另外印每題的摘錄（標題＋前 120 字），用來核對「回答與摘錄一致、沒有編造」
"""
import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mac_ai.router import cloud_client, memory, pipeline, search_client, sources  # noqa: E402

QUESTIONS = {
    "人物": ["周杰倫是誰", "黃仁勳是誰", "蔡依林是誰", "大谷翔平是誰", "張忠謀是誰"],
    "時事": ["最近有什麼新聞", "今年金馬獎最佳影片是哪一部", "最新的 iPhone 是哪一代", "台灣現在的總統是誰", "今年的諾貝爾和平獎得主是誰"],
    "比分": ["湖人最近一場比賽比數多少", "勇士隊最近一場比賽誰贏了", "今年世界盃足球賽冠軍是誰", "今年世界大賽冠軍是哪一隊", "中職今年總冠軍是哪一隊"],
    "營業時間": ["全聯營業到幾點", "台北101觀景台營業到幾點", "故宮博物院幾點開門", "誠品信義店營業到幾點", "鼎泰豐信義店幾點開始營業"],
}


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="印每題的摘錄（只在終端機，不存檔）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 題（除錯用）")
    args = ap.parse_args()
    if not (search_client.available() and cloud_client.available()):
        print("需要 .env 同時有 AI_API_KEY（雲端）與 BRAVE_API_KEY（搜尋）")
        return 2

    opened, captured, chosen = [], [], []
    subprocess.run = lambda cmd, **kw: (opened.append(cmd) if cmd[0] == "open" else None, subprocess.CompletedProcess(cmd, 0, "", ""))[1]  # 不真的開瀏覽器；只計 open（讀前景的 lsappinfo 不算）
    real_search, real_chat = search_client.search, cloud_client.chat

    def search(query, timeout=None, **kw):
        res = real_search(query, timeout, **kw)
        captured.append(res)
        return res

    def chat(messages, *, tools=None, **kw):
        msg = real_chat(messages, tools=tools, **kw)
        if tools:
            chosen.append([c["function"]["name"] for c in msg.get("tool_calls") or []] or ["（文字回覆）"])
        return msg

    search_client.search, cloud_client.chat = search, chat
    import mac_ai.router.answer_tools as la  # 執行時才取 search_client.search，所以上面的替換有效
    assert la.search_client is search_client

    rows, n = [], 0
    for cat, qs in QUESTIONS.items():
        for q in qs:
            if args.limit and n >= args.limit:
                break
            n += 1
            memory.STORE.clear()
            sources.STORE.clear()
            cloud_client.reset()  # 上一題的逾時不要讓這題被熔斷（題目彼此獨立）
            captured.clear()
            chosen.clear()
            opened.clear()
            t = time.perf_counter()
            out = pipeline.process_voice_command(q)
            ms = (time.perf_counter() - t) * 1000
            step = out["steps"][0]
            web = step.get("web") or {}
            rows.append({"cat": cat, "q": q, "ms": ms, "tool": chosen[0] if chosen else ["-"], "status": step["status"],
                         "answered": bool(web) and "error" not in web and bool(out["replies"]), "web": web,
                         "reply": " ".join(out["replies"]), "opened": len(opened), "snips": list(captured[0]) if captured else []})
    print("\n" + "=" * 100)
    for i, r in enumerate(rows, 1):
        w = r["web"]
        print(f"{i:2}. [{r['cat']}] {r['q']}\n    工具={r['tool']} 狀態={r['status']} 總 {r['ms']:.0f} ms（搜尋 {w.get('search_ms', 0):.0f}／回答 {w.get('answer_ms', 0):.0f}）"
              f"{' 錯誤=' + w['error'] if w.get('error') else ''}{' 開了網頁' if r['opened'] else ''}"
              f"{' 時間範圍=' + w['freshness'] if w.get('freshness') else ''}{'（近期沒結果，改不過濾）' if w.get('fresh_retry') else ''}"
              f"{' 回答 tokens=' + str((w.get('cloud') or {}).get('completion_tokens')) if w.get('cloud') else ''}"
              f"{' finish=' + str((w.get('cloud') or {}).get('finish_reason')) if w.get('cloud') else ''}\n    回覆：{r['reply'] or '（無）'}")
        if args.show:
            for s in r["snips"]:
                print(f"      · [{s.date or '無日期'}] {s.title[:40]}｜{s.snippet[:120]}")
    ms = [r["ms"] for r in rows]
    answered = [r for r in rows if r["answered"]]
    print("=" * 100)
    print(f"R2 延遲（全部 {len(ms)} 題）：p50 {statistics.median(ms):.0f} ms、p95 {pct(ms, 95):.0f} ms、max {max(ms):.0f} ms；"
          f"扣掉第 1 題（冷連線）：p50 {statistics.median(ms[1:] or ms):.0f} ms、p95 {pct(ms[1:] or ms, 95):.0f} ms")
    if answered:
        am = [r["ms"] for r in answered]
        print(f"有答案的 {len(answered)} 題：p50 {statistics.median(am):.0f} ms、p95 {pct(am, 95):.0f} ms")
    print(f"R4 選 web_answer：{sum(1 for r in rows if r['tool'] == ['web_answer'])}/{len(rows)}；"
          f"有答案 {len(answered)}/{len(rows)}；退回開搜尋頁 {sum(1 for r in rows if r['opened'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
