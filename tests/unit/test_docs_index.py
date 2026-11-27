"""ADR 索引與指南的引用檢查（ADR 0031；計畫 docs/plans/completed/2026-11-27-docs-index.md 的 B4、B5）。

- 索引（docs/decisions/README.md）：每份 ADR 剛好一列；標題、狀態、被修改／取代和檔案一致；強制方式引用的規則、契約、檔案都存在。
- AGENTS.md 紅線的〔擋：…〕和索引用同一套引用寫法。
- AGENTS.md、testing.md、coding-style.md、索引裡的 repo 路徑與相對連結都存在。

每項檢查都在現有文件上跑一次（必須 0 個問題），再用寫在這裡的片段確認反例會被抓到。
"""
import configparser
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "docs" / "decisions"
INDEX = DECISIONS / "README.md"
GUIDES = [ROOT / "AGENTS.md", ROOT / "docs" / "guides" / "testing.md", ROOT / "docs" / "guides" / "coding-style.md"]


# ---------- ADR 檔案 ----------
_ADR_FILE_RE = re.compile(r"\d{4}-[a-z0-9-]+\.md")
_LINKED_ADR_RE = re.compile(r"\]\((\d{4})-[a-z0-9-]+\.md")


def read_adrs(folder: Path = DECISIONS) -> dict[str, dict]:
    """{編號: {file, title, status, links}}；status 是狀態行第一個詞（採用、棄用…），links 是狀態行連到的其他 ADR"""
    adrs = {}
    for p in sorted(folder.iterdir()):
        if not _ADR_FILE_RE.fullmatch(p.name):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        num = p.name[:4]
        title = lines[0].removeprefix(f"# {num} ")
        status_line = next(line for line in lines if line.startswith("- 狀態："))
        status = re.split(r"[（(]", status_line.removeprefix("- 狀態："), maxsplit=1)[0].strip()
        adrs[num] = {"file": p.name, "title": title, "status": status,
                     "links": set(_LINKED_ADR_RE.findall(status_line)) - {num}}
    return adrs


def modifiers(adrs: dict[str, dict]) -> dict[str, set[str]]:
    """被修改／取代：比自己新、而且兩邊任一邊的狀態行連到對方的 ADR"""
    out = {n: set() for n in adrs}
    for n, a in adrs.items():
        for m in a["links"]:
            if m in adrs:
                newer, older = max(n, m), min(n, m)
                out[older].add(newer)
    return out


# ---------- 引用（強制方式、〔擋：…〕） ----------
def _config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(ROOT / ".importlinter", encoding="utf-8")
    return cfg


_CFG = _config()
CONTRACTS = {_CFG[s]["name"].split()[0] for s in _CFG.sections() if s.startswith("importlinter:contract")}
NPM_SCRIPTS = set(json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))["scripts"])
_KINDS = {
    "rule": lambda v: (ROOT / "rules" / f"{v}.yml").is_file(),
    "eslint": lambda v: (ROOT / "desktop" / "eslint-rules" / f"{v}.js").is_file(),
    "contract": lambda v: v in CONTRACTS,
    "npm": lambda v: v in NPM_SCRIPTS,
    "test": lambda v: (ROOT / v).exists(),
    "script": lambda v: (ROOT / v).exists(),
}


def check_ref(token: str) -> str | None:
    kind, sep, value = token.partition(":")
    if not sep or kind not in _KINDS or not value:
        return f"看不懂的引用：{token!r}"
    if not _KINDS[kind](value):
        return f"引用不存在：{token}"
    return None


def check_refs(cell: str) -> list[str]:
    """一格裡的引用（反引號包起來、頓號分隔）；— 表示沒有"""
    if cell.strip() == "—":
        return []
    tokens = re.findall(r"`([^`]+)`", cell)
    if not tokens:
        return [f"強制方式要用反引號寫引用或寫 —：{cell!r}"]
    return [e for t in tokens if (e := check_ref(t))]


# ---------- 索引 ----------
_ROW_RE = re.compile(r"^\| \[(\d{4})\]\(([^)]+)\) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$")


