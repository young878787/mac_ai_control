# mac_ai_control

在 Mac 上用語音控制電腦的本地語音助手。直接說出指令，就能開啟或關閉 App 等操作（預設常啟動，不需喚醒詞）。語音辨識與語言模型都在本機執行，不需要雲端 API。

- 範例：「打開小算盤」「關掉 Safari」
- 喚醒詞（終端機模式預設關閉）：**露西婭**（別名：露醬、露西亞）。要啟用，把 `mac_ai/config.py` 的 `WAKE_WORD_ENABLED` 改成 `True`，之後須說「露西婭 打開小算盤」才會執行。喚醒後進入 60 秒的**會話**（`WAKE_SESSION_S`）：期間每一句都不用再說喚醒詞，每說一句重新計時；逾時後要再喊一次
- 桌面角色（`python main.py --ui`）：Rushia（Live2D）常駐螢幕右下角，平常從下緣探頭，喚醒時出場、依執行結果做動作並以泡泡顯示；可用快捷鍵叫出輸入框打字。此模式預設啟用喚醒詞。見下方「桌面角色」

## 運作方式

```
麥克風 ──▶ macos-stt（系統語音辨識，WebSocket :8765）──▶ 喚醒詞閘門
                                                          │
                                  ┌───────────────────────┴───────────────────────┐
                                  ▼                                               ▼
                    Fast Path：詞典／規則直接比對                 Slow Path：本地 LLM 規劃工具呼叫
                    （不需 LLM，LLM 掛掉仍可用）                  （mlx_vlm.server，HTTP :8080）
                                  └───────────────────────┬───────────────────────┘
                                                          ▼
                                                  執行動作（開啟／關閉 App 等）
```

