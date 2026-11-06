"""喚醒詞閘門：句首喚醒詞、只喚醒後待命、未喚醒不執行、待命逾時（時鐘可注入，不真的等）。"""
import pytest

from mac_ai.wake import WakeGate, lexicon_wake_words, split_wake

WORDS = ["露西婭", "露醬"]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def gate(clock):
    return WakeGate(WORDS, session_s=8.0, clock=clock, enabled=True)


# ---------- split_wake ----------
@pytest.mark.parametrize("text, rest", [
    ("露醬，打開小算盤", "打開小算盤"),
    ("露西婭 打開小算盤", "打開小算盤"),
    ("露醬打開小算盤", "打開小算盤"),
    ("露醬、打開小算盤。", "打開小算盤。"),
    ("，露醬，打開小算盤", "打開小算盤"),  # 句首的標點不影響
    ("露，醬打開小算盤", "打開小算盤"),  # 詞中被插入標點
    ("露西婭", ""),
    ("露醬。", ""),
    ("　露醬！　把音量調到 30", "把音量調到 30"),  # 全形空白與標點
])
def test_split_wake_at_sentence_start(text, rest):
    assert split_wake(text, WORDS) == rest


@pytest.mark.parametrize("text", [
    "打開小算盤",
    "我跟露醬說打開小算盤",  # 喚醒詞不在句首
    "露西打開小算盤",  # 只有一部分
    "",
    "，。",
])
def test_split_wake_requires_exact_prefix(text):
    assert split_wake(text, WORDS) is None


def test_split_wake_ignores_case_and_width():
    assert split_wake("ＬＵＣＩＡ，開 Chrome", ["Lucia"]) == "開 Chrome"


def test_split_wake_prefers_longest_word():
    assert split_wake("露西婭娜，開", ["露西婭", "露西婭娜"]) == "開"


# ---------- 詞庫 ----------
@pytest.mark.parametrize("text", ["露西婭，打開小算盤", "露醬，打開小算盤", "露西亞，打開小算盤", "露西娅，打開小算盤", "露酱，打開小算盤"])
def test_lexicon_names_and_misheard_variants_wake(text):
    assert WakeGate(clock=Clock(), enabled=True).feed(text).command == "打開小算盤"


@pytest.mark.parametrize("text", [
    "Lucy幫我開Google", "lucy，幫我開Google", "西亞幫我開Google", "的路線幫我開Google",  # 實測 STT 輸出
])
def test_observed_misheard_wake_words_wake(text):
    r = WakeGate(clock=Clock(), enabled=True).feed(text)
    assert (r.status, r.command) == ("command", "幫我開Google")


@pytest.mark.parametrize("text", ["不行幫我開一個檔案總管", "我跟Lucy說開Google", "你好西亞"])
def test_common_phrases_and_non_prefix_do_not_wake(text):
    assert WakeGate(clock=Clock(), enabled=True).feed(text).status == "ignored"


def test_lexicon_wake_words_include_names_and_variants():
    words = lexicon_wake_words()
    assert {"露西婭", "露醬", "露西亞", "露酱", "Lucy", "西亞", "的路線"} <= set(words)
    assert "不行" not in words


def test_gate_without_wake_words_is_rejected():
    with pytest.raises(ValueError):
        WakeGate([], enabled=True)


# ---------- 閘門行為 ----------
def test_wake_word_with_command_executes_without_waiting(gate):
    r = gate.feed("露醬，打開小算盤")
    assert (r.status, r.command) == ("command", "打開小算盤")


def test_wake_word_only_arms_then_following_sentences_execute(gate, clock):
    assert gate.feed("露西婭").status == "armed"
    clock.now += 3
    r = gate.feed("把音量調到 30")
    assert (r.status, r.command) == ("command", "把音量調到 30")
    assert gate.feed("打開小算盤").status == "command"  # 會話內每一句都執行


def test_session_boundary(gate, clock):
    gate.feed("露醬")
    clock.now += 8.0
    assert gate.feed("打開小算盤").status == "command"  # 剛好 8 秒仍有效
    clock.now += 8.01
    assert gate.feed("打開小算盤").status == "ignored"  # 距上一句超過 8 秒，逾時


def test_expired_armed_state_stays_expired(gate, clock):
    gate.feed("露醬")
    clock.now += 10
    assert gate.feed("打開小算盤").status == "ignored"
    assert gate.feed("打開小算盤").status == "ignored"


def test_sentence_without_wake_word_is_ignored_when_not_armed(gate):
    assert gate.feed("打開小算盤").status == "ignored"
    assert gate.feed("我跟露醬說打開小算盤").status == "ignored"


def test_empty_or_punctuation_only_is_ignored_and_keeps_armed(gate, clock):
    gate.feed("露醬")
    assert gate.feed("").status == "ignored"
    assert gate.feed("。").status == "ignored"
    clock.now += 1
    assert gate.feed("打開小算盤").status == "command"


