# AGENTS.md

給在這個 repo 工作的 AI 程式助手：改程式前必讀的指示牌。人類使用者文件在 README.md。

## 專案與北極星

Apple Silicon Mac 上的語音助手：麥克風 → macos-stt → 路由 → 執行 macOS 動作。Fast Path 與本機 LLM 都在本機；雲端是選用的加分，`.env` 沒有金鑰時，行為要與沒有雲端逐字相同。
**兩個目標：快、準。** 任何改動都要先問：會不會讓常見指令變慢？會不會讓誤觸發變多？衝突時準確優先（誤開、誤關、誤執行的成本遠高於多等 1 秒），但不能讓 Fast Path 退化成要等 LLM。

## 文件地圖

| 要找 | 看 |
|---|---|
| 模組職責、桌面分層、延遲目標 | `ARCHITECTURE.md` |
| 輸入→路由→執行的完整流程、Slow Path 與 `validate_calls` 順序 | `docs/flows/main-flow.md`、`docs/flows/slow-path.md` |
| 為什麼這樣做（實測依據、使用者決定） | `docs/decisions/NNNN-*.md`（ADR） |
| 「快」「準」守則全文、常見修改怎麼做、不要碰的東西 | `docs/guides/coding-style.md` |
| 測試指令、基線、分層與 fixture | `docs/guides/testing.md` |
| 計畫：進行中 `docs/plans/active/`，結案 `docs/plans/completed/`（不刪） | 新計畫用 `YYYY-MM-DD-slug.md`；結構規則見 ADR 0016 |

改行為前先看對應的 ADR。要推翻 ADR 時先問使用者，再新增一份 ADR，並把舊的標成「被 NNNN 取代」。

## 常用指令

```bash
source .venv/bin/activate         # Python 3.12
pytest -m unit                    # 約 1.4 秒，改程式先跑
pytest                            # 離線全部，必須 0 failed
cd desktop && node --test && npm run typecheck
python main.py --text 打開小算盤   # 不起服務直接跑一句
```

live／cloud／app 實測與驗收腳本見 `docs/guides/testing.md`。

## 紅線（細節與理由見 coding-style.md 與 ADR）

1. 常見指令先加 Fast Path 規則（`fast_path.py` 的 `_r_*`），不加 LLM 工具。Fast Path、`match()`、`completeness` 不碰網路、LLM、子程序，也不讀前景。（0001、0003）
2. `SYSTEM_PROMPT`＋`TOOLS_SCHEMA` 逐字穩定、不增大；動態資訊只放 user 訊息；LLM 呼叫只走 `llm_client._llm`。（0002）
3. 改 Fast Path 規則會連動端點偵測：要跑 `test_completeness.py`、`test_endpoint.py`。（0003）
4. 安全守門在最前面：否定、敘述、提問不走 Fast；否定或敘述交給 Slow 後也不呼叫任何模型（G1）。放寬守門前先列誤觸發案例。（0007）
5. `validate_tool_call` 是唯一的信任邊界；LLM 編出來的名稱不能直接 `open -a`。本機工具對 LLM 保持看不到（雲端只有 `front_action`、`site_search` 兩個例外）。（0011）
6. 找檔案預設只在 Finder 顯示，危險檔一律只顯示，範圍限家目錄。（0005）
7. 對象順序固定：明說 → 同句剛開 → 前景 → 預設；讀不到就退回預設，不猜。（0006）
8. 常用功能走 `front_action`→`native.py`；送按鍵只允許 ⌘ 組合鍵與翻頁鍵；不任意打字、不放結束程式或刪除類。（0008）
9. 雲端只用在 Slow Path 規劃，失敗退回本機；不能繞過任何守門，不能謊稱已執行。（0009）
10. 金鑰只在 `cloud_client` 讀，不進 `os.environ`、log 或錯誤訊息。（0010）
11. 結果字串前綴 ✅／❌／⚠️／⏭️ 決定 status，不要把失敗寫成 ✅。送 LLM 的永遠是原句。
12. 外部指令一律設逾時；`command_worker` 是單執行緒，不加長時間阻塞。效能主張要附實測數字。
13. 桌面：依賴只能往下（`layers.test.js`）；匯流排只綁 127.0.0.1＋token＋不收 Origin；聊天室主動展開只用 `showInactive()`。（0012、0014）
14. 擬人化的指標門檻在 `desktop/dev/motion-metrics.js`，不得為了過關放寬。（0015）

## 不要碰

- `archive/`：唯讀參考，不改、不 import。`macos-stt/`：獨立 repo，不在本 repo 提交範圍。
- 不入版控：`AI_models/`、`Rushia_hd/`、`desktop/vendor/`、`desktop/node_modules/`、`logs/`、`bench/synth/`、`*.wav`、`.env*`。
- 不修改 `.env`、production 設定或外部服務。`vocab/lexicon.json` 的 `project_roots`、`projects.path` 是個人設定。
- `catalog._scan_installed` 在函式內 import `actions`（避免循環匯入），不要搬到檔案頂層。

## 工作流程

- 繁體中文（臺灣用語），先給結論。簡單任務直接做；多檔案、架構、資料流、行為變更、效能、安全性的任務先提計畫，等確認再做。
- 只改需求必要的部分，不順手重構，不新增 dependency；產品行為與驗收標準由使用者決定，偏離時立即停下提出。
- 新增行為要新增測試；修 bug 先寫能重現的測試；已知問題用 `xfail(strict=True)` 並寫日期。
- 不 commit、push、開 PR，除非使用者要求；commit 用 `feat:`／`fix:`／`docs:` 一行摘要，不加任何 AI 署名。
- 回報分開寫「已實際執行確認」與「尚未驗證」。STT 語音實機沒有自動測試，不要聲稱已驗證。
- 計畫結案時移到 `docs/plans/completed/`，把其中仍然有效的決策寫成 ADR，並同步 `ARCHITECTURE.md` 與 flows。
