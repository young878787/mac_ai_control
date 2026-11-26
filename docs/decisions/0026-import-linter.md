# 0026 import-linter：Python 模組依賴契約

- 狀態：採用
- 日期：2026-11-26
- 來源：[計畫書](../plans/completed/2026-11-26-import-linter-flows.md) §2、§3；使用者 2026-11-26 的決定

## 背景

使用者要求確保程式依賴正確、不跨過太大的模組、流程不混亂。[0025](0025-static-checks.md) 的 ast-grep 規則逐檔比對，看不到間接 import 與整個依賴圖；ast-grep 計畫書 §0 原本寫「不加 import-linter」，那句只在計畫書裡、不是 ADR，這次依使用者決定改變做法。

2026-11-26 實測（import-linter 2.16＋grimp 3.19）：掃 89 檔、368 條依賴約 0.15 秒；純靜態分析，不 import 被分析的程式，沒有 mlx 也能跑。函式內的 import 也算依賴；標準庫會併成頂層名稱（`urllib.parse` 和 `urllib.request` 分不開）。

## 決定

1. **契約寫在 repo 根目錄的 `.importlinter`**，root 只有 `mac_ai`（`main.py`、`scripts/`、`bench/`、`tests/` 不受約束）：

   | # | 名稱 | 類型 | 內容 |
   |---|---|---|---|
   | C1 | 後端分層 | layers | `assistant｜services` > `stt｜ui_bus` > `wake` > `router` > `db` > `config`（`｜` 是互不依賴的同層） |
   | C2 | router 編排順序 | layers | `pipeline` > `slow_path` > `fast_path` > `local_actions` > `actions` |
   | C3 | 底層不往上依賴編排層 | forbidden | 其餘 22 個 router 模組不得 import（含間接）C2 的 5 個模組 |
   | C4 | 熱路徑不碰 IO | forbidden | `fast_path`、`query_intent`、`domain`、`normalize`、`lexicon`、`stt.completeness`、`stt.endpoint`、`wake` 不得 import 5 個網路 client、`native`、`mac_ai.db`、`subprocess`、`sqlite3`、`socket`、`http` |
   | C5 | 網路 client 只做網路與解析 | forbidden | 5 個網路 client 不得 import `mac_ai.db`、`sqlite3`、`subprocess` |

2. **現有違規列為例外**（`ignore_imports`，每條上一行寫原因），不改 Python 程式碼。現有 5 條（C1 1、C3 1、C4 3、C5 2 次）：`db.rebuild → router.music_prefs`、`catalog → actions`（函式內）、`fast_path → actions`、`fast_path → local_actions`、`youtube_client → music_prefs`。`fast_path` 的兩條是**棘輪**：不准再惡化，本次不重構。原因與還清條件記在 [module-deps.md](../flows/module-deps.md)，兩邊由 `tests/unit/test_docs_flows.py`（T10）檢查一致。
3. **新增 router 模組必須加進 C2 或 C3**，否則不受約束（T11 檢查）。
4. **分工**（補充 0025 第 1 條）：
   - import-linter：**模組依賴圖**（包含間接依賴與函式內的 import）。
   - ast-grep：單檔語法與呼叫（`_run`、`urlopen`、`front_context`、逾時）。`urllib` 無法用 import-linter 區分 `parse` 與 `request`，網路呼叫的範圍仍由 ast-grep 的 `urlopen-only-in-clients` 管。
   - 熱路徑的「直接 import」兩邊都會擋，這是可以接受的重疊（ast-grep 那條規則同時也在檢查呼叫）。
5. **版本鎖定、用 `uvx` 執行**，不進 requirements：`uvx --from import-linter==2.16 --with grimp==3.19 lint-imports`，在 repo 根目錄執行（在其他目錄執行會報 `Could not read any configuration.`）。快取 `.import_linter_cache/` 不入版控。
6. **新增或放寬契約**（改 `layers`、`source_modules`、`forbidden_modules`、`ignore_imports`）等於改決策，先問使用者（比照 0025 第 5 條）。依賴消失後例外一定要刪：例外裡的 import 不存在時 `lint-imports` 會報 `No matches for ignored import`（結束碼 1）。

## 後果

- 本機 commit 前與 CI（`.github/workflows/lint.yml` 的 `import-linter` job）都會擋。
- 例外的邊被忽略之後，沿著那條邊新增的依賴（例如 `actions` 裡新增的 import）不會被 C4 抓到。C4 是「不准再惡化」的棘輪，不是完整保證；熱路徑仍要靠行為測試（例如 `tests/unit/test_domain.py`）。
- 一個 import 可能同時違反好幾份契約（例如 `router.tools` import `assistant` 會經 `stt.completeness → fast_path` 同時打破 C1、C2、C3、C5），錯誤訊息會列出完整的 import 鏈。
- 已知結構債（`fast_path` 頂層 import 執行層、`local_actions` 是最大的樞紐、`catalog` 的循環）只記錄在 module-deps.md，要處理得另開計畫。
- 第 2 條的例外清單、`fast_path` 的結構債與 `catalog` 的循環已由 [0029](0029-deps-converge.md) 收斂。
- C2 的順序與 C3 的範圍、`local_actions` 是最大樞紐的結構債，已由 [0030](0030-router-feature-modules.md) 依功能拆模組後更新。
