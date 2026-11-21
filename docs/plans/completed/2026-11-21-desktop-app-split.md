# 拆 `renderer/role/app.js`（桌面分層整理 S5）

> **歸檔（2026-11-21 結案）**：U1、U2 由使用者判斷通過。以下內文保留結案時的樣子；目前有效的決策見 [ADR 0014](../../decisions/0014-desktop-layers.md)（分層）與 [ADR 0017](../../decisions/0017-renderer-typecheck.md)（型別檢查）。

狀態：**已完成（2026-11-21）**，自動化與實機驗收通過（§5）。基準 HEAD 是 `00b2985`。承接 [桌面前端分層整理](../completed/2026-11-20-desktop-layering.md) §3、§5 的 S5，決策見 [ADR 0014](../../decisions/0014-desktop-layers.md)。

## 0. 結論

1. `renderer/role/app.js` 的 `main()` 是一個約 360 行的閉包，同時做載入模型、建 PIXI、裝修補、三個佇列、進退場、點擊穿透與拖曳、幀迴圈、效能統計、除錯介面。依分層計畫 §3 拆進 engine、animation、view 三層，`app.js` 只留組裝。
2. **整理過程不改外部行為**：演出、介面、效能都和改動前相同。
3. 型別檢查範圍納入 `renderer/`（分層計畫 §4 D4）。`PIXI` 用 `node_modules` 裡 `pixi.js`、`pixi-live2d-display` 的真實型別；`Live2DCubismCore`、`desktop`（preload 橋接）、`window.__ui` 宣告成 `any`（使用者 2026-11-21 確認，不寫 `desktop` 的方法簽名）。

## 1. 搬運對照

| 新檔 | 層 | 內容 |
|---|---|---|
| `renderer/engine/compat/render-orders.js` | engine | `bridgeRenderOrders` 原樣搬過去 |
| `renderer/engine/stage.js` | engine | `waitForCore`、建立 PIXI Application 並停掉三個 Ticker、確認 WebGL、`alphaAt` |
| `renderer/engine/load-model.js` | engine | `readJSON`、載入 model3／physics JSON、`Live2DModel.from`，再依原順序裝修補；載入動作與表情檔 |
| `renderer/engine/loop.js` | engine | rAF／停格計時器、`MIN_FRAME_MS`、`frameStats`、`partMs`、`perf()` 統計 |
| `renderer/animation/animator.js` | animation | 三個佇列、`startMotion`／`startExpression`／`startGesture`、`gestureNone`、進退場（`setPose`／`updatePose`／`topY`）、舊做法的游標視線、每個計算幀從「重設預設值」到 `physics.evaluate`、程序層 |
| `renderer/role/pointer.js` | view | 點擊穿透、拖曳、點擊判斷頭部或身體、右鍵選單 |
| `renderer/role/probe.js` | view | `window.__ui` 全部欄位、動作軌跡記錄器（`motionTrace`、`recordTrace`） |

留在 `app.js`：組裝、`applyEffects`、`desktop.onBus`／`onCursor`、`renderFrame` 的呼叫順序、`updateUi`、`RENDER_OPT`／`PROCEDURAL` 開關、`checkRig` 呼叫、`createFrameGate`。

分層規則逼出來的做法（都不改行為）：
- engine／animation 不能 import `domain/`：`createFrameGate(SETTLE)`、`idle`／`none` 語意名稱、`PEEK_RATIO`、`checkRig` 的詞彙都由 `app.js` 建好或傳入。
- engine／animation 不能碰 `window`／`document`：`desktop.gaze()` 由 view 在 `setPose` 後呼叫；`window.Live2DCubismCore` 改用全域 `Live2DCubismCore`；`__ui.frames` 對外仍是數字。
- **修補的安裝順序不變**：`bridgeRenderOrders` → `installRenderBuffers` → `installStateCache` → `installDrawableColors`；`RENDER_OPT`、`PROCEDURAL` 的開關保留。
- 每幀熱路徑的呼叫形狀不變，不在每幀新建 closure（見 `render-buffers.js` 註解與分層計畫 §7）。

