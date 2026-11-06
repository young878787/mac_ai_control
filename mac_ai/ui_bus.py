"""桌面角色介面的事件匯流排：本機 WebSocket 伺服器。後端用 publish() 廣播事件，UI 送回來的訊息交給 on_message。

連線需帶啟動時產生的 token，且不能帶 Origin 標頭（瀏覽器網頁一定會帶；Electron 主程序的連線不會）。
原因：任何網頁都能對 ws://127.0.0.1 發連線，不擋的話網頁可以替使用者下指令。
publish() 不會阻塞：每個連線有上限的佇列，滿了丟最舊的；沒有連線時幾乎不花時間。"""
import asyncio
import collections
import hmac
import json
import threading
import time
from http import HTTPStatus
from typing import Callable, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from mac_ai import config

PROTOCOL_VERSION = 1
UI_MESSAGE_TYPES = ("command", "wake", "pause", "quit")
_MAX_INCOMING_BYTES = 64 * 1024


def parse_ui_message(raw) -> Tuple[Optional[dict], Optional[str]]:
    """驗證 UI 送來的一則訊息。回傳 (訊息, None) 或 (None, 拒絕原因)"""
    if not isinstance(raw, str):
        return None, "不是文字訊息"
    try:
        msg = json.loads(raw)
    except ValueError:
        return None, "不是 JSON"
    if not isinstance(msg, dict):
        return None, "不是 JSON 物件"
    kind = msg.get("type")
    if kind not in UI_MESSAGE_TYPES:
        return None, f"未知的 type：{kind!r}"
    if kind == "command":
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            return None, "command 沒有文字"
        text = text.strip()
        if len(text) > config.UI_COMMAND_MAX_CHARS:
            return None, f"command 超過 {config.UI_COMMAND_MAX_CHARS} 字"
        return {"type": "command", "text": text}, None
    if kind == "pause":
        if not isinstance(msg.get("value"), bool):
            return None, "pause 需要布林值 value"
        return {"type": "pause", "value": msg["value"]}, None
    return {"type": kind}, None


class _Client:
    def __init__(self, maxlen: int):
        self.queue: collections.deque = collections.deque(maxlen=maxlen)
        self.wakeup = asyncio.Event()
        self.signaled = False  # 已排程喚醒送出協程；避免每則事件都跨執行緒排程一次


class UiBus:
    def __init__(self, port: int = config.UI_BUS_PORT, token: str = "", host: str = config.UI_BUS_HOST,
                 on_message: Optional[Callable[[dict], None]] = None, hello: Optional[Callable[[], dict]] = None,
                 warn: Callable[[str], None] = print, queue_max: int = config.UI_QUEUE_MAX):
        if not token:
            raise ValueError("UiBus 需要 token")
        self.host, self.port, self.token = host, port, token
        self.on_message = on_message
        self.hello = hello
        self._warn = warn
        self._queue_max = queue_max
        self._lock = threading.Lock()
        self._seq = 0
        self._clients: Tuple[_Client, ...] = ()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None

    def bind(self, on_message: Callable[[dict], None], hello: Callable[[], dict]) -> None:
        self.on_message, self.hello = on_message, hello

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}/?token={self.token}"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/health"

    def client_count(self) -> int:
        return len(self._clients)

    # ---------- 生命週期 ----------
    def start(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, name="ui-bus", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise OSError("UI 事件匯流排啟動逾時")
        if self._error is not None:
            raise OSError(f"UI 事件匯流排無法啟動：{self._error}") from self._error

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:  # 迴圈已關閉
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as e:  # 啟動失敗（例如埠被占用）要回報給 start()
            self._error = e
            self._ready.set()
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        self._stop = asyncio.Event()
        async with serve(self._handler, self.host, self.port, origins=[None], process_request=self._process_request,
                         max_size=_MAX_INCOMING_BYTES) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop.wait()

    # ---------- 連線 ----------
    def _process_request(self, connection, request):
        parts = urlsplit(request.path)
        if parts.path == "/health":  # 給 services.Service 判斷 UI 是否已連上；不含任何指令或內容
            return connection.respond(HTTPStatus.OK, json.dumps({"status": "ok", "clients": self.client_count()}) + "\n")
        supplied = (parse_qs(parts.query).get("token") or [""])[0]
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        return None

    async def _handler(self, ws) -> None:
        client = _Client(self._queue_max)
        hello = {"type": "hello", "id": 0, "ts": time.time(), "version": PROTOCOL_VERSION}
        try:
            hello.update(self.hello() if self.hello else {})
        except Exception as e:
            self._warn(f"⚠️ UI hello 狀態取得失敗：{e}")
        client.queue.append(json.dumps(hello, ensure_ascii=False))  # 第一則一定是 hello，之後才是 publish 的事件
        with self._lock:
            self._clients = (*self._clients, client)
        client.wakeup.set()
        sender = asyncio.create_task(self._send_loop(ws, client))
        try:
            async for raw in ws:
                msg, error = parse_ui_message(raw)
                if error:
                    self._warn(f"⚠️ 忽略 UI 訊息：{error}")
                    continue
                try:
                    if self.on_message:
                        self.on_message(msg)
                except Exception as e:
                    self._warn(f"⚠️ UI 訊息處理失敗：{e}")
        except ConnectionClosed:
            pass
        finally:
            with self._lock:
                self._clients = tuple(c for c in self._clients if c is not client)
            sender.cancel()

    @staticmethod
    async def _send_loop(ws, client: _Client) -> None:
        try:
            while True:
                await client.wakeup.wait()
                client.wakeup.clear()
                client.signaled = False
                while client.queue:
                    await ws.send(client.queue.popleft())
        except (ConnectionClosed, asyncio.CancelledError):
            pass

    # ---------- 廣播 ----------
    def publish(self, event: dict) -> None:
        """廣播一則事件（加上遞增的 id 與 ts）。可在任何執行緒呼叫，不會阻塞、不會丟例外"""
        try:
            with self._lock:
                self._seq += 1
                seq = self._seq
                clients = self._clients
            if not clients:
                return
            data = json.dumps({**event, "id": seq, "ts": time.time()}, ensure_ascii=False)
            for client in clients:
                client.queue.append(data)
                if not client.signaled:
                    client.signaled = True
                    self._loop.call_soon_threadsafe(client.wakeup.set)
        except Exception as e:  # UI 的問題不該影響語音主流程
            self._warn(f"⚠️ UI 事件發送失敗：{e}")
