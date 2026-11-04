# 多段意圖執行與 Fast Path 強化實作計劃書（定案版）

> 基礎文件：`docs/macOS 本地語音助手：雙軌架構與 Function Calling 設計指南.md`（以下稱「設計指南」）。本計劃書只寫**新增與變更**；沒提到的部分沿用設計指南。
>
> 草案 `docs/多段意圖執行與 Fast Path 強化計劃書.md` 保留不動，本文件依 2026-11-03 的決策獨立重寫，**以本文件為準**。
>
> 狀態：**已實作並驗證（2026-11-03）**。實測數據見設計指南第八章 I；本文件其餘章節保留為設計紀錄，實作與設計的差異見文末『實作結果與偏離』。第一章的現況數據是實作前實際跑出來的。
>
> 環境：M1 / 16GB、Python 3.12、`mlx-vlm 0.7.11`、模型 `LFM3-VL-2B-MLX-4bit`。

---

## 零、 已定案的決策

| # | 項目 | 決定 |
|---|---|---|
| D1 | 回覆輸出 | **這次不做回覆輸出通道**（不用 `say`、不做 UI）。需要回覆時，把原句**直接送入模型**（模擬模型收到使用者輸入），模型的輸出只記錄在回傳值 `replies` 和終端 log |
| D2 | 誤聽詞擴充 | 允許預先推測的詞（`seed`），但每個詞都要標來源（`observed`／`seed`）；另外記錄「STT 原文 → 正規化結果」，用來回收真實誤聽 |
| D3 | 深色模式 | `system_power_action` 的 enum 新增 `dark_mode_on`、`dark_mode_off`，保留 `toggle_dark_mode` |
| D4 | 專案清單 | 詞庫手動登錄，再加上自動掃描 `/Users/owo/projects` 第一層資料夾 |
| D5 | LLM 保護 | Fast 當作 LLM 前面的過濾層；LLM 失敗時先試 Fast 的候選結果，沒有候選就告知，而且**後面的步驟照常執行** |
| D6 | LLM 只回 `content`、沒呼叫工具 | 一樣觸發 chat call |
| D7 | `開 google` | **開 Google Chrome 程式**（`google` 成為 Google Chrome 的別名） |
| D8 | 讀畫面範圍 | 主螢幕截圖，再縮到 1280 寬 |
| D9 | 讀畫面的品質與延遲門檻 | 先量基線（C-V1、C-V2），看過模型實際輸出後再決定門檻；在那之前 C-V1、C-V2 只記錄，不判定通過與否 |
| D10 | 逾時與熔斷 | 規劃呼叫逾時 10 s、讀畫面 30 s、熔斷 30 s；實測後再調 |

---

## 一、 現況分析（2026-11-03 實測）

### 1. 基線
* 離線測試：`.venv/bin/python -m pytest -q` → **52 passed, 23 deselected**（0.73 s）。
* 量測當下 `mlx_vlm.server` **沒有啟動**，所以第 3 點的「LLM 未啟動」是真實情況，不是模擬出來的。

### 2. Fast Path 太嚴格：39 句口語實測，命中 12 句

測試方式：`normalize_utterance` → `split_clauses` → `FastPathMatcher.match`，`subprocess.run` 換成假物件。

| 類型 | 未命中的例子（全部掉到 LLM） | 原因 |
|---|---|---|
| 語尾助詞 | `打開chrome吧`、`取消靜音吧`、`靜音一下` | regex 錨定 `^…$`，結尾不能多字 |
| 前綴疊用 | `麻煩幫我開小算盤`、`嗯 打開 Chrome`、`那個打開Chrome好嗎` | 前綴只允許一個（`請|幫我|麻煩|給我`），沒有填充詞處理 |
| 語序 | `Chrome 打開`、`請幫我把 Chrome 打開`、`深色模式打開` | 只支援「動詞＋App」語序 |
| 量詞／插字 | `幫我打開一下 Chrome`、`幫我開個計算機` | 「一下」「個」只在少數位置被允許 |
| 重複 | `打開打開 Chrome` | 沒有去除疊詞 |
| 英文拼錯 | `開 chorme`、`開 Spotfy`、`開 VS扣` | 只做完全比對 |
| 中文數字 | `音量調到 五十`、`把音量調到百分之三十`、`音量調成五十趴` | `vol_regex` 只收阿拉伯數字 |
| 句型 | `音量 50`、`音量設成50%`、`聲音開到最大` | 動詞清單太少；沒有「最大／最小」 |
| 缺規則 | `開深色模式`、`切到深色模式`、`鎖定螢幕`、`鎖螢幕`、`把聲音關掉` | 深色模式不收單字「開」；沒有鎖定螢幕、亮度、搜尋規則 |
| 缺別名 | `開 google` | `google` 不是任何 App 的別名 |

**安全面（現況正確，修改後必須維持）**：`不要打開 Chrome`、`我昨天打開 Chrome`、`Chrome 是什麼` 目前都不會走 Fast Path。

### 3. LLM 服務沒啟動時沒有保護（已重現）

