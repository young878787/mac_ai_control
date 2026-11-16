"""簡體字轉繁體：macOS 內建 ICU（CoreFoundation 的 CFStringTransform，ctypes，不需額外套件）。

為什麼不整句轉換：ICU 的 Hans-Hant 會把繁體字也改掉（實測「退回路徑」→「退迴路徑」）。所以只替換兩類位置：
1. Big5（cp950）編不出來的 CJK 字——臺灣正體文字都在 Big5 裡，編不出來的幾乎一定是簡體字；
2. Big5 編得出來、但只有簡體才這樣寫的字（_SIMPLIFIED_IN_BIG5：离、体、气、网…；實測模型回覆出現過「远离」）。
有歧義的字（后、干、于、云、斗、几、采、范、征、沉…繁體也在用）一律不動。繁體字與英文、標點完全不動。
所有函式都不拋例外：載入失敗、轉換失敗、長度不符一律回傳原文。"""
import ctypes
import ctypes.util
from ctypes import c_bool, c_char_p, c_long, c_uint32, c_void_p
from typing import List, Optional

_UTF8 = 0x08000100
_lib: Optional[tuple] = None
_load_failed = False


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


# Big5 收錄、但只有簡體才這樣寫的字（逐字經 ICU 確認有唯一對應的繁體；tests/test_cloud_offline.py 驗證每個字都會被轉）
_SIMPLIFIED_IN_BIG5 = frozenset("万与么价优体儿党厂听圣坏宁机极气网种离适确胜赶触惊愿怀据构柜洁虫蚕蜡吨岭帘挂扰扑晒涂筑羡肮腊腌苹茧荐踊异")


def _simplified(ch: str) -> bool:
    if not _is_cjk(ch):
        return False
    if ch in _SIMPLIFIED_IN_BIG5:
        return True
    try:
        ch.encode("cp950")
        return False
    except UnicodeEncodeError:
        return True


def has_simplified(text: str) -> bool:
    """有沒有確定是簡體的字（Big5 編不出來的 CJK 字，或 _SIMPLIFIED_IN_BIG5 裡的字）"""
    return any(_simplified(ch) for ch in text)


def _load() -> Optional[tuple]:
    global _lib, _load_failed
    if _lib is None and not _load_failed:
        try:
            cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
            cf.CFStringCreateWithCString.restype, cf.CFStringCreateWithCString.argtypes = c_void_p, [c_void_p, c_char_p, c_uint32]
            cf.CFStringCreateMutableCopy.restype, cf.CFStringCreateMutableCopy.argtypes = c_void_p, [c_void_p, c_long, c_void_p]
            cf.CFStringTransform.restype, cf.CFStringTransform.argtypes = c_bool, [c_void_p, c_void_p, c_void_p, c_bool]
            cf.CFStringGetCString.restype, cf.CFStringGetCString.argtypes = c_bool, [c_void_p, c_char_p, c_long, c_uint32]
            cf.CFRelease.argtypes = [c_void_p]
            _lib = (cf, cf.CFStringCreateWithCString(None, b"Hans-Hant", _UTF8))
        except (OSError, AttributeError, TypeError):
            _load_failed = True
    return _lib


def _icu(text: str) -> Optional[str]:
    lib = _load()
    if lib is None:
        return None
    cf, rule = lib
    src = cf.CFStringCreateWithCString(None, text.encode("utf-8"), _UTF8)
    if not src:
        return None
    mut = cf.CFStringCreateMutableCopy(None, 0, src)
    try:
        if not mut or not cf.CFStringTransform(mut, None, rule, False):
            return None
        buf = ctypes.create_string_buffer(len(text.encode("utf-8")) * 2 + 16)
        return buf.value.decode("utf-8") if cf.CFStringGetCString(mut, buf, len(buf), _UTF8) else None
    finally:
        cf.CFRelease(src)
        if mut:
            cf.CFRelease(mut)


def hans_to_hant(text: str) -> str:
    """把 text 裡確定是簡體的字換成繁體；其他字原樣保留。沒有簡體字時不呼叫 ICU（熱路徑成本為零）"""
    try:
        if not text or not has_simplified(text):
            return text
        converted = _icu(text)
        if converted is None or len(converted) != len(text):  # 長度不同＝ICU 改了斷詞／組合字，位置對不上：寧可不換
            return text
        return "".join(c if _simplified(o) else o for o, c in zip(text, converted))
    except (ValueError, UnicodeError, OSError):
        return text


def residual_simplified(text: str) -> List[str]:
    """（量測用）text 裡還殘留的簡體字：確定是簡體、而且 ICU 有繁體對應的字。ICU 沒有對應的字（『嘞』這類方言字）不算簡體字"""
    out = []
    for ch in sorted(set(text)):
        if _simplified(ch):
            conv = _icu(ch)
            if conv and conv != ch:
                out.append(ch)
    return out
