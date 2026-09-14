#!/usr/bin/env python3
"""
Run the full weekly-update pipeline in order: refetch the lineage, refresh
team colors, refresh game box scores, build the next-game preview (stats,
forecast, and an AI prediction), generate AI recaps of settled games,
rebuild the site, then render the share image. One command instead of ten.

Usage:
    export CFBD_API_KEY=your_key_here        # required -- every CFBD step needs it
    export ANTHROPIC_API_KEY=your_key_here   # optional -- only for the AI preview + recaps
    export X_API_KEY=your_x_api_key          # optional -- only to post results/previews to X
    export X_API_KEY_SECRET=your_x_secret    # optional, same as above
    export X_ACCESS_TOKEN=your_x_token       # optional, same as above
    export X_ACCESS_TOKEN_SECRET=your_x_ts   # optional, same as above
    python3 update_all.py

Each stage is just the existing script, run as a subprocess with the same
environment -- nothing about how they work individually has changed, so
this is safe to point at an existing belt_data/ folder:

  1. build_lineage.py        -- incremental: resumes from the git-committed
                                 historical_data/baseline.json and only
                                 refetches the current + previous season
                                 (~4-6 calls), not the full 1869-now history
                                 (~316 calls). Also writes next_game.json.
  2. fetch_team_colors.py    -- cheap; re-derives team_colors.json from a
                                 single /teams call, picking up any new
                                 belt-holding team.
  3. fetch_game_details.py   -- incremental the same way as build_lineage.py:
                                 resumes from historical_data/team_stats.json
                                 and only refetches the current + previous
                                 season's belt games (~10-20 calls), not
                                 every 2003+ belt game (~300+ calls).
  4. fetch_game_plays.py     -- incremental the same way, against
                                 historical_data/game_plays.json: real
                                 play-by-play (trimmed to the notable plays)
                                 for every belt game since 2003, one call
                                 each, only for games not already cached.
  5. fetch_matchup_preview.py -- 1 call for the all-time head-to-head record
                                 against the holder's next opponent; recent
                                 form for both teams is mined for free out
                                 of games_raw.json from step 1. No-ops (no
                                 call) if there's no upcoming game.
  6. fetch_weather.py        -- kickoff-hour forecast (temp, wind, precip,
                                 sky condition) for the upcoming game, from
                                 Open-Meteo -- free, no key, no signup. Venue
                                 lat/lon comes for free out of step 1's own
                                 /venues call. No-ops if there's no upcoming
                                 game, no venue coordinates, or the game's
                                 more than ~15 days out (outside Open-Meteo's
                                 free forecast window).
  7. generate_ai_preview.py  -- writes a short AI preview of the upcoming
                                 game (overview, key matchups, betting
                                 angles) plus a predicted winner/score and a
                                 write-up, via the Claude API -- folding in
                                 the forecast from step 6 when there is one.
                                 OPTIONAL: skips itself cleanly if
                                 ANTHROPIC_API_KEY isn't set, or reuses its
                                 committed cache if nothing about the
                                 upcoming game has changed since the last
                                 real generation.
  8. generate_recaps.py      -- writes an AI recap (highlighting the plays
                                 and drives that mattered) for every SETTLED
                                 belt game since 2003 that has a box score.
                                 OPTIONAL, same as generate_ai_preview.py --
                                 skips cleanly with no key, and a committed
                                 cache (recap_cache/recaps.json) means each
                                 game is only ever generated once. The FIRST
                                 time this runs with a key set, it backfills
                                 every historical belt game at once (~280+
                                 Claude calls) -- see README.md's "AI recaps"
                                 section for the one-time cost estimate.
  9. build_site.py           -- no network calls; regenerates every page
                                 from whatever's now in belt_data/.
 10. generate_share_image.py -- no network call, no API key; renders
                                 site/share.png (the Open Graph / Twitter
                                 Card image for the homepage) for whoever
                                 currently holds the belt, the site's
                                 favicon in the same colors, and a
                                 downloadable belt-history poster per team,
                                 straight from belt_data/lineage.json +
                                 team_colors.json.
 11. post_to_x.py            -- OPTIONAL, same idea as generate_ai_preview.py
                                 / generate_recaps.py: skips itself cleanly
                                 unless X_API_KEY, X_API_KEY_SECRET,
                                 X_ACCESS_TOKEN and X_ACCESS_TOKEN_SECRET are
                                 ALL set. Posts to @CollegeFBBelt on X for
                                 EVERY belt game result since the last post
                                 -- a successful defense as well as a genuine
                                 change of holder, each worded differently
                                 -- tracked by game_id in
                                 social_cache/x_last_posted.json so nothing
                                 posts twice and a first-ever run doesn't
                                 dump the whole 1869-present history. Also
                                 posts one preview of the upcoming game, but
                                 only on the workflow's dedicated Friday
                                 evening run (see update-and-deploy.yml's
                                 X_POST_PREVIEW) and only once per game.

CFBD's free tier is capped at 1,000 calls/MONTH (not a short burst limit).
Steps 1, 3 and 5 default to the cheap incremental fetch above; step 4's
one-time backfill (~280 calls) still leaves comfortable headroom under that
cap even in the same month as everything else. Pass --full-refetch to
build_lineage.py's, fetch_game_details.py's, or fetch_game_plays.py's own
invocation (not exposed here) for a genuine from-scratch rebuild when you
actually need one.

Stops immediately if a CFBD stage fails (nonzero exit code), rather than
building a site from a half-updated data set. generate_ai_preview.py,
generate_recaps.py, and fetch_weather.py are the exception -- all three are
designed to never hand back a failure for anything short of a real bug,
since AI writing and the forecast are both nice-to-haves, not something the
rest of the site depends on. Safe to just rerun the same command after
fixing whatever failed -- every stage already knows how to resume/skip
what it's already done.
"""

