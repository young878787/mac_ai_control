"""分流語料（tests/route_corpus.py）本身的有效性：每句都真的會走到分流（Fast Path 未命中、不被 G1 擋下），句數與比例符合計畫 §9.2"""
import re
from collections import Counter

import pytest

from mac_ai.router.fast_path import FastPathMatcher
from mac_ai.router.normalize import normalize_utterance
from mac_ai.router.slow_path import intent_guard
from route_corpus import CORPUS, LABEL, MIN_COUNT


@pytest.mark.parametrize("text", [row[0] for row in CORPUS])
def test_sentence_reaches_router(text):
    m = FastPathMatcher()
    normalized = normalize_utterance(text)
    hits = [c for c in m.split_clauses(normalized) if m.match(c)]
    assert hits == [], f"{text!r} 有子句命中 Fast Path：{hits}（不會整句交給分流）"
    assert intent_guard(normalized) != "no_action", f"{text!r} 被 G1 擋下，到不了分流"


def test_no_duplicates():
    dup = [t for t, n in Counter(row[0] for row in CORPUS).items() if n > 1]
    assert dup == []


def test_counts_and_labels():
    counts = Counter(cat for _, _, cat, _ in CORPUS)
    for cat, n in MIN_COUNT.items():
        assert counts[cat] >= n, f"{cat} 只有 {counts[cat]} 句（至少 {n}）"
    assert all(label == LABEL[cat] for _, label, cat, _ in CORPUS)


def test_mixed_language_and_typo_ratio():
    en = sum(bool(re.search(r"[A-Za-z]", text)) for text, *_ in CORPUS)
    typo = sum(row[3] for row in CORPUS)
    assert en / len(CORPUS) >= 0.30, f"中英夾雜 {en}/{len(CORPUS)}"
    assert typo / len(CORPUS) >= 0.10, f"含錯字 {typo}/{len(CORPUS)}"
