"""找東西整條路由：語句 → Fast Path → 本機搜尋 → 選擇；本機 0 筆才轉交 LLM（假 LLM）；查詢用使用者原話"""
import json
import subprocess

import pytest

import mac_ai.router as r
from support.llm import FakeLLM, forbid_llm, llm_always, tc


@pytest.fixture
def xcode(installed_apps):
    installed_apps({"xcode": "Xcode"})


def test_reroute_find_to_app(monkeypatch, xcode):
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query":"Xcode"}')])
    meta = {}
    calls, _, rejected = r.plan_slow_path("幫我把那個 Xcode 找出來", meta)
    assert rejected == [] and calls == [("control_mac_app", {"app_name": "Xcode", "action": "open"})]
    assert meta["reroutes"] == ["[Domain 導向] 搜尋「Xcode」→ app"] and meta["intent"]["domain"] == "app"


def test_reroute_find_to_folder_and_file(monkeypatch, xcode):
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query":"下載"}')])
    calls, _, _ = r.plan_slow_path("找一下下載資料夾")
    assert calls == [("open_folder", {"path_key": "Downloads"})]
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query":"履歷.pdf"}')])
    calls, _, _ = r.plan_slow_path("找一下履歷.pdf")
    assert calls == [("find_file", {"name": "履歷", "kinds": ["pdf"], "filename": "履歷.pdf", "local_only": True, "action": "reveal"})]


@pytest.mark.parametrize("utterance,query", [
    ("搜尋 Xcode", "Xcode"),  # 『搜尋／查』沒有範圍詞：維持網頁搜尋
    ("查一下 Xcode", "Xcode"),
    ("幫我找一下最近的新聞", "最近的新聞"),  # 即時資訊維持網頁（天氣改走回答，見 test_route_weather.py）
    ("網路上找 Xcode", "Xcode"),
    ("不要找 Xcode", "Xcode"),  # 否定句不導向（維持 LLM 原本的決定）
    ("找 Xcode 嗎", "Xcode"),  # 提問不導向
    ("找一下 Xcode", "完全不同的東西"),  # 受詞和 LLM 的 query 對不上：不導向
])
def test_web_search_stays_web(monkeypatch, xcode, utterance, query):
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query":"%s"}' % query)])
    calls, _, rejected = r.plan_slow_path(utterance)
    assert rejected == [] and calls == [("web_search_or_open_url", {"query": query})]


def test_reroute_is_logged_when_executed(monkeypatch, sh, xcode):
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query":"Xcode"}')])
    step = {}
    logs = r.execute_slow_path_llm("幫我把那個 Xcode 找出來", step)
    assert sh == [["open", "-a", "Xcode"]]
    assert logs[0] == "[Domain 導向] 搜尋「Xcode」→ app" and "control_mac_app" in logs[1]
    assert step["intent"]["domain"] == "app"


def test_llm_app_not_installed_is_rejected_and_opens_nothing(monkeypatch, sh):
    llm_always(monkeypatch, [tc("control_mac_app", '{"app_name":"Photoshop","action":"open"}')])
    out = r.process_voice_command("打開 Photoshop")
    assert sh == []  # 沒有任何 open -a
    (step,) = out["steps"]
    assert step["kind"] == "slow" and step["status"] == "rejected"
    assert "找不到 App：Photoshop（沒有安裝）" in step["logs"][0]


def test_llm_installed_app_is_opened(monkeypatch, sh, installed_apps):
    installed_apps({"chatgpt": "ChatGPT"})
    llm_always(monkeypatch, [tc("control_mac_app", '{"app_name":"chatgpt","action":"open"}')])
    r.process_voice_command("我想用 ChatGPT")
    assert sh == [["open", "-a", "ChatGPT"]]


@pytest.mark.parametrize("name,args", [
    ("find_file", '{"name":"履歷"}'), ("open_folder", '{"path_key":"Home"}'), ("pick_choice", '{"n":1}'),
])
def test_llm_cannot_call_internal_tools(monkeypatch, sh, name, args):
    llm_always(monkeypatch, [tc(name, args)])
    out = r.process_voice_command("我想用電腦做點事")
    assert sh == [] and out["steps"][0]["status"] == "rejected"
    assert f"未知工具：{name}" in out["steps"][0]["logs"][0]


