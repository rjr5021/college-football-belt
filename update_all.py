#!/usr/bin/env python3
"""
Run the full weekly-update pipeline in order: refetch the lineage, refresh
team colors, refresh game box scores, build the next-game preview (stats,
forecast, and an AI prediction), generate AI recaps of settled games,
rebuild the site, then render the share image. One command instead of a dozen.

Usage:
    export CFBD_API_KEY=your_key_here        # required -- every CFBD step needs it
    export ANTHROPIC_API_KEY=your_key_here   # optional -- only for the AI preview + recaps
    export X_API_KEY=your_x_api_key          # optional -- only to post results/previews to X
    export X_API_KEY_SECRET=your_x_secret    # optional, same as above
    export X_ACCESS_TOKEN=your_x_token       # optional, same as above
    export X_ACCESS_TOKEN_SECRET=your_x_ts   # optional, same as above
    export IG_ACCESS_TOKEN=your_ig_token     # optional -- same, but for Instagram
    export IG_BUSINESS_ACCOUNT_ID=your_ig_id # optional, same as above
    python3 update_all.py

Each stage is just the existing script, run as a subprocess with the same
environment -- nothing about how they work individually has changed, so
this is safe to point at an existing belt_data/ folder:

  1. build_lineage.py        -- incremental: resumes from the git-committed
                                 historical_data/baseline.json and only
                                 refetches the current + previous season
                                 (~4-6 calls), not the full 1869-now history
                                 (~316 calls). Also writes next_game.json.
                                 Also drives the championship belt's own
                                 fbs/fcs SCOPES (mirroring the Losers Belt's
                                 three-way split) -- see
                                 BOOTSTRAP_CHAMPIONSHIP_SCOPES below. Those
                                 two extra scopes no-op cleanly (no
                                 lineage_fbs.html/lineage_fcs.html on the
                                 site) until their own one-time bootstrap is
                                 explicitly requested; "combined" (the
                                 original, unsuffixed lineage.html) is
                                 completely unaffected either way.
  2. build_losers_lineage.py -- the "Losers Belt" (see its own docstring):
                                 the mirror-image lineage where the belt
                                 passes to whoever LOSES to the holder
                                 instead of whoever beats them. OPTIONAL,
                                 like generate_ai_preview.py: skips itself
                                 cleanly (no losers-belt.html on the site)
                                 until its own one-time historical bootstrap
                                 is explicitly requested -- see
                                 BOOTSTRAP_LOSERS_BELT below. Once
                                 bootstrapped, incremental the same way as
                                 build_lineage.py (~4-6 more calls/run).
  3. build_conference_lineage.py -- one lineal "championship belt" per
                                 FBS/FCS conference, using CFBD's own
                                 per-game home_conference/away_conference
                                 fields so only games where BOTH teams were
                                 actually IN that conference AT THE TIME
                                 count -- realignment (a team leaving its
                                 conference) is handled the same way the
                                 Losers Belt handles a program going dark.
                                 OPTIONAL, same pattern as
                                 build_losers_lineage.py: every conference
                                 without a baseline yet skips itself
                                 cleanly (no conferences/<slug>.html for it)
                                 until BOOTSTRAP_CONFERENCE_BELTS below is
                                 set; once a conference has a baseline it's
                                 incremental from then on. Bootstraps ALL
                                 FBS and FCS conferences together in one
                                 run, sharing a single raw games fetch
                                 (~316 CFBD calls total, same cost as one
                                 full 1869-now history fetch, regardless of
                                 how many conferences exist).
  4. fetch_team_colors.py    -- cheap; re-derives team_colors.json from a
                                 single /teams call, picking up any new
                                 belt-holding team.
  5. fetch_game_details.py   -- incremental the same way as build_lineage.py:
                                 resumes from historical_data/team_stats.json
                                 and only refetches the current + previous
                                 season's belt games (~10-20 calls), not
                                 every 2003+ belt game (~300+ calls).
  6. fetch_game_plays.py     -- incremental the same way, against
                                 historical_data/game_plays.json: real
                                 play-by-play (trimmed to the notable plays)
                                 for every belt game since 2003, one call
                                 each, only for games not already cached.
  7. fetch_matchup_preview.py -- 1 call for the all-time head-to-head record
                                 against the holder's next opponent; recent
                                 form for both teams is mined for free out
                                 of games_raw.json from step 1. No-ops (no
                                 call) if there's no upcoming game.
  8. fetch_weather.py        -- kickoff-hour forecast (temp, wind, precip,
                                 sky condition) for the upcoming game, from
                                 Open-Meteo -- free, no key, no signup. Venue
                                 lat/lon comes for free out of step 1's own
                                 /venues call. No-ops if there's no upcoming
                                 game, no venue coordinates, or the game's
                                 more than ~15 days out (outside Open-Meteo's
                                 free forecast window).
  9. generate_ai_preview.py  -- writes a short AI preview of the upcoming
                                 game (overview, key matchups, betting
                                 angles) plus a predicted winner/score and a
                                 write-up, via the Claude API -- folding in
                                 the forecast from step 8 when there is one.
                                 OPTIONAL: skips itself cleanly if
                                 ANTHROPIC_API_KEY isn't set, or reuses its
                                 committed cache if nothing about the
                                 upcoming game has changed since the last
                                 real generation.
 10. generate_recaps.py      -- writes an AI recap (highlighting the plays
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
 11. generate_historical_notes.py -- writes a short, strictly factual note
                                 (NOT a recap -- no box score to write one
                                 from) for every belt game that has no box
                                 score on file, almost all of them pre-2003.
                                 Built ONLY from facts this site already has
                                 (matchup, date, score, what it meant for the
                                 belt) -- explicitly forbidden from inventing
                                 plays, players, attendance, or anything else
                                 not given to it. OPTIONAL, same pattern as
                                 generate_recaps.py: skips cleanly with no
                                 key, and a committed cache
                                 (recap_cache/historical_notes.json) means
                                 each game is only ever generated once.
 12. build_site.py           -- no network calls; regenerates every page
                                 from whatever's now in belt_data/.
 13. generate_share_image.py -- no network call, no API key; renders
                                 site/share.png (the Open Graph / Twitter
                                 Card image for the homepage) for whoever
                                 currently holds the belt, the site's
                                 favicon in the same colors, and a
                                 downloadable belt-history poster per team,
                                 straight from belt_data/lineage.json +
                                 team_colors.json.
 14. post_to_x.py            -- OPTIONAL, same idea as generate_ai_preview.py
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
                                 Also syncs the account's bio to always name
                                 the current holder, whenever it changes.
 15. post_to_instagram.py   -- OPTIONAL, same idea again: skips itself
                                 cleanly unless IG_ACCESS_TOKEN and
                                 IG_BUSINESS_ACCOUNT_ID are BOTH set. Mirrors
                                 post_to_x.py's result/preview posts and
                                 wording (reuses its helper functions
                                 directly), tracked separately in
                                 social_cache/ig_last_posted.json since
                                 Instagram's Graph API needs its own
                                 two-step image-post flow instead of a
                                 plain create_tweet() call.

follow_batch.py is deliberately NOT a stage here -- it runs on its own much
more frequent schedule (every ~2 hours, see follow-accounts.yml) to slowly
build up who @CollegeFBBelt follows, independent of the game-driven cadence
above. See follow_batch.py's own docstring.

CFBD's free tier is capped at 1,000 calls/MONTH (not a short burst limit).
Steps 1, 3 and 5 default to the cheap incremental fetch above; step 4's
one-time backfill (~280 calls) still leaves comfortable headroom under that
cap even in the same month as everything else. Pass --full-refetch to
build_lineage.py's or fetch_game_plays.py's own invocation (not exposed
here) for a genuine from-scratch rebuild of either of those when you
actually need one.

Step 4 (fetch_game_details.py) is one exception -- it IS exposed here,
because it's a CFBD stage a non-technical site owner is actually likely to
want to trigger on purpose: set the environment variable
FULL_REFETCH_GAME_DETAILS=true (update-and-deploy.yml wires this to a
checkbox on the workflow's manual "Run workflow" button, so this never
needs a local Python install) and this stage runs with --full-refetch
instead of the normal incremental fetch, redoing every 2003+ belt game's
box score from scratch (~600 calls -- CFBD's athlete `id` field, used to
link a player's name to their own page, was only added to this site's
data after most of 2003-2024 was already cached, so those seasons won't
carry a player_id, and player names on those older games won't link,
until this runs once). Safe to run more than once; every following
scheduled/pushed run goes back to the cheap incremental fetch on its own.

Step 2 (build_losers_lineage.py) is the other exception, and a special
case even among the OPTIONAL steps: unlike generate_ai_preview.py/
generate_recaps.py/generate_historical_notes.py (which just no-op without
an API key they don't have), this one no-ops until its OWN one-time
historical bootstrap is explicitly requested, because that bootstrap costs
real CFBD budget (~316 calls) the very first time it ever runs -- see its
own docstring. Set BOOTSTRAP_LOSERS_BELT=true (also wired to its own
"Run workflow" checkbox) to run it once; after that it's incremental like
everything else and this flag does nothing.

Step 1's fbs/fcs championship-belt scopes and step 3
(build_conference_lineage.py) are two MORE exceptions of the same shape,
added alongside the Losers Belt/conference-belts feature. Each no-ops
until its own one-time bootstrap is explicitly requested, because each
costs real CFBD budget (~316 calls) the first time it runs:

  - Set BOOTSTRAP_CHAMPIONSHIP_SCOPES=true (wired to its own "Run
    workflow" checkbox) to bootstrap the championship belt's fbs/fcs
    scopes once. This only affects build_lineage.py's fbs/fcs scopes --
    the original "combined" scope (lineage.html, everything the rest of
    the site already links to) is on its own separate, already-live code
    path and is never affected by this flag either way.
  - Set BOOTSTRAP_CONFERENCE_BELTS=true (wired to its own "Run workflow"
    checkbox) to bootstrap every FBS/FCS conference belt at once (per the
    site owner's own choice: all conferences together, not staged).
    Same no-op-until-requested behavior; a no-op for any conference
    that's already bootstrapped, so re-ticking this later is also how to
    pick up any newly-added conference without redoing the rest.

Don't tick more than one of FULL_REFETCH_GAME_DETAILS,
BOOTSTRAP_LOSERS_BELT, BOOTSTRAP_CHAMPIONSHIP_SCOPES, and
BOOTSTRAP_CONFERENCE_BELTS in the same run (or even the same month)
unless you've checked your CFBD usage first -- each of the three
bootstrap flags costs ~316 calls on its own (~600 for the full box-score
refetch), and stacking them, plus whatever the schedule has already spent
that month, can easily exceed the 1,000-call/month free-tier cap. When in
doubt, run them one at a time across separate months/weeks rather than
all at once.

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

# Set by update-and-deploy.yml when the manual "Run workflow" button's
# "full refetch" checkbox is ticked -- see the module docstring above.
# Only affects the fetch_game_details.py stage; every other stage runs
# exactly as it always has.
FULL_REFETCH_GAME_DETAILS = os.environ.get("FULL_REFETCH_GAME_DETAILS", "").strip().lower() in (
    "1", "true", "yes", "on")

# Set by update-and-deploy.yml when the manual "Run workflow" button's
# "bootstrap Losers Belt" checkbox is ticked -- see the module docstring
# above and build_losers_lineage.py's own docstring. Only affects the
# build_losers_lineage.py stage; every other stage runs exactly as it
# always has. Bootstraps all three SCOPES (combined/fbs/fcs) in one go,
# still only ~316 CFBD calls total -- they share one raw games fetch, see
# build_losers_lineage.py's main(). A no-op for any scope that's already
# bootstrapped (that scope goes back to its own cheap incremental fetch
# on its own from then on, same as build_lineage.py) -- so re-ticking
# this is also how to backfill fbs/fcs the first time after this scopes
# feature ships, without redoing "combined"'s own already-settled history.
BOOTSTRAP_LOSERS_BELT = os.environ.get("BOOTSTRAP_LOSERS_BELT", "").strip().lower() in (
    "1", "true", "yes", "on")

# Set by update-and-deploy.yml when the manual "Run workflow" button's
# "bootstrap championship scopes" checkbox is ticked -- see the module
# docstring above. Only affects build_lineage.py's fbs/fcs scopes; the
# original "combined" scope (and every other stage) runs exactly as it
# always has. A no-op for any of fbs/fcs that's already bootstrapped.
BOOTSTRAP_CHAMPIONSHIP_SCOPES = os.environ.get("BOOTSTRAP_CHAMPIONSHIP_SCOPES", "").strip().lower() in (
    "1", "true", "yes", "on")

# Set by update-and-deploy.yml when the manual "Run workflow" button's
# "bootstrap conference belts" checkbox is ticked -- see the module
# docstring above and build_conference_lineage.py's own docstring. Only
# affects the build_conference_lineage.py stage; every other stage runs
# exactly as it always has. Bootstraps every FBS/FCS conference together
# in one go (the site owner's own choice over staging FBS-then-FCS),
# sharing one raw games fetch (~316 CFBD calls total, same as any other
# single full-history bootstrap regardless of how many conferences exist).
# A no-op for any conference that's already bootstrapped -- so re-ticking
# this later also backfills any newly-added conference on its own.
BOOTSTRAP_CONFERENCE_BELTS = os.environ.get("BOOTSTRAP_CONFERENCE_BELTS", "").strip().lower() in (
    "1", "true", "yes", "on")

# Set by update-and-deploy.yml when the manual "Run workflow" button's
# "bootstrap alternate universes" checkbox is ticked -- see
# build_alternate_lineages.py's docstring. That script needs EVERY game
# since 1869 (not just the belt games the baseline keeps), which it
# fetches once (~316 CFBD calls) and archives, compressed, in
# historical_data/all_games.json.gz; every run after that is free. Until
# the archive exists the stage is a no-op and universes/ never appears on
# the site.
BOOTSTRAP_ALTERNATE_UNIVERSES = os.environ.get("BOOTSTRAP_ALTERNATE_UNIVERSES", "").strip().lower() in (
    "1", "true", "yes", "on")

# (script, human-readable label, env var it needs -- or None if it needs no key)
STAGES = [
    ("build_lineage.py", "Updating the lineage (current + previous season)", "CFBD_API_KEY"),
    ("build_losers_lineage.py", "Updating the Losers Belt (optional)", "CFBD_API_KEY"),
    ("build_conference_lineage.py", "Updating the FBS/FCS conference belts (optional)", "CFBD_API_KEY"),
    ("fetch_team_colors.py", "Refreshing team colors", "CFBD_API_KEY"),
    ("fetch_coaches.py", "Refreshing head-coach history for the by-coach leaderboard (optional)", "CFBD_API_KEY"),
    ("fetch_rankings.py", "Refreshing AP/CFP poll history for the belt-vs-polls pages (optional)", "CFBD_API_KEY"),
    ("build_alternate_lineages.py", "Rerunning the belt under alternate rules (optional)", "CFBD_API_KEY"),
    ("fetch_game_details.py", "Refreshing box scores for belt games", "CFBD_API_KEY"),
    ("fetch_game_plays.py", "Refreshing play-by-play for belt games", "CFBD_API_KEY"),
    ("fetch_matchup_preview.py", "Building the next-game matchup stats", "CFBD_API_KEY"),
    ("fetch_weather.py", "Fetching the kickoff forecast", None),
    ("fetch_belt_odds.py", "Computing belt-at-risk odds (optional)", "CFBD_API_KEY"),
    ("fetch_gameday_status.py", "Checking for a live belt game today (optional)", "CFBD_API_KEY"),
    ("generate_ai_preview.py", "Writing the AI game preview + prediction (optional)", None),
    ("generate_recaps.py", "Writing AI recaps of settled games (optional)", None),
    ("generate_historical_notes.py", "Writing fact-only notes for games with no box score (optional)", None),
    ("build_site.py", "Rebuilding the site", None),
    ("seo_enhance.py", "SEO polish (viewport, canonical, titles/descriptions, sitemap lastmod)", None),
    ("generate_share_image.py", "Rendering the share image, favicon, and team posters", None),
    ("post_to_x.py", "Posting results/preview to X (optional)", None),
    ("post_to_instagram.py", "Posting results/preview to Instagram (optional)", None),
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

        cmd = [sys.executable, script_path]
        if script == "fetch_game_details.py" and FULL_REFETCH_GAME_DETAILS:
            cmd.append("--full-refetch")
            print(f"\n=== [{i}/{len(STAGES)}] {label} ({script} --full-refetch) ===")
            print("    FULL_REFETCH_GAME_DETAILS is set -- redoing every 2003+ belt game's "
                  "box score from scratch (~600 CFBD calls) instead of the usual incremental "
                  "fetch. This is a one-off; the next run goes back to normal.")
        elif script == "build_losers_lineage.py" and BOOTSTRAP_LOSERS_BELT:
            cmd.append("--full-refetch")
            print(f"\n=== [{i}/{len(STAGES)}] {label} ({script} --full-refetch) ===")
            print("    BOOTSTRAP_LOSERS_BELT is set -- doing the one-time full 1869-now "
                  "Losers Belt walk (~316 CFBD calls) instead of skipping this stage. "
                  "This is a one-off; the next run goes back to the normal incremental "
                  "update.")
        elif script == "build_lineage.py" and BOOTSTRAP_CHAMPIONSHIP_SCOPES:
            cmd.append("--bootstrap-championship-scopes")
            print(f"\n=== [{i}/{len(STAGES)}] {label} ({script} --bootstrap-championship-scopes) ===")
            print("    BOOTSTRAP_CHAMPIONSHIP_SCOPES is set -- doing the one-time full "
                  "1869-now walk for the championship belt's fbs/fcs scopes (~316 CFBD "
                  "calls) instead of skipping them. The 'combined' scope (lineage.html) "
                  "is unaffected either way. This is a one-off; the next run goes back to "
                  "the normal incremental update.")
        elif script == "build_conference_lineage.py" and BOOTSTRAP_CONFERENCE_BELTS:
            cmd.append("--full-refetch")
            print(f"\n=== [{i}/{len(STAGES)}] {label} ({script} --full-refetch) ===")
            print("    BOOTSTRAP_CONFERENCE_BELTS is set -- doing the one-time full "
                  "1869-now walk for every FBS/FCS conference belt (~316 CFBD calls "
                  "total, shared across all conferences) instead of skipping them. "
                  "This is a one-off; the next run goes back to the normal incremental "
                  "update for any conference that now has a baseline.")
        elif script == "build_alternate_lineages.py" and BOOTSTRAP_ALTERNATE_UNIVERSES:
            cmd.append("--bootstrap")
            print(f"\n=== [{i}/{len(STAGES)}] {label} ({script} --bootstrap) ===")
            print("    BOOTSTRAP_ALTERNATE_UNIVERSES is set -- doing the one-time full 1869-now "
                  "games pull (~316 CFBD calls) into historical_data/all_games.json.gz so the "
                  "alternate-rule lineages can be rerun for free on every later run. This is a "
                  "one-off; the next run goes back to normal.")
        else:
            print(f"\n=== [{i}/{len(STAGES)}] {label} ({script}) ===")
        result = subprocess.run(cmd, cwd=here)
        if result.returncode != 0:
            sys.exit(f"\n{script} failed (exit code {result.returncode}) -- "
                      f"stopping here rather than rebuild from a half-updated "
                      f"data set. Fix the error above and rerun update_all.py; "
                      f"every stage resumes from where it left off.")

    print("\nAll done -- belt_data/ and site/ are both up to date.")


if __name__ == "__main__":
    main()
