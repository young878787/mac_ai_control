"""由 vocab/lexicon.json 產生 STT contextualStrings 詞表與 Jieba 自訂詞典。"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
LEXICON = ROOT / "vocab" / "lexicon.json"


def _text(entry) -> str:
    """misheard 可以是字串（舊格式）或 {"text", "source"}"""
    return entry["text"] if isinstance(entry, dict) else entry


def build(lexicon_path: Path = LEXICON, out_dir: Path = LEXICON.parent) -> None:
    lex = json.loads(lexicon_path.read_text(encoding="utf-8"))

    # STT 提示詞：App 與專案的標準名稱 + 別名 + 指令詞（不含 misheard／s2t，避免強化錯誤寫法）
    hints = []
    for app in lex["apps"]:
        hints += [app["name"], *app["aliases"]]
    for project in lex.get("projects", []):
        hints += [project["name"], *project.get("aliases", [])]
    hints += lex.get("terms", [])
    hints += lex.get("wake_words", {}).get("names", [])
    hints = list(dict.fromkeys(h.strip() for h in hints if h.strip()))
    (out_dir / "stt_hints.txt").write_text("\n".join(hints) + "\n", encoding="utf-8")

    # Jieba userdict：每行「詞 詞頻 詞性」，含空白的詞無法表示，略過
    words = list(hints)
    for app in lex["apps"]:
        words += [_text(m) for m in app.get("misheard", [])]
    words = [w for w in dict.fromkeys(words) if not any(c.isspace() for c in w)]
    lines = [f"{w} 100000 n" for w in words]
    (out_dir / "userdict.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
    print("已產生 vocab/stt_hints.txt、vocab/userdict.txt")
