#!/usr/bin/env python3
"""
seo_enhance.py -- search-engine polish applied to the finished site/ folder.

Runs right after build_site.py (see update_all.py's STAGES). It only touches
each page's <head> (plus sitemap.xml and a few legacy redirect stubs), so it
stays out of build_site.py's way and automatically covers any page template
added later.

What it does, per page:
  1. <meta name="viewport"> -- without it, phones render every page as a
     zoomed-out 980px desktop page and none of styles.css's mobile
     @media rules ever fire. Google indexes the mobile version of a site, so
     this is the single biggest fix here.
  2. <link rel="canonical"> -- one absolute URL per page. GitHub Pages serves
     the same page at /x.html and /x, so without this Google has to guess.
  3. Keyword-bearing <title> + <meta name="description"> where the template
     has none or only a generic one (every game page, the homepage, team
     pages, and the static section pages).
  4. Open Graph / Twitter tags on pages that lack them (link previews).
  5. JSON-LD: WebSite + Organization (with the X/Instagram profiles) on the
     homepage, BreadcrumbList on game and team pages.
  6. noindex on 404.html / offline.html.
  (plus two small mobile CSS fixes appended to styles.css -- see MOBILE_CSS)

Site-wide:
  7. sitemap.xml <lastmod> -- the build stamps today's date on every URL,
     which teaches Google to ignore lastmod entirely. Now each page's date is
     the day its built bytes last changed (historical_data/page_hashes.json
     remembers a hash per page; the workflow commits it back), so settled
     history keeps an old date and only pages that really changed move.
  8. Redirect stubs for URLs from the pre-2018 site that Google still has
     indexed -- the WordPress leftovers (/Seasons.htm, /index.htm,
     /author/admin/, /2018/10/, /blog, /about, /team/<Name>) and the whole
     old static scheme (/<year>/<year> Game Summaries/<Away> at <Home>.htm,
     /Schools/<Name> Stats.htm), generated from the lineage so every old
     URL lands on the page that replaced it (see legacy_redirect_map).
  9. IndexNow (2026-09-19): the key file Bing/DuckDuckGo/Yahoo/Yandex verify
     ownership with (/<INDEXNOW_KEY>.txt) and indexnow-changed.json, the
     list of pages whose built bytes changed this build (step 7's hashes;
     every page on the first run, which is the one-time full submission
     Bing wants anyway). The deploy job POSTs that
     list to api.indexnow.org once the site is live, so Bing re-crawls the
     changed pages within minutes instead of whenever it next reads the
     sitemap. Google ignores IndexNow; it gets the sitemap as before.

Idempotent: running it twice changes nothing the second time.
"""

import html
import json
import os
import re
import sys
from datetime import date
from urllib.parse import quote

import build_site as bs

OUT_DIR = bs.OUT_DIR
SITE_URL = bs.SITE_URL
SITE_NAME = "The College Football Belt"
SHARE_IMG = f"{SITE_URL}/share.png"
SOCIAL_PROFILES = [
    "https://x.com/CollegeFBBelt",
    "https://www.instagram.com/collegefbbelt/",
]
MARKER = "<!-- seo_enhance -->"

LEGACY_REDIRECTS = {
    "index.htm": "/",
    "Seasons.htm": "/lineage.html",
    "author/admin/index.html": "/",
    "2018/10/index.html": "/",
}

# Conference belt pages that folded into another when the belts were
# rebuilt on 2026-09-19 (a renamed league keeps one belt under its current
# name -- classification.CONFERENCE_RENAMES) or stopped existing (Division
# II leagues the old build had mistaken for FCS). Each old URL gets a stub
# pointing at the page that replaced it.
CONFERENCE_REDIRECTS = {
    "pac-10": "pac-12", "pac-8": "pac-12", "aawu": "pac-12", "pacific": "pac-12",
    "big-6": "big-8", "big-7": "big-8",
    "pcaa": "big-west",
    "gateway-football": "mvfc", "gateway-collegiate-athletic": "mvfc",
    "colonial": "patriot",
    "yankee": "coastal-athletic", "atlantic-10": "coastal-athletic", "caa": "coastal-athletic",
    "big-east": "american-athletic",
    "mountain-state": "skyline",
    "western": "big-ten",
    "ovc-big-south": "big-south-ovc",
    "siac": "index", "awc": "index",
}
for _old, _new in CONFERENCE_REDIRECTS.items():
    LEGACY_REDIRECTS[f"conferences/{_old}.html"] = f"/conferences/{_new}.html"

