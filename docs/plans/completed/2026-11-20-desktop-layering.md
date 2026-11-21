# 桌面前端分層整理（desktop/ 搬運與拆檔）

> **歸檔（2026-11-21 結案）**：原檔名 `docs/桌面前端分層整理：實作計畫.md`。以下內文保留結案時的原樣；文中的路徑、章節編號與「待確認／尚未驗證」都是當時的狀態，目前有效的決策見 `docs/decisions/`。

狀態：**已確認、尚未實作**（2026-11-20）。基準 HEAD 是 `c1bc3e8`，擬人化 P0 已提交。D1～D4 已確認（§8）。

## 0. 結論

1. **核心已經分好，外圈還沒分。**
   - 純邏輯已經是「輸入＋時間 → effects」的形式，而且有測試：`state.js`、`chat.js`、`frame-rate.js`、`physics-clock.js`。
   - 問題集中在三處：
     - 兩個大檔各做 7～10 件事：`main.js` 359 行、`renderer/app.js` 396 行。
     - 模型（rig）的事實散在 3 個檔案。
     - 與後端的協定常數只靠註解「要一致」維持。
2. **分層用兩刀切。**
   - 第一刀依**行程**切：main／preload／renderer／shared。
   - 第二刀在畫面行程內依**離函式庫多近**切：domain → animation → engine，rig 是資料。
   - 依賴只能往下（§1）。
3. **整理過程不改外部行為。**
   - 演出、介面、效能都和改動前相同。
   - 唯一的內部例外是 S4 的語意名稱（D2）：狀態機的 effect 改說「驚訝」，不再說 `Surprised`，由 rig profile 一對一對應回原本的名稱。
4. **順序（D1）**：S0～S4 → S4b 把新架構同步到擬人化計畫書 → 擬人化 P1～P4（新檔直接放進新層）→ S5 拆 `app.js`。
5. **型別檢查（D4）**：用 JSDoc＋`// @ts-check`，以 `tsc --noEmit` 檢查，不改執行方式、不加打包。每個階段把它負責的層納入檢查範圍，維持 0 錯誤。

## 1. 分層一張圖

```
              ┌──────────── main 行程（Node＋Electron）─────────────┐
              │ main/index.js 組裝根                                │
              │ bus · ipc · role-window · chat-window · telemetry · │
              │ settings · app-protocol · debug(只在 CAPTURE_DIR)    │
              └───────────────▲──────────────────────────────────────┘
                              │ IPC（頻道只在 preload 與 main/ipc 出現）
              ┌───────────────┴──────────┐
              │ preload/role.cjs chat.cjs│  sandbox，只做 contextBridge
              └───────────────▲──────────┘
┌──────────────────── renderer 行程（DOM＋WebGL）─────────────────────┐
│ view       renderer/role/app.js（組裝）、widgets、pointer、probe   │
│            renderer/chat/chat-app.js                                │
│   ↓                                                                 │
│ animation  renderer/animation/：語意名稱 → rig 名稱、動作／表情／  │
│            手勢佇列、參數疊加、進退場（擬人化的 mixer／ambient／    │
│            face／procedural）                                        │
│   ↓                                                                 │
│ engine     renderer/engine/：PIXI、pixi-live2d-display、修補、      │
│            物理時鐘、頂點內插、幀迴圈                               │
│   ↓                                                                 │
│ vendor     Cubism Core、pixi（鎖版本）                              │
└─────────────────────────────────────────────────────────────────────┘
        兩個行程都可以 import ↓（純 JS，不碰 DOM／Electron／PIXI）
┌─────────────────────────────────────────────────────────────────────┐
│ domain   角色狀態機（只說語意名稱）、聊天紀錄、幀率閘門、注意力導演 │
│ rig      模型資料：語意名稱 → rig 名稱、參數 ID、版面、頭部範圍      │
│ shared   與後端的合約（訊息型別、上限、expand 原因）                │
└─────────────────────────────────────────────────────────────────────┘
dev/     量測與除錯（motion-metrics），正式執行不載入
```

**依賴規則**（由 S2 的 `layers.test.js` 自動檢查）：

