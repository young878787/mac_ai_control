"""麥克風收音：16 kHz mono int16，切成固定長度的 PCM 幀。"""
import queue
from typing import Optional

import sounddevice as sd

from mac_ai import config


class MicSource:
    """sounddevice 的 callback 把幀放進有上限的佇列（滿了丟掉最舊的），read() 由送出端取用"""

    def __init__(self, device: Optional[int] = None, max_blocks: int = 50):
        self._device = device
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=max_blocks)
        self._stream: Optional[sd.InputStream] = None

    def start(self) -> None:
        try:
            self._stream = sd.InputStream(
                samplerate=config.STT_SAMPLE_RATE, channels=1, dtype="int16",
                blocksize=int(config.STT_SAMPLE_RATE * config.STT_BLOCK_S), device=self._device,
                callback=self._on_audio,
            )
            self._stream.start()
        except sd.PortAudioError as e:
            raise OSError(f"無法開啟麥克風：{e}") from e

    def _on_audio(self, indata, frames, time_info, status) -> None:
        block = indata.tobytes()
        while True:
            try:
                self._q.put_nowait(block)
                return
            except queue.Full:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    pass

    def read(self, timeout: float) -> Optional[bytes]:
        """取下一幀；等不到回傳 None"""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        """丟掉佇列裡累積的舊音訊（重新連線時，不要把幾秒前的話補送過去）"""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
