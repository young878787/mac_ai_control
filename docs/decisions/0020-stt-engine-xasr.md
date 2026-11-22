# 0020 語音辨識引擎可切換：macos-stt 或 X-ASR（Windows 專用的備用引擎）

- 狀態：採用
- 日期：2026-11-22
- 來源：[X-ASR 備用引擎實作計畫](../plans/completed/2026-11-21-stt-engine-xasr.md) §2 X1～X20；使用者 2026-11-21、2026-11-22 決定

## 背景

語音辨識只有 macos-stt（呼叫 macOS 內建的 `SFSpeechRecognizer`），只能在 macOS 用。要讓這一層可以在 Windows 執行，需要第二個引擎，而且不能動到端點偵測、路由與守門。沒有 Windows 機器，所以 Windows 部分只能在 Mac 上模擬。

## 決定

**切換點與選擇**

1. **切換點是 WS 協定 v1，不在 client 裡。** 新增 `mac_ai/stt/xasr_server.py`，說與 macos-stt 相同的 `WS /stream`＋`GET /health`，是獨立子程序、同一個埠（`STT_PORT`）。`SttClient`、`EndpointDetector`、`completeness`、Silero VAD、`assistant.py`、`stt_bench.py` 一行都不改（0003）；`EP_*` 端點參數不依引擎切換，要依引擎調參數先問使用者。
2. 選擇方式：`config.STT_ENGINE`（`"macos"`｜`"xasr"`）加 `main.py --stt-engine`。**macOS 預設 `macos`，行為與沒有這個功能時相同**；Windows（`sys.platform == "win32"`）預設 `xasr`。在非 macOS 選 `macos` 直接報錯結束。兩個引擎不會同時執行。
3. 引擎相關的啟動邏輯只在 `services.py`：`xasr_stt_service()` 缺模型檔就丟 `ServiceError` 並列出缺哪個檔，不退回別的引擎；ready 要求 `/health` 的 `engine` 是 `xasr`。

**模型與設定**

4. 模型：`Luigi/x-asr-zh-tw-native-itn` 的 `deployment/onnx-int8-480ms/`（revision `3d5bad21ccf54b37eef1fddccad48ca82001ddd9`，Apache-2.0），放 `AI_models/x-asr-zh-tw-native-itn/`，不入版控，README 寫下載指令與 SHA-256，不自動下載。完全在本機執行，不連網。
5. 執行設定（`config.XASR_*`，數值旁有實測理由）：`tokens_traditional.txt`（模型內建繁體 token 表，不用 OpenCC、cn2an）、`modified_beam_search`、熱詞由 `stt_hints.txt` 轉成 token 後 score 4、尾端補 0.6 秒靜音、2 個執行緒。數字（「三十」）不在伺服器處理，由既有的 `normalize` 處理。
6. 新增的 dependency 只有 `sherpa-onnx==2.0.2`（自帶 onnxruntime），而且是延遲 import，macOS 預設路徑不需要安裝。
7. `lexicon.json` 的 `term_misheard` 加「熒幕→螢幕」（只加固定的字形對應，不加「沙發率→Safari」這種聽錯的拼法）。

**伺服器行為（對 macos-stt 的原始碼逐項核對）**

8. 只綁 `127.0.0.1`。錯誤碼：`protocol`（1002）、`too_many_sessions`（1013，上限 2）、`internal`（1011）。flush 沒有文字就不送 final，final 之後送 `session_restarted`，`close` 不送 final，`multi_utterance=true` 拒絕。熱詞只在啟動時載入（`stt_hints.txt`）；client 在 config 裡送來、不在啟動熱詞裡的 `hints` 只記錄、不生效。
9. 安全網 `server_vad`：辨識文字連續 `vad_flush_seconds`（預設 3 秒）的音訊時間都沒變就自己收句；單一連線上限 300 秒。每次收句往 stdout 印 `[flush] reason=… pre_delay_ms=… finish_ms=…`（`stt_bench.py` 的 L1 從 log 讀這行）。
10. `/health` 回報 `engine`、`model`、`sessions_active`、`max_sessions`、`default_lang`、`hotwords`、`hotwords_skipped`。熱詞切不出 token 的略過並計數，不報錯。

**定位與驗收**

