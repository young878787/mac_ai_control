"""點歌偏好資料庫（計畫 §4.4）。執行期只用內建 sqlite3；alembic／sqlalchemy 只在 ensure_schema 函式內 import（D6）。

用法：啟動時 `MusicDb(...).start()` 在背景準備 schema（不阻塞）；`command_worker` 呼叫 `db.conn()`：
準備好之前回 None（照常播放、不寫紀錄），之後回這個執行緒專屬的連線。
"""
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Union

from mac_ai import config

HEAD_VERSION = 1  # 目前 migration 的 PRAGMA user_version；新增 revision 時一起改
DEFAULT_SCRIPT_LOCATION = Path(__file__).parent / "migrations"
CONNECT_TIMEOUT_S = 5.0


@dataclass
class EnsureResult:
    ok: bool                       # 資料庫可用（升級失敗時 False：呼叫端照常播放但不寫紀錄）
    message: str = ""              # ⚠️ 開頭的提示；沒有事要說時是空字串
    backup: Optional[Path] = None  # 升級前的備份檔（有備份才有值）
    corrupt: Optional[Path] = None  # 損毀檔改名後的位置（有發生才有值）


def connect(path: Union[str, Path] = config.MUSIC_DB_PATH) -> sqlite3.Connection:
    """執行期連線：內建 sqlite3、WAL。連線只能在建立它的執行緒使用（只有 command_worker 寫入）。"""
    conn = sqlite3.connect(str(path), timeout=CONNECT_TIMEOUT_S)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _sidecars(path: Path):
    return [path.with_name(path.name + s) for s in ("-wal", "-shm")]


def _inspect(path: Path) -> Dict[str, object]:
    """讀取既有檔案（不用 mode=ro：唯讀開 WAL 庫會留下 -wal／-shm）：user_version、alembic_version（沒有表時是 None）。不是資料庫會拋 sqlite3.DatabaseError"""
    conn = sqlite3.connect(str(path), timeout=CONNECT_TIMEOUT_S)
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rev = None
        if "alembic_version" in names:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            rev = row[0] if row else None
        return {"user_version": user_version, "revision": rev, "empty": not names}
    finally:
        conn.close()


def _backup_to(src: Path, dst: Path) -> None:
    s = sqlite3.connect(str(src), timeout=CONNECT_TIMEOUT_S)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _upgrade(target: Path, script_location: Path) -> str:
    """對 target 跑 alembic upgrade head；回傳 head revision。alembic／sqlalchemy 只在這裡 import（D6）"""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    cfg = Config()
    cfg.set_main_option("script_location", str(script_location))
    engine = create_engine(URL.create("sqlite", database=str(target)))
    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
    finally:
        engine.dispose()
    return ScriptDirectory.from_config(cfg).get_current_head()


def _head_revision(script_location: Path) -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(script_location))
    return ScriptDirectory.from_config(cfg).get_current_head()


def ensure_schema(path: Union[str, Path] = config.MUSIC_DB_PATH,
                  script_location: Optional[Path] = None) -> EnsureResult:
    """建立或升級資料庫到 head（流程見計畫 §4.4）。不丟例外，結果看 EnsureResult。
    - 不存在：建立資料夾與檔案
    - 版本落後：先用 sqlite3 backup API 備份成 `{檔名}.bak-{舊 user_version}`，升級在「複本」上做，成功才換回；
      失敗時原檔位元組不變（D4），回 ⚠️、ok=False
    - 檔案損毀：改名成 `{檔名}.corrupt-{時間}`，建立新庫，回 ⚠️（ok=True）
    script_location：migration 目錄（預設 mac_ai/db/migrations；測試用假的 revision 目錄）"""
    path = Path(path)
    loc = Path(script_location) if script_location else DEFAULT_SCRIPT_LOCATION
    try:
        return _ensure(path, loc)
    except Exception as e:  # 任何意外都不能讓啟動失敗
        return EnsureResult(False, f"⚠️ 偏好資料庫無法使用（{type(e).__name__}: {e}），點歌照常但不寫紀錄")


