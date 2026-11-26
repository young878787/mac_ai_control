"""程式檔行數上限（ADR 0028）：版控中的 .py／.js／.ts 超過 MAX_LINES 行即失敗，測試檔也算。

在 repo 根目錄執行：python3 scripts/check_file_lines.py
"""
import subprocess
import sys

MAX_LINES = 800
EXTS = (".py", ".js", ".mjs", ".cjs", ".ts")
SKIP = ("archive/", "macos-stt/", "desktop/vendor/")


def main() -> int:
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True, timeout=30).stdout
    bad = []
    for path in files.splitlines():
        if path.endswith(EXTS) and not path.startswith(SKIP):
            with open(path, encoding="utf-8") as f:
                n = sum(1 for _ in f)
            if n > MAX_LINES:
                bad.append((path, n))
    for path, n in bad:
        print(f"❌ {path}: {n} 行 > {MAX_LINES}")
    if bad:
        print("依職責把程式拆到既有模組；若需要新模組，先向使用者提出計畫（屬於哪一層、名稱、.importlinter 位置），確認後再拆。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
