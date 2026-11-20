"""桌面角色介面的示範與開發工具：不啟動 STT／LLM，依序播放每個狀態的假事件。

    python scripts/ui_demo.py                  啟動 Electron 並播放一輪（播完結束）
    python scripts/ui_demo.py --hold           播完後保持執行，可手動操作（打字、點擊、右鍵），Ctrl+C 結束
    python scripts/ui_demo.py --capture DIR    每個狀態把視窗存成 DIR/<名稱>.png＋.json，並逐張比對演出（動作、表情、手勢）印出 PASS／FAIL
    python scripts/ui_demo.py --no-launch      只開事件匯流排，Electron 自己用 npm start 啟動（會印出需要的環境變數）
    python scripts/ui_demo.py --focus-test N   輸入框開關 N 次，確認每次前景 App 都沒被搶走（取代人工的 E5 前半段）
    python scripts/ui_demo.py --interact-test  注入點擊與打字，確認 UI 會把 wake／command 送回後端（走完整條 Electron → 後端的路）
    python scripts/ui_demo.py --choices-test   推送搜尋結果清單，確認畫面顯示、點選會送出 choose、後端通知關閉時清單消失
    python scripts/ui_demo.py --chat-test [N]  聊天室：快捷泡泡、主動展開 N 次（預設 10）前景 App 不變、內容完整、自動收回與互動後不收、輸入送出
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

AUTO_CLOSE_S = 20.0  # 要和 desktop/chat.js 的 AUTO_CLOSE_MS 一致
EXPAND_NOTICE = "詳細內容在對話視窗"  # 要和 desktop/state.js 的 EXPAND_NOTICE 一致

OK_RESULT = {"type": "result", "command": "打開小算盤", "ok": True, "total_ms": 12.0, "replies": [],
             "steps": [{"kind": "fast", "text": "打開小算盤", "status": "ok", "logs": ["✅ 已開啟 小算盤"]}]}
FAIL_RESULT = {"type": "result", "command": "打開不存在的程式", "ok": False, "total_ms": 30.0, "replies": [],
               "steps": [{"kind": "fast", "text": "打開不存在的程式", "status": "exec_failed", "logs": ["❌ 找不到 App：不存在的程式"]}]}
REPLY_RESULT = {"type": "result", "command": "今天天氣如何", "ok": True, "total_ms": 900.0, "steps": [],
                "replies": ["今天台北多雲，氣溫約 24 度，晚上可能有短暫陣雨。"]}


# 每個截圖點應有的演出（docs/桌面角色模型升級（fullrig03）：實作計畫.md §5.2）：動作、臉部表情、手勢、是否出場
EXPECTED = {
    "02-appear": ("Surprised", "surprised", "none", True),
    "04-executing": ("Curious", "neutral", "none", True),
    "05-success": ("Nod", "happy", "none", True),
    "06-failure": ("Shake", "pout", "none", True),
    "07-reply": ("Talk", "happy", "none", True),
    "08-think": ("Curious", "neutral", "chin_rest", True),
    "09-fail-again": ("Shake", "pout_left", "none", True),
    "10-sleepy": (None, "sleepy", "chin_rest", True),
    "11-retreat": ("Idle", None, "none", False),
    "12-click-head": ("Surprised", "star_eyes", "arm_r_up", True),
    "13-click-body": ("Happy", "happy", "arm_l_up", True),
}
# 視窗座標：潛伏時的頭部（模型頂端 y＝560−420×0.3）、出場時的身體（模型頂端 y＝560−420）
PEEK_HEAD_XY, BODY_XY = (180, 474), (180, 380)


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
    pub({"type": "executing", "command": "打開不存在的程式"})
    wait(3.0, "long execution")
    capture("08-think")
    pub(FAIL_RESULT)
    wait(0.6, "failure")
    pub({"type": "executing", "command": "打開不存在的程式"})
    wait(0.3, "executing")
    pub(FAIL_RESULT)
    wait(1.0, "failure again")
    capture("09-fail-again")
    pub({"type": "session", "remaining_s": 0})
    wait(0.8, "session ended")
    capture("10-sleepy")
    wait(2.5, "retreat")
    capture("11-retreat")
    pub({"type": "debug_click", "x": PEEK_HEAD_XY[0], "y": PEEK_HEAD_XY[1]})  # debug_click 只有 --capture 時才有效
    wait(0.8, "click head")
    capture("12-click-head")
    wait(1.5, "gesture done")
    pub({"type": "debug_click", "x": BODY_XY[0], "y": BODY_XY[1]})
    wait(0.6, "click body")
    capture("13-click-body")


def check_capture(capture_dir: Path) -> bool:
    """逐張比對 EXPECTED（R2）；None 表示那一欄不檢查"""
    ok = True
    for name, (motion, expression, gesture, out) in EXPECTED.items():
        s = json.loads((capture_dir / f"{name}.json").read_text())["state"]
        got = (s["motion"], s["snap"]["expression"], s["gesture"], s["poseT"] > 0.5)
        want = (motion, expression, gesture, out)
        passed = all(w is None or g == w for g, w in zip(got, want)) and s["snap"]["gesture"] == s["gesture"]
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}：動作 {got[0]}、表情 {got[1]}、手勢 {got[2]}、出場 {got[3]}"
              + ("" if passed else f"（應為 {want}）"))
    return ok


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


CHOICES_RESULT = {
    "type": "result", "command": "找驗收報告", "ok": True, "total_ms": 80.0, "replies": ["找到 3 筆「驗收報告」。說「第二個」或點選清單即可顯示"],
    "steps": [{"kind": "fast", "text": "找驗收報告", "status": "ok", "logs": ["✅ 找到 3 筆"]}],
    "choices": {"list_id": 4, "query": "驗收報告", "total": 3, "default_action": "reveal", "expires_in_s": 60.0,
                "items": [{"n": 1, "name": "驗收報告A.pdf", "dir": "~/macai_find_test", "kind": "pdf"},
                          {"n": 2, "name": "驗收報告B.pdf", "dir": "~/macai_find_test", "kind": "pdf"},
                          {"n": 3, "name": "驗收報告C.txt", "dir": "~/macai_find_test", "kind": "txt"}]}}


def choices_test(bus: UiBus, received: list, capture_dir: Path) -> bool:
    """推送清單 → 畫面有 3 列；點整列 → choose(預設動作)；點列尾按鈕 → choose(另一個動作)；後端通知關閉 → 清單消失"""
    results = []

    def state(name: str) -> dict:
        bus.publish({"type": "debug_capture", "name": name})
        time.sleep(0.7)
        return json.loads((capture_dir / f"{name}.json").read_text())["state"]

    def check(label: str, ok: bool) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        results.append(ok)

    bus.publish({"type": "executing", "command": "找驗收報告"})
    bus.publish(CHOICES_RESULT)
    time.sleep(1.5)
    s = state("choices-1-open")
    rows = s["choiceRows"]
    check("清單顯示、有 3 列", s["choices"] and len(rows) == 3)
    check("第 1 列有序號、檔名與資料夾", bool(rows) and rows[0]["text"].startswith("1") and "驗收報告A.pdf" in rows[0]["text"] and "~/macai_find_test" in rows[0]["text"])
    check("標題含查詢字與預設動作", "驗收報告" in s["choicesTitle"] and "顯示" in s["choicesTitle"])
    check("狀態機的清單與後端一致", s["snap"]["choices"]["listId"] == 4 and len(s["snap"]["choices"]["items"]) == 3)
    received.clear()
    bus.publish({"type": "debug_click", "x": rows[1]["x"], "y": rows[1]["y"]})
    time.sleep(0.8)
    check("點第 2 列 → 送出 choose(n=2, reveal)", {"type": "choose", "list_id": 4, "n": 2, "action": "reveal"} in received)
    check("點選後清單先留著（等後端回報成功才關）", state("choices-2-after-click")["choices"] is True)
    received.clear()
    bus.publish({"type": "debug_click", "x": rows[0]["bx"], "y": rows[0]["by"]})
    time.sleep(0.8)
    check("點第 1 列的按鈕 → 送出 choose(n=1, open)", {"type": "choose", "list_id": 4, "n": 1, "action": "open"} in received)
    bus.publish({"type": "result", "command": "選擇第 2 筆", "ok": True, "total_ms": 5.0, "replies": [], "steps": [
        {"kind": "fast", "text": "t", "status": "ok", "logs": ["✅ 已在 Finder 中顯示"]}], "choices": {"list_id": 4, "closed": True}})
    time.sleep(0.8)
    check("後端通知選擇成功 → 清單消失", state("choices-3-closed")["choices"] is False)
    return all(results)


LONG_REPLY = "今天台北多雲，氣溫約 24 度，晚上可能有短暫陣雨，出門記得帶傘。" * 8  # 約 270 字：泡泡放不下、聊天室要完整顯示
EXPAND_RESULTS = [
    {"type": "result", "command": "看一下畫面", "ok": True, "total_ms": 1500.0, "expand": "screen", "replies": [LONG_REPLY],
     "steps": [{"kind": "slow", "text": "看一下畫面", "status": "ok", "route": "cloud", "ms": 1400.0, "logs": ["✅ 畫面內容（雲端）：" + LONG_REPLY]}]},
    {"type": "result", "command": "講個笑話", "ok": True, "total_ms": 900.0, "expand": "chat", "replies": ["為什麼程式設計師都分不清萬聖節和聖誕節？因為 Oct 31 == Dec 25。"],
     "steps": [{"kind": "slow", "text": "講個笑話", "status": "ok", "route": "cloud", "ms": 880.0, "logs": ["☁️ AI 回覆：..."]}]},
    {"type": "result", "command": "開 Safari 然後搜尋天氣", "ok": True, "total_ms": 400.0, "expand": "complex", "replies": [],
     "steps": [{"kind": "fast", "text": "開 Safari", "status": "ok", "ms": 120.0, "logs": ["✅ 已開啟 Safari"]},
               {"kind": "fast", "text": "搜尋天氣", "status": "ok", "ms": 230.0, "logs": ["✅ 已開啟瀏覽器搜尋：天氣"]}]},
]


def rss_mb() -> float:
    """桌面角色相關程序（命令列含 desktop 目錄）的 RSS 加總，MB"""
    out = subprocess.run(["ps", "-axo", "rss=,command="], capture_output=True, text=True, timeout=10).stdout
    return sum(int(line.split(None, 1)[0]) for line in out.splitlines() if str(config.UI_DIR) in line) / 1024


def chat_test(bus: UiBus, received: list, capture_dir: Path, rounds: int) -> bool:
    """聊天室：快捷泡泡 → 手動開關；主動展開 rounds 次（每次前景 App 不變）；完整內容與步驟；自動收回／互動後不收；輸入送出"""
    results = []

    def info(name: str, kind: str = "chat") -> dict:
        bus.publish({"type": "debug_chat_capture" if kind == "chat" else "debug_capture", "name": name})
        time.sleep(0.8)
        return json.loads((capture_dir / f"{name}.json").read_text())

    def check(label: str, ok: bool) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        results.append(ok)

    before = frontmost_app()
    print(f"前景 App：{before}")
    rss0 = rss_mb()
    check("一開始聊天室是隱藏的", info("chat-0-hidden")["visible"] is False)

    # ---- 快捷泡泡 → 手動開關 ----
    bus.publish({"type": "partial", "text": "露西婭", "wake": True})
    time.sleep(1.5)
    received.clear()
    bus.publish({"type": "debug_click", "x": 180, "y": 140 + 0.10 * 420})
    time.sleep(0.8)
    st = info("chat-1-quick", "role")["state"]
    check("點頭部 → wake 照舊送出，並出現快捷泡泡", {"type": "wake"} in received and st["quick"] is True)
    bus.publish({"type": "debug_click", "x": st["quickBtn"]["x"], "y": st["quickBtn"]["y"]})
    time.sleep(1.0)
    c = info("chat-2-manual-open")
    check("點「💬 聊天室」→ 聊天室手動開啟（不會自動收）", c["visible"] and c["lastOpen"]["auto"] is False and c["page"]["hidden"] is False)
    check("快捷泡泡已收起", info("chat-2b-quick-gone", "role")["state"]["quick"] is False)
    after_manual = frontmost_app()
    print(f"（資訊）手動開啟並取得焦點後前景 App：{after_manual}（開啟前：{before}）")
    bus.publish({"type": "debug_chat_toggle"})
    time.sleep(0.8)
    check("再開關一次 → 聊天室關閉", info("chat-3-closed")["visible"] is False)

    # ---- 主動展開：前景不變 ----
    rss1 = None
    show_ms, paint_ms = [], []
    kept = shown = 0
    for i in range(rounds):
        result = EXPAND_RESULTS[i % len(EXPAND_RESULTS)]
        bus.publish({"type": "executing", "command": result["command"]})
        bus.publish(result)
        time.sleep(0.7)
        c = info(f"chat-auto-{i}")
        shown += bool(c["visible"] and c["page"]["hidden"] is False and c["lastOpen"]["auto"] is True)
        kept += frontmost_app() == before and not c["focused"]
        show_ms.append(c["lastOpen"]["showMs"])
        if c["lastOpen"]["paintMs"] is not None:
            paint_ms.append(c["lastOpen"]["paintMs"])
        if rss1 is None:
            rss1 = rss_mb()
        bus.publish({"type": "debug_chat_toggle"})  # 關掉，下一輪才會再「主動」開
        time.sleep(0.6)
    check(f"主動展開 {rounds} 次：聊天室可見 {shown}/{rounds}", shown == rounds)
    check(f"主動展開 {rounds} 次：前景 App 沒變、聊天室沒拿到焦點 {kept}/{rounds}", kept == rounds)
    if show_ms:
        print(f"（R2）收到 result → showInactive：max {max(show_ms):.0f} ms、中位 {sorted(show_ms)[len(show_ms) // 2]:.0f} ms；"
              f"展開後第一幀畫出：{sorted(paint_ms)[len(paint_ms) // 2]:.0f} ms（中位，{len(paint_ms)} 筆）" if paint_ms else f"（R2）showInactive max {max(show_ms):.0f} ms")
    if rss1 is not None:
        print(f"（R3）聊天室第一次開啟前後的 RSS：{rss0:.0f} MB → {rss1:.0f} MB（增量 {rss1 - rss0:+.0f} MB）")

    # ---- 內容完整 ----
    bus.publish({"type": "executing", "command": "看一下畫面"})
    bus.publish(EXPAND_RESULTS[0])
    time.sleep(0.9)
    c = info("chat-4-content")
    check("長回覆（約 270 字）完整顯示在聊天室", LONG_REPLY in c["page"]["text"])
    check("步驟區塊摺疊在回覆下面", c["page"]["details"] >= 1)
    check("角色泡泡只放提示、不重複長內容", EXPAND_NOTICE in json.dumps(info("chat-4b-bubble", "role")["state"]["snap"]["bubbles"], ensure_ascii=False))

    # ---- 輸入送出 ----
    received.clear()
    bus.publish({"type": "debug_chat_submit", "text": "打開小算盤"})
    time.sleep(0.8)
    check("聊天室輸入送出 → 後端收到 command「打開小算盤」", {"type": "command", "text": "打開小算盤"} in received)
    c = info("chat-5-typed")
    check("送出的指令出現在聊天室（打字來源）", "打開小算盤" in c["page"]["text"] and any(e["source"] == "typed" for e in c["chat"]["entries"] if e["role"] == "user"))
    bus.publish({"type": "debug_chat_toggle"})
    time.sleep(0.6)

    # ---- 自動收回／互動後不收 ----
    bus.publish(EXPAND_RESULTS[1])
    time.sleep(0.8)
    check("主動展開後開著", info("chat-6-auto-open")["visible"])
    time.sleep(AUTO_CLOSE_S + 1)
    check(f"沒有互動 → 約 {AUTO_CLOSE_S:.0f} 秒後自動收回", info("chat-7-auto-closed")["visible"] is False)
    bus.publish(EXPAND_RESULTS[1])
    time.sleep(0.8)
    bus.publish({"type": "debug_chat_interact"})
    time.sleep(AUTO_CLOSE_S + 1)
    check("有互動 → 超過時間仍開著", info("chat-8-interacted")["visible"] is True)
    bus.publish({"type": "debug_chat_toggle"})
    time.sleep(0.6)
    check("最後關閉", info("chat-9-final")["visible"] is False)
    return all(results)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hold", action="store_true", help="播完後保持執行")
    ap.add_argument("--no-launch", action="store_true", help="不啟動 Electron")
    ap.add_argument("--capture", metavar="DIR", help="每個狀態截圖到這個資料夾")
    ap.add_argument("--focus-test", type=int, metavar="N", help="輸入框開關 N 次，檢查前景 App 沒被搶走")
    ap.add_argument("--interact-test", action="store_true", help="注入點擊與打字，檢查 UI 送回後端的訊息")
    ap.add_argument("--choices-test", action="store_true", help="推送搜尋結果清單，檢查顯示、點選與關閉")
    ap.add_argument("--chat-test", nargs="?", type=int, const=10, metavar="N", help="聊天室：快捷泡泡、主動展開 N 次（預設 10）、自動收回、輸入送出")
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
            capture_dir = args.capture or (tempfile.mkdtemp(prefix="ui_demo_") if args.focus_test or args.interact_test or args.choices_test or args.chat_test else None)
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
        if args.chat_test:
            time.sleep(2)
            return 0 if chat_test(bus, received, Path(args.capture), args.chat_test) else 1
        if args.choices_test:
            time.sleep(2)
            return 0 if choices_test(bus, received, Path(args.capture)) else 1
        if args.focus_test:
            time.sleep(2)
            return 0 if focus_test(bus, args.focus_test, Path(args.capture)) else 1
        play(bus, wait, capture)
        if args.capture and not check_capture(Path(args.capture)):
            return 1
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
