#!/usr/bin/env python3
"""
Posts automated updates to X (@CollegeFBBelt). Seven independent things
this does, any or all in a single run:

  1. RESULT posts -- one for every belt game since the last run, win or
     lose: both a genuine belt CHANGE and a successful DEFENSE get their
     own post (a defense is not silently skipped -- see below for why).
     On a Saturday this replies to post_live_game.py's final post rather
     than repeating it standalone -- see live_final_tweet_id().
  2. STATE-OF-THE-BELT posts -- Sunday 10:00 AM Eastern, every week of
     the season: where the belt sits after the weekend. Three branches
     (it moved / it was defended / nobody played for it), so it works on
     a bye week as readily as on a week the belt changed hands.
  3. CHALLENGER posts -- Thursday 7:10 PM Eastern: the single most
     surprising computed fact about whoever is coming for the belt this
     week, from challenger.py.
  4. PREVIEW posts -- Friday 6:10 PM Eastern, for the upcoming game,
     pointing at the site's AI-written preview + weather forecast +
     prediction. Carries a card (generate_post_image.py).
  5. POLL posts -- a two-option "does the holder keep it?" poll, Saturday
     9:10 AM Eastern, open until kickoff.
  6. GAME-DAY posts -- "it's game day, the belt is on the line", at 10:00
     AM Eastern on the day of every belt game: matchup, kickoff time, TV,
     venue, what the holder is defending (reign, days, defenses), the
     site's prediction + defend odds, the kickoff forecast, and the
     preview link. Carries a card. See "How a scheduled post picks its
     moment" below.
  7. BIO sync -- keeps the account bio's "Current champion: X" line in
     step with lineage.json's current_holder, whenever it changes (see
     sync_bio() below). Uses the legacy v1.1 API under the hood since
     profile updates aren't exposed on v2's tweepy.Client -- the same
     handle media_upload() needs for the cards.

The weekly shape above was set on 2026-09-23. It replaced a cadence of
roughly fifteen posts on a belt-game week, seven of them an unconditional
daily archive post (post_on_this_day_to_x.py, now weekly), with nine that
each have a reason to exist. Monday is deliberately empty.

Usage:
    export X_API_KEY=...
    export X_API_KEY_SECRET=...
    export X_ACCESS_TOKEN=...
    export X_ACCESS_TOKEN_SECRET=...
    python3 post_to_x.py

OPTIONAL, same pattern as generate_ai_preview.py / generate_recaps.py: if
any of the four X_* env vars aren't set, this prints a note and exits 0
(success) rather than failing the pipeline -- posting to X is a nice-to-
have, not something the site itself depends on.

Why defenses post too, not just changes
----------------------------------------
A defending team can go weeks or months between losses -- Notre Dame's
current reign, for example, could run all season. Posting only on a belt
CHANGE means the account could go quiet for a long stretch even though the
belt holder is actively playing (and winning) every week. Posting every
result -- defense or change -- keeps the account active and gives
followers something every week the holder plays, while still making clear
in the post itself whether the belt changed hands or not.

How result-posting avoids duplicates and a first-run history dump
-------------------------------------------------------------------
belt_data/lineage.json's "belt_games" is the full list of every belt game
ever played, oldest first. social_cache/x_last_posted.json remembers the
game_id of the last one this script posted about; each run posts about
every game AFTER that one, in order, then advances the marker.

The one exception: if social_cache/x_last_posted.json doesn't exist yet
(this script's first-ever run), it does NOT walk the whole 1869-present
history and post hundreds of tweets. It just records the most recent
game as the starting point and posts nothing that run -- posting starts
fresh from the next new game onward.

How a scheduled post picks its moment (2026-09-19, Bob: "an automatic X
post on the day of each belt game, 10 ET"; generalised 2026-09-23)
-----------------------------------------------------------------------
GitHub Actions cron runs on fixed UTC clock times, and 10:00 AM Eastern is
14:00 UTC in daylight time but 15:00 UTC in standard time -- and the
season straddles the switch (first Sunday of November). So the workflow
has a cron line for each (14:00 UTC Aug-Oct, 15:00 UTC Dec-Jan, both in
November) and passes the cron string that fired the run through as
X_FIRED_CRON; is_post_time_tick() then asks "does that cron's UTC time
equal GAMEDAY_POST_TIME_ET (10:00 AM America/New_York) on today's date?"
On any November day exactly one of the two lines answers yes, so the post
lands at a true 10 AM ET year-round -- no drift, and the check depends on
the cron's nominal time rather than the wall clock, so a late-starting
run (GitHub queues scheduled runs under load) still passes. Then it
posts only if today (Eastern) is the date of belt_data/next_game.json's
game, and only once per game ("last_posted_gameday_key" in the cache).
The state-of-the-belt, challenger, preview and poll posts all go through
the same check, via slot_gate(), with their own clock time and a weekday
to match on. The weekday is checked in Eastern rather than read off the
cron line, because a cron line and the post it sends can disagree about
what day it is: Thursday 7:10 PM ET is 23:10 UTC Thursday in summer and
00:10 UTC FRIDAY in winter. The Sunday post needs no cron line of its
own -- the game-day lines already fire at 10:00 ET every day of the
season, and the weekday check picks the Sunday out of them.

A manual "Run workflow" with the post_gameday box ticked sets
X_POST_GAMEDAY=true instead, which skips the tick check but keeps the
game-day and once-per-game checks (so it's safe to use as a same-day
retry if the 10 AM run failed).

social_cache/x_last_posted.json is meant to be git-committed by the
GitHub Actions workflow, the same way historical_data/, ai_preview_cache/
and recap_cache/ already are -- otherwise this whole "already posted"
memory resets on every run and the same things get posted repeatedly.
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, time as dtime, timezone

from challenger import belt_story, short_line
from share_links import tag as tag_link

HERE = os.path.dirname(os.path.abspath(__file__))
BELT_DATA_DIR = os.path.join(HERE, "belt_data")
LINEAGE_PATH = os.path.join(BELT_DATA_DIR, "lineage.json")
NEXT_GAME_PATH = os.path.join(BELT_DATA_DIR, "next_game.json")
AI_PREVIEW_PATH = os.path.join(BELT_DATA_DIR, "ai_preview.json")
BELT_RISK_PATH = os.path.join(BELT_DATA_DIR, "belt_risk.json")
WEATHER_PATH = os.path.join(BELT_DATA_DIR, "weather.json")
RANKINGS_PATH = os.path.join(BELT_DATA_DIR, "rankings.json")
CACHE_DIR = os.path.join(HERE, "social_cache")
CACHE_PATH = os.path.join(CACHE_DIR, "x_last_posted.json")
# post_live_game.py's cache, read-only from here: it carries the id of
# the final post it made, so the result post can reply to it.
LIVE_STATE_PATH = os.path.join(CACHE_DIR, "x_live_state.json")
SITE_URL = "https://collegefootballbelt.com"

REQUIRED_ENV = ["X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]

# When the game-day post goes out, in the belt's home time zone. Changing
# this also means moving the matching cron lines in update-and-deploy.yml
# (they have to land on this clock time in UTC -- see the docstring).
GAMEDAY_POST_TIME_ET = dtime(10, 0)

# The rest of the week (2026-09-23). Same rule as above: changing any of
# these means moving the matching cron lines in update-and-deploy.yml so
# they still land on this clock time in UTC. Monday is deliberately empty.
# Sunday rides the daily 10 AM ET game-day tick that already exists for
# the whole season -- same clock time, and the weekday check below picks
# the Sunday out of it, so it needs no cron line of its own.
STATE_POST_TIME_ET = dtime(10, 0)        # Sunday -- where the belt sits
# The other three are at :10 rather than :00 on purpose. Two cron lines
# in one workflow that name the same minute are ambiguous about which
# schedule string GitHub reports as github.event.schedule, and the daily
# 10 AM lines already occupy :00 at 14:00 and 15:00 UTC. Ten past also
# happens to look less machine-made in a timeline.
PREVIEW_POST_TIME_ET = dtime(18, 10)     # Friday -- the week's belt game
CHALLENGER_POST_TIME_ET = dtime(19, 10)  # Thursday -- who's coming for it
POLL_POST_TIME_ET = dtime(9, 10)         # Saturday -- does it stay put?
THURSDAY, FRIDAY, SATURDAY, SUNDAY = 3, 4, 5, 6   # date.weekday(): Monday is 0
EASTERN = "America/New_York"

# X's hard length limit for a post, in X's own weighted count (see
# tweet_length): 280 for every account, and X Premium's longer posts get
# folded behind "Show more" anyway, so everything here fits in 280.
TWEET_MAX = 280
URL_WEIGHT = 23   # every link counts as 23 no matter how long (t.co wrapping)

# Kept in sync with the account's actual bio on X -- see sync_bio() below.
# Longest holder seen in lineage.json so far ("West Virginia Wesleyan") still
# leaves this at 149 chars, comfortably under X's 160-char bio limit.
BIO_TEMPLATE = ("The lineal college football championship since 1869. "
                 "Whoever last beat the holder, on the field, holds it. "
                 "Current champion: {holder} \U0001F3C6")


# ---------------------------------------------------------------- helpers

def ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def pretty_date(iso_date):
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
        return f"{d:%b} {d.day}, {d:%Y}"
    except (ValueError, TypeError):
        return iso_date or ""


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_cache():
    return load_json(CACHE_PATH) or {"last_posted_game_id": None, "last_posted_preview_key": None}


def save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def team_score(game, team):
    """This game's score for one specific team -- game['score'] is always
    "home-away" (not "holder-opponent"), so this looks up the right half
    by matching against game['home']/game['away'] rather than assuming an
    order. Returns None if `team` isn't in this game (e.g. team is None
    for the very first belt game's "established" row)."""
    if not team or "-" not in (game.get("score") or ""):
        return None
    home_pts, away_pts = game["score"].split("-", 1)
    if team == game.get("home"):
        return home_pts
    if team == game.get("away"):
        return away_pts
    return None


