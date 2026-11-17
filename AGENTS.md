# AGENTS.md

給在這個 repo 工作的 AI 程式助手。人類使用者文件在 [README.md](README.md)，本檔只放「改程式前必須知道」的事。

## 1. 專案與北極星

在 Apple Silicon Mac 上的**語音助手**：麥克風 → 系統語音辨識（macos-stt）→ 路由 → 執行 macOS 動作（開關 App、音量、亮度、深色模式、分頁、找檔案、看畫面、聊天）。語音辨識、Fast Path 與本機 LLM 都在本機；**雲端模型是選用的加分**（`.env` 的 `AI_API_KEY` 有填才啟用，2026-11-15 使用者同意改變原本「沒有雲端 API」的北極星）：Slow Path 先問雲端、失敗退回本機，沒金鑰時行為與沒有雲端完全相同。

**兩個優先目標：快、準。** 任何改動都要先問：會不會讓常見指令變慢？會不會讓誤觸發變多？兩者衝突時**準確優先**（誤開、誤關、誤執行的成本遠高於多等 1 秒），但不能讓 Fast Path 退化成要等 LLM。

| 路徑 | 目標延遲 | 備註 |
|---|---|---|
| Fast Path（規則比對→執行） | 數 ms～約 100 ms（含 `mdfind` 的找檔案約 80–130 ms） | 不需要 LLM，LLM 掛掉仍可用 |
| 本機找不到→轉交 LLM | 約 1.1 秒 | 靠 prefix cache；沒命中快取會變 3 秒 |
| Slow Path（LLM 規劃工具呼叫） | 離線預算 `PLAN_TIMEOUT_S`＝10 秒；live 測試要求單次規劃 < 5 秒 | 本機 4bit 小模型，會出錯 |
| Slow Path 雲端 | `CLOUD_TIMEOUT_S`＝8 秒；2026-11-16 實測 130 句 p50 0.93 s、p95 2.1 s | 同一步失敗退回本機；熔斷 30 秒 |
| 端點偵測（說完到開始處理） | 完整指令靜音 0.35 秒就收句 | 見 §4 |

## 2. 指令

```bash
source .venv/bin/activate                 # Python 3.12
pytest                                    # 離線測試，預設，約 38 秒
pytest -m unit                            # 只跑單元層，約 1.4 秒（改程式先跑這個；另有 -m flow／-m runtime）
pytest -m live -s                         # 需 mlx_vlm.server 已載入模型（約 4 分鐘）
pytest -m cloud -s                        # 連真的雲端（需 .env 金鑰、約 200 次請求會產生費用，約 3 分鐘）
pytest -m app -v                          # 真實 App 驗收（Safari／Finder／Terminal／VS Code，佔用螢幕約 2 分鐘）
python scripts/acceptance.py run [--app] [--live] [--cloud] && python scripts/acceptance.py report   # 存數字並和上次比，變差結束碼 1
cd desktop && node --test                 # 桌面角色狀態機／路徑白名單
python main.py --text 打開小算盤           # 不啟動服務、不檢查喚醒詞，直接跑一句（改路由後的最快實測方式）
python main.py [--no-stt] [--ui] [--device N]
python build_vocab.py                     # 改 vocab/lexicon.json 後重產 stt_hints／userdict（main.py 啟動時也會自動跑）
python scripts/ui_demo.py [--hold|--choices-test]   # 不用麥克風預覽桌面角色
```

