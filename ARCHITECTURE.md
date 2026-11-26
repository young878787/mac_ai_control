# 架構

Apple Silicon Mac 上的語音助手：麥克風 → 系統語音辨識（macos-stt；備用引擎 X-ASR）→ 路由 → 執行 macOS 動作。語音辨識、Fast Path、本機 LLM 都在本機；雲端模型是選用的加分（[0009](docs/decisions/0009-cloud-optional.md)）。

流程細節：[docs/flows/main-flow.md](docs/flows/main-flow.md)、[docs/flows/slow-path.md](docs/flows/slow-path.md)；模組依賴：[docs/flows/module-deps.md](docs/flows/module-deps.md)；圖的寫法：[docs/flows/README.md](docs/flows/README.md)。

## 延遲目標

| 路徑 | 目標延遲 | 備註 |
|---|---|---|
| Fast Path（規則比對→執行） | 數 ms～約 100 ms（含 `mdfind` 的找檔案約 80–130 ms） | 不需要 LLM，LLM 掛掉仍可用 |
| 本機找不到→轉交 LLM | 約 1.1 秒 | 靠 prefix cache；沒命中快取會變 3 秒；有雲端金鑰時改走雲端規劃（[0023](docs/decisions/0023-query-routing.md)） |
| 聯合搜尋（`federated_search`） | 門檻 p50 ≤ 1.5 s、p95 ≤ 3.5 s（計畫 U10） | 本機 `mdfind`（`FILE_SEARCH_TIMEOUT_S`）與 Brave（`WEB_SEARCH_TIMEOUT_S`）並行，約等於較慢的一邊；沒有 Brave 金鑰時只等 mdfind（2026-11-23 實測 5 個詞 100～377 ms，沒說範圍的「搜尋 X」開 Google 前多這一段，使用者接受）。**含真實 Brave 的門檻未實測**，[0023](docs/decisions/0023-query-routing.md) |
| Slow Path（LLM 規劃工具呼叫） | 離線預算 `PLAN_TIMEOUT_S`＝10 秒；live 測試要求單次規劃 < 5 秒 | 本機 4bit 小模型，會出錯 |
| Slow Path 雲端 | `CLOUD_TIMEOUT_S`＝8 秒；2026-11-16 實測 130 句 p50 0.93 s、p95 2.1 s | 同一步失敗退回本機；熔斷 30 秒（`web_answer` 依摘錄回答那次逾時不熔斷，[0024](docs/decisions/0024-home-city-and-answer-timeout.md)） |
| 點歌（`play_media`，沒快取） | 門檻 p50 ≤ 1.0 s、p95 ≤ 2.0 s | 是**驗收門檻**，2026-11-21 實測（R1，20 首、每句一個程序）p50 946 ms、p95 1175 ms；InnerTube 兩邊並行共用截止時間 `YT_TIMEOUT_S`＝2 秒，最差（兩邊都逾時）約 2 秒會擋住下一句，[0019](docs/decisions/0019-music-play.md) |
| 點歌（有快取） | 門檻 p50 ≤ 0.3 s | 不連網；門檻實測待計畫 R2（找分頁改網址單項實測 p50 197 ms） |
| 天氣（`get_weather`） | 驗收門檻 p50 ≤ 1.0 s、p95 ≤ 2.0 s | 2026-11-23 實測（R1，每句一個程序）：氣象署路線 p50 93 ms、p95 1117 ms；只有 Open-Meteo 路線 p50 845 ms、p95 905 ms。每個來源各 `WEATHER_TIMEOUT_S`＝2 秒，最差（兩個都逾時）實測 4.0 秒會擋住下一句，[0021](docs/decisions/0021-weather-answer.md) |
| 長尾回答（`web_answer`） | 原門檻 p50 ≤ 3.0 s、p95 ≤ 6.0 s（計畫 R2）；實測未達，**使用者接受現況**（W27） | 規劃（雲端，`CLOUD_TIMEOUT_S`）＋Brave 搜尋（≤ `WEB_SEARCH_TIMEOUT_S`，近期沒結果時再搜一次）＋依摘錄回答（≤ `WEB_ANSWER_TIMEOUT_S`＝10 秒，逾時不熔斷）；最差約 24 秒會擋住下一句；2026-11-23 實測 20 題（加日期、freshness、回答 `max_tokens` 4096 後）：p50 3.8 s、p95 6.5 s、最慢 7.9 s（回答呼叫逾時 6 s 後退回開搜尋頁）；同日其他輪 p50 1.65～3.17 s，雲端波動大，見計畫書 §9.5，[0022](docs/decisions/0022-web-answer.md)。2026-11-25 回答那次單獨 30 次（`bench/web_answer_latency.py`）：p50 2.78 s、p95 20.83 s，10 秒上限逾時 30%（雲端伺服器變慢，推理速度 10～27 tok/s），**使用者接受現況**，[0024](docs/decisions/0024-home-city-and-answer-timeout.md) |
| 端點偵測（說完到開始處理） | 完整指令靜音 0.35 秒就收句 | [0003](docs/decisions/0003-client-endpoint-detection.md) |

