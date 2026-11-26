"""llm_client／_chat 的請求內容、timings、逾時預算，以及洩漏思考內容的清理"""
import io
import json
import urllib.error
import urllib.request

import pytest

import mac_ai.router as r
from mac_ai import config
from mac_ai.router import llm_client


# ---------- S3：_chat 的 timings 與 tools 開關 ----------
def _fake_urlopen(captured, payload):
    def fake(req, timeout):
        captured.update(json.loads(req.data))
        captured["timeout"] = timeout
        return io.BytesIO(json.dumps(payload).encode())
    return fake


def test_chat_fills_meta_from_server_timings(monkeypatch):
    captured, meta = {}, {}
    payload = {"choices": [{"message": {"content": "ok"}}],
               "timings": {"prompt_n": 600, "cache_n": 500, "predicted_n": 20, "prompt_ms": 120.5, "predicted_ms": 480.0}}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(captured, payload))
    assert llm_client._chat([{"role": "user", "content": "hi"}], meta=meta) == {"content": "ok"}
    assert meta == {"prefill_ms": 120.5, "decode_ms": 480.0, "prompt_n": 600, "cache_n": 500, "predicted_n": 20}


def test_chat_without_tools_omits_them_and_sets_max_tokens(monkeypatch):
    captured = {}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(captured, {"choices": [{"message": {"content": "ok"}}]}))
    llm_client._chat([{"role": "user", "content": "hi"}], tools=False, max_tokens=128, timeout=7)
    assert "tools" not in captured and captured["max_tokens"] == 128 and captured["timeout"] == 7
    assert captured["enable_thinking"] is False


def test_default_plan_timeout_is_10s(monkeypatch):
    captured = {}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(captured, {"choices": [{"message": {}}]}))
    llm_client._llm([{"role": "user", "content": "x"}], timeout=config.PLAN_TIMEOUT_S)
    assert captured["timeout"] == 10.0


def test_chat_request_body(monkeypatch):
    """請求必須帶 tools、關閉 thinking、使用官方建議取樣參數"""
    import io
    import json
    import urllib.request
    captured = {}

    def fake_urlopen(req, timeout):
        captured.update(json.loads(req.data))
        captured["url"] = req.full_url
        return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm_client._chat([{"role": "user", "content": "hi"}]) == {"content": "ok"}
    assert captured["url"].endswith("/chat/completions")
    assert captured["enable_thinking"] is False
    assert len(captured["tools"]) == 8
    assert (captured["temperature"], captured["top_k"], captured["repetition_penalty"]) == (0.4, 50, 1.1)


@pytest.mark.parametrize("raw,expected", [
    ("思考過程</think>答案", "答案"),  # 實測：沒有開頭標籤也會洩漏
    ("<think>想一想</think>答案", "答案"),
    ("a</think>b</think>答案", "答案"),
    ("答案", "答案"),
    ("<think>沒有結束就被截斷", ""),
    ("只有思考</think>", ""),
])
def test_strip_think(raw, expected):
    from mac_ai.router.actions import _strip_think
    assert _strip_think(raw) == expected


@pytest.mark.parametrize("message", [None, "text", ["x"]])
def test_non_object_message_is_llm_error(monkeypatch, sh, message):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode()))
    out = r.process_voice_command("幫我整理一下桌面")
    assert out["steps"][0]["status"] == "llm_error" and sh == []
