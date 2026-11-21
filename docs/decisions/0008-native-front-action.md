# 0008 常用功能以底層 API 直接執行（native.py）

- 狀態：採用
- 日期：2026-11-15
- 來源：[前景視窗上下文與常用功能](../plans/completed/2026-11-14-front-window-context.md) §16 D13

## 背景

模擬快捷鍵會先經過輸入法：注音啟用時，⌃`（VS Code 內建終端機）被當成打字，在聊天輸入框打出「、」。

## 決定

1. 常用功能只走 `front_action`，由 `native.py` 用 ctypes 直接呼叫輔助使用 API 與 CGEvent，對前景 App 的 pid 下指令，不經 osascript、不加套件、不拋例外。
2. `app_shortcuts` 的每個綁定擇一：`window`（關閉鈕、最小化、全螢幕屬性）、`path`（依選單標題，各語言並列）、`char`＋`mods`（依快捷鍵找 App 自己的選單項目）、`button`（視窗裡說明文字符合的按鈕）、`script`（App 自己的指令）。
3. 找不到選單項目才送按鍵，送之前重讀前景確認 pid 沒變；送按鍵只允許 ⌘ 組合鍵與翻頁鍵。
4. 功能不支援目前的 App 時什麼都不做（有 fallback 才執行原動作）。不做任意打字、不在終端機輸入指令；結束程式與刪除類不放進表。

## 後果

- 改 `front_action`、`app_shortcuts`、`native.py` 時要跑 `pytest -m app`（真實 App 驗收）。
