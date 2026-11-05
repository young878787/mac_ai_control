"""EndpointDetector：onset/offset 遲滯、依句子完整度的動態靜音門檻、ASR 輔助（停滯／無文字）、最長一句、等 final。"""
import pytest

from mac_ai.stt.endpoint import EndpointDetector

W = 512 / 16000  # 一窗 32 ms


class Sim:
    """依序餵機率與 partial；時間由我們自己推進（每窗 32 ms）"""

    def __init__(self, cls="complete", **kw):
        self.d = EndpointDetector(classifier=lambda text: cls if isinstance(cls, str) else cls(text), **kw)
        self.now = 0.0
        self.decisions = []

    def win(self, n, p):
        for _ in range(n):
            self.now += W
            dec = self.d.on_prob(p, self.now)
            if dec:
                self.decisions.append(dec)

    def speech(self, seconds, p=0.9):
        self.win(round(seconds / W), p)

    def silence(self, seconds, p=0.02):
        self.win(round(seconds / W), p)

    def partial(self, text):
        self.d.on_partial(text, self.now)

    def silence_until_decision(self, limit=5.0, p=0.02):
        n0 = len(self.decisions)
        t0 = self.now
        while len(self.decisions) == n0 and self.now - t0 < limit:
            self.win(1, p)
        return (self.now - t0) if len(self.decisions) > n0 else None


