# 端雲協同：本機優先＋雲端對話（實作計畫）

> **歸檔（2026-11-21 結案）**：原檔名 `docs/端雲協同：本機優先＋雲端對話：實作計畫.md`。以下內文保留結案時的原樣；文中的路徑、章節編號與「待確認／尚未驗證」都是當時的狀態，目前有效的決策見 `docs/decisions/`。

狀態：**P0–P3 已實作（2026-11-16，基準 HEAD `d32d239`，尚未 commit）**；P4（串流）未做。實作結果、與本計畫的差異、實際執行的驗證見 **§13**——§5–§9 是實作前的計畫，其中分流（R1／R1′）、WebP、本機附記憶、`fallback` 狀態等已依 P0 實測改變，以 §13.2 為準。本檔由 Gemini 產出的「端雲協同雙軌架構升級計畫書」依現有架構改寫：原提案逐項對照見 §2，決策見 §3、§10，量化驗收見 §9。

## 0. 結論

1. **方向可行，但原提案有一半已經存在、一部分和既有決策衝突**。工具註冊表（`TOOLS_SCHEMA`＋`validate_tool_call`＋`run_tool`）、按需截圖、前景視窗截圖都已經有了；要新增的只有「雲端 client」「分流」「雲端看畫面」「對話記憶」四塊。
2. **雲端是加分，不是依賴**：沒金鑰、斷網、熔斷、分流沒把握時，一律走今天的本機路徑，行為與現在逐字相同。Fast Path、`completeness`、端點偵測、`SYSTEM_PROMPT`、`TOOLS_SCHEMA` 都不動。
3. **刪掉 V 類（本機看畫面點 UI）**：和前景視窗計畫的決策 D4（不做「按下畫面上某個名稱的按鈕」）衝突，也違反「準確優先」。分類只剩 `L`（本機）／`C`（雲端）。
4. **1-Token 分流要先過 P0 量測才採用**：要確認 ① 準確率 ② 延遲 ③ 不會擠掉規劃呼叫的前綴快取。任一項不過就改用替代方案 R1′（本機規劃只選了 `chat_reply` 才把聊天送雲端，不多一次呼叫）。**P0 結果（§13.1）：準確率 86/130（66%）、延遲 p50 593 ms，兩項都不過，且雲端規劃本身 p50 0.93 s、比本機分流＋規劃更快更準，所以改為「Slow 步驟雲端優先、失敗退回本機」（K11），不分流。**
5. **雲端設定存 `.env`**（使用者指定）：`AI_API_URL`、`AI_MODEL`、`AI_API_KEY`（金鑰留空＝雲端停用）。端點是 LiteLLM proxy（OpenAI 相容）。`.gitignore` 已排除 `.env`；不新增 `python-dotenv`，在 `config.py` 寫一個小解析器；**金鑰不寫進 `os.environ`**（`services.py` 會把 `os.environ` 傳給 mlx_vlm／STT／Electron 子程序，寫進去就外洩給它們）。
6. 北極星改變**已確認**（K5）：本機優先，雲端負責聊天、知識問答、複雜畫面與「更強的 AI 控制」（共用記憶、共用工具、摘要輸入）。實作時同步 README／AGENTS.md。
7. 記憶可以用來解析「把它關掉」這類代名詞動作（K8）。為了準確，加一條**對象必須可追溯**的守門：解析出來的 App／專案必須出現在記憶的工具紀錄裡（§6.5）。

## 1. 要解決的問題

| 現況 | 證據 | 目標 |
|---|---|---|
| 聊天與知識問答由 4bit 2B 模型回答，品質有限 | `CHAT_SYSTEM_PROMPT` 限「最多兩句」、`max_tokens=128`；計劃書 §15：chat 回覆會謊稱已執行 | 聊天／知識問答改由雲端模型回答 |
| 看畫面只有本機 VL 模型，只能「條列最多 5 點」 | `SCREEN_QUESTION`、`read_screen` 的 `max_tokens=256` | 解釋程式錯誤、圖表、翻譯等複雜畫面交給雲端（需同意，Q3） |
| 「邊聊天邊下指令」走本機規劃 | 本機規劃實測 20 句×4：prefill 中位數 33 ms、整體 877 ms（前景視窗計劃書 §13.3） | 雲端也能下同一組工具，執行仍在本機、仍經信任邊界 |
| 每句話獨立，沒有對話連貫 | `ExecContext` 不跨語句（AGENTS §5.14） | 雲端聊天可參考最近幾輪（獨立 store，Q4） |

不改的目標：Fast Path 的延遲與準確率、LLM 掛掉時的降級行為、所有安全守門。

## 2. 原提案與現況對照（分析）

| # | 原提案 | 現況（程式碼） | 判斷 | 調整 |
|---|---|---|---|---|
| 1 | L0 硬規則快車道，小於 10 ms，直接呼叫 `osascript` | 已存在：`fast_path.py` 規則比對（數 ms），執行走 `open -a`／`native.py`（ctypes）／`osascript`；整步數 ms～約 100 ms（含 `mdfind` 的找檔案 80–130 ms） | 已完成；「10 ms」只對比對成立，不含執行 | **完全不動**；新增測試保證 Fast Path 不會呼叫雲端（A4） |
| 2 | L1 用「LFM2B-VL」做 1-Token 分流，60–120 ms | 模型實為 `AI_models/LFM3-VL-2B-MLX-4bit`；目前未命中的句子直接送規劃（含 `chat_reply` 工具），沒有分流 | 可行但未驗證；**每個 slow 步驟多一次呼叫**，`L` 路徑會變慢 | 列為 R1，先做 P0 量測（§8）；不過就改 R1′ |
| 3 | Assistant Prefill（預填 `Route: `）＋`max_tokens=1` | mlx_vlm 0.7.11 的 chat schema 有 `logit_bias`、`top_logprobs`（server 需 `--top-logprobs-k`，預設 0）；**沒找到**續寫助手訊息的參數 | 預填不一定能用 | 改用 `logit_bias` 只允許 `L`／`C` 兩個 token＋`top_logprobs` 取信心；預填等 P0 證實支援再評估 |
| 4 | V 類：本機看圖點擊 UI 元素 | 前景視窗計畫 D4：不做「按下畫面上某個名稱的按鈕」；常用功能只走 `front_action` 的固定表 | **與既有決策衝突**，小模型座標點擊誤觸成本高 | **刪除**；看畫面維持 `read_screen`（本機）或雲端看畫面（§6.4） |
| 5 | L2 本機執行器＋`escalate_to_cloud` 逃生門（模型自評畫面太複雜就轉交） | 現有 Slow Path 就是 L2 | 小模型自評不可靠（計劃書 §15：連否定都不理會） | 不做模型自評；改用規則：看畫面句帶「解釋／除錯／翻譯／總結／整理成表」才送雲端看畫面 |
| 6 | Phase 1：抽出 `ToolRegistry` | 已存在：`TOOLS_SCHEMA`（OpenAI function calling 格式，雲端可直接用）、`validate_tool_call`（唯一信任邊界）、`AVAILABLE_FUNCTIONS`／`LOCAL_TOOLS`／`_dispatch`／`run_tool` | **已完成，不需要重抽** | 只把 `plan_slow_path` 裡的「驗證迴圈」抽成函式，讓本機與雲端共用（§6.3） |
| 7 | 工具名 `control_app`、`system_setting`、`clipboard_op`、`ui_click`、`capture_screen` | 實際是 `control_mac_app`、`set_system_volume`、`adjust_screen_brightness`、`web_search_or_open_url`、`system_power_action`、`open_project`、`read_screen`、`chat_reply` | 名稱不存在；剪貼簿與 UI 點擊是新功能 | 沿用現有名稱；剪貼簿、UI 點擊不在範圍（§9.8） |
| 8 | 對本機給「精簡單行函數說明」、對雲端給 JSON Schema | 本機也是吃 JSON Schema，且 `TOOLS_SCHEMA` 是 prefix cache 的一部分（AGENTS §4.2–4.3） | 改本機格式會破壞快取與已實測的正確率 | 本機不動；雲端用**同一份** `TOOLS_SCHEMA`。`LOCAL_TOOLS` 對雲端也維持看不到（第一版，Q8） |
| 9 | 雲端回傳 `tool_calls` 交本機執行 | `plan_slow_path` 已有完整驗證：`validate_tool_call`、`_search_intent_error`、`_local_reroute`、`STATE_TOOLS`（提問句）、G1 | 可直接沿用 | 雲端的工具呼叫走同一套，一條都不能少（A6） |
| 10 | `capture_screen` 做成工具，按需截圖 | `read_screen` 已是工具、按需執行；「看畫面」補救由 `_is_look_at_screen` 處理 | 已完成 | 雲端版只在 Q3 同意後加；第一版**單輪**（雲端不能再要求截圖後回傳圖片，Q9） |
| 11 | 優先擷取前景視窗（Quartz／CGWindow） | 已有：句子提到頁面／網頁時用 CGWindowList 找前景瀏覽器視窗、`screencapture -l`；其餘截整個主螢幕 | 部分完成 | 第一版不擴大；任意 App 的前景視窗列為選項（Q3） |
| 12 | 長邊 1280／1536、WebP Q75–80、100–250 KB | `sips -Z 1280` 輸出 PNG；本機 `sips --formats` 有 `org.webmproject.webp`（**未實測體積**） | 可行，不需新依賴 | 只有送雲端的截圖轉 WebP；P0 量體積 |
| 13 | 墊音（預錄或本機 TTS）、串流 TTS | **專案沒有 TTS**：回覆只在終端機與桌面角色泡泡 | 新功能，範圍大 | TTS 不在本次範圍；改為桌面角色「思考中」狀態＋串流文字（P4 選配，要改 UI 協定） |
| 14 | 5 輪共用記憶池，用來解析「把它關掉」 | `ExecContext` 不跨語句；跨語句狀態要放獨立 store（如 `choices.STORE`） | 放獨立 store 可行；**用模型猜代名詞去執行動作**違反「對象優先順序」與準確優先 | 第一版記憶只給雲端聊天連貫用；代名詞動作是否開放由 Q4 決定 |
| 15 | 「100% 工具操作容錯率」「徹底消除分類誤判」 | — | 不可驗收的形容 | 改為 §9 的具體數字 |
| 16 | 路由準確率 95% 以上 | 既有：本機規劃 80/80（20 句×4） | 合理，但要有標註語料 | 新增分流語料（§9.2），門檻列 Q5 確認 |
| 17 | 雲端金鑰 | 無；`.gitignore` 已含 `.env`、`.env.*`；沒有 `python-dotenv` | — | §6.1；端點、模型、金鑰都放 `.env`（K6） |

