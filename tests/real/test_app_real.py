"""真實 App 驗收（計劃書 §14，取代人工驗收 U1、U4）：在 Safari、Finder、Terminal、VS Code 上跑真的流程，讀回 App 狀態判定。

跑法：pytest -m app -v（會佔用螢幕約 2–4 分鐘，跑的時候不要動鍵盤滑鼠）。
- 每個測試只操作自己開的視窗，結束時關掉；不動使用者原本的視窗與檔案。
- 指令一律直接餵文字（不測 STT）；只走 Fast Path，不需要 LLM。
- 缺授權（自動化、輔助使用、螢幕錄製）時 skip 並寫明原因，不會卡住。
- 設環境變數 ACCEPTANCE_METRICS=<檔案> 時，每句指令的結果與耗時會寫成 JSON Lines（scripts/acceptance.py 用）。"""
import functools
import http.server
import json
import os
import signal
import threading
import uuid
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

import mac_ai.router as r
from mac_ai import config
from mac_ai.router import context, proc

pytestmark = pytest.mark.app

if sys.platform != "darwin":
    pytest.skip("只能在 macOS 上執行", allow_module_level=True)

VSCODE = "Visual Studio Code"
_PERMISSION_HINTS = ("-1743", "-25211", "not allowed", "不允許", "輔助使用", "assistive")


@pytest.fixture(scope="module", autouse=True)
def keep_display_awake():
    """測試期間不讓螢幕關閉／自動鎖定：底層操作（輔助使用介面）不算使用者活動，不會重設閒置計時。
    2026-11-15 實測：使用者閒置 4.7 分鐘後開跑，16 秒後螢幕依『5 分鐘關閉顯示器』設定鎖定，之後全部失敗"""
    proc = subprocess.Popen(["caffeinate", "-d", "-i", "-u", "-w", str(os.getpid())])
    if front_app_raw() == "loginwindow":
        proc.terminate()
        pytest.skip("螢幕目前是鎖定狀態，請解鎖後再跑")
    yield
    proc.terminate()


def front_app_raw() -> str:
    out = subprocess.run(["sh", "-c", 'lsappinfo info -only name "$(lsappinfo front)"'], capture_output=True, text=True).stdout
    return out.split('"')[1] if '"' in out else ""


# ---------- 共用工具 ----------
def osa(script: str, timeout: float = 8.0, lang: str = "") -> str:
    """執行 AppleScript（lang="JavaScript" 為 JXA）。逾時或缺授權 → skip；其他錯誤 → 測試失敗"""
    cmd = ["osascript"] + (["-l", lang] if lang else []) + ["-e", script]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.skip(f"osascript 逾時 {timeout:g}s（可能在等授權對話框，請按允許後重跑）：{script[:60]}")
    if p.returncode != 0:
        err = p.stderr.strip()
        if any(h in err for h in _PERMISSION_HINTS):
            pytest.skip(f"缺少授權（系統設定 → 隱私權與安全性）：{err[-160:]}")
        raise AssertionError(f"osascript 失敗：{err}\n{script}")
    return p.stdout.strip()


def wait_for(cond, timeout: float = 6.0, interval: float = 0.2, what: str = ""):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = cond()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"等了 {timeout:g}s 仍不成立：{what}（最後一次：{last!r}）")


def front_app() -> str:
    fc = context.read_front(proc._run)
    return fc.app if fc else ""


def activate(app: str) -> None:
    osa(f'tell application "{app}" to activate')
    wait_for(lambda: front_app() == app, what=f"{app} 成為最前面的 App")
    time.sleep(0.3)


@pytest.fixture
def say(request):
    """跑一句指令（真的 process_voice_command），並把結果寫進 ACCEPTANCE_METRICS"""
    def run(text: str) -> dict:
        out = r.process_voice_command(text)
        path = os.environ.get("ACCEPTANCE_METRICS")
        if path:
            steps = [{"kind": s["kind"], "status": s["status"], "ms": s["ms"], "context": s.get("context"),
                      "log": s["logs"][-1] if s["logs"] else ""} for s in out["steps"]]
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"test": request.node.nodeid, "text": text, "total_ms": out["total_ms"],
                                    "steps": steps}, ensure_ascii=False) + "\n")
        return out
    return run


def only_step(out: dict) -> dict:
    assert len(out["steps"]) == 1, out["steps"]
    return out["steps"][0]


def ok_step(out: dict) -> dict:
    step = only_step(out)
    assert step["kind"] == "fast" and step["status"] == "ok", step
    return step