def spoke(sim, text="打開小算盤", seconds=1.0, lag=0.0):
    """說一句話：說話中 partial 每 0.3 s 多長一些；說完後過 lag 秒（引擎的延遲）定稿成 text"""
    steps = max(1, round(seconds / 0.3))
    for i in range(steps):
        sim.speech(seconds / steps)
        sim.partial(text[:max(1, len(text) * (i + 1) // (steps + 1))])  # 說話中永遠比全文短，全文要等說完（引擎延遲後）才定稿
    sim.silence(lag)
    sim.partial(text)


def test_single_spike_does_not_start_speech():
    s = Sim()
    s.win(1, 0.95)
    s.silence(0.5)
    s.win(2, 0.95)
    s.silence(0.5)
    assert not s.d.active


def test_three_consecutive_windows_start_speech_and_backdate_start():
    s = Sim()
    s.silence(0.5)
    s.win(3, 0.9)
    assert s.d.active
    s.partial("打開")
    s.silence(2.5)
    assert s.decisions and s.decisions[0].speech_start == pytest.approx(0.5 + W, abs=W)  # 起點是第一個語音窗，不是第三個


def test_hysteresis_probability_between_thresholds_keeps_speaking():
    s = Sim(cls="unknown")
    spoke(s)
    s.silence(0.4, p=0.4)  # 介於 offset(0.35) 與 onset(0.5)：還在說話，不算靜音
    assert s.decisions == []
    t = s.silence_until_decision(p=0.2)  # 低於 offset 才開始計靜音
    assert t == pytest.approx(0.8, abs=2 * W)


@pytest.mark.parametrize("cls,expected", [("complete", 0.35), ("unknown", 0.8), ("incomplete", 1.6)])
def test_silence_threshold_depends_on_sentence_completeness(cls, expected):
    s = Sim(cls=cls)
    spoke(s, seconds=1.5, lag=0.1)  # 引擎在說完後 0.1 s 定稿
    t = 0.1 + s.silence_until_decision()  # 從說完算起
    assert t == pytest.approx(expected, abs=2 * W)
    d = s.decisions[0]
    assert d.cls == cls and d.partial == "打開小算盤"
    assert d.reason == {"complete": "complete", "unknown": "default", "incomplete": "incomplete"}[cls]


def test_complete_text_that_engine_has_not_updated_since_speech_ended_needs_longer_silence():
    # partial 在說完之前就定稿＝引擎還沒對「最後那段語音」有反應，可能還有字沒吐出來
    s = Sim(cls="complete")
    spoke(s, seconds=1.5)  # 定稿在說完的當下，不晚於 last_speech
    t = s.silence_until_decision()
    assert t == pytest.approx(0.6, abs=2 * W)
    assert s.decisions[0].reason == "complete"


def test_pause_mid_sentence_waits_for_engine_to_report_the_last_word_instead_of_cutting():
    # 「打開Safari然後｜搜尋天氣」：停頓時 partial 還停在「打開Safari」（完整），「然後」要晚 0.5 s 才出現
    texts = {"打開Safari": "complete", "打開Safari然後": "incomplete"}
    s = Sim(cls=lambda t: texts.get(t, "unknown"))
    spoke(s, "打開Safari", seconds=1.0)
    s.silence(0.5)  # 還沒到 0.6 s，舊的「打開Safari」不能被收走
    assert s.decisions == []
    s.partial("打開Safari然後")  # 引擎補上「然後」→ 不完整，改等 1.6 s
    s.silence(0.8)
    assert s.decisions == []
    s.speech(1.0)  # 使用者接著說完
    s.partial("打開Safari然後搜尋天氣")
    assert s.decisions == []


def test_complete_waits_until_partial_is_stable():
    s = Sim(cls="complete", partial_stable_s=0.25)
    s.speech(0.5)
    s.partial("打開小")
    s.silence(0.30)
    s.partial("打開小算盤")  # 引擎還在更新：靜音已 0.30 s，但 partial 剛變
    t = s.silence_until_decision()
    assert t == pytest.approx(0.25, abs=2 * W)  # 再等 partial 穩定 0.25 s 才收（不是立刻）
    assert s.decisions[0].reason == "complete"


def test_complete_but_partial_keeps_changing_falls_back_to_default():
    s = Sim(cls="complete")
    s.speech(0.5)
    end = s.now
    for i in range(30):  # 靜音期間 partial 每 0.1 s 變一次：一直不穩定，不能用 0.35 s 的快門檻
        s.partial(f"打開小算盤{i}")
        s.silence(0.1)
        if s.decisions:
            break
    assert s.decisions[0].reason == "default"
    assert s.decisions[0].at - end == pytest.approx(0.8, abs=0.2)


def test_noise_that_vad_calls_speech_but_text_is_stalled_is_ended_by_asr_stall():
    s = Sim(cls="complete")
    s.speech(0.5)
    s.partial("打開小算盤")
    t0 = s.now
    s.speech(5.0, p=0.9)  # VAD 一直說「在說話」（風扇、音樂），文字卻不動
    assert len(s.decisions) == 1 and s.decisions[0].reason == "asr_stall"
    assert s.decisions[0].at - t0 == pytest.approx(1.2, abs=2 * W)


def test_no_text_for_too_long_is_noise_and_abandoned_without_flush():
    s = Sim()
    s.speech(0.4)
    s.silence(3.0)
    assert s.decisions == [] and s.d.abandoned == 1 and not s.d.active
    spoke(s)  # 之後正常的一句不受影響
    assert s.silence_until_decision() is not None


def test_never_flushes_without_any_text_even_with_long_silence():
    s = Sim()
    s.speech(0.6)
    s.silence(1.9)
    assert s.decisions == []


def test_max_utterance_forces_flush():
    s = Sim(cls="unknown")
    s.speech(0.5)
    for i in range(30):  # 文字持續在變，VAD 持續說話：只剩 max_len 能收
        s.partial(f"字{i}")
        s.speech(0.5)
        if s.decisions:
            break
    assert s.decisions[0].reason == "max_len"
    assert s.decisions[0].at - s.decisions[0].speech_start == pytest.approx(10.0, abs=0.6)


def test_spike_during_trailing_does_not_restart_silence_timer():
    s = Sim(cls="unknown")
    spoke(s)
    s.silence(0.5)
    s.win(2, 0.95)  # 兩窗不到 onset_frames
    t = s.silence_until_decision()
    assert t == pytest.approx(0.8 - 0.5 - 2 * W, abs=3 * W)  # 靜音計時沒被重置


def test_resuming_speech_after_a_pause_restarts_silence_timer():
    s = Sim(cls="unknown")
    spoke(s)
    s.silence(0.5)
    s.win(3, 0.95)  # 真的又開口：重新計
    s.silence(0.1)
    assert s.decisions == []
    t = s.silence_until_decision()
    assert t == pytest.approx(0.8 - 0.1, abs=2 * W)


def test_resuming_after_long_pause_is_not_mistaken_for_stalled_noise():
    # 句中停頓 1.4 s（比停滯門檻 1.2 s 長）後重新開口：引擎的 partial 還沒更新，不能立刻被當噪音收掉
    s = Sim(cls="incomplete")
    spoke(s)
    s.silence(1.4)
    assert s.decisions == []
    s.speech(0.5)
    assert s.decisions == []


def test_after_flush_partials_belong_to_previous_sentence_until_final():
    s = Sim(cls="unknown")
    spoke(s)
    s.silence_until_decision()
    assert s.d.awaiting_final
    s.speech(0.4)  # 使用者馬上講下一句
    s.partial("上一句的尾巴")  # 還沒收到 final：忽略
    utt = s.d.on_final(s.now)
    assert utt.decision is s.decisions[0] and utt.decision.partial == "打開小算盤"
    assert not s.d.awaiting_final
    assert s.d._text == ""  # 新一句沒有沾到舊文字


def test_final_that_never_arrives_does_not_block_next_sentence():
    s = Sim(cls="unknown", final_wait_s=1.0)
    spoke(s)
    s.silence_until_decision()
    s.silence(1.2)
    assert not s.d.awaiting_final


def test_final_without_our_flush_resets_current_utterance_and_reports_no_decision():
    s = Sim(cls="unknown")
    spoke(s)
    utt = s.d.on_final(s.now)  # server 自己用靜音斷句
    assert utt.decision is None and utt.speech_start is not None and utt.speech_end is not None
    assert not s.d.active


def test_observe_only_mode_never_decides_but_measures_speech_end():
    s = Sim(cls="complete", decide=False)
    spoke(s, seconds=1.0)
    end = s.now
    s.silence(3.0)
    assert s.decisions == []
    utt = s.d.on_final(s.now)
    assert utt.speech_end == pytest.approx(end, abs=2 * W)


def test_new_utterance_text_clock_starts_after_final():
    s = Sim(cls="unknown")
    spoke(s)
    s.silence_until_decision()
    s.speech(1.5)  # 下一句已開始 1.5 s，但 server 還在收尾，partial 被忽略
    s.d.on_final(s.now)
    s.speech(1.0)  # final 之後 1.0 s 內沒有文字不應被當噪音（門檻 2.0 s 從 final 起算）
    assert s.d.abandoned == 0 and s.d.active
