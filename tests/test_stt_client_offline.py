"""STT 串流客戶端：config 協定、音訊送出、事件接收、斷線重連、停止（對本機假 WS server）。"""
import socket
import threading
import time

import pytest
from fake_stt import FakeAudio, FakeSttServer, wait_until

from mac_ai import config
from mac_ai.stt.client import SttClient

FRAME = b"\x01\x00" * 3200  # 0.2 s、16 kHz、int16
WINDOW = b"\x01\x00" * 512  # 32 ms（一個 VAD 窗）


@pytest.fixture
def server_factory():
    servers = []

    def make(**kw):
        servers.append(FakeSttServer(**kw))
        return servers[-1]

    yield make
    for s in servers:
        s.stop()


@pytest.fixture
def running():
    """啟動客戶端執行緒，測試結束時一定停止"""
    started = []

    def start(client):
        stop = threading.Event()
        thread = threading.Thread(target=client.run, args=(stop,), daemon=True)
        thread.start()
        started.append((stop, thread))
        return stop, thread

    yield start
    for stop, thread in started:
        stop.set()
        thread.join(timeout=5)


def test_sends_config_then_audio_and_delivers_events_in_order(server_factory, running):
    events_from_stt = [
        {"type": "partial", "text": "露醬"},
        {"type": "utterance", "text": "露醬，打開小算盤"},
        {"type": "final", "text": "把音量調到 30", "confidence": 1.0},
    ]
    server = server_factory(events=events_from_stt)
    audio, got = FakeAudio([FRAME, FRAME, FRAME]), []
    running(SttClient(audio, got.append, url=server.url, hints=["露醬", "Chrome"]))

    assert wait_until(lambda: len(server.frames) == 3 and len(got) == 4)
    assert server.configs == [{"type": "config", "lang": "zh-TW", "sample_rate": 16000, "hints": ["露醬", "Chrome"],
                               "multi_utterance": config.STT_MULTI_UTTERANCE,
                               "vad_flush_seconds": config.STT_VAD_FLUSH_S,
                               "flush_pre_delay_seconds": config.STT_FLUSH_PRE_DELAY_S}]
    assert server.frames == [FRAME, FRAME, FRAME]
    assert got == [{"type": "connected"}, *events_from_stt]


def test_stop_closes_politely_and_returns_promptly(server_factory, running):
    server = server_factory()
    got = []
    stop, thread = running(SttClient(FakeAudio([FRAME]), got.append, url=server.url))
    assert wait_until(lambda: {"type": "connected"} in got)
    t = time.monotonic()
    stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive() and time.monotonic() - t < 2
    assert wait_until(lambda: server.closed_by_client)


def test_reconnects_after_server_drops_connection(server_factory, running):
    server = server_factory(drop_first=True)
    audio, got = FakeAudio([FRAME] * 100, pace=0.02), []  # 持續出幀：第一條連線掛掉時還有音訊留給第二條
    running(SttClient(audio, got.append, url=server.url, reconnect_min_s=0.05))

    assert wait_until(lambda: server.connections >= 2 and len(server.frames) >= 1)
    kinds = [e["type"] for e in got]
    assert kinds[:3] == ["connected", "disconnected", "connected"]
    assert audio.drained >= 2  # 每次連上都丟掉累積的舊音訊
    assert len(server.configs) >= 2


def test_unreachable_server_keeps_retrying_and_stop_still_works(running):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    got = []
    stop, thread = running(SttClient(FakeAudio(), got.append, url=f"ws://127.0.0.1:{port}/stream",
                                     reconnect_min_s=0.05, reconnect_max_s=0.1))
    assert wait_until(lambda: sum(e["type"] == "disconnected" for e in got) >= 3)
    t = time.monotonic()
    stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive() and time.monotonic() - t < 1


