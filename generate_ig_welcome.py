#!/usr/bin/env python3
"""
Renders a proper Instagram-native welcome post for @CollegeFBBelt --
1080x1350 (4:5 portrait, the tallest ratio Instagram's feed still shows at
full size, so it uses more of the scroll than a 1:1 square or the site's
1200x630 landscape share.png would).

This is deliberately a NEW composition, not a resize/crop of share.png --
share.png is shaped for Open Graph link previews (wide, holder-name-first)
and looks cramped or oddly cropped once forced into a vertical feed post.
This one is built vertical from the start: belt icon, wordmark, tagline,
then a "now defending" block for the current holder, using the same fixed
brand palette as x_branding/ (not team-colored -- this is an evergreen
welcome post, not a per-game result card).

Usage:
    python3 generate_ig_welcome.py

No API key, no network call -- reads belt_data/lineage.json for the
current holder's name/since-date/defense-count, same fields build_site.py
already computes for the homepage plate, and reuses generate_share_image's
own font/icon/gradient helpers so this matches the rest of the brand
exactly.
"""

import json
import os

from generate_share_image import (
    _DISPLAY_CANDIDATES, _BODY_CANDIDATES, _MONO_CANDIDATES,
    _font, draw_belt_icon, draw_tracked_text, fit_font, vertical_gradient,
)

OUT_DIR = "x_branding"
OUT_PATH = os.path.join(OUT_DIR, "ig_welcome.png")

BELT_DATA_DIR = "belt_data"

# Same fixed brand palette as x_branding/profile.png + banner.png.
BG_TOP = "#1f170e"
BG_BOTTOM = "#161009"
INK = "#ece3d1"
ACCENT = "#e0b46a"
MUTED = "#b6a98d"


def ordinal(n):
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def pretty_date(iso_date):
    from datetime import date
    y, m, d = (int(x) for x in iso_date.split("-"))
    months = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
    return f"{months[m - 1]} {d}, {y}"


def load_holder():
    path = os.path.join(BELT_DATA_DIR, "lineage.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        lineage = json.load(f)
    reigns = lineage["reigns"]
    current = reigns[-1]
    team_reign_num = sum(1 for r in reigns if r["team"] == current["team"]
                          and r["start_date"] <= current["start_date"])
    return {
        "team": current["team"],
        "since": current["start_date"],
        "defenses": current["defenses"],
        "reign_num": team_reign_num,
    }


def center_text(draw, cy, text, font, fill, w, tracking=0):
    """Draw text horizontally centered at width w, vertically centered on cy."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    if tracking:
        # draw_tracked_text draws letter-by-letter; measure the tracked width instead.
        tw += tracking * max(len(text) - 1, 0)
        x = (w - tw) // 2
        draw_tracked_text(draw, (x, cy - th // 2 - bbox[1]), text, font, fill, tracking=tracking)
        return th
    x = (w - tw) // 2 - bbox[0]
    draw.text((x, cy - th // 2 - bbox[1]), text, font=font, fill=fill)
    return th


def main():
    from PIL import ImageDraw

    w, h = 1080, 1350
    img = vertical_gradient(w, h, BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)

    holder = load_holder()

    # --- Belt icon, centered near the top ---
    icon_size = 220
    icon = draw_belt_icon(icon_size, BG_BOTTOM, ACCENT, INK)
    img.paste(icon, ((w - icon_size) // 2, 130), icon if icon.mode == "RGBA" else None)

    y = 130 + icon_size + 70

    eyebrow_font = _font(_MONO_CANDIDATES, 30)
    y += center_text(draw, y, "EST. 1869 · LINEAL CHAMPIONSHIP", eyebrow_font,
                      ACCENT, w, tracking=4)
    y += 46

    wordmark_font = fit_font(draw, "THE COLLEGE FOOTBALL BELT", _DISPLAY_CANDIDATES,
                              w - 160, start_size=72, min_size=46)
    y += center_text(draw, y, "THE COLLEGE FOOTBALL BELT", wordmark_font, INK, w)
    y += 70

    tagline_font = _font(_BODY_CANDIDATES, 34)
    tagline_lines = [
        "Whoever last beat the champ,",
        "on the field, holds the belt.",
    ]
    for line in tagline_lines:
        y += center_text(draw, y, line, tagline_font, MUTED, w)
        y += 12
    y += 90

    # --- Divider ---
    draw.line([(240, y), (w - 240, y)], fill=blend_hex(ACCENT, BG_BOTTOM, 0.35), width=2)
    y += 90

    # --- Now defending block ---
    if holder:
        now_font = _font(_MONO_CANDIDATES, 30)
        y += center_text(draw, y, "NOW DEFENDING", now_font, ACCENT, w, tracking=4)
        y += 60

        team_font = fit_font(draw, holder["team"], _DISPLAY_CANDIDATES,
                              w - 120, start_size=110, min_size=56)
        y += center_text(draw, y, holder["team"], team_font, ACCENT, w)
        y += 50

        detail_font = _font(_BODY_CANDIDATES, 32)
        reign_label = f'{ordinal(holder["reign_num"])} {holder["team"]} reign' if holder["reign_num"] else ""
        defense_word = "defense" if holder["defenses"] == 1 else "defenses"
        detail = f'Since {pretty_date(holder["since"])} · {holder["defenses"]} {defense_word}'
        y += center_text(draw, y, detail, detail_font, INK, w)
        if reign_label:
            y += 44
            small_font = _font(_BODY_CANDIDATES, 28)
            center_text(draw, y, reign_label, small_font, MUTED, w)
    else:
        now_font = _font(_BODY_CANDIDATES, 34)
        center_text(draw, y, "Tracking every belt game since 1869.", now_font, INK, w)

    # --- Footer ---
    url_font = _font(_MONO_CANDIDATES, 30)
    center_text(draw, h - 90, "collegefootballbelt.com", url_font, MUTED, w)

    os.makedirs(OUT_DIR, exist_ok=True)
    img.save(OUT_PATH, "PNG")
    print(f"Wrote {OUT_PATH} ({w}x{h})")


def blend_hex(hex1, hex2, weight1):
    from generate_share_image import blend
    return blend(hex1, hex2, weight1)


if __name__ == "__main__":
    main()