基線：`pytest` 必須 **0 failed**（測試數會隨功能增加，不要硬記數字；2026-11-17 整理測試分層後實測 1657 項通過、5 skipped，deselect 的 live 92／cloud 8／app 19）；`node --test` 46 pass。新增行為要新增測試。

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
| `mac_ai/config.py` | **所有**路徑、埠號、逾時、端點偵測、雲端參數（單一來源，數值旁有實測理由註解）；`env_value()` 讀 `.env`（雲端端點／模型／金鑰） |
| `mac_ai/services.py` | 子程序管理：沿用已健康的服務、`/health` 就緒檢查、SIGINT→SIGTERM→SIGKILL 收尾 |
| `mac_ai/wake.py` | `WakeGate`：喚醒詞（預設關閉；`--ui` 開啟）＋ 60 秒會話，每句重新計時 |
| `mac_ai/router/normalize.py` | NFKC、簡轉繁、誤聽詞、中文數字（只在數值語境）、疊詞 |
| `mac_ai/router/fast_path.py` | `canonicalize_clause`、安全守門、各類 regex 規則 `_r_*`；回傳尚未執行的 `Match` |
| `mac_ai/router/domain.py` | 第一層：範圍判斷（App／專案／資料夾／檔案／網頁），**純函式、無副作用** |
| `mac_ai/router/local_actions.py` | 第二層：只讀 `Intent.slots` 執行（開資料夾、`mdfind`、選擇清單）；`ACTION_TABLE` 是 (domain, verb)→工具對應 |
| `mac_ai/router/catalog.py` | 詞庫 App＋Spotlight 掃出的已安裝 App（快取，每指令開始檢查是否逾 60 秒） |
| `mac_ai/router/context.py` | 前景視窗上下文：`lsappinfo` 讀最前面的 App（含 pid）、`osascript` 讀瀏覽器 host、`sites`／`app_shortcuts` 查表。**只在執行階段呼叫** |
| `mac_ai/router/native.py` | 常用功能的底層執行：ctypes 直接呼叫輔助使用 API（視窗操作、按選單項目、按視窗裡的按鈕）與 CGEvent 送鍵；不經 osascript、不需額外套件、不拋例外 |
| `mac_ai/router/choices.py` | 多筆搜尋結果清單（`STORE`，跨語句、60 秒 TTL；UI 只拿到 `~` 縮寫路徑，完整路徑不出本程序） |
| `mac_ai/router/slow_path.py` | LLM 規劃（雲端優先 `_execute_cloud`、本機 `plan_slow_path`）、共用驗證 `validate_calls`、對象守門 `target_error`、`_local_reroute`、轉交、`warmup_llm` |
| `mac_ai/router/cloud_client.py` | 雲端 `/v1/chat/completions`（`urllib`）、`.env` 設定判斷、錯誤分類、獨立熔斷、XML 提示詞 `CLOUD_SYSTEM_PROMPT`、雲端工具 `CLOUD_TOOLS`；**金鑰只在這裡讀** |
| `mac_ai/router/hant.py` | 簡轉繁（macOS ICU，ctypes）：只換 Big5 編不出來的字與一份精選的「只有簡體才這樣寫」清單，繁體字不動（整句轉換會把「退回路徑」改成「退迴路徑」）；雲端回覆經 `cloud_client.clean_text` 套用，**熱路徑（`normalize`、`completeness`）不得呼叫** |
| `mac_ai/router/memory.py` | 對話記憶 `STORE`（最近 5 輪、60 秒；原句、工具與成功與否、回覆摘要），給雲端 user 訊息與「它」的對象守門 |
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
4. **LLM 呼叫一律走 `llm_client._llm`**：它處理逾時、錯誤分類、熔斷。不要自己 `urlopen`。保持 `enable_thinking=False`（開了延遲倍增）、`temperature=0.4`；chat 回覆仍要過 `_strip_think`。
5. **LLM 不可用時要優雅降級**：Fast Path 照常；Slow 步驟走 `_recover_slow_segment`（有候選才執行，否則告知）。熔斷期間不重試。
6. **`completeness.classify` 與 `catalog` 會在收音熱路徑上被呼叫**，不可在裡面做 `mdfind`、子程序或網路 IO。已安裝 App 清單在 `main.py` 啟動時先掃、`process_voice_command` 開頭才檢查重掃。
7. **改 Fast Path 規則會連動端點偵測**：`classify` 用 `FastPathMatcher` 判斷句子是否完整，命中越多、收句越早（靜音 0.35 秒）。新增規則後要跑 `tests/unit/test_completeness.py`、`tests/unit/test_endpoint.py`；擔心截斷句子時調 `config.py` 的 `EP_*`，並附實測依據。
8. 外部指令都有逾時（`_run(..., timeout)`）：`mdfind` 3 秒、`osascript` 取決於場景。新增子程序呼叫必須設逾時。
9. 指令是**單執行緒依序**執行（`command_worker`）；同步操作會擋住下一句，所以不要加長時間阻塞，必要時設短逾時。
10. 量測用既有工具：`step["ms"]`、`stages`、`logs/runtime/latency.jsonl`（收音計時）、`logs/runtime/commands.jsonl`（指令路由與各步 ms）、`scripts/stt_bench.py`（`list` 一覽、`report` 比較）、`scripts/ui_perf.py`；log 結構見 README「Logs」，`python scripts/log_view.py` 一行一筆檢視 runtime 紀錄；語音指令的 latency 與 commands 以 `utt_id` 對應（`SttClient` 編號 → `SpokenCommand` 帶進佇列 → `command_worker` 寫入），改這條鏈時要保持。**效能主張要有實測數字**，不要憑感覺。
11. **前景視窗只在執行階段讀，而且用到才讀**（`actions.front_context`／`front_app`／`front_site`，同句快取）：`match()` 與 `completeness` 不得讀。只需要 App 用 `lsappinfo`（實測約 11 ms）；瀏覽器網址要 `osascript`（Safari 實測約 170–230 ms），只有站內搜尋與需要網站的快捷鍵才問。
12. **雲端只在 Slow Path 的規劃步驟用**（`slow_path._execute_cloud`）：`match()`、`completeness`、Fast Path 執行、G1（`no_action` 不呼叫任何模型）、本機搜尋 0 筆後的轉交（`local_miss`）都不碰雲端。雲端呼叫一律走 `cloud_client.chat`（逾時、錯誤分類、獨立熔斷；連線／逾時／429／5xx 熔斷 30 秒，401／403 本次執行停用）；失敗同一步退回本機。**沒有金鑰、`CLOUD_ENABLED=False`、`AI_API_URL` 不是 https 時，行為必須與沒有雲端逐字相同**（`test_without_key_slow_path_is_exactly_the_local_path`）。
13. **本機 `SYSTEM_PROMPT`／`TOOLS_SCHEMA` 不為雲端而改**（prefix cache）。雲端有自己的 `CLOUD_SYSTEM_PROMPT`（XML、第三人稱，人格＝可愛熱情簡潔）與 `CLOUD_TOOLS`（只有 `chat_reply` 多一個 `text`：實測這個模型只要有 `tool_calls` 就不寫文字，動作＋聊天的混合句 0/19 → 19/19）。動態資訊（名稱對照、目前視窗、最近對話）放 user 訊息。
14. **金鑰**：只在 `cloud_client` 讀（`config.env_value`）、只放 Authorization header；不得寫進 `os.environ`（`services.py` 會把它傳給子程序）、不得進任何紀錄或錯誤訊息（錯誤只記 `HTTP 401`／`timeout` 這類分類，不記回應內容）。Cloudflare 會擋 `Python-urllib` 預設 User-Agent（403 error code 1010），所以送 `CLOUD_USER_AGENT`。
15. **雲端回覆不可謊稱已執行**：雲端的文字回覆只在「沒有工具」或「工具全部通過驗證且執行成功」時採用；有任何工具被拒絕、被提問句守門丟棄或執行失敗，就只顯示各工具的結果字串。
16. **記憶只放 user 訊息**，不放 system；不記完整路徑、網址、截圖（本機工具只記名稱）。送雲端的內容範圍見 README「雲端模型」；要擴大（例如加傳視窗標題、檔案路徑）先問使用者。
17. **proxy 有回應快取**：LiteLLM 對相同請求逐字回傳同一份答案（實測 1323→426→400 ms、內容完全相同，連「講個笑話」都會是同一個）。日常使用維持快取；**實測、A/B 比較必須開 `config.CLOUD_NO_CACHE`**（請求 body 帶 `cache:{no-cache:true}`；header 的 Cache-Control 無效），`tests/real/test_cloud_live.py` 已自動開。

