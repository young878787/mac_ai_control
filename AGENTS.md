# AGENTS.md

給在這個 repo 工作的 AI 程式助手。人類使用者文件在 [README.md](README.md)，本檔只放「改程式前必須知道」的事。

## 1. 專案與北極星

在 Apple Silicon Mac 上的**本地語音助手**：麥克風 → 系統語音辨識（macos-stt）→ 路由 → 執行 macOS 動作（開關 App、音量、亮度、深色模式、分頁、找檔案、看畫面、聊天）。語音辨識與 LLM 全部在本機，沒有雲端 API。

**兩個優先目標：快、準。** 任何改動都要先問：會不會讓常見指令變慢？會不會讓誤觸發變多？兩者衝突時**準確優先**（誤開、誤關、誤執行的成本遠高於多等 1 秒），但不能讓 Fast Path 退化成要等 LLM。

| 路徑 | 目標延遲 | 備註 |
|---|---|---|
| Fast Path（規則比對→執行） | 數 ms～約 100 ms（含 `mdfind` 的找檔案約 80–130 ms） | 不需要 LLM，LLM 掛掉仍可用 |
| 本機找不到→轉交 LLM | 約 1.1 秒 | 靠 prefix cache；沒命中快取會變 3 秒 |
| Slow Path（LLM 規劃工具呼叫） | 離線預算 `PLAN_TIMEOUT_S`＝10 秒；live 測試要求單次規劃 < 5 秒 | 4bit 小模型，會出錯 |
| 端點偵測（說完到開始處理） | 完整指令靜音 0.35 秒就收句 | 見 §4 |

## 2. 指令

```bash
source .venv/bin/activate                 # Python 3.12
pytest                                    # 離線測試，預設，約 34 秒
pytest -m live -s                         # 需 mlx_vlm.server 已載入模型（約 163 秒）
cd desktop && node --test                 # 桌面角色狀態機／路徑白名單
python main.py --text 打開小算盤           # 不啟動服務、不檢查喚醒詞，直接跑一句（改路由後的最快實測方式）
python main.py [--no-stt] [--ui] [--device N]
python build_vocab.py                     # 改 vocab/lexicon.json 後重產 stt_hints／userdict（main.py 啟動時也會自動跑）
python scripts/ui_demo.py [--hold|--choices-test]   # 不用麥克風預覽桌面角色
```

基線：`pytest` 必須 **0 failed**（測試數會隨功能增加，不要硬記數字；2026-11-08 在 HEAD `c9a848b` 附近實測約 1000 項通過、5 skipped、51 live 被 deselect）；`node --test` 46 pass。新增行為要新增測試。

## 3. 架構與資料流

```
mic.py → stt/client.py（WS :8765，Silero VAD + endpoint.py 決定何時 flush）
       → assistant.py CommandRouter（WakeGate 喚醒詞閘門 → queue）→ command_worker（單執行緒依序執行）
       → router/pipeline.py process_voice_command：
            normalize → split_clauses → 每個子句 FastPathMatcher.match
              命中 → fast 步驟（各自成一步）
              未命中 → 連續未命中的子句合併成一個 slow 步驟 → slow_path.py（LLM）
            → actions.run_tool 執行（ExecContext 帶「剛開的瀏覽器／編輯器」等同句狀態）
       → result 事件 →（--ui）ui_bus.py WebSocket :8766 → desktop/（Electron + Live2D）
```

