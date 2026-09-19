#!/usr/bin/env python3
"""
Shared winner-take belt engine: gap-aware chain walking + vacancy
resolution, factored out so more than one winner-take belt can reuse it
without re-deriving the same (once-hard-won) logic.

This is the winner-take counterpart to build_losers_lineage.py's own
walk_losers()/resolve_vacancies() -- same algorithm, same gap-detection
and disrupted-era handling, same vacancy bookkeeping -- just with "the
WINNER of each game becomes the new holder" instead of "the LOSER does."
build_losers_lineage.py is intentionally left as its own fully
self-contained file rather than retrofitted onto this module: it's
already live/bootstrapped, and there's no benefit to the churn-and-regression
risk of splicing already-proven code apart. This module exists for the
belts that need the SAME vacancy-resolution machinery but the NORMAL
"beat the holder, catch the belt" rule:

  - build_lineage.py's own Combined/FBS-only/FCS-only championship-belt
    scopes (an FCS-only scope can hit the exact same "program went dark
    for years" problem that originally motivated all of this for the
    Losers Belt -- small programs are small programs, whichever belt
    they're carrying).
  - build_conference_lineage.py's per-conference belts, where a holder
    leaving its conference (realignment) looks EXACTLY like "team never
    shows up in the filtered game list again" to this engine -- no
    special-casing needed, just pass `recent_teams` = the conference's
    CURRENT roster instead of "teams active in the current+previous
    season," and a departed team is correctly treated as vacated.

See resolve_vacancies()'s docstring for the full mechanics (gap
detection vs. terminal dormancy, the forgive-one-gap-at-a-time loop, and
why an earlier, simpler version of this shipped broken twice for the
Losers Belt before landing on this design).
"""

import json
import os
import sys
import time
from datetime import date, timedelta

FIRST_GAME_DATE = "1869-11-06"

# Same threshold as the Losers Belt's own GAP_THRESHOLD_DAYS (500 days,
# ~1.4 seasons) -- see build_losers_lineage.py for the full tuning
# rationale. A normal single-season-to-next-season gap is ~270-290 days,
# comfortably under this; a program that's truly stopped appearing in a
# belt's filtered game list (gone dark, OR -- for a conference belt --
# realigned into a different conference) blows well past it and keeps
# growing, so there's no meaningful false-positive risk from reusing the
# same number here.
GAP_THRESHOLD_DAYS = 500

DISRUPTION_WINDOWS = [
    # WWII: many programs suspended football for one or more full
    # seasons (1943 especially) as rosters emptied out to enlistment;
    # normal nationwide play had resumed by 1946.
    ("1941-09-01", "1946-09-01"),
    # COVID-19: the 2020 season was postponed, shortened, played
    # conference-only, or (many FCS programs) moved wholesale to spring
    # 2021. Normal fall play was back across Division I by 2021.
    ("2019-12-01", "2021-09-01"),
]
DISRUPTION_GAP_THRESHOLD_DAYS = 1500


def _disrupted_era_overlap(start_date, end_date):
    """True if [start_date, end_date] overlaps any DISRUPTION_WINDOWS entry."""
    s, e = date.fromisoformat(start_date), date.fromisoformat(end_date)
    return any(s <= date.fromisoformat(w_end) and e >= date.fromisoformat(w_start)
               for w_start, w_end in DISRUPTION_WINDOWS)


