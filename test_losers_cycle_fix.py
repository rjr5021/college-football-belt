"""Regression test for the revert-cycle bug found in the live FCS-scope
Losers Belt: The Citadel / East Tennessee State / Wofford bounced back
and forth, one revert per calendar day, for ~20 years (1981-2003) and
~7,455 spurious one-day reigns, because `seen` only guards against the
EXACT same (team, start_date) key repeating -- and a synthetic
start_date that advances by a day on every hop never repeats, even
though the same 3 teams do. This builds a small synthetic analog of
that exact shape (a real 3-team predecessor cycle, then a long stretch
where none of the three has a qualifying game within GAP_THRESHOLD_DAYS
of any point in the drought, then one of them finally plays for real)
and checks that resolve_vacancies now collapses the whole drought into
ONE forgiven long defense instead of exploding into hundreds of hops.
"""
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
    # Founding chain, all close together -- establishes a real 3-way
    # predecessor cycle among A/B/C, same shape as Citadel<-ETSU<-Wofford.
    game(1, d(0), "A", "X", 0, 10),           # A loses to X -> A is origin holder
    game(2, d(1), "A", "C", 20, 0),           # A beats C -> C catches it (won_from=A)
    game(3, d(2), "C", "B", 20, 0),           # C beats B -> B catches it (won_from=C)
    game(4, d(3), "B", "A", 20, 0),           # B beats A -> A catches it (won_from=B)
    game(5, d(4), "A", "C", 20, 0),           # A beats C -> C catches it (won_from=A)
    game(6, d(5), "C", "B", 20, 0),           # C beats B -> B catches it (won_from=C)
    # Now B holds the belt as of day 5. None of A/B/C has a qualifying
    # game again until each of their own real future games below --
    # every one of them is well past GAP_THRESHOLD_DAYS (500) from day 5.
    game(7, d(900), "A", "F", 20, 0),         # A's own next real game
    game(8, d(950), "C", "E", 0, 20),         # C's own next real game (C loses, real defense)
    game(9, d(1000), "B", "Z", 30, 0),        # B's own next real game -- ends the drought for real
]
games.sort(key=lambda g: g["date"])

recent_teams = {"A", "B", "C", "X", "F", "E", "Z"}

belt_games, reigns, vacancies = bll.resolve_vacancies(
    games, "holder", start_holder=None, start_reign=None,
    recent_teams=recent_teams, today=d(1100))

print(f"Total reigns produced: {len(reigns)}")
for r in reigns:
    print(" ", r["team"], r["start_date"], "->", r.get("end_date"),
          "defenses=", r.get("defenses"), "vacated=" , r.get("vacated", False))

assert len(reigns) < 15, (
    f"Expected a small, sane number of reigns (the bug produced ~7,455 for "
    f"the real 20-year drought) -- got {len(reigns)}, the cycle guard didn't "
    f"engage.")

# The drought must collapse to a SINGLE vacancy/forgiveness event, not one
# per calendar day.
day_reigns = [r for r in reigns if r.get("start_date") and r.get("end_date")
              and r["start_date"] != r["end_date"]
              and (date.fromisoformat(r["end_date"]) - date.fromisoformat(r["start_date"])).days > 800]
assert day_reigns, "Expected one reign to absorb the long drought as a real, credited defense"
print(f"\nDrought absorbed into one long reign: {day_reigns[0]['team']} "
      f"({day_reigns[0]['start_date']} -> {day_reigns[0]['end_date']})")

# And it must actually reach the real resuming game (Z should show up
# somewhere as the final holder, since B beats Z as the last event).
teams_seen = [r["team"] for r in reigns]
assert "Z" in teams_seen, f"Expected the walk to reach the real resuming game -- teams: {teams_seen}"

print("\nALL CYCLE-FIX TESTS PASSED")
