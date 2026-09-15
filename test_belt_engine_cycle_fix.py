"""Regression test for the revert-cycle bug found live in belt_engine.py
during workflow run #72 (the championship-scopes / conference-belts
bootstrap): belt_engine.py's resolve_vacancies() was forked from an EARLIER
version of build_losers_lineage.py's resolve_vacancies(), before the
chain_teams cycle guard existed at all (commit #67) -- so it had zero
protection against a real N-team predecessor cycle reverting once per
calendar day forever, the exact same failure mode as the ORIGINAL Losers
Belt bug (Citadel/East Tennessee State/Wofford) and its once-thought-fixed
second occurrence (Holy Cross/Brown/William & Mary/Dartmouth/Harvard).

The live run hit this via the CONFERENCE BELT path (build_conference_lineage.py
uses belt_engine.py with recent_teams = a conference's current roster), with
the exact same 5 Ivy/Patriot League programs cycling -- proof this engine's
missing guard, not anything specific to the Losers Belt's own code, was the
real gap.

This builds the same minimal 5-team predecessor-cycle fixture used in
build_losers_lineage.py's test_losers_cycle_fix_terminal.py (adapted to the
WINNER-take rule this engine uses, and to belt_engine's game dict shape) and
checks it collapses to one lap instead of spinning forever. A 15s SIGALRM
safety net guards the test itself.
"""
import signal
import sys
from datetime import date, timedelta

sys.path.insert(0, "/tmp/cfb-work")
import belt_engine as be

D = date.fromisoformat("2005-01-01")


def d(offset):
    return (D + timedelta(days=offset)).isoformat()


def game(gid, date_str, home, away, home_pts, away_pts):
    return {"id": gid, "date": date_str, "season": int(date_str[:4]), "week": 1,
            "season_type": "regular", "home": home, "away": away,
            "home_points": home_pts, "away_points": away_pts, "neutral": False}


games = [
    # Founding chain establishing a real 5-way predecessor cycle under the
    # WINNER-take rule: A -> B -> C -> D -> E -> A (each catches the belt
    # by BEATING the previous holder, the opposite direction from the
    # Losers Belt's lose-to-catch rule, but the same cyclical shape).
    game(1, d(0), "A", "X", 10, 0),   # A beats X -> A origin holder
    game(2, d(1), "B", "A", 20, 0),   # B beats A -> B catches (won_from=A)
    game(3, d(2), "C", "B", 20, 0),   # C beats B -> C catches (won_from=B)
    game(4, d(3), "D", "C", 20, 0),   # D beats C -> D catches (won_from=C)
    game(5, d(4), "E", "D", 20, 0),   # E beats D -> E catches (won_from=D)
    game(6, d(5), "A", "E", 20, 0),   # A beats E -> A catches AGAIN (won_from=E)
    # None of the five ever plays again -- pure terminal dormancy, the
    # exact path that has_gap-only guards (like the ORIGINAL belt_engine.py
    # before this fix) never check at all.
    game(7, d(900), "Z", "Y", 10, 3),
]
games.sort(key=lambda g: g["date"])

recent_teams = {"Z", "Y"}  # A/B/C/D/E deliberately NOT recent


def handler(signum, frame):
    raise TimeoutError(
        "resolve_vacancies did not terminate within 15s -- the bug is back: "
        "a 5-team terminal-dormancy cycle should collapse to one lap in a "
        "fraction of a second, not spin once per calendar day forever.")


signal.signal(signal.SIGALRM, handler)
signal.alarm(15)

belt_games, reigns, vacancies = be.resolve_vacancies(
    games, "holder", start_holder=None, start_reign=None,
    recent_teams=recent_teams, today=d(1000))
signal.alarm(0)

print(f"Total reigns produced: {len(reigns)}, vacancies: {len(vacancies)}")
for v in vacancies:
    print(" vacated:", v["team"], v["reign_started"], "->", v["effective_date"],
          "reverted_to", v["reverted_to"])

assert len(vacancies) == 5, (
    f"Expected exactly one lap of the 5-team cycle (5 reverts: B/C/D/E/A) "
    f"before the chain_teams guard stops it on the second trip through B -- "
    f"got {len(vacancies)}.")

teams_vacated = [v["team"] for v in vacancies]
assert len(set(teams_vacated)) == 5, (
    "Expected each of the 5 teams to be vacated exactly once, not looped "
    f"back around a second time -- got {teams_vacated}")

print("\nALL BELT-ENGINE CYCLE-FIX TESTS PASSED")
