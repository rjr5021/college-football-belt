"""Tests for post_to_x.py's game-day post (2026-09-19).

1. The 10 AM ET tick: with GitHub's cron string in hand, is_post_time_tick
   picks the 14:00 UTC line in daylight time and the 15:00 UTC line in
   standard time, on both sides of the November switch, in more than one
   year; the result/game-day-mode lines never qualify.
2. tweet_length counts the way X does (links 23, emoji 2, dashes 1).
3. compose_gameday_tweet: the wording for home / road / neutral games,
   TBD kickoffs, rankings, the challenger pick, a first defense, and the
   fact that it NEVER exceeds 280 -- it drops the forecast, then shortens
   the stakes, then drops the lean, then the stakes, then the venue.
4. post_gameday end to end against a fake X client and a scratch
   social_cache: posts once on the right tick on game day, not on the
   other tick, not on a non-game day, not twice, and the manual
   X_POST_GAMEDAY=true path skips the tick check but keeps the rest.

Needs no belt_data/ (everything is synthetic) and no network.
Run from the project folder: python3 test_gameday_post.py
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import post_to_x as p  # noqa: E402

TZ = p.eastern_tz()
TODAY = date(2026, 9, 19)

LINEAGE = {
    "current_holder": "Notre Dame",
    "reigns": [
        {"team": "Rutgers", "start_date": "1869-11-06", "defenses": 0, "end_date": "1869-11-13"},
        # eight earlier Notre Dame reigns, so the current one is its 9th (as in real life)
        *[{"team": "Notre Dame", "start_date": f"{1920 + 10 * i}-10-01", "defenses": 1,
           "end_date": f"{1920 + 10 * i}-11-01"} for i in range(8)],
        {"team": "Stanford", "start_date": "2025-11-01", "defenses": 3, "end_date": "2025-11-29"},
        {"team": "Notre Dame", "start_date": "2025-11-29", "won_from": "Stanford",
         "defenses": 2, "end_date": None, "lost_to": None},
    ],
    "belt_games": [],
}
NEXT_GAME = {
    "id": 401859999, "team": "Notre Dame", "opponent": "Michigan State",
    "is_home": True, "neutral": False, "date": "2026-09-19",
    "raw_date": "2026-09-19T23:30:00.000Z", "start_time_tbd": False,
    "season": 2026, "week": 3, "season_type": "regular",
    "venue_name": "Notre Dame Stadium", "venue_city": "Notre Dame", "venue_state": "IN",
    "tv": "NBC", "stream": "Peacock", "watch": "NBC",
}
AI_PREVIEW = {"predicted_winner": "Notre Dame", "predicted_score": "Notre Dame 42, Michigan State 17"}
BELT_RISK = {"holder": "Notre Dame", "next_game": {"opponent": "Michigan State", "date": "2026-09-19",
                                                    "defend_prob": 0.9712, "source": "cfbd_pregame_wp"}}
WEATHER = {"temp_f": 61.3, "condition": "Clear", "wind_mph": 7.2, "precip_chance": 5}
RANKINGS = {"current": {"season": 2026, "st": "regular", "week": 3,
                        "ap": {"Notre Dame": 3, "Michigan State": 22}, "cfp": {}}}


def compose(**kw):
    args = dict(next_game=NEXT_GAME, lineage=LINEAGE, ai_preview=AI_PREVIEW, belt_risk=BELT_RISK,
                weather=WEATHER, rankings=RANKINGS, today=TODAY, tz=TZ)
    args.update(kw)
    text = p.compose_gameday_tweet(**args)
    assert p.tweet_length(text) <= p.TWEET_MAX, f"over the limit ({p.tweet_length(text)}):\n{text}"
    return text


# 1. the tick ------------------------------------------------------------
ticks = [
    ("0 14 * 8-10 *", date(2026, 9, 19), True), ("0 15 * 11 *", date(2026, 9, 19), False),
    ("0 14 * 11 *", date(2026, 10, 31), True), ("0 15 * 11 *", date(2026, 10, 31), False),
    ("0 14 * 11 *", date(2026, 11, 1), False), ("0 15 * 11 *", date(2026, 11, 1), True),   # DST ends 2026-11-01
    ("0 15 * 12,1 *", date(2027, 1, 19), True), ("0 14 * 12,1 *", date(2027, 1, 19), False),
    ("0 14 * 11 *", date(2027, 11, 6), True), ("0 15 * 11 *", date(2027, 11, 7), True),   # DST ends 2027-11-07
    ("0 17-23 * * 6", date(2026, 9, 19), False), ("0 0-5 * * 0", date(2026, 9, 20), False),
    ("0 18 * * 5", date(2026, 9, 18), False), ("30 4 * * 0", date(2026, 9, 20), False),
    ("", date(2026, 9, 19), False), ("not a cron", date(2026, 9, 19), False),
]
for cron, day, want in ticks:
    assert p.is_post_time_tick(cron, day, TZ) is want, (cron, day, want)
print(f"1. the 10 AM ET tick is picked correctly across DST ({len(ticks)} cases)")

# 2. X's count ------------------------------------------------------------
assert p.tweet_length("https://collegefootballbelt.com/preview.html") == 23
assert p.tweet_length("\U0001F3C8 GAME DAY — the belt is on the line") == 37   # emoji 2, em dash 1
assert p.tweet_length("61°F · clear") == 12   # degree sign and middle dot count 1
assert p.tweet_length("a\nb") == 3
print("2. tweet_length matches X's weighted count")

# 3. wording ----------------------------------------------------------------
t = compose()
assert t.startswith("\U0001F3C8 GAME DAY — the belt is on the line\n\n")
assert "No. 22 Michigan State at No. 3 Notre Dame\n7:30 PM ET on NBC · Notre Dame Stadium" in t
assert "Notre Dame: 3rd defense of a 294-day reign. Michigan State takes the belt with a win." in t
assert "The Lean: Notre Dame 42-17 · 97% to defend" in t
assert t.endswith("\n\nhttps://collegefootballbelt.com/preview.html")
assert "forecast" not in t, "no room for the forecast next to the lean today"

t = compose(rankings=None)
assert "Michigan State at Notre Dame\n" in t and "No. " not in t
assert "3rd defense of its 9th reign (294 days)" in t, "the long stakes fit once the ranks go"

t = compose(next_game=dict(NEXT_GAME, is_home=False, venue_name="Spartan Stadium"),
            ai_preview={"predicted_winner": "Michigan State", "predicted_score": "Notre Dame 20, Michigan State 24"})
assert "No. 3 Notre Dame at No. 22 Michigan State\n7:30 PM ET on NBC · Spartan Stadium" in t
assert "The Lean: Michigan State 24-20 · Notre Dame 97% to defend" in t, "holder named when the pick is the challenger"

t = compose(next_game=dict(NEXT_GAME, neutral=True, venue_name="Aviva Stadium"))
assert "No. 3 Notre Dame vs. No. 22 Michigan State (neutral site)\n7:30 PM ET on NBC · Aviva Stadium" in t

t = compose(next_game=dict(NEXT_GAME, start_time_tbd=True, watch=None, venue_name=None))
assert "\nKickoff time TBA · Notre Dame, IN\n" in t, "TBD slot: no placeholder time, city/state stands in for the venue"

t = compose(ai_preview=None)
assert "Notre Dame is a 97% favorite to defend the belt" in t
t = compose(belt_risk={"next_game": {"opponent": "Purdue", "defend_prob": 0.5}})
assert "The Lean: Notre Dame 42-17\n" in t and "to defend" not in t, "odds for another opponent are ignored"
t = compose(ai_preview=None, belt_risk=None)
assert "Kickoff forecast: 61°F, clear" in t, "the forecast fits once the lean is gone"
assert p.weather_line(dict(WEATHER, wind_mph=22, precip_chance=70, condition="Rain showers")) == \
    "Kickoff forecast: 61°F, rain showers, wind 22 mph, 70% chance of rain"
assert p.weather_line(None) is None and p.weather_line({"temp_f": None}) is None
t = compose(ai_preview=None, belt_risk=None, weather=dict(WEATHER, wind_mph=22, precip_chance=70, condition="Rain showers"))
assert "3rd defense of its 9th reign" in t and "forecast" not in t, "a long forecast loses to the stakes, never the other way round"

fresh = dict(LINEAGE, reigns=LINEAGE["reigns"][:-1] + [dict(LINEAGE["reigns"][-1], start_date="2026-09-12", defenses=0)])
t = compose(lineage=fresh)
assert "Notre Dame: first defense of a 7-day reign." in t

stale = dict(LINEAGE, reigns=LINEAGE["reigns"] + [{"team": "Purdue", "start_date": "2026-09-18", "defenses": 0}])
t = compose(lineage=stale)
assert "defense" not in t, "no stakes sentence when lineage.json's holder isn't next_game's"

long_game = dict(NEXT_GAME, team="Northwestern State", opponent="Southeastern Louisiana", venue_name="Harry Turpin Stadium")
long_lineage = dict(LINEAGE, reigns=LINEAGE["reigns"] + [{"team": "Northwestern State", "start_date": "2026-09-05", "defenses": 1}])
t = compose(next_game=long_game, lineage=long_lineage, rankings=None,
            ai_preview={"predicted_winner": "Northwestern State", "predicted_score": "Northwestern State 31, Southeastern Louisiana 27"},
            belt_risk={"next_game": {"opponent": "Southeastern Louisiana", "defend_prob": 0.61}})
assert "Northwestern State: 2nd defense of a 14-day reign.\n" in t and "The Lean: Northwestern State 31-27 · 61% to defend" in t, \
    "long names: the stakes shrink to the tiny wording so the lean still fits"

absurd = dict(long_game, team="A" * 70, opponent="B" * 70, venue_name="V" * 90)
t = compose(next_game=absurd, lineage=dict(LINEAGE, reigns=LINEAGE["reigns"] + [{"team": "A" * 70, "start_date": "2026-09-05", "defenses": 1}]))
assert "V" * 90 not in t, "absurd names: the venue is the last thing to go"
print("3. wording: home/road/neutral, TBD, ranks, challenger pick, first defense, long names -- all within 280")


# 4. end to end -----------------------------------------------------------
class FakeClient:
    def __init__(self):
        self.posted = []

    def create_tweet(self, text=None, **kw):
        self.posted.append(text)
        return {"data": {"id": str(len(self.posted))}}


def run(env, cache=None, next_game=NEXT_GAME):
    """post_gameday() in a scratch folder with the given env; returns
    (posted texts, cache after)."""
    tmp = tempfile.mkdtemp()
    belt = os.path.join(tmp, "belt_data")
    os.makedirs(belt)
    for name, payload in (("next_game.json", next_game), ("ai_preview.json", AI_PREVIEW),
                          ("belt_risk.json", BELT_RISK), ("weather.json", WEATHER), ("rankings.json", RANKINGS)):
        with open(os.path.join(belt, name), "w") as f:
            json.dump(payload, f)
    saved = {k: getattr(p, k) for k in ("NEXT_GAME_PATH", "AI_PREVIEW_PATH", "BELT_RISK_PATH",
                                        "WEATHER_PATH", "RANKINGS_PATH", "CACHE_DIR", "CACHE_PATH")}
    p.NEXT_GAME_PATH = os.path.join(belt, "next_game.json")
    p.AI_PREVIEW_PATH = os.path.join(belt, "ai_preview.json")
    p.BELT_RISK_PATH = os.path.join(belt, "belt_risk.json")
    p.WEATHER_PATH = os.path.join(belt, "weather.json")
    p.RANKINGS_PATH = os.path.join(belt, "rankings.json")
    p.CACHE_DIR = os.path.join(tmp, "social_cache")
    p.CACHE_PATH = os.path.join(p.CACHE_DIR, "x_last_posted.json")
    old_env = {k: os.environ.get(k) for k in ("X_POST_GAMEDAY", "X_FIRED_CRON")}
    for k in old_env:
        os.environ.pop(k, None)
    os.environ.update(env)
    client = FakeClient()
    cache = dict(cache or {"last_posted_game_id": 1})
    try:
        p.post_gameday(client, LINEAGE, cache)
    finally:
        for k, v in saved.items():
            setattr(p, k, v)
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return client.posted, cache


today_et = p.datetime.now(TZ).date()
game_today = dict(NEXT_GAME, date=today_et.isoformat(),
                  raw_date=f"{today_et.isoformat()}T23:30:00.000Z")
ten_am_utc = p.datetime.combine(today_et, p.GAMEDAY_POST_TIME_ET, tzinfo=TZ).astimezone(p.timezone.utc)
right_tick = f"0 {ten_am_utc.hour} * 1,8-12 *"
wrong_tick = f"0 {(ten_am_utc.hour + 1) % 24} * 1,8-12 *"
key = p.gameday_cache_key(game_today)

posted, cache = run({"X_FIRED_CRON": right_tick}, next_game=game_today)
assert len(posted) == 1 and posted[0].startswith("\U0001F3C8 GAME DAY") and cache["last_posted_gameday_key"] == key, \
    "posts on the 10 AM ET tick on game day"
posted, cache2 = run({"X_FIRED_CRON": right_tick}, cache=cache, next_game=game_today)
assert posted == [] and cache2 == cache, "never twice for the same game"
posted, _ = run({"X_FIRED_CRON": wrong_tick}, next_game=game_today)
assert posted == [], "the other DST twin stays quiet"
posted, _ = run({"X_FIRED_CRON": "0 17-23 * * 6"}, next_game=game_today)
assert posted == [], "game-day-mode hourly runs never post it"
posted, _ = run({}, next_game=game_today)
assert posted == [], "a push / plain manual run never posts it"
tomorrow = today_et + timedelta(days=1)
posted, _ = run({"X_FIRED_CRON": right_tick},
                next_game=dict(game_today, date=tomorrow.isoformat(), raw_date=f"{tomorrow.isoformat()}T23:30:00.000Z"))
assert posted == [], "not game day -> no post"
posted, cache = run({"X_POST_GAMEDAY": "true"}, next_game=game_today)
assert len(posted) == 1 and cache["last_posted_gameday_key"] == key, "the manual ask skips the tick check"
posted, _ = run({"X_POST_GAMEDAY": "true"}, cache=cache, next_game=game_today)
assert posted == [], "...but still never twice"
posted, _ = run({"X_POST_GAMEDAY": "true"}, next_game=None)
assert posted == [], "no upcoming game -> nothing to post"
print("4. post_gameday end to end: right tick posts once, wrong tick / hourly / push / repeat / no game all stay quiet")

print("\nALL CHECKS PASSED")
