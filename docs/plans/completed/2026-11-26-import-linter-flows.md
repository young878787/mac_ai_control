# import-linter 依賴契約與 docs/flows 流程圖規範

> **歸檔（2026-11-26 結案）**：S1～S8 都已實作，目前有效的決策見 [ADR 0026](../../decisions/0026-import-linter.md)、[ADR 0027](../../decisions/0027-flow-docs.md)。以下內文保留結案時的樣子，原檔名 `docs/plans/active/2026-11-26-import-linter-flows.md`。實測結果見 §10。§10「尚未驗證」的三項在結案前已完成：H1（Mermaid 渲染）、H2（流程圖規範）由使用者 2026-11-26 確認；R1（CI）在 PR #3 與合併後的 `main` 通過，含 `import-linter` job。

狀態：**已結案（2026-11-26）**。原狀態：S1～S8 已實作，自動化驗收 A1～A7 通過，待使用者做 H1、H2、R1（見 §10）。§2 是使用者已決定的事。

## 0. 目標與結論

**目標**（使用者 2026-11-26）：確保程式依賴正確、不跨過太大的模組、流程不混亂，把流向整理清楚。

- **A. import-linter**：把 Python 的依賴方向寫成 5 份契約（§3），本機與 CI 都會擋。現有違規一律列為附原因的例外（`ignore_imports`），**不改任何 Python 程式碼**。
- **B. 流程圖規範**：寫在 `docs/flows/README.md`（§4），既有 6 張圖改成符合規範，新增 `docs/flows/module-deps.md` 說明依賴現況（§5），新增 `tests/unit/test_docs_flows.py` 防止文件過時（§6）。
- 分工：import-linter 管**模組依賴圖**（包含間接依賴與函式內的 import）；ast-grep 管**單檔語法與呼叫**（`_run`、`urlopen`、`front_context`、逾時）；pytest 管行為。熱路徑的「直接 import」兩邊都會擋，這是可以接受的重疊（ast-grep 那條規則同時也在檢查呼叫）。
- 這會推翻 [ast-grep 計畫](../completed/2026-11-26-ast-grep-rules.md) §0「不加 import-linter」那一句。那句只寫在計畫書裡，不是 ADR；本計畫依使用者決定改變做法，記在 ADR 0026。import-linter 用 `uvx` 執行，不進 requirements，跟 ruff、ast-grep 一樣。

## 1. 現況（2026-11-26 實測，scratchpad 試行設定，沒動 repo）

工具：`uvx --from import-linter==2.16 --with grimp==3.19`。
- 版本規則：發布滿兩週才用。2.17（2026-11-18）與 grimp 3.20（2026-11-13）都還沒滿兩週，所以不用。
- 2.16 的設定檔要用 `--config` 傳（位置參數是 2.17 才有）；預設會讀 repo 根目錄的 `.importlinter`。

- 掃到 89 檔、368 條依賴，約 0.15 秒。純靜態分析，不 import 被分析的程式，沒有 mlx 也能跑（CI 的 ubuntu 可以跑）。
- 標準庫可以當禁止對象（`include_external_packages = True`），但會併成頂層名稱：`urllib.parse` 和 `urllib.request` 分不開（`context.py` 用了 `urllib.parse`）。所以 `urllib` 不列進禁止清單，`urlopen` 繼續由 ast-grep 檢查。
- **函式內的 import 也算依賴**。`catalog._scan_installed` 的延遲 import 讓 `actions`、`catalog`、`cloud_client`、`llm_client`、`tools` 成為全專案**唯一的循環**（5 個模組）。
- 例外清單裡的 import 不存在時會報錯（exit 1），所以技術債還清後，例外一定要跟著刪。INI 多行值裡可以寫 `#` 註解。

**模組大小與依賴數**（grimp，只算 `mac_ai` 內部、含函式內 import；列出前幾名）：

| 模組 | 行數 | 往外依賴 | 被依賴 | 備註 |
|---|---|---|---|---|
| `router.local_actions` | 1011 | 18 | 3 | 最大的樞紐：本機工具、點歌、天氣、長尾回答、聯合搜尋都在這裡 |
| `router.actions` | 658 | 7 | 6 | 執行器 |
| `router.fast_path` | 480 | 7 | 4 | 頂層 import `actions`、`local_actions`（給 `Match.run` 用） |
| `router.slow_path` | 452 | 12 | 2 | |
| `router.pipeline` | 216 | 12 | 1 | |
| `config` | 191 | 0 | 33 | 最底層 |
| `router.lexicon` | 189 | 1 | 14 | |

