"""自動驗收的量化與監控（計劃書 §14）：跑測試、整理成數字存檔，和上一次比較有沒有變差。

    python scripts/acceptance.py run [--app] [--live] [--label 名稱]   跑離線測試（加 --app 真實 App、--live 需 LLM），結果寫到 logs/bench/acceptance_*.json
    python scripts/acceptance.py report [舊.json 新.json]              比較兩次（省略＝最近兩次）；變差時結束碼 1
    python scripts/acceptance.py list                                  列出歷次結果

記錄的數字：各組與各測試檔的通過／失敗／略過／xfail 與耗時、每個測試的結果、錯字各類別的矯正率、
路由（正規化＋切句＋Fast Path 比對，不執行）的耗時分布、真實 App 每句指令的整步耗時與讀前景／網址的耗時。
變差的定義（D9）：新增任何失敗、任一類矯正率下降、p50 延遲增加超過 20% 且超過 20 ms。"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from mac_ai import config  # noqa: E402

SUITES = {  # 名稱 → pytest 參數
    "offline": [],
    "app": ["-m", "app"],
    "live": ["-m", "live"],
}
LATENCY_RATIO, LATENCY_MIN_MS = 1.20, 20.0
_FAILED = ("failed", "error")


# ---------- 解析 ----------
def parse_junit(path: Path) -> Dict[str, dict]:
    """pytest 的 junit xml → {nodeid: {"outcome", "time", "file"}}；outcome：passed／failed／error／skipped／xfailed"""
    tests: Dict[str, dict] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        cls, name = case.get("classname", ""), case.get("name", "")
        file = cls.replace(".", "/") + ".py" if cls else ""
        outcome = "passed"
        for child in case:
            if child.tag == "failure":
                outcome = "failed"
            elif child.tag == "error":
                outcome = "error"
            elif child.tag == "skipped":
                outcome = "xfailed" if child.get("type") == "pytest.xfail" else "skipped"
        nodeid = f"{file}::{name}"
        if outcome == "error" and nodeid in tests and tests[nodeid]["outcome"] != "error":
            outcome = "error"  # 收尾錯誤：同一個測試再出現一次，以錯誤為準
        tests[nodeid] = {"outcome": outcome, "time": float(case.get("time") or 0), "file": file}
    return tests


def summarize(tests: Dict[str, dict]) -> dict:
    total = {k: 0 for k in ("passed", "failed", "error", "skipped", "xfailed")}
    files: Dict[str, dict] = {}
    for t in tests.values():
        total[t["outcome"]] += 1
        f = files.setdefault(t["file"], {**{k: 0 for k in total}, "time_s": 0.0})
        f[t["outcome"]] += 1
        f["time_s"] = round(f["time_s"] + t["time"], 3)
    return {"total": total, "files": files}


def typo_rates(tests: Dict[str, dict]) -> Dict[str, dict]:
    """test_typo_offline 的參數 id 前綴是類別代號 → {類別: {"ok", "n", "rate"}}"""
    from test_typo_offline import CATEGORIES
    rates: Dict[str, dict] = {}
    for nodeid, t in tests.items():
        if "test_typo_is_corrected_by_backend[" not in nodeid:
            continue
        slug = nodeid.rsplit("[", 1)[1].rstrip("]").rsplit("-", 1)[0]
        r = rates.setdefault(CATEGORIES.get(slug, slug), {"ok": 0, "n": 0})
        r["n"] += 1
        r["ok"] += t["outcome"] == "passed"
    for r in rates.values():
        r["rate"] = round(r["ok"] / r["n"], 4)
    return rates


def pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(q * (len(v) - 1))))], 2)


def dist(values: List[float]) -> dict:
    return {"n": len(values), "p50": pct(values, 0.5), "p95": pct(values, 0.95), "max": round(max(values), 2) if values else None}


def routing_latency(reps: int = 20) -> dict:
    """路由耗時（不執行任何動作）：錯字語料每句 正規化＋切句＋Fast Path 比對，取每句中位數再算分布"""
    from test_typo_offline import CASES

    from mac_ai.router import catalog
    from mac_ai.router.fast_path import FastPathMatcher
    from mac_ai.router.normalize import normalize_utterance
    catalog.DEFAULT.installed()  # 已安裝 App 清單先掃好（正式流程在啟動時掃）
    m = FastPathMatcher()
    per: List[float] = []
    for _, text, _, _ in CASES:
        samples = []
        for _ in range(reps):
            t = time.perf_counter()
            for clause in m.split_clauses(normalize_utterance(text)):
                m.match(clause)
            samples.append((time.perf_counter() - t) * 1000)
        per.append(statistics.median(samples))
    return dist(per)


def app_metrics(path: Path) -> dict:
    """真實 App 每句指令的紀錄 → 依動作類型分的整步耗時、讀前景／網址耗時、每句結果"""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    by_kind: Dict[str, List[float]] = {}
    front_ms: List[float] = []
    host_ms: List[float] = []
    commands = []
    for row in rows:
        for s in row["steps"]:
            ctx = s.get("context") or {}
            kind = "site_search" if ctx.get("site") or "搜尋" in s["log"] else ("front_action" if ctx else "other")
            by_kind.setdefault(kind, []).append(s["ms"])
            if "ms" in ctx:
                front_ms.append(ctx["ms"])
            if "host_ms" in ctx:
                host_ms.append(ctx["host_ms"])
        commands.append({"test": row["test"], "text": row["text"], "total_ms": round(row["total_ms"], 1),
                         "status": [s["status"] for s in row["steps"]]})
    return {"step_ms": {k: dist(v) for k, v in by_kind.items()}, "front_read_ms": dist(front_ms),
            "host_read_ms": dist(host_ms), "commands": commands}


def git_rev() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        return rev + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ---------- run ----------
def run_suite(name: str, work: Path) -> dict:
    xml = work / f"{name}.xml"
    env = dict(os.environ)
    metrics = work / "app_metrics.jsonl"
    if name == "app":
        env["ACCEPTANCE_METRICS"] = str(metrics)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}", *SUITES[name]]
    print(f"▶ {name}：{' '.join(cmd[3:])}")
    t = time.perf_counter()
    p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    elapsed = time.perf_counter() - t
    print("   " + (p.stdout.strip().splitlines() or ["（沒有輸出）"])[-1])
    if not xml.exists():
        return {"exit_code": p.returncode, "wall_s": round(elapsed, 1), "error": (p.stderr or p.stdout)[-500:], "tests": {}}
    tests = parse_junit(xml)
    out = {"exit_code": p.returncode, "wall_s": round(elapsed, 1), **summarize(tests), "tests": {k: v["outcome"] for k, v in tests.items()}}
    if name == "offline":
        out["typo"] = typo_rates(tests)
    if name == "app":
        out["app"] = app_metrics(metrics)
    return out


def cmd_run(args) -> int:
    names = ["offline"] + (["app"] if args.app else []) + (["live"] if args.live else [])
    started = datetime.now()
    result = {"meta": {"started_at": started.isoformat(timespec="seconds"), "git": git_rev(), "label": args.label,
                       "suites": names, "python": sys.version.split()[0]}, "suites": {}}
    with tempfile.TemporaryDirectory(prefix="acceptance_") as tmp:
        for name in names:
            result["suites"][name] = run_suite(name, Path(tmp))
    print("▶ 路由耗時（正規化＋切句＋比對）")
    result["routing_ms"] = routing_latency()
    config.BENCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = config.BENCH_LOG_DIR / f"acceptance_{started:%Y-%m-%d_%H%M%S}{'_' + args.label if args.label else ''}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print_summary(result)
    print(f"\n已寫入 {out.relative_to(ROOT)}")
    return 0 if all(s.get("exit_code") == 0 for s in result["suites"].values()) else 1


def print_summary(result: dict) -> None:
    print(f"\n=== 驗收摘要（{result['meta']['started_at']}，{result['meta']['git']}）===")
    for name, s in result["suites"].items():
        t = s.get("total", {})
        print(f"{name:8} 通過 {t.get('passed', 0):5}｜失敗 {t.get('failed', 0) + t.get('error', 0):3}｜略過 {t.get('skipped', 0):3}"
              f"｜xfail {t.get('xfailed', 0):3}｜{s.get('wall_s')} s")
        for cat, r in (s.get("typo") or {}).items():
            print(f"   錯字 {cat:6} {r['ok']}/{r['n']}")
        app = s.get("app")
        if app:
            for kind, d in app["step_ms"].items():
                print(f"   App {kind:12} 整步 p50 {d['p50']} ms、p95 {d['p95']} ms（n={d['n']}）")
            print(f"   讀前景 p50 {app['front_read_ms']['p50']} ms；讀網址 p50 {app['host_read_ms']['p50']} ms")
    r = result.get("routing_ms") or {}
    print(f"路由耗時 p50 {r.get('p50')} ms、p95 {r.get('p95')} ms（{r.get('n')} 句）")


# ---------- report ----------
def latency_points(result: dict) -> Dict[str, Optional[float]]:
    points = {"路由 p50": (result.get("routing_ms") or {}).get("p50")}
    app = (result["suites"].get("app") or {}).get("app")
    if app:
        for kind, d in app["step_ms"].items():
            points[f"App {kind} p50"] = d["p50"]
        points["讀前景 p50"] = app["front_read_ms"]["p50"]
        points["讀網址 p50"] = app["host_read_ms"]["p50"]
    return points


def compare(old: dict, new: dict) -> List[str]:
    """回傳變差的項目（空＝沒有變差）。只比兩次都有跑的測試組"""
    problems: List[str] = []
    for name in sorted(set(old["suites"]) & set(new["suites"])):
        o, n = old["suites"][name].get("tests", {}), new["suites"][name].get("tests", {})
        for nodeid, outcome in sorted(n.items()):
            if outcome in _FAILED and o.get(nodeid) not in _FAILED:
                problems.append(f"新增失敗［{name}］{nodeid}（上次：{o.get(nodeid, '沒有這個測試')}）")
        if new["suites"][name].get("exit_code") not in (0, None) and not any(v in _FAILED for v in n.values()):
            problems.append(f"［{name}］pytest 結束碼 {new['suites'][name]['exit_code']}（收集錯誤或中斷）")
        for cat, r in (new["suites"][name].get("typo") or {}).items():
            before = (old["suites"][name].get("typo") or {}).get(cat)
            if before is not None and r["rate"] < before["rate"]:
                problems.append(f"錯字矯正率下降［{cat}］{before['ok']}/{before['n']} → {r['ok']}/{r['n']}")
    lo, ln = latency_points(old), latency_points(new)
    for key, v in ln.items():
        b = lo.get(key)
        if v is not None and b is not None and v > b * LATENCY_RATIO and v - b > LATENCY_MIN_MS:
            problems.append(f"延遲變高［{key}］{b} → {v} ms（+{(v / b - 1) * 100:.0f}%）")
    return problems


def bench_files() -> List[Path]:
    return sorted(config.BENCH_LOG_DIR.glob("acceptance_*.json"))


def cmd_report(args) -> int:
    files = [Path(f) for f in args.files] if args.files else bench_files()[-2:]
    if len(files) != 2:
        sys.exit("需要兩份結果（省略參數時取 logs/bench/ 最近兩次 acceptance_*.json）")
    old, new = (json.loads(f.read_text(encoding="utf-8")) for f in files)
    print(f"舊：{files[0].name}（{old['meta']['git']}）\n新：{files[1].name}（{new['meta']['git']}）")
    for key, v in latency_points(new).items():
        print(f"   {key:16} {latency_points(old).get(key)} → {v} ms")
    problems = compare(old, new)
    if problems:
        print(f"\n❌ 變差 {len(problems)} 項：")
        for p in problems:
            print(f"   - {p}")
        return 1
    print("\n✅ 沒有變差")
    return 0


def cmd_list(args) -> int:
    for f in bench_files():
        d = json.loads(f.read_text(encoding="utf-8"))
        parts = []
        for name, s in d["suites"].items():
            t = s.get("total", {})
            parts.append(f"{name} {t.get('passed', 0)}✓/{t.get('failed', 0) + t.get('error', 0)}✗/{t.get('xfailed', 0)}x")
        print(f"{f.name:48} {d['meta']['git']:16} {'｜'.join(parts)}｜路由 p50 {(d.get('routing_ms') or {}).get('p50')} ms")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--app", action="store_true", help="加跑真實 App 測試（會佔用螢幕約 2 分鐘）")
    r.add_argument("--live", action="store_true", help="加跑 live 測試（需 mlx_vlm.server）")
    r.add_argument("--label", help="標籤，放進檔名與 meta")
    p = sub.add_parser("report")
    p.add_argument("files", nargs="*", help="舊.json 新.json（省略＝最近兩次）")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "report": cmd_report, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
