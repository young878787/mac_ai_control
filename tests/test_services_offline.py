"""背景服務管理：用假的子程序與本機 HTTP server 驗證啟動、就緒判斷、沿用、結束清理（不啟動真的模型）。"""
import json
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import REAL_URLOPEN

from mac_ai import config, services
from mac_ai.services import Service, ServiceError, probe, stop_all


@pytest.fixture(autouse=True)
def real_http(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", REAL_URLOPEN)


@pytest.fixture
def started():
    """收集測試中啟動的子程序，結束時一律收掉"""
    procs = []
    yield procs
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def health_server():
    """回傳 start(body, status=200) → url；/health 回應指定內容"""
    servers = []

    def start(body, status=200):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}/health"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def py_service(tmp_path, script, port=None, **kw) -> Service:
    kw.setdefault("health_url", f"http://127.0.0.1:{port or free_port()}/health")
    return Service(name="假服務", cmd=[sys.executable, "-u", "-c", textwrap.dedent(script)], cwd=tmp_path,
                   log_path=tmp_path / "logs" / "fake.log", **kw)


SERVE_HEALTH = """
    import json, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(HEALTH).encode())
        def log_message(self, *a): pass
    HTTPServer(("127.0.0.1", {port}), H).serve_forever()
"""


# ---------- probe ----------
def test_probe_returns_json_dict(health_server):
    assert probe(health_server({"status": "ok", "auth": "authorized"})) == {"status": "ok", "auth": "authorized"}


def test_probe_non_json_200_is_empty_dict(health_server):
    assert probe(health_server(b"OK")) == {}


def test_probe_error_status_or_unreachable_is_none(health_server):
    assert probe(health_server({"x": 1}, status=503)) is None
    assert probe(f"http://127.0.0.1:{free_port()}/health") is None


# ---------- 啟動與就緒 ----------
def test_start_wait_ready_and_stop_a_real_child(tmp_path, started):
    port = free_port()
    svc = py_service(tmp_path, SERVE_HEALTH.replace("{port}", str(port)).replace("HEALTH", "{'status': 'ok'}"), port)
    svc.start()
    started.append(svc.proc)
    svc.wait_ready(timeout=10, poll_s=0.05)
    assert svc.alive() and not svc.reused
    t = time.monotonic()
    stop_all([svc])
    assert not svc.alive() and time.monotonic() - t < 3


def test_wait_ready_reports_early_exit_with_log_tail(tmp_path, started):
    svc = py_service(tmp_path, "print('boom: port in use'); raise SystemExit(3)")
    svc.start()
    started.append(svc.proc)
    with pytest.raises(ServiceError, match=r"提前結束（結束碼 3）") as e:
        svc.wait_ready(timeout=10, poll_s=0.05)
    assert "boom: port in use" in str(e.value)


def test_wait_ready_times_out_when_child_never_serves(tmp_path, started):
    svc = py_service(tmp_path, "import time; time.sleep(60)")
    svc.start()
    started.append(svc.proc)
    with pytest.raises(ServiceError, match="0.3s 內未就緒"):
        svc.wait_ready(timeout=0.3, poll_s=0.05)


def test_start_reuses_healthy_server_and_never_stops_it(tmp_path, health_server):
    stopped = []
    svc = Service(name="既有", cmd=["/nonexistent/should-not-run"], health_url=health_server({"status": "ok"}),
                  cwd=tmp_path, log_path=tmp_path / "x.log", after_stop=lambda: stopped.append(1))
    svc.start()
    svc.wait_ready(timeout=0.1)
    assert svc.reused and svc.proc is None
    stop_all([svc])
    assert stopped == []


def test_start_failure_to_spawn_is_a_service_error(tmp_path):
    svc = Service(name="壞掉", cmd=["/nonexistent/bin"], health_url=f"http://127.0.0.1:{free_port()}/health",
                  cwd=tmp_path, log_path=tmp_path / "x.log")
    with pytest.raises(ServiceError, match="無法啟動"):
        svc.start()


def test_ready_callable_decides_readiness(tmp_path, started):
    port = free_port()
    script = SERVE_HEALTH.replace("{port}", str(port)).replace("HEALTH", "{'auth': 'not_determined'}")
    svc = py_service(tmp_path, script, port, ready=lambda h: h.get("auth") == "authorized")
    svc.start()
    started.append(svc.proc)
    with pytest.raises(ServiceError, match="未就緒"):
        svc.wait_ready(timeout=0.5, poll_s=0.05)


# ---------- STT 授權判斷 ----------
@pytest.mark.parametrize("health, expected", [
    ({"status": "ok", "auth": "authorized"}, True),
    ({"status": "ok", "auth": "not_determined"}, False),
    ({"status": "starting", "auth": "authorized"}, False),
    ({}, False),
])
def test_stt_ready(health, expected):
    assert services._stt_ready(health) is expected