11. **X-ASR 是比 macos-stt 不準的備用引擎**，不是 macOS 的替代品。沿用 2026-11-05 端點計畫的驗收定義，準確度項目（Q1 意圖、Q2 CER）已知不過，照實記錄、不阻擋；延遲、誤觸發、資源項目照常。
12. **CPU 門檻一律用整機百分比（全核）**，不用單核（[0013](0013-fullrig03-cpu-budget.md) 的 5% 是整台電腦；本計畫一度誤寫成單核 5%）。
13. 使用者接受兩項字面上沒過的數字：A 類 E2E p90 1370 ms（門檻 1200 ms），以及 B 類 Q3 的 3/8 對 2/8（xasr 的 3 句全是辨識錯字，沒有截斷）。

## 實測結果（2026-11-22，同一台 M 系列 Mac、同一份真人語料 `bench/human`）

| 項目 | 門檻 | macos-stt | X-ASR |
|---|---|---|---|
| A 類 E2E 中位／p90 | ≤ 800／≤ 1200 ms | 624／952 ms | 739／**1370** ms（p90 使用者接受） |
| 伺服器 `finish_ms` 中位 | ≤ 150 ms | 69 ms | 39 ms |
| A+C+F 意圖 | ≥ 95% 且 ≥ macos | 24/24 | 22/24＝92%（加「熒幕→螢幕」後；之前 19/24）→ 記錄 FAIL，不阻擋 |
| CER | ≤ macos＋1.0 pt | 2.6% | 10.0% → 記錄 FAIL，不阻擋 |
| B 類提早截斷（bench 算法） | ≤ macos | 2/8 | 3/8（使用者視為通過） |
| D 類噪音 0 指令 | 0 | 0 | 0 |
| CPU（整機，8 核心） | < 5% | 約 0.8%（bench 期間） | 閒置 3.9%、bench 期間 3.4% |
| RSS／RTF | < 600 MB／< 0.2 | 77 MB | 峰值 476 MB／0.045 |

## 後果

- **Windows 只在 macOS 上模擬過，沒有實機測試。** 模擬的範圍：乾淨 venv 只裝 Windows 版會裝的套件（`sherpa-onnx sounddevice onnxruntime websockets numpy`）起伺服器、import 鏈、跑 bench；`sys.platform` 改成 `win32` 確認預設選到 xasr（`ssl` 的 Windows 憑證函式要先打樁）；`win_amd64` 的安裝檔存在。**沒有驗證**：麥克風驅動（WASAPI）、主控台編碼（cp950）、防毒軟體擋 8765 埠、Windows 版 sherpa-onnx 的實際速度與準確度、`services.py` 在 Windows 的子程序收尾（`send_signal` 只支援 SIGTERM）。
- 範圍只有語音辨識這一層。Windows 全流程（執行器、本機 LLM、前景視窗、桌面角色）另開計畫。
- 已知缺點：英文 App 名稱的辨識錯字（Safari→「沙發率」）、環境雜音會被聽成短字送進 Slow Path（噪音幻覺，實測看到「Sh」）、辨識慢半拍（480 ms chunk，partial 比說話晚 0.5～0.9 秒）。「鎖定一幕」「靜音」（第一個 partial 晚到 2 秒以上，client 的 `EP_NO_TEXT_RESET_S`＝2.0 會把它當噪音丟掉，改靠伺服器 3 秒安全網）仍會錯。
- **沒有做**的事（要做先問使用者）：靜音時不送進模型（原 Q10 選項 a；整機 CPU 已過門檻，所以沒做）、只對 xasr 放寬 `EP_NO_TEXT_RESET_S`、試 160 ms 的模型。
- 整機 3.9% 是只開辨識伺服器的數字；macOS 上若同時開桌面角色（0013 的預算本身就在 5% 附近），總和會超過 5%。X-ASR 在 macOS 只用於開發比較，Windows 桌面角色不在範圍。
- 模型授權：`Luigi/x-asr-zh-tw-native-itn` 是 Apache-2.0；候選 1 `x-asr-zh-tw-en-streaming-ft75m`（研究用、不可商用，準確度更差）沒有採用，`AI_models/` 底下的檔案保留但程式與文件不引用。
- 人工驗收沒有做：M1（用 X-ASR 實際說話至少 10 句，確認沒有誤執行）、M3（執行中 kill 伺服器後介面顯示斷線、重啟後恢復）、W2 要求的「說 5 句」。STT 語音實機沒有自動測試。
