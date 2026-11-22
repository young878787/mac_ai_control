"""X-ASR 辨識伺服器的 WS 協定 v1（與 macos-stt 同一份）：事件順序與欄位、錯誤碼與關閉碼、安全網、上限與 /health。
辨識引擎用假的（依送進去的音訊長度吐預先寫好的文字），所以不載入模型；最後一組用真的模型與 bench/human 的真人錄音，缺檔就 skip。
每個斷言對應的 macos-stt 行為出處寫在註解（macos-stt/src/macos_stt/server.py、session.py）。"""
import asyncio
import csv
import json
import threading
import time
import urllib.error
import wave
from contextlib import ExitStack, contextmanager
from pathlib import Path

import numpy as np
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from mac_ai import config
from mac_ai.stt import xasr_server
from mac_ai.stt.xasr_server import XasrServer
from support.real import REAL_URLOPEN

SR = 16000
ROOT = Path(__file__).resolve().parents[2]


# ---------- 假引擎：依累積的音訊長度吐文字，記錄收尾補了多少靜音 ----------
class FakeStream:
    def __init__(self, engine):
        self.engine = engine
        self.samples = 0

    def feed(self, samples) -> str:
        if self.engine.feed_delay_s:
            time.sleep(self.engine.feed_delay_s)
        self.samples += len(samples)
        return self.text()

    def text(self) -> str:
        shown = ""
        for at_s, text in self.engine.script:
            if self.samples >= round(at_s * SR):
                shown = text
        return shown

    def finish(self, pad_s: float) -> str:
        self.engine.finish_pads.append(pad_s)
        return self.text() + self.engine.final_suffix


class FakeEngine:
    """script：[(累積音訊秒數, 文字)]，到了那個長度 partial 就變成該文字"""
    model_name = "fake-model"
    hotwords = 43
    hotwords_skipped = 2

    def __init__(self, script=((0.2, "打開"), (0.4, "打開小算盤")), final_suffix="", feed_delay_s=0.0):
        self.script, self.final_suffix, self.feed_delay_s = list(script), final_suffix, feed_delay_s
        self.streams = []
        self.finish_pads = []

    def new_stream(self):
        self.streams.append(FakeStream(self))
        return self.streams[-1]


def pcm(seconds: float, value: int = 0) -> bytes:
    return np.full(int(seconds * SR), value, dtype="<i2").tobytes()


CONFIG = {"type": "config", "lang": "zh-TW", "sample_rate": 16000, "hints": [], "multi_utterance": False,
          "vad_flush_seconds": 3.0, "flush_pre_delay_seconds": 0.0}


class Running:
    """在背景執行緒的 event loop 裡跑一台 XasrServer；回傳 url 與 http 位址"""

    def __init__(self, engine, **kw):
        self.logs = []
        kw.setdefault("tail_pad_s", 0.6)
        self.server = XasrServer(engine, log=self.logs.append, **kw)
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.ws_server = self.loop.run_until_complete(self.server.start("127.0.0.1", 0))
            self.port = self.ws_server.sockets[0].getsockname()[1]
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert ready.wait(5)
        self.url = f"ws://127.0.0.1:{self.port}/stream"
        self.http = f"http://127.0.0.1:{self.port}"

    def stop(self):
        async def shutdown():
            self.ws_server.close()
            await self.ws_server.wait_closed()
            self.server.close()
        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()

    def health(self, path="/health"):
        with REAL_URLOPEN(self.http + path, timeout=2) as resp:
            return json.loads(resp.read())


@pytest.fixture
def serve():
    started = []

    def start(engine=None, **kw):
        started.append(Running(engine or FakeEngine(), **kw))
        return started[-1]

    yield start
    for s in started:
        s.stop()


@contextmanager
def open_session(running, cfg=None):
    with connect(running.url, open_timeout=5) as ws:
        ws.send(json.dumps(CONFIG if cfg is None else cfg))
        yield ws


def recv_event(ws, timeout=3.0):
    return json.loads(ws.recv(timeout=timeout))


