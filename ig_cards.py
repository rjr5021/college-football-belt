#!/usr/bin/env python3
"""
ig_cards.py -- builds the two Instagram posts for @CollegeFBBelt:

  * "Belt on the Line"  -- the Friday-evening preview of the holder's next game
  * "Belt Defended" / "New Champion" -- the result, a few minutes after the final

Each post is a 1080x1350 image in the same design as the two hand-made posts
of Sept 18-19, 2026 (ND vs. Michigan State), a caption in the same voice, and
the tags Bob adds by hand: the stadium as the location, and the two team
accounts plus the TV network tagged on the photo.

Bob, 2026-10-01: "I want them to look exactly like the belt defended and belt
on the line posts I've already done, tagging the stadium as the location and
tagging the teams and broadcaster in the post as well."

Where the pieces come from
--------------------------
  * belt facts (holder, reign number, days held, defenses, belt game No.,
    the challenger's history) -- belt_data/lineage.json, the same file the
    site is built from, so the card can never disagree with the site.
  * the game itself (kickoff, TV, stadium, final score, box score, leaders,
    the next game) -- ESPN's public scoreboard/summary JSON, the same source
    post_live_game.py already polls during games.
  * the three preview numbers -- belt_data/belt_risk.json (defend %),
    ai_preview_cache/ (DraftKings line and "The Lean").
  * the caption and the italic line at the bottom of the card -- written by
    Claude (ANTHROPIC_API_KEY, already a repo secret for the AI preview) from
    those facts only, with Bob's two hand-written captions as the style guide.
    If the API is down, a plain fill-in-the-blanks caption is used instead.
  * team / network Instagram handles, stadium location IDs, a few overrides
    -- ig_data.json (hand-checked; anything missing is simply left off).

Rendering is a real browser (Playwright + Chromium) over ig_templates/, as the
original posts were made; it is installed on demand the first time a card is
rendered in a run, so the rest of the pipeline never pays for it.
"""

import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = timezone(timedelta(hours=-4))

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "ig_templates")
DATA_PATH = os.path.join(HERE, "ig_data.json")
TEAM_COLORS_PATH = os.path.join(HERE, "belt_data", "team_colors.json")
AI_CACHE_PATH = os.path.join(HERE, "ai_preview_cache", "cache.json")
AI_LEDGER_PATH = os.path.join(HERE, "ai_preview_cache", "ledger.json")
AI_PREVIEW_PATH = os.path.join(HERE, "belt_data", "ai_preview.json")
BELT_RISK_PATH = os.path.join(HERE, "belt_data", "belt_risk.json")

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CAPTION_MODELS = ["claude-sonnet-4-5", "claude-haiku-4-5-20251001"]

W, H = 1080, 1350


# ------------------------------------------------------------------ basics

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def get_json(url, tries=3):
    # No custom headers on purpose: ESPN's edge answers 403 to a custom or a
    # fake-browser User-Agent from GitHub's runners but 200 to plain urllib --
    # the same lesson as post_live_game.py's fetch_espn_summary (2026-09-19).
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"couldn't fetch {url}: {last}")


def ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def d(iso):
    return date.fromisoformat(str(iso)[:10])


def esc(s):
    return html.escape(str(s), quote=True)


def ig_data():
    return load_json(DATA_PATH, {}) or {}


def espn_dt(s):
    """ESPN dates look like 2026-09-19T23:30Z."""
    s = (s or "").replace("Z", "+00:00")
    if re.match(r".*T\d\d:\d\d\+", s):
        s = s.replace("+", ":00+", 1)
    return datetime.fromisoformat(s)


def clock(dt_utc):
    """7:30 PM ET style (no :00 for on-the-hour, as on the hand-made card's
    'Next · at Purdue, 2 PM')."""
    t = dt_utc.astimezone(ET)
    h = t.strftime("%I").lstrip("0")
    m = t.strftime("%M")
    ap = t.strftime("%p")
    return f"{h} {ap}" if m == "00" else f"{h}:{m} {ap}"


def day_part(dt_utc):
    t = dt_utc.astimezone(ET)
    wd = t.strftime("%A")
    if t.hour < 12:
        return f"{wd} morning"
    if t.hour < 16:
        return f"{wd} afternoon"
    if t.hour < 18:
        return f"{wd} evening"
    return f"{wd} night"


def short_date(dd):
    return f"{dd:%a} {dd:%b} {dd.day}"          # Sat Sep 19


def tile_date(dd):
    return f"{dd:%b} {dd.day}"                    # Sep 26


# ----------------------------------------------------------------- colors

