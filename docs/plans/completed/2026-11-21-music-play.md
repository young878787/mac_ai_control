# 說「我想聽 OOO」就播放：YouTube 點歌、本機偏好與點歌字詞矯正（實作計畫）

> **歸檔（2026-11-21 結案）**：P1～P6 完成；H1～H4 由使用者判定通過、沒有實際執行；H5（STT 實機辨識歌名）沒有做，列在 §9「尚未驗證」。以下內文保留結案時的樣子，**但 §0、§4.2 的 Data API 退回、`YOUTUBE_API_KEY` 已被 M22 取代（全部刪除）、只說歌手的做法被 M23 取代**，標註見 §2 與 §7。目前有效的決策見 [ADR 0019](../../decisions/0019-music-play.md)。

狀態：**已結案（2026-11-21）：P1～P6 完成；§7.2 R1～R5 已量、§7.3 H1～H4 使用者判定通過；H5（STT 實機）沒有做**。結果與偏離見 §9「P1～P5 實作結果」「§7.2、§7.3 驗收結果」；沿用的決策已寫成 [ADR 0019](../../decisions/0019-music-play.md)。§2 是決策，§6 是每一步的完成條件，§7 是驗收計畫。

（第四版 2026-11-21：§3 的待確認已全部定案；P0-1～P0-8 全部量完，結果見 §9，P0-8 的「有聲」待你確認。）

第四版的改動：搜尋改成 InnerTube 為主、Data API 退回（M3、M8）；YouTube Music 與一般搜尋並行，用 YouTube Music 的歌名、歌手挑官方 MV（M15）；拿掉 HTML 解析；只做拼音矯正，不做動態 STT 提示詞（M12）。

## 0. 結論

1. 常見的點歌說法（我想聽／播放／放一首／來一首 X）在 Fast Path 命中，產生 `play_media(query)`。比對本身不碰網路、不碰資料庫（紅線 1），網路和資料庫只在執行階段使用。
2. 執行順序：拼音矯正 → 本機快取 → **InnerTube 兩邊並行**（YouTube 一般搜尋、YouTube Music 搜尋）→ YouTube Data API v3（有金鑰才用）→ 開搜尋結果頁並回 ⚠️。前四種都會**直接播**，網址用 `watch?v={id}&list=RD{id}`（YouTube 合輯，播完會接著播）。
3. **挑哪一支**：YouTube Music 的第一筆是對得上 query 的「歌曲」時，用它的歌名和歌手，在一般搜尋前 5 筆裡挑對得上的那支（通常是官方 MV）；挑不到就播 YouTube Music 的歌曲 ID；YouTube Music 對不上時播一般搜尋第一筆。兩邊同時查，不多花時間（實測並行 p50 520 ms、p95 811 ms）。
4. **配額**：InnerTube 不需要金鑰、不扣配額。Data API 只在 InnerTube 兩邊都失敗時才用（`search.list` 一次 100 units，一天約 100 次）。正常情況 API 呼叫次數是 0。
5. 偏好資料存在本機 **SQLite**（`data/music.sqlite3`）：**永久保存**、不進版控、第一次啟動時自動建立。
   - schema 由 **Alembic** 管理，方便開發時改 schema，**之後可以拿掉**（§4.4 的移除步驟）。
   - 執行期的查詢只用 Python 內建的 `sqlite3`，不依賴 Alembic 或 SQLAlchemy。
6. 點歌字詞矯正**放在資料庫，會動態變動**（`terms` 表），只在執行階段用拼音比對改寫 query。**不改 STT 提示詞**，所以不需要重連 STT。
7. 播放控制（下一首、上一首、暫停、繼續）可以控制**背景**的音樂分頁：先用輔助使用 API 按播放器按鈕，按不到再送媒體鍵（只有播放／暫停、下一首、上一首三個）。

## 1. 現況（2026-11-21 實際執行確認）

| 項目 | 現況 |
|---|---|
| 「我想聽周杰倫」「播放周杰倫的晴天」「放一首晴天」「下一首」 | `FastPathMatcher().match()` 全部回傳 None，會交給 LLM（本機約 1～3 秒），而且不一定會播 |
| 「播放」「暫停」 | `app_shortcuts.play_pause`：YouTube 按按鈕，Music／Spotify 用 `playpause`。**只對最前面的 App 有效**；`native.press_button(pid, pattern)` 只找目前視窗 |
| STT 提示詞 | `build_vocab.py` 產生 `vocab/stt_hints.txt`（目前 54 條），`SttClient` 只在 WS 連線時送一次。本計畫不改 |
| 搜尋 | InnerTube 一般搜尋 20/20 成功、p50 482 ms；YouTube Music 搜尋 p50 463 ms；兩邊並行 p50 520 ms（§9） |
| 拼音（macOS ICU `Han-Latin; Latin-ASCII; Lower`，透過 ctypes 呼叫） | 每個詞約 0.08 ms。「周杰倫」和「周杰論」都轉成 `zhou jie lun`；「稻香」和「到香」都轉成 `dao xiang`；「晴天」和「情天」都轉成 `qing tian` |
| 瀏覽器 | `/Applications` 只有 Safari，Chrome 沒有安裝 |
| 依賴 | 目前沒有安裝 `alembic`／`SQLAlchemy`；專案沒有 `requirements.txt`，依賴列在 `README.md` 的 `pip install` 那一行 |
| 既有守門 | `_NEG_RE`（不要／不想／別…）、`_NARR_RE`（昨天／剛剛／…了）、`_QUESTION_RE`（嗎／什麼／哪…） |
| 還沒量 | 實機 STT 對歌名的辨識（只能 H5 人工觀察）；`main.py` 其他 import 加上 Alembic 後的啟動時間（P2 之後的 R6） |

## 2. 決策

| # | 決策 | 來源 |
|---|---|---|
| M1 | 播放目標是 YouTube 網頁＋合輯（`&list=RD{id}`） | 使用者 2026-11-21 |
| M2 | 直接播，不列清單讓使用者選（挑哪一支見 M15） | 使用者 2026-11-21 |
| M3 | **搜尋以 InnerTube 為主**（YouTube 與 YouTube Music 網頁本身用的內部介面），**YouTube Data API v3 只當退回** | 使用者 2026-11-21（方案 B，取代原本「搜尋使用 Data API」） |
| M4 | 偏好只存在本機 | 使用者 2026-11-21 |
| M5 | 模糊的要求直接拿去 YouTube 搜尋，不送 LLM 或雲端 | 使用者 2026-11-21 |
| M6 | `play_media` 是本機工具，LLM（本機和雲端）都看不到；`SYSTEM_PROMPT`、`TOOLS_SCHEMA` 不動 | 紅線 2、5 |
| M7 | 守門不放寬：否定、敘述、提問都不走這條規則 | 紅線 4 |
| M8 | 退回順序：快取 → InnerTube 兩邊並行 → Data API（有金鑰才用）→ 開搜尋結果頁並回 ⚠️。**不做 HTML 解析**（比 InnerTube 慢、回應大 4 倍，20 句裡有 2 句解析不到） | 使用者 2026-11-21（取代 Q1 的「改用解析搜尋頁」）（**Data API 那一段被 M22 取代**） |
| M9 | 金鑰放 `.env` 的 `YOUTUBE_API_KEY`，只在 `youtube_client.py` 讀，規則和 ADR 0010 相同；ADR 0019 把 0010 擴充成「每把金鑰只在它專屬的 client 讀」。InnerTube 不用金鑰 | 使用者 2026-11-21（Q2）（**被 M22 取代**：不再有金鑰） |
| M10 | SQLite 永久保存、不進版控、自動建立與維護；schema 由 Alembic 管理 | 使用者 2026-11-21（Q4） |
| M11 | §7.2 的實測門檻 | 使用者 2026-11-21（Q5） |
| M12 | 點歌字詞矯正放在資料庫，動態增刪改並定時整理，**只在執行階段做拼音矯正，不做動態 STT 提示詞**（P0-6：提示詞只在 WS 連線時送，要主動重連才會生效） | 使用者 2026-11-21（做法 B） |
| M13 | 播放控制：先用輔助使用 API 按音樂分頁上的播放器按鈕，按不到再送媒體鍵。按鈕用語言無關的錨點 `\(SHIFT\+n\)$`、`\(SHIFT\+p\)$`；AX 只看得到 Safari 最近使用的視窗、而且音樂分頁要是顯示中的分頁，其餘情況靠媒體鍵。媒體鍵用 ctypes 呼叫 `NSEvent`＋`CGEventPost`（不需要 pyobjc），只允許播放／暫停、下一首、上一首三個；紅線 8 加上這個例外，記錄在 ADR 0019 | 使用者 2026-11-21（Q3）；P0-4、P0-5 |
| M14 | Alembic 定位是開發期工具，之後可以移除：執行期不 import 它；每個 migration 同時寫 `PRAGMA user_version`，移除後可以改用內建的 `sqlite3` 從 `user_version` 繼續。啟動時間預算是增加 ≤ 300 ms，超過就改在背景執行緒做，資料庫準備好之前點歌照常播放、只是不寫紀錄 | 使用者 2026-11-21（Q6） |
| M15 | **YouTube Music 路由＋挑 MV**：兩邊並行查；YouTube Music 第一筆是對得上的歌曲時，用它的歌名和歌手在一般搜尋前 5 筆挑對得上的那支（通常是官方 MV），挑不到才播 YouTube Music 的歌曲 ID；對不上時播一般搜尋第一筆（§4.2） | 使用者 2026-11-21（方案 A） |
| M16 | 不使用第三方搜尋套件（`ytmusicapi`、`yt-dlp` 等），自己用 `urllib` 送 InnerTube 請求 | AGENTS「不新增 dependency」；§9 實測自寫版本可用 |
| M17 | InnerTube 的 `clientVersion` 動態取得：`config` 放預設值；定時整理時從兩個首頁讀新值存進 `maintenance` 表；請求失敗時只標記「需要更新」，不當場重試 | §8 風險（版本過舊會被拒） |
| M18 | **敘述句守門不變嚴**（U1）：「聽說明天會下雨」「他想聽周杰倫」「她說想聽晴天」不點歌，但允許交給 LLM；§7.1 C2 的 G1（敘述句不呼叫任何模型）不適用這三句 | 使用者 2026-11-21 決定（實作中） |
| M19 | **媒體鍵只在 Safari 有 YouTube 影片分頁時才送**（U2）：任何視窗、任何分頁都算；沒有就回 ⚠️「找不到正在播放的音樂」，不送（避免被 Music 等別的播放來源接走） | 使用者 2026-11-21 決定（實作中） |
| M20 | **暫停／繼續有方向**（U3）：前景是 Music／Spotify／YouTube 分頁時，Music／Spotify 用 `pause`／`play` 指令，YouTube 只按暫停或播放鈕，已是目標狀態回 ✅ 不動作；單說「播放」「播放暫停」仍是切換。取代 K9 的「既有 `play_pause` 行為不變」 | 使用者 2026-11-21 決定（實作中） |
| M21 | **`next_track`、`prev_track` 開放給雲端的 `front_action`**（U4）：雲端 schema 的 id enum 多兩個；雲端不帶 direction，維持切換 | 使用者 2026-11-21 決定（實作中） |
| M22 | **刪除 Data API 退回**：搜尋全面走 InnerTube（不用金鑰、不扣配額），兩邊都沒有可播的就開搜尋頁並回 ⚠️；`YOUTUBE_API_KEY`、`search_api`、`source=api` 全部移除，ADR 0010 恢復原狀。取代 M8 的 Data API 段落與 M9 | 使用者 2026-11-21（R5 時決定）|
| M23 | **只說歌手播歌手卡片第一首**：YouTube Music 回歌手卡片時，播卡片底下第一首可播的歌（`source=ytm`，歌手本人的熱門歌曲，之後由 `list=RD` 合輯接著播相關的歌）；卡片沒有歌才播一般搜尋第一筆。取代 §3 第 2 點 | 使用者 2026-11-21（R3 後決定）|

