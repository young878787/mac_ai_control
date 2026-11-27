# 桌面角色的模型契約統一：checkRig 直接拿模型對契約檢查、profile 改選填

狀態：**已結案（2026-11-27）**：實作與驗收結果見 §5，決策見 [ADR 0033](../../decisions/0033-rig-contract.md)。使用者 2026-11-27 確認：契約集中在一處、`checkRig` 直接拿模型對契約檢查、profile 改選填並在沒有時警告；表情全部選用、參數維持必要；新增 ADR 0033 修改 [0014](../../decisions/0014-desktop-layers.md) 第 2 條與 [0032](../../decisions/0032-switchable-character.md) 第 3 條。

## 0. 問題

現在的 `checkRig(profile, model3, vocab, paramIds)`（`desktop/rig/check.js`，2026-11-20 [桌面分層](../completed/2026-11-20-desktop-layering.md) §4 設計）：

1. **profile 同時是名稱對應與契約**：表情必要或選用，看 profile 把它放在 `expressions` 還是 `optional`，每個模型都能自己放寬標準（`rig/hiyori.js` 就把全部表情放進 `optional`）。
2. **標準參數 ID 每份 profile 重抄**：RushiaHD 與 hiyori 的 19 個完全相同。
3. **沒登錄的模型被套上 RushiaHD 的 profile**：`rigFor` 退回 `fullrig03`，版面一定錯；錯誤訊息只說「模型缺少動作：nod（Nod）」，不說模型有哪些名稱、該去哪裡配置。
4. **profile 打錯的 key 不會被發現**：多餘或拼錯的語意名稱被忽略。
5. **檢查與使用分兩處算**：`checkRig` 只回警告，`renderer/role/app.js` 再自己從 profile 抽要載入的名稱。

## 1. 設計

| 項目 | 內容 |
|---|---|
| 契約 `rig/contract.js` | 每個語意名稱 → 約定的模型名稱（沿用 RushiaHD 的命名）；19 個標準參數 ID。**等級以類別為單位：動作、參數必要；表情、手勢選用**。沒有 profile 時的版面預設值。rig 層，不 import 任何東西 |
| `checkRig({ file, model3, paramIds, canvas }, vocab, profile?)` | 回傳 `{ rig, warnings }`。名稱＝profile 有寫就用 profile 的，否則用約定名稱；等級一律看契約。缺必要的動作或參數拋錯，訊息列出模型有的動作群組並提示怎麼配置；缺表情或手勢只警告 |
| profile（選填） | 只能有 `motions`、`expressions`、`gestures`、`params`（只寫和約定不同的名稱）、`layout`、`once`。其他 key（含舊的 `optional`）、契約沒有的語意名稱、不完整的 `layout` 都拋錯：profile 改不了等級 |
| 沒有 profile | `rigFor` 回傳 `null`（不再退回 fullrig03）；名稱照約定，版面用整張畫布（`height` 420、`headFraction` 0.25），並警告「沒有 rig profile，在 `desktop/rig/` 新增並在 `rig/index.js` 登錄」 |
| 解析後的 rig | `{ motions, expressions, gestures, params, layout, once }`；拿掉 `optional`（表情只有一種等級），`animator.js`、`probe.js` 的 `?? rig.optional[...]` 跟著刪 |
| `fullrig03.js` | 名稱都和約定相同，只剩 `layout`：RushiaHD 的位置與演出不變 |
| `hiyori.js` | 只剩和約定不同的 6 個動作名稱、`once`、`layout` |

不在範圍：從模型可見範圍自動算版面；不開視窗的模型檢查指令；支援 hiyori FREE（缺 6 個眉毛參數，參數維持必要）；製作表情檔；渲染與幀迴圈（不改，所以不重跑 `ui_perf.py`、`ui_motion.py`）。

## 2. 實作步驟