**原樣跑試行契約時的違規**：

| 違規 | 原因 | 處理 |
|---|---|---|
| `db.rebuild → router.music_prefs` | 重算 `terms` 的維護 CLI，重用計分邏輯 | 例外 |
| `fast_path → actions`（頂層） | `Match.run` 呼叫 `run_tool`：比對結果和執行寫在同一個類別 | 例外（棘輪，§2.1） |
| `fast_path → local_actions`（頂層） | 比對階段呼叫 `plan_actions`：它是查 `ACTION_TABLE` 的純函式，但放在執行模組裡 | 例外（棘輪，§2.1） |
| `catalog → actions`（函式內） | AGENTS「不要碰」已記載的延遲 import | 例外 |
| `youtube_client → music_prefs → sqlite3` | 為了用純函式 `pinyin_units`；`music_prefs` 的 sqlite3 只用在型別標註與接收呼叫端的連線 | 例外 |

## 2. 決策（使用者 2026-11-26）

1. `fast_path → actions／local_actions` 照建議：列為例外、當成棘輪（不准再惡化），本次不重構。另外用一份獨立的現況文件說明（§5）。
2. 流程圖規範放 `docs/flows/README.md`。
3. 既有 6 張圖這次一起改成新規範（分開 commit）。
4. 做 `tests/unit/test_docs_flows.py`。
5. 計畫存成本檔。

## 3. 契約清單（`.importlinter`）

| # | 名稱 | 類型 | 內容 | 例外 |
|---|---|---|---|---|
| C1 | 後端分層 | layers | `assistant｜services` > `stt｜ui_bus` > `wake` > `router` > `db` > `config`（`｜` 是互不依賴的同層） | `db.rebuild → router.music_prefs` |
| C2 | router 編排順序 | layers（`containers = mac_ai.router`） | `pipeline` > `slow_path` > `fast_path` > `local_actions` > `actions`，也就是主流程的方向 | 無 |
| C3 | 底層不往上依賴編排層 | forbidden | 其餘 22 個 router 模組不得 import（含間接）C2 的 5 個模組 | `catalog → actions` |
| C4 | 熱路徑不碰 IO | forbidden | `fast_path`、`query_intent`、`domain`、`normalize`、`lexicon`、`stt.completeness`、`stt.endpoint`、`wake` 不得 import 5 個 client、`native`、`mac_ai.db`、`subprocess`、`sqlite3`、`socket`、`http` | `fast_path → actions`、`fast_path → local_actions`、`catalog → actions` |
| C5 | 網路 client 只做網路與解析 | forbidden | 5 個 client 不得 import `mac_ai.db`、`sqlite3`、`subprocess`（不 import 編排層這一條已由 C3 管） | `catalog → actions`、`youtube_client → music_prefs` |

- 試行結果：C1 到 C5 加上例外後全部 KEPT（C2、C3 是 2.16＋3.19 的實測；C1、C4、C5 的實測用的禁止清單比上表多幾項，C5 原本還包含編排層，正式設定會在 S1 重跑確認）。
- **例外的寫法**：每條上一行寫 `# 原因`。新增或放寬契約（改 layers、source、forbidden、ignore）等於改決策，要先問使用者，比照 ADR 0025 第 5 條。
- **新增 router 模組**時必須加進 C2 或 C3，否則不受約束。這點由 `test_docs_flows.py` 的 T11 檢查（§6）。
- 快取：`.gitignore` 加 `.import_linter_cache/`。

## 4. 流程圖規範（`docs/flows/README.md` 草案要點）

