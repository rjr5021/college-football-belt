"""generate_post_image: the cards attached to the automated X posts.

Run: python3 test_post_image.py   (no network, no API key)

Two things matter here and they pull in opposite directions. The card has
to say the right thing -- a final that changed hands must not lead with
the team that just lost it -- and it has to be impossible for the card to
take a post down with it. So the branch checks are strict and the failure
checks are deliberately hostile: garbage in, None out, never a traceback
reaching the caller.
"""

import json
import os
import sys
from datetime import date

import generate_post_image as gpi

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = date(2026, 9, 23)

fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        fails.append(label)


def load(name):
    with open(os.path.join(HERE, "belt_data", name)) as f:
        return json.load(f)


LINEAGE = load("lineage.json")
NEXT = load("next_game.json")
try:
    RANKINGS = load("rankings.json")
except FileNotFoundError:
    RANKINGS = {}
COLORS = load("team_colors.json")
HOLDER, OPP = NEXT["team"], NEXT["opponent"]

# 1. the branch that got this wrong the first time
print("1. a final that changes hands leads with the winner")
changed = gpi.final_spec(NEXT, LINEAGE, RANKINGS, 21, 24, TODAY)
check("the right panel carries the accent", changed.get("emphasis") == "right",
      str(changed.get("emphasis")))
check("the buckle shows the NEW holder",
      changed["buckle"] == gpi.team_chip(OPP), changed["buckle"])
check("the headline says so", changed["headline"][1] == "changes hands", str(changed["headline"]))
check("the loser is named the former holder", changed["left"]["role"] == "Former holder")
check("the scores land on the right sides",
      (changed["left"]["score"], changed["right"]["score"]) == (21, 24))

print("2. a final that doesn't")
kept = gpi.final_spec(NEXT, LINEAGE, RANKINGS, 31, 17, TODAY)
check("no emphasis flip", "emphasis" not in kept)
check("the buckle stays with the holder", kept["buckle"] == gpi.team_chip(HOLDER))
check("headline is 'defended'", kept["headline"][1] == "defended", str(kept["headline"]))

print("3. a tie leaves the belt where it is")
tied = gpi.final_spec(NEXT, LINEAGE, RANKINGS, 24, 24, TODAY)
check("not treated as a change", tied["buckle"] == gpi.team_chip(HOLDER))
check("headline is 'retained', not 'defended'", tied["headline"][1] == "retained",
      str(tied["headline"]))

# 4. no card is allowed to reach for a number it doesn't have
print("4. every spec is fully populated")
for name, spec in (("preview", gpi.preview_spec(NEXT, LINEAGE, RANKINGS, TODAY)),
                   ("gameday", gpi.gameday_spec(NEXT, LINEAGE, RANKINGS, TODAY)),
                   ("final-kept", kept), ("final-changed", changed)):
    flat = [spec["kicker"], spec["when"], spec["buckle"], spec.get("caption", "x"),
            spec.get("note", "x")] + [spec[s][k] for s in ("left", "right")
                                      for k in ("team", "role", "stat")]
    check(f"{name}: no empty or None text",
          all(isinstance(v, str) and v.strip() for v in flat), str(flat))
    check(f"{name}: no 'None' leaked into a string",
          not any("None" in v for v in flat), str([v for v in flat if "None" in v]))

# 5. the rendered file
print("5. the PNG itself")
out = os.path.join(gpi.OUT_DIR, "_test.png")
gpi.generate_post_image(kept, out, COLORS)
from PIL import Image
with Image.open(out) as im:
    check("1200x675, the ratio X shows uncropped", im.size == (1200, 675), str(im.size))
check("under 1 MB (X re-encodes past 5)", os.path.getsize(out) < 1_000_000,
      str(os.path.getsize(out)))
os.remove(out)

# 6. a team the colour file has never heard of must still render
print("6. an unknown team falls back rather than raising")
odd = dict(kept)
odd["left"] = dict(kept["left"], team="Not A Real School")
try:
    gpi.generate_post_image(odd, out, COLORS)
    check("renders with fallback colours", os.path.exists(out))
    os.remove(out)
except Exception as e:
    check("renders with fallback colours", False, repr(e))

# 7. the guarantee the whole design rests on: card() never raises
print("7. card() swallows everything and returns None")
check("unknown post type", gpi.card("nonsense", NEXT, LINEAGE, RANKINGS, COLORS) is None)
check("empty next_game", gpi.card("preview", {}, LINEAGE, RANKINGS, COLORS) is None)
check("lineage with no reigns",
      gpi.card("gameday", NEXT, {"reigns": [], "belt_games": []}, RANKINGS, COLORS) is None)
check("final with no scores",
      gpi.card("final", NEXT, LINEAGE, RANKINGS, COLORS, None, None) is None)
check("garbage in every slot", gpi.card("preview", None, None, None, None) is None)

print()
if fails:
    print(f"{len(fails)} FAILURE(S): " + ", ".join(fails))
    sys.exit(1)
print("All generate_post_image checks passed.")
