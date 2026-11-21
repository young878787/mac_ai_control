# 主流程：輸入 → 路由 → 執行 → 回報

從三種輸入到執行結果回到桌面角色。Slow Path（LLM 規劃）的細節見 [slow-path.md](slow-path.md)。

```mermaid
flowchart TD
    Mic[麥克風 MicSource] --> Stt[SttClient<br/>送音訊到 macos-stt WS :8765]
    Stt --> Ep[EndpointDetector<br/>Silero VAD＋completeness.classify]
    Ep -- 說完了 → flush --> Stt
    Stt -- final --> Router[CommandRouter → WakeGate]
    UI[桌面角色 ui_bus :8766] -- command／choose --> Q
    Router -- command --> Q[(指令佇列)]
    CLI[main.py --text] --> PVC
    Q --> W[command_worker<br/>單執行緒依序]
    W -- str --> PVC[process_voice_command]
    W -- dict --> Choice[process_choice → pick_choice]
    PVC --> Norm[normalize_utterance] --> Split[split_clauses] --> Match{逐子句<br/>FastPathMatcher.match}
    Match -- 命中 --> Fast[fast 步驟 → Match.run → run_tool]
    Match -- 未命中 --> Slow[slow 段 → execute_slow_path_llm]
    Fast -- find_file 0 筆且沒有本機證據 --> Esc[轉交：瀏覽器在前景 → context_search<br/>否則本機 LLM local_miss]
    Fast & Slow & Esc & Choice --> Run[actions.run_tool → _dispatch]
    Run --> Res[result 事件＋commands.jsonl]
    Res --> Role[角色視窗＋泡泡]
    Res --> Chat[聊天室視窗<br/>有 expand 時 showInactive]
```

## A. 輸入來源

三種輸入，最後都匯進同一個佇列（`--text` 除外）。

- **麥克風** `stt/mic.MicSource`（PCM）
  - `stt/client.SttClient` 把音訊送到 macos-stt（WS :8765），同時逐窗跑 Silero VAD，交給 `endpoint.EndpointDetector`。
  - `EndpointDetector` 是純邏輯，時間由外部注入。它結合 VAD 的 onset／offset 遲滯，以及 partial 文字經 `completeness.classify` 的結果：
    - `classify`：去喚醒詞 → normalize → split_clauses → 每個子句都被 `FastPathMatcher.match` 命中，就算 complete。
    - 收句門檻：complete 靜音 0.35 s；incomplete（句尾接續詞、光動詞）多等；其餘用 default。另有 `asr_stall`、`max_len` 保底。
    - 決定「說完了」時，先送剩下的音訊，再送 `{"type":"flush"}`。一直沒有文字的語音段當噪音放棄。
  - server 事件：partial 只更新偵測器；final 會編上 `utt_id`，計時寫進 `logs/runtime/latency.jsonl`。
  - `assistant.CommandRouter`（STT 執行緒）：
    - partial → 終端顯示；`--ui` 時 publish `{"partial", wake}`。
    - utterance／final → paused 時丟棄，否則交給 `WakeGate.feed`（加鎖）：
      - command（去掉句首喚醒詞，或在會話內）→ `SpokenCommand(utt_id, enqueued_at)` 放進佇列。
      - armed（只說喚醒詞）→ 開 60 s 會話；ignored → 不執行。
- **桌面角色**（`--ui`）：`ui_bus`（127.0.0.1:8766、需要 token、不收帶 Origin 的連線）→ `parse_ui_message` 驗證 → `CommandRouter.on_ui_message`。
  - command → 佇列（不經 WakeGate、不續會話）；choose → 佇列（dict）；wake／pause／quit 只改狀態。
- **CLI**：`main.py --text` 直接呼叫 `process_voice_command`，不起服務、不經佇列與喚醒詞。

## B. command_worker

單執行緒、依序執行；單一指令出例外只記錯，不停下。

- str → `pipeline.process_voice_command`
- dict → `pipeline.process_choice` → `run_tool("pick_choice")`（和語音說「第 N 個」走同一個工具）
- 輸出：
  - `result_event`：steps 帶 route／ms；有 expand 才帶這個欄位（見 C.6）→ publish 給 UI。
  - `command_record`：家目錄換成 `~`、不含 choices → `logs/runtime/commands.jsonl`（帶 `utt_id`、`queue_ms`、`expand`）。

## C. process_voice_command