def _ensure(path: Path, loc: Path) -> EnsureResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    message, corrupt = "", None
    info = None
    if path.exists():
        try:
            info = _inspect(path)
        except sqlite3.DatabaseError:
            corrupt = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
            n = 1
            while corrupt.exists():
                n += 1
                corrupt = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}-{n}")
            os.replace(path, corrupt)
            for side in _sidecars(path):
                if side.exists():
                    os.replace(side, side.with_name(corrupt.name + side.name[len(path.name):]))
            message = f"⚠️ 偏好資料庫損毀，已改名保留為 {corrupt.name}，重新建立了新的資料庫"
            info = None

    if info is None or info["empty"]:  # 新庫（不存在、剛改名走、或 0 位元組）：直接建立，失敗就清掉半成品
        try:
            _upgrade(path, loc)
        except Exception as e:
            for p in [path] + _sidecars(path):
                p.unlink(missing_ok=True)
            return EnsureResult(False, f"⚠️ 偏好資料庫建立失敗（{type(e).__name__}: {e}），點歌照常但不寫紀錄",
                                corrupt=corrupt)
        return EnsureResult(True, message, corrupt=corrupt)

    behind = info["revision"] != _head_revision(loc)
    if not behind:
        return EnsureResult(True, message, corrupt=corrupt)

    backup = path.with_name(f"{path.name}.bak-{info['user_version']}")
    work = path.with_name(path.name + ".upgrading")
    try:
        _backup_to(path, backup)
        work.unlink(missing_ok=True)
        shutil.copyfile(backup, work)
        _upgrade(work, loc)
        for side in _sidecars(path):
            side.unlink(missing_ok=True)
        os.replace(work, path)
    except Exception as e:
        for p in [work] + _sidecars(work):
            p.unlink(missing_ok=True)
        return EnsureResult(False, f"⚠️ 偏好資料庫升級失敗（{type(e).__name__}: {e}），原檔未動、已備份於 {backup.name}，"
                            "點歌照常但不寫紀錄", backup=backup if backup.exists() else None)
    return EnsureResult(True, message, backup=backup)


class MusicDb:
    """背景模式（預設，D7）：`start()` 在背景執行緒跑 ensure，不阻塞啟動。
    `conn()` 準備好之前（或升級失敗）回 None，呼叫端據此照常播放但不寫紀錄；
    準備好之後在『呼叫端的執行緒』第一次呼叫時才建立連線（每個執行緒各自一條，不跨執行緒共用）。"""

    def __init__(self, path: Union[str, Path] = config.MUSIC_DB_PATH,
                 ensure: Callable[[Path], EnsureResult] = ensure_schema):
        self.path = Path(path)
        self._ensure = ensure
        self._done = threading.Event()
        self._started = False
        self._lock = threading.Lock()
        self._result: Optional[EnsureResult] = None
        self._local = threading.local()

    def start(self) -> None:
        """啟動背景準備；立刻返回。重複呼叫無作用"""
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._run, name="music-db-ensure", daemon=True).start()

    def _run(self) -> None:
        try:
            result = self._ensure(self.path)
        except Exception as e:
            result = EnsureResult(False, f"⚠️ 偏好資料庫無法使用（{type(e).__name__}: {e}），點歌照常但不寫紀錄")
        self._result = result
        self._done.set()

    def wait_ready(self, timeout: Optional[float] = None) -> bool:
        """等 ensure 結束（不論成功失敗）；逾時回 False。給測試與腳本用，command_worker 不要呼叫"""
        return self._done.wait(timeout)

    @property
    def ready(self) -> bool:
        """ensure 已結束且資料庫可用"""
        return self._done.is_set() and bool(self._result and self._result.ok)

    @property
    def result(self) -> Optional[EnsureResult]:
        return self._result  # 還沒結束時是 None

    @property
    def message(self) -> str:
        """ensure 的 ⚠️ 提示；沒有、或還沒結束是空字串"""
        return self._result.message if self._result else ""

    def conn(self) -> Optional[sqlite3.Connection]:
        """這個執行緒的連線；還沒準備好或升級失敗回 None（不阻塞、不拋例外）"""
        if not self.ready:
            return None
        c = getattr(self._local, "conn", None)
        if c is None:
            try:
                c = self._local.conn = connect(self.path)
            except sqlite3.Error:
                return None
        return c

    def close(self) -> None:
        """關閉『呼叫端執行緒』的連線（sqlite3 連線不能由別的執行緒關）"""
        c = getattr(self._local, "conn", None)
        if c is not None:
            self._local.conn = None
            c.close()
