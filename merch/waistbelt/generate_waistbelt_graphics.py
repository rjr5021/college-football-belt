#!/usr/bin/env python3
"""
"Wear it low" belt graphic: a literal championship-belt strap, meant to be
printed across the waist/hem of a shirt so it reads like the wearer has
the belt on -- like a wrestling title belt. Same brand vocabulary as the
circular badge (octagon plate, side hinge plates, football/laces mark,
tracked mono type) but laid out as a wide horizontal strap instead of a
medallion.

Two variants are rendered per team from the same layout code:
  - "tee"    taper=120, scale=TEE_SCALE  -- strap tapers to a point at each
             end (belt running off the body), buckle sized up so it reads
             as a big wrestling-title buckle on a shirt hem.
  - "koozie" taper=0,   scale=KOOZIE_SCALE -- strap runs straight to both
             edges with no taper/notch, so when wrapped around a koozie it
             reads as one continuous loop instead of a belt with visible
             ends.

All buckle text (team name / mascot / status) is laid out from *measured*
font metrics against the inner octagon plate bounds, not hardcoded pixel
offsets, so it can never cross the plate's edge regardless of scale.
"""
import json
import math
import os
import re
from PIL import Image, ImageDraw, ImageFont

MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

W, H = 3600, 1500
CX, CY = W / 2, H / 2

TEE_SCALE = 2.4
KOOZIE_SCALE = 1.6


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
    return ImageFont.truetype(path, max(1, round(size)))


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


def text_half_height(fnt):
    asc, desc = fnt.getmetrics()
    return (asc + desc) / 2


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
    """A strap band. With taper > 0 it tapers to a point at both ends, like
    a belt running off-canvas around the body. With taper == 0 it is a
    plain straight-ended band -- used for the koozie wrap, which needs to
    read as one continuous loop with no visible "ends"."""
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
                     football_color, scale=1.9, taper=120, show_strap=True):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # scale helper for the buckle assembly -- defined up front so it can
    # also size the strap band and the rivet-clearance span below.
    def s(v):
        return v * scale

    if show_strap:
        strap_h = 320
        y0, y1 = CY - strap_h / 2, CY + strap_h / 2
        x0, x1 = -40, W + 40

        # main strap
        rounded_end_strap(d, y0, y1, x0, x1, strap_color, taper=taper)

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
        # -- scaled with the buckle itself so the clearance always matches its
        # actual footprint (outermost feature is the connecting-bar rivets at
        # s(615)+s(7)) instead of a fixed span that only fit the old smaller buckle.
        plate_half_span = s(650)
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

        # tag text left/right of the buckle, engraved on the strap -- the
        # available width shrinks as the buckle (plate_half_span) grows, so
        # size the tag text against what's actually left before the canvas
        # edge instead of a fixed budget that assumed a smaller buckle.
        tag_clearance = 40
        edge_margin = 50
        tag_avail_w = max(90, (CX - plate_half_span - tag_clearance) - edge_margin)
        tag_font, _ = fit_font_to_width(tag_left, tag_avail_w, 58, min_size=20, tracking=4)
        draw_tracked_text(d, (CX - plate_half_span - tag_clearance, CY), tag_left, tag_font,
                           blend_rgba(accent_text, strap_color, 0.8), tracking=4, anchor="right")
        tag_font2, _ = fit_font_to_width(tag_right, tag_avail_w, 58, min_size=20, tracking=4)
        draw_tracked_text(d, (CX + plate_half_span + tag_clearance, CY), tag_right, tag_font2,
                           blend_rgba(accent_text, strap_color, 0.8), tracking=4, anchor="left")

    # --- buckle assembly (side hinge plates + center octagon), scaled up ---
    # this is the part that stays even when show_strap is off -- gold hinge
    # plates + center octagon plate, nothing else.
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

    if show_strap:
        # connector bars + outer rivets that bridge the hinge plates to the
        # octagon across the strap -- only makes sense when there's a strap
        # underneath to bridge over. With no strap (bare buckle), skip them
        # so there's nothing gold floating outside the hinge plates/octagon.
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

    # --- vertical layout, measured -------------------------------------
    # Stack team name / football icon / mascot / status inside the inner
    # plate using *actual* font metrics, not hardcoded y-offsets, so no
    # row can ever cross the octagon's inner edge regardless of scale or
    # how long a team/mascot name is.
    inner_h = oct_h - inset * 2
    safety = s(22)
    content_top = CY - inner_h / 2 + safety
    content_bottom = CY + inner_h / 2 - safety
    avail = content_bottom - content_top

    name_half = text_half_height(name_font)
    mascot_half = text_half_height(mascot_font)
    status_half = text_half_height(status_font)
    icon_h = s(150)
    gap = s(16)

    total = (name_half * 2) + gap + icon_h + gap + (mascot_half * 2) + gap + (status_half * 2)
    if total > avail:
        # squeeze the gaps first, then the icon, before ever letting text
        # rows touch -- this keeps the layout valid for any team/mascot
        # name length or buckle scale.
        over = total - avail
        gap_reduction = min(gap - s(4), over / 3)
        gap -= gap_reduction
        over -= gap_reduction * 3
        if over > 0:
            icon_h = max(s(80), icon_h - over)
        total = (name_half * 2) + gap + icon_h + gap + (mascot_half * 2) + gap + (status_half * 2)

    cursor = content_top + max(0, (avail - total) / 2)
    name_cy = cursor + name_half
    cursor += name_half * 2 + gap
    icon_cy = cursor + icon_h / 2
    cursor += icon_h + gap
    mascot_cy = cursor + mascot_half
    cursor += mascot_half * 2 + gap
    status_cy = cursor + status_half

    draw_tracked_text(d, (CX, name_cy), team_name, name_font, ink_text, tracking=s(6))

    fw, fh = s(300), icon_h
    fb = [CX - fw / 2, icon_cy - fh / 2, CX + fw / 2, icon_cy + fh / 2]
    d.ellipse(fb, fill=football_color)
    # laces: a tight cluster of short, thin cross-ties over a narrow center
    # seam, like a real football -- not a wide ladder spanning the panel.
    lace_x0, lace_x1 = CX - fw * 0.16, CX + fw * 0.16
    lace_y = icon_cy
    seam_w = max(1, int(s(3.5)))
    d.line([CX - fw * 0.26, lace_y, CX + fw * 0.26, lace_y], fill=plate, width=seam_w)
    n_laces = 7
    lace_w = max(1, int(s(2.5)))
    for i in range(n_laces):
        t = i / (n_laces - 1)
        xx2 = lace_x0 + t * (lace_x1 - lace_x0)
        d.line([xx2, lace_y - s(13), xx2, lace_y + s(13)], fill=plate, width=lace_w)

    draw_tracked_text(d, (CX, mascot_cy), mascot, mascot_font, ink_text, tracking=s(6))
    draw_tracked_text(d, (CX, status_cy), status_text, status_font,
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


def crop_to_content(img, pad=30):
    """Tight-crop to the non-transparent content, then re-pad uniformly.
    Used for the tee (buckle-only) art so the print file isn't mostly
    empty canvas."""
    bbox = img.getbbox()
    if not bbox:
        return img
    cropped = img.crop(bbox)
    w, h = cropped.size
    out = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    out.paste(cropped, (pad, pad))
    return out


def roll_horizontal(img, frac=0.25):
    """Cyclically roll the image horizontally by `frac` of its width, with
    wraparound -- used for the koozie wrap so the buckle renders front-
    facing in Fourthwall's 3D mockup camera instead of at the print area's
    literal horizontal center."""
    w, h = img.size
    shift = int(w * frac)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img.crop((shift, 0, w, h)), (0, 0))
    out.paste(img.crop((0, 0, shift, h)), (w - shift, 0))
    return out


