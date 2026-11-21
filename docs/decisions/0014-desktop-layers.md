# 0014 桌面前端分層，依賴只能往下

- 狀態：採用
- 日期：2026-11-20
- 來源：[桌面前端分層整理](../plans/completed/2026-11-20-desktop-layering.md) §0、§1、§8 D1～D4

## 背景

`main.js` 和 `renderer/app.js` 各做 7～10 件事；模型的事實散在 3 個檔案；與後端的協定常數只靠註解「要一致」維持。

## 決定

1. 第一刀依行程切（`main/`、`preload/`、`renderer/`、`shared/`），第二刀在畫面行程內依離函式庫多近切（`domain/` → `renderer/animation/` → `renderer/engine/`），`rig/` 是資料，`dev/` 是量測。依賴只能往下，由 `desktop/test/layers.test.js` 掃原始碼檢查。
2. 狀態機只說語意名稱，由 rig profile 一對一對應回模型名稱（D2）；換模型就新增一份 profile。
3. 與後端的合約集中在 `shared/contract.js`，`tests/unit/test_ui_contract.py` 與 `ipc-channels.test.js` 比對兩端。
4. 型別檢查用 JSDoc＋`checkJs`（`tsc --noEmit`），不開 `strict`，不新增 `@ts-ignore`，不改執行方式、不打包（D4）。
5. 畫面行程只能讀 `app-protocol.js` 白名單內的檔案；`main/`、`preload/`、`dev/` 不得加入白名單。

## 後果

- 新檔要先決定屬於哪一層，放錯位置測試會直接失敗。
- S5（拆 `app.js`）在分層計畫結案時沒做，另開 [拆 app.js 計畫](../plans/completed/2026-11-21-desktop-app-split.md) 在 2026-11-21 完成：engine 多了 `stage`／`load-model`／`loop`，animation 多了 `animator`，`renderer/role/` 多了 `pointer`／`probe`；型別檢查範圍納入 `renderer/`（`tsconfig.renderer.json`），型別宣告的取捨見 [0017](0017-renderer-typecheck.md)。
