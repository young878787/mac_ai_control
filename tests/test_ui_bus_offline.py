"""桌面角色介面的事件匯流排：事件順序與欄位、轉送延遲、不阻塞、連線驗證、UI 指令、wake／pause（全用本機假資料）。"""
import http.client
import json
import queue
import statistics
import subprocess
import threading
import time

import pytest
from fake_stt import FakeAudio, FakeSttServer, wait_until
from websockets.exceptions import InvalidStatus, WebSocketException
from websockets.sync.client import connect

from mac_ai import config
from mac_ai.assistant import CommandRouter, command_worker, result_event, run_voice_loop
from mac_ai.stt.completeness import INCOMPLETE, UNKNOWN
from mac_ai.ui_bus import UiBus, parse_ui_message
from mac_ai.wake import WakeGate

TOKEN = "test-token"


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def bus():
    warnings = []
    b = UiBus(port=0, token=TOKEN, warn=warnings.append)
    b.warnings = warnings
    b.start()
    yield b
    b.stop()


def open_client(bus, **kw):
    return connect(bus.url, **kw)


def recv_json(ws, timeout=3.0):
    return json.loads(ws.recv(timeout=timeout))


def recv_until(ws, kind, timeout=3.0):
    """讀到指定 type 的事件為止，回傳（含）之前讀到的全部事件"""
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = recv_json(ws, max(0.05, deadline - time.monotonic()))
        seen.append(msg)
        if msg["type"] == kind:
            return seen
    raise AssertionError(f"沒收到 {kind}：{[m['type'] for m in seen]}")


# ---------- 訊息驗證 ----------
@pytest.mark.parametrize("raw, ok", [
    ('{"type":"command","text":"打開小算盤"}', True),
    ('{"type":"command","text":"  打開小算盤  "}', True),
    ('{"type":"command","text":"' + "字" * 200 + '"}', True),
    ('{"type":"command","text":"' + "字" * 201 + '"}', False),
    ('{"type":"command","text":""}', False),
    ('{"type":"command","text":"   "}', False),
    ('{"type":"command"}', False),
    ('{"type":"command","text":5}', False),
    ('{"type":"wake"}', True),
    ('{"type":"quit"}', True),
    ('{"type":"pause","value":true}', True),
    ('{"type":"pause","value":"yes"}', False),
    ('{"type":"pause"}', False),
    ('{"type":"shutdown"}', False),
    ('{"type":null}', False),
    ('[1,2]', False),
    ('not json', False),
    (b'{"type":"wake"}', False),
])
def test_parse_ui_message(raw, ok):
    msg, error = parse_ui_message(raw)
    assert (msg is not None) == ok and (error is None) == ok


def test_parse_command_strips_text():
    assert parse_ui_message('{"type":"command","text":"  打開小算盤 "}')[0] == {"type": "command", "text": "打開小算盤"}


# ---------- B1：事件順序與內容（假 STT → 閘門 → 指令執行 → 匯流排） ----------
def test_events_for_a_spoken_command_in_order(bus):
    server = FakeSttServer(events=[
        {"type": "partial", "text": "露醬打開小算"},
        {"type": "final", "text": "露醬 打開小算盤"},
    ])
    outcome = {"steps": [{"kind": "fast", "text": "打開小算盤", "ms": 3.0, "status": "ok", "logs": ["已開啟 小算盤"]}],
               "replies": [], "total_ms": 4.0, "stages": {}}
    stop = threading.Event()
    with open_client(bus) as ws:
        assert recv_json(ws)["type"] == "hello"
        t = threading.Thread(target=run_voice_loop, args=(FakeAudio(), lambda cmd: outcome, stop), daemon=True,
                             kwargs={"url": server.url, "say": lambda m: None, "gate": WakeGate(enabled=True),
                                     "endpoint_mode": "server", "bus": bus})
        t.start()
        try:
            events = recv_until(ws, "result")
        finally:
            stop.set()
            t.join(timeout=5)
            server.stop()
    kinds = [e["type"] for e in events]
    wanted = ["partial", "heard", "session", "executing", "result"]
    positions = [kinds.index(k) for k in wanted]
    assert positions == sorted(positions), kinds
    ids = [e["id"] for e in events]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    by = {e["type"]: e for e in events}
    assert by["partial"]["text"] == "露醬打開小算" and by["partial"]["wake"] is True
    assert (by["heard"]["status"], by["heard"]["command"]) == ("command", "打開小算盤")
    assert by["session"]["remaining_s"] == pytest.approx(config.WAKE_SESSION_S, abs=1.0)
    assert by["executing"]["command"] == "打開小算盤"
    res = by["result"]
    assert res["ok"] is True and res["command"] == "打開小算盤" and res["total_ms"] == 4.0
    assert res["steps"] == [{"kind": "fast", "text": "打開小算盤", "status": "ok", "logs": ["已開啟 小算盤"]}]
    assert all(isinstance(e["ts"], float) for e in events)