1. **何時要畫圖**：跨模組、跨執行緒、有退回鏈，或有守門順序時一定要畫；單一函式的內部邏輯只寫文字。圖或文字提到的函式、順序、退回鏈、守門一改，**同一個 commit 就要更新 flows**；計畫結案時同步（既有規定）。
2. **檔案**：分兩類，**流程檔**（一檔一條入口流程：`main-flow.md`、`slow-path.md`）與**結構檔**（`module-deps.md`）。H1 寫成「名稱：一句話」，第一段寫範圍、相關 flows 與 ADR 連結，接著放總覽圖。README 列出所有檔案和已用掉的章節字母。
3. **章節**（只套用在流程檔）：`## <字母>. 標題`，字母在 `docs/flows/` 全域唯一、只增不改（其他地方會用「見 C.6」引用）。目前沒有字母的兩節：`量測鏈` 改成 `J.`，`validate_calls` 改成 `K.`。每節前三行內要有：
   - `階段：…`（比對階段（純函式）、執行階段（`command_worker` 執行緒）、STT 執行緒、桌面行程…），直接對應紅線 1；
   - 有相關 ADR 時再加「決策見 [NNNN](…)」。
4. **Mermaid**：
   - 圖型：只用 `flowchart TD`；跨行程協定往返才用 `sequenceDiagram`。
   - 節點 ID 用英文 PascalCase，同一張圖內唯一；標籤一律加引號，例如 `["…"]`，換行用 `<br/>`。
   - 形狀固定 4 種：
     - 步驟、函式：`Id["…"]`
     - 判斷：`Id{"…？"}`
     - 佇列、`STORE`、資料庫：`Id[("…")]`
     - 外部服務（雲端、本機 LLM、Brave、氣象署、Open-Meteo、InnerTube）：`Id[["…"]]`
   - 邊：
     - `-->`：同一句內的下一步；
     - `-- 條件 -->`：分支，判斷節點的每條出邊都要有條件；
     - `-.->`／`-. 條件 .->`：跨語句、計時器、背景執行緒；
     - 不用 `==>`、`---`、`--x`、`--o`。
   - 結果節點帶 ✅／⚠️／❌／⏭️ 前綴（紅線 11）。
   - 守門與信任邊界（`_blocked`、`intent_guard`、`validate_calls`、`validate_tool_call`）出現在流程上時一定要畫成判斷節點。
   - 一張圖最多 20 個節點，超過就拆成子節的圖。不用 `classDef`、`style`、顏色。
   - 識別字照程式碼原樣寫，不加反引號，方便 grep。
5. **文字**：條列順序就是執行順序；數值寫常數名，括號附目前的值，例如 `WAKE_SESSION_S`（60 秒）。這條只套用在新寫或改到的文字，舊文字改到時才換。

## 5. 現況文件（`docs/flows/module-deps.md`，結構檔）

- 依 §4 的規範寫，內容只寫「現在長什麼樣」，規則本身以 `.importlinter` 為準：
  1. 套件分層圖（C1，Mermaid）與 router 編排流向圖（C2＋C3，Mermaid），各 20 個節點以內；
  2. 每個模組屬於哪一層或哪一類（表格）；
  3. **例外表**（依賴、原因、還清條件），要和 `.importlinter` 的 `ignore_imports` 完全一致（由 T10 檢查）；
  4. 唯一的循環（5 個模組，經 `catalog` 的延遲 import）；
  5. 樞紐模組（§1 的表）與已知結構債：`fast_path` 頂層 import 執行層（還清方式是把 `Match.run` 的執行移出 `fast_path`，會動到熱路徑與端點偵測，要另開計畫）；`local_actions` 1011 行、往外依賴 18 個（拆分要另開計畫）。
- 不寫自動產生的數字（行數、扇入扇出會一直變）。§1 的表只留在本計畫書裡，現況文件只描述結構和例外。
- `ARCHITECTURE.md` 加「後端分層」表，比照桌面那張（層、內容、可以 import 什麼），並連到 `module-deps.md`。

## 6. `tests/unit/test_docs_flows.py`

只用標準庫（`re`、`ast`、`configparser`、`pathlib`），離線、毫秒級，跟著 `-m unit` 一起跑。對象是 `docs/flows/*.md`，README 只檢查 T6。

