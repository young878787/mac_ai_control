"""answer_mode：開網頁還是直接回答（計畫 docs/plans/completed/2026-11-21-web-answer.md §4.1、驗收 A1）。純函式，不碰 IO。
每一列至少 3 句；語料可以加、不能刪。"""
import pytest

from mac_ai.router.fast_path import answer_mode

# 順序 2：明說要看畫面 → open（優先於一切）
OPEN_EXPLICIT = [
    "搜尋台北天氣", "搜索台北天氣", "幫我搜尋台北天氣", "打開瀏覽器查台北天氣", "開瀏覽器查一下台北天氣", "用 Google 搜尋周杰倫",
    "google 一下台北天氣", "上網查台北天氣", "上網找周杰倫", "網路上找 Xcode", "給我看台北天氣", "顯示台北天氣", "在 Google 搜尋台北天氣",
    "開 Safari 查今天會不會下雨", "搜尋台北天氣怎麼樣", "上網查勇士隊誰贏了", "搜尋誰贏了",
]
# 順序 3：問句或要求告知 → answer
ANSWER_ASK = [
    "今天台北會下雨嗎", "美金匯率多少", "周杰倫是誰", "告訴我台北天氣", "跟我說明天天氣", "台積電股價多少", "明天天氣怎麼樣",
    "勇士隊最近一場比賽誰贏了", "昨天比賽誰贏了", "這首歌誰唱的",
]
# 順序 4：查／找＋高頻類別 → answer
ANSWER_LOOKUP_CATEGORY = ["查一下台北天氣", "幫我找一下台北天氣", "查詢明天天氣", "查天氣", "找一下今天會不會下雨"]
# 順序 5：只有類別名詞 → answer
ANSWER_CATEGORY_ONLY = ["台北天氣", "明天天氣", "天氣", "高雄的天氣", "今天溫度"]
# 順序 6：其他中性的查／找 → open（現況）
OPEN_NEUTRAL = ["查周杰倫", "查一下最近的新聞", "找一下 Xcode 教學", "查 Python 文件", "找一下附近的餐廳", "美金匯率", "台積電股價"]


@pytest.mark.parametrize("text", OPEN_EXPLICIT)
def test_explicit_screen_cue_opens_the_web(text):
    assert answer_mode(text) == "open"


@pytest.mark.parametrize("text", ANSWER_ASK + ANSWER_LOOKUP_CATEGORY + ANSWER_CATEGORY_ONLY)
def test_questions_and_category_lookups_answer(text):
    assert answer_mode(text) == "answer"


@pytest.mark.parametrize("text", OPEN_NEUTRAL)
def test_neutral_lookups_without_category_open_the_web(text):
    assert answer_mode(text) == "open"


def test_both_cues_open_wins():
    """兩種線索同時出現：明說優先（同 0006）"""
    assert answer_mode("打開瀏覽器告訴我台北天氣") == "open"
    assert answer_mode("上網查台北會不會下雨") == "open"


def test_row_sizes():
    assert min(len(OPEN_EXPLICIT), len(ANSWER_ASK), len(ANSWER_LOOKUP_CATEGORY), len(ANSWER_CATEGORY_ONLY), len(OPEN_NEUTRAL)) >= 3
