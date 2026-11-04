"""實機測試：需 mlx_vlm.server 已載入 LFM3-VL-2B-MLX-4bit。跑法: pytest -m live -s"""
import json
import shutil
import statistics
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

import mac_ai.router as r
from mac_ai import config

pytestmark = pytest.mark.live
REPEAT = 3


def _health():
    try:
        with urllib.request.urlopen(f"{config.MLX_VLM_URL}/health", timeout=3) as resp:
            return json.load(resp)
    except OSError:
        return None


@pytest.fixture(scope="module", autouse=True)
def server():
    h = _health()
    if h is None:
        pytest.skip("mlx_vlm.server 未啟動")
    return h


def test_server_health(server):
    assert server["status"] == "healthy"
    assert server["loaded_tool_parser"] == "qwen3_coder"
    assert "LFM3-VL-2B-MLX-4bit" in server["loaded_model"]
    assert server["apc_enabled"] is True  # 啟動時需 APC_ENABLED=1，否則每次重算工具定義，延遲約為兩倍


def test_warmup_then_latency_budget():
    r.warmup_llm()
    t = time.perf_counter()
    r.plan_slow_path("把螢幕調暗一點")
    assert time.perf_counter() - t < 5.0


def _names(calls):
    return [n for n, _ in calls]


# (語句, 檢查函式)；語句先經 normalize_utterance，與正式流程一致
CASES = [
    ("把螢幕調暗一點", lambda c: c[0][0] == "adjust_screen_brightness" and c[0][1]["direction"] == "down" and 1 <= c[0][1]["steps"] <= 16 and len(c) == 1),
    ("把螢幕調到最亮", lambda c: c == [("adjust_screen_brightness", {"direction": "up", "steps": 16})]),
    ("打開瀏覽器查一下台北天氣", lambda c: _names(c) == ["web_search_or_open_url"] and "台北天氣" in c[0][1]["query"]),
    ("幫我鎖定螢幕", lambda c: c == [("system_power_action", {"action": "lock_screen"})]),
    ("開深色模式", lambda c: c == [("system_power_action", {"action": "dark_mode_on"})]),   # D3：打開 ≠ 切換
    ("把聲音關掉", lambda c: c == [("set_system_volume", {"mute": True})]),
    ("把音量調到五十", lambda c: c == [("set_system_volume", {"level": 50})]),
    ("打開科索", lambda c: c == [("control_mac_app", {"app_name": "Cursor", "action": "open"})]),
    ("開谷哥瀏覽器查台北天氣", lambda c: _names(c) == ["web_search_or_open_url"]),
    ("幫我開 VS Code 然後關掉 Spotify", lambda c: sorted((a["app_name"], a["action"]) for _, a in c) == [("Spotify", "quit"), ("Visual Studio Code", "open")]),
    ("打開終端機，順便把螢幕調暗一點，還有開瀏覽器查一下台北天氣",
     lambda c: _names(c).count("control_mac_app") == 1 and _names(c).count("adjust_screen_brightness") == 1 and _names(c).count("web_search_or_open_url") == 1 and len(c) == 3),
    ("今天心情不錯", lambda c: c == []),
]


@pytest.mark.parametrize("utterance,check", CASES, ids=[c[0] for c in CASES])
def test_tool_calling(utterance, check):
    text = r.normalize_utterance(utterance)
    latencies = []
    for _ in range(REPEAT):
        t = time.perf_counter()
        calls, reply, rejected = r.plan_slow_path(text)
        latencies.append(time.perf_counter() - t)
        assert rejected == [], f"出現被驗證層拒絕的呼叫：{rejected}"
        assert check(calls), f"{utterance!r} -> {calls} / {reply!r}"
    print(f"\n{utterance}: {[round(x, 2) for x in latencies]}")


@pytest.mark.parametrize("utterance,lo,hi", [("把音量調大一點", 50, 100), ("把音量調小一點", 0, 40)])
def test_relative_volume_guess(utterance, lo, hi):
    """『大/小一點』以猜測的絕對值表達（設計限制：不讀目前音量），只驗方向合理"""
    calls, _, rejected = r.plan_slow_path(utterance)
    assert rejected == [] and _names(calls) == ["set_system_volume"]
    assert lo <= calls[0][1]["level"] <= hi


