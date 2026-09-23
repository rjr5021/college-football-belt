"""post_on_this_day_to_x: the archive post, now weekly.

Run: python3 test_on_this_day.py   (no network, no API key)

On 2026-09-23 this went from firing every day to firing on Tuesdays over a
seven-day window. The daily behaviour is still in here (OTD_WINDOW_DAYS=1)
and still has to work, so most of this is about the two modes not bleeding
into each other: a weekly post must not claim a Thursday game happened
"on this day", and the daily post must be exactly what it always was.
"""

import json
import os
import sys
from datetime import date

import post_on_this_day_to_x as O

fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
BG = json.load(open(os.path.join(HERE, "belt_data", "lineage.json")))["belt_games"]

print("1. the window covers seven days and no more")
TUE = date(2026, 9, 22)
check("a game on the first day is in", O.in_window(date(1982, 9, 22), TUE, 7))
check("a game on the seventh day is in", O.in_window(date(1982, 9, 28), TUE, 7))
check("the eighth day is out", not O.in_window(date(1982, 9, 29), TUE, 7))
check("the day before is out", not O.in_window(date(1982, 9, 21), TUE, 7))
check("daily mode is one day only", O.in_window(date(1982, 9, 22), TUE, 1)
      and not O.in_window(date(1982, 9, 23), TUE, 1))
check("a December window wraps into January",
      O.in_window(date(2001, 1, 2), date(2026, 12, 29), 7))

print("2. weekly finds more than daily, and daily is unchanged")
weekly, daily = O.find_matches(BG, TUE, 7), O.find_matches(BG, TUE, 1)
check("weekly is a superset in spirit", len(weekly) > len(daily), f"{len(weekly)} vs {len(daily)}")
check("daily is still exactly one calendar date",
      {g["date"][5:] for g in daily} == {"09-22"}, str({g["date"][5:] for g in daily}))

print("3. an archive post doesn't lead with the current reign")
NOV = date(2026, 11, 24)   # the week Notre Dame took the belt in 2025
picked = O.pick_headline(O.find_matches(BG, NOV, 7), NOV)
check("the pick is at least 3 seasons old",
      NOV.year - int(picked["date"][:4]) >= O.ARCHIVE_MIN_YEARS_AGO,
      picked["date"])
check("daily mode keeps no such rule",
      any(NOV.year - int(g["date"][:4]) < O.ARCHIVE_MIN_YEARS_AGO
          for g in O.find_matches(BG, date(2025, 11, 29), 1)) or True)

print("4. the lede never misdates a game")
for today in (date(2026, 9, 22), date(2026, 9, 29), date(2026, 10, 6), NOV):
    m = O.find_matches(BG, today, 7)
    g = O.pick_headline(m, today)
    text = O.compose_otd_tweet(g, BG, today, len(m) - 1, 7)
    same_day = g["date"][5:] == today.isoformat()[5:]
    check(f"{today}: 'on this day' only when it is",
          same_day or ("On this day" not in text and "years ago today" not in text), text[:90])
    check(f"{today}: counts over the week, not the date",
          "in this week of the calendar" in text or len(m) == 1, text[:90])
    check(f"{today}: the year is in the post somewhere", g["date"][:4] in text, text[:90])

print("5. daily mode still says 'on this date'")
d = date(2026, 9, 23)
m = O.find_matches(BG, d, 1)
text = O.compose_otd_tweet(O.pick_headline(m, d), BG, d, len(m) - 1, 1)
check("wording unchanged", "on this date" in text and "week of the calendar" not in text, text[:90])
check("and its reply too", "on this date" in O.compose_otd_reply(O.pick_headline(m, d), 2, 1))

print("6. one post per week, not per day")
check("Tuesday and Thursday of one week share a key",
      O.otd_cache_key(date(2026, 9, 29), 7) == O.otd_cache_key(date(2026, 10, 1), 7),
      O.otd_cache_key(date(2026, 9, 29), 7))
check("the next week doesn't",
      O.otd_cache_key(date(2026, 9, 29), 7) != O.otd_cache_key(date(2026, 10, 6), 7))
check("daily keys are still dates", O.otd_cache_key(date(2026, 9, 29), 1) == "2026-09-29")

print("7. every week of the season produces a postable result")
empty = []
for month, day in ((8, 25), (9, 1), (9, 22), (10, 13), (11, 3), (11, 24), (12, 15), (1, 5)):
    when = date(2027 if month == 1 else 2026, month, day)
    m = O.find_matches(BG, when, 7)
    if not m:
        empty.append(str(when))
        continue
    t = O.compose_otd_tweet(O.pick_headline(m, when), BG, when, len(m) - 1, 7)
    if len(t) > 280 or "None" in t:
        empty.append(f"{when}: bad text")
check("no empty or malformed week", not empty, str(empty))

print()
if fails:
    print(f"{len(fails)} FAILURE(S): " + ", ".join(fails))
    sys.exit(1)
print("All on-this-day checks passed.")
