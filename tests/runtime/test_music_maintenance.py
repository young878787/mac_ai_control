"""點歌定時整理（計畫 §4.5、§7.1 S9／S10 的佇列那半）：計時器只負責把 QueuedJob 排進指令佇列，資料庫的讀寫都在 command_worker 上，
網路在別的執行緒、結果再排回佇列。真的執行緒、真的 SQLite（暫存目錄）、注入的時鐘。"""
import queue
import threading

import pytest

from mac_ai import db
from mac_ai.assistant import MusicMaintenance, QueuedJob, command_worker, run_voice_loop
from mac_ai.router import music_prefs
from support.clock import Clock
from support.stt import FakeAudio, FakeSttServer, wait_until

H = 3600.0


class Worker:
    """跑一條真的 command_worker 執行緒；records＝execute／choose／publish／command_log 收到的東西"""

    def __init__(self):
        self.commands: "queue.Queue[object]" = queue.Queue()
        self.stop = threading.Event()
        self.executed, self.chosen, self.published, self.logged = [], [], [], []
        self.thread = threading.Thread(
            target=command_worker, name="test-command-worker", daemon=True,
            args=(self.commands, lambda c: self.executed.append(c), self.stop, self.published.append,
                  lambda m: self.chosen.append(m), self.logged.append))
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(2)


@pytest.fixture
def worker():
    w = Worker()
    yield w
    w.close()


# ---------- command_worker：新的佇列項目不改變既有字串／dict 的行為 ----------
def test_queued_job_runs_on_worker_without_ui_events_or_command_log(worker):
    where = []
    worker.commands.put("打開小算盤")
    worker.commands.put(QueuedJob("x", lambda: where.append(threading.current_thread())))
    worker.commands.put({"n": 2, "list_id": 1, "action": "open"})
    worker.commands.put("再一句")
    assert wait_until(lambda: len(worker.executed) == 2 and len(worker.chosen) == 1 and where)
    assert where == [worker.thread] and worker.executed == ["打開小算盤", "再一句"]
    assert [e["command"] for e in worker.published if e["type"] == "executing"] == ["打開小算盤", "選擇第 2 筆", "再一句"]  # 內部工作沒有 executing／result
    assert [e["type"] for e in worker.published] == ["executing", "result"] * 3
    assert [l["command"] for l in worker.logged] == ["打開小算盤", "選擇第 2 筆", "再一句"]


def test_failing_queued_job_does_not_stop_the_worker(worker, capsys):
    def boom():
        raise RuntimeError("整理壞了")
    worker.commands.put(QueuedJob("boom", boom))
    worker.commands.put("下一句")
    assert wait_until(lambda: worker.executed == ["下一句"])
    assert "整理壞了" in capsys.readouterr().err and worker.logged and worker.logged[0]["command"] == "下一句"


# ---------- MusicMaintenance ----------
class Spy:
    """maintain／fetch／store 的假函式，記錄各自在哪條執行緒被呼叫"""

    def __init__(self, versions=None):
        self.maintain_threads, self.store_threads, self.fetch_threads = [], [], []
        self.versions = {"web": "2.20270101.00.00", "ytm": "1.20270101.00.00"} if versions is None else versions
        self.maintained, self.fetched, self.stored = threading.Event(), threading.Event(), threading.Event()
        self.maintain_calls = 0

    def maintain(self, conn, now):
        self.maintain_calls += 1
        self.maintain_threads.append(threading.current_thread())
        music_prefs.maintain(conn, now)  # 真的整理（寫資料庫）
        self.maintained.set()

    def fetch(self):
        self.fetch_threads.append(threading.current_thread())
        self.fetched.set()
        return dict(self.versions)

    def store(self, conn, versions):
        self.store_threads.append(threading.current_thread())
        music_prefs.store_client_versions(conn, versions)
        self.stored.set()


def make(music_db, worker, clock, spy, **kw):
    m = MusicMaintenance(music_db, maintain=spy.maintain, fetch_versions=spy.fetch, store_versions=spy.store,
                         schedule=music_prefs.MaintenanceSchedule(6 * H), clock=clock, poll_s=3600, ready_poll_s=0.01, **kw)
    m.start(worker.commands, worker.stop)  # poll_s 很大：計時器只在一開始看一次，之後由測試手動 tick
    return m


def test_s10_startup_run_then_every_six_hours_with_injected_clock(music_db, worker):
    clock, spy = Clock(1000.0), Spy()
    m = make(music_db, worker, clock, spy)
    assert spy.maintained.wait(5) and spy.stored.wait(5)  # 啟動時跑一次（含 clientVersion 寫回）
    assert wait_until(lambda: not m._pending)
    clock.now += 5 * H + 59 * 60
    assert m.tick() is False and spy.maintain_calls == 1   # 5 小時 59 分：不跑
    clock.now += 60
    spy.maintained.clear()
    assert m.tick() is True and spy.maintained.wait(5)      # 滿 6 小時：跑
    assert spy.maintain_calls == 2


