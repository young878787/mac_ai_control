"""實測回報的四個問題：喚醒詞前的招呼詞、Safari／Chrome 分頁工具、搜尋只取關鍵字、截圖立即走 Fast Path。
subprocess、截圖、LLM 全部是假物件；LLM 預設為離線（conftest）。"""
import os
import subprocess
import sys
import tempfile

import pytest

import mac_ai.router as r
from mac_ai.router import actions, llm_client
from mac_ai.stt.completeness import COMPLETE, INCOMPLETE, classify
from mac_ai.wake import WakeGate, lexicon_wake_greetings, lexicon_wake_words, split_wake
from test_fast_offline import APP, CHROME, SEARCH, fast_matches
from test_multistep_offline import Screen, _tc

SAFARI = "Safari"


# ---------- 1. 招呼詞＋喚醒詞 ----------
@pytest.mark.parametrize("text, rest", [
    ("嘿露西亞", ""),
    ("嘿，露西亞", ""),
    ("嗨露西婭，開 Safari", "開 Safari"),
    ("哈囉 露醬 打開小算盤", "打開小算盤"),
    ("Hey Lucy 開Google", "開Google"),
    ("hi，露西亞。把音量調到 30", "把音量調到 30"),
    ("露西亞，開 Safari", "開 Safari"),  # 沒有招呼詞照舊
])
def test_greeting_before_wake_word_wakes(text, rest):
    assert split_wake(text, lexicon_wake_words(), lexicon_wake_greetings()) == rest


@pytest.mark.parametrize("text", [
    "嘿嘿露西亞",  # 招呼詞只允許一個
    "嘿你好露西亞",  # 招呼詞與喚醒詞之間不能有別的字
    "我跟露西亞說嘿",  # 喚醒詞不在句首
    "嘿，打開小算盤",  # 只有招呼詞
    "你好",
])
def test_greeting_does_not_loosen_other_cases(text):
    assert split_wake(text, lexicon_wake_words(), lexicon_wake_greetings()) is None


def test_greeting_is_not_a_wake_word_by_itself():
    assert split_wake("嘿露西亞", ["露西亞"]) is None  # 沒傳 greetings＝舊行為
    assert "嘿" in lexicon_wake_greetings() and "嘿" not in lexicon_wake_words()


def test_gate_with_default_lexicon_accepts_greeting():
    gate = WakeGate(enabled=True)
    assert gate.feed("嘿露西亞").status == "armed"
    r2 = gate.feed("開 Safari")  # 會話內免喚醒詞
    assert (r2.status, r2.command) == ("command", "開 Safari")
    r3 = WakeGate(enabled=True).feed("嘿露西亞，開 Safari")
    assert (r3.status, r3.command) == ("command", "開 Safari")


def test_gate_with_custom_words_has_no_greetings():
    assert WakeGate(["露西婭"], enabled=True).feed("嘿露西婭，開 Safari").status == "ignored"


def test_gate_disabled_strips_greeting_and_wake_word():
    r1 = WakeGate(enabled=False).feed("嘿露西亞，開 Safari")
    assert (r1.status, r1.command) == ("command", "開 Safari")


def test_classify_agrees_with_gate_on_greeting():
    assert classify("嘿露西亞", wake_enabled=True) == INCOMPLETE  # 只說了喚醒詞，指令還在後面
    assert classify("嘿露西亞，開 Safari", wake_enabled=True) == COMPLETE


# ---------- 2. 搜尋只取關鍵字 ----------
@pytest.mark.parametrize("text, query", [
    ("幫我搜尋一下今天台北天氣", "今天台北天氣"),  # 實測：原本關鍵字變成「一下今天台北天氣」
    ("搜尋一下今天台北天氣", "今天台北天氣"),
    ("搜索一下台北天氣", "台北天氣"),
    ("搜尋看看 Python", "Python"),
    ("上網查查今天天氣", "今天天氣"),
    ("google 一下今天台北天氣", "今天台北天氣"),
    ("搜尋台北天氣怎麼樣", "台北天氣怎麼樣"),  # 關鍵字裡的疑問詞是搜尋內容
    ("查一下今天有什麼新聞", "今天有什麼新聞"),
])
def test_search_extracts_keyword_only(monkeypatch, sh, text, query):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    out = r.process_voice_command(text)
    assert sh == [SEARCH(query)]
    assert all(s["kind"] == "fast" for s in out["steps"])


