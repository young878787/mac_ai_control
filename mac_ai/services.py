"""背景服務管理：啟動 mlx_vlm.server 與 macos-stt server、等待就緒、結束時清理。"""
import http.client
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from mac_ai import config


class ServiceError(Exception):
    """服務無法啟動或不可用"""


def probe(url: str, timeout: float = 1.0) -> Optional[dict]:
    """GET url：連不上或狀態碼不是 200 回傳 None；200 回傳解析後的 JSON（不是 JSON 物件則回傳空 dict）"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
    except (OSError, http.client.HTTPException):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class Service:
    name: str
    cmd: List[str]
    health_url: str
    cwd: Path
    log_path: Path
    ready: Callable[[dict], bool] = lambda health: True  # 收到 /health 內容後判斷是否可用；不可恢復的狀況可拋 ServiceError
    env: Optional[Dict[str, str]] = None
    after_stop: Optional[Callable[[], None]] = None  # 程序結束後的額外清理
    proc: Optional[subprocess.Popen] = None
    reused: bool = False  # 沿用已在執行的服務；不是我們啟動的，結束時不關它

    def start(self) -> None:
        """服務已經健康就沿用；否則在新 session 啟動子程序（Ctrl+C 不會直接打到它，由 stop_all 統一收尾），輸出寫入 log"""
        health = probe(self.health_url)
        if health is not None and self.ready(health):
            self.reused = True
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("wb") as log:
            try:
                self.proc = subprocess.Popen(
                    self.cmd, cwd=self.cwd, env=self.env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
            except OSError as e:
                raise ServiceError(f"{self.name} 無法啟動：{e}") from e

    def wait_ready(self, timeout: float, poll_s: float = 0.5) -> None:
        if self.reused:
            return
        deadline = time.monotonic() + timeout
        while True:
            if self.proc is not None and self.proc.poll() is not None:
                raise ServiceError(f"{self.name} 提前結束（結束碼 {self.proc.returncode}）\n{self._log_tail()}")
            health = probe(self.health_url)
            if health is not None and self.ready(health):
                return
            if time.monotonic() >= deadline:
                raise ServiceError(f"{self.name} 在 {timeout:g}s 內未就緒（log：{self.log_path}）")
            time.sleep(poll_s)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def send_signal(self, sig: int) -> None:
        try:
            os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _log_tail(self, lines: int = 10) -> str:
        try:
            return "\n".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""


def stop_all(services: List[Service], sigint_grace: float = config.STOP_SIGINT_GRACE_S,
             sigterm_grace: float = config.STOP_SIGTERM_GRACE_S) -> None:
    """結束我們啟動的服務：SIGINT → 逾時 SIGTERM → 逾時 SIGKILL（各服務同時送、同時等）；沿用的服務不動"""
    for sig, grace in ((signal.SIGINT, sigint_grace), (signal.SIGTERM, sigterm_grace), (signal.SIGKILL, 1.0)):
        alive = [s for s in services if s.alive()]
        if not alive:
            break
        for s in alive:
            s.send_signal(sig)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and any(s.alive() for s in alive):
            time.sleep(0.05)
    for s in services:
        if s.after_stop and s.proc is not None:
            s.after_stop()


# ---------- 實際的兩個服務 ----------
def llm_service() -> Service:
    return Service(
        name="LLM（mlx_vlm.server）",
        cmd=[sys.executable, "-m", "mlx_vlm.server", "--model", config.MODEL_ID,
             "--host", config.MLX_VLM_HOST, "--port", str(config.MLX_VLM_PORT),
             "--max-tokens", str(config.MLX_VLM_MAX_TOKENS)],
        health_url=f"{config.MLX_VLM_URL}/health",
        cwd=config.ROOT,
        log_path=config.LOG_DIR / "mlx_vlm.log",
        env={**os.environ, "APC_ENABLED": "1"},
    )


def _stt_ready(health: dict) -> bool:
    auth = health.get("auth")
    if auth == "denied":
        raise ServiceError("語音辨識授權被拒絕：請到 系統設定 → 隱私權與安全性 → 語音辨識 允許 MacSTT，再重新啟動")
    return health.get("status") == "ok" and auth == "authorized"


def kill_stt_backend(pid_file: str = config.STT_PID_FILE) -> None:
    """start.py 結束後，.app 內的後端若還活著就收掉（它不是 start.py 的子程序）。
    PID 檔可能是舊的，先確認該 PID 真的是 MacSTT.app 才動手"""
    try:
        pid = int(Path(pid_file).read_text().strip())
    except (OSError, ValueError):
        return

    def is_backend() -> bool:
        ps = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
        return "MacSTT.app" in ps.stdout

    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not is_backend():
            return
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and is_backend():
            time.sleep(0.05)


def stt_service() -> Service:
    if not config.STT_PYTHON.exists():
        raise ServiceError(f"找不到 macos-stt 的虛擬環境：{config.STT_PYTHON}（請先在 macos-stt/ 執行 uv sync）")
    return Service(
        name="STT（macos-stt）",
        cmd=[str(config.STT_PYTHON), str(config.STT_DIR / "start.py"), "serve",
             "--port", str(config.STT_PORT), "--lang", config.STT_LANG, "--vocab", str(config.STT_HINTS_PATH)],
        health_url=f"{config.STT_URL}/health",
        cwd=config.STT_DIR,
        log_path=config.LOG_DIR / "stt.log",
        ready=_stt_ready,
        after_stop=kill_stt_backend,
    )


# ---------- 桌面角色介面（Electron） ----------
def find_electron() -> Optional[Path]:
    """desktop/ 裡 npm 安裝好的 Electron 執行檔；沒裝回傳 None"""
    root = config.UI_DIR / "node_modules" / "electron"
    try:
        exe = root / "dist" / (root / "path.txt").read_text().strip()
    except OSError:
        return None
    return exe if exe.is_file() else None


def check_ui_ready() -> Optional[str]:
    """--ui 需要的檔案都在就回傳 None，否則回傳缺什麼與怎麼補"""
    if find_electron() is None:
        return f"找不到 Electron，請先安裝：cd {config.UI_DIR} && npm install"
    core = config.UI_DIR / "vendor" / "live2dcubismcore.min.js"
    if not core.is_file():
        return f"找不到 Live2D Cubism Core：{core}（到 Live2D 官網下載 Cubism SDK for Web，把 Core/live2dcubismcore.min.js 放到這裡）"
    if not config.UI_MODEL_PATH.is_file():
        return f"找不到 Live2D 模型：{config.UI_MODEL_PATH}"
    return None


def ui_service(bus_url: str, bus_health_url: str) -> Service:
    """Electron 桌面角色。連上事件匯流排（/health 的 clients ≥ 1）才算就緒"""
    return Service(
        name="UI（Electron）",
        cmd=[str(find_electron()), str(config.UI_DIR)],
        health_url=bus_health_url,
        cwd=config.UI_DIR,
        log_path=config.LOG_DIR / "ui.log",
        ready=lambda health: health.get("clients", 0) >= 1,
        # 從 VS Code 等 Electron 程式的終端機啟動時，環境裡會有 ELECTRON_RUN_AS_NODE，不移除的話 Electron 只會當 Node 執行
        env={**{k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"},
             "MAC_AI_UI_URL": bus_url, "MAC_AI_UI_MODEL": str(config.UI_MODEL_PATH),
             "MAC_AI_UI_LOG": str(config.LOG_DIR / "ui.jsonl")},
    )