連帶要改：`tsconfig.json`／`package.json` 的 `typecheck`、`types/globals.d.ts`、`ARCHITECTURE.md` 的分層表、README 裡 `bridgeRenderOrders` 的路徑、`motion-sim.test.js` 與 `layers.test.js` 註解裡提到 `app.js` 的地方。

## 2. 實作階段

| 階段 | 內容 | 驗證 |
|---|---|---|
| S5.0 基線 | 在 `00b2985` 上記下 `node --test` 總數、`ui_demo --capture` 存 `before/`；`git archive` 取出舊版副本供 R3 交替量測 | 檔案存在 |
| S5.1 engine | 拆 `render-orders`、`stage`、`load-model`、`loop` | A1、A2 |
| S5.2 animator | 拆 `animator.js` | A1、A2 |
| S5.3 view | 拆 `pointer.js`、`probe.js`，`app.js` 收斂成組裝 | A1、A2 |
| S5.4 型別 | `renderer/` 納入 `typecheck` | A7 |
| S5.5 實機 | R1、R3、R4 | 見 §3 |
| S5.6 文件 | 同步 ARCHITECTURE、README、本計畫的實作結果 | A6 |

## 3. 驗收計畫

### 3.1 自動化（離線，實作者執行並貼出輸出）

| # | 項目 | 指令 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| A1 | 既有測試行為不變 | `cd desktop && node --test` | 0 fail；總數＝基線 266；既有測試檔除了註解沒有差異（`git diff HEAD -- desktop/test/`） | 修程式，**不得**改既有預期 |
| A2 | 分層規則 | 同上（`layers.test.js`） | 違規 0；新檔都落在 engine／animation／view 之內 | 把程式搬到正確的層 |
| A6 | 後端與文件 | `source .venv/bin/activate && pytest` | 0 failed | 修 |
| A7 | 型別檢查 | `cd desktop && npm run typecheck` | `shared domain dev rig main preload renderer` 全部 0 錯誤；新增的 `@ts-ignore`／`@ts-expect-error`／`@ts-nocheck` 為 0（`grep -rn` 計數）；`any` 宣告只有 `Live2DCubismCore`、`desktop`、`chat`、`window.__ui`、`window.__chat` | 修程式或補 JSDoc；需要 ignore 時逐一列出請使用者同意 |

### 3.2 真實環境（本機 Electron，實作者執行並貼出輸出）

| # | 項目 | 指令 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| R1 | 演出與介面不變 | `python scripts/ui_demo.py --capture <暫存>/after`，以及 `--interact-test`、`--choices-test`、`--chat-test`（跑 `--chat-test` 時不要碰滑鼠） | 13 張演出比對與 9 項情境比對全 PASS，三個測試全 PASS；用暫存腳本比對 `before/` 與 `after/` 的每個 `.json`：排除 `frames`、`perf`、`lastOpen` 的耗時欄位後，`motion`、`snap`、`gesture`、`input`、`quick`、`choices`、`choiceRows`、`attention` 必須相同，`poseT` 差 ≤ 0.01 | 對照是哪一個新檔搬錯 |
| R3 | 效能不退步 | 舊版副本與新版在同一個 session 交替各跑 3 次 `ui_perf.py` | 出場每秒峰值 3 次平均的差 ≤ **2 個單核百分點**；幀間隔 p95 ≤ **18.5 ms**（沿用分層計畫 10.3 的決定）；潛伏停格期間 0 幀；每幀 JS p95 ≤ 8 ms | 找出是哪個跨模組呼叫造成的；**不自行放寬**，超標先回報 |
| R4 | 量測工具仍可用 | `python scripts/ui_motion.py --skip-listen --minutes 1` | 正常產出指標（不判定數值） | 修 |

量測限制（分層計畫 10.3）：`ui_perf.py` 的絕對數字對背景取樣活動敏感，**不同時段的數字不能相減**，新舊必須同一個 session 交替執行。