## 行程

```
main.py ─┬─ mlx_vlm.server（本機 LLM，OpenAI 相容，APC_ENABLED=1）
         ├─ macos-stt（獨立 repo，.app bundle 內 start.py serve，WS :8765）
         │    或 xasr_server（mac_ai.stt.xasr_server，sherpa-onnx，同一個埠、同一套協定；兩者擇一，[0020](docs/decisions/0020-stt-engine-xasr.md)）
         ├─ ui_bus（127.0.0.1:8766，token）── Electron 桌面角色（desktop/）
         └─ 收音迴圈（STT 執行緒）→ 指令佇列 → command_worker（單執行緒依序）
                                                   └─（選用）雲端 LiteLLM proxy（https，.env）
```

## 模組職責

| 路徑 | 職責 |
|---|---|
| `main.py` | 入口；起 LLM／STT／UI 服務、暖機、收音迴圈；`--text` 繞過服務直接跑；`--stt-engine {macos,xasr}` 選語音辨識引擎（預設 `config.STT_ENGINE`） |
| `mac_ai/config.py` | **所有**路徑、埠號、逾時、端點偵測、雲端參數、點歌參數、語音辨識引擎與 X-ASR 參數（單一來源，數值旁有實測理由註解）；`env_value()` 讀 `.env`（雲端端點／模型／金鑰） |
| `mac_ai/services.py` | 子程序管理：沿用已健康的服務、`/health` 就緒檢查、SIGINT→SIGTERM→SIGKILL 收尾；`stt_service()`（macos-stt）與 `xasr_stt_service()`（缺模型檔就丟 `ServiceError`、ready 要求 `/health` 的 `engine` 是 `xasr`） |
| `mac_ai/wake.py` | `WakeGate`：喚醒詞（預設關閉；`--ui` 開啟）＋ 60 秒會話，每句重新計時 |
| `mac_ai/router/normalize.py` | NFKC、簡轉繁、誤聽詞、中文數字（只在數值語境）、疊詞 |
| `mac_ai/router/fast_path.py` | `canonicalize_clause`、安全守門、各類 regex 規則 `_r_*`；天氣、搜尋、找的規則呼叫 `classify_query`，由 `_route_query` 換成動作；`answer_mode()`（包裝 `query_intent.answer_mode_of`）；回傳只有資料的 `Match`（要呼叫的工具與參數，帶 `query`＝分類結果），由 `pipeline` 執行 |
| `mac_ai/router/query_intent.py` | 查詢分類的唯一一處（[0023](docs/decisions/0023-query-routing.md)）：`classify_query` → `QueryIntent`（target knowledge／local／either、mode show／answer、evidence…），會變的事實 `verify_query`、開網頁或回答 `answer_mode_of`、「我住 X」`_home_city`、冷啟動後的縣市回答 `parse_city_reply`（[0024](docs/decisions/0024-home-city-and-answer-timeout.md)）；詞表在詞庫 `query_cues`；**純函式，不碰網路、LLM、子程序、資料庫、前景** |
| `mac_ai/router/domain.py` | 第一層：範圍判斷（App／專案／資料夾／檔案／網頁），**純函式、無副作用** |
| `mac_ai/router/local_actions.py` | 第二層的入口：載入時把下面 4 個模組的本機工具登錄進 `actions.LOCAL_TOOLS`（LLM 看不到）；`pipeline`、`slow_path` 在頂層 import 它 |
| `mac_ai/router/find_tools.py` | 找東西：開資料夾、檔案搜尋 `find_file`（`mdfind`、範圍與危險檔規則）、選擇清單 `pick_choice`、聯合搜尋 `federated_search`（mdfind 與 Brave 並行、合併清單、讀寫常搜名詞統計）與 `record_explicit_search` |
| `mac_ai/router/front_tools.py` | 前景視窗的工具：站內搜尋 `context_search`、常用功能 `front_action`（詞庫 `app_shortcuts`）；播放控制在前景不支援時交給 `music_tools` 控制背景音樂，成功後寫控制事件 |
| `mac_ai/router/music_tools.py` | 音樂：點歌 `play_media`、開啟／重複使用 YouTube 合輯分頁（`open_music_video`：一支 osascript 找含 `list=RD` 的分頁並改網址，沒有才 `open -a` 開新分頁）、背景音樂控制（`_background_control`）、控制事件（`_record_control`）、`music_replace`、`music_feedback`、`music_top` |
| `mac_ai/router/answer_tools.py` | 直接回答：天氣 `get_weather`（記縣市、預設依序是居住地→常問縣市→冷啟動、冷啟動時登記待答、呼叫 `weather_client`、結果放 `ctx.replies`）與 `remember_home`（背景記下居住地，接著只聊天）、長尾回答 `web_answer`（搜尋→雲端摘要→退回開搜尋頁；沒有搜尋金鑰時開搜尋頁或交給本機 AI）與 `open_source`、`today_taipei` |
| `mac_ai/router/prefs_db.py` | 偏好資料庫 `MUSIC_DB` 與可注入的 `_now`、`_rng`、`_db_call`；點歌、聯合搜尋、天氣共用，其他模組一律用 `prefs_db.<名稱>` 存取（測試會整個換掉） |
| `mac_ai/router/action_plan.py` | 範圍判斷結果（`Intent`）→ 工具呼叫的對應表 `ACTION_TABLE`／`plan_actions`；純資料轉換，比對階段使用 |
| `mac_ai/router/catalog.py` | 詞庫 App＋Spotlight 掃出的已安裝 App（快取，每指令開始檢查是否逾 60 秒） |
| `mac_ai/router/context.py` | 前景視窗上下文：`lsappinfo` 讀最前面的 App（含 pid）、`osascript` 讀瀏覽器 host、`sites`／`app_shortcuts` 查表。**只在執行階段呼叫** |
| `mac_ai/router/native.py` | 常用功能的底層執行：ctypes 直接呼叫輔助使用 API（視窗操作、按選單項目、按視窗裡的按鈕）與 CGEvent 送鍵（含媒體鍵，只允許 play／next／previous）；不經 osascript、不需額外套件、不拋例外 |
| `mac_ai/router/choices.py` | 多筆搜尋結果清單（`STORE`，跨語句、60 秒 TTL；UI 只拿到 `~` 縮寫路徑，完整路徑不出本程序）；項目可以是本機路徑或網路結果 `WebItem`（UI 只拿到標題與網域） |
| `mac_ai/router/slow_path.py` | LLM 規劃（雲端優先 `_execute_cloud`、本機 `plan_slow_path`）、共用驗證 `validate_calls`、對象守門 `target_error`、`_local_reroute`、轉交（有雲端走雲端）、雲端只回文字時會變的事實改成 `web_answer`、只聊天的一步 `execute_chat_only`（「我住 X」：丟棄模型提出的工具，只採用文字）、`warmup_llm` |
| `mac_ai/router/cloud_client.py` | 雲端 `/v1/chat/completions`（`urllib`）、`.env` 設定判斷、錯誤分類、獨立熔斷（呼叫端可用 `trip_on_timeout=False` 讓逾時不熔斷，meta 記 `breaker`）、XML 提示詞 `CLOUD_SYSTEM_PROMPT`、雲端工具 `CLOUD_TOOLS`（有搜尋金鑰時加 `WEB_ANSWER_TOOL`）、依摘錄回答的訊息 `web_answer_messages`；**`AI_API_KEY` 只在這裡讀** |
| `mac_ai/router/youtube_client.py` | 點歌搜尋：InnerTube 一般搜尋＋YouTube Music 搜尋並行（`urllib`、共用截止時間）、YouTube Music 接受規則與挑 MV（只說歌手播歌手卡片第一首）、`clientVersion` 讀取、`valid_video_id`；**只做網路與解析，不碰資料庫與子程序；不用金鑰**（[0019](docs/decisions/0019-music-play.md)） |
| `mac_ai/router/weather_client.py` | 天氣查詢：氣象署（有 `CWA_API_KEY`）→ 失敗改 Open-Meteo → 都失敗回「查不到」；兩個來源整理成同一種 12 小時時段再套固定模板；**只做網路與解析，不碰資料庫與子程序；金鑰只放請求標頭**（[0021](docs/decisions/0021-weather-answer.md)） |
| `mac_ai/router/search_prefs.py` | 常搜名詞統計（執行階段、`command_worker` 執行緒）：`record` 記明說範圍與從清單選的那一邊（本機連同路徑，網路不記網址）、`lean` 依 90 天內的次數決定偏哪一邊與要不要直接執行 |
| `mac_ai/router/weather_prefs.py` | 天氣偏好（執行階段、`command_worker` 執行緒）：記明說的縣市、統計常問縣市當預設、冷啟動訊息 |
| `mac_ai/router/profile_store.py` | 常駐資料 `profile`（執行階段、`command_worker` 執行緒）：目前只有居住地 `home_city`，只記使用者明說的，再說一次就覆寫；只在本機，不送雲端、不進對話記憶（[0024](docs/decisions/0024-home-city-and-answer-timeout.md)） |
| `mac_ai/router/weather_followup.py` | 天氣冷啟動後的待答 `STORE`（記憶體、60 秒、每句開頭取走）：存原本要問的 day、part，下一句只說縣市就接著回答 |
| `mac_ai/router/music_prefs.py` | 點歌偏好（執行階段、`command_worker` 執行緒）：事件、計分、常聽的歌、`terms` 詞表與拼音矯正（拼音在 `pinyin.py`）、快取 `queries`、定時整理的排程邏輯 |
| `mac_ai/db/` | 偏好資料庫 `data/music.sqlite3`（點歌、天氣常問縣市、常搜名詞 `search_terms`、常駐資料 `profile` 共用；不入版控、永久保存）：`ensure_schema`（備份、在複本上升級、損毀改名）、`MusicDb`（背景準備、每執行緒一條連線）、`migrations/`（Alembic，只在 `ensure_schema` 內載入，日後可移除）、`rebuild.py`（從 `events` 重算 `terms`） |
| `mac_ai/router/hant.py` | 簡轉繁（macOS ICU，ctypes）：只換 Big5 編不出來的字與一份精選的「只有簡體才這樣寫」清單，繁體字不動（整句轉換會把「退回路徑」改成「退迴路徑」）；雲端回覆經 `cloud_client.clean_text` 套用，**熱路徑（`normalize`、`completeness`）不得呼叫** |
| `mac_ai/router/memory.py` | 對話記憶 `STORE`（最近 5 輪、60 秒；原句、工具與成功與否、回覆摘要；非 `TOOLS_SCHEMA` 工具的參數不留），給雲端 user 訊息與「它」的對象守門 |
| `mac_ai/router/search_client.py` | 長尾回答的網頁搜尋（Brave Search API，`urllib`）：query → 最多 5 筆摘錄；**只做網路與解析，不碰資料庫與子程序；金鑰 `BRAVE_API_KEY` 只在這裡讀、只放標頭；摘錄只活在記憶體**（[0022](docs/decisions/0022-web-answer.md)） |
| `mac_ai/router/sources.py` | 回答後「要打開來源嗎？」的待決來源 `STORE`（記憶體、60 秒、每句開頭取走）與肯定回答的判斷 `is_yes` |
| `mac_ai/router/tools.py` | `TOOLS_SCHEMA`（LLM 看得到的工具）與 `validate_tool_call`（唯一信任邊界） |
| `mac_ai/router/actions.py` | 工具的分派：`AVAILABLE_FUNCTIONS`（`TOOLS_SCHEMA` 的工具）、`LOCAL_TOOLS`、`_dispatch`（記住剛開的瀏覽器／編輯器與網頁、前置失敗就略過）、`run_tool` |
| `mac_ai/router/exec_context.py` | 執行基礎：`ExecContext`、前景的讀取（`front_context`／`front_app`／`front_site`，用到才讀、同句快取）、`mark_page`、外部指令逾時 `EXTERNAL_TIMEOUT_S`／`POLL_TIMEOUT_S`；不引用任何工具 |
| `mac_ai/router/system_tools.py`、`browser_tools.py` | `TOOLS_SCHEMA` 的系統工具（開關 App、音量、亮度、深色模式／睡眠／鎖定、開專案）與瀏覽器工具（開網址或搜尋、分頁操作、`TAB_BROWSERS`）；實際執行 `open -a`、`osascript`、`pmset` |
| `mac_ai/router/screen_tools.py`、`chat_tools.py` | 讀畫面 `read_screen`（等網頁載入、`screencapture`、縮圖、本機 VL 或雲端）與 chat call `chat_reply` |
| `mac_ai/router/proc.py` | 外部指令的共用呼叫 `_run`（逾時、結束碼、錯誤訊息）；只依賴標準庫 |
| `mac_ai/router/pinyin.py` | 拼音比對（macOS ICU，ctypes）：點歌接受規則與偏好矯正共用；純文字處理 |
| `mac_ai/router/llm_client.py` | `mlx_vlm.server` OpenAI 相容呼叫；連不上即熔斷 30 秒 |
| `desktop/` | 桌面角色（Electron）。依行程與層次分目錄，規則與依賴方向見下方「桌面前端分層」 |
| `mac_ai/assistant.py` | 收音迴圈、`CommandRouter`、`command_worker`；`QueuedJob`（內部工作，不發事件、不寫指令紀錄）與 `MusicMaintenance`（點歌定時整理的計時器，只負責排進指令佇列） |
| `mac_ai/stt/` | `mic`、`client`（WS＋重連）、`vad`（Silero ONNX）、`endpoint`（純邏輯，時間由外部注入）、`completeness`（句子完整度）、`latency_log` |
| `mac_ai/stt/xasr_server.py`、`xasr_hotwords.py` | 備用辨識引擎：獨立子程序，說與 macos-stt 相同的 WS 協定 v1（`/stream`、`/health`），用 sherpa-onnx 串流解碼；`SherpaEngine` 包住模型，`XasrServer` 處理協定、錯誤碼、安全網 `server_vad`；熱詞由 `stt_hints.txt` 轉成 token（`encode_hotwords`）。**只被 `services.xasr_stt_service()` 以子程序啟動；`SttClient`、端點偵測、路由不知道引擎是哪一個**；`sherpa_onnx` 是延遲 import，macOS 預設路徑不需要安裝 |
| `vocab/lexicon.json` | **詞庫單一來源**：App、別名、誤聽、動詞、資料夾、檔案類型、喚醒詞 |
| `archive/` | 舊版單檔實作，**唯讀參考，不要改、不要 import** |