def test_open_browser_then_search_keyword_only(monkeypatch, sh):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    r.process_voice_command("開 chrome 搜尋一下今天台北天氣")
    assert sh == [APP(CHROME), SEARCH("今天台北天氣", CHROME)]


@pytest.mark.parametrize("text", ["搜尋", "搜尋一下", "查一下", "搜尋嗎", "可以搜尋嗎", "查看音量", "怎麼搜尋", "今天天氣如何", "什麼是搜尋"])
def test_search_without_keyword_or_asking_about_search_is_not_fast(text):
    assert all(m is None for m in fast_matches(text)), text


# ---------- 3. 截圖：立刻截、走 Fast Path ----------
SCREEN_PHRASES = [
    "幫我截圖看這張圖片在說什麼",  # 實測：原本交給 LLM，被當成聊天
    "那你截這張圖看一下",  # 實測：原本要 LLM 規劃 0.8 秒才呼叫 read_screen
    "截圖", "截個圖", "截個圖看看", "截一下圖", "螢幕截圖", "幫我截圖", "截圖並說明", "截圖看這個畫面",
    "看一下這張圖片", "看這張圖片在說什麼", "這張圖片在說什麼", "這張圖在寫什麼", "這張圖片是什麼",
    "分析這張圖",
]


@pytest.mark.parametrize("text", SCREEN_PHRASES)
def test_screenshot_phrases_go_straight_to_read_screen(monkeypatch, tmp_path, text):
    s = Screen(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_client, "_chat", s.chat)  # 只允許 VL 請求
    out = r.process_voice_command(text)
    assert [st["kind"] for st in out["steps"]] == ["fast"]
    assert s.names == ["screencapture", "sips"]  # 沒有開網頁，不等待、不經 LLM 規劃
    (messages, kw), = s.vl_messages  # 唯一一次 LLM 請求是畫面辨識（不帶 tools）
    assert kw["tools"] is False
    assert out["replies"] == ["天氣：晴，氣溫 25 度"]


@pytest.mark.parametrize("text", [
    "不要截圖", "剛剛截圖了", "我昨天截圖", "怎麼截圖", "對這張圖片", "截圖在哪裡", "這張圖片",
])
def test_screenshot_negative_or_incomplete_phrases_do_not_capture(monkeypatch, tmp_path, text):
    assert all(m is None for m in fast_matches(text)), text
    s = Screen(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"content": "好"})
    r.process_voice_command(text)
    assert s.names == []


# 實測：『幫我看看畫面上寫什麼』Fast 與 LLM 都漏接，LLM 回「請提供截圖」、chat call 還編出畫面內容
LOOK_WHAT_SCREEN = ["幫我看看畫面上寫什麼", "看看畫面上寫什麼", "幫我看一下螢幕上有什麼", "螢幕上寫什麼", "看一下螢幕上說什麼"]
LOOK_WHAT_PAGE = ["幫我看看這個網頁在寫什麼", "幫我看看網頁上有什麼", "看看這個頁面在說什麼", "看一下目前的網頁上寫什麼", "讀一下網頁上有什麼"]


@pytest.mark.parametrize("text", LOOK_WHAT_SCREEN)
def test_look_at_screen_question_goes_straight_to_read_screen(monkeypatch, tmp_path, text):
    s = Screen(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_client, "_chat", s.chat)  # 只允許 VL 請求：不得有 LLM 規劃
    out = r.process_voice_command(text)
    assert [st["kind"] for st in out["steps"]] == ["fast"]
    assert s.names == ["screencapture", "sips"]
    assert out["replies"] == ["天氣：晴，氣溫 25 度"]


@pytest.mark.parametrize("text", LOOK_WHAT_PAGE)
def test_look_at_page_question_matches_read_screen(text):
    (m,) = fast_matches(text)
    assert m is not None and m.actions == [("read_screen", {})], text


