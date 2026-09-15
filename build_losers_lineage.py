#!/usr/bin/env python3
"""
Build the Losers Belt lineage -- the mirror image of the real College
Football Belt (build_lineage.py): instead of passing to whoever BEATS the
holder, it passes to whoever LOSES to the holder. Think of it as a curse
instead of a crown -- you catch it by losing to the current worst team in
the country, not by beating the best one.

The exact rule, each time the holder plays a game:
    - Holder WINS  -> the opponent just lost to the worst team in the
                       country and catches the Losers Belt from them.
    - Holder LOSES -> nothing changes; still the reigning worst team.
    - TIE          -> same site-wide convention the real belt already
                       uses in production (holder retains on a tie) --
                       see build_lineage.py's own --tie-rule default.

It starts from the same historic anchor as the real belt -- the very
first college football game, Rutgers over Princeton, 1869-11-06 -- except
here PRINCETON (the loser) is who starts holding it, not Rutgers.

This deliberately reuses build_lineage.py's own CFBD-fetching, venue/date,
and baseline-splitting machinery (identical import, zero duplication of the
tricky parts) rather than re-implementing any of it -- only the belt-walk
itself (who takes it, and when) is different, so only that one function is
new.

Usage:
    export CFBD_API_KEY=your_key_here      # get one at collegefootballdata.com
    python3 build_losers_lineage.py

    python3 build_losers_lineage.py --full-refetch   # the one-time full
                                                      # 1869-now bootstrap
                                                      # (~316 CFBD calls) --
                                                      # see below.

Outputs (into ./belt_data/, regenerated fresh every run, not committed):
    losers_lineage.json   same shape as lineage.json, for the site's
                           losers-belt.html page to consume

CFBD call budget -- IMPORTANT, read before running unattended: exactly like
build_lineage.py, an ONGOING run only needs the current + previous season
(~4-6 calls) once a historical baseline exists. But unlike build_lineage.py,
this repo has never had a Losers Belt baseline before, so THE VERY FIRST
TIME this script runs anywhere, there is no historical_data/losers_baseline.json
to resume from. Rather than silently spending ~316 CFBD calls (the full
1869-now walk, same order of magnitude as build_lineage.py's own original
bootstrap) the first time this stage happens to run in CI, this script
REFUSES to do that automatically -- see main() below. It only does the
one-time historical walk when explicitly told to with --full-refetch (wired
by update-and-deploy.yml to a "bootstrap_losers_belt" checkbox on the
workflow's manual "Run workflow" button, same pattern as
fetch_game_details.py's --full-refetch / FULL_REFETCH_GAME_DETAILS). Until
that's run once, this script just skips itself and belt_data/losers_lineage.json
is never written -- build_site.py already knows to skip the Losers Belt page
entirely when that file doesn't exist, so the rest of the site is completely
unaffected either way.

Once bootstrapped, this behaves exactly like build_lineage.py from then on:
historical_data/losers_baseline.json freezes seasons as they age out of the
"current + previous" window, and every ordinary run (scheduled, pushed, or
manual) only spends the same ~4-6 calls build_lineage.py itself does. Don't
run the one-time bootstrap in the same month as another big one-time CFBD
operation (like fetch_game_details.py's own --full-refetch, ~600 calls) --
CFBD's free tier is 1,000 calls/MONTH, and stacking two ~300-600 call
one-offs in the same month can blow through that.

Defunct-program handling (historical_data/losers_vacancies.json): the
Losers Belt can only change hands when the holder WINS a game. That's fine
for an active program, but it means a holder that stops fielding a team at
all -- and a lot of this belt's holders are exactly the small/historic
programs that don't exist anymore, since it drifts toward whoever gets
blown out -- would hold it forever under the literal rule, with no way to
ever lose it back. Every run, after walking as far as the freshly-fetched
games allow, this script checks whether the CURRENT holder (only the live
tip -- a closed historical reign already ended via a real, dated game and
is never touched) has shown up in ANY game, any classification, in the
freshly-fetched current + previous season window. If not, they're treated
as having discontinued football, and the belt reverts to whoever they'd
caught it from -- walking back further if that team is ALSO absent (a
chain of defunct programs), and stopping at the very first (1869) reign if
it somehow comes to that. Because a team is only checked against the
current + previous season window, and that window only drops a team's
last game once a further season has fully passed, this in practice needs
roughly two full seasons of silence before it fires -- not a single quiet
offseason.

Crucially, a revert doesn't just freeze the belt on the previous holder
forever: it re-walks the SAME already-fetched games starting from that
team's reopened reign, so if THEY have real games sitting in that window
too, those get processed normally -- the belt keeps moving by real results
from there, exactly as if the dormant team had simply been skipped over.
(For a defunct program discovered deep in history, e.g. from 1899, this
means an ordinary incremental run -- which only ever fetches the current +
previous season -- can only pick the reverted team back up from THIS
season forward; it has no way to see the 100+ years of real games they
played in between, since those seasons were frozen into the historical
baseline long before this fix existed. Recovering that backlog needs a
fresh --full-refetch, which walks the true 1869-now history in one
continuous pass and so naturally re-derives every real result in between.)

The dormant team's reign is dated as VOIDED, not as having lasted until
whenever the pipeline happened to notice: it closes the same day it
started (a real, sourced event -- they genuinely caught the belt -- that
just never got the chance to stand for anything, since they never played
again to either defend or lose it), and the team it reverts to picks back
up the very next day, as though that one game never actually cost them the
belt. So if Wyoming Seminary caught it on 1899-09-23 and never fielded a
team again, their reign shows as 1899-09-23 to 1899-09-23 ("vacated"), and
Bucknell's reign resumes 1899-09-24 -- not from today, whenever a run
happens to catch it. Detected vacancies are appended once to
historical_data/losers_vacancies.json (committed, same pattern as
losers_baseline.json) purely as a running audit trail of what's been found
and when -- since a revert is fully baked into the reigns it produces (and
from there into historical_data/losers_baseline.json, via the vacancy-aware
split below), nothing is ever replayed FROM that file; it's write-only
bookkeeping for humans. (The `detected_on` field on each record is
likewise just an audit trail of when the pipeline first noticed -- never
used for any displayed date.)
"""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

