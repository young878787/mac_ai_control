#!/usr/bin/env python3
"""端點偵測 bench：把音檔照真實時間節奏推給真的 macos-stt（ws://127.0.0.1:8765），量延遲與正確率。只比對指令、不執行任何動作。

  python scripts/stt_bench.py run --corpus bench/synth --mode server --out logs/bench_base.jsonl
  python scripts/stt_bench.py run --corpus bench/synth --mode client --out logs/bench_new.jsonl
  python scripts/stt_bench.py report logs/bench_base.jsonl logs/bench_new.jsonl     # 兩個檔：A/B 比較＋驗收判定
  python scripts/stt_bench.py report logs/bench_new.jsonl                           # 一個檔：只看數據

mode=server：舊行為（server 的能量 VAD 靜音斷句；本端 VAD 只觀察、量 speech_end）。
mode=client：本端神經網路 VAD＋語意判斷，主動 flush。
時間點一律相對於「串流開始」（音檔 0 秒）；E2E＝最後一句 final 收到的時間 − 音檔裡說完的時間點。
"""
import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mac_ai import config  # noqa: E402
from mac_ai.router.fast_path import FastPathMatcher  # noqa: E402
from mac_ai.router.normalize import normalize_utterance  # noqa: E402
from mac_ai.stt.client import SttClient  # noqa: E402
from mac_ai.stt.endpoint import EndpointDetector  # noqa: E402
from mac_ai.stt.vad import SileroVad  # noqa: E402

SERVER_LOG = "/tmp/macstt_server.log"
PID_FILE = config.STT_PID_FILE
SETTLE_S = 1.0  # 音檔（含尾端 3 s 靜音）放完後再多等這麼久，才看得出有沒有被拆成兩句
TRAILING_PUNCT = re.compile(r"[\W_]+")


# ---------------------------------------------------------------- 音源

class WavSource:
    """照真實時間節奏吐出 PCM 區塊（介面同 MicSource）；音檔放完後持續吐靜音，直到呼叫端停止"""

    def __init__(self, path: Path, block_s: float):
        import wave
        with wave.open(str(path)) as w:
            assert w.getframerate() == config.STT_SAMPLE_RATE and w.getnchannels() == 1
            self._pcm = w.readframes(w.getnframes())
        self.block_s = block_s
        self._block_bytes = int(config.STT_SAMPLE_RATE * block_s) * 2
        self.duration_s = len(self._pcm) / 2 / config.STT_SAMPLE_RATE
        self.t0: Optional[float] = None
        self._i = 0

    def drain(self) -> None:
        self.t0, self._i = time.monotonic(), 0  # 連線建立的當下開始計時

    def read(self, timeout: float) -> Optional[bytes]:
        due = self.t0 + (self._i + 1) * self.block_s
        wait = due - time.monotonic()
        if wait > timeout:
            time.sleep(timeout)
            return None
        if wait > 0:
            time.sleep(wait)
        start = self._i * self._block_bytes
        block = self._pcm[start:start + self._block_bytes]
        self._i += 1
        return block + b"\x00" * (self._block_bytes - len(block))


# ---------------------------------------------------------------- 系統取樣

