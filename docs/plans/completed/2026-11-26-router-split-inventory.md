# P3 盤點：`actions`／`local_actions` 依功能拆分前的現況盤點

狀態：**已結案（2026-11-26）**：盤點完成後，使用者決定直接依 §10.1 的分類拆分。實作與驗收結果見 §11，決策見 [ADR 0030](../../decisions/0030-router-feature-modules.md)。原狀態：I1～I10、I12 盤點完成，V1～V7、H1 通過（§10.11），§2 的 E1～E6 使用者已確認。§0～§10 是盤點當時的紀錄（`docs/plans/active/` 等路徑、行號都是當時的）。前置計畫 [依賴收斂 P1、P2](../completed/2026-11-26-deps-converge.md) 已實作。

**先看 [§10.0](#100-重點想拆法前先看)。**

## 0. 目標

**目標**（使用者 2026-11-26）：先把 `actions` 和 `local_actions` 的現況盤點清楚，再想拆法。

- 這兩個檔目前是按「LLM 看不看得到」分的：`AVAILABLE_FUNCTIONS` 對應 `TOOLS_SCHEMA`，`LOCAL_TOOLS` 則 LLM 看不到。但「看不看得到」已經由 `TOOLS_SCHEMA`、`CLOUD_ONLY_TOOLS` 和 `validate_tool_call` 決定，和程式放在哪個檔案無關。所以這兩個檔各自混了好幾種功能：
  - `local_actions`（973 行、67 個頂層定義：47 個函式／類別、20 個常數），按功能可分成 §10.1 的 11 組。
  - `actions`（643 行、52 個：27 個函式／類別、25 個常數）是另一種混合：送給 LLM 的工具、點歌分頁、`ExecContext`、讀畫面與聊天，加上 `_dispatch`／`run_tool`；按功能可分成 7 組。
- **新的硬性限制**（2026-11-26，[ADR 0028](../../decisions/0028-file-line-limit.md)）：程式檔上限 800 行。`local_actions.py` 973 行超標，CI 的 `file-lines` 目前是紅燈，所以拆分不再是可做可不做。
- 盤點要產出**能用來決定拆法的事實**：每個定義屬於哪個功能、誰在用、碰到哪些外部資源和狀態、功能之間怎麼互相呼叫、搬了會牽動哪些測試和文件。
- 本計畫不建議拆法。使用者提出候選拆法後，用同一份資料替每個候選算指標（§3 的 I11），讓候選之間可以比較。

## 1. 範圍與基準

- **對象**：`mac_ai/router/actions.py`、`mac_ai/router/local_actions.py` 的所有頂層定義，以及 `ExecContext`、`_dispatch`、`run_tool`、`LOCAL_TOOLS`、`AVAILABLE_FUNCTIONS`；`tests/flow/test_route_music.py`（E4，I12）。
- **一併掃描的使用端**：`mac_ai/`（`pipeline`、`slow_path`、`fast_path`、`catalog`、`cloud_client`、`tools`…）、`main.py`、`scripts/`、`bench/`、`tests/`、`docs/`、`AGENTS.md`、`ARCHITECTURE.md`、`rules/`、`.importlinter`。
- **基準版本**：分支 `refactor/deps-converge` 的工作目錄。盤點時是 HEAD `056a735` 加上 P1、P2 尚未 commit 的變更；之後 P1、P2 已 commit（`8be19c1`、`7f5c620`），兩個檔的雜湊沒變（I12 時重新確認過）。I12 的基準是 HEAD `7f5c620`，`test_route_music.py` SHA-1 `cf581266c28de6511e27b98c24456beee9f2e3dc`。`actions.py` SHA-1 `e2ebf1952d9c337dec6291a19c0c6051397fbd4a`、`local_actions.py` SHA-1 `ebb50274fb11f8848d51bbb7b1f896d76e979b8c`。任一個改變（單純 commit 不會改變），實作計畫開始前要重跑 V1～V5；`test_route_music.py` 改變要重跑 V7。
- **方法**：scratchpad 的腳本（`ast` 加 grep），不進 repo，暫存目錄清掉就沒了。功能歸類記在 §10.1，要重跑時照它重建腳本。手動判斷的欄位寫判斷依據。

## 2. 待使用者決定

**使用者 2026-11-26 確認 E1～E6 全部照建議。** E4 的後續：補盤點 `test_route_music.py`（I12，§10.12）。

盤點範圍的三題：

| # | 問題 | 本次採用 | 結果 |
|---|---|---|---|
| E1 | 是否包含 `music_prefs`（488 行）、`youtube_client`（440 行） | 不包含：各自只有一種職責，都沒超過 800 行 | 只記它們和 `local_actions` 的介面（§10.3） |
| E2 | 何時盤點 | P1、P2 做完之後 | 已符合 |
| E3 | 功能歸類的起點 | 現有節標題 | 節標題和內容對不上（§10.0 第 5 點），另外加了「功能」欄 |

盤點後新增的三題（已確認）：

| # | 問題 | 決定 | 理由 |
|---|---|---|---|
| E4 | `tests/flow/test_route_music.py`（1226 行，超標）要不要和拆法一起規劃 | 要：放進拆分的實作計畫 | 它和點歌程式綁在一起（§10.7），分兩次搬要改兩輪。`desktop/test/attention.test.js`（1041 行）和本次無關，不納入 |
| E5 | CI 紅燈要不要先用最小搬移解掉 | 不要，等拆法定了一次搬 | 先搬再拆會讓同一批測試 patch 和文件改兩次；代價是 `file-lines` 繼續紅燈 |
| E6 | §10.1 的功能歸類，爭議項（§10.0 第 6 點）要放哪一邊 | 想拆法時一起決定 | 歸類會影響 I11 算出來的依賴 |

## 3. 盤點項目

| # | 項目 | 方法 | 輸出（寫在 §10） |
|---|---|---|---|
| I1 | 頂層定義清單 | AST 列出兩個檔所有頂層的 `def`、`class`、賦值 | 名稱、類型、行數、所在節、公開或私有、初始功能分類（E3） |
| I2 | 檔內與兩檔之間的呼叫（含循環） | AST 收集每個定義引用的名稱，對照 I1 | 呼叫邊清單，以及「節 × 節」的矩陣；特別標出跨功能的呼叫，例如 `federated_search`、`context_search` 經 `_dispatch` 呼叫 `web_search_or_open_url`，`front_action` 呼叫 `_record_control`（點歌） |
| I3 | 外部依賴 | AST：每個定義用到的 `mac_ai` 模組名稱（5 個 client、`native`、`context`、`choices`、各種 `*_prefs`、`profile_store`、`sources`、`weather_followup`、`music_db`）、`_run`、`sqlite3`、`ThreadPoolExecutor`、`config` 常數 | 定義 × 依賴的表，加上每個節的依賴聯集 |
| I4 | `ExecContext` 欄位 | AST 找出 `ctx.<欄位>` 的讀與寫，範圍包含 `pipeline`、`slow_path` | 欄位 × 讀寫者（定義名稱）；標出「只有一個功能用到」和「多個功能共用」的欄位 |
| I5 | 工具登錄與可見性 | 對照 `AVAILABLE_FUNCTIONS`、`LOCAL_TOOLS`、`TOOLS_SCHEMA`、`cloud_client.CLOUD_ONLY_TOOLS`、`slow_path.STATE_TOOLS` 和 `_dispatch` 的分支 | 工具名稱 × 實作位置、可見性（本機 LLM／雲端／只有本機）、`_dispatch` 對它做的特例（記住瀏覽器或編輯器、前置失敗時略過、`mark_page`）；標出和紅線 5、8 有關的部分 |
| I6 | 外部使用者 | grep 加 AST：其他模組怎麼 import 或用屬性取這兩個檔的名稱 | 名稱 × 使用者。等於這兩個檔實際的公開介面 |
| I7 | 測試耦合 | grep `monkeypatch.setattr`／`setitem` 和 import | patch 目標（模組、名稱）× 測試檔 × 次數；直接 import 私有名稱的測試。結果見 §10.7 |
| I8 | 文件與規則的引用 | grep `docs/`、`AGENTS.md`、`ARCHITECTURE.md`、`rules/`、`.importlinter`、`tests/unit/test_docs_flows.py` | 會被 T9 檢查的 `模組.名稱`；寫死檔案路徑的規則（例如 `secret-read-scope` 是靠函式名稱 `get_weather` 白名單）；契約裡出現的模組名稱 |
| I9 | 模組層級的狀態 | AST：可變的模組全域變數，例如 `MUSIC_DB`、`_rng`、`LOCAL_TOOLS`、鎖、快取 | 狀態 × 擁有者 × 誰會換掉它（`conftest`、`main.py`） |
| I10 | 執行緒與時機 | 讀程式碼，對照 `ARCHITECTURE.md` | 每個工具在哪個執行緒跑（`command_worker`、`federated_search` 的執行緒池、背景）；啟動時要先做的事（`MUSIC_DB.start()`、預熱） |
| I11 | 候選拆法的指標（使用者提出候選後才做） | 依候選的「定義 → 新模組」對照，重算 I2～I9 | 每個候選的：新模組之間的依賴邊數、會不會產生循環、需要的 `.importlinter` 例外、要改的 patch 目標數、要改的文件引用數、每個模組的行數（全部要 ≤800）、哪些模組要靠 import 副作用登錄工具；測試檔每個新檔的行數、需要共用的 fixture／helper 和放在哪、`test_route_music.py` 內要改的 patch 目標與屬性存取數、要改的外部引用（§10.12）。候選只要寫「§10.1 的功能（或定義）→ 模組名稱」和「§10.12 的區塊 → 測試檔名」 |
| I12 | `tests/flow/test_route_music.py` 的結構（E4） | AST 依 `# ----------` 節分段，加上 `pytest --collect-only` 的案例數；grep 檔外的引用 | 每節的行數、測試函式數、案例數、fixture、測到的工具與 §10.1 功能組、碰到的正式程式名稱；跨節共用的 fixture／helper／常數；檔外引用這個檔的地方 |

## 4. 驗收計畫

### 4.1 自動化（盤點完整性）

| # | 項目 | 方法 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| V1 | I1 沒有遺漏 | 腳本比較 I1 表格的列和 AST 的頂層定義 | 兩邊集合相同，0 遺漏、0 重複 | 補表格 |
| V2 | I5 涵蓋所有工具 | 腳本比較 `AVAILABLE_FUNCTIONS`、`LOCAL_TOOLS`、`TOOLS_SCHEMA`、`CLOUD_ONLY_TOOLS` 的名稱聯集與 I5 的列 | 集合相同；每個工具剛好一個實作位置（雲端別名 `site_search` 要指到一個有實作的工具） | 補表格；找不到實作就停下來回報 |
| V3 | I2 可以重算 | 腳本重算呼叫邊，和表格比較 | 差異是空的 | 修表格或腳本 |
| V4 | I4 涵蓋所有欄位 | 腳本比較 `dataclasses.fields(ExecContext)` 與 I4 的列 | 每個欄位都在表裡；沒人讀寫的欄位標「未使用」 | 補表格 |
| V5 | I7 總數 | grep 計數和表格合計比較 | 相同 | 修表格 |
| V6 | 沒有改到程式 | 比對盤點前後工作目錄的檔案雜湊（工作目錄本來就有 P1、P2 未 commit 的變更，不能只看 `git status`） | 我改的只有本計畫檔 | 還原多出的變動 |
| V7 | I12 沒有遺漏 | 腳本加總 §10.12 區塊的行數、測試函式數、案例數，和 `wc -l`、AST 的 `test_*` 數、`pytest --collect-only` 數比較 | 三個數都相同 | 修表格或腳本 |

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
- 功能歸類（E3）有主觀成分。爭議項仍先歸在一組（才能算矩陣），在 §10.0 第 6 點標出。
- 搬移時的靜默失效：原模組如果保留同名的 import，測試 `monkeypatch` 原模組會「成功」但搬走的程式看不到。實作時搬走的名稱不要在原模組留同名轉出，讓漏改的 patch 直接報錯。
- 盤點完成後到實作拆分之間，如果程式又改了，實作計畫開始前要重跑 V1～V5。

## 10. 盤點結果（2026-11-26）

### 10.0 重點（想拆法前先看）

這些是任何拆法都會碰到的事實，不是建議的拆法。

**1. 行數**
- `local_actions.py` 973 行，要降到 800 以下至少移出 173 行。`actions.py` 643 行，沒超標。
- 只搬一節幾乎不夠：最大的「檔案搜尋」節是 168 行（含空行、註解），搬走後還有 805 行（跟著刪掉的 import 行另計）。
- 各節行數（節標題到下一節之前）：

| `local_actions` 的節 | 行 | `actions` 的節 | 行 |
|---|---|---|---|
| 檔頭（import、常數） | 49 | 檔頭（系統、瀏覽器工具） | 149 |
| 資料夾 | 15 | 點歌分頁 | 129 |
| 檔案搜尋 | 168 | 執行上下文與讀畫面 | 365 |
| 聯合搜尋 | 109 | | |
| 前景視窗 | 43 | | |
| 背景音樂控制 | 147 | | |
| 點歌 | 148 | | |
| 播放事件 | 129 | | |
| 天氣 | 69 | | |
| 長尾回答（含結尾的工具登錄） | 96 | | |

- 以功能算（只算定義本身），有兩群互相緊密、各自超過 173 行：音樂相關（點歌 121＋播放事件 116＋背景音樂控制 76＋偏好 DB 共用 10＝323 行），以及找東西相關（檔案搜尋 127＋選擇清單 33＋聯合搜尋 96＝256 行）。天氣＋長尾回答是 139 行，不夠。

**2. 兩個循環**（18 組功能之間；模組層級目前沒有循環，因為都在同一檔裡）
- **「執行基礎」和「分派」如果放在同一個模組，會和工具模組互相依賴。** 工具要用 `ExecContext`、逾時常數、`front_app`；`_dispatch`／`AVAILABLE_FUNCTIONS` 又要引用工具。分成兩個模組就沒有循環：執行基礎只依賴 `context`、`proc`，不引用任何工具。
- **檔案搜尋 ↔ 選擇清單 ↔ 聯合搜尋**：
  - `find_file → _close_previous`（檔案搜尋 → 清單）
  - `pick_choice → _act`（清單 → 檔案搜尋）
  - `pick_choice → _open_web`、`record_search`（清單 → 聯合搜尋）
  - `federated_search → _close_previous`（聯合搜尋 → 清單）
  - `_close_previous` 只依賴 `choices` 和 `ExecContext`。把它放到比三者都低的位置，這個循環就消失（已用腳本確認）；另一種做法是三組放同一模組。

**3. 共用狀態**
- `MUSIC_DB`、`_now`、`_rng`、`_db_call` 寫在「點歌」節，實際上點歌、播放事件、聯合搜尋、天氣四組都在用（`_rng` 只有點歌用）。
- `_now`、`_rng`、`MUSIC_DB` 是會被整個換掉的變數（測試 patch、`conftest` 換成暫存 DB）。如果其他模組寫 `from 某模組 import _now`，拿到的是當下的值，之後 patch 原模組不會生效。要用「`模組._now()`」的寫法存取。

**4. 本機工具靠 import 副作用登錄**
- `local_actions` 載入時執行 `LOCAL_TOOLS.update(...)`（14 個）。正式路徑靠 `pipeline`、`slow_path` 頂層 import `local_actions` 帶到（`tests/runtime/test_import_footprint.py` 有檢查）。
- 拆出去的每個含本機工具的模組，都要有正式路徑 import 它，否則工具在執行時才 `KeyError`。

**5. 節標題和內容對不上**
- `system_power_action`、`open_project` 在「點歌分頁」節底下。
- `front_action`、`_front_action` 在「背景音樂控制」節底下。
- `_close_previous`、`pick_choice` 在「檔案搜尋」節底下。
- `actions` 檔頭沒有節標題，`ExecContext` 到 `run_tool` 全在「執行上下文與讀畫面」一節。

**6. 歸屬有爭議的定義**（E6）

| 定義 | 本次歸在 | 也可以歸在 | 原因 |
|---|---|---|---|
| `pick_choice` | 選擇清單 | 找東西整體 | 同時處理本機檔案與網路項目 |
| `context_search` | 站內搜尋／常用功能 | 瀏覽器、聯合搜尋 | 站內搜尋，退回時是一般網頁搜尋；聯合搜尋與雲端 `site_search` 都用它 |
| `_record_control` | 播放事件／回饋 | 常用功能 | 唯一呼叫者是 `front_action` |
| `_SAFARI_TABS_SCRIPT`、`_safari_pid`、`_is_music_url` | 背景音樂控制 | Safari 共用 | `_music_tab_video_id`（回饋）也用 |
| `_video_row` | 點歌 | 偏好 DB 共用 | 播放事件也用 |
| `today_taipei` | 天氣 | 執行基礎 | `web_answer`、`slow_path`、bench 用，和天氣查詢無關 |
| `TAB_BROWSERS` | 瀏覽器／分頁 | 執行基礎 | 讀畫面、點歌分頁也用 |

**7. 對外介面很小**：正式程式只用到十幾個名稱（§10.6），其他都是內部細節。

**8. 搬移時不能變的契約**
- 工具名稱與參數（§10.5）。
- `ctx.escalate` 的三種形狀：`find_file` 給 `{"label", "scope"}`、`remember_home` 給 `{"kind": "chat", "note", "fallback"}`、`web_answer` 給 `{"kind": "slow"}`，由 `pipeline._escalate_to_llm` 讀。
- `ctx.step` 的鍵（會寫進紀錄檔，§10.4）。

**9. 會被拆分牽動的檢查**
- `.importlinter`：C2 是一條直線（… > `local_actions` > `actions`），新模組要加進 C2 或 C3。決定放哪裡要先問使用者（紅線 16）。
- `secret-read-scope` 是用函式名稱 `get_weather` 放行，搬 `get_weather` 不用改規則，改名要改。
- `tests/unit/test_docs_flows.py`：flows 裡的 `actions.run_tool`（3 處）搬走要跟著改（T9）；新模組要出現在契約（T11）。

**10. `test_route_music.py`（1226 行）**（I12，細節 §10.12）
- 按測的東西分成 5 塊（依檔案順序）：路由語料與守門 129、背景音樂控制 362、點歌 370、回饋路由 61、播放事件／回饋 304。單塊都 ≤800，相鄰兩塊合併也都 ≤800（最大是背景音樂控制＋點歌 732）。
- 拆檔的難處在**測試自己的共用設施**，不在正式程式：`bg` fixture（背景控制的假環境）同時給背景音樂控制和播放事件／回饋用；`tabs`、`mclock`、`_step`、`_rows`、`_events`、`T0` 給點歌、回饋路由、播放事件／回饋用。`test_s4_skip_penalty_through_the_real_route` 同時用 `bg` 和點歌那邊的 `net`／`_step`。
- **隱藏的耦合**：`tests/unit/test_completeness.py:52` 用檔案路徑載入整個 `test_route_music.py` 取 `C1_POSITIVE`。改檔名或搬走語料，這個測試會失敗（不會靜默）。
- 這個檔對 `actions`／`local_actions` 只有 23 處引用（21 處屬性存取、1 處字串形式的 `setattr(local_actions, "_now")`、1 個 from-import），其中 patch 只有 3 處（`LOCAL_TOOLS` 2、`_now` 1）；搬程式時要改的地方不多。

### 10.1 I1 頂層定義（119 個：`actions` 52、`local_actions` 67）

括號內是行數（含裝飾器，不含節標題與空行）。「功能」是本次的人工歸類（依節標題、註解與呼叫關係），拆法不必照它。

| 功能 | 檔 | 定義（行數） | 行數合計 | 所在節標題 |
|---|---|---|---|---|
| 系統/App | `actions` | `control_mac_app`(13)、`set_system_volume`(16)、`adjust_screen_brightness`(8)、`system_power_action`(21)、`open_project`(12) | 70 | 檔頭、點歌：開啟／重複使用 YouTube 合輯分頁 |
| 瀏覽器/分頁 | `actions` | `google_search_url`(2)、`web_search_or_open_url`(8)、`TAB_BROWSERS`(1)、`_FRONT_AND_RUNNING_SCRIPT`(8)、`_SAFARI_TAB_STEP`(3)、`_CHROME_TAB_STEP`(3)、`_TAB_SCRIPTS`(22)、`_TAB_DONE_TEXT`(1)、`_pick_tab_browser`(14)、`browser_tab`(9) | 71 | 檔頭 |
| 音樂分頁開啟 | `actions` | `MUSIC_TAB_TIMEOUT_S`(1)、`_MUSIC_WATCH_URL`(1)、`_SAFARI_MUSIC_TAB_SCRIPT`(27)、`_CHROME_MUSIC_TAB_SCRIPT`(22)、`_MUSIC_TAB_SCRIPTS`(1)、`music_watch_url`(4)、`music_browser`(6)、`open_music_video`(12)、`open_search_page`(4) | 78 | 點歌：開啟／重複使用 YouTube 合輯分頁 |
| 執行基礎 | `actions` | `_FAIL_PREFIXES`(1)、`ExecContext`(23)、`front_context`(12)、`front_app`(8)、`front_site`(14)、`mark_page`(7)、`EXTERNAL_TIMEOUT_S`(1)、`POLL_TIMEOUT_S`(1)、`_skipped`(2) | 69 | 檔頭、執行上下文與讀畫面 |
| 分派 | `actions` | `AVAILABLE_FUNCTIONS`(11)、`LOCAL_TOOLS`(1)、`_dispatch`(40)、`run_tool`(9) | 61 | 執行上下文與讀畫面 |
| 讀畫面 | `actions` | `PAGE_SETTLE_S`(1)、`PAGE_LOAD_MAX_S`(1)、`PAGE_FIXED_WAIT_S`(1)、`SCREEN_MAX_PX`(1)、`_PAGE_WORDS_RE`(1)、`_FRONT_BROWSER_WINDOW_SCRIPT`(8)、`SCREEN_QUESTION`(1)、`SCREEN_SYSTEM_PROMPT`(7)、`_wait_for_page`(21)、`_front_browser_window`(10)、`read_screen`(67)、`_cloud_read_screen`(32) | 151 | 執行上下文與讀畫面 |
| 聊天 | `actions` | `CHAT_SYSTEM_PROMPT`(8)、`_strip_think`(3)、`chat_reply`(36) | 47 | 執行上下文與讀畫面 |
| 資料夾 | `local_actions` | `_FOLDERS`(1)、`_folder_path`(3)、`open_folder`(7) | 11 | 檔頭、資料夾 |
| 檔案搜尋 | `local_actions` | `_KINDS`(1)、`_SINCE_ZH`(1)、`_SINCE_PREDICATE`(6)、`DANGEROUS_EXT`(4)、`_EXCLUDE_DIRS`(1)、`_BUNDLE_SUFFIXES`(1)、`_MAX_SCAN`(1)、`_esc`(2)、`build_predicate`(9)、`is_dangerous`(5)、`_filter_results`(18)、`_mtime`(5)、`_rank`(17)、`_label`(3)、`_act`(12)、`find_file`(41) | 127 | 檔頭、檔案搜尋 |
| 選擇清單 | `local_actions` | `_close_previous`(6)、`pick_choice`(27) | 33 | 檔案搜尋 |
| 聯合搜尋 | `local_actions` | `record_search`(7)、`_local_hits`(5)、`_web_hits`(12)、`_open_web`(8)、`federated_search`(54)、`record_explicit_search`(10) | 96 | 聯合搜尋 |
| 站內搜尋/常用功能 | `local_actions` | `context_search`(15)、`_run_binding`(23)、`front_action`(7)、`_front_action`(43) | 88 | 前景視窗：站內搜尋、常用功能、背景音樂控制 |
| 背景音樂控制 | `local_actions` | `_SAFARI_TABS_SCRIPT`(23)、`_safari_pid`(5)、`_is_music_url`(8)、`_safari_music_state`(5)、`_control_op`(3)、`_background_control`(32) | 76 | 背景音樂控制 |
| 偏好DB共用 | `local_actions` | `MUSIC_DB`(1)、`_now`(1)、`_rng`(1)、`_db_call`(7) | 10 | 點歌 |
| 點歌 | `local_actions` | `NO_DB_FAVORITE_MESSAGE`(1)、`_video_row`(3)、`_played`(2)、`_play_favorite`(22)、`_learn_after_play`(20)、`play_media`(57)、`_play_search_page`(16) | 121 | 點歌 |
| 播放事件/回饋 | `local_actions` | `_CONTROL_EVENTS`(1)、`NO_DB_MUSIC_MESSAGE`(1)、`NO_RECENT_PLAY_MESSAGE`(1)、`NO_CANDIDATE_MESSAGE`(1)、`NO_MUSIC_TAB_MESSAGE`(1)、`_record_control`(10)、`music_replace`(38)、`_music_tab_video_id`(23)、`music_feedback`(22)、`music_top`(18) | 116 | 播放控制事件、換一首、喜歡／不要再放、我最常聽什麼 |
| 天氣 | `local_actions` | `_TAIPEI`(1)、`today_taipei`(3)、`get_weather`(33)、`home_reply`(3)、`remember_home`(18) | 58 | 天氣 |
| 長尾回答 | `local_actions` | `_WHY`(2)、`_web_fallback`(9)、`web_answer`(60)、`open_source`(10) | 81 | 長尾回答 |

### 10.2 I2 呼叫關係

跨功能的引用共 149 條（全部引用 240 條）。下表列出「執行基礎」以外的部分：

| 呼叫端功能 | 被呼叫功能 | 邊（呼叫者→被引用者） |
|---|---|---|
| 音樂分頁開啟 | 瀏覽器/分頁 | `music_browser→TAB_BROWSERS` |
| 分派 | 系統/App | `AVAILABLE_FUNCTIONS→adjust_screen_brightness`、`AVAILABLE_FUNCTIONS→control_mac_app`、`AVAILABLE_FUNCTIONS→open_project`、`AVAILABLE_FUNCTIONS→set_system_volume`、`AVAILABLE_FUNCTIONS→system_power_action`、`_dispatch→control_mac_app`、`_dispatch→open_project` |
| 分派 | 瀏覽器/分頁 | `AVAILABLE_FUNCTIONS→browser_tab`、`AVAILABLE_FUNCTIONS→web_search_or_open_url`、`_dispatch→browser_tab`、`_dispatch→web_search_or_open_url` |
| 分派 | 讀畫面 | `AVAILABLE_FUNCTIONS→read_screen`、`_dispatch→read_screen` |
| 分派 | 聊天 | `AVAILABLE_FUNCTIONS→chat_reply`、`_dispatch→chat_reply` |
| 讀畫面 | 瀏覽器/分頁 | `_front_browser_window→TAB_BROWSERS` |
| 檔案搜尋 | 資料夾 | `find_file→_FOLDERS`、`find_file→_folder_path` |
| 檔案搜尋 | 選擇清單 | `find_file→_close_previous` |
| 選擇清單 | 檔案搜尋 | `pick_choice→_act` |
| 選擇清單 | 聯合搜尋 | `pick_choice→_open_web`、`pick_choice→record_search` |
| 聯合搜尋 | 瀏覽器/分頁 | `_web_hits→google_search_url`、`federated_search→web_search_or_open_url` |
| 聯合搜尋 | 分派 | `federated_search→_dispatch` |
| 聯合搜尋 | 檔案搜尋 | `_local_hits→_filter_results`、`_local_hits→_rank`、`_local_hits→build_predicate`、`federated_search→_act` |
| 聯合搜尋 | 選擇清單 | `federated_search→_close_previous` |
| 聯合搜尋 | 站內搜尋/常用功能 | `federated_search→context_search` |
| 聯合搜尋 | 偏好DB共用 | `federated_search→MUSIC_DB`、`federated_search→_db_call`、`federated_search→_now`、`record_search→MUSIC_DB`、`record_search→_db_call`、`record_search→_now` |
| 站內搜尋/常用功能 | 瀏覽器/分頁 | `context_search→web_search_or_open_url` |
| 站內搜尋/常用功能 | 分派 | `_front_action→_dispatch`、`context_search→_dispatch` |
| 站內搜尋/常用功能 | 背景音樂控制 | `_front_action→_background_control`、`_front_action→_control_op` |
| 站內搜尋/常用功能 | 播放事件/回饋 | `front_action→_record_control` |
| 點歌 | 音樂分頁開啟 | `_play_favorite→open_music_video`、`_play_search_page→open_search_page`、`play_media→open_music_video` |
| 點歌 | 偏好DB共用 | `_learn_after_play→_db_call`、`_play_favorite→_db_call`、`_play_favorite→_rng`、`_play_search_page→_db_call`、`play_media→MUSIC_DB`、`play_media→_db_call`、`play_media→_now` |
| 播放事件/回饋 | 音樂分頁開啟 | `music_replace→open_music_video` |
| 播放事件/回饋 | 背景音樂控制 | `_music_tab_video_id→_SAFARI_TABS_SCRIPT`、`_music_tab_video_id→_is_music_url`、`_music_tab_video_id→_safari_pid` |
| 播放事件/回饋 | 偏好DB共用 | `_record_control→MUSIC_DB`、`_record_control→_db_call`、`_record_control→_now`、`music_feedback→MUSIC_DB`、`music_feedback→_db_call`、`music_feedback→_now`、`music_replace→MUSIC_DB`、`music_replace→_db_call`、`music_replace→_now`、`music_top→MUSIC_DB`、`music_top→_db_call`、`music_top→_now` |
| 播放事件/回饋 | 點歌 | `music_feedback→_video_row`、`music_replace→_video_row` |
| 天氣 | 偏好DB共用 | `get_weather→MUSIC_DB`、`get_weather→_db_call`、`get_weather→_now`、`remember_home→MUSIC_DB`、`remember_home→_db_call`、`remember_home→_now`、`today_taipei→_now` |
| 長尾回答 | 瀏覽器/分頁 | `_web_fallback→web_search_or_open_url`、`open_source→web_search_or_open_url` |
| 長尾回答 | 天氣 | `web_answer→today_taipei` |

各功能用到「執行基礎」的名稱（執行基礎本身不引用其他功能）：

| 功能 | 用到的名稱 |
|---|---|
| 系統/App | `EXTERNAL_TIMEOUT_S` |
| 瀏覽器/分頁 | `EXTERNAL_TIMEOUT_S`、`POLL_TIMEOUT_S` |
| 分派 | `ExecContext`、`_FAIL_PREFIXES`、`_skipped`、`front_app`、`mark_page` |
| 讀畫面 | `EXTERNAL_TIMEOUT_S`、`ExecContext`、`POLL_TIMEOUT_S` |
| 聊天 | `ExecContext` |
| 資料夾 | `EXTERNAL_TIMEOUT_S`、`ExecContext` |
| 檔案搜尋 | `EXTERNAL_TIMEOUT_S`、`ExecContext` |
| 選擇清單 | `ExecContext`、`_FAIL_PREFIXES` |
| 聯合搜尋 | `EXTERNAL_TIMEOUT_S`、`ExecContext`、`front_app`、`front_site`、`mark_page` |
| 站內搜尋/常用功能 | `EXTERNAL_TIMEOUT_S`、`ExecContext`、`POLL_TIMEOUT_S`、`_skipped`、`front_context`、`front_site`、`mark_page` |
| 背景音樂控制 | `ExecContext`、`POLL_TIMEOUT_S` |
| 點歌 | `ExecContext` |
| 播放事件/回饋 | `ExecContext`、`POLL_TIMEOUT_S` |
| 天氣 | `ExecContext` |
| 長尾回答 | `ExecContext`、`_FAIL_PREFIXES`、`front_app`、`mark_page` |

- 動態呼叫（靜態看不到目標）：`run_tool` → `_dispatch(name, …)` → `LOCAL_TOOLS[name]`／`AVAILABLE_FUNCTIONS[name]`；`_front_action` 的 `fallback` 清單（由 `fast_path` 用先前比對到的動作組出，例如開終端機 → `control_mac_app`）。
- 反向進入分派的有 3 個工具：`context_search`、`federated_search`（呼叫 `_dispatch("web_search_or_open_url")`，沿用「瀏覽器失敗就略過」和 `mark_page`）、`_front_action`（跑 `fallback`）。`_web_fallback`、`open_source` 則是直接呼叫 `web_search_or_open_url`，自己做 `mark_page`。

### 10.3 I3 外部依賴

依賴名稱是 `mac_ai.router` 的模組（`mac_ai.db`、`config` 另外標）與用到的標準庫。

| 功能 | 依賴（聯集） | 逐定義（只列有外部依賴的） |
|---|---|---|
| 系統/App | lexicon, os, proc | `control_mac_app`: lexicon, proc；`set_system_volume`: proc；`adjust_screen_brightness`: proc；`system_power_action`: proc；`open_project`: lexicon, os, proc |
| 瀏覽器/分頁 | proc, urllib | `google_search_url`: urllib；`web_search_or_open_url`: proc；`_pick_tab_browser`: proc；`browser_tab`: proc |
| 音樂分頁開啟 | config, proc, youtube_client | `music_watch_url`: youtube_client；`music_browser`: config, proc；`open_music_video`: proc；`open_search_page`: proc |
| 執行基礎 | context, proc, time | `ExecContext`: context；`front_context`: context, proc, time；`front_site`: context, proc, time；`mark_page`: time |
| 分派 | lexicon, time | `_dispatch`: lexicon；`run_tool`: time |
| 讀畫面 | base64, cloud_client, config, json, llm_client, normalize, proc, re, shutil, tempfile, time | `_PAGE_WORDS_RE`: re；`_wait_for_page`: proc, time；`_front_browser_window`: json, proc；`read_screen`: base64, cloud_client, config, llm_client, normalize, proc, shutil, tempfile, time；`_cloud_read_screen`: base64, cloud_client, config, proc |
| 聊天 | config, llm_client, normalize, re, time | `_strip_think`: re；`chat_reply`: config, llm_client, normalize, time |
| 資料夾 | lexicon, os, proc | `_FOLDERS`: lexicon；`_folder_path`: os；`open_folder`: os, proc |
| 檔案搜尋 | choices, config, lexicon, os, proc, re, time | `_KINDS`: lexicon；`build_predicate`: re；`is_dangerous`: os；`_filter_results`: os；`_mtime`: os；`_rank`: os, re；`_act`: choices, os, proc；`find_file`: choices, config, os, proc, time |
| 選擇清單 | choices | `_close_previous`: choices；`pick_choice`: choices |
| 聯合搜尋 | choices, concurrent, config, os, proc, search_client, search_prefs, time | `record_search`: search_prefs；`_local_hits`: config, os, proc；`_web_hits`: choices, search_client；`_open_web`: choices, proc；`federated_search`: choices, concurrent, config, os, search_prefs, time |
| 站內搜尋/常用功能 | context, native, proc, urllib | `context_search`: proc, urllib；`_run_binding`: context, native, proc；`_front_action`: context, native |
| 背景音樂控制 | config, context, native, proc, urllib | `_safari_pid`: config, proc；`_is_music_url`: context, urllib；`_background_control`: native, proc |
| 偏好DB共用 | mac_ai.db, random, sqlite3, time | `MUSIC_DB`: mac_ai.db；`_now`: time；`_rng`: random；`_db_call`: sqlite3 |
| 點歌 | music_prefs, sqlite3, time, youtube_client | `_video_row`: sqlite3；`_play_favorite`: music_prefs, sqlite3, time, youtube_client；`_learn_after_play`: music_prefs, sqlite3；`play_media`: music_prefs, time, youtube_client；`_play_search_page`: music_prefs, sqlite3, time |
| 播放事件/回饋 | music_prefs, proc, time, urllib, youtube_client | `_record_control`: music_prefs；`music_replace`: music_prefs, time, youtube_client；`_music_tab_video_id`: proc, urllib, youtube_client；`music_feedback`: music_prefs, time；`music_top`: music_prefs |
| 天氣 | config, datetime, profile_store, time, weather_client, weather_followup, weather_prefs | `_TAIPEI`: datetime；`today_taipei`: datetime；`get_weather`: config, datetime, profile_store, time, weather_client, weather_followup, weather_prefs；`remember_home`: profile_store, weather_prefs |
| 長尾回答 | cloud_client, config, search_client, sources, time, urllib | `web_answer`: cloud_client, config, search_client, sources, time；`open_source`: urllib |

- `music_prefs` 只被「點歌」「播放事件／回饋」使用；`youtube_client` 被「音樂分頁開啟」「點歌」「播放事件／回饋」使用。三組以外沒有人碰。
- `search_client`：聯合搜尋、長尾回答；`cloud_client`：讀畫面、長尾回答；`llm_client`：讀畫面、聊天。

### 10.4 I4 `ExecContext` 欄位（19 個，全部有人讀寫）

| 欄位 | 寫 | 讀 | 範圍 |
|---|---|---|---|
| `last_editor` | `_dispatch` | `_dispatch` | 只有分派 |
| `last_browser` | `_dispatch` | `_dispatch`、讀畫面、聯合搜尋、站內搜尋、長尾回答 | 多功能 |
| `page_pending` | `mark_page`、`_wait_for_page` | `_wait_for_page` | 基礎 → 讀畫面 |
| `failed` | `_dispatch`、`mark_page` | `_dispatch`、`context_search` | 基礎、分派、站內搜尋 |
| `replies` | 讀畫面、聊天、`find_file`、`federated_search`、`get_weather`、`music_top`、`web_answer`、`pipeline`、`slow_path` | `pipeline` | 多功能 |
| `step` | `front_context`、`run_tool`、多個本機工具、`pipeline` | 多個 | 多功能（各寫各的鍵） |
| `utterance` | `pipeline`、`slow_path` | 讀畫面、聊天、點歌、播放事件、長尾回答 | 多功能（工具只讀） |
| `results` | `run_tool`、`pipeline`、`slow_path` | `pipeline`、`slow_path` | 編排層 |
| `status` | 讀畫面、聊天、`pipeline`、`slow_path` | `pipeline` | 多功能 |
| `chat_done` | `chat_reply`、`pipeline` | `chat_reply` | 只有聊天 |
| `escalate` | `find_file`、`remember_home`、`web_answer`、`pipeline` | `pipeline` | 本機工具 → 編排層 |
| `chat_hint` | `pipeline`、`slow_path` | `chat_reply` | 編排層 → 聊天 |
| `misheard` | `pipeline` | `pipeline` | 只有 `pipeline` |
| `normalized` | `pipeline` | `pipeline` | 只有 `pipeline` |
| `front` | `front_context` | `front_context`、`context_search` | 基礎、站內搜尋 |
| `front_loaded` | `front_context`、`_dispatch` | `front_context` | 基礎、分派 |
| `front_host` | `front_context`、`front_site` | `front_site`、`slow_path.front_hint` | 基礎、編排層 |
| `calls` | `run_tool` | `pipeline`、`slow_path` | 分派 → 編排層 |
| `ai_reply` | `chat_reply`、`web_answer`、`slow_path` | `pipeline`、`slow_path` | 多功能 |

`ctx.step` 各功能寫入的鍵：
- 分派／基礎：`exec_ms`、`context`
- 讀畫面：`wait_ms`、`window_ms`、`capture_ms`、`capture_target`、`resize_ms`、`vl_ms`、`vl`、`image`、`vision_cloud`
- 聊天：`chat_ms`、`chat`、`chat_error`
- 檔案／清單／聯合搜尋：`find_ms`、`choices`、`federated`、`search_prefs`
- 常用功能／背景音樂／播放事件：`context`、`control`、`music`
- 點歌：`music`；天氣：`weather`；長尾回答：`web`、`web_fallback`

共用的鍵：`context`（基礎、常用功能、背景音樂）、`control`（常用功能、背景音樂寫，播放事件讀）、`music`（點歌、播放事件）、`choices`（檔案搜尋、清單、聯合搜尋）。

### 10.5 I5 工具與可見性

可見性：**本機** ＝本機 LLM 的 `TOOLS_SCHEMA`（8 個）；**雲端** ＝雲端規劃看得到（上面 8 個加 `front_action`、`site_search`，有 `BRAVE_API_KEY` 時再加 `web_answer`）；**程式** ＝只有程式自己發出。

| 工具 | 實作 | 登錄表 | 可見性 | 誰發出 | `_dispatch` 的特例 |
|---|---|---|---|---|---|
| `control_mac_app` | `actions` | AVAILABLE | 本機、雲端 | `fast_path`、`action_plan` | 開瀏覽器／編輯器時記 `last_*`、更新 `failed`、重讀前景。`STATE_TOOLS` |
| `set_system_volume` | `actions` | AVAILABLE | 本機、雲端 | `fast_path` | 無。`STATE_TOOLS` |
| `adjust_screen_brightness` | `actions` | AVAILABLE | 本機、雲端 | `fast_path` | 無。`STATE_TOOLS` |
| `web_search_or_open_url` | `actions` | AVAILABLE | 本機、雲端 | `fast_path`、`action_plan`、`slow_path`、本機工具 | 瀏覽器失敗就略過；帶 `last_browser`／前景；`mark_page` |
| `system_power_action` | `actions` | AVAILABLE | 本機、雲端 | `fast_path` | 無。`STATE_TOOLS` |
| `open_project` | `actions` | AVAILABLE | 本機、雲端 | `fast_path`、`action_plan` | 編輯器失敗就略過；帶 `last_editor`／前景。`STATE_TOOLS` |
| `read_screen` | `actions` | AVAILABLE | 本機、雲端 | `fast_path`、`slow_path` | 瀏覽器或網頁失敗就略過 |
| `chat_reply` | `actions` | AVAILABLE | 本機、雲端 | 模型（`slow_path` 另外直接呼叫函式） | 不帶參數 |
| `browser_tab` | `actions` | AVAILABLE | 程式 | `fast_path` | 瀏覽器失敗就略過；帶 `last_browser` |
| `open_folder` | `local_actions` | LOCAL | 程式 | `action_plan` | — |
| `find_file` | `local_actions` | LOCAL | 程式 | `action_plan` | — |
| `pick_choice` | `local_actions` | LOCAL | 程式 | `fast_path`、`pipeline`（點選） | — |
| `context_search` | `local_actions` | LOCAL | 程式；雲端以 `site_search` 的名字 | `fast_path`、`pipeline`、`slow_path`（由 `site_search` 換名） | 自己檢查 `failed` |
| `front_action` | `local_actions` | LOCAL | 程式、雲端 | `fast_path`、模型（雲端） | `STATE_TOOLS` |
| `play_media` | `local_actions` | LOCAL | 程式 | `fast_path` | — |
| `music_replace` | `local_actions` | LOCAL | 程式 | `fast_path` | — |
| `music_feedback` | `local_actions` | LOCAL | 程式 | `fast_path` | — |
| `music_top` | `local_actions` | LOCAL | 程式 | `fast_path` | — |
| `get_weather` | `local_actions` | LOCAL | 程式 | `fast_path`、`pipeline` | — |
| `remember_home` | `local_actions` | LOCAL | 程式 | `fast_path` | — |
| `web_answer` | `local_actions` | LOCAL | 程式、雲端（有搜尋金鑰） | `fast_path`、`slow_path`、模型（雲端） | — |
| `open_source` | `local_actions` | LOCAL | 程式 | `pipeline` | — |
| `federated_search` | `local_actions` | LOCAL | 程式 | `fast_path` | — |
| `site_search` | 別名 → `context_search` | 無 | 雲端 | 模型（雲端） | `slow_path.validate_calls` 換名 |

和紅線有關的位置：紅線 5（本機工具對 LLM 看不到）靠 `TOOLS_SCHEMA`／`CLOUD_ONLY_TOOLS` 和 `validate_tool_call`，和檔案位置無關；紅線 6 在 `_act`／`is_dangerous`／`_filter_results`；紅線 8 在 `_front_action`／`_run_binding`／`_background_control`；紅線 10 的 `CWA_API_KEY` 只在 `get_weather`。

### 10.6 I6 外部使用者（這兩個檔實際的公開介面）

| 名稱 | 檔 | 使用者 |
|---|---|---|
| `ExecContext`、`run_tool` | `actions` | `pipeline`、`slow_path` |
| `front_app` | `actions` | `pipeline` |
| `front_context`、`chat_reply`、`_FAIL_PREFIXES` | `actions` | `slow_path` |
| `record_explicit_search` | `local_actions` | `pipeline` |
| `today_taipei` | `local_actions` | `slow_path`、`bench/web_answer_latency.py` |
| `MUSIC_DB` | `local_actions` | `main.py` |
| `search_client`（透過 `local_actions` 取模組） | `local_actions` | `bench/web_answer.py` |
| （整個模組，為了登錄工具） | `local_actions` | `pipeline`、`slow_path` |
| `router._EXPORTS` 的 9 個名稱：`ExecContext`、`adjust_screen_brightness`、`browser_tab`、`control_mac_app`、`open_project`、`read_screen`、`set_system_volume`、`system_power_action`、`web_search_or_open_url` | `actions` | 測試的 `r.<名稱>`；搬走要改 `router/__init__.py` |

### 10.7 I7 測試耦合

**`monkeypatch` 打在這兩個模組上的 17 處**（V5）：

| 目標 | 次數 | 測試檔 |
|---|---|---|
| `local_actions._now` | 5 | `flow/test_route_federated.py`、`test_route_music.py`、`test_route_weather.py`、`test_route_web_answer.py` |
| `local_actions.MUSIC_DB` | 3 | `conftest.py`（autouse）、`runtime/test_music_db_runtime.py`、`unit/test_music_db.py` |
| `local_actions.LOCAL_TOOLS["play_media"]` | 2 | `flow/test_route_music.py` |
| `local_actions._rng` | 1 | `conftest.py`（autouse） |
| `local_actions._run` | 1 | `unit/test_choice.py` |
| `actions.read_screen` | 3 | `flow/test_route_weather.py`、`real/test_llm_live.py`、`flow/test_route_guard.py`（寫成 `r.actions`） |
| `actions._dispatch` | 1 | `unit/test_completeness.py` |
| `actions.CLOUD_IMAGE_MAX_BYTES` | 1 | `flow/test_route_cloud.py` |

**測試直接用到的私有名稱**：

| 名稱 | 測試檔 |
|---|---|
| `actions._TAB_SCRIPTS`、`_FRONT_AND_RUNNING_SCRIPT` | `flow/test_route_tabs.py`、`runtime/test_applescript.py` |
| `actions._MUSIC_TAB_SCRIPTS`、`_FRONT_BROWSER_WINDOW_SCRIPT`、`_front_browser_window` | `runtime/test_applescript.py` |
| `actions._SAFARI_MUSIC_TAB_SCRIPT` | `flow/test_route_music.py` |
| `actions._run` | `flow/test_route_multistep.py`、`real/test_app_real.py`、`runtime/test_applescript.py` |
| `actions._strip_think` | `unit/test_llm_client.py` |
| `local_actions._SAFARI_TABS_SCRIPT` | `flow/test_route_music.py`、`runtime/test_applescript.py` |
| `local_actions._filter_results`、`_rank` | `unit/test_find.py` |
| `local_actions._rng` | `flow/test_route_music.py` |

- 直接 import 或用屬性取這兩個模組的測試檔：`actions` 13 個、`local_actions` 8 個。
- 外部指令多半是在 `conftest.py` 直接換掉 `subprocess.run`，搬移函式不影響這一層。

### 10.8 I8 文件與規則的引用

| 位置 | 內容 | 拆分後要不要改 |
|---|---|---|
| `.importlinter` C2、C3 | `local_actions`、`actions` 的層級 | 要（新模組分層，先問使用者） |
| `docs/flows/module-deps.md` | 分層表、依賴表、結構債 | 要 |
| `docs/flows/main-flow.md`（2 處）、`flows/README.md`（1 處） | `actions.run_tool` | 搬 `run_tool` 才要（T9） |
| `ARCHITECTURE.md` 模組表（`local_actions`、`actions` 兩列）與分層表 | 模組職責 | 要 |
| `docs/guides/testing.md` L0 對照表 | 5 列提到 `local_actions.py`、2 列提到 `actions.py` | 要 |
| `docs/guides/coding-style.md` | `actions.front_context`、`actions.LOCAL_TOOLS`、「新增本機工具寫在 `local_actions.py`」「`actions.py` 加執行函式」 | 要 |
| `docs/decisions/0005`、`0022`、`0023`、`0025`、`0026`、`0028` | 提到舊位置 | 不改（歷史紀錄）；新 ADR 說明新位置 |
| `rules/secret-read-scope.yml` | 用函式名 `get_weather` 放行 | 搬移不用改 |
| `rules/subprocess-needs-timeout.yml` | regex 認 `_run`、`actions._run`、`proc._run` | 新模組若用 `某模組._run(...)` 的寫法會漏抓；直接寫 `_run(...)` 不用改 |
| `rules/no-sleep-in-router.yml` | 範圍是 `router/**`，`_wait_for_page` 的 `ast-grep-ignore` 註解要跟著函式搬 | 規則不用改 |

### 10.9 I9 模組層級的狀態

| 狀態 | 所在 | 誰會換掉或改它 |
|---|---|---|
| `LOCAL_TOOLS`（dict） | `actions` | `local_actions` 載入時登錄；測試 `setitem` 換 `play_media` |
| `AVAILABLE_FUNCTIONS`（dict） | `actions` | 沒有人改 |
| `MUSIC_DB` | `local_actions` | `main.py` 呼叫 `start()`；`conftest`（autouse）換成暫存目錄 |
| `_now`（`time.time`） | `local_actions` | 測試換掉 |
| `_rng` | `local_actions` | `conftest`（autouse）固定種子 |
| `_FOLDERS`、`_KINDS`（由詞庫算出） | `local_actions` | 載入時讀一次 |
| `choices.STORE`、`sources.STORE`、`weather_followup.STORE` | 各自的模組 | 由本機工具寫入；拆分不用搬它們 |

### 10.10 I10 執行緒與時機

| 事情 | 執行緒 |
|---|---|
| 所有工具（語音與點選清單） | `command_worker`（點選和語音排同一個佇列）；`--text` 模式是主執行緒 |
| `federated_search` 的 `_local_hits`、`_web_hits` | `ThreadPoolExecutor(2)`；兩者不碰 `ctx` 和資料庫 |
| `play_media` 的搜尋 | `youtube_client` 自己的執行緒；資料庫仍在呼叫端執行緒 |
| `_wait_for_page` | 在 `command_worker` 上 `sleep`，有上限（唯一的例外） |
| 啟動 | `main.py`：`MUSIC_DB.start()`（背景升級 schema）→ 通知執行緒、`pinyin.warm` 執行緒；`--text` 模式 `wait_ready(30)` 後才執行 |

`remember_home` 的「在背景記下」指的是不出泡泡，實際上仍在同一個執行緒同步寫入。

### 10.11 驗收結果

| # | 結果 |
|---|---|
| V1 | 通過：§10.1 的定義名稱和 `ast` 的頂層定義集合相同（119 個，0 遺漏、0 重複） |
| V2 | 通過：§10.5 的 24 列等於 `AVAILABLE_FUNCTIONS`、`LOCAL_TOOLS`、`TOOLS_SCHEMA`、`CLOUD_ONLY_TOOLS`、`WEB_ANSWER_TOOL` 的名稱聯集；23 個各有一個實作，`site_search` 指到 `context_search` |
| V3 | 通過：§10.2 兩張表和重算的跨功能引用邊相同 |
| V4 | 通過：§10.4 的 19 列等於 `dataclasses.fields(ExecContext)` |
| V5 | 通過：§10.7 表格合計 17 ＝ grep 16 處＋ `r.actions` 寫法 1 處 |
| V6 | 通過：盤點前後 `actions.py`、`local_actions.py` 雜湊不變；本次只寫過本計畫檔。盤點期間 `deps-converge.md`（補上 §10）和 `tests/runtime/test_import_footprint.py` 由別的流程改過（22:35），不是本次改的；後者目前 6 passed，§10.0 第 4 點引用的檢查仍在 |
| H1 | 通過：固定亂數種子抽 9 列回原始碼核對，I2（`_play_search_page`、`_played`、`web_search_or_open_url`）、I3（`federated_search`、`_safari_pid`、`_pick_tab_browser`）、I4（`chat_done`、`escalate`、`results`）全部正確 |
| V7 | 通過：§10.12 五塊合計 1226 行 ＝ `wc -l`；100 個測試函式 ＝ AST 的 `test_*` 數；425 個案例 ＝ `pytest --collect-only`。I12 前後 `actions.py`、`local_actions.py`、`test_route_music.py` 雜湊不變，本次只寫過本計畫檔 |
| H1 | 通過：固定亂數種子抽 9 列回原始碼核對，I2（`_play_search_page`、`_played`、`web_search_or_open_url`）、I3（`federated_search`、`_safari_pid`、`_pick_tab_browser`）、I4（`chat_done`、`escalate`、`results`）全部正確。I12 另抽 3 項：`_seed_song` 被 K8、S8 用（1048、1138 行）、`local_actions._rng` 是讀不是 patch（1075 行）、S4 同時用 `bg` 和 `net`（1215 行），都正確 |
| H2 | **待使用者** |

**已知漏洞**
- 用腳本找「誰發出某個工具」時，漏掉條件運算式組出的名稱（`fast_path.py:122` 的 `context_search`）和 `slow_path` 的 `site_search` 換名；§10.5 這兩處是人工補的。可能還有同類寫法沒被發現。
- §10.2 只算函式與常數的引用；型別標註的字串（例如 `"youtube_client.SearchResult"`）和模組層級的敘述（`LOCAL_TOOLS.update`）不算邊。
- §10.4 只掃 `actions`、`local_actions`、`pipeline`、`slow_path`、`memory`、`fast_path`，而且只認變數名叫 `ctx` 的存取。
- 沒有跑任何執行期追蹤，全部是靜態分析。
- §10.12 的「測到的功能組」是看節標題、工具名稱字串和進入點手動歸的；整條路由（`process_voice_command`）的測試實際會經過更多程式。

**尚未做**
- I11：等使用者提出候選拆法。

### 10.12 I12 `tests/flow/test_route_music.py`（1226 行、100 個測試函式、425 個案例）

**按測的東西分成 5 塊**（依檔案順序；「節」是檔案裡的 `# ----------` 標題）：

| 區塊 | 行 | 行數 | 節 | 函式／案例 | 測的程式（§10.1 功能組） | 進入點 | 用到的 fixture |
|---|---|---|---|---|---|---|---|
| A 路由語料與守門 | 1–129 | 129 | 檔頭（import、C1／C2 語料）、C1、C2、C4 | 8／139 | `fast_path._r_music` 與守門；`play_media` 用 `LOCAL_TOOLS` 換成假的 | `fast_matches`、`process_voice_command` | `sh` |
| B 背景音樂控制 | 130–491 | 362 | K（`bg` fixture）、K1、K2、K3、H4、K4、暫停／繼續方向、K5、K9、既有行為的改變 | 32／146 | 站內搜尋／常用功能（`front_action`）、背景音樂控制 | `process_voice_command`（K1 也用 `fast_matches`） | `bg`、`front`、`native_calls` |
| C 點歌 | 492–861 | 370 | Y（中段 import、`tabs`／`mclock`、helper）、Y12、Y13、Y10、Y8／Y9、Y11、Y14、只說歌手、Y15、Y16、S6 | 27／33 | 點歌、音樂分頁開啟、偏好 DB 共用 | `process_voice_command`（Y11、Y15 直接呼叫函式） | `net`、`tabs`、`mclock`、`music_db` |
| D 回饋路由 | 862–922 | 61 | P5 後段 | 6／57 | `fast_path._r_music_feedback`、`completeness`、工具可見性 | `fast_matches`、`process_voice_command` | `sh`、`music_db` |
| E 播放事件／回饋 | 923–1226 | 304 | K7、K8、S8、播放控制寫事件與 S4 | 27／50 | 播放事件／回饋（`music_replace`、`music_feedback`、`music_top`、`_record_control`） | `process_voice_command`（K8 也直接呼叫 `music_feedback`） | K7、S8：`net`、`tabs`、`mclock`、`music_db`；K8、寫事件：`bg`、`mclock`、`music_db`（寫事件另加 `front`、`native_calls`、`net`） |

- 相鄰兩塊合併：A＋B 491、B＋C 732、C＋D 431、D＋E 365，都 ≤800。三塊以上：B＋C＋D 793、C＋D＋E 735 ≤800；A＋B＋C 861 超標。
- A、D 測的主要是 `fast_path`、`completeness`，不是 `actions`／`local_actions`。

**跨塊共用的測試設施**（定義在一塊、別塊也用；拆檔時要放到大家都拿得到的地方）：

| 名稱 | 種類 | 定義在 | 行數 | 也被誰用 |
|---|---|---|---|---|
| `bg`（假 `subprocess.run`、`native` 的按鈕／媒體鍵／讀標籤） | fixture | B（141 行） | 37 | E（K8、寫事件） |
| `_control` | helper | B | 3 | E（寫事件、S4） |
| `WATCH`、`SAFARI_PID` | 常數 | B | 各 1 | E（K8、寫事件） |
| `P_NEXT`、`P_PREV`、`P_PAUSE`、`P_PLAY`、`P_TOGGLE`、`LEGACY_YT_BUTTON` | 常數 | B | 各 1 | 只在 B 內 |
| 中段 import（`json`、`threading`、`urllib.error`、`config`、`music_prefs`、`Clock`、`support.youtube` 的 `net` 等） | import | C（494–501 行） | 7 | C、D、E |
| `tabs`（假 `subprocess.run`：osascript／open／pgrep） | fixture | C（511 行） | 18 | E（K7、S8） |
| `mclock`（patch `local_actions._now`） | fixture | C（531 行） | 5 | E（K7、K8、S8、寫事件） |
| `_step` | helper | C | 2 | E |
| `_rows`、`_events` | helper | C | 2、3 | D、E |
| `Y_QUERY`、`Y_ID`、`Y_URL`、`T0`、`DAY` | 常數 | C | 各 1 | E（`T0` 全部；`Y_ID` 寫事件、K7；`DAY` K7） |
| `VID_A`、`_seed_song` | 常數、helper | E（K8） | 1、6 | E（S8） |
| `C1_POSITIVE`、`C2_*` | 常數 | A（檔頭） | 23＋14 | 只在 A 內；**`tests/unit/test_completeness.py` 用檔案路徑載入整個檔取 `C1_POSITIVE`** |

- `net` fixture 本來就放在 `tests/support/youtube.py`，用 import 帶進來（`ruff.toml` 對 `tests/**` 忽略 `F811`、`E402`）。`tests/flow/` 目前沒有 `conftest.py`。
- `bg`、`tabs` 都換掉全域的 `subprocess.run`，不綁 `actions`／`local_actions`，搬正式程式不影響它們。
- 最複雜的跨塊測試：`test_s4_skip_penalty_through_the_real_route`（E）同時用 B 的 `bg`／`_control` 和 C 的 `net`／`_step`／`T0`／`Y_ID`。

**對 `actions`／`local_actions` 的引用**（23 處＝grep 屬性存取 21＋字串形式 `setattr` 1＋from-import 1；搬程式時要跟著改的就是這些）：

| 名稱 | 所在功能組（§10.1） | 次數 | 用法 | 區塊 |
|---|---|---|---|---|
| `local_actions.LOCAL_TOOLS["play_media"]` | 分派（定義在 `actions`，`local_actions` 有同一個物件） | 2 | `setitem`（patch） | A |
| `local_actions._now` | 偏好 DB 共用 | 1 | `setattr`（`mclock`，patch） | C |
| `local_actions._rng` | 偏好 DB 共用 | 1 | 讀 | E |
| `local_actions.MUSIC_DB` | 偏好 DB 共用 | 1 | 讀（另有 `conftest` 的 autouse 與 `music_db` fixture，見 §10.7） | E |
| `local_actions.NO_DB_FAVORITE_MESSAGE` | 點歌 | 1 | 比對訊息 | C |
| `local_actions.NO_DB_MUSIC_MESSAGE`、`NO_CANDIDATE_MESSAGE`、`NO_RECENT_PLAY_MESSAGE`、`NO_MUSIC_TAB_MESSAGE` | 播放事件／回饋 | 3＋2＋2＋2 | 比對訊息 | E |
| `local_actions._SAFARI_TABS_SCRIPT` | 背景音樂控制 | 2 | 比對腳本 | B、E |
| `local_actions.front_action` | 站內搜尋／常用功能 | 1 | 直接呼叫 | B |
| `local_actions.play_media` | 點歌 | 1 | 直接呼叫 | C |
| `local_actions.music_feedback` | 播放事件／回饋 | 1 | 直接呼叫 | E |
| `actions._SAFARI_MUSIC_TAB_SCRIPT`、`actions.open_music_video` | 音樂分頁開啟 | 1＋1 | 比對腳本、直接呼叫 | C |
| `from mac_ai.router.actions import LOCAL_TOOLS` | 分派 | 1 | 讀 | D |

- 其他正式程式名稱（不在本次拆分範圍）：`music_prefs` 二十多個（含私有的 `_nq`、`_decay`）、`context.SHORTCUTS`、`assistant._LOGGED_STEP_KEYS`、`tools.TOOLS_SCHEMA`、`config` 的兩個 `clientVersion`、`mac_ai.db.rebuild`、`completeness.classify`。

**檔外引用這個檔的地方**（拆檔或改名要跟著改）：

| 位置 | 引用方式 |
|---|---|
| `tests/unit/test_completeness.py:52` | 用路徑載入整個檔、取 `C1_POSITIVE`（會執行這個檔所有頂層 import） |
| `docs/guides/testing.md` 第 54、58、61 行 | 分層對照表：點歌規則、`youtube_client`、`play_media`／播放控制鏈各列要跑的檔 |
| `docs/guides/coding-style.md` 第 63 行 | 改點歌規則時，誤觸發案例要加進這個檔的反例 |
| 已結案計畫、ADR 0028 | 歷史紀錄，不改 |

## 11. 實作結果（2026-11-26）

### 11.1 決定

- 使用者 2026-11-26：「直接開始實作依照現有分類拆分」。I11（候選拆法的指標）和 H2 不另外做，直接用 §10.1 的歸類，每個功能組整組放進一個模組。對應表、分層與規則見 [ADR 0030](../../decisions/0030-router-feature-modules.md)。
- 爭議項（§10.0 第 6 點）全部照 §10.1 放：`pick_choice` 放 `find_tools`、`context_search` 放 `front_tools`、`_record_control` 放 `music_tools`、`today_taipei` 放 `answer_tools`、`TAB_BROWSERS` 放 `browser_tools`。

### 11.2 改了什麼

| 類別 | 內容 |
|---|---|
| 新模組 | `exec_context`（91 行）、`browser_tools`（87）、`system_tools`（87）、`screen_tools`（182）、`chat_tools`（63）、`prefs_db`（22）、`music_tools`（462）、`answer_tools`（170）、`front_tools`（107）、`find_tools`（331） |
| 縮小 | `actions` 643 → 83 行（只剩分派）、`local_actions` 973 → 14 行（只剩登錄） |
| 搬移方式 | 腳本依 §10.1 的行號從 HEAD 切出，函式本體逐字不變。只改了：`prefs_db` 的狀態改成用模組屬性存取（§10.0 第 3 點）；`music_watch_url` 的函式內 import 改用模組頂層已 import 的 `youtube_client` |
| 使用端 | `pipeline`、`slow_path` 改從新模組 import，並明寫 `import local_actions`（登錄）；`router/__init__.py` 的 `_EXPORTS` 改指新模組；`main.py`、`bench/web_answer*.py` 改用 `prefs_db`、`answer_tools` |
| 契約 | `.importlinter` 的 C2 加 9 個模組，C3 加 `prefs_db`，C3 的 forbidden 加 9 個；沒有新增 `ignore_imports` |
| 測試 | §10.7 的 patch 目標全部改到新模組（`prefs_db._now`／`MUSIC_DB`／`_rng`、`find_tools._run`、`screen_tools.CLOUD_IMAGE_MAX_BYTES`；`actions.read_screen`、`actions._dispatch` 不用改）；`test_route_music.py` 依 §10.12 拆成 4 個檔＋`tests/support/music.py`；`test_import_footprint.py` 的 HEAVY 加入新模組；`test_domain.py` 的「第二層不讀原句」改成檢查全部 `*_tools` 模組 |
| 文件與規則 | `ARCHITECTURE.md`、`README.md`、`docs/flows/module-deps.md`、`main-flow.md`、`coding-style.md`、`testing.md`；`rules/hot-path-no-io.yml`、`secret-read-scope.yml` 的說明文字（規則本身沒改）；ADR 0026、0029 補上指向 0030 的說明 |

### 11.3 驗收（已實際執行）

| 項目 | 結果 |
|---|---|
| 函式本體不變 | `test_route_music.py` 拆分前後 139 個頂層定義的原始碼逐一比對相同；收集到的 425 個測試 ID 和 HEAD 完全相同 |
| `pytest`（離線全部） | 3373 passed、6 skipped、0 failed，約 31 秒。另一次跑出 `test_xasr_server.py::test_unknown_http_path_is_404` 的 teardown 逾時 1 error，在乾淨的 HEAD worktree 也會出現，`testing.md` 已記錄為既有問題 |
| `ruff check .` | 0 違規 |
| `lint-imports` | 5 個契約全部 KEPT，例外仍是 3 處（C1 1、C4 1、C5 1） |
| `ast-grep test`／`scan` | 7 passed／0 違規 |
| `check_file_lines.py` | router 與測試都 ≤800。`desktop/test/attention.test.js`（1041）不在範圍（E4），但會讓 CI 的 file-lines job 一直失敗，使用者決定在同一個 PR 另外拆（見下一列），之後 exit 0 |
| `desktop/` | `attention.test.js` 拆成 `attention.test.js`（情境 1～6）與 `attention-cursor-idle.test.js`（游標、閒置、點頭、優先順序），共用 helper 放 `test/support/attention.js`；拆分前後都是 40 個 test，名稱相同。`node --test` 310 pass（原本 309，多的 1 個是 `support/attention.js` 被當成沒有 `test()` 的測試檔載入）、`npm run typecheck`、`npm run lint` 都通過 |
| 速度 | `import mac_ai.router.pipeline` 中位數：HEAD 61.3／61.0 ms，拆分後 61.0／62.0 ms（各 15 次）。`bench/routing_latency.py`：p50 0.013、p95 0.090 ms（門檻 0.098），和 HEAD 相同 |

### 11.4 尚未驗證

- `pytest -m app`（真的 App）、live／cloud、語音實機、CI。