from build_lineage import (
    FIRST_GAME_DATE,
    OUT_DIR,
    collect_venues,
    fetch_seasons,
    normalize,
    pick,
    split_at_season,
)

HIST_DIR = "historical_data"
BASELINE_PATH = os.path.join(HIST_DIR, "losers_baseline.json")
VACANCY_PATH = os.path.join(HIST_DIR, "losers_vacancies.json")
LINEAGE_PATH = os.path.join(OUT_DIR, "losers_lineage.json")


def walk_losers(games, tie_rule="holder", start_holder=None, start_reign=None):
    """Same shape and calling convention as build_lineage.py's walk(), and
    the SAME outcome vocabulary ("changed"/"retained"/"retained (tie)"/
    "lost (tie)"/"established") -- so split_at_season() (imported unchanged
    from build_lineage.py) works on this output with zero modification.
    The only real difference is which team "wins" a belt game: see the
    module docstring for the exact rule.

    When resuming (`start_holder` given), only games at or after
    `start_reign["start_date"]` are considered -- harmless for a normal
    resume (the caller's `games` is already scoped to the live window, all
    later than that date anyway), but required for resolve_vacancies()
    below, which re-walks a reverted holder's reopened reign against the
    SAME full games list a bootstrap walk already has in hand, and must
    not re-match any of that team's OWN earlier games from before this
    particular reign began.
    """
    games = [g for g in games if g["date"] >= FIRST_GAME_DATE]

    if start_holder is None:
        if not games:
            sys.exit("No games at or after the first-game date -- check the data pull.")
        first = games[0]
        if first["date"] != FIRST_GAME_DATE:
            print(f"WARNING: first game is {first['date']}, expected {FIRST_GAME_DATE}",
                  file=sys.stderr)
        # Mirror image of build_lineage.py's own bootstrap: the LOSER of
        # the very first game ever played starts holding the Losers Belt.
        holder = (first["away"] if first["home_points"] > first["away_points"]
                  else first["home"])
        reign = {"team": holder, "start_date": first["date"], "won_from": None,
                 "won_score": f"{first['home_points']}-{first['away_points']}",
                 "defenses": 0}
        remaining = games[1:]
        bootstrap_belt_game = {
            "date": first["date"], "season": first["season"], "week": first["week"],
            "season_type": first["season_type"], "holder": None,
            "opponent": (first["away"] if holder == first["home"] else first["home"]),
            "home": first["home"], "away": first["away"],
            "score": f"{first['home_points']}-{first['away_points']}",
            "neutral": first["neutral"], "outcome": "established",
            "new_holder": holder, "game_id": first["id"],
        }
    else:
        holder = start_holder
        reign = dict(start_reign)
        remaining = [g for g in games if g["date"] >= start_reign["start_date"]]
        bootstrap_belt_game = None

    belt_games, reigns = ([bootstrap_belt_game] if bootstrap_belt_game else []), []

    for g in remaining:
        if holder not in (g["home"], g["away"]):
            continue

        hp, ap = g["home_points"], g["away_points"]
        if hp == ap:
            # Same site-wide convention the real belt uses in production
            # (default --tie-rule=holder): a tie changes nothing.
            outcome = "retained (tie)" if tie_rule == "holder" else "lost (tie)"
            changed = tie_rule != "holder"
            new_holder = (g["away"] if holder == g["home"] else g["home"]) if changed else holder
        else:
            winner = g["home"] if hp > ap else g["away"]
            loser = g["away"] if winner == g["home"] else g["home"]
            # The one inversion from the real belt: the LOSER of the game
            # takes the Losers Belt. Holder loses again -> keeps it (still
            # the reigning worst team). Holder wins -> the team that just
            # lost TO the worst team in the country catches it from them.
            new_holder = loser
            changed = new_holder != holder
            outcome = "changed" if changed else "retained"

        opponent = g["away"] if holder == g["home"] else g["home"]
        belt_games.append({
            "date": g["date"], "season": g["season"], "week": g["week"],
            "season_type": g["season_type"], "holder": holder,
            "opponent": opponent, "home": g["home"], "away": g["away"],
            "score": f"{hp}-{ap}", "neutral": g["neutral"],
            "outcome": outcome, "new_holder": new_holder, "game_id": g["id"],
        })

        if changed:
            reign["end_date"] = g["date"]
            reign["lost_to"] = new_holder
            reigns.append(reign)
            reign = {"team": new_holder, "start_date": g["date"], "won_from": holder,
                     "won_score": f"{hp}-{ap}", "defenses": 0}
            holder = new_holder
        else:
            reign["defenses"] += 1

    reign["end_date"] = None
    reign["lost_to"] = None
    reigns.append(reign)
    return belt_games, reigns


