#!/usr/bin/env python3
"""
Render a static 1200x630 social-share image (Open Graph / Twitter Card
size) for whoever currently holds the College Football Belt -- team color
background, the holder's name, and how long they've held it.

Usage:
    python3 generate_share_image.py          # reads belt_data/, writes site/share.png

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

    # Build the vertical gradient at 1px wide, then stretch it -- H pixel
    # writes instead of W*H.
    top_rgb = hex_to_rgb(primary)
    bot_rgb = hex_to_rgb(dark_stop)
    grad = Image.new("RGB", (1, H))
    gpx = grad.load()
    for y in range(H):
        t = y / (H - 1)
        gpx[0, y] = tuple(round(top_rgb[i] + (bot_rgb[i] - top_rgb[i]) * t) for i in range(3))
    img = grad.resize((W, H))

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


if __name__ == "__main__":
    main()
