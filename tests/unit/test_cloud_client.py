"""cloud_client：.env 讀取與權限、回應快取開關、洩漏思考內容與簡體字的清理（假雲端）"""
import os

import pytest

import mac_ai.router as r
from mac_ai import config
from mac_ai.router import cloud_client
from mac_ai.stt import completeness


# ---------- .env ----------
def test_parse_env_formats():
    text = "\n# 註解\nAI_API_KEY=abc\nexport AI_MODEL=\"X Y\"\nAI_API_URL='https://u'\n壞掉的一行\nNO EQUALS\nEMPTY=\n=novalue\n"
    assert config.parse_env(text) == {"AI_API_KEY": "abc", "AI_MODEL": "X Y", "AI_API_URL": "https://u", "EMPTY": ""}


def test_env_var_beats_file_and_missing_is_empty(monkeypatch, tmp_path):
    f = tmp_path / "a.env"
    f.write_text("AI_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", f)
    config.reset_env_cache()
    assert config.env_value("AI_MODEL") == "from-file" and config.env_value("NOPE") == ""
    monkeypatch.setenv("AI_MODEL", "from-env")
    assert config.env_value("AI_MODEL") == "from-env"
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / "missing.env")
    config.reset_env_cache()
    monkeypatch.delenv("AI_MODEL")
    assert config.env_value("AI_MODEL") == ""


def test_example_env_copied_as_is_keeps_cloud_disabled(monkeypatch, tmp_path):
    f = tmp_path / "copied.env"
    f.write_bytes((config.ROOT / ".env.example").read_bytes())
    f.chmod(0o600)
    monkeypatch.setattr(config, "ENV_PATH", f)
    config.reset_env_cache()
    assert config.env_value("AI_API_URL") == "https://chatapi.ntubimdbirc.tw" and config.env_value("AI_MODEL") == "BIRC-LLM"
    assert config.env_value("AI_API_KEY") == ""
    assert cloud_client.settings() is None and not cloud_client.available()
    assert "未設定" in cloud_client.status_line()


@pytest.mark.parametrize("line", ["AI_API_KEY=", "AI_API_KEY=   "])
def test_blank_key_disables_cloud(cloud, monkeypatch, tmp_path, line):
    f = tmp_path / "blank.env"
    f.write_text(f"AI_API_URL=https://cloud.test\nAI_MODEL=M\n{line}\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", f)
    config.reset_env_cache()
    assert cloud_client.settings() is None


def test_http_url_disables_cloud_and_warns_once(cloud, monkeypatch, tmp_path, capsys):
    f = tmp_path / "http.env"
    f.write_text(f"AI_API_URL=http://cloud.test\nAI_MODEL=M\nAI_API_KEY={KEY}\n", encoding="utf-8")
    f.chmod(0o600)
    monkeypatch.setattr(config, "ENV_PATH", f)
    config.reset_env_cache()
    assert cloud_client.settings() is None and cloud_client.settings() is None
    out = capsys.readouterr().out
    assert out.count("不是 https") == 1 and KEY not in out


def test_loose_env_permission_warns_once(cloud, capsys):
    config.ENV_PATH.chmod(0o644)
    assert cloud_client.settings() is not None and cloud_client.settings() is not None
    assert capsys.readouterr().out.count("chmod 600") == 1


def test_strict_env_permission_does_not_warn(cloud, capsys):
    assert cloud_client.settings() is not None
    assert "chmod" not in capsys.readouterr().out


def test_cloud_enabled_false_means_local_only(cloud, monkeypatch):
    monkeypatch.setattr(config, "CLOUD_ENABLED", False)
    assert cloud_client.settings() is None and "已關閉" in cloud_client.status_line()


def test_key_never_enters_os_environ_or_child_process_env(cloud):
    from mac_ai.services import llm_service, ui_service
    assert cloud_client.settings() is not None
    assert "AI_API_KEY" not in os.environ
    for svc in (llm_service(), ui_service("ws://x", "http://x/health")):
        assert KEY not in "".join((svc.env or {}).values()) and "AI_API_KEY" not in (svc.env or {})


