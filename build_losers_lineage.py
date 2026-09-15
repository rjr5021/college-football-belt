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
ever lose it back. Every run, after the walk above, this script checks
whether the CURRENT holder (only the live tip -- a closed historical reign
already ended via a real, dated game and is never touched) appears in ANY
game, any classification, across this run's freshly-fetched current +
previous season data. If they don't, they're treated as having
discontinued football, and the belt reverts to whoever they'd caught it
from -- walking back further if that team is ALSO absent (a chain of
defunct programs), and stopping at the very first (1869) reign if it
somehow comes to that. Because a team is only checked against the current
+ previous season window, and that window only drops a team's last game
once a further season has fully passed, this in practice needs roughly two
full seasons of silence before it fires -- not a single quiet offseason.

The dormant team's reign is dated as VOIDED, not as having lasted until
whenever the pipeline happened to notice: it closes the same day it
started (a real, sourced event -- they genuinely caught the belt -- that
just never got the chance to stand for anything, since they never played
again to either defend or lose it), and the team it reverts to picks back
up the very next day, as though that one game never actually cost them the
belt. So if Wyoming Seminary caught it on 1899-09-23 and never fielded a
team again, their reign shows as 1899-09-23 to 1899-09-23 ("vacated"), and
Bucknell's reign resumes 1899-09-24 -- not from today, whenever a run
happens to catch it. Detected vacancies are recorded once in
historical_data/losers_vacancies.json (committed, same pattern as
losers_baseline.json) and replayed identically on every future run, so
these dates never drift just because the pipeline happened to run again.
(The `detected_on` field on each record is only an audit trail of when the
pipeline first noticed -- it's never used for any displayed date.)
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
        remaining = games
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
    that was reopened by a vacancy (see apply_vacancies below). None for
    the very first (origin) reign, which has neither.

    This explicit pointer is what makes a CHAIN of vacancies (more than
    one defunct program in a row) revert to the right place. Once any
    vacancy has been applied, the entry immediately before a reign in the
    `reigns` list is no longer reliably its predecessor -- it might just
    be the closed, vacated reign that triggered this one reopening.
    """
    return reign.get("predecessor") or reign.get("won_from")


def apply_vacancies(reigns, vacancies):
    """Replay already-recorded vacancy events (see module docstring) onto a
    freshly-walked `reigns` list. Each vacancy always applies to whatever is
    currently the open (last) reign -- that's the only place one can ever
    happen, since a closed reign already ended via a real game -- so this
    just walks the list in recorded order, voiding the dormant team's reign
    (closed the same day it started -- see the module docstring for why)
    and reopening the team it reverts to starting the very next day. Both
    dates come from the vacancy record itself, fixed at detection time and
    never today's date, so a reign's dates stay stable no matter how many
    times this runs.
    """
    reigns = list(reigns)
    for v in vacancies:
        tip = reigns[-1]
        if tip["team"] != v["team"]:
            print(f"WARNING: recorded vacancy for {v['team']!r} doesn't match "
                  f"the current tip {tip['team']!r} -- skipping it (the "
                  f"recorded facts may have changed upstream).", file=sys.stderr)
            continue
        reigns[-1] = {**tip, "end_date": v["reign_started"], "lost_to": None,
                       "vacated": True}
        # The reopened reign's predecessor is v["reverted_to"]'s OWN prior
        # predecessor (found from the last time THEY held it) -- NOT the
        # vacating team's predecessor, which is a different thing (that's
        # just v["reverted_to"] itself). Needed so a second vacancy on this
        # same reopened reign reverts to the right team. See _predecessor().
        inherited = None
        for r in reversed(reigns[:-1]):
            if r["team"] == v["reverted_to"]:
                inherited = _predecessor(r)
                break
        reigns.append({"team": v["reverted_to"], "start_date": v["effective_date"],
                        "won_from": None, "won_score": None, "defenses": 0,
                        "end_date": None, "lost_to": None,
                        "reclaimed_after": v["team"], "predecessor": inherited})
    return reigns


def detect_new_vacancy(reigns, recent_teams, today):
    """Check ONLY the live tip against `recent_teams` (every team that's
    appeared, any classification, in this run's current + previous season
    fetch). Returns a new vacancy record if the tip's team is absent and
    there's a previous reign to revert to, else None. Call in a loop (see
    main()) to walk back through a chain of more than one defunct program.

    `today` is recorded only as an audit trail (`detected_on`) of when the
    pipeline first noticed -- every date that actually gets DISPLAYED
    (reign_started/effective_date) comes from the dormant team's own real
    reign, not from whenever this happens to run. See apply_vacancies.
    """
    tip = reigns[-1]
    reverted_to = _predecessor(tip)
    if reverted_to is None:
        return None  # the origin reign -- nothing to revert to
    if tip["team"] in recent_teams:
        return None
    reign_started = tip["start_date"]
    effective_date = (date.fromisoformat(reign_started) + timedelta(days=1)).isoformat()
    return {"team": tip["team"], "reign_started": reign_started,
            "effective_date": effective_date, "detected_on": today,
            "reverted_to": reverted_to}


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
        belt_games, reigns = walk_losers(games, args.tie_rule)
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
        new_belt_games, tail_reigns = walk_losers(
            games, args.tie_rule,
            start_holder=baseline["open_reign"]["team"],
            start_reign=baseline["open_reign"],
        )
        belt_games = baseline["historical_belt_games"] + new_belt_games
        reigns = baseline["historical_reigns"] + tail_reigns

    # Defunct-program handling -- see the module docstring. Replay whatever
    # vacancies are already on record (fixed dates, never re-derived), then
    # check whether the live tip has newly gone dark, walking back through
    # a chain of more than one defunct program if needed.
    vacancies = load_vacancies()
    reigns = apply_vacancies(reigns, vacancies)

    recent_teams = {g["home"] for g in games if g["season"] >= live_start_year} | \
                   {g["away"] for g in games if g["season"] >= live_start_year}
    today = time.strftime("%Y-%m-%d")
    new_vacancies = []
    while True:
        v = detect_new_vacancy(reigns, recent_teams, today)
        if v is None:
            break
        print(f"Losers Belt: {v['team']} hasn't shown up in any game since "
              f"catching it on {v['reign_started']} -- treating their program "
              f"as having discontinued football. Voiding that reign and "
              f"reverting the belt to {v['reverted_to']}, in effect since "
              f"{v['effective_date']}.")
        reigns = apply_vacancies(reigns, [v])
        new_vacancies.append(v)
    if new_vacancies:
        save_vacancies(vacancies + new_vacancies)

    write_outputs(belt_games, reigns, args.tie_rule)

    hist_belt_games, hist_reigns, open_reign = split_at_season(
        belt_games, reigns, live_start_year)
    save_baseline(hist_belt_games, hist_reigns, open_reign, live_start_year)

    current = reigns[-1]
    print(f"\nLosers Belt current holder: {current['team']} since "
          f"{current['start_date']} ({current['defenses']} defenses)")


if __name__ == "__main__":
    main()
