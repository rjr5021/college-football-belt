#!/usr/bin/env python3
"""
Team-colored variants of the College Football Belt circular badge.
v2: bigger buckle + ring type, and shrink-to-fit sizing so long team
names / mascots never overflow the plate.
"""
import json
import math
import os
import re
from PIL import Image, ImageDraw, ImageFont

MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

W = H = 3000
CX = CY = W / 2


def team_slug(name):
    s = re.sub(r"[^A-Za-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "team"


def hex_to_rgba(h, a=255):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (r, g, b, a)


def font(path, size):
    return ImageFont.truetype(path, size)


def fit_font_to_width(text, max_width, start_size, min_size=28, tracking=0):
    size = start_size
    while size > min_size:
        f = font(MONO_BOLD, size)
        w = sum(f.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1)
        if w <= max_width:
            return f, w
        size -= 2
    f = font(MONO_BOLD, min_size)
    w = sum(f.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1)
    return f, w


def draw_tracked_text_centered(draw, center_xy, text, fnt, fill, tracking=0):
    cx, cy = center_xy
    widths = [fnt.getlength(ch) for ch in text]
    total_w = sum(widths) + tracking * (len(text) - 1)
    x = cx - total_w / 2
    asc, desc = fnt.getmetrics()
    y = cy - (asc + desc) / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += w + tracking


def draw_circular_text(base, text, center, radius, fnt, fill, center_angle_deg,
                        tracking_px=6, flip=False):
    cx, cy = center
    glyphs = []
    total_w = 0
    for ch in text:
        w = fnt.getlength(ch)
        glyphs.append((ch, w))
        total_w += w + tracking_px
    total_w -= tracking_px

    circumference = 2 * math.pi * radius
    total_angle = (total_w / circumference) * 360.0
    angle = center_angle_deg - total_angle / 2

    asc, desc = fnt.getmetrics()
    glyph_h = asc + desc
    seq = glyphs if not flip else list(reversed(glyphs))

    for ch, w in seq:
        char_angle_w = (w / circumference) * 360.0
        mid_angle = angle + char_angle_w / 2
        theta = math.radians(mid_angle)
        gx = cx + radius * math.sin(theta)
        gy = cy - radius * math.cos(theta)

        pad = 10
        tile = Image.new("RGBA", (int(w) + pad * 2, glyph_h + pad * 2), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(tile)
        tdraw.text((pad, pad - int(asc * 0.02)), ch, font=fnt, fill=fill)

        rot_deg = -mid_angle if not flip else 180 - mid_angle
        rotated = tile.rotate(rot_deg, resample=Image.BICUBIC, expand=True)
        rw, rh = rotated.size
        base.alpha_composite(rotated, (int(gx - rw / 2), int(gy - rh / 2)))

        angle += char_angle_w + (tracking_px / circumference) * 360.0


def blend_rgba(c1, c2, w1):
    return tuple(round(a * w1 + b * (1 - w1)) for a, b in zip(c1, c2))


def octagon_points(cx, cy, w, h, cut):
    x0, x1 = cx - w / 2, cx + w / 2
    y0, y1 = cy - h / 2, cy + h / 2
    return [
        (x0 + cut, y0), (x1 - cut, y0),
        (x1, y0 + cut), (x1, y1 - cut),
        (x1 - cut, y1), (x0 + cut, y1),
        (x0, y1 - cut), (x0, y0 + cut),
    ]


def draw_buckle(base, center, scale, plate, plate_edge):
    cx, cy = center
    d = ImageDraw.Draw(base)

    def s(v):
        return v * scale

    plate_w, plate_h = s(230), s(430)
    for side in (-1, 1):
        px = cx + side * s(390)
        box = [px - plate_w / 2, cy - plate_h / 2, px + plate_w / 2, cy + plate_h / 2]
        d.rounded_rectangle(box, radius=s(26), fill=plate_edge)
        inset = s(16)
        d.rounded_rectangle([box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset],
                             radius=s(16), fill=plate)
        rx = px
        for ry in (cy - s(120), cy + s(120)):
            r = s(11)
            d.ellipse([rx - r, ry - r, rx + r, ry + r],
                      fill=blend_rgba(plate_edge, (0, 0, 0, 255), 0.75))

    for yoff in (-s(95), s(95)):
        d.line([cx - s(560), cy + yoff, cx - s(190), cy + yoff], fill=plate_edge, width=int(s(6)))
        d.line([cx + s(190), cy + yoff, cx + s(560), cy + yoff], fill=plate_edge, width=int(s(6)))

    for side in (-1, 1):
        dx = cx + side * s(615)
        for dy in (cy - s(95), cy + s(95)):
            r = s(7)
            d.ellipse([dx - r, dy - r, dx + r, dy + r], fill=plate_edge)

    oct_w, oct_h = s(500), s(560)
    pts_outer = octagon_points(cx, cy, oct_w, oct_h, s(90))
    d.polygon(pts_outer, fill=plate_edge)
    inset = s(28)
    pts_inner = octagon_points(cx, cy, oct_w - inset * 2, oct_h - inset * 2, s(90) - inset * 0.6)
    d.polygon(pts_inner, fill=plate)

    # return the usable interior width/height for text (inner plate, minus margin)
    inner_w = (oct_w - inset * 2) - s(70)
    inner_h = oct_h - inset * 2
    return inner_w, inner_h


def build_badge(out_path, ring_color, disc_color, plate, plate_edge, ink_text,
                 accent_text, top_arc, bottom_arc, corner_top, corner_bottom,
                 buckle_line1, buckle_line2, football_color):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    outer_r = 1480
    ring_inner_r = 1270
    disc_r = ring_inner_r
    thin_ring_r = 1208
    thin_ring_w = 10

    d = ImageDraw.Draw(img)
    d.ellipse([CX - outer_r, CY - outer_r, CX + outer_r, CY + outer_r], fill=ring_color)
    d.ellipse([CX - disc_r, CY - disc_r, CX + disc_r, CY + disc_r], fill=disc_color)
    d.ellipse([CX - thin_ring_r, CY - thin_ring_r, CX + thin_ring_r, CY + thin_ring_r],
              outline=ring_color, width=thin_ring_w)

    # top arc wordmark -- bigger per feedback
    top_font, _ = fit_font_to_width(top_arc, 2500, 138, min_size=100, tracking=15)
    draw_circular_text(img, top_arc, (CX, CY), 1095, top_font, ring_color,
                        center_angle_deg=0, tracking_px=15, flip=False)

    # bottom arc status line -- bigger, and shrinks if long ("CURRENT CHAMPION")
    bottom_font, _ = fit_font_to_width(bottom_arc, 1900, 106, min_size=68, tracking=13)
    draw_circular_text(img, bottom_arc, (CX, CY), 1095, bottom_font, accent_text,
                        center_angle_deg=180, tracking_px=13, flip=True)

    # corner stamp (two lines), each shrink-to-fit independently
    max_corner_w = 620
    corner_top_font, _ = fit_font_to_width(corner_top, max_corner_w, 70, min_size=36, tracking=6)
    corner_bottom_font, _ = fit_font_to_width(corner_bottom, max_corner_w, 70, min_size=36, tracking=6)
    ang = math.radians(227)
    scx = CX + 900 * math.sin(ang)
    scy = CY - 900 * math.cos(ang)
    draw_tracked_text_centered(d, (scx, scy - 58), corner_top, corner_top_font, accent_text, tracking=6)
    draw_tracked_text_centered(d, (scx, scy + 58), corner_bottom, corner_bottom_font, ring_color, tracking=6)

    r = 13
    d.ellipse([CX - r, CY + 905 - r, CX + r, CY + 905 + r], fill=ring_color)

    # center buckle -- bigger scale per feedback
    scale = 1.42
    inner_w, inner_h = draw_buckle(img, (CX, CY), scale, plate, plate_edge)

    bd = ImageDraw.Draw(img)
    line1_font, _ = fit_font_to_width(buckle_line1, inner_w, 92, min_size=34, tracking=9)
    line2_font, _ = fit_font_to_width(buckle_line2, inner_w, 92, min_size=34, tracking=9)

    draw_tracked_text_centered(bd, (CX, CY - 230 * scale / 1.28), buckle_line1, line1_font, ink_text, tracking=9)

    fw, fh = 300 * scale / 1.28, 150 * scale / 1.28
    fb = [CX - fw / 2, CY - fh / 2, CX + fw / 2, CY + fh / 2]
    bd.ellipse(fb, fill=football_color)
    lace_x0, lace_x1 = CX - fw * 0.3, CX + fw * 0.3
    bd.line([lace_x0, CY, lace_x1, CY], fill=plate, width=9)
    step = max(1, int((lace_x1 - lace_x0 - 30) / 4))
    for lx in range(int(lace_x0) + 20, int(lace_x1) - 10, max(step, 28)):
        bd.line([lx, CY - 24, lx, CY + 24], fill=plate, width=9)

    draw_tracked_text_centered(bd, (CX, CY + 230 * scale / 1.28), buckle_line2, line2_font, ink_text, tracking=9)

    img.save(out_path)
    return img.size


def ordinal_status(entry):
    if entry["is_current"]:
        return "CURRENT CHAMPION"
    n = entry["reigns"]
    if n <= 1:
        return "CHAMPION"
    return f"{n}X CHAMPION"


def corner_lines(entry):
    top = entry["team"].upper()
    if entry["is_current"] and entry.get("current_since"):
        bottom = f"SINCE {entry['current_since']}"
    else:
        bottom = f"EST. {entry['first_year']}"
    return top, bottom


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    teams = json.load(open(os.path.join(here, "team_list.json")))
    outdir = os.path.join(here, "final")
    os.makedirs(outdir, exist_ok=True)

    # a couple of historical programs have no modern color data (small
    # schools the CFBD feed doesn't track colors for); fill in from their
    # known mascot/branding rather than leaving a broken hex.
    COLOR_OVERRIDES = {
        "Swarthmore": ("#782F40", "#cbb677"),  # Garnet Tide
    }
    for e in teams:
        if e["team"] in COLOR_OVERRIDES and (not re.match(r"^#[0-9a-fA-F]{6}$", e["color"] or "")):
            e["color"], e["alt"] = COLOR_OVERRIDES[e["team"]]
        if not re.match(r"^#[0-9a-fA-F]{6}$", e["color"] or ""):
            e["color"] = "#a97f38"
        if not re.match(r"^#[0-9a-fA-F]{6}$", e["alt"] or ""):
            e["alt"] = "#211a12"

    manifest = []
    for e in teams:
        ring = hex_to_rgba(e["color"])
        # dark ink disc derived from the team's primary color region: use
        # the primary color itself as the disc when it's already dark,
        # otherwise fall back to a near-black so text stays legible.
        disc = hex_to_rgba(e["color"])
        # if the primary color is light (poor contrast for cream/gold ring
        # text), swap ring/disc so the ring uses the lighter color and the
        # disc uses a dark neutral instead.
        def _lum(hexcolor):
            hexcolor = hexcolor.lstrip("#")
            r, g, b = (int(hexcolor[i:i + 2], 16) / 255 for i in (0, 2, 4))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        primary_lum = _lum(e["color"])
        alt_lum = _lum(e["alt"])

        if primary_lum < 0.45:
            ring_hex, disc_hex = e["alt"], e["color"]
        elif alt_lum < 0.45:
            ring_hex, disc_hex = e["color"], e["alt"]
        else:
            ring_hex, disc_hex = e["color"], "#161616"

        # guard: ring vs disc must themselves contrast reasonably, else force dark disc
        if _lum(ring_hex) < 0.45 and _lum(disc_hex) < 0.45:
            disc_hex = "#161616"
        if _lum(ring_hex) > 0.8 and disc_hex.lower() in ("#ffffff",):
            disc_hex = "#161616"

        ring_color = hex_to_rgba(ring_hex)
        disc_color = hex_to_rgba(disc_hex)
        plate = hex_to_rgba("#f2ead6")
        plate_edge = ring_color
        ink_text = disc_color if _lum(disc_hex) < 0.6 else (17, 17, 17, 255)
        accent_text = hex_to_rgba("#f2ead6")
        football_color = ink_text

        slug = team_slug(e["team"])
        out_path = os.path.join(outdir, f"belt-badge-{slug}.png")
        top, bottom = corner_lines(e)
        size = build_badge(
            out_path,
            ring_color=ring_color,
            disc_color=disc_color,
            plate=plate,
            plate_edge=plate_edge,
            ink_text=ink_text,
            accent_text=accent_text,
            top_arc="THE COLLEGE FOOTBALL BELT",
            bottom_arc=ordinal_status(e),
            corner_top=top,
            corner_bottom=bottom,
            buckle_line1=e["team"].upper(),
            buckle_line2=(e["mascot"] or "").upper(),
            football_color=football_color,
        )
        manifest.append({"team": e["team"], "slug": slug, "file": f"belt-badge-{slug}.png"})
        print("wrote", slug, size)

    json.dump(manifest, open(os.path.join(outdir, "manifest.json"), "w"), indent=2)
    print("done:", len(manifest))