```text
輸入：開 Chrome，把螢幕調暗一點，把音量調到 30
⚡ Fast：開 Chrome → 已執行
🧠 Slow：把螢幕調暗一點 → URLError: Connection refused（14 ms）→ 例外往上拋，整個流程中斷
實際執行：[['open', '-a', 'Google Chrome']]   ← 排在後面的「音量 30」沒有執行
```

* `_chat` 和 `process_voice_command` 都沒有接例外。
* `timeout=60.0`：server 卡住時會等 60 秒。
* 沒有熔斷：一句話裡有多個 Slow 段時，每段都會重試一次。

### 4. 執行結果回報不實（已重現）

`open -a NoSuchAppXYZ` → `Unable to find application named 'NoSuchAppXYZ'`，**exit=1**，但 `control_mac_app` 一律回傳「✅ 已開啟」。`osascript` 也一樣不檢查。

### 5. 多段意圖：現有架構做不到的部分

| 語句 | 現在的切分與路由 | 缺什麼 |
|---|---|---|
| `開vscode，開mac_ai_control專案` | `F`、`S` | 沒有「開專案」工具和專案白名單 |
| `開google，搜尋台北天氣，辨識這頁` | `S S S` | `google` 不是別名；沒有讀畫面工具；沒有等網頁載入；沒有回覆流程 |
| `開 google 搜尋台北天氣 然後辨識這頁` | `開 google 搜尋台北天氣` 沒被切開 | 「開 X 搜尋 Y」要在 Fast Path 內拆成兩個動作 |
| `今天心情不錯`、`Chrome 是什麼` | 直接印出規劃呼叫的 `content` | 沒有「判斷要回覆 → 觸發 chat call」 |

### 6. 其他觀察
* `normalize_utterance` 只處理 4 個 App 誤聽詞。
* `build_hint_lines` 用子字串比對：「音量**設定**為 50」會把 `設定→System Settings` 塞進 prompt。
* 深色模式只有 toggle：已經是深色時說「打開深色模式」反而會切回淺色（由 D3 修正）。
* mlx_vlm.server 回應帶有 `timings`（`prompt_ms`、`predicted_ms`、`cache_n`、`prompt_n`、`predicted_n`），也支援 `image_url`（依 `mlx_vlm/server/schemas.py`、`openai.py` 原始碼確認，**尚未實際呼叫驗證**）。
* 依 `processor_config.json`，每 32×32 px 約 1 個影像 token：Retina 全螢幕約 5000 tokens，以約 420 tok/s 推算 prefill 約 12 s，所以必須先縮圖（推論值，需實測）。

---

## 二、 目標與範圍

| # | 目標 |
|---|---|
| G1 | 混亂輸入能正規化（全形、中文數字、簡體、填充詞、疊詞、誤聽） |
| G2 | Fast Path 放寬比對而且執行正確；否定、敘述、提問句**絕不**誤執行 |
| G3 | Fast Path 擴充：亮度、鎖定螢幕、深色模式開／關、搜尋、開專案、讀畫面、「開 X 搜尋 Y」 |
| G4 | 多段意圖依序執行，後面的步驟可以使用前面的結果（剛開的瀏覽器／編輯器、網頁已載入） |
| G5 | 判斷需要回覆時觸發 chat call：把原句直接送入模型，結果記錄在 `replies`（D1） |
| G6 | LLM 擴充工具（開專案、讀畫面、回覆），能正確判斷並執行 |
| G7 | LLM 未啟動、逾時或輸出壞掉時不中斷、會補救、會告知 |
| G8 | 每個階段都計時，並有測試量測 |

**不在本次範圍**：語音或 UI 回覆輸出（D1）、STT 麥克風端到端、`macos-stt` 串接、相對音量（設計指南第十章 2）、多輪對話記憶。

---

## 三、 新資料流

```text
文字輸入（STT 輸出；開發時直接用 CLI 參數送入，見第十章 S9）
  │
  ▼
[0. normalize_utterance]  NFKC → 空白 → 簡轉繁／大陸用語 → 誤聽詞 → 中文數字 → 疊詞
  │
  ▼
[1. split_clauses]        沿用；「開 X 搜尋 Y」在 Fast Path 內部拆成兩個動作
  │
  ▼
[2. Fast Path 前置過濾]   canonicalize_clause → 安全守門 → 意圖規則 → 槽位解析（完全比對 → 容錯比對）
  │   回傳 Match(actions, confidence ∈ {exact, fuzzy}, note)；被守門擋下或信心不足 → None（可附上「候選」）
  │
  ▼
[3. 分段]  F 段各自一步；連續未命中合併成一個 S 段（沿用）
  │
  ▼
[4. 依序執行，所有步驟共用 ExecContext]
  │   ├─ F：直接執行（executor 檢查 returncode）
  │   ├─ S：LLM 規劃 → validate → 執行；需要回覆 → chat call
  │   │      └─ LLM 失敗 → 熔斷 → 用 Fast 候選 → 告知；後續步驟照常
  │   └─ read_screen 先等前一個網頁載入
  │
  ▼
回傳 {"steps", "replies", "stages", "total_ms"}；終端印出每步結果、replies、階段計時摘要
```

