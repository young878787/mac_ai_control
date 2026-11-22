# 語音辨識引擎可切換：macos-stt 或 X-ASR（sherpa-onnx，可跑 Windows）

> **歸檔（2026-11-22 結案）**：P0～P4 完成；§3 的待確認全部由使用者決定（X18～X20）。以下內文保留結案時的樣子，**但 §7.2 P1 的「單核 5%」是誤讀，已在原處改成整機 5%（X18）**；§1.7 的 P2 表格是改門檻前的原始判定，有標註新的判定。目前有效的決策見 [ADR 0020](../../decisions/0020-stt-engine-xasr.md)。**Windows 只在 macOS 上模擬過，沒有實機測試**；M1、M3、W2 的人聲部分沒有做。

狀態：**已結案（2026-11-22）：P0～P4 完成**；X-ASR 備用引擎已接進主程式，macOS 預設不變，Windows 預設 xasr。結果見 §1.5～§1.7 與 §6 P3 的實際結果；沿用的決策已寫成 [ADR 0020](../../decisions/0020-stt-engine-xasr.md)。**尚未驗證**：M1（用 X-ASR 實際說話至少 10 句）、M3（kill 伺服器後斷線、重啟恢復）、W2 的「說 5 句」，以及所有 Windows 實機項目（§8）。採用候選 2，定位是 Windows 專用、比 macos-stt 不準的備用引擎（X12）。沒有 Windows 機器，Windows 改在本機模擬，並分階段切換（X16、§6 P3）。分支 `windows-compat`。§1.5～§1.7 是實測結果，§4 是設計，§6 是實作步驟與完成條件，§7 是驗收計畫。

## 0. 結論

1. **切換點放在 WS 協定 v1，不放在 client 裡面。** 新增一個本 repo 內的辨識伺服器 `mac_ai/stt/xasr_server.py`，實作與 macos-stt 相同的 `WS /stream`＋`GET /health`。`SttClient`、端點偵測（`EndpointDetector`、Silero VAD、`completeness`）、`assistant.py`、`stt_bench.py` 全部不改，切換引擎只是換一個子程序。
2. 選擇方式：`config.STT_ENGINE`（`"macos"`｜`"xasr"`）加上 `main.py --stt-engine`。
   - **macOS 預設維持 `macos`**，逐字保留現在的行為。在 macOS 上只有明確帶 `--stt-engine xasr` 才會用 xasr，用途是開發與 bench 比較。
   - **Windows 預設 `xasr`**，也只有這個可以選。
3. **採用候選 2**（X12、X13）：`Luigi/x-asr-zh-tw-native-itn` 的 480 ms int8 部署版，設定如下：
   - 繁體 token 表 `tokens_traditional.txt`
   - `modified_beam_search`
   - 由 `stt_hints.txt` 自動產生的熱詞，score 4
   - 尾端補 0.6 秒靜音
   - 2 個執行緒
4. 驗收門檻維持 §7.2 不變（X14）。準確度兩項（§7.2 的 Q1 意圖、Q2 CER）已知不過，P2 照實記錄 FAIL，**不阻擋**；延遲、誤觸發、資源等其他門檻照常阻擋（§7.2）。
5. 新增一個 dependency：`sherpa-onnx`（2.0.2，內含自己的 onnxruntime；主 venv 的 `onnxruntime` 維持 1.31.1）。不裝 OpenCC、cn2an。
6. 範圍：先只做語音辨識這一層（X4）。Windows 全流程（執行器、LLM、前景）另開計畫。
7. **P0 結果：兩個候選都還沒達到 macos-stt 的準確度。** 用同一份真人語料比較：

   | | A+C+F 意圖 | 平均 CER | 句尾要補的靜音 |
   |---|---|---|---|
   | macos-stt | **24/24** | **2.6%** | — |
   | 候選 1 `ft75m`（最好的設定） | 15/24 | 24.9% | 0.2 秒 |
   | 候選 2 `native-itn` 480 ms（最好的設定） | **19/24** | **10.0%** | 0.6 秒 |
   | 候選 2，再加一條誤聽詞「熒幕→螢幕」（模擬） | 21/24 | — | — |

   - 候選 1 的數字整段不出字，英文 App 名稱也拼錯（§1.5）。
   - 候選 2 把這兩個問題大致修好了。剩下的錯是「螢幕」變成「熒幕／銀幕／一幕」、B 類的 Safari 變成「沙發率」、Line 變成「來」。
   - 候選 2 每 480 ms 才出一段字，partial 比說話晚約 0.5～0.9 秒，E2E 可能超過 S1 的 800 ms 門檻（§1.6）。

## 1. 現況（2026-11-21 實際查證）

### 1.1 STT 怎麼接進來

```
MicSource(16 kHz mono int16, 32 ms 一窗)
  → SttClient（WS client，送 PCM；本端 Silero VAD＋EndpointDetector 決定說完了就送 {"type":"flush"}）
  → macos-stt（.app bundle 內 start.py serve，WS :8765，SFSpeechRecognizer）
  → 事件 partial／utterance／final／session_restarted／error
  → CommandRouter（喚醒詞閘門）→ 指令佇列 → command_worker
```

- 協定（`macos-stt/docs/fastapi-server-plan.md` §4.2）：
  - 連線後第一則必須是 `config`，欄位有 `lang`、`sample_rate`、`hints`、`multi_utterance`、`vad_flush_seconds`、`flush_pre_delay_seconds`。
  - 之後送 binary PCM、`{"type":"flush"}`、`{"type":"close"}`。
  - 伺服器回 `partial`、`utterance`、`final`（含 `timing.reason`、`pre_delay_ms`、`finish_ms`）、`session_restarted`、`error`。
- 目前設定：`ENDPOINT_MODE="client"`、`STT_MULTI_UTTERANCE=False`、`STT_VAD_FLUSH_S=1.5`，`EP_FLUSH_PRE_DELAY_S` 用 0。
- 熱詞 `vocab/stt_hints.txt`（54 行）由 `build_vocab.py` 從 `lexicon.json` 產生，每次連線放在 `config.hints` 送出。
- `services.stt_service()` 用 macos-stt 自己的 venv 起子程序。`_stt_ready` 檢查語音辨識授權；收尾時 `kill_stt_backend` 讀 `/tmp/macstt_pid`，再用 `ps` 確認。
- 測試替身 `tests/support/stt.py` 的 `FakeSttServer` 依協定 v1 模擬伺服器。

### 1.2 現有的量測工具與基線

- `scripts/stt_bench.py`：照真實時間把音檔推給 `ws://127.0.0.1:8765`，量 E2E、端點、收尾時間、CER、意圖正確與否、有沒有被拆句，`report A B` 會做 A/B 判定。
- 語料：
  - `bench/human/`：真人錄音 40 段，`manifest.csv` 裡有參考文字與預期動作。分 A 完整句、B 句中停頓、C 有噪音、D 純噪音、E 多步驟、F 句尾字。
  - `bench/synth/`：合成語音 36 段（D 類除外）。
- macos-stt 基線：`logs/bench/2026-11-05_human_client_new2.jsonl`，用 2026-11-21 的路由重算。

  | 類 | n | E2E 中位 | E2E p90 | 收尾中位 | 意圖 | CER |
  |---|---|---|---|---|---|---|
  | A | 15 | 633 ms | 955 ms | 69 ms | 15/15 | 0.0% |
  | B | 8 | 482 ms | 1379 ms | 68 ms | 6/8（B01、B02 前文被丟掉） | 10.0% |
  | C | 6 | 467 ms | 735 ms | 74 ms | 6/6 | 0.0% |
  | D | 4 | — | — | — | 0 個指令命中 | — |
  | E | 4 | 380 ms | 639 ms | 86 ms | 4/4 | 3.3% |
  | F | 3 | 470 ms | 489 ms | 78 ms | 3/3 | 0.0% |

  合計：A+C+F 意圖 24/24，平均 CER 2.6%。

### 1.3 模型實際內容（Hugging Face，revision `90fe6b8a0f278886cbeabd2d41cc5402f7f90a2b`）