| 層 | 可以 import | 不可以 |
|---|---|---|
| `shared/`、`rig/` | 無（純資料） | 任何其他層 |
| `domain/` | `shared/` | `rig/`（只說語意名稱，不知道是哪個模型）、`electron`、`node:*`、`renderer/`、`main/`；不可碰 `document`／`window`／`PIXI` |
| `renderer/engine/` | `rig/` | `domain/`、`main/` |
| `renderer/animation/` | `engine/`、`rig/` | `domain/`、`main/` |
| `renderer/role|chat/`（view） | 以上全部 | `main/`、`electron` |
| `main/` | `domain/`、`shared/`、`node:*`、`electron` | `renderer/` |
| `dev/` | `domain/`、`shared/`、`rig/` | 不被任何正式程式 import |

- `domain/` 和 `engine/` 互不認識，只透過 **effect 物件**（例如 `{type:'motion', name:'surprised'}`）溝通。
- 語意名稱翻成 rig 名稱這件事，只在 `animation/` 做一次。
- 這相當於後端的分工：`fast_path` 只負責比對，`actions` 負責執行。

## 2. 現況問題

| # | 問題 | 位置 | 影響 |
|---|---|---|---|
| Q1 | 從目錄看不出程式跑在哪個行程 | 根目錄混放 main 用的 `main.js`／`chat.js`／`protocol.js`、預載腳本 `preload.cjs`、畫面用的 `state.js`（靠 `LIB_FILES['/state.js']` 單檔開放） | 新檔不知道放哪裡；擬人化計畫的 `attention.js`、`motion-metrics.js` 也只能放根目錄 |
| Q2 | `main.js` 職責太多 | 一個檔同時處理：視窗、聊天室視窗、匯流排與重連、`ui.jsonl`、settings、IPC、選單／快捷鍵、除錯鉤子 | 除錯鉤子 `PAGE_STATE` 用字串拼 DOM id，畫面改一個 id，除錯就會悄悄壞掉 |
| Q3 | `app.js` 的 `main()` 是一個 300 多行的閉包 | 包含：載模型、建 PIXI、裝修補（順序有依賴）、三個佇列、視線疊加、進退場、點擊穿透／拖曳、DOM 接線、幀迴圈、效能統計、`__ui` | 無法單獨測試；擬人化 P2／P3 還要再往裡面加 |
| Q4 | rig 的事實散落 | 動作清單：`app.js` 的 `NEEDED_MOTIONS` 和 `state.js` 的 `MOTIONS` 各一份<br>手勢：`GESTURE_EXPRESSIONS` 和 `GESTURES` 各一份<br>版面與參數：`REGION`、`MODEL_H`、`HEAD_FRACTION`、`ParamAngleX`… 寫死在 `app.js` | 這次 fullrig03 升級動了 `state.js` 85 行、`app.js` 173 行 |
| Q5 | 協定常數沒有單一來源 | `ALLOWED_SEND` 對應 `ui_bus.UI_MESSAGE_TYPES`<br>`CMD_MAX_CHARS` 對應 `UI_COMMAND_MAX_CHARS`<br>`CHOICES_MAX` 對應 `FIND_RESULTS_MAX`<br>`EXPAND_REASONS` 在 `state.js`、`chat.js` 各一份 | 後端改了、前端沒改時，沒有任何測試會失敗 |
| Q6 | IPC 頻道名稱只是散落的字串 | `preload.cjs`、`chat-preload.cjs`、`main.js` | 打錯字不會報錯，只是收不到訊息 |

## 3. 搬運對照表