# 有本機證據的句子：就算 0 筆也不呼叫 LLM（只回『本機沒有』）；網頁類由 Fast Path 直接搜尋
NO_LLM_CASES = [
    ("欸你幫我在電腦裡面找那個叫做履歷的檔案", "local"),
    ("找 report.pdf", "local"),
    ("找昨天下載的 PDF", "local"),
    ("上網找 Xcode", "web"),  # 「搜尋一下 Xcode 是什麼」「找一下最近的新聞」改成直接回答（U7、U12，test_route_verify.py）
    ("找一下股票走勢", "web"),
]


@pytest.mark.parametrize("utterance,kind", NO_LLM_CASES, ids=[c[0] for c in NO_LLM_CASES])
def test_requests_with_clear_scope_never_call_the_llm(monkeypatch, find_env, utterance, kind):
    forbid_llm(monkeypatch, "不該呼叫 LLM")
    (find_env.home / "Downloads").mkdir()
    out = r.process_voice_command(utterance)
    (step,) = out["steps"]
    assert step["kind"] == "fast" and "llm_ms" not in step and "escalated" not in step
    google = any("google.com/search" in " ".join(c) for c in find_env.opens())
    if kind == "local":
        assert find_env.mdfinds() and not google and "本機沒有" in step["logs"][0]
    else:
        assert google and not find_env.mdfinds()


# ---------- 閉環：本機 0 筆、沒有本機證據 → 轉交模型判斷 → 開網頁／確認找不到 ----------
# 沒說範圍的『找 X』改走聯合搜尋（查詢路由 U6，test_route_federated.py）；轉交只剩『找我的 X』與模型自己選 find_file，這裡用『找我的 X』

def test_escalated_query_can_become_a_web_search(monkeypatch, find_env):
    llm = FakeLLM([tc("web_search_or_open_url", '{"query":"python 教學"}')]).install(monkeypatch)
    out = r.process_voice_command("找我的 python 教學")
    (step,) = out["steps"]
    assert len(find_env.mdfinds()) == 1  # 先搜本機一次；模型的搜尋不會再被導回本機（不繞圈）
    assert any("google.com/search" in " ".join(c) for c in find_env.opens())
    assert step["escalated"] == {"label": "python 教學", "scope": "家目錄，不含 ~/Library", "query": "我的 python 教學"}
    assert step["status"] == "ok" and step["llm_ms"] >= 0
    assert step["logs"][0].startswith("🔎 本機沒有符合「python 教學」")
    system, user = llm.calls[0][0]["content"], llm.calls[0][-1]["content"]
    assert "本機沒有名為" not in system  # 放在使用者訊息：system＋工具定義的前綴快取才不會失效（實測差 2 秒）
    assert user == "找我的 python 教學\n\n## 本機搜尋結果\n本機沒有名為「python 教學」的檔案。"


def test_escalated_query_without_any_action_confirms_not_found(monkeypatch, find_env):
    llm = FakeLLM("好").install(monkeypatch)  # 模型沒有選任何工具
    out = r.process_voice_command("找我的履歷")
    (step,) = out["steps"]
    assert len(llm.calls) == 1  # 不另外觸發 chat call 閒聊
    assert out["replies"] == ["⚠️ 找不到「履歷」：本機沒有符合的檔案（範圍：家目錄，不含 ~/Library）；AI 沒有選擇上網搜尋。要上網查請說「搜尋 履歷」"]
    assert step["status"] == "exec_failed" and find_env.opens() == []


def test_escalation_when_llm_is_unavailable_still_answers(monkeypatch, find_env):
    FakeLLM(error=True).install(monkeypatch)
    out = r.process_voice_command("找我的 python 教學")
    (step,) = out["steps"]
    assert step["status"] == "llm_unavailable" and find_env.opens() == []
    assert out["replies"][0].startswith("⚠️ 找不到「python 教學」：本機沒有符合的檔案") and "AI 目前無法判斷要不要上網找" in out["replies"][0] and out["replies"][0].endswith("要上網查請說「搜尋 python 教學」")


