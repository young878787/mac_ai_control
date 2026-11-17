"""假 LLM 的共用工具（取代 llm_client._chat）：組 tool_call、依情境回覆的 FakeLLM、禁止呼叫／固定回覆的捷徑。"""
import json

import pytest

from mac_ai.router import llm_client

DEFAULT_REPLY = {"content": "好的"}


def tc(name, args=None):
    """一個 tool_call。args 給 dict 會轉成 JSON 字串；給字串則原樣放入（測畸形參數時用）"""
    if args is None:
        args = {}
    return {"type": "function", "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}


def message(value, messages=None):
    """把回覆來源轉成 LLM message：dict 原樣、tool_call 清單 → {"tool_calls"}、字串 → {"content"}、callable(messages) 先呼叫"""
    if callable(value):
        value = value(messages)
    if isinstance(value, list):
        return {"tool_calls": value}
    if isinstance(value, str):
        return {"content": value}
    return value


class FakeLLM:
    """假的 llm_client._chat（用 install(monkeypatch) 裝上）。回覆來源，依優先順序：
      error=True           拋 LLMUnavailable
      by_text={句: 回覆}    依最後一則 user 訊息查表
      plan= 或 chat=        帶 tools 的規劃請求回 plan，不帶 tools 的 chat 請求回 chat（沒設的一邊回 DEFAULT_REPLY）
      FakeLLM(r1, r2, ...) 依序回，用完回 DEFAULT_REPLY
    回覆可以是 message dict、tool_call 清單、字串、或 callable(messages)（見 message()）。plan／chat／replies 可以在測試裡改。
    記錄：calls（每次的 messages）、kws（對應的 kwargs）、plans／chats（依 tools 旗標分開的 messages）、texts（每次最後一則 user 訊息）"""

    def __init__(self, *replies, plan=None, chat=None, by_text=None, error=False):
        self.replies, self.plan, self.chat, self.by_text, self.error = list(replies), plan, chat, by_text, error
        self.calls, self.kws, self.plans, self.chats = [], [], [], []

    @property
    def texts(self):
        """每次呼叫最後一則（user）訊息的內容"""
        return [m[-1]["content"] for m in self.calls]

    def install(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_chat", self)
        return self

    def __call__(self, messages, **kw):
        self.calls.append(messages)
        self.kws.append(kw)
        (self.plans if kw.get("tools", True) else self.chats).append(messages)
        if self.error:
            raise llm_client.LLMUnavailable("連不上")
        if self.by_text is not None:
            return message(self.by_text[messages[-1]["content"]], messages)
        if self.plan is not None or self.chat is not None:
            source = self.plan if kw.get("tools", True) else self.chat
            return message(DEFAULT_REPLY if source is None else source, messages)
        return message(self.replies.pop(0) if self.replies else DEFAULT_REPLY, messages)


def forbid_llm(monkeypatch, why="不應呼叫 LLM"):
    """任何 LLM 呼叫都讓測試失敗（Fast Path、守門、本機搜尋等不該問模型的路徑）"""
    monkeypatch.setattr(llm_client, "_chat", lambda *a, **k: pytest.fail(why))


def llm_always(monkeypatch, value):
    """每次呼叫都回同一個 message（value 規則同 message()）"""
    monkeypatch.setattr(llm_client, "_chat", lambda messages, **kw: message(value, messages))
