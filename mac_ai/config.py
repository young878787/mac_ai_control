"""路徑、LLM 連線與逾時設定（單一來源）。"""
from pathlib import Path

ROOT = Path(__file__).parent.parent
LEXICON_PATH = ROOT / "vocab" / "lexicon.json"
STT_HINTS_PATH = ROOT / "vocab" / "stt_hints.txt"
LOG_DIR = ROOT / "logs"
NORMALIZE_LOG = LOG_DIR / "normalize.jsonl"

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

WAKE_WORD_ENABLED = False  # False＝常啟動，每一句都當指令；True＝句首要有喚醒詞才執行
WAKE_WINDOW_S = 8.0  # 只說喚醒詞之後，這段時間內的下一句不用喚醒詞也會執行

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
EP_SILENCE_DEFAULT_S = 0.8  # 不確定是否完整（例如交給 LLM 的句子）
EP_SILENCE_INCOMPLETE_S = 1.6  # 句尾是接續詞／缺受詞：多等一下
EP_PARTIAL_STABLE_S = 0.10  # partial 這麼久沒變才算穩定
EP_ASR_STALL_S = 1.2  # VAD 說在說話、但 partial 這麼久沒變 → 當成噪音，靜音從 partial 最後一次變動算起
EP_NO_TEXT_RESET_S = 2.0  # 開始說話這麼久仍沒有任何 partial → 當成噪音，放棄這一段
EP_MAX_UTTERANCE_S = 10.0  # 一句最長，到了強制 flush
EP_FINAL_WAIT_S = 3.0  # 送出 flush 後最多等這麼久的 final，沒來就當作這一句結束
EP_SERVER_BACKUP_FLUSH_S = 3.0  # client 模式下，server 的靜音斷句只當備援（我們沒送 flush 時才會動）
EP_FLUSH_PRE_DELAY_S = 0.0  # client 模式：flush 後、endAudio 前的等待。flush 只在「說完後 partial 已更新」才發，引擎此時已處理完句尾
STT_FLUSH_PRE_DELAY_S = 0.4  # server 模式（舊行為）：靜音斷句後、endAudio 前的等待
LATENCY_LOG = LOG_DIR / "latency.jsonl"
STT_RECONNECT_MIN_S = 1.0  # 斷線重連的等待時間（每次加倍，最多 MAX）
STT_RECONNECT_MAX_S = 10.0
STT_PID_FILE = "/tmp/macstt_pid"  # start.py 寫入的後端（.app 內）PID

LLM_START_TIMEOUT_S = 90.0
STT_START_TIMEOUT_S = 60.0  # 首次執行要留時間給使用者按下語音辨識授權
STOP_SIGINT_GRACE_S = 3.0  # 結束服務：先送 SIGINT 等這麼久，沒結束再 SIGTERM，最後 SIGKILL
STOP_SIGTERM_GRACE_S = 1.5
