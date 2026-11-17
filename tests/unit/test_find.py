"""已安裝 App 清單（catalog）與檔案搜尋（mdfind 結果過濾、排序、安全規則）"""
import json
import subprocess

import pytest

import mac_ai.router as r
from mac_ai.router import actions, catalog, choices, local_actions as la
from mac_ai.router.actions import ExecContext
from support.clock import Clock

REAL_SCAN = catalog._scan_installed  # conftest 的 autouse fixture 會把它換成空清單，這裡要測真的解析邏輯


# ---------- A3：App 清單 ----------
MDFIND_APPS = "\n".join([
    "/Applications/Safari.app   kMDItemDisplayName = Safari",
    "/System/Applications/Calculator.app   kMDItemDisplayName = 計算機",
    "/System/Applications/Photos.app   kMDItemDisplayName = 照片",
    "/Applications/Foo.app/Contents/Helpers/Helper.app   kMDItemDisplayName = Helper",
    "/Applications/Weird App.app   kMDItemDisplayName = (null)",
    "/Applications/NotAnApp.dmg   kMDItemDisplayName = NotAnApp",
])


def test_scan_installed_parses_mdfind_and_skips_nested_apps(monkeypatch):
    seen = []

    def fake_run(cmd, timeout=None):
        seen.append(cmd)
        return True, MDFIND_APPS

    monkeypatch.setattr(actions, "_run", fake_run)
    index = REAL_SCAN()
    assert index == {"safari": "Safari", "calculator": "Calculator", "計算機": "Calculator", "photos": "Photos",
                     "照片": "Photos", "weirdapp": "Weird App"}  # 巢狀 Helper.app 不算；(null) 不當名稱；.dmg 不算
    (cmd,) = seen
    assert cmd[0] == "mdfind" and cmd.count("-onlyin") == 3 and cmd[-2:] == ["-attr", "kMDItemDisplayName"]
    assert 'kMDItemContentType == "com.apple.application-bundle"' in cmd


def test_scan_installed_returns_empty_when_mdfind_fails(monkeypatch):
    monkeypatch.setattr(actions, "_run", lambda cmd, timeout=None: (False, "boom"))
    assert REAL_SCAN() == {}


def test_catalog_resolves_lexicon_first_then_installed():
    cat = catalog.Catalog(scan=lambda: {"計算機": "Calculator", "photos": "Photos", "照片": "Photos"})
    assert cat.resolve_app("chrome") == ("Google Chrome", "exact", "")  # 詞庫別名
    assert cat.resolve_app("照片") == ("Photos", "exact", "")  # 已安裝（系統語言名稱）
    assert cat.resolve_app("Photos")[0] == "Photos"
    assert cat.resolve_app("photoshop") is None


def test_catalog_rescans_at_most_once_per_interval():
    now = Clock(1000.0)
    scans = []

    def scan():
        scans.append(now.now)
        return {"xcode": "Xcode"} if len(scans) > 1 else {}

    cat = catalog.Catalog(scan=scan, clock=now, refresh_s=60)
    assert cat.resolve_app("xcode") is None and len(scans) == 1  # 第一次用到才掃描
    cat.resolve_app("nothing"), cat.resolve_app("nothing")
    assert len(scans) == 1  # 查不到不會自己重掃（比對可能發生在收音執行緒）
    now.now += 59
    cat.refresh_if_stale()
    assert len(scans) == 1
    now.now += 2
    cat.refresh_if_stale()  # 指令開始時才檢查；超過 60 秒就重掃，啟動後才安裝的 App 這時查得到
    assert len(scans) == 2 and cat.resolve_app("xcode") == ("Xcode", "exact", "")


def test_open_installed_app_by_display_name(sh, installed_apps):
    installed_apps({"郵件": "Mail", "mail": "Mail"})
    out = r.process_voice_command("打開郵件")
    assert sh == [["open", "-a", "Mail"]]
    assert out["steps"][0]["intent"]["domain"] == "app" and out["steps"][0]["status"] == "ok"


