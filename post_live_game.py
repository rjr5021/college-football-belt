#!/usr/bin/env python3
"""
Posts LIVE updates to X (@CollegeFBBelt) while the belt holder's game is
actually being played (2026-09-19, Bob: "automatic posts for the start of
the belt game, any scoring updates, halftime and the end of the game").
Four things, sourced from ESPN's public scoreboard feed -- the same one
build_site.py's client-side live scoreboard already polls from the
visitor's browser (see live_scoreboard_html() / LIVE_SCOREBOARD_JS) --
because CFBD (the site's usual data source) isn't real-time:

  1. KICKOFF -- the belt holder's game has gone from scheduled to live.
  2. SCORING UPDATE -- either team's score changes. Includes the actual
     play when ESPN's feed has it ("Jordan Faison 25 Yd pass from CJ Carr
     (Mitchell Evans Kick)") -- ESPN's own wording, not an AI paraphrase:
     a live post has no time for anyone to check it before it goes out,
     so this uses the official play text verbatim rather than risking an
     AI getting a yardage or a name wrong on a live score. Framed as
     SAFE / IN DANGER for the belt -- Bob (2026-09-19): "I only want it
     to say belt in danger if the holder is losing", so a tie reads as
     safe too (a tie doesn't change hands, same rule as the site's own
     outcome logic -- see belt_status_line()).
  3. HALFTIME -- ESPN's status reaches halftime.
  4. FINAL -- ESPN's status reaches final. This is a fast, live-sourced
     announcement; it runs ALONGSIDE (not instead of) post_to_x.py's own
     result post, which is the detailed recap posted once CFBD's data
     (belt_data/lineage.json) catches up, usually hours later -- see
     post_results() in post_to_x.py. Two posts per game is intentional:
     fast now, with the detailed recap + game page link later. (Bob
     2026-09-19: confirmed both, rather than suppressing the later one.)

Why this is a single script that loops, not five-minute cron ticks
--------------------------------------------------------------------
GitHub Actions' schedule trigger cannot fire more often than every 5
minutes (an official limit, not a suggestion -- anything tighter is
silently ignored/dropped), and even that's best-effort, with real delays
common at peak times. That's too slow for "the play that just happened."
So live-game.yml's cron still only ticks every 5 minutes, but each
invocation of THIS script, once it's near an actual kickoff, loops
internally (POLL_INTERVAL_SECONDS apart, ~20s) for as long as the game
runs -- one GitHub Actions job holds the whole game, rather than one job
per check. A 5-minute tick that lands while a previous run is still
inside this loop never starts a second one; see live-game.yml's
`concurrency` block, which queues (and auto-drops) any tick that arrives
while one is already running, so only one process is ever polling ESPN
for a given game.

Cost: collegefootballbelt.com's GitHub repo is public, so none of this
costs anything in Actions minutes regardless of how long the job runs or
how often ESPN gets polled -- public repos get unlimited free minutes.
The only real constraint honored here is being a reasonable citizen of
ESPN's free public feed (a poll every ~20s, only during an actual game,
not year-round) and GitHub's own 5-minute schedule floor.

Outside a ~30-minutes-before-kickoff window on an actual belt-game day,
main() exits immediately with ZERO network calls -- so the other ~23
hours a day, and every non-game day, this costs nothing at all.

State and crash recovery
-------------------------
social_cache/x_live_state.json remembers, for the CURRENT game only,
whether the kickoff/halftime/final posts already went out and the score
last seen (so a scoring update only fires on an actual change). Because
one job can run for hours, this file is git-committed (see
commit_cache()) right after EVERY post, not just at the end -- if the job
dies partway (a crash, a cancelled run, GitHub's 6-hour hard job limit),
the next scheduled tick picks the game back up from exactly where this
one left off instead of re-posting a kickoff that already happened.

Usage: same four X_* env vars as post_to_x.py.
    python3 post_live_game.py

OPTIONAL the same way: missing env vars, no upcoming game, or a kickoff
time nowhere near now just print a note and exit 0.
"""

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
BELT_DATA_DIR = os.path.join(HERE, "belt_data")
LINEAGE_PATH = os.path.join(BELT_DATA_DIR, "lineage.json")
NEXT_GAME_PATH = os.path.join(BELT_DATA_DIR, "next_game.json")
RANKINGS_PATH = os.path.join(BELT_DATA_DIR, "rankings.json")
CACHE_DIR = os.path.join(HERE, "social_cache")
CACHE_PATH = os.path.join(CACHE_DIR, "x_live_state.json")
SITE_URL = "https://collegefootballbelt.com"

