# ADR 索引、紅線補檢查與文件去重複

狀態：**已結案（2026-11-27）**：實作與驗收結果見 §3，決策見 [ADR 0031](../../decisions/0031-adr-rationale-only.md)。使用者 2026-11-27 確認：C 選「ADR 只記理由」，實作 A＋B＋D；L3 驗收步驟搬進 testing.md；存成計畫檔。

## 0. 目標

ADR 會一直增加，部分修改形成鏈（0021 被 0023、0024 修改），同一條規則散在 AGENTS、coding-style、ADR 三處。目標是讓 AI 平常不必讀 ADR，而且文件不會默默失效：

- **ADR 只記理由**（C）：舊 ADR 內文不再修改；現行規則以 `docs/guides/coding-style.md`（與 testing.md）和 lint／測試為準。
- **索引**（A）：`docs/decisions/README.md` 一列一份，AI 先讀索引再開相關的 ADR。
- **紅線補檢查**（B）：能機械判定但目前只靠文字的紅線改成測試。
- **去重複**（D）：AGENTS 標強制方式、coding-style 去掉與 AGENTS 重複的段落、testing.md 基線只留最新、L3 驗收步驟從結案計畫書搬進 testing.md。

不改任何產品程式；不改 `rules/`、`.importlinter`、`ruff.toml`、`eslint.config.js`。

## 1. 項目

| 代號 | 內容 | 位置 |
|---|---|---|
| C1 | 新增 ADR 0031「ADR 只記理由，現行規則以指南與自動檢查為準」；0016 狀態行註記第 3 條被 0031 修改 | `docs/decisions/0031-adr-rationale-only.md`、`0016` |
| C2 | 修改鏈的現行規則補進 coding-style：天氣（0021←0023、0024）、`web_answer` 金鑰與延遲（0022←0023、0024）、`profile` 常駐資料（0024）；照 ADR 原文整理，不新增規則 | `docs/guides/coding-style.md` |
| A1 | ADR 索引：編號｜標題｜狀態｜領域｜被修改／取代｜強制方式 | `docs/decisions/README.md` |
| B1 | `SYSTEM_PROMPT`＋`TOOLS_SCHEMA` 內容 hash 快照與字數上限（上限＝2026-11-27 的值） | `tests/unit/test_tools.py` |
| B2 | 詞庫 `app_shortcuts`：每個 `code` 綁定都帶 `command` 或是翻頁鍵（116／121）；id 與名稱、選單、按鈕、指令不得是結束程式或刪除類（說法不檢查：「退出全螢幕」合法） | `tests/unit/test_lexicon.py`（原寫 `test_context.py`；那個檔測前景讀取，詞庫資料的檢查放 `test_lexicon.py`） |
| B3 | 釘住 `IDLE_LIMITS`／`LISTEN_LIMITS` 的數值 | `desktop/test/motion-metrics.test.js` |
| B4 | `AGENTS.md`、`testing.md`、`coding-style.md` 反引號內的 repo 路徑都存在（排除含 `<`、`*`、`{` 的寫法與不入版控的路徑） | `tests/unit/test_docs_index.py` |
| B5 | 索引一致性：每份 ADR 剛好一列、標題與狀態和檔案一致、強制方式引用的 rule id／ESLint 規則／測試檔都存在 | `tests/unit/test_docs_index.py` |
| D1 | AGENTS：紅線標強制方式、文件地圖指向索引、工作流程依 0031、併入「不主動產出 .md」；維持 80 行以內 | `AGENTS.md` |
| D2 | coding-style 刪「工作流程偏好」（與 AGENTS 工作流程重複） | `docs/guides/coding-style.md` |
| D3 | testing.md 基線只留最新一次，歷史留在 git | `docs/guides/testing.md` |
| D4 | L3 驗收步驟照原文搬進 testing.md 新的一節（web-answer §7.2／§9、music-play §7.2／§7.3、stt-engine-xasr §7.2／§7.3），L3 欄改指向該節 | `docs/guides/testing.md` |

## 2. 驗收計畫

### 2.1 自動化（離線）

