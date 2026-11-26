# 依賴收斂 P1、P2：底層工具歸位、比對與執行分開

狀態：**草案，等使用者確認 §2 的 D1～D8 後才實作**。不改任何行為；只搬程式碼、改 import、改契約與文件。P3（`actions`／`local_actions` 依功能拆分）另見 [盤點計畫](2026-11-26-router-split-inventory.md)。

## 0. 目標與結論

**目標**（使用者 2026-11-26）：統一架構，讓依賴更簡潔、明瞭。收斂 [module-deps.md](../../flows/module-deps.md) 記錄的例外與循環，但不影響「快」與「準」。

- **P1 底層工具歸位**：`_run` 從 `actions` 移到新模組 `proc`；拼音比對從 `music_prefs` 移到新模組 `pinyin`。
- **P2 比對與執行分開**：`ACTION_TABLE`／`plan_actions` 從 `local_actions` 移到新模組 `action_plan`；刪掉 `Match.run`，改由 `pipeline` 執行；`router/__init__.py` 改成用到才 import。
- **結果**（grimp 3.19 在記憶體裡模擬搬移後的圖，§1.4）：
  - 例外從 5 條降到 **2 條**（`ignore_imports` 從 7 行降到 3 行）。
  - **全專案沒有循環**。
  - C4 從「不准再惡化」的棘輪，變成只剩一條有明確理由的例外。
  - `stt.completeness` 不再間接連到執行器、5 個網路 client 與資料庫。
- **更正上一輪分析**：上一輪說例外會降到 1 條，這是錯的。`catalog` 本身就要跑 `mdfind`，所以 `catalog → proc` 一定會留在 C4、C5（見 D3）。

## 1. 現況（2026-11-26 實測，沒改 repo）

### 1.1 契約

`lint-imports`：5 kept。被忽略的 import 數：C1 1、C2 0、C3 1、C4 3、C5 2。

### 1.2 實際執行時的 import 範圍（`.venv`，每項各開一個 Python 行程）

| import | 載入的 `mac_ai` 模組數 | 耗時 | 有載入 `llm_client`／`cloud_client`／`mac_ai.db`／`subprocess`／`sqlite3` |
|---|---|---|---|
| `mac_ai.router.lexicon` | 31 | 91 ms | 全部有 |
| `mac_ai.wake` | 32 | 64 ms | 全部有 |
| `mac_ai.stt.completeness` | 34 | 64 ms | 全部有 |
| `mac_ai.router.fast_path` | 31 | 59 ms | 全部有 |

原因：載入任何 `mac_ai.router.*` 之前，Python 都會先執行 `router/__init__.py`，而它在頂層 import 了 `actions`、`pipeline`、`slow_path` 等模組。grimp 不把父套件的 `__init__` 算成依賴，所以 import-linter 看不到這條路。

### 1.3 每條例外的根因

