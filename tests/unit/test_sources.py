"""待決來源（「要打開來源嗎？」）：取出就清掉、過期作廢、被新的取代；肯定回答只收整句的那幾種說法。"""
import pytest

from mac_ai import config
from mac_ai.router import sources


@pytest.fixture
def store(clock):
    return sources.SourceStore(clock=clock, ttl_s=60.0)


def test_take_returns_once_then_nothing(store):
    store.put("https://a.example/")
    assert store.take() == "https://a.example/"
    assert store.take() is None


def test_take_on_empty_store_is_none(store):
    assert store.take() is None


def test_expired_pending_is_dropped(store, clock):
    store.put("https://a.example/")
    clock.now += 60.0
    assert store.take() is None


def test_just_before_expiry_still_valid(store, clock):
    store.put("https://a.example/")
    clock.now += 59.9
    assert store.take() == "https://a.example/"


def test_new_answer_replaces_old_and_restarts_ttl(store, clock):
    store.put("https://a.example/")
    clock.now += 50
    store.put("https://b.example/")
    clock.now += 50  # 距第一次 100 秒、距第二次 50 秒
    assert store.take() == "https://b.example/"


def test_clear(store):
    store.put("https://a.example/")
    store.clear()
    assert store.take() is None


def test_default_ttl_follows_config():
    assert sources.SourceStore()._ttl == config.SOURCE_FOLLOWUP_TTL_S == 60.0


@pytest.mark.parametrize("text", ["好", "好的", "好啊", "好喔", "要", "要啊", "可以", "打開", "打開來源", "開啟來源", "幫我打開來源", "要打開來源", "開來源", "好啦", "打開一下"])
def test_yes_phrases(text):
    assert sources.is_yes(text)


@pytest.mark.parametrize("text", [
    "", "不用", "不要", "不好", "好吵", "好熱", "好，順便調音量", "打開小算盤", "打開 Safari", "打開 YouTube", "打開音樂", "開啟深色模式",
    "要不要", "我要聽歌", "可以幫我開小算盤嗎", "好像是", "要打開 Chrome", "打開來源網站的設定",
])
def test_non_yes_phrases(text):
    assert not sources.is_yes(text)
