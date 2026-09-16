#!/usr/bin/env python3
"""
Belt-at-risk odds -- wishlist item #2, 2026-09-16, Bob: "CFBD publishes
pregame win probabilities, so the Up Next card could say 'Notre Dame has
an 82% chance of defending Saturday,' and a tiny Monte Carlo over the
holder's remaining schedule gives '31% chance the Irish hold the belt
into the offseason.' One API call a week, and it turns a schedule into a
story. It also feeds the weekly X/IG post automatically."

Two numbers, from two different CFBD data sources, because they need
different kinds of prediction:

  - next_game.defend_prob: CFBD's own pregame win probability
    (/metrics/wp/pregame) for the holder's immediate next game --
    Vegas-line-derived, so it's only populated once betting lines exist
    for that specific game (typically days out, never the whole season
    in advance). Used as-is when CFBD has it.
  - season.holds_into_offseason_prob: a season-long Monte Carlo needs a
    win probability for EVERY remaining game, including ones many weeks
    out with no betting line posted yet -- pregame WP can't cover that,
    so this falls back to CFBD's current-season Elo ratings
    (/ratings/elo, one call, covers every team all season) run through
    the standard Elo win-probability formula with a modest home-field
    bump. The holder "holds into the offseason" in a given trial iff it
    wins EVERY one of its remaining games in that trial -- losing any one
    of them hands the belt to whoever won it, per the site's own
    winner-take-all rule (no partial credit for "still good").
  - next_game.defend_prob ALSO falls back to the same Elo estimate when
    CFBD's pregame WP isn't published yet for that game (common early in
    the week), so the Up Next card is never just blank.

Two API calls total, regardless of how many games are left (Elo covers
the whole season in one call; pregame WP is one call for one game).

Run this AFTER build_lineage.py in the pipeline -- reads
belt_data/next_game.json and belt_data/games_raw.json, both written by
that script. Safe to run when there's no current holder/upcoming game:
writes belt_data/belt_risk.json = null and makes no API call at all.

Usage:
    export CFBD_API_KEY=your_key_here
    python3 fetch_belt_odds.py
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.collegefootballdata.com"
OUT_DIR = "belt_data"
MONTE_CARLO_TRIALS = 20000
HOME_FIELD_ELO = 65  # standard-ish home-field Elo bump (~2-3 points on a 65-pt scale)
RNG_SEED = None      # None = real randomness; tests can pass a fixed seed


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


def elo_win_prob(elo_a, elo_b):
    """Standard Elo win-probability formula: the chance A beats B given
    both ratings (already including any home-field adjustment the caller
    wants baked in)."""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def fetch_elo(year, api_key):
    """Every current-season Elo rating CFBD has, {team: elo} -- covers
    every FBS/FCS team all season, so it's the one number available for
    games many weeks out that pregame win probability can't reach yet."""
    data = get_json(f"{API_BASE}/ratings/elo?year={year}", api_key)
    out = {}
    for row in data or []:
        team = pick(row, "team")
        elo = pick(row, "elo")
        if team and elo is not None:
            out[team] = elo
    return out


def fetch_pregame_wp(year, week, season_type, team, api_key):
    """CFBD's own pregame win probability for one specific upcoming game,
    or None if it isn't published yet (too far from kickoff for a line to
    exist) -- treated as absent, not an error, same as every other
    optional data source in this pipeline."""
    url = (f"{API_BASE}/metrics/wp/pregame?year={year}&week={week}"
           f"&seasonType={season_type}&team={urllib.parse.quote(team)}")
    try:
        data = get_json(url, api_key)
    except urllib.error.HTTPError:
        return None
    if not data:
        return None
    row = data[0]
    home_wp = pick(row, "home_win_prob", "homeWinProb")
    home_team = pick(row, "home_team", "homeTeam")
    if home_wp is None or not home_team:
        return None
    is_home = home_team == team
    return home_wp if is_home else (1.0 - home_wp)


def find_remaining(games_raw, team):
    """Every unplayed game left on `team`'s schedule in this run's fetched
    window, chronological -- the same "games CFBD has already scheduled
    but not yet scored" trick build_lineage.py's find_upcoming_games uses
    for Belt Watch, kept self-contained here (no venue-timezone lookup
    needed -- this only cares about date ORDER and who's playing whom,
    not the exact local kickoff hour)."""
    out = []
    for g in games_raw:
        home = pick(g, "home_team", "homeTeam")
        away = pick(g, "away_team", "awayTeam")
        if team not in (home, away):
            continue
        hp = pick(g, "home_points", "homePoints")
        ap = pick(g, "away_points", "awayPoints")
        if hp is not None and ap is not None:
            continue  # already played
        gdate = pick(g, "start_date", "startDate")
        if not gdate:
            continue
        out.append({
            "date": gdate,
            "season": pick(g, "season"),
            "week": pick(g, "week"),
            "season_type": pick(g, "season_type", "seasonType", default="regular"),
            "home": home,
            "away": away,
            "neutral": bool(pick(g, "neutral_site", "neutralSite", default=False)),
        })
    out.sort(key=lambda g: g["date"])
    return out