def _cpu_seconds(pid: int) -> Optional[float]:
    out = subprocess.run(["ps", "-o", "cputime=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not out:
        return None
    total = 0.0
    for part in out.split(":"):
        total = total * 60 + float(part)
    return total


def _backend_pid() -> Optional[int]:
    try:
        return int(Path(PID_FILE).read_text().strip())
    except (OSError, ValueError):
        return None


def _server_flush_counts() -> Counter:
    try:
        lines = Path(SERVER_LOG).read_text(errors="ignore").splitlines()
    except OSError:
        return Counter()
    return Counter(m.group(1) for ln in lines if (m := re.match(r"\[flush\] reason=(\w+)", ln)))


def _wait_sessions_idle(url: str = "http://127.0.0.1:8765/health", timeout: float = 5.0) -> None:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if json.load(urllib.request.urlopen(url, timeout=1)).get("sessions_active") == 0:
                time.sleep(0.4)
                return
        except OSError:
            pass
        time.sleep(0.2)


# ---------------------------------------------------------------- 離線量說完的時間點（真人錄音用）

def offline_speech_end(path: Path) -> Optional[float]:
    from make_bench_corpus import speech_span
    import wave
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    span = speech_span(x)
    return None if span is None else span[1] / config.STT_SAMPLE_RATE


# ---------------------------------------------------------------- run

def parse_overrides(items: List[str]) -> dict:
    return {k: float(v) for k, v in (it.split("=", 1) for it in items)}


def run_clip(row: dict, wav: Path, args, vad: SileroVad, hints: List[str]) -> dict:
    client_mode = args.mode == "client"
    block_s = args.block_s if args.block_s else (0.032 if client_mode else config.STT_BLOCK_S)
    pre_delay = args.pre_delay if args.pre_delay is not None else (config.EP_FLUSH_PRE_DELAY_S if client_mode else config.STT_FLUSH_PRE_DELAY_S)
    vad_flush = config.EP_SERVER_BACKUP_FLUSH_S if client_mode else config.STT_VAD_FLUSH_S
    src = WavSource(wav, block_s)
    events, records = [], []
    detector = EndpointDetector(decide=client_mode, **parse_overrides(args.ep))
    client = SttClient(src, lambda e: events.append((time.monotonic(), e)), hints=hints, vad=vad, endpoint=detector,
                       flush=client_mode, vad_flush_s=vad_flush, pre_delay_s=pre_delay, latency_sink=records.append,
                       send_s=args.send_s if args.send_s is not None else (config.EP_SEND_S if client_mode else 0.0))
    stop = threading.Event()
    thread = threading.Thread(target=client.run, args=(stop,), daemon=True)
    thread.start()
    while src.t0 is None and thread.is_alive():
        time.sleep(0.01)
    deadline = src.t0 + src.duration_s + SETTLE_S
    while time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    thread.join(timeout=5)
    t0 = src.t0

    def rel(v):
        return None if v is None else round(v - t0, 4)

    truth = row.get("speech_end_s")
    truth = float(truth) if truth else (offline_speech_end(wav) if row["category"] != "D" else None)
    return {
        "type": "clip", "clip": row["clip"], "category": row["category"], "mode": args.mode, "ref_text": row["ref_text"],
        "expected_actions": json.loads(row["expected_actions"]), "truth_speech_end_s": truth, "duration_s": src.duration_s,
        "finals": [{"t": rel(t), "text": e.get("text", ""), "timing": e.get("timing")} for t, e in events if e.get("type") == "final"],
        "partials": [{"t": rel(t), "text": e.get("text", "")} for t, e in events if e.get("type") == "partial"],
        "errors": [e for _, e in events if e.get("type") == "error"],
        "records": [{**r, **{k: rel(r.get(k)) for k in ("speech_start", "speech_end", "endpoint_at", "flush_sent", "final_recv")}}
                    for r in records],
    }


def cmd_run(args) -> None:
    corpus = Path(args.corpus)
    rows = list(csv.DictReader((corpus / "manifest.csv").open(encoding="utf-8")))
    only = set(args.only.split(",")) if args.only else None
    rows = [r for r in rows if (only is None or r["category"] in only or r["clip"] in only) and (corpus / f"{r['clip']}.wav").exists()]
    if not rows:
        sys.exit(f"{corpus} 沒有可跑的音檔")
    hints = config.STT_HINTS_PATH.read_text(encoding="utf-8").splitlines()
    vad = SileroVad()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    backend = _backend_pid()
    flush_before = _server_flush_counts()
    cpu0 = (time.monotonic(), time.process_time(), _cpu_seconds(backend) if backend else None)
    with out.open("w", encoding="utf-8") as f:
        for i, row in enumerate(rows, 1):
            _wait_sessions_idle()
            result = run_clip(row, corpus / f"{row['clip']}.wav", args, vad, hints)
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()
            last = result["finals"][-1]["text"] if result["finals"] else ""
            print(f"[{i}/{len(rows)}] {row['clip']} finals={len(result['finals'])} 「{last}」", flush=True)
        wall = time.monotonic() - cpu0[0]
        cpu1 = _cpu_seconds(backend) if backend else None
        flush_after = _server_flush_counts()
        meta = {
            "type": "meta", "mode": args.mode, "corpus": str(corpus), "args": vars(args), "wall_s": wall,
            "server_flush": dict(flush_after - flush_before),
            "cpu_pct": {"mac_ai": (time.process_time() - cpu0[1]) / wall * 100,
                        "macos_stt": ((cpu1 - cpu0[2]) / wall * 100) if cpu1 is not None and cpu0[2] is not None else None},
        }
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
    print(f"→ {out}")


# ---------------------------------------------------------------- report

_matcher = FastPathMatcher()


def _norm(text: str) -> str:
    return TRAILING_PUNCT.sub("", normalize_utterance(text)).lower()


def _actions(text: str) -> list:
    out = []
    for clause in _matcher.split_clauses(normalize_utterance(text)):
        m = _matcher.match(clause)
        if m is not None:
            out += [[name, args] for name, args in m.actions]
    return out


def _cer(ref: str, hyp: str) -> float:
    r, h = _norm(ref), _norm(hyp)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i]
        for j, hc in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1] / len(r)