def test_hello_carries_state_snapshot(bus):
    gate = WakeGate(enabled=True)
    router = CommandRouter(gate, queue.Queue(), lambda m: None)
    bus.bind(router.on_ui_message, router.hello_state)
    with open_client(bus) as ws:
        hello = recv_json(ws)
    assert hello["type"] == "hello" and hello["id"] == 0 and hello["version"] == 1
    assert (hello["wake_enabled"], hello["session_s"], hello["paused"]) == (True, config.WAKE_SESSION_S, False)


@pytest.mark.parametrize("statuses, ok", [
    (["ok", "ok"], True),
    (["ok", "exec_failed"], False),
    (["llm_unavailable"], False),
    (["llm_error"], False),
    (["skipped", "fallback"], True),
    ([], True),
])
def test_result_event_ok_flag(statuses, ok):
    outcome = {"steps": [{"kind": "fast", "text": "x", "status": s, "logs": []} for s in statuses], "replies": ["好"], "total_ms": 1.0}
    assert result_event("x", outcome)["ok"] is ok


def test_result_event_tolerates_non_dict_outcome():
    ev = result_event("x", None)
    assert ev["ok"] is True and ev["steps"] == [] and ev["replies"] == []


def test_command_worker_publishes_failed_result_when_execute_raises(capsys):
    events, commands, stop = [], queue.Queue(), threading.Event()

    def boom(cmd):
        raise RuntimeError("壞掉了")

    t = threading.Thread(target=command_worker, args=(commands, boom, stop, events.append), daemon=True)
    t.start()
    commands.put("x")
    assert wait_until(lambda: len(events) == 2)
    stop.set()
    t.join(timeout=2)
    assert [e["type"] for e in events] == ["executing", "result"] and events[1]["ok"] is False


def test_stt_events_are_published(bus):
    router = CommandRouter(WakeGate(enabled=False), queue.Queue(), lambda m: None, publish=bus.publish)
    with open_client(bus) as ws:
        recv_json(ws)
        router({"type": "connected"})
        router({"type": "disconnected", "message": "斷了"})
        router({"type": "error", "code": "E1", "message": "壞"})
        got = [recv_json(ws) for _ in range(3)]
    assert [(e["type"], e["state"]) for e in got] == [("stt", "connected"), ("stt", "disconnected"), ("stt", "error")]
    assert got[1]["message"] == "斷了" and got[2]["message"] == "E1 壞"


# ---------- B2：轉送延遲 ----------
def test_forwarding_latency_p95_under_20ms(bus):
    n = 1000
    with open_client(bus) as ws:
        recv_json(ws)

        def produce():
            for i in range(n):
                bus.publish({"type": "partial", "text": str(i), "wake": False})
                time.sleep(0.002)

        threading.Thread(target=produce, daemon=True).start()
        delays = []
        for _ in range(n):
            msg = recv_json(ws, timeout=5)
            delays.append((time.time() - msg["ts"]) * 1000)
    p95 = statistics.quantiles(delays, n=20)[18]
    assert p95 <= 20, f"p95={p95:.2f}ms"


