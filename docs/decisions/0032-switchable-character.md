# 0032 桌面角色可切換；Hiyori 只做動作、支援 pose3

- 狀態：採用（修改 [0013](0013-fullrig03-cpu-budget.md) 第 1 條；第 3 條被 [0033](0033-rig-contract.md) 修改：表情選用改由契約決定，所有模型一致）
- 日期：2026-11-27
- 來源：使用者 2026-11-27 的決定（用 Live2D 官方範例模型 Hiyori 測相容性；用 PRO 版、補 pose 支援、做成可切換、表情改選用）

## 背景

只有一個定版模型時，rig profile 寫死在 `renderer/role/app.js`。用 Hiyori 實測：Cubism Core 與現有渲染修補都能載入、繪製；接不上的原因是沒有任何表情檔、動作群組名稱不同、PRO 版要靠 `pose3.json` 在兩組手臂間擇一（程式沒套用時兩組同時顯示）、動作檔全部標 `Loop`（反應動作會播不完）。FREE 版沒有雙手臂問題，但缺 6 個程序層用的眉毛參數。

## 決定

1. RushiaHD 仍是預設角色；`main.py --ui --character hiyori` 切換。模型路徑在 `config.UI_CHARACTERS`，profile 由 `desktop/rig/index.js` 依模型檔名挑。
2. Hiyori 用 PRO 版，模型檔不修改、不入版控（受 Live2D 的授權與範例資料使用條款約束）。
3. 表情在 Hiyori 是選用：全部列在 profile 的 `optional`，不另外製作 `.exp3.json`。`checkRig` 不放寬，Rushia 的必要表情照舊必要。
4. 部件切換用現成的 `CubismPose`，每個計算幀在物理之後更新；沒有 `pose3.json` 的模型不受影響。
5. 只播一次的語意動作由 profile 的 `once` 指定，不改模型檔的 `Loop`。

## 後果

- Hiyori 沒有表情與手勢演出（睏倦、嘟嘴、托腮等），啟動時 `ui.log` 有 10 行略過警告。
- 7 個語意動作對到各群組的第一個動作，是依動作曲線挑的；群組裡的其他動作沒有使用。
- `ui_demo.py` 的期望表只寫了 Rushia 的名稱，Hiyori 沒有自動的演出比對。
- 2026-11-27 `ui_perf.py`（8 核 M1）：整台電腦 CPU 出場峰值 3.47%、進退場峰值 3.81%、潛伏平均 0.075%，都在 0013 的預算內。幀間隔 p95 18.6 ms 超過 18 ms（同次 Rushia 出場 18.5 ms 也超過、進退場 17.7 ms 通過）。退場後停格 5.55 秒（Rushia 3.20 秒，上限 5.8 秒）。
