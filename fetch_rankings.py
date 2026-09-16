#!/usr/bin/env python3
"""
Fetch the AP poll (and, from 2014, the CFP committee rankings) for every
season the belt has been on the line since the AP poll began in 1936, and
line every belt game up against the poll that was in effect when it was
played -- feeds polls.html ("The belt vs. the polls") plus the rank chips
on game pages, reign pages and the preview (2026-09-16 batch).

Usage:
    export CFBD_API_KEY=your_key_here      # same key as build_lineage.py
    python3 fetch_rankings.py               # reads belt_data/lineage.json

Inputs:
    belt_data/lineage.json                  (build_lineage.py)
    historical_data/rankings.json           the poll cache (committed back
                                            by the workflow like the rest
                                            of historical_data/)
Outputs:
    historical_data/rankings.json           cache, extended by this run
    belt_data/rankings.json                 per-belt-game ranks + the
                                            week-by-week "holder vs. No. 1"
                                            table build_site.py renders

CFBD call budget: ONE call per season (/rankings?year=YYYY returns every
week's polls at once). The current season is refetched every run (its
polls change weekly); every settled season is fetched once and cached
forever. A season that CFBD has no polls for (1936 is the first) is also
cached, as empty, so it's never asked for again. Backfill is capped at
BACKFILL_PER_RUN seasons per run so the first runs after this ships cost
~30 calls each rather than ~90 at once; the cache completes itself over
three or four runs. Set RANKINGS_BACKFILL_ALL=1 to do it in one go.

Only the AP poll and the CFP rankings are kept (school + rank), never the
vote/points detail -- that's what the site uses and it keeps the cache a
couple of megabytes rather than ten.

Safe to run with no API key: it still (re)writes belt_data/rankings.json
from whatever is already cached, so build_site.py always has something
to read.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date

API_BASE = "https://api.collegefootballdata.com"
DATA_DIR = "belt_data"
HIST_DIR = "historical_data"
CACHE_PATH = os.path.join(HIST_DIR, "rankings.json")
OUT_PATH = os.path.join(DATA_DIR, "rankings.json")
LINEAGE_PATH = os.path.join(DATA_DIR, "lineage.json")

FIRST_POLL_SEASON = 1936     # the AP poll's first season
BACKFILL_PER_RUN = 30


def pick(d, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _get_json(url, api_key, label, retries=6):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                wait = min(5 * (2 ** attempt), 60)
                print(f"  {e.code} on {label}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return []


def fetch_season_polls(year, api_key):
    """[{"st": "regular"|"postseason", "week": n, "ap": [[school, rank]...],
    "cfp": [[school, rank]...]}], one entry per poll week CFBD has."""
    rows = _get_json(f"{API_BASE}/rankings?year={year}", api_key, f"rankings {year}")
    out = []
    for entry in rows or []:
        st = pick(entry, "season_type", "seasonType", default="regular")
        week = pick(entry, "week", default=0)
        ap, cfp = [], []
        for poll in pick(entry, "polls", default=[]) or []:
            name = (pick(poll, "poll", default="") or "").lower()
            ranks = [[pick(r, "school", default=""), int(pick(r, "rank", default=0))]
                     for r in (pick(poll, "ranks", default=[]) or [])]
            ranks = [r for r in ranks if r[0] and r[1]]
            if "ap" in name.split() or name.startswith("ap"):
                ap = ranks
            elif "playoff" in name or "cfp" in name:
                cfp = ranks
        if ap or cfp:
            out.append({"st": st, "week": int(week), "ap": ap, "cfp": cfp})
    out.sort(key=lambda w: (w["st"] != "regular", w["week"]))
    return out


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(obj, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- matching

def _rank_of(ranks, school):
    for s, r in ranks:
        if s == school:
            return r
    return None


def poll_for_game(weeks, game):
    """The poll in effect for a belt game: the latest regular-season poll
    with week <= the game's week (a postseason game uses the last
    regular-season poll). None when the season's first poll came out after
    the game (early-season games before the poll era's mid-October debuts)."""
    reg = [w for w in weeks if w["st"] == "regular"]
    if not reg:
        return None
    if game.get("season_type") == "postseason":
        return reg[-1]
    gw = game.get("week") or 0
    eligible = [w for w in reg if w["week"] <= gw]
    return eligible[-1] if eligible else None


def holder_entering(belt_games, season, st, week):
    """The belt holder going into poll week (season, st, week) -- i.e.
    after every belt game played before that week. A postseason (final)
    poll comes out after the bowls, so it sees the holder after all of
    that season's games."""
    holder = None
    for g in belt_games:
        if g["season"] < season:
            holder = g["new_holder"]
            continue
        if g["season"] > season:
            break
        if st == "postseason":
            holder = g["new_holder"]
            continue
        g_st = g.get("season_type") or "regular"
        if g_st == "regular" and (g.get("week") or 0) < week:
            holder = g["new_holder"]
    return holder


def build_output(cache, belt_games, today):
    seasons = cache.get("seasons", {})
    games_out = {}
    weeks_out = []
    for g in belt_games:
        s = g["season"]
        entry = seasons.get(str(s))
        if not entry or s < FIRST_POLL_SEASON:
            continue
        poll = poll_for_game(entry["weeks"], g)
        if poll is None:
            games_out[str(g["game_id"])] = {"poll": None}
            continue
        holder = g["holder"] or g["new_holder"]
        opponent = g["opponent"]
        games_out[str(g["game_id"])] = {
            "poll": "AP", "week": poll["week"],
            "holder_ap": _rank_of(poll["ap"], holder),
            "opponent_ap": _rank_of(poll["ap"], opponent),
            "holder_cfp": _rank_of(poll["cfp"], holder) if poll["cfp"] else None,
            "opponent_cfp": _rank_of(poll["cfp"], opponent) if poll["cfp"] else None,
            "ap1": poll["ap"][0][0] if poll["ap"] else None,
        }
    for s_str, entry in sorted(seasons.items(), key=lambda kv: int(kv[0])):
        s = int(s_str)
        for w in entry["weeks"]:
            if not w["ap"]:
                continue
            holder = holder_entering(belt_games, s, w["st"], w["week"])
            if holder is None:
                continue
            weeks_out.append({
                "season": s, "st": w["st"], "week": w["week"],
                "ap1": w["ap"][0][0],
                "holder": holder,
                "holder_ap": _rank_of(w["ap"], holder),
                "holder_cfp": _rank_of(w["cfp"], holder) if w["cfp"] else None,
            })
    # the latest poll on file, for the preview / outlook chips
    current = None
    if seasons:
        latest_s = max(int(k) for k in seasons)
        wk = [w for w in seasons[str(latest_s)]["weeks"] if w["ap"]]
        if wk:
            w = wk[-1]
            current = {"season": latest_s, "st": w["st"], "week": w["week"],
                       "ap": {s: r for s, r in w["ap"]}, "cfp": {s: r for s, r in w["cfp"]}}
    wanted = [s for s in sorted({g["season"] for g in belt_games}) if s >= FIRST_POLL_SEASON]
    return {
        "generated": today.isoformat(),
        "first_poll_season": FIRST_POLL_SEASON,
        "seasons_cached": sorted(int(k) for k in seasons),
        "seasons_missing": [s for s in wanted if str(s) not in seasons],
        "games": games_out,
        "weeks": weeks_out,
        "current": current,
    }


def main():
    api_key = os.environ.get("CFBD_API_KEY")
    today = date.today()
    lineage = load_json(LINEAGE_PATH, None)
    if not lineage:
        print(f"{LINEAGE_PATH} not found -- run build_lineage.py first. Nothing to do.")
        return
    belt_games = lineage["belt_games"]
    cache = load_json(CACHE_PATH, {"schema": 1, "seasons": {}})
    seasons = cache.setdefault("seasons", {})

    wanted = sorted({g["season"] for g in belt_games if g["season"] >= FIRST_POLL_SEASON})
    current_season = max(wanted) if wanted else None
    to_fetch = []
    if api_key:
        if current_season is not None:
            to_fetch.append(current_season)          # polls change weekly
        missing = [s for s in wanted if str(s) not in seasons and s != current_season]
        cap = None if os.environ.get("RANKINGS_BACKFILL_ALL", "").lower() in ("1", "true", "yes") else BACKFILL_PER_RUN
        backfill = missing if cap is None else missing[-cap:]   # newest first: the seasons people look up most
        to_fetch.extend(backfill)
        if missing and cap is not None and len(missing) > cap:
            print(f"{len(missing)} seasons still uncached; fetching {len(backfill)} this run "
                  f"(BACKFILL_PER_RUN={BACKFILL_PER_RUN}); the rest complete on later runs.")
    else:
        print("No CFBD_API_KEY -- not fetching; rebuilding belt_data/rankings.json from the cache only.")

    fetched = 0
    for s in to_fetch:
        try:
            weeks = fetch_season_polls(s, api_key)
        except Exception as e:  # noqa: BLE001 -- one bad season must not sink the pipeline
            print(f"  WARNING: rankings fetch failed for {s}: {e} -- skipping", file=sys.stderr)
            continue
        seasons[str(s)] = {"fetched": today.isoformat(), "weeks": weeks}
        fetched += 1
        print(f"  {s}: {len(weeks)} poll week(s)")
        time.sleep(0.15)
        if fetched % 10 == 0:
            save_json(cache, CACHE_PATH)
    if fetched:
        save_json(cache, CACHE_PATH)
        print(f"Cached {fetched} season(s) of polls to {CACHE_PATH} ({len(seasons)} seasons total).")

    out = build_output(cache, belt_games, today)
    save_json(out, OUT_PATH)
    print(f"Wrote {OUT_PATH}: {len(out['games'])} belt games matched to a poll, "
          f"{len(out['weeks'])} poll weeks, {len(out['seasons_missing'])} season(s) still to fetch.")


if __name__ == "__main__":
    main()
