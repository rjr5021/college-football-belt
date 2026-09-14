#!/usr/bin/env python3
"""
Fetch real play-by-play for every belt game since 2003, trimmed down to the
plays that actually mattered -- scores, turnovers, and explosive gains --
for two uses: a "Key Plays" list on each game's own page, and real material
for generate_recaps.py's AI-written recap (instead of it having to guess at
what happened from box-score totals alone).

Usage:
    export CFBD_API_KEY=your_key_here
    python3 fetch_game_plays.py                # reads belt_data/lineage.json
    python3 fetch_game_plays.py --full-refetch  # ignore the historical
                                                 # cache, redo every season

Output (into ./belt_data/, regenerated fresh every run, not committed):
    game_plays.json   one entry per belt game, keyed by game_id (as a
                      string): a list of "notable" plays, each:
        { "period": 4, "clock": "2:15", "offense": "Notre Dame",
          "defense": "Stanford", "down": 1, "distance": 10,
          "yards_gained": 84, "play_type": "Passing Touchdown",
          "play_text": "J. Burnham pass complete to L. Talich for 84 yds...",
          "scoring": true }
      or null if CFBD had no play-by-play for that game.

WHY ONLY "NOTABLE" PLAYS, NOT EVERY PLAY: a single game can have 150+ plays.
Committing the full play-by-play for every belt game since 2003 would bloat
the repo (this is a git-committed historical cache, same reasoning as
team_stats.json/player_stats.json) and blow way past what's useful in an AI
prompt anyway. "Notable" here means: any scoring play, any turnover (any
play whose type mentions Interception, Fumble, Safety, or Blocked), or any
play that gained at least NOTABLE_YARDS_THRESHOLD net yards. That's plenty
to reconstruct which drives mattered and how they ended.

IMPORTANT DATA LIMIT, same as fetch_game_details.py: CFBD's play-by-play
only goes back to roughly 2003, matching the box-score cutoff.

IMPORTANT API SHAPE, NOT A BUG: unlike /games/teams and /games/players,
CFBD's /plays endpoint does NOT accept a game id -- it requires year+week
(+ team to scope it to one specific game). See STATS_MIN_SEASON note above;
this is confirmed against CFBD's own OpenAPI docs. So this script fetches
by (season, week, season_type, home_team) -- one call per belt game, same
cost profile as the other two per-game detail calls.

Incremental fetching: identical committed-cache pattern as
fetch_game_details.py -- historical_data/game_plays.json holds every
settled season's notable plays, and only the current + previous season is
refetched each run.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.collegefootballdata.com"
OUT_DIR = "belt_data"
HIST_DIR = "historical_data"
STATS_MIN_SEASON = 2003
NOTABLE_YARDS_THRESHOLD = 20  # net yards gained (or lost, for sacks/TFLs) to count as "big"
TURNOVER_MARKERS = ("interception", "fumble", "safety", "blocked", "turnover")


def pick(d, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _get_json(url, api_key, label, retries=8):
    """Same patient 429 backoff as fetch_game_details.py -- CFBD's rate-limit
    windows can take a while to clear."""
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
                print(f"  429 (rate limited) on {label}, waiting {wait}s "
                      f"(attempt {attempt+1}/{retries})...", file=sys.stderr)
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


def clock_str(clock):
    if not isinstance(clock, dict):
        return None
    m = pick(clock, "minutes", default=0) or 0
    s = pick(clock, "seconds", default=0) or 0
    return f"{m}:{s:02d}"


def is_notable(play):
    if pick(play, "scoring", default=False):
        return True
    play_type = (pick(play, "play_type", "playType", default="") or "").lower()
    if any(marker in play_type for marker in TURNOVER_MARKERS):
        return True
    yards = pick(play, "yards_gained", "yardsGained", default=0) or 0
    if abs(yards) >= NOTABLE_YARDS_THRESHOLD:
        return True
    return False


def trim_play(play):
    return {
        "period": pick(play, "period"),
        "clock": clock_str(pick(play, "clock")),
        "offense": pick(play, "offense"),
        "defense": pick(play, "defense"),
        "down": pick(play, "down"),
        "distance": pick(play, "distance"),
        "yards_gained": pick(play, "yards_gained", "yardsGained"),
        "play_type": pick(play, "play_type", "playType"),
        "play_text": pick(play, "play_text", "playText"),
        "scoring": bool(pick(play, "scoring", default=False)),
    }


def fetch_plays_for_game(season, week, season_type, team, api_key):
    """One call: year+week+team scopes CFBD's /plays down to exactly this
    team's one game that week (a team plays at most once per week)."""
    params = {
        "year": season, "week": week, "seasonType": season_type or "regular",
        "team": team,
    }
    url = f"{API_BASE}/plays?{urllib.parse.urlencode(params)}"
    rows = _get_json(url, api_key, f"plays year={season} week={week} team={team}")
    return [trim_play(p) for p in rows if is_notable(p)]


