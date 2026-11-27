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
python3 scripts/check_file_lines.py   # 程式檔（含測試）行數上限 800（repo 根目錄執行；見 ADR 0028）
python main.py --text 打開小算盤           # 不啟動服務、不檢查喚醒詞，直接跑一句（改路由後的最快實測方式；點歌句會真的搜尋並開 Safari，並寫專案的 data/music.sqlite3）
python main.py [--no-stt] [--ui] [--device N]
python build_vocab.py                     # 改 vocab/lexicon.json 後重產 stt_hints／userdict（main.py 啟動時也會自動跑）
python scripts/ui_demo.py [--hold|--choices-test|--chat-test [N]]   # 不用麥克風預覽桌面角色；--chat-test 驗聊天室（會短暫出現視窗）
python scripts/ui_motion.py [--skip-idle|--skip-listen]   # 桌面角色動作軌跡指標（擬人化 R2 閒置 10 分鐘／R3 聆聽 10 輪；量測期間不要動滑鼠）
```

基線：`pytest` 必須 **0 failed**（測試數會隨功能增加，不要硬記數字；最近一次實測 2026-11-27：3412 passed、6 skipped、126 deselected，約 44 秒）；`node --test` 0 fail（同日 311 pass）。已知問題：`tests/runtime/test_xasr_server.py::test_unknown_http_path_is_404` 的 teardown 逾時 1 error，2026-11-25 起出現，2026-11-27 在改動前的程式上也每次重現，與當次改動無關；出現時把改動前後各跑一次這個檔確認。歷次數字看 git 歷史。新增行為要新增測試。

## 分層節奏：改什麼跑什麼

不要每改一行就跑完整測試。由小到大，前一層過了才往下：

| 層 | 什麼時候 | 跑什麼 | 約耗時 |
|---|---|---|---|
| L0 | 每次改動 | 下表「L0」那一欄（只跑相關檔案） | 1～5 s（runtime 檔案較慢） |
| L1 | 一個功能改完 | `pytest -m "unit or flow"`；改到 `desktop/` 加 `cd desktop && node --test`，改到 `renderer/` 再加 `npm run typecheck` | 3～6 s |
| L2 | commit 前 | `pytest`（離線全部，0 failed）＋`uvx ruff@0.17.6 check .`＋`uvx --from ast-grep-cli@0.51.2 ast-grep scan`（改了 `rules/` 加 `ast-grep test`）＋`uvx --from import-linter==2.16 --with grimp==3.19 lint-imports`＋`python3 scripts/check_file_lines.py`；改到 `desktop/` 加 `node --test && npm run typecheck && npm run lint` | 約 20 s |
| L3 | 只在下表「L3」有寫的時候 | live／cloud／app／`ui_demo`／`ui_motion`／`ui_perf` | 分鐘級 |

Fast Path 與正規化的改動，flow 層的整條路由都會跑到，所以 L1 一定要跑；L1 只要 3 秒，不要省。

| 改到 | L0（`pytest` 加這些路徑） | L3 |
|---|---|---|
| `router/fast_path.py`、`normalize.py`、`lexicon.py`、`vocab/lexicon.json` | `tests/unit/test_completeness.py tests/unit/test_endpoint.py tests/unit/test_domain.py tests/unit/test_normalize.py tests/unit/test_lexicon.py tests/flow/test_route_fast.py tests/flow/test_typo.py tests/flow/test_route_corpus.py` | — |
| `router/slow_path.py`、`llm_client.py`、`tools.py`（含 prompt） | `tests/unit/test_guard.py tests/unit/test_tools.py tests/unit/test_llm_client.py tests/flow/test_route_slow.py tests/flow/test_route_guard.py tests/flow/test_route_multistep.py` | `pytest -m live -s` |
| `router/cloud_client.py`（含熔斷範圍 `trip_on_timeout`）、`memory.py`、`hant.py` | `tests/unit/test_cloud_client.py tests/flow/test_route_cloud.py tests/flow/test_route_web_answer.py` | 動到提示詞或雲端工具才跑 `pytest -m cloud -s`（會產生費用） |
| `router/domain.py`、`action_plan.py`、`catalog.py`、`find_tools.py`、`choices.py`（找檔案） | `tests/unit/test_domain.py tests/unit/test_find.py tests/unit/test_choice.py tests/flow/test_route_find.py` | — |
| `router/proc.py`、`router/__init__.py`、新增或搬移 `router/` 的 import | `tests/runtime/test_import_footprint.py tests/unit/test_find.py`＋`lint-imports` | — |
| `router/context.py`、`native.py`、`actions.py`、`exec_context.py`、`front_tools.py`、`browser_tools.py`、`screen_tools.py`（前景、常用功能、分頁、看畫面） | `tests/unit/test_context.py tests/unit/test_native.py tests/flow/test_route_context.py tests/flow/test_route_tabs.py tests/flow/test_route_screen.py tests/runtime/test_applescript.py` | `pytest -m app -v` |
| `router/fast_path.py` 的點歌規則（`_r_music`、`_r_music_feedback`）、詞庫 `music`、`app_shortcuts` 的 `play_pause`／`next_track`／`prev_track`、`completeness` 的點歌判斷 | `tests/flow/test_route_music.py tests/flow/test_route_music_control.py tests/unit/test_completeness.py tests/unit/test_endpoint.py tests/flow/test_route_context.py`（規則會連動其他指令，再加上第一列 `fast_path.py` 的檔案） | — |
| `router/fast_path.py` 的 `answer_mode()`、`_r_weather`、`weather_client.py`、`weather_prefs.py`、`profile_store.py`、`weather_followup.py`、`query_intent.py` 的 `_weather`／`_home_city`／`parse_city_reply`、`answer_tools.py` 的 `get_weather`／`remember_home`、`pipeline.py` 的冷啟動接續與 chat 轉交、`slow_path.execute_chat_only`、詞庫 `weather`（縣市別名與座標）、`mac_ai/config.py` 的天氣參數 | `tests/unit/test_answer_mode.py tests/unit/test_weather_client.py tests/unit/test_weather_prefs.py tests/unit/test_profile_store.py tests/unit/test_weather_followup.py tests/unit/test_query_intent.py tests/flow/test_route_weather.py tests/flow/test_route_cloud.py tests/unit/test_completeness.py tests/unit/test_endpoint.py tests/unit/test_music_db.py`（網路一律用 `tests/support/weather.py` 的 `net` 假來源，離線測試不連真的氣象署或 Open-Meteo；資料用 `tests/fixtures/weather/` 實際抓回的回應） | 實機延遲與兩來源一致性：下方「L3 實測程序」的天氣與長尾回答 R1、R5（會用真的網路；氣象署要 `CWA_API_KEY`） |
| `router/search_client.py`、`sources.py`、`cloud_client.py` 的 `web_answer_messages`／`cloud_tools`、`slow_path.validate_calls` 的 answer 改寫、`answer_tools.py` 的 `web_answer`／`open_source`、`pipeline.py` 的來源追問、`memory.py` | `tests/unit/test_search_client.py tests/unit/test_sources.py tests/unit/test_web_answer_prompt.py tests/flow/test_route_web_answer.py tests/flow/test_route_cloud.py`（網路一律用 `tests/support/web.py` 的 `brave` 假 Brave 與 `cloud` 假雲端，離線測試不連真的搜尋或雲端；樣本回應 `tests/fixtures/web/` 是依官方文件欄位**手寫**的，Brave 條款不准存真實回應） | 真實延遲與正確性：`python bench/web_answer.py --show`（R2、R3、R4，連真的 Brave 與雲端，**會產生費用**，需要兩把金鑰）；回答那次的延遲拆解（首位元組、推理段、答案段、各逾時上限的失敗率）：`python bench/web_answer_latency.py`（同上，會產生費用） |
| `router/query_intent.py`、`fast_path.py` 的 `_route_query`、詞庫 `query_cues`、`search_prefs.py`、`find_tools.py` 的 `federated_search`／`record_explicit_search`、`choices.py` 的 `WebItem`、`pipeline._escalate_to_llm`（查詢路由，[0023](../decisions/0023-query-routing.md)） | `tests/unit/test_query_intent.py tests/unit/test_routing_snapshot.py tests/unit/test_answer_mode.py tests/unit/test_search_prefs.py tests/flow/test_route_verify.py tests/flow/test_route_federated.py tests/flow/test_route_find.py tests/flow/test_route_web_answer.py tests/flow/test_route_cloud.py tests/unit/test_completeness.py tests/unit/test_endpoint.py`。快照（`tests/fixtures/routing/snapshot.json`，語料 `corpus.json` 只加不刪）有差異時，先把「舊結果 → 新結果」逐句列給使用者確認，確認後才用 `ROUTING_SNAPSHOT_UPDATE=1` 重產；聯合搜尋的假 mdfind 用 `find_env`、假 Brave 用 `tests/support/web.py` | `python bench/routing_latency.py`：`match()` 在語料上的 p95 ≤ 0.098 ms（P0 基線 0.089 ms 的 110%）；聯合搜尋含真實 Brave 的延遲還沒有 bench（計畫 L2 未做） |
| `router/youtube_client.py`（InnerTube 解析、接受規則、挑 MV、退回搜尋頁、`clientVersion`） | `tests/unit/test_youtube_client.py tests/unit/test_pinyin.py tests/runtime/test_youtube_search.py tests/flow/test_route_music_play.py` | 實機搜尋與延遲：下方「L3 實測程序」的點歌 R1～R5（會用真的網路；只打 InnerTube，不用金鑰） |
| `router/music_prefs.py`（事件、計分、`terms`、拼音矯正、定時整理排程）、`router/pinyin.py` | `tests/unit/test_music_prefs.py tests/unit/test_pinyin.py tests/runtime/test_music_maintenance.py tests/runtime/test_stt_client.py::test_t8_music_terms_correction_and_maintenance_do_not_touch_stt_hints`（T8：矯正與整理不影響 STT 提示詞） | — |
| `mac_ai/db/`（`ensure_schema`、migration、`rebuild`）、`assistant.py` 的 `QueuedJob`／`MusicMaintenance` | `tests/unit/test_music_db.py tests/runtime/test_music_db_runtime.py tests/runtime/test_music_maintenance.py tests/runtime/test_assistant.py` | — |
| `music_tools.py`（`play_media`、`music_*`、播放控制鏈 `_background_control`／`_record_control`、`open_music_video`）、`front_tools.py` 的 `front_action`、`prefs_db.py`、`native.py` 的 `press_button_in_window`／`post_media_key` | `tests/flow/test_route_music_play.py tests/flow/test_route_music_control.py tests/flow/test_route_music_feedback.py tests/unit/test_native.py tests/flow/test_route_context.py tests/runtime/test_applescript.py` | 實機（Safari 腳本、AX、媒體鍵）沒有自動測試，只能照下方「L3 實測程序」的點歌 H1～H5 人工驗收 |
| `router/pipeline.py`（主動展開） | `tests/flow/test_route_expand.py tests/unit/test_ui_contract.py` | — |
| `stt/completeness.py`、`stt/endpoint.py`、`wake.py` | `tests/unit/test_completeness.py tests/unit/test_endpoint.py tests/unit/test_wake.py` | 調參數用 `scripts/stt_bench.py` A/B |
| `stt/client.py`、`vad.py`、`mic.py` | `tests/runtime/test_stt_client.py tests/runtime/test_vad.py` | 語音實機（人工） |
| `stt/xasr_server.py`、`xasr_hotwords.py`（備用辨識引擎）、`vocab/stt_hints.txt` 的熱詞編碼 | `tests/unit/test_xasr_hotwords.py tests/runtime/test_xasr_server.py`（假引擎測協定；有模型檔才多跑 1 項真模型與 `stt_hints` 快照比對，缺檔自動 skip） | 協定對照：先起 macos-stt 或 `python -m mac_ai.stt.xasr_server`，再 `pytest -m live tests/real/test_stt_protocol_live.py -v`，**兩個引擎各跑一次**，7 項都要過；準確度與延遲用 `scripts/stt_bench.py run --corpus bench/human --mode client --label <引擎>` 對照（要先手動起伺服器，gate 的定義與 bench 內建判定的差異見下方「L3 實測程序」的 STT 備用引擎）；模擬 Windows（乾淨 venv、`sys.platform` 打樁、`win_amd64` 安裝檔）的步驟見同一節 W1～W5；語音實機（人工） |
| `assistant.py` | `tests/runtime/test_assistant.py tests/runtime/test_runlog.py tests/runtime/test_ui_bus.py` | — |
| `ui_bus.py`、UI 協定 | `tests/unit/test_ui_contract.py tests/runtime/test_ui_bus.py`＋`node --test test/ipc-channels.test.js test/protocol.test.js` | `ui_demo.py --chat-test` |
| `services.py`、`main.py`、`config.py` 的 `STT_ENGINE`／`XASR_*` | `tests/runtime/test_services.py tests/runtime/test_main.py`（含引擎選擇、非 macOS 拒絕 macos、monkeypatch `sys.platform='win32'` 的預設引擎） | `python main.py --stt-engine xasr` 能起、`/health` 回 `engine: xasr`、Ctrl+C 收乾淨（需 `AI_models/x-asr-zh-tw-native-itn/`） |
| `scripts/acceptance.py` | `tests/unit/test_acceptance.py` | — |
| `docs/flows/`、`.importlinter`、新增或改名 `mac_ai/` 的模組與函式 | `tests/unit/test_docs_flows.py`（流程圖規範、文件識別字、例外表與 router 模組分層）＋`lint-imports` | GitHub 或 VS Code 預覽 Mermaid 能渲染 |
| `AGENTS.md`、`docs/decisions/`（含索引 `README.md`）、`docs/guides/` | `tests/unit/test_docs_index.py`（索引與 ADR 一致、〔擋：…〕與強制方式引用存在、文件裡的 repo 路徑與連結存在） | — |
| `desktop/` 任一檔 | `cd desktop && node --test test/<同名或相關>.test.js` | 見下一節 |

## L3 實測程序（從結案計畫書搬來）

下面各節從結案計畫書的「驗收計畫」逐字搬來（2026-11-27，[0031](../decisions/0031-adr-rationale-only.md)），只把標題降一級、修正相對連結。表裡的 §、代號（W12、M11、X14…）指原計畫書；當時的實測結果與使用者接受的現況在原計畫書「實測結果」一節與對應的 ADR。會連網、產生費用或佔用螢幕，只在上面對照表的 L3 欄寫到時才跑。

### 天氣與長尾回答

原文：[`2026-11-21-web-answer.md`](../plans/completed/2026-11-21-web-answer.md) §7.2、§7.3。現況：R2 未達門檻，使用者接受（[0022](../decisions/0022-web-answer.md)「後果」）；依摘錄回答的逾時上限後來改成 10 秒（[0024](../decisions/0024-home-city-and-answer-timeout.md)，延遲拆解用 `python bench/web_answer_latency.py`）。

#### 原 7.2 實際環境（會連網，數字寫進 §9；門檻已確認：R1～R4 見 W12，R5 見 W17）

| # | 項目 | 做法 | 建議門檻 | 失敗處置 |
|---|---|---|---|---|
| R1 | 天氣延遲 | 20 句天氣問句，每句獨立程序，量從比對到回覆 | p50 ≤ 1.0 s、p95 ≤ 2.0 s（比照點歌） | 加短期快取（另外問你） |
| R2 | 長尾延遲 | 20 題固定題目（人物、時事、比分、營業時間各 5 題），量到回覆出現 | p50 ≤ 3.0 s、p95 ≤ 6.0 s | 換後端或縮減摘錄 |
| R3 | 長尾正確 | 同 R2，逐題對照摘錄與實際來源 | 至少 18/20 與來源一致；**編造 0 題**（摘錄沒有的事實寫成答案） | 調第二次呼叫的提示詞；仍不行就停下來問你 |
| R5 | 跨來源一致性（W17） | P0-2 同一時刻查 22 縣市，比較氣象署與 Open-Meteo 的「今天白天」最高溫 | 差 ≤ 3 °C 的縣市至少 20/22 | 列出差距大的縣市，停下來問你（沒有金鑰時是否仍用 Open-Meteo 回答） |
| R6 | 預設地點實際使用 | 實機明說「高雄天氣」2 次、「台北天氣」1 次後說「明天天氣」 | 回答的是高雄市，且資料庫只多 3 筆 | 修記錄或算法 |
| R4 | 雲端選對工具 | `python bench/web_answer.py`（20 題問句，看雲端第一次呼叫選了什麼；會產生費用） | 20 題至少 18 題選 `web_answer`；開網頁句不會變成回答（改寫後 100%，離線測試已驗證） | 調 `WEB_ANSWER_TOOL` 的描述（不動 `CLOUD_SYSTEM_PROMPT`） |

#### 原 7.3 人工與介面

| # | 項目 | 做法 | 通過標準 |
|---|---|---|---|
| H1 | 泡泡長度 | `python scripts/ui_demo.py --chat-test` 或實機各說 5 句 | 天氣 1 句、長尾 1～2 句加追問；你判定不臃腫 |
| H2 | 追問開來源 | 實機長尾問一句再說「好」 | 開的是對的來源頁 |

### 點歌

原文：[`2026-11-21-music-play.md`](../plans/completed/2026-11-21-music-play.md) §7.2、§7.3。現況：YouTube Data API 已在 M22 移除，R4、R5① 只留歷史結果（表內已註明）。

> ⚠️ 下表 R1、R2、R5 與 H1 用的 `python main.py --text` 不是隔離環境：有金鑰時會打真的雲端（會產生費用），也會升級並寫入專案正式的 `data/`（R1 的「清空快取」同樣是動正式資料）。跑之前先問使用者；只是要驗證行為時，改寫用暫存資料庫的腳本。

#### 原 7.2 真實環境（P6 一次；門檻已確認 M11）

API 用量：目前累計 **2,000 units**（P0-1），你定的提醒線是 **8,000 units**。下列項目預計再用 500 units（只有 R5），跑之前我會先報目前累計數字。

| # | 項目 | 做法 | 門檻 | 失敗時 |
|---|---|---|---|---|
| R1 | 沒有快取的延遲 | 20 個不重複的 query（清空快取），逐一 `python main.py --text 我想聽X`，取 `commands.jsonl` 的 `total_ms` | p50 ≤ 1.0 s、p95 ≤ 2.0 s | 回報數字與各階段耗時（InnerTube 兩邊、osascript），討論調逾時或預先抓取 |
| R2 | 有快取的延遲 | 同樣 20 句再跑一次 | p50 ≤ 0.3 s；網路請求 0 次 | 看找分頁的耗時（P0-3） |
| R3 | 播到想要的歌 | 30 句：20 首指定歌曲（中文 15、英文 5）、5 位歌手、5 個情境；判斷的是**最後實際播放的那支**，同時記錄每句的 `source`（mv／ytm／web） | 指定歌曲 ≥ 18/20 是那首歌（官方 MV 或官方音源；歌詞版、翻唱、伴奏不算）；歌手與情境由你判斷是否合理 | 依失敗句調整接受規則或挑 MV 的條件，補進 Y3～Y5 的語料 |
| R4 | API 不被用到 | R1～R3 跑完後查 `events` | `source=api` 的次數是 0；GCP 用量頁面這段期間沒有新增 | 查 InnerTube 失敗原因（M22 之後 API 已不存在，本項只留歷史結果） |
| R5 | 退回 | ① 暫時把 `config` 的兩個 InnerTube 網址改成無效位址（**不提交**，跑完還原並用 `git diff` 確認乾淨），跑 5 句；② 你把 `.env` 的金鑰拿掉（程式不改 `.env`），再跑 5 句 | ① 5/5 是 `source=api` 並開始播放；② 5/5 是 `source=page`、開搜尋頁並回 ⚠️ | 修退回（M22 之後只剩 ②：InnerTube 失敗 → 搜尋頁） |
| R6 | 啟動時間 | `main.py --no-stt` 啟動 5 次，比較有沒有 `ensure_schema` 的差值 | 中位數增加 ≤ 300 ms；超過就確認已經改成背景模式 | 依 M14 改背景 |
| R7 | Fast Path 比對耗時 | C1 加 C2 全部語料跑 `match()` 各 100 次 | 單句平均 ≤ 1 ms（和既有規則同一個量級） | 簡化 regex |

#### 原 7.3 人工（由你確認，我只回報觀察到的事實）

**操作 Safari 的規則**（P0 期間我誤關過你的視窗，之後一律照做）：
- 實測腳本只操作自己用 `make new document` 建立、**從回傳值取得參照**的視窗；不用 `front window` 或「最前面的視窗」判斷是不是自己開的。
- 結束時只關這些視窗；你原本的視窗與分頁不讀網址以外的內容、不關、不改。
- **關窗後 `windows` 清單可能還留一個幽靈項目**（P0-8 實測：`close` 成功、System Events 視窗數 0、`documents` 0，但 `count of windows` 仍是 1、該視窗 0 個分頁）。判斷有沒有關掉要看 `count of documents` 或 System Events，不要看 `windows`。
- 需要你的 Safari 狀態（例如 H4）時，先告訴你要做什麼，等你同意再做。

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| H1 | 有聲自動播放 | `main.py --text 我想聽晴天`，換 3 首 | 3/3 在 3 秒內有聲音 |
| H2 | 分頁重複使用 | 連續點 3 首不同的歌 | Safari 裡只有 1 個音樂分頁；同時只有 1 首在播 |
| H3 | 背景控制 | 前景是 VS Code，音樂分頁在背景視窗：說「下一首」「上一首」「暫停」「繼續」 | 4/4 作用在音樂分頁；Music App 沒有被啟動 |
| H4 | 分頁不在最上層 | ① 音樂分頁所在的視窗切到別的分頁，再說「下一首」；② 關掉 Safari 所有 YouTube 影片分頁（Music 不要在播）後再說「下一首」 | ① 媒體鍵作用在 YouTube（Safari 任何視窗任何分頁有 YouTube 影片分頁就會送，使用者 2026-11-21 決定 U2）；② 回 ⚠️「找不到正在播放的音樂」，不送媒體鍵、Music 沒有被啟動 |
| H5 | STT 實機 | 你對 10 首常聽的歌各說一次「我想聽 X」 | 記錄辨識原文、矯正結果、`source`、是否播對；**不設通過門檻，也不聲稱已驗證**，數字當作下一輪調整的依據 |

### STT 備用引擎

原文：[`2026-11-21-stt-engine-xasr.md`](../plans/completed/2026-11-21-stt-engine-xasr.md) §7.2、§7.3。

#### 原 7.2 真實模型執行（bench；沿用 2026-11-05 端點計畫的定義，X7）

指令：先起伺服器，再跑 `python scripts/stt_bench.py run --corpus bench/human --mode client --label <engine>`，最後跑 `report <macos.jsonl> <xasr.jsonl>`。兩個引擎在同一時段、同一台機器上跑。

| # | 指標 | 門檻 | 依據 | 不過時（X14） |
|---|---|---|---|---|
| S1 | A 類 E2E（說完 → final） | 中位 ≤ 800 ms、p90 ≤ 1200 ms | 2026-11-05 端點計畫 S1 | **停下來回報**（P0 推估可能不過，§1.6） |
| S4 | 伺服器收尾時間（`pre_delay_ms`＋`finish_ms`） | 中位 ≤ 150 ms | macos-stt 實測 68～86 ms。X-ASR 的 `pre_delay_ms` 回報的是 0.6 秒尾端靜音，但那是補進去的靜音，解碼它只要幾 ms，所以門檻只看實際花掉的時間（`finish_ms`），兩個欄位都照實記錄 | 停下來回報 |
| Q1 | A+C+F 意圖正確率 | ≥ 95% 且 ≥ macos | 端點計畫 Q1 | **記錄 FAIL，不阻擋**（P0 已知 79%） |
| Q2 | CER | ≤ macos＋1.0 pt | 端點計畫 Q2 | **記錄 FAIL，不阻擋**（P0 已知 10.0%） |
| Q3 | B 類提早截斷 | ≤ macos（真人 2/8） | 端點計畫 Q3（真人版） | 停下來回報 |
| Q4 | F 類句尾字 | 3/3 | 端點計畫 Q4 | 停下來回報 |
| R2 | D 類（20 秒純噪音，4 段） | 0 個指令命中、`max_len` 0 次 | 端點計畫 R2；誤觸發的成本最高 | 停下來回報 |
| P1 | 伺服器 CPU、記憶體 | 閒置（沒說話）CPU < **整機 5%**（8 個邏輯核心，等於單核 40%，X18）；RSS < 600 MB；RTF < 0.2 | 不能拖累 [0013](../decisions/0013-fullrig03-cpu-budget.md) 的整機預算。原本寫「單核 5%」是誤讀 0013 | 停下來回報 |

「停下來回報」的意思是：附上數字，由你決定要放寬門檻、調參數，還是接受現況；不會自己調。

**`stt_bench.py report` 內建的判定與這張表不同，看的時候要換算**：它的 S1 還要求「比 baseline 快 ≥ 40%」（那是 2026-11-05 端點計畫比較「改成 client 端點之前」的設計，拿來比兩個引擎沒有意義）；它的 S4 看的是 `pre_delay_ms + finish_ms`（xasr 的 `pre_delay_ms` 是補進去的 0.6 秒靜音，所以必然不過；本計畫的 S4 只看 `finish_ms`）；它的 P1 是「VAD 每窗耗時」，**不是**本表的伺服器 CPU／記憶體（那個要另外量，§1.7）；它的 Q3 是「被拆句，或意圖不對」，所以辨識錯字（例如把 Safari 聽成「沙發率」）也會算進去。本表的 S1、S4、P1 以這裡的定義為準。

- **輸出內容要另外人工核對**：CER 與意圖正確不等於文字正確。報告附上逐段文字對照（`report --detail`），人工看英文 App 名稱、數字、繁簡用字。
- Windows：能解碼即可。Windows 的 bench 只量 RTF 與 CER，不量意圖（路由執行器不能在 Windows 跑）。

#### 原 7.3 人工／介面驗收（P3）

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| M1 | macOS 實機語音 | `python main.py --stt-engine xasr --ui`，說 `manifest.csv` A、E、F 類各 3 句，以及「打開 VS Code」「打開 GitHub 專案」 | **沒有誤執行**（阻擋）；動作正確的句數照實記錄（備用引擎，不設句數門檻，X14） |
| M2 | 切回 macos | `python main.py`（不帶參數） | 行為與改動前相同，`/health` 回報的是 macos-stt |
| M3 | 斷線 | 執行中 kill xasr 伺服器 | 介面顯示 disconnected，伺服器重啟後自動恢復 |
以下 W1～W5 都在**本機 Mac 上模擬 Windows**（X16），不是 Windows 實機：

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| W1 | 模擬安裝環境＋啟動（S4） | 在 scratchpad 用 `uv venv --python 3.12` 開乾淨的 venv，只裝 `sherpa-onnx==2.0.2 sounddevice onnxruntime websockets numpy`（README 寫給 Windows 的清單），執行 `python -m mac_ai.stt.xasr_server` | `/health` 回 200；載入時間與 RSS 照實記錄 |
| W2 | 模擬平台判斷＋語音（S3） | `python -c "import sys, runpy, sounddevice, numpy, onnxruntime, websockets, sherpa_onnx; sys.platform = 'win32'; sys.argv = ['main.py', '--ui']; runpy.run_path('main.py', run_name='__main__')"`，說 5 句。這行只用來模擬，不加進產品程式。第三方套件要在改 `sys.platform` **之前**先 import，不然 `sounddevice` 會去載 Windows 的 DLL | 不帶 `--stt-engine` 也起的是 xasr；`/health` 回報 `engine: xasr`；沒有誤執行。如果有其他模組因為 `sys.platform` 被改而出錯，就記錄下來，W2 改標「無法模擬」，以 A8、A9 的自動化結果為準 |
| W3 | 模擬安裝環境的 import 鏈（S4） | 在 W1 的 venv 執行 `python -c "import mac_ai.stt.client, mac_ai.stt.mic, mac_ai.stt.xasr_server"` | 不會出錯。出錯就列出缺的模組（例如 `alembic`），回報，不在本計畫修（§1.4 最後一列） |
| W4 | 模擬安裝環境的 bench（S4） | 在 W1 的 venv 執行 `python scripts/stt_bench.py run --corpus bench/human --mode client --label xasr-winsim --only A` | 能跑完，A 類意圖與 S1 一樣；W3 失敗的話這項標記「受 W3 阻擋」 |
| W5 | Windows 安裝檔存在 | `uvx pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 --no-deps -d <scratchpad> sherpa-onnx==2.0.2 sherpa-onnx-core==2.0.2 onnxruntime==1.31.1 sounddevice==0.5.6 numpy==2.5.4 websockets==17.4` | 每個套件都下載得到 `win_amd64`（或 `py3-none-any`）的 wheel |

STT 語音實機沒有自動測試，M1～M3、W2 只能由你操作確認。Windows 上的 `main.py` 全流程不在範圍內（LLM 與執行器都不能跑）。

## 視覺驗收：以量化為準

桌面角色的畫面與演出，**通過與否只看腳本的 PASS／FAIL 與結束碼**，不靠看截圖判定（ADR [0018](../decisions/0018-visual-checks-quantitative.md)）：

| 改到 | 平常（L1） | 只在這些情況跑實機（L3） |
|---|---|---|
| 演出常數、注意力導演（`renderer/animation/`、`domain/attention.js`） | `node --test`（`motion-sim.test.js` 用 `dev/motion-metrics.js` 同一份指標模擬 T2／T3） | `python scripts/ui_demo.py --capture <dir>`（逐張比對動作、表情、手勢、情境）；改到幀迴圈、更新頻率或時序時才跑 `python scripts/ui_motion.py`（閒置 10 分鐘＋聆聽 10 輪，標準不縮短） |
| 模型、契約（`rig/contract.js`）、rig profile、語意名稱 | `node --test`（`rig.test.js`） | `ui_demo.py --capture <dir>` |
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
