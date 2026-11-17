"""Fast Path 整條路由（normalize → split → match → 執行）：正例、反例、歧義、站名、關鍵字擷取；LLM 一被呼叫就失敗"""
import subprocess

import pytest

import mac_ai.router as r
from mac_ai.router import llm_client
from support.expect import APP, BRIGHT, CHROME, DARK, LOCK, MUTE, QUIT, SEARCH, SLEEP, TOGGLE_DARK, VOL, VSCODE, fast_matches
from support.llm import forbid_llm, llm_always

HITS = [
    # --- 開／關 App ---
    ("開 Chrome", [APP(CHROME)]),
    ("開Chrome", [APP(CHROME)]),
    ("打開chrome吧", [APP(CHROME)]),
    ("幫我打開一下 Chrome", [APP(CHROME)]),
    ("Chrome 打開", [APP(CHROME)]),
    ("嗯 打開 Chrome", [APP(CHROME)]),
    ("那個打開Chrome好嗎", [APP(CHROME)]),
    ("請幫我把 Chrome 打開", [APP(CHROME)]),
    ("打開打開 Chrome", [APP(CHROME)]),
    ("開chrome。", [APP(CHROME)]),
    ("打開ＣＨＲＯＭＥ", [APP(CHROME)]),
    ("開 google", [APP(CHROME)]),
    ("開谷歌", [APP(CHROME)]),
    ("開谷哥瀏覽器", [APP(CHROME)]),
    ("開啟 vscode", [APP(VSCODE)]),
    ("開 VSCode", [APP(VSCODE)]),
    ("開 vs code", [APP(VSCODE)]),
    ("開 VS扣", [APP(VSCODE)]),
    ("開 chorme", [APP(CHROME)]),                # 英文拼錯（difflib 0.83）
    ("開 Spotfy", [APP("Spotify")]),             # 英文拼錯（0.92）
    ("開 iterm2", [APP("iTerm")]),
    ("開科索", [APP("Cursor")]),
    ("麻煩幫我開小算盤", [APP("Calculator")]),
    ("開一下小算盤", [APP("Calculator")]),
    ("幫我開個計算機", [APP("Calculator")]),
    ("打开计算器", [APP("Calculator")]),
    ("開設定", [APP("System Settings")]),
    ("打開系統設定", [APP("System Settings")]),
    ("打開音樂", [APP("Music")]),
    ("叫出 finder", [APP("Finder")]),
    ("打開中端機", [APP("Terminal")]),
    ("關掉備忘錄", [QUIT("Notes")]),
    ("把 Chrome 關掉", [QUIT(CHROME)]),
    ("關閉 Spotify", [QUIT("Spotify")]),
    ("請關閉訪達", [QUIT("Finder")]),
    ("幫我開檔案總管", [APP("Finder")]),             # 實測：先前交給 LLM，被誤判成 open_project／file_manager
    ("幫我開一個檔案總管", [APP("Finder")]),
    ("幫我開檔案資料夾", [APP("Finder")]),           # 實測：先前被 LLM 誤判成開 VS Code 專案
    ("關掉檔案總管", [QUIT("Finder")]),
    ("幫我開Miss call", [APP(VSCODE)]),             # 實測：STT 把 VS Code 聽成 Miss call
    ("開 miss call", [APP(VSCODE)]),
    # --- 音量 ---
    ("音量調到50", [VOL(50)]),
    ("音量調到 五十", [VOL(50)]),
    ("把音量調到百分之三十", [VOL(30)]),
    ("音量 50", [VOL(50)]),
    ("音量調成五十趴", [VOL(50)]),
    ("音量設成50%", [VOL(50)]),
    ("聲音開到最大", [VOL(100)]),
    ("聲音調到最小", [VOL(0)]),
    ("音量調到一半", [VOL(50)]),
    ("把音量調到 150", [VOL(100)]),              # executor 夾限
    ("把音量設為二十", [VOL(20)]),
    ("因量調到40", [VOL(40)]),
    ("声音调到五十", [VOL(50)]),
    # --- 靜音 ---
    ("靜音一下", [MUTE(True)]),
    ("幫我靜音", [MUTE(True)]),
    ("把聲音關掉", [MUTE(True)]),
    ("關聲音", [MUTE(True)]),
    ("淨音", [MUTE(True)]),
    ("取消靜音吧", [MUTE(False)]),
    ("解除靜音", [MUTE(False)]),
    ("打開聲音", [MUTE(False)]),
    # --- 亮度 ---
    ("把螢幕調暗一點", [BRIGHT("down", 3)]),
    ("螢幕調亮", [BRIGHT("up", 3)]),
    ("亮度調亮一點", [BRIGHT("up", 3)]),
    ("螢幕亮一點", [BRIGHT("up", 3)]),
    ("調到最亮", [BRIGHT("up", 16)]),
    ("螢幕調到最暗", [BRIGHT("down", 16)]),
    ("把亮度調低", [BRIGHT("down", 3)]),
    ("亮度調高", [BRIGHT("up", 3)]),
    ("螢幕調暗兩格", [BRIGHT("down", 2)]),
    ("把屏幕调暗一点", [BRIGHT("down", 3)]),
    # --- 深色模式 ---
    ("開深色模式", [DARK("true")]),
    ("切到深色模式", [DARK("true")]),
    ("切換到深色模式", [DARK("true")]),
    ("深色模式打開", [DARK("true")]),
    ("打開深色模式", [DARK("true")]),
    ("關閉深色模式", [DARK("false")]),
    ("關掉夜間模式", [DARK("false")]),
    ("切到淺色模式", [DARK("false")]),
    ("切換深色模式", [TOGGLE_DARK]),
    # --- 鎖定螢幕／睡眠 ---
    ("鎖定螢幕", [LOCK]),
    ("鎖螢幕", [LOCK]),
    ("鎖屏", [LOCK]),
    ("幫我鎖定螢幕", [LOCK]),
    ("把螢幕鎖定", [LOCK]),
    ("讓電腦睡眠", [SLEEP]),
    # --- 搜尋與開網站 ---
    ("搜尋台北天氣", [SEARCH("台北天氣")]),
    ("查一下台北天氣", [SEARCH("台北天氣")]),
    ("上網查台北天氣", [SEARCH("台北天氣")]),
    ("搜索台北天氣", [SEARCH("台北天氣")]),
    ("google 一下台北天氣", [SEARCH("台北天氣")]),
    ("用 google 查台北天氣", [SEARCH("台北天氣")]),
    ("開瀏覽器查一下台北天氣", [SEARCH("台北天氣")]),          # 只搜尋，不另外開 App
    ("開 google 搜尋台北天氣", [APP(CHROME), SEARCH("台北天氣", CHROME)]),
    ("開 chrome 查天氣", [APP(CHROME), SEARCH("天氣", CHROME)]),
    ("開google，搜尋台北天氣", [APP(CHROME), SEARCH("台北天氣", CHROME)]),
    ("開 Safari 搜尋 Python", [APP("Safari"), SEARCH("Python", "Safari")]),
    # --- 多句組合 ---
    ("幫我打開小算盤，然後把音量調到 40", [APP("Calculator"), VOL(40)]),
    ("開 Chrome，順便把螢幕調暗一點，還有靜音", [APP(CHROME), BRIGHT("down", 3), MUTE(True)]),
    ("打開終端機，順便幫我把螢幕調暗一點，還有開瀏覽器查一下台北天氣", [APP("Terminal"), BRIGHT("down", 3), SEARCH("台北天氣")]),
]


