# 「查」的統一路由：一個分類點、聯合搜尋、常搜名詞統計

狀態：**已定案，待開始實作（2026-11-23）**。§2 是使用者已決定的事。完成後依 ADR 0016 移到 `completed/`，並新增 ADR 取代 [0005](../../decisions/0005-local-first-find.md) 第 2、5 條與 [0021](../../decisions/0021-weather-answer.md) 第 1 條的「句首的搜尋 → open」，並修改 [0022](../../decisions/0022-web-answer.md) 第 1 條（沒有 Brave 金鑰時的行為，U13）。

## 0. 結論

1. **目標**：「查知識」「查檔案」「看畫面」用**同一個純函式**判斷，判斷要快、規則要少、每條路都有確定的結尾（閉環），並把散在 6 個地方的判斷收成一處（解耦）。
2. **兩個獨立的維度**：
   - **找什麼（target）**：`knowledge`（世界知識）、`local`（本機檔案、資料夾、App、專案）、`screen`（畫面）、`either`（說不出是哪個），或 `none`（不是查詢，交給原本的路由）。
   - **怎麼給（mode）**：`show`（開網頁或 Finder 給你看）、`answer`（在泡泡裡直接回答）。
3. **新增聯合搜尋**（現在沒有）：`either` 的句子（「搜尋履歷」「找周杰倫」）同時查本機與網路，合併成一份清單，說「第 N 個」或點選。
4. **常搜名詞統計**：記下每個名詞你最後選了本機還是網路，下次同一個詞自動排前面；歷史夠明確時直接執行、不列清單。只記明說與實際選擇，不記預設（比照天氣 W13，避免自己越記越偏）。
5. **模型只在規則判斷不了時補位**；模型選完工具後只用同一個分類函式驗證，取代現有 4 種事後改寫。轉交（本機 0 筆）改走一般雲端規劃，可以用 `web_answer`。

## 1. 現況（2026-11-23，`match()` 實跑，只判斷路由不執行）

| 說法 | 現在 | 問題 |
|---|---|---|
| 周杰倫是誰 | Slow Path → 雲端 → `web_answer` 回答 | — |
| 查一下周杰倫是誰 | Fast Path `context_search` → 開 Google | 同義句結果不同 |
| 找一下周杰倫是誰 | `find_file` 找名叫「周杰倫是誰」的檔案 → 0 筆 → 轉交**本機** LLM | 白搜本機；轉交碰不到雲端與 `web_answer` |
| 找周杰倫／找 python 教學 | 先搜本機 → 0 筆 → 轉交本機 LLM | 多一次本機搜尋與一次 LLM |
| 搜尋履歷 | `context_search` → 開 Google | 不找本機；「找一下我的履歷」卻找本機 |
| 查履歷檔案／在電腦裡搜尋履歷 | `find_file`（只找本機） | — |
| 台積電股價多少／查台積電股價／台積電股價 | 雲端回答／開 Google／交給雲端自己決定 | 三種說法三種路線 |

判斷散在：[fast_path.py](../../../mac_ai/router/fast_path.py) 的 `match()` 規則順序、`_r_search`、`_r_weather`、`answer_mode`；[domain.py](../../../mac_ai/router/domain.py) 的 `resolve_intent`；[slow_path.py](../../../mac_ai/router/slow_path.py) 的 `intent_guard`、`validate_calls`（`answer_mode` 導向、`_local_reroute`、轉交時改用原話、網站首頁放行）；[pipeline.py](../../../mac_ai/router/pipeline.py) 的 `_escalate_to_llm`。
問句詞表有 5 套（`_QUESTION_RE`、`_QUESTION_END_RE`、`_ASK_RE`、`_INFO_QUESTION_RE`、`_NOT_A_REQUEST_RE`），類別詞 3 套（`_REALTIME_RE`、`_CATEGORY_RE`、`_SEARCH_INTENT_RE`），內容互不一致（2026-11-23「誰」只在其中一套，造成「勇士隊最近一場比賽誰贏了」什麼都沒做）。

## 2. 決策（使用者 2026-11-23）

