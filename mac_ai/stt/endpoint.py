"""端點偵測：用 VAD 語音機率（onset/offset 遲滯）、partial 文字的完整度與穩定度，決定這句話什麼時候算說完。

純邏輯，不碰 IO；時間由呼叫端傳入（單調時鐘秒數），所以可以完全離線測試。
所有公開方法都有鎖：機率從送音訊的執行緒進來，partial／final 從收事件的執行緒進來。"""
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from mac_ai import config
from mac_ai.stt.completeness import COMPLETE, INCOMPLETE, classify
from mac_ai.stt.vad import WINDOW


@dataclass
class Decision:
    """要求 flush 的決定。reason：complete／default／incomplete（依句子完整度）、asr_stall、max_len"""
    reason: str
    cls: str
    partial: str
    at: float
    speech_start: float
    speech_end: float


@dataclass
class Utterance:
    """一句話結束（收到 final）時的紀錄。decision 是 None＝不是我們要求的 flush（server 自己斷句，或 server 模式）"""
    speech_start: Optional[float]
    speech_end: Optional[float]
    decision: Optional[Decision]


class EndpointDetector:
    def __init__(self, decide: bool = True, classifier: Callable[[str], str] = classify,
                 sample_rate: int = config.STT_SAMPLE_RATE,
                 onset_prob: float = config.VAD_ONSET_PROB, offset_prob: float = config.VAD_OFFSET_PROB,
                 onset_frames: int = config.VAD_ONSET_FRAMES,
                 silence_complete_s: float = config.EP_SILENCE_COMPLETE_S,
                 silence_unconfirmed_s: float = config.EP_SILENCE_COMPLETE_UNCONFIRMED_S,
                 silence_default_s: float = config.EP_SILENCE_DEFAULT_S,
                 silence_incomplete_s: float = config.EP_SILENCE_INCOMPLETE_S,
                 partial_stable_s: float = config.EP_PARTIAL_STABLE_S, asr_stall_s: float = config.EP_ASR_STALL_S,
                 no_text_reset_s: float = config.EP_NO_TEXT_RESET_S, max_utterance_s: float = config.EP_MAX_UTTERANCE_S,
                 final_wait_s: float = config.EP_FINAL_WAIT_S):
        """decide=False：只觀察（量 speech_start／speech_end），不做 flush 決定（server 模式的 baseline 用）"""
        self.decide = decide
        self._classify = classifier
        self._window_s = WINDOW / sample_rate
        self._onset_prob, self._offset_prob, self._onset_frames = onset_prob, offset_prob, onset_frames
        self._thr = {COMPLETE: silence_complete_s, "unconfirmed": silence_unconfirmed_s,
                     INCOMPLETE: silence_incomplete_s, "default": silence_default_s}
        self._stable_s, self._stall_s = partial_stable_s, asr_stall_s
        self._no_text_s, self._max_s, self._final_wait_s = no_text_reset_s, max_utterance_s, final_wait_s
        self._lock = threading.Lock()
        self._cls_cache: Dict[str, str] = {}
        self._pending: Optional[Utterance] = None  # 已送出 flush、等 final 的那一句
        self._awaiting_since = 0.0
        self._text_clock_start = 0.0
        self.abandoned = 0  # 被當成噪音放棄的次數（供驗收）
        self._reset_utterance()

    # ---- 狀態 ----

    def _reset_utterance(self) -> None:
        self._active = False
        self._speaking = False
        self._onset_n = 0
        self._start = 0.0
        self._speech_since = 0.0  # 目前這一段連續語音的起點（句中停頓後重新開口會更新）
        self._last_speech = 0.0
        self._quiet_since = 0.0
        self._text = ""
        self._text_changed_at = 0.0

    def reset(self) -> None:
        """重新連線時呼叫：丟掉進行中的一句與等 final 的狀態"""
        with self._lock:
            self._pending = None
            self._text_clock_start = 0.0
            self._reset_utterance()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def awaiting_final(self) -> bool:
        with self._lock:
            return self._pending is not None

    # ---- 輸入 ----

    def on_prob(self, p: float, now: float) -> Optional[Decision]:
        """now＝這一窗結束的時間。回傳 Decision 時，呼叫端要送 flush"""
        with self._lock:
            if self._pending is not None and now - self._awaiting_since >= self._final_wait_s:
                self._pending = None  # final 一直沒來，不要卡住下一句
            if not self._active:
                self._onset_n = self._onset_n + 1 if p >= self._onset_prob else 0
                if self._onset_n >= self._onset_frames:
                    self._active, self._speaking = True, True
                    self._start = self._speech_since = now - self._onset_n * self._window_s
                    self._last_speech = now
                    self._onset_n = 0
                return None
            if self._speaking:
                if p >= self._offset_prob:
                    self._last_speech = now
                else:
                    self._speaking = False
                    self._quiet_since = now - self._window_s
            else:
                self._onset_n = self._onset_n + 1 if p >= self._onset_prob else 0
                if self._onset_n >= self._onset_frames:
                    self._speaking, self._last_speech = True, now
                    self._speech_since = now - self._onset_n * self._window_s
                    self._onset_n = 0
            return self._evaluate(now) if self.decide else None

    def on_partial(self, text: str, now: float) -> None:
        with self._lock:
            if self._pending is not None or not self._active or text == self._text:
                return  # 等 final 期間的 partial 屬於上一句；還沒確認開始說話的聲音不採用
            self._text, self._text_changed_at = text, now

    def on_final(self, now: float) -> Utterance:
        """收到 final：結束一句。若這句是我們 flush 的，回傳當時的快照；否則（server 自己斷句）把目前這一句收掉"""
        with self._lock:
            self._text_clock_start = now  # server 剛收尾重開，新一句的 partial 要從現在才開始計
            if self._pending is not None:
                record, self._pending = self._pending, None
                return record
            record = Utterance(self._start if self._active else None, self._last_speech if self._active else None, None)
            self._reset_utterance()
            return record

    # ---- 決策 ----

    def _evaluate(self, now: float) -> Optional[Decision]:
        if now - self._start >= self._max_s:
            return self._flush("max_len", now)
        has_text = bool(self._text.strip())
        if not has_text:
            if self._pending is None and now - max(self._start, self._text_clock_start) >= self._no_text_s:
                self.abandoned += 1  # 一直沒有任何文字：是噪音，放棄這一段，不送 flush
                self._reset_utterance()
            return None
        stalled = False
        if not self._speaking:
            quiet = now - self._quiet_since
        elif now - max(self._text_changed_at, self._speech_since) >= self._stall_s:
            # 說在說話但文字不動：靜音從文字最後一次變動算。句中停頓後才重新開口的，要從重新開口起算（引擎的 partial 會晚一點才來）
            quiet, stalled = now - max(self._text_changed_at, self._speech_since), True
        else:
            return None
        cls = self._cls_cache.get(self._text)
        if cls is None:
            cls = self._cls_cache[self._text] = self._classify(self._text)
        if cls == COMPLETE and now - self._text_changed_at >= self._stable_s:
            # 說完之後 partial 有更新過＝引擎已經對這段語音有反應，文字才可信；否則舊文字可能缺了還沒吐出來的字
            key = COMPLETE if self._text_changed_at > self._last_speech else "unconfirmed"
        else:
            key = INCOMPLETE if cls == INCOMPLETE else "default"
        if quiet < self._thr[key]:
            return None
        return self._flush("asr_stall" if stalled else {"unconfirmed": COMPLETE}.get(key, key), now, cls)

    def _flush(self, reason: str, now: float, cls: Optional[str] = None) -> Decision:
        text = self._text
        decision = Decision(reason, cls if cls is not None else self._classify(text), text, now,
                            self._start, self._last_speech)
        self._pending = Utterance(self._start, self._last_speech, decision)
        self._cls_cache.clear()
        self._awaiting_since = now
        self._reset_utterance()
        return decision
