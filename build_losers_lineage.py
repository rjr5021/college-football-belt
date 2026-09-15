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

Division 1 restriction (Losers Belt only -- NOT applied to the real belt):
unlike the real belt, this one is restricted to programs CFBD currently
classifies as "fbs" or "fcs" -- NCAA Division 1 -- via
filter_division1_games() / fetch_division1_teams() (the latter in
build_lineage.py). A game where either side isn't a current Division 1
program is dropped before the walk ever sees it, same as if it never
happened. Two reasons: (1) CFBD's game data for this belt is pulled across
"any classification" (see below), which includes prep schools and D-II/D-III
programs that occasionally show up in the earliest era of college football
-- Wyoming Seminary, a prep school, is a real example that used to hold this
belt for 13 years; (2) those same small/non-D1 programs tend to have far
sparser historical coverage in CFBD, which was producing wildly implausible
"reigns" -- Bloomsburg (D-II) once showed an unbroken 101-year reign that
was really just an 87-year hole in CFBD's own data (1919 to 2006) with no
recorded games at all in between, not 101 years of real dominance. This is
CFBD's CURRENT classification applied uniformly across all of history, not
a season-by-season one -- the FBS/FCS split didn't exist before 1978, so
there's nothing historically meaningful to apply before then anyway.

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

Defunct-program / mid-reign gap handling (historical_data/losers_vacancies.json):
the Losers Belt can only change hands when the holder WINS a game. That's
fine for an active program, but it means a holder that stops fielding a
team at all -- and a lot of this belt's holders are exactly the
small/historic programs that don't exist anymore, since it drifts toward
whoever gets blown out -- would hold it forever under the literal rule,
with no way to ever lose it back. There are two ways this shows up, and a
single unified mechanism (walk_losers's `gap_threshold_days` +
resolve_vacancies's `has_gap` check, both above) catches both:

  - TERMINAL dormancy: the CURRENT holder (the live tip -- a closed
    historical reign already ended via a real, dated game and is never
    touched) hasn't shown up in ANY game, any classification, in the
    freshly-fetched current + previous season window. Because a team is
    only checked against that window, and the window only drops a team's
    last game once a further season has fully passed, this in practice
    needs roughly two full seasons of silence before it fires -- not a
    single quiet offseason.
  - IN-REIGN gap, ANYWHERE in history: a team goes quiet for more than
    GAP_THRESHOLD_DAYS (700 days -- comfortably wider than a normal
    season-to-season gap, or even a single skipped season) and only later
    shows up again in a real game. This is deliberately the SAME
    mechanism as the Division 1 restriction's own effect: a team that
    temporarily drops out of Division 1 partway through history has its
    non-D1-era games filtered out entirely by filter_division1_games
    before the walk ever runs, which produces exactly this same shape --
    a real game, then a long silence in the (already-filtered) data, then
    a real game again -- so "give the belt back to whoever it belonged to
    before a team dropped from D1" falls straight out of the general gap
    check with no extra CFBD calls or historical classification data
    needed. It's also what catches an implausibly long, mostly-inactive
    reign that isn't really about Division 1 status at all -- e.g. a
    small program that just has a multi-decade hole in CFBD's own
    coverage (see Bloomsburg above) rather than 40+ years of genuinely
    unbroken dominance.

Either way, once a tip needs reverting, the belt goes back to whoever it
was caught from -- walking back further if that team is ALSO
defunct/gapped (a chain of reverts), and stopping at the very first (1869)
reign if it somehow comes to that.

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
whenever the pipeline happened to notice, and NOT blindly frozen to the day
they first caught it either: it closes on their TRUE last recorded game --
tracked via each reign's `last_game_date`, updated on every real defense,
not just the catch -- and the team it reverts to picks back up the very
next day. If a team caught the belt and simply never played again (zero
real defenses), `last_game_date` never moves past the catch date, so their
reign correctly shows as voided the same day it started -- e.g. if Wyoming
Seminary caught it on 1899-09-23 and never fielded a team again, their
reign shows as 1899-09-23 to 1899-09-23 ("vacated"), and Bucknell's reign
resumes 1899-09-24. But if a team caught the belt, genuinely defended it
for real one or more times (real losses, real dates, real games), and only
THEN stopped fielding a team, their reign closes on that true last game
instead -- so a team with, say, 11 recorded losses before going dark never
shows a self-contradictory 0-day reign alongside those 11 defenses. Either
way, the reverted-to team resumes the day after the dormant team's true
last activity, not from today, whenever a run happens to catch it.
Detected vacancies are appended once to
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
    fetch_division1_teams,
    fetch_seasons,
    normalize,
    pick,
    split_at_season,
)

