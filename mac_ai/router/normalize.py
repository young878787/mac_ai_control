"""STT 文字正規化：簡轉繁、誤聽詞、中文數字、疊詞。"""
import json
import re
import time
import unicodedata
from typing import Optional

from mac_ai.config import NORMALIZE_LOG
from mac_ai.router.lexicon import APP_MISHEARD, S2T, TERM_MISHEARD, _LONG_VERB, _alt


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