def compute_defense_numbers(belt_games):
    """game_id -> which defense of the CURRENT reign this game was (0 for
    the game that won the belt -- "changed" or "established" -- then 1, 2,
    3... for each successive "retained"/"retained (tie)" until the next
    change). Mirrors how reigns[]'s own "defenses" counter is built."""
    numbers = {}
    n = 0
    for g in belt_games:
        if g["outcome"] in ("changed", "established"):
            n = 0
        else:
            n += 1
        numbers[g["game_id"]] = n
    return numbers


# ------------------------------------------------------------ result post

def compose_result_tweet(game, reign_number, defense_number):
    holder, opponent = game.get("holder"), game.get("opponent")
    new_holder = game["new_holder"]
    date = pretty_date(game["date"])
    game_url = tag_link(f"{SITE_URL}/games/{game['game_id']}.html", "result")

    if game["outcome"] == "established":
        winner_score = team_score(game, new_holder)
        loser_score = team_score(game, opponent)
        return (
            f"\U0001F3C6 The College Football Belt has been established \U0001F3C6\n\n"
            f"{new_holder} defeated {opponent} {winner_score}-{loser_score} "
            f"on {date} -- the very first belt game, dating back to 1869.\n\n"
            f"{game_url}"
        )

    if game["outcome"] == "changed":
        winner_score = team_score(game, new_holder)
        loser_score = team_score(game, holder)
        return (
            f"\U0001F3C6 THE BELT HAS CHANGED HANDS \U0001F3C6\n\n"
            f"{new_holder} defeated {holder} {winner_score}-{loser_score} "
            f"on {date}.\n\n"
            f"{new_holder} is the {ordinal(reign_number)} holder of the "
            f"College Football Belt.\n\n"
            f"{game_url}"
        )

    holder_score = team_score(game, holder)
    opp_score = team_score(game, opponent)
    if game["outcome"] == "retained (tie)":
        return (
            f"\U0001F6E1️ Belt retained (tie) \U0001F6E1️\n\n"
            f"{holder} tied {opponent} {holder_score}-{opp_score} on {date}. "
            f"A tie doesn't change hands, so {holder} keeps the belt -- "
            f"defense #{defense_number}.\n\n"
            f"{game_url}"
        )

    # "retained" -- an ordinary successful defense
    return (
        f"\U0001F6E1️ Belt successfully defended \U0001F6E1️\n\n"
        f"{holder} beat {opponent} {holder_score}-{opp_score} on {date} -- "
        f"defense #{defense_number} of this reign.\n\n"
        f"{game_url}"
    )


