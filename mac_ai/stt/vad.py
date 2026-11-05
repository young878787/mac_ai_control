"""Silero VAD v5（ONNX）：以 512 個 sample（16 kHz 下 32 ms）為一窗，輸出每窗的語音機率。"""
import time
from collections import deque
from typing import List

import numpy as np
import onnxruntime as ort

from mac_ai import config

WINDOW = 512  # Silero v5 在 16 kHz 只接受 512 sample 的窗
CONTEXT = 64  # 每窗前要接上一窗的最後 64 sample


class SileroVad:
    """feed() 可以收任意長度的 int16 PCM，不足一窗的尾巴留到下次；有狀態，一個麥克風串流用一個實例"""

    def __init__(self, model_path=config.VAD_MODEL_PATH):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(config.STT_SAMPLE_RATE, dtype=np.int64)
        self.infer_ms: "deque[float]" = deque(maxlen=4096)  # 最近每窗的推論耗時，供效能驗收
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)

    def feed(self, pcm: bytes) -> List[float]:
        """回傳這段 PCM 湊得出的每個完整窗的語音機率（0~1）"""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        buf = np.concatenate([self._tail, samples])
        probs: List[float] = []
        n = len(buf) // WINDOW
        for i in range(n):
            probs.append(self._infer(buf[i * WINDOW:(i + 1) * WINDOW]))
        self._tail = buf[n * WINDOW:]
        return probs

    def _infer(self, window: np.ndarray) -> float:
        t = time.perf_counter()
        x = np.concatenate([self._context, window[None, :]], axis=1)
        out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -CONTEXT:]
        self.infer_ms.append((time.perf_counter() - t) * 1000)
        return float(out[0][0])