import os
import subprocess
import sys

# (script, human-readable label, env var it needs -- or None if it needs no key)
STAGES = [
    ("build_lineage.py", "Updating the lineage (current + previous season)", "CFBD_API_KEY"),
    ("fetch_team_colors.py", "Refreshing team colors", "CFBD_API_KEY"),
    ("fetch_game_details.py", "Refreshing box scores for belt games", "CFBD_API_KEY"),
    ("fetch_game_plays.py", "Refreshing play-by-play for belt games", "CFBD_API_KEY"),
    ("fetch_matchup_preview.py", "Building the next-game matchup stats", "CFBD_API_KEY"),
    ("fetch_weather.py", "Fetching the kickoff forecast", None),
    ("generate_ai_preview.py", "Writing the AI game preview + prediction (optional)", None),
    ("generate_recaps.py", "Writing AI recaps of settled games (optional)", None),
    ("build_site.py", "Rebuilding the site", None),
    ("generate_share_image.py", "Rendering the share image, favicon, and team posters", None),
    ("post_to_x.py", "Posting results/preview to X (optional)", None),
]


def main():
    here = os.path.dirname(os.path.abspath(__file__))

    for i, (script, label, required_key) in enumerate(STAGES, 1):
        script_path = os.path.join(here, script)
        if not os.path.exists(script_path):
            sys.exit(f"Can't find {script} in {here} -- run this from the "
                      f"project folder.")

        if required_key and not os.environ.get(required_key):
            sys.exit(f"No API key. Set {required_key} first. "
                      f"Free key at https://collegefootballdata.com/key")

        print(f"\n=== [{i}/{len(STAGES)}] {label} ({script}) ===")
        result = subprocess.run([sys.executable, script_path], cwd=here)
        if result.returncode != 0:
            sys.exit(f"\n{script} failed (exit code {result.returncode}) -- "
                      f"stopping here rather than rebuild from a half-updated "
                      f"data set. Fix the error above and rerun update_all.py; "
                      f"every stage resumes from where it left off.")

    print("\nAll done -- belt_data/ and site/ are both up to date.")


if __name__ == "__main__":
    main()
