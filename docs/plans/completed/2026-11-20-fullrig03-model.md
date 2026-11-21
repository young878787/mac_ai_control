# 桌面角色模型升級（runtime-fullrig03）：動作、表情與物理

> **歸檔（2026-11-21 結案）**：原檔名 `docs/桌面角色模型升級（fullrig03）：實作計畫.md`。以下內文保留結案時的原樣；文中的路徑、章節編號與「待確認／尚未驗證」都是當時的狀態，目前有效的決策見 `docs/decisions/`。

狀態：**已實作，待使用者驗收**（2026-11-20，基準 HEAD `635a741`；結果見 §13）。D1＝整台電腦 5%、走甲路線；D2 照提案；D5（潛伏停格）隨甲路線一併採用。O5 目前開著，要使用者看過 U2／U6 才決定是否保留。

## 0. 結論

1. **新模型不必改渲染器就能載入**。用 Cubism Core 6.0.1 實際讀過：沒有 offscreen、88 個 drawable 全是 Normal 混合，固定步長物理轉接器的三個前提都成立。只換模型路徑時，畫面已實際跑過（§3.2）。
2. 新模型多了 **43 個參數、19 組物理（共 28 組）、4 個表情**（舉左手、舉右手、托腮、星星眼）。另外原本就有、但狀態機沒用到的還有 `surprised`、`pout_left` 兩個表情和 `Talk` 動作。§5 提案把它們接進狀態機。手臂切換類的表情要放在**獨立的手勢層**，否則會把臉部表情蓋掉。
3. **28% 不是程式錯誤，主要也不是模型**（§3.3 對照實驗）。成本有三塊：
   - **每出一幀的固定成本**：Chromium 的 renderer → GPU 程序 → ANGLE／Metal → 合成。Electron 透明視窗只清畫面、60 fps 就要 **20.9%**。
   - **Live2D 計算**：WASM 版 Core、物理、動作，每幀約 2 ms，60 fps 時約 12%。
   - **Live2D 繪製**：官方 Web 框架和 pixi-live2d-display 的做法都是每個 drawable、每幀重新上傳頂點、UV、索引。
   - 新模型完整流程 60 fps 實測 **37.5%**。
4. **5% 的單位要先確認**（§3.5）：本計畫的數字都是「一顆核心」的百分比。換算成 8 核心 M1「整台電腦」的百分比，現在 60 fps 約 4.5～4.7%，已經在 5% 以內。網頁 Live2D 看起來省，主要就是因為用整台電腦當單位；實測 WebKit 反而比 Electron 更耗。
5. 如果 5% 指的是一顆核心，那麼**「穩定 60 fps」加上「最高峰 5%」在這台 M1 上用任何路線都達不到**：原生 macOS（Swift＋Metal）透明視窗只畫一個方塊、60 fps，就已經 **7.7～8.3%**。§6 列出三條路線，推薦「Electron＋渲染優化＋潛伏停格」：出場時全程 60 fps，峰值推估 **33～36%**，若再加上 O5（Core 以 30 Hz 計算、頂點內插到 60 fps）可再降到約 **27～30%**；潛伏時約 0～1%。

## 1. 要解決的問題

- 目前用的是 `Rushia_hd/runtime-swing`（[config.py](../mac_ai/config.py) `UI_MODEL_PATH`）。新的完整骨架版 `runtime-fullrig03` 加了手臂、袖子、飾品的骨架與物理，但現在的程式沒有用到新的表情，也沒有讓新物理的擺動被完整看見：
  - 閒置出場時是 12 fps，動作結束後袖子、披肩還在擺，畫面卻已經降成 12 fps。
- 狀態機（[state.js](../desktop/state.js)）只用 6 個動作和 4 個表情（neutral／happy／pout／sleepy）。
- 臉部表情和手臂姿勢目前共用同一個佇列（[app.js](../desktop/renderer/app.js) 的 `expressionQueue`），直接把手臂表情放進去，臉部表情會被淡出。

## 2. 已定案的決策（2026-11-20 使用者確認）

| # | 決策 |
|---|---|
| A1 | 以 `Rushia_hd/runtime-fullrig03` 這版匯出結果為定版模型，不再用 psd2live 重新匯出，模型檔不修改 |
| A2 | 新的表情與動作接進狀態機（怎麼對應見 §5，待 D2 確認） |
| A3 | 有畫面在動時一律穩定 60 fps（第二版：取代原本「只在物理收尾期間 60 fps」；不再使用 12／5 fps 的分級） |
| A4 | 目標：完整運行時最高峰 CPU 5%，並以此為優化方向（單位見 A6） |
| A5 | 說話動作 Talk 沒有聲音也可以用（原 D4） |
| A6 | CPU 5% 指**整台電腦**（8 核心 M1 等於單核 40%）；走路線甲（Electron＋渲染優化＋潛伏停格）（原 D1） |
| A7 | §5.2 的事件對應照提案（原 D2）；潛伏停格隨路線甲採用（原 D5） |

## 3. 已實測的前提（2026-11-20，本機）

### 3.1 相容性（Core 6.0.1 直接讀 moc3，node 執行）

| 項目 | runtime-swing（現行） | runtime-fullrig03 | 結論 |
|---|---|---|---|
| moc3 版本 | 5 | 5 | 相容 |
| Drawables／頂點 | 58／6,844 | 88／10,896 | |
| 被遮罩的 drawable／遮罩組數 | 4／2 | 6／2 | 遮罩緩衝 1024 夠用 |
| offscreen／混合模式 | 0／全 Normal | 0／全 Normal | `bridgeRenderOrders` 可用 |
| 參數 | 40 | 83（只有新增，沒有移除） | 既有程式用到的參數都還在 |
| 貼圖 | 4 張 4096² | 6 張 4096² | RSS 反而較低（見 §3.2） |
| 外框（畫布 px，含手臂伸到最遠） | x 653–1428 | x 636–1444、y 12–2037 | 仍在 `REGION`（x 380–1660）內，視窗與點擊區不用改 |
| 物理轉接器前提（輸出不重複、Weight 100、輸入輸出不交集） | 成立 | 成立 | [physics-clock.js](../desktop/renderer/physics-clock.js) 不會拋錯 |

### 3.2 效能（`scripts/ui_perf.py --seconds 20`，另以暫存腳本拆出各程序 CPU 與每幀耗時；只切換模型，程式未改）

| 量測 | swing | fullrig03 |
|---|---|---|
| 潛伏閒置 5 fps：CPU 合計 | 7.5% | 8.4% |
| └ main／GPU／renderer | 1.0／3.5／3.1 | 1.0／3.1／4.1 |
| 出場閒置 12 fps：CPU 合計 | 15.3% | 16.7% |
| └ main／GPU／renderer | 1.6／6.6／7.0 | 1.7／5.9／9.4 |
| 動作中 30 fps（每秒送一次 `executing`）：CPU 合計 | 27.4% | 28.2% |
| 每幀 JS 平均耗時：physics／coreUpdate／render | 0.21／1.09／1.04 ms | 0.58／2.22／1.22 ms |
| 每幀繪製耗時 p95 | 3.3 ms | 5.8 ms |
| RSS | 666 MB | 481 MB |
| 進退場幀間隔 中位／p95 | 33.3／34.3 ms | 33.3／34.3 ms |

- CPU 是 Electron 全部程序（main、GPU、renderer、utility）的合計，**不含** Python 後端、LLM、STT，也不含 macOS WindowServer 的合成成本。
- `ui_perf.py` 的現行上限（潛伏 3%、出場 15%、p95 34 ms）**兩個模型都沒過**，所以現在的上限本來就沒有反映實際情況。
- 第一版推估 60 fps 約 45～55%。§3.3 已實測，新模型完整流程 60 fps 是 **37.5%**，以實測為準。

### 3.3 為什麼會到 28%：對照實驗（2026-11-20，實際執行）

環境：

- 本機 Apple M1、內建 60 Hz Retina，Electron 45.2.3（和 `desktop/` 同一個執行檔）。
- 視窗 360×560、`type: 'panel'`、Retina 2 倍，和正式視窗相同。

做法：

- 暫存腳本（不入版控）在同樣的視窗設定下只換渲染內容。
- 每項穩定 7 秒後，用 `ps cputime` 取 15 秒差值。
- CPU 是一顆核心的百分比，加總 Electron 各程序，**不含 WindowServer**。
- 「更新／繪製」是 JS 每幀的平均耗時。