0. `catalog.refresh_if_stale()`：已安裝 App 清單超過 60 s 才重掃，在指令執行緒上做。
1. `normalize_utterance`：NFKC＋空白 → 簡轉繁（詞庫 s2t）→ 誤聽詞（記下 misheard，供還原原話）→ 中文數字（只在數值語境）→ 疊詞。接著 `log_normalization`，只記詞庫造成的改動。
2. `split_clauses`：依標點與「然後／接著／再幫我／順便／還有／並且／以及」切，不以空白切。
3. 逐子句跑 `FastPathMatcher.match`。這是純比對：不執行、不讀前景、不碰網路或 LLM。順序：
   - 「讓電腦睡眠」固定句 → `canonicalize_clause`（去填充詞、「把 X 打開」改成「打開 X」）
   - → `_r_negated_command`（窄的否定即指令）→ `_blocked`（否定／敘述，`_narrative_exempt` 例外）→ None
   - → `_read_screen_re` → `_r_open_search` → `_r_search`（這三項在問句守門之前）→ `_QUESTION_RE` → None
   - → `_r_front` → `_r_tab` → `_r_project` → `_r_volume` → `_r_mute` → `_r_brightness` → `_r_dark` → `_r_lock`
   - → `_r_find` → `_r_pick` → `_r_site` → `_r_app` → `_r_front_degree`（第一個命中的規則勝出）
   - 命中 → `("fast", [子句], Match)`，各自成一步；未命中 → 併進前一個 slow 段（連續的才合併），否則新開 slow 段。
4. 建立 `ExecContext`（整句共用、不跨語句），依說話順序跑每一段：
   - **fast** → `Match.run` → `run_tool`（逐個動作）
     - `find_file` 0 筆且不是 `local_only` → `ctx.escalate` → `_escalate_to_llm`（查詢用原話＋`restore_misheard`）
       - 前景是瀏覽器 → `context_search`（不問模型）
       - 否則 → `execute_slow_path_llm(local_miss=…)`（只走本機 LLM）；LLMError → `local_miss_reply`
   - **slow** → `execute_slow_path_llm`（送原句，不是 canonicalize 後的）
     - LLMError → `_recover_slow_segment`：`match(candidates=True)` 有候選才執行，否則回「AI 無法使用」
   - `step["status"] = _step_status`：`ctx.status` 優先，再看結果字串前綴（❌⚠️ → exec_failed、⏭️ → skipped）。
5. `memory.STORE.add`（原句、工具與成敗、回覆）→ 回傳 `{stages, steps, replies, total_ms}`，有 expand 才帶。
6. `expand_reason`（聊天室主動展開，只看這一句的 ctx）：`read_screen` 成功 → screen；`ctx.ai_reply`（本機 chat call 或被採用的雲端文字）→ chat；動作數（`ctx.calls` 不含 `chat_reply`）≥ `EXPAND_MIN_ACTIONS`，或回覆總字數 > `EXPAND_REPLY_CHARS` → complex；都不是就沒有 expand。

## E. 執行

`actions.run_tool` → `_dispatch`（記 `exec_ms`、`ctx.results`、`ctx.calls`）

- `control_mac_app` 開瀏覽器或編輯器 → 記下 `last_browser`／`last_editor`，成功時重置前景快取；失敗 → `ctx.failed`。
- `web_search`／`browser_tab`／`open_project`／`read_screen`：前置失敗就 ⏭️ 略過；對象依序是明說 → 同句剛開 → 前景 → 預設。
- `LOCAL_TOOLS`（`open_folder`、`find_file`、`pick_choice`、`context_search`、`front_action`）→ `local_actions`。
- `front_action` → `native.py`（視窗、選單、按鈕、App 指令；最後才送按鍵，送前重讀前景 pid）。
- `read_screen` → `screencapture`；雲端路線且 `CLOUD_VISION_ENABLED` 時，看圖走雲端。

結果 → result 事件 →（`--ui`）`ui_bus` WebSocket :8766 → `desktop/main/`（`bus.js` 是唯一持有連線與 token 的地方）：

- 角色視窗：Electron＋Live2D＋泡泡；有 expand 時，泡泡只顯示「詳細內容在對話視窗」。
- `chat.js`：紀錄只放記憶體，重開清空 → 聊天室視窗。這是獨立視窗，平常隱藏；隱藏時不推紀錄，展開前才補送。有 expand 時 `showInactive` 展開，無互動 20 秒收回。

## 量測鏈

語音指令的 latency 與 commands 紀錄以 `utt_id` 對應：`SttClient` 編號 → `SpokenCommand` 帶進佇列 → `command_worker` 寫入。改這條鏈時要保持。
