#!/usr/bin/env python3
"""
Renders the two fixed brand images for the @CollegeFBBelt X account:

    x_branding/profile.png   800x800   -- upload as the account's profile photo
    x_branding/banner.png    1500x500  -- upload as the account's header image

Deliberately NOT team-colored and NOT part of update_all.py's pipeline --
unlike site/share.png (which re-renders in the CURRENT holder's colors
every run because the holder changes), an account's profile photo and
banner should stay constant. These use the site's own fixed brand palette
(the dark "paper"/brass theme from build_site.py's CSS -- see --paper,
--ink, --brass-bright under `@media (prefers-color-scheme: dark)`) and the
same belt-buckle glyph already used for the site's favicon
(generate_share_image.draw_belt_icon), just at profile-picture scale and
in the brand's own colors instead of a team's.

This is a one-off/occasional script, not a pipeline stage: rerun it by
hand (`python3 generate_x_branding.py`) only if the brand palette or
wordmark ever changes -- nothing else regenerates these automatically.

Usage:
    python3 generate_x_branding.py

No API key, no network call, no belt_data/ dependency at all -- just the
fonts already used by generate_share_image.py, imported from that module
to render two static images.
"""

import os

from generate_share_image import (
    _DISPLAY_CANDIDATES, _BODY_CANDIDATES, _MONO_CANDIDATES,
    _font, draw_belt_icon, draw_tracked_text, fit_font, hex_to_rgb,
    vertical_gradient,
)

OUT_DIR = "x_branding"

# The site's own dark "paper"/brass palette (build_site.py's CSS, the
# `@media (prefers-color-scheme: dark)` block) -- fixed brand colors, not
# tied to whichever team currently holds the belt.
BG_TOP = "#1f170e"     # --paper-2 (dark)
BG_BOTTOM = "#161009"  # --paper (dark)
INK = "#ece3d1"        # --ink (dark) -- cream
ACCENT = "#e0b46a"     # --brass-bright (dark) -- gold
MUTED = "#b6a98d"      # --ink-soft (dark)


def make_profile(size=800):
    # Same glyph as the site's own favicon/apple-touch-icon -- background,
    # strap band, outlined buckle -- just rendered at the brand's fixed
    # colors instead of a team's, and much larger.
    img = draw_belt_icon(size, BG_BOTTOM, ACCENT, INK)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "profile.png")
    img.save(path, "PNG")
    print(f"Wrote {path} ({size}x{size})")


def make_banner(w=1500, h=500):
    from PIL import Image, ImageDraw

    img = vertical_gradient(w, h, BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    pad = 90

    eyebrow_font = _font(_MONO_CANDIDATES, 26)
    wordmark_font = _font(_DISPLAY_CANDIDATES, 64)
    sub_font = _font(_BODY_CANDIDATES, 30)
    url_font = _font(_MONO_CANDIDATES, 24)

    # Small belt-buckle glyph, top-left, echoing the profile picture.
    icon_size = 84
    icon = draw_belt_icon(icon_size, BG_BOTTOM, ACCENT, INK)
    img.paste(icon, (pad, pad))

    text_x = pad
    text_y = pad + icon_size + 34

    draw_tracked_text(draw, (text_x, text_y), "EST. 1869 · LINEAL CHAMPIONSHIP",
                       eyebrow_font, ACCENT, tracking=3)
    text_y += 40

    wordmark = "THE COLLEGE FOOTBALL BELT"
    wordmark_font = fit_font(draw, wordmark, _DISPLAY_CANDIDATES,
                              w - 2 * pad, start_size=64, min_size=40)
    draw.text((text_x, text_y), wordmark, font=wordmark_font, fill=INK)
    bbox = draw.textbbox((0, 0), wordmark, font=wordmark_font)
    text_y += (bbox[3] - bbox[1]) + 26

    subline = "Whoever last beat the holder, on the field, holds the belt."
    draw.text((text_x, text_y), subline, font=sub_font, fill=MUTED)

    url_text = "collegefootballbelt.com"
    url_bbox = draw.textbbox((0, 0), url_text, font=url_font)
    url_w = url_bbox[2] - url_bbox[0]
    draw.text((w - pad - url_w, h - pad - 4), url_text, font=url_font, fill=MUTED)

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "banner.png")
    img.save(path, "PNG")
    print(f"Wrote {path} ({w}x{h})")


def main():
    make_profile()
    make_banner()


if __name__ == "__main__":
    main()
