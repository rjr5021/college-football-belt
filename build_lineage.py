#!/usr/bin/env python3
"""
Build the College Football Belt lineage from the CollegeFootballData API.

The lineal belt starts with the winner of the first college football game
(Rutgers over Princeton, 1869-11-06) and passes to whoever beats the holder.
Given every game in chronological order, the chain is fully determined --
this script computes it rather than researching it.

Usage:
    export CFBD_API_KEY=your_key_here      # get one at collegefootballdata.com
    python3 build_lineage.py

    python3 build_lineage.py --tie-rule challenger   # holder loses belt on a tie
    python3 build_lineage.py --start-year 1869 --end-year 2026

Outputs (into ./belt_data/):
    games_raw.json    every game pulled, cached so reruns don't refetch
    venues_raw.json   every venue pulled, cached so reruns don't refetch
    belt_games.csv    every game the belt was at stake in
    reigns.csv        one row per reign
    lineage.json      structured dataset for the site to consume

Dates: CFBD's start_date is UTC. A game's calendar date is taken from the
LOCAL kickoff time at the venue, not a naive truncation of the UTC string --
a late West Coast kickoff can otherwise land on the wrong side of midnight
UTC and get dated a day off. CFBD's /venues endpoint has a "timezone" field
but it's null for nearly every venue in practice, so the timezone is instead
derived from each venue's latitude/longitude via the `timezonefinder`
package (offline, no extra network call). Venues with neither an explicit
timezone nor usable coordinates fall back to the naive UTC-date truncation
(the run summary reports how many, split out by how each was resolved).

Requires two extra packages beyond the standard library:
    pip install tzdata timezonefinder
tzdata is needed on Windows, where Python doesn't ship the IANA timezone
database that the zoneinfo module (and timezonefinder) rely on.
timezonefinder is what turns venue coordinates into an IANA zone name.
Without it, every game falls back to the naive UTC date -- the script still
runs, just with the same off-by-a-day risk as before this fix.

NOTE: written without network access and NOT yet executed. The CFBD API has
used both snake_case and camelCase field names across versions; this script
accepts either, but expect to debug the first run.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

API_BASE = "https://api.collegefootballdata.com"
FIRST_GAME_DATE = "1869-11-06"
OUT_DIR = "belt_data"


def pick(d, *names, default=None):
    """CFBD field names have shifted between snake_case and camelCase."""
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _get_json(url, api_key, label, retries=4):
    """Shared GET-with-retry used for both /games and /venues."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  {e.code} on {label}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return []


def fetch_year(year, api_key, season_type, retries=4):
    url = f"{API_BASE}/games?year={year}&seasonType={season_type}"
    return _get_json(url, api_key, f"{year}/{season_type}", retries=retries)


def fetch_venues(api_key, retries=4):
    return _get_json(f"{API_BASE}/venues", api_key, "venues", retries=retries)


_TZF = "UNSET"


def _get_tzfinder():
    """Lazily construct a single TimezoneFinder (loads its coordinate data
    once). Returns None if the `timezonefinder` package isn't installed."""
    global _TZF
    if _TZF == "UNSET":
        try:
            from timezonefinder import TimezoneFinder
            _TZF = TimezoneFinder()
        except ImportError:
            _TZF = None
    return _TZF


def collect_venues(api_key):
    """Map venue id -> IANA timezone name, e.g. 3713 -> 'America/New_York'.

    CFBD's own "timezone" field on a venue is usually null, so this derives
    the zone from the venue's latitude/longitude via `timezonefinder` when
    the field isn't populated.
    """
    cache = os.path.join(OUT_DIR, "venues_raw.json")
    if os.path.exists(cache):
        print(f"Using cached {cache} (delete it to refetch)")
        with open(cache) as f:
            raw = json.load(f)
    else:
        raw = fetch_venues(api_key)
        with open(cache, "w") as f:
            json.dump(raw, f)
        print(f"Cached {len(raw)} venues to {cache}")

    tf = _get_tzfinder()
    tz_by_venue = {}
    from_field = from_coords = unresolved = 0
    for v in raw:
        vid = pick(v, "id")
        if vid is None:
            continue
        tz = pick(v, "timezone", "time_zone")
        if tz:
            tz_by_venue[vid] = tz
            from_field += 1
            continue
        lat = pick(v, "latitude", "lat")
        lng = pick(v, "longitude", "lng", "lon")
        found = None
        if tf is not None and lat is not None and lng is not None:
            try:
                found = tf.timezone_at(lat=float(lat), lng=float(lng))
            except Exception:
                found = None
        if found:
            tz_by_venue[vid] = found
            from_coords += 1
        else:
            unresolved += 1

    print(f"Venue timezones: {from_field} from CFBD's own field, "
          f"{from_coords} derived from venue coordinates, "
          f"{unresolved} of {len(raw)} venues unresolved.")
    if tf is None:
        print("  Install 'timezonefinder' (pip install timezonefinder) to "
              "derive timezones from venue coordinates -- most venues only "
              "have coordinates, not an explicit timezone.")
    return tz_by_venue


