"""本機目錄：詞庫 App ＋ 已安裝 App 清單 ＋ 專案。範圍判斷（domain.py）只透過這裡唯讀查詢。"""
import os
import time
from typing import Callable, Dict, Optional, Tuple

from mac_ai.config import APP_INDEX_REFRESH_S
from mac_ai.router.lexicon import _compact, resolve_app, resolve_project

APP_DIRS = ("/Applications", "/System/Applications", "~/Applications")
_DISPLAY_SEP = "   kMDItemDisplayName = "
_SCAN_TIMEOUT_S = 5.0


def _scan_installed() -> Dict[str, str]:
    """用 Spotlight 列出已安裝 App → {compact 名稱: 標準名稱（.app 檔名）}。
    bundle 檔名與系統語言的顯示名稱（『計算機』『郵件』）都查得到；包在其他 .app 裡面的不算"""
    from mac_ai.router.actions import _run  # 函式內才匯入：actions → llm_client → tools → catalog，頂層匯入會循環
    cmd = ["mdfind"]
    for d in APP_DIRS:
        cmd += ["-onlyin", os.path.expanduser(d)]
    cmd += ['kMDItemContentType == "com.apple.application-bundle"', "-attr", "kMDItemDisplayName"]
    ok, out = _run(cmd, _SCAN_TIMEOUT_S)
    index: Dict[str, str] = {}
    if not ok:
        return index
    for line in out.splitlines():
        path, _, display = line.partition(_DISPLAY_SEP)
        path = path.strip()
        if not path.endswith(".app") or ".app/" in path:
            continue
        name = os.path.basename(path)[:-4]
        for label in (name, display.strip()):
            key = _compact(label)
            if key and key != "(null)":
                index.setdefault(key, name)
    return index


class Catalog:
    """已安裝 App 清單第一次用到才掃描並快取；查不到時由 refresh_if_stale() 在每次指令開始時決定要不要重掃"""

    def __init__(self, scan: Optional[Callable[[], Dict[str, str]]] = None, clock: Callable[[], float] = time.monotonic,
                 refresh_s: float = APP_INDEX_REFRESH_S):
        self._scan = scan
        self._clock = clock
        self._refresh_s = refresh_s
        self._installed: Optional[Dict[str, str]] = None
        self._scanned_at = 0.0
        self.scans = 0

    def refresh(self) -> None:
        self._installed = (self._scan or _scan_installed)()
        self._scanned_at = self._clock()
        self.scans += 1

    def refresh_if_stale(self) -> None:
        if self._installed is not None and self._clock() - self._scanned_at >= self._refresh_s:
            self.refresh()

    def installed(self) -> Dict[str, str]:
        if self._installed is None:
            self.refresh()
        return self._installed or {}

    def resolve_app(self, token: str, candidates: bool = False) -> Optional[Tuple[str, str, str]]:
        """詞庫（別名、容錯）優先，再查已安裝 App（完全比對）。回傳 (標準名稱, 信心, 說明)"""
        hit = resolve_app(token, candidates)
        if hit is not None and hit[1] == "exact":
            return hit
        name = self.installed().get(_compact(token))
        if name is not None:
            return name, "exact", ""
        return hit

    def resolve_project(self, token: str, candidates: bool = False):
        return resolve_project(token, candidates)


DEFAULT = Catalog()
