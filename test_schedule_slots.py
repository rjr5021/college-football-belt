"""post_to_x: which run is allowed to send which scheduled post.

Run: python3 test_schedule_slots.py   (no network, no API key)

GitHub Actions cron is fixed-UTC and knows nothing about daylight saving,
so every weekly post has TWO cron lines and the script decides which one
was actually right for today's date. The failure this guards against is
the quiet one: a post going out an hour off, or twice, or on the wrong
day, for half the season -- and November, where the clocks change
mid-month, is where that shows up.

Dates used below are real 2026/27 calendar days, chosen either side of the
US switch (Nov 1, 2026).
"""

import sys
from datetime import date

import post_to_x as P

fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        fails.append(label)


TZ = P.eastern_tz()


def fires(cron, day, post_time, weekday):
    return P.is_post_time_tick(cron, day, TZ, post_time, weekday)


# --- the four slots, each with its daylight-time and standard-time line ---
SLOTS = [
    ("Thursday challenger", P.CHALLENGER_POST_TIME_ET, P.THURSDAY,
     "10 23 * * 4", "10 0 * * 5", date(2026, 10, 1), date(2026, 12, 3)),
    ("Friday preview", P.PREVIEW_POST_TIME_ET, P.FRIDAY,
     "10 22 * * 5", "10 23 * * 5", date(2026, 10, 2), date(2026, 12, 4)),
    ("Saturday poll", P.POLL_POST_TIME_ET, P.SATURDAY,
     "10 13 * * 6", "10 14 * * 6", date(2026, 10, 3), date(2026, 12, 5)),
    ("Sunday state", P.STATE_POST_TIME_ET, P.SUNDAY,
     "0 14 * 8-10 *", "0 15 * 12,1 *", date(2026, 10, 4), date(2026, 12, 6)),
]

print("1. exactly one line fires per slot, and it's the right one")
for label, t, wd, edt_cron, est_cron, edt_day, est_day in SLOTS:
    check(f"{label}: the daylight line fires in daylight time", fires(edt_cron, edt_day, t, wd))
    check(f"{label}: the standard line does not", not fires(est_cron, edt_day, t, wd))
    check(f"{label}: the standard line fires in standard time", fires(est_cron, est_day, t, wd))
    check(f"{label}: the daylight line does not", not fires(edt_cron, est_day, t, wd))

print("2. November, where the clocks change mid-month")
# 2026's switch is Sunday Nov 1. Thursday Nov 5 is already standard time;
# Thursday Oct 29 is still daylight.
t, wd = P.CHALLENGER_POST_TIME_ET, P.THURSDAY
check("Oct 29 takes the daylight line", fires("10 23 * * 4", date(2026, 10, 29), t, wd))
check("Oct 29 refuses the standard line", not fires("10 0 * * 5", date(2026, 10, 29), t, wd))
check("Nov 5 takes the standard line", fires("10 0 * * 5", date(2026, 11, 5), t, wd))
check("Nov 5 refuses the daylight line", not fires("10 23 * * 4", date(2026, 11, 5), t, wd))

print("3. the weekday check, which the cron field alone can't be trusted for")
# The whole reason it exists: in standard time the Thursday 7:10pm slot is
# a FRIDAY cron line, so the cron's own day-of-week says Friday while the
# post belongs to Thursday.
check("the Thursday slot refuses a Friday", not fires("10 0 * * 5", date(2026, 12, 4), t, wd))
check("the Thursday slot refuses a Wednesday", not fires("10 0 * * 5", date(2026, 12, 2), t, wd))
check("the Sunday slot refuses the same cron on a Saturday",
      not fires("0 14 * 8-10 *", date(2026, 10, 3), P.STATE_POST_TIME_ET, P.SUNDAY))
check("...and accepts it on the Sunday",
      fires("0 14 * 8-10 *", date(2026, 10, 4), P.STATE_POST_TIME_ET, P.SUNDAY))

print("4. no slot answers to another slot's line")
for label, t, wd, edt_cron, _e, edt_day, _d in SLOTS:
    for other_label, other_t, other_wd, other_cron, _o, other_day, _x in SLOTS:
        if label == other_label:
            continue
        check(f"{label}'s line doesn't fire {other_label}",
              not fires(edt_cron, other_day, other_t, other_wd),
              f"{edt_cron} on {other_day}")

print("5. the ranged and stepped lines are never a post slot")
for cron in ("0 17-23 * * 6", "0 0-5 * * 0", "*/5 16-23 * * *", "", None):
    check(f"{cron!r} fires nothing",
          not any(fires(cron, d, t, wd) for _l, t, wd, _a, _b, d, _c in SLOTS))

print("6. the game-day post's own behaviour is unchanged")
check("the default slot is still 10 AM with no weekday rule",
      P.is_post_time_tick("0 14 * 8-10 *", date(2026, 9, 26), TZ))
check("and it still refuses an hourly line",
      not P.is_post_time_tick("0 17-23 * * 6", date(2026, 9, 26), TZ))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("All schedule-slot checks passed.")