## 3. 已定案的決策

| # | 決策 | 來源 |
|---|---|---|
| K1 | 雲端設定存在專案根目錄的 `.env`，不進版控、程式不寫入 `.env` | 使用者 2026-11-15 指定 |
| K2 | Fast Path、`completeness`、端點偵測不動；本機 `TOOLS_SCHEMA` 不動 | AGENTS §4.1–4.3、§4.6（既有規則） |
| K3 | 所有模型（本機、雲端）產生的工具呼叫都經 `validate_tool_call` 與既有守門 | AGENTS §5.2（既有規則） |
| K4 | G1：否定／敘述句不呼叫任何模型（本機、雲端、分流都不送） | 計劃書 §15（既有規則） |
| K5 | 接受北極星改變：允許雲端 API；本機優先不變 | 使用者 2026-11-15（Q1） |
| K6 | 雲端：`AI_API_URL=https://chatapi.ntubimdbirc.tw`、`AI_MODEL=BIRC-LLM`、`AI_API_KEY` 先留空；三者都放 `.env` | 使用者 2026-11-15（Q2） |
| K7 | 螢幕截圖可以送雲端；沿用現有截圖範圍（頁面類截前景瀏覽器視窗，其餘整個主螢幕） | 使用者 2026-11-15（Q3） |
| K8 | 端雲共用記憶、共用工具、摘要輸入（更強的 AI 控制）：記憶給本機與雲端，可用來解析代名詞動作 | 使用者 2026-11-15（Q1、Q4） |
| K9 | 提示詞可以微調（分流、雲端、本機 `SYSTEM_PROMPT`）；每次修改都要附 live A/B 數字，本機的改動不得破壞前綴快取 | 使用者 2026-11-15 |
| K11 | （實作決策，2026-11-16）Slow Path 雲端優先、失敗同一步退回本機；不做 1-Token 分流（P0 實測不過，§13.1）。Fast Path、G1、本機轉交（`local_miss`）不碰雲端 | P0 實測 |
| K12 | （實作決策）截圖用 JPEG 不用 WebP：`sips` 只能讀 WebP、不能寫（實測 `Can't write format`），不加依賴。雲端另有 `chat_reply(text)` 讓動作＋聊天的混合句能一次回覆（§13.1） | 實測 |
| K13 | 雲端提示詞：XML、第三人稱，人格＝可愛、熱情、簡潔；思考深度 `reasoning_effort=low` | 使用者 2026-11-16 |
| K10 | 其餘照 §10 的建議：門檻照提案值、R 表由我起草再由使用者確認、提交 `.env.example`、費用依供應商決定、雲端工具＝`TOOLS_SCHEMA`、單輪 | 使用者 2026-11-15（「其他同意」） |

## 4. 已知前提

| 項目 | 結果 | 方式 | 影響 |
|---|---|---|---|
| mlx_vlm 版本 | 0.7.11 | `importlib.metadata` | — |
| chat 請求可用參數 | 有 `logit_bias`、`top_logprobs`、`stream` | 讀 `mlx_vlm/server/schemas.py` | R1 用 `logit_bias`＋`top_logprobs`；未實測 |
| `top_logprobs` 上限 | server 端 `--top-logprobs-k`，預設 0（關閉） | 讀 `mlx_vlm/server/cli.py` | `services.llm_service` 要加 `--top-logprobs-k 2` |
| 精確前綴快取條目數 | `APC_EXACT_CACHE_ENTRIES` 預設 2 | 讀 `mlx_vlm/apc.py` | 分流與規劃兩組前綴可能互相擠掉 → P0-3 必測 |
| 本機規劃延遲 | prefill 中位數 33 ms、整體 877 ms（20 句×4） | 前景視窗計劃書 §13.3 實測 | 分流若 p50 100 ms，`L` 路徑約增加 11% |
| `python-dotenv` | 沒有安裝 | `import dotenv` 失敗 | 自寫解析器，不加依賴 |
| `sips` WebP | `sips --formats` 列出 `org.webmproject.webp` | 實際執行 | 可轉 WebP；體積未測 |
| 子程序環境變數 | `services.py` 以 `{**os.environ, ...}` 啟動 mlx_vlm 與 Electron | 讀程式碼 | 金鑰不可放進 `os.environ` |
| 測試網路隔離 | `conftest.py` 讓 `urllib.request.urlopen` 預設拋錯 | 讀程式碼 | 雲端 client 用 `urllib`，離線測試自動擋住真連線 |
| TTS | 專案沒有 | grep | §2 #13 |
| 雲端端點 | `GET /` 回 LiteLLM Swagger UI；`GET /v1/models`、`/health` 沒帶金鑰回 401 `auth_error` | 實際執行（2026-11-15，未帶金鑰、未送內容） | OpenAI 相容：`POST {AI_API_URL}/v1/chat/completions`，`Authorization: Bearer {AI_API_KEY}` |

尚未量到（P0）：分流準確率與延遲、快取互擾、雲端延遲（從這台電腦）、WebP 體積、**BIRC-LLM 是否支援 tool calling／圖片輸入／串流**（需要金鑰）。

## 5. 架構

```
process_voice_command（不變）
 ├─ fast 子句 → Fast Path（不變；不碰本機 LLM、不碰雲端）
 └─ slow 步驟 → execute_slow_path_llm
      ├─ intent_guard（G1，不變）：no_action → 固定回覆，任何模型都不送
      ├─ local_miss 轉交（不變，只走本機）
      ├─ route_utterance()                      ← 新（R1）
      │    規則：雲端不可用（沒金鑰／設定關閉／熔斷中／401 停用）→ L，不呼叫分流
      │    本機 1-Token：p(C) ≥ ROUTE_CLOUD_MIN_P → C，否則 L
      │    本機 LLM 不可用、雲端可用 → C
      ├─ L → plan_slow_path（現狀，含本機 chat_reply；user 訊息附「## 最近對話」，L5 不過則不附）
      └─ C → plan_cloud（同一份 TOOLS_SCHEMA；user 訊息附「## 最近對話」）
             tool_calls → validate_calls()（與 L 共用，含代名詞對象守門）→ run_tool（本機執行）
             content    → 回覆（不再另外 chat call）
             失敗       → 同一步退回 L
 每句結束 → memory.STORE 記一輪（原句、路由、工具與結果、回覆摘要）
```

| 檔案 | 變更 |
|---|---|
| `mac_ai/config.py` | `ENV_PATH`、`CLOUD_ENABLED`／`CLOUD_TIMEOUT_S`／`CLOUD_VISION_ENABLED`、`ROUTE_*`；`env_value(name)` 讀 `.env`（§6.1） |
| `mac_ai/router/cloud_client.py`（新） | OpenAI 相容 `{AI_API_URL}/v1/chat/completions`（`urllib`，不加 SDK）、逾時、獨立熔斷、錯誤分類、不記金鑰 |
| `.env.example`（新）、`.gitignore` | 範例檔（金鑰留空）；`.gitignore` 加 `!.env.example` |
| `mac_ai/router/slow_path.py` | 抽出 `validate_calls()`；新增 `route_utterance()`、`plan_cloud()`；`execute_slow_path_llm` 依路由分派 |
| `mac_ai/router/llm_client.py` | `_chat` 可帶 `logit_bias`／`top_logprobs`，回填 logprobs（不改既有呼叫的 body） |
| `mac_ai/services.py` | `llm_service` 加 `--top-logprobs-k 2`（只在 R1 採用時） |
| `mac_ai/router/memory.py`（新，P3） | 對話記憶 store（§6.5） |
| `mac_ai/router/actions.py`（P3） | `read_screen` 的截圖部分抽成函式，雲端看畫面共用 |
| `mac_ai/ui_bus.py`、`desktop/protocol.js`、`desktop/state.js`（P4 選配） | 串流文字事件 |
| `tests/conftest.py` | 預設沒有金鑰、`ENV_PATH` 指向不存在的檔案；`cloud` fixture（假雲端，記錄請求） |
| `pytest.ini` | marker `cloud`；`addopts` 加 `and not cloud` |
| `scripts/acceptance.py` | `run --cloud`、分流與雲端指標、變差規則（§9.5） |
| `main.py` | 啟動時印一行雲端狀態；`warmup` 時也暖分流前綴（R1 採用時） |
| `README.md`、`AGENTS.md` | 北極星、`.env` 設定方式、新指令、新守則 |

