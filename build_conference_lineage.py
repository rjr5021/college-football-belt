#!/usr/bin/env python3
"""
Build one lineal "championship belt" per FBS/FCS conference -- same
winner-take rule as the real belt (build_lineage.py), same origin
convention (belt starts at the first qualifying game), but restricted to
games where BOTH sides were members of that ONE conference.

2026-09-15, Bob: "then create belts for all FBS and FCS conferences" --
follow-up to the three-scope (Combined/FBS/FCS) split just built for both
the real belt and the Losers Belt. Conference eligibility uses
"membership AT THE TIME OF EACH GAME" (Bob's explicit choice over "current
members only"), via the per-game home_conference/away_conference fields
CFBD already reports (added to build_lineage.py's normalize() output --
no extra API call needed to get this). That matters because realignment
is constant and often recent: Texas and Oklahoma were Big 12 members
until 2024, for instance, so filtering by each team's CURRENT conference
would silently misattribute a decade-plus of their real Big 12 games to
the SEC instead.

KNOWN LIMITATION: this matches games against a conference's CURRENT name
(from fetch_division1_teams's sibling, fetch_conferences). If a
conference has literally renamed itself over the decades (not just
changed membership), its games recorded under an old name in CFBD's data
won't match and are excluded -- there's no reliable historical
name-mapping data to resolve that safely, so this belt effectively starts
from whenever the conference has held its current name.

REALIGNMENT AS A VACANCY: the exact same belt_engine.py machinery built
for the Losers Belt's "program went dark for years" problem also solves
"the holder left this conference" for free -- once a team leaves, none of
its games match this conference's filter ever again, which looks
identical to a program going permanently dark. resolve_vacancies() (with
`recent_teams` = teams that actually appear in this conference's own
filtered games recently, not the whole sport) correctly reverts the belt
to whoever it was caught from. See belt_engine.py's own module docstring.

A conference with too little qualifying history (a brand-new conference,
or a name CFBD's game data never actually uses) is skipped entirely for
that run rather than crashing the whole batch -- see `built`/`skipped` in
main()'s summary.

Outputs, one set per conference (see baseline_path/vacancy_path/
lineage_path below for exact filenames, all keyed off a filesystem-safe
slug of the conference's current name):
    historical_data/conferences/<slug>_baseline.json   (git-committed)
    historical_data/conferences/<slug>_vacancies.json  (git-committed)
    belt_data/conferences/<slug>_lineage.json           (ephemeral)

Usage:
    export CFBD_API_KEY=your_key_here
    python3 build_conference_lineage.py                 # incremental
    python3 build_conference_lineage.py --full-refetch   # one-time bootstrap,
                                                          # ~316 CFBD calls,
                                                          # shared across
                                                          # EVERY conference
"""

import argparse
import json
import os
import re
import sys
import time

from build_lineage import (
    HIST_DIR,
    OUT_DIR,
    collect_venues,
    fetch_conferences,
    fetch_seasons,
    normalize,
)
from belt_engine import (
    load_json,
    load_vacancies,
    merge_vacancies,
    resolve_vacancies,
    save_json,
    split_winner_baseline,
)

CONF_HIST_DIR = os.path.join(HIST_DIR, "conferences")
CONF_OUT_DIR = os.path.join(OUT_DIR, "conferences")


