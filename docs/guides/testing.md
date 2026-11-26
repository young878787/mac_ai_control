# 測試與驗證

測試分層、指令、基線與規範。改程式先跑 `pytest -m unit`，再依影響範圍擴大。

## 指令與基線

```bash
source .venv/bin/activate                 # Python 3.12
pytest                                    # 離線測試，預設，約 15 秒（commit 前跑，平常照下面「分層節奏」只跑相關的）
pytest -m "unit or flow"                  # 單元＋整條路由，約 3 秒（改完一個功能跑這個）
pytest -m unit                            # 只跑單元層，約 1.5 秒（另有 -m flow 約 2 秒／-m runtime 約 13 秒）
pytest -m live -s                         # 需 mlx_vlm.server 已載入模型（約 4 分鐘）
pytest -m cloud -s                        # 連真的雲端（需 .env 金鑰、約 200 次請求會產生費用，約 3 分鐘）
pytest -m app -v                          # 真實 App 驗收（Safari／Finder／Terminal／VS Code，佔用螢幕約 2 分鐘）
python scripts/acceptance.py run [--app] [--live] [--cloud] && python scripts/acceptance.py report   # 存數字並和上次比，變差結束碼 1
cd desktop && node --test                 # 桌面角色狀態機／路徑白名單／分層規則／IPC 頻道
cd desktop && npm run typecheck           # 桌面前端型別檢查（JSDoc＋checkJs）
uvx ruff@0.17.6 check .                   # Python lint（設定 ruff.toml，版本鎖定；只開抓 bug 的規則，不做格式化）
cd desktop && npm run lint                # ESLint＋自訂規則（eslint-rules/：no-window-show、secure-web-preferences、ipc-channel-literal、bus-loopback-only）
uvx --from ast-grep-cli@0.51.2 ast-grep test   # Python 專案規則（rules/）的 valid／invalid 案例；改了規則才需要，CI 每次都跑
uvx --from ast-grep-cli@0.51.2 ast-grep scan   # 專案規則掃描（repo 根目錄執行；規則與依據見 ADR 0025）
uvx --from import-linter==2.16 --with grimp==3.19 lint-imports   # 模組依賴契約 C1～C5（repo 根目錄執行，讀 .importlinter；見 ADR 0026）
python main.py --text 打開小算盤           # 不啟動服務、不檢查喚醒詞，直接跑一句（改路由後的最快實測方式；點歌句會真的搜尋並開 Safari，並寫專案的 data/music.sqlite3）
python main.py [--no-stt] [--ui] [--device N]
python build_vocab.py                     # 改 vocab/lexicon.json 後重產 stt_hints／userdict（main.py 啟動時也會自動跑）
python scripts/ui_demo.py [--hold|--choices-test|--chat-test [N]]   # 不用麥克風預覽桌面角色；--chat-test 驗聊天室（會短暫出現視窗）
python scripts/ui_motion.py [--skip-idle|--skip-listen]   # 桌面角色動作軌跡指標（擬人化 R2 閒置 10 分鐘／R3 聆聽 10 輪；量測期間不要動滑鼠）
```

基線：`pytest` 必須 **0 failed**（測試數會隨功能增加，不要硬記數字；2026-11-17 整理測試分層後實測 1657 項通過、5 skipped，deselect 的 live 92／cloud 8／app 19）；`node --test` 0 fail（2026-11-20 前端分層整理 S0～S4 後實測 142 pass；2026-11-21 擬人化 P1～P4 後 266 pass，其中 `motion-sim.test.js` 是 T2／T3 模擬指標；pytest 1730 通過、5 skipped；同日去掉 runtime 層的空等〔關閉握手逾時、0.5 s 輪詢〕後，離線全部 37.5 s → 14.5 s，runtime 35.3 s → 12.6 s，項數不變；2026-11-21 點歌 P1～P5 後 `pytest` 2473 passed、6 skipped、0 failed，約 21 秒，119 deselected；2026-11-22 X-ASR 備用引擎後 `pytest` 2621 passed、6 skipped、0 failed，約 29 秒，126 deselected，`node --test` 266 pass；2026-11-23 天氣 P1～P2 後 `pytest` 2840 passed、6 skipped、0 failed，約 29 秒，126 deselected；同日長尾回答 P3～P4 後 3008 passed、6 skipped、0 failed，約 29 秒；同日查詢路由 P0～P6 後 3191 passed、6 skipped、0 failed，約 30 秒，`node --test` 267 pass；2026-11-25 天氣城市與回答逾時 P1～P4 後 3311 passed、6 skipped，約 35 秒，`tests/runtime/test_xasr_server.py::test_unknown_http_path_is_404` 的 teardown 逾時 1 error，改動前的程式也一樣，與本次無關）。新增行為要新增測試。