@pytest.mark.parametrize("text", [
    "看看台北天氣", "看看天氣怎麼樣", "幫我看看有什麼好吃的拉麵", "畫面上的字太小怎麼辦", "螢幕是什麼",
    "不要看畫面上寫什麼", "昨天畫面上寫什麼", "沒有我說的是這網頁", "看看網頁是什麼",
])
def test_look_question_guards_still_block_fast_read_screen(text):
    ms = fast_matches(text)
    assert not any(m is not None and ("read_screen", {}) in m.actions for m in ms), text


# ---------- LLM 沒呼叫 read_screen 的『看畫面』請求：直接讀畫面，不讓 chat call 編內容 ----------
def _screen_llm(monkeypatch, tmp_path, plan_content="好的，請提供截圖"):
    """規劃請求（帶 tools）一律不呼叫工具；畫面辨識（含圖片）回固定內容；其餘請求記在 other"""
    s = Screen(monkeypatch, tmp_path)
    s.other = []

    def chat(messages, **kw):
        if isinstance(messages[-1]["content"], list):
            return s.chat(messages, **kw)
        s.other.append((messages, kw))
        return {"content": plan_content}

    monkeypatch.setattr(llm_client, "_chat", chat)
    return s


@pytest.mark.parametrize("text", ["看一下現在畫面的內容是什麼", "麻煩念一下螢幕上的字"])
def test_look_request_missed_by_llm_runs_read_screen_directly(monkeypatch, tmp_path, text):
    assert all(m is None for m in fast_matches(text)), "這句不該被 Fast Path 接走，否則測不到 LLM 補救"
    s = _screen_llm(monkeypatch, tmp_path)
    out = r.process_voice_command(text)
    (step,) = out["steps"]
    assert step["kind"] == "slow" and step["status"] == "ok"
    assert s.names == ["screencapture", "sips"] and len(s.vl_messages) == 1
    assert len(s.other) == 1  # 只有規劃請求，沒有 chat call
    assert out["replies"] == ["天氣：晴，氣溫 25 度"]
    assert step["logs"][0].startswith("[Slow Path - 補救] read_screen")


@pytest.mark.parametrize("text", [
    "網頁是什麼", "螢幕解析度是什麼", "怎麼看螢幕亮度", "不要看畫面上寫什麼", "昨天看過畫面", "能看到畫面嗎",
])
def test_non_look_requests_still_go_to_chat(monkeypatch, tmp_path, text):
    s = _screen_llm(monkeypatch, tmp_path, plan_content="一般回覆")
    out = r.process_voice_command(text)
    assert s.names == [] and s.vl_messages == []  # 沒有截圖
    assert len(s.other) == 2  # 規劃＋chat call
    assert out["replies"] == ["一般回覆"]


def test_look_request_after_failed_browser_is_skipped(monkeypatch, tmp_path):
    """前置步驟（開瀏覽器）失敗時，補救的 read_screen 也要略過，與一般的 read_screen 行為一致"""
    s = _screen_llm(monkeypatch, tmp_path)
    ctx = r.ExecContext()
    ctx.failed.add("browser")
    ctx.step = {}
    from mac_ai.router.slow_path import execute_slow_path_llm
    logs = execute_slow_path_llm("看一下現在畫面的內容是什麼", ctx.step, ctx)
    assert logs[0].endswith("⏭️ 略過 read_screen：前一步失敗") and s.names == []


# ---------- 4. 瀏覽器分頁 ----------
def NEW_TAB(browser):
    return actions._TAB_SCRIPTS[browser]["new"]


def TAB(browser, action):
    return ["osascript", "-e", actions._TAB_SCRIPTS[browser][action]]


QUERY = ["osascript", "-e", actions._FRONT_AND_RUNNING_SCRIPT]