| 路徑 | 職責 |
|---|---|
| `main.py` | 入口；起 LLM／STT／UI 服務、暖機、收音迴圈；`--text` 繞過服務直接跑 |
| `mac_ai/config.py` | **所有**路徑、埠號、逾時、端點偵測參數（單一來源，數值旁有實測理由註解） |
| `mac_ai/services.py` | 子程序管理：沿用已健康的服務、`/health` 就緒檢查、SIGINT→SIGTERM→SIGKILL 收尾 |
| `mac_ai/wake.py` | `WakeGate`：喚醒詞（預設關閉；`--ui` 開啟）＋ 60 秒會話，每句重新計時 |
| `mac_ai/router/normalize.py` | NFKC、簡轉繁、誤聽詞、中文數字（只在數值語境）、疊詞 |
| `mac_ai/router/fast_path.py` | `canonicalize_clause`、安全守門、各類 regex 規則 `_r_*`；回傳尚未執行的 `Match` |
| `mac_ai/router/domain.py` | 第一層：範圍判斷（App／專案／資料夾／檔案／網頁），**純函式、無副作用** |
| `mac_ai/router/local_actions.py` | 第二層：只讀 `Intent.slots` 執行（開資料夾、`mdfind`、選擇清單）；`ACTION_TABLE` 是 (domain, verb)→工具對應 |
| `mac_ai/router/catalog.py` | 詞庫 App＋Spotlight 掃出的已安裝 App（快取，每指令開始檢查是否逾 60 秒） |
| `mac_ai/router/choices.py` | 多筆搜尋結果清單（`STORE`，跨語句、60 秒 TTL；UI 只拿到 `~` 縮寫路徑，完整路徑不出本程序） |
| `mac_ai/router/slow_path.py` | LLM 規劃、驗證、`_local_reroute`、轉交、`warmup_llm` |
| `mac_ai/router/tools.py` | `TOOLS_SCHEMA`（LLM 看得到的工具）與 `validate_tool_call`（唯一信任邊界） |
| `mac_ai/router/actions.py` | 實際執行器（`open -a`、`osascript`、`screencapture`…）、`ExecContext`、`read_screen`、`chat_reply`、`run_tool` |
| `mac_ai/router/llm_client.py` | `mlx_vlm.server` OpenAI 相容呼叫；連不上即熔斷 30 秒 |
| `mac_ai/stt/` | `mic`、`client`（WS＋重連）、`vad`（Silero ONNX）、`endpoint`（純邏輯，時間由外部注入）、`completeness`（句子完整度）、`latency_log` |
| `vocab/lexicon.json` | **詞庫單一來源**：App、別名、誤聽、動詞、資料夾、檔案類型、喚醒詞 |
| `archive/` | 舊版單檔實作，**唯讀參考，不要改、不要 import** |

## 4. 「快」的守則

1. **能用規則解決就不要問 LLM。** 新增常見指令先加 Fast Path（`fast_path.py` 的 `_r_*`），不是加 LLM 工具。Fast Path 不碰網路、不碰 LLM。
2. **不要破壞 prefix cache。** `SYSTEM_PROMPT` ＋ `TOOLS_SCHEMA` 是快取前綴，必須逐字穩定。動態資訊（本機搜尋結果、別名提示）放 **user 訊息**，不要塞進 system 或 tools（實測放 system 尾巴：每次多 2 秒、冷啟動 3 秒）。`warmup_llm` 要帶與正式請求相同的 system prompt。server 必須以 `APC_ENABLED=1` 啟動（`services.llm_service` 已設）。
3. **不要增大 `TOOLS_SCHEMA`。** 每個工具都會增加每次請求的 prefill。工具描述保持短。
4. **LLM 呼叫一律走 `llm_client._llm`**：它處理逾時、錯誤分類、熔斷。不要自己 `urlopen`。保持 `enable_thinking=False`（開了延遲倍增）、`temperature=0.1`；chat 回覆仍要過 `_strip_think`。
5. **LLM 不可用時要優雅降級**：Fast Path 照常；Slow 步驟走 `_recover_slow_segment`（有候選才執行，否則告知）。熔斷期間不重試。
6. **`completeness.classify` 與 `catalog` 會在收音熱路徑上被呼叫**，不可在裡面做 `mdfind`、子程序或網路 IO。已安裝 App 清單在 `main.py` 啟動時先掃、`process_voice_command` 開頭才檢查重掃。
7. **改 Fast Path 規則會連動端點偵測**：`classify` 用 `FastPathMatcher` 判斷句子是否完整，命中越多、收句越早（靜音 0.35 秒）。新增規則後要跑 `tests/test_completeness_offline.py`、`tests/test_endpoint_offline.py`；擔心截斷句子時調 `config.py` 的 `EP_*`，並附實測依據。
8. 外部指令都有逾時（`_run(..., timeout)`）：`mdfind` 3 秒、`osascript` 取決於場景。新增子程序呼叫必須設逾時。
9. 指令是**單執行緒依序**執行（`command_worker`）；同步操作會擋住下一句，所以不要加長時間阻塞，必要時設短逾時。
10. 量測用既有工具：`step["ms"]`、`stages`、`logs/latency.jsonl`、`scripts/stt_bench.py`、`scripts/ui_perf.py`。**效能主張要有實測數字**，不要憑感覺。

## 5. 「準」的守則