## 3. 待確認

無。以下兩項已依建議定案（使用者 2026-11-21「照你建議」）：

1. **Alembic 一開始就用背景模式**（M14、D7）：P0-7 穩定狀態 import 中位數 201 ms，餘裕只有約 100 ms，而且還沒和 `main.py` 其他 import 一起量。背景模式是預設，不等超標才改。
2. ~~**只說歌手（「我想聽周杰倫」）**：YouTube Music 第一筆通常是歌手卡片，不接受，播一般搜尋第一筆（route_bench：5 句裡 3 句是該歌手本人的作品）。~~ 被 M23 取代（R3 實測播到團員個人作品、第三方串燒）。

實作途中發現和 §2 衝突、或量測結果不如預期時，停下來回報。

## 4. 設計

### 4.1 觸發句型（`fast_path._r_music`）

**會收的句型**

| 句型 | 例子 → query |
|---|---|
| `[幫我／請][我]想聽／要聽／想要聽 X` | 我想聽周杰倫 → `周杰倫` |
| `[幫我／請]播放 X` | 播放晴天 → `晴天` |
| `[幫我]放一首／放首／放點 X` | 放點輕音樂 → `輕音樂` |
| `來一首／來首／來點 X` | 來首五月天 → `五月天` |
| `聽 X 的歌` | 聽五月天的歌 → `五月天` |
| `X 的歌來一首` | 五月天的歌來一首 → `五月天` |

- **清理 query**：句尾的「的歌、這首歌、一下、給我聽、吧、好了」去掉；開頭的「一首」去掉。中間的「的」保留（`周杰倫的晴天`）。
- **query 是空字串**時（「我想聽歌」「放音樂」「來一首」）改播常聽的歌（§4.4）。
- **「播放」後面接的是控制詞時不收**：速度、清單、設定、鍵、器、紀錄、次數、模式、音量。
- 單獨的「聽 X」（「聽晴天」）**不收**。「聽好了」「聽我說」太容易誤觸發，交給 LLM。

**一定不能命中的說法**（完整清單見 §7.1 的 C2）：
- 否定、敘述、提問
- 受詞是「你／我」（我想聽你說、聽我說）
- 疊字「聽聽」
- 第三人稱主語（他想聽、她說想聽）
- 「放」接的不是點歌用語（放大、放到、放進、放假、放心、放棄）
- 單獨的播放、暫停、繼續播放、暫停播放：維持原本的 `play_pause`

**完整度和端點偵測**（紅線 3）：
- 「我想聽」「播放」「來一首」「放首」後面沒接東西，算不完整（等 2.0 s 後才收句；收句後如果仍是空的 query，就播常聽的歌）
- 句尾是「的」算不完整
- 其他命中點歌規則的句子算完整

### 4.2 執行：`play_media(query)`（`local_actions.py`）

1. 正規化 query，然後做點歌字詞矯正（§4.5）。
2. 查快取：`queries` 表有這個 query 而且在 30 天內 → 直接拿 videoId，不連網。使用者糾正過的對應（`pinned`）不會過期。
3. 沒命中時，**兩條執行緒並行**送 InnerTube 請求，最多等 `YT_TIMEOUT_S`（2.0 s，兩邊共用同一個截止時間）：
   - **一般搜尋**：`POST https://www.youtube.com/youtubei/v1/search`，client `WEB`，`hl=zh-TW`、`gl=TW`，`params` 限定只要影片；取 `videoRenderer` 前 5 筆（id、標題、頻道）。
   - **YouTube Music 搜尋**：`POST https://music.youtube.com/youtubei/v1/search`，client `WEB_REMIX`；取第一個結果的類型、videoId、歌名、歌手。**歌手一律從 run 的 `pageType`＝`MUSIC_PAGE_TYPE_ARTIST` 取**，不用「•」切字串（route_bench 發現只說歌手時切錯）。
   - 執行緒只做網路與解析，**不碰資料庫**；結果交回 `command_worker` 的執行緒處理。
4. **接受 YouTube Music 的第一筆**，條件全部成立：
   1. 類型是歌曲（`Song`／`歌曲`），不是歌手、專輯、播放清單、影片
   2. 歌名和歌手都不含伴奏類標記：karaoke、伴奏、卡拉OK、KTV、instrumental、cover、翻唱
   3. **歌名對得上**：歌名去掉括號內容（`()`、`（）`、`【】`、`[]`）後，以「正規化後的字面」或「拼音音節」比對，是 query 的連續子字串
   4. **剩下的字對得上**：query 去掉歌名、「的」與空白後如果還有字，那些字要以字面或拼音對到某位歌手；沒有剩字就不要求
5. **選要播的影片**：
   - 接受了 YouTube Music：在一般搜尋前 5 筆裡，依序找：
     ① 頻道名稱對到歌手（官方頻道）、而且標題含歌名的第一筆；
     ② 沒有的話，標題同時含歌名和歌手、而且不含排除標記（伴奏類標記＋歌詞、lyrics）的第一筆；
     ③ 都沒有（或一般搜尋失敗）就播 YouTube Music 的歌曲 ID。`source` 分別記 `mv`、`mv`、`ytm`。
   - 沒接受（或 YouTube Music 失敗）：播一般搜尋第一筆，`source=web`。
   - 兩邊都失敗（HTTP 錯誤、逾時、回應格式不對、0 筆）：有金鑰就呼叫 Data API `search.list`（`type=video`、`videoCategoryId=10`、`regionCode=TW`、`maxResults=5`，逾時 `YT_TIMEOUT_S`；標題先 `html.unescape`），`source=api`；沒金鑰或 API 也失敗就開 `search_url` 搜尋頁並回 ⚠️，`source=page`。
   - 「換一首」的候選清單：選中的那支排第一，後面接一般搜尋前 5 筆中其餘的（去重）。
6. **videoId 一律先比對 `^[A-Za-z0-9_-]{11}$`**，不符合就丟棄並換下一筆。InnerTube 和 API 都是不可信的外部輸入，不能讓它跑進 osascript 或網址。
7. 開啟：`https://www.youtube.com/watch?v={id}&list=RD{id}`。
   - 用**一支** osascript 在 Safari 所有視窗裡找含 `list=RD` 的分頁並當場改網址（P0-3：p50 197 ms；另外支援 Chrome，只做離線測試）。
   - 找不到就開新分頁。osascript 一律加逾時。
   - Safari 不需要改自動播放設定（P0-2：3/3 有聲音）。
8. 回傳 `✅ 播放：{title}（{channel}）`（`ytm` 時用 YouTube Music 的歌名與歌手）；退回搜尋頁時回 ⚠️；失敗回 ❌（紅線 11）。
9. 寫 `play` 事件（`source`＝cache／mv／ytm／web／api／page），並更新 `terms`（§4.5）。所有 SQL 一律用參數化查詢。

**InnerTube 的 `clientVersion`**（M17）：`config` 放兩個預設值（WEB `2.20261119.07.00`、WEB_REMIX `1.20261117.06.00`）。定時整理時，由背景執行緒抓 `youtube.com`、`music.youtube.com` 首頁讀 `INNERTUBE_CLIENT_VERSION`（逾時 2 s，格式要符合 `^\d\.\d{8}\.\d{2}\.\d{2}$`），結果交回 `command_worker` 寫進 `maintenance` 表。點歌時 InnerTube 回 4xx 只標記「需要更新」，不當場重試，直接往下退回。

**請求內容**：InnerTube 請求只帶搜尋字、client 名稱與版本、語言地區，不帶 cookie、金鑰或任何使用者資訊；User-Agent 用一般瀏覽器字串。

**最差耗時**：InnerTube 2 s ＋ API 2 s ＋ osascript，約 4.3 s。`command_worker` 是單執行緒，這段時間會擋住下一句；正常情況（InnerTube 成功）約 0.7 s。

### 4.3 播放控制（M13）