不變的東西：`fast_path.py`、`completeness`、`endpoint.py`、`domain.py`、`local_actions.py`、`TOOLS_SCHEMA`、`native.py`、`vocab/lexicon.json`。本機 `SYSTEM_PROMPT` 只在 K9 的條件下微調。

## 6. 規格

### 6.1 金鑰與設定（`.env`）

`.env` 格式（使用者 `cp .env.example .env` 後自己填金鑰，程式只讀不寫）：

```
# 雲端模型（LiteLLM proxy，OpenAI 相容）。不要提交 .env；建議 chmod 600 .env
AI_API_URL=https://chatapi.ntubimdbirc.tw
AI_MODEL=BIRC-LLM
AI_API_KEY=
```

| 規則 | 內容 |
|---|---|
| 讀取來源 | 每個鍵先看程序環境變數，沒有再讀 `config.ENV_PATH`（預設 `ROOT/.env`） |
| 請求 | `POST {AI_API_URL 去掉結尾 /}/v1/chat/completions`，`Authorization: Bearer {AI_API_KEY}`，`model={AI_MODEL}` |
| 雲端啟用條件 | `CLOUD_ENABLED` 為真，且三個鍵都非空；`AI_API_URL` 必須是 `https://`（否則停用並警告，金鑰不走明文） |
| 解析 | 一行一個 `KEY=VALUE`；忽略空行與 `#` 開頭；允許 `export ` 前綴；去掉成對的單／雙引號；**不做**變數展開；格式錯的行略過並警告行號（不印內容） |
| 讀取時機 | 第一次要用雲端時讀一次並快取在模組變數；`import config` 不做任何 IO |
| 不外洩給子程序 | **不寫進 `os.environ`**（`services.py` 會把它傳給 mlx_vlm、STT、Electron） |
| 檔案權限 | `.env` 的權限含 group／other 可讀（`mode & 0o077`）時印一次警告「建議 chmod 600 .env」，不阻擋 |
| 沒有金鑰（含留空） | 雲端停用，行為與現在相同；啟動印一行「☁️ 雲端：未設定 AI_API_KEY，只用本機」 |
| 金鑰錯誤 | HTTP 401／403 → 本次執行期間停用雲端，印一次警告（不含金鑰），之後走本機 |
| 紀錄 | `commands.jsonl` 只記 `cloud: {model, status, ttft_ms, total_ms, prompt_tokens, completion_tokens}`；錯誤只記 `HTTP 401`、`timeout` 這類分類，**不記回應內容**（可能回顯請求） |
| 其餘設定 | `CLOUD_ENABLED`（預設 True）、`CLOUD_TIMEOUT_S`、`CLOUD_VISION_ENABLED`（預設 True，K7）、`ROUTE_CLOUD_MIN_P` 放 `config.py`；端點與模型放 `.env`（K6） |
| 範例檔 | `.env.example` 入版控（`.gitignore` 加 `!.env.example`）；內容與上面相同，金鑰留空 |

### 6.2 分流（R1）

| 項目 | 規格 |
|---|---|
| 位置 | `execute_slow_path_llm` 內，`intent_guard` 之後、`plan_slow_path` 之前；`local_miss` 轉交不分流 |
| 規則先判（不呼叫模型） | 雲端不可用 → `L`（零額外延遲，與現在相同） |
| 請求 | 獨立、逐字穩定的 `ROUTER_SYSTEM_PROMPT`；user＝原句（不改字，AGENTS §5.13）；`tools` 不帶；`max_tokens=1`、`temperature=0`、`logit_bias` 只放行 `L`、`C` 兩個 token、`top_logprobs=2` |
| 判定 | `p(C) ≥ ROUTE_CLOUD_MIN_P`（提案 0.70）→ `C`；其餘（含回應沒有 logprobs、格式錯）→ `L` |
| 分類準則 | `L`：句子要電腦做事（開關、調整、搜尋、看畫面、專案）且單純；`C`：閒聊、知識問答、意見、需要長回答、帶「解釋／除錯／翻譯／總結」的看畫面 |
| 本機 LLM 不可用 | 雲端可用 → `C`；雲端也不可用 → 既有 `_recover_slow_segment` |
| 紀錄 | `step["route"] = {"to": "L"\|"C", "by": "rule"\|"model", "p": 0.93, "ms": 85}` |
| 暖機 | `warmup_llm` 多送一次分流請求，讓分流前綴進快取 |

**P0 不過時的替代方案 R1′**：不加分流呼叫。照現在先做本機規劃；規劃結果**只有** `chat_reply`（或沒有工具）且雲端可用時，聊天改送雲端。代價：聊天句多約 0.9 秒本機規劃；好處：控制句延遲完全不變、沒有快取互擾、不需要新語料門檻（但雲端回覆的驗收照做）。

### 6.3 雲端規劃與回覆（`C`）

| 項目 | 規格 |
|---|---|
| 請求 | `CLOUD_SYSTEM_PROMPT`（逐字穩定，雲端的 prompt cache 也吃前綴）＋`TOOLS_SCHEMA`＋user＝`build_user_message(原句, front=目前視窗, memory=最近對話)` |
| 模型能力不足時 | P0 若發現 BIRC-LLM 不支援 tool calling：`C` 只做聊天（`tools` 不帶），句子含動作時路由改 `L`；不支援圖片：雲端看畫面停用，走本機 `read_screen` |
| 系統提示要點 | 繁體中文（臺灣用語）、口語簡短（預設 ≤ 3 句）、只用提供的工具、**沒有呼叫工具就不得說已執行** |
| 工具呼叫 | 抽出共用的 `validate_calls(message, utterance, local_miss)`：`validate_tool_call` → `_search_intent_error` → `_local_reroute` → `info_question` 丟 `STATE_TOOLS`。本機與雲端走同一個函式 |
| 文字回覆 | 有 `content` 且沒有工具呼叫 → 直接當回覆（不再發 chat call）；有工具呼叫時，只有**全部通過驗證且執行成功**才採用 `content`，否則回覆改用各工具的結果字串（避免「已幫你打開」但其實被拒絕） |
| 輪數 | 第一版單輪：工具結果不回傳給雲端（Q9） |
| 逾時與熔斷 | `CLOUD_TIMEOUT_S`（提案 8 秒，總時間）；連線錯誤／逾時／429／5xx → 獨立熔斷 `BREAKER_COOLDOWN_S`（30 秒），不影響本機熔斷 |
| 退回本機 | 雲端失敗 → 同一步改走 `plan_slow_path`；`ctx.status = "fallback"`，`step["cloud"]["status"]` 記原因 |
| 結果前綴 | 雲端回覆用 `☁️ AI 回覆：`；失敗仍用 `❌`／`⚠️`（`_step_status` 依此判斷，AGENTS §5.12） |
| 傳送的資料 | 原句、目前視窗 App 名稱（與本機相同，不含網址與標題）、記憶摘要、看畫面時的截圖（§6.4）；**不含**檔案完整路徑、搜尋結果清單 |
| 提示詞微調（K9） | `CLOUD_SYSTEM_PROMPT`、`ROUTER_SYSTEM_PROMPT` 依 C3／L1 調；本機 `SYSTEM_PROMPT` 改動要附 `test_llm_live.py` 改動前後的正確數與 prefill 中位數 |

### 6.4 雲端看畫面（P3，K7 已同意，預設開啟）

| 項目 | 規格 |
|---|---|
| 觸發 | `CLOUD_VISION_ENABLED=True`、雲端可用且 P0 確認支援圖片、路由為 `C`、而且句子是看畫面＋「解釋／除錯／翻譯／總結／整理成表」；其餘看畫面維持本機 `read_screen` |
| 截圖 | 共用 `read_screen` 的截圖邏輯（頁面類截前景瀏覽器視窗，其餘整個主螢幕），不擴大範圍 |
| 壓縮 | `sips -Z 1280` 後轉 WebP（品質 80）；> 250 KB 時降到 60 再轉一次；仍超過就退回本機 `read_screen` |
| 紀錄 | 只記 `capture_target`、`bytes`、`px`、各段 ms；不記圖片；暫存檔一律刪除（沿用 `finally`） |
| 失敗 | 雲端失敗 → 退回本機 `read_screen` |

### 6.5 對話記憶（P3）

| 項目 | 規格 |
|---|---|
| 位置 | `mac_ai/router/memory.py` 的 `STORE`（獨立 store，不放 `ExecContext`） |
| 內容 | 最近 5 輪：`{user: 原句, route, tools: [(名稱, 參數, status)], reply: 前 80 字}`（摘要輸入，K8）；Fast 步驟也記（「打開 Safari」之後說「把它關掉」才接得上） |
| 有效期 | 距離上一輪超過 `WAKE_SESSION_S`（60 秒）清空；時鐘注入 |
| 使用範圍 | 本機規劃與雲端的 **user 訊息**都附 `## 最近對話`（不放 system，保住前綴快取）；本機要通過 L5，不過就只給雲端 |
| 不記的東西 | 檔案完整路徑（只記 `~` 縮寫）、截圖、網址 |
| 代名詞動作（K8） | 句子沒有明確對象（只有「它／那個／剛剛那個」）時，模型可依記憶決定對象，但**對象守門**：`control_mac_app.app_name`、`open_project.project_name` 必須出現在本句、或記憶中某一輪成功的工具參數裡，否則拒絕（`⚠️ 不確定「它」指的是什麼`）；仍要通過 `validate_tool_call` |
| Fast Path | 不讀記憶（`match()` 維持純函式）；代名詞句本來就不會命中 Fast，交給 Slow 處理 |

### 6.6 串流與桌面角色（P4，選配）