def _collect_generic(games, api_key, cache_filename, label):
    """Resumable-cache fetch loop, same shape as fetch_game_details.py's
    _collect_generic -- cache keyed by str(game_id) -> list of notable
    plays (or null), saved to disk every 10 games and on exit."""
    cache_path = os.path.join(OUT_DIR, cache_filename)
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)
        print(f"Resuming from cached {cache_path}: {len(cache)} game(s) already fetched")

    remaining = [g for g in games if str(g["game_id"]) not in cache]

    def save_cache():
        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(cache, f)
        os.replace(tmp_path, cache_path)

    if not remaining:
        print(f"All games already cached in {cache_path} -- nothing new to fetch.")
        return cache

    print(f"{label}: {len(remaining)} of {len(games)} game(s) still needed "
          f"({len(games) - len(remaining)} already cached)...")

    try:
        for i, g in enumerate(remaining, 1):
            plays = fetch_plays_for_game(g["season"], g["week"], g.get("season_type"),
                                          g["home"], api_key)
            cache[str(g["game_id"])] = plays if plays or plays == [] else None
            if i % 10 == 0 or i == len(remaining):
                save_cache()
                print(f"  {i}/{len(remaining)} fetched this run "
                      f"({len(cache)}/{len(games)} total) -- saved to {cache_path}")
            time.sleep(0.12)
    finally:
        save_cache()

    print(f"Cached {len(cache)} games' {label} to {cache_path}")
    return cache


def load_historical_cache():
    path = os.path.join(HIST_DIR, "game_plays.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_historical_cache(cache):
    os.makedirs(HIST_DIR, exist_ok=True)
    path = os.path.join(HIST_DIR, "game_plays.json")
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    print(f"Wrote {path} ({len(cache)} settled games' notable plays frozen)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lineage", default=os.path.join(OUT_DIR, "lineage.json"))
    p.add_argument("--full-refetch", action="store_true",
                   help="ignore historical_data/game_plays.json and redo "
                        "every 2003+ belt game's plays from scratch")
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = p.parse_args()

    if not args.key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                 "Free key at https://collegefootballdata.com/key")

    if not os.path.exists(args.lineage):
        sys.exit(f"Can't find {args.lineage} -- run build_lineage.py first.")

    with open(args.lineage) as f:
        lineage = json.load(f)
    belt_games = lineage["belt_games"]
    stats_games = [g for g in belt_games if g["season"] >= STATS_MIN_SEASON]
    print(f"{len(belt_games)} belt games in the lineage, "
          f"{len(stats_games)} from {STATS_MIN_SEASON} onward have play-by-play available")

    os.makedirs(OUT_DIR, exist_ok=True)
    live_start_year = time.localtime().tm_year - 1

    if args.full_refetch:
        print(f"--full-refetch: fetching all {len(stats_games)} belt game(s)' "
              f"plays from {STATS_MIN_SEASON} onward (one call each)")
        combined = _collect_generic(stats_games, args.key, "game_plays_raw.json", "plays")
        historical_ids = {g["game_id"] for g in stats_games if g["season"] < live_start_year}
    else:
        historical_games = [g for g in stats_games if g["season"] < live_start_year]
        live_games = [g for g in stats_games if g["season"] >= live_start_year]
        historical_ids = {g["game_id"] for g in historical_games}

        historical_cache = load_historical_cache()
        missing = [g for g in historical_games if str(g["game_id"]) not in historical_cache]
        to_fetch_now = missing + live_games
        print(f"plays: {len(historical_games)} belt game(s) settled (season < "
              f"{live_start_year}) -- {len(missing)} not yet in the historical cache. "
              f"{len(live_games)} belt game(s) in the live window -- always refetched. "
              f"Fetching {len(to_fetch_now)} of {len(stats_games)} total this run.")

        fetched = _collect_generic(to_fetch_now, args.key, "game_plays_raw.json", "plays")
        combined = dict(historical_cache)
        combined.update(fetched)

    new_historical_cache = {str(gid): combined.get(str(gid)) for gid in historical_ids}
    save_historical_cache(new_historical_cache)

    out_path = os.path.join(OUT_DIR, "game_plays.json")
    out = {str(g["game_id"]): combined.get(str(g["game_id"])) for g in belt_games}
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)

    with_plays = sum(1 for v in out.values() if v)
    print(f"\nWrote {out_path}")
    print(f"  {with_plays} of {len(belt_games)} belt games have notable-play data")


if __name__ == "__main__":
    main()
