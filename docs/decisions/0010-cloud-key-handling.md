# 0010 雲端設定放 .env，金鑰只在 cloud_client 讀

- 狀態：採用
- 日期：2026-11-15
- 來源：[端雲協同](../plans/completed/2026-11-15-cloud-hybrid.md) §0-5、K1、K6、§13.1

## 背景

`services.py` 會把 `os.environ` 傳給 mlx_vlm、STT、Electron 子程序，金鑰寫進環境變數就會外洩給它們。

## 決定

1. `AI_API_URL`、`AI_MODEL`、`AI_API_KEY` 放在專案根目錄的 `.env`，不進版控；程式不寫入 `.env`，只提交 `.env.example`。金鑰留空等於停用雲端。
2. 不新增 `python-dotenv`，由 `config.env_value()` 解析。
3. 金鑰只在 `cloud_client` 讀，只放在 Authorization header；不寫進 `os.environ`，不進任何 log 或錯誤訊息（錯誤只記 `HTTP 401`、`timeout` 這類分類）。
4. Cloudflare 會擋 `Python-urllib` 預設的 User-Agent（403，error code 1010），所以送 `CLOUD_USER_AGENT`。

## 後果

- `.env` 權限建議 600，程式只警告，不代為修改。
