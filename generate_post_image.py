#!/usr/bin/env python3
"""
Render the image that goes out attached to an automated X post -- the
1200x675 landscape version of the hand-made Instagram "Belt defended" /
"Belt on the line" cards.

Why this file and not the Instagram route: those cards are laid out in
HTML and rendered with a headless Chromium, with webfonts installed from
npm and team logos fetched as base64 -- fine for a human doing it once a
week, too much machinery to put in a GitHub Actions job that has to run
unattended four or five times on a Saturday. This draws the same design
with Pillow only, exactly the way generate_share_image.py already draws
site/share.png: no browser, no webfont, and the only network call is the
team logo, which degrades to the team's own letter chip if it can't be
had. Nothing in here is allowed to stop a post: card() swallows every
exception and returns None, and the caller posts without a picture.

    python3 generate_post_image.py          # renders one of each to site/post/

Sizes: X shows a 16:9 image uncropped in the timeline. 1200x675 is that
ratio at the resolution X keeps without re-encoding.
"""

import json
import os
import re
import tempfile

from generate_share_image import (
    _font, _draw_belt_mark, blend, contrast_ratio, draw_tracked_text,
    fit_font, hex_to_rgb, panel_colors, team_color, team_logo,
    _BODY_CANDIDATES, _DISPLAY_CANDIDATES, _MONO_CANDIDATES,
)

W, H = 1200, 675
PAD = 48

PAPER = "#e7e2d5"
INK = "#211a12"
BRASS = "#a97f38"
BRASS_TEXT = "#725626"
MUTED = "#5b5140"
HAIR = blend(INK, PAPER, 0.16)

DATA_DIR = "belt_data"

# Cards are a transport artifact, not a build output: they exist only long
# enough for media_upload() to read them. Keeping them in the system temp
# dir rather than under site/ means they are never deployed to the live
# site, and never turn up as repo churn in the pipeline's commit-back.
# The logo cache goes with them -- within one live-game run (which loops
# for hours) it still means one fetch per team, not one per post.
OUT_DIR = os.path.join(tempfile.gettempdir(), "belt-post-cards")
LOGO_CACHE = os.path.join(OUT_DIR, "logos")


def team_chip(name):
    """Must match build_site.py's team_chip() -- the same 2-4 letter code
    the site itself shows, so the buckle on the card and the chip on the
    page never disagree."""
    base = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip()
    words = [w for w in re.split(r"[\s\-]+", base) if w]
    letters = [w[0].upper() for w in words if w[0].isalpha()]
    if len(letters) >= 2:
        return "".join(letters[:4])
    return (re.sub(r"[^A-Za-z]", "", base).upper()[:4] or "?")


def fetch_logo(url, name):
    """The team's logo as an RGBA image, or None. Cached on disk by team so
    a Saturday with four live posts makes one request per team, not four,
    and a run with no network at all just falls back to the letter chip."""
    if not url:
        return None
    from PIL import Image
    os.makedirs(LOGO_CACHE, exist_ok=True)
    path = os.path.join(LOGO_CACHE, re.sub(r"[^A-Za-z0-9]+", "-", name.lower()) + ".png")
    if not os.path.exists(path):
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=8) as r:
                data = r.read()
            if not data.startswith(b"\x89PNG"):
                return None
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:
            print(f"  (no logo for {name}: {e})")
            return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def hatch(img, box, color, opacity=10, step=16):
    """The faint diagonal weave over each team panel -- the one texture the
    HTML template has that a flat fill would lose."""
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rgba = hex_to_rgb(color) + (opacity,)
    for off in range(-(y1 - y0), x1 - x0, step):
        d.line([(off, y1 - y0), (off + (y1 - y0), 0)], fill=rgba, width=2)
    img.paste(Image.alpha_composite(img.crop(box).convert("RGBA"), layer).convert("RGB"), (x0, y0))


def panel_gradient(w, h, top_hex, bottom_hex):
    from PIL import Image
    top, bot = hex_to_rgb(top_hex), hex_to_rgb(bottom_hex)
    grad = Image.new("RGB", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / (h - 1) if h > 1 else 0
        px[0, y] = tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3))
    return grad.resize((w, h))