## 後端（`mac_ai/`）分層

決策見 [0026](docs/decisions/0026-import-linter.md)、[0029](docs/decisions/0029-deps-converge.md)、[0030](docs/decisions/0030-router-feature-modules.md)。依賴只能往下；同一層的兩個模組互不 import。

| 層 | 模組 | 內容 | 可以 import |
|---|---|---|---|
| 1 | `assistant`、`services` | 收音迴圈與指令執行緒；啟動與清理子程序（STT、LLM server） | 以下全部 |
| 2 | `stt/`、`ui_bus` | 收音、端點偵測、辨識引擎；桌面匯流排 | `wake`、`router`、`db`、`config` |
| 3 | `wake` | 喚醒詞與會話 | `router`、`db`、`config` |
| 4 | `router/` | 路由、規劃、執行；內部再分編排與執行層（`pipeline` > `slow_path` > `fast_path` > `local_actions` > 各功能的 `*_tools` > `actions` > `exec_context`，完整順序見 [module-deps.md](docs/flows/module-deps.md)）與底層 | `db`、`config` |
| 5 | `db/` | 點歌資料庫的 schema、migration、維護 CLI | `config`（例外：`db.rebuild` 用 `router.music_prefs`） |
| 6 | `config` | 設定與常數 | 無 |