def walk_winner(games, tie_rule="holder", start_holder=None, start_reign=None,
                 gap_threshold_days=None, gaps_to_forgive=0,
                 first_game_date=FIRST_GAME_DATE):
    """Walk `games` in chronological order under the NORMAL belt rule: the
    WINNER of each game the holder plays becomes (or stays) the holder.
    Same shape, calling convention, gap-detection semantics and outcome
    vocabulary as build_losers_lineage.py's walk_losers() -- see that
    function's docstring for the full explanation of `start_holder`/
    `start_reign` (resuming), `gap_threshold_days` (stop-early-on-a-
    too-long-silence), and `gaps_to_forgive` (forgive this many
    over-threshold gaps before actually stopping on one).
    The only difference is who "wins" a belt game.
    """
    games = [g for g in games if g["date"] >= first_game_date]

    if start_holder is None:
        if not games:
            sys.exit("No games at or after the first-game date -- check the data pull.")
        first = games[0]
        if first["date"] != first_game_date:
            print(f"WARNING: first game is {first['date']}, expected {first_game_date}",
                  file=sys.stderr)
        holder = (first["home"] if first["home_points"] > first["away_points"]
                  else first["away"])
        reign = {"team": holder, "start_date": first["date"], "won_from": None,
                 "won_score": f"{first['home_points']}-{first['away_points']}",
                 "defenses": 0, "last_game_date": first["date"]}
        remaining = games[1:]
        bootstrap_belt_game = {
            "date": first["date"], "season": first["season"], "week": first["week"],
            "season_type": first["season_type"], "holder": None,
            "opponent": (first["away"] if holder == first["home"] else first["home"]),
            "home": first["home"], "away": first["away"],
            "score": f"{first['home_points']}-{first['away_points']}",
            "neutral": first["neutral"], "outcome": "established",
            "new_holder": holder, "game_id": first["id"],
        }
    else:
        holder = start_holder
        reign = dict(start_reign)
        reign.setdefault("last_game_date", reign["start_date"])
        remaining = [g for g in games if g["date"] >= start_reign["start_date"]]
        bootstrap_belt_game = None

    belt_games, reigns = ([bootstrap_belt_game] if bootstrap_belt_game else []), []

    for g in remaining:
        if holder not in (g["home"], g["away"]):
            continue

        if gap_threshold_days is not None:
            gap = (date.fromisoformat(g["date"])
                   - date.fromisoformat(reign["last_game_date"])).days
            effective_threshold = gap_threshold_days
            if _disrupted_era_overlap(reign["last_game_date"], g["date"]):
                effective_threshold = max(effective_threshold, DISRUPTION_GAP_THRESHOLD_DAYS)
            if gap > effective_threshold:
                if gaps_to_forgive > 0:
                    gaps_to_forgive -= 1
                else:
                    break

        hp, ap = g["home_points"], g["away_points"]
        if hp == ap:
            outcome = "retained (tie)" if tie_rule == "holder" else "lost (tie)"
            changed = tie_rule != "holder"
            new_holder = (g["away"] if holder == g["home"] else g["home"]) if changed else holder
        else:
            winner = g["home"] if hp > ap else g["away"]
            new_holder = winner
            changed = new_holder != holder
            outcome = "changed" if changed else "retained"

        opponent = g["away"] if holder == g["home"] else g["home"]
        belt_games.append({
            "date": g["date"], "season": g["season"], "week": g["week"],
            "season_type": g["season_type"], "holder": holder,
            "opponent": opponent, "home": g["home"], "away": g["away"],
            "score": f"{hp}-{ap}", "neutral": g["neutral"],
            "outcome": outcome, "new_holder": new_holder, "game_id": g["id"],
        })

        if changed:
            reign["end_date"] = g["date"]
            reign["lost_to"] = new_holder
            reigns.append(reign)
            reign = {"team": new_holder, "start_date": g["date"], "won_from": holder,
                     "won_score": f"{hp}-{ap}", "defenses": 0, "last_game_date": g["date"]}
            holder = new_holder
        else:
            reign["defenses"] += 1
            reign["last_game_date"] = g["date"]

    reign["end_date"] = None
    reign["lost_to"] = None
    reigns.append(reign)
    return belt_games, reigns


def _predecessor(reign):
    """Whoever a reign's team caught the belt from -- `won_from` for a
    normal reign, or `predecessor` for one reopened by a vacancy."""
    return reign.get("predecessor") or reign.get("won_from")


def _next_game_date(team, games_after):
    for g in games_after:
        if team in (g["home"], g["away"]):
            return g["date"]
    return None


