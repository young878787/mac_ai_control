"""本機工具的登錄：載入時把各功能模組的本機工具放進 actions.LOCAL_TOOLS。
這些工具不在 LLM 的 TOOLS_SCHEMA 裡，所以 LLM 叫不到它們；會呼叫 run_tool 的 pipeline、slow_path 都要在頂層 import 本模組。"""
from mac_ai.router.actions import LOCAL_TOOLS
from mac_ai.router.answer_tools import get_weather, open_source, remember_home, web_answer
from mac_ai.router.find_tools import federated_search, find_file, open_folder, pick_choice
from mac_ai.router.front_tools import context_search, front_action
from mac_ai.router.music_tools import music_feedback, music_replace, music_top, play_media

LOCAL_TOOLS.update({
    "open_folder": open_folder, "find_file": find_file, "pick_choice": pick_choice,
    "context_search": context_search, "front_action": front_action, "play_media": play_media,
    "music_replace": music_replace, "music_feedback": music_feedback, "music_top": music_top, "get_weather": get_weather,
    "remember_home": remember_home, "web_answer": web_answer, "open_source": open_source, "federated_search": federated_search,
})
