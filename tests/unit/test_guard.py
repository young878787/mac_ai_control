"""G1 守門的判斷（intent_guard）與『否定即指令』的 Fast Path 窄規則：純函式"""
import pytest

import mac_ai.router as r
from mac_ai.router import slow_path
from mac_ai.router.fast_path import FastPathMatcher


# ---------- 判斷 ----------
@pytest.mark.parametrize("text,guard", [
    ("不要鎖定螢幕", "no_action"), ("我不想鎖定螢幕", "no_action"), ("別讓電腦睡眠", "no_action"),
    ("剛剛鎖定螢幕了", "no_action"), ("我已經打開小算盤了", "no_action"), ("要不要鎖定螢幕", "no_action"),
    ("不要鎖定螢幕，幫我想個名字", "no_action"),          # 混合：任一子句是否定就整步只回話
    ("不是，不要鎖定螢幕", "no_action"),
    ("鎖定螢幕是什麼意思", "info_question"), ("怎麼開深色模式", "info_question"), ("台北天氣怎麼樣", "info_question"),
    ("可以幫我開小算盤嗎", None), ("今天台北會下雨嗎", None),  # 是非問句／客氣請求：不擋
    ("螢幕太亮了，Python 是什麼", None),                       # 不是每個子句都是資訊提問
    ("沒有我說的是這網頁", None), ("沒有，我說的是這個畫面", None), ("不是啦我是說把音量調到30", None),  # 糾正句
    ("今天心情不錯", None),
])
def test_intent_guard(text, guard):
    assert slow_path.intent_guard(r.normalize_utterance(text)) == guard


# ---------- 否定即指令：Fast Path 窄規則 ----------
@pytest.mark.parametrize("text,action", [
    ("不要靜音了", ("set_system_volume", {"mute": False})),
    ("別再靜音了", ("set_system_volume", {"mute": False})),
    ("螢幕不要那麼亮", ("adjust_screen_brightness", {"direction": "down", "steps": 3})),
    ("不要這麼暗", ("adjust_screen_brightness", {"direction": "up", "steps": 3})),
    ("把螢幕別那麼亮了", ("adjust_screen_brightness", {"direction": "down", "steps": 3})),
    ("不要深色模式了", ("system_power_action", {"action": "dark_mode_off"})),
    ("我不想用深色模式", ("system_power_action", {"action": "dark_mode_off"})),
    ("我不要深色模式", ("system_power_action", {"action": "dark_mode_off"})),
    ("幫我不要深色模式了", ("system_power_action", {"action": "dark_mode_off"})),
])
def test_negated_command_rules(text, action):
    m = FastPathMatcher().match(r.normalize_utterance(text))
    assert m is not None and m.actions == [action]


@pytest.mark.parametrize("text", [
    "不要靜音", "不要打開深色模式", "不要開深色模式", "不要鎖定螢幕", "別讓電腦睡眠", "不要調暗", "螢幕不要",
    "不要那麼亮的螢幕", "不想要深色模式的網站",
])
def test_true_negations_are_not_negated_commands(text):
    assert FastPathMatcher().match(r.normalize_utterance(text)) is None