## 5. 「準」的守則

1. **安全守門在 Fast Path 最前面**（`_blocked`、`_QUESTION_RE`）：否定（不要／別…）、敘述（昨天／剛剛／…了）、提問句一律不走 Fast。唯一例外：`找昨天下載的 PDF` 的「昨天」是檔案時間條件（`_narrative_exempt`）。放寬守門前先想誤觸發案例。
   - 守門前只放行窄的「否定即指令」（`_r_negated_command`：不要靜音了、螢幕不要那麼亮、不想用深色模式）。
   - **G1**（`slow_path.intent_guard`）：交給 Slow Path 後，任一子句是否定／敘述 → **不呼叫 LLM**（規劃與 chat 都不送），回固定文字 `NO_ACTION_REPLY`；每個子句都是資訊提問（什麼／怎麼…）→ 照常規劃但丟掉 `STATE_TOOLS`。只有「嗎」的是非問句／客氣請求、糾正句開頭的「沒有／不是」不擋。實測依據：4bit 模型幾乎不理會否定（「別讓電腦睡眠」→ 睡眠），chat 回覆會謊稱已執行，改提示詞無效（計劃書 §15）。
2. **`validate_tool_call` 是唯一的信任邊界**：未知工具／參數、缺必填、型別錯、enum 不符一律拒絕；App 名稱必須在詞庫或已安裝清單；專案只能從白名單解析；editor 必須是編輯器類 App。**LLM 編出來的名稱不能直接拿去 `open -a`。**
3. **LLM 看不到的工具要保持看不到**：`browser_tab`、`open_folder`、`find_file`、`pick_choice`、`context_search`、`front_action` 刻意不在 `TOOLS_SCHEMA`（加了會讓 4bit 模型誤判，例如「開深色模式」→ `toggle_dark_mode`）。本機工具登錄在 `actions.LOCAL_TOOLS`。
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
16. **對象的優先順序固定**：句子明說的 → 同句剛開的（`last_browser`／`last_editor`）→ 前景視窗 → 預設。前景讀不到（含桌面角色自己在前景）就退回預設，不猜。
17. **常用功能只走 `front_action`，以底層直接執行為主**（`native.py`，對前景 App 的 pid 下指令，不經鍵盤與輸入法）：
   - 詞庫 `app_shortcuts` 的每個綁定擇一：`window`（關閉鈕／最小化／全螢幕屬性）、`path`（依選單標題，各語言名稱並列）、`char`＋`mods`（依快捷鍵找 App 自己的選單項目，和介面語言無關）、`button`（視窗裡說明文字符合的按鈕，例如 YouTube 播放器）、`script`（App 自己的指令，例如 Music 的 playpause）。
   - 找不到選單項目才送按鍵（`code`），送之前重讀前景確認 pid 沒變；**送按鍵只允許 ⌘ 組合鍵與翻頁鍵**（實測注音輸入法會吃掉 ⌃` 與單一字母鍵）。
   - 功能不支援目前的 App 時什麼都不做（有 fallback 才執行原動作）。不做任意打字、不在終端機輸入指令；結束程式、刪除類不放進表。`step["context"]` 只記 App、host 與做法（`method`）。
18. **「它／剛剛那個」的對象守門**（`slow_path.target_error`，本機與雲端共用）：句子有代名詞而模型決定了 App／專案時，對象必須出現在本句，或最近幾輪（`memory.STORE`）／同句前面步驟成功的操作裡，否則拒絕。**`剛剛`開頭的說法（「把剛剛開的關掉」）會被 G1 當成敘述擋下，這是既有的安全決策，不要為了代名詞放寬 G1。**
19. **雲端也不能繞過任何守門**：雲端的 `tool_calls` 走 `validate_calls`（`validate_tool_call` → 對象守門 → 搜尋意圖守門 → 本機優先導向）；`LOCAL_TOOLS` 對雲端同樣看不到；`info_question` 一樣丟掉 `STATE_TOOLS`。
20. **開網站的放行是「只放寬、不改變既有結果」**：`_search_intent_error` 原本會拒絕的 `web_search_or_open_url`，只有同時符合（句子沒有搜尋字詞、不是資訊提問、有開啟動詞、只明說一個詞庫網站〔名稱／別名，「YouTube Music」不算〕、query 的網域屬於該網站）才放行，而且**改成網站首頁**（不用模型給的網址，避免深層連結）；原本放行的（有搜尋字詞、字面網址）原樣不動。

## 6. 常見修改怎麼做

| 目標 | 做法 |
|---|---|
| 新增 App／別名／誤聽詞／資料夾／檔案類型 | 只改 `vocab/lexicon.json`（別重複寫在程式裡）；misheard 條目要標 `source`（`observed` 實測／`seed` 預設）；再跑詞庫相關測試 |
| 新增一類語音指令（常見的） | `fast_path.py` 加 regex 與 `_r_*`，在 `match()` 的規則清單排對位置；`actions.py` 加執行函式並登錄 `AVAILABLE_FUNCTIONS`／`_dispatch`；加離線測試（命中、不命中、被守門擋下） |
| 新增 LLM 可用工具（少見） | 先確認 Fast Path 做不到；`tools.py` 加 schema 與驗證；跑 `pytest -m live`（準確率與延遲）；注意 prompt cache；雲端自動沿用（`CLOUD_TOOLS` 由 `TOOLS_SCHEMA` 產生），再跑 `pytest -m cloud -s` |
| 調雲端提示詞 | 只動 `cloud_client.CLOUD_SYSTEM_PROMPT`；用 `pytest -m cloud -s tests/real/test_cloud_live.py -k planning` 比改動前後的各類正確率與延遲，數字附在 commit 或計劃書 |
| 新增本機（不給 LLM）工具 | 寫在 `local_actions.py`，登錄 `LOCAL_TOOLS`，在 `ACTION_TABLE` 加對應；不要放進 `TOOLS_SCHEMA` |
| 新增站內搜尋網站／常用功能快捷鍵／可開啟的網站 | 只改 `vocab/lexicon.json` 的 `sites`（`search_url` 站內搜尋、`home` 首頁、`aliases` 別名）／`app_shortcuts`（說法要整句完全相同才命中）；`home` 有填的網站，「打開／上／去＋名稱」會由 Fast Path（`_r_site`）開首頁，雲端／本機規劃的開網站呼叫也靠它放行（`slow_path.site_open_home`）；跑 `tests/flow/test_route_context.py`、`tests/unit/test_context.py` 與 `tests/unit/test_completeness.py`，確認新說法不是其他指令的前半句 |
| 調整範圍判斷 | 只動 `domain.py`（純函式）；要有 Intent 層的測試（`tests/unit/test_domain.py`、`tests/flow/test_route_find.py`） |
| 調端點偵測參數 | 動 `config.py` 的 `EP_*`／`VAD_*`；用 `scripts/stt_bench.py` A/B 比對再決定 |
| 改 UI 協定 | `ui_bus.py` 與 `desktop/protocol.js`／`state.js` 一起改，兩邊測試都要跑 |

## 7. 測試規範

- **測試分層**（`tests/`，依目錄自動加 marker）：`unit/`（單一模組、全假物件）→ `flow/`（`process_voice_command` 整條路由）→ `runtime/`（真的執行緒、socket、子程序、osacompile）→ `real/`（live／cloud／app 實測，預設不跑）。新測試放在能測到它的最低一層；同一主題集中在一個檔案，不要另開檔。
- **共用假物件放 `tests/support/`，不要在測試檔裡再寫一份**：`llm.py`（`tc()` 組 tool_call、`FakeLLM` 依序／plan＋chat／依原句回覆並記錄 calls／plans／chats／texts、`forbid_llm`、`llm_always`）、`expect.py`（預期指令 `APP`／`VOL`／`SEARCH`… 與 `fast_matches`）、`clock.py`（假時鐘，conftest 的 `clock` fixture）、`typo_corpus.py`／`route_corpus.py`（語料）、`stt.py`／`endpoint.py`／`screen.py`（假 STT server、端點模擬器、螢幕環境）、`real.py`（被 conftest 換掉前的真函式）。conftest 的 `sh` 用 `sh.fail(指令)` 讓指定指令失敗。
- 離線測試是預設且必須通過；`tests/conftest.py` 已經：重設 LLM 熔斷、**讓 `urllib.request.urlopen` 預設拋錯**（不會誤打本機 server）、把已安裝 App 清單固定為空、把 `normalize`／`latency`／`commands` 三種 log 導到暫存目錄（測試不得寫進專案的 `logs/`）。
- 需要的假環境用現成 fixture：`sh`（記錄 `subprocess.run`）、`find_env`（假家目錄＋假 `mdfind`）、`installed_apps`、`projects`、`front`（假的前景 App 與網站；預設是「讀不到」）。**測試不得依賴這台電腦裝了哪些 App，也不得真的執行 `open`／`osascript`／`mdfind`。**
- 時間相關邏輯（喚醒會話、選擇清單 TTL、端點偵測）用注入的時鐘測，不要 `sleep`。
- 修 bug 先寫能重現的測試；重構要確認前後行為一致。
- 路由、LLM 工具或 prompt 的改動要另外跑 `pytest -m live`（需 server）；不能跑就明說「live 未驗證」。
- **錯字語料**（資料在 `tests/support/typo_corpus.py`，測試在 `tests/flow/test_typo.py`）：每句跑完整 `process_voice_command`，LLM 一被呼叫就失敗（矯正必須在正規化＋Fast Path 完成）。新增誤聽詞或常用功能說法時順手加案例；類別代號給 `scripts/acceptance.py` 算矯正率。
- **真實 App 驗收**（`tests/real/test_app_real.py`，marker `app`，預設不跑）：只操作測試自己開的視窗並在收尾關掉；Terminal 要等 shell 閒置才關（否則跳「終止程序」對話框卡住）；測試期間以 `caffeinate` 防止螢幕自動鎖定（底層操作不算使用者活動）。改 `front_action`、`app_shortcuts`、`native.py`、站內搜尋或前景讀取時要跑。離線測試的 `native_calls` fixture 會攔下並記錄所有底層操作。
- **已知問題用 `xfail(strict=True)` 記錄**並寫明日期與實測結果，修好時會提醒；不要用 skip 掩蓋。
- VAD 相關測試依賴 `AI_models/silero_vad.onnx`，缺檔會 skip；目前的 5 個 skipped 是 `tests/runtime/test_applescript.py` 的「沒有安裝 Google Chrome」，屬正常。
- 格式合法、測試通過、結束碼 0 都不等於端到端正確；STT（麥克風、語音辨識）沒有自動測試，語音實機仍要人工確認，不要聲稱已驗證。

## 8. 不要碰／注意

- `macos-stt/` 是**獨立 git repo**（在 `.gitignore`），自有 venv、不在本 repo 提交範圍；語音授權必須在它的 `.app` bundle 內，只能經 `start.py serve` 啟動。
- 不入版控、不要提交：`AI_models/`、`Rushia_hd/`（Live2D 模型，個人使用）、`desktop/vendor/`（Live2D Cubism Core 專有授權）、`desktop/node_modules/`、`logs/`、`bench/synth/`、`*.wav`、`.env*`。不要修改 `.env`、production 設定或外部服務。
- `mac_ai/router/catalog.py` 的 `_scan_installed` 在函式內才 import `actions`（避免 `actions → llm_client → tools → catalog` 的循環匯入），不要搬到檔案頂層。
- `ENDPOINT_MODE="client"`（預設）需要 `AI_models/silero_vad.onnx`；沒有這個檔案時才改成 `"server"`。
- `WAKE_WORD_ENABLED` 預設 `False`；`--ui` 會強制啟用喚醒詞。
- 本機專屬路徑（如 `project_roots`、`projects` 的 `path`）在 `vocab/lexicon.json`，改動前確認是否為使用者個人設定。
- 已知文件落差：README 引用的 `docs/語音端點偵測與延遲改善：實作計畫.md` 目前不在 repo（`docs/` 只有「本機優先的找東西分流」「前景視窗上下文與常用功能」兩份計畫書）。

## 9. 工作流程偏好（使用者的全域規則摘要）

- 全程使用**繁體中文（臺灣用語）**；程式碼識別字、指令、錯誤訊息保留原文。回覆先給結論，簡潔。
- 簡單、低風險任務直接做；涉及多檔案、架構、資料流、public API／schema、行為變更、效能或安全性的**複雜任務，先在對話中提計畫，等確認再實作**。
- 只改完成需求所必須的內容；符合既有命名、風格與註解密度；不順手重構、不加未要求的功能、不新增 dependency（必要時先說明）。
- 功能設計方向、產品行為、資料契約、互動流程、驗收標準由使用者決定；發現與既有設計衝突或可能偏離時立即停下提出。
- 不主動產出 .md／文件檔；不 commit、push、開 PR，除非使用者要求；commit 訊息沿用既有 `feat:`／`fix:`／`docs:` 一行摘要風格，**不加** `Co-Authored-By: Claude` 或任何 AI 署名。
- 回報分開寫「已實際執行確認」與「尚未驗證」，禁止聲稱未執行的測試已通過。