| 項目 | 內容 |
|---|---|
| 檔案 | 已下載到 `AI_models/x-asr-zh-tw-en-streaming-ft75m/`：`encoder.int8.onnx` 121 MB、`decoder.onnx` 13.9 MB、`joiner.int8.onnx` 3.2 MB、`tokens.txt`（6257 個 token）、`README.md` |
| SHA-256 | encoder `9a92f643…3e07`、decoder `0d6d200e…3b05`、joiner `f8c88ae0…a7d` |
| 架構 | 串流 Zipformer transducer，75.5M 參數；以 `pfluo/k2fsa-zipformer-chinese-english-mixed` 為起點，用約 1,560 小時台灣華語（Breeze-ASR-25 偽標註）＋Common Voice zh-TW＋NTU-ML 微調 |
| 輸出 | 繁體；**沒有標點 token**；**英文是大寫 BPE**；**沒有阿拉伯數字 token** |
| 準確度（作者自評） | CV-zh-TW 500 句，CER 0.0822 |
| 授權 | `license: other`。研究用模型；微調資料含 YouTube 音訊；沒有用乾淨權利的語料重訓之前，不可商業部署 |

### 1.4 Windows 上會卡住的地方（STT 以外，本計畫不處理）

| 範圍 | 綁 macOS 的東西 |
|---|---|
| 本機 LLM | `mlx_vlm.server`（只支援 Apple Silicon） |
| 執行器 | `actions.py`、`local_actions.py`：`open -a`、`osascript`、`screencapture`、`mdfind` |
| 常用功能 | `native.py`：用 ctypes 呼叫輔助使用 API、CGEvent、AppKit |
| 前景上下文 | `context.py`：`lsappinfo`、`osascript` |
| 簡轉繁／拼音 | `hant.py`（CoreFoundation）、`music_prefs.py`（`/usr/lib/libicucore.dylib`） |
| App 清單 | `catalog.py`：Spotlight |
| 服務管理 | `services.py`：`ps`、`/tmp` PID 檔、.app bundle |
| 端點偵測 | `stt/completeness.py` import `fast_path` → `actions`。macOS 專用的 CDLL 都在函式內才載入，**import 應該不會失敗，但沒有在 Windows 驗證過** |

### 1.5 P0 實測結果：候選 1 `ft75m`（2026-11-21，M 系列 Mac，實際執行確認）

方法：
- 用 `bench/human` 的 40 段音檔做離線串流解碼：每 0.096 秒送一塊（與 `EP_SEND_S` 相同），沒有經過 WS 與端點偵測。
- 意圖與 CER 用 `stt_bench.py` 的 `_actions`、`_cer`、`_norm` 計算，和 macos 的數字是同一套判定。
- 「整段」是把整段音檔（含尾端靜音）都送完；「補靜音 X 秒」是在說完後 0.35 秒切斷、補 X 秒靜音再收尾，模擬 client flush。
- 腳本與原始結果放在 `logs/xasr_p0/`（不入版控）。

**準確度（真人語料，整段，跟 macos-stt 比）**

| 設定 | A | B | C | E | F | A+C+F | 平均 CER |
|---|---|---|---|---|---|---|---|
| macos-stt（基線） | 15/15 | 6/8 | 6/6 | 4/4 | 3/3 | **24/24** | **2.6%** |
| X-ASR greedy，沒有熱詞 | 8/15 | 2/8 | 2/6 | 1/4 | 1/3 | 11/24 | 28.4% |
| X-ASR beam＋中文熱詞（詞表裡的中文 26 個＋一～一百），score 3 | 11/15 | 2/8 | 3/6 | 1/4 | 1/3 | **15/24** | 24.9% |
| 同上，score 5 | 10/15 | 2/8 | 3/6 | 1/4 | 1/3 | 14/24 | 25.7% |

合成語料（greedy，沒有熱詞）：意圖 20/36。

錯誤類型（逐段文字在 `logs/xasr_p0/*.jsonl`，人工看過）：

| 類型 | 例子 | 次數（沒有熱詞，真人） |
|---|---|---|
| 數字不出字或聽錯 | 「音量調到三十」→「音量調到」（A07～A09、C03 全部）；「調到五十」→「調大」 | 6/6 |
| Safari 拼錯 | 「SUPARRY」「啥法律」「撒法律」「沙發 RI」「VERR」，或整個字不見 | 13/13 |
| Line 丟字 | 「打開 Line」→「打開」（F01） | 1/2 |
| 中文近音字 | 「鎖定螢幕」→「鎖定一幕」、「打開」→「咋開」 | 3 |
| 純噪音出現幻覺 | D01（風扇）→「麥口以為接下來的時間裡他將和父親並肩作戰…」，沒有命中指令，但在會話中會被送進 Slow Path | 1/4 |

**各項 P0 的結論**

| # | 項目 | 結果 |
|---|---|---|
| P0-1 | 載入、記憶體、速度 | 載入 0.65 s；峰值 RSS 約 420 MB（含路由模組）；RTF：1 執行緒 0.043、**2 執行緒 0.032**、4 執行緒 0.040。速度**通過** |
| P0-2 | 尾端靜音、partial 反應 | 補 0 秒會丟最後的字；**補 0.2 秒就和補 0.8 秒一樣完整**（`XASR_TAIL_PAD_S` 建議 0.2）。partial 等於最終文字的時間比說完晚，中位 144～320 ms（只算音訊時間，不含 RTF 0.03 的運算），比 macos 的 0.3～0.6 秒快 |
| P0-3 | 英文大寫 | `打開 LINE`、`關掉 SAFARI`、`SOFARI` 都能命中正確動作，路由本來就不分大小寫。**通過，不用改 `normalize`**（Q7 結案） |
| P0-4 | 熱詞 | 英文熱詞**不能用**：`cjkchar` 找不到 `SAFARI` 這個 token，`cjkchar+bpe`／`bpe` 需要模型沒附的 `bpe.vocab`。中文熱詞可以用：數字有部分救回來，但有副作用（「三十一」、「一量調到」、「三法律」），RTF 0.032 → 0.040。結論：**熱詞救不到門檻** |
| P0-5 | Windows 實機 | **沒做**：沒有 Windows 機器或 VM |

**判定**：§7.2 的 Q1（A+C+F 意圖 ≥ 95% 且 ≥ macos）是 15/24＝63%，**不通過**。Q2（CER ≤ macos＋1 pt）是 24.9% 對 2.6%，**不通過**。

### 1.6 P0 實測結果：候選 2 `Luigi/x-asr-zh-tw-native-itn`（2026-11-21，實際執行確認）

**模型**
- 只下載部署版 `deployment/onnx-int8-480ms/`（revision `3d5bad21ccf54b37eef1fddccad48ca82001ddd9`），放在 `AI_models/x-asr-zh-tw-native-itn/`。
- 不下載 643 MB 的 `xasr_tw_init.pt`（那是給微調用的）。
- 檔案：
  - `encoder.int8.onnx` 155 MB（`908596dc…09c5eb1e`）
  - `decoder.onnx` 11 MB（`a1cbc9ea…13c8c257`）
  - `joiner.int8.onnx` 2.6 MB（`aedb7fa6…726e63b3`）
  - `tokens.txt`（簡體）、`tokens_traditional.txt`（繁體，與簡體一一對應）、`bpe.model`、`itn.py`、`decode_onnx.py`
- 授權 **Apache-2.0**（候選 1 是研究用、不可商用），對開源比較好。
- 本體是 GilgameshWind 部署的 X-ASR（約 160M，chunk 480 ms，int8）。有標點，英文是小寫 BPE，中文 token 是「▁字」的形式。
- 作者建議的後處理是 OpenCC `s2twp`＋`itn.py`（cn2an），**兩個都要新增 dependency，所以不採用**：
  - 繁體改用模型內建的 `tokens_traditional.txt`。
  - 數字留給既有的 `normalize`，它本來就會處理中文數字。

**準確度（真人語料，整段）**

| 設定 | A | B | C | E | F | A+C+F | 平均 CER |
|---|---|---|---|---|---|---|---|
| macos-stt（基線） | 15/15 | 6/8 | 6/6 | 4/4 | 3/3 | **24/24** | **2.6%** |
| 簡體 `tokens.txt`＋既有 `normalize` | 8/15 | 3/8 | 3/6 | 0/4 | 1/3 | 12/24 | 20.2% |
| 繁體 `tokens_traditional.txt`，greedy | 11/15 | 5/8 | 4/6 | 0/4 | 2/3 | 17/24 | 15.2% |
| 繁體，beam，沒有熱詞 | — | — | — | — | — | 18/24 | 13.1% |
| 繁體，beam＋`stt_hints` 熱詞，score 2 | 11/15 | 5/8 | 4/6 | 3/4 | 3/3 | 18/24 | 12.5% |
| **繁體，beam＋`stt_hints` 熱詞，score 4** | 12/15 | 5/8 | 4/6 | 3/4 | 3/3 | **19/24** | **10.0%** |

