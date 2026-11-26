# AGENTS.md

給在這個 repo 工作的 AI 程式助手：改程式前必讀的指示牌。人類使用者文件在 README.md。

## 專案與北極星

Apple Silicon Mac 上的語音助手：麥克風 → macos-stt（Windows 改用備用引擎 X-ASR）→ 路由 → 執行 macOS 動作。Fast Path 與本機 LLM 都在本機；雲端是選用的加分，`.env` 沒有金鑰時，行為要與沒有雲端逐字相同。
**兩個目標：快、準。** 任何改動都要先問：會不會讓常見指令變慢？會不會讓誤觸發變多？衝突時準確優先（誤開、誤關、誤執行的成本遠高於多等 1 秒），但不能讓 Fast Path 退化成要等 LLM。

## 文件地圖

| 要找 | 看 |
|---|---|
| 模組職責、桌面分層、延遲目標 | `ARCHITECTURE.md` |
| 輸入→路由→執行的完整流程、Slow Path 與 `validate_calls` 順序；圖的寫法 | `docs/flows/main-flow.md`、`docs/flows/slow-path.md`；規範 `docs/flows/README.md`（0027） |
| 為什麼這樣做（實測依據、使用者決定） | `docs/decisions/NNNN-*.md`（ADR） |
| 「快」「準」守則全文、常見修改怎麼做、不要碰的東西 | `docs/guides/coding-style.md` |
| 測試指令、基線、分層與 fixture | `docs/guides/testing.md` |
| Lint 設定與 ESLint 自訂規則、CI | `ruff.toml`、`desktop/eslint.config.js`、`desktop/eslint-rules/`（測試 `desktop/test/eslint-rules.test.js`）、`.github/workflows/lint.yml`；分工見 ADR 0025 |
| Python 專案規則（紅線的語法檢查） | `sgconfig.yml`、`rules/`（測試 `rule-tests/`） |
| Python 模組依賴（分層、例外、結構債） | `.importlinter`、`docs/flows/module-deps.md`（0026） |
| 計畫：進行中 `docs/plans/active/`，結案 `docs/plans/completed/`（不刪） | 新計畫用 `YYYY-MM-DD-slug.md`；結構規則見 ADR 0016 |

改行為前先看對應的 ADR。要推翻 ADR 時先問使用者，再新增一份 ADR，並把舊的標成「被 NNNN 取代」。

## 常用指令

```bash
source .venv/bin/activate         # Python 3.12
pytest <相關測試檔>                # L0：改什麼跑什麼，對照表見 testing.md「分層節奏」
pytest -m "unit or flow"          # L1：約 3 秒，一個功能改完跑
pytest                            # L2：離線全部約 15 秒，commit 前跑，必須 0 failed
cd desktop && node --test && npm run typecheck
uvx ruff@0.17.6 check . && (cd desktop && npm run lint)   # lint，CI（.github/workflows/lint.yml）也跑
uvx --from ast-grep-cli@0.51.2 ast-grep test && uvx --from ast-grep-cli@0.51.2 ast-grep scan   # 專案規則，在 repo 根目錄跑
uvx --from import-linter==2.16 --with grimp==3.19 lint-imports   # 依賴契約，在 repo 根目錄跑
python3 scripts/check_file_lines.py   # 程式檔行數上限 800，在 repo 根目錄跑
python main.py --text 打開小算盤   # 不起服務直接跑一句
```

live／cloud／app 實測與驗收腳本只在對照表寫到時才跑，見 `docs/guides/testing.md`。視覺驗收以腳本的 PASS／FAIL 為準，人眼最準但不是通過條件（0018）。

## 紅線（細節與理由見 coding-style.md 與 ADR）

