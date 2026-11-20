"""桌面前端（desktop/shared/contract.js）與後端的合約一致：後端是來源，前端的鏡像不一致就失敗。"""
import ast
import inspect
import re

from mac_ai import config, ui_bus
from mac_ai.router import pipeline

CONTRACT = (config.ROOT / "desktop" / "shared" / "contract.js").read_text(encoding="utf-8")


def _js_array(name: str) -> list:
    m = re.search(rf"export const {name} = Object\.freeze\(\[([^\]]*)\]\)", CONTRACT)
    assert m, f"contract.js 找不到陣列 {name}"
    return re.findall(r"'([^']*)'", m.group(1))


def _js_int(name: str) -> int:
    m = re.search(rf"export const {name} = (\d+);", CONTRACT)
    assert m, f"contract.js 找不到數字 {name}"
    return int(m.group(1))


def test_message_types_match_ui_bus():
    assert _js_array("UI_MESSAGE_TYPES") == list(ui_bus.UI_MESSAGE_TYPES)


def test_command_limit_matches_config():
    assert _js_int("COMMAND_MAX_CHARS") == config.UI_COMMAND_MAX_CHARS


def test_choices_limit_matches_config():
    assert _js_int("CHOICES_MAX") == config.FIND_RESULTS_MAX


def test_expand_reasons_match_pipeline():
    tree = ast.parse(inspect.getsource(pipeline.expand_reason).lstrip())
    returned = {n.value.value for n in ast.walk(tree)
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    assert returned, "expand_reason 沒有回傳字串常數，測試失效"
    assert set(_js_array("EXPAND_REASONS")) == returned


def test_html_input_limits_match_contract():
    """畫面的輸入框 maxlength 是靜態屬性，不能 import 常數，只能在這裡比對"""
    limit = _js_int("COMMAND_MAX_CHARS")
    htmls = [p for p in (config.ROOT / "desktop" / "renderer").rglob("*.html") if re.search(r"maxlength=", p.read_text(encoding="utf-8"))]
    assert htmls, "找不到帶 maxlength 的 html，測試失效"
    for p in htmls:
        assert set(re.findall(r'maxlength="(\d+)"', p.read_text(encoding="utf-8"))) == {str(limit)}, p.name