def test_llm_cannot_open_app_that_is_not_installed():
    calls, err = r.validate_tool_call("control_mac_app", {"app_name": "Photoshop", "action": "open"})
    assert calls is None and "找不到 App：Photoshop（沒有安裝）" in err
    assert r.validate_tool_call("control_mac_app", {"app_name": "Safari", "action": "open"})[0]["app_name"] == "Safari"


def test_llm_app_name_is_normalized_to_installed_name(installed_apps):
    installed_apps({"計算機": "Calculator", "calculator": "Calculator", "郵件": "Mail", "mail": "Mail"})
    assert r.validate_tool_call("control_mac_app", {"app_name": "郵件", "action": "open"})[0] == {"app_name": "Mail", "action": "open"}


# ---------- A4：檔案搜尋 ----------
def test_predicate_is_exact_and_escapes_user_text():
    assert la.build_predicate("驗收報告") == 'kMDItemFSName == "*驗收報告*"cd'
    assert la.build_predicate('a"b*c?d') == 'kMDItemFSName == "*a\\"bcd*"cd'  # " 跳脫、* ? 拿掉（不讓使用者字串變成萬用字元）
    assert la.build_predicate("a\\b") == 'kMDItemFSName == "*a\\\\b*"cd'
    assert la.build_predicate("python 教學") == 'kMDItemFSName == "*python*"cd && kMDItemFSName == "*教學*"cd'
    assert la.build_predicate("驗收", ["pdf"], "yesterday") == (
        'kMDItemFSName == "*驗收*"cd && (kMDItemContentTypeTree == "com.adobe.pdf") && '
        'kMDItemFSContentChangeDate >= $time.today(-1) && kMDItemFSContentChangeDate < $time.today')
    assert la.build_predicate("", ["image"]) == '(kMDItemContentTypeTree == "public.image")'
    assert la.build_predicate("", [], "recent") == "kMDItemFSContentChangeDate >= $time.today(-7)"
    assert la.build_predicate("") == 'kMDItemFSName == "*"'
    assert "||" in la.build_predicate("", ["doc"])  # 一種類型有多個 UTI：OR


def test_mdfind_is_called_as_list_with_onlyin_home(find_env):
    la.find_file(name="報告", ctx=ExecContext())
    assert find_env.mdfinds() == [["mdfind", "-onlyin", str(find_env.home), la.build_predicate("報告")]]


def test_location_narrows_the_search_folder(find_env):
    (find_env.home / "Downloads").mkdir()
    la.find_file(name="報告", location="Downloads", ctx=ExecContext())
    assert find_env.mdfinds()[0][2] == str(find_env.home / "Downloads")
    out = la.find_file(name="報告", location="Desktop", ctx=ExecContext())  # 資料夾不存在
    assert out.startswith("❌") and len(find_env.mdfinds()) == 1


def test_results_are_filtered(find_env, tmp_path):
    good = find_env.touch("docs/驗收A.pdf")
    bad = [
        find_env.touch("Library/Caches/x.pdf"),
        find_env.touch("proj/node_modules/x/y.pdf"),
        find_env.touch("proj/.git/objects/zz"),
        find_env.touch(".config/a.txt"),
        find_env.touch("proj/.venv/lib/site-packages/a.pdf"),
        find_env.touch("proj/__pycache__/a.pyc"),
        find_env.touch("Apps/Foo.app/Contents/Resources/a.pdf"),  # 同時建出 Foo.app 這個資料夾
        find_env.touch("Apps/Bar.framework/a.pdf"),
        find_env.touch("Apps/Baz.bundle/a.pdf"),
        "/etc/hosts",
    ]
    app_itself = find_env.home / "Apps" / "Foo.app"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("x")
    (find_env.home / "link").symlink_to(outside)
    bad.append(find_env.home / "link" / "a.txt")  # 家目錄底下的符號連結指到家目錄外
    kept = la._filter_results([str(p) for p in [good, app_itself, *bad]], str(find_env.home))
    assert kept == [str(good), str(app_itself)]  # .app 本身可以被找到（只顯示），裡面的檔案不行


