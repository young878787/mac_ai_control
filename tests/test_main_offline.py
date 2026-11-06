"""main.py 的啟動流程：服務啟動順序、失敗時的處置、結束時一定收掉服務（服務、麥克風、LLM 都用假的）。"""
import pytest

import build_vocab
import main
from mac_ai.services import ServiceError


class FakeService:
    def __init__(self, name, log, start_error=None, ready_error=None):
        self.name, self.log = name, log
        self.start_error, self.ready_error = start_error, ready_error

    def start(self):
        self.log.append(f"{self.name}.start")
        if self.start_error:
            raise self.start_error

    def wait_ready(self, timeout):
        self.log.append(f"{self.name}.wait_ready")
        if self.ready_error:
            raise self.ready_error

    def alive(self):
        return True


class FakeMic:
    def __init__(self, log, device=None, start_error=None):
        self.log, self.start_error = log, start_error
        log.append(f"mic.new({device})")

    def start(self):
        self.log.append("mic.start")
        if self.start_error:
            raise self.start_error

    def close(self):
        self.log.append("mic.close")


@pytest.fixture
def env(monkeypatch):
    """把 main 依賴的外部東西全換成會記錄呼叫順序的假物件；回傳 (log, 可調整的設定)"""
    log = []
    cfg = {"llm": {}, "stt": {}, "mic": {}, "stt_factory_error": None, "warm": 1.5}

    def llm_service():
        return FakeService("llm", log, **cfg["llm"])

    def stt_service():
        if cfg["stt_factory_error"]:
            raise cfg["stt_factory_error"]
        return FakeService("stt", log, **cfg["stt"])

    monkeypatch.setattr(build_vocab, "build", lambda *a, **k: log.append("build_vocab"))
    monkeypatch.setattr(main, "llm_service", llm_service)
    monkeypatch.setattr(main, "stt_service", stt_service)
    monkeypatch.setattr(main, "stop_all", lambda services: log.append(("stop_all", [s.name for s in services])))
    monkeypatch.setattr(main, "warmup_llm", lambda: log.append("warmup") or cfg["warm"])
    monkeypatch.setattr(main, "MicSource", lambda device=None: FakeMic(log, device, **cfg["mic"]))
    monkeypatch.setattr(main, "run_voice_loop", lambda mic, execute, stop, **kw: log.append(("voice_loop", execute, kw["hints"])))
    monkeypatch.setattr(main, "wait_forever", lambda: log.append("wait_forever"))
    return log, cfg


# ---------- --text ----------
def test_text_mode_sends_text_without_starting_anything(monkeypatch, env):
    log, _ = env
    sent = []
    monkeypatch.setattr(main, "process_voice_command", sent.append)
    assert main.main(["--text", "打開小算盤，然後把音量調到", "40"]) == 0
    assert sent == ["打開小算盤，然後把音量調到 40"]
    assert log == []


# ---------- 正常流程 ----------
def test_full_start_order_and_cleanup(env, capsys):
    log, _ = env
    assert main.main(["--device", "2"]) == 0
    assert log[:5] == ["build_vocab", "llm.start", "stt.start", "llm.wait_ready", "stt.wait_ready"]
    assert log[5:8] == ["warmup", "mic.new(2)", "mic.start"]
    kind, execute, hints = log[8]
    assert kind == "voice_loop" and execute is main.process_voice_command
    assert "露醬" in hints and "Google Chrome" in hints  # 詞表逐行讀入，含空白的詞不被拆開
    assert log[9:] == ["mic.close", ("stop_all", ["llm", "stt"])]
    out = capsys.readouterr().out
    assert "LLM 就緒" in out and "STT 就緒" in out and "喚醒詞已關閉" in out


def test_start_message_names_wake_words_when_enabled(env, monkeypatch, capsys):
    monkeypatch.setattr(main.config, "WAKE_WORD_ENABLED", True)
    assert main.main([]) == 0
    out = capsys.readouterr().out
    assert "露西婭" in out and "露醬" in out and "喚醒詞已關閉" not in out


def test_no_stt_only_runs_llm_and_waits(env):
    log, _ = env
    assert main.main(["--no-stt"]) == 0
    assert log == ["build_vocab", "llm.start", "llm.wait_ready", "warmup", "wait_forever", ("stop_all", ["llm"])]


# ---------- LLM 失敗：警告但繼續 ----------
@pytest.mark.parametrize("llm_cfg", [{"ready_error": ServiceError("LLM 在 90s 內未就緒")},
                                    {"start_error": ServiceError("LLM 無法啟動")}])
def test_llm_failure_warns_and_voice_mode_still_runs(env, capsys, llm_cfg):
    log, cfg = env
    cfg["llm"] = llm_cfg
    assert main.main([]) == 0
    assert "warmup" not in log and "mic.start" in log
    assert any(isinstance(x, tuple) and x[0] == "voice_loop" for x in log)
    out = capsys.readouterr().out
    assert "⚠️" in out and "Fast Path 仍可使用" in out
    assert log[-1] == ("stop_all", ["llm", "stt"])


def test_llm_warmup_failure_is_only_a_warning(env):
    log, cfg = env
    cfg["warm"] = None
    assert main.main([]) == 0
    assert "mic.start" in log