| 依賴 | 根因 | 處理 |
|---|---|---|
| `catalog → actions`（函式內 import，造成循環） | 只用到 `_run`，但 `_run` 和需要 LLM 的 `read_screen`、`chat_reply` 放在同一檔 | P1：`_run` 移到 `proc` |
| `youtube_client → music_prefs` | 只用到純函式 `pinyin_units`，但它放在使用 sqlite 的模組裡 | P1：拼音那一節移到 `pinyin` |
| `fast_path → local_actions` | `plan_actions`／`ACTION_TABLE` 是純查表（[local_actions.py:50-85](../../../mac_ai/router/local_actions.py#L50-L85)，只依賴 `Intent`），但放在執行模組裡 | P2：移到 `action_plan` |
| `fast_path → actions` | `Match.run` 只有一行 `run_tool` 迴圈，呼叫端只有 `pipeline` 的 3 處（[pipeline.py:56](../../../mac_ai/router/pipeline.py#L56)、[:80](../../../mac_ai/router/pipeline.py#L80)、[:166](../../../mac_ai/router/pipeline.py#L166)） | P2：改由 `pipeline` 執行 |
| `db.rebuild → music_prefs` | 維護用 CLI 重用計分邏輯 | 保留 |

### 1.4 搬移後的模擬結果（grimp 3.19，在記憶體裡加刪邊，沒改檔案）

- C3（底層 → 編排層）：0 條鏈。
- C2：0 條違規。
- 循環：0 組。
- C4 剩 3 條鏈、C5 剩 2 條鏈，**全部經過 `catalog → proc → subprocess`**：
  - `fast_path → catalog → proc`
  - `stt.completeness → fast_path → …`
  - `stt.endpoint → …`
  - `llm_client／cloud_client → tools → catalog → proc`

### 1.5 會受影響的測試與靜態檢查

- **`_run`**：
  - [test_find.py:33](../../../tests/unit/test_find.py#L33)、[:43](../../../tests/unit/test_find.py#L43) patch 了 `actions._run`，目的是影響 `catalog._scan_installed`（它在函式內 import）。搬移後要改成 patch `catalog._run`。
  - [test_choice.py:105](../../../tests/unit/test_choice.py#L105) patch `la._run`。只要 `local_actions` 頂層仍有 `_run` 這個名稱，就不用改。
  - 另外 5 處直接呼叫 `actions._run`（runtime、real、flow 測試）。`actions` 從 `proc` import `_run` 後，這 5 處不用改。
- **拼音**：
  - `test_pinyin.py`、`test_music_prefs.py`（`mp.pinyin_key`、`mp.warm`）、`runtime/test_youtube_search.py`（`music_prefs._han_syllable`、`pinyin_syllables`）、`runtime/test_music_db_runtime.py`（字串裡的 `mp.warm()`）。
  - `main.py:140` 的 `music_prefs.warm`。
- **`Match`**：`tests/` 沒有呼叫 `.run(ctx)` 或 `Match(...)`。`mac_ai/` 裡只有 `pipeline` 的 3 處，加上 `Match.__call__` 自己呼叫 `run`。
- **`router/__init__`**：39 個測試檔用 `import mac_ai.router as r` 取重新匯出的名稱，約 500 處，其中 `r.process_voice_command` 有 265 處。
- **ast-grep**：
  - `catalog-lazy-actions-import`：延遲 import 消失後，這條規則就失去意義（D4）。
  - `subprocess-needs-timeout` 的 regex 是 `^(actions\.)?_run$`，抓不到 `proc._run(...)` 這種寫法。
  - `hot-path-no-io` 的 `files` 沒有 `action_plan.py`；它的 note 寫「經 Match.run／run_tool 呼叫」。

## 2. 待使用者決定

| # | 問題 | 建議 | 理由／替代方案 |
|---|---|---|---|
| D1 | 新模組名稱 | `mac_ai/router/proc.py`（`_run`）、`pinyin.py`、`action_plan.py`；`_run` 保留原名 | 保留原名，ast-grep 的 `(^|\.)_run$` 和 5 處直接呼叫都不用改 |
| D2 | `router/__init__.py` 怎麼改 | **用 PEP 562 的 `__getattr__` 加名稱→子模組對照表**，用到才 import | 39 個測試檔和 `main.py` 都不用改。替代方案：清空 `__init__`，改 39 個測試檔約 500 處加上 `main.py`，比較徹底但改動大 |
| D3 | `catalog → proc` | 留作 C4、C5 的例外，寫明原因 | `catalog` 是比對路徑上唯一會跑子程序的地方：只在快取是空的（啟動預掃前）或 `refresh_if_stale` 時跑，`main.py:160` 啟動時會先掃。要消掉得改 `catalog.DEFAULT` 的建立方式（由組裝處注入掃描函式），測試和 bench 預設也會跟著變，不在本計畫範圍 |
| D4 | ast-grep 規則 `catalog-lazy-actions-import` | **刪除**規則、它的 rule-test 和 snapshot；AGENTS「不要碰」與 coding-style 第 77 行的對應條目也刪掉 | 保護的對象（延遲 import）已經不存在。這屬於修改 `rules/`，需要你同意；收尾的 ADR 要記錄 |
| D5 | `Match.run`／`__call__` | 刪掉，`pipeline` 加私有函式 `_run_match`，不留相容層 | 只有 3 個呼叫端，留著會讓 `fast_path` 仍然要 import `actions` |
| D6 | `LOCAL_TOOLS` 的登錄方式 | 本計畫**不改**，仍在 `local_actions` 載入時登錄；另加一個測試，確認只 import `pipeline` 就會登錄全部 14 個工具 | 搬移後的實際路徑是 `pipeline → local_actions`，`pipeline` 本來就要用 `record_explicit_search`。登錄方式和功能拆分一起在 P3 決定 |
| D7 | 新增 import 範圍的回歸測試 | 新增 `tests/runtime/test_import_footprint.py`，在子程序裡 import 後檢查 `sys.modules` | import-linter 看不到父套件 `__init__` 造成的載入（§1.2），這是唯一能防止退回原狀的檢查。要開子程序，所以放在 runtime 層 |
| D8 | 分支 | 從 `chore/lint-ci` 開 `refactor/deps-converge` | `chore/lint-ci` 還有 import-linter 計畫的 H1、H2、R1 沒做完，分開比較好回退 |

## 3. 改完之後的 `.importlinter`

| 契約 | 被忽略的 import | 其他變更 |
|---|---|---|
| C1 | `db.rebuild → router.music_prefs`（不變） | — |
| C2 | 無 | — |
| C3 | 無（刪掉 `catalog → actions`） | `source_modules` 加 `proc`、`pinyin`、`action_plan`（T11 會檢查） |
| C4 | `catalog → proc`（原因：§2 D3） | `source_modules` 加 `action_plan`；刪掉 `fast_path → actions`、`fast_path → local_actions`、`catalog → actions` |
| C5 | `catalog → proc` | 刪掉 `catalog → actions`、`youtube_client → music_prefs` |

## 4. 實作步驟

開始前先做 S0。順序是 S1 → S2（P1，一個 commit）→ S3 → S4 → S5（P2，一個 commit）。依 flows 規範，文件和程式在同一個 commit 一起改（S6 的內容分散到各 commit）。未經要求不 commit。

| 步驟 | 內容 | 驗證 |
|---|---|---|
| S0 | 記錄基線：`lint-imports` 的輸出、`pytest -m "unit or flow"` 與 `pytest` 的項數、§1.2 的 import 範圍；先寫 D7 的測試，確認在 HEAD 上**會失敗** | 數字寫進 §10 |
| S1 | 新增 `proc.py`（`_run` 原樣搬過去，連同 `subprocess` import）；`actions`、`local_actions`、`catalog` 改成在頂層 `from mac_ai.router.proc import _run`，刪掉 `catalog` 的函式內 import；`test_find.py` 兩處 patch 目標改成 `catalog`；處理 D4；`subprocess-needs-timeout` 的 regex 改成 `^((actions|proc)\.)?_run$`（這是收緊範圍，不是放寬）；`.importlinter` 照 §3 改 | A1 的部分項目、A6、`tests/unit/test_find.py tests/unit/test_choice.py` |
| S2 | 新增 `pinyin.py`：`music_prefs` 的「拼音」那一節原樣搬過去（`_ICU_*`、`_HAN*`、`_UNIT_RE`、`_icu_*`、`_u16`、`_open_icu`、`_icu_transliterate`、`_relax`、`_han_syllable`、`_word_syllable`、`pinyin_units`、`pinyin_syllables`、`pinyin_key`、`warm`）；`music_prefs`、`youtube_client` 改成從 `pinyin` import；`main.py` 改用 `pinyin.warm`；改 §1.5 列的測試；更新 `music_prefs` 的 docstring | A7、`tests/unit/test_pinyin.py tests/unit/test_music_prefs.py tests/unit/test_youtube_client.py tests/runtime/test_youtube_search.py tests/runtime/test_music_db_runtime.py` |
| S3 | 新增 `action_plan.py`（`_plan_*`、`ACTION_TABLE`、`plan_actions` 原樣搬過去）；`fast_path` 改成從這裡 import；`hot-path-no-io` 的 `files` 加 `action_plan.py`（收緊） | A7、`tests/unit/test_domain.py tests/unit/test_find.py tests/flow/test_route_find.py` |
| S4 | 刪掉 `Match.run`／`__call__`；`pipeline` 加 `_run_match(m, ctx)`，語意和原本的 `Match.run` 一樣（`ctx` 是 None 時建立新的 `ExecContext`），換掉 3 個呼叫端；`fast_path` 刪掉 `actions` import；更新 `hot-path-no-io` 的 note | `tests/unit/test_completeness.py tests/unit/test_endpoint.py`（紅線 3）、`pytest -m flow` |
| S5 | `router/__init__.py` 依 D2 改成用到才 import（`_EXPORTS` 對照表＋`__getattr__`，名稱集合和現在完全相同）；加 D6 的登錄測試 | A4、A5、`pytest` 全部 |
| S6 | 文件：`module-deps.md`（分層表、router 表、「唯一的循環」改成「沒有循環」並說明怎麼解開、例外表 2 列、結構債）、`ARCHITECTURE.md`（新增 3 個模組列，更新 `actions`、`local_actions`、`music_prefs` 列）、`main-flow.md`（`Match.run` → `pipeline._run_match`）、`AGENTS.md`（刪掉「不要碰」裡的 `catalog` 條目）、`coding-style.md`（第 61、77 行）、`testing.md`（L0 對照表加 `pinyin.py`、`action_plan.py`、`proc.py`、import 範圍測試） | A8 |

## 5. 驗收計畫

### 5.1 自動化（本機實際執行）

| # | 項目 | 指令／操作 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| A1 | 契約 | repo 根目錄執行 `uvx --from import-linter==2.16 --with grimp==3.19 lint-imports` | 結束碼 0；`Contracts: 5 kept, 0 broken`；被忽略的 import 數 **C1 1、C2 0、C3 0、C4 1、C5 1** | 和 §1.4 的模擬不一樣 → 停下來回報差異，不自行加例外 |
| A2 | 契約仍然抓得到違規（逐項暫時改、跑完還原） | (a) `catalog.py` 頂層加 `from mac_ai.router import actions`<br>(b) `fast_path.py` 加 `from mac_ai.router import local_actions`<br>(c) `youtube_client.py` 加 `from mac_ai.router import music_prefs`<br>(d) `action_plan.py` 加 `from mac_ai.router import actions` | 每項結束碼 1。(a) C3、C4、C5 BROKEN；(b) C4 BROKEN；(c) C5 BROKEN；(d) C3、C4 BROKEN。全部還原後 `git diff --stat` 只剩本計畫的檔案 | 抓不到 → 修契約範圍 |
| A3 | 沒有循環 | scratchpad 的 grimp 腳本（§1.4 那支）改成讀真的 repo，列出 `mac_ai` 內所有互相可達的模組對 | 0 組 | 停下來回報 |
| A4 | 實際執行時的 import 範圍（D7 新測試） | `pytest tests/runtime/test_import_footprint.py -v` | `lexicon`、`wake`：`sys.modules` 裡沒有 5 個 client、`actions`、`local_actions`、`pipeline`、`slow_path`、`mac_ai.db`、`sqlite3`、`subprocess`。`fast_path`、`stt.completeness`：同一份清單，只容許 `subprocess`（經 `catalog → proc`，D3）。**S0 時在 HEAD 上要失敗，改完要通過** | 多出模組 → 找出是哪條 import 鏈，回報；不改測試的清單 |
| A5 | 本機工具的登錄（D6 新測試） | 同一個測試檔，在子程序裡只 `import mac_ai.router.pipeline` | `actions.LOCAL_TOOLS` 的鍵剛好是這 14 個：`open_folder`、`find_file`、`pick_choice`、`context_search`、`front_action`、`play_media`、`music_replace`、`music_feedback`、`music_top`、`get_weather`、`remember_home`、`web_answer`、`open_source`、`federated_search` | 回報 |
| A6 | ast-grep | `ast-grep test`、`ast-grep scan`；另外暫時注入兩處違規：(a) 在 `local_actions.py` 加 `proc._run(["x"])`（沒有 timeout），(b) 在 `action_plan.py` 加 `_run(["x"], 1)` | test 全過、規則剩 7 條；scan 結束碼 0；(a) 被 `subprocess-needs-timeout` 抓到、(b) 被 `hot-path-no-io` 抓到；還原後逐位元組相同 | 修規則範圍 |
| A7 | 搬移沒有改到內容 | scratchpad 腳本：對搬移的每個定義（`_run`、拼音那一節的所有定義、`_plan_*`、`ACTION_TABLE` 迴圈、`plan_actions`）比較搬移前後的 `ast.dump` | 全部相同。只有 import 行和 docstring 允許不同 | 對回原始碼修正 |
| A8 | 文件 | `pytest tests/unit/test_docs_flows.py -v` | 0 failed（T9 識別字存在、T10 例外表和 `.importlinter` 一致、T11 新模組有分層） | 修文件 |
| A9 | 回歸 | `pytest -m "unit or flow"`、`pytest`、`uvx ruff@0.17.6 check .`、`ast-grep scan`、`lint-imports`、`cd desktop && npm run lint && npm run typecheck && node --test` | pytest 0 failed，項數＝S0 基線＋新增的測試；`test_xasr_server.py::test_unknown_http_path_is_404` 的 teardown error 只有在 S0 基線也出現時才算可接受；其他結束碼 0、0 違規 | 修正後重跑；不改測試來配合 |
| A10 | 效能數字（只記錄，不是通過條件） | 重跑 §1.2 的量測 | 記下前後的模組數與 ms | — |

### 5.2 真實環境

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| R1 | 真實 App | `pytest -m app -v`（會佔用螢幕約 2 分鐘，**由使用者決定要不要跑**） | 0 failed |
| R2 | CI | push 後看 `lint` workflow（**push 由使用者執行**） | 4 個 job 都綠燈 |

### 5.3 人工

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| H1 | 語音冒煙測試（選做） | `python main.py` 用麥克風說「打開小算盤」「我想聽周杰倫」 | 和改動前一樣成功。STT 實機沒有自動測試，沒跑就回報「尚未驗證」 |

### 5.4 不在本次範圍

- P3：`actions`／`local_actions` 依功能拆分、`LOCAL_TOOLS` 的登錄方式、`_dispatch` 的特例。見盤點計畫。
- 用注入的方式消掉 `catalog → proc`（D3）。
- `stt → router` 的方向、`db.rebuild` 例外。
- `main-flow.md:102` 的 `LOCAL_TOOLS` 清單少了 `web_answer`、`open_source`：這是既有的文件落後，要另外問使用者要不要修。
- pytest 進 CI。

## 6. 風險與假設

- **import 順序改變**：`__init__` 改成用到才 import 之後，原本「import 任何 router 模組都會順便登錄 `LOCAL_TOOLS`」的副作用就沒了。如果有測試靠這個副作用，會在 A9 失敗。處理方式是在那個測試裡明確 import 需要的模組，**不恢復一次全部 import**。
- `ExecContext` 仍在 `actions` 裡。`pipeline`、`slow_path` 依賴 `actions` 的方向本來就合法（C2），本計畫不動。
- `_run` 搬到 `proc` 後，`actions._run`、`local_actions._run`、`catalog._run` 是同一個函式在三個模組裡的名稱。patch 時要 patch 呼叫端所在的模組，這和現在一樣，S1 只有 `test_find.py` 要改。
- §1.4 是在記憶體裡模擬的結果，以 A1 的實測為準。
- 拼音的 ICU 鎖和 `lru_cache` 搬移後仍然是模組層級的單例，`music_prefs` 和 `youtube_client` 會用到同一份，這和現在一樣。

## 7. 收尾（計畫結案時）

- 新增 ADR 0028（依賴收斂：`proc`、`pinyin`、`action_plan` 三個模組的定位；`Match` 只保留資料；`__init__` 用到才 import；`catalog → proc` 例外的理由；刪掉 `catalog-lazy-actions-import` 規則）。
- ADR 0025、0026 的「後果」各加一行指向 0028，比照 0025 → 0026 的做法。
- 本檔移到 `completed/`。

## 10. 實測結果

（實作時填寫：分「已實際執行確認」與「尚未驗證」。）