def load(path: str):
    clips, meta = [], {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d["type"] == "clip":
            clips.append(d)
        else:
            meta = d
    return clips, meta


def _wait_ms(clip: dict) -> Optional[float]:
    """決策等待：最後一次 flush 送出的時間 − 它之前 partial 文字最後一次變動的時間。
    只含我們自己的判斷多等了多久（引擎出字慢多少不算），與語料、說話人、真值推定都無關"""
    flushes = [r["flush_sent"] for r in clip["records"] if r.get("flush_sent") is not None]
    if not flushes:
        return None
    sent, prev, changed = flushes[-1], None, None
    for p in sorted(clip["partials"], key=lambda x: x["t"]):
        if p["t"] > sent:
            break
        if p["text"] != prev:
            prev, changed = p["text"], p["t"]
    return None if changed is None else (sent - changed) * 1000


def analyze(clip: dict) -> dict:
    finals = [f for f in clip["finals"] if f["text"].strip()]
    truth = clip["truth_speech_end_s"]
    recs = clip["records"]
    decided = [r for r in recs if r.get("endpoint_at") is not None]
    a: Dict[str, object] = {"clip": clip["clip"], "category": clip["category"], "n_finals": len(finals), "split": len(finals) >= 2,
                            "text": "".join(f["text"] for f in finals)}
    a["e2e_ms"] = (finals[-1]["t"] - truth) * 1000 if finals and truth is not None else None
    a["endpoint_ms"] = (decided[-1]["endpoint_at"] - truth) * 1000 if decided and truth is not None else None
    a["wait_ms"] = _wait_ms(clip)
    srv = [f["timing"] for f in finals if f.get("timing") and f["timing"].get("finish_ms") is not None]
    a["finalize_ms"] = (srv[-1]["pre_delay_ms"] + srv[-1]["finish_ms"]) if srv else None
    a["reasons"] = [r["reason"] for r in recs]
    got = [x for f in finals for x in _actions(f["text"])]
    a["intent_ok"] = json.dumps(got, sort_keys=True, ensure_ascii=False) == json.dumps(clip["expected_actions"], sort_keys=True, ensure_ascii=False)
    a["has_actions"] = bool(got)
    a["cer"] = _cer(clip["ref_text"], a["text"]) if clip["ref_text"] else None
    ref_n = _norm(clip["ref_text"])
    a["tail_ok"] = bool(ref_n) and _norm(a["text"]).endswith(ref_n[-2:])
    a["records_match_finals"] = len(recs) == len(clip["finals"])
    return a


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _p90(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(0.9 * len(xs)))] if xs else None


def _fmt(v, unit="ms"):
    return "-" if v is None else (f"{v:.0f}{unit}" if unit == "ms" else f"{v:.1f}{unit}")


def table(name: str, an: List[dict]) -> None:
    print(f"\n== {name} ==")
    print(f"{'類':<2} {'n':>3} {'E2E中位':>8} {'E2E p90':>8} {'端點中位':>8} {'等待中位':>8} {'收尾中位':>8} {'意圖':>7} {'CER':>6} {'拆句':>4}")
    for cat in sorted({a['category'] for a in an}):
        g = [a for a in an if a["category"] == cat]
        intents = [a["intent_ok"] for a in g]
        cers = [a["cer"] for a in g if a["cer"] is not None]
        print(f"{cat:<2} {len(g):>3} {_fmt(_med([a['e2e_ms'] for a in g])):>8} {_fmt(_p90([a['e2e_ms'] for a in g])):>8} "
              f"{_fmt(_med([a['endpoint_ms'] for a in g])):>8} {_fmt(_med([a['wait_ms'] for a in g])):>8} {_fmt(_med([a['finalize_ms'] for a in g])):>8} "
              f"{sum(intents)}/{len(intents):<4} {(statistics.mean(cers) * 100 if cers else 0):>5.1f}% {sum(a['split'] for a in g):>4}")


