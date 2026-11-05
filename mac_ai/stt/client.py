"""macos-stt 串流客戶端：把 PCM 推到 WS /stream，收辨識事件；斷線自動重連。

可選的端點偵測：傳入 vad 與 endpoint 時，送音訊的同時用 Silero VAD 量每一窗的語音機率；
flush=True 時，偵測器決定「說完了」就送 {"type":"flush"} 要求 server 立刻收尾，不等 server 的靜音計時。
latency_sink 每收到一句 final 就收到一筆計時紀錄。"""
import json
import threading
import time
from typing import Callable, Optional, Sequence

import numpy as np
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from mac_ai import config
from mac_ai.stt.endpoint import EndpointDetector, Utterance
from mac_ai.stt.vad import WINDOW, SileroVad


class SttClient:
    """audio 需提供 read(timeout) -> Optional[bytes] 與 drain()（MicSource 即是）。
    on_event 收到 macos-stt 的事件（partial／utterance／final／session_restarted／error），
    以及本客戶端自己產生的 connected／disconnected"""

    def __init__(self, audio, on_event: Callable[[dict], None], url: str = config.STT_WS_URL,
                 lang: str = config.STT_LANG, hints: Sequence[str] = (),
                 reconnect_min_s: float = config.STT_RECONNECT_MIN_S,
                 reconnect_max_s: float = config.STT_RECONNECT_MAX_S,
                 vad: Optional[SileroVad] = None, endpoint: Optional[EndpointDetector] = None, flush: bool = False,
                 vad_flush_s: float = config.STT_VAD_FLUSH_S, pre_delay_s: float = config.STT_FLUSH_PRE_DELAY_S,
                 latency_sink: Optional[Callable[[dict], None]] = None, send_s: float = 0.0,
                 clock: Callable[[], float] = time.monotonic):
        self.audio = audio
        self.on_event = on_event
        self.url = url
        self.lang = lang
        self.hints = list(hints)
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self.vad = vad
        self.endpoint = endpoint
        self.flush = flush
        self.vad_flush_s = vad_flush_s
        self.pre_delay_s = pre_delay_s
        self.latency_sink = latency_sink
        self.send_s = send_s  # >0：累積到這麼長才送一則訊息（VAD 仍逐窗判斷）；0＝收到就送
        self._clock = clock
        self._connected = False
        self._flush_sent_at: Optional[float] = None

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
                "vad_flush_seconds": self.vad_flush_s, "flush_pre_delay_seconds": self.pre_delay_s,
            }))
            self.audio.drain()
            if self.vad is not None:
                self.vad.reset()
            if self.endpoint is not None:
                self.endpoint.reset()
            self._flush_sent_at = None
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
                        self._observe(event)
                        self.on_event(event)
                if not stop.is_set():
                    raise OSError("音訊送出失敗，連線中斷")
                ws.send(json.dumps({"type": "close"}))
            finally:
                done.set()
                sender.join(timeout=1)

    def _send_audio(self, ws, stop: threading.Event, done: threading.Event) -> None:
        pending, pending_bytes = [], 0
        send_bytes = int(self.send_s * config.STT_SAMPLE_RATE) * 2
        tracking = self.vad is not None and self.endpoint is not None
        try:
            while not stop.is_set() and not done.is_set():
                chunk = self.audio.read(0.2)
                if not chunk:
                    continue
                pending.append(chunk)
                pending_bytes += len(chunk)
                decided = self._track(chunk) if tracking else False
                if decided or pending_bytes >= send_bytes:
                    ws.send(b"".join(pending))  # 先送音訊再送 flush：flush 一定排在最後一幀之後
                    pending, pending_bytes = [], 0
                if decided and self.flush:
                    ws.send(json.dumps({"type": "flush"}))
                    self._flush_sent_at = self._clock()
        except (OSError, WebSocketException):
            done.set()

    def _track(self, chunk: bytes) -> bool:
        """這個 chunk 的每一窗都進偵測器；回傳是否做出「說完了」的決定"""
        probs = self.vad.feed(chunk)
        now = self._clock()
        decided = False
        for i, p in enumerate(probs):
            # 一個 chunk 可能湊出多窗：第 i 窗的結束時間往回推
            if self.endpoint.on_prob(p, now - (len(probs) - 1 - i) * WINDOW / config.STT_SAMPLE_RATE) is not None:
                decided = True
        return decided

    def _observe(self, event: dict) -> None:
        """事件進偵測器（partial 供語意判斷、final 結束一句）並寫計時紀錄"""
        if self.endpoint is None:
            return
        kind = event.get("type")
        if kind == "partial":
            self.endpoint.on_partial(event.get("text") or "", self._clock())
        elif kind == "final":
            now = self._clock()
            utt = self.endpoint.on_final(now)
            if self.latency_sink is not None:
                self.latency_sink(self._latency_record(utt, event, now))
            self._flush_sent_at = None

    def _latency_record(self, utt: Utterance, event: dict, final_recv: float) -> dict:
        d = utt.decision
        times = np.array(self.vad.infer_ms) if self.vad is not None and self.vad.infer_ms else None
        record = {
            "mode": "client" if self.flush else "server",
            "speech_start": utt.speech_start, "speech_end": utt.speech_end,
            "endpoint_at": d.at if d else None,
            "reason": d.reason if d else "server_vad",
            "class_at_endpoint": d.cls if d else None,
            "partial_at_endpoint": d.partial if d else None,
            "flush_sent": self._flush_sent_at if d else None,
            "final_recv": final_recv, "final_text": event.get("text"),
            "server": event.get("timing"),
            "vad_ms_mean": float(times.mean()) if times is not None else None,
            "vad_ms_p99": float(np.percentile(times, 99)) if times is not None else None,
            "ts": time.time(),
        }
        if utt.speech_end is not None:
            record["e2e_ms"] = (final_recv - utt.speech_end) * 1000
            if d:
                record["endpoint_ms"] = (d.at - utt.speech_end) * 1000
        if d and self._flush_sent_at is not None:
            record["finalize_ms"] = (final_recv - self._flush_sent_at) * 1000
        return record