REQUIRED_ENV = ["X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]

ESPN_SUMMARY_URL = ("https://site.api.espn.com/apis/site/v2/sports/football/"
                     "college-football/summary?event={event_id}")

# How long before kickoff this starts watching, and the longest a single
# job's loop will run before giving up and letting the next scheduled
# tick take over (comfortably past any normal game + overtime; a job that
# hits this without seeing FINAL just means an unusually long game, not a
# bug -- see main()).
PREGAME_WINDOW = timedelta(minutes=30)
MAX_SESSION = timedelta(hours=5, minutes=30)
POLL_INTERVAL_SECONDS = 20


# ---------------------------------------------------------------- helpers

def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_cache():
    return load_json(CACHE_PATH) or {}


def save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def commit_cache(message):
    """Commit + push social_cache/x_live_state.json right now (see the
    module docstring for why this can't wait until the job ends). Not
    fatal if git isn't set up (e.g. running this by hand outside Actions,
    or a transient push race with another workflow) -- prints and the
    polling loop carries on either way; a failed commit here just means
    the next successful one catches up, at worst risking one duplicate
    post if the job dies in between."""
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"],
                        cwd=HERE, check=False)
        subprocess.run(["git", "config", "user.email",
                         "github-actions[bot]@users.noreply.github.com"],
                        cwd=HERE, check=False)
        subprocess.run(["git", "add", "social_cache/x_live_state.json"], cwd=HERE, check=False)
        committed = subprocess.run(["git", "commit", "-m", f"{message} [skip ci]"],
                                    cwd=HERE, capture_output=True, text=True)
        if committed.returncode != 0:
            return  # nothing new to commit
        for attempt in range(3):
            subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=HERE, check=False)
            pushed = subprocess.run(["git", "push"], cwd=HERE, capture_output=True, text=True)
            if pushed.returncode == 0:
                return
            time.sleep(3)
        print("Warning: couldn't push the live-state cache after 3 tries "
              "(not fatal -- the next successful commit will catch up).")
    except Exception as e:
        print(f"git commit/push for the live-state cache failed (not fatal): {e}")


def game_key(next_game):
    return f"{next_game['team']}|{next_game['opponent']}|{next_game['date']}"


def fresh_state(key):
    return {
        "game_key": key,
        "kickoff_posted": False,
        "halftime_posted": False,
        "final_posted": False,
        "last_holder_score": None,
        "last_opp_score": None,
    }


def in_live_window(next_game, now_utc):
    """True from PREGAME_WINDOW before kickoff through MAX_SESSION after
    it. False (no ESPN call at all) when the kickoff time is unknown
    (TBD) -- there's no way to know when to start watching."""
    raw = next_game.get("raw_date")
    if not raw or next_game.get("start_time_tbd"):
        return False
    try:
        kickoff = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return kickoff - PREGAME_WINDOW <= now_utc <= kickoff + MAX_SESSION


def fetch_espn_summary(event_id):
    url = ESPN_SUMMARY_URL.format(event_id=event_id)
    req = Request(url, headers={"User-Agent": "collegefootballbelt.com live poster"})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_team(team_obj, name):
    """Fuzzy match against one of ESPN's team objects, the same way the
    client-side live scoreboard's identify() does -- location/
    displayName/name/abbreviation, case-insensitive, either containing
    the other (handles 'Notre Dame' vs 'Notre Dame Fighting Irish')."""
    name = (name or "").lower()
    for field in ("location", "displayName", "name", "shortDisplayName", "abbreviation"):
        val = (team_obj.get(field) or "").lower()
        if val and (val in name or name in val):
            return True
    return False