- 簡體版本比較差，因為 `normalize` 的簡轉繁只涵蓋部分字（例如「打开小算盘」只轉成「打開小算盘」），這是熱路徑的既定設計，不改。
- 熱詞的做法：
  - `stt_hints.txt` 54 行先切成 token 再寫進熱詞檔：中文寫成「▁字」，英文轉小寫後依 `tokens_traditional.txt` 最長比對切開。最後有 43 行可以用，`vscode`／`vs code` 切不出來。
  - `modeling_unit="cjkchar"`，直接查 token 表。
  - sherpa 的 `bpe_vocab` 需要文字格式的 vocab，不能直接吃 `bpe.model`；不先切好 token 的話，中文熱詞會查不到。
- score 4 的剩餘錯誤（逐段人工看過）：

  | 類型 | 例子 | 次數 |
  |---|---|---|
  | 螢幕 | 「鎖定熒幕」「鎖定銀幕」「鎖定一幕」，E04 句尾的「螢幕」整個不見 | 5 |
  | B 類的 Safari | 「打開沙發率」「關掉， 沙發綠」（A、C、E、F 類的 Safari 都對了） | 3 |
  | Line | 「打開來」（C06，有噪音） | 1 |

  - 「熒」來自繁體 token 表：簡體「荧」被對應成「熒」，台灣慣用的是「螢」。
  - 如果在 `lexicon.json` 加一條誤聽詞「熒幕→螢幕」，模擬結果是 21/24＝87.5%，仍然低於 95%。
- 數字（三十、五十）6/6 全對；純噪音 D 類 0 個指令命中，但 D01 仍然出現幻覺長句（和候選 1 是同一句）。

**速度與延遲**

| 項目 | 結果 |
|---|---|
| 載入、記憶體 | 載入 1.4 s；峰值 RSS 約 515 MB |
| RTF | greedy 0.038；beam＋熱詞 0.045 |
| 句尾要補的靜音 | 0.4 秒時 F 類只有 1/3 完整，**0.6 秒才 3/3**（候選 1 只要 0.2 秒） |
| partial 反應（等於最終文字的時間 − 說完的時間） | 中位 A 544 ms、C 736 ms、F 864 ms（候選 1 是 144～320 ms，macos 是 0.3～0.6 秒） |

- **推估**（沒有實測，要等 P1 的伺服器做好才能量）：client 端點偵測要等 partial 穩定才會送 flush，所以 E2E 大約是 partial 反應＋穩定等待（約 0.1～0.25 秒）＋收尾，A 類可能在 0.7～1.1 秒，S1（中位 ≤ 800 ms）有機會不過。
- 基底 repo `GilgameshWind/X-ASR-zh-en`（Apache-2.0，revision `689ff18c`）有 160／480／960／1920 ms 四種 chunk，但都是 fp32：
  - encoder 約 593 MB，joiner 10 MB，合計約 615 MB。
  - `tokens.txt` 的大小和候選 2 相同（58806 bytes），推測可以直接套用候選 2 的 `tokens_traditional.txt`（**沒有驗證**）。
  - 160 ms 版本理論上能把 partial 延遲降到和候選 1 同一級，fp32 也可能比 int8 稍微準一點，但都沒有實測。

**判定**：Q1 是 19/24＝79%（加誤聽詞模擬 87.5%），**不通過**。Q2 是 10.0% 對 2.6%，**不通過**。比候選 1 好很多。

### 1.7 P2 實測結果：X-ASR 伺服器對 macos-stt（2026-11-22，實際執行確認）

方法：同一台機器、同一時段，先後起 xasr 伺服器（預設設定：2 執行緒、補 0.6 秒、熱詞 score 4、43 個熱詞）與 macos-stt，各跑一次 `stt_bench.py run --corpus bench/human|synth --mode client`，label 是引擎名稱。原始結果：`logs/bench/2026-11-22_p2_{human,synth}_{macos,xasr}.jsonl`（不入版控）。真人語料（human）：

| 門檻（§7.2） | macos-stt | xasr | 判定 |
|---|---|---|---|
| **S1** A 類 E2E 中位 ≤ 800、p90 ≤ 1200 ms | 624／952 | 751／**1366** | ❌ p90 不過；**使用者接受（X20）** |
| **S4** 伺服器 `finish_ms` 中位 ≤ 150 ms | 69 | 39（p90 46、最大 79） | ✅ |
| Q1 A+C+F 意圖 | 24/24 | 19/24＝79% | ❌ 記錄、不阻擋（X14） |
| Q2 CER | 2.6% | 10.0% | ❌ 記錄、不阻擋（X14） |
| **Q3** B 類提早截斷 ≤ macos（bench 的算法：被拆句或意圖不對） | 2/8（B01、B02） | 3/8（B05、B06、B07） | ❌ 字面上不過，但內容見下；**使用者視為通過（X20）** |
| Q4 F 類句尾字 | 3/3 | 3/3 | ✅ |
| R2 D 類 0 個指令、`max_len` 0 次 | 0／0 | 0／0（有文字的 final 0 句） | ✅ |
| **P1** 閒置 CPU < 整機 5%（單核 40%，X18） | macos-stt 平均 6.1%（單核，跑 bench 期間，整機約 0.8%） | **2 執行緒單核 31.2%＝整機 3.9%**（連續送 60 秒靜音）；bench 期間單核 27.4%＝整機 3.4%；1 執行緒單核 13.4% | ✅（原先用單核 5% 判成 ❌，是門檻寫錯，見說明 1） |
| P1 RSS < 600 MB | 77 MB | 峰值 476 MB | ✅ |
| P1 RTF < 0.2 | — | 0.045 | ✅ |

各類明細（human，意圖／E2E 中位）：A 15/15→12/15、624→751 ms；B 6/8→5/8、481→894 ms；C 6/6→4/6、448→1000 ms；E 4/4→3/4、358→480 ms；F 3/3→3/3、465→1072 ms。synth：A 15/15→12/15、B 8/8→6/8（xasr 有 2 句被拆句）、F 3/3→2/3（Q4 在 synth 上是 2/3）；這張表以 human 為準（§7.2）。

**逐項說明**

1. **P1 閒置 CPU（原本判成阻擋，X18 改用整機定義後通過）**：
   - 麥克風一直開著，client 會持續送音訊（包括靜音），伺服器每一段都要解碼；模型每 480 ms 喚醒一次執行緒池，空轉成本比連續處理高。
   - 同一個模型在程序內連續處理 60 秒靜音只要 14.7%（P0 的 RTF 0.045 是牆鐘時間，不是 CPU 時間），所以 P0 低估了即時串流的成本。
   - **計畫寫的「單核 5%」是我引用 ADR 0013 時的誤讀**：0013 的 5% 是**整台電腦**（8 核 M1，等於單核 40%），而且那是桌面角色的預算（桌面角色自己已經佔 38～42%）。按 0013 的定義 31% 不到 40%，但加上桌面角色就超過；按計畫的字面 31% 是 6 倍超標。
   - 使用者 2026-11-22 確認：CPU 一律用整機百分比（全核），不用單核（X18）。這台 8 個邏輯核心，31.2% ÷ 8 ＝ 3.9% < 5%，通過。
   - 沒有做的事：原先建議的「靜音時不送進模型」（Q10 選項 a）。門檻改成整機定義後已經過，加它是新增行為、要補測試與重量準確度，所以沒做（X19）。
   - 仍然成立的風險：整機 3.9% 是只開辨識伺服器的數字；macOS 上若同時開桌面角色（0013 的預算本身就在 5% 附近），總和會超過 5%。X-ASR 是 Windows 專用的備用引擎，桌面角色在 Windows 的表現不在本次範圍（X4）。