| 現在 | 之後 | 階段 |
|---|---|---|
| （新增） | `shared/contract.js`：訊息型別、上限、`EXPAND_REASONS` | S1 |
| `main.js` | `main/index.js`（只做組裝）＋ `main/{bus,ipc,role-window,chat-window,telemetry,settings,debug}.js` | S2 搬、S3 拆 |
| `protocol.js` | `main/app-protocol.js` | S2 |
| `preload.cjs`、`chat-preload.cjs` | `preload/role.cjs`、`preload/chat.cjs` | S2 |
| `state.js` | `domain/character.js` | S2 |
| `chat.js` | `domain/chat-log.js` | S2 |
| `renderer/frame-rate.js` | `domain/frame-gate.js`（純邏輯） | S2 |
| `motion-metrics.js` | `dev/motion-metrics.js` | S2 |
| `renderer/{render-buffers,drawable-colors}.js` | `renderer/engine/compat/` | S2 |
| `renderer/{physics-clock,vertex-interp}.js` | `renderer/engine/` | S2 |
| `renderer/app.js`、`ui.js`、`index.html`、`style.css` | `renderer/role/app.js`、`widgets.js`、`index.html`、`style.css` | S2 |
| `renderer/chat-app.js`、`chat.html`、`chat.css` | `renderer/chat/` | S2 |
| （新增） | `tsconfig.json`、`package.json` 的 `typecheck` 腳本 | S2 |
| （新增） | `rig/fullrig03.js`、`rig/check.js`（`checkRig`） | S4 |
| `app.js` 的 `bridgeRenderOrders`、載模型、建 PIXI | `renderer/engine/{stage,load-model,compat/render-orders}.js` | S5 |
| `app.js` 的佇列、視線、進退場 | `renderer/animation/animator.js` | S5 |
| `app.js` 的點擊穿透／拖曳、幀迴圈、`__ui` | `renderer/role/pointer.js`、`renderer/engine/loop.js`、`renderer/role/probe.js` | S5 |
| 擬人化的 `attention.js` | `domain/attention.js` | 擬人化 P3 |
| 擬人化的 `mixer`／`ambient`／`face`／`procedural` | `renderer/animation/` | 擬人化 P1／P2 |

連帶要改的地方：
- `package.json` 的 `main` 改成 `main/index.js`。`services.py` 是用 `electron desktop/` 啟動，所以它不用改。
- 兩個 html 的 `<script>`／`<link>` 路徑。
- `scripts/ui_motion.py` 呼叫的 `node desktop/motion-metrics.js` 要改成 `dev/` 的新路徑。
- `scripts/ui_demo.py`、`ui_perf.py` 註解裡的路徑。
- README 與 AGENTS.md 的 `desktop/` 段落。

## 4. 規則、rig profile 與語意名稱

**`app://` 白名單（安全邊界）**：
- `ROOTS` 改成：`/renderer/`、`/domain/`、`/rig/`、`/shared/`、`/vendor/`、`/model/`。
- `LIB_FILES` 只剩 `pixi.min.js` 和 `cubism4.min.js`。
- **`main/`、`preload/`、`dev/`、`node_modules/` 不可經 `app://` 讀到**，由 `protocol.test.js` 加案例驗證。

**rig profile（S4）**：key 是語意名稱，value 是這個模型裡的名稱。

```js
// rig/fullrig03.js
export default {
  motions:     { idle: 'Idle', surprised: 'Surprised', curious: 'Curious', nod: 'Nod', shake: 'Shake', happy: 'Happy', talk: 'Talk' },
  expressions: { neutral: 'neutral', happy: 'happy', pout: 'pout', sleepy: 'sleepy' },
  optional:    { surprised: 'surprised', starEyes: 'star_eyes', poutLeft: 'pout_left' },   // 缺了只警告、不演
  gestures:    { armLeftUp: 'arm_l_up', armRightUp: 'arm_r_up', chinRest: 'chin_rest' },
  params:      { angleX: 'ParamAngleX', angleY: 'ParamAngleY', angleZ: 'ParamAngleZ', bodyZ: 'ParamBodyAngleZ', eyeX: 'ParamEyeBallX', eyeY: 'ParamEyeBallY' },
  layout:      { region: { x: 380, y: 0, w: 1280, h: 2048 }, height: 420, headFraction: 0.30 },
};
```

**語意名稱（D2）**：
- `domain/character.js` 只說語意名稱，effect 統一用 `name` 欄位：
  - `{type:'motion', name:'surprised'}`（原本是 `group:'Surprised'`）
  - `{type:'expression', name:'starEyes'}`
  - `{type:'gesture', name:'chinRest'|'none'}`
- `animation/` 查 profile 把語意名稱換成 rig 名稱。查不到的選用表情照現在的做法略過。
- 除錯介面（`__ui.activeMotion`、`probe`、`motionTrace`）**繼續回報 rig 名稱**，也就是實際在播的動作。這樣 `ui_demo`、`ui_motion` 的比對不用改。
- `checkRig(profile, model3json, vocab)` 是純函式，檢查兩件事：
  - profile 涵蓋 domain 的每個語意名稱。
  - 必要的動作／表情確實在 model3.json 裡。

  缺必要項目就拋錯，缺選用項目只警告。這段原本寫在 `app.js` 裡。
