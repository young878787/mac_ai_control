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
STT_MULTI_UTTERANCE = False  # True＝引擎自斷句；實測喚醒詞後面一停頓，短片段（<6 字）會被丟掉，所以改用靜音斷句
STT_VAD_FLUSH_S = 1.5  # 靜音這麼久就把這句送出（STT_MULTI_UTTERANCE=False 時就是斷句時間）
STT_RECONNECT_MIN_S = 1.0  # 斷線重連的等待時間（每次加倍，最多 MAX）
STT_RECONNECT_MAX_S = 10.0
STT_PID_FILE = "/tmp/macstt_pid"  # start.py 寫入的後端（.app 內）PID

LLM_START_TIMEOUT_S = 90.0
STT_START_TIMEOUT_S = 60.0  # 首次執行要留時間給使用者按下語音辨識授權
STOP_SIGINT_GRACE_S = 3.0  # 結束服務：先送 SIGINT 等這麼久，沒結束再 SIGTERM，最後 SIGKILL
STOP_SIGTERM_GRACE_S = 1.5