def _rgb(hexs):
    h = (hexs or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        return None
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(rgb, other, t):
    return tuple(c + (o - c) * t for c, o in zip(rgb, other))


def panel_colors(primary, accent_candidates):
    """Panel background (made dark enough for white type, the way the
    hand-made cards read), its darker gradient stop, and an accent that
    reads on it (the gold of ND's '27')."""
    base = _rgb(primary) or (40, 40, 40)
    white = (255, 255, 255)
    steps = 0
    while contrast(base, white) < 3.4 and steps < 20:
        base = _mix(base, (0, 0, 0), 0.12)
        steps += 1
    dark = _mix(base, (0, 0, 0), 0.42)
    accent = None
    for cand in accent_candidates:
        rgb = _rgb(cand)
        if rgb and _lum(rgb) > _lum(base) and contrast(rgb, base) >= 3.2 and contrast(rgb, white) >= 1.25:
            accent = rgb
            break
    if accent is None:
        accent = (217, 178, 92)   # the site's bright brass
    return _hex(base), _hex(dark), _hex(accent)


# ------------------------------------------------------------- belt facts

def reigns_of(lineage, team):
    return [r for r in lineage.get("reigns", []) if r.get("team") == team]


def reign_on(lineage, team, on):
    """The reign `team` held going into date `on` (latest start <= on whose
    end is open or >= on)."""
    best = None
    for r in reigns_of(lineage, team):
        s = d(r["start_date"])
        e = d(r["end_date"]) if r.get("end_date") else None
        if s <= on and (e is None or e >= on):
            if best is None or s >= d(best["start_date"]):
                best = r
    return best


def history(lineage, team, before, today=None):
    """Reign count, total days and last year held, all before `before`."""
    today = today or before
    n, days, last = 0, 0, None
    for r in reigns_of(lineage, team):
        s = d(r["start_date"])
        if s >= before:
            continue
        e = d(r["end_date"]) if r.get("end_date") else today
        e = min(e, today)
        n += 1
        days += max(0, (e - s).days)
        y = (d(r["end_date"]) if r.get("end_date") else today).year
        last = y if last is None else max(last, y)
    return n, days, last


def game_index(lineage, game_id):
    for i, g in enumerate(lineage.get("belt_games", [])):
        if str(g.get("game_id")) == str(game_id):
            return i
    return None


def meetings(lineage, a, b, before):
    out = []
    for g in lineage.get("belt_games", []):
        if {g.get("holder"), g.get("opponent")} == {a, b} and d(g["date"]) < before:
            out.append(g)
    return out


# ---------------------------------------------------------------- ESPN

def espn_summary(event_id):
    return get_json(f"{ESPN}/summary?event={event_id}")


def espn_schedule(team_id, season):
    return get_json(f"{ESPN}/teams/{team_id}/schedule?season={season}")


def comp_of(summary):
    return summary["header"]["competitions"][0]


def side(summary, lineage_name, data):
    """The ESPN competitor dict for a lineage team name."""
    want = fold(lineage_name)
    alias = fold((data.get("espn_names") or {}).get(lineage_name, ""))
    comps = comp_of(summary)["competitors"]
    for c in comps:
        t = c["team"]
        names = {fold(t.get(k, "")) for k in ("location", "displayName", "shortDisplayName", "nickname", "abbreviation")}
        if want in names or (alias and alias in names):
            return c
    for c in comps:  # looser: "Miami" vs "Miami Hurricanes"
        if fold(c["team"].get("displayName", "")).startswith(want):
            return c
    raise RuntimeError(f"couldn't find {lineage_name} in ESPN event")


def fold(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("&", "and"))


def broadcasts(comp):
    out = []
    for b in comp.get("broadcasts") or []:
        n = (b.get("media") or {}).get("shortName") or ""
        if not n:
            for nm in b.get("names") or []:
                out.append(nm)
            continue
        out.append(n)
    seen, res = set(), []
    for n in out:
        if n and n not in seen:
            seen.add(n)
            res.append(n)
    return res


def team_stat(summary, team_id, name):
    for t in (summary.get("boxscore") or {}).get("teams") or []:
        if str(t["team"]["id"]) == str(team_id):
            for s in t.get("statistics") or []:
                if s.get("name") == name:
                    return s.get("displayValue")
    return None


def leaders(summary, team_id):
    out = {}
    for blk in summary.get("leaders") or []:
        if str(blk["team"]["id"]) != str(team_id):
            continue
        for cat in blk.get("leaders") or []:
            if cat.get("leaders"):
                ld = cat["leaders"][0]
                out[cat["name"]] = {"name": ld["athlete"].get("displayName", ""),
                                    "line": ld.get("displayValue", "")}
    return out


def yds(line):
    m = re.search(r"(-?\d+)\s*YDS", line or "")
    return int(m.group(1)) if m else None


def last_name(full):
    parts = [p for p in (full or "").split() if p]
    while len(parts) > 1 and parts[-1].rstrip(".").lower() in ("jr", "sr", "ii", "iii", "iv", "v"):
        parts.pop()
    return parts[-1] if parts else full


def standout(lds):
    """The tile-2 stat: a 100-yard receiver or rusher if there is one (the
    bigger of the two), otherwise the passer."""
    best = None
    for key, label in (("receivingYards", "rec. yards"), ("rushingYards", "rush yards")):
        if key in lds:
            y = yds(lds[key]["line"])
            if y is not None and y >= 100 and (best is None or y > best[0]):
                best = (y, lds[key]["name"], label)
    if best is None and "passingYards" in lds:
        y = yds(lds["passingYards"]["line"])
        if y is not None:
            best = (y, lds["passingYards"]["name"], "pass yards")
    if best is None:
        for key, label in (("rushingYards", "rush yards"), ("receivingYards", "rec. yards")):
            if key in lds and yds(lds[key]["line"]) is not None:
                best = (yds(lds[key]["line"]), lds[key]["name"], label)
                break
    return best


def game_flow(summary, winner_id):
    """Halftime score and whether the winner ever trailed -- the kind of
    detail Bob's recap leads with ('17-10 at the break, never trailed')."""
    comp = comp_of(summary)
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
    def half(c):
        ls = c.get("linescores") or []
        try:
            return sum(int(float(x.get("displayValue") or x.get("value") or 0)) for x in ls[:2])
        except ValueError:
            return None
    w_home = str(home["team"]["id"]) == str(winner_id)
    trailed, lead_changes, prev_sign = False, 0, 0
    for p in summary.get("scoringPlays") or []:
        hs, as_ = p.get("homeScore"), p.get("awayScore")
        if hs is None or as_ is None:
            continue
        margin = (hs - as_) if w_home else (as_ - hs)
        if margin < 0:
            trailed = True
        sign = (margin > 0) - (margin < 0)
        if sign and prev_sign and sign != prev_sign:
            lead_changes += 1
        if sign:
            prev_sign = sign
    return {
        "halftime": {home["team"]["abbreviation"]: half(home), away["team"]["abbreviation"]: half(away)},
        "winner_ever_trailed": trailed,
        "lead_changes": lead_changes,
        "quarters": {c["team"]["abbreviation"]: [x.get("displayValue") for x in (c.get("linescores") or [])]
                     for c in (home, away)},
    }


def next_game_after(team_id, season, after_date):
    """The team's first game after `after_date` from ESPN's schedule, or None."""
    try:
        sch = espn_schedule(team_id, season)
    except Exception as e:  # noqa: BLE001
        print(f"  (no schedule for team {team_id}: {e})")
        return None
    for ev in sch.get("events") or []:
        try:
            when = espn_dt(ev["date"])
        except Exception:  # noqa: BLE001
            continue
        if when.astimezone(ET).date() <= after_date:
            continue
        c = ev["competitions"][0]
        me = next((x for x in c["competitors"] if str(x["team"]["id"]) == str(team_id)), None)
        opp = next((x for x in c["competitors"] if str(x["team"]["id"]) != str(team_id)), None)
        if not me or not opp:
            continue
        return {
            "date": when.astimezone(ET).date(),
            "dt": when,
            "time_valid": c.get("timeValid", ev.get("timeValid", True)) is not False,
            "home": me.get("homeAway") == "home",
            "neutral": bool(c.get("neutralSite")),
            "opp": opp["team"].get("location") or opp["team"].get("displayName"),
            "opp_abbr": opp["team"].get("abbreviation", ""),
            "venue": (c.get("venue") or {}).get("fullName", ""),
            "tv": broadcasts(c),
        }
    return None


def home_city(team_name, team_id, season, data):
    """Where the belt 'stays' / 'moves to' -- the program's home city,
    overridable in ig_data.json (ESPN calls Notre Dame Stadium's city
    'Notre Dame'; everyone else calls it South Bend)."""
    o = (data.get("cities") or {}).get(team_name)
    if o:
        return o
    try:
        sch = espn_schedule(team_id, season)
        for ev in sch.get("events") or []:
            c = ev["competitions"][0]
            me = next((x for x in c["competitors"] if str(x["team"]["id"]) == str(team_id)), None)
            if me and me.get("homeAway") == "home" and not c.get("neutralSite"):
                city = ((c.get("venue") or {}).get("address") or {}).get("city")
                if city:
                    return city
    except Exception:  # noqa: BLE001
        pass
    return None


# ------------------------------------------------------- names and handles

def team_label(team_name, comp_team, data, max_chars):
    """A short way to say the team in a tile label ('Irish to defend')."""
    o = (data.get("short_names") or {}).get(team_name)
    cands = [o] if o else []
    cands += [comp_team.get("name"), team_name, comp_team.get("abbreviation")]
    for c in cands:
        if c and len(c) <= max_chars:
            return c
    return comp_team.get("abbreviation") or team_name


def opp_short(name, abbr, max_chars=9):
    return name if len(name) <= max_chars else (abbr or name)


def two_lines(name):
    """'Notre Dame' -> 'Notre<br>Dame' as on the preview card; one word stays one line."""
    words = name.split()
    if len(words) < 2:
        return esc(name)
    best, cut = None, 1
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = max(len(a), len(b))
        if best is None or score < best:
            best, cut = score, i
    return esc(" ".join(words[:cut])) + "<br>" + esc(" ".join(words[cut:]))


def handle_for_team(team_name, data):
    th = data.get("team_handles") or {}
    h = th.get(team_name)
    if not h:
        want = fold(team_name)
        h = next((v for k, v in th.items() if fold(k) == want), None)
    return (h or "").lstrip("@") or None


def handle_for_network(networks, data):
    nh = data.get("network_handles") or {}
    for n in networks:
        h = nh.get(n) or nh.get(n.upper())
        if h:
            return h.lstrip("@")
    return None


def location_for(venue, data):
    v = (data.get("locations") or {}).get(venue)
    if isinstance(v, dict):
        v = v.get("id")
    return str(v) if v else None


def player_handle(name, data):
    return ((data.get("player_handles") or {}).get(name) or "").lstrip("@") or None


def tv_label(networks):
    return " + ".join(networks[:2])


# ---------------------------------------------------------------- render

BELT_SVG = """<svg class="belt" viewBox="0 0 260 150" fill="none">
  <defs>
    <linearGradient id="brassG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d9b25c"/><stop offset=".55" stop-color="#a97f38"/><stop offset="1" stop-color="#7d5a25"/></linearGradient>
    <linearGradient id="strapG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3a2d1c"/><stop offset="1" stop-color="#1a130b"/></linearGradient>
    <filter id="sh" x="-20%" y="-20%" width="140%" height="160%"><feDropShadow dx="0" dy="10" stdDeviation="9" flood-color="#000" flood-opacity=".45"/></filter>
  </defs>
  <g filter="url(#sh)">
    <rect x="0" y="57" width="260" height="36" rx="4" fill="url(#strapG)"/>
    <rect x="0" y="57" width="260" height="3" fill="#a97f38" opacity=".6"/>
    <rect x="0" y="90" width="260" height="3" fill="#a97f38" opacity=".6"/>
    <rect x="18" y="46" width="42" height="58" rx="5" fill="url(#brassG)" stroke="#211a12" stroke-width="2.5"/>
    <rect x="200" y="46" width="42" height="58" rx="5" fill="url(#brassG)" stroke="#211a12" stroke-width="2.5"/>
    <circle cx="39" cy="75" r="9" fill="#211a12" opacity=".85"/>
    <circle cx="221" cy="75" r="9" fill="#211a12" opacity=".85"/>
    <path d="M130 8 L182 30 L182 120 L130 142 L78 120 L78 30 Z" fill="url(#brassG)" stroke="#211a12" stroke-width="3"/>
    <path d="M130 22 L170 39 L170 111 L130 128 L90 111 L90 39 Z" fill="none" stroke="#211a12" stroke-width="2" opacity=".55"/>
  </g>
  <text x="130" y="97" text-anchor="middle" font-family="Big Shoulders Display" font-weight="900" font-size="{fs}" fill="#211a12" letter-spacing="1">{txt}</text>
</svg>"""

HEADER = """<div class="hdr">
  <div class="brand">
    <svg width="52" height="34" viewBox="0 0 34 22" fill="none"><rect x="0" y="8" width="34" height="6" rx="1" fill="#211a12"/><rect x="3" y="6" width="6" height="10" rx="1" fill="#a97f38"/><rect x="25" y="6" width="6" height="10" rx="1" fill="#a97f38"/><path d="M17 0 L24 4 L24 18 L17 22 L10 18 L10 4 Z" fill="#a97f38" stroke="#211a12" stroke-width="1.5"/><circle cx="17" cy="11" r="3.5" fill="#211a12"/></svg>
    <div class="disp wm">The College Football Belt</div>
  </div>
  <div class="mono est">Est. 1869 · Lineal title</div>
</div>
<div class="rule"></div>"""

FOOT = """<div class="mono foot"><span>@CollegeFBBelt</span><span class="url">collegefootballbelt.com</span></div>
<div class="grain"></div>"""

# Shrinks any line that would otherwise run past its box (a long stadium or
# school name), one pixel at a time, the way you'd nudge it by hand.
FIT_JS = """() => {
  const fit = (el, min, box) => {
    let fs = parseFloat(getComputedStyle(el).fontSize);
    const over = () => el.scrollWidth > el.clientWidth + 1 || (box && el.scrollHeight > el.clientHeight + 1);
    while (over() && fs > min) { fs -= 1; el.style.fontSize = fs + 'px'; }
    return over();
  };
  const bad = [];
  const rules = [['.h1', 90], ['.when', 15], ['.kicker', 14], ['.role', 13], ['.team', 36], ['.stat', 13],
                 ['.tile .v', 44], ['.tile .l', 12], ['.beltcap', 12], ['.score', 100]];
  for (const [sel, min] of rules) for (const el of document.querySelectorAll(sel)) if (fit(el, min, false)) bad.push(sel);
  for (const el of document.querySelectorAll('.note')) if (fit(el, 18, true)) bad.push('.note');
  return bad;
}"""


def belt_svg(text):
    fs = 62 if len(text) <= 2 else (48 if len(text) == 3 else (38 if len(text) == 4 else 32))
    return BELT_SVG.replace("{fs}", str(fs)).replace("{txt}", esc(text))


def chip(img_path, abbr):
    if img_path:
        return f'<div class="chip"><img src="file://{esc(img_path)}" alt=""></div>'
    return f'<div class="chip"><span class="disp abbr">{esc(abbr)}</span></div>'


def page(body, classes, colors):
    css_vars = ";".join(f"--{k}:{v}" for k, v in colors.items())
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<link rel="stylesheet" href="card.css"><style>:root{{{css_vars}}}</style></head>'
            f'<body><div class="canvas {classes}">{body}</div></body></html>')


