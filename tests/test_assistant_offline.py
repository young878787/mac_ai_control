"""辨識事件 → 喚醒詞 → 指令佇列 → 依序執行（含對本機假 WS server 的端到端）。"""
import queue
import threading

import pytest
from fake_stt import FakeAudio, FakeSttServer, wait_until

from mac_ai.assistant import CommandRouter, command_worker, run_voice_loop
from mac_ai.wake import WakeGate


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def commands():
    return queue.Queue()


@pytest.fixture
def said():
    return []


@pytest.fixture
def router(clock, commands, said):
    return CommandRouter(WakeGate(["露西婭", "露醬"], window_s=8.0, clock=clock), commands, said.append)


def queued(commands):
    return list(commands.queue)


# ---------- CommandRouter ----------
@pytest.mark.parametrize("kind", ["utterance", "final"])
def test_wake_word_plus_command_is_queued_without_the_wake_word(router, commands, said, kind):
    router({"type": kind, "text": "露醬，打開小算盤"})
    assert queued(commands) == ["打開小算盤"] and said == []


@pytest.mark.parametrize("kind", ["utterance", "final"])
def test_sentence_without_wake_word_is_shown_dimmed_and_not_queued(router, commands, said, kind):
    router({"type": kind, "text": "打開小算盤"})
    assert queued(commands) == []
    assert said == ["\033[90m（未喚醒）打開小算盤\033[0m"]


def test_wake_word_only_announces_standby_then_next_sentence_is_queued(router, commands, said, clock):
    router({"type": "final", "text": "露西婭"})
    assert "已喚醒" in said[0] and "8 秒" in said[0] and queued(commands) == []
    clock.now += 3
    router({"type": "final", "text": "把音量調到 30"})
    assert queued(commands) == ["把音量調到 30"]
    router({"type": "final", "text": "打開小算盤"})  # 待命只管一次
    assert queued(commands) == ["把音量調到 30"]


def test_partial_never_executes_and_only_shows_when_enabled(clock, commands, said):
    shown = []
    r = CommandRouter(WakeGate(["露醬"], clock=clock), commands, said.append, shown.append)
    r({"type": "partial", "text": "露醬打開小算"})
    assert shown == ["露醬打開小算"] and queued(commands) == []
    r({"type": "connected"})
    assert said[-1].startswith("\r\033[K")  # 有顯示 partial 時，訊息前先清掉那一行
    CommandRouter(WakeGate(["露醬"], clock=clock), commands, said.append)({"type": "partial", "text": "x"})  # 不報錯


def test_blank_text_is_ignored_silently(router, commands, said):
    router({"type": "final", "text": "  "})
    router({"type": "final"})
    assert queued(commands) == [] and said == []


def test_connection_and_error_events_are_reported(router, said):
    router({"type": "connected"})
    router({"type": "disconnected", "message": "refused"})
    router({"type": "error", "code": "too_many_sessions", "message": "limit"})
    router({"type": "session_restarted"})  # 透明重開，不需要顯示
    assert len(said) == 3
    assert "已連上" in said[0] and "refused" in said[1] and "too_many_sessions" in said[2]


# ---------- command_worker ----------
def run_worker(commands, execute):
    stop = threading.Event()
    t = threading.Thread(target=command_worker, args=(commands, execute, stop), daemon=True)
    t.start()
    return stop, t


def test_worker_runs_commands_in_order(commands):
    done = []
    stop, t = run_worker(commands, done.append)
    for c in ("a", "b", "c"):
        commands.put(c)
    assert wait_until(lambda: done == ["a", "b", "c"])
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()


def test_worker_survives_a_failing_command(commands, capsys):
    done = []

    def execute(cmd):
        if cmd == "boom":
            raise RuntimeError("壞掉了")
        done.append(cmd)

    stop, t = run_worker(commands, execute)
    for c in ("a", "boom", "b"):
        commands.put(c)
    assert wait_until(lambda: done == ["a", "b"])
    stop.set()
    t.join(timeout=2)
    assert "壞掉了" in capsys.readouterr().err


# ---------- 端到端：假麥克風 → 假 STT server → 閘門 → 執行 ----------
def test_voice_loop_end_to_end():
    server = FakeSttServer(events=[
        {"type": "utterance", "text": "露醬，打開小算盤"},
        {"type": "utterance", "text": "今天天氣很好"},  # 沒喚醒
        {"type": "final", "text": "露西婭"},
        {"type": "final", "text": "把音量調到 30"},
    ])
    executed, said = [], []
    stop = threading.Event()
    t = threading.Thread(target=run_voice_loop, args=(FakeAudio(), executed.append, stop),
                         kwargs={"url": server.url, "say": said.append}, daemon=True)
    t.start()
    try:
        assert wait_until(lambda: executed == ["打開小算盤", "把音量調到 30"])
        assert any("（未喚醒）今天天氣很好" in s for s in said)
    finally:
        stop.set()
        t.join(timeout=5)
        server.stop()
    assert not t.is_alive()