- 規則寫在 `.importlinter`（import-linter，C1～C5：後端分層、router 編排順序、底層不往上依賴編排層、熱路徑不碰 IO、網路 client 只做網路與解析），本機與 CI 都會擋；函式內的 import 也算。
- **新模組先決定層**；新的 `router/` 模組要加進 C2 或 C3（`tests/unit/test_docs_flows.py` 會檢查）。例外與結構債見 [module-deps.md](docs/flows/module-deps.md)；新增或放寬契約、例外要先問使用者。

## 桌面前端（`desktop/`）分層

決策見 [0014](docs/decisions/0014-desktop-layers.md)。

| 目錄 | 層 | 內容 | 可以 import |
|---|---|---|---|
| `shared/` | 資料 | 與後端的合約（訊息型別、上限、expand 原因）；後端是來源 | 無 |
| `rig/` | 資料 | 這個模型的事實：語意名稱→動作／表情／手勢名稱、參數 ID、版面（`fullrig03.js`）；`check.js` 的 `checkRig` 驗證 profile 涵蓋 domain 的每個語意名稱、必要項目在模型裡；換模型＝新增一份 | 無 |
| `domain/` | 純邏輯 | 角色狀態機（只說語意名稱）、注意力導演 `attention.js`（情境→注視計畫，亂數注入）、聊天室紀錄、幀率閘門；時間注入，不碰 DOM／PIXI／Node／Electron | `shared/` |
| `renderer/engine/` | 渲染 | `stage`（Cubism Core 就緒、PIXI 應用程式、讀像素）、`load-model`（載模型並依固定順序裝修補、讀動作表情檔）、`loop`（rAF／停格計時器、逐幀統計）、PIXI／pixi-live2d-display 的修補（`compat/`，含 `render-orders`）、物理時鐘、頂點內插 | `rig/` |
| `renderer/animation/` | 動畫 | 程序層（擬人化）：`mixer`（彈簧、角度上限）、`ambient`（頭身曲線、掃視、漂移）、`face`（眨眼、眼皮與眉毛事件）、`procedural`（依參數所有權寫參數陣列）、`animator`（語意名稱→動作／表情／手勢三個佇列、進退場、每個計算幀的參數疊加到物理）；時間、亂數與語意名稱由外部注入，不 import `domain/`（導演的計畫與 `idle`／`none` 名稱由 `app.js` 轉交） | `engine/`、`rig/` |
| `renderer/role/`、`renderer/chat/` | 畫面 | DOM 與事件接線，唯一能碰 `document` 的層。`role/app.js` 只做組裝（`applyEffects`、每幀順序、後端事件）；`pointer`（點擊穿透、拖曳）、`probe`（`window.__ui` 與動作軌跡）、`widgets` | 以上全部 |
| `main/` | main 行程 | 視窗、匯流排連線（唯一持有 token）、IPC、`ui.jsonl`、除錯鉤子 | `domain/`、`shared/` |
| `preload/` | sandbox | 只做 contextBridge；sandbox 不能共用常數檔，頻道名稱靠測試比對 | 無 |
| `dev/` | 量測 | 軌跡指標，正式執行不載入 | `domain/`、`shared/`、`rig/` |

- 依賴只能往下。`domain/` 與 `engine/` 互不認識，只透過 effect 物件（資料）和 rig（資料）溝通。規則由 `desktop/test/layers.test.js` 掃原始碼檢查，**新檔先決定屬於哪一層**，放在規則以外的位置會直接失敗。
- 畫面行程能讀到的檔案只有 `main/app-protocol.js` 的 `appRoutes` 列的（`renderer/`、`domain/`、`shared/`、`rig/`、`vendor/`、`/model/` 與兩個函式庫單檔）；新增目錄給畫面用時，要加進白名單並在 `protocol.test.js` 補案例，`main/`、`preload/`、`dev/` 不得加。
- 主程序不拼 DOM 選擇器：除錯鉤子讀畫面的 `window.__ui.probe()`／`window.__chat.probe()`。
- 型別檢查 `npm run typecheck`（JSDoc＋`checkJs`，不開 `strict`，不允許新增 `@ts-ignore`）。
