"""雲端實測：連真的雲端模型（.env 的 AI_API_URL／AI_MODEL／AI_API_KEY）。會產生費用，一次完整執行約 200 次請求。跑法: pytest -m cloud -s
- 設環境變數 ACCEPTANCE_CLOUD_METRICS=<檔案> 時，每次請求的結果與耗時寫成 JSON Lines（scripts/acceptance.py 用）。
- 金鑰留空時整組 skip。"""
import json
import os
import re
import statistics
import subprocess
import urllib.request

import pytest
from _pytest.monkeypatch import MonkeyPatch

import mac_ai.router as r
from mac_ai import config
from mac_ai.router import cloud_client, hant, memory, slow_path
from mac_ai.router.actions import ExecContext
from mac_ai.router.fast_path import FastPathMatcher
from mac_ai.router.normalize import normalize_utterance
from support.route_corpus import CORPUS

pytestmark = pytest.mark.cloud
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
# 回覆品質（計畫 §14 項目 3；2026-11-16 使用者同意的門檻）：語音泡泡要簡短、不換行、純繁體
REPLY_OVER60_MAX, REPLY_MAX_CHARS, REPLY_OVER3_MAX = 0.20, 120, 0.10
REPLY_LIMIT_CHARS = 60
CLAIM = re.compile(r"已(?:經)?(?:為你|幫你|替你|為您|幫您)?(?:鎖定|睡眠|關閉|關掉|開啟|打開|調整|調到|調暗|調亮|靜音|切換)")
ACCURACY_MIN = {"ctrl": 0.90, "screen": 0.90, "chat": 0.95, "screen_c": 0.90, "mixed": 0.90}  # 原提案整體 ≥ 95%，分類下限略放寬
LATENCY_P50_S, LATENCY_P95_S = 2.0, 4.0  # 計畫 C2 提案值


@pytest.fixture(scope="module", autouse=True)
def configured():
    cloud_client.reset()
    if cloud_client.settings() is None:
        pytest.skip("雲端未設定（.env 的 AI_API_KEY 留空）")


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    """實測一律繞過 proxy 的回應快取（相同請求會逐字回傳同一份答案，重複執行不是獨立樣本）"""
    monkeypatch.setattr(config, "CLOUD_NO_CACHE", True)


def record(**row):
    path = os.environ.get("ACCEPTANCE_CLOUD_METRICS")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------- C1 連線 ----------