## 分層節奏：改什麼跑什麼

不要每改一行就跑完整測試。由小到大，前一層過了才往下：

| 層 | 什麼時候 | 跑什麼 | 約耗時 |
|---|---|---|---|
| L0 | 每次改動 | 下表「L0」那一欄（只跑相關檔案） | 1～5 s（runtime 檔案較慢） |
| L1 | 一個功能改完 | `pytest -m "unit or flow"`；改到 `desktop/` 加 `cd desktop && node --test`，改到 `renderer/` 再加 `npm run typecheck` | 3～6 s |
| L2 | commit 前 | `pytest`（離線全部，0 failed）＋`uvx ruff@0.17.6 check .`＋`uvx --from ast-grep-cli@0.51.2 ast-grep scan`（改了 `rules/` 加 `ast-grep test`）＋`uvx --from import-linter==2.16 --with grimp==3.19 lint-imports`；改到 `desktop/` 加 `node --test && npm run typecheck && npm run lint` | 約 20 s |
| L3 | 只在下表「L3」有寫的時候 | live／cloud／app／`ui_demo`／`ui_motion`／`ui_perf` | 分鐘級 |

Fast Path 與正規化的改動，flow 層的整條路由都會跑到，所以 L1 一定要跑；L1 只要 3 秒，不要省。