| # | 內容 | fps | 合計 | GPU 程序 | renderer | 更新／繪製（ms） |
|---|---|---|---|---|---|---|
| E0 | 透明視窗，不畫 | 0 | 0.3% | 0.1 | 0.1 | — |
| E5 | 只跑 rAF 迴圈，不畫 | 60 | 5.3% | 1.7 | 3.5 | — |
| E4 | 原生 WebGL，只清畫面＋一塊色塊 | 60 | **20.9%** | 13.5 | 7.4 | — |
| E7 | 同 E4，`desynchronized` context | 60 | 20.9% | 13.5 | 7.3 | — |
| E1 | Pixi 畫一個圓（透明視窗） | 60 | 24.1% | 15.1 | 8.9 | —／0.53 |
| E2 | 同 E1，不透明視窗 | 60 | 24.7% | 15.5 | 9.3 | —／0.31 |
| E3 | 同 E1，關閉 `preserveDrawingBuffer` 與抗鋸齒 | 60 | 23.5% | 14.7 | 8.9 | —／0.28 |
| E6 | 同 E1，改用 timer 排程 | 60 | 25.5% | 16.3 | 9.3 | —／0.28 |
| E9 | 同 E1 | 15 | 12.5% | 6.6 | 5.8 | —／0.48 |
| L1 | fullrig03：只算（動作＋物理＋Core），不畫 | 60 | 19.7% | 1.5 | 18.1 | 2.44／— |
| L2 | fullrig03：只畫，不算（參數不變） | 60 | 33.0% | 21.8 | 11.2 | —／1.21 |
| **L0** | **fullrig03 完整流程** | 60 | **37.5%** | 15.7 | 21.7 | 2.04／0.96 |
| L3 | fullrig03 完整流程 | 30 | 26.7% | 10.5 | 16.1 | 2.65／1.42 |
| L4 | 同 L0，關閉 `preserveDrawingBuffer` 與抗鋸齒 | 60 | 37.3% | 15.3 | 21.9 | 2.10／0.88 |
| L5 | 同 L0，解析度 1 倍 | 60 | 37.8% | 15.9 | 21.9 | 2.08／0.87 |
| L6 | swing 完整流程（對照） | 60 | 35.5% | 18.5 | 16.9 | 0.96／0.78 |
| N1 | **原生 Swift＋Metal**透明 NSPanel，畫 1 個方塊（CADisplayLink） | 60 | **7.7～8.3%** | — | — | — |
| N2 | 同 N1，畫 100／300 個方塊（每個都上傳頂點） | 60 | 8.2／8.6% | — | — | — |
| N3 | 同 N1 | 30 | 4.3% | — | — | — |

解讀：

1. **不是程式錯誤**。
   - 正式程式在潛伏和出場閒置時用 timer 排程，不會讓 rAF 空轉（E5 的 5.3% 只會出現在動作播放中）。
   - 沒有重複繪製，也沒有無限迴圈。
   - §3.2 的「動作中 30 fps 28.2%」和 L3 的 26.7% 吻合。
2. **最大的一塊是 Chromium 每出一幀的固定成本，大約每 1 fps 增加 0.35%**。
   - 什麼都不畫、只清畫面（E4），60 fps 就要 20.9%，其中 GPU 程序佔 13.5%：它負責把 WebGL 指令轉成 Metal，並交給合成器。
   - 透明與否（E2）、抗鋸齒和 `preserveDrawingBuffer`（E3／L4）、解析度（L5）、`desynchronized`（E7）都**沒有**明顯影響。
   - 所以在 Electron 裡，60 fps 的下限約 21%，不管畫什麼都一樣。
3. **Live2D 計算是第二塊**：
   - 每幀 2.0～2.4 ms，在 renderer 程序裡；60 fps 約 12～14%。
   - 新模型比 swing 多一倍（2.04 對 0.96 ms），原因是 drawable、頂點、變形器、參數都變多。官方的模型效能說明也指出，物件數量和「每個物件掛幾個參數」是主要成本。
   - 模型依 A1 定版，所以這塊只能靠程式端減少不必要的工作。
4. **Live2D 繪製是第三塊**：
   - pixi-live2d-display 0.4.0，以及官方 CubismWebFramework 目前的 `cubismshader_webgl.ts`，都是所有 drawable 共用一組 buffer。
   - **每個 drawable 每幀都用 `bufferData` 重新上傳頂點、UV、索引三份資料**，再加上大約 25 次 GL 狀態呼叫。88 個 drawable 合計每幀約 2,000 次呼叫。
   - 遮罩每幀重建（只略過頂點沒變的遮罩來源）。
   - 這些呼叫都要經過 GPU 程序轉譯，L2 的 GPU 程序因此比 E4 多了 8 個百分點。
5. **原生也有下限**：Metal 加 CADisplayLink 的透明視窗，60 fps 只畫一個方塊就要約 8%，30 fps 是 4.3%。所以如果 5% 指的是**一顆核心**，「60 fps 且峰值 ≤ 5%」在這台機器上不論用 Electron 或原生都做不到；如果指的是**整台電腦**，見 §3.5。
6. 以上數字都**不含 WindowServer 的合成成本**。系統整體實際的負擔還會再高一些。

### 3.5 「網頁跑 Live2D 不太耗 CPU」的原因：單位不同，不是網頁比較省（2026-11-20，實際執行）

同一份測試頁用三種引擎在同樣大小的透明視窗裡跑。CPU 是一顆核心的百分比。

| 引擎 | 只清畫面 60 fps | fullrig03 完整 60 fps | 各程序 |
|---|---|---|---|
| Electron 45（Chromium，WebGL） | 20.9%（E4，複測 20.9%） | 37.5%（L0，複測 36.0%） | GPU 程序 15.4、renderer 20.4 |
| Electron 45（Chromium，**WebGPU**） | 23.1%（G1） | — | GPU 程序 14.7、renderer 8.3 |
| **WKWebView**（WebKit，也就是 Safari 和 Tauri 在 macOS 上用的引擎） | **28.9%**（W4） | **46.4%**（W-L0） | WebContent 23.5、GPU 程序 17.7、App 5.2 |
| 原生 Swift＋Metal | 7.7～8.3%（N1） | —（沒有原生 Live2D） | — |

結論：**放到網頁裡不會比較省**，WebKit 反而更耗，WebGPU 的出幀下限也沒有比 WebGL 低。一般覺得「網頁 Live2D 只用幾 % CPU」，比較可能是以下三個原因：

1. **百分比的單位不同（最主要）。**
   - 本計畫和 `ps`，以及「活動監視器」每個程序的 % CPU 欄，都是**一顆核心**的百分比。
   - Windows 工作管理員、活動監視器底部的「CPU 負載」、iStat Menus 顯示的是**整台電腦**的百分比。
   - 這台 M1 有 8 核心：fullrig03 跑 60 fps 的 36～37.5%，換算成整台電腦約 **4.5～4.7%**；30 fps 的 26.7% 約 3.3%；只清畫面的 20.9% 約 2.6%。
   - 桌機常見 12～16 執行緒，同樣的負擔看起來只剩 2～3%。
2. **瀏覽器把成本分散到看不到的地方。** 分頁本身的 renderer 只佔一部分；GPU 程序（Chrome 的「Helper (GPU)」、Safari 的 `com.apple.WebKit.GPU`）由所有分頁共用，大家很少把它算進去。合成成本則算在 WindowServer 上。
3. **網頁示範通常是小模型**，例如官方範例的 Hiyori、Haru。新模型每幀 Core 運算的成本是 swing 的 2 倍（L6 對 L0）。

WindowServer 的部分：本機閒置時 WindowServer 自己就有 40.9%（其他 App 造成），Electron L0 期間 47.4%、原生 Metal 期間 38.8%，波動大於差值，**無法歸因**，所以本計畫的數字都不含 WindowServer。

### 3.6 社群與官方提到的優化路線（逐項評估）