2. **S1 的 p90（原本判成阻擋，X20 接受）**：中位 751 ms 過了，p90 1366 ms 不過。拉高 p90 的是三類句子：
   - **辨識錯字，導致端點走預設 1.3 秒**：A13、A14「鎖定熒幕」、A15「鎖定一幕」都不是已知指令，`completeness` 判成 unknown，要等 `EP_SILENCE_DEFAULT_S`（1.3 秒）才收，E2E 約 1.35 秒。X15（熒幕→螢幕）會讓 A13、A14、C05 變成完整指令而提早收，A15 的「一幕」不會。
   - **A12「靜音」的第一個 partial 晚到 2.0 秒以上**（說話開始後 2.03 秒才出字，說完後 1.4 秒）：client 的 `EP_NO_TEXT_RESET_S`＝2.0 把這一段當噪音丟掉，最後靠伺服器的 3 秒安全網才收，E2E 約 4.95 秒。離線解碼（P0）同一句的反應也是 1824 ms，是模型的特性，不是伺服器的問題。用 bench 覆寫 `--ep no_text_reset_s=3.5` 量過：A12 變成 1441 ms 收尾、意圖正確（只是量測，沒有改產品設定）。
   - 即使如此，A15 與 A12 仍會讓 15 句的 p90 停在 1.3～1.4 秒左右，所以光靠 X15 解不掉 S1 的 p90。
3. **Q3（字面不過，X20 視為通過）**：B 類的 3 個「失敗」全是辨識錯字，不是被截斷：B05、B06「打開沙發率， 然後搜尋天氣」、B07「關掉， 沙發綠」（Safari 被聽成「沙發率」）。**拆句 0 次**；而 macos-stt 的 B01、B02 才是真的前文被丟（只剩「小算盤」），xasr 兩句都完整（「打開小算盤」）。也就是說這項在「截斷」的意義上 xasr 比 macos-stt 好，在 bench 的算法上輸 1 句。
4. **端點偵測沿用**：沒有改任何 `EP_*`。E2E 比 macos 慢的原因是 partial 比說話晚 0.5～0.9 秒（480 ms chunk），A 類端點中位 693 ms（macos 550 ms）。
5. **L1**（client 送出的 flush 次數＝伺服器 log）：human 37＝37、synth 38＝38；human 另有 1 次 `server_vad`（A12）。
6. **量測假象（兩個引擎一樣）**：A07、A09 的 E2E 是負值（macos −1429／−2380、xasr 約 −1078／−1970），因為離線估的「真值說完時間」比錄音實際說完的時間晚。兩邊的中位數都含這兩個負值，比較仍然公平，但絕對值偏低。
7. **文字品質（人工核對）**：xasr 輸出的英文是小寫且和中文之間有空白（`safari`、`打開 safari`），標點是全形逗號後接空白（「打開沙發率， 然後搜尋天氣」），路由與正規化都能處理；數字是中文（「音量調到三十」），macos 是阿拉伯數字，兩者經過 `normalize` 後意圖相同。剩下的錯誤與 P0 相同：螢幕（A13～A15、C05）、B 類的 Safari、Line（C06「打開來」）、E04 句尾的「螢幕」整個不見。

## 2. 決策

| # | 決策 | 來源 |
|---|---|---|
| X1 | 使用 `Luigi/x-asr-zh-tw-en-streaming-ft75m` 作為可切換的第二個 STT 引擎，目標是能在 Windows 執行 | 使用者 2026-11-21 |
| X2 | 切換點是 WS 協定 v1：新引擎是一個獨立子程序，client 端的端點偵測與路由完全共用 | 計畫建議，使用者 2026-11-21 同意 |
| X3 | 新引擎完全在本機執行，不連網（模型檔由使用者事先下載）。不送任何音訊或文字到外部 | 北極星、[0009](../../decisions/0009-cloud-optional.md) |
| X4 | 範圍：先只做語音辨識這一層；Windows 全流程另開計畫（原 Q1） | 使用者 2026-11-21 |
| X5 | 模型放在 `AI_models/x-asr-zh-tw-en-streaming-ft75m/`（不入版控），固定 revision `90fe6b8a`；README 寫下載指令，不自動下載（原 Q3） | 使用者 2026-11-21 |
| X6 | 用途：個人使用，專案開源。模型權重不放進 repo，由使用者自己從 Hugging Face 下載；README 註明模型授權是研究用、不可商用（原 Q5） | 使用者 2026-11-21 |
| X7 | 驗收沿用 2026-11-05 端點計畫的定義與門檻，並與 macos-stt 的測試結果比較（原 Q6） | 使用者 2026-11-21 |
| X8 | 英文大寫不處理：伺服器不改文字，路由本來就不分大小寫（原 Q7，P0-3 實測） | P0-3 |
| X9 | 在分支 `windows-compat` 上開發 | 使用者 2026-11-21 |
| X10 | 候選 1 沒過，改測候選 2 `Luigi/x-asr-zh-tw-native-itn`（原 Q8 選 (b)） | 使用者 2026-11-21 |
| X11 | 候選 2 用模型內建的繁體 `tokens_traditional.txt`，不用 OpenCC、cn2an（避免新增 dependency；數字由既有的 `normalize` 處理） | P0（§1.6）：簡體＋`normalize` 只有 12/24 |
| X12 | 用候選 2 進 P1，定位是 **Windows 專用、比 macos-stt 不準的備用引擎**；macOS 預設維持 `macos`（原 Q9 選 (b)，Q2 一併結案） | 使用者 2026-11-21 |
| X13 | 候選 2 的執行設定：`modified_beam_search`、熱詞 score 4、`XASR_TAIL_PAD_S`＝0.6、`XASR_THREADS`＝2 | P0（§1.6）最好的組合 |
| X14 | 驗收門檻維持 §7.2，不另定 Windows 門檻；準確度已知不過的項目照實記錄、不阻擋，其他項目照常阻擋 | 使用者 2026-11-21（「驗證門檻先保持」）；不阻擋的範圍是計畫依 X12 推導的，見 §7.2 |
| X15 | `lexicon.json` 的 `term_misheard` 加「熒幕→螢幕」（原 Q4）。只加這種固定的字形對應，不加「沙發率→Safari」這種聽錯的拼法 | 使用者 2026-11-21 |
| X16 | 沒有 Windows 機器：Windows 改用**本機（Mac）跑 X-ASR 模擬**，分階段切換，每一階段都可以單獨退回（§6 P3）。真的 Windows 實機不在本次範圍 | 使用者 2026-11-21（「用本機跑 STT 模型模擬 windows 逐步切換」）；階段怎麼切是計畫的解讀，見 §6 P3 |
| X17 | 候選 1 的 `AI_models/x-asr-zh-tw-en-streaming-ft75m/` 先保留，不刪；程式與文件不引用 | 使用者 2026-11-21 |
| X18 | CPU 門檻一律用**整機百分比（全核）**，不用單核；這台 8 個邏輯核心，整機 5% ＝ 單核 40%（§7.2 P1 同步改）。xasr 閒置單核 31.2% ＝ 整機 3.9%，通過 | 使用者 2026-11-22（「CPU 是用全核％，不要用單核」） |
| X19 | 閒置 CPU 不做「靜音時不送進模型」（原 Q10 選項 a），接受現況（原 Q10 選項 b）。**這是計畫的解讀**：使用者回「Q10 同意」，同一則訊息把 CPU 改成整機定義，門檻已經過，所以沒有新增行為；若原意是要做選項 a，要另外說 | 使用者 2026-11-22；解讀見左 |
| X20 | S1 p90 1366 ms（門檻 1200）接受；Q3 視為通過。不改 `EP_NO_TEXT_RESET_S`、不試 160 ms 模型 | 使用者 2026-11-22（「Q11 Q12 通過」） |

## 3. 待確認（決定後移到 §2）

目前沒有待確認的項目。已結案：Q1→X4、Q2→X12、Q3→X5、Q4→X15、Q5→X6、Q6→X7、Q7→X8、Q8→X10、Q9→X12、Q10→X19（連同 X18）、Q11、Q12→X20。

## 4. 設計

### 4.1 為什麼切換點選在 WS 協定（X2）

- **最小改動、零回歸**：macOS 預設路徑一行都不變。`SttClient`、`EndpointDetector`、`completeness`、`CommandRouter` 共用同一套，端點偵測的調校成果直接沿用。
- **可以直接 A/B**：`stt_bench.py` 打的是 `ws://127.0.0.1:8765`，換一個伺服器就能用同一份語料、同一組指標比兩個引擎，不用寫第二套 bench。
- **隔離**：推論在另一個程序，ONNX 的執行緒不會跟 `command_worker` 搶 GIL，伺服器當掉也有現成的斷線重連。
- 另一個做法是在程序內直接呼叫 sherpa-onnx，可以省掉本機 WS 往返（約 1 ms 等級）。但要把 `SttClient` 抽成介面，還要另寫 bench 路徑，收益不值得，所以不採用。