---

## 四、 正規化擴充（G1）

### 1. `normalize_utterance`（整句層級，Fast 與 LLM 共用）

| 順序 | 處理 | 例子 | 備註 |
|---|---|---|---|
| 1 | Unicode NFKC | `ＣＨＲＯＭＥ`→`CHROME`、`５０％`→`50%` | 標準函式庫 `unicodedata` |
| 2 | 空白收斂與去頭尾 | `開   Chrome ` → `開 Chrome` | 保留中英之間的空白（`VS Code`） |
| 3 | 簡轉繁／大陸用語 | `打开`→`打開`、`屏幕`→`螢幕`、`设置`→`設定`、`搜索`→`搜尋`、`浏览器`→`瀏覽器`、`计算器`→`小算盤` | 不引入 OpenCC，用詞庫的小對照表 |
| 4 | 誤聽詞（App＋指令詞） | `科索`→`Cursor`、`VS扣`→`VS Code`、`因量`→`音量` | 每個詞標 `observed`／`seed`（D2） |
| 5 | 中文數字（只在數值語境轉換） | `調到五十`→`調到50`、`百分之三十`→`30%`、`五十趴`→`50%` | **不轉換** `一點`、`一下`、`一個` |
| 6 | 疊詞（只處理動作詞） | `打開打開`→`打開` | 只處理詞庫列出的動作詞 |

* 每次正規化都把 `(原文, 結果)` 附加寫入 `logs/normalize.jsonl`（D2，用來回收真實誤聽；`logs/` 不納入版控）。

### 2. `canonicalize_clause`（子句層級，只給 Fast Path 用；送 LLM 的仍是第 1 點之後的原文）

* 去前綴（可疊用）：`嗯|呃|欸|啊|喂|那個|就是|請|幫我|麻煩|麻煩你|給我|可以|能不能|你|幫忙`
* 去語尾：`吧|啦|喔|哦|呢|囉|好嗎|好不好|謝謝|一下|一下下|看看`
* 語序改寫：`把 X 打開/關掉`、`X 打開/關掉` → `打開/關掉 X`
* 量詞：`開個 X`、`開一下 X` → `開 X`

### 3. 詞庫 schema 擴充（`vocab/lexicon.json`）

```json
{
  "apps": [
    {"name": "Google Chrome", "kind": "browser",
     "aliases": ["chrome", "google chrome", "google", "谷歌", "谷歌瀏覽器", "瀏覽器"],
     "misheard": [{"text": "谷哥瀏覽器", "source": "observed"}, {"text": "谷哥", "source": "observed"}]},
    {"name": "Visual Studio Code", "kind": "editor", "aliases": ["vscode", "vs code"], "misheard": [{"text": "VS扣", "source": "seed"}]}
  ],
  "projects": [{"name": "mac_ai_control", "aliases": ["語音助手專案"], "path": "/Users/owo/projects/mac_ai_control"}],
  "project_roots": ["/Users/owo/projects"],
  "term_misheard": [{"text": "因量", "to": "音量", "source": "seed"}],
  "s2t": {"打开": "打開", "屏幕": "螢幕"},
  "verbs": {"open": ["打開", "開啟", "啟動", "開", "叫出"], "quit": ["關掉", "關閉", "退出", "結束"]},
  "fillers": {"prefix": ["..."], "suffix": ["..."]},
  "terms": ["..."]
}
```

* `google`、`谷歌` 加入 Google Chrome 的別名（D7）。
* `kind`：`browser`／`editor`／`other`，給 `ExecContext` 判斷用。
* `misheard` 改成物件格式（帶 `source`）；載入程式同時接受舊的字串格式（視為 `observed`），讓現有詞條不需要一次改完。
* `project_roots`：第一層資料夾自動成為專案（名稱就是資料夾名）；`projects` 用來補中文暱稱或覆寫路徑（D4）。
* `build_vocab.py`：`stt_hints.txt` 加入專案名稱與別名；`misheard`、`term_misheard`、`s2t` 一律不寫入。
* 碰撞檢查擴充：同一個詞不能同時屬於兩個 App、兩個專案，或同時是 App 又是專案。

---

## 五、 Fast Path 放寬與擴充（G2、G3）

### 1. 比對流程（每個子句）

```text
canonicalize_clause(clause)
  → 安全守門：否定（不要|別|不用|先不要|不必）、敘述（昨天|剛剛|剛才|之前|已經…了）、
              提問（嗎|是什麼|怎麼|為什麼|可以嗎|?）→ 回傳 None，交給 LLM（而且不提供候選）
  → 意圖規則：App 開關 / 音量 / 靜音 / 亮度 / 深色模式 / 鎖定螢幕 / 搜尋 / 開專案 / 讀畫面
  → 槽位解析：完全比對別名 → 容錯比對
  → Match(actions=[(工具, 參數), ...], confidence, note)
```