# ---------- 截圖比對（效果讀不到的功能用，D8）----------
_WINDOW_SCRIPT = (
    'ObjC.import("CoreGraphics");'
    "const wins = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly, 0)));"
    'const w = wins.find(w => w.kCGWindowLayer === 0 && w.kCGWindowOwnerName === "__OWNER__"'
    " && w.kCGWindowBounds.Width > 300 && w.kCGWindowBounds.Height > 200);"
    'w ? [w.kCGWindowNumber, (w.kCGWindowName || "")].join("\\t") : ""'
)


def window_number(owner: str, title_part: str = "") -> str:
    def find():
        out = osa(_WINDOW_SCRIPT.replace("__OWNER__", owner), lang="JavaScript")
        num, _, title = out.partition("\t")
        return num if num.isdigit() and title_part in title else ""
    return wait_for(find, what=f"{owner} 最前面的視窗{f'（標題含 {title_part}）' if title_part else ''}")


def shot(num: str, path: Path):
    from PIL import Image
    if path.exists():
        path.unlink()
    p = subprocess.run(["screencapture", "-x", "-o", "-l", num, str(path)], capture_output=True, text=True, timeout=10)
    if p.returncode != 0 or not path.exists():
        pytest.skip(f"截圖失敗（請到 系統設定 → 隱私權與安全性 → 螢幕錄製 允許執行測試的 App）：{p.stderr.strip()}")
    img = Image.open(path).convert("L")
    if img.getextrema() == (0, 0):
        pytest.skip("截圖全黑：沒有螢幕錄製權限")
    return img


def changed(a, b) -> float:
    """兩張截圖有變化的像素比例（灰階差超過 24 才算；尺寸不同視為全變）"""
    from PIL import ImageChops
    if a.size != b.size:
        return 1.0
    hist = ImageChops.difference(a, b).histogram()
    return sum(hist[25:]) / (a.size[0] * a.size[1])


CHANGED_MIN = 0.02  # 功能有作用：至少 2% 像素改變
RESTORED_MAX = 0.01  # 再切一次回到原樣：最多 1% 像素不同（游標、時鐘等雜訊）


# ---------- Safari ----------
def safari_ids() -> set:
    out = osa('tell application "Safari" to get id of every window')
    return {x.strip() for x in out.split(",") if x.strip()}


def safari_url() -> str:
    return osa('tell application "Safari" to return URL of current tab of front window')


@pytest.fixture
def safari():
    """開一個測試專用的 Safari 視窗（open(url) 會載入網址並帶到最前面）；結束時關掉這次新開的所有 Safari 視窗"""
    before = safari_ids()

    def open_(url: str, wait_prefix: str = ""):
        osa(f'tell application "Safari" to make new document with properties {{URL:"{url}"}}')
        activate("Safari")
        wait_for(lambda: safari_url().startswith(wait_prefix or url), timeout=15, what=f"Safari 載入 {url}")
        time.sleep(1.0)
    yield open_
    osa('tell application "System Events" to tell process "Safari"\n'
        'repeat with w in windows\ntry\nif value of attribute "AXFullScreen" of w then set value of attribute "AXFullScreen" of w to false\nend try\nend repeat\nend tell')
    for wid in safari_ids() - before:
        osa(f'tell application "Safari" to close (every window whose id is {wid})')


@pytest.mark.parametrize("url,site,expect", [
    ("https://www.facebook.com/", "Facebook", ("facebook.com", "mlx")),
    ("https://www.youtube.com/", "YouTube", ("youtube.com/results", "search_query=mlx")),
    ("https://github.com/", "GitHub", ("github.com/search", "q=mlx")),
    ("https://example.com/", None, ("google.com/search", "q=mlx")),
])
def test_site_search_in_front_safari(safari, say, url, site, expect):
    safari(url)
    step = ok_step(say("查 mlx"))
    assert step["context"]["site"] == site and step["context"]["app"] == "Safari"
    got = wait_for(lambda: (u := urllib.parse.unquote(safari_url())) and all(e in u for e in expect) and u,
                   timeout=15, what=f"Safari 開出 {expect}")
    assert all(e in got for e in expect)