- 換模型＝新增一份 profile，再把 `config.UI_MODEL_PATH` 指向新模型。狀態機不用動。

**型別檢查（D4）**：
- `devDependencies` 新增兩個套件，都鎖死確切版本：
  - `typescript`
  - `@types/node`：`main/` 需要 Node 型別，主版本跟 Electron 45 內建的 Node 相同，安裝時用 `process.versions.node` 確認。`electron` 套件本身就有型別。
- `tsconfig.json`：`allowJs`、`checkJs`、`noEmit`、`module: nodenext`，不開 `strict`。目的是抓搬移後的錯 import、錯欄位、錯參數，不是全面型別化。
- 檢查範圍：S2 是 `shared/ domain/ dev/`，S3 加 `main/`，S4 加 `rig/`，S5 加 `renderer/`（PIXI／Cubism 的全域變數在 `types/globals.d.ts` 宣告）。

## 5. 實作階段

每個階段各自一個 commit，可以單獨 revert。每個階段結束都要跑 §6 的 A1～A3、A7、R1。

| 階段 | 內容 |
|---|---|
| S0 基線 | 在 `c1bc3e8` 上：<br>• 記下 `node --test` 的總數<br>• 跑 `ui_demo --capture` 存到 `<暫存>/before/`<br>• 跑 3 次 `ui_perf.py`，記下數字 |
| S1 合約與頻道測試 | 新增 `shared/contract.js`，`main.js`、`state.js`、`chat.js` 改從它取常數<br>新增 `tests/unit/test_ui_contract.py`（A4）與 `desktop/test/ipc-channels.test.js`（A5） |
| S2 目錄搬運 | 依 §3 用 `git mv` 搬移，只改 import、白名單、html 路徑、`package.json`、`ui_motion.py`<br>新增 `test/layers.test.js`（A2）<br>安裝 TypeScript、建立 `tsconfig.json`（A7）<br>同步 README／AGENTS.md |
| S3 拆 `main.js` | 拆成 §3 的 7 個模組，`index.js` 只負責組裝<br>`debug.js` 改成呼叫 `window.__ui.probe()`，不再拼 DOM 選擇器（`probe` 在畫面端回傳和現在 `PAGE_STATE` 相同的欄位） |
| S4 rig profile＋語意名稱 | 照 §4 做；`app.js` 的 `NEEDED_*`、`GESTURE_EXPRESSIONS`、`REGION`、參數 ID 改從 profile 讀 |
| S4b 同步擬人化計畫書 | 修改 `docs/桌面角色擬人化（注視與聆聽）：實作計畫.md`：<br>• §4.2 模組表的路徑改成新層（`domain/attention.js`、`renderer/animation/*`）<br>• 「state.js 不改」改成「`domain/character.js` 不改；導演從 animator 取目前動作的**語意名稱**」<br>• §4.9 與指標定義裡 `motion-metrics.js` 的路徑改到 `dev/`<br>• P3 的 `PAGE_STATE` 改成 `probe`<br>• §6 各階段的檔案欄跟著改<br>• 加一條規則：新檔要符合 §1 的依賴規則，並納入型別檢查 |
| S5 拆 `app.js` | 擬人化 P4 之後才做。照 §3 拆檔<br>**修補的安裝順序不變**：`bridgeRenderOrders` → `installRenderBuffers` → `installStateCache` → `installDrawableColors`<br>`RENDER_OPT`、`PROCEDURAL` 的開關都保留<br>跑 R3 效能 A/B |

## 6. 驗收計畫

### 6.1 自動化測試（離線，實作者執行並貼出輸出）

