"""SileroVad：用真的模型檔。語音用 macOS 的 say 合成（沒有 say 或模型就略過）。"""
import shutil
import subprocess
import wave

import numpy as np
import pytest

from mac_ai import config
from mac_ai.stt.vad import WINDOW, SileroVad

pytestmark = pytest.mark.skipif(not config.VAD_MODEL_PATH.exists(), reason="沒有 AI_models/silero_vad.onnx")


@pytest.fixture(scope="module")
def speech_pcm(tmp_path_factory):
    if shutil.which("say") is None or shutil.which("afconvert") is None:
        pytest.skip("需要 macOS 的 say／afconvert")
    d = tmp_path_factory.mktemp("vad")
    subprocess.run(["say", "-v", "Meijia", "-o", str(d / "a.aiff"), "打開小算盤，然後把音量調到五十"], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(d / "a.aiff"), str(d / "a.wav")], check=True)
    with wave.open(str(d / "a.wav")) as w:
        return w.readframes(w.getnframes())


def test_silence_has_low_probability():
    vad = SileroVad()
    probs = vad.feed(b"\x00\x00" * 16000)
    assert len(probs) == 16000 // WINDOW and max(probs) < 0.1


def test_synthetic_speech_is_detected_and_silence_around_it_is_not(speech_pcm):
    vad = SileroVad()
    gap = b"\x00\x00" * 16000
    probs = np.array(vad.feed(gap + speech_pcm + gap))
    lead, tail = 16000 // WINDOW, 16000 // WINDOW
    body = probs[lead + 2:-tail - 2]
    assert (body >= 0.5).mean() > 0.6
    assert probs[:lead].max() < 0.1 and probs[-tail + 2:].max() < 0.1


def test_noise_is_not_speech():
    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 800, 16000 * 3)).astype(np.int16).tobytes()
    assert max(SileroVad().feed(noise)) < 0.5


def test_chunking_does_not_change_results_and_does_not_drop_samples(speech_pcm):
    whole = SileroVad().feed(speech_pcm)
    vad, chunked = SileroVad(), []
    step = 1000 * 2  # 不是 512 的倍數
    for i in range(0, len(speech_pcm), step):
        chunked += vad.feed(speech_pcm[i:i + step])
    assert len(chunked) == len(whole) == len(speech_pcm) // 2 // WINDOW
    assert np.allclose(chunked, whole, atol=1e-5)


def test_reset_makes_output_reproducible(speech_pcm):
    vad = SileroVad()
    first = vad.feed(speech_pcm)
    vad.reset()
    assert np.allclose(vad.feed(speech_pcm), first, atol=1e-6)


def test_inference_is_fast_enough_for_realtime(speech_pcm):
    vad = SileroVad()
    vad.feed(speech_pcm * 3)
    t = np.array(vad.infer_ms)
    assert t.mean() < 1.0 and np.percentile(t, 99) < 3.0