def test_page_back_and_forward_with_typo(safari, say):
    safari("https://example.com/")
    osa('tell application "Safari" to set URL of current tab of front window to "https://example.org/"')
    wait_for(lambda: safari_url().startswith("https://example.org"), timeout=15, what="載入 example.org")
    time.sleep(1.0)
    ok_step(say("上一業"))  # 錯字：上一頁
    wait_for(lambda: safari_url().startswith("https://example.com"), what="上一頁回到 example.com")
    time.sleep(0.8)
    ok_step(say("下一頁"))
    wait_for(lambda: safari_url().startswith("https://example.org"), what="下一頁回到 example.org")


def test_safari_new_window_then_close_window(safari, say):
    safari("https://example.com/")
    n = len(safari_ids())
    ok_step(say("開新視窗"))
    wait_for(lambda: len(safari_ids()) == n + 1, what="Safari 多一個視窗")
    time.sleep(0.8)
    ok_step(say("關閉視窗"))
    wait_for(lambda: len(safari_ids()) == n, what="Safari 視窗數回到原本")


YOUTUBE_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # 19 秒的公開影片（YouTube 第一支影片），長期存在
# 播放中是「暫停 (k)」、暫停後是「播放鍵盤快速鍵k」（2026-11-15 實測兩種格式不同）；和詞庫 play_pause 的規則相同
_PLAYER_BUTTON = context.SHORTCUTS["play_pause"]["bindings"][0]["button"]


def _player_label(pid: int) -> str:
    from mac_ai.router import native
    return native.button_label(pid, _PLAYER_BUTTON) or ""


def _player_state(pid: int) -> str:
    """按鈕寫『暫停』＝正在播放；寫『播放』＝已暫停（同一狀態的文字格式會變，所以只看開頭）"""
    label = _player_label(pid)
    return "playing" if label.startswith(("暫停", "Pause")) else ("paused" if label.startswith(("播放", "Play")) else "")


def test_youtube_play_pause_presses_player_button(safari, say):
    """改為按播放器按鈕（輔助使用介面）：以前送 k，中文輸入法下會被吃掉。以按鈕文字（暫停 (k)／播放 (k)）判定切換"""
    safari(YOUTUBE_VIDEO, "https://www.youtube.com/watch")
    pid = context.read_front(proc._run).pid
    before = wait_for(lambda: _player_state(pid), timeout=15, what="YouTube 播放器按鈕出現")
    step = ok_step(say("播放暫停"))
    assert step["context"]["method"] == "按鈕" and step["context"]["site"] == "YouTube"
    after = wait_for(lambda: (x := _player_state(pid)) and x != before and x, timeout=5, what=f"播放狀態由 {before} 切換")
    ok_step(say("播放暫停"))
    wait_for(lambda: _player_state(pid) == before, timeout=5, what=f"播放狀態由 {after} 切回 {before}")


def _safari_fullscreen() -> bool:
    return osa('tell application "System Events" to tell process "Safari" to return value of attribute "AXFullScreen" of window 1') == "true"


def test_fullscreen_enter_and_leave(safari, say):
    safari("https://example.com/")
    ok_step(say("全屏"))  # 大陸用語
    wait_for(_safari_fullscreen, timeout=8, what="進入全螢幕")
    time.sleep(1.5)
    ok_step(say("離開全螢幕"))
    wait_for(lambda: not _safari_fullscreen(), timeout=8, what="離開全螢幕")
    time.sleep(1.0)


LONG_PAGE = "<html><body style='font:40px sans-serif'>" + "".join(f"<p>第 {i} 行 ACCEPTANCE</p>" for i in range(1, 301)) + "</body></html>"


