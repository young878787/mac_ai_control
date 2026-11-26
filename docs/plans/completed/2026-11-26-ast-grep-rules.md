# ast-grep 專案規則：把可由語法判斷的紅線變成 lint

> **歸檔（2026-11-26 結案）**：S1～S5 都已實作，目前有效的決策見 [ADR 0025](../../decisions/0025-static-checks.md)。以下內文保留結案時的樣子，原檔名 `docs/plans/active/2026-11-26-ast-grep-rules.md`。實作差異與結果見 §8。**結案時沒有做的**：R1（`pytest -m app` 真實 App 驗收）。

狀態：**已結案（2026-11-26）**。原狀態：進行中。§2 是使用者已決定的事。

## 0. 結論

- Ruff、ESLint（含 4 條桌面自訂規則）與 GitHub Actions lint CI 已在 commit `6f22477` 完成，本計畫不重做，只補 ADR。
- 新增 **ast-grep，只管 Python 的專案規則**：把 ADR／紅線中「看語法就能判斷」的部分寫成規則，違反時本機與 CI 都擋下，訊息寫出 ADR 編號與正確做法（給 AI 看）。
- JS 端不加 ast-grep：紅線 13 已由 ESLint 自訂規則＋`desktop/test/eslint-rules.test.js` 守住，兩套工具管同一批檔案只會重複。
- 不建 `pyproject.toml`（沿用 `ruff.toml`；本 repo 沒有 pyproject，新增會改變 uv／pip 的行為）；不加 import-linter（新 dependency，分層改用 ast-grep 規則）。

## 1. 結構

```
├── ruff.toml                 # 既有：Ruff
├── desktop/eslint.config.js  # 既有：ESLint＋desktop/eslint-rules/
├── sgconfig.yml              # 新增：ast-grep 主設定（ruleDirs、testConfigs）
├── rules/                    # 新增：一條規則一個 YAML，message 寫 ADR 編號、note 寫正確做法
└── rule-tests/               # 新增：每條規則的 valid／invalid 案例與快照（__snapshots__）
```

執行方式比照 Ruff，用 `uvx` 鎖版本，不進 requirements：`uvx --from ast-grep-cli@0.51.2 ast-grep scan`。
版本選 0.51.2（2026-11-11 發布）；最新的 0.51.3（2026-11-16）發布未滿兩週，不用。

## 2. 決策（使用者 2026-11-26）

1. **`_run`／`subprocess.run` 沒帶逾時的既有 15 處：補上逾時**（選項 b）。
   - 實作時規則實際掃出 **17 處**（提計畫時用 grep 估 15 處，漏了兩處 `_run([...] if browser else [...])`：`actions.web_search_or_open_url`、`local_actions` 開聯合搜尋的網頁結果），同屬本決策，一併處理。
   - `actions._run` 呼叫（`open`、`osascript`、`pmset`：`actions.py` 11 處、`local_actions.py` 5 處）一律用既有的 `EXTERNAL_TIMEOUT_S`（15 秒）。正常情況這些指令在 1 秒內結束，15 秒只是上限，不影響常見延遲；卡住時（例如等授權對話框）改為回 ❌，不再無限擋住 `command_worker`。
   - `services.py` 的 `is_backend`（`ps`）設 2 秒；逾時視為「不是後端」，不送訊號（不確定就不殺）。
2. **`time.sleep` 規則範圍是 `mac_ai/router/`**（選項 a）：`actions._wait_for_page` 既有 3 處（看畫面前等網頁載入，有上限）加壓制註解並寫理由。
3. 計畫存成本檔。

## 3. 規則清單