def test_ranking_exact_then_prefix_then_contains_then_newest(find_env):
    t = 1_700_000_000
    exact_new = find_env.touch("a/report.txt", mtime=t + 300)
    exact_old = find_env.touch("a/report.pdf", mtime=t + 100)
    prefix = find_env.touch("a/report-final.txt", mtime=t + 900)
    contains_new = find_env.touch("a/my report.txt", mtime=t + 800)
    contains_old = find_env.touch("a/xreport.txt", mtime=t + 200)
    shuffled = [contains_old, prefix, exact_old, contains_new, exact_new]
    assert la._rank([str(p) for p in shuffled], "report", None) == [
        str(p) for p in (exact_new, exact_old, prefix, contains_new, contains_old)]


def test_empty_name_ranks_by_modified_time(find_env):
    t = 1_700_000_000
    a, b, c = (find_env.touch(f"a/{n}.pdf", mtime=t + i) for i, n in enumerate("abc"))
    assert la._rank([str(a), str(b), str(c)], "", None) == [str(c), str(b), str(a)]


def test_single_result_is_revealed_not_opened(find_env):
    path = find_env.touch("docs/驗收單一檔Z.pdf")
    find_env.mdfind_paths = [path]
    out = la.find_file(name="驗收單一檔Z", ctx=ExecContext())
    assert find_env.opens() == [["open", "-R", str(path)]]
    assert out == "✅ 已在 Finder 中顯示：驗收單一檔Z.pdf（~/docs）"


def test_single_result_with_open_opens_the_file(find_env):
    path = find_env.touch("docs/履歷.pdf")
    find_env.mdfind_paths = [path]
    out = la.find_file(name="履歷", action="open", ctx=ExecContext())
    assert find_env.opens() == [["open", str(path)]] and out.startswith("✅ 已開啟：履歷.pdf")


@pytest.mark.parametrize("rel,mode", [
    ("tools/驗收腳本.command", 0o755), ("tools/run.sh", 0o644), ("Apps/Foo.app", None), ("tools/installer.pkg", None),
    ("bin/mytool", 0o755),  # 沒有副檔名但有執行權限
])
def test_dangerous_files_are_only_revealed_even_when_asked_to_open(find_env, rel, mode):
    path = find_env.touch(rel, mode=mode)
    find_env.mdfind_paths = [path]
    out = la.find_file(name="x", action="open", ctx=ExecContext())
    assert find_env.opens() == [["open", "-R", str(path)]]
    assert "可執行檔，只在 Finder 中顯示" in out


def test_plain_file_without_extension_can_be_opened(find_env):
    path = find_env.touch("docs/README", mode=0o644)
    find_env.mdfind_paths = [path]
    la.find_file(name="README", action="open", ctx=ExecContext())
    assert find_env.opens() == [["open", str(path)]]


def test_zero_results_with_local_evidence_says_not_on_this_mac(find_env):
    ctx = ExecContext()
    out = la.find_file(name="不存在xyz123", local_only=True, ctx=ctx)
    assert out == "⚠️ 找不到「不存在xyz123」：本機沒有符合的檔案（範圍：家目錄，不含 ~/Library）"
    assert find_env.opens() == [] and ctx.escalate is None  # 有本機證據：不轉交模型


def test_zero_results_without_evidence_escalates_to_the_model(find_env):
    ctx = ExecContext()
    out = la.find_file(name="python 教學", ctx=ctx)
    assert out == "🔎 本機沒有符合「python 教學」的檔案（範圍：家目錄，不含 ~/Library），交給 AI 判斷"
    assert ctx.escalate == {"label": "python 教學", "scope": "家目錄，不含 ~/Library"} and find_env.opens() == []


def test_results_all_filtered_out_counts_as_not_found(find_env):
    find_env.mdfind_paths = [find_env.touch("Library/x.pdf"), find_env.touch("p/node_modules/y.pdf")]
    assert "找不到" in la.find_file(name="x", local_only=True, ctx=ExecContext()) and find_env.opens() == []


