# P3 盤點：`actions`／`local_actions` 依功能拆分前的現況盤點

狀態：**草案，等使用者確認範圍（§2）後才盤點**。本計畫**只盤點、不改程式**。拆法由使用者看完盤點結果再決定；決定後另開實作計畫。前置計畫見 [依賴收斂 P1、P2](2026-11-26-deps-converge.md)。

## 0. 目標

**目標**（使用者 2026-11-26）：先把 `actions` 和 `local_actions` 的現況盤點清楚，再想拆法。

- 這兩個檔目前是按「LLM 看不看得到」分的：`AVAILABLE_FUNCTIONS` 對應 `TOOLS_SCHEMA`，`LOCAL_TOOLS` 則 LLM 看不到。但「看不看得到」已經由 `TOOLS_SCHEMA`、`CLOUD_ONLY_TOOLS` 和 `validate_tool_call` 決定，和程式放在哪個檔案無關。所以這兩個檔各自混了好幾種功能：
  - `local_actions`（1011 行、53 個頂層函式或類別）分成 9 節：對應表、資料夾、檔案搜尋、聯合搜尋、前景視窗、背景音樂控制、點歌、播放控制事件、天氣、長尾回答。對應表會在 P2 移走。
  - `actions`（658 行、28 個）是另一種混合：送給 LLM 的工具、點歌分頁、`ExecContext`、讀畫面與聊天，加上 `_dispatch`／`run_tool`。
- 盤點要產出**能用來決定拆法的事實**：每個定義屬於哪個功能、誰在用、碰到哪些外部資源和狀態、功能之間怎麼互相呼叫、搬了會牽動哪些測試和文件。
- 本計畫不建議拆法。使用者提出候選拆法後，用同一份資料替每個候選算指標（§3 的 I11），讓候選之間可以比較。

## 1. 範圍與基準

- **對象**：`mac_ai/router/actions.py`、`mac_ai/router/local_actions.py` 的所有頂層定義，以及 `ExecContext`、`_dispatch`、`run_tool`、`LOCAL_TOOLS`、`AVAILABLE_FUNCTIONS`。
- **一併掃描的使用端**：`mac_ai/`（`pipeline`、`slow_path`、`fast_path`、`catalog`、`cloud_client`、`tools`…）、`main.py`、`scripts/`、`bench/`、`tests/`、`docs/`、`AGENTS.md`、`ARCHITECTURE.md`、`rules/`、`.importlinter`。
- **基準版本**：P1、P2 實作後的程式（`_run` 已在 `proc`，對應表已在 `action_plan`）。如果在 P1、P2 之前就盤點，受影響的列要標「P1／P2 會移動」，而且 P1、P2 做完後要重跑一次 V1～V5。
- **方法**：用 scratchpad 裡的腳本（`ast` 加 grep）產生資料，不進 repo。結果用表格寫在本檔 §10。手動判斷的欄位（例如功能歸類）要寫判斷依據。

## 2. 待使用者決定

| # | 問題 | 建議 |
|---|---|---|
| E1 | 盤點範圍要不要包含 `music_prefs`（596 行）和 `youtube_client`（440 行） | 不包含。它們各自只有一種職責，大小是因為功能本身複雜，不是因為混了多種功能。拆點歌功能時只看它們和 `local_actions` 之間的介面 |
| E2 | 何時盤點 | P1、P2 做完之後。基準比較乾淨，不用重跑 |
| E3 | 功能歸類的起點 | 以現有的節標題（`# ---------- … ----------`）當初始分類，跨節呼叫多的定義另外標出來。不先自己發明分類 |

## 3. 盤點項目