### 3.3 人工驗收（由使用者判斷）

| # | 看什麼 |
|---|---|
| U1 | 角色外觀、進退場、泡泡、選擇清單、聊天室展開與收回，和整理前看不出差別 — **通過（使用者 2026-11-21）** |
| U2 | 照新目錄找一個功能（例如「點擊怎麼判斷頭部」「幀迴圈在哪」），是否比較好找 — **通過（使用者 2026-11-21）** |

### 3.4 不在本次範圍

- 任何外部行為、效能優化、演出參數的改變。
- 寫 `desktop` 橋接的方法簽名型別、TypeScript `strict`、把 `.js` 改成 `.ts`、打包工具。
- 後端、`ui_bus` 協定、模型檔、`app://` 白名單（沒有新增目錄給畫面用）。
- STT 真人語音（需要麥克風）。

## 4. 風險

- **效能**：每幀的呼叫從閉包內區域函式變成跨模組方法呼叫，V8 可能最佳化不同。不改熱路徑形狀，R3 實測為準。
- **時序**：事件處理器在全部 `await` 之後才註冊、`attention.update` 在 animator 計算前由 `app.js` 呼叫（它不讀參數陣列，`activeSemantic` 只在 `startMotion` 時改變），R1 的 `attention` 欄位比對會驗證。
- **型別檢查噪音**：試跑 `renderer/` 有約 25 個錯誤（`face.js`、`procedural.js` 的 JSDoc、DOM 元素需轉型），只修 JSDoc 或加 `/** @type */` 轉型，不改邏輯。若需要大量 `any` 先回報。

## 5. 實作結果

### 5.1 實際做了什麼

| 新檔 | 行數 | 備註 |
|---|---|---|
| `renderer/engine/compat/render-orders.js` | 8 | `bridgeRenderOrders` 原樣 |
| `renderer/engine/stage.js` | 32 | `waitForCore`、`createStage`（含 `alphaAt`、`destroy`） |
| `renderer/engine/load-model.js` | 51 | `loadModel`（修補順序不變）、`loadAssets`、`readJSON` |
| `renderer/engine/loop.js` | 68 | rAF／停格計時器、逐幀統計、`perf()`；`prev`／`lastRender` 歸它管 |
| `renderer/animation/animator.js` | 131 | 三個佇列、進退場、`compute`（到物理為止）、舊做法游標視線 |
| `renderer/role/pointer.js` | 49 | 點擊穿透、拖曳、點擊、右鍵選單 |
| `renderer/role/probe.js` | 70 | `window.__ui` 全部欄位與動作軌跡 |
| `renderer/role/app.js` | 436 → 199 | 組裝 |
| `tsconfig.renderer.json`、`types/globals.d.ts` | | `typecheck` 改成兩份 config 都跑 |

### 5.2 驗收結果（實際執行）

| # | 結果 |
|---|---|
| A1 | `node --test` **266 pass、0 fail**（基線 266）。`git diff HEAD -- desktop/test/` 只有 `motion-sim.test.js` 三行註解（檔名參照），沒有改任何預期 |
| A2 | `layers.test.js` 通過，違規 0 |
| A6 | `pytest`：**1730 passed、5 skipped、0 failed** |
| A7 | `npm run typecheck`（`shared domain dev rig main preload` 與 `renderer`）exit 0；`@ts-ignore`／`@ts-expect-error`／`@ts-nocheck` 為 0 |
| R1 | `ui_demo --capture`：9 項情境比對、11 項演出比對全 PASS；與 `before/` 逐張比對 **13/13 相同**（排除 `session.ratio`／`remainingMs`：它們是牆鐘倒數，兩次相差毫秒級）。`--interact-test`、`--choices-test` exit 0；`--chat-test` 16 項全 PASS（游標移到螢幕角落後。第一次有一項「約 20 秒後自動收回」失敗，原因是游標停在聊天室上，即分層計畫 10.5 記載的已知行為） |
| R3 | 見 5.3 |
| R4 | `ui_motion --skip-listen --minutes 1`：全部通過（動作軌跡經新的 `probe.js` 記錄，1816 幀） |

