"""native（輔助使用 API）的純邏輯與 conftest 攔截確認"""
import re

import pytest

from mac_ai.router import native
# conftest 會把 native 的操作換掉；這裡在 collection 時先保存真實函式，測它們的邏輯（底層送事件／AX 呼叫一律換成假的）
REAL_POST_MEDIA_KEY = native.post_media_key
REAL_PRESS_BUTTON = native.press_button
REAL_PRESS_BUTTON_IN_WINDOW = native.press_button_in_window


@pytest.mark.parametrize("mods,mask", [
    (["command"], 0), (["command", "shift"], 1), (["command", "option"], 2), (["command", "control"], 4),
    (["command", "control", "shift"], 5), ([], 8), (["control"], 12),
])
def test_menu_mask(mods, mask):
    assert native.menu_mask(mods) == mask  # 與 AXMenuItemCmdModifiers 的定義一致


def test_menu_mask_matches_observed_values():
    """2026-11-15 唯讀探測實際讀到的值：VS Code 開新視窗 ⇧⌘N＝1、Safari 將全部縮到最小 ⌥⌘M＝2、Terminal 清除螢幕 ⌃⌘L＝4"""
    assert native.menu_mask(["command", "shift"]) == 1
    assert native.menu_mask(["command", "option"]) == 2
    assert native.menu_mask(["command", "control"]) == 4


@pytest.mark.parametrize("title,clean", [
    ("縮小 [⇧⌘ㄦ]", "縮小"), ("重設縮放 [⌘NumPad0]", "重設縮放"), ("命令選擇區…", "命令選擇區"), ("終端", "終端"), (None, ""), ("", ""),
])
def test_clean_title(title, clean):
    assert native._clean(title) == clean


def test_conftest_intercepts_native_in_offline_tests(native_calls):
    """離線測試裡 native 的操作一律被攔下（不會真的對 App 下指令）"""
    assert native.window_op(1, "close") == "ok" and native.press_menu(1, "S", ["command"]) == ("ok", "")
    assert native_calls == ["window close", "menu S command"]


def test_conftest_intercepts_new_native_functions(native_calls):
    """按鈕（指定視窗）與媒體鍵在離線測試裡被攔下並記錄，不會真的執行"""
    assert native.press_button_in_window(1, r"\(SHIFT\+n\)$") == "ok" and native.post_media_key("next") == "ok"
    assert native_calls == [r"button_in_window \(SHIFT\+n\)$", "mediakey next"]


@pytest.fixture
def fake_events(monkeypatch):
    """換掉建立與送出媒體鍵事件的函式：記錄 (code, down) 與送出次數；不載入 AppKit、不送任何事件"""
    class Fake:
        made, posted = [], []
    fake = Fake()
    fake.made, fake.posted = [], []
    monkeypatch.setattr(native, "_media_event", lambda code, down: fake.made.append((code, down)) or 1)
    monkeypatch.setattr(native, "_post_cg_event", lambda ev: fake.posted.append(ev))
    return fake


@pytest.mark.parametrize("key,code", [("play", 16), ("next", 17), ("previous", 18)])
def test_post_media_key_allows_whitelist(fake_events, key, code):
    """K6：白名單內的鍵各送一次按下＋放開"""
    assert REAL_POST_MEDIA_KEY(key) == "ok"
    assert fake_events.made == [(code, True), (code, False)] and len(fake_events.posted) == 2


@pytest.mark.parametrize("key", ["volume", "volume_up", "mute", "brightness", "PLAY", " play", "", None, 16, 17, 0, -1, 25, 3.0, b"play", ["play"]])
def test_post_media_key_rejects_others(fake_events, key):
    """K6：其他值（音量鍵、任意整數、None…）回 error，而且不建立、不送出任何事件"""
    assert REAL_POST_MEDIA_KEY(key) == "error"
    assert fake_events.made == [] and fake_events.posted == []


