"""follow_batch: whose fault is an error?

Run: python3 test_follow_batch.py   (no network, no API key)

The distinction this file exists for: an error about OUR credentials, OUR
credits or OUR rate limit must stop the run and mark nobody, while an
error about the account being looked at may mark that one account failed
and move on. Getting it backwards is expensive and silent -- a handle
written into cache["failed"] is never retried.

On 2026-09-26 X started returning "402 Payment Required" because the API
credits had run out, and 402 was not in the stop list. Four accounts a
run, twelve runs a day, would have burned the entire 240-name target list
into permanent failures for a reason that had nothing to do with any of
them.
"""

import sys

import follow_batch as F

fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        fails.append(label)


print("1. our problem -- stop the run, blame nobody")
OURS = [
    "402 Payment Required",
    "Payment Required",
    "401 Unauthorized",
    "Could not authenticate you",
    "403 Forbidden",
    "429 Too Many Requests",
    "Rate limit exceeded",
    # tweepy wraps these, so the real shape is messier than the bare code
    "tweepy.errors.TwitterServerError: 402 Payment Required\nYour credits have run out",
    "HTTPError: 429 Client Error: Too Many Requests for url: https://api.x.com/2/users",
]
for msg in OURS:
    check(msg.split("\n")[0][:52], F._should_stop_run(Exception(msg)))

print("2. their problem -- mark that one and carry on")
THEIRS = [
    "user not found",
    "No user matches for specified terms",
    "404 Not Found",
    "This account is suspended",
    "Cannot follow a user who has blocked you",
]
for msg in THEIRS:
    check(msg[:52], not F._should_stop_run(Exception(msg)))

print("3. the specific regression")
check("402 is a stop, not a permanent failure",
      F._should_stop_run(Exception("402 Payment Required")))
check("and it was not before this fix",
      "402" in F.STOP_RUN_MARKERS or "Payment Required" in F.STOP_RUN_MARKERS)
check("the old name still resolves, for anything importing it",
      F._is_auth_error is F._should_stop_run)

print()
if fails:
    print(f"{len(fails)} FAILURE(S): " + ", ".join(fails))
    sys.exit(1)
print("All follow_batch checks passed.")
