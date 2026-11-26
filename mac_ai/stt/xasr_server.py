"""X-ASR 辨識伺服器：說與 macos-stt 相同的 WS 協定 v1（WS /stream、GET /health），後端是 sherpa-onnx 的串流 transducer。

client（SttClient、端點偵測、stt_bench）完全不用改，換引擎只是換一個子程序。
    python -m mac_ai.stt.xasr_server [--port 8765] [--model-dir DIR] [--tokens FILE] [--hints FILE] ...

協定（macos-stt/docs/fastapi-server-plan.md §4.2）：首則 config；之後 binary int16 mono 16 kHz PCM、{"type":"flush"}、{"type":"close"}。
回 partial／final（帶 timing）／session_restarted／error。設計與取捨見 docs/plans/completed/2026-11-21-stt-engine-xasr.md §4。
只綁 127.0.0.1。推論都在同一條工作執行緒序列執行，event loop 不會被擋住（/health 隨時回得出來）。"""
import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from typing import Callable, Iterable, List, Optional

import numpy as np
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from mac_ai import config
from mac_ai.stt.xasr_hotwords import encode_hotwords, load_tokens

SAMPLE_RATE = 16000
LANGS = ("zh-TW", "en-US")  # 模型本來就中英混合，語言只做檢查，不切模型
MAX_FRAME_BYTES = 256 * 1024  # 與 macos-stt 相同
CONFIG_TIMEOUT_S = 10.0  # 與 macos-stt 的 _WS_CONFIG_TIMEOUT 相同
MAX_SESSIONS = 2  # 一次只服務一個使用者；bench 與正式不同時跑
DEFAULT_VAD_FLUSH_S = 3.0  # config 沒給時與 macos-stt 相同
DEFAULT_MAX_SESSION_S = 300.0
MODEL_FILES = ("encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx")


def _log(message: str) -> None:
    print(message, flush=True)


class ProtocolError(Exception):
    """config 或訊息不合協定：回 error（code=protocol）並以 1002 關閉"""


# ---------------------------------------------------------------- 辨識引擎（sherpa-onnx）

class SherpaEngine:
    """載入模型與熱詞，產生每條連線各自的 stream。只有這個類別 import sherpa_onnx（測試用假引擎不需要）"""

    def __init__(self, model_dir: Path, tokens: str, hints: Iterable[str], threads: int, hotwords_score: float):
        import sherpa_onnx

        model_dir = Path(model_dir)
        missing = [f for f in (*MODEL_FILES, tokens) if not (model_dir / f).is_file()]
        if missing:
            raise FileNotFoundError(f"模型目錄 {model_dir} 缺少：{'、'.join(missing)}")
        self.model_name = model_dir.name
        lines, skipped = encode_hotwords(hints, load_tokens(model_dir / tokens))
        self.hotwords, self.hotwords_skipped = len(lines), len(skipped)
        self._hotwords_file: Optional[str] = None
        if lines:
            fd, self._hotwords_file = tempfile.mkstemp(prefix="xasr_hotwords_", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(model_dir / tokens), encoder=str(model_dir / "encoder.int8.onnx"),
            decoder=str(model_dir / "decoder.onnx"), joiner=str(model_dir / "joiner.int8.onnx"),
            num_threads=threads, sample_rate=SAMPLE_RATE, decoding_method="modified_beam_search", max_active_paths=4,
            hotwords_file=self._hotwords_file or "", hotwords_score=hotwords_score, modeling_unit="cjkchar")

    def new_stream(self) -> "SherpaStream":
        return SherpaStream(self._recognizer)

    def close(self) -> None:
        if self._hotwords_file:
            try:
                os.unlink(self._hotwords_file)
            except OSError:
                pass
            self._hotwords_file = None


class SherpaStream:
    def __init__(self, recognizer):
        self._rec = recognizer
        self._stream = recognizer.create_stream()

    def _decode(self) -> str:
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        return str(self._rec.get_result(self._stream))

    def feed(self, samples: np.ndarray) -> str:
        self._stream.accept_waveform(SAMPLE_RATE, samples)
        return self._decode()

    def finish(self, pad_s: float) -> str:
        """補尾端靜音讓模型吐出最後的字（它每 480 ms 才出一段），再結束輸入、解碼到底"""
        if pad_s > 0:
            self._stream.accept_waveform(SAMPLE_RATE, np.zeros(int(pad_s * SAMPLE_RATE), dtype=np.float32))
        self._stream.input_finished()
        return self._decode()


# ---------------------------------------------------------------- 伺服器

class _State:
    """一條連線目前這一句的狀態（單位是取樣數，不用浮點秒，比較才不會有誤差）"""

    def __init__(self, stream):
        self.carry = b""  # 上一則音訊多出來的 1 byte（奇數長度）；跟著連線，不跟著 stream 重設
        self.reset(stream)

    def reset(self, stream) -> None:
        self.stream = stream
        self.text = ""
        self.samples = 0  # 這條 stream 收了多少取樣
        self.changed_at = 0  # 文字最後一次變動時的 samples