| # | 項目 | 指令 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| A1 | 既有測試行為不變 | `cd desktop && node --test` | • 0 fail<br>• 總數＝S0 基線＋本計畫新增的數目<br>• **S1～S3、S5**：既有測試檔除了 import 行沒有任何差異（`git diff -M HEAD -- desktop/test/` 逐檔檢查）<br>• **S4**：`state.test.js` 的變更只能是名稱替換。用暫存腳本把新檔的語意名稱依 profile 換回 rig 名稱、`name` 換回 `group` 之後，與舊檔的 diff 必須為空（腳本不入版控） | 修程式；**不得**改既有預期 |
| A2 | 分層規則 | 同上（`layers.test.js`：逐檔掃 `import` 與 `document\|window\|PIXI\.` 這幾個關鍵字） | • §1 表格的違規數為 0<br>• 另有一組故意違規的字串要能被偵測到（證明檢查真的有在檢查） | 把程式搬到正確的層 |
| A3 | 白名單 | 同上（`protocol.test.js` 加案例） | • `app://ui/main/index.js`、`/preload/role.cjs`、`/dev/motion-metrics.js`、`/node_modules/electron/package.json`、`/rig/../main/index.js` 都回傳 `null`<br>• `/domain/character.js`、`/rig/fullrig03.js`、`/shared/contract.js`、`/lib/pixi.min.js` 都回傳檔案路徑 | 修 `ROOTS`／`LIB_FILES` |
| A4 | 前後端合約一致 | `source .venv/bin/activate && pytest tests/unit/test_ui_contract.py` | 用 regex 從 `shared/contract.js` 取出的值和後端相同：<br>• 訊息型別集合＝`ui_bus.UI_MESSAGE_TYPES`<br>• 指令上限＝`config.UI_COMMAND_MAX_CHARS`<br>• 清單上限＝`config.FIND_RESULTS_MAX`<br>另外，故意改值的暫存副本要讓測試失敗（只在開發時驗一次，不入版控） | 改前端常數；後端不動 |
| A5 | IPC 頻道兩邊對應 | `cd desktop && node --test test/ipc-channels.test.js` | `preload/*.cjs` 裡 `ipcRenderer.send/invoke` 的頻道集合，等於 `main/ipc.js` 裡 `ipcMain.on/handle` 的頻道集合；`webContents.send` 和 `ipcRenderer.on` 的集合也相同 | 修頻道名稱 |
| A6 | 後端與文件 | `pytest` | 0 failed（包含檢查 docs 沒有簡體字的測試） | 修 |
| A7 | 型別檢查 | `cd desktop && npm run typecheck` | • 該階段範圍內 0 錯誤<br>• 新增的 `@ts-ignore`／`@ts-expect-error`／`@ts-nocheck` 為 0（`grep -rn` 計數）；真的需要時逐一列出理由，請使用者同意 | 修程式或補 JSDoc |
| A8 | rig 涵蓋（S4） | `cd desktop && node --test test/rig.test.js` | • `checkRig` 對 fullrig03 的 profile 與真的 model3.json 結構（測試用的精簡副本）通過<br>• 少一個必要動作會拋錯<br>• 少一個選用表情只回傳警告<br>• domain 新增一個沒對應的語意名稱會拋錯 | 修 profile 或 `checkRig` |

### 6.2 真實環境（本機 Electron，實作者執行並貼出輸出）

| # | 項目 | 指令／步驟 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| R1 | 演出與介面不變 | `python scripts/ui_demo.py --capture <暫存>/after`，以及 `--interact-test`、`--choices-test`、`--chat-test` | • 13 張的演出比對全部 PASS，三個測試全部 PASS<br>• 用暫存腳本比對 `before/` 和 `after/` 的每個 `.json`：排除 `frames`、`perf`、`lastOpen` 的耗時欄位後，`motion`、`snap.expression`、`gesture`、`input`、`quick`、`choices`、`choiceRows`、聊天室 `rows`／`text` 必須相同；`poseT` 差 ≤ 0.01<br>• S4 之後 `snap` 裡的名稱會變成語意名稱，比對時依 profile 換算後必須相同 | 對照是哪一層搬錯 |
| R2 | 啟動與記錄 | `python main.py --ui`，說喚醒詞加 2 句；另外試拖曳、右鍵選單、`Alt+Space` 輸入框 | • console 有 `[ui] ready`，沒有 error<br>• `logs/runtime/ui.jsonl` 的每一行都有 `bus_ms`，非 `hello` 的行有 `apply_ms`（不是 null）<br>• 拖曳後重開，位置有被記住 | 修 |
| R3 | 效能不退步（S2、S5） | `python scripts/ui_perf.py`，改動前後交替各跑 3 次 | • 出場每秒峰值：3 次平均的差 ≤ **2 個單核百分點**（D3）<br>• 幀間隔 p95 ≤ **18.5 ms**（2026-11-21 使用者同意由 18 放寬 0.5 ms，見 10.3）<br>• 潛伏停格期間 0 幀<br>• 每幀 JS p95 ≤ 8 ms | 找出是哪個包裝或閉包造成的；V8 最佳化的問題見 `render-buffers.js` 的註解；**不自行放寬** |
| R4 | 量測工具仍可用（S2） | `python scripts/ui_motion.py --skip-listen --minutes 1` | 正常產出指標（不判定數值，只確認新路徑可以執行） | 修 `ui_motion.py` 的路徑 |

