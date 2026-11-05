#!/usr/bin/env python3
"""產生端點偵測的測試語料與清單。

  python scripts/make_bench_corpus.py synth   # 合成語料（macOS say）→ bench/synth/*.wav＋manifest.csv
  python scripts/make_bench_corpus.py human   # 真人錄音清單 → bench/human/manifest.csv（錄音用 scripts/record_human.py）

類別：A 短指令／B 句中停頓／C 噪音中說指令／D 只有噪音／E 多步驟長句／F 句尾短字。
manifest 欄位：clip, category, ref_text, expected_actions(JSON), speech_end_s（說完的時間點，相對於音檔開頭；D 為空）, voice, noise, note
"""
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mac_ai.router.fast_path import FastPathMatcher  # noqa: E402
from mac_ai.router.normalize import normalize_utterance  # noqa: E402
from mac_ai.stt.vad import WINDOW, SileroVad  # noqa: E402

SR = 16000
LEAD_S, TAIL_S = 1.0, 3.0  # 每段前面留 1 s、後面留 3 s 靜音
SPEECH_RMS = 0.08  # 約 -22 dBFS，一般麥克風近距離說話
ROOM_RMS = 0.002  # 安靜房間的底噪（不是數位全零）
VOICES = [("Meijia", 150), ("Meijia", 185), ("Meijia", 220)]  # (語音, 語速 wpm)。Eddy／Sandy 等其他 zh_TW 語音 Apple STT 常聽不懂，量到的是 TTS 品質不是端點，所以只用 Meijia 變化語速

# (類別, [(片段, 與下一片段之間的停頓秒數)], 噪音) —— 片段串起來就是 ref_text
A_TEXTS = ["打開小算盤", "關掉 Safari", "音量調到三十", "靜音", "鎖定螢幕"]
B_PATTERNS = [(["打開", "小算盤"], 0.8), (["音量調到", "五十"], 0.9), (["打開 Safari 然後", "搜尋天氣"], 0.7), (["關掉", "Safari"], 0.6)]
# 噪音寫法：kind 或 kind@late。@late＝說話中途才出現（開風扇、放音樂；server 的能量 VAD 底噪會被鎖死的情境）
C_ITEMS = [("打開小算盤", "fan"), ("關掉 Safari", "hum"), ("音量調到三十", "typing"),
           ("靜音", "fan@late"), ("鎖定螢幕", "music@late"), ("打開 Line", "fan+typing@late")]
D_NOISES = ["fan", "hum", "typing", "music"]
E_TEXTS = ["打開小算盤，然後打開 Safari", "打開 Safari 然後搜尋今天天氣", "關掉 Safari，然後靜音", "打開小算盤，然後鎖定螢幕"]
F_TEXTS = ["打開 Line", "關掉音樂", "打開 Safari"]

HUMAN_NOTES = {
    "A": "正常語速說完整句，說完就停",
    "B": "照括號停頓：在『|』處停頓 0.6~1.0 秒再接著說",
    "C": "請先開風扇／冷氣／音樂／邊打字，再說指令",
    "D": "不要說話，只保持背景聲音（風扇／冷氣／音樂／打字）20 秒",
    "E": "一口氣說完整句（多步驟）",
    "F": "正常語速，句尾字不要拖或吃掉",
}


def tts(voice: str, text: str, rate: int) -> np.ndarray:
    d = Path(tempfile.mkdtemp())
    try:
        subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(d / "a.aiff"), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(d / "a.aiff"), str(d / "a.wav")], check=True)
        with wave.open(str(d / "a.wav")) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def speech_span(x: np.ndarray, thresh: float = 0.35):
    """離線 Silero：回傳語音的 (開始, 結束) sample；找不到回 None"""
    vad = SileroVad()
    probs = vad.feed((x * 32768).astype(np.int16).tobytes())
    idx = [i for i, p in enumerate(probs) if p >= thresh]
    return None if not idx else (idx[0] * WINDOW, (idx[-1] + 1) * WINDOW)


def trim(x: np.ndarray) -> np.ndarray:
    span = speech_span(x)
    if span is None:
        raise RuntimeError("TTS 輸出偵測不到語音")
    return x[max(0, span[0] - 800):span[1] + 800]


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def normalize_speech(x: np.ndarray) -> np.ndarray:
    return x * (SPEECH_RMS / max(rms(x), 1e-6))


