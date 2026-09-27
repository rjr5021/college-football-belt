"""Tests for post_live_game.py (2026-09-19).

1. in_live_window: TBD kickoff never opens the window; well before/after
   kickoff is closed; the pregame-to-postgame span is open.
2. belt_status_line: leading and tied both read SAFE; only trailing
   reads IN DANGER (Bob: "I only want it to say belt in danger if the
   holder is losing").
3. parse_competition / find_scoring_play against a synthetic ESPN
   summary payload shaped like the real one (header.competitions[0] +
   top-level scoringPlays).
4. compose_* wording, and that every composed tweet fits X's 280
   (ptx.tweet_length <= ptx.TWEET_MAX) even with a long play description.
5. run_once end to end against a fake ESPN fetch (monkeypatched) and a
   fake X client (records posts, never actually calls X) walking a whole
   game: pre-kickoff (nothing), kickoff (0-0), a Notre Dame touchdown
   (7-0), halftime (still 7-0), a Michigan State touchdown that ties it
   at halftime... actually kept separate below -- then final. Confirms
   each of the four things posts exactly once, in order, with the belt
   status wording matching the score at that moment, and that calling
   run_once again after final does nothing further (idempotent).

Needs no belt_data/ and no network -- everything is synthetic, and
fetch_espn_summary / tweepy.Client are both replaced with fakes.

Run from this folder: python3 test_live_game.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import post_to_x as ptx  # noqa: E402
import post_live_game as p  # noqa: E402

FAILURES = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        FAILURES.append(name)


def espn_payload(state, holder_score, opp_score, half=False, scoring_play=None):
    """A minimal stand-in for ESPN's summary?event= response, carrying
    only the fields parse_competition / find_scoring_play actually read."""
    type_name = "STATUS_HALFTIME" if half else {
        "pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS", "post": "STATUS_FINAL",
    }[state]
    payload = {
        "header": {
            "competitions": [{
                "status": {"type": {"state": state, "name": type_name,
                                     "shortDetail": "Halftime" if half else ""}},
                "competitors": [
                    {"team": {"location": "Notre Dame", "displayName": "Notre Dame Fighting Irish",
                              "abbreviation": "ND"}, "score": str(holder_score)},
                    {"team": {"location": "Michigan State", "displayName": "Michigan State Spartans",
                              "abbreviation": "MSU"}, "score": str(opp_score)},
                ],
            }],
        },
        "scoringPlays": [],
    }
    if scoring_play:
        payload["scoringPlays"].append(scoring_play)
    return payload


NEXT_GAME = {
    "id": "401858453", "team": "Notre Dame", "opponent": "Michigan State",
    "is_home": True, "neutral": False, "date": "2026-09-19",
    "raw_date": "2026-09-19T23:30:00Z", "start_time_tbd": False,
}
LINEAGE = {
    "current_holder": "Notre Dame",
    "reigns": [
        {"team": "Rutgers", "start_date": "1869-11-06", "defenses": 0, "end_date": "1869-11-13"},
        {"team": "Notre Dame", "start_date": "2025-11-29", "won_from": "Stanford",
         "defenses": 2, "end_date": None, "lost_to": None},
    ],
    "belt_games": [],
}


def test_in_live_window():
    kickoff = datetime(2026, 9, 19, 23, 30, tzinfo=timezone.utc)
    tbd = dict(NEXT_GAME, start_time_tbd=True)
    check("TBD kickoff never opens the window",
          not p.in_live_window(tbd, kickoff))
    check("closed well before kickoff",
          not p.in_live_window(NEXT_GAME, kickoff - timedelta(hours=2)))
    check("open just before kickoff",
          p.in_live_window(NEXT_GAME, kickoff - timedelta(minutes=10)))
    check("open mid-game",
          p.in_live_window(NEXT_GAME, kickoff + timedelta(hours=2)))
    check("closed long after MAX_SESSION",
          not p.in_live_window(NEXT_GAME, kickoff + timedelta(hours=8)))


def test_belt_status_line():
    check("leading is SAFE", p.belt_status_line(14, 7) == "Belt SAFE, for now.")
    check("tied is NOT in danger", "IN DANGER" not in p.belt_status_line(7, 7))
    check("trailing IS in danger", p.belt_status_line(7, 14) == "Belt IN DANGER.")


def test_parse_and_scoring_play():
    play = {"type": {"text": "Passing Touchdown"},
            "team": {"location": "Notre Dame", "displayName": "Notre Dame Fighting Irish"},
            "text": "Jordan Faison 25 Yd pass from CJ Carr (Mitchell Evans Kick)"}
    payload = espn_payload("in", 7, 0, scoring_play=play)
    parsed = p.parse_competition(payload, "Notre Dame", "Michigan State")
    check("parse_competition reads state/scores", parsed == ("in", False, 7, 0))
    found = p.find_scoring_play(payload, "Notre Dame", "Michigan State")
    check("find_scoring_play attributes the play to the holder", found["is_holder"] is True)
    check("find_scoring_play labels it a touchdown", found["label"] == "touchdown")
    check("find_scoring_play keeps ESPN's own text verbatim",
          found["text"] == "Jordan Faison 25 Yd pass from CJ Carr (Mitchell Evans Kick)")

    no_plays = espn_payload("in", 7, 0)
    check("find_scoring_play is None with no scoringPlays",
          p.find_scoring_play(no_plays, "Notre Dame", "Michigan State") is None)

    half_payload = espn_payload("in", 7, 0, half=True)
    parsed_half = p.parse_competition(half_payload, "Notre Dame", "Michigan State")
    check("parse_competition detects halftime", parsed_half[1] is True)


def test_compose_fits_and_reads_right():
    rankings = {"current": {"ap": {"Notre Dame": 3, "Michigan State": 22}}}

    kickoff_text = p.compose_kickoff_tweet(NEXT_GAME, LINEAGE, rankings, ptx)
    check("kickoff post mentions KICKOFF", "KICKOFF" in kickoff_text)
    check("kickoff post fits 280", ptx.tweet_length(kickoff_text) <= ptx.TWEET_MAX)

    play = {"is_holder": True, "label": "touchdown",
            "text": "Jordan Faison 25 Yd pass from CJ Carr (Mitchell Evans Kick)"}
    score_text = p.compose_score_tweet(NEXT_GAME, "Notre Dame", "Michigan State", 0, 0,
                                        7, 0, rankings, ptx, play)
    check("scoring post includes the real play text",
          "Jordan Faison 25 Yd pass" in score_text)
    check("scoring post reads SAFE while leading", "SAFE" in score_text)
    check("scoring post fits 280", ptx.tweet_length(score_text) <= ptx.TWEET_MAX)

    trailing_text = p.compose_score_tweet(NEXT_GAME, "Notre Dame", "Michigan State", 7, 0,
                                           7, 14, rankings, ptx, None)
    check("scoring post reads IN DANGER while trailing", "IN DANGER" in trailing_text)

    # A wall-of-text play description should still fit -- falls back to
    # the version without the play line.
    huge_play = {"is_holder": True, "label": "touchdown", "text": "X " * 200}
    huge_text = p.compose_score_tweet(NEXT_GAME, "Notre Dame", "Michigan State", 0, 0,
                                       7, 0, rankings, ptx, huge_play)
    check("an oversized play description gets dropped, not truncated mid-word",
          "X " * 200 not in huge_text and ptx.tweet_length(huge_text) <= ptx.TWEET_MAX)

    half_text = p.compose_halftime_tweet(NEXT_GAME, "Notre Dame", "Michigan State", 14, 10, rankings, ptx)
    check("halftime post says HALFTIME", "HALFTIME" in half_text)
    check("halftime post fits 280", ptx.tweet_length(half_text) <= ptx.TWEET_MAX)

    defend_text = p.compose_final_tweet(LINEAGE, "Notre Dame", "Michigan State", 27, 20, ptx)
    check("a win reads as a defense", "defends the belt" in defend_text)
    change_text = p.compose_final_tweet(LINEAGE, "Notre Dame", "Michigan State", 20, 24, ptx)
    check("a loss reads as a belt change", "CHANGED HANDS" in change_text)
    check("final posts fit 280",
          ptx.tweet_length(defend_text) <= ptx.TWEET_MAX and ptx.tweet_length(change_text) <= ptx.TWEET_MAX)


class FakeClient:
    def __init__(self):
        self.posts = []

    def create_tweet(self, text, **kwargs):
        self.posts.append(text)
        return {"id": len(self.posts)}


def test_run_once_end_to_end(monkeypatch=None):
    # No monkeypatch fixture here (no pytest) -- swap the module-level
    # fetch function by hand and restore it after.
    original_fetch = p.fetch_espn_summary
    original_commit = p.commit_cache
    p.commit_cache = lambda message: None  # no real git in this test env

    sequence = [
        espn_payload("pre", 0, 0),
        espn_payload("in", 0, 0),
        espn_payload("in", 7, 0, scoring_play={
            "type": {"text": "Rushing Touchdown"},
            "team": {"location": "Notre Dame", "displayName": "Notre Dame Fighting Irish"},
            "text": "CJ Carr 3 Yd Run (Mitchell Evans Kick)"}),
        espn_payload("in", 7, 0, half=True),
        espn_payload("post", 27, 20),
        espn_payload("post", 27, 20),  # a rerun after final -- must be a no-op
    ]
    it = iter(sequence)
    p.fetch_espn_summary = lambda event_id: next(it)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            p.CACHE_DIR = tmp
            p.CACHE_PATH = os.path.join(tmp, "x_live_state.json")

            client = FakeClient()
            cache = p.fresh_state(p.game_key(NEXT_GAME), NEXT_GAME)
            rankings = None

            finished = p.run_once(client, NEXT_GAME, LINEAGE, rankings, cache, ptx)
            check("pregame poll posts nothing", client.posts == [] and not finished)

            p.run_once(client, NEXT_GAME, LINEAGE, rankings, cache, ptx)
            check("kickoff posts exactly once", len(client.posts) == 1 and "KICKOFF" in client.posts[0])

            p.run_once(client, NEXT_GAME, LINEAGE, rankings, cache, ptx)
            check("the holder going 7-0 up leaves the belt exactly as safe as "
                  "it was, so nothing is posted",
                  len(client.posts) == 1)

            p.run_once(client, NEXT_GAME, LINEAGE, rankings, cache, ptx)
            check("halftime posts once, no duplicate scoring post for an unchanged score",
                  len(client.posts) == 2 and "HALFTIME" in client.posts[1])

            # 7-0 to 27-20: the holder led before and leads after. Since
            # 2026-09-26 that is noted in the cache and NOT posted -- nine
            # such posts in one game are what pushed the final and the
            # result post over X's daily allowance.
            finished = p.run_once(client, NEXT_GAME, LINEAGE, rankings, cache, ptx)
            check("a score that doesn't change the belt's position posts nothing",
                  len(client.posts) == 3 and finished)
            check("but the final still goes out", "defends the belt" in client.posts[2])
            check("and the cache kept the real score anyway",
                  (cache["last_holder_score"], cache["last_opp_score"]) == (27, 20))

            p.run_once(client, NEXT_GAME, LINEAGE, rankings, cache, ptx)
            check("a rerun after final is idempotent -- exits immediately with no new posts",
                  len(client.posts) == 3)
    finally:
        p.fetch_espn_summary = original_fetch
        p.commit_cache = original_commit


if __name__ == "__main__":
    test_in_live_window()
    test_belt_status_line()
    test_parse_and_scoring_play()
    test_compose_fits_and_reads_right()
    test_run_once_end_to_end()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


# ---------------------------------------------------------------------------
# What Saturday 2026-09-26 taught us. Notre Dame beat Purdue 49-10: nine
# scoring posts went out, then the final post and the pipeline's result post
# both failed, and because next_game.json had already advanced the final
# could never be retried. These pin down both halves of the fix.
# ---------------------------------------------------------------------------

import post_live_game as _plg
from datetime import datetime as _dt, timezone as _tz

_fails = []


def _check(label, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        _fails.append(label)


print("\nbelt_at_risk: the same rule the post text prints")
_check("holder ahead -- safe", _plg.belt_at_risk(49, 10) is False)
_check("holder trailing -- at risk", _plg.belt_at_risk(10, 49) is True)
_check("tied is safe, because a tie doesn't move the belt",
       _plg.belt_at_risk(14, 14) is False)
_check("a fresh game opens safe", _plg.belt_at_risk(None, None) is False)
_check("it agrees with belt_status_line, which is the point",
       ("IN DANGER" in _plg.belt_status_line(10, 49)) is _plg.belt_at_risk(10, 49)
       and ("IN DANGER" in _plg.belt_status_line(14, 14)) is _plg.belt_at_risk(14, 14))

print("\nthe Purdue game, scoring drive by scoring drive")
# the real shape of a 49-10 win: the holder scores first and never trails
DRIVES = [(7, 0), (7, 3), (14, 3), (21, 3), (28, 3), (28, 10), (35, 10), (42, 10), (49, 10)]
posts, prev = 0, (None, None)
for now in DRIVES:
    if _plg.belt_at_risk(*now) != _plg.belt_at_risk(*prev):
        posts += 1
    prev = now
_check(f"nine scoring plays in a blowout now produce {posts} posts, not 9",
       posts == 0, str(posts))
print("       (kickoff, halftime and the final still go out -- three posts, not twelve)")

print("\na game that actually swings still gets covered")
SWING = [(0, 7), (7, 7), (7, 14), (14, 14), (21, 14), (21, 21), (21, 24)]
posts, prev = 0, (None, None)
for now in SWING:
    if _plg.belt_at_risk(*now) != _plg.belt_at_risk(*prev):
        posts += 1
    prev = now
_check(f"a see-saw game still posts every time the belt flips ({posts})",
       posts == 5, str(posts))

print("\nthe final can still be posted after next_game.json moves on")
PLAYED = {"id": 401858467, "team": "Notre Dame", "opponent": "Purdue",
          "date": "2026-09-26", "raw_date": "2026-09-26T18:00:00.000Z"}
MOVED_ON = {"id": 401858999, "team": "Notre Dame", "opponent": "Boise State",
            "date": "2026-10-03", "raw_date": "2026-10-03T18:00:00.000Z"}
DURING = _dt(2026, 9, 26, 21, 20, tzinfo=_tz.utc)   # ten minutes after the last score

stranded = _plg.fresh_state(_plg.game_key(PLAYED), PLAYED)
stranded.update(kickoff_posted=True, final_posted=False)
_check("the unfinished game wins over the one the pipeline moved to",
       _plg.unfinished_game(stranded, MOVED_ON, DURING)["id"] == PLAYED["id"])

done = dict(stranded, final_posted=True)
_check("once its final is posted, the next game takes over",
       _plg.unfinished_game(done, MOVED_ON, DURING)["id"] == MOVED_ON["id"])

never_started = _plg.fresh_state(_plg.game_key(PLAYED), PLAYED)
_check("a game that never kicked off doesn't hold things up",
       _plg.unfinished_game(never_started, MOVED_ON, DURING)["id"] == MOVED_ON["id"])

LATE = _dt(2026, 9, 27, 12, 0, tzinfo=_tz.utc)      # long past its window
_check("and it is abandoned once its own window closes",
       _plg.unfinished_game(stranded, MOVED_ON, LATE)["id"] == MOVED_ON["id"])

_check("the cache carries what finishing the game needs",
       all(stranded["game"].get(k) for k in ("id", "team", "opponent", "raw_date")))

if _fails:
    print(f"\n{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    raise SystemExit(1)
print("\nAll 2026-09-26 regression checks passed.")