class Browsers:
    """假的 macOS：System Events 回報最前景 App 與執行中的 App；其餘 osascript 一律成功並記錄"""

    def __init__(self, monkeypatch, front="Safari", running=("Safari",), fail=()):
        self.calls, self.front, self.running, self.fail = [], front, list(running), list(fail)
        monkeypatch.setattr(subprocess, "run", self.run)

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd in self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", "execution error: Can't get window 1. (-1728)")
        if cmd == QUERY:
            return subprocess.CompletedProcess(cmd, 0, f"{self.front}\n{','.join(self.running)}\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


TAB_PHRASES = [
    ("幫我開一個新的分頁", "new"),  # 實測：原本被 LLM 亂呼叫 open_project
    ("幫我再開一個新的分頁", "new"),  # 實測：原本被 LLM 拿去 Google 搜尋「如何開新分頁」
    ("開新分頁", "new"), ("打開新的分頁", "new"), ("開啟一個新分頁", "new"), ("新增分頁", "new"),
    ("新增一個新的分頁", "new"), ("開一個分頁", "new"), ("新分頁", "new"), ("開新標籤頁", "new"),
    ("關掉這個分頁", "close"), ("關閉分頁", "close"), ("把這個分頁關掉", "close"), ("關閉目前的分頁", "close"),
    ("重新整理", "reload"), ("重新整理這個分頁", "reload"), ("重新載入頁面", "reload"), ("刷新", "reload"),
    ("下一個分頁", "next"), ("切換到下一個分頁", "next"), ("切到下個分頁", "next"),
    ("上一個分頁", "previous"), ("切換到上一個分頁", "previous"), ("跳到上個分頁", "previous"),
]


@pytest.mark.parametrize("text, action", TAB_PHRASES, ids=[t[0] for t in TAB_PHRASES])
def test_tab_phrases_run_browser_tab_without_llm(monkeypatch, text, action):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    env = Browsers(monkeypatch, front="Safari", running=["Safari", "Finder"])
    out = r.process_voice_command(text)
    assert env.calls == [QUERY, TAB(SAFARI, action)]
    assert [st["kind"] for st in out["steps"]] == ["fast"] and out["steps"][0]["status"] == "ok"
    assert out["steps"][0]["logs"][0].startswith("✅ Safari")


@pytest.mark.parametrize("text", [
    "不要開新分頁", "剛剛開了新分頁", "怎麼開新分頁", "分頁", "新增", "關掉所有分頁", "關掉其他分頁", "開會",
    "上一頁", "下一頁", "重新整理電腦",
])
def test_tab_negative_phrases_do_not_touch_browser(monkeypatch, text):
    assert all(m is None for m in fast_matches(text)), text
    env = Browsers(monkeypatch)
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"content": "好"})
    r.process_voice_command(text)
    assert env.calls == []


def test_tab_browser_choice_front_then_running(monkeypatch):
    env = Browsers(monkeypatch, front="Google Chrome", running=["Safari", "Google Chrome"])
    assert r.browser_tab("new").startswith("✅ Google Chrome")
    assert env.calls[-1] == TAB("Google Chrome", "new")
    env = Browsers(monkeypatch, front="Finder", running=["Finder", "Google Chrome", "Safari"])  # 前景不是瀏覽器 → 依序挑 Safari
    assert r.browser_tab("close").startswith("✅ Safari") and env.calls[-1] == TAB(SAFARI, "close")
    env = Browsers(monkeypatch, front="Finder", running=["Finder", "Google Chrome"])
    assert r.browser_tab("reload").startswith("✅ Google Chrome") and env.calls[-1] == TAB("Google Chrome", "reload")


def test_tab_without_browser_reports_failure(monkeypatch):
    env = Browsers(monkeypatch, front="Finder", running=["Finder", "Notes"])
    res = r.browser_tab("new")
    assert res.startswith("❌ 找不到可操作的瀏覽器") and env.calls == [QUERY]
    out = r.process_voice_command("開新分頁")
    assert out["steps"][0]["status"] == "exec_failed"


def test_tab_hint_from_this_command_beats_frontmost(monkeypatch):
    """『開 Chrome 然後開新分頁』：剛開的瀏覽器優先，不必（也不該）去問最前景是誰"""
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    env = Browsers(monkeypatch, front="Safari", running=["Safari", "Google Chrome"])
    out = r.process_voice_command("開 Chrome，然後開新分頁")
    assert env.calls == [APP(CHROME), TAB("Google Chrome", "new")]
    assert all(st["status"] == "ok" for st in out["steps"])


