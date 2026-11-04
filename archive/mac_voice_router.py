import base64
import difflib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ==========================================
# 0. 預先詞庫（單一來源：vocab/lexicon.json）
# ==========================================
LEXICON_PATH = Path(__file__).parent / "vocab" / "lexicon.json"
NORMALIZE_LOG = Path(__file__).parent / "logs" / "normalize.jsonl"


def load_lexicon(path: Path = LEXICON_PATH) -> dict:
    """讀詞庫。misheard 可為字串（舊格式）或 {"text", "source"}；載入後一律攤平成字串，
    來源另存於 lex["misheard_sources"]（小寫詞 → observed／seed）"""
    lex = json.loads(path.read_text(encoding="utf-8"))
    sources: Dict[str, str] = {}
    for app in lex["apps"]:
        texts = []
        for entry in app.get("misheard", []):
            text = entry["text"] if isinstance(entry, dict) else entry
            source = entry.get("source", "observed") if isinstance(entry, dict) else "observed"
            sources[text.lower()] = source
            texts.append(text)
        app["misheard"] = texts
    for entry in lex.get("term_misheard", []):
        sources[entry["text"].lower()] = entry.get("source", "observed")
    lex["misheard_sources"] = sources
    return lex


LEXICON = load_lexicon()

# 別名 → 標準名稱（Fast Path 與 LLM 提示共用）；key 一律小寫
MAC_APP_ALIASES: Dict[str, str] = {}
# STT 常見誤聽 → 標準名稱（路由前先替換）；key 一律小寫
APP_MISHEARD: Dict[str, str] = {}
# App 標準名稱 → 類型（browser／editor／other）
APP_KIND: Dict[str, str] = {}
for _app in LEXICON["apps"]:
    MAC_APP_ALIASES[_app["name"].lower()] = _app["name"]
    APP_KIND[_app["name"]] = _app.get("kind", "other")
    for _a in _app.get("aliases", []):
        MAC_APP_ALIASES[_a.lower()] = _app["name"]
    for _m in _app.get("misheard", []):
        APP_MISHEARD[_m.lower()] = _app["name"]

# 指令詞誤聽（如「因量」→「音量」）；key 一律小寫
TERM_MISHEARD: Dict[str, str] = {e["text"].lower(): e["to"] for e in LEXICON.get("term_misheard", [])}
# 簡體 → 繁體（只收指令相關詞）
S2T: Dict[str, str] = LEXICON.get("s2t", {})
# 專案根目錄：第一層資料夾自動視為專案
PROJECT_ROOTS: List[str] = list(LEXICON.get("project_roots", []))

_VERBS = LEXICON.get("verbs", {})
OPEN_VERBS: List[str] = _VERBS.get("open", ["打開", "開啟", "啟動", "叫出", "開"])
QUIT_VERBS: List[str] = _VERBS.get("quit", ["關掉", "關閉", "退出", "結束", "關"])
_FILLERS = LEXICON.get("fillers", {})


def _alt(words: List[str]) -> str:
    """把詞串成 regex 交替式；長詞在前，避免短詞先吃掉（如『開』吃掉『開啟』）"""
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_OPEN = _alt(OPEN_VERBS)
_QUIT = _alt(QUIT_VERBS)
_VERB = _alt(OPEN_VERBS + QUIT_VERBS)
_LONG_VERB = _alt([v for v in OPEN_VERBS + QUIT_VERBS if len(v) >= 2])

_MISHEARD_RE = (
    re.compile(_alt(list({**APP_MISHEARD, **TERM_MISHEARD})), re.IGNORECASE)
    if (APP_MISHEARD or TERM_MISHEARD)
    else None
)
_S2T_RE = re.compile(_alt(list(S2T))) if S2T else None
_DUP_VERB_RE = re.compile(rf"({_LONG_VERB})\1+")

# ---------- 中文數字（只在數值語境轉換）----------
_ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ZH_NUM = r"[零〇一二兩三四五六七八九十百]+"
_NOT_COUNTER = r"(?![點下個些半])"  # 『一點』『一下』『一個』『一半』不是數字
_SET_VERBS = "調到|調成|調整到|調整為|設為|設定為|設成|設定成|開到|改成|改為"


def zh_to_int(s: str) -> Optional[int]:
    """『五十』→50、『一百零五』→105、『一百二』→120、『十五』→15；無法解析回傳 None"""
    total, num = 0, None
    for ch in s:
        if ch in _ZH_DIGITS:
            num = _ZH_DIGITS[ch]
        elif ch in ("十", "百"):
            total += (1 if num is None else num) * (10 if ch == "十" else 100)
            num = None
        else:
            return None
    if num is not None:
        if total >= 100 and total % 100 == 0 and "零" not in s and "〇" not in s:
            num *= 10  # 一百二 = 120
        total += num
    return total


def _num_text(m_text: str) -> str:
    n = zh_to_int(m_text)
    return str(n) if n is not None else m_text


def _convert_numerals(text: str) -> str:
    text = re.sub(rf"百分之({_ZH_NUM})", lambda m: f"{_num_text(m[1])}%", text)
    text = re.sub(rf"({_ZH_NUM})\s*(?:趴|%)", lambda m: f"{_num_text(m[1])}%", text)
    text = re.sub(rf"({_ZH_NUM})(?=\s*格)", lambda m: _num_text(m[1]), text)
    text = re.sub(rf"({_SET_VERBS})\s*({_ZH_NUM}){_NOT_COUNTER}", lambda m: m[1] + _num_text(m[2]), text)
    text = re.sub(rf"(音量|聲音|亮度)\s*({_ZH_NUM}){_NOT_COUNTER}", lambda m: m[1] + _num_text(m[2]), text)
    return text


def to_traditional(text: str) -> str:
    """簡轉繁（只涵蓋詞庫 s2t 表內的詞，無法保證全部轉換）"""
    return _S2T_RE.sub(lambda m: S2T[m.group(0)], text) if _S2T_RE else text


def _basic_normalize(text: str) -> str:
    """NFKC（全形→半形）、空白收斂、移除中文與中文之間的空白；中英之間的空白保留（VS Code）"""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"(?<=[一-鿿]) (?=[一-鿿])", "", t)


def normalize_utterance(text: str) -> str:
    """正規化 STT 文字：NFKC → 空白 → 簡轉繁 → 誤聽詞（App／指令詞）→ 中文數字 → 疊詞。
    『一點』『一下』不會被當成數字；中英之間的空白保留（VS Code）"""
    t = to_traditional(_basic_normalize(text))
    if _MISHEARD_RE is not None:
        lookup = {**APP_MISHEARD, **TERM_MISHEARD}
        t = _MISHEARD_RE.sub(lambda m: lookup[m.group(0).lower()], t)
    t = _convert_numerals(t)
    return _DUP_VERB_RE.sub(r"\1", t)


def log_normalization(original: str, normalized: str) -> None:
    """把『STT 原文 → 正規化結果』附加寫入 NORMALIZE_LOG，用來回收真實誤聽；寫入失敗不影響流程。
    只記錄詞庫替換造成的改動（簡轉繁、誤聽詞、中文數字、疊詞）；全形／空白這類機械性轉換沒有資訊量，不記"""
    if normalized == _basic_normalize(original):
        return
    try:
        NORMALIZE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with NORMALIZE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "raw": original, "normalized": normalized}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------- 名稱解析（App、專案共用）：完全比對 → 拉丁字母容錯比對 ----------
