#!/usr/bin/env python3
"""
Write a short AI-generated preview (overview, key matchups, betting angles)
for the belt holder's next scheduled game, using the Claude API.

Why a committed cache: the update pipeline can run more than once before
the next game actually changes (a manual re-run, an unrelated commit, the
Sunday cron landing the same week twice). Every run rebuilds everything
from scratch, so without a cache this would call the Claude API -- and
spend real money -- for an identical result every single time. Instead,
the last generated preview is cached in ai_preview_cache/cache.json,
committed to git (same pattern as historical_data/ for the CFBD budget),
keyed to exactly which two teams and which date it's for. A fresh call
only happens when that key changes, i.e. there's actually a new "next
game" to preview.

Usage:
    export ANTHROPIC_API_KEY=your_key_here   # console.anthropic.com
    python3 generate_ai_preview.py

Safe to run with no upcoming game, no matchup data, or no API key at all
-- in every case it writes belt_data/ai_preview.json = null (or reuses the
cache) rather than failing the pipeline. This feature is optional; the
rest of the site works fine without it.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

OUT_DIR = "belt_data"
CACHE_DIR = "ai_preview_cache"
CACHE_PATH = os.path.join(CACHE_DIR, "cache.json")
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"  # cheap + fast; change here to use a different model
MAX_TOKENS = 700


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def cache_key(next_game):
    return f"{next_game['team']}|{next_game['opponent']}|{next_game['date']}"


def form_line(team, games):
    if not games:
        return f"{team}: no recent completed games on record."
    bits = []
    for g in games:
        result = "T" if g.get("tied") else ("W" if g["won"] else "L")
        loc = "vs" if g["home"] else "at"
        bits.append(f"{result} {g['score_for']}-{g['score_against']} {loc} {g['opponent']}")
    return f"{team} last {len(games)}: " + "; ".join(bits)


def build_prompt(next_game, matchup):
    holder = next_game["team"]
    opponent = next_game["opponent"]
    if next_game.get("neutral"):
        side = "faces (neutral site)"
    elif next_game.get("is_home"):
        side = "hosts"
    else:
        side = "travels to"

    matchup = matchup or {}
    recent = matchup.get("recent_form") or {}
    holder_form = form_line(holder, recent.get(holder, []))
    opp_form = form_line(opponent, recent.get(opponent, []))

    h2h = matchup.get("head_to_head")
    total = 0
    if h2h:
        total = (h2h.get("team1_wins", 0) or 0) + (h2h.get("team2_wins", 0) or 0) + (h2h.get("ties", 0) or 0)
    if h2h and total > 0:
        h2h_line = (f"All-time series since {h2h.get('start_year')}: "
                    f"{holder} {h2h.get('team1_wins', 0)}, {opponent} {h2h.get('team2_wins', 0)}")
        if h2h.get("ties"):
            h2h_line += f", {h2h['ties']} tie(s)"
        h2h_line += "."
        recent_meetings = (h2h.get("games") or [])[:5]
        if recent_meetings:
            m_bits = [f"{m.get('season')}: {m.get('home_team')} {m.get('home_score')}-"
                      f"{m.get('away_score')} {m.get('away_team')}" for m in recent_meetings]
            h2h_line += " Last meetings: " + "; ".join(m_bits) + "."
    else:
        h2h_line = f"{holder} and {opponent} have no recorded meetings in CFBD's data."

    return f'''You are writing a short preview for "The College Football Belt," a site that tracks a lineal championship belt that has passed hand to hand on the field since 1869 -- whoever last beat the holder holds the belt, no committee or poll involved. The belt itself is on the line in this game.

Upcoming game: {holder} (current belt holder) {side} {opponent}, on {next_game['date']}.

Stats:
{holder_form}
{opp_form}
{h2h_line}

Write a JSON object with exactly these keys and nothing else:
  "overview": 2-3 sentences on the game and what's at stake for the belt. Plain prose, no markdown.
  "key_matchups": a list of 2-3 short strings, each a specific on-field matchup or storyline worth watching.
  "betting_angles": 2-3 sentences discussing betting-relevant trends (e.g. what recent form or the head-to-head history suggests). Frame this as analysis of the numbers, not a recommendation -- do not tell the reader what to bet or claim a pick is likely to hit.

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


def parse_preview(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        data = json.loads(t)
        return {
            "overview": (data.get("overview") or "").strip(),
            "key_matchups": [str(x).strip() for x in (data.get("key_matchups") or []) if str(x).strip()],
            "betting_angles": (data.get("betting_angles") or "").strip(),
        }
    except (json.JSONDecodeError, AttributeError):
        # Model didn't return clean JSON -- fall back to showing it as
        # plain prose rather than losing the generation entirely.
        return {"overview": t, "key_matchups": [], "betting_angles": ""}


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    os.makedirs(OUT_DIR, exist_ok=True)
    ai_preview_path = os.path.join(OUT_DIR, "ai_preview.json")

    next_game = load_json(os.path.join(OUT_DIR, "next_game.json"))
    if not next_game:
        with open(ai_preview_path, "w") as f:
            json.dump(None, f)
        print("No upcoming game -- nothing to preview.")
        return

    key = cache_key(next_game)
    cached = load_json(CACHE_PATH)
    if cached and cached.get("key") == key and cached.get("preview"):
        print(f"Cached AI preview already covers {key} -- reusing it (no API call).")
        with open(ai_preview_path, "w") as f:
            json.dump(cached["preview"], f, indent=2)
        return

    if not api_key:
        print("No ANTHROPIC_API_KEY set -- skipping the AI preview (this "
              "feature is optional; the rest of the site builds fine "
              "without it). Get a key at https://console.anthropic.com/"
              "settings/keys and set ANTHROPIC_API_KEY to enable it.")
        with open(ai_preview_path, "w") as f:
            json.dump(None, f)
        return

    matchup = load_json(os.path.join(OUT_DIR, "matchup_preview.json"))
    prompt = build_prompt(next_game, matchup)

    print(f"Generating a fresh AI preview for {key} (1 Claude API call, model {MODEL})...")
    try:
        raw_text = call_claude(prompt, api_key)
    except Exception as e:
        print(f"AI preview generation failed ({e}) -- continuing without "
              f"one rather than failing the whole build.", file=sys.stderr)
        with open(ai_preview_path, "w") as f:
            json.dump(None, f)
        return

    preview = parse_preview(raw_text)

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump({
            "key": key,
            "generated": time.strftime("%Y-%m-%d"),
            "preview": preview,
        }, f, indent=2)
    with open(ai_preview_path, "w") as f:
        json.dump(preview, f, indent=2)
    print(f"Wrote a fresh AI preview for {key} and cached it to {CACHE_PATH}.")


if __name__ == "__main__":
    main()
