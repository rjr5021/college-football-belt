"""build_site.belt_reach: the fewest games that could carry the belt to each team.

Run: python3 test_belt_reach.py   (no network, no API key)

The property that matters is not "is there a path" -- by four or five
games most of Division I has one -- but "how many games, and which". This
checks the search returns the minimum, that every chain it returns is a
legal one (strictly later each time, each step starting from whoever won
the step before), and that it never invents a path where none exists.
"""

import sys

from build_site import belt_reach

fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        fails.append(label)


def game(gid, day, home, away, conf="Big Ten", away_conf=None):
    return {"id": gid, "date": day, "season": 2026, "home": home, "away": away,
            "home_conference": conf, "away_conference": away_conf or conf}


def legal(chain, holder, games_by_id):
    """A chain is legal when each step is a real game between the previous
    holder and the winner, and the dates strictly increase."""
    prev, last_day = holder, ""
    for step in chain:
        g = games_by_id.get(step["id"])
        if not g:
            return False
        if step["loser"] != prev or step["winner"] not in (g["home"], g["away"]):
            return False
        if prev not in (g["home"], g["away"]):
            return False
        if not (last_day < step["date"] == g["date"]):
            return False
        prev, last_day = step["winner"], step["date"]
    return True


# 1. the case that prompted this: five games gets there sooner, three is the answer
print("1. fewest games, not soonest arrival")
GAMES = [
    game(1, "2026-09-26", "Purdue", "Notre Dame", "Big Ten", "FBS Independents"),
    game(2, "2026-10-03", "Illinois", "Purdue"),
    game(3, "2026-10-10", "Michigan State", "Illinois"),
    game(4, "2026-10-17", "Northwestern", "Michigan State"),
    game(5, "2026-10-23", "Northwestern", "Rutgers"),
    game(6, "2026-10-31", "Penn State", "Purdue"),
    game(7, "2026-11-21", "Rutgers", "Penn State"),
]
by_id = {g["id"]: g for g in GAMES}
reach = belt_reach("Notre Dame", GAMES)
check("holder's own chain is empty", reach["Notre Dame"] == [])
check("a team that plays the holder is one game", len(reach["Purdue"]) == 1)
check("Rutgers is three games, not five", len(reach["Rutgers"]) == 3,
      str([s["winner"] for s in reach["Rutgers"]]))
check("and it is the Penn State route",
      [s["winner"] for s in reach["Rutgers"]] == ["Purdue", "Penn State", "Rutgers"],
      str([s["winner"] for s in reach["Rutgers"]]))
check("a team only reachable the long way keeps the long chain",
      len(reach["Northwestern"]) == 4, str(len(reach["Northwestern"])))
check("every chain is legal", all(legal(c, "Notre Dame", by_id) for c in reach.values()))

# 2. time only runs forwards
print("2. a game before the belt could arrive is not a path")
BACKWARDS = [
    game(1, "2026-11-01", "Purdue", "Notre Dame", "Big Ten", "FBS Independents"),
    game(2, "2026-09-01", "Rutgers", "Purdue"),      # earlier than Purdue could have it
]
r2 = belt_reach("Notre Dame", BACKWARDS)
check("Purdue is reachable", "Purdue" in r2)
check("Rutgers is not, the game came first", "Rutgers" not in r2, str(r2.get("Rutgers")))

# 3. games between two teams the belt cannot reach add nobody
print("3. unrelated games add nobody")
r3 = belt_reach("Notre Dame", [game(1, "2026-10-03", "Michigan", "Wisconsin")])
check("nobody but the holder", set(r3) == {"Notre Dame"}, str(set(r3)))

# 4. the Division I rule applies here too
print("4. non-Division I games cannot move it")
LOWER = [
    {"id": 1, "date": "2026-09-26", "season": 2026, "home": "Wofford", "away": "Notre Dame",
     "home_conference": "Southern", "away_conference": "FBS Independents"},
    {"id": 2, "date": "2026-10-03", "season": 2026, "home": "Someone D2", "away": "Notre Dame",
     "home_conference": None, "away_conference": "FBS Independents"},
]
r4 = belt_reach("Notre Dame", LOWER)
check("an FCS opponent can take it", "Wofford" in r4)
check("an unclassified one cannot", "Someone D2" not in r4, str(sorted(r4)))

# 5. a long chain still terminates and stays legal
print("5. a long chain")
LONG = [game(i + 1, f"2026-{9 + (i // 4):02d}-{1 + (i % 4) * 7:02d}", f"T{i + 1}", f"T{i}")
        for i in range(1, 11)]
LONG.insert(0, game(100, "2026-09-01", "T1", "Holder", "Big Ten", "Big Ten"))
r5 = belt_reach("Holder", LONG)
by_id5 = {g["id"]: g for g in LONG}
check("the far end is reached", "T11" in r5, str(sorted(r5)))
check("its chain is legal", legal(r5.get("T11") or [], "Holder", by_id5))
check("and each step is one longer than the last",
      all(len(r5[f"T{i}"]) == i for i in range(1, 12) if f"T{i}" in r5))

print()
if fails:
    print(f"{len(fails)} FAILURE(S): " + ", ".join(fails))
    sys.exit(1)
print("All belt_reach checks passed.")