def load_baseline():
    if not os.path.exists(BASELINE_PATH):
        return None
    with open(BASELINE_PATH) as f:
        return json.load(f)


def load_vacancies():
    if not os.path.exists(VACANCY_PATH):
        return []
    with open(VACANCY_PATH) as f:
        return json.load(f)


def save_vacancies(vacancies):
    os.makedirs(HIST_DIR, exist_ok=True)
    with open(VACANCY_PATH, "w") as f:
        json.dump(vacancies, f, indent=2)
    print(f"Wrote {VACANCY_PATH} ({len(vacancies)} recorded vacanc{'y' if len(vacancies) == 1 else 'ies'})")


def _predecessor(reign):
    """Whoever a reign's team caught the belt from -- `won_from` for a
    normal, game-based reign, or the explicit `predecessor` field for one
    that was reopened by a vacancy (see resolve_vacancies below). None for
    the very first (origin) reign, which has neither.

    This explicit pointer is what makes a CHAIN of vacancies (more than
    one defunct program in a row) revert to the right place. Once any
    vacancy has been applied, the entry immediately before a reign in a
    `reigns` list is no longer reliably its predecessor -- it might just
    be the closed, vacated reign that triggered this one reopening.
    """
    return reign.get("predecessor") or reign.get("won_from")