### 4.2 `mac_ai/stt/xasr_server.py`（新檔）

- 啟動指令（用主專案的 venv）：

  ```
  python -m mac_ai.stt.xasr_server --port 8765 --model-dir <dir> --tokens tokens_traditional.txt
    --hints vocab/stt_hints.txt --hotwords-score 4 --threads 2 --tail-pad 0.6
  ```

  參數預設值都來自 `config`（§4.3）。
- 只用已經有的 `websockets`（asyncio server，`process_request` 處理 `GET /health`），**不引入 FastAPI／uvicorn**。
- 啟動時建立一個 `sherpa_onnx.OnlineRecognizer.from_transducer(...)`：
  - `decoding_method="modified_beam_search"`、`max_active_paths=4`
  - `modeling_unit="cjkchar"`、`hotwords_file=<啟動時產生的暫存檔>`、`hotwords_score=4`
  - `num_threads=2`
- 每條 WS 連線各開一個 `OnlineStream`；同一個 recognizer 可以服務多個 stream。
- **熱詞**：新檔 `mac_ai/stt/xasr_hotwords.py`，純函式 `encode_hotwords(hints, tokens) -> (lines, skipped)`，不 import sherpa：
  - 中文字轉成「▁字」。
  - 英文與數字轉小寫，依 token 表做最長比對切開，第一段加「▁」。
  - 任何一段切不出來，整個詞略過（例如 `vscode`、`vs code`）。
  - 去掉重複、保持原本順序。
  - 演算法就是 P0 用的那一套：43 行可以用、2 行略過。
  - 伺服器啟動時把結果寫到暫存的熱詞檔。
  - sherpa 還有另一種做法 `create_stream(hotwords=...)` 可以每條連線各自給熱詞，但字串格式沒有驗證過，所以不用。

模型目錄裡要有的檔案：`encoder.int8.onnx`、`decoder.onnx`、`joiner.int8.onnx`、`tokens_traditional.txt`。缺任何一個就啟動失敗，錯誤訊息列出缺了哪些檔。

協定對應：

| 協定 v1 | X-ASR 伺服器的做法 |
|---|---|
| `config.sample_rate` | 只接受 16000，其他值回 `error` 並關閉連線（錯誤碼對齊 macos-stt，由 P1 的協定對照測試確認） |
| `config.lang` | 接受 `zh-TW`、`en-US`；這個模型本來就中英混合，語言只做檢查不切模型 |
| `config.hints` | 熱詞在伺服器啟動時就固定（`--hints`）。連線送來的 `hints` 如果有啟動時沒有的詞，就忽略並寫進 log，不重建 recognizer。本專案 client 送的就是同一份 `stt_hints.txt`，所以實際上不會有差異。`/health` 回報可用與略過的熱詞數量 |
| `config.multi_utterance` | 只支援 `false`（目前的設定）。明確送 `true` 會回 `error`（code `protocol`）並以 1002 關閉：本引擎沒有引擎自斷句，老實拒絕，不假裝支援（原本計畫寫「`true` 改用 sherpa endpoint rule」，沒有人用，不實作）。沒送這個欄位視為 `false` |
| `config.vad_flush_seconds` | client 沒送 flush 時的安全網：**文字停止變動滿這麼久（以收到的音訊長度計，不看牆鐘）且有文字**就自動收尾，`reason="server_vad"`。原本計畫寫對應 sherpa 的 `rule2`，但 sherpa 的端點規則在建立 recognizer 時就固定，沒辦法每條連線各自設定，所以改成伺服器自己算 |
| `config.flush_pre_delay_seconds` | 這是 macos-stt 的「endAudio 前等待」，X-ASR 不用（client 送的是 0）。尾端靜音固定用伺服器的 `--tail-pad`（`XASR_TAIL_PAD_S`＝0.6，P0 實測：0.4 秒 F 類只有 1/3 完整），回報在 `timing.pre_delay_ms` |
| binary PCM | int16 → float32/32768 → `accept_waveform`；`while is_ready: decode_stream`；文字有變化就送 `partial` |
| `{"type":"flush"}` | 這一句還沒有文字：什麼都不做（與 macos-stt `session.flush` 相同）。有文字：補尾端靜音 → `input_finished` → 解碼到底 → 送 `final`（`timing.reason="client_flush"`、`pre_delay_ms`、`finish_ms`）→ 換新的 stream → 送 `session_restarted`（與 macos-stt 的事件順序相同） |
| `{"type":"close"}` | 結束連線，**不送 `final`**（macos-stt 的 `feed` 收到 close 就結束，`session.close` 之後不再送事件；原本計畫寫「先送 final」，改成以 macos-stt 為準） |
| 空字串的 final、事件順序、錯誤碼 | 以 macos-stt 的實際行為為準：沒有文字就不送 `final`；錯誤碼 `protocol`（1002）、`too_many_sessions`（1013）、`internal`（1011）。`tests/real/test_stt_protocol_live.py` 對兩個伺服器跑同一組案例（§6 P1-2） |
| 其他 | `max_session_seconds`（預設 300，與 macos-stt 相同）：這條 stream 收滿這麼多音訊就收尾重開，有文字才送 `final`（`reason="max_session"`），一定送 `session_restarted`。奇數長度的音訊多出來的 1 byte 留到下一則，不丟 |
| `GET /health` | `{"status":"ok","engine":"xasr","model":<目錄名>,"hints":...}`；模型載入完成才回 200 |

- 輸出的文字不做簡轉繁（模型已經是繁體）、不補標點、不改大小寫（X8）。
- 一次只服務一個使用者：最多 2 條連線（bench 與正式不同時跑），超過就回 `too_many_sessions`（macos-stt 是 3 條）。
- 啟動時 stdout 會印 `[flush] reason=… pre_delay_ms=… finish_ms=… text_len=…`，格式與 macos-stt 相同，`stt_bench.py` 靠它核對 client 與 server 的 flush 次數（L1）：跑 bench 時要把伺服器的輸出導到 `/tmp/macstt_server.log`（`python -m mac_ai.stt.xasr_server > /tmp/macstt_server.log 2>&1 &`；macos-stt 的 `start.py` 自己會寫這個檔）。

### 4.3 選擇引擎與啟動

- `config.py` 新增以下常數，數值旁附上 P0 實測理由的註解：

  | 常數 | 值 |
  |---|---|
  | `STT_ENGINE` | `"xasr" if sys.platform == "win32" else "macos"` |
  | `XASR_MODEL_DIR` | `ROOT / "AI_models" / "x-asr-zh-tw-native-itn"` |
  | `XASR_TOKENS` | `"tokens_traditional.txt"` |
  | `XASR_THREADS` | `2`（P0：1／2／4 執行緒的 RTF 是 0.043／0.032／0.040） |
  | `XASR_TAIL_PAD_S` | `0.6`（P0：0.4 秒 F 類 1/3，0.6 秒 3/3） |
  | `XASR_HOTWORDS_SCORE` | `4.0`（P0：score 2 是 18/24，score 4 是 19/24） |

- 埠號沿用 `STT_PORT`，兩個引擎不會同時執行，`STT_WS_URL` 不用改。
- `services.py` 新增 `xasr_stt_service()`：
  - 用 `sys.executable -m mac_ai.stt.xasr_server` 啟動。
  - 模型目錄或必要檔案不存在時，丟出 `ServiceError`，訊息附上下載指令。
  - ready 只檢查 `/health` 的 `status`，**不檢查** macOS 語音辨識授權。
  - 沒有 `after_stop`（不需要 PID 檔，直接收尾子程序就好）。
- `main.py` 新增 `--stt-engine {macos,xasr}`，覆寫 `config.STT_ENGINE`。在非 darwin 平台選 `macos` 時，直接報錯結束。
- `stt_bench.py` 不改：先手動起其中一個伺服器，再跑 bench。`--label` 寫上引擎名稱，例如 `--label xasr`。
- README 新增模型下載指令：固定 revision `3d5bad21`，只抓 `deployment/onnx-int8-480ms/` 的 4 個必要檔案。另外註明：
  - 授權是 Apache-2.0。
  - 這是比 macos-stt 不準的備用引擎，數字參考 §1.6。
