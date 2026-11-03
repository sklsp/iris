"""The weather from open-meteo.com: geocode a place, then read now and up to seven days."""

import json

import iris.weather as w
from iris import tools

GEO = {"results": [{"name": "Rotterdam", "latitude": 51.9, "longitude": 4.48}]}
FORECAST = {
    "current": {"temperature_2m": 9.1, "apparent_temperature": 6.0, "precipitation": 0.2,
                "weather_code": 61, "wind_speed_10m": 25.3},
    "daily": {
        "time": ["2026-10-31", "2026-11-01"],
        "temperature_2m_min": [-0.4, -1.2],
        "temperature_2m_max": [7.8, 5.9],
        "precipitation_probability_max": [None, 40.0],
        "weather_code": [61, 3],
    },
}


def _fake_http_get(monkeypatch):
    """http_get that answers the two open-meteo URLs with canned JSON and records what was asked."""
    seen = []

    def fake(url, timeout=15, max_bytes=2_000_000):
        seen.append(url)
        if "geocoding-api" in url:
            return url, "application/json", json.dumps(GEO)
        return url, "application/json", json.dumps(FORECAST)

    monkeypatch.setattr(w.web, "http_get", fake)
    return seen


def test_now_and_days_are_one_line_each(monkeypatch):
    _fake_http_get(monkeypatch)
    out = w.weather("Rotterdam", days=2)
    assert out == (
        "Rotterdam now: 9 C (feels 6 C), rain, wind 25 km/h\n"
        "31 Oct: 0 to 8 C, rain\n"
        "1 Nov: -1 to 6 C, partly cloudy, 40% precipitation")


def test_the_urls_ask_for_now_and_seven_days(monkeypatch):
    seen = _fake_http_get(monkeypatch)
    w.weather("Rotterdam", days=7)
    assert seen[0] == "https://geocoding-api.open-meteo.com/v1/search?name=Rotterdam&count=1"
    url = seen[1]
    assert url.startswith("https://api.open-meteo.com/v1/forecast?latitude=51.9&longitude=4.48")
    for part in ("current=temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                 "daily=temperature_2m_min,temperature_2m_max,precipitation_probability_max,weather_code",
                 "timezone=auto", "forecast_days=7"):
        assert part in url


def test_place_is_quoted_in_the_geocode_url(monkeypatch):
    seen = _fake_http_get(monkeypatch)
    w.weather("New York")
    assert "name=New%20York" in seen[0]


def test_no_place_and_no_configured_city_asks_for_one(tmp_path, monkeypatch):
    def fake(url, timeout=15, max_bytes=2_000_000):
        raise AssertionError("no network call may be made without a place")

    monkeypatch.setattr(w.web, "http_get", fake)
    assert w.weather() == "error: say which place, or set city in config.toml"


def test_configured_city_is_used_when_place_is_empty(tmp_path, monkeypatch):
    (tmp_path / "iris-config.toml").write_text('city = "Rotterdam"\n', encoding="utf-8")
    _fake_http_get(monkeypatch)
    assert w.weather().startswith("Rotterdam now: ")


def test_unknown_place_is_an_error(monkeypatch):
    def fake(url, timeout=15, max_bytes=2_000_000):
        if "geocoding-api" in url:
            return url, "application/json", "{}"  # no results key
        raise AssertionError("the forecast must not be asked for an unknown place")

    monkeypatch.setattr(w.web, "http_get", fake)
    assert w.weather("Xyzzyville") == "error: no place called 'Xyzzyville'"


def test_days_out_of_range_is_an_error(monkeypatch):
    _fake_http_get(monkeypatch)
    assert w.weather("Rotterdam", days=0) == "error: days must be between 1 and 7"
    assert w.weather("Rotterdam", days=8) == "error: days must be between 1 and 7"


def test_a_network_failure_is_an_error(monkeypatch):
    def fake(url, timeout=15, max_bytes=2_000_000):
        raise OSError("connection refused")

    monkeypatch.setattr(w.web, "http_get", fake)
    assert w.weather("Rotterdam") == "error: could not reach the weather service"


def test_a_forecast_key_error_is_an_error(monkeypatch):
    def fake(url, timeout=15, max_bytes=2_000_000):
        if "geocoding-api" in url:
            return url, "application/json", json.dumps(GEO)
        raise AssertionError("the forecast is faked; no network call may be made")

    monkeypatch.setattr(w.web, "http_get", fake)

    def broken(lat, lon, days):
        raise KeyError("current")

    monkeypatch.setattr(w, "_forecast", broken)
    assert w.weather("Rotterdam") == "error: could not read the weather"


def test_a_forecast_os_error_is_an_error(monkeypatch):
    def fake(url, timeout=15, max_bytes=2_000_000):
        if "geocoding-api" in url:
            return url, "application/json", json.dumps(GEO)
        raise AssertionError("the forecast is faked; no network call may be made")

    monkeypatch.setattr(w.web, "http_get", fake)

    def broken(lat, lon, days):
        raise OSError("connection reset")

    monkeypatch.setattr(w, "_forecast", broken)
    assert w.weather("Rotterdam") == "error: could not read the weather"


def test_weather_is_safe_and_does_not_escalate():
    tool = tools.TOOLS["weather"]
    assert tool.tier == "safe"
    assert tool.tier_for is None