def test_hit_count():
    assert len(HITS) >= 60


@pytest.mark.parametrize("text,expected", HITS, ids=[h[0] for h in HITS])
def test_fast_hits_execute_expected_commands(monkeypatch, sh, text, expected):
    forbid_llm(monkeypatch)
    out = r.process_voice_command(text)
    assert sh == expected
    assert all(s["kind"] == "fast" for s in out["steps"])
    assert all(s["status"] == "ok" for s in out["steps"])


def test_fuzzy_hit_records_correction(monkeypatch, sh):
    forbid_llm(monkeypatch)
    out = r.process_voice_command("開 chorme")
    assert out["steps"][0]["match"]["confidence"] == "fuzzy"
    assert "chorme→Google Chrome" in out["steps"][0]["match"]["note"]
    out = r.process_voice_command("開 Chrome")
    assert out["steps"][0]["match"] == {"confidence": "exact", "note": ""}


# ---------- 子句切分 ----------
@pytest.mark.parametrize("text,expected", [
    ("幫我打開小算盤，然後把音量調到 40", ["幫我打開小算盤", "把音量調到 40"]),
    ("打開 VS Code", ["打開 VS Code"]),
    ("開終端機順便把螢幕調暗一點還有開瀏覽器查天氣", ["開終端機", "把螢幕調暗一點", "開瀏覽器查天氣"]),
])
def test_split_clauses(text, expected):
    assert r.FastPathMatcher().split_clauses(text) == expected