# ---------- 回應快取開關 ----------
def test_no_cache_flag_adds_cache_control_to_the_body_only_when_on(cloud, monkeypatch):
    r.process_voice_command("今天心情不錯")
    assert "cache" not in cloud.requests[-1]                      # 預設：維持 proxy 快取
    monkeypatch.setattr(config, "CLOUD_NO_CACHE", True)
    r.process_voice_command("今天心情不佳")
    assert cloud.requests[-1]["cache"] == {"no-cache": True}


# ---------- 洩漏的思考內容、重複段落、殘留簡體的量測定義 ----------
LEAK = ("```thinking\nThe user is asking how to write a Python for loop. I need to reply within 60 characters.\n"
        "Let me count: \"for i in range(5): 縮排\"\n```\n\nfor i in range(5): 接著縮排寫迴圈內容喔～\n\n\nfor i in range(5): 接著縮排寫迴圈內容喔～")


def test_clean_text_strips_thinking_fence_and_duplicate_paragraphs():
    assert cloud_client.clean_text(LEAK) == "for i in range(5): 接著縮排寫迴圈內容喔～"
    assert cloud_client.clean_text("```thinking\n推理到一半沒有結尾") == ""           # 沒結尾的整段丟掉
    assert cloud_client.clean_text("```python\nprint(1)\n```") == "```python\nprint(1)\n```"   # 一般程式碼區塊不動
    assert cloud_client.clean_text("好的～\n\n好的～\n\n再見") == "好的～\n\n再見"          # 只合併連續重複
    assert cloud_client.clean_text("A\n\nB\n\nA") == "A\n\nB\n\nA"


def test_chat_reply_text_with_leaked_thinking_is_cleaned_end_to_end(cloud):
    cloud.reply = {"content": LEAK}
    assert r.process_voice_command("教我寫一個 for 迴圈")["replies"] == ["for i in range(5): 接著縮排寫迴圈內容喔～"]


def test_residual_simplified_ignores_chars_without_a_traditional_form():
    assert hant.residual_simplified("好嘞，喇叭已經靜音囉") == []            # 『嘞』是方言字，ICU 沒有繁體對應
    assert sorted(hant.residual_simplified("远离螢幕")) == sorted(["远", "离"])
    assert hant.residual_simplified(hant.hans_to_hant("远离螢幕")) == []


def test_cloud_prompt_pins_length_single_line_and_traditional_rules():
    """這幾條是 2026-11-16 A/B 實測出來的（見 cloud_client 註解）：改動前後要重跑 `pytest -m cloud -s -k reply_quality`"""
    p = cloud_client.CLOUD_SYSTEM_PROMPT
    assert "只有一行" in p and "不超過 50 個字" in p and "不可夾雜任何簡體字" in p and "不貼程式碼" in p
    assert "不可夾雜任何簡體字" in cloud_client.CLOUD_VISION_SYSTEM_PROMPT   # 看圖的回覆也要純繁體
    assert "最多五句話" in cloud_client.CLOUD_VISION_SYSTEM_PROMPT          # 看圖的長度規則不動（沒有 A/B 過）


# ---------- 簡體字轉換（hant）----------
from mac_ai.router import hant  # noqa: E402
from support.cloud import KEY

LEAKED = "欢别献则创历医远协沟长办专动认简内暂"  # 2026-11-16 實測雲端回覆裡殘留的簡體字
LEAKED_FIXED = "歡別獻則創歷醫遠協溝長辦專動認簡內暫"


def test_hant_converts_every_simplified_char_seen_in_cloud_replies():
    assert hant.hans_to_hant(LEAKED) == LEAKED_FIXED
    assert hant.hans_to_hant("睡前一小時远离螢幕，体验气氛网络") == "睡前一小時遠離螢幕，體驗氣氛網絡"  # 離／體／氣／網 是 Big5 編得出來的簡體字
    assert not hant.has_simplified(hant.hans_to_hant(LEAKED + "离体气网"))


def test_hant_keeps_traditional_text_and_ambiguous_chars_untouched():
    same = ["退回路徑 皇后 只有 乾杯 迴圈 回覆 一隻貓", "于右任 云林 干貝 北斗 茶几 風采 范仲淹 征服 沉默 恒春", "臺灣 裡面 麵包 頭髮 Docker 是 container；VM → 虛擬機，OK！", ""]
    for text in same:
        assert hant.hans_to_hant(text) == text and not hant.has_simplified(text)


