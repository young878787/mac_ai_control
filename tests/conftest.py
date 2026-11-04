import subprocess
import urllib.error
import urllib.request

import pytest

import mac_ai.router as r
from mac_ai.router import lexicon, normalize

REAL_URLOPEN = urllib.request.urlopen  # 需要真的連線的測試（本機 socket）用這個


@pytest.fixture(autouse=True)
def _isolate_router(request, monkeypatch, tmp_path):
    """每個測試前重設 LLM 熔斷，並把正規化紀錄導到暫存目錄（不污染專案的 logs/）。
    離線測試預設 LLM 不可用（不會誤打到本機正在跑的 server）；需要 LLM 的測試自己 monkeypatch llm_client._chat 或 urlopen。
    live 測試要真的連 server，所以不套用這個預設。"""
    r.reset_llm_breaker()
    monkeypatch.setattr(normalize, "NORMALIZE_LOG", tmp_path / "logs" / "normalize.jsonl")
    if request.node.get_closest_marker("live") is None:
        def offline(*a, **kw):
            raise urllib.error.URLError("offline test: LLM not mocked")
        monkeypatch.setattr(urllib.request, "urlopen", offline)
    yield
    r.reset_llm_breaker()


@pytest.fixture
def sh(monkeypatch):
    """記錄所有 subprocess.run 呼叫，不真的執行（一律回傳結束碼 0）"""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.fixture
def projects(monkeypatch, tmp_path):
    """以暫存資料夾取代真實專案清單：mac_ai_control（含別名『語音助手』）、other_proj（靠掃描）"""
    for name in ("mac_ai_control", "other_proj"):
        (tmp_path / name).mkdir()
    (tmp_path / ".hidden").mkdir()
    monkeypatch.setattr(lexicon, "PROJECT_ROOTS", [str(tmp_path)])
    monkeypatch.setitem(r.LEXICON, "projects", [
        {"name": "mac_ai_control", "aliases": ["語音助手"], "path": str(tmp_path / "mac_ai_control")}])
    return tmp_path
