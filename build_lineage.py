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

from belt_engine import (
    filter_division1_games,
    load_json as engine_load_json,
    load_vacancies as engine_load_vacancies,
    merge_vacancies,
    resolve_vacancies,
    save_json as engine_save_json,
    split_winner_baseline,
)

API_BASE = "https://api.collegefootballdata.com"
FIRST_GAME_DATE = "1869-11-06"
OUT_DIR = "belt_data"          # ephemeral, regenerated every run, gitignored
HIST_DIR = "historical_data"   # small, git-committed baseline lives here

# Three parallel championship-belt lineages, one per SCOPE, mirroring
# build_losers_lineage.py's own SCOPES: "combined" is the real belt exactly
# as it's always worked -- no Division 1 restriction at all, unsuffixed
# filenames, walk()/split_at_season() below untouched -- so the
# already-live baseline.json/lineage.json stay byte-for-byte compatible
# with zero migration and zero behavior change. "fbs" and "fcs" are NEW
# scopes (2026-09-15, Bob: "we could have an overall championship belt, a
# FBS belt, FCS belt and conference belts") that additionally require BOTH
# sides of every game to share that one current CFBD classification -- see
# filter_division1_games in belt_engine.py. Those two use belt_engine.py's
# resolve_vacancies()/split_winner_baseline() (gap-aware, vacancy-reverting)
# rather than the plain walk()/split_at_season() below, because an FCS-only
# scope can hit the exact same "small program goes dark for years" problem
# that originally motivated building that machinery for the Losers Belt --
# plain walk() has no way to recover from that and would just freeze.
SCOPES = ("combined", "fbs", "fcs")


def _scope_suffix(scope):
    return "" if scope == "combined" else f"_{scope}"


def baseline_path(scope):
    return os.path.join(HIST_DIR, f"baseline{_scope_suffix(scope)}.json")


def vacancy_path(scope):
    return os.path.join(HIST_DIR, f"vacancies{_scope_suffix(scope)}.json")