def draw_chip(img, draw, cx, cy, r, logo, label, ring_ink):
    """The round paper disc each team sits behind: its logo when there is
    one, its letter code when there isn't."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hex_to_rgb(PAPER))
    if logo is not None:
        side = int(r * 1.5)
        fitted = logo.copy()
        fitted.thumbnail((side, side))
        img.paste(fitted, (int(cx - fitted.width / 2), int(cy - fitted.height / 2)), fitted)
        return
    on_paper = ring_ink if contrast_ratio(ring_ink, PAPER) >= 4.5 else INK
    f = fit_font(draw, label, _DISPLAY_CANDIDATES, int(r * 1.5), int(r * 1.1), 20)
    box = draw.textbbox((0, 0), label, font=f)
    draw.text((cx - (box[2] - box[0]) / 2 - box[0], cy - (box[3] - box[1]) / 2 - box[1]),
              label, font=f, fill=hex_to_rgb(on_paper))


def ellipsize(draw, text, font, max_w):
    while text and draw.textbbox((0, 0), text, font=font)[2] > max_w:
        text = text[:-2].rstrip()
        if len(text) < 8:
            break
        text += "…" if not text.endswith("…") else ""
        text = text.rstrip("…") + "…"
    return text


def generate_post_image(spec, out_path, colors=None):
    """One card. `spec`:
        kind     'result' | 'preview'  -- scores, or no scores
        headline ('BELT', 'DEFENDED')  -- drawn two-tone, ink then brass
        kicker   the small tracked line above the headline
        when     the tracked line under it
        left/right  {team, role, score, stat}
        buckle   the letter code on the centre buckle
        caption  the small line under the buckle
        note     one italic sentence under the card
    """
    from PIL import Image, ImageDraw
    colors = colors if colors is not None else {}
    img = Image.new("RGB", (W, H), hex_to_rgb(PAPER))
    draw = ImageDraw.Draw(img)

    disp = lambda s: _font(_DISPLAY_CANDIDATES, s)
    mono = lambda s: _font(_MONO_CANDIDATES, s)
    body = lambda s: _font(_BODY_CANDIDATES, s)

    # --- masthead -------------------------------------------------------
    _draw_belt_mark(draw, PAD + 26, PAD + 12, 13, strap_color=hex_to_rgb(INK),
                    plate_color=hex_to_rgb(BRASS), plate_edge=hex_to_rgb(INK),
                    stud_color=hex_to_rgb(INK), strap_span=26)
    wm = disp(26)
    draw.text((PAD + 66, PAD - 3), "THE COLLEGE FOOTBALL BELT", font=wm, fill=hex_to_rgb(INK))
    est, est_f = "EST. 1869 · LINEAL TITLE", mono(14)
    est_w = draw.textbbox((0, 0), est, font=est_f)[2] + 2 * (len(est) - 1)
    draw_tracked_text(draw, (W - PAD - est_w, PAD + 2), est, est_f, hex_to_rgb(BRASS_TEXT), tracking=2)
    draw.line([(PAD, PAD + 44), (W - PAD, PAD + 44)], fill=hex_to_rgb(HAIR), width=2)

    # --- headline -------------------------------------------------------
    draw.ellipse([PAD, PAD + 63, PAD + 10, PAD + 73], fill=hex_to_rgb(BRASS))
    draw_tracked_text(draw, (PAD + 22, PAD + 60), spec["kicker"].upper(), mono(14),
                      hex_to_rgb(BRASS_TEXT), tracking=3)
    head_a, head_b = spec["headline"]
    head = f"{head_a} {head_b}".upper()
    hf = fit_font(draw, head, _DISPLAY_CANDIDATES, W - 2 * PAD, 78, 44)
    hy = PAD + 86
    draw.text((PAD, hy), head_a.upper(), font=hf, fill=hex_to_rgb(INK))
    ax = PAD + draw.textbbox((0, 0), head_a.upper() + " ", font=hf)[2]
    draw.text((ax, hy), head_b.upper(), font=hf, fill=hex_to_rgb(BRASS_TEXT))
    hh = draw.textbbox((0, 0), "Xg", font=hf)[3]
    draw_tracked_text(draw, (PAD, hy + hh + 10), spec["when"].upper(), mono(15),
                      hex_to_rgb(INK), tracking=2)

    # --- the two team panels --------------------------------------------
    card_top, card_bot = 262, 572
    half = (W - 2 * PAD) // 2
    seam_x = PAD + half
    result = spec["kind"] == "result"
    lead = spec.get("emphasis", "left")

    def h_of(font, sample="Xg"):
        return draw.textbbox((0, 0), sample, font=font)[3]

    for side in ("left", "right"):
        side_spec = spec[side]
        team = side_spec["team"]
        primary, alt = team_color(colors, team)
        pink, accent, dark = panel_colors(primary, alt)
        x0 = PAD if side == "left" else seam_x
        box = (x0, card_top, x0 + half, card_bot)
        img.paste(panel_gradient(half, card_bot - card_top, primary, dark), (x0, card_top))
        hatch(img, box, "#ffffff", opacity=12)

        inner = 28
        left_edge, right_edge = x0 + inner, x0 + half - inner
        anchor = left_edge if side == "left" else right_edge
        max_w = half - 2 * inner

        logo = fetch_logo(team_logo(colors, team), team)
        chip_r = 34
        chip_cx = (left_edge + chip_r) if side == "left" else (right_edge - chip_r)
        draw_chip(img, draw, chip_cx, card_top + 24 + chip_r, chip_r, logo,
                  team_chip(team), primary)

        def put(text, font, fill, y, track=0):
            w = draw.textbbox((0, 0), text, font=font)[2] + (track * max(0, len(text) - 1))
            x = anchor if side == "left" else anchor - w
            if track:
                draw_tracked_text(draw, (x, y), text, font, fill, tracking=track)
            else:
                draw.text((x, y), text, font=font, fill=fill)
            return h_of(font, text or "Xg")

        y = card_top + 24 + 2 * chip_r + 20
        rf = mono(14)
        put(ellipsize(draw, side_spec["role"].upper(), rf, max_w - 30), rf,
            hex_to_rgb(accent if side == lead else pink), y, track=2)
        y += h_of(rf) + 10
        if result:
            sf = disp(76)
            put(str(side_spec["score"]), sf, hex_to_rgb(accent if side == lead else pink), y)
            y += h_of(sf, "88") + 4
            tf = fit_font(draw, team, _DISPLAY_CANDIDATES, max_w, 32, 20)
            put(team, tf, hex_to_rgb(pink), y)
            y += h_of(tf, team) + 12
        else:
            y += 18
            tf = fit_font(draw, team, _DISPLAY_CANDIDATES, max_w, 68, 28)
            put(team, tf, hex_to_rgb(accent if side == lead else pink), y)
            y += h_of(tf, team) + 20
        if side_spec.get("stat"):
            stf = mono(14)
            put(ellipsize(draw, side_spec["stat"], stf, max_w), stf, hex_to_rgb(pink), y)

    draw.rectangle([seam_x - 2, card_top, seam_x + 2, card_bot], fill=hex_to_rgb(BRASS))

    # --- the buckle on the seam -----------------------------------------
    by = card_top + 58
    _draw_belt_mark(draw, seam_x, by, 44, strap_color=hex_to_rgb("#241b10"),
                    plate_color=hex_to_rgb(BRASS), plate_edge=hex_to_rgb(INK),
                    stud_color=hex_to_rgb(BRASS), strap_span=108)
    bf = fit_font(draw, spec["buckle"], _DISPLAY_CANDIDATES,
                  46 if len(spec["buckle"]) > 3 else 52,
                  38 if len(spec["buckle"]) < 4 else 28, 14)
    bb = draw.textbbox((0, 0), spec["buckle"], font=bf)
    draw.text((seam_x - (bb[2] - bb[0]) / 2 - bb[0], by - (bb[3] - bb[1]) / 2 - bb[1]),
              spec["buckle"], font=bf, fill=hex_to_rgb(INK))
    if spec.get("caption"):
        cf, cap = mono(13), spec["caption"].upper()
        cw = draw.textbbox((0, 0), cap, font=cf)[2] + 3 * (len(cap) - 1)
        ch = draw.textbbox((0, 0), "Xg", font=cf)[3]
        cy = by + 56
        draw.rounded_rectangle([seam_x - cw / 2 - 16, cy - 7, seam_x + cw / 2 + 16, cy + ch + 7],
                               radius=4, fill=hex_to_rgb("#241b10"))
        draw_tracked_text(draw, (seam_x - cw / 2, cy), cap, cf, hex_to_rgb(PAPER), tracking=3)

    # --- note and footer -------------------------------------------------
    if spec.get("note"):
        nf = body(19)
        draw.text((PAD, card_bot + 14), ellipsize(draw, spec["note"], nf, W - 2 * PAD),
                  font=nf, fill=hex_to_rgb(MUTED))
    ff = mono(15)
    draw_tracked_text(draw, (PAD, H - PAD - 4), "@COLLEGEFBBELT", ff, hex_to_rgb(BRASS_TEXT), tracking=2)
    url = "COLLEGEFOOTBALLBELT.COM"
    uw = draw.textbbox((0, 0), url, font=ff)[2] + 2 * (len(url) - 1)
    draw_tracked_text(draw, (W - PAD - uw, H - PAD - 4), url, ff, hex_to_rgb(INK), tracking=2)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def _ord(n):
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _times(n):
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _facts(next_game, lineage, rankings, today=None):
    """Everything all three cards draw from, computed once. Same files the
    posts themselves read, so a card can never disagree with the text it
    is attached to."""
    from datetime import date
    import post_to_x as ptx
    from challenger import belt_story

    today = today or date.today()
    holder, opp = next_game["team"], next_game["opponent"]
    game_day = date.fromisoformat(next_game["date"])
    reign = lineage["reigns"][-1]
    days = (game_day - date.fromisoformat(reign["start_date"])).days
    story = belt_story(opp, holder, lineage, today)

    kick = ptx.kickoff_et(next_game, ptx.eastern_tz())
    watch = next_game.get("watch") or next_game.get("tv") or next_game.get("stream")
    when = (f'{game_day:%a %b} {game_day.day} · {kick or "Time TBD"} · '
            f'{next_game["venue_name"]}')
    if watch:
        when += f" · {watch}"

    return {
        "holder": holder, "opp": opp, "game_day": game_day, "when": when,
        "days": days,
        "defense_no": (reign.get("defenses") or 0) + 1,
        "defenses": reign.get("defenses") or 0,
        "holder_reign_no": sum(1 for r in lineage["reigns"] if r["team"] == holder),
        "holder_all_days": sum(
            ((date.fromisoformat(r["end_date"]) if r.get("end_date") else game_day)
             - date.fromisoformat(r["start_date"])).days
            for r in lineage["reigns"] if r["team"] == holder),
        "next_reign_no": len(lineage["reigns"]) + 1,
        "opp_reign_no": sum(1 for r in lineage["reigns"] if r["team"] == opp) + 1,
        "game_no": len(lineage["belt_games"]) + 1,
        "rank_h": ptx.ranked(holder, rankings),
        "story": story,
        "opp_last": (f'last held {story["last_held"]}' if story.get("last_held")
                     else "never held it"),
        "holder_stat": f'{days} days held · {reign.get("defenses") or 0} defenses',
        "opp_stat": (f'{story["reigns"]} reigns · {story["days"]} days all-time'
                     if story["reigns"] else "Never held the belt"),
    }


def preview_spec(next_game, lineage, rankings, today=None):
    f = _facts(next_game, lineage, rankings, today)
    return {
        "kind": "preview",
        "kicker": "Next belt game · Saturday",
        "headline": ("Belt", "on the line"),
        "when": f["when"],
        "left": {"team": f["holder"], "role": f'Holder · {_ord(f["holder_reign_no"])} reign',
                 "stat": f["holder_stat"]},
        "right": {"team": f["opp"], "role": f'Challenger · {f["opp_last"]}',
                  "stat": f["opp_stat"]},
        "buckle": team_chip(f["holder"]),
        "caption": f'Win and {f["opp"]} takes it',
        "note": f["story"]["ranked"][0],
    }


def gameday_spec(next_game, lineage, rankings, today=None):
    f = _facts(next_game, lineage, rankings, today)
    return {
        "kind": "preview",
        "kicker": f'Game day · Belt game No. {f["game_no"]:,}',
        "headline": ("Game", "day"),
        "when": f["when"],
        "left": {"team": f["holder"], "role": f'Holder · {_ord(f["defense_no"])} defense',
                 "stat": f["holder_stat"]},
        "right": {"team": f["opp"], "role": f'Challenger · {f["opp_last"]}',
                  "stat": f["opp_stat"]},
        "buckle": team_chip(f["holder"]),
        "caption": f'{f["opp"]} takes the belt with a win',
        "note": (f'{f["rank_h"]}: {_ord(f["defense_no"])} defense of its '
                 f'{_ord(f["holder_reign_no"])} reign.'),
    }


def final_spec(next_game, lineage, rankings, holder_score, opp_score, today=None):
    """The card for the live final post. `lineage` here is still the
    PRE-game archive -- post_live_game.py runs minutes after the whistle,
    long before the pipeline ingests the result -- so every count below is
    deliberately projected forward from it rather than read out of it.

    A tie leaves the belt where it is, same as the site's own rule."""
    f = _facts(next_game, lineage, rankings, today)
    changed = opp_score > holder_score
    when = (f'{f["game_day"]:%a %b} {f["game_day"].day} · {next_game["venue_name"]} · '
            + (f'{_ord(f["next_reign_no"])} reign in 157 years' if changed
               else f'{_ord(f["defense_no"])} defense this reign'))

    if changed:
        takes = _times((f["story"].get("vs_takes") or 0) + 1)
        return {
            "kind": "result", "emphasis": "right",
            "kicker": f'Belt game No. {f["game_no"]:,} · Final',
            "headline": ("Belt", "changes hands"), "when": when,
            "left": {"team": f["holder"], "role": "Former holder", "score": holder_score,
                     "stat": f'Reign ended at {f["days"]} days'},
            "right": {"team": f["opp"], "role": f'New holder · {_ord(f["opp_reign_no"])} reign',
                      "score": opp_score,
                      "stat": (f'{f["story"]["days"]} days held before tonight'
                               if f["story"]["reigns"] else "First time holding it")},
            "buckle": team_chip(f["opp"]),
            "caption": f'Moves to {f["opp"]}',
            "note": f'{f["opp"]} has now taken the belt off {f["holder"]} {takes}.',
        }

    tie = holder_score == opp_score
    return {
        "kind": "result",
        "kicker": f'Belt game No. {f["game_no"]:,} · Final',
        "headline": ("Belt", "retained" if tie else "defended"), "when": when,
        "left": {"team": f["holder"], "role": "Holder · retains", "score": holder_score,
                 "stat": f'{f["days"]} days and counting · {f["defense_no"]} defenses'},
        "right": {"team": f["opp"], "role": f'Challenger · {f["opp_last"]}',
                  "score": opp_score, "stat": f["opp_stat"]},
        "buckle": team_chip(f["holder"]),
        "caption": "A tie leaves it put" if tie else f'Stays with {f["holder"]}',
        "note": (f'{f["holder"]} has now held the belt {f["holder_all_days"]:,} days in all, '
                 f'across {f["holder_reign_no"]} reigns.'),
    }


