#!/usr/bin/env python3
"""
Rerun the belt under alternate rules ("alternate universes", 2026-09-16
batch): the same engine as the real belt, walked over the same games,
with one rule changed each time --

    ties-pass   a tie hands the belt to the challenger (the real belt keeps
                it with the holder)
    no-bowls    postseason games can't move the belt
    poll-era    the belt starts with the first AP No. 1, October 1936
    bcs-era     the belt starts with the 1998 preseason AP No. 1

-- so the site can show where the belt WOULD be, how often each universe
agrees with the real one, and the first game where they parted ways
(universes/ on the site; see build_site.py's generate_universe_pages).

Usage:
    export CFBD_API_KEY=your_key_here
    python3 build_alternate_lineages.py                # normal run: free
    python3 build_alternate_lineages.py --bootstrap    # one-time archive build

Why this needs its own archive
------------------------------
The real belt's incremental fetch only keeps belt games (historical_data/
baseline.json); every other game is thrown away once a season is frozen.
A different rule needs EVERY game, because the moment a universe's holder
differs from the real one, the games that matter are different games. So
the first run (--bootstrap, ~316 CFBD calls, the same one-time cost as
the Losers Belt / conference bootstraps) pulls every season since 1869
and archives the handful of fields the engine needs, gzip-compressed, in
historical_data/all_games.json.gz (~1-2 MB, committed back by the
workflow with the rest of historical_data/). Every later run is free:
the archive covers the frozen seasons, and the live seasons come from
belt_data/games_raw.json, which build_lineage.py fetches every run
anyway. As seasons age into the frozen baseline they're appended to the
archive from that same games_raw.json -- no API call.

Without the archive (before the bootstrap has been run) this stage prints
a note and exits 0 -- universes/ just doesn't appear on the site yet.

Outputs (into ./belt_data/universes/, regenerated every run):
    <slug>.json     {name, slug, rule, description, current_holder, reigns,
                     belt_games, totals} -- same shape as lineage.json's
                     reigns/belt_games, so build_site.py can reuse its
                     lineage-table rendering
    index.json      the list of universes + the archive's coverage
"""

import argparse
import gzip
import json
import os
import sys
import time
from datetime import date

from belt_engine import resolve_vacancies, walk_winner

DATA_DIR = "belt_data"
HIST_DIR = "historical_data"
ARCHIVE_PATH = os.path.join(HIST_DIR, "all_games.json.gz")
UNIVERSES_DIR = os.path.join(DATA_DIR, "universes")
RANKINGS_CACHE = os.path.join(HIST_DIR, "rankings.json")
FIRST_SEASON = 1869

UNIVERSES = [
    {"slug": "ties-pass", "name": "Ties pass the belt",
     "rule": "A tie hands the belt to the challenger instead of leaving it with the holder.",
     "why": "The real belt borrows boxing's rule: a draw doesn't move the title. Flip that one call and every tie since 1877 becomes a title change -- 54 of them, each rerouting everything after it.",
     "tie_rule": "challenger"},
    {"slug": "no-bowls", "name": "Bowls don't count",
     "rule": "Postseason games can't move the belt; the holder carries it through the winter untouched.",
     "why": "Bowls pair the holder with someone good on purpose, and dozens of reigns have ended in one. This universe treats the postseason as an exhibition, the way the sport itself did for most of a century.",
     "exclude_postseason": True},
    {"slug": "poll-era", "name": "The poll era",
     "rule": "The belt is born the week the first AP poll comes out, in October 1936, held by that poll's No. 1.",
     "why": "Everything before the poll era is prehistory to most fans. This universe starts the lineage where the arguments started.",
     "start_season": 1936, "origin": "ap1"},
    {"slug": "bcs-era", "name": "The BCS era",
     "rule": "The belt starts with the 1998 preseason AP No. 1 and follows the modern game from there.",
     "why": "1998 is when the sport first tried to settle its championship on the field. A belt that starts there has only ever lived in the era of title games and playoffs.",
     "start_season": 1998, "origin": "ap1"},
]

FIELDS = ("id", "date", "season", "week", "season_type", "home", "away",
          "home_points", "away_points", "neutral")


# ------------------------------------------------------------- archive I/O

