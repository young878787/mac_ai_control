# 0019 點歌與播放控制：Fast Path 規則、InnerTube 搜尋、本機 SQLite、媒體鍵

- 狀態：採用
- 日期：2026-11-21
- 來源：[點歌實作計畫](../plans/completed/2026-11-21-music-play.md) §2 M1～M23、§9；使用者 2026-11-21 決定（M3、M10、M12、M14、M15、M22、M23、U1～U4）

## 背景

「我想聽周杰倫」原本會交給 LLM（約 1～3 秒），而且不一定會播；「暫停」「下一首」只對最前面的 App 有效。要的是常見點歌說法走 Fast Path，直接在 YouTube 播，並記住使用者的偏好。

## 決定

**點歌（`play_media`）**

1. 點歌說法在 Fast Path（`fast_path._r_music`、詞庫 `music`）命中，產生 `play_media(query)`；`query` 空字串＝播常聽的歌。比對只看原句（`canonicalize` 會吃掉「我想」「來」），不碰網路、資料庫、子程序，也不讀前景（紅線 1）。守門不放寬：否定、敘述、提問不走這條規則。「我想聽 X」的 X 含另一個指令（幫我、打開…）、「我想聽說…」、「播放」接控制詞（速度、清單、列表、設定、鍵、器、紀錄、記錄、次數、模式、音量、進度）都不收。單說「我想聽」「來一首」等回 `play_media("")`，`completeness` 判為不完整（等收句，仍是空的才播常聽的歌）。
2. `play_media`、`music_replace`、`music_feedback`、`music_top` 是本機工具（`LOCAL_TOOLS`），本機與雲端 LLM 都看不到；`SYSTEM_PROMPT`、`TOOLS_SCHEMA` 不動（0002、0011）。
3. 執行順序：拼音矯正 → 本機快取（30 天，`pinned` 不過期）→ InnerTube 兩邊並行 → 開 YouTube 搜尋頁並回 ⚠️。快取與 InnerTube 的結果直接播 `watch?v={id}&list=RD{id}`（合輯）；有音樂分頁就改它的網址，沒有才開新分頁。videoId 一律先過 `^[A-Za-z0-9_-]{11}$`，外部輸入不可信。模糊的要求（情境、歌手）直接拿去搜尋，不送 LLM。
4. **搜尋以 InnerTube 為主**（YouTube、YouTube Music 網頁本身用的內部介面，自寫 `urllib` 請求，不用 `ytmusicapi`、`yt-dlp`；不帶 cookie、金鑰或使用者資訊），不用金鑰、不扣配額。**不做 Data API 退回**（M22）：InnerTube 兩邊都沒有可播的就開搜尋頁。不做 HTML 解析（比 InnerTube 慢、回應大 4 倍，20 句裡 2 句解析不到）。`clientVersion` 預設值在 `config`，定時整理時從兩個首頁讀新值存進 `maintenance` 表；請求回 4xx 只標記「需要更新」，不當場重試。
5. **挑哪一支**：YouTube Music 第一筆（只看結果清單、略過頂端卡片）是歌曲、歌名與歌手沒有伴奏類標記（英文整字比對）、歌名對得上 query、剩下的字對到歌手時接受，再用它的歌名與歌手在一般搜尋前 5 筆挑官方 MV；挑不到播 YouTube Music 的歌曲 ID。**只說歌手**（YouTube Music 回歌手卡片）時播卡片底下第一首可播的歌（`source=ytm`，歌手本人的熱門歌曲；M23），卡片沒有歌才播一般搜尋第一筆。其餘不接受或 YouTube Music 失敗時播一般搜尋第一筆。
6. 只用在 YouTube 網頁；Spotify／Apple Music 的搜尋播放不做。

**金鑰**

7. 點歌不讀任何金鑰。計畫中的 `YOUTUBE_API_KEY`（Data API 退回）已刪除（M22），[0010](0010-cloud-key-handling.md) 不變。錯誤只記 `http_500`、`timeout` 這類分類，不記網址或回應內容。

**偏好資料庫（SQLite＋Alembic）**

8. （2026-11-23 補註：同一個資料庫也存天氣的常問縣市 `weather_queries`，migration `0002`，見 [0021](0021-weather-answer.md)。2026-11-25 補註：另有常駐資料 `profile`（居住地），migration `0004`，見 [0024](0024-home-city-and-answer-timeout.md)。）偏好存在 `data/music.sqlite3`（`MUSIC_DB_PATH`）：永久保存（`events` 只追加不刪）、不入版控、啟動時自動建立。執行期只用內建 `sqlite3`、WAL、只有 `command_worker` 一個寫入者；搜尋執行緒與 `clientVersion` 更新執行緒不碰資料庫；定時整理用 `QueuedJob` 排進指令佇列。
9. schema 由 Alembic 管理（`mac_ai/db/migrations/`，程式組設定，不放 `alembic.ini`；`render_as_batch`）。`ensure_schema` 只在函式內 import alembic／sqlalchemy，執行期程式不載入它們。版本落後時先用 `sqlite3` backup API 備份成 `music.sqlite3.bak-{舊版本}`，升級在備份複本上做，成功才 `os.replace`（失敗時原檔位元組不變，點歌照常但不寫紀錄並回 ⚠️）；檔案損毀改名成 `.corrupt-{時間}` 後重建。啟動時在背景執行緒準備（`MusicDb.start()`），準備好之前 `conn()` 回 None，點歌照常播放、只是不矯正、不查快取、不寫紀錄；`--text` 模式同步等它準備好。
10. 點歌字詞矯正只在執行階段用拼音（macOS ICU，ctypes）改寫 query，詞表在資料庫、動態增刪並定時整理（`MUSIC_MAINT_INTERVAL_S`＝6 小時）；**不改 STT 提示詞**（提示詞只在 WS 連線時送一次，要主動重連才會生效）。