| # | 檢查 | 失敗代表 |
|---|---|---|
| T1 | 每個 mermaid 區塊的開頭是 `flowchart TD` 或 `sequenceDiagram` | 圖型不符 |
| T2 | 節點定義只用 §4 的 4 種形狀，而且標籤有加引號 | 形狀不符或沒加引號 |
| T3 | 邊只用 `-->`、`-- x -->`、`-.->`、`-. x .->` | 用了不允許的邊 |
| T4 | 判斷節點的每條出邊都有條件標籤 | 分支沒寫條件 |
| T5 | 每張圖最多 20 個節點 | 圖太大要拆 |
| T6 | 相對連結（含 README）的目標檔案存在 | 連結失效 |
| T7 | 流程檔的 `##` 章節都是 `<字母>. ` 格式，字母跨檔不重複 | 章節編號錯 |
| T8 | 流程檔每個 `##` 章節的前三行內有 `階段：` | 缺階段標示 |
| T9 | 文字與圖中 `模組.名稱` 形式的識別字（模組是 `mac_ai/` 下的檔案）在該模組頂層有定義（AST：def、class、賦值、import） | 文件過時 |
| T10 | `module-deps.md` 的例外表 = `.importlinter` 所有 `ignore_imports` 的聯集 | 例外沒同步 |
| T11 | 每個 `mac_ai/router/*.py`（不含 `__init__`）都出現在 C2 的 layers 或 C3 的 source_modules | 新模組沒有分層 |

- 每項檢查都要有**反例**：用寫在測試裡的錯誤 Markdown 片段確認會失敗（比照 `rule-tests/`），不是只在現有文件上跑一次。
- T9 在現有文件上找到過時的識別字時，**修文件、不修程式**，並在 §10 逐條列出。如果判斷不了是文件錯還是程式錯，停下來問使用者。
- 不檢查 Mermaid 語法能不能渲染：那要裝 mermaid-cli 和 Chromium，不加這個依賴。改用人工預覽（§8.3）。

## 7. 影響範圍與實作步驟

分支：`chore/lint-ci`。commit 分三個（未經要求不 commit）：A（import-linter）、B1（規範、現況文件、測試）、B2（6 張圖改成新規範）。

| 步驟 | 內容 | 驗證 |
|---|---|---|
| S1 | `.importlinter`（C1 到 C5＋例外與原因）、`.gitignore` 加 `.import_linter_cache/` | A1 |
| S2 | 突變驗收（暫時改、事後還原） | A2 |
| S3 | `.github/workflows/lint.yml` 加 `import-linter` job（寫法比照 ruff job） | A3 |
| S4 | `docs/flows/README.md`（§4） | 人工對照 §4 |
| S5 | `docs/flows/module-deps.md`（§5；**2026-11-26 已先依使用者要求建立**，依賴表由 grimp 3.19 產生；S1 完成後刪掉開頭的狀態說明，S4 完成後依 README 規範再對一次）、`ARCHITECTURE.md` 的「後端分層」表 | A5（T6、T9、T10） |
| S6 | `tests/unit/test_docs_flows.py`（§6），先寫測試和反例 | A4 |
| S7 | 6 張圖與章節改成新規範（§4），T9 找到的過時識別字修文件 | A4、A6、H1 |
| S8 | ADR 0026（import-linter 契約、例外、與 0025 的分工）、ADR 0027（流程圖規範、flows 目錄分流程檔與結構檔）；ADR 0025 的「後果」加一行指向 0026；`AGENTS.md`（常用指令、文件地圖、commit 前檢查、不得為了過關放寬 `.importlinter`，維持 80 行以內）；`docs/guides/testing.md`（lint 指令、L2）；`docs/guides/coding-style.md`（新增模組先決定層、新增例外要先問） | A7 |

## 8. 驗收計畫

### 8.1 自動化（本機實際執行）

