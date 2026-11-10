"""執行期紀錄（JSON Lines）：一行一筆，執行緒安全。每筆自動補 ts_iso（本地時間）與 run_id（哪一次啟動寫的）。"""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from mac_ai import config

RUN_ID = datetime.now().strftime("%Y%m%d-%H%M%S")  # 這次程式啟動的編號：同一次啟動寫出的各種紀錄共用，可串起來看


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _round(value, key: str = ""):
    """浮點數四捨五入，紀錄才讀得懂：*_ms 取小數 1 位，其餘（秒、時間戳）取 3 位"""
    if isinstance(value, float):
        return round(value, 1 if key.endswith("_ms") else 3)
    if isinstance(value, dict):
        return {k: _round(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v, key) for v in value]
    return value


class JsonlLog:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path if path is not None else config.LATENCY_LOG)  # 建立時才讀 config（測試可改路徑）
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        row = _round({"ts_iso": iso_now(), "run_id": RUN_ID, **record})
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