| # | 決策 | 依據 |
|---|---|---|
| U1 | 目標：統一、簡潔、快速的判斷，每條路閉環，解耦現有路線；**新增聯合搜尋** | 使用者 |
| U2 | 「查一下／找一下 X＋問句」（例：查一下周杰倫是誰）→ **直接回答**（兩把金鑰都有時走 `web_answer`；缺金鑰時見 U13） | 使用者同意 |
| U3 | 「找＋沒有本機證據＋問句」→ **跳過檔案搜尋**，當知識問題 | 使用者同意 |
| U4 | 轉交（本機 0 筆、沒有本機證據）**允許走雲端與 `web_answer`**（取代 0005 第 5 條「轉交不碰雲端」） | 使用者同意 |
| U5 | 「搜尋履歷」這類說不出範圍的句子：用**常搜名詞統計**決定本機或網路，越用越準 | 使用者 |
| U6 | 「找周杰倫」（只有名詞、不是問句、沒有本機證據）→ 聯合搜尋，由同一份統計決定：查這個詞過去最常選的一邊，本機那邊**記下選過的路徑**，下次直接用 | 使用者 2026-11-23（原 Q1） |
| U7 | 「搜尋周杰倫是誰」（句首搜尋＋問句）**一併整合成直接回答**，與「查一下周杰倫是誰」相同；要看搜尋頁就說「上網搜尋／google／在瀏覽器查」（取代 0021 第 1 條的「句首的搜尋 → open」） | 使用者 2026-11-23（原 Q2） |
| U8 | 統計夠明確才直接執行：同一個詞 90 天內同一邊 ≥ 2 次、另一邊 0 次 | 使用者同意建議（原 Q3） |
| U9 | 聯合清單：本機最多 3 筆＋網路最多 2 筆，總數 ≤ `FIND_RESULTS_MAX`（5）；沒有 Brave 金鑰時網路只列一筆「在 Google 搜尋『X』」 | 使用者同意建議（原 Q4） |
| U10 | 延遲門檻：`match()` 在語料上的 p95 ≤ P0 基線的 110%；聯合搜尋 p50 ≤ 1.5 s、p95 ≤ 3.5 s | 使用者同意建議（原 Q5） |
| U12 | **會變的事實不憑記憶回答**：人物（X 是誰）、時事新聞、比分賽果、營業時間、價格（股價、匯率、多少錢）、天氣。由規則判斷後直接查證：天氣走 `get_weather`，其餘走 `web_answer`，**不經模型選擇**，也省掉一次規劃呼叫。名詞片語（「台積電股價」）、「查／搜尋」開頭（「查台積電股價」「搜尋最近的新聞」）一樣適用；要看搜尋頁就明說「上網／google／瀏覽器」。穩定知識（「Python 是什麼」「為什麼天空是藍色的」）仍由模型決定。規則漏掉、交給模型的句子，模型若只用文字回答而原句屬於這些類別，事後改成 `web_answer`（§4.5） | 使用者 2026-11-23（2026-11-23 實測 R4 有 1/20「周杰倫是誰」模型憑記憶回答） |
| U13 | 有雲端金鑰、**沒有 Brave 金鑰**時，U12 的句子（天氣除外，天氣不需要 Brave）改開 Google 搜尋頁，回覆 ⚠️ 並在後端 log 記「沒有搜尋金鑰，已退回搜尋頁」（`step["web_fallback"]="no_search_key"`，終端機也印一行；只寫變數名稱，不寫金鑰）。取代 0022 第 1 條「沒有 Brave 金鑰時行為逐字相同」。**連雲端金鑰都沒有時維持現況**（交給本機 LLM，北極星「沒有雲端時與沒有雲端逐字相同」） | 使用者 2026-11-23 |
| U11 | 選中網路項目：用瀏覽器（Safari）開，網址直接填入；有 Brave 是那一筆的網址，沒有 Brave 是填好查詢字的 Google 搜尋網址 | 使用者 2026-11-23（原 Q6） |

不變的紅線：否定、敘述句最先擋（0007）；Fast Path 與分類函式不碰網路、LLM、子程序、資料庫（0001、0003，統計在執行階段才讀）；開網頁或回答由原句決定，不由模型決定（0021、0022）；`validate_tool_call` 仍是唯一信任邊界（0011）；檔案預設只在 Finder 顯示（0005 第 3 條）；Brave 搜尋結果不寫 log、資料庫、對話記憶（0022）。

## 3. 待確認

無（2026-11-23 全部決定，見 §2 U6～U11）。實作中若發現與決策衝突，停下來問，不自行調整。

**實作採用的假設**（與你的意思不同請指正）：

