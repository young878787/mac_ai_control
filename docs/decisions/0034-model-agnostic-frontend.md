# 0034 前端不寫死模型：lint 擋常值，換名模型測試擋執行時的寫死

- 狀態：採用（修改 [0033](0033-rig-contract.md) 第 4 條）
- 日期：2026-11-27
- 來源：使用者 2026-11-27 的決定（做 lint 防止前端表現過度針對 RushiaHD；頭部中心改成 layout 的選填欄位；換名模型測試一起做）

## 背景

契約集中到 `rig/contract.js` 之後（0033），換模型只要寫 profile。但之後調整前端表現時，很容易順手寫死 RushiaHD 的名稱或比例，日常測試和 `ui_demo` 都只跑 RushiaHD，所以別的模型壞了也不會發現。盤點 rig/ 以外的前端，已經有幾處這種寫死：

- `procedural.js` 的 `motionBlinks` 預設看 `'ParamEyeLOpen'`。
- `dev/motion-metrics.js` 用 `'Idle'` 找待機動作。
- `physics-clock.js` 的診斷字串寫著 RushiaHD。
- 畫面標題寫 Rushia。
- `animator.js` 的頭部中心寫死 `0.16 * MODEL_H`。

頭部中心那一處是比例，不是名稱，lint 看不出來。

## 決定

1. ESLint 規則 `local/model-agnostic` 套用到 `renderer/`、`domain/`、`dev/`（rig/ 與測試除外），擋四種寫法：
   - `Param`／`Part` 開頭的參數 ID 常值。
   - 契約的約定名稱常值。清單從 `rig/contract.js` 算出來；和語意名稱相同的（例如 `'happy'`）不算。
   - rig/ 以外直接 import 個別模型的 profile。
   - 程式碼裡出現 rushia、fullrig、hiyori。

   `'EyeBlink'`、`'LipSync'` 是 Live2D 的標準群組名稱，不擋。
2. `desktop/test/model-agnostic.test.js` 用一個每個名稱都改過的模型，照 `app.js` 的接法跑 animator、程序層、導演與狀態機。查表只認換過的名稱，所以繞過 rig 的寫法都會失敗，包括用字串組出來的名稱。
3. 頭部中心改成 profile `layout.headCenter`，選填，預設 0.16，也就是原本寫死的 RushiaHD 值。hiyori 先沿用預設。
4. 動作軌跡帶 `idle`（待機動作的模型名稱），閒置指標用它找待機，不再寫死名稱。

## 後果

- 前端寫死模型名稱會被 `npm run lint` 擋下；繞過 rig 的寫法由 `node --test` 擋下。
- 變異檢查：把待機名稱、參數 ID、頭部中心改回寫死，不論是常值還是字串組的，至少一層會失敗。
- 數值的調參仍然抓不到，例如停格門檻、眉毛增益、物理的假設，這些都是在 RushiaHD 上調的。換模型時要目視確認。
- hiyori 的頭部中心沿用 0.16，沒有實測。點擊與游標的 dy 可能偏低，需要時在 `rig/hiyori.js` 設 `headCenter`。
