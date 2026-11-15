"""錯字語料（計劃書 §14）：帶 STT 式錯誤的句子，必須在後端（正規化＋Fast Path）矯正成功，送出正確的指令。
每句都跑完整的 process_voice_command；subprocess 與前景 App 都是假的，LLM 一被呼叫就失敗（矯正不能靠模型）。
類別代號（id 的前綴）給 scripts/acceptance.py 算各類矯正率用。"""
import subprocess

import pytest

import mac_ai.router as r
from mac_ai.router import llm_client

VSCODE = "Visual Studio Code"
CATEGORIES = {
    "s2t": "簡體", "width": "全形", "num": "中文數字", "filler": "填充詞", "space": "空白", "repeat": "疊詞",
    "lexicon": "詞庫誤聽", "latin": "英文拼錯", "homophone": "同音字", "translit": "音譯", "mixed": "組合",
}


def APP(name):
    return f"open -a {name}"


def KEY(code, *mods):
    using = ", ".join(f"{m} down" for m in mods)
    return f"key code {code}" + (f" using {{{using}}}" if using else "")


VOL = "set volume output volume {}".format
MUTE = "set volume output muted true"
DARKER, BRIGHTER = "key code 145", "key code 144"
LOCK = 'keystroke "q" using {control down, command down}'
DARK_ON = "set dark mode to true"

# (類別代號, 輸入, 前景 App, 預期依序送出的指令片段)
CASES = [
    ("s2t", "打开计算器", None, [APP("Calculator")]),
    ("s2t", "把屏幕调暗一点", None, [DARKER]),
    ("s2t", "关掉声音", None, [MUTE]),
    ("s2t", "上一页", "Safari", [KEY(33, "command")]),
    ("s2t", "关闭窗口", "Finder", [KEY(13, "command")]),
    ("s2t", "全屏", "Safari", [KEY(3, "command", "control")]),
    ("s2t", "保存", VSCODE, [KEY(1, "command")]),
    ("s2t", "打开终端机", VSCODE, [KEY(50, "control")]),
    ("s2t", "新建标签页", "Terminal", [KEY(17, "command")]),
    ("width", "音量調到５０", None, [VOL(50)]),
    ("width", "打開ＳＡＦＡＲＩ", None, [APP("Safari")]),
    ("num", "音量調到五十", None, [VOL(50)]),
    ("num", "螢幕調亮三格", None, [BRIGHTER]),
    ("filler", "嗯幫我那個打開小算盤好不好", None, [APP("Calculator")]),
    ("filler", "欸上一頁啦", "Safari", [KEY(33, "command")]),
    ("filler", "麻煩你存檔一下", VSCODE, [KEY(1, "command")]),
    ("space", "上 一 頁", "Safari", [KEY(33, "command")]),
    ("space", "關 閉 視 窗", "Finder", [KEY(13, "command")]),
    ("repeat", "打開打開小算盤", None, [APP("Calculator")]),
    ("lexicon", "打開科索", None, [APP("Cursor")]),
    ("lexicon", "開谷哥瀏覽器", None, [APP("Google Chrome")]),
    ("lexicon", "幫我開Miss call", None, [APP(VSCODE)]),
    ("lexicon", "因量調到40", None, [VOL(40)]),
    ("lexicon", "淨音", None, [MUTE]),
    ("lexicon", "把營幕調暗", None, [DARKER]),
    ("lexicon", "開中端機", VSCODE, [KEY(50, "control")]),
    ("latin", "打開 Saffari", None, [APP("Safari")]),
    ("latin", "打開 Spotfy", None, [APP("Spotify")]),
    ("latin", "打開 Discrod", None, [APP("Discord")]),
    ("homophone", "音亮調到30", None, [VOL(30)]),
    ("homophone", "金音", None, [MUTE]),
    ("homophone", "螢目調暗一點", None, [DARKER]),
    ("homophone", "所定螢幕", None, [LOCK]),
    ("homophone", "打開深色膜式", None, [DARK_ON]),
    ("homophone", "打開小蒜盤", None, [APP("Calculator")]),
    ("homophone", "打開被忘錄", None, [APP("Notes")]),
    ("homophone", "開終端積", VSCODE, [KEY(50, "control")]),
    ("homophone", "上一業", "Safari", [KEY(33, "command")]),
    ("homophone", "下一業", "Safari", [KEY(30, "command")]),
    ("homophone", "存擋", VSCODE, [KEY(1, "command")]),
    ("homophone", "關閉試窗", "Finder", [KEY(13, "command")]),
    ("homophone", "全螢目", "Safari", [KEY(3, "command", "control")]),
    ("homophone", "開側編欄", VSCODE, [KEY(11, "command")]),
    ("homophone", "最小話", "Finder", [KEY(46, "command")]),
    ("homophone", "清除話面", "Terminal", [KEY(40, "command")]),
    ("homophone", "開新分夜", "Terminal", [KEY(17, "command")]),
    ("translit", "打開撒法利", None, [APP("Safari")]),
    ("translit", "打開史波提fy", None, [APP("Spotify")]),
    ("mixed", "打开计算器然后把因量调到五十", None, [APP("Calculator"), VOL(50)]),
    ("mixed", "嗯幫我上一業啦", "Safari", [KEY(33, "command")]),
    ("mixed", "開終端積吧", VSCODE, [KEY(50, "control")]),
    ("mixed", "金音，然後螢目調暗一點", None, [MUTE, DARKER]),
]
# 不能過度矯正：改對字之後仍是否定／敘述／提問，一個指令都不能送
NEGATIVE = [
    ("不要關閉試窗", "Finder"), ("別存擋", VSCODE), ("要不要上一業", "Safari"), ("剛剛所定螢幕了", None),
    ("不要金音", None), ("不要打開小蒜盤", None), ("我昨天已經存擋了", VSCODE), ("全螢目是什麼", "Safari"),
]


