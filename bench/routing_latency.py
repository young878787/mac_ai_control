"""路由判斷延遲（查詢路由計畫 docs/plans/completed/2026-11-23-query-routing.md §7.2 L1）：
對 tests/fixtures/routing/corpus.json 每一句，先正規化、切句（不計時），再對每個子句跑 match() N 次，
取這一句（各子句加總）的中位數，最後印全部句子的 p50／p95／最大值。
用這台電腦真的詞庫與已安裝 App 清單（第一次掃描在計時前完成），不連網、不執行任何動作、不寫檔案。
P0 量基線；P2、P3 各再跑一次，p95 不得超過基線的 110%（U10）。

    python bench/routing_latency.py             # 每句 100 次
    python bench/routing_latency.py --runs 20   # 快速看一下
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mac_ai.router import catalog  # noqa: E402
from mac_ai.router.fast_path import FastPathMatcher  # noqa: E402
from mac_ai.router.normalize import normalize_utterance  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "routing" / "corpus.json"


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=100, help="每句重複次數（取中位數）")
    args = ap.parse_args()
    sentences = [s for g in json.loads(CORPUS.read_text())["groups"].values() for s in g]
    catalog.DEFAULT.installed()  # 已安裝 App 清單第一次用到才掃描（會跑 mdfind）：放在計時之前
    m = FastPathMatcher()
    per_sentence = []
    for text in sentences:
        clauses = m.split_clauses(normalize_utterance(text))
        samples = []
        for _ in range(args.runs):
            t = time.perf_counter()
            for clause in clauses:
                m.match(clause)
            samples.append((time.perf_counter() - t) * 1000)
        per_sentence.append((statistics.median(samples), text))
    ms = [v for v, _ in per_sentence]
    print(f"{len(sentences)} 句，每句 {args.runs} 次取中位數（單位 ms，各子句 match() 加總）")
    print(f"p50 {pct(ms, 50):.3f}｜p95 {pct(ms, 95):.3f}｜最大 {max(ms):.3f}｜平均 {statistics.mean(ms):.3f}")
    print("最慢 5 句：")
    for v, text in sorted(per_sentence, reverse=True)[:5]:
        print(f"  {v:.3f}  {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
