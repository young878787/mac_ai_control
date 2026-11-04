# macOS 本地語音助手：雙軌執行架構與 LLM Function Calling 設計指南

本文件針對 **macOS (Apple Silicon M系列晶片)** 環境，設計一套結合 **「毫秒級規則匹配（Fast Path）」** 與 **「本地 LLM 多工具調用（Slow Path）」** 的雙軌語音控制核心。當語音經由 macOS 語音辨識（如原生 `SFSpeechRecognizer`、`mlx-whisper` 或 `Sherpa-ONNX`）轉為繁體中文文字後，系統會依此架構進行拆解與執行。

Slow Path 使用 **LiquidAI `LFM3-VL-2B` 4bit MLX 量化版**（8bit 版僅作比較），以 **`mlx-vlm`** 的 OpenAI 相容 server 啟動（規劃與對話為純文字；『讀畫面』會把縮圖送給同一個模型）。

> **驗證狀態標記**：本文件的程式碼與數據已於 2026-11-02、2026-11-03 在 Apple M1 / 16GB / macOS（Darwin 27.0.0）、Python 3.12.15、`mlx-vlm 0.7.11`、`mlx 0.32.4` 實際執行。2026-11-03 新增的多段意圖、Fast Path 強化、LLM 保護與讀畫面，實測結果見第八章 I。凡未實測的項目，在「八、驗證計畫」與「十、已知限制」明確列出。

---

## 一、 系統整體資料流架構

當 STT 輸出一段繁體中文語音辨識文字（例如：*「幫我打開終端機，然後把音量調到五十，順便查一下台北天氣」*）時，處理流程如下：

```text
[macOS 語音辨識 (STT) 輸出繁體中文]
          │
          ▼
[0. 詞庫正規化 (normalize_utterance)]
   NFKC（全形→半形）→ 空白收斂 → 簡轉繁 → 誤聽詞（科索→Cursor、因量→音量）→ 中文數字（五十→50，「一點」不轉）→ 疊詞
          │
          ▼
[1. 文本子句切分器 (Clause Splitter)]
   利用標點符號與中文連接詞（然後、接著、順便、還有）將長句拆分為獨立子句陣列
          │
          ▼
[2. 雙軌路由器 (Dual-Track Router)] ──(依說話順序逐一檢查子句，並依同一順序執行；同一句話的步驟共用 ExecContext)
          ├──► Fast Path 前置過濾 ──(命中)──► 【Fast Path 快速軌道】 (毫秒級)
          │      canonicalize_clause：去填充詞／語尾助詞、改寫語序（「Chrome 打開」→「打開 Chrome」）
          │      → 安全守門：否定、敘述、提問一律不走 Fast
          │      → 意圖規則：App 開關、音量、靜音、亮度、深色模式開／關、鎖定螢幕、搜尋、開 X 搜尋 Y、開專案、讀畫面
          │      → 槽位解析：完全比對 → 拉丁字母容錯比對（歧義不走 Fast）
          │      └─ 呼叫 macOS `open -a` 或 `osascript`，檢查結束碼，失敗如實回報
          │
          └──► 未命中 ──► 【Slow Path AI 軌道】 (單工具約 0.7 s、多工具 1.6 ~ 2.2 s，需開 prefix cache)
                          ├─ 連續的未命中子句合併成一段，送入本地 mlx_vlm.server (LFM3-VL-2B 4bit，8 個工具)
                          ├─ validate_tool_call 驗證工具名稱／參數（不合法一律不執行）
                          ├─ 依序映射至 Python 控制函數執行；沒有工具可呼叫或呼叫 chat_reply → 觸發 chat call
                          │     （原句直接送入模型、不帶 tools，回覆記錄在 replies）
                          └─ LLM 連不上／逾時／格式錯誤 → 熔斷 30 s；改執行 Fast 候選或告知使用者；後面的步驟照常執行
```

---

## 二、 文本前處理與子句切分設計

使用者常在一句話中下達多個指令。為了讓 **Fast Path** 能攔截其中簡單的指令（或決定是否將複雜句交給 LLM），需先做輕量級的子句切分：

* **切分規則**：利用正則表達式針對標點符號（`，`、`。`、`、`、`；`）以及口語連接詞（`然後`、`接著`、`再幫我`、`順便`、`還有`、`並且`、`以及`）進行切割。
  * **不以空白切分**：「VS Code」「調到 40」本身含空白，以空白切會把指令拆壞（實測會讓 `開 VS Code` 無法命中 Fast Path）。
* **依說話順序執行**：子句切完後依序分段——命中 Fast Path 的子句各自一段；**連續**未命中的子句合併成一段送 LLM（一次呼叫）。各段依說話順序執行，被 Fast Path 子句隔開的未命中子句會分成多次 LLM 呼叫（例如「調暗螢幕，靜音，查天氣」＝LLM → Fast → LLM）。取捨：順序正確，但隔開時 LLM 呼叫次數變多。
* **Fast 專用的子句整理（`canonicalize_clause`）**：只給 Fast Path 比對用，送進 LLM 的仍是正規化後的原句（填充詞與否定語氣要留給 LLM 判斷）。疊用的前綴（`嗯`、`請`、`幫我`、`麻煩`…）與語尾助詞（`吧`、`好嗎`、`一下`…）會被去掉，`把 X 打開`、`X 打開` 會改寫成 `打開 X`。
* **為什麼不在送給 LLM 前做「斷詞（Word Segmentation）」？**
  * **Fast Path（規則軌道）**：使用 Regex 抓出 `[動作]` 與 `[App 名稱]`；若要用 `Jieba`，載入由詞庫產生的 `vocab/userdict.txt`（見第六章）。
  * **Slow Path（AI 軌道）**：**絕對不要**把斷好詞的陣列塞給 LLM，直接給予自然語言句子，LLM 內建的 Tokenizer 與 Attention 機制才能正確理解上下文。

---

## 三、 雙軌執行核心設計

### 1. 快速軌道（Fast Path）
針對日常高頻指令，完全繞過 AI 模型，達到「話音剛落、程式秒開」的體驗。實測正規化＋切句＋比對（Fast 的決策時間）p95 約 0.1 ms（見第八章 I）；加上實際執行，每個 `osascript` 約 180 ~ 200 ms、`open -a` 約 80 ~ 110 ms（程序啟動成本，見 H2）。

* **規則涵蓋**：App 開關、音量（數字／最大／最小／一半）、靜音與取消靜音、亮度（一點＝3 格、最亮／最暗＝16 格、`N 格`）、深色模式（開／關／切換）、鎖定螢幕、搜尋、`開 X 搜尋 Y`（開瀏覽器後用同一個瀏覽器搜尋）、`開 X 專案`、讀畫面（`辨識這頁`、`這頁在寫什麼`）。睡眠只接受固定句 `讓電腦睡眠`（誤觸成本高，不放寬）。
* **比對寬鬆但正確**：
  * 去填充詞與語尾、改寫語序、中文數字與簡體已在正規化處理。
  * **安全守門**：含否定（`不要`、`別`、`不用`…）、敘述（`昨天`、`剛剛`、`已經…了`…）、提問（`嗎`、`什麼`、`怎麼`、`如何`…）的子句一律不走 Fast（`好嗎` 這類客氣語尾會先被當成語尾去掉，所以「那個打開 Chrome 好嗎」仍會執行）。讀畫面規則例外，因為「這頁在寫什麼」本身就是問句。
  * **容錯比對**：只對拉丁字母名稱（App、專案）使用 `difflib` 相似度，門檻 ≥ 0.80、長度 ≥ 5（`chorme`→Chrome 0.83、`spotfy`→Spotify 0.92；`chr`、`chro` 不命中）；中文名稱不做模糊，只查別名與誤聽表。第 1、2 名分數差 < 0.05 視為歧義，不走 Fast。
* **別名映射表（Alias Mapping）**：台灣使用者常混用中英文（如講「開 Chrome」或「開谷哥瀏覽器」、「開 VS Code」、「開 google」），別名與誤聽詞統一維護於 `vocab/lexicon.json`，載入後自動產生 `MAC_APP_ALIASES`、`APP_MISHEARD`、`TERM_MISHEARD`。`google` 是 Google Chrome 的別名；「google 一下 X」「用 google 查 X」則是搜尋。
* **macOS 底層執行方式**：
  * 開啟應用程式：`subprocess.run(["open", "-a", "Google Chrome"])`
  * 系統控制：透過 `osascript -e` 執行單行 AppleScript。
  * **結果如實回報**：檢查結束碼，非 0 就回傳 `❌` 與 stderr 摘要（舊版一律回報 ✅，`open -a` 找不到 App 時也是）。

### 2. AI 軌道（Slow Path）
當子句無法被 Fast Path 攔截（例如：「*螢幕太亮了*」、「*Python 是什麼*」、「*開 Slack*」這類 App 不在詞庫或沒有明確動詞的說法），系統將句子送至本機 **`mlx_vlm.server`**（模型：`LiquidAI/LFM3-VL-2B-MLX-4bit`，約 1.5 GB）。

* **實測延遲**（M1 / 16GB，暖機後，皆開 prefix cache `APC_ENABLED=1`）：

  | 情境 | 4bit（採用） | 8bit |
  |---|---|---|
  | 單工具（C14，n=10） | 中位數 **0.66 s**、最大 0.72 s | 中位數 0.87 s、最大 0.92 s |
  | 額外 20 句語句（第八章 G 組，單次） | 中位數 1.04 s | 中位數 1.35 s |
  | 雙工具／三工具（穩定後） | 約 1.6 s／2.2 s | 約 2.2 s／3.0 s |

  * 未開 prefix cache（8bit 實測）：單工具約 2.1 ~ 2.5 s，雙工具約 3.5 s，三工具約 4.4 s。
  * **延遲來源**（8bit、未開 APC 的拆解）：單工具請求 prompt 約 620 tokens（5 個工具定義＋system prompt），prefill 約 **1.5 s**（約 420 tok/s）；輸出約 20 tokens，decode 約 30 tok/s ≈ 0.65 s。工具定義每次請求都相同，開 prefix cache 後 prefill 降到 0.04 ~ 0.3 s。8bit decode 30 tok/s 接近 M1 記憶體頻寬上限（約 68 GB/s ÷ 2.1 GB 權重，為推論值）；4bit 權重較小所以 decode 較快（4bit 的 decode 速率未單獨量測）。
  * 多工具變慢是因為輸出 token 變多（每多一個呼叫約 +0.5 ~ 0.7 s decode），prefix cache 無法減少。
  * 部分案例**第一次**出現時較慢（約 2 s，之後約 0.7 s），原因未查明（可能與 prefix cache 首次寫入有關，未驗證）。
  * 工具增加到 8 個後，prompt 約 **890 tokens**（`cache_n` 890，實測）；單工具延遲不變（p50 0.66 s）。暖機現在帶上與正式請求相同的 system prompt，否則第一個真實請求的 `cache_n` 只有 99、prefill 約 2 s。
  * 附帶詞庫提示（含專案清單）的請求，system prompt 的前綴與一般請求不同，第一次會較慢（實測約 3 s，prefill 2 s）。
* **必須關閉 thinking**：LFM3-VL 的 chat template 預設 `enable_thinking=true`（會先輸出 `<think>…</think>`）。每個請求都明確帶 `"enable_thinking": false`。
* **官方建議取樣參數**：`temperature=0.1, top_p=1.0, top_k=50, min_p=0.0, repetition_penalty=1.1`。
* 模型卡聲明「不建議用於 coding、多輪對話、複雜推理」；本設計每次只送單輪、單句、固定 8 個工具，符合其適用範圍。
* **LLM 保護**：所有 LLM 呼叫經 `_llm()`——連線被拒／逾時（規劃與對話 10 s、讀畫面 30 s）轉為 `LLMUnavailable` 並啟動 30 s 熔斷；HTTP 錯誤或格式錯誤轉為 `LLMError`（不熔斷）。失敗時該段改執行 Fast 候選（歧義被擋下的第 1 名，且未被安全守門擋下）或告知使用者，**後面的步驟照常執行**。`warmup_llm()` 失敗只印警告。

---

## 四、 LLM Function Calling (Tools Schema) 設計

工具定義的**唯一來源**是第七章 `mac_voice_router.py` 內的 `TOOLS_SCHEMA`（OpenAI `tools` 格式，支援一次回傳多個工具調用）。摘要如下：

| 工具 | 參數 | 說明 |
|---|---|---|
| `control_mac_app` | `app_name`(string, 必填)、`action`(`open`/`quit`, 必填) | 開啟或關閉 App，`app_name` 須為標準英文名稱 |
| `set_system_volume` | `level`(integer 0-100)、`mute`(boolean) | 設定音量或靜音，皆為選填 |
| `adjust_screen_brightness` | `direction`(`up`/`down`)、`steps`(integer 1-16)，皆必填 | 調整亮度，「一點」約 3 格、「最亮／最暗」16 格 |
| `web_search_or_open_url` | `query`(string, 必填) | 以預設瀏覽器 Google 搜尋或開啟網址 |
| `system_power_action` | `action`(`dark_mode_on`/`dark_mode_off`/`lock_screen`/`sleep`/`toggle_dark_mode`, 必填) | 系統狀態控制；『打開深色模式』必須是 `dark_mode_on`（toggle 只在明說『切換』時用） |
| `open_project` | `project_name`(string, 必填)、`editor`(string, 選填) | 用編輯器開啟已知專案；名稱只能從白名單（詞庫 `projects` ＋ `project_roots` 第一層資料夾）解析，任意路徑一律拒絕 |
| `read_screen` | 無 | 截主螢幕、縮到 1280 寬後交給 VL 模型說明內容（回覆寫入 `replies`） |
| `chat_reply` | 無 | 使用者在提問或閒聊時呼叫，觸發 chat call（不帶 tools） |

**LFM3-VL 的 Function Calling 機制（已驗證）**：

* `tools` 以 OpenAI 格式傳入，chat template 自動渲染進 system prompt；模型輸出 `qwen3_coder` 系 XML（`<tool_call><function=…><parameter=…>`），`mlx_vlm.server` 會依 chat template 自動選用 `qwen3_coder` parser，回傳標準 OpenAI `tool_calls`。可由 `GET /health` 的 `loaded_tool_parser` 確認。
* 回傳的 `function.arguments` 是 **JSON 字串**，必須 `json.loads` 後再用；程式碼已處理字串與物件兩種形態。
* `read_screen`、`chat_reply` 不帶參數，減少 2B 模型要填的欄位；若模型多塞參數仍會被驗證層拒絕。
* 模型會偶爾多塞參數（實測：`mute=true` 時多帶 `level=30`；極簡 prompt 下 `level=50` 旁多帶 `mute=false`），所以**一律先過 `validate_tool_call`** 才執行，詳見第七、八章。

---

## 五、 環境建置與啟動（uv + Python 3.12 + mlx-vlm）

專案目錄結構（皆相對於 `/Users/owo/projects/mac_ai_control`）：

```text
mac_ai_control/
├─ .venv/                          # uv 建立的 Python 3.12 虛擬環境
├─ AI_models/
│  └─ LFM3-VL-2B-MLX-4bit/         # 模型放在專案目錄內（約 1.5 GB）；8bit 版（2.1 GB）可並存供比較
├─ vocab/
│  ├─ lexicon.json                 # 預先詞庫（手動維護的唯一來源）
│  ├─ stt_hints.txt                # 產生物：給 STT contextualStrings
│  └─ userdict.txt                 # 產生物：給 Jieba
├─ build_vocab.py
├─ mac_voice_router.py
├─ pytest.ini
├─ logs/                           # 執行時產生：normalize.jsonl（詞庫替換過的『原文 → 結果』，內含你說過的話；不需版控）
└─ tests/
   ├─ conftest.py                  # 共用 fixture（sh、projects、LLM 預設離線）
   ├─ test_router_offline.py       # 離線測試（不需模型）
   ├─ test_normalize_offline.py    # 正規化
   ├─ test_fast_offline.py         # Fast Path 正例／反例
   ├─ test_multistep_offline.py    # 執行回報、LLM 保護、多段意圖、讀畫面、計時、chat call
   ├─ test_lexicon_offline.py      # 詞庫與 build_vocab
   └─ test_llm_live.py             # 實機測試（需 server）
```

### 1. 建立虛擬環境與安裝套件
```bash
cd /Users/owo/projects/mac_ai_control
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python mlx-vlm jieba pytest
```
* `mlx-vlm` 會一併安裝 `mlx`、`transformers`、`huggingface_hub`。LLM 呼叫只用標準函式庫 `urllib`，不需另裝 `openai` 或 `ollama`。
* `jieba`、`pytest` 僅詞庫測試與驗證使用。

### 2. 下載模型到 `AI_models/`
```bash
.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('LiquidAI/LFM3-VL-2B-MLX-4bit', local_dir='AI_models/LFM3-VL-2B-MLX-4bit')"
```
* 選 `LFM3-VL-2B-MLX-4bit`（4-bit，約 1.5 GB）。同系列另有 5bit／6bit／8bit／bf16 repo；要比較 8bit，把上面指令的 `4bit` 全換成 `8bit`，並同步修改程式 `MODEL_ID` 與測試的 `loaded_model` 斷言。
* 官方 repo 已含 `configuration_*.py`、`modeling_*.py` 等自訂程式檔，但 `mlx-vlm 0.7.11` 以內建的 `lfm3_vl` 實作載入，實測不需 `--trust-remote-code`。

### 3. 啟動 `mlx_vlm.server`
```bash
APC_ENABLED=1 .venv/bin/python -m mlx_vlm.server \
  --model AI_models/LFM3-VL-2B-MLX-4bit \
  --host 127.0.0.1 --port 8080 --max-tokens 512
```
* `APC_ENABLED=1`：開啟 prefix cache（預設關閉）。實測使單工具延遲由約 2.2 s 降到約 1 s；`/health` 會顯示 `apc_enabled: true`。
* `--host 127.0.0.1`：預設是 `0.0.0.0`，語音助手只需本機存取，務必收斂。
* 實測啟動（模型載入完成）約 2 秒，server 端對外 API 為 `/health`、`/chat/completions` 等。
* 程式中的 `MODEL_ID`（`AI_models/LFM3-VL-2B-MLX-4bit`）須與 `--model` 字串一致。

### 4. 安裝測試提示詞（依序執行，驗收條件見第八章 A 組）

**① 健康檢查**
```bash
curl -s 127.0.0.1:8080/health
```
預期：`status` 為 `healthy`、`loaded_tool_parser` 為 `qwen3_coder`、`apc_enabled` 為 `true`。

**② 純文字生成（不經 server，驗證安裝）**
```bash
.venv/bin/python -m mlx_vlm.generate --model AI_models/LFM3-VL-2B-MLX-4bit \
  --prompt "請用一句繁體中文自我介紹。" --max-tokens 64 --temperature 0.1
```
實測輸出：`我是 LFM3-VL-1.9B，由 Liquid AI 打造，能夠理解與生成文字與图像。`（注意：模型回覆可能夾雜簡體字「图」，見第十章）。

**③ 純文字 chat（經 server）**
```bash
curl -s 127.0.0.1:8080/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "AI_models/LFM3-VL-2B-MLX-4bit",
  "messages": [{"role": "user", "content": "請用一句繁體中文自我介紹。"}],
  "temperature": 0.1, "max_tokens": 128, "enable_thinking": false}'
```
預期：`finish_reason` 為 `stop`，`content` 為非空中文且不含 `<think>`。

**④ 工具調用**
```bash
curl -s 127.0.0.1:8080/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "AI_models/LFM3-VL-2B-MLX-4bit",
  "messages": [{"role": "user", "content": "把音量調到五十"}],
  "tools": [{"type": "function", "function": {"name": "set_system_volume",
    "description": "調整系統音量(0-100)或靜音",
    "parameters": {"type": "object", "properties": {"level": {"type": "integer"}, "mute": {"type": "boolean"}}}}}],
  "temperature": 0.1, "max_tokens": 128, "enable_thinking": false}'
```
預期：`finish_reason` 為 `tool_calls`，`tool_calls[0].function.name` 為 `set_system_volume`，`arguments` 的 `level` 為 `50`。實測回傳 `{"level": 50, "mute": false}`（多出 `mute: false`，正是需要驗證層的原因）。

---

## 六、 預先詞庫建立