def test_hant_only_changes_simplified_positions():
    text = "退回路徑，睡前远离螢幕。皇后说：乾杯！"
    out = hant.hans_to_hant(text)
    assert out == "退回路徑，睡前遠離螢幕。皇后說：乾杯！"
    assert all(a == b for a, b in zip(text, out) if not hant._simplified(a))


def test_hant_curated_list_is_all_convertible_and_not_ambiguous():
    for ch in hant._SIMPLIFIED_IN_BIG5:
        out = hant._icu(ch)
        assert out and out != ch and len(out) == 1, ch
    assert not set("后干于云斗几采范征沉恒占复并舍丑") & hant._SIMPLIFIED_IN_BIG5  # 繁體也在用的字不能放進清單


def test_hant_fails_safe(monkeypatch):
    monkeypatch.setattr(hant, "_icu", lambda t: None)
    assert hant.hans_to_hant("远离") == "远离"                  # ICU 失敗
    monkeypatch.setattr(hant, "_icu", lambda t: t + "多一個字")
    assert hant.hans_to_hant("远离") == "远离"                  # 長度不符
    monkeypatch.setattr(hant, "_icu", lambda t: (_ for _ in ()).throw(OSError("boom")))
    assert hant.hans_to_hant("远离") == "远离"                  # 例外
    assert hant.hans_to_hant("") == ""


def test_hant_missing_library_returns_input(monkeypatch):
    monkeypatch.setattr(hant, "_lib", None)
    monkeypatch.setattr(hant, "_load_failed", False)
    monkeypatch.setattr(hant.ctypes, "CDLL", lambda *a: (_ for _ in ()).throw(OSError("no lib")))
    assert hant.hans_to_hant("远离") == "远离" and hant._load_failed is True


def test_hant_skips_icu_without_simplified_chars(monkeypatch):
    calls = []
    monkeypatch.setattr(hant, "_icu", lambda t: calls.append(t) or None)
    hant.hans_to_hant("全部都是繁體字與 English，沒有簡體")
    assert calls == []  # 沒有簡體字就不呼叫 ICU


def test_hant_is_fast():
    import time
    text = "Docker 是容器化技術；VM 则是整套虛擬一台電腦，睡前一小時远离螢幕喔～" * 3  # 約 100 字
    t = time.perf_counter()
    for _ in range(1000):
        hant.hans_to_hant(text)
    assert (time.perf_counter() - t) / 1000 < 0.001  # 實測約 0.1 ms


def test_clean_text_uses_both_dictionary_and_icu():
    out = cloud_client.clean_text("\n## 標題\n**重點**：今天适合学习，睡前远离螢幕，体验很好\n</thinking>")
    assert out == "標題\n重點：今天適合學習，睡前遠離螢幕，體驗很好" and not hant.has_simplified(out)


def test_hot_paths_never_call_hans_to_hant(monkeypatch):
    calls = []
    monkeypatch.setattr(hant, "hans_to_hant", lambda t: calls.append(t) or t)
    monkeypatch.setattr(cloud_client, "hans_to_hant", hant.hans_to_hant)
    from support.typo_corpus import CASES
    for _, text, _, _ in CASES:
        r.normalize_utterance(text)
        completeness.classify(text)
    assert calls == []


def test_docs_are_not_corrupted_by_conversion():
    """全部繁體文件逐行轉換：被改的行都含確定是簡體的字（文件裡本來就在舉例簡體字的那幾行），沒有別的行被動到"""
    lines = [l for f in ("README.md", "AGENTS.md") for l in (config.ROOT / f).read_text(encoding="utf-8").splitlines()]
    lines += [l for f in (config.ROOT / "docs").glob("*.md") for l in f.read_text(encoding="utf-8").splitlines()]
    changed = [l for l in lines if hant.hans_to_hant(l) != l]
    assert len(lines) > 1000 and all(hant.has_simplified(l) for l in changed)
    assert len(changed) <= 10, len(changed)  # 2026-11-16 實測 1672 行中 4 行（說明簡體字的例子）
