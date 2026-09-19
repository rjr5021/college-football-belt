#!/usr/bin/env python3
"""
The companion belts: one lineal belt per conference, plus the FBS-only and
FCS-only belts -- same winner-take rule as the real belt (build_lineage.py),
each restricted to the games that count for it.

2026-09-15, Bob: "then create belts for all FBS and FCS conferences" and
"do the same on the Full History and All Games tabs" (FBS Only / FCS Only).
Rewritten 2026-09-19 after a reader, Elliot, reported three things the
first version got wrong: every FBS/FCS reign said "Established the belt",
the FCS belt was a chain of one-day reigns, and North Dakota State had
somehow never held it. Two of those came from the same root cause -- the
old incremental machinery (committed baselines + vacancy files, extended
every run) let a vacated belt revert to a team that would never play again,
then vacate that team the next day, and so on around the same few programs
forever. See belt_engine.resolve_vacancies for the rule that replaced it.

How it works now:

  - EVERY run re-walks the whole history from scratch, from the committed
    archive of every game since 1869 (historical_data/all_games.json.gz,
    maintained by build_alternate_lineages.py) plus this run's freshly
    fetched seasons (belt_data/games_raw.json, from build_lineage.py).
    That's ~105,000 games, a few seconds of Python, zero extra API calls,
    and no state to go stale: nothing here is committed back.
  - Conference belts count games where BOTH sides were in that conference
    AT THE TIME (the per-game conference fields the data source reports),
    with pure renames folded together (Pac-8/10/12, Big 6/7/8, Yankee ->
    A-10 -> CAA -> Coastal Athletic, ...; classification.CONFERENCE_RENAMES).
  - The FBS belt is the real belt through the 1977 season, then only games
    between two FBS-conference members; the FCS belt starts with the first
    Division I-AA game of 1978. Subdivision is decided per game from the
    conference each side was in that season (classification.py), so a
    program that moves up or down takes its games with it.
  - Membership is read season by season from the conference label on EVERY
    game a team played (belt_engine.Membership), not inferred from whether
    it keeps turning up in the qualifying games. A holder that is still a
    member keeps the belt through any stretch without a qualifying game --
    Notre Dame is an independent whether or not it meets another one this
    year (a Reddit reader caught the old inference "vacating" it twice). A
    holder that LEFT (realignment, a move between subdivisions, a program
    going dark) vacates the belt as of its last game as a member, and the
    belt reverts to the most recent earlier holder that is a member of the
    season it left for. When no one can inherit it -- a conference that
    dissolved -- the belt RETIRES with its last real holder; the page shows
    the dissolution date (classification.CONFERENCE_DISSOLVED) instead of
    "present". A silence that crosses seasons the data source does not
    cover (FCS play before 2003) is a data gap, not a story: the belt
    retires there and a fresh lineage starts with the next game on record.

Outputs (belt_data/, regenerated every run, nothing committed):
    lineage_fbs.json, lineage_fcs.json          the FBS-only / FCS-only belts
    conferences/<slug>_lineage.json             one per conference

Needs no API key of its own. `--full-refetch` is accepted for compatibility
with the workflow's old bootstrap checkbox and does nothing.

Usage:
    python3 build_conference_lineage.py
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
from collections import Counter

from belt_engine import Membership, index_labels, resolve_vacancies_by_membership, walk_winner
from classification import (
    CONFERENCE_DISSOLVED,
    CONFERENCES,
    FBS,
    FCS_FIRST_SEASON,
    PRE_SPLIT_CONFERENCES,
    canonical_conference,
    classify,
    conference_classification,
    conference_counts,
    fcs_first_season,
    scope_games,
)

OUT_DIR = "belt_data"
HIST_DIR = "historical_data"
CONF_OUT_DIR = os.path.join(OUT_DIR, "conferences")
ARCHIVE_PATH = os.path.join(HIST_DIR, "all_games.json.gz")
ARCHIVE_FIELDS = ("id", "date", "season", "week", "season_type", "home", "away",
                  "home_points", "away_points", "neutral",
                  "home_conference", "away_conference", "venue_id")
MIN_CONFERENCE_GAMES = 12     # a conference needs this much qualifying history to get a belt
MIN_CONFERENCE_SEASONS = 2


def slugify(name):
    """Filesystem/URL-safe slug of a conference name, e.g.
    "Southeastern Conference" -> "southeastern-conference" -- the
    conferences/<slug>.html page and belt_data/conferences/<slug>_lineage.json."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "conference"


def lineage_path(slug):
    return os.path.join(CONF_OUT_DIR, f"{slug}_lineage.json")


def scope_lineage_path(scope):
    return os.path.join(OUT_DIR, f"lineage_{scope}.json")