台灣使用者的語音指令容易出現「App 名稱被 STT 聽錯」或「口語別名」。詞庫以 **單一來源 `vocab/lexicon.json`** 維護，同時餵給下列地方：

| 用途 | 產物 | 使用位置 |
|---|---|---|
| Fast Path 別名表 | `MAC_APP_ALIASES` | `mac_voice_router.py` 啟動時載入 `lexicon.json` |
| 誤聽詞與簡繁正規化 | `APP_MISHEARD`、`TERM_MISHEARD`、`S2T`、`normalize_utterance()` | 進入子句切分前先替換為標準名稱；`s2t` 簡轉繁、`term_misheard` 指令詞誤聽（如 `因量`→`音量`） |
| 專案白名單 | `projects`、`project_roots`、`project_index()` | `open_project` 只能開這裡列的專案（`project_roots` 第一層資料夾自動納入） |
| 動詞與填充詞 | `verbs`、`fillers` | Fast Path 的動作詞、`canonicalize_clause` 去前綴／語尾 |
| LLM 詞庫提示 | `build_hint_lines()` | 只把語句中**實際出現**的別名以「別名→標準名稱」附在 system prompt，避免撐大 prompt |
| STT 辨識偏好 | `vocab/stt_hints.txt` | 載入 `SFSpeechRecognizer.contextualStrings`（本專案 `macos-stt` 的 `POST /vocabulary`） |
| Jieba 斷詞 | `vocab/userdict.txt` | `jieba.load_userdict()` |

**實測的詞庫效益**（同一模型、同一 system prompt，有／無詞庫提示）：

| 語句 | 無詞庫 | 有詞庫 |
|---|---|---|
| 打開科索 | ❌ `web_search_or_open_url("科索")` | ✅ `control_mac_app(Cursor, open)` |
| 開谷哥瀏覽器 | ❌ `web_search_or_open_url("開谷哥瀏覽器")` | ✅ `control_mac_app(Google Chrome, open)` |

目前程式採**雙重保險**：`normalize_utterance` 做確定性替換（Fast Path 與 LLM 都受益），`build_hint_lines` 再補 LLM 提示。

**維護原則**
* `misheard` 每筆帶 `source`：`observed`＝STT 紀錄中真的出現過（「科索」「谷哥」來自本文既有案例），`seed`＝預先推測、尚未被真實 STT 驗證（`VS扣`、`因量`…）。累積 `logs/normalize.jsonl` 後，把真的出現過的改標 `observed`、沒出現過的刪掉。舊的字串格式仍可載入（視為 `observed`）。
* `misheard`、`term_misheard`、`s2t` **不會**寫入 `stt_hints.txt`（不能把錯誤寫法餵給 STT 強化它）。
* 新增 App：在 `apps` 加一筆 `name`（macOS 實際 App 名稱）＋ `aliases`，再執行 `build_vocab.py` 重新產生 STT 詞表與 Jieba 詞典。
* 同一個詞不得同時屬於兩個 App、兩個專案或 App 與專案（離線測試 `test_lexicon_no_alias_collision`、`test_no_collision_across_apps_projects_and_terms` 會擋）；`google` 只屬於 Google Chrome。
* `kind`（`browser`／`editor`／`other`）決定 `開 X 搜尋 Y`、`用 X 開專案` 與執行上下文怎麼用這個 App。

**與 `macos-stt` 串接**（API 以該專案 `server.py` 原始碼為準，**本次未實際呼叫驗證**）：
```bash
python build_vocab.py
curl -X POST http://127.0.0.1:<macos-stt 埠號>/vocabulary -H 'Content-Type: application/json' \
  -d '{"mode":"load","file":"/Users/owo/projects/mac_ai_control/vocab/stt_hints.txt"}'
```
詞庫僅對**新建立**的 session 生效；`macos-stt` 建議詞庫上限 200 條（目前 47 條）。

### `vocab/lexicon.json`
```json
{
  "apps": [
    {"name": "Google Chrome", "kind": "browser", "aliases": ["chrome", "google chrome", "google", "谷歌", "谷歌瀏覽器", "瀏覽器"], "misheard": [{"text": "谷哥瀏覽器", "source": "observed"}, {"text": "谷哥", "source": "observed"}, {"text": "古歌瀏覽器", "source": "seed"}]},
    {"name": "Safari", "kind": "browser", "aliases": ["safari"], "misheard": []},
    {"name": "Visual Studio Code", "kind": "editor", "aliases": ["vscode", "vs code"], "misheard": [{"text": "VS扣", "source": "seed"}]},
    {"name": "Cursor", "kind": "editor", "aliases": ["cursor"], "misheard": [{"text": "科索", "source": "observed"}]},
    {"name": "Terminal", "kind": "other", "aliases": ["terminal", "終端機"], "misheard": []},
    {"name": "iTerm", "kind": "other", "aliases": ["iterm"], "misheard": []},
    {"name": "Finder", "kind": "other", "aliases": ["finder", "訪達"], "misheard": []},
    {"name": "Calculator", "kind": "other", "aliases": ["小算盤", "計算機"], "misheard": []},
    {"name": "Notes", "kind": "other", "aliases": ["備忘錄", "記事本"], "misheard": []},
    {"name": "System Settings", "kind": "other", "aliases": ["系統設定", "設定"], "misheard": []},
    {"name": "Music", "kind": "other", "aliases": ["音樂"], "misheard": []},
    {"name": "Spotify", "kind": "other", "aliases": ["spotify"], "misheard": []},
    {"name": "LINE", "kind": "other", "aliases": ["line"], "misheard": []},
    {"name": "Discord", "kind": "other", "aliases": ["discord"], "misheard": []}
  ],
  "projects": [
    {"name": "mac_ai_control", "aliases": ["語音助手"], "path": "/Users/owo/projects/mac_ai_control"}
  ],
  "project_roots": ["/Users/owo/projects"],
  "term_misheard": [
    {"text": "因量", "to": "音量", "source": "seed"},
    {"text": "英量", "to": "音量", "source": "seed"},
    {"text": "淨音", "to": "靜音", "source": "seed"},
    {"text": "競音", "to": "靜音", "source": "seed"},
    {"text": "營幕", "to": "螢幕", "source": "seed"},
    {"text": "中端機", "to": "終端機", "source": "seed"}
  ],
  "s2t": {
    "打开": "打開", "开启": "開啟", "开": "開", "关掉": "關掉", "关闭": "關閉", "关": "關",
    "启动": "啟動", "屏幕": "螢幕", "设置": "設定", "设定": "設定", "设": "設",
    "搜索": "搜尋", "查询": "查詢", "浏览器": "瀏覽器", "计算器": "小算盤", "计算机": "計算機",
    "备忘录": "備忘錄", "终端": "終端", "静音": "靜音", "静": "靜", "声音": "聲音", "调到": "調到",
    "调成": "調成", "调整": "調整", "调": "調", "锁定": "鎖定", "锁": "鎖", "页面": "頁面",
    "网页": "網頁", "页": "頁", "画面": "畫面", "识别": "識別", "辨识": "辨識", "项目": "專案",
    "专案": "專案", "夜间": "夜間", "应用": "應用", "软件": "軟體", "内容": "內容", "显示": "顯示",
    "请": "請", "帮": "幫", "给": "給", "这": "這", "个": "個", "么": "麼", "还": "還", "说": "說",
    "话": "話", "记": "記", "图": "圖", "语": "語", "写": "寫", "为": "為", "点": "點",
    "然后": "然後", "接着": "接著", "顺便": "順便", "并且": "並且",
    "气": "氣", "东": "東", "车": "車", "们": "們", "会": "會", "时": "時", "间": "間", "问": "問", "题": "題", "对": "對", "没": "沒", "样": "樣", "来": "來", "过": "過", "现": "現", "学": "學", "实": "實", "经": "經", "无": "無", "与": "與", "边": "邊", "种": "種", "体": "體", "电": "電", "脑": "腦", "机": "機", "视": "視", "频": "頻", "络": "絡", "输": "輸", "务": "務", "户": "戶", "资": "資", "档": "檔", "译": "譯", "网": "網", "览": "覽", "约": "約", "买": "買", "读": "讀", "侧": "側", "连": "連", "应": "應", "该": "該", "当": "當", "选": "選", "项": "項", "统": "統", "联": "聯", "单": "單", "击": "擊", "钮": "鈕", "显": "顯", "终": "終", "颜": "顏", "转": "轉", "换": "換"
  },
  "verbs": {
    "open": ["打開", "開啟", "啟動", "叫出", "開"],
    "quit": ["關掉", "關閉", "退出", "結束", "關"]
  },
  "fillers": {
    "prefix": ["嗯", "呃", "欸", "啊", "喂", "那個", "就是", "請", "幫我", "麻煩你", "麻煩", "給我", "可以", "能不能", "你", "幫忙", "我要", "我想要", "我想", "我需要", "來", "先"],
    "suffix": ["吧", "啦", "喔", "哦", "呢", "囉", "啊", "好嗎", "好不好", "可以嗎", "謝謝", "一下下", "一下", "看看", "就好", "就行"]
  },
  "terms": ["深色模式", "暗黑模式", "夜間模式", "靜音", "鎖定螢幕", "螢幕亮度"]
}
```

### `build_vocab.py`
```python
"""由 vocab/lexicon.json 產生 STT contextualStrings 詞表與 Jieba 自訂詞典。"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
LEXICON = ROOT / "vocab" / "lexicon.json"


def _text(entry) -> str:
    """misheard 可以是字串（舊格式）或 {"text", "source"}"""
    return entry["text"] if isinstance(entry, dict) else entry


def build(lexicon_path: Path = LEXICON, out_dir: Path = LEXICON.parent) -> None:
    lex = json.loads(lexicon_path.read_text(encoding="utf-8"))

    # STT 提示詞：App 與專案的標準名稱 + 別名 + 指令詞（不含 misheard／s2t，避免強化錯誤寫法）
    hints = []
    for app in lex["apps"]:
        hints += [app["name"], *app["aliases"]]
    for project in lex.get("projects", []):
        hints += [project["name"], *project.get("aliases", [])]
    hints += lex.get("terms", [])
    hints = list(dict.fromkeys(h.strip() for h in hints if h.strip()))
    (out_dir / "stt_hints.txt").write_text("\n".join(hints) + "\n", encoding="utf-8")

    # Jieba userdict：每行「詞 詞頻 詞性」，含空白的詞無法表示，略過
    words = list(hints)
    for app in lex["apps"]:
        words += [_text(m) for m in app.get("misheard", [])]
    words = [w for w in dict.fromkeys(words) if not any(c.isspace() for c in w)]
    lines = [f"{w} 100000 n" for w in words]
    (out_dir / "userdict.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
    print("已產生 vocab/stt_hints.txt、vocab/userdict.txt")
```

---

## 七、 完整可執行 Python 實作（macOS 專用）

核心程式 `mac_voice_router.py`。與舊版（Ollama）相比的變更：

* Slow Path 由 `ollama.chat` 改為呼叫 `mlx_vlm.server`（`urllib`，無新增相依）。
* 新增詞庫載入、`normalize_utterance`、`build_hint_lines`。
* 新增 `validate_tool_call`（驗證層）、`plan_slow_path`（規劃與執行分離，便於測試）、`warmup_llm`。
* **依說話順序執行並計時**：`FastPathMatcher.match()` 只比對、回傳待執行函式（`try_execute()` 保留）；`process_voice_command()` 依子句順序分段執行，回傳 `{"steps": [...], "total_ms": ...}`，每步含耗時，Slow 步另含 `llm_ms`。
* **修正 `split_clauses`**：移除以空白切分（會拆壞 `VS Code`、`調到 40`）。
* Fast Path 動作詞補上單字「開」（支援「開 Chrome」「開 VS Code」口語）。

2026-11-03 的多段意圖與 Fast Path 強化（詳見 `docs/多段意圖執行與 Fast Path 強化實作計劃書（定案版）.md`）：

* **正規化擴充**：`normalize_utterance` 依序處理 NFKC、空白、簡轉繁（`s2t`）、誤聽詞（App 與指令詞，標註 `observed`／`seed`）、中文數字（只在數值語境）、疊詞。詞庫 schema 新增 `kind`、`projects`、`project_roots`、`term_misheard`、`s2t`、`verbs`、`fillers`；`misheard` 可為字串（舊格式）或 `{"text","source"}`。
* **Fast Path 放寬**：`canonicalize_clause`、安全守門、容錯比對、更多規則（見第三章）。`match()` 回傳 `Match`（可呼叫；有 `actions`、`confidence`、`note`），`candidates=True` 時歧義的第 1 名也會回傳，供 LLM 失效時補救。
* **執行上下文 `ExecContext`**：只在同一句話內有效；記住剛開的編輯器／瀏覽器與剛開的網頁，前置步驟失敗時略過依賴它的步驟（`skipped`）。
* **新執行器**：`open_project`、`read_screen`（先等網頁載入再截圖）、`chat_reply`（chat call）；`web_search_or_open_url` 會用剛開的瀏覽器。
* **LLM 保護**：`_llm()`、熔斷、逾時、補救（見第三章）；畸形的 `tool_calls`、非物件的 `message` 都轉為拒絕或 `llm_error`，不會讓流程崩潰。
* **回傳結構**：`{"stages", "steps", "replies", "total_ms"}`；每步含 `status`（`ok／exec_failed／llm_unavailable／llm_error／fallback／rejected／skipped`）、`exec_ms`，slow 步另含 `llm_ms`、`llm`（server `timings`：prefill／decode／prompt_n／cache_n／overhead）、`validate_ms`、`chat_ms`，讀畫面含 `wait_ms／capture_ms／resize_ms／vl_ms`。
* **命令列**：`python mac_voice_router.py "句子"` 直接送入文字（模擬收到 STT 輸出）；不帶參數則維持原本的 demo。
* **正規化紀錄**：詞庫替換造成的改動（簡轉繁、誤聽詞、數字、疊詞）會附加寫入 `logs/normalize.jsonl`，用來回收真實誤聽（內含你說過的話，只存在本機）。

