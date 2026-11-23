"""weather_client：兩個來源的解析、選時段、固定模板、查詢順序（氣象署→Open-Meteo→查不到）。
驗收 A2、A4、A12。fixture 是真實回應（見 tests/support/weather.py）；全部離線。"""
import datetime
import json
import urllib.error

import pytest

from mac_ai import config
from mac_ai.router import weather_client as w
from support.weather import (
    CWA_TODAY, CWA_TOMORROW, KEY, METEO_TODAY, METEO_TOMORROW, NOW, load, net,  # noqa: F401
)

D = datetime.datetime


def cwa():
    return w.parse_cwa(load("cwa_taipei"), "臺北市")


def meteo():
    return w.parse_open_meteo(load("open_meteo_taipei"), "臺北市")


# ---------- 氣象署解析 ----------
def test_cwa_parses_three_periods_with_all_fields():
    f = cwa()
    assert (f.city, f.source) == ("臺北市", "氣象署")
    assert [(p.start.hour, p.wx, p.pop, p.tmin, p.tmax) for p in f.periods] == [
        (6, "晴時多雲", 20, 17, 26), (18, "多雲短暫陣雨", 80, 20, 22), (6, "陰短暫陣雨", 90, 18, 20)]
    assert f.periods[0].start == D(2026, 11, 23, 6) and f.periods[0].end == D(2026, 11, 23, 18)


def test_cwa_unknown_city_is_no_data():
    with pytest.raises(w.WeatherError) as e:
        w.parse_cwa(load("cwa_taipei"), "高雄市")
    assert e.value.kind == "no_data"


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("records"),
    lambda d: d.update(success="false"),
    lambda d: d["records"]["location"][0]["weatherElement"].pop(0),  # 沒有 Wx
    lambda d: d["records"]["location"][0]["weatherElement"][0]["time"][0].pop("startTime"),
    lambda d: d["records"]["location"][0]["weatherElement"][0]["time"].clear(),
])
def test_cwa_missing_fields_raise_only_weather_error(mutate):
    data = load("cwa_taipei")
    mutate(data)
    with pytest.raises(w.WeatherError) as e:
        w.parse_cwa(data, "臺北市")
    assert e.value.kind == "bad_format"


def test_cwa_missing_optional_elements_become_none():
    data = load("cwa_taipei")
    data["records"]["location"][0]["weatherElement"] = [e for e in data["records"]["location"][0]["weatherElement"] if e["elementName"] == "Wx"]
    p = w.parse_cwa(data, "臺北市").periods[0]
    assert (p.wx, p.pop, p.tmin, p.tmax) == ("晴時多雲", None, None, None)


def test_cwa_ignores_periods_not_starting_at_6_or_18():
    data = load("cwa_taipei")
    data["records"]["location"][0]["weatherElement"][0]["time"][0]["startTime"] = "2026-11-23 11:00:00"
    assert [p.start.hour for p in w.parse_cwa(data, "臺北市").periods] == [18, 6]


# ---------- Open-Meteo 解析（用原始資料獨立驗算，不抄程式的結果）----------
def test_open_meteo_aggregates_hours_into_12_hour_periods():
    raw = load("open_meteo_taipei")["hourly"]
    f = meteo()
    assert [(p.start, p.end) for p in f.periods[:2]] == [(D(2026, 11, 23, 6), D(2026, 11, 23, 18)), (D(2026, 11, 23, 18), D(2026, 11, 24, 6))]
    i = raw["time"].index("2026-11-23T06:00")
    day = f.periods[0]
    assert day.tmin == 18 and day.tmax == 27 and min(raw["temperature_2m"][i:i + 12]) == 18.1 and max(raw["temperature_2m"][i:i + 12]) == 27.0
    assert day.pop == max(raw["precipitation_probability"][i:i + 12]) == 0
    i = raw["time"].index("2026-11-23T18:00")
    night = f.periods[1]
    assert (night.tmin, night.tmax, night.pop) == (18, 22, 41) and max(raw["temperature_2m"][i:i + 12]) == 21.5  # 21.5 四捨五入成 22


def test_open_meteo_drops_the_last_incomplete_night():
    f = meteo()
    assert len(f.periods) == 5 and f.periods[-1].start == D(2026, 11, 25, 6)  # 3 天 72 小時：最後一晚只有 6 小時，不用


def _hourly(temps, codes=None, pops=None, days=("2026-11-23",)):
    times = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
    n = len(times)
    return {"hourly": {"time": times, "temperature_2m": temps, "weather_code": codes or [0] * n,
                       "precipitation_probability": pops or [10] * n}}


def test_open_meteo_rounds_half_up_not_bankers():
    """round(26.5)＝26、round(-0.5)＝-0：四捨五入要看到 27 與 -1"""
    hot = [20.0] * 6 + [26.5] * 12 + [20.0] * 6
    cold = [0.0] * 6 + [-0.5] * 12 + [0.0] * 6
    assert (lambda p: (p.tmin, p.tmax))(w.parse_open_meteo(_hourly(hot), "臺北市").periods[0]) == (27, 27)
    assert (lambda p: (p.tmin, p.tmax))(w.parse_open_meteo(_hourly(cold), "臺北市").periods[0]) == (-1, -1)
    assert [w._round(x) for x in (0.4, 0.5, 1.5, 2.5, -0.4, -1.5)] == [0, 1, 2, 3, 0, -2]


