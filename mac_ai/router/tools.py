"""LLM 工具定義（OpenAI function calling schema）與呼叫參數驗證。"""
from typing import Any, Dict, Optional, Tuple

from mac_ai.router.lexicon import APP_KIND, resolve_app, resolve_project


def _fn(name: str, description: str, properties: dict, required: tuple = ()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)},
        },
    }


TOOLS_SCHEMA = [
    _fn(
        "control_mac_app",
        "在 macOS 上打開或關閉指定的應用程式。",
        {
            "app_name": {
                "type": "string",
                "description": "macOS 應用程式標準英文名稱，如 'Google Chrome', 'Safari', 'Terminal', 'Visual Studio Code', 'Cursor', 'Calculator', 'Notes', 'Finder', 'Spotify'",
            },
            "action": {"type": "string", "enum": ["open", "quit"]},
        },
        ("app_name", "action"),
    ),
    _fn(
        "set_system_volume",
        "調整 macOS 系統音量（0-100）或切換靜音。",
        {
            "level": {"type": "integer", "description": "音量百分比 0-100"},
            "mute": {"type": "boolean", "description": "是否靜音"},
        },
    ),
    _fn(
        "adjust_screen_brightness",
        "調亮或調暗 macOS 螢幕亮度。",
        {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "steps": {"type": "integer", "description": "調整格數 1-16，『一點』約為 3，『最亮/最暗』為 16"},
        },
        ("direction", "steps"),
    ),
    _fn(
        "web_search_or_open_url",
        "使用瀏覽器進行 Google 搜尋（如查天氣、查新聞）或打開特定網站。",
        {"query": {"type": "string", "description": "搜尋關鍵字或網址"}},
        ("query",),
    ),
    _fn(
        "system_power_action",
        "macOS 系統控制：睡眠、鎖定螢幕、深色模式。打開深色模式用 dark_mode_on，關閉用 dark_mode_off。",
        {"action": {
            "type": "string",
            "enum": ["dark_mode_on", "dark_mode_off", "lock_screen", "sleep", "toggle_dark_mode"],
            "description": "toggle_dark_mode 只在使用者明說『切換』時使用",
        }},
        ("action",),
    ),
    _fn(
        "open_project",
        "用編輯器開啟一個已知的程式專案。只有使用者明確說『專案』時才呼叫。",
        {
            "project_name": {"type": "string", "description": "使用者說的專案名稱，如 'mac_ai_control'"},
            "editor": {"type": "string", "description": "編輯器標準英文名稱，如 'Visual Studio Code', 'Cursor'；使用者沒指定時不要填"},
        },
        ("project_name",),
    ),
    _fn(
        "read_screen",
        "看使用者的螢幕畫面並說明內容。使用者問『畫面／螢幕／這頁／網頁上有什麼、寫什麼、在講什麼』時呼叫。不需要參數。",
        {},
    ),
    _fn(
        "chat_reply",
        "使用者在提問、閒聊或需要文字回答時呼叫（如『Python 是什麼』『今天心情不錯』）。不需要參數。",
        {},
    ),
]

_SCHEMA_BY_NAME = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS_SCHEMA}


def validate_tool_call(name: str, args: Any) -> Tuple[Optional[dict], Optional[str]]:
    """檢查 LLM 回傳的工具名稱與參數，回傳 (乾淨參數, None) 或 (None, 錯誤原因)。
    只信任 schema：未知工具、未知參數、缺必填、型別錯誤、enum 不符一律拒絕，不交給 executor。"""
    schema = _SCHEMA_BY_NAME.get(name)
    if schema is None:
        return None, f"未知工具：{name}"
    if not isinstance(args, dict):
        return None, f"{name} 參數不是物件"
    props = schema["properties"]
    clean: Dict[str, Any] = {}
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            return None, f"{name} 出現未定義參數：{key}"
        if spec["type"] == "integer":
            if isinstance(value, bool):
                return None, f"{name}.{key} 應為整數"
            try:
                value = int(value)
            except (TypeError, ValueError):
                return None, f"{name}.{key} 應為整數"
        elif spec["type"] == "boolean":
            if not isinstance(value, bool):
                return None, f"{name}.{key} 應為布林"
        elif spec["type"] == "string":
            if not isinstance(value, str) or not value.strip():
                return None, f"{name}.{key} 應為非空字串"
        if "enum" in spec and value not in spec["enum"]:
            return None, f"{name}.{key} 不在允許值 {spec['enum']}"
        clean[key] = value
    missing = [k for k in schema["required"] if k not in clean]
    if missing:
        return None, f"{name} 缺少必填參數：{missing}"
    # 實測：LLM 在 mute=true 時偶爾多塞 level（如 30），靜音語意以 mute 為準
    if name == "set_system_volume" and clean.get("mute") is True:
        clean.pop("level", None)
    # 專案只能從白名單解析，不接受 LLM 給的任意名稱／路徑；editor 必須是編輯器類 App
    if name == "open_project":
        hit = resolve_project(clean["project_name"])
        if hit is None:
            return None, f"open_project 找不到專案：{clean['project_name']}"
        clean["project_name"] = hit[0][0]
        if "editor" in clean:
            editor = resolve_app(clean["editor"])
            if editor is None or APP_KIND.get(editor[0]) != "editor":
                return None, f"open_project.editor 不是已知編輯器：{clean['editor']}"
            clean["editor"] = editor[0]
    return clean, None
