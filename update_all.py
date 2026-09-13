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

  1. build_lineage.py     -- always does a full refetch (it has to, to
                              catch newly played games); this is the slow
                              step and the only one that must run in full
                              every time.
  2. fetch_team_colors.py -- cheap; re-derives team_colors.json from
                              whatever's in the (cached) teams_raw.json,
                              picking up any new belt-holding team.
  3. fetch_game_details.py -- incremental; only fetches team stats for
                              belt games it hasn't already cached, so a
                              normal week (a handful of new games) is a
                              handful of new API calls, not 300+.
  4. build_site.py         -- no network calls; regenerates every page
                              from whatever's now in belt_data/.

Stops immediately if any stage fails (nonzero exit code), rather than
building a site from a half-updated data set. Safe to just rerun the same
command after fixing whatever failed -- every stage already knows how to
resume/skip what it's already done.
"""

import os
import subprocess
import sys

STAGES = [
    ("build_lineage.py", "Refetching the full game record and lineage"),
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