def parse_competition(summary, holder, opponent):
    """(state, is_halftime, holder_score, opp_score) from ESPN's summary
    payload, or None if it doesn't look like this game (wrong event id,
    an ESPN hiccup, holder/opponent not found among the two competitors).
    state is ESPN's own 'pre' | 'in' | 'post'."""
    comps = ((summary.get("header") or {}).get("competitions") or [])
    if not comps:
        return None
    comp = comps[0]
    competitors = comp.get("competitors") or []
    if len(competitors) != 2:
        return None

    holder_score = opp_score = None
    for c in competitors:
        team = c.get("team") or {}
        try:
            pts = int(c.get("score"))
        except (TypeError, ValueError):
            pts = None
        if _is_team(team, holder):
            holder_score = pts
        elif _is_team(team, opponent):
            opp_score = pts
    if holder_score is None or opp_score is None:
        return None

    status = comp.get("status") or {}
    type_info = status.get("type") or {}
    state = (type_info.get("state") or "").lower()
    name_and_detail = f"{type_info.get('name') or ''} {type_info.get('shortDetail') or ''}".lower()
    is_half = "halftime" in name_and_detail

    return state, is_half, holder_score, opp_score


def find_scoring_play(summary, holder, opponent):
    """The most recent entry in ESPN's top-level 'scoringPlays' list (not
    under header/competitions) -- {is_holder, label, text} for whichever
    team it belongs to, or None if that list is missing/empty (a brief
    lag right after the score itself updates) or doesn't match either
    team. label is a short category ('touchdown', 'field goal', 'safety',
    '2-point conversion', or ESPN's own type text lowercased as a
    fallback); text is ESPN's own play description, used as-is."""
    plays = summary.get("scoringPlays") or []
    if not plays:
        return None
    play = plays[-1]
    team = play.get("team") or {}
    is_holder = _is_team(team, holder)
    is_opp = _is_team(team, opponent)
    if not is_holder and not is_opp:
        return None
    type_text = ((play.get("type") or {}).get("text") or "").lower()
    if "touchdown" in type_text:
        label = "touchdown"
    elif "field goal" in type_text:
        label = "field goal"
    elif "safety" in type_text:
        label = "safety"
    elif "two-point" in type_text or "2-point" in type_text:
        label = "2-point conversion"
    else:
        label = type_text or "scores"
    return {"is_holder": is_holder, "label": label, "text": (play.get("text") or "").strip()}


# -------------------------------------------------------------- wording

def belt_status_line(holder_score, opp_score):
    """Bob (2026-09-19): 'I only want it to say belt in danger if the
    holder is losing' -- a tie doesn't change hands (same rule as the
    site's own outcome logic: 'retained (tie)'), so only trailing counts
    as IN DANGER; leading OR tied both read as safe."""
    if holder_score > opp_score:
        return "Belt SAFE, for now."
    if holder_score == opp_score:
        return "Tied — belt stays put unless that changes."
    return "Belt IN DANGER."


def matchup_line(next_game, holder, opponent, rankings, ptx):
    h, o = ptx.ranked(holder, rankings), ptx.ranked(opponent, rankings)
    if next_game.get("neutral"):
        return f"{h} vs. {o} (neutral site)"
    if next_game.get("is_home"):
        return f"{o} at {h}"
    return f"{h} at {o}"


def compose_kickoff_tweet(next_game, lineage, rankings, ptx):
    holder, opponent = next_game["team"], next_game["opponent"]
    matchup = matchup_line(next_game, holder, opponent, rankings, ptx)
    reign = lineage["reigns"][-1] if lineage.get("reigns") else None
    stakes = ""
    if reign and reign.get("team") == holder:
        defenses = (reign.get("defenses") or 0) + 1
        nth = "first" if defenses == 1 else ptx.ordinal(defenses)
        days = (date.today() - date.fromisoformat(reign["start_date"])).days
        # Always "-day-" (hyphenated compound adjective), never "-days-",
        # regardless of the count -- matches stakes_lines()'s tiny form
        # in post_to_x.py ("a 294-day reign", not "a 294-days reign").
        stakes = f"\n\n{holder}: {nth} defense of a {days}-day reign."
    return (f"\U0001F3C8 KICKOFF — the belt is on the line\n\n"
            f"{matchup} is underway.{stakes}\n\n{SITE_URL}/preview.html")


