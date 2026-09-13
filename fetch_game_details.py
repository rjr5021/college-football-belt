#!/usr/bin/env python3
"""
Fetch box-score detail for every belt game, keyed by CFBD game id, for the
site's game detail pages (quarter-by-quarter line score + full team stats).

Usage:
    export CFBD_API_KEY=your_key_here      # same key as the other scripts
    python3 fetch_game_details.py                       # reads belt_data/lineage.json

Output (into ./belt_data/):
    team_stats_raw.json   raw /games/teams pulls, keyed by game_id (as a
                          string) -> that game's row (or null). Saved
                          incrementally every 10 games, so a crash or a
                          rate-limit (HTTP 429) never loses progress -- just
                          rerun the script and it picks up where it left off.
    game_details.json     one entry per belt game, keyed by game_id (as a string):
        { "401754614": {
              "date": "2025-11-29", "season": 2025, "week": 14,
              "home": "Stanford", "away": "Notre Dame", "score": "20-49",
              "line_score": {"home": [0,7,7,6], "away": [7,14,14,14]} or null,
              "team_stats": {
                  "home": {"team": "Stanford", "stats": {"totalYards": "312", ...}},
                  "away": {"team": "Notre Dame", "stats": {"totalYards": "441", ...}}
              } or null
          }, ... }

IMPORTANT DATA LIMIT, not a bug: CFBD only has line scores and team box-score
stats from roughly 2003 onward. Checked directly against this project's own
belt_data/games_raw.json: 0 of the games before 2001 have a line score, vs.
~100% from 2003 on. That means only about 19% of all 1,633 belt games (the
ones from 2003 onward) can ever get a full box score here -- the other 81%
(1869-2002) will only ever have the final score, because CFBD's own data
doesn't go deeper than that for those eras. Surface this on the site rather
than hide it (e.g. no "box score" link/section at all for older games,
instead of an empty or broken one).

Line scores are read straight out of belt_data/games_raw.json (already
fetched by build_lineage.py) -- no extra API calls needed for those. Full
team stats need a separate /games/teams pull -- one call per belt game
that's 2003 or later (~310 calls, via CFBD's `id` filter), not a scan of
whole seasons, so this should be a quick run even on a fresh key.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.collegefootballdata.com"
OUT_DIR = "belt_data"
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


def collect_team_stats(game_ids, api_key):
    """Cache is a dict keyed by str(game_id) -> that game's /games/teams row
    (or null if CFBD had nothing for it), saved to disk every 10 games (and
    on any exit, including a crash) via atomic temp-file-then-rename writes.
    A rerun loads whatever's cached and only fetches games still missing --
    so a 429 mid-run, or the user closing the terminal, never throws away
    progress already made."""
    cache_path = os.path.join(OUT_DIR, "team_stats_raw.json")
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
        print("All games already cached -- nothing new to fetch.")
        return cache

    print(f"{len(remaining)} of {len(game_ids)} game(s) still needed "
          f"({len(game_ids) - len(remaining)} already cached)...")

    try:
        for i, gid in enumerate(remaining, 1):
            rows = fetch_team_stats_for_game(gid, api_key)
            cache[str(gid)] = rows[0] if rows else None
            if i % 10 == 0 or i == len(remaining):
                save_cache()
                print(f"  {i}/{len(remaining)} fetched this run "
                      f"({len(cache)}/{len(game_ids)} total) -- saved to {cache_path}")
            time.sleep(0.12)
    finally:
        # Always persist whatever we got, even on a crash or Ctrl-C.
        save_cache()

    print(f"Cached {len(cache)} games' team stats to {cache_path}")
    return cache


def build_team_stats_index(raw_cache):
    """game_id -> {"home": {...}, "away": {...}}. raw_cache is the dict from
    collect_team_stats: str(game_id) -> row (or None)."""
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
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = p.parse_args()

    if not args.key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                 "Free key at https://collegefootballdata.com/key")

    if not os.path.exists(args.lineage):
        sys.exit(f"Can't find {args.lineage} -- run build_lineage.py first.")
    if not os.path.exists(args.games_raw):
        sys.exit(f"Can't find {args.games_raw} -- run build_lineage.py first "
                 f"(it should have created this as a side effect).")

    with open(args.lineage) as f:
        lineage = json.load(f)
    belt_games = lineage["belt_games"]
    print(f"{len(belt_games)} belt games in the lineage")

    with open(args.games_raw) as f:
        games_raw = json.load(f)
    line_scores = build_line_score_index(games_raw)

    stats_game_ids = {g["game_id"] for g in belt_games if g["season"] >= STATS_MIN_SEASON}
    print(f"{len(stats_game_ids)} belt game(s) from {STATS_MIN_SEASON} onward -- "
          f"fetching team stats for those only (one call each)")

    os.makedirs(OUT_DIR, exist_ok=True)
    team_stats_raw = collect_team_stats(stats_game_ids, args.key)
    team_stats = build_team_stats_index(team_stats_raw)

    details = {}
    with_line = with_stats = 0
    for g in belt_games:
        gid = g["game_id"]
        entry = {
            "date": g["date"], "season": g["season"], "week": g["week"],
            "home": g["home"], "away": g["away"], "score": g["score"],
            "line_score": line_scores.get(gid),
            "team_stats": team_stats.get(gid),
        }
        if entry["line_score"] is not None:
            with_line += 1
        if entry["team_stats"] is not None:
            with_stats += 1
        details[str(gid)] = entry

    out_path = os.path.join(OUT_DIR, "game_details.json")
    with open(out_path, "w") as f:
        json.dump(details, f, indent=2, sort_keys=True)

    pct_line = 100 * with_line / len(belt_games)
    pct_stats = 100 * with_stats / len(belt_games)
    print(f"\nWrote {out_path}")
    print(f"  {with_line} of {len(belt_games)} belt games ({pct_line:.1f}%) have a "
          f"quarter-by-quarter line score")
    print(f"  {with_stats} of {len(belt_games)} belt games ({pct_stats:.1f}%) have "
          f"a full team box score")
    print(f"  The remaining ~{100-pct_stats:.0f}% (pre-{STATS_MIN_SEASON}) will only "
          f"ever have the final score -- that's a CFBD data-coverage limit, not "
          f"something a rerun fixes.")


if __name__ == "__main__":
    main()
