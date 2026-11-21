# 測試與驗證

測試分層、指令、基線與規範。改程式先跑 `pytest -m unit`，再依影響範圍擴大。

## 指令與基線

```bash
source .venv/bin/activate                 # Python 3.12
pytest                                    # 離線測試，預設，約 38 秒
pytest -m unit                            # 只跑單元層，約 1.4 秒（改程式先跑這個；另有 -m flow／-m runtime）
pytest -m live -s                         # 需 mlx_vlm.server 已載入模型（約 4 分鐘）
pytest -m cloud -s                        # 連真的雲端（需 .env 金鑰、約 200 次請求會產生費用，約 3 分鐘）
pytest -m app -v                          # 真實 App 驗收（Safari／Finder／Terminal／VS Code，佔用螢幕約 2 分鐘）
python scripts/acceptance.py run [--app] [--live] [--cloud] && python scripts/acceptance.py report   # 存數字並和上次比，變差結束碼 1
cd desktop && node --test                 # 桌面角色狀態機／路徑白名單／分層規則／IPC 頻道
cd desktop && npm run typecheck           # 桌面前端型別檢查（JSDoc＋checkJs）
python main.py --text 打開小算盤           # 不啟動服務、不檢查喚醒詞，直接跑一句（改路由後的最快實測方式）
python main.py [--no-stt] [--ui] [--device N]
python build_vocab.py                     # 改 vocab/lexicon.json 後重產 stt_hints／userdict（main.py 啟動時也會自動跑）
python scripts/ui_demo.py [--hold|--choices-test|--chat-test [N]]   # 不用麥克風預覽桌面角色；--chat-test 驗聊天室（會短暫出現視窗）
python scripts/ui_motion.py [--skip-idle|--skip-listen]   # 桌面角色動作軌跡指標（擬人化 R2 閒置 10 分鐘／R3 聆聽 10 輪；量測期間不要動滑鼠）
```

基線：`pytest` 必須 **0 failed**（測試數會隨功能增加，不要硬記數字；2026-11-17 整理測試分層後實測 1657 項通過、5 skipped，deselect 的 live 92／cloud 8／app 19）；`node --test` 0 fail（2026-11-20 前端分層整理 S0～S4 後實測 142 pass；2026-11-21 擬人化 P1～P4 後 266 pass，其中 `motion-sim.test.js` 是 T2／T3 模擬指標；pytest 1730 通過、5 skipped）。新增行為要新增測試。

## 測試規範

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