HIST_DIR = "historical_data"
BASELINE_PATH = os.path.join(HIST_DIR, "losers_baseline.json")
VACANCY_PATH = os.path.join(HIST_DIR, "losers_vacancies.json")
LINEAGE_PATH = os.path.join(OUT_DIR, "losers_lineage.json")

# Was 700 days ("roughly two full seasons of silence", matching the
# ORIGINAL terminal-only dormancy check's current+previous-season window).
# Lowered to 500 on purpose, after confirming the mid-reign gap check's
# own fallback for un-revertable reigns (see resolve_vacancies) had a
# separate bug that silently disabled gap-awareness for the rest of
# history once triggered -- which is what actually let multi-thousand-day
# gaps like Abilene Christian's slip through even at 700. That deeper bug
# is fixed now (see the skip_first_gap forgive-one-gap-at-a-time loop),
# but a lower threshold is still worth having on top of the fix: it
# catches shorter, subtler silences (well under "thousands of days") that
# 700 was too loose to flag at all, at the accepted cost of a higher
# chance of a false-positive short "flash revert" on a genuinely
# irregular-but-real schedule -- e.g. a single skipped season (last game
# of one season to first game two seasons later can run ~550-650 days)
# may now trip this where it wouldn't have at 700. Every resulting
# reign was manually reviewed for plausibility before this shipped.
GAP_THRESHOLD_DAYS = 500

# That manual review missed a whole CATEGORY of false positives: two
# real, well-documented, nationwide interruptions to the college football
# schedule, where a large fraction of programs suspended or drastically
# curtailed play for one or more seasons. Neither is a hole in CFBD's own
# coverage or a program going dormant -- it's what the sport itself
# looked like that year -- but at 500 days (and even at the old 700) a
# LOT of teams' real last game before the interruption sits more than
# GAP_THRESHOLD_DAYS from their real first game after it, and because
# resolve_vacancies() reverts unconditionally the moment walk_losers
# reports a gap (see its own docstring), that produced a visible cascade
# of dozens of same-week reigns each immediately vacated to the next --
# exactly the pattern a human reviewer flags on sight as noise, not
# signal. Reported directly (Bob, 2026-09-15: "ignore all reverts around
# WW2 and COVID").
#
# Fix: widen (never fully disable -- a program that goes quiet AT THE
# START of one of these windows and simply never comes back, e.g. drops
# to non-D1 or shuts down its program mid-war, is still a real, revertable
# gap once the silence runs past DISRUPTION_GAP_THRESHOLD_DAYS) the
# threshold for any gap whose span overlaps one of these windows at all.
DISRUPTION_WINDOWS = [
    # WWII: many programs suspended football for one or more full
    # seasons -- 1943 especially, the low point -- as rosters emptied out
    # to enlistment; normal nationwide play had resumed by 1946. A team
    # silent from its last 1942-season game to its first 1946-season game
    # is not a data gap.
    ("1941-09-01", "1946-09-01"),
    # COVID-19: the 2020 season was postponed, shortened, played
    # conference-only, or (many FCS programs) moved wholesale to spring
    # 2021 -- schedules that don't remotely resemble a normal season's
    # game-to-game cadence. Normal fall play was back across Division I
    # by the 2021 season.
    ("2019-12-01", "2021-09-01"),
]
# ~3.3 seasons' worth of silence -- comfortably covers a program that
# lost multiple consecutive WWII seasons (e.g. last game in 1942, first
# game back in 1946) while still catching a gap that runs well past the
# disruption window's own end, which is exactly what a genuine permanent
# dropout starting during the war looks like.
DISRUPTION_GAP_THRESHOLD_DAYS = 1500


def _disrupted_era_overlap(start_date, end_date):
    """True if the [start_date, end_date] span (a reign's last real game
    through the next candidate game) overlaps any DISRUPTION_WINDOWS
    entry at all. Overlap, not containment -- a gap that starts before a
    window and ends inside it (or vice versa) is exactly the shape a real
    war/pandemic-interrupted reign takes."""
    s, e = date.fromisoformat(start_date), date.fromisoformat(end_date)
    return any(s <= date.fromisoformat(w_end) and e >= date.fromisoformat(w_start)
               for w_start, w_end in DISRUPTION_WINDOWS)


