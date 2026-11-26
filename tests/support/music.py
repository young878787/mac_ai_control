"""點歌、播放控制、回饋的 flow 測試（tests/flow/test_route_music*.py）共用的假環境與小工具。
fixture（bg、tabs、mclock）用 import 帶進測試檔（ruff 對 tests/** 忽略 F811）。
底層全部是假的：subprocess（pgrep、osascript）、輔助使用 API、媒體鍵；不得真的對 Safari 按按鈕或送媒體鍵"""
import subprocess
import types

import pytest

import mac_ai.router as r
from mac_ai.router import prefs_db
from support.clock import Clock

# 背景控制（bg）：Safari 的行程編號與它目前分頁的網址
WATCH = "https://www.youtube.com/watch?v=l6a5D6yxqEU&list=RDl6a5D6yxqEU"
SAFARI_PID = 777
# 點歌（tabs、mclock）：fixture「晴天」與時鐘起點
Y_QUERY = "晴天"
Y_ID = "DYptgVvkVLQ"  # fixture「晴天」挑中的官方 MV（tests/runtime/test_youtube_search.py 的 REAL）
Y_URL = f"https://www.youtube.com/watch?v={Y_ID}&list=RD{Y_ID}"
T0 = 1_800_000_000.0
DAY = 86400.0


@pytest.fixture
def bg(monkeypatch, native_calls):
    """背景控制的假環境。bg.pid＝Safari 的行程編號（0＝沒在執行）、bg.url＝它最近使用的視窗目前分頁的網址（None＝osascript 失敗）、
    bg.tabs＝其他視窗／背景分頁的網址（目前分頁自己一定也在『所有分頁』裡，和真實腳本的輸出一致）、
    bg.ax／bg.key＝輔助使用 API 按鈕、媒體鍵的回傳狀態、bg.label＝讀按鈕文字的結果；bg.cmds＝所有 subprocess 指令，bg.pids＝按按鈕的對象行程。
    native_calls 照 conftest 的格式記錄（button_in_window／mediakey／label）"""
    from mac_ai.router import native
    env = types.SimpleNamespace(pid=SAFARI_PID, url=WATCH, tabs=[], ax="ok", key="ok", label=None, cmds=[], pids=[])

    def fake_run(cmd, **kw):
        env.cmds.append(cmd)
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(cmd, 0 if env.pid else 1, f"{env.pid}\n" if env.pid else "", "")
        if cmd[0] == "osascript" and "Safari" in cmd[2]:
            urls = ([env.url] if env.url else []) + env.tabs
            out = "\n".join([f"C:{env.url or ''}"] + [f"T:{u}" for u in urls])
            return subprocess.CompletedProcess(cmd, 0 if env.url is not None else 1, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def press(pid, pattern):
        env.pids.append(pid)
        native_calls.append(f"button_in_window {pattern}")
        return env.ax

    def media(key):
        native_calls.append(f"mediakey {key}")
        return env.key

    def label(pid, pattern):
        native_calls.append(f"label {pattern}")
        return env.label

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(native, "press_button_in_window", press)
    monkeypatch.setattr(native, "post_media_key", media)
    monkeypatch.setattr(native, "button_label", label)
    return env


def _control(text):
    step = r.process_voice_command(text)["steps"][0]
    return step, step.get("control")


@pytest.fixture
def tabs(monkeypatch):
    """假 subprocess：osascript（找分頁改網址）回 tabs.osa（'reused'＝有音樂分頁、'none'＝沒有），pgrep 回找到，其餘回 0。
    記錄 tabs.cmds（指令）、tabs.timeouts、tabs.envs；tabs.fail(程式名) 讓該程式回結束碼 1"""
    env = types.SimpleNamespace(cmds=[], timeouts=[], envs=[], osa="none", failing=set())
    env.fail = lambda *names: env.failing.update(names)
    env.of = lambda name: [c for c in env.cmds if c[0] == name]

    def fake_run(cmd, **kw):
        env.cmds.append(list(cmd))
        env.timeouts.append(kw.get("timeout"))
        env.envs.append(kw.get("env"))
        if cmd[0] in env.failing:
            return subprocess.CompletedProcess(cmd, 1, "", "boom")
        return subprocess.CompletedProcess(cmd, 0, env.osa if cmd[0] == "osascript" else "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return env


@pytest.fixture
def mclock(monkeypatch):
    clock = Clock(T0)
    monkeypatch.setattr(prefs_db, "_now", clock)
    return clock


def _step(text):
    return r.process_voice_command(text)["steps"][0]


def _rows(music_db, sql, *args):
    return music_db.conn().execute(sql, args).fetchall()


def _events(music_db, kind=None):
    sql = "SELECT kind, utterance, query, corrected, video_id, source, latency_ms, ok FROM events"
    return _rows(music_db, sql + (" WHERE kind=?" if kind else "") + " ORDER BY id", *([kind] if kind else []))
