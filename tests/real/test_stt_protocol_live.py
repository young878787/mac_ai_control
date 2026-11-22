"""實機測試：對「正在 8765 埠執行的 STT 伺服器」跑同一組協定案例，macos-stt 與 xasr 各跑一次，兩邊行為必須一致。
只用靜音與壞訊息，所以不需要麥克風、也不看辨識內容。
跑法：先起其中一個伺服器，再 pytest -m live tests/real/test_stt_protocol_live.py -v（換引擎重跑一次）。
macos-stt 的出處：macos-stt/src/macos_stt/server.py（ws_stream）與 session.py（flush、close）。"""
import json
import urllib.request

import numpy as np
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from mac_ai import config

pytestmark = pytest.mark.live

CONFIG = {"type": "config", "lang": "zh-TW", "sample_rate": 16000, "hints": [], "multi_utterance": False,
          "vad_flush_seconds": 3.0, "flush_pre_delay_seconds": 0.0}
SILENCE_1S = np.zeros(16000, dtype="<i2").tobytes()


@pytest.fixture(scope="module", autouse=True)
def engine():
    try:
        with urllib.request.urlopen(f"{config.STT_URL}/health", timeout=3) as resp:
            health = json.load(resp)
    except OSError:
        pytest.skip("STT 伺服器沒有在執行（先起 macos-stt 或 python -m mac_ai.stt.xasr_server）")
    print(f"\n對 {'xasr' if health.get('engine') == 'xasr' else 'macos-stt'} 執行：{health}")
    return health


def expect_protocol_error(ws):
    event = json.loads(ws.recv(timeout=5))
    assert event["type"] == "error" and event["code"] == "protocol" and event["message"]
    with pytest.raises(ConnectionClosed) as info:
        ws.recv(timeout=5)
    assert info.value.rcvd.code == 1002


def test_health_has_the_fields_the_bench_and_services_read(engine):
    assert engine["status"] == "ok" and engine["sessions_active"] == 0 and engine["max_sessions"] >= 2


@pytest.mark.parametrize("first", [
    {**CONFIG, "sample_rate": 8000},
    {"type": "flush"},
    {"lang": "zh-TW"},
])
def test_bad_first_message_is_protocol_error_1002(first):
    with connect(config.STT_WS_URL, open_timeout=5) as ws:
        ws.send(json.dumps(first))
        expect_protocol_error(ws)


def test_first_message_not_json_is_protocol_error_1002():
    with connect(config.STT_WS_URL, open_timeout=5) as ws:
        ws.send("{oops")
        expect_protocol_error(ws)


def test_silence_then_flush_produces_no_events():
    with connect(config.STT_WS_URL, open_timeout=5) as ws:
        ws.send(json.dumps(CONFIG))
        for _ in range(3):
            ws.send(SILENCE_1S)
        ws.send(json.dumps({"type": "flush"}))
        with pytest.raises(TimeoutError):
            ws.recv(timeout=1.5)


def test_close_message_ends_the_connection_without_events():
    with connect(config.STT_WS_URL, open_timeout=5) as ws:
        ws.send(json.dumps(CONFIG))
        ws.send(SILENCE_1S)
        ws.send(json.dumps({"type": "close"}))
        with pytest.raises(ConnectionClosed):
            while True:
                assert json.loads(ws.recv(timeout=5))["type"] not in ("partial", "final", "utterance")