```python
import base64
import difflib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ==========================================
# 0. 預先詞庫（單一來源：vocab/lexicon.json）
# ==========================================
LEXICON_PATH = Path(__file__).parent / "vocab" / "lexicon.json"
NORMALIZE_LOG = Path(__file__).parent / "logs" / "normalize.jsonl"


def load_lexicon(path: Path = LEXICON_PATH) -> dict:
    """讀詞庫。misheard 可為字串（舊格式）或 {"text", "source"}；載入後一律攤平成字串，
    來源另存於 lex["misheard_sources"]（小寫詞 → observed／seed）"""
    lex = json.loads(path.read_text(encoding="utf-8"))
    sources: Dict[str, str] = {}
    for app in lex["apps"]:
        texts = []
        for entry in app.get("misheard", []):
            text = entry["text"] if isinstance(entry, dict) else entry
            source = entry.get("source", "observed") if isinstance(entry, dict) else "observed"
            sources[text.lower()] = source
            texts.append(text)
        app["misheard"] = texts
    for entry in lex.get("term_misheard", []):
        sources[entry["text"].lower()] = entry.get("source", "observed")
    lex["misheard_sources"] = sources
    return lex


LEXICON = load_lexicon()

# 別名 → 標準名稱（Fast Path 與 LLM 提示共用）；key 一律小寫
MAC_APP_ALIASES: Dict[str, str] = {}
# STT 常見誤聽 → 標準名稱（路由前先替換）；key 一律小寫
APP_MISHEARD: Dict[str, str] = {}
# App 標準名稱 → 類型（browser／editor／other）
APP_KIND: Dict[str, str] = {}
for _app in LEXICON["apps"]:
    MAC_APP_ALIASES[_app["name"].lower()] = _app["name"]
    APP_KIND[_app["name"]] = _app.get("kind", "other")
    for _a in _app.get("aliases", []):
        MAC_APP_ALIASES[_a.lower()] = _app["name"]
    for _m in _app.get("misheard", []):
        APP_MISHEARD[_m.lower()] = _app["name"]

# 指令詞誤聽（如「因量」→「音量」）；key 一律小寫
TERM_MISHEARD: Dict[str, str] = {e["text"].lower(): e["to"] for e in LEXICON.get("term_misheard", [])}
# 簡體 → 繁體（只收指令相關詞）
S2T: Dict[str, str] = LEXICON.get("s2t", {})
# 專案根目錄：第一層資料夾自動視為專案
PROJECT_ROOTS: List[str] = list(LEXICON.get("project_roots", []))

_VERBS = LEXICON.get("verbs", {})
OPEN_VERBS: List[str] = _VERBS.get("open", ["打開", "開啟", "啟動", "叫出", "開"])
QUIT_VERBS: List[str] = _VERBS.get("quit", ["關掉", "關閉", "退出", "結束", "關"])
_FILLERS = LEXICON.get("fillers", {})


def _alt(words: List[str]) -> str:
    """把詞串成 regex 交替式；長詞在前，避免短詞先吃掉（如『開』吃掉『開啟』）"""
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_OPEN = _alt(OPEN_VERBS)
_QUIT = _alt(QUIT_VERBS)
_VERB = _alt(OPEN_VERBS + QUIT_VERBS)
_LONG_VERB = _alt([v for v in OPEN_VERBS + QUIT_VERBS if len(v) >= 2])

_MISHEARD_RE = (
    re.compile(_alt(list({**APP_MISHEARD, **TERM_MISHEARD})), re.IGNORECASE)
    if (APP_MISHEARD or TERM_MISHEARD)
    else None
)
_S2T_RE = re.compile(_alt(list(S2T))) if S2T else None
_DUP_VERB_RE = re.compile(rf"({_LONG_VERB})\1+")

# ---------- 中文數字（只在數值語境轉換）----------
_ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ZH_NUM = r"[零〇一二兩三四五六七八九十百]+"
_NOT_COUNTER = r"(?![點下個些半])"  # 『一點』『一下』『一個』『一半』不是數字
_SET_VERBS = "調到|調成|調整到|調整為|設為|設定為|設成|設定成|開到|改成|改為"


def zh_to_int(s: str) -> Optional[int]:
    """『五十』→50、『一百零五』→105、『一百二』→120、『十五』→15；無法解析回傳 None"""
    total, num = 0, None
    for ch in s:
        if ch in _ZH_DIGITS:
            num = _ZH_DIGITS[ch]
        elif ch in ("十", "百"):
            total += (1 if num is None else num) * (10 if ch == "十" else 100)
            num = None
        else:
            return None
    if num is not None:
        if total >= 100 and total % 100 == 0 and "零" not in s and "〇" not in s:
            num *= 10  # 一百二 = 120
        total += num
    return total


def _num_text(m_text: str) -> str:
    n = zh_to_int(m_text)
    return str(n) if n is not None else m_text


def _convert_numerals(text: str) -> str:
    text = re.sub(rf"百分之({_ZH_NUM})", lambda m: f"{_num_text(m[1])}%", text)
    text = re.sub(rf"({_ZH_NUM})\s*(?:趴|%)", lambda m: f"{_num_text(m[1])}%", text)
    text = re.sub(rf"({_ZH_NUM})(?=\s*格)", lambda m: _num_text(m[1]), text)
    text = re.sub(rf"({_SET_VERBS})\s*({_ZH_NUM}){_NOT_COUNTER}", lambda m: m[1] + _num_text(m[2]), text)
    text = re.sub(rf"(音量|聲音|亮度)\s*({_ZH_NUM}){_NOT_COUNTER}", lambda m: m[1] + _num_text(m[2]), text)
    return text


def to_traditional(text: str) -> str:
    """簡轉繁（只涵蓋詞庫 s2t 表內的詞，無法保證全部轉換）"""
    return _S2T_RE.sub(lambda m: S2T[m.group(0)], text) if _S2T_RE else text


def _basic_normalize(text: str) -> str:
    """NFKC（全形→半形）、空白收斂、移除中文與中文之間的空白；中英之間的空白保留（VS Code）"""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"(?<=[一-鿿]) (?=[一-鿿])", "", t)


def normalize_utterance(text: str) -> str:
    """正規化 STT 文字：NFKC → 空白 → 簡轉繁 → 誤聽詞（App／指令詞）→ 中文數字 → 疊詞。
    『一點』『一下』不會被當成數字；中英之間的空白保留（VS Code）"""
    t = to_traditional(_basic_normalize(text))
    if _MISHEARD_RE is not None:
        lookup = {**APP_MISHEARD, **TERM_MISHEARD}
        t = _MISHEARD_RE.sub(lambda m: lookup[m.group(0).lower()], t)
    t = _convert_numerals(t)
    return _DUP_VERB_RE.sub(r"\1", t)


def log_normalization(original: str, normalized: str) -> None:
    """把『STT 原文 → 正規化結果』附加寫入 NORMALIZE_LOG，用來回收真實誤聽；寫入失敗不影響流程。
    只記錄詞庫替換造成的改動（簡轉繁、誤聽詞、中文數字、疊詞）；全形／空白這類機械性轉換沒有資訊量，不記"""
    if normalized == _basic_normalize(original):
        return
    try:
        NORMALIZE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with NORMALIZE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "raw": original, "normalized": normalized}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------- 名稱解析（App、專案共用）：完全比對 → 拉丁字母容錯比對 ----------
FUZZY_MIN = 0.80  # 相似度門檻
FUZZY_MARGIN = 0.05  # 第 1、2 名（不同對象）差距小於此值視為歧義
FUZZY_MIN_LEN = 5  # 太短的英文字（如 chr）不做容錯


def _compact(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s.lower())


def _fuzzy_pick(key: str, index: Dict[str, Any]) -> Optional[Tuple[Any, float, bool]]:
    """只比對拉丁字母／數字的名稱（中文差一字常是另一個詞，不做模糊）。回傳 (對象, 分數, 是否歧義)"""
    if not re.fullmatch(r"[a-z0-9.]+", key) or len(key) < FUZZY_MIN_LEN:
        return None
    best: Dict[Any, float] = {}
    for k, value in index.items():
        if not re.fullmatch(r"[a-z0-9.]+", k):
            continue
        score = difflib.SequenceMatcher(None, key, k).ratio()
        if score > best.get(value, 0.0):
            best[value] = score
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < FUZZY_MIN:
        return None
    ambiguous = len(ranked) > 1 and ranked[0][1] - ranked[1][1] < FUZZY_MARGIN
    return ranked[0][0], ranked[0][1], ambiguous


def _resolve(token: str, index: Dict[str, Any], candidates: bool = False) -> Optional[Tuple[Any, str, str]]:
    """回傳 (對象, 信心 exact／fuzzy／candidate, 說明)。歧義時只有 candidates=True 才回傳（信心 candidate）"""
    key = _compact(token)
    if not key:
        return None
    if key in index:
        return index[key], "exact", ""
    hit = _fuzzy_pick(key, index)
    if hit is None:
        return None
    value, score, ambiguous = hit
    if ambiguous and not candidates:
        return None
    label = value[0] if isinstance(value, tuple) else value
    return value, ("candidate" if ambiguous else "fuzzy"), f"{token}→{label} ({score:.2f})"


def _alias_index() -> Dict[str, str]:
    return {_compact(k): v for k, v in MAC_APP_ALIASES.items()}


def resolve_app(token: str, candidates: bool = False) -> Optional[Tuple[str, str, str]]:
    return _resolve(token, _alias_index(), candidates)


def project_index() -> Dict[str, Tuple[str, str]]:
    """compact 名稱 → (專案名, 路徑)。詞庫 projects 優先，再補 PROJECT_ROOTS 第一層資料夾（略過隱藏資料夾）"""
    idx: Dict[str, Tuple[str, str]] = {}
    for p in LEXICON.get("projects", []):
        for k in [p["name"], *p.get("aliases", [])]:
            idx[_compact(k)] = (p["name"], p["path"])
    for root in PROJECT_ROOTS:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            path = os.path.join(root, name)
            if not name.startswith(".") and os.path.isdir(path):
                idx.setdefault(_compact(name), (name, path))
    return idx


def resolve_project(token: str, candidates: bool = False) -> Optional[Tuple[Tuple[str, str], str, str]]:
    return _resolve(token, project_index(), candidates)


def _alias_in(alias: str, text: str) -> bool:
    """alias 是否出現在語句中：英文別名用詞邊界；兩字以內的中文別名（如『設定』）必須接在開關動詞後，
    避免『音量設定為 50』被當成要開系統設定"""
    if re.fullmatch(r"[a-z0-9 ._\-]+", alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None
    if len(alias) <= 2:
        return re.search(rf"(?:{_VERB})(?:一下|個)?\s*{re.escape(alias)}", text) is not None
    return alias in text


def build_hint_lines(utterance: str) -> List[str]:
    """只挑出語句中實際出現的別名，產生『別名→標準名稱』提示，避免撐大 prompt；
    語句提到『專案』時再附上專案清單，讓 LLM 能填 project_name"""
    hints = []
    lowered = utterance.lower()
    for alias, name in MAC_APP_ALIASES.items():
        if alias != name.lower() and _alias_in(alias, lowered):
            hints.append(f"{alias}→{name}")
    if re.search(r"專案|project", lowered):
        for p in LEXICON.get("projects", []):
            for alias in p.get("aliases", []):
                if alias.lower() in lowered:
                    hints.append(f"{alias}→{p['name']}")
        hints.append("專案：" + "、".join(sorted({name for name, _ in project_index().values()})[:10]))
    return hints


# ==========================================
# 1. macOS 底層控制執行器 (System Executors)
# ==========================================
_FAIL_PREFIXES = ("❌", "⚠️", "⏭️")


def _run(cmd: List[str], timeout: Optional[float] = None) -> Tuple[bool, str]:
    """執行外部指令並檢查結束碼。回傳 (成功?, 成功時的 stdout／失敗時 stderr 最後一行)；
    timeout 用於可能卡在授權對話框的指令（逾時視為失敗）"""
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"逾時 {timeout:g}s（可能在等待授權對話框）"
    except OSError as e:  # 指令不存在或無法執行
        return False, str(e)[:120]
    if p.returncode == 0:
        return True, (p.stdout or "").strip()
    lines = (p.stderr or "").strip().splitlines()
    return False, (lines[-1][:120] if lines else f"結束碼 {p.returncode}")


def control_mac_app(app_name: str, action: str = "open") -> str:
    """透過 open -a 或 osascript 開關 macOS 應用程式"""
    normalized_key = app_name.strip().lower()
    target_app = MAC_APP_ALIASES.get(normalized_key, app_name)

    if action == "open":
        ok, err = _run(["open", "-a", target_app])
        return f"✅ 已開啟應用程式：{target_app}" if ok else f"❌ 開啟失敗：{target_app}（{err}）"
    elif action == "quit":
        script = f'tell application "{target_app}" to quit'
        ok, err = _run(["osascript", "-e", script])
        return f"✅ 已關閉應用程式：{target_app}" if ok else f"❌ 關閉失敗：{target_app}（{err}）"
    return f"⚠️ 未知的動作：{action}"


def set_system_volume(level: Optional[int] = None, mute: Optional[bool] = None) -> str:
    """透過 AppleScript 控制 macOS 系統音量"""
    results = []
    if level is not None:
        clamped_level = max(0, min(100, int(level)))
        ok, err = _run(["osascript", "-e", f"set volume output volume {clamped_level}"])
        if not ok:
            return f"❌ 設定音量失敗（{err}）"
        results.append(f"音量設為 {clamped_level}%")
    if mute is not None:
        mute_str = "true" if mute else "false"
        ok, err = _run(["osascript", "-e", f"set volume output muted {mute_str}"])
        if not ok:
            return f"❌ 設定靜音失敗（{err}）"
        results.append("已靜音" if mute else "已取消靜音")
    return "✅ " + "、".join(results)


def adjust_screen_brightness(direction: str, steps: int = 3) -> str:
    """透過模擬 macOS 亮度鍵 (Key Code 144=調亮, 145=調暗) 調整螢幕亮度"""
    key_code = 144 if direction == "up" else 145
    steps = max(1, min(16, int(steps)))
    script = f'tell application "System Events" to repeat {steps} times\nkey code {key_code}\ndelay 0.05\nend repeat'
    ok, err = _run(["osascript", "-e", script])
    action_text = "調亮" if direction == "up" else "調暗"
    return f"✅ 已將螢幕{action_text} {steps} 格" if ok else f"❌ 調整亮度失敗（{err}）"


def web_search_or_open_url(query: str, browser: Optional[str] = None) -> str:
    """使用瀏覽器開啟網址或進行 Google 搜尋；browser 由執行上下文帶入（剛開啟的瀏覽器），未指定則用預設瀏覽器"""
    if query.startswith("http://") or query.startswith("https://"):
        url = query
    else:
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    ok, err = _run(["open", "-a", browser, url] if browser else ["open", url])
    return f"✅ 已開啟瀏覽器搜尋：{query}" if ok else f"❌ 開啟瀏覽器失敗（{err}）"


def system_power_action(action: str) -> str:
    """控制深色模式、睡眠或鎖定螢幕"""
    if action == "toggle_dark_mode":
        script = 'tell application "System Events" to tell appearance preferences to set dark mode to not dark mode'
        ok, err = _run(["osascript", "-e", script])
        return "✅ 已切換深色/淺色模式" if ok else f"❌ 切換深色模式失敗（{err}）"
    elif action in ("dark_mode_on", "dark_mode_off"):
        on = action == "dark_mode_on"
        script = f'tell application "System Events" to tell appearance preferences to set dark mode to {"true" if on else "false"}'
        ok, err = _run(["osascript", "-e", script])
        if not ok:
            return f"❌ 設定深色模式失敗（{err}）"
        return "✅ 已開啟深色模式" if on else "✅ 已關閉深色模式（淺色）"
    elif action == "sleep":
        ok, err = _run(["pmset", "sleepnow"])
        return "✅ 系統進入睡眠" if ok else f"❌ 睡眠失敗（{err}）"
    elif action == "lock_screen":
        script = 'tell application "System Events" to keystroke "q" using {control down, command down}'
        ok, err = _run(["osascript", "-e", script])
        return "✅ 已鎖定螢幕" if ok else f"❌ 鎖定螢幕失敗（{err}）"
    return f"⚠️ 不支援的系統動作：{action}"


def open_project(project_name: str, editor: Optional[str] = None) -> str:
    """用編輯器開啟白名單內的專案資料夾（不接受任意路徑）；editor 預設 Visual Studio Code"""
    hit = resolve_project(project_name)
    if hit is None:
        known = "、".join(sorted({name for name, _ in project_index().values()})[:10]) or "（無）"
        return f"❌ 找不到專案：{project_name}（已知專案：{known}）"
    (name, path), _, _ = hit
    if not os.path.isdir(path):
        return f"❌ 專案路徑不存在：{path}"
    target = editor or "Visual Studio Code"
    ok, err = _run(["open", "-a", target, path])
    return f"✅ 已用 {target} 開啟專案：{name}" if ok else f"❌ 開啟專案失敗：{name}（{err}）"


# ---------- 執行上下文與讀畫面 ----------
@dataclass
class ExecContext:
    """同一次 process_voice_command 內共用的狀態（不跨語句）"""
    last_editor: Optional[str] = None
    last_browser: Optional[str] = None
    page_pending: Optional[dict] = None  # {"url", "t"}：剛開的網頁，讀畫面前要先等載入
    failed: Set[str] = field(default_factory=set)  # browser／editor／page：這幾類前置步驟失敗過
    replies: List[str] = field(default_factory=list)
    # 以下為「目前這一步」的狀態，由 router 在每步開始時重設
    step: Optional[dict] = None
    utterance: str = ""
    results: List[str] = field(default_factory=list)
    status: Optional[str] = None
    chat_done: bool = False


PAGE_SETTLE_S = 1.0  # 開網頁後至少等這麼久才開始問「載入完了嗎」（避免讀到舊頁面的 loading=false）
PAGE_LOAD_MAX_S = 8.0
PAGE_FIXED_WAIT_S = 2.5  # 非 Chrome 無法查載入狀態，固定等待
EXTERNAL_TIMEOUT_S = 15.0  # screencapture／sips 逾時
POLL_TIMEOUT_S = 5.0  # 查 Chrome 載入狀態的 osascript 逾時（第一次可能跳出自動化授權）
SCREEN_MAX_PX = 1280
SCREEN_QUESTION = "這個畫面在顯示什麼？用繁體中文條列重點。"
SCREEN_SYSTEM_PROMPT = "你是螢幕內容辨識助手。用繁體中文條列重點，最多 5 點，只描述畫面上看得到的內容，不要編造。"


def _wait_for_page(ctx: ExecContext) -> None:
    pending = ctx.page_pending
    if not pending:
        return
    elapsed = time.monotonic() - pending["t"]
    if ctx.last_browser == "Google Chrome":
        if PAGE_SETTLE_S - elapsed > 0:
            time.sleep(PAGE_SETTLE_S - elapsed)
        deadline = time.monotonic() + PAGE_LOAD_MAX_S
        while time.monotonic() < deadline:
            ok, out = _run(["osascript", "-e", 'tell application "Google Chrome" to return loading of active tab of front window'], POLL_TIMEOUT_S)
            if not ok or out.lower() != "true":
                break
            time.sleep(0.3)
    elif PAGE_FIXED_WAIT_S - elapsed > 0:
        time.sleep(PAGE_FIXED_WAIT_S - elapsed)
    ctx.page_pending = None


def read_screen(ctx: Optional[ExecContext] = None) -> str:
    """截主螢幕 → sips 縮到 1280 → 送 VL 模型辨識；辨識結果寫入 ctx.replies。暫存檔一律刪除"""
    ctx = ctx or ExecContext()
    step = ctx.step if ctx.step is not None else {}
    t = time.perf_counter()
    _wait_for_page(ctx)
    step["wait_ms"] = (time.perf_counter() - t) * 1000

    tmp = Path(tempfile.mkdtemp(prefix="macvoice_"))
    try:
        raw, small = tmp / "screen.png", tmp / "screen_small.png"
        t = time.perf_counter()
        ok, err = _run(["screencapture", "-x", "-m", str(raw)], EXTERNAL_TIMEOUT_S)
        step["capture_ms"] = (time.perf_counter() - t) * 1000
        if not ok or not raw.exists() or raw.stat().st_size == 0:
            return f"❌ 截圖失敗（{err or '沒有輸出檔'}；請確認已授權螢幕錄製）"
        t = time.perf_counter()
        ok, err = _run(["sips", "-Z", str(SCREEN_MAX_PX), str(raw), "--out", str(small)], EXTERNAL_TIMEOUT_S)
        step["resize_ms"] = (time.perf_counter() - t) * 1000
        if not ok or not small.exists():
            return f"❌ 縮圖失敗（{err}）"
        data_uri = "data:image/png;base64," + base64.b64encode(small.read_bytes()).decode()
        messages = [
            {"role": "system", "content": SCREEN_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": SCREEN_QUESTION},
            ]},
        ]
        meta: dict = {}
        t = time.perf_counter()
        try:
            message = _llm(messages, timeout=VISION_TIMEOUT_S, tools=False, max_tokens=256, meta=meta)
        except LLMError as e:
            ctx.status = "llm_unavailable" if isinstance(e, LLMUnavailable) else "llm_error"
            return f"❌ AI 無法辨識畫面（{e}）"
        finally:
            step["vl_ms"] = (time.perf_counter() - t) * 1000
            step["vl"] = meta
        text = to_traditional((message.get("content") or "").strip())
        if not text:
            return "❌ 畫面辨識沒有輸出內容"
        ctx.replies.append(text)
        return f"✅ 畫面內容：{text}"
    except OSError as e:
        return f"❌ 讀取截圖失敗（{e}）"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def chat_reply(ctx: Optional[ExecContext] = None) -> str:
    """chat call：把這一步的原句直接當成使用者訊息送入模型（不帶 tools），回覆寫入 ctx.replies。
    同一步只回覆一次；失敗只記錄，不拋例外"""
    ctx = ctx or ExecContext()
    if ctx.chat_done:
        return "✅ 已回覆"
    ctx.chat_done = True
    step = ctx.step if ctx.step is not None else {}
    meta: dict = {}
    t = time.perf_counter()
    try:
        message = _llm(
            [{"role": "system", "content": CHAT_SYSTEM_PROMPT}, {"role": "user", "content": ctx.utterance}],
            timeout=CHAT_TIMEOUT_S, tools=False, max_tokens=128, meta=meta,
        )
    except LLMError as e:
        ctx.status = "llm_unavailable" if isinstance(e, LLMUnavailable) else "llm_error"
        text = f"⚠️ AI 回覆失敗（{e}）"
        ctx.replies.append(text)
        return text
    finally:
        step["chat_ms"] = (time.perf_counter() - t) * 1000
        step["chat"] = meta
    reply = to_traditional((message.get("content") or "").strip()) or "無法理解指令"
    ctx.replies.append(reply)
    return f"🤖 AI 回覆：{reply}"


AVAILABLE_FUNCTIONS = {
    "control_mac_app": control_mac_app,
    "set_system_volume": set_system_volume,
    "adjust_screen_brightness": adjust_screen_brightness,
    "web_search_or_open_url": web_search_or_open_url,
    "system_power_action": system_power_action,
    "open_project": open_project,
    "read_screen": read_screen,
    "chat_reply": chat_reply,
}


def _skipped(name: str) -> str:
    return f"⏭️ 略過 {name}：前一步失敗"


def _dispatch(name: str, args: dict, ctx: ExecContext) -> str:
    """執行單一工具，並維護 ExecContext：記住剛開的瀏覽器／編輯器、剛開的網頁；前置步驟失敗時略過依賴它的步驟"""
    if name == "control_mac_app":
        res = control_mac_app(**args)
        target = MAC_APP_ALIASES.get(args["app_name"].strip().lower(), args["app_name"])
        kind = APP_KIND.get(target, "other")
        if args.get("action", "open") == "open" and kind in ("browser", "editor"):
            if res.startswith(_FAIL_PREFIXES):
                ctx.failed.add(kind)
            else:
                ctx.failed.discard(kind)
                if kind == "browser":
                    ctx.last_browser = target
                else:
                    ctx.last_editor = target
        return res
    if name == "web_search_or_open_url":
        if "browser" in ctx.failed:
            return _skipped(name)
        res = web_search_or_open_url(**args, browser=ctx.last_browser)
        if res.startswith(_FAIL_PREFIXES):
            ctx.failed.add("page")
        else:
            ctx.failed.discard("page")
            ctx.page_pending = {"t": time.monotonic()}
        return res
    if name == "open_project":
        if "editor" in ctx.failed:
            return _skipped(name)
        return open_project(args["project_name"], args.get("editor") or ctx.last_editor)
    if name == "read_screen":
        if ctx.failed & {"browser", "page"}:
            return _skipped(name)
        return read_screen(ctx)
    if name == "chat_reply":
        return chat_reply(ctx)
    return AVAILABLE_FUNCTIONS[name](**args)


def run_tool(name: str, args: dict, ctx: ExecContext) -> str:
    """執行一個工具並記錄耗時（ctx.step["exec_ms"]）與結果（ctx.results）"""
    t = time.perf_counter()
    res = _dispatch(name, dict(args), ctx)
    if ctx.step is not None:
        ctx.step.setdefault("exec_ms", []).append((time.perf_counter() - t) * 1000)
    ctx.results.append(res)
    return res


# ==========================================
# 2. 文本斷句器與快速軌道 (Fast Path)
# ==========================================
_PREFIX_RE = re.compile(rf"^(?:{_alt(_FILLERS.get('prefix', []))})\s*") if _FILLERS.get("prefix") else None
_SUFFIX_RE = re.compile(rf"\s*(?:{_alt(_FILLERS.get('suffix', []))})$") if _FILLERS.get("suffix") else None
_ACTION_WORDS_RE = re.compile(rf"{_VERB}|搜尋|搜索|查|調|切換|鎖|辨識|看|念")
_BA_RE = re.compile(rf"^把\s*(?P<x>.+?)\s*(?P<v>{_VERB})$")
_TOPIC_RE = re.compile(rf"^(?P<x>.+?)\s*(?P<v>{_LONG_VERB})$")

# 安全守門：否定、敘述、提問一律不走 Fast（交給 LLM）
_NEG_RE = re.compile(r"不要|不用|不必|不想|不能|不可以|不行|不需要|別|勿|沒有|先不")
_NARR_RE = re.compile(rf"昨天|前天|剛剛|剛才|之前|已經|以前|上次|曾經|本來|原本|(?:{_VERB})了")
_QUESTION_RE = re.compile(r"嗎|什麼|怎麼|為什麼|為何|如何|是不是|有沒有|哪|幾|多少|\?")


def canonicalize_clause(clause: str) -> str:
    """只給 Fast Path 用：去疊用的前綴填充詞與語尾助詞，並把『把 X 打開』『X 打開』改寫成『打開 X』。
    送給 LLM 的仍是原句（填充詞要留給 LLM 判斷語氣）"""
    s = clause.strip()
    while True:
        t = s
        if _PREFIX_RE:
            t = _PREFIX_RE.sub("", t, count=1).strip()
        if _SUFFIX_RE:
            t = _SUFFIX_RE.sub("", t, count=1).strip()
        if t == s:
            break
        s = t
    for pattern in (_BA_RE, _TOPIC_RE):
        m = pattern.match(s)
        if m and not _ACTION_WORDS_RE.search(m["x"]):
            return f"{m['v']}{m['x']}"
    return s


class Match:
    """Fast Path 比對結果：可呼叫（執行全部動作）。比對與執行分開，路由器才能依說話順序決定何時執行"""

    def __init__(self, actions: List[Tuple[str, dict]], confidence: str = "exact", note: str = ""):
        self.actions = actions
        self.confidence = confidence  # exact／fuzzy／candidate
        self.note = note

    def run(self, ctx: Optional[ExecContext] = None) -> List[str]:
        ctx = ctx or ExecContext()
        return [run_tool(name, args, ctx) for name, args in self.actions]

    def __call__(self, ctx: Optional[ExecContext] = None) -> str:
        return "；".join(self.run(ctx))


_SEARCH = _alt(["搜尋", "搜索", "查詢", "查一下", "查查", "查"])
_DARK = "(?:深色|暗黑|夜間|暗色|黑暗)"
_LIGHT = "(?:淺色|亮色|日間)"
_VOL_SET = r"(?:調到|調成|調整到|調整為|設為|設定為|設成|設定成|開到|改成|改為|到|成|為)?"
_I = re.IGNORECASE


class FastPathMatcher:
    # --- 讀畫面（含『什麼』問句，所以在問句守門之前判斷）---
    _read_screen_re = (
        re.compile(r"^(?:辨識|辨認|識別|解讀|分析|看看|看|讀|念)(?:一下)?(?:目前的?|現在的?|這個?)?(?:(?:頁面|網頁|畫面|頁|螢幕)(?:上)?(?:的)?(?:內容)?|內容)$"),
        re.compile(r"^(?:這個?)?(?:頁面|網頁|畫面|頁)(?:上)?(?:在)?(?:寫|說|講|有|顯示)(?:了)?什麼$"),
        re.compile(r"^(?:螢幕|畫面)上有什麼$"),
    )
    # --- 其餘規則 ---
    _open_search_re = re.compile(rf"^(?P<v>{_OPEN})(?:一下|個)?\s*(?P<app>.+?)\s*(?:並且|並|然後|再|且)?\s*(?:{_SEARCH})\s*(?P<q>.+)$", _I)
    _search_res = (
        re.compile(rf"^(?:上網|網路上|網上|線上)?(?:{_SEARCH})\s*(?P<q>.+)$", _I),
        re.compile(rf"^(?:用\s*)?(?:google|谷歌)\s*(?:一下|{_SEARCH})\s*(?P<q>.+)$", _I),
    )
    _project_re = re.compile(rf"^(?:(?:用|使用|在)\s*(?P<ed>.+?)\s*)?(?:{_OPEN})(?:一下|個)?\s*(?P<proj>.+?)\s*(?:的)?(?:專案|項目|project)$", _I)
    _vol_level_re = re.compile(rf"^(?:把)?(?:系統)?(?:音量|聲音)\s*{_VOL_SET}\s*(?P<n>\d{{1,3}})\s*(?:%|趴|度)?$")
    _vol_word_re = re.compile(rf"^(?:把)?(?:系統)?(?:音量|聲音)\s*{_VOL_SET}\s*(?P<w>最大|最小|最高|最低|一半)$")
    _mute_re = re.compile(r"^(?:靜音|(?:關掉|關閉|關)(?:系統)?(?:聲音|音量))$")
    _unmute_re = re.compile(r"^(?:取消靜音|解除靜音|(?:打開|開啟|開)(?:系統)?(?:聲音|音量))$")
    _bright_re = re.compile(
        r"^(?:把)?(?P<subj>(?:螢幕|屏幕|畫面)?(?:的)?(?:亮度)?)\s*(?:稍微|再)?(?P<verb>調整|調|弄|開|變|設定|設)?(?:得|到|成)?(?P<most>最)?(?P<d>暗|亮|低|高)\s*(?P<rest>.*)$"
    )
    _dark_on_re = re.compile(rf"^(?:打開|開啟|開|啟用|切換到|切換成|切到|切成|換成|改成|轉成)(?:一下)?{_DARK}模式$")
    _dark_off_re = re.compile(rf"^(?:(?:關掉|關閉|關|停用|取消|退出)(?:一下)?{_DARK}模式|(?:切換到|切換成|切到|切成|換成|改成|轉成)(?:一下)?{_LIGHT}模式|(?:打開|開啟|開|啟用){_LIGHT}模式)$")
    _dark_toggle_re = re.compile(rf"^切換{_DARK}模式$")
    _lock_res = (re.compile(r"^(?:鎖定|鎖)(?:螢幕|屏幕|畫面|電腦|屏)$"), re.compile(r"^(?:把)?(?:螢幕|電腦)(?:鎖定|鎖起來|鎖住)$"))
    _app_re = re.compile(rf"^(?P<v>{_VERB})(?:一下|個|一個)?\s*(?:那個|這個)?\s*(?P<app>.+)$", _I)

    def split_clauses(self, text: str) -> List[str]:
        """將 STT 輸出的長句依據標點符號與中文口語連接詞切分為獨立子句（不以空白切分，以免拆壞「VS Code」「調到 50」）"""
        pattern = r"[，。、；,;！!？?]+|(?:然後|接著|再幫我|順便|還有|並且|以及)"
        return [c.strip() for c in re.split(pattern, text) if c.strip()]

    def match(self, clause: str, candidates: bool = False) -> Optional[Match]:
        """以 Fast Path 比對單一子句；命中回傳「尚未執行」的 Match，未命中回傳 None。
        candidates=True 時，歧義（兩個 App 相似度太接近）的第 1 名也會回傳（信心 candidate），
        供 LLM 不可用時補救；被安全守門擋下的句子永遠回傳 None。"""
        raw = clause.strip()
        if raw == "讓電腦睡眠":  # 睡眠誤觸成本高：只接受這個固定句，不做放寬
            return Match([("system_power_action", {"action": "sleep"})])
        s = canonicalize_clause(raw)
        if not s or _NEG_RE.search(s) or _NARR_RE.search(s):
            return None
        for pattern in self._read_screen_re:
            if pattern.match(s):
                return Match([("read_screen", {})])
        if _QUESTION_RE.search(s):
            return None
        for rule in (
            self._r_open_search, self._r_search, self._r_project, self._r_volume, self._r_mute,
            self._r_brightness, self._r_dark, self._r_lock, self._r_app,
        ):
            m = rule(s, candidates)
            if m is not None:
                return m
        return None

    # ---- 規則：命中回傳 Match，否則 None ----
    def _r_open_search(self, s: str, candidates: bool) -> Optional[Match]:
        """『開 google 搜尋 X』→ 開瀏覽器 + 搜尋；『開瀏覽器查 X』→ 只搜尋，不另外開 App"""
        m = self._open_search_re.match(s)
        if not m or not m["q"].strip():
            return None
        query = m["q"].strip()
        search = ("web_search_or_open_url", {"query": query})
        if _compact(m["app"]) in ("瀏覽器", "browser"):
            return Match([search])
        res = resolve_app(m["app"], candidates)
        if res is None or APP_KIND.get(res[0]) != "browser":
            return None
        name, conf, note = res
        return Match([("control_mac_app", {"app_name": name, "action": "open"}), search], conf, note)

    def _r_search(self, s: str, candidates: bool) -> Optional[Match]:
        for pattern in self._search_res:
            m = pattern.match(s)
            if m:
                query = m["q"].strip()
                if query and not query.startswith("看"):  # 『查看…』不是搜尋
                    return Match([("web_search_or_open_url", {"query": query})])
        return None

    def _r_project(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._project_re.match(s)
        if not m:
            return None
        args: Dict[str, Any] = {}
        conf, note = "exact", ""
        if m["ed"]:
            ed = resolve_app(m["ed"], candidates)
            if ed is None or APP_KIND.get(ed[0]) != "editor":
                return None
            args["editor"], conf, note = ed
        hit = resolve_project(m["proj"], candidates)
        if hit is not None:
            args["project_name"] = hit[0][0]
            if hit[1] != "exact":
                conf, note = hit[1], hit[2]
        else:  # 不在白名單：照樣比對成功，由 open_project 回報「找不到專案」，不交給 LLM 亂猜
            args["project_name"] = m["proj"].strip()
        return Match([("open_project", args)], conf, note)

    def _r_volume(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._vol_level_re.match(s)
        if m:
            return Match([("set_system_volume", {"level": int(m["n"])})])
        m = self._vol_word_re.match(s)
        if m:
            level = {"最大": 100, "最高": 100, "最小": 0, "最低": 0, "一半": 50}[m["w"]]
            return Match([("set_system_volume", {"level": level})])
        return None

    def _r_mute(self, s: str, candidates: bool) -> Optional[Match]:
        if self._mute_re.match(s):
            return Match([("set_system_volume", {"mute": True})])
        if self._unmute_re.match(s):
            return Match([("set_system_volume", {"mute": False})])
        return None

    def _r_brightness(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._bright_re.match(s)
        if not m:
            return None
        has_subject = bool(m["subj"])
        if m["d"] in ("低", "高") and not has_subject:
            return None  # 『調高』可能是音量，必須指明螢幕或亮度
        if not has_subject and not (m["most"] and m["verb"]):
            return None
        direction = "down" if m["d"] in ("暗", "低") else "up"
        rest = m["rest"].strip()
        if m["most"]:
            return Match([("adjust_screen_brightness", {"direction": direction, "steps": 16})]) if not rest else None
        if rest in ("", "一點", "一點點", "一些", "些", "稍微"):
            steps = 3
        else:
            n = re.fullmatch(r"(\d+)\s*格", rest)
            if not n:
                return None
            steps = int(n[1])
        return Match([("adjust_screen_brightness", {"direction": direction, "steps": steps})])

    def _r_dark(self, s: str, candidates: bool) -> Optional[Match]:
        if self._dark_on_re.match(s):
            return Match([("system_power_action", {"action": "dark_mode_on"})])
        if self._dark_off_re.match(s):
            return Match([("system_power_action", {"action": "dark_mode_off"})])
        if self._dark_toggle_re.match(s):
            return Match([("system_power_action", {"action": "toggle_dark_mode"})])
        return None

    def _r_lock(self, s: str, candidates: bool) -> Optional[Match]:
        if any(p.match(s) for p in self._lock_res):
            return Match([("system_power_action", {"action": "lock_screen"})])
        return None

    def _r_app(self, s: str, candidates: bool) -> Optional[Match]:
        m = self._app_re.match(s)
        if not m:
            return None
        res = resolve_app(m["app"], candidates)
        if res is None:
            return None
        name, conf, note = res
        action = "quit" if re.fullmatch(_QUIT, m["v"]) else "open"
        return Match([("control_mac_app", {"app_name": name, "action": action})], conf, note)

    def try_execute(self, clause: str) -> Optional[str]:
        """嘗試以 Fast Path 匹配並立即執行單一子句，若未命中則回傳 None"""
        run = self.match(clause)
        return run() if run else None


# ==========================================
# 3. AI 軌道 (Slow Path - mlx-vlm + LFM3-VL-2B Function Calling)
# ==========================================
MLX_VLM_URL = "http://127.0.0.1:8080"
MODEL_ID = "AI_models/LFM3-VL-2B-MLX-4bit"  # 須與 mlx_vlm.server 的 --model 一致

PLAN_TIMEOUT_S = 10.0  # 規劃（工具呼叫）逾時
CHAT_TIMEOUT_S = 10.0
VISION_TIMEOUT_S = 30.0
WARMUP_TIMEOUT_S = 60.0  # 首次載入較慢
BREAKER_COOLDOWN_S = 30.0  # LLM 連不上之後，這段時間內不再嘗試

SYSTEM_PROMPT = (
    "你是運行在 macOS 上的本地語音控制助手。依使用者的繁體中文指令呼叫工具，可一次呼叫多個。"
    "若指令與任何工具無關，不要呼叫工具，直接簡短回覆。"
    "『打開瀏覽器查某事』只需呼叫 web_search_or_open_url，不要另外開啟瀏覽器。"
    "app_name 必須使用標準英文名稱。"
)
CHAT_SYSTEM_PROMPT = (
    "你是運行在 macOS 上的本地語音助手。用繁體中文簡短回答（兩句以內）。"
    "句子中的電腦操作（開關程式、音量、亮度、搜尋等）已由其他程式處理，不要描述或重複，只回答提問或閒聊的部分。"
)


def _fn(name: str, description: str, properties: dict, required: tuple = ()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)},
        },
    }


TOOLS_SCHEMA = [
    _fn(
        "control_mac_app",
        "在 macOS 上打開或關閉指定的應用程式。",
        {
            "app_name": {
                "type": "string",
                "description": "macOS 應用程式標準英文名稱，如 'Google Chrome', 'Safari', 'Terminal', 'Visual Studio Code', 'Cursor', 'Calculator', 'Notes', 'Finder', 'Spotify'",
            },
            "action": {"type": "string", "enum": ["open", "quit"]},
        },
        ("app_name", "action"),
    ),
    _fn(
        "set_system_volume",
        "調整 macOS 系統音量（0-100）或切換靜音。",
        {
            "level": {"type": "integer", "description": "音量百分比 0-100"},
            "mute": {"type": "boolean", "description": "是否靜音"},
        },
    ),
    _fn(
        "adjust_screen_brightness",
        "調亮或調暗 macOS 螢幕亮度。",
        {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "steps": {"type": "integer", "description": "調整格數 1-16，『一點』約為 3，『最亮/最暗』為 16"},
        },
        ("direction", "steps"),
    ),
    _fn(
        "web_search_or_open_url",
        "使用瀏覽器進行 Google 搜尋（如查天氣、查新聞）或打開特定網站。",
        {"query": {"type": "string", "description": "搜尋關鍵字或網址"}},
        ("query",),
    ),
    _fn(
        "system_power_action",
        "macOS 系統控制：睡眠、鎖定螢幕、深色模式。打開深色模式用 dark_mode_on，關閉用 dark_mode_off。",
        {"action": {
            "type": "string",
            "enum": ["dark_mode_on", "dark_mode_off", "lock_screen", "sleep", "toggle_dark_mode"],
            "description": "toggle_dark_mode 只在使用者明說『切換』時使用",
        }},
        ("action",),
    ),
    _fn(
        "open_project",
        "用編輯器開啟一個已知的程式專案資料夾。",
        {
            "project_name": {"type": "string", "description": "專案名稱，如 'mac_ai_control'"},
            "editor": {"type": "string", "description": "編輯器標準英文名稱，如 'Visual Studio Code', 'Cursor'；使用者沒指定時不要填"},
        },
        ("project_name",),
    ),
    _fn(
        "read_screen",
        "看使用者的螢幕畫面並說明內容。使用者問『畫面／螢幕／這頁／網頁上有什麼、寫什麼、在講什麼』時呼叫。不需要參數。",
        {},
    ),
    _fn(
        "chat_reply",
        "使用者在提問、閒聊或需要文字回答時呼叫（如『Python 是什麼』『今天心情不錯』）。不需要參數。",
        {},
    ),
]

_SCHEMA_BY_NAME = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS_SCHEMA}


def validate_tool_call(name: str, args: Any) -> Tuple[Optional[dict], Optional[str]]:
    """檢查 LLM 回傳的工具名稱與參數，回傳 (乾淨參數, None) 或 (None, 錯誤原因)。
    只信任 schema：未知工具、未知參數、缺必填、型別錯誤、enum 不符一律拒絕，不交給 executor。"""
    schema = _SCHEMA_BY_NAME.get(name)
    if schema is None:
        return None, f"未知工具：{name}"
    if not isinstance(args, dict):
        return None, f"{name} 參數不是物件"
    props = schema["properties"]
    clean: Dict[str, Any] = {}
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            return None, f"{name} 出現未定義參數：{key}"
        if spec["type"] == "integer":
            if isinstance(value, bool):
                return None, f"{name}.{key} 應為整數"
            try:
                value = int(value)
            except (TypeError, ValueError):
                return None, f"{name}.{key} 應為整數"
        elif spec["type"] == "boolean":
            if not isinstance(value, bool):
                return None, f"{name}.{key} 應為布林"
        elif spec["type"] == "string":
            if not isinstance(value, str) or not value.strip():
                return None, f"{name}.{key} 應為非空字串"
        if "enum" in spec and value not in spec["enum"]:
            return None, f"{name}.{key} 不在允許值 {spec['enum']}"
        clean[key] = value
    missing = [k for k in schema["required"] if k not in clean]
    if missing:
        return None, f"{name} 缺少必填參數：{missing}"
    # 實測：LLM 在 mute=true 時偶爾多塞 level（如 30），靜音語意以 mute 為準
    if name == "set_system_volume" and clean.get("mute") is True:
        clean.pop("level", None)
    # 專案只能從白名單解析，不接受 LLM 給的任意名稱／路徑；editor 必須是編輯器類 App
    if name == "open_project":
        hit = resolve_project(clean["project_name"])
        if hit is None:
            return None, f"open_project 找不到專案：{clean['project_name']}"
        clean["project_name"] = hit[0][0]
        if "editor" in clean:
            editor = resolve_app(clean["editor"])
            if editor is None or APP_KIND.get(editor[0]) != "editor":
                return None, f"open_project.editor 不是已知編輯器：{clean['editor']}"
            clean["editor"] = editor[0]
    return clean, None


class LLMError(Exception):
    """LLM 回應無法使用（格式錯誤、HTTP 錯誤）"""


class LLMUnavailable(LLMError):
    """LLM 連不上或逾時（含熔斷中）"""


_llm_down_until = 0.0  # time.monotonic() 時間點；之前不再呼叫 LLM


def reset_llm_breaker() -> None:
    global _llm_down_until
    _llm_down_until = 0.0


def _chat(messages: List[dict], timeout: float = PLAN_TIMEOUT_S, tools: bool = True,
          max_tokens: int = 512, meta: Optional[dict] = None) -> dict:
    """呼叫 mlx_vlm.server 的 OpenAI 相容 /chat/completions；回傳第一個 choice 的 message。
    meta 若有傳入，回填 server 的 timings（prefill_ms／decode_ms／prompt_n／cache_n／predicted_n）"""
    body = {
        "model": MODEL_ID,
        "messages": messages,
        # 官方建議取樣參數；enable_thinking=False 關閉 <think>，否則延遲倍增
        "temperature": 0.1,
        "top_p": 1.0,
        "top_k": 50,
        "repetition_penalty": 1.1,
        "max_tokens": max_tokens,
        "enable_thinking": False,
    }
    if tools:
        body["tools"] = TOOLS_SCHEMA
    req = urllib.request.Request(
        f"{MLX_VLM_URL}/chat/completions",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if meta is not None:
        t = data.get("timings") or {}
        meta.update(
            prefill_ms=t.get("prompt_ms"), decode_ms=t.get("predicted_ms"),
            prompt_n=t.get("prompt_n"), cache_n=t.get("cache_n"), predicted_n=t.get("predicted_n"),
        )
    message = data["choices"][0]["message"]
    if not isinstance(message, dict):
        raise TypeError("message 不是物件")
    return message


def _llm(messages: List[dict], *, timeout: float, tools: bool = True, max_tokens: int = 512,
         meta: Optional[dict] = None) -> dict:
    """所有 LLM 呼叫的入口：把連線／逾時／格式錯誤轉成 LLMError，並在連不上時啟動熔斷"""
    global _llm_down_until
    if time.monotonic() < _llm_down_until:
        raise LLMUnavailable("AI 服務暫時無法使用（熔斷中）")
    try:
        return _chat(messages, timeout=timeout, tools=tools, max_tokens=max_tokens, meta=meta)
    except urllib.error.HTTPError as e:  # server 有回應但出錯：不熔斷
        raise LLMError(f"HTTP {e.code}") from e
    except (OSError, http.client.HTTPException) as e:  # 連線被拒、逾時、中斷
        _llm_down_until = time.monotonic() + BREAKER_COOLDOWN_S
        raise LLMUnavailable(str(e) or type(e).__name__) from e
    except (KeyError, IndexError, TypeError, ValueError) as e:  # 含 JSONDecodeError
        raise LLMError(f"回應格式錯誤：{type(e).__name__}") from e


def plan_slow_path(utterance: str, meta: Optional[dict] = None) -> Tuple[List[Tuple[str, dict]], Optional[str], List[str]]:
    """送 LLM 規劃工具呼叫。回傳 (通過驗證的 [(工具, 參數)], LLM 純文字回覆, 被拒絕的原因)。
    meta 若有傳入，回填 llm（server timings）與 validate_ms；LLM 不可用時拋出 LLMError"""
    hints = build_hint_lines(utterance)
    system = SYSTEM_PROMPT + (f"\n已知詞庫：{'；'.join(hints)}" if hints else "")
    llm_meta: dict = {}
    message = _llm(
        [{"role": "system", "content": system}, {"role": "user", "content": utterance}],
        timeout=PLAN_TIMEOUT_S, meta=llm_meta,
    )

    t = time.perf_counter()
    calls: List[Tuple[str, dict]] = []
    rejected: List[str] = []
    for call in message.get("tool_calls") or []:
        func = call.get("function") if isinstance(call, dict) else None
        if not isinstance(func, dict):
            rejected.append("tool_call 格式錯誤（缺少 function 物件）")
            continue
        raw_args = func.get("arguments", {})
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            rejected.append(f"{func.get('name')} 參數不是合法 JSON")
            continue
        clean, err = validate_tool_call(func.get("name", ""), args)
        if err:
            rejected.append(err)
        else:
            calls.append((func["name"], clean))
    if meta is not None:
        meta["llm"] = llm_meta
        meta["validate_ms"] = (time.perf_counter() - t) * 1000
    return calls, message.get("content"), rejected


def execute_slow_path_llm(utterance: str, timing: Optional[dict] = None, ctx: Optional[ExecContext] = None) -> List[str]:
    """將未命中的自然語言指令送入本地 LLM 進行多工具拆解與執行；
    若傳入 timing，會回填 llm_ms（規劃耗時）、llm（server timings）、validate_ms。
    LLM 不可用時拋出 LLMError，由呼叫端決定補救"""
    step = timing if timing is not None else {}
    ctx = ctx or ExecContext()
    ctx.utterance = utterance
    t0 = time.perf_counter()
    try:
        calls, reply, rejected = plan_slow_path(utterance, step)
    finally:
        step["llm_ms"] = (time.perf_counter() - t0) * 1000
    llm = step.get("llm") or {}
    if llm.get("prefill_ms") is not None and llm.get("decode_ms") is not None:
        llm["overhead_ms"] = max(0.0, step["llm_ms"] - llm["prefill_ms"] - llm["decode_ms"])

    logs = [f"⚠️ 已拒絕不合法的工具呼叫：{r}" for r in rejected]
    if not calls and not rejected:
        # 沒有工具可呼叫：規劃呼叫的 content 不採用，另外觸發 chat call
        return [chat_reply(ctx)]
    if rejected and not calls:
        ctx.status = "rejected"
    for name, args in calls:
        res = run_tool(name, args, ctx)
        logs.append(f"[Slow Path - LLM] {name}({args}) -> {res}")
    return logs


def warmup_llm() -> Optional[float]:
    """啟動時送一次極短請求，把首次載入／編譯成本吃掉；回傳耗時秒數。
    LLM 連不上時只印警告並回傳 None，不讓程式結束（Fast Path 照常可用）"""
    start = time.perf_counter()
    try:  # 帶上與正式請求相同的 system prompt，prefix cache 才會對到
        _llm([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "你好"}], timeout=WARMUP_TIMEOUT_S)
    except LLMError as e:
        print(f"⚠️ LLM 暖機失敗：{e}（Fast Path 仍可使用）")
        return None
    reset_llm_breaker()
    return time.perf_counter() - start


# ==========================================
# 4. 雙軌整合路由器 (Main Voice Command Pipeline)
# ==========================================
def _step_status(ctx: ExecContext) -> str:
    if ctx.status:
        return ctx.status
    if any(r.startswith(("❌", "⚠️")) for r in ctx.results):
        return "exec_failed"
    if any(r.startswith("⏭️") for r in ctx.results):
        return "skipped"
    return "ok"


def _recover_slow_segment(clauses: List[str], matcher: FastPathMatcher, ctx: ExecContext, err: LLMError) -> List[str]:
    """LLM 不可用時的補救：有 Fast 候選（歧義而被擋下的第 1 名）就執行；沒有就告知使用者。
    被安全守門擋下的句子沒有候選，不會執行"""
    ctx.status = "llm_unavailable" if isinstance(err, LLMUnavailable) else "llm_error"
    logs = []
    for clause in clauses:
        cand = matcher.match(clause, candidates=True)
        if cand is not None:
            logs += [f"[Fallback | {cand.note}] {r}" for r in cand.run(ctx)]
            ctx.status = "fallback"
        else:
            msg = f"⚠️ AI 服務無法使用（{err}），這句沒有處理：「{clause}」"
            ctx.replies.append(msg)
            logs.append(msg)
    return logs


def process_voice_command(stt_text: str) -> dict:
    """依說話順序執行。命中 Fast Path 的子句各自成一步；連續未命中的子句合併成一步送 LLM。
    回傳 {"stages", "steps": [{kind, text, ms, status, logs, ...}], "replies", "total_ms"}，ms 含該步的執行時間"""
    print(f"\n🎙️ 收到語音辨識文字：「{stt_text}」")
    start_time = time.perf_counter()

    t = time.perf_counter()
    normalized = normalize_utterance(stt_text)
    stages = {"normalize_ms": (time.perf_counter() - t) * 1000}
    log_normalization(stt_text, normalized)
    stt_text = normalized

    matcher = FastPathMatcher()
    t = time.perf_counter()
    clauses = matcher.split_clauses(stt_text)
    stages["split_ms"] = (time.perf_counter() - t) * 1000
    print(f"✂️ 子句切分結果：{clauses}")

    t = time.perf_counter()
    segments: List[Tuple[str, List[str], Optional[Match]]] = []
    for clause in clauses:
        run = matcher.match(clause)
        if run:
            segments.append(("fast", [clause], run))
        elif segments and segments[-1][0] == "slow":
            segments[-1][1].append(clause)
        else:
            segments.append(("slow", [clause], None))
    stages["match_ms"] = (time.perf_counter() - t) * 1000

    ctx = ExecContext()
    steps = []
    for kind, texts, run in segments:
        text = "，".join(texts)
        step_start = time.perf_counter()
        step: Dict[str, Any] = {"kind": kind, "text": text}
        ctx.step, ctx.utterance, ctx.results, ctx.status, ctx.chat_done = step, text, [], None, False
        if kind == "fast":
            logs = run.run(ctx)
            step["match"] = {"confidence": run.confidence, "note": run.note}
            step["ms"] = (time.perf_counter() - step_start) * 1000
            for log in logs:
                print(f"⚡ [Fast Path | {step['ms']:.2f}ms] 「{text}」 -> {log}")
        else:
            print(f"🧠 [轉交 Slow Path AI 軌道] 待解析語句：「{text}」")
            try:
                logs = execute_slow_path_llm(text, step, ctx)
            except LLMError as e:
                logs = _recover_slow_segment(texts, matcher, ctx, e)
            step["ms"] = (time.perf_counter() - step_start) * 1000
            print(f"🤖 [Slow Path | {step['ms']:.0f}ms，其中 LLM {step.get('llm_ms', 0):.0f}ms]")
            for log in logs:
                print(f"   {log}")
        step["status"] = _step_status(ctx)
        step["logs"] = logs
        steps.append(step)
    ctx.step = None

    total_ms = (time.perf_counter() - start_time) * 1000
    for reply in ctx.replies:
        print(f"💬 回覆：{reply}")
    print(f"⏱️ 階段：normalize {stages['normalize_ms']:.2f}ms｜split {stages['split_ms']:.2f}ms｜match {stages['match_ms']:.2f}ms")
    print(f"⏱️ 全部完成：{total_ms:.0f}ms（{len(steps)} 步）")
    return {"stages": stages, "steps": steps, "replies": ctx.replies, "total_ms": total_ms}


if __name__ == "__main__":
    if len(sys.argv) > 1:  # 直接送入文字，模擬收到 STT 輸出
        process_voice_command(" ".join(sys.argv[1:]))
    else:
        warm = warmup_llm()
        if warm is not None:
            print(f"LLM 暖機完成：{warm:.2f}s")
        process_voice_command("幫我打開小算盤，然後把音量調到 40")
        process_voice_command("打開終端機，順便幫我把螢幕調暗一點，還有開瀏覽器查一下台北天氣")
```