def case_ids(cases):
    seen = {}
    ids = []
    for c in cases:
        seen[c[0]] = seen.get(c[0], 0) + 1
        ids.append(f"{c[0]}-{seen[c[0]]:02d}")
    return ids


@pytest.fixture
def real_like_sh(monkeypatch):
    """記錄 subprocess.run；送鍵腳本回 ok（前景 App 沒變），其餘回空字串、結束碼 0"""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = "ok\n" if cmd[0] == "osascript" and "key code" in cmd[-1] and "frontmost" in cmd[-1] else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail("錯字矯正不能靠 LLM"))


def test_every_category_has_cases():
    assert {c[0] for c in CASES} == set(CATEGORIES) and len(CASES) >= 50


@pytest.mark.parametrize("cat,text,front_app,expected", CASES, ids=case_ids(CASES))
def test_typo_is_corrected_by_backend(real_like_sh, front, no_llm, cat, text, front_app, expected):
    front(front_app)
    out = r.process_voice_command(text)
    assert [s["kind"] for s in out["steps"]] == ["fast"] * len(expected), out["steps"]
    assert all(s["status"] == "ok" for s in out["steps"]), [s["logs"] for s in out["steps"]]
    sent = [" ".join(c) for c in real_like_sh]
    assert len(sent) == len(expected), sent
    for cmd, want in zip(sent, expected):
        assert want in cmd, (cmd, want)
    if front_app is not None and "key code" in expected[0]:  # 送鍵前會先確認前景 App 沒變
        assert f'"/{front_app}.app"' in sent[0]


@pytest.mark.parametrize("text,front_app", NEGATIVE, ids=[f"neg-{i:02d}" for i in range(1, len(NEGATIVE) + 1)])
def test_corrected_negative_sentence_still_runs_nothing(real_like_sh, front, text, front_app):
    """LLM 在離線測試裡不可用（conftest）：被守門擋下的句子走補救路徑，也不能有任何動作"""
    front(front_app)
    out = r.process_voice_command(text)
    assert real_like_sh == [] and all(s["kind"] == "slow" for s in out["steps"])


def test_term_misheard_also_rewrites_search_keywords(real_like_sh, front):
    """已知副作用（記錄現況）：指令詞誤聽是整句替換，直接搜尋時關鍵字也會被改字（『話面』→『畫面』）；
    只有本機 0 筆轉交模型的路徑會用 restore_misheard 還原原話"""
    front(None)
    r.process_voice_command("搜尋話面設計")
    assert real_like_sh == [["open", "https://www.google.com/search?q=%E7%95%AB%E9%9D%A2%E8%A8%AD%E8%A8%88"]]