| # | 項目 | 指令／操作 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| A1 | 契約在現有程式上全過 | repo 根目錄 `uvx --from import-linter==2.16 --with grimp==3.19 lint-imports` | 結束碼 0；輸出 `Contracts: 5 kept, 0 broken`；被忽略的 import 數：C1 1、C2 0、C3 1、C4 3、C5 2 | 新發現的違規 → 停下問使用者要列例外還是改程式，不自行放寬 |
| A2 | 每份契約都抓得到違規 | 逐項暫時改，各跑一次 `lint-imports`：<br>(a) `router/tools.py` 頂層加 `from mac_ai import assistant` → C1<br>(b) `router/actions.py` 頂層加 `from mac_ai.router import pipeline` → C2<br>(c) `router/memory.py` 函式內加 `from mac_ai.router import local_actions` → C3（同時確認函式內 import 抓得到）<br>(d) `router/query_intent.py` 加 `from mac_ai.router import llm_client` → C4<br>(e) `router/weather_client.py` 加 `import subprocess` → C5<br>(f) 從 `.importlinter` 刪掉一條例外 → 對應的契約 BROKEN<br>(g) 在 `.importlinter` 加一條不存在的例外 → `No matches for ignored import`<br>(h) `lint-imports` 在 `mac_ai/` 以外的目錄執行 → 確認會失敗或找不到設定（記錄行為，CI 與文件都寫「在 repo 根目錄執行」） | (a)～(g) 每項結束碼 1，而且只有預期的那份契約 BROKEN；每項還原後 `git diff --stat` 只剩本計畫的檔案 | 抓不到 → 修契約範圍後重跑 |
| A3 | CI job 的指令 | 本機照 job 的寫法執行一次 | 同 A1 | — |
| A4 | 文件檢查測試 | `pytest tests/unit/test_docs_flows.py -v` | 0 failed；T1～T11 每項至少 1 個反例會失敗、1 個正例會通過 | 修測試或文件；不刪反例 |
| A5 | 現況文件與設定一致 | 包含在 A4 的 T10、T11；另外在 `module-deps.md` 的例外表多加一列假例外 | 加假例外時 T10 失敗，還原後通過 | — |
| A6 | 改圖沒有改到語意 | scratchpad 腳本從改動前後的 mermaid 抽出邊（來源標籤 → 目標標籤 → 條件），逐張圖比對 | 差異只有 §8.2 列出的刻意修正；其他節點與邊一一對應 | 對回原圖修正 |
| A7 | 回歸 | `pytest -m "unit or flow"`、`pytest`、`uvx ruff@0.17.6 check .`、`uvx --from ast-grep-cli@0.51.2 ast-grep test && … ast-grep scan`、`cd desktop && npm run lint && npm run typecheck && node --test` | pytest 0 failed（已知 `test_xasr_server.py::test_unknown_http_path_is_404` teardown 偶發逾時，出現時重跑並對照改動前）；其餘結束碼 0、0 違規 | 修正後重跑 |

### 8.2 刻意修正清單（A6 允許的差異，實作時補齊）

- H 長尾回答：「下一句」那條邊改成虛線 `-.->`（跟 G 天氣一致）。
- `weather_followup.STORE`、`sources.STORE`、`choices` 清單、`search_terms`、`queries` 快取改成資料儲存形狀 `[("…")]`。
- 雲端、本機 LLM、Brave、氣象署、Open-Meteo、InnerTube 改成外部服務形狀 `[["…"]]`。
- 判斷節點缺條件的出邊補上條件（只寫圖旁文字已有的條件；文字裡沒有就停下來問）。
- `量測鏈` 改成 `J.`、`validate_calls` 改成 `K.`；每節補上 `階段：`。
- （實作時補）結果符號移到標籤開頭：F「⚠️ 開搜尋頁」、G「✅ ctx.replies 泡泡」、H「⚠️ 開 Google 搜尋頁」「✅ ctx.replies：答案＋要打開來源嗎？」、I「⚠️ 開 Google 搜尋頁」。
- （實作時補）結果節點和 STORE 寫在同一個節點的拆成兩個：G「⚠️ 請使用者說縣市」→`weather_followup.STORE.put`、H「✅ 答案」→`sources.STORE`；虛線改從 STORE 節點出發。
- （實作時補）I「choices 清單 → pick_choice」改成虛線（使用者在下一句或點選才挑）。
- （實作時補）slow-path 的 `validate_calls` 改成判斷節點，往 `run_tool` 的邊補條件「有通過的 calls」（取自 D 節「執行通過的 calls」）。
- 總覽圖的 `&` 拆成 4 條邊、所有節點 ID 改成描述性的 PascalCase（不影響 A6 比對，比對用標籤）。
- **與上面清單不同的一處**：F 的 `queries 快取` 維持判斷節點（它有「命中／沒有」兩條分支，畫成資料儲存會失去判斷的意思）；`search_terms` 不在任何圖裡，沒有可改的節點。