| 說法 | 做法（依序嘗試，前一個成功就停） |
|---|---|
| 下一首、上一首、暫停、繼續 | ① 前景是 Music 或 Spotify：沿用 `app_shortcuts` 的 App 指令<br>② Safari 最近使用的視窗（`AXFocusedWindow`→`AXMainWindow`）目前顯示的就是音樂分頁：輔助使用 API 在**那個視窗**按播放器按鈕（`native.press_button_in_window`，可以是背景視窗）。按鈕比對：暫停 `^暫停 \(k\)$`／`^Pause \(k\)$`、播放 `播放.*k$`／`^Play \(k\)$`、下一首 `\(SHIFT\+n\)$`、上一首 `\(SHIFT\+p\)$`<br>③ **Safari 任何視窗任何分頁有 YouTube 影片分頁時**才送媒體鍵（`native.post_media_key("play"\|"next"\|"previous")`，其他值一律拒絕；使用者 2026-11-21 決定 U2）<br>④ 回 ⚠️「找不到正在播放的音樂」 |
| 換一首、不是這首 | 改播同一次搜尋的下一個候選，`queries` 的快取改指向新的影片並標記 `pinned` |
| 我喜歡這首、不要再放這首 | 用 osascript 讀音樂分頁目前的網址取得 videoId，寫 `like`／`dislike` 事件 |
| 我最常聽什麼 | 用 `chat_reply` 列出前 5 名歌手和歌曲 |

- **既有行為的改變**：
  - 前景不是 YouTube、Music、Spotify 時說「暫停」，原本回報「目前的 App 不支援」；改成會控制背景的音樂分頁。
  - **暫停／繼續有方向**（使用者 2026-11-21 決定 U3）：前景是 Music／Spotify／YouTube 分頁時，「暫停」「繼續」「暫停播放」「繼續播放」不再切換——Music／Spotify 用 `pause`／`play` 指令，YouTube 只按暫停或播放鈕，已是目標狀態回 ✅ 不動作；單說「播放」「播放暫停」仍是切換。背景控制同理：能用 AX 確認狀態時依方向按鈕，媒體鍵的 play 是切換，所以確認得了狀態就不送。
- **不採用的做法**：Safari「允許 Apple Events 執行 JavaScript」是全域設定，安全面擴大太多；MediaRemote 是私有 API。

### 4.4 偏好資料庫：SQLite＋Alembic

**存放位置與生命週期**

- 檔案是 `data/music.sqlite3`，`data/` 加進 `.gitignore` 與 AGENTS.md 的「不入版控」清單。啟動時如果不存在就自動建立。
- **永久保存**：事件不刪除。估計每筆約 200 bytes，一天 50 筆，一年約 3.6 MB。
- 啟動時 `mac_ai.db.ensure_schema()` 的流程：
  1. 版本落後 head → 先用 `sqlite3` 的 backup API 備份成 `data/music.sqlite3.bak-{舊版本}`
  2. 執行 `alembic.command.upgrade(cfg, "head")`。以程式組設定，不在專案根目錄放 `alembic.ini`；SQLite 的 `ALTER` 限制用 `render_as_batch=True` 處理
  3. 升級失敗：點歌照常播放但不寫紀錄，回報 ⚠️，原檔不刪除也不覆蓋
- migration 腳本放 `mac_ai/db/migrations/versions/`，進版控；資料庫檔案不進版控。
- 執行期只由 `command_worker` 用 `sqlite3` 讀寫（只有一個寫入者，開 WAL）。搜尋執行緒、`clientVersion` 更新執行緒都不碰資料庫。

**日後移除 Alembic 的步驟**（寫進 ADR 0019）：
1. 每個 migration 的 `upgrade()` 最後都要執行 `PRAGMA user_version = {編號}`。
2. 要移除時：把 `versions/` 裡的升級內容改寫成 `mac_ai/db/migrate.py` 的「`user_version` → SQL 清單」。
3. 刪除 `migrations/`、`alembic_version` 表，以及 README 裡的 `alembic` 依賴。
4. 執行期程式不需要改。

**schema**（`0001_music_init`）

```sql
CREATE TABLE events (               -- 只追加、永久保存；terms 與統計都能從這裡重算
  id INTEGER PRIMARY KEY, ts REAL NOT NULL,
  kind TEXT NOT NULL,               -- request|play|replace|next|prev|pause|resume|like|dislike|fallback|correct
  utterance TEXT, query TEXT, corrected TEXT,   -- 原句（給矯正分析用）、清理後、矯正後
  video_id TEXT, source TEXT,       -- cache|mv|ytm|web|api|page|ax|mediakey|app
  latency_ms REAL, ok INTEGER NOT NULL);
CREATE INDEX events_ts ON events(ts);
CREATE TABLE videos (video_id TEXT PRIMARY KEY, title TEXT, channel TEXT, channel_id TEXT,
  first_seen REAL, last_seen REAL);
CREATE TABLE queries (query TEXT PRIMARY KEY, video_id TEXT NOT NULL, results TEXT,  -- results：候選 id（JSON，最多 6 筆）
  updated REAL NOT NULL, pinned INTEGER NOT NULL DEFAULT 0);                          -- pinned：使用者糾正過
CREATE TABLE terms (term TEXT PRIMARY KEY, kind TEXT NOT NULL,   -- artist|title|query
  pinyin TEXT NOT NULL, weight REAL NOT NULL, plays INTEGER NOT NULL,
  liked INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL);
CREATE INDEX terms_pinyin ON terms(pinyin);
CREATE TABLE maintenance (key TEXT PRIMARY KEY, value TEXT);     -- last_run、web／ytm 的 clientVersion、需要更新的旗標
-- 結尾：PRAGMA user_version = 1
```

**計分與「常聽的歌」**

- 計分：主動點播每次 +1，以半衰期 30 天衰減；`like` +3；`dislike` 直接排除；播放後 30 秒內就說「下一首」扣 0.3（跳過是有雜訊的訊號，只給小權重）。
- 常聽的歌：從分數前 20 名依權重抽一首（亂數可以注入），排除最近 10 首，用快取的 videoId，不連網。資料庫是空的時回 ⚠️「還沒有聽歌紀錄，說『我想聽』加歌名」。
- 限制：只記得到語音指令，用滑鼠跳歌記不到。
- 擴充：新的訊號就是新的 `kind`；改 schema 就新增一個 revision；`terms` 可以用 `python -m mac_ai.db.rebuild` 從事件重算。

### 4.5 點歌字詞矯正：資料庫裡的動態詞表（只做拼音矯正）

**增刪改查（在 `command_worker` 執行，每次只花幾 ms）**

| 操作 | 時機 | 內容 |
|---|---|---|
| 新增 | 播放成功 | upsert 三種詞：歌手、歌名、使用者的說法。`mv`／`ytm` 時歌手與歌名**直接用 YouTube Music 的欄位**（乾淨，不用清理）；`web`／`api` 時才從頻道名稱（去掉「Official」「- Topic」「官方頻道」這類字尾）與標題（去掉【】、「MV」「Lyrics」「歌詞」「Official Music Video」等標記）推 |
| 新增 | 播放成功 | `pinyin` 欄位存「無聲調拼音＋放寬」後的結果（zh→z、ch→c、sh→s、ing→in、eng→en；英文轉小寫、去掉空白和標點） |
| 查詢 | `play_media` 執行時 | 拼音比對（見下方條件） |
| 更新 | 播放 | `plays`+1，`weight`+1 |
| 更新 | 喜歡 | `liked`=1，`weight`+3 |
| 更新 | 換一首 | 原影片的歌名 `weight` −0.5 |
| 刪除 | 不要再放這首 | 刪除該歌名的詞 |
| 刪除 | 定時整理 | 衰減後 `weight` < 0.05 而且沒按過喜歡的詞刪除；總數超過 500 時從權重最低、沒按過喜歡的開始刪 |

**定時整理**：啟動時跑一次，之後每 `MUSIC_MAINT_INTERVAL_S`（6 小時）由計時器把整理工作排進指令佇列，不另開會寫資料庫的執行緒。整理內容：`terms` 衰減與刪除、`clientVersion` 更新（§4.2，網路部分在背景執行緒，寫入回到 `command_worker`）。

**矯正規則**：先用「的」和空白把 query 切成幾段，每段分別比對。以下條件必須**全部**成立才改寫那一段：
1. 這一段本身不在 `terms` 裡
2. 拼音只對到**一個** term（整段拼音完全相等，不做部分比對）
3. 那個 term 播過至少 2 次，或按過喜歡

其他情況一律不改。每次改寫都寫一筆 `correct` 事件，並把結果記在 `events.corrected`。沒有任何紀錄的新歌講錯字時，靠 YouTube Music 搜尋本身的糾錯（route_bench：「到香」「告白汽球」都回對的歌）加上 §4.2 的拼音比對接受。

**不做的事**：
- 不改 STT 提示詞（M12）：`vocab/stt_hints.txt` 與 `SttClient` 送出的 `hints` 不受 `terms` 影響
- 不寫回 `vocab/lexicon.json`
- 誤聽的觸發詞（例如「想聽」被聽成「想停」）沿用現有流程：從 `normalize.jsonl` 回收，由人加進 `term_misheard`

## 5. 影響範圍

| 檔案 | 改動 |
|---|---|
| `mac_ai/router/fast_path.py` | `_r_music` 與守門 |
| `mac_ai/stt/completeness.py` | 點歌句的完整度判斷 |
| `mac_ai/router/local_actions.py` | `play_media`、播放控制、回饋 |
| `mac_ai/router/actions.py` | 找音樂分頁、改網址、讀網址的 osascript |
| `mac_ai/router/native.py` | `press_button_in_window`、`post_media_key` |
| 新增 `mac_ai/router/youtube_client.py` | InnerTube 兩邊的請求與解析、並行與截止時間、YouTube Music 接受規則、挑 MV、Data API 退回、金鑰讀取、videoId 驗證、`clientVersion` 讀取 |
| 新增 `mac_ai/router/music_prefs.py` | 事件、計分、`terms` 增刪改查、拼音（ICU ctypes）、定時整理 |
| 新增 `mac_ai/db/` | `__init__.py`（`ensure_schema`、備份）、`rebuild.py`、`migrations/env.py`、`migrations/versions/0001_music_init.py` |
| `mac_ai/config.py` | `YT_TIMEOUT_S`、InnerTube 端點與預設 `clientVersion`、`MUSIC_DB_PATH`、快取 TTL、`MUSIC_MAINT_INTERVAL_S`、詞表上限 |
| `vocab/lexicon.json` | 點歌動詞；`app_shortcuts` 的 `next_track`／`prev_track` |
| `main.py`、`mac_ai/assistant.py` | 啟動時呼叫 `ensure_schema`、定時整理計時器 |
| `.gitignore`、`.env.example` | `data/`、`YOUTUBE_API_KEY=`（註解說明是退回用） |
| `README.md` | `pip install` 那一行加 `alembic` |
| 文件 | ADR 0019（點歌、InnerTube 為主與 API 退回、YouTube 金鑰、Alembic 與移除步驟、播放控制與媒體鍵）、`ARCHITECTURE.md`、`docs/flows/main-flow.md`、`docs/guides/testing.md`（L0 對照表加 music 相關檔案）、`AGENTS.md`（不入版控清單、紅線 8 與 10 的措辭） |

