"""語音指令路由器：正規化 → Fast Path／Slow Path（LLM）→ 執行 macOS 動作。"""
from mac_ai.router.actions import (
    ExecContext, adjust_screen_brightness, control_mac_app, open_project, read_screen,
    set_system_volume, system_power_action, web_search_or_open_url,
)
from mac_ai.router.fast_path import FastPathMatcher, canonicalize_clause
from mac_ai.router.lexicon import (
    APP_KIND, APP_MISHEARD, LEXICON, MAC_APP_ALIASES, S2T, TERM_MISHEARD, build_hint_lines, load_lexicon,
    project_index,
)
from mac_ai.router.llm_client import LLMUnavailable, reset_llm_breaker
from mac_ai.router.normalize import normalize_utterance, to_traditional, zh_to_int
from mac_ai.router.pipeline import process_voice_command
from mac_ai.router.slow_path import execute_slow_path_llm, plan_slow_path, warmup_llm
from mac_ai.router.tools import TOOLS_SCHEMA, validate_tool_call