FUZZY_MIN = 0.80  # 相似度門檻
FUZZY_MARGIN = 0.05  # 第 1、2 名（不同對象）差距小於此值視為歧義
FUZZY_MIN_LEN = 5  # 太短的英文字（如 chr）不做容錯


def _compact(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s.lower())


def _fuzzy_pick(key: str, index: Dict[str, Any]) -> Optional[Tuple[Any, float, bool]]:
    """只比對拉丁字母／數字的名稱（中文差一字常是另一個詞，不做模糊）。回傳 (對象, 分數, 是否歧義)"""
    if not re.fullmatch(r"[a-z0-9.]+", key) or len(key) < FUZZY_MIN_LEN:
        return None
    best: Dict[Any, float] = {}
    for k, value in index.items():
        if not re.fullmatch(r"[a-z0-9.]+", k):
            continue
        score = difflib.SequenceMatcher(None, key, k).ratio()
        if score > best.get(value, 0.0):
            best[value] = score
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < FUZZY_MIN:
        return None
    ambiguous = len(ranked) > 1 and ranked[0][1] - ranked[1][1] < FUZZY_MARGIN
    return ranked[0][0], ranked[0][1], ambiguous


def _resolve(token: str, index: Dict[str, Any], candidates: bool = False) -> Optional[Tuple[Any, str, str]]:
    """回傳 (對象, 信心 exact／fuzzy／candidate, 說明)。歧義時只有 candidates=True 才回傳（信心 candidate）"""
    key = _compact(token)
    if not key:
        return None
    if key in index:
        return index[key], "exact", ""
    hit = _fuzzy_pick(key, index)
    if hit is None:
        return None
    value, score, ambiguous = hit
    if ambiguous and not candidates:
        return None
    label = value[0] if isinstance(value, tuple) else value
    return value, ("candidate" if ambiguous else "fuzzy"), f"{token}→{label} ({score:.2f})"


def _alias_index() -> Dict[str, str]:
    return {_compact(k): v for k, v in MAC_APP_ALIASES.items()}


def resolve_app(token: str, candidates: bool = False) -> Optional[Tuple[str, str, str]]:
    return _resolve(token, _alias_index(), candidates)


def project_index() -> Dict[str, Tuple[str, str]]:
    """compact 名稱 → (專案名, 路徑)。詞庫 projects 優先，再補 PROJECT_ROOTS 第一層資料夾（略過隱藏資料夾）"""
    idx: Dict[str, Tuple[str, str]] = {}
    for p in LEXICON.get("projects", []):
        for k in [p["name"], *p.get("aliases", [])]:
            idx[_compact(k)] = (p["name"], p["path"])
    for root in PROJECT_ROOTS:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            path = os.path.join(root, name)
            if not name.startswith(".") and os.path.isdir(path):
                idx.setdefault(_compact(name), (name, path))
    return idx


def resolve_project(token: str, candidates: bool = False) -> Optional[Tuple[Tuple[str, str], str, str]]:
    return _resolve(token, project_index(), candidates)


