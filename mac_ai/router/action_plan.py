"""範圍判斷的結果（Intent）→ 工具呼叫的對應表。純資料轉換：只讀 Intent.slots，不看原句、不做 IO、不執行。
比對階段（fast_path）使用；工具的實作在各 *_tools 模組，由 actions 分派。"""
from typing import Callable, Dict, List, Tuple

from mac_ai.router.domain import Intent


# ---------- 對應表：(domain, 動詞) → 工具呼叫 ----------
def _plan_app(i: Intent) -> List[Tuple[str, dict]]:
    return [("control_mac_app", {"app_name": i.slots["app"], "action": "open"})]


def _plan_project(i: Intent) -> List[Tuple[str, dict]]:
    return [("open_project", {"project_name": i.slots["project_name"]})]


def _plan_folder(i: Intent) -> List[Tuple[str, dict]]:
    return [("open_folder", {"path_key": i.slots["path_key"]})]


def _plan_web(i: Intent) -> List[Tuple[str, dict]]:
    return [("web_search_or_open_url", {"query": i.slots["query"]})]


def _plan_file(action: str) -> Callable[[Intent], List[Tuple[str, dict]]]:
    def plan(i: Intent) -> List[Tuple[str, dict]]:
        args = {k: v for k, v in i.slots.items() if v}
        args["action"] = action
        return [("find_file", args)]
    return plan


ACTION_TABLE: Dict[Tuple[str, str], Callable[[Intent], List[Tuple[str, dict]]]] = {}
for _verb in ("find", "open", "search"):
    ACTION_TABLE[("app", _verb)] = _plan_app
    ACTION_TABLE[("project", _verb)] = _plan_project
    ACTION_TABLE[("folder", _verb)] = _plan_folder
    ACTION_TABLE[("web", _verb)] = _plan_web
    ACTION_TABLE[("file", _verb)] = _plan_file("open" if _verb == "open" else "reveal")  # 只有明說『打開』才開檔


def plan_actions(intent: Intent) -> List[Tuple[str, dict]]:
    return ACTION_TABLE[(intent.domain, intent.verb)](intent)
