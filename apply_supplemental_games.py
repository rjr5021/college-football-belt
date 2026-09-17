#!/usr/bin/env python3
"""
One-time fix (repeatable any time historical_data/supplemental_games.json
changes): fold the supplemental games -- completed games CFBD doesn't have
-- into the committed historical baseline, historical_data/baseline.json.

First use (2026-09-17): the 1931 Olympic Club correction. CFBD is missing
Loyola (CA) 13, Olympic Club 0 on Nov. 21, 1931, so the lineage had
Olympic Club holding the belt for ten months, until Stanford beat them on
Sept. 17, 1932. With the missing games in, the belt actually went:

    Olympic Club   won 1931-11-07 from Saint Mary's (CA)
    Loyola (CA)    won 1931-11-21, 13-0          <- missing from CFBD
    San Diego Marines won 1931-11-29, 7-0        <- missing
                   defended 1932-09-12, 1932-09-18  <- missing
    Fresno State   won 1932-09-24, 12-0          <- missing
    West Coast Army won 1932-10-01, 7-6          <- missing
                   defended 1932-10-08 (in CFBD)
    Stanford       won 1932-10-15, 26-0 (in CFBD)
    USC            won 1932-10-22 -- back on the existing lineage

Why a re-walk and not a hand edit: the baseline is a frozen walk of the
game-by-game archive (historical_data/all_games.json.gz). This script
re-walks that archive from 1869 with the exact same walk() build_lineage.py
uses, and FIRST checks that the archive alone reproduces today's baseline
byte for byte. Only if that holds does it re-walk with the supplemental
games merged in and write the result, so every reign/defense count after
the correction is computed, not typed. No CFBD API calls.

Usage (from the project folder):
    python3 apply_supplemental_games.py --dry-run   # show what would change
    python3 apply_supplemental_games.py             # write baseline.json

Safe to run more than once: if the baseline already includes the
supplemental games it says so and changes nothing.

After running: build_lineage.py normally (no --full-refetch needed), then
the rest of the pipeline -- or just update_all.py. Reign numbers from #66
on shift by +4 with the 1931 correction (four reigns are inserted), so
reigns/N.html URLs for those reigns change on the next build.

Only the "combined" scope (the real belt) is patched. The FBS/FCS-only
scopes filter to current Division 1 teams, which drops every one of these
games anyway, and the Losers Belt / conference belts don't pass through them.
"""

import argparse
import gzip
import json
import os
import sys

from build_lineage import FIRST_GAME_DATE, split_at_season, walk
from supplemental_games import SUPPLEMENTAL_PATH, load_entries, merge_supplemental

HIST_DIR = "historical_data"
# AI-written text cached per game_id that states what the game meant for
# the belt. A game whose belt outcome changes needs its text regenerated.
GAME_TEXT_CACHES = [os.path.join("recap_cache", "historical_notes.json"),
                    os.path.join("recap_cache", "recaps.json")]
BASELINE_PATH = os.path.join(HIST_DIR, "baseline.json")
ARCHIVE_PATH = os.path.join(HIST_DIR, "all_games.json.gz")
# Must match build_alternate_lineages.FIELDS (the archive's row layout).
# Duplicated rather than imported so this script doesn't pull in that
# module's heavier dependencies.
FIELDS = ("id", "date", "season", "week", "season_type", "home", "away",
          "home_points", "away_points", "neutral",
          "home_conference", "away_conference", "venue_id")


def load_archive_games(before_season):
    if not os.path.exists(ARCHIVE_PATH):
        sys.exit(f"Can't find {ARCHIVE_PATH}. It's built once by "
                 f"build_alternate_lineages.py --bootstrap (the "
                 f"'bootstrap_alternate_universes' workflow option).")
    with gzip.open(ARCHIVE_PATH, "rt", encoding="utf-8") as f:
        archive = json.load(f)
    seasons = archive.get("seasons", {})
    have = {int(s) for s in seasons}
    missing = [y for y in range(1869, before_season) if y not in have]
    if missing:
        sys.exit(f"{ARCHIVE_PATH} is missing {len(missing)} season(s) before "
                 f"{before_season} ({missing[:5]}...) -- can't re-walk the "
                 f"full history. Run build_alternate_lineages.py --bootstrap first.")
    games = []
    for s, rows in seasons.items():
        if int(s) < before_season:
            games.extend(dict(zip(FIELDS, row)) for row in rows)
    games.sort(key=lambda g: (g["date"], (g.get("season_type") or "regular") != "regular",
                              g.get("id") or 0))
    return games


def rewalk(games, live_start_year):
    belt_games, reigns = walk(games, "holder")
    return split_at_season(belt_games, reigns, live_start_year)


def reign_label(r):
    return f"{r['team']} ({r['start_date']} to {r.get('end_date') or 'open'}, {r['defenses']} def.)"


