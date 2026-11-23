"""第一層範圍判斷（domain.py 純函式）、Intent 層的 Fast 比對、兩層分離、網頁查詢用原話、副檔名分級"""
import ast
from pathlib import Path

import pytest

from mac_ai.router.domain import parse_find, resolve_intent
from mac_ai.router.fast_path import FastPathMatcher, canonicalize_clause
from mac_ai.router.lexicon import _compact, resolve_app, resolve_project

ROUTER_DIR = Path(__file__).resolve().parents[2] / "mac_ai" / "router"


class FakeCatalog:
    """已安裝：Xcode、Calculator（計算機）、Terminal（終端機）、Photos（照片）、Music（音樂）、Mail（郵件）；
    加上詞庫 App 與專案（mac_ai_control）"""
    installed = {"xcode": "Xcode", "計算機": "Calculator", "終端機": "Terminal", "photos": "Photos", "照片": "Photos",
                 "music": "Music", "音樂": "Music", "mail": "Mail", "郵件": "Mail"}

    def resolve_app(self, token, candidates=False):
        hit = resolve_app(token, candidates)
        if hit is not None and hit[1] == "exact":
            return hit
        name = self.installed.get(_compact(token))
        return (name, "exact", "") if name else hit

    def resolve_project(self, token, candidates=False):
        return resolve_project(token, candidates)


def intent_of(utterance):
    req = parse_find(canonicalize_clause(utterance))
    return resolve_intent(req, FakeCatalog()) if req else None


# (語句, (domain, 動詞類別, slots 的關鍵欄位)) ；None＝第一層判斷不出範圍
DOMAIN_CASES = [
    ("找一下 Xcode", ("app", "find", {"app": "Xcode"})),
    ("打開 Xcode", ("app", "open", {"app": "Xcode"})),
    ("搜尋 Xcode", ("web", "search", {"query": "Xcode"})),
    ("搜尋 Xcode 這個 App", ("app", "search", {"app": "Xcode"})),
    ("在電腦裡搜尋 Xcode", ("app", "search", {"app": "Xcode"})),
    ("查一下 mac_ai_control 專案", ("project", "search", {"project_name": "mac_ai_control"})),
    ("找一下終端機", ("app", "find", {"app": "Terminal"})),
    ("打開計算機", ("app", "open", {"app": "Calculator"})),
    ("打開下載資料夾", ("folder", "open", {"path_key": "Downloads"})),
    ("找桌面", ("folder", "find", {"path_key": "Desktop"})),
    ("找文件資料夾", ("folder", "find", {"path_key": "Documents"})),
    ("找應用程式", ("folder", "find", {"path_key": "Applications"})),
    ("幫我找一下台北天氣", ("web", "find", {"query": "台北天氣"})),
    ("網路上找 Xcode", ("web", "find", {"query": "Xcode"})),
    ("打開 Photoshop", None),
    ("打開 github.com", None),
    ("找 report.pdf", ("file", "find", {"name": "report", "kinds": ["pdf"], "filename": "report.pdf", "local_only": True})),
    ("找昨天下載的 PDF", ("file", "find", {"name": "", "kinds": ["pdf"], "since": "yesterday", "location": "Downloads"})),
    ("找履歷", ("file", "find", {"name": "履歷", "kinds": [], "local_only": False})),
    ("找照片", ("file", "find", {"name": "", "kinds": ["image"], "local_only": True})),
    ("打開照片", ("app", "open", {"app": "Photos"})),
    ("打開履歷.pdf", ("file", "open", {"name": "履歷", "kinds": ["pdf"], "filename": "履歷.pdf"})),
    ("找桌面上的截圖", ("file", "find", {"kinds": ["image"], "location": "Desktop"})),
    ("在電腦裡找 python 教學", ("file", "find", {"name": "python 教學"})),
    ("找 python 教學", ("file", "find", {"name": "python 教學", "local_only": False})),
    ("搜尋 report.pdf", ("file", "search", {"name": "report", "kinds": ["pdf"]})),
    ("搜尋最近的新聞", ("web", "search", {})),
    ("找今天的新聞", ("web", "find", {})),
    ("欸你幫我在電腦裡面找那個叫做履歷的檔案", ("file", "find", {"name": "履歷"})),
    ("幫我把那個 Xcode 找出來", ("app", "find", {"app": "Xcode"})),
    ("找驗收腳本.command", ("file", "find", {"name": "驗收腳本.command", "filename": "驗收腳本.command"})),
    ("找 mac_ai_control 資料夾", ("file", "find", {"name": "mac_ai_control", "kinds": ["folder"]})),
    ("找最近的檔案", ("file", "find", {"since": "recent"})),
    ("我昨天找過了", None),
    # 本機證據：有證據 → local_only（0 筆回『本機沒有』）；沒有 → 0 筆轉交模型
    ("找下載的履歷", ("file", "find", {"name": "履歷", "location": "Downloads", "local_only": True})),
    ("在電腦裡找履歷", ("file", "find", {"local_only": True})),
    # 例外句：交給模型判斷
    ("找到了", None),
    ("找不到我的鑰匙", None),
    ("找得到嗎", None),
    ("找最近的電影", None),
    ("找影片教學", None),
    ("找今天的會議", None),
]