雲端改 `stream=true`、SSE 解析（含跨 chunk 的 UTF-8 與工具呼叫增量組裝）；`ui_bus.py` 新增 `reply_delta` 事件，`desktop/protocol.js`、`desktop/state.js` 同步（AGENTS §6：UI 協定兩邊一起改）。TTS 與墊音不在本次範圍。

## 7. 端點偵測連動

沒有。分流與雲端都在 `command_worker` 執行階段，`completeness.classify`、`FastPathMatcher.match` 不變。A14 確認這兩條路徑不會 import 或呼叫雲端模組。

## 8. 實作階段

| 階段 | 內容 | 進入條件 | 驗收 |
|---|---|---|---|
| P0 | 可行性量測（不改產品程式碼；量測腳本放 `scripts/route_bench.py`，結果寫進本檔 §4）：① 分流準確率 ② 分流延遲 ③ 分流與規劃交錯時規劃的 `prefill_ms` ④ 雲端延遲 ⑤ WebP 體積 ⑥ BIRC-LLM 的 tool calling／圖片／串流支援 ⑦ 本機規劃附記憶的影響（L5） | R 表標註已確認；`.env` 已填金鑰（④⑥ 需要） | L1–L3、L5、C2、C8 的數字；**分流不過就改 R1′**，結果先回報再進 P2 |
| P1 | `.env` 讀取、`.env.example`、`cloud_client`、熔斷、紀錄、conftest 隔離、`pytest.ini`、`acceptance.py --cloud` 骨架 | P0 完成（`.env.example` 已先建立） | A0–A3、A8、A10 |
| P2 | `validate_calls` 抽出、R1（或 R1′）、`plan_cloud`、退回本機 | P1 | A4–A9、A14、L1–L4、C1–C5、E1–E8 |
| P3 | 對話記憶（含代名詞對象守門）、雲端看畫面 | P2 | A11、A12、L5、C6、C9、U3 |
| P4 | 串流文字（選配） | P3；使用者要做 | A13、U2 |

每階段一個 commit（`feat:` 開頭），可單獨 `git revert`。

## 9. 驗收（量化）

門檻中標「提案」的數字已於 2026-11-15 由使用者同意（K10）；P0 量完基線後若要調整，先回報再改。

### 9.1 自動化離線測試（`pytest`，不連網、不花錢）

新增 `tests/test_cloud_offline.py`、`tests/test_route_offline.py`。假雲端用 `cloud` fixture（取代 `cloud_client._post`，記錄每次請求的 body 與次數）。

| # | 驗收項目 | 指令 | 通過標準 | 失敗時處置 |
|---|---|---|---|---|
| A0 | 全部回歸 | `pytest -q`；`cd desktop && node --test` | 0 failed；skipped 仍是 5；passed ≥ 改動前＋新增數（P1 開工時記下實際數字）；node 46 pass（P4 ≥ 46＋新增） | 先修迴歸，不進下一階段 |
| A1 | `.env` 解析 | `pytest -q tests/test_cloud_offline.py -k env` | ① `AI_API_KEY=abc` → `"abc"` ② `export AI_API_KEY="abc"`、單引號 → `"abc"` ③ 空行、`#` 註解、格式錯的行略過 ④ 環境變數優先於檔案 ⑤ 檔案不存在、沒有這個鍵、或 `AI_API_KEY=`（留空）→ 雲端停用 ⑥ `AI_API_URL=http://…` → 停用並警告 ⑦ `.env.example` 原樣複製成 `.env` → 雲端停用、URL／模型讀到 K6 的值 ⑧ 讀完後 `os.environ` 不含 `AI_API_KEY` ⑨ 權限 0644 → 警告 1 次；0600 → 0 次 ⑩ `services.llm_service()`、`stt_service()`、`ui_service(...)` 的 `env` 都不含金鑰值 | 修 `config.env_value` |
| A2 | 金鑰不外洩 | `pytest -q tests/test_cloud_offline.py -k leak` | 金鑰設為 `sk-TEST-7f3a9c`，跑：成功、401、429、500（回應 body 內含金鑰字串）、逾時、JSON 格式錯；之後掃描 capsys 輸出、暫存 `commands.jsonl`／`latency.jsonl`／`normalize.jsonl`、回傳 dict 的 `json.dumps`、例外 `str()` → **出現次數 0** | 修錯誤分類與紀錄欄位 |
| A3 | 沒金鑰＝現狀 | `pytest -q tests/test_route_offline.py -k no_key` | 沒金鑰時跑 `test_llm_live` 的 `CASES` 句子（假本機 LLM）：雲端請求 0 次、分流請求 0 次、`step["route"] == {"to": "L", "by": "rule", ...}`；送給本機的 messages 與改動前**逐字相同** | 修規則先判 |
| A4 | Fast Path 不碰雲端 | `pytest -q tests/test_typo_offline.py tests/test_fast_offline.py`（conftest 讓雲端一被呼叫就失敗） | 0 failed；雲端與分流請求 0 次 | 找出誤入的路徑 |
| A5 | G1 守門涵蓋雲端 | `pytest -q tests/test_route_offline.py -k guard` | `test_guard_offline.py` 的否定／敘述句、`MISFIRE_CASES`，在有金鑰時：雲端 0 次、分流 0 次、回覆 `== NO_ACTION_REPLY`、`sh` 沒有任何 `open`／`osascript` | 修 `execute_slow_path_llm` 的順序 |
| A6 | 雲端工具呼叫的信任邊界 | `pytest -q tests/test_cloud_offline.py -k validate` | 假雲端回傳：未知工具、編造的 App `FooBarApp`、多餘參數、`front_action`（LOCAL_TOOLS）、糾正句上的 `web_search_or_open_url`、提問句上的 `system_power_action` → 全部被拒或丟棄，`sh` 對它們 0 筆指令；合法呼叫送出的指令與本機路徑同參數時**完全相同** | 修 `validate_calls` |
| A7 | 分流判定 | `pytest -q tests/test_route_offline.py -k decide` | 假本機回應：`p(C)=0.90` → C；`0.60` → L；沒有 logprobs／格式錯 → L；本機 LLM 不可用＋雲端可用 → C；兩者都不可用 → `_recover_slow_segment`（status `llm_unavailable`／`fallback`） | 修 `route_utterance` |
| A8 | 雲端失敗退回本機 | `pytest -q tests/test_cloud_offline.py -k fallback` | ① `urlopen` 拋錯 → 同一步改走本機規劃，`ctx.status == "fallback"`、`step["cloud"]["status"] == "unavailable"` ② 30 秒內（注入時鐘）下一句雲端 0 次 ③ 401 → 之後雲端 0 次、警告只印 1 次 ④ 雲端逾時設 8 秒時，假逾時不讓整步超過 `CLOUD_TIMEOUT_S`＋本機規劃時間 | 修熔斷 |
| A9 | 回覆誠實 | `pytest -q tests/test_cloud_offline.py -k honest` | 假雲端回 `tool_calls=[被拒的呼叫]`＋`content="已幫你打開 FooBar"` → 回覆不含該 content，`_CLAIM_RE`（`test_llm_live.py`）0 次命中；全部成功時才採用 content | 修 §6.3 的回覆規則 |
| A10 | 紀錄欄位 | `pytest -q tests/test_runlog_offline.py tests/test_cloud_offline.py -k log` | `commands.jsonl` 的 step 有 `route`、`cloud`，`cloud` 的鍵只在 {`model`,`status`,`ttft_ms`,`total_ms`,`prompt_tokens`,`completion_tokens`} 之內；沒有 base64 圖片 | 修紀錄 |
| A11 | 對話記憶（P3） | `pytest -q tests/test_cloud_offline.py -k memory` | 注入時鐘：6 輪後只剩最後 5 輪；閒置 61 秒 → 空；Fast 步驟也有紀錄；記憶只出現在 user 訊息，本機與雲端的 system 與 tools 逐字不變；不含完整路徑（只有 `~`）、網址、base64；`ExecContext` 沒有新欄位；`FastPathMatcher.match` 不讀記憶 | 修 `memory.py` |
| A15 | 代名詞對象守門（P3） | `pytest -q tests/test_cloud_offline.py -k pronoun` | ① 記憶有「打開 Safari ✅」，說「把它關掉」，假模型回 `quit Safari` → 執行 `quit Safari` ② 假模型回 `quit Spotify`（記憶與本句都沒有）→ 拒絕，`sh` 0 筆 ③ 記憶裡 Safari 那一輪是 ❌ → 拒絕 ④ 記憶過期 → 拒絕 ⑤ 本句明說 App（「把 Spotify 關掉」）→ 不受守門影響 ⑥ 本機與雲端兩條路徑結果相同 | 修對象守門 |
| A12 | 截圖送出規則（P3） | `pytest -q tests/test_cloud_offline.py -k vision` | 預設（`True`）且符合觸發 → 恰好 1 張、`image/webp`、≤ 250 KB；不符觸發的看畫面句 → 雲端請求 body 不含 `image_url`、走本機 `read_screen`；`False` → 所有雲端請求都沒有圖片；> 250 KB 兩次 → 退回本機；P0 判定不支援圖片 → 退回本機；暫存目錄被刪除 | 修 §6.4 |
| A13 | 串流解析（P4） | `pytest -q tests/test_cloud_offline.py -k stream`；`cd desktop && node --test` | 錄好的 SSE 片段（中文跨 chunk、`[DONE]`、工具呼叫增量）組回的結果與非串流相同；`ui_bus` 與 `desktop/protocol.js` 兩邊測試都過 | 修解析器／協定 |
| A14 | 熱路徑不受影響 | `pytest -q tests/test_completeness_offline.py tests/test_endpoint_offline.py` | 0 failed；在全新子程序 import `completeness` 後，`mac_ai.router.cloud_client` 不在 `sys.modules` | 移除匯入 |

