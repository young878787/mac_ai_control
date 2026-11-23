# 0023 「查」的統一路由：一個分類點、會變的事實一律查證、沒說範圍時聯合搜尋

- 狀態：採用（取代 [0005](0005-local-first-find.md) 第 2、5 條，[0021](0021-weather-answer.md) 第 1 條的「句首的搜尋 → open」，修改 [0022](0022-web-answer.md) 第 1 條與「後果」第 3 點）
- 日期：2026-11-23
- 來源：[計畫書](../plans/completed/2026-11-23-query-routing.md) §2（U1～U13）、§4、§9；使用者 2026-11-23 的決定

## 背景

「查一下周杰倫是誰」開 Google、「周杰倫是誰」雲端回答、「找一下周杰倫是誰」去找檔案；「台積電股價」三種說法三種路線；「搜尋履歷」只上網、「找一下我的履歷」只找本機。判斷散在 `fast_path`、`domain`、`slow_path`、`pipeline` 六個地方，問句詞表有 5 套、類別詞 3 套，內容互不一致。另外雲端偶爾憑記憶回答會變的事實（2026-11-23 實測 1/20）。

## 決定

1. **單一分類點**：`query_intent.classify_query(s, catalog, raw=, stage=, answer=)` 是純函式（不碰網路、LLM、子程序、資料庫、前景），回傳 `QueryIntent(target, mode, evidence, query, category, slots, intent)`。Fast Path 的天氣、搜尋（`stage="lead"`）與找（`stage="find"`）規則都呼叫它，再由 `fast_path._route_query` 換成動作；規則順序不變（「找」仍排在其他指令規則之後）。問句、明說網路、類別、查證詞表放在 `vocab/lexicon.json` 的 `query_cues`。
2. **路由表**（第一個成立的就採用；否定、敘述仍最先擋，0007 不變）：

   | 說法 | target／mode | 動作 |
   |---|---|---|
   | 明說網路：上網、google 一下、瀏覽器、在 X 搜尋 | knowledge／show | `web_search_or_open_url`／`context_search`（照舊） |
   | 有本機證據：副檔名、檔案／電腦裡、位置、只說類型或時間、「我的…」、App／專案／資料夾 | local／show | `find_file` 等（照舊，0005 第 3 條不變） |
   | 天氣（含「搜尋台北天氣」「找台北天氣」） | knowledge／answer | `get_weather` |
   | **會變的事實**（U12）：人物「X 是誰」、新聞、比分賽果、營業時間、價格；問句、名詞片語、查／搜尋開頭都算 | knowledge／answer | `web_answer`，**不經規劃呼叫** |
   | 查／找／搜尋＋問句（U2、U3、U7）：查一下 Python 怎麼安裝、找一下 Python 是什麼 | knowledge／answer | `web_answer` |
   | 查／找／搜尋＋名詞、沒有以上證據：搜尋履歷、找周杰倫、找 python 教學 | either／show | `federated_search`（第 5 條） |
   | 其他（沒有查詢動詞的「Python 是什麼」、聊天、指令） | — | 照舊交給其他規則或 Slow Path，模型決定 |

   代名詞、指示詞開頭（你是誰、這個多少錢）與操作動詞開頭（播放新聞、打開股票）不算會變的事實。同一句另一個子句明說要看畫面（開 Safari，搜尋台北天氣）時，查詢子句照舊開網頁（`match(whole=整句)`）。
3. **會變的事實一律查證，不憑記憶回答**（U12）。規則漏掉、交給雲端的句子，雲端只回文字（沒有工具、沒有被拒絕）而原句符合 `query_intent.verify_query` 時，不採用那段文字，改執行 `web_answer`（查詢字用原話），記 `[Route 導向] 不憑記憶回答`。
4. **缺金鑰的退路**（U13，修改 0022 第 1 條）：
   - 兩把都有：`web_answer`（0022 第 2～8 條不變）。
   - 有 `AI_API_KEY`、沒有 `BRAVE_API_KEY`：開 Google 搜尋頁、回 ⚠️、`step["web_fallback"]="no_search_key"`，終端機印一行提醒，只寫變數名稱。
   - 兩把都沒有：與沒有這個功能時相同。`web_answer` 設 `ctx.escalate`，`pipeline._escalate_to_llm` 用 `match(answer=False)`（舊路線）重比一次：原本開網頁或找檔案的照舊，原本交給 Slow Path 的交給本機 AI。