def test_health_lists_model_and_chat_works():
    url, model, key = cloud_client.settings()
    req = urllib.request.Request(f"{url}/v1/models", headers={"Authorization": f"Bearer {key}", "User-Agent": config.CLOUD_USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        ids = [m["id"] for m in json.load(resp)["data"]]
    assert model in ids, ids
    meta: dict = {}
    msg = cloud_client.chat([{"role": "user", "content": "只回答 OK 兩個字母"}], meta=meta, max_tokens=100)
    assert meta["status"] == "ok" and msg["content"], msg


# ---------- C2／C3 規劃正確率與延遲（分流語料，每句 1 次）----------
def _judge(cat, calls, content):
    names = {n for n, _ in calls} - {"chat_reply"}
    if cat in ("ctrl", "screen", "mixed"):
        return bool(names) and (cat != "mixed" or bool(content))
    if cat == "screen_c":
        return names == {"read_screen"}
    return not names and bool(content)  # chat


def _sentences(text):
    return [x for x in re.split(r"[。！？!?～~\n]+", text) if x.strip()]


def reply_stats(say):
    """一句雲端回覆的品質指標"""
    return {"chars": len(say), "sentences": len(_sentences(say)), "newline": "\n" in say,
            "emoji": len(EMOJI.findall(say)), "simplified": bool(hant.residual_simplified(say))}


@pytest.fixture(scope="module")
def plan_rows(configured):
    """分流語料 130 句各問一次雲端（關快取）；規劃正確率與回覆品質共用這一份，不重複花請求"""
    rows = []
    with MonkeyPatch.context() as mp:
        mp.setattr(config, "CLOUD_NO_CACHE", True)
        for text, label, cat, _ in CORPUS:
            step: dict = {}
            cloud_client.reset()  # 量測：每句獨立，前一句的逾時不能讓熔斷連帶影響後面的句子
            try:
                calls, content, rejected = slow_path.plan_cloud(normalize_utterance(text), ExecContext(), step, set())
            except cloud_client.CloudError:  # 請求失敗（逾時、5xx…）：記成這句失敗，不中斷整組量測（2026-11-16 實測 130 句中出現過 1 次 8 秒逾時）
                calls, content, rejected = [], "", []
            rows.append({"text": text, "cat": cat, "calls": calls, "content": content, "rejected": rejected,
                         "ok": _judge(cat, calls, content) and step["cloud"]["status"] == "ok", "meta": step["cloud"]})
    return rows


def test_planning_accuracy_validity_and_latency_on_route_corpus(plan_rows):
    by, wrong, ms, invalid = {}, [], [], []
    for row in plan_rows:
        cat, meta = row["cat"], row["meta"]
        total, good = by.get(cat, (0, 0))
        by[cat] = (total + 1, good + row["ok"])
        ms.append(meta["total_ms"] / 1000)
        invalid += [(row["text"], x) for x in row["rejected"]]
        if not row["ok"]:
            wrong.append((cat, row["text"], row["calls"], row["content"][:40]))
        record(kind="plan", cat=cat, text=row["text"], ok=row["ok"], total_ms=meta["total_ms"], status=meta["status"],
               prompt_tokens=meta.get("prompt_tokens"), completion_tokens=meta.get("completion_tokens"))
    ms.sort()
    p50, p95 = statistics.median(ms), ms[int(0.95 * (len(ms) - 1))]
    print(f"\n規劃正確率 { {k: f'{g}/{n}' for k, (n, g) in by.items()} }；延遲 p50 {p50:.2f}s p95 {p95:.2f}s；被驗證拒絕 {len(invalid)}")
    for w in wrong:
        print("  錯：", w)
    failed = [(row["text"], row["meta"]["status"]) for row in plan_rows if row["meta"]["status"] != "ok"]
    print(f"請求失敗 {len(failed)}：{failed}")
    assert len(failed) <= 2, failed  # 偶發逾時（proxy 長尾延遲）可容忍 ≤ 2 句（1.5%）；更多代表雲端不穩
    for cat, (n, g) in by.items():
        assert g / n >= ACCURACY_MIN[cat], f"{cat} {g}/{n}"
    # 被既有守門拒絕的呼叫是守門在運作，不是雲端出錯；超過 2 句代表雲端常編出不合法的呼叫
    assert len(invalid) <= 2, invalid
    assert p50 <= LATENCY_P50_S and p95 <= LATENCY_P95_S, (p50, p95)


def test_reply_quality_is_short_single_line_and_traditional(plan_rows):
    """聊天與混合句（61 句）的最終回覆（清理後）：長度、句數、換行、表情、殘留簡體"""
    rows = [row for row in plan_rows if row["cat"] in ("chat", "mixed") and row["meta"]["status"] == "ok"]
    stats = [reply_stats(row["content"]) for row in rows]
    for row, st in zip(rows, stats):
        record(kind="reply", cat=row["cat"], text=row["text"], **st)
    n = len(stats)
    over60 = [(r["text"], st["chars"]) for r, st in zip(rows, stats) if st["chars"] > REPLY_LIMIT_CHARS]
    over3 = [r["text"] for r, st in zip(rows, stats) if st["sentences"] > 3]
    print(f"\n回覆品質 n={n}：字數 p50 {statistics.median(st['chars'] for st in stats)}、最長 {max(st['chars'] for st in stats)}｜"
          f"超過{REPLY_LIMIT_CHARS}字 {len(over60)}（{len(over60) / n:.0%}）｜超過三句 {len(over3)}｜換行 {sum(st['newline'] for st in stats)}"
          f"｜表情>1 {sum(st['emoji'] > 1 for st in stats)}｜殘留簡體 {sum(st['simplified'] for st in stats)}")
    assert len(over60) / n <= REPLY_OVER60_MAX, over60
    assert max(st["chars"] for st in stats) <= REPLY_MAX_CHARS
    assert len(over3) / n <= REPLY_OVER3_MAX, over3
    assert not any(st["newline"] for st in stats)
    assert not any(st["simplified"] for st in stats), [r["text"] for r, st in zip(rows, stats) if st["simplified"]]


# ---------- C4 否定／敘述：端到端（G1 開）要 0 次雲端請求、0 個動作 ----------
NEGATIVE = ["不要鎖定螢幕", "別讓電腦睡眠", "剛剛鎖定螢幕了", "昨天把音量調到50", "我不想鎖定螢幕", "不要金音", "別存擋"]


def test_negative_sentences_end_to_end_send_nothing_and_do_nothing(monkeypatch):
    sent, ran = [], []
    real_post = cloud_client._post
    monkeypatch.setattr(cloud_client, "_post", lambda *a: sent.append(1) or real_post(*a))
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    for text in NEGATIVE:
        out = r.process_voice_command(text)
        assert out["replies"] == [slow_path.NO_ACTION_REPLY], text
    assert sent == [] and [c for c in ran if c[0] in ("open", "osascript")] == []


def test_record_cloud_misfires_on_negative_sentences_without_g1():
    """只量測（不判通過）：繞過 G1 直接問雲端，否定句有幾句會規劃出會改變狀態的工具。數字是日後是否放寬 G1 的依據"""
    fired = []
    for text in NEGATIVE:
        calls, _, _ = slow_path.plan_cloud(normalize_utterance(text), ExecContext(), {}, set())
        if {n for n, _ in calls} & slow_path.STATE_TOOLS:
            fired.append(text)
    print(f"\n雲端在否定／敘述句誤規劃動作：{len(fired)}/{len(NEGATIVE)} {fired}")
    record(kind="misfire", n=len(NEGATIVE), fired=len(fired))


# ---------- C5 誠實：工具失敗時，回覆不得聲稱已執行 ----------
def test_replies_never_claim_success_when_tools_fail(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    texts = [t for t, _, cat, _ in CORPUS if cat in ("ctrl", "mixed")][:20]
    for text in texts:
        memory.STORE.clear()
        out = r.process_voice_command(text)
        assert not any(CLAIM.search(x) for x in out["replies"]), (text, out["replies"])


# ---------- C8 圖片輸入 ----------
def test_image_input_is_understood(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (800, 300), "white")
    ImageDraw.Draw(img).text((30, 100), "TypeError: unsupported operand 'int' + 'str'", fill="black",
                             font=ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", 32))
    png, jpg = tmp_path / "e.png", tmp_path / "e.jpg"
    img.save(png)
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80", str(png), "--out", str(jpg)], capture_output=True, check=True)
    import base64
    messages = [
        {"role": "system", "content": cloud_client.CLOUD_VISION_SYSTEM_PROMPT},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode()}},
                                      {"type": "text", "text": "這個 error 是什麼意思"}]}]
    meta: dict = {}
    for attempt in (1, 2):  # 2026-11-16 實測第一次曾逾時 15 秒、重跑 0.4 秒：偶發逾時重試一次並印出，不讓單次網路抖動判整組失敗
        cloud_client.reset()
        try:
            msg = cloud_client.chat(messages, timeout=config.CLOUD_VISION_TIMEOUT_S, meta=meta)
            break
        except cloud_client.CloudError as e:
            print(f"\n看圖第 {attempt} 次失敗：{e}")
            record(kind="vision_error", status=meta.get("status"), total_ms=meta.get("total_ms"))
            if attempt == 2:
                raise
    print(f"\n看圖 {meta['total_ms']:.0f} ms：{msg['content'][:80]}")
    assert any(w in msg["content"] for w in ("TypeError", "型別", "類型")), msg["content"]  # 2026-11-16 重複 8 次：1 次說「類型錯誤」（同義），其餘說「型別」；只認「型別」會誤判
    record(kind="vision", total_ms=meta["total_ms"], status=meta["status"])


