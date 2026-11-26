"""外部指令：全專案共用的一次性子程序呼叫（長駐服務用 Popen，在 services）。底層模組，只依賴標準庫。"""
import subprocess
from typing import List, Optional, Tuple


def _run(cmd: List[str], timeout: Optional[float] = None) -> Tuple[bool, str]:
    """執行外部指令並檢查結束碼。回傳 (成功?, 成功時的 stdout／失敗時 stderr 最後一行)；
    timeout 用於可能卡在授權對話框的指令（逾時視為失敗）"""
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"逾時 {timeout:g}s（可能在等待授權對話框）"
    except OSError as e:  # 指令不存在或無法執行
        return False, str(e)[:120]
    if p.returncode == 0:
        return True, (p.stdout or "").strip()
    lines = (p.stderr or "").strip().splitlines()
    return False, (lines[-1][:120] if lines else f"結束碼 {p.returncode}")
