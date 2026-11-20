"""桌面角色介面的效能量測：Electron 全部程序的 CPU／記憶體，以及各階段的幀間隔與停格。

    python scripts/ui_perf.py [--seconds 60]

CPU 用 `ps -o cputime` 每秒取樣一次（CPU 時間差 ÷ 牆鐘時間；macOS 的 %cpu 是整個生命週期平均，不能取樣），
取樣值是「單核 %」，報每個階段的平均與每秒峰值，並換算成整台電腦（÷ 核心數）；**CPU 的通過判定用整台電腦 %**。
階段：潛伏停格 → 出場 60 fps（期間輪流觸發每一種演出）→ 反覆進退場 → 退場後停格、事件喚醒。
通過標準見 docs/桌面角色擬人化（注視與聆聽）：實作計畫.md §7.2 R4（沿用 docs/桌面角色模型升級（fullrig03）：實作計畫.md §9.2 R4～R6）。"""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mac_ai import config  # noqa: E402
from mac_ai.services import check_ui_ready, stop_all, ui_service  # noqa: E402
from mac_ai.ui_bus import UiBus  # noqa: E402
from ui_demo import FAIL_RESULT, OK_RESULT, REPLY_RESULT  # noqa: E402

# CPU 是整台電腦 %（B5）：峰值 5.0＝8 核 M1 的單核 40%；潛伏停格 0.125＝單核 1%
LIMITS = {"peak_cpu": 5.0, "frozen_cpu": 0.125, "rss_mb": 700.0, "interval_median": (16.2, 17.2), "interval_p95_ms": 18.0,
          "drop_ratio": 0.005, "js_p95_ms": 8.0}
SETTLE_MAX_S = 3.0          # 要和 desktop/renderer/role/app.js 的 SETTLE.maxMs 一致
SLEEPY_S, RETREAT_S = 1.5, 0.8   # 要和 desktop/domain/character.js 的 SLEEPY_MS、RETREAT_MS 一致
HEAD = (180, 182)           # 出場時頭部、身體的視窗座標（模型頂端 y＝560−420）
BODY = (180, 380)


def parse_cputime(text: str) -> float:
    parts = [float(p) for p in text.split(":")]
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def electron_processes():
    """回傳 [(pid, rss_kb, cpu_seconds)]：命令列含 desktop 的 Electron.app 的全部程序（主程序與各 Helper）"""
    out = subprocess.run(["ps", "-axo", "pid=,rss=,cputime=,command="], capture_output=True, text=True).stdout
    rows = []
    marker = str(config.UI_DIR / "node_modules" / "electron" / "dist" / "Electron.app")
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)", line)
        if m and marker in m.group(4):
            rows.append((int(m.group(1)), int(m.group(2)), parse_cputime(m.group(3))))
    return rows