@pytest.mark.parametrize("utterance,second_check", [
    ("打開系統設定，把亮度調暗", lambda c: "key code 145" in c[2]),
    ("開系統設定，鎖定螢幕", lambda c: "keystroke" in c[2]),
    ("打開系統設定，查一下台北天氣", lambda c: c[0] == "open" and "google.com/search" in c[1]),
])
def test_pipeline_open_app_then_other_action(sh, utterance, second_check):
    """真實流程：『開 App，其他動作』先被切句，各子句依序執行
    （亮度、鎖定、搜尋已納入 Fast Path；同一句整句直送 4bit 會漏掉後半，故不能繞過切句）"""
    rec = sh
    r.process_voice_command(utterance)
    assert rec[0] == ["open", "-a", "System Settings"]
    assert len(rec) == 2 and second_check(rec[1]), rec


VOL = lambda n: ["osascript", "-e", f"set volume output volume {n}"]
ORDER_CASES = [
    ("開 Slack，開 Notion，把音量調到20", "SF", [["open", "-a", "Slack"], ["open", "-a", "Notion"], VOL(20)]),
    ("把音量調到35，把音量調到五十，把音量調到60", "FFF", [VOL(35), VOL(50), VOL(60)]),  # 『五十』經正規化後走 Fast
    ("把音量調大一點，靜音，幫我找一下台北天氣", "SFS", None),  # 兩段 LLM 夾一個 Fast；逐項檢查見下
]


@pytest.mark.parametrize("utterance,shape,expected", ORDER_CASES, ids=[c[0] for c in ORDER_CASES])
def test_pipeline_spoken_order_and_time(sh, utterance, shape, expected):
    """依說話順序執行（Fast／Slow 交錯），並要求總時間在預算內（subprocess 為假，量到的是決策＋分派時間）"""
    rec = sh
    out = r.process_voice_command(utterance)
    assert "".join("F" if s["kind"] == "fast" else "S" for s in out["steps"]) == shape
    if expected is not None:
        assert rec == expected
    else:
        assert rec[0][2].startswith("set volume output volume") and rec[1] == ["osascript", "-e", "set volume output muted true"]
        assert rec[2][0] == "open" and "google.com/search" in rec[2][1]
    assert out["total_ms"] < 3000, f"{out['total_ms']:.0f}ms"


def test_latency_distribution():
    """單工具指令暖機後的延遲分布（n=10）"""
    r.warmup_llm()
    samples = []
    for _ in range(10):
        t = time.perf_counter()
        r.plan_slow_path("幫我鎖定螢幕")
        samples.append(time.perf_counter() - t)
    p50, mx = statistics.median(samples), max(samples)
    print(f"\n單工具 p50={p50:.2f}s max={mx:.2f}s")
    assert p50 < 1.5 and mx < 3.0


# ---------- C-N1：LLM 判斷新工具（每案 REPEAT 次全對、不得被驗證層拒絕）----------
NEW_TOOL_CASES = [
    ("打開那個語音助手的專案", lambda c: c == [("open_project", {"project_name": "mac_ai_control"})]),
    ("用 cursor 開 mac_ai_control 專案", lambda c: c == [("open_project", {"project_name": "mac_ai_control", "editor": "Cursor"})]),
    ("幫我看看畫面上寫什麼", lambda c: c == [("read_screen", {})]),
    ("螢幕上有什麼", lambda c: c == [("read_screen", {})]),
    ("Python 是什麼", lambda c: _names(c) in ([], ["chat_reply"])),   # 兩種都會觸發 chat call（D6）
    ("今天心情不錯", lambda c: _names(c) in ([], ["chat_reply"])),
    ("關掉深色模式", lambda c: c == [("system_power_action", {"action": "dark_mode_off"})]),
]


@pytest.mark.parametrize("utterance,check", NEW_TOOL_CASES, ids=[c[0] for c in NEW_TOOL_CASES])
def test_new_tools_selection(utterance, check):
    text = r.normalize_utterance(utterance)
    for _ in range(REPEAT):
        calls, reply, rejected = r.plan_slow_path(text)
        assert rejected == [], f"被驗證層拒絕：{rejected}"
        assert check(calls), f"{utterance!r} -> {calls} / {reply!r}"