def parse_index(text: str) -> list[tuple[str, ...]]:
    return [m.groups() for line in text.splitlines() if (m := _ROW_RE.match(line))]


def check_index(text: str, adrs: dict[str, dict]) -> list[str]:
    errors = []
    rows = parse_index(text)
    seen = [r[0] for r in rows]
    mods = modifiers(adrs)
    for num in sorted(set(seen) - set(adrs)):
        errors.append(f"{num}：索引有、檔案沒有")
    for num in sorted(set(adrs) - set(seen)):
        errors.append(f"{num}：檔案有、索引沒有")
    for num in sorted({n for n in seen if seen.count(n) > 1}):
        errors.append(f"{num}：索引重複")
    for num, link, title, status, _domain, mod_cell, refs in rows:
        a = adrs.get(num)
        if a is None:
            continue
        if link != a["file"]:
            errors.append(f"{num}：連結 {link} 應為 {a['file']}")
        if title != a["title"]:
            errors.append(f"{num}：標題 {title!r} 與檔案 {a['title']!r} 不一致")
        if status != a["status"]:
            errors.append(f"{num}：狀態 {status!r} 與檔案 {a['status']!r} 不一致")
        listed = set() if mod_cell.strip() == "—" else set(re.findall(r"\d{4}", mod_cell))
        if listed != mods[num]:
            errors.append(f"{num}：被修改／取代 {sorted(listed)} 與狀態行 {sorted(mods[num])} 不一致")
        errors += [f"{num}：{e}" for e in check_refs(refs)]
    return errors


# ---------- AGENTS.md 紅線的〔擋：…〕 ----------
def check_guards(text: str) -> list[str]:
    errors = []
    for cell in re.findall(r"〔擋：(.*?)〕", text):
        if cell == "…":  # 標題的說明文字
            continue
        errors += check_refs(cell)
    return errors


# ---------- repo 路徑與相對連結 ----------
_PATH_PREFIXES = ("tests/", "mac_ai/", "scripts/", "docs/", "desktop/", "rules/", "rule-tests/", "bench/",
                  "vocab/", ".github/")
_ALIASES = {"router/": "mac_ai/router/", "stt/": "mac_ai/stt/"}  # testing.md 對照表的簡寫
_UNTRACKED = ("AI_models/", "Rushia_hd/", "desktop/vendor/", "desktop/node_modules/", "logs/", "data/",
              "bench/synth/", "macos-stt/")
_PLACEHOLDER = re.compile(r"[<>*{}]|NNNN|YYYY")
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _repo_path(token: str) -> str | None:
    token = token.split("::")[0].rstrip("，。、；：）)")
    for short, full in _ALIASES.items():
        if token.startswith(short):
            token = full + token[len(short):]
    if not token.startswith(_PATH_PREFIXES) or token.startswith(_UNTRACKED) or _PLACEHOLDER.search(token):
        return None
    return token


def check_paths(text: str, base: Path) -> list[str]:
    """反引號裡的 repo 路徑（從 repo 根目錄算）與 Markdown 相對連結（從檔案所在目錄算）都要存在"""
    errors = []
    for span in re.findall(r"`([^`\n]+)`", text):
        for word in span.split():
            if (path := _repo_path(word)) and not (ROOT / path).exists():
                errors.append(f"路徑不存在：{path}")
    for target in _LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        if not (base / target.split("#")[0]).exists():
            errors.append(f"連結不存在：{target}")
    return errors


# ---------- 在現有文件上 ----------
def test_index_matches_adr_files():
    adrs = read_adrs()
    assert len(adrs) >= 31
    assert len(parse_index(INDEX.read_text(encoding="utf-8"))) == len(adrs)  # 每一列都有被讀到
    assert check_index(INDEX.read_text(encoding="utf-8"), adrs) == []


def test_agents_guards_exist():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert len(re.findall(r"〔擋：", text)) > 10  # 真的有被讀到
    assert check_guards(text) == []