def _team_colors(e):
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
    return strap_color, plate, plate_edge, ink_text, accent_text, football_color


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
        strap_color, plate, plate_edge, ink_text, accent_text, football_color = _team_colors(e)

        if e["is_current"] and e.get("current_since"):
            tag_right = f"SINCE {e['current_since']}"
        else:
            tag_right = f"EST. {e['first_year']}"

        slug = team_slug(e["team"])
        common = dict(
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

        # tee: buckle-only (no strap), tight-cropped with a small margin so
        # the print file is mostly buckle, not empty canvas.
        tee_raw_path = os.path.join(outdir, f"_raw-{slug}-tee.png")
        build_waistbelt(tee_raw_path, scale=TEE_SCALE, taper=120, show_strap=False, **common)
        tee_img = crop_to_content(Image.open(tee_raw_path), pad=30)
        tee_path = os.path.join(outdir, f"belt-waist-{slug}.png")
        tee_img.save(tee_path)
        os.remove(tee_raw_path)

        # koozie: full strap+buckle loop, rolled 25% so the buckle lands
        # front-facing in the 3D mockup camera instead of at the print
        # area's literal horizontal center.
        koozie_raw_path = os.path.join(outdir, f"_raw-{slug}-koozie.png")
        build_waistbelt(koozie_raw_path, scale=KOOZIE_SCALE, taper=0, show_strap=True, **common)
        koozie_img = roll_horizontal(Image.open(koozie_raw_path), frac=0.25)
        koozie_path = os.path.join(outdir, f"belt-waist-{slug}-koozie.png")
        koozie_img.save(koozie_path)
        os.remove(koozie_raw_path)

        manifest.append({"team": e["team"], "slug": slug,
                          "file": f"belt-waist-{slug}.png",
                          "koozie_file": f"belt-waist-{slug}-koozie.png"})
        print("wrote", slug, tee_img.size, koozie_img.size)

    json.dump(manifest, open(os.path.join(outdir, "manifest.json"), "w"), indent=2)
    print("done:", len(manifest))