_PW_READY = False


def ensure_playwright():
    global _PW_READY
    if _PW_READY:
        return
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Installing Playwright for the Instagram cards (once per run)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "playwright"], check=True)
    exe = os.environ.get("IG_CHROMIUM_PATH")
    if not exe:
        args = [sys.executable, "-m", "playwright", "install", "chromium"]
        if os.environ.get("GITHUB_ACTIONS") == "true":
            args.insert(4, "--with-deps")
        subprocess.run(args, check=True)
    _PW_READY = True


def render(html_text, out_jpg):
    """HTML -> 1080x1350 JPEG (Instagram only takes JPEG). Rendered at 2x and
    downsampled, like the originals. Returns the list of anything that still
    didn't fit after shrinking (empty is good)."""
    ensure_playwright()
    from playwright.sync_api import sync_playwright
    from PIL import Image
    src = os.path.join(TEMPLATE_DIR, "_card.html")
    with open(src, "w") as f:
        f.write(html_text)
    big = out_jpg + ".2x.png"
    try:
        with sync_playwright() as p:
            kw = {}
            if os.environ.get("IG_CHROMIUM_PATH"):
                kw["executable_path"] = os.environ["IG_CHROMIUM_PATH"]
            b = p.chromium.launch(**kw)
            pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
            pg.goto("file://" + src)
            pg.evaluate("document.fonts.ready.then(() => true)")
            pg.wait_for_timeout(400)
            bad = pg.evaluate(FIT_JS)
            fonts = pg.evaluate("() => Array.from(document.fonts).filter(f => f.status === 'loaded').length")
            pg.screenshot(path=big, clip={"x": 0, "y": 0, "width": W, "height": H})
            b.close()
        im = Image.open(big).convert("RGB").resize((W, H), Image.LANCZOS)
        im.save(out_jpg, "JPEG", quality=92, optimize=True, progressive=True)
        if fonts < 4:
            bad = list(bad) + [f"only {fonts} fonts loaded"]
        return list(bad)
    finally:
        for pth in (src, big):
            try:
                os.remove(pth)
            except OSError:
                pass