def _alias_in(alias: str, text: str) -> bool:
    """alias 是否出現在語句中：英文別名用詞邊界；兩字以內的中文別名（如『設定』）必須接在開關動詞後，
    避免『音量設定為 50』被當成要開系統設定"""
    if re.fullmatch(r"[a-z0-9 ._\-]+", alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None
    if len(alias) <= 2:
        return re.search(rf"(?:{_VERB})(?:一下|個)?\s*{re.escape(alias)}", text) is not None
    return alias in text


def build_hint_lines(utterance: str) -> List[str]:
    """只挑出語句中實際出現的別名，產生『別名→標準名稱』提示，避免撐大 prompt；
    語句提到『專案』時再附上專案清單，讓 LLM 能填 project_name"""
    hints = []
    lowered = utterance.lower()
    for alias, name in MAC_APP_ALIASES.items():
        if alias != name.lower() and _alias_in(alias, lowered):
            hints.append(f"{alias}→{name}")
    if re.search(r"專案|project", lowered):
        for p in LEXICON.get("projects", []):
            for alias in p.get("aliases", []):
                if alias.lower() in lowered:
                    hints.append(f"{alias}→{p['name']}")
        hints.append("專案：" + "、".join(sorted({name for name, _ in project_index().values()})[:10]))
    return hints


# ==========================================
# 1. macOS 底層控制執行器 (System Executors)
# ==========================================
_FAIL_PREFIXES = ("❌", "⚠️", "⏭️")


def _run(cmd: List[str], timeout: Optional[float] = None) -> Tuple[bool, str]:
    """執行外部指令並檢查結束碼。回傳 (成功?, 成功時的 stdout／失敗時 stderr 最後一行)；
    timeout 用於可能卡在授權對話框的指令（逾時視為失敗）"""
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"逾時 {timeout:g}s（可能在等待授權對話框）"
    except OSError as e:  # 指令不存在或無法執行
        return False, str(e)[:120]
    if p.returncode == 0:
        return True, (p.stdout or "").strip()
    lines = (p.stderr or "").strip().splitlines()
    return False, (lines[-1][:120] if lines else f"結束碼 {p.returncode}")


def control_mac_app(app_name: str, action: str = "open") -> str:
    """透過 open -a 或 osascript 開關 macOS 應用程式"""
    normalized_key = app_name.strip().lower()
    target_app = MAC_APP_ALIASES.get(normalized_key, app_name)

    if action == "open":
        ok, err = _run(["open", "-a", target_app])
        return f"✅ 已開啟應用程式：{target_app}" if ok else f"❌ 開啟失敗：{target_app}（{err}）"
    elif action == "quit":
        script = f'tell application "{target_app}" to quit'
        ok, err = _run(["osascript", "-e", script])
        return f"✅ 已關閉應用程式：{target_app}" if ok else f"❌ 關閉失敗：{target_app}（{err}）"
    return f"⚠️ 未知的動作：{action}"


def set_system_volume(level: Optional[int] = None, mute: Optional[bool] = None) -> str:
    """透過 AppleScript 控制 macOS 系統音量"""
    results = []
    if level is not None:
        clamped_level = max(0, min(100, int(level)))
        ok, err = _run(["osascript", "-e", f"set volume output volume {clamped_level}"])
        if not ok:
            return f"❌ 設定音量失敗（{err}）"
        results.append(f"音量設為 {clamped_level}%")
    if mute is not None:
        mute_str = "true" if mute else "false"
        ok, err = _run(["osascript", "-e", f"set volume output muted {mute_str}"])
        if not ok:
            return f"❌ 設定靜音失敗（{err}）"
        results.append("已靜音" if mute else "已取消靜音")
    return "✅ " + "、".join(results)


def adjust_screen_brightness(direction: str, steps: int = 3) -> str:
    """透過模擬 macOS 亮度鍵 (Key Code 144=調亮, 145=調暗) 調整螢幕亮度"""
    key_code = 144 if direction == "up" else 145
    steps = max(1, min(16, int(steps)))
    script = f'tell application "System Events" to repeat {steps} times\nkey code {key_code}\ndelay 0.05\nend repeat'
    ok, err = _run(["osascript", "-e", script])
    action_text = "調亮" if direction == "up" else "調暗"
    return f"✅ 已將螢幕{action_text} {steps} 格" if ok else f"❌ 調整亮度失敗（{err}）"


def web_search_or_open_url(query: str, browser: Optional[str] = None) -> str:
    """使用瀏覽器開啟網址或進行 Google 搜尋；browser 由執行上下文帶入（剛開啟的瀏覽器），未指定則用預設瀏覽器"""
    if query.startswith("http://") or query.startswith("https://"):
        url = query
    else:
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    ok, err = _run(["open", "-a", browser, url] if browser else ["open", url])
    return f"✅ 已開啟瀏覽器搜尋：{query}" if ok else f"❌ 開啟瀏覽器失敗（{err}）"


def system_power_action(action: str) -> str:
    """控制深色模式、睡眠或鎖定螢幕"""
    if action == "toggle_dark_mode":
        script = 'tell application "System Events" to tell appearance preferences to set dark mode to not dark mode'
        ok, err = _run(["osascript", "-e", script])
        return "✅ 已切換深色/淺色模式" if ok else f"❌ 切換深色模式失敗（{err}）"
    elif action in ("dark_mode_on", "dark_mode_off"):
        on = action == "dark_mode_on"
        script = f'tell application "System Events" to tell appearance preferences to set dark mode to {"true" if on else "false"}'
        ok, err = _run(["osascript", "-e", script])
        if not ok:
            return f"❌ 設定深色模式失敗（{err}）"
        return "✅ 已開啟深色模式" if on else "✅ 已關閉深色模式（淺色）"
    elif action == "sleep":
        ok, err = _run(["pmset", "sleepnow"])
        return "✅ 系統進入睡眠" if ok else f"❌ 睡眠失敗（{err}）"
    elif action == "lock_screen":
        script = 'tell application "System Events" to keystroke "q" using {control down, command down}'
        ok, err = _run(["osascript", "-e", script])
        return "✅ 已鎖定螢幕" if ok else f"❌ 鎖定螢幕失敗（{err}）"
    return f"⚠️ 不支援的系統動作：{action}"


def open_project(project_name: str, editor: Optional[str] = None) -> str:
    """用編輯器開啟白名單內的專案資料夾（不接受任意路徑）；editor 預設 Visual Studio Code"""
    hit = resolve_project(project_name)
    if hit is None:
        known = "、".join(sorted({name for name, _ in project_index().values()})[:10]) or "（無）"
        return f"❌ 找不到專案：{project_name}（已知專案：{known}）"
    (name, path), _, _ = hit
    if not os.path.isdir(path):
        return f"❌ 專案路徑不存在：{path}"
    target = editor or "Visual Studio Code"
    ok, err = _run(["open", "-a", target, path])
    return f"✅ 已用 {target} 開啟專案：{name}" if ok else f"❌ 開啟專案失敗：{name}（{err}）"


# ---------- 執行上下文與讀畫面 ----------
@dataclass
class ExecContext:
    """同一次 process_voice_command 內共用的狀態（不跨語句）"""
    last_editor: Optional[str] = None
    last_browser: Optional[str] = None
    page_pending: Optional[dict] = None  # {"url", "t"}：剛開的網頁，讀畫面前要先等載入
    failed: Set[str] = field(default_factory=set)  # browser／editor／page：這幾類前置步驟失敗過
    replies: List[str] = field(default_factory=list)
    # 以下為「目前這一步」的狀態，由 router 在每步開始時重設
    step: Optional[dict] = None
    utterance: str = ""
    results: List[str] = field(default_factory=list)
    status: Optional[str] = None
    chat_done: bool = False


PAGE_SETTLE_S = 1.0  # 開網頁後至少等這麼久才開始問「載入完了嗎」（避免讀到舊頁面的 loading=false）
PAGE_LOAD_MAX_S = 8.0
PAGE_FIXED_WAIT_S = 2.5  # 非 Chrome 無法查載入狀態，固定等待
EXTERNAL_TIMEOUT_S = 15.0  # screencapture／sips 逾時
POLL_TIMEOUT_S = 5.0  # 查 Chrome 載入狀態的 osascript 逾時（第一次可能跳出自動化授權）
SCREEN_MAX_PX = 1280
SCREEN_QUESTION = "這個畫面在顯示什麼？用繁體中文條列重點。"
SCREEN_SYSTEM_PROMPT = "你是螢幕內容辨識助手。用繁體中文條列重點，最多 5 點，只描述畫面上看得到的內容，不要編造。"


def _wait_for_page(ctx: ExecContext) -> None:
    pending = ctx.page_pending
    if not pending:
        return
    elapsed = time.monotonic() - pending["t"]
    if ctx.last_browser == "Google Chrome":
        if PAGE_SETTLE_S - elapsed > 0:
            time.sleep(PAGE_SETTLE_S - elapsed)
        deadline = time.monotonic() + PAGE_LOAD_MAX_S
        while time.monotonic() < deadline:
            ok, out = _run(["osascript", "-e", 'tell application "Google Chrome" to return loading of active tab of front window'], POLL_TIMEOUT_S)
            if not ok or out.lower() != "true":
                break
            time.sleep(0.3)
    elif PAGE_FIXED_WAIT_S - elapsed > 0:
        time.sleep(PAGE_FIXED_WAIT_S - elapsed)
    ctx.page_pending = None


def read_screen(ctx: Optional[ExecContext] = None) -> str:
    """截主螢幕 → sips 縮到 1280 → 送 VL 模型辨識；辨識結果寫入 ctx.replies。暫存檔一律刪除"""
    ctx = ctx or ExecContext()
    step = ctx.step if ctx.step is not None else {}
    t = time.perf_counter()
    _wait_for_page(ctx)
    step["wait_ms"] = (time.perf_counter() - t) * 1000

    tmp = Path(tempfile.mkdtemp(prefix="macvoice_"))
    try:
        raw, small = tmp / "screen.png", tmp / "screen_small.png"
        t = time.perf_counter()
        ok, err = _run(["screencapture", "-x", "-m", str(raw)], EXTERNAL_TIMEOUT_S)
        step["capture_ms"] = (time.perf_counter() - t) * 1000
        if not ok or not raw.exists() or raw.stat().st_size == 0:
            return f"❌ 截圖失敗（{err or '沒有輸出檔'}；請確認已授權螢幕錄製）"
        t = time.perf_counter()
        ok, err = _run(["sips", "-Z", str(SCREEN_MAX_PX), str(raw), "--out", str(small)], EXTERNAL_TIMEOUT_S)
        step["resize_ms"] = (time.perf_counter() - t) * 1000
        if not ok or not small.exists():
            return f"❌ 縮圖失敗（{err}）"
        data_uri = "data:image/png;base64," + base64.b64encode(small.read_bytes()).decode()
        messages = [
            {"role": "system", "content": SCREEN_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": SCREEN_QUESTION},
            ]},
        ]
        meta: dict = {}
        t = time.perf_counter()
        try:
            message = _llm(messages, timeout=VISION_TIMEOUT_S, tools=False, max_tokens=256, meta=meta)
        except LLMError as e:
            ctx.status = "llm_unavailable" if isinstance(e, LLMUnavailable) else "llm_error"
            return f"❌ AI 無法辨識畫面（{e}）"
        finally:
            step["vl_ms"] = (time.perf_counter() - t) * 1000
            step["vl"] = meta
        text = to_traditional((message.get("content") or "").strip())
        if not text:
            return "❌ 畫面辨識沒有輸出內容"
        ctx.replies.append(text)
        return f"✅ 畫面內容：{text}"
    except OSError as e:
        return f"❌ 讀取截圖失敗（{e}）"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def chat_reply(ctx: Optional[ExecContext] = None) -> str:
    """chat call：把這一步的原句直接當成使用者訊息送入模型（不帶 tools），回覆寫入 ctx.replies。
    同一步只回覆一次；失敗只記錄，不拋例外"""
    ctx = ctx or ExecContext()
    if ctx.chat_done:
        return "✅ 已回覆"
    ctx.chat_done = True
    step = ctx.step if ctx.step is not None else {}
    meta: dict = {}
    t = time.perf_counter()
    try:
        message = _llm(
            [{"role": "system", "content": CHAT_SYSTEM_PROMPT}, {"role": "user", "content": ctx.utterance}],
            timeout=CHAT_TIMEOUT_S, tools=False, max_tokens=128, meta=meta,
        )
    except LLMError as e:
        ctx.status = "llm_unavailable" if isinstance(e, LLMUnavailable) else "llm_error"
        text = f"⚠️ AI 回覆失敗（{e}）"
        ctx.replies.append(text)
        return text
    finally:
        step["chat_ms"] = (time.perf_counter() - t) * 1000
        step["chat"] = meta
    reply = to_traditional((message.get("content") or "").strip()) or "無法理解指令"
    ctx.replies.append(reply)
    return f"🤖 AI 回覆：{reply}"


