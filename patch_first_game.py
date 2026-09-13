#!/usr/bin/env python3
"""
One-time fix: add the very first game (Rutgers 6, Princeton 4, November 6,
1869 -- the game that put the belt up for grabs in the first place) to the
committed historical baseline, so it gets a real game page and shows up on
the All Games / Full History pages like every other belt game.

Why it was missing: build_lineage.py's walk() always treated the bootstrap
game as establishing reigns[0] but never recorded it as a belt_games entry
(there's no "defending champion" for it to be a defense of). That's been
fixed for any FUTURE from-scratch rebuild, but historical_data/baseline.json
was already frozen from an earlier run made before the fix, and a full
--full-refetch to regenerate it from scratch would cost ~316 CFBD calls just
to fix one record. This script instead makes exactly ONE call (year=1869)
to get that single game's real CFBD id, and patches the existing baseline
in place -- so the git-committed history and the current reign are otherwise
untouched.

Usage:
    export CFBD_API_KEY=your_key_here
    python3 patch_first_game.py

Safe to run more than once -- it checks whether the game is already in the
baseline before adding it again.

After running this, just run build_lineage.py normally (no --full-refetch
needed) to regenerate belt_data/lineage.json from the patched baseline, then
fetch_game_details.py and build_site.py as usual (or just update_all.py).
"""

import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.collegefootballdata.com"
HIST_DIR = "historical_data"
FIRST_GAME_DATE = "1869-11-06"


def pick(d, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def get_json(url, api_key):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def main():
    api_key = os.environ.get("CFBD_API_KEY")
    if not api_key:
        sys.exit("No API key. Set CFBD_API_KEY. Free key at "
                 "https://collegefootballdata.com/key")

    baseline_path = os.path.join(HIST_DIR, "baseline.json")
    if not os.path.exists(baseline_path):
        sys.exit(f"Can't find {baseline_path} -- run this from the project folder.")

    with open(baseline_path) as f:
        baseline = json.load(f)

    already = [g for g in baseline["historical_belt_games"] if g.get("outcome") == "established"]
    if already:
        print(f"Already patched -- {baseline_path} has an 'established' belt "
              f"game (game_id {already[0]['game_id']}). Nothing to do.")
        return

    print("Fetching year=1869 (regular season) from CFBD -- 1 API call...")
    rows = get_json(f"{API_BASE}/games?year=1869&seasonType=regular", api_key)

    match = None
    for g in rows:
        raw_date = pick(g, "start_date", "startDate", default="")
        home = pick(g, "home_team", "homeTeam")
        away = pick(g, "away_team", "awayTeam")
        if raw_date.startswith(FIRST_GAME_DATE) and {home, away} == {"Rutgers", "Princeton"}:
            match = g
            break

    if match is None:
        sys.exit(f"Couldn't find the {FIRST_GAME_DATE} Rutgers-Princeton game in "
                  f"CFBD's 1869 data ({len(rows)} games returned). Nothing changed.")

    home = pick(match, "home_team", "homeTeam")
    away = pick(match, "away_team", "awayTeam")
    hp = int(pick(match, "home_points", "homePoints"))
    ap = int(pick(match, "away_points", "awayPoints"))
    gid = pick(match, "id")

    if (hp, ap) != (6, 4) and (hp, ap) != (4, 6):
        print(f"WARNING: expected a 6-4 final, CFBD has {home} {hp} - {away} {ap}. "
              f"Using CFBD's real numbers below rather than the assumed 6-4 -- "
              f"double check the site once it's rebuilt.", file=sys.stderr)

    holder = home if hp > ap else away
    opponent = away if holder == home else home

    established_game = {
        "date": FIRST_GAME_DATE,
        "season": pick(match, "season"),
        "week": pick(match, "week"),
        "season_type": pick(match, "season_type", "seasonType", default="regular"),
        "holder": None,
        "opponent": opponent,
        "home": home,
        "away": away,
        "score": f"{hp}-{ap}",
        "neutral": bool(pick(match, "neutral_site", "neutralSite", default=False)),
        "outcome": "established",
        "new_holder": holder,
        "game_id": gid,
    }

    print(f"Found it: game_id={gid}, {away} at {home}, final {hp}-{ap} -- "
          f"{holder} establishes the belt.")

    baseline["historical_belt_games"].insert(0, established_game)

    with open(baseline_path, "w") as f:
        json.dump(baseline, f, indent=2)

    print(f"\nPatched {baseline_path}: {len(baseline['historical_belt_games'])} "
          f"historical belt games (was {len(baseline['historical_belt_games']) - 1}).")
    print("Now run build_lineage.py (no --full-refetch needed), then "
          "fetch_game_details.py and build_site.py -- or just update_all.py.")


if __name__ == "__main__":
    main()
