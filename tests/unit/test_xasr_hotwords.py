"""X-ASR 熱詞切 token：中文、英文最長比對、切不出來的略過、去重保序（純函式，不載入 sherpa）"""
from pathlib import Path

import pytest

from mac_ai import config
from mac_ai.stt.xasr_hotwords import encode_hotwords, load_tokens

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stt"
MODEL_TOKENS = config.XASR_MODEL_DIR / config.XASR_TOKENS

TOKENS = {"▁go", "o", "g", "le", "▁c", "▁ch", "h", "ro", "me", "▁sa", "fa", "ri", "▁谷", "▁歌", "▁瀏", "▁覽", "▁器",
          "▁ma", "c", "▁a", "i", "▁control", "▁it", "er", "m", "▁v", "s"}


def test_chinese_each_char_gets_word_prefix():
    assert encode_hotwords(["谷歌瀏覽器"], TOKENS) == (["▁谷 ▁歌 ▁瀏 ▁覽 ▁器"], [])


def test_english_is_lowercased_and_cut_by_longest_match():
    # "▁c" 與 "▁ch" 都在表裡：取最長的 "▁ch"，剩下的 "rome" 依序取 "ro"、"me"
    assert encode_hotwords(["Google"], TOKENS)[0] == ["▁go o g le"]
    assert encode_hotwords(["Chrome"], TOKENS)[0] == ["▁ch ro me"]
    assert encode_hotwords(["iTerm"], TOKENS)[0] == ["▁it er m"]


def test_longest_match_prefers_longer_piece():
    assert encode_hotwords(["ch"], {"▁c", "▁ch", "h"})[0] == ["▁ch"]
    assert encode_hotwords(["ch"], {"▁c", "h"})[0] == ["▁c h"]


def test_each_word_starts_with_prefix_and_separators_split_words():
    # 空白、底線都是詞的分隔；每個詞的第一段加 ▁
    assert encode_hotwords(["google safari"], TOKENS)[0] == ["▁go o g le ▁sa fa ri"]
    assert encode_hotwords(["mac_ai_control"], TOKENS)[0] == ["▁ma c ▁a i ▁control"]


def test_mixed_chinese_and_english_in_one_hint():
    assert encode_hotwords(["谷歌go"], TOKENS)[0] == ["▁谷 ▁歌 ▁go"]


def test_unsplittable_hint_is_skipped_and_reported_not_raised():
    lines, skipped = encode_hotwords(["谷歌", "vscode", "vs code", "歌"], TOKENS)
    assert lines == ["▁谷 ▁歌", "▁歌"]
    assert skipped == ["vscode", "vs code"]  # "vscode" 的 "code" 切不出來


def test_unknown_chinese_char_skips_the_whole_hint():
    assert encode_hotwords(["谷歌乙"], TOKENS) == ([], ["谷歌乙"])


def test_duplicates_after_lowercasing_are_dropped_keeping_first_position():
    lines, skipped = encode_hotwords(["Google", "谷歌", "google", "GOOGLE", "谷歌"], TOKENS)
    assert lines == ["▁go o g le", "▁谷 ▁歌"]
    assert skipped == []


def test_order_is_preserved():
    assert encode_hotwords(["歌", "谷", "歌谷"], TOKENS)[0] == ["▁歌", "▁谷", "▁歌 ▁谷"]


def test_blank_and_punctuation_only_lines():
    lines, skipped = encode_hotwords(["", "   ", "谷", "...", "_"], TOKENS)
    assert lines == ["▁谷"]
    assert skipped == ["...", "_"]  # 空白行直接忽略；只有標點或底線的行沒有任何詞，算略過


def test_hint_whitespace_around_is_trimmed():
    assert encode_hotwords(["  谷歌  \n"], TOKENS)[0] == ["▁谷 ▁歌"]


def test_does_not_mutate_inputs_and_accepts_any_iterable():
    tokens = frozenset(TOKENS)
    assert encode_hotwords(iter(["谷"]), tokens)[0] == ["▁谷"]
    assert tokens == TOKENS


def test_load_tokens_reads_first_column(tmp_path):
    f = tmp_path / "tokens.txt"
    f.write_text("<blk> 0\n▁, 2\n▁谷 10\ngo 11\n\n", encoding="utf-8")
    assert load_tokens(f) == {"<blk>", "▁,", "▁谷", "go"}


@pytest.mark.skipif(not MODEL_TOKENS.is_file(), reason=f"沒有 X-ASR 模型的 token 表：{MODEL_TOKENS}（下載方式見 README）")
def test_stt_hints_snapshot_encodes_to_the_43_lines_validated_in_p0():
    """用 2026-11-21 的 stt_hints.txt（54 行）與模型真正的 token 表：結果必須與 P0 驗證過的熱詞檔逐行相同（43 行）"""
    hints = (FIXTURES / "xasr_hints_2026-11-21.txt").read_text(encoding="utf-8").splitlines()
    expected = (FIXTURES / "xasr_hotwords_2026-11-21.txt").read_text(encoding="utf-8").splitlines()
    lines, skipped = encode_hotwords(hints, load_tokens(MODEL_TOKENS))
    assert len(hints) == 54 and len(expected) == 43
    assert lines == expected
    assert skipped == ["vscode", "vs code"]