def test_tab_skipped_when_browser_failed_to_open(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(
        cmd, 1 if cmd[:2] == ["open", "-a"] else 0, "", "boom"))
    out = r.process_voice_command("開 Chrome，然後開新分頁")
    assert calls == [APP(CHROME)]
    assert out["steps"][1]["logs"] == ["⏭️ 略過 browser_tab：前一步失敗"]


def test_tab_script_failure_reports_stderr(monkeypatch):
    Browsers(monkeypatch, fail=[TAB(SAFARI, "close")])
    res = r.browser_tab("close")
    assert res.startswith("❌ 分頁操作失敗：Safari") and "Can't get window 1" in res


def test_tab_query_failure_reports_reason(monkeypatch):
    Browsers(monkeypatch, fail=[QUERY])
    assert r.browser_tab("new").startswith("❌ 找不到可操作的瀏覽器") and "Can't get window 1" in r.browser_tab("new")


def test_tab_unknown_action_is_rejected(monkeypatch):
    env = Browsers(monkeypatch)
    assert r.browser_tab("explode").startswith("⚠️") and env.calls == []


# ---------- 工具 schema 與 LLM 路徑 ----------
def test_browser_tab_is_not_exposed_to_llm():
    """分頁操作只走 Fast Path：實測 browser_tab 放進 LLM 工具清單，『開深色模式』會被 4bit 模型誤判成 toggle_dark_mode"""
    names = [t["function"]["name"] for t in r.TOOLS_SCHEMA]
    assert "browser_tab" not in names and len(names) == 8
    assert r.validate_tool_call("browser_tab", {"action": "new"})[1]  # LLM 若硬呼叫，當未知工具拒絕


def test_llm_planned_browser_tab_is_rejected(monkeypatch):
    env = Browsers(monkeypatch, front="Safari", running=["Safari"])
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: {"tool_calls": [_tc("browser_tab", '{"action":"new"}')]})
    out = r.process_voice_command("幫我弄一個空白的頁籤出來")  # Fast 規則外的說法，交給 LLM
    assert env.calls == []
    assert out["steps"][0]["status"] == "rejected" and "未知工具：browser_tab" in out["steps"][0]["logs"][0]


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


# ---------- 5. 說「頁面／網頁」只截前景瀏覽器視窗 ----------
WIN = "16304\tSafari\t今天台灣股票股市 - Google 搜尋"
JXA = ["osascript", "-l", "JavaScript"]


def _shots(s):
    return [c for c in s.calls if c[0] == "screencapture"]


@pytest.mark.parametrize("text", ["辨識這個頁面", "這個網頁在說什麼", "這頁在寫什麼", "幫我看一下這個網頁"])
def test_page_words_capture_only_the_front_browser_window(monkeypatch, tmp_path, text):
    s = Screen(monkeypatch, tmp_path, window_out=WIN)
    out = r.process_voice_command(text)
    assert s.names == ["osascript", "screencapture", "sips"]
    assert s.calls[0][:3] == JXA and "Safari" in s.calls[0][4] and "Google Chrome" in s.calls[0][4]
    assert _shots(s)[0][:5] == ["screencapture", "-x", "-o", "-l", "16304"]  # 指定視窗，不是整個螢幕
    log = out["steps"][0]["logs"][0]
    assert log.startswith("✅ 畫面內容（Safari視窗「今天台灣股票股市 - Google 搜尋」）：")
    assert out["steps"][0]["capture_target"] == "Safari視窗「今天台灣股票股市 - Google 搜尋」"
    assert out["replies"] == ["天氣：晴，氣溫 25 度"]  # 給使用者的回覆不含截圖來源，維持原樣


@pytest.mark.parametrize("text", ["螢幕上有什麼", "幫我截圖看這張圖片在說什麼", "截圖", "辨識目前的畫面"])
def test_screen_words_still_capture_whole_screen_without_window_lookup(monkeypatch, tmp_path, text):
    s = Screen(monkeypatch, tmp_path, window_out=WIN)  # 就算有瀏覽器視窗也不查、不用
    out = r.process_voice_command(text)
    assert s.names == ["screencapture", "sips"]
    assert _shots(s)[0][:3] == ["screencapture", "-x", "-m"]
    assert "（整個螢幕）" in out["steps"][0]["logs"][0]


