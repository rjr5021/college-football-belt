#!/usr/bin/env python3
"""
Posts a daily "On this day" tweet to X (@CollegeFBBelt) during football
season -- one post a day spotlighting a belt game that happened on this
exact month+day in a past year, the same data already powering the
homepage's "On this day" widget and the standalone on-this-day.html page.

Deliberately a SEPARATE script from post_to_x.py, and meant to run from
its own lightweight daily workflow (post-on-this-day.yml) rather than
inside update_all.py's full pipeline:

  - post_to_x.py posts about NEW results (a game that just happened) and
    runs only when the main pipeline runs (a handful of times a week,
    timed around actual kickoffs) -- it has nothing to do on a random
    Tuesday with no game.
  - This script posts about OLD results (something that happened on this
    date in a past year) and wants to run every single day of the
    season regardless of whether today has a game -- that's the whole
    point of an "on this day" post.

Where the data comes from
--------------------------
Rather than re-fetching from CFBD (which would blow through the 1,000
calls/month free-tier cap running daily on top of the main pipeline's own
usage) or requiring belt_data/lineage.json to exist locally (it isn't
git-committed -- see update-and-deploy.yml's comments), this pulls the
same data the site already publishes for exactly this purpose: the public
GET https://collegefootballbelt.com/api/games.json endpoint (see api.html
/ generate_api_files() in build_site.py), refreshed by every ordinary
pipeline run. A plain server-side HTTPS GET like this isn't subject to
the custom-domain CORS gap that affects browser-side fetches of the same
endpoint (see api.html's own caveat) -- CORS only applies to browsers.

Usage:
    export X_API_KEY=...
    export X_API_KEY_SECRET=...
    export X_ACCESS_TOKEN=...
    export X_ACCESS_TOKEN_SECRET=...
    python3 post_on_this_day_to_x.py

OPTIONAL, same pattern as post_to_x.py: if any of the four X_* env vars
aren't set, this prints a note and exits 0 rather than failing.

How this avoids duplicate posts
---------------------------------
social_cache/x_otd_last_posted.json remembers the ISO date (in US/Eastern,
matching the site's own framing of "today") this script last posted an
on-this-day tweet for. If that date is today, it skips -- covers the
workflow firing more than once on the same calendar day (a manual re-run,
a retry) without double-posting. A day with zero matching belt games
still updates the marker (nothing to say, but also nothing to retry).

Picking which game to headline
---------------------------------
A given month+day can have more than one belt game across 150+ years of
history. Posting all of them as one flat list reads worse on X than
spotlighting the single most compelling one -- so this scores every match
(milestone anniversary > belt actually changing hands > a defended tie >
an ordinary defense, then prefers the most recent year as a tiebreak) and
leads with the winner, mentioning how many others happened on this date
only as a short footer, with a link to browse the rest posted as a reply
(see "Reply-for-links" below).

Reply-for-links, and a share image
-------------------------------------
Two changes made 2026-09 after researching X's current (2026) ranking
behavior, both aimed at the same goal -- more reach on a post that was
already fine content-wise, not more hashtags (1-2 is the researched
sweet spot; #CFB alone is already there):

  1. A link in the body of the main post measurably suppresses reach
     (X's own ranking penalizes off-platform links in-thread). So the
     main tweet now carries zero URLs -- it ends with a short "reply
     below" cue instead -- and a *separate* reply tweet, posted right
     after, carries the actual link(s). Replies aren't penalized the
     same way, and a reader who wants the link still gets it one tap
     away.
  2. The main tweet now attaches a generated share image (see
     generate_share_image.py's generate_otd_share_image()) built from
     the same team-color/logo data the site itself uses -- images
     meaningfully outperform text-only posts. This needs team colors,
     which aren't in games.json, so this script also fetches the site's
     api/colors.json (see build_site.py's generate_api_files()). Image
     generation is best-effort: any failure (network, missing Pillow
     fonts, a team with no color data) just posts without an image
     rather than failing the whole run.
"""

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover -- zoneinfo should always be present on 3.9+
    EASTERN = None

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "social_cache")
CACHE_PATH = os.path.join(CACHE_DIR, "x_otd_last_posted.json")
SITE_URL = "https://collegefootballbelt.com"
GAMES_API_URL = f"{SITE_URL}/api/games.json"
COLORS_API_URL = f"{SITE_URL}/api/colors.json"

