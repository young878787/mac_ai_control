"""路徑、LLM 連線與逾時設定（單一來源）。"""
import os
import sys
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).parent.parent
LEXICON_PATH = ROOT / "vocab" / "lexicon.json"
STT_HINTS_PATH = ROOT / "vocab" / "stt_hints.txt"
LOG_DIR = ROOT / "logs"
RUNTIME_LOG_DIR = LOG_DIR / "runtime"  # 程式執行時自動追加的紀錄（JSON Lines）
SERVICE_LOG_DIR = LOG_DIR / "services"  # 子服務的 stdout／stderr，每次啟動覆寫
BENCH_LOG_DIR = LOG_DIR / "bench"  # scripts/stt_bench.py 的輸出，一次 run 一個檔
NORMALIZE_LOG = RUNTIME_LOG_DIR / "normalize.jsonl"  # STT 原文→正規化結果（回收誤聽詞）
COMMAND_LOG = RUNTIME_LOG_DIR / "commands.jsonl"  # 每個指令的路由與執行結果（intent、各步 status／ms）
UI_LOG = RUNTIME_LOG_DIR / "ui.jsonl"  # 桌面角色每則訊息從收到到畫面套用的延遲

MLX_VLM_HOST = "127.0.0.1"
MLX_VLM_PORT = 8080
MLX_VLM_URL = f"http://{MLX_VLM_HOST}:{MLX_VLM_PORT}"
MODEL_ID = "AI_models/LFM3-VL-2B-MLX-4bit"  # 須與 mlx_vlm.server 的 --model 一致
MLX_VLM_MAX_TOKENS = 512

PLAN_TIMEOUT_S = 10.0  # 規劃（工具呼叫）逾時
CHAT_TIMEOUT_S = 10.0
VISION_TIMEOUT_S = 30.0
WARMUP_TIMEOUT_S = 60.0  # 首次載入較慢
BREAKER_COOLDOWN_S = 30.0  # LLM 連不上之後，這段時間內不再嘗試

# --- 雲端模型（LiteLLM proxy，OpenAI 相容）。端點、模型、金鑰在 .env（AI_API_URL／AI_MODEL／AI_API_KEY），見 env_value
ENV_PATH = ROOT / ".env"  # 不入版控；程式只讀不寫。範本是 .env.example
CLOUD_ENABLED = True  # False＝只用本機；AI_API_KEY 留空時也只用本機
CLOUD_TIMEOUT_S = 8.0  # 規劃／回覆的總逾時（2026-11-15 實測 130 句：p50 0.87 s、p95 1.59 s、最慢 2.7 s）
CLOUD_VISION_TIMEOUT_S = 15.0  # 看畫面（實測一張圖約 2.5 s）
CLOUD_MAX_TOKENS = 600  # 含推理 token（reasoning_effort=low 實測 40–80）
CLOUD_REASONING_EFFORT = "low"  # 底層 Qwen 的思考深度；low 實測推理 token 76→42、延遲 1.1→0.84 s
CLOUD_USER_AGENT = "mac-ai-control/1.0"  # 端點前有 Cloudflare：Python-urllib 預設 UA 會被擋（實測 403 error code 1010）
CLOUD_NO_CACHE = False  # True＝請求帶 cache:{no-cache:true} 繞過 LiteLLM 的回應快取。proxy 對相同請求逐字回傳同一份答案（實測重複請求 1323→426→400 ms、內容完全相同），
# 日常使用維持快取（快）；實測／A-B 比較一定要開，否則重複執行不是獨立樣本。header 的 Cache-Control 無效，要放 body
CLOUD_VISION_ENABLED = True  # 雲端規劃的看畫面請求，截圖送雲端辨識（使用者 2026-11-15 同意）；失敗退回本機 VL
CLOUD_IMAGE_MAX_BYTES = 250_000  # 送雲端的截圖上限：JPEG 品質 80 超過就改 60（實測整個螢幕縮到 1280、品質 80 約 311 KB）
MEMORY_TURNS = 5  # 對話記憶保留幾輪（給雲端參考、解析「它」）；有效期同 WAKE_SESSION_S

_env_file: Optional[Dict[str, str]] = None  # .env 的內容（第一次用到才讀）
_env_path_loaded: Optional[Path] = None


