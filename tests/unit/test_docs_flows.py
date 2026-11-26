"""docs/flows/ 的格式與同步檢查（規範見 docs/flows/README.md，T1～T11）。

每項檢查都有兩種測試：在現有文件上跑一次（必須 0 個問題），以及用寫在這裡的 Markdown 片段確認正例通過、反例會被抓到。
"""
import ast
import configparser
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLOWS = ROOT / "docs" / "flows"
STRUCTURE_FILES = {"module-deps.md"}  # 結構檔：不套用章節規則（T7、T8）

DOC_FILES = sorted(p for p in FLOWS.glob("*.md") if p.name != "README.md")
FLOW_FILES = [p for p in DOC_FILES if p.name not in STRUCTURE_FILES]


def _config(text: str | None = None) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    if text is None:
        cfg.read(ROOT / ".importlinter", encoding="utf-8")
    else:
        cfg.read_string(text)
    return cfg


# ---------- Mermaid（T1～T5） ----------
_MERMAID_RE = re.compile(r"^```mermaid\n(.*?)^```", re.M | re.S)
_HEADERS = {"flowchart TD", "sequenceDiagram"}
_ID_RE = re.compile(r"[A-Za-z_]\w*")
_PASCAL_RE = re.compile(r"[A-Z][A-Za-z0-9]*")
_SHAPES = [  # 順序有關：[( 與 [[ 要在 [ 之前
    ("store", re.compile(r'\[\("([^"]*)"\)\]')),
    ("external", re.compile(r'\[\["([^"]*)"\]\]')),
    ("step", re.compile(r'\["([^"]*)"\]')),
    ("decision", re.compile(r'\{"([^"]*)"\}')),
]
_EDGE_RE = re.compile(r'\s*(?:-->|-- "(?P<solid>[^"]*)" -->|-\.->|-\. "(?P<dotted>[^"]*)" \.->)\s*')
_KEYWORDS = {"subgraph", "end", "classDef", "class", "style", "linkStyle", "click", "direction"}


def check_mermaid(text: str) -> list[tuple[str, str]]:
    """回傳 (檢查代號, 說明)；只看 flowchart，sequenceDiagram 只檢查 T1"""
    errors: list[tuple[str, str]] = []
    for block in _MERMAID_RE.findall(text):
        lines = block.splitlines()
        header = lines[0].strip() if lines else ""
        if header not in _HEADERS:
            errors.append(("T1", f"圖型不符：{header!r}"))
            continue
        if header == "sequenceDiagram":
            continue
        errors += _check_flowchart(lines[1:])
    return errors


def _check_flowchart(lines: list[str]) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    shapes: dict[str, str] = {}
    referenced: list[str] = []
    edges: list[tuple[str, str, str | None]] = []

    def node(line: str, pos: int) -> tuple[str | None, int]:
        m = _ID_RE.match(line, pos)
        if not m:
            errors.append(("T2", f"無法解析節點：{line[pos:]!r}"))
            return None, pos
        nid, pos = m.group(), m.end()
        if not _PASCAL_RE.fullmatch(nid):
            errors.append(("T2", f"節點 ID 不是 PascalCase：{nid}"))
        referenced.append(nid)
        for shape, rx in _SHAPES:
            s = rx.match(line, pos)
            if s:
                if nid in shapes:
                    errors.append(("T2", f"節點標籤定義兩次：{nid}"))
                shapes[nid] = shape
                return nid, s.end()
        if pos < len(line) and line[pos] in "[({>":
            errors.append(("T2", f"形狀不符或標籤沒加引號：{line[pos - len(nid):]!r}"))
            return None, pos
        return nid, pos

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if line.split()[0] in _KEYWORDS:
            errors.append(("T2", f"不用 subgraph／classDef／style：{line!r}"))
            continue
        prev, pos = node(line, 0)
        while prev is not None and pos < len(line):
            e = _EDGE_RE.match(line, pos)
            if not e:
                errors.append(("T3", f"不允許的邊或語法：{line[pos:]!r}"))
                break
            pos = e.end()
            if line.startswith("|", pos):
                errors.append(("T3", f"不用 -->|條件| 寫法：{line!r}"))
                break
            cur, pos = node(line, pos)
            if cur is None:
                break
            edges.append((prev, cur, e.group("solid") if e.group("solid") is not None else e.group("dotted")))
            prev = cur

    for nid in dict.fromkeys(referenced):
        if nid not in shapes:
            errors.append(("T2", f"節點沒有標籤：{nid}"))
    for src, dst, label in edges:
        if shapes.get(src) == "decision" and not label:
            errors.append(("T4", f"判斷節點的出邊沒有條件：{src} → {dst}"))
    if len(set(referenced)) > 20:
        errors.append(("T5", f"節點 {len(set(referenced))} 個，超過 20 個"))
    return errors