- U6「記錄路徑」只記**本機**選中的檔案路徑。網路那邊只記「選了網路」，不記網址，因為 Brave 條款不允許保存搜尋結果（0022 第 8 條）。（使用者 2026-11-23 確認）
- U11 用 `open <網址>`，也就是交給預設瀏覽器（這台是 Safari）；前景已經是瀏覽器時，沿用 `context_search` 開在前景那個瀏覽器。（使用者 2026-11-23 確認）

## 4. 設計

### 4.1 分類：`classify_query(utterance) -> QueryIntent`（純函式）

新模組 `mac_ai/router/query_intent.py`。不碰網路、LLM、子程序、資料庫、前景；catalog 只做唯讀查詢（與 `resolve_intent` 相同）。

```
QueryIntent
  target:   knowledge | local | screen | either | none
  mode:     show | answer
  query:    原話抽出的查詢字（沿用 domain._clean_query；送 LLM 的仍是原句）
  slots:    local：name、kinds、since、location、filename（沿用 file_terms）；knowledge：category（weather…）
  evidence: 判斷依據，依序記錄（log 用）：guard／screen／explicit_web／local_word／ext／location／catalog／category／question／verb
  local_only: 有本機證據（沿用 0005 的定義）
```

**判斷順序（一張表，第一個成立的就採用）**：

| 順序 | 條件 | target／mode |
|---|---|---|
| 1 | 否定、敘述（`_blocked`，不改） | none（照舊只回話） |
| 2 | 看畫面的說法（`_read_screen_re`） | screen／answer |
| 3 | 明說網路：上網、網路上、google、瀏覽器、網站、網頁、官網、在 X 搜尋 | knowledge／show |
| 4 | 本機證據：副檔名、檔案／資料夾／電腦裡、位置、只說類型或時間、catalog 命中 App／專案／資料夾 | local／show |
| 5 | 天氣（現有天氣窄規則照舊） | knowledge／answer，category＝weather |
| 6 | **會變的事實**（U12）：人物「X 是誰」（X 不是你／我／他這類代名詞）、時事新聞、比分賽果（誰贏、比數）、營業時間（幾點開門、營業到幾點）、價格（股價、匯率、多少錢）；問句、名詞片語、查／搜尋開頭都算 | knowledge／answer，category＝verify |
| 7 | 查／找／搜尋＋問句（**單一問句詞表**）或要求告知（告訴我…）（U2、U3、U7） | knowledge／answer |
| 8 | 找／查／搜尋＋名詞，沒有以上證據（找周杰倫、搜尋履歷、找 python 教學） | **either**／show（聯合搜尋，U5、U6） |
| 9 | 其他（沒有查詢動詞的一般問句「Python 是什麼」、聊天、操作指令） | none（照舊交給其他規則或 Slow Path，模型決定聊天或 `web_answer`） |

**詞表整併**：問句、明說網路、類別、範圍四組詞各只留一份，搬到 `vocab/lexicon.json` 的 `query_cues`（範圍詞已在 `scope_words`）。P1 先「同名不同集合」搬家、行為不變，P3 才合併成一份，合併造成的行為差異逐句列給使用者確認（§6）。

### 4.2 路由表與閉環

每個 `(target, mode)` 對應一串來源，前一個失敗或 0 筆才換下一個；**最後一個一定是會給出明確結果的來源**（✅、⚠️ 加替代動作，或 ❌ 說明原因），不會停在「交給 AI 判斷」卻沒有下文。

| target／mode | 來源 → 備援 |
|---|---|
| knowledge／answer | 天氣 → `get_weather`（0021 的閉環）；其他 → `web_answer`（兩把金鑰都有，0022 的閉環）→ 有雲端金鑰、沒有 Brave 金鑰：開 Google 搜尋頁＋⚠️＋後端 log（U13）→ 連雲端金鑰都沒有：交給本機 LLM（現況） |
| knowledge／show | `context_search`（前景是詞庫網站就站內搜尋，否則 Google） |
| local／show | `find_file`／`open_folder`／開 App／開專案（0005 第 3 條不變）→ 0 筆且 `local_only` → 「本機沒有」 |
| screen／answer | `read_screen` |
| either／show | **聯合搜尋**（§4.3）→ 兩邊都 0 筆 → 開 Google 搜尋頁（⚠️ 說明本機沒有） |
| none | 照現有規則（音量、亮度、點歌…）或 Slow Path；雲端規劃有 `web_answer`（U4） |

