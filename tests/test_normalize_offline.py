"""B2：正規化。混亂輸入（全形、簡體、中文數字、誤聽、疊詞、空白）必須逐字等於預期。"""
import json

import pytest

import mac_ai.router as r
from mac_ai.router import normalize

CASES = [
    # --- 全形／空白 ---
    ("ＣＨＲＯＭＥ", "CHROME"),
    ("５０％", "50%"),
    ("打開　Chrome", "打開 Chrome"),
    ("  開   Chrome  ", "開 Chrome"),
    ("打開 VS Code", "打開 VS Code"),            # 中英之間的空白保留
    ("嗯 打開 Chrome", "嗯打開 Chrome"),          # 中文與中文之間的空白移除
    ("打 開 Chrome", "打開 Chrome"),
    ("ｃｈｒｏｍｅ 打开", "chrome 打開"),
    # --- 簡轉繁 ---
    ("打开浏览器", "打開瀏覽器"),
    ("关掉备忘录", "關掉備忘錄"),
    ("把屏幕调暗一点", "把螢幕調暗一點"),
    ("搜索台北天氣", "搜尋台北天氣"),
    ("打开计算器", "打開小算盤"),
    ("声音关掉", "聲音關掉"),
    ("开启设置", "開啟設定"),
    ("锁定屏幕", "鎖定螢幕"),
    ("识别这个网页", "識別這個網頁"),
    ("开启项目", "開啟專案"),
    # --- 誤聽詞（App／指令詞）---
    ("科索", "Cursor"),
    ("打開科索", "打開Cursor"),
    ("開谷哥瀏覽器", "開Google Chrome"),
    ("開谷哥", "開Google Chrome"),
    ("開VS扣", "開Visual Studio Code"),
    ("開 vs扣", "開 Visual Studio Code"),       # 不分大小寫
    ("幫我開Miss call", "幫我開Visual Studio Code"),  # 實測誤聽
    ("開 miss call", "開 Visual Studio Code"),
    ("因量調到40", "音量調到40"),
    ("英量調小", "音量調小"),
    ("淨音", "靜音"),
    ("營幕調暗", "螢幕調暗"),
    ("打開中端機", "打開終端機"),
    ("开谷哥浏览器", "開Google Chrome"),          # 簡轉繁後才命中誤聽詞
    # --- 中文數字（只在數值語境）---
    ("音量調到五十", "音量調到50"),
    ("音量調到 五十", "音量調到50"),
    ("百分之三十", "30%"),
    ("百分之百", "100%"),
    ("把音量調到百分之三十", "把音量調到30%"),
    ("音量調成五十趴", "音量調成50%"),
    ("螢幕調暗兩格", "螢幕調暗2格"),
    ("音量五十", "音量50"),
    ("音量調到一百", "音量調到100"),
    ("音量調到一百二", "音量調到120"),
    ("音量調到一百零五", "音量調到105"),
    ("音量調到十五", "音量調到15"),
    ("音量設為二十五", "音量設為25"),
    ("音量調到零", "音量調到0"),
    ("亮度調到五格", "亮度調到5格"),
    # --- 不可轉換 ---
    ("螢幕調暗一點", "螢幕調暗一點"),
    ("螢幕調暗一點點", "螢幕調暗一點點"),
    ("幫我開一下 Chrome", "幫我開一下 Chrome"),
    ("開一個視窗", "開一個視窗"),
    ("音量調到一半", "音量調到一半"),
    ("調到一點", "調到一點"),
    ("開開心心", "開開心心"),
    # --- 疊詞 ---
    ("打開打開 Chrome", "打開 Chrome"),
    ("開啟開啟 Spotify", "開啟 Spotify"),
    ("關掉關掉關掉 Notes", "關掉 Notes"),
    # --- 綜合 ---
    ("請幫我打开谷哥浏览器，然后把声音调到五十", "請幫我打開Google Chrome,然後把聲音調到50"),
    ("你好", "你好"),
    ("", ""),
]


def test_case_count():
    assert len(CASES) >= 40


@pytest.mark.parametrize("raw,expected", CASES, ids=[f"{i}:{c[0]}" for i, c in enumerate(CASES)])
def test_normalize_exact(raw, expected):
    assert r.normalize_utterance(raw) == expected


def test_normalize_is_idempotent():
    for raw, _ in CASES:
        once = r.normalize_utterance(raw)
        assert r.normalize_utterance(once) == once, raw


@pytest.mark.parametrize("text,expected", [
    ("五十", 50), ("十", 10), ("十五", 15), ("二十", 20), ("一百", 100), ("一百零五", 105),
    ("一百二", 120), ("一百二十", 120), ("百", 100), ("零", 0), ("兩百", 200), ("九十九", 99),
])
def test_zh_to_int(text, expected):
    assert r.zh_to_int(text) == expected


def test_zh_to_int_rejects_non_numerals():
    assert r.zh_to_int("很多") is None


def test_to_traditional_only_touches_table_words():
    assert r.to_traditional("请打开浏览器") == "請打開瀏覽器"
    assert r.to_traditional("台北天氣") == "台北天氣"


def test_normalization_is_logged_only_when_changed(sh):
    r.process_voice_command("打開科索")
    r.process_voice_command("你好")  # 沒有被改動 → 不記錄（也不會有 App 動作）
    r.process_voice_command("開 Chrome，　靜音")  # 只有全形標點／空白的機械性轉換 → 不記錄
    r.process_voice_command("音量調到五十")  # 中文數字屬詞庫規則的改動 → 記錄
    records = [json.loads(line) for line in normalize.NORMALIZE_LOG.read_text(encoding="utf-8").splitlines()]
    assert [(x["raw"], x["normalized"]) for x in records] == [("打開科索", "打開Cursor"), ("音量調到五十", "音量調到50")]


def test_misheard_sources_are_labelled():
    sources = r.LEXICON["misheard_sources"]
    assert sources["科索"] == "observed"
    assert sources["vs扣"] == "seed"
    assert sources["因量"] == "seed"