* 睡眠（`sleep`）不放寬：只接受固定句 `讓電腦睡眠`，其餘交給 LLM。
* `match()` 維持「只比對、不執行」的契約；回傳的 `Match` 可以被呼叫執行；`try_execute()` 保留。

### 2. 容錯比對

| 對象 | 方法 | 門檻（初始值，用 B3／B4 校正） |
|---|---|---|
| 拉丁字母的 App／專案名 | 轉小寫、去掉空白 `_` `-` 後，用 `difflib.SequenceMatcher` 比對（標準函式庫） | 相似度 ≥ 0.80 且長度 ≥ 4 |
| 中文名 | **不做模糊比對**，只查別名表和誤聽表 | — |
| 歧義保護 | 第 1、2 名分數差 < 0.05 | 不走 Fast；第 1 名當作「候選」，交給 LLM |

例子：`chorme`→Google Chrome（0.83）、`spotfy`→Spotify（0.92）、`mac ai control`→`mac_ai_control`（1.0）。容錯命中時會在 `note` 記下修正過程。

### 3. Fast 規則

| 意圖 | 例句（正規化之後） | 動作 |
|---|---|---|
| App 開關 | `打開 Chrome`、`把 Chrome 打開`、`Chrome 關掉`、`開個計算機`、`開 google` | `control_mac_app` |
| 音量 | `音量 50`、`音量調到50`、`音量設成50%`、`聲音開到最大`／`最小`／`一半` | `set_system_volume(level)` |
| 靜音 | `靜音`、`靜音一下`、`把聲音關掉`、`關聲音` / `取消靜音`、`把聲音打開`、`開聲音` | `set_system_volume(mute)` |
| 亮度 | `螢幕調暗`、`亮度調亮一點`（3 格）、`調到最亮`／`最暗`（16 格） | `adjust_screen_brightness` |
| 深色模式 | `開深色模式`、`切到深色模式`、`深色模式打開` → on；`關閉深色模式`、`關掉夜間模式` → off；`切換深色模式` → toggle | `system_power_action`（D3） |
| 鎖定螢幕 | `鎖定螢幕`、`鎖螢幕`、`鎖屏` | `system_power_action(lock_screen)` |
| 搜尋 | `搜尋 X`、`查一下 X`、`上網查 X`、`google 一下 X`、`用 google 查 X`、`開瀏覽器查 X` | `web_search_or_open_url(X)`；X 不可為空 |
| 開 X 搜尋 Y | `開 google 搜尋台北天氣`、`開 chrome 查天氣` | 兩個動作：開瀏覽器 → 在該瀏覽器搜尋 |
| 開專案 | `開 mac_ai_control 專案`、`用 cursor 開 X 專案` | `open_project` |
| 讀畫面 | `辨識這頁`、`看一下這頁`、`這頁在寫什麼`、`念這頁`、`幫我看畫面` | `read_screen`（產生回覆） |

* `google` 的判斷：前面是開啟動詞（`開 google`）→ 開 Chrome；`google 一下 X`、`用 google 查 X` → 搜尋。
* 「開瀏覽器查 X」只搜尋、不另外開 App（沿用設計指南的 LLM 規則）。

---

## 六、 多段意圖與新執行器（G4、G6）

### 1. 執行上下文 `ExecContext`（只在同一次 `process_voice_command` 內有效，不跨語句）

| 欄位 | 由誰寫入 | 由誰讀取 |
|---|---|---|
| `last_editor`、`last_browser` | `control_mac_app` 開啟成功時，依詞庫 `kind` 寫入 | `open_project` 選編輯器；搜尋選瀏覽器 |
| `page_pending`（URL、開始時間） | `web_search_or_open_url` | `read_screen` 先等網頁載入 |
| `failed` | 任何執行失敗的步驟 | 依賴它的步驟改成記錄「前一步失敗，略過」 |
| `replies` | chat call、`read_screen`、錯誤告知 | 回傳值 |

### 2. 執行器變更與新增

**`web_search_or_open_url(query)`（行為擴充，schema 不變）**：有 `ctx.last_browser` 時用 `open -a "<browser>" <url>`，沒有就維持 `open <url>`（預設瀏覽器）。

**`open_project(project_name, editor=None)`（新增）**
* 路徑**只能**由白名單解析（`projects` ＋ `project_roots` 掃描），LLM 給的任意路徑一律拒絕。
* 名稱比對：完全比對 → 拉丁字母容錯比對（同第五章 2）。
* 編輯器選擇順序：參數 `editor` → `ctx.last_editor` → `Visual Studio Code`。
* 執行 `open -a "<editor>" "<path>"`。
* 找不到專案時記錄「找不到專案 X，已知專案：…」，不猜。

