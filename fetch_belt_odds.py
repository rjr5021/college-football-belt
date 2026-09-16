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

Three API calls total, regardless of how many games are left (Elo covers
the whole season in one call; pregame WP and the betting line are one
call each for the one upcoming game).

Season outlook (2026-09-16, outlook.html): the same Elo numbers, but the
Monte Carlo walks the REST of the season for every team -- when the holder
loses, the belt moves to the winner and the trial continues down that
team's schedule -- so every program gets a probability of holding the belt
when the games run out (season.end_of_season). No extra API calls.

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
from bisect import bisect_right
from collections import Counter, defaultdict
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


LINE_PROVIDER_PREFERENCE = ("consensus", "DraftKings", "ESPN Bet", "Bovada", "teamrankings", "numberfire")


def fetch_line(year, week, season_type, team, opponent, api_key):
    """The betting line for the holder's next game from CFBD's /lines
    (one call; the consensus line when there is one, else the first
    provider that posted). None when no book has a line yet -- normal
    early in the week and for lightly traded games. Spread is stored from
    the HOLDER's side: negative means the holder is favored."""
    url = (f"{API_BASE}/lines?year={year}&week={week}&seasonType={season_type}"
           f"&team={urllib.parse.quote(team)}")
    try:
        data = get_json(url, api_key)
    except urllib.error.HTTPError:
        return None
    for row in data or []:
        home = pick(row, "home_team", "homeTeam")
        away = pick(row, "away_team", "awayTeam")
        if {home, away} != {team, opponent}:
            continue
        lines = pick(row, "lines", default=[]) or []
        if not lines:
            return None
        by_provider = {(pick(l, "provider", default="") or ""): l for l in lines}
        chosen = next((by_provider[p] for p in LINE_PROVIDER_PREFERENCE if p in by_provider), lines[0])
        spread = pick(chosen, "spread")
        if spread is None:
            return None
        try:
            spread = float(spread)
        except (TypeError, ValueError):
            return None
        holder_is_home = home == team
        holder_spread = spread if holder_is_home else -spread
        ou = pick(chosen, "over_under", "overUnder")
        return {
            "provider": pick(chosen, "provider", default=""),
            "spread": spread,                      # CFBD convention: from the home team's side
            "holder_spread": holder_spread,        # negative = holder favored
            "formatted_spread": pick(chosen, "formatted_spread", "formattedSpread", default=""),
            "over_under": float(ou) if ou is not None else None,
            "home_moneyline": pick(chosen, "home_moneyline", "homeMoneyline"),
            "away_moneyline": pick(chosen, "away_moneyline", "awayMoneyline"),
            "home_team": home, "away_team": away,
        }
    return None


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


def find_all_remaining(games_raw):
    """Every unplayed game in the fetched window, for EVERY team,
    chronological -- the season outlook below walks the belt through
    these the same way build_lineage.py walks it through played games."""
    out = []
    for g in games_raw:
        home = pick(g, "home_team", "homeTeam")
        away = pick(g, "away_team", "awayTeam")
        if not home or not away:
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
            "home": home,
            "away": away,
            "neutral": bool(pick(g, "neutral_site", "neutralSite", default=False)),
        })
    out.sort(key=lambda g: g["date"])
    return out


def monte_carlo_season(remaining_all, holder, elo, trials=MONTE_CARLO_TRIALS, rng=None):
    """The season outlook (2026-09-16): simulate the REST of the belt's
    season, not just the holder's own schedule. Each trial walks the
    unplayed games in date order the way the real lineage does -- the
    current holder plays its next game, an Elo draw decides it, a loss
    hands the belt to the winner and the walk continues down THAT team's
    schedule -- until the games run out. Returns (end-of-season holder
    counts, trials actually run). "Who's most likely to hold the belt on
    New Year's Day?" is this Counter divided by trials; note it can give
    the current holder a way back (lose it in October, win it back in
    November), which the simpler wins-every-game number can't."""
    rng = rng or random.Random(RNG_SEED)
    by_team = defaultdict(list)
    for i, g in enumerate(remaining_all):
        by_team[g["home"]].append(i)
        by_team[g["away"]].append(i)
    end_counts = Counter()
    for _ in range(trials):
        cur, pos = holder, -1
        while True:
            lst = by_team.get(cur)
            if not lst:
                break
            j = bisect_right(lst, pos)
            if j >= len(lst):
                break
            gi = lst[j]
            g = remaining_all[gi]
            pos = gi
            if rng.random() >= holder_win_prob(g, cur, elo):
                cur = g["away"] if g["home"] == cur else g["home"]
        end_counts[cur] += 1
    return end_counts, trials


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

    print(f"Fetching the betting line for {holder}'s next game (1 API call)...")
    line = fetch_line(next_game["season"], next_game["week"], next_game["season_type"],
                      holder, next_game["opponent"], api_key)
    if line:
        print(f"  {line['provider']}: {line['formatted_spread'] or line['spread']}"
              + (f", O/U {line['over_under']}" if line.get("over_under") is not None else ""))
    else:
        print("  no line posted yet")

    if pregame_wp is not None:
        defend_prob, defend_source = pregame_wp, "cfbd_pregame_wp"
    elif remaining:
        defend_prob, defend_source = holder_win_prob(remaining[0], holder, elo), "elo_estimate"
    else:
        defend_prob, defend_source = None, None

    wins_out_prob = monte_carlo_survive(remaining, holder, elo)

    # Season outlook: walk the whole rest of the season (every team's
    # unplayed games), so every program gets an end-of-season probability.
    remaining_all = find_all_remaining(games_raw)
    end_counts, trials_run = monte_carlo_season(remaining_all, holder, elo)
    outlook = [{"team": t, "prob": round(n / trials_run, 4)}
               for t, n in end_counts.most_common() if n / trials_run >= 0.0005]
    holds_prob = end_counts[holder] / trials_run

    risk = {
        "generated": time.strftime("%Y-%m-%d"),
        "holder": holder,
        "next_game": {
            "opponent": next_game["opponent"],
            "date": next_game["date"],
            "defend_prob": round(defend_prob, 4) if defend_prob is not None else None,
            "source": defend_source,
            # the sportsbook line (fetch_line), None until a book posts one
            "line": line,
        },
        "season": {
            # ends the season holding the belt (a lost-and-regained belt
            # counts, so this is >= wins_out_prob)
            "holds_into_offseason_prob": round(holds_prob, 4),
            # wins every remaining game outright (the original, stricter number)
            "wins_out_prob": round(wins_out_prob, 4),
            "games_modeled": len(remaining),
            "season_games_modeled": len(remaining_all),
            "trials": MONTE_CARLO_TRIALS,
            "source": "elo_monte_carlo",
            "end_of_season": outlook,
            "teams_with_a_chance": len(end_counts),
        },
    }
    with open(out_path, "w") as f:
        json.dump(risk, f, indent=2)

    defend_pct = f"{defend_prob * 100:.0f}%" if defend_prob is not None else "n/a"
    print(f"Wrote {out_path}: {holder} {defend_pct} to defend vs {next_game['opponent']} "
          f"({defend_source}), {holds_prob * 100:.0f}% to hold the belt into the offseason "
          f"(wins out: {wins_out_prob * 100:.0f}%) across {len(remaining)} remaining game(s); "
          f"season outlook walked {len(remaining_all)} unplayed games, {len(end_counts)} programs "
          f"finish with the belt in at least one of {trials_run} trials")


if __name__ == "__main__":
    main()
