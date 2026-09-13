#!/usr/bin/env python3
"""
Run the full weekly-update pipeline in order: refetch the lineage, refresh
team colors, refresh game box scores, then rebuild the site. One command
instead of four.

Usage:
    export CFBD_API_KEY=your_key_here      # same key as every other script
    python3 update_all.py

Each stage is just the existing script, run as a subprocess with the same
API key -- nothing about how they work individually has changed, so this
is safe to point at an existing belt_data/ folder:

  1. build_lineage.py     -- incremental: resumes from the git-committed
                              historical_data/baseline.json and only
                              refetches the current + previous season
                              (~4-6 calls), not the full 1869-now history
                              (~316 calls). See its own docstring.
  2. fetch_team_colors.py -- cheap; re-derives team_colors.json from a
                              single /teams call, picking up any new
                              belt-holding team.
  3. fetch_game_details.py -- incremental the same way as build_lineage.py:
                              resumes from historical_data/team_stats.json
                              and only refetches the current + previous
                              season's belt games (~10-20 calls), not
                              every 2003+ belt game (~300+ calls).
  4. build_site.py         -- no network calls; regenerates every page
                              from whatever's now in belt_data/.

CFBD's free tier is capped at 1,000 calls/MONTH (not a short burst limit),
so steps 1 and 3 both default to the cheap incremental fetch above. Pass
--full-refetch to either script's own invocation (not exposed here) for a
genuine from-scratch rebuild when you actually need one.

Stops immediately if any stage fails (nonzero exit code), rather than
building a site from a half-updated data set. Safe to just rerun the same
command after fixing whatever failed -- every stage already knows how to
resume/skip what it's already done.
"""

import os
import subprocess
import sys

STAGES = [
    ("build_lineage.py", "Updating the lineage (current + previous season)"),
    ("fetch_team_colors.py", "Refreshing team colors"),
    ("fetch_game_details.py", "Refreshing box scores for belt games"),
    ("build_site.py", "Rebuilding the site"),
]


def main():
    api_key = os.environ.get("CFBD_API_KEY")
    here = os.path.dirname(os.path.abspath(__file__))

    for i, (script, label) in enumerate(STAGES, 1):
        script_path = os.path.join(here, script)
        if not os.path.exists(script_path):
            sys.exit(f"Can't find {script} in {here} -- run this from the "
                      f"project folder.")

        # build_site.py needs no API key; the other three do.
        if script != "build_site.py" and not api_key:
            sys.exit("No API key. Set CFBD_API_KEY first. "
                      "Free key at https://collegefootballdata.com/key")

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
