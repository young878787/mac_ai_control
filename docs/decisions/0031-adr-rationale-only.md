# 0031 ADR 只記理由；現行規則以指南與自動檢查為準

- 狀態：採用（修改 [0016](0016-docs-structure.md) 第 3 條）
- 日期：2026-11-27
- 來源：[計畫書](../plans/completed/2026-11-27-docs-index.md)；使用者 2026-11-27 的決定

## 背景

ADR 到 0030 共 30 份、約 97 KB。其中 7 份被後來的 ADR 部分修改，形成修改鏈：天氣目前的規則要讀 0021、0023、0024 三份才拼得出來。同一條規則又同時寫在 `AGENTS.md` 紅線、`docs/guides/coding-style.md`、ADR 三處，改一處容易漏改另一處。AI 每次只自動載入 `AGENTS.md`，但改行為前會去讀 ADR，修改鏈越長讀得越多，也越容易拼錯。

## 決定

1. **ADR 只記理由**：背景、當時的實測、使用者的決定、取捨。寫成之後內文不再修改，只有狀態行可以補註「第 N 條被 NNNN 修改／取代」。
2. **現行規則以這些地方為準**：`docs/guides/coding-style.md`（守則與常見修改）、`docs/guides/testing.md`（測試與驗收）、`AGENTS.md` 紅線（摘要），以及能機械判定的 lint、專案規則、依賴契約與測試。ADR 與它們不一致時，以現行規則為準，並修正現行規則的出處標註。
3. **改決定時同一個 commit 要做三件事**：新增一份 ADR 寫理由並在舊 ADR 狀態行補註；更新 `docs/decisions/README.md` 索引；更新 coding-style（或 testing.md）的現行規則。推翻 ADR 前先問使用者（0016 第 3 條不變的部分）。
4. **索引** `docs/decisions/README.md`：一列一份 ADR，寫狀態、領域、被誰修改或取代、強制方式（rule id、契約或測試）。AI 先讀索引，再開需要的 ADR。`tests/unit/test_docs_index.py` 檢查索引和檔案一致、引用的規則與測試存在。
5. **能機械判定的規則優先寫成檢查**，ADR 編號寫在錯誤訊息或測試說明裡；只靠文字的規則留在 coding-style。

## 後果

- 平常改程式只需讀 `AGENTS.md`、coding-style 與 lint 結果；要知道「為什麼」或要推翻規則時才讀 ADR。
- 既有修改鏈（0005、0009、0015、0019、0021、0022）的現行規則整理進 coding-style 與 testing.md，舊 ADR 內文不改。
- coding-style 會隨功能變長；接近難以閱讀時再依領域拆分，拆分前先提計畫。
