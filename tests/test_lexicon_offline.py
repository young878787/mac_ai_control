"""B1：詞庫 schema 擴充（kind／projects／term_misheard／s2t／fillers）與 build_vocab 產物。"""
import json
from pathlib import Path

import build_vocab
import mac_ai.router as r

ROOT = Path(__file__).parent.parent


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
    hints = r.build_hint_lines("打開語音助手專案")
    assert "語音助手→mac_ai_control" in hints
    assert any(h.startswith("專案：") and "mac_ai_control" in h and "other_proj" in h for h in hints)
    assert not any(h.startswith("專案：") for h in r.build_hint_lines("查天氣"))