def test_repeated_wake_word_refreshes_window(gate, clock):
    gate.feed("露醬")
    clock.now += 6
    assert gate.feed("露西婭").status == "armed"
    clock.now += 6  # 距第一次 12 秒，但距第二次只有 6 秒
    assert gate.feed("打開小算盤").status == "command"


def test_wake_word_with_command_while_in_session_strips_wake_word_and_keeps_session(gate, clock):
    gate.feed("露醬")
    r = gate.feed("露西婭，打開計算機")
    assert (r.status, r.command) == ("command", "打開計算機")
    assert gate.feed("打開小算盤").status == "command"


# ---------- 停用喚醒詞（預設，常啟動） ----------
def test_wake_word_is_disabled_by_default():
    assert WakeGate(WORDS).enabled is False


@pytest.mark.parametrize("text, command", [
    ("打開小算盤", "打開小算盤"),
    ("  把音量調到 30 ", "把音量調到 30"),
    ("露醬，打開小算盤", "打開小算盤"),  # 仍帶喚醒詞也可用，會被去掉
    ("我跟露醬說打開小算盤", "我跟露醬說打開小算盤"),  # 非句首不是喚醒詞，整句照常執行
])
def test_disabled_gate_treats_every_sentence_as_command(text, command):
    r = WakeGate(WORDS, enabled=False).feed(text)
    assert (r.status, r.command) == ("command", command)


@pytest.mark.parametrize("text", ["", "   ", "。", "露醬", "露西婭，"])
def test_disabled_gate_ignores_empty_or_wake_word_only(text):
    assert WakeGate(WORDS, enabled=False).feed(text).status == "ignored"


def test_disabled_gate_never_arms(clock):
    g = WakeGate(WORDS, clock=clock, enabled=False)
    assert g.feed("露西婭").status == "ignored"
    assert g.feed("打開小算盤").command == "打開小算盤"


def test_disabled_gate_accepts_empty_wake_words():
    assert WakeGate([], enabled=False).feed("打開小算盤").command == "打開小算盤"


# ---------- 會話（預設 60 秒、每句重新計時） ----------
@pytest.fixture
def gate60(clock):
    return WakeGate(WORDS, session_s=60.0, clock=clock, enabled=True)


def test_default_session_is_60_seconds():
    assert WakeGate(WORDS, enabled=True).session_s == 60.0


def test_session_lasts_60_seconds_then_expires(gate60, clock):  # W1
    gate60.feed("露西婭")
    clock.now += 59.9
    assert gate60.feed("打開小算盤").status == "command"
    clock.now += 59.9
    assert gate60.feed("把音量調到 30").status == "command"
    clock.now += 60.01
    assert gate60.feed("打開小算盤").status == "ignored"
    assert gate60.feed("打開小算盤").status == "ignored"  # 逾時後維持失效


def test_every_sentence_restarts_the_timer(gate60, clock):  # W2
    gate60.feed("露西婭")
    for _ in range(4):
        clock.now += 50
        assert gate60.feed("打開小算盤").status == "command"  # 總共 200 秒，每句都在上一句之後 50 秒


def test_wake_word_with_command_starts_session(gate60, clock):  # W3
    r = gate60.feed("露西婭 打開小算盤")
    assert (r.status, r.command) == ("command", "打開小算盤")
    clock.now += 30
    assert gate60.feed("把音量調到 30").status == "command"


def test_blank_or_punctuation_does_not_extend_session(gate60, clock):  # W4
    gate60.feed("露西婭")
    clock.now += 50
    assert gate60.feed("。").status == "ignored"
    assert gate60.feed("  ").status == "ignored"
    clock.now += 10.01
    assert gate60.feed("打開小算盤").status == "ignored"


def test_session_api(gate60, clock):  # W6
    assert gate60.session_remaining() == 0
    gate60.start_session()
    assert gate60.session_remaining() == pytest.approx(60.0, abs=1e-6)
    clock.now += 20
    assert gate60.session_remaining() == pytest.approx(40.0, abs=1e-6)
    gate60.end_session()
    assert gate60.session_remaining() == 0
    assert gate60.feed("打開小算盤").status == "ignored"
    clock.now += 100
    gate60.start_session()
    clock.now += 61
    assert gate60.session_remaining() == 0  # 逾時


def test_feed_reports_remaining_time_after_command(gate60, clock):
    gate60.feed("露西婭")
    clock.now += 45
    gate60.feed("打開小算盤")
    assert gate60.session_remaining() == pytest.approx(60.0, abs=1e-6)


def test_start_session_lets_sentences_without_wake_word_run(gate60):
    gate60.start_session()
    assert gate60.feed("打開小算盤").status == "command"