# ------------------------------------------------------------- the games

def load_all_games(include_live=True):
    """Every game on record: the committed archive (seasons frozen through
    build_alternate_lineages.py's cutoff) plus this run's live seasons from
    games_raw.json, de-duplicated by season, with the hand-added games
    the data source lacks merged in. Chronological."""
    games = []
    if os.path.exists(ARCHIVE_PATH):
        with gzip.open(ARCHIVE_PATH, "rt", encoding="utf-8") as f:
            archive = json.load(f)
        for season in sorted(archive.get("seasons") or {}, key=int):
            for row in archive["seasons"][season]:
                games.append(dict(zip(ARCHIVE_FIELDS, row)))
    else:
        print(f"{ARCHIVE_PATH} not found -- the companion belts need the archive of every game "
              f"(tick 'bootstrap_alternate_universes' on a manual 'Run workflow' once).")
    live = []
    if include_live:
        try:
            from build_alternate_lineages import normalized_live_games
            live = normalized_live_games()
        except Exception as e:  # a missing games_raw.json (a run without build_lineage.py) is not fatal
            print(f"  no live seasons merged ({e})")
    live_seasons = {g["season"] for g in live}
    # a live season supersedes an archived copy of the same season
    games = [g for g in games if g["season"] not in live_seasons]
    for g in live:
        games.append(g)
    try:
        from supplemental_games import merge_supplemental
        games = merge_supplemental(games)
    except Exception as e:
        print(f"  supplemental games not merged ({e})")
    games = [g for g in games if g.get("home_points") is not None and g.get("away_points") is not None]
    games.sort(key=lambda g: (g["date"], g.get("season_type") != "regular", g.get("id") or 0))
    return games


def _major_before_split(name):
    """Whether a conference's games before the 1978 split can be trusted to
    be in the archive: the data source's coverage of major-college football
    is essentially complete, its coverage of the small colleges that became
    I-AA is thin -- so a league that was I-A at the split (or one of the
    pre-split majors) is covered, one that started I-AA is not."""
    if name in PRE_SPLIT_CONFERENCES:
        return True
    entry = CONFERENCES.get(name)
    if entry is None:
        return False
    if isinstance(entry, str):
        return entry == FBS
    return entry[0][2] == FBS


def conference_coverage(name, fcs_from):
    """season -> bool: is the archive complete enough for this conference's
    belt that season? FBS seasons always; FCS seasons only from the first
    season the data source actually covers I-AA/FCS play (`fcs_from`)."""
    def covered(season):
        if season >= FCS_FIRST_SEASON:
            return classify(name, season) == FBS or (fcs_from is not None and season >= fcs_from)
        return _major_before_split(name)
    return covered


def scope_membership(scope, index, fcs_from):
    """The FBS-only / FCS-only belt's world: a team is a member in a season
    when its conference was in that subdivision (one undivided Division I
    before 1978 counts as FBS)."""
    def member(label, season):
        if scope == FBS and season < FCS_FIRST_SEASON:
            return True
        return classify(canonical_conference(label), season) == scope
    covered = None if scope == FBS else (lambda s: fcs_from is not None and s >= fcs_from)
    return Membership.from_index(index, member, covered)


def dissolved_last_season(dissolved_date):
    """The last season a league that dissolved on `dissolved_date` played:
    an offseason date (June 30, Aug 30) closes the previous season."""
    y, m = int(dissolved_date[:4]), int(dissolved_date[5:7])
    return y if m >= 9 else y - 1


def conference_membership(name, index, fcs_from):
    """One conference's world: a team is a member in a season when its games
    that season carried the conference's label (renames folded together)."""
    def member(label, season):
        return canonical_conference(label) == name
    return Membership.from_index(index, member, conference_coverage(name, fcs_from))


# ------------------------------------------------------------- one belt

def walk_belt(games, membership, tie_rule, today, plain_through=None):
    """resolve_vacancies_by_membership over one belt's qualifying games,
    chronological. With `plain_through`, the seasons up to and including
    it are walked the way the real belt is -- no vacancies, the belt simply
    waits for its holder -- and the vacancy rules apply only after that
    (the FBS belt is the real belt until the 1978 split)."""
    if not games:
        return [], [], []
    if plain_through is not None:
        early = [g for g in games if g["season"] <= plain_through]
        late = [g for g in games if g["season"] > plain_through]
        if early:
            belt_games, reigns = walk_winner(early, tie_rule, first_game_date=early[0]["date"])
            if not late:
                return belt_games, reigns, []
            open_reign = reigns[-1]
            tail_games, tail_reigns, vacancies = resolve_vacancies_by_membership(
                late, tie_rule, start_holder=open_reign["team"], start_reign=open_reign,
                membership=membership, today=today, context_reigns=reigns[:-1],
                first_game_date=late[0]["date"])
            return belt_games + tail_games, reigns[:-1] + tail_reigns, vacancies
    belt_games, reigns, vacancies = resolve_vacancies_by_membership(
        games, tie_rule, start_holder=None, start_reign=None,
        membership=membership, today=today, first_game_date=games[0]["date"])
    return belt_games, reigns, vacancies