def find_new_result_games(lineage, cache):
    games = lineage["belt_games"]
    last_id = cache.get("last_posted_game_id")

    if last_id is None:
        # First-ever run: don't post the entire 1869-present history --
        # just bootstrap the marker at the most recent game and start
        # posting fresh from the NEXT new one.
        return [], (games[-1]["game_id"] if games else None)

    idx = next((i for i, g in enumerate(games) if g["game_id"] == last_id), None)
    if idx is None:
        # Marker points at a game_id we can no longer find (shouldn't
        # normally happen) -- fall back to just the latest game rather
        # than silently losing track.
        new_games = games[-1:]
    else:
        new_games = games[idx + 1:]

    return new_games, last_id


def live_final_tweet_id(game):
    """The id of the live final post for `game`, or None.

    Saturday posts this twice by design: post_live_game.py within a few
    minutes of the whistle, and this script an hour or two later once the
    pipeline has ingested the result. Rather than drop one, the slower
    post replies to the faster one -- the timeline shows a single final,
    with the permanent game-page link hanging off it.

    None here is the ordinary case, not a failure: a Thursday game or a
    bowl outside the live window never had a live post, a live run can
    fail, and this script's checkout can predate the live job's cache
    commit. All three fall back to a standalone post, which is exactly
    what it did before any of this existed.
    """
    state = load_json(LIVE_STATE_PATH)
    if not state or not state.get("final_tweet_id"):
        return None
    holder = game.get("holder") or game.get("new_holder")
    if not holder:
        return None
    # Same shape as post_live_game.game_key(): holder|opponent|date, built
    # from next_game.json there and from the lineage row here.
    if state.get("game_key") != f"{holder}|{game.get('opponent')}|{game.get('date')}":
        return None
    return state["final_tweet_id"]


def post_results(client, lineage, cache):
    new_games, last_id = find_new_result_games(lineage, cache)
    if cache.get("last_posted_game_id") is None:
        cache["last_posted_game_id"] = last_id
        save_cache(cache)
        if not new_games:
            print("First-ever run of post_to_x.py -- bootstrapping the "
                  "result marker at the current game without posting the "
                  "historical backlog. Future belt games will post normally.")

    if not new_games:
        print("No new belt game results to post.")
        return

    defense_numbers = compute_defense_numbers(lineage["belt_games"])
    # reign_number for a "changed"/"established" game is how many reigns
    # exist up to and including the one that game started -- count reigns
    # whose start_date is <= this game's date.
    reign_starts = sorted(r["start_date"] for r in lineage["reigns"])

    for game in new_games:
        reign_number = None
        if game["outcome"] in ("changed", "established"):
            reign_number = sum(1 for d in reign_starts if d <= game["date"])

        text = compose_result_tweet(game, reign_number, defense_numbers.get(game["game_id"]))
        reply_to = live_final_tweet_id(game)
        if reply_to:
            print(f"Replying to the live final post ({reply_to}) rather than "
                  f"posting this result standalone.")

        try:
            response = client.create_tweet(
                text=text, **({"in_reply_to_tweet_id": reply_to} if reply_to else {}))
        except Exception as e:
            print(f"X post FAILED for game {game['game_id']} (not fatal to "
                  f"the pipeline): {e}")
            print("Stopping result-posting here; already-posted games stay "
                  "marked as posted, and this one will be retried next run.")
            break

        print(f"Posted result for game {game['game_id']}: {response}")
        print(text)
        cache["last_posted_game_id"] = game["game_id"]
        save_cache(cache)
        time.sleep(2)  # be polite between consecutive posts


# ----------------------------------------------------------- preview post

def compose_preview_tweet(next_game, ai_preview, belt_risk=None, lineage=None):
    holder, opponent = next_game["team"], next_game["opponent"]
    date = pretty_date(next_game["date"])
    if next_game.get("neutral"):
        matchup = f"{holder} vs. {opponent} (neutral site)"
    elif next_game.get("is_home"):
        matchup = f"{holder} vs. {opponent}"
    else:
        matchup = f"{holder} at {opponent}"

    lines = [
        "\U0001F3C8 Belt on the line this week",
        "",
        f"{matchup} — {date}",
    ]

    if ai_preview and ai_preview.get("predicted_winner") and ai_preview.get("predicted_score"):
        lines.append("")
        lines.append(f"Prediction: {ai_preview['predicted_winner']} "
                      f"({ai_preview['predicted_score']})")

    # Belt-at-risk odds (wishlist #2, 2026-09-16, Bob: "it also feeds the
    # weekly X/IG post automatically") -- only when fetch_belt_odds.py's
    # output actually covers THIS matchup (a stale belt_risk.json for a
    # different opponent is skipped rather than shown wrong).
    if belt_risk and belt_risk.get("next_game", {}).get("opponent") == opponent:
        defend_prob = belt_risk["next_game"].get("defend_prob")
        if defend_prob is not None:
            lines.append("")
            lines.append(f"{holder} is a {round(defend_prob * 100)}% favorite to defend it")

    # One computed fact about the challenger (challenger.py) -- the reason
    # to care about a game between two teams you don't follow. Added last
    # and only if the post still fits X's 280, so it never costs the
    # matchup, the prediction or the odds their place.
    lines.append("")
    lines.append(tag_link(f"{SITE_URL}/preview.html", "preview", bust=next_game.get("id")))
    text = "\n".join(lines)
    if lineage:
        # skip=1: Thursday's challenger post already led with rank 0,
        # so the preview takes the next-best fact rather than repeating it.
        fact = short_line(belt_story(opponent, holder, lineage), skip=1)
        if fact:
            with_fact = "\n".join(lines[:-2] + ["", fact] + lines[-2:])
            if tweet_length(with_fact) <= TWEET_MAX:
                return with_fact
    return text


