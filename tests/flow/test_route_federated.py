"""聯合搜尋（查詢路由計畫 docs/plans/completed/2026-11-23-query-routing.md §4.3、§4.4、U5、U6、U8、U9、U11，§7.1 A5～A8、A10）。
沒說範圍的『搜尋履歷』『找周杰倫』：本機（假 mdfind）與網路（假 Brave；沒有金鑰就是一筆 Google 搜尋）並行，合併成一份清單。"""
import json
import subprocess
import time
import urllib.parse

import pytest

import mac_ai.router as r
from mac_ai.assistant import command_record
from mac_ai.router import memory, prefs_db
from support.expect import URL
from support.llm import forbid_llm
from support.web import KEY as BRAVE_KEY
from support.web import FakeBrave, brave, load  # noqa: F401

NOW = 1_800_000_000.0
WIKI = "https://zh.wikipedia.org/wiki/%E5%91%A8%E6%9D%B0%E5%80%AB"
TITLES = ["周杰倫 - 維基百科，自由的百科全書", "周杰倫 Jay Chou 官方網站", "周杰倫 - 百度百科"]


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    forbid_llm(monkeypatch, "聯合搜尋不該呼叫 LLM")
    monkeypatch.setattr(prefs_db, "_now", lambda: NOW)


def say(text):
    return r.process_voice_command(text)


def files(find_env, *names):
    find_env.mdfind_paths = [find_env.touch(f"docs/{n}", mtime=1_700_000_000 - i) for i, n in enumerate(names)]
    return find_env.mdfind_paths


def kinds(step):
    return [i["kind"] for i in step["choices"]["items"]]


def terms(db):
    return db.conn().execute("SELECT term, side, signal, path FROM search_terms ORDER BY id").fetchall()


# ---------- A7：沒有 Brave 金鑰 ----------
def test_without_search_key_web_side_is_one_google_item_and_nothing_is_fetched(find_env, monkeypatch):
    fake = FakeBrave()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    files(find_env, "履歷A.pdf", "履歷B.pdf")
    (step,) = say("搜尋履歷")["steps"]
    assert fake.calls == [] and kinds(step) == ["pdf", "pdf", "web"]
    assert step["choices"]["items"][-1]["name"] == "在 Google 搜尋「履歷」" and step["choices"]["items"][-1]["dir"] == "www.google.com"
    assert find_env.opens() == [] and step["status"] == "ok" and step["federated"]["web_error"] == "no_key"


def test_without_search_key_and_no_local_hit_opens_google_exactly_as_before(find_env):
    (step,) = say("找周杰倫")["steps"]
    assert find_env.opens() == [["open", URL("周杰倫")]] and step["logs"] == ["✅ 已開啟瀏覽器搜尋：周杰倫"]
    assert "choices" not in step and step["federated"]["local"] == 0


# ---------- A5：並行與合併 ----------
def test_both_sides_run_in_parallel(find_env, brave, monkeypatch):  # noqa: F811
    delay = 0.4
    orig = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (time.sleep(delay) if cmd[0] == "mdfind" else None) or orig(cmd, **kw))
    sample = load()
    brave.value = lambda req: (time.sleep(delay), sample)[1]
    files(find_env, "周杰倫.txt", "周杰倫歌詞.txt")
    t = time.perf_counter()
    (step,) = say("找周杰倫")["steps"]
    elapsed = time.perf_counter() - t
    assert elapsed < delay * 1.6, f"兩邊各 {delay}s，整步 {elapsed:.2f}s：沒有並行"
    assert step["federated"]["local"] == 2 and step["federated"]["web"] == 3


def test_list_takes_at_most_three_local_and_two_web(find_env, brave):  # noqa: F811
    files(find_env, *[f"周杰倫{i}.txt" for i in range(6)])
    (step,) = say("找周杰倫")["steps"]
    assert kinds(step) == ["txt", "txt", "txt", "web", "web"] and step["choices"]["total"] == 9
    assert [i["name"] for i in step["choices"]["items"][3:]] == TITLES[:2]
    assert [i["dir"] for i in step["choices"]["items"][3:]] == ["zh.wikipedia.org", "www.jaychou.example"]


def test_only_one_web_result_and_no_local_hit_opens_it_directly(find_env, brave):  # noqa: F811
    data = load()
    data["web"]["results"] = data["web"]["results"][:1]
    brave.value = data
    (step,) = say("找周杰倫")["steps"]
    assert find_env.opens() == [["open", WIKI]] and "choices" not in step and step["status"] == "ok"