- **語音辨識**交給 [macos-stt](#感謝)，它會呼叫 Apple 內建的 `SFSpeechRecognizer`，不需下載模型。
- **語言模型**使用 `mlx_vlm.server` 載入的本地模型（預設 `AI_models/LFM3-VL-2B-MLX-4bit`）。
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
pip install mlx-vlm sounddevice websockets pytest onnxruntime numpy
```

開發時的版本：`mlx-vlm 0.7.11`、`sounddevice 0.5.6`、`websockets 17.4`、`pytest 9.2.0`、`onnxruntime 1.31.1`、`numpy 2.5.4`。

### 4. 準備語言模型

模型權重不在 repo 內（目前 `AI_models/` 已加入 `.gitignore`），請自行放到：

```
AI_models/LFM3-VL-2B-MLX-4bit/
```

若改用其他模型，請同步修改 `mac_ai/config.py` 的 `MODEL_ID`。

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

若曾拒絕授權，請到上述位置改回允許後重新啟動。

## 使用方式

```bash
source .venv/bin/activate

python main.py                    # 啟動所有服務並開始收音（Ctrl+C 結束）
python main.py --no-stt           # 只啟動 LLM 服務，不收音
python main.py --text 打開小算盤  # 直接送入文字指令後結束（不檢查喚醒詞）
python main.py --device 1         # 指定麥克風裝置編號
python main.py --ui               # 同時顯示桌面角色（需先完成「桌面角色」一節的安裝）
```

啟動後會依序啟動 LLM（`mlx_vlm.server`）與 STT（`macos-stt serve`），等兩者就緒後開始收音。

## 桌面角色

`--ui` 會另外啟動一個 Electron 視窗（`desktop/`）：透明、置頂、可穿透點擊（只有角色身體與泡泡會吃到滑鼠），出現在所有桌面空間。後端與視窗用本機 WebSocket（`127.0.0.1:8766`，連線需帶啟動時產生的 token）溝通；沒加 `--ui` 時完全不會啟動這些東西。

### 安裝

```bash
cd desktop
npm install                                   # electron、pixi.js、pixi-live2d-display（版本已鎖定）
ls node_modules/electron/dist >/dev/null 2>&1 || node node_modules/electron/install.js   # npm 沒有下載到 Electron 執行檔時補這行
```

另外兩樣東西不入版控，需自行準備：

- **Live2D Cubism Core**：到 [Live2D 官網](https://www.live2d.com/en/sdk/download/web/) 下載 Cubism SDK for Web，把 `Core/live2dcubismcore.min.js` 放到 `desktop/vendor/`（專有授權，不可散布）。Core 5.x 與 6.x 都可用；6.x 會由 `desktop/renderer/app.js` 的 `bridgeRenderOrders` 補上 pixi-live2d-display 0.4.0 讀不到的繪製順序。
- **模型**：`Rushia_hd/runtime-swing/RushiaHD.model3.json`（路徑在 `mac_ai/config.py` 的 `UI_MODEL_PATH`）。

`python main.py --ui` 啟動時會檢查以上三樣，缺什麼就印出怎麼補。

### 操作

| 操作 | 效果 |
|---|---|
| 說喚醒詞 | 角色出場、頭上出現會話光環（倒數環）；60 秒內每句免喚醒詞、每句重新計時 |
| `⌥Space`（可在 Electron 的 `userData/settings.json` 的 `shortcut` 改） | 叫出／關閉輸入框，Enter 送出（不用喚醒詞）、Esc 關閉；收音時 partial 以灰字顯示在輸入框裡 |
| 點頭部 | 出場並開啟會話；點身體：開心動作 |
| 拖曳 | 只能沿螢幕下緣左右移動，位置會記住 |
| 右鍵 | 對話紀錄／暫停聽指令／結束 |

不用麥克風也能預覽演出：`python scripts/ui_demo.py`（`--hold` 播完後保持執行，可手動操作）。效能量測：`python scripts/ui_perf.py`。

## 端點偵測（說完就收）

預設 `mac_ai/config.py` 的 `ENDPOINT_MODE = "client"`：本端用 Silero VAD 判斷何時開始／結束說話，並看辨識中的文字是不是一句完整的指令（完整就早點收、句尾是「然後」之類就多等），決定後主動要求 macos-stt 收尾。改成 `"server"` 即回到舊行為（macos-stt 的能量 VAD，靜音 1.5 秒才斷句）。每句的計時寫在 `logs/latency.jsonl`。設計、參數與實測結果見 [docs/語音端點偵測與延遲改善：實作計畫.md](docs/語音端點偵測與延遲改善：實作計畫.md)。

量測與比較（需要 macos-stt 已啟動）：

```bash
python scripts/make_bench_corpus.py synth      # 產生合成測試語料到 bench/synth/
python scripts/stt_bench.py run --corpus bench/synth --mode server --out logs/bench_base.jsonl
python scripts/stt_bench.py run --corpus bench/synth --mode client --out logs/bench_new.jsonl
python scripts/stt_bench.py report logs/bench_base.jsonl logs/bench_new.jsonl   # A/B 比較與驗收判定
python scripts/record_human.py                 # 錄真人測試語料（清單在 bench/human/manifest.csv）
```

## 測試

```bash
pytest              # 離線測試（預設）
pytest -m live      # 需要 mlx_vlm.server 已啟動並載入模型
cd desktop && node --test   # 桌面角色的狀態機與路徑白名單測試
```

## 專案結構

| 路徑 | 說明 |
|---|---|
| `main.py` | 啟動入口：起服務、收音、主迴圈 |
| `mac_ai/config.py` | 路徑、埠號、逾時等設定（單一來源） |
| `mac_ai/services.py` | 背景服務的啟動、就緒檢查與關閉 |
| `mac_ai/wake.py` | 喚醒詞閘門與會話 |
| `mac_ai/ui_bus.py` | 桌面角色的事件匯流排（本機 WebSocket） |
| `mac_ai/router/` | 正規化、Fast Path、Slow Path（LLM）、動作執行 |
| `mac_ai/stt/` | 麥克風收音（`mic.py`）、STT WebSocket 用戶端（`client.py`）、神經網路 VAD（`vad.py`）、端點偵測（`endpoint.py`、`completeness.py`）、計時紀錄（`latency_log.py`） |
| `desktop/` | 桌面角色（Electron）：`main.js` 視窗與連線、`state.js` 狀態機、`renderer/` 畫面與 Live2D |
| `scripts/` | 端點偵測的測試語料產生、bench、真人錄音；桌面角色的示範（`ui_demo.py`）與效能量測（`ui_perf.py`） |
| `bench/` | 測試語料；`synth/` 可重新產生、不入版控，`human/manifest.csv` 是真人錄音清單 |
| `vocab/lexicon.json` | 喚醒詞、App 名稱、指令詞、常見誤辨識對照（單一來源） |
| `vocab/stt_hints.txt` | 由 `build_vocab.py` 產生，餵給語音辨識當提示詞 |
| `docs/` | 設計與實作計畫（端點偵測、桌面角色與喚醒會話） |
| `tests/` | 離線測試與 live 測試 |

修改 `vocab/lexicon.json` 後，`main.py` 啟動時會自動重新產生詞表；也可以手動執行 `python build_vocab.py`。

## 感謝

本專案的語音辨識能力來自 **[bannana/macos-stt](https://github.com/bannana/macos-stt)**，作者 **bannana** 以 MIT 授權釋出這個 Python 函式庫，讓 Python 能直接使用 macOS 內建的語音辨識引擎，不必下載任何模型。這個專案的 `macos-stt/` 目錄是在原版基礎上另外加入 `serve` 子命令（HTTP／WebSocket 串流伺服器）等修改，感謝原作者的設計與實作。

同時感謝 [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) 與 Apple [MLX](https://github.com/ml-explore/mlx) 團隊，讓本地語言模型能在 Apple Silicon 上順利執行。