`mac_ai/stt/client.py` 不改（第三版原本要改，M12 取消）。

## 6. 實作步驟與每一步的完成條件

每一步都要**先過完成條件才進下一步**；沒過就停下來回報，附上實際的輸出和數字。

### P0 量測（不改產品程式碼；腳本與暫存 venv 都放 scratchpad）

| # | 量什麼 | 做法 | 判斷 | 不如預期時 |
|---|---|---|---|---|
| P0-1 | API 延遲 | 你的金鑰，20 個不重複 query，`urllib` 直接打 `search.list`，每個量一次 | 記錄 p50／p95。p95 > 1.5 s 會讓 R1 達不到 | 停下回報 |
| P0-2 | Safari 有聲自動播放 | 開 3 個不同的合輯網址；由你確認有沒有聲音 | 3/3 在 3 秒內有聲音 | 依序試：① Safari 對 youtube.com 設「允許所有自動播放」；② 頁面載入後用輔助使用 API 按一次播放鈕 |
| P0-3 | 找分頁並改網址 | osascript 在 Safari 找含 `list=RD` 的分頁並改網址，10 次 | p50 ≤ 200 ms（R2 要 ≤ 0.3 s） | 停下回報 |
| P0-4 | 背景視窗按按鈕 | 音樂分頁在背景視窗、而且是該視窗目前的分頁；前景是 VS Code；用輔助使用 API 按下一首、暫停 | 兩個按鈕都會作用，各 3/3；記錄按鈕的實際說明文字 | 這一層退化成「只能控制前景」，改由媒體鍵補上，回報 |
| P0-5 | 媒體鍵 | Safari 在播 YouTube、Music 沒開：送 play／next／previous；再測 Music 開著但暫停的情況 | 第一種情況 3/3 都作用在 YouTube；第二種記錄實際作用在誰 | 回報後決定是否只在 Music 沒開時才送 |
| P0-6 | STT 提示詞多久生效 | 讀 `SttClient` 的重連邏輯和 server 的 `max_session_seconds` | 記錄新提示詞最晚多久會生效 | 已依結果改成 M12（不做動態提示詞） |
| P0-7 | Alembic 成本 | 暫存 venv 安裝 alembic；在已經是 head 的資料庫上，量 `import alembic` 加上 `upgrade head` 的時間，5 次 | 中位數 ≤ 300 ms（M14） | 依 M14 改成背景執行緒 |
| P0-8 | YouTube Music 歌曲 ID 在 youtube.com 播放 | **先問你、你在場時才做**。用 YouTube Music 搜尋取 3 首歌曲的 videoId（挑一般搜尋前 5 筆裡沒有的那種官方音源），用 `make new document` 開一個**新的** Safari 視窗、從回傳值取得視窗參照，依序載入 `watch?v={id}&list=RD{id}`；每首等播放鈕變成「暫停 (k)」，再按一次下一首看合輯有沒有接下去 | 3/3 在 3 秒內有聲音（你確認）、合輯下一首 3/3 換到不同的 videoId | 退化成「YouTube Music 只當路由參考，挑不到 MV 就播一般搜尋第一筆」，回報後由你決定 |

**P0 完成條件**：8 項數字都寫進 §9；不如預期的項目已回報並取得你的決定。

### P1～P6

| 步驟 | 內容 | 完成條件 |
|---|---|---|
| P1 | 規則、守門、完整度；`play_media` 先回 ⏭️ 佔位 | §7.1 的 C1、C2、C3、C4、C5 通過；`pytest -m "unit or flow"` 0 failed |
| P2 | 安裝 `alembic`；`mac_ai/db`、`0001` migration、備份、`ensure_schema`、背景模式 | D1～D8 通過 |
| P3 | `youtube_client`（InnerTube 兩邊、並行、接受規則、挑 MV、API 退回、`clientVersion`、videoId 驗證）、快取、找分頁、開分頁 | Y1～Y16 通過；`pytest -m "unit or flow"` 0 failed |
| P4 | `music_prefs`：事件、計分、常聽的歌、`terms` 增刪改查、拼音矯正、定時整理 | S1～S10、T1～T8 通過 |
| P5 | 下一首、上一首、暫停、繼續、換一首、喜歡／不要、最常聽什麼 | K1～K9 通過；`pytest tests/unit/test_native.py tests/flow/test_route_context.py` 0 failed |
| P6 | 文件、ADR 0019、README、`.env.example`；跑 §7.2、§7.3 | L2：`pytest` 0 failed；`cd desktop && node --test` 0 fail；R1～R7 達標；H1～H5 由你確認 |

## 7. 驗收計畫

### 7.1 自動化（離線，必須全部通過）

共同規則：
- 測試不連網（`urlopen` 用假物件）、不執行 `open`／`osascript`、不寫專案的 `data/` 和 `logs/`（用 `tmp_path`）。
- 時間和亂數一律注入。
- 「100%」的意思是語料裡每一句都要對，不接受用比例過關。
- InnerTube 的 fixture 是 P0 期間存下的真實回應（去掉追蹤欄位），放 `tests/fixtures/youtube/`。

**C：路由與守門**（`tests/flow/test_route_music.py`、`tests/unit/test_completeness.py`）

| # | 項目 | 通過標準 | 失敗時 |
|---|---|---|---|
| C1 | 正例語料，至少下列 42 句（實作時可以加，不能刪） | 每句都只產生一個 `play_media`，query 和預期**逐字相同**；LLM 呼叫 0 次（`forbid_llm`） | 修規則，不放寬守門 |
| C2 | 反例語料，至少下列 40 句 | 每句都**不**產生 `play_media`；否定和敘述句的 LLM 呼叫 0 次（G1；U1 的三句除外，見下表）；「播放」「暫停」「繼續播放」「暫停播放」仍產生 `front_action(play_pause)` | 修規則；誤觸發優先處理 |
| C3 | 完整度 | 不完整：「我想聽」「播放」「來一首」「放首」「我想聽周杰倫的」；完整：C1 中其他所有句子 | 調 `completeness` |
| C4 | `match()` 保持純粹 | C1、C2 全部語料跑 `match()` 期間，`urlopen`、`sqlite3.connect`、`subprocess.run` 呼叫 0 次 | 把存取移到執行階段 |
| C5 | 既有語料不退步 | `tests/flow/test_typo.py`、`tests/flow/test_route_corpus.py`、`tests/unit/test_endpoint.py` 0 failed | 回退造成問題的改動 |

C1 正例（原句 → query；`""` 表示播常聽的歌）：

| 原句 | query | 原句 | query |
|---|---|---|---|
| 我想聽周杰倫 | 周杰倫 | 我要聽周杰倫 | 周杰倫 |
| 我想要聽晴天 | 晴天 | 想聽稻香 | 稻香 |
| 我想聽周杰倫的晴天 | 周杰倫的晴天 | 我想聽伍佰的挪威的森林 | 伍佰的挪威的森林 |
| 我想聽一首晴天 | 晴天 | 我想聽晴天吧 | 晴天 |
| 我想聽周杰倫的晴天給我聽 | 周杰倫的晴天 | 幫我放一首晴天 | 晴天 |
| 播放晴天 | 晴天 | 請播放晴天 | 晴天 |
| 幫我播放周杰倫 | 周杰倫 | 播放周杰倫的稻香 | 周杰倫的稻香 |
| 播放周杰倫的歌 | 周杰倫 | 播放 Shape of You | Shape of You |
| 播放 Lo-fi hip hop | Lo-fi hip hop | 放一首晴天 | 晴天 |
| 放首稻香 | 稻香 | 放一首周杰倫的歌 | 周杰倫 |
| 放點輕音樂 | 輕音樂 | 放一首適合下雨天的歌 | 適合下雨天 |
| 來一首告白氣球 | 告白氣球 | 來首五月天 | 五月天 |
| 來點爵士樂 | 爵士樂 | 來一首鄧紫棋的光年之外 | 鄧紫棋的光年之外 |
| 聽五月天的歌 | 五月天 | 五月天的歌來一首 | 五月天 |
| 我想聽 Taylor Swift | Taylor Swift | 我想聽 lofi | lofi |
| 我想聽讀書用的輕音樂 | 讀書用的輕音樂 | 我想聽 podcast | podcast |
| 我想聽這首歌晴天 | 晴天 | 播放告白氣球一下 | 告白氣球 |
| 我想聽歌 | "" | 想聽歌 | "" |
| 放音樂 | "" | 來點音樂 | "" |
| 我想聽音樂 | "" | 播放音樂 | "" |
| 來一首 | "" | 放首歌 | "" |

C2 反例（不能產生 `play_media`）：