def fetch_logo(team_id, workdir):
    if not team_id:
        return None
    url = f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png"
    path = os.path.join(workdir, f"logo-{team_id}.png")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=25) as r, open(path, "wb") as f:
            f.write(r.read())
        return path
    except Exception as e:  # noqa: BLE001
        print(f"  (no logo for team {team_id}: {e} -- using its initials)")
        return None


# ------------------------------------------------------------- captions

STYLE_RESULT = """The belt stays in South Bend. 🏆

Notre Dame 27, Michigan State 10. Third defense of the Irish's 9th reign, now 294 days and counting since they took it from Stanford on Nov. 29.

The Spartans hung around for a half — 17–10 at the break — but the Irish never trailed and never blinked. CJ Carr went 18 of 24 for 234, Micah Gilbert caught 8 balls for 170 (61 of them on the very first snap), Nolan James Jr. ground out 121 and a score, and the defense picked Michigan State off twice and held them to 167 total yards.

Belt game No. 1,638 is in the books. Michigan State's wait since 2018 continues.

Next up: the Irish carry the belt to West Lafayette. At Purdue, Sat Sep 26, 2 PM ET on Peacock. Beat the holder, take the belt.

🔗 Box score, drive chart and the full chain of custody since 1869 → link in bio

#CollegeFootballBelt #NotreDame #GoIrish #MichiganState #CollegeFootball #CFB #FightingIrish #Spartans #NDvsMSU #Purdue"""

STYLE_PREVIEW = """293 days. 2 defenses. One belt. Michigan State gets its shot Saturday night. 🏆

Notre Dame has held the College Football Belt since taking it from Stanford on Nov. 29 — the program's 9th reign since 1869. Michigan State hasn't touched it since 2018, and these two have never met with the belt on the line. Belt game No. 1,638 fixes that.

The numbers say Irish: 97% to defend, −29.5 at DraftKings, and the lean is Notre Dame 42–17.

The belt doesn't read numbers. No committee, no poll — you have to take it from whoever's holding it.

🏈 Sat · 7:30 PM ET · NBC + Peacock · Notre Dame Stadium
🔗 Full preview, head-to-head history and 157 years of chain of custody → link in bio

#CollegeFootballBelt #NotreDame #GoIrish #MichiganState #GoGreen #CollegeFootball #CFB #NDvsMSU #FightingIrish #Spartans"""

STYLE_NOTES = {
    "preview": ("First belt game ever between these two.",
                "Notre Dame took the belt from Stanford on Nov. 29 and has held it 293 days. The Spartans haven't touched it since 2018."),
    "result": ("The Irish never trailed.",
               "CJ Carr went 18 of 24 for 234, Nolan James Jr. ran for 121 and a score, and the defense picked off Michigan State twice and held the Spartans to 167 yards."),
}


def claude_caption(kind, facts):
    """Caption + the card's italic note, written by Claude from `facts`
    only. Returns None on any failure (the caller has a plain fallback)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    example = STYLE_RESULT if kind == "result" else STYLE_PREVIEW
    nb, nr = STYLE_NOTES[kind]
    what = ("the result of the belt game that just ended" if kind == "result"
            else "a preview of the upcoming belt game")
    prompt = f"""You write the Instagram captions for @CollegeFBBelt, the account for the lineal College Football Belt (whoever beats the holder takes the belt; it has been passed on since 1869).

Write {what}, in exactly the voice, length and structure of this caption the account's owner wrote by hand:

<example>
{example}
</example>

and the short italic line printed at the bottom of the image, which for that post was:
<example_note><b>{nb}</b> {nr}</example_note>