1. **安全守門在 Fast Path 最前面**（`_blocked`、`_QUESTION_RE`）：否定（不要／別…）、敘述（昨天／剛剛／…了）、提問句一律不走 Fast，交給 LLM。唯一例外：`找昨天下載的 PDF` 的「昨天」是檔案時間條件（`_narrative_exempt`）。放寬守門前先想誤觸發案例。
2. **`validate_tool_call` 是唯一的信任邊界**：未知工具／參數、缺必填、型別錯、enum 不符一律拒絕；App 名稱必須在詞庫或已安裝清單；專案只能從白名單解析；editor 必須是編輯器類 App。**LLM 編出來的名稱不能直接拿去 `open -a`。**
3. **LLM 看不到的工具要保持看不到**：`browser_tab`、`open_folder`、`find_file`、`pick_choice` 刻意不在 `TOOLS_SCHEMA`（加了會讓 4bit 模型誤判，例如「開深色模式」→ `toggle_dark_mode`）。本機工具登錄在 `actions.LOCAL_TOOLS`。
4. **搜尋意圖守門**（`_search_intent_error`）：句子沒有「搜／查／找／天氣…」等字，LLM 的 `web_search_or_open_url` 會被拒絕（防小模型對糾正句亂開搜尋頁）。
5. **範圍判斷順序固定**（`domain.resolve_intent`）：網頁範圍詞 → 專案 → 資料夾 → App → 即時資訊 → 檔案 → 預設。**本機優先**。「搜尋／查」沒帶範圍詞維持網頁搜尋；「找」預設先搜本機檔案、0 筆且無本機證據才轉交 LLM。判斷結果寫入 `step["intent"]`，錯判時從這裡追查。
6. **找檔案的安全規則不可放寬**：預設只「在 Finder 中顯示」，使用者明說「打開」才開檔；`DANGEROUS_EXT` 與無副檔名的可執行檔一律只顯示（開檔等於執行）；搜尋只限家目錄、排除 `~/Library`、隱藏資料夾、`node_modules`、`.app` 內部、符號連結逃出家目錄的結果。
7. **多筆結果**：`pick_choice` 檢查 `list_id`、TTL、序號範圍；新搜尋取代舊清單。
8. **模糊比對只用在拉丁字母名稱**（門檻 0.80、前兩名差 < 0.05 視為歧義、長度 ≥ 5）；中文差一字常是另一個詞，不做模糊。歧義時 Fast 不執行，只在 LLM 不可用的補救路徑當候選。
9. **高風險動作維持窄入口**：睡眠只接受固定句「讓電腦睡眠」；鎖定螢幕、深色模式各有嚴格 regex。
10. **前置步驟失敗要略過依賴它的步驟**（`ExecContext.failed`：開瀏覽器失敗 → 略過搜尋／分頁／讀畫面）。
11. **看畫面不能讓模型編內容**：沒呼叫 `read_screen` 的「看畫面」說法由 `_is_look_at_screen` 直接補救讀取。
12. **誠實回報**：結果字串前綴 ✅／❌／⚠️／⏭️ 決定 step status（`_step_status`），UI 依此顯示。不要把失敗寫成 ✅。
13. 給 Fast Path 的 `canonicalize_clause` 只用在比對；**送給 LLM 的永遠是原句**（填充詞要留給模型判斷語氣）。
14. `ExecContext` **不跨語句**；要跨語句的狀態（如選擇清單）放在獨立的 store。
15. 桌面角色匯流排：只綁 127.0.0.1、連線需 token 且不得帶 `Origin`（擋網頁）、UI 訊息一律經 `parse_ui_message` 驗證。Electron 的 `app://` 只經 `resolveAppFile` 白名單。這些不要放寬。

## 6. 常見修改怎麼做

| 目標 | 做法 |
|---|---|
| 新增 App／別名／誤聽詞／資料夾／檔案類型 | 只改 `vocab/lexicon.json`（別重複寫在程式裡）；misheard 條目要標 `source`（`observed` 實測／`seed` 預設）；再跑詞庫相關測試 |
| 新增一類語音指令（常見的） | `fast_path.py` 加 regex 與 `_r_*`，在 `match()` 的規則清單排對位置；`actions.py` 加執行函式並登錄 `AVAILABLE_FUNCTIONS`／`_dispatch`；加離線測試（命中、不命中、被守門擋下） |
| 新增 LLM 可用工具（少見） | 先確認 Fast Path 做不到；`tools.py` 加 schema 與驗證；跑 `pytest -m live`（準確率與延遲）；注意 prompt cache |
| 新增本機（不給 LLM）工具 | 寫在 `local_actions.py`，登錄 `LOCAL_TOOLS`，在 `ACTION_TABLE` 加對應；不要放進 `TOOLS_SCHEMA` |
| 調整範圍判斷 | 只動 `domain.py`（純函式）；要有 Intent 層的測試（`test_domain_offline.py`、`test_find_route_offline.py`） |
| 調端點偵測參數 | 動 `config.py` 的 `EP_*`／`VAD_*`；用 `scripts/stt_bench.py` A/B 比對再決定 |
| 改 UI 協定 | `ui_bus.py` 與 `desktop/protocol.js`／`state.js` 一起改，兩邊測試都要跑 |

