"""語音助手主流程：辨識事件 → 喚醒詞閘門 → 指令佇列 → 依序執行。"""
import queue
import threading
import traceback
from typing import Callable, Optional, Sequence

from mac_ai import config
from mac_ai.stt.client import SttClient
from mac_ai.stt.endpoint import EndpointDetector
from mac_ai.stt.latency_log import JsonlLog
from mac_ai.stt.vad import SileroVad
from mac_ai.wake import WakeGate

_CLEAR_LINE = "\r\033[K"


class CommandRouter:
    """STT 事件的處理器：utterance／final 先過喚醒詞閘門，通過的指令放進佇列；partial 只顯示，不執行"""

    def __init__(self, gate: WakeGate, commands: "queue.Queue[str]", say: Callable[[str], None] = print,
                 show_partial: Optional[Callable[[str], None]] = None):
        self.gate = gate
        self.commands = commands
        self._say = say
        self._show_partial = show_partial

    def say(self, message: str) -> None:
        # 顯示 partial 時游標停在同一行，先清掉再印，才不會接在 partial 後面
        self._say((_CLEAR_LINE if self._show_partial else "") + message)

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "partial":
            if self._show_partial and event.get("text"):
                self._show_partial(event["text"])
        elif kind in ("utterance", "final"):
            text = (event.get("text") or "").strip()
            if text:
                self._on_text(text)
        elif kind == "connected":
            self.say("🎧 已連上 STT，開始收音")
        elif kind == "disconnected":
            self.say(f"⚠️ STT 連線中斷（{event.get('message')}），重新連線中…")
        elif kind == "error":
            self.say(f"⚠️ STT 錯誤：{event.get('code')} {event.get('message')}")

    def _on_text(self, text: str) -> None:
        result = self.gate.feed(text)
        if result.status == "command":
            self.commands.put(result.command)
        elif result.status == "armed":
            self.say(f"👂 已喚醒，{self.gate.session_s:g} 秒內不用再說喚醒詞（每句重新計時）")
        elif self.gate.enabled:
            self.say(f"\033[90m（未喚醒）{text}\033[0m")


def command_worker(commands: "queue.Queue[str]", execute: Callable[[str], object], stop: threading.Event) -> None:
    """依序執行佇列裡的指令；單一指令出錯只印出錯誤，不讓整個助手停下"""
    while not stop.is_set():
        try:
            command = commands.get(timeout=0.2)
        except queue.Empty:
            continue
        try:
            execute(command)
        except Exception:
            traceback.print_exc()


def run_voice_loop(audio, execute: Callable[[str], object], stop: threading.Event, *,
                   url: str = config.STT_WS_URL, hints: Sequence[str] = (), gate: Optional[WakeGate] = None,
                   say: Callable[[str], None] = print, show_partial: Optional[Callable[[str], None]] = None,
                   reconnect_min_s: float = config.STT_RECONNECT_MIN_S,
                   endpoint_mode: str = config.ENDPOINT_MODE) -> None:
    """收音 → STT → 喚醒詞 → 執行，直到 stop 被設定。指令在另一條執行緒依序執行，不擋住收音。
    endpoint_mode="client"：本端神經網路 VAD＋語意判斷說完了就要求 server 收尾，並把每句的計時寫進 logs/latency.jsonl"""
    commands: "queue.Queue[str]" = queue.Queue()
    router = CommandRouter(gate or WakeGate(), commands, say, show_partial)
    worker = threading.Thread(target=command_worker, args=(commands, execute, stop), daemon=True)
    worker.start()
    try:
        endpoint_args = {}
        if endpoint_mode == "client":
            endpoint_args = dict(vad=SileroVad(), endpoint=EndpointDetector(), flush=True,
                                 vad_flush_s=config.EP_SERVER_BACKUP_FLUSH_S, pre_delay_s=config.EP_FLUSH_PRE_DELAY_S,
                                 latency_sink=JsonlLog().write, send_s=config.EP_SEND_S)
        SttClient(audio, router, url=url, hints=hints, reconnect_min_s=reconnect_min_s, **endpoint_args).run(stop)
    finally:
        stop.set()  # 例外離開（如 Ctrl+C）時也讓指令執行緒結束
        worker.join(timeout=2)
