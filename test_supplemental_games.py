"""Tests for historical_data/supplemental_games.json and its plumbing.

1. The file validates (reserved ids, sources present, sane dates/scores).
2. merge_supplemental() skips a supplemental game once CFBD has its own
   copy (same teams, date within two days), and leaves the input alone.
3. Walking the full archive WITH the supplemental games reproduces the
   committed baseline exactly -- i.e. apply_supplemental_games.py has been
   run and nothing has drifted since.
4. The 1931-32 correction comes out as expected: Olympic Club -> Loyola
   Marymount -> San Diego Marines -> Fresno State -> West Coast Army ->
   Stanford -> USC, rejoining the old chain on 1932-10-22.
5. build_site.py's Sources strip gets citations for a supplemental game
   and nothing (so the usual CFBD line) for a CFBD game.

Run from the project folder: python3 test_supplemental_games.py
"""
import gzip
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import supplemental_games as sg
from apply_supplemental_games import FIELDS
from build_lineage import split_at_season, walk

# 1. validation
entries = sg.load_entries()
assert entries, "expected supplemental games in historical_data/supplemental_games.json"
print(f"1. {len(entries)} supplemental games validate")

# 2. dedupe against a CFBD copy
e = entries[0]
fake_cfbd = dict(sg.normalized(e), id=12345, date=e["date"])
games = [fake_cfbd]
merged = sg.merge_supplemental(games, seasons=[e["season"]], quiet=True)
assert [g["id"] for g in merged if g["home"] == e["home"] and g["away"] == e["away"]] == [12345], \
    "a game CFBD already has must not be added twice"
assert games == [fake_cfbd], "merge_supplemental must not modify its input"
assert sg.merge_supplemental([], seasons=[1800], quiet=True) == [], "season filter"
print("2. CFBD copies win; input untouched; season filter works")

# 3. archive + supplemental == committed baseline
with open("historical_data/baseline.json") as f:
    baseline = json.load(f)
lsy = baseline["live_start_year"]
with gzip.open("historical_data/all_games.json.gz", "rt", encoding="utf-8") as f:
    archive = json.load(f)
archive_games = [dict(zip(FIELDS, row)) for s, rows in archive["seasons"].items()
                 if int(s) < lsy for row in rows]
archive_games.sort(key=lambda g: (g["date"], g["season_type"] != "regular", g["id"] or 0))
all_games = sg.merge_supplemental(archive_games, seasons=range(1869, lsy), quiet=True)
bg, rg = walk(all_games, "holder")
hb, hr, op = split_at_season(bg, rg, lsy)
assert hb == baseline["historical_belt_games"], "belt games differ from baseline -- run apply_supplemental_games.py"
assert hr == baseline["historical_reigns"], "reigns differ from baseline -- run apply_supplemental_games.py"
assert op == baseline["open_reign"], "open reign differs from baseline"
print(f"3. archive + supplemental reproduces baseline.json ({len(hb)} belt games, {len(hr)} closed reigns)")

# 4. the 1931-32 chain
window = [r for r in hr if "1931-11-01" <= r["start_date"] <= "1932-10-22"]
got = [(r["team"], r["start_date"], r["defenses"]) for r in window]
want = [
    ("Olympic Club", "1931-11-07", 0),
    ("Loyola Marymount", "1931-11-21", 0),
    ("San Diego Marines", "1931-11-29", 2),
    ("Fresno State", "1932-09-24", 0),
    ("West Coast Army", "1932-10-01", 1),
    ("Stanford", "1932-10-15", 0),
    ("USC", "1932-10-22", 12),
]
assert got == want, f"1931-32 chain:\n  got  {got}\n  want {want}"
print("4. 1931-32 chain is Olympic Club -> Loyola -> Marines -> Fresno State -> West Coast Army -> Stanford -> USC")

# 5. sources strip
esc = lambda t: html.escape(str(t))
s = sg.sources_html(90000001, esc, rel="../")
assert s and "newspapers.com/clip/59494261" in s and "../ruleset.html" in s
assert sg.sources_html(17906, esc) is None
assert sg.sources_html("not-an-id", esc) is None
print("5. sources strip OK")

print("\nALL SUPPLEMENTAL-GAMES TESTS PASSED")
