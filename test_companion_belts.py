"""Regression tests for the companion belts: belt_engine's membership-based
vacancy resolver (2026-09-19, rewritten the same day after a Reddit reader
caught the FBS Independents belt "vacating" Notre Dame twice) and
build_conference_lineage's archive-based FBS/FCS/conference belts.

Runs offline: the engine tests use tiny fixtures with conference labels;
the archive tests use the committed historical_data/all_games.json.gz and
baseline.json (skipped with a message if either is missing).

    python3 test_companion_belts.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import belt_engine as be

D = date.fromisoformat("2010-09-01")


def d(offset):
    return (D + timedelta(days=offset)).isoformat()


def game(gid, date_str, home, away, hp, ap, hc="C", ac="C"):
    """A game; `hc`/`ac` are the conference labels of each side that season
    ("C" is the conference this belt is for, anything else is elsewhere)."""
    return {"id": gid, "date": date_str, "season": be.season_of(date_str), "week": 1,
            "season_type": "regular", "home": home, "away": away,
            "home_points": hp, "away_points": ap, "neutral": False,
            "home_conference": hc, "away_conference": ac}


def world(games, covered=None, today=None):
    """The belt's world from the fixture games: members are the sides labeled "C";
    qualifying games are the ones between two members."""
    membership = be.Membership.from_games(games, lambda lab, s: lab == "C", covered)
    qualifying = [g for g in games if g["home_conference"] == "C" and g["away_conference"] == "C"]
    return membership, qualifying


def resolve(games, covered=None, today=None):
    membership, qualifying = world(games, covered)
    return be.resolve_vacancies_by_membership(
        qualifying, "holder", start_holder=None, start_reign=None, membership=membership,
        today=today or d(5000), first_game_date=qualifying[0]["date"])


# ---- 1. a holder that plays a later season under another label vacates the belt
#         on its last game as a member; the most recent earlier holder that is a
#         member of the season it left for inherits it the next day
games = [
    game(1, d(0), "A", "B", 10, 0),                 # 2010: A establishes
    game(2, d(7), "C", "A", 20, 0),                 # C takes it
    game(3, d(14), "D", "C", 20, 0),                # D takes it ...
    game(4, d(60), "D", "Z", 30, 0, ac="Other"),    # ... and plays a non-member (still a member, not a belt game)
    game(5, d(370), "D", "Q", 21, 7, hc="Other", ac="Other"),   # 2011: D is in another league now
    game(6, d(371), "C", "B", 21, 7),               # C and B carry on in this one
    game(7, d(378), "A", "C", 3, 0),
    game(8, d(740), "A", "B", 3, 0),                # 2012
]
belt_games, reigns, vacancies = resolve(games)
teams = [(r["team"], r.get("reclaimed_after"), r.get("vacated"), r.get("end_date")) for r in reigns]
print("1.", teams)
assert [r["team"] for r in reigns] == ["A", "C", "D", "C", "A"], teams
assert reigns[2]["vacated"] and reigns[2]["end_date"] == d(60) and reigns[2]["left_season"] == 2011, \
    "D's reign closes on its last game as a member (the non-belt game), not its last belt game"
assert reigns[3]["reclaimed_after"] == "D" and reigns[3]["start_date"] == d(61), "C inherits the day after"
assert len(vacancies) == 1 and vacancies[0]["reverted_to"] == "C"
assert not any(r.get("end_date") == r["start_date"] and r.get("reclaimed_after") for r in reigns), "no zero-day placeholders"

# ---- 2. an "independent" that goes years without meeting another member keeps the
#         belt (it is still a member: it keeps playing under this label) -- the
#         Notre Dame case; the others' games meanwhile are not belt games
games2 = [
    game(1, d(0), "A", "B", 10, 0),                 # 2010: A establishes
    game(2, d(370), "A", "X", 10, 0, ac="Other"),   # 2011-2013: A plays only outsiders
    game(3, d(380), "B", "C", 10, 0),               # B beats C -- neither holds the belt, not a belt game
    game(4, d(735), "A", "X", 10, 0, ac="Other"),
    game(5, d(1100), "A", "X", 10, 0, ac="Other"),
    game(6, d(1470), "A", "B", 10, 0),              # 2014: A finally meets a member again -- a defense
    game(7, d(1471), "C", "B", 10, 0),
]
belt_games, reigns, vacancies = resolve(games2)
print("2.", [(r["team"], r["defenses"], r.get("end_date")) for r in reigns])
assert len(reigns) == 1 and reigns[0]["team"] == "A" and reigns[0]["defenses"] == 1 and not vacancies
assert [g["date"] for g in belt_games] == [d(0), d(1470)]

# ---- 3. nobody left in the line to inherit from a departed holder, but the world
#         goes on: the belt retires and is re-established by the next game
games3 = [
    game(1, d(0), "A", "B", 10, 0),                 # A establishes, then leaves for good
    game(2, d(370), "A", "Q", 10, 0, hc="Other", ac="Other"),
    game(3, d(371), "C", "B", 21, 7),               # two other members play on
    game(4, d(740), "C", "B", 21, 7),
]
belt_games, reigns, vacancies = resolve(games3)
print("3.", [(r["team"], r.get("retired"), r.get("reestablished"), r["defenses"]) for r in reigns])
assert reigns[0]["team"] == "A" and reigns[0].get("retired") and reigns[0]["end_date"] == d(0)
assert reigns[1]["team"] == "C" and reigns[1].get("reestablished") and reigns[1]["end_date"] is None and reigns[1]["defenses"] == 1
assert vacancies[0]["reverted_to"] is None and vacancies[0].get("retired")

# ---- 3b. the re-established flag survives a pass that has to forgive a gap
games3b = [
    game(1, d(0), "A", "B", 10, 0),
    game(2, d(370), "A", "Q", 10, 0, hc="Other", ac="Other"),   # A leaves
    game(3, d(371), "C", "B", 21, 7),               # C re-establishes the belt ...
    game(4, d(1300), "C", "B", 14, 3),              # ... goes 930 days without a member, then defends it
    game(5, d(1301), "B", "X", 14, 3, ac="Other"),  # (B keeps playing in between, so the world goes on)
    game(6, d(800), "B", "X", 14, 3, ac="Other"),
]
belt_games, reigns, vacancies = resolve(games3b)
print("3b.", [(r["team"], r.get("retired"), r.get("reestablished"), r["defenses"]) for r in reigns])
assert [r["team"] for r in reigns] == ["A", "C"], reigns
assert reigns[0].get("retired") and reigns[1].get("reestablished") and reigns[1]["defenses"] == 1

# ---- 4. a dissolved world: the last holder keeps the belt to the end -- no
#         vacancy, the reign simply stays open for build_conference to close
games4 = [game(1, d(0), "A", "B", 10, 0), game(2, d(7), "B", "A", 14, 3),
          game(3, d(370), "A", "Q", 1, 0, hc="Other", ac="Other"),   # both moved on: the league is gone
          game(4, d(371), "B", "Q", 1, 0, hc="Other", ac="Other")]
belt_games, reigns, vacancies = resolve(games4)
print("4.", [(r["team"], r.get("retired"), r.get("end_date")) for r in reigns])
assert [r["team"] for r in reigns] == ["A", "B"] and reigns[-1]["end_date"] is None and not vacancies

# ---- 5. a holder that suspends football and comes back keeps the belt (the belt waits)
games5 = [game(1, d(0), "A", "B", 10, 0),
          game(2, d(370), "B", "C", 10, 0), game(3, d(740), "B", "C", 10, 0),   # A plays nothing for two seasons
          game(4, d(1100), "A", "B", 10, 0)]                                    # then returns and defends
belt_games, reigns, vacancies = resolve(games5)
print("5.", [(r["team"], r["defenses"]) for r in reigns])
assert len(reigns) == 1 and reigns[0]["defenses"] == 1 and not vacancies

# ---- 6. a program gone for good (never plays again while the world goes on) vacates
games6 = [game(1, d(0), "A", "B", 10, 0), game(2, d(7), "B", "A", 14, 3), game(3, d(14), "A", "B", 14, 3),
          game(4, d(370), "B", "C", 10, 0), game(5, d(740), "B", "C", 10, 0), game(6, d(1100), "B", "C", 10, 0)]
belt_games, reigns, vacancies = resolve(games6)
print("6.", [(r["team"], r.get("vacated"), r.get("reclaimed_after"), r["defenses"]) for r in reigns])
assert [r["team"] for r in reigns] == ["A", "B", "A", "B"] and reigns[2].get("vacated") and reigns[3]["reclaimed_after"] == "A"
assert reigns[2]["end_date"] == d(14) and reigns[3]["start_date"] == d(15), "A is gone after 2010: the belt reverts to B as of A's last game"
assert reigns[3]["defenses"] == 3 and reigns[3]["end_date"] is None    # B's three wins over C are defenses of the inherited belt

# ---- 7. a silence across seasons the archive does not cover is a data gap:
#         retire on the last game we can see, start over with the next on record
games7 = [game(1, d(0), "A", "B", 10, 0),                  # 2010
          game(2, d(365 * 6), "A", "B", 10, 0),            # 2016: A's next game, six years later
          game(3, d(365 * 6 + 7), "B", "A", 10, 0)]
belt_games, reigns, vacancies = resolve(games7, covered=lambda s: s not in (2012, 2013))
print("7.", [(r["team"], r.get("retired"), r.get("reestablished"), r["defenses"]) for r in reigns])
assert [r["team"] for r in reigns] == ["A", "A", "B"] and reigns[0].get("retired") and reigns[1].get("reestablished")
belt_games, reigns, vacancies = resolve(games7)   # the same silence in a fully covered world is just a long reign
assert [r["team"] for r in reigns] == ["A", "B"] and reigns[0]["defenses"] == 1 and not vacancies

# ---- 8. the archive: the FBS belt is the real belt through 1977, and no belt has placeholder reigns
ARCHIVE = os.path.join("historical_data", "all_games.json.gz")
BASELINE = os.path.join("historical_data", "baseline.json")
if os.path.exists(ARCHIVE) and os.path.exists(BASELINE):
    import json
    import build_conference_lineage as bcl
    from classification import fcs_first_season
    games = bcl.load_all_games(include_live=False)
    index = be.index_labels(games)
    fcs_from = fcs_first_season(games)
    latest = games[-1]["season"]
    fbs = bcl.build_scope("fbs", games, index, fcs_from, "holder", "2026-01-01")
    real = json.load(open(BASELINE))["historical_reigns"]
    r77 = [(r["team"], r["start_date"], r.get("end_date"), r["defenses"]) for r in real if r["start_date"] < "1978-01-01"]
    f77 = [(r["team"], r["start_date"], r.get("end_date"), r["defenses"]) for r in fbs["reigns"] if r["start_date"] < "1978-01-01"]
    assert r77 == f77, "the FBS belt must match the real belt through the 1977 season"
    fcs = bcl.build_scope("fcs", games, index, fcs_from, "holder", "2026-01-01")
    assert fcs["first_season"] >= 2003 and fcs["reigns"][0]["start_date"][:4] == str(fcs["first_season"])
    assert any("North Dakota State" in (g["home"], g["away"]) for g in fcs["belt_games"]), "NDSU must appear in the FCS belt"
    big8 = bcl.build_conference("Big 8", games, index, fcs_from, "holder", "2026-01-01", latest)
    assert big8 and big8["retired"] and big8["reigns"][-1]["end_date"] == "1996-08-30", big8 and big8["retired"]
    indep = bcl.build_conference("FBS Independents", games, index, fcs_from, "holder", "2026-01-01", latest)
    nd = [r for r in indep["reigns"] if r["team"] == "Notre Dame" and r["start_date"] == "2011-10-29"]
    assert nd and nd[0].get("vacated") and nd[0]["left_season"] == 2020 and nd[0]["end_date"] == "2019-12-28", \
        "Notre Dame keeps the Independents belt through its years without an independent opponent and vacates it only for its 2020 ACC season"
    assert not indep["retired"], "the Independents belt is alive: UConn and Notre Dame are still independents"
    for doc in (fbs, fcs, big8, indep):
        assert not any(r.get("reclaimed_after") and r.get("end_date") == r["start_date"] for r in doc["reigns"]), \
            "no synthetic zero-day reign may appear"
        assert not any(r.get("reclaimed_after") and r["defenses"] == 0 and r.get("end_date") and
                       (date.fromisoformat(r["end_date"]) - date.fromisoformat(r["start_date"])).days <= 1
                       for r in doc["reigns"]), "no day-by-day reversion chains"
    print("8. FBS == real belt through 1977 (%d reigns); FCS from %d with NDSU; Big 8 retired %s; Independents alive with %s" %
          (len(f77), fcs["first_season"], big8["reigns"][-1]["end_date"], indep["current_holder"]))
else:
    print("8. (skipped: archive or baseline not present)")

print("\nALL COMPANION-BELT TESTS PASSED")