1. `rig/contract.js`、改寫 `rig/check.js`、`rig/index.js`；`fullrig03.js`、`hiyori.js` 瘦身。→ `node --test test/rig.test.js`
2. `renderer/role/app.js` 改用解析後的 rig；`animator.js`、`probe.js` 刪 `optional`；`domain/character.js` 註解改指向契約。→ `node --test`、`npm run typecheck`、`npm run lint`
3. ADR 0033；0014、0032 狀態行補註；同步 `docs/decisions/README.md`、`docs/guides/coding-style.md`、`ARCHITECTURE.md`。→ `pytest tests/unit/test_docs_index.py`

## 3. 驗收計畫

### 3.1 自動化（離線）

| 項目 | 指令 | 通過標準 | 失敗時 |
|---|---|---|---|
| V1 契約與檢查 | `cd desktop && node --test test/rig.test.js test/animator.test.js` | 0 fail，且涵蓋：契約剛好涵蓋 domain 的語意名稱；照約定命名、沒有 profile → 通過、警告只有一條「沒有 rig profile」、版面＝整張畫布；fullrig03 → 通過、0 警告；缺必要動作 → 拋錯且訊息含模型有的動作群組與配置提示；缺必要參數 → 拋錯；缺任何表情（含 neutral、sleepy）或手勢 → 只警告；hiyori 形狀＋hiyori profile → 通過、10 條警告；hiyori 形狀沒有 profile → 拋錯並列出 `Tap`；profile 有 `optional`、契約沒有的語意名稱、`once` 有不明動作、`layout` 不完整 → 拋錯；`rigFor` 未登錄回 `null` | 修程式，不放寬測試 |
| V2 檢查會咬人 | 暫時把契約的 `talk` 刪掉、把 fullrig03 加上 `optional: {}`，各跑一次 `node --test test/rig.test.js` 後還原 | 兩次都 FAIL；還原後 PASS，`git diff` 不含暫時修改 | 測試寫錯，修測試 |
| V3 桌面全套 | `cd desktop && node --test && npm run typecheck && npm run lint` | 0 fail、0 錯誤 | 修到通過 |
| V4 L2 全套 | `pytest`；`uvx ruff@0.17.6 check .`；`ast-grep scan`；`lint-imports`；`python3 scripts/check_file_lines.py` | `pytest` 0 failed；其餘 0 違規 | 修到通過，不放寬設定 |

### 3.2 真實環境（本機 Electron）

| 項目 | 操作 | 通過標準 | 失敗時 |
|---|---|---|---|
| R1 RushiaHD 不變 | `python scripts/ui_demo.py --capture <暫存>` | 9 項情境＋11 項演出全部 PASS，和改動前（2026-11-27 同分支 `06c5e1e` 的結果）逐行相同；`logs/services/ui.log` 沒有 `[ui]` 警告 | 停下回報，比對 before／after 的 JSON |
| R2 hiyori PRO | 用暫存腳本以 `config.UI_CHARACTERS["hiyori"]` 啟動，注入點擊與成功／失敗結果 | 反應動作是 `FlickUp`、`Tap@Body`、`FlickDown`，播完回到 `Idle`；潛伏與退場後 5 秒內 0 幀；`ui.log` 的 `[ui]` 警告剛好 10 條（7 表情＋3 手勢），沒有「沒有 rig profile」，沒有 error | 停下回報 |
| R3 沒有 profile、名稱不符（hiyori FREE） | 同上，模型指向 `Hiyori/hiyori_free/runtime/hiyori_free_t08.model3.json` | 啟動失敗；`ui.log` 有「啟動失敗」，訊息含缺少的 6 個動作、6 個眉毛參數、模型有的動作群組（`Idle`、`Flick`、`Tap`…）與配置提示 | 停下回報 |
| R4 沒有 profile、名稱照約定 | 暫存資料夾放 RushiaHD runtime 的符號連結，`model3.json` 改名後啟動，跑 `ui_demo.py` 的同一套比對 | 啟動成功；`ui.log` 有一條「沒有 rig profile」；演出與情境 PASS | 停下回報 |

### 3.3 人工（使用者判斷）