def compose_score_tweet(next_game, holder, opponent, prev_h, prev_o,
                         holder_score, opp_score, rankings, ptx, play=None):
    """Tries, in order: the real play (from ESPN's scoringPlays) plus the
    belt framing; then the same without the play text (too long, or
    ESPN's feed hadn't caught up); always fits under X's 280 -- see
    ptx.tweet_length / ptx.TWEET_MAX."""
    holder_disp, opp_disp = ptx.ranked(holder, rankings), ptx.ranked(opponent, rankings)
    if play:
        scorer = holder_disp if play["is_holder"] else opp_disp
        header = f"\U0001F525 {scorer} {play['label']}!"
    else:
        h_delta = holder_score - (prev_h or 0)
        scorer = holder_disp if h_delta > 0 else opp_disp
        header = f"\U0001F525 {scorer} scores!"

    score_line = f"{holder_disp} {holder_score} – {opp_score} {opp_disp}"
    status = belt_status_line(holder_score, opp_score)
    link = f"{SITE_URL}/preview.html"

    candidates = []
    if play and play.get("text"):
        candidates.append(f"{header}\n\n{play['text']}\n\n{score_line}\n{status}\n{link}")
    candidates.append(f"{header}\n\n{score_line}\n{status}\n{link}")

    for text in candidates:
        if ptx.tweet_length(text) <= ptx.TWEET_MAX:
            return text
    return candidates[-1]  # shortest form; always fits in practice


def compose_halftime_tweet(next_game, holder, opponent, holder_score, opp_score, rankings, ptx):
    holder_disp, opp_disp = ptx.ranked(holder, rankings), ptx.ranked(opponent, rankings)
    return (f"\U0001F3DF️ HALFTIME\n\n"
            f"{holder_disp} {holder_score} – {opp_score} {opp_disp}\n\n"
            f"{belt_status_line(holder_score, opp_score)}\n"
            f"{SITE_URL}/preview.html")


def compose_final_tweet(lineage, holder, opponent, holder_score, opp_score, ptx):
    if holder_score > opp_score:
        reign = lineage["reigns"][-1] if lineage.get("reigns") else None
        defenses = None
        if reign and reign.get("team") == holder:
            defenses = (reign.get("defenses") or 0) + 1
        stakes = f"\n\n{ptx.ordinal(defenses)} defense of the reign." if defenses else ""
        return (f"\U0001F6E1️ FINAL — {holder} defends the belt\n\n"
                f"{holder} {holder_score}, {opponent} {opp_score}.{stakes}\n\n"
                f"{SITE_URL}/preview.html")
    if opp_score > holder_score:
        reign_number = ptx.team_reign_number(lineage, opponent) + 1
        return (f"\U0001F3C6 THE BELT HAS CHANGED HANDS\n\n"
                f"{opponent} defeats {holder} {opp_score}-{holder_score}.\n\n"
                f"{opponent} is the {ptx.ordinal(reign_number)} holder of the "
                f"College Football Belt.\n\n{SITE_URL}/preview.html")
    # An outright tie in modern FBS/FCS is essentially impossible (every
    # game goes to overtime), but this exists rather than silently
    # skipping the final post on the off chance of one.
    return (f"\U0001F6E1️ FINAL (TIE) — {holder} keeps the belt\n\n"
            f"{holder} {holder_score}, {opponent} {opp_score}. A tie doesn't "
            f"change hands.\n\n{SITE_URL}/preview.html")


# ----------------------------------------------------------------- loop

