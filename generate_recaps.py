#!/usr/bin/env python3
"""
Write a short AI-generated recap -- highlighting the drives and plays that
actually decided it -- for every belt game since 2003 that has a full box
score and/or play-by-play on file. Unlike generate_ai_preview.py (one
upcoming game, regenerated only when the matchup changes), this covers many
games at once: a one-time backfill the first time it runs (currently ~280+
settled belt games with box scores), then just the newest completed belt
game or two on every run after that.

Why a committed cache, same reasoning as ai_preview_cache/: a settled
game's recap never needs to change once written, so recap_cache/recaps.json
is committed to git and keyed by game id -- a rerun only ever generates a
recap for a game id missing from that cache, so re-running the pipeline
(or the whole backfill) twice never spends money twice on the same game.

Usage:
    export ANTHROPIC_API_KEY=your_key_here   # console.anthropic.com
    python3 generate_recaps.py

Safe to run with no API key, no game_details.json, or no game_plays.json --
in every case it writes belt_data/recaps.json from whatever is already
cached (or {} / nulls) rather than failing the pipeline. This feature is
optional; the rest of the site works fine without it.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

OUT_DIR = "belt_data"
CACHE_DIR = "recap_cache"
CACHE_PATH = os.path.join(CACHE_DIR, "recaps.json")
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"  # cheap + fast; change here to use a different model
MAX_TOKENS = 800
SAVE_EVERY = 10  # persist the cache to disk this often during a big backfill


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


STAT_LABELS = [
    ("totalYards", "total yards"),
    ("rushingYards", "rushing yards"),
    ("netPassingYards", "passing yards"),
    ("turnovers", "turnovers"),
    ("thirdDownEff", "3rd-down conversions"),
    ("possessionTime", "time of possession"),
]


def team_stats_line(side_name, side_stats):
    if not side_stats:
        return None
    stats = side_stats.get("stats") or {}
    bits = [f"{label} {stats[cat]}" for cat, label in STAT_LABELS if cat in stats]
    if not bits:
        return None
    return f"{side_stats.get('team', side_name)}: " + ", ".join(bits)


def plays_block(plays, limit=25):
    if not plays:
        return "No detailed play-by-play is available for this game -- write from the score and team stats alone."
    lines = []
    for p in plays[:limit]:
        when = f"Q{p.get('period', '?')} {p.get('clock') or '?'}"
        yards = p.get("yards_gained")
        yard_bit = f" ({yards} yds)" if yards not in (None, 0) else ""
        text = p.get("play_text") or p.get("play_type") or "notable play"
        lines.append(f"{when} -- {p.get('offense', '?')}: {text}{yard_bit}")
    return "\n".join(lines)


def build_prompt(game, details, plays):
    home, away = game["home"], game["away"]
    line_score = (details or {}).get("line_score")
    team_stats = (details or {}).get("team_stats") or {}

    if game.get("outcome") == "changed":
        stakes = (f"This game DECIDED THE BELT -- {game['new_holder']} took it from "
                  f"{game['holder']} by winning on the field.")
    else:
        stakes = f"{game['holder']} was defending the belt in this game and kept it."

    home_line = team_stats_line(home, team_stats.get("home"))
    away_line = team_stats_line(away, team_stats.get("away"))
    stats_block = "\n".join(x for x in (home_line, away_line) if x) or "No team box score on file."

    quarter_bit = ""
    if line_score:
        quarter_bit = (f"Quarter-by-quarter -- {home}: {line_score.get('home')}, "
                        f"{away}: {line_score.get('away')}.\n")

    return f'''You are writing a recap for "The College Football Belt," a site that tracks a lineal championship belt that has passed hand to hand on the field since 1869 -- whoever last beat the holder holds the belt, no committee or poll involved.

Game: {away} at {home} ({game['date']}), final score {game['score']} (home-away, {home} then {away}).
{stakes}
{quarter_bit}Team stats:
{stats_block}

Notable plays, in order:
{plays_block(plays)}

Write a JSON object with exactly these keys and nothing else:
  "recap": 3-5 sentences recapping how the game actually unfolded -- reference specific plays or drives from the notable-plays list above where you can, rather than only the final score. Plain prose, no markdown.
  "key_moments": a list of 2-4 short strings, each naming one specific play or drive that mattered (a score, a turnover, a defensive stop) and why.

Output ONLY the JSON object, no other text, no markdown code fence.'''


def call_claude(prompt, api_key, retries=4):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    delay = 3
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                resp = json.loads(r.read().decode())
            return "".join(block.get("text", "") for block in resp.get("content", []))
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < retries:
                print(f"  {e.code} from the Claude API, waiting {delay}s "
                      f"(attempt {attempt}/{retries})...")
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise


def parse_recap(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        data = json.loads(t)
        return {
            "recap": (data.get("recap") or "").strip(),
            "key_moments": [str(x).strip() for x in (data.get("key_moments") or []) if str(x).strip()],
        }
    except (json.JSONDecodeError, AttributeError):
        return {"recap": t, "key_moments": []}


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    os.makedirs(OUT_DIR, exist_ok=True)

    lineage = load_json(os.path.join(OUT_DIR, "lineage.json"))
    if not lineage:
        print("No belt_data/lineage.json -- run build_lineage.py first. "
              "Writing an empty recaps.json rather than failing the build.", file=sys.stderr)
        with open(os.path.join(OUT_DIR, "recaps.json"), "w") as f:
            json.dump({}, f)
        return
    belt_games = lineage["belt_games"]

    game_details = load_json(os.path.join(OUT_DIR, "game_details.json"), default={})
    game_plays = load_json(os.path.join(OUT_DIR, "game_plays.json"), default={})

    eligible = [g for g in belt_games
                if (game_details.get(str(g["game_id"])) or {}).get("team_stats")]
    print(f"{len(belt_games)} belt games total, {len(eligible)} have a full box score "
          f"and are eligible for an AI recap.")

    cache = load_json(CACHE_PATH, default={})

    def write_output():
        out = {str(g["game_id"]): (cache.get(str(g["game_id"])) or {}).get("recap")
               for g in belt_games}
        with open(os.path.join(OUT_DIR, "recaps.json"), "w") as f:
            json.dump(out, f, indent=2, sort_keys=True)
        have = sum(1 for v in out.values() if v)
        print(f"Wrote belt_data/recaps.json ({have} of {len(belt_games)} belt games have a recap).")

    missing = [g for g in eligible if str(g["game_id"]) not in cache]

    if not api_key:
        print("No ANTHROPIC_API_KEY set -- skipping recap generation (this "
              "feature is optional; the rest of the site builds fine "
              "without it). Get a key at https://console.anthropic.com/"
              "settings/keys and set ANTHROPIC_API_KEY to enable it.")
        write_output()
        return

    if not missing:
        print("Every eligible belt game already has a cached recap -- nothing new to generate.")
        write_output()
        return

    print(f"Generating {len(missing)} new recap(s) (1 Claude API call each, model {MODEL})...")

    def save_cache():
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        os.replace(tmp, CACHE_PATH)

    generated = 0
    try:
        for i, g in enumerate(missing, 1):
            gid = str(g["game_id"])
            details = game_details.get(gid)
            plays = game_plays.get(gid) or []
            prompt = build_prompt(g, details, plays)
            try:
                raw_text = call_claude(prompt, api_key)
                recap = parse_recap(raw_text)
                cache[gid] = {"generated": time.strftime("%Y-%m-%d"), "recap": recap}
                generated += 1
            except Exception as e:
                print(f"  Recap for game {gid} ({g['away']} at {g['home']}, {g['date']}) "
                      f"failed ({e}) -- skipping it, continuing with the rest.", file=sys.stderr)
            if i % SAVE_EVERY == 0 or i == len(missing):
                save_cache()
                print(f"  {i}/{len(missing)} processed this run "
                      f"({generated} generated, cache now has {len(cache)} entries)")
            time.sleep(0.3)
    finally:
        save_cache()

    write_output()


if __name__ == "__main__":
    main()
