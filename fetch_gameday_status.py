#!/usr/bin/env python3
"""
Game-day mode -- wishlist item #3, 2026-09-16, Bob: "On Saturdays I want
the homepage to feel alive: the live score of the belt game, a 'SAFE / IN
DANGER' state, and the moment it flips, the recap and share image already
generated. CFBD's scoreboard endpoint polled hourly on game days only is
roughly 50 calls a month, well inside the free tier, and it's just a
different cron schedule on the workflow you already have."

That "different cron schedule on the workflow you already have" is exactly
what .github/workflows/update-and-deploy.yml does -- extra hourly
Saturday-afternoon/evening cron lines run the SAME full `python
update_all.py` pipeline (not a separate lightweight one), so the recap +
share image regeneration Bob wants "the moment it flips" is already
handled by the existing pipeline stages (generate_recaps.py,
generate_share_image.py) picking up the final score the next time they
run -- this script's only job is the NEW part: a live in-game score and a
SAFE/IN DANGER read for the homepage banner, written to
belt_data/gameday.json.

Zero API calls on any day that isn't the belt holder's own game day (the
vast majority of runs, including every hourly Saturday run in a bye week)
-- see the early-exit below. On an actual game day, CFBD's /scoreboard
endpoint (Tier 1+ Patreon feature -- Bob is on Tier 2, which includes it)
takes no team/date filter, only `classification` (fbs/fcs/ii/iii), so this
queries both fbs and fcs (the belt's only two possible classifications)
and picks out the holder's own game -- 2 calls per run, roughly 26 runs a
season (see the workflow's new cron lines), comfortably inside Tier 2's
30,000/month even stacked on everything else the pipeline already does.

Run this AFTER build_lineage.py in the pipeline -- reads
belt_data/next_game.json for who's playing whom today. Safe to run with
no current holder/upcoming game, or on any day that isn't game day:
writes belt_data/gameday.json = null and makes no API call at all.

Usage:
    export CFBD_API_KEY=your_key_here
    python3 fetch_gameday_status.py
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.collegefootballdata.com"
OUT_DIR = "belt_data"
CLASSIFICATIONS = ("fbs", "fcs")


def pick(d, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def get_json(url, api_key, retries=5):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    delay = 2
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                print(f"  429 (rate limited), waiting {delay}s "
                      f"(attempt {attempt}/{retries})...")
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise


def find_scoreboard_game(api_key, holder, opponent):
    """Scans both classifications' live scoreboards for the one game
    between `holder` and `opponent` -- /scoreboard has no team filter, so
    this is the only way to find a specific game on it. Returns the raw
    ScoreboardGame dict, or None if it isn't listed yet (common well
    before kickoff -- CFBD populates /scoreboard close to game time, not
    the whole week out)."""
    for classification in CLASSIFICATIONS:
        url = f"{API_BASE}/scoreboard?classification={classification}"
        try:
            games = get_json(url, api_key)
        except urllib.error.HTTPError as e:
            print(f"  /scoreboard?classification={classification} failed: {e}", file=sys.stderr)
            continue
        for g in games or []:
            home = pick(g.get("home_team") or {}, "name")
            away = pick(g.get("away_team") or {}, "name")
            if {home, away} == {holder, opponent}:
                return g
    return None


def normalize_status(raw_status):
    """CFBD's own status strings aren't documented field-by-field, so this
    matches loosely on keywords rather than an exact enum -- resilient to
    whichever exact wording ('in_progress' vs 'live' vs similar) the API
    actually sends."""
    s = (raw_status or "").lower()
    if "final" in s or "complet" in s:
        return "final"
    if "progress" in s or "live" in s or "half" in s:
        return "in_progress"
    return "scheduled"


def main():
    api_key = os.environ.get("CFBD_API_KEY")
    next_game_path = os.path.join(OUT_DIR, "next_game.json")
    out_path = os.path.join(OUT_DIR, "gameday.json")

    next_game = None
    if os.path.exists(next_game_path):
        with open(next_game_path) as f:
            next_game = json.load(f)

    if not next_game:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print("No upcoming game -- nothing to check for game-day mode.")
        return

    today = time.strftime("%Y-%m-%d", time.gmtime())
    if next_game["date"] != today:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print(f"Not game day (next game is {next_game['date']}, today is {today}) -- "
              f"skipping, no API call made.")
        return

    if not api_key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                  "Free key at https://collegefootballdata.com/key")

    holder = next_game["team"]
    opponent = next_game["opponent"]

    print(f"It's game day -- looking up {holder} vs {opponent} on /scoreboard "
          f"({len(CLASSIFICATIONS)} call(s))...")
    game = find_scoreboard_game(api_key, holder, opponent)

    if not game:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print("Game isn't on /scoreboard yet (too far from kickoff) -- skipping this run.")
        return

    home = game.get("home_team") or {}
    away = game.get("away_team") or {}
    home_name = pick(home, "name")
    is_home = home_name == holder
    holder_team, opponent_team = (home, away) if is_home else (away, home)
    holder_score = pick(holder_team, "points")
    opponent_score = pick(opponent_team, "points")

    status = normalize_status(game.get("status"))

    safe = None
    if status == "scheduled":
        safe = True  # nothing's happened yet
    elif holder_score is not None and opponent_score is not None:
        safe = holder_score >= opponent_score

    gameday = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "date": next_game["date"],
        "holder": holder,
        "opponent": opponent,
        "is_home": is_home,
        "status": status,
        "period": game.get("period"),
        "clock": game.get("clock"),
        "situation": game.get("situation"),
        "holder_score": holder_score,
        "opponent_score": opponent_score,
        "safe": safe,
    }
    with open(out_path, "w") as f:
        json.dump(gameday, f, indent=2)

    score_txt = (f"{holder} {holder_score}-{opponent_score} {opponent}"
                 if holder_score is not None else f"{holder} vs {opponent}")
    safe_txt = {True: "SAFE", False: "IN DANGER", None: "n/a"}[safe]
    print(f"Wrote {out_path}: {score_txt} ({status}, {safe_txt})")


if __name__ == "__main__":
    main()