@pytest.mark.parametrize("utterance", ["幫我開資料夾", "幫我開檔案資料夾", "幫我開檔案總管"])
def test_folder_request_never_opens_a_project(utterance):
    """實測：舊 schema 會把『開資料夾』誤判成開 VS Code 專案。被驗證層擋下（rejected）可以接受，但不得出現可執行的 open_project"""
    text = r.normalize_utterance(utterance)
    for _ in range(REPEAT):
        calls, reply, rejected = r.plan_slow_path(text)
        assert "open_project" not in _names(calls), f"{utterance!r} -> {calls}"


def test_mixed_action_and_question_real_llm(sh):
    """C-N2：開 Chrome（Fast）＋ 提問（LLM → chat call）。回覆非空、不含簡體詞表內的字"""
    out = r.process_voice_command("開 Chrome，順便告訴我 Python 是什麼")
    assert sh == [["open", "-a", "Google Chrome"]]
    assert [s["kind"] for s in out["steps"]] == ["fast", "slow"]
    assert out["steps"][1]["status"] == "ok", out["steps"][1]
    assert len(out["replies"]) == 1 and out["replies"][0].strip()
    assert r.to_traditional(out["replies"][0]) == out["replies"][0]
    print(f"\n回覆：{out['replies'][0]}")


# ---------- C-T2：階段計時表（不設門檻，只輸出）----------
STAGE_SCENARIOS = [
    ("全 Fast", "幫我打開小算盤，然後把音量調到 40"),
    ("單一 LLM 工具", "把音量調大一點"),
    ("F-S-F", "開 Chrome，把音量調大一點，靜音"),
    ("LLM 多工具", "開 Slack，開 Notion，把音量調到20"),
    ("提問（規劃＋chat call）", "Python 是什麼"),
]


def _med(xs):
    return statistics.median(xs) if xs else float("nan")


def test_stage_timing_report(sh):
    """每個情境跑 5 次，『首次』與『其餘中位』分列（subprocess 為假，量到的是決策＋分派＋LLM 時間）"""
    r.warmup_llm()
    rows = []
    for label, text in STAGE_SCENARIOS:
        runs = []
        for _ in range(5):
            out = r.process_voice_command(text)
            slow = [s for s in out["steps"] if s["kind"] == "slow"]
            runs.append({
                "normalize": out["stages"]["normalize_ms"], "split": out["stages"]["split_ms"], "match": out["stages"]["match_ms"],
                "fast_exec": sum(sum(s.get("exec_ms", [])) for s in out["steps"] if s["kind"] == "fast"),
                "llm": sum(s.get("llm_ms", 0) for s in slow),
                "prefill": sum((s.get("llm") or {}).get("prefill_ms") or 0 for s in slow),
                "decode": sum((s.get("llm") or {}).get("decode_ms") or 0 for s in slow),
                "chat": sum(s.get("chat_ms", 0) for s in slow),
                "total": out["total_ms"],
            })
        rows.append((label, runs))
    keys = ["normalize", "split", "match", "fast_exec", "llm", "prefill", "decode", "chat", "total"]
    print("\n| 情境 | 次 | " + " | ".join(keys) + " |\n|---|---|" + "---|" * len(keys))
    for label, runs in rows:
        print(f"| {label} | 首次 | " + " | ".join(f"{runs[0][k]:.1f}" for k in keys) + " |")
        print(f"| {label} | 其餘中位 | " + " | ".join(f"{_med([x[k] for x in runs[1:]]):.1f}" for k in keys) + " |")
    assert all(len(runs) == 5 for _, runs in rows)