def test_open_meteo_hour_with_missing_value_drops_only_that_period():
    temps = [20.0] * 48
    temps[8] = None  # 第一天 08:00 沒有資料：第一天白天不用
    f = w.parse_open_meteo(_hourly(temps, days=("2026-11-23", "2026-11-24")), "臺北市")
    assert [p.start for p in f.periods] == [D(2026, 11, 23, 18), D(2026, 11, 24, 6)]


def test_open_meteo_with_no_complete_period_is_bad_format():
    data = _hourly([20.0] * 24)  # 一天只有 1 個完整的白天，把 8 點拿掉就一個都不剩
    data["hourly"]["temperature_2m"][8] = None
    with pytest.raises(w.WeatherError) as e2:
        w.parse_open_meteo(data, "臺北市")
    assert e2.value.kind == "bad_format"


def test_open_meteo_error_payload_is_bad_format():
    with pytest.raises(w.WeatherError) as e:
        w.parse_open_meteo({"error": True, "reason": "x"}, "臺北市")
    assert e.value.kind == "bad_format"


WMO_DOCUMENTED = [0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99]


def test_wmo_table_covers_every_documented_code():
    assert sorted(w.WMO_TEXT) == WMO_DOCUMENTED and all(w.WMO_TEXT.values())


def test_unknown_wmo_code_is_labelled_not_crashing():
    p = w.parse_open_meteo(_hourly([20.0] * 24, codes=[7] * 24), "臺北市").periods[0]
    assert p.wx == "天氣不明"


def test_rain_hours_decide_the_weather_only_when_there_are_enough_of_them():
    base = [0] * 12
    two_rain = base[:]; two_rain[3] = two_rain[4] = 61
    three_rain = base[:]; three_rain[3] = three_rain[4] = three_rain[5] = 61
    assert w._dominant(two_rain) == "晴天"        # 2 小時小雨：整段還是晴天
    assert w._dominant(three_rain) == "小雨"      # 3 小時：以降水天氣為準
    assert w._dominant([61, 61, 61, 63, 63, 63] + [0] * 6) == "中雨"  # 同數量取較嚴重的


# ---------- 選時段 ----------
def labels(periods, now):
    return [w._label(p, now.date()) for p in periods]


def test_select_today_returns_day_and_tonight():
    f = cwa()
    assert labels(w.select(f.periods, "today", "", NOW), NOW) == ["今天白天", "今晚"]


def test_select_today_in_the_evening_skips_the_finished_day():
    f, now = cwa(), D(2026, 11, 23, 20)
    assert labels(w.select(f.periods, "today", "", now), now) == ["今晚"]


def test_select_explicit_part_is_returned_even_if_it_is_over():
    f, now = meteo(), D(2026, 11, 23, 20)
    assert labels(w.select(f.periods, "today", "day", now), now) == ["今天白天"]


def test_select_tomorrow_and_parts():
    f = meteo()
    assert labels(w.select(f.periods, "tomorrow", "", NOW), NOW) == ["明天白天", "明晚"]
    assert labels(w.select(f.periods, "tomorrow", "night", NOW), NOW) == ["明晚"]
    assert labels(w.select(f.periods, "tomorrow", "day", NOW), NOW) == ["明天白天"]
    assert labels(w.select(f.periods, "today", "night", NOW), NOW) == ["今晚"]


def test_select_after_midnight_includes_the_night_still_going_on():
    data = load("cwa_taipei")
    for e in data["records"]["location"][0]["weatherElement"]:  # 把整份資料往前一個時段：昨晚 18:00 開始
        for t, (s, en) in zip(e["time"], [("2026-11-22 18:00:00", "2026-11-23 06:00:00"), ("2026-11-23 06:00:00", "2026-11-23 18:00:00"), ("2026-11-23 18:00:00", "2026-11-24 06:00:00")]):
            t["startTime"], t["endTime"] = s, en
    f, now = w.parse_cwa(data, "臺北市"), D(2026, 11, 23, 3)
    assert labels(w.select(f.periods, "today", "", now), now) == ["今天凌晨", "今天白天"]
    assert w.select(f.periods, "tomorrow", "", now) == []  # 氣象署這時還沒有明天的資料：交給 lookup 改查下一個來源


# ---------- 模板 ----------
def test_render_templates_are_exact():
    assert w.render("臺北市", w.select(cwa().periods, "today", "", NOW), "氣象署", NOW) == CWA_TODAY
    assert w.render("臺北市", w.select(cwa().periods, "tomorrow", "", NOW), "氣象署", NOW) == CWA_TOMORROW
    assert w.render("臺北市", w.select(meteo().periods, "today", "", NOW), "Open-Meteo", NOW) == METEO_TODAY
    assert w.render("臺北市", w.select(meteo().periods, "tomorrow", "", NOW), "Open-Meteo", NOW) == METEO_TOMORROW