1. 常見指令先加 Fast Path 規則（`fast_path.py` 的 `_r_*`），不加 LLM 工具。Fast Path、`match()`、`classify_query`、`completeness` 不碰網路、LLM、子程序，也不讀前景。（0001、0003）
2. `SYSTEM_PROMPT`＋`TOOLS_SCHEMA` 逐字穩定、不增大；動態資訊只放 user 訊息；LLM 呼叫只走 `llm_client._llm`。（0002）
3. 改 Fast Path 規則會連動端點偵測：要跑 `test_completeness.py`、`test_endpoint.py`。（0003）
4. 安全守門在最前面：否定、敘述、提問不走 Fast；否定或敘述交給 Slow 後也不呼叫任何模型（G1）。放寬守門前先列誤觸發案例。（0007；天氣、會變的事實、查／找＋問句只豁免提問這一關，見 0021、0023）
5. `validate_tool_call` 是唯一的信任邊界；LLM 編出來的名稱不能直接 `open -a`。本機工具對 LLM 保持看不到（雲端只有 `front_action`、`site_search` 兩個例外，有 `BRAVE_API_KEY` 時多 `web_answer`）。（0011、0022）
6. 找檔案預設只在 Finder 顯示，危險檔一律只顯示，範圍限家目錄。（0005）
7. 對象順序固定：明說 → 同句剛開 → 前景 → 預設；讀不到就退回預設，不猜。（0006）
8. 常用功能走 `front_action`→`native.py`；送按鍵只允許 ⌘ 組合鍵與翻頁鍵，媒體鍵只允許 play／next／previous；不任意打字、不放結束程式或刪除類。（0008、0019）
9. 雲端只用在 Slow Path 規劃（與長尾回答的第二次不帶工具的呼叫），失敗退回本機；不能繞過任何守門，不能謊稱已執行。（0009、0022）
10. 金鑰只在 `cloud_client`（`AI_API_KEY`）、`search_client`（`BRAVE_API_KEY`）、`get_weather`（`CWA_API_KEY`）讀，不進 `os.environ`、log 或錯誤訊息；搜尋摘錄與結果的標題、網址不寫 log、資料庫與對話記憶。（0010、0021、0022）
11. 結果字串前綴 ✅／❌／⚠️／⏭️ 決定 status，不要把失敗寫成 ✅。送 LLM 的永遠是原句。
12. 外部指令一律設逾時；`command_worker` 是單執行緒，不加長時間阻塞。效能主張要附實測數字。
13. 桌面：依賴只能往下（`layers.test.js`）；匯流排只綁 127.0.0.1＋token＋不收 Origin；聊天室主動展開只用 `showInactive()`。ESLint 自訂規則擋 `.show()`、非 127.0.0.1 網址、`webPreferences` 缺安全設定、非常值的 IPC 頻道。（0012、0014）
14. 擬人化的指標門檻在 `desktop/dev/motion-metrics.js`，不得為了過關放寬。（0015）
15. 辨識引擎的切換點是 WS 協定：`xasr_server` 與 macos-stt 行為要一致，`SttClient`、端點偵測、`EP_*` 不為單一引擎改；CPU 門檻用整機百分比；Windows 只在 macOS 上模擬過，不可說成實機驗證。（0020）
16. 後端依賴只能往下：`.importlinter` 的 C1～C5（分層、router 編排順序、熱路徑不碰 IO、網路 client 不碰資料庫與子程序，函式內 import 也算）。新模組先決定層，新的 `router/` 模組加進 C2 或 C3；新增 `ignore_imports` 或放寬契約先問使用者，依賴消失就刪例外並同步 `module-deps.md`。（0026）
17. 程式檔（含測試）不超過 800 行（`scripts/check_file_lines.py`）。超過時依職責拆到既有模組；需要新模組先向使用者提計畫，確認後再拆；不得調高上限或加排除。（0028）

## 不要碰

- `archive/`：唯讀參考，不改、不 import。`macos-stt/`：獨立 repo，不在本 repo 提交範圍。
- 不入版控：`AI_models/`、`Rushia_hd/`、`desktop/vendor/`、`desktop/node_modules/`、`logs/`、`data/`、`bench/synth/`、`*.wav`、`.env*`。
- 不修改 `.env`、production 設定或外部服務。`vocab/lexicon.json` 的 `project_roots`、`projects.path` 是個人設定。

## 工作流程

- 繁體中文（臺灣用語），先給結論。簡單任務直接做；多檔案、架構、資料流、行為變更、效能、安全性的任務先提計畫，等確認再做。
- 只改需求必要的部分，不順手重構，不新增 dependency；產品行為與驗收標準由使用者決定，偏離時立即停下提出。
- 新增行為要新增測試；修 bug 先寫能重現的測試；已知問題用 `xfail(strict=True)` 並寫日期。
- commit 前 `ruff check`、`ast-grep scan`、`lint-imports`、`check_file_lines.py`、`npm run lint` 必須 0 違規。不得為了過關放寬 `ruff.toml`／`eslint.config.js`／`rules/`／`.importlinter`（含新增 `ignore_imports`）或自訂規則；確定是誤報或確有必要的例外才加 `noqa`／`eslint-disable`／`ast-grep-ignore`，並寫原因（ast-grep 的壓制要在上一行，原因寫在相鄰的註解）。CI 只跑 lint、typecheck、`node --test`，不跑 pytest，CI 綠燈不代表 pytest 通過。
- 不 commit、push、開 PR，除非使用者要求；commit 用 `feat:`／`fix:`／`docs:` 一行摘要，不加任何 AI 署名。
- 回報分開寫「已實際執行確認」與「尚未驗證」。STT 語音實機沒有自動測試，不要聲稱已驗證。
- 改到流程（函式、順序、退回鏈、守門）的 commit 要同時更新 `docs/flows/`，照 `docs/flows/README.md` 的規範；`tests/unit/test_docs_flows.py` 檢查圖的寫法、文件裡的 `模組.名稱`、例外表與 `.importlinter` 一致。（0027）
- 計畫結案時移到 `docs/plans/completed/`，把其中仍然有效的決策寫成 ADR，並同步 `ARCHITECTURE.md` 與 flows。