| 類別 | 句子 |
|---|---|
| 否定 | 我不想聽這首、不要播放、別放這首歌、先不要放音樂、我沒有要聽歌 |
| 敘述 | 聽說明天會下雨、我剛剛在聽周杰倫、昨天聽了晴天、我以前很常聽五月天、他想聽周杰倫、她說想聽晴天（使用者 2026-11-21 決定 U1：「聽說明天會下雨」「他想聽周杰倫」「她說想聽晴天」這三句只要求不產生 `play_media`，**允許交給 LLM**，不要求 G1；其餘敘述句仍要求不呼叫任何模型） |
| 對話 | 我想聽聽你的看法、我想聽你說、我想聽你解釋一下、想聽你唱歌、聽我說、聽好了、我想聽聽晴天 |
| 提問 | 播放清單在哪、你想聽什麼歌、可以放周杰倫的歌嗎、有沒有好聽的歌、聽得到嗎 |
| 「放」的其他意思 | 放大、放大一點、放到桌面、放進資料夾、放假、放心、放棄 |
| 「播放」接控制詞 | 播放速度調快、播放設定、播放器 |
| 聲音問題 | 聽不到聲音、聽不清楚 |
| 既有功能 | 播放、暫停、繼續播放、暫停播放（這四句要產生 `play_pause`） |

**D：資料庫生命週期**（`tests/unit/test_music_db.py`）

| # | 項目 | 通過標準 |
|---|---|---|
| D1 | 第一次啟動 | `tmp_path` 裡沒有資料夾也沒有檔案 → `ensure_schema` 後檔案存在；`PRAGMA table_info` 和 §4.4 的 schema 逐欄一致；`alembic_version` 是 head；`user_version` = 1 |
| D2 | 已經是 head | 再跑一次：不產生備份、資料完全不變（列數與內容比對） |
| D3 | 升級前備份 | 建一個 head 之前的假 revision 並寫入 10 筆事件 → 升級後 `music.sqlite3.bak-{舊版本}` 存在，內容和升級前相同；新檔的 10 筆事件都還在 |
| D4 | 升級失敗 | 用一個會丟例外的假 revision → 原檔的位元組和升級前完全相同；`play_media` 照常回 ✅ 但不寫事件；回報裡有 ⚠️ |
| D5 | 資料庫損毀 | 檔案內容是亂碼 → 不刪除、不覆蓋，改名成 `.corrupt-{時間}` 後建立新資料庫，並回報 ⚠️ |
| D6 | 執行期不載入 Alembic | 在新的子程序裡 import `fast_path`、`local_actions`、`music_prefs`，並跑一次假的 `play_media` → `sys.modules` 裡沒有 `alembic` 和 `sqlalchemy` |
| D7 | 背景模式 | 把 `ensure_schema` 換成會卡 1 秒的假物件並開啟背景模式 → 啟動流程不被阻塞；這段時間內 `play_media` 回 ✅ 但不寫事件；完成後再點歌會寫事件 |
| D8 | 從事件重算 | 給一組事件 → `rebuild` 算出的 `terms` 和逐步即時更新的結果完全相同 |

**Y：搜尋、路由、退回、開啟**（`tests/unit/test_youtube_client.py`、`tests/flow/test_route_music.py`）

| # | 情境 | 通過標準 |
|---|---|---|
| Y1 | 一般搜尋解析（fixture） | 取到前 5 筆的 id、標題、頻道，和 fixture 逐筆相同；Shorts、廣告、播放清單、頻道項目不算在 5 筆裡 |
| Y2 | YouTube Music 解析（fixture） | 第一筆的類型、videoId、歌名、歌手和 fixture 相同；歌手從 `MUSIC_PAGE_TYPE_ARTIST` 的 run 取（含多位歌手、歌手名稱裡有「•」的 fixture）；只說歌手的 fixture（「周杰倫」）解析出類型是歌手、不是歌曲 |
| Y3 | 接受規則：要接受 | 晴天、周杰倫的晴天、晴天 周杰倫、稻香、Shape of You、告白氣球、到香（拼音）、告白汽球（拼音）、周杰論的晴天（剩下的字用拼音對到歌手）：全部接受 |
| Y4 | 接受規則：要拒絕 | 第一筆是 karaoke 版（Sunfly 這類）、類型是影片或播放清單、類型是歌手（只說歌手）、歌名不在 query 裡（「睡眠音樂」回一首不相關的歌）、剩下的字對不到歌手（「五月天的晴天」回周杰倫的晴天）、情境句（適合下雨天、讀書用的輕音樂）：全部拒絕並播一般搜尋第一筆，`source=web` |
| Y5 | 挑 MV | ① 前 5 筆有官方頻道的 MV 排第 3 → 選它，`source=mv`；② 沒有官方頻道，第 2 筆標題含歌名與歌手 → 選它；③ 只有歌詞版和伴奏版 → 播 YouTube Music 的 ID，`source=ytm`；④ 一般搜尋失敗、YouTube Music 接受 → 播 YouTube Music 的 ID |
| Y6 | 並行與截止時間 | 兩邊各用會卡住的假 `urlopen`：一般搜尋卡住 → YouTube Music 接受時播它的 ID；YouTube Music 卡住 → 播一般搜尋第一筆；兩邊都卡住 → 進 API。每種情況從開始到決定的時間 ≤ `YT_TIMEOUT_S` ＋ 0.1 s；`urlopen` 收到的 timeout 都 ≤ `YT_TIMEOUT_S` |
| Y7 | InnerTube 兩邊失敗 → API | HTTP 400、500、逾時、JSON 壞掉、0 筆，各自進 API；`source=api`；標題含 `&amp;`、`&#39;` 時寫進資料庫和回傳字串前已還原（**已刪除，M22**） |
| Y8 | API 也失敗 | 403 `quotaExceeded`、401、500、逾時、0 筆：`source=page`；開 `search_url`；回傳 ⚠️；事件記錄 HTTP 狀態碼的分類（**改為**：InnerTube 兩邊都失敗 → `source=page`、開搜尋頁、回 ⚠️，M22） |
| Y9 | 沒金鑰 | InnerTube 兩邊都失敗時不打 API（`urlopen` 不會收到 googleapis 的網址），直接 `source=page`（**改為**：任何情況都不讀金鑰、不打 googleapis，M22） |
| Y10 | `clientVersion` | 首頁 fixture 解析出版本並寫進 `maintenance`；格式不符（空字串、含引號、太長）的不寫；InnerTube 回 4xx 時設「需要更新」旗標，而且每個端點的 `urlopen` 只被呼叫 1 次（沒有當場重試）；下一次請求使用 `maintenance` 裡的新版本 |
| Y11 | videoId 不合法 | `abc"; do shell script "x`、10 個字元、12 個字元：一律丟棄並換下一筆；osascript 和 `open` 的參數裡不會出現這些字串；InnerTube、YouTube Music、API 三種來源都測 |
| Y12 | 快取命中 | 第二次說同一句時，`urlopen` 呼叫 0 次，`source=cache`；超過 30 天沒用過的未釘選快取會重新查詢；`pinned` 的不會過期 |
| Y13 | 重複使用分頁 | 假 osascript 回報「有音樂分頁」→ 送出的是改網址的指令，沒有 `open` 新分頁；回報「沒有」→ 開新分頁；只呼叫 1 次 osascript 完成找＋改；osascript 都帶逾時；腳本本身對 0 個分頁的視窗（Safari 幽靈視窗，P0-8）不報錯、視為「沒有」（P0-8 已用真實 Safari 確認 `URL of every tab of every window` 回空字串、rc=0） |
| Y14 | 金鑰與隱私 | 金鑰設成一個獨特字串，跑完 Y1～Y9：在 log 檔、所有例外訊息、回傳字串、`events` 表、`os.environ`、傳給 subprocess 的 env、InnerTube 的請求內容裡都出現 0 次；InnerTube 請求不帶 cookie |
| Y15 | 特殊字元 | 標題含 `'`、`"`、`;`、`--`、emoji 時，事件和 `videos` 都正確寫入並讀回（驗證 SQL 是參數化的） |
| Y16 | 搜尋執行緒不碰資料庫 | 記錄每次 `sqlite3.connect` 與寫入所在的執行緒：全部是呼叫 `play_media` 的那個執行緒 |

**S：計分與偏好**（`tests/unit/test_music_prefs.py`）

| # | 項目 | 通過標準 |
|---|---|---|
| S1 | 衰減 | 一筆 30 天前的點播，分數＝0.5（誤差 < 1e-9）；60 天前＝0.25 |
| S2 | 喜歡 | `like` 讓分數 +3 |
| S3 | 不要再放 | `dislike` 的影片永遠不會被常聽的歌選到（固定亂數種子跑 1,000 次） |
| S4 | 快速跳過 | 播放後 29 秒說「下一首」扣 0.3；31 秒不扣 |
| S5 | 常聽的歌 | 只從前 20 名選；最近 10 首不會被選到；固定亂數種子時結果固定；`urlopen` 呼叫 0 次 |
| S6 | 空資料庫 | 播常聽的歌回 ⚠️ 和固定的提示文字 |
| S7 | 永久保存 | 定時整理之後，`events` 的列數不變 |
| S8 | 我最常聽什麼 | 回傳前 5 名，順序和手算的分數一致 |
| S9 | 一個寫入者 | 所有寫入都發生在呼叫端的執行緒（用假的 worker 驗證）；定時整理是排進佇列，不是另開執行緒寫入 |
| S10 | 定時整理排程 | 用注入的時鐘：啟動時跑一次；過 6 小時又跑一次；5 小時 59 分時不會跑 |

**T：字詞矯正**（`tests/unit/test_music_prefs.py -k correct`、`tests/runtime/test_stt_client.py`）

先準備好 terms：周杰倫、稻香、晴天、告白氣球、光年之外、鄧紫棋都播過 3 次；七里香只播過 1 次；「情天」和「晴天」同時存在的情況另外準備一份。

| # | 輸入 | 預期 |
|---|---|---|
| T1 | 到香、周杰論、情天（terms 裡沒有情天時）、告白汽球、光念之外、鄧子棋、走杰倫（zh→z 放寬） | 依序改寫成 稻香、周杰倫、晴天、告白氣球、光年之外、鄧紫棋、周杰倫，7/7 |
| T2 | 周杰論的到香 | 周杰倫的稻香（分段各自矯正） |
| T3 | 起里香（七里香只播過 1 次） | 不改 |
| T4 | 青天（terms 裡同時有晴天和情天，拼音對到兩個） | 不改 |
| T5 | 情天（本身就在 terms 裡） | 不改 |
| T6 | 晴天娃娃、Jay Chou、lofi | 不改（只比整段完全相等的拼音） |
| T7 | 每次改寫 | 寫一筆 `correct` 事件，`query` 和 `corrected` 正確 |
| T8 | STT 提示詞不受影響 | 跑完 T1～T7 與一次定時整理後，`SttClient` 送出的 `config.hints` 和 `vocab/stt_hints.txt` 逐條相同；`SttClient` 沒有被要求重連 |