def preview_cache_key(next_game):
    return f"{next_game['team']}|{next_game['opponent']}|{next_game['date']}"


# ------------------------------------------------------- cards on the posts

def card_media_id(api_v1, kind, **kw):
    """Upload the card for post type `kind` and return its media_id, or
    None -- in which case the caller posts text-only.

    There are three places this can come up empty and none of them is
    fatal: generate_post_image may not be importable (no Pillow on some
    future runner), card() returns None if it couldn't draw the thing,
    and media_upload can fail on its own (rate limit, network, X having a
    bad afternoon). A post with no picture is a worse post; a picture
    that takes the post down with it is no post at all.

    media_upload lives on the legacy v1.1 API only -- tweepy.Client (v2)
    doesn't expose it -- which is the same api_v1 object sync_bio()
    already needs, so this adds no new credentials or scopes.
    """
    if api_v1 is None:
        return None
    try:
        import generate_post_image
    except Exception as e:
        print(f"No card for the {kind} post ({e}) -- posting without one.")
        return None
    path = generate_post_image.card(kind, **kw)
    if not path:
        return None
    try:
        media = api_v1.media_upload(filename=path)
    except Exception as e:
        print(f"Card upload FAILED for the {kind} post ({e}) -- posting without one.")
        return None
    print(f"Uploaded the {kind} card ({path}) as media_id {media.media_id}")
    return media.media_id


def media_kw(media_id):
    """create_tweet(**media_kw(mid)) -- the keyword, or nothing at all."""
    return {"media_ids": [media_id]} if media_id else {}


def post_preview(client, cache, api_v1=None):
    # X_POST_PREVIEW stays set by the workflow on both Friday lines because
    # post_to_instagram.py reads it as "is this the Friday preview run".
    # This script needs the sharper answer -- which of the two lines is
    # actually 6:10 PM Eastern today -- so it uses the same tick check the
    # game-day, challenger and poll posts use, with its own manual flag.
    ok, _today, _tz = slot_gate("preview", "X_POST_PREVIEW_MANUAL",
                                PREVIEW_POST_TIME_ET, FRIDAY)
    if not ok:
        return

    next_game = load_json(NEXT_GAME_PATH)
    if not next_game:
        print("No upcoming game -- nothing to preview.")
        return

    key = preview_cache_key(next_game)
    if cache.get("last_posted_preview_key") == key:
        print("Already posted the preview for this upcoming game -- skipping.")
        return

    ai_preview = load_json(AI_PREVIEW_PATH)
    belt_risk = load_json(BELT_RISK_PATH)
    lineage = load_json(LINEAGE_PATH)
    text = compose_preview_tweet(next_game, ai_preview, belt_risk, lineage)
    media_id = card_media_id(api_v1, "preview", next_game=next_game, lineage=lineage,
                             rankings=load_json(RANKINGS_PATH))

    try:
        response = client.create_tweet(text=text, **media_kw(media_id))
    except Exception as e:
        print(f"X preview post FAILED (not fatal to the pipeline): {e}")
        return

    print(f"Posted preview: {response}")
    print(text)
    cache["last_posted_preview_key"] = key
    save_cache(cache)


# ------------------------------------------------------------- Friday poll

POLL_MAX_MINUTES = 7 * 24 * 60   # X's ceiling for a poll
POLL_OPTION_MAX = 25             # characters per option, X's limit


