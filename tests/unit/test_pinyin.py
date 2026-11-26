"""pinyin（macOS ICU Han-Latin; Latin-ASCII; Lower，ctypes 呼叫）：放寬後的無聲調音節。
放寬：zh→z、ch→c、sh→s、ing→in、eng→en。ICU 是本機系統函式庫，不連網、不開子程序。"""
import pytest

from mac_ai.router import pinyin
from mac_ai.router.pinyin import pinyin_key, pinyin_syllables, pinyin_units


@pytest.mark.parametrize("group", [
    ["周杰倫", "周杰論", "走杰倫"],  # zh→z 放寬：走杰倫（zou）和周杰倫（zhou）同音
    ["稻香", "到香"],
    ["晴天", "情天", "青天"],  # qing→qin 放寬：情天、青天都和晴天同 key
    ["光年之外", "光年知外"],  # zhi→zi
    ["鄧紫棋", "鄧子棋"],  # eng→en、zi
    ["告白氣球", "告白汽球"],
])
def test_homophones_share_key(group):
    keys = {pinyin_key(t) for t in group}
    assert len(keys) == 1, {t: pinyin_key(t) for t in group}


@pytest.mark.parametrize("a,b", [("晴天娃娃", "晴天"), ("周杰倫", "周杰"), ("稻香", "七里香"), ("晴天", "陰天")])
def test_different_words_differ(a, b):
    assert pinyin_key(a) != pinyin_key(b)


def test_syllables_relaxed():
    assert pinyin_syllables("周杰倫") == ["zou", "jie", "lun"]
    assert pinyin_syllables("晴天") == ["qin", "tian"]
    assert pinyin_syllables("鄧紫棋") == ["den", "zi", "qi"]
    assert pinyin_syllables("音樂") == ["yin", "le"]
    assert pinyin_syllables("女") == ["nu"]  # ü 去掉聲調與分音符號


def test_english_and_mixed():
    assert pinyin_syllables("Shape of You") == ["shape", "of", "you"]  # 小寫；連續英數字一個 token；不放寬（sing 不變成 sin）
    assert pinyin_syllables("Sing, Sing!") == ["sing", "sing"]
    assert pinyin_syllables("周杰倫Jay Chou!") == ["zou", "jie", "lun", "jay", "chou"]
    assert pinyin_syllables("Beyoncé") == ["beyonce"]
    assert pinyin_syllables("ＡＢ１２") == ["ab12"]  # 全形 → 半形
    assert pinyin_syllables("7里香") == ["7", "li", "xiang"]


def test_key_drops_spaces_and_punctuation():
    assert pinyin_key("Shape of You") == "shapeofyou" == pinyin_key("shape-of-you!")
    assert pinyin_key("  稻 香 ") == pinyin_key("稻香") == "daoxiang"
    assert pinyin_key("") == "" and pinyin_syllables("，。！ ") == []


def test_units_pair_literal_with_syllable():
    assert pinyin_units("周杰倫Jay") == [("周", "zou"), ("杰", "jie"), ("倫", "lun"), ("jay", "jay")]
    assert pinyin_units("Taylor Swift") == [("taylor", "taylor"), ("swift", "swift")]


def test_traditional_and_simplified_share_key():
    assert pinyin_key("告白氣球") == pinyin_key("告白气球")


def test_no_icu_falls_back_to_literal(monkeypatch):
    """ICU 取不到（非 macOS）：不丟例外，漢字退化成原字，字面比對仍可用"""
    monkeypatch.setattr(pinyin, "_icu_transliterate", lambda text: text)
    pinyin._han_syllable.cache_clear()
    try:
        assert pinyin_syllables("周杰倫") == ["周", "杰", "倫"]
        assert pinyin_syllables("Jay 周") == ["jay", "周"]
    finally:
        monkeypatch.undo()
        pinyin._han_syllable.cache_clear()
    assert pinyin_syllables("周杰倫") == ["zou", "jie", "lun"]
