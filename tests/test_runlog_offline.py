"""執行期紀錄（logs/runtime/*.jsonl）的路徑、欄位與數值格式；測試不污染專案 logs/。"""
import json
from pathlib import Path

from mac_ai import config
from mac_ai.stt.latency_log import RUN_ID, JsonlLog


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

# ---------- 寫入時四捨五入 ----------
def test_jsonl_log_rounds_floats_so_records_are_readable(tmp_path):
    log = JsonlLog(tmp_path / "r.jsonl")
    log.write({"e2e_ms": 710.8269160380587, "speech_start": 719137.961680083, "server": {"finish_ms": 68.23456}, "stages": [1.23456]})
    row = lines(tmp_path / "r.jsonl")[0]
    assert row["e2e_ms"] == 710.8 and row["speech_start"] == 719137.962 and row["server"] == {"finish_ms": 68.2}
