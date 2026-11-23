import atexit
import random
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import mac_ai.router as r
from mac_ai import config, db
from mac_ai.router import cloud_client, context, lexicon, memory, native, normalize
from support.clock import Clock
from support.cloud import KEY


@pytest.fixture(autouse=True)
def _isolate_router(request, monkeypatch, tmp_path):
    """每個測試前重設 LLM 熔斷，並把正規化／延遲／指令紀錄導到暫存目錄（不污染專案的 logs/）。
    離線測試預設 LLM 不可用（不會誤打到本機正在跑的 server）；需要 LLM 的測試自己 monkeypatch llm_client._chat 或 urlopen。
    live 與 cloud 測試要真的連 server，所以不套用這個預設。"""
    r.reset_llm_breaker()
    real_cloud = request.node.get_closest_marker("cloud") is not None  # cloud 實測要讀真的 .env、連真的雲端
    if not real_cloud:
        for name in ("AI_API_URL", "AI_MODEL", "AI_API_KEY", "CWA_API_KEY", "BRAVE_API_KEY"):  # 雲端、氣象署與搜尋預設停用：不讀這台電腦的 .env 與環境變數（cloud fixture 與天氣測試的 with_key 才開）
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(config, "ENV_PATH", tmp_path / "no.env")
    config.reset_env_cache()
    cloud_client.reset()
    memory.STORE.clear()
    monkeypatch.setattr(normalize, "NORMALIZE_LOG", tmp_path / "logs" / "normalize.jsonl")
    monkeypatch.setattr(config, "LATENCY_LOG", tmp_path / "logs" / "latency.jsonl")
    monkeypatch.setattr(config, "COMMAND_LOG", tmp_path / "logs" / "commands.jsonl")
    if request.node.get_closest_marker("live") is None and not real_cloud:
        def offline(*a, **kw):
            raise urllib.error.URLError("offline test: LLM not mocked")
        monkeypatch.setattr(urllib.request, "urlopen", offline)
    yield
    r.reset_llm_breaker()
    cloud_client.reset()
    config.reset_env_cache()


@pytest.fixture
def clock():
    """可注入的假時鐘（support.clock.Clock）：clock.now += 秒數 推進，時間相關邏輯不要 sleep"""
    return Clock()


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    """假雲端：寫一個暫存 .env（金鑰 sk-TEST-7f3a9c）並把 cloud_client._post 換成假的。
    cloud.reply＝下一次回應的 message（dict），可設成函式 (body)→message 或 Exception；cloud.requests 記錄每次請求的 body"""
    env = tmp_path / "cloud.env"
    env.write_text(f"AI_API_URL=https://cloud.test\nAI_MODEL=TEST-LLM\nAI_API_KEY={KEY}\n", encoding="utf-8")
    env.chmod(0o600)
    monkeypatch.setattr(config, "ENV_PATH", env)
    config.reset_env_cache()
    cloud_client.reset()

    class Fake:
        key = KEY
        reply = {"content": "好的～"}
        requests: list = []

        def __call__(self, url, key, body, timeout):
            self.requests.append(body)
            reply = self.reply(body) if callable(self.reply) else self.reply
            if isinstance(reply, Exception):
                raise reply
            return {"choices": [{"message": reply}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}

    fake = Fake()
    fake.requests = []
    monkeypatch.setattr(cloud_client, "_post", fake)
    return fake


@pytest.fixture(autouse=True)
def _isolate_catalog(monkeypatch):
    """離線測試不能依賴這台電腦裝了哪些 App，也不能真的跑 mdfind：已安裝 App 清單固定為空（只剩詞庫），
    選擇清單每個測試各自獨立。需要已安裝 App 的測試用 installed_apps fixture 加。"""
    from mac_ai.router import catalog, choices, sources
    monkeypatch.setattr(catalog, "_scan_installed", lambda: {})
    monkeypatch.setattr(catalog, "DEFAULT", catalog.Catalog())
    monkeypatch.setattr(choices, "STORE", choices.ChoiceStore())
    monkeypatch.setattr(sources, "STORE", sources.SourceStore())
    yield


@pytest.fixture(autouse=True)
def _isolate_front(request, monkeypatch):
    """離線測試不能依賴這台電腦現在最前面是哪個 App：前景固定為『讀不到』。需要前景的測試用 front fixture 設定。
    真實 App 測試（-m app）要讀真的前景，不套用。"""
    if request.node.get_closest_marker("app") is not None:
        yield
        return
    monkeypatch.setattr(context, "read_front", lambda run: None)
    monkeypatch.setattr(context, "read_host", lambda run, app: "")
    yield


_MUSIC_TEMPLATE: dict = {}
_MUSIC_TEMPLATE_LOCK = threading.Lock()


def _music_template() -> Path:
    """用真的 ensure_schema（alembic）建一份空的 head 資料庫，整個測試程序只建一次，之後每個測試複製它（不用每次載入 alembic）"""
    with _MUSIC_TEMPLATE_LOCK:
        if "path" not in _MUSIC_TEMPLATE:
            folder = Path(tempfile.mkdtemp(prefix="music_template_"))
            atexit.register(shutil.rmtree, folder, ignore_errors=True)
            path = folder / "music.sqlite3"
            assert db.ensure_schema(path).ok
            _MUSIC_TEMPLATE["path"] = path
        return _MUSIC_TEMPLATE["path"]


def _copy_template_ensure(path: Path) -> db.EnsureResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_music_template(), path)
    return db.EnsureResult(True)