- 候選 1 的 `AI_models/x-asr-zh-tw-en-streaming-ft75m/` 先保留不刪（X17），程式與文件都不引用它。

### 4.4 不改的東西（明確列出）

- macos-stt 子專案（獨立 repo）、`SttClient`、`EndpointDetector`、`completeness`、Silero VAD、`assistant.py`、路由與守門。
- `EP_*` 端點參數：先沿用。P2 的數字如果顯示需要另外一組參數，就停下來問，不默默加「依引擎切換」的設定。

## 5. 影響範圍

| 檔案 | 改動 |
|---|---|
| `mac_ai/stt/xasr_server.py` | 新增（約 200 行） |
| `mac_ai/stt/xasr_hotwords.py` | 新增，純函式（約 40 行） |
| `mac_ai/config.py` | 新增 6 個常數（§4.3） |
| `vocab/lexicon.json` | `term_misheard` 加一條「熒幕→螢幕」（X15） |
| `tests/unit/test_normalize.py` | 加「鎖定熒幕」→「鎖定螢幕」的案例 |
| `tests/runtime/test_winsim.py`（或併入 `test_main.py`） | 模擬 Windows 的平台預設與服務選擇（§6 P3、A8） |
| `mac_ai/services.py` | 新增 `xasr_stt_service()` |
| `main.py` | `--stt-engine`，依引擎選擇 service |
| `tests/unit/`、`tests/runtime/` | 協定對照、伺服器、服務選擇的測試（§7.1） |
| 相依套件 | 新增 `sherpa-onnx==2.0.2`（已裝進主 venv，README 的套件清單要補上） |
| 文件 | `ARCHITECTURE.md`（行程、模組、延遲表）、`docs/flows/main-flow.md`、`docs/guides/testing.md`（新的 bench 指令）、`README.md`（下載模型、模型授權、切換方式）；結案時新增 ADR（STT 引擎可切換、預設值、模型與授權） |

## 6. 實作步驟

### P0 可行性實測（已完成，結果見 §1.5）

| # | 做什麼 | 完成條件 | 狀態 |
|---|---|---|---|
| P0-1 | 安裝 `sherpa-onnx`，載入模型，對 `bench/human` 做離線串流解碼 | 記錄載入時間、RSS、RTF（1／2／4 執行緒） | ✅ 完成 |
| P0-2 | 比較 0／0.2／0.4／0.6／0.8 秒的尾端靜音補齊 | 找出句尾完整的最小補齊時間 | ✅ 0.2 秒 |
| P0-3 | 英文大寫與中英之間的空白會不會影響路由 | 跟原句結果相同 | ✅ 通過 |
| P0-4 | 熱詞（`cjkchar`、`cjkchar+bpe`、`bpe`） | 能不能用、對意圖與 RTF 的影響 | ✅ 英文不能用，中文有限 |
| P0-5 | Windows 實機 import 與解碼 | 可以 import、可以解碼、麥克風可以開 | ⏸ 沒有 Windows 機器 |
| — | 候選 1 準確度與 macos-stt 比較（X7） | A+C+F 意圖 ≥ 95% 且 ≥ macos；CER ≤ macos＋1 pt | ❌ 15/24、24.9% |
| — | 候選 2 準確度與 macos-stt 比較（X7） | 同上 | ❌ 19/24、10.0%（§1.6） |

P0 結案，採用候選 2（X12）。之後要換模型重跑，用環境變數指定模型與 token 表，不用改腳本：

```bash
XASR_MODEL=AI_models/<目錄> XASR_TOKENS=tokens_traditional.txt \
  python logs/xasr_p0/p0_xasr.py accuracy --method modified_beam_search \
  --hotwords logs/xasr_p0/hw_itn_general.txt --unit cjkchar --score 4
```

`XASR_CORPUS=synth` 可以改跑合成語料。

### P1 伺服器與協定

| # | 做什麼 | 驗證 |
|---|---|---|
| P1-1 | `xasr_hotwords.encode_hotwords`：先寫測試再實作 | A7 通過；對 `stt_hints.txt` 的輸出與 `logs/xasr_p0/hw_itn_general.txt` 逐行相同（43 行） |
| P1-2 | 協定對照測試：用固定的 PCM（從 `bench/human` 截取 2 段短音檔放到 `tests/fixtures/stt/`）定義事件序列的斷言。macos-stt 的部分只在 `-m live` 跑，用來確認錯誤碼與空 final 的行為 | 測試寫好，xasr 的部分先失敗 |
| P1-3 | 實作 `xasr_server.py` | A1～A3 通過；`/health` 回報模型名稱與熱詞數量 |
| P1-4 | 冒煙測試：起伺服器，跑 `scripts/stt_bench.py run --corpus bench/human --mode client --label xasr --only A` | 產出 jsonl，沒有斷線、沒有 `error` 事件；A 類意圖與 P0 的離線結果（12/15）差不超過 1 句。差太多代表伺服器和離線解碼不一致，要先查 |

**P1 實際結果（2026-11-22，實際執行確認）**

| # | 結果 |
|---|---|
| P1-1 | `tests/unit/test_xasr_hotwords.py` 14 項通過；`stt_hints` 快照（`tests/fixtures/stt/xasr_hints_2026-11-21.txt`，54 行）在真的 token 表上編碼出 43 行，與 P0 驗證過的熱詞檔（`xasr_hotwords_2026-11-21.txt`）逐行相同，略過 `vscode`、`vs code`（A7） |
| P1-2 | 協定測試寫在 `tests/runtime/test_xasr_server.py`，每個斷言的註解標出 macos-stt 的出處。`tests/real/test_stt_protocol_live.py`（`-m live`）對正在 8765 埠的伺服器跑 7 個只用靜音與壞訊息的案例：**macos-stt 7 項通過、xasr 7 項通過**，兩邊行為一致。**和計畫不同的地方**：計畫寫「從 `bench/human` 截 2 段放進 `tests/fixtures/stt/`」，但 `*.wav` 不入版控（真人錄音，`.gitignore`），所以離線測試改用假引擎（依累積音訊長度吐預先寫好的文字），只有「真模型」那一項用 `bench/human` 的錄音，缺檔就 skip |
| P1-3 | `mac_ai/stt/xasr_server.py`。`pytest tests/runtime/test_xasr_server.py` 28 項通過（A1～A3 用假引擎，另有 1 項載入真模型、串流一段真人錄音、flush，`finish_ms` < 150）。`/health` 回報 engine、model、`sessions_active`、`max_sessions`、`hotwords`、`hotwords_skipped`。實作時依 macos-stt 實際程式碼調整的協定細節見 §4.2 |
| P1-4 | 起伺服器（載入 1.6 s，熱詞 43 個、略過 2 個）跑 `stt_bench.py run --corpus bench/human --mode client --label xasr --only A`：15 句、每句 1 個 final，沒有斷線、沒有 `error` 事件；A 類意圖 **12/15**，與 P0 離線結果（12/15）相同 |

L2 `pytest`：2598 passed、6 skipped、0 failed（基線 2556＋新增 42），既有測試數量沒有減少（A6 的部分驗證：目前只改了 `config.py` 加常數）。連跑 22 次 `test_xasr_server.py`，只有第一次在某個測試的 teardown 出現過一次逾時，之後沒有重現，原因沒查出來。

### P2 量測與比較（同一時段、同一台機器）

1. macos-stt：`--label macos` 跑 `bench/human` 與 `bench/synth`。xasr：`--label xasr` 跑一樣的語料。
2. `stt_bench.py report <macos> <xasr>`，對照 §7.2：
   - Q1、Q2 照實記錄（預期 FAIL，X14）。
   - 其他項目不過，就停下來回報，不調門檻、不調 `EP_*`。
3. 把 §7.2 的完整結果與逐段文字對照補進本計畫的 §1.7。**已完成（2026-11-22）**：結果在 §1.7；三項阻擋項已由使用者決定（X18～X20），接著做 P3。

### P3 接進主程式

| # | 做什麼 | 驗證 |
|---|---|---|
| P3-1 | `config`、`services`、`main.py --stt-engine` | A5、A8、A9 通過；`pytest`（L2）0 failed（A6） |
| P3-2 | `lexicon.json` 加「熒幕→螢幕」（X15），`test_normalize.py` 加案例 | `pytest tests/unit/test_normalize.py tests/unit/test_completeness.py tests/unit/test_endpoint.py` 通過（紅線 3）；重跑 P2 的 xasr bench，A+C+F 意圖不下降，A13、C05 變對 |
| P3-3 | 依下面的階段，在本機逐步切到「模擬 Windows」（X16） | 每一階段的驗證都過了才進下一階段 |