**K：播放控制**（`tests/unit/test_native.py`、`tests/flow/test_route_music.py`）

| # | 情境 | 通過標準 |
|---|---|---|
| K1 | 「下一首」「上一首」「暫停」「繼續」的路由 | Fast Path 命中，LLM 呼叫 0 次 |
| K2 | 前景是 Music 或 Spotify | 走 App 指令；不送媒體鍵 |
| K3 | 音樂分頁是所在視窗目前的分頁、假的輔助使用 API 回 `ok` | 按的是**那個視窗**的按鈕；比對用的是 §4.3 的錨點（`下一個 (SHIFT+n)`、`Next (SHIFT+n)` 都命中，「前往下一頁」不命中）；不送媒體鍵；`source=ax` |
| K4 | 假的輔助使用 API 回 `missing` | 送一次對應的媒體鍵；`source=mediakey` |
| K5 | 沒有音樂分頁，而且媒體鍵失敗 | 回 ⚠️「找不到正在播放的音樂」 |
| K6 | `post_media_key` 的白名單 | 只接受 play、next、previous；其他值（包含音量、任意整數）一律拒絕，而且假的事件送出函式收到 0 次呼叫 |
| K7 | 換一首 | 改播候選清單的第 2 筆；`queries` 改指向它並設 `pinned=1`；再換一次播第 3 筆；候選都播完回 ⚠️ |
| K8 | 喜歡、不要再放 | 讀分頁網址取得 videoId 並寫事件；讀不到網址時回 ⚠️，不寫事件 |
| K9 | `play_pause` 的方向（使用者 2026-11-21 決定 U3，取代原本的「行為不變」） | 前景是 Music／Spotify／YouTube 分頁時：「暫停」「繼續」依方向下指令（Music／Spotify 用 `pause`／`play`，YouTube 只按暫停或播放鈕），已是目標狀態回 ✅ 不動作；單說「播放」「播放暫停」仍是切換；`test_route_context.py` 既有案例依此更新後全過 |

### 7.2 真實環境（P6 一次；門檻已確認 M11）

API 用量：目前累計 **2,000 units**（P0-1），你定的提醒線是 **8,000 units**。下列項目預計再用 500 units（只有 R5），跑之前我會先報目前累計數字。

| # | 項目 | 做法 | 門檻 | 失敗時 |
|---|---|---|---|---|
| R1 | 沒有快取的延遲 | 20 個不重複的 query（清空快取），逐一 `python main.py --text 我想聽X`，取 `commands.jsonl` 的 `total_ms` | p50 ≤ 1.0 s、p95 ≤ 2.0 s | 回報數字與各階段耗時（InnerTube 兩邊、osascript），討論調逾時或預先抓取 |
| R2 | 有快取的延遲 | 同樣 20 句再跑一次 | p50 ≤ 0.3 s；網路請求 0 次 | 看找分頁的耗時（P0-3） |
| R3 | 播到想要的歌 | 30 句：20 首指定歌曲（中文 15、英文 5）、5 位歌手、5 個情境；判斷的是**最後實際播放的那支**，同時記錄每句的 `source`（mv／ytm／web） | 指定歌曲 ≥ 18/20 是那首歌（官方 MV 或官方音源；歌詞版、翻唱、伴奏不算）；歌手與情境由你判斷是否合理 | 依失敗句調整接受規則或挑 MV 的條件，補進 Y3～Y5 的語料 |
| R4 | API 不被用到 | R1～R3 跑完後查 `events` | `source=api` 的次數是 0；GCP 用量頁面這段期間沒有新增 | 查 InnerTube 失敗原因（M22 之後 API 已不存在，本項只留歷史結果） |
| R5 | 退回 | ① 暫時把 `config` 的兩個 InnerTube 網址改成無效位址（**不提交**，跑完還原並用 `git diff` 確認乾淨），跑 5 句；② 你把 `.env` 的金鑰拿掉（程式不改 `.env`），再跑 5 句 | ① 5/5 是 `source=api` 並開始播放；② 5/5 是 `source=page`、開搜尋頁並回 ⚠️ | 修退回（M22 之後只剩 ②：InnerTube 失敗 → 搜尋頁） |
| R6 | 啟動時間 | `main.py --no-stt` 啟動 5 次，比較有沒有 `ensure_schema` 的差值 | 中位數增加 ≤ 300 ms；超過就確認已經改成背景模式 | 依 M14 改背景 |
| R7 | Fast Path 比對耗時 | C1 加 C2 全部語料跑 `match()` 各 100 次 | 單句平均 ≤ 1 ms（和既有規則同一個量級） | 簡化 regex |

### 7.3 人工（由你確認，我只回報觀察到的事實）

**操作 Safari 的規則**（P0 期間我誤關過你的視窗，之後一律照做）：
- 實測腳本只操作自己用 `make new document` 建立、**從回傳值取得參照**的視窗；不用 `front window` 或「最前面的視窗」判斷是不是自己開的。
- 結束時只關這些視窗；你原本的視窗與分頁不讀網址以外的內容、不關、不改。
- **關窗後 `windows` 清單可能還留一個幽靈項目**（P0-8 實測：`close` 成功、System Events 視窗數 0、`documents` 0，但 `count of windows` 仍是 1、該視窗 0 個分頁）。判斷有沒有關掉要看 `count of documents` 或 System Events，不要看 `windows`。
- 需要你的 Safari 狀態（例如 H4）時，先告訴你要做什麼，等你同意再做。

| # | 項目 | 操作 | 通過標準 |
|---|---|---|---|
| H1 | 有聲自動播放 | `main.py --text 我想聽晴天`，換 3 首 | 3/3 在 3 秒內有聲音 |
| H2 | 分頁重複使用 | 連續點 3 首不同的歌 | Safari 裡只有 1 個音樂分頁；同時只有 1 首在播 |
| H3 | 背景控制 | 前景是 VS Code，音樂分頁在背景視窗：說「下一首」「上一首」「暫停」「繼續」 | 4/4 作用在音樂分頁；Music App 沒有被啟動 |
| H4 | 分頁不在最上層 | ① 音樂分頁所在的視窗切到別的分頁，再說「下一首」；② 關掉 Safari 所有 YouTube 影片分頁（Music 不要在播）後再說「下一首」 | ① 媒體鍵作用在 YouTube（Safari 任何視窗任何分頁有 YouTube 影片分頁就會送，使用者 2026-11-21 決定 U2）；② 回 ⚠️「找不到正在播放的音樂」，不送媒體鍵、Music 沒有被啟動 |
| H5 | STT 實機 | 你對 10 首常聽的歌各說一次「我想聽 X」 | 記錄辨識原文、矯正結果、`source`、是否播對；**不設通過門檻，也不聲稱已驗證**，數字當作下一輪調整的依據 |

### 7.4 不在本次範圍

- Spotify、Apple Music 的搜尋播放；在 music.youtube.com 播放（只用它的搜尋結果，播放一律在 youtube.com）
- YouTube Music 的歌手頁、專輯頁（只說歌手時播一般搜尋第一筆，§3）
- 動態 STT 提示詞（M12）
- 解析 YouTube 搜尋頁 HTML（M8）
- Google「好手氣」、DuckDuckGo 轉址、Invidious／Piped（§9：被擋、要 JavaScript 或不穩）
- 第三方搜尋套件 `ytmusicapi`、`yt-dlp` 等（M16）；Google Takeout 匯入
- TTS
- 禮貌問句（「可以放…嗎」）
- 單獨的「聽 X」
- 把 `play_media` 開放給 LLM
- 偵測滑鼠跳歌
- 桌面 UI 的統計畫面
- 手動編輯詞表的指令或介面
- 資料庫加密
- 跨裝置同步
- Safari 以外瀏覽器的人工驗收（Chrome 的程式路徑只做離線測試）

## 8. 風險與假設

- **主要路徑是非官方介面**：InnerTube 沒有公開規格，YouTube 改版、改請求格式或擋自動化請求時會失效。對策：退回 Data API（Y7）、再退回搜尋頁（Y8）；離線 fixture 測試在改版時會先看到解析失敗。
- **服務條款**：以程式呼叫 InnerTube 可能不符合 YouTube 的服務條款，**我沒有逐條查證**。目前的用法是個人、本機、低頻率（一句話一次搜尋，有快取），不下載影片、不繞過廣告。介意的話可以改回「Data API 為主」，只動 §4.2 的順序。
- **`clientVersion` 過舊**：預設值會隨時間變舊；定時整理每 6 小時更新一次（M17）。更新失敗時仍有 API 與搜尋頁可退。
- **YouTube Music 誤接受**：接受規則要求類型是歌曲、歌名在 query 裡、剩下的字對到歌手（Y3、Y4）；仍可能接受到同名的另一首歌。用 R3 量；說「換一首」可以糾正並釘選。
- **YouTube Music 歌曲 ID 在 youtube.com 的表現**：P0-8 實測 3/3 能播、合輯 3/3 接得下去。
- **API 配額**：只在 InnerTube 失敗時用，一天約 100 次；用完就開搜尋頁。
- **新依賴**：`alembic`（連帶 `SQLAlchemy`、`Mako`），只在啟動時載入（D6），之後可以移除（§4.4）。
- **永久保存**：資料庫只會變大（估計一年約 3.6 MB）；升級前會備份（D3），損毀時保留原檔（D5）。
- **媒體鍵**：可能被別的「正在播放」來源接走（P0-5 沒測 Music 正在播的情況；H4）。對策：Safari 沒有 YouTube 影片分頁就不送（M19）。
- **合輯網址** `list=RD{id}` 是 YouTube 現有行為，不是公開規格。
- **同音異字**：使用者真的想聽同音的另一首歌時，可能被改寫。用 §4.5 的三個條件壓低機率（T3～T6）；說「換一首」可以糾正並釘選。
- **端點偵測**：說歌名時停頓可能被提早收句（C3、H5 觀察）。
- **行為改變**：前景不是媒體時說「暫停」，會改成控制背景的音樂（§4.3）。

