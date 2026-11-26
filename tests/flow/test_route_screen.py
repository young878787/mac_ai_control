"""看畫面整條路由：截圖片語走 Fast Path、LLM 沒呼叫 read_screen 的補救、頁面／網頁只截前景瀏覽器視窗"""
import subprocess

import pytest

import mac_ai.router as r
from mac_ai.router import exec_context, llm_client
from support.expect import fast_matches
from support.llm import llm_always, tc
from support.screen import Screen


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
    llm_always(monkeypatch, {"content": "好"})
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


@pytest.mark.parametrize("text,calls,reply", [
    ("網頁是什麼", 2, "一般回覆"), ("螢幕解析度是什麼", 2, "一般回覆"), ("怎麼看螢幕亮度", 2, "一般回覆"), ("能看到畫面嗎", 2, "一般回覆"),
    ("不要看畫面上寫什麼", 0, "好，我沒有執行任何動作。"), ("昨天看過畫面", 0, "好，我沒有執行任何動作。"),  # 否定／敘述（G1）：不呼叫 LLM
])
def test_non_look_requests_still_go_to_chat(monkeypatch, tmp_path, text, calls, reply):
    s = _screen_llm(monkeypatch, tmp_path, plan_content="一般回覆")
    out = r.process_voice_command(text)
    assert s.names == [] and s.vl_messages == []  # 沒有截圖
    assert len(s.other) == calls  # 規劃＋chat call
    assert out["replies"] == [reply]


def test_look_request_after_failed_browser_is_skipped(monkeypatch, tmp_path):
    """前置步驟（開瀏覽器）失敗時，補救的 read_screen 也要略過，與一般的 read_screen 行為一致"""
    s = _screen_llm(monkeypatch, tmp_path)
    ctx = r.ExecContext()
    ctx.failed.add("browser")
    ctx.step = {}
    from mac_ai.router.slow_path import execute_slow_path_llm
    logs = execute_slow_path_llm("看一下現在畫面的內容是什麼", ctx.step, ctx)
    assert logs[0].endswith("⏭️ 略過 read_screen：前一步失敗") and s.names == []


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
                        else {"tool_calls": [tc("read_screen", "{}")]})
    out = r.process_voice_command("幫我想想這個網頁到底在寫什麼東西")  # Fast 規則外的說法，交給 LLM
    assert out["steps"][0]["kind"] == "slow"
    assert _shots(s)[0][:5] == ["screencapture", "-x", "-o", "-l", "16304"]


def test_website_word_also_means_page_scope(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path, window_out=WIN)
    ctx = exec_context.ExecContext(utterance="看一下這個網站")
    assert r.read_screen(ctx).startswith("✅ 畫面內容（Safari視窗")
    assert _shots(s)[0][:5] == ["screencapture", "-x", "-o", "-l", "16304"]


def test_read_screen_without_context_captures_whole_screen(monkeypatch, tmp_path):
    s = Screen(monkeypatch, tmp_path, window_out=WIN)
    assert r.read_screen().startswith("✅ 畫面內容（整個螢幕）：")
    assert s.names == ["screencapture", "sips"]