AVAILABLE_FUNCTIONS = {
    "control_mac_app": control_mac_app,
    "set_system_volume": set_system_volume,
    "adjust_screen_brightness": adjust_screen_brightness,
    "web_search_or_open_url": web_search_or_open_url,
    "system_power_action": system_power_action,
    "open_project": open_project,
    "read_screen": read_screen,
    "chat_reply": chat_reply,
}


def _skipped(name: str) -> str:
    return f"⏭️ 略過 {name}：前一步失敗"


def _dispatch(name: str, args: dict, ctx: ExecContext) -> str:
    """執行單一工具，並維護 ExecContext：記住剛開的瀏覽器／編輯器、剛開的網頁；前置步驟失敗時略過依賴它的步驟"""
    if name == "control_mac_app":
        res = control_mac_app(**args)
        target = MAC_APP_ALIASES.get(args["app_name"].strip().lower(), args["app_name"])
        kind = APP_KIND.get(target, "other")
        if args.get("action", "open") == "open" and kind in ("browser", "editor"):
            if res.startswith(_FAIL_PREFIXES):
                ctx.failed.add(kind)
            else:
                ctx.failed.discard(kind)
                if kind == "browser":
                    ctx.last_browser = target
                else:
                    ctx.last_editor = target
        return res
    if name == "web_search_or_open_url":
        if "browser" in ctx.failed:
            return _skipped(name)
        res = web_search_or_open_url(**args, browser=ctx.last_browser)
        if res.startswith(_FAIL_PREFIXES):
            ctx.failed.add("page")
        else:
            ctx.failed.discard("page")
            ctx.page_pending = {"t": time.monotonic()}
        return res
    if name == "open_project":
        if "editor" in ctx.failed:
            return _skipped(name)
        return open_project(args["project_name"], args.get("editor") or ctx.last_editor)
    if name == "read_screen":
        if ctx.failed & {"browser", "page"}:
            return _skipped(name)
        return read_screen(ctx)
    if name == "chat_reply":
        return chat_reply(ctx)
    return AVAILABLE_FUNCTIONS[name](**args)


def run_tool(name: str, args: dict, ctx: ExecContext) -> str:
    """執行一個工具並記錄耗時（ctx.step["exec_ms"]）與結果（ctx.results）"""
    t = time.perf_counter()
    res = _dispatch(name, dict(args), ctx)
    if ctx.step is not None:
        ctx.step.setdefault("exec_ms", []).append((time.perf_counter() - t) * 1000)
    ctx.results.append(res)
    return res


# ==========================================
# 2. 文本斷句器與快速軌道 (Fast Path)
# ==========================================
_PREFIX_RE = re.compile(rf"^(?:{_alt(_FILLERS.get('prefix', []))})\s*") if _FILLERS.get("prefix") else None
_SUFFIX_RE = re.compile(rf"\s*(?:{_alt(_FILLERS.get('suffix', []))})$") if _FILLERS.get("suffix") else None
_ACTION_WORDS_RE = re.compile(rf"{_VERB}|搜尋|搜索|查|調|切換|鎖|辨識|看|念")
_BA_RE = re.compile(rf"^把\s*(?P<x>.+?)\s*(?P<v>{_VERB})$")
_TOPIC_RE = re.compile(rf"^(?P<x>.+?)\s*(?P<v>{_LONG_VERB})$")

# 安全守門：否定、敘述、提問一律不走 Fast（交給 LLM）
_NEG_RE = re.compile(r"不要|不用|不必|不想|不能|不可以|不行|不需要|別|勿|沒有|先不")
_NARR_RE = re.compile(rf"昨天|前天|剛剛|剛才|之前|已經|以前|上次|曾經|本來|原本|(?:{_VERB})了")
_QUESTION_RE = re.compile(r"嗎|什麼|怎麼|為什麼|為何|如何|是不是|有沒有|哪|幾|多少|\?")


def canonicalize_clause(clause: str) -> str:
    """只給 Fast Path 用：去疊用的前綴填充詞與語尾助詞，並把『把 X 打開』『X 打開』改寫成『打開 X』。
    送給 LLM 的仍是原句（填充詞要留給 LLM 判斷語氣）"""
    s = clause.strip()
    while True:
        t = s
        if _PREFIX_RE:
            t = _PREFIX_RE.sub("", t, count=1).strip()
        if _SUFFIX_RE:
            t = _SUFFIX_RE.sub("", t, count=1).strip()
        if t == s:
            break
        s = t
    for pattern in (_BA_RE, _TOPIC_RE):
        m = pattern.match(s)
        if m and not _ACTION_WORDS_RE.search(m["x"]):
            return f"{m['v']}{m['x']}"
    return s


class Match:
    """Fast Path 比對結果：可呼叫（執行全部動作）。比對與執行分開，路由器才能依說話順序決定何時執行"""

    def __init__(self, actions: List[Tuple[str, dict]], confidence: str = "exact", note: str = ""):
        self.actions = actions
        self.confidence = confidence  # exact／fuzzy／candidate
        self.note = note

    def run(self, ctx: Optional[ExecContext] = None) -> List[str]:
        ctx = ctx or ExecContext()
        return [run_tool(name, args, ctx) for name, args in self.actions]

    def __call__(self, ctx: Optional[ExecContext] = None) -> str:
        return "；".join(self.run(ctx))