def slugify(name):
    """Filesystem/URL-safe slug of a conference's current name, e.g.
    "Southeastern Conference" -> "southeastern-conference". Used for both
    the historical_data/belt_data filenames here and the site's
    conferences/<slug>.html page (build_site.py)."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "conference"


def baseline_path(slug):
    return os.path.join(CONF_HIST_DIR, f"{slug}_baseline.json")


def vacancy_path(slug):
    return os.path.join(CONF_HIST_DIR, f"{slug}_vacancies.json")


def lineage_path(slug):
    return os.path.join(CONF_OUT_DIR, f"{slug}_lineage.json")


def filter_conference_games(games, conference):
    """Keep only games where BOTH sides' per-game conference field equals
    `conference` exactly -- membership AT THE TIME OF THAT GAME, not
    either team's conference today. See this module's own docstring."""
    return [g for g in games
            if g.get("home_conference") == conference and g.get("away_conference") == conference]


def save_baseline(historical_belt_games, historical_reigns, open_reign, live_start_year, path):
    os.makedirs(CONF_HIST_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "live_start_year": live_start_year,
            "historical_belt_games": historical_belt_games,
            "historical_reigns": historical_reigns,
            "open_reign": open_reign,
        }, f, indent=2)


def write_outputs(belt_games, reigns, tie_rule, conference, classification, path):
    os.makedirs(CONF_OUT_DIR, exist_ok=True)
    current = reigns[-1]
    origin_note = (f"First {conference}-vs-{conference} game in CFBD's data"
                    if belt_games and belt_games[0]["outcome"] == "established" else None)
    with open(path, "w") as f:
        json.dump({
            "generated": time.strftime("%Y-%m-%d"),
            "tie_rule": tie_rule,
            "conference": conference,
            "classification": classification,
            "origin": {"date": belt_games[0]["date"] if belt_games else None,
                       "note": origin_note},
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
    p.add_argument("--tie-rule", choices=["holder", "challenger"], default="holder")
    p.add_argument("--full-refetch", action="store_true",
                    help="do the one-time full walk (~316 CFBD calls, shared "
                         "across every conference) -- required the first time "
                         "this ever runs")
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = p.parse_args()

    if not args.key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                 "Free key at https://collegefootballdata.com/key")

    os.makedirs(CONF_OUT_DIR, exist_ok=True)
    os.makedirs(CONF_HIST_DIR, exist_ok=True)

    print("Fetching current FBS/FCS conference list from CFBD...")
    conferences = fetch_conferences(args.key)
    if not conferences:
        print("No conferences returned by CFBD -- nothing to do.")
        return
    fbs_n = sum(1 for c in conferences.values() if c == "fbs")
    fcs_n = sum(1 for c in conferences.values() if c == "fcs")
    other_n = len(conferences) - fbs_n - fcs_n
    print(f"{len(conferences)} current conferences ({fbs_n} FBS, {fcs_n} FCS"
          f"{f', {other_n} unclassified' if other_n else ''})")

    live_start_year = args.end_year - 1
    today = time.strftime("%Y-%m-%d")

    # A conference with an existing baseline always runs (normal
    # incremental update). A conference with NO baseline yet only runs
    # when --full-refetch is explicitly passed -- otherwise it's skipped
    # entirely this pass, same as build_lineage.py's own
    # --bootstrap-championship-scopes gate. This matters even after the
    # very first bootstrap: a conference CFBD's per-game data never
    # actually uses (see this module's KNOWN LIMITATION docstring note)
    # will NEVER get a baseline no matter how many times it's retried, so
    # without this gate every ordinary future run would keep silently
    # paying for a full ~316-call historical re-walk forever, just because
    # one conference's baseline permanently stays None.
    conferences_to_run = []
    baselines = {}
    for name in conferences:
        existing = None if args.full_refetch else load_json(baseline_path(slugify(name)))
        if existing is not None or args.full_refetch:
            conferences_to_run.append(name)
            baselines[name] = existing
    skipped_no_bootstrap = len(conferences) - len(conferences_to_run)

    if not conferences_to_run:
        print("No historical_data/conferences/*_baseline.json yet, and the "
              "one-time historical bootstrap wasn't requested -- skipping "
              "conference belts entirely this run (nothing else is affected). "
              "Tick the 'bootstrap_conference_belts' checkbox on a manual "
              "'Run workflow' to build every conference belt once.")
        return
    if skipped_no_bootstrap:
        print(f"{skipped_no_bootstrap} conference(s) have no baseline yet and the "
              f"one-time bootstrap wasn't requested this run -- skipping just those "
              f"(tick 'bootstrap_conference_belts' on a manual run to build them).")

    print("Fetching venue timezones from CFBD...")
    venue_tz, _venue_info = collect_venues(args.key)

    if any(b is None for b in baselines.values()):
        fetch_from = args.start_year
        print(f"At least one conference needs the one-time full {fetch_from}-"
              f"{args.end_year} walk from CFBD (~{2 * (args.end_year - fetch_from + 1)} "
              f"calls, shared across every conference running this pass).")
    else:
        fetch_from = min(min(b["live_start_year"] for b in baselines.values()), live_start_year)
        seasons = list(range(fetch_from, args.end_year + 1))
        print(f"Baselines found for every conference running this pass -- fetching only "
              f"seasons {seasons} fresh (~{2 * len(seasons)} calls, shared across all of them).")
    raw = fetch_seasons(range(fetch_from, args.end_year + 1), args.key)
    games_all = normalize(raw, venue_tz)
    print(f"{len(games_all)} completed games in chronological order\n")

    built = skipped = 0
    for name in sorted(conferences_to_run):
        classification = conferences[name]
        slug = slugify(name)
        baseline = baselines[name]
        games = filter_conference_games(games_all, name)

        if baseline is None:
            if not games:
                print(f"[{slug}] No {name}-vs-{name} games found in CFBD's data at all "
                      f"-- skipping (likely a name CFBD's per-game data doesn't use; "
                      f"see this module's KNOWN LIMITATION docstring note).")
                skipped += 1
                continue
            print(f"[{slug}] No historical baseline -- doing the one-time full walk "
                  f"({len(games)} {name}-vs-{name} games).")
            recent_teams = {g["home"] for g in games if g["season"] >= live_start_year} | \
                           {g["away"] for g in games if g["season"] >= live_start_year}
            belt_games, reigns, new_vacancies = resolve_vacancies(
                games, args.tie_rule, start_holder=None, start_reign=None,
                recent_teams=recent_teams, today=today)
        else:
            scope_fetch_from = min(baseline["live_start_year"], live_start_year)
            scoped_games = [g for g in games if g["season"] >= scope_fetch_from]
            print(f"[{slug}] Baseline found (through season "
                  f"{baseline['live_start_year'] - 1}, "
                  f"{len(baseline['historical_belt_games'])} belt games already settled) "
                  f"-- using {len(scoped_games)} {name}-vs-{name} games from season "
                  f"{scope_fetch_from} on")
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

        all_vacancies = load_vacancies(vacancy_path(slug))
        if new_vacancies:
            all_vacancies, changed = merge_vacancies(all_vacancies, new_vacancies)
            if changed:
                save_json(all_vacancies, vacancy_path(slug), CONF_HIST_DIR)

        write_outputs(belt_games, reigns, args.tie_rule, name, classification, lineage_path(slug))

        hist_belt_games, hist_reigns, open_reign = split_winner_baseline(
            belt_games, all_vacancies, reigns[0], live_start_year)
        save_baseline(hist_belt_games, hist_reigns, open_reign, live_start_year, baseline_path(slug))

        current = reigns[-1]
        built += 1
        print(f"[{slug}] current holder: {current['team']} since {current['start_date']} "
              f"({current['defenses']} defenses, {len(belt_games)} belt games)\n")

    print(f"Built {built} conference belt(s), skipped {skipped} with no qualifying history.")


if __name__ == "__main__":
    main()
