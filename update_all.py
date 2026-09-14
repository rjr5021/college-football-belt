#!/usr/bin/env python3
"""
Run the full weekly-update pipeline in order: refetch the lineage, refresh
team colors, refresh game box scores, build the next-game preview, generate
AI recaps of settled games, rebuild the site, then render the share image.
One command instead of nine.

Usage:
    export CFBD_API_KEY=your_key_here        # required -- every CFBD step needs it
    export ANTHROPIC_API_KEY=your_key_here   # optional -- only for the AI preview + recaps
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
  6. generate_ai_preview.py  -- writes a short AI preview of the upcoming
                                 game via the Claude API. OPTIONAL: skips
                                 itself cleanly if ANTHROPIC_API_KEY isn't
                                 set, or reuses its committed cache if nothing
                                 about the upcoming game has changed since
                                 the last real generation.
  7. generate_recaps.py      -- writes an AI recap (highlighting the plays
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
  8. build_site.py           -- no network calls; regenerates every page
                                 from whatever's now in belt_data/.
  9. generate_share_image.py -- no network call, no API key; renders
                                 site/share.png (the Open Graph / Twitter
                                 Card image for the homepage) for whoever
                                 currently holds the belt, straight from
                                 belt_data/lineage.json + team_colors.json.

CFBD's free tier is capped at 1,000 calls/MONTH (not a short burst limit).
Steps 1, 3 and 5 default to the cheap incremental fetch above; step 4's
one-time backfill (~280 calls) still leaves comfortable headroom under that
cap even in the same month as everything else. Pass --full-refetch to
build_lineage.py's, fetch_game_details.py's, or fetch_game_plays.py's own
invocation (not exposed here) for a genuine from-scratch rebuild when you
actually need one.

Stops immediately if a CFBD stage fails (nonzero exit code), rather than
building a site from a half-updated data set. generate_ai_preview.py and
generate_recaps.py are the exception -- both are designed to never hand
back a failure for anything short of a real bug, since the AI writing is a
nice-to-have, not something the rest of the site depends on. Safe to just
rerun the same command after fixing whatever failed -- every stage already
knows how to resume/skip what it's already done.
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
    ("generate_ai_preview.py", "Writing the AI game preview (optional)", None),
    ("generate_recaps.py", "Writing AI recaps of settled games (optional)", None),
    ("build_site.py", "Rebuilding the site", None),
    ("generate_share_image.py", "Rendering the social share image", None),
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
