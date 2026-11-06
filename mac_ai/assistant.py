"""語音助手主流程：辨識事件 → 喚醒詞閘門 → 指令佇列 → 依序執行。"""
import functools
import queue
import threading
import traceback
from typing import Callable, Optional, Sequence

from mac_ai import config
from mac_ai.stt.client import SttClient
from mac_ai.stt.completeness import classify
from mac_ai.stt.endpoint import EndpointDetector
from mac_ai.stt.latency_log import JsonlLog
from mac_ai.stt.vad import SileroVad
from mac_ai.ui_bus import UiBus
from mac_ai.wake import WakeGate, split_wake

_CLEAR_LINE = "\r\033[K"
_FAILED_STEP_STATUS = ("exec_failed", "llm_unavailable", "llm_error")


def _ignore(event: dict) -> None:
    pass


def result_event(command: str, outcome) -> dict:
    """把 process_voice_command 的回傳整理成給 UI 的 result 事件；ok＝沒有任何一步失敗"""
    steps, replies, total_ms = [], [], 0.0
    if isinstance(outcome, dict):
        steps = [{"kind": st.get("kind"), "text": st.get("text"), "status": st.get("status"),
                  "logs": [str(x) for x in st.get("logs", [])]} for st in outcome.get("steps", [])]
        replies = [str(r) for r in outcome.get("replies", [])]
        total_ms = outcome.get("total_ms", 0.0)
    return {"type": "result", "command": command, "ok": all(st["status"] not in _FAILED_STEP_STATUS for st in steps),
            "steps": steps, "replies": replies, "total_ms": total_ms}


class CommandRouter:
    """STT 事件的處理器：utterance／final 先過喚醒詞閘門，通過的指令放進佇列；partial 只顯示，不執行"""

    def __init__(self, gate: WakeGate, commands: "queue.Queue[str]", say: Callable[[str], None] = print,
                 show_partial: Optional[Callable[[str], None]] = None,
                 publish: Optional[Callable[[dict], None]] = None, on_quit: Optional[Callable[[], None]] = None):
        self.gate = gate
        self.commands = commands
        self._say = say
        self._show_partial = show_partial
        self._publish = publish or _ignore  # 給桌面角色介面的事件；沒有 UI 時什麼都不做
        self._on_quit = on_quit
        self.paused = False  # UI 要求暫停聽指令：辨識結果一律丟棄
        self._lock = threading.Lock()  # gate 會被 STT 執行緒與 UI 訊息執行緒同時用到

    def say(self, message: str) -> None:
        # 顯示 partial 時游標停在同一行，先清掉再印，才不會接在 partial 後面
        self._say((_CLEAR_LINE if self._show_partial else "") + message)

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "partial":
            if self._show_partial and event.get("text"):
                self._show_partial(event["text"])
            if event.get("text") and not self.paused:
                self._publish({"type": "partial", "text": event["text"],
                               "wake": split_wake(event["text"], self.gate.words) is not None})
        elif kind in ("utterance", "final"):
            text = (event.get("text") or "").strip()
            if text:
                self._on_text(text)
        elif kind == "connected":
            self.say("🎧 已連上 STT，開始收音")
            self._publish({"type": "stt", "state": "connected", "message": ""})
        elif kind == "disconnected":
            self.say(f"⚠️ STT 連線中斷（{event.get('message')}），重新連線中…")
            self._publish({"type": "stt", "state": "disconnected", "message": str(event.get("message"))})
        elif kind == "error":
            self.say(f"⚠️ STT 錯誤：{event.get('code')} {event.get('message')}")
            self._publish({"type": "stt", "state": "error", "message": f"{event.get('code')} {event.get('message')}"})

    def _on_text(self, text: str) -> None:
        if self.paused:
            return
        with self._lock:
            result = self.gate.feed(text)
            remaining = self.gate.session_remaining()
        self._publish({"type": "heard", "text": text, "status": result.status, "command": result.command})
        if result.status in ("command", "armed"):
            self._publish({"type": "session", "remaining_s": remaining})
        if result.status == "command":
            self.commands.put(result.command)
        elif result.status == "armed":
            self.say(f"👂 已喚醒，{self.gate.session_s:g} 秒內不用再說喚醒詞（每句重新計時）")
        elif self.gate.enabled:
            self.say(f"\033[90m（未喚醒）{text}\033[0m")

    # ---------- 桌面角色介面 ----------
    def hello_state(self) -> dict:
        return {"wake_enabled": self.gate.enabled, "session_s": self.gate.session_s, "paused": self.paused,
                "remaining_s": self.gate.session_remaining()}

    def on_ui_message(self, msg: dict) -> None:
        """處理 UI 送來、已驗證過的訊息（在 UI 匯流排的執行緒上呼叫）"""
        kind = msg["type"]
        if kind == "command":  # 直接執行，不經喚醒詞閘門、不影響會話計時（同 --text）
            self.commands.put(msg["text"])
        elif kind == "wake":
            if self.gate.enabled and not self.paused:
                with self._lock:
                    self.gate.start_session()
                    remaining = self.gate.session_remaining()
                self._publish({"type": "session", "remaining_s": remaining})
        elif kind == "pause":
            self.paused = msg["value"]
            if self.paused:
                with self._lock:
                    self.gate.end_session()
            self._publish({"type": "paused", "value": self.paused})
            if self.paused:
                self._publish({"type": "session", "remaining_s": 0.0})
        elif kind == "quit" and self._on_quit:
            self._on_quit()


