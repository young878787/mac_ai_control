"""偏好資料庫的執行緒／子程序部分（計畫 §7.1 D6、D7）。全部用 tmp_path。
D6 的「假 play_media」與 D7 的 play_media 回 ✅ 但不寫事件。"""
import json
import subprocess
import sys
import threading
from pathlib import Path

from mac_ai import db
from mac_ai.router import local_actions
from support.youtube import FIX, net, set_real  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def test_d6_runtime_modules_do_not_load_alembic(tmp_path):
    """import 路由模組、music_prefs、rebuild 與 mac_ai.db，並實際用 music_prefs 讀寫資料庫（不呼叫 ensure_schema）
    → alembic、sqlalchemy 不在 sys.modules。資料庫由這個測試程序先用 ensure_schema 建好，子程序只連線"""
    path = tmp_path / "music.sqlite3"
    assert db.ensure_schema(path).ok
    code = (
        "import sys, random\n"
        "import mac_ai.router.fast_path, mac_ai.router.local_actions, mac_ai.router.music_prefs as mp, mac_ai.router.pinyin as py, mac_ai.db.rebuild as rb\n"
        "from mac_ai import db\n"
        f"c = db.connect({str(path)!r})\n"
        "py.warm()\n"
        "mp.learn_play(c, query='晴天', source='ytm', title='晴天', channel='周杰倫', artists=['周杰倫'], now=1.0, video_id='abcdefghijk')\n"
        "mp.record_event(c, 'play', now=1.0, query='晴天', video_id='abcdefghijk', source='ytm')\n"
        "mp.cache_put(c, '晴天', 'abcdefghijk', [], 1.0)\n"
        "assert mp.correct(c, '情天', 2.0) == '情天' and mp.favorite(c, 2.0, random.Random(0)) is not None\n"
        "mp.maintain(c, 3.0); rb.rebuild(c)\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('alembic', 'sqlalchemy'))\n"
        "print('LOADED:' + ','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "LOADED:"


def test_d6_fake_play_media_does_not_load_alembic(tmp_path):
    """子程序裡跑一次 play_media（假的 urlopen 與 subprocess，真的 SQLite）→ 結果是 ✅、事件寫進去了、sys.modules 沒有 alembic／sqlalchemy"""
    path = tmp_path / "music.sqlite3"
    assert db.ensure_schema(path).ok
    code = (
        "import sys, json, subprocess, urllib.request\n"
        "from pathlib import Path\n"
        f"FIX = Path({str(FIX)!r})\n"
        "class R:\n"
        "    def __init__(s, b): s.b = b\n"
        "    def read(s, n=-1): return s.b\n"
        "    def close(s): pass\n"
        "def fake_urlopen(req, timeout=None, **kw):\n"
        "    kind = 'ytm' if 'music.youtube.com' in req.full_url else 'web'\n"
        "    return R((FIX / f'{kind}_qingtian.json').read_bytes())\n"
        "urllib.request.urlopen = fake_urlopen\n"
        "subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, '', '')\n"
        "import mac_ai.router.fast_path\n"
        "from mac_ai import db\n"
        "from mac_ai.router import local_actions as la\n"
        f"la.MUSIC_DB = db.MusicDb({str(path)!r}, ensure=lambda p: db.EnsureResult(True))\n"
        "la.MUSIC_DB.start(); assert la.MUSIC_DB.wait_ready(10)\n"
        "out = la.play_media('晴天')\n"
        "n = la.MUSIC_DB.conn().execute(\"SELECT COUNT(*) FROM events WHERE kind='play'\").fetchone()[0]\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('alembic', 'sqlalchemy'))\n"
        "print(json.dumps({'out': out, 'events': n, 'bad': bad}, ensure_ascii=False))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["out"].startswith("✅ 播放：") and got["events"] == 1 and got["bad"] == []


def test_d6_ensure_schema_is_the_only_place_that_loads_alembic(tmp_path):
    """反向確認 D6 的檢查有效：呼叫 ensure_schema 之後才會載入 alembic"""
    code = (
        "import sys\n"
        "from mac_ai import db\n"
        f"db.ensure_schema({str(tmp_path / 'music.sqlite3')!r})\n"
        "print('alembic' in sys.modules)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True"


def _gated_ensure():
    """假 ensure：等 release 事件才完成（代替『卡 1 秒』，不用 sleep 空等）"""
    started, release = threading.Event(), threading.Event()
    calls = []

    def ensure(path):
        calls.append(path)
        started.set()
        assert release.wait(10)
        return db.ensure_schema(path)

    return ensure, started, release, calls


def test_d7_background_start_does_not_block_and_conn_none_until_ready(tmp_path):
    path = tmp_path / "music.sqlite3"
    ensure, started, release, calls = _gated_ensure()
    m = db.MusicDb(path, ensure=ensure)
    m.start()                                        # 立刻返回（ensure 還卡著）
    assert started.wait(5)
    assert m.conn() is None and not m.ready and m.message == ""   # 準備好之前：照常播放、不寫紀錄
    release.set()
    assert m.wait_ready(5)
    c = m.conn()
    assert c is not None and m.ready
    c.execute("INSERT INTO events (ts, kind, ok) VALUES (1.0, 'play', 1)")
    c.commit()
    assert m.conn() is c                             # 同一執行緒重複取得同一條連線
    m.close()
    assert calls == [path]


def test_d7_start_twice_runs_ensure_once(tmp_path):
    ensure, started, release, calls = _gated_ensure()
    m = db.MusicDb(tmp_path / "music.sqlite3", ensure=ensure)
    m.start()
    m.start()
    assert started.wait(5)
    release.set()
    assert m.wait_ready(5)
    assert len(calls) == 1


def test_d7_connection_is_created_in_callers_thread_not_shared(tmp_path):
    m = db.MusicDb(tmp_path / "music.sqlite3")
    m.start()
    assert m.wait_ready(10)
    main_conn = m.conn()
    box = {}
    t = threading.Thread(target=lambda: box.update(conn=m.conn(), used=m.conn().execute("SELECT 1").fetchone()))
    t.start()
    t.join(5)
    assert box["used"] == (1,)                       # 別的執行緒拿到自己的連線，能用
    assert box["conn"] is not main_conn
    m.close()


def test_d7_ensure_failure_gives_none_and_warning(tmp_path):
    def failing(path):
        return db.EnsureResult(False, "⚠️ 升級失敗")

    m = db.MusicDb(tmp_path / "music.sqlite3", ensure=failing)
    m.start()
    assert m.wait_ready(5)
    assert m.conn() is None and not m.ready and m.message == "⚠️ 升級失敗"


def test_d7_ensure_raising_is_contained(tmp_path):
    def boom(path):
        raise RuntimeError("x")

    m = db.MusicDb(tmp_path / "music.sqlite3", ensure=boom)
    m.start()
    assert m.wait_ready(5)
    assert m.conn() is None and m.message.startswith("⚠️")


def test_d7_play_media_plays_without_records_until_ready_then_writes(tmp_path, monkeypatch, net, sh):
    """卡住的 ensure 期間點歌照常回 ✅、不寫事件、不建資料庫檔；準備好之後再點歌才寫事件（D7 的 play_media 那半）"""
    path = tmp_path / "music.sqlite3"
    ensure, started, release, calls = _gated_ensure()
    m = db.MusicDb(path, ensure=ensure)
    monkeypatch.setattr(local_actions, "MUSIC_DB", m)
    m.start()
    assert started.wait(5)
    set_real(net, "qingtian")
    assert local_actions.play_media("晴天").startswith("✅ 播放：") and not path.exists()   # 準備中：沒有紀錄
    release.set()
    assert m.wait_ready(10)
    assert local_actions.play_media("晴天").startswith("✅ 播放：")
    n = m.conn().execute("SELECT COUNT(*) FROM events WHERE kind='play'").fetchone()[0]
    assert n == 1
    m.close()