@pytest.mark.parametrize("utterance,expected", DOMAIN_CASES, ids=[c[0] for c in DOMAIN_CASES])
def test_domain_table(utterance, expected):
    intent = intent_of(utterance)
    if expected is None:
        assert intent is None
        return
    domain, verb, slots = expected
    assert intent is not None, "第一層判斷不出範圍"
    assert (intent.domain, intent.verb) == (domain, verb)
    for key, value in slots.items():
        assert intent.slots[key] == value, f"slots[{key!r}]"


def test_domain_source_explains_the_decision():
    assert intent_of("網路上找 Xcode").source == "scope_word"
    assert intent_of("找一下 Xcode").source == "catalog"
    assert intent_of("幫我找一下台北天氣").source == "realtime"
    assert intent_of("找 report.pdf").source == "file_hint"
    assert intent_of("找履歷").source == "default"


def test_search_without_scope_word_stays_web_but_scope_words_go_local():
    assert intent_of("搜尋 Xcode").domain == "web"
    assert intent_of("搜尋最近的新聞").domain == "web"  # 『最近』只對『找／打開』算檔案時間
    assert intent_of("搜尋 PDF 教學").domain == "web"
    assert intent_of("搜尋 report.pdf").domain == "file"  # 副檔名就是範圍提示
    assert intent_of("在電腦裡搜尋 Xcode").domain == "app"


def test_open_with_unknown_target_is_left_to_llm():
    assert intent_of("打開 Photoshop") is None
    assert intent_of("開始錄音") is None


def test_file_without_any_condition_is_not_matched():
    assert intent_of("找那個") is None
    assert intent_of("找檔案") is None


# ---------- Fast Path：實際會執行的動作（含守門規則）----------
def APP(name):
    return [("control_mac_app", {"app_name": name, "action": "open"})]


def FIND(**args):
    return [("find_file", args)]


MATCH_CASES = [
    ("找一下 Xcode", APP("Xcode")),
    ("打開 Xcode", APP("Xcode")),
    ("搜尋 Xcode", [("federated_search", {"query": "Xcode"})]),  # 沒說範圍：聯合搜尋（U5），執行時前景停在詞庫網站照舊站內搜尋
    ("搜尋 Xcode 這個 App", APP("Xcode")),
    ("在電腦裡搜尋 Xcode", APP("Xcode")),
    ("查一下 mac_ai_control 專案", [("open_project", {"project_name": "mac_ai_control"})]),
    ("找一下終端機", APP("Terminal")),
    ("打開計算機", APP("Calculator")),
    ("打開郵件", APP("Mail")),
    ("打開下載資料夾", [("open_folder", {"path_key": "Downloads"})]),
    ("找桌面", [("open_folder", {"path_key": "Desktop"})]),
    ("幫我找一下最近的新聞", [("web_answer", {"query": "最近的新聞"})]),  # 會變的事實直接查證（U12）
    ("網路上找 Xcode", [("web_search_or_open_url", {"query": "Xcode"})]),
    ("搜尋最近的新聞", [("web_answer", {"query": "最近的新聞"})]),
    ("找 report.pdf", FIND(name="report", kinds=["pdf"], filename="report.pdf", local_only=True, action="reveal")),
    ("找昨天下載的 PDF", FIND(kinds=["pdf"], since="yesterday", location="Downloads", local_only=True, action="reveal")),
    ("找履歷", [("federated_search", {"query": "履歷"})]),  # 沒有本機證據：聯合搜尋（U6）
    ("找我的履歷", FIND(name="履歷", action="reveal")),
    ("找照片", FIND(kinds=["image"], local_only=True, action="reveal")),
    ("打開照片", APP("Photos")),
    ("打開履歷.pdf", FIND(name="履歷", kinds=["pdf"], filename="履歷.pdf", local_only=True, action="open")),
    ("搜尋 report.pdf", FIND(name="report", kinds=["pdf"], filename="report.pdf", local_only=True, action="reveal")),
    ("找桌面上的截圖", FIND(kinds=["image"], location="Desktop", local_only=True, action="reveal")),
    ("找 python 教學", [("federated_search", {"query": "python 教學"})]),  # 沒有本機證據：聯合搜尋（U6）
    ("找我的 python 教學", FIND(name="python 教學", action="reveal")),  # 『我的』：找檔案，不帶 local_only，0 筆會轉交模型
    ("第二個", [("pick_choice", {"n": 2})]),
    ("打開第三個", [("pick_choice", {"n": 3, "action": "open"})]),
    ("在 Finder 顯示第一個", [("pick_choice", {"n": 1, "action": "reveal"})]),
    ("顯示第五個", [("pick_choice", {"n": 5, "action": "reveal"})]),
    ("最後一個", [("pick_choice", {"n": -1})]),
    ("第三個打開", [("pick_choice", {"n": 3, "action": "open"})]),
    # 守門：否定、敘述、提問、超出 1–5、其他規則不受影響
    ("不要找 Xcode", None),
    ("我昨天找過了", None),
    ("找昨天下載的檔案了", None),
    ("找得到嗎", None),
    ("找什麼檔案", None),
    ("打開 Photoshop", None),
    ("第六個", None),
    # 例外句：交給模型，不搜檔案
    ("找到了", None),
    ("找不到我的鑰匙", None),
    ("找最近的電影", None),
    ("找影片教學", None),
    ("找今天的會議", None),
    ("找 pdf 轉 word 的方法", None),
    ("第二", [("pick_choice", {"n": 2})]),
    ("下一個分頁", [("browser_tab", {"action": "next"})]),
    ("打開深色模式", [("system_power_action", {"action": "dark_mode_on"})]),
    ("打開新分頁", [("front_action", {"id": "terminal_new_tab", "fallback": [["browser_tab", {"action": "new"}]]})]),  # 前景是終端機才開它的分頁
]


