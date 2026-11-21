# 0001 能用規則就不問 LLM（Fast Path 優先）

- 狀態：採用
- 日期：2026-11-03
- 來源：[多段意圖與 Fast Path 強化](../plans/completed/2026-11-04-multi-intent-fast-path.md) §零 D2、D5；AGENTS 原 §4.1、§4.5

## 背景

本機 LLM 是 4bit 小模型：規劃一次約 1～3 秒，而且會出錯。常見指令（開關 App、音量、亮度、深色模式、分頁、找檔案）佔大多數，而且句型固定。

## 決定

1. 常見指令一律走 Fast Path：正規化 → 切子句 → `FastPathMatcher.match` 的 regex 規則 `_r_*`，命中就直接執行，不碰網路、不碰 LLM。
2. 新增常見指令時先加 Fast Path 規則，不是加 LLM 工具。
3. Fast Path 是 LLM 前面的過濾層。LLM 不可用時，Slow 步驟走 `_recover_slow_segment`：有候選才執行，沒有候選就告知；後面的步驟照常執行；熔斷期間不重試。
4. 誤聽詞可以預先推測（`seed`），但每個條目都要標來源（`observed`／`seed`），並記錄「STT 原文 → 正規化結果」，用來回收真實的誤聽。

## 後果

- Fast Path 的延遲是數 ms～約 100 ms，LLM 掛掉也能用。
- Fast Path 規則也是端點偵測判斷「句子完整」的依據（[0003](0003-client-endpoint-detection.md)），改規則會連動收句時機。
- 錯字矯正必須在正規化＋Fast Path 內完成；錯字語料測試只要 LLM 被呼叫就算失敗。