def lineage_doc(belt_games, reigns, vacancies, tie_rule, extra):
    current = reigns[-1]
    retired = bool(current.get("retired"))
    doc = {
        "generated": time.strftime("%Y-%m-%d"),
        "tie_rule": tie_rule,
        "origin": {"date": belt_games[0]["date"] if belt_games else None},
        "current_holder": current["team"],
        "current_reign_since": current["start_date"],
        "current_defenses": current["defenses"],
        "last_game_date": belt_games[-1]["date"] if belt_games else None,
        "retired": None,
        "totals": {
            "belt_games": len(belt_games),
            "reigns": len(reigns),
            "distinct_teams": len({r["team"] for r in reigns}),
            "vacancies": sum(1 for v in vacancies if v.get("reverted_to")),
        },
    }
    if retired:
        doc["retired"] = {"team": current["team"], "last_game_date": current["end_date"]}
    doc.update(extra)
    doc["reigns"] = reigns
    doc["belt_games"] = belt_games
    return doc


def build_scope(scope, games, index, fcs_from, tie_rule, today):
    qualifying = scope_games(games, scope)
    data_from = None
    if scope == "fcs":
        # the subdivision dates from 1978, the data source's coverage of it from ~2003
        data_from = fcs_from
        if data_from is None:
            return None
        qualifying = [g for g in qualifying if g["season"] >= data_from]
    if not qualifying:
        return None
    membership = scope_membership(scope, index, fcs_from)
    belt_games, reigns, vacancies = walk_belt(qualifying, membership, tie_rule, today,
                                              plain_through=FCS_FIRST_SEASON - 1 if scope == "fbs" else None)
    if scope == "fbs":
        note = ("Rutgers def. Princeton 6-4, first game ever played. Identical to the real belt "
                f"through the {FCS_FIRST_SEASON - 1} season; from {FCS_FIRST_SEASON} only games between "
                "two FBS-conference members count.")
    else:
        g0 = belt_games[0]
        note = (f"{g0['new_holder']} def. {g0['opponent']} {g0['score']}, the first FCS-vs-FCS game "
                f"of {data_from}, the first season the data source covers I-AA/FCS play "
                f"(the subdivision itself dates from {FCS_FIRST_SEASON}). Only games between two "
                f"FCS-conference members count.")
    doc = lineage_doc(belt_games, reigns, vacancies, tie_rule, {"scope": scope, "first_season": data_from or 1869})
    doc["origin"]["note"] = note
    return doc


def build_conference(name, games, index, fcs_from, tie_rule, today, latest_season=None):
    qualifying = [g for g in games
                  if canonical_conference(g.get("home_conference")) == name
                  and canonical_conference(g.get("away_conference")) == name
                  and conference_counts(name, g.get("season"))]
    seasons = sorted({g["season"] for g in qualifying})
    if len(qualifying) < MIN_CONFERENCE_GAMES or len(seasons) < MIN_CONFERENCE_SEASONS:
        return None
    classification = conference_classification(name, seasons)
    dissolved = CONFERENCE_DISSOLVED.get(name)
    membership = conference_membership(name, index, fcs_from)
    if dissolved:
        # a league that formally dissolved on a date: nothing played under its
        # label after that counts (the Rocky Mountain Conference lived on as a
        # small-college league; the SAIAA label lingers on 1921 games)
        through = dissolved_last_season(dissolved[0])
        qualifying = [g for g in qualifying if g["season"] <= through]
        seasons = [s for s in seasons if s <= through]
        if len(qualifying) < MIN_CONFERENCE_GAMES or len(seasons) < MIN_CONFERENCE_SEASONS:
            return None
        membership.clamp(through)
    belt_games, reigns, vacancies = walk_belt(qualifying, membership, tie_rule, today)
    if latest_season is None:
        latest_season = games[-1]["season"] if games else seasons[-1]
    if reigns and not reigns[-1].get("retired") and reigns[-1].get("end_date") is None \
            and (dissolved or membership.world_through < latest_season - 1):
        # the league is gone -- formally dissolved, or no games under its label for
        # two seasons -- and the belt retires with whoever held it at the end
        last = reigns[-1]
        last["end_date"] = last.get("last_game_date", last["start_date"])
        last["retired"] = True
    aliases = sorted({c for g in qualifying for c in (g.get("home_conference"), g.get("away_conference"))
                      if c and c != name})
    doc = lineage_doc(belt_games, reigns, vacancies, tie_rule, {
        "conference": name,
        "classification": classification,      # fbs / fcs / None (a conference from before the 1978 split)
        "former_names": aliases,
        "first_season": seasons[0], "last_season": seasons[-1],
    })
    doc["origin"]["note"] = f"First {name}-vs-{name} game on record"
    if doc["retired"]:
        if dissolved:
            doc["retired"]["dissolved_date"], doc["retired"]["note"] = dissolved
            # the final reign runs to the day the league dissolved, not just its last game
            last = reigns[-1]
            if dissolved[0] > last["end_date"]:
                last["end_date"] = dissolved[0]
        else:
            doc["retired"]["note"] = f"{name} last played a conference game in {seasons[-1]}"
    return doc


