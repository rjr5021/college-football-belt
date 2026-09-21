"""challenger.belt_story: the computed facts about the week's challenger.

Run: python3 test_challenger.py   (no network, no API key)

Fixtures first (so the rules are readable without the archive), then a
replay against the real lineage, which is what catches a wording bug or a
crash on the odd shape -- a team with one reign, a team with none, a
matchup with ties in it.
"""

import json
import os
import sys
from datetime import date

from challenger import belt_story, short_line

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = date(2026, 9, 21)


def game(d, home, away, score, holder, new_holder, outcome):
    return {"date": d, "home": home, "away": away, "score": score,
            "holder": holder, "new_holder": new_holder, "outcome": outcome,
            "game_id": d.replace("-", "")}


def lineage_of(games, reigns):
    return {"belt_games": games, "reigns": reigns}


fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ok   {label}")
    else:
        fails.append(f"{label}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL {label} {detail}")


# 1. a challenger that has taken the belt off this holder twice, both on the
#    same calendar day -- the fact the whole thing exists to find
print("1. the coincidence is found and leads")
games = [
    game("1968-09-28", "Holder U", "Chaser", "22-37", "Holder U", "Chaser", "changed"),
    game("1968-10-05", "Third", "Chaser", "6-43", "Chaser", "Chaser", "retained"),
    game("1968-10-12", "Third", "Chaser", "13-0", "Chaser", "Third", "changed"),
    game("1974-09-28", "Holder U", "Chaser", "20-31", "Holder U", "Chaser", "changed"),
    game("1974-10-05", "Fourth", "Chaser", "16-14", "Chaser", "Fourth", "changed"),
]
reigns = [
    {"team": "Chaser", "start_date": "1968-09-28", "end_date": "1968-10-12", "defenses": 1},
    {"team": "Chaser", "start_date": "1974-09-28", "end_date": "1974-10-05", "defenses": 0},
]
s = belt_story("Chaser", "Holder U", lineage_of(games, reigns), TODAY)
check("two reigns counted", s["reigns"] == 2, str(s["reigns"]))
check("days held sums both reigns", s["days"] == 14 + 7, str(s["days"]))
# Chaser holds it in three games here: one defense (1968-10-05) and the two
# that ended each reign -- 1-2, counted from the games, not the reign rows
check("record as holder is 1-2", (s["defenses"], s["losses_as_holder"]) == (1, 2),
      f'{s["defenses"]}-{s["losses_as_holder"]}')
check("took it off this holder twice", s["vs_takes"] == 2, str(s["vs_takes"]))
check("the same-day coincidence leads", "September 28" in s["ranked"][0], s["ranked"][0])
check("the lead line names both teams", "Chaser" in s["ranked"][0] and "Holder U" in s["ranked"][0])
check("both years are in it", "1968" in s["ranked"][0] and "1974" in s["ranked"][0])
check("no robot plurals", "2 times" not in " ".join(s["lines"]), " ".join(s["lines"]))

# 2. same, one month apart rather than one day: the weaker coincidence
print("2. same month, different days -> the month version")
games2 = [g.copy() for g in games]
games2[3]["date"] = "1974-09-21"
s2 = belt_story("Chaser", "Holder U", lineage_of(games2, reigns), TODAY)
check("month coincidence found", "September" in s2["ranked"][0] and "28" not in s2["ranked"][0],
      s2["ranked"][0])

# 3. no coincidence at all -> the plain "last on" line, never a false pattern
print("3. no pattern -> no claim of one")
games3 = [g.copy() for g in games]
games3[3]["date"] = "1974-11-02"
s3 = belt_story("Chaser", "Holder U", lineage_of(games3, reigns), TODAY)
joined = " ".join(s3["lines"])
check("no 'every time' claim", "every time" not in joined, joined)
check("still reports the takes", "taken the belt off Holder U twice" in joined, joined)

# 4. a team that has never held it
print("4. never held it")
never = lineage_of([game("2013-09-07", "Baylor", "Nobody", "70-7", "Baylor", "Baylor", "retained")], [])
s4 = belt_story("Nobody", "Holder U", never, TODAY)
check("says so plainly", s4["ranked"][0].startswith("Nobody has never held the belt"), s4["ranked"][0])
check("singular reads right", "one game" in s4["ranked"][0] and "lost it" in s4["ranked"][0], s4["ranked"][0])
check("no reigns, no days", (s4["reigns"], s4["days"]) == (0, 0))

# 5. ties are not silently dropped from a head-to-head record
print("5. ties show up in the head-to-head")
tie_games = [
    game("1890-11-27", "A", "B", "0-32", "A", "B", "changed"),
    game("1891-11-26", "A", "B", "6-6", "B", "B", "retained"),
]
tie_reigns = [{"team": "B", "start_date": "1890-11-27", "end_date": "1892-01-01", "defenses": 1}]
s5 = belt_story("B", "A", lineage_of(tie_games, tie_reigns), TODAY)
vs_line = next(l for l in s5["lines"] if "have met" in l)
check("record carries the tie", "1–0–1" in vs_line, vs_line)
check("and explains it", "ties" in vs_line, vs_line)

# 6. the real archive: every team that has ever played a belt game
print("6. archive replay")
lineage = json.load(open(os.path.join(HERE, "belt_data", "lineage.json")))
teams = sorted({t for g in lineage["belt_games"] for t in (g["home"], g["away"])})
holder = lineage["reigns"][-1]["team"]
bad = []
for t in teams:
    st = belt_story(t, holder, lineage, TODAY)
    if not st["lines"] or not st["ranked"] or not short_line(st):
        bad.append((t, "empty"))
    elif any(not l.strip().endswith(".") for l in st["lines"]):
        bad.append((t, "unterminated sentence"))
    elif any("  " in l or " ," in l or "None" in l for l in st["lines"]):
        bad.append((t, "formatting"))
check(f"all {len(teams)} teams produce clean sentences", not bad, str(bad[:4]))
short = [t for t in teams if len(short_line(belt_story(t, holder, lineage, TODAY))) > 150]
check("every post line fits 150 characters", not short, str(short[:4]))
me = belt_story(holder, holder, lineage, TODAY)
check("a team against itself doesn't crash", bool(me["lines"]))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("All challenger.py checks passed.")
