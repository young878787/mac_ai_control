"""語音指令路由器：正規化 → Fast Path／Slow Path（LLM）→ 執行 macOS 動作。

對外名稱用到才 import（PEP 562）：Python 載入任何 router 子模組前都會先執行本檔，在這裡一次 import 全部，
會讓 lexicon、wake、stt.completeness 這些熱路徑模組跟著載入整個執行器與網路 client（tests/runtime/test_import_footprint.py）。
新增對外名稱要加進 _EXPORTS。"""
import importlib
import importlib.util

_EXPORTS = {
    "browser_tools": ("browser_tab", "web_search_or_open_url"),
    "exec_context": ("ExecContext",),
    "fast_path": ("FastPathMatcher", "canonicalize_clause"),
    "lexicon": (
        "APP_KIND", "APP_MISHEARD", "LEXICON", "MAC_APP_ALIASES", "S2T", "TERM_MISHEARD", "build_hint_lines",
        "load_lexicon", "project_index",
    ),
    "llm_client": ("LLMUnavailable", "reset_llm_breaker"),
    "normalize": ("normalize_utterance", "to_traditional", "zh_to_int"),
    "pipeline": ("process_choice", "process_voice_command"),
    "screen_tools": ("read_screen",),
    "slow_path": ("execute_slow_path_llm", "plan_slow_path", "warmup_llm"),
    "system_tools": (
        "adjust_screen_brightness", "control_mac_app", "open_project", "set_system_volume", "system_power_action",
    ),
    "tools": ("TOOLS_SCHEMA", "validate_tool_call"),
}
_SOURCE = {name: module for module, names in _EXPORTS.items() for name in names}


def __getattr__(name: str):
    """不快取：每次回到來源模組取值，測試 patch 來源模組時這裡看到的是同一份"""
    if name in _SOURCE:
        return getattr(importlib.import_module(f"{__name__}.{_SOURCE[name]}"), name)
    if not name.startswith("_") and importlib.util.find_spec(f"{__name__}.{name}") is not None:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