# ---------- 連結（T6） ----------
_FENCE_RE = re.compile(r"^```.*?^```", re.M | re.S)
_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def check_links(text: str, base: Path) -> list[str]:
    body = _CODE_SPAN_RE.sub("", _FENCE_RE.sub("", text))
    errors = []
    for target in _LINK_RE.findall(body):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0]
        if not (base / path).exists():
            errors.append(f"連結目標不存在：{target}")
    return errors


# ---------- 章節（T7、T8） ----------
_SECTION_RE = re.compile(r"^## (.*)$", re.M)
_LETTER_RE = re.compile(r"([A-Z])\. \S")


def check_sections(files: dict[str, str]) -> tuple[list[str], list[str]]:
    """回傳 (T7 問題, T8 問題)；files 是 {檔名: 內容}，只放流程檔"""
    t7, t8, seen = [], [], {}
    for name, text in files.items():
        heads = list(_SECTION_RE.finditer(text))
        for i, h in enumerate(heads):
            title = h.group(1)
            m = _LETTER_RE.match(title)
            if not m:
                t7.append(f"{name}：章節不是「<字母>. 標題」：{title}")
            elif m.group(1) in seen:
                t7.append(f"{name}：章節字母 {m.group(1)} 已用在 {seen[m.group(1)]}")
            else:
                seen[m.group(1)] = name
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            first = [l for l in text[h.end():end].splitlines() if l.strip()][:3]
            if not any("階段：" in l for l in first):
                t8.append(f"{name}：「{title}」前三行沒有「階段：」")
    return t7, t8


# ---------- 識別字（T9） ----------
_MODULES = {p.stem: p for p in (ROOT / "mac_ai").rglob("*.py") if p.stem != "__init__"}
# 模組.名稱：前面不能是字元、點、連字號（排除 mac_ai.router.x 這種完整路徑與檔名片段）；名稱不是副檔名
_IDENT_RE = re.compile(r"(?<![\w.\-])([a-z_][a-z0-9_]*)\.(?!(?:py|md|js|json|jsonl|sqlite3|txt|yml|onnx)\b)([A-Za-z_]\w*)")
_top_cache: dict[Path, set[str]] = {}