# Section pages: (title, description). None = keep the template's own.
STATIC_PAGES = {
    "lineage.html": (
        "Full College Football Belt History: Every Reign Since 1869",
        "Every reign of the College Football Belt, the lineal college football "
        "championship, from Rutgers in 1869 to today: who won it, from whom, "
        "and for how long."),
    "all-games.html": (
        "Every College Football Belt Game Since 1869 — Scores & Results",
        "Searchable list of every game played for the College Football Belt since "
        "1869: dates, scores, title changes, defenses and ties."),
    "records.html": (
        "College Football Belt Records — Longest Reigns, Most Defenses",
        "All-time College Football Belt records: longest reigns, most days held, "
        "most defenses, longest droughts and belt held by coach."),
    "ruleset.html": (
        "How the College Football Belt Works — Official Ruleset",
        "The rules of the College Football Belt: how the lineal title passes on "
        "the field, how ties, vacancies and non-FBS games are handled, and where "
        "it all starts."),
    "map.html": (
        "College Football Belt Map — Every State the Title Has Lived In",
        "Interactive map of every state that has held the College Football Belt, "
        "with a playable timeline of the title's journey since 1869."),
    "compare.html": (
        "Compare Teams' College Football Belt History Head to Head",
        "Pick any two programs and compare their College Football Belt history: "
        "reigns, days held, defenses and every belt game between them."),
    "trivia.html": (
        "College Football Belt Trivia Quiz — Test Your Lineal Title IQ",
        "A 10-question quiz on the College Football Belt's 150+ year history: "
        "longest reigns, famous title changes and programs that never held it."),
    "on-this-day.html": (
        "On This Day in College Football Belt History",
        "College Football Belt games played on today's date throughout history — "
        "title changes, defenses and upsets since 1869."),
    "preview.html": (None,
        "Preview of the next College Football Belt game: recent form, "
        "head-to-head history, kickoff weather and what's at stake for the "
        "lineal college football title."),
    "stories.html": (
        "College Football Belt Stories — Data-Driven Title History",
        None),
    "losers-belt.html": (
        "The Losers Belt — College Football's Reverse Lineal Title",
        None),
    "embed.html": (
        "Embed the College Football Belt Badge on Your Site",
        "Free embeddable badge that always shows the current College Football "
        "Belt holder. Copy-paste HTML or Markdown."),
    "api.html": (
        "College Football Belt Data API — Free JSON",
        "Free JSON endpoints for the College Football Belt: current holder, every "
        "reign and every belt game since 1869."),
    "privacy.html": (None,
        "Privacy policy for collegefootballbelt.com: analytics, advertising "
        "cookies and how to contact us."),
}
NOINDEX_PAGES = {"404.html", "offline.html"}


# ------------------------------------------------------------------ helpers

def attr(s):
    """Escape for an HTML attribute value (input is plain text)."""
    return html.escape(s, quote=True)


def plain(s):
    """Plain text out of an HTML snippet/attribute value."""
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def has(head, pattern):
    return re.search(pattern, head, flags=re.I) is not None


def set_title(doc, new_title):
    return re.sub(r"<title>.*?</title>", lambda m: f"<title>{attr(new_title)}</title>",
                  doc, count=1, flags=re.S)


def get_title(doc):
    m = re.search(r"<title>(.*?)</title>", doc, flags=re.S)
    return plain(m.group(1)) if m else ""