def make_noise(kind: str, n: int, rng: np.random.Generator) -> np.ndarray:
    """回傳單位 RMS 的噪音（之後依 SNR 縮放）"""
    t = np.arange(n) / SR
    parts = []
    for k in kind.split("+"):
        if k == "fan":  # 粉紅噪音：風扇／冷氣的寬頻聲
            spec = np.fft.rfft(rng.normal(size=n))
            f = np.maximum(np.fft.rfftfreq(n, 1 / SR), 30.0)
            y = np.fft.irfft(spec / np.sqrt(f), n)
        elif k == "hum":  # 低頻嗡聲＋諧波
            y = sum(np.sin(2 * np.pi * f0 * t + rng.uniform(0, 6.28)) / h for h, f0 in enumerate([100, 200, 300, 450], 1))
            y = y + 0.1 * rng.normal(size=n)
        elif k == "typing":  # 鍵盤：不規則的短促敲擊
            y = np.zeros(n)
            pos = 0
            while pos < n - 800:
                pos += int(rng.exponential(0.22) * SR) + 400
                burst = np.sin(2 * np.pi * rng.uniform(1800, 4200) * np.arange(480) / SR) * np.exp(-np.arange(480) / 70)
                if pos + 480 < n:
                    y[pos:pos + 480] += burst * rng.uniform(0.6, 1.0)
        elif k == "music":  # 和弦 pad：每秒換一次和弦，緩慢起伏
            y = np.zeros(n)
            chords = [(261.6, 329.6, 392.0), (220.0, 261.6, 329.6), (174.6, 220.0, 261.6), (196.0, 246.9, 293.7)]
            for i in range(int(n / SR) + 1):
                seg = slice(i * SR, min(n, (i + 1) * SR))
                tt = t[seg]
                for f0 in chords[i % 4]:
                    y[seg] += sum(np.sin(2 * np.pi * f0 * h * tt) / h for h in (1, 2, 3))
            y *= 0.6 + 0.4 * np.sin(2 * np.pi * 0.3 * t)
        else:
            raise ValueError(k)
        parts.append(y / max(rms(y), 1e-9))
    mix = sum(parts)
    return mix / max(rms(mix), 1e-9)


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = np.clip(x, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((pcm * 32767).astype(np.int16).tobytes())


def compose(segments, pause_s, noise, snr_db, rng, noise_start_s=0.0):
    """lead 靜音＋片段（片段之間停頓）＋tail 靜音；回傳 (波形, 說完的時間點秒)"""
    speech = []
    for i, seg in enumerate(segments):
        speech.append(seg)
        if i < len(segments) - 1:
            speech.append(np.zeros(int(pause_s * SR), dtype=np.float32))
    body = np.concatenate(speech) if speech else np.zeros(0, dtype=np.float32)
    total = np.concatenate([np.zeros(int(LEAD_S * SR)), body, np.zeros(int(TAIL_S * SR))]).astype(np.float32)
    end_s = (int(LEAD_S * SR) + len(body)) / SR if len(body) else None
    total = total + ROOM_RMS * rng.normal(size=len(total)).astype(np.float32)
    if noise:
        level = make_noise(noise, len(total), rng) * SPEECH_RMS / 10 ** (snr_db / 20)
        if noise_start_s > 0:  # 噪音從 noise_start_s 才出現（0.2 s 淡入）
            ramp = np.clip((np.arange(len(total)) / SR - noise_start_s) / 0.2, 0.0, 1.0)
            level = level * ramp
        total = total + level.astype(np.float32)
    return total, end_s


def expected_actions(ref_text: str):
    if not ref_text:
        return []
    matcher = FastPathMatcher()
    actions = []
    for clause in matcher.split_clauses(normalize_utterance(ref_text)):
        m = matcher.match(clause)
        if m is None:
            raise RuntimeError(f"Fast Path 無法比對：{clause!r}（換一句語料）")
        actions += [[name, args] for name, args in m.actions]
    return actions


FIELDS = ["clip", "category", "ref_text", "expected_actions", "speech_end_s", "voice", "noise", "note"]


def synth(out: Path) -> None:
    if shutil.which("say") is None:
        sys.exit("需要 macOS 的 say")
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20261104)
    rows = []

    def add(clip, cat, segments_text, voice, pause_s=0.0, noise="", snr=12.0, speech_noise_only=False, dur=20.0, note=""):
        if speech_noise_only:
            x, end_s = compose([], 0, noise, snr, rng)
            n = int(dur * SR)
            x = (ROOM_RMS * rng.normal(size=n) + make_noise(noise.partition("@")[0], n, rng) * 0.03).astype(np.float32)
            ref = ""
        else:
            name, rate = voice
            segs = [normalize_speech(trim(tts(name, t, rate))) for t in segments_text]
            kind, _, when = noise.partition("@")
            x, end_s = compose(segs, pause_s, kind, snr, rng, noise_start_s=(LEAD_S + 0.6) if when == "late" else 0.0)
            ref = "".join(segments_text)
        write_wav(out / f"{clip}.wav", x)
        rows.append({"clip": clip, "category": cat, "ref_text": ref, "expected_actions": json.dumps(expected_actions(ref), ensure_ascii=False),
                     "speech_end_s": "" if end_s is None else f"{end_s:.3f}",
                     "voice": f"{voice[0]}@{voice[1]}" if voice else "", "noise": noise, "note": note})

    n = 0
    for text in A_TEXTS:
        for v in VOICES:
            n += 1
            add(f"A{n:02d}", "A", [text], v)
    for i, (segs, pause) in enumerate(B_PATTERNS):
        for j in range(2):
            add(f"B{2 * i + j + 1:02d}", "B", segs, VOICES[j], pause_s=pause + 0.1 * j, note=f"pause={pause + 0.1 * j:.1f}")
    for i, (text, noise) in enumerate(C_ITEMS, 1):
        add(f"C{i:02d}", "C", [text], VOICES[i % 3], noise=noise, snr=12.0)
    for i, noise in enumerate(D_NOISES, 1):
        add(f"D{i:02d}", "D", [], None, noise=noise, speech_noise_only=True)
    for i, text in enumerate(E_TEXTS, 1):
        add(f"E{i:02d}", "E", [text], VOICES[i % 3])
    for i, text in enumerate(F_TEXTS, 1):
        add(f"F{i:02d}", "F", [text], VOICES[i % 3])
    with (out / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"已產生 {len(rows)} 段 → {out}")


