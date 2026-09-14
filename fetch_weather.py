#!/usr/bin/env python3
"""
Fetch a forecast for the belt holder's next scheduled game -- kickoff-hour
temperature, feels-like, wind, precipitation chance, and sky condition at
the actual venue -- from Open-Meteo (https://open-meteo.com/), a free
weather API that needs no key and no signup.

Usage:
    python3 fetch_weather.py          # reads belt_data/next_game.json, writes belt_data/weather.json

Run this AFTER build_lineage.py in the same pipeline -- it reads
belt_data/next_game.json (which now carries the venue's lat/lon and the
game's UTC kickoff time, both mined for free out of the /venues and
/games calls build_lineage.py already makes -- no extra CFBD call here at
all, and no API key needed for Open-Meteo either).

Safe to run with no upcoming game, no venue coordinates, or no network
access -- in every case it writes belt_data/weather.json = null rather
than failing the pipeline. This feature is optional, same reasoning as
generate_ai_preview.py / generate_recaps.py: a bad forecast call should
never be the reason the whole site fails to build.

Forecast range: Open-Meteo's free forecast endpoint only covers roughly
the next 16 days. Most "up next" games are within a week (this project's
schedule cadence is weekly), so that's comfortably enough in practice --
but if the next game is further out than that (an early-announced
championship date, an offseason placeholder), this just skips forecasting
it for now rather than erroring; a later run, once the game is within
range, picks it up automatically.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

OUT_DIR = "belt_data"
API_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_HORIZON_DAYS = 15  # stay a day inside Open-Meteo's ~16-day free window

# WMO weather codes, as used by Open-Meteo's "weathercode" field --
# https://open-meteo.com/en/docs#weathervariables
WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Light rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Light snow showers", 86: "Heavy snow showers",
    95: "Thunderstorms", 96: "Thunderstorms with light hail", 99: "Thunderstorms with heavy hail",
}


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def get_json(url, retries=3):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    delay = 2
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                import time
                time.sleep(delay)
                delay *= 2
                continue
            raise


def parse_kickoff_utc(raw_date):
    """CFBD's start_date, e.g. '2026-09-19T19:30:00.000Z' -> aware datetime."""
    s = raw_date.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def nearest_hour_index(hourly_times, kickoff_utc):
    """hourly_times are 'YYYY-MM-DDTHH:MM' strings in UTC (timezone=UTC was
    requested below); find the one closest to kickoff."""
    target = kickoff_utc.replace(minute=0, second=0, microsecond=0)
    if kickoff_utc.minute >= 30:
        target += timedelta(hours=1)
    target_str = target.strftime("%Y-%m-%dT%H:%M")
    for i, t in enumerate(hourly_times):
        if t == target_str:
            return i
    # Fall back to the closest available hour rather than giving up entirely.
    best_i, best_diff = None, None
    for i, t in enumerate(hourly_times):
        try:
            dt = datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        diff = abs((dt - kickoff_utc).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i


def main():
    out_path = os.path.join(OUT_DIR, "weather.json")
    os.makedirs(OUT_DIR, exist_ok=True)

    next_game = load_json(os.path.join(OUT_DIR, "next_game.json"))
    if not next_game:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print("No upcoming game -- nothing to forecast.")
        return

    lat, lon = next_game.get("venue_lat"), next_game.get("venue_lon")
    raw_date = next_game.get("raw_date")
    if lat is None or lon is None or not raw_date:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print("No venue coordinates or kickoff time on next_game.json -- "
              "skipping the forecast (the rest of the site builds fine "
              "without it).")
        return

    try:
        kickoff_utc = parse_kickoff_utc(raw_date)
    except ValueError as e:
        print(f"Couldn't parse kickoff time {raw_date!r} ({e}) -- skipping "
              f"the forecast.", file=sys.stderr)
        with open(out_path, "w") as f:
            json.dump(None, f)
        return

    now = datetime.now(timezone.utc)
    days_out = (kickoff_utc.date() - now.date()).days
    if days_out < 0 or days_out > FORECAST_HORIZON_DAYS:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print(f"Next game is {days_out} day(s) out -- outside Open-Meteo's "
              f"~{FORECAST_HORIZON_DAYS + 1}-day free forecast window for now. "
              f"A later run, once it's in range, will pick this up.")
        return

    date_str = kickoff_utc.date().isoformat()
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,apparent_temperature,precipitation_probability,windspeed_10m,weathercode",
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
        "start_date": date_str,
        "end_date": date_str,
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"

    try:
        data = get_json(url)
    except Exception as e:
        print(f"Weather forecast fetch failed ({e}) -- continuing without "
              f"one rather than failing the whole build.", file=sys.stderr)
        with open(out_path, "w") as f:
            json.dump(None, f)
        return

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print("Open-Meteo returned no hourly data for that date -- skipping "
              "the forecast.")
        return

    idx = nearest_hour_index(times, kickoff_utc)
    if idx is None:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print("Couldn't match a forecast hour to kickoff -- skipping.")
        return

    def at(key):
        vals = hourly.get(key) or []
        return vals[idx] if idx < len(vals) else None

    code = at("weathercode")
    weather = {
        "venue_name": next_game.get("venue_name"),
        "venue_city": next_game.get("venue_city"),
        "venue_state": next_game.get("venue_state"),
        "kickoff_utc": raw_date,
        "forecast_hour_utc": times[idx],
        "temp_f": at("temperature_2m"),
        "feels_like_f": at("apparent_temperature"),
        "precip_chance": at("precipitation_probability"),
        "wind_mph": at("windspeed_10m"),
        "condition": WEATHER_CODES.get(code, "Unknown") if code is not None else None,
        "fetched": now.strftime("%Y-%m-%d"),
    }
    with open(out_path, "w") as f:
        json.dump(weather, f, indent=2)

    print(f"Wrote {out_path}: {weather['temp_f']}°F, "
          f"{weather['condition']}, wind {weather['wind_mph']} mph, "
          f"{weather['precip_chance']}% precip chance, at "
          f"{weather['venue_name'] or 'the venue'} ({days_out} day(s) out).")


if __name__ == "__main__":
    main()