def compose_poll(next_game):
    """(text, options, duration_minutes) for the Friday 'does the holder
    keep it?' poll -- two options, both inside X's 25-character cap (a long
    program name falls back to a generic label), open until kickoff when
    the kickoff time is known (capped at X's 7-day maximum, floored at an
    hour) and for a day otherwise."""
    holder, opponent = next_game["team"], next_game["opponent"]
    keep = f"{holder} keeps it"
    take = f"{opponent} takes it"
    if len(keep) > POLL_OPTION_MAX:
        keep = "The holder keeps it"
    if len(take) > POLL_OPTION_MAX:
        take = "The challenger takes it"
    date = pretty_date(next_game["date"])
    if next_game.get("neutral"):
        matchup = f"{holder} vs. {opponent} (neutral site)"
    elif next_game.get("is_home"):
        matchup = f"{holder} vs. {opponent}"
    else:
        matchup = f"{holder} at {opponent}"
    text = (f"\U0001F3C8 Belt on the line: {matchup}, {date}.\n\n"
            f"Your call \u2014 does the belt stay put?\n\n"
            + tag_link(f"{SITE_URL}/preview.html", "poll", bust=next_game.get("id")))
    minutes = 24 * 60
    raw = next_game.get("raw_date")
    if raw:
        try:
            from datetime import datetime, timezone
            kick = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            minutes = int((kick - datetime.now(timezone.utc)).total_seconds() // 60)
        except ValueError:
            pass
    minutes = max(60, min(POLL_MAX_MINUTES, minutes))
    return text, [keep, take], minutes


def post_poll(client, cache):
    """The engagement poll. Moved to its own Saturday 9 AM ET slot on
    2026-09-23: it used to fire off the Friday preview run, in the same
    minute as the preview itself, which put two posts from this account
    back to back and gave the poll the worse half of the attention. One
    per upcoming game (cache key), so a rerun never posts a second poll
    for the same game. Not fatal to the pipeline if X refuses it."""
    ok, _today, _tz = slot_gate("poll", "X_POST_POLL", POLL_POST_TIME_ET, SATURDAY)
    if not ok:
        return
    next_game = load_json(NEXT_GAME_PATH)
    if not next_game:
        return
    key = preview_cache_key(next_game)
    if cache.get("last_posted_poll_key") == key:
        print("Already posted the poll for this upcoming game -- skipping.")
        return
    text, options, minutes = compose_poll(next_game)
    try:
        response = client.create_tweet(text=text, poll_options=options, poll_duration_minutes=minutes)
    except Exception as e:
        print(f"X poll post FAILED (not fatal to the pipeline): {e}")
        return
    print(f"Posted poll ({minutes} min): {response}")
    print(text)
    cache["last_posted_poll_key"] = key
    save_cache(cache)


# ----------------------------------------------------------- game-day post

def eastern_tz():
    """America/New_York as a tzinfo. tzdata is in requirements.txt (for
    build_lineage.py's venue-local dates), so this works on GitHub's
    runners and on Windows alike; if the zone database is somehow missing
    the caller treats that as 'skip the game-day post', never as a
    pipeline failure."""
    from zoneinfo import ZoneInfo
    return ZoneInfo(EASTERN)


def tweet_length(text):
    """X's weighted character count, the one its 280 limit is measured
    in: every link counts as 23 (X wraps them in t.co), code points in
    X's 'light' ranges (Latin letters, digits, punctuation, en/em dashes,
    the middle dot) count 1, everything else -- emoji above all -- counts
    2. Slightly conservative for emoji written as multi-code-point
    sequences (each part counts 2 here), which is the safe direction."""
    length = 0
    for chunk in re.split(r"(https?://\S+)", text):
        if chunk.startswith(("http://", "https://")):
            length += URL_WEIGHT
            continue
        for ch in chunk:
            cp = ord(ch)
            if (cp <= 0x10FF or 0x2000 <= cp <= 0x200D
                    or 0x2010 <= cp <= 0x201F or 0x2032 <= cp <= 0x2037):
                length += 1
            else:
                length += 2
    return length


def parse_cron_utc_time(cron):
    """(hour, minute) of a five-field cron string whose minute and hour
    are plain numbers -- '0 14 * 1,8-12 *' -> (14, 0). None for anything
    else (a range like '0 17-23 * * 6', a step, a '*'): those lines are
    the game-day-mode and result check-ins, never the 10 AM tick."""
    parts = (cron or "").split()
    if len(parts) != 5 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    return int(parts[1]), int(parts[0])


def is_post_time_tick(fired_cron, today_et, tz, post_time=None, weekday=None):
    """Is the cron line that fired this run the one meant to send a post
    scheduled for `post_time` Eastern (default: the game-day 10 AM slot)?

    Every slot needs at least two cron lines, because a fixed Eastern
    clock time is two different UTC times across the year -- 10 AM ET is
    14:00 UTC in daylight time and 15:00 UTC in standard time. Rather
    than guess, each line fires and this decides which one was actually
    right for today's date. A run GitHub started late still counts: the
    comparison is against the cron's nominal time, not the wall clock.

    `weekday` (date.weekday(), Monday 0) is the second half of it, and it
    is checked in EASTERN, not UTC. Thursday 7 PM ET is 23:00 UTC on
    Thursday in summer but 00:00 UTC on FRIDAY in winter, so the cron's
    own day-of-week field disagrees with itself twice a year and cannot
    be trusted on its own.
    """
    hm = parse_cron_utc_time(fired_cron)
    if hm is None:
        return False
    if weekday is not None and today_et.weekday() != weekday:
        return False
    post_utc = datetime.combine(today_et, post_time or GAMEDAY_POST_TIME_ET,
                                tzinfo=tz).astimezone(timezone.utc)
    return hm == (post_utc.hour, post_utc.minute)


def slot_gate(label, manual_env, post_time, weekday):
    """(ok, today_et, tz) for a post that runs on one weekly slot.

    Factored out because four posts now need exactly this: a zone, a
    manual override for "Run workflow", and the tick check above. Returns
    ok=False (having said why) rather than raising, so a missing tzdata
    costs one post and not the pipeline.
    """
    manual = os.environ.get(manual_env, "").lower() in ("1", "true", "yes")
    fired_cron = os.environ.get("X_FIRED_CRON", "").strip()
    if not manual and not fired_cron:
        print(f"Not a scheduled run and no {manual_env} ask -- skipping the {label} post.")
        return False, None, None
    try:
        tz = eastern_tz()
    except Exception as e:
        print(f"{label} post skipped -- no America/New_York zone data ({e}); "
              f"pip install tzdata to fix (not fatal to the pipeline).")
        return False, None, None
    today = datetime.now(tz).date()
    if manual:
        return True, today, tz
    if not is_post_time_tick(fired_cron, today, tz, post_time, weekday):
        print(f"Cron {fired_cron!r} isn't the {post_time:%I:%M %p} ET "
              f"{DAY_NAMES[weekday]} tick for {today} -- skipping the {label} post.")
        return False, today, tz
    return True, today, tz


DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")


def kickoff_et(next_game, tz):
    """'7:30 PM ET' from the game's UTC kickoff, the way TV listings say
    it (same rule as build_site.kickoff_et), or None while the slot is
    still TBD -- CFBD then carries a placeholder time we shouldn't print."""
    raw = next_game.get("raw_date")
    if not raw or next_game.get("start_time_tbd"):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(tz)
    return f"{dt:%I:%M %p}".lstrip("0") + " ET"


def team_reign_number(lineage, team):
    """How many reigns `team` has had, counting the current one -- the
    site's 'team_reign_number' (api/current.json), e.g. Notre Dame's 9th."""
    return sum(1 for r in lineage.get("reigns", []) if r.get("team") == team)


def lean_line(ai_preview, belt_risk, holder, opponent):
    """'The Lean: Notre Dame 42-17 · 97% to defend' -- the site's own
    prediction (generate_ai_preview.py, 'The Lean' on the preview page)
    and the model's defend probability (fetch_belt_odds.py), whichever
    of the two exist. None when neither does. belt_risk is only trusted
    when it's about THIS opponent, same rule as the Friday preview."""
    pick = None
    if ai_preview and ai_preview.get("predicted_winner"):
        winner = ai_preview["predicted_winner"]
        nums = re.findall(r"\d+", ai_preview.get("predicted_score") or "")
        if len(nums) == 2 and winner in (holder, opponent):
            hi, lo = sorted((int(nums[0]), int(nums[1])), reverse=True)
            pick = f"{winner} {hi}-{lo}"
        else:
            pick = ai_preview.get("predicted_score") or winner
    pct = None
    if belt_risk and (belt_risk.get("next_game") or {}).get("opponent") == opponent:
        prob = belt_risk["next_game"].get("defend_prob")
        if prob is not None:
            pct = round(prob * 100)
    if pick and pct is not None:
        # name the holder when the pick is the challenger, so "97% to
        # defend" can't read as the challenger's number
        who = "" if pick.startswith(holder) else f"{holder} "
        return f"The Lean: {pick} \u00b7 {who}{pct}% to defend"
    if pick:
        return f"The Lean: {pick}"
    if pct is not None:
        return f"{holder} is a {pct}% favorite to defend the belt"
    return None


def weather_line(weather):
    """'Kickoff forecast: 61°F, clear' (+ wind when it's a factor, + rain
    chance when it's real) from fetch_weather.py's weather.json, or None
    when there's no forecast (venue unknown, game out of range, fetch
    failed -- the file is then literally null)."""
    if not weather or weather.get("temp_f") is None:
        return None
    bits = [f"{round(weather['temp_f'])}\u00b0F"]
    if weather.get("condition") and weather["condition"] != "Unknown":
        bits.append(weather["condition"].lower())
    wind = weather.get("wind_mph")
    if wind is not None and wind >= 15:
        bits.append(f"wind {round(wind)} mph")
    rain = weather.get("precip_chance")
    if rain is not None and rain >= 40:
        bits.append(f"{round(rain)}% chance of rain")
    return "Kickoff forecast: " + ", ".join(bits)


def ranked(team, rankings):
    """'No. 3 Notre Dame' when the latest poll on file ranks the team
    (fetch_rankings.py's rankings.json 'current' block: the CFP rankings
    once they exist, the AP poll before that), else the bare name."""
    current = (rankings or {}).get("current") or {}
    poll = current.get("cfp") or current.get("ap") or {}
    rank = poll.get(team)
    return f"No. {rank} {team}" if rank else team


def stakes_lines(next_game, lineage, today):
    """(long, short, tiny) versions of the stakes line -- the same fact at
    three lengths, so the post can keep it next to the lean even for
    long-named teams -- or (None, None, None) when lineage.json's current
    reign isn't the holder next_game names (a stale next_game.json right
    after a belt change: say nothing rather than something wrong).
      long:  'Notre Dame: 3rd defense of its 9th reign (294 days). Michigan
              State takes the belt with a win.'
      short: 'Notre Dame: 3rd defense of a 294-day reign. Michigan State
              takes the belt with a win.'
      tiny:  'Notre Dame: 3rd defense of a 294-day reign.'"""
    holder, opponent = next_game["team"], next_game["opponent"]
    reign = lineage["reigns"][-1] if lineage.get("reigns") else None
    if not reign or reign.get("team") != holder:
        return None, None, None
    days = (today - date.fromisoformat(reign["start_date"])).days
    defenses = (reign.get("defenses") or 0) + 1
    nth_defense = "first" if defenses == 1 else ordinal(defenses)
    reigns_so_far = team_reign_number(lineage, holder)
    nth_reign = "first" if reigns_so_far == 1 else ordinal(reigns_so_far)
    challenger = f"{opponent} takes the belt with a win."
    day_word = "day" if days == 1 else "days"
    long = (f"{holder}: {nth_defense} defense of its {nth_reign} reign "
            f"({days} {day_word}). {challenger}")
    tiny = f"{holder}: {nth_defense} defense of a {days}-day reign."
    short = f"{tiny} {challenger}"
    return long, short, tiny


def compose_gameday_tweet(next_game, lineage, ai_preview=None, belt_risk=None,
                          weather=None, rankings=None, today=None, tz=None):
    """The 10 AM game-day post. Always: the header, the matchup (with
    'No. N' from the latest poll), kickoff time / TV / venue, and the
    preview link. Then as much of the rest as fits X's 280, in this order
    of preference: the stakes sentence (what the holder is defending,
    what the challenger gets -- a shorter wording is tried before giving
    it up), the site's lean + defend odds, the kickoff forecast. Never
    exceeds TWEET_MAX; if even the four fixed lines don't fit (absurdly
    long names), the venue goes."""
    tz = tz or eastern_tz()
    today = today or datetime.now(tz).date()
    holder, opponent = next_game["team"], next_game["opponent"]
    h, o = ranked(holder, rankings), ranked(opponent, rankings)

    if next_game.get("neutral"):
        matchup = f"{h} vs. {o} (neutral site)"
    elif next_game.get("is_home"):
        matchup = f"{o} at {h}"
    else:
        matchup = f"{h} at {o}"

    when = kickoff_et(next_game, tz) or "Kickoff time TBA"
    if next_game.get("watch"):
        when = f"{when} on {next_game['watch']}"
    venue = next_game.get("venue_name")
    if not venue and next_game.get("venue_city"):
        venue = ", ".join(x for x in (next_game.get("venue_city"), next_game.get("venue_state")) if x)

    header = "\U0001F3C8 GAME DAY \u2014 the belt is on the line"
    link = tag_link(f"{SITE_URL}/preview.html", "gameday", bust=next_game.get("id"))
    long_stakes, short_stakes, tiny_stakes = stakes_lines(next_game, lineage, today)
    lean = lean_line(ai_preview, belt_risk, holder, opponent)
    forecast = weather_line(weather)

    def assemble(details, *extras):
        blocks = [header, "", matchup, details]
        extras = [x for x in extras if x]
        if extras:
            blocks += ["", extras[0]]          # the stakes get their own paragraph
        if len(extras) > 1:
            blocks += [""] + extras[1:]        # lean + forecast share one
        return "\n".join(blocks + ["", link])

    details_full = f"{when} \u00b7 {venue}" if venue else when
    preferences = [
        (long_stakes, lean, forecast),
        (long_stakes, lean, None),
        (short_stakes, lean, None),
        (tiny_stakes, lean, None),
        (long_stakes, None, forecast),
        (long_stakes, None, None),
        (short_stakes, None, None),
        (tiny_stakes, None, None),
        (None, lean, forecast),
        (None, lean, None),
        (None, None, forecast),
        (None, None, None),
    ]
    tried = set()
    for combo in preferences:
        text = assemble(details_full, *combo)
        if text in tried:
            continue
        tried.add(text)
        if tweet_length(text) <= TWEET_MAX:
            return text
    return assemble(when)   # no venue, nothing optional -- always short enough


def gameday_cache_key(next_game):
    return preview_cache_key(next_game)


def post_gameday(client, lineage, cache, api_v1=None):
    """The game-day post, gated three ways (see the docstring): this run
    has to be the 10 AM ET tick (or a manual X_POST_GAMEDAY=true ask),
    today (Eastern) has to be the next belt game's date, and it posts at
    most once per game. Never fatal to the pipeline: any surprise here is
    printed and the rest of the run carries on."""
    manual = os.environ.get("X_POST_GAMEDAY", "").lower() in ("1", "true", "yes")
    fired_cron = os.environ.get("X_FIRED_CRON", "").strip()
    if not manual and not fired_cron:
        print("Not a scheduled run and no X_POST_GAMEDAY ask -- skipping the game-day post.")
        return

    try:
        tz = eastern_tz()
    except Exception as e:
        print(f"Game-day post skipped -- no America/New_York zone data ({e}); "
              f"pip install tzdata to fix (not fatal to the pipeline).")
        return
    now_et = datetime.now(tz)
    today = now_et.date()

    if not manual and not is_post_time_tick(fired_cron, today, tz):
        print(f"Cron {fired_cron!r} isn't the {GAMEDAY_POST_TIME_ET:%I:%M %p} ET tick "
              f"for {today} -- skipping the game-day post.")
        return

    next_game = load_json(NEXT_GAME_PATH)
    if not next_game:
        print("No upcoming game -- nothing to post for game day.")
        return
    if next_game.get("date") != today.isoformat():
        print(f"Today ({today}, Eastern) isn't the next belt game's date "
              f"({next_game.get('date')}) -- skipping the game-day post.")
        return

    key = gameday_cache_key(next_game)
    if cache.get("last_posted_gameday_key") == key:
        print("Already posted the game-day post for this game -- skipping.")
        return

    try:
        text = compose_gameday_tweet(next_game, lineage, load_json(AI_PREVIEW_PATH),
                                     load_json(BELT_RISK_PATH), load_json(WEATHER_PATH),
                                     load_json(RANKINGS_PATH), today=today, tz=tz)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Game-day post skipped -- couldn't compose it ({e}); not fatal to the pipeline.")
        return

    media_id = card_media_id(api_v1, "gameday", next_game=next_game, lineage=lineage,
                             rankings=load_json(RANKINGS_PATH))

    try:
        response = client.create_tweet(text=text, **media_kw(media_id))
    except Exception as e:
        print(f"X game-day post FAILED (not fatal to the pipeline): {e}")
        return

    print(f"Posted game-day post: {response}")
    print(text)
    cache["last_posted_gameday_key"] = key
    save_cache(cache)


# ----------------------------------------------- Sunday: state of the belt

def next_game_line(next_game, today):
    """'Next on the line: Sat Oct 3, at Purdue.' -- or nothing at all when
    the schedule doesn't know yet (the offseason, or a holder whose next
    belt game hasn't been fixed)."""
    if not next_game or not next_game.get("date"):
        return ""
    try:
        when = date.fromisoformat(next_game["date"])
    except (TypeError, ValueError):
        return ""
    if when < today:
        return ""
    where = ("vs. " if next_game.get("is_home") else
             "" if next_game.get("neutral") else "at ")
    opp = next_game.get("opponent") or ""
    if not opp:
        return ""
    return f"Next on the line: {when:%a %b} {when.day}, {where}{opp}."


def compose_state_tweet(lineage, next_game, today):
    """Sunday morning: where the belt sits after the weekend.

    The one post that works every week of the season -- if the belt moved
    that's the news, if it was defended that's the news, and if nobody
    played for it the streak itself is the news. All three branches are
    computed off the same reign row, so none of them can drift out of
    step with the site.
    """
    reigns = lineage.get("reigns") or []
    if not reigns:
        return ""
    reign = reigns[-1]
    holder = reign["team"]
    days = (today - date.fromisoformat(reign["start_date"])).days
    reign_no = team_reign_number(lineage, holder)
    tail = next_game_line(next_game, today)
    link = tag_link(f"{SITE_URL}/", "state")

    # anything that happened in the seven days ending today
    recent = [g for g in (lineage.get("belt_games") or [])
              if 0 <= (today - date.fromisoformat(g["date"])).days <= 6]
    last = recent[-1] if recent else None

    if last and last["outcome"] in ("changed", "established"):
        winner = last["new_holder"]
        loser = last.get("holder") or last["opponent"]
        ws, ls = team_score(last, winner), team_score(last, loser)
        head = f"\U0001F3C6 The belt is with {holder}."
        body = (f"{winner} beat {loser} {ws}-{ls} — the "
                f"{ordinal(len(reigns))} reign in the belt's history, and "
                f"{holder}'s {ordinal(reign_no)}.")
    elif last:
        defenses = reign.get("defenses") or 0
        ws, ls = team_score(last, holder), team_score(last, last["opponent"])
        streak = (f"a {ordinal(defenses)} straight defense" if defenses > 1
                  else "its first defense of this reign")
        head = f"\U0001F6E1️ The belt stays with {holder}."
        body = (f"{holder} turned back {last['opponent']} {ws}-{ls} — {streak}, "
                f"{days:,} days into its {ordinal(reign_no)} reign.")
    else:
        defenses = reign.get("defenses") or 0
        head = "\U0001F6E1️ Another week, and the belt hasn't moved."
        body = (f"{holder}: {days:,} days, {defenses} "
                f"{'defense' if defenses == 1 else 'defenses'}, "
                f"{ordinal(reign_no)} reign.")

    parts = [head, "", body]
    if tail:
        parts += ["", tail]
    parts += ["", link]
    text = "\n".join(parts)
    if tweet_length(text) > TWEET_MAX and tail:
        # the next-game line is the first thing to go -- the site has it
        text = "\n".join([head, "", body, "", link])
    return text


def post_state_of_belt(client, lineage, cache, api_v1=None):
    """Sunday 10 AM ET, every week of the season. Cached by the date, so a
    rerun of the Sunday job never posts a second one."""
    ok, today, _tz = slot_gate("state-of-the-belt", "X_POST_STATE",
                               STATE_POST_TIME_ET, SUNDAY)
    if not ok:
        return
    key = today.isoformat()
    if cache.get("last_posted_state_key") == key:
        print("Already posted the state-of-the-belt post today -- skipping.")
        return
    try:
        text = compose_state_tweet(lineage, load_json(NEXT_GAME_PATH), today)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"State-of-the-belt post skipped -- couldn't compose it ({e}).")
        return
    if not text:
        print("No reign on file -- nothing to say about the state of the belt.")
        return
    try:
        response = client.create_tweet(text=text)
    except Exception as e:
        print(f"X state-of-the-belt post FAILED (not fatal to the pipeline): {e}")
        return
    print(f"Posted state-of-the-belt: {response}")
    print(text)
    cache["last_posted_state_key"] = key
    save_cache(cache)


