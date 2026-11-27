# 程式守則與常見修改

「快」「準」兩組守則是本專案的紅線，**現行規則以本檔為準**；背後的實測與理由見 `docs/decisions/`（ADR 只記理由，[0031](../decisions/0031-adr-rationale-only.md)；先看[索引](../decisions/README.md)）。兩者衝突時準確優先，但不能讓 Fast Path 退化成要等 LLM。

## 「快」的守則

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
11. **前景視窗只在執行階段讀，而且用到才讀**（`exec_context.front_context`／`front_app`／`front_site`，同句快取）：`match()` 與 `completeness` 不得讀。只需要 App 用 `lsappinfo`（實測約 11 ms）；瀏覽器網址要 `osascript`（Safari 實測約 170–230 ms），只有站內搜尋與需要網站的快捷鍵才問。
12. **雲端只在 Slow Path 的規劃步驟用**（`slow_path._execute_cloud`，含本機搜尋 0 筆後的轉交 `local_miss`，[0023](../decisions/0023-query-routing.md) U4），以及長尾回答依摘錄回答的那一次（`web_answer`）：`match()`、`classify_query`、`completeness`、G1（`no_action` 不呼叫任何模型）都不碰雲端。雲端呼叫一律走 `cloud_client.chat`（逾時、錯誤分類、獨立熔斷；連線／逾時／429／5xx 熔斷 30 秒，401／403 本次執行停用；只有 `web_answer` 依摘錄回答那次傳 `trip_on_timeout=False`，逾時不熔斷，[0024](../decisions/0024-home-city-and-answer-timeout.md)）；失敗同一步退回本機。**沒有金鑰、`CLOUD_ENABLED=False`、`AI_API_URL` 不是 https 時，行為必須與沒有雲端逐字相同**（`test_without_key_slow_path_is_exactly_the_local_path`）。
13. **本機 `SYSTEM_PROMPT`／`TOOLS_SCHEMA` 不為雲端而改**（prefix cache）。雲端有自己的 `CLOUD_SYSTEM_PROMPT`（XML、第三人稱，人格＝可愛熱情簡潔）與 `CLOUD_TOOLS`（`chat_reply` 多一個 `text`：實測這個模型只要有 `tool_calls` 就不寫文字，動作＋聊天的混合句 0/19 → 19/19；另有只給雲端的 `front_action`、`site_search`，見「準」第 19 條）。動態資訊（名稱對照、目前視窗、最近對話）放 user 訊息。
14. **金鑰**：只在 `cloud_client` 讀（`config.env_value`）、只放 Authorization header；不得寫進 `os.environ`（`services.py` 會把它傳給子程序）、不得進任何紀錄或錯誤訊息（錯誤只記 `HTTP 401`／`timeout` 這類分類，不記回應內容）。Cloudflare 會擋 `Python-urllib` 預設 User-Agent（403 error code 1010），所以送 `CLOUD_USER_AGENT`。
15. **雲端回覆不可謊稱已執行**：雲端的文字回覆只在「沒有工具」或「工具全部通過驗證且執行成功」時採用；有任何工具被拒絕、被提問句守門丟棄或執行失敗，就只顯示各工具的結果字串。
16. **記憶只放 user 訊息**，不放 system；不記完整路徑、網址、截圖（本機工具只記名稱）。送雲端的內容範圍見 README「雲端模型」；要擴大（例如加傳視窗標題、檔案路徑）先問使用者。
17. **proxy 有回應快取**：LiteLLM 對相同請求逐字回傳同一份答案（實測 1323→426→400 ms、內容完全相同，連「講個笑話」都會是同一個）。日常使用維持快取；**實測、A/B 比較必須開 `config.CLOUD_NO_CACHE`**（請求 body 帶 `cache:{no-cache:true}`；header 的 Cache-Control 無效），`tests/real/test_cloud_live.py` 已自動開。

## 「準」的守則