### 6.3 人工驗收（由使用者判斷）

| # | 看什麼 |
|---|---|
| U1 | 角色外觀、進退場、泡泡、選擇清單、聊天室展開與收回，和整理前看不出差別 |
| U2 | 照新目錄找一個功能（例如「點頭部會怎樣」「換模型要改哪裡」），覺得是否比較好找 |

### 6.4 不在本次範圍

- 任何外部行為、效能優化、演出參數的改變。
- TypeScript 的 `strict` 模式、把 `.js` 改寫成 `.ts`、打包工具（esbuild 等）。
- 後端程式、`ui_bus` 協定、模型檔。

## 7. 風險

- **白名單是安全邊界**：搬移時若把 `ROOTS` 開得太寬（例如直接開放整個 `desktop/`），畫面就能讀到 `main/`。A3 用來擋這個情況。
- **語意名稱漏對應**：domain 說了某個名稱、profile 卻沒有對應時，那個演出會靜靜地不播。A8 在載入時拋錯，R1 比對演出。
- **修補的順序與 V8 最佳化**：`render-buffers.js` 曾經因為每次繪製新建 closure 造成 CPU 尖峰（fullrig03 §13）。S5 只搬程式、不改呼叫形狀，並用 R3 驗證。
- **sandbox 預載腳本不能 require 本地檔案**：頻道常數沒辦法讓 preload 和 main 共用同一個檔，所以改用 A5 的掃描測試保證一致。
- **型別檢查在 S5 可能很吵**：PIXI 6 和 pixi-live2d-display 0.4.0 是以全域變數載入，內部 API（`internalModel`、`coreModel`）的型別不完整，可能需要在 `types/globals.d.ts` 補宣告。若會用到大量 `any`，先回報使用者。
- STT 與實際語音沒有自動測試，R2 需要人工執行。

## 8. 已確認的決策（2026-11-20）

| # | 決定 |
|---|---|
| D1 | 擬人化 P0 已提交（`c1bc3e8`）。順序是 S0～S4 → S4b 同步擬人化計畫書 → 擬人化 P1～P4 → S5 |
| D2 | 狀態機改成只說語意名稱，由 rig profile 對應回 rig 名稱（§4），做法保持簡潔 |
| D3 | R3 的門檻：出場峰值 3 次平均的差 ≤ 2 個單核百分點 |
| D4 | 安裝 `typescript`（以及 `main/` 需要的 `@types/node`），用 `checkJs` 做型別檢查 |

## 9. 回退方式

- S1～S5、S4b 各自一個 commit，可以逐一 revert。
- S2 是純 `git mv` 加上路徑修改，revert 後就回到現在的結構。
- S4 revert 後，effect 會回到 rig 名稱。

## 10. 實作結果

2026-11-20～21 完成 S0～S4 與 S4b；已依 S1～S4、S4b 分開提交到 main（見 `git log`）。S5（拆 `app.js`、`renderer/animation/`、型別檢查納入 `renderer/`）依 D1 排在擬人化 P4 之後。

### 10.1 實際做了什麼

