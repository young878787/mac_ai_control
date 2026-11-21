"""桌面角色動作軌跡量測（擬人化計畫書 R2／R3）：記錄出場時頭、眼的參數，算「無機械感」與「聆聽專注」的指標。

    python scripts/ui_motion.py [--minutes 10] [--rounds 10] [--skip-idle] [--skip-listen]

**量測期間盡量不要動滑鼠**（游標會影響視線）。閒置：每次移動後 3 秒不算，排除的時間超過 10% 判無效；聆聽：游標動過就判無效。
階段：
  閒置：出場後什麼都不送，記錄 --minutes 分鐘（R2：頭部靜止佔比、最長靜止、視線正中、掃視、眨眼）。
  聆聽：--rounds 輪，每輪 8 秒內每秒送一次 partial（模擬持續說話）再送 heard（ignored），輪與輪之間閒置 10 秒（R3：L1～L5；L3 看軌跡的情境欄位，舊軌跡沒有就略過）。
指標由 desktop/dev/motion-metrics.js 計算（和 desktop/test 的模擬測試同一份定義）。
通過標準見 docs/桌面角色擬人化（注視與聆聽）：實作計畫.md §7.1、§7.2。"""
import argparse
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mac_ai import config  # noqa: E402
from mac_ai.services import ServiceError, check_ui_ready, stop_all, ui_service  # noqa: E402
from mac_ai.ui_bus import UiBus  # noqa: E402

WARMUP_S = 8.0          # 出場的 Surprised（3 秒）、surprised 表情與物理穩定
LISTEN_S = 8            # 每輪聆聽的秒數（每秒一個 partial）
GAP_S = 10.0            # 輪與輪之間閒置多久（供比較閒置的頭速度與眨眼率）


def analyze(kind: str, trace_file: Path) -> dict:
    node = shutil.which("node")
    if node is None:
        raise ServiceError("找不到 node（指標計算需要，和 cd desktop && node --test 相同）")
    out = subprocess.run([node, str(config.UI_DIR / "dev" / "motion-metrics.js"), kind, str(trace_file)], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise ServiceError(f"指標計算失敗：{out.stderr.strip()}")
    return json.loads(out.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=10.0, help="閒置階段的長度（分鐘）")
    ap.add_argument("--rounds", type=int, default=10, help="聆聽階段的輪數")
    ap.add_argument("--skip-idle", action="store_true")
    ap.add_argument("--skip-listen", action="store_true")
    args = ap.parse_args()
    problem = check_ui_ready()
    if problem:
        print(f"❌ {problem}")
        return 1
    cap = Path(tempfile.mkdtemp(prefix="ui_motion_"))
    bus = UiBus(port=0, token=secrets.token_urlsafe(32))
    bus.bind(lambda msg: None, lambda: {"wake_enabled": True, "session_s": 3600.0, "paused": False, "remaining_s": 0.0})
    bus.start()
    ui = ui_service(bus.url, bus.health_url)
    ui.env = {**ui.env, "MAC_AI_UI_CAPTURE_DIR": str(cap)}
    results: dict[str, list[dict]] = {}

    def record(name: str, seconds_fn) -> Path:
        """開始記錄 → 執行 seconds_fn（送事件、睡覺）→ 停止並取回軌跡檔"""
        bus.publish({"type": "debug_trace", "cmd": "start", "name": name})
        time.sleep(0.5)
        seconds_fn()
        bus.publish({"type": "debug_trace", "cmd": "stop", "name": name})
        path = cap / f"{name}.json"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if path.exists():
                time.sleep(0.5)  # 等檔案寫完
                try:
                    if json.loads(path.read_text()) is not None:
                        return path
                except json.JSONDecodeError:
                    pass
            time.sleep(0.2)
        raise ServiceError(f"等不到軌跡檔：{path}")

    def report(label: str, kind: str, path: Path) -> None:
        data = analyze(kind, path)
        results[label] = data["checks"]
        m = data["metrics"]
        print(f"\n── {label}（軌跡 {path}）")
        print("  " + json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items() if k != "arriveMs"}, ensure_ascii=False))
        for c in data["checks"]:
            tag = "SKIP" if c["pass"] is None else ("PASS" if c["pass"] else "FAIL")
            print(f"  {tag}  {c['name']}：{c['detail']}")

    try:
        ui.start()
        ui.wait_ready(config.UI_START_TIMEOUT_S)
        time.sleep(8)  # 模型載入、貼圖上傳
        bus.publish({"type": "session", "remaining_s": 3600.0})  # 出場並保持（一小時）
        time.sleep(WARMUP_S)
        if not args.skip_idle:
            print(f"閒置階段 {args.minutes:g} 分鐘（不要動滑鼠）…")

            def idle():
                end = time.monotonic() + args.minutes * 60
                while time.monotonic() < end:
                    time.sleep(min(60.0, max(0.0, end - time.monotonic())))
                    print(f"  … 剩 {max(0.0, end - time.monotonic()) / 60:.1f} 分鐘", flush=True)

            report("R2 閒置", "idle", record("idle", idle))
        if not args.skip_listen:
            print(f"\n聆聽階段 {args.rounds} 輪（不要動滑鼠）…")

            def listen():
                for r in range(args.rounds):
                    for _ in range(LISTEN_S):
                        bus.publish({"type": "partial", "text": "露西婭", "wake": True})
                        time.sleep(1.0)
                    bus.publish({"type": "heard", "text": "露西婭", "status": "ignored", "command": ""})
                    time.sleep(GAP_S)

            report("R3 聆聽", "listen", record("listen", listen))
        failed = [f"{label}：{c['name']}" for label, checks in results.items() for c in checks if c["pass"] is False]
        print("\n" + ("全部通過" if not failed else "未通過 %d 項：\n  %s" % (len(failed), "\n  ".join(failed))))
        return 1 if failed else 0
    except ServiceError as e:
        print(f"❌ {e}")
        return 1
    finally:
        stop_all([ui])
        bus.stop()


if __name__ == "__main__":
    sys.exit(main())