### 9.2 分流語料（R 表，L1 的測試資料）

草稿已建立在 `tests/route_corpus.py`（**標註待使用者逐句確認**），每句標 `L`／`C`。`tests/test_route_corpus_offline.py` 檢查：每句都是 **Fast Path 未命中**且**不被 G1 擋下**（否則根本到不了分流）、沒有重複、各類句數與比例符合下表；中英夾雜 ≥ 30%；含錯字 ≥ 10%。

| 類別 | 下限 | 草稿句數 | 來源 |
|---|---|---|---|
| 控制（L） | 40 | 43 | `TYPO_LLM_CASES` 中會到 Slow Path 的句子＋間接說法（「螢幕太亮了眼睛好痛」） |
| 看畫面（L） | 10 | 14 | Fast 沒接住的看畫面說法 |
| 閒聊／知識／意見（C） | 40 | 42 | 新增 |
| 看畫面＋解釋／除錯／翻譯／總結（C） | 10 | 12 | 新增 |
| 混合（同一個 slow 步驟內有動作＋聊天） | 15 | 19 | 新增；標 C（雲端有同一組工具） |

草稿共 130 句：中英夾雜 44 句（34%）、含錯字 14 句（11%）；`pytest -q tests/test_route_corpus_offline.py` 133 passed（2026-11-15 實際執行）。

起草時的發現（實際執行 `normalize`＋`FastPathMatcher`＋`intent_guard`）：`test_llm_live.py` 的 `CASES`、`NEW_TOOL_CASES` 現在**幾乎全被 Fast Path 接走**（49 句候選中 23 句命中）；混合句通常被切成「Fast 子句＋聊天子句」，交給 Slow 的只剩聊天那段。所以分流實際看到的句子以聊天為主，控制句多是間接說法與錯字，L 類比 C 類難判。

範例（標註由使用者確認，Q5）：

| 語句 | 標註 |
|---|---|
| 把螢幕調到最亮 | L |
| 幫我開 VS Code 然後關掉 Spotify | L |
| 打開科索 | L |
| 螢幕上有什麼 | L |
| Python 是什麼 | C |
| 今天心情不錯 | C |
| 你覺得 React 跟 Vue 哪個比較適合新手 | C |
| recursion 跟 iteration 差在哪 | C |
| 幫我看一下這個 error 是什麼意思 | C |
| 把畫面上的英文翻成中文 | C |
| 幫我開深色模式，順便講個笑話 | C |

### 9.3 本機模型實測（`pytest -m live -s`，需 `mlx_vlm.server` 以 `--top-logprobs-k 2` 啟動）

| # | 驗收項目 | 指令 | 通過標準 | 失敗時處置 |
|---|---|---|---|---|
| L1 | 分流準確率 | `pytest -m live -s tests/test_route_live.py -k accuracy`（R 表 × 3 次） | 整體 ≥ 95%（提案，原提案值）；L 被判 C ≤ 5%；C 被判 L ≤ 10%（提案：誤判成 L 只是回答品質差，不會誤動作）；3 次結果一致 ≥ 98% | 調 `ROUTER_SYSTEM_PROMPT`（每次修改附 R 表數字）；兩輪仍不過 → R1′ |
| L2 | 分流延遲 | 同檔 `-k latency`（R 表 × 3，每句記 wall ms） | p50 ≤ 120 ms、p95 ≤ 200 ms（提案，原提案 60–120 ms） | 回報數字；超過 → R1′ |
| L3 | 快取互擾 | 同檔 `-k cache`：20 句 × 4，每句「分流→規劃」交錯 | 規劃 `prefill_ms` 中位數 ≤ 基線 33 ms＋100 ms（沿用前景視窗計畫 Q2）；`L` 路徑整步中位數增加 ≤ 150 ms | 試 `APC_EXACT_CACHE_ENTRIES=4`（附 A/B 數字）；仍不過 → R1′ |
| L4 | 既有 live 不退步 | `pytest -m live -q tests/test_llm_live.py` | 改動前 52 passed → 改動後 52 passed；strict xfail 不變 | 修迴歸 |
| L5 | 本機規劃附記憶 | 同檔 `-k memory`：`CASES`＋`NEW_TOOL_CASES` 20 句 × 4，每句前面附 3 輪假記憶 | 正確數 = 不附記憶時（基線 80/80）；prefill 中位數 ≤ 基線＋100 ms；代名詞句 10 句 × 3 的對象正確 ≥ 27/30（提案） | 不過 → 記憶只給雲端（§6.5），本機維持現狀 |

### 9.4 雲端實測（`pytest -m cloud -s`，需 `.env` 金鑰、會產生費用）

新增 marker `cloud`（預設不跑）。每次執行前印出預估請求數；結束時印出實際 token 總數。

| # | 驗收項目 | 指令 | 通過標準 | 失敗時處置 |
|---|---|---|---|---|
| C1 | 連線與金鑰 | `pytest -m cloud -s tests/test_cloud_live.py -k health` | `GET /v1/models` 的清單含 `BIRC-LLM`；1 次 chat 請求成功；金鑰錯時測試標示 401 並結束，不重試；金鑰留空時整組 skip 並寫明原因 | 檢查 `.env` 的 `AI_API_URL`／`AI_API_KEY` |
| C8 | 模型能力 | 同檔 `-k capability`：帶 `tools` 的「把螢幕調到最亮」、帶一張 WebP 的看圖、`stream=true` 各 3 次 | 記錄三項是否支援（回應有 `tool_calls`／內容提到圖片裡的字／收到多個 SSE chunk），寫進 §4；不支援的項目依 §6.3 降級 | 回報，由使用者決定是否換模型 |
| C9 | 代名詞動作（P3） | 同檔 `-k pronoun`：10 組「先開 X → 把它關掉／用它開…」× 3 | 對象正確 ≥ 27/30（提案）；對象守門拒絕的都是錯的對象（誤拒 0） | 調 `CLOUD_SYSTEM_PROMPT` 的記憶段落格式 |
| C2 | 延遲 | 同檔 `-k latency`：R 表的 C 類 20 句 × 3 | 非串流整體 p50 ≤ 2.0 s、p95 ≤ 4.0 s（提案；P0 量完基線再定）；P4 串流 TTFT p50 ≤ 1.0 s（提案） | 回報數字；換模型或區域由使用者決定 |
| C3 | 工具規劃正確率 | 同檔 `-k tools`：`CASES`＋`NEW_TOOL_CASES` 用同一組 `check` × 3 | 正確數 ≥ 本機同批次（本機基線 80/80 ⇒ 雲端 60/60）；被 `validate_tool_call` 拒絕 0 次（提案） | 調 `CLOUD_SYSTEM_PROMPT`，附數字 |
| C4 | 否定／敘述 | 同檔 `-k guard`：① 直接送雲端規劃（繞過 G1，只量測）記錄誤規劃數 ② 端到端（G1 開） | ① 只記錄、不判通過（數字寫進本檔，作為日後是否放寬 G1 的依據） ② 動作 0、雲端請求 0 | ② 不過即 A5 迴歸 |
| C5 | 誠實 | 同檔 `-k honest`：C 類＋被拒呼叫情境 | 回覆 `_CLAIM_RE` 命中 0 次 | 修 §6.3 |
| C6 | 雲端看畫面（P3） | 同檔 `-k vision`：`test_llm_live.py` 的合成截圖（`shots` fixture）× 3 | 每張 WebP ≤ 250 KB；整體 p50 ≤ 4 s（提案）；輸出寫到 `logs/bench/` 給 U3 人工看 | 回報數字 |
| C7 | 費用 | 每次 `-m cloud` 結束 | 印出 prompt／completion token 總數；整個 `-m cloud` 一次 ≤ 300 次請求（提案） | 減少重複次數 |

### 9.5 量化監控（`scripts/acceptance.py`）

| 變更 | 內容 |
|---|---|
| 新測試組 | `SUITES["cloud"] = ["-m", "cloud"]`；`run --cloud` |
| 指標收集 | 沿用 `ACCEPTANCE_METRICS` 做法：live／cloud 測試把每次結果寫成 JSON Lines（`{"kind": "route", "text", "expect", "got", "p", "ms"}`、`{"kind": "cloud", "text", "ttft_ms", "total_ms", "prompt_tokens", "completion_tokens", "status"}`） |
| 存檔欄位 | `route`：整體與各類正確率、混淆矩陣、`ms` 分布；`cloud`：`total_ms`／`ttft_ms` 分布、退回本機比例、token 總數 |
| 變差規則（新增） | ① 分流整體或任一類正確率下降 ② `route p50` 依既有規則（+20% 且 +20 ms） ③ 雲端延遲 p50 +30% 且 +300 ms（提案：網路波動大，門檻放寬） ④ 退回本機比例上升 > 5 個百分點 |
| 既有規則 | 不變；只比兩次都跑過的測試組（沒跑 `--cloud` 時不比雲端） |
| 測試 | `tests/test_acceptance_offline.py` 補新欄位的解析與 `compare` 案例 |

驗收：`python scripts/acceptance.py run --live --cloud && python scripts/acceptance.py report` 在 P2 完成時跑一次，結果檔名與摘要貼進 §13（實作結果）。

### 9.6 真實環境（實作者在本機執行並貼出輸出）