**日後移除 Alembic 的步驟**

11. 每個 migration 的 `upgrade()` 最後都要執行 `PRAGMA user_version = {編號}`（`0001` 已做）。要移除時：① 把 `versions/` 裡的升級內容改寫成 `mac_ai/db/migrate.py` 的「`user_version` → SQL 清單」，`ensure_schema` 改成依 `user_version` 逐一執行（備份與原子換檔的流程不變）；② 刪除 `migrations/`、既有資料庫的 `alembic_version` 表，以及 README 安裝行的 `alembic`；③ 執行期程式（`MusicDb`、`music_prefs`）不需要改。

**播放控制與媒體鍵（紅線 8 的例外）**

12. 「下一首、上一首、暫停、繼續」走 `front_action`（詞庫 `app_shortcuts` 的 `play_pause`、`next_track`、`prev_track`，各有 `background`）。前景是 Music／Spotify／YouTube 分頁時照原本的綁定；否則改控制背景的音樂，依序：Safari 最近使用的視窗目前顯示的是 YouTube 影片分頁 → 輔助使用 API 按該視窗的播放器按鈕（`native.press_button_in_window`，`source=ax`）；Safari 任何視窗任何分頁有 YouTube 影片 → 送媒體鍵（`source=mediakey`）；否則回 ⚠️「找不到正在播放的音樂」。
13. **媒體鍵只允許 play／next／previous 三個**（`native.post_media_key` 白名單，ctypes 呼叫 `NSEvent`＋`CGEventPost`，不加依賴），其他值一律拒絕、不建立事件。媒體鍵會被系統交給「現在的播放中 App」，所以是最後手段：能用 AX 確認狀態時絕不送（媒體鍵的 play 是切換），而且 Safari 沒有 YouTube 影片分頁就不送（U2）。紅線 8 的「送按鍵只允許 ⌘ 組合鍵與翻頁鍵」其餘不變。
14. 「換一首」改播同一次搜尋的下一個候選（快取改指向並釘選）；「我喜歡這首」「不要再放這首」讀 Safari 音樂分頁目前的 videoId 寫事件，優先找含 `list=RD` 的 YouTube 分頁，讀不到才退而用目前的 YouTube 影片分頁；「我最常聽什麼」把前 5 名歌手與歌曲放進 `ctx.replies`（不呼叫 LLM）。

**使用者 2026-11-21 的決定（相對於計畫書第四版）**

- **U1 敘述句守門不變嚴**：「聽說明天會下雨」「他想聽周杰倫」「她說想聽晴天」不點歌，但允許交給 LLM（計畫 C2 對這三句不要求 G1）。0007 的 G1 不變。
- **U2** 媒體鍵只在 Safari 有 YouTube 影片分頁（任何視窗任何分頁）時才送，否則回 ⚠️。
- **U3** 前景是 Music／Spotify／YouTube 分頁時，暫停／繼續有方向：Music／Spotify 用 `pause`／`play` 指令，YouTube 只按暫停或播放鈕，已是目標狀態回 ✅ 不動作；單說「播放」「播放暫停」仍是切換。計畫 K9「行為不變」不再成立。
- **U4** `next_track`、`prev_track` 開放給雲端的 `front_action`（雲端 schema 的 id enum 多兩個；雲端不帶 direction，維持切換）。`play_media` 等其他本機工具仍對雲端不可見。

**實作時接受的偏離**（相對於計畫書；細節見計畫書 §9）

- 播放控制詞多「列表、記錄、進度」；「我想聽 X」的 X 含另一個指令不收（見決定 1）。
- `videos` 對 `mv`／`ytm` 存 YouTube Music 的歌名與歌手（`python -m mac_ai.db.rebuild` 需要；schema 未改）；`on_dislike` 會刪指向該影片的快取；常聽的歌在歌少時退回只排除最近一首。
- 分頁重複使用只找 `youtube.com/watch` 且含 `list=RD` 的分頁；失敗分類存在 `fallback` 事件的 `source` 欄位；`events.utterance` 存正規化後的子句。
- 換一首寫 `play` 事件 `source=replace`（不 `learn_play`）；toggle（沒有方向的播放）不寫控制事件。

## 後果

- 新依賴：`alembic`（連帶 SQLAlchemy、Mako），只在 `ensure_schema` 載入；依賴列在 README 安裝行。
- 「暫停」「下一首」在前景不是媒體 App 時，原本回報「目前的 App 不支援」，現在會控制 Safari 的背景音樂。
- InnerTube 是非官方介面，YouTube 改版或擋請求時會失效；退回搜尋頁與離線 fixture 測試（`tests/fixtures/youtube/`）是對策。是否符合 YouTube 服務條款沒有逐條查證，目前用法是個人、本機、低頻率（有快取）。
- 改 `youtube_client` 的接受規則或挑 MV 條件時，要先在 `tests/unit/test_youtube_client.py` 補真實 fixture 或衍生案例。
- 實機量測 R1～R5 已執行（見計畫書 §9）；人工驗收 H1～H4 由使用者判定通過、沒有實際執行；H5（STT 實機辨識歌名）尚未進行。計畫已於 2026-11-21 結案移到 `docs/plans/completed/`。
