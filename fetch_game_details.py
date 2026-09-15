#!/usr/bin/env python3
"""
Fetch box-score detail for every belt game, keyed by CFBD game id, for the
site's game detail pages (quarter-by-quarter line score + full team stats +
full player stats).

Usage:
    export CFBD_API_KEY=your_key_here      # same key as the other scripts
    python3 fetch_game_details.py                       # reads belt_data/lineage.json
    python3 fetch_game_details.py --full-refetch         # ignore the
                                                          # historical cache,
                                                          # redo every season

Output (into ./belt_data/, regenerated fresh every run, not committed):
    team_stats_raw.json    this run's LIVE-window /games/teams pulls (see
                           "Incremental fetching" below), keyed by game_id
                           (as a string) -> that game's row (or null).
    player_stats_raw.json  same idea, for /games/players.
    game_details.json     one entry per belt game, keyed by game_id (as a string):
        { "401754614": {
              "date": "2025-11-29", "season": 2025, "week": 14,
              "home": "Stanford", "away": "Notre Dame", "score": "20-49",
              "line_score": {"home": [0,7,7,6], "away": [7,14,14,14]} or null,
              "team_stats": {
                  "home": {"team": "Stanford", "stats": {"totalYards": "312", ...}},
                  "away": {"team": "Notre Dame", "stats": {"totalYards": "441", ...}}
              } or null,
              "player_stats": {
                  "passing": [{"player": "T. Smith", "player_id": "4567890",
                                "team": "Stanford",
                                "stats": {"C/ATT": "18/29", "YDS": "245", ...}}, ...],
                  "rushing": [...], "receiving": [...],
                  "defensive": [...], "kicking": [...], ...
              } or null  -- player_id is CFBD's own athlete id (a string), used
                            to link a player to their own page across every
                            belt game they appear in; null for the rare
                            athlete CFBD itself didn't tag with one
          }, ... }

IMPORTANT DATA LIMIT, not a bug: CFBD only has line scores, team box-score
stats, and player stats from roughly 2003 onward. That means only about 19%
of all belt games (the ones from 2003 onward) can ever get a full box score
here -- the other 81% (1869-2002) will only ever have the final score.

Line scores are read straight out of belt_data/games_raw.json (already
fetched by build_lineage.py) -- no extra API calls needed for those. Full
team stats need a separate /games/teams pull, and full player stats a
separate /games/players pull -- one call each per belt game (two calls total
per 2003+ belt game, not one).

Incremental fetching: a belt game's box score, once played, never changes --
so once a season is no longer "current or previous", there's no reason to
ever re-pull its stats. This script keeps small, git-committed historical
caches (./historical_data/team_stats.json and player_stats.json) of every
settled season's stats, and each run only calls CFBD for belt games in the
current + previous season (a handful of calls, not ~310 x2) -- the same fix,
and the same reason (CFBD's free tier is 1,000 calls/MONTH, not a short
burst limit), as build_lineage.py.
`--full-refetch` ignores the cache and redoes every 2003+ belt game, useful
for a genuine from-scratch rebuild.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.collegefootballdata.com"
OUT_DIR = "belt_data"          # ephemeral, regenerated every run, gitignored
HIST_DIR = "historical_data"   # small, git-committed cache lives here
STATS_MIN_SEASON = 2003  # confirmed empirically -- see module docstring


def pick(d, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _get_json(url, api_key, label, retries=8):
    """429s need a longer, more patient backoff than a generic 5xx --
    CFBD's rate-limit windows can take a while to clear, and giving up
    after a handful of seconds just means restarting the whole run.
    5xx/network errors still use a quick exponential backoff; 429s use
    a steeper one with a much higher cap (up to 60s per wait)."""
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


def fetch_team_stats_for_game(game_id, api_key):
    """CFBD's /games/teams rejects year-only queries (400 Bad Request) --
    it needs year+week/team/conference, or a single game `id`. A specific
    id is simplest here since we already know exactly which games we want."""
    url = f"{API_BASE}/games/teams?id={game_id}"
    return _get_json(url, api_key, f"games/teams id={game_id}")


def fetch_player_stats_for_game(game_id, api_key):
    """Same idea as fetch_team_stats_for_game, against /games/players."""
    url = f"{API_BASE}/games/players?id={game_id}"
    return _get_json(url, api_key, f"games/players id={game_id}")


def _collect_generic(game_ids, api_key, cache_filename, fetch_one, label):
    """Shared resumable-cache fetch loop used for both /games/teams and
    /games/players -- cache is a dict keyed by str(game_id) -> that game's
    raw row (or null if CFBD had nothing for it), saved to disk every 10
    games (and on any exit, including a crash) via atomic temp-file-then-
    rename writes. A rerun loads whatever's cached and only fetches games
    still missing -- so a 429 mid-run, or the user closing the terminal,
    never throws away progress already made."""
    cache_path = os.path.join(OUT_DIR, cache_filename)
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)
        print(f"Resuming from cached {cache_path}: {len(cache)} game(s) already fetched")

    game_ids = sorted(game_ids)
    remaining = [gid for gid in game_ids if str(gid) not in cache]

    def save_cache():
        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(cache, f)
        os.replace(tmp_path, cache_path)

    if not remaining:
        print(f"All games already cached in {cache_path} -- nothing new to fetch.")
        return cache

    print(f"{label}: {len(remaining)} of {len(game_ids)} game(s) still needed "
          f"({len(game_ids) - len(remaining)} already cached)...")

    try:
        for i, gid in enumerate(remaining, 1):
            rows = fetch_one(gid, api_key)
            cache[str(gid)] = rows[0] if rows else None
            if i % 10 == 0 or i == len(remaining):
                save_cache()
                print(f"  {i}/{len(remaining)} fetched this run "
                      f"({len(cache)}/{len(game_ids)} total) -- saved to {cache_path}")
            time.sleep(0.12)
    finally:
        # Always persist whatever we got, even on a crash or Ctrl-C.
        save_cache()

    print(f"Cached {len(cache)} games' {label} to {cache_path}")
    return cache


def collect_team_stats(game_ids, api_key):
    return _collect_generic(game_ids, api_key, "team_stats_raw.json",
                             fetch_team_stats_for_game, "team stats")


def collect_player_stats(game_ids, api_key):
    return _collect_generic(game_ids, api_key, "player_stats_raw.json",
                             fetch_player_stats_for_game, "player stats")


def load_historical_cache(filename):
    path = os.path.join(HIST_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_historical_cache(filename, cache, label):
    os.makedirs(HIST_DIR, exist_ok=True)
    path = os.path.join(HIST_DIR, filename)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    print(f"Wrote {path} ({len(cache)} settled games' {label} frozen)")


def load_historical_team_stats():
    return load_historical_cache("team_stats.json")


def save_historical_team_stats(cache):
    save_historical_cache("team_stats.json", cache, "stats")


def load_historical_player_stats():
    return load_historical_cache("player_stats.json")


def save_historical_player_stats(cache):
    save_historical_cache("player_stats.json", cache, "player stats")


def build_team_stats_index(raw_cache):
    """game_id -> {"home": {...}, "away": {...}}. raw_cache is a dict from
    str(game_id) -> row (or None)."""
    index = {}
    for row in raw_cache.values():
        if row is None:
            continue
        gid = pick(row, "id")
        teams = pick(row, "teams", default=[]) or []
        if gid is None or len(teams) != 2:
            continue
        entry = {}
        for t in teams:
            side = pick(t, "home_away", "homeAway")
            if side not in ("home", "away"):
                continue
            stats = {}
            for s in pick(t, "stats", default=[]) or []:
                cat = pick(s, "category")
                val = pick(s, "stat")
                if cat is not None:
                    stats[cat] = val
            entry[side] = {"team": pick(t, "school", "team"), "stats": stats}
        if "home" in entry and "away" in entry:
            index[gid] = entry
    return index


def build_player_stats_index(raw_cache):
    """game_id -> {category_name: {"columns": [type names...],
    "rows": [{"player":, "player_id":, "team":, "home_away":, "stats": {type: value}}, ...]}}.
    player_id is CFBD's own athlete id (used to link a player to their own
    page across every belt game they appear in) -- null on the rare athlete
    CFBD itself didn't tag with one.

    CFBD's own shape is type-major (team -> categories -> types -> athletes,
    with one stat value per athlete per type) -- this pivots it to
    player-major (one row per player, one column per stat type) since
    that's what an actual box score table looks like, and combines both
    teams into one table per category so they're easy to compare side by
    side, the way a real box score does."""
    index = {}
    for row in raw_cache.values():
        if row is None:
            continue
        gid = pick(row, "id")
        teams = pick(row, "teams", default=[]) or []
        if gid is None:
            continue

        categories = {}  # cat_name -> {"columns": [...], "players": {(team,name): row}}
        for t in teams:
            team_name = pick(t, "team", "school")
            side = pick(t, "home_away", "homeAway")
            for cat in pick(t, "categories", default=[]) or []:
                cat_name = pick(cat, "name")
                if not cat_name:
                    continue
                bucket = categories.setdefault(cat_name, {"columns": [], "players": {}})
                for typ in pick(cat, "types", default=[]) or []:
                    type_name = pick(typ, "name")
                    if not type_name:
                        continue
                    if type_name not in bucket["columns"]:
                        bucket["columns"].append(type_name)
                    for ath in pick(typ, "athletes", default=[]) or []:
                        player_name = pick(ath, "name")
                        if not player_name:
                            continue
                        athlete_id = pick(ath, "id")
                        key = (team_name, player_name)
                        prow = bucket["players"].setdefault(key, {
                            "player": player_name, "player_id": athlete_id,
                            "team": team_name,
                            "home_away": side, "stats": {},
                        })
                        prow["stats"][type_name] = pick(ath, "stat")

        if categories:
            index[gid] = {cat_name: {"columns": bucket["columns"],
                                      "rows": list(bucket["players"].values())}
                          for cat_name, bucket in categories.items()}
    return index


def build_line_score_index(games_raw):
    """game_id -> {"home": [q1,q2,...], "away": [...]}"""
    index = {}
    for g in games_raw:
        gid = pick(g, "id")
        hl = pick(g, "home_line_scores", "homeLineScores")
        al = pick(g, "away_line_scores", "awayLineScores")
        if gid is not None and hl and al:
            index[gid] = {"home": hl, "away": al}
    return index


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lineage", default=os.path.join(OUT_DIR, "lineage.json"))
    p.add_argument("--games-raw", default=os.path.join(OUT_DIR, "games_raw.json"))
    p.add_argument("--full-refetch", action="store_true",
                   help="ignore historical_data/team_stats.json and "
                        "player_stats.json and redo every 2003+ belt game's "
                        "box score from scratch")
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = p.parse_args()

    if not args.key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                 "Free key at https://collegefootballdata.com/key")

    if not os.path.exists(args.lineage):
        sys.exit(f"Can't find {args.lineage} -- run build_lineage.py first.")
    if not os.path.exists(args.games_raw):
        sys.exit(f"Can't find {args.games_raw} -- run build_lineage.py first "
                 f"(it should have created this as a side effect). Note: "
                 f"games_raw.json now only covers build_lineage.py's own "
                 f"live-window seasons -- that's expected and enough for "
                 f"line scores, since only recent belt games need them fresh.")

    with open(args.lineage) as f:
        lineage = json.load(f)
    belt_games = lineage["belt_games"]
    print(f"{len(belt_games)} belt games in the lineage")

    with open(args.games_raw) as f:
        games_raw = json.load(f)
    line_scores = build_line_score_index(games_raw)

    season_by_gid = {g["game_id"]: g["season"] for g in belt_games}
    all_stats_ids = {gid for gid, season in season_by_gid.items()
                      if season >= STATS_MIN_SEASON}
    live_start_year = time.localtime().tm_year - 1

    os.makedirs(OUT_DIR, exist_ok=True)

    def fetch_incrementally(collect_fn, load_hist_fn, save_hist_fn, label):
        """Shared incremental-vs-full-refetch flow for one stat kind (team
        or player stats) -- identical logic, just parameterized by which
        collect/load/save functions to call, so team and player stats stay
        perfectly in sync instead of two copies silently drifting apart."""
        if args.full_refetch:
            print(f"--full-refetch: fetching all {len(all_stats_ids)} belt "
                  f"game(s)' {label} from {STATS_MIN_SEASON} onward (one call each)")
            return collect_fn(all_stats_ids, args.key)

        historical_ids = {gid for gid in all_stats_ids
                           if season_by_gid[gid] < live_start_year}
        live_ids = all_stats_ids - historical_ids

        historical_cache = load_hist_fn()  # keys are strings (from JSON)
        missing_from_historical = {gid for gid in historical_ids
                                     if str(gid) not in historical_cache}
        to_fetch_now = missing_from_historical | live_ids
        print(f"{label}: {len(historical_ids)} belt game(s) settled (season < "
              f"{live_start_year}) -- {len(missing_from_historical)} not yet "
              f"in the historical cache. {len(live_ids)} belt game(s) in the "
              f"live window (season >= {live_start_year}) -- always refetched. "
              f"Fetching {len(to_fetch_now)} of {len(all_stats_ids)} total this run.")

        fetched = collect_fn(to_fetch_now, args.key)
        combined = dict(historical_cache)
        combined.update(fetched)

        new_historical_cache = {str(gid): combined.get(str(gid)) for gid in historical_ids}
        save_hist_fn(new_historical_cache)
        return combined

    team_raw = fetch_incrementally(collect_team_stats, load_historical_team_stats,
                                    save_historical_team_stats, "team stats")
    player_raw = fetch_incrementally(collect_player_stats, load_historical_player_stats,
                                      save_historical_player_stats, "player stats")

    team_stats = build_team_stats_index(team_raw)
    player_stats = build_player_stats_index(player_raw)

    details = {}
    with_line = with_stats = with_players = 0
    for g in belt_games:
        gid = g["game_id"]
        entry = {
            "date": g["date"], "season": g["season"], "week": g["week"],
            "home": g["home"], "away": g["away"], "score": g["score"],
            "line_score": line_scores.get(gid),
            "team_stats": team_stats.get(gid),
            "player_stats": player_stats.get(gid),
        }
        if entry["line_score"] is not None:
            with_line += 1
        if entry["team_stats"] is not None:
            with_stats += 1
        if entry["player_stats"] is not None:
            with_players += 1
        details[str(gid)] = entry

    out_path = os.path.join(OUT_DIR, "game_details.json")
    with open(out_path, "w") as f:
        json.dump(details, f, indent=2, sort_keys=True)

    pct_line = 100 * with_line / len(belt_games)
    pct_stats = 100 * with_stats / len(belt_games)
    pct_players = 100 * with_players / len(belt_games)
    print(f"\nWrote {out_path}")
    print(f"  {with_line} of {len(belt_games)} belt games ({pct_line:.1f}%) have a "
          f"quarter-by-quarter line score")
    print(f"  {with_stats} of {len(belt_games)} belt games ({pct_stats:.1f}%) have "
          f"a full team box score")
    print(f"  {with_players} of {len(belt_games)} belt games ({pct_players:.1f}%) have "
          f"full player stats")
    print(f"  The remaining ~{100-pct_stats:.0f}% (pre-{STATS_MIN_SEASON}) will only "
          f"ever have the final score -- that's a CFBD data-coverage limit, not "
          f"something a rerun fixes.")


if __name__ == "__main__":
    main()
