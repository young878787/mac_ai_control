"""TOOLS_SCHEMA 與 validate_tool_call（唯一的信任邊界）：未知工具／參數、型別、enum、專案白名單、LLM 看不到的內部工具"""
import hashlib
import json

import pytest

import mac_ai.router as r
from mac_ai.router import slow_path, tools


# ---------- 工具參數驗證 ----------
@pytest.mark.parametrize("name,args,ok", [
    ("control_mac_app", {"app_name": "Notes", "action": "open"}, True),
    ("control_mac_app", {"app_name": "Notes", "action": "delete"}, False),   # enum
    ("control_mac_app", {"app_name": "Notes"}, False),                        # 缺必填
    ("control_mac_app", {"app_name": "Notes", "action": "open", "x": 1}, False),  # 未定義參數
    ("set_system_volume", {"level": "50"}, True),                             # 字串數字可轉 int
    ("set_system_volume", {"level": "很大"}, False),
    ("set_system_volume", {"level": True}, False),
    ("set_system_volume", {"mute": "true"}, False),                           # 非布林
    ("adjust_screen_brightness", {"direction": "down", "steps": 3}, True),
    ("adjust_screen_brightness", {"direction": "left", "steps": 3}, False),
    ("web_search_or_open_url", {"query": "  "}, False),
    ("rm_rf", {}, False),                                                     # 未知工具
    ("system_power_action", ["sleep"], False),                                # 參數非物件
])
def test_validate_tool_call(name, args, ok):
    clean, err = r.validate_tool_call(name, args)
    assert (err is None) == ok and (clean is not None) == ok


def test_open_project_schema_does_not_lure_llm_into_opening_folders():
    """實測：2B LLM 看到『專案資料夾』，把『開資料夾』誤判成開 VS Code 專案；
    範例 'mac_ai_control' 不能拿掉（拿掉後『語音助手的專案』會開成清單第一個專案），但要限定為『使用者說的』"""
    fn = next(t["function"] for t in r.TOOLS_SCHEMA if t["function"]["name"] == "open_project")
    project_name = fn["parameters"]["properties"]["project_name"]["description"]
    assert "資料夾" not in fn["description"] + project_name
    assert "明確說『專案』" in fn["description"]
    assert project_name.startswith("使用者說的")


def test_validate_mute_drops_level():
    clean, _ = r.validate_tool_call("set_system_volume", {"level": 30, "mute": True})
    assert clean == {"mute": True}
    clean, _ = r.validate_tool_call("set_system_volume", {"level": 30, "mute": False})
    assert clean == {"level": 30, "mute": False}


def test_new_tools_in_schema():
    names = [t["function"]["name"] for t in r.TOOLS_SCHEMA]
    assert names[-3:] == ["open_project", "read_screen", "chat_reply"] and len(names) == 8
    enum = tools._SCHEMA_BY_NAME["system_power_action"]["properties"]["action"]["enum"]
    assert {"dark_mode_on", "dark_mode_off", "toggle_dark_mode"} <= set(enum)
    assert r.validate_tool_call("read_screen", {}) == ({}, None)
    assert r.validate_tool_call("chat_reply", {}) == ({}, None)


def test_validate_open_project_whitelist(projects):
    assert r.validate_tool_call("open_project", {"project_name": "/etc"})[1]
    assert r.validate_tool_call("open_project", {"project_name": "../../etc"})[1]
    assert r.validate_tool_call("open_project", {"project_name": "mac_ai_control", "editor": "Chrome"})[1]
    clean, err = r.validate_tool_call("open_project", {"project_name": "語音助手", "editor": "cursor"})
    assert err is None and clean == {"project_name": "mac_ai_control", "editor": "Cursor"}
    assert r.validate_tool_call("open_project", {})[1]


@pytest.mark.parametrize("name,args", [
    ("find_file", {"name": "x"}), ("open_folder", {"path_key": "Home"}), ("pick_choice", {"n": 1}),
    ("browser_tab", {"action": "new"}),
])
def test_internal_tools_are_not_visible_to_llm(name, args):
    """本機工具（Fast Path 專用）不在 TOOLS_SCHEMA，LLM 硬呼叫也當未知工具拒絕。
    實測 browser_tab 放進清單，『開深色模式』會被 4bit 模型誤判成 toggle_dark_mode"""
    assert name not in {t["function"]["name"] for t in r.TOOLS_SCHEMA}
    assert r.validate_tool_call(name, args)[1] == f"未知工具：{name}"


# ---------- 快取前綴（紅線 2，ADR 0002） ----------
# 有意改 SYSTEM_PROMPT 或 TOOLS_SCHEMA 時：先照紅線 2 跑 pytest -m live 比對準確率與延遲，再更新這裡的 hash；
# 字數上限是 2026-11-27 的實際值，只能往下調（「不增大」），放寬先問使用者
PREFIX_SHA256 = "68f233086ff99f46abcd79f047d527b1408c0affa7c89b9a05d59661c666b11e"
SYSTEM_PROMPT_MAX_CHARS = 155
TOOLS_SCHEMA_MAX_CHARS = 2462


def test_prompt_prefix_is_byte_stable():
    """送給本機 LLM 的前綴（system＋tools，序列化方式同 llm_client 的 json.dumps）逐字不變"""
    prefix = (slow_path.SYSTEM_PROMPT + json.dumps(tools.TOOLS_SCHEMA)).encode()
    assert hashlib.sha256(prefix).hexdigest() == PREFIX_SHA256


def test_prompt_prefix_does_not_grow():
    assert len(slow_path.SYSTEM_PROMPT) <= SYSTEM_PROMPT_MAX_CHARS
    assert len(json.dumps(tools.TOOLS_SCHEMA, ensure_ascii=False)) <= TOOLS_SCHEMA_MAX_CHARS