def describe_diff(old_games, old_reigns, new_games, new_reigns):
    old_ids = {g["game_id"] for g in old_games}
    new_ids = {g["game_id"] for g in new_games}
    added = [g for g in new_games if g["game_id"] not in old_ids]
    removed = [g for g in old_games if g["game_id"] not in new_ids]
    old_by_id = {g["game_id"]: g for g in old_games}
    changed = [g for g in new_games if g["game_id"] in old_by_id and g != old_by_id[g["game_id"]]]

    def line(g):
        return (f"    {g['date']}  {g['away']} at {g['home']} {g['score']}  "
                f"[{g['outcome']}, holder {g['holder']} -> {g['new_holder']}]")

    print(f"\nBelt games: {len(old_games)} -> {len(new_games)}")
    for title, rows in (("Added", added), ("No longer belt games", removed),
                        ("Same game, different outcome/holder", changed)):
        if rows:
            print(f"  {title} ({len(rows)}):")
            for g in rows:
                print(line(g))

    # reigns: find the first and last index where the lists differ
    i = 0
    while i < min(len(old_reigns), len(new_reigns)) and old_reigns[i] == new_reigns[i]:
        i += 1
    j = 0
    while (j < min(len(old_reigns), len(new_reigns)) - i
           and old_reigns[-1 - j] == new_reigns[-1 - j]):
        j += 1
    print(f"\nClosed reigns: {len(old_reigns)} -> {len(new_reigns)} "
          f"(first difference at reign #{i + 1})")
    print("  Before:")
    for n, r in enumerate(old_reigns[i:len(old_reigns) - j], start=i + 1):
        print(f"    #{n} {reign_label(r)}")
    print("  After:")
    for n, r in enumerate(new_reigns[i:len(new_reigns) - j], start=i + 1):
        print(f"    #{n} {reign_label(r)}")
    shift = len(new_reigns) - len(old_reigns)
    if shift:
        print(f"  Every reign after that is unchanged but renumbered by {shift:+d}.")
    return changed


def drop_stale_game_text(game_ids, dry_run):
    """Remove cached notes/recaps for games whose belt outcome changed, so
    the next pipeline run rewrites them from the corrected facts."""
    ids = {str(g) for g in game_ids}
    for path in GAME_TEXT_CACHES:
        if not ids or not os.path.exists(path):
            continue
        with open(path) as f:
            cache = json.load(f)
        stale = sorted(k for k in ids if k in cache)
        if not stale:
            continue
        print(f"{path}: {'would drop' if dry_run else 'dropping'} {len(stale)} cached "
              f"entr{'y' if len(stale) == 1 else 'ies'} with outdated belt facts ({', '.join(stale)})")
        if dry_run:
            continue
        for k in stale:
            del cache[k]
        with open(path, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would change without writing baseline.json")
    args = ap.parse_args()

    if not os.path.exists(BASELINE_PATH):
        sys.exit(f"Can't find {BASELINE_PATH} -- run this from the project folder.")
    entries = load_entries()
    if not entries:
        sys.exit(f"No supplemental games found in {SUPPLEMENTAL_PATH}. Nothing to do.")

    with open(BASELINE_PATH) as f:
        baseline = json.load(f)
    lsy = baseline["live_start_year"]
    current = (baseline["historical_belt_games"], baseline["historical_reigns"],
               baseline["open_reign"])
    frozen = [e for e in entries if e["season"] < lsy]
    if len(frozen) < len(entries):
        print(f"{len(entries) - len(frozen)} supplemental game(s) are in the live "
              f"window (season >= {lsy}); build_lineage.py merges those itself.")

    print(f"Loading {ARCHIVE_PATH} (seasons 1869-{lsy - 1})...")
    archive_games = load_archive_games(lsy)
    if not archive_games or archive_games[0]["date"] != FIRST_GAME_DATE:
        sys.exit(f"Archive doesn't start at {FIRST_GAME_DATE} -- refusing to re-walk.")
    print(f"{len(archive_games):,} archived games.")

    merged = merge_supplemental(archive_games, seasons=range(1869, lsy))
    patched = rewalk(merged, lsy)
    if patched == current:
        print(f"{BASELINE_PATH} already includes the supplemental games. Nothing to do.")
        return

    # Safety check: the archive by itself must reproduce the baseline as it
    # stands now. If it doesn't, the baseline has drifted from the archive
    # (a hand edit, a different tie rule, a stale archive) and splicing a
    # fresh re-walk over it would silently change more than intended.
    if rewalk(archive_games, lsy) != current:
        sys.exit("STOP: re-walking the archive WITHOUT the supplemental games doesn't "
                 f"reproduce the current {BASELINE_PATH}, so it isn't safe to replace it "
                 "with a re-walk. Nothing was changed. (If the baseline already holds an "
                 "older version of the supplemental games, restore baseline.json from git "
                 "before that change and rerun.)")
    print("Check passed: the archive alone reproduces the current baseline exactly.")

    new_games, new_reigns, new_open = patched
    changed = describe_diff(current[0], current[1], new_games, new_reigns)
    if new_open != current[2]:
        print(f"\nOpen reign at the {lsy} boundary changes too:\n"
              f"    before: {current[2]}\n    after:  {new_open}")
    else:
        print(f"\nThe lineage rejoins the existing chain; the open reign at the "
              f"{lsy} boundary ({new_open['team']}) is unchanged.")

    print()
    drop_stale_game_text([g["game_id"] for g in changed], args.dry_run)
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    baseline["historical_belt_games"] = new_games
    baseline["historical_reigns"] = new_reigns
    baseline["open_reign"] = new_open
    with open(BASELINE_PATH, "w") as f:
        json.dump(baseline, f, indent=2)
    print(f"\nWrote {BASELINE_PATH}. Now run build_lineage.py (no --full-refetch "
          f"needed), then the rest of the pipeline -- or just update_all.py.")


if __name__ == "__main__":
    main()