@pytest.mark.parametrize("doc", [*GUIDES, INDEX], ids=lambda p: p.name)
def test_doc_paths_and_links_exist(doc):
    assert check_paths(doc.read_text(encoding="utf-8"), doc.parent) == []


# ---------- 反例 ----------
def _fake_adrs() -> dict[str, dict]:
    return {
        "0001": {"file": "0001-a.md", "title": "甲", "status": "採用", "links": {"0002"}},
        "0002": {"file": "0002-b.md", "title": "乙", "status": "採用", "links": set()},
        "0003": {"file": "0003-c.md", "title": "丙", "status": "採用", "links": {"0001"}},
    }


GOOD_INDEX = """| 編號 | 標題 | 狀態 | 領域 | 被修改／取代 | 強制方式 |
|---|---|---|---|---|---|
| [0001](0001-a.md) | 甲 | 採用 | 路由 | 0002、0003 | `rule:hot-path-no-io`、`contract:C4` |
| [0002](0002-b.md) | 乙 | 採用 | 路由 | — | `test:tests/unit/test_tools.py` |
| [0003](0003-c.md) | 丙 | 採用 | 文件 | — | — |
"""


def test_good_index_passes():
    assert check_index(GOOD_INDEX, _fake_adrs()) == []


@pytest.mark.parametrize("old, new, expect", [
    ("| [0003](0003-c.md) | 丙 | 採用 | 文件 | — | — |\n", "", "檔案有、索引沒有"),
    ("| 丙 |", "| 丁 |", "標題"),
    ("| 乙 | 採用 |", "| 乙 | 棄用 |", "狀態"),
    ("| 0002、0003 |", "| 0003 |", "被修改／取代"),
    ("(0002-b.md)", "(0002-x.md)", "連結"),
    ("`rule:hot-path-no-io`", "`rule:no-such-rule`", "引用不存在"),
    ("`contract:C4`", "`contract:C9`", "引用不存在"),
    ("`test:tests/unit/test_tools.py`", "`test:tests/unit/test_nope.py`", "引用不存在"),
    ("`test:tests/unit/test_tools.py`", "`lint:x`", "看不懂的引用"),
    ("| 文件 | — | — |", "| 文件 | — | 只靠文字 |", "反引號"),
])
def test_index_counterexamples(old, new, expect):
    bad = GOOD_INDEX.replace(old, new, 1)
    assert bad != GOOD_INDEX and any(expect in e for e in check_index(bad, _fake_adrs()))


def test_duplicate_and_unknown_rows():
    extra = GOOD_INDEX + "| [0002](0002-b.md) | 乙 | 採用 | 路由 | — | — |\n| [0009](0009-z.md) | 庚 | 採用 | 路由 | — | — |\n"
    errors = check_index(extra, _fake_adrs())
    assert any("索引重複" in e for e in errors) and any("0009：索引有、檔案沒有" in e for e in errors)


def test_guard_counterexamples():
    assert check_guards("1. 規則〔擋：`rule:hot-path-no-io`、`eslint:no-window-show`〕") == []
    assert check_guards("1. 規則〔擋：`rule:missing-rule`〕")
    assert check_guards("1. 規則〔擋：沒有反引號〕")


@pytest.mark.parametrize("text", [
    "跑 `pytest tests/unit/test_no_such_file.py`",
    "改 `router/no_such_module.py`",
    "見 [計畫](../plans/completed/no-such-plan.md)",
    "`tests/unit/test_nope.py::test_x`",
])
def test_path_counterexamples(text):
    assert check_paths(text, ROOT / "docs" / "guides")


@pytest.mark.parametrize("text", [
    "`desktop/test/<同名或相關>.test.js`、`rules/<id>.yml`、`docs/decisions/NNNN-*.md`",
    "不入版控：`AI_models/`、`data/`、`logs/`",
    "`tests/unit/test_tools.py::test_new_tools_in_schema`、`router/fast_path.py`",
    "見 [索引](../decisions/README.md#adr-索引)、[外部](https://example.com)",
])
def test_path_examples_that_pass(text):
    assert check_paths(text, ROOT / "docs" / "guides") == []