def load_archive():
    if not os.path.exists(ARCHIVE_PATH):
        return None
    with gzip.open(ARCHIVE_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


def save_archive(archive):
    os.makedirs(HIST_DIR, exist_ok=True)
    tmp = ARCHIVE_PATH + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(archive, f, separators=(",", ":"))
    os.replace(tmp, ARCHIVE_PATH)


def compact(games):
    """Normalized game dicts -> rows in FIELDS order (the archive format)."""
    return [[g.get(k) for k in FIELDS] for g in games]


def expand(rows):
    return [dict(zip(FIELDS, row)) for row in rows]


def archived_seasons(archive):
    return sorted(int(s) for s in archive.get("seasons", {}))


# ------------------------------------------------------------- game sources

def live_start_year():
    """The real belt's frozen/live boundary (historical_data/baseline.json)."""
    path = os.path.join(HIST_DIR, "baseline.json")
    if os.path.exists(path):
        with open(path) as f:
            return int(json.load(f).get("live_start_year") or date.today().year)
    return date.today().year


def normalized_live_games(venue_tz=None):
    """This run's freshly fetched seasons (belt_data/games_raw.json, written
    by build_lineage.py), normalized exactly the way the real belt sees
    them. [] when the file isn't there (a run without build_lineage.py)."""
    path = os.path.join(DATA_DIR, "games_raw.json")
    if not os.path.exists(path):
        return []
    from build_lineage import normalize
    with open(path) as f:
        raw = json.load(f)
    return normalize(raw, venue_tz)


def bootstrap_archive(api_key, through_season):
    """The one-time full pull: every season FIRST_SEASON..through_season,
    both season types, normalized with venue-local dates like the real
    belt, then archived. Resumable per season."""
    from build_lineage import collect_venues, fetch_year, normalize
    print("Fetching venue timezones from CFBD...")
    venue_tz, _ = collect_venues(api_key)
    archive = load_archive() or {"schema": 1, "seasons": {}}
    seasons = archive["seasons"]
    todo = [s for s in range(FIRST_SEASON, through_season + 1) if str(s) not in seasons]
    print(f"Archiving {len(todo)} season(s) ({len(seasons)} already archived): ~{2 * len(todo)} CFBD calls")
    for n, s in enumerate(todo, 1):
        raw = []
        for st in ("regular", "postseason"):
            raw.extend(fetch_year(s, api_key, st) or [])
            time.sleep(0.15)
        games = normalize(raw, venue_tz)
        seasons[str(s)] = compact(games)
        print(f"  {s}: {len(games)} completed games")
        if n % 10 == 0 or n == len(todo):
            save_archive(archive)
    return archive, venue_tz


# ------------------------------------------------------------- the walks

def first_poll_no1(season):
    """(team, week) of the AP No. 1 in the FIRST poll of `season`, from
    fetch_rankings.py's cache; None if that season isn't cached."""
    if not os.path.exists(RANKINGS_CACHE):
        return None
    with open(RANKINGS_CACHE) as f:
        cache = json.load(f)
    entry = (cache.get("seasons") or {}).get(str(season))
    if not entry:
        return None
    for w in entry.get("weeks", []):
        if w.get("st") == "regular" and w.get("ap"):
            return w["ap"][0][0], int(w["week"])
    return None


def run_universe(u, games, today):
    """Walk one universe over `games` (chronological, every division).
    Returns (belt_games, reigns, vacancies, origin_note)."""
    tie_rule = u.get("tie_rule", "holder")
    if u.get("exclude_postseason"):
        games = [g for g in games if (g.get("season_type") or "regular") != "postseason"]
    seasons_present = sorted({g["season"] for g in games})
    recent = seasons_present[-2:]
    recent_teams = {g["home"] for g in games if g["season"] in recent} | {g["away"] for g in games if g["season"] in recent}

    start_holder = start_reign = None
    origin_note = ""
    if u.get("start_season"):
        s0 = u["start_season"]
        games = [g for g in games if g["season"] >= s0]
        no1 = first_poll_no1(s0) if u.get("origin") == "ap1" else None
        if no1:
            team, week = no1
            first = next((g for g in games if g["season"] == s0 and (g.get("week") or 0) >= week
                          and team in (g["home"], g["away"])), None)
            if first:
                start_holder = team
                start_reign = {"team": team, "start_date": first["date"], "won_from": None,
                               "won_score": None, "defenses": 0, "last_game_date": first["date"]}
                origin_note = (f"{team} was No. 1 in the first AP poll of {s0} (week {week}); the belt starts in their hands "
                               f"going into their next game, {first['date']}.")
        if start_holder is None:
            first = games[0]
            winner = first["home"] if first["home_points"] > first["away_points"] else first["away"]
            if first["home_points"] == first["away_points"]:
                winner = first["home"]
            start_holder = winner
            start_reign = {"team": winner, "start_date": first["date"], "won_from": None,
                           "won_score": f"{first['home_points']}-{first['away_points']}",
                           "defenses": 0, "last_game_date": first["date"]}
            origin_note = (f"No poll on file for {s0}, so the belt starts with the winner of the season's first game: "
                           f"{winner}, {first['date']}.")
            games = games[1:]
    belt_games, reigns, vacancies = resolve_vacancies(
        games, tie_rule, start_holder=start_holder, start_reign=start_reign,
        recent_teams=recent_teams, today=today)
    if start_reign is not None and belt_games and start_holder is not None:
        # resolve_vacancies resumes from start_reign; make the first reign
        # visibly "established" so the site can explain the origin
        reigns[0].setdefault("origin", origin_note)
    for r in reigns:
        r.pop("last_game_date", None)
    return belt_games, reigns, vacancies, origin_note


def write_universe(u, belt_games, reigns, vacancies, origin_note, coverage, today):
    os.makedirs(UNIVERSES_DIR, exist_ok=True)
    current = reigns[-1]
    out = {
        "generated": today.isoformat(),
        "slug": u["slug"], "name": u["name"], "rule": u["rule"], "why": u["why"],
        "origin_note": origin_note,
        "tie_rule": u.get("tie_rule", "holder"),
        "exclude_postseason": bool(u.get("exclude_postseason")),
        "start_season": u.get("start_season"),
        "coverage": coverage,
        "current_holder": current["team"],
        "current_reign_since": current["start_date"],
        "current_defenses": current.get("defenses", 0),
        "totals": {"belt_games": len(belt_games), "reigns": len(reigns),
                   "distinct_teams": len({r["team"] for r in reigns}),
                   "vacancies": len(vacancies or [])},
        "reigns": reigns,
        "belt_games": belt_games,
    }
    with open(os.path.join(UNIVERSES_DIR, f"{u['slug']}.json"), "w") as f:
        json.dump(out, f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", action="store_true",
                    help="one-time: pull every season since 1869 into the archive (~316 CFBD calls)")
    args = ap.parse_args()
    api_key = os.environ.get("CFBD_API_KEY")
    today = date.today()
    frozen_through = live_start_year() - 1

    archive = load_archive()
    venue_tz = None
    if args.bootstrap:
        if not api_key:
            sys.exit("--bootstrap needs CFBD_API_KEY.")
        archive, venue_tz = bootstrap_archive(api_key, frozen_through)
    if archive is None:
        print(f"{ARCHIVE_PATH} not found -- the alternate universes need the one-time archive of every "
              f"game since {FIRST_SEASON}. Tick 'bootstrap_alternate_universes' on a manual 'Run workflow' "
              f"(or run this script with --bootstrap) once; skipping this stage until then.")
        return

    live = normalized_live_games(venue_tz)
    # seasons that have aged into the frozen range since the archive was built come from games_raw for free
    have = set(archived_seasons(archive))
    added = 0
    for s in sorted({g["season"] for g in live}):
        if s <= frozen_through and s not in have:
            archive["seasons"][str(s)] = compact([g for g in live if g["season"] == s])
            added += 1
    if added:
        save_archive(archive)
        print(f"Appended {added} newly frozen season(s) to {ARCHIVE_PATH} from this run's games_raw.json.")

    seasons = archived_seasons(archive)
    missing = [s for s in range(FIRST_SEASON, frozen_through + 1) if s not in set(seasons)]
    games = []
    for s in seasons:
        games.extend(expand(archive["seasons"][str(s)]))
    live_seasons = sorted({g["season"] for g in live})
    games.extend(g for g in live if g["season"] > (seasons[-1] if seasons else 0))
    games.sort(key=lambda g: (g["date"], (g.get("season_type") or "regular") != "regular", g.get("id") or 0))
    coverage = {"archived_through": seasons[-1] if seasons else None, "live_seasons": live_seasons,
                "missing_seasons": missing, "games": len(games)}
    if missing:
        print(f"WARNING: the archive is missing {len(missing)} season(s) ({missing[:5]}...); the universes "
              f"below are walked over an incomplete record. Re-run with --bootstrap to fill the gaps.")
    print(f"{len(games):,} games on hand (archived through {coverage['archived_through']}, live {live_seasons}).")

    index = {"generated": today.isoformat(), "coverage": coverage, "universes": []}
    for u in UNIVERSES:
        belt_games, reigns, vacancies, origin_note = run_universe(u, games, today)
        out = write_universe(u, belt_games, reigns, vacancies, origin_note, coverage, today)
        index["universes"].append({k: out[k] for k in ("slug", "name", "rule", "current_holder", "current_reign_since", "totals")})
        print(f"  [{u['slug']}] {out['current_holder']} holds it since {out['current_reign_since']} "
              f"-- {len(reigns)} reigns, {len(belt_games)} belt games, {len(vacancies or [])} vacancies")
    with open(os.path.join(UNIVERSES_DIR, "index.json"), "w") as f:
        json.dump(index, f, indent=1)
    print(f"Wrote {len(UNIVERSES)} universe(s) to {UNIVERSES_DIR}/")


if __name__ == "__main__":
    main()