# ------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--end-year", type=int, default=time.localtime().tm_year)
    p.add_argument("--tie-rule", choices=["holder", "challenger"], default="holder")
    p.add_argument("--full-refetch", action="store_true",
                   help="accepted for compatibility with the old bootstrap checkbox; does nothing "
                        "(every run is a full rebuild from the archive now)")
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"), help="unused; kept for compatibility")
    p.add_argument("--no-live", action="store_true",
                   help="archive only, ignore belt_data/games_raw.json (offline testing)")
    args = p.parse_args()

    today = time.strftime("%Y-%m-%d")
    os.makedirs(CONF_OUT_DIR, exist_ok=True)

    games = load_all_games(include_live=not args.no_live)
    if not games:
        print("No games available -- skipping the companion belts this run.")
        return
    latest_season = max(g["season"] for g in games)
    live_start_year = latest_season - 1
    index = index_labels(games)
    fcs_from = fcs_first_season(games)
    print(f"{len(games):,} games on record ({games[0]['season']}-{latest_season}); "
          f"FCS-vs-FCS play covered from {fcs_from}")

    # ---- the FBS-only / FCS-only belts ----
    for scope in ("fbs", "fcs"):
        doc = build_scope(scope, games, index, fcs_from, args.tie_rule, today)
        if doc is None:
            print(f"[{scope}] no qualifying games -- skipped")
            continue
        with open(scope_lineage_path(scope), "w") as f:
            json.dump(doc, f, indent=2)
        cur = doc["reigns"][-1]
        print(f"[{scope}] {doc['totals']['belt_games']} belt games, {doc['totals']['reigns']} reigns, "
              f"{doc['totals']['distinct_teams']} programs; holder {cur['team']} since {cur['start_date']}"
              f"{' (retired)' if doc['retired'] else ''}")

    # ---- one belt per conference ----
    names = Counter()
    archived_names = {canonical_conference(c) for g in games if g["season"] < live_start_year
                      for c in (g.get("home_conference"), g.get("away_conference")) if c}
    drift = Counter()
    for g in games:
        for c in (g.get("home_conference"), g.get("away_conference")):
            c = canonical_conference(c)
            if c:
                names[c] += 1
                if g["season"] >= live_start_year and c not in archived_names:
                    drift[c] += 1
    if drift:
        # a conference label the data source only uses in the live seasons: usually a
        # renamed/merged league -- add it to classification.CONFERENCE_RENAMES if it is one
        print("  conference labels new in the live seasons (not in the archive): "
              + ", ".join(f"{c} ({n})" for c, n in drift.most_common()))
    built, skipped, stale = 0, 0, 0
    written = set()
    for name in sorted(names):
        doc = build_conference(name, games, index, fcs_from, args.tie_rule, today, latest_season)
        if doc is None:
            skipped += 1
            continue
        slug = slugify(name)
        with open(lineage_path(slug), "w") as f:
            json.dump(doc, f, indent=2)
        written.add(f"{slug}_lineage.json")
        built += 1
        cur = doc["reigns"][-1]
        tail = (f"retired with {cur['team']} after {cur['end_date']}" if doc["retired"]
                else f"holder {cur['team']} since {cur['start_date']}")
        print(f"[{slug}] {doc['classification'] or 'pre-1978'}: {doc['totals']['belt_games']} belt games, "
              f"{doc['totals']['reigns']} reigns, {doc['totals']['vacancies']} vacancies -- {tail}")
    # lineage files for names that no longer get a belt (renamed/merged) must not linger
    for fname in os.listdir(CONF_OUT_DIR):
        if fname.endswith("_lineage.json") and fname not in written:
            os.remove(os.path.join(CONF_OUT_DIR, fname))
            stale += 1
    print(f"Built {built} conference belt(s), skipped {skipped} with too little history"
          f"{f', removed {stale} stale file(s)' if stale else ''}.")


if __name__ == "__main__":
    main()