| 項目 | 指令 | 通過標準 | 失敗時 |
|---|---|---|---|
| V1 新測試在現有文件上通過 | `pytest tests/unit/test_docs_index.py tests/unit/test_tools.py tests/unit/test_lexicon.py` | 0 failed | B4 抓到的失效路徑先列給使用者，確認後才修文件 |
| V2 新檢查抓得到違規 | 每個新測試把值暫時改壞一次（改提示詞一字、加一個無 ⌘ 的 `code`、放寬一個門檻、索引刪一列、引用不存在的路徑），跑對應測試後還原 | 每次都 FAIL，還原後 PASS；`git diff` 只剩預期變更 | 檢查寫錯，修測試 |
| V3 桌面 | `cd desktop && node --test && npm run typecheck && npm run lint` | 0 fail、0 錯誤 | 修 B3 |
| V4 L2 全套 | `pytest`；`uvx ruff@0.17.6 check .`；`ast-grep test`＋`scan`；`lint-imports`；`python3 scripts/check_file_lines.py` | `pytest` 0 failed（已知偶發：xasr teardown 逾時，改動前也會出現，出現時重跑該檔確認）；其餘 0 違規 | 修到通過，不放寬設定 |
| V5 AGENTS 行數 | `wc -l AGENTS.md` | ≤ 80 | 精簡措辭，不移掉紅線 |

### 2.2 人工（使用者確認）

| 項目 | 做法 | 通過標準 |
|---|---|---|
| H1 C2 整理正確 | 回報列出每條新寫的現行規則與 ADR 出處（編號、條次） | 使用者確認沒有誤寫或自行新增的規則 |
| H2 索引的領域分類 | 看 `docs/decisions/README.md` | 使用者接受或指定調整 |
| H3 D4 搬移 | 回報列出搬了哪些節、原文是否逐字 | 門檻與指令和原計畫書一致 |

### 2.3 不在範圍

- 產品程式、lint 設定、CI 設定的任何改動；`native.post_key` 本身的白名單（B2 只鎖詞庫資料）。
- 舊 ADR 內文的修改（0016 只改狀態行）。
- live／cloud／app 實測（沒有改行為）。
- 子目錄 `AGENTS.md`（等根目錄接近 80 行上限再談）。

## 3. 實作結果

2026-11-27，已實際執行確認：

- **V1**：`test_docs_index.py` 27 passed，`test_tools.py`＋`test_lexicon.py` 46 passed。B4 實際檢查了 176 個路徑、62 個連結（AGENTS 22、testing 118＋9、coding-style 30＋21、索引 6＋32），現有文件 0 個失效。
- **V2**：6 個改壞案例（`SYSTEM_PROMPT` 加一字、詞庫 `save` 去掉 ⌘、`slowFraction` 0.15→0.2、索引連結改錯、testing.md 引用不存在的測試檔、AGENTS 引用不存在的規則）全部 FAIL；還原後 PASS，SHA-1 與原檔相同，產品檔不在 `git status`。
- **V3**：`node --test` 311 pass；`npm run typecheck`、`npm run lint` 0 錯誤。
- **V4**：`pytest` 3412 passed、6 skipped、126 deselected、1 error（`test_xasr_server.py::test_unknown_http_path_is_404` teardown 逾時；`git stash` 撤掉改動後同樣 1 error，既有問題）；ruff、`ast-grep test`（7 passed）＋`scan`（rc 0）、`lint-imports`（5 kept）、`check_file_lines.py`（rc 0）。
- **V5**：`AGENTS.md` 79 行。
- 實作與計畫不同：B2 放 `test_lexicon.py`；0011 的狀態行補註「第 1 條被 0022 第 9 條取代」（原本只寫在 0022 內文，索引的被修改欄要靠狀態行）；testing.md 的 L0 對照表加一列 `test_docs_index.py`；coding-style「新增 LLM 可用工具」那列補上字數上限與 `PREFIX_SHA256`。
- **H1、H2**：使用者確認沒問題。**H3**：搬移內容照原文保留，使用者要求在點歌那節前面加一行警告（`main.py --text` 會打真雲端、寫正式 `data/`），已加。