@pytest.mark.parametrize("utterance,expected", MATCH_CASES, ids=[c[0] for c in MATCH_CASES])
def test_fast_match_actions(utterance, expected):
    m = FastPathMatcher(catalog=FakeCatalog()).match(utterance)
    assert (m.actions if m else None) == expected


def test_match_records_intent_for_tracing():
    m = FastPathMatcher(catalog=FakeCatalog()).match("找昨天下載的 PDF")
    assert m.intent.to_log() == {
        "domain": "file", "verb": "find", "source": "file_hint", "query": "昨天下載的 PDF",
        "slots": {"name": "", "kinds": ["pdf"], "since": "yesterday", "location": "Downloads", "filename": None,
                  "local_only": True}}
    assert FastPathMatcher(catalog=FakeCatalog()).match("打開 Xcode").intent.domain == "app"


def test_yesterday_exemption_only_for_find_verb_without_le():
    matcher = FastPathMatcher(catalog=FakeCatalog())
    assert matcher.match("找昨天下載的 PDF") is not None
    assert matcher.match("我昨天找過了") is None
    assert matcher.match("找昨天下載的 PDF 了") is None
    assert matcher.match("前天找昨天下載的 PDF") is None  # 『前天』仍是敘述詞


def test_match_does_not_scan_apps_for_unrelated_commands(monkeypatch):
    """音量、亮度等不經過 App 清單：不該為了它們去跑 mdfind"""
    from mac_ai.router import catalog

    def boom():
        raise AssertionError("不該掃描 App 清單")

    monkeypatch.setattr(catalog, "_scan_installed", boom)
    matcher = FastPathMatcher()
    for text in ("把音量調到50", "靜音", "螢幕調暗一點", "打開深色模式", "鎖定螢幕", "找桌面", "網路上找 Xcode"):
        assert matcher.match(text) is not None, text


# ---------- A2：兩層分離 ----------
def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def test_domain_layer_has_no_execution_dependencies():
    names = _imports(ROUTER_DIR / "domain.py")
    forbidden = {"subprocess", "os", "mac_ai.router.actions", "mac_ai.router.local_actions", "mac_ai.router.catalog",
                 "mac_ai.router.choices"}
    assert not names & forbidden, names & forbidden
    calls = {ast.unparse(n.func) for n in ast.walk(ast.parse((ROUTER_DIR / "domain.py").read_text(encoding="utf-8")))
             if isinstance(n, ast.Call)}
    assert not {c for c in calls if c.startswith(("os.", "subprocess."))}


