# mac_ai_control

在 Mac 上用語音控制電腦的語音助手。直接說出指令，就能開啟或關閉 App 等操作（預設常啟動，不需喚醒詞）。語音辨識與常用指令都在本機執行；聊天、知識問答、複雜畫面與「更強的 AI 控制」可選用雲端模型（`.env` 填金鑰才啟用，沒填就完全在本機，見下方「雲端模型」）。

- 範例：「打開小算盤」「關掉 Safari」
- 喚醒詞（終端機模式預設關閉）：**露西婭**（別名：露醬、露西亞）。要啟用，把 `mac_ai/config.py` 的 `WAKE_WORD_ENABLED` 改成 `True`，之後須說「露西婭 打開小算盤」才會執行（喚醒詞前可以加一個招呼詞，如「嘿露西亞」，詞表在 `vocab/lexicon.json` 的 `wake_words.greetings`）。喚醒後進入 60 秒的**會話**（`WAKE_SESSION_S`）：期間每一句都不用再說喚醒詞，每說一句重新計時；逾時後要再喊一次
- 桌面角色（`python main.py --ui`）：Rushia（Live2D）常駐螢幕右下角，平常從下緣探頭，喚醒時出場、依執行結果做動作並以泡泡顯示；可用快捷鍵叫出輸入框打字；點角色可開「聊天室」視窗，看圖、聊天等長回覆會自動展開。此模式預設啟用喚醒詞。見下方「桌面角色」

## 運作方式

```
麥克風 ──▶ macos-stt（系統語音辨識，WebSocket :8765）──▶ 喚醒詞閘門
                                                          │
                                  ┌───────────────────────┴───────────────────────┐
                                  ▼                                               ▼
                    Fast Path：詞典／規則直接比對                 Slow Path：LLM 規劃工具呼叫
                    （不需 LLM，LLM 掛掉仍可用）                  雲端優先（有金鑰時），失敗退回本機
                                                                  （本機：mlx_vlm.server，HTTP :8080）
                                  └───────────────────────┬───────────────────────┘
                                                          ▼
                                                  執行動作（開啟／關閉 App 等）
```