Facts for this post (use ONLY these -- never add a stat, record, streak, quote or storyline that isn't here; if a detail in the example has no matching fact, leave that idea out):
<facts>
{json.dumps(facts, indent=1, ensure_ascii=False, default=str)}
</facts>

Rules:
- Same shape as the example: a punchy first line ending in 🏆, then short paragraphs, then the {"'Next up' paragraph" if kind == "result" else "'🏈 day · time · TV · stadium' line"}, then the 🔗 '→ link in bio' line, then one line of 8-11 hashtags.
- Hashtags: start with #CollegeFootballBelt; include both schools, #CollegeFootball and #CFB, the two nicknames, and an ABBRvsABBR tag; well-known fan tags like #GoIrish only if you're sure they're real.
- No @mentions (tags go on the photo), no links, no exclamation-mark hype. Use the en dash in scores (27–10) and the minus sign in lines (−20.5), like the example.
- Under 1,600 characters.
- The note: a bold first sentence of at most 45 characters, then at most 120 more characters. Together they must fit two lines of a card.

Reply with JSON only: {{"caption": "...", "note_bold": "...", "note_rest": "..."}}"""
    body = {"max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}
    for model in CAPTION_MODELS:
        body["model"] = model
        try:
            req = urllib.request.Request(
                ANTHROPIC_URL, data=json.dumps(body).encode(),
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                out = json.loads(r.read().decode())
            text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
            m = re.search(r"\{.*\}", text, re.S)
            res = json.loads(m.group(0))
            cap = (res.get("caption") or "").strip()
            if not cap or len(cap) > 2200 or "#CollegeFootballBelt" not in cap or cap.count("#") > 30:
                raise ValueError("caption failed checks")
            for must in facts.get("must_mention", []):
                if str(must) not in cap:
                    raise ValueError(f"caption is missing {must!r}")
            nb2 = (res.get("note_bold") or "").strip()
            nr2 = (res.get("note_rest") or "").strip()
            if len(nb2) > 60 or len(nb2) + len(nr2) > 190:
                nb2, nr2 = "", ""
            print(f"  caption written by {model}")
            return {"caption": cap, "note_bold": nb2, "note_rest": nr2}
        except Exception as e:  # noqa: BLE001
            print(f"  caption via {model} failed ({e})")
    return None


def hashtags(h_name, h_abbr, h_mascot, o_name, o_abbr, o_mascot, extra=()):
    tags = ["CollegeFootballBelt", h_name, o_name, "CollegeFootball", "CFB", h_mascot, o_mascot,
            f"{h_abbr}vs{o_abbr}", *extra]
    out, seen = [], set()
    for t in tags:
        t = re.sub(r"[^A-Za-z0-9]", "", t or "")
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append("#" + t)
    return " ".join(out)


# ------------------------------------------------------------ the posts

def build_preview(lineage, next_game, workdir, today=None, data=None):
    """'Belt on the Line' for next_game.json's game. Returns a dict with
    image path, caption, tags, location -- or raises."""
    data = data if data is not None else ig_data()
    today = today or datetime.now(ET).date()
    holder, opp = next_game["team"], next_game["opponent"]
    summ = espn_summary(next_game["id"])
    comp = comp_of(summ)
    hs, os_ = side(summ, holder, data), side(summ, opp, data)
    ht, ot = hs["team"], os_["team"]
    kick = espn_dt(comp["date"])
    gday = kick.astimezone(ET).date()
    venue = ((summ.get("gameInfo") or {}).get("venue") or {}).get("fullName") or next_game.get("venue_name") or ""
    nets = broadcasts(comp) or [x for x in (next_game.get("tv"), next_game.get("stream")) if x]
    tbd = bool(next_game.get("start_time_tbd"))

    reign = reign_on(lineage, holder, today) or (lineage.get("reigns") or [{}])[-1]
    start = d(reign["start_date"])
    days_held = (today - start).days
    defenses = int(reign.get("defenses") or 0)
    reign_no = sum(1 for r in reigns_of(lineage, holder) if d(r["start_date"]) <= start)
    game_no = len(lineage.get("belt_games", [])) + (0 if game_index(lineage, next_game["id"]) is not None else 1)
    o_n, o_days, o_last = history(lineage, opp, gday, today)
    met = meetings(lineage, holder, opp, gday)

    risk = load_json(BELT_RISK_PATH) or {}
    rng = (risk.get("next_game") or {}) if isinstance(risk, dict) else {}
    if rng.get("opponent") not in (None, opp):
        rng = {}
    pct = rng.get("defend_prob")
    src = "Elo" if (rng.get("source") or "").startswith("elo") else "CFBD"

    cache = load_json(AI_CACHE_PATH) or {}
    ai = None
    if cache.get("key") == f"{holder}|{opp}|{next_game['date']}":
        ai = cache.get("preview")
    ai = ai or load_json(AI_PREVIEW_PATH)
    line = None
    for row in load_json(AI_LEDGER_PATH, []) or []:
        if row.get("key") == f"{holder}|{opp}|{next_game['date']}" and row.get("line"):
            line = row["line"]
    lean = None
    if ai and ai.get("predicted_score"):
        nums = re.findall(r"\d+", ai["predicted_score"])
        if len(nums) == 2:
            a, b = sorted((int(x) for x in nums), reverse=True)
            lean = (f"{a}–{b}", ai.get("predicted_winner") or "")

    short = team_label(holder, ht, data, 9)
    tiles = []
    if pct is not None:
        tiles.append((f'{round(pct * 100)}<small>%</small>', f"{short} to defend · {src}"))
    if line and line.get("holder_spread") is not None:
        sp = float(line["holder_spread"])
        v = "PK" if sp == 0 else (f"−{abs(sp):g}" if sp < 0 else f"+{sp:g}")
        tiles.append((v, f"Line · {line.get('provider') or 'Vegas'}"))
    if lean:
        tiles.append((lean[0], f"The Lean · {team_label(lean[1], ht if lean[1] == holder else ot, data, 9)}"))
    fill = [(f"{days_held:,}", "Days held"), (str(defenses), "Defenses this reign"),
            (f"{o_n}", f"{opp_short(opp, ot.get('abbreviation'))} reigns all-time")]
    for t in fill:
        if len(tiles) >= 3:
            break
        tiles.append(t)

    when_bits = [f"<b>{esc(short_date(gday))}</b>",
                 f"<b>{'TBA' if tbd else esc(clock(kick) + ' ET')}</b>"]
    if nets:
        when_bits.append(esc(tv_label(nets)))
    if venue:
        when_bits.append(esc(venue))
    when = '<span class="sep">·</span>'.join(when_bits)

    role_r = (f"Challenger · last held {o_last}" if o_last else "Challenger · never held it")
    stat_r = (f"<b>{o_n}</b> {'reign' if o_n == 1 else 'reigns'} · <b>{o_days:,}</b> days all-time" if o_n
              else "<b>0</b> reigns · first title shot" if not met else f"<b>0</b> reigns · <b>{len(met)}</b> belt shots")

    facts = {
        "belt_game_number": f"{game_no:,}",
        "holder": holder, "holder_nickname": ht.get("name"), "challenger": opp, "challenger_nickname": ot.get("name"),
        "holder_reign_number": ordinal(reign_no), "holder_took_belt_from": reign.get("won_from"),
        "holder_took_belt_on": f"{start:%b}. {start.day}, {start.year}".replace("May.", "May"),
        "days_held_as_of_post": days_held, "defenses_so_far_this_reign": defenses,
        "challenger_reigns_all_time": o_n, "challenger_days_all_time": o_days,
        "challenger_last_held_year": o_last,
        "previous_belt_meetings_between_them": [
            {"date": g["date"], "holder": g["holder"], "opponent": g["opponent"], "score_home_away": g["score"],
             "home": g["home"], "outcome": g["outcome"]} for g in met[-5:]],
        "number_of_previous_belt_meetings": len(met),
        "kickoff": "TBA" if tbd else f"{kick.astimezone(ET):%a} {clock(kick)} ET",
        "day_part": day_part(kick), "tv": tv_label(nets), "stadium": venue,
        "holder_is_home": bool(next_game.get("is_home")), "neutral_site": bool(next_game.get("neutral")),
        "city": f"{next_game.get('venue_city', '')}, {next_game.get('venue_state', '')}".strip(", "),
        "defend_probability": f"{round(pct * 100)}%" if pct is not None else None,
        "defend_probability_source": src if pct is not None else None,
        "betting_line": (f"{holder} {'−' if (line or {}).get('holder_spread', 0) < 0 else '+'}"
                         f"{abs(line['holder_spread']):g} at {line.get('provider')}") if line and line.get("holder_spread") is not None else None,
        "over_under": (line or {}).get("over_under"),
        "the_lean_prediction": ai.get("predicted_score") if ai else None,
        "site_preview_overview": ai.get("overview") if ai else None,
        "site_preview_angles": ai.get("betting_angles") if ai else None,
        "years_of_belt_history": today.year - 1869,
        "hashtag_suggestion": hashtags(holder, ht.get("abbreviation"), ht.get("name"), opp, ot.get("abbreviation"), ot.get("name")),
        "must_mention": [],
    }
    cap = claude_caption("preview", facts)
    if not cap:
        cap = {
            "caption": (f"{days_held:,} days. {defenses} {'defense' if defenses == 1 else 'defenses'}. One belt. "
                        f"{opp} gets its shot {day_part(kick)}. 🏆\n\n"
                        f"{holder} has held the College Football Belt since taking it from {reign.get('won_from')} on "
                        f"{start:%b} {start.day} — the program's {ordinal(reign_no)} reign since 1869. "
                        + (f"{opp} hasn't held it since {o_last}." if o_last else f"{opp} has never held it.")
                        + f" Belt game No. {game_no:,}.\n\n"
                        + "The belt doesn't read numbers. No committee, no poll — you have to take it from whoever's holding it.\n\n"
                        f"🏈 {kick.astimezone(ET):%a} · {'TBA' if tbd else clock(kick) + ' ET'} · {tv_label(nets)} · {venue}\n"
                        "🔗 Full preview, head-to-head history and the chain of custody since 1869 → link in bio\n\n"
                        + facts["hashtag_suggestion"]),
            "note_bold": "", "note_rest": ""}
    note_b = cap.get("note_bold") or (f"{len(met)} belt meetings before this one." if met else "First belt game ever between these two.")
    note_r = cap.get("note_rest") or (f"{holder} took the belt from {reign.get('won_from')} on {start:%b}. {start.day} and has held it {days_held:,} days.")

    colors_l = panel_colors(colors_for(holder, ht), accents_for(holder, ht))
    colors_r = panel_colors(colors_for(opp, ot), [])
    logo_h = fetch_logo(ht.get("id"), workdir)
    logo_o = fetch_logo(ot.get("id"), workdir)
    body = f"""{HEADER}
<div class="mono kicker"><span class="dot"></span>Belt game No. {game_no:,} · {esc(day_part(kick))}</div>
<div class="disp h1">Belt on <span class="thin">the line</span></div>
<div class="mono when">{when}</div>
<div class="card">
  <div class="panel l">
    {chip(logo_h, ht.get('abbreviation', ''))}
    <div class="mono role">Holder · {ordinal(reign_no)} reign</div>
    <div class="disp team">{two_lines(holder)}</div>
    <div class="mono stat"><b>{days_held:,}</b> days held · <b>{defenses}</b> {'defense' if defenses == 1 else 'defenses'}</div>
  </div>
  <div class="seam"></div>
  <div class="panel r">
    {chip(logo_o, ot.get('abbreviation', ''))}
    <div class="mono role">{esc(role_r)}</div>
    <div class="disp team">{two_lines(opp)}</div>
    <div class="mono stat">{stat_r}</div>
  </div>
  {belt_svg('VS')}
</div>
<div class="tiles">
  {''.join(f'<div class="tile"><div class="disp v">{v}</div><div class="mono l">{esc(lab)}</div></div>' for v, lab in tiles[:3])}
</div>
<div class="note"><b>{esc(note_b)}</b> {esc(note_r)}</div>
{FOOT}"""
    html_text = page(body, "preview", {"l1": colors_l[0], "l2": colors_l[1], "lacc": colors_l[2],
                                        "r1": colors_r[0], "r2": colors_r[1]})
    out = os.path.join(workdir, f"belt-on-the-line-{fold(holder)}-{fold(opp)}-{gday}.jpg")
    bad = render(html_text, out)

    tags = []
    for team, x in ((holder, 0.27), (opp, 0.73)):
        h = handle_for_team(team, data)
        if h:
            tags.append({"username": h, "x": x, "y": 0.47})
    nh = handle_for_network(nets, data)
    if nh:
        tags.append({"username": nh, "x": 0.5, "y": 0.255})
    return {
        "kind": "preview", "image": out, "caption": cap["caption"], "fit_problems": bad,
        "user_tags": tags, "location_id": location_for(venue, data), "venue": venue,
        "alt_text": (f"Belt on the line: {holder} ({ordinal(reign_no)} reign, {days_held:,} days, {defenses} defenses) "
                     f"vs. {opp}, {short_date(gday)}, {venue}. Belt game No. {game_no:,}."),
        "key": f"{holder}|{opp}|{next_game['date']}",
    }


def colors_for(team, espn_team):
    tc = (load_json(TEAM_COLORS_PATH, {}) or {}).get(team) or {}
    return tc.get("primary") or ("#" + espn_team["color"] if espn_team.get("color") else None)


def accents_for(team, espn_team):
    tc = (load_json(TEAM_COLORS_PATH, {}) or {}).get(team) or {}
    c = [tc.get("secondary")]
    if espn_team.get("alternateColor"):
        c.append("#" + espn_team["alternateColor"])
    return [x for x in c if x]


def build_result(lineage, game, workdir, data=None):
    """'Belt Defended' / 'New Champion' for one finished belt game.

    `game` is a lineage-style row: date, holder (the holder going in),
    opponent, game_id, outcome ('retained' / 'changed' / 'retained (tie)')
    plus 'holder_score' / 'opp_score'. Works whether or not the pipeline
    has ingested the game into lineage.json yet."""
    data = data if data is not None else ig_data()
    holder, opp = game["holder"], game["opponent"]
    gdate = d(game["date"])
    summ = espn_summary(game["game_id"])
    comp = comp_of(summ)
    hs, os_ = side(summ, holder, data), side(summ, opp, data)
    ht, ot = hs["team"], os_["team"]
    h_pts, o_pts = int(float(hs.get("score") or game["holder_score"])), int(float(os_.get("score") or game["opp_score"]))
    changed = o_pts > h_pts
    if (game.get("outcome") or "").startswith("changed") != changed and game.get("outcome"):
        print(f"  note: ESPN score {h_pts}-{o_pts} vs lineage outcome {game['outcome']} -- going with ESPN")
    venue = ((summ.get("gameInfo") or {}).get("venue") or {}).get("fullName") or ""
    season = int(game.get("season") or gdate.year)

    idx = game_index(lineage, game["game_id"])
    game_no = (idx + 1) if idx is not None else len(lineage.get("belt_games", [])) + 1
    reign = reign_on(lineage, holder, gdate) or {}
    start = d(reign["start_date"]) if reign.get("start_date") else gdate
    days_held = (gdate - start).days
    # defenses of the holder's reign BEFORE this game
    before = 0
    for g in lineage.get("belt_games", []):
        gd = d(g["date"])
        if g.get("holder") == holder and start < gd < gdate and str(g.get("outcome", "")).startswith("retained"):
            before += 1
        if start == gd and g.get("new_holder") == holder:
            pass
    defense_no = before + 1
    h_reign_no = sum(1 for r in reigns_of(lineage, holder) if d(r["start_date"]) <= start)
    o_n, o_days, o_last = history(lineage, opp, gdate, gdate)
    new_reign_no = o_n + 1

    winner, loser = (opp, holder) if changed else (holder, opp)
    wt, lt = (ot, ht) if changed else (ht, ot)
    w_pts, l_pts = max(h_pts, o_pts), min(h_pts, o_pts)
    tie = h_pts == o_pts

    w_id, l_id = wt.get("id"), lt.get("id")
    lds = leaders(summ, w_id)
    best = standout(lds)
    flow = game_flow(summ, w_id)
    nxt = next_game_after(w_id, season, gdate)
    city = home_city(winner, w_id, season, data)

    def tstats(tid):
        return team_stat(summ, tid, "totalYards"), team_stat(summ, tid, "turnovers")

    wy, wto = tstats(w_id)
    ly, lto = tstats(l_id)

    def stat_line(y, to):
        bits = []
        if y is not None:
            bits.append(f"<b>{esc(y)}</b> total yds")
        if to is not None:
            bits.append(f"<b>{esc(to)}</b> {'turnover' if str(to) == '1' else 'turnovers'}")
        return " · ".join(bits)

    if changed:
        h1 = 'New <span class="thin">champion</span>'
        when_tail = f"<b>{esc(wt.get('abbreviation') or winner)}</b>'s {ordinal(new_reign_no)} reign begins"
        role_l = f"New holder · {ordinal(new_reign_no)} reign"
        role_r = f"Dethroned · {days_held:,} days"
        cap_line = f"Moves to {city}" if city else f"New holder: {wt.get('name') or winner}"
        tile1 = (f"{days_held:,}", f"Days {lt.get('abbreviation') or loser} held it")
    else:
        h1 = 'Belt <span class="thin">defended</span>'
        when_tail = f"<b>{ordinal(defense_no)} defense</b> this reign"
        role_l = "Holder · retains" if not tie else "Holder · retains on a tie"
        role_r = f"Challenger · last held {o_last}" if o_last else "Challenger · never held it"
        cap_line = f"Stays in {city}" if city else f"Stays with {wt.get('name') or winner}"
        tile1 = (f"{days_held:,}", "Days held this reign")

    tiles = [tile1]
    if best:
        tiles.append((str(best[0]), f"{last_name(best[1])} · {best[2]}"))
    else:
        tiles.append((str(wy or "—"), "Total yards"))
    if nxt:
        where = "vs" if nxt["home"] else ("" if nxt["neutral"] else "at")
        when = clock(nxt["dt"]) if nxt["time_valid"] else "TBA"
        lab = f"Next · {where + ' ' if where else ''}{opp_short(nxt['opp'], nxt['opp_abbr'])}, {when}"
        tiles.append((tile_date(nxt["date"]), lab))
    else:
        tiles.append((f"{game_no:,}", "Belt game No."))

    facts = {
        "headline": "NEW CHAMPION -- the belt changed hands" if changed else "BELT DEFENDED",
        "belt_game_number": f"{game_no:,}",
        "final_score": f"{winner} {w_pts}, {loser} {l_pts}", "date": f"{gdate:%a} {gdate:%b} {gdate.day}",
        "stadium": venue, "winner": winner, "winner_nickname": wt.get("name"), "loser": loser, "loser_nickname": lt.get("name"),
        "holder_going_in": holder, "holder_reign_number": ordinal(h_reign_no),
        "holder_took_belt_from": reign.get("won_from"),
        "holder_took_belt_on": f"{start:%b}. {start.day}, {start.year}",
        "days_holder_had_held_it_on_game_day": days_held,
        "this_was_defense_number": None if changed else defense_no,
        "challenger_reigns_before_today": o_n, "challenger_last_held_year": o_last,
        "new_reign_number_for_winner": ordinal(new_reign_no) if changed else None,
        "winner_home_city_where_belt_goes": city,
        "halftime_score": flow["halftime"], "winner_ever_trailed": flow["winner_ever_trailed"],
        "lead_changes": flow["lead_changes"], "quarter_by_quarter": flow["quarters"],
        "winner_leaders": lds, "loser_leaders": leaders(summ, l_id),
        "team_stats": {winner: {"total_yards": wy, "turnovers": wto,
                                "possession": team_stat(summ, w_id, "possessionTime")},
                       loser: {"total_yards": ly, "turnovers": lto,
                               "possession": team_stat(summ, l_id, "possessionTime")}},
        "scoring_plays": [f"Q{p.get('period', {}).get('number')} {p.get('clock', {}).get('displayValue')}: "
                          f"{p.get('text')} ({p.get('awayScore')}-{p.get('homeScore')} away-home)"
                          for p in (summ.get("scoringPlays") or [])][:24],
        "next_game_for_belt_holder": ({"date": f"{nxt['date']:%a} {nxt['date']:%b} {nxt['date'].day}",
                                       "opponent": nxt["opp"], "home": nxt["home"], "neutral": nxt["neutral"],
                                       "kickoff_et": (clock(nxt["dt"]) + " ET") if nxt["time_valid"] else "TBA",
                                       "tv": tv_label(nxt["tv"]), "stadium": nxt["venue"]} if nxt else None),
        "hashtag_suggestion": hashtags(winner, wt.get("abbreviation"), wt.get("name"), loser, lt.get("abbreviation"),
                                       lt.get("name"), extra=[nxt["opp"]] if nxt else ()),
        "must_mention": [str(w_pts), str(l_pts)],
    }
    cap = claude_caption("result", facts)
    if not cap:
        nl = ""
        if nxt:
            nl = (f"Next up: {'vs.' if nxt['home'] else 'at'} {nxt['opp']}, {nxt['date']:%a} {nxt['date']:%b} {nxt['date'].day}"
                  + (f", {clock(nxt['dt'])} ET" if nxt["time_valid"] else "")
                  + (f" on {tv_label(nxt['tv'])}" if nxt["tv"] else "") + ". Beat the holder, take the belt.\n\n")
        lead = (f"The belt has a new home. 🏆\n\n{winner} {w_pts}, {loser} {l_pts}. {winner} takes the College Football Belt "
                f"— its {ordinal(new_reign_no)} reign — and ends {loser}'s run at {days_held:,} days.\n\n") if changed else \
               (f"The belt stays {('in ' + city) if city else 'put'}. 🏆\n\n{winner} {w_pts}, {loser} {l_pts}. "
                f"{ordinal(defense_no).capitalize()} defense of the {ordinal(h_reign_no)} reign, {days_held:,} days and counting.\n\n")
        cap = {"caption": lead + f"Belt game No. {game_no:,} is in the books.\n\n" + nl +
                          "🔗 Box score, drive chart and the full chain of custody since 1869 → link in bio\n\n" +
                          facts["hashtag_suggestion"], "note_bold": "", "note_rest": ""}
    note_b = cap.get("note_bold") or (f"{winner} takes the belt." if changed else f"{wt.get('name') or winner} hold on.")
    note_r = cap.get("note_rest") or f"Final: {winner} {w_pts}, {loser} {l_pts} at {venue}."

    colors_l = panel_colors(colors_for(winner, wt), accents_for(winner, wt))
    colors_r = panel_colors(colors_for(loser, lt), [])
    logo_w = fetch_logo(w_id, workdir)
    logo_l = fetch_logo(l_id, workdir)
    body = f"""{HEADER}
<div class="mono kicker"><span class="dot"></span>Belt game No. {game_no:,} · Final</div>
<div class="disp h1">{h1}</div>
<div class="mono when"><b>{esc(short_date(gdate))}</b><span class="sep">·</span>{esc(venue)}<span class="sep">·</span>{when_tail}</div>
<div class="card">
  <div class="panel l">
    {chip(logo_w, wt.get('abbreviation', ''))}
    <div class="mono role">{esc(role_l)}</div>
    <div class="disp score">{w_pts}</div>
    <div class="disp team">{esc(winner)}</div>
    <div class="mono stat">{stat_line(wy, wto)}</div>
  </div>
  <div class="seam"></div>
  <div class="panel r">
    {chip(logo_l, lt.get('abbreviation', ''))}
    <div class="mono role">{esc(role_r)}</div>
    <div class="disp score">{l_pts}</div>
    <div class="disp team">{esc(loser)}</div>
    <div class="mono stat">{stat_line(ly, lto)}</div>
  </div>
  {belt_svg(wt.get('abbreviation') or winner[:3].upper())}
  <div class="mono beltcap">{esc(cap_line)}</div>
</div>
<div class="tiles">
  {''.join(f'<div class="tile"><div class="disp v">{esc(v)}</div><div class="mono l">{esc(lab)}</div></div>' for v, lab in tiles[:3])}
</div>
<div class="note"><b>{esc(note_b)}</b> {esc(note_r)}</div>
{FOOT}"""
    html_text = page(body, "result", {"l1": colors_l[0], "l2": colors_l[1], "lacc": colors_l[2],
                                       "r1": colors_r[0], "r2": colors_r[1]})
    kind = "new-champion" if changed else "belt-defended"
    out = os.path.join(workdir, f"{kind}-{fold(winner)}-{w_pts}-{fold(loser)}-{l_pts}-{gdate}.jpg")
    bad = render(html_text, out)

    tags = []
    for team, x in ((winner, 0.27), (loser, 0.73)):
        h = handle_for_team(team, data)
        if h:
            tags.append({"username": h, "x": x, "y": 0.47})
    nh = handle_for_network(broadcasts(comp), data)
    if nh:
        tags.append({"username": nh, "x": 0.5, "y": 0.255})
    if best:
        ph = player_handle(best[1], data)
        if ph:
            tags.append({"username": ph, "x": 0.5, "y": 0.83})
    return {
        "kind": "result", "image": out, "caption": cap["caption"], "fit_problems": bad,
        "user_tags": tags, "location_id": location_for(venue, data), "venue": venue,
        "alt_text": (f"{'New champion' if changed else 'Belt defended'}: {winner} {w_pts}, {loser} {l_pts}, "
                     f"{short_date(gdate)} at {venue}. Belt game No. {game_no:,}."),
        "key": str(game["game_id"]),
    }