def test_mdfind_timeout_is_reported_not_raised(find_env):
    find_env.mdfind_error = subprocess.TimeoutExpired("mdfind", 3)
    ctx = ExecContext()
    out = la.find_file(name="x", ctx=ctx)
    assert out.startswith("❌ 檔案搜尋失敗") and find_env.opens() == []
    assert ctx.escalate is None  # 搜尋本身失敗：回報原因，不當成『沒有』轉交模型


def test_multiple_results_become_a_choice_list(find_env):
    t = 1_700_000_000
    paths = [find_env.touch(f"docs/驗收報告{c}.pdf", mtime=t - i) for i, c in enumerate("ABC")]
    find_env.mdfind_paths = paths
    ctx = ExecContext()
    ctx.step = {}
    out = la.find_file(name="驗收報告", ctx=ctx)
    assert find_env.opens() == []  # 多筆不執行任何動作
    assert out.startswith("✅ 找到 3 筆「驗收報告」") and "1. 驗收報告A.pdf（~/docs）" in out and "3. 驗收報告C.pdf（~/docs）" in out
    payload = ctx.step["choices"]
    assert [i["name"] for i in payload["items"]] == ["驗收報告A.pdf", "驗收報告B.pdf", "驗收報告C.pdf"]
    assert payload["total"] == 3 and payload["default_action"] == "reveal" and payload["query"] == "驗收報告"
    assert all(i["dir"] == "~/docs" and i["kind"] == "pdf" for i in payload["items"])
    assert str(find_env.home) not in json.dumps(payload, ensure_ascii=False)  # 完整路徑不送給 UI
    assert len(ctx.replies) == 1 and "找到 3 筆" in ctx.replies[0]
    assert choices.STORE.get().paths == [str(p) for p in paths]


def test_choice_list_is_capped_and_reports_total(find_env):
    t = 1_700_000_000
    find_env.mdfind_paths = [find_env.touch(f"d/報告{i}.txt", mtime=t + i) for i in range(7)]
    ctx = ExecContext()
    ctx.step = {}
    out = la.find_file(name="報告", ctx=ctx)
    assert "找到 7 筆" in out and "列出前 5 筆" in out
    assert len(ctx.step["choices"]["items"]) == 5 and ctx.step["choices"]["total"] == 7


def test_open_verb_makes_open_the_default_action_of_the_list(find_env):
    find_env.mdfind_paths = [find_env.touch("d/a.txt"), find_env.touch("d/b.txt")]
    ctx = ExecContext()
    ctx.step = {}
    la.find_file(name="x", action="open", ctx=ctx)
    assert ctx.step["choices"]["default_action"] == "open"


def test_full_filename_with_one_exact_match_needs_no_choice(find_env):
    a, b = find_env.touch("d/履歷.pdf"), find_env.touch("d/履歷表.pdf")
    find_env.mdfind_paths = [a, b]
    la.find_file(name="履歷", kinds=["pdf"], filename="履歷.pdf", action="open", ctx=ExecContext())
    assert find_env.opens() == [["open", str(a)]]


def test_new_search_closes_the_previous_list(find_env):
    find_env.mdfind_paths = [find_env.touch("d/a.txt"), find_env.touch("d/b.txt")]
    first = ExecContext()
    first.step = {}
    la.find_file(name="x", ctx=first)
    old_id = first.step["choices"]["list_id"]
    find_env.mdfind_paths = []
    second = ExecContext()
    second.step = {}
    la.find_file(name="nothing", ctx=second)
    assert second.step["choices"] == {"list_id": old_id, "closed": True} and choices.STORE.get() is None


def test_open_folder(find_env):
    (find_env.home / "Downloads").mkdir()
    assert la.open_folder("Downloads").startswith("✅ 已開啟資料夾")
    assert find_env.opens() == [["open", str(find_env.home / "Downloads")]]
    assert la.open_folder("Desktop").startswith("❌ 資料夾不存在") and len(find_env.opens()) == 1
    assert la.open_folder("NoSuchKey").startswith("❌")
