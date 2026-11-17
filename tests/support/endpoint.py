"""端點偵測的模擬器：依序餵機率與 partial，時間由測試自己推進（不 sleep）"""
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