def test_post_media_key_event_creation_failure_posts_nothing(fake_events, monkeypatch):
    monkeypatch.setattr(native, "_media_event", lambda code, down: None)
    assert REAL_POST_MEDIA_KEY("play") == "error" and fake_events.posted == []


def test_post_media_key_whitelist_is_exactly_three():
    assert native._MEDIA_KEYS == {"play": 16, "next": 17, "previous": 18}


@pytest.mark.parametrize("label,hit", [
    ("下一個 (SHIFT+n)", True), ("Next (SHIFT+n)", True), ("前往下一頁", False), ("上一部 (SHIFT+p)", False),
])
def test_next_anchor_is_language_independent(label, hit):
    """K3：下一首按鈕的錨點 \\(SHIFT\\+n\\)$ 不依賴介面語言，也不誤中『前往下一頁』"""
    assert bool(re.search(r"\(SHIFT\+n\)$", label)) is hit


@pytest.fixture
def fake_ax(monkeypatch):
    """假的 AX 樹：tree 是 {元件: (role, 說明, 標題, [子元件])}；按下的元件記在 pressed，_app 收到的 pid 記在 pids"""
    class Fake:
        tree = {}
        pressed, pids = [], []
        window = "win"
    fake = Fake()
    fake.pressed, fake.pids = [], []
    attr = {"AXRole": 0, "AXDescription": 1, "AXTitle": 2, "AXChildren": 3}
    monkeypatch.setattr(native, "press_button", REAL_PRESS_BUTTON)  # conftest 換掉了，這裡要走真實流程
    monkeypatch.setattr(native, "_app", lambda pid: fake.pids.append(pid) or "app")
    monkeypatch.setattr(native, "_window", lambda app: fake.window)
    monkeypatch.setattr(native, "_get", lambda el, name: fake.tree[el][attr[name]])
    monkeypatch.setattr(native, "_free", lambda *els: None)
    monkeypatch.setattr(native, "_press", lambda el: fake.pressed.append(el) or True)
    return fake


def _tree(*buttons):
    tree = {"win": ("AXWindow", None, None, [f"b{i}" for i in range(len(buttons) + 1)])}
    tree["b0"] = ("AXStaticText", "下一個 (SHIFT+n)", None, [])  # 不是按鈕：不能被按
    for i, (role, desc, title) in enumerate(buttons, 1):
        tree[f"b{i}"] = (role, desc, title, [])
    return tree


def test_press_button_in_window_matches_with_search_on_description_or_title(fake_ax):
    fake_ax.tree = _tree(("AXButton", "前往下一頁", None), ("AXButton", None, "Next (SHIFT+n)"))
    assert REAL_PRESS_BUTTON_IN_WINDOW(777, r"\(SHIFT\+n\)$") == "ok"
    assert fake_ax.pids == [777] and fake_ax.pressed == ["b2"]  # 目標 pid 原樣傳入；AXTitle 備援；非 AXButton 與不符合的都跳過


def test_press_button_in_window_missing_and_error(fake_ax, monkeypatch):
    fake_ax.tree = _tree(("AXButton", "前往下一頁", None))
    assert REAL_PRESS_BUTTON_IN_WINDOW(1, r"\(SHIFT\+p\)$") == "missing" and fake_ax.pressed == []
    fake_ax.window = None  # 沒有視窗
    assert REAL_PRESS_BUTTON_IN_WINDOW(1, r"\(SHIFT\+n\)$") == "missing"
    fake_ax.window = "win"
    assert REAL_PRESS_BUTTON_IN_WINDOW(1, "(") == "error"  # 壞掉的 regex
    monkeypatch.setattr(native, "_press", lambda el: False)
    fake_ax.tree = _tree(("AXButton", "下一個 (SHIFT+n)", None))
    assert REAL_PRESS_BUTTON_IN_WINDOW(1, r"\(SHIFT\+n\)$") == "error"