轉交（U4）：`find_file` 0 筆且沒有本機證據的情況，在新分類下只剩模型自己選了 `find_file` 的少數句子；這時改走一般的 `execute_slow_path_llm`（雲端優先，有 `web_answer`），不再固定用本機 LLM。`local_miss` 的「查詢字用原話」規則保留。

### 4.3 聯合搜尋（`federated_search(query, ctx)`，執行階段）

1. 讀統計（§4.4），算出這個詞偏本機、偏網路，或沒有歷史；偏本機時一併取出最常選的路徑。
2. **U8 成立（歷史夠明確）**：
   - 偏本機，而且記錄的路徑還存在 → 直接在 Finder 顯示那個路徑，不跑 mdfind；路徑不存在 → 照常查本機。
   - 偏網路 → 只查網路。
   - 查的那一邊有結果就直接照該邊的行為（本機 1 筆 → Finder 顯示；網路 → 開第一筆網址或 Google 搜尋頁），0 筆才查另一邊。
3. **否則並行查兩邊**（`ThreadPoolExecutor`，各自逾時；`command_worker` 在這一步最多等較慢的一邊）：
   - 本機：`mdfind`（同 `find_file`，`FILE_SEARCH_TIMEOUT_S`）
   - 網路：有 Brave 金鑰 → `search_client.search`（`WEB_SEARCH_TIMEOUT_S`）；沒有 → 不連網，只產生一筆「在 Google 搜尋『X』」
4. **合併**成一份 `choices` 清單：偏本機的詞本機在前，偏網路的網路在前，沒有歷史時本機在前（0005 本機優先）；筆數依 U9。
5. 只有一邊有結果而且只有 1 筆 → 直接執行，不列清單。
6. 說「第 N 個」或點選 → `pick_choice`：本機項目照 `_act`（顯示或開啟）；網路項目用瀏覽器開網址（U11）。選完記一筆統計（signal＝picked；本機項目連同路徑）。

`choices.ChoiceList` 的項目從「路徑」擴成 `{kind: local|web, path 或 url, title}`；`payload()` 給 UI 的網路項目只有標題與網域，不含摘錄。Brave 的標題與網址只在記憶體的清單裡（60 秒 TTL），不寫 `commands.jsonl`、資料庫或對話記憶（0022 第 8 條）；統計只記使用者說的詞與選了哪一邊，不記搜尋結果。

### 4.4 常搜名詞統計（U5）

- 新 migration `0003_search_terms`：`search_terms(id, ts, term, side, signal, path)`，只追加；`term`＝`_compact` 後的查詢字（小寫、去空白），`side`＝local／web，`signal`＝explicit（句子明說範圍，例如「找履歷檔案」「上網查履歷」）或 picked（從聯合清單選了哪一邊）。
- `path`：本機項目被選中（含語音「第 N 個」、點選、單筆直接顯示後沒有改選）時記下完整路徑；網路一律為空（不存網址，Brave 條款）。同一個詞、同一邊最常出現的路徑就是 U6 的「直接用」對象。
- 這是**搜尋領域的一份紀錄**：明說本機（`find_file`）、明說網路（`context_search`、`web_answer`）、聯合搜尋的選擇都寫進這張表，用同一個 `lean()` 比較同一個詞兩邊的次數（一條 SQL）。現有的音樂表（0001）與天氣表 `weather_queries`（0002）**不動、不寫入**；天氣句子照舊只記在 `weather_queries`。
- **不記**：用統計或預設自動決定的結果、0 筆、否定與敘述句（比照 W13：預設補上的不能記，否則越記越偏）。
- 新模組 `search_prefs.py`（比照 `weather_prefs`）：`record(conn, term, side, signal, now, path=None)`、`lean(conn, term, now) -> (local | web | None, 最常選的路徑或 None)`（U8 的門檻）。只在執行階段由 `command_worker` 呼叫；資料庫還沒準備好時當成沒有歷史，照常搜尋。
- 統計讀寫各一條參數化 SQL，`term` 有索引，預期 < 5 ms（P4 實測）。

### 4.5 模型之後只驗證、不另外改寫

`validate_calls` 的 `answer_mode` 導向、`_local_reroute`、`site_open_home` 改成：模型給的工具先過 `validate_tool_call`（不變），再用 `classify_query(原句)` 比對；target 或 mode 不一致時以原句為準換成路由表的工具，記一筆 `[Route 導向]`；`classify_query` 是 none 時尊重模型。`_search_intent_error`（沒有搜尋意圖不開網頁）保留在信任邊界這一層。

