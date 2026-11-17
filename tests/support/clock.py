"""可注入的假時鐘：Clock() 當 callable 回傳 now，測試用 clock.now += 秒數 推進。時間相關邏輯一律用它，不要 sleep"""


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now