**`read_screen()`（新增）**
1. 有 `ctx.page_pending` 時先等：瀏覽器是 Chrome 就用 AppleScript 輪詢 `loading of active tab of front window`（上限 8 s）；其他瀏覽器固定等 2.5 s。
2. `screencapture -x -m <tmp>.png` → `sips -Z 1280`（都是系統內建工具，D8）。
3. `_chat` 送出 `image_url`（data URI）＋固定問題「這個畫面在顯示什麼？用繁體中文條列重點」，**不帶 tools**，`max_tokens` 256，逾時 30 s。
4. 結果寫入 `ctx.replies`；截圖暫存檔在 `finally` 中刪除。
5. 需要「螢幕錄製」權限（第十一章 D 組 E3）。

### 3. Tools schema 變更

| 工具 | 參數 | 說明 |
|---|---|---|
| `open_project`（新增） | `project_name`(string, 必填)、`editor`(string, 選填) | 用編輯器開啟已知專案 |
| `read_screen`（新增） | 無 | 辨識目前畫面或網頁內容 |
| `chat_reply`（新增） | 無 | 使用者在提問或閒聊、需要回答時呼叫 |
| `system_power_action`（變更） | `action` 的 enum 新增 `dark_mode_on`、`dark_mode_off` | D3 |

* 工具從 5 個增加到 8 個，prompt 約從 620 tokens 增加到 800 tokens（推估）。4bit 漏呼叫可能增加（設計指南第八章 G3），C-R 要全部重跑。
* `read_screen`、`chat_reply` 不帶參數，減少 2B 模型需要填的欄位。
* `validate_tool_call` 同步支援新工具；`open_project.project_name` 解析不到白名單就拒絕執行。
* 離線測試 `test_chat_request_body` 的 `len(tools) == 5` 改成 8。

---

## 七、 LLM 保護與補救（G7、D5）

| 情況 | 偵測 | 狀態 |
|---|---|---|
| 未啟動 | `URLError`／`ConnectionRefusedError` | `llm_unavailable` |
| 卡住 | 規劃呼叫逾時 10 s、讀畫面 30 s（目前 60 s） | `llm_unavailable` |
| 回應格式壞掉 | `KeyError`／`IndexError`／`JSONDecodeError` | `llm_error` |
| 熔斷 | 失敗後 30 s 內不再呼叫 LLM（模組層級時間戳） | 後面的 S 段直接進入補救 |

**補救順序（每個 S 段）**
1. 有 Fast 候選（歧義保護擋下的第 1 名），而且**沒有被安全守門擋下** → 執行候選，狀態記為 `fallback`。
2. 沒有候選 → 不執行，記錄「AI 服務未啟動，這句沒有處理：『…』」到 `replies`。
3. **無論如何都繼續執行後面的步驟**。

* `warmup_llm()` 失敗只記警告，不讓程式結束。
* executor 改成 `capture_output=True` 並檢查 `returncode`：不為 0 就回傳 `❌` 和 stderr 摘要，狀態記為 `exec_failed`。離線測試的 `sh` fixture 改成回傳 `CompletedProcess(returncode=0)`。

---

## 八、 回覆判斷與 chat call（G5、D1、D6）

| 來源 | 觸發 chat call？ | 結果 |
|---|---|---|
| 一般指令執行成功 | 否 | 沿用現行 log |
| 執行失敗／LLM 無法使用 | 否 | 錯誤訊息寫入 `replies` |
| LLM 呼叫 `chat_reply` | **是** | — |
| LLM 沒呼叫工具、只回 `content` | **是**（D6；`content` 只記錄在 log） | — |
| `read_screen` | 否（VL 結果本身就是回覆） | 寫入 `replies` |
| 被 Fast 守門擋下的提問句（`Chrome 是什麼`） | 交給 LLM 判斷 | — |

**chat call 的規格**
* 輸入是 **S 段原文**，直接當成使用者訊息送入模型（模擬模型收到使用者輸入），不使用 LLM 改寫過的問題。
* 不帶 tools；用對話專用 system prompt：「用繁體中文簡短回答；句中的電腦操作已由其他程式處理，不要描述或重複」；`max_tokens` 128；逾時 10 s。
* 結果做簡轉繁後處理（用 `s2t` 表，覆蓋範圍有限，見 R4），寫入 `replies`，並印在終端。
* **不做**語音或 UI 輸出（D1）。

---

## 九、 階段計時規格（G8）

### 1. 回傳結構（既有欄位不變）

```python
{
  "stages": {"normalize_ms", "split_ms", "match_ms"},
  "steps": [{
      "kind": "fast|slow", "text", "ms", "logs",                           # 既有
      "status": "ok|exec_failed|llm_unavailable|llm_error|fallback|rejected|skipped",
      "match": {"confidence", "note"},                                     # 只有 fast 有
      "llm_ms",                                                            # 既有：規劃呼叫總耗時
      "llm": {"prefill_ms", "decode_ms", "prompt_n", "cache_n", "predicted_n", "overhead_ms"},
      "validate_ms", "exec_ms": [...],
      "wait_ms", "capture_ms", "resize_ms", "vl_ms", "vl": {...},          # read_screen
      "chat_ms", "chat": {...},                                            # chat call
  }],
  "replies": [...],
  "total_ms": ...
}
```