_ZONE_CACHE = {}


def local_date(iso_utc, venue_tz_name):
    """Calendar date of a UTC kickoff time, in the venue's local timezone.

    Falls back to a naive truncation of the UTC string when there's no
    venue timezone to convert with (unmatched venue, or missing tzdata).
    Returns (date_str, used_fallback).
    """
    if not iso_utc:
        return None, True
    naive_fallback = str(iso_utc)[:10]

    if not venue_tz_name:
        return naive_fallback, True

    tz = _ZONE_CACHE.get(venue_tz_name, "MISS")
    if tz == "MISS":
        try:
            tz = ZoneInfo(venue_tz_name)
        except Exception:
            tz = None
        _ZONE_CACHE[venue_tz_name] = tz
    if tz is None:
        return naive_fallback, True

    try:
        s = str(iso_utc).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).date().isoformat(), False
    except ValueError:
        return naive_fallback, True


def collect(start_year, end_year, api_key):
    cache = os.path.join(OUT_DIR, "games_raw.json")
    if os.path.exists(cache):
        print(f"Using cached {cache} (delete it to refetch)")
        with open(cache) as f:
            return json.load(f)

    all_games = []
    for year in range(start_year, end_year + 1):
        got = 0
        for st in ("regular", "postseason"):
            rows = fetch_year(year, api_key, st)
            all_games.extend(rows)
            got += len(rows)
        print(f"  {year}: {got} games")
        time.sleep(0.15)

    with open(cache, "w") as f:
        json.dump(all_games, f)
    print(f"Cached {len(all_games)} games to {cache}")
    return all_games


def normalize(raw, venue_tz=None):
    """Flatten to the handful of fields the chain walk actually needs."""
    venue_tz = venue_tz or {}
    out = []
    skipped = 0
    fallback_dates = 0
    for g in raw:
        home = pick(g, "home_team", "homeTeam")
        away = pick(g, "away_team", "awayTeam")
        hp = pick(g, "home_points", "homePoints")
        ap = pick(g, "away_points", "awayPoints")
        raw_date = pick(g, "start_date", "startDate")
        if not (home and away and raw_date) or hp is None or ap is None:
            skipped += 1          # unplayed, scheduled, or missing a score
            continue
        venue_id = pick(g, "venue_id", "venueId")
        date, used_fallback = local_date(raw_date, venue_tz.get(venue_id))
        if used_fallback:
            fallback_dates += 1
        out.append({
            "id": pick(g, "id"),
            "date": date,
            "season": pick(g, "season"),
            "week": pick(g, "week"),
            "season_type": pick(g, "season_type", "seasonType", default="regular"),
            "home": home,
            "away": away,
            "home_points": int(hp),
            "away_points": int(ap),
            "neutral": bool(pick(g, "neutral_site", "neutralSite", default=False)),
        })
    out.sort(key=lambda x: (x["date"], x["season_type"] != "regular", x["id"] or 0))
    if skipped:
        print(f"Skipped {skipped} games with no final score (unplayed/scheduled)")
    if fallback_dates:
        pct = 100 * fallback_dates / max(len(out), 1)
        print(f"{fallback_dates} of {len(out)} games ({pct:.1f}%) had no venue "
              f"timezone to convert with, and used the naive UTC date instead. "
              f"If that's high, see the venue-timezone summary above -- it "
              f"usually means 'pip install tzdata timezonefinder' is needed.")
    return out