_SEARCH = _alt(["搜尋", "搜索", "查詢", "查一下", "查查", "查"])
_DARK = "(?:深色|暗黑|夜間|暗色|黑暗)"
_LIGHT = "(?:淺色|亮色|日間)"
_VOL_SET = r"(?:調到|調成|調整到|調整為|設為|設定為|設成|設定成|開到|改成|改為|到|成|為)?"
_I = re.IGNORECASE


class FastPathMatcher:
    # --- 讀畫面（含『什麼』問句，所以在問句守門之前判斷）---
    _read_screen_re = (
        re.compile(r"^(?:辨識|辨認|識別|解讀|分析|看看|看|讀|念)(?:一下)?(?:目前的?|現在的?|這個?)?(?:(?:頁面|網頁|畫面|頁|螢幕)(?:上)?(?:的)?(?:內容)?|內容)$"),
        re.compile(r"^(?:這個?)?(?:頁面|網頁|畫面|頁)(?:上)?(?:在)?(?:寫|說|講|有|顯示)(?:了)?什麼$"),
        re.compile(r"^(?:螢幕|畫面)上有什麼$"),
    )
    # --- 其餘規則 ---
    _open_search_re = re.compile(rf"^(?P<v>{_OPEN})(?:一下|個)?\s*(?P<app>.+?)\s*(?:並且|並|然後|再|且)?\s*(?:{_SEARCH})\s*(?P<q>.+)$", _I)
    _search_res = (
        re.compile(rf"^(?:上網|網路上|網上|線上)?(?:{_SEARCH})\s*(?P<q>.+)$", _I),
        re.compile(rf"^(?:用\s*)?(?:google|谷歌)\s*(?:一下|{_SEARCH})\s*(?P<q>.+)$", _I),
    )
    _project_re = re.compile(rf"^(?:(?:用|使用|在)\s*(?P<ed>.+?)\s*)?(?:{_OPEN})(?:一下|個)?\s*(?P<proj>.+?)\s*(?:的)?(?:專案|項目|project)$", _I)
    _vol_level_re = re.compile(rf"^(?:把)?(?:系統)?(?:音量|聲音)\s*{_VOL_SET}\s*(?P<n>\d{{1,3}})\s*(?:%|趴|度)?$")
    _vol_word_re = re.compile(rf"^(?:把)?(?:系統)?(?:音量|聲音)\s*{_VOL_SET}\s*(?P<w>最大|最小|最高|最低|一半)$")
    _mute_re = re.compile(r"^(?:靜音|(?:關掉|關閉|關)(?:系統)?(?:聲音|音量))$")
    _unmute_re = re.compile(r"^(?:取消靜音|解除靜音|(?:打開|開啟|開)(?:系統)?(?:聲音|音量))$")
    _bright_re = re.compile(
        r"^(?:把)?(?P<subj>(?:螢幕|屏幕|畫面)?(?:的)?(?:亮度)?)\s*(?:稍微|再)?(?P<verb>調整|調|弄|開|變|設定|設)?(?:得|到|成)?(?P<most>最)?(?P<d>暗|亮|低|高)\s*(?P<rest>.*)$"
    )
    _dark_on_re = re.compile(rf"^(?:打開|開啟|開|啟用|切換到|切換成|切到|切成|換成|改成|轉成)(?:一下)?{_DARK}模式$")
    _dark_off_re = re.compile(rf"^(?:(?:關掉|關閉|關|停用|取消|退出)(?:一下)?{_DARK}模式|(?:切換到|切換成|切到|切成|換成|改成|轉成)(?:一下)?{_LIGHT}模式|(?:打開|開啟|開|啟用){_LIGHT}模式)$")
    _dark_toggle_re = re.compile(rf"^切換{_DARK}模式$")
    _lock_res = (re.compile(r"^(?:鎖定|鎖)(?:螢幕|屏幕|畫面|電腦|屏)$"), re.compile(r"^(?:把)?(?:螢幕|電腦)(?:鎖定|鎖起來|鎖住)$"))
    _app_re = re.compile(rf"^(?P<v>{_VERB})(?:一下|個|一個)?\s*(?:那個|這個)?\s*(?P<app>.+)$", _I)

    def split_clauses(self, text: str) -> List[str]:
        """將 STT 輸出的長句依據標點符號與中文口語連接詞切分為獨立子句（不以空白切分，以免拆壞「VS Code」「調到 50」）"""
        pattern = r"[，。、；,;！!？?]+|(?:然後|接著|再幫我|順便|還有|並且|以及)"
        return [c.strip() for c in re.split(pattern, text) if c.strip()]

    def match(self, clause: str, candidates: bool = False) -> Optional[Match]:
        """以 Fast Path 比對單一子句；命中回傳「尚未執行」的 Match，未命中回傳 None。
        candidates=True 時，歧義（兩個 App 相似度太接近）的第 1 名也會回傳（信心 candidate），
        供 LLM 不可用時補救；被安全守門擋下的句子永遠回傳 None。"""
        raw = clause.strip()
        if raw == "讓電腦睡眠":  # 睡眠誤觸成本高：只接受這個固定句，不做放寬
            return Match([("system_power_action", {"action": "sleep"})])
        s = canonicalize_clause(raw)
        if not s or _NEG_RE.search(s) or _NARR_RE.search(s):
            return None
        for pattern in self._read_screen_re:
            if pattern.match(s):
                return Match([("read_screen", {})])
        if _QUESTION_RE.search(s):
            return None
        for rule in (
            self._r_open_search, self._r_search, self._r_project, self._r_volume, self._r_mute,
            self._r_brightness, self._r_dark, self._r_lock, self._r_app,
        ):
            m = rule(s, candidates)
            if m is not None:
                return m
        return None

    # ---- 規則：命中回傳 Match，否則 None ----
    def _r_open_search(self, s: str, candidates: bool) -> Optional[Match]:
        """『開 google 搜尋 X』→ 開瀏覽器 + 搜尋；『開瀏覽器查 X』→ 只搜尋，不另外開 App"""
        m = self._open_search_re.match(s)
        if not m or not m["q"].strip():
            return None
        query = m["q"].strip()
        search = ("web_search_or_open_url", {"query": query})
        if _compact(m["app"]) in ("瀏覽器", "browser"):
            return Match([search])
        res = resolve_app(m["app"], candidates)
        if res is None or APP_KIND.get(res[0]) != "browser":
            return None
        name, conf, note = res
        return Match([("control_mac_app", {"app_name": name, "action": "open"}), search], conf, note)

    def _r_search(self, s: str, candidates: bool) -> Optional[Match]:
        for pattern in self._search_res:
            m = pattern.match(s)
            if m:
                query = m["q"].strip()
                if query and not query.startswith("看"):  # 『查看…』不是搜尋
                    return Match([("web_search_or_open_url", {"query": query})])
        return None

    def _r_project(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._project_re.match(s)
        if not m:
            return None
        args: Dict[str, Any] = {}
        conf, note = "exact", ""
        if m["ed"]:
            ed = resolve_app(m["ed"], candidates)
            if ed is None or APP_KIND.get(ed[0]) != "editor":
                return None
            args["editor"], conf, note = ed
        hit = resolve_project(m["proj"], candidates)
        if hit is not None:
            args["project_name"] = hit[0][0]
            if hit[1] != "exact":
                conf, note = hit[1], hit[2]
        else:  # 不在白名單：照樣比對成功，由 open_project 回報「找不到專案」，不交給 LLM 亂猜
            args["project_name"] = m["proj"].strip()
        return Match([("open_project", args)], conf, note)

    def _r_volume(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._vol_level_re.match(s)
        if m:
            return Match([("set_system_volume", {"level": int(m["n"])})])
        m = self._vol_word_re.match(s)
        if m:
            level = {"最大": 100, "最高": 100, "最小": 0, "最低": 0, "一半": 50}[m["w"]]
            return Match([("set_system_volume", {"level": level})])
        return None

    def _r_mute(self, s: str, candidates: bool) -> Optional[Match]:
        if self._mute_re.match(s):
            return Match([("set_system_volume", {"mute": True})])
        if self._unmute_re.match(s):
            return Match([("set_system_volume", {"mute": False})])
        return None

    def _r_brightness(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._bright_re.match(s)
        if not m:
            return None
        has_subject = bool(m["subj"])
        if m["d"] in ("低", "高") and not has_subject:
            return None  # 『調高』可能是音量，必須指明螢幕或亮度
        if not has_subject and not (m["most"] and m["verb"]):
            return None
        direction = "down" if m["d"] in ("暗", "低") else "up"
        rest = m["rest"].strip()
        if m["most"]:
            return Match([("adjust_screen_brightness", {"direction": direction, "steps": 16})]) if not rest else None
        if rest in ("", "一點", "一點點", "一些", "些", "稍微"):
            steps = 3
        else:
            n = re.fullmatch(r"(\d+)\s*格", rest)
            if not n:
                return None
            steps = int(n[1])
        return Match([("adjust_screen_brightness", {"direction": direction, "steps": steps})])

    def _r_dark(self, s: str, candidates: bool) -> Optional[Match]:
        if self._dark_on_re.match(s):
            return Match([("system_power_action", {"action": "dark_mode_on"})])
        if self._dark_off_re.match(s):
            return Match([("system_power_action", {"action": "dark_mode_off"})])
        if self._dark_toggle_re.match(s):
            return Match([("system_power_action", {"action": "toggle_dark_mode"})])
        return None

    def _r_lock(self, s: str, candidates: bool) -> Optional[Match]:
        if any(p.match(s) for p in self._lock_res):
            return Match([("system_power_action", {"action": "lock_screen"})])
        return None

    def _r_app(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._app_re.match(s)
        if not m:
            return None
        res = resolve_app(m["app"], candidates)
        if res is None:
            return None
        name, conf, note = res
        action = "quit" if re.fullmatch(_QUIT, m["v"]) else "open"
        return Match([("control_mac_app", {"app_name": name, "action": action})], conf, note)

    def try_execute(self, clause: str) -> Optional[str]:
        """嘗試以 Fast Path 匹配並立即執行單一子句，若未命中則回傳 None"""
        run = self.match(clause)
        return run() if run else None


# ==========================================
# 3. AI 軌道 (Slow Path - mlx-vlm + LFM3-VL-2B Function Calling)
# ==========================================
MLX_VLM_URL = "http://127.0.0.1:8080"
MODEL_ID = "AI_models/LFM3-VL-2B-MLX-4bit"  # 須與 mlx_vlm.server 的 --model 一致

PLAN_TIMEOUT_S = 10.0  # 規劃（工具呼叫）逾時
CHAT_TIMEOUT_S = 10.0
VISION_TIMEOUT_S = 30.0
WARMUP_TIMEOUT_S = 60.0  # 首次載入較慢
BREAKER_COOLDOWN_S = 30.0  # LLM 連不上之後，這段時間內不再嘗試

SYSTEM_PROMPT = (
    "你是運行在 macOS 上的本地語音控制助手。依使用者的繁體中文指令呼叫工具，可一次呼叫多個。"
    "若指令與任何工具無關，不要呼叫工具，直接簡短回覆。"
    "『打開瀏覽器查某事』只需呼叫 web_search_or_open_url，不要另外開啟瀏覽器。"
    "app_name 必須使用標準英文名稱。"
)
CHAT_SYSTEM_PROMPT = (
    "你是運行在 macOS 上的本地語音助手。用繁體中文簡短回答（兩句以內）。"
    "句子中的電腦操作（開關程式、音量、亮度、搜尋等）已由其他程式處理，不要描述或重複，只回答提問或閒聊的部分。"
)


def _fn(name: str, description: str, properties: dict, required: tuple = ()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)},
        },
    }


TOOLS_SCHEMA = [
    _fn(
        "control_mac_app",
        "在 macOS 上打開或關閉指定的應用程式。",
        {
            "app_name": {
                "type": "string",
                "description": "macOS 應用程式標準英文名稱，如 'Google Chrome', 'Safari', 'Terminal', 'Visual Studio Code', 'Cursor', 'Calculator', 'Notes', 'Finder', 'Spotify'",
            },
            "action": {"type": "string", "enum": ["open", "quit"]},
        },
        ("app_name", "action"),
    ),
    _fn(
        "set_system_volume",
        "調整 macOS 系統音量（0-100）或切換靜音。",
        {
            "level": {"type": "integer", "description": "音量百分比 0-100"},
            "mute": {"type": "boolean", "description": "是否靜音"},
        },
    ),
    _fn(
        "adjust_screen_brightness",
        "調亮或調暗 macOS 螢幕亮度。",
        {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "steps": {"type": "integer", "description": "調整格數 1-16，『一點』約為 3，『最亮/最暗』為 16"},
        },
        ("direction", "steps"),
    ),
    _fn(
        "web_search_or_open_url",
        "使用瀏覽器進行 Google 搜尋（如查天氣、查新聞）或打開特定網站。",
        {"query": {"type": "string", "description": "搜尋關鍵字或網址"}},
        ("query",),
    ),
    _fn(
        "system_power_action",
        "macOS 系統控制：睡眠、鎖定螢幕、深色模式。打開深色模式用 dark_mode_on，關閉用 dark_mode_off。",
        {"action": {
            "type": "string",
            "enum": ["dark_mode_on", "dark_mode_off", "lock_screen", "sleep", "toggle_dark_mode"],
            "description": "toggle_dark_mode 只在使用者明說『切換』時使用",
        }},
        ("action",),
    ),
    _fn(
        "open_project",
        "用編輯器開啟一個已知的程式專案資料夾。",
        {
            "project_name": {"type": "string", "description": "專案名稱，如 'mac_ai_control'"},
            "editor": {"type": "string", "description": "編輯器標準英文名稱，如 'Visual Studio Code', 'Cursor'；使用者沒指定時不要填"},
        },
        ("project_name",),
    ),
    _fn(
        "read_screen",
        "看使用者的螢幕畫面並說明內容。使用者問『畫面／螢幕／這頁／網頁上有什麼、寫什麼、在講什麼』時呼叫。不需要參數。",
        {},
    ),
    _fn(
        "chat_reply",
        "使用者在提問、閒聊或需要文字回答時呼叫（如『Python 是什麼』『今天心情不錯』）。不需要參數。",
        {},
    ),
]

_SCHEMA_BY_NAME = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS_SCHEMA}