1. **安全守門在 Fast Path 最前面**（`_blocked`、`_QUESTION_RE`）：否定（不要／別…）、敘述（昨天／剛剛／…了）、提問句一律不走 Fast。唯一例外：`找昨天下載的 PDF` 的「昨天」是檔案時間條件（`_narrative_exempt`）。放寬守門前先想誤觸發案例。
   - 守門前只放行窄的「否定即指令」（`_r_negated_command`：不要靜音了、螢幕不要那麼亮、不想用深色模式）。
   - **G1**（`slow_path.intent_guard`）：交給 Slow Path 後，任一子句是否定／敘述 → **不呼叫 LLM**（規劃與 chat 都不送），回固定文字 `NO_ACTION_REPLY`；每個子句都是資訊提問（什麼／怎麼…）→ 照常規劃但丟掉 `STATE_TOOLS`。只有「嗎」的是非問句／客氣請求、糾正句開頭的「沒有／不是」不擋。實測依據：4bit 模型幾乎不理會否定（「別讓電腦睡眠」→ 睡眠），chat 回覆會謊稱已執行，改提示詞無效（[0007](../decisions/0007-no-model-for-negation.md)）。
2. **`validate_tool_call` 是唯一的信任邊界**：未知工具／參數、缺必填、型別錯、enum 不符一律拒絕；App 名稱必須在詞庫或已安裝清單；專案只能從白名單解析；editor 必須是編輯器類 App。**LLM 編出來的名稱不能直接拿去 `open -a`。**
3. **LLM 看不到的工具要保持看不到**：`browser_tab`、`open_folder`、`find_file`、`pick_choice`、`context_search`、`front_action`、`play_media`（及 `music_replace`、`music_feedback`、`music_top`）刻意不在 `TOOLS_SCHEMA`（加了會讓 4bit 模型誤判，例如「開深色模式」→ `toggle_dark_mode`）。本機工具登錄在 `actions.LOCAL_TOOLS`（由 `local_actions` 登錄）。例外只有雲端（「準」第 19 條）。
4. **搜尋意圖守門**（`_search_intent_error`）：句子沒有「搜／查／找／天氣…」等字，LLM 的 `web_search_or_open_url` 會被拒絕（防小模型對糾正句亂開搜尋頁）。
5. **範圍判斷順序固定**（`domain.resolve_intent`）：網頁範圍詞 → 專案 → 資料夾 → App → 即時資訊 → 檔案 → 預設。**本機優先**。查詢句先經 `query_intent.classify_query`（[0023](../decisions/0023-query-routing.md)）：明說網路 → 網頁；有本機證據 → 本機；會變的事實、查／找＋問句 → `web_answer`；沒說範圍的找／搜尋／查 → 聯合搜尋（`federated_search`）；「找我的 X」0 筆才轉交模型。判斷結果寫入 `step["intent"]`，錯判時從這裡追查。
6. **找檔案的安全規則不可放寬**：預設只「在 Finder 中顯示」，使用者明說「打開」才開檔；`DANGEROUS_EXT` 與無副檔名的可執行檔一律只顯示（開檔等於執行）；搜尋只限家目錄、排除 `~/Library`、隱藏資料夾、`node_modules`、`.app` 內部、符號連結逃出家目錄的結果。
7. **多筆結果**：`pick_choice` 檢查 `list_id`、TTL、序號範圍；新搜尋取代舊清單。
8. **模糊比對只用在拉丁字母名稱**（門檻 0.80、前兩名差 < 0.05 視為歧義、長度 ≥ 5）；中文差一字常是另一個詞，不做模糊。歧義時 Fast 不執行，只在 LLM 不可用的補救路徑當候選。
9. **高風險動作維持窄入口**：睡眠只接受固定句「讓電腦睡眠」；鎖定螢幕、深色模式各有嚴格 regex。
10. **前置步驟失敗要略過依賴它的步驟**（`ExecContext.failed`：開瀏覽器失敗 → 略過搜尋／分頁／讀畫面）。
11. **看畫面不能讓模型編內容**：沒呼叫 `read_screen` 的「看畫面」說法由 `_is_look_at_screen` 直接補救讀取。
12. **誠實回報**：結果字串前綴 ✅／❌／⚠️／⏭️ 決定 step status（`_step_status`），UI 依此顯示。不要把失敗寫成 ✅。
13. 給 Fast Path 的 `canonicalize_clause` 只用在比對；**送給 LLM 的永遠是原句**（填充詞要留給模型判斷語氣）。
14. `ExecContext` **不跨語句**；要跨語句的狀態（如選擇清單）放在獨立的 store。
15. 桌面角色匯流排：只綁 127.0.0.1、連線需 token 且不得帶 `Origin`（擋網頁）、UI 訊息一律經 `parse_ui_message` 驗證。Electron 的 `app://` 只經 `resolveAppFile` 白名單。這些不要放寬。聊天室視窗同理：有自己的預載腳本（不開放 menu、拖曳、穿透）、送出的文字經主程序驗證且只能是 `command`、IPC 只認自己的 webContents、CSP 不放 `unsafe-inline`／`unsafe-eval`、紀錄不寫硬碟。**主動展開一律 `showInactive()`，不得搶焦點**（前景 App 會決定 `front_action`、分頁、看畫面的對象）；`result.expand` 是選用欄位，沒有就不帶。
16. **對象的優先順序固定**：句子明說的 → 同句剛開的（`last_browser`／`last_editor`）→ 前景視窗 → 預設。前景讀不到（含桌面角色自己在前景）就退回預設，不猜。
17. **常用功能只走 `front_action`，以底層直接執行為主**（`native.py`，對前景 App 的 pid 下指令，不經鍵盤與輸入法）：
   - 詞庫 `app_shortcuts` 的每個綁定擇一：`window`（關閉鈕／最小化／全螢幕屬性）、`path`（依選單標題，各語言名稱並列）、`char`＋`mods`（依快捷鍵找 App 自己的選單項目，和介面語言無關）、`button`（視窗裡說明文字符合的按鈕，例如 YouTube 播放器）、`script`（App 自己的指令，例如 Music 的 playpause）。
   - 找不到選單項目才送按鍵（`code`），送之前重讀前景確認 pid 沒變；**送按鍵只允許 ⌘ 組合鍵與翻頁鍵**（實測注音輸入法會吃掉 ⌃` 與單一字母鍵）；唯一的例外是系統媒體鍵（`native.post_media_key`，只允許 play／next／previous），只在播放控制鏈最後一步、而且 Safari 有 YouTube 影片分頁時才送（[0019](../decisions/0019-music-play.md)）。
   - 功能不支援目前的 App 時什麼都不做（有 fallback 才執行原動作）。不做任意打字、不在終端機輸入指令；結束程式、刪除類不放進表。`step["context"]` 只記 App、host 與做法（`method`）。
18. **「它／剛剛那個」的對象守門**（`slow_path.target_error`，本機與雲端共用）：句子有代名詞而模型決定了 App／專案時，對象必須出現在本句，或最近幾輪（`memory.STORE`）／同句前面步驟成功的操作裡，否則拒絕。**`剛剛`開頭的說法（「把剛剛開的關掉」）會被 G1 當成敘述擋下，這是既有的安全決策，不要為了代名詞放寬 G1。**
19. **雲端也不能繞過任何守門**：雲端的 `tool_calls` 走 `validate_calls`（`validate_tool_call` → 對象守門 → 搜尋意圖守門 → 本機優先導向）；`LOCAL_TOOLS` 對雲端同樣看不到，**唯二例外**（2026-11-17 使用者同意）是 `cloud_client.CLOUD_ONLY_TOOLS`：`front_action(id)`（id 限詞庫有說法的常用功能、不收 `fallback`；句子有「關掉／關閉／退出…」時不得是 `minimize`〔`_CLOSE_RE`〕，「收起來」最小化可以）與 `site_search(query)`（過搜尋意圖守門與本機導向後換成 `context_search` 執行），驗證用 `CLOUD_SCHEMAS`、本機規劃一律拒絕；`info_question` 一樣丟掉 `STATE_TOOLS`（含 `front_action`）。
20. **開網站的放行是「只放寬、不改變既有結果」**：`_search_intent_error` 原本會拒絕的 `web_search_or_open_url`，只有同時符合（句子沒有搜尋字詞、不是資訊提問、有開啟動詞、只明說一個詞庫網站〔名稱／別名，「YouTube Music」不算〕、query 的網域屬於該網站）才放行，而且**改成網站首頁**（不用模型給的網址，避免深層連結）；原本放行的（有搜尋字詞、字面網址）原樣不動。
21. **天氣直接回答**（理由見 [0021](../decisions/0021-weather-answer.md)、[0023](../decisions/0023-query-routing.md)、[0024](../decisions/0024-home-city-and-answer-timeout.md)）：
   - 一句話只有一種結果：開網頁或直接回答，由 `fast_path.answer_mode` 依原句決定，LLM 不決定；明說要看畫面（瀏覽器、上網、google 一下、在 X 搜尋…）→ 開網頁，兩種線索同時出現時開網頁優先。「搜尋台北天氣」「找台北天氣」是回答。
   - 窄規則 `_r_weather` 整句符合才命中，產生 `get_weather(city, day, part)`；strong 主題（天氣、會不會下雨、下雨嗎、下雨機率、氣溫、溫度）單獨就算，weak（幾度、多少度）要同時說縣市或時間；「下雨」一定要帶疑問。句首可以先有語氣詞（那麼、好那、那、好、嗯、欸、對了），但後面一定要接幫我、查／告訴我、縣市或時間；「我住 X」後面接天氣也收（另說了不同縣市就不收）。不收：打開天氣、天氣之子、今天天氣好熱、兩個不同的縣市或時間、超過 36 小時。
   - 規則在 `_blocked` 之後，只豁免提問這一關；否定、敘述照舊擋下、不呼叫任何模型。`match()` 不碰網路、資料庫、子程序，也不看金鑰。
   - 來源：有 `CWA_API_KEY` 先查氣象署，沒有金鑰、失敗、逾時或缺時段改查 Open-Meteo，各自逾時 `WEATHER_TIMEOUT_S`；兩個都失敗回 `❌ 查不到`，**不開網頁**。不經過 LLM，回覆結尾標來源。
   - 沒說縣市時的預設順序：明說 → 居住地 `profile.home_city` → 統計常問（`weather_prefs.default_city`）→ 冷啟動（`COLD_START_MESSAGE`）。只記句子明說的縣市，用預設補上的不記。
   - 冷啟動接續：`weather_followup.STORE` 只放記憶體 60 秒、每句開頭 `take()`；緊接著的那一句只有一個子句、`parse_city_reply` 認得是縣市回答時，接著回答原本的 day／part 並記成居住地。待答狀態由 pipeline 判斷，不放進 Fast Path 規則。
22. **長尾回答 `web_answer` 的啟用與退路**（理由見 [0022](../decisions/0022-web-answer.md)、[0023](../decisions/0023-query-routing.md)、[0024](../decisions/0024-home-city-and-answer-timeout.md)）：
   - `AI_API_KEY` 與 `BRAVE_API_KEY` 都有 → `web_answer`；有雲端、沒有搜尋金鑰 → 開 Google 搜尋頁並回 ⚠️（`step["web_fallback"]="no_search_key"`）；兩把都沒有 → 與沒有這個功能時逐字相同（`ctx.escalate` 後 `match(answer=False)` 重比）。本機 LLM 永遠看不到 `web_answer`。
   - 會變的事實（人物、新聞、比分賽果、營業時間、價格）一律查證，不憑記憶回答：雲端只回文字而原句符合 `query_intent.verify_query` 時改執行 `web_answer`。
   - 摘錄是不可信的資料：放在 user 訊息的 `<search_results>` 並跳脫，回答那次不帶工具。任何一步失敗（搜尋、雲端、被截斷、摘錄沒有答案）都退回開 Google 搜尋頁並回 ⚠️，不謊稱查到。
   - 依摘錄回答那次的逾時上限 `WEB_ANSWER_TIMEOUT_S`＝10 秒、逾時不熔斷（`trip_on_timeout=False`）；最差約 24 秒會擋住下一句（使用者 2026-11-25 接受）。
23. **常駐資料 `profile`**（理由見 [0024](../decisions/0024-home-city-and-answer-timeout.md)）：
   - **只記使用者明說的**（`source`＝`said`「我住 X」或 `cold_start_reply`），不從統計推，不給模型寫入工具；只在 `command_worker` 讀寫，只在本機，不送雲端、不進對話記憶。
   - 「我住 X」（`query_intent._home_city`，在 `_blocked` 之後；原句含不、沒、以前、之前、曾、嗎、？或主詞是別人都不收）→ 本機工具 `remember_home` 在背景記下，同一步只聊天：雲端或本機的 `chat` 都**丟棄模型提出的所有工具呼叫**，只採用文字；資料庫沒準備好或寫入失敗回 ⚠️，不說已記住。

## 常見修改怎麼做

| 目標 | 做法 |
|---|---|
| 新增 App／別名／誤聽詞／資料夾／檔案類型 | 只改 `vocab/lexicon.json`（別重複寫在程式裡）；misheard 條目要標 `source`（`observed` 實測／`seed` 預設）；再跑詞庫相關測試 |
| 新增一類語音指令（常見的） | `fast_path.py` 加 regex 與 `_r_*`，在 `match()` 的規則清單排對位置；執行函式寫在對應功能的 `*_tools.py`（系統 `system_tools`、瀏覽器 `browser_tools`…），在 `actions.py` 登錄 `AVAILABLE_FUNCTIONS`／`_dispatch`；加離線測試（命中、不命中、被守門擋下） |
| 新增 LLM 可用工具（少見） | 先確認 Fast Path 做不到；會讓 `TOOLS_SCHEMA` 變大，超過 `tests/unit/test_tools.py` 的字數上限（紅線 2）要先問使用者；有意改 `SYSTEM_PROMPT`／`TOOLS_SCHEMA` 後更新同檔的 `PREFIX_SHA256`；`tools.py` 加 schema 與驗證；跑 `pytest -m live`（準確率與延遲）；注意 prompt cache；雲端自動沿用（`CLOUD_TOOLS` 由 `TOOLS_SCHEMA` 產生），再跑 `pytest -m cloud -s` |
| 調雲端提示詞 | 只動 `cloud_client.CLOUD_SYSTEM_PROMPT`；用 `pytest -m cloud -s tests/real/test_cloud_live.py -k planning` 比改動前後的各類正確率與延遲，數字附在 commit 或計劃書 |
| 新增本機（不給 LLM）工具 | 寫在對應功能的模組（`find_tools`、`front_tools`、`music_tools`、`answer_tools`；都不合適就先提計畫），在 `local_actions.py` 登錄 `LOCAL_TOOLS`，在 `action_plan.ACTION_TABLE` 加對應；不要放進 `TOOLS_SCHEMA` |
| 新增站內搜尋網站／常用功能快捷鍵／可開啟的網站 | 只改 `vocab/lexicon.json` 的 `sites`（`search_url` 站內搜尋、`home` 首頁、`aliases` 別名）／`app_shortcuts`（說法要整句完全相同才命中）；`home` 有填的網站，「打開／上／去＋名稱」會由 Fast Path（`_r_site`）開首頁，雲端／本機規劃的開網站呼叫也靠它放行（`slow_path.site_open_home`）；跑 `tests/flow/test_route_context.py`、`tests/unit/test_context.py` 與 `tests/unit/test_completeness.py`，確認新說法不是其他指令的前半句 |
| 改點歌規則／接受規則 | 規則在 `fast_path._r_music`、詞庫 `music`（比對原句、純比對、不碰 IO；守門不放寬，先列誤觸發案例並加進 `tests/flow/test_route_music.py` 的反例）；改了要連動跑 `test_completeness.py`、`test_endpoint.py`、`test_route_music.py`。搜尋的接受規則與挑 MV 在 `youtube_client.accept_music`／`pick`：先用 `tests/fixtures/youtube/` 的真實回應或衍生案例補測試（已知風險：YouTube Music 回中文歌手名時，英文說法如「Jay Chou 晴天」對不到歌手而被拒、改播一般搜尋第一筆，見計畫書 §9）；動 `youtube_client` 不得碰資料庫與子程序，videoId 一律過 `valid_video_id`。偏好資料庫改 schema 要新增 Alembic revision（結尾 `PRAGMA user_version`），不改舊的 |
| 調整範圍判斷 | 只動 `domain.py`（純函式）；要有 Intent 層的測試（`tests/unit/test_domain.py`、`tests/flow/test_route_find.py`） |
| 調端點偵測參數 | 動 `config.py` 的 `EP_*`／`VAD_*`；用 `scripts/stt_bench.py` A/B 比對再決定 |
| 改 UI 協定 | `ui_bus.py`／`config.py` 與 `desktop/shared/contract.js`、`domain/`、`main/` 一起改（`tests/unit/test_ui_contract.py` 會比對合約、`desktop/test/ipc-channels.test.js` 比對 IPC 頻道），兩邊測試都要跑；跑 `python scripts/ui_demo.py --chat-test` |
| 調聊天室主動展開的條件 | 只動 `config.EXPAND_*` 與 `pipeline.expand_reason`；案例在 `tests/flow/test_route_expand.py` |
| 調桌面角色的注視／聆聽演出（擬人化） | 常數在 `desktop/renderer/animation/{ambient,face,mixer}.js` 與 `desktop/domain/attention.js`（來源與理由見 [擬人化計畫](../plans/completed/2026-11-20-humanize.md) §5、§12.3）；參數所有權在 `procedural.js`；跑 `node --test`（`motion-sim.test.js` 是 T2／T3 模擬指標）與 `python scripts/ui_demo.py --capture <dir>`（含情境比對）；改到幀迴圈、更新頻率或時序時才另跑 `python scripts/ui_motion.py`；以 PASS／FAIL 判定，人眼最準但不是通過條件（[0018](../decisions/0018-visual-checks-quantitative.md)）；指標定義與門檻在 `dev/motion-metrics.js`，不得為了過關放寬 |
| 新增 Python 模組、改 import | 先決定屬於哪一層（`ARCHITECTURE.md`「後端分層」）；新的 `router/` 模組加進 `.importlinter` 的 C2（編排）或 C3（底層），並更新 `docs/flows/module-deps.md`；跑 `lint-imports` 與 `tests/unit/test_docs_flows.py`。違規時改依賴方向；**新增例外（`ignore_imports`）或放寬契約要先問使用者**（[0026](../decisions/0026-import-linter.md)） |
| 新增／修改專案規則（紅線的語法檢查） | Python 寫在 `rules/<id>.yml`（`message` 寫 ADR 編號、`note` 寫正確做法，用 `files`／`ignores` 限範圍），在 `rule-tests/<id>-test.yml` 加 valid／invalid 各至少 2 例，跑 `ast-grep test -U` 更新快照，再跑 `ast-grep scan` 確認現有程式 0 違規（有違規先問使用者）；JS 寫成 `desktop/eslint-rules/` 的 ESLint 規則並加到 `desktop/test/eslint-rules.test.js`。分工與例外規則見 [0025](../decisions/0025-static-checks.md)；放寬既有規則先問使用者 |
| 換桌面角色的 Live2D 模型／新增動作、表情、手勢的語意名稱 | 模型路徑加進 `config.UI_CHARACTERS`（`--character` 切換，預設 `rushia`）。契約只有 `desktop/rig/contract.js` 一份：動作、參數必要，表情、手勢選用，由契約決定，profile 改不了（[0033](../decisions/0033-rig-contract.md)）。照約定名稱做的模型不用 profile（啟動時警告要配置）；名稱不同、版面要調或動作檔標了循環但只該播一次時，在 `desktop/rig/` 新增 profile（只寫有差的 `motions`／`expressions`／`gestures`／`params`，加上 `layout`、`once`），在 `rig/index.js` 依模型檔名登錄（[0032](../decisions/0032-switchable-character.md)）；語意名稱在 `domain/character.js` 的 `MOTIONS`／`EXPRESSIONS`／`GESTURES`，要和契約一起改，`desktop/test/rig.test.js` 會檢查；`node --test` 與 `python scripts/ui_demo.py --capture <dir>` 都要跑 |

## 不要碰／注意

- `macos-stt/` 是**獨立 git repo**（在 `.gitignore`），自有 venv、不在本 repo 提交範圍；語音授權必須在它的 `.app` bundle 內，只能經 `start.py serve` 啟動。
- 不入版控、不要提交：`AI_models/`、`Rushia_hd/`（Live2D 模型，個人使用）、`Hiyori/`（Live2D 官方範例模型，受其授權條款約束）、`desktop/vendor/`（Live2D Cubism Core 專有授權）、`desktop/node_modules/`、`logs/`、`data/`、`bench/synth/`、`*.wav`、`.env*`。不要修改 `.env`、production 設定或外部服務。
- `mac_ai/router/__init__.py` 不在頂層 import 子模組：Python 載入任何 router 子模組前都會先執行它，熱路徑會跟著載入整個執行器與網路 client。新的對外名稱加進 `_EXPORTS`；`tests/runtime/test_import_footprint.py` 會檢查（[0029](../decisions/0029-deps-converge.md)）。
- `ENDPOINT_MODE="client"`（預設）需要 `AI_models/silero_vad.onnx`；沒有這個檔案時才改成 `"server"`。
- `WAKE_WORD_ENABLED` 預設 `False`；`--ui` 會強制啟用喚醒詞。
- 本機專屬路徑（如 `project_roots`、`projects` 的 `path`）在 `vocab/lexicon.json`，改動前確認是否為使用者個人設定。
- 文件結構與放置規則見 [0016](../decisions/0016-docs-structure.md)、[0031](../decisions/0031-adr-rationale-only.md)。
