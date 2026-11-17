"""native（輔助使用 API）的純邏輯與 conftest 攔截確認"""
import pytest

from mac_ai.router import native


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
