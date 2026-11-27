# ADR 索引

ADR 只記「為什麼」：背景、實測、使用者的決定（[0031](0031-adr-rationale-only.md)）。**現行規則看 `docs/guides/coding-style.md`、`docs/guides/testing.md` 與 lint**；要知道理由或要推翻規則時，先在這裡找到相關的 ADR 再打開。

- **被修改／取代**：後來修改或取代這份部分內容的 ADR；細節看兩邊的狀態行。有值時，現行規則已整理在 coding-style 或 testing.md。
- **強制方式**：違反時會擋下來的檢查。`rule:` 是 `rules/` 的 ast-grep 規則，`eslint:` 是 `desktop/eslint-rules/` 的規則，`contract:` 是 `.importlinter` 的契約，`npm:` 是 `desktop/package.json` 的指令，`test:`／`script:` 是 repo 裡的檔案或目錄；`—` 表示只靠文字或人工。
- 新增 ADR、改狀態行時同步這張表；`tests/unit/test_docs_index.py` 檢查每份 ADR 剛好一列、標題與狀態和檔案一致、被修改／取代和狀態行一致、強制方式引用的東西都存在。

| 編號 | 標題 | 狀態 | 領域 | 被修改／取代 | 強制方式 |
|---|---|---|---|---|---|
| [0001](0001-fast-path-first.md) | 能用規則就不問 LLM（Fast Path 優先） | 採用 | 路由 | — | `rule:hot-path-no-io`、`contract:C4`、`test:tests/flow/test_typo.py` |
| [0002](0002-stable-prompt-prefix.md) | 本機 LLM 的提示詞前綴逐字穩定（prefix cache） | 採用 | 路由 | — | `rule:prompt-prefix-immutable`、`rule:urlopen-only-in-clients`、`test:tests/unit/test_tools.py` |
| [0003](0003-client-endpoint-detection.md) | 端點偵測在本端做（Silero VAD＋句子完整度） | 採用 | 語音 | — | `test:tests/unit/test_completeness.py`、`test:tests/unit/test_endpoint.py` |
| [0004](0004-wake-session.md) | 喚醒詞與 60 秒滑動會話；桌面角色用 Electron | 採用 | 語音 | — | `test:tests/unit/test_wake.py` |
| [0005](0005-local-first-find.md) | 找東西本機優先，找不到才轉交模型 | 採用 | 找檔案 | 0023 | `test:tests/unit/test_find.py`、`test:tests/flow/test_route_find.py` |
| [0006](0006-front-window-target.md) | 以前景視窗為對象；「按鈕」＝常用功能 | 採用 | 前景 | — | `test:tests/unit/test_context.py`、`test:tests/flow/test_route_context.py` |
| [0007](0007-no-model-for-negation.md) | 否定或敘述句不呼叫任何模型（G1） | 採用 | 路由 | — | `test:tests/unit/test_guard.py`、`test:tests/flow/test_route_guard.py` |
| [0008](0008-native-front-action.md) | 常用功能以底層 API 直接執行（native.py） | 採用 | 前景 | — | `test:tests/unit/test_native.py`、`test:tests/unit/test_context.py` |
| [0009](0009-cloud-optional.md) | 雲端是選用的加分：Slow Path 先問雲端，失敗退回本機 | 採用 | 雲端 | 0024 | `test:tests/flow/test_route_cloud.py` |
| [0010](0010-cloud-key-handling.md) | 雲端設定放 .env，金鑰只在 cloud_client 讀 | 採用 | 雲端 | — | `rule:secret-read-scope`、`rule:no-environ-write` |
| [0011](0011-cloud-tools-and-site-open.md) | 雲端專用工具 front_action／site_search；開網站「只放寬」 | 採用 | 雲端 | 0022 | `test:tests/flow/test_route_cloud.py` |
| [0012](0012-chat-window-expand.md) | 聊天室是獨立視窗，主動展開不搶焦點 | 採用 | 桌面 | — | `eslint:no-window-show`、`test:tests/flow/test_route_expand.py` |
| [0013](0013-fullrig03-cpu-budget.md) | fullrig03 定版模型；CPU 5% 指整台電腦；Electron 路線甲 | 採用 | 桌面 | 0032 | `script:scripts/ui_perf.py` |
| [0014](0014-desktop-layers.md) | 桌面前端分層，依賴只能往下 | 採用 | 桌面 | 0033 | `test:desktop/test/layers.test.js` |
| [0015](0015-humanize-procedural.md) | 擬人化：程序層接管頭身眼眉，用指標判定「無機械感」 | 採用 | 桌面 | 0018 | `test:desktop/test/motion-metrics.test.js`、`test:desktop/test/motion-sim.test.js` |
| [0016](0016-docs-structure.md) | 文件結構：指示牌、架構、ADR、流程、計畫、指南 | 採用 | 文件 | 0031 | `test:tests/unit/test_docs_index.py` |
| [0017](0017-renderer-typecheck.md) | 畫面程序的型別檢查：PIXI 用真實型別，其餘宣告為 any | 採用 | 桌面 | — | `npm:typecheck` |
| [0018](0018-visual-checks-quantitative.md) | 視覺驗收以量化指標判定，人眼不是通過條件 | 採用 | 桌面 | — | `script:scripts/ui_demo.py`、`script:scripts/ui_motion.py` |
| [0019](0019-music-play.md) | 點歌與播放控制：Fast Path 規則、InnerTube 搜尋、本機 SQLite、媒體鍵 | 採用 | 點歌 | 0024 | `test:tests/flow/test_route_music.py`、`test:tests/unit/test_native.py` |
| [0020](0020-stt-engine-xasr.md) | 語音辨識引擎可切換：macos-stt 或 X-ASR（Windows 專用的備用引擎） | 採用 | 語音 | — | `test:tests/runtime/test_xasr_server.py`、`test:tests/real/test_stt_protocol_live.py` |
| [0021](0021-weather-answer.md) | 天氣直接回答：開網頁或回答由規則決定、氣象署→Open-Meteo 備援、常問縣市 | 採用 | 回答 | 0023、0024 | `test:tests/flow/test_route_weather.py`、`rule:secret-read-scope` |
| [0022](0022-web-answer.md) | 長尾問題直接回答：Brave 搜尋＋雲端依摘錄回答，失敗退回開網頁 | 採用 | 回答 | 0023、0024 | `test:tests/flow/test_route_web_answer.py`、`rule:secret-read-scope` |
| [0023](0023-query-routing.md) | 「查」的統一路由：一個分類點、會變的事實一律查證、沒說範圍時聯合搜尋 | 採用 | 路由 | — | `test:tests/unit/test_query_intent.py`、`test:tests/unit/test_routing_snapshot.py` |
| [0024](0024-home-city-and-answer-timeout.md) | 居住地常駐資料、天氣冷啟動接續、依摘錄回答逾時不熔斷 | 採用 | 回答 | — | `test:tests/unit/test_profile_store.py`、`test:tests/unit/test_weather_followup.py` |
| [0025](0025-static-checks.md) | 靜態檢查分工：Ruff、ESLint 自訂規則、ast-grep 專案規則 | 採用 | 工具鏈 | — | `test:rule-tests`、`rule:subprocess-needs-timeout`、`rule:no-sleep-in-router`、`eslint:bus-loopback-only`、`eslint:secure-web-preferences`、`eslint:ipc-channel-literal` |
| [0026](0026-import-linter.md) | import-linter：Python 模組依賴契約 | 採用 | 工具鏈 | — | `contract:C1`、`contract:C2`、`contract:C3`、`contract:C4`、`contract:C5` |
| [0027](0027-flow-docs.md) | flows 文件規範：流程檔與結構檔、Mermaid 寫法、自動檢查 | 採用 | 文件 | — | `test:tests/unit/test_docs_flows.py` |
| [0028](0028-file-line-limit.md) | 程式檔行數上限 800 | 採用 | 工具鏈 | — | `script:scripts/check_file_lines.py` |
| [0029](0029-deps-converge.md) | 依賴收斂：比對與執行分開、底層工具歸位 | 採用 | 工具鏈 | — | `test:tests/runtime/test_import_footprint.py`、`contract:C4` |
| [0030](0030-router-feature-modules.md) | router 的執行器依功能分模組 | 採用 | 工具鏈 | — | `contract:C2`、`contract:C3` |
| [0031](0031-adr-rationale-only.md) | ADR 只記理由；現行規則以指南與自動檢查為準 | 採用 | 文件 | — | `test:tests/unit/test_docs_index.py` |
| [0032](0032-switchable-character.md) | 桌面角色可切換；Hiyori 只做動作、支援 pose3 | 採用 | 桌面 | 0033 | `test:desktop/test/rig.test.js`、`test:desktop/test/animator.test.js` |
| [0033](0033-rig-contract.md) | 模型契約統一：checkRig 拿模型對契約檢查，profile 選填 | 採用 | 桌面 | 0034 | `test:desktop/test/rig.test.js` |
| [0034](0034-model-agnostic-frontend.md) | 前端不寫死模型：lint 擋常值，換名模型測試擋執行時的寫死 | 採用 | 桌面 | — | `eslint:model-agnostic`、`test:desktop/test/model-agnostic.test.js` |