def test_backoff_doubles_up_to_max(running):
    """連不上時等待時間 min → 2×min → … 不超過 max；用實際重試間隔檢查"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    stamps = []
    running(SttClient(FakeAudio(), lambda e: stamps.append(time.monotonic()), url=f"ws://127.0.0.1:{port}/stream",
                      reconnect_min_s=0.1, reconnect_max_s=0.2))
    assert wait_until(lambda: len(stamps) >= 4)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert gaps[0] >= 0.1 and gaps[1] >= 0.2 and max(gaps) < 0.5  # 第三次起固定 0.2，不再加倍


def test_error_event_is_passed_through(server_factory, running):
    err = {"type": "error", "code": "too_many_sessions", "message": "session limit reached (max=3)"}
    server = server_factory(events=[err])
    got = []
    running(SttClient(FakeAudio(), got.append, url=server.url))
    assert wait_until(lambda: err in got)


class FakeVad:
    """每個 chunk 剛好一窗：依序吐出預設的機率；每吐一個，假時鐘前進 32 ms"""

    def __init__(self, probs, clock):
        self.probs = list(probs)
        self._clock = clock
        self.infer_ms = [0.1]
        self.resets = 0

    def feed(self, pcm):
        self._clock.t += 0.032
        return [self.probs.pop(0) if self.probs else 0.0]

    def reset(self):
        self.resets += 1


class FakeClock:
    t = 0.0

    def __call__(self):
        return self.t


def _endpoint_client(server, probs, *, flush, clock=None, chunk=FRAME, **kw):
    from mac_ai.stt.endpoint import EndpointDetector
    clock = clock or FakeClock()
    records = []
    audio = FakeAudio([chunk] * 60, pace=0.01)
    detector = EndpointDetector(decide=flush, classifier=lambda t: "complete", silence_complete_s=0.35, partial_stable_s=0.25, no_text_reset_s=9)
    client = SttClient(audio, lambda e: None, url=server.url, vad=FakeVad(probs, clock), endpoint=detector, flush=flush,
                       latency_sink=records.append, clock=clock, **kw)
    return client, records, detector


SPEECH_THEN_SILENCE = [0.9] * 8 + [0.05] * 60
PARTIAL_AT_5 = {5: [{"type": "partial", "text": "打開小算盤"}]}
FINAL = {"type": "final", "text": "打開小算盤", "timing": {"reason": "client_flush", "pre_delay_ms": 100, "finish_ms": 50}}


def test_client_mode_sends_one_flush_after_the_speech_frames_and_records_latency(server_factory, running):
    server = server_factory(after_frames=PARTIAL_AT_5, reply_on_flush=FINAL)
    client, records, _ = _endpoint_client(server, SPEECH_THEN_SILENCE, flush=True, pre_delay_s=0.1, vad_flush_s=3.0)
    running(client)

    assert wait_until(lambda: len(records) == 1)
    time.sleep(0.3)  # 之後不該再有第二次 flush
    assert len(server.flush_at_frame) == 1
    assert server.flush_at_frame[0] >= 8  # flush 排在最後一幀語音之後
    assert server.configs[0]["flush_pre_delay_seconds"] == 0.1 and server.configs[0]["vad_flush_seconds"] == 3.0
    r = records[0]
    assert r["mode"] == "client" and r["reason"] == "complete" and r["partial_at_endpoint"] == "打開小算盤"
    assert r["server"]["pre_delay_ms"] == 100 and r["final_text"] == "打開小算盤"
    # partial 在說完之前就到了（引擎說完後沒再更新）→ 走「未確認」的 0.6 s，不是 0.35 s
    assert r["endpoint_ms"] == pytest.approx(600, abs=70) and r["e2e_ms"] >= r["endpoint_ms"]
    assert r["finalize_ms"] is not None


def test_server_mode_never_sends_flush_but_still_records_server_vad_final(server_factory, running):
    server = server_factory(after_frames={**PARTIAL_AT_5, 40: [FINAL]})
    client, records, _ = _endpoint_client(server, SPEECH_THEN_SILENCE, flush=False)
    running(client)

    assert wait_until(lambda: len(records) == 1)
    assert server.flush_at_frame == []
    assert records[0]["mode"] == "server" and records[0]["reason"] == "server_vad"
    assert records[0]["speech_end"] is not None and records[0]["e2e_ms"] > 0


def test_reconnect_resets_vad_and_detector(server_factory, running):
    server = server_factory(drop_first=True)
    client, _, _ = _endpoint_client(server, [], flush=True, reconnect_min_s=0.05)
    running(client)

    assert wait_until(lambda: server.connections >= 2 and client.vad.resets >= 2)  # 每次連線都重置，不帶上一條連線的狀態


def test_send_bundling_sends_fewer_messages_but_flush_still_follows_all_speech_audio(server_factory, running):
    server = server_factory(after_frames={2: [{"type": "partial", "text": "打開小算盤"}]}, reply_on_flush=FINAL)
    client, records, _ = _endpoint_client(server, SPEECH_THEN_SILENCE, flush=True, send_s=0.096, chunk=WINDOW)
    running(client)

    assert wait_until(lambda: len(records) == 1)
    assert len(server.flush_at_frame) == 1
    sent_before_flush = sum(len(f) for f in server.frames[:server.flush_at_frame[0]])
    assert sent_before_flush >= 8 * len(WINDOW)  # 說話的 8 窗都已送出，flush 才到
    assert len(server.frames) <= 21  # 60 窗每 3 窗一則（20 則），不是每窗一則