### 8.3 人工／介面驗收

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| H1 | Mermaid 渲染 | 在能渲染 Mermaid 的預覽（GitHub 或裝了 Mermaid 擴充的 VS Code）打開 `main-flow.md`、`slow-path.md`、`module-deps.md` | 每張圖都渲染成功、沒有語法錯誤框；**由使用者執行**（push 由使用者決定） |
| H2 | 規範可讀性 | 使用者讀 `docs/flows/README.md` | 使用者認可；有意見就改規範並同步測試 |

### 8.4 真實環境

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| R1 | CI 實際執行 | push 後看 GitHub Actions 的 `lint` workflow | `ruff`、`ast-grep`、`import-linter`、`desktop` 四個 job 都綠燈；**push 由使用者執行** |

### 8.5 不在本次範圍

- 重構：把 `Match.run` 移出 `fast_path`、拆 `local_actions`、解開 `catalog` 的循環。只記錄在 `module-deps.md`，要另開計畫。
- `main.py`、`scripts/`、`bench/`、`tests/` 的依賴（import-linter 的 root 只有 `mac_ai`）。
- Mermaid 語法的自動驗證（mermaid-cli）。
- 舊文字裡寫死的數字改成常數名（只在改到時換）。
- pytest 進 CI。

## 9. 風險與假設

- 例外的邊被忽略之後，沿著那條邊新增的依賴（例如 `actions` 裡新增的 import）不會被 C4 抓到。C4 是「不准再惡化」的棘輪，不是完整保證，熱路徑仍要靠 `tests/unit/test_domain.py` 等行為測試。
- import-linter 會把標準庫併成頂層名稱，網路呼叫（`urllib.request`）的範圍只能靠 ast-grep。
- 6 張圖的標籤統一加引號屬於語法變動，可能讓某張圖渲染失敗，自動化測試抓不到，要靠 H1。
- T9 的識別字比對是用 regex 抓 `模組.名稱`，像 `step["web"]`、JSON 欄位這種不符合這個格式的寫法不會被檢查；可能有誤報，誤報時收窄 regex，不加豁免清單。
- 現況文件放在 `docs/flows/module-deps.md`，屬於結構檔（使用者 2026-11-26 要求建立）。

## 10. 實測結果（2026-11-26）

### 已實際執行確認

- **A1**：`uvx --from import-linter==2.16 --with grimp==3.19 lint-imports` 結束碼 0；`Analyzed 89 files, 368 dependencies`；`Contracts: 5 kept, 0 broken`；被忽略的 import 數 C1 1、C2 0、C3 1、C4 3、C5 2，與 §8.1 一致。`.import_linter_cache/` 已被 `.gitignore` 忽略。
- **A2**（scratchpad 腳本逐項改、跑、還原；還原後 `git diff --stat` 只剩 `.gitignore`）：

  | 突變 | 結束碼 | BROKEN |
  |---|---|---|
  | (a) `router/tools.py` 加 `from mac_ai import assistant` | 1 | C1、C2、C3、C5 |
  | (a′) `router/hant.py` 加 `from mac_ai import ui_bus`（補做，只打到 C1 的版本） | 1 | C1 |
  | (b) `router/actions.py` 加 `from mac_ai.router import pipeline` | 1 | C2 |
  | (c) `router/memory.py` 函式內加 `from mac_ai.router import local_actions` | 1 | C3（函式內 import 抓得到） |
  | (d) `router/query_intent.py` 加 `from mac_ai.router import llm_client` | 1 | C4 |
  | (e) `router/weather_client.py` 加 `import subprocess` | 1 | C5 |
  | (f) 刪掉 C5 的 `youtube_client -> music_prefs` 例外 | 1 | C5 |
  | (g) C1 加不存在的例外 `tools -> native` | 1 | `No matches for ignored import mac_ai.router.tools -> mac_ai.router.native.` |
  | (h) 在 `mac_ai/`、`/tmp` 執行 | 1 | `Could not read any configuration.`（文件與 CI 都寫在 repo 根目錄執行） |

  (a) 沒有達到「只有預期的那份契約 BROKEN」：多打到的 C2、C3、C5 都是真的間接鏈（`tools → assistant → stt.completeness → router.fast_path → actions`），屬於正確偵測，不是契約範圍錯；另做 (a′) 確認 C1 能單獨抓到。
