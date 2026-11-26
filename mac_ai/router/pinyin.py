"""拼音比對（macOS ICU，ctypes 呼叫，不新增依賴）：youtube_client 的接受規則、music_prefs 的 terms.pinyin 欄位與矯正共用。
純文字處理的底層模組：不碰資料庫、網路與子程序。"""
import ctypes
import functools
import re
import threading
import unicodedata
from typing import List, Optional, Tuple

# ---------- 拼音（無聲調、放寬）----------
# 放寬：zh→z、ch→c、sh→s（聲母）、ing→in、eng→en（韻尾）。STT 常把這幾組搞混（周杰倫／走杰倫、晴天／青天）；
# 兩邊都經過同一個函式，所以只會讓「同音或近音」的詞對在一起，不會產生新的歧義來源（改寫條件在 §4.5，不在這裡）。
_ICU_LIB = "/usr/lib/libicucore.dylib"
_ICU_ID = "Han-Latin; Latin-ASCII; Lower"  # 漢字→帶調拼音→去調→小寫
_HAN = "㐀-䶿一-鿿豈-﫿"
_UNIT_RE = re.compile(rf"[{_HAN}]|(?:(?![{_HAN}])[^\W_])+")  # 一個漢字，或一段連續的非漢字英數字（當一個 token）
_HAN_RE = re.compile(rf"[{_HAN}]")

_icu_lock = threading.Lock()  # utrans 物件不是執行緒安全的；搜尋執行緒與 command_worker 都會用，整個轉換在鎖內（約 0.025 ms）
_icu: Optional[Tuple[ctypes.CDLL, int]] = None
_icu_failed = False


def _u16(s: str):
    raw = s.encode("utf-16-le")
    return (ctypes.c_uint16 * (len(raw) // 2)).from_buffer_copy(raw)


def _open_icu() -> Optional[Tuple[ctypes.CDLL, int]]:
    """第一次用到時才開，轉換器只建立一次。找不到 ICU 就回 None（拼音退化成原字，比對只剩字面）。呼叫端要持有 _icu_lock"""
    global _icu, _icu_failed
    if _icu is not None or _icu_failed:
        return _icu
    try:
        lib = ctypes.CDLL(_ICU_LIB)  # macOS 的 libicucore 符號沒有版本後綴（utrans_openU，不是 utrans_openU_NN）
        lib.utrans_openU.restype = ctypes.c_void_p
        lib.utrans_openU.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int, ctypes.c_void_p, ctypes.c_int32,
                                     ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        lib.utrans_transUChars.restype = None
        lib.utrans_transUChars.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32), ctypes.c_int32,
                                           ctypes.c_int32, ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int)]
        ident = _u16(_ICU_ID)
        status = ctypes.c_int(0)
        handle = lib.utrans_openU(ident, len(ident), 0, None, 0, None, ctypes.byref(status))  # 0＝UTRANS_FORWARD
        if status.value > 0 or not handle:
            _icu_failed = True
            return None
        _icu = (lib, handle)
    except (OSError, AttributeError):
        _icu_failed = True
    return _icu


def _icu_transliterate(text: str) -> str:
    with _icu_lock:
        opened = _open_icu()
        if opened is None:
            return text
        lib, handle = opened
        src = _u16(text)
        cap = len(src) * 8 + 32
        buf = (ctypes.c_uint16 * cap)()
        buf[: len(src)] = list(src)
        length, limit, status = ctypes.c_int32(len(src)), ctypes.c_int32(len(src)), ctypes.c_int(0)
        lib.utrans_transUChars(handle, buf, ctypes.byref(length), cap, 0, ctypes.byref(limit), ctypes.byref(status))
        if status.value > 0:
            return text
        return bytes(buf)[: length.value * 2].decode("utf-16-le", "replace")


def _relax(syl: str) -> str:
    if syl.startswith(("zh", "ch", "sh")):
        syl = syl[0] + syl[2:]
    if syl.endswith(("ing", "eng")):
        syl = syl[:-1]
    return syl


@functools.lru_cache(maxsize=8192)
def _han_syllable(ch: str) -> str:
    """單一漢字的放寬音節。每個字只問 ICU 一次（和整段轉換的結果相同，實測「周杰倫」「快樂」逐字與整段一致）"""
    out = re.sub(r"[^0-9a-z]", "", _icu_transliterate(ch).lower())
    return _relax(out) if out and not _HAN_RE.search(out) else ch  # ICU 沒有讀音的字（罕用字）保留原字，仍可字面比對


def _word_syllable(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


def pinyin_units(text: str) -> List[Tuple[str, str]]:
    """切成比對單位，每個單位是（字面, 音節）：一個漢字一個單位；連續的英數字（含數字）一個單位。
    字面是 NFKC＋小寫（全形英數會變半形）；標點與空白不成單位。音節：漢字是放寬後的無聲調拼音，英數字是去掉重音符號的小寫原文（不放寬）。"""
    out = []
    for m in _UNIT_RE.finditer(unicodedata.normalize("NFKC", text).lower()):
        lit = m.group()
        out.append((lit, _han_syllable(lit) if _HAN_RE.match(lit) else _word_syllable(lit)))
    return out


def pinyin_syllables(text: str) -> List[str]:
    """放寬後的音節串列。「周杰倫」「周杰論」「走杰倫」都是 ['zou','jie','lun']；"Shape of You" 是 ['shape','of','you']"""
    return [syl for _, syl in pinyin_units(text)]


def pinyin_key(text: str) -> str:
    """音節串起來（沒有空白與標點），給 terms.pinyin 欄位與「整段拼音完全相等」的比對用。「稻香」「到香」都是 'daoxiang'"""
    return "".join(pinyin_syllables(text))


def warm() -> None:
    """啟動時在背景呼叫一次：第一次開 ICU 轉換器約 42 ms（P3a 實測），先付掉，點歌時就不會卡"""
    pinyin_key("周杰倫")