**U12 的安全網**：雲端只回文字、沒有呼叫工具，而原句（規則沒接到的說法）含 U12 的類別詞時，不採用那段文字，改執行 `web_answer`（查詢字用原話），記一筆 `[Route 導向] 不憑記憶回答`。沒有 Brave 金鑰時照 U13。

### 4.6 可觀測

`step["route"]` 記 `target`、`mode`、`evidence`、試過的來源與各自毫秒數、統計 lean（不記 Brave 的標題、網址、摘錄）；寫進 `commands.jsonl`，用來驗收與日後調整詞表。

## 5. 影響範圍

- 新增：`mac_ai/router/query_intent.py`、`mac_ai/router/search_prefs.py`、`mac_ai/db/migrations/versions/0003_search_terms.py`
- 修改：`fast_path.py`（搜尋、找、天氣規則改用 `classify_query`；問句詞表）、`domain.py`（併入或被呼叫）、`slow_path.py`（`intent_guard`、`validate_calls`、轉交、U12 安全網）、`pipeline.py`（轉交）、`local_actions.py`（`federated_search`、`pick_choice` 支援網路項目、`web_answer` 沒有 Brave 金鑰時開搜尋頁＋log（U13））、`choices.py`（項目格式）、`actions.py`（`LOCAL_TOOLS` 登錄）、`config.py`、`vocab/lexicon.json`（`query_cues`）、`db/__init__.py`（`HEAD_VERSION` 3）
- 桌面：`desktop/renderer/role/widgets.js` 等顯示清單的地方要能顯示網路項目（標題＋網域）；依賴方向不變（`layers.test.js`）
- 連動：Fast Path 規則改動會影響端點偵測（紅線 3），`test_completeness.py`、`test_endpoint.py` 必跑
- 文件：新 ADR（取代 0005 第 2、5 條與 0021 第 1 條「句首的搜尋 → open」，修改 0022 第 1 條）、`ARCHITECTURE.md`、`docs/flows/main-flow.md`、`docs/flows/slow-path.md`、`docs/guides/testing.md`、`README.md`、`AGENTS.md`（紅線若有變）

## 6. 實作步驟

每一步都可以單獨回退；P0～P2 行為零變更。

| 步驟 | 內容 | 完成條件 |
|---|---|---|
| P0 | **行為快照與基線**：把現有測試裡的句子＋§1 的句子做成語料（`tests/fixtures/routing/corpus.json`），記下每句目前的 Fast Path 結果（工具＋參數）或「交給 Slow Path」＋`intent_guard`；量 `match()` 在語料上的 p50／p95 | 快照測試全綠；基線數字寫進 §9。**2026-11-23 完成（§9.1）** |
| P1 | **詞表搬家**：問句、明說網路、類別詞搬到 `query_cues`，各呼叫點用與現在**相同**的集合 | 快照 0 差異；`pytest` 0 failed。**2026-11-23 完成（§9.2）** |
| P2 | **加入 `classify_query`**，Fast Path 的搜尋、找、天氣規則改用它，規則的實際結果不變 | 快照 0 差異；`test_completeness.py`、`test_endpoint.py` 通過；`match()` 延遲符合 U10 |
| P3 | **改行為**：詞表合併成一份；U2、U3、U7；`either` 判斷（暫時仍走現有路線） | 快照差異**只出現在核准清單裡**：逐句列「舊結果 → 新結果」給使用者確認後才合併 |
| P4 | **聯合搜尋與統計**：migration 0003、`search_prefs`、`federated_search`、清單網路項目、UI | §7.1 A5～A10 通過 |
| P5 | **轉交與事後驗證**：U4、§4.5 | §7.1 A11、A12 通過；雲端與本機的 flow 測試全綠 |
| P6 | **實測與文件**：§7.2、§7.3；ADR、flows、ARCHITECTURE、README 同步；計畫結案 | §7 全部有結果；`pytest` 0 failed；`node --test`、`npm run typecheck` 通過 |

## 7. 驗收計畫

### 7.1 自動化測試（離線）