---

## 八、 驗證計畫

### 驗證環境
* 路徑：`/Users/owo/projects/mac_ai_control`；執行前 `source .venv/bin/activate` 或直接用 `.venv/bin/python`。
* `pytest.ini`（`live` 測試預設跳過，避免離線時失敗）：

```ini
[pytest]
pythonpath = .
testpaths = tests
markers =
    live: 需要 mlx_vlm.server 已啟動並載入模型（預設 skip，跑法: pytest -m live）
addopts = -m "not live"
```

### A. 安裝與啟動驗收（手動，第五章 4 的四個提示詞）

| # | 項目 | 指令 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| A1 | server 健康 | `curl -s 127.0.0.1:8080/health` | `status=="healthy"`、`loaded_tool_parser=="qwen3_coder"` 且 `apc_enabled==true` | 看 server log；`apc_enabled` 為 false 代表啟動時漏了 `APC_ENABLED=1`；parser 不是 `qwen3_coder` 代表 chat template 或 mlx-vlm 版本不符，升級 `mlx-vlm` 後重試 |
| A2 | 模型可載入生成 | `mlx_vlm.generate` 純文字 | 輸出非空繁體中文、無 `<think>` | 確認 `AI_models/` 檔案完整（4bit 約 1.5 GB）、重新 `snapshot_download` |
| A3 | server 純文字 chat | 提示詞 ③ | `finish_reason=="stop"`、`content` 非空 | 確認請求帶 `enable_thinking:false`、`MODEL_ID` 與 `--model` 一致 |
| A4 | 工具調用 | 提示詞 ④ | `finish_reason=="tool_calls"`、name=`set_system_volume`、`level==50` | 同 A1；以 `temperature 0.1` 重試 3 次仍失敗視為不通過 |