@pytest.mark.parametrize("period,expected", [
    (w.Period(D(2026, 11, 23, 6), D(2026, 11, 23, 18), "晴天", 0, 25, 25), "今天白天：晴天，25 度，降雨機率 0%"),
    (w.Period(D(2026, 11, 23, 6), D(2026, 11, 23, 18), "晴天", None, 20, 25), "今天白天：晴天，20～25 度"),
    (w.Period(D(2026, 11, 23, 6), D(2026, 11, 23, 18), "晴天", 30, None, None), "今天白天：晴天，降雨機率 30%"),
    (w.Period(D(2026, 11, 23, 18), D(2026, 11, 24, 6), "陰天", 5, None, 19), "今晚：陰天，19 度，降雨機率 5%"),
])
def test_segment_handles_missing_numbers(period, expected):
    assert w._segment(period, NOW.date()) == expected


# ---------- 查詢：氣象署 → Open-Meteo → 查不到（Q14）----------
def test_with_key_only_cwa_is_called(net):
    res = w.lookup("臺北市", "today", "", NOW, KEY)
    assert res.text == CWA_TODAY and res.source == "氣象署" and res.tried == [] and net.names() == ["cwa"]


def test_without_key_only_open_meteo_is_called(net):
    res = w.lookup("臺北市", "today", "", NOW, "")
    assert res.text == METEO_TODAY and res.source == "Open-Meteo" and res.tried == [] and net.names() == ["meteo"]


@pytest.mark.parametrize("failure,kind", [
    (urllib.error.URLError(TimeoutError()), "timeout"),
    (TimeoutError(), "timeout"),
    (urllib.error.URLError("dns"), "network"),
    (500, "http_500"),
    (401, "http_401"),
    (b"not json", "bad_json"),
    ({"success": "true", "records": {"location": []}}, "no_data"),
    ({"unexpected": 1}, "bad_format"),
])
def test_cwa_failure_falls_back_to_open_meteo(net, failure, kind):
    net.set("cwa", failure)
    res = w.lookup("臺北市", "today", "", NOW, KEY)
    assert res.text == METEO_TODAY and res.source == "Open-Meteo" and res.tried == [f"cwa:{kind}"]
    assert net.names() == ["cwa", "meteo"]


def test_cwa_without_the_requested_time_falls_back_too(net):
    data = load("cwa_taipei")
    for e in data["records"]["location"][0]["weatherElement"]:
        e["time"] = e["time"][:2]  # 沒有明天白天
    net.set("cwa", data)
    res = w.lookup("臺北市", "tomorrow", "", NOW, KEY)
    assert res.text == METEO_TOMORROW and res.tried == ["cwa:no_data"]


@pytest.mark.parametrize("meteo_failure,kind", [(urllib.error.URLError(TimeoutError()), "timeout"), (503, "http_503"), (b"{", "bad_json")])
def test_both_failing_returns_no_text_and_never_raises(net, meteo_failure, kind):
    net.set("cwa", 500).set("meteo", meteo_failure)
    res = w.lookup("臺北市", "today", "", NOW, KEY)
    assert res.text is None and res.source == "" and res.tried == ["cwa:http_500", f"open_meteo:{kind}"]


def test_no_key_and_open_meteo_down_means_not_found_without_calling_cwa(net):
    net.set("meteo", urllib.error.URLError(TimeoutError()))
    res = w.lookup("臺北市", "today", "", NOW, "")
    assert res.text is None and res.tried == ["open_meteo:timeout"] and net.names() == ["meteo"]


def test_unexpected_exception_is_classified_not_propagated(net):
    net.set("cwa", RuntimeError("boom"))
    assert w.lookup("臺北市", "today", "", NOW, KEY).tried == ["cwa:bad_format"]


def test_requests_carry_key_in_header_only_and_use_the_configured_timeout(net):
    net.set("cwa", 500).set("meteo", 500)
    w.lookup("臺北市", "today", "", NOW, KEY)
    cwa_call, meteo_call = net.calls
    assert cwa_call["headers"]["authorization"] == KEY and KEY not in cwa_call["url"]
    assert "locationName=%E8%87%BA%E5%8C%97%E5%B8%82" in cwa_call["url"] and cwa_call["url"].startswith(config.CWA_FORECAST_URL)
    assert "authorization" not in meteo_call["headers"] and KEY not in meteo_call["url"]
    assert "latitude=25.0375" in meteo_call["url"] and "longitude=121.5637" in meteo_call["url"] and "timezone=Asia%2FTaipei" in meteo_call["url"]
    assert cwa_call["timeout"] == meteo_call["timeout"] == config.WEATHER_TIMEOUT_S


def test_nothing_returned_contains_the_key_even_when_it_fails(net):
    net.set("cwa", urllib.error.HTTPError(f"https://x/?Authorization={KEY}", 401, "bad", {}, None)).set("meteo", RuntimeError(KEY))
    res = w.lookup("臺北市", "today", "", NOW, KEY)
    assert KEY not in json.dumps([res.text, res.source, res.tried])
