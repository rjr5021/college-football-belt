#!/usr/bin/env python3
"""
Fetch team colors/branding for every team that has held the College Football
Belt, keyed for the site's color-shift feature. Also captures each team's
home state, which build_site.py uses for the "everywhere the belt has
lived" map -- free, since it's already part of the same /teams response.

Usage:
    export CFBD_API_KEY=your_key_here      # same key as build_lineage.py
    python3 fetch_team_colors.py                        # reads belt_data/lineage.json
    python3 fetch_team_colors.py --lineage path/to/lineage.json

Output (into ./belt_data/):
    teams_raw.json     every team CFBD returns, cached so reruns don't refetch
    team_colors.json    one entry per team that has ever held the belt:
        { "<team name as it appears in lineage.json>": {
              "matched": true/false,
              "color": "#...", "alternate_color": "#...",
              "logo": "https://...", "mascot": "...",
              "classification": "fbs" / "fcs" / ... or null,
              "state": "IN" / ... or null (two-letter state code)
          }, ... }

Run this AFTER build_lineage.py has produced belt_data/lineage.json.

Two separate reasons a team can end up with no usable color, both reported
by the run summary and both meaning "pick a color by hand":
  - "matched": false -- the team name never turned up in CFBD's team list at
    all (renamed schools, long-defunct programs, obscure early-era clubs).
  - "matched": true but "color": null -- CFBD does track the school, but for
    some smaller/historic programs its color field is a literal placeholder
    string ("#null") rather than a real hex color. This script normalizes
    that placeholder to a real null so it never accidentally renders as an
    (invalid) CSS color -- it does NOT invent a color.
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


def pick(d, *names, default=None):
    """CFBD field names have shifted between snake_case and camelCase."""
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def clean_color(v):
    """CFBD represents a genuinely missing color as the literal string
    "#null" (or "null") for some older/small programs, not JSON null.
    Treat that the same as missing, so it never ends up rendered as an
    (invalid) CSS color."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip().lower() in ("#null", "null", ""):
        return None
    return v


def _get_json(url, api_key, label, retries=4):
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


def fetch_all_teams(api_key):
    """No year/conference filter -- CFBD returns every team it tracks across
    every classification it knows about."""
    return _get_json(f"{API_BASE}/teams", api_key, "teams")


def collect_teams(api_key):
    cache = os.path.join(OUT_DIR, "teams_raw.json")
    if os.path.exists(cache):
        print(f"Using cached {cache} (delete it to refetch)")
        with open(cache) as f:
            return json.load(f)
    raw = fetch_all_teams(api_key)
    with open(cache, "w") as f:
        json.dump(raw, f)
    print(f"Cached {len(raw)} teams to {cache}")
    return raw


def build_school_index(all_teams):
    """Map every name CFBD might call a team by -> its team record."""
    by_school = {}
    for t in all_teams:
        school = pick(t, "school")
        if school:
            by_school.setdefault(school, t)
        alt_names = pick(t, "alternate_names", "alternateNames", default=[]) or []
        for alt in alt_names:
            by_school.setdefault(alt, t)
    return by_school


def resolve(name, by_school):
    t = by_school.get(name)
    if t is None:
        return {"matched": False, "color": None, "alternate_color": None,
                "logo": None, "mascot": None, "classification": None, "state": None}
    logos = pick(t, "logos", default=[]) or []
    location = pick(t, "location", default={}) or {}
    return {
        "matched": True,
        "color": clean_color(pick(t, "color")),
        "alternate_color": clean_color(pick(t, "alternate_color", "alternateColor")),
        "logo": logos[0] if logos else None,
        "mascot": pick(t, "mascot"),
        "classification": pick(t, "classification"),
        # Two-letter state code (or None for the handful of unmatched historic
        # programs above) -- feeds the "everywhere the belt has lived" map in
        # build_site.py. Free: CFBD's /teams already returns this in the same
        # call, no extra API cost.
        "state": pick(location, "state"),
    }


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
    all_teams = collect_teams(args.key)
    by_school = build_school_index(all_teams)

    result = {name: resolve(name, by_school) for name in belt_teams}
    matched = sum(1 for v in result.values() if v["matched"])

    out_path = os.path.join(OUT_DIR, "team_colors.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)

    unmatched = sorted(n for n, v in result.items() if not v["matched"])
    no_primary = sorted(n for n, v in result.items() if not v["color"])
    no_alt = sorted(n for n, v in result.items()
                     if v["matched"] and not v["alternate_color"])

    print(f"\nMatched {matched} of {len(belt_teams)} teams to CFBD's team list.")
    print(f"Wrote {out_path}")
    if unmatched:
        print(f"\n{len(unmatched)} team(s) not found in CFBD's team list at all "
              f"-- likely renamed, defunct, or pre-modern programs:")
        for n in unmatched:
            print(f"  - {n}")
    if no_primary:
        print(f"\n{len(no_primary)} team(s) matched but have no usable PRIMARY "
              f"color on file (needed for the color-shift feature) -- pick one "
              f"by hand in team_colors.json:")
        for n in no_primary:
            print(f"  - {n}")
    if no_alt:
        print(f"\n{len(no_alt)} more team(s) are missing only an ALTERNATE "
              f"color (lower priority -- primary color is what the color-shift "
              f"feature needs):")
        for n in no_alt:
            print(f"  - {n}")


if __name__ == "__main__":
    main()
