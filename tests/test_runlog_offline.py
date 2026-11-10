"""執行期紀錄與 bench：JsonlLog、commands.jsonl、stt_bench 檔名／生效參數／list；不污染專案 logs/。"""
import json
import queue
import sys
import threading
from pathlib import Path

from mac_ai import config
from mac_ai.assistant import command_record, command_worker
from mac_ai.stt.latency_log import RUN_ID, JsonlLog

sys.path.insert(0, str(config.ROOT / "scripts"))
import stt_bench  # noqa: E402


def lines(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines()]


# ---------- JsonlLog ----------
def test_jsonl_log_adds_time_and_run_id_and_keeps_record_fields(tmp_path):
    log = JsonlLog(tmp_path / "a" / "x.jsonl")
    log.write({"final_text": "打開小算盤", "e2e_ms": None})
    row = lines(tmp_path / "a" / "x.jsonl")[0]
    assert row["run_id"] == RUN_ID and "T" in row["ts_iso"] and row["final_text"] == "打開小算盤" and row["e2e_ms"] is None


def test_jsonl_log_default_path_follows_config_and_tests_do_not_touch_project_logs(tmp_path):
    JsonlLog().write({"k": 1})
    assert lines(config.LATENCY_LOG)[0]["k"] == 1
    assert config.LATENCY_LOG.is_relative_to(tmp_path) and config.COMMAND_LOG.is_relative_to(tmp_path)


def test_log_locations_are_split_by_kind():
    assert config.RUNTIME_LOG_DIR == config.LOG_DIR / "runtime" and config.SERVICE_LOG_DIR == config.LOG_DIR / "services"
    assert config.BENCH_LOG_DIR == config.LOG_DIR / "bench"
    assert config.UI_LOG.parent == config.RUNTIME_LOG_DIR and config.UI_LOG.name == "ui.jsonl"

# ---------- commands.jsonl ----------
def _run_worker(execute, item, choose=None):
    records, commands, stop = [], queue.Queue(), threading.Event()
    t = threading.Thread(target=command_worker, args=(commands, execute, stop, None, choose, records.append), daemon=True)
    t.start()
    commands.put(item)
    for _ in range(100):
        if records:
            break
        threading.Event().wait(0.02)
    stop.set()
    t.join(timeout=2)
    return records


def test_command_record_has_steps_intent_ms_and_hides_home_dir_and_choices():
    home = str(Path.home())
    outcome = {"stages": {"normalize_ms": 0.1}, "replies": ["好"], "total_ms": 9.0, "steps": [
        {"kind": "fast", "text": "找 報告", "ms": 8.0, "status": "ok", "intent": {"domain": "file", "verb": "find"},
         "match": {"confidence": 0.9, "note": "n"}, "logs": [f"✅ 已在 Finder 顯示 {home}/Documents/報告.pdf"],
         "choices": {"list_id": "x", "items": [f"{home}/a"]}, "find_ms": 5.0}]}
    rec = command_record({"command": "找報告", "ok": True, "total_ms": 9.0, "replies": ["好"], "steps": []}, outcome)
    st = rec["steps"][0]
    assert rec["command"] == "找報告" and rec["ok"] is True and rec["stages"] == {"normalize_ms": 0.1}
    assert st["intent"] == {"domain": "file", "verb": "find"} and st["ms"] == 8.0 and st["status"] == "ok"
    assert st["logs"] == ["✅ 已在 Finder 顯示 ~/Documents/報告.pdf"]
    assert "choices" not in st and "find_ms" not in st and home not in json.dumps(rec, ensure_ascii=False)


def test_worker_writes_one_command_record_per_command_in_order():
    outcome = {"steps": [{"kind": "fast", "text": "靜音", "ms": 1.0, "status": "ok", "logs": ["✅ 靜音"]}],
               "replies": [], "total_ms": 2.0, "stages": {}}
    records = _run_worker(lambda c: outcome, "靜音")
    assert [r["command"] for r in records] == ["靜音"] and records[0]["ok"] is True and records[0]["steps"][0]["status"] == "ok"


def test_worker_writes_failure_record_when_execute_raises(capsys):
    def boom(cmd):
        raise RuntimeError("壞掉了")

    records = _run_worker(boom, "x")
    assert records == [{"command": "x", "ok": False, "error": "RuntimeError: 壞掉了"}]