### B. 自動化離線測試（不需模型、不改系統設定）
```bash
.venv/bin/python -m pytest -q
```
**通過標準**：全數通過（實測 **349 passed**，36 個 live 測試被 deselect，約 1.7 s），結束碼 0。測試檔：`test_router_offline.py`（原有，下方附原始碼）、`test_normalize_offline.py`（正規化 58 組）、`test_fast_offline.py`（Fast 正例 95 句、反例 45 句、歧義與候選）、`test_multistep_offline.py`（執行結果回報、LLM 保護、多段意圖、讀畫面、計時、chat call）、`test_lexicon_offline.py`（詞庫與 build_vocab）、`conftest.py`（共用 fixture）。涵蓋：

* 詞庫：別名不衝突、`normalize_utterance`、`build_vocab` 產物（誤聽詞不入 `stt_hints.txt`、Jieba 載入後 `科索` 不被拆開）
* 子句切分（含 `VS Code` 不被拆、連接詞切分）
* Fast Path 命中／未命中（音量夾限 100、靜音、深色模式開／關）；放寬後的正例與反例（否定、敘述、提問、歧義、低相似度、非 App 名詞，**絕不誤執行**）
* `validate_tool_call`：未知工具、未知參數、缺必填、enum 錯誤、型別錯誤、非物件、`mute=true` 丟棄 `level`
* Slow Path：JSON 字串／物件兩種 `arguments`、不合法呼叫被拒絕且**不執行**、純文字回覆、詞庫提示只在命中別名時注入、請求內容（`tools`＝8、`enable_thinking=false`、取樣參數）
* 整合：全 Fast 命中時**不呼叫** LLM；混合句只把未命中子句送 LLM；誤聽詞正規化後走 Fast Path
* 順序與計時：`match()` 不會提前執行；Fast／Slow 交錯時依說話順序執行；連續未命中合併成一次 LLM、被 Fast 隔開則分兩次；回傳的 `steps`／`total_ms` 結構
* 執行結果如實回報（結束碼非 0 → `❌`）、LLM 連不上／逾時／格式錯誤／HTTP 500 的保護與熔斷、Fast 候選補救、畸形 `tool_calls`
* 多段意圖：開編輯器再開專案、開 Chrome 搜尋再讀畫面（截圖／縮圖／VL 皆為假物件）、前置步驟失敗時略過依賴步驟、暫存檔一律刪除、chat call（原句直接送入、不帶 tools）
* 計時結構與 Fast 決策時間（240 個樣本 p95 < 5 ms）
* AppleScript 語法：以 `osacompile` **只編譯不執行**（含新增的深色模式開／關）

**既有測試的調整**（誠實紀錄）：亮度與搜尋已改走 Fast Path，所以 5 個原本拿『調暗螢幕』『開瀏覽器查天氣』當 Slow 範例的測試，改用 Fast 規則不涵蓋的說法（『螢幕太亮了』『幫我找一下台北天氣』）以保留原本要驗證的事（未命中的子句才送 LLM、依說話順序、計時）；`test_fast_path_miss` 移除這兩句並另加命中測試；`sh` fixture 搬進 `conftest.py` 並回傳結束碼 0；`tools` 數量 5→8。其餘斷言沒有放寬。

**失敗時**：先看是哪一類——詞庫／切分失敗是規則回歸，直接修；`validate_tool_call` 失敗代表 schema 與驗證層不一致，**不可**為了讓測試過而放寬驗證。

<details>
<summary><code>tests/test_router_offline.py</code> 完整原始碼</summary>

