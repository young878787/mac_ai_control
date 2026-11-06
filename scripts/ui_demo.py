"""桌面角色介面的示範與開發工具：不啟動 STT／LLM，依序播放每個狀態的假事件。

    python scripts/ui_demo.py                  啟動 Electron 並播放一輪（播完結束）
    python scripts/ui_demo.py --hold           播完後保持執行，可手動操作（打字、點擊、右鍵），Ctrl+C 結束
    python scripts/ui_demo.py --capture DIR    每個狀態把視窗存成 DIR/<名稱>.png（開發驗證用）
    python scripts/ui_demo.py --no-launch      只開事件匯流排，Electron 自己用 npm start 啟動（會印出需要的環境變數）
    python scripts/ui_demo.py --focus-test N   輸入框開關 N 次，確認每次前景 App 都沒被搶走（取代人工的 E5 前半段）
    python scripts/ui_demo.py --interact-test  注入點擊與打字，確認 UI 會把 wake／command 送回後端（走完整條 Electron → 後端的路）
"""
import argparse
import json
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mac_ai import config  # noqa: E402
from mac_ai.services import ServiceError, check_ui_ready, stop_all, ui_service  # noqa: E402
from mac_ai.ui_bus import UiBus  # noqa: E402

OK_RESULT = {"type": "result", "command": "打開小算盤", "ok": True, "total_ms": 12.0, "replies": [],
             "steps": [{"kind": "fast", "text": "打開小算盤", "status": "ok", "logs": ["✅ 已開啟 小算盤"]}]}
FAIL_RESULT = {"type": "result", "command": "打開不存在的程式", "ok": False, "total_ms": 30.0, "replies": [],
               "steps": [{"kind": "fast", "text": "打開不存在的程式", "status": "exec_failed", "logs": ["❌ 找不到 App：不存在的程式"]}]}
REPLY_RESULT = {"type": "result", "command": "今天天氣如何", "ok": True, "total_ms": 900.0, "steps": [],
                "replies": ["今天台北多雲，氣溫約 24 度，晚上可能有短暫陣雨。"]}


def play(bus: UiBus, wait, capture) -> None:
    pub = bus.publish
    wait(1.5, "peek")
    capture("01-peek")
    pub({"type": "partial", "text": "露西婭", "wake": True})
    wait(1.2, "appear")
    capture("02-appear")
    pub({"type": "heard", "text": "露西婭", "status": "armed", "command": ""})
    pub({"type": "session", "remaining_s": config.WAKE_SESSION_S})
    pub({"type": "partial", "text": "打開小算", "wake": False})
    wait(0.8, "listening")
    capture("03-listening")
    pub({"type": "heard", "text": "打開小算盤", "status": "command", "command": "打開小算盤"})
    pub({"type": "session", "remaining_s": config.WAKE_SESSION_S})
    pub({"type": "executing", "command": "打開小算盤"})
    wait(0.6, "executing")
    capture("04-executing")
    pub(OK_RESULT)
    wait(1.0, "success")
    capture("05-success")
    pub({"type": "executing", "command": "打開不存在的程式"})
    wait(0.4, "executing")
    pub(FAIL_RESULT)
    wait(1.0, "failure")
    capture("06-failure")
    pub({"type": "executing", "command": "今天天氣如何"})
    pub(REPLY_RESULT)
    wait(1.2, "reply")
    capture("07-reply")
    pub({"type": "session", "remaining_s": 0})
    wait(0.8, "session ended")
    capture("08-sleepy")
    wait(2.5, "retreat")
    capture("09-retreat")


def frontmost_app() -> str:
    out = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
                         capture_output=True, text=True, timeout=10)
    return out.stdout.strip()


def focus_test(bus: UiBus, rounds: int, capture_dir: Path) -> bool:
    """開關輸入框 rounds 次；通過標準：每次開著時輸入框確實有焦點，且前景 App 前後都跟一開始一樣"""
    before = frontmost_app()
    print(f"前景 App：{before}")
    kept_open = kept_closed = opened = 0
    bus.publish({"type": "session", "remaining_s": 0})  # 先出場（打字觸發），避免依賴會話
    for i in range(rounds):
        bus.publish({"type": "debug_toggle_input"})
        time.sleep(0.6)
        bus.publish({"type": "debug_capture", "name": f"focus-open-{i}"})
        time.sleep(0.5)
        info = json.loads((capture_dir / f"focus-open-{i}.json").read_text())
        opened += bool(info["state"] and info["state"]["input"] and info["state"]["inputFocused"])
        kept_open += frontmost_app() == before
        bus.publish({"type": "debug_toggle_input"})
        time.sleep(0.5)
        kept_closed += frontmost_app() == before
    print(f"輸入框成功開啟並取得焦點：{opened}/{rounds}；開著時前景未變：{kept_open}/{rounds}；關閉後前景未變：{kept_closed}/{rounds}")
    return opened == rounds and kept_open == rounds and kept_closed == rounds


