#!/usr/bin/env python3
"""
Posts automated updates to Instagram (@CollegeFBBelt), mirroring post_to_x.py's
two things -- one post for every belt game result (a change OR a defense,
worded differently) since the last run, and one preview post for the
upcoming game on the workflow's dedicated Friday run. Reuses post_to_x.py's
own helper functions and caption text directly (ordinal(), pretty_date(),
team_score(), compute_defense_numbers(), compose_result_tweet(),
find_new_result_games(), compose_preview_tweet(), preview_cache_key()) --
see that module's docstring for how the wording and dedup logic work; this
file only adds the Instagram-specific plumbing on top.

Why Instagram needs a different API shape than X
---------------------------------------------------
X's API (via tweepy) is one call: create_tweet(text=...). Instagram's Graph
API needs TWO calls per post -- create a "media container" pointing at a
publicly-hosted image (POST /{ig-user-id}/media with image_url + caption),
then publish that container (POST /{ig-user-id}/media_publish with the
container's id) -- and needs a real HTTPS image URL, not an uploaded file.
This uses site/share.png (https://collegefootballbelt.com/share.png) for
every post: it's the same image the homepage's Open Graph tag already
uses, generate_share_image.py already regenerates it every pipeline run to
match whoever currently holds the belt, and it's already publicly hosted --
so this needs no new image-generation work. It won't always depict the
SPECIFIC game just posted about (e.g. a defense doesn't change who's shown),
but it's always accurate for the belt's current state, and Instagram simply
requires an image, so this is the pragmatic choice over building a whole
second per-game graphics pipeline.

Usage:
    export IG_ACCESS_TOKEN=...          # from developers.facebook.com's
                                         # Instagram API setup, "Generate
                                         # access tokens" step
    export IG_BUSINESS_ACCOUNT_ID=...   # the numeric Instagram user id
                                         # shown on that same page
    python3 post_to_instagram.py

OPTIONAL, same pattern as post_to_x.py: if either IG_* env var isn't set,
this prints a note and exits 0 (success) rather than failing the pipeline.

social_cache/ig_last_posted.json tracks progress the same way
social_cache/x_last_posted.json does for post_to_x.py -- same
"last_posted_game_id"/"last_posted_preview_key" shape, same first-run
bootstrap (records the latest game without posting the historical
backlog), same reason it needs to be git-committed by the workflow.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from post_to_x import (  # noqa: E402
    compose_preview_tweet,
    compose_result_tweet,
    compute_defense_numbers,
    find_new_result_games,
    load_json,
    preview_cache_key,
)

HERE = os.path.dirname(os.path.abspath(__file__))
BELT_DATA_DIR = os.path.join(HERE, "belt_data")
LINEAGE_PATH = os.path.join(BELT_DATA_DIR, "lineage.json")
NEXT_GAME_PATH = os.path.join(BELT_DATA_DIR, "next_game.json")
AI_PREVIEW_PATH = os.path.join(BELT_DATA_DIR, "ai_preview.json")
CACHE_DIR = os.path.join(HERE, "social_cache")
CACHE_PATH = os.path.join(CACHE_DIR, "ig_last_posted.json")
SITE_URL = "https://collegefootballbelt.com"
SHARE_IMAGE_URL = f"{SITE_URL}/share.png"

REQUIRED_ENV = ["IG_ACCESS_TOKEN", "IG_BUSINESS_ACCOUNT_ID"]

GRAPH_API_VERSION = "v25.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

HASHTAGS = "#CollegeFootball #CFB #CollegeFootballBelt"


# ---------------------------------------------------------------- helpers

def load_cache():
    return load_json(CACHE_PATH) or {"last_posted_game_id": None, "last_posted_preview_key": None}


def save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def publish_photo(access_token, ig_user_id, image_url, caption):
    """The Graph API's two-step publish: create a media container, then
    publish it. Raises RuntimeError with the API's own error message on
    failure at either step, rather than a raw requests exception, so
    callers can log something useful."""
    import requests

    create_resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media",
        data={"image_url": image_url, "caption": caption, "access_token": access_token},
        timeout=30,
    )
    create_data = create_resp.json()
    if "id" not in create_data:
        raise RuntimeError(f"container creation failed: {create_data}")

    publish_resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": create_data["id"], "access_token": access_token},
        timeout=30,
    )
    publish_data = publish_resp.json()
    if "id" not in publish_data:
        raise RuntimeError(f"publish failed: {publish_data}")

    return publish_data


# ------------------------------------------------------------ result post

def post_results(access_token, ig_user_id, lineage, cache):
    new_games, last_id = find_new_result_games(lineage, cache)
    if cache.get("last_posted_game_id") is None:
        cache["last_posted_game_id"] = last_id
        save_cache(cache)
        if not new_games:
            print("First-ever run of post_to_instagram.py -- bootstrapping "
                  "the result marker at the current game without posting "
                  "the historical backlog. Future belt games will post "
                  "normally.")

    if not new_games:
        print("No new belt game results to post.")
        return

    defense_numbers = compute_defense_numbers(lineage["belt_games"])
    reign_starts = sorted(r["start_date"] for r in lineage["reigns"])

    for game in new_games:
        reign_number = None
        if game["outcome"] in ("changed", "established"):
            reign_number = sum(1 for d in reign_starts if d <= game["date"])

        caption = compose_result_tweet(game, reign_number, defense_numbers.get(game["game_id"]))
        caption = f"{caption}\n\n{HASHTAGS}"

        try:
            response = publish_photo(access_token, ig_user_id, SHARE_IMAGE_URL, caption)
        except Exception as e:
            print(f"Instagram post FAILED for game {game['game_id']} (not "
                  f"fatal to the pipeline): {e}")
            print("Stopping result-posting here; already-posted games stay "
                  "marked as posted, and this one will be retried next run.")
            break

        print(f"Posted result for game {game['game_id']}: {response}")
        print(caption)
        cache["last_posted_game_id"] = game["game_id"]
        save_cache(cache)
        time.sleep(2)  # be polite between consecutive posts


# ----------------------------------------------------------- preview post

def post_preview(access_token, ig_user_id, cache):
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
    caption = compose_preview_tweet(next_game, ai_preview)
    caption = f"{caption}\n\n{HASHTAGS}"

    try:
        response = publish_photo(access_token, ig_user_id, SHARE_IMAGE_URL, caption)
    except Exception as e:
        print(f"Instagram preview post FAILED (not fatal to the pipeline): {e}")
        return

    print(f"Posted preview: {response}")
    print(caption)
    cache["last_posted_preview_key"] = key
    save_cache(cache)


# ----------------------------------------------------------------- main

def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"Skipping Instagram posts -- missing env var(s): {', '.join(missing)} "
              f"(optional; the site itself doesn't depend on this).")
        return

    if not os.path.exists(LINEAGE_PATH):
        print(f"Skipping Instagram posts -- {LINEAGE_PATH} doesn't exist yet.")
        return

    try:
        import requests  # noqa: F401
    except ImportError:
        sys.exit("requests isn't installed -- add it to requirements.txt "
                  "(pip install requests) and rerun.")

    access_token = os.environ["IG_ACCESS_TOKEN"]
    ig_user_id = os.environ["IG_BUSINESS_ACCOUNT_ID"]

    lineage = load_json(LINEAGE_PATH)
    cache = load_cache()

    post_results(access_token, ig_user_id, lineage, cache)
    post_preview(access_token, ig_user_id, cache)


if __name__ == "__main__":
    main()