```python
"""不需模型、不動系統設定的離線測試：subprocess.run 與 LLM 皆以假物件取代。"""
import subprocess
import sys

import pytest

import build_vocab
import mac_voice_router as r

real_run = subprocess.run  # conftest 的 sh fixture 會換掉 subprocess.run，需要真實版本時用這個


# ---------- 詞庫 ----------
def test_lexicon_no_alias_collision():
    seen = {}
    for app in r.LEXICON["apps"]:
        for key in [app["name"], *app["aliases"], *app["misheard"]]:
            k = key.lower()
            assert seen.setdefault(k, app["name"]) == app["name"], f"{key} 同時屬於多個 App"


def test_misheard_not_in_aliases():
    assert not set(r.APP_MISHEARD) & set(r.MAC_APP_ALIASES)


@pytest.mark.parametrize("raw,expected", [
    ("打開科索", "打開Cursor"),
    ("開谷哥瀏覽器", "開Google Chrome"),
    ("打開終端機", "打開終端機"),
])
def test_normalize_utterance(raw, expected):
    assert r.normalize_utterance(raw) == expected


def test_build_vocab_outputs(tmp_path):
    build_vocab.build(build_vocab.LEXICON, tmp_path)
    hints = (tmp_path / "stt_hints.txt").read_text(encoding="utf-8").splitlines()
    assert "Cursor" in hints and "Google Chrome" in hints and "小算盤" in hints
    assert "科索" not in hints  # 誤聽詞不可餵給 STT 當提示
    userdict = (tmp_path / "userdict.txt").read_text(encoding="utf-8")
    assert "科索 100000 n" in userdict
    assert all(len(line.split(" ")) == 3 for line in userdict.splitlines())  # 無含空白的詞

    jieba = pytest.importorskip("jieba")
    tk = jieba.Tokenizer()
    tk.load_userdict(str(tmp_path / "userdict.txt"))
    assert "科索" in tk.lcut("打開科索")
    assert "谷歌瀏覽器" in tk.lcut("開谷歌瀏覽器")


# ---------- 子句切分 ----------
@pytest.mark.parametrize("text,expected", [
    ("幫我打開小算盤，然後把音量調到 40", ["幫我打開小算盤", "把音量調到 40"]),
    ("打開 VS Code", ["打開 VS Code"]),
    ("開終端機順便把螢幕調暗一點還有開瀏覽器查天氣", ["開終端機", "把螢幕調暗一點", "開瀏覽器查天氣"]),
])
def test_split_clauses(text, expected):
    assert r.FastPathMatcher().split_clauses(text) == expected


# ---------- Fast Path ----------
@pytest.mark.parametrize("clause,expected", [
    ("幫我打開小算盤", [["open", "-a", "Calculator"]]),
    ("開 Chrome", [["open", "-a", "Google Chrome"]]),
    ("開Google Chrome", [["open", "-a", "Google Chrome"]]),
    ("開 VS Code", [["open", "-a", "Visual Studio Code"]]),
    ("關掉備忘錄", [["osascript", "-e", 'tell application "Notes" to quit']]),
    ("把音量調到 50", [["osascript", "-e", "set volume output volume 50"]]),
    ("把音量調到 150", [["osascript", "-e", "set volume output volume 100"]]),
    ("靜音", [["osascript", "-e", "set volume output muted true"]]),
    ("取消靜音", [["osascript", "-e", "set volume output muted false"]]),
])
def test_fast_path_hit(sh, clause, expected):
    assert r.FastPathMatcher().try_execute(clause) is not None
    assert sh == expected


@pytest.mark.parametrize("clause", [
    "把音量調大一點", "今天天氣如何", "",
])
def test_fast_path_miss(sh, clause):
    assert r.FastPathMatcher().try_execute(clause) is None
    assert sh == []


def test_fast_path_brightness_and_search_now_fast(sh):
    """亮度與搜尋已納入 Fast Path（原本交給 LLM）"""
    assert r.FastPathMatcher().try_execute("把螢幕調暗一點") is not None
    assert "key code 145" in sh[0][2]
    assert r.FastPathMatcher().try_execute("開瀏覽器查一下台北天氣") is not None
    assert sh[1] == ["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]


def test_fast_path_dark_mode(sh):
    assert r.FastPathMatcher().try_execute("打開深色模式")
    assert "dark mode" in sh[0][2]


# ---------- 工具參數驗證 ----------
@pytest.mark.parametrize("name,args,ok", [
    ("control_mac_app", {"app_name": "Notes", "action": "open"}, True),
    ("control_mac_app", {"app_name": "Notes", "action": "delete"}, False),   # enum
    ("control_mac_app", {"app_name": "Notes"}, False),                        # 缺必填
    ("control_mac_app", {"app_name": "Notes", "action": "open", "x": 1}, False),  # 未定義參數
    ("set_system_volume", {"level": "50"}, True),                             # 字串數字可轉 int
    ("set_system_volume", {"level": "很大"}, False),
    ("set_system_volume", {"level": True}, False),
    ("set_system_volume", {"mute": "true"}, False),                           # 非布林
    ("adjust_screen_brightness", {"direction": "down", "steps": 3}, True),
    ("adjust_screen_brightness", {"direction": "left", "steps": 3}, False),
    ("web_search_or_open_url", {"query": "  "}, False),
    ("rm_rf", {}, False),                                                     # 未知工具
    ("system_power_action", ["sleep"], False),                                # 參數非物件
])
def test_validate_tool_call(name, args, ok):
    clean, err = r.validate_tool_call(name, args)
    assert (err is None) == ok and (clean is not None) == ok


def test_validate_mute_drops_level():
    clean, _ = r.validate_tool_call("set_system_volume", {"level": 30, "mute": True})
    assert clean == {"mute": True}
    clean, _ = r.validate_tool_call("set_system_volume", {"level": 30, "mute": False})
    assert clean == {"level": 30, "mute": False}


# ---------- Slow Path（LLM 以假回應取代）----------
def _tc(name, args):
    return {"type": "function", "function": {"name": name, "arguments": args}}


def test_plan_slow_path_parses_json_string_and_dict(monkeypatch):
    msg = {"tool_calls": [
        _tc("control_mac_app", '{"action": "open", "app_name": "Cursor"}'),
        _tc("adjust_screen_brightness", {"direction": "down", "steps": 3}),
    ]}
    monkeypatch.setattr(r, "_chat", lambda messages, **kw: msg)
    calls, reply, rejected = r.plan_slow_path("x")
    assert calls == [("control_mac_app", {"action": "open", "app_name": "Cursor"}),
                     ("adjust_screen_brightness", {"direction": "down", "steps": 3})]
    assert rejected == []


def test_plan_slow_path_rejects_bad_calls(monkeypatch):
    msg = {"tool_calls": [
        _tc("rm_rf", "{}"),
        _tc("system_power_action", "{not json"),
        _tc("system_power_action", '{"action": "lock_screen"}'),
    ]}
    monkeypatch.setattr(r, "_chat", lambda messages, **kw: msg)
    calls, _, rejected = r.plan_slow_path("x")
    assert calls == [("system_power_action", {"action": "lock_screen"})]
    assert len(rejected) == 2


def test_execute_slow_path_does_not_run_rejected(monkeypatch, sh):
    monkeypatch.setattr(r, "_chat", lambda messages, **kw: {"tool_calls": [_tc("rm_rf", "{}")]})
    logs = r.execute_slow_path_llm("x")
    assert sh == [] and "已拒絕" in logs[0]


def test_execute_slow_path_plain_reply(monkeypatch, sh):
    monkeypatch.setattr(r, "_chat", lambda messages, **kw: {"content": "你好"})
    assert r.execute_slow_path_llm("x") == ["🤖 AI 回覆：你好"]
    assert sh == []


def test_hint_injected_only_when_alias_present(monkeypatch):
    seen = []
    monkeypatch.setattr(r, "_chat", lambda messages, **kw: seen.append(messages[0]["content"]) or {})
    r.plan_slow_path("開小算盤然後查天氣")
    r.plan_slow_path("查天氣")
    assert "小算盤→Calculator" in seen[0] and "備忘錄" not in seen[0]
    assert "已知詞庫" not in seen[1]


def test_chat_request_body(monkeypatch):
    """請求必須帶 tools、關閉 thinking、使用官方建議取樣參數"""
    import io, json, urllib.request
    captured = {}

    def fake_urlopen(req, timeout):
        captured.update(json.loads(req.data))
        captured["url"] = req.full_url
        return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert r._chat([{"role": "user", "content": "hi"}]) == {"content": "ok"}
    assert captured["url"].endswith("/chat/completions")
    assert captured["enable_thinking"] is False
    assert len(captured["tools"]) == 8
    assert (captured["temperature"], captured["top_k"], captured["repetition_penalty"]) == (0.1, 50, 1.1)


# ---------- 雙軌整合 ----------
def test_pipeline_all_fast_never_calls_llm(monkeypatch, sh):
    monkeypatch.setattr(r, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    r.process_voice_command("幫我打開小算盤，然後把音量調到 40")
    assert sh == [["open", "-a", "Calculator"], ["osascript", "-e", "set volume output volume 40"]]


def test_pipeline_mixed_routes_unmatched_to_llm(monkeypatch, sh):
    seen = []

    def fake_chat(messages, **kw):
        seen.append(messages[-1]["content"])
        return {"tool_calls": [_tc("adjust_screen_brightness", '{"direction":"down","steps":3}'),
                               _tc("web_search_or_open_url", '{"query":"台北天氣"}')]}

    monkeypatch.setattr(r, "_chat", fake_chat)
    r.process_voice_command("打開終端機，順便螢幕太亮了，還有幫我找一下台北天氣")
    assert sh[0] == ["open", "-a", "Terminal"]
    assert seen == ["螢幕太亮了，幫我找一下台北天氣"]
    assert sh[2] == ["open", "https://www.google.com/search?q=%E5%8F%B0%E5%8C%97%E5%A4%A9%E6%B0%A3"]


def test_pipeline_misheard_goes_fast_after_normalize(monkeypatch, sh):
    monkeypatch.setattr(r, "_chat", lambda *a, **k: pytest.fail("不應呼叫 LLM"))
    r.process_voice_command("打開科索")
    assert sh == [["open", "-a", "Cursor"]]


# ---------- 依說話順序執行與計時 ----------
def _scripted_chat(replies):
    """依 user 訊息內容回傳預先寫好的 tool_calls，並記錄每次送進 LLM 的語句"""
    seen = []

    def fake(messages, **kw):
        text = messages[-1]["content"]
        seen.append(text)
        return {"tool_calls": [_tc(n, a) for n, a in replies[text]]}

    return fake, seen


def test_match_does_not_execute(sh):
    run = r.FastPathMatcher().match("開 Chrome")
    assert sh == []
    run()
    assert sh == [["open", "-a", "Google Chrome"]]


def test_pipeline_keeps_spoken_order(monkeypatch, sh):
    """未命中的『開 Slack，開 Notion』在前、Fast Path 的音量在後 → 執行順序仍照說話順序"""
    fake, seen = _scripted_chat({"開 Slack，開 Notion": [
        ("control_mac_app", '{"app_name":"Slack","action":"open"}'),
        ("control_mac_app", '{"app_name":"Notion","action":"open"}'),
    ]})
    monkeypatch.setattr(r, "_chat", fake)
    r.process_voice_command("開 Slack，開 Notion，把音量調到20")
    assert sh == [["open", "-a", "Slack"], ["open", "-a", "Notion"],
                  ["osascript", "-e", "set volume output volume 20"]]
    assert seen == ["開 Slack，開 Notion"]  # 連續未命中合併成一次 LLM 呼叫


def test_pipeline_slow_fast_slow_splits_segments(monkeypatch, sh):
    fake, seen = _scripted_chat({
        "螢幕太亮了": [("adjust_screen_brightness", '{"direction":"down","steps":3}')],
        "幫我找一下台北天氣": [("web_search_or_open_url", '{"query":"台北天氣"}')],
    })
    monkeypatch.setattr(r, "_chat", fake)
    out = r.process_voice_command("螢幕太亮了，靜音，幫我找一下台北天氣")
    assert seen == ["螢幕太亮了", "幫我找一下台北天氣"]  # 被 Fast Path 的『靜音』隔開 → 兩次 LLM
    assert [c[2] if c[0] == "osascript" else c[0] for c in sh] == [
        sh[0][2], "set volume output muted true", "open"]
    assert "key code 145" in sh[0][2]
    assert [s["kind"] for s in out["steps"]] == ["slow", "fast", "slow"]


def test_pipeline_returns_timing(monkeypatch, sh):
    fake, _ = _scripted_chat({"螢幕太亮了": [("adjust_screen_brightness", '{"direction":"down","steps":3}')]})
    monkeypatch.setattr(r, "_chat", fake)
    out = r.process_voice_command("開 Chrome，螢幕太亮了")
    fast, slow = out["steps"]
    assert (fast["kind"], slow["kind"]) == ("fast", "slow")
    assert "llm_ms" not in fast and 0 <= slow["llm_ms"] <= slow["ms"]
    assert out["total_ms"] >= fast["ms"] + slow["ms"]


# ---------- AppleScript 語法（只編譯、不執行）----------
@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osacompile")
def test_applescripts_compile(tmp_path, sh):
    r.control_mac_app("Notes", "quit")
    r.set_system_volume(level=30, mute=False)
    r.adjust_screen_brightness("up", 2)
    r.system_power_action("toggle_dark_mode")
    r.system_power_action("lock_screen")
    scripts = [c[2] for c in sh if c[0] == "osascript"]
    assert len(scripts) == 6
    for i, script in enumerate(scripts):
        p = real_run(["osacompile", "-e", script, "-o", str(tmp_path / f"{i}.scpt")],
                           capture_output=True, text=True, timeout=15)
        assert p.returncode == 0, f"{script!r}: {p.stderr}"
```

</details>

### C. 實機模型測試（需 server 已啟動）
```bash
.venv/bin/python -m pytest -m live -s -q tests/test_llm_live.py
```
每個案例重複 3 次，**3 次都必須通過**；且 `rejected` 必須為空（不得出現被驗證層拒絕的呼叫）。

| # | 語句 | 通過標準 |
|---|---|---|
| C1 | 把螢幕調暗一點 | 僅 1 個呼叫：`adjust_screen_brightness`、`direction=down`、`steps` 1~16 |
| C2 | 把螢幕調到最亮 | 恰為 `adjust_screen_brightness(up, 16)` |
| C3 | 打開瀏覽器查一下台北天氣 | 僅 `web_search_or_open_url`（不得另開瀏覽器），`query` 含「台北天氣」 |
| C4 | 幫我鎖定螢幕 | 恰為 `system_power_action(lock_screen)` |
| C5 | 開深色模式 | 恰為 `system_power_action(dark_mode_on)`（打開不等於切換） |
| C6 | 把聲音關掉 | 恰為 `set_system_volume(mute=true)`（驗證層會丟掉多餘 `level`） |
| C7 | 把音量調到五十 | 恰為 `set_system_volume(level=50)` |
| C8 | 打開科索 | 恰為 `control_mac_app(Cursor, open)`（經詞庫正規化） |
| C9 | 開谷哥瀏覽器查台北天氣 | 僅 `web_search_or_open_url` |
| C10 | 幫我開 VS Code 然後關掉 Spotify | 兩個 `control_mac_app`：(Visual Studio Code, open)、(Spotify, quit) |
| C11 | 打開終端機，順便把螢幕調暗一點，還有開瀏覽器查一下台北天氣 | 恰 3 個呼叫：app 1、亮度 1、搜尋 1 |
| C12 | 今天心情不錯 | 0 個工具呼叫 |
| C13 | 把音量調大一點／調小一點 | `level` 分別落在 50~100／0~40（**只驗方向合理**，見第十章） |
| C14 | 延遲 | 暖機後單工具 10 次：中位數 < 1.5 s 且最大值 < 3.0 s（需開 APC） |
| C15 | 經完整 pipeline（subprocess 為假）：打開系統設定，把亮度調暗／開系統設定，鎖定螢幕／打開系統設定，查一下台北天氣 | 第 1 個指令恰為 `open -a System Settings`，第 2 個分別為亮度鍵 145／鎖定螢幕／Google 搜尋；共 2 個指令（現在整句都走 Fast Path） |
| C16 | 依說話順序執行與時間（pipeline，subprocess 為假）：①開 Slack，開 Notion，把音量調到20 ②把音量調到35，把音量調到五十，把音量調到60 ③把音量調大一點，靜音，幫我找一下台北天氣 | 分段形狀分別為 `SF`／`FFF`（『五十』經正規化後走 Fast）／`SFS`（兩段 LLM 夾一個 Fast）；①指令序列恰為 Slack → Notion → 音量20，②恰為 音量35 → 50 → 60，③恰為 音量 → 靜音 → 搜尋；每句 `total_ms` < 3000 |
| C-N1 | 新工具的判斷（每案 3 次全對、`rejected` 為空）：打開那個語音助手的專案／用 cursor 開 mac_ai_control 專案／幫我看看畫面上寫什麼／螢幕上有什麼／Python 是什麼／今天心情不錯／關掉深色模式 | 分別為 `open_project(mac_ai_control)`／`open_project(mac_ai_control, Cursor)`／`read_screen`／`read_screen`／`chat_reply` 或無工具／`chat_reply` 或無工具／`dark_mode_off` |
| C-N2 | 開 Chrome，順便告訴我 Python 是什麼（LLM 為真） | 指令序列恰為 `open -a Google Chrome`；形狀 Fast → Slow；`replies` 恰 1 筆、非空、不含 `s2t` 表內的簡體字 |
| C-T2 | 階段計時表（5 個情境 × 5 次） | 只輸出『首次／其餘中位』，**不設門檻**，結果見 I 組 |
| C-V1 | 讀畫面延遲基線（固定 1280×800 畫面 × 5 次） | 只記錄 `prompt_n`／prefill／decode，**不設門檻**（D9：先看實際輸出再定） |
| C-V2 | 讀畫面內容（新聞、天氣、程式碼 3 張固定畫面） | 只確認有輸出、為繁體、暫存檔已清除；**內容是否可接受由人判斷** |
| C0 | health | 同 A1 |

**失敗時**：
* 工具選錯或參數錯 → 先查是否為詞庫缺漏（補 `lexicon.json`），再考慮調整 `SYSTEM_PROMPT` 或工具 `description`；不要直接放寬測試。
* C14 超標 → 確認沒有其他程式吃滿記憶體／GPU、是否漏了 `enable_thinking:false`、是否已暖機。

<details>
<summary><code>tests/test_llm_live.py</code> 完整原始碼</summary>

