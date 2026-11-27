"""main.py 入口：--text、正常流程、LLM／STT／麥克風失敗、Ctrl+C、--ui、語音辨識引擎選擇（含模擬 Windows）"""
import importlib.util
import subprocess
import sys

import main

import pytest

import build_vocab
from mac_ai import config, services
from mac_ai.services import Service, ServiceError


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
    monkeypatch.setattr(main, "check_ui_ready", lambda model: cfg.get("ui_problem"))
    monkeypatch.setattr(main, "UiBus", lambda **kw: FakeBus(log, **cfg["bus"]))
    monkeypatch.setattr(main, "ui_service", lambda url, health, model: cfg.setdefault("ui_models", []).append(model) or FakeService("ui", log, **cfg["ui"]))
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


@pytest.mark.parametrize("argv, character", [(["--ui"], "rushia"), (["--ui", "--character", "hiyori"], "hiyori")])
def test_ui_character_flag_picks_the_model(ui_env, argv, character):
    _, cfg = ui_env
    assert main.main(argv) == 0
    assert cfg["ui_models"] == [main.config.UI_CHARACTERS[character]]


def test_unknown_character_is_rejected():
    with pytest.raises(SystemExit):
        main.parse_args(["--ui", "--character", "nobody"])


def test_without_ui_flag_nothing_ui_related_happens(ui_env):
    log, _ = ui_env
    assert main.main([]) == 0
    assert "bus.start" not in log and "ui.start" not in log
    kind, kw_names, gate, bus = next(e for e in log if isinstance(e, tuple) and e[0] == "voice_loop")
    assert gate is None and bus is None
    assert ("stop_all", ["llm", "stt"]) in log


# ---------- 語音辨識引擎選擇（--stt-engine）與模擬 Windows（X16；沒有 Windows 實機，只能這樣驗） ----------
@pytest.fixture
def engines(env, monkeypatch):
    """stt_service（macos-stt）與 xasr_stt_service 都換成會記錄的假物件；log 看得出這次選了哪一個"""
    log, cfg = env
    monkeypatch.setattr(main, "stt_service", lambda: FakeService("stt-macos", log))
    monkeypatch.setattr(main, "xasr_stt_service", lambda: FakeService("stt-xasr", log))
    return log


@pytest.mark.parametrize("argv, platform, default_engine, started", [
    ([], "darwin", "macos", "stt-macos"),  # macOS 預設不變
    (["--stt-engine", "xasr"], "darwin", "macos", "stt-xasr"),  # macOS 上明確選備用引擎
    (["--stt-engine", "macos"], "darwin", "xasr", "stt-macos"),  # 明確選的蓋過預設
    ([], "win32", "xasr", "stt-xasr"),  # Windows 預設 xasr
])
def test_engine_choice_follows_flag_then_config_default(engines, monkeypatch, argv, platform, default_engine, started):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(main.config, "STT_ENGINE", default_engine)
    assert main.main(argv) == 0
    assert f"{started}.start" in engines and f"{started}.wait_ready" in engines
    assert not any(isinstance(e, str) and e.startswith("stt-") and not e.startswith(started) for e in engines)


def test_macos_engine_is_refused_off_macos_before_anything_starts(engines, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    assert main.main(["--stt-engine", "macos"]) == 1
    assert engines == []  # 沒有 build_vocab、沒有任何服務被啟動
    assert "只能在 macOS" in capsys.readouterr().out


def test_no_stt_ignores_the_engine_check(engines, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert main.main(["--no-stt", "--stt-engine", "macos"]) == 0


def test_xasr_model_missing_exits_with_download_hint_and_cleans_up(env, monkeypatch, tmp_path, capsys):
    log, _ = env
    monkeypatch.setattr(config, "XASR_MODEL_DIR", tmp_path / "nothing")
    assert main.main(["--stt-engine", "xasr"]) == 1
    out = capsys.readouterr().out
    assert "找不到 X-ASR 模型檔" in out and "README" in out
    assert log[-1] == ("stop_all", ["llm"])


def load_config_as(platform, monkeypatch):
    """用指定的 sys.platform 重新執行 config.py（載入成另一個模組物件，不動到真正的 mac_ai.config）"""
    monkeypatch.setattr(sys, "platform", platform)
    spec = importlib.util.spec_from_file_location("config_winsim", config.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("platform, engine", [("win32", "xasr"), ("darwin", "macos"), ("linux", "macos")])
def test_platform_decides_the_default_engine(monkeypatch, platform, engine):
    assert load_config_as(platform, monkeypatch).STT_ENGINE == engine


def test_winsim_xasr_never_touches_macos_only_stt_machinery(env, monkeypatch, tmp_path):
    """模擬 Windows、不帶 --stt-engine：用真的 xasr_stt_service 與 stop_all，斷言沒有讀 PID 檔、沒有呼叫 ps、沒有檢查語音辨識授權"""
    log, _ = env
    model = tmp_path / "x-asr"
    model.mkdir()
    for name in (*services.XASR_MODEL_FILES, config.XASR_TOKENS):
        (model / name).write_text("")
    monkeypatch.setattr(config, "XASR_MODEL_DIR", model)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(main.config, "STT_ENGINE", "xasr")

    forbidden = []
    for name in ("kill_stt_backend", "_stt_ready"):
        monkeypatch.setattr(services, name, lambda *a, _n=name, **k: forbidden.append(_n))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: forbidden.append(("subprocess.run", a)))
    monkeypatch.setattr(main, "xasr_stt_service", services.xasr_stt_service)
    monkeypatch.setattr(main, "stop_all", lambda started: services.stop_all([s for s in started if isinstance(s, Service)]))  # 真的收尾邏輯；LLM 的假服務不收

    class Done:  # 假的子程序：已經結束
        pid, returncode = 1, 0

        def poll(self):
            return 0

    def fake_start(self):
        log.append(f"{self.name}.start")
        self.proc = Done()

    monkeypatch.setattr(Service, "start", fake_start)
    monkeypatch.setattr(Service, "wait_ready", lambda self, timeout: log.append(f"{self.name}.wait_ready"))
    assert main.main([]) == 0
    assert "STT（X-ASR）.start" in log and "STT（X-ASR）.wait_ready" in log
    assert forbidden == []
