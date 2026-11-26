"""實際執行時的 import 範圍：在乾淨的子程序裡 import，檢查 sys.modules。
import-linter 只看靜態依賴、不把父套件的 __init__ 算進去，所以熱路徑「執行時載入了什麼」要在這裡守（ADR 0029）"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

HEAVY = (
    "mac_ai.router.llm_client", "mac_ai.router.cloud_client", "mac_ai.router.search_client",
    "mac_ai.router.weather_client", "mac_ai.router.youtube_client",
    "mac_ai.router.actions", "mac_ai.router.local_actions", "mac_ai.router.pipeline", "mac_ai.router.slow_path",
    "mac_ai.db", "sqlite3", "subprocess",
)
LOCAL_TOOL_NAMES = {
    "open_folder", "find_file", "pick_choice", "context_search", "front_action", "play_media", "music_replace",
    "music_feedback", "music_top", "get_weather", "remember_home", "web_answer", "open_source", "federated_search",
}


def _run_python(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _loaded_after(module: str, forbidden) -> list:
    code = (
        "import sys\n"
        "before = set(sys.modules)\n"  # 直譯器啟動時就載入的不算
        f"import {module}\n"
        f"print(','.join(sorted(m for m in {tuple(forbidden)!r} if m in sys.modules and m not in before)))\n"
    )
    out = _run_python(code)
    return out.split(",") if out else []


@pytest.mark.parametrize("module", ["mac_ai.router.lexicon", "mac_ai.wake"])
def test_pure_modules_do_not_load_clients_or_executors(module):
    assert _loaded_after(module, HEAVY) == []


@pytest.mark.parametrize("module", ["mac_ai.router.fast_path", "mac_ai.stt.completeness"])
def test_hot_path_only_loads_subprocess_via_catalog(module):
    """subprocess 是例外：catalog 要用 proc._run 跑 mdfind（.importlinter C4 的 catalog → proc）"""
    assert _loaded_after(module, [m for m in HEAVY if m != "subprocess"]) == []


@pytest.mark.parametrize("entry", ["mac_ai.router.pipeline", "mac_ai.router.slow_path"])
def test_importing_executor_entry_registers_every_local_tool(entry):
    """LOCAL_TOOLS 靠 local_actions 載入時登錄；會呼叫 run_tool 的入口模組單獨 import 時也要帶到它"""
    code = (
        f"import {entry}\n"
        "from mac_ai.router.actions import LOCAL_TOOLS\n"
        "print(','.join(sorted(LOCAL_TOOLS)))\n"
    )
    assert set(_run_python(code).split(",")) == LOCAL_TOOL_NAMES
