#!/usr/bin/env python3
"""錄真人測試語料：依 bench/human/manifest.csv 一段一段錄，存成 bench/human/<clip>.wav（16 kHz mono）。

  python scripts/record_human.py                 # 從還沒錄的開始，全部錄完
  python scripts/record_human.py --only C,D      # 只錄某些類別或 clip（例如 C03）
  python scripts/record_human.py --redo A05      # 重錄
  python scripts/record_human.py --list          # 只看進度

每段流程：顯示要說的句子與情境 → 按 Enter → 倒數 → 開始錄（前 1 秒保持安靜、再開口；說完就安靜等到結束）。
錄完可選擇：Enter＝接受、r＝重錄、s＝跳過、q＝離開。D 類（只有噪音）不要說話。
錄完後跑：python scripts/stt_bench.py run --corpus bench/human --mode server --out logs/human_base.jsonl
         python scripts/stt_bench.py run --corpus bench/human --mode client --out logs/human_new.jsonl
         python scripts/stt_bench.py report logs/human_base.jsonl logs/human_new.jsonl
"""
import argparse
import csv
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HUMAN = ROOT / "bench" / "human"
SR = 16000


def seconds_of(note: str) -> float:
    m = re.search(r"錄 (\d+(?:\.\d+)?) 秒", note)
    return float(m.group(1)) if m else 8.0


def record(seconds: float, device=None) -> np.ndarray:
    import sounddevice as sd
    data = sd.rec(int(seconds * SR), samplerate=SR, channels=1, dtype="int16", device=device)
    t0 = time.time()
    while time.time() - t0 < seconds:
        left = seconds - (time.time() - t0)
        print(f"\r  ● 錄音中… 剩 {left:4.1f} 秒", end="", flush=True)
        time.sleep(0.1)
    sd.wait()
    print("\r  ■ 錄完了" + " " * 20)
    return data[:, 0]


def save(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.astype(np.int16).tobytes())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="類別或 clip，逗號分隔")
    ap.add_argument("--redo", help="要重錄的 clip，逗號分隔")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--device", type=int)
    args = ap.parse_args()

    rows = list(csv.DictReader((HUMAN / "manifest.csv").open(encoding="utf-8")))
    redo = set(args.redo.split(",")) if args.redo else set()
    only = set(args.only.split(",")) if args.only else None
    done = {r["clip"] for r in rows if (HUMAN / f"{r['clip']}.wav").exists()}
    if args.list:
        for r in rows:
            print(f"{'✓' if r['clip'] in done else '·'} {r['clip']}  {r['ref_text'] or '（只有噪音）'}  {r['noise']}")
        print(f"\n已錄 {len(done)}/{len(rows)}")
        return
    todo = [r for r in rows if (r["clip"] in redo) or (r["clip"] not in done and (only is None or r["category"] in only or r["clip"] in only))]
    print(f"要錄 {len(todo)} 段（已有 {len(done)} 段）。每段前 1 秒請保持安靜。\n")
    for i, r in enumerate(todo, 1):
        while True:
            print(f"[{i}/{len(todo)}] {r['clip']}（{r['category']} 類）")
            print(f"  情境：{r['note']}")
            if r["noise"]:
                print(f"  背景：{r['noise']}（請先準備好）")
            print(f"  要說：{r['ref_text'] or '（不要說話）'}")
            ans = input("  準備好按 Enter 開始（s 跳過／q 離開）：").strip().lower()
            if ans == "q":
                return
            if ans == "s":
                break
            for n in (3, 2, 1):
                print(f"  {n}…", end="\r", flush=True)
                time.sleep(0.6)
            pcm = record(seconds_of(r["note"]), args.device)
            peak = int(np.abs(pcm).max())
            print(f"  音量峰值 {peak}/32767" + ("  ⚠️ 太小聲或沒收到音" if peak < 800 and r["category"] != "D" else ""))
            ans = input("  Enter＝接受、r＝重錄、s＝跳過、q＝離開：").strip().lower()
            if ans == "q":
                return
            if ans == "r":
                continue
            if ans != "s":
                save(HUMAN / f"{r['clip']}.wav", pcm)
                print("  已儲存\n")
            break
    print("全部完成")


if __name__ == "__main__":
    if sys.stdin.isatty() is False:
        sys.exit("需要在互動終端機執行")
    main()
