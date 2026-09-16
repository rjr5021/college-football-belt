#!/usr/bin/env python3
"""Standalone sanity check for the two new Records-page boards added for
task #84 (droughts + belt-held-by-coach) -- synthetic data only, no
network, no real CFBD calls. Not part of the CI pipeline; run by hand:

    python3 test_records_droughts_coaches.py
"""
import sys
from datetime import date

sys.path.insert(0, ".")
import build_site as bs

TODAY = date.today()

# Team A wins in 1990, loses to B in 2000, B holds it to this day.
# Team A's drought should be ~ (today - 2000-01-01).
reigns = [
    {"team": "Team A", "start_date": "1980-01-01", "end_date": "1990-01-01", "defenses": 3},
    {"team": "Team B", "start_date": "1990-01-01", "end_date": "2000-01-01", "defenses": 5},
    {"team": "Team A", "start_date": "2000-01-01", "end_date": "2005-06-15", "defenses": 1},
    {"team": "Team B", "start_date": "2005-06-15", "end_date": None, "defenses": 10},
]
lineage = {"reigns": reigns}

belt_games = [
    {"game_id": "g1", "date": "1980-01-01", "season": 1979, "week": 1, "outcome": "established",
     "home": "Team A", "away": "Team X", "score": "10-0", "holder": None, "new_holder": "Team A"},
    {"game_id": "g2", "date": "1990-01-01", "season": 1989, "week": 1, "outcome": "changed",
     "home": "Team A", "away": "Team B", "score": "7-14", "holder": "Team A", "new_holder": "Team B"},
    {"game_id": "g3", "date": "2000-01-01", "season": 1999, "week": 1, "outcome": "changed",
     "home": "Team B", "away": "Team A", "score": "3-20", "holder": "Team B", "new_holder": "Team A"},
    {"game_id": "g4", "date": "2005-06-15", "season": 2005, "week": 1, "outcome": "changed",
     "home": "Team A", "away": "Team B", "score": "0-1", "holder": "Team A", "new_holder": "Team B"},
]

colors = {}

coaches = {
    "Team A": [
        {"year": 1979, "coach": "Coach Old"},
        {"year": 1999, "coach": "Coach New"},
    ],
    "Team B": [
        {"year": 1989, "coach": "Coach Middle"},
        {"year": 2005, "coach": "Coach Current"},
    ],
}


def check(name, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {name}")
    return cond


def main():
    all_ok = True

    # Team B is the current holder (last reign, end_date None) -- should
    # NOT show up in the droughts board at all.
    html = bs.generate_records_page(lineage, colors, belt_games, coaches)
    droughts_section_start = html.index("Longest Droughts")
    droughts_section_end = html.index("</section>", droughts_section_start)
    droughts_section = html[droughts_section_start:droughts_section_end]
    all_ok &= check("Team A appears in droughts board", "Team A" in droughts_section)
    all_ok &= check("Team B (current holder) does NOT appear in droughts board",
                     "Team B" not in droughts_section)
    all_ok &= check("drought sub-text shows last-held date (2005)",
                     "2005" in droughts_section)

    # Belt-held-by-coach: reign g1 (1980-1990, Team A, season 1979) should
    # attribute to Coach Old (3653 days). Reign g2 (1990-2000, Team B,
    # season 1989) -> Coach Middle. Reign g3 (2000-2005-06-15, Team A,
    # season 1999) -> Coach New. Reign g4 (2005-06-15-present, Team B,
    # season 2005) -> Coach Current, and should be the LONGEST since it's
    # the still-open current reign (biggest of the four spans by far).
    coach_section_start = html.index("Belt Held By Coach")
    coach_section = html[coach_section_start:coach_section_start + 2000]
    for name in ("Coach Old", "Coach Middle", "Coach New", "Coach Current"):
        all_ok &= check(f"{name} appears in by-coach board", name in coach_section)
    # Coach Current should rank #1 (current, still-open, longest reign).
    current_rank_pos = coach_section.index("Coach Current")
    old_rank_pos = coach_section.index("Coach Old")
    all_ok &= check("Coach Current (current, longest reign) ranks above Coach Old",
                     current_rank_pos < old_rank_pos)

    # No coaches.json at all -> card should be omitted entirely (no
    # "Belt Held By Coach" heading), not error out.
    html_no_coaches = bs.generate_records_page(lineage, colors, belt_games, None)
    all_ok &= check("with coaches=None, 'Belt Held By Coach' card is omitted",
                     "Belt Held By Coach" not in html_no_coaches)
    all_ok &= check("with coaches=None, droughts board still renders",
                     "Longest Droughts" in html_no_coaches)

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