## 7. 測試規範

- 離線測試是預設且必須通過；`tests/conftest.py` 已經：重設 LLM 熔斷、**讓 `urllib.request.urlopen` 預設拋錯**（不會誤打本機 server）、把已安裝 App 清單固定為空、把 `normalize.jsonl` 導到暫存目錄。
- 需要的假環境用現成 fixture：`sh`（記錄 `subprocess.run`）、`find_env`（假家目錄＋假 `mdfind`）、`installed_apps`、`projects`。**測試不得依賴這台電腦裝了哪些 App，也不得真的執行 `open`／`osascript`／`mdfind`。**
- 時間相關邏輯（喚醒會話、選擇清單 TTL、端點偵測）用注入的時鐘測，不要 `sleep`。
- 修 bug 先寫能重現的測試；重構要確認前後行為一致。
- 路由、LLM 工具或 prompt 的改動要另外跑 `pytest -m live`（需 server）；不能跑就明說「live 未驗證」。
- VAD 相關測試依賴 `AI_models/silero_vad.onnx`，缺檔會 skip；目前的 5 個 skipped 是 `test_browser_screen_offline.py` 的「沒有安裝 Google Chrome」，屬正常。
- 格式合法、測試通過、結束碼 0 都不等於端到端正確；語音實機與桌面角色畫面要人工確認，不要聲稱已驗證。

## 8. 不要碰／注意

- `macos-stt/` 是**獨立 git repo**（在 `.gitignore`），自有 venv、不在本 repo 提交範圍；語音授權必須在它的 `.app` bundle 內，只能經 `start.py serve` 啟動。
- 不入版控、不要提交：`AI_models/`、`Rushia_hd/`（Live2D 模型，個人使用）、`desktop/vendor/`（Live2D Cubism Core 專有授權）、`desktop/node_modules/`、`logs/`、`bench/synth/`、`*.wav`、`.env*`。不要修改 `.env`、production 設定或外部服務。
- `mac_ai/router/catalog.py` 的 `_scan_installed` 在函式內才 import `actions`（避免 `actions → llm_client → tools → catalog` 的循環匯入），不要搬到檔案頂層。
- `ENDPOINT_MODE="client"`（預設）需要 `AI_models/silero_vad.onnx`；沒有這個檔案時才改成 `"server"`。
- `WAKE_WORD_ENABLED` 預設 `False`；`--ui` 會強制啟用喚醒詞。
- 本機專屬路徑（如 `project_roots`、`projects` 的 `path`）在 `vocab/lexicon.json`，改動前確認是否為使用者個人設定。
- 已知文件落差：README 引用的 `docs/語音端點偵測與延遲改善：實作計畫.md` 目前不在 repo（`docs/` 只有「本機優先的找東西分流」計畫書）。

## 9. 工作流程偏好（使用者的全域規則摘要）

- 全程使用**繁體中文（臺灣用語）**；程式碼識別字、指令、錯誤訊息保留原文。回覆先給結論，簡潔。
- 簡單、低風險任務直接做；涉及多檔案、架構、資料流、public API／schema、行為變更、效能或安全性的**複雜任務，先在對話中提計畫，等確認再實作**。
- 只改完成需求所必須的內容；符合既有命名、風格與註解密度；不順手重構、不加未要求的功能、不新增 dependency（必要時先說明）。
- 功能設計方向、產品行為、資料契約、互動流程、驗收標準由使用者決定；發現與既有設計衝突或可能偏離時立即停下提出。
- 不主動產出 .md／文件檔；不 commit、push、開 PR，除非使用者要求；commit 訊息沿用既有 `feat:`／`fix:`／`docs:` 一行摘要風格，**不加** `Co-Authored-By: Claude` 或任何 AI 署名。
- 回報分開寫「已實際執行確認」與「尚未驗證」，禁止聲稱未執行的測試已通過。
