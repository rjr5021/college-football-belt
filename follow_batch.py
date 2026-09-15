#!/usr/bin/env python3
"""
Slowly builds up who @CollegeFBBelt follows on X, a small batch at a time.

Reads follow_targets.json (a curated list of CFB media/analyst accounts
plus all current FBS team accounts), follows a handful of them that aren't
already recorded as done, and records progress in
social_cache/x_follow_progress.json so re-runs never re-attempt an account
already followed (or one that permanently failed, e.g. a bad/suspended
handle).

Why this is a SEPARATE script from post_to_x.py, on its OWN schedule
---------------------------------------------------------------------
post_to_x.py runs on the game-driven schedule (a handful of times a week,
timed around kickoffs). Following ~150+ accounts needs many more, much
smaller, much more evenly-spaced runs than that -- see follow-accounts.yml,
which runs this roughly every 2 hours around the clock. Mixing the two
into one schedule would mean either following accounts in big bursts
(this got the account rate-limited once already, via manual/browser-based
following -- see README.md) or barely following anyone at all between game
weekends. A dedicated frequent-but-tiny schedule avoids both.

Why a small batch, not "follow everyone in one run"
-----------------------------------------------------
X's API and abuse-detection systems rate-limit and flag accounts that
follow too many people too quickly, especially newer accounts (see
README.md's rate-limit note from the initial manual follow attempt, which
got rate-limited by X's UI after ~15-19 follows in a few minutes). This
follows only BATCH_SIZE accounts per run and sleeps briefly between each,
so at BATCH_SIZE=4 and a 2-hour cadence that's roughly 48/day -- comfortably
paced, with the full ~150-account list worked through in a few days rather
than all at once.

Usage:
  export X_API_KEY=...
  export X_API_KEY_SECRET=...
  export X_ACCESS_TOKEN=...
  export X_ACCESS_TOKEN_SECRET=...
  python3 follow_batch.py

OPTIONAL, same pattern as post_to_x.py: if any of the four X_* env vars
aren't set, this prints a note and exits 0 (success) rather than failing
the pipeline.

Handling "already following"
-----------------------------
X's follow endpoint is idempotent -- following someone you already follow
still returns a normal success response (following: true), it just doesn't
change anything. So this never needs to reconcile against who was already
followed manually/via browser automation before this script existed; it
just follows everyone in follow_targets.json that its OWN cache doesn't
yet show as done, and a handful of harmless no-op API calls for accounts
followed earlier by hand is a fine trade for keeping the logic simple.

Handling auth failures separately from "this account doesn't exist"
----------------------------------------------------------------------
A lookup or follow call can fail for two very different reasons: the
account genuinely doesn't exist (or is suspended), which is permanent and
safe to record as "failed" -- or the X_* credentials themselves are being
rejected (401 Unauthorized / "Could not authenticate you"), which has
nothing to do with this specific account and will be true for every
account until the credentials are fixed. Treating the second case like
the first would permanently blacklist real, valid accounts just because
they happened to be up next when the credentials broke. So an auth-looking
error stops the run early instead -- nobody in that batch gets marked
failed, and the whole batch is retried automatically on the next scheduled
run once the credentials work again.
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS_PATH = os.path.join(HERE, "follow_targets.json")
CACHE_DIR = os.path.join(HERE, "social_cache")
CACHE_PATH = os.path.join(CACHE_DIR, "x_follow_progress.json")

REQUIRED_ENV = ["X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]

# How many NEW follows to attempt in a single run. Kept deliberately small
# -- see the module docstring for why. Tune by editing this constant (and/or
# follow-accounts.yml's cron cadence) if X's actual rate limits turn out to
# allow more, or if you want the full list finished sooner.
BATCH_SIZE = 4

SLEEP_BETWEEN = 3  # seconds between consecutive follow attempts, politeness

# Substrings that mean "the credentials themselves were rejected", not
# "this particular account is bad". Kept as a list (rather than one big
# check) so it's easy to extend if X's error wording ever changes.
AUTH_ERROR_MARKERS = ["401", "Unauthorized", "Could not authenticate"]


def _is_auth_error(exc):
    msg = str(exc)
    return any(marker in msg for marker in AUTH_ERROR_MARKERS)


def load_targets():
    if not os.path.exists(TARGETS_PATH):
        return []
    with open(TARGETS_PATH) as f:
        return json.load(f)["targets"]


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return {"followed": [], "failed": {}}
    with open(CACHE_PATH) as f:
        data = json.load(f)
    data.setdefault("followed", [])
    data.setdefault("failed", {})
    return data


def save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"Skipping follow batch -- missing env var(s): {', '.join(missing)} "
              f"(optional; the site itself doesn't depend on this).")
        return

    targets = load_targets()
    if not targets:
        print(f"Skipping follow batch -- {TARGETS_PATH} is missing or empty.")
        return

    try:
        import tweepy
    except ImportError:
        sys.exit("tweepy isn't installed -- add it to requirements.txt "
                  "(pip install tweepy) and rerun.")

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_KEY_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )

    cache = load_cache()
    done = set(h.lower() for h in cache["followed"]) | set(h.lower() for h in cache["failed"])

    pending = [t for t in targets if t["handle"].lower() not in done]
    if not pending:
        print(f"All {len(targets)} accounts in {TARGETS_PATH} have already "
              f"been followed (or permanently failed) -- nothing to do. Add "
              f"more accounts to follow_targets.json to keep growing.")
        return

    batch = pending[:BATCH_SIZE]
    print(f"{len(cache['followed'])} followed so far, {len(pending)} still "
          f"pending -- attempting {len(batch)} this run.")

    for target in batch:
        handle, label = target["handle"], target.get("label", target["handle"])

        try:
            user = client.get_user(username=handle)
        except Exception as e:
            if _is_auth_error(e):
                print(f"  {handle} ({label}): couldn't look up user -- {e}")
                print("  X API credentials are being rejected -- this isn't about "
                      "this account. Stopping this run early without marking anyone "
                      "permanently failed; the whole batch will retry once the "
                      "credentials work again.")
                break
            print(f"  {handle} ({label}): couldn't look up user -- {e}")
            cache["failed"][handle] = f"lookup failed: {e}"
            save_cache(cache)
            time.sleep(SLEEP_BETWEEN)
            continue

        if not user.data:
            print(f"  {handle} ({label}): no such user -- marking permanently failed.")
            cache["failed"][handle] = "user not found"
            save_cache(cache)
            time.sleep(SLEEP_BETWEEN)
            continue

        try:
            client.follow_user(user.data.id)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "Too Many Requests" in msg or "rate limit" in msg.lower():
                print(f"  {handle} ({label}): rate limited -- stopping this "
                      f"run early, will resume next scheduled run. {e}")
                break
            if _is_auth_error(e):
                print(f"  {handle} ({label}): couldn't follow -- {e}")
                print("  X API credentials are being rejected -- this isn't about "
                      "this account. Stopping this run early without marking anyone "
                      "permanently failed; the whole batch will retry once the "
                      "credentials work again.")
                break
            print(f"  {handle} ({label}): follow failed -- {e}")
            cache["failed"][handle] = f"follow failed: {e}"
            save_cache(cache)
            time.sleep(SLEEP_BETWEEN)
            continue

        print(f"  Followed {handle} ({label}).")
        cache["followed"].append(handle)
        save_cache(cache)
        time.sleep(SLEEP_BETWEEN)

    remaining = len(targets) - len(cache["followed"]) - len(cache["failed"])
    print(f"Done. {len(cache['followed'])} followed total, "
          f"{len(cache['failed'])} permanently failed, {remaining} still pending.")


if __name__ == "__main__":
    main()
