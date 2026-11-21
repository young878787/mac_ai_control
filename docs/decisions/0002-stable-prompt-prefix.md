# 0002 本機 LLM 的提示詞前綴逐字穩定（prefix cache）

- 狀態：採用
- 日期：2026-11-14（commit `f70115c`）
- 來源：AGENTS 原 §4.2～4.4；[前景視窗上下文](../plans/completed/2026-11-14-front-window-context.md)

## 背景

`mlx_vlm.server` 以 `APC_ENABLED=1` 啟動，相同前綴可以重用快取。實測把動態資訊放在 system 尾巴：每次多 2 秒，冷啟動 3 秒。沒命中快取時，「本機找不到 → 轉交 LLM」會從約 1.1 秒變成約 3 秒。

## 決定

1. `SYSTEM_PROMPT` ＋ `TOOLS_SCHEMA` 是快取前綴，必須逐字穩定。
2. 動態資訊（本機搜尋結果、別名提示、目前視窗、最近對話）只放 **user 訊息**。
3. `TOOLS_SCHEMA` 不增大，工具描述保持短。LLM 看不到的工具（`browser_tab`、`open_folder`、`find_file`、`pick_choice`、`context_search`、`front_action`）維持看不到：加進去會讓 4bit 模型誤判，例如「開深色模式」被判成 `toggle_dark_mode`。
4. LLM 呼叫一律走 `llm_client._llm`（逾時、錯誤分類、熔斷）；`enable_thinking=False`、`temperature=0.4`；`warmup_llm` 帶與正式請求相同的 system prompt。
5. 本機提示詞不為雲端修改；雲端另有自己的提示詞與工具表（[0009](0009-cloud-optional.md)）。

## 後果

- 改本機提示詞或工具表時，要附 live A/B 數字（延遲與準確率）。