def resolve_vacancies(games, tie_rule, start_holder, start_reign, recent_teams,
                       today, context_reigns=()):
    """Walk `games` from (start_holder, start_reign) -- exactly like
    calling walk_losers() directly -- except that if the resulting tip has
    gone dormant (absent from `recent_teams`, see the module docstring),
    it doesn't just get left stuck there: the belt is reverted to whoever
    it was caught from, and `games` gets RE-WALKED starting from that
    team's reopened reign, so any real games they played while the
    dormant team was incorrectly "still holding" it get processed too,
    instead of silently discarded. Repeats for a chain of more than one
    defunct program, and stops at the very first (origin) reign if it
    somehow comes to that.

    `context_reigns` is optional extra history (e.g. the frozen
    historical_reigns from baseline) consulted only to look up a
    reverted-to team's OWN predecessor, for correctly chaining a further
    revert later -- see the `inherited` lookup below.

    Returns (belt_games, reigns, vacancies) -- `vacancies` is just the
    NEW reverts found this call (for the audit-trail log), not a
    replayed/cumulative list.
    """
    all_belt_games, all_reigns, vacancies = [], [], []
    holder, reign = start_holder, start_reign
    seen = set()

    while True:
        belt_games, reigns = walk_losers(games, tie_rule, start_holder=holder, start_reign=reign)
        tip = reigns[-1]
        predecessor = _predecessor(tip)

        if predecessor is None or tip["team"] in recent_teams or tip["team"] in seen:
            all_belt_games += belt_games
            all_reigns += reigns
            return all_belt_games, all_reigns, vacancies

        v = {"team": tip["team"], "reign_started": tip["start_date"],
             "effective_date": (date.fromisoformat(tip["start_date"])
                                 + timedelta(days=1)).isoformat(),
             "detected_on": today, "reverted_to": predecessor}
        print(f"Losers Belt: {v['team']} hasn't shown up in any game since "
              f"catching it on {v['reign_started']} -- treating their program "
              f"as having discontinued football. Voiding that reign and "
              f"reverting the belt to {v['reverted_to']}, in effect since "
              f"{v['effective_date']}.")
        vacated_tip = {**tip, "end_date": v["reign_started"], "lost_to": None,
                        "vacated": True}
        all_belt_games += belt_games
        all_reigns += reigns[:-1] + [vacated_tip]
        vacancies.append(v)
        seen.add(tip["team"])

        inherited = None
        for r in reversed(list(context_reigns) + all_reigns):
            if r["team"] == predecessor:
                inherited = _predecessor(r)
                break
        holder = predecessor
        reign = {"team": predecessor, "start_date": v["effective_date"],
                 "won_from": None, "won_score": None, "defenses": 0,
                 "reclaimed_after": v["team"], "predecessor": inherited}


