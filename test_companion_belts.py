"""Regression tests for the companion belts (2026-09-19 rewrite): the
belt_engine.resolve_vacancies successor rule, and build_conference_lineage's
archive-based FBS/FCS/conference belts.

Runs offline: the engine tests use tiny fixtures; the archive tests use
the committed historical_data/all_games.json.gz and baseline.json (skipped
with a message if either is missing).

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


def game(gid, date_str, home, away, hp, ap):
    return {"id": gid, "date": date_str, "season": int(date_str[:4]), "week": 1,
            "season_type": "regular", "home": home, "away": away,
            "home_points": hp, "away_points": ap, "neutral": False}


# ---- 1. a departed holder reverts to the most recent earlier holder that will play again
games = [
    game(1, d(0), "A", "B", 10, 0),      # A establishes
    game(2, d(7), "C", "A", 20, 0),      # C takes it from A
    game(3, d(14), "D", "C", 20, 0),     # D takes it from C -- then D leaves (never plays here again)
    game(4, d(370), "C", "B", 21, 7),    # next season: C and B still play in this world
    game(5, d(377), "A", "C", 3, 0),
]
belt_games, reigns, vacancies = be.resolve_vacancies(
    games, "holder", start_holder=None, start_reign=None,
    recent_teams={"A", "B", "C"}, today=d(400), first_game_date=d(0))
teams = [(r["team"], r.get("reclaimed_after"), r.get("vacated")) for r in reigns]
print("1.", teams)
assert [r["team"] for r in reigns] == ["A", "C", "D", "C", "A"], teams
assert reigns[2]["vacated"] and reigns[2]["end_date"] == d(14), "D's reign closes on its last game"
assert reigns[3]["reclaimed_after"] == "D" and reigns[3]["start_date"] == d(15), "C inherits the day after"
assert len(vacancies) == 1 and vacancies[0]["reverted_to"] == "C"
assert not any(r.get("end_date") == r["start_date"] and r.get("reclaimed_after") for r in reigns), "no zero-day placeholders"

# ---- 2. nobody to inherit from a departed holder, but the world goes on: retire + re-establish
games2 = [
    game(1, d(0), "A", "B", 10, 0),      # A establishes, then leaves for good
    game(2, d(370), "C", "B", 21, 7),    # a season later two other members play
]
belt_games, reigns, vacancies = be.resolve_vacancies(
    games2, "holder", start_holder=None, start_reign=None,
    recent_teams={"B", "C"}, today=d(400), first_game_date=d(0))
print("2.", [(r["team"], r.get("retired"), r.get("reestablished")) for r in reigns])
assert reigns[0]["team"] == "A" and reigns[0].get("retired") and reigns[0]["end_date"] == d(0)
assert reigns[1]["team"] == "C" and reigns[1].get("reestablished") and reigns[1]["end_date"] is None
assert vacancies[0]["reverted_to"] is None and vacancies[0].get("retired")

# ---- 2b. the re-established flag survives a pass that has to forgive a gap
#          (the first holder of the new segment goes quiet with nobody to hand
#          it to, then comes back -- the resolver re-walks the segment)
games2b = [
    game(1, d(0), "A", "B", 10, 0),      # A establishes, then leaves for good
    game(2, d(370), "C", "B", 21, 7),    # C re-establishes the belt
    game(3, d(1300), "C", "B", 14, 3),   # ...goes quiet for 930 days, then defends it
]
belt_games, reigns, vacancies = be.resolve_vacancies(
    games2b, "holder", start_holder=None, start_reign=None,
    recent_teams={"B", "C"}, today=d(1400), first_game_date=d(0))
print("2b.", [(r["team"], r.get("retired"), r.get("reestablished"), r["defenses"]) for r in reigns])
assert [r["team"] for r in reigns] == ["A", "C"], reigns
assert reigns[0].get("retired") and reigns[1].get("reestablished") and reigns[1]["defenses"] == 1
assert len(vacancies) == 1 and vacancies[0].get("retired")

# ---- 3. a dissolved world: the last holder keeps a closed, retired reign; nothing follows
games3 = [game(1, d(0), "A", "B", 10, 0), game(2, d(7), "B", "A", 14, 3)]
belt_games, reigns, vacancies = be.resolve_vacancies(
    games3, "holder", start_holder=None, start_reign=None,
    recent_teams=set(), today=d(4000), first_game_date=d(0))
print("3.", [(r["team"], r.get("retired"), r.get("end_date")) for r in reigns])
assert [r["team"] for r in reigns] == ["A", "B"] and reigns[-1].get("retired") and reigns[-1]["end_date"] == d(7)
assert len(vacancies) == 1 and vacancies[0]["reverted_to"] is None

# ---- 4. a quiet holder that comes back, with no one else to hand it to, carries it through the silence
games4 = [game(1, d(0), "A", "B", 10, 0), game(2, d(900), "A", "B", 10, 0)]
belt_games, reigns, vacancies = be.resolve_vacancies(
    games4, "holder", start_holder=None, start_reign=None,
    recent_teams={"A", "B"}, today=d(1000), first_game_date=d(0))
print("4.", [(r["team"], r["defenses"]) for r in reigns])
assert len(reigns) == 1 and reigns[0]["defenses"] == 1 and not vacancies

# ---- 5. the archive: the FBS belt is the real belt through 1977, and no belt has placeholder reigns
ARCHIVE = os.path.join("historical_data", "all_games.json.gz")
BASELINE = os.path.join("historical_data", "baseline.json")
if os.path.exists(ARCHIVE) and os.path.exists(BASELINE):
    import json
    import build_conference_lineage as bcl
    games = bcl.load_all_games(include_live=False)
    counts = bcl.team_season_counts(games)
    live_start_year = max(g["season"] for g in games) - 1
    fbs = bcl.build_scope("fbs", games, counts, live_start_year, "holder", "2026-01-01")
    real = json.load(open(BASELINE))["historical_reigns"]
    r77 = [(r["team"], r["start_date"], r.get("end_date"), r["defenses"]) for r in real if r["start_date"] < "1978-01-01"]
    f77 = [(r["team"], r["start_date"], r.get("end_date"), r["defenses"]) for r in fbs["reigns"] if r["start_date"] < "1978-01-01"]
    assert r77 == f77, "the FBS belt must match the real belt through the 1977 season"
    fcs = bcl.build_scope("fcs", games, counts, live_start_year, "holder", "2026-01-01")
    assert fcs["first_season"] >= 2003 and fcs["reigns"][0]["start_date"][:4] == str(fcs["first_season"])
    assert any("North Dakota State" in (g["home"], g["away"]) for g in fcs["belt_games"]), "NDSU must appear in the FCS belt"
    big8 = bcl.build_conference("Big 8", games, counts, live_start_year, "holder", "2026-01-01")
    assert big8 and big8["retired"] and big8["reigns"][-1]["end_date"] == "1996-08-30", big8 and big8["retired"]
    for doc in (fbs, fcs, big8):
        assert not any(r.get("reclaimed_after") and r.get("end_date") == r["start_date"] for r in doc["reigns"]), \
            "no synthetic zero-day reign may appear"
        assert not any(r.get("reclaimed_after") and r["defenses"] == 0 and r.get("end_date") and
                       (date.fromisoformat(r["end_date"]) - date.fromisoformat(r["start_date"])).days <= 1
                       for r in doc["reigns"]), "no day-by-day reversion chains"
    print("5. FBS == real belt through 1977 (%d reigns); FCS from %d with NDSU; Big 8 retired %s" %
          (len(f77), fcs["first_season"], big8["reigns"][-1]["end_date"]))
else:
    print("5. (skipped: archive or baseline not present)")

print("\nALL COMPANION-BELT TESTS PASSED")