def check(base, new) -> int:
    (bc, bm), (nc, nm) = base, new
    ba, na = [analyze(c) for c in bc], [analyze(c) for c in nc]
    of = lambda an, cats: [a for a in an if a["category"] in cats]  # noqa: E731
    results = []

    def gate(code, desc, ok, detail):
        results.append((code, desc, ok, detail))

    A, B_, C, D, F = (of(na, c) for c in ("A", "B", "C", "D", "F"))
    e2e_a = _med([a["e2e_ms"] for a in A])
    base_e2e_a = _med([a["e2e_ms"] for a in of(ba, "A")])
    p90_a = _p90([a["e2e_ms"] for a in A])
    speedup = 1 - e2e_a / base_e2e_a if e2e_a is not None and base_e2e_a else None
    gate("S1", "A 類 E2E 中位≤800ms、p90≤1200ms、比 baseline 快≥40%",
         speedup is not None and e2e_a <= 800 and p90_a <= 1200 and speedup >= 0.4,
         f"中位 {_fmt(e2e_a)}／p90 {_fmt(p90_a)}／baseline {_fmt(base_e2e_a)}／快 {0 if speedup is None else speedup:.0%}")
    wait_limit = (config.EP_SILENCE_COMPLETE_S + config.EP_PARTIAL_STABLE_S) * 1000
    wait_a = _med([a["wait_ms"] for a in A])
    gate("S2", f"A 類決策等待（文字定稿後多等）中位≤{wait_limit:.0f}ms（＝EP_SILENCE_COMPLETE_S＋EP_PARTIAL_STABLE_S）",
         wait_a is not None and wait_a <= wait_limit, f"中位 {_fmt(wait_a)}／p90 {_fmt(_p90([a['wait_ms'] for a in A]))}")
    e2e_b = _med([a["e2e_ms"] for a in B_])
    gate("S3", "B 類 E2E 中位≤2200ms", e2e_b is not None and e2e_b <= 2200, f"{_fmt(e2e_b)}")
    fn, fb = _med([a["finalize_ms"] for a in na]), _med([a["finalize_ms"] for a in ba])
    gate("S4", "server 收尾時間（pre_delay+finish）中位比 baseline 少≥250ms", fn is not None and fb is not None and fb - fn >= 250,
         f"new {_fmt(fn)}／baseline {_fmt(fb)}")
    acf = of(na, "ACF")
    q1 = sum(a["intent_ok"] for a in acf) / max(1, len(acf))
    q1b = sum(a["intent_ok"] for a in of(ba, "ACF")) / max(1, len(of(ba, "ACF")))
    gate("Q1", "A+C+F 意圖正確率≥95% 且≥baseline", q1 >= 0.95 and q1 >= q1b, f"{q1:.0%}／baseline {q1b:.0%}")
    cer_n = statistics.mean([a["cer"] for a in na if a["cer"] is not None] or [0])
    cer_b = statistics.mean([a["cer"] for a in ba if a["cer"] is not None] or [0])
    gate("Q2", "CER ≤ baseline+1.0pt", cer_n <= cer_b + 0.01, f"{cer_n:.1%}／baseline {cer_b:.1%}")
    cut = [a["clip"] for a in B_ if a["split"] or not a["intent_ok"]]
    gate("Q3", "B 類提早截斷 0 次", not cut, f"{len(cut)}/{len(B_)} {cut}")
    tail = [a["clip"] for a in F if not a["tail_ok"]]
    gate("Q4", "F 類句尾字全對", not tail, f"{len(F) - len(tail)}/{len(F)} {tail}")
    slow = [a["clip"] for a in C if a["e2e_ms"] is None or a["e2e_ms"] > 2500]
    maxlen_c = sum(r == "max_len" for a in C for r in a["reasons"])
    gate("R1", "C 類說完後 2.5s 內都斷句、max_len 0 次", not slow and maxlen_c == 0, f"超時 {slow}／max_len {maxlen_c}")
    bad_d = [a["clip"] for a in D if a["has_actions"]]
    maxlen_d = sum(r == "max_len" for a in D for r in a["reasons"])
    gate("R2", "D 類 0 個指令命中、max_len 0 次", not bad_d and maxlen_d == 0, f"命中 {bad_d}／max_len {maxlen_d}／有文字的 final {sum(a['n_finals'] for a in D)} 句")
    vm = [r["vad_ms_mean"] for c in nc for r in c["records"] if r.get("vad_ms_mean") is not None]
    vp = [r["vad_ms_p99"] for c in nc for r in c["records"] if r.get("vad_ms_p99") is not None]
    gate("P1", "VAD 每窗平均<2ms、p99<3ms", bool(vm) and max(vm) < 2 and max(vp) < 3, f"平均 {max(vm or [0]):.2f}ms／p99 {max(vp or [0]):.2f}ms")
    ncpu, bcpu = nm.get("cpu_pct", {}), bm.get("cpu_pct", {})
    cpu_ok = ncpu.get("mac_ai") is not None and ncpu["mac_ai"] < 5 and (
        ncpu.get("macos_stt") is None or bcpu.get("macos_stt") is None or ncpu["macos_stt"] - bcpu["macos_stt"] <= 5)
    gate("P2", "CPU：mac_ai<5%、macos-stt 比 baseline 增加≤5pt", cpu_ok,
         f"mac_ai {ncpu.get('mac_ai', 0):.1f}%／macos-stt {ncpu.get('macos_stt') or 0:.1f}% (baseline {bcpu.get('macos_stt') or 0:.1f}%)")
    flushes = sum(1 for c in nc for r in c["records"] if r.get("reason") != "server_vad")
    srv = nm.get("server_flush", {}).get("client_flush", 0)
    gate("L1", "每個 final 恰一筆計時紀錄；server log 的 client_flush 行數＝client 送出數",
         all(a["records_match_finals"] for a in na) and srv == flushes, f"client {flushes}／server log {srv}")
    print("\n== 驗收判定 ==")
    for code, desc, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {code}  {desc}  →  {detail}")
    failed = [c for c, _, ok, _ in results if not ok]
    print(f"\n{'全部通過' if not failed else '未通過：' + ', '.join(failed)}")
    return 1 if failed else 0