```python
"""實機測試：需 mlx_vlm.server 已載入 LFM3-VL-2B-MLX-4bit。跑法: pytest -m live -s"""
import json
import shutil
import statistics
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

import mac_voice_router as r

pytestmark = pytest.mark.live
REPEAT = 3


def _health():
    try:
        with urllib.request.urlopen(f"{r.MLX_VLM_URL}/health", timeout=3) as resp:
            return json.load(resp)
    except OSError:
        return None


@pytest.fixture(scope="module", autouse=True)
def server():
    h = _health()
    if h is None:
        pytest.skip("mlx_vlm.server 未啟動")
    return h


def test_server_health(server):
    assert server["status"] == "healthy"
    assert server["loaded_tool_parser"] == "qwen3_coder"
    assert "LFM3-VL-2B-MLX-4bit" in server["loaded_model"]
    assert server["apc_enabled"] is True  # 啟動時需 APC_ENABLED=1，否則每次重算工具定義，延遲約為兩倍


def test_warmup_then_latency_budget():
    r.warmup_llm()
    t = time.perf_counter()
    r.plan_slow_path("把螢幕調暗一點")
    assert time.perf_counter() - t < 5.0


def _names(calls):
    return [n for n, _ in calls]


# (語句, 檢查函式)；語句先經 normalize_utterance，與正式流程一致
CASES = [
    ("把螢幕調暗一點", lambda c: c[0][0] == "adjust_screen_brightness" and c[0][1]["direction"] == "down" and 1 <= c[0][1]["steps"] <= 16 and len(c) == 1),
    ("把螢幕調到最亮", lambda c: c == [("adjust_screen_brightness", {"direction": "up", "steps": 16})]),
    ("打開瀏覽器查一下台北天氣", lambda c: _names(c) == ["web_search_or_open_url"] and "台北天氣" in c[0][1]["query"]),
    ("幫我鎖定螢幕", lambda c: c == [("system_power_action", {"action": "lock_screen"})]),
    ("開深色模式", lambda c: c == [("system_power_action", {"action": "dark_mode_on"})]),   # D3：打開 ≠ 切換
    ("把聲音關掉", lambda c: c == [("set_system_volume", {"mute": True})]),
    ("把音量調到五十", lambda c: c == [("set_system_volume", {"level": 50})]),
    ("打開科索", lambda c: c == [("control_mac_app", {"app_name": "Cursor", "action": "open"})]),
    ("開谷哥瀏覽器查台北天氣", lambda c: _names(c) == ["web_search_or_open_url"]),
    ("幫我開 VS Code 然後關掉 Spotify", lambda c: sorted((a["app_name"], a["action"]) for _, a in c) == [("Spotify", "quit"), ("Visual Studio Code", "open")]),
    ("打開終端機，順便把螢幕調暗一點，還有開瀏覽器查一下台北天氣",
     lambda c: _names(c).count("control_mac_app") == 1 and _names(c).count("adjust_screen_brightness") == 1 and _names(c).count("web_search_or_open_url") == 1 and len(c) == 3),
    ("今天心情不錯", lambda c: c == []),
]


@pytest.mark.parametrize("utterance,check", CASES, ids=[c[0] for c in CASES])
def test_tool_calling(utterance, check):
    text = r.normalize_utterance(utterance)
    latencies = []
    for _ in range(REPEAT):
        t = time.perf_counter()
        calls, reply, rejected = r.plan_slow_path(text)
        latencies.append(time.perf_counter() - t)
        assert rejected == [], f"出現被驗證層拒絕的呼叫：{rejected}"
        assert check(calls), f"{utterance!r} -> {calls} / {reply!r}"
    print(f"\n{utterance}: {[round(x, 2) for x in latencies]}")


@pytest.mark.parametrize("utterance,lo,hi", [("把音量調大一點", 50, 100), ("把音量調小一點", 0, 40)])
def test_relative_volume_guess(utterance, lo, hi):
    """『大/小一點』以猜測的絕對值表達（設計限制：不讀目前音量），只驗方向合理"""
    calls, _, rejected = r.plan_slow_path(utterance)
    assert rejected == [] and _names(calls) == ["set_system_volume"]
    assert lo <= calls[0][1]["level"] <= hi


@pytest.mark.parametrize("utterance,second_check", [
    ("打開系統設定，把亮度調暗", lambda c: "key code 145" in c[2]),
    ("開系統設定，鎖定螢幕", lambda c: "keystroke" in c[2]),
    ("打開系統設定，查一下台北天氣", lambda c: c[0] == "open" and "google.com/search" in c[1]),
])
def test_pipeline_open_app_then_other_action(sh, utterance, second_check):
    """真實流程：『開 App，其他動作』先被切句，各子句依序執行
    （亮度、鎖定、搜尋已納入 Fast Path；同一句整句直送 4bit 會漏掉後半，故不能繞過切句）"""
    rec = sh
    r.process_voice_command(utterance)
    assert rec[0] == ["open", "-a", "System Settings"]
    assert len(rec) == 2 and second_check(rec[1]), rec


VOL = lambda n: ["osascript", "-e", f"set volume output volume {n}"]
ORDER_CASES = [
    ("開 Slack，開 Notion，把音量調到20", "SF", [["open", "-a", "Slack"], ["open", "-a", "Notion"], VOL(20)]),
    ("把音量調到35，把音量調到五十，把音量調到60", "FFF", [VOL(35), VOL(50), VOL(60)]),  # 『五十』經正規化後走 Fast
    ("把音量調大一點，靜音，幫我找一下台北天氣", "SFS", None),  # 兩段 LLM 夾一個 Fast；逐項檢查見下
]


@pytest.mark.parametrize("utterance,shape,expected", ORDER_CASES, ids=[c[0] for c in ORDER_CASES])
def test_pipeline_spoken_order_and_time(sh, utterance, shape, expected):
    """依說話順序執行（Fast／Slow 交錯），並要求總時間在預算內（subprocess 為假，量到的是決策＋分派時間）"""
    rec = sh
    out = r.process_voice_command(utterance)
    assert "".join("F" if s["kind"] == "fast" else "S" for s in out["steps"]) == shape
    if expected is not None:
        assert rec == expected
    else:
        assert rec[0][2].startswith("set volume output volume") and rec[1] == ["osascript", "-e", "set volume output muted true"]
        assert rec[2][0] == "open" and "google.com/search" in rec[2][1]
    assert out["total_ms"] < 3000, f"{out['total_ms']:.0f}ms"


def test_latency_distribution():
    """單工具指令暖機後的延遲分布（n=10）"""
    r.warmup_llm()
    samples = []
    for _ in range(10):
        t = time.perf_counter()
        r.plan_slow_path("幫我鎖定螢幕")
        samples.append(time.perf_counter() - t)
    p50, mx = statistics.median(samples), max(samples)
    print(f"\n單工具 p50={p50:.2f}s max={mx:.2f}s")
    assert p50 < 1.5 and mx < 3.0


# ---------- C-N1：LLM 判斷新工具（每案 REPEAT 次全對、不得被驗證層拒絕）----------
NEW_TOOL_CASES = [
    ("打開那個語音助手的專案", lambda c: c == [("open_project", {"project_name": "mac_ai_control"})]),
    ("用 cursor 開 mac_ai_control 專案", lambda c: c == [("open_project", {"project_name": "mac_ai_control", "editor": "Cursor"})]),
    ("幫我看看畫面上寫什麼", lambda c: c == [("read_screen", {})]),
    ("螢幕上有什麼", lambda c: c == [("read_screen", {})]),
    ("Python 是什麼", lambda c: _names(c) in ([], ["chat_reply"])),   # 兩種都會觸發 chat call（D6）
    ("今天心情不錯", lambda c: _names(c) in ([], ["chat_reply"])),
    ("關掉深色模式", lambda c: c == [("system_power_action", {"action": "dark_mode_off"})]),
]


@pytest.mark.parametrize("utterance,check", NEW_TOOL_CASES, ids=[c[0] for c in NEW_TOOL_CASES])
def test_new_tools_selection(utterance, check):
    text = r.normalize_utterance(utterance)
    for _ in range(REPEAT):
        calls, reply, rejected = r.plan_slow_path(text)
        assert rejected == [], f"被驗證層拒絕：{rejected}"
        assert check(calls), f"{utterance!r} -> {calls} / {reply!r}"


def test_mixed_action_and_question_real_llm(sh):
    """C-N2：開 Chrome（Fast）＋ 提問（LLM → chat call）。回覆非空、不含簡體詞表內的字"""
    out = r.process_voice_command("開 Chrome，順便告訴我 Python 是什麼")
    assert sh == [["open", "-a", "Google Chrome"]]
    assert [s["kind"] for s in out["steps"]] == ["fast", "slow"]
    assert out["steps"][1]["status"] == "ok", out["steps"][1]
    assert len(out["replies"]) == 1 and out["replies"][0].strip()
    assert r.to_traditional(out["replies"][0]) == out["replies"][0]
    print(f"\n回覆：{out['replies'][0]}")


# ---------- C-T2：階段計時表（不設門檻，只輸出）----------
STAGE_SCENARIOS = [
    ("全 Fast", "幫我打開小算盤，然後把音量調到 40"),
    ("單一 LLM 工具", "把音量調大一點"),
    ("F-S-F", "開 Chrome，把音量調大一點，靜音"),
    ("LLM 多工具", "開 Slack，開 Notion，把音量調到20"),
    ("提問（規劃＋chat call）", "Python 是什麼"),
]


def _med(xs):
    return statistics.median(xs) if xs else float("nan")


def test_stage_timing_report(sh):
    """每個情境跑 5 次，『首次』與『其餘中位』分列（subprocess 為假，量到的是決策＋分派＋LLM 時間）"""
    r.warmup_llm()
    rows = []
    for label, text in STAGE_SCENARIOS:
        runs = []
        for _ in range(5):
            out = r.process_voice_command(text)
            slow = [s for s in out["steps"] if s["kind"] == "slow"]
            runs.append({
                "normalize": out["stages"]["normalize_ms"], "split": out["stages"]["split_ms"], "match": out["stages"]["match_ms"],
                "fast_exec": sum(sum(s.get("exec_ms", [])) for s in out["steps"] if s["kind"] == "fast"),
                "llm": sum(s.get("llm_ms", 0) for s in slow),
                "prefill": sum((s.get("llm") or {}).get("prefill_ms") or 0 for s in slow),
                "decode": sum((s.get("llm") or {}).get("decode_ms") or 0 for s in slow),
                "chat": sum(s.get("chat_ms", 0) for s in slow),
                "total": out["total_ms"],
            })
        rows.append((label, runs))
    keys = ["normalize", "split", "match", "fast_exec", "llm", "prefill", "decode", "chat", "total"]
    print("\n| 情境 | 次 | " + " | ".join(keys) + " |\n|---|---|" + "---|" * len(keys))
    for label, runs in rows:
        print(f"| {label} | 首次 | " + " | ".join(f"{runs[0][k]:.1f}" for k in keys) + " |")
        print(f"| {label} | 其餘中位 | " + " | ".join(f"{_med([x[k] for x in runs[1:]]):.1f}" for k in keys) + " |")
    assert all(len(runs) == 5 for _, runs in rows)


# ---------- C-V1／C-V2：讀畫面（固定畫面；門檻待 D9，只記錄不判定）----------
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    """以 PIL 產生 3 張已知內容的 1280×800 畫面：中文新聞、天氣、程式碼"""
    pytest.importorskip("PIL.ImageDraw")
    from PIL import Image, ImageDraw, ImageFont
    if not Path(FONT).exists():
        pytest.skip("找不到中文字型")
    out = tmp_path_factory.mktemp("shots")

    def shot(name, bg, bar, title, lines):
        im = Image.new("RGB", (1280, 800), bg)
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 1280, 60], fill=bar)
        d.text((24, 14), title, font=ImageFont.truetype(FONT, 30), fill=(255, 255, 255))
        y = 100
        for size, text, color in lines:
            d.text((60, y), text, font=ImageFont.truetype(FONT, size), fill=color)
            y += int(size * 1.7)
        im.save(out / f"{name}.png")

    shot("news", (255, 255, 255), (30, 90, 160), "新聞網 - 今日焦點", [
        (44, "央行宣布升息一碼 房貸族每月多負擔數百元", (20, 20, 20)),
        (30, "台北訊 中央銀行今天宣布調升政策利率0.125個百分點，", (60, 60, 60)),
        (30, "為近兩年來首次調整，專家預期對房市影響有限。", (60, 60, 60)),
        (44, "高鐵 週末加開 12 班次疏運連假人潮", (20, 20, 20)),
        (30, "台灣高鐵公司表示，連假期間南下北上各加開六班次。", (60, 60, 60)),
    ])
    shot("weather", (225, 240, 255), (40, 120, 200), "中央氣象署 - 台北市天氣", [
        (50, "台北市 今日天氣", (10, 40, 90)),
        (44, "多雲時晴  氣溫 22 ~ 28 度", (20, 20, 20)),
        (44, "降雨機率 20%   濕度 65%", (20, 20, 20)),
        (36, "明日：陰短暫雨  氣溫 21 ~ 25 度", (60, 60, 60)),
        (36, "紫外線指數：中量級", (60, 60, 60)),
    ])
    shot("code", (30, 30, 35), (50, 50, 60), "mac_voice_router.py - Visual Studio Code", [
        (30, "def set_system_volume(level, mute):", (220, 220, 100)),
        (30, "    clamped = max(0, min(100, int(level)))", (200, 200, 200)),
        (30, "    subprocess.run([\"osascript\", \"-e\",", (200, 200, 200)),
        (30, "        f\"set volume output volume {clamped}\"])", (200, 200, 200)),
        (30, "    return f\"音量設為 {clamped}%\"", (150, 220, 150)),
    ])
    return out


@pytest.fixture
def fake_screen(monkeypatch):
    """screencapture 以固定畫面取代（其餘如 sips 照常真的執行），其他指令照常記錄"""
    state = {"image": None}
    real = subprocess.run

    def run(cmd, **kw):
        if cmd[0] == "screencapture":
            shutil.copy(state["image"], cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    return state


def test_read_screen_latency_baseline(shots, fake_screen, tmp_path):
    """C-V1：同一張畫面 5 次，記錄 prompt_n／prefill／decode／整體（不判定通過與否，D9）"""
    fake_screen["image"] = shots / "news.png"
    r.warmup_llm()
    rows = []
    for _ in range(5):
        ctx = r.ExecContext()
        ctx.step = {}
        res = r.read_screen(ctx)
        assert res.startswith("✅"), res
        vl = ctx.step["vl"]
        rows.append((ctx.step["capture_ms"], ctx.step["resize_ms"], ctx.step["vl_ms"], vl["prompt_n"], vl["prefill_ms"], vl["decode_ms"], vl["predicted_n"]))
    print("\n| 次 | capture ms | resize ms | vl ms | prompt_n | prefill ms | decode ms | predicted_n |\n|---|---|---|---|---|---|---|---|")
    for i, row in enumerate(rows, 1):
        print(f"| {i} | " + " | ".join(f"{v:.0f}" for v in row) + " |")
    print(f"vl_ms 中位 {statistics.median(x[2] for x in rows):.0f}，最大 {max(x[2] for x in rows):.0f}")
    assert len(rows) == 5


@pytest.mark.parametrize("name", ["news", "weather", "code"])
def test_read_screen_content_for_review(shots, fake_screen, name):
    """C-V2：印出模型對固定畫面的輸出，由人工判斷（D9）。這裡只確認有輸出、為繁體、暫存檔已清除"""
    fake_screen["image"] = shots / f"{name}.png"
    ctx = r.ExecContext()
    ctx.step = {}
    res = r.read_screen(ctx)
    print(f"\n=== {name} ===\n{res}")
    assert res.startswith("✅") and ctx.replies
    assert r.to_traditional(ctx.replies[0]) == ctx.replies[0]
    assert list(Path(tempfile.gettempdir()).glob("macvoice_*")) == []
```

</details>

### D. 實測結果（2026-11-02，M1 / 16GB，皆開 APC）

| 項目 | 4bit（採用） | 8bit（比較） |
|---|---|---|
| B 組離線測試 | **52 passed**（0.7 s） | 48 passed（當時尚無順序測試） |
| C 組實機測試 | **23 passed**（52.3 s）；12 案例 × 3 次全過，`rejected` 皆為空；含 C15、C16 | 17 passed（63.1 s；當時尚無 C15、C16） |
| 單工具延遲（C14，n=10） | p50 **0.65 s**、max **0.72 s** | p50 0.87 s、max 0.92 s |
| 各案例延遲（3 次，秒） | 調暗 0.9｜最亮 0.9（首次 2.37）｜查天氣 0.75（首次 2.24）｜鎖定 0.67（首次 1.97）｜深色 0.7（首次 2.1）｜靜音 0.65｜音量 50 0.62｜科索 0.86｜谷哥查天氣 0.76（首次 2.08）｜雙 App 1.6（首次 3.03）｜三工具 2.17（首次 3.51）｜閒聊 0.8 | 調暗 1.18｜最亮 1.18 ~ 1.42｜查天氣 0.98｜鎖定 0.90｜深色 0.90｜靜音 0.83 ~ 1.0｜音量 50 0.81｜科索 1.12｜谷哥查天氣 0.97（首次 2.23）｜雙 App 2.19（首次 3.53）｜三工具 2.96（首次 4.28）｜閒聊 1.04 |
| 暖機前第一個請求 | 未量測 | 約 5 s（未開 APC） |
| 端到端流程（subprocess 以假物件取代、LLM 為真） | 未重跑 | 「幫我打開小算盤，然後把音量調到 40」全走 Fast Path；「打開終端機，順便…還有開谷哥瀏覽器查一下台北天氣」→ 終端機走 Fast、其餘（未開 APC）3.35 s 由 LLM 產生亮度與搜尋兩個呼叫，「谷哥」已正規化為 Google Chrome |
| AppleScript 語法 | 6 段腳本 `osacompile` 全數通過（僅編譯，未執行；與模型無關） | 同 |

### E. 人工／介面驗收（會真的改系統，需你在場）

| # | 操作 | 通過標準 | 2026-11-03 結果 |
|---|---|---|---|
| E1 | `python mac_voice_router.py "開vscode，開mac_ai_control專案"` | VS Code 開啟該專案資料夾 | 兩個指令結束碼 0（`open -a "Visual Studio Code"`、`open -a "Visual Studio Code" <專案路徑>`），共 180 ms；**視窗內容未目視確認** |
| E2 | `python mac_voice_router.py "開 Safari，搜尋台北天氣，辨識這頁"` | 瀏覽器顯示搜尋結果；`replies` 符合畫面 | ✅ 搜尋已開啟，讀畫面回覆「搜尋關鍵字：台北天氣…」，共 8.7 s（其中讀畫面 8.4 s）；**這台沒有安裝 Chrome**，所以 `開 google` 實測是如實回報 `❌ Unable to find application named 'Google Chrome'`，後面的搜尋與讀畫面標為 `skipped`，暫存目錄無殘留 |
| E3 | 首次執行讀畫面 | 跳出『螢幕錄製』授權，授權後成功 | 授權前 `screencapture` 回傳 `could not create image from display`，流程回報 `❌ 截圖失敗（…請確認已授權螢幕錄製）`；你在系統設定授權 VS Code 後截圖成功（2880×1800，縮圖後 1280×800） |
| E4 | LLM 連不上時執行『開小算盤，幫我整理一下桌面，把音量調到<原音量>』 | 小算盤開啟、音量不變、有『AI 服務無法使用』訊息、程式沒崩潰 | ✅ 狀態 `ok／llm_unavailable／ok`，278 ms；測後已關閉小算盤 |
| E5 | `開 NoSuchAppXYZ` | 回報 `❌`，不是 `✅` | ✅ `❌ 開啟失敗：NoSuchAppXYZ（Unable to find application named 'NoSuchAppXYZ'）` |
| E6 | 『打開深色模式』連做兩次（第二次已是深色）再『關閉深色模式』還原 | 第二次後仍為深色；最後還原 | ✅ 查詢結果依序 false → true → true → false（已還原為淺色） |
| E7 | 首次執行亮度／鎖定螢幕 | 跳出『輔助使用』授權提示，授權後執行成功 | **未驗證**（會改螢幕亮度或鎖定畫面） |
| E8 | 說『打開科索』（有安裝 Cursor） | Cursor 開啟 | **未驗證**（這台沒有安裝 Cursor） |
| E9 | 長時間待命後第一句 Slow Path 指令 | 不比暖機後慢超過 2 秒 | **未驗證** |
| E10 | 不帶參數執行 `python mac_voice_router.py`（demo） | 輸出含 `LLM 暖機完成`；開啟小算盤與終端機、音量 40、螢幕變暗、瀏覽器搜尋 | **未驗證**（會改音量與亮度） |

### F. 本次未驗證／不在範圍
* 不含實際 STT 麥克風端到端（只驗證 STT 輸出文字之後的流程）。
* `macos-stt` 的 `POST /vocabulary` 串接、`SFSpeechRecognizer.contextualStrings` 對辨識率的實際提升。
* E 組中標示『未驗證』的項目（亮度與鎖定螢幕的輔助使用授權、Cursor、長時間待命、demo）；睡眠（`pmset sleepnow`）未實測。
* Chrome 的載入狀態輪詢（`loading of active tab`）未實測（這台沒有安裝 Chrome；離線測試以假物件驗證流程與逾時處理）；Safari 只有固定等待。
* `seed` 誤聽詞（`VS扣`、`因量`…）在真實 STT 上是否真的會出現；`logs/normalize.jsonl` 是用來之後回收真實誤聽的。
* server 常駐記憶體用量、長時間穩定性、並行請求。
* 5bit／6bit／bf16 量化版；4bit 與 8bit 的比較僅限 C、G 組語句，樣本小（G 組 20 句、單次），不代表整體準確度。
* 影片輸入；圖片輸入只用於『讀畫面』（單張、縮到 1280 寬）。

### G. 4bit 與 8bit 額外比對與「雙動作漏呼叫」處理（不在 C 組內）

**G1. 20 句比對**（無預期答案、單次，整句直接送 `plan_slow_path`）：20 句中 15 句工具呼叫完全相同。差異：

| 語句 | 4bit | 8bit |
|---|---|---|
| 開計算機然後靜音 | 只開 Calculator，漏掉靜音 | 兩個動作都有 |
| 幫我開音樂然後把音量調到30 | 只開 Music，漏掉音量 | 兩個動作都有 |
| 打開系統設定然後把亮度調暗 | 只開 System Settings，漏掉亮度 | 同左（也漏） |
| 螢幕亮一點 | `steps=1` | `steps=3` |
| 把音量調大一點 | `level=70` | `level=60` |
| 幫我訂明天的機票 | 搜尋「明天的機票訂閱 航班」 | 搜尋「明天機票訂閱 航班」 |

**G2. 校正測試方式**：「然後」「順便」等連接詞在真實流程中會先被 `split_clauses` 拆掉，Fast Path 命不中的子句是以「，」合併後才送 LLM，所以 G1 的「然後」形式不代表實際輸入。改用「，」形式重測（4bit 與 8bit 皆用原 `SYSTEM_PROMPT`）：

| 語句（「，」形式） | 4bit | 8bit |
|---|---|---|
| 開計算機，靜音 | ✅ 2 個呼叫 | ✅ 2 個呼叫 |
| 幫我開音樂，把音量調到30 | ✅ 2 個呼叫 | ✅ 2 個呼叫 |
| 開 LINE 跟 Discord，把螢幕調暗 | ✅ 3 個呼叫 | ✅ 3 個呼叫 |
| 打開系統設定，把亮度調暗 | ❌ 只開 System Settings | ✅ 2 個呼叫 |

→ 真實輸入形式下，兩者只差最後一句；「然後」形式的漏呼叫多半是測試輸入造成，不是實際風險。

**G3. 方案 (c) 試驗：微調 `SYSTEM_PROMPT` 的多動作語句（4bit，結論：不加，維持原文）**

評分集：DEV 12 句（雙／三動作，「，」連接）、GUARD 6 句（單動作，不可多呼叫或少呼叫）、HOLD 6 句（未參與調整，專測「系統設定」類）。通過＝工具名稱集合與預期完全一致。**這些是整句直送 LLM 的分數**（繞過切句與 Fast Path，屬壓力測試）：

| 版本 | DEV | GUARD | HOLD | 備註 |
|---|---|---|---|---|
| V0 無此句（現行程式） | 11/12 | 6/6 | 4/6 | 基準 |
| V1 「必須逐一呼叫…不可遺漏後面的動作」 | 11/12 | 5/6 | 5/6 | GUARD 退步：「開 Google Chrome 查一下天氣」只剩開 App |
| V3 「工具呼叫數 = 動作數，並列舉獨立動作類別」 | 11/12 | 5/6 | － | 同 V1 退步 |
| V4 「不同類別動作各自呼叫」 | 11/12 | 5/6 | － | 同 V1 退步 |
| V5 「【重要】有幾個動作就呼叫幾次」 | 11/12 | 6/6 | － | 與 V0 同分；放到 prompt 最前面反而退步（DEV 10/12） |
| D1 在 `control_mac_app` 描述加「只負責開關 App，其後動作另外呼叫」 | 11/12 | 6/6 | 4/6 | 與 V0 同分 |
| D2 prompt 加「『系統設定』只是 App 名稱…」 | 10/12 | 5/6 | 3/6 | 更差 |

所有版本唯一共同失敗的 DEV 句是「打開系統設定，把亮度調暗」，HOLD 的失敗也集中在「系統設定」開頭的句子（模型會丟掉其中一個動作）。**任何措辭都沒有讓它通過**，且 V1／V3／V4 會造成新的退步，因此 `SYSTEM_PROMPT` 維持原文。

**G4. 真實流程驗證（採用的結論）**：「系統設定」是詞庫別名，真實流程會先切句，`打開系統設定` 由 Fast Path 處理，LLM 只收到後半。以完整 pipeline（LLM 為真、subprocess 為假）實測 11 句，**全部正確**：

| 語句 | 實際指令序列 |
|---|---|
| 打開系統設定，把亮度調暗 | 開 System Settings → 亮度 |
| 打開系統設定，把亮度調暗，鎖定螢幕 | 開 System Settings → 亮度 → 鎖定 |
| 開系統設定，鎖定螢幕 | 開 System Settings → 鎖定 |
| 打開系統設定，查一下台北天氣 | 開 System Settings → 搜尋 |
| 打開系統設定，把音量調到20 | 開 System Settings → 音量 |
| 開 Safari，把螢幕調亮 | 開 Safari → 亮度 |
| 開 Slack，把螢幕調暗一點 | 開 Slack → 亮度 |
| 開 Notes，把亮度調到最亮 | 開 Notes → 亮度 |
| 開 Finder，把螢幕調暗 | 開 Finder → 亮度 |
| 關掉 Slack，把螢幕調暗一點 | 關閉 App → 亮度 |
| 開 Slack，開 Notion，把音量調到20 | **音量 → 開 Slack → 開 Notion**（舊版順序；現版為 Slack → Notion → 音量，見 H） |

* **執行順序**：此表是舊版（Fast 先於 Slow）的結果，最後一句因此為「音量 → Slack → Notion」；現已改為依說話順序執行，見第八章 H。

### H. 依說話順序執行：真實流程耗時（4bit，開 APC，M1 / 16GB，2026-11-03）

**H1. 順序與決策／分派時間**（LLM 為真、subprocess 為假；F＝Fast 段、S＝Slow 段；每句 5 次，首次與其餘分列，單位 ms）

| 語句 | 順序 | 形狀 | 首次 | 其餘中位 | 其餘最小 ~ 最大 |
|---|---|---|---|---|---|
| 幫我打開小算盤，然後把音量調到 40 | ✓ | FF | 1 | 0 | 0 ~ 0 |
| 打開系統設定，把亮度調暗 | ✓ | FS | 1014 | 896 | 889 ~ 910 |
| 把音量調到35，把音量調到五十，把音量調到60 | ✓ | FSF | 745 | 624 | 621 ~ 632 |
| 開谷哥瀏覽器查台北天氣 | ✓ | S | 858 | 750 | 743 ~ 752 |
| 開 Slack，開 Notion，把音量調到20 | ✓ | SF | 1785 | 1576 | 1571 ~ 1629 |
| 打開終端機，順便幫我把螢幕調暗一點，還有開瀏覽器查一下台北天氣 | ✓ | FS | 2898 | 1589 | 1530 ~ 1608 |
| 把螢幕調暗一點，靜音，查一下台北天氣 | ✓ | SFS（2 次 LLM） | 1864 | 1651 | 1639 ~ 1693 |

