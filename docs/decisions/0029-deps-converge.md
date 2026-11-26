# 0029 依賴收斂：比對與執行分開、底層工具歸位

- 狀態：採用
- 日期：2026-11-26
- 來源：[計畫書](../plans/completed/2026-11-26-deps-converge.md) §2；使用者 2026-11-26 的決定

## 背景

[0026](0026-import-linter.md) 上線時有 5 條例外和 1 個循環。根因都是東西放錯檔案：`_run` 和需要 LLM 的工具同檔、拼音比對放在使用 sqlite 的模組、對應表放在執行模組、`Match` 自己執行動作。另外 `router/__init__.py` 頂層 import 全部子模組，只 import `lexicon` 也會載入整個執行器與網路 client（31 個模組），import-linter 看不到這條路。

## 決定

1. **三個底層模組**：`router.proc`（外部指令 `_run`）、`router.pinyin`（拼音比對）、`router.action_plan`（`Intent` → 工具呼叫的對應表）。
2. **`fast_path` 只比對、不執行**：`Match` 只有資料，由 `pipeline` 呼叫工具。`Match.run`、`Match.__call__`、`FastPathMatcher.try_execute` 刪除。
3. **`router/__init__.py` 用到才 import**（PEP 562），不在頂層 import 子模組；新的對外名稱加進 `_EXPORTS`。由 `tests/runtime/test_import_footprint.py` 守住。
4. **會呼叫 `run_tool` 的入口模組（`pipeline`、`slow_path`）在頂層 import `local_actions`**，本機工具靠它登錄。登錄方式要不要改，留到拆分 `local_actions` 時決定。
5. **例外只剩 2 條**，取代 0026 第 2 條的清單：`db.rebuild → router.music_prefs`（C1）、`router.catalog → router.proc`（C4、C5；`catalog` 要跑 `mdfind` 掃已安裝 App，只在快取是空的或過期時執行）。
6. **ast-grep 規則**：刪除 `catalog-lazy-actions-import`（保護的延遲 import 已不存在）；`subprocess-needs-timeout` 擴及 `proc._run`，`hot-path-no-io` 擴及 `action_plan.py`。

## 後果

- 沒有循環。熱路徑單獨 import 的範圍變小（`lexicon` 31 → 4 個模組，`stt.completeness` 34 → 15）。`main.py` 仍載入全部模組，啟動與每句指令的速度沒有變；`match()` p95 不變。
- C3 的底層 router 模組從 22 個變 25 個。
- `local_actions.py` 仍超過 [0028](0028-file-line-limit.md) 的 800 行，拆分另開計畫。
- 結案時沒有跑：`pytest -m app`、CI、語音實機。