def recv_until(ws, kind, timeout=3.0):
    """收事件直到出現 kind；回傳 (該事件, 之前收到的事件)"""
    seen, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = recv_event(ws, max(0.05, deadline - time.monotonic()))
        if event["type"] == kind:
            return event, seen
        seen.append(event)
    raise AssertionError(f"沒收到 {kind}：{seen}")


def no_event(ws, wait=0.4):
    try:
        ws.recv(timeout=wait)
    except TimeoutError:
        return True
    return False


# ---------- A1：config → PCM → flush → final ----------
def test_a1_config_pcm_flush_final_event_order_and_fields(serve):
    engine = FakeEngine()
    running = serve(engine)
    with open_session(running) as ws:
        ws.send(pcm(0.2))
        assert recv_event(ws) == {"type": "partial", "text": "打開"}
        ws.send(pcm(0.2))
        assert recv_event(ws) == {"type": "partial", "text": "打開小算盤"}
        ws.send(json.dumps({"type": "flush"}))
        final = recv_event(ws)
        assert final["type"] == "final" and final["text"] == "打開小算盤"
        # macos-stt session.py _timing_field：reason／pre_delay_ms／finish_ms 三個欄位
        assert set(final["timing"]) == {"reason", "pre_delay_ms", "finish_ms"}
        assert final["timing"]["reason"] == "client_flush"
        assert final["timing"]["pre_delay_ms"] == 600.0  # 回報補進去的尾端靜音（--tail-pad）
        assert 0 <= final["timing"]["finish_ms"] < 1000
        assert recv_event(ws) == {"type": "session_restarted"}  # macos-stt _flush_tail：final 之後重開並通知
    assert engine.finish_pads == [0.6]


def test_a1_partial_is_sent_only_when_text_changes_and_never_empty(serve):
    engine = FakeEngine(script=[(0.4, "打開")])
    running = serve(engine)
    with open_session(running) as ws:
        ws.send(pcm(0.2))
        assert no_event(ws)  # 還沒有文字：不送空的 partial
        ws.send(pcm(0.2))
        assert recv_event(ws) == {"type": "partial", "text": "打開"}
        ws.send(pcm(0.2))
        assert no_event(ws)  # 文字沒變：不重送


def test_a1_flush_restarts_with_a_fresh_stream(serve):
    engine = FakeEngine()
    running = serve(engine)
    with open_session(running) as ws:
        ws.send(pcm(0.4))
        recv_until(ws, "partial")
        ws.send(json.dumps({"type": "flush"}))
        recv_until(ws, "session_restarted")
        ws.send(pcm(0.2))
        assert recv_event(ws) == {"type": "partial", "text": "打開"}  # 第二句從頭算，不接在上一句後面
    assert len(engine.streams) == 2


def test_a1_flush_without_any_text_does_nothing(serve):
    # macos-stt session.flush：這一句還沒有任何語音／文字時什麼都不做（不送 final、不送 session_restarted）
    engine = FakeEngine()
    running = serve(engine)
    with open_session(running) as ws:
        ws.send(pcm(0.1))  # 還不到出字的長度
        ws.send(json.dumps({"type": "flush"}))
        assert no_event(ws)
        ws.send(pcm(0.2))
        assert recv_event(ws) == {"type": "partial", "text": "打開"}  # 同一條 stream 繼續用
    assert engine.finish_pads == [] and len(engine.streams) == 1


def test_a1_final_can_extend_the_last_partial(serve):
    engine = FakeEngine(final_suffix="螢幕")  # 補尾端靜音後模型才吐出最後的字
    running = serve(engine)
    with open_session(running) as ws:
        ws.send(pcm(0.4))
        recv_until(ws, "partial")
        ws.send(json.dumps({"type": "flush"}))
        assert recv_until(ws, "final")[0]["text"] == "打開小算盤螢幕"


def test_a1_odd_sized_frames_are_carried_not_dropped(serve):
    engine = FakeEngine()
    running = serve(engine)
    data = pcm(0.2)
    with open_session(running) as ws:
        ws.send(data[:-1])  # 少 1 byte
        assert no_event(ws, 0.2)
        ws.send(data[-1:] + pcm(0.2))  # 補回那 1 byte，再加 0.2 秒：合計正好 0.4 秒（6400 個取樣）
        assert recv_event(ws) == {"type": "partial", "text": "打開小算盤"}
    assert engine.streams[0].samples == int(0.4 * SR)