def split_losers_baseline(belt_games, all_vacancies, first_reign, live_start_year):
    """Vacancy-aware counterpart to build_lineage.py's own split_at_season.
    That function alone would silently lose a vacancy-driven transition
    when freezing history, since it reconstructs everything purely by
    replaying real belt_games -- and a vacancy has no belt game of its
    own. This does the same job (freeze everything before
    `live_start_year` into closed reigns, return the reign that's open as
    of that boundary for resuming) but replays vacancy events in their
    correct chronological position too, so a defunct-program revert deep
    in history stays correctly reflected in the frozen baseline forever,
    not just in this run's own output.

    `all_vacancies` should be every vacancy on record (persisted plus any
    newly found this run). Returns (historical_belt_games,
    historical_reigns, open_reign) -- same shape as split_at_season.
    """
    historical_belt_games = [bg for bg in belt_games if bg["season"] < live_start_year]
    boundary = f"{live_start_year}-01-01"
    historical_vacancies = [v for v in all_vacancies if v["reign_started"] < boundary]

    events = [("game", bg["date"], bg) for bg in historical_belt_games]
    events += [("vacancy", v["reign_started"], v) for v in historical_vacancies]
    events.sort(key=lambda e: e[1])

    if not events:
        open_reign = {"team": first_reign["team"], "start_date": first_reign["start_date"],
                      "won_from": first_reign.get("won_from"),
                      "won_score": first_reign.get("won_score"), "defenses": 0,
                      "predecessor": first_reign.get("predecessor")}
        return [], [], open_reign

    closed = []
    reign = {"team": first_reign["team"], "start_date": first_reign["start_date"],
             "won_from": first_reign.get("won_from"),
             "won_score": first_reign.get("won_score"), "defenses": 0,
             "predecessor": first_reign.get("predecessor")}
    for kind, _, e in events:
        if kind == "game":
            bg = e
            if bg["outcome"] == "established":
                # The bootstrap game itself -- already fully reflected in
                # the seed `reign` above. Not a defense, don't double-count.
                continue
            if bg["outcome"] in ("changed", "lost (tie)"):
                reign["end_date"] = bg["date"]
                reign["lost_to"] = bg["new_holder"]
                closed.append(reign)
                reign = {"team": bg["new_holder"], "start_date": bg["date"],
                         "won_from": bg["holder"], "won_score": bg["score"], "defenses": 0}
            else:
                reign["defenses"] += 1
        else:
            v = e
            if reign["team"] != v["team"]:
                # Shouldn't happen if events are truly chronological, but
                # don't crash the pipeline over a bookkeeping mismatch --
                # just skip it and note it for investigation.
                print(f"WARNING: vacancy record for {v['team']!r} doesn't match "
                      f"the reign open at that point ({reign['team']!r}) -- "
                      f"skipping it in the historical split.", file=sys.stderr)
                continue
            reign["end_date"] = v["reign_started"]
            reign["lost_to"] = None
            reign["vacated"] = True
            closed.append(reign)
            inherited = None
            for r in reversed(closed):
                if r["team"] == v["reverted_to"]:
                    inherited = _predecessor(r)
                    break
            reign = {"team": v["reverted_to"], "start_date": v["effective_date"],
                     "won_from": None, "won_score": None, "defenses": 0,
                     "reclaimed_after": v["team"], "predecessor": inherited}
    return historical_belt_games, closed, reign


def save_baseline(historical_belt_games, historical_reigns, open_reign, live_start_year):
    os.makedirs(HIST_DIR, exist_ok=True)
    with open(BASELINE_PATH, "w") as f:
        json.dump({
            "live_start_year": live_start_year,
            "historical_belt_games": historical_belt_games,
            "historical_reigns": historical_reigns,
            "open_reign": open_reign,
        }, f, indent=2)
    print(f"Wrote {BASELINE_PATH} (historical through season {live_start_year - 1}, "
          f"{len(historical_belt_games)} losers-belt games / {len(historical_reigns)} "
          f"closed reigns frozen)")


