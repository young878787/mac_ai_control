"""詞庫 schema（kind／projects／term_misheard／s2t／fillers）、build_vocab 產物、詞庫自洽檢查與常用功能的安全範圍"""
import json
import re
from pathlib import Path

import pytest

import build_vocab
import mac_ai.router as r

ROOT = Path(__file__).resolve().parents[2]


def test_no_collision_across_apps_projects_and_terms():
    owners = {}

    def claim(word, owner):
        key = word.lower()
        assert owners.setdefault(key, owner) == owner, f"{word!r} 同時屬於 {owners[key]} 與 {owner}"

    for app in r.LEXICON["apps"]:
        for w in [app["name"], *app["aliases"], *app["misheard"]]:
            claim(w, f"app:{app['name']}")
    for p in r.LEXICON["projects"]:
        for w in [p["name"], *p["aliases"]]:
            claim(w, f"project:{p['name']}")
    for t in r.LEXICON["term_misheard"]:
        claim(t["text"], f"term:{t['text']}")
        assert t["to"].lower() not in {k for k in r.TERM_MISHEARD}, "誤聽詞的目標不可再是誤聽詞"
    for w in r.LEXICON["terms"]:
        claim(w, f"term:{w}")
    for w in [*r.LEXICON["wake_words"]["names"], *r.LEXICON["wake_words"]["misheard"]]:
        claim(w, "wake_words")


def test_every_app_has_a_known_kind_and_google_belongs_to_chrome_only():
    assert {a["kind"] for a in r.LEXICON["apps"]} <= {"browser", "editor", "other"}
    assert r.MAC_APP_ALIASES["google"] == "Google Chrome"
    assert r.APP_KIND["Google Chrome"] == "browser" and r.APP_KIND["Cursor"] == "editor"


def test_misheard_entries_carry_a_source():
    raw = json.loads((ROOT / "vocab" / "lexicon.json").read_text(encoding="utf-8"))
    entries = [m for a in raw["apps"] for m in a["misheard"]] + raw["term_misheard"] + raw["wake_words"]["misheard"]
    assert entries and all(isinstance(e, dict) and e["source"] in ("observed", "seed") for e in entries)


def test_old_string_misheard_format_still_loads(tmp_path):
    p = tmp_path / "lex.json"
    p.write_text(json.dumps({"apps": [{"name": "Cursor", "aliases": ["cursor"], "misheard": ["科索"]}]}), encoding="utf-8")
    lex = r.load_lexicon(p)
    assert lex["apps"][0]["misheard"] == ["科索"]
    assert lex["misheard_sources"] == {"科索": "observed"}


def test_build_vocab_includes_projects_and_excludes_misheard_and_s2t(tmp_path):
    build_vocab.build(build_vocab.LEXICON, tmp_path)
    hints = (tmp_path / "stt_hints.txt").read_text(encoding="utf-8").splitlines()
    assert "mac_ai_control" in hints and "語音助手" in hints and "google" in hints
    assert len(hints) <= 200  # macos-stt 建議上限
    forbidden = [m for a in r.LEXICON["apps"] for m in a["misheard"]] + list(r.TERM_MISHEARD) + list(r.S2T) \
        + r.LEXICON["wake_words"]["misheard"]
    assert not {h.lower() for h in hints} & {f.lower() for f in forbidden}
    userdict = (tmp_path / "userdict.txt").read_text(encoding="utf-8")
    assert "科索 100000 n" in userdict and "VS扣 100000 n" in userdict


def test_build_vocab_includes_wake_word_names_only(tmp_path):
    build_vocab.build(build_vocab.LEXICON, tmp_path)
    hints = (tmp_path / "stt_hints.txt").read_text(encoding="utf-8").splitlines()
    assert "露西婭" in hints and "露醬" in hints and "露西亞" in hints
    assert not set(r.LEXICON["wake_words"]["misheard"]) & set(hints)


def test_wake_words_misheard_loads_flat_with_sources():
    wake = r.LEXICON["wake_words"]
    assert wake["names"] == ["露西婭", "露醬", "露西亞"]
    assert all(isinstance(m, str) for m in wake["misheard"])
    assert "露西亞" not in wake["misheard"]  # 已升為標準名稱，不重複列在誤聽
    sources = r.LEXICON["misheard_sources"]
    assert sources["露西雅"] == "seed"
    assert all(sources[w.lower()] == "observed" for w in ("Lucy", "西亞", "的路線"))
    assert "不行" not in wake["misheard"]  # 太常見，會誤觸發


def test_finder_aliases_cover_windows_style_names():
    for alias in ("檔案總管", "檔案資料夾", "訪達", "finder"):
        assert r.MAC_APP_ALIASES[alias.lower()] == "Finder"


def test_miss_call_is_observed_vscode_misheard():
    assert r.APP_MISHEARD["miss call"] == "Visual Studio Code"
    assert r.LEXICON["misheard_sources"]["miss call"] == "observed"


def test_checked_in_vocab_files_are_up_to_date(tmp_path):
    build_vocab.build(build_vocab.LEXICON, tmp_path)
    for name in ("stt_hints.txt", "userdict.txt"):
        assert (tmp_path / name).read_text(encoding="utf-8") == (ROOT / "vocab" / name).read_text(encoding="utf-8"), \
            f"vocab/{name} 過期，請執行 python build_vocab.py"


