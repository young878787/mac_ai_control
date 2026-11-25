"""天氣冷啟動的待答（計畫 2026-11-24-weather-city-and-answer-timeout.md §3.2、A6）：取出就清掉、過期作廢、被新的取代；只放記憶體。"""
import pytest

from mac_ai import config
from mac_ai.router import weather_followup


@pytest.fixture
def store(clock):
    return weather_followup.FollowupStore(clock=clock, ttl_s=60.0)


def test_take_returns_once_then_nothing(store):
    store.put("tomorrow", "night")
    assert store.take() == ("tomorrow", "night") and store.take() is None


def test_expired_pending_is_dropped(store, clock):
    store.put("tomorrow", "")
    clock.now += 60.0
    assert store.take() is None


def test_just_before_expiry_still_valid(store, clock):
    store.put("tomorrow", "")
    clock.now += 59.9
    assert store.take() == ("tomorrow", "")


def test_new_cold_start_replaces_the_old_one(store):
    store.put("today", "")
    store.put("tomorrow", "day")
    assert store.take() == ("tomorrow", "day")


def test_clear(store):
    store.put("today", "")
    store.clear()
    assert store.take() is None


def test_ttl_matches_the_wake_session():
    assert config.WEATHER_CITY_FOLLOWUP_TTL_S == config.SOURCE_FOLLOWUP_TTL_S == 60.0
