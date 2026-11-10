#!/usr/bin/env python3
"""把 logs/runtime/ 的紀錄整理成「一行一筆」的表格，一眼看出每句話從說完到執行完等了多久、指令怎麼走。唯讀。
語音指令的收音計時（latency）與指令執行（commands）用 utt_id 對成同一行；沒有語音的指令（UI 打字、清單點選）另列。

  python scripts/log_view.py                    最近一次啟動（run_id）的指令／收音計時／桌面角色延遲
  python scripts/log_view.py --run 20261109-221129   指定哪一次啟動
  python scripts/log_view.py --all              全部啟動
  python scripts/log_view.py -n 50              每種最多列最後 50 筆（預設 20）
"""
import argparse
import json
import statistics
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mac_ai import config  # noqa: E402

NOTE = ("收尾判斷：complete＝規則命中，靜音 {c}s 即收｜default＝不確定（如交給 LLM），等 {d}s｜"
        "incomplete＝句尾沒說完，等 {i}s｜server_vad＝server 自己斷句（備援）\n"
        "說完→執行完（約）＝說完→定稿＋排隊＋指令執行；同一句用 utt_id 對起來").format(
    c=config.EP_SILENCE_COMPLETE_S, d=config.EP_SILENCE_DEFAULT_S, i=config.EP_SILENCE_INCOMPLETE_S)


def read_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass  # 寫到一半的行跳過
    return rows


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def clip(text: str, limit: int) -> str:
    out, w = "", 0
    for c in text:
        cw = width(c)
        if w + cw > limit - 1:
            return out + "…"
        out, w = out + c, w + cw
    return out


def table(headers: List[str], rows: List[List[str]]) -> None:
    cols = [max(width(str(x)) for x in col) for col in zip(headers, *rows)] if rows else [width(h) for h in headers]
    for row in [headers] + rows:
        print("  ".join(str(x) + " " * (c - width(str(x))) for x, c in zip(row, cols)).rstrip())


def ms(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.0f}ms"


def hms(row: dict) -> str:
    return str(row.get("ts_iso", ""))[11:19]


def command_cells(c: dict) -> List[str]:
    """一個指令的 [結果, 路徑, 第一行結果]"""
    steps = c.get("steps", [])
    path = " → ".join(f"{s.get('kind')} {ms(s.get('ms'))}" + (f"（LLM {ms(s['llm_ms'])}）" if s.get("llm_ms") else "") for s in steps)
    first = c.get("error") or (steps[0]["logs"][0] if steps and steps[0].get("logs") else (c.get("replies") or [""])[0])
    return ["✅" if c.get("ok") else "❌", path or "（執行出錯）", clip(first, 46)]


def voice_rows(latency: List[dict], commands: List[dict]):
    """一句語音一行：收音計時（latency）＋指令執行（commands），用 (run_id, utt_id) 對起來。
    回傳 (語音各列, 沒有對到語音的指令——UI 打字、清單點選、舊紀錄)"""
    by_id = {(c.get("run_id"), c["utt_id"]): c for c in commands if c.get("utt_id") is not None}
    rows, used = [], set()
    for lat in latency:
        key = (lat.get("run_id"), lat.get("utt_id"))
        cmd = by_id.get(key) if lat.get("utt_id") is not None else None
        head = [hms(lat), clip(str(lat.get("final_text") or ""), 24), lat.get("reason", ""), ms(lat.get("e2e_ms"))]
        if cmd is None:
            rows.append(head + ["-", "-", "-", "—", "（沒有指令：未喚醒、只說喚醒詞或雜訊）", ""])
            continue
        used.add(key)
        parts = (lat.get("e2e_ms"), cmd.get("queue_ms"), cmd.get("total_ms"))
        total = None if any(v is None for v in parts) else sum(parts)
        rows.append(head + [ms(cmd.get("queue_ms")), ms(cmd.get("total_ms")), ms(total)] + command_cells(cmd))
    return rows, [c for c in commands if (c.get("run_id"), c.get("utt_id")) not in used]


def other_command_rows(commands: List[dict]) -> List[List[str]]:
    return [[hms(c), clip(c.get("command", ""), 28), ms(c.get("total_ms"))] + command_cells(c) for c in commands]


def ui_rows(rows: List[dict]) -> List[List[str]]:
    by: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by[r.get("type", "?")].append(r)
    out = []
    for kind, g in sorted(by.items(), key=lambda kv: -len(kv[1])):
        apply = sorted(r["apply_ms"] for r in g if r.get("apply_ms") is not None)
        bus = [r["bus_ms"] for r in g if r.get("bus_ms") is not None]
        out.append([kind, str(len(g)), ms(statistics.median(bus)) if bus else "-",
                    ms(statistics.median(apply)) if apply else "-", ms(apply[min(len(apply) - 1, int(len(apply) * 0.9))]) if apply else "-",
                    ms(apply[-1]) if apply else "-"])
    return out


def section(title: str, headers: List[str], rows: List[List[str]], n: int, note: str = "", total: Optional[int] = None) -> None:
    shown = rows[-n:]
    total = len(rows) if total is None else total  # ui 是彙總表：列數是訊息種類，筆數要另外給
    more = f"（共 {total} 筆，只列最後 {n} 列）" if len(rows) > n else f"（{total} 筆）"
    print(f"\n[{title}] {more}")
    if not rows:
        print("  沒有紀錄")
        return
    table(headers, shown)
    if note:
        print(note)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="只看這個 run_id（預設：最近一次啟動）")
    ap.add_argument("--all", action="store_true", help="全部啟動都列")
    ap.add_argument("-n", type=int, default=20, help="每種最多列最後幾筆")
    ap.add_argument("--dir", default=str(config.RUNTIME_LOG_DIR), help="紀錄資料夾（預設 logs/runtime）")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    commands, latency, ui = (read_jsonl(d / f) for f in ("commands.jsonl", "latency.jsonl", "ui.jsonl"))
    runs = sorted({r["run_id"] for r in commands + latency + ui if r.get("run_id")})
    if not runs:
        print(f"{d} 裡沒有帶 run_id 的紀錄（先跑 python main.py 或 --ui）")
        return 1
    chosen = None if args.all else (args.run or runs[-1])
    if chosen is not None and chosen not in runs:
        print(f"找不到 run_id {chosen}；現有：{', '.join(runs)}")
        return 1
    pick = (lambda rows: rows) if chosen is None else (lambda rows: [r for r in rows if r.get("run_id") == chosen])
    print(f"== {'全部啟動：' + str(len(runs)) + ' 次' if chosen is None else '啟動 ' + chosen}（{d}）==")
    voice, others = voice_rows(pick(latency), pick(commands))
    section("語音指令：收音計時＋指令執行（一句一行）",
            ["時間", "辨識文字", "收尾判斷", "說完→定稿", "排隊", "指令執行", "說完→執行完", "結果", "路徑（各步耗時）", "第一行結果"],
            voice, args.n, NOTE)
    section("其他指令：沒有對應語音（UI 打字、清單點選、舊紀錄）", ["時間", "指令", "總耗時", "結果", "路徑（各步耗時）", "第一行結果"],
            other_command_rows(others), args.n)
    section("桌面角色 ui.jsonl（依訊息種類彙總）", ["訊息", "次數", "後端→Electron 中位", "→畫面套用 中位", "p90", "最大"],
            ui_rows(pick(ui)), args.n, total=len(pick(ui)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