| # | 項目 | 方法 | 輸出（寫在 §10） |
|---|---|---|---|
| I1 | 頂層定義清單 | AST 列出兩個檔所有頂層的 `def`、`class`、賦值 | 名稱、類型、行數、所在節、公開或私有、初始功能分類（E3） |
| I2 | 檔內與兩檔之間的呼叫 | AST 收集每個定義引用的名稱，對照 I1 | 呼叫邊清單，以及「節 × 節」的矩陣；特別標出跨功能的呼叫，例如 `federated_search`、`context_search` 經 `_dispatch` 呼叫 `web_search_or_open_url`，`front_action` 呼叫 `_record_control`（點歌） |
| I3 | 外部依賴 | AST：每個定義用到的 `mac_ai` 模組名稱（5 個 client、`native`、`context`、`choices`、各種 `*_prefs`、`profile_store`、`sources`、`weather_followup`、`music_db`）、`_run`、`sqlite3`、`ThreadPoolExecutor`、`config` 常數 | 定義 × 依賴的表，加上每個節的依賴聯集 |
| I4 | `ExecContext` 欄位 | AST 找出 `ctx.<欄位>` 的讀與寫，範圍包含 `pipeline`、`slow_path` | 欄位 × 讀寫者（定義名稱）；標出「只有一個功能用到」和「多個功能共用」的欄位 |
| I5 | 工具登錄與可見性 | 對照 `AVAILABLE_FUNCTIONS`、`LOCAL_TOOLS`、`TOOLS_SCHEMA`、`cloud_client.CLOUD_ONLY_TOOLS`、`slow_path.STATE_TOOLS` 和 `_dispatch` 的分支 | 工具名稱 × 實作位置、可見性（本機 LLM／雲端／只有本機）、`_dispatch` 對它做的特例（記住瀏覽器或編輯器、前置失敗時略過、`mark_page`）；標出和紅線 5、8 有關的部分 |
| I6 | 外部使用者 | grep 加 AST：其他模組怎麼 import 或用屬性取這兩個檔的名稱 | 名稱 × 使用者。等於這兩個檔實際的公開介面 |
| I7 | 測試耦合 | grep `monkeypatch.setattr`／`setitem` 和 import | patch 目標（模組、名稱）× 測試檔 × 次數；直接 import 私有名稱的測試。目前粗算 patch 了 `la`／`local_actions`／`actions` 的有 15 處，用字串路徑 patch 的有 2 處，盤點時以腳本結果為準 |
| I8 | 文件與規則的引用 | grep `docs/`、`AGENTS.md`、`ARCHITECTURE.md`、`rules/`、`.importlinter`、`tests/unit/test_docs_flows.py` | 會被 T9 檢查的 `模組.名稱`；寫死檔案路徑的規則（例如 `secret-read-scope` 是靠函式名稱 `get_weather` 白名單）；契約裡出現的模組名稱 |
| I9 | 模組層級的狀態 | AST：可變的模組全域變數，例如 `MUSIC_DB`、`_rng`、`LOCAL_TOOLS`、鎖、快取 | 狀態 × 擁有者 × 誰會換掉它（`conftest`、`main.py`） |
| I10 | 執行緒與時機 | 讀程式碼，對照 `ARCHITECTURE.md` | 每個工具在哪個執行緒跑（`command_worker`、`federated_search` 的執行緒池、背景）；啟動時要先做的事（`MUSIC_DB.start()`、預熱） |
| I11 | 候選拆法的指標（使用者提出候選後才做） | 依候選的「定義 → 新模組」對照，重算 I2～I9 | 每個候選的：新模組之間的依賴邊數、會不會產生循環、需要的 `.importlinter` 例外、要改的 patch 目標數、要改的文件引用數、每個模組的行數 |

## 4. 驗收計畫

### 4.1 自動化（盤點完整性）

| # | 項目 | 方法 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| V1 | I1 沒有遺漏 | 腳本比較 I1 表格的列和 AST 的頂層定義 | 兩邊集合相同，0 遺漏、0 重複 | 補表格 |
| V2 | I5 涵蓋所有工具 | 腳本比較 `AVAILABLE_FUNCTIONS`、`LOCAL_TOOLS`、`TOOLS_SCHEMA`、`CLOUD_ONLY_TOOLS` 的名稱聯集與 I5 的列 | 集合相同；每個工具剛好一個實作位置 | 補表格；找不到實作就停下來回報 |
| V3 | I2 可以重算 | 腳本重算呼叫邊，和表格比較 | 差異是空的 | 修表格或腳本 |
| V4 | I4 涵蓋所有欄位 | 腳本比較 `dataclasses.fields(ExecContext)` 與 I4 的列 | 每個欄位都在表裡；沒人讀寫的欄位標「未使用」 | 補表格 |
| V5 | I7 總數 | grep 計數和表格合計比較 | 相同 | 修表格 |
| V6 | 沒有改到程式 | `git status --porcelain` | 只有本計畫檔有變動 | 還原多出的變動 |

### 4.2 人工

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| H1 | 抽查 | 從 I2、I3、I4 各隨機抽 3 列，讀程式碼核對（§10 寫出抽了哪幾列、結果如何） | 9 列全部正確。只要錯 1 列，就檢查那支腳本並重跑整項 |
| H2 | 資訊夠不夠 | 使用者讀 §10 | 使用者表示可以開始想拆法，或指出還缺哪一項（缺的話補一個 I 項目再盤點） |

### 4.3 不在本次範圍

- 任何程式碼、測試、契約、規則的修改。
- 決定拆法、決定 `LOCAL_TOOLS` 的登錄方式、`_dispatch` 特例的去留（這些是盤點後實作計畫的內容）。
- E1 沒選進來的模組。

## 5. 風險與假設

- 動態呼叫用 AST 抓不完整：`LOCAL_TOOLS`／`AVAILABLE_FUNCTIONS` 這種字典、`_dispatch("名稱", …)` 這種字串，以及測試用字串路徑 patch。處理方式：字典的值和 `_dispatch` 的字串常數當成呼叫邊；抓不到的情況寫在 §10 的「已知漏洞」。
- 功能歸類（E3）有主觀成分。有爭議的定義不歸類，標「待使用者決定」。
- 盤點完成後到實作拆分之間，如果程式又改了，實作計畫開始前要重跑 V1～V5。

## 10. 盤點結果

（盤點時填寫。）