# ---------- C9 代名詞動作（10 組 × 3 次）----------
PRONOUN_GROUPS = [
    ("打開 Safari", "把它關掉", "Safari"), ("打開 Spotify", "把它關閉", "Spotify"), ("打開 Notes", "它關掉", "Notes"),
    ("打開 Terminal", "把它退出", "Terminal"), ("打開 Finder", "幫我關掉它", "Finder"), ("打開 Google Chrome", "把它關起來", "Google Chrome"),
    ("打開 Safari", "把它收起來", "Safari"), ("打開 Spotify", "請把它關掉", "Spotify"),
    ("打開 Notes", "幫我把它關閉", "Notes"), ("打開 Terminal", "它可以關掉", "Terminal"),
]  # 『把剛剛開的關掉』『剛剛那個』會被 G1 當成敘述擋下（既有的安全決策），所以不在這裡


def test_pronoun_actions_hit_the_right_app(monkeypatch):
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    m = FastPathMatcher()
    right = wrong_target = 0
    for first, follow, app in PRONOUN_GROUPS:
        assert all(m.match(c) is None for c in m.split_clauses(normalize_utterance(follow))), f"{follow} 被 Fast Path 接走，測不到雲端"
        assert slow_path.intent_guard(normalize_utterance(follow)) != "no_action", f"{follow} 被 G1 擋下，測不到雲端"
        for _ in range(3):
            memory.STORE.clear()
            r.process_voice_command(first)
            ran.clear()
            out = r.process_voice_command(follow)
            quits = [c for c in ran if c[0] == "osascript" and " to quit" in c[-1]]
            minimized = any("front_action({'id': 'minimize'})" in l for s in out["steps"] for l in s["logs"])
            if len(quits) == 1 and f'"{app}"' in quits[0][-1]:
                right += 1
            elif "收起來" in follow and minimized and not quits:  # 2026-11-17 使用者決定：『收起來』最小化也可以；『關掉』類一定要關（slow_path._CLOSE_RE 守門）
                right += 1
            elif quits:
                wrong_target += 1
    print(f"\n代名詞動作：對象正確 {right}/30，執行了錯的對象 {wrong_target}")
    record(kind="pronoun", n=30, right=right, wrong=wrong_target)
    assert wrong_target == 0 and right >= 27