def test_escalation_chat_reply_is_told_nothing_was_found(monkeypatch, find_env):
    llm = FakeLLM([tc("chat_reply", "{}")], {"content": "電腦裡沒有這份檔案喔"}).install(monkeypatch)
    out = r.process_voice_command("找我的履歷")
    assert out["replies"] == ["電腦裡沒有這份檔案喔"]
    chat_system = llm.calls[1][0]["content"]
    assert "本機沒有名為「履歷」的檔案" in chat_system and "不得聲稱已經找到" in chat_system


def test_escalation_with_rejected_tool_still_confirms_not_found(monkeypatch, find_env):
    FakeLLM([tc("control_mac_app", '{"app_name":"Photoshop","action":"open"}')]).install(monkeypatch)
    out = r.process_voice_command("找我的履歷")
    (step,) = out["steps"]
    assert any("找不到 App：Photoshop" in log for log in step["logs"])
    assert out["replies"][-1].startswith("⚠️ 找不到「履歷」") and find_env.opens() == []


def test_found_locally_does_not_call_the_llm(monkeypatch, find_env):
    forbid_llm(monkeypatch, "本機找得到就不該呼叫 LLM")
    path = find_env.touch("docs/履歷.pdf")
    find_env.mdfind_paths = [path]
    out = r.process_voice_command("找我的履歷")
    assert find_env.opens() == [["open", "-R", str(path)]] and "escalated" not in out["steps"][0]


def test_search_failure_is_reported_not_escalated(monkeypatch, find_env):
    forbid_llm(monkeypatch, "搜尋失敗不該轉交 LLM")
    find_env.mdfind_error = subprocess.TimeoutExpired("mdfind", 3)
    out = r.process_voice_command("找我的履歷")
    assert out["steps"][0]["logs"][0].startswith("❌ 檔案搜尋失敗") and out["steps"][0]["status"] == "exec_failed"


@pytest.mark.parametrize("utterance", ["找到了", "找不到我的鑰匙", "找最近的電影", "找影片教學"])
def test_exception_sentences_go_straight_to_the_model(monkeypatch, find_env, utterance):
    llm = FakeLLM({"content": "x"}, {"content": "這是回覆"}).install(monkeypatch)
    out = r.process_voice_command(utterance)
    (step,) = out["steps"]
    assert step["kind"] == "slow" and find_env.mdfinds() == []  # 不搜檔案
    assert out["replies"] == ["這是回覆"]


def test_escalation_keeps_spoken_order_with_other_clauses(monkeypatch, find_env):
    FakeLLM([tc("web_search_or_open_url", '{"query":"python 教學"}')]).install(monkeypatch)
    out = r.process_voice_command("找我的 python 教學，靜音")
    assert [s["kind"] for s in out["steps"]] == ["fast", "fast"]
    kinds = [c[0] for c in find_env.calls]
    assert kinds == ["mdfind", "open", "osascript"]  # 搜本機 → 轉交後開網頁 → 靜音，照說話順序


def test_model_web_decision_without_local_evidence_is_not_rerouted_back_to_files(monkeypatch):
    """沒有本機證據的『找 X』：模型判斷要上網就尊重它（本機已由 Fast Path 先找過），不再被導回檔案搜尋"""
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query":"swift 入門教材"}')])
    calls, _, _ = r.plan_slow_path("找 swift 入門教材")
    assert calls == [("web_search_or_open_url", {"query": "swift 入門教材"})]


def test_the_suggested_next_step_really_searches_the_web(monkeypatch, find_env):
    """保底訊息說『要上網查請說「搜尋 履歷」』：照著說就真的開網頁搜尋，不再問模型"""
    forbid_llm(monkeypatch)
    r.process_voice_command("搜尋 履歷")
    assert [c for c in find_env.opens()] == [["open", "https://www.google.com/search?q=%E5%B1%A5%E6%AD%B7"]]