def interact_test(bus: UiBus, received: list, capture_dir: Path) -> bool:
    """點頭部 → 應送出 wake；輸入框打字送出 → 應送出 command；點身體 → 不送訊息、播 Happy"""
    results = []

    def state(name: str) -> dict:
        bus.publish({"type": "debug_capture", "name": name})
        time.sleep(0.6)
        return json.loads((capture_dir / f"{name}.json").read_text())["state"]

    def check(label: str, ok: bool) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        results.append(ok)

    bus.publish({"type": "partial", "text": "露西婭", "wake": True})  # 先出場
    time.sleep(1.5)
    top = state("interact-0")["poseT"]
    check("出場（poseT = 1）", abs(top - 1.0) < 0.01)
    head_y = 140 + 0.10 * 420  # 出場時模型頂端 y＝視窗高 560 − 模型高 420
    received.clear()
    bus.publish({"type": "debug_click", "x": 180, "y": head_y})
    time.sleep(0.8)
    check("點頭部 → 後端收到 wake", {"type": "wake"} in received)
    received.clear()
    bus.publish({"type": "debug_toggle_input"})
    time.sleep(0.5)
    bus.publish({"type": "debug_submit", "text": "打開小算盤"})
    time.sleep(0.8)
    check("輸入框送出 → 後端收到 command「打開小算盤」", {"type": "command", "text": "打開小算盤"} in received)
    s = state("interact-1")
    check("送出後輸入框已關閉", s["input"] is False)
    received.clear()
    bus.publish({"type": "debug_click", "x": 180, "y": 380})
    time.sleep(0.6)
    s = state("interact-2")
    check("點身體 → 不送訊息、動作是 Happy", received == [] and s["motion"] == "Happy")
    received.clear()
    bus.publish({"type": "debug_click", "x": 5, "y": 5})  # 透明角落：不該有任何反應
    time.sleep(0.5)
    check("點透明區 → 沒有任何訊息", received == [])
    return all(results)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hold", action="store_true", help="播完後保持執行")
    ap.add_argument("--no-launch", action="store_true", help="不啟動 Electron")
    ap.add_argument("--capture", metavar="DIR", help="每個狀態截圖到這個資料夾")
    ap.add_argument("--focus-test", type=int, metavar="N", help="輸入框開關 N 次，檢查前景 App 沒被搶走")
    ap.add_argument("--interact-test", action="store_true", help="注入點擊與打字，檢查 UI 送回後端的訊息")
    args = ap.parse_args()

    if not args.no_launch:
        problem = check_ui_ready()
        if problem:
            print(f"❌ {problem}")
            return 1
    bus = UiBus(port=0, token=secrets.token_urlsafe(32))
    received: list = []

    def on_ui_message(msg: dict) -> None:
        received.append(msg)
        print(f"⬅️  UI：{json.dumps(msg, ensure_ascii=False)}")

    bus.bind(on_ui_message,
             lambda: {"wake_enabled": True, "session_s": config.WAKE_SESSION_S, "paused": False, "remaining_s": 0.0})
    bus.start()
    ui = None
    try:
        if args.no_launch:
            print("事件匯流排已啟動，請另開終端機：")
            print(f"  cd desktop && MAC_AI_UI_URL='{bus.url}' MAC_AI_UI_MODEL='{config.UI_MODEL_PATH}' npm start")
            deadline = time.monotonic() + 120
            while bus.client_count() == 0 and time.monotonic() < deadline:
                time.sleep(0.2)
        else:
            ui = ui_service(bus.url, bus.health_url)
            capture_dir = args.capture or (tempfile.mkdtemp(prefix="ui_demo_") if args.focus_test or args.interact_test else None)
            if capture_dir:
                args.capture = capture_dir
                ui.env = {**ui.env, "MAC_AI_UI_CAPTURE_DIR": capture_dir}
            ui.start()
            ui.wait_ready(config.UI_START_TIMEOUT_S)
        print("✅ UI 已連上，開始播放")

        def wait(seconds: float, label: str) -> None:
            print(f"… {label}")
            time.sleep(seconds)

        def capture(name: str) -> None:
            if args.capture:
                bus.publish({"type": "debug_capture", "name": name})
                time.sleep(0.5)

        if args.interact_test:
            time.sleep(2)
            return 0 if interact_test(bus, received, Path(args.capture)) else 1
        if args.focus_test:
            time.sleep(2)
            return 0 if focus_test(bus, args.focus_test, Path(args.capture)) else 1
        play(bus, wait, capture)
        if args.hold:
            print("播放完畢；保持執行中（Ctrl+C 結束）。可打字、點擊角色、按右鍵。")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    except ServiceError as e:
        print(f"❌ {e}")
        return 1
    finally:
        if ui is not None:
            stop_all([ui])
        bus.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