| # | 驗收項目 | 操作 | 通過標準 | 失敗時處置 |
|---|---|---|---|---|
| E1 | 沒金鑰＝現狀 | `.env` 的 `AI_API_KEY` 留空；`python main.py --text 打開小算盤`、`--text Python 是什麼` | 輸出與改動前相同；`commands.jsonl` 的 `route.by == "rule"` | 修規則先判 |
| E2 | 雲端聊天 | 有金鑰；`--text 用一句話解釋什麼是遞迴` | `☁️ AI 回覆`；`route.to == "C"`；`cloud.total_ms` 有值 | 查 `step["cloud"]["status"]` |
| E3 | 混合句 | `--text 打開小算盤然後告訴我今天適合做什麼` | 小算盤由 Fast Path 開（`kind == "fast"`），問題由雲端回答 | 查切句 |
| E4 | 斷網 | 關 Wi-Fi；`--text 幫我講個笑話` | 本機回覆；`status == "fallback"`；整步 ≤ 本機正常值＋2 秒（提案） | 修逾時 |
| E5 | 金鑰錯誤 | `.env` 改成錯的金鑰；連跑兩句 | 第一句警告 1 次、兩句都由本機回覆；輸出不含金鑰 | 修 401 處理 |
| E6 | 紀錄沒有金鑰 | E2–E5 之後：`python -c "from mac_ai import config; print(config.env_value('AI_API_KEY'))" \| xargs -I{} grep -rlF {} logs/ \| wc -l` | `0` | 修紀錄 |
| E7 | 子程序沒有金鑰 | `python main.py` 運行中：`ps eww $(pgrep -f mlx_vlm.server) \| grep -c AI_API_KEY` | `0` | 修 §6.1 |
| E9 | 代名詞實機（P3） | `--text 打開小算盤` 後 60 秒內 `--text 把它關掉`（`--text` 每次是新程序，所以這項改用 `--ui` 打字輸入） | 小算盤被關閉；`step["memory"]` 記錄用到的那一輪 | 修記憶或對象守門 |
| E8 | Fast Path 延遲不變 | 改動前後各 `python scripts/acceptance.py run`；`report` | `✅ 沒有變差`（路由 p50 規則） | 找出熱路徑上的新成本 |

### 9.7 人工驗收（由使用者判斷）

| # | 驗收項目 | 操作 | 通過標準 |
|---|---|---|---|
| U1 | 聊天品質 | 看 C2 的 20 句回覆（測試輸出到 `logs/bench/`） | 使用者判斷比本機好、語氣適合語音、繁體中文臺灣用語 |
| U2 | 語音實機 | `python main.py --ui`，用麥克風混著講控制與聊天各 10 句 | 控制句沒有變慢的感覺；聊天回覆可接受；沒有誤動作 |
| U3 | 雲端看畫面（P3） | 開一個程式錯誤畫面，說「幫我看一下這個 error 是什麼意思」 | 使用者判斷解說有用；本機版對照 |

### 9.8 不在本次範圍

- TTS、墊音語音、語音串流播放。
- V 類：依畫面點擊 UI、任意打字、剪貼簿操作。
- 多輪雲端 agent（雲端看完工具結果再決定下一步）。
- 雲端語音辨識、送出音訊。
- 雲端看得到 `LOCAL_TOOLS`（`browser_tab`、`find_file`、`front_action`…）。
- 擴大截圖範圍到任意 App 的前景視窗。
- 費用上限的自動阻擋（供應商是學校的 LiteLLM proxy，計費方式未知；先只記錄 token）。
- 記憶跨程序保存（`--text` 每次是新程序，記憶不會延續）。

## 10. 待確認（2026-11-15 已確認，結論見 §3）

| # | 問題 | 結論 |
|---|---|---|
| Q1 | 是否接受北極星改變？送出哪些資料？ | 接受（K5）；共用記憶、工具、摘要輸入（K8） |
| Q2 | 雲端供應商與模型、設定放哪 | `AI_API_URL`／`AI_MODEL`／`AI_API_KEY` 都放 `.env`，金鑰先留空（K6） |
| Q3 | 螢幕截圖可以送雲端嗎？ | 可以，沿用現有截圖範圍（K7） |
| Q4 | 可以依記憶執行代名詞動作嗎？ | 可以（K8），加對象守門（§6.5） |
| Q5 | 門檻數字與 R 表標註 | 門檻同意；R 表草稿在 `tests/route_corpus.py`，**標註仍待逐句確認** |
| Q6 | 提交 `.env.example` | 同意（已建立） |
| Q7 | 費用上限 | 先只記錄 token |
| Q8 | 雲端工具範圍 | 與本機相同的 `TOOLS_SCHEMA` |
| Q9 | 單輪 | 同意 |
| — | 提示詞 | 可以微調（K9） |

## 11. 風險與已知限制

- **隱私**：原句與（同意後的）截圖會離開本機；紀錄只記 metadata。使用者要知道哪些指令走雲端（UI 用 ☁️ 標示）。
- **延遲波動**：雲端延遲受網路影響，C2 只是這台電腦、當下網路的數字。
- **費用**：`-m cloud` 測試與日常使用都會產生費用（C7、Q7）。
- **金鑰外洩**：主要風險是寫進 `os.environ` 被子程序繼承、錯誤訊息回顯；A1、A2、E6、E7 覆蓋。
- **快取互擾**：分流若擠掉規劃前綴，規劃會從 0.9 秒退到 3 秒（AGENTS §1）；L3 覆蓋，不過就 R1′。
- **誤分流**：控制句被送雲端 → 仍由同一套驗證執行，只是較慢；聊天句留在本機 → 回答品質如今天。兩者都不會增加誤動作。
- **雲端也會編造**：工具呼叫靠 `validate_tool_call`；文字回覆靠 §6.3 的採用規則與 A9／C5。
- **代名詞動作**：模型可能把「它」對到錯的 App；對象守門只保證對象「出現過」，不保證是使用者指的那一個（A15、C9 量化）。
- **模型能力未知**：BIRC-LLM 是否支援 tool calling、圖片、串流要等 C8；不支援就降級（§6.3），「更強的 AI 控制」會打折。
- **服務可用性**：端點是學校的 LiteLLM proxy，可用時間與速率限制未知；靠熔斷與退回本機處理。
- **OpenAI 相容層差異**：各家 `tool_calls.arguments` 可能是字串或物件；`validate_calls` 沿用現有「兩種都接受」的寫法。

## 12. 回退方式

- 執行期：把 `.env` 的 `AI_API_KEY` 清空，或 `config.CLOUD_ENABLED = False` → 行為與現在相同（A3、E1 驗證）。
- 程式：每階段一個 commit，`git revert`；P2–P4 只依賴 P1。R1 與 R1′ 互斥，切換只動 `route_utterance`。

## 13. 實作結果（2026-11-16，P0–P3；全部為實際執行的數字）

### 13.1 P0 實測（決定了和 §5–§9 不同的地方）

| 項目 | 結果 | 影響 |
|---|---|---|
| 雲端端點 | `GET /` 是 LiteLLM Swagger；`/v1/models` 有 `BIRC-LLM`、`BIRC-Embedding`；**Python-urllib 預設 UA 被 Cloudflare 擋（403，error code 1010）** | 送 `CLOUD_USER_AGENT`（`config.py`） |
| 雲端能力 | tool calling ✅（`TOOLS_SCHEMA` 直接可用）、圖片輸入 ✅（JPEG，讀得出圖中的 TypeError）、串流 ✅（首個 chunk 0.61 s，P4 未做） | — |
| 思考深度 | `reasoning_effort=low` 有效：推理 token 76→42、延遲 1.1→0.84 s；回應的 `content` 偶爾尾端多一個落單的 `</thinking>` | `CLOUD_REASONING_EFFORT="low"`；`clean_text` 清掉思考標籤、Markdown 粗體／標題、簡轉繁（詞庫補 `适→適`） |
| 本機 1-Token 分流（`logit_bias` 限 `L`／`C`、`max_tokens=1`） | 格式可用（logprobs 回得來），但**準確率 86/130（66%）**（ctrl 32/43、screen 4/14、chat 32/42、screen_c 11/12、mixed 7/19）、**延遲 p50 593 ms／p95 618 ms**（門檻 95%、120 ms）；快取互擾沒有問題（規劃 prefill 中位數交錯時 33 ms） | **不做分流**（K11） |
| 雲端規劃（130 句語料，同一份 `TOOLS_SCHEMA`） | 延遲 p50 0.87 s、p95 1.59 s，快過本機「分流＋規劃」；`ctrl` 41/43、`chat` 42/42、`screen_c` 12/12 | 改為「Slow 步驟雲端優先、失敗退回本機」 |
| 混合句（動作＋聊天） | 這個模型**只要有 `tool_calls` 就不寫文字**：三種提示詞（現行／加強語氣／加範例）都 0/19（我最初的 P0 判準只看有沒有工具，漏看這點） | 雲端專用 `chat_reply(text)`：19/19，其他類別不變（K12）；本機 `TOOLS_SCHEMA` 不動 |
| 截圖格式 | `sips` 只能讀 WebP、不能寫（`Can't write format`）；整個螢幕縮到 1280 的 PNG 約 679 KB，JPEG 品質 80 約 311 KB／實測一般畫面 17 KB | 用 JPEG（品質 80，超過 250 KB 改 60，再超過退回本機 VL） |
| 看圖延遲 | 一張圖 1.6–2.6 s；第一次曾逾時 15 s（偶發，重跑 0.4 s） | `CLOUD_VISION_TIMEOUT_S=15`；實測檔偶發逾時重試一次並印出 |