# ---------- Fast Path ----------
@pytest.mark.parametrize("clause,expected", [
    ("幫我打開小算盤", [["open", "-a", "Calculator"]]),
    ("開 Chrome", [["open", "-a", "Google Chrome"]]),
    ("開Google Chrome", [["open", "-a", "Google Chrome"]]),
    ("開 VS Code", [["open", "-a", "Visual Studio Code"]]),
    ("關掉備忘錄", [["osascript", "-e", 'tell application "Notes" to quit']]),
    ("把音量調到 50", [["osascript", "-e", "set volume output volume 50"]]),
    ("把音量調到 150", [["osascript", "-e", "set volume output volume 100"]]),
    ("靜音", [["osascript", "-e", "set volume output muted true"]]),
    ("取消靜音", [["osascript", "-e", "set volume output muted false"]]),
])
def test_fast_path_hit(sh, clause, expected):
    assert r.FastPathMatcher().try_execute(clause) is not None
    assert sh == expected


@pytest.mark.parametrize("clause", [
    "把音量調大一點", "今天天氣如何", "",
])
def test_fast_path_miss(sh, clause):
    assert r.FastPathMatcher().try_execute(clause) is None
    assert sh == []


# ---------- 專案（projects fixture 在 conftest：以暫存資料夾取代真實專案清單）----------
@pytest.mark.parametrize("text,editor,project", [
    ("開mac_ai_control專案", None, "mac_ai_control"),
    ("開 mac ai control 專案", None, "mac_ai_control"),
    ("打開語音助手專案", None, "mac_ai_control"),
    ("用 cursor 開 mac_ai_control 專案", "Cursor", "mac_ai_control"),
    ("開 other_proj 專案", None, "other_proj"),              # 自動掃描 project_roots
    ("開 mac_ai_contrl 專案", None, "mac_ai_control"),      # 專案名拼錯（容錯）
])
def test_fast_open_project(monkeypatch, sh, projects, text, editor, project):
    forbid_llm(monkeypatch)
    r.process_voice_command(text)
    assert sh == [["open", "-a", editor or VSCODE, str(projects / project)]]


def test_scan_ignores_hidden_and_files(projects):
    (projects / "a_file.txt").write_text("x")
    names = {name for name, _ in r.project_index().values()}
    assert names == {"mac_ai_control", "other_proj"}


# ---------- 反例：寧可少命中，也不可誤執行 ----------
MISSES = [
    # 否定
    "不要打開 Chrome", "別開 Chrome", "不用開 Chrome 了", "先不要關掉 Spotify", "我不想開 Chrome",
    # 敘述（過去）
    "我昨天打開 Chrome", "剛剛開了 Chrome", "剛才關掉 Spotify", "已經打開 Chrome 了", "之前開過 Chrome",
    # 提問
    "Chrome 是什麼", "怎麼開 Chrome", "為什麼要開 Chrome", "可以打開 Chrome 嗎", "Chrome 開了嗎", "音量是多少",
    # 相似度不足／太短
    "開 chr", "開 abcdef", "開 mac_ai_control",
    # 不是 App 的名詞
    "開會", "開心", "開始", "開玩笑", "關於這個", "開機", "關機", "設定為", "音量設定為",
    # 未知 App 交給 LLM
    "開 Slack", "開 Notion",
    # 睡眠不放寬
    "睡眠", "電腦睡眠", "讓電腦睡眠一下", "讓電腦睡眠吧",
    # 缺少主詞或相對調整，語意不明
    "把音量調大一點", "聲音小聲一點", "螢幕太亮了", "調亮一點", "調高一點", "最亮", "把螢幕關掉",
    # 空查詢／無意義
    "搜尋", "打開", "查看音量", "今天心情不錯",
]


