#!/usr/bin/env python3
"""
Render a static 1200x630 social-share image (Open Graph / Twitter Card
size) for whoever currently holds the College Football Belt -- team color
background, the holder's name, and how long they've held it. Also renders
the site's favicon (site/favicon.png + apple-touch-icon.png) in the same
team colors -- a small belt-buckle glyph, not a photo, so it stays legible
down to 16x16 -- and a downloadable portrait "belt history" poster
(site/posters/<slug>.png) for every program that's ever held the belt: a
timeline of that team's reigns in its own colors, linked from its team
page.

Usage:
    python3 generate_share_image.py          # reads belt_data/, writes site/share.png + favicons + posters

No API key, no network call, no headless browser -- just belt_data/lineage.json
+ belt_data/team_colors.json (both already on disk after build_lineage.py and
fetch_team_colors.py have run) rendered directly with Pillow. Run this AFTER
build_site.py, since it writes straight into site/, which build_site.py owns.

Font handling is defensive on purpose: GitHub Actions' ubuntu-latest runner
ships DejaVu/Liberation TrueType fonts by default, and this sandbox's dev
environment has the same, but nothing here should ever be able to fail the
whole pipeline just because a font file moved. If none of the usual paths
exist, Pillow's own built-in default font is used instead -- plainer, but
the image still gets made.
"""

import json
import os
import sys

DATA_DIR = "belt_data"
OUT_DIR = "site"
OUT_PATH = os.path.join(OUT_DIR, "share.png")

W, H = 1200, 630
PAD = 72

# Same font families as the site's HTML wants (Big Shoulders Display /
# Spectral / IBM Plex Mono), but those are Google Fonts loaded over the
# network in a browser -- there's no equivalent here, so this reaches for
# whatever bold serif / mono TrueType fonts are actually on disk instead.
_DISPLAY_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_BODY_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_MONO_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]