| 改到 | L0（`pytest` 加這些路徑） | L3 |
|---|---|---|
| `router/fast_path.py`、`normalize.py`、`lexicon.py`、`vocab/lexicon.json` | `tests/unit/test_completeness.py tests/unit/test_endpoint.py tests/unit/test_domain.py tests/unit/test_normalize.py tests/unit/test_lexicon.py tests/flow/test_route_fast.py tests/flow/test_typo.py tests/flow/test_route_corpus.py` | — |
| `router/slow_path.py`、`llm_client.py`、`tools.py`（含 prompt） | `tests/unit/test_guard.py tests/unit/test_tools.py tests/unit/test_llm_client.py tests/flow/test_route_slow.py tests/flow/test_route_guard.py tests/flow/test_route_multistep.py` | `pytest -m live -s` |
| `router/cloud_client.py`（含熔斷範圍 `trip_on_timeout`）、`memory.py`、`hant.py` | `tests/unit/test_cloud_client.py tests/flow/test_route_cloud.py tests/flow/test_route_web_answer.py` | 動到提示詞或雲端工具才跑 `pytest -m cloud -s`（會產生費用） |
| `router/domain.py`、`catalog.py`、`local_actions.py`、`choices.py`（找檔案） | `tests/unit/test_domain.py tests/unit/test_find.py tests/unit/test_choice.py tests/flow/test_route_find.py` | — |
| `router/context.py`、`native.py`、`actions.py`（前景、常用功能、分頁、看畫面） | `tests/unit/test_context.py tests/unit/test_native.py tests/flow/test_route_context.py tests/flow/test_route_tabs.py tests/flow/test_route_screen.py tests/runtime/test_applescript.py` | `pytest -m app -v` |
| `router/fast_path.py` 的點歌規則（`_r_music`、`_r_music_feedback`）、詞庫 `music`、`app_shortcuts` 的 `play_pause`／`next_track`／`prev_track`、`completeness` 的點歌判斷 | `tests/flow/test_route_music.py tests/unit/test_completeness.py tests/unit/test_endpoint.py tests/flow/test_route_context.py`（規則會連動其他指令，再加上第一列 `fast_path.py` 的檔案） | — |
| `router/fast_path.py` 的 `answer_mode()`、`_r_weather`、`weather_client.py`、`weather_prefs.py`、`profile_store.py`、`weather_followup.py`、`query_intent.py` 的 `_weather`／`_home_city`／`parse_city_reply`、`local_actions.py` 的 `get_weather`／`remember_home`、`pipeline.py` 的冷啟動接續與 chat 轉交、`slow_path.execute_chat_only`、詞庫 `weather`（縣市別名與座標）、`mac_ai/config.py` 的天氣參數 | `tests/unit/test_answer_mode.py tests/unit/test_weather_client.py tests/unit/test_weather_prefs.py tests/unit/test_profile_store.py tests/unit/test_weather_followup.py tests/unit/test_query_intent.py tests/flow/test_route_weather.py tests/flow/test_route_cloud.py tests/unit/test_completeness.py tests/unit/test_endpoint.py tests/unit/test_music_db.py`（網路一律用 `tests/support/weather.py` 的 `net` 假來源，離線測試不連真的氣象署或 Open-Meteo；資料用 `tests/fixtures/weather/` 實際抓回的回應） | 實機延遲與兩來源一致性：計畫書 `docs/plans/completed/2026-11-21-web-answer.md` §9（R1、R5，會用真的網路；氣象署要 `CWA_API_KEY`） |
| `router/search_client.py`、`sources.py`、`cloud_client.py` 的 `web_answer_messages`／`cloud_tools`、`slow_path.validate_calls` 的 answer 改寫、`local_actions.py` 的 `web_answer`／`open_source`、`pipeline.py` 的來源追問、`memory.py` | `tests/unit/test_search_client.py tests/unit/test_sources.py tests/unit/test_web_answer_prompt.py tests/flow/test_route_web_answer.py tests/flow/test_route_cloud.py`（網路一律用 `tests/support/web.py` 的 `brave` 假 Brave 與 `cloud` 假雲端，離線測試不連真的搜尋或雲端；樣本回應 `tests/fixtures/web/` 是依官方文件欄位**手寫**的，Brave 條款不准存真實回應） | 真實延遲與正確性：`python bench/web_answer.py --show`（R2、R3、R4，連真的 Brave 與雲端，**會產生費用**，需要兩把金鑰）；回答那次的延遲拆解（首位元組、推理段、答案段、各逾時上限的失敗率）：`python bench/web_answer_latency.py`（同上，會產生費用） |
| `router/query_intent.py`、`fast_path.py` 的 `_route_query`、詞庫 `query_cues`、`search_prefs.py`、`local_actions.py` 的 `federated_search`／`record_explicit_search`、`choices.py` 的 `WebItem`、`pipeline._escalate_to_llm`（查詢路由，[0023](../decisions/0023-query-routing.md)） | `tests/unit/test_query_intent.py tests/unit/test_routing_snapshot.py tests/unit/test_answer_mode.py tests/unit/test_search_prefs.py tests/flow/test_route_verify.py tests/flow/test_route_federated.py tests/flow/test_route_find.py tests/flow/test_route_web_answer.py tests/flow/test_route_cloud.py tests/unit/test_completeness.py tests/unit/test_endpoint.py`。快照（`tests/fixtures/routing/snapshot.json`，語料 `corpus.json` 只加不刪）有差異時，先把「舊結果 → 新結果」逐句列給使用者確認，確認後才用 `ROUTING_SNAPSHOT_UPDATE=1` 重產；聯合搜尋的假 mdfind 用 `find_env`、假 Brave 用 `tests/support/web.py` | `python bench/routing_latency.py`：`match()` 在語料上的 p95 ≤ 0.098 ms（P0 基線 0.089 ms 的 110%）；聯合搜尋含真實 Brave 的延遲還沒有 bench（計畫 L2 未做） |
| `router/youtube_client.py`（InnerTube 解析、接受規則、挑 MV、退回搜尋頁、`clientVersion`） | `tests/unit/test_youtube_client.py tests/unit/test_pinyin.py tests/runtime/test_youtube_search.py tests/flow/test_route_music.py` | 實機搜尋與延遲：計畫書 `docs/plans/completed/2026-11-21-music-play.md` §7.2（R1～R5，會用真的網路；只打 InnerTube，不用金鑰） |
| `router/music_prefs.py`（事件、計分、`terms`、拼音矯正、定時整理排程） | `tests/unit/test_music_prefs.py tests/unit/test_pinyin.py tests/runtime/test_music_maintenance.py tests/runtime/test_stt_client.py::test_t8_music_terms_correction_and_maintenance_do_not_touch_stt_hints`（T8：矯正與整理不影響 STT 提示詞） | — |
| `mac_ai/db/`（`ensure_schema`、migration、`rebuild`）、`assistant.py` 的 `QueuedJob`／`MusicMaintenance` | `tests/unit/test_music_db.py tests/runtime/test_music_db_runtime.py tests/runtime/test_music_maintenance.py tests/runtime/test_assistant.py` | — |
| `local_actions.py` 的 `play_media`、`music_*`、播放控制鏈（`_background_control`、`_record_control`）、`native.py` 的 `press_button_in_window`／`post_media_key`、`actions.py` 的 `open_music_video` | `tests/flow/test_route_music.py tests/unit/test_native.py tests/flow/test_route_context.py tests/runtime/test_applescript.py` | 實機（Safari 腳本、AX、媒體鍵）沒有自動測試，只能照計畫書 §7.3 人工驗收 |
| `router/pipeline.py`（主動展開） | `tests/flow/test_route_expand.py tests/unit/test_ui_contract.py` | — |
| `stt/completeness.py`、`stt/endpoint.py`、`wake.py` | `tests/unit/test_completeness.py tests/unit/test_endpoint.py tests/unit/test_wake.py` | 調參數用 `scripts/stt_bench.py` A/B |
| `stt/client.py`、`vad.py`、`mic.py` | `tests/runtime/test_stt_client.py tests/runtime/test_vad.py` | 語音實機（人工） |
| `stt/xasr_server.py`、`xasr_hotwords.py`（備用辨識引擎）、`vocab/stt_hints.txt` 的熱詞編碼 | `tests/unit/test_xasr_hotwords.py tests/runtime/test_xasr_server.py`（假引擎測協定；有模型檔才多跑 1 項真模型與 `stt_hints` 快照比對，缺檔自動 skip） | 協定對照：先起 macos-stt 或 `python -m mac_ai.stt.xasr_server`，再 `pytest -m live tests/real/test_stt_protocol_live.py -v`，**兩個引擎各跑一次**，7 項都要過；準確度與延遲用 `scripts/stt_bench.py run --corpus bench/human --mode client --label <引擎>` 對照（要先手動起伺服器，gate 的定義與 bench 內建判定的差異見計畫書 `docs/plans/completed/2026-11-21-stt-engine-xasr.md` §7.2）；模擬 Windows（乾淨 venv、`sys.platform` 打樁、`win_amd64` 安裝檔）的步驟見計畫書 §7.3 W1～W5；語音實機（人工） |
| `assistant.py` | `tests/runtime/test_assistant.py tests/runtime/test_runlog.py tests/runtime/test_ui_bus.py` | — |
| `ui_bus.py`、UI 協定 | `tests/unit/test_ui_contract.py tests/runtime/test_ui_bus.py`＋`node --test test/ipc-channels.test.js test/protocol.test.js` | `ui_demo.py --chat-test` |
| `services.py`、`main.py`、`config.py` 的 `STT_ENGINE`／`XASR_*` | `tests/runtime/test_services.py tests/runtime/test_main.py`（含引擎選擇、非 macOS 拒絕 macos、monkeypatch `sys.platform='win32'` 的預設引擎） | `python main.py --stt-engine xasr` 能起、`/health` 回 `engine: xasr`、Ctrl+C 收乾淨（需 `AI_models/x-asr-zh-tw-native-itn/`） |
| `scripts/acceptance.py` | `tests/unit/test_acceptance.py` | — |
| `docs/flows/`、`.importlinter`、新增或改名 `mac_ai/` 的模組與函式 | `tests/unit/test_docs_flows.py`（流程圖規範、文件識別字、例外表與 router 模組分層）＋`lint-imports` | GitHub 或 VS Code 預覽 Mermaid 能渲染 |
| `desktop/` 任一檔 | `cd desktop && node --test test/<同名或相關>.test.js` | 見下一節 |

