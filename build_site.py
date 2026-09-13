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
import os
import re
import sys
from collections import Counter
from datetime import date

OUT_DIR = "site"
DATA_DIR = "belt_data"

PAPER_LIGHT = "#e7e2d5"
PAPER_DARK = "#161009"


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


def load_data():
    with open(os.path.join(DATA_DIR, "lineage.json")) as f:
        lineage = json.load(f)
    with open(os.path.join(DATA_DIR, "game_details.json")) as f:
        details = json.load(f)
    with open(os.path.join(DATA_DIR, "team_colors.json")) as f:
        colors = json.load(f)
    return lineage, details, colors


def team_color(colors, name):
    entry = colors.get(name) or {}
    primary = entry.get("color") or "#5b5b5b"
    alt = entry.get("alternate_color") or "#ffffff"
    return primary, alt


def fmt_date(iso):
    # avoid a libc/locale dependency -- do it by hand
    months = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{months[m-1]} {d}, {y}"


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
        if g["outcome"] == "changed":
            index[(g["date"], g["new_holder"])] = g
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
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#161009; --paper-2:#1f170e;
    --ink:#ece3d1; --ink-soft:#b6a98d;
    --brass:#cf9f52; --brass-bright:#e0b46a; --brass-line: rgba(207,159,82,.32);
    --hairline: rgba(236,227,209,.14);
    --shadow: 0 18px 44px -20px rgba(0,0,0,.6);
    --good:#7fbf7f; --good-bg: rgba(127,191,127,.14);
  }
}
:root[data-theme="dark"]{
  --paper:#161009; --paper-2:#1f170e;
  --ink:#ece3d1; --ink-soft:#b6a98d;
  --brass:#cf9f52; --brass-bright:#e0b46a; --brass-line: rgba(207,159,82,.32);
  --hairline: rgba(236,227,209,.14);
  --shadow: 0 18px 44px -20px rgba(0,0,0,.6);
  --good:#7fbf7f; --good-bg: rgba(127,191,127,.14);
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
.teamPanel{ padding:26px 22px; display:flex; flex-direction:column; gap:10px; }
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
.link{ position:relative; padding:16px 16px 14px; margin:0 4px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:6px; }
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
        loss and a tie, since a tie means the holder keeps the belt)."""
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
      <a href="../ruleset.html">Ruleset</a>
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
      <span class="side">Home &middot; {home_defender}</span>
      <span class="name">{esc(home)}</span>
      <span class="pts tabular">{home_score}</span>
    </div>
    <div class="vs">AT</div>
    <div class="teamPanel away">
      <span class="side">Away &middot; {away_defender}</span>
      <span class="name">{esc(away)}</span>
      <span class="pts tabular">{away_score}</span>
    </div>
  </div>
{render_line_score(g)}
{render_team_stats(g)}
{game_nav}
</main>

<footer class="wrap">
  <div class="footRow">
    <span>Part of the lineage since 1869. Score{" and line score" if g.get("line_score") else ""} sourced from the College Football Data API.</span>
    <nav aria-label="Footer">
      <a href="../index.html">Home</a>
      <a href="../lineage.html">Full History</a>
      <a href="../all-games.html">All Games</a>
      <a href="../ruleset.html">Ruleset</a>
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


def generate_homepage(lineage, colors, belt_games):
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
    reign_num = len(reigns)
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
        open_a = f'<a href="games/{game_id}.html" style="text-decoration:none;color:inherit">' if game_id else ""
        close_a = "</a>" if game_id else ""

        if is_current:
            right_meta = f'<span class="tabular">{defenses} def.</span>'
            now_badge = '<span class="now">Current</span>'
            cls = " current"
        else:
            right_meta = f"&rarr; {esc(team_chip(r['lost_to']))}" if r.get("lost_to") else ""
            now_badge = ""
            cls = ""

        links_html += f'''
      <div class="link{cls}">
        {now_badge}
        <div class="chip" style="background:{p};color:{chip_accent}">{esc(team_chip(r["team"]))}</div>
        <div class="team">{open_a}{esc(r["team"])}{close_a}</div>
        <div class="beat">{beat}</div>
        <div class="meta"><span>{esc(r["start_date"])}</span><span>{right_meta}</span></div>
      </div>'''

    chain_lead = ""
    if hidden_count > 0:
        chain_lead = (f'<div class="chainLead">&larr; {hidden_count:,} earlier '
                       f'reign{"s" if hidden_count != 1 else ""}<br>since 1869</div>')

    monogram = esc(team_chip(holder))

    return f'''<meta charset="UTF-8">
<title>The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
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
      <a href="ruleset.html">Ruleset</a>
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
        <div class="holderName">{esc(holder)}</div>
        <div class="sub">{sub_line}</div>
        <div class="plateStats">
          <div><span class="n tabular">{days_held:,}</span><span class="l">Days</span></div>
          <div><span class="n tabular">{defenses}</span><span class="l">Defenses</span></div>
          <div><span class="n tabular">{ordinal(reign_num)}</span><span class="l">Reign</span></div>
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
      <a href="ruleset.html">Ruleset</a>
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

<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>
    <nav class="site" aria-label="Primary">
      <a href="index.html">Home</a>
      <a href="all-games.html">All Games</a>
      <a href="ruleset.html">Ruleset</a>
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
    <div class="searchBox">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/></svg>
      <input id="teamSearch" type="text" placeholder="Filter by team&hellip;" autocomplete="off">
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
    <span>Every reign computed from the College Football Data API, oldest to newest, top to bottom.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="all-games.html">All Games</a>
      <a href="ruleset.html">Ruleset</a>
    </nav>
  </div>
</footer>

<script>
(function(){{
  var input = document.getElementById('teamSearch');
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
    title_changes = sum(1 for g in belt_games if g["outcome"] == "changed")
    defenses_total = len(belt_games) - title_changes

    rows_html = ""
    defense_no = 0
    for g in belt_games:
        home, away = g["home"], g["away"]
        home_score, away_score = (int(x) for x in g["score"].split("-"))
        outcome = g["outcome"]
        is_last = g is belt_games[-1]

        if outcome == "changed":
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
        if outcome == "changed":
            cls_bits.append("titleChange")
        if is_last:
            cls_bits.append("current")
        cls = " ".join(cls_bits)

        rows_html += f'''
        <tr class="{cls}" data-team="{esc(home.lower())} {esc(away.lower())}">
          <td class="num">{g["game_number"]:,}</td>
          <td class="dates">{fmt_date(g["date"])}</td>
          <td class="matchup">{matchup_html}</td>
          <td class="tabular">{away_score}&ndash;{home_score}</td>
          <td class="result">{result_html}</td>
        </tr>'''

    return f'''<meta charset="UTF-8">
<title>All Games — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">

<header class="site wrap">
  <div class="headerRow">
    <div class="brandBlock">
      <span class="eyebrow">Est. 1869 &middot; Lineal Championship</span>
      <span class="wordmark">The College Football Belt</span>
    </div>
    <nav class="site" aria-label="Primary">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="ruleset.html">Ruleset</a>
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
    <div class="searchBox">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/></svg>
      <input id="teamSearch" type="text" placeholder="Filter by team&hellip;" autocomplete="off">
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
    <span>Every game computed from the College Football Data API, oldest to newest, top to bottom.</span>
    <nav aria-label="Footer">
      <a href="index.html">Home</a>
      <a href="lineage.html">Full History</a>
      <a href="ruleset.html">Ruleset</a>
    </nav>
  </div>
</footer>

<script>
(function(){{
  var input = document.getElementById('teamSearch');
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
}})();
</script>
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
    </nav>
  </div>
</footer>
'''


# -------------------------------------------------------------------- main

def main():
    lineage, details, colors = load_data()
    belt_games = lineage["belt_games"]
    compute_sequence(belt_games)

    games_dir = os.path.join(OUT_DIR, "games")
    os.makedirs(games_dir, exist_ok=True)

    with open(os.path.join(OUT_DIR, "styles.css"), "w", encoding="utf-8") as f:
        f.write(STYLES_CSS)

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

        prev_game = belt_games[i - 1] if i > 0 else None
        next_game = belt_games[i + 1] if i < len(belt_games) - 1 else None

        html_out = render_page(merged, colors, prev_game, next_game, len(belt_games))
        with open(os.path.join(games_dir, f"{gid}.html"), "w", encoding="utf-8") as f:
            f.write(html_out)
        written += 1

    homepage_html = generate_homepage(lineage, colors, belt_games)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(homepage_html)

    lineage_html = generate_lineage_page(lineage, colors, belt_games)
    with open(os.path.join(OUT_DIR, "lineage.html"), "w", encoding="utf-8") as f:
        f.write(lineage_html)

    all_games_html = generate_all_games_page(lineage, colors, belt_games)
    with open(os.path.join(OUT_DIR, "all-games.html"), "w", encoding="utf-8") as f:
        f.write(all_games_html)

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

    print(f"Wrote {written} game pages to {games_dir}/")
    print(f"Wrote shared stylesheet to {OUT_DIR}/styles.css")
    print(f"Wrote homepage to {OUT_DIR}/index.html")
    print(f"Wrote full-history page to {OUT_DIR}/lineage.html")
    print(f"Wrote all-games page to {OUT_DIR}/all-games.html")
    if wrote_ruleset:
        print(f"Wrote ruleset page to {OUT_DIR}/ruleset.html")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings[:20]:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
