"""路由行為快照（查詢路由計畫 P0，docs/plans/active/2026-11-23-query-routing.md §7.1 A1）。

語料 tests/fixtures/routing/corpus.json 每句照 pipeline 的順序跑：正規化 → 切句 → 每個子句 match()；
未命中的子句另記 match(candidates=True)（LLM 不可用時的補救候選）；整句記 intent_guard 與 answer_mode。
結果和 snapshot.json 逐句比對。P1、P2 必須 0 差異；P3 起差異只能是使用者核准的句子。
重產快照（只在行為改變已經確認後）：ROUTING_SNAPSHOT_UPDATE=1 pytest tests/unit/test_routing_snapshot.py"""
import json
import os
from pathlib import Path

from mac_ai.router.fast_path import FastPathMatcher, answer_mode
from mac_ai.router.normalize import normalize_utterance
from mac_ai.router.slow_path import intent_guard

DIR = Path(__file__).resolve().parents[1] / "fixtures" / "routing"
CORPUS, SNAPSHOT = DIR / "corpus.json", DIR / "snapshot.json"


def _sentences():
    return [s for group in json.loads(CORPUS.read_text())["groups"].values() for s in group]


def _fast(hit):
    if hit is None:
        return None
    row = {"actions": [[name, args] for name, args in hit.actions], "confidence": hit.confidence}
    if hit.intent is not None:
        row["intent"] = hit.intent.to_log()
    return row


def _route(m: FastPathMatcher, text: str) -> dict:
    normalized = normalize_utterance(text)
    clauses = []
    for clause in m.split_clauses(normalized):
        hit = m.match(clause)
        row = {"clause": clause, "fast": _fast(hit)}
        if hit is None:
            row["candidate"] = _fast(m.match(clause, candidates=True))
        clauses.append(row)
    return {"normalized": normalized, "clauses": clauses, "guard": intent_guard(normalized),
            "answer_mode": answer_mode(normalized)}


def _dump(row: dict, projects: Path) -> str:
    """一句一行（git diff 好讀）；暫存專案路徑與家目錄換成固定代號，快照不帶這台電腦的路徑"""
    s = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    return s.replace(str(projects), "<projects>").replace(os.path.expanduser("~"), "~")


def test_corpus_has_no_duplicates():
    sentences = _sentences()
    assert len(sentences) == len(set(sentences))


def test_routing_snapshot(projects):
    m = FastPathMatcher()
    now = {text: _dump(_route(m, text), projects) for text in _sentences()}
    if os.environ.get("ROUTING_SNAPSHOT_UPDATE") == "1":
        body = ",\n".join(f"{json.dumps(k, ensure_ascii=False)}: {v}" for k, v in now.items())
        SNAPSHOT.write_text("{\n" + body + "\n}\n")
    old = {k: json.dumps(v, ensure_ascii=False, sort_keys=True) for k, v in json.loads(SNAPSHOT.read_text()).items()}
    missing = [t for t in now if t not in old]
    changed = [t for t in now if t in old and old[t] != now[t]]
    report = [f"「{t}」\n  舊：{old[t]}\n  新：{now[t]}" for t in changed[:20]]
    assert not missing, f"{len(missing)} 句不在快照裡（語料加了句子要重產快照）：{missing[:10]}"
    assert not changed, f"{len(changed)} 句路由改變（前 20 句）：\n" + "\n".join(report)
