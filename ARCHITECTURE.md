# 架構

Apple Silicon Mac 上的語音助手：麥克風 → 系統語音辨識（macos-stt）→ 路由 → 執行 macOS 動作。語音辨識、Fast Path、本機 LLM 都在本機；雲端模型是選用的加分（[0009](docs/decisions/0009-cloud-optional.md)）。

流程細節：[docs/flows/main-flow.md](docs/flows/main-flow.md)、[docs/flows/slow-path.md](docs/flows/slow-path.md)。

## 延遲目標

| 路徑 | 目標延遲 | 備註 |
|---|---|---|
| Fast Path（規則比對→執行） | 數 ms～約 100 ms（含 `mdfind` 的找檔案約 80–130 ms） | 不需要 LLM，LLM 掛掉仍可用 |
| 本機找不到→轉交 LLM | 約 1.1 秒 | 靠 prefix cache；沒命中快取會變 3 秒 |
| Slow Path（LLM 規劃工具呼叫） | 離線預算 `PLAN_TIMEOUT_S`＝10 秒；live 測試要求單次規劃 < 5 秒 | 本機 4bit 小模型，會出錯 |
| Slow Path 雲端 | `CLOUD_TIMEOUT_S`＝8 秒；2026-11-16 實測 130 句 p50 0.93 s、p95 2.1 s | 同一步失敗退回本機；熔斷 30 秒 |
| 端點偵測（說完到開始處理） | 完整指令靜音 0.35 秒就收句 | [0003](docs/decisions/0003-client-endpoint-detection.md) |

## 行程

```
main.py ─┬─ mlx_vlm.server（本機 LLM，OpenAI 相容，APC_ENABLED=1）
         ├─ macos-stt（獨立 repo，.app bundle 內 start.py serve，WS :8765）
         ├─ ui_bus（127.0.0.1:8766，token）── Electron 桌面角色（desktop/）
         └─ 收音迴圈（STT 執行緒）→ 指令佇列 → command_worker（單執行緒依序）
                                                   └─（選用）雲端 LiteLLM proxy（https，.env）
```

## 模組職責

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
| `desktop/` | 桌面角色（Electron）。依行程與層次分目錄，規則與依賴方向見下方「桌面前端分層」 |
| `mac_ai/stt/` | `mic`、`client`（WS＋重連）、`vad`（Silero ONNX）、`endpoint`（純邏輯，時間由外部注入）、`completeness`（句子完整度）、`latency_log` |
| `vocab/lexicon.json` | **詞庫單一來源**：App、別名、誤聽、動詞、資料夾、檔案類型、喚醒詞 |
| `archive/` | 舊版單檔實作，**唯讀參考，不要改、不要 import** |

## 桌面前端（`desktop/`）分層

決策見 [0014](docs/decisions/0014-desktop-layers.md)。

| 目錄 | 層 | 內容 | 可以 import |
|---|---|---|---|
| `shared/` | 資料 | 與後端的合約（訊息型別、上限、expand 原因）；後端是來源 | 無 |
| `rig/` | 資料 | 這個模型的事實：語意名稱→動作／表情／手勢名稱、參數 ID、版面（`fullrig03.js`）；`check.js` 的 `checkRig` 驗證 profile 涵蓋 domain 的每個語意名稱、必要項目在模型裡；換模型＝新增一份 | 無 |
| `domain/` | 純邏輯 | 角色狀態機（只說語意名稱）、注意力導演 `attention.js`（情境→注視計畫，亂數注入）、聊天室紀錄、幀率閘門；時間注入，不碰 DOM／PIXI／Node／Electron | `shared/` |
| `renderer/engine/` | 渲染 | PIXI／pixi-live2d-display 的修補（`compat/`）、物理時鐘、頂點內插 | `rig/` |
| `renderer/animation/` | 動畫 | 程序層（擬人化）：`mixer`（彈簧、角度上限）、`ambient`（頭身曲線、掃視、漂移）、`face`（眨眼、眼皮與眉毛事件）、`procedural`（依參數所有權寫參數陣列）；純邏輯、亂數與時間注入，不 import `domain/`（導演的計畫由 `app.js` 轉交） | `engine/`、`rig/` |
| `renderer/role/`、`renderer/chat/` | 畫面 | DOM 與事件接線，唯一能碰 `document` 的層 | 以上全部 |
| `main/` | main 行程 | 視窗、匯流排連線（唯一持有 token）、IPC、`ui.jsonl`、除錯鉤子 | `domain/`、`shared/` |
| `preload/` | sandbox | 只做 contextBridge；sandbox 不能共用常數檔，頻道名稱靠測試比對 | 無 |
| `dev/` | 量測 | 軌跡指標，正式執行不載入 | `domain/`、`shared/`、`rig/` |

- 依賴只能往下。`domain/` 與 `engine/` 互不認識，只透過 effect 物件（資料）和 rig（資料）溝通。規則由 `desktop/test/layers.test.js` 掃原始碼檢查，**新檔先決定屬於哪一層**，放在規則以外的位置會直接失敗。
- 畫面行程能讀到的檔案只有 `main/app-protocol.js` 的 `appRoutes` 列的（`renderer/`、`domain/`、`shared/`、`rig/`、`vendor/`、`/model/` 與兩個函式庫單檔）；新增目錄給畫面用時，要加進白名單並在 `protocol.test.js` 補案例，`main/`、`preload/`、`dev/` 不得加。
- 主程序不拼 DOM 選擇器：除錯鉤子讀畫面的 `window.__ui.probe()`／`window.__chat.probe()`。
- 型別檢查 `npm run typecheck`（JSDoc＋`checkJs`，不開 `strict`，不允許新增 `@ts-ignore`）。