def _first_existing(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _font(paths, size):
    from PIL import ImageFont
    path = _first_existing(paths)
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()  # older Pillow: no `size` kwarg


# ---- tiny color helpers, deliberately duplicated from build_site.py rather
# than imported -- every script in this project is meant to run standalone,
# same as fetch_team_colors.py's own pick()/clean_color() ----

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _linearize(c):
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hexcolor):
    r, g, b = (_linearize(c) for c in hex_to_rgb(hexcolor))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex1, hex2):
    l1, l2 = relative_luminance(hex1), relative_luminance(hex2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def blend(hex1, hex2, weight1):
    r1, g1, b1 = hex_to_rgb(hex1)
    r2, g2, b2 = hex_to_rgb(hex2)
    r = round(r1 * weight1 + r2 * (1 - weight1))
    g = round(g1 * weight1 + g2 * (1 - weight1))
    b = round(b1 * weight1 + b2 * (1 - weight1))
    return f"#{r:02x}{g:02x}{b:02x}"


def pick_best(candidates, against_colors):
    best, best_score = candidates[0], -1
    for cand in candidates:
        worst = min(contrast_ratio(cand, bg) for bg in against_colors)
        if worst > best_score:
            best, best_score = cand, worst
    return best, best_score


def panel_colors(primary, alt):
    dark_stop = blend(primary, "#000000", 0.75)
    stops = [primary, dark_stop]
    ink, _ = pick_best(["#ffffff", "#000000"], stops)
    alt_worst = min(contrast_ratio(alt, s) for s in stops)
    accent = alt if alt_worst >= 3.0 else ink
    return ink, accent, dark_stop


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def fmt_date(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{MONTH_NAMES[m - 1]} {d}, {y}"


def team_color(colors, name):
    entry = colors.get(name) or {}
    return entry.get("color") or "#5b5b5b", entry.get("alternate_color") or "#ffffff"


def team_logo(colors, name):
    """CFBD's own logo URL for this team, or None -- same field build_site.py's
    team_logo() reads, duplicated here rather than imported (this file runs
    standalone, same as every other script in this project)."""
    entry = colors.get(name) or {}
    return entry.get("logo") or None


def fit_font(draw, text, candidates, max_width, start_size, min_size=40):
    """Shrink a font size until `text` fits max_width, never below min_size."""
    size = start_size
    while size > min_size:
        font = _font(candidates, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 4
    return _font(candidates, min_size)


def draw_tracked_text(draw, xy, text, font, fill, tracking=0):
    """Draw text with a bit of manual letter-spacing -- Pillow has no
    native tracking support."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        w = draw.textbbox((0, 0), ch, font=font)[2]
        x += w + tracking


def _belt_mark_hex_points(cx, cy, th):
    """The 6 points of the hexagonal center shield -- same proportions as
    BELT_MARK_SVG's path in build_site.py (a 34x22 viewBox: tip-to-center
    11, shoulder corners 7 up/down and 7 either side of center), so every
    belt glyph in this file traces the SAME shape as the real site mark
    instead of a generic round buckle. `th` is the tip-to-center distance;
    the shoulder offset scales off it at that same 7:11 ratio."""
    shoulder = th * (7 / 11)
    return [
        (cx, cy - th),
        (cx + shoulder, cy - shoulder),
        (cx + shoulder, cy + shoulder),
        (cx, cy + th),
        (cx - shoulder, cy + shoulder),
        (cx - shoulder, cy - shoulder),
    ]


def _draw_belt_mark(draw, cx, cy, th, strap_color, plate_color, plate_edge, stud_color, strap_span):
    """Draws the actual mark -- a strap band, two brass side plates, a
    hexagonal center shield, and a dark center stud -- centered at
    (cx, cy). Every ratio here is lifted straight from BELT_MARK_SVG's own
    34x22 path (see _belt_mark_hex_points), scaled by `th` (the hexagon's
    tip-to-center distance, matching the original's 11 units) so every
    caller in this file draws the identical shape, just recolored and
    resized -- the favicon, the PWA icons, and the "on this day" share
    badge all trace the same mark as the site's own nav logo.
    `strap_span` is how far the strap extends from center on each side
    (edge-to-edge for the square icon glyphs; inset to stay inside the
    circle for the round badge)."""
    strap_h = th * (6 / 11)
    draw.rectangle([cx - strap_span, cy - strap_h / 2, cx + strap_span, cy + strap_h / 2], fill=strap_color)

    plate_w = th * (6 / 11)
    plate_h = th * (10 / 11)
    plate_offset = th
    edge_w = max(1, round(th * 0.09))
    for sign in (-1, 1):
        px = cx + sign * plate_offset
        draw.rectangle([px - plate_w / 2, cy - plate_h / 2, px + plate_w / 2, cy + plate_h / 2],
                        fill=plate_color, outline=plate_edge, width=edge_w)

    hex_pts = _belt_mark_hex_points(cx, cy, th)
    draw.polygon(hex_pts, fill=plate_color)
    for i in range(6):
        draw.line([hex_pts[i], hex_pts[(i + 1) % 6]], fill=plate_edge, width=edge_w)

    stud_r = th * (3.5 / 11)
    draw.ellipse([cx - stud_r, cy - stud_r, cx + stud_r, cy + stud_r], fill=stud_color)


def draw_belt_icon(size, primary, accent, ink):
    """The site's favicon / PWA icon glyph -- the SAME mark as
    BELT_MARK_SVG (build_site.py's nav logo): a strap across, two brass
    side plates, and a hexagonal center shield with a dark stud, recolored
    to the current holder's own primary/accent/ink so the browser-tab icon
    always matches whoever holds the belt. Legible down to 16x16 the same
    way the real SVG mark is at similar on-page sizes."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), hex_to_rgb(primary))
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    th = size * 0.30
    _draw_belt_mark(draw, cx, cy, th, strap_color=hex_to_rgb(ink), plate_color=hex_to_rgb(accent),
                     plate_edge=hex_to_rgb(ink), stud_color=hex_to_rgb(ink), strap_span=size / 2)
    return img


def _team_slug(name):
    """Must match build_site.py's team_slug() exactly -- this is the same
    filename stem a team's own page links a poster download to."""
    import re
    return re.sub(r"[^A-Za-z0-9]+", "-", name.strip().lower()).strip("-") or "team"


def vertical_gradient(w, h, top_hex, bottom_hex):
    """Same 1px-wide-then-resize trick as the share image -- H pixel writes
    instead of W*H."""
    from PIL import Image
    top_rgb, bot_rgb = hex_to_rgb(top_hex), hex_to_rgb(bottom_hex)
    grad = Image.new("RGB", (1, h))
    gpx = grad.load()
    for y in range(h):
        t = y / (h - 1) if h > 1 else 0
        gpx[0, y] = tuple(round(top_rgb[i] + (bot_rgb[i] - top_rgb[i]) * t) for i in range(3))
    return grad.resize((w, h))


TEAM_SHARE_DIR = "team-share"


def generate_team_share_image(team, team_reigns, primary, alt, out_path):
    """A 1200x630 Open Graph / Twitter Card image for one team's OWN page --
    same landscape size and layout language as the homepage's share.png,
    just in that team's own colors with that team's own all-time stats
    instead of the current holder's. Every program that's ever held the
    belt gets one, at site/team-share/<slug>.png, so a link to a team page
    doesn't fall back to sharing the current holder's image (or nothing)."""
    from PIL import ImageDraw
    import datetime as _dt

    today = _dt.date.today()
    ink, accent, dark_stop = panel_colors(primary, alt)

    img = vertical_gradient(W, H, primary, dark_stop)
    draw = ImageDraw.Draw(img)

    eyebrow_font = _font(_MONO_CANDIDATES, 22)
    wordmark_font = _font(_DISPLAY_CANDIDATES, 34)
    stat_font = _font(_BODY_CANDIDATES, 28)
    url_font = _font(_MONO_CANDIDATES, 22)

    draw_tracked_text(draw, (PAD, PAD), "EST. 1869 · LINEAL CHAMPIONSHIP",
                       eyebrow_font, ink, tracking=2)
    draw.text((PAD, PAD + 38), "THE COLLEGE FOOTBALL BELT", font=wordmark_font, fill=ink)

    team_font = fit_font(draw, team, _DISPLAY_CANDIDATES, W - 2 * PAD,
                          start_size=132, min_size=56)
    team_y = H // 2 - 40
    draw.text((PAD, team_y), team, font=team_font, fill=accent)

    sorted_reigns = sorted(team_reigns, key=lambda r: r["start_date"])
    total_days, total_defenses = 0, 0
    for r in sorted_reigns:
        start = _dt.date.fromisoformat(r["start_date"])
        end = _dt.date.fromisoformat(r["end_date"]) if r.get("end_date") else today
        total_days += (end - start).days
        total_defenses += r.get("defenses", 0)
    n = len(sorted_reigns)
    is_current = sorted_reigns[-1].get("end_date") is None

    stat_bbox = draw.textbbox((0, 0), "Xg", font=team_font)
    stat_y = team_y + (stat_bbox[3] - stat_bbox[1]) + 30

    stat_line = (f"{n} reign{'s' if n != 1 else ''} · {total_days:,} total days held · "
                 f"{total_defenses} total defense{'s' if total_defenses != 1 else ''}")
    if is_current:
        stat_line = "Current champion · " + stat_line
    draw.text((PAD, stat_y), stat_line, font=stat_font, fill=ink)

    url_text = "collegefootballbelt.com"
    url_bbox = draw.textbbox((0, 0), url_text, font=url_font)
    url_w = url_bbox[2] - url_bbox[0]
    draw.text((W - PAD - url_w, H - PAD - 22), url_text, font=url_font, fill=ink)

    img.save(out_path, "PNG")


def generate_team_share_images(lineage, colors):
    """One share.png-equivalent per program that's ever held the belt.
    Zero new data -- same lineage.json + team_colors.json already loaded
    for the homepage share image and the posters."""
    out_dir = os.path.join(OUT_DIR, TEAM_SHARE_DIR)
    os.makedirs(out_dir, exist_ok=True)

    by_team = {}
    for r in lineage["reigns"]:
        by_team.setdefault(r["team"], []).append(r)

    for team, reigns in by_team.items():
        primary, alt = team_color(colors, team)
        out_path = os.path.join(out_dir, f"{_team_slug(team)}.png")
        generate_team_share_image(team, reigns, primary, alt, out_path)

    print(f"Wrote {len(by_team)} team share image(s) to {out_dir}/")


POSTER_W = 1200
POSTER_MIN_H = 560  # floor for a 1-reign team so the poster isn't mostly blank
POSTER_MAX_ROWS = 9  # more than this and the poster just notes "+N earlier reigns"


def fmt_duration_approx(days):
    """A light years/days breakdown for the poster's timeline rows -- uses
    flat 365-day years (not the leap-aware anniversary math the site's own
    pages use for exact reign lengths), which is a fine approximation for a
    decorative image. The poster's TOTAL days-held stat is still exact real
    date arithmetic; only this per-row breakdown is approximate."""
    years, remainder = divmod(days, 365)
    if years > 0:
        return f"{years} yr{'s' if years != 1 else ''}, {remainder} day{'s' if remainder != 1 else ''}"
    return f"{days} day{'s' if days != 1 else ''}"


def generate_team_poster(team, team_reigns, primary, alt, out_path):
    """One portrait poster per program that's ever held the belt: a
    timeline of every reign it's had, newest first, in that team's own
    colors. Same Pillow toolkit as the share image, no browser involved.

    The canvas height is derived from how many rows actually get drawn
    (measured on a throwaway 1x1 surface first), floored at POSTER_MIN_H --
    a team with one short reign gets a short poster instead of a tall
    canvas that's two-thirds empty gradient below the history section."""
    from PIL import Image, ImageDraw
    import datetime as _dt

    today = _dt.date.today()
    ink, accent, dark_stop = panel_colors(primary, alt)
    pad = 80

    eyebrow_font = _font(_MONO_CANDIDATES, 20)
    wordmark_font = _font(_DISPLAY_CANDIDATES, 26)
    stat_font = _font(_BODY_CANDIDATES, 24)
    row_font = _font(_BODY_CANDIDATES, 26)
    row_sub_font = _font(_MONO_CANDIDATES, 17)
    url_font = _font(_MONO_CANDIDATES, 20)

    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    team_font = fit_font(measure, team, _DISPLAY_CANDIDATES, POSTER_W - 2 * pad, start_size=88, min_size=40)

    sorted_reigns = sorted(team_reigns, key=lambda r: r["start_date"])
    total_days = 0
    total_defenses = 0
    for r in sorted_reigns:
        start = _dt.date.fromisoformat(r["start_date"])
        end = _dt.date.fromisoformat(r["end_date"]) if r.get("end_date") else today
        total_days += (end - start).days
        total_defenses += r.get("defenses", 0)
    n = len(sorted_reigns)
    stat_line = (f"{n} reign{'s' if n != 1 else ''} · {total_days:,} total days held · "
                 f"{total_defenses} total defense{'s' if total_defenses != 1 else ''}")

    newest_first = list(reversed(sorted_reigns))
    shown = newest_first[:POSTER_MAX_ROWS]
    hidden_count = len(newest_first) - len(shown)

    # Layout pass: walk through the exact same sequence of y-advances the
    # draw pass below uses, just without drawing anything, to find where
    # the content actually ends.
    y = pad
    y += 32
    y += 56
    bbox = measure.textbbox((0, 0), "Xg", font=team_font)
    y += (bbox[3] - bbox[1]) + 22
    y += 46
    y += 26
    y += 40
    for _ in shown:
        y += 34 + 30 + 18
    if hidden_count > 0:
        y += 26
    content_bottom = y

    poster_h = max(POSTER_MIN_H, content_bottom + 40 + 22 + pad)

    img = vertical_gradient(POSTER_W, poster_h, primary, dark_stop)
    draw = ImageDraw.Draw(img)

    y = pad
    draw_tracked_text(draw, (pad, y), "EST. 1869 · LINEAL CHAMPIONSHIP", eyebrow_font, ink, tracking=2)
    y += 32
    draw.text((pad, y), "THE COLLEGE FOOTBALL BELT", font=wordmark_font, fill=ink)
    y += 56

    draw.text((pad, y), team, font=team_font, fill=accent)
    y += (bbox[3] - bbox[1]) + 22

    draw.text((pad, y), stat_line, font=stat_font, fill=ink)
    y += 46

    draw.line([(pad, y), (POSTER_W - pad, y)], fill=ink, width=1)
    y += 26
    draw_tracked_text(draw, (pad, y), "BELT HISTORY", eyebrow_font, ink, tracking=2)
    y += 40

    for r in shown:
        start = _dt.date.fromisoformat(r["start_date"])
        is_current = r.get("end_date") is None
        end = today if is_current else _dt.date.fromisoformat(r["end_date"])
        days = (end - start).days
        date_range = f"{start.strftime('%b %Y')} – {'present' if is_current else end.strftime('%b %Y')}"
        draw.text((pad, y), date_range, font=row_font, fill=accent if is_current else ink)
        def_txt = f"{r.get('defenses', 0)} def."
        def_w = draw.textbbox((0, 0), def_txt, font=row_sub_font)[2]
        draw.text((POSTER_W - pad - def_w, y + 6), def_txt, font=row_sub_font, fill=ink)
        y += 34
        draw.text((pad, y), fmt_duration_approx(days), font=row_sub_font, fill=ink)
        y += 30
        draw.line([(pad, y), (POSTER_W - pad, y)], fill=dark_stop, width=1)
        y += 18

    if hidden_count > 0:
        draw.text((pad, y), f"+ {hidden_count} earlier reign{'s' if hidden_count != 1 else ''}",
                   font=row_sub_font, fill=ink)

    url_text = "collegefootballbelt.com"
    url_bbox = draw.textbbox((0, 0), url_text, font=url_font)
    url_w = url_bbox[2] - url_bbox[0]
    draw.text((POSTER_W - pad - url_w, poster_h - pad - 22), url_text, font=url_font, fill=ink)

    img.save(out_path, "PNG")


def generate_team_posters(lineage, colors):
    """A poster PNG per program that's ever held the belt, written to
    site/posters/<slug>.png -- linked from that team's own page. Zero new
    data: same lineage.json + team_colors.json already loaded for the
    share image."""
    posters_dir = os.path.join(OUT_DIR, "posters")
    os.makedirs(posters_dir, exist_ok=True)

    by_team = {}
    for r in lineage["reigns"]:
        by_team.setdefault(r["team"], []).append(r)

    for team, reigns in by_team.items():
        primary, alt = team_color(colors, team)
        out_path = os.path.join(posters_dir, f"{_team_slug(team)}.png")
        generate_team_poster(team, reigns, primary, alt, out_path)

    print(f"Wrote {len(by_team)} team poster(s) to {posters_dir}/")


OTD_OUTCOME_LABELS = {
    # No emoji here on purpose -- unlike the tweet text (rendered by X's own
    # font stack), this pill is drawn with a plain TrueType font via Pillow,
    # which has no color-emoji glyphs and would render a broken tofu box
    # instead (confirmed by an actual test render).
    "established": "THE BELT WAS BORN",
    "changed": "BELT CHANGED HANDS",
    "retained (tie)": "TIE — BELT RETAINED",
    "retained": "SUCCESSFULLY DEFENDED",
}


def _otd_team_score(game, team):
    """Same lookup as post_on_this_day_to_x.py's team_score() -- game['score']
    is always "home-away", not "winner-loser", so this matches against
    game['home']/game['away'] rather than assuming an order."""
    if not team or "-" not in (game.get("score") or ""):
        return None
    home_pts, away_pts = game["score"].split("-", 1)
    if team == game.get("home"):
        return home_pts
    if team == game.get("away"):
        return away_pts
    return None


def _fetch_logo_chip(url, size):
    """A team's logo (if CFBD has one on file) centered on a white circular
    backdrop, ready to paste onto any colored panel -- same fix as
    build_site.py's logo_chip(): a logo dominated by its own team's color
    (Penn State's navy crest on a navy panel, say) would otherwise nearly
    disappear. Returns None on ANY failure (no logo URL, network error,
    unrecognized format) -- callers just render the panel without a logo
    rather than letting an image-fetch hiccup break the whole post."""
    if not url:
        return None
    try:
        import io
        import requests
        from PIL import Image, ImageDraw
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        logo = Image.open(io.BytesIO(resp.content)).convert("RGBA")

        chip = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(chip)
        d.ellipse([0, 0, size, size], fill=(255, 255, 255, 255))

        inner = round(size * 0.76)
        logo.thumbnail((inner, inner))
        chip.paste(logo, ((size - logo.width) // 2, (size - logo.height) // 2), logo)
        return chip
    except Exception:
        return None


# A fixed metallic gold (plus a matching leather strap brown), not derived
# from either team's colors -- this is what actually makes it read as "the
# belt" (a real championship belt is a gold buckle on a leather strap) no
# matter which two teams are playing, so it's the one constant across
# every image this function ever produces.
BADGE_GOLD = "#D4AF37"
BADGE_GOLD_DARK = "#8a6c17"
BADGE_STRAP = "#3d2812"


def _belt_badge(size, ink_bg, gold, paper):
    """The championship-belt badge that sits on the seam between the two
    panels -- a circular medallion drawing the SAME mark as
    BELT_MARK_SVG (build_site.py's nav logo) and draw_belt_icon() above: a
    leather strap, two brass side plates, and a hexagonal brass shield
    with a dark center stud -- not a generic round buckle. Built on its
    own transparent canvas so it can be dropped onto the seam (with a
    shadow) regardless of what's behind it, same paste-with-alpha-mask
    trick as _fetch_logo_chip."""
    from PIL import Image, ImageDraw
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(badge)
    d.ellipse([0, 0, size - 1, size - 1], fill=hex_to_rgb(ink_bg) + (255,))

    cx = cy = size / 2
    th = size * 0.30
    _draw_belt_mark(d, cx, cy, th,
                     strap_color=hex_to_rgb(BADGE_STRAP) + (255,),
                     plate_color=hex_to_rgb(gold) + (255,),
                     plate_edge=hex_to_rgb(BADGE_GOLD_DARK) + (255,),
                     stud_color=hex_to_rgb(ink_bg) + (255,),
                     strap_span=size * 0.46)

    # Circular clip, then a bright ring drawn on top of the clipped edge.
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    badge.putalpha(mask)
    ImageDraw.Draw(badge).ellipse([1, 1, size - 2, size - 2], outline=hex_to_rgb(gold) + (255,),
                                   width=max(3, round(size * 0.04)))
    return badge


def _diagonal_stripe(size_wh, p1, p2, width, color_rgba):
    """One soft diagonal gloss stripe on its own transparent layer -- a
    thin parallelogram from p1 to p2, `width` px wide, low-alpha `color_rgba`
    -- meant to be alpha_composite'd over a panel for a bit of shine instead
    of a flat, static-looking gradient fill."""
    from PIL import Image, ImageDraw
    layer = Image.new("RGBA", size_wh, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    (x1, y1), (x2, y2) = p1, p2
    dx, dy = x2 - x1, y2 - y1
    length = max((dx ** 2 + dy ** 2) ** 0.5, 1)
    # perpendicular unit vector, to offset the centerline into a band
    ox, oy = -dy / length * width / 2, dx / length * width / 2
    d.polygon([(x1 + ox, y1 + oy), (x2 + ox, y2 + oy), (x2 - ox, y2 - oy), (x1 - ox, y1 - oy)], fill=color_rgba)
    return layer


def generate_otd_share_image(game, colors, out_path):
    """A 1200x630 share image for one "on this day" belt game -- built for
    post_on_this_day_to_x.py to attach to the daily X post instead of a bare
    wall of text (images/media get a real reach boost on X; a link-only
    text post doesn't). Same underlying color math as every other image in
    this file (vertical_gradient/panel_colors) and the same "each team in
    its own color, its own score in its own accent color" language as the
    live game page's scoreboard (build_site.py's .teamPanel.home/.away) --
    but with real graphic punch on top of that base: a diagonal seam
    between the two panels instead of a flat vertical split, a gold
    belt-buckle badge sitting on that seam (the one constant across every
    matchup, since this is a picture of a BELT game), diagonal gloss
    stripes, drop-shadowed score numbers, and a filled pill badge for the
    outcome instead of bare text -- meant to read as a real sports-graphic,
    not a colored rectangle with words on it.

    Zero new data: `game` is one row from belt_games (already has both
    teams, score, outcome, date), `colors` is the same team_colors.json-
    shaped dict every other generator here takes. Logo lookups/fetches
    fail soft (see _fetch_logo_chip) -- a team CFBD doesn't have a logo
    for, or a network hiccup, still produces a clean image rather than no
    image at all."""
    from PIL import Image, ImageDraw

    away, home = game["away"], game["home"]
    away_primary, away_alt = team_color(colors, away)
    home_primary, home_alt = team_color(colors, home)
    away_ink, away_accent, away_dark = panel_colors(away_primary, away_alt)
    home_ink, home_accent, home_dark = panel_colors(home_primary, home_alt)

    BAR_H = 70
    half_w = W // 2
    panel_h = H - 2 * BAR_H
    INK_BAR = "#171310"
    PAPER = "#f4ede0"
    SEAM_SHIFT = 72  # total horizontal travel of the seam from top to bottom of the panel band

    img = Image.new("RGB", (W, H), hex_to_rgb(INK_BAR))

    # ---- diagonal seam: home fills the whole panel band, away is cut in
    # on top of it with a slanted polygon mask, so the boundary between
    # them is a clean chevron instead of a dead-center vertical line.
    top_x = half_w - SEAM_SHIFT // 2
    bottom_x = half_w + SEAM_SHIFT // 2
    img.paste(vertical_gradient(W, panel_h, home_primary, home_dark), (0, BAR_H))
    away_mask = Image.new("L", (W, panel_h), 0)
    ImageDraw.Draw(away_mask).polygon([(0, 0), (top_x, 0), (bottom_x, panel_h), (0, panel_h)], fill=255)
    img.paste(vertical_gradient(W, panel_h, away_primary, away_dark), (0, BAR_H), away_mask)

    # ---- diagonal gloss stripes, one per side, same slant as the seam --
    # a subtle lightening band rather than a flat static gradient, so the
    # panels catch a bit of "light".
    shine = Image.new("RGBA", img.size, (0, 0, 0, 0))
    away_stripe = _diagonal_stripe((W, H), (top_x - 210, BAR_H), (bottom_x - 210, H - BAR_H), 130, (255, 255, 255, 22))
    home_stripe = _diagonal_stripe((W, H), (top_x + 260, BAR_H), (bottom_x + 260, H - BAR_H), 130, (255, 255, 255, 22))
    shine.alpha_composite(away_stripe)
    shine.alpha_composite(home_stripe)
    img = Image.alpha_composite(img.convert("RGBA"), shine).convert("RGB")
    draw = ImageDraw.Draw(img)

    mono_sm = _font(_MONO_CANDIDATES, 20)
    side_font = _font(_MONO_CANDIDATES, 17)
    score_font = _font(_DISPLAY_CANDIDATES, 96)
    pill_font = _font(_MONO_CANDIDATES, 21)

    # Top bar: "ON THIS DAY" left, the full date right -- draw_tracked_text
    # adds `tracking` px after EVERY character (including the last), so a
    # plain textbbox() on the untracked string under-measures the actual
    # rendered width by about tracking*len(text); compensated for below so
    # the right-aligned date doesn't creep past the margin.
    draw_tracked_text(draw, (PAD, BAR_H // 2 - 9), "ON THIS DAY", mono_sm, PAPER, tracking=3)
    date_text = fmt_date(game["date"]).upper()
    date_w = draw.textbbox((0, 0), date_text, font=mono_sm)[2] + 2 * len(date_text)
    draw_tracked_text(draw, (W - PAD - date_w, BAR_H // 2 - 9), date_text, mono_sm, PAPER, tracking=2)

    def render_panel(team, score, x0, ink, accent, label, logo_url):
        # x0 is the left edge of this team's half (0 for away, half_w for
        # home) -- both panels render identically, left-aligned from their
        # own PAD margin. Safe against the diagonal seam above: at its most
        # extreme the seam is only SEAM_SHIFT/2 (36px) from center, well
        # inside the PAD (72px) margin either side of it.
        lx = x0 + PAD
        y = BAR_H + PAD
        chip = _fetch_logo_chip(logo_url, 60)
        if chip:
            img.paste(chip, (lx, y), chip)
            draw_tracked_text(draw, (lx + 60 + 16, y + 20), label, side_font, ink, tracking=3)
            name_y = y + 60 + 26
        else:
            draw_tracked_text(draw, (lx, y), label, side_font, ink, tracking=3)
            name_y = y + 30
        name_font = fit_font(draw, team, _DISPLAY_CANDIDATES, half_w - 2 * PAD, start_size=68, min_size=32)
        draw.text((lx, name_y), team, font=name_font, fill=ink)
        name_bbox = draw.textbbox((0, 0), "Xg", font=name_font)
        # A short accent-colored underline beneath the name -- a small
        # scoreboard-style detail that also visually separates name from
        # score without extra vertical space.
        name_w = draw.textbbox((0, 0), team, font=name_font)[2]
        rule_y = name_y + (name_bbox[3] - name_bbox[1]) + 6
        draw.rectangle([lx, rule_y, lx + min(name_w, 120), rule_y + 5], fill=hex_to_rgb(accent))
        score_y = rule_y + 20
        score_text = str(score) if score is not None else "-"
        # Drop shadow behind the score for some depth/punch, then the real
        # (accent-colored) score on top, offset a few px up-left.
        draw.text((lx + 4, score_y + 4), score_text, font=score_font, fill=INK_BAR)
        draw.text((lx, score_y), score_text, font=score_font, fill=accent)

    render_panel(away, _otd_team_score(game, away), 0, away_ink, away_accent, "AWAY", team_logo(colors, away))
    render_panel(home, _otd_team_score(game, home), half_w, home_ink, home_accent, "HOME", team_logo(colors, home))

    # ---- the belt-buckle badge, right on the seam, with a solid shadow
    # behind it for lift. Gold regardless of either team's colors -- see
    # BADGE_GOLD's own comment.
    badge_size = 116
    badge_cx, badge_cy = half_w, BAR_H + panel_h // 2
    shadow_off = 7
    draw.ellipse([badge_cx - badge_size // 2 - 3 + shadow_off, badge_cy - badge_size // 2 - 3 + shadow_off,
                  badge_cx + badge_size // 2 + 3 + shadow_off, badge_cy + badge_size // 2 + 3 + shadow_off],
                 fill=(0, 0, 0))
    badge = _belt_badge(badge_size, INK_BAR, BADGE_GOLD, PAPER)
    img.paste(badge, (badge_cx - badge_size // 2, badge_cy - badge_size // 2), badge)

    # Bottom bar: outcome as a filled pill badge (gold), url bottom-right.
    outcome_text = OTD_OUTCOME_LABELS.get(game["outcome"], "BELT GAME")
    ob_bbox = draw.textbbox((0, 0), outcome_text, font=pill_font)
    ob_w, ob_h = ob_bbox[2] - ob_bbox[0], ob_bbox[3] - ob_bbox[1]
    pill_pad_x, pill_pad_y = 22, 12
    pill_w, pill_h = ob_w + 2 * pill_pad_x, ob_h + 2 * pill_pad_y
    pill_x0 = (W - pill_w) // 2
    pill_y0 = H - BAR_H // 2 - pill_h // 2 - 2
    draw.rounded_rectangle([pill_x0, pill_y0, pill_x0 + pill_w, pill_y0 + pill_h], radius=pill_h // 2,
                            fill=hex_to_rgb(BADGE_GOLD))
    draw.text((pill_x0 + pill_pad_x, pill_y0 + pill_pad_y - ob_bbox[1]), outcome_text, font=pill_font, fill=hex_to_rgb(INK_BAR))

    url_text = "collegefootballbelt.com"
    url_w = draw.textbbox((0, 0), url_text, font=mono_sm)[2]
    draw.text((W - PAD - url_w, H - BAR_H // 2 - 10), url_text, font=mono_sm, fill="#c9c2b3")

    img.save(out_path, "PNG")
    return out_path


def generate_favicon(primary, accent, ink):
    """Writes site/favicon.png (32x32, referenced as the tab icon on every
    page), site/apple-touch-icon.png (180x180, for iOS home-screen
    bookmarks), and site/icon-192.png / icon-512.png (for manifest.json /
    "Add to Home Screen" on Android and desktop) -- same belt-buckle glyph,
    four sizes."""
    favicon = draw_belt_icon(32, primary, accent, ink)
    favicon.save(os.path.join(OUT_DIR, "favicon.png"), "PNG")
    touch_icon = draw_belt_icon(180, primary, accent, ink)
    touch_icon.save(os.path.join(OUT_DIR, "apple-touch-icon.png"), "PNG")
    icon_192 = draw_belt_icon(192, primary, accent, ink)
    icon_192.save(os.path.join(OUT_DIR, "icon-192.png"), "PNG")
    icon_512 = draw_belt_icon(512, primary, accent, ink)
    icon_512.save(os.path.join(OUT_DIR, "icon-512.png"), "PNG")
    print(f"Wrote {OUT_DIR}/favicon.png (32x32), "
          f"{OUT_DIR}/apple-touch-icon.png (180x180), and "
          f"{OUT_DIR}/icon-192.png / icon-512.png (PWA manifest icons)")


def main():
    from PIL import Image, ImageDraw

    lineage_path = os.path.join(DATA_DIR, "lineage.json")
    colors_path = os.path.join(DATA_DIR, "team_colors.json")
    if not os.path.exists(lineage_path) or not os.path.exists(colors_path):
        sys.exit(f"Can't find {lineage_path} / {colors_path} -- run "
                  f"build_lineage.py and fetch_team_colors.py first.")

    with open(lineage_path) as f:
        lineage = json.load(f)
    with open(colors_path) as f:
        colors = json.load(f)

    current = lineage["reigns"][-1]
    holder = current["team"]
    defenses = current.get("defenses", 0)

    primary, alt = team_color(colors, holder)
    ink, accent, dark_stop = panel_colors(primary, alt)

    os.makedirs(OUT_DIR, exist_ok=True)

    img = vertical_gradient(W, H, primary, dark_stop)
    draw = ImageDraw.Draw(img)

    eyebrow_font = _font(_MONO_CANDIDATES, 22)
    wordmark_font = _font(_DISPLAY_CANDIDATES, 34)
    stat_font = _font(_BODY_CANDIDATES, 30)
    url_font = _font(_MONO_CANDIDATES, 22)

    draw_tracked_text(draw, (PAD, PAD), "EST. 1869 · LINEAL CHAMPIONSHIP",
                       eyebrow_font, ink, tracking=2)
    draw.text((PAD, PAD + 38), "THE COLLEGE FOOTBALL BELT", font=wordmark_font, fill=ink)

    holder_font = fit_font(draw, holder, _DISPLAY_CANDIDATES, W - 2 * PAD,
                            start_size=132, min_size=56)
    holder_y = H // 2 - 40
    draw.text((PAD, holder_y), holder, font=holder_font, fill=accent)

    stat_bbox = draw.textbbox((0, 0), "Xg", font=holder_font)
    stat_y = holder_y + (stat_bbox[3] - stat_bbox[1]) + 30

    stat_line = f"Holds the belt since {fmt_date(current['start_date'])}"
    if defenses:
        stat_line += f" · {defenses} defense{'s' if defenses != 1 else ''} since"
    draw.text((PAD, stat_y), stat_line, font=stat_font, fill=ink)

    url_text = "collegefootballbelt.com"
    url_bbox = draw.textbbox((0, 0), url_text, font=url_font)
    url_w = url_bbox[2] - url_bbox[0]
    draw.text((W - PAD - url_w, H - PAD - 22), url_text, font=url_font, fill=ink)

    img.save(OUT_PATH, "PNG")
    print(f"Wrote {OUT_PATH} ({W}x{H}) for {holder}")

    generate_favicon(primary, accent, ink)
    generate_team_posters(lineage, colors)
    generate_team_share_images(lineage, colors)


if __name__ == "__main__":
    main()