def test_miss_count():
    assert len(MISSES) >= 25


@pytest.mark.parametrize("text", MISSES)
def test_fast_misses_never_execute(monkeypatch, sh, text):
    assert all(m is None for m in fast_matches(text)), text
    # 整條流程：LLM 即使回覆『好』，也不會有任何系統指令
    llm_always(monkeypatch, {"content": "好"})
    r.process_voice_command(text)
    assert sh == []


def test_setting_alias_not_triggered_by_volume_phrase(sh, monkeypatch):
    """『音量設定為 50』要設音量，不可以開系統設定"""
    forbid_llm(monkeypatch)
    r.process_voice_command("音量設定為50")
    assert sh == [VOL(50)]


def test_hint_lines_do_not_misfire_on_short_alias():
    assert r.build_hint_lines("把音量設定為50") == []
    assert "設定→System Settings" in r.build_hint_lines("打開設定")
    assert "google→Google Chrome" in r.build_hint_lines("開 google")
    assert r.build_hint_lines("googles") == []  # 英文別名用詞邊界


# ---------- 歧義保護與候選 ----------
def test_ambiguous_fuzzy_is_not_fast_but_is_a_candidate(monkeypatch):
    monkeypatch.setitem(r.MAC_APP_ALIASES, "slackapp1", "Slack One")
    monkeypatch.setitem(r.MAC_APP_ALIASES, "slackapp2", "Slack Two")
    matcher = r.FastPathMatcher()
    assert matcher.match("開 slackapp") is None
    cand = matcher.match("開 slackapp", candidates=True)
    assert cand.confidence == "candidate"
    assert cand.actions == [("control_mac_app", {"app_name": "Slack One", "action": "open"})]


def test_guard_blocks_candidates_too(monkeypatch):
    monkeypatch.setitem(r.MAC_APP_ALIASES, "slackapp1", "Slack One")
    monkeypatch.setitem(r.MAC_APP_ALIASES, "slackapp2", "Slack Two")
    assert r.FastPathMatcher().match("不要開 slackapp", candidates=True) is None


def test_canonicalize_clause():
    c = r.canonicalize_clause
    assert c("嗯 麻煩幫我開小算盤吧") == "開小算盤"
    assert c("請幫我把 Chrome 打開") == "打開Chrome"
    assert c("深色模式打開") == "打開深色模式"
    assert c("把音量調到50") == "把音量調到50"      # 結尾不是動詞，不改寫
    assert c("搜尋如何關閉") == "搜尋如何關閉"      # 含動作詞的主題不改寫


# ---------- 開已知網站（_r_site）：整句只有「動詞（＋瀏覽器）＋網站名稱（＋網站）」，不經 LLM ----------
YT_HOME, FB_HOME, GH_HOME, GOOGLE_HOME = "https://www.youtube.com", "https://www.facebook.com", "https://github.com", "https://www.google.com"
SITE_HITS = [
    ("打開 YouTube", YT_HOME), ("打開youtube", YT_HOME), ("開 YouTube", YT_HOME), ("上 YouTube", YT_HOME), ("去 YouTube", YT_HOME),
    ("幫我開瀏覽器看 YouTube", YT_HOME), ("開瀏覽器上 YouTube", YT_HOME), ("請打開 YouTube 一下", YT_HOME), ("打開 YouTube 網站", YT_HOME),
    ("打開打開 YouTube", YT_HOME), ("打開ＹＯＵＴＵＢＥ", YT_HOME), ("嗯 打開 YouTube 好不好", YT_HOME),
    ("開油管", YT_HOME), ("幫我開 yt", YT_HOME), ("打開臉書", FB_HOME), ("開 Facebook", FB_HOME), ("幫我開 FB", FB_HOME),
    ("去 GitHub", GH_HOME), ("開 GitHub 首頁", GH_HOME), ("進入 GitHub 官網", GH_HOME),
    ("打開谷歌網站", GOOGLE_HOME), ("開估狗", GOOGLE_HOME),   # 『Google／谷歌』單獨說＝Chrome App（見 test_site_defers_to_same_name_app）
]