| 階段 | 結果 |
|---|---|
| S0 | 基線：`node --test` 101 pass、`ui_demo --capture` 13 張存成 `before/`、`ui_perf` 3 次 |
| S1 | `shared/contract.js`（訊息型別、`COMMAND_MAX_CHARS`、`CHOICES_MAX`、`EXPAND_REASONS`）；`tests/unit/test_ui_contract.py` 5 項比對後端 |
| S2 | 純 `git mv` 與路徑修改：`state.js`→`domain/character.js`、`chat.js`→`domain/chat-log.js`、`renderer/frame-rate.js`→`domain/frame-gate.js`、`motion-metrics.js`→`dev/`、`renderer/*` 依角色分進 `role/`、`chat/`、`engine/`、`engine/compat/`；`layers.test.js` |
| S3 | `main.js` 拆成 `main/` 10 個檔（`index`、`app-protocol`、`bus`、`role-window`、`chat-window`、`ipc`、`telemetry`、`settings`、`console`、`debug`）；`preload/`；除錯鉤子改讀 `window.__ui.probe()`／`__chat.probe()`；`ipc-channels.test.js`、`telemetry.test.js`；白名單改成 `appRoutes`（含 `/rig/`，`protocol.test.js` 加 3 項真實設定案例） |
| S4 | `rig/fullrig03.js`（profile）與 `rig/check.js`（`checkRig`）；`domain/character.js` 只說語意名稱、effect 用 `name`；`renderer/role/app.js` 的動作、表情、手勢、參數 ID、版面改讀 profile；`test/rig.test.js` 6 項；`typescript` 7.0.2、`@types/node` 24.19.2（鎖死版本）、`npm run typecheck` |
| S4b | 擬人化計畫書的檔案位置、層別、`probe()`、`dev/` 路徑、測試基線同步（設計與驗收不變） |
| 文件 | AGENTS.md §3.3 分層表與基線、§6 新增「換模型」一列；README 的 `desktop/` 段落與指令 |

### 10.2 驗收結果（實際執行）

| # | 結果 |
|---|---|
| A1 | `node --test` **142 pass、0 fail**（基線 101＋新增 41）。S1～S3 的既有測試檔只有 import 行差異（`git diff -M HEAD` 逐檔確認）。S4 的 `state.test.js` 用暫存腳本反向代換（`e.name`→`e.group` 僅限 motion 三處、`starEyes`→`star_eyes`、`poutLeft`→`pout_left`）後與舊檔 diff 為空 |
| A2 | `layers.test.js` 違規 0；故意違規的字串都被偵測到 |
| A3 | `protocol.test.js`：`main/`、`preload/`、`dev/`、`node_modules`、`/rig/../main/` 回傳 null；`domain/`、`rig/`、`shared/`、`/lib/*.min.js` 回傳檔案路徑 |
| A4 | `tests/unit/test_ui_contract.py` 5 項通過；故意改值的副本會失敗（開發時驗過一次，不入版控） |
| A5 | `ipc-channels.test.js` 通過 |
| A6 | `pytest`：**1730 passed、5 skipped、0 failed**（基線 1725＋5 項合約測試） |
| A7 | `npm run typecheck` 0 錯誤（範圍 `shared domain dev rig main preload`）；`@ts-ignore`／`@ts-expect-error`／`@ts-nocheck` 為 0 |
| A8 | `rig.test.js` 6 項通過（缺必要動作／表情拋錯、缺選用表情或手勢只警告、domain 多一個語意名稱拋錯）；另用真的 `RushiaHD.model3.json` 跑 `checkRig`：通過、0 警告 |
| R1 | `ui_demo --capture`：13 張演出比對全 PASS，和 `before/` 逐張比對 **13/13 相同**；`--interact-test` 6 項、`--choices-test` 8 項、`--chat-test` 16 項全 PASS |
| R2（部分） | `logs/runtime/ui.jsonl` 最近 400 行：每行都有 `bus_ms`，387 行非 `hello` 的 `apply_ms` 全部不是 null |
| R3 | 見 10.3 |
| R4 | `python scripts/ui_motion.py --skip-listen --minutes 1` 正常產出指標（新路徑 `dev/motion-metrics.js` 可用） |

### 10.3 R3 效能

**結論：同條件的新舊對照沒有差異，D3 通過。** 但原本照字面做的量測方式有一個量測假象，過程與限制如下。

- 同一個量測 wrapper 下（舊版＝`git archive HEAD` 取出的暫存副本；新版＝目前程式，跑了 7 次，包含放在不同位置、每幀常數改回字面值的變體）：