| id | 依據 | 擋什麼 | 範圍 |
|---|---|---|---|
| `hot-path-no-io` | 0001、0003、紅線 1 | import `subprocess`／`socket`／`urllib`／`http`／`requests`／`httpx`／各 `*_client`；呼叫 `_run`、`front_context`、`front_app`、`front_site`、`_llm`、`urlopen`、`os.system`、`os.popen` | `router/fast_path.py`、`stt/completeness.py`、`router/query_intent.py`、`router/domain.py`、`router/normalize.py` |
| `urlopen-only-in-clients` | 0002、紅線 2 | 呼叫 `urlopen`、`HTTPConnection`／`HTTPSConnection` | `mac_ai/`；白名單 `llm_client`、`cloud_client`、`search_client`、`weather_client`、`youtube_client`、`services` |
| `prompt-prefix-immutable` | 0002、紅線 2 | `SYSTEM_PROMPT`／`TOOLS_SCHEMA` 的 `+=`、f-string／`.format`、在函式內或以屬性重新賦值、`TOOLS_SCHEMA` 的 `.append`／`.extend`／`.insert` 等原地修改、下標賦值、`del` | `mac_ai/` |
| `no-environ-write` | 0010、紅線 10 | `os.environ[...] = ...`、`.update`、`.setdefault`、`\|=`、`os.putenv` | `mac_ai/`、`main.py`、`build_vocab.py`、`scripts/`、`bench/` |
| `secret-read-scope` | 0010、0021、0022、紅線 10 | 白名單以外呼叫 `env_value(...)` | `mac_ai/`、`main.py`、`scripts/`、`bench/`；白名單 `cloud_client`、`search_client`、`local_actions.get_weather` |
| `catalog-lazy-actions-import` | AGENTS「不要碰」 | `catalog.py` 在函式外 import `actions` | `router/catalog.py` |
| `subprocess-needs-timeout` | 紅線 12 | `subprocess.run`／`call`／`check_call`／`check_output` 沒有 `timeout=`；`_run(cmd)` 只給一個參數 | `mac_ai/`（`scripts/` 是開發工具，不在範圍） |
| `no-sleep-in-router` | 紅線 12（`command_worker` 單執行緒） | `time.sleep`、`from time import sleep` 後的 `sleep(...)` | `mac_ai/router/` |

**不做成 ast-grep 規則**（需要資料流或資料內容，語法看不出來，繼續由 pytest 守）：結果前綴 ✅／❌、`open -a` 名稱需經驗證、送按鍵白名單、搜尋摘錄不寫 log、LLM 看不到的工具。

**已知限制**：ast-grep 逐檔比對，看不到間接 import 帶進來的 IO；熱路徑的傳遞性保證仍靠 `tests/unit/test_domain.py` 等行為測試（`_scan_installed` 換成會丟例外的版本）。`files`／`ignores` 白名單無法用 `ast-grep test` 測，改在驗收時注入違規確認（§6 A4）。

## 4. 壓制規則

- 寫法：在違規那行的上一行放 `# ast-grep-ignore: <rule-id>`，並在同一段註解寫理由；不得整檔壓制、不得不寫理由。
- 新增壓制等於例外，要在 PR／commit 說明理由；放寬規則本身（改 `files`、`ignores`、pattern）要先問使用者，等同改 ADR。

## 5. 影響範圍與實作步驟

| 步驟 | 內容 | 驗證 |
|---|---|---|
| S1 | `sgconfig.yml`、`rules/`、`rule-tests/` | `ast-grep test` 全過 |
| S2 | 補逾時（§2.1）：`actions.py`、`local_actions.py`、`services.py`；`services` 補一個逾時的單元測試 | L0：`tests/unit/test_context.py tests/unit/test_native.py tests/flow/test_route_context.py tests/runtime/test_applescript.py`、services 測試 |
| S3 | `actions._wait_for_page` 3 處加壓制註解（§2.2） | `ast-grep scan` 結束碼 0 |
| S4 | `.github/workflows/lint.yml` 加 `ast-grep` job | 本機跑相同指令 |
| S5 | ADR 0025；同步 `AGENTS.md`（常用指令、紅線引用）、`docs/guides/testing.md`（指令、L2）、`docs/guides/coding-style.md`（常見修改加「新增專案規則」）| 人工對照 |

## 6. 驗收計畫

### 6.1 自動化（本機實際執行）

| # | 項目 | 指令 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| A1 | 規則單元測試 | `uvx --from ast-grep-cli@0.51.2 ast-grep test` | 結束碼 0，8 條規則都有 valid ≥ 2、invalid ≥ 2，且全過 | 修規則或案例，不刪案例 |
| A2 | 現有程式 0 違規 | `uvx --from ast-grep-cli@0.51.2 ast-grep scan` | 結束碼 0，0 error；壓制註解只有 §2.2 的 3 處 | 違規是真問題 → 停下問使用者；誤報 → 收窄 pattern 並加 valid 案例 |
| A3 | 壓制註解有效 | 拿掉 `actions.py` 其中一處壓制再 scan | 回報 1 個 `no-sleep-in-router`，還原後回到 0 | 檢查註解格式 |
| A4 | 白名單與範圍（`ast-grep test` 測不到的部分） | 每條規則在範圍內的實際檔案注入 1 處違規跑 scan；`urlopen`、`env_value` 另在白名單檔案注入確認**不**報 | 8 條各至少報 1 個、訊息含依據（ADR、紅線或 AGENTS 條目）；白名單不報；全部還原後 `git diff` 只剩本計畫的改動 | 修 `files`／`ignores` glob |
| A5 | 補逾時沒改壞行為 | §5 S2 的 L0 測試＋`pytest -m "unit or flow"` | 0 failed | 修正後重跑 |
| A6 | 離線全部與 lint | `pytest`、`uvx ruff@0.17.6 check .`、`cd desktop && npm run lint && npm run typecheck && node --test` | pytest 0 failed（runtime 已知的 `test_xasr_server.py::test_unknown_http_path_is_404` teardown 逾時與本次無關，若出現要對照改動前）；其餘結束碼 0 | 修正後重跑 |
| A7 | CI 設定語法 | 本機依序執行 job 內的指令 | 與 A1、A2 相同結果 | — |

