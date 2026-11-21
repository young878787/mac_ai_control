"""只編譯、不執行：本專案送出的 AppleScript／JXA 都要能被 osacompile 接受（沒有 osacompile 或沒裝該 App 就略過）"""
import os
import subprocess
import sys
import tempfile

import pytest

import mac_ai.router as r
from mac_ai.router import actions, local_actions
from support.real import real_run


# ---------- AppleScript 語法（只編譯、不執行）----------
@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osacompile")
def test_applescripts_compile(tmp_path, sh):
    r.control_mac_app("Notes", "quit")
    r.set_system_volume(level=30, mute=False)
    r.adjust_screen_brightness("up", 2)
    r.system_power_action("toggle_dark_mode")
    r.system_power_action("lock_screen")
    scripts = [c[2] for c in sh if c[0] == "osascript"]
    assert len(scripts) == 6
    for i, script in enumerate(scripts):
        p = real_run(["osacompile", "-e", script, "-o", str(tmp_path / f"{i}.scpt")],
                           capture_output=True, text=True, timeout=15)
        assert p.returncode == 0, f"{script!r}: {p.stderr}"


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osacompile")
def test_dark_mode_scripts_compile(tmp_path, sh):
    """新增的深色模式開／關腳本只編譯、不執行"""
    r.system_power_action("dark_mode_on")
    r.system_power_action("dark_mode_off")
    assert [c[2].rsplit(" ", 1)[1] for c in sh] == ["true", "false"]
    for i, cmd in enumerate(sh):
        p = real_run(["osacompile", "-e", cmd[2], "-o", str(tmp_path / f"{i}.scpt")], capture_output=True, text=True, timeout=15)
        assert p.returncode == 0, p.stderr


# ---------- AppleScript 語法（只在有該 App 的 Mac 上驗；沒裝就略過）----------
def _app_installed(name):
    return subprocess.run(["osascript", "-e", f'id of application "{name}"'], capture_output=True).returncode == 0


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osacompile")
@pytest.mark.parametrize("browser", ["Safari", "Google Chrome"])
@pytest.mark.parametrize("action", ["new", "close", "reload", "next", "previous"])
def test_tab_scripts_compile(browser, action):
    if not _app_installed(browser):
        pytest.skip(f"沒有安裝 {browser}，無法編譯它的 AppleScript")
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(["osacompile", "-e", actions._TAB_SCRIPTS[browser][action], "-o", os.path.join(tmp, "x.scpt")],
                           capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osacompile")
def test_front_and_running_script_compiles():
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(["osacompile", "-e", actions._FRONT_AND_RUNNING_SCRIPT, "-o", os.path.join(tmp, "x.scpt")],
                           capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osacompile")
def test_safari_tabs_script_compiles():
    """背景音樂控制讀 Safari 目前分頁與所有分頁網址的腳本：只編譯、不執行（執行會對 Safari 下指令）"""
    if not _app_installed("Safari"):
        pytest.skip("沒有安裝 Safari，無法編譯它的 AppleScript")
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(["osacompile", "-e", local_actions._SAFARI_TABS_SCRIPT, "-o", os.path.join(tmp, "x.scpt")],
                           capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osacompile")
@pytest.mark.parametrize("browser", ["Safari", "Google Chrome"])
def test_music_tab_scripts_compile(browser):
    """點歌的『找音樂分頁並改網址』腳本（actions._MUSIC_TAB_SCRIPTS）：只編譯、不執行（執行會對瀏覽器下指令）"""
    if not _app_installed(browser):
        pytest.skip(f"沒有安裝 {browser}，無法編譯它的 AppleScript")
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(["osacompile", "-e", actions._MUSIC_TAB_SCRIPTS[browser], "-o", os.path.join(tmp, "x.scpt")],
                           capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osascript／CoreGraphics")
def test_front_browser_window_script_runs_on_this_mac():
    """真的執行 JXA（唯讀）：語法與 CoreGraphics 呼叫要正確；有瀏覽器視窗時回傳數字編號，沒有就是 None"""
    res = actions._front_browser_window(None)
    assert res is None or (res[0].isdigit() and res[1] in actions.TAB_BROWSERS)
    ok, _ = actions._run(["osascript", "-l", "JavaScript", "-e", actions._FRONT_BROWSER_WINDOW_SCRIPT.replace("__OWNERS__", "[]")])
    assert ok