* `overhead_ms` = `llm_ms` − `prefill_ms` − `decode_ms`。
* `_chat` 新增選填參數 `meta: dict`（回填 server `timings`）和 `tools: bool = True`（chat call 與 VL 傳 False）；回傳值仍然是 message，現有測試的 `lambda messages, **kw` 不受影響。
* 終端：每句結束印一行階段摘要。

### 2. 量測方式
* **離線**：固定語料各跑 200 次，算出 normalize／split／match 的 p50／p95。
* **實機**：每個情境跑 5 次，「首次」與「其餘中位數」分開列（沿用設計指南第八章 H 格式）。

---

## 十、 實作步驟（每步完成就驗證；每步都可以單獨回退）

| 步驟 | 內容 | 驗證 |
|---|---|---|
| S1 | executor 檢查 returncode；`sh` fixture 改成回傳 `CompletedProcess` | B0 全過；新增 `open -a` 失敗回報 `❌` 的測試 |
| S2 | LLM 保護：`LLMUnavailable`、逾時、熔斷、router 不中斷、warmup 容忍失敗 | B5 全過 |
| S3 | 計時欄位與 `_chat(meta=, tools=)` | B7 全過 |
| S4 | 詞庫 schema 擴充、`build_vocab`、碰撞檢查、`project_roots` 掃描 | B1 全過 |
| S5 | `normalize_utterance` 擴充、`normalize.jsonl` 紀錄 | B2 全過 |
| S6 | `canonicalize_clause`、安全守門、容錯比對、新 Fast 規則、深色模式開／關 | B3、B4 全過；既有 Fast 測試沒有退步 |
| S7 | `ExecContext`、`open_project`、`read_screen`、「開 X 搜尋 Y」、搜尋使用指定瀏覽器 | B6 全過 |
| S8 | 新 tools schema、chat call 流程 | B8 全過 → C 組實機 |
| S9 | `python mac_voice_router.py "句子"`：直接送入文字（模擬收到 STT 輸入）；不帶參數時維持現有 demo | 手動執行一次，輸出含階段摘要 |
| S10 | 更新設計指南中失效的段落（第一、三、四、六、七、八、十章），只修改既有內容 | 人工對照 |

---

## 十一、 驗收計畫

### A. 執行指令
```bash
cd /Users/owo/projects/mac_ai_control
.venv/bin/python -m pytest -q              # 離線（B 組）
APC_ENABLED=1 .venv/bin/python -m mlx_vlm.server --model AI_models/LFM3-VL-2B-MLX-4bit --host 127.0.0.1 --port 8080 --max-tokens 512
.venv/bin/python -m pytest -m live -s -q   # 實機（C 組）
.venv/bin/python mac_voice_router.py "開google，搜尋台北天氣，辨識這頁"   # 人工（D 組）
```

### B. 自動化離線測試（不需要模型；`subprocess.run` 是假物件；**通過標準一律是 100% 通過、結束碼 0**）