# ---------- 轉交時搜尋關鍵字用使用者的原話 ----------
def google_queries(find_env):
    import urllib.parse
    return [urllib.parse.unquote(c[-1].split("q=")[-1]) for c in find_env.opens() if "google.com/search" in " ".join(c)]


@pytest.mark.parametrize("spoken,model_query,expected", [
    ("找我的蘋果官網", "Apple official website", "我的蘋果官網"),  # 模型翻成英文
    ("找我的天文望遠鏡推薦", "天文望遠鏡推薦 2025", "我的天文望遠鏡推薦"),  # 模型憑空加年份
    ("找我的台中早午餐", "早午餐 地點", "我的台中早午餐"),  # 模型丟掉『台中』
    ("找我的靈感", "灵感 概念 灵感 想法", "我的靈感"),  # 模型混進簡體
    ("找我的 python 教學", "Python 教學", "我的 python 教學"),  # 只差大小寫也一律用原話
])
def test_escalated_web_search_uses_original_words(monkeypatch, find_env, spoken, model_query, expected):
    FakeLLM([tc("web_search_or_open_url", json.dumps({"query": model_query}))]).install(monkeypatch)
    out = r.process_voice_command(spoken)
    (step,) = out["steps"]
    assert google_queries(find_env) == [expected] and len(find_env.mdfinds()) == 1
    assert step["model_query"] == model_query and step["used_query"] == expected  # 兩者都記下來，方便追查
    assert step["status"] == "ok"


def test_escalation_with_two_web_searches_opens_one_page(monkeypatch, find_env):
    two = {"tool_calls": [tc("web_search_or_open_url", '{"query":"A 一"}'), tc("web_search_or_open_url", '{"query":"A 二"}')]}
    FakeLLM(two).install(monkeypatch)
    r.process_voice_command("找我的附近的餐廳")
    assert google_queries(find_env) == ["我的附近的餐廳"]