def parse_env(text: str) -> Dict[str, str]:
    """KEY=VALUE 一行一個；忽略空行與 # 註解、允許 export 前綴、去掉成對引號；不做變數展開。格式錯的行略過"""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def env_value(name: str) -> str:
    """設定值：先看程序環境變數，再看 .env；沒有回空字串。讀到的值不寫回 os.environ（子程序會繼承 os.environ，金鑰會外洩給它們）"""
    global _env_file, _env_path_loaded
    if os.environ.get(name):
        return os.environ[name]
    if _env_file is None or _env_path_loaded != ENV_PATH:
        try:
            _env_file = parse_env(ENV_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            _env_file = {}
        _env_path_loaded = ENV_PATH
    return _env_file.get(name, "")


def reset_env_cache() -> None:
    global _env_file, _env_path_loaded
    _env_file, _env_path_loaded = None, None


WAKE_WORD_ENABLED = False  # False＝常啟動，每一句都當指令；True＝句首要有喚醒詞才執行
WAKE_SESSION_S = 60.0  # 喚醒後的會話長度：這段時間內每一句都不用喚醒詞，且每句都會重新計時

# --- 找東西（App／資料夾／檔案）。檔案搜尋用 Spotlight（mdfind），只搜家目錄且排除 ~/Library
FIND_RESULTS_MAX = 5  # 多筆結果最多列幾筆（也是語音／UI 可選的最大序號）
FIND_CHOICE_TTL_S = 60.0  # 結果清單有效時間，與喚醒會話一致
FILE_SEARCH_TIMEOUT_S = 3.0  # mdfind 逾時
APP_INDEX_REFRESH_S = 60.0  # 已安裝 App 清單：查不到時，最短隔這麼久才重掃一次
CONTEXT_TIMEOUT_S = 1.0  # 讀前景 App（lsappinfo，實測約 15 ms）的逾時；逾時視為沒有上下文

# --- 點歌（YouTube）。搜尋用 InnerTube（不用金鑰），都失敗就開搜尋頁
YT_TIMEOUT_S = 2.0  # InnerTube 兩邊共用的截止時間（實測並行 p50 520 ms、p95 811 ms）
YT_WEB_SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
YT_MUSIC_SEARCH_URL = "https://music.youtube.com/youtubei/v1/search?prettyPrint=false"
YT_WEB_HOME_URL = "https://www.youtube.com/"  # 定時整理時從首頁讀新的 clientVersion
YT_MUSIC_HOME_URL = "https://music.youtube.com/"
YT_WEB_CLIENT_VERSION = "2.20261119.07.00"  # 預設值；maintenance 表有新值時用新值
YT_MUSIC_CLIENT_VERSION = "1.20261117.06.00"
MUSIC_DB_PATH = ROOT / "data" / "music.sqlite3"  # 偏好資料庫（點歌、天氣常問縣市；永久保存、不入版控，啟動時自動建立）
MUSIC_CACHE_TTL_S = 30 * 86400.0  # query→videoId 快取多久沒用就重新查（pinned 不過期）
MUSIC_MAINT_INTERVAL_S = 6 * 3600.0  # 定時整理（terms 衰減與刪除、clientVersion 更新）的間隔
MUSIC_TERMS_MAX = 500  # 點歌字詞矯正詞表上限

# --- 天氣（回答不開網頁）。有 CWA_API_KEY 先用氣象署，失敗或沒有金鑰就用 Open-Meteo（免金鑰）；兩個都失敗直接回查不到
WEATHER_TIMEOUT_S = 2.0  # 每個來源各自的逾時（最差兩個都逾時約 4 秒）
CWA_FORECAST_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"  # 今明 36 小時縣市預報；舊網域 opendata.cwb.gov.tw 已不存在（2026-11-23 實測）
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_DEFAULT_WINDOW_S = 90 * 86400.0  # 沒說縣市時，「常問縣市」統計的時間窗口

# --- 長尾問題回答（搜尋 → 雲端摘要 1～2 句）。要有 BRAVE_API_KEY 與 AI_API_KEY 才啟用；少一個就照舊開搜尋網頁。數值是建議值，實測後在 §9 定案
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_COUNTRY = "TW"  # 搜尋結果偏向臺灣
BRAVE_SEARCH_LANG = "zh-hant"
WEB_SEARCH_TIMEOUT_S = 3.0  # Brave 單次逾時
WEB_SEARCH_COUNT = 5  # 摘錄筆數
WEB_SNIPPET_CHARS = 300  # 每筆摘錄送雲端的字數上限
WEB_ANSWER_TIMEOUT_S = 6.0  # 依摘錄回答的那次雲端呼叫的逾時（規劃那次仍是 CLOUD_TIMEOUT_S）
WEB_ANSWER_MAX_TOKENS = 4096  # 依摘錄回答那次的上限（含推理；推理維持 CLOUD_REASONING_EFFORT）。600 時摘錄互相矛盾會推理到上限、答案空白或被截斷（2026-11-23 實測），使用者 2026-11-23 定 4096
SOURCE_FOLLOWUP_TTL_S = 60.0  # 回答後「要打開來源嗎？」說「好」有效的時間，與喚醒會話一致

# macos-stt 是獨立子專案（自己的 venv），語音辨識授權必須在它的 .app bundle 內取得，所以用 start.py serve 啟動
STT_DIR = ROOT / "macos-stt"
STT_PYTHON = STT_DIR / ".venv" / "bin" / "python"
STT_PORT = 8765
STT_URL = f"http://127.0.0.1:{STT_PORT}"
STT_WS_URL = f"ws://127.0.0.1:{STT_PORT}/stream"
STT_LANG = "zh-TW"
STT_SAMPLE_RATE = 16000  # macos-stt 串流協定 v1 只收 16 kHz mono int16
STT_BLOCK_S = 0.2  # 每 0.2 秒送一幀（約 6.4 KB）
VAD_MODEL_PATH = ROOT / "AI_models" / "silero_vad.onnx"  # Silero VAD v5，下載方式見 README
STT_MULTI_UTTERANCE = False  # True＝引擎自斷句；實測喚醒詞後面一停頓，短片段（<6 字）會被丟掉，所以改用靜音斷句
STT_VAD_FLUSH_S = 1.5  # 靜音這麼久就把這句送出（STT_MULTI_UTTERANCE=False 時就是斷句時間）
# --- 端點偵測（說完話就收）。ENDPOINT_MODE="server"＝舊行為（macos-stt 的能量 VAD 靜音斷句）；"client"＝本端神經網路 VAD＋語意判斷，主動要求 flush
ENDPOINT_MODE = "client"
EP_SEND_S = 0.096  # client 模式把幾個 VAD 窗合成一則 WS 訊息送出（每則訊息 server 都有固定開銷；判斷仍逐窗）
EP_BLOCK_S = 0.032  # client 模式的收音幀長（剛好一個 VAD 窗）；server 模式用 STT_BLOCK_S
VAD_ONSET_PROB = 0.5  # 語音機率 ≥ 這個值才算「在說話」
VAD_OFFSET_PROB = 0.35  # 說話中，機率掉到這個值以下才算「停了」（遲滯：進出門檻不同，不會在邊界抖動）
VAD_ONSET_FRAMES = 3  # 連續這麼多窗（每窗 32 ms）超過 ONSET 才確認開始說話，單一突波（鍵盤、關門）不算
EP_SILENCE_COMPLETE_S = 0.35  # 句子已完整（Fast Path 命中）且 partial 穩定：靜音這麼久就收
EP_SILENCE_COMPLETE_UNCONFIRMED_S = 0.6  # 句子看起來完整、但說完之後 partial 還沒更新過（引擎可能還有字沒吐出來；partial 實測比語音晚 0.3~0.6 s）：靜音要等這麼久
EP_SILENCE_DEFAULT_S = 1.3  # 不確定是否完整（例如交給 LLM 的句子）
EP_SILENCE_INCOMPLETE_S = 2.0  # 句尾是接續詞／缺受詞：多等一下
EP_PARTIAL_STABLE_S = 0.10  # partial 這麼久沒變才算穩定
EP_ASR_STALL_S = 1.2  # VAD 說在說話、但 partial 這麼久沒變 → 當成噪音，靜音從 partial 最後一次變動算起
EP_NO_TEXT_RESET_S = 2.0  # 開始說話這麼久仍沒有任何 partial → 當成噪音，放棄這一段
EP_MAX_UTTERANCE_S = 10.0  # 一句最長，到了強制 flush
EP_FINAL_WAIT_S = 3.0  # 送出 flush 後最多等這麼久的 final，沒來就當作這一句結束
EP_SERVER_BACKUP_FLUSH_S = 3.0  # client 模式下，server 的靜音斷句只當備援（我們沒送 flush 時才會動）
EP_FLUSH_PRE_DELAY_S = 0.0  # client 模式：flush 後、endAudio 前的等待。flush 只在「說完後 partial 已更新」才發，引擎此時已處理完句尾
STT_FLUSH_PRE_DELAY_S = 0.4  # server 模式（舊行為）：靜音斷句後、endAudio 前的等待
LATENCY_LOG = RUNTIME_LOG_DIR / "latency.jsonl"  # 每句話的收音→定稿計時
STT_RECONNECT_MIN_S = 1.0  # 斷線重連的等待時間（每次加倍，最多 MAX）
STT_RECONNECT_MAX_S = 10.0
STT_PID_FILE = "/tmp/macstt_pid"  # start.py 寫入的後端（.app 內）PID

# --- 備用辨識引擎 X-ASR（mac_ai/stt/xasr_server.py，sherpa-onnx，離線、可跑 Windows）。與 macos-stt 說同一份 WS 協定，兩者不同時執行（共用 STT_PORT）
# macOS 預設 macos-stt（比較準）；Windows 沒有 macos-stt，預設 xasr。數值的依據：docs/plans/completed/2026-11-21-stt-engine-xasr.md §1.6
STT_ENGINE = "xasr" if sys.platform == "win32" else "macos"  # "macos"｜"xasr"；main.py --stt-engine 可覆寫
XASR_MODEL_DIR = ROOT / "AI_models" / "x-asr-zh-tw-native-itn"  # Luigi/x-asr-zh-tw-native-itn 的 deployment/onnx-int8-480ms，下載方式見 README
XASR_TOKENS = "tokens_traditional.txt"  # 模型內建的繁體 token 表（用簡體表再靠 normalize 轉，A+C+F 意圖只有 12/24）
XASR_THREADS = 2  # P0：1／2／4 執行緒的 RTF 是 0.043／0.032／0.040
XASR_TAIL_PAD_S = 0.6  # flush 時補在句尾的靜音：模型每 480 ms 才出一段字，P0：補 0.4 秒時 F 類句尾只有 1/3 完整，0.6 秒 3/3
XASR_HOTWORDS_SCORE = 4.0  # stt_hints 熱詞的加分，P0：score 2 是 18/24、score 4 是 19/24

# --- 桌面角色介面（main.py --ui）。UI 是另一個程序（Electron），透過本機 WebSocket 收事件、送指令
UI_BUS_HOST = "127.0.0.1"  # 只綁本機
UI_BUS_PORT = 8766
UI_QUEUE_MAX = 256  # 每個 UI 連線最多積壓幾則事件，滿了丟最舊的（UI 不讀也不會拖住語音）
UI_COMMAND_MAX_CHARS = 200  # 從 UI 打字送來的指令最長字數
EXPAND_MIN_ACTIONS = 2  # 一句話執行了這麼多個動作（chat_reply 不算）就算複雜功能，result 事件帶 expand=complex，聊天室主動展開
EXPAND_REPLY_CHARS = 20  # 回覆總字數超過這個數（泡泡放不下）也算複雜功能（2026-11-18 使用者定）
UI_DIR = ROOT / "desktop"
UI_MODEL_PATH = ROOT / "Rushia_hd" / "runtime-fullrig03" / "RushiaHD.model3.json"
UI_START_TIMEOUT_S = 30.0  # 等 Electron 連上事件匯流排

LLM_START_TIMEOUT_S = 90.0
STT_START_TIMEOUT_S = 60.0  # 首次執行要留時間給使用者按下語音辨識授權
STOP_SIGINT_GRACE_S = 3.0  # 結束服務：先送 SIGINT 等這麼久，沒結束再 SIGTERM，最後 SIGKILL
STOP_SIGTERM_GRACE_S = 1.5