def _successor(effective_date, games, exclude, recent_teams, chronology, gap_threshold_days):
    """Who inherits a vacated belt: the most recent earlier holder (newest
    first through the real chronology) that is actually going to play --
    a qualifying game within one gap threshold of the vacancy -- so the
    walk that follows makes real progress instead of reverting again the
    next day. At the end of the fetched data (nobody has a later game yet)
    the heir must instead be a team that has played recently
    (`recent_teams`), which is what keeps a live belt with a live holder
    and lets a dissolved conference's belt retire (its roster is empty).
    Returns a team name, or None when nobody qualifies."""
    games_after = [g for g in games if g["date"] >= effective_date]
    end_of_data = not games_after
    tried = set()
    for r in reversed(list(chronology)):
        t = r["team"]
        if t in exclude or t in tried:
            continue
        tried.add(t)
        if end_of_data:
            if t in recent_teams:
                return t
            continue
        nxt = _next_game_date(t, games_after)
        if nxt is None:
            continue
        window = gap_threshold_days
        if _disrupted_era_overlap(effective_date, nxt):
            window = max(window, DISRUPTION_GAP_THRESHOLD_DAYS)
        if (date.fromisoformat(nxt) - date.fromisoformat(effective_date)).days <= window:
            return t
    return None


def resolve_vacancies(games, tie_rule, start_holder, start_reign, recent_teams,
                       today, context_reigns=(), gap_threshold_days=GAP_THRESHOLD_DAYS,
                       first_game_date=None, reestablish=True):
    """Walk a filtered game list under the winner-take rule and resolve
    every point where the holder stops appearing in it -- a program that
    went dark, a team that left the conference this belt is restricted to,
    a program that moved between subdivisions -- without ever inventing a
    holder that isn't going to play.

    Rewritten 2026-09-19 after the previous version (a port of the Losers
    Belt's logic) was found producing runaway day-by-day reversions on the
    live site: when a vacated belt reverted to a predecessor with no later
    game, that predecessor was vacated the next day, and so on around the
    same few teams -- 124 zero-day "reigns" on the FCS belt, 1,209 bogus
    vacancies on the WAC belt, a defunct Big 8 belt "held since 1996" --
    and every pipeline run added more, because the cycle guard only lived
    for one run. A reader, Elliot, reported the symptoms.

    The rule now:

      1. Walk until the holder has no further qualifying game (or a gap
         longer than `gap_threshold_days` before its next one).
      2. If the holder is simply the current holder at the end of the data
         and still an active member of this belt's world (`recent_teams`),
         stop: that's the reign in progress.
      3. Otherwise the reign is vacated as of its last real game and the
         belt reverts to the most recent EARLIER holder that will actually
         play again within one threshold (see _successor). That heir's
         very next game is guaranteed to be walked, so a vacancy always
         moves the story forward by at least one real game.
      4. If no earlier holder qualifies: a holder that merely went quiet
         (it has a later game) carries the belt through the silence, one
         forgiven gap at a time; a holder with no later game at all
         RETIRES the belt -- its reign closes on its last game, flagged
         `retired`, and nothing synthetic follows. If qualifying games
         resume later (a conference that dropped football and came back),
         a fresh lineage is established with the next game, flagged
         `reestablished`.

    `first_game_date` is the belt's own origin (its first qualifying
    game) -- the conference belts and the FBS/FCS belts do not start on
    1869-11-06. Returns (belt_games, reigns, vacancies); a retirement is
    recorded as a vacancy with `reverted_to` None and `retired` True.
    """
    games = sorted(games, key=lambda g: (g["date"], g.get("season_type") != "regular", g.get("id", 0)))
    if first_game_date is None:
        first_game_date = start_reign["start_date"] if start_reign else (games[0]["date"] if games else FIRST_GAME_DATE)
    all_belt_games, all_reigns, vacancies = [], [], []
    holder, reign = start_holder, start_reign
    gaps_to_forgive = 0
    chain_teams = set()
    reestablished_pending = False
    iterations = 0

    while True:
        iterations += 1
        if iterations > 100000:
            raise RuntimeError("resolve_vacancies: no progress after 100000 passes -- this is a bug")
        if not games:
            return all_belt_games, all_reigns, vacancies
        belt_games, reigns = walk_winner(games, tie_rule, start_holder=holder, start_reign=reign,
                                          gap_threshold_days=gap_threshold_days,
                                          gaps_to_forgive=gaps_to_forgive,
                                          first_game_date=first_game_date)
        if reestablished_pending and reigns and holder is None:
            # the first reign of a re-established segment; this pass may be
            # re-run (gaps_to_forgive) before the segment is committed, so the
            # flag is re-applied each pass and only cleared once it is kept
            reigns[0]["reestablished"] = True
        tip = reigns[-1]
        if tip.get("defenses", 0) > 0 or len(reigns) > 1 or belt_games:
            chain_teams = set()          # real forward progress happened in this pass
        last_activity = tip.get("last_game_date", tip["start_date"])
        has_gap = any(g["date"] > last_activity and tip["team"] in (g["home"], g["away"]) for g in games)
        dormant = tip["team"] not in recent_teams

        if not has_gap and not dormant:
            all_belt_games += belt_games
            all_reigns += reigns
            return all_belt_games, all_reigns, vacancies

        effective_date = (date.fromisoformat(last_activity) + timedelta(days=1)).isoformat()
        chronology = list(context_reigns) + all_reigns + reigns[:-1]
        successor = _successor(effective_date, games, chain_teams | {tip["team"]}, recent_teams,
                               chronology, gap_threshold_days)

        if successor is None:
            if has_gap:
                gaps_to_forgive += 1     # nobody to hand it to: the holder carries it through the silence
                continue
            # end of the line for this belt: retire it with its last real holder
            v = {"team": tip["team"], "reign_started": tip["start_date"],
                 "last_activity_date": last_activity, "effective_date": effective_date,
                 "detected_on": today, "reverted_to": None, "retired": True}
            vacancies.append(v)
            retired_tip = {**tip, "end_date": last_activity, "lost_to": None, "retired": True}
            all_belt_games += belt_games
            all_reigns += reigns[:-1] + [retired_tip]
            reestablished_pending = False
            later = [g for g in games if g["date"] > last_activity]
            if later and reestablish:
                print(f"Belt: {tip['team']} has no qualifying game after {last_activity} and nobody "
                      f"can inherit it -- retiring the belt there; a new lineage starts with the "
                      f"next qualifying game on {later[0]['date']}.")
                games = later
                holder, reign = None, None
                first_game_date = later[0]["date"]
                gaps_to_forgive = 0
                chain_teams = set()
                reestablished_pending = True
                continue
            print(f"Belt: {tip['team']} has no qualifying game after {last_activity} and nobody "
                  f"can inherit it -- the belt retires there.")
            return all_belt_games, all_reigns, vacancies

        v = {"team": tip["team"], "reign_started": tip["start_date"],
             "last_activity_date": last_activity, "effective_date": effective_date,
             "detected_on": today, "reverted_to": successor}
        held = (f"caught it on {v['reign_started']}" if last_activity == v['reign_started']
                else f"caught it on {v['reign_started']} and defended it through {last_activity}")
        if has_gap:
            reason = ("then went quiet for well over a season before showing up again "
                      "later in the data")
        else:
            reason = "and has had no qualifying game since"
        print(f"Belt: {v['team']} {held}, {reason} -- closing that reign as of their last real "
              f"activity and reverting the belt to {v['reverted_to']}, in effect since {v['effective_date']}.")
        vacated_tip = {**tip, "end_date": last_activity, "lost_to": None, "vacated": True}
        all_belt_games += belt_games
        all_reigns += reigns[:-1] + [vacated_tip]
        reestablished_pending = False
        vacancies.append(v)
        chain_teams.add(tip["team"])

        inherited = None
        for r in reversed(chronology):
            if r["team"] == successor:
                inherited = _predecessor(r)
                break
        holder = successor
        reign = {"team": successor, "start_date": effective_date,
                 "won_from": None, "won_score": None, "defenses": 0,
                 "reclaimed_after": tip["team"], "predecessor": inherited}
        gaps_to_forgive = 0