def lineage_path(scope):
    return os.path.join(OUT_DIR, f"lineage{_scope_suffix(scope)}.json")


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

    Used by build_losers_lineage.py's own filter_division1_games (Losers
    Belt) AND by this module's own FBS-only/FCS-only championship-belt
    scopes (belt_engine.py's filter_division1_games) to keep those scopes
    restricted to Division 1 programs -- the "combined" scope of EACH belt
    (the original, always-worked-this-way version) applies no restriction
    at all. The per-team classification lets each belt build its FBS-only/
    FCS-only/combined lineages from one shared fetch instead of one flat
    "is D1" bit. This is CFBD's CURRENT classification, applied uniformly
    across all of history, not a season-by-season historical one: the
    FBS/FCS split didn't exist before 1978, so there's no meaningful
    historical classification to apply before then anyway. A school is
    either a Division 1 program today or it isn't (and is either FBS or
    FCS today or it isn't); that's the eligibility bar, regardless of when
    a given game was played.
    """
    teams = _get_json(f"{API_BASE}/teams", api_key, "teams", retries=retries)
    return {t["school"]: t["classification"] for t in teams
            if t.get("classification") in ("fbs", "fcs")}


def fetch_conferences(api_key, retries=8):
    """Every conference CFBD currently classifies as "fbs" or "fcs", as a
    {conference_name: classification} dict -- the seed list
    build_conference_lineage.py loops over to build one belt per
    conference (2026-09-15, Bob: "create belts for all FBS and FCS
    conferences"). A single cheap call (GET /conferences), negligible
    against either belt's call budget.

    Deliberately does NOT include FBS/FCS Independents (schools with no
    conference at all) -- CFBD represents those as a null/absent
    conference on the team/game records, not as a real conference object
    here, and "the Independents belt" wouldn't mean anything under the
    normal both-sides-in-the-same-conference rule anyway (two
    independents playing each other aren't IN a conference together).
    build_conference_lineage.py's own filter already requires both sides'
    per-GAME conference field to be non-null and match, which excludes
    independent-vs-independent and independent-vs-conference games from
    every conference belt the same way -- this function just supplies the
    list of real conferences to iterate.

    CFBD's /conferences response shape has shifted between API versions
    (a "classification" field vs. separate fbs/fcs list endpoints) -- this
    accepts either the unified list-with-classification shape or, if that
    field is missing, falls back to treating every returned conference as
    worth trying (build_conference_lineage.py's own per-game filter is
    what actually enforces correctness either way, so an overly broad
    conference list here just means a mostly-empty belt gets built and
    skipped, not a wrong one)."""
    conferences = _get_json(f"{API_BASE}/conferences", api_key, "conferences", retries=retries)
    out = {}
    for c in conferences:
        name = pick(c, "name")
        classification = pick(c, "classification")
        if not name:
            continue
        if classification in ("fbs", "fcs"):
            out[name] = classification
        elif classification is None:
            # Older/alternate API shape with no classification field at
            # all on the conference object -- keep it and let the per-game
            # filter sort out whether it ever actually has qualifying
            # games, rather than silently dropping a real conference.
            out[name] = "unknown"
    return out


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
            # Conference/division AS OF THIS GAME (CFBD reports these per-game,
            # not just per-team-today) -- used by build_conference_lineage.py
            # to filter "both sides were in conference X at the time" without
            # any extra API call, since realignment means a team's CURRENT
            # conference (from /teams) is often wrong for its older games.
            "home_conference": pick(g, "home_conference", "homeConference"),
            "away_conference": pick(g, "away_conference", "awayConference"),
            "home_division": pick(g, "home_division", "homeDivision", "home_classification", "homeClassification"),
            "away_division": pick(g, "away_division", "awayDivision", "away_classification", "awayClassification"),
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


def compute_team_paths(raw, current_holder, d1_teams, venue_tz=None, venue_info=None):
    """For every current FBS/FCS team, a precomputed answer to "when could
    we get a shot at the belt?" -- 2026-09-16, Bob's wishlist item #1,
    "My team and the path to the belt": "The site already knows the
    holder's remaining schedule and every team's schedule, so it can say
    'Penn State doesn't play Notre Dame this year, but you play Purdue on
    Nov 14 -- if Purdue takes it on Sep 26 and holds it, that's your
    game.' A little scenario tree off the holder's schedule, pruned to
    games my team is in."

    Two kinds of path, both read straight out of this run's already-
    fetched season schedule (`raw` -- see find_upcoming_games above; the
    only NEW cost is the one-time fetch_division1_teams call to get the
    full current D1 team list, already paid elsewhere for the Losers
    Belt/FBS-FCS scopes):

      - direct: the team's own remaining games against the CURRENT
        holder -- "you play them on <date>."
      - indirect (one-hop only, deliberately not chased further): for
        each of the holder's own remaining games (holder vs. some
        opponent O on date d), any LATER game the team has against that
        SAME opponent O -- "if O takes the belt from the holder on d and
        still has it, that's your shot." Whether O actually holds onto
        it that long isn't modeled (that's the job of belt-at-risk odds,
        wishlist item #2) -- the "and holds it" framing in the copy
        itself carries the caveat, matching how Bob described it.

    Returns {team_name: {...}} for every current D1 team plus the holder
    itself (whose own entry just sets is_holder). build_site.py keys this
    by team_slug() when it builds the client-side payload -- kept as
    plain team names here since slugging is build_site.py's job
    everywhere else in this pipeline, not this module's."""
    venue_tz = venue_tz or {}
    venue_info = venue_info or {}
    holder_schedule = find_upcoming_games(raw, current_holder, count=99,
                                           venue_tz=venue_tz, venue_info=venue_info)
    all_teams = sorted(set(d1_teams) | {current_holder})
    out = {}
    for team in all_teams:
        if team == current_holder:
            out[team] = {"is_holder": True, "direct": [], "indirect": []}
            continue
        team_schedule = find_upcoming_games(raw, team, count=99,
                                             venue_tz=venue_tz, venue_info=venue_info)
        direct = [{"date": g["date"], "is_home": g["is_home"]}
                  for g in team_schedule if g["opponent"] == current_holder]
        indirect = []
        for hg in holder_schedule:
            via = hg["opponent"]
            if via == team:
                continue
            for tg in team_schedule:
                if tg["opponent"] == via and tg["date"] > hg["date"]:
                    indirect.append({
                        "via": via,
                        "via_date": hg["date"],
                        "your_date": tg["date"],
                        "your_is_home": tg["is_home"],
                    })
        indirect.sort(key=lambda x: x["your_date"])
        out[team] = {"is_holder": False, "direct": direct, "indirect": indirect}
    return out


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


def load_baseline(scope="combined"):
    path = baseline_path(scope)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_baseline(historical_belt_games, historical_reigns, open_reign, live_start_year, scope="combined"):
    os.makedirs(HIST_DIR, exist_ok=True)
    path = baseline_path(scope)
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


def write_outputs(belt_games, reigns, tie_rule, scope="combined"):
    """Writes lineage JSON to scope's own path (lineage.json for "combined"
    -- unchanged filename/shape from before scopes existed -- lineage_fbs
    .json / lineage_fcs.json for the two new scopes). The belt_games.csv/
    reigns.csv exports are a "combined"-only convenience (unchanged from
    before) -- not worth tripling for the two new, smaller-audience scopes."""
    if scope == "combined":
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
    with open(lineage_path(scope), "w") as f:
        json.dump({
            "generated": time.strftime("%Y-%m-%d"),
            "tie_rule": tie_rule,
            "scope": scope,
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
                   help="ignore historical_data/baseline*.json and rebuild "
                        "the whole 1869-now chain from scratch (~316 CFBD "
                        "calls) instead of the normal incremental update -- "
                        "applies to all three scopes (combined/fbs/fcs)")
    p.add_argument("--bootstrap-championship-scopes", action="store_true",
                   help="do the one-time full historical walk (~316 CFBD "
                        "calls) for the FBS-only/FCS-only championship-belt "
                        "scopes specifically -- required once before "
                        "lineage_fbs.json/lineage_fcs.json ever get built; "
                        "every run after this one is cheap and automatic. "
                        "Without this (and without --full-refetch), a scope "
                        "that has no baseline yet is simply skipped, NOT "
                        "auto-bootstrapped -- unlike 'combined', which is "
                        "already live and always runs.")
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = p.parse_args()

    if not args.key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                 "Free key at https://collegefootballdata.com/key")

    os.makedirs(OUT_DIR, exist_ok=True)

    live_start_year = args.end_year - 1
    today = time.strftime("%Y-%m-%d")

    # "combined" always runs (it's the original, already-live belt, and
    # its own baseline is never missing in practice). "fbs"/"fcs" only run
    # this pass if they already have a baseline (normal incremental
    # update) OR the one-time bootstrap was explicitly requested --
    # otherwise a scope with no baseline is SKIPPED entirely rather than
    # silently triggering an unrequested ~316-call full fetch on some
    # ordinary scheduled run. This mirrors build_losers_lineage.py's own
    # opt-in bootstrap gate, just per-scope instead of all-or-nothing
    # (Losers Belt's "combined" isn't already-live from day one the way
    # this belt's is, so its gate can afford to be all-three-or-nothing).
    scopes_to_run = ["combined"]
    baselines = {"combined": None if args.full_refetch else load_baseline("combined")}
    for scope in ("fbs", "fcs"):
        existing = None if args.full_refetch else load_baseline(scope)
        if existing is not None or args.full_refetch or args.bootstrap_championship_scopes:
            scopes_to_run.append(scope)
            baselines[scope] = existing
        else:
            print(f"[{scope}] {baseline_path(scope)} not found, and the one-time "
                  f"bootstrap wasn't requested -- skipping this scope entirely this "
                  f"run (lineage_{scope}.json won't be (re)written; nothing else is "
                  f"affected). Tick the 'bootstrap_championship_scopes' checkbox on "
                  f"a manual 'Run workflow' to build it once.")

    print("Fetching venue timezones from CFBD...")
    venue_tz, venue_info = collect_venues(args.key)

    # One shared CFBD fetch sized to whichever scope (of the ones actually
    # running this pass) needs the most -- a full 1869-now pull (~316
    # calls) if any of THOSE still needs bootstrapping, otherwise just the
    # live window every running scope's baseline already covers. Each
    # scope below re-filters this SAME raw pull, so running all three
    # costs no more CFBD calls than running just "combined" used to.
    if any(b is None for b in baselines.values()):
        fetch_from = args.start_year
        print(f"At least one scope ({', '.join(scopes_to_run)}) needs the one-time "
              f"full {fetch_from}-{args.end_year} pull from CFBD "
              f"(~{2 * (args.end_year - fetch_from + 1)} calls, shared across "
              f"{'all three' if len(scopes_to_run) == 3 else 'both' if len(scopes_to_run) == 2 else 'it'}).")
    else:
        fetch_from = min(min(b["live_start_year"] for b in baselines.values()), live_start_year)
        seasons = list(range(fetch_from, args.end_year + 1))
        print(f"Baselines found for every scope running this pass ({', '.join(scopes_to_run)}) "
              f"-- fetching only seasons {seasons} fresh (~{2 * len(seasons)} calls).")
    raw = fetch_seasons(range(fetch_from, args.end_year + 1), args.key)
    games_all = normalize(raw, venue_tz)
    print(f"{len(games_all)} completed games in chronological order (any classification)")

    d1_teams = None  # fetched lazily below, only if fbs/fcs actually run this time
    combined_current = None

    for scope in scopes_to_run:
        baseline = baselines[scope]
        new_vacancies = all_vacancies = None

        if scope == "combined":
            games = games_all   # NO Division 1 restriction -- exactly as this always worked
            label = "no restriction (the real belt, as always)"
        else:
            if d1_teams is None:
                print("Fetching current Division 1 (FBS/FCS) team list from CFBD...")
                d1_teams = fetch_division1_teams(args.key)
            games = filter_division1_games(games_all, d1_teams, scope)
            label = {"fbs": "FBS-only", "fcs": "FCS-only"}[scope]

        if baseline is None:
            print(f"\n[{scope}] No historical baseline -- doing a full "
                  f"{args.start_year}-{args.end_year} walk ({label}, "
                  f"{len(games)} of {len(games_all)} games qualify).")
            if scope == "combined":
                belt_games, reigns = walk(games, args.tie_rule)
            else:
                recent_teams = {g["home"] for g in games if g["season"] >= live_start_year} | \
                               {g["away"] for g in games if g["season"] >= live_start_year}
                belt_games, reigns, new_vacancies = resolve_vacancies(
                    games, args.tie_rule, start_holder=None, start_reign=None,
                    recent_teams=recent_teams, today=today)
        else:
            scope_fetch_from = min(baseline["live_start_year"], live_start_year)
            scoped_games = [g for g in games if g["season"] >= scope_fetch_from]
            print(f"\n[{scope}] Baseline found (through season "
                  f"{baseline['live_start_year'] - 1}, "
                  f"{len(baseline['historical_belt_games'])} belt games already settled) "
                  f"-- using {len(scoped_games)} {label} games from season {scope_fetch_from} on")
            if scope == "combined":
                new_belt_games, tail_reigns = walk(
                    scoped_games, args.tie_rule,
                    start_holder=baseline["open_reign"]["team"],
                    start_reign=baseline["open_reign"])
                belt_games = baseline["historical_belt_games"] + new_belt_games
                reigns = baseline["historical_reigns"] + tail_reigns
            else:
                recent_teams = {g["home"] for g in scoped_games if g["season"] >= live_start_year} | \
                               {g["away"] for g in scoped_games if g["season"] >= live_start_year}
                tail_belt_games, tail_reigns, new_vacancies = resolve_vacancies(
                    scoped_games, args.tie_rule,
                    start_holder=baseline["open_reign"]["team"],
                    start_reign=baseline["open_reign"],
                    recent_teams=recent_teams, today=today,
                    context_reigns=baseline["historical_reigns"])
                belt_games = baseline["historical_belt_games"] + tail_belt_games
                reigns = baseline["historical_reigns"] + tail_reigns

        if scope != "combined":
            all_vacancies = engine_load_vacancies(vacancy_path(scope))
            if new_vacancies:
                all_vacancies, changed = merge_vacancies(all_vacancies, new_vacancies)
                if changed:
                    engine_save_json(all_vacancies, vacancy_path(scope), HIST_DIR)
                    print(f"[{scope}] Wrote {vacancy_path(scope)} "
                          f"({len(all_vacancies)} recorded vacancies)")

        write_outputs(belt_games, reigns, args.tie_rule, scope)

        # Advance this scope's baseline to today's live-window boundary.
        if scope == "combined":
            hist_belt_games, hist_reigns, open_reign = split_at_season(
                belt_games, reigns, live_start_year)
        else:
            hist_belt_games, hist_reigns, open_reign = split_winner_baseline(
                belt_games, all_vacancies, reigns[0], live_start_year)
        save_baseline(hist_belt_games, hist_reigns, open_reign, live_start_year, scope)

        current = reigns[-1]
        teams_n = len({r["team"] for r in reigns})
        print(f"[{scope}] current holder: {current['team']} since {current['start_date']} "
              f"({current['defenses']} defenses, {len(belt_games)} belt games, "
              f"{teams_n} distinct teams all-time)")
        if scope == "combined":
            combined_current = current

    # "Belt Watch" (next_game/upcoming_games) stays COMBINED-only -- that's
    # what the homepage shows; the fbs/fcs scopes are Full History/All
    # Games-only for now, no separate homepage of their own.
    #
    # count=99 (not just the 3 "Belt Watch" needs) so the same file also
    # covers the current season page's "remaining schedule" section
    # (wishlist #4, 2026-09-16) -- no new API call, this is still the same
    # already-fetched `raw` season data, just asking for more of it back.
    # generate_homepage's own Belt Watch still only ever renders
    # upcoming_games[1:3], so this is a pure superset -- nothing about the
    # homepage's look changes.
    upcoming_games = find_upcoming_games(raw, combined_current["team"], count=99,
                                          venue_tz=venue_tz, venue_info=venue_info)
    next_game = upcoming_games[0] if upcoming_games else None
    with open(os.path.join(OUT_DIR, "next_game.json"), "w") as f:
        json.dump(next_game, f, indent=2)
    with open(os.path.join(OUT_DIR, "upcoming_games.json"), "w") as f:
        json.dump(upcoming_games, f, indent=2)
    if next_game:
        side = "vs." if next_game["is_home"] else "at"
        print(f"\nNext game: {combined_current['team']} {side} {next_game['opponent']} "
              f"on {next_game['date']} ({len(upcoming_games)} game(s) in the "
              f"Belt Watch lookahead)")
    else:
        print("\nNo upcoming game found for the current holder in the fetched "
              "window (schedule not out yet, or the season's over).")

    # "My team and the path to the belt" (wishlist #1) -- every current D1
    # team's precomputed direct/indirect shot at the CURRENT holder, off
    # this run's already-fetched season schedule. d1_teams may already be
    # loaded (fbs/fcs scopes fetch it lazily above); fetch it here if this
    # run only did the combined scope.
    if d1_teams is None:
        print("Fetching current Division 1 (FBS/FCS) team list from CFBD...")
        d1_teams = fetch_division1_teams(args.key)
    team_paths = compute_team_paths(raw, combined_current["team"], d1_teams,
                                     venue_tz=venue_tz, venue_info=venue_info)
    with open(os.path.join(OUT_DIR, "team_paths.json"), "w") as f:
        json.dump({"holder": combined_current["team"], "teams": team_paths}, f, indent=2)
    n_direct = sum(1 for t in team_paths.values() if t["direct"])
    n_indirect = sum(1 for t in team_paths.values() if not t["direct"] and t["indirect"])
    print(f"Wrote team_paths.json: {len(team_paths)} teams, {n_direct} with a direct "
          f"shot on the schedule, {n_indirect} more with only an indirect one")

    print(f"""
Lineage built -> {OUT_DIR}/
  combined current holder  {combined_current['team']} since {combined_current['start_date']}
  ({combined_current['defenses']} defenses)

Expected as of 2026-09-13: Notre Dame, since 2025-11-29, 2 defenses.
A large divergence from the reference totals usually means a ruleset
difference -- the tie rule is the most common culprit.""")


if __name__ == "__main__":
    main()
