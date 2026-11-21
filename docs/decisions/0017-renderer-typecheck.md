# 0017 畫面程序的型別檢查：PIXI 用真實型別，其餘宣告為 any

- 狀態：採用
- 日期：2026-11-21
- 來源：[拆 renderer/role/app.js 計畫](../plans/completed/2026-11-21-desktop-app-split.md) §1、§5.4；使用者 2026-11-21 確認

## 背景

分層計畫 D4 規定用 JSDoc＋`checkJs` 做型別檢查，不開 `strict`。`renderer/` 要納入檢查時，要決定畫面程序的全域變數要宣告到什麼程度。

## 決定

1. `renderer/` 由 `tsconfig.renderer.json` 檢查（加 DOM lib，不載 Node 型別），`npm run typecheck` 兩份 config 都跑。
2. `PIXI` 與 `PIXI.live2d` 用 `node_modules` 裡 `pixi.js`、`pixi-live2d-display` 的真實型別，因為 PIXI 的內部 API 多、打錯只會在執行時悄悄壞掉。
3. 下列全域宣告成 `any`（集中在 `desktop/types/globals.d.ts`）：
   - `Live2DCubismCore`：只用到 `Version.csmGetVersion()`。
   - `desktop`、`chat`、`window.__ui`、`window.__chat`：preload 橋接與除錯探針。方法簽名不另外寫，和 `preload/*.cjs` 不同步的風險由實機驗收（`ui_demo`）抓。
   - `PIXI.live2d` 裡 pixi-live2d-display 0.4.0 型別未涵蓋的 Cubism 內部類別（動作佇列、表情、物理、著色器）。
4. 不新增 `@ts-ignore`／`@ts-expect-error`／`@ts-nocheck`。需要時以 JSDoc 轉型處理，並在 PR 或計畫中列出。

## 後果

- 改 PIXI 呼叫的方法名或參數會在 typecheck 時報錯。
- `desktop` 橋接的欄位打錯不會被型別抓到，要靠 `ui_demo`、`ipc-channels.test.js`。
- 若要把橋接也型別化，另開計畫（會多一個要同步的檔案）。