# ---------------------------------------------------------- the public API

def card(kind, next_game, lineage, rankings=None, colors=None,
         holder_score=None, opp_score=None, today=None, out_dir=None):
    """Render the card for post type `kind` ('preview' | 'gameday' |
    'final') and return the PNG's path -- or None if anything at all went
    wrong.

    None is the whole point: a post that would have carried a picture goes
    out without one rather than not going out. A missing font, an
    unreadable belt_data file, a team the colour file has never heard of,
    a Pillow that isn't installed -- every one of those ends here, printed
    and swallowed, and the caller just skips the media upload."""
    try:
        builders = {"preview": preview_spec, "gameday": gameday_spec}
        if kind == "final":
            spec = final_spec(next_game, lineage, rankings, holder_score, opp_score, today)
        elif kind in builders:
            spec = builders[kind](next_game, lineage, rankings, today)
        else:
            print(f"No card for post type {kind!r} -- posting without one.")
            return None
        if colors is None:
            with open(os.path.join(DATA_DIR, "team_colors.json")) as fh:
                colors = json.load(fh)
        out = os.path.join(out_dir or OUT_DIR, f"{kind}.png")
        return generate_post_image(spec, out, colors)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Card for the {kind} post couldn't be rendered ({e}) -- "
              f"posting without a picture (not fatal).")
        return None


def main():
    """Render one of each against whatever is in belt_data right now --
    the way to eyeball a design change without waiting for a Saturday."""
    with open(os.path.join(DATA_DIR, "team_colors.json")) as fh:
        colors = json.load(fh)
    with open(os.path.join(DATA_DIR, "lineage.json")) as fh:
        lineage = json.load(fh)
    with open(os.path.join(DATA_DIR, "next_game.json")) as fh:
        next_game = json.load(fh)
    try:
        with open(os.path.join(DATA_DIR, "rankings.json")) as fh:
            rankings = json.load(fh)
    except FileNotFoundError:
        rankings = {}

    for kind, spec in (
        ("preview", preview_spec(next_game, lineage, rankings)),
        ("gameday", gameday_spec(next_game, lineage, rankings)),
        ("final-defended", final_spec(next_game, lineage, rankings, 31, 17)),
        ("final-changed", final_spec(next_game, lineage, rankings, 21, 24)),
    ):
        print("Wrote", generate_post_image(spec, os.path.join(OUT_DIR, f"{kind}.png"), colors))


if __name__ == "__main__":
    main()