@pytest.fixture
def local_page(tmp_path):
    """測試用長網頁：只綁 127.0.0.1 的暫時 HTTP 伺服器（Safari 不讓測試直接開暫存資料夾裡的 file://）"""
    (tmp_path / "long.html").write_text(LONG_PAGE.replace("<html>", "<html><head><title>ACCEPTANCE_LONG</title></head>"), encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/long.html"
    httpd.shutdown()


def test_zoom_and_scroll_change_the_page_and_restore(safari, say, tmp_path, local_page):
    safari(local_page)
    num = window_number("Safari", "ACCEPTANCE_LONG")
    base = shot(num, tmp_path / "base.png")

    def check(cmd_on, cmd_off, name):
        ok_step(say(cmd_on))
        time.sleep(1.0)
        after = shot(num, tmp_path / f"{name}-on.png")
        ok_step(say(cmd_off))
        time.sleep(1.0)
        back = shot(num, tmp_path / f"{name}-off.png")
        return changed(base, after), changed(base, back)

    on, off = check("放大", "縮小", "zoom")
    assert on > CHANGED_MIN and off < RESTORED_MAX, f"放大後變化 {on:.3f}、縮小後殘留 {off:.3f}"
    on, off = check("往下捲", "往上捲", "scroll")
    assert on > CHANGED_MIN and off < RESTORED_MAX, f"往下捲變化 {on:.3f}、往上捲殘留 {off:.3f}"
    ok_step(say("往下捲"))
    ok_step(say("往下捲"))
    time.sleep(0.8)
    ok_step(say("回到頂端"))
    time.sleep(1.0)
    top = changed(base, shot(num, tmp_path / "top.png"))
    assert top < RESTORED_MAX, f"回到頂端後殘留 {top:.3f}"


# ---------- Finder ----------
@pytest.fixture
def finder(tmp_path):
    folder = tmp_path / "finder_acceptance"
    folder.mkdir()
    wid = osa(f'tell application "Finder"\nset w to make new Finder window to (POSIX file "{folder}")\nreturn id of w\nend tell')
    osa(f'tell application "Finder" to set index of (Finder window id {wid}) to 1')
    activate("Finder")
    yield wid
    osa(f'tell application "Finder"\nif exists Finder window id {wid} then close Finder window id {wid}\nend tell')


def _finder(wid: str, prop: str) -> str:
    return osa(f'tell application "Finder"\nif not (exists Finder window id {wid}) then return "gone"\nreturn {prop} of Finder window id {wid}\nend tell')


def test_unsupported_shortcut_sends_nothing_in_finder(finder, say):
    target = _finder(finder, "name")
    step = only_step(say("上一頁"))
    assert step["status"] == "exec_failed" and step["logs"][-1] == "⚠️ 目前最前面的是 Finder，「上一頁」只支援瀏覽器"
    assert _finder(finder, "name") == target and front_app() == "Finder"


def test_finder_minimize_with_typo_then_close(finder, say):
    ok_step(say("最小話"))  # 錯字：最小化
    wait_for(lambda: _finder(finder, "collapsed") == "true", what="Finder 視窗最小化")
    osa(f'tell application "Finder" to set collapsed of Finder window id {finder} to false')
    wait_for(lambda: _finder(finder, "collapsed") == "false", what="還原視窗")
    osa(f'tell application "Finder" to set index of (Finder window id {finder}) to 1')
    activate("Finder")
    ok_step(say("關閉試窗"))  # 錯字：關閉視窗
    wait_for(lambda: _finder(finder, "name") == "gone", what="Finder 視窗被關閉")


# ---------- Terminal ----------
def terminal_ids() -> set:
    out = osa('tell application "Terminal" to get id of every window')
    return {x.strip() for x in out.split(",") if x.strip()}


def _wait_idle(wid: str) -> None:
    """等新開的 shell 載入完（busy＝false）。shell 啟動中就關閉會跳出『要終止正在執行的程序嗎』，把視窗卡住"""
    wait_for(lambda: osa(f'tell application "Terminal" to return busy of selected tab of window id {wid}') == "false",
             timeout=8, what=f"Terminal 視窗 {wid} 的 shell 閒置")


def close_new_terminal_windows(before: set) -> None:
    """關掉測試期間新增的 Terminal 視窗（含原生分頁）。視窗可能晚一點才出現，所以重試 3 秒直到沒有新視窗"""
    end = time.monotonic() + 3.0
    while time.monotonic() < end:
        new = terminal_ids() - before
        for w in new:
            _wait_idle(w)
            osa(f'tell application "Terminal" to close (every window whose id is {w})')
        if not new:
            time.sleep(0.5)
            if not terminal_ids() - before:
                return
    assert not terminal_ids() - before, f"Terminal 測試視窗沒關掉：{terminal_ids() - before}"


@pytest.fixture
def terminal():
    before = terminal_ids() if subprocess.run(["pgrep", "-x", "Terminal"], capture_output=True).returncode == 0 else set()
    ref = osa('tell application "Terminal" to do script ""')  # 開新視窗，不執行任何指令
    wid = ref.rsplit("window id", 1)[-1].strip()
    assert wid.isdigit(), ref
    _wait_idle(wid)
    osa(f'tell application "Terminal" to set index of (window id {wid}) to 1')
    activate("Terminal")
    time.sleep(0.7)
    yield wid
    close_new_terminal_windows(before)


def _all_tabs() -> int:
    """全部分頁數。這台若開了 macOS 原生分頁，AppleScript 會把每個分頁當成一個只有 1 個分頁的視窗，所以加總才準"""
    return sum(int(x) for x in osa('tell application "Terminal" to return count of tabs of every window').split(",") if x.strip())


def _terminal_merged() -> bool:
    """AppleScript 看到的視窗比畫面上的視窗多＝有視窗被併成原生分頁"""
    ax = int(osa('tell application "System Events" to tell process "Terminal" to return count of windows'))
    return ax < len(terminal_ids())


def _history(wid: str) -> str:
    return osa(f'tell application "Terminal" to return history of selected tab of window id {wid}')


def test_terminal_new_tab_and_clear_screen(terminal, say):
    osa(f'tell application "Terminal" to do script "echo ACCEPTANCE_MARK" in selected tab of window id {terminal}')
    wait_for(lambda: "ACCEPTANCE_MARK" in _history(terminal), what="終端機印出標記")
    ok_step(say("清除話面"))  # 錯字：清除畫面
    wait_for(lambda: "ACCEPTANCE_MARK" not in _history(terminal), what="清除畫面後標記消失")
    n = _all_tabs()
    ok_step(say("新分頁"))
    wait_for(lambda: _all_tabs() == n + 1, what="Terminal 多一個分頁")


def test_terminal_close_window(terminal, say):
    if _terminal_merged():  # ⇧⌘W 會關掉整個視窗（含併進來的分頁）：測試視窗若和原本的視窗併在一起就不送
        pytest.skip("Terminal 測試視窗被併成其他視窗的原生分頁，關閉視窗會連帶關掉原本的視窗，略過")
    wait_for(lambda: osa('tell application "Terminal" to return id of front window') == terminal, what="測試視窗在最前面")
    ok_step(say("關閉視窗"))
    wait_for(lambda: terminal not in terminal_ids(), timeout=8, what="Terminal 測試視窗被關閉")


def test_open_terminal_app_when_editor_is_not_in_front(finder, say):
    before = terminal_ids() if subprocess.run(["pgrep", "-x", "Terminal"], capture_output=True).returncode == 0 else set()
    try:
        step = ok_step(say("開終端機"))
        assert step["logs"][-1] == "✅ 已開啟應用程式：Terminal"
        wait_for(lambda: front_app() == "Terminal", what="Terminal 成為最前面的 App")
    finally:
        close_new_terminal_windows(before)


# ---------- VS Code（只操作測試自己開的視窗）----------
def _code_windows() -> int:
    return int(osa('tell application "System Events" to tell process "Code" to return count of windows'))


def _code_front_title() -> str:
    return osa('tell application "System Events" to tell process "Code" to return name of window 1')


@pytest.fixture
def vscode(tmp_path):
    folder = tmp_path / f"vscode_acc_{uuid.uuid4().hex[:6]}"  # 每個測試不同名稱：標題比對不會認錯上一個測試的視窗
    folder.mkdir()
    n = _code_windows()
    subprocess.run(["open", "-a", VSCODE, str(folder)], check=True, timeout=10)
    try:
        wait_for(lambda: _code_windows() == n + 1 and folder.name in _code_front_title(), timeout=20,
                 what="VS Code 為測試資料夾開了新視窗")
    except AssertionError:
        pytest.fail("VS Code 沒有開新視窗（可能沿用了原本的視窗）；為了不動到你的視窗，停止測試")
    activate(VSCODE)
    time.sleep(2.0)  # 新視窗的歡迎畫面、檔案總管要一點時間畫完

    def bring_front():
        subprocess.run(["open", "-a", VSCODE, str(folder)], check=True, timeout=10)
        wait_for(lambda: front_app() == VSCODE and folder.name in _code_front_title(), what="測試視窗回到最前面")
        time.sleep(0.8)
    bring_front.folder, bring_front.count = folder, n
    yield bring_front
    if _code_windows() > n:  # 測試沒關掉就收尾：確認最前面是測試視窗才送 ⇧⌘W
        bring_front()
        if folder.name in _code_front_title():
            osa('tell application "System Events" to key code 53')  # Esc：先取消輸入法組字（否則後面的按鍵都被輸入法吃掉）
            time.sleep(0.3)
            osa('tell application "System Events" to key code 13 using {command down, shift down}')
            wait_for(lambda: _code_windows() == n, what="關閉 VS Code 測試視窗")


def _toggle_twice(vscode, say, tmp_path, on, off, name):
    num = window_number("Code", vscode.folder.name)
    base = shot(num, tmp_path / f"{name}-base.png")
    ok_step(say(on))
    time.sleep(1.5)
    after = changed(base, shot(num, tmp_path / f"{name}-on.png"))
    ok_step(say(off))
    time.sleep(1.5)
    back = changed(base, shot(num, tmp_path / f"{name}-off.png"))
    return after, back


def _chinese_input_method() -> bool:
    out = subprocess.run(["defaults", "read", "com.apple.HIToolbox", "AppleSelectedInputSources"], capture_output=True, text=True).stdout
    return any(k in out for k in ("inputmethod.TCIM", "inputmethod.SCIM", "Zhuyin", "Pinyin", "Cangjie"))


def test_vscode_sidebar_toggle(vscode, say, tmp_path):
    after, back = _toggle_twice(vscode, say, tmp_path, "開側編欄", "開側邊欄", "sidebar")  # 前者是錯字
    assert after > CHANGED_MIN and back < RESTORED_MAX, f"側邊欄：切換後變化 {after:.3f}、切回殘留 {back:.3f}"


def test_vscode_terminal_panel_toggle(vscode, say, tmp_path):
    """改為按選單「檢視 > 終端」後，中文輸入法啟用時也要打得開面板（以前送 ⌃` 會被注音吃掉，在聊天框打出「、」）。
    只驗『打開』：測試資料夾是未受信任的暫存資料夾，VS Code 要建立終端機時會跳『是否信任』對話框並拒絕啟動終端程序，
    再按一次會再跳一次，所以『關掉』要在受信任的資料夾才能驗（不改使用者的信任設定，見計劃書 §16.5）"""
    from mac_ai.router import native
    ime = _chinese_input_method()
    num = window_number("Code", vscode.folder.name)
    base = shot(num, tmp_path / "terminal-base.png")
    step = ok_step(say("開終端積"))  # 錯字：開終端機
    assert step["context"]["method"] == "選單"
    time.sleep(1.5)
    opened = changed(base, shot(num, tmp_path / "terminal-on.png"))
    assert opened > CHANGED_MIN, f"終端機面板沒有出現（中文輸入法={ime}）：變化 {opened:.3f}"
    native.post_key(53)  # Esc：關掉信任對話框（不按『信任』）
    time.sleep(0.8)


def test_vscode_save_sends_keys_only(vscode, say):
    """存檔：測試不打字，檔案永遠沒有未存變更，效果驗不了（D8）：只驗選單「檔案 > 儲存」按下成功且前景沒變"""
    step = ok_step(say("存擋"))  # 錯字：存檔
    assert step["logs"][-1] == f"✅ {VSCODE}：存檔" and front_app() == VSCODE


def test_vscode_open_project_uses_front_editor_then_close_test_window(vscode, say):
    step = only_step(say("上一頁"))
    assert step["status"] == "exec_failed" and "只支援瀏覽器" in step["logs"][-1]
    ok_step(say("開 mac_ai_control 專案"))
    wait_for(lambda: "mac_ai_control" in _code_front_title(), what="VS Code 切到 mac_ai_control 專案視窗")
    vscode()  # 回到測試視窗
    ok_step(say("關閉視窗"))
    wait_for(lambda: _code_windows() == vscode.count, what="VS Code 測試視窗被關閉")


# ---------- 桌面角色在前景（取代 U4）----------
def test_desktop_character_in_front_is_never_operated(say):
    proc = subprocess.Popen([sys.executable, str(config.ROOT / "scripts" / "ui_demo.py"), "--hold"],
                            cwd=config.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        def electron_pid():
            p = subprocess.run(["pgrep", "-f", str(config.ROOT / "desktop" / "node_modules" / "electron")], capture_output=True, text=True)
            pids = p.stdout.split()
            return pids[0] if pids else ""
        pid = wait_for(electron_pid, timeout=20, what="桌面角色（Electron）啟動")
        time.sleep(2.0)
        osa(f'tell application "System Events" to set frontmost of (first process whose unix id is {pid}) to true')
        try:
            wait_for(lambda: context.read_front(proc._run) is None and front_app() == "", timeout=5,
                     what="桌面角色成為最前面的 App")
        except AssertionError:
            pytest.skip("桌面角色無法成為最前面的 App（視窗不接受焦點），這條路徑只有離線測試")
        step = only_step(say("關閉視窗"))
        assert step["logs"][-1] == "⚠️ 不知道要操作哪個視窗（讀不到最前面的 App），沒有執行「關閉視窗」"
        assert proc.poll() is None, "桌面角色被關掉了"
    finally:
        os.killpg(proc.pid, signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
