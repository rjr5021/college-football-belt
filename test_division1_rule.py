"""The Division I rule (2026-09-19): from the 1978 split on, only a game
between two Division I teams can move the belt; before it, every game on
record counts. classification.division1_game is what build_lineage.py (live
seasons, Belt Watch), build_alternate_lineages.py (the what-if record, the
universes) apply. Prompted by a Reddit reader who flipped Baylor-Wofford 2013
on the what-if page and watched the belt fall to Division II UNC Pembroke,
whose next game on record was in 2021.

Runs offline; the archive checks are skipped if the archive is missing.

    python3 test_division1_rule.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classification import division1_game

# ---- 1. the rule on hand-made games
assert division1_game({"season": 1931, "home_conference": None, "away_conference": None}), "pre-1978: everything counts"
assert division1_game({"season": 2013, "home_conference": "Big 12", "away_conference": "Southern"}), "FBS vs FCS counts"
assert not division1_game({"season": 2013, "home_conference": "FCS Independents", "away_conference": None}), \
    "FCS vs a team with no Division I conference (UNC Pembroke, 2013) does not"
assert not division1_game({"season": 2022, "home_conference": "Mountain East", "away_conference": "Great Midwest Athletic"}), "D2 vs D2"
assert division1_game({"season": 2025, "home_division": "fbs", "away_division": "fcs"}), "the per-game classification wins when present"
assert not division1_game({"season": 2025, "home_division": "fbs", "away_division": "ii", "away_conference": "SEC"}), \
    "...even over a misleading conference label"
assert division1_game({"season": 2025, "home_division": "FBS", "away_conference": "Big Ten"}), "case-insensitive, one side by label"
print("1. rule OK")

# ---- 2. the archive: the real belt's own games all pass, and the flipped Baylor-Wofford 2013
#         timeline no longer leaves Division I
ARCHIVE = os.path.join("historical_data", "all_games.json.gz")
LINEAGE = os.path.join("belt_data", "lineage.json")
if os.path.exists(ARCHIVE):
    import json
    import build_conference_lineage as bcl
    games = bcl.load_all_games(include_live=False)
    kept = [g for g in games if division1_game(g)]
    dropped = len(games) - len(kept)
    assert dropped > 0, "the archive has lower-division games to drop"
    if os.path.exists(LINEAGE):
        real = json.load(open(LINEAGE))
        by_key = {(g["date"], frozenset((g["home"], g["away"]))) for g in kept}
        missing = [g for g in real["belt_games"] if g["season"] >= 1978 and g["season"] <= games[-1]["season"]
                   and (g["date"], frozenset((g["home"], g["away"]))) not in by_key]
        assert not missing, f"a real belt game since 1978 would be dropped by the rule: {missing[:3]}"
    # replay the what-if flip: Wofford beats Baylor on 2013-08-31, then follow the schedule under the rule
    i0 = next(i for i, g in enumerate(kept) if g["date"] == "2013-08-31" and {g["home"], g["away"]} == {"Baylor", "Wofford"})
    holder, path = "Wofford", []
    for g in kept[i0 + 1:]:
        if holder not in (g["home"], g["away"]):
            continue
        hp, ap = g["home_points"], g["away_points"]
        winner = g["home"] if hp > ap else (g["away"] if ap > hp else holder)
        path.append((g["date"], holder, winner))
        holder = winner
        if g["date"] > "2014-12-31":
            break
    holders = {h for _, h, _ in path}
    assert "UNC Pembroke" not in holders, "the belt must not fall to a Division II team"
    charlotte = [p for p in path if p[1] == "Charlotte"]
    assert charlotte and charlotte[0][0] > "2013-10-12", "Charlotte keeps the belt past its loss to UNC Pembroke"
    print(f"2. archive OK: {dropped:,} lower-division games dropped; flipped 2013 path stays in Division I "
          f"({len(path)} belt games through 2014, {len(holders)} holders)")
else:
    print("2. (skipped: archive not present)")

print("\nALL DIVISION I RULE TESTS PASSED")