| # | 驗收項目 | 測資與通過標準 | 失敗時 |
|---|---|---|---|
| B0 | 不退步 | 既有 52 個離線測試全過；只允許改 `len(tools)`、`sh` fixture 回傳值、`toggle_dark_mode` 相關的 Fast 測試預期（依 D3 改成 on），**不能放寬其他斷言** | 停下來找原因 |
| B1 | 詞庫 | 沒有碰撞（含 `google` 只屬於 Chrome）；`misheard`／`term_misheard`／`s2t` 不在 `stt_hints.txt`；`stt_hints` 不超過 200 條；舊的字串格式 `misheard` 仍然能載入；`project_roots` 掃描以 `tmp_path` 假目錄驗證 | 修詞庫或載入程式 |
| B2 | 正規化 | ≥ 40 組 `(原文, 預期結果)` **逐字相等**：全形、簡體、中文數字（`一點`／`一下`**不能**被轉換）、誤聽、疊詞、空白（`打開 VS Code` 不變）；`normalize.jsonl` 有寫入（用 `tmp_path`） | 修規則；預期值有歧義就問你 |
| B3 | Fast 放寬（正例） | ≥ 60 句，每句的 **subprocess 指令序列**完全等於預期，而且全程不呼叫 LLM（`_chat` 設成 `pytest.fail`）；涵蓋第一章 2 所有未命中的例子，以及 `開 google`→`open -a Google Chrome` | 修規則或門檻 |
| B4 | Fast 安全（反例） | ≥ 25 句：否定、敘述、提問、歧義、低相似度（`開 chr`）、非 App 名詞（`開會`、`開心`、`音量設定為50` 不能開 System Settings）→ 不會產生錯誤動作（`match()` 是 None，或者只產生預期的音量動作），**沒有誤執行的 subprocess** | 安全項目：寧可少命中，也不能放寬 |
| B5 | LLM 保護 | ① `_chat` 拋 `URLError`：`開 Chrome，幫我整理一下桌面，把音量調到30` → 指令序列 = [開 Chrome, 音量 30]、不拋例外、`steps[1].status=="llm_unavailable"`、`replies` 有告知訊息、`total_ms` < 100 ② 本機開一個只接受連線、不回應的 socket，逾時設 0.5 s → 該段 0.7 s 內結束 ③ 同一句兩個 S 段：第二段**不呼叫** `_chat`（熔斷）④ 回應缺少 `choices` → `llm_error` ⑤ 有 Fast 候選時 → `fallback` 並執行候選；被安全守門擋下的句子 → 不執行 ⑥ `warmup_llm` 失敗不會讓程式結束 | 修保護邏輯 |
| B6 | 多段意圖 | ① `開vscode，開mac_ai_control專案` → [`open -a Visual Studio Code`, `open -a Visual Studio Code /Users/owo/projects/mac_ai_control`] ② `用 cursor 開 mac ai control 專案` → 用 Cursor 開 ③ `開google，搜尋台北天氣，辨識這頁`（截圖、縮圖、VL 都是假的）→ 順序 = `open -a Google Chrome` → `open -a Google Chrome <搜尋網址>` → 輪詢載入 → `screencapture` → `sips` → VL，`replies` 長度 1 ④ `開 google 搜尋台北天氣 然後辨識這頁` 結果和 ③ 相同 ⑤ 不在白名單的專案 → 0 次 `open`、記錄「找不到專案」⑥ LLM 給的 `open_project` 解析不到白名單 → validate 拒絕 ⑦ VL 失敗時截圖暫存檔也會被刪除 ⑧ 開 Chrome 失敗（假 returncode=1）→ 後面的搜尋和讀畫面是 `skipped` | 修 context 或執行器 |
| B7 | 計時結構 | 每個 step 都有 `status`；fast 有 `match.confidence`；slow 的 `llm` 欄位來自假 timings、`overhead_ms` ≥ 0；`total_ms` ≥ 各步 `ms` 的總和；離線語料 200 次 normalize＋split＋match **p95 < 5 ms／句** | 結構錯就修；超標先 profile |
| B8 | chat call | ① LLM 回傳 `chat_reply` → 觸發第 2 次 `_chat`，第 2 次**不帶 tools**，user 訊息**等於 S 段原文** ② 只回 `content` → 一樣觸發 chat call ③ 回覆中的簡體詞被轉換（用表內的詞驗證）④ chat call 失敗 → `replies` 記錄告知，不拋例外 | 修流程 |

### C. 實機模型測試（需要 server；除非註明，`subprocess` 都是假的）

| # | 驗收項目 | 通過標準 | 失敗時 |
|---|---|---|---|
| C-R | 回歸 | 設計指南 C0～C16 用 8 個工具的 schema 全部重跑，每案 3 次都要過（C5 的預期依 D3 改為 `dark_mode_on`） | 先比較 5 工具和 8 工具的差異；如果是工具變多造成的退步，停下來跟你討論，不硬改 prompt |
| C-N1 | LLM 判斷新工具 | `打開那個語音助手的專案`→`open_project(mac_ai_control)`；`幫我看看畫面上寫什麼`→`read_screen`；`Python 是什麼`→`chat_reply`；`今天心情不錯`→`chat_reply` 或沒有工具（兩種都會觸發 chat call）；每案 3 次全對、`rejected` 為空 | 調整工具描述後重跑 C-R |
| C-N2 | 混合句 | `開 Chrome，順便告訴我 Python 是什麼` → 開 Chrome（F）＋ chat call（S），`replies[0]` 非空，而且不含 `s2t` 表內的簡體詞 | 同上 |
| C-T1 | 規劃延遲 | 8 個工具、暖機後單工具 n=10：p50 < 1.5 s、max < 3.0 s（沿用 C14） | 看 `cache_n` 是否命中 APC |
| C-T2 | 階段計時表 | 第九章 2 的情境輸出「首次／其餘中位數」，每階段都有數值；**不設門檻**，結果記錄到設計指南 | — |
| C-V1 | 讀畫面延遲基線 | 固定一張 1280 寬的網頁截圖跑 5 次，記錄 `prompt_n`、`prefill_ms`、`decode_ms`；**門檻由你訂（D9）** | — |
| C-V2 | 讀畫面內容 | 固定 3 張截圖（中文新聞、天氣、程式碼），列出模型輸出，**由你判斷是否可以接受（D9）** | — |

### D. 人工／介面驗收（真的會改系統，需要你在場；用 `mac_voice_router.py "句子"` 送入文字）

| # | 操作 | 通過標準 |
|---|---|---|
| E1 | `開vscode，開mac_ai_control專案` | VS Code 開啟該專案資料夾 |
| E2 | `開google，搜尋台北天氣，辨識這頁` | Chrome 開啟並顯示搜尋結果；終端印出的 `replies` 內容符合畫面（由你判斷） |
| E3 | 第一次執行 `辨識這頁` | 跳出「螢幕錄製」授權提示，授權後重跑成功 |
| E4 | 關掉 server 後執行 `開 Chrome，幫我整理一下桌面，把音量調到30` | Chrome 開啟、音量變成 30、終端有「AI 服務未啟動」訊息、程式沒有崩潰 |
| E5 | `開 NoSuchApp` | 終端回報 `❌`，不是 `✅` |
| E6 | 已經是深色時執行 `打開深色模式` | 仍然是深色（不會被切回淺色） |