## 視覺驗收：以量化為準

桌面角色的畫面與演出，**通過與否只看腳本的 PASS／FAIL 與結束碼**，不靠看截圖判定（ADR [0018](../decisions/0018-visual-checks-quantitative.md)）：

| 改到 | 平常（L1） | 只在這些情況跑實機（L3） |
|---|---|---|
| 演出常數、注意力導演（`renderer/animation/`、`domain/attention.js`） | `node --test`（`motion-sim.test.js` 用 `dev/motion-metrics.js` 同一份指標模擬 T2／T3） | `python scripts/ui_demo.py --capture <dir>`（逐張比對動作、表情、手勢、情境）；改到幀迴圈、更新頻率或時序時才跑 `python scripts/ui_motion.py`（閒置 10 分鐘＋聆聽 10 輪，標準不縮短） |
| 模型、rig profile、語意名稱 | `node --test`（`rig.test.js`） | `ui_demo.py --capture <dir>` |
| 渲染迴圈、幀率、緩衝 | `node --test`（`frame-rate`／`render-buffers`／`physics-clock`） | `python scripts/ui_perf.py`（CPU、RSS、幀間隔） |
| 聊天室、輸入框、選擇清單 | `node --test`（`chat`／`ipc-channels`） | `ui_demo.py --chat-test`／`--focus-test N`／`--choices-test`／`--interact-test` |

