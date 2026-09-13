#!/usr/bin/env python3
"""
Build the stats half of the upcoming-game preview: each team's last few
completed games (recent form), plus the all-time head-to-head record and
recent meetings between the two teams. This is everything the AI preview
(generate_ai_preview.py) and the site's preview page need, besides the
written narrative itself.

Recent form comes for free out of belt_data/games_raw.json, which
build_lineage.py already fetched THIS RUN for every team (not just the
belt holder) across the current + previous season -- no extra API call.
The all-time head-to-head is the one new call this script makes, to
CFBD's /teams/matchup endpoint (1 call).

Usage:
    export CFBD_API_KEY=your_key_here
    python3 fetch_matchup_preview.py

Run this AFTER build_lineage.py in the same pipeline -- it reads
belt_data/next_game.json and belt_data/games_raw.json, both written by
that script. Safe to run when there's no upcoming game: writes
belt_data/matchup_preview.json = null and makes no API call at all.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.collegefootballdata.com"
OUT_DIR = "belt_data"
RECENT_FORM_COUNT = 5


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
                import time
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise


def recent_form(games_raw, team, count=RECENT_FORM_COUNT):
    """This team's last `count` COMPLETED games, most recent first."""
    played = []
    for g in games_raw:
        home = pick(g, "home_team", "homeTeam")
        away = pick(g, "away_team", "awayTeam")
        if team not in (home, away):
            continue
        hp = pick(g, "home_points", "homePoints")
        ap = pick(g, "away_points", "awayPoints")
        if hp is None or ap is None:
            continue  # not played yet
        date = pick(g, "start_date", "startDate", default="")
        is_home = home == team
        score_for = hp if is_home else ap
        score_against = ap if is_home else hp
        opponent = away if is_home else home
        played.append({
            "date": date,
            "opponent": opponent,
            "home": is_home,
            "score_for": score_for,
            "score_against": score_against,
            "won": score_for > score_against,
            "tied": score_for == score_against,
        })
    played.sort(key=lambda g: g["date"], reverse=True)
    return played[:count]


def fetch_head_to_head(team1, team2, api_key):
    url = (f"{API_BASE}/teams/matchup?team1={urllib.parse.quote(team1)}"
           f"&team2={urllib.parse.quote(team2)}")
    data = get_json(url, api_key)
    games = []
    for g in pick(data, "games", default=[]) or []:
        games.append({
            "season": pick(g, "season"),
            "week": pick(g, "week"),
            "date": pick(g, "date"),
            "season_type": pick(g, "season_type", "seasonType"),
            "neutral": bool(pick(g, "neutral_site", "neutralSite", default=False)),
            "home_team": pick(g, "home_team", "homeTeam"),
            "home_score": pick(g, "home_score", "homeScore"),
            "away_team": pick(g, "away_team", "awayTeam"),
            "away_score": pick(g, "away_score", "awayScore"),
        })
    games.sort(key=lambda g: g.get("date") or "", reverse=True)
    return {
        "team1": pick(data, "team1", default=team1),
        "team2": pick(data, "team2", default=team2),
        "team1_wins": pick(data, "team1_wins", "team1Wins", default=0) or 0,
        "team2_wins": pick(data, "team2_wins", "team2Wins", default=0) or 0,
        "ties": pick(data, "ties", default=0) or 0,
        "start_year": pick(data, "start_year", "startYear"),
        "games": games,
    }


def main():
    api_key = os.environ.get("CFBD_API_KEY")
    next_game_path = os.path.join(OUT_DIR, "next_game.json")
    out_path = os.path.join(OUT_DIR, "matchup_preview.json")

    next_game = None
    if os.path.exists(next_game_path):
        with open(next_game_path) as f:
            next_game = json.load(f)

    if not next_game:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print("No upcoming game -- nothing to build a matchup preview for.")
        return

    if not api_key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                  "Free key at https://collegefootballdata.com/key")

    games_raw_path = os.path.join(OUT_DIR, "games_raw.json")
    games_raw = []
    if os.path.exists(games_raw_path):
        with open(games_raw_path) as f:
            games_raw = json.load(f)
    else:
        print("Warning: belt_data/games_raw.json not found -- run this "
              "right after build_lineage.py in the same pipeline run. "
              "Recent form will come back empty.", file=sys.stderr)

    holder = next_game["team"]
    opponent = next_game["opponent"]

    print(f"Fetching all-time head-to-head: {holder} vs {opponent} (1 API call)...")
    head_to_head = fetch_head_to_head(holder, opponent, api_key)

    matchup = {
        "recent_form": {
            holder: recent_form(games_raw, holder),
            opponent: recent_form(games_raw, opponent),
        },
        "head_to_head": head_to_head,
    }
    with open(out_path, "w") as f:
        json.dump(matchup, f, indent=2)

    h = matchup["recent_form"][holder]
    o = matchup["recent_form"][opponent]
    print(f"Wrote {out_path}: {len(h)} recent game(s) for {holder}, "
          f"{len(o)} for {opponent}, all-time series "
          f"{head_to_head['team1_wins']}-{head_to_head['team2_wins']}"
          f"-{head_to_head['ties']} across {len(head_to_head['games'])} "
          f"meeting(s).")


if __name__ == "__main__":
    main()