def test_a1_close_message_ends_the_session_without_a_final(serve):
    # macos-stt feed 收到 close 就結束收音迴圈；session.close 只做收尾，不再送事件
    engine = FakeEngine()
    running = serve(engine)
    with open_session(running) as ws:
        ws.send(pcm(0.4))
        recv_until(ws, "partial")
        ws.send(json.dumps({"type": "close"}))
        with pytest.raises(ConnectionClosed):
            while True:
                assert recv_event(ws)["type"] != "final"
    assert running.health()["sessions_active"] == 0


def test_a1_unknown_and_malformed_text_messages_are_ignored(serve):
    # macos-stt server.py：JSON 壞掉或沒有認得的 type 都直接略過
    running = serve()
    with open_session(running) as ws:
        ws.send("not json")
        ws.send(json.dumps({"type": "ping"}))
        ws.send(pcm(0.2))
        assert recv_event(ws) == {"type": "partial", "text": "打開"}


def test_a1_extra_hints_are_ignored_and_logged_known_ones_are_not(serve):
    running = serve(known_hints=["Chrome", "vscode"])
    with open_session(running, {**CONFIG, "hints": ["chrome", "VSCode", "新詞", "另一個"]}) as ws:
        ws.send(pcm(0.2))
        recv_event(ws)
    logs = [x for x in running.logs if "提示詞" in x]
    assert len(logs) == 1 and "2 個" in logs[0]  # 只有啟動時沒有的兩個；大小寫不同的算已知


# ---------- A2：錯誤路徑 ----------
def expect_error_then_close(ws, code, close_code):
    event = recv_event(ws)
    assert event["type"] == "error" and event["code"] == code and event["message"]
    with pytest.raises(ConnectionClosed) as info:
        while True:
            ws.recv(timeout=3)
    assert info.value.rcvd.code == close_code


@pytest.mark.parametrize("cfg", [
    {**CONFIG, "sample_rate": 8000},  # server.py：only sample_rate 16000 supported in v1
    {**CONFIG, "lang": "fr-FR"},
    {**CONFIG, "multi_utterance": True},  # 本引擎不支援引擎自斷句；老實拒絕，不假裝支援
    {"type": "flush"},  # 第一則不是 config
    {"lang": "zh-TW"},  # 沒有 type
])
def test_a2_bad_config_is_protocol_error_1002(serve, cfg):
    running = serve()
    with connect(running.url, open_timeout=5) as ws:
        ws.send(json.dumps(cfg))
        expect_error_then_close(ws, "protocol", 1002)
    assert running.health()["sessions_active"] == 0


def test_a2_first_message_not_json_or_binary_is_protocol_error(serve):
    running = serve()
    for first in ("{oops", b"\x00\x01"):
        with connect(running.url, open_timeout=5) as ws:
            ws.send(first)
            expect_error_then_close(ws, "protocol", 1002)


def test_a2_config_must_arrive_in_time(serve):
    running = serve(config_timeout_s=0.3)  # 產品是 10 秒（macos-stt _WS_CONFIG_TIMEOUT）
    with connect(running.url, open_timeout=5) as ws:
        expect_error_then_close(ws, "protocol", 1002)


def test_a2_audio_frame_over_256kb_is_protocol_error(serve):
    running = serve()
    with open_session(running) as ws:
        ws.send(b"\x00" * (256 * 1024 + 2))
        expect_error_then_close(ws, "protocol", 1002)


def test_a2_too_many_sessions_is_1013_and_slot_is_released_afterwards(serve):
    running = serve(max_sessions=2)
    with ExitStack() as stack:
        first, second = (stack.enter_context(open_session(running)) for _ in range(2))
        for ws in (first, second):
            ws.send(pcm(0.2))
            recv_event(ws)
        assert running.health()["sessions_active"] == 2
        with open_session(running) as third:
            event = recv_event(third)
            assert event["type"] == "error" and event["code"] == "too_many_sessions"  # macos-stt：同樣的 code 與 1013
            with pytest.raises(ConnectionClosed) as info:
                third.recv(timeout=3)
            assert info.value.rcvd.code == 1013
        first.close()
        deadline = time.monotonic() + 3
        while running.health()["sessions_active"] != 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        with open_session(running) as again:
            again.send(pcm(0.2))
            assert recv_event(again)["type"] == "partial"


