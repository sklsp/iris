"""The weather from open-meteo.com: geocode a place, then read now and up to seven days.

Stdlib only; every network call goes through iris.web.http_get, which already refuses local addresses.
"""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote

from iris import config, web

_CODES = {0: "clear", 1: "clear", 2: "partly cloudy", 3: "partly cloudy",
          45: "fog", 48: "fog",
          51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle",
          61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
          71: "snow", 73: "snow", 75: "snow", 77: "snow",
          80: "showers", 81: "showers", 82: "showers", 85: "showers", 86: "showers",
          95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm"}


def _word(code: int) -> str:
    """A short English word for a WMO weather code; 'unknown' when the code is not one we know."""
    return _CODES.get(int(code), "unknown")


def _geocode(where: str) -> tuple[str, float, float] | None:
    """The name and coordinates of the first geocoding hit for `where`; None when there is no such place.

    Raises OSError on a network failure."""
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={quote(where)}&count=1"
    try:
        _, _, text = web.http_get(url)
        hits = json.loads(text).get("results") or []
        hit = hits[0]
        return str(hit["name"]), float(hit["latitude"]), float(hit["longitude"])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None


def _forecast_url(lat: float, lon: float, days: int) -> str:
    current = "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
    daily = "temperature_2m_min,temperature_2m_max,precipitation_probability_max,weather_code"
    return (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            f"&current={current}&daily={daily}&timezone=auto&forecast_days={days}")


def _forecast(lat: float, lon: float, days: int) -> tuple[dict, dict]:
    """The current and daily blocks of the forecast for these coordinates."""
    _, _, text = web.http_get(_forecast_url(lat, lon, days))
    data = json.loads(text)
    return data["current"], data["daily"]


def _now_line(name: str, cur: dict) -> str:
    """The now line, e.g. 'Rotterdam now: 9 C (feels 6 C), rain, wind 25 km/h'."""
    return (f"{name} now: {round(cur['temperature_2m'])} C "
            f"(feels {round(cur['apparent_temperature'])} C), {_word(cur['weather_code'])}, "
            f"wind {round(cur['wind_speed_10m'])} km/h")


def _day_line(daily: dict, i: int) -> str:
    """One forecast day, e.g. '31 Oct: -1 to 7 C, rain, 40% precipitation'."""
    day = datetime.strptime(daily["time"][i], "%Y-%m-%d")
    line = (f"{day.day} {day:%b}: {round(daily['temperature_2m_min'][i])} "
            f"to {round(daily['temperature_2m_max'][i])} C, {_word(daily['weather_code'][i])}")
    prob = daily["precipitation_probability_max"][i]
    if prob is not None:
        line += f", {round(prob)}% precipitation"
    return line


def weather(place: str = "", days: int = 1) -> str:
    """The current weather and up to seven days for `place`, or the city from config.toml when place is empty."""
    where = place.strip() or config.get("city")
    if not where:
        return "error: say which place, or set city in config.toml"
    if not 1 <= days <= 7:
        return "error: days must be between 1 and 7"
    try:
        geo = _geocode(where)
    except OSError:
        return "error: could not reach the weather service"
    if geo is None:
        return f"error: no place called {where!r}"
    name, lat, lon = geo
    try:
        cur, daily = _forecast(lat, lon, days)
        lines = [_now_line(name, cur)] + [_day_line(daily, i) for i in range(len(daily["time"]))]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return "error: could not read the weather"
    return "\n".join(lines)