def test_model_search_outside_escalation_keeps_the_models_keywords(monkeypatch, sh):
    """範圍：一般由模型發起的網頁搜尋（不是本機 0 筆轉交）這次不動，仍用模型給的關鍵字"""
    llm_always(monkeypatch, [tc("web_search_or_open_url", '{"query":"台北天氣"}')])
    r.process_voice_command("今天台北天氣會很熱嗎")
    assert sh == [["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]]


# ---------- 誤聽詞：正規化照舊替換（開 App 要用），查詢用原字 ----------
def test_normalize_records_each_misheard_replacement_in_order():
    from mac_ai.router.normalize import normalize_utterance
    rec = []
    assert normalize_utterance("開谷哥瀏覽器，找科索的評價", rec) == "開Google Chrome,找Cursor的評價"
    assert rec == [("Google Chrome", "谷哥瀏覽器"), ("Cursor", "科索")]
    assert normalize_utterance("開谷哥") == "開Google Chrome"  # 不傳 replacements：行為不變


def test_misheard_word_in_a_query_is_restored_to_what_the_user_said(monkeypatch, find_env):
    FakeLLM([tc("web_search_or_open_url", '{"query":"x"}')]).install(monkeypatch)
    r.process_voice_command("找我的谷哥評價")
    assert google_queries(find_env) == ["我的谷哥評價"]  # 不是「Google Chrome的評價」


def test_misheard_word_still_opens_the_app_when_it_is_a_command(find_env):
    r.process_voice_command("開谷哥")
    assert find_env.opens() == [["open", "-a", "Google Chrome"]]


def test_restore_picks_the_right_occurrence_across_clauses(monkeypatch, find_env):
    FakeLLM([tc("web_search_or_open_url", '{"query":"x"}')]).install(monkeypatch)
    r.process_voice_command("開谷哥瀏覽器，找我的谷哥評價")  # 兩個子句各有一個替換：第二句要對到第二筆（谷哥），不是第一筆（谷哥瀏覽器）
    assert find_env.opens()[0] == ["open", "-a", "Google Chrome"]
    assert google_queries(find_env) == ["我的谷哥評價"]


def test_restore_is_skipped_when_it_cannot_be_matched():
    from mac_ai.router.normalize import restore_misheard
    rec = [("Google Chrome", "谷哥")]
    assert restore_misheard("找谷哥的評價".replace("谷哥", "Google Chrome"), "Google Chrome的評價", rec) == "谷哥的評價"
    # 使用者本來就說過「Google Chrome」：整句出現 2 次、替換紀錄只有 1 筆 → 分不出哪個是替換的，保持原樣
    assert restore_misheard("找Google Chrome和Google Chrome", "Google Chrome和Google Chrome", rec) == "Google Chrome和Google Chrome"
    assert restore_misheard("找x", "x", []) == "x" and restore_misheard("找x", "", rec) == ""
    assert restore_misheard("找x", "不在句子裡的查詢", [("Cursor", "科索")]) == "不在句子裡的查詢"


# ---------- 運算時間記錄 ----------
def test_escalation_records_stage_timings(monkeypatch, find_env):
    FakeLLM([tc("web_search_or_open_url", '{"query":"x"}')]).install(monkeypatch)
    out = r.process_voice_command("找我的 python 教學")
    (step,) = out["steps"]
    assert {"find_ms", "extract_ms", "llm_ms", "validate_ms", "ms"} <= set(step)
    assert 0 <= step["extract_ms"] < 5 and step["find_ms"] >= 0  # 原話查詢（含誤聽還原）的運算時間
    assert step["ms"] >= step["find_ms"] + step["llm_ms"]
    print(f"\n轉交各階段：搜本機 {step['find_ms']:.2f}ms｜截取原話 {step['extract_ms']:.3f}ms｜LLM(假) {step['llm_ms']:.2f}ms｜整步 {step['ms']:.2f}ms")


# ---------- 整條路徑：語句 → Fast Path → 檔案搜尋 → 選擇 ----------
def test_pipeline_find_then_pick(find_env):
    t = 1_700_000_000
    paths = [find_env.touch(f"macai_find_test/驗收報告{c}.pdf", mtime=t - i) for i, c in enumerate("ABC")]
    find_env.mdfind_paths = paths
    out = r.process_voice_command("找驗收報告")  # 沒說範圍：聯合搜尋，本機 3 筆＋一筆 Google 搜尋（U6、U9，沒有 Brave 金鑰）
    (step,) = out["steps"]
    assert step["kind"] == "fast" and step["status"] == "ok"
    assert step["intent"]["domain"] == "file" and step["intent"]["slots"]["name"] == "驗收報告"
    assert step["choices"]["total"] == 4 and [i["kind"] for i in step["choices"]["items"]] == ["pdf", "pdf", "pdf", "web"]
    assert find_env.opens() == []
    out = r.process_voice_command("第二個")
    assert find_env.opens() == [["open", "-R", str(paths[1])]]
    assert out["steps"][0]["status"] == "ok" and out["steps"][0]["choices"]["closed"] is True
    out = r.process_voice_command("第二個")  # 選過就失效
    assert out["steps"][0]["logs"] == ["⚠️ 沒有可選的清單（已過期或尚未搜尋）"]
    assert len(find_env.opens()) == 1


def test_pipeline_not_found_with_local_evidence_opens_nothing(find_env):
    out = r.process_voice_command("在電腦裡找不存在的檔名xyz123")
    assert find_env.opens() == []  # 沒有 open：不開 Google、不開 Finder
    (step,) = out["steps"]
    assert step["status"] == "exec_failed" and "本機沒有" in step["logs"][0] and "escalated" not in step


def test_pipeline_dangerous_open_only_reveals(find_env):
    script = find_env.touch("macai_find_test/驗收腳本.command", mode=0o755)
    find_env.mdfind_paths = [script]
    out = r.process_voice_command("打開驗收腳本.command")
    assert find_env.opens() == [["open", "-R", str(script)]]
    assert "可執行檔" in out["steps"][0]["logs"][0]


def test_pipeline_folder_and_unrelated_commands(find_env):
    (find_env.home / "Downloads").mkdir()
    r.process_voice_command("打開下載資料夾")
    assert find_env.opens() == [["open", str(find_env.home / "Downloads")]]
    assert find_env.mdfinds() == []  # 資料夾不需要搜尋
