"""測試用：依 macos-stt 的 WS /stream 協定（首則 config、之後 binary PCM、text close）模擬的本機伺服器與假麥克風。"""
import json
import threading
import time

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve


def wait_until(cond, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return False


class FakeSttServer:
    """events：每條連線收到 config 後立刻送出的事件；drop_first：第一條連線收到 config 就掛斷（測重連）"""

    def __init__(self, events=(), drop_first=False):
        self.events = list(events)
        self.drop_first = drop_first
        self.connections = 0
        self.configs = []
        self.frames = []
        self.closed_by_client = False
        self._server = serve(self._handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self._server.socket.getsockname()[1]}/stream"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def _handler(self, ws):
        self.connections += 1
        self.configs.append(json.loads(ws.recv(timeout=5)))
        if self.drop_first and self.connections == 1:
            return
        for event in self.events:
            ws.send(json.dumps(event, ensure_ascii=False))
        try:
            for message in ws:
                if isinstance(message, bytes):
                    self.frames.append(message)
                elif json.loads(message).get("type") == "close":
                    self.closed_by_client = True
        except ConnectionClosed:
            pass

    def stop(self):
        self._server.shutdown()
        self._server.socket.close()


class FakeAudio:
    """依序吐出給定的幀（每幀間隔 pace 秒），吐完之後 read 只會逾時；記錄 drain 次數"""

    def __init__(self, chunks=(), pace=0.0):
        self._chunks = list(chunks)
        self._pace = pace
        self.drained = 0

    def read(self, timeout):
        if self._chunks:
            time.sleep(self._pace)
            return self._chunks.pop(0)
        time.sleep(min(timeout, 0.02))
        return None

    def drain(self):
        self.drained += 1
