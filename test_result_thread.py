"""The Saturday-night handoff between the two scripts that post a final.

Run: python3 test_result_thread.py   (no network, no API key)

post_live_game.py posts the final within minutes of the whistle;
post_to_x.py posts the result an hour or two later, once the pipeline has
ingested it. They used to both go out standalone, saying the same thing.
Now the slower one replies to the faster one -- when, and only when, it
can prove the two are about the same game.

The failure that matters is not "no reply" (that is the normal fallback
for a game with no live coverage) but a reply hung off the WRONG post, so
most of this is about refusing to match.
"""

import json
import os
import sys
import tempfile

import post_live_game as live
import post_to_x as ptx

fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        fails.append(label)


GAME = {"holder": "Notre Dame", "opponent": "Purdue", "date": "2026-09-26",
        "new_holder": "Notre Dame", "game_id": 401858467}
NEXT = {"team": "Notre Dame", "opponent": "Purdue", "date": "2026-09-26",
        "id": 401858467}

# The two scripts build the key from different objects -- next_game.json
# there, a lineage row here -- so the first thing to pin down is that they
# agree on what it looks like.
print("1. the two scripts agree on the game key")
check("live's key matches the one the result post rebuilds",
      live.game_key(NEXT) == f'{GAME["holder"]}|{GAME["opponent"]}|{GAME["date"]}',
      live.game_key(NEXT))

print("2. matching state -> a reply")
tmp = os.path.join(tempfile.gettempdir(), "test_live_state.json")
real = ptx.LIVE_STATE_PATH
ptx.LIVE_STATE_PATH = tmp


def write_state(**kw):
    state = {"game_key": live.game_key(NEXT), "final_tweet_id": "1972000000000000001"}
    state.update(kw)
    with open(tmp, "w") as f:
        json.dump(state, f)


write_state()
check("returns the live final's id", ptx.live_final_tweet_id(GAME) == "1972000000000000001",
      str(ptx.live_final_tweet_id(GAME)))

print("3. every reason to refuse")
write_state(game_key="Notre Dame|Michigan State|2026-09-19")
check("a different game", ptx.live_final_tweet_id(GAME) is None)

write_state(final_tweet_id=None)
check("live posted but recorded no id", ptx.live_final_tweet_id(GAME) is None)

write_state(final_tweet_id="")
check("empty id", ptx.live_final_tweet_id(GAME) is None)

os.remove(tmp)
check("no state file at all (a game with no live coverage)",
      ptx.live_final_tweet_id(GAME) is None)

write_state()
check("a lineage row with no holder (the belt's very first game)",
      ptx.live_final_tweet_id({"holder": None, "new_holder": None,
                               "opponent": "Princeton", "date": "1869-11-06"}) is None)
check("a row for the right teams on the wrong date",
      ptx.live_final_tweet_id(dict(GAME, date="2026-10-03")) is None)
os.remove(tmp)
ptx.LIVE_STATE_PATH = real

# 4. the link the final post carries
print("4. the final post links the game, not the fixture list")
with_id = live.final_link(NEXT)
check("uses this game's own page", "/games/401858467.html" in with_id, with_id)
check("keeps its campaign tag", "utm_campaign=x-live-final" in with_id, with_id)
check("falls back when there is no id", "/preview.html" in live.final_link({}),
      live.final_link({}))
check("and when there is no game at all", "/preview.html" in live.final_link(None),
      live.final_link(None))

# 5. the composer still produces all three outcomes, now with that link
print("5. all three final outcomes carry it")
LINEAGE = {"reigns": [{"team": "Notre Dame", "start_date": "2025-11-29",
                       "end_date": None, "defenses": 3}],
           "belt_games": []}
for label, hs, os_ in (("defended", 31, 17), ("changed hands", 21, 24), ("tie", 24, 24)):
    text = live.compose_final_tweet(LINEAGE, "Notre Dame", "Purdue", hs, os_, ptx, NEXT)
    check(f"{label}: links the game page", "/games/401858467.html" in text, text)
    check(f"{label}: fits X's limit", ptx.tweet_length(text) <= ptx.TWEET_MAX,
          str(ptx.tweet_length(text)))

print()
if fails:
    print(f"{len(fails)} FAILURE(S): " + ", ".join(fails))
    sys.exit(1)
print("All result-threading checks passed.")