| # | 驗收項目 | 執行 | 通過標準 | 失敗處置 |
|---|---|---|---|---|
| A1 | 行為快照 | `pytest tests/unit/test_routing_snapshot.py` | P1、P2：0 差異；P3 起：差異的句子集合＝使用者核准的清單（多一句、少一句都算失敗） | 找出造成差異的規則，修到 0 差異或補進清單請使用者確認 |
| A2 | 分類函式語料 | `pytest tests/unit/test_query_intent.py` | §4.1 每一列至少 5 句正例、3 句不得命中的反例，全部符合表格；否定、敘述句一律 none | 修規則或詞表；不得刪語料 |
| A3 | 分類函式純粹 | 同上（測試中把 `subprocess.run`、`urllib.request.urlopen`、`sqlite3.connect` 換成會丟例外的假物件，再跑全部語料） | 沒有任何例外 | 把 IO 移到執行階段 |
| A4 | 端點偵測連動 | `pytest tests/unit/test_completeness.py tests/unit/test_endpoint.py` | 0 failed；期待值有改的句子都在 A1 的核准清單裡 | 同 A1 |
| A5 | 聯合搜尋：並行與合併 | `pytest tests/flow/test_route_federated.py`（假 mdfind、假 Brave，各自可設延遲） | 兩邊各 2 秒延遲時整步 < 2.5 秒（證明並行）；清單順序依統計 lean；筆數依 U9；只有一邊 1 筆時直接執行；偏本機且記錄的路徑存在時不呼叫 mdfind、直接顯示該路徑，路徑已刪除時照常搜尋 | 修並行或合併 |
| A6 | 聯合搜尋：閉環 | 同上（本機逾時、Brave 逾時／429／壞 JSON、兩邊都 0 筆、資料庫未準備） | 每種組合都回 ✅、⚠️ 加替代動作或 ❌ 加原因之一，沒有空回覆、沒有「交給 AI」後無下文 | 補該組合的備援 |
| A7 | 聯合搜尋：沒有金鑰 | 同上（沒有 `BRAVE_API_KEY`） | 不連網（假 Brave 0 次呼叫）；網路只有一筆「在 Google 搜尋『X』」 | 修 |
| A8 | 選網路項目 | 同上：「搜尋履歷」→「第 N 個」（網路項目） | 送出的指令是 `open <網址>`（前景是瀏覽器時是 `open -a <前景瀏覽器> <網址>`），網址是該項目的網址（有 Brave）或填好查詢字的 Google 搜尋網址（沒有）；本機項目仍照 0005 第 3 條只顯示 | 修 `pick_choice` |
| A9 | 統計 | `pytest tests/unit/test_search_prefs.py` | 只有 explicit 與 picked 會寫入；統計或預設決定的不寫；U8 門檻的邊界（1 次、2 次、另一邊 1 次、超過 90 天）全部符合；本機紀錄有 `path`、網路紀錄 `path` 為空；`lean()` 回傳最常選的路徑 | 修記錄或門檻 |
| A10 | 資料安全 | `pytest tests/flow/test_route_federated.py -k leak` | Brave 的標題、網址、摘錄不出現在 `commands.jsonl` 紀錄、資料庫任何表、對話記憶；金鑰不出現在任何輸出 | 修紀錄欄位 |
| A11 | 轉交走雲端（U4） | `pytest tests/flow/test_route_find.py tests/flow/test_route_web_answer.py` | 模型選 `find_file` 且 0 筆、沒有本機證據時，第二次規劃是雲端且工具含 `web_answer`；沒有雲端金鑰時與現況相同 | 修轉交 |
| A12 | 事後驗證（§4.5） | `pytest tests/flow/test_route_slow.py tests/flow/test_route_cloud.py tests/flow/test_route_web_answer.py` | 原本 4 種改寫的既有測試全部通過（行為等價）；新增「模型選錯 target」各 3 句改回路由表的工具 | 修驗證 |
| A13 | 遷移 | `pytest tests/unit/test_music_db.py` | 版本 2 → 3 先備份、既有表資料不變；全新資料庫直接建到 3 | 修 migration |
| A15 | 會變的事實直接查證（U12） | `pytest tests/flow/test_route_web_answer.py tests/flow/test_route_weather.py` | 人物、時事、比分、營業時間、價格各 ≥ 5 句（問句、名詞片語、查／搜尋開頭各有）：兩把金鑰都有時第一個工具是 `web_answer`，**規劃呼叫 0 次**；天氣是 `get_weather`；反例（「你是誰」「Python 是什麼」「打開台積電股價網站」「上網查台積電股價」）不走 `web_answer`；雲端只回文字的類別句被改成 `web_answer` | 修規則；不得刪語料 |
| A16 | 沒有 Brave 金鑰（U13） | 同上（有 `AI_API_KEY`、沒有 `BRAVE_API_KEY`；兩把都沒有） | 只有雲端金鑰：U12 的句子開 Google 搜尋頁、回 ⚠️、`step["web_fallback"]=="no_search_key"`、log 只出現變數名稱不出現金鑰；兩把都沒有：與 P0 快照及現況逐字相同 | 修備援 |
| A14 | 全部 | `pytest`；`cd desktop && node --test && npm run typecheck` | 0 failed | 修 |