@pytest.mark.parametrize("text,home", SITE_HITS, ids=[h[0] for h in SITE_HITS])
def test_site_open_is_fast_and_opens_the_home_page(monkeypatch, sh, text, home):
    forbid_llm(monkeypatch)
    out = r.process_voice_command(text)
    assert sh == [["open", home]] and [s["kind"] for s in out["steps"]] == ["fast"] and out["steps"][0]["status"] == "ok"
    assert out["steps"][0]["intent"] == {"domain": "web", "verb": "open", "slots": {"query": home}, "source": "site"}


SITE_MISSES = [
    "去 YouTube 看影片", "打開 YouTube Music", "打開 You", "打開 Tube", "打開 YouTube 跟 Facebook", "打開瀏覽器",   # 多的受詞、別的名稱、半個詞
    "不要開 YouTube", "別打開臉書", "剛剛開了 YouTube", "我昨天去 GitHub", "YouTube 是什麼", "打開 YouTube 嗎", "為什麼打開 YouTube",  # 守門
    "YouTube", "油管", "我說的是 YouTube", "沒有，我要開的是 YouTube",   # 沒有開啟動詞／糾正句
]


@pytest.mark.parametrize("text", SITE_MISSES)
def test_site_misses_never_open_a_page(monkeypatch, sh, text):
    m = r.FastPathMatcher()
    assert all(m.match(c) is None or m.match(c).actions[0][0] != "web_search_or_open_url" for c in m.split_clauses(r.normalize_utterance(text)))
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: (_ for _ in ()).throw(llm_client.LLMUnavailable("測試")))
    r.process_voice_command(text)
    assert [c for c in sh if c[0] == "open" and c[-1].startswith("http")] == []   # 沒有開任何網頁（開 App 是另一回事）


def test_site_defers_to_same_name_app(monkeypatch, sh, installed_apps):
    forbid_llm(monkeypatch)
    r.process_voice_command("開 Google")                 # 詞庫別名＝Chrome App
    assert sh == [APP(CHROME)]
    sh.clear()
    installed_apps({"YouTube": "YouTube"})               # 已安裝同名 App：本機優先
    r.process_voice_command("打開 YouTube")
    assert sh == [APP("YouTube")]
    sh.clear()
    r.process_voice_command("打開 YouTube 網站")          # 明說『網站』：一律開網站
    assert sh == [["open", YT_HOME]]


def test_site_match_is_a_pure_function(monkeypatch, sh):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("match() 不得執行任何外部指令"))
    m = r.FastPathMatcher()
    for text, _ in SITE_HITS:
        for clause in m.split_clauses(r.normalize_utterance(text)):
            m.match(clause)


@pytest.mark.parametrize("text", ["打開 YouTube", "開瀏覽器看 YouTube"])
def test_site_open_target_browser_priority(front, sh, text):
    front("Safari")
    r.process_voice_command(text)
    assert sh == [["open", "-a", "Safari", YT_HOME]]          # 前景是瀏覽器：開在那裡
    sh.clear()
    front("Finder")
    r.process_voice_command(text)
    assert sh == [["open", YT_HOME]]                           # 前景不是瀏覽器：預設
    sh.clear()
    front(None)
    r.process_voice_command(text)
    assert sh == [["open", YT_HOME]]                           # 讀不到：預設，不猜
    sh.clear()
    front("Safari")
    r.process_voice_command("打開 Chrome，" + text)             # 同句剛開的優先於前景
    assert sh == [APP(CHROME), ["open", "-a", CHROME, YT_HOME]]


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
    forbid_llm(monkeypatch)
    out = r.process_voice_command(text)
    assert sh == [SEARCH(query)]
    assert all(s["kind"] == "fast" for s in out["steps"])


def test_open_browser_then_search_keyword_only(monkeypatch, sh):
    forbid_llm(monkeypatch)
    r.process_voice_command("開 chrome 搜尋一下今天台北天氣")
    assert sh == [APP(CHROME), SEARCH("今天台北天氣", CHROME)]


@pytest.mark.parametrize("text", ["搜尋", "搜尋一下", "查一下", "搜尋嗎", "可以搜尋嗎", "查看音量", "怎麼搜尋", "今天天氣如何", "什麼是搜尋"])
def test_search_without_keyword_or_asking_about_search_is_not_fast(text):
    assert all(m is None for m in fast_matches(text)), text