def test_stt_denied_is_fatal_with_instructions():
    with pytest.raises(ServiceError, match="語音辨識"):
        services._stt_ready({"status": "ok", "auth": "denied"})


# ---------- 結束清理 ----------
IGNORE = """
    import signal, time
    for s in {signals}:
        signal.signal(s, signal.SIG_IGN)
    print("ready", flush=True)
    time.sleep(60)
"""


def wait_for_log(svc, text):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if text in svc._log_tail():
            return
        time.sleep(0.02)
    raise AssertionError(f"log 沒有出現 {text!r}")


@pytest.mark.parametrize("ignored", [["signal.SIGINT"], ["signal.SIGINT", "signal.SIGTERM"]])
def test_stop_all_escalates_when_child_ignores_signals(tmp_path, started, ignored):
    svc = py_service(tmp_path, IGNORE.replace("{signals}", f"[{', '.join(ignored)}]"))
    svc.start()
    started.append(svc.proc)
    wait_for_log(svc, "ready")
    stop_all([svc], sigint_grace=0.2, sigterm_grace=0.2)
    assert not svc.alive()


def test_stop_all_stops_services_in_parallel(tmp_path, started):
    svcs = [py_service(tmp_path, IGNORE.replace("{signals}", "[signal.SIGINT]")) for _ in range(2)]
    for s in svcs:
        s.log_path = tmp_path / "logs" / f"{id(s)}.log"
        s.start()
        started.append(s.proc)
    for s in svcs:
        wait_for_log(s, "ready")
    t = time.monotonic()
    stop_all(svcs, sigint_grace=0.5, sigterm_grace=0.5)
    assert not any(s.alive() for s in svcs)
    assert time.monotonic() - t < 1.5  # 並行：約一個寬限期，而不是兩個


def test_after_stop_runs_for_started_services(tmp_path, started):
    ran = []
    svc = py_service(tmp_path, "import time; time.sleep(60)", after_stop=lambda: ran.append(1))
    svc.start()
    started.append(svc.proc)
    stop_all([svc], sigint_grace=1)
    assert ran == [1]


# ---------- STT 後端清理 ----------
def spawn_sleeper(started, marker):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker])
    started.append(p)
    return p


def test_kill_stt_backend_kills_macstt_process(tmp_path, started):
    victim = spawn_sleeper(started, "/x/MacSTT.app/Contents/MacOS/macstt")
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(victim.pid))
    services.kill_stt_backend(str(pid_file))
    assert victim.wait(timeout=5) is not None


def test_kill_stt_backend_leaves_unrelated_process_alone(tmp_path, started):
    bystander = spawn_sleeper(started, "unrelated")
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(bystander.pid))  # 過期的 PID 檔剛好指到別的程序
    services.kill_stt_backend(str(pid_file))
    assert bystander.poll() is None


@pytest.mark.parametrize("content", [None, "", "not-a-pid", "99999999"])
def test_kill_stt_backend_ignores_missing_or_bad_pid_file(tmp_path, content):
    pid_file = tmp_path / "pid"
    if content is not None:
        pid_file.write_text(content)
    services.kill_stt_backend(str(pid_file))


# ---------- 指令組裝 ----------
def test_llm_service_command_and_env():
    svc = services.llm_service()
    assert svc.cmd[:3] == [sys.executable, "-m", "mlx_vlm.server"]
    assert svc.cmd[svc.cmd.index("--model") + 1] == config.MODEL_ID
    assert svc.cmd[svc.cmd.index("--port") + 1] == str(config.MLX_VLM_PORT)
    assert svc.env["APC_ENABLED"] == "1"
    assert svc.health_url == f"{config.MLX_VLM_URL}/health" and svc.cwd == config.ROOT


def test_stt_service_command_uses_its_own_venv_and_vocab(monkeypatch, tmp_path):
    fake_python = tmp_path / "python"
    fake_python.write_text("")
    monkeypatch.setattr(config, "STT_PYTHON", fake_python)
    svc = services.stt_service()
    assert svc.cmd[0] == str(fake_python)
    assert svc.cmd[1].endswith("macos-stt/start.py") and svc.cmd[2] == "serve"
    assert svc.cmd[svc.cmd.index("--vocab") + 1] == str(config.STT_HINTS_PATH)
    assert svc.cmd[svc.cmd.index("--port") + 1] == str(config.STT_PORT)
    assert svc.after_stop is services.kill_stt_backend


def test_stt_service_without_its_venv_explains_how_to_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STT_PYTHON", tmp_path / "missing" / "python")
    with pytest.raises(ServiceError, match="uv sync"):
        services.stt_service()
