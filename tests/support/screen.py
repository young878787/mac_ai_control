"""假的螢幕環境（讀畫面／看畫面相關測試共用）：攔下 subprocess.run、time.sleep，並把 llm_client._chat 換成視覺模型的假回覆"""
import subprocess
import tempfile
import time
from pathlib import Path

from mac_ai.router import llm_client


class Screen:
    """假的螢幕環境：截圖與縮圖會真的寫出檔案；Chrome 的 loading 依序回傳 loading_seq"""

    def __init__(self, monkeypatch, tmp_path, loading_seq=("false",), vl_reply="天氣：晴，氣溫 25 度", window_out=""):
        self.calls, self.sleeps, self.vl_messages = [], [], []
        self.loading_seq = list(loading_seq)
        self.vl_reply = vl_reply
        self.window_out = window_out  # 查前景瀏覽器視窗（JXA）的輸出；空字串＝沒有瀏覽器視窗
        monkeypatch.setattr(subprocess, "run", self.run)
        monkeypatch.setattr(time, "sleep", self.sleeps.append)
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        monkeypatch.setattr(llm_client, "_chat", self.chat)

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if cmd[0] == "screencapture":
            Path(cmd[-1]).write_bytes(b"BIG")
        elif cmd[0] == "sips":
            Path(cmd[cmd.index("--out") + 1]).write_bytes(b"SMALL")
        elif cmd[:3] == ["osascript", "-l", "JavaScript"]:
            out = self.window_out
        elif cmd[0] == "osascript" and "loading of active tab" in cmd[2]:
            out = self.loading_seq.pop(0) if len(self.loading_seq) > 1 else self.loading_seq[0]
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def chat(self, messages, **kw):
        self.vl_messages.append((messages, kw))
        if isinstance(self.vl_reply, Exception):
            raise self.vl_reply
        return {"content": self.vl_reply}

    @property
    def names(self):
        return [c[0] for c in self.calls]