def test_worker_without_command_log_still_runs():
    done, commands, stop = [], queue.Queue(), threading.Event()
    t = threading.Thread(target=command_worker, args=(commands, done.append, stop), daemon=True)
    t.start()
    commands.put("a")
    for _ in range(100):
        if done:
            break
        threading.Event().wait(0.02)
    stop.set()
    t.join(timeout=2)
    assert done == ["a"]

# ---------- stt_bench 檔名／list ----------
BASE = {"corpus": "bench/synth", "mode": "client", "label": None, "only": None, "ep": [], "pre_delay": None, "send_s": None, "block_s": None}


def test_bench_name_encodes_what_was_run():
    assert stt_bench.bench_name("2026-11-09", BASE) == "2026-11-09_synth_client.jsonl"
    a = {**BASE, "label": "sweep-a", "only": "A,B,F", "ep": ["partial_stable_s=0.25"], "pre_delay": 0.1}
    assert stt_bench.bench_name("2026-11-04", a) == "2026-11-04_synth_client_sweep-a_only-A.B.F_partial_stable_s-0.25_pre_delay-0.1.jsonl"
    assert stt_bench.bench_name("d", {**BASE, "pre_delay": 0.0}).endswith("_pre_delay-0.jsonl")  # 0 也要寫出來


def test_unique_adds_suffix_instead_of_overwriting(tmp_path):
    p = tmp_path / "a.jsonl"
    assert stt_bench._unique(p) == p
    p.write_text("x")
    assert stt_bench._unique(p) == tmp_path / "a-2.jsonl"
    (tmp_path / "a-2.jsonl").write_text("x")
    assert stt_bench._unique(p) == tmp_path / "a-3.jsonl"


def test_effective_params_include_defaults_and_overrides():
    import argparse
    args = argparse.Namespace(mode="client", pre_delay=None, send_s=None, ep=["partial_stable_s=0.25"])
    eff = stt_bench.effective_params(args)
    assert eff["partial_stable_s"] == 0.25 and eff["silence_complete_s"] == config.EP_SILENCE_COMPLETE_S
    assert eff["pre_delay_s"] == config.EP_FLUSH_PRE_DELAY_S and eff["send_s"] == config.EP_SEND_S and "decide" not in eff
    srv = stt_bench.effective_params(argparse.Namespace(mode="server", pre_delay=None, send_s=None, ep=[]))
    assert srv["pre_delay_s"] == config.STT_FLUSH_PRE_DELAY_S and srv["send_s"] == 0.0


def _write_run(path, clips_ok, with_meta=True):
    rows = [{"type": "clip", "clip": f"A0{i}", "category": "A", "mode": "client", "ref_text": "打開小算盤",
             "expected_actions": [["control_mac_app", {"app_name": "Calculator", "action": "open"}]], "truth_speech_end_s": 2.0,
             "duration_s": 5.0, "finals": [{"t": 2.5, "text": "打開小算盤" if ok else "你好", "timing": None}], "partials": [],
             "errors": [], "records": []} for i, ok in enumerate(clips_ok)]
    if with_meta:
        rows.append({"type": "meta", "mode": "client", "corpus": "bench/synth", "args": dict(BASE)})
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_list_shows_one_row_per_run_with_key_numbers(tmp_path, capsys):
    _write_run(tmp_path / "2026-11-04_synth_client.jsonl", [True, True])
    _write_run(tmp_path / "2026-11-05_synth_client_broken.jsonl", [True, False], with_meta=False)
    stt_bench.cmd_list(__import__("argparse").Namespace(files=[str(p) for p in sorted(tmp_path.glob("*.jsonl"))]))
    out = capsys.readouterr().out.splitlines()
    assert "檔名" in out[0] and "A類E2E中位" in out[0]
    assert out[1].startswith("2026-11-04_synth_client.jsonl") and "500ms" in out[1] and "2/2" in out[1]
    assert "沒有 meta" in out[2] and "1/2" in out[2]

# ---------- 寫入時四捨五入 ----------
def test_jsonl_log_rounds_floats_so_records_are_readable(tmp_path):
    log = JsonlLog(tmp_path / "r.jsonl")
    log.write({"e2e_ms": 710.8269160380587, "speech_start": 719137.961680083, "server": {"finish_ms": 68.23456}, "stages": [1.23456]})
    row = lines(tmp_path / "r.jsonl")[0]
    assert row["e2e_ms"] == 710.8 and row["speech_start"] == 719137.962 and row["server"] == {"finish_ms": 68.2}