def human(out: Path) -> None:
    """真人錄音清單：和合成語料同樣的類別與數量（A15／B8／C6／D4／E4／F3），錄音由 record_human.py 完成"""
    out.mkdir(parents=True, exist_ok=True)
    rows = []

    def add(clip, cat, ref_text, noise="", seconds=7.0, note=""):
        shown = ref_text if not note.startswith("pause") else note.split("|", 1)[1]
        rows.append({"clip": clip, "category": cat, "ref_text": ref_text, "expected_actions": json.dumps(expected_actions(ref_text), ensure_ascii=False),
                     "speech_end_s": "", "voice": "human", "noise": noise,
                     "note": f"{HUMAN_NOTES[cat]}｜顯示：{shown}｜錄 {seconds:g} 秒"})

    n = 0
    for text in A_TEXTS:
        for _ in range(3):
            n += 1
            add(f"A{n:02d}", "A", text, seconds=7)
    for i, (segs, pause) in enumerate(B_PATTERNS):
        for j in range(2):
            add(f"B{2 * i + j + 1:02d}", "B", "".join(segs), seconds=9, note=f"pause|{' | '.join(segs)}")
    for i, (text, noise) in enumerate(C_ITEMS, 1):
        add(f"C{i:02d}", "C", text, noise=noise, seconds=8)
    for i, noise in enumerate(D_NOISES, 1):
        rows.append({"clip": f"D{i:02d}", "category": "D", "ref_text": "", "expected_actions": "[]", "speech_end_s": "", "voice": "human",
                     "noise": noise, "note": f"{HUMAN_NOTES['D']}｜錄 20 秒"})
    for i, text in enumerate(E_TEXTS, 1):
        add(f"E{i:02d}", "E", text, seconds=11)
    for i, text in enumerate(F_TEXTS, 1):
        add(f"F{i:02d}", "F", text, seconds=7)
    with (out / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"已產生真人錄音清單 {len(rows)} 段 → {out / 'manifest.csv'}")


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    if kind == "synth":
        synth(ROOT / "bench" / "synth")
    elif kind == "human":
        human(ROOT / "bench" / "human")
    else:
        sys.exit(__doc__)