def holder_win_prob(game, holder, elo):
    """P(holder wins this game) via Elo, with a home-field bump for
    whichever side is actually at home (none on a neutral field). Missing
    Elo data for either side (a small FCS opponent CFBD hasn't rated,
    typically) falls back to a neutral 50/50 for that one game rather
    than guessing or crashing -- rare, and this is a "for fun" estimate
    either way, not a betting product."""
    is_home = game["home"] == holder
    opponent = game["away"] if is_home else game["home"]
    elo_holder = elo.get(holder)
    elo_opp = elo.get(opponent)
    if elo_holder is None or elo_opp is None:
        return 0.5
    bump = 0 if game["neutral"] else HOME_FIELD_ELO
    elo_holder_adj = elo_holder + (bump if is_home else 0)
    elo_opp_adj = elo_opp + (bump if not is_home else 0)
    return elo_win_prob(elo_holder_adj, elo_opp_adj)


def monte_carlo_survive(remaining, holder, elo, trials=MONTE_CARLO_TRIALS, rng=None):
    """Fraction of `trials` simulated seasons where the holder wins EVERY
    remaining game -- i.e. still holds the belt once the games run out.
    Independent Bernoulli draws per game (this is deliberately "a tiny
    Monte Carlo," not a full season simulator with correlated upsets or
    strength-of-schedule drift) -- exactly what Bob asked for."""
    rng = rng or random.Random(RNG_SEED)
    if not remaining:
        return 1.0
    probs = [holder_win_prob(g, holder, elo) for g in remaining]
    survived = 0
    for _ in range(trials):
        if all(rng.random() < p for p in probs):
            survived += 1
    return survived / trials


def main():
    api_key = os.environ.get("CFBD_API_KEY")
    next_game_path = os.path.join(OUT_DIR, "next_game.json")
    games_raw_path = os.path.join(OUT_DIR, "games_raw.json")
    out_path = os.path.join(OUT_DIR, "belt_risk.json")

    next_game = None
    if os.path.exists(next_game_path):
        with open(next_game_path) as f:
            next_game = json.load(f)

    if not next_game:
        with open(out_path, "w") as f:
            json.dump(None, f)
        print("No upcoming game -- nothing to compute belt-at-risk odds for.")
        return

    if not api_key:
        sys.exit("No API key. Set CFBD_API_KEY or pass --key. "
                  "Free key at https://collegefootballdata.com/key")

    if not os.path.exists(games_raw_path):
        with open(out_path, "w") as f:
            json.dump(None, f)
        print("Warning: belt_data/games_raw.json not found -- run this right "
              "after build_lineage.py in the same pipeline run. Skipping "
              "belt-at-risk odds this run.", file=sys.stderr)
        return
    with open(games_raw_path) as f:
        games_raw = json.load(f)

    holder = next_game["team"]
    year = next_game["season"]
    remaining = find_remaining(games_raw, holder)

    print(f"Fetching {year} Elo ratings (1 API call)...")
    elo = fetch_elo(year, api_key)

    print(f"Fetching pregame win probability for {holder}'s next game (1 API call)...")
    pregame_wp = fetch_pregame_wp(next_game["season"], next_game["week"],
                                   next_game["season_type"], holder, api_key)

    if pregame_wp is not None:
        defend_prob, defend_source = pregame_wp, "cfbd_pregame_wp"
    elif remaining:
        defend_prob, defend_source = holder_win_prob(remaining[0], holder, elo), "elo_estimate"
    else:
        defend_prob, defend_source = None, None

    holds_prob = monte_carlo_survive(remaining, holder, elo)

    risk = {
        "generated": time.strftime("%Y-%m-%d"),
        "holder": holder,
        "next_game": {
            "opponent": next_game["opponent"],
            "date": next_game["date"],
            "defend_prob": round(defend_prob, 4) if defend_prob is not None else None,
            "source": defend_source,
        },
        "season": {
            "holds_into_offseason_prob": round(holds_prob, 4),
            "games_modeled": len(remaining),
            "trials": MONTE_CARLO_TRIALS,
            "source": "elo_monte_carlo",
        },
    }
    with open(out_path, "w") as f:
        json.dump(risk, f, indent=2)

    defend_pct = f"{defend_prob * 100:.0f}%" if defend_prob is not None else "n/a"
    print(f"Wrote {out_path}: {holder} {defend_pct} to defend vs {next_game['opponent']} "
          f"({defend_source}), {holds_prob * 100:.0f}% to hold the belt into the offseason "
          f"across {len(remaining)} remaining game(s) ({MONTE_CARLO_TRIALS} trials)")


if __name__ == "__main__":
    main()