def walk(games, tie_rule="holder"):
    """Walk the chain. Returns (belt_games, reigns)."""
    games = [g for g in games if g["date"] >= FIRST_GAME_DATE]
    if not games:
        sys.exit("No games at or after the first-game date -- check the data pull.")

    first = games[0]
    if first["date"] != FIRST_GAME_DATE:
        print(f"WARNING: first game is {first['date']}, expected {FIRST_GAME_DATE}",
              file=sys.stderr)

    holder = (first["home"] if first["home_points"] > first["away_points"]
              else first["away"])
    belt_games, reigns = [], []
    reign = {"team": holder, "start_date": first["date"], "won_from": None,
             "won_score": f"{first['home_points']}-{first['away_points']}",
             "defenses": 0}

    for g in games[1:]:
        if holder not in (g["home"], g["away"]):
            continue

        hp, ap = g["home_points"], g["away_points"]
        if hp == ap:
            outcome = "retained (tie)" if tie_rule == "holder" else "lost (tie)"
            changed = tie_rule != "holder"
            winner = (g["away"] if holder == g["home"] else g["home"]) if changed else holder
        else:
            winner = g["home"] if hp > ap else g["away"]
            changed = winner != holder
            outcome = "changed" if changed else "retained"

        opponent = g["away"] if holder == g["home"] else g["home"]
        belt_games.append({
            "date": g["date"], "season": g["season"], "week": g["week"],
            "season_type": g["season_type"], "holder": holder,
            "opponent": opponent, "home": g["home"], "away": g["away"],
            "score": f"{hp}-{ap}", "neutral": g["neutral"],
            "outcome": outcome, "new_holder": winner, "game_id": g["id"],
        })

        if changed:
            reign["end_date"] = g["date"]
            reign["lost_to"] = winner
            reigns.append(reign)
            reign = {"team": winner, "start_date": g["date"], "won_from": holder,
                     "won_score": f"{hp}-{ap}", "defenses": 0}
            holder = winner
        else:
            reign["defenses"] += 1

    reign["end_date"] = None
    reign["lost_to"] = None
    reigns.append(reign)
    return belt_games, reigns


def write_outputs(belt_games, reigns, tie_rule):
    with open(os.path.join(OUT_DIR, "belt_games.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(belt_games[0].keys()))
        w.writeheader()
        w.writerows(belt_games)

    with open(os.path.join(OUT_DIR, "reigns.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["team", "start_date", "end_date",
                                          "won_from", "won_score", "lost_to",
                                          "defenses"])
        w.writeheader()
        for r in reigns:
            w.writerow({k: r.get(k) for k in w.fieldnames})

    current = reigns[-1]
    with open(os.path.join(OUT_DIR, "lineage.json"), "w") as f:
        json.dump({
            "generated": time.strftime("%Y-%m-%d"),
            "tie_rule": tie_rule,
            "origin": {"date": FIRST_GAME_DATE,
                       "note": "Rutgers def. Princeton 6-4, first game ever played"},
            "current_holder": current["team"],
            "current_reign_since": current["start_date"],
            "current_defenses": current["defenses"],
            "totals": {
                "belt_games": len(belt_games),
                "reigns": len(reigns),
                "distinct_teams": len({r["team"] for r in reigns}),
            },
            "reigns": reigns,
            "belt_games": belt_games,
        }, f, indent=2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-year", type=int, default=1869)
    p.add_argument("--end-year", type=int, default=time.localtime().tm_year)
    p.add_argument("--tie-rule", choices=["holder", "challenger"], default="holder",
                   help="who keeps the belt on a tie (default: holder retains)")
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = p.parse_args()

    if not args.key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                 "Free key at https://collegefootballdata.com/key")

    os.makedirs(OUT_DIR, exist_ok=True)

    print("Fetching venue timezones from CFBD...")
    venue_tz = collect_venues(args.key)

    print(f"Pulling {args.start_year}-{args.end_year} from CFBD...")
    raw = collect(args.start_year, args.end_year, args.key)
    games = normalize(raw, venue_tz)
    print(f"{len(games)} completed games in chronological order")

    belt_games, reigns = walk(games, args.tie_rule)
    write_outputs(belt_games, reigns, args.tie_rule)

    current = reigns[-1]
    teams = len({r["team"] for r in reigns})
    print(f"""
Lineage built -> {OUT_DIR}/
  belt games      {len(belt_games):>6}   (cfb-belt.com reports ~1,636)
  reigns          {len(reigns):>6}   (cfb-belt.com reports ~328)
  distinct teams  {teams:>6}   (cfb-belt.com reports ~101)

  current holder  {current['team']} since {current['start_date']}
                  ({current['defenses']} defenses)

Expected as of 2026-09-13: Notre Dame, since 2025-11-29, 2 defenses.
A large divergence from the reference totals usually means a ruleset
difference -- the tie rule is the most common culprit.""")


if __name__ == "__main__":
    main()