@pytest.fixture(autouse=True)
def _isolate_music_db(monkeypatch, tmp_path):
    """點歌偏好資料庫一律指到暫存目錄，絕對不寫專案的 data/。預設『還沒啟動』（conn() 是 None：照常播放、不寫紀錄）；
    需要資料庫的測試用 music_db fixture 啟動它。亂數固定種子；時間（local_actions._now）由需要的測試自己 monkeypatch"""
    from mac_ai.router import local_actions
    path = tmp_path / "data" / "music.sqlite3"
    monkeypatch.setattr(local_actions, "MUSIC_DB", db.MusicDb(path, ensure=_copy_template_ensure))
    monkeypatch.setattr(local_actions, "_rng", random.Random(0))
    yield
    local_actions.MUSIC_DB.close()


@pytest.fixture
def music_db():
    """啟動並等準備好的點歌資料庫（暫存目錄、schema 是複製來的空 head 庫）。回傳 local_actions.MUSIC_DB；conn() 給呼叫端執行緒自己的連線"""
    from mac_ai.router import local_actions
    local_actions.MUSIC_DB.start()
    assert local_actions.MUSIC_DB.wait_ready(30) and local_actions.MUSIC_DB.ready
    return local_actions.MUSIC_DB


FAKE_PID = 4242


@pytest.fixture(autouse=True)
def native_calls(request, monkeypatch):
    """離線測試不得真的對 App 下指令：native 的底層操作一律攔下，記成字串（給測試檢查），預設回傳成功。
    格式：window close／menu [ command／menu 檢視/終端／button <pattern>／button_in_window <pattern>／mediakey <key>／key 121。
    要模擬失敗時改 native_calls.result（例如 "missing"）。真實 App 測試（-m app）不套用"""
    class Calls(list):
        result = "ok"
    calls = Calls()
    if request.node.get_closest_marker("app") is not None:
        yield calls
        return

    def rec(text, ret=None):
        calls.append(text)
        return calls.result if ret is None else ret

    monkeypatch.setattr(native, "window_op", lambda pid, op: rec(f"window {op}"))
    monkeypatch.setattr(native, "press_menu", lambda pid, chars="", mods=(), path=None: (
        rec(f"menu {'/'.join(p[0] for p in path)}" if path else f"menu {chars} {'+'.join(mods)}"), ""))
    monkeypatch.setattr(native, "press_button", lambda pid, pattern: rec(f"button {pattern}"))
    monkeypatch.setattr(native, "press_button_in_window", lambda pid, pattern: rec(f"button_in_window {pattern}"))
    monkeypatch.setattr(native, "post_media_key", lambda key: rec(f"mediakey {key}"))
    monkeypatch.setattr(native, "post_key", lambda code, mods=(): rec(f"key {code}" + (f" {'+'.join(mods)}" if mods else "")))
    yield calls


@pytest.fixture
def front(monkeypatch):
    """設定前景 App：front("Safari")、front("Safari", host="www.facebook.com")；front(None)＝讀不到。
    回傳的 reads 記錄讀取（App 名稱；讀網址記 ("host", App)）"""
    reads = []

    def setup(app, host=""):
        fc = None if app is None else context.FrontContext(app, lexicon.APP_KIND.get(app, "other"), FAKE_PID)

        def fake(run):
            reads.append(app)
            return fc

        def fake_host(run, name):
            reads.append(("host", name))
            return host
        monkeypatch.setattr(context, "read_front", fake)
        monkeypatch.setattr(context, "read_host", fake_host)
        return reads
    return setup


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


class ShCalls(list):
    """sh 記錄的指令清單；sh.fail(cmd, ..., stderr=) 讓指定指令回傳結束碼 1"""
    failing = ()
    stderr = "boom"

    def fail(self, *cmds, stderr="boom"):
        self.failing, self.stderr = cmds, stderr


@pytest.fixture
def sh(monkeypatch):
    """記錄所有 subprocess.run 呼叫，不真的執行（預設一律回傳結束碼 0；用 sh.fail(指令) 讓某些指令失敗）"""
    calls = ShCalls()

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd in calls.failing:
            return subprocess.CompletedProcess(cmd, 1, "", calls.stderr)
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


LAYERS = ("unit", "flow", "runtime")  # tests/ 下的分層目錄；real/ 另有 live／cloud／app marker


def pytest_collection_modifyitems(items):
    """依所在目錄自動加上層級 marker：pytest -m unit（快速回饋）、-m "unit or flow"…"""
    for item in items:
        layer = item.path.parent.name
        if layer in LAYERS:
            item.add_marker(getattr(pytest.mark, layer))
