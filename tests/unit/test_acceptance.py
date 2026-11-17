"""scripts/acceptance.py 的 junit 解析、彙整與前後比較規則"""
import copy
import importlib.util

import pytest

from mac_ai import config

_spec = importlib.util.spec_from_file_location("acceptance", config.ROOT / "scripts" / "acceptance.py")
acceptance = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(acceptance)

JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">
<testcase classname="tests.test_a" name="test_ok" time="0.10"/>
<testcase classname="tests.test_a" name="test_bad" time="0.20"><failure message="boom"/></testcase>
<testcase classname="tests.test_b" name="test_skip" time="0.00"><skipped type="pytest.skip" message="no chrome"/></testcase>
<testcase classname="tests.test_b" name="test_known" time="0.30"><skipped type="pytest.xfail" message="known"/></testcase>
<testcase classname="tests.test_b" name="test_teardown" time="0.40"/>
<testcase classname="tests.test_b" name="test_teardown" time="0.00"><error message="teardown"/></testcase>
<testcase classname="tests.flow.test_typo" name="test_typo_is_corrected_by_backend[s2t-01]" time="0.01"/>
<testcase classname="tests.flow.test_typo" name="test_typo_is_corrected_by_backend[s2t-02]" time="0.01"><failure message="x"/></testcase>
<testcase classname="tests.flow.test_typo" name="test_typo_is_corrected_by_backend[homophone-01]" time="0.01"/>
</testsuite></testsuites>"""


@pytest.fixture
def junit(tmp_path):
    p = tmp_path / "r.xml"
    p.write_text(JUNIT, encoding="utf-8")
    return acceptance.parse_junit(p)


def test_parse_junit_outcomes(junit):
    got = {k: v["outcome"] for k, v in junit.items()}
    assert got["tests/test_a.py::test_ok"] == "passed" and got["tests/test_a.py::test_bad"] == "failed"
    assert got["tests/test_b.py::test_skip"] == "skipped" and got["tests/test_b.py::test_known"] == "xfailed"
    assert got["tests/test_b.py::test_teardown"] == "error"  # 收尾錯誤以錯誤計


def test_summarize_and_typo_rates(junit):
    s = acceptance.summarize(junit)
    assert s["total"] == {"passed": 3, "failed": 2, "error": 1, "skipped": 1, "xfailed": 1}
    assert s["files"]["tests/test_a.py"]["passed"] == 1 and s["files"]["tests/test_a.py"]["time_s"] == pytest.approx(0.3)
    assert acceptance.typo_rates(junit) == {"簡體": {"ok": 1, "n": 2, "rate": 0.5}, "同音字": {"ok": 1, "n": 1, "rate": 1.0}}


def test_pct_and_dist():
    assert acceptance.dist([]) == {"n": 0, "p50": None, "p95": None, "max": None}
    d = acceptance.dist([float(i) for i in range(1, 101)])
    assert d["n"] == 100 and d["p50"] in (50.0, 51.0) and d["p95"] in (95.0, 96.0) and d["max"] == 100.0


BASE = {
    "meta": {"git": "abc"},
    "suites": {
        "offline": {"exit_code": 0, "tests": {"t::a": "passed", "t::b": "xfailed"}, "typo": {"同音字": {"ok": 17, "n": 17, "rate": 1.0}}},
        "app": {"exit_code": 0, "tests": {"t::app": "passed"}, "app": {
            "step_ms": {"front_action": {"p50": 260.0}}, "front_read_ms": {"p50": 12.0}, "host_read_ms": {"p50": 180.0}}},
    },
    "routing_ms": {"p50": 1.2},
}


def test_identical_runs_have_no_regression():
    assert acceptance.compare(BASE, copy.deepcopy(BASE)) == []


def test_new_failure_and_rate_drop_and_latency_are_reported():
    new = copy.deepcopy(BASE)
    new["suites"]["offline"]["tests"]["t::a"] = "failed"
    new["suites"]["offline"]["tests"]["t::new"] = "error"
    new["suites"]["offline"]["typo"]["同音字"] = {"ok": 16, "n": 17, "rate": 0.9412}
    new["suites"]["app"]["app"]["step_ms"]["front_action"]["p50"] = 400.0   # +54%、+140 ms
    new["suites"]["app"]["app"]["host_read_ms"]["p50"] = 200.0              # +11%：不算
    probs = acceptance.compare(BASE, new)
    assert any("新增失敗［offline］t::a" in p for p in probs) and any("t::new" in p for p in probs)
    assert any("錯字矯正率下降［同音字］17/17 → 16/17" in p for p in probs)
    assert any("App front_action p50" in p for p in probs) and not any("讀網址" in p for p in probs)
    assert len(probs) == 4


@pytest.mark.parametrize("old,new,bad", [
    (1.2, 15.0, False),     # +1150% 但只多 13.8 ms：不算（絕對值門檻）
    (200.0, 235.0, False),  # +17.5%：不算（比例門檻）
    (200.0, 241.0, True),   # +20.5% 且 +41 ms
])
def test_latency_rule_needs_both_ratio_and_absolute(old, new, bad):
    a, b = copy.deepcopy(BASE), copy.deepcopy(BASE)
    a["routing_ms"]["p50"], b["routing_ms"]["p50"] = old, new
    assert bool(acceptance.compare(a, b)) is bad


def test_still_failing_test_is_not_a_new_failure_and_missing_suite_is_ignored():
    old, new = copy.deepcopy(BASE), copy.deepcopy(BASE)
    old["suites"]["offline"]["tests"]["t::a"] = new["suites"]["offline"]["tests"]["t::a"] = "failed"
    del new["suites"]["app"]  # 這次沒跑 --app：不比
    assert acceptance.compare(old, new) == []


def test_report_exit_code(tmp_path, capsys):
    import json
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(BASE), encoding="utf-8")
    worse = copy.deepcopy(BASE)
    worse["suites"]["offline"]["tests"]["t::a"] = "failed"
    b.write_text(json.dumps(worse), encoding="utf-8")
    assert acceptance.main(["report", str(a), str(a)]) == 0
    assert acceptance.main(["report", str(a), str(b)]) == 1
    assert "新增失敗" in capsys.readouterr().out


# ---------- 雲端（--cloud）----------
CLOUD_ROWS = [
    {"kind": "plan", "cat": "ctrl", "text": "a", "ok": True, "total_ms": 800.0, "status": "ok", "prompt_tokens": 1500, "completion_tokens": 40},
    {"kind": "plan", "cat": "ctrl", "text": "b", "ok": False, "total_ms": 900.0, "status": "ok", "prompt_tokens": 1500, "completion_tokens": 60},
    {"kind": "plan", "cat": "chat", "text": "c", "ok": True, "total_ms": 1000.0, "status": "ok", "prompt_tokens": 1500, "completion_tokens": 80},
    {"kind": "plan", "cat": "chat", "text": "d", "ok": True, "total_ms": 1100.0, "status": "timeout"},
    {"kind": "pronoun", "n": 30, "right": 30, "wrong": 0},
    {"kind": "vision", "total_ms": 1600.0, "status": "ok"},
    {"kind": "vision_error", "status": "timeout", "total_ms": 15000.0},
    {"kind": "misfire", "n": 7, "fired": 1},
    {"kind": "reply", "cat": "chat", "text": "e", "chars": 40, "sentences": 2, "newline": False, "emoji": 0, "simplified": False},
    {"kind": "reply", "cat": "chat", "text": "f", "chars": 80, "sentences": 4, "newline": True, "emoji": 1, "simplified": False},
]


def _cloud_result(tmp_path, rows=CLOUD_ROWS):
    import json
    f = tmp_path / "cloud.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return {"meta": {"git": "abc"}, "suites": {"cloud": {"exit_code": 0, "tests": {"t::c": "passed"}, "cloud": acceptance.cloud_metrics(f)}}}


def test_cloud_metrics_parsing(tmp_path):
    m = _cloud_result(tmp_path)["suites"]["cloud"]["cloud"]
    assert m["plan"] == {"ctrl": {"ok": 1, "n": 2, "rate": 0.5}, "chat": {"ok": 2, "n": 2, "rate": 1.0}}
    assert m["plan_ms"]["n"] == 4 and m["plan_ms"]["p50"] in (900.0, 1000.0) and m["errors"] == 1
    assert m["tokens"] == {"prompt": 4500, "completion": 180}
    assert m["pronoun"] == {"right": 30, "n": 30, "wrong": 0} and m["vision_ms"]["n"] == 1 and m["vision_errors"] == 1
    assert m["misfire_without_g1"] == {"fired": 1, "n": 7}
    assert acceptance.cloud_metrics(tmp_path / "missing.jsonl")["plan"] == {}  # 沒跑到雲端測試：不拋例外


def test_cloud_regressions_use_their_own_thresholds(tmp_path):
    old = _cloud_result(tmp_path)
    assert acceptance.compare(old, copy.deepcopy(old)) == []
    new = copy.deepcopy(old)
    c = new["suites"]["cloud"]["cloud"]
    c["plan"]["chat"] = {"ok": 1, "n": 2, "rate": 0.5}
    c["pronoun"] = {"right": 27, "n": 30, "wrong": 1}
    probs = acceptance.compare(old, new)
    assert any("雲端規劃正確率下降［chat］2/2 → 1/2" in p for p in probs)
    assert any("代名詞動作正確數下降 30 → 27" in p for p in probs) and any("錯的對象 1 次" in p for p in probs)


@pytest.mark.parametrize("old,new,bad", [
    (900.0, 1100.0, False),   # +22%：雲端門檻是 +30% 且 +300 ms
    (300.0, 450.0, False),    # +50% 但只多 150 ms：絕對值門檻（300 ms）不夠
    (900.0, 1150.0, False),   # +27.8%：比例不夠
    (2000.0, 2500.0, False),  # +25%：比例不夠
    (900.0, 1230.0, True),    # +36.7% 且 +330 ms
])
def test_cloud_latency_rule_is_looser_than_local(tmp_path, old, new, bad):
    a, b = _cloud_result(tmp_path), _cloud_result(tmp_path)
    a["suites"]["cloud"]["cloud"]["plan_ms"]["p50"], b["suites"]["cloud"]["cloud"]["plan_ms"]["p50"] = old, new
    assert bool(acceptance.compare(a, b)) is bad


def test_cloud_suite_missing_in_one_run_is_not_compared(tmp_path):
    old, new = _cloud_result(tmp_path), copy.deepcopy(BASE)
    assert acceptance.compare(old, new) == [] and acceptance.compare(new, old) == []


def test_cloud_reply_quality_metrics_and_regressions(tmp_path):
    old = _cloud_result(tmp_path)
    q = old["suites"]["cloud"]["cloud"]["reply"]
    assert q == {"n": 2, "over60": 1, "pct_over60": 0.5, "max_chars": 80, "over3": 1, "newline": 1, "simplified": 0}
    assert acceptance.cloud_metrics(tmp_path / "missing.jsonl")["reply"] is None
    assert acceptance.compare(old, copy.deepcopy(old)) == []
    worse = copy.deepcopy(old)
    worse["suites"]["cloud"]["cloud"]["reply"].update(simplified=2, pct_over60=0.61)
    probs = acceptance.compare(old, worse)
    assert any("殘留簡體 2 句" in p for p in probs) and any("超過 60 字的比例上升 50% → 61%" in p for p in probs)
    ok = copy.deepcopy(old)
    ok["suites"]["cloud"]["cloud"]["reply"]["pct_over60"] = 0.59   # 只上升 9 個百分點：不算
    assert acceptance.compare(old, ok) == []
