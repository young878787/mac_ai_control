"""查詢分類 classify_query（計畫 docs/plans/active/2026-11-23-query-routing.md §4.1、§7.1 A2／A3）。
P2：分類與既有規則相同，這裡鎖住每種結果的 target／mode／evidence；整條路由的行為由 test_routing_snapshot 鎖住"""
import json
import sqlite3
import subprocess
import urllib.request
from pathlib import Path

import pytest

from mac_ai.router import catalog
from mac_ai.router.fast_path import canonicalize_clause
from mac_ai.router.query_intent import classify_query

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "routing" / "corpus.json"


def classify(text, stage="lead"):
    raw = text.strip()
    return classify_query(canonicalize_clause(raw), catalog.DEFAULT, raw=raw, stage=stage)


@pytest.mark.parametrize("text, target, mode, evidence, extra", [
    ("查一下台北天氣", "knowledge", "answer", "category", {"category": "weather", "slots": {"city": "臺北市", "day": "today", "part": ""}}),
    ("明天會下雨嗎", "knowledge", "answer", "category", {"category": "weather"}),
    ("搜尋履歷", "knowledge", "show", "verb", {"slots": {"where": "context"}, "query": "履歷"}),
    ("查一下周杰倫是誰", "knowledge", "show", "verb", {"slots": {"where": "context"}, "query": "周杰倫是誰"}),
    ("上網查周杰倫", "knowledge", "show", "explicit_web", {"slots": {"where": "google"}}),
    ("google 一下周杰倫", "knowledge", "show", "explicit_web", {"slots": {"where": "google"}}),
    ("查台積電股價", "knowledge", "show", "realtime", {"slots": {"where": "google"}}),
    ("打開瀏覽器查台北天氣", "knowledge", "show", "explicit_web", {"slots": {"where": "google"}}),
    ("打開 Safari 搜尋貓咪", "knowledge", "show", "explicit_web", {"slots": {"where": "google", "app": "Safari"}}),
    ("搜尋 report.pdf", "local", "show", "file_hint", {}),
    ("在電腦裡搜尋履歷", "local", "show", "scope_word", {}),
])
def test_lead_stage_labels(text, target, mode, evidence, extra):
    q = classify(text)
    assert (q.target, q.mode, q.evidence) == (target, mode, evidence)
    for k, v in extra.items():
        assert getattr(q, k) == v


@pytest.mark.parametrize("text, target, domain", [
    ("找一下我的履歷", "local", "file"),
    ("找昨天下載的 PDF", "local", "file"),
    ("找一下周杰倫是誰", "local", "file"),  # 現況：當成檔名（U3 在 P3 改成直接回答）
    ("上網找履歷範本", "knowledge", "web"),
])
def test_find_stage_labels(text, target, domain):
    q = classify(text, stage="find")
    assert (q.target, q.mode, q.intent.domain) == (target, "show", domain)


@pytest.mark.parametrize("text, stage", [
    ("打開小算盤", "lead"), ("音量調到 30", "lead"), ("周杰倫是誰", "lead"), ("台積電股價", "lead"),
    ("搜尋嗎", "lead"), ("google 周杰倫", "lead"),  # 現況：google 後面要接「一下」或搜尋動詞
    ("找一下周杰倫", "lead"),  # 「找」不在 lead 階段判斷
    ("搜尋履歷", "find"), ("打開小算盤", "find"),
])
def test_not_a_query_at_this_stage(text, stage):
    assert classify(text, stage) is None


def test_classify_query_is_pure(monkeypatch):
    """A3：整份語料（兩個階段）跑完都不碰子程序、網路、資料庫"""
    def boom(*a, **kw):
        raise AssertionError("classify_query 不得做 IO")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(sqlite3, "connect", boom)
    sentences = [s for g in json.loads(CORPUS.read_text())["groups"].values() for s in g]
    for text in sentences:
        classify(text)
        classify(text, stage="find")
