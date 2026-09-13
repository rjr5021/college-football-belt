#!/usr/bin/env python3
"""
Run the full weekly-update pipeline in order: refetch the lineage, refresh
team colors, refresh game box scores, build the next-game preview, then
rebuild the site. One command instead of six.

Usage:
    export CFBD_API_KEY=your_key_here        # required -- every CFBD step needs it
    export ANTHROPIC_API_KEY=your_key_here   # optional -- only for the AI preview
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
  4. fetch_matchup_preview.py -- 1 call for the all-time head-to-head record
                                 against the holder's next opponent; recent
                                 form for both teams is mined for free out
                                 of games_raw.json from step 1. No-ops (no
                                 call) if there's no upcoming game.
  5. generate_ai_preview.py  -- writes a short AI preview of the upcoming
                                 game via the Claude API. OPTIONAL: skips
                                 itself cleanly if ANTHROPIC_API_KEY isn't
                                 set, or reuses its committed cache if nothing
                                 about the upcoming game has changed since
                                 the last real generation.
  6. build_site.py           -- no network calls; regenerates every page
                                 from whatever's now in belt_data/.

CFBD's free tier is capped at 1,000 calls/MONTH (not a short burst limit),
so steps 1, 3 and 4 all default to the cheap incremental fetch above. Pass
--full-refetch to build_lineage.py's or fetch_game_details.py's own
invocation (not exposed here) for a genuine from-scratch rebuild when you
actually need one.

Stops immediately if a CFBD stage fails (nonzero exit code), rather than
building a site from a half-updated data set. generate_ai_preview.py is the
one exception -- it's designed to never hand back a failure for anything
short of a real bug, since the AI preview is a nice-to-have, not something
the rest of the site depends on. Safe to just rerun the same command after
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
    ("fetch_matchup_preview.py", "Building the next-game matchup stats", "CFBD_API_KEY"),
    ("generate_ai_preview.py", "Writing the AI game preview (optional)", None),
    ("build_site.py", "Rebuilding the site", None),
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