def validate_tool_call(name: str, args: Any) -> Tuple[Optional[dict], Optional[str]]:
    """檢查 LLM 回傳的工具名稱與參數，回傳 (乾淨參數, None) 或 (None, 錯誤原因)。
    只信任 schema：未知工具、未知參數、缺必填、型別錯誤、enum 不符一律拒絕，不交給 executor。"""
    schema = _SCHEMA_BY_NAME.get(name)
    if schema is None:
        return None, f"未知工具：{name}"
    if not isinstance(args, dict):
        return None, f"{name} 參數不是物件"
    props = schema["properties"]
    clean: Dict[str, Any] = {}
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            return None, f"{name} 出現未定義參數：{key}"
        if spec["type"] == "integer":
            if isinstance(value, bool):
                return None, f"{name}.{key} 應為整數"
            try:
                value = int(value)
            except (TypeError, ValueError):
                return None, f"{name}.{key} 應為整數"
        elif spec["type"] == "boolean":
            if not isinstance(value, bool):
                return None, f"{name}.{key} 應為布林"
        elif spec["type"] == "string":
            if not isinstance(value, str) or not value.strip():
                return None, f"{name}.{key} 應為非空字串"
        if "enum" in spec and value not in spec["enum"]:
            return None, f"{name}.{key} 不在允許值 {spec['enum']}"
        clean[key] = value
    missing = [k for k in schema["required"] if k not in clean]
    if missing:
        return None, f"{name} 缺少必填參數：{missing}"
    # 實測：LLM 在 mute=true 時偶爾多塞 level（如 30），靜音語意以 mute 為準
    if name == "set_system_volume" and clean.get("mute") is True:
        clean.pop("level", None)
    # 專案只能從白名單解析，不接受 LLM 給的任意名稱／路徑；editor 必須是編輯器類 App
    if name == "open_project":
        hit = resolve_project(clean["project_name"])
        if hit is None:
            return None, f"open_project 找不到專案：{clean['project_name']}"
        clean["project_name"] = hit[0][0]
        if "editor" in clean:
            editor = resolve_app(clean["editor"])
            if editor is None or APP_KIND.get(editor[0]) != "editor":
                return None, f"open_project.editor 不是已知編輯器：{clean['editor']}"
            clean["editor"] = editor[0]
    return clean, None


