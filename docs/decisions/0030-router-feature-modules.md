# 0030 router 的執行器依功能分模組

- 狀態：採用
- 日期：2026-11-26
- 來源：[盤點計畫](../plans/completed/2026-11-26-router-split-inventory.md) §10、§11；使用者 2026-11-26 確認 E1～E6，並要求「依照現有分類拆分」

## 背景

`local_actions.py`（973 行）超過 [0028](0028-file-line-limit.md) 的 800 行上限。它和 `actions.py`（643 行）是按「LLM 看不看得到」分的，但可見性已經由 `TOOLS_SCHEMA`、`CLOUD_ONLY_TOOLS` 和 `validate_tool_call` 決定，和程式在哪個檔無關。結果兩個檔各混了好幾種功能。測試檔 `tests/flow/test_route_music.py`（1226 行）也超標，而且它測的正是這些功能。

## 決定

1. **依盤點 §10.1 的功能組分模組，每一組整組放進一個模組**：

   | 模組 | 功能組 |
   |---|---|
   | `exec_context` | 執行基礎（`ExecContext`、`front_context`／`front_app`／`front_site`、`mark_page`、逾時常數、`_FAIL_PREFIXES`、`_skipped`） |
   | `browser_tools` | 瀏覽器／分頁 |
   | `system_tools` | 系統／App |
   | `screen_tools` | 讀畫面 |
   | `chat_tools` | 聊天 |
   | `actions` | 分派（`AVAILABLE_FUNCTIONS`、`LOCAL_TOOLS`、`_dispatch`、`run_tool`） |
   | `prefs_db` | 偏好 DB 共用（`MUSIC_DB`、`_now`、`_rng`、`_db_call`） |
   | `music_tools` | 音樂分頁開啟、背景音樂控制、點歌、播放事件／回饋 |
   | `answer_tools` | 天氣、長尾回答 |
   | `front_tools` | 站內搜尋／常用功能 |
   | `find_tools` | 資料夾、檔案搜尋、選擇清單、聯合搜尋 |
   | `local_actions` | 只登錄本機工具（`LOCAL_TOOLS.update`） |

   盤點時歸屬有爭議的定義（§10.0 第 6 點）都照 §10.1 的歸類放。檔案搜尋、選擇清單、聯合搜尋之間的循環因為放在同一個模組而消失。

2. **分層**（`.importlinter`）：C2 是 `pipeline` > `slow_path` > `fast_path` > `local_actions` > `find_tools` > `front_tools` > `music_tools`｜`answer_tools` > `actions` > `screen_tools`｜`chat_tools`｜`system_tools` > `browser_tools` > `exec_context`。`prefs_db` 放 C3（底層），C3 禁止底層 import 的模組加上 9 個新的執行模組。沒有新增 `ignore_imports`，例外仍是 [0029](0029-deps-converge.md) 的 2 條。

3. **會被測試整個換掉的狀態一律用模組屬性存取**：`prefs_db.MUSIC_DB`、`prefs_db._now()`、`prefs_db._rng`。不要寫 `from prefs_db import _now`，否則 patch 不會生效。

4. **搬走的名稱不在原模組留同名的轉出**：漏改的 patch 或 import 會直接報錯，不會靜默失效。唯一保留的是 `actions` 為了分派而 import 的工具函式，例如 `actions.read_screen`，測試 patch 它就是 patch 分派時呼叫的那一個。

5. **登錄方式不變**：`local_actions` 載入時登錄。`pipeline`、`slow_path` 改成明寫 `from mac_ai.router import local_actions  # noqa: F401`，並寫上原因。

6. **名稱不變**：只搬位置、改 import，不改函式名稱與行為。跨模組使用的私有名稱（`_dispatch`、`_background_control`、`_record_control`、`_now`…）記在 [module-deps.md](../flows/module-deps.md) 的結構債。

7. **測試檔依盤點 §10.12 的區塊拆開**：
   - `test_route_music.py`：路由語料、守門、回饋路由。保留原檔名，因為 `test_completeness.py` 用路徑載入它的 `C1_POSITIVE`。
   - `test_route_music_control.py`：播放控制。
   - `test_route_music_play.py`：點歌執行。
   - `test_route_music_feedback.py`：播放事件與回饋。
   - 共用的 fixture 與 helper 放 `tests/support/music.py`。

## 後果

- 每個 router 模組都在 800 行以內，最大的是 `music_tools` 462 行；測試檔最大 326 行。同一個 PR 也把不在本次範圍的 `desktop/test/attention.test.js`（1041 行）拆成兩個檔，共用的 helper 放 `desktop/test/support/attention.js`，`scripts/check_file_lines.py` 全部通過。
- 行為不變：函式本體逐字搬移；拆分前後的測試案例集合相同（425 個）；離線 `pytest` 0 failed。
- 速度不變：`import mac_ai.router.pipeline` 前後的中位數都約 61 ms，`match()` p95 前後都是 0.090 ms（門檻 0.098 ms）。熱路徑仍不載入任何執行模組（`test_import_footprint.py` 已把新模組加進檢查清單）。
- 新增本機工具要寫在對應功能的模組，再到 `local_actions` 登錄；都不合適時先提計畫（[coding-style.md](../guides/coding-style.md)）。
- 沒有跑：`pytest -m app`、live／cloud、語音實機、CI。