| 項目 | 做法 | 通過標準 |
|---|---|---|
| H1 錯誤訊息好不好用 | 回報貼出 R3 的錯誤全文與 R4 的警告全文 | 使用者看得懂缺什麼、要去哪裡配置 |
| H2 版面預設值 | 看 R4 的截圖（沒有 profile 時的位置與大小） | 使用者接受，或指定調整 |

## 4. 風險

- `checkRig` 的參數改了，`paramIds` 一律要給（原本沒給就不檢查參數，只有測試用到）。
- 沒有 profile 時用整張畫布當版面，模型四周的空白會讓角色偏小、點頭部的判定不準；警告會提醒配置。
- 改的是啟動時的檢查與名稱解析，不改每幀的計算；R1 用 `ui_demo.py` 確認 RushiaHD 的演出不變。

## 5. 實作結果

2026-11-27，分支 `feat/switchable-character`（基準 `06c5e1e`），已實際執行確認：

| 項目 | 結果 |
|---|---|
| V1 | `rig.test.js` 11 項＋`animator.test.js` 4 項，15 pass |
| V2 | 刪契約的 `talk`：7 fail；fullrig03 加 `optional: {}`：4 fail；還原後 SHA-1 與原檔相同、11 pass |
| V3 | `node --test` 317 pass（原 318：舊 rig 測試 12 項改寫成 11 項）；`npm run typecheck`、`npm run lint` 0 錯誤 |
| V4 | `pytest` 3416 passed、6 skipped、126 deselected、1 error：`test_xasr_server.py` 的 teardown 逾時，`git stash` 撤掉改動後同樣 1 error（testing.md 記載的既有問題）。ruff、`ast-grep scan`（rc 0）、`lint-imports`（5 kept）、`check_file_lines.py`（rc 0） |
| R1 | `ui_demo.py --capture`：20 項（情境 9＋演出 11）全部 PASS，和 `06c5e1e` 的擷取重新比對的結果逐行相同；`[ui]` 警告 0 條 |
| R2 | 反應動作 `FlickUp`、`Tap@Body`、`FlickDown`，播完回到 `Idle`；潛伏與退場後各 5.5 秒 0 幀；執行中 error 0 行；`[ui]` 警告剛好 10 條（7 表情＋3 手勢），沒有「沒有 rig profile」 |
| R3 | 啟動失敗，錯誤全文：`hiyori_free_t08.model3.json：模型缺少動作：surprised（Surprised）；…talk（Talk）；模型缺少參數：browLY（ParamBrowLY）；…browRAngle（ParamBrowRAngle）。模型有的動作群組：Idle、Flick、FlickDown、Tap、Tap@Body、Flick@Body。在 desktop/rig/ 新增 profile 並在 rig/index.js 依模型檔名登錄（範例 rig/hiyori.js），用 motions／params 把語意名稱對應到模型裡的名稱` |
| R4 | RushiaHD runtime 的符號連結、`model3.json` 改名 `Plain.model3.json`：啟動成功，20 項全部 PASS；警告只有一條：`Plain.model3.json 沒有 rig profile：名稱照約定（rig/contract.js）、版面用整張畫布；名稱或版面不對時在 desktop/rig/ 新增 profile 並在 rig/index.js 依模型檔名登錄（範例 rig/hiyori.js）`；截圖位置與大小和有 profile 時目視無差異（RushiaHD 畫布是正方形，約定版面只差 4 個模型像素） |

與計畫不同的地方：`docs/guides/testing.md` 的視覺驗收表那列改寫成「模型、契約、rig profile、語意名稱」。

已知、不在範圍：`ui.log` 在視窗關閉時偶爾有一筆 `TypeError`（舞台銷毀後又跑了一幀）。在 Hiyori 之前的 `8b33811` 跑 3 次都出現，是既有問題。

H1、H2：使用者 2026-11-27 確認沒問題。

尚未驗證：真正的語音流程（`main.py --ui`）；hiyori 的拖曳、右鍵、輸入框、聊天室。