def test_one_local_hit_and_only_google_left_reveals_it(find_env, brave):  # noqa: F811
    """Brave 0 筆 → 網路只剩 Google 搜尋：本機 1 筆就直接顯示，不列清單（使用者 2026-11-23 決定）"""
    brave.value = {"web": {"results": []}}
    (path,) = files(find_env, "周杰倫.txt")
    (step,) = say("找周杰倫")["steps"]
    assert find_env.opens() == [["open", "-R", str(path)]] and "choices" not in step and step["federated"]["web_error"] == "no_results"


def test_one_local_hit_without_search_key_reveals_it_as_before(find_env):
    (path,) = files(find_env, "履歷.pdf")
    (step,) = say("找履歷")["steps"]
    assert find_env.opens() == [["open", "-R", str(path)]] and "choices" not in step


def test_front_site_keeps_site_search(find_env, front, brave):  # noqa: F811
    front("Safari", host="www.youtube.com")
    say("搜尋鋼琴教學")
    assert find_env.mdfinds() == [] and brave.calls == []
    assert find_env.opens() == [["open", "-a", "Safari", f"https://www.youtube.com/results?search_query={urllib.parse.quote('鋼琴教學')}"]]


# ---------- A6：閉環 ----------
@pytest.mark.parametrize("brave_value, web_error", [(TimeoutError("timed out"), "timeout"), (429, "rate_limited"), (b"{not json", "bad_format")])
def test_local_timeout_and_web_failure_still_opens_google(find_env, brave, brave_value, web_error):  # noqa: F811
    find_env.mdfind_error = subprocess.TimeoutExpired("mdfind", 3)
    brave.value = brave_value
    out = say("找周杰倫")
    (step,) = out["steps"]
    assert find_env.opens() == [["open", URL("周杰倫")]] and step["status"] == "ok"
    assert step["federated"]["local_error"] is True and step["federated"]["web_error"]


def test_local_timeout_with_web_results_lists_the_web(find_env, brave):  # noqa: F811
    find_env.mdfind_error = subprocess.TimeoutExpired("mdfind", 3)
    (step,) = say("找周杰倫")["steps"]
    assert kinds(step) == ["web", "web"] and step["status"] == "ok"


def test_every_combination_gives_a_definite_answer(find_env, brave):  # noqa: F811
    """A6：本機（有／0／逾時）× 網路（有／0／失敗），都是 ✅ 或 ⚠️／❌ 加原因，沒有空回覆、沒有轉交"""
    for local in ("hit", "none", "timeout"):
        for web in ("hit", "none", "fail"):
            find_env.calls.clear()
            find_env.mdfind_error = subprocess.TimeoutExpired("mdfind", 3) if local == "timeout" else None
            find_env.mdfind_paths = [find_env.touch("docs/周杰倫.txt")] if local == "hit" else []
            brave.value = load() if web == "hit" else {"web": {"results": []}} if web == "none" else 500
            (step,) = say("找周杰倫")["steps"]
            assert step["logs"] and step["logs"][0][:1] in ("✅", "⚠", "❌"), (local, web, step["logs"])
            assert "escalated" not in step


# ---------- A8：選網路項目（U11）----------
def test_pick_web_item_opens_its_url_in_default_browser(find_env, brave):  # noqa: F811
    files(find_env, "周杰倫.txt")
    say("找周杰倫")
    (step,) = say("第二個")["steps"]
    assert find_env.opens() == [["open", WIKI]] and step["logs"] == ["✅ 已用瀏覽器打開網路結果"]


def test_pick_web_item_uses_the_front_browser(find_env, brave, front):  # noqa: F811
    front("Safari", host="example.com")
    files(find_env, "周杰倫.txt")
    say("找周杰倫")
    say("第二個")
    assert find_env.opens() == [["open", "-a", "Safari", WIKI]]


def test_pick_google_item_without_search_key(find_env):
    files(find_env, "履歷A.pdf", "履歷B.pdf")
    say("搜尋履歷")
    say("第三個")
    assert find_env.opens() == [["open", URL("履歷")]]


def test_pick_local_item_still_only_reveals(find_env, brave):  # noqa: F811
    (path,) = files(find_env, "周杰倫.txt")
    say("找周杰倫")
    say("打開第一個")
    assert find_env.opens() == [["open", str(path)]]  # 明說打開才開檔（0005 第 3 條）
    say("找周杰倫")
    say("第一個")
    assert find_env.opens()[-1] == ["open", "-R", str(path)]


# ---------- 統計（U5、U6、U8）----------
def test_picks_are_recorded_local_with_path_and_web_without_url(find_env, brave, music_db):  # noqa: F811
    (path,) = files(find_env, "周杰倫.txt")
    say("找周杰倫")
    say("第一個")
    say("找周杰倫")
    say("第二個")
    assert terms(music_db) == [("周杰倫", "local", "picked", str(path)), ("周杰倫", "web", "picked", None)]


