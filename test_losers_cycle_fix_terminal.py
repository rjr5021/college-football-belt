"""Regression test for the SECOND revert-cycle bug: a real 5-team predecessor
cycle (Holy Cross / Brown / William & Mary / Dartmouth / Harvard) reverted
once per calendar day, forever, during the live FCS-scope Losers Belt
bootstrap (workflow run #70) -- the exact same symptom as the original
Citadel/East Tennessee State/Wofford bug that `chain_teams` was added to fix
(see test_losers_cycle_fix.py), but NOT caught by that fix.

Root cause: `chain_teams` was only ever consulted inside the has_gap
forgiveness loop in resolve_vacancies(). A cycle reached through the OTHER
revert reason -- TERMINAL dormancy, where none of the cycling teams has any
later game at all left in `games` -- sailed straight past it every lap,
because the outer stop/revert decision never checked it. And the cycle is
self-perpetuating once entered: the "inherited" predecessor lookup finds a
reverted team's MOST RECENT prior appearance in `all_reigns` -- which, once
a team has been through the cycle once, is its own already-vacated reign
from earlier in the SAME cycle, whose predecessor is fixed at creation and
always points to the same next team. So A's second reopening always
inherits from the same team its first one did, and so on forever.

This builds a clean 5-team predecessor cycle (A -> B -> C -> D -> E -> A, a
handful of quick real games) with NO later game for any of the five ANYWHERE
in the fetched window (pure terminal dormancy, no in-reign gap involved at
all) and checks that resolve_vacancies collapses it to one lap (5 reverts)
instead of spinning once per calendar day forever. A SIGALRM wall-clock
timeout guards the test itself in case of a future regression that
reintroduces the runaway -- without it, an unfixed run doesn't fail loudly,
it just never returns.
"""
import signal
import sys
from datetime import date, timedelta

sys.path.insert(0, "/tmp/cfb-work")
import build_losers_lineage as bll

D = date.fromisoformat("2005-01-01")  # arbitrary anchor, well clear of both
                                       # DISRUPTION_WINDOWS entries


def d(offset):
    return (D + timedelta(days=offset)).isoformat()


def game(gid, date_str, home, away, home_pts, away_pts):
    return {"id": gid, "date": date_str, "season": int(date_str[:4]), "week": 1,
            "season_type": "regular", "home": home, "away": away,
            "home_points": home_pts, "away_points": away_pts, "neutral": False}


games = [
    # Quick founding chain establishing a real 5-way predecessor cycle:
    # A -> B -> C -> D -> E -> A. Unlike test_losers_cycle_fix.py's 3-team
    # case, NONE of these five ever plays again -- pure terminal dormancy,
    # no later game anywhere in `games` for any of them, so has_gap is
    # False at every single hop and the has_gap-scoped forgiveness loop
    # never engages at all.
    game(1, d(0), "A", "X", 0, 10),   # A loses to X -> A origin holder
    game(2, d(1), "A", "B", 20, 0),   # A beats B -> B catches (won_from=A)
    game(3, d(2), "B", "C", 20, 0),   # B beats C -> C catches (won_from=B)
    game(4, d(3), "C", "D", 20, 0),   # C beats D -> D catches (won_from=C)
    game(5, d(4), "D", "E", 20, 0),   # D beats E -> E catches (won_from=D)
    game(6, d(5), "E", "A", 20, 0),   # E beats A -> A catches AGAIN (won_from=E)
    # One distant, unrelated game so `games`/recent_teams aren't trivially
    # empty -- doesn't touch A-E at all.
    game(7, d(900), "Z", "Y", 10, 3),
]
games.sort(key=lambda g: g["date"])

recent_teams = {"Z", "Y"}  # A/B/C/D/E deliberately NOT recent


def handler(signum, frame):
    raise TimeoutError(
        "resolve_vacancies did not terminate within 15s -- the old bug is "
        "back: a 5-team terminal-dormancy cycle should collapse to one lap "
        "in a fraction of a second, not spin once per calendar day forever.")


signal.signal(signal.SIGALRM, handler)
signal.alarm(15)

belt_games, reigns, vacancies = bll.resolve_vacancies(
    games, "holder", start_holder=None, start_reign=None,
    recent_teams=recent_teams, today=d(1000))
signal.alarm(0)

print(f"Total reigns produced: {len(reigns)}, vacancies: {len(vacancies)}")
for v in vacancies:
    print(" vacated:", v["team"], v["reign_started"], "->", v["effective_date"],
          "reverted_to", v["reverted_to"])

assert len(vacancies) == 5, (
    f"Expected exactly one lap of the 5-team cycle (5 reverts: A/E/D/C/B) "
    f"before the chain_teams guard stops it on the second trip through A -- "
    f"got {len(vacancies)}. Either the guard isn't engaging at all (bug is "
    f"back) or it's engaging at the wrong point.")

teams_vacated = [v["team"] for v in vacancies]
assert teams_vacated == ["A", "E", "D", "C", "B"], (
    f"Expected the cycle to be walked in predecessor order A->E->D->C->B "
    f"before stopping -- got {teams_vacated}")

assert len(set(teams_vacated)) == 5, (
    "Expected each of the 5 teams to be vacated exactly once, not looped "
    f"back around a second time -- got {teams_vacated}")

print("\nALL TERMINAL-DORMANCY CYCLE-FIX TESTS PASSED")