### 7.2 實際環境（會連網、會讀本機檔案）

| # | 項目 | 做法 | 通過標準 | 失敗處置 |
|---|---|---|---|---|
| L1 | 分類延遲 | P0 與 P2、P3 各跑一次語料計時（`match()`，每句 100 次取中位數） | 符合 U10（p95 ≤ 基線 110%） | 找出變慢的規則（通常是正則回溯）並修 |
| L2 | 聯合搜尋延遲 | 新 bench `bench/federated.py`：10 個名詞（5 個本機確定有、5 個本機沒有），真實 `mdfind`＋真實 Brave | 符合 U10（p50 ≤ 1.5 s、p95 ≤ 3.5 s） | 縮短逾時或改成本機先回、網路後補（另外問你） |
| L3 | 越用越方便 | 暫存資料庫：說「搜尋履歷」，從清單選本機的履歷 2 次，再說一次「搜尋履歷」 | 第 3 次不列清單、不跑 mdfind，直接在 Finder 顯示前兩次選的那個路徑；資料庫只多 2 筆 picked（含路徑）；「上網查履歷」說過 1 次後，第 3 次改回列清單（另一邊不是 0 次） | 修統計 |
| L4 | U2、U7、U12 回歸 | `python bench/web_answer.py`，題目加「查一下周杰倫是誰」「搜尋周杰倫是誰」「找一下勇士隊最近一場比賽誰贏了」「台積電股價」「查台積電股價」5 題 | 新題 5/5 走 `web_answer`；原 20 題 R4（走 `web_answer`）**20/20**，沒有任何一題憑記憶回答；R3 不低於 2026-11-23 的 18/20；R2 p50 不高於 2026-11-23 的 3.8 s（U12 省掉規劃呼叫，預期變快） | 修分類或停下來問你 |

### 7.3 人工與介面

| # | 項目 | 做法 | 通過標準 |
|---|---|---|---|
| H1 | 清單顯示 | 桌面角色說「搜尋履歷」 | 本機項目與網路項目都看得到，網路項目只有標題與網域；由你判定是否清楚 |
| H2 | 語音選擇 | 實機說「搜尋履歷」再說「第四個」 | 開的是清單上第四項；STT 實機沒有自動測試，結果只記你的判定 |

### 7.4 不在本次範圍

- 本機檔案「回答」模式（例如「我昨天下載了什麼」在泡泡裡列出），維持顯示在 Finder。
- 檔案內容的語意搜尋（向量索引）、抓網頁全文。
- 人名、地名辨識（NER）；U6 改用聯合搜尋加統計處理。
- Windows 上的本機搜尋（`mdfind` 只在 macOS）。

## 8. 風險與依據

- **搬結構時改到行為**：P0 快照擋住；P1、P2 必須 0 差異才進下一步。
- **詞表合併讓更多句子變成問句**：例如加了「誰」之後，原本走 Fast Path 的句子可能改成交給模型；A1 的差異清單逐句確認。
- **聯合搜尋多花時間**：並行後約等於較慢的一邊（各 3 秒逾時）；L2 實測。統計夠明確時只查一邊。
- **統計自我強化**：只記明說與實際選擇（§4.4）。
- **記錄的路徑過時或選錯**：直接顯示前先確認路徑存在，不存在就照常搜尋；選錯的那一次另一邊不是 0 次，所以 U8 不會成立，下次會再列清單。路徑屬於個人資料，只存在本機 `data/`（不入版控），不寫進 log。
- **U7、U12 改變現有習慣**：「搜尋 X 是誰」「查台積電股價」「搜尋最近的新聞」從開 Google 改成直接回答；要看搜尋頁得說「上網搜尋」或「google」。P3 的差異清單會逐句列出。
- **U12 規則誤判成事實類**：例如「你是誰」「他是誰」、「這個多少錢」（指畫面）會被送去搜尋。代名詞與指示詞列為反例（A15），誤判的代價是多一次搜尋，不會執行任何操作。
- **網路項目把搜尋結果帶進 UI**：只顯示標題與網域，不進紀錄（A10）；Brave 條款允許顯示結果、不允許存成資料庫（0022）。
- 業界參考：分層 NLU（領域 → 意圖 → 參數，Alexa 研究與專利）、查詢意圖分類（Broder 2002：導航型、資訊型、交易型）、聯合搜尋（Spotlight、Raycast 一個查詢問多個來源再排序）、個人資料與世界知識分開由協調器決定（Apple Intelligence）、依問題決定要不要檢索（Adaptive-RAG，NAACL 2024）。共通點是單一分類點、路由表、宣告式備援。