- `--capture` 產生的 PNG 只拿來除錯（FAIL 時看哪裡不對），不作為通過條件。
- 量化指標只能證明「符合定義、沒有退步」，不能證明「好看、自然」。**人眼仍是最準的判準**：演出、表情、動作手感有明顯改動時，回報裡要提醒使用者親自看一次，但這不是通過條件，也不要聲稱「看起來自然」。

## 測試規範

- **測試分層**（`tests/`，依目錄自動加 marker）：`unit/`（單一模組、全假物件）→ `flow/`（`process_voice_command` 整條路由）→ `runtime/`（真的執行緒、socket、子程序、osacompile）→ `real/`（live／cloud／app 實測，預設不跑）。新測試放在能測到它的最低一層；同一主題集中在一個檔案，不要另開檔。
- **共用假物件放 `tests/support/`，不要在測試檔裡再寫一份**：`llm.py`（`tc()` 組 tool_call、`FakeLLM` 依序／plan＋chat／依原句回覆並記錄 calls／plans／chats／texts、`forbid_llm`、`llm_always`）、`expect.py`（預期指令 `APP`／`VOL`／`SEARCH`… 與 `fast_matches`）、`clock.py`（假時鐘，conftest 的 `clock` fixture）、`typo_corpus.py`／`route_corpus.py`（語料）、`stt.py`／`endpoint.py`／`screen.py`（假 STT server、端點模擬器、螢幕環境）、`real.py`（被 conftest 換掉前的真函式）。conftest 的 `sh` 用 `sh.fail(指令)` 讓指定指令失敗。
- 離線測試是預設且必須通過；`tests/conftest.py` 已經：重設 LLM 熔斷、**讓 `urllib.request.urlopen` 預設拋錯**（不會誤打本機 server）、把已安裝 App 清單固定為空、把 `normalize`／`latency`／`commands` 三種 log 導到暫存目錄（測試不得寫進專案的 `logs/`）。
- 需要的假環境用現成 fixture：`sh`（記錄 `subprocess.run`）、`find_env`（假家目錄＋假 `mdfind`）、`installed_apps`、`projects`、`front`（假的前景 App 與網站；預設是「讀不到」）。**測試不得依賴這台電腦裝了哪些 App，也不得真的執行 `open`／`osascript`／`mdfind`。**
- 時間相關邏輯（喚醒會話、選擇清單 TTL、端點偵測）用注入的時鐘測，不要 `sleep`。
- runtime 測試不要讓收尾空等：產品端等待 stop 的輪詢間隔做成可注入（`SttClient.RECV_POLL_S`、`assistant.WORKER_POLL_S`，由 `tests/runtime/conftest.py` 調成 0.02 s）；假 server 收尾用短輪詢；不讀資料的 WebSocket 客戶端要設短的 `close_timeout`。
- 修 bug 先寫能重現的測試；重構要確認前後行為一致。
- **YouTube fixture**（`tests/fixtures/youtube/`，55 個檔、約 419 KB；載入與假 `urlopen` 在 `tests/support/youtube.py`）：2026-11-21 用真的 InnerTube 抓的回應（`web_*`、`ytm_*`、`home_*`），`index.json` 記每個檔的搜尋字。**重新抓取只打 InnerTube**（不需金鑰、不扣配額）；重新抓取與縮小用 `scripts/capture_youtube_fixtures.py`（`fetch` 抓原始回應到 repo 外的資料夾、`shrink` 縮成 fixture；請求格式與正式程式相同，縮小規則寫在腳本裡，`tests/unit/test_youtube_fixture_tool.py` 鎖住規則；重抓後內容變了，要重跑 pytest 並先看過再改測試預期值），`test_fixture_directory_size` 檢查總大小。邊界案例（Shorts、廣告、不合法 videoId…）從真實 fixture 複製再改欄位，標示「衍生」。測試不得連網、不得真的執行 `open`／`osascript`，也不得寫專案的 `data/`（用 `tmp_path`）。
- 路由、LLM 工具或 prompt 的改動要另外跑 `pytest -m live`（需 server）；不能跑就明說「live 未驗證」。
- **錯字語料**（資料在 `tests/support/typo_corpus.py`，測試在 `tests/flow/test_typo.py`）：每句跑完整 `process_voice_command`，LLM 一被呼叫就失敗（矯正必須在正規化＋Fast Path 完成）。新增誤聽詞或常用功能說法時順手加案例；類別代號給 `scripts/acceptance.py` 算矯正率。
- **真實 App 驗收**（`tests/real/test_app_real.py`，marker `app`，預設不跑）：只操作測試自己開的視窗並在收尾關掉；Terminal 要等 shell 閒置才關（否則跳「終止程序」對話框卡住）；測試期間以 `caffeinate` 防止螢幕自動鎖定（底層操作不算使用者活動）。改 `front_action`、`app_shortcuts`、`native.py`、站內搜尋或前景讀取時要跑。離線測試的 `native_calls` fixture 會攔下並記錄所有底層操作。
- **已知問題用 `xfail(strict=True)` 記錄**並寫明日期與實測結果，修好時會提醒；不要用 skip 掩蓋。
- VAD 相關測試依賴 `AI_models/silero_vad.onnx`，缺檔會 skip；目前的 5 個 skipped 是 `tests/runtime/test_applescript.py` 的「沒有安裝 Google Chrome」，屬正常。
- 格式合法、測試通過、結束碼 0 都不等於端到端正確；STT（麥克風、語音辨識）沒有自動測試，語音實機仍要人工確認，不要聲稱已驗證。
