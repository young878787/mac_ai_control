"""預期指令的建構（Fast Path 會送出的 subprocess 指令）與 Fast 比對輔助。多個測試檔共用，改格式只改這裡"""
import urllib.parse

import mac_ai.router as r


def APP(name):
    return ["open", "-a", name]


def QUIT(name):
    return ["osascript", "-e", f'tell application "{name}" to quit']


def VOL(n):
    return ["osascript", "-e", f"set volume output volume {n}"]


def MUTE(flag):
    return ["osascript", "-e", f"set volume output muted {'true' if flag else 'false'}"]


def BRIGHT(direction, steps):
    key = 145 if direction == "down" else 144
    return ["osascript", "-e", f'tell application "System Events" to repeat {steps} times\nkey code {key}\ndelay 0.05\nend repeat']


def DARK(value):
    return ["osascript", "-e", f'tell application "System Events" to tell appearance preferences to set dark mode to {value}']


TOGGLE_DARK = ["osascript", "-e", 'tell application "System Events" to tell appearance preferences to set dark mode to not dark mode']
LOCK = ["osascript", "-e", 'tell application "System Events" to keystroke "q" using {control down, command down}']
SLEEP = ["pmset", "sleepnow"]


def URL(q):
    return f"https://www.google.com/search?q={urllib.parse.quote(q)}"


def SEARCH(q, browser=None):
    return ["open", "-a", browser, URL(q)] if browser else ["open", URL(q)]


CHROME, SAFARI, VSCODE = "Google Chrome", "Safari", "Visual Studio Code"


def fast_matches(text):
    """正規化、切句後，每個子句的 Fast 比對結果"""
    matcher = r.FastPathMatcher()
    return [matcher.match(c) for c in matcher.split_clauses(r.normalize_utterance(text))]