def write_outputs(belt_games, reigns, tie_rule):
    current = reigns[-1]
    with open(LINEAGE_PATH, "w") as f:
        json.dump({
            "generated": time.strftime("%Y-%m-%d"),
            "tie_rule": tie_rule,
            "origin": {"date": FIRST_GAME_DATE,
                       "note": "Princeton lost to Rutgers 4-6, the first game ever "
                               "played -- the first team to come up short, and so "
                               "the first to hold the Losers Belt."},
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
    print(f"Wrote {LINEAGE_PATH} ({len(belt_games)} losers-belt games, "
          f"{len(reigns)} reigns)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-year", type=int, default=1869)
    p.add_argument("--end-year", type=int, default=time.localtime().tm_year)
    p.add_argument("--tie-rule", choices=["holder", "challenger"], default="holder",
                   help="who keeps the Losers Belt on a tie (default: holder retains, "
                        "matching the real belt's own production default)")
    p.add_argument("--full-refetch", action="store_true",
                   help="do the one-time full 1869-now walk (~316 CFBD calls) -- "
                        "required the very first time this ever runs (no baseline "
                        "exists yet), also usable later for a genuine from-scratch "
                        "rebuild")
    p.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = p.parse_args()

    if not args.key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                 "Free key at https://collegefootballdata.com/key")

    os.makedirs(OUT_DIR, exist_ok=True)

    baseline = None if args.full_refetch else load_baseline()
    live_start_year = args.end_year - 1
    today = time.strftime("%Y-%m-%d")

    if baseline is None and not args.full_refetch:
        print("No historical_data/losers_baseline.json yet, and the one-time "
              "historical bootstrap wasn't requested -- skipping the Losers Belt "
              "this run (belt_data/losers_lineage.json won't be written, so "
              "build_site.py just won't render losers-belt.html yet; nothing else "
              "is affected). Tick the 'bootstrap_losers_belt' checkbox on a manual "
              "'Run workflow' to build it once (~316 CFBD calls, one-time).")
        return

    print("Fetching venue timezones from CFBD...")
    venue_tz, _venue_info = collect_venues(args.key)

    if baseline is None:
        print(f"Doing the one-time full {args.start_year}-{args.end_year} Losers "
              f"Belt walk from CFBD (~{2 * (args.end_year - args.start_year + 1)} calls).")
        raw = fetch_seasons(range(args.start_year, args.end_year + 1), args.key)
        games = normalize(raw, venue_tz)
        print(f"{len(games)} completed games in chronological order")
        recent_teams = {g["home"] for g in games if g["season"] >= live_start_year} | \
                       {g["away"] for g in games if g["season"] >= live_start_year}
        belt_games, reigns, new_vacancies = resolve_vacancies(
            games, args.tie_rule, start_holder=None, start_reign=None,
            recent_teams=recent_teams, today=today)
        context_reigns = []
    else:
        fetch_from = min(baseline["live_start_year"], live_start_year)
        seasons = range(fetch_from, args.end_year + 1)
        print(f"Losers Belt baseline found (through season {fetch_from - 1}, "
              f"{len(baseline['historical_belt_games'])} losers-belt games already "
              f"settled) -- fetching only seasons {list(seasons)} fresh "
              f"(~{2 * len(list(seasons))} calls).")
        raw = fetch_seasons(seasons, args.key)
        games = normalize(raw, venue_tz)
        print(f"{len(games)} completed games in the live window")
        recent_teams = {g["home"] for g in games if g["season"] >= live_start_year} | \
                       {g["away"] for g in games if g["season"] >= live_start_year}
        tail_belt_games, tail_reigns, new_vacancies = resolve_vacancies(
            games, args.tie_rule,
            start_holder=baseline["open_reign"]["team"],
            start_reign=baseline["open_reign"],
            recent_teams=recent_teams, today=today,
            context_reigns=baseline["historical_reigns"])
        belt_games = baseline["historical_belt_games"] + tail_belt_games
        reigns = baseline["historical_reigns"] + tail_reigns
        context_reigns = baseline["historical_reigns"]

    all_vacancies = load_vacancies()
    if new_vacancies:
        existing_keys = {(v["team"], v["reign_started"]) for v in all_vacancies}
        to_add = [v for v in new_vacancies
                  if (v["team"], v["reign_started"]) not in existing_keys]
        if to_add:
            all_vacancies = all_vacancies + to_add
            save_vacancies(all_vacancies)

    write_outputs(belt_games, reigns, args.tie_rule)

    hist_belt_games, hist_reigns, open_reign = split_losers_baseline(
        belt_games, all_vacancies, reigns[0], live_start_year)
    save_baseline(hist_belt_games, hist_reigns, open_reign, live_start_year)

    current = reigns[-1]
    print(f"\nLosers Belt current holder: {current['team']} since "
          f"{current['start_date']} ({current['defenses']} defenses)")


if __name__ == "__main__":
    main()