## 9. 實作結果

### P0 量測（2026-11-21）

腳本與暫存 venv 都在 scratchpad，沒有改產品程式碼。

| # | 狀態 | 結果 |
|---|---|---|
| P0-1 API 延遲 | **完成，通過** | 20 個不重複 query（中英文歌名、歌手、情境）全部成功：**p50 304 ms、p95 477 ms**（最小 218、最大 477，最慢是 `Lo-fi hip hop`）。每次回 5 筆。**API 累計用量：20 次 × 100 = 2,000 units**（上限依使用者要求為 8,000 units，接近時要提醒）。發現：① 標題含 HTML 編碼（`&amp;`、`&#39;`），存進資料庫前要先還原；② 「晴天 周杰倫」「告白氣球」「十年」的第一筆是第三方**歌詞版**，不是官方 MV |
| P0-2 Safari 有聲自動播放 | **完成，通過** | 用 `open -a Safari` 依序開 3 首合輯（稻香、七里香、夜曲）：播放器按鈕分別在 2.8、2.0、2.1 秒變成「暫停 (k)」（＝播放中）；使用者確認 **3/3 有聲音**。**不需要改 Safari 的自動播放設定**。按鈕在播放中叫「暫停 (k)」，暫停後叫「播放鍵盤快速鍵k」 |
| P0-3 找分頁並改網址 | **完成，勉強通過** | osascript 本身啟動 0.04～0.06 s；一次呼叫 `URL of every tab of every window` 0.15～0.18 s；逐個分頁迴圈 0.31～0.36 s；JXA 0.16～0.21 s；預先編譯成 `.scpt` 反而更慢。**「找＋改網址」寫在同一支 osascript：10 次 p50 197 ms、最小 182、最大 268 ms**（門檻 p50 ≤ 200 ms，餘裕只有 3 ms）。改成「記住上次的視窗 ID 與分頁位置、只檢查那一個分頁」也是 p50 184 ms，差不多，所以**耗時主要是 Safari 處理『改網址』本身**，不值得多做快取。→ R2（快取命中 p50 ≤ 0.3 s）有約 100 ms 餘裕 |
| P0-4 背景視窗按按鈕 | **完成，通過（有限制）** | 音樂在獨立的 Safari 視窗、前景是 VS Code：輔助使用 API 按「暫停」「播放」「下一首」「上一首」**全部有作用**（前景仍是 VS Code，沒有搶焦點）；單次按下 30～65 ms（第一次含找按鈕 266 ms），掃描整個視窗 511 個節點約 71 ms。**實際按鈕名稱**：`暫停 (k)`／`播放鍵盤快速鍵k`、`上一部 (SHIFT+p)`、`下一個 (SHIFT+n)`。→ 比對改用 `\(SHIFT\+n\)$`、`\(SHIFT\+p\)$`；Safari 自己的「返回上一頁」「前往下一頁」不會誤中。**限制**：① Safari 的 `AXWindows` 是空的，只能透過 `AXFocusedWindow`／`AXMainWindow` 取得 Safari 最近使用的視窗；② 音樂分頁不是它所在視窗的目前分頁時，AX 看不到播放鈕 → 這時只能靠媒體鍵 |
| P0-5 媒體鍵 | **完成，通過（部分情境未測）** | 用 ctypes 呼叫 `NSEvent`＋`CGEventPost` 送系統媒體鍵（**不需要新增依賴**）。① Safari 播 YouTube、Music 沒開、前景 VS Code：play／next／previous **全部作用在 Safari**；② Music 以 `open -g` 開著但停止狀態：play 暫停了 Safari、再送一次恢復，**Music 維持 stopped**；③ 音樂分頁在背景（不是目前分頁）：媒體鍵仍然能暫停與恢復。**沒有測**：Music 載入了歌曲後暫停、或 Music 正在播放的同時 Safari 也在播 |
| P0-6 STT 提示詞多久生效 | **完成（讀程式）** | `hints` 只在 WS 連線建立時送一次（`client.py:71`）；server 的 session 輪替沿用同一份 config，不會重讀；client 只在斷線時重連 → 動態提示詞必須主動重連才會生效。**使用者決定不做動態提示詞（M12）**，所以重連耗時不用量 |
| P0-7 Alembic 成本 | **完成，通過但餘裕小** | 暫存 venv 安裝 alembic 1.20.1／SQLAlchemy 2.1.9／Mako 1.4.3。最小專案、一個 migration，5 次：`import` 中位數 **201 ms**；已是 head 的 `upgrade` 1～2 ms；第一次建立 10～22 ms。**另有一次冷啟動 5,055 ms**（安裝後第一次 import，推測是第一次編譯 bytecode；沒有另外驗證原因）。→ 穩定狀態通過 ≤ 300 ms，但只有約 100 ms 餘裕，而且沒有把 `main.py` 其他 import 一起量 → 背景模式定為預設（M14、D7） |
| P0-8 YouTube Music 歌曲 ID 播放 | **完成，通過（有聲待你確認）** | 先用 InnerTube 對 10 個 query 取 YouTube Music 第一筆：9 筆是「歌曲」且 `musicVideoType`＝`ATV`（官方音源），1 筆是「影片」（`晴天 周杰倫` 第一筆是歌詞影片，`UGC`）；**9 筆歌曲的 ID 全部不在一般搜尋前 5 筆裡**（0/9），所以「挑不到 MV 就播 YouTube Music ID」這條路會常被用到。實測挑 3 首（稻香 `l6a5D6yxqEU`、夜曲 `Ii46Q7i4uuk`、Shape of You `xTvyyoF_LZY`），照 §7.3 的規則（Safari 當時沒有任何視窗，新視窗用「開之前沒有、開之後多出的 id」確認恰好 1 個）：① 網址 `watch?v={YTM id}&list=RD{YTM id}` **3/3 載入並進入播放**（播放鈕變「暫停 (k)」：1.8、2.5、1.8 s），分頁標題是正確的歌名（`稻香`、`夜曲`、`Shape of You`）；② 合輯**3/3 有接下去**：用 AX 按 `下一個 (SHIFT+n)` 後影片 ID 換掉（→ `Pi5xzNsmmZU` 聽媽媽的話、`VwI4LwlPO3k` 夜的第七章、`J7p4bzqLvCw` Blinding Lights），換歌後仍在播放；③ 第 2、3 首是「改同一個分頁的網址」（產品的分頁重複使用做法），不用開新視窗。**有沒有聲音我無法客觀判斷**：播放鈕狀態是播放中，但要由你確認（你剛才有沒有聽到） |

### 搜尋替代方案實測（2026-11-21）

自寫 `urllib` 腳本，20 個 P0-1 同一批 query；第三方套件只安裝在暫存 venv、**沒有執行**（自動模式不允許執行剛下載的套件），所以改用自寫請求打同樣的端點。

| 方案 | 成功 | 延遲 | 回應大小 | 準確度 | 結論 |
|---|---|---|---|---|---|
| InnerTube 一般搜尋（`WEB`） | 20/20 | p50 482 ms、p95 703 ms | 平均 339 KB | 第一筆和 API 第一筆相同 14/20；API 第一筆在它前 5 筆內 19/20；第一筆是歌詞版 **0/20** | **採用**（主要） |
| InnerTube YouTube Music（`WEB_REMIX`） | 20/20 | p50 463 ms | — | 見下方 route_bench | **採用**（路由與挑 MV） |
| 解析搜尋頁 HTML | 18/20（2 句解析不到） | p50 690 ms | 1,428 KB | 和 InnerTube 一般搜尋相同來源 | 不採用（M8） |
| Data API v3 | 20/20 | p50 304 ms、p95 477 ms | 小 | 第一筆是第三方歌詞版 3/20 | **退回用**（要金鑰、扣配額） |
| Google「好手氣」 | — | — | — | curl 直接回 429＋驗證碼；轉址參數裡的目標有 4/5 和 API 第一筆相同 | 不採用（會被擋） |
| DuckDuckGo `!ducky` | — | — | — | 要執行 JavaScript 才會轉址；瀏覽器內的測試因我的腳本錯誤作廢 | 不採用 |
| Invidious／Piped 公開站台 | 不穩 | — | — | — | 不採用（第三方站台，可用性不穩） |

**route_bench**（31 句，兩邊並行，套用 §4.2 的接受規則雛形；並行總耗時 **p50 520 ms、p95 811 ms**）：

| 類別 | 結果 |
|---|---|
| 指定歌名（16 句） | YouTube Music 接受 15 句，全部是對的歌；1 句第一筆是 karaoke 版，正確拒絕 |
| 只說歌手（5 句） | 當時的解析用「•」切歌手，有錯，全部走一般搜尋；第一筆 3/5 是該歌手本人的作品 → §4.2 改成從 `pageType` 取歌手（Y2） |
| 情境、模糊（7 句） | 全部走一般搜尋，第一筆都和情境相關 |
| 錯字（3 句） | YouTube Music 3 句都回對的歌；但雛形只用字面比對，「到香」「告白汽球」被拒絕 → §4.2 加入拼音比對（Y3）。**加了拼音之後的結果沒有重跑**，P3 完成後由 R3 量 |

**P0 對計畫的影響**

1. **動態提示詞**（P0-6）：使用者選做法 B，只做拼音矯正，不改 STT（M12、T8）。
2. **Alembic**（P0-7）：**一開始就用背景模式**（D7 的做法，使用者 2026-11-21 同意）。
3. **播放控制（§4.3）**：M13 的「先 AX、後媒體鍵」可行；按鈕比對用 `(SHIFT+n)`／`(SHIFT+p)`（K3）。
4. **找分頁（§4.2、R2）**：osascript 一次呼叫完成「找＋改網址」，p50 約 190 ms。
5. **搜尋**：InnerTube 為主、Data API 退回（M3、M8），YouTube Music 路由＋挑 MV（M15）；標題 `html.unescape` 只在 API 路徑需要（Y7）。YouTube Music 的歌曲 ID（ATV）在 youtube.com 可以播放、合輯會接下去（P0-8），所以 M15 的第三步成立；但 ATV 的 ID 從來不在一般搜尋前 5 筆（0/9），挑 MV 失敗時會直接播官方音源。

