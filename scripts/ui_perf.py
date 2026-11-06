"""桌面角色介面的效能量測：Electron 全部程序的 CPU／記憶體，以及進退場動畫的幀間隔。

    python scripts/ui_perf.py --seconds 30

量測方式：每個階段先等穩定，再在 --seconds 內取兩次 `ps -o cputime`，用 CPU 時間差除以牆鐘時間（macOS 的 %cpu 是整個生命週期平均，不能取樣）。
階段：潛伏閒置 → 出場（會話中、游標不動）→ 反覆進退場取幀間隔。通過標準見 docs/桌面角色介面與喚醒會話：實作計畫.md E8。"""
import argparse
import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mac_ai import config  # noqa: E402
from mac_ai.services import check_ui_ready, stop_all, ui_service  # noqa: E402
from mac_ai.ui_bus import UiBus  # noqa: E402

LIMITS = {"peek_cpu": 3.0, "out_cpu": 15.0, "rss_mb": 700.0, "anim_p95_ms": 34.0}


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


def sample(seconds: float):
    a = {pid: cpu for pid, _, cpu in electron_processes()}
    t0 = time.monotonic()
    time.sleep(seconds)
    rows = electron_processes()
    wall = time.monotonic() - t0
    cpu = sum(max(0.0, c - a.get(pid, c)) for pid, _, c in rows) / wall * 100
    rss = sum(r for _, r, _ in rows) / 1024
    return cpu, rss, len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=30.0)
    args = ap.parse_args()
    problem = check_ui_ready()
    if problem:
        print(f"❌ {problem}")
        return 1
    cap = Path(tempfile.mkdtemp(prefix="ui_perf_"))
    bus = UiBus(port=0, token=secrets.token_urlsafe(32))
    bus.bind(lambda msg: None, lambda: {"wake_enabled": True, "session_s": 3600.0, "paused": False, "remaining_s": 0.0})
    bus.start()
    ui = ui_service(bus.url, bus.health_url)
    ui.env = {**ui.env, "MAC_AI_UI_CAPTURE_DIR": str(cap)}
    try:
        ui.start()
        ui.wait_ready(config.UI_START_TIMEOUT_S)
        time.sleep(8)  # 模型載入與貼圖上傳後穩定
        peek_cpu, peek_rss, n = sample(args.seconds)
        print(f"潛伏閒置：CPU {peek_cpu:.1f}%｜RSS {peek_rss:.0f} MB｜{n} 個程序")
        bus.publish({"type": "session", "remaining_s": 3600.0})
        time.sleep(4)
        out_cpu, out_rss, n = sample(args.seconds)
        print(f"出場（會話中）：CPU {out_cpu:.1f}%｜RSS {out_rss:.0f} MB｜{n} 個程序")
        for _ in range(6):  # 反覆進退場取動畫幀間隔
            bus.publish({"type": "session", "remaining_s": 0})
            time.sleep(3.6)
            bus.publish({"type": "session", "remaining_s": 3600.0})
            time.sleep(1.6)
        bus.publish({"type": "debug_capture", "name": "perf"})
        time.sleep(1)
        perf = json.loads((cap / "perf.json").read_text())["state"]["perf"]
        print(f"動畫幀（{perf['animFrames']} 幀）：間隔中位 {perf['animIntervalMedian']:.1f} ms｜p95 {perf['animIntervalP95']:.1f} ms｜繪製耗時 p95 {perf['renderMsP95']:.1f} ms")
        checks = {
            "潛伏 CPU": (peek_cpu, LIMITS["peek_cpu"]), "出場 CPU": (out_cpu, LIMITS["out_cpu"]),
            "RSS": (max(peek_rss, out_rss), LIMITS["rss_mb"]), "動畫幀間隔 p95": (perf["animIntervalP95"], LIMITS["anim_p95_ms"]),
        }
        ok = True
        for name, (value, limit) in checks.items():
            passed = value <= limit
            ok &= passed
            print(f"{'PASS' if passed else 'FAIL'}  {name}：{value:.1f} ≤ {limit:g}")
        return 0 if ok else 1
    finally:
        stop_all([ui])
        bus.stop()


if __name__ == "__main__":
    sys.exit(main())
