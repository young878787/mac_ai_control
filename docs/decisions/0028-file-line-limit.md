# 0028 程式檔行數上限 800

- 狀態：採用
- 日期：2026-11-26
- 來源：使用者 2026-11-26 的決定

## 背景

使用者要求單一程式檔過長時 lint 不通過，讓 AI 依職責拆分，並維持程式簡潔。ruff 與 ast-grep 都沒有檔案行數規則；ESLint 的 `max-lines` 只管 JS。

## 決定

1. `scripts/check_file_lines.py`：`git ls-files` 中的 `.py`、`.js`、`.mjs`、`.cjs`、`.ts` 超過 800 行（含空行、註解）即結束碼 1。**測試檔也算**。只排除不歸本 repo 管的 `archive/`、`macos-stt/`、`desktop/vendor/`。
2. 錯誤訊息直接指示：依職責拆到既有模組；需要新模組先向使用者提出計畫（屬於哪一層、名稱、`.importlinter` 位置），確認後再拆。
3. **不設既有超標清單**：上線時 `tests/flow/test_route_music.py`（1226）、`desktop/test/attention.test.js`（1041）、`mac_ai/router/local_actions.py`（1011）超標，CI 紅燈直到拆完，由使用者另行處理。
4. 不靠任何套件，CI 的 `file-lines` job 直接用 runner 的 `python3`。調高上限或新增排除等於改決策，先問使用者（比照 0025 第 5 條）。

## 後果

- 本機 commit 前與 CI（`.github/workflows/lint.yml` 的 `file-lines` job）都會擋。
- 拆 `mac_ai/` 的模組時仍要照 [0026](0026-import-linter.md) 決定層級、更新 `.importlinter` 與 `docs/flows/module-deps.md`。