def merge_vacancies(all_vacancies, new_vacancies):
    """Upsert `new_vacancies` into `all_vacancies` by (team, reign_started),
    overwriting when last_activity_date/effective_date/reverted_to differ.
    Returns (merged_list, changed). Identical logic to
    build_losers_lineage.py's own merge_vacancies -- kept as a plain,
    stateless function so both can use it without importing from each
    other."""
    by_key = {(v["team"], v["reign_started"]): v for v in all_vacancies}
    changed = False
    for v in new_vacancies:
        key = (v["team"], v["reign_started"])
        existing = by_key.get(key)
        if existing is None or (
            existing.get("last_activity_date") != v.get("last_activity_date")
            or existing.get("effective_date") != v.get("effective_date")
            or existing.get("reverted_to") != v.get("reverted_to")
        ):
            by_key[key] = v
            changed = True
    return list(by_key.values()), changed


def split_winner_baseline(belt_games, all_vacancies, first_reign, live_start_year):
    """Vacancy-aware baseline split -- winner-take counterpart to
    build_losers_lineage.py's split_losers_baseline(). Freezes everything
    before `live_start_year` into closed reigns (replaying both real belt
    games AND vacancy events in chronological order), returns the reign
    open as of that boundary. Returns (historical_belt_games,
    historical_reigns, open_reign)."""
    historical_belt_games = [bg for bg in belt_games if bg["season"] < live_start_year]
    boundary = f"{live_start_year}-01-01"
    historical_vacancies = [
        v for v in all_vacancies
        if v.get("last_activity_date", v["reign_started"]) < boundary
    ]

    events = [("game", bg["date"], bg) for bg in historical_belt_games]
    events += [("vacancy", v.get("last_activity_date", v["reign_started"]), v)
               for v in historical_vacancies]
    events.sort(key=lambda e: e[1])

    if not events:
        open_reign = {"team": first_reign["team"], "start_date": first_reign["start_date"],
                      "won_from": first_reign.get("won_from"),
                      "won_score": first_reign.get("won_score"), "defenses": 0,
                      "predecessor": first_reign.get("predecessor")}
        return [], [], open_reign

    closed = []
    reign = {"team": first_reign["team"], "start_date": first_reign["start_date"],
             "won_from": first_reign.get("won_from"),
             "won_score": first_reign.get("won_score"), "defenses": 0,
             "predecessor": first_reign.get("predecessor")}
    for kind, _, e in events:
        if kind == "game":
            bg = e
            if reign is None:   # a belt re-established after a retirement
                reign = {"team": bg["new_holder"], "start_date": bg["date"],
                         "won_from": bg.get("holder"), "won_score": bg["score"], "defenses": 0,
                         "reestablished": True}
                continue
            if bg["outcome"] == "established":
                continue
            if bg["outcome"] in ("changed", "lost (tie)"):
                reign["end_date"] = bg["date"]
                reign["lost_to"] = bg["new_holder"]
                closed.append(reign)
                reign = {"team": bg["new_holder"], "start_date": bg["date"],
                         "won_from": bg["holder"], "won_score": bg["score"], "defenses": 0}
            else:
                reign["defenses"] += 1
        else:
            v = e
            if reign is None or reign["team"] != v["team"]:
                print(f"WARNING: vacancy record for {v['team']!r} doesn't match "
                      f"the reign open at that point ({reign['team']!r}) -- "
                      f"skipping it in the historical split.", file=sys.stderr)
                continue
            reign["end_date"] = v.get("last_activity_date", v["reign_started"])
            reign["lost_to"] = None
            if v.get("reverted_to") is None:
                reign["retired"] = True     # the belt ended here; nothing synthetic follows
                closed.append(reign)
                reign = None
                continue
            reign["vacated"] = True
            closed.append(reign)
            inherited = None
            for r in reversed(closed):
                if r["team"] == v["reverted_to"]:
                    inherited = _predecessor(r)
                    break
            reign = {"team": v["reverted_to"], "start_date": v["effective_date"],
                     "won_from": None, "won_score": None, "defenses": 0,
                     "reclaimed_after": v["team"], "predecessor": inherited}
    return historical_belt_games, closed, reign


def filter_division1_games(games, d1_teams, scope=None):
    """Keep only games where BOTH participants are eligible under `scope`:
    `d1_teams` is the {school: "fbs"|"fcs"} dict from build_lineage.py's
    fetch_division1_teams. scope=None/"combined" keeps both current
    Division 1 classifications; scope="fbs"/"fcs" additionally requires
    BOTH sides to carry that one classification. Identical logic to
    build_losers_lineage.py's own filter_division1_games -- duplicated
    here (rather than imported, which would create a circular import
    since that file already imports FROM build_lineage.py) so
    build_lineage.py's own FBS-only/FCS-only championship-belt scopes can
    use it too."""
    if scope in (None, "combined"):
        eligible = set(d1_teams)
    else:
        eligible = {school for school, c in d1_teams.items() if c == scope}
    return [g for g in games if g["home"] in eligible and g["away"] in eligible]


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_vacancies(path):
    return load_json(path) or []


def save_json(obj, path, hist_dir):
    os.makedirs(hist_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