**逐步切換（本機模擬 Windows，X16）**

每一階段只多加一層「像 Windows」的條件，不過就停在那一階段回報。退回的方式都是不帶參數重跑 `python main.py`，所以不會影響 macOS 的日常使用。

| 階段 | 本機狀態 | 做法 | 驗證 | 退回 |
|---|---|---|---|---|
| S0 基線 | macos-stt（現況） | 不帶參數 `python main.py` | M2；P2 的 macos bench 數字 | — |
| S1 引擎單獨跑 | xasr 伺服器單獨起，主程式不動 | `python -m mac_ai.stt.xasr_server`＋`stt_bench.py` | P1-4、P2（§7.2） | 關掉伺服器 |
| S2 主程式手動切換 | `main.py --stt-engine xasr`，執行器仍然是 macOS 的 | 平常的語音使用，至少說 10 句 | M1、M3 | 拿掉參數 |
| S3 模擬 Windows 平台判斷 | 讓程式以為自己在 Windows：`sys.platform` 是 `win32` | 自動化用 monkeypatch（A8、A9）；手動用 W2 的指令，不帶 `--stt-engine` 也要選到 xasr | A8、A9、W2 | 拿掉模擬指令 |
| S4 模擬 Windows 安裝環境 | 另開一個乾淨的 venv，只裝 Windows 版會裝的套件（沒有 `mlx`、`mlx_vlm`、`alembic` 等） | 在這個 venv 起 xasr 伺服器、跑 import 檢查與 bench | W1、W3、W4；另外查 `win_amd64` 的 wheel 有沒有（W5） | 刪掉這個 venv（放在 scratchpad，不入版控） |

模擬做不到的事列在 §8，ADR 要寫明「**Windows 只在 macOS 上模擬過，沒有實機測試**」。

**P3 實際結果（2026-11-22）**

| # | 結果 |
|---|---|
| P3-1 | `config.py` 加 `STT_ENGINE`（`win32` 預設 `xasr`，其餘 `macos`）與 `XASR_*` 常數；`services.py` 加 `xasr_stt_service()`（缺模型檔就丟 `ServiceError` 並列出缺哪個檔）；`main.py` 加 `--stt-engine`，在非 macOS 選 `macos` 會直接拒絕。A5、A8、A9 通過（`tests/runtime/test_services.py`、`tests/runtime/test_main.py`，含一項在模型存在時真的起停 xasr 子程序） |
| P3-2 | `lexicon.json` 加「熒幕→螢幕」（`source: seed`），`test_normalize.py`、`test_route_fast.py` 加案例；`stt_hints.txt` 沒有變。紅線 3 的 `test_completeness.py`、`test_endpoint.py` 通過。重跑 xasr 的 human bench（`logs/bench/2026-11-22_p3_human_xasr.jsonl`）：A 12/15→**14/15**（A13、A14 變對；剩 A12「靜音」與 A15「鎖定一幕」），C 4/6→**5/6**（C05 變對），F 3/3，**A+C+F＝22/24＝92%**（先前 19/24＝79%），仍低於 95%，Q1 照 X14 記錄、不阻擋。A 類 E2E 中位 739 ms、p90 1370 ms（P2：751／1366），p90 沒有因為 X15 而過線，與 §1.7 的預測相同 |
| S0 | macos-stt、不帶參數（P2 的 macos bench 數字） |
| S1 | 伺服器單獨起，bench 的結果就是 §1.7 與上面 P3-2 |
| S2 | `python main.py --stt-engine xasr` 實際啟動：LLM、STT 就緒、`/health` 回 `engine: xasr`、SIGTERM 後服務收乾淨、8765 釋放。**M1（平常說至少 10 句）、M3（kill 伺服器再恢復）沒有人聲實測，留給使用者**。S2 期間環境雜音曾被辨識成「Sh」並交給 Slow Path 回聊天（模型對雜音會吐字；§7.4 的噪音幻覺不在本次範圍，但這是實際觀察到的現象） |
| S3 | A8、A9（monkeypatch `sys.platform`）通過。手動 W2：`import ssl` 等第三方套件先載入、把 `ssl.SSLContext._load_windows_store_certs` 打樁成空函式、再改 `sys.platform = 'win32'` 並 `importlib.reload(config)`，執行 `main.main([])`：`STT_ENGINE = xasr`、不帶 `--stt-engine` 起的就是 xasr、`/health` 回 `engine: xasr`、「已連上 STT，開始收音」、收尾乾淨。這個模擬不加進產品程式。模擬下印出「偏好資料庫無法使用（`_sysconfigdata__win32_darwin`）」是 `sys.platform` 被改掉後 `sysconfig` 找不到對應檔，只在模擬時出現，不是產品問題。**W2 要求的「說 5 句」沒有人聲實測** |
| S4／W1 | 乾淨 venv（`uv venv --python 3.12`，只裝 `sherpa-onnx==2.0.2 sounddevice onnxruntime websockets numpy`，共 11 個套件，確認沒有 `mlx`、`mlx_vlm`、`alembic`、`sqlalchemy`、`requests`、`httpx`、`pytest`）起 `python -m mac_ai.stt.xasr_server`：`/health` 200，載入 2.25 s（第二次 1.45 s），閒置 RSS 465 MB。**未釘版本時裝到 numpy 2.5.5、onnxruntime 1.32.0，比開發用的 2.5.4、1.31.1 新，仍可正常運作** |
| S4／W3 | `import mac_ai.stt.client, mac_ai.stt.mic, mac_ai.stt.xasr_server` 在乾淨 venv 通過，沒有缺任何模組（§1.4 最後一列的顧慮沒有發生）；`scripts/stt_bench.py --help` 也通過 |
| S4／W4 | 乾淨 venv 的 `stt_bench.py run --corpus bench/human --mode client --label xasr-winsim --only A`：跑完 15 句、每句 1 個 final、沒有 error；**15 句辨識文字與 S1 逐句相同**，意圖 14/15（同 S1），E2E 中位 779 ms、p90 1369 ms（同機器 S1：739／1370）。原始結果 `logs/bench/2026-11-22_w4_human_xasr_winsim_A.jsonl` |
| S4／W5 | `win_amd64` 安裝檔都下載得到：`sherpa_onnx-2.0.2-cp312-cp312-win_amd64`、`sherpa_onnx_core-2.0.2-py3-none-win_amd64`、`onnxruntime-1.31.1-cp312-cp312-win_amd64`、`sounddevice-0.5.6-py3-none-win_amd64`、`numpy-2.5.4-cp312-cp312-win_amd64`、`websockets-17.4-cp312-cp312-win_amd64`。只確認檔案存在，不代表能在 Windows 跑 |
| A6 | 完整 `pytest`：**2621 passed、6 skipped、0 failed**（基線 2556；6 個 skip 都是這台沒裝 Google Chrome 的 AppleScript 編譯測試）。`cd desktop && node --test && npm run typecheck`：266 pass、typecheck 通過 |

模擬用的乾淨 venv 與下載的 wheel 已刪除（放在 scratchpad，不入版控）。

### P4 文件與結案

同步 `ARCHITECTURE.md`、flows、testing、README；新增 ADR；計畫移到 `completed/`。**已完成（2026-11-22）**：README 新增「備用辨識引擎」一節（下載指令、SHA-256、授權、實測比較、Windows 只在 macOS 模擬過）；`ARCHITECTURE.md`（行程、模組職責）、`docs/flows/main-flow.md`、`docs/guides/testing.md`（對照表、基線）、`AGENTS.md`（北極星一句、紅線 15）已同步；新增 [ADR 0020](../../decisions/0020-stt-engine-xasr.md)；本計畫移到 `docs/plans/completed/`。

## 7. 驗收計畫

### 7.1 自動化測試