def test_showing_the_list_or_a_lean_decision_is_not_recorded(find_env, music_db):
    files(find_env, "履歷A.pdf", "履歷B.pdf")
    say("搜尋履歷")
    assert terms(music_db) == []


def test_explicit_scope_is_recorded(find_env, music_db):
    files(find_env, "履歷A.pdf")
    say("找履歷檔案")
    say("上網查履歷")
    find_env.mdfind_paths = []
    say("找不存在的檔名xyz123檔案")  # 0 筆不記
    assert terms(music_db) == [("履歷", "local", "explicit", None), ("履歷", "web", "explicit", None)]


def test_two_local_picks_then_the_path_is_used_directly(find_env, music_db):
    a, b = files(find_env, "履歷A.pdf", "履歷B.pdf")
    for _ in range(2):
        say("搜尋履歷")
        say("第二個")
    find_env.calls.clear()
    (step,) = say("搜尋履歷")["steps"]
    assert find_env.mdfinds() == [] and find_env.opens() == [["open", "-R", str(b)]] and step["federated"]["used"] == "path"
    assert len(terms(music_db)) == 2  # 統計決定的不記（不會越記越偏）


def test_one_web_choice_after_two_local_picks_lists_again(find_env, music_db):
    """§7.2 L3：另一邊不是 0 次，U8 不成立 → 照常查兩邊、列清單（本機仍排前面）"""
    files(find_env, "履歷A.pdf", "履歷B.pdf")
    for _ in range(2):
        say("搜尋履歷")
        say("第二個")
    say("上網查履歷")
    find_env.calls.clear()
    (step,) = say("搜尋履歷")["steps"]
    assert len(find_env.mdfinds()) == 1 and find_env.opens() == []
    assert kinds(step) == ["pdf", "pdf", "web"] and step["federated"]["lean"] == "local" and "used" not in step["federated"]
    assert [(s, sig) for _, s, sig, _ in terms(music_db)] == [("local", "picked")] * 2 + [("web", "explicit")]


def test_recorded_path_gone_searches_local_only_again(find_env, brave, music_db):  # noqa: F811
    a, b = files(find_env, "履歷A.pdf", "履歷B.pdf")
    for _ in range(2):
        say("搜尋履歷")
        say("第二個")
    b.unlink()
    find_env.mdfind_paths = [a]
    find_env.calls.clear()
    brave.calls.clear()
    (step,) = say("搜尋履歷")["steps"]
    assert len(find_env.mdfinds()) == 1 and brave.calls == [] and find_env.opens() == [["open", "-R", str(a)]]


def test_web_lean_opens_the_web_without_searching_files(find_env, brave, music_db):  # noqa: F811
    say("上網查周杰倫")
    say("google 一下周杰倫")
    find_env.calls.clear()
    brave.calls.clear()
    (step,) = say("找周杰倫")["steps"]
    assert find_env.mdfinds() == [] and brave.calls == [] and find_env.opens() == [["open", URL("周杰倫")]]
    assert step["federated"]["used"] == "web"


def test_weak_web_lean_puts_web_items_first(find_env, brave, music_db):  # noqa: F811
    files(find_env, "周杰倫.txt", "周杰倫2.txt")
    say("上網查周杰倫")
    (step,) = say("找周杰倫")["steps"]
    assert kinds(step) == ["web", "web", "txt", "txt"] and step["federated"]["lean"] == "web"


def test_database_not_ready_still_searches(find_env, brave):  # noqa: F811
    files(find_env, "周杰倫.txt")
    (step,) = say("找周杰倫")["steps"]
    assert kinds(step) == ["txt", "web", "web"] and "lean" not in step["federated"]


# ---------- A10：資料安全 ----------
def test_search_results_and_key_leak_nowhere(find_env, brave, music_db, capsys):  # noqa: F811
    files(find_env, "周杰倫.txt")
    out1 = say("找周杰倫")
    out2 = say("第二個")
    records = json.dumps([command_record({"command": c, "ok": True, "total_ms": 1, "replies": o["replies"]}, o)
                          for c, o in (("找周杰倫", out1), ("第二個", out2))], ensure_ascii=False)
    printed = capsys.readouterr().out
    remembered = json.dumps(memory.STORE.__dict__, ensure_ascii=False, default=str)
    stored = json.dumps(music_db.conn().execute("SELECT * FROM search_terms").fetchall(), ensure_ascii=False)
    for text in (records, printed, remembered, stored):
        assert not any(t in text for t in TITLES) and "wikipedia" not in text and "jaychou" not in text
        assert BRAVE_KEY not in text
    assert "choices" in out1["steps"][0]  # UI 拿得到標題與網域（只在記憶體，不進紀錄）