class LLMError(Exception):
    """LLM 回應無法使用（格式錯誤、HTTP 錯誤）"""


class LLMUnavailable(LLMError):
    """LLM 連不上或逾時（含熔斷中）"""


_llm_down_until = 0.0  # time.monotonic() 時間點；之前不再呼叫 LLM


def reset_llm_breaker() -> None:
    global _llm_down_until
    _llm_down_until = 0.0


def _chat(messages: List[dict], timeout: float = PLAN_TIMEOUT_S, tools: bool = True,
          max_tokens: int = 512, meta: Optional[dict] = None) -> dict:
    """呼叫 mlx_vlm.server 的 OpenAI 相容 /chat/completions；回傳第一個 choice 的 message。
    meta 若有傳入，回填 server 的 timings（prefill_ms／decode_ms／prompt_n／cache_n／predicted_n）"""
    body = {
        "model": MODEL_ID,
        "messages": messages,
        # 官方建議取樣參數；enable_thinking=False 關閉 <think>，否則延遲倍增
        "temperature": 0.1,
        "top_p": 1.0,
        "top_k": 50,
        "repetition_penalty": 1.1,
        "max_tokens": max_tokens,
        "enable_thinking": False,
    }
    if tools:
        body["tools"] = TOOLS_SCHEMA
    req = urllib.request.Request(
        f"{MLX_VLM_URL}/chat/completions",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if meta is not None:
        t = data.get("timings") or {}
        meta.update(
            prefill_ms=t.get("prompt_ms"), decode_ms=t.get("predicted_ms"),
            prompt_n=t.get("prompt_n"), cache_n=t.get("cache_n"), predicted_n=t.get("predicted_n"),
        )
    message = data["choices"][0]["message"]
    if not isinstance(message, dict):
        raise TypeError("message 不是物件")
    return message


def _llm(messages: List[dict], *, timeout: float, tools: bool = True, max_tokens: int = 512,
         meta: Optional[dict] = None) -> dict:
    """所有 LLM 呼叫的入口：把連線／逾時／格式錯誤轉成 LLMError，並在連不上時啟動熔斷"""
    global _llm_down_until
    if time.monotonic() < _llm_down_until:
        raise LLMUnavailable("AI 服務暫時無法使用（熔斷中）")
    try:
        return _chat(messages, timeout=timeout, tools=tools, max_tokens=max_tokens, meta=meta)
    except urllib.error.HTTPError as e:  # server 有回應但出錯：不熔斷
        raise LLMError(f"HTTP {e.code}") from e
    except (OSError, http.client.HTTPException) as e:  # 連線被拒、逾時、中斷
        _llm_down_until = time.monotonic() + BREAKER_COOLDOWN_S
        raise LLMUnavailable(str(e) or type(e).__name__) from e
    except (KeyError, IndexError, TypeError, ValueError) as e:  # 含 JSONDecodeError
        raise LLMError(f"回應格式錯誤：{type(e).__name__}") from e


def plan_slow_path(utterance: str, meta: Optional[dict] = None) -> Tuple[List[Tuple[str, dict]], Optional[str], List[str]]:
    """送 LLM 規劃工具呼叫。回傳 (通過驗證的 [(工具, 參數)], LLM 純文字回覆, 被拒絕的原因)。
    meta 若有傳入，回填 llm（server timings）與 validate_ms；LLM 不可用時拋出 LLMError"""
    hints = build_hint_lines(utterance)
    system = SYSTEM_PROMPT + (f"\n已知詞庫：{'；'.join(hints)}" if hints else "")
    llm_meta: dict = {}
    message = _llm(
        [{"role": "system", "content": system}, {"role": "user", "content": utterance}],
        timeout=PLAN_TIMEOUT_S, meta=llm_meta,
    )

    t = time.perf_counter()
    calls: List[Tuple[str, dict]] = []
    rejected: List[str] = []
    for call in message.get("tool_calls") or []:
        func = call.get("function") if isinstance(call, dict) else None
        if not isinstance(func, dict):
            rejected.append("tool_call 格式錯誤（缺少 function 物件）")
            continue
        raw_args = func.get("arguments", {})
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            rejected.append(f"{func.get('name')} 參數不是合法 JSON")
            continue
        clean, err = validate_tool_call(func.get("name", ""), args)
        if err:
            rejected.append(err)
        else:
            calls.append((func["name"], clean))
    if meta is not None:
        meta["llm"] = llm_meta
        meta["validate_ms"] = (time.perf_counter() - t) * 1000
    return calls, message.get("content"), rejected


def execute_slow_path_llm(utterance: str, timing: Optional[dict] = None, ctx: Optional[ExecContext] = None) -> List[str]:
    """將未命中的自然語言指令送入本地 LLM 進行多工具拆解與執行；
    若傳入 timing，會回填 llm_ms（規劃耗時）、llm（server timings）、validate_ms。
    LLM 不可用時拋出 LLMError，由呼叫端決定補救"""
    step = timing if timing is not None else {}
    ctx = ctx or ExecContext()
    ctx.utterance = utterance
    t0 = time.perf_counter()
    try:
        calls, reply, rejected = plan_slow_path(utterance, step)
    finally:
        step["llm_ms"] = (time.perf_counter() - t0) * 1000
    llm = step.get("llm") or {}
    if llm.get("prefill_ms") is not None and llm.get("decode_ms") is not None:
        llm["overhead_ms"] = max(0.0, step["llm_ms"] - llm["prefill_ms"] - llm["decode_ms"])

    logs = [f"⚠️ 已拒絕不合法的工具呼叫：{r}" for r in rejected]
    if not calls and not rejected:
        # 沒有工具可呼叫：規劃呼叫的 content 不採用，另外觸發 chat call
        return [chat_reply(ctx)]
    if rejected and not calls:
        ctx.status = "rejected"
    for name, args in calls:
        res = run_tool(name, args, ctx)
        logs.append(f"[Slow Path - LLM] {name}({args}) -> {res}")
    return logs


def warmup_llm() -> Optional[float]:
    """啟動時送一次極短請求，把首次載入／編譯成本吃掉；回傳耗時秒數。
    LLM 連不上時只印警告並回傳 None，不讓程式結束（Fast Path 照常可用）"""
    start = time.perf_counter()
    try:  # 帶上與正式請求相同的 system prompt，prefix cache 才會對到
        _llm([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "你好"}], timeout=WARMUP_TIMEOUT_S)
    except LLMError as e:
        print(f"⚠️ LLM 暖機失敗：{e}（Fast Path 仍可使用）")
        return None
    reset_llm_breaker()
    return time.perf_counter() - start


