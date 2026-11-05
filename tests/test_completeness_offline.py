"""partial 文字的完整度分類：complete（可早收）／incomplete（多等）／unknown。"""
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
    # 半個詞、聽不懂、聊天：交給預設門檻
    ("打開小", UNKNOWN), ("今天天氣怎麼樣", UNKNOWN), ("我想聽音樂", UNKNOWN), ("你好", UNKNOWN),
    ("", UNKNOWN), ("   ", UNKNOWN), ("，。", UNKNOWN),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_classify_without_wake_word(text, expected):
    assert classify(text, wake_enabled=False) == expected


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
    from mac_ai.router import fast_path

    def boom(*a, **k):
        raise AssertionError("分類不該執行動作")

    monkeypatch.setattr(fast_path.Match, "run", boom)
    monkeypatch.setattr(fast_path, "run_tool", boom)
    assert classify("打開小算盤", wake_enabled=False) == COMPLETE