def test_s9_all_writes_on_command_worker_network_on_another_thread(music_db, worker):
    clock, spy = Clock(1000.0), Spy()
    make(music_db, worker, clock, spy)
    assert spy.stored.wait(5)
    assert spy.maintain_threads == [worker.thread] and spy.store_threads == [worker.thread]
    assert len(spy.fetch_threads) == 1 and spy.fetch_threads[0] not in (worker.thread, threading.current_thread())  # 網路在背景執行緒，不碰資料庫
    conn = music_db.conn()
    assert music_prefs.client_versions(conn) == {"web": "2.20270101.00.00", "ytm": "1.20270101.00.00"}  # 寫回的新版本（Y10）
    assert music_prefs.maintenance_get(conn, music_prefs.KEY_LAST_RUN) == repr(1000.0)


def test_s7_maintenance_keeps_events(music_db, worker):
    conn = music_db.conn()
    for i in range(5):
        music_prefs.record_event(conn, "play", now=1.0 + i, query="x", video_id="abcdefghijk", source="ytm")
    clock, spy = Clock(10 * 86400.0), Spy()
    make(music_db, worker, clock, spy)
    assert spy.stored.wait(5)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 5


def test_no_job_is_queued_twice_while_one_is_pending(music_db):
    commands: "queue.Queue[object]" = queue.Queue()
    spy = Spy()
    m = MusicMaintenance(music_db, maintain=spy.maintain, fetch_versions=spy.fetch, store_versions=spy.store,
                         schedule=music_prefs.MaintenanceSchedule(6 * H), clock=Clock(1.0))
    m._commands = commands  # 不啟動計時器，手動 tick
    assert m.tick() is True and m.tick() is False and commands.qsize() == 1
    assert isinstance(commands.get_nowait(), QueuedJob)


def test_failed_database_never_queues_maintenance(tmp_path):
    commands: "queue.Queue[object]" = queue.Queue()
    mdb = db.MusicDb(tmp_path / "m.sqlite3", ensure=lambda p: db.EnsureResult(False, "⚠️ 壞了"))
    mdb.start()
    assert mdb.wait_ready(5)
    spy = Spy()
    m = MusicMaintenance(mdb, maintain=spy.maintain, fetch_versions=spy.fetch, store_versions=spy.store,
                         schedule=music_prefs.MaintenanceSchedule(6 * H), clock=Clock(1.0))
    m._commands = commands
    assert m.tick() is False and commands.empty()


def test_fetch_failure_or_empty_result_writes_nothing(music_db, worker):
    clock, spy = Clock(1000.0), Spy(versions={})
    make(music_db, worker, clock, spy)
    assert spy.maintained.wait(5) and spy.fetched.wait(5)
    assert wait_until(lambda: not spy.fetch_threads[0].is_alive()) and worker.commands.empty()
    assert not spy.stored.is_set() and music_prefs.maintenance_get(music_db.conn(), music_prefs.KEY_WEB_VERSION) is None


def test_waits_for_background_database_before_first_run(tmp_path, worker):
    """資料庫還在背景準備：計時器等它準備好才排第一個工作（不會在 conn() 是 None 時白跑一次而延後 60 秒）"""
    gate = threading.Event()

    def ensure(path):
        assert gate.wait(10)
        return db.ensure_schema(path)
    mdb = db.MusicDb(tmp_path / "m.sqlite3", ensure=ensure)
    mdb.start()
    spy, clock = Spy(), Clock(1.0)
    make(mdb, worker, clock, spy)
    assert not spy.maintained.wait(0.2)
    gate.set()
    assert spy.maintained.wait(10) and spy.maintain_threads == [worker.thread]


def test_run_voice_loop_gives_maintenance_the_worker_queue():
    """run_voice_loop 把自己的佇列與 stop 交給 maintenance.start；排進去的 QueuedJob 在同一條 worker 上和語音指令依序執行"""
    server = FakeSttServer(events=[{"type": "final", "text": "打開小算盤"}])
    where, executed = [], []
    started = {}

    class FakeMaint:
        def start(self, commands, stop):
            started.update(commands=commands, stop=stop)
            commands.put(QueuedJob("m", lambda: where.append(threading.current_thread().name)))

    stop = threading.Event()
    t = threading.Thread(target=run_voice_loop, args=(FakeAudio(), executed.append, stop),
                         kwargs={"url": server.url, "say": lambda m: None, "maintenance": FakeMaint()}, daemon=True)
    t.start()
    try:
        assert wait_until(lambda: executed == ["打開小算盤"] and len(where) == 1)
    finally:
        stop.set()
        t.join(timeout=5)
        server.stop()
    assert isinstance(started["commands"], queue.Queue) and started["stop"] is stop