def test_project_hint_lists_known_projects(projects):
    hints = r.build_hint_lines("打開那個專案")
    assert any(h.startswith("專案：") and "mac_ai_control" in h and "other_proj" in h for h in hints)
    assert not any(h.startswith("專案：") for h in r.build_hint_lines("查天氣"))
    # 別名已命中就不附清單（實測附了清單，模型會選清單第一個）
    assert r.build_hint_lines("打開語音助手專案") == ["語音助手→mac_ai_control"]


# ---------- 詞庫 ----------


def test_misheard_not_in_aliases():
    assert not set(r.APP_MISHEARD) & set(r.MAC_APP_ALIASES)


def test_build_vocab_outputs(tmp_path):
    build_vocab.build(build_vocab.LEXICON, tmp_path)
    hints = (tmp_path / "stt_hints.txt").read_text(encoding="utf-8").splitlines()
    assert "Cursor" in hints and "Google Chrome" in hints and "小算盤" in hints
    assert "科索" not in hints  # 誤聽詞不可餵給 STT 當提示
    userdict = (tmp_path / "userdict.txt").read_text(encoding="utf-8")
    assert "科索 100000 n" in userdict
    assert all(len(line.split(" ")) == 3 for line in userdict.splitlines())  # 無含空白的詞

    jieba = pytest.importorskip("jieba")
    tk = jieba.Tokenizer()
    tk.load_userdict(str(tmp_path / "userdict.txt"))
    assert "科索" in tk.lcut("打開科索")
    assert "谷歌瀏覽器" in tk.lcut("開谷歌瀏覽器")


# ---------- 常用功能 app_shortcuts 的安全範圍（紅線 8，ADR 0008、0019） ----------
_PAGING_CODES = {116, 121}  # Page Up／Page Down：唯一允許不帶 ⌘ 送出的按鍵
_FORBIDDEN_CODES = {12, 51, 117}  # Q（⌘Q 結束程式）、Delete、Forward Delete
_FORBIDDEN_WORDS = re.compile(r"結束|退出|刪除|刪掉|垃圾|quit|exit|delete|trash|remove", re.IGNORECASE)


def check_shortcuts(shortcuts: list[dict]) -> list[str]:
    """送按鍵只允許 ⌘ 組合鍵與翻頁鍵；不放結束程式、刪除類（說法 phrases 不檢查：「退出全螢幕」是合法說法）"""
    errors = []
    for s in shortcuts:
        sid = s["id"]
        if _FORBIDDEN_WORDS.search(f"{sid} {s['label']}"):
            errors.append(f"{sid}：名稱像結束程式或刪除類")
        for b in s["bindings"]:
            mods = b.get("mods", [])
            if "code" in b and "command" not in mods and not (b["code"] in _PAGING_CODES and not mods):
                errors.append(f"{sid}：按鍵 {b['code']} {mods} 不是 ⌘ 組合鍵或翻頁鍵")
            if b.get("code") in _FORBIDDEN_CODES or str(b.get("char", "")).upper() == "Q":
                errors.append(f"{sid}：按鍵 {b.get('code')}／{b.get('char')} 是結束程式或刪除類")
            if "char" in b and "command" not in mods:
                errors.append(f"{sid}：選單快捷鍵 {b['char']!r} 沒有 ⌘")
            if b.get("window", "close") not in {"close", "minimize", "fullscreen"}:
                errors.append(f"{sid}：未知的視窗操作 {b['window']!r}")
            texts = [b.get("script", ""), b.get("button", ""), json.dumps(b.get("path", []), ensure_ascii=False)]
            if any(_FORBIDDEN_WORDS.search(t) for t in texts):
                errors.append(f"{sid}：選單、按鈕或指令像結束程式或刪除類")
    return errors


def test_app_shortcuts_stay_in_safe_range():
    shortcuts = json.loads((ROOT / "vocab" / "lexicon.json").read_text(encoding="utf-8"))["app_shortcuts"]
    assert len(shortcuts) > 10 and check_shortcuts(shortcuts) == []


@pytest.mark.parametrize("binding", [
    {"char": "K", "code": 40, "mods": []},               # 沒有 ⌘ 的字元鍵（注音輸入法會吃掉）
    {"code": 50, "mods": ["control"]},                   # ⌃`
    {"code": 116, "mods": ["shift"]},                    # 翻頁鍵只允許單獨送
    {"char": "Q", "code": 12, "mods": ["command"]},      # ⌘Q 結束程式
    {"code": 51, "mods": ["command"]},                   # ⌘Delete 丟到垃圾桶
    {"path": [["檔案", "File"], ["丟到垃圾桶", "Move to Trash"]]},
    {"script": "quit"},
    {"window": "zoom"},
])
def test_app_shortcuts_counterexamples(binding):
    assert check_shortcuts([{"id": "x", "label": "x", "bindings": [{"when": {}, **binding}]}])


def test_app_shortcuts_forbidden_name():
    assert check_shortcuts([{"id": "quit_app", "label": "結束程式", "bindings": []}])