def test_action_layer_does_not_read_the_utterance():
    """第二層只吃 Intent：local_actions 不 import 句子解析（parse_find、canonicalize_clause、FastPathMatcher）"""
    tree = ast.parse((ROUTER_DIR / "local_actions.py").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not imported & {"parse_find", "resolve_intent", "canonicalize_clause", "FastPathMatcher"}


# ---------- 網頁查詢：用使用者的原話（只去頭尾的指令詞與指示詞）----------
QUERY_CASES = [
    ("幫我找一下 python 教學", "python 教學"),
    ("找附近的餐廳啦", "附近的餐廳"),
    ("我想找台中好吃的早午餐", "台中好吃的早午餐"),
    ("找個好看的電影看看", "好看的電影"),
    ("找我的最愛清單", "我的最愛清單"),  # 「我的」是內容詞，不能刪（檔名清理會刪，查詢不會）
    ("找那個叫做進擊的巨人的動畫", "進擊的巨人的動畫"),
    ("找 C++ 教學", "C++ 教學"),
    ("找 ASP.NET 教學", "ASP.NET 教學"),
    ("找 iphone 15 的價格", "iphone 15 的價格"),
    ("找五十嵐菜單", "五十嵐菜單"),
    ("找一個好看的電影", "好看的電影"),
    ("找下載速度測試", "下載速度測試"),
    ("找桌面桌布", "桌面桌布"),
    ("找 Taylor Swift 的歌", "Taylor Swift 的歌"),
]


@pytest.mark.parametrize("utterance,expected", QUERY_CASES, ids=[c[0] for c in QUERY_CASES])
def test_query_is_the_users_own_words(utterance, expected):
    from mac_ai.router.normalize import normalize_utterance
    spoken = normalize_utterance(utterance)
    req = parse_find(canonicalize_clause(spoken))
    assert req.query == expected
    compact = lambda t: "".join(t.split()).casefold()
    assert compact(req.query) in compact(spoken)  # 不變式：查詢一定是原話的一段，一個字都不能是別人加的
    intent = resolve_intent(req, FakeCatalog())
    assert intent.domain == "file" and intent.query == expected and not intent.slots["local_only"]


def test_query_invariant_holds_for_every_find_case_in_the_tables():
    """表格裡所有『找』的句子：查詢都是原話的子字串（含需要清檔名的、有本機證據的）"""
    from mac_ai.router.normalize import normalize_utterance
    compact = lambda t: "".join(t.split()).casefold()
    checked = 0
    for utterance, _ in DOMAIN_CASES + [(c[0], None) for c in MATCH_CASES]:
        spoken = normalize_utterance(utterance)
        req = parse_find(canonicalize_clause(spoken))
        if req is None:
            continue
        assert req.query and compact(req.query) in compact(spoken), utterance
        checked += 1
    assert checked > 40


def test_query_extraction_cost_is_recorded_and_tiny():
    """運算時間：截取＋範圍判斷每句的耗時（實際數字寫進計畫書；這裡的門檻很寬，只擋數量級的退步）"""
    import statistics
    import time
    clause = canonicalize_clause("幫我找一下 python 教學")
    catalog_ = FakeCatalog()
    samples = []
    for _ in range(300):
        t = time.perf_counter()
        resolve_intent(parse_find(clause), catalog_)
        samples.append((time.perf_counter() - t) * 1000)
    median, p95 = statistics.median(samples), sorted(samples)[int(len(samples) * 0.95)]
    print(f"\n範圍判斷＋截取：中位 {median * 1000:.0f} µs，p95 {p95 * 1000:.0f} µs")
    assert median < 2.0 and p95 < 10.0  # ms


# ---------- 副檔名分級：已知文件格式才算確定的本機證據 ----------
EXT_CASES = [
    ("找 node.js", ("file", "find", {"local_only": False})),  # .js 也可能是網站名稱
    ("找 vue.js 教學", ("file", "find", {"local_only": False})),
    ("搜尋 node.js", ("web", "search", {})),
    ("找 report.pdf", ("file", "find", {"local_only": True})),
    ("搜尋 report.pdf", ("file", "search", {"local_only": True})),
    ("打開驗收腳本.command", ("file", "open", {"local_only": False, "filename": "驗收腳本.command"})),
    ("找驗收腳本.command", ("file", "find", {"local_only": False})),
    ("找 run.sh", ("file", "find", {"local_only": False})),
    ("找 notes.txt", ("file", "find", {"local_only": True})),
    ("找 photo.png", ("file", "find", {"local_only": True})),
]


@pytest.mark.parametrize("utterance,expected", EXT_CASES, ids=[c[0] for c in EXT_CASES])
def test_ext_grading(utterance, expected):
    intent = intent_of(utterance)
    domain, verb, slots = expected
    assert (intent.domain, intent.verb) == (domain, verb)
    for key, value in slots.items():
        assert intent.slots[key] == value


def test_ext_grading_fast_path_actions():
    matcher = FastPathMatcher(catalog=FakeCatalog())
    assert matcher.match("搜尋 node.js").actions == [("federated_search", {"query": "node.js"})]
    assert matcher.match("找 node.js").actions == [("find_file", {"name": "node.js", "filename": "node.js", "action": "reveal"})]
