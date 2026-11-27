# 0011 雲端專用工具 front_action／site_search；開網站「只放寬」

- 狀態：採用（第 1 條的「只開放兩個工具」被 [0022](0022-web-answer.md) 第 9 條取代：有搜尋金鑰時多 `web_answer`）
- 日期：2026-11-16（開網站）、2026-11-17（雲端工具）
- 來源：[端雲協同](../plans/completed/2026-11-15-cloud-hybrid.md) §14.2；commit `c7508ad`；AGENTS 原 §5.19～5.20

## 背景

雲端模型能力足夠，可以安全地對目前視窗下指令；但本機工具對 LLM 不可見（[0002](0002-stable-prompt-prefix.md)）。另外，「打開 YouTube」原本會被搜尋意圖守門拒絕。

## 決定

1. `cloud_client.CLOUD_ONLY_TOOLS` 只開放兩個工具給雲端，本機規劃一律拒絕，驗證時使用 `CLOUD_SCHEMAS`：
   - `front_action(id)`：id 限詞庫有說法的常用功能，不收 `fallback`；句子有「關掉／關閉／退出…」時不得是 `minimize`。
   - `site_search(query)`：先過搜尋意圖守門與本機優先導向，再換成 `context_search` 執行。
2. `info_question` 一樣會丟掉 `front_action`（屬於 `STATE_TOOLS`）。
3. 開網站的放行只放寬原本會拒絕的呼叫，而且條件要全部成立：沒有搜尋字詞、不是資訊提問、有開啟動詞、只明說一個詞庫網站（「YouTube Music」不算）、query 的網域屬於該網站。放行時**改開網站首頁**，不用模型給的深層網址。原本就放行的呼叫不變。
4. Fast Path `_r_site`：「動詞＋網站名稱」開首頁；同名的 App 優先於網站（「開 Google」開的是 Chrome）。

## 後來的變更

- 2026-11-23（[0022](0022-web-answer.md)）：有 `BRAVE_API_KEY` 時雲端多一個 `web_answer`，第 1 條的「只開放兩個工具」改成「兩個，加上有搜尋金鑰時的 `web_answer`」；其餘不變。

## 後果

- 新增網站只改 `vocab/lexicon.json` 的 `sites`（`home`、`aliases`、`search_url`）。
