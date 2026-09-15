"""Regression test for the INFINITE-LOOP bug found live in belt_engine.py's
resolve_vacancies() during workflow run #76 (the second full triple-bootstrap
attempt, after the earlier chain_teams cycle bugs -- fixed in ed45ed3 and
ede839f -- had already shipped). This is a THIRD, distinct bug: it needs no
cycle among multiple teams at all, just a single origin reign with more than
one un-revertable gap before real predecessor data resumes.

The has_gap forgiveness loop used to re-walk from the exact same
(start_holder, start_reign) every pass with a bare "forgive one gap" flag
that was never counted. walk_winner() is a pure function of its arguments,
so once one pass forgave the FIRST gap and still landed on a tip with no
predecessor and ANOTHER gap ahead, every later pass fed it identical
arguments and got the identical result back -- forever. Caught live when the
SIAA conference's from-scratch bootstrap walk hit exactly this shape (an
origin holder that goes quiet more than once before ever losing the belt)
and spun reprinting the same "WARNING: first game is ..." line 900+ times
without ever advancing, forcing a manual cancellation of the run before it
could commit anything.

This builds a minimal fixture where the origin holder (A) has TWO separate
over-threshold silences before finally losing the belt for real, and checks
the walk both terminates and produces the correct 2-reign, zero-vacancy
result (A defended through both long silences, then lost outright -- nothing
here should ever have been reverted). A 15s SIGALRM safety net guards the
test itself, matching the pattern used in test_belt_engine_cycle_fix.py.
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
    game(1, d(0), "A", "X", 10, 0),      # A beats X -> A origin holder
    # First over-threshold silence (600 days > GAP_THRESHOLD_DAYS=500) --
    # must be forgiven once before the walk can even reach the second one.
    game(2, d(600), "A", "Y", 10, 0),    # A beats Y -> real defense, if forgiven
    # Second over-threshold silence (700 days since day 600) -- with the
    # old bare-flag bug, the inner loop never got far enough to see this
    # game at all; it just replayed the FIRST forgiveness forever.
    game(3, d(1300), "A", "Z", 10, 0),   # A beats Z -> real defense, if forgiven
    # A finally loses the belt for real, immediately after -- no gap here.
    game(4, d(1301), "B", "A", 20, 0),   # B beats A -> B catches (won_from=A)
]
games.sort(key=lambda g: g["date"])

recent_teams = {"B"}  # A deliberately not "recent" -- irrelevant here since
                       # B (the tip) has a real predecessor and no gap.


def handler(signum, frame):
    raise TimeoutError(
        "resolve_vacancies did not terminate within 15s -- the multi-gap "
        "infinite-loop bug is back: an origin reign with two forgivable "
        "gaps should resolve in a fraction of a second, not spin forever "
        "replaying the same single-gap forgiveness.")


signal.signal(signal.SIGALRM, handler)
signal.alarm(15)

belt_games, reigns, vacancies = be.resolve_vacancies(
    games, "holder", start_holder=None, start_reign=None,
    recent_teams=recent_teams, today=d(1400))
signal.alarm(0)

print(f"Total reigns produced: {len(reigns)}, vacancies: {len(vacancies)}")
for r in reigns:
    print(" reign:", r["team"], r["start_date"], "->", r.get("end_date"),
          "defenses", r.get("defenses"))

assert len(vacancies) == 0, (
    f"Nothing here should have been reverted -- A defended through both "
    f"long silences for real, then lost outright. Got {len(vacancies)} "
    f"vacancies: {vacancies}")

assert [r["team"] for r in reigns] == ["A", "B"], (
    f"Expected exactly two reigns (A, then B after A lost for real), got "
    f"{[r['team'] for r in reigns]}")

a_reign = reigns[0]
assert a_reign["defenses"] == 2, (
    f"Expected A's reign to show 2 defenses (both forgiven silences "
    f"credited as real defenses), got {a_reign['defenses']}")
assert a_reign["end_date"] == d(1301) and a_reign["lost_to"] == "B", (
    "Expected A's reign to close out by really losing to B, not by being "
    f"reverted -- got end_date={a_reign.get('end_date')}, "
    f"lost_to={a_reign.get('lost_to')}")

print("\nALL BELT-ENGINE MULTI-GAP FIX TESTS PASSED")
