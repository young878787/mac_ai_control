# 0025 靜態檢查分工：Ruff、ESLint 自訂規則、ast-grep 專案規則

- 狀態：採用
- 日期：2026-11-26
- 來源：[計畫書](../plans/completed/2026-11-26-ast-grep-rules.md) §2；使用者 2026-11-26 的決定

## 背景

紅線與 ADR 原本只靠文件與 pytest 守住。AI 改程式時常在語法層就違反（例如新的 `_run` 沒帶逾時、熱路徑 import 網路模組），等到測試或 review 才發現。commit `6f22477` 先加了 Ruff、ESLint 自訂規則與 lint CI；Python 端的專案規則還沒有工具擋。

## 決定

1. **分工**：
   - **Ruff**（`ruff.toml`）：Python 通用 bug 規則（F、E4、E7、E9、B），不做格式化與純風格規則，避免大量純格式 diff。
   - **ESLint**（`desktop/eslint.config.js`＋`desktop/eslint-rules/`）：JS 通用規則與桌面紅線（不搶焦點、`webPreferences` 安全設定、IPC 頻道常值、匯流排只連本機）；自訂規則的測試在 `desktop/test/eslint-rules.test.js`。
   - **ast-grep**（`sgconfig.yml`＋`rules/`＋`rule-tests/`）：**只管 Python 的專案規則**，把 ADR／紅線中看語法就能判斷的部分寫成規則。JS 不用 ast-grep，避免兩套工具管同一批檔案。
   - 需要資料流或資料內容才能判斷的（結果前綴 ✅／❌、`open -a` 名稱驗證、送按鍵白名單、搜尋摘錄不寫 log、LLM 看不到的工具），繼續由 pytest 守。
2. **ast-grep 規則**（每條的 `message` 寫依據、`note` 寫正確做法）：

   | id | 依據 |
   |---|---|
   | `hot-path-no-io` | 0001、0003 |
   | `urlopen-only-in-clients` | 0002 |
   | `prompt-prefix-immutable` | 0002 |
   | `no-environ-write` | 0010 |
   | `secret-read-scope` | 0010、0021、0022 |
   | `catalog-lazy-actions-import` | AGENTS「不要碰」 |
   | `subprocess-needs-timeout` | 紅線 12 |
   | `no-sleep-in-router` | 紅線 12 |

3. **工具版本鎖定、用 `uvx` 執行**，不進 requirements：`uvx ruff@0.17.6`、`uvx --from ast-grep-cli@0.51.2 ast-grep`。一律在 repo 根目錄執行（規則的 `files`／`ignores` 以此為基準）。升級版本時要重跑 `ast-grep test` 與 `scan`。
4. **外部指令一律有逾時**（落實紅線 12）：原本沒帶逾時的 `_run` 呼叫（`open`、`osascript`、`pmset`）改用 `EXTERNAL_TIMEOUT_S`（15 秒）；`services.kill_stt_backend` 的 `ps` 設 2 秒，逾時視為不是後端、不送訊號。
5. **例外與放寬**：
   - 個別例外用壓制註解（`# ast-grep-ignore: <id>`、`# noqa: <code>`、`// eslint-disable-next-line <rule>`），一定要寫理由；不得整檔壓制。
   - 目前唯一的 ast-grep 例外：`actions._wait_for_page` 的 3 處 `time.sleep`（同句開網頁後讀畫面，有上限）。
   - 放寬規則本身（改 `ruff.toml`、`eslint.config.js`、自訂規則、`rules/` 的 pattern、`files`、`ignores`）等同改決策，先問使用者。

## 後果

- 本機 commit 前與 CI（`.github/workflows/lint.yml` 的 `ruff`、`ast-grep`、`desktop` 三個 job）都會擋。CI 不跑 pytest（依賴 Apple 限定套件），綠燈不代表 pytest 通過。
- ast-grep 逐檔比對，看不到間接 import；熱路徑的傳遞性保證仍靠行為測試（例如 `tests/unit/test_domain.py` 把 `_scan_installed` 換成會丟例外的版本）。
- 模組依賴圖（含間接 import）後來改由 import-linter 檢查，見 [0026](0026-import-linter.md)。
- 規則表的 `catalog-lazy-actions-import` 在依賴收斂後刪除，見 [0029](0029-deps-converge.md)。
- 一般動作的外部指令卡住超過 15 秒時回 ❌（動作可能稍後仍完成）；正常情況不影響延遲。
