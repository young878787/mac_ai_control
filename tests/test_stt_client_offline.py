"""STT 串流客戶端：config 協定、音訊送出、事件接收、斷線重連、停止（對本機假 WS server）。"""
import socket
import threading
import time

import pytest
from fake_stt import FakeAudio, FakeSttServer, wait_until

from mac_ai import config
from mac_ai.stt.client import SttClient

FRAME = b"\x01\x00" * 3200  # 0.2 s、16 kHz、int16


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
                               "vad_flush_seconds": config.STT_VAD_FLUSH_S}]
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
