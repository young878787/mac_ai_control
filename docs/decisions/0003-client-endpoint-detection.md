# 0003 端點偵測在本端做（Silero VAD＋句子完整度）

- 狀態：採用
- 日期：2026-11-05
- 來源：[語音端點偵測與延遲改善](../plans/completed/2026-11-05-endpoint-latency.md) §3、§5

## 背景

macos-stt 原本用能量 VAD，要靜音 1.5 秒才斷句，說完到開始處理的等待太長。

## 決定

1. `ENDPOINT_MODE="client"`：本端逐窗跑 Silero VAD（`AI_models/silero_vad.onnx`，新增依賴 `onnxruntime`），再用 partial 文字經 `completeness.classify` 判斷句子是否完整，決定後送 `flush` 要求 macos-stt 收尾。
2. 靜音門檻依完整度分級：完整（每個子句都被 Fast Path 命中）0.35 s，未確認 0.6 s，預設 1.3 s，未完成（句尾接續詞、光動詞）2.0 s。另有 ASR 停滯、最長 10 秒等保底。server 端斷句只當備援。
3. 驗收數值：S1 ≤ 0.8 s／p90 ≤ 1.2 s、Q1 ≥ 95%、Q3 合成 0／真人 ≤ 1。真人語料由使用者錄製：`bench/human/manifest.csv` 入版控，音檔不入版控。
4. `"server"` 保留為回退：缺 VAD 模型時使用，行為與舊版相同。

## 後果

- `completeness.classify` 與 `catalog` 在收音熱路徑上，不得做子程序、`mdfind` 或網路 IO，也不得讀前景視窗。
- 新增 Fast Path 規則要跑 `test_completeness.py`、`test_endpoint.py`；調 `EP_*` 要用 `scripts/stt_bench.py` A/B 比對。
