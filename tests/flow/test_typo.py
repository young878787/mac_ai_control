"""錯字語料（support/typo_corpus.py）：帶 STT 式錯誤的句子必須在後端（正規化＋Fast Path）矯正，LLM 一被呼叫就失敗。類別代號（id 前綴）給 scripts/acceptance.py 算矯正率"""
import pytest

import mac_ai.router as r
from support.llm import forbid_llm
from support.typo_corpus import CASES, CATEGORIES, NEGATIVE, case_ids


@pytest.fixture
def no_llm(monkeypatch):
    forbid_llm(monkeypatch, "錯字矯正不能靠 LLM")


def test_every_category_has_cases():
    assert {c[0] for c in CASES} == set(CATEGORIES) and len(CASES) >= 50


@pytest.mark.parametrize("cat,text,front_app,expected", CASES, ids=case_ids(CASES))
def test_typo_is_corrected_by_backend(sh, native_calls, front, no_llm, cat, text, front_app, expected):
    front(front_app)
    out = r.process_voice_command(text)
    assert [s["kind"] for s in out["steps"]] == ["fast"] * len(expected), out["steps"]
    assert all(s["status"] == "ok" for s in out["steps"]), [s["logs"] for s in out["steps"]]
    assert native_calls == [e[len("native:"):] for e in expected if e.startswith("native:")]
    sent = [" ".join(c) for c in sh]
    want_sh = [e for e in expected if not e.startswith("native:")]
    assert len(sent) == len(want_sh), sent
    for cmd, want in zip(sent, want_sh):
        assert want in cmd, (cmd, want)


@pytest.mark.parametrize("text,front_app", NEGATIVE, ids=[f"neg-{i:02d}" for i in range(1, len(NEGATIVE) + 1)])
def test_corrected_negative_sentence_still_runs_nothing(sh, native_calls, front, text, front_app):
    """LLM 在離線測試裡不可用（conftest）：被守門擋下的句子走補救路徑，也不能有任何動作"""
    front(front_app)
    out = r.process_voice_command(text)
    assert sh == [] and native_calls == [] and all(s["kind"] == "slow" for s in out["steps"])


def test_term_misheard_also_rewrites_search_keywords(sh, front):
    """已知副作用（記錄現況）：指令詞誤聽是整句替換，直接搜尋時關鍵字也會被改字（『話面』→『畫面』）；
    只有本機 0 筆轉交模型的路徑會用 restore_misheard 還原原話（聯合搜尋也是直接搜尋，同樣不還原）"""
    front(None)
    r.process_voice_command("搜尋話面設計")
    assert [c for c in sh if c[0] == "open"] == [["open", "https://www.google.com/search?q=%E7%95%AB%E9%9D%A2%E8%A8%AD%E8%A8%88"]]
    assert [c[-1] for c in sh if c[0] == "mdfind"] == ['kMDItemFSName == "*畫面設計*"cd']
