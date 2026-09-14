#!/usr/bin/env python3
"""
Generate the real, static game-detail pages for every belt game -- one HTML
file per game, built from belt_data/lineage.json + belt_data/game_details.json
+ belt_data/team_colors.json, sharing a single site/styles.css instead of
repeating ~150 lines of CSS 1,633 times.

Usage:
    python3 build_site.py

Output (into ./site/):
    styles.css              shared stylesheet (fonts, layout, components)
    games/<game_id>.html     one page per belt game

No network calls, no API key needed -- this only reads data already fetched
by build_lineage.py / fetch_team_colors.py / fetch_game_details.py.

Team colors vary wildly (a black-on-black program next to a pale-gold one),
so instead of hand-picking text colors per team like the first mockup did,
this script picks each page's text colors algorithmically at build time
using WCAG relative luminance / contrast ratio -- see pick_best() below.
That's what makes this safe to run unattended across all 101 teams instead
of eyeballing every color pair.
"""

import hashlib
import html
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from email.utils import format_datetime

OUT_DIR = "site"
DATA_DIR = "belt_data"

# The custom domain, once DNS + GitHub Pages are both pointed at it (see
# README.md's "Custom domain" section). Two things depend on this:
#   1. site/CNAME -- GitHub Pages reads this file from the published output
#      to know which custom domain to serve. Since site/ is wiped and
#      rebuilt from scratch every run, this HAS to be written by the build
#      itself, or the custom domain setting would silently get dropped the
#      next time the pipeline runs (a real GitHub Pages gotcha, not a
#      guess -- it says so in their own docs).
#   2. SITE_URL below, for the absolute URLs that Open Graph / Twitter Card
#      meta tags need (see generate_homepage()).
CUSTOM_DOMAIN = "collegefootballbelt.com"
SITE_URL = f"https://{CUSTOM_DOMAIN}"

# Optional, free, privacy-friendly page-view analytics (https://www.goatcounter.com/).
# Empty by default -- every page just omits the tracking snippet, same
# no-op-when-unset pattern as ANTHROPIC_API_KEY for the AI preview/recaps.
# To turn it on: sign up free at goatcounter.com, pick a site code (that's
# the "goatcounter.com" subdomain you're assigned, e.g. "my-code" for
# my-code.goatcounter.com), then set it here and rerun the pipeline.
GOATCOUNTER_CODE = ""

PAPER_LIGHT = "#e7e2d5"
PAPER_DARK = "#161009"


def head_extras(rel=""):
    """Favicon links (+ the analytics snippet, when GOATCOUNTER_CODE is
    set) shared by every page template. `rel` is the relative path prefix
    back to the site root -- "" for root-level pages, "../" for pages one
    directory down (games/, teams/)."""
    bits = [
        f'<link rel="icon" type="image/png" href="{rel}favicon.png">',
        f'<link rel="apple-touch-icon" href="{rel}apple-touch-icon.png">',
        f'<link rel="alternate" type="application/rss+xml" '
        f'title="The College Football Belt — Belt Changes" href="{rel}feed.xml">',
    ]
    if GOATCOUNTER_CODE:
        bits.append(
            f'<script data-goatcounter="https://{GOATCOUNTER_CODE}.goatcounter.com/count" '
            f'async src="//gc.zgo.at/count.js"></script>'
        )
    return "\n".join(bits)


# ---------------------------------------------------------------- color math

def hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _linearize(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hexcolor):
    r, g, b = hex_to_rgb(hexcolor)
    r, g, b = _linearize(r), _linearize(g), _linearize(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex1, hex2):
    l1, l2 = relative_luminance(hex1), relative_luminance(hex2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def blend(hex1, hex2, weight1):
    """weight1 fraction of hex1, the rest hex2 -- mirrors CSS color-mix()."""
    r1, g1, b1 = hex_to_rgb(hex1)
    r2, g2, b2 = hex_to_rgb(hex2)
    r = round(r1 * weight1 + r2 * (1 - weight1))
    g = round(g1 * weight1 + g2 * (1 - weight1))
    b = round(b1 * weight1 + b2 * (1 - weight1))
    return f"#{r:02x}{g:02x}{b:02x}"


def pick_best(candidates, against_colors):
    """Return whichever candidate hex has the best WORST-CASE contrast ratio
    across every color in against_colors (e.g. both stops of a gradient)."""
    best, best_score = candidates[0], -1
    for cand in candidates:
        worst = min(contrast_ratio(cand, bg) for bg in against_colors)
        if worst > best_score:
            best, best_score = cand, worst
    return best, best_score


def panel_colors(primary, alt):
    """Text colors for a team's scoreboard panel, whose CSS background is a
    gradient from `primary` down to `primary` blended 75% toward black.

    `ink` (for the team name/labels) always wins on pure legibility between
    white and black. `accent` (for the big score digits) prefers the team's
    own alternate color for personality, and only falls back to `ink` when
    that alternate color genuinely doesn't read against this team's primary
    -- picking whichever of {alt, ink} has more raw contrast would almost
    always throw the accent away in favor of white/black, since a mid-tone
    accent rarely out-contrasts a pure white/black ink."""
    dark_stop = blend(primary, "#000000", 0.75)
    stops = [primary, dark_stop]
    ink, _ = pick_best(["#ffffff", "#000000"], stops)
    alt_worst = min(contrast_ratio(alt, s) for s in stops)
    accent = alt if alt_worst >= 3.0 else ink
    return ink, accent


def emphasis_color(primary, alt, paper):
    cand, score = pick_best([primary, alt], [paper])
    if score < 3.0:
        return None  # neither team color reads well here -- fall back to plain ink
    return cand


# --------------------------------------------------------------- data utils

def pick(d, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def load_optional_json(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_data():
    with open(os.path.join(DATA_DIR, "lineage.json")) as f:
        lineage = json.load(f)
    with open(os.path.join(DATA_DIR, "game_details.json")) as f:
        details = json.load(f)
    with open(os.path.join(DATA_DIR, "team_colors.json")) as f:
        colors = json.load(f)
    next_game = load_optional_json("next_game.json")
    upcoming_games = load_optional_json("upcoming_games.json") or []
    matchup = load_optional_json("matchup_preview.json")
    ai_preview = load_optional_json("ai_preview.json")
    weather = load_optional_json("weather.json")
    recaps = load_optional_json("recaps.json") or {}
    game_plays = load_optional_json("game_plays.json") or {}
    return (lineage, details, colors, next_game, upcoming_games, matchup,
            ai_preview, weather, recaps, game_plays)


def team_color(colors, name):
    entry = colors.get(name) or {}
    primary = entry.get("color") or "#5b5b5b"
    alt = entry.get("alternate_color") or "#ffffff"
    return primary, alt


def team_logo(colors, name):
    """CFBD's own logo URL for this team, or None -- fetch_team_colors.py
    already captures it (same /teams call as the color data, zero extra
    cost), it just wasn't rendered anywhere yet. None for the handful of
    historic programs CFBD doesn't track (Carlisle, Olympic Club, etc.) --
    callers render nothing rather than a broken image."""
    entry = colors.get(name) or {}
    return entry.get("logo") or None


def logo_img(colors, name, css_class="teamLogo", size=40):
    """A ready-to-embed <img>, or "" when this team has no logo on file --
    always check truthiness before using this in a layout that assumes an
    image is present."""
    url = team_logo(colors, name)
    if not url:
        return ""
    return (f'<img class="{css_class}" src="{esc(url)}" alt="" width="{size}" height="{size}" '
            f'loading="lazy" onerror="this.remove()">')


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def fmt_date(iso):
    # avoid a libc/locale dependency -- do it by hand
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{MONTH_NAMES[m-1]} {d}, {y}"


def fmt_month_day(d):
    return f"{MONTH_NAMES[d.month-1]} {d.day}"


def ordinal_ot_label(period_index):
    """period_index is 0-based; 0..3 are Q1-Q4, 4+ are OT periods."""
    if period_index < 4:
        return str(period_index + 1)
    ot_num = period_index - 4 + 1
    return "OT" if ot_num == 1 else f"{ot_num}OT"


def compute_sequence(belt_games):
    """Attach game_number (1-based) and reign_number to each belt game,
    in place. reign_number follows the boxing-lineage convention: the
    title-changing game itself belongs to the NEW reign."""
    reign_no = 1
    for i, g in enumerate(belt_games, 1):
        g["game_number"] = i
        if g["outcome"] == "changed":
            reign_no += 1
        g["reign_number"] = reign_no


def ordinal(n):
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def team_chip(name):
    """A short (2-4 letter) code for a team, auto-derived since there's no
    data source for real school abbreviations across all 101 programs --
    initials for multi-word names, first few letters otherwise."""
    base = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip()
    words = [w for w in re.split(r"[\s\-]+", base) if w]
    letters = [w[0].upper() for w in words if w[0].isalpha()]
    if len(letters) >= 2:
        return "".join(letters[:4])
    stripped = re.sub(r"[^A-Za-z]", "", base).upper()
    return (stripped[:4] or "?")


def team_slug(name):
    """URL-safe filename stem for a team's own page, e.g. 'Notre Dame' ->
    'notre-dame', "Texas A&M" -> 'texas-a-m'. Deterministic and shared by
    every team-page link across the site, so it only has to be right once."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "team"


def reign_dates(reign, today):
    start = date.fromisoformat(reign["start_date"])
    end = date.fromisoformat(reign["end_date"]) if reign.get("end_date") else today
    return start, end


def reign_duration_days(reign, today):
    start, end = reign_dates(reign, today)
    return (end - start).days


def fmt_duration(start, end):
    """Human-readable span between two dates: '<n> days' under a year,
    else '<y> yrs, <d> days' (day part omitted if it lands on 0) -- using
    the actual calendar years between the two dates (via the anniversary
    of `start`), not a division by 365/365.25, so leap years don't drift
    the day count."""
    days = (end - start).days
    if days < 365:
        return f"{days:,} day{'s' if days != 1 else ''}"

    years = end.year - start.year

    def anniversary(y):
        try:
            return start.replace(year=start.year + y)
        except ValueError:
            # start was a Feb 29 with no such date `y` years later
            return start.replace(year=start.year + y, month=2, day=28)

    anniv = anniversary(years)
    if anniv > end:
        years -= 1
        anniv = anniversary(years)

    rem_days = (end - anniv).days
    parts = [f"{years} yr{'s' if years != 1 else ''}"]
    if rem_days:
        parts.append(f"{rem_days} day{'s' if rem_days != 1 else ''}")
    return ", ".join(parts)


def build_change_game_index(belt_games):
    """(start_date, new_holder) -> the belt_game where that reign began, so
    a reign's title-winning score can be shown winner-first (reigns.json's
    own won_score is copied straight from belt_games' home-away order, not
    winner-first, so it needs re-deriving from the actual game)."""
    index = {}
    for g in belt_games:
        if g["outcome"] in ("changed", "established"):
            index[(g["date"], g["new_holder"])] = g
    return index


def build_loss_game_index(belt_games):
    """(end_date, team) -> the belt_game where `team` lost the belt, the
    mirror image of build_change_game_index (which indexes by the WINNING
    side of a title change) -- used on a team's own page to show how each
    of its reigns ended."""
    index = {}
    for g in belt_games:
        if g["outcome"] == "changed":
            index[(g["date"], g["holder"])] = g
    return index


STAT_ROWS = [
    ("Total yards", "totalYards", None),
    ("Rushing yards", "rushingYards", None),
    ("Passing yards", "netPassingYards", None),
    ("First downs", "firstDowns", None),
    ("3rd down conv.", "thirdDownEff", "eff"),
    ("Turnovers", "turnovers", None),
    ("Penalties–yards", "totalPenaltiesYards", "pen"),
    ("Time of possession", "possessionTime", None),
]


def fmt_stat(raw, kind):
    if raw is None:
        return "—"
    if kind == "eff":
        return raw.replace("-", "/")
    if kind == "pen":
        return raw.replace("-", "–")
    return raw


def render_recap(g):
    recap = g.get("recap")
    if not recap or not (recap.get("recap") or recap.get("key_moments")):
        return ""
    body = recap.get("recap") or ""
    moments = recap.get("key_moments") or []
    moments_html = "".join(f"<li>{esc(m)}</li>" for m in moments)
    moments_block = f'<ul class="keyMatchups">{moments_html}</ul>' if moments_html else ""

    return f'''
  <section>
    <div class="sectionHead withTag">
      <span class="tag">AI-Written</span>
      <span class="rule"></span>
      <h2>Recap</h2>
    </div>
    <div class="aiPreviewBody"><p>{esc(body)}</p></div>
    {moments_block}
    <p class="noteBox">Written by Claude from the box score and play-by-play on this page &mdash; a plain-English recap of the numbers below, not a substitute for them.</p>
  </section>'''


def render_key_plays(g):
    plays = g.get("key_plays")
    if not plays:
        return ""
    rows = ""
    for p in plays:
        when = f"Q{p.get('period')} {p.get('clock')}" if p.get("period") else (p.get("clock") or "—")
        yards = p.get("yards_gained")
        yard_txt = f"{yards:+,} yd" if isinstance(yards, int) else "—"
        text = p.get("play_text") or p.get("play_type") or "—"
        cls = " scoring" if p.get("scoring") else ""
        rows += (f'<tr class="keyPlayRow{cls}"><td class="tabular">{esc(when)}</td>'
                 f'<td>{esc(p.get("offense") or "—")}</td>'
                 f'<td class="tabular">{esc(yard_txt)}</td>'
                 f'<td>{esc(text)}</td></tr>')

    return f'''
  <section>
    <div class="sectionHead withTag">
      <span class="tag">Play By Play</span>
      <span class="rule"></span>
      <h2>Key plays</h2>
      <span class="sourceTag">CFBD play-by-play</span>
    </div>
    <div style="overflow-x:auto">
      <table class="keyPlaysTable">
        <thead><tr><th>When</th><th>Team</th><th>Yards</th><th>Play</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <p class="noteBox">Scoring plays, turnovers, and gains of 20+ yards, pulled from CFBD’s play-by-play. This section only appears for belt games with play-by-play on file (2003 onward).</p>
  </section>'''


# ------------------------------------------------------------------- styles

STYLES_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@500;700;800;900&family=Spectral:ital,wght@0,400;0,500;0,600;1,400;1,500&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root{
  --paper:#e7e2d5; --paper-2:#dcd5c3;
  --ink:#211a12; --ink-soft:#5b5140;
  --brass:#8a6a34; --brass-bright:#a97f38; --brass-line: rgba(138,106,52,.32);
  --hairline: rgba(33,26,18,.14);
  --shadow: 0 18px 40px -22px rgba(24,17,12,.55);
  --good:#3f6b3f; --good-bg: rgba(63,107,63,.12);
  --map-1: rgba(138,106,52,.28); --map-2: rgba(138,106,52,.55); --map-3: rgba(138,106,52,.88);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#161009; --paper-2:#1f170e;
    --ink:#ece3d1; --ink-soft:#b6a98d;
    --brass:#cf9f52; --brass-bright:#e0b46a; --brass-line: rgba(207,159,82,.32);
    --hairline: rgba(236,227,209,.14);
    --shadow: 0 18px 44px -20px rgba(0,0,0,.6);
    --good:#7fbf7f; --good-bg: rgba(127,191,127,.14);
    --map-1: rgba(207,159,82,.28); --map-2: rgba(207,159,82,.55); --map-3: rgba(207,159,82,.88);
  }
}
:root[data-theme="dark"]{
  --paper:#161009; --paper-2:#1f170e;
  --ink:#ece3d1; --ink-soft:#b6a98d;
  --brass:#cf9f52; --brass-bright:#e0b46a; --brass-line: rgba(207,159,82,.32);
  --hairline: rgba(236,227,209,.14);
  --shadow: 0 18px 44px -20px rgba(0,0,0,.6);
  --good:#7fbf7f; --good-bg: rgba(127,191,127,.14);
  --map-1: rgba(207,159,82,.28); --map-2: rgba(207,159,82,.55); --map-3: rgba(207,159,82,.88);
}

*{box-sizing:border-box}
body{ margin:0; background:var(--paper); color:var(--ink); font-family:"Spectral",Georgia,serif; line-height:1.55; -webkit-font-smoothing:antialiased; }
h1,h2{ text-wrap:balance }
.mono{ font-family:"IBM Plex Mono", ui-monospace, monospace; }
.display{ font-family:"Big Shoulders Display","Arial Narrow",sans-serif; }
.tabular{ font-variant-numeric:tabular-nums; }
a{ color:inherit }

.wrap{ max-width:980px; margin:0 auto; padding-inline:20px; }
@media (min-width:760px){ .wrap{ padding-inline:32px } }

header.site{ padding-block:20px 14px; border-bottom:1px solid var(--hairline); }
.back{ font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.04em; color:var(--ink-soft); text-decoration:none; display:inline-flex; gap:6px; }
.back:hover{ color:var(--ink) }
.crumbTitle{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:20px; margin-top:8px; }

.gameMeta{ display:flex; gap:14px; flex-wrap:wrap; align-items:center; margin: 22px 0 6px; font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--brass-bright); }
.gameMeta .dot{ width:4px; height:4px; border-radius:50%; background:var(--ink-soft); }
.beltTag{ background: var(--good-bg); color:var(--good); padding:3px 9px; border-radius:3px; font-weight:600; }
.neutralTag{ background: color-mix(in srgb, var(--brass) 18%, transparent); color:var(--brass-bright); padding:3px 9px; border-radius:3px; font-weight:600; }

h1.matchup{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:clamp(28px,4.6vw,42px); line-height:1.02; margin:6px 0 26px; }
h1.matchup .win{ color:var(--emph); }

/* scoreboard */
.scoreboard{
  display:grid; grid-template-columns:1fr auto 1fr; align-items:stretch; gap:0;
  border:1px solid var(--hairline); border-radius:8px; overflow:hidden; box-shadow:var(--shadow);
}
@media (max-width:640px){ .scoreboard{ grid-template-columns:1fr; } .scoreboard .vs{ display:none } }
.teamLogo{ object-fit:contain; flex:none; vertical-align:middle; }
.teamPanel{ padding:26px 22px; display:flex; flex-direction:column; gap:10px; }
.teamPanel .panelTop{ display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
.teamPanel .teamLogo{ filter:drop-shadow(0 1px 3px rgba(0,0,0,.4)); }
.teamPanel.home{ background: linear-gradient(160deg, var(--home) 0%, color-mix(in srgb, var(--home) 75%, black) 100%); color:var(--home-ink); }
.teamPanel.away{ background: linear-gradient(160deg, var(--away) 0%, color-mix(in srgb, var(--away) 75%, black) 100%); color:var(--away-ink); }
.teamPanel .side{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.14em; text-transform:uppercase; opacity:.8; }
.teamPanel .name{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(22px,3.6vw,30px); line-height:1.02; }
.teamPanel .pts{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:clamp(46px,7vw,64px); line-height:.9; margin-top:2px; }
.teamPanel.home .pts{ color: var(--home-accent); }
.teamPanel.away .pts{ color: var(--away-accent); }
.teamPanel .badge{ align-self:flex-start; font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.08em; text-transform:uppercase; padding:3px 8px; border-radius:3px; border:1px solid currentColor; opacity:.85; margin-top:4px; }
.vs{ display:flex; align-items:center; justify-content:center; padding:0 18px; background:var(--paper-2); font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:15px; color:var(--ink-soft); }

/* section pattern */
.sectionHead{ display:flex; align-items:baseline; gap:14px; margin:44px 0 16px; }
.sectionHead .tag{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:var(--brass-bright); white-space:nowrap; }
.sectionHead .rule{ height:1px; flex:1; background:var(--brass-line); }
.sectionHead h2{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(20px,3vw,25px); margin:0; white-space:nowrap; }
.sectionHead.withTag{ flex-wrap:wrap; row-gap:8px; }

/* line score table */
table.lineScore{ width:100%; border-collapse:collapse; font-family:"IBM Plex Mono",monospace; font-size:14px; }
table.lineScore th, table.lineScore td{ padding:10px 12px; text-align:center; border-bottom:1px solid var(--hairline); }
table.lineScore th{ font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); font-weight:600; text-align:center; }
table.lineScore td.teamCell, table.lineScore th.teamCell{ text-align:left; font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:16px; letter-spacing:.01em; }
table.lineScore tr.winner td.teamCell{ color:var(--emph); }
table.lineScore td.final{ font-weight:700; font-size:16px; }
table.lineScore tbody tr:last-child td{ border-bottom:none; }
.swatch{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:8px; vertical-align:middle; border:1px solid var(--hairline); }

.statCategory{ margin:0 0 26px; }
.statCategory:last-child{ margin-bottom:0; }
.statCategory h3{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.1em; text-transform:uppercase; color:var(--ink-soft); margin:0 0 8px; }
table.playerStatsTable{ width:100%; border-collapse:collapse; font-size:13.5px; }
table.playerStatsTable th{ text-align:right; font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-soft); font-weight:600; padding:7px 8px; border-bottom:1px solid var(--brass-line); }
table.playerStatsTable th.teamCell{ text-align:left; }
table.playerStatsTable td{ padding:8px; border-bottom:1px solid var(--hairline); text-align:right; }
table.playerStatsTable td.teamCell{ text-align:left; font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:14.5px; white-space:nowrap; }
table.playerStatsTable td.teamCell .playerTeam{ font-family:"IBM Plex Mono",monospace; font-weight:400; font-size:10.5px; color:var(--ink-soft); margin-left:6px; text-transform:uppercase; letter-spacing:.04em; }
table.playerStatsTable tbody tr:last-child td{ border-bottom:none; }
table.keyPlaysTable{ width:100%; border-collapse:collapse; font-size:13.5px; }
table.keyPlaysTable th{ text-align:left; font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-soft); font-weight:600; padding:7px 8px; border-bottom:1px solid var(--brass-line); }
table.keyPlaysTable td{ padding:9px 8px; border-bottom:1px solid var(--hairline); vertical-align:top; }
table.keyPlaysTable td:first-child{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); white-space:nowrap; }
table.keyPlaysTable td:nth-child(2){ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:14px; white-space:nowrap; }
table.keyPlaysTable td:nth-child(3){ font-family:"IBM Plex Mono",monospace; font-size:12.5px; white-space:nowrap; }
table.keyPlaysTable tbody tr:last-child td{ border-bottom:none; }
table.keyPlaysTable tr.scoring td:nth-child(2){ color:var(--brass-bright); }
details.moreStats{ margin-top:8px; }
details.moreStats summary{ cursor:pointer; font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.04em; color:var(--brass-bright); padding:8px 0; list-style:none; }
details.moreStats summary::-webkit-details-marker{ display:none; }
details.moreStats summary::before{ content:"+ "; }
details.moreStats[open] summary::before{ content:"\\2212 "; }
details.moreStats summary:hover{ text-decoration:underline; }
details.moreStats .statCategory{ margin-top:18px; }

/* box score */
.sampleTag{
  font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.1em; text-transform:uppercase;
  background: color-mix(in srgb, var(--brass) 18%, transparent); color:var(--brass-bright);
  padding:3px 8px; border-radius:3px; border:1px dashed var(--brass-line);
}
.sourceTag{
  font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.1em; text-transform:uppercase;
  background: var(--good-bg); color:var(--good);
  padding:3px 8px; border-radius:3px; border:1px solid transparent;
}
.statGrid{ display:grid; grid-template-columns:1fr auto auto; gap:0 18px; }
.statRow{ display:contents; }
.statRow .label{ padding:9px 0; border-bottom:1px solid var(--hairline); color:var(--ink-soft); font-size:13.5px; }
.statRow .val{ padding:9px 0; border-bottom:1px solid var(--hairline); font-family:"IBM Plex Mono",monospace; font-size:14px; text-align:right; }
.statRow .val.home{ color: color-mix(in srgb, var(--home) 82%, var(--ink)); }
.statRow .val.away{ color: color-mix(in srgb, var(--away) 82%, var(--ink)); }
.statHead{ display:contents; }
.statHead span{ padding-bottom:8px; font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:120px; }
.statHead span:first-child{ text-align:left; max-width:none; }

.noteBox{
  margin-top:18px; padding:14px 16px; border-left:2px solid var(--brass); background:var(--paper-2);
  font-size:13px; color:var(--ink-soft); border-radius:0 4px 4px 0;
}

/* ---------- upcoming game preview page ---------- */
.previewMeta{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; color:var(--ink-soft); margin:2px 0 6px; }
.kickoffLocal{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; color:var(--ink-soft); margin:0 0 30px; }
.kickoffLocal[hidden]{ display:none; }
.formGrid{ display:grid; grid-template-columns:1fr 1fr; gap:22px; margin:0 0 8px; }
@media (max-width:700px){ .formGrid{ grid-template-columns:1fr; } }
.formCol h3{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; margin:0 0 10px; display:flex; align-items:center; gap:8px; }
.formList{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px; }
.formList li{ display:flex; align-items:center; gap:10px; padding:9px 11px; background:var(--paper-2); border-radius:6px; font-size:13px; }
.formBadge{ font-family:"IBM Plex Mono",monospace; font-weight:700; font-size:11px; width:20px; height:20px; border-radius:50%; display:flex; align-items:center; justify-content:center; flex-shrink:0; }
.formBadge.w{ background:#2f6b3f; color:#eafaea; }
.formBadge.l{ background:#7a2e2e; color:#fbeaea; }
.formBadge.t{ background:var(--ink-soft); color:var(--paper); }
.formScore{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap; }
.formOpp{ color:var(--ink-soft); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.formDate{ margin-left:auto; font-family:"IBM Plex Mono",monospace; font-size:10.5px; color:var(--ink-soft); white-space:nowrap; }
.emptyNote{ color:var(--ink-soft); font-size:13px; }
.weatherCard{
  display:flex; align-items:baseline; flex-wrap:wrap; gap:6px 14px;
  padding:14px 18px; margin:4px 0 6px; border:1px solid var(--hairline);
  border-radius:6px; background:var(--paper-2);
}
.weatherTemp{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:26px; }
.weatherCond{ font-size:14.5px; color:var(--ink); }
.weatherSub{ font-size:13px; color:var(--ink-soft); flex-basis:100%; }
.h2hHeadline{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:21px; margin:0 0 16px; }
.aiPreviewBody p{ max-width:70ch; font-size:15px; }
.keyMatchups{ margin:14px 0 20px; padding-left:1.15em; max-width:68ch; }
.keyMatchups li{ margin:7px 0; font-size:14.5px; }
.keyMatchups li::marker{ color:var(--brass); }
.bettingHead{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); margin:20px 0 6px; }
.predictionCall{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:23px; margin:0 0 10px; color:var(--brass); }
.predictionBody{ margin-top:4px; }

footer{ padding-block:28px 40px; border-top:1px solid var(--hairline); margin-top:52px; font-size:12.5px; color:var(--ink-soft); }
.footRow{ display:flex; justify-content:space-between; gap:20px; flex-wrap:wrap; }
.footRow nav{ display:flex; gap:16px; font-family:"IBM Plex Mono",monospace; }
.footRow nav a{ text-decoration:none; color:var(--ink-soft); }
.footRow nav a:hover{ color:var(--ink); }

/* ---------- homepage: header/hero ---------- */
.headerRow{ display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between; gap:14px 28px; }
.brandBlock{ display:flex; flex-direction:column; gap:2px; min-width:0; }
.eyebrow{ font-family:"IBM Plex Mono", monospace; font-size:11.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--brass-bright); font-weight:600; }
.wordmark{ font-family:"Big Shoulders Display", sans-serif; font-weight:800; font-size:clamp(28px, 4.4vw, 44px); letter-spacing:.01em; line-height:.95; margin:2px 0 0; text-wrap:balance; }
nav.site{ display:flex; gap:22px; font-family:"IBM Plex Mono", monospace; font-size:13px; letter-spacing:.03em; }
nav.site a{ text-decoration:none; border-bottom:1px solid transparent; padding-bottom:2px; color:var(--ink-soft); }
nav.site a:hover{ color:var(--ink); border-color:var(--brass); }
.tagline{ font-style:italic; color:var(--ink-soft); max-width:46ch; font-size:15px; margin:10px 0 16px; }

.hero{ display:grid; grid-template-columns: 1.05fr .95fr; gap:34px; align-items:center; padding-block: 34px 30px; }
@media (max-width:820px){ .hero{ grid-template-columns:1fr; } }
.hero h1{ font-family:"Big Shoulders Display", sans-serif; font-weight:900; font-size:clamp(30px, 4vw, 42px); line-height:1.02; margin:0 0 14px; text-wrap:balance; }
.hero p.lede{ font-size:17px; max-width:44ch; margin:0 0 18px; }

.nextGame{ display:flex; align-items:center; gap:10px; width:fit-content; max-width:100%; margin:0 0 22px; padding:9px 16px; background:var(--paper-2); border:1px solid var(--brass-line); border-radius:20px; text-decoration:none; color:inherit; transition:border-color .15s ease, box-shadow .15s ease; }
.nextGame:hover{ border-color:var(--brass); box-shadow:0 2px 8px rgba(0,0,0,.08); }
.nextGame:hover .nextGameText strong{ color:var(--brass-bright); }
.nextGameTag{ font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--brass-bright); font-weight:600; white-space:nowrap; padding-right:10px; border-right:1px solid var(--brass-line); }
.nextGameText{ font-size:13.5px; color:var(--ink); }
.nextGameText strong{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:14.5px; }
@media (max-width:500px){ .nextGame{ white-space:normal; } }

.beltWatch{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin:0 0 22px; font-size:12.5px; }
.beltWatchLabel{ font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); white-space:nowrap; }
.watchChip{ color:var(--ink-soft); white-space:nowrap; }
.watchChip strong{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:13.5px; color:var(--ink); }

.heroFacts{ display:flex; gap:26px; flex-wrap:wrap; }
.heroFacts div{ display:flex; flex-direction:column; gap:2px; }
.heroFacts .n{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:26px; }
.heroFacts .l{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); }

.plateWrap{ display:flex; justify-content:center; }
.plate{
  width:100%; max-width:400px;
  background: linear-gradient(160deg, var(--holder) 0%, color-mix(in srgb, var(--holder) 78%, black) 100%);
  color:var(--holder-ink);
  border-radius: 130px 130px 18px 18px;
  padding: 46px 30px 30px;
  text-align:center;
  box-shadow: var(--shadow), inset 0 0 0 1px rgba(255,255,255,.06);
  position:relative;
  overflow:hidden;
}
.plate::before{ content:""; position:absolute; inset:10px 10px auto 10px; height:1px; background: linear-gradient(90deg, transparent, var(--holder-alt), transparent); opacity:.55; }
.plate .rim{ position:absolute; inset:7px; border-radius:124px 124px 12px 12px; border:1.5px solid color-mix(in srgb, var(--holder-alt) 65%, transparent); pointer-events:none; }
.monogram{
  width:78px; height:78px; margin:0 auto 14px; border-radius:50%;
  background: color-mix(in srgb, var(--holder-alt) 22%, transparent);
  border:2px solid var(--holder-alt);
  display:flex; align-items:center; justify-content:center;
  font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:26px;
  color: var(--holder-alt);
}
.plate .eyebrow2{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.16em; text-transform:uppercase; color: color-mix(in srgb, var(--holder-ink) 78%, transparent); }
.plate .holderName{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size: clamp(32px, 6vw, 44px); line-height:.95; margin:6px 0 4px; text-wrap:balance; }
.plate .holderName a{ color:inherit; text-decoration:none; }
.plate .holderName a:hover{ text-decoration:underline; text-decoration-color:currentColor; }
.plate .sub{ font-size:13.5px; color: color-mix(in srgb, var(--holder-ink) 82%, transparent); margin-bottom:18px; }
.plateStats{ display:grid; grid-template-columns:repeat(3,1fr); gap:0; border-top:1px solid color-mix(in srgb, var(--holder-alt) 45%, transparent); padding-top:14px; }
.plateStats div{ display:flex; flex-direction:column; gap:2px; }
.plateStats .n{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:20px; }
.plateStats .l{ font-family:"IBM Plex Mono",monospace; font-size:9.5px; letter-spacing:.08em; text-transform:uppercase; opacity:.75; }

@media (prefers-reduced-motion: no-preference){
  .plate{ animation: sheen 1.4s ease-out .1s both; }
  @keyframes sheen{ from{ filter:brightness(1.28) saturate(.85); } to{ filter:brightness(1) saturate(1); } }
}

/* ---------- homepage: lineage chain ---------- */
.chain{ display:grid; grid-auto-flow:column; grid-auto-columns:minmax(148px,1fr); gap:0; overflow-x:auto; padding-bottom:6px; margin: 0 -4px; scrollbar-width:thin; }
@media (max-width:700px){ .chain{ grid-auto-flow:row; grid-auto-columns:unset; } }
.chainLead{ display:flex; align-items:center; padding:0 14px 0 4px; font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--ink-soft); white-space:nowrap; }
@media (max-width:700px){ .chainLead{ padding:0 0 10px; } }
.link{ position:relative; display:block; padding:16px 16px 14px; margin:0 4px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:6px; text-decoration:none; color:inherit; transition:border-color .15s ease, box-shadow .15s ease, transform .15s ease; }
.link:hover{ border-color:var(--brass); box-shadow:0 2px 8px rgba(0,0,0,.08); transform:translateY(-1px); }
.link:hover .team{ color:var(--brass-bright); }
.link + .link::before{ content:""; position:absolute; left:-9px; top:50%; width:10px; height:2px; background:var(--brass-line); }
@media (max-width:700px){ .link + .link::before{ display:none; } }
.link .chip{ width:34px; height:34px; border-radius:50%; margin-bottom:10px; display:flex; align-items:center; justify-content:center; font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:12.5px; border:1.5px solid rgba(0,0,0,.15); }
.link .team{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:17px; line-height:1.05; }
.link .beat{ font-size:12px; color:var(--ink-soft); margin:5px 0 7px; }
.link .meta{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; color:var(--ink-soft); display:flex; justify-content:space-between; }
.link.current{ border-color: var(--brass); box-shadow: inset 0 0 0 1px var(--brass-line); }
.link.current .now{ position:absolute; top:12px; right:12px; font-family:"IBM Plex Mono",monospace; font-size:9px; letter-spacing:.08em; text-transform:uppercase; color:var(--brass-bright); display:flex; align-items:center; gap:5px; }
.link.current .now::before{ content:""; width:6px; height:6px; border-radius:50%; background:var(--brass-bright); box-shadow:0 0 0 3px color-mix(in srgb, var(--brass-bright) 25%, transparent); }

/* ---------- homepage: ruleset teaser ---------- */
.rules{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; }
@media (max-width:900px){ .rules{ grid-template-columns:repeat(2,1fr); } }
@media (max-width:520px){ .rules{ grid-template-columns:1fr; } }
.rule-card{ padding:18px 18px 16px; border-top:2px solid var(--brass); background:var(--paper-2); border-radius:2px 2px 6px 6px; }
.rule-card h3{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:17px; margin:0 0 7px; }
.rule-card p{ margin:0; font-size:13.5px; color:var(--ink-soft); }
.rulesFoot{ margin-top:16px; font-size:13.5px; }
.rulesFoot a{ text-decoration:none; border-bottom:1px solid var(--brass); color:var(--ink); font-weight:600; }

/* ---------- homepage: on this day ---------- */
.otdList{ display:flex; flex-direction:column; margin-top:6px; }
.otdRow{ display:flex; align-items:center; gap:16px; padding:12px 4px; border-bottom:1px solid var(--hairline); text-decoration:none; color:inherit; }
.otdList a.otdRow:hover .otdMatchup{ text-decoration:underline; text-decoration-color:var(--brass); }
.otdRow:last-child{ border-bottom:none; }
.otdYear{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:16px; color:var(--brass-bright); width:44px; flex:none; }
.otdMatchup{ flex:1; font-size:14.5px; }
.otdTag{ font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-soft); white-space:nowrap; }
.otdTag.changed{ color:var(--brass-bright); }
.otdRow.hiddenRow{ display:none; }
@media (max-width:560px){ .otdRow{ flex-wrap:wrap; } .otdTag{ order:3; width:100%; padding-left:60px; } }

/* ---------- homepage: stats band ---------- */
.band{ background:#18110c; color:#ecdfc4; margin-block:52px 0; padding-block:34px; }
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]) .band{ background:#0c0805; } }
:root[data-theme="dark"] .band{ background:#0c0805; }
.bandGrid{ display:grid; grid-template-columns:repeat(4,1fr); gap:18px; text-align:center; }
@media (max-width:700px){ .bandGrid{ grid-template-columns:repeat(2,1fr); gap:26px 18px; } }
.bandGrid .n{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:clamp(30px,5vw,42px); color:#cf9f52; line-height:1; }
.bandGrid .l{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:rgba(236,223,196,.7); margin-top:6px; }

/* ---------- ruleset page ---------- */
.pageTitle{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:clamp(30px,4.6vw,44px); line-height:1.02; margin:26px 0 10px; text-wrap:balance; }
.lede{ font-size:16px; color:var(--ink-soft); max-width:62ch; margin:0 0 8px; }
.proseBlock{ max-width:68ch; }
.proseBlock p{ margin:0 0 14px; }
.ruleList{ margin:10px 0 0; padding-left:1.15em; max-width:68ch; }
.ruleList li{ margin:7px 0; }
.ruleList li::marker{ color:var(--brass); }

/* ---------- records page ---------- */
.recordsGrid{ display:grid; grid-template-columns:1fr 1fr; gap:22px; margin:28px 0 8px; }
@media (max-width:760px){ .recordsGrid{ grid-template-columns:1fr; } }
.recordCard{ background:var(--paper-2); border:1px solid var(--hairline); border-radius:10px; padding:20px 22px 8px; }
.recordCard h2{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; margin:0 0 2px; }
.recordCardSub{ font-size:12.5px; color:var(--ink-soft); margin:0 0 14px; }
.recordList{ display:flex; flex-direction:column; }
.recordRow{ display:grid; grid-template-columns:20px 1fr auto; align-items:baseline; column-gap:10px; row-gap:2px; padding:10px 0; border-bottom:1px solid var(--hairline); text-decoration:none; color:inherit; }
.recordRow:last-child{ border-bottom:none; }
a.recordRow:hover .recordMain{ text-decoration:underline; text-decoration-color:var(--brass); }
.recordRank{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); }
.recordMain{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:15px; display:flex; align-items:center; }
.recordValue{ font-size:14px; font-weight:600; color:var(--brass-bright); }
.recordSub{ grid-column:2 / 4; font-size:12px; color:var(--ink-soft); }
.currentTag{ font-family:"IBM Plex Mono",monospace; font-size:9px; letter-spacing:.08em; text-transform:uppercase; color:var(--brass-bright); }

/* ---------- team page ---------- */
.teamPageHead{ display:flex; align-items:center; gap:12px; border-bottom:3px solid; padding-bottom:10px; margin-top:22px; }
.posterLink{ display:inline-block; margin-left:6px; font-size:12.5px; color:var(--brass-bright); text-decoration:none; border-bottom:1px dotted var(--brass); white-space:nowrap; }
.posterLink:hover{ border-bottom-style:solid; }
.teamReignList{ display:flex; flex-direction:column; gap:10px; margin-top:20px; }
.teamReignCard{ padding:14px 18px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:8px; }
.teamReignHead{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; }
.teamReignDates{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:16px; }
.teamReignDuration{ font-size:13px; color:var(--brass-bright); font-weight:600; }
.teamReignMeta{ display:flex; gap:16px; flex-wrap:wrap; margin-top:6px; font-size:13px; color:var(--ink-soft); }
.teamReignMeta a{ color:inherit; text-decoration:underline; text-decoration-color:var(--brass); }

/* ---------- map page ---------- */
.mapWrap{ margin:22px 0 6px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:10px; padding:14px; }
.mapSvg{ width:100%; height:auto; display:block; }
.mapState{ fill:var(--paper); stroke:var(--hairline); stroke-width:1; }
.mapState--1{ fill:var(--map-1); stroke:var(--brass-line); }
.mapState--2{ fill:var(--map-2); stroke:var(--brass-line); }
.mapState--3{ fill:var(--map-3); stroke:var(--brass-line); }
.mapState:hover{ stroke:var(--brass-bright); stroke-width:2; }
.mapLegend{ display:flex; flex-direction:column; margin:18px 0 8px; }
.mapLegendRow{ display:grid; grid-template-columns:110px auto 1fr; column-gap:14px; row-gap:2px; padding:9px 0; border-bottom:1px solid var(--hairline); font-size:13px; align-items:baseline; }
.mapLegendRow:last-child{ border-bottom:none; }
.mapLegendState{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:14.5px; }
.mapLegendCount{ color:var(--brass-bright); font-weight:600; font-size:12.5px; white-space:nowrap; }
.mapLegendTeams{ color:var(--ink-soft); grid-column:1 / 4; }
@media (min-width:640px){ .mapLegendTeams{ grid-column:3; } }

/* ---------- full history page ---------- */
.historyTop{ display:flex; flex-wrap:wrap; gap:16px 28px; align-items:flex-end; justify-content:space-between; margin:24px 0 8px; }
.historyStats{ display:flex; gap:22px; flex-wrap:wrap; }
.historyStats div{ display:flex; flex-direction:column; gap:2px; }
.historyStats .n{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:22px; }
.historyStats .l{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); }

.searchBox{ position:relative; }
.searchBox input{
  font-family:"IBM Plex Mono",monospace; font-size:13px; padding:9px 14px 9px 30px;
  border:1px solid var(--hairline); border-radius:20px; background:var(--paper-2); color:var(--ink);
  width:220px; max-width:60vw;
}
.searchBox input::placeholder{ color:var(--ink-soft); }
.searchBox svg{ position:absolute; left:10px; top:50%; transform:translateY(-50%); opacity:.55; pointer-events:none; }

.controls{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
.sortToggle{ display:flex; border:1px solid var(--hairline); border-radius:20px; overflow:hidden; background:var(--paper-2); }
.sortToggle button{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.03em; padding:8px 14px; border:none; background:transparent; color:var(--ink-soft); cursor:pointer; white-space:nowrap; }
.sortToggle button.active{ background:var(--brass); color:var(--paper); font-weight:600; }
.sortToggle button:not(.active):hover{ color:var(--ink); }

.dateSelect{ display:flex; gap:8px; }
.dateSelect select{
  font-family:"IBM Plex Mono",monospace; font-size:13px; padding:8px 12px;
  border:1px solid var(--hairline); border-radius:20px; background:var(--paper-2); color:var(--ink);
}
.todayBtn{
  font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.03em;
  padding:8px 14px; border:1px solid var(--hairline); border-radius:20px;
  background:var(--paper-2); color:var(--ink-soft); cursor:pointer;
}
.todayBtn:hover{ color:var(--ink); border-color:var(--brass); }

.records{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:26px 0 34px; }
@media (max-width:820px){ .records{ grid-template-columns:1fr; } }
.record-card{ padding:14px 16px; background:var(--paper-2); border-radius:2px 8px 8px 2px; border-left:3px solid var(--brass); }
.record-card .l{ font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); margin-bottom:5px; }
.record-card .v{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; }
.record-card .sub{ font-size:12.5px; color:var(--ink-soft); margin-top:2px; }

.tableScroll{ overflow-x:auto; }
table.reignsTable{ width:100%; border-collapse:collapse; font-size:14.5px; min-width:560px; }
table.reignsTable th{ text-align:left; font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); font-weight:600; padding:9px 10px; border-bottom:1px solid var(--brass-line); position:sticky; top:0; background:var(--paper); }
table.reignsTable td{ padding:10px; border-bottom:1px solid var(--hairline); vertical-align:middle; }
table.reignsTable td.num{ font-family:"IBM Plex Mono",monospace; color:var(--ink-soft); font-size:12.5px; }
table.reignsTable td.teamCell{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:16px; white-space:nowrap; }
table.reignsTable td.teamCell a{ text-decoration:none; }
table.reignsTable td.teamCell a:hover{ text-decoration:underline; text-decoration-color:var(--brass); }
table.reignsTable td.won a, table.reignsTable td.lost a{ text-decoration:none; border-bottom:1px dotted var(--ink-soft); }
table.reignsTable td.won a:hover, table.reignsTable td.lost a:hover{ border-bottom-color:var(--brass); color:var(--brass-bright); }
table.reignsTable td.tabular a{ text-decoration:none; color:inherit; border-bottom:1px dotted var(--ink-soft); }
table.reignsTable td.tabular a:hover{ border-bottom-color:var(--brass); color:var(--brass-bright); }
table.reignsTable td.dates, table.reignsTable td.won, table.reignsTable td.lost{ font-size:12.5px; color:var(--ink-soft); }
table.reignsTable td.tabular{ text-align:right; font-family:"IBM Plex Mono",monospace; }
table.reignsTable tr.current{ background: color-mix(in srgb, var(--brass) 10%, transparent); }
table.reignsTable tr.current td.teamCell{ color:var(--brass-bright); }
table.reignsTable tr.hiddenRow{ display:none; }
.reignChip{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:9px; vertical-align:middle; border:1px solid var(--hairline); }
.noResults{ padding:34px 0; text-align:center; color:var(--ink-soft); font-style:italic; display:none; }

.gameNav{ display:flex; gap:12px; margin:26px 0 0; font-family:"IBM Plex Mono",monospace; font-size:12px; }
.gameNav a{ text-decoration:none; color:var(--ink-soft); border:1px solid var(--hairline); border-radius:20px; padding:7px 16px; flex:1; }
.gameNav a:hover{ color:var(--ink); border-color:var(--brass); }
.gameNav a.next{ text-align:right; }
.gameNav a.disabled{ opacity:.35; pointer-events:none; }

/* all-games log page */
table.reignsTable td.matchup{ font-size:13.5px; }
table.reignsTable td.matchup a{ text-decoration:none; color:inherit; }
table.reignsTable td.matchup a:hover{ text-decoration:underline; text-decoration-color:var(--brass); }
table.reignsTable td.result{ font-size:12.5px; color:var(--ink-soft); white-space:nowrap; }
table.reignsTable td.result .win{ color:var(--brass-bright); font-weight:700; }
table.reignsTable tr.titleChange{ background: color-mix(in srgb, var(--brass) 7%, transparent); }
.viewToggle{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); margin:2px 0 0; }
.viewToggle a{ color:var(--brass-bright); text-decoration:none; border-bottom:1px dotted var(--brass); }
.viewToggle a:hover{ border-bottom-style:solid; }
""".strip()

# A short hash of the stylesheet's own content, appended to every
# <link href="styles.css"> as a cache-busting query string (?v=<hash>).
# Without this, a returning browser (or GitHub Pages' own CDN) can keep
# serving an OLD cached copy of styles.css under the same URL after a
# deploy changes it -- the HTML and JS update fine, but any new CSS rule
# (e.g. the Full History search filter's `.hiddenRow{ display:none }`)
# silently never applies, because the page never re-fetches the file it
# lives in. The hash changes automatically whenever CSS content changes,
# so every deploy gets a fresh URL and old cached copies are never reused.
STYLES_VERSION = hashlib.sha256(STYLES_CSS.encode("utf-8")).hexdigest()[:10]


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- headline

def build_headline(g):
    home, away = g["home"], g["away"]
    home_score, away_score = (int(x) for x in g["score"].split("-"))
    outcome = g["outcome"]
    new_holder = g["new_holder"]
    neutral = g["neutral"]
    new_holder_side = "home" if new_holder == home else "away"
    other_team = away if new_holder_side == "home" else home

    if outcome == "established":
        belt_tag = "Belt established"
        ns_score = home_score if new_holder_side == "home" else away_score
        other_score = away_score if new_holder_side == "home" else home_score
        if neutral:
            loc = f"at a neutral site over {esc(other_team)}"
        elif new_holder_side == "home":
            loc = f"at home over {esc(other_team)}"
        else:
            loc = f"on the road at {esc(other_team)}"
        headline = (f'{esc(new_holder)} <span class="win">establishes the belt</span>, '
                    f'{ns_score}–{other_score}, {loc} &mdash; the first game to '
                    f'ever have it on the line.')
        return headline, belt_tag

    if outcome == "retained (tie)":
        belt_tag = "Belt retained (tie)"
        suffix = " at a neutral site" if neutral else ""
        headline = (f'{esc(new_holder)} <span class="win">retains the belt</span> '
                     f'after a {home_score}–{away_score} tie with {esc(other_team)}{suffix}.')
        return headline, belt_tag

    verb = "takes" if outcome == "changed" else "defends"
    belt_tag = "Belt changed hands" if outcome == "changed" else "Belt defended"
    ns_score = home_score if new_holder_side == "home" else away_score
    other_score = away_score if new_holder_side == "home" else home_score
    if neutral:
        loc = f"at a neutral site over {esc(other_team)}"
    elif new_holder_side == "home":
        loc = f"at home over {esc(other_team)}"
    else:
        loc = f"on the road at {esc(other_team)}"
    headline = (f'{esc(new_holder)} <span class="win">{verb} the belt</span>, '
                f'{ns_score}–{other_score}, {loc}.')
    return headline, belt_tag


# ------------------------------------------------------------------ sections

def render_line_score(g):
    ls = g.get("line_score")
    if not ls:
        return ""
    home_periods, away_periods = ls["home"], ls["away"]
    n = len(home_periods)
    headers = "".join(f"<th>{ordinal_ot_label(i)}</th>" for i in range(n))
    home_score, away_score = (int(x) for x in g["score"].split("-"))
    winner_side = "home" if home_score > away_score else ("away" if away_score > home_score else None)

    def row(side, team, periods, final, is_winner):
        cells = "".join(f'<td class="tabular">{p}</td>' for p in periods)
        cls = ' class="winner"' if is_winner else ""
        return (f'<tr{cls}><td class="teamCell"><span class="swatch" '
                f'style="background:var(--{side})"></span>{esc(team)}</td>{cells}'
                f'<td class="final tabular">{final}</td></tr>')

    rows = row("home", g["home"], home_periods, home_score, winner_side == "home")
    rows += row("away", g["away"], away_periods, away_score, winner_side == "away")

    mismatch_note = ""
    if sum(home_periods) != home_score or sum(away_periods) != away_score:
        mismatch_note = (" The by-quarter total doesn’t quite match the final score above "
                          "— that’s a gap in CFBD’s own play-by-play record for this "
                          "game, not an error in this total.")

    return f'''
  <section>
    <div class="sectionHead">
      <span class="tag">By Quarter</span>
      <span class="rule"></span>
      <h2>Line score</h2>
    </div>
    <div style="overflow-x:auto">
      <table class="lineScore">
        <thead><tr><th class="teamCell">Team</th>{headers}<th>Final</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <p class="noteBox">Pulled directly from CFBD’s play-by-play record, the same source as every score in the lineage.{mismatch_note}</p>
  </section>'''


def render_team_stats(g):
    ts = g.get("team_stats")
    if not ts or "home" not in ts or "away" not in ts:
        return ""
    home_side, away_side = ts["home"], ts["away"]
    # defensive: skip rather than risk showing stats under the wrong team
    if home_side.get("team") and home_side["team"] != g["home"]:
        return ""
    if away_side.get("team") and away_side["team"] != g["away"]:
        return ""
    home_stats, away_stats = home_side.get("stats", {}), away_side.get("stats", {})

    rows = ""
    for label, key, kind in STAT_ROWS:
        h = fmt_stat(home_stats.get(key), kind)
        a = fmt_stat(away_stats.get(key), kind)
        if h == "—" and a == "—":
            continue
        rows += (f'<div class="statRow"><span class="label">{esc(label)}</span>'
                 f'<span class="val home tabular">{h}</span>'
                 f'<span class="val away tabular">{a}</span></div>')
    if not rows:
        return ""

    return f'''
  <section>
    <div class="sectionHead withTag">
      <span class="tag">Full Box Score</span>
      <span class="rule"></span>
      <h2>Team stats</h2>
      <span class="sourceTag">CFBD box score</span>
    </div>
    <div class="statGrid">
      <div class="statHead"><span>&nbsp;</span><span title="{esc(g['home'])}">{esc(g['home'])}</span><span title="{esc(g['away'])}">{esc(g['away'])}</span></div>
      {rows}
    </div>
    <p class="noteBox">Full team box score from CFBD’s <span class="mono">/games/teams</span> data. This section only appears for belt games from 2003 onward — CFBD doesn’t have team-level stats that far back.</p>
  </section>'''


# --------------------------------------------------------------- player stats

# Skill-position categories shown by default (lower-cased CFBD category names);
# everything else CFBD returns (defense, kicking, punting, returns, etc.)
# is folded into a collapsible "view more" section instead.
SKILL_STAT_CATEGORIES = ["passing", "rushing", "receiving"]

CATEGORY_LABELS = {
    "passing": "Passing",
    "rushing": "Rushing",
    "receiving": "Receiving",
    "defensive": "Defense",
    "fumbles": "Fumbles",
    "interceptions": "Interceptions",
    "kicking": "Kicking",
    "punting": "Punting",
    "kickreturns": "Kick Returns",
    "puntreturns": "Punt Returns",
}


def category_label(name):
    return CATEGORY_LABELS.get(name.lower(), name.replace("_", " ").title())


def render_stat_table(cat_name, cat_data, home_team):
    columns = cat_data.get("columns", [])
    rows = cat_data.get("rows", [])
    if not columns or not rows:
        return ""

    rows_sorted = sorted(rows, key=lambda r: 0 if r.get("team") == home_team else 1)

    head_cells = "".join(f"<th>{esc(c)}</th>" for c in columns)
    body_rows = ""
    for r in rows_sorted:
        stats = r.get("stats", {})
        cells = ""
        for c in columns:
            val = stats.get(c)
            cells += f'<td class="tabular">{"—" if val is None else esc(str(val))}</td>'
        body_rows += (f'<tr><td class="teamCell">{esc(r.get("player", ""))}'
                       f'<span class="playerTeam">{esc(r.get("team", ""))}</span></td>{cells}</tr>')

    return f'''
    <div class="statCategory">
      <h3>{esc(category_label(cat_name))}</h3>
      <table class="playerStatsTable">
        <thead><tr><th class="teamCell">Player</th>{head_cells}</tr></thead>
        <tbody>{body_rows}</tbody>
      </table>
    </div>'''


def render_player_stats(g):
    ps = g.get("player_stats")
    if not ps:
        return ""
    home = g["home"]

    skill_keys = [k for k in ps if k.lower() in SKILL_STAT_CATEGORIES]
    skill_keys.sort(key=lambda k: SKILL_STAT_CATEGORIES.index(k.lower()))
    other_keys = [k for k in ps if k.lower() not in SKILL_STAT_CATEGORIES]

    skill_html = "".join(render_stat_table(k, ps[k], home) for k in skill_keys)
    more_html = "".join(render_stat_table(k, ps[k], home) for k in other_keys)

    if not skill_html and not more_html:
        return ""

    more_block = ""
    if more_html:
        n = len(other_keys)
        label = f"Show more stats ({n} more categor{'y' if n == 1 else 'ies'})"
        more_block = f'''
    <details class="moreStats">
      <summary>{esc(label)}</summary>
      {more_html}
    </details>'''

    if not skill_html:
        skill_html = ('<p class="noteBox">No passing, rushing, or receiving stats recorded '
                       'for this game — see below for what CFBD does have.</p>')

    return f'''
  <section>
    <div class="sectionHead withTag">
      <span class="tag">Box Score</span>
      <span class="rule"></span>
      <h2>Player stats</h2>
      <span class="sourceTag">CFBD player stats</span>
    </div>
    {skill_html}
    {more_block}
    <p class="noteBox">Full player stats from CFBD’s <span class="mono">/games/players</span> data. This section only appears for belt games from 2003 onward — CFBD doesn’t have player-level stats that far back.</p>
  </section>'''


# --------------------------------------------------------------------- page

def render_page(g, colors, prev_game=None, next_game=None, total_games=None):
    home, away = g["home"], g["away"]
    home_score, away_score = (int(x) for x in g["score"].split("-"))
    home_primary, home_alt = team_color(colors, home)
    away_primary, away_alt = team_color(colors, away)
    home_ink, home_accent = panel_colors(home_primary, home_alt)
    away_ink, away_accent = panel_colors(away_primary, away_alt)

    new_holder = g["new_holder"]
    new_primary, new_alt = (home_primary, home_alt) if new_holder == home else (away_primary, away_alt)
    emph_light = emphasis_color(new_primary, new_alt, PAPER_LIGHT) or "var(--ink)"
    emph_dark = emphasis_color(new_primary, new_alt, PAPER_DARK) or "var(--ink)"

    headline, belt_tag = build_headline(g)
    date_str = fmt_date(g["date"])
    season_label = "Postseason" if g["season_type"] == "postseason" else f'Week {g["week"]} · Regular Season'

    meta_bits = [f"<span>{esc(date_str)}</span>", '<span class="dot"></span>', f"<span>{esc(season_label)}</span>"]
    if g["neutral"]:
        meta_bits += ['<span class="dot"></span>', '<span class="neutralTag mono">Neutral site</span>']
    meta_bits += [f'<span class="beltTag mono">{esc(belt_tag)}</span>']

    def side_label(team):
        """The pre-game holder is always 'Defending Holder' -- win, lose, or
        tie. The other side is 'New Holder' only if they actually took the
        belt this game (outcome == changed); otherwise they challenged and
        didn't take it, so they're just the 'Challenger' (true for both a
        loss and a tie, since a tie means the holder keeps the belt).
        The very first belt game has no defending holder at all (holder is
        None) -- there was no belt yet for anyone to defend."""
        if g["holder"] is None:
            return "First Champion" if g["new_holder"] == team else "First Opponent"
        if g["holder"] == team:
            return "Defending Holder"
        return "New Holder" if g["new_holder"] == team else "Challenger"

    home_defender = side_label(home)
    away_defender = side_label(away)

    title = f"{esc(away)} at {esc(home)} — Belt Game {g['game_number']}"

    def nav_link(game, cls, arrow_first):
        if game is None:
            arrow = "&larr; " if arrow_first else " &rarr;"
            label = "Start of the lineage" if arrow_first else "Present day"
            text = f"{arrow}{esc(label)}" if arrow_first else f"{esc(label)}{arrow}"
            return f'<a class="disabled {cls}">{text}</a>'
        matchup = f"{esc(game['away'])} at {esc(game['home'])}"
        arrow = "&larr; " if arrow_first else " &rarr;"
        text = f"{arrow}{matchup}" if arrow_first else f"{matchup}{arrow}"
        return f'<a class="{cls}" href="{game["game_id"]}.html">{text}</a>'

    game_nav = (f'<div class="gameNav">{nav_link(prev_game, "prev", True)}'
                f'{nav_link(next_game, "next", False)}</div>')

    body = f'''<meta charset="UTF-8">
<title>{title}</title>
<link rel="stylesheet" href="../styles.css?v={STYLES_VERSION}">
{head_extras('../')}
<style>
  :root{{
    --home:{home_primary}; --home-ink:{home_ink}; --home-accent:{home_accent};
    --away:{away_primary}; --away-ink:{away_ink}; --away-accent:{away_accent};
    --emph:{emph_light};
  }}
  @media (prefers-color-scheme: dark){{
    :root:not([data-theme="light"]){{ --emph:{emph_dark}; }}
  }}
  :root[data-theme="dark"]{{ --emph:{emph_dark}; }}
</style>

<header class="site wrap">
  <div class="headerRow">
    <a class="back" href="../index.html">&larr; The College Football Belt</a>
    <nav class="site" aria-label="Primary">
      <a href="../lineage.html">Full History</a>
      <a href="../all-games.html">All Games</a>
      <a href="../records.html">Records</a>
      <a href="../ruleset.html">Ruleset</a>
      <a href="../map.html">Map</a>
    </nav>
  </div>
  <div class="crumbTitle">Reign #{g['reign_number']} &middot; Game {g['game_number']:,} of {total_games:,}</div>
</header>

<main class="wrap">
  <div class="gameMeta">
    {' '.join(meta_bits)}
  </div>
  <h1 class="matchup">{headline}</h1>

  <div class="scoreboard">
    <div class="teamPanel home">
      <div class="panelTop">
        <span class="side">Home &middot; {home_defender}</span>
        {logo_img(colors, home, "teamLogo", 30)}
      </div>
      <span class="name">{esc(home)}</span>
      <span class="pts tabular">{home_score}</span>
    </div>
    <div class="vs">AT</div>
    <div class="teamPanel away">
      <div class="panelTop">
        <span class="side">Away &middot; {away_defender}</span>
        {logo_img(colors, away, "teamLogo", 30)}
      </div>
      <span class="name">{esc(away)}</span>
      <span class="pts tabular">{away_score}</span>
    </div>
  </div>
{render_recap(g)}
{render_line_score(g)}
{render_team_stats(g)}
{render_key_plays(g)}
{render_player_stats(g)}
{game_nav}
</main>

<footer class="wrap">
  <div class="footRow">
    <span>Part of the lineage since 1869. Score{" and line score" if g.get("line_score") else ""} sourced from the College Football Data API.</span>
    <nav aria-label="Footer">
      <a href="../index.html">Home</a>
      <a href="../lineage.html">Full History</a>
      <a href="../all-games.html">All Games</a>
      <a href="../records.html">Records</a>
      <a href="../ruleset.html">Ruleset</a>
      <a href="../map.html">Map</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>
'''
    return body


# --------------------------------------------------------------- homepage

CHAIN_LEN = 7  # how many recent reigns the "chain of custody" strip shows


def _reign_win_score(reign, change_index):
    """Winner-first (score_for, score_against) for how `reign`'s team took
    the belt, or (None, None) if this is the very first (originating)
    reign, which has no won_from."""
    if not reign.get("won_from"):
        return None, None
    g = change_index.get((reign["start_date"], reign["team"]))
    if not g:
        return None, None
    home_s, away_s = (int(x) for x in g["score"].split("-"))
    if g["home"] == reign["team"]:
        return home_s, away_s
    return away_s, home_s


def render_on_this_day(belt_games, today):
    """Belt games that happened on this exact month+day in a past year --
    free, computed entirely from data already on hand. Most years won't
    have one (the season only runs Aug-Jan); when none match, the section
    just doesn't render, same as every other optional widget on this site."""
    matches = [g for g in belt_games
               if date.fromisoformat(g["date"]).month == today.month
               and date.fromisoformat(g["date"]).day == today.day
               and date.fromisoformat(g["date"]) != today]
    if not matches:
        return ""
    matches.sort(key=lambda g: g["date"], reverse=True)

    rows = ""
    for g in matches[:3]:
        year = g["date"][:4]
        h, a = (int(x) for x in g["score"].split("-"))
        if g["outcome"] in ("changed", "established"):
            tag = f'<span class="otdTag changed">Belt changed hands</span>'
        else:
            tag = f'<span class="otdTag">Title defended</span>'
        loc_word = "vs." if g["neutral"] else "at"
        rows += f'''
      <a class="otdRow" href="games/{g["game_id"]}.html">
        <span class="otdYear tabular">{year}</span>
        <span class="otdMatchup">{esc(g["away"])} {loc_word} {esc(g["home"])} <span class="tabular">{a}&ndash;{h}</span></span>
        {tag}
      </a>'''

    plural = "s" if len(matches) != 1 else ""
    return f'''
  <section>
    <div class="sectionHead">
      <span class="tag">{esc(fmt_month_day(today))}</span>
      <span class="rule"></span>
      <h2>On this day in belt history</h2>
    </div>
    <p class="lede">{len(matches)} belt game{plural} on this date since 1869.</p>
    <div class="otdList">{rows}
    </div>
    <p class="viewToggle">Curious about a different date? <a href="on-this-day.html">Browse On
      This Day across all of belt history &rarr;</a></p>
  </section>'''


def generate_on_this_day_page(belt_games):
    """Standalone version of the homepage's "On this day" widget -- every
    belt game ever, tagged with its month/day, filtered entirely
    client-side against the VISITOR's own local date (not the build
    server's), with a month/day picker to browse any other date in belt
    history. Same data as everywhere else on the site, just reshaped --
    no new fetches."""
    rows = ""
    for g in sorted(belt_games, key=lambda g: g["date"], reverse=True):
        d = date.fromisoformat(g["date"])
        year = g["date"][:4]
        h, a = (int(x) for x in g["score"].split("-"))
        if g["outcome"] in ("changed", "established"):
            tag = '<span class="otdTag changed">Belt changed hands</span>'
        else:
            tag = '<span class="otdTag">Title defended</span>'
        loc_word = "vs." if g["neutral"] else "at"
        rows += f'''
      <a class="otdRow" data-month="{d.month}" data-day="{d.day}" href="games/{g["game_id"]}.html">
        <span class="otdYear tabular">{year}</span>
        <span class="otdMatchup">{esc(g["away"])} {loc_word} {esc(g["home"])} <span class="tabular">{a}&ndash;{h}</span></span>
        {tag}
      </a>'''

    month_options = "".join(f'<option value="{i}">{name}</option>' for i, name in enumerate(MONTH_NAMES, 1))
    day_options = "".join(f'<option value="{d}">{d}</option>' for d in range(1, 32))

    return f'''<meta charset="UTF-8">
<title>On This Day — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>
    <nav class="site" aria-label="Primary">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <h1 class="pageTitle">On This Day</h1>
  <p class="lede" id="otdLede">Every belt game that&rsquo;s ever happened on this date, across
    all 158 years of belt history.</p>

  <div class="historyTop">
    <div class="controls">
      <div class="dateSelect">
        <select id="monthSelect" aria-label="Month">{month_options}</select>
        <select id="daySelect" aria-label="Day">{day_options}</select>
      </div>
      <button type="button" class="todayBtn" id="todayBtn">Jump to today</button>
    </div>
  </div>

  <div class="otdList" id="otdList">{rows}
  </div>
  <p class="noResults" id="noResults">No belt games have ever landed on this date &mdash; the
    season only runs August&ndash;January, so a lot of the calendar is quiet.</p>
</main>

<footer class="wrap">
  <div class="footRow">
    <span>Every belt game computed from the College Football Data API.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>

<script>
(function(){{
  var monthSelect = document.getElementById('monthSelect');
  var daySelect = document.getElementById('daySelect');
  var todayBtn = document.getElementById('todayBtn');
  var rows = Array.prototype.slice.call(document.querySelectorAll('.otdRow'));
  var noResults = document.getElementById('noResults');
  var lede = document.getElementById('otdLede');
  var MONTHS = {json.dumps(MONTH_NAMES)};

  function applyFilter(){{
    var m = parseInt(monthSelect.value, 10);
    var d = parseInt(daySelect.value, 10);
    var shown = 0;
    rows.forEach(function(r){{
      var match = parseInt(r.getAttribute('data-month'), 10) === m && parseInt(r.getAttribute('data-day'), 10) === d;
      r.classList.toggle('hiddenRow', !match);
      if (match) shown++;
    }});
    noResults.style.display = shown === 0 ? 'block' : 'none';
    lede.textContent = shown + (shown === 1 ? ' belt game has' : ' belt games have') +
      ' happened on ' + MONTHS[m - 1] + ' ' + d + ' since 1869.';
  }}

  monthSelect.addEventListener('change', applyFilter);
  daySelect.addEventListener('change', applyFilter);
  todayBtn.addEventListener('click', function(){{
    var now = new Date();
    monthSelect.value = String(now.getMonth() + 1);
    daySelect.value = String(now.getDate());
    applyFilter();
  }});

  var now = new Date();
  monthSelect.value = String(now.getMonth() + 1);
  daySelect.value = String(now.getDate());
  applyFilter();
}})();
</script>
'''


def generate_homepage(lineage, colors, belt_games, next_game=None, upcoming_games=None):
    reigns = lineage["reigns"]
    totals = lineage["totals"]
    current = reigns[-1]
    holder = current["team"]
    change_index = build_change_game_index(belt_games)

    primary, alt = team_color(colors, holder)
    ink, accent = panel_colors(primary, alt)

    today = date.today()
    since_date = date.fromisoformat(current["start_date"])
    days_held = (today - since_date).days
    defenses = current["defenses"]
    team_reign_num = sum(1 for r in reigns if r["team"] == holder)
    years_span = today.year - 1869 + 1

    won_score, lost_score = _reign_win_score(current, change_index)
    won_from = current.get("won_from")

    sub_line = f"Since {fmt_date(current['start_date'])}"
    if won_from and won_score is not None:
        sub_line += f" &middot; def. {esc(won_from)}, {won_score}–{lost_score}"

    if won_from and won_score is not None:
        lede = (f"Won the belt from {esc(won_from)}, {won_score}–{lost_score}, "
                f"on {fmt_date(current['start_date'])}.")
    else:
        lede = f"Holds the belt since {fmt_date(current['start_date'])}."
    if defenses:
        lede += f" {defenses} defense{'s' if defenses != 1 else ''} since."

    # ---- up next: the current holder's next scheduled game, if CFBD's
    # released that far ahead -- mined for free out of the same season
    # fetch build_lineage.py already does, no extra API call ----
    next_game_html = ""
    if next_game and next_game.get("date"):
        opponent = next_game["opponent"]
        is_neutral = bool(next_game.get("neutral"))
        loc_word = "vs." if (next_game.get("is_home") or is_neutral) else "at"
        neutral_txt = " (neutral site)" if is_neutral else ""
        when_txt = ""
        try:
            game_date = date.fromisoformat(next_game["date"])
            days_until = (game_date - today).days
            if days_until == 0:
                when_txt = " &middot; Today"
            elif days_until == 1:
                when_txt = " &middot; Tomorrow"
            elif days_until > 1:
                when_txt = f" &middot; in {days_until} days"
        except ValueError:
            pass
        next_game_html = f'''
    <a class="nextGame" href="preview.html">
      <span class="nextGameTag">Up Next</span>
      <span class="nextGameText">{loc_word} <strong>{esc(opponent)}</strong>{neutral_txt} &middot; {fmt_date(next_game["date"])}{when_txt}</span>
    </a>'''

    # ---- belt watch: a short lookahead past the very next game, same free
    # schedule data -- only rendered when there's actually more than one
    # upcoming game on file ----
    belt_watch_html = ""
    later_games = (upcoming_games or [])[1:3]
    if later_games:
        chips = ""
        for g in later_games:
            loc = "vs." if (g.get("is_home") or g.get("neutral")) else "at"
            chips += (f'<span class="watchChip">{loc} <strong>{esc(g["opponent"])}</strong> '
                      f'&middot; {fmt_date(g["date"])}</span>')
        belt_watch_html = f'''
    <div class="beltWatch">
      <span class="beltWatchLabel">Belt Watch</span>
      {chips}
    </div>'''

    # ---- chain of custody: the last CHAIN_LEN reigns, oldest to newest ----
    chain_reigns = reigns[-CHAIN_LEN:]
    hidden_count = len(reigns) - len(chain_reigns)

    links_html = ""
    for r in chain_reigns:
        is_current = r is current
        p, a = team_color(colors, r["team"])
        _, chip_accent = panel_colors(p, a)
        w, l = _reign_win_score(r, change_index)
        beat = f"def. {esc(r['won_from'])}, {w}–{l}" if (r.get("won_from") and w is not None) else "Established the belt"

        win_game = change_index.get((r["start_date"], r["team"]))
        game_id = win_game["game_id"] if win_game else None

        if is_current:
            right_meta = f'<span class="tabular">{defenses} def.</span>'
            now_badge = '<span class="now">Current</span>'
            cls = " current"
        else:
            right_meta = f"&rarr; {esc(team_chip(r['lost_to']))}" if r.get("lost_to") else ""
            now_badge = ""
            cls = ""

        tag = "a" if game_id else "div"
        href_attr = f' href="games/{game_id}.html"' if game_id else ""

        links_html += f'''
      <{tag} class="link{cls}"{href_attr}>
        {now_badge}
        <div class="chip" style="background:{p};color:{chip_accent}">{esc(team_chip(r["team"]))}</div>
        <div class="team">{esc(r["team"])}</div>
        <div class="beat">{beat}</div>
        <div class="meta"><span>{esc(r["start_date"])}</span><span>{right_meta}</span></div>
      </{tag}>'''

    chain_lead = ""
    if hidden_count > 0:
        chain_lead = (f'<div class="chainLead">&larr; {hidden_count:,} earlier '
                       f'reign{"s" if hidden_count != 1 else ""}<br>since 1869</div>')

    monogram = esc(team_chip(holder))

    share_desc = esc(f"{lede} {years_span} years, {totals.get('reigns', '')} reigns.".strip())
    return f'''<meta charset="UTF-8">
<title>The College Football Belt</title>
<meta name="description" content="{share_desc}">
<meta property="og:title" content="The College Football Belt">
<meta property="og:description" content="{share_desc}">
<meta property="og:image" content="{SITE_URL}/share.png">
<meta property="og:url" content="{SITE_URL}/">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="The College Football Belt">
<meta name="twitter:description" content="{share_desc}">
<meta name="twitter:image" content="{SITE_URL}/share.png">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}
<style>
  :root{{
    --holder:{primary}; --holder-alt:{accent}; --holder-ink:{ink};
  }}
</style>

<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>
    <nav class="site" aria-label="Primary">
      <a href="#lineage">Lineage</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
      <a href="#numbers">By the Numbers</a>
    </nav>
  </div>
  <p class="tagline">The title that has passed hand to hand, on the field, since Rutgers beat Princeton 6&ndash;4 on November&nbsp;6, 1869. No committee, no poll &mdash; you have to take it from whoever&rsquo;s holding it.</p>
</header>

<main class="wrap">

  <section class="hero">
    <div>
      <h1>{esc(holder)} holds the belt.</h1>
      <p class="lede">{lede}</p>
      {next_game_html}
      {belt_watch_html}
      <div class="heroFacts">
        <div><span class="n tabular">{days_held:,}</span><span class="l">Days Held</span></div>
        <div><span class="n tabular">{defenses}</span><span class="l">Defenses</span></div>
        <div><span class="n tabular">1869</span><span class="l">Belt Established</span></div>
      </div>
    </div>

    <div class="plateWrap">
      <div class="plate">
        <div class="rim"></div>
        <div class="monogram">{monogram}</div>
        <div class="eyebrow2">Current Holder</div>
        <div class="holderName"><a href="teams/{team_slug(holder)}.html">{esc(holder)}</a></div>
        <div class="sub">{sub_line}</div>
        <div class="plateStats">
          <div><span class="n tabular">{days_held:,}</span><span class="l">Days</span></div>
          <div><span class="n tabular">{defenses}</span><span class="l">Defenses</span></div>
          <div><span class="n tabular">{ordinal(team_reign_num)}</span><span class="l">{esc(holder)} Reign</span></div>
        </div>
      </div>
    </div>
  </section>

  <section id="lineage">
    <div class="sectionHead">
      <span class="tag">Chain of Custody</span>
      <span class="rule"></span>
      <h2>How it got here</h2>
    </div>
    <div class="chain">
      {chain_lead}
      {links_html}
    </div>
    <p class="rulesFoot"><a href="lineage.html">View the full history &mdash; all {len(reigns)} reigns &rarr;</a></p>
  </section>
{render_on_this_day(belt_games, today)}
  <section id="ruleset">
    <div class="sectionHead">
      <span class="tag">The Ruleset</span>
      <span class="rule"></span>
      <h2>How the belt works</h2>
    </div>
    <div class="rules">
      <div class="rule-card">
        <h3>Won on the field</h3>
        <p>Beat the holder, take the belt. Every other result leaves it exactly where it was.</p>
      </div>
      <div class="rule-card">
        <h3>Ties: holder retains</h3>
        <p>Standard lineal convention. A tie isn&rsquo;t a loss, so it isn&rsquo;t treated like one.</p>
      </div>
      <div class="rule-card">
        <h3>Idle holder, belt carries</h3>
        <p>A bye, a canceled season, a bowl opt-out &mdash; the belt just waits for the next game.</p>
      </div>
      <div class="rule-card">
        <h3>Computed, not researched</h3>
        <p>Every reign is derived mechanically from the full game record &mdash; no editorial judgment per game.</p>
      </div>
    </div>
    <p class="rulesFoot"><a href="ruleset.html">Read the full ruleset, with sourcing notes &rarr;</a></p>
  </section>

  <section id="numbers" class="band" style="margin-inline:calc(50% - 50vw)">
    <div class="wrap">
      <div class="bandGrid">
        <div><div class="n tabular">{totals["belt_games"]:,}</div><div class="l">Belt Games</div></div>
        <div><div class="n tabular">{totals["reigns"]:,}</div><div class="l">Reigns</div></div>
        <div><div class="n tabular">{totals["distinct_teams"]:,}</div><div class="l">Programs</div></div>
        <div><div class="n tabular">{years_span}</div><div class="l">Years, 1869&ndash;Present</div></div>
      </div>
    </div>
  </section>

</main>

<footer class="wrap">
  <div class="footRow">
    <span>Every belt game sourced from the College Football Data API. Colors shown are the current holder&rsquo;s &mdash; the plate above recolors itself with every change of hands.</span>
    <nav aria-label="Footer">
      <a href="#lineage">Lineage</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>
'''


# ---------------------------------------------------------- full history page

def generate_lineage_page(lineage, colors, belt_games):
    reigns = lineage["reigns"]
    totals = lineage["totals"]
    current = reigns[-1]
    change_index = build_change_game_index(belt_games)
    today = date.today()

    # ---- records: computed, not curated -- same ethos as the rest of the site
    durations = [(r, reign_duration_days(r, today)) for r in reigns]
    longest_reign, longest_days = max(durations, key=lambda p: p[1])
    most_defended = max(reigns, key=lambda r: r["defenses"])
    reign_counts = Counter(r["team"] for r in reigns)
    most_reigns_team, most_reigns_n = reign_counts.most_common(1)[0]

    def game_link_for(reign):
        g = change_index.get((reign["start_date"], reign["team"]))
        return g["game_id"] if g else None

    records_html = f'''
    <div class="record-card">
      <div class="l">Longest Reign</div>
      <div class="v">{esc(longest_reign["team"])} &mdash; {fmt_duration(*reign_dates(longest_reign, today))}</div>
      <div class="sub">{fmt_date(longest_reign["start_date"])} &ndash; {fmt_date(longest_reign["end_date"]) if longest_reign.get("end_date") else "present"}</div>
    </div>
    <div class="record-card">
      <div class="l">Most Defenses, One Reign</div>
      <div class="v">{esc(most_defended["team"])} &mdash; {most_defended["defenses"]}</div>
      <div class="sub">starting {fmt_date(most_defended["start_date"])}</div>
    </div>
    <div class="record-card">
      <div class="l">Most Reigns, All-Time</div>
      <div class="v">{esc(most_reigns_team)} &mdash; {most_reigns_n} separate reign{"s" if most_reigns_n != 1 else ""}</div>
      <div class="sub">won the belt back {most_reigns_n - 1} time{"s" if most_reigns_n - 1 != 1 else ""} after losing it</div>
    </div>'''

    rows_html = ""
    for i, r in enumerate(reigns, 1):
        is_current = r is current
        team = r["team"]
        p, a = team_color(colors, team)
        w, l = _reign_win_score(r, change_index)
        gid = game_link_for(r)

        team_html = f'<a href="games/{gid}.html">{esc(team)}</a>' if gid else esc(team)
        won_inner = f"def. {esc(r['won_from'])} {w}&ndash;{l}" if (r.get("won_from") and w is not None) else "Established the belt"
        won_txt = f'<a href="games/{gid}.html">{won_inner}</a>' if gid else won_inner

        next_reign = reigns[i] if i < len(reigns) else None
        lost_gid = game_link_for(next_reign) if next_reign else None
        if is_current:
            lost_txt = '<span class="mono">— present —</span>'
            end_txt = "Present"
        elif r.get("lost_to"):
            lost_inner = f"to {esc(r['lost_to'])}"
            lost_txt = f'<a href="games/{lost_gid}.html">{lost_inner}</a>' if lost_gid else lost_inner
            end_txt = fmt_date(r["end_date"])
        else:
            lost_txt = "—"
            end_txt = fmt_date(r["end_date"]) if r.get("end_date") else "—"

        cls = " current" if is_current else ""
        rows_html += f'''
        <tr class="{cls.strip()}" data-team="{esc(team.lower())}">
          <td class="num">{i}</td>
          <td class="teamCell"><span class="reignChip" style="background:{p}"></span>{team_html}</td>
          <td class="dates">{fmt_date(r["start_date"])} &ndash; {end_txt}</td>
          <td class="tabular">{fmt_duration(*reign_dates(r, today))}</td>
          <td class="tabular">{r["defenses"]}</td>
          <td class="won">{won_txt}</td>
          <td class="lost">{lost_txt}</td>
        </tr>'''

    return f'''<meta charset="UTF-8">
<title>Full History — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>
    <nav class="site" aria-label="Primary">
      <a href="index.html">Home</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
      <a href="index.html#numbers">By the Numbers</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <h1 class="pageTitle">The Full History</h1>
  <p class="lede">Every reign since Rutgers beat Princeton on November&nbsp;6, 1869 &mdash;
    {totals["reigns"]:,} of them, computed from {totals["belt_games"]:,} belt games across
    {totals["distinct_teams"]} programs. Type a team name to filter; tap a team to jump to the
    game that won it.</p>

  <div class="historyTop">
    <div class="historyStats">
      <div><span class="n tabular">{totals["reigns"]:,}</span><span class="l">Reigns</span></div>
      <div><span class="n tabular">{totals["belt_games"]:,}</span><span class="l">Belt Games</span></div>
      <div><span class="n tabular">{totals["distinct_teams"]}</span><span class="l">Programs</span></div>
    </div>
    <div class="controls">
      <div class="sortToggle" role="group" aria-label="Sort order">
        <button type="button" class="sortBtn active" data-order="asc">Oldest First</button>
        <button type="button" class="sortBtn" data-order="desc">Newest First</button>
      </div>
      <div class="searchBox">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/></svg>
        <input id="teamSearch" type="text" placeholder="Filter by team&hellip;" autocomplete="off">
      </div>
    </div>
  </div>

  <div class="records">{records_html}
  </div>

  <div class="tableScroll">
    <table class="reignsTable">
      <thead>
        <tr>
          <th>#</th><th>Team</th><th>Reign</th><th style="text-align:right">Length</th>
          <th style="text-align:right">Def.</th><th>Won it</th><th>Lost it</th>
        </tr>
      </thead>
      <tbody>{rows_html}
      </tbody>
    </table>
    <p class="noResults" id="noResults">No reigns match &ldquo;<span id="noResultsTerm"></span>.&rdquo;</p>
  </div>
  <p class="viewToggle">Want every individual game, defenses included &mdash;
    not just who won each reign? <a href="all-games.html">See the full game log &rarr;</a></p>
</main>

<footer class="wrap">
  <div class="footRow">
    <span>Every reign computed from the College Football Data API.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>

<script>
(function(){{
  var input = document.getElementById('teamSearch');
  var tbody = document.querySelector('table.reignsTable tbody');
  var rows = Array.prototype.slice.call(document.querySelectorAll('table.reignsTable tbody tr'));
  var noResults = document.getElementById('noResults');
  var noResultsTerm = document.getElementById('noResultsTerm');
  input.addEventListener('input', function(){{
    var q = input.value.trim().toLowerCase();
    var shown = 0;
    rows.forEach(function(r){{
      var name = r.getAttribute('data-team') || '';
      var match = !q || name.indexOf(q) !== -1;
      r.classList.toggle('hiddenRow', !match);
      if (match) shown++;
    }});
    noResultsTerm.textContent = input.value.trim();
    noResults.style.display = (shown === 0 && q) ? 'block' : 'none';
  }});

  var STORAGE_KEY = 'cfbBelt:lineageSortOrder';
  var sortBtns = Array.prototype.slice.call(document.querySelectorAll('.sortToggle .sortBtn'));
  function applyOrder(order){{
    var ordered = order === 'desc' ? rows.slice().reverse() : rows.slice();
    ordered.forEach(function(r){{ tbody.appendChild(r); }});
    sortBtns.forEach(function(b){{ b.classList.toggle('active', b.getAttribute('data-order') === order); }});
    try {{ localStorage.setItem(STORAGE_KEY, order); }} catch(e) {{}}
  }}
  sortBtns.forEach(function(b){{
    b.addEventListener('click', function(){{ applyOrder(b.getAttribute('data-order')); }});
  }});
  var savedOrder = null;
  try {{ savedOrder = localStorage.getItem(STORAGE_KEY); }} catch(e) {{}}
  if (savedOrder === 'desc') applyOrder('desc');
}})();
</script>
'''


# -------------------------------------------------------------- all games page

def generate_all_games_page(lineage, colors, belt_games):
    """Every belt game, one row each -- title changes AND defenses, unlike
    the Full History page above which only has one row per reign (the game
    where it STARTED). Reuses the same reignsTable/searchBox/historyTop
    CSS and search-filter JS as generate_lineage_page for a consistent look,
    just with a different (game-shaped, not reign-shaped) column set."""
    totals = lineage["totals"]
    # "established" (the very first belt game, which put the title up in the
    # first place) is folded into title_changes for this stat band -- it's
    # not a "defense" of anything, and folding it in keeps title_changes +
    # defenses_total == len(belt_games) exactly, and title_changes == the
    # total reign count on the Full History page (328, not 327).
    title_changes = sum(1 for g in belt_games if g["outcome"] in ("changed", "established"))
    defenses_total = len(belt_games) - title_changes

    rows_html = ""
    defense_no = 0
    for g in belt_games:
        home, away = g["home"], g["away"]
        home_score, away_score = (int(x) for x in g["score"].split("-"))
        outcome = g["outcome"]
        is_last = g is belt_games[-1]

        if outcome == "established":
            defense_no = 0
            result_html = f'<span class="win">Belt established: {esc(g["new_holder"])}</span>'
        elif outcome == "changed":
            defense_no = 0
            result_html = f'<span class="win">New champion: {esc(g["new_holder"])}</span>'
        else:
            defense_no += 1
            tie_note = " (tie)" if outcome == "retained (tie)" else ""
            result_html = f'Defended{tie_note} &middot; #{defense_no}'

        loc_word = "vs." if g["neutral"] else "at"
        matchup_text = f'{esc(away)} {loc_word} {esc(home)}'
        matchup_html = f'<a href="games/{g["game_id"]}.html">{matchup_text}</a>'

        cls_bits = []
        if outcome in ("changed", "established"):
            cls_bits.append("titleChange")
        if is_last:
            cls_bits.append("current")
        cls = " ".join(cls_bits)

        rows_html += f'''
        <tr class="{cls}" data-team="{esc(home.lower())} {esc(away.lower())}">
          <td class="num">{g["game_number"]:,}</td>
          <td class="dates">{fmt_date(g["date"])}</td>
          <td class="matchup">{matchup_html}</td>
          <td class="tabular"><a href="games/{g["game_id"]}.html">{away_score}&ndash;{home_score}</a></td>
          <td class="result">{result_html}</td>
        </tr>'''

    return f'''<meta charset="UTF-8">
<title>All Games — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>
    <nav class="site" aria-label="Primary">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
      <a href="index.html#numbers">By the Numbers</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <h1 class="pageTitle">Every Belt Game</h1>
  <p class="lede">Every game with the belt on the line since Rutgers beat Princeton on
    November&nbsp;6, 1869 &mdash; {len(belt_games):,} of them: {title_changes:,} title changes
    and {defenses_total:,} successful defenses, across {totals["distinct_teams"]} programs.
    Type a team name to filter; tap any game to open its page.</p>

  <div class="historyTop">
    <div class="historyStats">
      <div><span class="n tabular">{len(belt_games):,}</span><span class="l">Total Games</span></div>
      <div><span class="n tabular">{title_changes:,}</span><span class="l">Title Changes</span></div>
      <div><span class="n tabular">{defenses_total:,}</span><span class="l">Defenses</span></div>
    </div>
    <div class="controls">
      <div class="sortToggle" role="group" aria-label="Sort order">
        <button type="button" class="sortBtn active" data-order="asc">Oldest First</button>
        <button type="button" class="sortBtn" data-order="desc">Newest First</button>
      </div>
      <div class="searchBox">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/></svg>
        <input id="teamSearch" type="text" placeholder="Filter by team&hellip;" autocomplete="off">
      </div>
    </div>
  </div>

  <div class="tableScroll">
    <table class="reignsTable">
      <thead>
        <tr>
          <th>#</th><th>Date</th><th>Matchup</th><th style="text-align:right">Score</th><th>Result</th>
        </tr>
      </thead>
      <tbody>{rows_html}
      </tbody>
    </table>
    <p class="noResults" id="noResults">No games match &ldquo;<span id="noResultsTerm"></span>.&rdquo;</p>
  </div>
  <p class="viewToggle">Looking for the reign-by-reign summary instead?
    <a href="lineage.html">See the Full History page &rarr;</a></p>
</main>

<footer class="wrap">
  <div class="footRow">
    <span>Every game computed from the College Football Data API.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>

<script>
(function(){{
  var input = document.getElementById('teamSearch');
  var tbody = document.querySelector('table.reignsTable tbody');
  var rows = Array.prototype.slice.call(document.querySelectorAll('table.reignsTable tbody tr'));
  var noResults = document.getElementById('noResults');
  var noResultsTerm = document.getElementById('noResultsTerm');
  input.addEventListener('input', function(){{
    var q = input.value.trim().toLowerCase();
    var shown = 0;
    rows.forEach(function(r){{
      var name = r.getAttribute('data-team') || '';
      var match = !q || name.indexOf(q) !== -1;
      r.classList.toggle('hiddenRow', !match);
      if (match) shown++;
    }});
    noResultsTerm.textContent = input.value.trim();
    noResults.style.display = (shown === 0 && q) ? 'block' : 'none';
  }});

  var STORAGE_KEY = 'cfbBelt:allGamesSortOrder';
  var sortBtns = Array.prototype.slice.call(document.querySelectorAll('.sortToggle .sortBtn'));
  function applyOrder(order){{
    var ordered = order === 'desc' ? rows.slice().reverse() : rows.slice();
    ordered.forEach(function(r){{ tbody.appendChild(r); }});
    sortBtns.forEach(function(b){{ b.classList.toggle('active', b.getAttribute('data-order') === order); }});
    try {{ localStorage.setItem(STORAGE_KEY, order); }} catch(e) {{}}
  }}
  sortBtns.forEach(function(b){{
    b.addEventListener('click', function(){{ applyOrder(b.getAttribute('data-order')); }});
  }});
  var savedOrder = null;
  try {{ savedOrder = localStorage.getItem(STORAGE_KEY); }} catch(e) {{}}
  if (savedOrder === 'desc') applyOrder('desc');
}})();
</script>
'''


# ------------------------------------------------------------ preview page

def render_recent_form(team, games):
    if not games:
        return f'<p class="emptyNote">No recent completed games on record for {esc(team)}.</p>'
    items = ""
    for g in games:
        cls = "t" if g.get("tied") else ("w" if g["won"] else "l")
        letter = "T" if g.get("tied") else ("W" if g["won"] else "L")
        loc = "vs" if g["home"] else "at"
        date_txt = fmt_date(g["date"][:10]) if g.get("date") else ""
        items += f'''
        <li>
          <span class="formBadge {cls}">{letter}</span>
          <span class="formScore">{g["score_for"]}&ndash;{g["score_against"]}</span>
          <span class="formOpp">{loc} {esc(g["opponent"])}</span>
          <span class="formDate">{date_txt}</span>
        </li>'''
    return f'<ul class="formList">{items}\n      </ul>'


def render_head_to_head(holder, opponent, h2h):
    if not h2h:
        return '<p class="emptyNote">No head-to-head data available.</p>'
    t1w = h2h.get("team1_wins", 0) or 0
    t2w = h2h.get("team2_wins", 0) or 0
    ties = h2h.get("ties", 0) or 0
    if t1w + t2w + ties == 0:
        return (f'<p class="emptyNote">{esc(holder)} and {esc(opponent)} have '
                f'no recorded meetings in CFBD&rsquo;s data.</p>')

    tie_txt = f"&ndash;{ties}" if ties else ""
    if t1w > t2w:
        headline = f"{esc(holder)} leads the series {t1w}&ndash;{t2w}{tie_txt}"
    elif t2w > t1w:
        headline = f"{esc(opponent)} leads the series {t2w}&ndash;{t1w}{tie_txt}"
    else:
        headline = f"Series tied {t1w}&ndash;{t2w}{tie_txt}"
    since = f" (since {h2h['start_year']})" if h2h.get("start_year") else ""

    rows = ""
    for m in (h2h.get("games") or [])[:8]:
        season = m.get("season") or "&mdash;"
        home_t, away_t = m.get("home_team") or "?", m.get("away_team") or "?"
        hs, as_ = m.get("home_score"), m.get("away_score")
        score_txt = f"{as_}&ndash;{hs}" if (hs is not None and as_ is not None) else "&mdash;"
        loc_word = "vs." if m.get("neutral") else "at"
        rows += f'''
        <tr>
          <td class="num">{season}</td>
          <td class="teamCell">{esc(away_t)} {loc_word} {esc(home_t)}</td>
          <td class="tabular">{score_txt}</td>
        </tr>'''

    table_html = ""
    if rows:
        table_html = f'''
    <div class="tableScroll">
      <table class="reignsTable">
        <thead><tr><th>Season</th><th>Matchup</th><th style="text-align:right">Score</th></tr></thead>
        <tbody>{rows}
        </tbody>
      </table>
    </div>'''

    return f'<p class="h2hHeadline">{headline}{since}</p>{table_html}'


def render_weather(weather):
    """A small kickoff-forecast card -- gracefully renders nothing when
    fetch_weather.py had no venue coordinates, the game's too far out for
    Open-Meteo's forecast window, or the fetch itself failed."""
    if not weather or weather.get("temp_f") is None:
        return ""
    temp = round(weather["temp_f"])
    feels = weather.get("feels_like_f")
    feels_bit = f" (feels {round(feels)}&deg;)" if feels is not None and round(feels) != temp else ""
    condition = weather.get("condition") or ""
    wind = weather.get("wind_mph")
    precip = weather.get("precip_chance")
    where = weather.get("venue_name")
    where_bit = f" at {esc(where)}" if where else ""

    bits = []
    if wind is not None:
        bits.append(f"Wind {round(wind)} mph")
    if precip is not None:
        bits.append(f"{round(precip)}% chance of precipitation")
    sub = " &middot; ".join(bits)

    return f'''
  <div class="sectionHead withTag">
    <span class="tag">Forecast</span>
    <span class="rule"></span>
    <h2>Kickoff Weather</h2>
  </div>
  <div class="weatherCard">
    <span class="weatherTemp tabular">{temp}&deg;F{feels_bit}</span>
    <span class="weatherCond">{esc(condition)}{where_bit}</span>
    {f'<span class="weatherSub">{sub}</span>' if sub else ''}
  </div>
  <p class="emptyNote">Forecast as of {weather.get("fetched", "recently")} &mdash; weather this far out can change; treat it as a rough guide, not a promise.</p>'''


def generate_preview_page(next_game, matchup, ai_preview, weather, colors):
    nav = '''
    <nav class="site" aria-label="Primary">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
    </nav>'''
    header = f'''<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>{nav}
  </div>
</header>'''
    footer = f'''<footer class="wrap">
  <div class="footRow">
    <span>Recent form and head-to-head from the College Football Data API.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>'''

    if not next_game:
        return f'''<meta charset="UTF-8">
<title>Up Next — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{header}

<main class="wrap">
  <h1 class="pageTitle">No Upcoming Game Yet</h1>
  <p class="lede">The current holder&rsquo;s next game hasn&rsquo;t shown up in CollegeFootballData&rsquo;s
    records yet &mdash; check back soon.</p>
</main>

{footer}
'''

    holder = next_game["team"]
    opponent = next_game["opponent"]
    if next_game.get("neutral"):
        side_word, side_full = "vs.", "faces (neutral site)"
    elif next_game.get("is_home"):
        side_word, side_full = "vs.", "hosts"
    else:
        side_word, side_full = "at", "travels to"

    title = f"{esc(holder)} {side_word} {esc(opponent)}"

    matchup = matchup or {}
    recent = matchup.get("recent_form") or {}
    holder_form_html = render_recent_form(holder, recent.get(holder, []))
    opp_form_html = render_recent_form(opponent, recent.get(opponent, []))
    h2h_html = render_head_to_head(holder, opponent, matchup.get("head_to_head"))

    ai_preview = ai_preview or {}
    overview = ai_preview.get("overview") or ""
    key_matchups = ai_preview.get("key_matchups") or []
    betting = ai_preview.get("betting_angles") or ""
    predicted_winner = ai_preview.get("predicted_winner") or ""
    predicted_score = ai_preview.get("predicted_score") or ""
    prediction_writeup = ai_preview.get("prediction_writeup") or ""

    ai_html = ""
    if overview or key_matchups or betting or predicted_winner or prediction_writeup:
        matchups_html = "".join(f"<li>{esc(m)}</li>" for m in key_matchups)
        betting_html = f'<p class="bettingHead">Betting Angles</p><p>{esc(betting)}</p>' if betting else ""
        matchups_block = f'<ul class="keyMatchups">{matchups_html}</ul>' if matchups_html else ""

        preview_block = ""
        if overview or matchups_block or betting_html:
            preview_block = f'''
  <div class="sectionHead withTag">
    <span class="tag">AI-Written</span>
    <span class="rule"></span>
    <h2>Game Preview</h2>
  </div>
  <div class="aiPreviewBody">
    <p>{esc(overview)}</p>
    {matchups_block}
    {betting_html}
  </div>'''

        prediction_block = ""
        if predicted_winner or prediction_writeup:
            if predicted_score:
                call_line = f'<span class="tabular">{esc(predicted_score)}</span>'
            elif predicted_winner:
                call_line = f'{esc(predicted_winner)} to win'
            else:
                call_line = "No clear pick"
            prediction_block = f'''
  <div class="sectionHead withTag">
    <span class="tag">AI-Written</span>
    <span class="rule"></span>
    <h2>Prediction</h2>
  </div>
  <div class="aiPreviewBody predictionBody">
    <p class="predictionCall">{call_line}</p>
    <p>{esc(prediction_writeup)}</p>
  </div>'''

        ai_html = f'''{preview_block}{prediction_block}
  <p class="noteBox">Written by Claude from the stats (and forecast, when available) on this page
    &mdash; a for-fun editorial call, not betting advice or a guarantee. If it stops being fun, the
    National Problem Gambling Helpline is 1-800-522-4700.</p>'''

    weather_html = render_weather(weather)

    return f'''<meta charset="UTF-8">
<title>{title} Preview — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{header}

<main class="wrap">
  <h1 class="pageTitle">Up Next: {title}</h1>
  <p class="previewMeta">{esc(holder)} {esc(side_full)} {esc(opponent)} &middot; {fmt_date(next_game["date"])} &middot; the belt is on the line</p>
  <p class="kickoffLocal" id="kickoffLocal" data-utc="{esc(next_game.get('raw_date') or '')}" hidden></p>
  <script>
  (function(){{
    var el = document.getElementById('kickoffLocal');
    var raw = el && el.getAttribute('data-utc');
    if (!raw) return;
    var d = new Date(raw);
    if (isNaN(d.getTime())) return;
    try {{
      var fmt = new Intl.DateTimeFormat(undefined, {{
        weekday: 'short', month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit', timeZoneName: 'short'
      }});
      el.textContent = 'Kickoff: ' + fmt.format(d) + ' your time';
      el.hidden = false;
    }} catch (e) {{}}
  }})();
  </script>
{weather_html}
  <div class="sectionHead withTag">
    <span class="tag">Recent Form</span>
    <span class="rule"></span>
    <h2>Last 5 Games</h2>
  </div>
  <div class="formGrid">
    <div class="formCol">
      <h3>{logo_img(colors, holder, "teamLogo", 22)}{esc(holder)}</h3>
      {holder_form_html}
    </div>
    <div class="formCol">
      <h3>{logo_img(colors, opponent, "teamLogo", 22)}{esc(opponent)}</h3>
      {opp_form_html}
    </div>
  </div>

  <div class="sectionHead withTag">
    <span class="tag">Head-to-Head</span>
    <span class="rule"></span>
    <h2>All-Time Series</h2>
  </div>
  {h2h_html}
{ai_html}
</main>

{footer}
'''


# --------------------------------------------------------------- ruleset page

RULESET_MD_PATH = "ruleset.md"


def inline_md(text):
    """Escape text, then turn **bold** / *italic* into real tags. Order
    matters -- escape the raw text first, then add trusted markup on top,
    and resolve **bold** before single *italic* so a bold span's asterisks
    aren't half-eaten by the italic pattern first."""
    t = esc(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    return t


def parse_ruleset_md(md_text):
    """A tiny, purpose-built parser for this one file's shape (H1 title,
    H2 sections, plain paragraphs, `- ` bullet lists, and whole-paragraph
    *asides* used for status/rationale notes) -- not a general markdown
    parser. Keeps ruleset.md as the single source of truth for the page
    instead of hand-copying its prose into a second, driftable place."""
    blocks = re.split(r"\n\s*\n+", md_text.strip())
    sections = []
    current = {"heading": None, "body": []}

    def flush():
        if current["heading"] is not None or current["body"]:
            sections.append(dict(current))

    for raw in blocks:
        block = raw.strip()
        if not block:
            continue
        if block.startswith("# "):
            continue  # the page has its own masthead; skip the markdown H1
        if block.startswith("## "):
            flush()
            current = {"heading": block[3:].strip(), "body": []}
            continue
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if lines and lines[0].startswith("- "):
            # a list item can wrap onto following lines with no "- " of its
            # own (just indentation in the source) -- fold those into the
            # item they continue rather than treating them as new items
            items, item = [], None
            for l in lines:
                if l.startswith("- "):
                    if item is not None:
                        items.append(item)
                    item = l[2:].strip()
                elif item is not None:
                    item += " " + l
            if item is not None:
                items.append(item)
            current["body"].append(("list", [inline_md(it) for it in items]))
            continue
        joined = " ".join(lines)
        if joined.startswith("*") and not joined.startswith("**") and joined.endswith("*"):
            current["body"].append(("aside", inline_md(joined[1:-1])))
        else:
            current["body"].append(("p", inline_md(joined)))
    flush()
    return sections


def render_ruleset_body(text_type, content):
    if text_type == "p":
        return f"<p>{content}</p>"
    if text_type == "aside":
        return f'<p class="noteBox">{content}</p>'
    if text_type == "list":
        items = "".join(f"<li>{item}</li>" for item in content)
        return f'<ul class="ruleList">{items}</ul>'
    return ""


def generate_ruleset_page(md_text):
    sections = parse_ruleset_md(md_text)

    intro_html = ""
    numbered_sections = []
    for s in sections:
        if s["heading"] is None:
            intro_html += "".join(render_ruleset_body(t, c) for t, c in s["body"])
        else:
            numbered_sections.append(s)

    rule_no = 0
    sections_html = ""
    for s in numbered_sections:
        is_open_items = s["heading"].strip().lower() == "open items"
        if is_open_items:
            tag = "Status"
        else:
            rule_no += 1
            tag = f"Rule {rule_no:02d}"
        body_html = "".join(render_ruleset_body(t, c) for t, c in s["body"])
        sections_html += f'''
  <section>
    <div class="sectionHead">
      <span class="tag">{esc(tag)}</span>
      <span class="rule"></span>
      <h2>{esc(s["heading"])}</h2>
    </div>
    <div class="proseBlock">{body_html}</div>
  </section>'''

    return f'''<meta charset="UTF-8">
<title>Ruleset — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>
    <nav class="site" aria-label="Primary">
      <a href="index.html">Home</a>
      <a href="index.html#lineage">Lineage</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="map.html">Map</a>
      <a href="index.html#numbers">By the Numbers</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <h1 class="pageTitle">The Ruleset</h1>
  <div class="proseBlock">{intro_html}</div>
{sections_html}
</main>

<footer class="wrap">
  <div class="footRow">
    <span>Every belt game sourced from the College Football Data API and computed against the rules on this page &mdash; no editorial judgment per game.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="index.html#lineage">Lineage</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="map.html">Map</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>
'''


# --------------------------------------------------------------- records page

def _record_row(rank, swatch_color, main_html, value_html, sub_html, href=None):
    dot = f'<span class="swatch" style="background:{swatch_color}"></span>' if swatch_color else ""
    body = f'<span class="recordMain">{dot}{main_html}</span><span class="recordValue tabular">{value_html}</span>'
    if sub_html:
        body += f'<span class="recordSub">{sub_html}</span>'
    tag = "a" if href else "div"
    href_attr = f' href="{href}"' if href else ""
    return f'<{tag} class="recordRow"{href_attr}><span class="recordRank">{rank}</span>{body}</{tag}>'


def generate_records_page(lineage, colors, belt_games):
    """Four record boards, all computed straight from data already on hand
    -- no new API calls, no AI. Ties aren't broken (a team a few days short
    of another's reign length still shows up if it's genuinely top-5)."""
    reigns = lineage["reigns"]
    today = date.today()
    change_index = build_change_game_index(belt_games)

    def start_game_href(r):
        g = change_index.get((r["start_date"], r["team"]))
        return f'games/{g["game_id"]}.html' if g else None

    def team_swatch(name):
        primary, _ = team_color(colors, name)
        return primary

    # ---- longest reigns, by days held (current reign counts through today) ----
    longest = sorted(reigns, key=lambda r: reign_duration_days(r, today), reverse=True)[:5]
    longest_rows = ""
    for i, r in enumerate(longest, 1):
        start, end = reign_dates(r, today)
        is_current = r is reigns[-1]
        sub = f'{fmt_date(r["start_date"])} &ndash; {"present" if is_current else fmt_date(r["end_date"])}'
        if is_current:
            sub += ' <span class="currentTag">current</span>'
        longest_rows += _record_row(i, team_swatch(r["team"]), esc(r["team"]),
                                     fmt_duration(start, end), sub, start_game_href(r))

    # ---- most reigns held by one program ----
    reign_counts = {}
    for r in reigns:
        reign_counts[r["team"]] = reign_counts.get(r["team"], 0) + 1
    most_reigns = sorted(reign_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    most_reigns_rows = ""
    for i, (team, n) in enumerate(most_reigns, 1):
        most_reigns_rows += _record_row(i, team_swatch(team), esc(team),
                                         f'{n}&times;', "reigns held",
                                         f'teams/{team_slug(team)}.html')

    # ---- most defenses in a single reign ----
    most_defended = sorted(reigns, key=lambda r: r.get("defenses", 0), reverse=True)[:5]
    most_defended_rows = ""
    for i, r in enumerate(most_defended, 1):
        is_current = r is reigns[-1]
        sub = fmt_date(r["start_date"])
        if is_current:
            sub += ' <span class="currentTag">current</span>'
        most_defended_rows += _record_row(i, team_swatch(r["team"]), esc(r["team"]),
                                           f'{r.get("defenses", 0)}', sub, start_game_href(r))

    # ---- biggest blowouts in any belt game ----
    def margin(g):
        h, a = (int(x) for x in g["score"].split("-"))
        return abs(h - a)
    blowouts = sorted(belt_games, key=margin, reverse=True)[:5]
    blowout_rows = ""
    for i, g in enumerate(blowouts, 1):
        h, a = (int(x) for x in g["score"].split("-"))
        winner = g["home"] if h > a else g["away"]
        loser = g["away"] if h > a else g["home"]
        win_score, lose_score = max(h, a), min(h, a)
        blowout_rows += _record_row(i, team_swatch(winner), f'{esc(winner)} over {esc(loser)}',
                                     f'+{margin(g)}', f'{win_score}&ndash;{lose_score} &middot; {fmt_date(g["date"])}',
                                     f'games/{g["game_id"]}.html')

    # ---- closest calls: narrowest defenses, narrowest upsets, biggest upsets ----
    defenses_only = [g for g in belt_games if g["holder"] and g["new_holder"] == g["holder"]]
    changes_only = [g for g in belt_games if g["holder"] and g["new_holder"] != g["holder"]]

    def close_call_row(rank, g, verb):
        h, a = (int(x) for x in g["score"].split("-"))
        tie = h == a
        value = "Tie" if tie else f'+{margin(g)}'
        return _record_row(
            rank, team_swatch(g["new_holder"]),
            f'{esc(g["new_holder"])} {"tied" if tie else verb} {esc(g["holder"])}',
            value, f'{max(h, a)}&ndash;{min(h, a)} &middot; {fmt_date(g["date"])}',
            f'games/{g["game_id"]}.html')

    narrowest_defense_rows = "".join(
        close_call_row(i, g, "over") for i, g in enumerate(sorted(defenses_only, key=margin)[:5], 1))
    narrowest_change_rows = "".join(
        close_call_row(i, g, "took it from") for i, g in enumerate(sorted(changes_only, key=margin)[:5], 1))
    biggest_upset_rows = "".join(
        close_call_row(i, g, "routed") for i, g in enumerate(sorted(changes_only, key=margin, reverse=True)[:5], 1))

    cards = [
        ("Longest Reigns", "By days holding the belt", longest_rows),
        ("Most Reigns", "By program, across all 158 years", most_reigns_rows),
        ("Most Defended", "Consecutive defenses in a single reign", most_defended_rows),
        ("Biggest Blowouts", "Largest margin of victory in any belt game", blowout_rows),
        ("Narrowest Defenses", "Closest the holder has come to losing it and didn't", narrowest_defense_rows),
        ("Narrowest Upsets", "The belt changed hands by the barest possible margin", narrowest_change_rows),
        ("Biggest Upsets", "The belt changed hands in an outright rout", biggest_upset_rows),
    ]
    cards_html = "".join(f'''
    <section class="recordCard">
      <h2>{esc(title)}</h2>
      <p class="recordCardSub">{esc(sub)}</p>
      <div class="recordList">{rows}</div>
    </section>''' for title, sub, rows in cards)

    return f'''<meta charset="UTF-8">
<title>Records — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>
    <nav class="site" aria-label="Primary">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <h1 class="pageTitle">Records</h1>
  <p class="lede">Superlatives computed straight from the lineage &mdash; no editorial
    judgment, same as everything else on this site. Ties aren&rsquo;t broken; a
    program just short of the cutoff simply isn&rsquo;t shown.</p>

  <div class="recordsGrid">{cards_html}
  </div>
</main>

<footer class="wrap">
  <div class="footRow">
    <span>Computed from the full belt lineage &mdash; recalculated fresh every run.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>
'''


# ------------------------------------------------------------------ team pages

def _team_reign_row(r, today, change_index, loss_index, is_current):
    start, end = reign_dates(r, today)
    dates = f'{fmt_date(r["start_date"])} &ndash; {"present" if is_current else fmt_date(r["end_date"])}'
    duration = fmt_duration(start, end)
    defenses = r.get("defenses", 0)

    won_score, lost_score = _reign_win_score(r, change_index)
    won_from = r.get("won_from")
    if won_from and won_score is not None:
        win_game = change_index.get((r["start_date"], r["team"]))
        won_line = (f'Won from <a href="../games/{win_game["game_id"]}.html">'
                    f'{esc(won_from)}, {won_score}&ndash;{lost_score}</a>')
    else:
        won_line = "Established the belt"

    lost_line = ""
    if not is_current and r.get("lost_to"):
        loss_game = loss_index.get((r["end_date"], r["team"]))
        if loss_game:
            h, a = (int(x) for x in loss_game["score"].split("-"))
            my_score, their_score = (h, a) if loss_game["home"] == r["team"] else (a, h)
            lost_line = (f'Lost to <a href="../games/{loss_game["game_id"]}.html">'
                         f'{esc(r["lost_to"])}, {their_score}&ndash;{my_score}</a>')

    current_badge = ' <span class="currentTag">current</span>' if is_current else ""
    return f'''
    <div class="teamReignCard">
      <div class="teamReignHead">
        <span class="teamReignDates">{dates}{current_badge}</span>
        <span class="teamReignDuration tabular">{duration}</span>
      </div>
      <div class="teamReignMeta">
        <span>{won_line}</span>
        {f'<span>{lost_line}</span>' if lost_line else ''}
        <span>{defenses} defense{"s" if defenses != 1 else ""}</span>
      </div>
    </div>'''


def generate_team_pages(lineage, colors, belt_games, teams_dir):
    """One page per program that has ever held the belt -- every reign it
    ever had, newest first, how each one started and (if it's over) ended.
    Every team that's ever HELD the belt gets a page here; a team that's
    only ever challenged and lost doesn't have reigns to show, so it
    doesn't get a page -- same "no editorial judgment" computed approach
    as the rest of the site."""
    reigns = lineage["reigns"]
    today = date.today()
    change_index = build_change_game_index(belt_games)
    loss_index = build_loss_game_index(belt_games)
    current_reign = reigns[-1]

    by_team = {}
    for r in reigns:
        by_team.setdefault(r["team"], []).append(r)

    os.makedirs(teams_dir, exist_ok=True)
    written = 0
    for team, team_reigns in by_team.items():
        team_reigns_sorted = sorted(team_reigns, key=lambda r: r["start_date"])
        total_days = sum(reign_duration_days(r, today) for r in team_reigns_sorted)
        total_defenses = sum(r.get("defenses", 0) for r in team_reigns_sorted)
        is_holder_now = team_reigns_sorted[-1] is current_reign

        primary, alt = team_color(colors, team)
        ink, accent = panel_colors(primary, alt)

        rows_html = "".join(
            _team_reign_row(r, today, change_index, loss_index, r is current_reign)
            for r in reversed(team_reigns_sorted))

        n = len(team_reigns_sorted)
        holder_line = (f'{esc(team)} currently holds the belt.' if is_holder_now else
                        f'{esc(team)} last held the belt {fmt_date(team_reigns_sorted[-1]["end_date"])}.')

        page = f'''<meta charset="UTF-8">
<title>{esc(team)} — The College Football Belt</title>
<link rel="stylesheet" href="../styles.css?v={STYLES_VERSION}">
{head_extras('../')}
<style>
  :root{{ --team:{primary}; --team-ink:{ink}; --team-accent:{accent}; }}
</style>

<header class="site wrap">
  <div class="headerRow">
    <a class="back" href="../index.html">&larr; The College Football Belt</a>
    <nav class="site" aria-label="Primary">
      <a href="../lineage.html">Full History</a>
      <a href="../all-games.html">All Games</a>
      <a href="../records.html">Records</a>
      <a href="../ruleset.html">Ruleset</a>
      <a href="../map.html">Map</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <div class="teamPageHead" style="border-color:{primary}">
    {logo_img(colors, team, "teamLogo", 48)}
    <span class="swatch" style="background:{primary};width:14px;height:14px;"></span>
    <h1 class="pageTitle" style="margin:0">{esc(team)}</h1>
  </div>
  <p class="lede">{holder_line} {n} reign{"s" if n != 1 else ""} in belt history,
    {total_days:,} total day{"s" if total_days != 1 else ""} held,
    {total_defenses} total defense{"s" if total_defenses != 1 else ""}.
    <a class="posterLink" href="../posters/{team_slug(team)}.png">Download a poster of this
      history &darr;</a></p>

  <div class="teamReignList">{rows_html}
  </div>
</main>

<footer class="wrap">
  <div class="footRow">
    <span>Every reign computed from the College Football Data API.</span>
    <nav aria-label="Footer">
      <a href="../index.html">Home</a>
      <a href="../lineage.html">Full History</a>
      <a href="../records.html">Records</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>
'''
        with open(os.path.join(teams_dir, f"{team_slug(team)}.html"), "w", encoding="utf-8") as f:
            f.write(page)
        written += 1

    return written, [team_slug(t) for t in by_team]


# --------------------------------------------------------------- belt map

HISTORICAL_DIR = "historical_data"
STATE_SHAPES_PATH = os.path.join(HISTORICAL_DIR, "us_state_shapes.json")

# Standard USGS Albers Equal-Area Conic parameters for the contiguous US --
# the same standard parallels/origin behind EPSG:5070 -- applied on a unit
# sphere. That's plenty accurate for a small decorative map; it's not meant
# for real measurement. Alaska and Hawaii are skipped rather than inset,
# since no belt-holding program has ever been based in either.
_ALBERS_PHI1 = math.radians(29.5)
_ALBERS_PHI2 = math.radians(45.5)
_ALBERS_PHI0 = math.radians(23.0)
_ALBERS_LON0 = math.radians(-96.0)
_ALBERS_N = (math.sin(_ALBERS_PHI1) + math.sin(_ALBERS_PHI2)) / 2
_ALBERS_C = math.cos(_ALBERS_PHI1) ** 2 + 2 * _ALBERS_N * math.sin(_ALBERS_PHI1)
_ALBERS_RHO0 = math.sqrt(_ALBERS_C - 2 * _ALBERS_N * math.sin(_ALBERS_PHI0)) / _ALBERS_N


def albers_project(lon, lat):
    phi = math.radians(lat)
    lam = math.radians(lon)
    theta = _ALBERS_N * (lam - _ALBERS_LON0)
    rho = math.sqrt(_ALBERS_C - 2 * _ALBERS_N * math.sin(phi)) / _ALBERS_N
    return rho * math.sin(theta), _ALBERS_RHO0 - rho * math.cos(theta)


def load_state_shapes():
    if not os.path.exists(STATE_SHAPES_PATH):
        return None
    with open(STATE_SHAPES_PATH) as f:
        return json.load(f)


def _state_rings(entry):
    """Split a state's flat lons/lats (NaN-separated for exclaves like Long
    Island) into a list of point-lists."""
    rings, ring = [], []
    for lon, lat in zip(entry["lons"], entry["lats"]):
        if lon is None or lat is None or lon != lon or lat != lat:  # NaN check, no numpy needed
            if ring:
                rings.append(ring)
                ring = []
            continue
        ring.append((lon, lat))
    if ring:
        rings.append(ring)
    return rings


def build_state_paths(shapes):
    """Project every state's ring(s) through Albers, then scale/flip the
    whole set into one shared SVG coordinate space. Returns
    ({abbr: {"name":..., "d": "<path d>"}}, (viewbox_w, viewbox_h))."""
    projected = {}
    all_x, all_y = [], []
    for abbr, entry in shapes.items():
        rings = []
        for ring in _state_rings(entry):
            proj_ring = [albers_project(lon, lat) for lon, lat in ring]
            rings.append(proj_ring)
            all_x.extend(x for x, _ in proj_ring)
            all_y.extend(y for _, y in proj_ring)
        projected[abbr] = rings

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    pad = 0.02 * max(max_x - min_x, max_y - min_y)
    min_x, max_x = min_x - pad, max_x + pad
    min_y, max_y = min_y - pad, max_y + pad

    width, height = 1000.0, 620.0
    scale = min(width / (max_x - min_x), height / (max_y - min_y))
    off_x = (width - (max_x - min_x) * scale) / 2
    off_y = (height - (max_y - min_y) * scale) / 2

    def to_svg(x, y):
        sx = (x - min_x) * scale + off_x
        sy = height - ((y - min_y) * scale + off_y)  # SVG y grows downward
        return sx, sy

    out = {}
    for abbr, rings in projected.items():
        parts = []
        for ring in rings:
            pts = [to_svg(x, y) for x, y in ring]
            parts.append("M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts) + " Z")
        out[abbr] = {"name": shapes[abbr]["name"], "d": " ".join(parts)}
    return out, (width, height)


def build_state_belt_history(lineage, colors):
    """Every reign, joined against team_colors.json's "state" field, into a
    per-state tally: how many reigns started there, and which team(s)."""
    by_state = {}
    for r in lineage["reigns"]:
        team = r["team"]
        state = (colors.get(team) or {}).get("state")
        if not state:
            continue
        entry = by_state.setdefault(state, {"reigns": 0, "teams": {}})
        entry["reigns"] += 1
        year = int(r["start_date"][:4])
        t = entry["teams"].setdefault(team, {"count": 0, "first": year, "last": year})
        t["count"] += 1
        t["first"] = min(t["first"], year)
        t["last"] = max(t["last"], year)
    return by_state


def _team_bits(teams):
    return ", ".join(
        f'{esc(name)} ({t["count"]}&times;)' if t["count"] > 1 else esc(name)
        for name, t in sorted(teams.items(), key=lambda kv: -kv[1]["count"]))


def generate_map_page(lineage, colors):
    """A US map shaded by how many belt reigns have started in each state
    -- every figure computed straight from lineage.json + team_colors.json,
    no new API calls. Returns None (and build_site.py skips writing the
    page) if historical_data/us_state_shapes.json isn't present."""
    shapes = load_state_shapes()
    if not shapes:
        return None
    paths, (vb_w, vb_h) = build_state_paths(shapes)
    by_state = build_state_belt_history(lineage, colors)
    max_reigns = max((v["reigns"] for v in by_state.values()), default=0)

    def tint_class(n):
        if n == 0 or max_reigns == 0:
            return ""
        frac = n / max_reigns
        if frac > 0.66 or max_reigns <= 1:
            return " mapState--3"
        if frac > 0.33:
            return " mapState--2"
        return " mapState--1"

    path_svg = []
    for abbr in sorted(paths):
        info = paths[abbr]
        st = by_state.get(abbr)
        n = st["reigns"] if st else 0
        title = esc(info["name"])
        if st:
            title += f": {_team_bits(st['teams'])}"
        path_svg.append(f'<path class="mapState{tint_class(n)}" d="{info["d"]}"><title>{title}</title></path>')

    legend_rows = ""
    for abbr, st in sorted(by_state.items(), key=lambda kv: -kv[1]["reigns"]):
        state_name = paths.get(abbr, {}).get("name", abbr)
        n = st["reigns"]
        legend_rows += f'''
    <div class="mapLegendRow">
      <span class="mapLegendState">{esc(state_name)}</span>
      <span class="mapLegendCount tabular">{n} reign{"s" if n != 1 else ""}</span>
      <span class="mapLegendTeams">{_team_bits(st["teams"])}</span>
    </div>'''

    n_states = len(by_state)

    return f'''<meta charset="UTF-8">
<title>Map — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>
    <nav class="site" aria-label="Primary">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <h1 class="pageTitle">Everywhere the Belt Has Lived</h1>
  <p class="lede">{n_states} state{"s" if n_states != 1 else ""} ha{"ve" if n_states != 1 else "s"} produced
    a College Football Belt holder since 1869. Shading shows how many separate reigns
    started there &mdash; darker means more; hover a state (or check the list below) for who.</p>

  <div class="mapWrap">
    <svg class="mapSvg" viewBox="0 0 {vb_w:.0f} {vb_h:.0f}" role="img" aria-label="Map of US states that have held the College Football Belt">{"".join(path_svg)}
    </svg>
  </div>

  <div class="mapLegend">{legend_rows}
  </div>
</main>

<footer class="wrap">
  <div class="footRow">
    <span>Every reign's state comes from the belt-holding team's CFBD-listed home state.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>
'''


# -------------------------------------------------------------------- main

# --------------------------------------------------- sitemap / robots / feed / 404

def generate_sitemap(urls):
    """A plain sitemap.xml -- every URL, one <lastmod> for all of them
    (today's build date; nothing here tracks true per-page last-changed
    dates, and search engines treat a same-day sitemap-wide date as fine).
    """
    today = date.today().isoformat()
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        parts.append(f"  <url><loc>{esc(u)}</loc><lastmod>{today}</lastmod></url>")
    parts.append("</urlset>")
    return "\n".join(parts) + "\n"


def generate_robots_txt():
    return f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n"


def generate_404_page():
    nav = '''
    <nav class="site" aria-label="Primary">
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="records.html">Records</a>
      <a href="ruleset.html">Ruleset</a>
      <a href="map.html">Map</a>
    </nav>'''
    return f'''<meta charset="UTF-8">
<title>Page Not Found — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

<header class="site wrap">
  <div class="headerRow">
    <a class="back" href="index.html">&larr; The College Football Belt</a>{nav}
  </div>
</header>

<main class="wrap">
  <h1 class="pageTitle">404 &mdash; Fumbled</h1>
  <p class="lede">Whatever you were looking for isn&rsquo;t here &mdash; might&rsquo;ve moved,
    might never have existed. Either way, no need to punt.</p>
  <p><a href="index.html">&larr; Back to the current belt holder</a></p>
</main>

<footer class="wrap">
  <div class="footRow">
    <span>The College Football Belt &mdash; lineal championship, since 1869.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="all-games.html">All Games</a>
      <a href="mailto:hello@collegefootballbelt.com">Contact</a>
    </nav>
  </div>
</footer>
'''


def generate_feed(belt_games, recaps):
    """RSS 2.0 feed of belt CHANGES only (not every defense) -- newest
    first, so an RSS reader or RSS-to-email service can notify someone the
    moment the belt actually changes hands without them checking the site.
    Capped at the most recent 30 changes; a reader only ever cares about
    what's new anyway, and 327-some entries would be a wall for no benefit.
    """
    changes = [g for g in belt_games if g.get("outcome") == "changed"]
    changes.sort(key=lambda g: g["date"], reverse=True)

    items = []
    for g in changes[:30]:
        try:
            dt = datetime.strptime(g["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        recap = recaps.get(str(g["game_id"])) or {}
        # holder is always set here -- the very first ("established") game
        # has no defending holder and outcome "established", not "changed",
        # so it's never in `changes` above.
        home_score, away_score = (int(x) for x in g["score"].split("-"))
        winner_score, loser_score = ((home_score, away_score) if g["new_holder"] == g["home"]
                                      else (away_score, home_score))
        desc = recap.get("recap") or f'{g["new_holder"]} def. {g["holder"]} {winner_score}–{loser_score}.'
        link = f'{SITE_URL}/games/{g["game_id"]}.html'
        items.append(f'''
    <item>
      <title>{esc(g["new_holder"])} takes the belt from {esc(g["holder"])}</title>
      <link>{link}</link>
      <guid isPermaLink="true">{link}</guid>
      <pubDate>{format_datetime(dt)}</pubDate>
      <description>{esc(desc)}</description>
    </item>''')

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>The College Football Belt</title>
    <link>{SITE_URL}/</link>
    <description>Every time the lineal College Football Belt changes hands, on the field.</description>
    <language>en-us</language>{''.join(items)}
  </channel>
</rss>
'''


def main():
    (lineage, details, colors, next_game, upcoming_games, matchup,
     ai_preview, weather, recaps, game_plays) = load_data()
    belt_games = lineage["belt_games"]
    compute_sequence(belt_games)

    games_dir = os.path.join(OUT_DIR, "games")
    os.makedirs(games_dir, exist_ok=True)

    with open(os.path.join(OUT_DIR, "styles.css"), "w", encoding="utf-8") as f:
        f.write(STYLES_CSS)

    with open(os.path.join(OUT_DIR, "CNAME"), "w", encoding="utf-8") as f:
        f.write(CUSTOM_DOMAIN + "\n")

    warnings = []
    written = 0
    for i, g in enumerate(belt_games):
        gid = g["game_id"]
        d = details.get(str(gid))
        if d is None:
            warnings.append(f"game {gid}: no entry in game_details.json")
            d = {}
        merged = dict(g)
        merged["line_score"] = d.get("line_score")
        merged["team_stats"] = d.get("team_stats")
        merged["player_stats"] = d.get("player_stats")
        merged["recap"] = recaps.get(str(gid))
        merged["key_plays"] = game_plays.get(str(gid))

        prev_game = belt_games[i - 1] if i > 0 else None
        next_belt_game = belt_games[i + 1] if i < len(belt_games) - 1 else None

        html_out = render_page(merged, colors, prev_game, next_belt_game, len(belt_games))
        with open(os.path.join(games_dir, f"{gid}.html"), "w", encoding="utf-8") as f:
            f.write(html_out)
        written += 1

    homepage_html = generate_homepage(lineage, colors, belt_games, next_game, upcoming_games)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(homepage_html)

    lineage_html = generate_lineage_page(lineage, colors, belt_games)
    with open(os.path.join(OUT_DIR, "lineage.html"), "w", encoding="utf-8") as f:
        f.write(lineage_html)

    all_games_html = generate_all_games_page(lineage, colors, belt_games)
    with open(os.path.join(OUT_DIR, "all-games.html"), "w", encoding="utf-8") as f:
        f.write(all_games_html)

    preview_html = generate_preview_page(next_game, matchup, ai_preview, weather, colors)
    with open(os.path.join(OUT_DIR, "preview.html"), "w", encoding="utf-8") as f:
        f.write(preview_html)

    records_html = generate_records_page(lineage, colors, belt_games)
    with open(os.path.join(OUT_DIR, "records.html"), "w", encoding="utf-8") as f:
        f.write(records_html)

    on_this_day_html = generate_on_this_day_page(belt_games)
    with open(os.path.join(OUT_DIR, "on-this-day.html"), "w", encoding="utf-8") as f:
        f.write(on_this_day_html)

    teams_dir = os.path.join(OUT_DIR, "teams")
    teams_written, team_slugs = generate_team_pages(lineage, colors, belt_games, teams_dir)

    map_html = generate_map_page(lineage, colors)
    wrote_map = map_html is not None
    if wrote_map:
        with open(os.path.join(OUT_DIR, "map.html"), "w", encoding="utf-8") as f:
            f.write(map_html)
    else:
        warnings.append(f"{STATE_SHAPES_PATH} not found -- skipped map.html")

    if os.path.exists(RULESET_MD_PATH):
        with open(RULESET_MD_PATH, encoding="utf-8") as f:
            ruleset_md = f.read()
        ruleset_html = generate_ruleset_page(ruleset_md)
        with open(os.path.join(OUT_DIR, "ruleset.html"), "w", encoding="utf-8") as f:
            f.write(ruleset_html)
        wrote_ruleset = True
    else:
        wrote_ruleset = False
        warnings.append(f"{RULESET_MD_PATH} not found -- skipped ruleset.html "
                         f"(run this from the project folder, where that file lives)")

    sitemap_urls = [f"{SITE_URL}/", f"{SITE_URL}/lineage.html", f"{SITE_URL}/all-games.html",
                     f"{SITE_URL}/records.html", f"{SITE_URL}/preview.html",
                     f"{SITE_URL}/on-this-day.html"]
    if wrote_ruleset:
        sitemap_urls.append(f"{SITE_URL}/ruleset.html")
    if wrote_map:
        sitemap_urls.append(f"{SITE_URL}/map.html")
    sitemap_urls += [f"{SITE_URL}/teams/{slug}.html" for slug in team_slugs]
    sitemap_urls += [f"{SITE_URL}/games/{g['game_id']}.html" for g in belt_games]

    with open(os.path.join(OUT_DIR, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(generate_sitemap(sitemap_urls))

    with open(os.path.join(OUT_DIR, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(generate_robots_txt())

    with open(os.path.join(OUT_DIR, "404.html"), "w", encoding="utf-8") as f:
        f.write(generate_404_page())

    with open(os.path.join(OUT_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(generate_feed(belt_games, recaps))

    print(f"Wrote {written} game pages to {games_dir}/")
    print(f"Wrote shared stylesheet to {OUT_DIR}/styles.css")
    print(f"Wrote {OUT_DIR}/CNAME ({CUSTOM_DOMAIN})")
    print(f"Wrote homepage to {OUT_DIR}/index.html")
    print(f"Wrote full-history page to {OUT_DIR}/lineage.html")
    print(f"Wrote all-games page to {OUT_DIR}/all-games.html")
    print(f"Wrote preview page to {OUT_DIR}/preview.html")
    print(f"Wrote records page to {OUT_DIR}/records.html")
    print(f"Wrote On This Day page to {OUT_DIR}/on-this-day.html")
    print(f"Wrote {teams_written} team pages to {teams_dir}/")
    if wrote_map:
        print(f"Wrote map page to {OUT_DIR}/map.html")
    if wrote_ruleset:
        print(f"Wrote ruleset page to {OUT_DIR}/ruleset.html")
    print(f"Wrote sitemap.xml ({len(sitemap_urls)} URLs), robots.txt, 404.html, and feed.xml "
          f"({min(len([g for g in belt_games if g.get('outcome') == 'changed']), 30)} item(s)) to {OUT_DIR}/")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings[:20]:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
