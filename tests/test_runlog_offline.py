"""執行期紀錄（logs/runtime/*.jsonl）、bench 檔名／list、log_view 檢視：欄位、路徑與對應規則；不污染專案 logs/。"""
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


# ---------- scripts/log_view.py ----------
sys.path.insert(0, str(config.ROOT / "scripts"))
import log_view  # noqa: E402


def _w(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _runtime(tmp_path):
    d = tmp_path / "runtime"
    d.mkdir()
    run = "20261109-221129"
    _w(d / "commands.jsonl", [
        {"ts_iso": "2026-11-09T10:00:01+08:00", "run_id": "20261109-100000", "command": "舊指令", "ok": True, "total_ms": 5.0,
         "steps": [{"kind": "fast", "ms": 5.0, "logs": ["✅ 舊"]}]},
        {"ts_iso": "2026-11-09T22:11:36+08:00", "run_id": run, "utt_id": 1, "queue_ms": 2.0, "command": "打開小算盤", "ok": True,
         "total_ms": 90.0, "steps": [{"kind": "fast", "ms": 90.2, "logs": ["✅ 已開啟應用程式：Calculator"]}]},
        {"ts_iso": "2026-11-09T22:11:50+08:00", "run_id": run, "utt_id": 2, "queue_ms": 5.0, "command": "你覺得 AI 會取代工程師嗎", "ok": True,
         "total_ms": 3991.0, "steps": [{"kind": "slow", "ms": 3990.0, "llm_ms": 2382.0, "logs": ["🤖 AI 回覆：不會"]}]},
        {"ts_iso": "2026-11-09T22:11:55+08:00", "run_id": run, "command": "UI 打的字", "ok": False, "error": "RuntimeError: 壞掉了"}])
    _w(d / "latency.jsonl", [
        {"ts_iso": "2026-11-09T22:11:33+08:00", "run_id": run, "utt_id": 1, "final_text": "打開小算盤", "reason": "complete",
         "e2e_ms": 700.0, "endpoint_ms": 640.0, "finalize_ms": 70.2},
        {"ts_iso": "2026-11-09T22:11:47+08:00", "run_id": run, "utt_id": 2, "final_text": "你覺得AI會取代工程師嗎", "reason": "default",
         "e2e_ms": 1400.0, "endpoint_ms": 1330.0, "finalize_ms": 70.0},
        {"ts_iso": "2026-11-09T22:12:00+08:00", "run_id": run, "utt_id": 3, "final_text": "雜訊", "reason": "server_vad",
         "e2e_ms": None, "endpoint_ms": None, "finalize_ms": None}])
    _w(d / "ui.jsonl", [{"ts_iso": "2026-11-09T22:11:34+08:00", "run_id": run, "conn": 1, "id": i, "type": "partial",
                         "bus_ms": 1.0, "apply_ms": 10.0 + i} for i in range(5)])
    return d


def test_log_view_defaults_to_latest_run_and_joins_latency_with_command_by_utt_id(tmp_path, capsys):
    assert log_view.main(["--dir", str(_runtime(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "啟動 20261109-221129" in out and "舊指令" not in out
    one = next(l for l in out.splitlines() if "打開小算盤" in l and "complete" in l)  # 同一行有收音計時與指令執行
    assert "22:11:33" in one and "700ms" in one and "2ms" in one and "90ms" in one and "✅" in one
    assert "792ms" in one and "已開啟應用程式：Calculator" in one  # 說完→執行完 ＝ 700 + 2 + 90
    two = next(l for l in out.splitlines() if "你覺得AI" in l)
    assert "slow 3990ms（LLM 2382ms）" in two and "5396ms" in two  # 1400 + 5 + 3991
    noise = next(l for l in out.splitlines() if "雜訊" in l)
    assert "沒有指令" in noise and "—" in noise
    assert "其他指令" in out and "UI 打的字" in out and "❌" in out and "RuntimeError: 壞掉了" in out and "（執行出錯）" in out
    assert "收尾判斷：complete＝" in out and "同一句用 utt_id 對起來" in out
    assert "partial  5" in out and "依訊息種類彙總）] （5 筆）" in out  # ui 依訊息種類彙總，筆數是原始行數


def test_log_view_does_not_join_across_runs_even_if_utt_id_repeats(tmp_path, capsys):
    d = tmp_path / "runtime"
    d.mkdir()
    _w(d / "latency.jsonl", [{"ts_iso": "2026-11-09T22:00:00+08:00", "run_id": "B", "utt_id": 1, "final_text": "新", "reason": "complete", "e2e_ms": 1.0}])
    _w(d / "commands.jsonl", [{"ts_iso": "2026-11-09T21:00:00+08:00", "run_id": "A", "utt_id": 1, "command": "舊", "ok": True, "total_ms": 1.0,
                               "steps": []}])
    log_view.main(["--dir", str(d), "--all"])
    out = capsys.readouterr().out
    assert any("沒有指令" in l and "新" in l for l in out.splitlines()) and "其他指令" in out and "舊" in out


def test_log_view_all_and_specific_run_and_unknown_run(tmp_path, capsys):
    d = str(_runtime(tmp_path))
    assert log_view.main(["--dir", d, "--all"]) == 0 and "舊指令" in capsys.readouterr().out
    assert log_view.main(["--dir", d, "--run", "20261109-100000"]) == 0
    out = capsys.readouterr().out
    assert "舊指令" in out and "打開小算盤" not in out and "complete" not in out
    assert log_view.main(["--dir", d, "--run", "nope"]) == 1 and "找不到 run_id" in capsys.readouterr().out


def test_log_view_limits_rows_and_handles_empty_dir(tmp_path, capsys):
    d = _runtime(tmp_path)
    log_view.main(["--dir", str(d), "-n", "1"])
    assert "共 3 筆，只列最後 1 列" in capsys.readouterr().out  # 3 句語音
    assert log_view.main(["--dir", str(tmp_path / "empty")]) == 1
