# 0004 喚醒詞與 60 秒滑動會話；桌面角色用 Electron

- 狀態：採用
- 日期：2026-11-05
- 來源：[桌面角色介面與喚醒會話](../plans/completed/2026-11-06-desktop-character-wake.md) §2、§5；[合併計劃](../plans/completed/2026-11-04-merged-plan.md)

## 背景

原本喚醒後只有 8 秒的一次性待命。桌面角色需要連續對話，而且角色的反應不能顯得遲鈍。

## 決定

1. 喚醒詞（露西婭／露醬…，來源是詞庫）預設關閉（`WAKE_WORD_ENABLED=False`）；`--ui` 強制開啟。
2. 會話 60 秒，每句重新計時。會話中沒有喚醒詞的句子照樣執行；只有喚醒詞的句子 → `armed`；空白句不續時。
3. `--ui` 時 `WakeGate` 與 `EndpointDetector` 的喚醒詞設定一起切換。UI 看到 `partial.wake` 就先出場，只做演出、不執行。
4. 桌面殼用 Electron，畫面用原生 ES modules，不加 React／Vite。不做 TTS 與口型，角色不走路，停靠在主螢幕右下角。
5. macos-stt 是獨立的子專案（自有 venv、獨立 git），在 `.app` bundle 內以 `start.py serve` 啟動，主程序經 WS `/stream` 串接。

## 後果

- UI 匯流排只綁 127.0.0.1、需要 token、不收帶 `Origin` 的連線，訊息一律經 `parse_ui_message` 驗證。