# ------------------------------------------------- Thursday: the challenger

def compose_challenger_tweet(next_game, lineage, today):
    """The single most surprising computed fact about whoever is coming
    for the belt this week. Takes challenger.py's rank 0; the Friday
    preview takes rank 1, so the two posts never lead with the same
    sentence two days apart."""
    from challenger import belt_story, short_line
    holder, opponent = next_game["team"], next_game["opponent"]
    fact = short_line(belt_story(opponent, holder, lineage, today), limit=200, skip=0)
    if not fact:
        return ""
    when = date.fromisoformat(next_game["date"])
    return (f"\U0001F3C8 {opponent} gets a shot at the belt on {when:%A}.\n\n"
            f"{fact}\n\n" + tag_link(f"{SITE_URL}/preview.html", "challenger",
                                      bust=next_game.get("id")))


def post_challenger(client, lineage, cache, api_v1=None):
    """Thursday 7 PM ET, once per upcoming game. Silent in the offseason
    and on any week the holder isn't playing for it."""
    ok, today, _tz = slot_gate("challenger", "X_POST_CHALLENGER",
                               CHALLENGER_POST_TIME_ET, THURSDAY)
    if not ok:
        return
    next_game = load_json(NEXT_GAME_PATH)
    if not next_game:
        print("No upcoming game -- no challenger to post about.")
        return
    key = preview_cache_key(next_game)
    if cache.get("last_posted_challenger_key") == key:
        print("Already posted the challenger post for this game -- skipping.")
        return
    try:
        text = compose_challenger_tweet(next_game, lineage, today)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Challenger post skipped -- couldn't compose it ({e}).")
        return
    if not text:
        print("challenger.py had nothing short enough to say -- skipping.")
        return
    try:
        response = client.create_tweet(text=text)
    except Exception as e:
        print(f"X challenger post FAILED (not fatal to the pipeline): {e}")
        return
    print(f"Posted challenger: {response}")
    print(text)
    cache["last_posted_challenger_key"] = key
    save_cache(cache)


