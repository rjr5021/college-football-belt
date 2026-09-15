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
    python3 build_lineage.py --full-refetch          # ignore the historical
                                                      # baseline, redo 1869-now

Outputs (into ./belt_data/, all regenerated fresh every run, none committed):
    games_raw.json    this run's freshly-fetched games (see "Incremental
                      fetching" below -- NOT every game since 1869 anymore)
    venues_raw.json   every venue pulled, cached so reruns don't refetch
    belt_games.csv    every game the belt was at stake in, 1869-now
    reigns.csv        one row per reign, 1869-now
    lineage.json      structured dataset for the site to consume, 1869-now
    next_game.json    the current holder's next scheduled game, mined for
                      free out of games_raw.json (which already includes
                      the season's unplayed games -- no extra API call);
                      null if nothing upcoming is in the fetched window
    upcoming_games.json  same idea, up to the next 3 games ("Belt Watch"
                      on the homepage) -- [] if nothing's upcoming

Incremental fetching and the CFBD call budget: CFBD's FREE tier is capped at
1,000 calls/MONTH (not a short burst limit -- see
https://collegefootballdata.com/api-tiers) and a full 1869-now pull is
~316 calls (158+ years x 2 season types). Refetching the entire history on
every run -- which is what this script used to do, since GitHub Actions
runners never keep belt_data/ between runs -- burns through that monthly
budget in a small handful of runs and was the real cause of persistent
"429 Too Many Requests" failures in CI (retrying more patiently doesn't
help when the problem is "no calls left this month," not "too many calls
this second").

The fix: 1869-2002-ish never changes -- final scores are permanent -- so
there's no reason to ever refetch a season once it's fully concluded and
no more games will be added to it. This script now keeps a small,
GIT-COMMITTED baseline (./historical_data/baseline.json: the already-walked
chain of custody -- closed reigns + their belt games -- through the end of
whatever seasons are safely "done") and, each run, freshly fetches ONLY the
current and previous season (2 seasons x 2 season types = ~4 calls) to
extend that baseline forward. A full run is now ~4-6 calls instead of ~316,
comfortably inside the free tier even on a daily schedule. The baseline
file updates (and, via the GitHub Actions workflow, commits back to the
repo) automatically as seasons age out of the "current + previous" window
-- no manual maintenance needed.

`--full-refetch` (or no existing ./historical_data/baseline.json, e.g. a
brand new checkout) falls back to the original full 1869-now pull, still
useful for a genuine from-scratch rebuild (a tie-rule change, a suspected
data issue, or bootstrapping the baseline for the first time).

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

429s (which can still happen on a `--full-refetch` run, or just from bad
luck) get a patient, capped-exponential backoff (up to 60s, 8 tries)
distinct from the faster backoff used for 5xx/network errors, and
`--full-refetch` progress is saved year-by-year to a resumable cache -- so
a run that still exhausts retries and dies partway through doesn't lose
the years it already fetched; just rerun the same command.

The CFBD API has used both snake_case and camelCase field names across
versions; this script accepts either.
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
OUT_DIR = "belt_data"          # ephemeral, regenerated every run, gitignored
HIST_DIR = "historical_data"   # small, git-committed baseline lives here


def pick(d, *names, default=None):
    """CFBD field names have shifted between snake_case and camelCase."""
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _get_json(url, api_key, label, retries=8):
    """Shared GET-with-retry used for both /games and /venues.

    429s (rate limit) get a much more patient, longer backoff than a 5xx or
    a network blip -- capped at 60s, over `retries` tries -- since CFBD's
    rate limit needs real time to clear, not a couple of quick retries.
    """
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
            if e.code == 429 and attempt < retries - 1:
                wait = min(5 * (2 ** attempt), 60)
                print(f"  429 on {label}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code in (500, 502, 503) and attempt < retries - 1:
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


def fetch_year(year, api_key, season_type, retries=8):
    url = f"{API_BASE}/games?year={year}&seasonType={season_type}"
    return _get_json(url, api_key, f"{year}/{season_type}", retries=retries)


def fetch_venues(api_key, retries=8):
    return _get_json(f"{API_BASE}/venues", api_key, "venues", retries=retries)


def fetch_division1_teams(api_key, retries=8):
    """Every school CFBD currently classifies as "fbs" or "fcs" -- i.e.
    NCAA Division 1 -- as a {school: classification} dict (classification
    is the literal string "fbs" or "fcs"). A single cheap call (GET
    /teams, no year filter), so it's negligible against the call budget
    either belt cares about.

    Used only by the Losers Belt (see build_losers_lineage.py's own
    filter_division1_games) to keep that belt restricted to Division 1
    programs -- not applied to this, the real belt. The per-team
    classification lets the Losers Belt build three separate lineages
    (FBS-only, FCS-only, and combined Division 1) from one shared fetch
    instead of one flat "is D1" bit. This is CFBD's CURRENT
    classification, applied uniformly across all of history, not a
    season-by-season historical one: the FBS/FCS split didn't exist
    before 1978, so there's no meaningful historical classification to
    apply before then anyway. A school is either a Division 1 program
    today or it isn't (and is either FBS or FCS today or it isn't);
    that's the eligibility bar, regardless of when a given game was
    played.
    """
    teams = _get_json(f"{API_BASE}/teams", api_key, "teams", retries=retries)
    return {t["school"]: t["classification"] for t in teams
            if t.get("classification") in ("fbs", "fcs")}


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
    """Map venue id -> IANA timezone name, e.g. 3713 -> 'America/New_York',
    and a second map venue id -> {name, city, state, lat, lon} for whatever
    coordinates CFBD has (used by fetch_weather.py for the upcoming game's
    forecast -- no extra API call, same venues response this already pulls
    for timezones).

    CFBD's own "timezone" field on a venue is usually null, so this derives
    the zone from the venue's latitude/longitude via `timezonefinder` when
    the field isn't populated. Always fetched fresh (a single cheap call --
    new stadiums do appear occasionally); not part of the call-budget
    problem this script otherwise solves for.
    """
    cache = os.path.join(OUT_DIR, "venues_raw.json")
    raw = fetch_venues(api_key)
    with open(cache, "w") as f:
        json.dump(raw, f)
    print(f"Cached {len(raw)} venues to {cache}")

    tf = _get_tzfinder()
    tz_by_venue = {}
    venue_info = {}
    from_field = from_coords = unresolved = 0
    for v in raw:
        vid = pick(v, "id")
        if vid is None:
            continue
        lat = pick(v, "latitude", "lat")
        lng = pick(v, "longitude", "lng", "lon")
        try:
            lat_f = float(lat) if lat is not None else None
            lng_f = float(lng) if lng is not None else None
        except (TypeError, ValueError):
            lat_f = lng_f = None
        venue_info[vid] = {
            "name": pick(v, "name"),
            "city": pick(v, "city"),
            "state": pick(v, "state"),
            "lat": lat_f,
            "lon": lng_f,
        }

        tz = pick(v, "timezone", "time_zone")
        if tz:
            tz_by_venue[vid] = tz
            from_field += 1
            continue
        found = None
        if tf is not None and lat_f is not None and lng_f is not None:
            try:
                found = tf.timezone_at(lat=lat_f, lng=lng_f)
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
    return tz_by_venue, venue_info


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


def fetch_seasons(seasons, api_key):
    """Fetch every game (any division) for each season year in `seasons`,
    both season types. Progress is saved incrementally per (year, type) to
    a resumable cache in case of a crash or exhausted retries -- cheap
    insurance even though this is normally a tiny handful of seasons now.
    """
    progress_cache = os.path.join(OUT_DIR, "games_raw_progress.json")
    progress = {}
    if os.path.exists(progress_cache):
        with open(progress_cache) as f:
            progress = json.load(f)
        print(f"Resuming {progress_cache}: {len(progress)} season/type "
              f"combo(s) already fetched")

    def save_progress():
        with open(progress_cache, "w") as f:
            json.dump(progress, f)

    try:
        for year in seasons:
            for st in ("regular", "postseason"):
                key = f"{year}/{st}"
                if key in progress:
                    continue
                rows = fetch_year(year, api_key, st)
                progress[key] = rows
                print(f"  {year}/{st}: {len(rows)} games")
                save_progress()
                time.sleep(0.15)
    finally:
        save_progress()

    all_games = [g for year in seasons for st in ("regular", "postseason")
                 for g in progress.get(f"{year}/{st}", [])]
    cache = os.path.join(OUT_DIR, "games_raw.json")
    with open(cache, "w") as f:
        json.dump(all_games, f)
    print(f"Cached {len(all_games)} games (seasons {sorted(seasons)}) to {cache}")
    os.remove(progress_cache)
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


def find_upcoming_games(raw, holder, count=3, venue_tz=None, venue_info=None):
    """The current holder's next `count` scheduled games, straight from
    this run's freshly-fetched season data -- CFBD returns the full season
    (including games it hasn't scored yet), and normalize() above throws
    those away since the chain walk only cares about settled results. This
    re-scans the same `raw` list (no extra API call) for the holder's
    earliest not-yet-played games, so the site can show what's coming next
    -- "Belt Watch" wants a short lookahead, not just the very next game.

    Returns [] if nothing upcoming is in the fetched window -- e.g. the
    next game's schedule slot hasn't been announced yet, or the season's
    already over and next year's slate isn't out. Order is chronological
    (soonest first); a season's schedule rarely has more than `count` games
    left to give anyway, but this never returns more than that."""
    venue_tz = venue_tz or {}
    venue_info = venue_info or {}
    candidates = []
    for g in raw:
        home = pick(g, "home_team", "homeTeam")
        away = pick(g, "away_team", "awayTeam")
        if holder not in (home, away):
            continue
        hp = pick(g, "home_points", "homePoints")
        ap = pick(g, "away_points", "awayPoints")
        if hp is not None and ap is not None:
            continue  # already played
        raw_date = pick(g, "start_date", "startDate")
        if not raw_date:
            continue
        venue_id = pick(g, "venue_id", "venueId")
        date, _ = local_date(raw_date, venue_tz.get(venue_id))
        if not date:
            continue
        candidates.append({
            "date": date,
            "raw_date": raw_date,
            "season": pick(g, "season"),
            "week": pick(g, "week"),
            "season_type": pick(g, "season_type", "seasonType", default="regular"),
            "home": home,
            "away": away,
            "neutral": bool(pick(g, "neutral_site", "neutralSite", default=False)),
            "venue_id": venue_id,
        })
    candidates.sort(key=lambda c: (c["date"], c["raw_date"]))

    upcoming = []
    for nxt in candidates[:count]:
        is_home = nxt["home"] == holder
        v = venue_info.get(nxt["venue_id"]) or {}
        upcoming.append({
            "team": holder,
            "opponent": nxt["away"] if is_home else nxt["home"],
            "is_home": is_home,
            "neutral": nxt["neutral"],
            "date": nxt["date"],
            "raw_date": nxt["raw_date"],   # UTC kickoff, e.g. 2026-09-19T19:30:00.000Z -- for fetch_weather.py
            "season": nxt["season"],
            "week": nxt["week"],
            "season_type": nxt["season_type"],
            "venue_name": v.get("name"),
            "venue_city": v.get("city"),
            "venue_state": v.get("state"),
            "venue_lat": v.get("lat"),
            "venue_lon": v.get("lon"),
        })
    return upcoming


def find_next_game(raw, holder, venue_tz=None, venue_info=None):
    """The current holder's single next scheduled game, or None -- kept as
    its own function since fetch_matchup_preview.py and
    generate_ai_preview.py only ever care about the immediate next game,
    not the short lookahead find_upcoming_games gives "Belt Watch"."""
    upcoming = find_upcoming_games(raw, holder, count=1, venue_tz=venue_tz, venue_info=venue_info)
    return upcoming[0] if upcoming else None


def walk(games, tie_rule="holder", start_holder=None, start_reign=None):
    """Walk the chain, in chronological order, over `games`.

    By default (start_holder=None) this bootstraps from games[0] exactly
    as the very first belt game (Rutgers-Princeton) always has: games[0]
    itself establishes the initial holder and is NOT recorded as a belt
    game, and every following game where the holder plays gets folded in.

    Pass start_holder/start_reign to RESUME an existing chain instead --
    `start_reign` should be the still-open reign dict (as returned by a
    previous call to this function, whose last reign entry always has
    end_date=None) and `games` should be ONLY the games that happen after
    that reign's last known state (typically just the current + previous
    season's freshly-fetched games). Every game in `games` is then folded
    in starting from that state, with nothing treated as the bootstrap
    game -- the caller is responsible for not re-passing any game already
    reflected in `start_reign`/its prior belt games.

    Returns (belt_games, reigns) covering only what this call folded in --
    reigns' last entry is always the (possibly still-open) current reign.
    The caller combines this with whatever came before (either nothing, in
    the bootstrap case, or a baseline's historical belt_games/reigns, in
    the resume case).
    """
    games = [g for g in games if g["date"] >= FIRST_GAME_DATE]

    if start_holder is None:
        if not games:
            sys.exit("No games at or after the first-game date -- check the data pull.")
        first = games[0]
        if first["date"] != FIRST_GAME_DATE:
            print(f"WARNING: first game is {first['date']}, expected {FIRST_GAME_DATE}",
                  file=sys.stderr)
        holder = (first["home"] if first["home_points"] > first["away_points"]
                  else first["away"])
        reign = {"team": holder, "start_date": first["date"], "won_from": None,
                 "won_score": f"{first['home_points']}-{first['away_points']}",
                 "defenses": 0}
        remaining = games[1:]
        # The bootstrap game itself IS a real, playable belt game -- the one
        # that put the belt up in the first place -- so it belongs in
        # belt_games too (outcome "established", holder None since there was
        # no defending champion yet), not just reflected in reigns[0]. This
        # gives it a real game page like every other belt game instead of
        # silently having none. split_at_season() knows to skip this outcome
        # when replaying defenses (see its own comment) since reigns[0]
        # above already reflects it.
        bootstrap_belt_game = {
            "date": first["date"], "season": first["season"], "week": first["week"],
            "season_type": first["season_type"], "holder": None,
            "opponent": (first["away"] if holder == first["home"] else first["home"]),
            "home": first["home"], "away": first["away"],
            "score": f"{first['home_points']}-{first['away_points']}",
            "neutral": first["neutral"], "outcome": "established",
            "new_holder": holder, "game_id": first["id"],
        }
    else:
        holder = start_holder
        reign = dict(start_reign)
        remaining = games
        bootstrap_belt_game = None

    belt_games, reigns = ([bootstrap_belt_game] if bootstrap_belt_game else []), []

    for g in remaining:
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


def split_at_season(belt_games, reigns, live_start_year):
    """Given a COMPLETE (belt_games, reigns) pair -- reigns' last entry
    open (end_date None), every other reign closed -- split off everything
    with season < live_start_year as a frozen, resumable baseline:

        (historical_belt_games, historical_closed_reigns, open_reign)

    `open_reign` is the reign state exactly as of the start of
    `live_start_year` -- i.e. what walk(..., start_holder=.., start_reign=..)
    needs to correctly resume and reproduce the exact same tail if fed the
    same games again. This replays belt_games' own already-decided
    outcome/new_holder fields (no tie_rule needed -- those decisions were
    already made) rather than touching raw per-game data, so the frozen
    baseline never needs anything but the small, already-computed
    belt_games/reigns lists CFBD-side re-verification would still start
    from the same recorded facts.
    """
    historical_belt_games = [bg for bg in belt_games if bg["season"] < live_start_year]

    if not historical_belt_games:
        # The live window starts at or before the very first belt game --
        # the original bootstrap reign (Rutgers) IS the open reign.
        first_reign = reigns[0]
        open_reign = {"team": first_reign["team"], "start_date": first_reign["start_date"],
                      "won_from": first_reign["won_from"], "won_score": first_reign["won_score"],
                      "defenses": 0}
        return [], [], open_reign

    closed = []
    first_reign = reigns[0]
    reign = {"team": first_reign["team"], "start_date": first_reign["start_date"],
             "won_from": first_reign["won_from"], "won_score": first_reign["won_score"],
             "defenses": 0}
    for bg in historical_belt_games:
        if bg["outcome"] == "established":
            # The bootstrap game itself -- already fully reflected in
            # first_reign/reign above (same team, start_date, won_score).
            # Not a defense of that reign, so don't double-count it.
            continue
        if bg["outcome"] in ("changed", "lost (tie)"):
            reign["end_date"] = bg["date"]
            reign["lost_to"] = bg["new_holder"]
            closed.append(reign)
            reign = {"team": bg["new_holder"], "start_date": bg["date"],
                     "won_from": bg["holder"], "won_score": bg["score"], "defenses": 0}
        else:
            reign["defenses"] += 1
    return historical_belt_games, closed, reign


def load_baseline():
    path = os.path.join(HIST_DIR, "baseline.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_baseline(historical_belt_games, historical_reigns, open_reign, live_start_year):
    os.makedirs(HIST_DIR, exist_ok=True)
    path = os.path.join(HIST_DIR, "baseline.json")
    with open(path, "w") as f:
        json.dump({
            "live_start_year": live_start_year,
            "historical_belt_games": historical_belt_games,
            "historical_reigns": historical_reigns,
            "open_reign": open_reign,
        }, f, indent=2)
    print(f"Wrote {path} (historical through season {live_start_year - 1}, "
          f"{len(historical_belt_games)} belt games / {len(historical_reigns)} "
          f"closed reigns frozen)")


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
    p.add_argument("--full-refetch", action="store_true",
                   help="ignore historical_data/baseline.json and rebuild "
                        "the whole 1869-now chain from scratch (~316 CFBD "
                        "calls) instead of the normal incremental update")
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = p.parse_args()

    if not args.key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                 "Free key at https://collegefootballdata.com/key")

    os.makedirs(OUT_DIR, exist_ok=True)

    print("Fetching venue timezones from CFBD...")
    venue_tz, venue_info = collect_venues(args.key)

    baseline = None if args.full_refetch else load_baseline()
    live_start_year = args.end_year - 1

    if baseline is None:
        print(f"No historical baseline found -- doing a full "
              f"{args.start_year}-{args.end_year} pull from CFBD "
              f"(~{2 * (args.end_year - args.start_year + 1)} calls).")
        raw = fetch_seasons(range(args.start_year, args.end_year + 1), args.key)
        games = normalize(raw, venue_tz)
        print(f"{len(games)} completed games in chronological order")
        belt_games, reigns = walk(games, args.tie_rule)
    else:
        fetch_from = min(baseline["live_start_year"], live_start_year)
        seasons = range(fetch_from, args.end_year + 1)
        print(f"Historical baseline found (through season {fetch_from - 1}, "
              f"{len(baseline['historical_belt_games'])} belt games already "
              f"settled) -- fetching only seasons {list(seasons)} fresh "
              f"(~{2 * len(list(seasons))} calls).")
        raw = fetch_seasons(seasons, args.key)
        games = normalize(raw, venue_tz)
        print(f"{len(games)} completed games in the live window")
        new_belt_games, tail_reigns = walk(
            games, args.tie_rule,
            start_holder=baseline["open_reign"]["team"],
            start_reign=baseline["open_reign"],
        )
        belt_games = baseline["historical_belt_games"] + new_belt_games
        reigns = baseline["historical_reigns"] + tail_reigns

    write_outputs(belt_games, reigns, args.tie_rule)

    # Advance the baseline to today's live-window boundary. Most runs this
    # reproduces the same split as before (no season has aged out since
    # last time); once a year it absorbs one more season permanently.
    hist_belt_games, hist_reigns, open_reign = split_at_season(
        belt_games, reigns, live_start_year)
    save_baseline(hist_belt_games, hist_reigns, open_reign, live_start_year)

    current = reigns[-1]
    teams = len({r["team"] for r in reigns})

    upcoming_games = find_upcoming_games(raw, current["team"], count=3, venue_tz=venue_tz, venue_info=venue_info)
    next_game = upcoming_games[0] if upcoming_games else None
    with open(os.path.join(OUT_DIR, "next_game.json"), "w") as f:
        json.dump(next_game, f, indent=2)
    with open(os.path.join(OUT_DIR, "upcoming_games.json"), "w") as f:
        json.dump(upcoming_games, f, indent=2)
    if next_game:
        side = "vs." if next_game["is_home"] else "at"
        print(f"Next game: {current['team']} {side} {next_game['opponent']} "
              f"on {next_game['date']} ({len(upcoming_games)} game(s) in the "
              f"Belt Watch lookahead)")
    else:
        print("No upcoming game found for the current holder in the fetched "
              "window (schedule not out yet, or the season's over).")

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