def test_a2_engine_failure_is_internal_error_1011_and_server_keeps_running(serve):
    class Broken(FakeEngine):
        def new_stream(self):
            s = super().new_stream()
            if len(self.streams) == 1:
                s.feed = lambda samples: (_ for _ in ()).throw(RuntimeError("boom"))
            return s

    running = serve(Broken())
    with open_session(running) as ws:
        ws.send(pcm(0.2))
        expect_error_then_close(ws, "internal", 1011)
    with open_session(running) as ws:  # 下一條連線不受影響
        ws.send(pcm(0.2))
        assert recv_event(ws)["type"] == "partial"
    assert running.health()["sessions_active"] == 0


# ---------- A3：安全網（client 沒送 flush） ----------
def test_a3_silence_after_text_finalizes_as_server_vad(serve):
    # macos-stt session._process_block：靜音超過 vad_flush_seconds 且有文字 → _flush_tail("server_vad")，之後 session_restarted
    # 這裡的「靜音」以音訊時間計（文字停止變動的長度），所以快速送音訊也一樣觸發，測試不用真的等
    engine = FakeEngine(script=[(0.2, "打開小算盤")])
    running = serve(engine)
    with open_session(running, {**CONFIG, "vad_flush_seconds": 1.0}) as ws:
        ws.send(pcm(0.2))
        assert recv_event(ws)["type"] == "partial"  # 文字在音訊 0.2 秒時定稿
        for _ in range(8):
            ws.send(pcm(0.1))  # 音訊 1.0 秒：文字已經停了 0.8 秒，還不到
        assert no_event(ws)
        for _ in range(3):
            ws.send(pcm(0.1))  # 音訊 1.3 秒：停了 1.1 秒，超過
        event = recv_event(ws)
        assert event["type"] == "final" and event["text"] == "打開小算盤"
        assert event["timing"]["reason"] == "server_vad"
        assert recv_event(ws) == {"type": "session_restarted"}
        assert no_event(ws, 0.3)  # 收過一次就不會重複收


def test_a3_silence_without_any_text_never_finalizes(serve):
    running = serve(FakeEngine(script=[]))
    with open_session(running, {**CONFIG, "vad_flush_seconds": 0.5}) as ws:
        for _ in range(20):
            ws.send(pcm(0.1))
        assert no_event(ws, 0.5)


def test_a3_changing_text_keeps_resetting_the_silence_timer(serve):
    engine = FakeEngine(script=[(0.3 * i, "字" * i) for i in range(1, 10)])
    running = serve(engine)
    with open_session(running, {**CONFIG, "vad_flush_seconds": 0.5}) as ws:
        for _ in range(27):  # 2.7 秒，文字每 0.3 秒變一次，間隔都小於 0.5 秒
            ws.send(pcm(0.1))
        events = [recv_event(ws) for _ in range(9)]
        assert all(e["type"] == "partial" for e in events)
        assert no_event(ws, 0.3)


def test_a3_max_session_rotates_with_final_when_there_is_text(serve):
    # macos-stt：session 超過 max_session_seconds 就收掉重開（reason=max_session），一定送 session_restarted
    engine = FakeEngine(script=[(0.2, "打開小算盤")])
    running = serve(engine)
    with open_session(running, {**CONFIG, "vad_flush_seconds": 100.0, "max_session_seconds": 1.0}) as ws:
        for _ in range(11):
            ws.send(pcm(0.1))
        final, _ = recv_until(ws, "final")
        assert final["timing"]["reason"] == "max_session"
        assert recv_event(ws) == {"type": "session_restarted"}


def test_a3_max_session_without_text_restarts_silently_but_notifies(serve):
    engine = FakeEngine(script=[])
    running = serve(engine)
    with open_session(running, {**CONFIG, "vad_flush_seconds": 100.0, "max_session_seconds": 1.0}) as ws:
        for _ in range(11):
            ws.send(pcm(0.1))
        assert recv_event(ws) == {"type": "session_restarted"}
    assert len(engine.streams) == 2