# ---------- C-V1／C-V2：讀畫面（固定畫面；門檻待 D9，只記錄不判定）----------
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    """以 PIL 產生 3 張已知內容的 1280×800 畫面：中文新聞、天氣、程式碼"""
    pytest.importorskip("PIL.ImageDraw")
    from PIL import Image, ImageDraw, ImageFont
    if not Path(FONT).exists():
        pytest.skip("找不到中文字型")
    out = tmp_path_factory.mktemp("shots")

    def shot(name, bg, bar, title, lines):
        im = Image.new("RGB", (1280, 800), bg)
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 1280, 60], fill=bar)
        d.text((24, 14), title, font=ImageFont.truetype(FONT, 30), fill=(255, 255, 255))
        y = 100
        for size, text, color in lines:
            d.text((60, y), text, font=ImageFont.truetype(FONT, size), fill=color)
            y += int(size * 1.7)
        im.save(out / f"{name}.png")

    shot("news", (255, 255, 255), (30, 90, 160), "新聞網 - 今日焦點", [
        (44, "央行宣布升息一碼 房貸族每月多負擔數百元", (20, 20, 20)),
        (30, "台北訊 中央銀行今天宣布調升政策利率0.125個百分點，", (60, 60, 60)),
        (30, "為近兩年來首次調整，專家預期對房市影響有限。", (60, 60, 60)),
        (44, "高鐵 週末加開 12 班次疏運連假人潮", (20, 20, 20)),
        (30, "台灣高鐵公司表示，連假期間南下北上各加開六班次。", (60, 60, 60)),
    ])
    shot("weather", (225, 240, 255), (40, 120, 200), "中央氣象署 - 台北市天氣", [
        (50, "台北市 今日天氣", (10, 40, 90)),
        (44, "多雲時晴  氣溫 22 ~ 28 度", (20, 20, 20)),
        (44, "降雨機率 20%   濕度 65%", (20, 20, 20)),
        (36, "明日：陰短暫雨  氣溫 21 ~ 25 度", (60, 60, 60)),
        (36, "紫外線指數：中量級", (60, 60, 60)),
    ])
    shot("code", (30, 30, 35), (50, 50, 60), "mac_voice_router.py - Visual Studio Code", [
        (30, "def set_system_volume(level, mute):", (220, 220, 100)),
        (30, "    clamped = max(0, min(100, int(level)))", (200, 200, 200)),
        (30, "    subprocess.run([\"osascript\", \"-e\",", (200, 200, 200)),
        (30, "        f\"set volume output volume {clamped}\"])", (200, 200, 200)),
        (30, "    return f\"音量設為 {clamped}%\"", (150, 220, 150)),
    ])
    return out


@pytest.fixture
def fake_screen(monkeypatch):
    """screencapture 以固定畫面取代（其餘如 sips 照常真的執行），其他指令照常記錄"""
    state = {"image": None}
    real = subprocess.run

    def run(cmd, **kw):
        if cmd[0] == "screencapture":
            shutil.copy(state["image"], cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    return state


def test_read_screen_latency_baseline(shots, fake_screen, tmp_path):
    """C-V1：同一張畫面 5 次，記錄 prompt_n／prefill／decode／整體（不判定通過與否，D9）"""
    fake_screen["image"] = shots / "news.png"
    r.warmup_llm()
    rows = []
    for _ in range(5):
        ctx = r.ExecContext()
        ctx.step = {}
        res = r.read_screen(ctx)
        assert res.startswith("✅"), res
        vl = ctx.step["vl"]
        rows.append((ctx.step["capture_ms"], ctx.step["resize_ms"], ctx.step["vl_ms"], vl["prompt_n"], vl["prefill_ms"], vl["decode_ms"], vl["predicted_n"]))
    print("\n| 次 | capture ms | resize ms | vl ms | prompt_n | prefill ms | decode ms | predicted_n |\n|---|---|---|---|---|---|---|---|")
    for i, row in enumerate(rows, 1):
        print(f"| {i} | " + " | ".join(f"{v:.0f}" for v in row) + " |")
    print(f"vl_ms 中位 {statistics.median(x[2] for x in rows):.0f}，最大 {max(x[2] for x in rows):.0f}")
    assert len(rows) == 5


@pytest.mark.parametrize("name", ["news", "weather", "code"])
def test_read_screen_content_for_review(shots, fake_screen, name):
    """C-V2：印出模型對固定畫面的輸出，由人工判斷（D9）。這裡只確認有輸出、為繁體、暫存檔已清除"""
    fake_screen["image"] = shots / f"{name}.png"
    ctx = r.ExecContext()
    ctx.step = {}
    res = r.read_screen(ctx)
    print(f"\n=== {name} ===\n{res}")
    assert res.startswith("✅") and ctx.replies
    assert r.to_traditional(ctx.replies[0]) == ctx.replies[0]
    assert list(Path(tempfile.gettempdir()).glob("macvoice_*")) == []