def command_worker(commands: "queue.Queue[str]", execute: Callable[[str], object], stop: threading.Event,
                   publish: Optional[Callable[[dict], None]] = None) -> None:
    """依序執行佇列裡的指令；單一指令出錯只印出錯誤，不讓整個助手停下"""
    publish = publish or _ignore
    while not stop.is_set():
        try:
            command = commands.get(timeout=0.2)
        except queue.Empty:
            continue
        publish({"type": "executing", "command": command})
        try:
            outcome = execute(command)
        except Exception:
            traceback.print_exc()
            publish({"type": "result", "command": command, "ok": False, "steps": [],
                     "replies": ["執行時發生錯誤"], "total_ms": 0.0})
        else:
            publish(result_event(command, outcome))


def run_voice_loop(audio, execute: Callable[[str], object], stop: threading.Event, *,
                   url: str = config.STT_WS_URL, hints: Sequence[str] = (), gate: Optional[WakeGate] = None,
                   say: Callable[[str], None] = print, show_partial: Optional[Callable[[str], None]] = None,
                   reconnect_min_s: float = config.STT_RECONNECT_MIN_S,
                   endpoint_mode: str = config.ENDPOINT_MODE, bus: Optional[UiBus] = None) -> None:
    """收音 → STT → 喚醒詞 → 執行，直到 stop 被設定。指令在另一條執行緒依序執行，不擋住收音。
    endpoint_mode="client"：本端神經網路 VAD＋語意判斷說完了就要求 server 收尾，並把每句的計時寫進 logs/latency.jsonl。
    bus：桌面角色介面的事件匯流排（已啟動）；有的話把狀態事件廣播出去，並處理 UI 送回來的指令"""
    commands: "queue.Queue[str]" = queue.Queue()
    gate = gate or WakeGate()
    publish = bus.publish if bus is not None else None
    router = CommandRouter(gate, commands, say, show_partial, publish=publish, on_quit=stop.set)
    if bus is not None:
        bus.bind(router.on_ui_message, router.hello_state)
    worker = threading.Thread(target=command_worker, args=(commands, execute, stop, publish), daemon=True)
    worker.start()
    try:
        endpoint_args = {}
        if endpoint_mode == "client":
            # 端點判斷對「只說喚醒詞」的認定要與閘門一致
            endpoint = EndpointDetector(classifier=functools.partial(classify, wake_enabled=gate.enabled))
            endpoint_args = dict(vad=SileroVad(), endpoint=endpoint, flush=True,
                                 vad_flush_s=config.EP_SERVER_BACKUP_FLUSH_S, pre_delay_s=config.EP_FLUSH_PRE_DELAY_S,
                                 latency_sink=JsonlLog().write, send_s=config.EP_SEND_S)
        SttClient(audio, router, url=url, hints=hints, reconnect_min_s=reconnect_min_s, **endpoint_args).run(stop)
    finally:
        stop.set()  # 例外離開（如 Ctrl+C）時也讓指令執行緒結束
        worker.join(timeout=2)