def test_page_without_browser_window_falls_back_to_whole_screen(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path, window_out="")
    out = r.process_voice_command("辨識這個頁面")
    assert s.names == ["osascript", "screencapture", "sips"] and _shots(s)[0][:3] == ["screencapture", "-x", "-m"]
    assert "（整個螢幕）" in out["steps"][0]["logs"][0] and out["steps"][0]["status"] == "ok"


@pytest.mark.parametrize("garbage", ["Safari", "abc\tSafari\t標題", "\t", "missing value"])
def test_window_lookup_with_malformed_output_falls_back(monkeypatch, tmp_path, garbage):
    s = Screen(monkeypatch, tmp_path, window_out=garbage)
    r.process_voice_command("辨識這個頁面")
    assert _shots(s)[0][:3] == ["screencapture", "-x", "-m"]


def test_window_without_title_is_labelled_by_browser_only(monkeypatch, tmp_path):
    Screen(monkeypatch, tmp_path, window_out="7029\tGoogle Chrome")
    out = r.process_voice_command("辨識這個頁面")
    assert "（Google Chrome視窗）：" in out["steps"][0]["logs"][0]


def test_window_capture_failure_falls_back_to_whole_screen(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path, window_out=WIN)
    real_run = s.run

    def run(cmd, **kw):
        if cmd[0] == "screencapture" and "-l" in cmd:  # 視窗截不到（例如剛好被最小化）
            s.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "could not create image from window")
        return real_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    out = r.process_voice_command("辨識這個頁面")
    assert [c[:4] for c in _shots(s)] == [["screencapture", "-x", "-o", "-l"], ["screencapture", "-x", "-m", _shots(s)[1][3]]]
    assert "（整個螢幕）" in out["steps"][0]["logs"][0] and out["steps"][0]["status"] == "ok"


def test_window_lookup_is_limited_to_browser_opened_in_this_command(monkeypatch, tmp_path):
    """『開 Chrome，辨識這頁』：只找剛開的 Chrome，不會截到另一個瀏覽器的視窗"""
    s = Screen(monkeypatch, tmp_path, window_out="7029\tGoogle Chrome\t新分頁")
    r.process_voice_command("開 Chrome，辨識這個頁面")
    script = next(c for c in s.calls if c[:3] == JXA)[4]
    assert 'const owners = ["Google Chrome"];' in script
    assert _shots(s)[0][:5] == ["screencapture", "-x", "-o", "-l", "7029"]


def test_llm_planned_read_screen_also_uses_page_scope(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path, window_out=WIN)
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: s.chat(messages, **kw) if kw.get("tools") is False
                        else {"tool_calls": [_tc("read_screen", "{}")]})
    out = r.process_voice_command("幫我想想這個網頁到底在寫什麼東西")  # Fast 規則外的說法，交給 LLM
    assert out["steps"][0]["kind"] == "slow"
    assert _shots(s)[0][:5] == ["screencapture", "-x", "-o", "-l", "16304"]


def test_website_word_also_means_page_scope(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path, window_out=WIN)
    ctx = actions.ExecContext(utterance="看一下這個網站")
    assert r.read_screen(ctx).startswith("✅ 畫面內容（Safari視窗")
    assert _shots(s)[0][:5] == ["screencapture", "-x", "-o", "-l", "16304"]


def test_read_screen_without_context_captures_whole_screen(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path, window_out=WIN)
    assert r.read_screen().startswith("✅ 畫面內容（整個螢幕）：")
    assert s.names == ["screencapture", "sips"]


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osascript／CoreGraphics")
def test_front_browser_window_script_runs_on_this_mac():
    """真的執行 JXA（唯讀）：語法與 CoreGraphics 呼叫要正確；有瀏覽器視窗時回傳數字編號，沒有就是 None"""
    res = actions._front_browser_window(None)
    assert res is None or (res[0].isdigit() and res[1] in actions.TAB_BROWSERS)
    ok, _ = actions._run(["osascript", "-l", "JavaScript", "-e", actions._FRONT_BROWSER_WINDOW_SCRIPT.replace("__OWNERS__", "[]")])
    assert ok