REQUIRED_ENV = ["X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]

# Anniversaries worth calling out by name in the opening line -- everything
# else just says "on this day in <year>".
MILESTONE_YEARS = {5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 100, 110, 120, 125, 130, 140, 150}


# ---------------------------------------------------------------- helpers

def ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def eastern_today():
    if EASTERN is not None:
        return datetime.now(EASTERN).date()
    # Fallback: approximate ET as UTC-5 if the zoneinfo database is
    # somehow unavailable -- close enough for picking a calendar date.
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_cache():
    return load_json(CACHE_PATH) or {"last_posted_date": None}


def save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def fetch_belt_games():
    import requests
    resp = requests.get(GAMES_API_URL, timeout=30)
    resp.raise_for_status()
    return resp.json()["belt_games"]


def fetch_team_colors():
    """The site's public api/colors.json -- {"teams": {name: {color,
    alternate_color, logo}}}. Only used for the share image; any failure
    here should degrade to a text-only post, never abort the run, so
    callers are expected to wrap this in a try/except."""
    import requests
    resp = requests.get(COLORS_API_URL, timeout=30)
    resp.raise_for_status()
    return resp.json()["teams"]


def team_score(game, team):
    if not team or "-" not in (game.get("score") or ""):
        return None
    home_pts, away_pts = game["score"].split("-", 1)
    if team == game.get("home"):
        return home_pts
    if team == game.get("away"):
        return away_pts
    return None


def compute_defense_numbers(belt_games):
    """Same logic as post_to_x.py's helper of the same name -- game_id ->
    which defense of that reign the game was (0 for the game that won the
    belt, then 1, 2, 3... until the next change)."""
    numbers = {}
    n = 0
    for g in sorted(belt_games, key=lambda g: g["date"]):
        if g["outcome"] in ("changed", "established"):
            n = 0
        else:
            n += 1
        numbers[g["game_id"]] = n
    return numbers


def compute_reign_number(game, belt_games):
    """How many reigns exist up to and including the one this game
    started -- only meaningful for a "changed"/"established" game."""
    starts = sorted(g["date"] for g in belt_games if g["outcome"] in ("changed", "established"))
    return sum(1 for d in starts if d <= game["date"])


# --------------------------------------------------------- match finding

def find_matches(belt_games, today):
    """Every belt game that happened on today's month+day in a past year,
    most recent year first -- mirrors build_site.py's render_on_this_day()."""
    matches = [g for g in belt_games
               if date.fromisoformat(g["date"]).month == today.month
               and date.fromisoformat(g["date"]).day == today.day
               and date.fromisoformat(g["date"]) != today]
    matches.sort(key=lambda g: g["date"], reverse=True)
    return matches


OUTCOME_TIER = {"established": 3, "changed": 2, "retained (tie)": 1, "retained": 0}


def score_match(game, today):
    """A (tier, milestone, year) tuple, compared lexicographically by
    max() below -- outcome tier always wins first (the belt's one-ever
    "established" game beats every "retained" no matter how round the
    anniversary; a hands-change beats a defense the same way), a milestone
    anniversary only breaks ties WITHIN the same tier, and the most recent
    year breaks any remaining tie."""
    year = int(game["date"][:4])
    years_ago = today.year - year
    tier = OUTCOME_TIER.get(game["outcome"], 0)
    milestone = 1 if years_ago in MILESTONE_YEARS else 0
    return (tier, milestone, year)


def pick_headline(matches, today):
    return max(matches, key=lambda g: score_match(g, today))


# ------------------------------------------------------------ composing

def compose_otd_tweet(game, belt_games, today, extra_count):
    year = int(game["date"][:4])
    years_ago = today.year - year
    winner_score = team_score(game, game["new_holder"])
    # The non-winning team, regardless of outcome type (the "established"
    # / "changed" rows don't carry a separate "loser" field).
    other_team = game["away"] if game["new_holder"] == game["home"] else game["home"]
    other_score = team_score(game, other_team)

    milestone = years_ago in MILESTONE_YEARS
    lede_year = f"{years_ago} years ago today" if milestone else f"On this day in {year}"

    if game["outcome"] == "established":
        hook = f"\U0001F3C6 {lede_year}, the College Football Belt was born."
        body = (f"{game['new_holder']} beat {other_team} {winner_score}-{other_score} "
                f"in the very first belt game -- everything since traces back to this.")
    elif game["outcome"] == "changed":
        reign_number = compute_reign_number(game, belt_games)
        hook = f"\U0001F504 {lede_year}, the belt changed hands."
        body = (f"{game['new_holder']} took it from {game['holder']} {winner_score}-{other_score}, "
                f"becoming the {ordinal(reign_number)} holder in College Football Belt history.")
    elif game["outcome"] == "retained (tie)":
        h_score = team_score(game, game["holder"])
        o_score = team_score(game, game["opponent"])
        hook = f"\U0001F6E1️ {lede_year}, a tie kept the belt right where it was."
        body = f"{game['holder']} and {game['opponent']} played to a {h_score}-{o_score} draw -- no winner, so the holder kept it."
    else:  # ordinary defense
        defense_numbers = compute_defense_numbers(belt_games)
        n = defense_numbers.get(game["game_id"])
        h_score = team_score(game, game["holder"])
        o_score = team_score(game, game["opponent"])
        hook = f"\U0001F6E1️ {lede_year}, the belt survived another test."
        nth = f"defense #{n} of that reign" if n else "a successful defense"
        body = f"{game['holder']} beat {game['opponent']} {h_score}-{o_score} -- {nth}."

    lines = [hook, "", body]
    if extra_count:
        lines.append("")
        lines.append(f"That's not even the only one -- {extra_count} more belt game{'s' if extra_count != 1 else ''} "
                      f"happened on this date.")
    # No link in the main post body on purpose -- see this module's
    # docstring ("Reply-for-links, and a share image"). The link(s) go in
    # a follow-up reply instead; this just points there.
    lines.append("")
    lines.append("Full story + more \U0001F447")
    lines.append("")
    lines.append("#CFB")

    return "\n".join(lines)


def compose_otd_reply(game, extra_count):
    """The link(s) that used to live in the main tweet body, now posted
    as a reply instead -- see this module's docstring. Kept separate from
    compose_otd_tweet() so the two are easy to reason about independently."""
    game_url = f"{SITE_URL}/games/{game['game_id']}.html"
    if extra_count:
        return (f"Full game: {game_url}\n\n"
                f"Every belt game on this date across history: {SITE_URL}/on-this-day.html")
    return f"Full game: {game_url}"


def otd_cache_key(today):
    return today.isoformat()


def build_share_image(headline, tmp_dir):
    """Best-effort: a path to a generated share PNG for the headline game,
    or None on ANY failure (colors.json unreachable, a team missing from
    it, a Pillow hiccup, generate_share_image.py itself changing shape).
    Deliberately swallows everything -- a broken image generator should
    never take down the tweet itself, just fall back to text-only."""
    try:
        colors = fetch_team_colors()
    except Exception as e:
        print(f"No share image -- couldn't fetch {COLORS_API_URL}: {e}")
        return None
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import generate_share_image
        out_path = os.path.join(tmp_dir, "otd_share.png")
        generate_share_image.generate_otd_share_image(headline, colors, out_path)
        return out_path
    except Exception as e:
        print(f"No share image -- generation failed: {e}")
        return None


def upload_media(api_v1, image_path):
    """Best-effort upload of the share image via the legacy v1.1 media
    endpoint (still required for media on v2 tweets) -- returns a
    media_id, or None on any failure (falls back to a text-only post)."""
    try:
        media = api_v1.media_upload(filename=image_path)
        return media.media_id
    except Exception as e:
        print(f"No share image -- upload failed (posting text-only): {e}")
        return None


# ----------------------------------------------------------------- main

def main():
    print(f"[debug] OTD_FORCE_DATE env = {os.environ.get('OTD_FORCE_DATE')!r}")  # TEMP -- remove after diagnosing the override not taking effect
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"Skipping On This Day post -- missing env var(s): {', '.join(missing)} "
              f"(optional; the site itself doesn't depend on this).")
        return

    try:
        import tweepy
    except ImportError:
        sys.exit("tweepy isn't installed -- add it to requirements.txt "
                  "(pip install tweepy) and rerun.")

    today = eastern_today()
    force_date = os.environ.get("OTD_FORCE_DATE")
    if force_date:
        try:
            today = date.fromisoformat(force_date)
            print(f"OTD_FORCE_DATE={force_date} set -- testing against this date instead of the real "
                  f"one (only meant for a manual workflow_dispatch test run; leave unset for real days).")
        except ValueError:
            print(f"OTD_FORCE_DATE={force_date!r} isn't a valid YYYY-MM-DD date -- ignoring, using the real date.")

    cache = load_cache()
    if cache.get("last_posted_date") == otd_cache_key(today):
        print(f"Already posted (or checked) the On This Day tweet for {today} -- skipping.")
        return

    try:
        belt_games = fetch_belt_games()
    except Exception as e:
        print(f"Skipping On This Day post -- couldn't fetch {GAMES_API_URL}: {e}")
        return

    matches = find_matches(belt_games, today)
    if not matches:
        print(f"No belt games on {today.strftime('%B %-d')} in any past year -- nothing to post today.")
        cache["last_posted_date"] = otd_cache_key(today)
        save_cache(cache)
        return

    headline = pick_headline(matches, today)
    extra_count = len(matches) - 1
    text = compose_otd_tweet(headline, belt_games, today, extra_count)
    reply_text = compose_otd_reply(headline, extra_count)

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_KEY_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )

    media_id = None
    with tempfile.TemporaryDirectory() as tmp_dir:
        image_path = build_share_image(headline, tmp_dir)
        if image_path:
            auth_v1 = tweepy.OAuth1UserHandler(
                os.environ["X_API_KEY"],
                os.environ["X_API_KEY_SECRET"],
                os.environ["X_ACCESS_TOKEN"],
                os.environ["X_ACCESS_TOKEN_SECRET"],
            )
            media_id = upload_media(tweepy.API(auth_v1), image_path)

        try:
            if media_id:
                response = client.create_tweet(text=text, media_ids=[media_id])
            else:
                response = client.create_tweet(text=text)
        except Exception as e:
            print(f"On This Day post FAILED (not fatal to the pipeline): {e}")
            return

    print(f"Posted On This Day tweet for {today} (image: {'yes' if media_id else 'no'}): {response}")
    print(text)

    # The link-carrying reply -- see this module's docstring for why this
    # is a separate tweet rather than part of the one above. Best-effort:
    # if this fails, the main post already succeeded and still stands.
    tweet_id = response.data.get("id") if getattr(response, "data", None) else None
    if tweet_id:
        try:
            reply_response = client.create_tweet(text=reply_text, in_reply_to_tweet_id=tweet_id)
            print(f"Posted link reply: {reply_response}")
            print(reply_text)
        except Exception as e:
            print(f"Link reply FAILED (main post still stands): {e}")

    cache["last_posted_date"] = otd_cache_key(today)
    save_cache(cache)


if __name__ == "__main__":
    main()
