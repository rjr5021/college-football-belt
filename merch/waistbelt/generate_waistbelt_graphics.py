#!/usr/bin/env python3
"""
"Wear it low" belt graphic: a literal championship-belt strap, meant to be
printed across the waist/hem of a shirt so it reads like the wearer has
the belt on -- like a wrestling title belt. Same brand vocabulary as the
circular badge (octagon plate, side hinge plates, football/laces mark,
tracked mono type) but laid out as a wide horizontal strap instead of a
medallion.
"""
import json
import math
import os
import re
from PIL import Image, ImageDraw, ImageFont

MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

W, H = 3600, 1200
CX, CY = W / 2, H / 2


def team_slug(name):
    s = re.sub(r"[^A-Za-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "team"


def hex_to_rgba(h, a=255):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (r, g, b, a)


def _lum(hexcolor):
    hexcolor = hexcolor.lstrip("#")
    r, g, b = (int(hexcolor[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


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


def draw_tracked_text(draw, xy, text, fnt, fill, tracking=0, anchor="center"):
    """anchor: 'left', 'center', or 'right' horizontal anchor at xy."""
    x0, y0 = xy
    widths = [fnt.getlength(ch) for ch in text]
    total_w = sum(widths) + tracking * (len(text) - 1)
    if anchor == "center":
        x = x0 - total_w / 2
    elif anchor == "right":
        x = x0 - total_w
    else:
        x = x0
    asc, desc = fnt.getmetrics()
    y = y0 - (asc + desc) / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += w + tracking


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


def rounded_end_strap(draw, y0, y1, x0, x1, fill, taper=90):
    """A strap band that tapers to a point at both ends, like a belt
    running off-canvas around the body."""
    pts = [
        (x0, (y0 + y1) / 2),
        (x0 + taper, y0),
        (x1 - taper, y0),
        (x1, (y0 + y1) / 2),
        (x1 - taper, y1),
        (x0 + taper, y1),
    ]
    draw.polygon(pts, fill=fill)


def build_waistbelt(out_path, strap_color, plate, plate_edge, ink_text, accent_text,
                     team_name, mascot, status_text, tag_left, tag_right,
                     football_color):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    strap_h = 560
    y0, y1 = CY - strap_h / 2, CY + strap_h / 2
    x0, x1 = -40, W + 40

    # main strap
    rounded_end_strap(d, y0, y1, x0, x1, strap_color, taper=120)

    # stitch lines near each long edge
    stitch_inset = 34
    stitch_color = blend_rgba(accent_text, strap_color, 0.55)
    for yy in (y0 + stitch_inset, y1 - stitch_inset):
        dash_w, gap = 26, 20
        xx = 60
        while xx < W - 60:
            d.line([xx, yy, xx + dash_w, yy], fill=stitch_color, width=6)
            xx += dash_w + gap

    # rivet holes across the strap (skip the region the buckle assembly covers)
    plate_half_span = 900
    rivet_r = 12
    rivet_y_top, rivet_y_bot = CY - strap_h / 2 + 90, CY + strap_h / 2 - 90
    step = 130
    xx = 140
    while xx < W - 140:
        if abs(xx - CX) > plate_half_span:
            for ry in (rivet_y_top, rivet_y_bot):
                d.ellipse([xx - rivet_r, ry - rivet_r, xx + rivet_r, ry + rivet_r],
                          fill=blend_rgba(accent_text, strap_color, 0.35))
        xx += step

    # tag text left/right of the buckle, engraved on the strap
    tag_clearance = 190
    tag_font, _ = fit_font_to_width(tag_left, 560, 58, min_size=34, tracking=7)
    draw_tracked_text(d, (CX - plate_half_span - tag_clearance, CY), tag_left, tag_font,
                       blend_rgba(accent_text, strap_color, 0.8), tracking=7, anchor="right")
    tag_font2, _ = fit_font_to_width(tag_right, 560, 58, min_size=34, tracking=7)
    draw_tracked_text(d, (CX + plate_half_span + tag_clearance, CY), tag_right, tag_font2,
                       blend_rgba(accent_text, strap_color, 0.8), tracking=7, anchor="left")

    # --- buckle assembly (side hinge plates + center octagon), scaled up ---
    scale = 1.9

    def s(v):
        return v * scale

    for side in (-1, 1):
        plate_w, plate_h = s(230), s(430)
        px = CX + side * s(390)
        box = [px - plate_w / 2, CY - plate_h / 2, px + plate_w / 2, CY + plate_h / 2]
        d.rounded_rectangle(box, radius=s(26), fill=plate_edge)
        inset = s(16)
        d.rounded_rectangle([box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset],
                             radius=s(16), fill=plate)
        for ry in (CY - s(120), CY + s(120)):
            r = s(11)
            d.ellipse([px - r, ry - r, px + r, ry + r],
                      fill=blend_rgba(plate_edge, (0, 0, 0, 255), 0.75))

    for yoff in (-s(95), s(95)):
        d.line([CX - s(560), CY + yoff, CX - s(190), CY + yoff], fill=plate_edge, width=int(s(6)))
        d.line([CX + s(190), CY + yoff, CX + s(560), CY + yoff], fill=plate_edge, width=int(s(6)))

    for side in (-1, 1):
        dx = CX + side * s(615)
        for dy in (CY - s(95), CY + s(95)):
            r = s(7)
            d.ellipse([dx - r, dy - r, dx + r, dy + r], fill=plate_edge)

    oct_w, oct_h = s(500), s(560)
    d.polygon(octagon_points(CX, CY, oct_w, oct_h, s(90)), fill=plate_edge)
    inset = s(28)
    d.polygon(octagon_points(CX, CY, oct_w - inset * 2, oct_h - inset * 2, s(90) - inset * 0.6), fill=plate)

    inner_w = (oct_w - inset * 2) - s(70)

    name_font, _ = fit_font_to_width(team_name, inner_w, s(66), min_size=s(30), tracking=s(6))
    mascot_font, _ = fit_font_to_width(mascot, inner_w, s(66), min_size=s(30), tracking=s(6))
    status_font, _ = fit_font_to_width(status_text, inner_w, s(40), min_size=s(22), tracking=s(5))

    draw_tracked_text(d, (CX, CY - s(190)), team_name, name_font, ink_text, tracking=s(6))

    fw, fh = s(300), s(150)
    fb = [CX - fw / 2, CY - fh / 2 - s(15), CX + fw / 2, CY + fh / 2 - s(15)]
    d.ellipse(fb, fill=football_color)
    lace_x0, lace_x1 = CX - fw * 0.3, CX + fw * 0.3
    lace_y = CY - s(15)
    d.line([lace_x0, lace_y, lace_x1, lace_y], fill=plate, width=int(s(8)))
    xx2 = lace_x0 + 20
    while xx2 < lace_x1 - 10:
        d.line([xx2, lace_y - s(24), xx2, lace_y + s(24)], fill=plate, width=int(s(8)))
        xx2 += max(28, (lace_x1 - lace_x0 - 30) / 4)

    draw_tracked_text(d, (CX, CY + s(150)), mascot, mascot_font, ink_text, tracking=s(6))
    draw_tracked_text(d, (CX, CY + s(230)), status_text, status_font,
                       blend_rgba(ink_text, plate, 0.75), tracking=s(5))

    img.save(out_path)
    return img.size


def ordinal_status(entry):
    if entry["is_current"]:
        return "CURRENT CHAMPION"
    n = entry["reigns"]
    if n <= 1:
        return "CHAMPION"
    return f"{n}X CHAMPION"


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    teams = json.load(open(os.path.join(here, "team_list.json")))
    outdir = os.path.join(here, "waistbelt")
    os.makedirs(outdir, exist_ok=True)

    COLOR_OVERRIDES = {"Swarthmore": ("#782F40", "#cbb677")}
    for e in teams:
        if e["team"] in COLOR_OVERRIDES and not re.match(r"^#[0-9a-fA-F]{6}$", e["color"] or ""):
            e["color"], e["alt"] = COLOR_OVERRIDES[e["team"]]
        if not re.match(r"^#[0-9a-fA-F]{6}$", e["color"] or ""):
            e["color"] = "#a97f38"
        if not re.match(r"^#[0-9a-fA-F]{6}$", e["alt"] or ""):
            e["alt"] = "#211a12"

    manifest = []
    for e in teams:
        primary_lum = _lum(e["color"])
        alt_lum = _lum(e["alt"])
        if primary_lum < 0.45:
            strap_hex, plate_edge_hex = e["color"], e["alt"] if alt_lum > 0.35 else "#c9a227"
        elif alt_lum < 0.45:
            strap_hex, plate_edge_hex = e["alt"], e["color"]
        else:
            strap_hex, plate_edge_hex = "#161616", e["color"]

        strap_color = hex_to_rgba(strap_hex)
        plate_edge = hex_to_rgba(plate_edge_hex)
        plate = hex_to_rgba("#f2ead6")
        ink_text = hex_to_rgba(strap_hex) if _lum(strap_hex) < 0.55 else hex_to_rgba("#111111")
        accent_text = hex_to_rgba("#f2ead6")
        football_color = ink_text

        if e["is_current"] and e.get("current_since"):
            tag_right = f"SINCE {e['current_since']}"
        else:
            tag_right = f"EST. {e['first_year']}"

        slug = team_slug(e["team"])
        out_path = os.path.join(outdir, f"belt-waist-{slug}.png")
        size = build_waistbelt(
            out_path,
            strap_color=strap_color,
            plate=plate,
            plate_edge=plate_edge,
            ink_text=ink_text,
            accent_text=accent_text,
            team_name=e["team"].upper(),
            mascot=(e["mascot"] or "").upper(),
            status_text=ordinal_status(e),
            tag_left="EST. 1869",
            tag_right=tag_right,
            football_color=football_color,
        )
        manifest.append({"team": e["team"], "slug": slug, "file": f"belt-waist-{slug}.png"})
        print("wrote", slug, size)

    json.dump(manifest, open(os.path.join(outdir, "manifest.json"), "w"), indent=2)
    print("done:", len(manifest))