| 指標 | 舊版（n=5） | 新版（n=7） | 差 |
|---|---|---|---|
| 出場 CPU 平均（單核 %） | 31.14 | 31.04 | −0.10 |
| 出場 CPU 每秒峰值（單核 %，D3 門檻 ≤ +2） | 37.02 | 35.76 | **−1.26** |
| 每幀 compute／render（ms） | 1.13／0.38 | 1.12／0.38 | 0／0 |
| 幀間隔 p95（ms） | 18.04 | 18.06 | +0.02 |
| GPU 程序 CPU 秒（整段量測） | 13.90 | 13.91 | +0.01 |
| renderer CPU 秒（整段量測） | 14.80 | 14.77 | −0.03 |

- **量測假象**：最初直接用 `python scripts/ui_perf.py` 前後比較，新版的 CPU 看起來高約 10%（平均 +2～+5 單核點）。後來證實：同一份新程式、同一個目錄，只改量測 wrapper 的取樣方式，就從 33.6%（compute 1.22 ms）變成 29.5%（compute 1.10 ms）；舊版程式走同樣的「不修補」路徑，page 內耗時也是 compute 1.20 ms／render 0.41 ms。也就是說這個差異來自**量測路徑**，新舊程式碼都一樣。機制**推測**是取樣活動多寡改變了 CPU 的省電／頻率狀態，**這一點沒有驗證**。
- 由此得到的提醒：`ui_perf.py` 的絕對數字對「背景有多少其他取樣活動」敏感，**不同時段、不同做法的數字不能直接相減**（S0 基線、S2、S4 的 plain 跑法互相比較時，差異要打折扣看）。要比較前後，必須同一個 session 交替執行。
- **幀間隔 p95：本計畫的 R3 門檻 2026-11-21 由 ≤ 18 ms 放寬為 ≤ 18.5 ms（使用者決定）。** 依據：新舊兩版在現在這個時段都是 18.0～18.1（S0 基線時為 17.3～17.7），差異與這次整理無關；放寬後新版 18.06 **通過**。放寬只適用本計畫 R3：`scripts/ui_perf.py` 的 `LIMITS` 仍是 18（該工具與 fullrig03 R4、擬人化 B11 共用，B11 寫明門檻本身不改），所以工具輸出這一項仍會標 FAIL，判定以本表為準。
- 潛伏停格 CPU 整台平均：新版 0.11～0.14%，舊版 0.09～0.13%，門檻 0.125%；兩版都會在邊緣上下波動，與前述 S2 時的觀察相同。

### 10.4 與計畫不同的地方

- `rig/fullrig03.js` 的 `params` 多了 `eyeOpen`（原計畫的範例沒列，但 `app.js` 軌跡量測用到）；版面（`layout`）放在 profile 裡，`checkRig(profile, model3, vocab)` 回傳 `{ warnings }`。
- **手勢缺少只警告、不拋錯**（和原本的行為一致；原計畫沒寫清楚）。
- 除錯介面 `__ui.snapshot()` 會把 `expression`、`gesture` 換回 rig 名稱，所以 `ui_demo.py` 的 `EXPECTED` 與 `cmp_capture` 不用改。
- `main/` 實際拆成 10 個檔（計畫列的是「約 8 個」）。

### 10.5 尚未驗證／風險

- **R2 的人工部分**：2026-11-21 用真的 CGEvent 鍵盤與滑鼠事件實測通過——`Alt+Space` 開關輸入框、拖曳 −300 px 視窗左移 300 且不誤送 wake、`settings.json` 記下 x 且重啟後位置相同、右鍵選單彈出並可用「聊天室」「暫停聽指令」（暫停需後端回送 `paused`）。**仍未驗證**：`python main.py --ui` 真人說喚醒詞加 2 句（需要麥克風）。
- U1、U2（外觀與好不好找）要你判斷。
- `--chat-test` 的「20 秒自動收回」會被游標取消：聊天室視窗出現時游標若停在上面，`mouseover` 就算互動（`chat-app.js`）。2026-11-21 對照實測：游標在遠處 → 20 秒後收回；游標在聊天室上 → 仍開著。整理前就存在的設計行為；跑 `--chat-test` 時請勿碰滑鼠。
- R3 的量測限制（見 10.3：絕對數字不同時段不能直接相減）。
- 已依階段提交到 main（見 `git log --oneline`）。