- **語音辨識**交給 [macos-stt](#感謝)，它會呼叫 Apple 內建的 `SFSpeechRecognizer`，不需下載模型。也可以改用本機的 X-ASR（備用引擎，比較不準，Windows 的預設），見[備用辨識引擎](#備用辨識引擎x-asrwindows-預設)。
- **語言模型**使用 `mlx_vlm.server` 載入的本地模型（預設 `AI_models/LFM3-VL-2B-MLX-4bit`）。
- **雲端模型**（選用）：Slow Path 有金鑰時先問雲端（OpenAI 相容的 LiteLLM proxy），失敗、逾時、熔斷時同一步退回本機模型。雲端與本機用同一套驗證，雲端也不能繞過否定／敘述句守門；雲端另外多兩個工具：對目前視窗執行常用功能（「再往下」）與站內搜尋。
- 模型不可用時，Fast Path 仍能執行常見指令。

## 需要搭配 macos-stt

本專案的語音辨識**依賴另一個子專案 `macos-stt`**，兩者必須放在同一個目錄下：

```
mac_ai_control/
├── main.py
├── mac_ai/
├── ...
└── macos-stt/          ← 另外 clone，自帶 .venv，本 repo 不包含
```

原因：macOS 要求語音辨識程式必須是 `.app` bundle 才能取得授權，所以 `mac_ai` 會透過 `macos-stt/start.py serve` 啟動它，而不是直接 import。`mac_ai/config.py` 會去找 `macos-stt/.venv/bin/python`。

## 備用辨識引擎（X-ASR，Windows 預設）

macos-stt 只能在 macOS 用。為了讓語音辨識這一層可以在 Windows 執行，另外提供一個本機的備用引擎：`mac_ai/stt/xasr_server.py` 用 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 跑 [Luigi/x-asr-zh-tw-native-itn](https://huggingface.co/Luigi/x-asr-zh-tw-native-itn)（160M 的串流 Zipformer，繁體輸出，int8 ONNX），並說跟 macos-stt 一樣的 WebSocket 協定，所以端點偵測與路由都不用改。完全在本機執行，不連網。

**它比 macos-stt 不準**。同一份真人語料（`bench/human`）的實測：

| 項目 | macos-stt | X-ASR |
|---|---|---|
| A+C+F 類（指令）意圖正確率 | 24/24 | 22/24（92%） |
| 字錯率（CER） | 2.6% | 10.0% |
| A 類說完到收到結果（中位／p90） | 624／952 ms | 739／1370 ms |
| 辨識伺服器 CPU（整機百分比，8 核心機器） | 跑 bench 期間 0.8% | 跑 bench 期間 3.4%；閒置（麥克風開著沒說話）3.9% |
| 記憶體 | 77 MB | 約 470 MB |

已知的缺點：英文 App 名稱會有辨識錯字（例如 Safari 被聽成「沙發率」）；環境雜音可能被聽成短字（例如「Sh」）而送進 Slow Path；講「螢幕」偶爾被聽成「熒幕」（已由詞庫矯正）或「一幕」（沒有矯正）。macOS 預設仍是 macos-stt，只有明確指定才會用 X-ASR。

**授權**：模型是 Apache-2.0；它是用 `GilgameshWind/X-ASR-zh-en` 與 k2-fsa/icefall 做的。模型權重不放在本 repo，請自行下載。

**安裝**（只有用 X-ASR 才需要）：

```bash
pip install sherpa-onnx==2.0.2      # 內含自己的 onnxruntime，不影響主環境的 onnxruntime
mkdir -p AI_models/x-asr-zh-tw-native-itn
R=3d5bad21ccf54b37eef1fddccad48ca82001ddd9
B=https://huggingface.co/Luigi/x-asr-zh-tw-native-itn/resolve/$R/deployment/onnx-int8-480ms
for f in encoder.int8.onnx decoder.onnx joiner.int8.onnx tokens_traditional.txt; do
  curl -L -o AI_models/x-asr-zh-tw-native-itn/$f $B/$f
done
shasum -a 256 AI_models/x-asr-zh-tw-native-itn/*     # Windows 用 Get-FileHash -Algorithm SHA256
# encoder.int8.onnx        908596dcc137a73b95be908ca55e88caa1b3dbbe8027c171615f4b0609c5eb1e
# decoder.onnx             a1cbc9eac2d5e3fb6617a218c67ad6daaa7f4e0fd225f08b2c22ab0413c8c257
# joiner.int8.onnx         aedb7fa697b2ab43f20499826fff7c997eea7d67db77be97769aeeeb726e63b3
# tokens_traditional.txt   62b7a97bd385dd84835e4e627820ca3ca69058502dfbd80108c6a88c94ecdd90
```

共約 170 MB。其餘需要的套件與 macOS 相同（`sounddevice websockets onnxruntime numpy`）。缺少模型檔時，啟動會直接列出缺哪個檔，不會默默退回別的引擎。

**使用**：

```bash
python main.py --stt-engine xasr         # macOS 上手動選用；Windows 不帶參數就是 xasr
python -m mac_ai.stt.xasr_server         # 只起辨識伺服器（127.0.0.1:8765），例如跑 scripts/stt_bench.py
```

**Windows 的狀態（請先看這段）**：沒有 Windows 實機測試過。語音辨識這一層是在 macOS 上模擬 Windows 驗證的（只裝 Windows 版會裝的套件的乾淨環境、把 `sys.platform` 改成 `win32`、確認 `win_amd64` 的安裝檔存在）。本專案其餘部分（執行 macOS 動作、`mlx_vlm` 本機模型、前景視窗、桌面角色的 macOS 專用處理）不能在 Windows 跑，Windows 全流程不在目前範圍。麥克風驅動（WASAPI）、主控台編碼（cp950）、防毒軟體擋 8765 埠、Windows 版 sherpa-onnx 的實際速度，都還沒有驗證。

## 環境需求

- macOS（Apple Silicon，`mlx` 需要）
- Python 3.12（開發時使用的版本）
- [uv](https://docs.astral.sh/uv/)（建議，用於建立 `macos-stt` 的虛擬環境）

## 安裝

### 1. Clone 本專案與 macos-stt

```bash
git clone <本專案 URL> mac_ai_control
cd mac_ai_control
git clone https://github.com/young878787/macos-stt.git macos-stt
```

### 2. 安裝 macos-stt 的環境

```bash
cd macos-stt
uv sync
cd ..
```

### 3. 安裝本專案的環境

本專案目前沒有 `requirements.txt`，開發時使用的主要套件如下：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install mlx-vlm sounddevice websockets pytest onnxruntime numpy alembic
```

開發時的版本：`mlx-vlm 0.7.11`、`sounddevice 0.5.6`、`websockets 17.4`、`pytest 9.2.0`、`onnxruntime 1.31.1`、`numpy 2.5.4`、`alembic 1.20.1`（連帶 `SQLAlchemy 2.1.9`、`Mako 1.4.3`；只在啟動時準備點歌偏好資料庫用，之後可以移除，見 [0019](docs/decisions/0019-music-play.md)）。若虛擬環境是用 `uv` 建的（沒有 `pip`），改用 `uv pip install` 安裝同一組套件。

### 4. 準備語言模型

模型權重不在 repo 內（目前 `AI_models/` 已加入 `.gitignore`），請自行放到：

```
AI_models/LFM3-VL-2B-MLX-4bit/
```

若改用其他模型，請同步修改 `mac_ai/config.py` 的 `MODEL_ID`。

### 4-0. 雲端模型（選用）

```bash
cp .env.example .env && chmod 600 .env    # 然後編輯 .env，填入 AI_API_KEY
```

| 鍵 | 說明 |
|---|---|
| `AI_API_URL` | OpenAI 相容端點，必須是 `https://`（預設 `https://chatapi.ntubimdbirc.tw`） |
| `AI_MODEL` | 模型名稱（預設 `BIRC-LLM`） |
| `AI_API_KEY` | 金鑰；**留空＝不使用雲端**，完全在本機執行 |
| `BRAVE_API_KEY` | 選用：[Brave Search API](https://api-dashboard.search.brave.com) 金鑰。**要和 `AI_API_KEY` 一起填才啟用長尾問題直接回答**；留空＝這類問題照舊（雲端憑記憶回答或開搜尋網頁）。按次計費，用量上限請在 Brave 後台自己設 |
| `CWA_API_KEY` | 選用：中央氣象署開放資料授權碼（免費）。**有填＝天氣先查氣象署，失敗改查 Open-Meteo；留空＝只用免金鑰的 Open-Meteo** |

- `.env` 不入版控，程式只讀不寫；金鑰不會放進環境變數，也不會傳給 `mlx_vlm`、STT、Electron 子程序，紀錄與錯誤訊息都不含金鑰。
- 會送到雲端的內容：Slow Path 的原句、前景 App 名稱、最近 5 輪對話摘要（60 秒內，含執行過的工具）；說「看畫面並翻譯／解釋／除錯」這類由雲端規劃的看畫面，會把縮小的**螢幕截圖**送到雲端（`config.CLOUD_VISION_ENABLED` 可關閉）。Fast Path 的指令、否定／敘述句（G1）、本機搜尋後轉交的判斷不送雲端。長尾問題直接回答（有 `BRAVE_API_KEY` 時）另外會：把搜尋關鍵字（只有關鍵字，不含原句）送到 Brave、把搜尋結果的標題與摘要（不含網址）送到雲端。
- `config.CLOUD_ENABLED = False` 可整個關閉雲端。啟動時會印一行雲端狀態。

### 4-1. 準備語音活動偵測模型（Silero VAD）

端點偵測（`ENDPOINT_MODE = "client"`）與測試會用到 Silero VAD v5（MIT 授權，約 2.3 MB）。同樣因為 `*.onnx` 已加入 `.gitignore`，請自行下載：

```bash
curl -L -o AI_models/silero_vad.onnx \
  https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/src/silero_vad/data/silero_vad.onnx
shasum -a 256 AI_models/silero_vad.onnx
# 應為 2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f
```

沒有這個檔案時，預設的 `ENDPOINT_MODE = "client"` 會在啟動語音迴圈時因找不到模型而失敗；此時請把 `mac_ai/config.py` 的 `ENDPOINT_MODE` 改成 `"server"`（舊行為，不需要 VAD 模型），VAD 相關測試則會被略過。

### 5. 系統權限

第一次執行時，macOS 會要求以下授權，請在 **系統設定 → 隱私權與安全性** 中允許：

- **麥克風**：終端機（或你執行的 IDE）需要麥克風權限
- **語音辨識**：允許 `MacSTT`
- **輔助使用**、**自動化**（Safari／Google Chrome、System Events）：亮度、鎖定螢幕、分頁、「目前視窗」的常用功能與站內搜尋會用到

若曾拒絕授權，請到上述位置改回允許後重新啟動。

## 使用方式

```bash
source .venv/bin/activate

python main.py                    # 啟動所有服務並開始收音（Ctrl+C 結束）
python main.py --no-stt           # 只啟動 LLM 服務，不收音
python main.py --text 打開小算盤  # 直接送入文字指令後結束（不檢查喚醒詞）
python main.py --device 1         # 指定麥克風裝置編號
python main.py --ui               # 同時顯示桌面角色（需先完成「桌面角色」一節的安裝）
python main.py --stt-engine xasr  # 語音辨識改用備用引擎 X-ASR（見「備用辨識引擎」；預設 macOS 用 macos-stt）
```

啟動後會依序啟動 LLM（`mlx_vlm.server`）與 STT（`macos-stt serve`，或 `--stt-engine xasr` 時的 X-ASR 伺服器），等兩者就緒後開始收音。

### 找 App、資料夾、檔案

「找」「搜尋」「打開」會先判斷**範圍**（App、專案、資料夾、檔案、網頁），再決定動作，本機優先：

| 說法 | 結果 |
|---|---|
| 找一下 ChatGPT／打開郵件 | 開啟已安裝的 App（也認系統語言的名稱，如「郵件」「計算機」） |
| 打開下載資料夾／找桌面 | 在 Finder 開啟資料夾 |
| 打開 YouTube／開油管／上 GitHub／開瀏覽器看 Facebook | 開網站首頁（Fast Path，不經 LLM）。網站與別名在 `vocab/lexicon.json` 的 `sites`；同名 App 已存在時 App 優先（「開 Google」＝Chrome），說「網站／首頁／官網」則一律開網站 |
| 找 report.pdf／找昨天下載的 PDF／找桌面上的截圖 | 用 Spotlight 搜家目錄（排除 `~/Library`、隱藏資料夾、`node_modules`），預設**在 Finder 中顯示**；說「打開」才開檔，可執行檔（`.command`、`.sh`、`.app`…）一律只顯示 |
| 找履歷、找 python 教學（沒有本機證據） | 先當檔名找；0 筆時轉交模型判斷，可能改開網頁搜尋（搜尋關鍵字用你說的原話，不用模型改寫的字）；模型也判斷不用查、或模型沒開，就回「找不到」並說明原因與下一步（說「搜尋 X」） |
| 找 report.pdf、在電腦裡找履歷、找下載的履歷（有本機證據） | 0 筆時只回「本機沒有」，不會改開網頁 |
| 找到了、找不到我的鑰匙、找最近的電影 | 不是找檔案：直接交給模型判斷 |
| 搜尋台北天氣／網路上找 Xcode／找台北天氣 | Google 搜尋（`搜尋`、`查` 沒帶「電腦裡」「App」「檔案」之類範圍詞時維持網頁搜尋） |

找到多筆時列出前 5 筆（60 秒內有效）：說「第二個」「打開第三個」「在 Finder 顯示第一個」「最後一個」選擇；桌面角色會顯示清單，點整列執行預設動作，點列尾按鈕執行另一個動作。範圍判斷的結果會記在每一步的 `step["intent"]`，判斷錯了可以從這裡追查。

### 以目前視窗為對象

句子沒指定對象時，用**最前面的視窗**決定（順序：句子明說的 → 同一句剛開的 → 最前面的視窗 → 預設）：

| 最前面是 | 說法 | 結果 |
|---|---|---|
| Safari／Chrome 停在 Facebook、YouTube、GitHub、Google | 查貓咪專頁 | 在這個瀏覽器用該網站的站內搜尋（開新分頁）；說「上網查」「google 一下」或查天氣、新聞等即時資訊仍用 Google |
| Safari／Chrome 其他網站 | 查貓咪／找 python 教學（本機 0 筆、沒有本機證據） | 在這個瀏覽器 Google 搜尋；「找」不再先問模型 |
| VS Code／Cursor | 開 X 專案 | 用最前面的編輯器開 |
| VS Code／Cursor | 開終端機／關終端機 | 切換內建終端機面板；說「終端機程式」「獨立終端機」「Terminal」才開 Terminal.app |
| Terminal／iTerm | 新分頁、清除畫面 | ⌘T、⌘K |
| 任何 App | 關閉視窗、開新視窗、最小化、全螢幕、放大、縮小 | 直接對最前面的 App 操作（按視窗的關閉鈕、設定最小化／全螢幕、按 App 自己的選單項目） |
| 瀏覽器 | 上一頁、下一頁、回到頂端、往下捲、往上捲 | 同上 |
| 編輯器 | 開側邊欄、存檔 | 同上 |
| YouTube、Music、Spotify | 播放、暫停、繼續、下一首、上一首 | YouTube 按播放器的按鈕；Music／Spotify 用 App 自己的指令。「暫停」「繼續」有方向（已經是那個狀態就不動作），單說「播放」是切換 |

網站與常用功能都定義在 `vocab/lexicon.json` 的 `sites`、`app_shortcuts`。常用功能以輔助使用介面直接操作，不經過鍵盤，所以不受中文輸入法影響；只有沒有選單項目的翻頁鍵才送按鍵。說法前後多了「再／多／繼續／稍微」「一點／多一點」也算（「再往下滑」「往下滑一點」）；其他說法（「再往下」）由雲端依目前視窗與最近對話判斷。功能不支援最前面的 App 時什麼都不做，只回報。需要「輔助使用」權限。判斷結果記在每一步的 `step["context"]`（只記 App 與網站 host，不記完整網址與視窗標題）。

已知限制：讀網址只支援 Safari 與 Google Chrome；選單依快捷鍵或標題尋找，App 改了快捷鍵或介面語言不在詞庫列出的名稱時，會退回送按鍵或回報找不到。

### 點歌與播放控制

說一句話就在 Safari 用 YouTube 播放（Fast Path，不經 LLM；Safari 以外的瀏覽器只有離線測試）：

| 說法 | 結果 |
|---|---|
| 我想聽周杰倫的晴天／播放稻香／放一首告白氣球／來首五月天／聽五月天的歌／五月天的歌來一首 | 搜尋後直接播放（YouTube 合輯，播完接著播）；有音樂分頁就換它的網址，沒有才開新分頁 |
| 我想聽歌／放音樂／來一首 | 播常聽的歌（偏好資料庫沒有紀錄時會請你說歌名） |
| 下一首、上一首、暫停、繼續 | 前景是 YouTube、Music、Spotify 時直接操作；否則控制 Safari 背景的 YouTube 音樂分頁（先按播放器按鈕，按不到才送系統媒體鍵；找不到音樂分頁回「找不到正在播放的音樂」） |
| 換一首／不是這首 | 改播同一次搜尋的下一個候選，並記住 |
| 我喜歡這首／不要再放這首 | 記下偏好（不要再放的歌不會被「常聽的歌」選到） |
| 我最常聽什麼 | 列出最常聽的歌手與歌曲 |

否定、敘述、提問不會觸發（「我不想聽這首」「播放清單在哪」「聽我說」）；「聽說明天會下雨」「他想聽周杰倫」會交給模型判斷。搜尋用 YouTube／YouTube Music 網頁用的內部介面（InnerTube），不需要金鑰、不扣配額；搜尋失敗就改開搜尋頁並回報。只說歌手（「我想聽五月天」）會播 YouTube Music 上該歌手的第一首熱門歌曲，接著自動播合輯。偏好（聽歌紀錄、說法矯正、搜尋快取）存在本機 `data/music.sqlite3`：永久保存、不入版控、第一次啟動自動建立；結構與取捨見 [0019](docs/decisions/0019-music-play.md)。
語音實機辨識歌名（H5）還沒有驗收；有聲自動播放、背景控制、媒體鍵（H1～H4）由使用者判定通過、沒有逐項實際執行，見 `docs/plans/completed/2026-11-21-music-play.md` §9。

### 天氣

問天氣直接在泡泡回答，不開網頁（Fast Path，不經 LLM）：

| 說法 | 結果 |
|---|---|
| 台北天氣／今天高雄天氣／明天台中會下雨嗎／今晚新北幾度 | 回答今天白天、今晚、明天白天、明晚其中一段：天氣、溫度範圍、降雨機率，結尾標示來源 |
| 今天天氣／會下雨嗎 | 沒說縣市時，用你最近最常問的縣市；還沒問過任何縣市就請你說一下縣市 |
| 搜尋台北天氣／打開瀏覽器查台北天氣 | 照舊開網頁（明說要看畫面） |

資料來源：有填 `CWA_API_KEY` 先查中央氣象署，失敗或逾時改查 Open-Meteo（免金鑰）；兩個都失敗就回「查不到」，不開網頁。否定、敘述句（「不要查天氣」「昨天天氣很好」）不會觸發；後天、週末、下週還沒支援。常問縣市只記你**明說**的縣市，存在本機 `data/music.sqlite3`。結構與取捨見 [0021](docs/decisions/0021-weather-answer.md)。

### 長尾問題直接回答

有 `AI_API_KEY` 與 `BRAVE_API_KEY` 時，Fast Path 沒命中的問句由雲端決定要不要上網查，查到就在泡泡回答（不開網頁）：

| 說法 | 結果 |
|---|---|
| 周杰倫是誰／湖人最近一場比賽比數多少／全聯營業到幾點／最近有什麼新聞 | 搜尋後由雲端依摘錄回答 1～2 句，結尾問「要打開來源嗎？」 |
| 好／要／打開來源（回答後 60 秒內，整句只有這樣） | 開第一筆來源網頁 |
| 在 Google 搜尋周杰倫是誰／幫我上網看一下… | 明說要看畫面，照舊開網頁 |

搜尋、雲端或摘錄裡沒有答案時，退回開 Google 搜尋頁並回 ⚠️，不謊稱查到。名詞片語（「故宮博物院開放時間」）沒有疑問，維持開網頁；Fast Path 命中的「查一下周杰倫是誰」「搜尋周杰倫是誰」也維持開 Google。搜尋摘錄只放在記憶體：不寫紀錄、不存資料庫（Brave 條款）。決策見 [0022](docs/decisions/0022-web-answer.md)。

## 桌面角色

`--ui` 會另外啟動一個 Electron 視窗（`desktop/`）：透明、置頂、可穿透點擊（只有角色身體與泡泡會吃到滑鼠），出現在所有桌面空間。後端與視窗用本機 WebSocket（`127.0.0.1:8766`，連線需帶啟動時產生的 token）溝通；沒加 `--ui` 時完全不會啟動這些東西。

### 安裝

```bash
cd desktop
npm install                                   # electron、pixi.js、pixi-live2d-display（版本已鎖定）；typescript、@types/node 只用在 npm run typecheck
ls node_modules/electron/dist >/dev/null 2>&1 || node node_modules/electron/install.js   # npm 沒有下載到 Electron 執行檔時補這行
```

另外兩樣東西不入版控，需自行準備：

- **Live2D Cubism Core**：到 [Live2D 官網](https://www.live2d.com/en/sdk/download/web/) 下載 Cubism SDK for Web，把 `Core/live2dcubismcore.min.js` 放到 `desktop/vendor/`（專有授權，不可散布）。Core 5.x 與 6.x 都可用；6.x 會由 `desktop/renderer/engine/compat/render-orders.js` 的 `bridgeRenderOrders` 補上 pixi-live2d-display 0.4.0 讀不到的繪製順序。
- **模型**：`Rushia_hd/runtime-fullrig03/RushiaHD.model3.json`（路徑在 `mac_ai/config.py` 的 `UI_MODEL_PATH`）。

`python main.py --ui` 啟動時會檢查以上三樣，缺什麼就印出怎麼補。

### 操作

| 操作 | 效果 |
|---|---|
| 說喚醒詞 | 角色出場、頭上出現會話光環（倒數環）；60 秒內每句免喚醒詞、每句重新計時 |
| `⌥Space`（可在 Electron 的 `userData/settings.json` 的 `shortcut` 改） | 叫出／關閉輸入框，Enter 送出（不用喚醒詞）、Esc 關閉；收音時 partial 以灰字顯示在輸入框裡 |
| 點頭部 | 出場並開啟會話，並彈出快捷泡泡「💬 聊天室」；點身體：開心動作（同樣彈出快捷泡泡）。再點一下角色、5 秒沒操作會收起 |
| 點「💬 聊天室」／右鍵「聊天室」 | 開關聊天室視窗（手動開啟不會自動收）；視窗內可看完整對話、展開每一步的狀態／路線／耗時，多行輸入框 Enter 送出、Shift+Enter 換行 |
| 拖曳 | 只能沿螢幕下緣左右移動，位置會記住 |
| 右鍵 | 聊天室／暫停聽指令／結束 |
| 點搜尋結果清單的一列 | 執行清單的預設動作（顯示或打開）；列尾按鈕執行另一個 |

不用麥克風也能預覽演出：`python scripts/ui_demo.py`（`--hold` 播完後保持執行，可手動操作；`--choices-test` 驗證搜尋結果清單；`--chat-test` 驗證聊天室）。效能量測：`python scripts/ui_perf.py`；動作軌跡指標（無機械感、聆聽專注）：`python scripts/ui_motion.py`（量測期間不要動滑鼠）。

### 注視與聆聽（擬人化）

出場時頭、身體、眼睛、眉毛由程序層接手（`desktop/renderer/animation/`），原生 Idle 動作只保留手臂、肩、呼吸與嘴；反應動作（點頭、搖頭、說話…）播放時程序層把頭身讓給動作，交還時不跳。注意力導演（`desktop/domain/attention.js`）依情境決定看哪裡，優先順序：

| 情境 | 角色的反應 |
|---|---|
| 聆聽（聽到喚醒詞或會話中說話） | 立刻正視使用者、動作變小、眨眼變少；收到指令時輕點一下頭 |
| 打字（輸入框 2 秒內有輸入） | 看輸入框 |
| 點擊角色 | 看點擊的位置約 1 秒 |
| 思考（執行中） | 視線往上側移開，在那一帶小幅掃視 |
| 回應（結果出來、反應動作播放中） | 看使用者；說話開頭先移開視線一下 |
| 游標（明顯移動） | 眼睛先動、頭跟一點；游標停 1.5～3 秒就失去興趣 |
| 閒置 | 隨機的劇本（看旁邊、低頭、往上想、換重心、歪頭…），每次都不一樣 |

潛伏時回到中性並停格。要回到舊做法（Idle 全權＋一直看游標），把 `desktop/renderer/role/app.js` 的 `PROCEDURAL` 改成 `false`。設計、常數與實測見 [擬人化計畫](docs/plans/completed/2026-11-20-humanize.md)。

### 聊天室

平常不開，只用角色與泡泡。獨立視窗（白底淺藍、可縮放、貼在角色左側，放不下改右側），紀錄只放記憶體、重開就清空；視窗隱藏時主程序不推送紀錄（省 CPU），打開的瞬間才一次補上。

後端判斷這句需要看完整內容時，會在 `result` 事件帶 `expand`，聊天室就會帶動畫自動展開（不搶焦點，最前面的 App 不變），角色泡泡只放「詳細內容在對話視窗」：

| `expand` | 條件 |
|---|---|
| `screen` | 看畫面（截圖、讀螢幕）成功 |
| `chat` | 模型寫了回覆（本機 chat call，或雲端文字被採用） |
| `complex` | 一句話執行了 2 個以上動作（`chat_reply` 不算），或回覆超過 20 字（`config.EXPAND_MIN_ACTIONS`／`EXPAND_REPLY_CHARS`） |

主動展開後，滑鼠沒移上去、也沒點進去，20 秒後自動收回；有互動就一直開著，直到按 ✕ 或 Esc。沒有 `expand` 的指令（開 App、調音量…）不會打擾。

## 端點偵測（說完就收）

預設 `mac_ai/config.py` 的 `ENDPOINT_MODE = "client"`：本端用 Silero VAD 判斷何時開始／結束說話，並看辨識中的文字是不是一句完整的指令（完整就早點收、句尾是「然後」之類就多等），決定後主動要求 macos-stt 收尾。改成 `"server"` 即回到舊行為（macos-stt 的能量 VAD，靜音 1.5 秒才斷句）。每句的計時寫在 `logs/runtime/latency.jsonl`（各 log 的用途見下方「Logs」）。設計、參數與實測結果見 [docs/plans/completed/2026-11-05-endpoint-latency.md](docs/plans/completed/2026-11-05-endpoint-latency.md)。

量測與比較（需要 macos-stt 已啟動）：

```bash
python scripts/make_bench_corpus.py synth      # 產生合成測試語料到 bench/synth/
python scripts/stt_bench.py run --corpus bench/synth --mode server --label base   # 輸出自動命名並存到 logs/bench/
python scripts/stt_bench.py run --corpus bench/synth --mode client --label new
python scripts/stt_bench.py list                                                  # 列出所有 bench 的重點數字
python scripts/stt_bench.py report logs/bench/<base 檔> logs/bench/<new 檔>       # A/B 比較與驗收判定
python scripts/record_human.py                 # 錄真人測試語料（清單在 bench/human/manifest.csv）
```

## Logs

`logs/` 不入版控，依來源分資料夾：

| 位置 | 內容 | 寫入者 |
|---|---|---|
| `logs/runtime/latency.jsonl` | 每句話的收音→定稿計時，`utt_id`＝這句 final 的編號（`e2e_ms`＝說完→定稿、`endpoint_ms`＝說完→決定收尾、`finalize_ms`＝送 flush→定稿；`e2e_ms` 為 null＝本端 VAD 沒偵測到說話） | `stt/client.py` |
| `logs/runtime/commands.jsonl` | 每個指令的路由與執行結果（Slow 步驟另有 `route`＝cloud／local、`cloud`＝model／status／total_ms／token 數，不含回應內容與金鑰）：語音指令帶 `utt_id`（對應 latency 同一句）與 `queue_ms`（進佇列到開始執行的等待；UI 打字、清單點選沒有這兩欄）；各步 `kind`／`status`／`ms`／`intent`／`logs`、`stages`；家目錄以 `~` 表示，不含搜尋清單。`--text` 不寫 | `assistant.py` |
| `logs/runtime/normalize.jsonl` | STT 原文→正規化結果（只記詞庫造成的改動，用來回收誤聽詞） | `router/normalize.py` |
| `logs/runtime/ui.jsonl` | 桌面角色每則訊息一行：`bus_ms`（後端→Electron）、`apply_ms`（→畫面套用） | `desktop/main/telemetry.js` |
| `logs/services/*.log` | 子服務（`mlx_vlm`／`stt`／`ui`）的 stdout，每次啟動覆寫 | `services.py` |
| `logs/bench/*.jsonl` | `stt_bench.py run` 的輸出，檔名＝`日期_語料_模式[_標籤][_only-類別][_參數]`；最後一行 `meta` 記錄實際生效參數與 git commit | `scripts/stt_bench.py` |
| `logs/_legacy/` | 整理前的舊格式紀錄（唯讀備查，程式不會寫） | — |

`runtime/` 的每一行都有 `ts_iso`（本地時間）與 `run_id`（哪一次啟動，格式 `YYYYMMDD-HHMMSS`），三種紀錄可用 `run_id` 對起來，同一次啟動內 latency 與 commands 再用 `utt_id` 一句對一句；浮點數寫入時已四捨五入（`*_ms` 取 1 位）。`ui.jsonl` 另有 `conn`（第幾次連上匯流排）。

原始 JSONL 一行很長，要快速看請用檢視指令（唯讀）：

```bash
python scripts/log_view.py                   # 最近一次啟動：語音每句一行（收音計時＋指令執行＋說完→執行完）、其他指令、桌面角色延遲
python scripts/log_view.py --all             # 全部啟動；--run <run_id> 指定哪一次；-n 50 多列幾筆
python scripts/stt_bench.py list             # bench 一覽
```

## 測試

```bash
pytest              # 離線測試（預設，含錯字語料 tests/flow/test_typo.py）
pytest -m unit      # 只跑單元層（約 1.4 秒，改程式時的快速回饋）；另有 -m flow（整條路由）、-m runtime（真執行緒／socket）
pytest -m live      # 需要 mlx_vlm.server 已啟動並載入模型
python bench/web_answer.py --show   # 連真的 Brave 與雲端跑 20 題長尾問題（延遲、選工具、回答與摘錄是否一致；需要兩把金鑰，會產生費用）
pytest -m cloud -s  # 連真的雲端模型（需 .env 金鑰，約 200 次請求會產生費用）：規劃正確率／延遲、否定句、誠實、看圖、代名詞動作
pytest -m app -v    # 真實 App 驗收：在 Safari、Finder、Terminal、VS Code 上實際執行並讀回狀態（會佔用螢幕約 2 分鐘）
cd desktop && node --test   # 桌面角色的狀態機、聊天室紀錄、路徑白名單、分層規則與 IPC 頻道測試
cd desktop && npm run typecheck   # 桌面前端的型別檢查（JSDoc＋checkJs，不開 strict）
python scripts/acceptance.py run [--app] [--live] [--cloud]   # 跑測試並把數字存到 logs/bench/acceptance_*.json
python scripts/acceptance.py report                 # 和上一次比較，變差（新增失敗、矯正率下降、延遲 +20% 且 +20 ms；雲端：各類規劃正確率或代名詞正確數下降、規劃 p50 +30% 且 +300 ms）時結束碼 1
python scripts/acceptance.py list                   # 歷次結果
```

真實 App 驗收只操作測試自己開的視窗，結束時關掉；第一次跑會要求「自動化」「輔助使用」「螢幕錄製」授權，缺授權的項目會 skip 並寫明原因。跑的時候不要動鍵盤滑鼠。

## 專案結構

| 路徑 | 說明 |
|---|---|
| `main.py` | 啟動入口：起服務、收音、主迴圈 |
| `mac_ai/config.py` | 路徑、埠號、逾時等設定（單一來源） |
| `mac_ai/services.py` | 背景服務的啟動、就緒檢查與關閉 |
| `mac_ai/wake.py` | 喚醒詞閘門與會話 |
| `mac_ai/ui_bus.py` | 桌面角色的事件匯流排（本機 WebSocket） |
| `mac_ai/router/` | 正規化、Fast Path、Slow Path（雲端優先、退回本機 LLM；`cloud_client.py` 雲端呼叫與提示詞，`memory.py` 對話記憶）、動作執行；找東西分兩層：`domain.py`（範圍判斷，純函式）與 `local_actions.py`（執行），`catalog.py` 是已安裝 App／專案目錄，`choices.py` 是多筆結果的選擇清單，`context.py` 讀最前面的 App 與網站；點歌：`youtube_client.py`（InnerTube 搜尋）、`music_prefs.py`（偏好、計分、拼音矯正）；天氣：`weather_client.py`（氣象署→Open-Meteo）、`weather_prefs.py`（常問縣市）；長尾回答：`search_client.py`（Brave 搜尋）、`sources.py`（來源追問） |
| `mac_ai/db/` | 偏好資料庫（`data/music.sqlite3`，點歌與天氣常問縣市共用）的建立、備份與升級（Alembic migration）、從事件重算詞表（`rebuild.py`） |
| `mac_ai/stt/` | 麥克風收音（`mic.py`）、STT WebSocket 用戶端（`client.py`）、神經網路 VAD（`vad.py`）、端點偵測（`endpoint.py`、`completeness.py`）、計時紀錄（`latency_log.py`）；備用辨識引擎的伺服器（`xasr_server.py`）與熱詞編碼（`xasr_hotwords.py`） |
| `desktop/` | 桌面角色（Electron），依行程與層次分目錄：`main/`（視窗、匯流排連線、IPC）、`preload/`、`domain/`（角色狀態機、注意力導演、聊天室紀錄、幀率閘門，純邏輯）、`rig/`（模型資料）、`shared/`（與後端的合約）、`renderer/`（`engine/` 渲染、`animation/` 程序動畫、`role/` 與 `chat/` 畫面）、`dev/`（量測）；分層規則見 [ARCHITECTURE.md](ARCHITECTURE.md) |
| `scripts/` | 端點偵測的測試語料產生、bench、真人錄音；桌面角色的示範（`ui_demo.py`）與效能量測（`ui_perf.py`）；自動驗收的量化與比較（`acceptance.py`） |
| `bench/` | 測試語料；`synth/` 可重新產生、不入版控，`human/manifest.csv` 是真人錄音清單 |
| `vocab/lexicon.json` | 喚醒詞、App 名稱、指令詞、常見誤辨識對照（單一來源） |
| `vocab/stt_hints.txt` | 由 `build_vocab.py` 產生，餵給語音辨識當提示詞 |
| `docs/` | `decisions/` 設計決策（ADR）、`flows/` 跨模組流程、`guides/` 測試與程式守則、`plans/` 實作計畫（`completed/` 為已結案）；AI 程式助手的入口是 `AGENTS.md` |
| `tests/` | 分層測試：`unit/` 單元、`flow/` 整條路由、`runtime/` 真執行緒與 socket、`real/` live／cloud／app 實測；共用假物件與語料在 `support/` |

修改 `vocab/lexicon.json` 後，`main.py` 啟動時會自動重新產生詞表；也可以手動執行 `python build_vocab.py`。

## 感謝

備用辨識引擎使用 **[Luigi/x-asr-zh-tw-native-itn](https://huggingface.co/Luigi/x-asr-zh-tw-native-itn)**（Apache-2.0，建立在 GilgameshWind/X-ASR-zh-en 與 k2-fsa/icefall 之上）與 **[k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)**（Apache-2.0），感謝這些作者。

本專案的語音辨識能力來自 **[bannana/macos-stt](https://github.com/bannana/macos-stt)**，作者 **bannana** 以 MIT 授權釋出這個 Python 函式庫，讓 Python 能直接使用 macOS 內建的語音辨識引擎，不必下載任何模型。這個專案的 `macos-stt/` 目錄是在原版基礎上另外加入 `serve` 子命令（HTTP／WebSocket 串流伺服器）等修改，感謝原作者的設計與實作。

同時感謝 [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) 與 Apple [MLX](https://github.com/ml-explore/mlx) 團隊，讓本地語言模型能在 Apple Silicon 上順利執行。