# ==========================================
# 4. 雙軌整合路由器 (Main Voice Command Pipeline)
# ==========================================
def _step_status(ctx: ExecContext) -> str:
    if ctx.status:
        return ctx.status
    if any(r.startswith(("❌", "⚠️")) for r in ctx.results):
        return "exec_failed"
    if any(r.startswith("⏭️") for r in ctx.results):
        return "skipped"
    return "ok"


def _recover_slow_segment(clauses: List[str], matcher: FastPathMatcher, ctx: ExecContext, err: LLMError) -> List[str]:
    """LLM 不可用時的補救：有 Fast 候選（歧義而被擋下的第 1 名）就執行；沒有就告知使用者。
    被安全守門擋下的句子沒有候選，不會執行"""
    ctx.status = "llm_unavailable" if isinstance(err, LLMUnavailable) else "llm_error"
    logs = []
    for clause in clauses:
        cand = matcher.match(clause, candidates=True)
        if cand is not None:
            logs += [f"[Fallback | {cand.note}] {r}" for r in cand.run(ctx)]
            ctx.status = "fallback"
        else:
            msg = f"⚠️ AI 服務無法使用（{err}），這句沒有處理：「{clause}」"
            ctx.replies.append(msg)
            logs.append(msg)
    return logs


def process_voice_command(stt_text: str) -> dict:
    """依說話順序執行。命中 Fast Path 的子句各自成一步；連續未命中的子句合併成一步送 LLM。
    回傳 {"stages", "steps": [{kind, text, ms, status, logs, ...}], "replies", "total_ms"}，ms 含該步的執行時間"""
    print(f"\n🎙️ 收到語音辨識文字：「{stt_text}」")
    start_time = time.perf_counter()

    t = time.perf_counter()
    normalized = normalize_utterance(stt_text)
    stages = {"normalize_ms": (time.perf_counter() - t) * 1000}
    log_normalization(stt_text, normalized)
    stt_text = normalized

    matcher = FastPathMatcher()
    t = time.perf_counter()
    clauses = matcher.split_clauses(stt_text)
    stages["split_ms"] = (time.perf_counter() - t) * 1000
    print(f"✂️ 子句切分結果：{clauses}")

    t = time.perf_counter()
    segments: List[Tuple[str, List[str], Optional[Match]]] = []
    for clause in clauses:
        run = matcher.match(clause)
        if run:
            segments.append(("fast", [clause], run))
        elif segments and segments[-1][0] == "slow":
            segments[-1][1].append(clause)
        else:
            segments.append(("slow", [clause], None))
    stages["match_ms"] = (time.perf_counter() - t) * 1000

    ctx = ExecContext()
    steps = []
    for kind, texts, run in segments:
        text = "，".join(texts)
        step_start = time.perf_counter()
        step: Dict[str, Any] = {"kind": kind, "text": text}
        ctx.step, ctx.utterance, ctx.results, ctx.status, ctx.chat_done = step, text, [], None, False
        if kind == "fast":
            logs = run.run(ctx)
            step["match"] = {"confidence": run.confidence, "note": run.note}
            step["ms"] = (time.perf_counter() - step_start) * 1000
            for log in logs:
                print(f"⚡ [Fast Path | {step['ms']:.2f}ms] 「{text}」 -> {log}")
        else:
            print(f"🧠 [轉交 Slow Path AI 軌道] 待解析語句：「{text}」")
            try:
                logs = execute_slow_path_llm(text, step, ctx)
            except LLMError as e:
                logs = _recover_slow_segment(texts, matcher, ctx, e)
            step["ms"] = (time.perf_counter() - step_start) * 1000
            print(f"🤖 [Slow Path | {step['ms']:.0f}ms，其中 LLM {step.get('llm_ms', 0):.0f}ms]")
            for log in logs:
                print(f"   {log}")
        step["status"] = _step_status(ctx)
        step["logs"] = logs
        steps.append(step)
    ctx.step = None

    total_ms = (time.perf_counter() - start_time) * 1000
    for reply in ctx.replies:
        print(f"💬 回覆：{reply}")
    print(f"⏱️ 階段：normalize {stages['normalize_ms']:.2f}ms｜split {stages['split_ms']:.2f}ms｜match {stages['match_ms']:.2f}ms")
    print(f"⏱️ 全部完成：{total_ms:.0f}ms（{len(steps)} 步）")
    return {"stages": stages, "steps": steps, "replies": ctx.replies, "total_ms": total_ms}


if __name__ == "__main__":
    if len(sys.argv) > 1:  # 直接送入文字，模擬收到 STT 輸出
        process_voice_command(" ".join(sys.argv[1:]))
    else:
        warm = warmup_llm()
        if warm is not None:
            print(f"LLM 暖機完成：{warm:.2f}s")
        process_voice_command("幫我打開小算盤，然後把音量調到 40")
        process_voice_command("打開終端機，順便幫我把螢幕調暗一點，還有開瀏覽器查一下台北天氣")
