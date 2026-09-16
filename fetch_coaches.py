#!/usr/bin/env python3
"""
Fetch head-coach history for every team that has ever held the College
Football Belt, from CFBD's /coaches endpoint -- feeds the Records page's
"Belt Held By Coach" leaderboard (2026-09-16 wishlist item, task #84:
"droughts and coaches leaderboards").

Usage:
    export CFBD_API_KEY=your_key_here      # same key as build_lineage.py
    python3 fetch_coaches.py                        # reads belt_data/lineage.json
    python3 fetch_coaches.py --lineage path/to/lineage.json

Output (into ./belt_data/):
    coaches_raw.json   every /coaches?team=<team> response, cached per team
                        so reruns only fetch teams not already cached
    coaches.json        { "<team name as it appears in lineage.json>":
                              [ {"year": 1998, "coach": "Bobby Bowden"}, ... ]
                            , sorted by year, one entry per coach-season }

Run this AFTER build_lineage.py has produced belt_data/lineage.json. One
CFBD call per distinct team that's ever held the (combined, main) belt --
a few hundred at most, well inside a Tier 2 month's budget, and every
result is cached in coaches_raw.json so a rerun only pays for teams that
were never fetched (delete a team's key from coaches_raw.json, or the
whole file, to force a refetch).

Same graceful-degradation posture as fetch_team_colors.py: a team CFBD's
/coaches doesn't recognize (renamed, defunct, obscure pre-modern program)
just gets no entry in coaches.json -- build_site.py already treats an
unattributed reign as "coach unknown" and skips it in the leaderboard,
same as it does for any other optional/partial data on this site.
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


def pick(d, *names, default=None):
    """CFBD field names have shifted between snake_case and camelCase."""
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _get_json(url, api_key, label, retries=5):
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
                wait = min(5 * (2 ** attempt), 60)
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


def fetch_coaches_for_team(team, api_key):
    url = f"{API_BASE}/coaches?team={urllib.parse.quote(team)}"
    return _get_json(url, api_key, f"coaches team={team}")


def collect_coaches_raw(belt_teams, api_key, cache_path):
    """Resumable per-team cache, same pattern as fetch_game_details.py's
    _collect_generic -- saved after every team so a 429 mid-run, or the
    process getting killed, never throws away progress already made."""
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)
        print(f"Resuming from cached {cache_path}: {len(cache)} team(s) already fetched")

    remaining = [t for t in belt_teams if t not in cache]
    if not remaining:
        print(f"All {len(belt_teams)} team(s) already cached in {cache_path} -- nothing new to fetch.")
        return cache

    print(f"Fetching coach history for {len(remaining)} of {len(belt_teams)} team(s) "
          f"({len(belt_teams) - len(remaining)} already cached)...")

    def save_cache():
        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(cache, f)
        os.replace(tmp_path, cache_path)

    failed = []
    try:
        for i, team in enumerate(remaining, 1):
            try:
                cache[team] = fetch_coaches_for_team(team, api_key)
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                # This whole feature is optional (a leaderboard card that
                # just doesn't render if coaches.json comes up short) -- a
                # team CFBD chokes on shouldn't take down the rest of the
                # pipeline the way a required stage's failure should. Leave
                # it out of the cache so the next run retries just this team.
                print(f"  WARNING: coaches fetch failed for {team!r}: {e} -- skipping", file=sys.stderr)
                failed.append(team)
            if i % 10 == 0 or i == len(remaining):
                save_cache()
                print(f"  {i}/{len(remaining)} teams fetched...")
    finally:
        save_cache()

    if failed:
        print(f"\n{len(failed)} team(s) failed to fetch and will be retried next run: "
              f"{', '.join(failed)}", file=sys.stderr)

    return cache


def build_coach_seasons(team, raw_coaches):
    """Flatten a /coaches response (list of Coach, each with a `seasons`
    array) into one row per coach-season actually spent AT this team --
    a coach's `seasons` list can include years at OTHER schools too, so
    filter on season.school == team rather than trusting the ?team= query
    param alone to have already done that."""
    rows = []
    for c in raw_coaches or []:
        first = pick(c, "first_name", "firstName", default="")
        last = pick(c, "last_name", "lastName", default="")
        name = f"{first} {last}".strip()
        if not name:
            continue
        for s in pick(c, "seasons", default=[]) or []:
            school = pick(s, "school")
            year = pick(s, "year")
            if school != team or year is None:
                continue
            rows.append({"year": int(year), "coach": name})
    rows.sort(key=lambda r: r["year"])
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lineage", default=os.path.join(OUT_DIR, "lineage.json"))
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = p.parse_args()

    if not args.key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                 "Free key at https://collegefootballdata.com/key")

    if not os.path.exists(args.lineage):
        sys.exit(f"Can't find {args.lineage} -- run build_lineage.py first.")

    with open(args.lineage) as f:
        lineage = json.load(f)
    belt_teams = sorted({r["team"] for r in lineage["reigns"]})
    print(f"{len(belt_teams)} distinct teams have held the belt")

    os.makedirs(OUT_DIR, exist_ok=True)
    raw_path = os.path.join(OUT_DIR, "coaches_raw.json")
    raw = collect_coaches_raw(belt_teams, args.key, raw_path)

    result = {}
    for team in belt_teams:
        seasons = build_coach_seasons(team, raw.get(team))
        if seasons:
            result[team] = seasons

    out_path = os.path.join(OUT_DIR, "coaches.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)

    unmatched = sorted(t for t in belt_teams if t not in result)
    print(f"\nMatched coach history for {len(result)} of {len(belt_teams)} teams.")
    print(f"Wrote {out_path}")
    if unmatched:
        print(f"\n{len(unmatched)} team(s) with no coach data from CFBD -- reigns for these "
              f"teams just won't show up in the by-coach leaderboard:")
        for n in unmatched:
            print(f"  - {n}")


if __name__ == "__main__":
    main()