### 13.2 與 §5–§9 的差異

| 計畫 | 實作 |
|---|---|
| R1 1-Token 分流／R1′ | 都不採用；Slow 步驟雲端優先（K11）。`local_miss` 轉交、G1、Fast Path 不碰雲端 |
| 雲端工具＝`TOOLS_SCHEMA` | 相同，只有 `chat_reply` 多 `text`（`CLOUD_TOOLS`）；`chat_reply` 不會被當工具執行 |
| WebP | JPEG（K12） |
| 記憶給本機與雲端（L5 不過則只給雲端） | **只給雲端**：L5 沒量（本機規劃的 `SYSTEM_PROMPT`／user 訊息逐字不變）；本機仍套用同一個對象守門 |
| 失敗時 `ctx.status="fallback"` | 沒有改 step 狀態；紀錄 `step["route"]="local"`、`step["cloud_fallback"]`、`step["cloud"]["status"]`（狀態仍由執行結果決定，UI 不需改） |
| A14：`cloud_client` 不在 `sys.modules` | 改為「`match()`／`completeness.classify` 跑完語料 0 次雲端請求」：`mac_ai.router` 套件匯入時會載入 `cloud_client`（無 IO、無呼叫），真正要保證的是不呼叫 |
| `top-logprobs-k`、暖機分流前綴 | 不需要（沒做分流）；`services.llm_service` 沒改 |
| P4 串流、桌面角色事件 | 未做（使用者要時再做，§6.6） |
| 新增：「它」守門的限制 | 「把剛剛開的關掉」「剛剛那個」會被既有的 G1 當敘述擋下（`剛剛`），**不放寬 G1**；用「把它關掉」「幫我關掉它」等說法可用 |

### 13.3 自動化測試（全部實際執行）

| 驗收 | 指令 | 結果 |
|---|---|---|
| A0 全部回歸 | `pytest -q`（改動前 1485 passed／5 skipped） | **1560 passed、5 skipped、0 failed**（新增 61＋6＋8；skipped 仍是 5） |
| A0 | `cd desktop && node --test` | 46 pass |
| A1–A3、A5、A6、A8–A12、A15 | `pytest -q tests/test_cloud_offline.py` | 67 passed；變異測試：拿掉對象守門（3 項變紅）、無條件採用雲端文字（8）、錯誤訊息回顯回應內容（4）、雲端不經 `validate_tool_call`（6），還原後全綠 |
| A4、A14 | 同檔 `test_fast_path_and_completeness_never_call_cloud` | 通過 |
| 語料有效性 | `pytest -q tests/test_route_corpus_offline.py` | 133 passed（130 句：中英夾雜 34%、含錯字 11%） |
| 驗收腳本 | `pytest -q tests/test_acceptance_offline.py` | 18 passed（含雲端欄位解析與雲端變差門檻） |

### 13.4 實機測試（全部實際執行）

| 驗收 | 指令 | 結果 |
|---|---|---|
| L4 本機 live 不退步 | `pytest -m live -q tests/test_llm_live.py` | **86 passed、6 xfailed、0 failed**（254 s）；conftest 讓 live 測試也不讀 `.env`，只走本機 |
| C1 連線 | `pytest -m cloud -s tests/test_cloud_live.py` | `/v1/models` 含 `BIRC-LLM`；chat 成功 |
| C2／C3 規劃 | 同上，130 句語料 | 兩次完整執行：`ctrl` 39–40/43、`screen` 14/14、`chat` 42/42、`screen_c` 12/12、`mixed` 18–19/19；延遲 p50 0.90–0.93 s、p95 1.6–2.1 s（門檻 2.0／4.0 s）；被守門拒絕 1 句（「幫我開瀏覽氣看 YouTube」沒有搜尋字詞，既有 `_search_intent_error`）；請求失敗 0 |
| C4 否定句 | 同上 | 端到端 7 句：雲端請求 0、動作 0；繞過 G1 直接問雲端：1/7 誤規劃動作（「不要金音」）——**證明 G1 仍必要** |
| C5 誠實 | 同上 | 工具全失敗時 20 句回覆 0 次聲稱已執行 |
| C8 看圖 | 同上 | 讀得出圖中的 TypeError（1.6–2.6 s） |
| C9 代名詞 | 同上，10 組×3 | **30/30**，執行了錯的對象 0（第一次實測 21/30，原因：我把三組「剛剛…」說法放進測試，被 G1 擋下，不是雲端錯） |
| 量化監控 | `python scripts/acceptance.py run --live --cloud --label cloud-impl` → `report` | 離線 1560／live 86／cloud 7 皆 0 失敗；雲端規劃 p50 896 ms、p95 1637 ms、請求失敗 0、token 輸入 270,962／輸出 12,945（130 句）；`report`：「✅ 沒有變差」（錯字矯正率全部不變、路由 p50 0.01 ms） |

### 13.5 真實環境（`python main.py --text`，本機 server＋真的雲端）

| # | 操作 | 結果 |
|---|---|---|
| E1 | `AI_API_URL=http://x`（雲端停用）說「用一句話解釋什麼是遞迴」 | 警告並用本機回覆（2.6 s）；**本機回答是錯的**（「遞迴是程式在執行過程中依次處理每個系統任務的順序」） |
| E2 | 同一句，雲端 | `☁️ AI 回覆`（1.1 s）：內容正確、語氣符合人格（「～」）；另一句笑話回得偏長（已加「表情符號最多一個」） |
| E3 | 「打開小算盤，然後告訴我今天適合做什麼」 | 小算盤由 Fast Path 開（69 ms，含 `mdfind`／`open`），聊天由雲端回覆（1.2 s），回覆有參考「目前視窗 VS Code」 |
| E4 | `AI_API_URL=https://127.0.0.1:9`（拒絕連線） | 印「雲端無法使用（連線失敗），改用本機」，同一步本機回覆（2.3 s） |
| E5 | 錯誤金鑰，同一程序連跑兩句 | 警告只印 1 次；第一句 `http_401`、第二句 0 次雲端請求（route=local）；輸出不含金鑰 |
| E6 | 掃描 `logs/` 是否含真金鑰 | 0 個檔案 |
| E7 | 子程序環境不含金鑰 | 由 A1／`test_key_never_enters_os_environ_or_child_process_env`（`llm_service`、`ui_service` 的 env）驗證；沒另外用 `ps eww` 查 |
| E9 | 同一程序「打開小算盤」→「把它關掉」 | 第二句 `route=cloud`、`memory_turns=1`，執行 `quit Calculator`，小算盤確實關閉 |
| 雲端看畫面 | 「把螢幕上的內容翻譯成中文」 | `route=cloud`；截圖 17,589 bytes（JPEG q80）；雲端看圖 2.6 s；`vision_cloud` 只記 model／status／ms／token，不含圖片 |

### 13.6 尚未驗證／已知限制

- **語音實機**（麥克風＋`--ui` 桌面角色）沒有測：U1–U3 要使用者判斷（雲端回覆品質與語氣、控制句有沒有變慢的感覺、看畫面解說有沒有用）。`--ui` 的泡泡只是顯示 `result` 事件的 `replies`，UI 協定沒改，但沒實際看過 `☁️` 回覆在泡泡裡的樣子。
- **雲端回覆偶爾偏長或夾簡體字**：已加提示詞（表情符號最多一個）與 `适→適`，但 `s2t` 表不可能涵蓋全部簡體字；沒有量化。
- **規劃的非確定性**：同一份語料兩次執行 `ctrl` 39 與 40／43、`mixed` 18 與 19／19；門檻（`ACCURACY_MIN` 0.90）留了餘裕，但單次失敗不代表退步。
- **`.env` 權限目前是 644**（執行時會警告）：我沒有改你的 `.env`，請自行 `chmod 600 .env`。
- 費用：本次開發期間約 3 次完整 `-m cloud`＋探測，總計約 500 次請求；LiteLLM 的計費方式未知，沒有做用量上限（§9.8）。
- 看圖逾時偶發（第一次 15 s）；`CLOUD_TIMEOUT_S=8` 對規劃夠用（p95 2.1 s），但網路差時會退回本機（約多 2–3 秒）。
- 本機規劃沒有附記憶（L5 未量）；「它」在本機退回路徑只能靠對象守門擋錯，不能解析。
- P4（串流、桌面角色顯示）未做。
- 未 commit（依規則等你要求）。

## 14. 後續修正：簡體字、開網站、回覆長度（2026-11-16）

### 14.1 問題分析（實測）

| 問題 | 成因 | 類型 |
|---|---|---|
| 回覆夾簡體字 | 模型產生文字時本身會混字（推理內容裡也有）；`to_traditional` 只有 117 個詞的對照表。提示詞寫明「不可夾雜簡體字」只把殘留從 9 句降到 7 句（61 句） | 流程 |
| 回覆偏長 | 「最多三句話」限不住長度：61 句聊天 59–66% 超過 60 字、最長 247 字、2–4 句含換行 | 提示詞 |
| 「打開 YouTube」被拒 | `_search_intent_error` 只認「搜尋字詞」或「字面網址」，不認詞庫的網站名稱；12 種開網站說法 9 種被拒，但雲端回的網址每次都對。Fast Path 也沒有開網站的規則，要先送 LLM | 流程 |
| 洩漏的思考內容 | 實測發現：有一句回覆是「```thinking …英文推理… ```」加上重複兩次的答案（685 字），`clean_text` 沒涵蓋這個格式 | 流程 |
| proxy 回應快取 | LiteLLM 對相同請求逐字回傳同一份答案（`x-litellm-cache-key` 標頭；1323→426→400 ms、內容全相同），重複量測不是獨立樣本；`cache:{no-cache:true}` 放在 body 可繞過，header 的 `Cache-Control` 無效 | 量測／產品 |