def cmd_report(args) -> None:
    loaded = [load(p) for p in args.files]
    for p, (clips, meta) in zip(args.files, loaded):
        table(f"{p}（mode={meta.get('mode')}）", [analyze(c) for c in clips])
        print(f"   server log 收尾次數：{meta.get('server_flush')}　CPU：{meta.get('cpu_pct')}")
    if args.detail:
        for c in loaded[-1][0]:
            a = analyze(c)
            print(f"  {a['clip']} e2e={_fmt(a['e2e_ms'])} ep={_fmt(a['endpoint_ms'])} fin={_fmt(a['finalize_ms'])} "
                  f"reasons={a['reasons']} ok={a['intent_ok']} 「{a['text']}」")
            if args.trace or not a["intent_ok"] or a["split"]:
                end = c["truth_speech_end_s"] or 0
                for p in c.get("partials", []):
                    print(f"      partial {p['t'] - end:+.2f}s 「{p['text']}」")
                for r in c["records"]:
                    print(f"      flush  {(r['endpoint_at'] or 0) - end:+.2f}s reason={r['reason']} cls={r['class_at_endpoint']} 「{r['partial_at_endpoint']}」")
                for f in c["finals"]:
                    print(f"      final  {f['t'] - end:+.2f}s 「{f['text']}」")
    if len(loaded) == 2:
        sys.exit(check(*loaded))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--corpus", required=True)
    r.add_argument("--mode", choices=["server", "client"], required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--only", help="類別或 clip 名稱，逗號分隔，例如 A,B 或 A01")
    r.add_argument("--block-s", type=float, help="收音幀長（預設 server 0.2／client 0.032）")
    r.add_argument("--send-s", type=float, help="client 模式幾秒的音訊合成一則 WS 訊息（預設 config.EP_SEND_S）")
    r.add_argument("--pre-delay", type=float, help="flush_pre_delay_seconds（預設 config：server 0.4／client EP_FLUSH_PRE_DELAY_S）")
    r.add_argument("--ep", action="append", default=[], metavar="KEY=VAL", help="覆寫 EndpointDetector 參數，例如 silence_complete_s=0.25")
    p = sub.add_parser("report")
    p.add_argument("files", nargs="+")
    p.add_argument("--detail", action="store_true", help="逐段列出（意圖錯誤或被拆句的會附上 partial／flush／final 時間軸）")
    p.add_argument("--trace", action="store_true", help="--detail 時每一段都附時間軸（時間相對於說完的時間點）")
    args = ap.parse_args()
    cmd_run(args) if args.cmd == "run" else cmd_report(args)


if __name__ == "__main__":
    main()
