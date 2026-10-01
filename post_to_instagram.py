#!/usr/bin/env python3
"""
Posts @CollegeFBBelt's two Instagram posts, automatically:

  * "Belt on the Line" -- Friday evening before each belt game (the workflow's
    6:10 PM ET Friday run, the same one that sends the X preview).
  * "Belt Defended" / "New Champion" -- a few minutes after the final whistle
    (live-game.yml runs this right after post_live_game.py), with the pipeline
    run as the backstop if that misses it.

Each is a 1080x1350 card in the design of the hand-made Sept 18-19, 2026 posts,
a caption in the same voice, the stadium as the location, and the two teams
and the TV network tagged on the photo. ig_cards.py builds the post; this file
only decides WHEN to post and talks to Instagram. See ig_cards.py for where
every number comes from.

Usage
-----
    python3 post_to_instagram.py                # pipeline stage (update_all.py)
    python3 post_to_instagram.py --after-live   # live-game.yml, every 5 min on game days
    python3 post_to_instagram.py --check        # token check + sample cards, posts nothing
    python3 post_to_instagram.py --preview-now  # post this week's preview right away
    IG_DRY_RUN=1 python3 post_to_instagram.py   # build and print, post nothing

Secrets (repo Settings > Secrets and variables > Actions)
--------------------------------------------------------
    IG_ACCESS_TOKEN         Meta app > Instagram API > "API setup with Instagram
                            login" > Generate access tokens (collegefbbelt row)
    IG_BUSINESS_ACCOUNT_ID  the number under collegefbbelt on that page
    ANTHROPIC_API_KEY       (already there) writes the captions; without it a
                            plain caption is used
    GH_SECRETS_PAT          OPTIONAL -- a fine-grained GitHub token with
                            "Secrets: read and write" on this repo, so the
                            60-day Instagram token can renew itself.
Without the two IG_* secrets this prints a note and exits 0, as before.

Why the old version never posted (fixed 2026-10-01)
----------------------------------------------------
The app uses "Instagram login", whose tokens (they start with IG) only work on
graph.instagram.com. The old script sent them to graph.facebook.com, which
answers "Invalid OAuth access token - Cannot parse access token" -- every post
from Sept 14 on failed that way (the pipeline treats IG as optional, so the run
stayed green). Meta's dashboard showed 0 calls ever to the publish permission.

How the image gets a public URL
-------------------------------
Instagram downloads the picture from a URL; it won't take an upload. The card
is committed to this (public) repo under social_cache/ig_images/ through the
GitHub contents API -- using the checkout's own token, without touching the
working tree mid-pipeline -- and handed to Instagram as a raw.githubusercontent
URL pinned to that commit.

social_cache/ig_last_posted.json remembers what went out (by ESPN game id and
by "holder|opponent|date" for previews), so nothing posts twice whichever of
the two workflows gets there first.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ig_cards as cards  # noqa: E402

BELT_DATA_DIR = os.path.join(HERE, "belt_data")
LINEAGE_PATH = os.path.join(BELT_DATA_DIR, "lineage.json")
NEXT_GAME_PATH = os.path.join(BELT_DATA_DIR, "next_game.json")
LIVE_STATE_PATH = os.path.join(HERE, "social_cache", "x_live_state.json")
CACHE_REL = "social_cache/ig_last_posted.json"
CACHE_PATH = os.path.join(HERE, CACHE_REL)
IMAGES_REL = "social_cache/ig_images"
CHECK_REL = "social_cache/ig_check"

REQUIRED_ENV = ["IG_ACCESS_TOKEN", "IG_BUSINESS_ACCOUNT_ID"]
FB_GRAPH = "https://graph.facebook.com/v25.0"
IG_GRAPH = "https://graph.instagram.com"

# a result older than this is stale news -- mark it done instead of posting it
RESULT_MAX_AGE_DAYS = 2


# ------------------------------------------------------------------ cache

def load_cache():
    c = cards.load_json(CACHE_PATH, {}) or {}
    c.setdefault("last_posted_game_id", None)
    c.setdefault("last_posted_preview_key", None)
    c.setdefault("posted_results", [])
    c.setdefault("posted_previews", [])
    return c


def cache_text(c):
    c["posted_results"] = [str(x) for x in c["posted_results"]][-40:]
    c["posted_previews"] = c["posted_previews"][-40:]
    return json.dumps(c, indent=2) + "\n"


def save_cache(c, message):
    text = cache_text(c)
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        f.write(text)
    if os.environ.get("IG_DRY_RUN") == "1":
        return
    # also straight to main, so the other workflow sees it within seconds
    # (identical content, so the pipeline's own later commit rebases cleanly)
    try:
        gh_put(CACHE_REL, text.encode(), message)
    except Exception as e:  # noqa: BLE001
        print(f"  (couldn't push the IG cache right away: {e} -- the workflow's own commit will carry it)")


# --------------------------------------------------------- GitHub storage

def gh_repo():
    return os.environ.get("GITHUB_REPOSITORY") or "rjr5021/college-football-belt"


def gh_token():
    """The token actions/checkout leaves in .git/config -- lets this push one
    file through the API without a workflow change. None outside Actions."""
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    try:
        out = subprocess.run(["git", "config", "--get-regexp", r"http\..*\.extraheader"],
                             cwd=HERE, capture_output=True, text=True).stdout
        m = re.search(r"AUTHORIZATION:\s*basic\s+(\S+)", out, re.I)
        if m:
            user_pass = base64.b64decode(m.group(1)).decode()
            return user_pass.split(":", 1)[1]
    except Exception:  # noqa: BLE001
        pass
    return None


def gh_api(method, path, body=None, token=None):
    token = token or gh_token()
    if not token:
        raise RuntimeError("no GitHub token (not running in Actions?)")
    req = urllib.request.Request(
        f"https://api.github.com{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "cfb-belt-ig"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 404 and method == "GET":
            return None
        raise RuntimeError(f"GitHub {method} {path}: {e.code} {e.read().decode()[:300]}")


def gh_put(rel_path, content, message):
    """Create/replace one file on main; returns the new commit sha."""
    repo = gh_repo()
    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    url_path = f"/repos/{repo}/contents/{urllib.parse.quote(rel_path)}"
    for attempt in range(4):
        cur = gh_api("GET", f"{url_path}?ref={branch}")
        body = {"message": f"{message} [skip ci]", "branch": branch,
                "content": base64.b64encode(content).decode()}
        if cur and cur.get("sha"):
            body["sha"] = cur["sha"]
        try:
            res = gh_api("PUT", url_path, body)
            return res["commit"]["sha"]
        except RuntimeError as e:
            if ("409" in str(e) or "422" in str(e)) and attempt < 3:   # someone pushed in between
                time.sleep(3)
                continue
            raise


def host_image(local_path, rel_dir, message):
    """Commit the card and return a public URL Instagram can fetch."""
    name = os.path.basename(local_path)
    rel = f"{rel_dir}/{name}"
    with open(local_path, "rb") as f:
        sha = gh_put(rel, f.read(), message)
    url = f"https://raw.githubusercontent.com/{gh_repo()}/{sha}/{rel}"
    for _ in range(12):      # raw.githubusercontent can lag a few seconds
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200:
                    return url
        except Exception:  # noqa: BLE001
            pass
        time.sleep(5)
    return url


# -------------------------------------------------------------- Instagram

def graph_base(token):
    return IG_GRAPH if token.startswith("IG") else FB_GRAPH


def graph(method, path, token, params=None):
    params = dict(params or {})
    params["access_token"] = token
    base = graph_base(token)
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(f"{base}{path}?{data.decode()}")
    else:
        req = urllib.request.Request(f"{base}{path}", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {})
        except Exception:  # noqa: BLE001
            err = {"message": str(e)}
        msg = err.get("error_user_msg") or err.get("message") or str(e)
        raise RuntimeError(f"{msg} (code {err.get('code')}, subcode {err.get('error_subcode')})")


def publish(token, ig_user, image_url, post):
    """Container -> wait until Instagram has fetched the image -> publish.
    If a tag or the location is what Instagram objects to, try again without
    it rather than lose the post."""
    base = {"image_url": image_url, "caption": post["caption"]}
    extras = {}
    if post.get("alt_text"):
        extras["alt_text"] = post["alt_text"][:990]
    if post.get("user_tags"):
        extras["user_tags"] = json.dumps(post["user_tags"])
    if post.get("location_id"):
        extras["location_id"] = post["location_id"]
    attempt_sets = [dict(extras)]
    for drop in ("location_id", "user_tags", "alt_text"):
        nxt = dict(attempt_sets[-1])
        if drop in nxt:
            nxt.pop(drop)
            attempt_sets.append(nxt)
    errors = []
    for extra in attempt_sets:
        try:
            cont = graph("POST", f"/{ig_user}/media", token, {**base, **extra})
            cid = cont["id"]
            for _ in range(30):
                st = graph("GET", f"/{cid}", token, {"fields": "status_code"}).get("status_code")
                if st == "FINISHED":
                    break
                if st == "ERROR":
                    raise RuntimeError("Instagram couldn't process the image")
                time.sleep(3)
            pub = graph("POST", f"/{ig_user}/media_publish", token, {"creation_id": cid})
            dropped = [k for k in extras if k not in extra]
            if dropped:
                print(f"  posted WITHOUT {', '.join(dropped)} (Instagram rejected them: {errors})")
                summary(f"Instagram post went out without {', '.join(dropped)}: {errors}")
            try:
                link = graph("GET", f"/{pub['id']}", token, {"fields": "permalink"}).get("permalink")
            except Exception:  # noqa: BLE001
                link = None
            return pub["id"], link
        except RuntimeError as e:
            errors.append(str(e))
            print(f"  publish attempt failed: {e}")
            if "code 190" in str(e):      # bad token -- no point retrying variants
                break
    raise RuntimeError("; ".join(errors))


def check_token(token, ig_user):
    me = graph("GET", "/me", token, {"fields": "user_id,username"})
    uid = str(me.get("user_id") or me.get("id"))
    print(f"Instagram token OK: posts as @{me.get('username')} (user_id {uid})")
    summary(f"Instagram token OK: posts as @{me.get('username')}")
    if ig_user and uid != str(ig_user):
        print(f"  !! IG_BUSINESS_ACCOUNT_ID is {ig_user}, but the token's account is {uid}.")
        summary(f"IG_BUSINESS_ACCOUNT_ID ({ig_user}) doesn't match the token's account ({uid}).")
    return me


def maybe_refresh_token(token, cache):
    """Instagram-login tokens last 60 days and can be renewed once they're a
    day old. Renew weekly; store the new one if GH_SECRETS_PAT allows it."""
    if not token.startswith("IG") or not os.environ.get("GH_SECRETS_PAT"):
        return token   # nowhere to keep a renewed token -- leave the saved one alone
    last = cache.get("token_refreshed")
    if last and (date.today() - date.fromisoformat(last)).days < 7:
        return token
    try:
        res = graph("GET", "/refresh_access_token", token, {"grant_type": "ig_refresh_token"})
    except RuntimeError as e:
        print(f"  (token refresh skipped: {e})")
        return token
    new = res.get("access_token") or token
    exp = date.today() + timedelta(seconds=int(res.get("expires_in") or 0))
    cache["token_refreshed"] = date.today().isoformat()
    cache["token_expires"] = exp.isoformat()
    if new != token:
        if store_secret("IG_ACCESS_TOKEN", new):
            print(f"  Instagram token renewed and saved (good until {exp}).")
        else:
            print(f"  !! Instagram renewed the token but there's no GH_SECRETS_PAT to save it; "
                  f"the saved one runs out around {exp}.")
            summary(f"Instagram token needs replacing before {exp} (add GH_SECRETS_PAT to automate this).")
    else:
        print(f"  Instagram token renewed in place (good until {exp}).")
    return new


def store_secret(name, value):
    pat = os.environ.get("GH_SECRETS_PAT")
    if not pat:
        return False
    try:
        try:
            from nacl import encoding, public
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pynacl"], check=True)
            from nacl import encoding, public
        repo = gh_repo()
        key = gh_api("GET", f"/repos/{repo}/actions/secrets/public-key", token=pat)
        box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
        enc = base64.b64encode(box.encrypt(value.encode())).decode()
        gh_api("PUT", f"/repos/{repo}/actions/secrets/{name}",
               {"encrypted_value": enc, "key_id": key["key_id"]}, token=pat)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  (couldn't save the renewed token: {e})")
        return False


def summary(line):
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a") as f:
            f.write(f"- {line}\n")


# ------------------------------------------------------------------- posting

def post(kind_label, built, token, ig_user, dry):
    print(f"\n--- {kind_label} ---")
    print(built["caption"])
    print(f"tags: {[t['username'] for t in built['user_tags']]}  location: {built['venue']} -> {built['location_id']}")
    if built["fit_problems"]:
        print(f"  !! layout: {built['fit_problems']}")
    if not built["location_id"] and built["venue"]:
        summary(f"No Instagram location ID for **{built['venue']}** -- add it to ig_data.json (posted without a location).")
    if dry:
        print(f"  (dry run -- not posted; image at {built['image']})")
        return True
    url = host_image(built["image"], IMAGES_REL, f"IG card: {os.path.basename(built['image'])}")
    print(f"  image: {url}")
    media_id, link = publish(token, ig_user, url, built)
    print(f"  POSTED {kind_label}: {link or media_id}")
    summary(f"Posted to Instagram: {kind_label} {link or media_id}")
    return True


def result_row_from_lineage(g):
    holder = g["holder"]
    home_pts, away_pts = (g.get("score") or "0-0").split("-", 1)
    h_pts = home_pts if g.get("home") == holder else away_pts
    o_pts = away_pts if g.get("home") == holder else home_pts
    return {"game_id": g["game_id"], "date": g["date"], "season": g.get("season") or cards.d(g["date"]).year,
            "holder": holder, "opponent": g["opponent"], "outcome": g.get("outcome"),
            "holder_score": h_pts, "opp_score": o_pts}


def pipeline_results(lineage, cache, token, ig_user, dry, workdir):
    """Backstop for results live-game.yml missed. Returns True if the cache
    changed without being saved yet."""
    games = lineage.get("belt_games", [])
    last = cache.get("last_posted_game_id")
    if last is None:
        cache["last_posted_game_id"] = games[-1]["game_id"] if games else None
        print("First run -- bootstrapped the result marker without posting history.")
        return True
    idx = next((i for i, g in enumerate(games) if str(g["game_id"]) == str(last)), None)
    new = games[idx + 1:] if idx is not None else []
    if not new:
        print("No new belt game results to post.")
    changed = False
    today = datetime.now(cards.ET).date()
    for g in new:
        gid = str(g["game_id"])
        if gid in cache["posted_results"]:
            cache["last_posted_game_id"] = g["game_id"]
            changed = True
            continue
        if (today - cards.d(g["date"])).days > RESULT_MAX_AGE_DAYS:
            print(f"Skipping stale result {gid} ({g['date']}) -- too old to post now.")
            cache["last_posted_game_id"] = g["game_id"]
            cache["posted_results"].append(gid)
            changed = True
            continue
        try:
            built = cards.build_result(lineage, result_row_from_lineage(g), workdir)
            post("result", built, token, ig_user, dry)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"Instagram result post FAILED for {gid} (not fatal; retried next run): {e}")
            summary(f"Instagram result post failed for game {gid}: {e}")
            break
        if dry:
            break
        cache["last_posted_game_id"] = g["game_id"]
        cache["posted_results"].append(gid)
        save_cache(cache, f"IG: result {gid} posted")
        changed = False
    return changed


def preview(lineage, cache, token, ig_user, dry, workdir, force=False):
    flag = os.environ.get("X_POST_PREVIEW", "").lower() in ("1", "true", "yes")
    if not (flag or force):
        print("Not the Friday preview run -- no preview post.")
        return
    nxt = cards.load_json(NEXT_GAME_PATH)
    if not nxt or not nxt.get("id"):
        print("No upcoming belt game -- nothing to preview.")
        return
    key = f"{nxt['team']}|{nxt['opponent']}|{nxt['date']}"
    if key in cache["posted_previews"] or cache.get("last_posted_preview_key") == key:
        print(f"Preview for {key} already posted.")
        return
    today = datetime.now(cards.ET).date()
    if (cards.d(nxt["date"]) - today).days > 6 or cards.d(nxt["date"]) < today:
        print(f"Next belt game {key} isn't this week -- no preview yet.")
        return
    try:
        built = cards.build_preview(lineage, nxt, workdir)
        post("preview", built, token, ig_user, dry)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"Instagram preview post FAILED (not fatal): {e}")
        summary(f"Instagram preview post failed: {e}")
        return
    if not dry:
        cache["last_posted_preview_key"] = key
        cache["posted_previews"].append(key)
        save_cache(cache, f"IG: preview {key} posted")


def after_live(lineage, cache, token, ig_user, dry, workdir):
    """Result post within minutes of the final, from live-game.yml. Makes no
    network call at all unless a belt game kicked off 2.4-10 hours ago."""
    cands = []
    nxt = cards.load_json(NEXT_GAME_PATH) or {}
    live = (cards.load_json(LIVE_STATE_PATH) or {}).get("game") or {}
    for g in (nxt, live):
        if g.get("id") and g.get("team") and g.get("opponent") and g.get("raw_date"):
            if str(g["id"]) not in [str(c["id"]) for c in cands]:
                cands.append(g)
    now = datetime.now(cards.ET)
    for g in cands:
        gid = str(g["id"])
        if gid in cache["posted_results"]:
            continue
        try:
            kick = datetime.fromisoformat(g["raw_date"].replace("Z", "+00:00"))
        except ValueError:
            continue
        hrs = (now - kick).total_seconds() / 3600
        if not (2.4 <= hrs <= 10):
            continue
        summ = cards.espn_summary(gid)
        st = cards.comp_of(summ)["status"]["type"]
        if not st.get("completed"):
            print(f"{g['team']} vs {g['opponent']}: not final yet ({st.get('state')}).")
            continue
        data = cards.ig_data()
        hs = cards.side(summ, g["team"], data)
        os_ = cards.side(summ, g["opponent"], data)
        row = {"game_id": g["id"], "date": kick.astimezone(cards.ET).date().isoformat(),
               "season": kick.year, "holder": g["team"], "opponent": g["opponent"],
               "holder_score": hs.get("score"), "opp_score": os_.get("score"), "outcome": None}
        idx = cards.game_index(lineage, gid)
        if idx is not None:          # pipeline already has it: use its row
            row = result_row_from_lineage(lineage["belt_games"][idx])
        try:
            built = cards.build_result(lineage, row, workdir, data)
            post("result", built, token, ig_user, dry)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"Instagram result post FAILED for {gid} (not fatal; retried next tick): {e}")
            summary(f"Instagram result post failed for game {gid}: {e}")
            continue
        if not dry:
            cache["posted_results"].append(gid)
            save_cache(cache, f"IG: result {gid} posted")


def check(lineage, token, ig_user, workdir):
    """Token check + both cards rendered from real data, committed to
    social_cache/ig_check/ for a look. Posts nothing."""
    if token:
        try:
            check_token(token, ig_user)
        except RuntimeError as e:
            print(f"!! Instagram token check FAILED: {e}")
            summary(f"Instagram token check failed: {e}")
    out = []
    nxt = cards.load_json(NEXT_GAME_PATH)
    if nxt and nxt.get("id"):
        try:
            out.append(("preview", cards.build_preview(lineage, nxt, workdir)))
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"preview sample failed: {e}")
    games = lineage.get("belt_games", [])
    if games:
        try:
            out.append(("result", cards.build_result(lineage, result_row_from_lineage(games[-1]), workdir)))
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"result sample failed: {e}")
    for kind, b in out:
        print(f"\n=== sample {kind} ===\n{b['caption']}\n"
              f"tags: {b['user_tags']}\nlocation: {b['venue']} -> {b['location_id']}\nlayout: {b['fit_problems'] or 'ok'}")
        if os.environ.get("IG_DRY_RUN") == "1":
            print(f"sample image: {b['image']}")
            continue
        try:
            ext = os.path.splitext(b["image"])[1]
            fixed = os.path.join(workdir, f"sample-{kind}{ext}")
            os.replace(b["image"], fixed)
            url = host_image(fixed, CHECK_REL, f"IG check: sample {kind}")
            txt = (f"{b['caption']}\n\n---\ntags: {b['user_tags']}\nlocation: {b['venue']} -> {b['location_id']}\n"
                   f"alt: {b['alt_text']}\nlayout: {b['fit_problems'] or 'ok'}\n")
            gh_put(f"{CHECK_REL}/sample-{kind}.txt", txt.encode(), f"IG check: sample {kind} caption")
            print(f"sample image: {url}")
            summary(f"Sample {kind}: {url}")
            if token:
                # A dry run of the real thing: Instagram builds the post (and
                # checks every tag and the location) but nothing is published;
                # an unpublished container just expires after a day.
                params = {"image_url": url, "caption": b["caption"], "alt_text": b["alt_text"][:990]}
                if b["user_tags"]:
                    params["user_tags"] = json.dumps(b["user_tags"])
                if b["location_id"]:
                    params["location_id"] = b["location_id"]
                try:
                    cid = graph("POST", f"/{ig_user}/media", token, params)["id"]
                    print(f"Instagram accepted the {kind} post with its tags and location (not published).")
                    summary(f"Instagram accepted the sample {kind} with tags + location (container {cid}, not published).")
                except RuntimeError as e:
                    print(f"!! Instagram rejected the sample {kind}: {e}")
                    summary(f"Instagram rejected the sample {kind}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"(couldn't publish the sample to the repo: {e})")


# ------------------------------------------------------------------- main

def main():
    args = set(sys.argv[1:])
    dry = os.environ.get("IG_DRY_RUN") == "1"
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing and not dry:
        print(f"Skipping Instagram posts -- missing env var(s): {', '.join(missing)} "
              f"(optional; the site itself doesn't depend on this).")
        return
    lineage = cards.load_json(LINEAGE_PATH)
    if not lineage:
        print(f"Skipping Instagram posts -- {LINEAGE_PATH} doesn't exist yet.")
        return
    token = (os.environ.get("IG_ACCESS_TOKEN") or "").strip()
    ig_user = (os.environ.get("IG_BUSINESS_ACCOUNT_ID") or "").strip()
    cache = load_cache()
    workdir = tempfile.mkdtemp(prefix="igcards-")

    if "--check" in args:
        check(lineage, token, ig_user, workdir)
        return
    if token and not dry:
        before = json.dumps(cache, sort_keys=True)
        token = maybe_refresh_token(token, cache)
        if json.dumps(cache, sort_keys=True) != before:
            save_cache(cache, "IG: token renewal date")

    if "--after-live" in args:
        after_live(lineage, cache, token, ig_user, dry, workdir)
        return
    if "--preview-now" in args:
        preview(lineage, cache, token, ig_user, dry, workdir, force=True)
        return

    if pipeline_results(lineage, cache, token, ig_user, dry, workdir) and not dry:
        save_cache(cache, "IG: result marker")
    preview(lineage, cache, token, ig_user, dry, workdir)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 -- never take the site build down with it
        import traceback
        traceback.print_exc()
        print(f"Instagram step failed (not fatal to the pipeline): {e}")