# ---------- B3：不阻塞 ----------
def test_publish_without_clients_is_nearly_free(bus):
    n = 20000
    t = time.perf_counter()
    for _ in range(n):
        bus.publish({"type": "partial", "text": "x", "wake": False})
    avg_ms = (time.perf_counter() - t) * 1000 / n
    assert avg_ms <= 0.05, f"avg={avg_ms:.4f}ms"


def test_publish_never_blocks_on_a_client_that_does_not_read(bus):
    with open_client(bus):  # 連上後完全不讀
        assert wait_until(lambda: bus.client_count() == 1)
        t = time.perf_counter()
        for i in range(10000):
            bus.publish({"type": "partial", "text": "x" * 200, "wake": False})
        elapsed = time.perf_counter() - t
        assert elapsed <= 0.1, f"{elapsed * 1000:.1f}ms"
        assert len(bus._clients[0].queue) <= config.UI_QUEUE_MAX


def test_slow_client_keeps_only_newest_events(bus):
    with open_client(bus) as ws:
        recv_json(ws)
        assert wait_until(lambda: bus.client_count() == 1)
        client = bus._clients[0]
        for i in range(config.UI_QUEUE_MAX * 3):  # 佇列滿了丟最舊的
            client.queue.append(json.dumps({"type": "partial", "id": i}))
        assert len(client.queue) == config.UI_QUEUE_MAX
        assert json.loads(client.queue[0])["id"] == config.UI_QUEUE_MAX * 2


# ---------- B4：連線驗證 ----------
@pytest.mark.parametrize("url_suffix, kw", [
    ("", {}),  # 沒帶 token
    ("?token=wrong", {}),  # 錯的 token
    ("?token=" + TOKEN + "x", {}),
    ("?token=" + TOKEN, {"origin": "http://example.com"}),  # 對的 token，但是網頁來的（有 Origin）
    ("?token=wrong", {"origin": "http://example.com"}),
])
def test_bad_connections_are_rejected_quickly(bus, url_suffix, kw):
    t = time.monotonic()
    with pytest.raises((InvalidStatus, WebSocketException)):
        connect(f"ws://127.0.0.1:{bus.port}/{url_suffix}", **kw).close()
    assert time.monotonic() - t < 1.0
    assert bus.client_count() == 0


def test_correct_token_without_origin_connects(bus):
    with open_client(bus) as ws:
        assert recv_json(ws)["type"] == "hello"


