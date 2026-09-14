#!/usr/bin/env python3
"""
Posts automated updates to X (@CollegeFBBelt). Two independent things this
does, either or both in a single run:

  1. RESULT posts -- one for every belt game since the last run, win or
     lose: both a genuine belt CHANGE and a successful DEFENSE get their
     own post (a defense is not silently skipped -- see below for why).
  2. PREVIEW posts -- one post for the upcoming game, meant to go out the
     Friday before it, pointing at the site's AI-written preview + weather
     forecast + prediction.

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

How preview-posting decides "is this Friday"
-----------------------------------------------
This does NOT guess the day of the week itself. GitHub Actions' schedule
trigger tells a running workflow which cron line fired it (a run started
by any other means -- workflow_dispatch, a push -- leaves that unset), so
the workflow passes that through as X_POST_PREVIEW=true only on the one
cron line timed for Friday evening ET; every other trigger leaves it
unset/false, and this script simply trusts that flag rather than
recomputing it. A preview only ever posts once per upcoming game either
way (tracked by social_cache/x_last_posted.json's
"last_posted_preview_key"), so even a Friday push wouldn't double-post.

social_cache/x_last_posted.json is meant to be git-committed by the
GitHub Actions workflow, the same way historical_data/, ai_preview_cache/
and recap_cache/ already are -- otherwise this whole "already posted"
memory resets on every run and the same things get posted repeatedly.
"""

import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BELT_DATA_DIR = os.path.join(HERE, "belt_data")
LINEAGE_PATH = os.path.join(BELT_DATA_DIR, "lineage.json")
NEXT_GAME_PATH = os.path.join(BELT_DATA_DIR, "next_game.json")
AI_PREVIEW_PATH = os.path.join(BELT_DATA_DIR, "ai_preview.json")
CACHE_DIR = os.path.join(HERE, "social_cache")
CACHE_PATH = os.path.join(CACHE_DIR, "x_last_posted.json")
SITE_URL = "https://collegefootballbelt.com"

REQUIRED_ENV = ["X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]


# ---------------------------------------------------------------- helpers

def ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def pretty_date(iso_date):
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %-d, %Y")
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
    game_url = f"{SITE_URL}/games/{game['game_id']}.html"

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

        try:
            response = client.create_tweet(text=text)
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

def compose_preview_tweet(next_game, ai_preview):
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

    lines.append("")
    lines.append(f"{SITE_URL}/preview.html")
    return "\n".join(lines)


def preview_cache_key(next_game):
    return f"{next_game['team']}|{next_game['opponent']}|{next_game['date']}"


def post_preview(client, cache):
    if os.environ.get("X_POST_PREVIEW", "").lower() not in ("1", "true", "yes"):
        print("Not the scheduled Friday preview run -- skipping the preview post.")
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
    text = compose_preview_tweet(next_game, ai_preview)

    try:
        response = client.create_tweet(text=text)
    except Exception as e:
        print(f"X preview post FAILED (not fatal to the pipeline): {e}")
        return

    print(f"Posted preview: {response}")
    print(text)
    cache["last_posted_preview_key"] = key
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

    lineage = load_json(LINEAGE_PATH)
    cache = load_cache()

    post_results(client, lineage, cache)
    post_preview(client, cache)


if __name__ == "__main__":
    main()