## 9. 實測結果

### 9.1 P0 基線（2026-11-23）

- **語料** `tests/fixtures/routing/corpus.json`：669 句。
  - `query`：89 句，為這個計畫手寫，涵蓋 §4.1 每一列與 U2、U3、U6、U7 的句型。
  - `tests`：398 句，用 AST 從路由相關測試的呼叫參數與 `parametrize` 抽出。
  - `route_corpus`：125 句；`typo_corpus`：57 句。
  - 4 組合併時已去重。只加不刪。
- **快照** `tests/fixtures/routing/snapshot.json`：一句一行，記正規化結果、每個子句的 `match()`（工具＋參數＋信心＋intent）、未命中子句的補救候選、整句的 `intent_guard` 與 `answer_mode`。
  - 測試用 `projects` fixture 固定專案清單；暫存路徑換成 `<projects>`，家目錄換成 `~`。快照不帶這台電腦的路徑（已用 grep 確認）。
  - 測試 `tests/unit/test_routing_snapshot.py` 是 A1。
  - 刻意改壞快照的 `answer_mode` 後，抓到 141 句差異，證明會擋；還原後通過。
- **快照內容與 §1 一致**：

  | 說法 | `match()` | `answer_mode` |
  |---|---|---|
  | 查一下周杰倫是誰 | `context_search` | answer（工具與模式不一致，U2 要修的就是這個） |
  | 找一下周杰倫是誰 | `find_file` name＝周杰倫是誰 | — |
  | 搜尋周杰倫是誰、搜尋履歷 | `context_search` | open |
  | 找周杰倫、找一下我的履歷 | `find_file` | — |
  | 台積電股價 | 交給 Slow Path | — |
  | 不要搜尋周杰倫 | 不命中 | `intent_guard`＝no_action |
- **L1 延遲基線**：`python bench/routing_latency.py`，這台 Mac，真的詞庫與已安裝 App 清單，每句 100 次取中位數。
  - p50 0.011 ms、**p95 0.089 ms**、最大 0.378 ms（第二次跑：0.011／0.087／0.374，差距約 2%）。
  - 最慢的是「打開 YouTube Music」「打開 github.com」這類 App 與網站比對。
  - **U10 門檻：P2、P3 的 p95 ≤ 0.098 ms。**
- **全部離線測試**：`pytest`，3041 passed、6 skipped、126 deselected、0 failed，29.3 秒。

### 9.2 P1 詞表搬家（2026-11-23）

- **搬了哪些**：9 組詞從程式搬到 `vocab/lexicon.json` 的 `query_cues`，程式用 `lexicon.QUERY_CUES` 取用。
  - `question`、`question_end`、`ask`、`open_cue`、`lead_search`、`answer_category`：`fast_path`。
  - `info_question`、`not_a_request`、`search_intent`：`slow_path`。
  - `realtime_words` 原本就在詞庫，沒有動。
  - `open_cue` 的「在…搜尋」是正規表示式結構，留在程式，改用 `lead_search` 組成。
- **搬家前確認**：用一次性腳本比對每一組詞與原本正規表示式的交替項，8 組逐詞相同。
  - 這些正規表示式全部只拿來判斷有沒有命中（`search`／`match` 的布林值），`_alt` 把長詞排前面不影響結果。
- **結果**：
  - 快照 0 差異。
  - `pytest -m "unit or flow"` 2740 passed；`pytest` 3041 passed、6 skipped、0 failed。
  - L1：p50 0.011／p95 0.087／最大 0.375 ms，符合 U10。
- 各組內容仍各自不同，P3 才合併，見 §6。
