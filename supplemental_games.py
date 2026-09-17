#!/usr/bin/env python3
"""
Supplemental games: completed games the College Football Data API doesn't
have, entered by hand from cited sources in
historical_data/supplemental_games.json.

The belt is computed from CFBD's record, and CFBD's pre-war coverage of
club, service and small-college teams has holes. When a hole changes the
lineage (the first one: Loyola (CA) 13, Olympic Club 0 on Nov. 21, 1931,
plus the five games that follow from it), the missing game goes in the
JSON file with its sources, and this module merges it into the game
stream wherever the belt is walked:

    build_lineage.py            (so a --full-refetch rebuild includes it)
    build_alternate_lineages.py (so the alternate universes include it)
    apply_supplemental_games.py (one-time splice into baseline.json)

Supplemental games are never written into historical_data/all_games.json.gz
-- a --bootstrap rebuild of that archive would silently drop them. They're
merged at read time instead.

If CFBD ever adds one of these games itself, the CFBD copy wins and the
supplemental copy is skipped with a warning (same season, same two teams,
dates within two days). The entry can then be deleted from the JSON file.

Ids: 90,000,000-99,999,999 is reserved for these. CFBD's own ids are
either small (pre-2001, max ~64k) or ESPN ids (>= 100,000,000), and
build_site.py only links ESPN box scores for ids >= 100,000,000, so a
supplemental id never collides and never gets a bogus ESPN link.
"""

import json
import os
import sys
from datetime import date

HIST_DIR = "historical_data"
SUPPLEMENTAL_PATH = os.path.join(HIST_DIR, "supplemental_games.json")
ID_MIN, ID_MAX = 90_000_000, 99_999_999

REQUIRED = ("id", "date", "season", "week", "season_type", "home", "away",
            "home_points", "away_points", "neutral", "sources")

_cache = {}


def load_entries(path=SUPPLEMENTAL_PATH):
    """The validated raw entries (with sources/notes), [] if no file."""
    if path in _cache:
        return _cache[path]
    if not os.path.exists(path):
        _cache[path] = []
        return []
    with open(path) as f:
        data = json.load(f)
    entries = data.get("games", []) if isinstance(data, dict) else data
    seen = set()
    for e in entries:
        missing = [k for k in REQUIRED if k not in e]
        if missing:
            sys.exit(f"{path}: entry {e.get('id')} is missing {missing}")
        gid = e["id"]
        if not isinstance(gid, int) or not ID_MIN <= gid <= ID_MAX:
            sys.exit(f"{path}: id {gid!r} is outside the reserved "
                     f"{ID_MIN:,}-{ID_MAX:,} range")
        if gid in seen:
            sys.exit(f"{path}: duplicate id {gid}")
        seen.add(gid)
        d = date.fromisoformat(e["date"])  # raises on a bad date
        if e["season"] not in (d.year, d.year - 1):
            sys.exit(f"{path}: id {gid} has season {e['season']} but date {e['date']}")
        if not isinstance(e["home_points"], int) or not isinstance(e["away_points"], int):
            sys.exit(f"{path}: id {gid} needs integer scores")
        if e["home"] == e["away"]:
            sys.exit(f"{path}: id {gid} has the same team on both sides")
        if not e["sources"]:
            sys.exit(f"{path}: id {gid} has no sources -- every supplemental game must cite one")
    _cache[path] = entries
    return entries


def normalized(entry):
    """One entry in the exact shape build_lineage.normalize() produces
    (plus venue_id, which the alternate-lineage archive rows carry)."""
    return {
        "id": entry["id"],
        "date": entry["date"],
        "season": entry["season"],
        "week": entry["week"],
        "season_type": entry["season_type"],
        "home": entry["home"],
        "away": entry["away"],
        "home_points": entry["home_points"],
        "away_points": entry["away_points"],
        "neutral": bool(entry["neutral"]),
        "home_conference": entry.get("home_conference"),
        "away_conference": entry.get("away_conference"),
        "home_division": entry.get("home_division"),
        "away_division": entry.get("away_division"),
        "venue_id": None,
    }


def _sort_key(g):
    return (g["date"], (g.get("season_type") or "regular") != "regular", g.get("id") or 0)


def _already_in(entry, games):
    teams = {entry["home"], entry["away"]}
    d = date.fromisoformat(entry["date"])
    for g in games:
        if g.get("season") != entry["season"] or {g["home"], g["away"]} != teams:
            continue
        if abs((date.fromisoformat(g["date"]) - d).days) <= 2:
            return g
    return None


def merge_supplemental(games, seasons=None, path=SUPPLEMENTAL_PATH, quiet=False):
    """Return `games` plus every supplemental game whose season is in
    `seasons` (all of them when seasons is None), re-sorted the way
    normalize() sorts. `games` itself is not modified."""
    entries = load_entries(path)
    if not entries:
        return games
    wanted = None if seasons is None else set(int(s) for s in seasons)
    added = []
    for e in entries:
        if wanted is not None and e["season"] not in wanted:
            continue
        dup = _already_in(e, games)
        if dup is not None:
            print(f"NOTE: supplemental game {e['id']} ({e['away']} at {e['home']}, "
                  f"{e['date']}) is now in CFBD as id {dup.get('id')} -- using CFBD's "
                  f"copy. It can be removed from {path}.", file=sys.stderr)
            continue
        added.append(normalized(e))
    if not added:
        return games
    if not quiet:
        print(f"Merged {len(added)} supplemental game(s) from {path} "
              f"(games CFBD doesn't have -- see ruleset.md)")
    return sorted(list(games) + added, key=_sort_key)


def is_supplemental(game_id):
    try:
        return ID_MIN <= int(game_id) <= ID_MAX
    except (TypeError, ValueError):
        return False


def entry_for(game_id, path=SUPPLEMENTAL_PATH):
    """The full entry (sources, note) for a supplemental game id, or None."""
    if not is_supplemental(game_id):
        return None
    for e in load_entries(path):
        if e["id"] == int(game_id):
            return e
    return None


def sources_html(game_id, esc, rel=""):
    """The Sources-strip links for a supplemental game's page (its cited
    sources plus a pointer to the ruleset), or None for a CFBD game --
    build_site.py shows its usual CFBD attribution in that case."""
    e = entry_for(game_id)
    if e is None:
        return None
    links = [f'<a href="{esc(s["url"])}" target="_blank" rel="noopener">{esc(s["label"])}</a>'
             for s in e["sources"] if s.get("url")]
    links.append(f'<a href="{rel}ruleset.html">Added by hand: not in College Football Data</a>')
    return " &middot; ".join(links)
