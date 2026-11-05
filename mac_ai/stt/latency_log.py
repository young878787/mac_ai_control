"""每一句話的計時紀錄（JSON Lines）：一行一筆，執行緒安全。"""
import json
import threading
from pathlib import Path

from mac_ai import config


class JsonlLog:
    def __init__(self, path: Path = config.LATENCY_LOG):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