def filter_division1_games(games, d1_teams):
    """Keep only games where BOTH participants are current Division 1
    (FBS/FCS) programs -- see fetch_division1_teams's docstring (in
    build_lineage.py) for the full reasoning. A game involving even one
    non-D1 opponent (a prep school, a D-II/D-III program, anything CFBD's
    "any classification" game pull picks up that isn't a Division 1
    school today) is dropped entirely, exactly as if it never happened
    for Losers Belt purposes -- the D1 side of that matchup is otherwise
    unaffected via its other, all-D1 games. This is what keeps a program
    like Bloomsburg (D-II, and hence also very sparsely covered by CFBD
    across most of the 20th century -- see the module docstring) from
    ever entering the lineage and producing a multi-decade "reign" that's
    really just a hole in the source data.
    """
    return [g for g in games if g["home"] in d1_teams and g["away"] in d1_teams]


def walk_losers(games, tie_rule="holder", start_holder=None, start_reign=None,
                 gap_threshold_days=None, skip_first_gap=False):
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

    `gap_threshold_days`, when given, stops the walk early -- BEFORE
    processing a game -- the moment the gap since the reign's last real
    activity (`last_game_date`) would exceed it. This is what lets
    resolve_vacancies() below catch a too-long silence ANYWHERE in
    history, not just at the live tip: the stop leaves a real, later game
    for this same team sitting unprocessed in the remaining games, which
    is exactly the signal resolve_vacancies() uses to tell "this was a
    genuine in-reign gap" apart from "we've simply run out of data so
    far." None of the existing callers (tests, and any plain walk) pass
    this, so the default (no gap awareness at all) is unchanged.

    Whatever `gap_threshold_days` is passed, it's automatically widened
    to DISRUPTION_GAP_THRESHOLD_DAYS for any gap whose span overlaps a
    DISRUPTION_WINDOWS entry (WWII, COVID-19) -- see that constant's own
    comment. This applies uniformly regardless of caller, so it can't be
    bypassed by a re-walk the way the old gap-awareness-disabling
    fallback could.

    `skip_first_gap`, when True, forgives exactly the FIRST over-threshold
    gap this call would otherwise stop on: that one game is processed
    normally (crediting the silence as an unusually long but real
    defense) and the flag is immediately cleared, so any LATER gap later
    in this same call still stops the walk as usual. This is deliberately
    narrower than passing `gap_threshold_days=None` (which switches gap
    awareness off for the rest of the call, however much history remains)
    -- it exists so resolve_vacancies() below can forgive one
    un-revertable gap at a time while keeping gap detection fully armed
    for everything downstream.
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
                 "defenses": 0, "last_game_date": first["date"]}
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
        reign.setdefault("last_game_date", reign["start_date"])
        remaining = [g for g in games if g["date"] >= start_reign["start_date"]]
        bootstrap_belt_game = None

    belt_games, reigns = ([bootstrap_belt_game] if bootstrap_belt_game else []), []

    for g in remaining:
        if holder not in (g["home"], g["away"]):
            continue

        if gap_threshold_days is not None:
            gap = (date.fromisoformat(g["date"])
                   - date.fromisoformat(reign["last_game_date"])).days
            effective_threshold = gap_threshold_days
            if _disrupted_era_overlap(reign["last_game_date"], g["date"]):
                effective_threshold = max(effective_threshold, DISRUPTION_GAP_THRESHOLD_DAYS)
            if gap > effective_threshold:
                if skip_first_gap:
                    # Forgive exactly this one over-threshold gap -- the
                    # caller already knows this reign can't be reverted
                    # right now (no predecessor, or already reverted once
                    # before) and is deliberately asking us to credit this
                    # specific silence as a real defense and keep going,
                    # WITHOUT switching off gap-awareness for the rest of
                    # the walk. Clear the flag so any FURTHER gap later in
                    # this same call still stops it as usual -- only the
                    # first one is forgiven.
                    skip_first_gap = False
                else:
                    # A real, later game for this holder exists (this one)
                    # -- but only after a suspiciously long silence. Stop
                    # here rather than silently crediting it as an
                    # unbroken defense; leave it (and everything after)
                    # unprocessed for the caller to pick up from a
                    # reopened predecessor.
                    break

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
                     "won_score": f"{hp}-{ap}", "defenses": 0, "last_game_date": g["date"]}
            holder = new_holder
        else:
            reign["defenses"] += 1
            reign["last_game_date"] = g["date"]

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
    calling walk_losers() directly -- except that whenever the resulting
    tip needs reverting, it doesn't just get left stuck there: the belt is
    reverted to whoever it was caught from, and `games` gets RE-WALKED
    starting from that team's reopened reign, so any real games they
    played while the dormant/gapped team was incorrectly "still holding"
    it get processed too, instead of silently discarded. Repeats for a
    chain of more than one defunct/gapped program, and stops at the very
    first (origin) reign if it somehow comes to that.

    A tip needs reverting for either of two reasons, checked every pass:
      - IN-REIGN GAP: walk_losers (called with GAP_THRESHOLD_DAYS) stopped
        early because a real, LATER game for this same team exists in
        `games`, just too far past their last activity to credit as an
        unbroken defense. Proven by that later game's own presence in
        `games` -- this is what catches a too-long silence ANYWHERE in
        history, e.g. a program that stopped fielding a team for years
        (or even permanently dropped out of Division 1, since a non-D1
        team's games are already filtered out entirely before this ever
        runs -- see filter_division1_games) and only later resumed
        Division 1 play, not just a team that's dormant as of today.
      - TERMINAL dormancy: we've simply run out of fetched games (the live
        edge) and the team hasn't shown up in the current+previous season
        window (`recent_teams`) -- the original check, unchanged, for
        when there's no later game (yet) to prove a gap either way.
    Because this fires on ANY reign a pass produces -- not only the very
    last one in history -- a single bootstrap walk (start_holder=None)
    naturally finds and fixes the EARLIEST such issue first, then re-walks
    forward from there, repeating until every reign in the resulting
    history is clean.

    A gap alone isn't always actionable, though: the very first (origin)
    reign has no predecessor to revert to, and `seen` can (rarely) already
    contain this exact reign's key. In either case we're NOT going to void
    it -- but there can still be real, later games sitting unprocessed
    past the gap (that's what proved has_gap in the first place), and
    those must NOT just be silently dropped from the rest of history. So
    whenever a pass lands on a tip that has a gap but nowhere to revert
    it, an inner loop re-walks that exact (holder, reign) again with
    `skip_first_gap=True` -- forgiving ONLY that one gap, crediting it as
    an unusually long (but real) defense, with normal GAP_THRESHOLD_DAYS
    awareness restored for everything after it -- and recomputes the tip.
    If the new tip still can't be reverted and still has a (different,
    later) gap, this repeats; it stops as soon as either the tip becomes
    genuinely revertable (a predecessor exists and this exact reign
    hasn't been reverted before) or it's truly gap-free. Each forgiven
    pass consumes strictly more of the finite games list, so this always
    terminates, and -- unlike an earlier version of this fallback that
    disabled gap-awareness for the rest of the call once triggered --
    gap detection is never switched off for the remainder of history.
    (This matters because it isn't a hypothetical: an early version of
    this fallback shipped broken once already, in two stages. First, the
    Losers Belt's own origin holder, Princeton, hit a gap soon after 1869
    with no predecessor to revert to, and with no fallback at all the
    walk silently froze there forever, collapsing 150+ years of real
    history down to 5 reigns. Games are sparse enough in the earliest era
    of college football that even the origin team can go stretches
    longer than GAP_THRESHOLD_DAYS between its own real games -- not a
    rare edge case at all. Then, once a one-shot "re-walk with gap
    awareness off entirely" fallback was added to fix that, it turned out
    to swallow ALL later gaps too -- including a team like Abilene
    Christian's genuine, decades-long, multiple-gap absence -- because
    turning gap-awareness off for the rest of that one walk silently
    disabled it for everything chronologically after wherever it first
    triggered. The forgive-one-gap-at-a-time loop here is what fixes
    both.)

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
        belt_games, reigns = walk_losers(games, tie_rule, start_holder=holder, start_reign=reign,
                                          gap_threshold_days=GAP_THRESHOLD_DAYS)
        tip = reigns[-1]
        predecessor = _predecessor(tip)

        # A later game for this team sitting unprocessed in `games` is the
        # proof an in-reign gap is what stopped the walk (see walk_losers's
        # own gap_threshold_days docstring) -- as opposed to genuinely
        # having no more data yet, which is what the recent_teams check
        # below is for.
        has_gap = any(g["date"] > tip["last_game_date"]
                      and tip["team"] in (g["home"], g["away"]) for g in games)
        reign_key = (tip["team"], tip["start_date"])

        # There's a real, later game for this team proving a genuine gap,
        # but we can't void this reign over it yet -- either it's the
        # very first (origin) reign with nowhere to revert to (e.g.
        # Princeton for the Losers Belt), or reign_key is already in
        # `seen` (the infinite-loop guard: we've reverted this exact
        # reign once before). walk_losers already stopped dead right
        # before that later game, and everything after it -- possibly a
        # lot of real history -- would silently vanish if we accepted
        # `reigns` as final here. So forgive ONE gap at a time (re-walk
        # with skip_first_gap=True, gap-awareness otherwise fully
        # restored) and recompute the tip, until it's either revertable
        # or genuinely gap-free -- never disabling gap detection for the
        # rest of history the way a full gap_threshold_days=None re-walk
        # would.
        while has_gap and (predecessor is None or reign_key in seen):
            belt_games, reigns = walk_losers(games, tie_rule, start_holder=holder,
                                              start_reign=reign,
                                              gap_threshold_days=GAP_THRESHOLD_DAYS,
                                              skip_first_gap=True)
            tip = reigns[-1]
            predecessor = _predecessor(tip)
            has_gap = any(g["date"] > tip["last_game_date"]
                          and tip["team"] in (g["home"], g["away"]) for g in games)
            reign_key = (tip["team"], tip["start_date"])

        if predecessor is None or reign_key in seen or \
                not (has_gap or tip["team"] not in recent_teams):
            all_belt_games += belt_games
            all_reigns += reigns
            return all_belt_games, all_reigns, vacancies

        # Date the void to the team's TRUE last recorded activity, not
        # blindly to when they first caught it -- a team can catch the
        # belt, genuinely defend it for real (real losses, real dates)
        # for a while, and only THEN go quiet. Collapsing that whole span
        # down to "voided the same day it started" would be wrong
        # whenever defenses > 0: last_game_date already equals start_date
        # for a team with zero real defenses, so this naturally reduces to
        # the original same-day behavior in that case.
        last_activity = tip.get("last_game_date", tip["start_date"])
        v = {"team": tip["team"], "reign_started": tip["start_date"],
             "last_activity_date": last_activity,
             "effective_date": (date.fromisoformat(last_activity)
                                 + timedelta(days=1)).isoformat(),
             "detected_on": today, "reverted_to": predecessor}
        if has_gap:
            reason = ("went quiet for a long stretch (no game in well over two "
                      "seasons) before showing up again later in the data -- "
                      "whether that's a real gap in the program's own history "
                      "or just a hole in CFBD's coverage, either way it's not "
                      "a real unbroken defense")
        elif last_activity == tip["start_date"]:
            reason = "hasn't shown up in any game since catching it"
        else:
            reason = "hasn't shown up in any game since"
        print(f"Losers Belt: {v['team']} {reason} on {v['reign_started']}"
              f"{'' if last_activity == v['reign_started'] else f', defended it for real through {last_activity},'} "
              f"-- closing that reign as of their last real activity and reverting "
              f"the belt to {v['reverted_to']}, in effect since {v['effective_date']}.")
        vacated_tip = {**tip, "end_date": last_activity, "lost_to": None,
                        "vacated": True}
        all_belt_games += belt_games
        all_reigns += reigns[:-1] + [vacated_tip]
        vacancies.append(v)
        seen.add(reign_key)

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
    historical_vacancies = [
        v for v in all_vacancies
        if v.get("last_activity_date", v["reign_started"]) < boundary
    ]

    events = [("game", bg["date"], bg) for bg in historical_belt_games]
    events += [("vacancy", v.get("last_activity_date", v["reign_started"]), v)
               for v in historical_vacancies]
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
            reign["end_date"] = v.get("last_activity_date", v["reign_started"])
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

    print("Fetching current Division 1 (FBS/FCS) team list from CFBD...")
    d1_teams = fetch_division1_teams(args.key)
    print(f"{len(d1_teams)} current FBS/FCS programs -- the Losers Belt can only "
          f"pass between these (see filter_division1_games's docstring)")

    if baseline is None:
        print(f"Doing the one-time full {args.start_year}-{args.end_year} Losers "
              f"Belt walk from CFBD (~{2 * (args.end_year - args.start_year + 1)} calls).")
        raw = fetch_seasons(range(args.start_year, args.end_year + 1), args.key)
        games = normalize(raw, venue_tz)
        all_games_count = len(games)
        games = filter_division1_games(games, d1_teams)
        print(f"{len(games)} of {all_games_count} completed games are Division 1 "
              f"vs. Division 1, in chronological order")
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
        all_games_count = len(games)
        games = filter_division1_games(games, d1_teams)
        print(f"{len(games)} of {all_games_count} completed games in the live "
              f"window are Division 1 vs. Division 1")
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