### 5.3 R3 效能（同一個 session 交替，舊版＝`git archive 00b2985`，各 3 次）

| 指標 | 舊版平均 | 新版平均 | 差 | 門檻 |
|---|---|---|---|---|
| 出場 CPU 每秒峰值（單核 %） | 40.63 | 39.60 | **−1.03** | 差 ≤ +2 ✅ |
| 出場 CPU 平均（單核 %） | 35.07 | 35.23 | +0.17 | （參考） |
| 幀間隔 p95（ms），各次 | 17.6／17.6／18.1 | 17.6／18.2／18.2 | 新版最大 18.2 | 每次 ≤ 18.5 ✅ |
| 每幀 JS p95（ms） | 3.60 | 3.73 | +0.13 | ≤ 8 ✅ |
| 每幀 compute／render（ms） | 1.17／0.43 | 1.25／0.43 | +0.07／0 | （參考） |
| 潛伏停格期間繪製幀數 | 0 | 0 | 0 | 0 ✅ |
| 潛伏停格 CPU（整台 %） | 0.113 | 0.115 | +0.002 | （參考，門檻 0.125 兩版都在邊緣） |

- `ui_perf.py` 自己的 `LIMITS` 仍是 18 ms／5%，所以工具輸出舊版與新版各有幾項 FAIL（幀間隔 p95 18.1～18.2、峰值 5.0～5.4%、進退場 p95 18.2～18.4）。舊版同樣會 FAIL，判定以本表為準（沿用分層計畫 10.3）。
- 新版 compute 平均多 0.07 ms，在 3 次的波動範圍內（舊版單次 1.06～1.36）；沒有證據顯示跨模組呼叫造成退步。
- 量測踩到的坑：舊版副本的 `node_modules` 若是符號連結，Helper 的命令列會顯示正式路徑，`ui_perf.py` 的程序比對（`config.UI_DIR`）會漏掉 Helper，CPU 與 RSS 只剩主程序（第一輪因此作廢重跑）。舊版副本的 `node_modules` 必須是實體副本（`cp -Rc`）。

### 5.4 與計畫不同的地方

- **`any` 比 §3.1 A7 寫的多**：`PIXI.live2d` 的真實型別沒有涵蓋 Cubism 內部類別，`types/globals.d.ts` 把 `CubismMotionManager`、`CubismMotionQueueManager`、`CubismMotion`、`CubismExpressionMotion`、`CubismPhysics`、`CubismShader_WebGL` 這 6 個宣告成 `any`；另有 `/** @type {any} */` 轉型 3 處（`model.internalModel`、`app.renderer.gl`、`motionPreload: 'NONE'`）、`$` 回傳 any（3 個檔）、`Element` 轉型 1 處。都只是 JSDoc 轉型，沒有 `@ts-ignore`。
- 修了擬人化 P1／P2 留下的 JSDoc 型別問題（`face.js` 3 處、`procedural.js` 2 處），沒有改邏輯。
- `loadModel` 先建舞台再讀 model3／physics JSON（原本是先讀 JSON 再建 PIXI）；只影響兩種啟動錯誤同時發生時先報哪一個。
- 新增 `loop.js` 擁有 `prev`（上一幀時間）與 `dt` 的計算，`renderFrame(now, dt)` 改收 `dt`；`attention.update` 移到 `animator.compute` 之前（R1 的 `attention` 欄位 13/13 相同，驗證了時序等價）。

### 5.5 尚未驗證／風險

- U1、U2（外觀與好不好找）要你判斷。
- STT 真人語音（需要麥克風）沒有測；這次沒有動後端與 STT 路徑。
- `ui_perf.py` 的絕對數字對背景取樣活動敏感（見分層計畫 10.3），本表只代表同一個 session 內的新舊差異。