* 7 句 × 5 次，執行的指令序列全部等於說話順序。
* 單次 LLM 呼叫約 0.6 ~ 0.9 s（輸出越多越慢）；「SFS」兩次 LLM 合計約 1.65 s。
* 「首次」較慢是 prefix cache 尚未熱（最差 2.9 s）。

**H2. 真的執行**（LLM 為真、`open`／`osascript` 為真；會開小算盤並改系統音量，測完已還原音量與靜音狀態並關掉我開的小算盤；每句 3 次）

| 語句 | 形狀 | 最終系統狀態 | 總計（首次／其餘中位） | 單一指令實際耗時 |
|---|---|---|---|---|
| 打開小算盤，把音量調到35 | FF | 音量 35 ✓ | 310／257 ms | `open -a` 80 ms、`osascript` 179 ms |
| 把音量調到35，把音量調到五十，把音量調到60 | FSF | 音量 **60** ✓ | 1373／1220 ms | 175、184、193 ms |
| 打開小算盤，把音量調到30，把聲音關掉 | FFS | 音量 30 且靜音 ✓ | 1377／1183 ms | 108、183、192 ms |

* **第二列可直接證明順序**：若 Fast 先於 Slow 執行，最終音量會是 50；實測為 60。
* **每個 `osascript` 指令實測約 180 ~ 190 ms**（程序啟動成本），`open -a` 約 80 ~ 110 ms。所以 Fast Path 的「毫秒級」只指比對與決策；真的執行約 0.1 ~ 0.2 s／指令，多指令會累加。
* 端到端粗估：總時間 ≈ 各 LLM 段（約 0.6 ~ 0.9 s）＋ 每個指令 0.1 ~ 0.2 s。

**H3. 未涵蓋**：真實執行僅限開 App 與音量／靜音；亮度、鎖定螢幕、深色模式、睡眠、瀏覽器搜尋與「關閉 App」的真實執行時間未量（涉及輔助使用／自動化授權提示或會干擾桌面）。

### I. 多段意圖與 Fast Path 強化實測（2026-11-03，4bit，開 APC，M1 / 16GB）

**I1. 測試總覽**

| 項目 | 結果 |
|---|---|
| B 組離線測試 | **349 passed**（約 1.7 s） |
| C 組實機測試 | **36 passed**（三輪各 152 s、131 s、133 s）；含 C-N1（7 案例 × 3 次）、C-N2、C-T2、C-V1、C-V2 |
| E 組人工驗收 | E1～E6 已做（見 E）；E7～E10 未驗證 |

**I2. Fast Path 放寬前後**（第一章 39 句口語，正規化 → 切句 → 比對，subprocess 為假）

| | 命中 Fast | 說明 |
|---|---|---|
| 舊版 | 12 / 39 | 語尾助詞、疊用前綴、語序、拼錯、中文數字、缺規則都掉到 LLM |
| 新版 | 36 / 39 | 另外 3 句（`不要打開 Chrome`、`我昨天打開 Chrome`、`Chrome 是什麼`）被安全守門擋下，交給 LLM；與預期一致 |

離線測試另有 95 句正例（指令序列逐一等於預期、且不呼叫 LLM）與 45 句反例（否定、敘述、提問、歧義、低相似度、非 App 名詞、睡眠，**沒有任何誤執行**）。正例／反例測試做過變異驗證（故意改錯兩處預期，測試如預期失敗），確認不是空轉。

**I3. Fast 決策時間**（normalize＋split＋match，12 句 × 20 次 = 240 個樣本）：p50 **0.028 ms**、p95 **0.141 ms**、max 2.4 ms。此外 `osascript` 約 180 ~ 200 ms、`open -a` 約 80 ~ 110 ms 是程序啟動成本（E4 實測 `開小算盤` 59 ms、`把音量調到0` 201 ms），與規則無關。

**I4. 各案例規劃延遲**（C1～C12，`plan_slow_path`，8 個工具，3 次；秒，首次／其餘）

| 案例 | 首次 | 其餘 |
|---|---|---|
| 把螢幕調暗一點 | 0.87 | 0.88、0.90 |
| 把螢幕調到最亮 | 1.01 | 0.92、0.90 |
| 打開瀏覽器查一下台北天氣 | 0.89 | 0.76、0.82 |
| 幫我鎖定螢幕 | 0.81 | 0.70、0.71 |
| 開深色模式（現為 `dark_mode_on`） | 0.77 | 0.66、0.66 |
| 把聲音關掉 | 0.73 | 0.63、0.63 |
| 把音量調到五十 | 0.72 | 0.68、0.62 |
| 打開科索 | 1.01 | 0.85、0.86 |
| 開谷哥瀏覽器查台北天氣 | 0.85 | 0.75、0.87 |
| 幫我開 VS Code 然後關掉 Spotify | 2.08 | 1.85、1.84 |
| 打開終端機，順便把螢幕調暗一點，還有開瀏覽器查一下台北天氣 | 2.66 | 2.49、2.19 |
| 今天心情不錯 | 0.99 | 0.91、0.93 |

單工具延遲（C14，n=10）：p50 **0.66 s**、max **0.74 s**（工具由 5 個增加到 8 個，與 11-02 的 0.65／0.72 s 相同）。12 案例 × 3 次全過，`rejected` 皆為空。

**I5. 階段計時表**（C-T2：5 個情境 × 5 次；subprocess 為假，所以 `fast_exec` 約 0；單位 ms）

| 情境 | 次 | normalize | split | match | llm | prefill | decode | chat | total |
|---|---|---|---|---|---|---|---|---|---|
| 全 Fast（開小算盤、音量 40） | 首次 | 0.1 | 0.0 | 0.3 | – | – | – | – | 0.5 |
| | 其餘中位 | 0.1 | 0.0 | 0.2 | – | – | – | – | 0.3 |
| 單一 LLM 工具（把音量調大一點） | 首次 | 0.0 | 0.0 | 0.0 | 704.9 | 32.7 | 461.8 | – | 705.1 |
| | 其餘中位 | 0.1 | 0.0 | 0.1 | 611.4 | 31.9 | 457.3 | – | 611.6 |
| F-S-F（開 Chrome，音量調大一點，靜音） | 首次 | 0.0 | 0.0 | 0.1 | 616.9 | 30.8 | 464.6 | – | 617.1 |
| | 其餘中位 | 0.1 | 0.0 | 0.3 | 607.2 | 31.8 | 457.1 | – | 607.8 |
| LLM 多工具（開 Slack，開 Notion，音量 20） | 首次 | 0.1 | 0.0 | 0.9 | 1718.7 | 32.9 | 1470.2 | – | 1719.7 |
| | 其餘中位 | 0.1 | 0.0 | 1.1 | 1574.6 | 31.8 | 1425.0 | – | 1575.9 |
| 提問（Python 是什麼：規劃＋chat call） | 首次 | 0.1 | 0.0 | 0.0 | 1722.9 | 32.6 | 1480.8 | 1075.9 | 2799.0 |
| | 其餘中位 | 0.1 | 0.0 | 0.0 | 1681.9 | 32.8 | 1536.0 | 997.0 | 2685.9 |

* **瓶頸是 decode**：prefill 因 prefix cache 命中只剩約 32 ms（`cache_n` 890）；單工具 decode 約 460 ms、三個輸出 token 較多的情境約 1.4 ~ 1.5 s。`llm` 與 `prefill + decode` 的差約 110 ~ 120 ms 是 HTTP、JSON 與排程的額外成本（`overhead_ms`）。
* **提問要付兩次 LLM**：規劃呼叫（約 1.7 s，因為模型直接寫了一段回答才結束）＋ chat call（約 1.0 s）＝ 約 2.7 ~ 2.8 s。這是決策 D6（只回文字也觸發 chat call）的代價。
* 正規化、切句、比對合計 < 1.5 ms，不是瓶頸。

**I6. 讀畫面基線**（C-V1：固定 1280×800 合成畫面 5 次；以及真實全螢幕）

| 情境 | capture | resize | VL 步驟 | prompt_n | prefill | decode | 輸出 tokens |
|---|---|---|---|---|---|---|---|
| 合成畫面，第 1 次（未快取） | 2 ms | 26 ms | **6,541 ms** | 1,077 | 3,541 ms | 2,714 ms | 105 |
| 合成畫面，第 2 ~ 5 次（prefix cache 命中） | 1 ms | 23 ms | 約 4,060 ms | 1 | 約 1,120 ms | 約 2,670 ms | 105 |
| 真實全螢幕 2880×1800 → 1280×800，第 1 次 | 210 ms | 53 ms | 7,001 ms | 1,077 | 3,715 ms | 2,876 ms | 99 |
| 真實全螢幕，第 2 次（不同畫面內容） | 234 ms | 61 ms | 7,546 ms | 1,077 | 3,596 ms | 3,472 ms | 130 |

* 圖片約 1,077 tokens（1280×800 ÷ 32×32 ≈ 1,000 ＋ 文字），prefill 約 290 ~ 300 tok/s；**每次真實畫面約 7 ~ 7.5 s**，加上等網頁（Chrome 至少 1 s、其他瀏覽器固定 2.5 s），使用者說完「辨識這頁」到拿到回覆**推估**約 8 ~ 10 s（E2 用 Safari 實測整個讀畫面步驟 8.4 s）。
* **門檻尚未訂定（D9）**：以上只是基線，請看過輸出後決定可接受的延遲與品質。

**C-V2 內容**（固定合成畫面；原文是我畫的，右為模型輸出，**是否可接受由你判斷**）

| 畫面 | 畫面上的字 | 模型輸出（重點） |
|---|---|---|
| 新聞 | 央行宣布升息一碼…、利率 0.125 個百分點、高鐵週末加開 12 班次、連假南下北上各加開六班次 | 4 點逐項複述，數字（0.125、12、六）皆正確 |
| 天氣 | 台北市今日天氣、多雲時晴 22 ~ 28 度、降雨機率 20%、濕度 65%、明日陰短暫雨 21 ~ 25 度、紫外線中量級 | 4 點複述，數字皆正確 |
| 程式碼 | `set_system_volume(level, mute)`、`clamped`、`subprocess.run`、`osascript` | 說明函式名稱、兩個參數、夾限 0 ~ 100、用子程序設定音量、回傳字串；沒有逐行抄寫 |

真實螢幕的輸出沒有對照畫面核對。

**I7. 工具描述實驗**（為了讓模型選對新工具，單次、temperature 0.1；`system_power_action` 與 `read_screen`）

| 版本 | 深色模式（7 句） | 讀畫面（6 句） | 說明 |
|---|---|---|---|
| 基準（只在描述寫「打開用 dark_mode_on」） | 4 / 7 | 3 / 6 | 「開深色模式」「打開暗黑模式」「開啟夜間模式」仍選 `toggle_dark_mode` |
| v1（把規則寫進參數描述） | 4 / 7 | 2 / 6 | 沒有改善，讀畫面反而退步 |
| v2（採用：enum 把 `dark_mode_on／off` 排前面、`toggle` 排最後並註明『只在明說切換時』；`read_screen` 描述列出『畫面／螢幕／這頁／網頁上有什麼、寫什麼、在講什麼』） | **7 / 7** | 4 / 6 | 剩下 2 句：「這個網頁在講什麼」被當成搜尋、「我在看什麼」太模糊 |

採用 v2 後 C 組全部重跑（I1）。這是小樣本實驗，不代表整體準確度。

**I8. 暖機**：舊的 `warmup_llm()` 只送 `你好`，沒有 system prompt，第一個真實請求 `cache_n` 只有 99、prefill 約 1.9 s（實測該請求 3.7 s）。現在暖機帶上與正式請求相同的 system prompt。

---

## 九、 macOS 實戰調校與權限注意事項

1. **輔助使用權限（Accessibility Permissions）**：
   * 當 Python 透過 `osascript` 呼叫 `System Events`（例如調整螢幕亮度、鎖定螢幕、切換深色模式）時，macOS 會跳出權限要求。
   * 請前往 **「系統設定」 > 「隱私權與安全性」 > 「輔助使用」** 與 **「自動化」**，將你執行腳本的終端機（`Terminal.app`、`iTerm.app` 或 `Visual Studio Code`）打勾授權。
2. **macOS 原生語音辨識（STT）的繁體中文斷句優化**：
   * 若使用 macOS 原生 `SFSpeechRecognizer`（可透過 PyObjC 或 Swift CLI 封裝呼叫），請將 Locale 設為 **`zh-TW`**，並開啟 `addsPunctuation = true`（macOS Ventura 以上支援自動加入標點符號），這能大幅提升我們第一階段 `split_clauses` 子句切分的準確度。
   * 針對常講錯的軟體名稱（如把「開啟 Cursor」聽成「開啟科索」），把 `vocab/stt_hints.txt`（由詞庫產生）塞入 `SFSpeechRecognitionRequest` 的 `contextualStrings`，提高特定詞彙的辨識權重；仍聽錯的再用 `misheard` 做事後替換。
3. **模型常駐與暖機（避免冷啟動延遲）**：
   * `mlx_vlm.server` 以常駐程序執行，模型只載入一次。實測**第一個請求明顯較慢**（約 5 s，之後穩定），因此服務啟動後先呼叫 `warmup_llm()`。
   * 務必以 `APC_ENABLED=1` 啟動：未開時每次請求都重算約 620 tokens 的工具定義（`cached_tokens` 恆為 0）。開啟後相同前綴（system＋工具定義）直接命中快取。
   * 已知細節：詞庫提示 `build_hint_lines` 目前接在 system prompt 內、位於工具定義之前，提示內容不同時前綴就不同，該次請求會較慢（實測「開谷哥瀏覽器查台北天氣」首次 2.2 s，之後 1.0 s）。若要再優化，可把提示移到 user 訊息；**尚未實作也未驗證**。
4. **螢幕錄製與自動化權限（讀畫面）**：
   * `screencapture` 需要「**螢幕錄製**」授權：**系統設定 > 隱私權與安全性 > 螢幕錄製**，打勾執行腳本的程式（本專案實測是 `Visual Studio Code`）。授權前會得到 `could not create image from display`，`read_screen` 會回報 `❌ 截圖失敗（…請確認已授權螢幕錄製）`；授權後需要重新啟動該程式才會對新程序生效。
   * 等待 Chrome 網頁載入用 `osascript` 問 `loading of active tab`，第一次會跳出「自動化」授權；授權對話框會讓指令卡住，所以該指令設有 5 s 逾時（`screencapture`／`sips` 為 15 s），逾時就直接截圖。
   * 暫存截圖放在系統暫存目錄的 `macvoice_*` 資料夾，正常結束或 VL 失敗都會刪除；程序被強制終止（SIGKILL）時 `finally` 來不及執行，可能殘留，手動刪除即可。
5. **server 只綁本機**：啟動務必加 `--host 127.0.0.1`；此 server 可執行模型推論，不應暴露在區網。

---

## 十、 已知限制與待決定事項

1. **延遲與原預期的差距**：開 prefix cache 後單工具約 0.7 ~ 1.0 s（4bit），已落在原預期 0.5 ~ 1.5 s 內；多工具仍為 2 ~ 3 s（輸出 token 數決定）。已改用 4bit（單工具中位數 0.66 s）。
2. **「大一點／小一點」不是真正的相對調整**：`set_system_volume` 只有絕對值 `level`，模型實測穩定回 60（大）／30（小）。若目前音量是 80，「調大一點」會變成 60 反而變小。可選方案：(a) 新增 `delta` 參數；(b) executor 讀目前音量再加減。兩者都會改 tool schema，**待你決定**，目前維持原設計。
3. **輸出可能夾雜簡體字**：自我介紹回覆出現「图像」。目前 `web_search_or_open_url` 的 `query` 實測正確（「台北天氣」），但長查詢可能被轉成簡體，建議之後加入實機案例監控。
4. **驗證層的 `mute` 規則來自實測觀察**：`mute=true` 時丟棄 `level`；若你認為「靜音並設音量」是合法需求，需調整該規則。
5. **（已處理）Fast Path 與 Slow Path 的語意重疊**：「把聲音關掉」「靜音一下」「深色模式打開」等現在都走 Fast Path（第三章）。仍交給 LLM 的是沒有明確動詞或相對調整的說法（「螢幕太亮了」「把音量調大一點」）。
6. **4bit 整句直送 LLM 時，「系統設定」開頭的雙動作句會漏一個動作**（第八章 G3）：8bit 在「打開系統設定，把亮度調暗」正常，4bit 不行；改 prompt／工具描述都無效。但真實流程會先切句，由 Fast Path 開 App，實測 11 句雙動作句全部正確（G4）。風險只剩「LLM 收到完整雙動作句」的情況，例如 App 不在詞庫又和別的動作同句時，需靠詞庫把常用 App 納入。
7. **（已處理）執行順序**：現已依說話順序執行（第八章 H）。代價是被 Fast 子句隔開的未命中子句會多一次 LLM 呼叫。
8. **讀畫面很慢，內容正確性尚未核對**：主螢幕截圖縮到 1280×800 約 1,077 個 prompt tokens，prefill 約 3.6 s（約 300 tok/s）＋輸出 100 ~ 130 tokens 約 3 s。實測真實全螢幕畫面的 VL 步驟為 7.0 s、7.5 s（兩次），Safari 端到端的讀畫面步驟為 8.4 s（含固定等待網頁 2.5 s）；同一張圖第二次起因 prefix cache 命中降到約 4 s。2B 模型讀合成畫面的中文大致正確（見第八章 I）；**真實螢幕的輸出我沒有對照畫面核對**。**延遲與品質門檻尚未訂定（D9）**，目前只記錄不判定。
9. **模型不一定會選 `read_screen`／`dark_mode_on`**：工具描述經實驗調整後，深色模式 7/7、讀畫面 4/6（「這個網頁在講什麼」會被當成搜尋、「我在看什麼」太模糊）。沒選到工具時會走 chat call，不會誤執行，但使用者得到的是文字回答而不是動作。
10. **`read_screen`／`chat_reply` 不接受任何參數**：若 2B 模型多塞參數（例如 `{"question": ...}`），驗證層會拒絕，該句沒有回覆。目前實測 C-N1 每案 3 次都是空參數；若之後出現，再決定是否對這兩個工具忽略多餘參數。
11. **誤聽詞的 `seed` 尚未被真實 STT 驗證**：`VS扣`、`因量`、`營幕` 等是預先推測，來源標為 `seed`。`logs/normalize.jsonl` 會累積詞庫替換過的『原文 → 結果』，之後據此把真的出現過的改標 `observed`、刪掉沒出現過的。簡轉繁表（`s2t`，116 個詞／字）只涵蓋指令與常見字，LLM 回覆仍可能夾雜表外的簡體字。
12. **Fast 容錯比對只對拉丁字母名稱**：`chorme`、`spotfy` 會命中，但中文名稱差一個字（例如「小算般」）不會；門檻 0.80／長度 5／歧義差距 0.05 是依測資訂的初始值，沒有真實 STT 資料校正。
13. **熔斷只針對連不上或逾時**：回應格式錯誤或 HTTP 500 不熔斷（單次問題不該讓 AI 停用 30 s）。熔斷期間 Fast Path 照常，LLM 段落走補救。
14. **LLM 失效時的補救範圍很窄**：只有『歧義被擋下的第 1 名』會被當作候選執行；其餘未命中的句子只告知『這句沒有處理』。這是刻意的保守（寧可不做也不亂做）。
15. **這台機器沒有安裝 Google Chrome**：`開 google` 會如實回報失敗並略過後續依賴步驟。詞庫把 `google`／`瀏覽器` 對應到 Chrome（決策 D7）；若要改用 Safari 或預設瀏覽器，需調整詞庫或另行決定。
16. **LFM3-VL 在 mlx-vlm 的支援仍在演進**（模型卡標示 in progress）：本文件鎖定 `mlx-vlm 0.7.11` 實測，升級後請重跑第八章 B、C 組。
