import sys, os
sys.path.insert(0, ".")
sys.path.insert(0, "/tmp/cfb-work")

from build_losers_lineage import filter_division1_games, walk_losers, resolve_vacancies

# ---- Test 1: filter_division1_games drops any game with a non-D1
# participant, keeps D1-vs-D1 games untouched, and preserves order. ----
games = [
    {"id": 1, "date": "1869-11-06", "season": 1869, "week": 1, "season_type": "regular",
     "home": "Rutgers", "away": "Princeton", "home_points": 6, "away_points": 4, "neutral": False},
    {"id": 2, "date": "1870-01-01", "season": 1870, "week": 1, "season_type": "regular",
     "home": "Princeton", "away": "Yale", "home_points": 10, "away_points": 0, "neutral": False},
    # Yale plays a D-II cupcake -- must be dropped entirely, not just
    # "not counted": it should never even reach walk_losers.
    {"id": 3, "date": "1870-02-01", "season": 1870, "week": 2, "season_type": "regular",
     "home": "Yale", "away": "SmallD2", "home_points": 40, "away_points": 0, "neutral": False},
    {"id": 4, "date": "1870-03-01", "season": 1870, "week": 3, "season_type": "regular",
     "home": "Yale", "away": "Cornell", "home_points": 30, "away_points": 0, "neutral": False},
    # Two non-D1 teams playing each other -- also dropped.
    {"id": 5, "date": "1870-04-01", "season": 1870, "week": 4, "season_type": "regular",
     "home": "SmallD2", "away": "TinyD3", "home_points": 14, "away_points": 7, "neutral": False},
]
d1_teams = {"Rutgers": "fbs", "Princeton": "fbs", "Yale": "fcs", "Cornell": "fcs"}
# SmallD2, TinyD3 excluded entirely (not in d1_teams at all)

filtered = filter_division1_games(games, d1_teams)  # scope=None == "combined"
assert [g["id"] for g in filtered] == [1, 2, 4], \
    "games 3 (vs SmallD2) and 5 (SmallD2 vs TinyD3) must be dropped, order preserved"
print("Test 1 (filter drops any game touching a non-D1 team, keeps order) PASSED")

# ---- Test 2: a dropped game leaves the D1 team's OWN reign completely
# unaffected -- no phantom vacancy, no distorted defense count, its real
# D1 games on either side of the dropped one still connect normally. ----
belt_games, reigns = walk_losers(filtered)
teams_in_order = [r["team"] for r in reigns]
assert teams_in_order == ["Princeton", "Yale", "Cornell"], teams_in_order
assert "SmallD2" not in teams_in_order and "TinyD3" not in teams_in_order

yale_reign = reigns[1]
assert yale_reign["defenses"] == 0, \
    "the dropped SmallD2 game must not count as a defense"
assert yale_reign["start_date"] == "1870-01-01" and yale_reign["end_date"] == "1870-03-01"
assert yale_reign["lost_to"] == "Cornell"
print("Test 2 (a D1 team's own reign is unaffected by a dropped non-D1 game) PASSED")

# ---- Test 3: a non-D1 team never enters the lineage even via
# resolve_vacancies (the full pipeline path), and no vacancy is ever
# recorded for it -- it's simply invisible to the Losers Belt, not
# detected-and-reverted like a defunct program would be. ----
recent_teams = {"Yale", "Cornell", "Rutgers", "Princeton"}
today = "2026-09-16"
full_belt_games, full_reigns, vacancies = resolve_vacancies(
    filtered, "holder", start_holder=None, start_reign=None,
    recent_teams=recent_teams, today=today)
assert vacancies == [], "no defunct-program revert should ever fire over a non-D1 exclusion"
assert [r["team"] for r in full_reigns] == ["Princeton", "Yale", "Cornell"]
print("Test 3 (non-D1 teams never enter the lineage, no spurious vacancy) PASSED")

# ---- Test 4: scope="fbs"/"fcs" additionally requires BOTH sides to share
# that one classification -- this is what lets an fbs-scoped or
# fcs-scoped belt never cross into the other level, unlike "combined".
# Rutgers/Princeton are fbs; Yale/Cornell are fcs. Game 1 (Rutgers-
# Princeton) is fbs-vs-fbs; game 2 (Princeton-Yale) is fbs-vs-fcs, mixed;
# game 4 (Yale-Cornell) is fcs-vs-fcs.
fbs_filtered = filter_division1_games(games, d1_teams, scope="fbs")
assert [g["id"] for g in fbs_filtered] == [1], \
    "fbs scope must keep only game 1 (Rutgers-Princeton, both fbs) -- " \
    f"got {[g['id'] for g in fbs_filtered]}"

fcs_filtered = filter_division1_games(games, d1_teams, scope="fcs")
assert [g["id"] for g in fcs_filtered] == [4], \
    "fcs scope must keep only game 4 (Yale-Cornell, both fcs) -- " \
    f"got {[g['id'] for g in fcs_filtered]}"

combined_filtered = filter_division1_games(games, d1_teams, scope="combined")
assert [g["id"] for g in combined_filtered] == [1, 2, 4], \
    "scope='combined' must behave identically to scope=None"
print("Test 4 (scope='fbs'/'fcs' additionally requires both sides to share "
      "that one classification; 'combined' matches the scope=None default) PASSED")

print("\nALL DIVISION 1 FILTER TESTS PASSED")