- **A3**：`.github/workflows/lint.yml` 新增 `import-linter` job（寫法比照 `ruff`）；YAML 解析出 4 個 job（`ruff`、`ast-grep`、`import-linter`、`desktop`）；本機照 job 的指令執行，結果同 A1。
- **A4**：`pytest tests/unit/test_docs_flows.py -v` 45 passed。T1～T11 在現有文件上 0 問題；每項都有反例（T1 2、T2 8、T3 6、T4 2、T5 1、T6 2、T7 3、T8 2、T9 3、T10 3、T11 1）和正例（`GOOD_DOC`、20 個節點、`sequenceDiagram`）。
- **A5**：在真的 `module-deps.md` 例外表後加一列假例外 → T10 失敗（`例外只在 module-deps.md：mac_ai.router.hant -> mac_ai.router.native`）；還原（`cmp` 與備份相同）後通過。
- **A6**：scratchpad 腳本抽出改圖前後的邊（含 `&` 展開）逐張比對：總覽圖 23 條邊完全相同；其餘差異只有 §8.2 列的項目。
- **Mermaid 語法（補充，不取代 H1）**：在 scratchpad 裝 mermaid 11.4.1＋jsdom（不進 repo），用 `mermaid.parse()` 解析 9 張圖（main-flow 5、slow-path 1、module-deps 3）全部通過，故意寫錯的反例會被擋。只驗語法，不等於渲染。
- **T9 找到的過時識別字**（修文件、不修程式）：main-flow C.0 的 `catalog.refresh_if_stale()` → `catalog.DEFAULT.refresh_if_stale()`（`refresh_if_stale` 是 `Catalog` 的方法，`pipeline.py` 呼叫的是 `catalog.DEFAULT`）。只有這 1 條。
- **module-deps.md 的依賴表**：用 grimp 3.19 重算每個模組的 `mac_ai` 內部直接依賴（不含 `config`），與文件的表 0 不一致。
- **A7**：
  - `pytest -m "unit or flow"`：3065 passed。
  - `pytest`：3367 passed、6 skipped、1 error。error 是已知的 `tests/runtime/test_xasr_server.py::test_unknown_http_path_is_404` teardown 逾時（`TimeoutError`，timeout 5）。對照：在 HEAD（d803e95）的乾淨 worktree 單獨跑這一項，3/3 次同樣報錯；本機單獨跑 5/5 次報錯；不含本次新增測試檔的完整套件曾經 0 error（偶發）。這項與本次改動無關（本次沒有改任何 Python 程式碼）。
  - `uvx ruff@0.17.6 check .`：All checks passed；`ast-grep test`：8 passed；`ast-grep scan`：結束碼 0；`lint-imports`：5 kept。
  - `desktop`：`npm run lint` 結束碼 0、`npm run typecheck` 通過、`node --test` 309 pass、0 fail。
- `AGENTS.md` 75 行（80 行以內）。

### 與計畫不同的地方

- README 比 §4 多寫了幾條讓 T2、T3 能機械判斷的細節：邊的條件也加引號、不用 `&`、不用 `subgraph`、每個節點的標籤只定義一次。判斷節點寫成 `Id{"…"}`（標籤寫判斷的對象或問句），沒有強制以「？」結尾，因為既有的判斷節點（`下一句`、`federated_search`…）不是問句，改掉會動到標籤。
- T2 另外檢查節點 ID 是 PascalCase、每個節點都有標籤；T10 除了依賴的聯集，也比對每條例外屬於哪幾份契約。
- testing.md 除了 lint 指令與 L2，L0 對照表也加一列（改 `docs/flows/`、`.importlinter`、模組或函式名稱時跑 `test_docs_flows.py`＋`lint-imports`）。

### 尚未驗證（由使用者執行）

- **H1**：在 GitHub 或 VS Code Mermaid 預覽打開 `main-flow.md`、`slow-path.md`、`module-deps.md`，確認每張圖都能渲染。
- **H2**：讀 `docs/flows/README.md`，確認規範可以接受。
- **R1**：push 後看 GitHub Actions `lint` workflow 的 4 個 job 是否綠燈（`import-linter` job 尚未在 CI 實際跑過）。
- 本計畫結案時要移到 `completed/`，ADR 0026、0027 裡指向 `plans/active/` 的連結要一起改。
