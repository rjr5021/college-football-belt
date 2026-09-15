#!/usr/bin/env python3
"""
Write a short, strictly factual note for every belt game that does NOT have
a full box score on file -- almost all of them are pre-2003, since that's
CFBD's hard cutoff for team/player stats (see fetch_game_details.py's
module docstring). Unlike generate_recaps.py (which writes a real recap
FROM a game's actual box score and play-by-play), there is no such detail
to draw from here for most of these games -- it usually doesn't exist
anywhere online in a form this site could verify. So this writes something
much more limited on purpose: a couple of sentences built ONLY from the
handful of facts this site already has for every belt game (the matchup,
the date, the final score, and what it meant for the belt), with the model
explicitly told never to invent a play, a player, an attendance figure, or
any other specific not given to it. See README.md's "Historical notes"
section for the reasoning.

This is deliberately a SEPARATE script/cache from generate_recaps.py rather
than one script handling both, so the two are never confused with each
other: a "recap" on this site always means "written from a real box
score"; a "historical note" always means "written from the bare facts
this site's own record book already has, nothing more." build_site.py
only ever falls back to a historical note when there's no recap for a
game.

Usage:
    export ANTHROPIC_API_KEY=your_key_here   # console.anthropic.com
    python3 generate_historical_notes.py

Safe to run with no API key or no lineage.json -- in every case it writes
belt_data/historical_notes.json from whatever is already cached (or {})
rather than failing the pipeline. This feature is optional; the rest of
the site works fine without it.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

OUT_DIR = "belt_data"
CACHE_DIR = "recap_cache"
CACHE_PATH = os.path.join(CACHE_DIR, "historical_notes.json")
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"  # cheap + fast; change here to use a different model
MAX_TOKENS = 300  # these are short on purpose -- a couple of sentences, not a recap
SAVE_EVERY = 20  # persist the cache to disk this often during the backfill


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def stakes_line(game):
    holder, new_holder = game["holder"], game["new_holder"]
    outcome = game["outcome"]
    if holder is None:
        return (f"This was the very first game in the belt's entire history -- there was "
                 f"no defending holder yet. {new_holder} won it, putting the belt up for "
                 f"the first time.")
    if outcome == "changed":
        return f"{new_holder} took the belt from {holder} by winning this game on the field."
    if outcome == "lost (tie)":
        return (f"This game ended in a tie, and under this site's tie rule that counts as "
                 f"the challenger taking it -- {new_holder} took the belt from {holder}.")
    if outcome == "retained (tie)":
        return (f"This game ended in a tie, and under this site's tie rule the holder keeps "
                 f"the belt on a tie -- {holder} defended it successfully.")
    return f"{holder} was defending the belt and kept it, beating {game['opponent']}."


def build_prompt(game, game_number, total_games):
    home, away = game["home"], game["away"]
    neutral_bit = " (neutral site)" if game["neutral"] else ""
    week_bit = f", week {game['week']}" if game["season_type"] == "regular" else ""

    return f'''You are writing a short, strictly factual note for "The College Football Belt," a site that tracks a lineal college football championship that has passed hand to hand on the field since 1869 -- whoever last beat the holder holds the belt, no committee or poll involved.

This is belt game #{game_number} of {total_games} in the site's full history. No box score, player stats, or play-by-play survive for this game in this site's data -- that level of detail from the era this game was played in simply isn't available anywhere the site can verify. ONLY the facts listed below are known and may be stated.

Facts -- this is the ENTIRE set of verified facts for this game. Do not add, infer, or guess anything beyond it: no plays, no weather, no attendance figure, no standout performer, no narrative color, nothing not explicitly given here.
- Matchup: {away} at {home}{neutral_bit}
- Date: {game["date"]}
- Season: {game["season"]} ({game["season_type"]}{week_bit})
- Final score: {game["score"]} (home {home} -- away {away})
- What it meant for the belt: {stakes_line(game)}

Write a JSON object with exactly one key:
  "note": 1-3 sentences of plain prose, no markdown, covering only the facts above -- the matchup, the score, and what it meant for the belt. Do not invent a single detail beyond what's given. If there is genuinely nothing more to add beyond a restatement of the facts, that's fine -- write it plainly rather than padding it out with invented specifics.

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


def parse_note(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        data = json.loads(t)
        return {"note": (data.get("note") or "").strip()}
    except (json.JSONDecodeError, AttributeError):
        return {"note": t}


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    os.makedirs(OUT_DIR, exist_ok=True)

    lineage = load_json(os.path.join(OUT_DIR, "lineage.json"))
    if not lineage:
        print("No belt_data/lineage.json -- run build_lineage.py first. "
              "Writing an empty historical_notes.json rather than failing the build.",
              file=sys.stderr)
        with open(os.path.join(OUT_DIR, "historical_notes.json"), "w") as f:
            json.dump({}, f)
        return
    belt_games = lineage["belt_games"]
    total_games = len(belt_games)

    game_details = load_json(os.path.join(OUT_DIR, "game_details.json"), default={})

    # Eligible = the exact opposite of generate_recaps.py's eligibility: no
    # team_stats on file, so there's no box score to write a real recap
    # from. A game that DOES have a recap never needs a historical note
    # too -- build_site.py only falls back to this when there's no recap.
    eligible = [(i, g) for i, g in enumerate(belt_games, 1)
                if not (game_details.get(str(g["game_id"])) or {}).get("team_stats")]
    print(f"{total_games} belt games total, {len(eligible)} have no box score on file "
          f"and are eligible for a historical note.")

    cache = load_json(CACHE_PATH, default={})

    def write_output():
        # build_site.py reads each entry as {"note": "..."} (same shape as
        # what's cached), not a bare string -- keep this in sync with how
        # render_recap() in build_site.py unpacks g["historical_note"].
        out = {}
        for _, g in eligible:
            gid = str(g["game_id"])
            note_text = (cache.get(gid) or {}).get("note")
            if note_text:
                out[gid] = {"note": note_text}
        with open(os.path.join(OUT_DIR, "historical_notes.json"), "w") as f:
            json.dump(out, f, indent=2, sort_keys=True)
        print(f"Wrote belt_data/historical_notes.json "
              f"({len(out)} of {len(eligible)} eligible belt games have a note).")

    missing = [(n, g) for n, g in eligible if str(g["game_id"]) not in cache]

    if not api_key:
        print("No ANTHROPIC_API_KEY set -- skipping historical-note generation (this "
              "feature is optional; the rest of the site builds fine "
              "without it). Get a key at https://console.anthropic.com/"
              "settings/keys and set ANTHROPIC_API_KEY to enable it.")
        write_output()
        return

    if not missing:
        print("Every eligible belt game already has a cached historical note -- "
              "nothing new to generate.")
        write_output()
        return

    print(f"Generating {len(missing)} new historical note(s) "
          f"(1 Claude API call each, model {MODEL})...")

    def save_cache():
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        os.replace(tmp, CACHE_PATH)

    generated = 0
    try:
        for i, (game_number, g) in enumerate(missing, 1):
            gid = str(g["game_id"])
            prompt = build_prompt(g, game_number, total_games)
            try:
                raw_text = call_claude(prompt, api_key)
                note = parse_note(raw_text)
                cache[gid] = {"generated": time.strftime("%Y-%m-%d"), "note": note["note"]}
                generated += 1
            except Exception as e:
                print(f"  Historical note for game {gid} ({g['away']} at {g['home']}, "
                      f"{g['date']}) failed ({e}) -- skipping it, continuing "
                      f"with the rest.", file=sys.stderr)
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
