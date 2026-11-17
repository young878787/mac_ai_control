"""conftest 會把下列函式換成假的；需要真的那一份的測試從這裡拿（模組載入時先存下來）"""
import subprocess
import urllib.request

from mac_ai.router import context

real_run = subprocess.run  # sh／find_env fixture 會換掉 subprocess.run，需要真的 osacompile 等時用這個
REAL_URLOPEN = urllib.request.urlopen  # 需要真的連線的測試（本機 socket）用這個
REAL_READ_FRONT, REAL_READ_HOST = context.read_front, context.read_host  # 測讀取函式本身的測試用這個