def _top_level_names(path: Path) -> set[str]:
    if path not in _top_cache:
        names: set[str] = set()

        def targets(t: ast.expr) -> None:
            if isinstance(t, ast.Name):
                names.add(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                for e in t.elts:
                    targets(e)

        def visit(body: list[ast.stmt]) -> None:
            for n in body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(n.name)
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        targets(t)
                elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
                    targets(n.target)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    names.update((a.asname or a.name).split(".")[0] for a in n.names)
                elif isinstance(n, (ast.If, ast.Try, ast.With)):
                    for part in ("body", "orelse", "finalbody"):
                        visit(getattr(n, part, []))
                    for h in getattr(n, "handlers", []):
                        visit(h.body)

        visit(ast.parse(path.read_text(encoding="utf-8")).body)
        _top_cache[path] = names
    return _top_cache[path]


def check_identifiers(text: str) -> list[str]:
    errors = []
    for mod, name in dict.fromkeys(_IDENT_RE.findall(text)):
        path = _MODULES.get(mod)
        if path is not None and name not in _top_level_names(path):
            errors.append(f"{mod}.{name}：{path.relative_to(ROOT)} 的頂層沒有 {name}")
    return errors


# ---------- 例外與分層（T10、T11） ----------
_EXCEPTION_SECTION_RE = re.compile(r"^## 例外.*?(?=^## |\Z)", re.M | re.S)


def _config_exceptions(cfg: configparser.ConfigParser) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for sec in cfg.sections():
        if not sec.startswith("importlinter:contract:"):
            continue
        code = cfg[sec]["name"].split()[0]
        for line in cfg[sec].get("ignore_imports", "").splitlines():
            if line.strip():
                out.setdefault(" ".join(line.split()), set()).add(code)
    return out


def _doc_exceptions(text: str) -> dict[str, set[str]]:
    m = _EXCEPTION_SECTION_RE.search(text)
    out: dict[str, set[str]] = {}
    for line in (m.group() if m else "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) < 2 or "->" not in cells[0]:
            continue
        out[" ".join(cells[0].strip("`").split())] = set(re.findall(r"C\d+", cells[1]))
    return out


def check_exceptions(module_deps: str, cfg: configparser.ConfigParser) -> list[str]:
    doc, conf = _doc_exceptions(module_deps), _config_exceptions(cfg)
    errors = [f"例外只在 module-deps.md：{d}" for d in doc.keys() - conf.keys()]
    errors += [f"例外只在 .importlinter：{d}" for d in conf.keys() - doc.keys()]
    errors += [f"{d} 的契約不一致：文件 {sorted(doc[d])}，設定 {sorted(conf[d])}"
               for d in doc.keys() & conf.keys() if doc[d] != conf[d]]
    return errors


def check_router_layers(modules: list[str], cfg: configparser.ConfigParser) -> list[str]:
    c2, c3 = cfg["importlinter:contract:c2-router-flow"], cfg["importlinter:contract:c3-router-leaves"]
    covered = {f"{c2['containers'].strip()}.{m.strip()}"
               for line in c2["layers"].splitlines() for m in line.split("|") if m.strip()}
    covered |= {line.strip() for line in c3["source_modules"].splitlines() if line.strip()}
    return [f"mac_ai.router.{m} 不在 C2 的 layers 或 C3 的 source_modules" for m in modules
            if f"mac_ai.router.{m}" not in covered]


ROUTER_MODULES = sorted(p.stem for p in (ROOT / "mac_ai" / "router").glob("*.py") if p.stem != "__init__")


# ---------- 現有文件 ----------
def test_flow_files_found():
    assert {p.name for p in FLOW_FILES} >= {"main-flow.md", "slow-path.md"}
    assert {p.name for p in DOC_FILES} >= STRUCTURE_FILES


@pytest.mark.parametrize("code", ["T1", "T2", "T3", "T4", "T5"])
def test_mermaid_rules_hold(code):
    errors = [f"{p.name}：{msg}" for p in DOC_FILES for c, msg in check_mermaid(p.read_text(encoding="utf-8")) if c == code]
    assert errors == []


def test_t6_links_resolve():
    errors = [f"{p.name}：{e}" for p in sorted(FLOWS.glob("*.md")) for e in check_links(p.read_text(encoding="utf-8"), p.parent)]
    assert errors == []


def test_t7_t8_sections():
    t7, t8 = check_sections({p.name: p.read_text(encoding="utf-8") for p in FLOW_FILES})
    assert t7 == []
    assert t8 == []


def test_t9_identifiers_exist():
    errors = [f"{p.name}：{e}" for p in DOC_FILES for e in check_identifiers(p.read_text(encoding="utf-8"))]
    assert errors == []


def test_t10_exception_table_matches_importlinter():
    assert check_exceptions((FLOWS / "module-deps.md").read_text(encoding="utf-8"), _config()) == []
    assert _doc_exceptions((FLOWS / "module-deps.md").read_text(encoding="utf-8"))  # 表格真的有被讀到


def test_t11_every_router_module_has_a_layer():
    assert len(ROUTER_MODULES) > 20
    assert check_router_layers(ROUTER_MODULES, _config()) == []


# ---------- 正例與反例 ----------
GOOD_DOC = """# 範例：一句話

範圍見 [main-flow.md](main-flow.md)，舊寫法 `[NNNN](…)` 在程式碼樣式裡不算連結。

```mermaid
flowchart TD
    In["slow 段原句"] --> Guard{"intent_guard"}
    Guard -- "no_action" --> No["回 NO_ACTION_REPLY"]
    Guard -- "一般" --> Cloud[["雲端規劃"]] --> Run["actions.run_tool"]
    Run --> Store[("memory.STORE")]
    Store -. "下一句" .-> In
    Run -.-> Done["✅ 完成"]
```

## X. 範例章節

階段：執行階段（`command_worker` 執行緒）。決策見 [0007](../decisions/0007-no-model-for-negation.md)。

- 呼叫 `actions.run_tool`、`stt/mic.MicSource`、`memory.STORE.add`；檔名 `native.py`、`mac_ai.router.actions` 不算識別字。

## Y. 另一節

決策見 [0007](../decisions/0007-no-model-for-negation.md)。
階段：比對階段（純函式）。
"""


def _mermaid(body: str) -> str:
    return "```mermaid\n" + body.strip("\n") + "\n```\n"


def test_good_doc_passes_every_check():
    assert check_mermaid(GOOD_DOC) == []
    assert check_links(GOOD_DOC, FLOWS) == []
    assert check_sections({"good.md": GOOD_DOC}) == ([], [])
    assert check_identifiers(GOOD_DOC) == []


def test_sequence_diagram_is_allowed():
    assert check_mermaid(_mermaid("sequenceDiagram\n    A->>B: hello")) == []


@pytest.mark.parametrize("code, body", [
    ("T1", 'flowchart LR\n    A["x"] --> B["y"]'),
    ("T1", 'graph TD\n    A["x"] --> B["y"]'),
    ("T2", "flowchart TD\n    Start[未加引號] --> End[\"y\"]"),
    ("T2", 'flowchart TD\n    Start("圓角") --> End["y"]'),
    ("T2", 'flowchart TD\n    Start>"旗子"] --> End["y"]'),
    ("T2", 'flowchart TD\n    start["小寫 ID"] --> End["y"]'),
    ("T2", 'flowchart TD\n    Start["x"] --> End["y"]\n    Start["又定義一次"]'),
    ("T2", 'flowchart TD\n    Start["x"] --> Bare'),
    ("T2", 'flowchart TD\n    Start["x"]\n    classDef hot fill:#f00'),
    ("T2", 'flowchart TD\n    subgraph S\n    Start["x"]\n    end'),
    ("T3", 'flowchart TD\n    Start["x"] ==> End["y"]'),
    ("T3", 'flowchart TD\n    Start["x"] --- End["y"]'),
    ("T3", 'flowchart TD\n    Start["x"] --x End["y"]'),
    ("T3", 'flowchart TD\n    Start["x"] -->|條件| End["y"]'),
    ("T3", 'flowchart TD\n    Start["x"] -- 沒加引號 --> End["y"]'),
    ("T3", 'flowchart TD\n    Start["x"] & Other["z"] --> End["y"]'),
    ("T4", 'flowchart TD\n    Ask{"有嗎"} -- "有" --> Yes["y"]\n    Ask --> No["n"]'),
    ("T4", 'flowchart TD\n    Ask{"有嗎"} -.-> Later["y"]'),
    ("T5", "flowchart TD\n" + "\n".join(f'    N{i}["{i}"] --> N{i + 1}["{i + 1}"]' for i in range(0, 21, 2))),
])
def test_mermaid_counterexamples(code, body):
    assert code in {c for c, _ in check_mermaid(_mermaid(body))}


def test_t5_twenty_nodes_is_ok():
    body = "flowchart TD\n" + "\n".join(f'    N{i}["{i}"] --> N{i + 1}["{i + 1}"]' for i in range(0, 20, 2))
    assert check_mermaid(_mermaid(body)) == []


@pytest.mark.parametrize("text", [
    "見 [不存在](no-such-file.md)",
    "見 [ADR](../decisions/9999-missing.md#背景)",
])
def test_t6_counterexamples(text):
    assert check_links(text, FLOWS)


@pytest.mark.parametrize("files, which", [
    ({"a.md": "## 沒有字母\n\n階段：執行階段。\n"}, 0),
    ({"a.md": "## A.沒有空白\n\n階段：執行階段。\n"}, 0),
    ({"a.md": "## A. 一\n\n階段：x\n", "b.md": "## A. 重複\n\n階段：x\n"}, 0),
    ({"a.md": "## A. 沒有階段\n\n只有說明。\n"}, 1),
    ({"a.md": "## A. 階段太晚\n\n一\n\n二\n\n三\n\n階段：x\n"}, 1),
])
def test_t7_t8_counterexamples(files, which):
    assert check_sections(files)[which]


@pytest.mark.parametrize("text", [
    "`actions.no_such_function`",
    "Fast 步驟 → stt/mic.NoSuchSource",
    "`memory.NOT_A_STORE.add`",
])
def test_t9_counterexamples(text):
    assert check_identifiers(text)


def test_t10_counterexamples():
    real = (FLOWS / "module-deps.md").read_text(encoding="utf-8")
    fake_row = "| `mac_ai.router.hant -> mac_ai.router.native` | C3 | 假例外 | — |\n"
    extra = real.replace("## 結構債", fake_row + "\n## 結構債", 1)
    assert extra != real and any("只在 module-deps.md" in e for e in check_exceptions(extra, _config()))
    wrong_contract = real.replace("| `mac_ai.db.rebuild -> mac_ai.router.music_prefs` | C1 |",
                                  "| `mac_ai.db.rebuild -> mac_ai.router.music_prefs` | C2 |", 1)
    assert wrong_contract != real and any("契約不一致" in e for e in check_exceptions(wrong_contract, _config()))
    missing_cfg = (ROOT / ".importlinter").read_text(encoding="utf-8") + (
        "\n[importlinter:contract:extra]\nname = C9 假契約\ntype = forbidden\n"
        "ignore_imports =\n    mac_ai.router.hant -> mac_ai.router.native\n")
    assert any("只在 .importlinter" in e for e in check_exceptions(real, _config(missing_cfg)))


def test_t11_counterexample():
    assert check_router_layers([*ROUTER_MODULES, "new_module"], _config()) == [
        "mac_ai.router.new_module 不在 C2 的 layers 或 C3 的 source_modules"]