def test_listens_on_loopback_only(bus):
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{bus.port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
    assert f"127.0.0.1:{bus.port}" in out and "*:" not in out and "0.0.0.0" not in out, out


def health(bus):
    conn = http.client.HTTPConnection("127.0.0.1", bus.port, timeout=2)
    conn.request("GET", "/health")
    return json.loads(conn.getresponse().read())


def test_health_endpoint_reports_client_count_without_token(bus):
    assert health(bus) == {"status": "ok", "clients": 0}
    with open_client(bus):
        assert wait_until(lambda: health(bus)["clients"] == 1)


def test_bus_requires_a_token():
    with pytest.raises(ValueError):
        UiBus(port=0, token="")


def test_port_in_use_raises_oserror(bus):
    other = UiBus(port=bus.port, token="x", warn=lambda m: None)
    with pytest.raises(OSError):
        other.start()


# ---------- B5：UI 指令 ----------
def make_router(bus, clock=None, enabled=True):
    commands, said = queue.Queue(), []
    gate = WakeGate(["露醬"], clock=clock or Clock(), enabled=enabled)
    router = CommandRouter(gate, commands, said.append, publish=bus.publish)
    bus.bind(router.on_ui_message, router.hello_state)
    return router, commands, gate


def test_ui_command_is_queued_exactly_once_without_wake_word(bus):
    router, commands, gate = make_router(bus)
    with open_client(bus) as ws:
        ws.send(json.dumps({"type": "command", "text": "打開小算盤"}))
        assert wait_until(lambda: commands.qsize() == 1)
        time.sleep(0.1)
    assert list(commands.queue) == ["打開小算盤"]
    assert gate.session_remaining() == 0  # 打字不開啟會話


def test_invalid_ui_messages_are_dropped_without_closing_connection(bus):
    router, commands, gate = make_router(bus)
    with open_client(bus) as ws:
        for bad in ('{"type":"command","text":""}', '{"type":"command","text":"' + "字" * 201 + '"}', "not json",
                    '{"type":"nope"}', b"\x00\x01"):
            ws.send(bad)
        ws.send(json.dumps({"type": "command", "text": "好的"}))  # 前面全丟掉，連線仍可用
        assert wait_until(lambda: commands.qsize() == 1)
    assert list(commands.queue) == ["好的"]
    assert len(bus.warnings) == 5


# ---------- B6：wake／pause／quit ----------
def test_wake_message_starts_session_so_next_sentence_runs(bus):
    router, commands, gate = make_router(bus)
    with open_client(bus) as ws:
        recv_json(ws)
        ws.send(json.dumps({"type": "wake"}))
        ev = recv_until(ws, "session")[-1]
    assert ev["remaining_s"] == pytest.approx(config.WAKE_SESSION_S, abs=0.01)
    router({"type": "final", "text": "打開小算盤"})
    assert list(commands.queue) == ["打開小算盤"]


def test_pause_ends_session_drops_sentences_and_resume_needs_wake_word(bus):
    router, commands, gate = make_router(bus)
    gate.start_session()
    with open_client(bus) as ws:
        recv_json(ws)
        ws.send(json.dumps({"type": "pause", "value": True}))
        events = recv_until(ws, "session")
        assert [e["type"] for e in events] == ["paused", "session"]
        assert events[0]["value"] is True and events[1]["remaining_s"] == 0
        router({"type": "final", "text": "打開小算盤"})
        router({"type": "final", "text": "露醬，關掉小算盤"})
        assert commands.qsize() == 0  # 暫停：整句丟棄
        ws.send(json.dumps({"type": "pause", "value": False}))
        assert recv_until(ws, "paused")[-1]["value"] is False
    router({"type": "final", "text": "打開小算盤"})
    assert commands.qsize() == 0  # 會話已結束，沒有喚醒詞不執行
    router({"type": "final", "text": "露醬，打開小算盤"})
    assert list(commands.queue) == ["打開小算盤"]


def test_wake_is_ignored_while_paused(bus):
    router, commands, gate = make_router(bus)
    router.on_ui_message({"type": "pause", "value": True})
    router.on_ui_message({"type": "wake"})
    assert gate.session_remaining() == 0


def test_quit_message_sets_stop_event():
    stop = threading.Event()
    router = CommandRouter(WakeGate(enabled=False), queue.Queue(), lambda m: None, on_quit=stop.set)
    router.on_ui_message({"type": "quit"})
    assert stop.is_set()


def test_no_bus_means_no_events_and_same_behavior():
    commands = queue.Queue()
    router = CommandRouter(WakeGate(["露醬"], enabled=True, clock=Clock()), commands, lambda m: None)
    router({"type": "partial", "text": "露醬"})
    router({"type": "final", "text": "露醬，打開小算盤"})
    assert list(commands.queue) == ["打開小算盤"]


# ---------- W7：UI 模式的端點判斷與閘門一致 ----------
@pytest.mark.parametrize("enabled, expected", [(True, INCOMPLETE), (False, UNKNOWN)])
def test_endpoint_classifier_follows_the_gate(monkeypatch, enabled, expected):
    import mac_ai.assistant as assistant

    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def run(self, stop):
            pass

    monkeypatch.setattr(assistant, "SttClient", FakeClient)
    monkeypatch.setattr(assistant, "SileroVad", lambda: None)
    monkeypatch.setattr(assistant, "JsonlLog", lambda: type("L", (), {"write": lambda self, r: None})())
    monkeypatch.setattr(assistant, "EndpointDetector", lambda **kw: captured.update(kw))
    run_voice_loop(None, lambda c: None, threading.Event(), gate=WakeGate(enabled=enabled), endpoint_mode="client")
    assert captured["classifier"]("露西婭") == expected