### 14.2 決策與實作

| # | 內容 | 檔案 |
|---|---|---|
| 1 | 簡轉繁改用 macOS ICU（`CFStringTransform`，ctypes）：只換 **Big5 編不出來的字**＋一份精選的 **Big5 編得出來但只有簡體才這樣寫的字**（离、体、气、网…共 50 多個，逐字用 ICU 驗證）；歧義字（后、干、于、云、斗、几、采、范…）一律不動。獨立模組而不放進 `native.py`（不被輔助使用 API 載入失敗牽連）。`clean_text` 順序：去思考區塊（`<think>`、**新增 ```thinking**）→ 合併連續重複段落 → 詞庫 `s2t` → ICU | `mac_ai/router/hant.py`（新）、`cloud_client.py` |
| 2 | 守門放行開網站（`site_open_home`）：**只放寬原本會拒絕的**；條件全部成立才放行——沒有搜尋字詞、不是資訊提問、有開啟動詞、只明說一個詞庫網站（名稱／別名，「YouTube Music」不算）、query 的網域屬於該網站；放行時**改成網站首頁**（不用模型給的深層網址） | `slow_path.py`、`context.py`（`site_in`／`site_exact`） |
| 4 | Fast Path `_r_site`：整句「動詞（＋瀏覽器）＋網站名稱（＋網站／首頁／官網）」；名稱必須整段等於詞庫名稱或別名；同名 App 先於網站（「開 Google」＝Chrome），說「網站」則開網站；純比對、不讀前景。詞庫 `sites` 新增 `home`、`aliases`（YouTube：油管、YT；Facebook：臉書、FB；Google：谷歌、估狗；GitHub：無） | `fast_path.py`、`vocab/lexicon.json` |
| 3 | 雲端提示詞：「每則回覆只有一行、總長不超過 **50** 個字（含標點與英文）；最多三句；技術問題只講一個重點，不舉例、不列點、不貼程式碼」＋「臺灣正體字、不可夾雜任何簡體字」。寫 50 而不是 60：模型會超過約兩成，50 才留得出餘裕 | `cloud_client.py` |
| — | `config.CLOUD_NO_CACHE`（預設 False，日常維持快取；實測檔自動開）；驗收腳本加回覆品質指標與變差規則（殘留簡體 > 0、超過 60 字的比例上升 > 10 個百分點）；實測檔重構：規劃語料只跑一次、同時產生規劃正確率與回覆品質；單句請求失敗不中斷整組 | `config.py`、`scripts/acceptance.py`、`tests/test_cloud_live.py` |

### 14.3 提示詞 A/B（完整規劃語料 130 句、關快取、每版 2 次；全部為實際執行）

| 版本 | 超過 60 字（≤20%） | 最長（≤120） | 超過三句（≤10%） | 換行（0） | 規劃正確（總） | 殘留簡體 |
|---|---|---|---|---|---|---|
| A 現行（「最多三句話」） | 66%、62% | 247、185 | 20%、30% | 4、2 | 130、130 | 0、1（「嘞」，量測誤判，已修） |
| B60（一行、不超過 60 字） | 18%、25% | 93、**685（洩漏思考）** | 11%、7% | 0、2 | 129、130 | 0、0 |
| C60（更強的措辭，60 字） | 20%、18% | 160、86 | 7%、8% | 0、0 | **126**、129（mixed 16/19） | 0、0 |
| **C50（更強的措辭，50 字）＝採用** | 13%、18% | 129、86 | 8%、5% | 0、1（使用者要的四行短詩） | 129、129（同一句：亮度調回正常，模型做不到） | 0、0 |

第一輪（B）、第二輪（C）後依計畫停止調整。**C50 沒有達到「每次執行都過全部門檻」**（一次最長 129 字＝列歌單、一次 1 句換行＝詩），但其餘指標比 A 好很多；依 K9 採用，並如實記錄這個未全數達標。最終驗收那一次的結果見 §14.5。

### 14.4 自動化測試（全部實際執行）

| 驗收 | 結果 |
|---|---|
| 全部回歸 `pytest -q`（改動前 1560 passed／5 skipped） | **1663 passed、5 skipped、0 failed** |
| 項目 1：`pytest -q tests/test_cloud_offline.py -k "hant or clean_text or hot_paths or docs_are or residual"` | 通過：實測漏出的 18 個簡體字全部轉對；「退回路徑」「皇后」「于右任」「云林」「干貝」「北斗」「茶几」等繁體／歧義字原樣不變；ICU 失敗、長度不符、例外、載入失敗都回傳原文；沒有簡體字時不呼叫 ICU；100 字約 0.1 ms；1672 行繁體文件逐行轉換只動到含簡體字的行（說明簡體字的例子）；`normalize`／`completeness` 跑完錯字語料 0 次呼叫 |
| 項目 2：`-k "site_open or negated_site or context_site"` | 27 通過：12 種開網站說法放行且改成首頁；11 種該擋的仍擋（糾正句、沒說網站、網域不在詞庫、模型給別的網站、網域後綴偽裝 `youtube.com.evil.com`、資訊提問、沒有開啟動詞、YouTube Music、兩個網站、query 不是網址）；有搜尋字詞與字面網址的結果完全不變；雲端與本機送出的指令相同；否定句 0 次模型呼叫 |
| 項目 4：`tests/test_fast_offline.py -k site` | 43 通過：22 種說法 Fast 命中（含別名、全形、填充詞、疊詞）且 LLM 一被呼叫就失敗；17 種不該命中的不命中；同名 App 優先；`match()` 不呼叫 `subprocess`；對象優先順序（前景 Safari／Finder／讀不到／同句剛開 Chrome）；`step["intent"]` 記 `domain=web, source=site` |
| 端點偵測與錯字語料 | 新增 10 句完整度案例、6 句錯字案例，全部通過 |
| 變異測試（暫時弄壞邏輯，測試應變紅；還原後全綠） | 不要求網站名稱出現（4 項變紅）、不要求開啟動詞（2）、不改成首頁（8）、Fast 不讓給同名 App（4）、Fast 不要求整段等於名稱（3）、簡轉繁改整句轉換（1） |
| 驗收腳本 `pytest -q tests/test_acceptance_offline.py` | 19 通過（含回覆品質欄位與變差規則） |

### 14.5 實機測試（全部實際執行）

| 驗收 | 結果 |
|---|---|
| 規劃＋回覆品質（`pytest -m cloud -s -k "planning or reply_quality"`，關快取） | **通過**：規劃 ctrl 42/43、screen 14/14、chat 42/42、screen_c 12/12、mixed 19/19；延遲 p50 0.96 s、p95 1.81 s；被守門拒絕 **0**（之前 1 句「幫我開瀏覽氣看 YouTube」）；請求失敗 0；回覆 n=61：字數 p50 37、最長 83、超過 60 字 8 句（13%）、超過三句 3、換行 0、殘留簡體 0 |
| 本機 live 不退步 | 最終驗收：86 passed、6 xfailed、0 failed（260 s），與改動前相同 |
| **最終驗收**（`python scripts/acceptance.py run --live --cloud --label final-sites-hant-prompt`，2026-11-16 15:11） | offline **1663 passed、5 skipped、0 failed**（37.8 s）；live **86 passed、6 xfailed、0 failed**（260 s）；cloud **6 passed、2 failed**（182 s）。雲端規劃 ctrl 43/43、screen 14/14、chat 42/42、screen_c 12/12、mixed **18/19**；延遲 p50 885 ms、p95 1663 ms（n=130，請求失敗 0）；回覆 n=61：超過 60 字 5 句（8%）、最長 78、超過三句 2、**換行 1**、殘留簡體 0；代名詞 30/30；繞過 G1 時雲端誤規劃 1/7（僅記錄）；路由 p50 0.02 ms |
| 最終驗收的 2 個失敗（原因與處置） | ① `test_image_input_is_understood`：**測試判準太窄**。同一張圖連問 8 次（關快取），7 次說「型別錯誤」、1 次說「**類型**錯誤」（同義且正確）；判準只認「型別」。已改為接受「TypeError／型別／類型」，改後單跑通過（1.6 s）。② `test_reply_quality_…`：**門檻「換行 0」沒有達成**——最終驗收 1 句、緊接著單獨重跑 1 句換行；再多問 122 次（61 句×2）0 句換行。C50 各次實測 6 次執行有 3 次出現 1 句換行（約 0.8%，模型自己偶爾分行，提示詞擋不住）。**這項沒有被我放寬，等你決定**（見最終報告） |
| 2-E1 真實環境：「去 YouTube 看影片」 | `route=cloud`，`web_search_or_open_url({'query': 'https://www.youtube.com'})` → ✅，988 ms（放行守門） |
| 4-E1 真實環境：前景 Safari「打開 YouTube」 | Fast Path 92 ms、整步 177 ms；`step["context"]={'app': 'Safari', 'used': True, 'ms': 10.3}`，送出 `open -a Safari https://www.youtube.com` |
| 4-E1 前景 Finder「開油管」 | Fast Path 141 ms、整步 210 ms（< 150 ms 門檻） |
| 守門 | 「不要開 YouTube」→ 0 次模型呼叫、不動作；「打開 YouTube Music」→ 雲端選了 App，因沒安裝被拒，沒有開網頁 |