| # | 驗收項目 | 指令 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| A1 | 協定：config → PCM → flush → final 的事件順序與欄位（`timing.reason`、`pre_delay_ms`、`finish_ms`） | `pytest tests/runtime/test_xasr_server.py` | 全部通過；final 的 `reason` 是 `client_flush` | 修伺服器，不改測試 |
| A2 | 協定錯誤路徑：sample_rate≠16000、沒送 config、第 3 條連線 | 同上 | 錯誤碼與關閉碼和 macos-stt 一致（以 §4.2 的對照表為準） | 同上 |
| A3 | 安全網：不送 flush，靜音超過 `vad_flush_seconds` 就自動 final | 同上 | `reason="server_vad"`，在 vad_flush_s＋0.5 秒內收到 | 同上 |
| A4 | `SttClient` 對 xasr 伺服器：重連、`utt_id`、`latency.jsonl` 欄位齊全 | `pytest tests/runtime/test_stt_client.py`（加一組參數化） | 全部通過 | 同上 |
| A5 | 服務選擇：`--stt-engine xasr` 起 xasr service；模型目錄不存在時是 `ServiceError`，訊息附下載指令；非 darwin 選 macos 會報錯 | `pytest tests/runtime/test_services.py tests/runtime/test_main.py` | 全部通過 | 同上 |
| A6 | macOS 預設不變 | `pytest`（L2） | 0 failed，原有測試數量不減少 | 找出回歸，修好才繼續 |
| A7 | 熱詞切 token：中文、英文大小寫、中英混合、切不出來的詞（`vscode`）要略過、去重、保持順序 | `pytest tests/unit/test_xasr_hotwords.py` | 全部通過；`stt_hints.txt` 的輸出正好是 43 行，`vscode`、`vs code` 被略過 | 修函式 |
| A8 | 平台預設（S3）：monkeypatch `sys.platform` 是 `win32` 時，重新載入的 `config.STT_ENGINE` 是 `xasr`，`darwin` 時是 `macos` | `pytest tests/runtime/test_winsim.py` | 全部通過 | 同上 |
| A9 | 模擬 Windows 的服務選擇（S3）：`win32` 下 `--stt-engine macos` 會報錯結束；選 xasr 時不讀 `/tmp/macstt_pid`、不呼叫 `ps`、不檢查語音辨識授權（用替身記錄 `subprocess` 呼叫並斷言沒有） | 同上 | 全部通過 | 同上 |
| A10 | 誤聽詞（X15）：「鎖定熒幕」正規化成「鎖定螢幕」，並命中鎖定螢幕的動作 | `pytest tests/unit/test_normalize.py tests/flow/test_route_fast.py` | 全部通過 | 同上 |

新增的測試依 `docs/guides/testing.md` 標 marker：A7、A10 是 `unit`／`flow`；A1～A5、A8、A9 是 `runtime`。A1～A3 會載入真的模型（約 1.4 秒），模型目錄不存在就 skip 並寫明原因。

### 7.2 真實模型執行（bench；沿用 2026-11-05 端點計畫的定義，X7）

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
| P1 | 伺服器 CPU、記憶體 | 閒置（沒說話）CPU < **整機 5%**（8 個邏輯核心，等於單核 40%，X18）；RSS < 600 MB；RTF < 0.2 | 不能拖累 [0013](../../decisions/0013-fullrig03-cpu-budget.md) 的整機預算。原本寫「單核 5%」是誤讀 0013 | 停下來回報 |

「停下來回報」的意思是：附上數字，由你決定要放寬門檻、調參數，還是接受現況；不會自己調。

**`stt_bench.py report` 內建的判定與這張表不同，看的時候要換算**：它的 S1 還要求「比 baseline 快 ≥ 40%」（那是 2026-11-05 端點計畫比較「改成 client 端點之前」的設計，拿來比兩個引擎沒有意義）；它的 S4 看的是 `pre_delay_ms + finish_ms`（xasr 的 `pre_delay_ms` 是補進去的 0.6 秒靜音，所以必然不過；本計畫的 S4 只看 `finish_ms`）；它的 P1 是「VAD 每窗耗時」，**不是**本表的伺服器 CPU／記憶體（那個要另外量，§1.7）；它的 Q3 是「被拆句，或意圖不對」，所以辨識錯字（例如把 Safari 聽成「沙發率」）也會算進去。本表的 S1、S4、P1 以這裡的定義為準。

- **輸出內容要另外人工核對**：CER 與意圖正確不等於文字正確。報告附上逐段文字對照（`report --detail`），人工看英文 App 名稱、數字、繁簡用字。
- Windows：能解碼即可。Windows 的 bench 只量 RTF 與 CER，不量意圖（路由執行器不能在 Windows 跑）。

### 7.3 人工／介面驗收（P3）

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

### 7.4 不在本次範圍

- Windows 上的執行器、前景讀取、本機 LLM、桌面角色（X4）。
- Windows 實機測試（X16，只在本機模擬）。
- 模型微調、自己訓練、加標點模型。
- 改 macOS 的預設引擎（X12）。
- 160 ms／fp32 等其他模型（原 Q9 (a)，需要的話另開計畫）。
- 噪音幻覺長句的過濾（§8，需要的話另外決定）。
- 把 macos-stt 的程式碼併進本 repo。

## 8. 風險與假設

| 風險 | 影響 | 對策 |
|---|---|---|
| **準確度不足（已實測）**：候選 2 的意圖 79%，錯在螢幕、B 類的 Safari、Line | Windows 上常用指令會比 macOS 常「沒反應」或聽錯 | X12：定位是備用引擎，macOS 不受影響；README 寫明。誤執行比沒反應嚴重，所以 R2 與 M1 的「沒有誤執行」照常阻擋 |
| **延遲可能超過 S1**：480 ms chunk，partial 晚 0.5～0.9 秒 | 說完到執行變慢 | P2 實測，不過就停下來問；160 ms 版是之後的選項（§7.4） |
| 純噪音出現幻覺長句（D01） | 會話中會送進 Slow Path，多一次模型呼叫，可能回一段無關的話 | P2 用 R2 驗；過濾需要另外決定 |
| 授權 | 候選 2 是 Apache-2.0，可以開源；候選 1（研究用）不使用 | X6：權重不進 repo，README 註明來源與授權 |
| 熱詞切 token 是自己寫的近似版，不是 SentencePiece 真正的切法 | 少數英文熱詞的切法和模型實際輸出不一致，沒有效果 | P0 已實測整體有幫助（18 → 19/24）；切不出來的詞會略過，不會報錯 |
| partial 節奏和 Apple 引擎不同 | `EP_PARTIAL_STABLE_S`、`EP_ASR_STALL_S` 可能誤判 | P2 看 B 類截斷與 E2E，不過就停下來問 |
| Windows 上 `completeness` 的 import 鏈會帶進整個路由 | Windows 的 client 模式可能 import 失敗 | W3；失敗就回報，不在本計畫順手重構 |
| **本機模擬蓋不到的 Windows 差異**：麥克風驅動（WASAPI／MME）、主控台編碼（cp950 印中文）、路徑分隔符號、Windows 版 sherpa-onnx 的實際速度、防毒軟體擋 8765 埠 | 模擬全部通過，Windows 實機仍然可能失敗 | 結案的 ADR 與 README 寫明「只在 macOS 上模擬」；W5 只能確認安裝檔存在，確認不了能不能跑 |
| Windows 的子程序收尾：`services.py` 用 SIGINT→SIGTERM→SIGKILL，Windows 的 `send_signal` 只支援 SIGTERM 與 CTRL 事件 | 在 Windows 上用 `main.py` 起服務時可能收不乾淨 | Windows 全流程不在範圍；W1 直接起伺服器，不經過 `services.py` |

## 9. 參考

- 模型：<https://huggingface.co/Luigi/x-asr-zh-tw-en-streaming-ft75m>（revision `90fe6b8a`）
- 訓練方法與 benchmark：<https://github.com/vieenrose/jetson-stt>
- 基底系列：<https://huggingface.co/GilgameshWind/X-ASR-zh-en>
- 候選 2（繁體＋ITN 版本）：<https://huggingface.co/Luigi/x-asr-zh-tw-native-itn>（revision `3d5bad21`）
- 160／480／960／1920 ms 的 fp32 版本（之後的選項，§7.4）：<https://huggingface.co/GilgameshWind/X-ASR-zh-en>（revision `689ff18c`）
- 執行環境：<https://github.com/k2-fsa/sherpa-onnx>
- 協定 v1：`macos-stt/docs/fastapi-server-plan.md` §4.2
- 端點基線：`docs/plans/completed/2026-11-05-endpoint-latency.md`、`logs/bench/2026-11-05_human_client_new2.jsonl`
- P0 腳本與結果：`logs/xasr_p0/`（本機，不入版控）