### 6.2 真實環境

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| R1 | 補逾時後的真實 App 操作 | `pytest -m app -v`（佔用螢幕約 2 分鐘，會開關 Safari／Finder／Terminal／VS Code） | 與改動前同樣通過；**由使用者決定何時跑** |
| R2 | CI 實際執行 | push 後看 GitHub Actions 的 `lint` workflow | `ruff`、`desktop`、`ast-grep` 三個 job 綠燈；**push 由使用者執行** |

### 6.3 不在本次範圍

- JS 的 ast-grep 規則、import-linter、`pyproject.toml`。
- pytest 進 CI（依賴 mlx 等 Apple 限定套件）。
- 語音實機。

## 7. 風險與假設

- 補逾時把「無限等」改成「15 秒後回 ❌」：若某個合法動作真的超過 15 秒（例如首次授權對話框使用者遲遲未按），會回報失敗；動作本身可能稍後仍完成。15 秒取自既有 `EXTERNAL_TIMEOUT_S`，沒有實測依據要更短。
- ast-grep 的 `files`／`ignores` glob 以 repo 根目錄為基準，**必須在 repo 根目錄執行**（CI 與文件都這樣寫）。
- `rules/`、`rule-tests/` 放 repo 根目錄是照使用者的範例。

## 8. 實測結果（2026-11-26，本機）

| # | 結果 |
|---|---|
| A1 | `ast-grep test`：8 passed、0 failed（含快照）。案例數：hot-path 16、prompt 15、subprocess 10、environ 8、catalog 7、secret 6、sleep 6、urlopen 6 |
| A2 | `ast-grep scan`：結束碼 0；壓制註解只有 `actions.py` 3 處 |
| A3 | 拿掉一處壓制 → 1 個 `no-sleep-in-router`；還原後 0 |
| A4 | 19 個注入案例全部符合預期（8 條規則各至少命中 1 次、訊息含依據；`urlopen`／`env_value` 白名單、`scripts/` 的 subprocess、`services.py` 的 sleep、`catalog.py` 對熱路徑規則都不報）；每案還原後逐位元組比對與原檔相同 |
| A5 | L0（context、native、applescript、services、multistep、find、federated）427 passed、6 skipped；`pytest -m "unit or flow"` 3020 passed |
| A6 | `pytest` 3322 passed、6 skipped、0 failed（126 deselected，約 38 秒；先前的 xasr teardown 逾時這次沒出現）；`ruff check .` 0；`npm run lint` 0；`npm run typecheck` 0；`node --test` 309 pass、0 fail |
| A7 | CI 的 `ast-grep` job 兩個指令本機執行結果同 A1、A2 |
| R1 | **未執行**：`pytest -m app` 會佔用螢幕操作真實 App |
| R2 | 通過：push `15c0206` 後 `lint` workflow（run 45774457628）的 `ruff`、`ast-grep`、`desktop` 三個 job 全部成功；有 setup-uv 找不到快取依據檔的警告，不影響結果 |

實作中的調整：
- `ast-grep test` 不認壓制註解，壓制改由 A3 驗證。
- `catalog-lazy-actions-import` 的訊息補上依據（AGENTS「不要碰」），A4 的標準因此寫成「訊息含依據（ADR、紅線或 AGENTS 條目）」。
- 新增測試：`tests/flow/test_route_multistep.py::test_plain_actions_have_timeout_and_report_failure`（10 個一般動作，逾時都回 ❌，且帶的是 `EXTERNAL_TIMEOUT_S`）、`tests/runtime/test_services.py::test_kill_stt_backend_ps_timeout_kills_nothing`。
- 提交前重跑 `pytest` 時，已知的 `test_xasr_server.py::test_unknown_http_path_is_404` teardown 逾時偶發出現（3322 passed、1 error），再跑一次 0 error。
