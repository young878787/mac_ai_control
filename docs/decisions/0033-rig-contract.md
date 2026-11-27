# 0033 模型契約統一：checkRig 拿模型對契約檢查，profile 選填

- 狀態：採用（修改 [0014](0014-desktop-layers.md) 第 2 條、[0032](0032-switchable-character.md) 第 3 條；第 4 條被 [0034](0034-model-agnostic-frontend.md) 修改）
- 日期：2026-11-27
- 來源：[計畫書](../plans/completed/2026-11-27-rig-contract.md)；使用者 2026-11-27 的決定（契約集中一處、profile 選填並警告、表情全部選用、參數維持必要）

## 背景

原本的 `checkRig(profile, model3, vocab)` 檢查的是「profile 加模型」：哪些表情必要，由每份 profile 放在 `expressions` 還是 `optional` 決定。接上 hiyori 時，它的 profile 把所有表情放進 `optional` 才通過，等於每個模型都能自己放寬標準，`checkRig` 不再是統一的契約。另外標準參數 ID 每份 profile 重抄、沒登錄的模型被套上 RushiaHD 的 profile、錯誤訊息不說模型有哪些名稱。

## 決定

1. 契約只有一份（`desktop/rig/contract.js`）：語意名稱、約定的模型名稱（沿用 RushiaHD 的命名）、標準參數 ID。
2. 等級以類別為單位，由契約決定：**動作、參數必要；表情、手勢選用**。RushiaHD 的表情也改成選用，缺了只警告。
3. `checkRig` 拿模型對契約檢查，回傳解析後的 rig；缺必要項目時列出模型有的動作群組並提示怎麼配置。
4. profile 選填，只能改名稱、版面與只播一次的動作，改不了等級。沒有 profile 的模型照約定名稱檢查、版面用整張畫布，並警告要配置。

## 後果

- 照約定命名的模型不用寫 profile 就能用；名稱不同的模型只需寫有差的名稱。
- RushiaHD 少了表情檔不會再擋下啟動，只在 `ui.log` 警告。
- hiyori FREE 仍不能用（缺 6 個眉毛參數，參數維持必要）。
- 沒有 profile 時的預設版面（整張畫布）會讓四周有空白的模型偏小。