def run_once(client, next_game, lineage, rankings, cache, ptx):
    """One ESPN check: fetch, diff against the cache, post whatever's
    new. Returns True once FINAL has been posted -- the loop's signal to
    stop."""
    try:
        summary = fetch_espn_summary(next_game["id"])
    except (URLError, TimeoutError, OSError, ValueError) as e:
        print(f"ESPN fetch failed (not fatal, retrying next poll): {e}")
        return False

    holder, opponent = next_game["team"], next_game["opponent"]
    parsed = parse_competition(summary, holder, opponent)
    if not parsed:
        print("Couldn't parse ESPN's payload for this game -- skipping this poll.")
        return False
    state, is_half, holder_score, opp_score = parsed

    def post(text, label):
        try:
            response = client.create_tweet(text=text)
        except Exception as e:
            print(f"X {label} post FAILED (not fatal): {e}")
            return False
        print(f"Posted {label}: {response}\n{text}\n")
        return True

    if state == "in" and not cache["kickoff_posted"]:
        if post(compose_kickoff_tweet(next_game, lineage, rankings, ptx), "kickoff"):
            cache["kickoff_posted"] = True
            cache["last_holder_score"], cache["last_opp_score"] = holder_score, opp_score
            save_cache(cache)
            commit_cache("Live: kickoff posted")

    prev_h, prev_o = cache.get("last_holder_score"), cache.get("last_opp_score")
    if prev_h is None:
        # First time this script has seen a score for this game -- set
        # the baseline without posting (the kickoff post above already
        # covers 0-0, and this also fires if the job started late).
        cache["last_holder_score"], cache["last_opp_score"] = holder_score, opp_score
        save_cache(cache)
    elif (holder_score, opp_score) != (prev_h, prev_o):
        play = find_scoring_play(summary, holder, opponent)
        text = compose_score_tweet(next_game, holder, opponent, prev_h, prev_o,
                                    holder_score, opp_score, rankings, ptx, play)
        if post(text, "scoring update"):
            cache["last_holder_score"], cache["last_opp_score"] = holder_score, opp_score
            save_cache(cache)
            commit_cache("Live: scoring update posted")

    if is_half and not cache["halftime_posted"]:
        text = compose_halftime_tweet(next_game, holder, opponent, holder_score, opp_score, rankings, ptx)
        if post(text, "halftime"):
            cache["halftime_posted"] = True
            save_cache(cache)
            commit_cache("Live: halftime posted")

    if state == "post" and not cache["final_posted"]:
        text = compose_final_tweet(lineage, holder, opponent, holder_score, opp_score, ptx)
        if post(text, "final"):
            cache["final_posted"] = True
            save_cache(cache)
            commit_cache("Live: final posted")
        return True

    return False


def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"Skipping live X posts -- missing env var(s): {', '.join(missing)} (optional).")
        return

    next_game = load_json(NEXT_GAME_PATH)
    if not next_game:
        print("No upcoming game -- nothing live to check.")
        return

    now_utc = datetime.now(timezone.utc)
    if not in_live_window(next_game, now_utc):
        print("Not near the next belt game's kickoff -- skipping (no ESPN call).")
        return

    if not next_game.get("id"):
        print("next_game.json has no ESPN event id -- can't check live status.")
        return

    key = game_key(next_game)
    cache = load_cache()
    if cache.get("game_key") != key:
        cache = fresh_state(key)
        save_cache(cache)

    if cache.get("final_posted"):
        print("Already posted this game's final -- nothing left to check.")
        return

    try:
        import tweepy
    except ImportError:
        sys.exit("tweepy isn't installed -- add it to requirements.txt (pip install tweepy) and rerun.")

    import post_to_x as ptx  # reuse ranked() / ordinal() / team_reign_number() / tweet_length()

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_KEY_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )

    lineage = load_json(LINEAGE_PATH) or {"reigns": [], "belt_games": []}
    rankings = load_json(RANKINGS_PATH)

    # This one job stays alive and keeps polling for as long as the game
    # runs -- see the module docstring for why (GitHub's schedule trigger
    # can't fire more often than every 5 minutes, so real-time updates
    # mean looping inside a single run instead). live-game.yml's
    # `concurrency` block keeps a later 5-minute tick from starting a
    # second one of these while this loop is still going.
    deadline = now_utc + MAX_SESSION
    while True:
        finished = run_once(client, next_game, lineage, rankings, cache, ptx)
        if finished:
            print("Final posted -- this game is done.")
            break
        if datetime.now(timezone.utc) >= deadline:
            print("Hit this job's safety time limit without seeing FINAL "
                  "(unusually long game / multiple overtimes?) -- the next "
                  "scheduled tick will pick back up and keep polling.")
            break
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