5. **聯合搜尋** `local_actions.federated_search`（U5、U6、U9、U11，取代 0005 第 2 條「搜尋／查沒帶範圍詞就上網」）：
   - 前景瀏覽器停在詞庫網站，或同一句剛開了瀏覽器 → 照舊 `context_search`（站內搜尋）。
   - 否則本機 `mdfind`（同 `find_file` 的範圍與排序）與網路並行（`ThreadPoolExecutor`）。網路有 Brave 金鑰就搜尋，沒有就一筆「在 Google 搜尋『X』」，不連網。
   - 清單本機最多 `FEDERATED_LOCAL_MAX`＝3 筆、網路最多 `FEDERATED_WEB_MAX`＝2 筆，放在 `choices.STORE`（網路項目是 `WebItem(title, url)`，UI 只拿到標題與網域）。
   - 只剩 1 筆就直接執行；**本機 1 筆、網路只剩 Google 搜尋時也直接在 Finder 顯示**（同以前的找檔案，使用者 2026-11-23 決定）。只剩 Google 那一筆時走 `web_search_or_open_url`，log 與以前逐字相同。
   - 選網路項目用前景瀏覽器或預設瀏覽器開網址；本機項目照 0005 第 3 條只顯示。
6. **常搜名詞統計** `search_prefs`（U5、U6、U8）：表 `search_terms(ts, term, side, signal, path)`（migration 0003，`data/music.sqlite3`）。
   - 只記兩種：從聯合清單選了哪一邊（picked；本機連同路徑），以及 Fast Path 執行成功、明說範圍的找／搜尋（explicit）。
   - 不記：清單本身、統計自動決定的、0 筆、轉交、`web_answer`。理由同天氣 W13：預設補上的會越記越偏。
   - 網路一律不記網址（Brave 條款，0022 第 8 條）。
   - **U8**：同一個詞 90 天內同一邊 ≥ 2 次、另一邊 0 次才直接執行。偏本機而且記錄的路徑還在 → 直接顯示、不跑 mdfind；偏網路 → 直接開 Google 搜尋頁。沒達門檻的偏好只決定清單先列哪一邊。
   - 只在執行階段由 `command_worker` 讀寫；資料庫還沒準備好時當成沒有歷史。
7. **轉交走雲端**（U4，取代 0005 第 5 條的「轉交不碰雲端」）：本機 0 筆而且沒有本機證據的轉交，現在只剩「找我的 X」這類句子。
   - 有雲端金鑰：走雲端規劃（`plan_cloud(local_miss=…)`），附本機搜尋結果、網頁搜尋用原話、不導回本機；有 Brave 金鑰時工具含 `web_answer`。
   - 模型沒有選任何動作：回保底的「找不到」。
   - 沒有雲端金鑰：照舊本機 LLM。前景是瀏覽器時照舊直接上網找、不問模型。
8. **事後驗證只統一了一部分**（使用者 2026-11-23 接受）：計畫 §4.5 原本要把 `validate_calls` 的 4 種改寫換成「用 `classify_query` 比對 target／mode」，實際保留原本的 4 種，加上第 3 條的安全網。這 4 種是 `answer_mode` 兩個方向、`_local_reroute`、`site_open_home`。
   - `answer_mode` 已會判斷會變的事實，所以「模型開網頁、原句問會變的事實 → `web_answer`」已經成立。
   - 整套改寫會改變既有改寫的邊界，與「行為等價」的驗收衝突。

## 後果

- **行為變更**：
  - 「查一下周杰倫是誰」「搜尋周杰倫是誰」「台積電股價」「查台積電股價」「故宮博物院開放時間」「搜尋台北天氣」從開 Google 或交給模型，改成直接回答。要看搜尋頁就說「上網查」「google 一下」「打開瀏覽器查」。
  - 「搜尋履歷」「找周杰倫」「查貓咪專頁」（前景不是詞庫網站）從只上網或只找本機，改成列出兩邊的清單。
- **延遲**：
  - `match()` 在 669 句語料上的 p95 0.091～0.093 ms，P0 基線 0.089 ms，符合 U10（≤ 110%）。
  - 會變的事實省掉一次規劃呼叫。
  - **沒說範圍的「搜尋 X」開 Google 前多一次 mdfind**：這台 Mac 5 個詞實測 100～377 ms（使用者 2026-11-23 接受）。U8 偏網路成立後就不再跑 mdfind。
- **實作與計畫不同的地方**（計畫 §9.5、§9.6）：
  - 「我的…」與副檔名算本機證據。
  - 統計偏網路時開 Google 搜尋頁，不另外呼叫 Brave。
  - 「單筆直接顯示後沒有改選」不記，因為要等下一句才知道。
  - 聯合搜尋不還原誤聽詞。
  - A5 並行測試用 0.4 秒延遲取代 2 秒。
  - 前景瀏覽器停在非詞庫網站時，「查貓咪」改成聯合搜尋。只剩 Google 那一筆時，仍開在前景瀏覽器。
- **已知、不在本次範圍**：
  - 「勇士隊昨天贏了嗎」被敘述守門（昨天）擋下。
  - 「查不到」「查不到這個人的資料」本來就誤判成搜尋，現在變成聯合搜尋，沒有變好也沒有變差。
- **沒有驗證**：
  - 聯合搜尋含真實 Brave 的延遲（L2，U10 p50 ≤ 1.5 s、p95 ≤ 3.5 s）。
  - `bench/web_answer.py` 加新題的回歸（L4）。
  - 桌面清單畫面與語音選擇（H1、H2）。