# ---------- /health ----------
def test_health_reports_engine_model_hotwords_and_sessions(serve):
    running = serve(FakeEngine(), max_sessions=2)
    health = running.health()
    assert health == {"status": "ok", "engine": "xasr", "model": "fake-model", "sessions_active": 0, "max_sessions": 2,
                      "default_lang": "zh-TW", "hotwords": 43, "hotwords_skipped": 2}
    with open_session(running) as ws:
        ws.send(pcm(0.2))
        recv_event(ws)
        assert running.health()["sessions_active"] == 1


def test_unknown_http_path_is_404(serve):
    running = serve()
    with pytest.raises(urllib.error.HTTPError) as info:
        running.health("/nope")
    assert info.value.code == 404


def test_slow_decoding_does_not_block_health(serve):
    running = serve(FakeEngine(feed_delay_s=0.6))
    with open_session(running) as ws:
        ws.send(pcm(0.2))
        time.sleep(0.1)  # 解碼中（要 0.6 秒）
        started = time.monotonic()
        assert running.health()["status"] == "ok"
        assert time.monotonic() - started < 0.3
        assert recv_event(ws)["type"] == "partial"


# ---------- 真的模型（缺檔就 skip） ----------
MODEL_FILES = ("encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", config.XASR_TOKENS)
MISSING = [f for f in MODEL_FILES if not (config.XASR_MODEL_DIR / f).is_file()]


def first_human_clip(category="A"):
    manifest = ROOT / "bench" / "human" / "manifest.csv"
    if not manifest.is_file():
        return None
    for row in csv.DictReader(manifest.open(encoding="utf-8")):
        wav = ROOT / "bench" / "human" / f"{row['clip']}.wav"
        if row["category"] == category and wav.is_file():
            return row, wav
    return None


@pytest.mark.skipif(bool(MISSING), reason=f"沒有 X-ASR 模型檔：{MISSING}（下載方式見 README）")
def test_real_model_streams_a_human_clip_and_flushes():
    clip = first_human_clip()
    if clip is None:
        pytest.skip("bench/human 沒有真人錄音（*.wav 不入版控，要自己錄）")
    row, wav = clip
    with wave.open(str(wav)) as w:
        audio = w.readframes(w.getnframes())
    samples = np.frombuffer(audio, dtype="<i2")
    loud = np.flatnonzero(np.abs(samples[: len(samples) // 320 * 320].reshape(-1, 320)).max(axis=1) > 1500)
    audio = audio[: min(len(audio), (int(loud[-1] + 1) * 320 + int(0.35 * SR)) * 2)]  # 說完後 0.35 秒就 flush，不送完尾端靜音
    engine = xasr_server.SherpaEngine(config.XASR_MODEL_DIR, config.XASR_TOKENS,
                                      hints=config.STT_HINTS_PATH.read_text(encoding="utf-8").splitlines(),
                                      threads=config.XASR_THREADS, hotwords_score=config.XASR_HOTWORDS_SCORE)
    assert engine.model_name == config.XASR_MODEL_DIR.name and engine.hotwords > 0
    running = Running(engine, tail_pad_s=config.XASR_TAIL_PAD_S)
    try:
        with open_session(running) as ws:
            step = int(0.096 * SR) * 2
            for i in range(0, len(audio), step):
                ws.send(audio[i:i + step])
            ws.send(json.dumps({"type": "flush"}))
            final, partials = recv_until(ws, "final", timeout=10)
        assert final["text"].strip() and final["timing"]["reason"] == "client_flush"
        assert final["timing"]["pre_delay_ms"] == config.XASR_TAIL_PAD_S * 1000
        assert final["timing"]["finish_ms"] < 150  # 計畫 S4：伺服器實際收尾時間
        assert any(e["type"] == "partial" for e in partials)
        health = running.health()
        assert health["model"] == config.XASR_MODEL_DIR.name and health["hotwords"] > 0
    finally:
        running.stop()