# ---------- STT / 麥克風失敗：結束並收掉服務 ----------
def test_stt_not_ready_exits_with_error_and_cleans_up(env, capsys):
    log, cfg = env
    cfg["stt"] = {"ready_error": ServiceError("語音辨識授權被拒絕")}
    assert main.main([]) == 1
    assert "mic.start" not in log and not any(isinstance(x, tuple) and x[0] == "voice_loop" for x in log)
    assert log[-1] == ("stop_all", ["llm", "stt"])
    assert "❌" in capsys.readouterr().out


def test_stt_unavailable_at_creation_exits_with_error_and_stops_llm(env):
    log, cfg = env
    cfg["stt_factory_error"] = ServiceError("找不到 macos-stt 的虛擬環境")
    assert main.main([]) == 1
    assert log[-1] == ("stop_all", ["llm"])


def test_mic_failure_exits_with_error_and_cleans_up(env, capsys):
    log, cfg = env
    cfg["mic"] = {"start_error": OSError("無法開啟麥克風：denied")}
    assert main.main([]) == 1
    assert "mic.close" in log and log[-1] == ("stop_all", ["llm", "stt"])
    assert "麥克風" in capsys.readouterr().out


# ---------- Ctrl+C ----------
@pytest.mark.parametrize("where", ["llm.wait_ready", "stt.wait_ready", "voice_loop"])
def test_keyboard_interrupt_anywhere_cleans_up_and_exits_zero(env, monkeypatch, where):
    log, _ = env
    if where == "voice_loop":
        def interrupted(*a, **k):
            raise KeyboardInterrupt
        monkeypatch.setattr(main, "run_voice_loop", interrupted)
    else:
        name = where.split(".")[0]
        monkeypatch.setattr(FakeService, "wait_ready",
                            lambda self, timeout: (_ for _ in ()).throw(KeyboardInterrupt) if self.name == name else None)
    assert main.main([]) == 0
    assert log[-1][0] == "stop_all"


# ---------- --ui（桌面角色介面） ----------
class FakeBus:
    def __init__(self, log, start_error=None, **kw):
        self.log, self.start_error = log, start_error
        self.url, self.health_url = "ws://127.0.0.1:1/?token=t", "http://127.0.0.1:1/health"

    def start(self):
        self.log.append("bus.start")
        if self.start_error:
            raise self.start_error

    def stop(self):
        self.log.append("bus.stop")


@pytest.fixture
def ui_env(env, monkeypatch):
    log, cfg = env
    cfg["ui"] = {}
    cfg["bus"] = {}
    monkeypatch.setattr(main, "check_ui_ready", lambda: cfg.get("ui_problem"))
    monkeypatch.setattr(main, "UiBus", lambda **kw: FakeBus(log, **cfg["bus"]))
    monkeypatch.setattr(main, "ui_service", lambda url, health: FakeService("ui", log, **cfg["ui"]))
    monkeypatch.setattr(main, "run_voice_loop", lambda mic, execute, stop, **kw: log.append(("voice_loop", sorted(kw), kw.get("gate"), kw.get("bus"))))
    return log, cfg


def test_ui_mode_starts_bus_and_ui_then_shuts_both_down(ui_env, capsys):
    log, _ = ui_env
    assert main.main(["--ui"]) == 0
    assert log.index("bus.start") < log.index("ui.start") < log.index("ui.wait_ready") < log.index("mic.start")
    kind, kw_names, gate, bus = next(e for e in log if isinstance(e, tuple) and e[0] == "voice_loop")
    assert "gate" in kw_names and gate.enabled is True and isinstance(bus, FakeBus)  # UI 模式預設啟用喚醒詞
    assert ("stop_all", ["llm", "stt", "ui"]) in log and log[-1] == "bus.stop"
    out = capsys.readouterr().out
    assert "桌面角色就緒" in out and "露西婭" in out and "喚醒詞已關閉" not in out


def test_ui_not_installed_exits_with_hint_before_starting_anything(ui_env, capsys):
    log, cfg = ui_env
    cfg["ui_problem"] = "找不到 Electron，請先安裝：cd desktop && npm install"
    assert main.main(["--ui"]) == 1
    assert log == []
    assert "npm install" in capsys.readouterr().out


def test_ui_failing_to_start_keeps_voice_running(ui_env, capsys):
    log, cfg = ui_env
    cfg["ui"] = {"ready_error": ServiceError("UI（Electron） 提前結束（結束碼 1）")}
    assert main.main(["--ui"]) == 0
    assert any(isinstance(e, tuple) and e[0] == "voice_loop" for e in log)
    assert "沒有桌面角色，語音仍可使用" in capsys.readouterr().out
    assert ("stop_all", ["llm", "stt", "ui"]) in log


def test_ui_bus_port_in_use_exits_and_cleans_up(ui_env, capsys):
    log, cfg = ui_env
    cfg["bus"] = {"start_error": OSError("UI 事件匯流排無法啟動：Address already in use")}
    assert main.main(["--ui"]) == 1
    assert ("stop_all", ["llm", "stt"]) in log
    assert "Address already in use" in capsys.readouterr().out


def test_without_ui_flag_nothing_ui_related_happens(ui_env):
    log, _ = ui_env
    assert main.main([]) == 0
    assert "bus.start" not in log and "ui.start" not in log
    kind, kw_names, gate, bus = next(e for e in log if isinstance(e, tuple) and e[0] == "voice_loop")
    assert gate is None and bus is None
    assert ("stop_all", ["llm", "stt"]) in log