def get_meta(doc, key, kind="name"):
    m = re.search(rf'<meta {kind}="{re.escape(key)}" content="([^"]*)"', doc)
    return html.unescape(m.group(1)) if m else None


DESC_MAX = 160


def trim_description(text, limit=DESC_MAX):
    """Shorten a meta description to `limit` characters: at the last
    sentence end if that keeps at least half the budget, else at a word
    boundary with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("; "), head.rfind(": "))
    if cut >= limit // 2:
        return head[:cut + 1].rstrip(";:").rstrip() + ("" if head[cut] == "." else ".")
    words = head[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:—-")
    return words + "…"


def set_meta(doc, key, value, kind="name"):
    """Replace an existing <meta> content or queue it for insertion."""
    pat = rf'(<meta {kind}="{re.escape(key)}" content=")[^"]*(")'
    if re.search(pat, doc):
        return re.sub(pat, lambda m: m.group(1) + attr(value) + m.group(2), doc, count=1), None
    return doc, f'<meta {kind}="{key}" content="{attr(value)}">'


def json_ld(data):
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
            + "</script>")


def breadcrumbs(*crumbs):
    return json_ld({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": url}
            for i, (name, url) in enumerate(crumbs, 1)
        ],
    })


def canonical_url(rel_path):
    rel_path = rel_path.replace(os.sep, "/")
    if rel_path == "index.html":
        return f"{SITE_URL}/"
    return f"{SITE_URL}/{rel_path}"


def fmt_date(iso):
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


# ------------------------------------------------------ per-page-type content

def game_meta(g, total):
    home, away = g["home"], g["away"]
    hs, as_ = (int(x) for x in g["score"].split("-"))
    year = g["date"][:4]
    when = fmt_date(g["date"])
    n = g["game_number"]
    outcome = g["outcome"]
    if hs == as_:
        result = f"{home} and {away} tied {hs}–{as_}"
    else:
        winner, loser = (home, away) if hs > as_ else (away, home)
        result = f"{winner} beat {loser} {max(hs, as_)}–{min(hs, as_)}"

    if outcome == "established":
        short = "the first belt game"
        stake = f"{g['new_holder']} became the first holder of the College Football Belt."
    elif outcome == "changed":
        short = f"{g['new_holder']} takes the belt"
        stake = f"{g['new_holder']} took the College Football Belt from {g['holder']}."
    elif outcome.startswith("retained (tie)"):
        short = f"{g['holder']} keeps the belt on a tie"
        stake = f"The tie let {g['holder']} keep the College Football Belt."
    else:
        short = f"{g['holder']} defends the belt"
        stake = f"{g['holder']} successfully defended the College Football Belt."

    score = f"{max(hs, as_)}–{min(hs, as_)}"
    title = f"{away} at {home} {year}: {short}, {score}"
    if len(title) > 62:
        title = f"{away} at {home} {year}: {score} belt game"
    desc = f"{when}: {result}. {stake} College Football Belt game #{n:,} of {total:,} since 1869."
    if len(desc) > 158:  # Google truncates around 155-160 characters
        desc = f"{when}: {result}. {stake} Belt game #{n:,} of {total:,}."
    return title, desc


def enhance_head(doc, rel_path, extra_head=(), title=None, desc=None):
    head_end = doc.find("<header")
    if head_end == -1:
        head_end = doc.find("<body")
    if head_end == -1:
        head_end = min(len(doc), 6000)
    head = doc[:head_end]
    inserts = []

    if not has(head, r'<meta name="viewport"'):
        inserts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')

    if title:
        doc = set_title(doc, title)
    cur_title = get_title(doc)

    if desc:
        desc = trim_description(desc)   # a page with no description of its own gets the trimmed one queued
        doc, ins = set_meta(doc, "description", desc)
        if ins:
            inserts.append(ins)
    cur_desc = get_meta(doc, "description") or desc
    # Keep every description inside what search results actually show: a
    # template that runs long is cut at a sentence end when that leaves a
    # real sentence, otherwise at a word boundary.
    if cur_desc and len(html.unescape(cur_desc)) > DESC_MAX:
        trimmed = trim_description(html.unescape(cur_desc))
        doc, _ = set_meta(doc, "description", trimmed)
        cur_desc = get_meta(doc, "description")
        if not desc:
            desc = trimmed

    name = os.path.basename(rel_path)
    if name in NOINDEX_PAGES:
        if not has(head, r'<meta name="robots"'):
            inserts.append('<meta name="robots" content="noindex">')
    else:
        url = canonical_url(rel_path)
        if not has(head, r'rel="canonical"'):
            inserts.append(f'<link rel="canonical" href="{url}">')
        # Social tags: keep the template's own if present, fill the gaps.
        og = {"og:title": cur_title, "og:url": url, "og:type": "website",
              "og:site_name": SITE_NAME, "og:image": SHARE_IMG}
        if cur_desc:
            og["og:description"] = cur_desc
        for k, v in og.items():
            if get_meta(doc, k, "property") is None:
                inserts.append(f'<meta property="{k}" content="{attr(v)}">')
            elif k == "og:title" and title:
                doc, _ = set_meta(doc, k, v, "property")
        tw = {"twitter:card": "summary_large_image", "twitter:site": "@CollegeFBBelt"}
        for k, v in tw.items():
            if get_meta(doc, k) is None:
                inserts.append(f'<meta name="{k}" content="{attr(v)}">')
        if title and get_meta(doc, "twitter:title") is not None:
            doc, _ = set_meta(doc, "twitter:title", cur_title)
        if desc:
            for k, kind in (("og:description", "property"), ("twitter:description", "name")):
                if get_meta(doc, k, kind) is not None:
                    doc, _ = set_meta(doc, k, desc, kind)

    inserts.extend(extra_head)
    if not inserts:
        return doc
    block = MARKER + "\n" + "\n".join(inserts) + "\n"
    m = re.search(r'<meta charset="[^"]*">\s*', doc, flags=re.I)
    pos = m.end() if m else doc.find("<title>")
    return doc[:pos] + block + doc[pos:]


# --------------------------------------------------------------- the passes

def process_pages(lineage):
    belt_games = lineage["belt_games"]
    bs.compute_sequence(belt_games)
    total = len(belt_games)
    games_by_id = {str(g["game_id"]): g for g in belt_games}
    holder = lineage.get("current_holder") or ""
    first_year = belt_games[0]["date"][:4] if belt_games else "1869"
    counts = {"pages": 0}

    for root, _dirs, files in os.walk(OUT_DIR):
        for fn in files:
            if not fn.endswith(".html"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, OUT_DIR).replace(os.sep, "/")
            if rel in LEGACY_REDIRECTS or rel in LEGACY_EXACT or "Game Summaries/" in rel or rel.startswith(("Schools/", "team/", "Logos/")):
                continue
            with open(path, encoding="utf-8") as f:
                doc = f.read()
            if MARKER in doc:
                continue  # already processed (build_site.py rewrites pages fresh each run)
            title = desc = None
            extra = []

            if rel == "index.html":
                if holder:
                    # the head query, verbatim, then the answer -- "who holds
                    # the college football belt" is what people type
                    title = f"Who Holds the College Football Belt? {holder} | CFB Belt"
                    if len(title) > 62:
                        title = f"Who Holds the College Football Belt? {holder}"
                else:
                    title = "College Football Belt — The Lineal Title Since 1869"
                base = get_meta(doc, "description") or ""
                if "college football belt" not in base.lower():
                    lead = (f"{holder} holds the College Football Belt, the lineal college "
                            f"football title passed on the field since {first_year}. "
                            if holder else
                            f"The lineal college football championship, passed on the field "
                            f"since {first_year}. ")
                    desc = (lead + base).strip()
                    if len(desc) > 158:  # keep it inside what Google shows
                        first_sentence = base.split(". ")[0].rstrip(".") + "."
                        desc = (lead + first_sentence).strip()
                    if len(desc) > 158:
                        desc = lead.strip()
                extra.append(json_ld({
                    "@context": "https://schema.org",
                    "@graph": [
                        {"@type": "WebSite", "@id": f"{SITE_URL}/#website",
                         "name": SITE_NAME, "alternateName": ["College Football Belt", "CFB Belt"],
                         "url": f"{SITE_URL}/",
                         "publisher": {"@id": f"{SITE_URL}/#org"}},
                        {"@type": "Organization", "@id": f"{SITE_URL}/#org",
                         "name": SITE_NAME, "url": f"{SITE_URL}/",
                         "logo": f"{SITE_URL}/icon-512.png",
                         "email": "hello@collegefootballbelt.com",
                         "sameAs": SOCIAL_PROFILES},
                    ],
                }))

            elif rel.startswith("games/"):
                g = games_by_id.get(fn[:-5])
                if g:
                    title, desc = game_meta(g, total)
                    extra.append(breadcrumbs(
                        ("Home", f"{SITE_URL}/"),
                        ("All Games", f"{SITE_URL}/all-games.html"),
                        (f"{g['away']} at {g['home']} ({g['date'][:4]})", canonical_url(rel))))

            elif rel.startswith("teams/"):
                team = re.sub(r"\s+—\s+The College Football Belt$", "", get_title(doc))
                stats = get_meta(doc, "description") or ""
                if team and "College Football Belt history" not in stats:
                    title = f"{team} College Football Belt History"
                    stats_clean = stats.rstrip(".")
                    desc = (f"Every {team} reign with the College Football Belt, the lineal "
                            f"college football championship since 1869"
                            + (f": {stats_clean}." if stats_clean else ".")
                            + " Dates, defenses and the games that won and lost it.")
                    if len(title) + len(" | CFB Belt") <= 60:
                        title += " | CFB Belt"
                extra.append(breadcrumbs(
                    ("Home", f"{SITE_URL}/"),
                    ("Full History", f"{SITE_URL}/lineage.html"),
                    (team or fn, canonical_url(rel))))

            elif rel.startswith(("reigns/", "rivalries/", "states/", "decades/", "universes/", "coaches/", "venues/")) and fn != "index.html":
                # the 2026-09 batch: one breadcrumb trail per family, the
                # page's own <title> (minus any site-name suffix) as the leaf
                family = rel.split("/", 1)[0]
                parent_name, parent_href = {
                    "reigns": ("Full History", "lineage.html"),
                    "rivalries": ("Rivalries", "rivalries/index.html"),
                    "states": ("States", "states/index.html"),
                    "decades": ("Decades", "decades/index.html"),
                    "universes": ("Alternate universes", "universes/index.html"),
                    "coaches": ("Coaches", "coaches/index.html"),
                    "venues": ("Venues", "venues/index.html"),
                }[family]
                leaf = re.sub(r"\s+—\s+.*$", "", html.unescape(get_title(doc) or "")) or fn[:-5]
                extra.append(breadcrumbs(
                    ("Home", f"{SITE_URL}/"),
                    (parent_name, f"{SITE_URL}/{parent_href}"),
                    (leaf, canonical_url(rel))))

            elif rel.startswith("story-") and "/" not in rel:
                # data-driven longreads: Article markup so they can surface
                # as articles, dated to this build since the numbers are live
                headline = re.sub(r"\s+—\s+The College Football Belt$", "", html.unescape(get_title(doc) or ""))
                extra.append(json_ld({
                    "@context": "https://schema.org",
                    "@type": "Article",
                    "headline": headline,
                    "description": html.unescape(get_meta(doc, "description") or ""),
                    "url": canonical_url(rel),
                    "dateModified": date.today().isoformat(),
                    "image": SHARE_IMG,
                    "author": {"@type": "Organization", "name": SITE_NAME, "url": f"{SITE_URL}/"},
                    "publisher": {"@id": f"{SITE_URL}/#org"},
                    "isPartOf": {"@type": "WebSite", "@id": f"{SITE_URL}/#website"},
                    "mainEntityOfPage": canonical_url(rel),
                }))
                extra.append(breadcrumbs(
                    ("Home", f"{SITE_URL}/"),
                    ("Stories", f"{SITE_URL}/stories.html"),
                    (headline, canonical_url(rel))))

            elif rel in STATIC_PAGES:
                title, desc = STATIC_PAGES[rel]

            new = enhance_head(doc, rel, extra, title, desc)
            if new != doc:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
                counts["pages"] += 1
    return counts["pages"]


PAGE_HASHES = os.path.join("historical_data", "page_hashes.json")   # committed back by the workflow


def _page_file(loc):
    """The built file behind a sitemap URL (None if it isn't a page of ours)."""
    if not loc.startswith(SITE_URL + "/"):
        return None
    rel = loc[len(SITE_URL) + 1:] or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    if not rel.endswith(".html"):
        return None
    return os.path.join(OUT_DIR, *rel.split("/"))


def track_page_changes(sitemap_urls):
    """Which pages really changed this build, and when each last did.
    historical_data/page_hashes.json (committed back by the workflow with the
    rest of historical_data/) keeps {url: [sha1 of the built file, date it
    last changed]}; a page whose bytes match the stored hash keeps its old
    date. Returns ({url: last_changed_date}, [urls changed this build]).
    Before 2026-09-19 this was a heuristic (section pages and the last three
    weeks' games always "fresh"); real hashes mean <lastmod> is true and
    IndexNow gets exactly the pages worth re-crawling."""
    import hashlib
    try:
        with open(PAGE_HASHES, encoding="utf-8") as f:
            store = json.load(f)
    except (OSError, ValueError):
        store = {}
    today = date.today().isoformat()
    dates, changed, seen = {}, [], set()
    for loc in sitemap_urls:
        path = _page_file(loc)
        if not path or not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            digest = hashlib.sha1(f.read()).hexdigest()[:16]
        old = store.get(loc)
        if old and old[0] == digest:
            dates[loc] = old[1]
        else:
            dates[loc] = today
            changed.append(loc)
            store[loc] = [digest, today]
        seen.add(loc)
    for loc in [k for k in store if k not in seen]:
        del store[loc]          # a page that left the sitemap
    os.makedirs(os.path.dirname(PAGE_HASHES), exist_ok=True)
    with open(PAGE_HASHES, "w", encoding="utf-8") as f:
        json.dump(store, f, separators=(",", ":"), sort_keys=True)
    return dates, changed


def process_sitemap(lineage):
    """<lastmod> = the date each page's content last changed (see
    track_page_changes), instead of today's date on everything. Returns
    (urls, changed): every sitemap URL, and the ones that changed this build."""
    path = os.path.join(OUT_DIR, "sitemap.xml")
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as f:
        xml = f.read()
    urls = [html.unescape(m.group(1)) for m in re.finditer(r"<url><loc>([^<]+)</loc>", xml)]
    dates, changed = track_page_changes(urls)

    def fix(m):
        loc = html.unescape(m.group(1))
        when = dates.get(loc)
        return (f"  <url><loc>{m.group(1)}</loc><lastmod>{when}</lastmod></url>" if when
                else f"  <url><loc>{m.group(1)}</loc></url>")

    xml = re.sub(r"  <url><loc>([^<]+)</loc>(?:<lastmod>[^<]+</lastmod>)?</url>", fix, xml)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    return urls, changed


# Once the viewport tag is on, phones lay pages out at their real width, which
# exposes two spots that were never built to wrap: the footer link row and
# the nowrap section headings. Appended to styles.css (idempotent).
MOBILE_CSS_MARKER = "/* seo_enhance: mobile */"
MOBILE_CSS = (MOBILE_CSS_MARKER
              + ".footRow nav{flex-wrap:wrap;row-gap:8px}"
              + "@media (max-width:480px){.sectionHead h2{white-space:normal}}")


def patch_styles():
    path = os.path.join(OUT_DIR, "styles.css")
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        css = f.read()
    if MOBILE_CSS_MARKER in css:
        return False
    with open(path, "a", encoding="utf-8") as f:
        f.write(MOBILE_CSS)
    return True


REDIRECT_TEMPLATE = """<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Moved — The College Football Belt</title>
<link rel="canonical" href="{url}">
<meta http-equiv="refresh" content="0; url={url}">
<script>location.replace({url_js});</script>
<p>The College Football Belt has moved to <a href="{url}">{url}</a>.</p>
"""


# The pre-2018 site was a hand-built static site with its own URL scheme
# (per-game pages under "<year>/<year> Game Summaries/", per-program pages
# under "Schools/", later a WordPress layer with "/team/<Name>", "/blog",
# "/about"). Search Console (2026-09-16) still lists 29 of those as 404s,
# and there are surely more links to them around the web. Rather than fix
# the 29 one by one, generate a stub for every URL the old scheme could
# have produced from the current lineage, plus the exact ones Google
# reported, and point each at the page that replaced it.

# Old site's spellings for programs CFBD names differently.
OLD_NAME_ALIASES = {
    "USC": ["Southern Cal", "Southern California"],
    "Ole Miss": ["Mississippi"],
    "Pittsburgh": ["Pitt"],
    "Miami": ["Miami (FL)", "Miami-FL"],
    "LSU": ["Louisiana State"],
    "BYU": ["Brigham Young"],
    "TCU": ["Texas Christian"],
    "SMU": ["Southern Methodist"],
    "UCF": ["Central Florida"],
    "UTEP": ["Texas-El Paso"],
    "Hawai'i": ["Hawaii"],
}

# Exactly what Search Console reported (paths relative to the site root),
# for the ones the generated patterns can't reproduce from the lineage --
# a game the old site counted that ours doesn't, a program with no page.
# Each maps to the closest thing we have.
LEGACY_EXACT = {
    "blog.html": "/stories.html",          # served at /blog (GitHub Pages resolves the extension; /about already is about.html)
    "Logos/index.html": "/data.html",
    "2018/10/05/hello-world/feed/index.html": "/",
    "team/Florida.html": "/teams/florida.html",
    "team/South Florida.html": "/teams/south-florida.html",
    "team/Baylor.html": "/teams/baylor.html",
}


def _old_names(team):
    return [team] + OLD_NAME_ALIASES.get(team, [])


def legacy_redirect_map(lineage):
    """{relative path under site/: target path} for every old-scheme URL."""
    belt_games = lineage["belt_games"]
    holders = {r["team"] for r in lineage["reigns"]}
    programs = {t for g in belt_games for t in (g["home"], g["away"])}
    out = dict(LEGACY_REDIRECTS)
    out.update(LEGACY_EXACT)

    def team_target(team):
        return f"/teams/{bs.team_slug(team)}.html" if team in programs else f"/all-games.html?q={quote(team)}"

    # per-program pages, both eras
    for team in sorted(programs):
        for name in _old_names(team):
            out[f"Schools/{name} Stats.htm"] = team_target(team)
            out[f"team/{name}.html"] = team_target(team)
    # the programs Search Console reported that have no belt game in our lineage
    for name, team in (("Troy", "Troy"), ("Southern Cal", "USC"), ("Maryland", "Maryland"),
                       ("Arizona State", "Arizona State"), ("Mississippi State", "Mississippi State")):
        out.setdefault(f"Schools/{name} Stats.htm", team_target(team))

    # per-game pages: "<year>/<year> Game Summaries/<Away> at <Home>.htm",
    # neutral-site games as "<A> vs <B>.htm" in either order
    by_key = {}
    for g in belt_games:
        year = g["date"][:4]
        target = f"/games/{g['game_id']}.html"
        homes, aways = _old_names(g["home"]), _old_names(g["away"])
        names = []
        if g.get("neutral"):
            for a in aways:
                for h in homes:
                    names += [f"{a} vs {h}", f"{h} vs {a}"]
        for a in aways:
            for h in homes:
                names.append(f"{a} at {h}")
        for n in names:
            out[f"{year}/{year} Game Summaries/{n}.htm"] = target
        by_key[(year, g["home"], g["away"])] = target
    # the ones Google reported by name; the season page when the old site
    # counted a game ours doesn't
    reported = [
        ("2008", "Missouri vs Illinois"), ("1974", "Purdue at Duke"), ("1974", "Army at Duke"),
        ("1983", "Florida at Auburn"), ("1986", "Alabama vs Ohio State"), ("1975", "Tennessee at UCLA"),
        ("1983", "Auburn at Georgia"), ("1994", "Utah at Colorado State"), ("2007", "Auburn at Florida"),
        ("1978", "Alabama at Missouri"), ("1975", "Indiana at Ohio State"), ("1991", "Texas Tech at Houston"),
    ]
    seasons = {g["date"][:4] for g in belt_games}
    for year, name in reported:
        out.setdefault(f"{year}/{year} Game Summaries/{name}.htm", f"/season-{year}.html" if year in seasons else "/lineage.html")
    return out


def write_legacy_redirects(lineage=None):
    redirects = legacy_redirect_map(lineage) if lineage else dict(LEGACY_REDIRECTS)
    for rel, target in redirects.items():
        url = SITE_URL + target
        path = os.path.join(OUT_DIR, *rel.split("/"))
        os.makedirs(os.path.dirname(path) or OUT_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(REDIRECT_TEMPLATE.format(url=attr(url), url_js=json.dumps(url)))
    return len(redirects)


# IndexNow (https://www.indexnow.org): a key any string of 8-128 hex/dash
# characters; the search engines prove we own the site by fetching
# /<key>.txt, so the key is public by design and lives here rather than in a
# secret. Change it and every engine simply re-verifies on the next ping.
INDEXNOW_KEY = "86bde37ac2fecf6ed78a67f2ade711a7"
INDEXNOW_CHANGED = "indexnow-changed.json"
INDEXNOW_MAX_URLS = 10000            # the protocol's per-request limit


def write_indexnow(changed):
    """The key file and this build's changed-URL list for the deploy job's
    IndexNow ping (the workflow POSTs indexnow-changed.json to
    api.indexnow.org after the site is live). `changed` comes from
    process_sitemap(): the pages whose built bytes differ from the last
    build's (all of them on the first run, which doubles as the one-time
    full submission)."""
    with open(os.path.join(OUT_DIR, f"{INDEXNOW_KEY}.txt"), "w", encoding="utf-8") as f:
        f.write(INDEXNOW_KEY)
    host = SITE_URL.split("//", 1)[-1].strip("/")
    payload = {"host": host, "key": INDEXNOW_KEY, "keyLocation": f"{SITE_URL}/{INDEXNOW_KEY}.txt",
               "urlList": changed[:INDEXNOW_MAX_URLS]}
    with open(os.path.join(OUT_DIR, INDEXNOW_CHANGED), "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    return len(payload["urlList"])


def main():
    if not os.path.isdir(OUT_DIR):
        sys.exit(f"No {OUT_DIR}/ folder -- run build_site.py first.")
    with open(os.path.join(bs.DATA_DIR, "lineage.json"), encoding="utf-8") as f:
        lineage = json.load(f)
    pages = process_pages(lineage)
    redirects = write_legacy_redirects(lineage)
    patch_styles()
    # the pages are final now: hash them, date the sitemap, list the changes
    urls, changed = process_sitemap(lineage)
    listed = write_indexnow(changed)
    print(f"SEO: updated {pages} pages, wrote {redirects} legacy redirect stubs; {len(changed)} of {len(urls)} "
          f"sitemap pages changed since the last build (<lastmod> dated from real changes), "
          f"{listed} listed for IndexNow.")


if __name__ == "__main__":
    main()
