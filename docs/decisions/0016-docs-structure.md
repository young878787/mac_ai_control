# 0016 文件結構：指示牌、架構、ADR、流程、計畫、指南

- 狀態：採用（第 3 條被 [0031](0031-adr-rationale-only.md) 修改：ADR 只記理由，現行規則以指南與自動檢查為準）
- 日期：2026-11-21
- 來源：使用者決定

## 背景

原本只有一份 268 行的 `AGENTS.md`，以及 5 份「計畫＋決策＋驗收＋實作結果」混在一起的計畫書。決策散在各份計畫書裡，編號互相撞名（D1 在三份計畫書裡意思都不同），找不到目前有效的決定。另外有 6 份計畫書在整理時被刪掉，只剩 git 歷史。

## 決定

1. 文件結構（全部入版控）：
   - `AGENTS.md`：指示牌，80 行以內：北極星、文件地圖、常用指令、紅線、不要碰、工作流程
   - `CLAUDE.md`：只有一行 `@AGENTS.md`
   - `ARCHITECTURE.md`：延遲目標、行程、模組職責、桌面分層
   - `docs/decisions/NNNN-slug.md`：ADR，依編號，一份一個決定
   - `docs/flows/`：跨模組流程（Mermaid＋文字）
   - `docs/plans/active/`、`docs/plans/completed/`：計畫，檔名 `YYYY-MM-DD-slug.md`，結案移到 completed、不刪
   - `docs/guides/`：測試（`testing.md`）、程式守則與常見修改（`coding-style.md`）
2. 計畫結案時，把其中仍然有效的決策寫成 ADR，並同步 `ARCHITECTURE.md` 與 flows。計畫書內文保留結案時的原樣，只在開頭加歸檔說明與原檔名。
3. 推翻 ADR 時先問使用者，再新增一份 ADR，並把舊的標成「被 NNNN 取代」。
4. 已刪的 6 份計畫書從 git 歷史還原到 `completed/`。

## 後果

- 程式註解引用計畫書時，寫 `docs/plans/completed/` 下的新路徑。
- `README.md` 是給人看的使用說明，不放 AI 守則。
