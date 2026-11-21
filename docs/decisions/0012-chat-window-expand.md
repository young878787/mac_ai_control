# 0012 聊天室是獨立視窗，主動展開不搶焦點

- 狀態：採用
- 日期：2026-11-18
- 來源：[聊天室視窗與主動展開](../plans/completed/2026-11-18-chat-window-expand.md) §2 D1～D9

## 背景

看畫面、聊天、多步驟指令的回覆太長，角色的泡泡放不下。

## 決定

1. 聊天室是獨立視窗，平常不開。紀錄只放記憶體，重開就清空；不做後台。
2. 後端用 `pipeline.expand_reason` 判斷是否展開（`result.expand` 是選用欄位）：看畫面成功 → `screen`；AI 回覆被採用 → `chat`；動作數 ≥ 2 或回覆超過 20 字 → `complex`。
3. 主動展開一律用 `showInactive()`，不得搶焦點，因為前景 App 決定 `front_action`、分頁、看畫面的對象。沒有互動時 20 秒收回，有互動就一直開著；手動打開的不會自動收。
4. 聊天室有自己的 preload；送出的文字經主程序驗證，只能是 `command`；IPC 只認自己的 webContents；CSP 不放 `unsafe-inline`／`unsafe-eval`。
5. 聊天室隱藏時不推送紀錄，展開前才一次補送（2026-11-20 為了降低 CPU 尖峰）。

## 後果

- 調整展開條件只動 `config.EXPAND_*` 與 `expand_reason`，案例在 `tests/flow/test_route_expand.py`。