### E. 本次不驗證／不在範圍
* 語音或 UI 回覆輸出（D1）。
* 實際麥克風 STT 端到端、`macos-stt` 串接；`seed` 誤聽詞在真實 STT 上的命中率。
* 讀畫面的準確率統計（只有 C-V2 的 3 張人工判讀）。
* 睡眠真實執行；Safari／Firefox 的網頁載入偵測（只有固定等待）。
* 8bit 模型回歸；長時間常駐、記憶體、並行請求。

---

## 十二、 風險與假設

| # | 風險 | 對策 |
|---|---|---|
| R1 | 放寬比對造成誤執行 | 安全守門＋B4 反例集；中文不做模糊比對；睡眠不放寬 |
| R2 | 工具變成 8 個，4bit 漏呼叫或選錯工具 | C-R 全部重跑；退步時停下來討論 |
| R3 | 讀畫面太慢或讀錯 | 縮到 1280 寬；先量基線（C-V1）再由你訂門檻 |
| R4 | 模型回覆夾雜簡體 | `s2t` 後處理只涵蓋表內的詞，無法保證全部轉換 |
| R5 | `seed` 誤聽詞替換掉正常用語 | 每個詞標來源；B2 加反例；靠 `normalize.jsonl` 修正 |
| R6 | `google`、`設定`、`音樂`、`line` 這類短別名誤觸（例如「google 一下」不應該開 Chrome） | 短別名只能出現在動詞後面的槽位；`google` 依動詞判斷是開 App 還是搜尋；`build_hint_lines` 改成以詞邊界判斷 |
| R7 | 依賴造成連鎖失敗 | 前一步失敗時，依賴它的步驟記為 `skipped` |
| R8 | 改 `_chat`／executor 的細節影響既有測試 | B0 限定只能改三處已知的地方 |
| R9 | `project_roots` 掃描到不該開的資料夾 | 只掃描第一層、排除隱藏資料夾；開啟前一律從白名單解析 |

**假設**：VS Code 與 Cursor 的 App 名稱是 `Visual Studio Code`／`Cursor`；Chrome 已允許 AppleScript 讀取分頁狀態（不需要「允許來自 Apple 事件的 JavaScript」，因為只讀 `loading` 屬性）；`logs/` 寫在專案根目錄。

---

## 十三、 實作結果與偏離（2026-11-03）

**結果**：離線 349 passed、實機 36 passed；E1～E6 人工驗收已做，E7～E10 未驗證（見設計指南第八章 E）。D9（讀畫面門檻）仍待你看過輸出後決定。

**與本計劃書的差異**

| 項目 | 計劃 | 實作 | 原因 |
|---|---|---|---|
| B0「不退步」 | 只允許改 3 處既有測試 | 實際改了 5 個路由測試的 Slow 範例，並移除 `test_fast_path_miss` 的 2 句 | 亮度與搜尋依需求改走 Fast，這些測試原本拿它們當 Slow 範例；已改用 Fast 不涵蓋的說法，斷言沒有放寬 |
| 容錯比對長度 | 英文名稱 ≥ 4 | ≥ 5 | `chro`（4 字母）與 `chrome` 相似度剛好 0.80 會誤命中；B4 要求 `chr` 之類不命中 |
| 熔斷範圍 | 失敗後 30 s 內不再呼叫 | 只有連不上／逾時熔斷；格式錯誤與 HTTP 錯誤不熔斷 | 單次壞回應不該讓 AI 停用 30 s |
| 正規化紀錄 | 每次都記 | 只記詞庫替換造成的改動 | 全形逗號被 NFKC 轉半形也算改動，幾乎每句都會寫入 |
| 簡轉繁表 | 小對照表 | 116 個詞／字 | 讀畫面回覆與簡體指令（`然后`、`点`、`气`）需要更多字 |

**計劃書沒有、但實作時加的**

* `ExecContext` 的略過規則：開瀏覽器／編輯器失敗時，依賴它的搜尋、讀畫面、開專案標為 `skipped`。
* 外部指令逾時：`screencapture`／`sips` 15 s、查 Chrome 載入狀態 5 s（避免卡在授權對話框）；`OSError` 轉成 `❌`。
* 畸形 `tool_calls`（元素非物件、缺 `function`）與非物件的 `message`：轉為拒絕或 `llm_error`，不崩潰（寫測試重現後才修）。
* 工具描述調整（I7）與暖機帶 system prompt（I8）。

**未做／未驗證**：Chrome 載入狀態輪詢的真實效果（這台沒有安裝 Chrome）、亮度與鎖定螢幕的授權流程、Cursor、長時間待命、demo 模式；`seed` 誤聽詞對真實 STT 的命中率。