class XasrServer:
    def __init__(self, engine, tail_pad_s: float = config.XASR_TAIL_PAD_S, max_sessions: int = MAX_SESSIONS,
                 known_hints: Iterable[str] = (), config_timeout_s: float = CONFIG_TIMEOUT_S,
                 log: Callable[[str], None] = _log):
        """engine 需提供 new_stream()（回傳有 feed(samples)->文字、finish(pad_s)->文字 的物件）、model_name、hotwords、hotwords_skipped"""
        self.engine = engine
        self.tail_pad_s = tail_pad_s
        self.max_sessions = max_sessions
        self.known_hints = {h.strip().lower() for h in known_hints}
        self.config_timeout_s = config_timeout_s
        self._log = log
        self._active = 0
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xasr")  # 推論序列化；event loop 不會被擋住

    # ---- 啟動與 /health ----
    async def start(self, host: str, port: int):
        return await serve(self.handle, host, port, process_request=self._process_request, compression=None,
                           max_size=2 * MAX_FRAME_BYTES, max_queue=64)

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
        close = getattr(self.engine, "close", None)
        if close:
            close()

    def health(self) -> dict:
        return {"status": "ok", "engine": "xasr", "model": self.engine.model_name, "sessions_active": self._active,
                "max_sessions": self.max_sessions, "default_lang": config.STT_LANG,
                "hotwords": self.engine.hotwords, "hotwords_skipped": self.engine.hotwords_skipped}

    def _process_request(self, connection, request):
        path = request.path.split("?", 1)[0]
        if path == "/health":
            response = connection.respond(HTTPStatus.OK, json.dumps(self.health()) + "\n")
            response.headers["Content-Type"] = "application/json"
            return response
        if path != "/stream":
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        return None

    # ---- 一條連線 ----
    async def handle(self, ws) -> None:
        counted = False
        try:
            cfg = await self._read_config(ws)
            if self._active >= self.max_sessions:
                await self._send(ws, {"type": "error", "code": "too_many_sessions",
                                      "message": f"session limit reached (max={self.max_sessions})"})
                await ws.close(1013)
                return
            self._active += 1
            counted = True
            extra = [h for h in cfg["hints"] if h.strip().lower() not in self.known_hints]
            if extra:  # 熱詞在啟動時就固定了，不為單一連線重建模型
                self._log(f"[xasr] 忽略 {len(extra)} 個啟動時沒有的提示詞（熱詞只在啟動時載入）：{'、'.join(extra[:5])}")
            state = _State(await self._run(self.engine.new_stream))
            async for message in ws:
                if isinstance(message, bytes):
                    await self._on_audio(ws, state, cfg, message)
                    continue
                try:
                    kind = json.loads(message).get("type")
                except (ValueError, AttributeError):
                    continue  # JSON 壞掉或不是物件：略過（與 macos-stt 相同）
                if kind == "close":
                    return
                if kind == "flush":
                    await self._on_flush(ws, state)
        except ProtocolError as e:
            await self._send(ws, {"type": "error", "code": "protocol", "message": str(e)})
            await self._close(ws, 1002)
        except ConnectionClosed:
            pass
        except Exception as e:  # 引擎或其他未預期的錯誤：這條連線結束，伺服器繼續服務下一條
            self._log(f"[xasr] 連線錯誤：{traceback.format_exc()}")
            await self._send(ws, {"type": "error", "code": "internal", "message": str(e)})
            await self._close(ws, 1011)
        finally:
            if counted:
                self._active -= 1

    async def _read_config(self, ws) -> dict:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self.config_timeout_s)
        except asyncio.TimeoutError:
            raise ProtocolError("timed out waiting for config") from None
        if isinstance(raw, bytes):
            raise ProtocolError("config must be a text JSON frame")
        try:
            cfg = json.loads(raw)
        except ValueError:
            raise ProtocolError("config is not valid JSON") from None
        if not isinstance(cfg, dict) or cfg.get("type") != "config":
            raise ProtocolError('first message must be {"type":"config",...}')
        lang = cfg.get("lang") or config.STT_LANG
        if lang not in LANGS:
            raise ProtocolError(f"unsupported language '{lang}'")
        if cfg.get("sample_rate", SAMPLE_RATE) != SAMPLE_RATE:
            raise ProtocolError(f"only sample_rate {SAMPLE_RATE} supported in v1")
        if cfg.get("multi_utterance"):
            raise ProtocolError("multi_utterance=true is not supported by the xasr engine")
        try:
            vad_flush_s = float(cfg.get("vad_flush_seconds", DEFAULT_VAD_FLUSH_S))
            max_session_s = float(cfg.get("max_session_seconds", DEFAULT_MAX_SESSION_S))
            hints = [str(h) for h in (cfg.get("hints") or [])]
        except (TypeError, ValueError):
            raise ProtocolError("invalid config field") from None
        return {"vad_flush_samples": int(vad_flush_s * SAMPLE_RATE), "max_session_samples": int(max_session_s * SAMPLE_RATE),
                "hints": hints}

    async def _on_audio(self, ws, state: _State, cfg: dict, data: bytes) -> None:
        if len(data) > MAX_FRAME_BYTES:
            raise ProtocolError("audio frame too large (max 256KB)")
        data = state.carry + data
        state.carry = data[len(data) // 2 * 2:]  # 奇數 byte 留到下一則，不丟音訊
        samples = np.frombuffer(data[:len(data) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        if not len(samples):
            return
        text = (await self._run(state.stream.feed, samples)).strip()
        state.samples += len(samples)
        if text and text != state.text:
            await self._send(ws, {"type": "partial", "text": text})
            state.changed_at = state.samples
        state.text = text
        # 安全網（macos-stt session._process_block 同一個順序）：文字停了這麼久（音訊時間）就收；超過 session 上限也收
        if state.text and state.samples - state.changed_at >= cfg["vad_flush_samples"]:
            await self._finalize(ws, state, "server_vad")
        elif state.samples >= cfg["max_session_samples"]:
            await self._finalize(ws, state, "max_session")

    async def _on_flush(self, ws, state: _State) -> None:
        # macos-stt session.flush：這一句還沒有任何文字就什麼都不做
        if state.text:
            await self._finalize(ws, state, "client_flush")

    async def _finalize(self, ws, state: _State, reason: str) -> None:
        """補尾端靜音、解碼到底、送 final（有文字才送）、換新的 stream 並通知 session_restarted"""
        started = time.monotonic()
        text = (await self._run(state.stream.finish, self.tail_pad_s)).strip()
        finish_ms = round((time.monotonic() - started) * 1000, 1)
        pre_delay_ms = round(self.tail_pad_s * 1000, 1)  # 補進去的尾端靜音；解碼它只要幾 ms，真正的收尾時間看 finish_ms
        # 這一行的格式與 macos-stt 相同：scripts/stt_bench.py 靠 "[flush] reason=" 數 server 收到幾次 client_flush
        self._log(f"[flush] reason={reason} pre_delay_ms={pre_delay_ms} finish_ms={finish_ms} text_len={len(text)}")
        state.reset(await self._run(self.engine.new_stream))
        if text:
            await self._send(ws, {"type": "final", "text": text,
                                  "timing": {"reason": reason, "pre_delay_ms": pre_delay_ms, "finish_ms": finish_ms}})
        await self._send(ws, {"type": "session_restarted"})

    # ---- 小工具 ----
    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    @staticmethod
    async def _send(ws, event: dict) -> None:
        try:
            await ws.send(json.dumps(event, ensure_ascii=False))
        except ConnectionClosed:
            pass

    @staticmethod
    async def _close(ws, code: int) -> None:
        try:
            await ws.close(code)
        except ConnectionClosed:
            pass


# ---------------------------------------------------------------- 命令列

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="X-ASR 辨識伺服器（macos-stt 的 WS 協定 v1，sherpa-onnx，只綁 127.0.0.1）")
    p.add_argument("--port", type=int, default=config.STT_PORT)
    p.add_argument("--model-dir", type=Path, default=config.XASR_MODEL_DIR)
    p.add_argument("--tokens", default=config.XASR_TOKENS, help="模型目錄裡的 token 表檔名")
    p.add_argument("--hints", type=Path, default=config.STT_HINTS_PATH, help="熱詞（每行一個詞，啟動時切成 token 載入）")
    p.add_argument("--hotwords-score", type=float, default=config.XASR_HOTWORDS_SCORE)
    p.add_argument("--threads", type=int, default=config.XASR_THREADS)
    p.add_argument("--tail-pad", type=float, default=config.XASR_TAIL_PAD_S, help="flush 時補在句尾的靜音秒數")
    p.add_argument("--max-sessions", type=int, default=MAX_SESSIONS)
    return p.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    started = time.monotonic()
    hints = args.hints.read_text(encoding="utf-8").splitlines() if args.hints.is_file() else []
    engine = SherpaEngine(args.model_dir, args.tokens, hints, args.threads, args.hotwords_score)
    server = XasrServer(engine, args.tail_pad, args.max_sessions, known_hints=hints)
    ws_server = await server.start("127.0.0.1", args.port)  # 模型載入完才開始聽，/health 能回應就代表可用
    _log(f"[xasr] ready port={args.port} model={engine.model_name} tokens={args.tokens} hotwords={engine.hotwords} "
         f"skipped={engine.hotwords_skipped} threads={args.threads} tail_pad={args.tail_pad} load_s={time.monotonic() - started:.2f}")
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass  # Windows 的 event loop 不支援；Ctrl+C 會變成 KeyboardInterrupt
    try:
        await stop.wait()
    finally:
        ws_server.close()
        await ws_server.wait_closed()
        server.close()


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # Windows 的預設編碼（cp950）印不出所有字，導到 log 檔時會當掉
    args = parse_args(argv)
    try:
        asyncio.run(run(args))
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