# --------------------------------------------------------------- bio sync

def sync_bio(api_v1, lineage, cache):
    """Keeps the account's bio's "Current champion: X" line in step with
    lineage.json's current_holder -- runs every pipeline invocation but is a
    no-op (no API call) unless the holder actually changed since the last
    successful sync, tracked by cache['last_synced_bio_holder'].

    Needs the legacy v1.1 API (tweepy.API via OAuth1UserHandler) because
    profile description updates aren't exposed on tweepy.Client (API v2) --
    unlike create_tweet()/follow_user(), which are v2-only above/elsewhere.
    """
    holder = lineage.get("current_holder")
    if not holder:
        return
    if cache.get("last_synced_bio_holder") == holder:
        return

    bio = BIO_TEMPLATE.format(holder=holder)
    try:
        api_v1.update_profile(description=bio)
    except Exception as e:
        print(f"X bio sync FAILED for holder '{holder}' (not fatal to the "
              f"pipeline; will retry next run): {e}")
        return

    print(f"Synced bio -- current champion is now {holder}.")
    cache["last_synced_bio_holder"] = holder
    save_cache(cache)


# ----------------------------------------------------------------- main

def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"Skipping X posts -- missing env var(s): {', '.join(missing)} "
              f"(optional; the site itself doesn't depend on this).")
        return

    if not os.path.exists(LINEAGE_PATH):
        print(f"Skipping X posts -- {LINEAGE_PATH} doesn't exist yet.")
        return

    try:
        import tweepy
    except ImportError:
        sys.exit("tweepy isn't installed -- add it to requirements.txt "
                  "(pip install tweepy) and rerun.")

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_KEY_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    auth_v1 = tweepy.OAuth1UserHandler(
        os.environ["X_API_KEY"],
        os.environ["X_API_KEY_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    api_v1 = tweepy.API(auth_v1)

    lineage = load_json(LINEAGE_PATH)
    cache = load_cache()

    post_results(client, lineage, cache)
    post_preview(client, cache, api_v1)
    post_poll(client, cache)
    post_gameday(client, lineage, cache, api_v1)
    post_state_of_belt(client, lineage, cache, api_v1)
    post_challenger(client, lineage, cache, api_v1)
    sync_bio(api_v1, lineage, cache)


if __name__ == "__main__":
    main()
