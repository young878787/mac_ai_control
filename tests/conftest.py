import subprocess
import urllib.error
import urllib.request

import pytest

import mac_ai.router as r
from mac_ai import config
from mac_ai.router import lexicon, normalize

REAL_URLOPEN = urllib.request.urlopen  # 需要真的連線的測試（本機 socket）用這個


@pytest.fixture(autouse=True)
def _isolate_router(request, monkeypatch, tmp_path):
    """每個測試前重設 LLM 熔斷，並把正規化／延遲／指令紀錄導到暫存目錄（不污染專案的 logs/）。
    離線測試預設 LLM 不可用（不會誤打到本機正在跑的 server）；需要 LLM 的測試自己 monkeypatch llm_client._chat 或 urlopen。
    live 測試要真的連 server，所以不套用這個預設。"""
    r.reset_llm_breaker()
    monkeypatch.setattr(normalize, "NORMALIZE_LOG", tmp_path / "logs" / "normalize.jsonl")
    monkeypatch.setattr(config, "LATENCY_LOG", tmp_path / "logs" / "latency.jsonl")
    monkeypatch.setattr(config, "COMMAND_LOG", tmp_path / "logs" / "commands.jsonl")
    if request.node.get_closest_marker("live") is None:
        def offline(*a, **kw):
            raise urllib.error.URLError("offline test: LLM not mocked")
        monkeypatch.setattr(urllib.request, "urlopen", offline)
    yield
    r.reset_llm_breaker()


@pytest.fixture(autouse=True)
def _isolate_catalog(monkeypatch):
    """離線測試不能依賴這台電腦裝了哪些 App，也不能真的跑 mdfind：已安裝 App 清單固定為空（只剩詞庫），
    選擇清單每個測試各自獨立。需要已安裝 App 的測試用 installed_apps fixture 加。"""
    from mac_ai.router import catalog, choices
    monkeypatch.setattr(catalog, "_scan_installed", lambda: {})
    monkeypatch.setattr(catalog, "DEFAULT", catalog.Catalog())
    monkeypatch.setattr(choices, "STORE", choices.ChoiceStore())
    yield


@pytest.fixture
def installed_apps(monkeypatch):
    """設定『已安裝』的 App：{顯示名稱或別名: 標準名稱}（key 會轉成 compact）"""
    from mac_ai.router import catalog, lexicon

    def setup(mapping):
        index = {lexicon._compact(k): v for k, v in mapping.items()}
        monkeypatch.setattr(catalog, "_scan_installed", lambda: dict(index))
        monkeypatch.setattr(catalog, "DEFAULT", catalog.Catalog())
    return setup


@pytest.fixture
def find_env(monkeypatch, tmp_path):
    """檔案搜尋用的假環境：HOME 換成暫存資料夾，mdfind 回傳 env.mdfind_paths（或拋 env.mdfind_error），
    open 等其他指令只記錄。env.touch 在假家目錄建檔；env.opens() 取出所有 open 呼叫"""
    import os
    import types
    home = tmp_path.resolve() / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    env = types.SimpleNamespace(home=home, calls=[], mdfind_paths=[], mdfind_error=None)

    def fake_run(cmd, **kw):
        env.calls.append(cmd)
        if cmd[0] == "mdfind":
            if env.mdfind_error is not None:
                raise env.mdfind_error
            return subprocess.CompletedProcess(cmd, 0, "\n".join(str(p) for p in env.mdfind_paths), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def touch(rel, mtime=None, mode=None):
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        if mode is not None:
            path.chmod(mode)
        return path

    monkeypatch.setattr(subprocess, "run", fake_run)
    env.touch = touch
    env.opens = lambda: [c for c in env.calls if c[0] == "open"]
    env.mdfinds = lambda: [c for c in env.calls if c[0] == "mdfind"]
    return env


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
