"""預先詞庫（單一來源：vocab/lexicon.json）與名稱解析（App、專案）。"""
import difflib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mac_ai.config import LEXICON_PATH


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
    wake = lex.get("wake_words", {})
    for entry in wake.get("misheard", []):
        sources[entry["text"].lower()] = entry.get("source", "observed")
    if "misheard" in wake:
        wake["misheard"] = [e["text"] for e in wake["misheard"]]
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
