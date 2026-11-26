"""句子完整度 classify（熱路徑，不得碰 IO）與常用功能前半句不被提早收句（注入時鐘）"""
import pytest

from mac_ai.stt.completeness import COMPLETE, INCOMPLETE, UNKNOWN, classify

CASES = [
    # 完整的單句指令
    ("打開小算盤", COMPLETE), ("關掉Safari", COMPLETE), ("關掉 Safari", COMPLETE), ("音量調到30", COMPLETE),
    ("音量調到五十", COMPLETE), ("靜音", COMPLETE), ("鎖定螢幕", COMPLETE), ("打開Line", COMPLETE),
    ("關掉音樂", COMPLETE), ("取消靜音", COMPLETE), ("打開深色模式", COMPLETE),
    # 多步驟：每個子句都命中才算完整
    ("打開Safari然後搜尋天氣", COMPLETE), ("打開小算盤，關掉Safari", COMPLETE),
    # 句尾是接續詞：話還沒說完
    ("打開Safari然後", INCOMPLETE), ("打開小算盤和", INCOMPLETE), ("打開小算盤還有", INCOMPLETE),
    ("打開小算盤，", COMPLETE),  # 句尾標點不影響
    ("音量調到", INCOMPLETE), ("把音量調到", INCOMPLETE), ("把", INCOMPLETE), ("幫我", INCOMPLETE),
    # 只有動詞、缺受詞
    ("打開", INCOMPLETE), ("開啟", INCOMPLETE), ("關掉", INCOMPLETE), ("關閉", INCOMPLETE), ("搜尋", INCOMPLETE),
    ("幫我打開", INCOMPLETE), ("音量", INCOMPLETE), ("亮度", INCOMPLETE),
    # 常用功能（前景視窗的快捷鍵）：整句命中才算完整；後面可能還有『分頁／視窗』的不能早收
    ("關閉視窗", COMPLETE), ("上一頁", COMPLETE), ("全螢幕", COMPLETE), ("存檔", COMPLETE), ("開終端機", COMPLETE),
    ("再往下滑", COMPLETE), ("往下滑一點", COMPLETE), ("往下翻", COMPLETE),
    ("往下", UNKNOWN), ("再往下", UNKNOWN),  # 2026-11-17 決策：單獨的『往下』不收，避免『往下……一頁』停頓時被切成捲動
    ("關掉這個", INCOMPLETE), ("關閉這個", INCOMPLETE), ("開新", INCOMPLETE), ("打開新", INCOMPLETE),
    # 開已知網站：整句命中才算完整；名稱還沒說完、後面還有東西的不能早收
    ("打開 YouTube", COMPLETE), ("開油管", COMPLETE), ("上 YouTube", COMPLETE), ("幫我開瀏覽器看 YouTube", COMPLETE), ("開 GitHub 首頁", COMPLETE),
    ("打開 YouTube 然後", INCOMPLETE), ("打開 YouTube，", COMPLETE), ("打開 You", UNKNOWN), ("去 YouTube 看影片", UNKNOWN), ("打開 YouTube Music", UNKNOWN),
    # 半個詞、聽不懂、聊天：交給預設門檻
    ("打開小", UNKNOWN), ("今天天氣怎麼樣", COMPLETE), ("今天天氣很好", UNKNOWN), ("你好", UNKNOWN),  # 天氣問句走 Fast Path 回答，所以完整；敘述不收
    ("", UNKNOWN), ("   ", UNKNOWN), ("，。", UNKNOWN),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_classify_without_wake_word(text, expected):
    assert classify(text, wake_enabled=False) == expected


# ---------- C3：點歌句。歌名還沒說（動詞後面是空的、句尾是『的』）不完整；其他命中點歌規則的句子完整 ----------
MUSIC_INCOMPLETE = ["我想聽", "播放", "來一首", "放首", "我想聽周杰倫的", "想聽", "放一首", "來點", "我想聽一首"]


@pytest.mark.parametrize("text", MUSIC_INCOMPLETE)
def test_c3_music_without_a_title_is_incomplete(text):
    assert classify(text, wake_enabled=False) == INCOMPLETE


def test_c3_every_matched_music_sentence_is_complete():
    """語料沿用 tests/flow/test_route_music.py 的 C1；只有動詞、沒有歌名的句子（MUSIC_INCOMPLETE）除外"""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("route_music_corpus", Path(__file__).parents[1] / "flow" / "test_route_music.py")
    corpus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(corpus)
    sentences = [t for t, _ in corpus.C1_POSITIVE if t not in MUSIC_INCOMPLETE]
    assert len(sentences) >= 40
    assert [t for t in sentences if classify(t, wake_enabled=False) != COMPLETE] == []


def test_there_are_at_least_30_cases():
    assert len(CASES) >= 30


def test_wake_word_prefix_is_ignored_in_both_modes():
    for enabled in (False, True):
        assert classify("露西婭打開小算盤", wake_enabled=enabled) == COMPLETE
        assert classify("露西婭，打開小算盤", wake_enabled=enabled) == COMPLETE
        assert classify("露西婭打開", wake_enabled=enabled) == INCOMPLETE


def test_wake_word_alone():
    assert classify("露西婭", wake_enabled=True) == INCOMPLETE  # 指令還在後面，多等一下
    assert classify("露西婭", wake_enabled=False) == UNKNOWN


def test_stt_misheard_variants_still_count_as_commands():
    assert classify("打開計算機", wake_enabled=False) == COMPLETE


def test_classify_never_runs_actions(monkeypatch):
    from mac_ai.router import actions

    def boom(*a, **k):
        raise AssertionError("分類不該執行動作")

    monkeypatch.setattr(actions, "_dispatch", boom)  # 所有執行都經 run_tool → _dispatch
    assert classify("打開小算盤", wake_enabled=False) == COMPLETE


# ---------- 取代人工驗收 U3：常用功能的前半句不會被提早收句（真的 classify ＋ 注入時鐘）----------
@pytest.mark.parametrize("head,full", [("關掉這個", "關掉這個分頁"), ("關閉這個", "關閉這個視窗"), ("開新", "開新視窗"), ("打開新", "打開新分頁")])
def test_u3_half_sentence_waits_then_full_sentence_flushes(head, full):
    from support.endpoint import Sim

    from mac_ai.stt.completeness import classify
    s = Sim(cls=classify)
    s.speech(0.8)
    s.partial(head)
    s.silence(1.0)  # 句中停頓 1 秒：完整句 0.35～0.6 秒就會收，半句要等 2 秒
    assert s.decisions == [], f"「{head}」被提早收句"
    s.speech(0.5)
    s.partial(full)
    waited = s.silence_until_decision()
    assert [(d.partial, d.cls) for d in s.decisions] == [(full, "complete")] and waited <= 0.7


@pytest.mark.parametrize("text", ["關閉視窗", "上一頁", "全螢幕", "存檔", "上一業"])
def test_u3_complete_shortcut_flushes_early(text):
    from support.endpoint import Sim

    from mac_ai.stt.completeness import classify
    s = Sim(cls=classify)
    s.speech(0.6)
    s.partial(text)
    assert s.silence_until_decision() <= 0.7 and (s.decisions[0].partial, s.decisions[0].cls) == (text, "complete")
