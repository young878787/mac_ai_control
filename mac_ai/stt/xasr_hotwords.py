"""X-ASR 的熱詞：把 stt_hints.txt 的詞切成模型的 token（純函式，不 import sherpa-onnx）。

sherpa-onnx 的 modeling_unit="cjkchar" 直接用 token 表查字，熱詞檔每行必須是切好的 token：
中文一個字一個 token（"▁字"），英文轉小寫後依 token 表由左到右取最長的符合段，每個詞的第一段加 "▁"。
這是近似做法，不是 SentencePiece 真正的切法；任何一段切不出來，整個詞略過（不報錯）。"""
import re
from pathlib import Path
from typing import Iterable, List, Set, Tuple

WORD_PREFIX = "▁"
# 拉丁字母／數字連續的一串算一個詞；其他字（中文等）一個字一個詞；空白、底線、標點都是分隔
_WORD = re.compile(r"[a-z0-9]+|[^\W_a-z0-9]")


def load_tokens(path: Path) -> Set[str]:
    """token 表每行是「token 編號」，取第一欄"""
    tokens = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            tokens.add(line.rsplit(" ", 1)[0])
    return tokens


def _split_word(word: str, tokens: Set[str]) -> List[str]:
    """由左到右取最長的符合段；中途有一段切不出來就回傳空 list"""
    pieces, i = [], 0
    while i < len(word):
        for j in range(len(word), i, -1):
            piece = (WORD_PREFIX if i == 0 else "") + word[i:j]
            if piece in tokens:
                pieces.append(piece)
                i = j
                break
        else:
            return []
    return pieces


def encode_hotwords(hints: Iterable[str], tokens: Set[str]) -> Tuple[List[str], List[str]]:
    """回傳 (熱詞檔的行, 略過的詞)：行依 hints 的順序、小寫後去重；空白行直接忽略，切不出來的詞放進略過"""
    lines, skipped, seen = [], [], set()
    for hint in hints:
        hint = hint.strip()
        if not hint:
            continue
        pieces = []
        for word in _WORD.findall(hint.lower()):
            word_pieces = _split_word(word, tokens)
            if not word_pieces:
                pieces = []
                break
            pieces.extend(word_pieces)
        if not pieces:
            skipped.append(hint)
            continue
        line = " ".join(pieces)
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines, skipped