class Sampler:
    """背景每秒取樣一次 CPU（單核 %）與 RSS；with 區塊結束後讀 samples"""

    def __init__(self):
        self.samples: list[float] = []
        self.rss = 0.0
        self._stop = threading.Event()

    def _run(self):
        prev, t0 = {pid: cpu for pid, _, cpu in electron_processes()}, time.monotonic()
        while not self._stop.wait(1.0):
            rows, t1 = electron_processes(), time.monotonic()
            self.samples.append(sum(max(0.0, c - prev.get(pid, c)) for pid, _, c in rows) / (t1 - t0) * 100)
            self.rss = max(self.rss, sum(r for _, r, _ in rows) / 1024)
            prev, t0 = {pid: c for pid, _, c in rows}, t1

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()

    @property
    def avg(self):
        return sum(self.samples) / len(self.samples) if self.samples else 0.0

    @property
    def peak(self):
        return max(self.samples, default=0.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=60.0, help="出場 60 fps 階段的長度")
    ap.add_argument("--peek-seconds", type=float, default=20.0, help="潛伏停格階段的長度")
    args = ap.parse_args()
    problem = check_ui_ready()
    if problem:
        print(f"❌ {problem}")
        return 1
    cores = os.cpu_count() or 8
    cap = Path(tempfile.mkdtemp(prefix="ui_perf_"))
    bus = UiBus(port=0, token=secrets.token_urlsafe(32))
    bus.bind(lambda msg: None, lambda: {"wake_enabled": True, "session_s": 3600.0, "paused": False, "remaining_s": 0.0})
    bus.start()
    ui = ui_service(bus.url, bus.health_url)
    ui.env = {**ui.env, "MAC_AI_UI_CAPTURE_DIR": str(cap)}
    n = 0

    def state() -> dict:
        """讀畫面狀態；perf 是上次讀取以來的統計（讀完會清空）"""
        nonlocal n
        n += 1
        bus.publish({"type": "debug_capture", "name": f"s{n}"})
        path = cap / f"s{n}.json"
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.1)
        return json.loads(path.read_text())["state"]

    def cpu_line(label: str, s: Sampler) -> None:
        print(f"{label}：CPU 平均 {s.avg:.1f}%（整台 {s.avg / cores:.2f}%）｜每秒峰值 {s.peak:.1f}%（整台 {s.peak / cores:.2f}%）｜RSS {s.rss:.0f} MB")

    checks: dict[str, tuple[bool, str]] = {}
    try:
        ui.start()
        ui.wait_ready(config.UI_START_TIMEOUT_S)
        time.sleep(8)  # 模型載入、貼圖上傳、潛伏收尾停格
        state()
        with Sampler() as peek:
            time.sleep(args.peek_seconds)
        st = state()
        cpu_line("潛伏停格", peek)
        print(f"  期間繪製 {st['perf']['frames']} 幀、停格中 {st['perf']['frozen']}")
        checks["潛伏停格 CPU 平均（整台）"] = (peek.avg / cores <= LIMITS["frozen_cpu"], f"{peek.avg / cores:.3f}% ≤ {LIMITS['frozen_cpu']:g}%")

        # 出場 60 fps：輪流觸發每一種演出（點身體、點頭部、長執行托腮、成功、說話、連續失敗）
        bus.publish({"type": "session", "remaining_s": 3600.0})
        time.sleep(3)
        state()
        script = [([{"type": "debug_click", "x": BODY[0], "y": BODY[1]}], 5), ([{"type": "debug_click", "x": HEAD[0], "y": HEAD[1]}], 5),
                  ([{"type": "executing", "command": "x"}], 3.5), ([OK_RESULT], 4), ([{"type": "executing", "command": "y"}, REPLY_RESULT], 6),
                  ([{"type": "executing", "command": "z"}, FAIL_RESULT], 4), ([{"type": "executing", "command": "z"}, FAIL_RESULT], 4)]
        with Sampler() as out:
            end, i = time.monotonic() + args.seconds, 0
            while time.monotonic() < end:
                msgs, wait = script[i % len(script)]
                for m in msgs:
                    bus.publish(m)
                time.sleep(min(wait, max(0.0, end - time.monotonic())))
                i += 1
        p = state()["perf"]
        cpu_line(f"出場 60 fps（{args.seconds:.0f} 秒、{i} 段演出）", out)
        drop_ratio = p["drops"] / max(1, p["frames"])
        print(f"  {p['frames']} 幀：間隔中位 {p['intervalMedian']:.2f} ms｜p95 {p['intervalP95']:.2f} ms｜掉格（> 25 ms）{p['drops']}（{drop_ratio:.2%}）"
              f"｜每幀 JS p95 {p['renderMsP95']:.2f} ms｜各段平均 {p['parts']}")
        lo, hi = LIMITS["interval_median"]
        checks["出場 CPU 每秒峰值（整台）"] = (out.peak / cores <= LIMITS["peak_cpu"], f"{out.peak / cores:.2f}% ≤ {LIMITS['peak_cpu']:g}%")
        checks["幀間隔中位"] = (lo <= p["intervalMedian"] <= hi, f"{p['intervalMedian']:.2f} 在 {lo}～{hi}")
        checks["幀間隔 p95"] = (p["intervalP95"] <= LIMITS["interval_p95_ms"], f"{p['intervalP95']:.2f} ≤ {LIMITS['interval_p95_ms']:g}")
        checks["掉格比例"] = (drop_ratio <= LIMITS["drop_ratio"], f"{drop_ratio:.2%} ≤ {LIMITS['drop_ratio']:.1%}")
        checks["每幀 JS p95"] = (p["renderMsP95"] <= LIMITS["js_p95_ms"], f"{p['renderMsP95']:.2f} ≤ {LIMITS['js_p95_ms']:g}")

        with Sampler() as trans:  # 反覆進退場
            for _ in range(6):
                bus.publish({"type": "session", "remaining_s": 0})
                time.sleep(3.6)
                bus.publish({"type": "session", "remaining_s": 3600.0})
                time.sleep(1.6)
        p = state()["perf"]
        cpu_line("反覆進退場", trans)
        print(f"  動畫幀 {p['animFrames']}：間隔中位 {p['animIntervalMedian']:.2f} ms｜p95 {p['animIntervalP95']:.2f} ms")
        checks["進退場 CPU 每秒峰值（整台）"] = (trans.peak / cores <= LIMITS["peak_cpu"], f"{trans.peak / cores:.2f}% ≤ {LIMITS['peak_cpu']:g}%")
        checks["進退場幀間隔 p95"] = (p["animIntervalP95"] <= LIMITS["interval_p95_ms"], f"{p['animIntervalP95']:.2f} ≤ {LIMITS['interval_p95_ms']:g}")

        # 退場 → 停格：會話結束後 sleepy＋退場動畫，再加收尾上限之內要停格，之後幀數不再增加
        bus.publish({"type": "session", "remaining_s": 0})
        t0 = time.monotonic()
        frozen_at = None
        while time.monotonic() - t0 < SLEEPY_S + RETREAT_S + SETTLE_MAX_S + 2:
            if state()["perf"]["frozen"]:
                frozen_at = time.monotonic() - t0
                break
        f1 = state()["frames"]
        time.sleep(1.0)
        f2 = state()["frames"]
        limit = SLEEPY_S + RETREAT_S + SETTLE_MAX_S + 0.5  # 0.5 秒是讀取狀態本身的延遲
        print(f"退場後停格：{'未停格' if frozen_at is None else f'{frozen_at:.2f} 秒'}；停格後 1 秒內幀數 {f1} → {f2}")
        checks["退場後停格"] = (frozen_at is not None and frozen_at <= limit, f"{frozen_at} ≤ {limit:g} 秒")
        checks["停格後不再繪製"] = (f2 == f1, f"{f1} → {f2}")
        bus.publish({"type": "partial", "text": "露西婭", "wake": True})
        time.sleep(0.3)
        st = state()
        print(f"事件喚醒：0.3 秒後停格 {st['perf']['frozen']}、繪製 {st['perf']['frames']} 幀、間隔中位 {st['perf']['intervalMedian']}")
        checks["事件喚醒恢復 60 fps"] = (not st["perf"]["frozen"] and st["perf"]["frames"] >= 12, f"{st['perf']['frames']} 幀 ≥ 12")
        rss = max(peek.rss, out.rss, trans.rss)
        checks["RSS"] = (rss <= LIMITS["rss_mb"], f"{rss:.0f} ≤ {LIMITS['rss_mb']:g} MB")

        ok = True
        for name, (passed, detail) in checks.items():
            ok &= passed
            print(f"{'PASS' if passed else 'FAIL'}  {name}：{detail}")
        return 0 if ok else 1
    finally:
        stop_all([ui])
        bus.stop()


if __name__ == "__main__":
    sys.exit(main())
