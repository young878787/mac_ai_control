import pytest

from mac_ai import assistant
from mac_ai.stt.client import SttClient


@pytest.fixture(autouse=True)
def _fast_stop(monkeypatch):
    """STT 客戶端與指令執行緒每 0.02 s 檢查一次 stop（產品預設 0.5 s／0.2 s），測試收尾不空等"""
    monkeypatch.setattr(SttClient, "RECV_POLL_S", 0.02)
    monkeypatch.setattr(assistant, "WORKER_POLL_S", 0.02)