| 路線 | 來源 | 對本專案的效果 | 採用 |
|---|---|---|---|
| 閒置時降幀、不可見時暫停 ticker | [PixiJS Ticker](https://pixijs.com/8.x/guides/components/ticker)、[VTube Studio Lag Troubleshooting](https://github.com/DenchiSoft/VTubeStudio/wiki/Lag-Troubleshooting) | 有效（E9：15 fps 12.5%），但和 A3（穩定 60 fps）衝突。只用在潛伏停格 | 部分（§6.2） |
| 降低渲染解析度（render scale） | [Animaze FAQ](https://www.animaze.us/faq/animazedesktop/lightanimaze) | 對 CPU 沒有效果（L5：37.8%）。它省的是 GPU 填充率，不是 CPU | 不採用 |
| 換 Safari／WebKit 或 Tauri | [Babylon.js 論壇：Safari 與 WKWebView 效能](https://forum.babylonjs.com/t/performance-between-safari-and-wkwebview-tauri/60811)、[tauri-plugin-macos-fps](https://docs.rs/crate/tauri-plugin-macos-fps/0.1.0) | 更耗（§3.5，46.4%） | 不採用 |
| WebGPU（例如 AIRI 的網頁版） | [AIRI Web](https://mintlify.com/moeru-ai/airi/platforms/web) | 出幀下限沒有降低（G1：23.1%）；pixi-live2d-display 也沒有 WebGPU 版 | 不採用 |
| 換官方 CubismWebFramework | §3.7 原始碼 | buffer 做法相同，不會比較快 | 不採用 |
| 自己改繪製（共用 buffer 改成每個 drawable 各自一組、只上傳有變的資料、快取 GL 狀態） | 官方 Web 注意事項、WebGL 慣例 | 只能省繪製那 2～3 個百分點（§6.3 O1～O3） | 採用 |
| 降低 Core 的計算頻率，再做內插 | 本計畫提案（O5） | 推估省 6～7 個百分點 | 選做 |
| 原生 Cubism SDK（Metal） | [Live2D SDK 說明](https://docs.live2d.com/en/cubism-sdk-manual/performance-unity/) | 出幀下限 8%（N1），最省，但工程量大 | 路線乙 |
| 模型減量（drawable、變形器、參數） | [官方模型效能說明](https://docs.live2d.com/en/cubism-editor-manual/performance/) | 效果大（swing 的計算量只有一半），但違反 A1 | 不採用（A1） |

### 3.7 參考文件

- Live2D 官方：
  - [Performance When Working With Models in the SDK](https://docs.live2d.com/en/cubism-editor-manual/performance/)：drawable、多邊形、變形器數量，每個物件的參數數量，以及繪製順序中貼圖、混合、遮罩切換的成本。
  - [Mask pretreatment methods (Web)](https://docs.live2d.com/en/cubism-sdk-manual/ow-sdk-mask-premake-web)：遮罩在每次繪製模型的開頭全部畫進同一張遮罩緩衝；同樣的遮罩組合會共用。
  - [Notes specific to the Web version](https://docs.live2d.com/en/cubism-sdk-manual/point-to-note)：迴圈內不要呼叫 WebGL getter，改用快取的狀態。
- [CubismWebFramework `cubismrenderer_webgl.ts`](https://raw.githubusercontent.com/Live2D/CubismWebFramework/develop/src/rendering/cubismrenderer_webgl.ts) 與 [`cubismshader_webgl.ts`](https://raw.githubusercontent.com/Live2D/CubismWebFramework/develop/src/rendering/cubismshader_webgl.ts)：共用 buffer、每次繪製都 `bufferData`、遮罩每幀重建。
- [Performance Tuning of Cubism SDK for Unity](https://docs.live2d.com/en/cubism-sdk-manual/performance-unity/)：官方也提到 SDK 為了結構清楚犧牲了部分效能，建議自行剖析。
- Electron 在 macOS 26 的陰影重算問題已在 38.2.0 修正（[MongoDB COMPASS-9907](https://jira.mongodb.org/browse/COMPASS-9907)）。本專案用 45.2.3，不受影響。

## 4. 模型盤點（runtime-fullrig03）

### 4.1 動作（`motion3.json`，8 個，都是 30 fps、淡入淡出 0.35 s）

| 名稱 | 長度 | 循環 | 曲線數 | 相對 swing 新增的曲線 | 目前用途 | 本計畫 |
|---|---|---|---|---|---|---|
| Idle | 8.0 s | 是 | 24 | ArmL/RA、ElbowL/R、HandL/R、ShoulderY、EyeBallX/Y、BrowLX/RX | 預設循環 | 沿用。手臂一直微擺（−3～5），會持續帶動袖子物理 |
| Surprised | 3.0 s | 否 | 22 | 手臂、BaseY（整體往上 3）、ShoulderY 4、HairFluffy 0.3 | 出場打招呼 | 沿用 |
| Curious | 3.6 s | 否 | 17 | ArmL/RA、ElbowR（到 8）、HandR（右手舉起） | 執行中 | 沿用 |
| Nod | 2.8 s | 否 | 17 | ArmL/RA、ElbowL/R | 成功 | 沿用 |
| Shake | 3.2 s | 否 | 17 | ArmL/RA、ElbowL/R | 失敗 | 沿用 |
| Happy | 3.6 s | 否 | 20 | 手臂、手、Cheek | 點身體 | 沿用 |
| Talk | 6.0 s | 是 | 19 | 手臂、手 | **未使用** | **新接**：有文字回覆時說話（§5） |
| Blink | 1.0 s | 否 | 13 | （同 swing） | 未使用 | **不接**：它的頭、身體、呼吸曲線全部是 0，播放時會把 Idle 的姿勢歸零。Idle 本身每 8 秒眨眼 2 次（2.3 s、6.1 s） |

### 4.2 表情（`exp3.json`，10 個）

| 名稱 | 淡入／淡出 | 內容（只列非中性值；M＝Multiply、A＝Add、O＝Overwrite） | 相對 swing | 目前用途 | 本計畫 |
|---|---|---|---|---|---|
| neutral | 0.35／0.35 | 全部中性 | 相同 | 復原 | 沿用 |
| happy | 0.35／0.35 | EyeOpen ×0.9、BrowY +0.22、MouthForm +0.5、**Cheek +0.5** | **改**：多了臉紅 | 成功／點身體／出場 | 沿用 |
| pout | 0.35／0.35 | MouthPout +1 | 相同 | 失敗 | 沿用 |
| sleepy | 0.35／0.35 | EyeOpen ×0.52、BrowY −0.18、MouthForm −0.05、**ShoulderY −2** | **改**：多了垮肩，會牽動披肩、骷髏、荷葉邊物理 | 會話結束 | 沿用 |
| pout_left | 0.35／0.35 | MouthPout +1、MouthAngle +90 | 相同 | 未使用 | **新接** |
| surprised | 0.35／0.35 | BrowY +0.4、MouthForm −0.18、MouthOpenY +0.12 | 相同 | 未使用 | **新接** |
| star_eyes | 0.35／0.35 | MouthForm +0.6、EyeL/RSmile +1、Cheek +0.4 | **新** | — | **新接** |
| arm_l_up | 0.2／0.5 | ArmLChange 1（O） | **新** | — | **新接，手勢層** |
| arm_r_up | 0.2／0.5 | ArmRChange 1（O） | **新** | — | **新接，手勢層** |
| chin_rest | 0.2／0.5 | ArmLB2 1、ArmRB2 1（O） | **新** | — | **新接，手勢層** |

手臂切換的實測行為（Core 直接設定參數）：

- `ArmLChange`／`ArmRChange`：讓 A 手臂的 8 個 mesh 淡出，同時讓 B 手臂（舉手）的 5 個 mesh 淡入。不透明度是線性交叉淡入淡出（0.5 時兩組各 50%）。
- `ArmLB2`／`ArmRB2`：同樣讓 A 手臂淡出，換成托腮的 4 個 mesh。
- **同一側的兩種切換同時為 1 時**（例如 `arm_l_up` 加 `chin_rest`），A 手臂完全消失，兩組 B 手臂會**同時出現**。所以手勢必須互斥，三個手勢要放在同一個佇列。
- 切換期間，動作仍然在驅動已經隱藏的 A 手臂（`ArmLA`、`ElbowL`、`HandL`），袖子物理也照樣跟著動。B 手臂由 `ArmLB`／`ArmRB` 控制，**沒有任何動作驅動它們**，所以舉手時手臂本身是靜止的，只有袖子物理會擺。

### 4.3 參數（83 個；「新」＝swing 沒有；「目前誰驅動」指 fullrig03 的動作、表情、物理和 app.js）

名稱與群組取自 `RushiaHD.cdi3.json`，原檔是簡體，這裡轉成繁體（`tests/unit/test_cloud_client.py` 會檢查文件裡的簡體字）。

| # | Id | 名稱 | 群組 | 範圍（預設） | 新／沿用 | 目前誰驅動 |
|---|---|---|---|---|---|---|
| 1 | `ParamArmLB2` | 左腕 B2 切換 | 手臂切換 | 0～1（0） | **新** | 表情 chin_rest |
| 2 | `ParamArmRB2` | 右腕 B2 切換 | 手臂切換 | 0～1（0） | **新** | 表情 chin_rest |
| 3 | `ParamArmRChange` | 右腕切換 | 手臂切換 | 0～1（0） | **新** | 表情 arm_r_up |
| 4 | `ParamArmLChange` | 左腕切換 | 手臂切換 | 0～1（0） | **新** | 表情 arm_l_up |
| 5 | `ParamArmRB` | 腕 右 B | 手臂 | -30～30（0） | **新** | 物理輸入（2 組） |
| 6 | `ParamArmLB` | 腕 左 B | 手臂 | -30～30（0） | **新** | 物理輸入（2 組） |
| 7 | `ParamElbowL` | 左手肘 | 手臂 | -30～30（0） | **新** | 動作 happy、idle、nod、shake、surprised、talk；物理輸入（3 組） |
| 8 | `ParamElbowR` | 右手肘 | 手臂 | -30～30（0） | **新** | 動作 全部（除 blink）；物理輸入（3 組） |
| 9 | `ParamSleeveCapL` | 左荷葉邊晃動 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveCapL |
| 10 | `ParamSleeveLB2` | 左袂後片尖 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveLB |
| 11 | `ParamSleeveLB1` | 左袂後片根 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveLB |
| 12 | `ParamSleeveL3` | 左袂前片尖 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveL |
| 13 | `ParamSleeveL2` | 左袂前片中 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveL |
| 14 | `ParamSleeveL1` | 左袂前片根 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveL |
| 15 | `ParamSleeveCapR` | 右荷葉邊晃動 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveCapR |
| 16 | `ParamSleeveRB2` | 右袂後片尖 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveRB |
| 17 | `ParamSleeveRB1` | 右袂後片根 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveRB |
| 18 | `ParamSleeveR3` | 右袂前片尖 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveR |
| 19 | `ParamSleeveR2` | 右袂前片中 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveR |
| 20 | `ParamSleeveR1` | 右袂前片根 | 振袖物理 | -1～1（0） | **新** | 物理輸出 SleeveR |
| 21 | `ParamHandR` | 手 右 | 手臂 | -10～10（0） | **新** | 動作 curious、happy、idle、surprised、talk |
| 22 | `ParamHandL` | 手 左 | 手臂 | -10～10（0） | **新** | 動作 happy、idle、surprised、talk |
| 23 | `ParamArmRA` | 腕 右 A | 手臂 | -30～30（0） | **新** | 動作 全部（除 blink）；物理輸入（4 組） |
| 24 | `ParamArmLA` | 腕 左 A | 手臂 | -30～30（0） | **新** | 動作 全部（除 blink）；物理輸入（4 組） |
| 25 | `ParamEyeRSmile` | 右眼 星星眼 | 眼睛 | 0～1（0） | **新** | 表情 star_eyes |
| 26 | `ParamEyeLSmile` | 左眼 星星眼 | 眼睛 | 0～1（0） | **新** | 表情 star_eyes |
| 27 | `ParamHairSide` | 側髮搖擺 | HD 髮絲物理 | -1～1（0） | **新** | 物理輸出 HairSide |
| 28 | `ParamHairFluffy` | 髮量蓬鬆 | 面部 | -1～1（0） | **新** | 動作 surprised |
| 29 | `ParamShoulderY` | 聳肩 | 身體 | -10～10（0） | **新** | 動作 idle、surprised；表情 sleepy；物理輸入（4 組） |
| 30 | `ParamBaseY` | 整體 Y | 身體 | -10～10（0） | **新** | 動作 surprised |
| 31 | `ParamBaseX` | 整體 X | 身體 | -10～10（0） | **新** | —（沒有任何檔案驅動） |
| 32 | `ParamSkirtBowR` | 右裙蝴蝶結擺動 | 物理 | -1～1（0） | **新** | 物理輸出 SkirtBowR |
| 33 | `ParamSkirtBowL` | 左裙蝴蝶結擺動 | 物理 | -1～1（0） | **新** | 物理輸出 SkirtBowL |
| 34 | `ParamCuffR` | 右袖口擺動 | 振袖物理 | -1～1（0） | **新** | 物理輸出 CuffR |
| 35 | `ParamCuffL` | 左袖口擺動 | 振袖物理 | -1～1（0） | **新** | 物理輸出 CuffL |
| 36 | `ParamChestSkull` | 胸前骷骸擺動 | 物理 | -1～1（0） | **新** | 物理輸出 ChestSkull |
| 37 | `ParamCollar` | 披肩擺動 | 物理 | -1～1（0） | **新** | 物理輸出 Collar |
| 38 | `ParamEarringR` | 右耳環擺動 | 物理 | -1～1（0） | **新** | 物理輸出 EarringR |
| 39 | `ParamEarringL` | 左耳環擺動 | 物理 | -1～1（0） | **新** | 物理輸出 EarringL |
| 40 | `ParamRibbonLTip` | 左緞帶尾尖延遲 | 物理 | -1～1（0） | **新** | 物理輸出 RibbonL |
| 41 | `ParamRibbonL` | 左緞帶擺動 | 物理 | -1～1（0） | **新** | 物理輸出 RibbonL |
| 42 | `ParamHairBunR` | 右髮包擺動 | 物理 | -1～1（0） | **新** | 物理輸出 HairBunR |
| 43 | `ParamHairBunL` | 左髮包擺動 | 物理 | -1～1（0） | **新** | 物理輸出 HairBunL |
| 44 | `ParamMouthAngle` | 嘟嘴旋轉 | 嘴巴 | 0～90（0） | 沿用 | 表情 happy、neutral、pout、pout_left、sleepy、star_eyes、surprised |
| 45 | `ParamMouthPout` | 嘟嘴 | 嘴巴 | 0～1（0） | 沿用 | 表情 happy、neutral、pout、pout_left、sleepy、star_eyes、surprised |
| 46 | `ParamCheek` | 臉色（蒼白／紅潤） | 面部 | -1～1（0） | 沿用 | 動作 happy；表情 happy、star_eyes |
| 47 | `ParamBrowRX` | 右眉水平 | 眉毛 | -1～1（0） | 沿用 | 動作 idle |
| 48 | `ParamBrowLX` | 左眉水平 | 眉毛 | -1～1（0） | 沿用 | 動作 idle |
| 49 | `ParamBrowRForm` | 右眉彎曲 | 眉毛 | -1～1（0） | 沿用 | —（沒有任何檔案驅動） |
| 50 | `ParamBrowLForm` | 左眉彎曲 | 眉毛 | -1～1（0） | 沿用 | —（沒有任何檔案驅動） |
| 51 | `ParamBrowRAngle` | 右眉角度 | 眉毛 | -1～1（0） | 沿用 | —（沒有任何檔案驅動） |
| 52 | `ParamBrowLAngle` | 左眉角度 | 眉毛 | -1～1（0） | 沿用 | —（沒有任何檔案驅動） |
| 53 | `ParamRibbonRTip` | 右緞帶尾尖延遲 | 物理 | -1～1（0） | 沿用 | 物理輸出 RibbonR |
| 54 | `ParamRibbonR` | 右緞帶擺動 | 物理 | -1～1（0） | 沿用 | 物理輸出 RibbonR |
| 55 | `ParamHairRoll` | 髮絲歪頭慣性 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairRoll |
| 56 | `ParamHairPitch` | 髮絲俯仰慣性 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairPitch |
| 57 | `ParamSeamBake` | 袖口接縫補償 |  | 0～1（1） | 沿用 | —（沒有任何檔案驅動） |
| 58 | `ParamAngleX` | 角度 X | 面部 | -45～45（0） | 沿用 | app.js 視線；動作 全部；物理輸入（17 組） |
| 59 | `ParamAngleY` | 角度 Y | 面部 | -30～30（0） | 沿用 | app.js 視線；動作 全部；物理輸入（1 組） |
| 60 | `ParamAngleZ` | 角度 Z | 面部 | -30～30（0） | 沿用 | app.js 潛伏歪頭／進退場傾斜；動作 全部；物理輸入（14 組） |
| 61 | `ParamEyeLOpen` | 左眼開合 | 眼睛 | 0～1（1） | 沿用 | 動作 全部；表情 happy、neutral、pout、pout_left、sleepy、star_eyes、surprised |
| 62 | `ParamEyeROpen` | 右眼開合 | 眼睛 | 0～1（1） | 沿用 | 動作 全部；表情 happy、neutral、pout、pout_left、sleepy、star_eyes、surprised |
| 63 | `ParamEyeBallX` | 眼球 X | 眼睛 | -1～1（0） | 沿用 | app.js 視線；動作 idle；物理輸入（1 組） |
| 64 | `ParamEyeBallY` | 眼球 Y | 眼睛 | -1～1（0） | 沿用 | app.js 視線；動作 idle；物理輸入（1 組） |
| 65 | `ParamEyeBallForm` | 果凍眼 | 眼睛 | -1～1（0） | 沿用 | 物理輸出 EyeJelly |
| 66 | `ParamBrowLY` | 左眉 Y | 眉毛 | -1～1（0） | 沿用 | 動作 全部；表情 happy、neutral、pout、pout_left、sleepy、star_eyes、surprised |
| 67 | `ParamBrowRY` | 右眉 Y | 眉毛 | -1～1（0） | 沿用 | 動作 全部；表情 happy、neutral、pout、pout_left、sleepy、star_eyes、surprised |
| 68 | `ParamMouthForm` | 嘴型 | 嘴巴 | -1～1（0） | 沿用 | 動作 全部；表情 happy、neutral、pout、pout_left、sleepy、star_eyes、surprised |
| 69 | `ParamMouthOpenY` | 嘴巴開合 | 嘴巴 | 0～1（0） | 沿用 | 動作 全部；表情 happy、neutral、pout、pout_left、sleepy、star_eyes、surprised |
| 70 | `ParamBodyAngleX` | 身體角度 X | 身體 | -10～10（0） | 沿用 | 動作 全部；物理輸入（25 組） |
| 71 | `ParamBodyAngleY` | 身體角度 Y | 身體 | -10～10（0） | 沿用 | 動作 全部；物理輸入（1 組） |
| 72 | `ParamBodyAngleZ` | 身體角度 Z | 身體 | -10～10（0） | 沿用 | app.js 潛伏／進退場傾斜；動作 全部；物理輸入（25 組） |
| 73 | `ParamBreath` | 呼吸 | 身體 | 0～1（0） | 沿用 | 動作 全部；物理輸入（18 組） |
| 74 | `ParamHairBack` | 後髮擺動 | 物理 | -1～1（0） | 沿用 | 物理輸出 HairBack |
| 75 | `ParamHairFront` | 前髮擺動 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairBangs |
| 76 | `ParamHairLongR` | 右長髮擺動 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairLongR |
| 77 | `ParamHairLongL` | 左長髮擺動 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairLongL |
| 78 | `ParamHairSideR` | 右側髮擺動 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairSideR |
| 79 | `ParamHairSideL` | 左側髮擺動 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairSideL |
| 80 | `ParamHairLongRTip` | 右長髮尾 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairLongR |
| 81 | `ParamHairLongLTip` | 左長髮尾 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairLongL |
| 82 | `ParamHairSideRTip` | 右側髮尾 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairSideR |
| 83 | `ParamHairSideLTip` | 左側髮尾 | HD 髮絲物理 | -1～1（0） | 沿用 | 物理輸出 HairSideL |

沒有任何檔案驅動的參數：

- `ParamBaseX`：整體平移，88 個 drawable 都會動。
- `ParamBrowL/RForm`、`ParamBrowL/RAngle`。
- `ParamArmLB`／`ParamArmRB`：只當物理輸入。
- `ParamSeamBake`：預設值是 1，是袖口接縫補償，**不能**被覆寫成 0。現在每幀都會先 `values.set(defaults)`，所以會維持 1。

本計畫**不**新驅動上面這些參數。

### 4.4 物理（28 組、68 個頂點、40 個輸出、142 個輸入；swing 是 9 組、23 個頂點、14 個輸出）

輸入欄的 ° 表示 Angle 型輸入，其餘是 X 型。

| # | 群組 | 說明 | 輸入（權重） | 輸出 | 頂點 | 與 swing 比較 |
|---|---|---|---|---|---|---|
| 1 | `HDHairRoll` | 髮絲歪頭慣性 | AngleZ° 100 | HairRoll | 2 | 相同 |
| 2 | `HDHairLongR` | 右長髮慣性鏈（主段＋髮尾） | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、Breath 60 | HairLongR、HairLongRTip | 3 | 調整：頂點參數微調 |
| 3 | `HDHairLongL` | 左長髮慣性鏈（主段＋髮尾） | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、Breath 60 | HairLongL、HairLongLTip | 3 | 調整：頂點參數微調 |
| 4 | `HDHairSideR` | 右側髮慣性鏈（主段＋髮尾） | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、Breath 60 | HairSideR、HairSideRTip | 3 | 調整：Scale 3→2.13；頂點參數微調 |
| 5 | `HDHairSideL` | 左側髮慣性鏈（主段＋髮尾） | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、Breath 60 | HairSideL、HairSideLTip | 3 | 調整：Scale 3→2.18；頂點參數微調 |
| 6 | `HDHairSide` | 側髮共同擺動（左右整體） | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、Breath 60 | HairSide | 2 | **新增** |
| 7 | `HDHairBangs` | 瀏海慣性 | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30 | HairFront | 2 | 相同 |
| 8 | `HDRibbonR` | 右緞帶慣性鏈（主段＋尾尖） | AngleX 40、AngleZ 30、BodyAngleX 60、BodyAngleZ 40、Breath 60 | RibbonR、RibbonRTip | 3 | 相同 |
| 9 | `HDHairBack` | 後髮下段慣性 | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、Breath 90 | HairBack | 2 | 相同 |
| 10 | `HDHairPitch` | 髮絲俯仰慣性（頭＋身體） | AngleY° 100、BodyAngleY° 33 | HairPitch | 2 | 調整：輸入變更 |
| 11 | `HDHairBunL` | 左髮包慣性 | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、AngleZ° 20 | HairBunL | 2 | **新增** |
| 12 | `HDHairBunR` | 右髮包慣性 | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、AngleZ° 20 | HairBunR | 2 | **新增** |
| 13 | `HDRibbonL` | 左緞帶慣性鏈（主段＋尾尖） | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、AngleZ° 50、Breath 60 | RibbonL、RibbonLTip | 3 | **新增** |
| 14 | `HDEarringL` | 左耳環垂墜 | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、AngleZ° 100 | EarringL | 2 | **新增** |
| 15 | `HDEarringR` | 右耳環垂墜 | AngleX 60、AngleZ 40、BodyAngleX 40、BodyAngleZ 30、AngleZ° 100 | EarringR | 2 | **新增** |
| 16 | `HDCollar` | 披肩慣性 | BodyAngleX 60、BodyAngleZ 60、ShoulderY 30、Breath 30 | Collar | 2 | **新增** |
| 17 | `HDChestSkull` | 胸前骷髏慣性 | BodyAngleX 60、BodyAngleZ 60、ShoulderY 30、Breath 30 | ChestSkull | 2 | **新增** |
| 18 | `HDCuffL` | 左袖口慣性 | BodyAngleX 60、BodyAngleZ 60、ArmLA 40、ElbowL 40、Breath 30 | CuffL | 2 | **新增** |
| 19 | `HDCuffR` | 右袖口慣性 | BodyAngleX 60、BodyAngleZ 60、ArmRA 40、ElbowR 40、Breath 30 | CuffR | 2 | **新增** |
| 20 | `HDSleeveR` | 右袂前片慣性鏈（根＋中＋尾） | ArmRA 45、ArmRA° 60、ArmRB 45、ArmRB° 75、ElbowR 30、ElbowR° 50、BodyAngleX 13、BodyAngleZ 13、AngleX 5、Breath 13 | SleeveR1、SleeveR2、SleeveR3 | 4 | **新增** |
| 21 | `HDSleeveL` | 左袂前片慣性鏈（根＋中＋尾） | ArmLA 45、ArmLA° 60、ArmLB 45、ArmLB° 75、ElbowL 30、ElbowL° 50、BodyAngleX 13、BodyAngleZ 13、AngleX 5、Breath 13 | SleeveL1、SleeveL2、SleeveL3 | 4 | **新增** |
| 22 | `HDSleeveRB` | 右袂後片慣性鏈（根＋尾） | ArmRA 45、ArmRA° 60、ArmRB 45、ArmRB° 60、ElbowR 30、ElbowR° 50、BodyAngleX 13、BodyAngleZ 13、AngleX 5、Breath 13 | SleeveRB1、SleeveRB2 | 3 | **新增** |
| 23 | `HDSleeveLB` | 左袂後片慣性鏈（根＋尾） | ArmLA 45、ArmLA° 60、ArmLB 45、ArmLB° 60、ElbowL 30、ElbowL° 50、BodyAngleX 13、BodyAngleZ 13、AngleX 5、Breath 13 | SleeveLB1、SleeveLB2 | 3 | **新增** |
| 24 | `HDSleeveCapR` | 右荷葉邊慣性 | ArmRA 40、ShoulderY 30、BodyAngleX 30、BodyAngleZ 30 | SleeveCapR | 2 | **新增** |
| 25 | `HDSleeveCapL` | 左荷葉邊慣性 | ArmLA 40、ShoulderY 30、BodyAngleX 30、BodyAngleZ 30 | SleeveCapL | 2 | **新增** |
| 26 | `HDSkirtBowL` | 左裙蝴蝶結慣性 | BodyAngleX 60、BodyAngleZ 60、Breath 30 | SkirtBowL | 2 | **新增** |
| 27 | `HDSkirtBowR` | 右裙蝴蝶結慣性 | BodyAngleX 60、BodyAngleZ 60、Breath 30 | SkirtBowR | 2 | **新增** |
| 28 | `HDEyeJelly` | 眼球果凍 | EyeBallX 20、EyeBallY 13 | EyeBallForm | 2 | **新增** |

本計畫不修改 `physics3.json`（決策 A1）。

### 4.5 其他

- Parts 7 個：頭部、後髮、面部、前髮、頭部附件、附加物、身體。參數群組 10 個，新增的是「手臂」「手臂切換」「振袖物理」。
- 模型資料夾裡的 `RushiaHD.psd2live.json` 是舊版的，裡面寫 77 個參數、4 張貼圖、14 組物理，和實際檔案不符。這個版本也沒有 `export-report.json`，無法用 sha256 驗證完整性。依 A1 接受現狀，執行期也不讀這兩個檔案。

## 5. 狀態機對應（提案，待 D2 確認）

### 5.1 三層演出

| 層 | 佇列 | 內容 | 互斥方式 |
|---|---|---|---|
| 動作 | `motionQueue`（現有） | Idle、Surprised、Curious、Nod、Shake、Happy、**Talk** | 新的取代舊的 |
| 臉部表情 | `expressionQueue`（現有） | neutral、happy、pout、sleepy、**pout_left、surprised、star_eyes** | 新的取代舊的 |
| **手勢（新）** | `gestureQueue`（新的 `CubismMotionQueueManager`） | **arm_l_up、arm_r_up、chin_rest**，以及程式內建的 `none`（四個切換參數都 Overwrite 成 0，用來淡出） | 同一個佇列，所以不會同時出現兩種手臂 |

每幀的套用順序是：預設值 → 動作 → 臉部表情 → 手勢 → 視線 → 物理 → `core.update()`。

### 5.2 事件對應

| 觸發 | 現在 | 提案：動作 | 提案：臉部（維持時間） | 提案：手勢（維持時間） |
|---|---|---|---|---|
| 語音喚醒／會話開始出場（`appear` greet） | Surprised＋happy | Surprised | **surprised**（2 s） | — |
| 點頭部 | Surprised＋happy | Surprised | **star_eyes**（2 s） | **arm_r_up**（1.5 s，揮手） |
| 執行中 `executing` | Curious | Curious | — | — |
| 執行超過 `THINK_MS`（2.5 s）還沒有結果 | — | — | — | **chin_rest**（直到 result） |
| 成功、沒有文字回覆 | Nod＋happy | Nod | happy（2 s） | 清除 |
| 成功、有文字回覆（`replies` 非空） | Nod＋happy | **Talk**（依泡泡字數，1.5～4 s 後回到 Idle） | happy（2 s） | 清除 |
| 失敗 | Shake＋pout | Shake | pout（2 s） | 清除 |
| 同一個會話中連續第 2 次以上失敗 | Shake＋pout | Shake | **pout_left**（2 s） | 清除 |
| 點身體 | Happy＋happy | Happy | happy（2 s） | **arm_l_up**（1.5 s） |
| 會話結束 | sleepy 1.5 s 後退場 | — | sleepy（1.5 s） | **chin_rest**（1.5 s） |
| 退場 | Idle | Idle | — | 清除 |

- 「有文字回覆」的 Talk 只是演出，沒有聲音，也不對嘴（不做 TTS）。
- 「連續失敗」的計數在成功、會話結束或退場時歸零。

### 5.3 介面契約變更（只在 desktop 內部，後端協定不變）

- effects 新增 `{type:'gesture', name}`（name 是 `arm_l_up`／`arm_r_up`／`chin_rest`／`none`）。臉部表情和動作沿用現有的 effect。
- `snapshot()` 新增 `gesture` 欄位。`window.__ui` 新增 `activeGesture()`。
- [main.js](../desktop/main.js) 的 `PAGE_STATE` 一併帶出，給 `ui_demo --capture` 和自動驗收讀取。
- 後端的 `ui_bus` 事件**不改**。

## 6. 渲染路線、幀率與 CPU（第二版）

### 6.1 三條路線（D1）

依 §3.3 的實測，「穩定 60 fps」和「峰值 5%」只能二選一。三條路線：

| 路線 | 做法 | 出場時峰值（60 fps） | 潛伏時 | 工程量 | 依據 |
|---|---|---|---|---|---|
| **甲（推薦）Electron＋優化＋潛伏停格** | 保留現有架構；做 §6.3 的 O1～O4；出場時全程 60 fps；退場後物理收尾完就停格（0 fps） | 推估 **33～36%**；加 O5 約 **27～30%**（現在 37.5%。下限 E4 20.9%，加上計算〔L1 扣掉 rAF 約 14.6%〕已經約 35%，所以 O1～O3 只能省繪製那 2～3 點，真正大的是計算） | 推估 **0.3～1%**（E0） | 中（只改 desktop 的渲染層） | E4、L0、L1、E0 |
| 乙 原生角色視窗 | 角色視窗改用 Swift＋Metal＋Cubism SDK for Native；聊天室與匯流排仍用 Electron | 推估 **14～18%**（下限 8%，加上原生 Core 計算推估 6～9%〔原生 C 通常比 WASM 快 1.5～2 倍〕，**未實測**） | 推估約 0～1% | 大：重寫渲染、動作、物理、狀態機橋接、點擊穿透、拖曳；需要另外下載 Cubism Native SDK（授權條款要確認） | N1、N2 |
| 丙 守住 5% | 乙，再把幀率降到約 20 fps；或者 Electron 約 2 fps | ≤ 5%（推估） | ≈ 0% | 大，或犧牲流暢度 | N3、E9 |

- 甲與 A3（60 fps）相符，但**峰值不會低於 21%**，所以 A4 的 5% 達不到。
- 乙的數字是推估，選乙前要先做一個原生載入模型的原型實測。
- 丙違反 A3。

### 6.2 幀率規格（甲）

| 狀態 | 現在 | 甲 |
|---|---|---|
| 出場（閒置、動作、進退場、收尾） | 30／12 | **一律 60**，固定節奏不分級 |
| 潛伏，物理收尾中 | 5 | 60，直到收尾結束（見下） |
| 潛伏，已經收尾 | 5 | **0（停格，畫面維持最後一幀）** |
| 潛伏時收到任何 effect 或事件 | 立刻畫一幀 | 回到 60 |

- 收尾判斷：
  - [physics-clock.js](../desktop/renderer/physics-clock.js) 每個物理步長回傳「40 個輸出的最大變化量」。只多回傳一個數字，不改物理解算。
  - 連續 `SETTLE_HOLD_MS` 低於 `SETTLE_EPS` 就算收尾完成；或者退場後超過 `SETTLE_MAX_MS` 就強制停格。
- 停格期間 Idle 動作暫停（不推進時間），醒來時從暫停的位置繼續，避免跳格。
- 游標不會喚醒潛伏中的角色（現狀就是潛伏時不追游標）。
- 60 fps 用 rAF 每個 vsync 畫一次，不再用 `period - 2` 的輪詢判斷。驗收看幀間隔（§9.2 R4）。
- `SETTLE_EPS`、`SETTLE_HOLD_MS`、`SETTLE_MAX_MS` 在 P0 實測後決定：
  - 純 Idle 60 秒，以及每個動作、表情、手勢之後各 5 秒，記錄最大變化量的時間序列。
  - `SETTLE_EPS` 要高於純 Idle 的 p95，`SETTLE_MAX_MS` 要涵蓋退場後最長的收尾時間。

### 6.3 渲染優化項目（甲，逐項 A/B 實測，有效才保留）

| # | 項目 | 做法 | 預期效果（未實測） | 依據 |
|---|---|---|---|---|
| O1 | 每個 drawable 用自己的 buffer | 改寫 pixi-live2d-display 的 `CubismShader_WebGL.setupShaderProgram`（和 [drawable-colors.js](../desktop/renderer/drawable-colors.js) 一樣用 prototype 修補，不改 node_modules）：UV 與索引在初始化時上傳一次；頂點只在 `VertexPositionsDidChange` 時用 `bufferSubData` 更新 | GPU 程序往 E4 的 13.5% 靠近（L2 是 21.8%） | §3.3 解讀 4 |
| O2 | GL 狀態快取 | 省掉每個 mesh 的 `useProgram(null)`、重複的 CULL_FACE／frontFace、相同的 uniform 與貼圖綁定 | renderer 和 GPU 程序各降一些 | 官方 Web 注意事項 |
| O3 | 遮罩只在來源動了才重畫 | 6 個被遮的 drawable、2 組遮罩；來源沒變就沿用上一幀的遮罩緩衝 | 小 | 官方遮罩前處理說明 |
| O5（選做） | Core 以 30 Hz 計算、頂點內插到 60 fps | `core.update()`、動作、物理每 2 幀做一次；中間那一幀用前後兩次的頂點位置與不透明度做線性內插（10,896 個頂點，推估 < 0.1 ms）。物理維持固定 60 Hz 步長 | 計算減半，renderer 推估降 6～7 個百分點 | L1；§3.3 解讀 3 |
| O4 | 不畫看不到的東西 | 潛伏時畫面外的 drawable（視窗下緣以下）不送繪製；停格時完全不跑 Core 和物理 | 潛伏收尾期間降低 | E0 |

- 每一項都用 `scripts/ui_perf.py` 的 60 fps 階段做 A/B，數字寫回本檔。
- 沒有效果（< 1 個百分點）就撤回，不保留複雜度。
- O1／O2 改的是渲染結果的來源：U6 要和改動前逐像素比對。
- O5 會改變畫面：中間幀是內插出來的，快速動作（Shake、進退場）可能變軟，手勢切換時的不透明度內插也和原本不同。所以 O5 要先過 U2、U6，再由使用者決定是否保留。
- **不做**的調整：關抗鋸齒、關 `preserveDrawingBuffer`、降解析度。L4、L5 已經實測沒有效果，點擊判斷也還需要 `preserveDrawingBuffer`。

## 7. 實作階段

| 階段 | 內容 | 檔案 |
|---|---|---|
| P0 量測 | 切換模型路徑，量物理變化量的時間序列，決定 `SETTLE_*`；用正式程式實測 60 fps 的基線（預期接近 L0 的 37.5%）。數字寫回 §6.2 與實作後新增的「§13 實作結果」 | [config.py](../mac_ai/config.py)、[README.md](../README.md)、暫存量測腳本（不入版控） |
| P1 狀態機 | §5 的對應、手勢層、Talk 的停止時機、連續失敗計數；`node --test` 案例 | [state.js](../desktop/state.js)、`desktop/test/state.test.js` |
| P2 渲染 | `NEEDED_MOTIONS` 加入 Talk；`NEEDED_EXPRESSIONS` 加入 6 個新表情；`gestureQueue`；`gesture` effect；`__ui.activeGesture`、`PAGE_STATE` | [app.js](../desktop/renderer/app.js)、[main.js](../desktop/main.js) |
| P3 幀率 | 物理變化量、收尾判斷、60 fps／停格兩種狀態；把「要不要畫下一幀」的判斷抽成純函式並寫測試 | [physics-clock.js](../desktop/renderer/physics-clock.js)、`desktop/renderer/frame-rate.js`（新增，純函式）、`desktop/test/frame-rate.test.js`、`desktop/test/physics-clock.test.js` |
| P3.5 渲染優化 | §6.3 的 O1～O5，逐項 A/B，無效就撤回；O5 另外要使用者看過才保留 | `desktop/renderer/render-buffers.js`（新增，prototype 修補）、[app.js](../desktop/renderer/app.js) |
| P4 驗收工具 | `ui_perf.py` 依 D1 更新上限，階段改成「潛伏停格」「出場 60 fps」「進退場」「退場後收尾到停格」，分別量 CPU 峰值（每秒取樣取最大值）與幀間隔；`ui_demo.py` 播放序列加入「點頭部」「長執行」「有回覆的成功」「連續失敗」，`--capture` 會帶出 gesture | [scripts/ui_perf.py](../scripts/ui_perf.py)、[scripts/ui_demo.py](../scripts/ui_demo.py) |

## 8. 新增常數

| 位置 | 常數 | 值 |
|---|---|---|
| `state.js` | `GESTURE_MS` | 1500 |
| `state.js` | `THINK_MS` | 2500 |
| `state.js` | `TALK_MIN_MS`／`TALK_MAX_MS`／`TALK_PER_CHAR_MS` | 1500／4000／120 |
| `app.js` | `MIN_FRAME_MS`（取代 busy／idleOut／peek 三級；rAF 逐 vsync，120 Hz 螢幕跳過間隔 < 12 ms 的那次） | 12 |
| `app.js` | `SETTLE`（eps／holdMs／maxMs，P0 實測，§13.1） | 0.05／500／3000 |
| `app.js` | `FROZEN_TICK_MS`（停格時狀態機與泡泡的更新間隔） | 250 |
| `app.js` | `RENDER_OPT`（buffers＝O1、state＝O2、halfRate＝O5） | 全部 true |
| `frame-rate.js` | `TAIL_MS`（事件後至少再畫多久，涵蓋淡入淡出） | 700 |

## 9. 驗收計畫

### 9.1 自動化測試（離線，實作者執行並貼出輸出）

| # | 項目 | 指令 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| T1 | 狀態機對應 | `cd desktop && node --test` | 0 fail；§5.2 每一列至少一個案例，驗證 effects 的動作、表情、手勢與維持時間；另有手勢互斥、Talk 依字數停止、連續失敗計數歸零、退場時清除手勢；既有 67 項不改預期也全過 | 修程式；既有案例的預期要改，必須先說明理由並請使用者同意 |
| T2 | 幀率判斷純函式 | 同上（`frame-rate.test.js`） | 每個狀態組合的結果符合 §6.2（出場一律畫；潛伏收尾中畫；收尾完成或超過 `SETTLE_MAX_MS` 就停；任何事件會喚醒） | 修程式 |
| T3 | 物理變化量 | 同上（`physics-clock.test.js`，假 solver） | 輸出固定時變化量是 0；輸出跳動時回傳的最大差值正確；`evaluate` 的輸出和改動前逐位元相同（用同一組假輸入比對） | 修程式 |
| T4 | 後端不受影響 | `source .venv/bin/activate && pytest -m unit && pytest tests/runtime/test_services.py` | 0 failed | 修程式 |
| T5 | 全部離線測試 | `pytest` | 0 failed | 修程式 |

### 9.2 真實環境（本機 Electron，實作者執行並貼出輸出）

| # | 項目 | 指令／步驟 | 通過標準 | 失敗時 |
|---|---|---|---|---|
| R1 | 載入 | `python scripts/ui_demo.py --capture <暫存資料夾>` | 沒有「啟動失敗」；console 有 `[ui] ready`；每個狀態都有 png 和 state | 看 console 錯誤；載入問題回頭查 §3.1 |
| R2 | 演出對照 | 同上，讀每個 capture 的 `motion`、`snap.expression`、`gesture` | 和 §5.2 的對應表逐列相同（自動比對，由 `ui_demo` 印出 PASS／FAIL） | 修狀態機或渲染 |
| R3 | 物理數值健康 | P0 的量測腳本：純 Idle 60 s，加上每個動作、表情、手勢 | 40 個輸出全部是有限值；沒有任何輸出貼在 ±1 超過 0.5 s；每個事件之後在 `SETTLE_MAX_MS` 內回到 `SETTLE_EPS` 以下 | 記錄是哪一組，交給使用者判斷（模型檔不改，A1） |
| R4 | 穩定 60 fps | `python scripts/ui_perf.py`（P4 版） | 出場 60 秒，其中至少包含一次每種動作：幀間隔中位數 16.7±0.5 ms、p95 ≤ 18 ms、掉格（間隔 > 25 ms）≤ 0.5%；每幀 JS 耗時 p95 ≤ 8 ms | 看是哪一段超時（`__ui.perf().parts`）；不能用降幀解決 |
| R5 | CPU | 同上 | 出場 60 fps 時每秒取樣的**峰值**以單核計 ≤ 40%（整台電腦 5%，D1）；潛伏停格的平均 ≤ 1%；O1～O5 各自的 A/B 數字都附上 | 依 D1 處理，不自行放寬 |
| R5b | 停格與喚醒 | 同上 | 退場後 ≤ `SETTLE_MAX_MS` 內停格（renderer 的幀數不再增加）；送一個事件後 ≤ 1 幀內恢復 60 fps | 修程式 |
| R6 | 記憶體 | 同上 | RSS ≤ 700 MB（實測基線 481 MB） | 查貼圖或 buffer 洩漏 |
| R7 | 實際語音流程 | `python main.py --ui`，說喚醒詞加 3 句（成功、失敗、聊天各一） | 演出符合 §5.2；`logs/runtime/ui.jsonl` 沒有錯誤 | 修程式 |

### 9.3 人工驗收（由使用者判斷）

| # | 看什麼 | 怎麼看 | 由使用者決定是否通過 |
|---|---|---|---|
| U1 | 手勢切換時的殘影 | `ui_demo --hold` 點頭部、點身體；讓會話結束 | 0.2～0.5 s 的手臂交叉淡入淡出能不能接受 |
| U2 | 收尾的流暢度 | 每個動作結束後看袖子、披肩、耳環 | 不會一格一格跳；停下來的過程自然 |
| U3 | 物理幅度 | 進退場、Surprised（整體往上跳）、sleepy（垮肩） | 沒有穿模、沒有甩過頭 |
| U4 | Talk 的觀感 | 問一句聊天 | 說話動作的長度、時機是否自然（已確認沒聲音可以，A5） |
| U5 | 潛伏停格（只限甲案） | 潛伏 1 分鐘 | 停格看起來能不能接受 |
| U6 | 優化前後的畫面一致 | `ui_demo --capture` 優化前後各存一份，逐張比對（自動算像素差，差異大的交給使用者看） | 看不出差別 |

### 9.4 不在本次範圍

- 修改模型檔（moc3、physics3、motion、exp3、貼圖），以及縮小貼圖。
- 對嘴、TTS、聲音。
- 游標帶動的 60 fps；手臂跟著游標動；讓動作去驅動 `ArmLB`／`ArmRB`／`BaseX`。
- 升級 pixi-live2d-display 或 Cubism Core；換成官方 CubismWebFramework（§3.7：它的 buffer 做法相同，換了也不會更快）。
- 路線乙、丙（原生角色視窗），除非 D1 選了它們。
- 後端 `ui_bus` 協定。

## 10. 待確認（2026-11-20 已全部確認）

| # | 決定 |
|---|---|
| D1 | 5% 指整台電腦；走路線甲。驗收用的上限：出場 60 fps 時，每秒取樣的峰值以單核計 ≤ 40%（等於整台電腦 5%），目標 ≤ 36%；潛伏停格 ≤ 1%。O5 只在 O1～O4 之後仍超過 40% 才做 |
| D2 | 照 §5.2 提案 |
| D3 | 出場一律 60 fps（A3） |
| D4 | Talk 沒聲音可以用（A5） |
| D5 | 潛伏停格，隨路線甲採用 |

## 11. 風險與已知限制

- **CPU**：甲在出場期間全程 60 fps，持續約 33～36%（推估；採 O5 約 27～30%）。出場時間越長，平均越高（會話 60 秒、閒置 15 秒後退場）。
- O1／O2 是修補第三方函式庫的內部方法，綁定 pixi-live2d-display 0.4.0（已鎖版本）。升級時要重新驗證。
- 手勢切換是不透明度的交叉淡入淡出，淡入淡出期間兩組手臂各有半透明（U1）。
- 舉手和托腮時 B 手臂是靜止的（沒有動作驅動 `ArmLB`／`ArmRB`），只有袖子會擺。
- Idle 的手臂一直在動，物理永遠不會完全靜止。潛伏停格要靠 `SETTLE_EPS` 和 `SETTLE_MAX_MS` 決定，門檻設太低會一直不停格。實測（§13.1）事件造成的擺動不比 Idle 本身大，所以停格的時機實際上是「Idle 週期中平緩的一段」，停住的姿勢不一定是靜止姿勢（U5）。
- 出場時的每秒峰值在 40% 上下（§13.2），量 60 秒時有一次超過，還不能說穩定達標。
- 模型沒有 export report，完整性無法驗證（A1 接受）。
- STT 和實際語音沒有自動測試，R7 要人工執行。

## 12. 回退方式

- 模型：`UI_MODEL_PATH` 改回 `runtime-swing` 即可。P2 讓 swing 沒有的表情、手勢在缺檔時略過（只記一次 warning），既有 6 個動作與 4 個表情缺檔時照舊拋錯。
- 程式：P1～P4（含 P3.5）各自獨立 commit，可以逐一 revert；O1、O2、O5 各自一個開關（`app.js` 的 `RENDER_OPT`），改成 false 就回到原本的做法。O3、O4 沒有實作（§13.3）。

## 13. 實作結果（2026-11-20，本機 M1、8 核）

### 13.1 P0：收尾門檻

暫時把停格關掉，用正式程式記錄每幀物理輸出的最大變化量（`__ui.perf().deltaTrace`），量測腳本不入版控。

| 階段 | 變化量 p50 | p95 | 最大 |
|---|---|---|---|
| 潛伏、純 Idle 20 秒 | 0.0031 | 0.048 | 0.089 |
| 出場、純 Idle 30 秒 | 0.0027 | 0.048 | 0.087 |
| 點身體／點頭部／托腮／成功／說話／失敗兩次（各 5～6 秒） | 0.003～0.013 | 0.021～0.055 | 0.043～0.086 |
| 會話結束→退場（14 秒） | 0.0037 | 0.062 | 0.098 |

- 40 個輸出全部是有限值（`physics-clock` 遇到非有限值會拋錯，量測期間沒有發生）。「沒有輸出貼在 ±1 超過 0.5 秒」這項沒有另外量（R3 部分未驗證）。
- 用實測軌跡模擬幾組參數後，採用 `eps 0.05／hold 500 ms／max 3000 ms`：
  - 退場動畫結束後約 0.73 秒停格。
  - 從 Idle 任意時點起算，最壞 0.8 秒停格。
  - `maxMs` 留約 4 倍餘裕，當保險用。

### 13.2 CPU 與幀率（單核 %；整台電腦＝÷8）

**優化前的 60 fps 基線**（`ui_perf.py`，60 秒、14 段演出）：
- 出場：平均 33.0%，每秒峰值 41.9%。
- 幀間隔：中位 16.70 ms，p95 18.6 ms，0 掉格。
- 潛伏停格：0.8%。

**A/B 方法**：開關交替、各跑 3 次。每次重新啟動 Electron，出場後跑 30 秒相同的演出腳本，每秒取樣。兩次量測之間的雜訊約 2～3 個百分點，所以不採用單次比較。

| 項目 | 平均（關→開） | 每秒峰值（關→開） | 分程序 | 結論 |
|---|---|---|---|---|
| O1 第一版（頂點內容比對） | 36.4 → 35.9 | 42.9 → 41.5 | GPU −1.3、renderer +0.8 | 頂點每幀約 61 次真的有變、只有 2.4 次相同，比對沒有省到東西。改成第二版 |
| O1 第二版（UV／索引只傳一次、頂點用 `bufferSubData`）＋O2 | 37.5 → 36.9 | 43.6 → 41.4（3 組都低 1.9～2.9） | GPU −1.4、renderer +0.7 | 平均只省 0.6（< 1 點），但峰值穩定省約 2.2；D1 看的是峰值，所以**保留** |
| O5（Core 30 Hz＋內插） | 36.2 → 33.6 | 42.9 → 37.8（3 組都 ≤ 39.0） | renderer −3.3 | **保留，待使用者看過 U2／U6** |

**最終**（O1＋O2＋O5，`ui_perf.py`，60 秒）：

| 項目 | 結果 | 標準 | |
|---|---|---|---|
| 潛伏停格 CPU 平均 | 0.82%（期間 0 幀） | ≤ 1 | PASS |
| 出場 CPU 平均 | 34.2%（整台 4.28%） | — | |
| 出場 CPU 每秒峰值 | **42.0%（整台 5.25%）** | ≤ 40 | **FAIL** |
| 進退場 CPU 每秒峰值 | 38.8% | ≤ 40 | PASS |
| 幀間隔 | 中位 16.70 ms、p95 17.80 ms、0 掉格 | 16.2～17.2／≤ 18／≤ 0.5% | PASS |
| 進退場幀間隔 p95 | 17.80 ms | ≤ 18 | PASS |
| 每幀 JS p95 | 3.9 ms | ≤ 8 | PASS |
| 退場後停格 | 會話結束後 3.22 秒（含 sleepy 1.5 秒、退場 0.8 秒），之後 1 秒內幀數不變 | ≤ 5.8 秒 | PASS |
| 事件喚醒 | 0.3 秒內畫了 19 幀 | ≥ 12 幀 | PASS |
| RSS | 693 MB（三次量測分別是 673、541、693） | ≤ 700 | PASS（接近上限） |

- 出場峰值：A/B 的 30 秒量測，O5 開著的 3 次都 ≤ 39.0；但 60 秒的正式量測出現一次 42.0。取樣次數多一倍就碰到尖峰，所以**還不能說穩定達到 5%**。
- 尖峰的來源、修正與修正後的數字見 §13.5（原本推測的 DOM 排版、陰影重繪不是主因）。
- 優化前，幀間隔 p95 是 18.6 ms（略高於 18）；最終量測降到 17.8 ms。

### 13.3 沒有實作的項目

- **O3（遮罩快取）**：O1 第一版的統計顯示，出場時頂點幾乎每幀都在變（每幀約 61 次上傳、2.4 次相同），遮罩來源也包含在內，快取命中不了，對峰值沒有幫助。
- **O4（不畫畫面外的 drawable）**：潛伏時已經停格，只剩退場後約 1 秒的收尾會畫。峰值發生在出場，O4 對它沒有影響。

### 13.4 驗收狀態

| # | 結果 |
|---|---|
| T1 | `node --test` 101 pass：狀態機 79 項、幀率閘門 9 項、物理變化量 4 項、內插 2 項、渲染包裝 7 項。既有 4 項的預期依 D2 修改：喚醒時的表情 happy→surprised、移除 `fps` 欄位、隨機序列允許 Talk 與新表情、「Talk 不在白名單」改成「不使用 Blink」 |
| T2 | PASS（`frame-rate.test.js`） |
| T3 | PASS。另外用一次性腳本比對新舊 `physics-clock.js`：2 萬幀假輸入，結果逐位元相同 |
| T4／T5 | `pytest` 1725 passed、5 skipped（含 `-m unit` 與 `test_services.py`） |
| R1／R2 | `ui_demo.py --capture`：13 張截圖都有 png 與 state，11 個比對點全部 PASS（喚醒、執行、成功、失敗、說話、托腮、連續失敗、會話結束、退場、潛伏時點頭部、點身體） |
| R3 | 部分（§13.1） |
| R4 | PASS |
| R5 | 停格 PASS；出場峰值 **未穩定達標**：`ui_perf` 多次量測 38.1～42.0，門檻 40（§13.5） |
| R5b／R6 | PASS |
| R7 | 未執行（需要人工說話） |
| U1～U6 | 待使用者。U6 沒有做逐像素比對：動畫和時間相關，正式程式裡截不到兩張同一時刻的畫面，只人工看過截圖 |

### 13.5 出場尖峰的來源與修正（2026-11-20）

**方法**：Electron 加 `--js-flags=--trace-gc --trace-opt --trace-deopt` 與 Chromium trace，跑 180 秒演出腳本，每 0.5 秒取樣各程序 CPU；trace 的時間軸與 Python `time.monotonic()` 對齊。

**來源**：
- 每個尖峰都對上 renderer 的一次 Major GC（Incremental Mark-Compact），誤差 0.1 秒內。主執行緒只暫停 1～2 ms，所以不掉格。
- GC 之後約 60～90 個熱函式因 `embedded weak objects cleared` 被判定程式碼失效，重新編譯：TurboFan 約 140～260 ms、Maglev 約 30～40 ms，都在背景執行緒，加上 GC 標記，就是尖峰多出來的約 55～70 ms CPU（0.5 秒內 +11～14 點）。
- heap 每輪約增加 3.5 MB 才觸發下一次 Major GC。推測是重新編譯產生的舊程式碼，**未驗證**。
- 次要：隱藏中的聊天室視窗每收到一則結果就重畫，約 20～40 ms CPU。

**修正**（2026-11-20 使用者同意 1、2、3）：
1. O1、O2 的 GL 包裝只在啟動時裝一次，每次繪製只更新模組變數；原本每次繪製都新建 closure、換掉 `gl` 的方法。
2. `vertex-interp.js` 的 `computed` 內層迴圈拆成獨立函式 `midpoint`（原本 180 秒內 OSR deopt 50 次）。
3. 聊天室視窗隱藏時，`main.js` 不推送紀錄；展開前（`openChat`）才一次補送。

**效果**（180 秒追蹤量測，修正前→後）：總 deopt 114→22、OSR deopt 91→0、編譯總數 714→676；Major GC 之後重新編譯的規模沒有變（每輪仍約 60～90 個函式）。

**同條件對照**（`ui_perf.py` 60 秒；舊版＝修正前的程式暫時換回，各 2 次，單核 %）：

| 項目 | 舊版 | 新版 |
|---|---|---|
| 出場平均 | 35.4、34.8 | 34.4、33.6 |
| 出場每秒峰值 | 40.0、40.1 | 40.1、39.1 |
| 進退場每秒峰值 | 40.0、39.1 | 42.0、38.1 |
| 每幀 compute／render 平均（ms） | 1.29／0.45、1.28／0.47 | 1.23／0.42、1.24／0.42 |
| 每幀 JS p95（ms） | 4.00、4.00 | 3.90、3.80 |

**結論**：
- 平均省約 1 點、每幀 JS 略低；**峰值沒有可分辨的差別**。到目前為止 `ui_perf` 共 8 次量測（舊版 2、新版 2、旗標實驗 2、修正前正式 2），出場與進退場每秒峰值落在 38.1～42.0，門檻 40 正好在分佈的上緣，**R5 出場峰值仍然不能說穩定達標**。
- 幀間隔 p95 與 RSS 在這次量測時，新舊版都超標（舊版 18.6～18.7 ms、778～783 MB；新版 18.4～18.7 ms、784～794 MB）。今天稍早的量測是 17.8 ms、693 MB。新舊版一樣，所以不是這次修正造成，系統狀態為何不同**未查**。
- 實驗（**未採用**）：Electron 加 `--js-flags=--initial-old-space-size=64`。180 秒追蹤量測 renderer 完全沒有 Major GC、最高 36.5%；但同樣的旗標跑 `ui_perf` 60 秒，峰值仍是 39.1、42.0、41.2、39.2，對驗收沒有幫助，所以不採用。`ui_perf` 的峰值是否來自出場一開始的 JIT 暖機編譯，**未驗證**。
- 要再降低峰值，剩下的是降低整體負載（例如 `resolution` 上限、遮罩緩衝尺寸），會影響畫質，需要使用者決定。