### P1～P5 實作結果（2026-11-21）

離線測試全部在 `tmp_path` 與假物件上跑，沒有連網、沒有執行 `open`／`osascript`、沒有寫專案的 `data/`。`pytest`（離線全部）**2473 passed、6 skipped、0 failed**（119 deselected，約 21 秒）；`cd desktop && node --test` 266 pass、0 fail（P6 文件完成時重跑確認）。

| 步驟 | 完成條件 | 結果 |
|---|---|---|
| P1 規則、守門、完整度 | C1～C5；`pytest -m "unit or flow"` 0 failed | **達成**。C1～C5 全過；R7 的單句比對平均 **0.006 ms**（門檻 ≤ 1 ms，已先量，不必等 P6）。`play_media` 一開始回 ⏭️ 佔位，P3 換成真的 |
| P2 資料庫 | D1～D8 | **達成**。D1～D8 全過；`ensure_schema`（含 `import alembic`）中位數約 **150 ms**（P0-7 最小專案量到 201 ms） |
| P3 搜尋、快取、開分頁 | Y1～Y16；`pytest -m "unit or flow"` 0 failed | **達成**。Y1～Y16 全過；fixture **55 個檔、419 KB**（`tests/fixtures/youtube/`）。用真實 fixture 跑決策的結果：晴天、周杰倫的晴天、晴天 周杰倫、稻香、Shape of You、告白氣球與三個錯字案例（到香、告白汽球、周杰論的晴天）都接受並挑到 `mv`；只說歌手、情境句、歌名不在 query 裡、剩下的字對不到歌手（「五月天的晴天」）都拒絕並播一般搜尋第一筆；「月亮代表我的心」YouTube Music 第一筆是五月天阿信版，被接受，**播到五月天阿信版**（query 沒有歌手，分不出版本）。**已知風險：YouTube Music 回中文歌手名時，英文歌手說法（例如「Jay Chou 晴天」）對不到歌手而被拒**，改播一般搜尋第一筆（用真實 fixture 確認過「Jay Chou 晴天」被拒、「周杰倫 晴天」被接受）；R3 要觀察 |
| P4 偏好與矯正 | S1～S10、T1～T8 | **達成**。S1～S10、T1～T8 全過（T8 在 `tests/runtime/test_stt_client.py`） |
| P5 播放控制與回饋 | K1～K9；`pytest tests/unit/test_native.py tests/flow/test_route_context.py` 0 failed | **達成**。K1～K9 全過（K9 依 U3 改寫，見 §2 M20）；兩個指定測試檔 0 failed |

**P1～P5 實作時，使用者 2026-11-21 的決定**（已改寫 §2、§4.3、§7.1、§7.3；ADR 0019 有記錄）：M18（U1）敘述句守門不變嚴、M19（U2）媒體鍵只在 Safari 有 YouTube 影片分頁時才送、M20（U3）暫停／繼續有方向、M21（U4）`next_track`／`prev_track` 開放給雲端 `front_action`。

**架構師接受的偏離**（相對於第四版計畫書）：

1. 點歌規則比對**原句**（`canonicalize` 會吃掉「我想」「來」）；「我想聽 X」的 X 含另一個指令（幫我、打開…）不收；「我想聽說…」不收；播放控制詞多「列表、記錄、進度」；單說「我想聽」「來一首」等 `match` 回 `play_media("")`、`completeness` 為 INCOMPLETE。
2. Alembic 升級在**備份複本**上做，成功才 `os.replace`（失敗時原檔位元組不變）。
3. YouTube Music「第一筆」只看結果清單、略過頂端卡片；YouTube Music 不接受且一般搜尋失敗或 0 筆時也走 API（API 已由 M22 刪除，現在是開搜尋頁）；英文伴奏標記整字比對。
4. `videos` 對 `mv`／`ytm` 存 YouTube Music 的歌名與歌手（`rebuild` 需要；schema 未改）；`on_dislike` 會刪指向該影片的快取；常聽的歌在歌少時退回只排除最近一首。
5. 分頁重複使用只找 `youtube.com/watch` 且含 `list=RD` 的分頁；失敗分類存在 `fallback` 事件的 `source` 欄位；`events.utterance` 存正規化後的子句；`--text` 模式同步 `ensure_schema`（語音模式在背景）；定時整理用 `QueuedJob` 排進 `command_worker`，不另開寫入執行緒。
6. 換一首寫 `play` 事件 `source=replace`（不 `learn_play`）；喜歡／不要再放讀不到 `RD` 分頁時退而用目前的 YouTube 影片分頁；toggle 不寫控制事件。
7. 新增測試支援檔 `tests/support/youtube.py`、fixture `tests/fixtures/youtube/`（2026-11-21 抓取並縮小，只打 InnerTube）。
8. 依賴：alembic 1.20.1、SQLAlchemy 2.1.9、Mako 1.4.3（README 安裝行已加 `alembic`；venv 是 `uv` 管理，安裝用 `uv pip install`）。

**P6 文件**：已完成（ADR 0019、`AGENTS.md`、`ARCHITECTURE.md`、`docs/flows/main-flow.md`、`docs/guides/testing.md`、`docs/guides/coding-style.md`、`README.md`、`.env.example`）；L2 `pytest` 0 failed、`node --test` 0 fail 已確認。本計畫已於 2026-11-21 結案移到 `completed/`（使用者決定）；§7.2 見下方「§7.2、§7.3 驗收結果」，H1～H4 由使用者判定通過、H5 沒有做。

**尚未驗證**（沒有自動測試能證明，不要當成已驗證）：

- 輔助使用 API 在真實 Safari 按鈕、系統媒體鍵（`post_media_key` 只測白名單與假的送出函式）：H3、H4 由使用者判定通過，沒有實際執行
- R6 只量了主執行緒的部分：`import main` 各 6 次，HEAD 中位數約 172 ms、P1～P5 後約 177 ms（+5 ms）；`ensure_schema`（含 import alembic，約 150 ms）在語音模式是背景執行緒，不擋啟動。`main.py --no-stt` 整段啟動（含 mlx 服務）沒有量。`--text` 模式改成同步等 `ensure_schema`，每次 `--text` 多約 0.15～0.2 s（冷啟動可能數秒）
- §7.3 H5（STT 實機辨識歌名）；H1～H4 沒有實際執行（使用者判定通過）
- M23 改動後只用即時搜尋確認 5 位歌手的挑選（沒有開分頁實際播放）
- 拼音加入後的 route_bench 沒有重跑（沿用 §9 P0 的說明）

### §7.2、§7.3 驗收結果（2026-11-21）

量測腳本在 scratchpad（不進 repo）：每句一個程序，流程同 `main.py --text`（同步 `ensure_schema` 後 `process_voice_command`），記 `music` 各階段耗時與 `urlopen` 次數；R3 用 YouTube oEmbed 查實際播放影片的標題與頻道。資料庫從空的開始（`data/music.sqlite3`）。

| # | 結果 | 門檻 | 判定 |
|---|---|---|---|
| R1 沒快取 | 20 首：p50 946 ms、p95 1175 ms、最慢 1299 ms；每句 `urlopen` 2 次（InnerTube 兩邊） | p50 ≤ 1.0 s、p95 ≤ 2.0 s | 通過 |
| R2 有快取 | 每句一個程序：p50 308 ms、p95 338 ms，網路 0 次（每個新程序第一次開 ICU 拼音轉換器約 50 ms）；單一程序先 `warm()`（接近語音模式）：p50 275 ms、p95 310 ms，網路 0 次。時間主要在改分頁網址（約 230～320 ms） | p50 ≤ 0.3 s、網路 0 次 | 語音模式通過；`--text` 差 8 ms |
| R3 播到想要的歌 | 指定歌曲 20/20：官方 MV 13、Taylor Swift 官方頻道 1、Topic 官方音源 3、王菲官方頻道 1、歌手官方頻道的現場版 2（江南 JJ20、勇氣 20 週年；**使用者判定現場版算**）。情境 5 句都播到對應情境的長篇背景音樂 | 指定歌曲 ≥ 18/20；歌手與情境由使用者判斷 | 通過。歌手 5 句當時播一般搜尋第一筆，五月天播到阿信個人作品、陳奕迅播到第三方串燒 → 使用者決定 M23 |
| R4 API 不被用到 | 70 次播放 `source=api` 0、`fallback` 0、`ok=0` 0 | 0 次 | 通過（GCP 用量頁使用者沒有回報；M22 之後已無 API） |
| R5 退回 | ②（InnerTube 兩個網址在量測程序內換成無效位址、沒有金鑰）：5/5 `source=page`、開搜尋頁、`fallback` 事件記 `web=network;ytm=network`，每句 126～277 ms。① 沒跑（`.env` 沒有金鑰），之後 M22 刪除 API 退回，① 不再適用 | ② 5/5 | 通過 |
| H1～H4 | 使用者 2026-11-21 判定通過，**沒有實際執行** | — | 使用者判定 |
| H5 | 未進行 | 不設門檻 | — |

R1～R3 期間 50 次點歌只在第一次開新分頁，之後都改同一個分頁的網址（Safari 只有 1 個合輯分頁），這是 H2 的觀察，不是 H2 的驗收。

**M23 改動後即時搜尋（只搜尋、沒開分頁）**：周杰倫 → 女兒殿下、五月天 → 派對動物＋離開地球表面（live）、鄧紫棋 → 任天堂、Taylor Swift → Love Story (Taylor's Version)、陳奕迅 → 天公地道，全部 `source=ytm`；對照組「晴天」仍是 `mv`、「適合下雨天」仍是 `web`。
