"""macos-stt 串流客戶端：把 PCM 推到 WS /stream，收辨識事件；斷線自動重連。"""
import json
import threading
from typing import Callable, Sequence

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from mac_ai import config


class SttClient:
    """audio 需提供 read(timeout) -> Optional[bytes] 與 drain()（MicSource 即是）。
    on_event 收到 macos-stt 的事件（partial／utterance／final／session_restarted／error），
    以及本客戶端自己產生的 connected／disconnected"""

    def __init__(self, audio, on_event: Callable[[dict], None], url: str = config.STT_WS_URL,
                 lang: str = config.STT_LANG, hints: Sequence[str] = (),
                 reconnect_min_s: float = config.STT_RECONNECT_MIN_S,
                 reconnect_max_s: float = config.STT_RECONNECT_MAX_S):
        self.audio = audio
        self.on_event = on_event
        self.url = url
        self.lang = lang
        self.hints = list(hints)
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self._connected = False

    def run(self, stop: threading.Event) -> None:
        """連線、串流、斷線就重連，直到 stop 被設定"""
        delay = self.reconnect_min_s
        while not stop.is_set():
            try:
                self._session(stop)
            except (OSError, WebSocketException) as e:
                if self._connected:
                    delay = self.reconnect_min_s  # 連上過才重置等待時間
                self.on_event({"type": "disconnected", "message": str(e) or type(e).__name__})
            self._connected = False
            if stop.wait(delay):
                return
            delay = min(delay * 2, self.reconnect_max_s)

    def _session(self, stop: threading.Event) -> None:
        with connect(self.url, open_timeout=5) as ws:
            ws.send(json.dumps({
                "type": "config", "lang": self.lang, "sample_rate": config.STT_SAMPLE_RATE,
                "hints": self.hints, "multi_utterance": config.STT_MULTI_UTTERANCE,
                "vad_flush_seconds": config.STT_VAD_FLUSH_S,
            }))
            self.audio.drain()
            self._connected = True
            self.on_event({"type": "connected"})
            done = threading.Event()  # 被設定＝送出執行緒該結束（連線中斷或離開 session）
            sender = threading.Thread(target=self._send_audio, args=(ws, stop, done), daemon=True)
            sender.start()
            try:
                while not stop.is_set() and not done.is_set():
                    try:
                        raw = ws.recv(timeout=0.5)
                    except TimeoutError:
                        continue
                    event = json.loads(raw)
                    if isinstance(event, dict):
                        self.on_event(event)
                if not stop.is_set():
                    raise OSError("音訊送出失敗，連線中斷")
                ws.send(json.dumps({"type": "close"}))
            finally:
                done.set()
                sender.join(timeout=1)

    def _send_audio(self, ws, stop: threading.Event, done: threading.Event) -> None:
        try:
            while not stop.is_set() and not done.is_set():
                chunk = self.audio.read(0.2)
                if chunk:
                    ws.send(chunk)
        except (OSError, WebSocketException):
            done.set()
