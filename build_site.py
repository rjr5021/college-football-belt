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
import math
import os
import random
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import quote, urlencode

OUT_DIR = "site"
DATA_DIR = "belt_data"

# The custom domain, once DNS + GitHub Pages are both pointed at it (see
# README.md's "Custom domain" section). Two things depend on this:
#   1. site/CNAME -- GitHub Pages reads this file from the published output
#      to know which custom domain to serve. Since site/ is wiped and
#      rebuilt from scratch every run, this HAS to be written by the build
#      itself, or the custom domain setting would silently get dropped the
#      next time the pipeline runs (a real GitHub Pages gotcha, not a
#      guess -- it says so in their own docs).
#   2. SITE_URL below, for the absolute URLs that Open Graph / Twitter Card
#      meta tags need (see generate_homepage()).
CUSTOM_DOMAIN = "collegefootballbelt.com"
SITE_URL = f"https://{CUSTOM_DOMAIN}"

# Optional, free, privacy-friendly page-view analytics (https://www.goatcounter.com/).
# Empty by default -- every page just omits the tracking snippet, same
# no-op-when-unset pattern as ANTHROPIC_API_KEY for the AI preview/recaps.
# To turn it on: sign up free at goatcounter.com, pick a site code (that's
# the "goatcounter.com" subdomain you're assigned, e.g. "my-code" for
# my-code.goatcounter.com), then set it here and rerun the pipeline.
GOATCOUNTER_CODE = "collegefootballbelt"

# Optional Google Search Console ownership check. Empty by default -- every
# page just omits the meta tag, same no-op-when-unset pattern as
# GOATCOUNTER_CODE above. To turn it on: create a property for
# collegefootballbelt.com at search.google.com/search-console, choose the
# "HTML tag" verification method (not the DNS or file-upload ones), copy
# just the content="..." value out of the <meta> tag it gives you, set it
# here, rerun the pipeline, then click Verify on Search Console's end once
# the new build is deployed -- that last click has to happen there, not
# here. Submit sitemap.xml from the same dashboard afterward.
GOOGLE_SITE_VERIFICATION = ""

# Optional Google AdSense monetization. Empty by default -- every page omits
# the ad-loader script and ads.txt is skipped entirely, same no-op-when-unset
# pattern as GOATCOUNTER_CODE/GOOGLE_SITE_VERIFICATION above. To turn it on:
# 1. Apply for a free AdSense account at adsense.google.com using the site's
#    Google account -- Google reviews the site (checks for original content,
#    a privacy policy, and enough traffic/history) before approving it, which
#    can take anywhere from a day to a few weeks. This step has to happen in
#    Google's own dashboard; there's no build-time equivalent.
# 2. Once approved, AdSense gives you a publisher ID shaped like
#    "pub-XXXXXXXXXXXXXXXX". Set it here (with the "pub-" prefix) and rerun
#    the pipeline -- that both adds the ad-loader script to every page's
#    <head> AND writes site/ads.txt (required by Google to confirm this site
#    is authorized to show ads for that publisher; without it, ads silently
#    stay disabled even with a valid ID and script).
# 3. Ad placement itself (which pages, how many, where on the page) is
#    configured from the AdSense dashboard under Auto ads -- not here --
#    once the script above is live on the site.
ADSENSE_PUBLISHER_ID = "pub-4807241949046212"

PAPER_LIGHT = "#e7e2d5"
PAPER_DARK = "#161009"


def head_extras(rel=""):
    """Viewport + web-font links, favicon links, the PWA manifest/service-
    worker wiring, the shared page script (theme toggle, menu drawer, search),
    and (when GOATCOUNTER_CODE / ADSENSE_PUBLISHER_ID are set) the analytics
    and ad-loader snippets -- shared by every page template. `rel` is the
    relative path prefix back to the site root -- "" for root-level pages,
    "../" for pages one directory down (games/, teams/, conferences/), "/"
    for pages that can be served from any path (404.html, offline.html)."""
    bits = [
        # Without this, phones render the ~980px desktop layout scaled down
        # and none of the @media rules in STYLES_CSS ever fire (found in the
        # 2026-09-15 audit; seo_enhance.py also back-fills it, but it belongs
        # in the template itself).
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        # Web fonts: a <link> in <head> (with preconnect) instead of the old
        # @import at the top of styles.css -- @import chained a third
        # render-blocking round trip (HTML -> styles.css -> fonts.googleapis
        # -> font files); this way the font CSS downloads in parallel with
        # styles.css.
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        f'<link rel="stylesheet" href="{FONTS_URL}">',
        f'<link rel="icon" type="image/png" href="{rel}favicon.png">',
        f'<link rel="apple-touch-icon" href="{rel}apple-touch-icon.png">',
        f'<link rel="manifest" href="{rel}manifest.json">',
        '<meta name="theme-color" content="#8a6a34">',
        f'<link rel="alternate" type="application/rss+xml" '
        f'title="The College Football Belt — Belt Changes" href="{rel}feed.xml">',
    ]
    if GOOGLE_SITE_VERIFICATION:
        bits.append(f'<meta name="google-site-verification" content="{esc(GOOGLE_SITE_VERIFICATION)}">')
    if GOATCOUNTER_CODE:
        bits.append(
            f'<script data-goatcounter="https://{GOATCOUNTER_CODE}.goatcounter.com/count" '
            f'async src="//gc.zgo.at/count.js"></script>'
        )
    if ADSENSE_PUBLISHER_ID:
        bits.append(
            f'<meta name="google-adsense-account" content="ca-{esc(ADSENSE_PUBLISHER_ID)}">'
        )
        bits.append(
            f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js'
            f'?client=ca-{esc(ADSENSE_PUBLISHER_ID)}" crossorigin="anonymous"></script>'
        )
    # The site's one shared script: the theme toggle (reads/writes
    # localStorage("cfbBelt:theme") so a visitor's explicit light/dark
    # choice overrides the OS-level prefers-color-scheme default; data-theme
    # is set synchronously here, before the header renders, so there's no
    # flash of the wrong theme), the phone menu drawer, the header search
    # box (see generate_search_index()), and the service-worker
    # registration. The header itself comes from site_header() below.
    bits.append('''<script>
(function(){
  var KEY = 'cfbBelt:theme';
  var stored = null;
  try { stored = localStorage.getItem(KEY); } catch (e) {}
  var root = document.documentElement;
  function apply(t){
    if (t === 'light' || t === 'dark') root.setAttribute('data-theme', t);
    else root.removeAttribute('data-theme');
    root.setAttribute('data-theme-mode', t || 'auto');
  }
  apply(stored);
  function labelFor(t){ return t === 'light' ? 'Light' : (t === 'dark' ? 'Dark' : 'Auto (follows your device)'); }
  function sync(){
    var btns = document.querySelectorAll('.themeToggle');
    for (var j = 0; j < btns.length; j++) btns[j].setAttribute('aria-label', 'Theme: ' + labelFor(stored || 'auto') + ' \\u2014 tap to change');
  }
  document.addEventListener('DOMContentLoaded', sync);
  document.addEventListener('click', function(e){
    var btn = e.target.closest && e.target.closest('.themeToggle');
    if (btn) {
      var order = ['auto', 'light', 'dark'];
      var next = order[(order.indexOf(stored || 'auto') + 1) % order.length];
      stored = next === 'auto' ? null : next;
      try { if (!stored) localStorage.removeItem(KEY); else localStorage.setItem(KEY, stored); } catch (e) {}
      apply(stored); sync();
      return;
    }
    var tog = e.target.closest && e.target.closest('.navToggle');
    if (tog) {
      var open = root.classList.toggle('navOpen');
      tog.setAttribute('aria-expanded', open ? 'true' : 'false');
      tog.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
      /* the "More" groups are a dropdown on desktop but plain sections in
         the phone drawer, so they have to be open whenever the drawer is */
      var mm = document.querySelectorAll('.moreMenu');
      for (var k = 0; k < mm.length; k++) mm[k].open = open;
      return;
    }
    if (root.classList.contains('navOpen') && !e.target.closest('.siteHead')) {
      root.classList.remove('navOpen');
      var t2 = document.querySelector('.navToggle');
      if (t2) { t2.setAttribute('aria-expanded', 'false'); t2.setAttribute('aria-label', 'Open menu'); }
    }
    if (!root.classList.contains('navOpen')) {
      var openMenus = document.querySelectorAll('.moreMenu[open]');
      for (var m = 0; m < openMenus.length; m++) if (!openMenus[m].contains(e.target)) openMenus[m].open = false;
    }
  });
  document.addEventListener('keydown', function(e){
    if (e.key !== 'Escape') return;
    if (root.classList.contains('navOpen')) {
      root.classList.remove('navOpen');
      var t3 = document.querySelector('.navToggle');
      if (t3) { t3.setAttribute('aria-expanded', 'false'); t3.focus(); }
      return;
    }
    var om = document.querySelectorAll('.moreMenu[open]');
    for (var q = 0; q < om.length; q++) { om[q].open = false; var s = om[q].querySelector('summary'); if (s) s.focus(); }
  });
  /* Header search: one lazy fetch of search-index.json (teams, seasons,
     conferences, section pages), filtered client-side; Enter with a single
     obvious match jumps straight there, otherwise the form falls through to
     all-games.html?q=... which pre-fills that page's own filter. */
  var index = null, loading = null;
  function loadIndex(form){
    if (index || loading) return loading;
    loading = fetch(form.getAttribute('data-index')).then(function(r){ return r.json(); })
      .then(function(j){ index = j; return j; }).catch(function(){ index = []; return index; });
    return loading;
  }
  function rank(q, items){
    q = q.toLowerCase();
    var out = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i], n = it.n.toLowerCase(), s = -1;
      if (n === q) s = 0; else if (n.indexOf(q) === 0) s = 1; else if (n.indexOf(' ' + q) !== -1) s = 2; else if (n.indexOf(q) !== -1) s = 3;
      else if (it.k && it.k.toLowerCase().indexOf(q) !== -1) s = 4;
      if (s >= 0) out.push([s, it]);
    }
    out.sort(function(a, b){ return a[0] - b[0] || a[1].n.localeCompare(b[1].n); });
    return out.map(function(x){ return x[1]; });
  }
  function initSearch(form){
    var input = form.querySelector('input'), box = form.querySelector('.searchResults');
    var rel = form.getAttribute('data-rel') || '';
    var hits = [], active = -1;
    function render(){
      if (!hits.length) { box.hidden = true; box.innerHTML = ''; input.setAttribute('aria-expanded', 'false'); return; }
      box.innerHTML = hits.map(function(h, i){
        return '<a role="option" id="ss-opt-' + i + '" class="searchHit' + (i === active ? ' active' : '') + '" href="' + rel + h.u + '"><span class="searchHitName">' + h.n.replace(/</g, '&lt;') + '</span><span class="searchHitKind">' + (h.t || '') + '</span></a>';
      }).join('');
      box.hidden = false; input.setAttribute('aria-expanded', 'true');
    }
    function update(){
      var q = input.value.trim();
      if (q.length < 2 || !index) { hits = []; active = -1; render(); return; }
      hits = rank(q, index).slice(0, 7); active = -1; render();
    }
    input.addEventListener('focus', function(){ loadIndex(form).then(update); });
    input.addEventListener('input', function(){ if (index) update(); else loadIndex(form).then(update); });
    input.addEventListener('keydown', function(e){
      if (e.key === 'ArrowDown' && hits.length) { e.preventDefault(); active = (active + 1) % hits.length; render(); }
      else if (e.key === 'ArrowUp' && hits.length) { e.preventDefault(); active = (active - 1 + hits.length) % hits.length; render(); }
      else if (e.key === 'Escape') { hits = []; render(); }
      else if (e.key === 'Enter') {
        var q = input.value.trim();
        var pick = active >= 0 ? hits[active] : (hits.length && hits[0].n.toLowerCase() === q.toLowerCase() ? hits[0] : null);
        if (!pick && /^(18|19|20)\\d\\d$/.test(q) && index) {
          for (var i = 0; i < index.length; i++) if (index[i].t === 'Season' && index[i].n.indexOf(q) === 0) { pick = index[i]; break; }
        }
        if (!pick && hits.length === 1) pick = hits[0];
        if (pick) { e.preventDefault(); location.href = rel + pick.u; }
      }
    });
    document.addEventListener('click', function(e){ if (!form.contains(e.target)) { hits = []; render(); } });
  }
  document.addEventListener('DOMContentLoaded', function(){
    var forms = document.querySelectorAll('form.siteSearch');
    for (var i = 0; i < forms.length; i++) initSearch(forms[i]);
    var qs = null;
    try { qs = new URLSearchParams(location.search).get('q'); } catch (e) {}
    var filter = document.getElementById('teamSearch');
    if (qs && filter) { filter.value = qs; filter.dispatchEvent(new Event('input')); }
  });
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function(){
      navigator.serviceWorker.register('__SW_PATH__').catch(function(){});
    });
  }
})();
</script>'''.replace('__SW_PATH__', f'{rel}sw.js'))
    return "\n".join(bits)


FONTS_URL = ("https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@500;700;800;900"
             "&family=Spectral:ital,wght@0,400;0,500;0,600;1,400;1,500"
             "&family=IBM+Plex+Mono:wght@400;500;600&display=swap")

# The belt mark used in the header/footer -- an octagon medallion between two
# side plates on a strap, the same silhouette generate_share_image.py draws
# for the favicon/social art, in inline SVG so it recolors with the theme.
BELT_MARK_SVG = ('<svg class="beltMark" width="34" height="22" viewBox="0 0 34 22" aria-hidden="true" focusable="false">'
                 '<rect x="0" y="8" width="34" height="6" rx="1" fill="currentColor"/>'
                 '<rect x="3" y="6" width="6" height="10" rx="1" fill="var(--brass-bright)"/>'
                 '<rect x="25" y="6" width="6" height="10" rx="1" fill="var(--brass-bright)"/>'
                 '<path d="M17 0 L24 4 L24 18 L17 22 L10 18 L10 4 Z" fill="var(--brass-bright)" stroke="currentColor" stroke-width="1.5"/>'
                 '<circle cx="17" cy="11" r="3.5" fill="currentColor"/></svg>')

# Primary navigation: the five destinations most visits are for, plus a
# "More" menu that keeps the long tail one click away (2026-09-16 redesign --
# the old header had grown to 15 flat links). `active` keys match the first
# element of each tuple.
NAV_PRIMARY = [
    ("home", "index.html", "The Belt"),
    ("history", "lineage.html", "History"),
    ("records", "records.html", "Records"),
    ("map", "map.html", "Map"),
    ("stories", "stories.html", "Stories"),
]
NAV_MORE = [
    ("Explore", [
        ("all-games", "all-games.html", "All games"),
        ("seasons", "seasons.html", "Seasons"),
        ("timeline", "timeline.html", "Timeline"),
        ("rivalries", "rivalries/index.html", "Rivalries"),
        ("conferences", "conferences/index.html", "Conference belts"),
        ("states", "states/index.html", "States"),
        ("decades", "decades/index.html", "Decades"),
        ("losers", "losers-belt.html", "Losers Belt"),
        ("on-this-day", "on-this-day.html", "On this day"),
    ]),
    ("Play", [
        ("my-team", "my-team.html", "My Team"),
        ("daily", "daily.html", "The Daily Belt"),
        ("dod", "defend-or-dethrone.html", "Defend or Dethrone"),
        ("trivia", "trivia.html", "Trivia"),
        ("compare", "compare.html", "Compare teams"),
    ]),
    ("Numbers", [
        ("outlook", "outlook.html", "Season outlook"),
        ("preview", "preview.html", "Next belt game"),
        ("leaders", "leaders.html", "Belt-game leaders"),
        ("heartbreak", "heartbreak.html", "Heartbreak list"),
        ("lean", "lean.html", "The lean&rsquo;s ledger"),
    ]),
    ("About", [
        ("about", "about.html", "About the belt"),
        ("ruleset", "ruleset.html", "Ruleset"),
        ("embed", "embed.html", "Embed badge"),
        ("api", "api.html", "API"),
        ("contact", "mailto:hello@collegefootballbelt.com", "Contact"),
    ]),
]

THEME_TOGGLE_HTML = (
    '<button type="button" class="themeToggle" aria-label="Theme: Auto — tap to change" title="Light / dark / auto">'
    '<svg class="ti ti-auto" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 1.5 A6.5 6.5 0 0 1 8 14.5 Z" fill="currentColor"/></svg>'
    '<svg class="ti ti-light" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="3.2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3"/></svg>'
    '<svg class="ti ti-dark" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M13.5 10.2A6 6 0 0 1 5.8 2.5a6 6 0 1 0 7.7 7.7z" fill="currentColor"/></svg>'
    '</button>')


def _nav_a(rel, key, href, label, active):
    ext = href.startswith(("http", "mailto:"))
    url = href if ext else f"{rel}{href}"
    cur = ' aria-current="page"' if key == active else ""
    return f'<a href="{url}"{cur}>{label}</a>'


def site_header(rel="", active=None, crumb=""):
    """The shared page header: brand, five primary links, a "More" menu,
    site search, the theme toggle, and (on phones) a menu button that opens
    all of it as a drawer. `rel` is the path prefix back to the site root
    ("" or "../"); `active` marks the current section; `crumb` is optional
    HTML shown under the header row (game pages use it for "Reign #N ·
    Game X of Y")."""
    primary = "".join(_nav_a(rel, k, h, l, active) for k, h, l in NAV_PRIMARY)
    more_active = any(k == active for _, items in NAV_MORE for k, _, _ in items)
    groups = ""
    for title, items in NAV_MORE:
        links = "".join(_nav_a(rel, k, h, l, active) for k, h, l in items)
        groups += f'<div class="moreGroup"><span class="moreKicker">{title}</span>{links}</div>'
    more = (f'<details class="moreMenu"{" data-active" if more_active else ""}>'
            f'<summary>More <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 3.5 L5 6.5 L8 3.5"/></svg></summary>'
            f'<div class="moreGrid">{groups}</div></details>')
    search = (f'<form class="siteSearch" role="search" action="{rel}all-games.html" method="get" '
              f'data-index="{rel}search-index.json" data-rel="{rel}" autocomplete="off">'
              f'<label class="srOnly" for="siteSearchInput">Search a team or year</label>'
              f'<label class="searchIcon" for="siteSearchInput"><svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="7" cy="7" r="5"/><path d="M11 11 L15 15"/></svg></label>'
              f'<input id="siteSearchInput" name="q" type="search" placeholder="Search a team or year" '
              f'role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="siteSearchResults" enterkeyhint="search">'
              f'<div class="searchResults" id="siteSearchResults" role="listbox" hidden></div></form>')
    crumb_html = f'<div class="wrap crumbRow">{crumb}</div>' if crumb else ""
    return f'''<header class="siteHead">
  <div class="wrap siteHeadRow">
    <a class="brand" href="{rel}index.html">{BELT_MARK_SVG}<span class="brandName"><span class="brandLong">The College Football Belt</span><span class="brandShort">The CFB Belt</span></span></a>
    <div class="headTools">{THEME_TOGGLE_HTML}<button type="button" class="navToggle" aria-expanded="false" aria-controls="primaryNav" aria-label="Open menu"><svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path class="nt-open" d="M3 5.5h14M3 10h14M3 14.5h14"/><path class="nt-close" d="M5 5l10 10M15 5L5 15"/></svg></button></div>
    <nav class="primaryNav" id="primaryNav" aria-label="Primary">
      <div class="navLinks">{primary}{more}</div>
      <div class="navTools">{search}<span class="navThemeSlot">{THEME_TOGGLE_HTML}</span></div>
    </nav>
  </div>{crumb_html}
</header>'''


FOOTER_COLUMNS = [
    ("Lineage", [("lineage.html", "Full history"), ("all-games.html", "All games"), ("seasons.html", "Seasons"),
                 ("timeline.html", "Timeline"), ("rivalries/index.html", "Rivalries"), ("conferences/index.html", "Conference belts"),
                 ("states/index.html", "States"), ("decades/index.html", "Decades")]),
    ("Tools", [("outlook.html", "Season outlook"), ("my-team.html", "My Team"), ("compare.html", "Compare teams"),
               ("preview.html", "Next belt game"), ("daily.html", "The Daily Belt"), ("embed.html", "Embed badge"),
               ("api.html", "API"), ("feed.xml", "RSS feed")]),
    ("About", [("about.html", "About"), ("ruleset.html", "Ruleset"), ("stories.html", "Stories"), ("records.html", "Records"),
               ("losers-belt.html", "Losers Belt"), ("mailto:hello@collegefootballbelt.com", "Contact"), ("privacy.html", "Privacy"),
               ("https://x.com/CollegeFBBelt", "X · @CollegeFBBelt"), ("https://www.instagram.com/collegefbbelt/", "Instagram")]),
]


def site_footer(rel="", note=""):
    """The shared page footer: brand + a one-line data note (per page),
    three link columns, and the base line."""
    cols = ""
    for title, links in FOOTER_COLUMNS:
        anchors = ""
        for href, label in links:
            ext = href.startswith(("http", "mailto:"))
            url = href if ext else f"{rel}{href}"
            target = ' target="_blank" rel="noopener"' if href.startswith("http") else ""
            anchors += f'<a href="{url}"{target}>{label}</a>'
        cols += f'<nav class="footCol" aria-label="{title} links"><span class="footKicker">{title}</span>{anchors}</nav>'
    note_html = f'<p class="footNote">{note}</p>' if note else ""
    return f'''<footer class="siteFoot">
  <div class="wrap footGrid">
    <div class="footBrand"><a class="brand" href="{rel}index.html">{BELT_MARK_SVG}<span class="brandName">The College Football Belt</span></a>{note_html}</div>
    {cols}
  </div>
  <div class="wrap footBase"><span>Every belt game sourced from the College Football Data API. Colors on the site are the current holder&rsquo;s &mdash; it recolors itself with every change of hands.</span><span>&copy; {date.today().year} collegefootballbelt.com</span></div>
</footer>'''


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


def load_optional_json(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_data():
    with open(os.path.join(DATA_DIR, "lineage.json")) as f:
        lineage = json.load(f)
    with open(os.path.join(DATA_DIR, "game_details.json")) as f:
        details = json.load(f)
    with open(os.path.join(DATA_DIR, "team_colors.json")) as f:
        colors = json.load(f)
    next_game = load_optional_json("next_game.json")
    upcoming_games = load_optional_json("upcoming_games.json") or []
    team_paths = load_optional_json("team_paths.json")
    belt_risk = load_optional_json("belt_risk.json")
    gameday = load_optional_json("gameday.json")
    coaches = load_optional_json("coaches.json")
    matchup = load_optional_json("matchup_preview.json")
    ai_preview = load_optional_json("ai_preview.json")
    weather = load_optional_json("weather.json")
    recaps = load_optional_json("recaps.json") or {}
    historical_notes = load_optional_json("historical_notes.json") or {}
    game_plays = load_optional_json("game_plays.json") or {}
    # Three independent, independently-optional Losers Belt lineages (see
    # build_losers_lineage.py's SCOPES) -- each entry is None until that
    # scope's one-time historical bootstrap has been run (see that
    # module's own docstring); main() below skips rendering that scope's
    # losers-belt*.html page entirely when its entry is None, same
    # no-op-when-unset pattern as every other optional feature on this
    # site. "combined" keeps the original unsuffixed filename so the
    # already-live page/data needs no migration.
    losers_lineages = {
        "combined": load_optional_json("losers_lineage.json"),
        "fbs": load_optional_json("losers_lineage_fbs.json"),
        "fcs": load_optional_json("losers_lineage_fcs.json"),
    }
    # Same idea for the real championship belt's own new FBS-only/FCS-only
    # scopes (2026-09-15, Bob: "do the same on the Full History and All
    # Games tabs") -- "combined" here is just `lineage` above, already
    # loaded (unsuffixed lineage.json, always present); "fbs"/"fcs" are
    # None until build_lineage.py's own one-time bootstrap for those two
    # scopes has run.
    championship_lineages = {
        "combined": lineage,
        "fbs": load_optional_json("lineage_fbs.json"),
        "fcs": load_optional_json("lineage_fcs.json"),
    }
    # Conference belts (build_conference_lineage.py) -- unlike the fixed
    # three scopes above, this is an open-ended, dynamically-discovered
    # set: whichever belt_data/conferences/<slug>_lineage.json files
    # actually exist, since which conferences have been bootstrapped (and
    # which even HAVE enough qualifying history to build at all) isn't
    # known ahead of time.
    conference_lineages = {}
    conferences_dir = os.path.join(DATA_DIR, "conferences")
    if os.path.isdir(conferences_dir):
        for fname in sorted(os.listdir(conferences_dir)):
            if fname.endswith("_lineage.json"):
                slug = fname[:-len("_lineage.json")]
                with open(os.path.join(conferences_dir, fname)) as f:
                    conference_lineages[slug] = json.load(f)
    return (lineage, details, colors, next_game, upcoming_games, matchup,
            ai_preview, weather, recaps, historical_notes, game_plays,
            losers_lineages, championship_lineages, conference_lineages,
            team_paths, belt_risk, gameday, coaches)


def team_color(colors, name):
    entry = colors.get(name) or {}
    primary = entry.get("color") or "#5b5b5b"
    alt = entry.get("alternate_color") or "#ffffff"
    return primary, alt


def team_logo(colors, name):
    """CFBD's own logo URL for this team, or None -- fetch_team_colors.py
    already captures it (same /teams call as the color data, zero extra
    cost), it just wasn't rendered anywhere yet. None for the handful of
    historic programs CFBD doesn't track (Carlisle, Olympic Club, etc.) --
    callers render nothing rather than a broken image."""
    entry = colors.get(name) or {}
    return entry.get("logo") or None


def logo_img(colors, name, css_class="teamLogo", size=40):
    """A ready-to-embed <img>, or "" when this team has no logo on file --
    always check truthiness before using this in a layout that assumes an
    image is present."""
    url = team_logo(colors, name)
    if not url:
        return ""
    return (f'<img class="{css_class}" src="{esc(url)}" alt="" width="{size}" height="{size}" '
            f'loading="lazy" onerror="this.remove()">')


def logo_chip(colors, name, size=30):
    """A team's logo on a small white circular backdrop -- for placing it on
    top of a panel filled with that SAME team's own primary color (the
    game-page scoreboard, the homepage hero card). Without the backdrop a
    team whose logo is mostly its own primary color -- Penn State's navy
    crest on a navy panel -- nearly disappears. The chip always renders: the
    team's initials sit underneath the image, so a program with no logo on
    file (Carlisle, Olympic Club) or a logo that fails to load still gets a
    legible mark instead of an empty circle."""
    return team_dot(colors, name, size, "logoChip")


def team_dot(colors, name, size=40, css_class="tlDot"):
    """Initials-under-logo team mark (see logo_chip). `size` is the logo's
    box; the circle itself is sized by the CSS class."""
    primary, _ = team_color(colors, name)
    img = logo_img(colors, name, "teamLogo", size)
    init = esc(team_chip(name))
    style = f' style="width:{size + 6}px;height:{size + 6}px"' if css_class == "logoChip" else ""
    return (f'<span class="{css_class}"{style}><span class="dotInit" aria-hidden="true" '
            f'style="color:{readable_on_white(primary)}">{init}</span>{img}</span>')


def readable_on_white(hexcolor, minimum=4.5):
    """The team color itself when it reads on the white chip, otherwise the
    same hue darkened just enough to pass WCAG AA (a pale gold or orange
    would otherwise be near-invisible as fallback initials)."""
    c = hexcolor
    for weight in (1.0, 0.85, 0.7, 0.55, 0.4, 0.25):
        c = blend(hexcolor, "#000000", weight)
        if contrast_ratio(c, "#ffffff") >= minimum:
            return c
    return "#211a12"


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def fmt_date(iso):
    # avoid a libc/locale dependency -- do it by hand
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{MONTH_NAMES[m-1]} {d}, {y}"


def fmt_month_day(d):
    return f"{MONTH_NAMES[d.month-1]} {d.day}"


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


def possessive(name):
    """Escaped possessive form of a program name: Yale&rsquo;s, Rutgers&rsquo;."""
    return esc(name) + ("&rsquo;" if name.endswith(("s", "S")) else "&rsquo;s")


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


def team_slug(name):
    """URL-safe filename stem for a team's own page, e.g. 'Notre Dame' ->
    'notre-dame', "Texas A&M" -> 'texas-a-m'. Deterministic and shared by
    every team-page link across the site, so it only has to be right once."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "team"


def player_slug(player_id, name):
    """URL-safe filename stem for a player's own page -- the name AND
    CFBD's own athlete id, since two different players can (and do) share
    a name; the id keeps their pages from colliding. Deterministic and
    shared by every player-page link across the site, so it only has to be
    right once."""
    base = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return f"{base}-{player_id}" if player_id else (base or "player")


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
        if g["outcome"] in ("changed", "established"):
            index[(g["date"], g["new_holder"])] = g
    return index


def build_loss_game_index(belt_games):
    """(end_date, team) -> the belt_game where `team` lost the belt, the
    mirror image of build_change_game_index (which indexes by the WINNING
    side of a title change) -- used on a team's own page to show how each
    of its reigns ended."""
    index = {}
    for g in belt_games:
        if g["outcome"] == "changed":
            index[(g["date"], g["holder"])] = g
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


def render_recap(g):
    recap = g.get("recap")
    if recap and (recap.get("recap") or recap.get("key_moments")):
        body = recap.get("recap") or ""
        moments = recap.get("key_moments") or []
        moments_html = "".join(f"<li>{esc(m)}</li>" for m in moments)
        moments_block = f'<ul class="keyMatchups">{moments_html}</ul>' if moments_html else ""

        return f'''
  <section>
    <div class="sectionHead withTag">
      <span class="tag">AI-Written</span>
      <span class="rule"></span>
      <h2>Recap</h2>
    </div>
    <div class="aiPreviewBody"><p>{esc(body)}</p></div>
    {moments_block}
    <p class="noteBox">Written by Claude from the box score and play-by-play on this page &mdash; a plain-English recap of the numbers below, not a substitute for them.</p>
  </section>'''

    note = g.get("historical_note")
    note_text = (note or {}).get("note")
    if note_text:
        return f'''
  <section>
    <div class="sectionHead withTag">
      <span class="tag">Historical Note</span>
      <span class="rule"></span>
      <h2>About this game</h2>
    </div>
    <div class="aiPreviewBody"><p>{esc(note_text)}</p></div>
    <p class="noteBox">Written by Claude, strictly from this site&rsquo;s own record of the game &mdash; the score and what it meant for the belt. CFBD doesn&rsquo;t have a box score for games this old, so nothing here is invented beyond what&rsquo;s verifiably on file.</p>
  </section>'''

    return ""


def render_key_plays(g):
    plays = g.get("key_plays")
    if not plays:
        return ""
    rows = ""
    for p in plays:
        when = f"Q{p.get('period')} {p.get('clock')}" if p.get("period") else (p.get("clock") or "—")
        yards = p.get("yards_gained")
        yard_txt = f"{yards:+,} yd" if isinstance(yards, int) else "—"
        text = p.get("play_text") or p.get("play_type") or "—"
        cls = " scoring" if p.get("scoring") else ""
        rows += (f'<tr class="keyPlayRow{cls}"><td class="tabular">{esc(when)}</td>'
                 f'<td>{esc(p.get("offense") or "—")}</td>'
                 f'<td class="tabular">{esc(yard_txt)}</td>'
                 f'<td>{esc(text)}</td></tr>')

    return f'''
  <section>
    <div class="sectionHead withTag">
      <span class="tag">Play By Play</span>
      <span class="rule"></span>
      <h2>Key plays</h2>
      <span class="sourceTag">CFBD play-by-play</span>
    </div>
    <div style="overflow-x:auto">
      <table class="keyPlaysTable">
        <thead><tr><th>When</th><th>Team</th><th>Yards</th><th>Play</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <p class="noteBox">Scoring plays, turnovers, and gains of 20+ yards, pulled from CFBD’s play-by-play. This section only appears for belt games with play-by-play on file (2003 onward).</p>
  </section>'''


# ------------------------------------------------------------------- styles

STYLES_CSS = """
:root{
  --paper:#e7e2d5; --paper-2:#dcd5c3; --paper-3:#d3cbb6;
  --ink:#211a12; --ink-soft:#5b5140;
  --brass:#8a6a34; --brass-bright:#a97f38; --brass-text:#725626; --brass-line: rgba(138,106,52,.32);
  --hairline: rgba(33,26,18,.14); --hairline-strong: rgba(33,26,18,.25);
  --shadow: 0 18px 40px -22px rgba(24,17,12,.55);
  --good:#3f6b3f; --good-text:#2f5a2f; --good-bg: rgba(63,107,63,.12);
  --bad:#7a2e2e; --bad-text:#8a2f2f; --bad-bg: rgba(122,46,46,.12);
  --band:#211a12; --band-ink:#ecdfc4;
  --map-1: rgba(138,106,52,.28); --map-2: rgba(138,106,52,.55); --map-3: rgba(138,106,52,.88);
  --wrap:1120px; --gutter:20px;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#161009; --paper-2:#1f170e; --paper-3:#2a2015;
    --ink:#ece3d1; --ink-soft:#b6a98d;
    --brass:#cf9f52; --brass-bright:#e0b46a; --brass-text:#e0b46a; --brass-line: rgba(207,159,82,.32);
    --hairline: rgba(236,227,209,.14); --hairline-strong: rgba(236,227,209,.28);
    --shadow: 0 18px 44px -20px rgba(0,0,0,.6);
    --good:#7fbf7f; --good-text:#8fcf8f; --good-bg: rgba(127,191,127,.14);
    --bad:#c65f5f; --bad-text:#e08a8a; --bad-bg: rgba(198,95,95,.14);
    --band:#0c0805; --band-ink:#ecdfc4;
    --map-1: rgba(207,159,82,.28); --map-2: rgba(207,159,82,.55); --map-3: rgba(207,159,82,.88);
  }
}
:root[data-theme="dark"]{
  --paper:#161009; --paper-2:#1f170e; --paper-3:#2a2015;
  --ink:#ece3d1; --ink-soft:#b6a98d;
  --brass:#cf9f52; --brass-bright:#e0b46a; --brass-text:#e0b46a; --brass-line: rgba(207,159,82,.32);
  --hairline: rgba(236,227,209,.14); --hairline-strong: rgba(236,227,209,.28);
  --shadow: 0 18px 44px -20px rgba(0,0,0,.6);
  --good:#7fbf7f; --good-text:#8fcf8f; --good-bg: rgba(127,191,127,.14);
  --bad:#c65f5f; --bad-text:#e08a8a; --bad-bg: rgba(198,95,95,.14);
  --band:#0c0805; --band-ink:#ecdfc4;
  --map-1: rgba(207,159,82,.28); --map-2: rgba(207,159,82,.55); --map-3: rgba(207,159,82,.88);
}

*{box-sizing:border-box}
html{ -webkit-text-size-adjust:100%; }
body{ margin:0; background:var(--paper); color:var(--ink); font-family:"Spectral",Georgia,serif; line-height:1.55; -webkit-font-smoothing:antialiased; }
h1,h2{ text-wrap:balance }
.mono{ font-family:"IBM Plex Mono", ui-monospace, monospace; }
.display{ font-family:"Big Shoulders Display","Arial Narrow",sans-serif; }
.tabular{ font-variant-numeric:tabular-nums; }
a{ color:inherit }
img{ max-width:100%; }
.srOnly{ position:absolute !important; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
:focus-visible{ outline:2px solid var(--brass-bright); outline-offset:2px; }

.wrap{ max-width:var(--wrap); margin:0 auto; padding-inline:var(--gutter); }
@media (min-width:760px){ :root{ --gutter:32px; } }
@media (min-width:1200px){ :root{ --gutter:40px; } }
.bleed{ margin-inline:calc(50% - 50vw); }
[id]{ scroll-margin-top:80px; }

/* ---------- kicker / headline system (2026-09-16 redesign) ---------- */
.kicker{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.18em; text-transform:uppercase; color:var(--brass-text); font-weight:500; }
.pageIntro{ padding-block:36px 8px; }
.pageIntro .kicker{ display:block; margin-bottom:10px; }
.pageTitle{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(32px,4.6vw,48px); line-height:1; margin:26px 0 12px; text-wrap:balance; letter-spacing:-.005em; }
.pageIntro .pageTitle{ margin-top:0; }
.lede{ font-size:17px; color:var(--ink-soft); max-width:64ch; margin:0 0 8px; text-wrap:pretty; }
.pageKicker{ display:block; margin:36px 0 0; }
.pageKicker + .pageTitle{ margin-top:10px; }
.eyebrow{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.18em; text-transform:uppercase; color:var(--brass-text); }
.hero{ display:flex; flex-direction:column; gap:10px; padding:22px 24px; border:1px solid var(--hairline-strong); }
.hero h2{ margin:0; }
.hero .lede{ margin:0; }
.crumbRow{ padding:10px var(--gutter) 0; font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--ink-soft); }
.crumbRow a{ text-decoration:underline; text-decoration-color:var(--brass-line); text-underline-offset:3px; color:var(--brass-text); }
.crumbRow a:hover{ color:var(--ink); }

/* ---------- site header ---------- */
.siteHead{ position:sticky; top:0; z-index:40; background:var(--paper); border-bottom:1px solid var(--hairline); }
.siteHeadRow{ display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:0 18px; min-height:64px; }
.siteHeadRow > .primaryNav{ padding-block:8px; }
.brand{ display:inline-flex; align-items:center; gap:12px; flex:none; text-decoration:none; color:var(--ink); font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:22px; letter-spacing:.01em; white-space:nowrap; }
.beltMark{ flex:none; }
.brandShort{ display:none; }
.headTools{ display:none; align-items:center; gap:4px; }
.primaryNav{ display:flex; align-items:center; gap:22px; flex:1; justify-content:flex-end; min-width:0; }
.navLinks{ display:flex; align-items:center; gap:26px; font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.12em; text-transform:uppercase; }
.navLinks > a, .moreMenu > summary{ text-decoration:none; color:var(--ink-soft); padding:6px 0; border-bottom:2px solid transparent; white-space:nowrap; cursor:pointer; }
.navLinks > a:hover, .moreMenu > summary:hover{ color:var(--ink); }
.navLinks > a[aria-current="page"], .moreMenu[data-active] > summary{ color:var(--ink); border-bottom-color:var(--brass-bright); }
.moreMenu{ position:relative; }
.moreMenu > summary{ list-style:none; display:inline-flex; align-items:center; gap:6px; }
.moreMenu > summary::-webkit-details-marker{ display:none; }
.moreMenu[open] > summary svg{ transform:rotate(180deg); }
.moreGrid{ position:absolute; right:0; top:calc(100% + 10px); z-index:50; display:grid; grid-template-columns:repeat(4,minmax(150px,1fr)); gap:22px; padding:20px 22px; background:var(--paper); border:1px solid var(--hairline-strong); border-radius:6px; box-shadow:var(--shadow); text-transform:none; letter-spacing:0; font-family:"Spectral",Georgia,serif; font-size:15px; }
.moreGroup{ display:flex; flex-direction:column; gap:8px; }
.moreKicker{ font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:var(--brass-text); margin-bottom:2px; }
.moreGroup a{ text-decoration:none; color:var(--ink); white-space:nowrap; }
.moreGroup a:hover, .moreGroup a[aria-current="page"]{ color:var(--brass-text); }
.moreGroup a[aria-current="page"]{ font-weight:600; }
.navTools{ display:flex; align-items:center; gap:10px; }
.siteSearch{ position:relative; display:flex; align-items:center; gap:8px; height:36px; width:200px; flex:0 1 200px; min-width:120px; padding:0 12px; border:1px solid var(--hairline-strong); border-radius:18px; color:var(--ink-soft); background:var(--paper); }
.siteSearch:focus-within{ border-color:var(--brass); color:var(--ink); }
.siteSearch svg{ flex:none; display:block; }
.siteSearch .searchIcon{ display:flex; }
.siteSearch input{ flex:1; min-width:0; border:0; background:transparent; color:var(--ink); font-family:"IBM Plex Mono",monospace; font-size:12px; outline:none; padding:0; }
.siteSearch input::placeholder{ color:var(--ink-soft); }
.siteSearch input::-webkit-search-cancel-button{ -webkit-appearance:none; }
.searchResults{ position:absolute; top:calc(100% + 8px); right:0; left:0; min-width:260px; z-index:60; background:var(--paper); border:1px solid var(--hairline-strong); border-radius:6px; box-shadow:var(--shadow); overflow:hidden; }
.searchHit{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; padding:10px 14px; text-decoration:none; color:var(--ink); border-top:1px solid var(--hairline); }
.searchHit:first-child{ border-top:0; }
.searchHit:hover, .searchHit.active{ background:var(--paper-2); }
.searchHitName{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:16px; }
.searchHitKind{ font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:var(--ink-soft); white-space:nowrap; }
.themeToggle{ display:inline-flex; align-items:center; justify-content:center; width:36px; height:36px; padding:0; margin:0; flex:none; border:1px solid var(--hairline-strong); border-radius:50%; background:transparent; color:var(--ink-soft); cursor:pointer; }
.themeToggle:hover{ border-color:var(--brass); color:var(--ink); }
.themeToggle .ti{ display:none; }
html:not([data-theme-mode]) .themeToggle .ti-auto, html[data-theme-mode="auto"] .themeToggle .ti-auto{ display:block; }
html[data-theme-mode="light"] .themeToggle .ti-light{ display:block; }
html[data-theme-mode="dark"] .themeToggle .ti-dark{ display:block; }
.navToggle{ display:inline-flex; align-items:center; justify-content:center; width:44px; height:44px; border:0; background:transparent; color:var(--ink); cursor:pointer; padding:0; margin-right:-10px; }
.navToggle .nt-close{ display:none; }
html.navOpen .navToggle .nt-open{ display:none; }
html.navOpen .navToggle .nt-close{ display:block; }
@media (max-width:1240px){
  .brand{ font-size:20px; }
  .navLinks{ gap:18px; font-size:11px; }
  .primaryNav{ gap:14px; }
  .siteSearch{ width:36px; min-width:36px; padding:0; justify-content:center; cursor:text; transition:width .15s ease; }
  .siteSearch:focus-within{ width:200px; padding:0 12px; justify-content:flex-start; }
  .siteSearch input{ width:0; opacity:0; }
  .siteSearch:focus-within input{ width:auto; opacity:1; }
  .siteSearch .searchIcon{ cursor:pointer; }
}
@media (max-width:1000px){
  .siteHeadRow{ flex-wrap:wrap; min-height:56px; gap:0; }
  .brand{ font-size:19px; }
  .headTools{ display:flex; }
  .primaryNav{ display:none; flex-basis:100%; flex-direction:column; align-items:stretch; gap:0; padding:6px 0 18px; border-top:1px solid var(--hairline); margin-top:6px; max-height:calc(100vh - 70px); max-height:calc(100dvh - 70px); overflow-y:auto; overscroll-behavior:contain; }
  html.navOpen .primaryNav{ display:flex; }
  .navTools{ order:-1; padding:12px 0 6px; }
  .siteSearch{ width:100%; height:44px; border-radius:22px; }
  .navThemeSlot{ display:none; }
  .navLinks{ flex-direction:column; align-items:stretch; gap:0; font-size:13px; }
  .navLinks > a, .moreMenu > summary{ display:flex; align-items:center; min-height:48px; padding:0; border-bottom:1px solid var(--hairline); color:var(--ink); }
  .navLinks > a[aria-current="page"], .moreMenu[data-active] > summary{ border-bottom-color:var(--hairline); color:var(--brass-text); }
  .moreMenu{ display:contents; }
  .moreMenu > summary{ display:none; }
  .moreGrid{ position:static; display:grid; grid-template-columns:1fr; gap:14px; padding:14px 0 0; border:0; box-shadow:none; background:transparent; }
  .moreGroup{ gap:0; }
  .moreKicker{ padding:12px 0 6px; }
  .moreGroup a{ display:flex; align-items:center; min-height:44px; font-size:16px; border-bottom:1px solid var(--hairline); }
}
@media (max-width:420px){ .brandLong{ display:none; } .brandShort{ display:inline; } }

.gameMeta{ display:flex; gap:14px; flex-wrap:wrap; align-items:center; margin: 22px 0 6px; font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--brass-text); }
.gameMeta .dot{ width:4px; height:4px; border-radius:50%; background:var(--ink-soft); }
.beltTag{ background: var(--good-bg); color:var(--good-text); padding:3px 9px; border-radius:3px; font-weight:600; }
.neutralTag{ background: color-mix(in srgb, var(--brass) 18%, transparent); color:var(--brass-text); padding:3px 9px; border-radius:3px; font-weight:600; }

h1.matchup{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:clamp(28px,4.6vw,42px); line-height:1.02; margin:6px 0 26px; }
h1.matchup .win{ color:var(--emph); }

/* scoreboard */
.scoreboard{
  display:grid; grid-template-columns:1fr auto 1fr; align-items:stretch; gap:0;
  border:1px solid var(--hairline); border-radius:8px; overflow:hidden; box-shadow:var(--shadow);
}
@media (max-width:640px){ .scoreboard{ grid-template-columns:1fr; } .scoreboard .vs{ display:none } }
.teamLogo{ object-fit:contain; flex:none; vertical-align:middle; }
.teamPanel{ padding:26px 22px; display:flex; flex-direction:column; gap:10px; }
.teamPanel .panelTop{ display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
.teamPanel .teamLogo{ filter:drop-shadow(0 1px 3px rgba(0,0,0,.4)); }
.logoChip{ position:relative; display:flex; align-items:center; justify-content:center; flex:none; border-radius:50%; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,.35); overflow:hidden; }
.dotInit{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:.38em; letter-spacing:.02em; line-height:1; }
.logoChip .dotInit{ font-size:12px; }
.logoChip .teamLogo, .tlDot .teamLogo{ position:absolute; inset:0; margin:auto; z-index:1; background:#fff; border-radius:50%; }
.logoChip .teamLogo{ filter:none; }
.teamPanel.home{ background: linear-gradient(160deg, var(--home) 0%, color-mix(in srgb, var(--home) 75%, black) 100%); color:var(--home-ink); }
.teamPanel.away{ background: linear-gradient(160deg, var(--away) 0%, color-mix(in srgb, var(--away) 75%, black) 100%); color:var(--away-ink); }
.teamPanel .side{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.14em; text-transform:uppercase; opacity:.8; }
.teamPanel .name{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(22px,3.6vw,30px); line-height:1.02; }
.teamPanel .pts{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:clamp(46px,7vw,64px); line-height:.9; margin-top:2px; }
.teamPanel.home .pts{ color: var(--home-accent); }
.teamPanel.away .pts{ color: var(--away-accent); }
.teamPanel .badge{ align-self:flex-start; font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.08em; text-transform:uppercase; padding:3px 8px; border-radius:3px; border:1px solid currentColor; opacity:.85; margin-top:4px; }
.vs{ display:flex; align-items:center; justify-content:center; padding:0 18px; background:var(--paper-2); font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:15px; color:var(--ink-soft); }

/* section pattern: a mono kicker stacked over a display headline, with an
   optional right-aligned link/tag on the headline's baseline */
.sectionHead{ display:grid; grid-template-columns:1fr auto; align-items:end; column-gap:16px; row-gap:6px; margin:48px 0 18px; padding-bottom:12px; border-bottom:1px solid var(--hairline); }
.sectionHead .tag{ grid-column:1; font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.18em; text-transform:uppercase; color:var(--brass-text); }
.sectionHead .rule{ display:none; }
.sectionHead h2{ grid-column:1; font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(24px,3.2vw,32px); line-height:1; margin:0; }
.sectionHead .sourceTag, .sectionHead .sectionLink{ grid-column:2; grid-row:1 / span 2; align-self:end; }
.sectionLink{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--brass-text); text-decoration:none; white-space:nowrap; }
.sectionLink:hover{ color:var(--ink); }
.sectionHead.withTag{ }

/* line score table */
table.lineScore{ width:100%; border-collapse:collapse; font-family:"IBM Plex Mono",monospace; font-size:14px; }
table.lineScore th, table.lineScore td{ padding:10px 12px; text-align:center; border-bottom:1px solid var(--hairline); }
table.lineScore th{ font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); font-weight:600; text-align:center; }
table.lineScore td.teamCell, table.lineScore th.teamCell{ text-align:left; font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:16px; letter-spacing:.01em; }
table.lineScore tr.winner td.teamCell{ color:var(--emph); }
table.lineScore td.final{ font-weight:700; font-size:16px; }
table.lineScore tbody tr:last-child td{ border-bottom:none; }
.swatch{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:8px; vertical-align:middle; border:1px solid var(--hairline); }

.statCategory{ margin:0 0 26px; }
.statCategory:last-child{ margin-bottom:0; }
.statCategory h3{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.1em; text-transform:uppercase; color:var(--ink-soft); margin:0 0 8px; }
table.playerStatsTable{ width:100%; border-collapse:collapse; font-size:13.5px; }
table.playerStatsTable th{ text-align:right; font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-soft); font-weight:600; padding:7px 8px; border-bottom:1px solid var(--brass-line); }
table.playerStatsTable th.teamCell{ text-align:left; }
table.playerStatsTable td{ padding:8px; border-bottom:1px solid var(--hairline); text-align:right; }
table.playerStatsTable td.teamCell{ text-align:left; font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:14.5px; white-space:nowrap; }
table.playerStatsTable td.teamCell .playerTeam{ font-family:"IBM Plex Mono",monospace; font-weight:400; font-size:10.5px; color:var(--ink-soft); margin-left:6px; text-transform:uppercase; letter-spacing:.04em; }
table.playerStatsTable tbody tr:last-child td{ border-bottom:none; }
table.playerStatsTable td.teamCell a{ color:inherit; text-decoration:none; border-bottom:1px dotted var(--ink-soft); }
table.playerStatsTable td.teamCell a:hover{ border-bottom-style:solid; border-bottom-color:var(--ink); }
.statCategoryTeams{ display:grid; grid-template-columns:1fr 1fr; gap:10px 22px; align-items:start; }
@media (max-width:640px){ .statCategoryTeams{ grid-template-columns:1fr; gap:18px; } }
.statTeamBlock h4.statTeamName{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:14.5px; margin:0 0 6px; color:var(--ink); }
.noStatsForTeam{ font-size:12.5px; color:var(--ink-soft); font-style:italic; margin:0; }
table.playerGameLog{ width:100%; border-collapse:collapse; font-size:13.5px; }
table.playerGameLog th{ text-align:left; font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-soft); font-weight:600; padding:7px 8px; border-bottom:1px solid var(--brass-line); }
table.playerGameLog td{ padding:9px 8px; border-bottom:1px solid var(--hairline); vertical-align:top; }
table.playerGameLog tbody tr:last-child td{ border-bottom:none; }
table.playerGameLog td.tabular{ font-family:"IBM Plex Mono",monospace; white-space:nowrap; }
.playerGameCat{ margin:0 0 3px; font-size:12.5px; }
.playerGameCat:last-child{ margin-bottom:0; }
.playerGameCat .catLabel{ font-weight:700; margin-right:6px; }
.playerGameCat .catLine{ color:var(--ink-soft); font-family:"IBM Plex Mono",monospace; font-size:11.5px; }
table.keyPlaysTable{ width:100%; border-collapse:collapse; font-size:13.5px; }
table.keyPlaysTable th{ text-align:left; font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-soft); font-weight:600; padding:7px 8px; border-bottom:1px solid var(--brass-line); }
table.keyPlaysTable td{ padding:9px 8px; border-bottom:1px solid var(--hairline); vertical-align:top; }
table.keyPlaysTable td:first-child{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); white-space:nowrap; }
table.keyPlaysTable td:nth-child(2){ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:14px; white-space:nowrap; }
table.keyPlaysTable td:nth-child(3){ font-family:"IBM Plex Mono",monospace; font-size:12.5px; white-space:nowrap; }
table.keyPlaysTable tbody tr:last-child td{ border-bottom:none; }
table.keyPlaysTable tr.scoring td:nth-child(2){ color:var(--brass-text); }
details.moreStats{ margin-top:8px; }
details.moreStats summary{ cursor:pointer; font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.04em; color:var(--brass-text); padding:8px 0; list-style:none; }
details.moreStats summary::-webkit-details-marker{ display:none; }
details.moreStats summary::before{ content:"+ "; }
details.moreStats[open] summary::before{ content:"\\2212 "; }
details.moreStats summary:hover{ text-decoration:underline; }
details.moreStats .statCategory{ margin-top:18px; }

/* box score */
.sampleTag{
  font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.1em; text-transform:uppercase;
  background: color-mix(in srgb, var(--brass) 18%, transparent); color:var(--brass-text);
  padding:3px 8px; border-radius:3px; border:1px dashed var(--brass-line);
}
.sourceTag{
  font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
  background: var(--good-bg); color:var(--good-text);
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

/* ---------- upcoming game preview page ---------- */
.previewMeta{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; color:var(--ink-soft); margin:2px 0 6px; }
.previewOdds{ font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.01em; color:var(--ink-soft); margin:0 0 14px; padding:8px 12px; background:var(--paper-2); border:1px solid var(--brass-line); border-radius:4px; }
.kickoffLocal{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; color:var(--ink-soft); margin:0 0 30px; }
.kickoffLocal[hidden]{ display:none; }
.formGrid{ display:grid; grid-template-columns:1fr 1fr; gap:22px; margin:0 0 8px; }
@media (max-width:700px){ .formGrid{ grid-template-columns:1fr; } }
.formCol h3{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; margin:0 0 10px; display:flex; align-items:center; gap:8px; }
.formList{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px; }
.formList li{ display:flex; align-items:center; gap:10px; padding:9px 11px; background:var(--paper-2); border-radius:6px; font-size:13px; }
.formBadge{ font-family:"IBM Plex Mono",monospace; font-weight:700; font-size:11px; width:20px; height:20px; border-radius:50%; display:flex; align-items:center; justify-content:center; flex-shrink:0; }
.formBadge.w{ background:#2f6b3f; color:#eafaea; }
.formBadge.l{ background:#7a2e2e; color:#fbeaea; }
.formBadge.t{ background:var(--ink-soft); color:var(--paper); }
.formScore{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap; }
.formOpp{ color:var(--ink-soft); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.formDate{ margin-left:auto; font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--ink-soft); white-space:nowrap; }
.emptyNote{ color:var(--ink-soft); font-size:13px; }
.weatherCard{
  display:flex; align-items:baseline; flex-wrap:wrap; gap:6px 14px;
  padding:14px 18px; margin:4px 0 6px; border:1px solid var(--hairline);
  border-radius:6px; background:var(--paper-2);
}
.weatherTemp{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:26px; }
.weatherCond{ font-size:14.5px; color:var(--ink); }
.weatherSub{ font-size:13px; color:var(--ink-soft); flex-basis:100%; }
.h2hHeadline{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:21px; margin:0 0 16px; }
.aiPreviewBody p{ max-width:70ch; font-size:15px; }
.keyMatchups{ margin:14px 0 20px; padding-left:1.15em; max-width:68ch; }
.keyMatchups li{ margin:7px 0; font-size:14.5px; }
.keyMatchups li::marker{ color:var(--brass-text); }
.bettingHead{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); margin:20px 0 6px; }
.predictionCall{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:23px; margin:0 0 10px; color:var(--brass-text); }
.predictionBody{ margin-top:4px; }

/* ---------- next-game preview page (2026-09-16 redesign) ---------- */
.matchHead{ display:grid; grid-template-columns:1fr 200px 1fr; margin-top:18px; border-radius:6px; overflow:hidden; box-shadow:var(--shadow); }
.matchSide{ padding:36px 36px 30px; display:flex; flex-direction:column; gap:14px; min-width:0; }
.matchSide.home{ background:linear-gradient(160deg, var(--home) 0%, color-mix(in srgb, var(--home) 82%, black) 100%); color:var(--home-ink); }
.matchSide.away{ background:linear-gradient(200deg, var(--away) 0%, color-mix(in srgb, var(--away) 82%, black) 100%); color:var(--away-ink); align-items:flex-end; text-align:right; }
.matchSide .kicker{ color:inherit; opacity:.85; }
.matchSide.home .kicker{ color:var(--home-accent); opacity:1; }
.matchTeam{ display:flex; align-items:center; gap:16px; min-width:0; }
.matchSide.away .matchTeam{ flex-direction:row-reverse; }
.matchName{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:clamp(30px,4.2vw,58px); line-height:.9; text-transform:uppercase; text-wrap:balance; }
.matchName a{ color:inherit; text-decoration:none; }
.matchName a:hover{ text-decoration:underline; text-decoration-thickness:.05em; text-underline-offset:.08em; }
.matchRecord{ font-size:15px; opacity:.82; }
.matchCenter{ background:var(--band); color:var(--band-ink); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; padding:20px 14px; text-align:center; }
.matchCenter .kicker{ color:#cf9f52; }
.matchDay{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:48px; line-height:1; }
.matchWhen{ font-family:"IBM Plex Mono",monospace; font-size:12px; line-height:1.6; }
.matchLocal{ font-family:"IBM Plex Mono",monospace; font-size:11px; opacity:.65; }
.matchLocal[hidden]{ display:none; }
@media (max-width:820px){
  .matchHead{ grid-template-columns:1fr; }
  .matchSide{ padding:24px 20px 20px; }
  .matchSide.away{ align-items:flex-start; text-align:left; }
  .matchSide.away .matchTeam{ flex-direction:row; }
  .matchCenter{ flex-direction:row; flex-wrap:wrap; justify-content:flex-start; gap:6px 16px; text-align:left; padding:14px 20px; }
  .matchCenter .kicker{ flex-basis:100%; }
  .matchDay{ font-size:34px; }
}
.stakes{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid var(--hairline); border-top:0; }
.stakeCell{ padding:16px 22px; display:flex; flex-direction:column; gap:5px; border-right:1px solid var(--hairline); min-width:0; }
.stakeCell:last-child{ border-right:0; }
.stakeVal{ font-size:15px; line-height:1.4; }
.stakeSub{ display:block; font-family:"IBM Plex Mono",monospace; font-size:10.5px; color:var(--ink-soft); margin-top:2px; }
@media (max-width:820px){ .stakes{ grid-template-columns:1fr 1fr; } .stakeCell:nth-child(2){ border-right:0; } .stakeCell:nth-child(-n+2){ border-bottom:1px solid var(--hairline); } }
@media (max-width:480px){ .stakes{ grid-template-columns:1fr; } .stakeCell{ border-right:0; border-bottom:1px solid var(--hairline); } .stakeCell:last-child{ border-bottom:0; } }
.previewOdds{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; color:var(--ink-soft); margin:16px 0 0; }
.previewOdds strong{ color:var(--ink); }
.previewGrid{ display:grid; grid-template-columns:minmax(0,7fr) minmax(0,4fr); gap:56px; margin-top:8px; align-items:start; }
@media (max-width:900px){ .previewGrid{ grid-template-columns:minmax(0,1fr); gap:24px; } }
.previewMain .sectionHead:first-child{ margin-top:40px; }
.editorial{ font-size:17.5px; line-height:1.65; max-width:68ch; }
.editorial p{ margin:0 0 18px; }
.editorial ul{ margin:0 0 18px; }
.leanBox{ padding:22px 24px; background:var(--paper-2); display:flex; flex-direction:column; gap:8px; margin:8px 0 8px; max-width:68ch; }
.leanBox p{ margin:0; font-size:15.5px; line-height:1.6; }
.leanCall{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(24px,2.6vw,30px); line-height:1.05; margin:0; }
.leanNote{ font-size:13px; color:var(--ink-soft); }
.previewSide{ display:flex; flex-direction:column; gap:18px; margin-top:40px; }
.sideCard{ border:1px solid var(--hairline-strong); padding:20px 22px; display:flex; flex-direction:column; gap:12px; }
.sideCard .calendarLinks{ flex-direction:column; margin:0; }
.sideCard .calBtn, .sideCard .btn{ width:100%; text-align:center; justify-content:center; }
.miniList{ display:flex; flex-direction:column; }
.miniRow{ display:flex; justify-content:space-between; gap:12px; padding:9px 0; border-top:1px solid var(--hairline); font-size:14px; text-decoration:none; color:inherit; }
.miniRow:last-child{ border-bottom:1px solid var(--hairline); }
.miniRow:hover strong{ color:var(--brass-text); }
.miniTag{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.1em; text-transform:uppercase; color:var(--ink-soft); white-space:nowrap; }
.miniStats{ display:flex; gap:24px; flex-wrap:wrap; }
.miniStats div{ display:flex; flex-direction:column; gap:2px; }
.miniStats .n{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:30px; line-height:1; }
.miniStats .l{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--ink-soft); }
.sideCard .moreLink{ margin:0; }
.crumbRow .sep{ color:var(--ink-soft); margin:0 4px; }

/* ---------- add to calendar ---------- */
.calendarLinks{ display:flex; gap:10px; flex-wrap:wrap; margin:0 0 26px; }
.calBtn{
  display:inline-flex; align-items:center; justify-content:center; min-height:44px;
  font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.12em; text-transform:uppercase;
  padding:0 16px; border:1px solid var(--hairline-strong); border-radius:4px;
  background:transparent; color:var(--ink); text-decoration:none; white-space:nowrap; cursor:pointer;
}
.calBtn:hover{ border-color:var(--brass); color:var(--brass-text); }

/* ---------- email alerts (homepage) ---------- */
.feedUrlBox{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-top:14px; padding:14px 18px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:8px; }
.feedUrlText{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; word-break:break-all; color:var(--ink-soft); }

/* ---------- compare tool ---------- */
.compareForm{ display:flex; align-items:center; gap:14px; margin:18px 0 30px; flex-wrap:wrap; }
.compareForm select{
  font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:17px;
  padding:10px 14px; border:1px solid var(--hairline); border-radius:6px;
  background:var(--paper-2); color:var(--ink); min-width:200px; max-width:100%;
}
.compareVs{ font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.1em; text-transform:uppercase; color:var(--ink-soft); }
.compareStats{ display:grid; grid-template-columns:1fr 1fr; gap:22px; margin:0 0 30px; }
@media (max-width:700px){ .compareStats{ grid-template-columns:1fr; } }
.compareStatCard{ padding:18px 20px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:8px; }
.compareStatCard h3{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; margin:0 0 12px; display:flex; align-items:center; gap:8px; }
.compareStatRow{ display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--hairline); font-size:13.5px; }
.compareStatRow:last-child{ border-bottom:none; }
.compareStatRow .val{ font-family:"IBM Plex Mono",monospace; font-weight:600; }
.compareGamesHead{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; margin:36px 0 4px; }
.compareRecord{ font-size:13.5px; color:var(--ink-soft); margin:0 0 16px; }
.compareGameRow{ display:flex; align-items:center; gap:14px; padding:11px 4px; border-bottom:1px solid var(--hairline); text-decoration:none; color:inherit; font-size:13.5px; }
.compareGameRow:last-child{ border-bottom:none; }
.compareGameRow:hover .compareMatchup{ text-decoration:underline; text-decoration-color:var(--brass); }
.compareGameDate{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--ink-soft); white-space:nowrap; width:88px; flex:none; }
.compareMatchup{ flex:1; min-width:0; }
.compareScore{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap; }
.compareEmpty{ color:var(--ink-soft); font-size:13.5px; padding:16px 0; }

/* ---------- my team / path to the belt ---------- */
.myTeamPicker{ display:flex; align-items:center; gap:14px; margin:18px 0 30px; flex-wrap:wrap; }
.myTeamPicker select{
  font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:17px;
  padding:10px 14px; border:1px solid var(--hairline); border-radius:6px;
  background:var(--paper-2); color:var(--ink); min-width:220px; max-width:100%;
}
.myTeamPicker button{
  font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.1em; text-transform:uppercase;
  min-height:44px; padding:11px 18px; border-radius:4px; border:none; background:var(--ink); color:var(--paper); cursor:pointer;
}
.myTeamPicker button:hover{ opacity:.88; }
.myTeamHeader{ display:flex; align-items:center; gap:14px; margin:0 0 22px; flex-wrap:wrap; }
.myTeamHeader .teamLogo{ width:48px; height:48px; }
.myTeamChangeLink{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); background:none; border:none; padding:0; cursor:pointer; text-decoration:underline; text-decoration-color:var(--brass); }
.myTeamCard{ padding:22px 24px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:8px; margin:0 0 16px; }
.myTeamCard.holder{ background:var(--good-bg); border-color:var(--good); }
.myTeamCard .kicker{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:var(--brass-text); margin:0 0 8px; }
.myTeamCard p{ margin:0; font-size:16px; line-height:1.6; }
.myTeamCard p + p{ margin-top:10px; }
.myTeamEmpty{ color:var(--ink-soft); font-size:14.5px; padding:8px 0 0; }

/* ---------- embed page ---------- */
.embedPreview{ display:flex; align-items:center; gap:14px; margin:20px 0 28px; padding:18px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:8px; flex-wrap:wrap; }
.embedLabel{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:var(--ink-soft); margin:24px 0 8px; }
.embedCode{ width:100%; box-sizing:border-box; font-family:"IBM Plex Mono",monospace; font-size:12.5px; padding:12px 14px; border:1px solid var(--hairline); border-radius:6px; background:var(--paper-2); color:var(--ink); resize:vertical; }

/* ---------- map journey scrubber ---------- */
.journeyBar{ display:flex; align-items:center; gap:10px; margin:16px 0 4px; flex-wrap:wrap; }
.journeyBtn{ font-family:"IBM Plex Mono",monospace; font-size:12px; border:1px solid var(--hairline); background:var(--paper-2); color:var(--ink); border-radius:6px; padding:7px 13px; cursor:pointer; }
.journeyBtn:hover{ border-color:var(--brass); }
.journeySlider{ flex:1; min-width:140px; accent-color:var(--brass); }
.journeyLabel{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; color:var(--ink-soft); min-width:220px; }
.mapWrap.journeyMode .mapState:not(.mapState--active){ opacity:.25; }
.mapState--active{ fill:var(--brass-bright) !important; stroke:var(--ink) !important; stroke-width:2 !important; }

/* ---------- trivia ---------- */
.triviaProgress{ font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-soft); margin-bottom:14px; }
.triviaQ{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(22px,3.4vw,28px); margin:0 0 22px; text-wrap:balance; max-width:640px; }
.triviaChoices{ display:flex; flex-direction:column; gap:10px; max-width:560px; }
.triviaChoice{
  text-align:left; font-family:"Spectral",Georgia,serif; font-size:15.5px; padding:13px 16px;
  border:1px solid var(--hairline); border-radius:8px; background:var(--paper-2); color:var(--ink); cursor:pointer;
}
.triviaChoice:hover:not(:disabled){ border-color:var(--brass); }
.triviaChoice:disabled{ cursor:default; }
.triviaChoice--right{ border-color:var(--good); background:var(--good-bg); font-weight:600; }
.triviaChoice--wrong{ border-color:var(--bad); background:var(--bad-bg); }
.triviaFeedback{ margin-top:16px; font-size:14px; font-weight:600; min-height:1.2em; }
.triviaFeedback--right{ color:var(--good-text); }
.triviaFeedback--wrong{ color:var(--ink-soft); }
.triviaResults{ max-width:560px; }
.triviaScore{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:56px; color:var(--brass-text); margin-bottom:6px; }

/* ---------- site footer ---------- */
.siteFoot{ margin-top:72px; border-top:1px solid var(--hairline); font-size:14px; color:var(--ink-soft); }
.footGrid{ display:grid; grid-template-columns:2fr 1fr 1fr 1fr; gap:32px; padding-block:44px 32px; }
@media (max-width:820px){ .footGrid{ grid-template-columns:1fr 1fr; } .footBrand{ grid-column:1 / -1; } }
@media (max-width:420px){ .footGrid{ grid-template-columns:1fr; gap:24px; } }
.footBrand{ display:flex; flex-direction:column; gap:12px; }
.footBrand .brand{ font-size:20px; }
.footNote{ margin:0; max-width:40ch; line-height:1.55; text-wrap:pretty; }
.footCol{ display:flex; flex-direction:column; gap:9px; }
.footCol a{ text-decoration:none; color:var(--ink); width:fit-content; padding:3px 0; }
@media (max-width:1000px){ .footCol{ gap:4px; } .footCol a{ padding:8px 0; } }
.footCol a:hover{ color:var(--brass-text); }
.footKicker{ font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:var(--brass-text); margin-bottom:4px; }
.footBase{ display:flex; justify-content:space-between; gap:16px 32px; flex-wrap:wrap; padding-block:18px 36px; border-top:1px solid var(--hairline); font-size:12.5px; }
.footBase span:first-child{ max-width:70ch; }

/* ---------- homepage: hero plate ---------- */
.heroPlate{ background:linear-gradient(180deg, var(--holder) 0%, color-mix(in srgb, var(--holder) 88%, black) 100%); color:var(--holder-ink); position:relative; overflow:hidden; }
.heroPlate::before{ content:""; position:absolute; inset:0; background-image:repeating-linear-gradient(135deg, rgba(255,255,255,.028) 0 2px, transparent 2px 14px); pointer-events:none; }
.heroGrid{ position:relative; display:grid; grid-template-columns:7fr 5fr; gap:40px; align-items:end; padding-block:56px 52px; }
@media (max-width:900px){ .heroGrid{ grid-template-columns:1fr; gap:28px; padding-block:34px 28px; } }
.heroCopy{ display:flex; flex-direction:column; gap:20px; min-width:0; }
.heroKicker{ display:flex; align-items:center; gap:12px; font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.2em; text-transform:uppercase; color:var(--holder-alt); }
.heroKicker::before{ content:""; width:8px; height:8px; border-radius:50%; background:var(--holder-alt); flex:none; }
.heroKicker a{ color:inherit; text-decoration:none; }
.heroName{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:clamp(64px,11.5vw,150px); line-height:.86; letter-spacing:-.01em; text-transform:uppercase; margin:0; text-wrap:balance; overflow-wrap:anywhere; }
.heroName a{ color:inherit; text-decoration:none; }
.heroName a:hover{ text-decoration:underline; text-decoration-thickness:.04em; text-underline-offset:.08em; }
.heroLede{ font-size:clamp(17px,1.6vw,22px); line-height:1.4; max-width:34ch; margin:0; color:color-mix(in srgb, var(--holder-ink) 82%, transparent); text-wrap:pretty; }
.heroStats{ display:flex; gap:clamp(24px,4vw,48px); flex-wrap:wrap; margin-top:4px; }
.heroStats div{ display:flex; flex-direction:column; gap:4px; }
.heroStats .n{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(34px,4vw,48px); line-height:1; color:var(--holder-alt); }
.heroStats .l{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:color-mix(in srgb, var(--holder-ink) 62%, transparent); }
.heroStats .l a{ color:inherit; text-decoration:none; border-bottom:1px dotted currentColor; }

/* the Up Next card -- the one call to action on the page */
.upNext{ background:var(--paper); color:var(--ink); border-radius:6px; padding:24px 26px; display:flex; flex-direction:column; gap:16px; box-shadow:0 18px 40px -22px rgba(0,0,0,.6); }
.upNextHead{ display:flex; justify-content:space-between; align-items:center; gap:10px; }
.upNextHead .kicker{ font-size:11px; }
.soonChip{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--good-text); background:var(--good-bg); padding:4px 8px; border-radius:3px; white-space:nowrap; }
.upNextMatch{ display:flex; align-items:center; gap:14px; }
.upNextMatch .vs{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:26px; background:none; padding:0; color:var(--ink-soft); }
.upNextWho{ display:flex; flex-direction:column; gap:2px; min-width:0; }
.upNextWho .team{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(22px,2.2vw,27px); line-height:1.05; }
.upNextWho .when{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); }
.upNextOdds{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--ink-soft); margin:-4px 0 0; }
.upNextOdds a, .previewOdds a{ color:inherit; text-decoration:underline; text-decoration-color:var(--brass-line); text-underline-offset:3px; }
.upNextOdds a:hover, .previewOdds a:hover{ color:var(--brass-text); text-decoration-color:var(--brass); }
.btnRow{ display:flex; gap:10px; flex-wrap:wrap; }
.btn{ display:inline-flex; align-items:center; justify-content:center; gap:8px; min-height:44px; padding:0 18px; border-radius:4px; font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.12em; text-transform:uppercase; text-decoration:none; cursor:pointer; border:1px solid transparent; background:var(--ink); color:var(--paper); white-space:nowrap; }
.btn:hover{ background:var(--brass-text); color:#fff; }
.btn.ghost{ background:transparent; color:var(--ink); border-color:var(--hairline-strong); }
.btn.ghost:hover{ border-color:var(--brass); color:var(--brass-text); background:transparent; }
.btn.grow{ flex:1; }
.beltWatch{ display:flex; flex-direction:column; gap:8px; margin:0; padding-top:14px; border-top:1px solid var(--hairline); font-size:14px; }
.beltWatch .kicker{ font-size:10px; margin-right:10px; }
.watchRow{ display:flex; justify-content:space-between; gap:12px; text-decoration:none; color:inherit; }
.watchRow:hover strong{ color:var(--brass-text); }
.watchRow .when{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); white-space:nowrap; }
.gamedayBanner{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:0; padding:10px 14px; border-radius:5px; text-decoration:none; font-family:"IBM Plex Mono",monospace; border:1px solid; animation:gamedayPulse 2.4s ease-in-out infinite; }
.gamedayBanner.gamedaySafe{ background:var(--good-bg); border-color:var(--good); color:var(--good-text); }
.gamedayBanner.gamedayDanger{ background:var(--bad-bg); border-color:var(--bad); color:var(--bad-text); }
.gamedayTag{ font-size:11px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; padding:2px 7px; border-radius:3px; border:1px solid currentColor; }
.gamedayTag::before{ content:"\25CF"; display:inline-block; margin-right:4px; }
.gamedayScore{ font-size:14px; font-weight:600; color:var(--ink); }
.gamedayClock{ font-size:12px; color:var(--ink-soft); }
.gamedayState{ font-size:11px; font-weight:700; letter-spacing:.06em; margin-left:auto; }
@keyframes gamedayPulse{ 0%,100%{ opacity:1; } 50%{ opacity:.72; } }
@media (prefers-reduced-motion: reduce){ .gamedayBanner{ animation:none; } }

/* one-line thesis under the plate */
.thesis{ display:flex; justify-content:space-between; align-items:center; gap:24px; padding-block:26px; border-bottom:1px solid var(--hairline); }
.thesis p{ margin:0; font-size:clamp(16px,1.4vw,19px); font-style:italic; color:var(--ink-soft); max-width:70ch; text-wrap:pretty; }
@media (max-width:700px){ .thesis{ flex-direction:column; align-items:flex-start; gap:12px; } .btnRow .btn{ flex:1; } }

/* ---------- homepage: chain of custody timeline ---------- */
.timeline{ position:relative; padding-top:18px; margin-top:8px; }
.timeline::before{ content:""; position:absolute; left:0; right:0; top:46px; height:2px; background:var(--brass-line); }
.timelineGrid{ position:relative; display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:16px; }
.tlItem{ display:flex; flex-direction:column; gap:12px; align-items:flex-start; text-decoration:none; color:inherit; min-width:0; }
.tlDot{ position:relative; width:56px; height:56px; border-radius:50%; display:flex; align-items:center; justify-content:center; flex:none; background:#fff; border:3px solid var(--paper); box-shadow:0 0 0 2px var(--brass-line); font-size:14px; overflow:hidden; }
.tlDot .dotInit{ font-size:13px; }
.tlDot .teamLogo{ width:40px; height:40px; }
.tlItem.current .tlDot{ box-shadow:0 0 0 3px var(--holder); }
.tlText{ display:flex; flex-direction:column; gap:4px; min-width:0; }
.tlTeam{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:21px; line-height:1; }
.tlItem:hover .tlTeam{ color:var(--brass-text); }
.tlBeat{ font-size:13.5px; color:var(--ink-soft); }
.tlMeta{ font-family:"IBM Plex Mono",monospace; font-size:11px; color:var(--brass-text); }
.tlItem.current .tlMeta{ color:var(--good-text); }
.tlEarlier{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--ink-soft); text-decoration:none; }
@media (max-width:1000px){ .timelineGrid{ grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; } .tlItem:nth-child(-n+3){ display:none; } }
@media (max-width:760px){
  .timeline::before{ display:none; }
  .timelineGrid{ grid-template-columns:1fr; gap:14px; }
  .tlItem:nth-child(-n+3){ display:flex; }
  .tlItem:nth-child(-n+2){ display:none; }
  .tlItem{ position:relative; flex-direction:row; align-items:flex-start; gap:14px; }
  .tlItem::after{ content:""; position:absolute; left:19px; top:44px; bottom:-14px; width:2px; background:var(--brass-line); }
  .tlItem.current::after{ display:none; }
  .tlDot{ width:40px; height:40px; font-size:12px; }
  .tlDot .teamLogo{ width:28px; height:28px; }
  .tlText{ flex:1; padding-top:2px; }
  .tlText .tlRow{ display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
  .tlTeam{ font-size:20px; }
  .tlItem.current{ background:var(--holder); color:var(--holder-ink); margin-inline:calc(-1 * var(--gutter)); padding:14px var(--gutter); align-items:center; }
  .tlItem.current .tlDot{ box-shadow:none; border-color:transparent; }
  .tlItem.current .tlBeat{ color:color-mix(in srgb, var(--holder-ink) 75%, transparent); }
  .tlItem.current .tlMeta, .tlItem.current:hover .tlTeam{ color:var(--holder-alt); }
}
.tlEarlierRow{ display:none; }
@media (max-width:760px){ .tlEarlierRow{ display:block; padding-left:54px; margin-bottom:4px; } }

/* ---------- homepage: two-up (on this day + ruleset), explore, follow ---------- */
.twoUp{ display:grid; grid-template-columns:1fr 1fr; gap:48px; align-items:start; }
@media (max-width:860px){ .twoUp{ grid-template-columns:minmax(0,1fr); gap:8px; } }
.rulesList{ list-style:none; margin:0; padding:0; display:grid; grid-template-columns:1fr 1fr; gap:14px; counter-reset:rule; }
@media (max-width:1000px){ .rulesList{ grid-template-columns:1fr; } }
.rulesList li{ background:var(--paper-2); padding:18px 20px; display:flex; flex-direction:column; gap:6px; }
.rulesList h3{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:20px; margin:0; line-height:1.05; }
.rulesList p{ margin:0; font-size:14px; line-height:1.5; color:var(--ink-soft); }
.explore{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }
@media (max-width:900px){ .explore{ grid-template-columns:1fr 1fr; } }
@media (max-width:480px){ .explore{ grid-template-columns:1fr; } }
.exploreCard{ display:flex; flex-direction:column; gap:10px; padding:22px; border:1px solid var(--hairline-strong); text-decoration:none; color:inherit; min-height:130px; transition:border-color .15s ease; }
.exploreCard:hover{ border-color:var(--brass); }
.exploreCard svg{ color:var(--brass-text); }
.exploreCard h3{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:22px; margin:0; line-height:1; }
.exploreCard p{ margin:0; font-size:14px; line-height:1.45; color:var(--ink-soft); }
.chipRow{ display:flex; gap:10px; flex-wrap:wrap; margin-top:16px; }
.chipLink{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.1em; text-transform:uppercase; padding:10px 14px; border:1px solid var(--hairline-strong); border-radius:20px; text-decoration:none; color:var(--ink); }
.chipLink:hover{ border-color:var(--brass); color:var(--brass-text); }
.followStrip{ margin-top:56px; padding:28px 32px; background:var(--paper-2); display:flex; justify-content:space-between; align-items:center; gap:24px; flex-wrap:wrap; }
.followStrip h2{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(22px,2.4vw,28px); line-height:1.05; margin:0 0 6px; }
.followStrip p{ margin:0; font-size:15px; color:var(--ink-soft); }
.followStrip details{ flex-basis:100%; font-size:14px; color:var(--ink-soft); }
.followStrip summary{ cursor:pointer; font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--brass-text); }
.feedUrlBox{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-top:12px; padding:12px 14px; background:var(--paper); border:1px solid var(--hairline); border-radius:6px; }
.feedUrlText{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; word-break:break-all; color:var(--ink-soft); }

.remainingSchedule{ display:flex; flex-direction:column; gap:8px; margin:0 0 22px; padding:0; list-style:none; font-size:12.5px; }
.remainingSchedule .watchChip{ display:block; padding:9px 12px; border:1px solid var(--brass-line); border-radius:4px; }
.dodStreakBar{ display:flex; flex-wrap:wrap; align-items:center; gap:22px; margin:20px 0 26px; padding:14px 18px; background:var(--paper-2); border:1px solid var(--brass-line); border-radius:6px; }
.dodStreakBar > div{ display:flex; flex-direction:column; gap:2px; }
.dodStreakBar .n{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:26px; }
.dodStreakBar .l{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); }
.dodHistoryWrap{ flex:1; min-width:160px; }
.dodHistory{ font-size:18px; letter-spacing:2px; line-height:1; }
.dodPicker{ margin:0 0 22px; }
.dodMatchup{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:20px; margin:0 0 14px; }
.dodChoices{ display:flex; gap:14px; flex-wrap:wrap; }
.dodChoice{ flex:1; min-width:160px; padding:16px 18px; border-radius:6px; border:1px solid var(--brass-line); background:var(--paper); font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; cursor:pointer; text-align:left; display:flex; flex-direction:column; gap:4px; transition:transform .12s ease; }
.dodChoice:hover{ transform:translateY(-1px); }
.dodChoice span{ font-family:"IBM Plex Mono",monospace; font-weight:400; font-size:11px; letter-spacing:.04em; text-transform:none; color:var(--ink-soft); }
.dodChoice.dodDefend:hover{ border-color:var(--good); background:var(--good-bg); }
.dodChoice.dodDethrone:hover{ border-color:var(--bad); background:var(--bad-bg); }
.dodPending{ margin:0 0 22px; padding:14px 18px; border:1px solid var(--brass-line); border-radius:6px; background:var(--paper-2); font-size:14px; }
.dodShareBtn{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; letter-spacing:.04em; padding:9px 16px; border-radius:5px; border:1px solid var(--brass); background:transparent; color:var(--brass-text); cursor:pointer; }
.dodShareBtn:hover{ background:var(--brass); color:var(--paper); }
.birthdayPicker{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin:0 0 18px; }
.birthdayPicker input[type="date"]{ font-family:"IBM Plex Mono",monospace; font-size:13px; padding:9px 12px; border-radius:5px; border:1px solid var(--brass-line); background:var(--paper); color:var(--ink); }
.birthdayPicker button{ font-family:"IBM Plex Mono",monospace; font-size:12px; letter-spacing:.1em; text-transform:uppercase; min-height:44px; padding:9px 18px; border-radius:4px; border:1px solid var(--ink); background:var(--ink); color:var(--paper); cursor:pointer; }
.birthdayPicker button:hover{ background:var(--brass-text); border-color:var(--brass-text); color:#fff; }
.birthdayResult{ margin:0 0 30px; padding:16px 18px; border:1px solid var(--brass-line); border-radius:6px; background:var(--paper-2); font-size:14px; line-height:1.6; }
.birthdayResult p{ margin:0 0 8px; }
.birthdayResult p:last-child{ margin-bottom:0; }
.birthdayAnswer{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:19px; }
.watchChip{ color:var(--ink-soft); white-space:nowrap; }
.watchChip strong{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:13.5px; color:var(--ink); }
.heroFacts{ display:flex; gap:26px; flex-wrap:wrap; }
.heroFacts div{ display:flex; flex-direction:column; gap:2px; }
.heroFacts .n{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:26px; }
.heroFacts .l{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); }

/* ---------- homepage: ruleset teaser ---------- */
.rules{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; }
@media (max-width:900px){ .rules{ grid-template-columns:repeat(2,1fr); } }
@media (max-width:520px){ .rules{ grid-template-columns:1fr; } }
.rule-card{ padding:18px 18px 16px; border-top:2px solid var(--brass); background:var(--paper-2); border-radius:2px 2px 6px 6px; }
.rule-card h3{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:17px; margin:0 0 7px; }
.rule-card p{ margin:0; font-size:13.5px; color:var(--ink-soft); }
.rulesFoot{ margin-top:16px; font-size:13.5px; }
.rulesFoot a{ text-decoration:none; border-bottom:1px solid var(--brass); color:var(--ink); font-weight:600; }

/* ---------- homepage: on this day ---------- */
.otdList{ display:flex; flex-direction:column; margin-top:6px; }
.moreLink{ margin:16px 0 0; }
.moreLink, .moreLink a{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--brass-text); text-decoration:none; }
.moreLink:hover, .moreLink a:hover{ color:var(--ink); }
.otdRow{ display:flex; align-items:center; gap:16px; padding:12px 4px; border-bottom:1px solid var(--hairline); text-decoration:none; color:inherit; }
.otdList a.otdRow:hover .otdMatchup{ text-decoration:underline; text-decoration-color:var(--brass); }
.otdRow:last-child{ border-bottom:none; }
.otdYear{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:16px; color:var(--brass-text); width:44px; flex:none; }
.otdMatchup{ flex:1; font-size:14.5px; }
.otdTag{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-soft); white-space:nowrap; }
.otdTag.changed{ color:var(--brass-text); }
.otdRow.hiddenRow{ display:none; }
@media (max-width:560px){ .otdRow{ flex-wrap:wrap; } .otdTag{ order:3; width:100%; padding-left:60px; } }

/* ---------- homepage: stats band ---------- */
.band{ background:var(--band); color:var(--band-ink); margin-block:56px 0; padding-block:44px; }
.bandGrid{ display:grid; grid-template-columns:repeat(4,1fr); gap:24px; text-align:center; }
@media (max-width:700px){ .bandGrid{ grid-template-columns:repeat(2,1fr); gap:26px 18px; } }
.bandGrid .n{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:clamp(40px,5vw,56px); color:#cf9f52; line-height:1; }
.bandGrid .l{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.18em; text-transform:uppercase; color:rgba(236,223,196,.7); margin-top:6px; }

/* ---------- ruleset page ---------- */
.pageTitle{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:clamp(30px,4.6vw,44px); line-height:1.02; margin:26px 0 10px; text-wrap:balance; }
.lede{ font-size:16px; color:var(--ink-soft); max-width:62ch; margin:0 0 8px; }
.proseBlock{ max-width:68ch; }
.proseBlock p{ margin:0 0 14px; }
.ruleList{ margin:10px 0 0; padding-left:1.15em; max-width:68ch; }
.ruleList li{ margin:7px 0; }
.ruleList li::marker{ color:var(--brass-text); }

/* ---------- records page ---------- */
.recordsGrid{ display:grid; grid-template-columns:1fr 1fr; gap:22px; margin:28px 0 8px; }
@media (max-width:760px){ .recordsGrid{ grid-template-columns:1fr; } }
.recordCard{ background:var(--paper-2); border:1px solid var(--hairline); border-radius:10px; padding:20px 22px 8px; }
.recordCard h2{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; margin:0 0 2px; }
.recordCardSub{ font-size:12.5px; color:var(--ink-soft); margin:0 0 14px; }
.recordList{ display:flex; flex-direction:column; }
.recordRow{ display:grid; grid-template-columns:20px minmax(0,1fr) auto; align-items:baseline; column-gap:10px; row-gap:2px; padding:10px 0; border-bottom:1px solid var(--hairline); text-decoration:none; color:inherit; }
.recordRow:last-child{ border-bottom:none; }
a.recordRow:hover .recordMain{ text-decoration:underline; text-decoration-color:var(--brass); }
.recordRank{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); }
.recordMain{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:15px; display:flex; align-items:center; min-width:0; }
.recordValue{ font-size:14px; font-weight:600; color:var(--brass-text); }
.recordSub{ grid-column:2 / 4; font-size:12px; color:var(--ink-soft); }
.currentTag{ font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--brass-text); }

/* ---------- stories (hub + article) ---------- */
.storyGrid{ display:grid; grid-template-columns:repeat(auto-fill, minmax(260px,1fr)); gap:18px; margin:28px 0 8px; }
.storyCard{ display:flex; flex-direction:column; gap:8px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:10px; padding:20px 22px; text-decoration:none; color:inherit; transition:border-color .15s ease, transform .15s ease; }
.storyCard:hover{ border-color:var(--brass); transform:translateY(-1px); }
.storyCard h2, .storyCard h3{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; margin:0; text-wrap:balance; }
.storySection .sectionHead{ margin-top:36px; }
.storySection .storyGrid{ margin-top:0; }
.sectionMeta{ grid-column:2; font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--ink-soft); }
.storyCard p{ font-size:13.5px; color:var(--ink-soft); margin:0; }
.storyCardLink{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.04em; color:var(--brass-text); margin-top:auto; }
.storyArticle{ max-width:680px; }
.storyKicker{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--brass-text); font-weight:600; margin:0 0 6px; }
.storyChapter{ margin:30px 0; padding-top:22px; border-top:1px solid var(--hairline); }
.storyChapter:first-of-type{ margin-top:26px; }
.storyChapter h2{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:21px; margin:0 0 10px; text-wrap:balance; }
.storyChapter p{ font-size:15.5px; line-height:1.6; }
.storyRank{ font-family:"IBM Plex Mono",monospace; font-size:13px; font-weight:600; color:var(--ink-soft); margin-right:8px; }
.storyStat{ display:flex; flex-direction:column; align-items:flex-start; gap:2px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:10px; padding:18px 22px; margin:22px 0; font-size:14px; color:var(--ink-soft); }
.storyStatN{ font-family:"Big Shoulders Display",sans-serif; font-weight:900; font-size:44px; color:var(--brass); line-height:1; }
.storyList{ margin:10px 0 0; padding-left:20px; font-size:14.5px; line-height:1.8; }
.storyList a{ color:inherit; }
.storyBackLink{ margin-top:34px; font-size:13.5px; }

/* ---------- 2026-09 batch: outlook, timeline, ledger, daily, leaders ---------- */
.outlookLead{ margin:10px 0 26px; }
.outlookList{ list-style:none; margin:0; padding:0; border-top:1px solid var(--hairline); }
.outlookRow{ display:grid; grid-template-columns:28px 28px minmax(0,1fr) minmax(80px,2fr) 56px; align-items:center; column-gap:12px; padding:9px 0; border-bottom:1px solid var(--hairline); }
.outlookRow.isHolder{ background:color-mix(in srgb, var(--brass) 8%, transparent); margin:0 -8px; padding-left:8px; padding-right:8px; }
.outlookRank{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); text-align:right; }
.tlDot.outlookDot{ width:28px; height:28px; border-width:2px; box-shadow:0 0 0 1px var(--hairline-strong); font-size:9px; }
.tlDot.outlookDot .dotInit{ font-size:8px; letter-spacing:0; }
.tlDot.outlookDot .teamLogo{ width:22px; height:22px; }
.outlookTeam{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:17px; display:flex; align-items:center; gap:10px; min-width:0; }
.outlookTeam a{ text-decoration:none; color:inherit; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.outlookTeam a:hover{ color:var(--brass-text); }
.outlookTeam .soonChip{ font-size:9.5px; padding:3px 6px; }
.outlookBar{ display:block; height:10px; border-radius:5px; background:var(--paper-3); overflow:hidden; }
.outlookFill{ display:block; height:100%; border-radius:5px; background:linear-gradient(90deg, var(--brass) 0%, var(--brass-bright) 100%); }
.outlookPct{ font-family:"IBM Plex Mono",monospace; font-size:13px; font-weight:600; text-align:right; }
@media (max-width:560px){
  .outlookRow{ grid-template-columns:24px 28px minmax(0,1fr) 52px; }
  .outlookBar{ grid-column:3 / 5; grid-row:2; height:6px; margin-top:-4px; }
  .outlookTeam{ font-size:16px; }
  .outlookTeam .soonChip{ display:none; }
}

.tlWrap{ margin:26px 0 10px; overflow-x:auto; -webkit-overflow-scrolling:touch; }
.tlSvg{ display:block; width:100%; height:auto; min-width:640px; }
.tlLabel{ font-family:"IBM Plex Mono",monospace; font-size:12px; fill:var(--ink-soft); }
.tlTrack{ fill:var(--paper-2); }
.tlTick{ stroke:var(--hairline-strong); stroke-width:1; }
.tlSvg a rect:not(.tlTrack){ stroke:var(--paper); stroke-width:.6; }
.tlSvg a:hover rect, .tlSvg a:focus rect{ stroke:var(--brass-bright); stroke-width:2; }
.tlLegend{ list-style:none; display:flex; flex-wrap:wrap; gap:8px 22px; margin:6px 0 0; padding:0; font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); }
.tlLegend li{ display:flex; align-items:center; gap:8px; }
.tlLegend .swatch{ margin:0; width:12px; height:12px; }
.tlLegend a{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:16px; color:var(--ink); text-decoration:none; }
.tlLegend a:hover{ color:var(--brass-text); }

.ledger{ border-top:1px solid var(--hairline); margin-top:8px; }
.ledgerRow{ display:grid; grid-template-columns:150px minmax(0,1.4fr) minmax(0,1.6fr) minmax(0,1fr) 86px; column-gap:16px; align-items:center; padding:12px 0; border-bottom:1px solid var(--hairline); font-size:14px; }
.ledgerDate{ font-size:12px; color:var(--ink-soft); }
.ledgerGame{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:17px; }
.ledgerGame a{ text-decoration:none; color:inherit; }
.ledgerGame a:hover{ color:var(--brass-text); }
.ledgerPick{ color:var(--ink-soft); font-size:13.5px; }
.ledgerPick strong{ color:var(--ink); }
.ledgerResult{ font-size:13.5px; }
.ledgerResult a{ color:inherit; text-decoration:underline; text-decoration-color:var(--brass-line); text-underline-offset:3px; }
.ledgerTag{ justify-self:end; font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.12em; text-transform:uppercase; padding:4px 8px; border-radius:3px; background:var(--paper-3); color:var(--ink-soft); white-space:nowrap; }
.ledgerRow.hit .ledgerTag{ background:var(--good-bg); color:var(--good-text); }
.ledgerRow.miss .ledgerTag{ background:var(--bad-bg); color:var(--bad-text); }
.ledgerRow.pending .ledgerTag{ border:1px dashed var(--hairline-strong); background:transparent; }
@media (max-width:820px){
  .ledgerRow{ grid-template-columns:minmax(0,1fr) auto; row-gap:4px; }
  .ledgerDate{ grid-column:1; }
  .ledgerTag{ grid-column:2; grid-row:1; }
  .ledgerGame, .ledgerPick, .ledgerResult{ grid-column:1 / 3; }
}

.dailyWrap{ max-width:640px; }
.dailyClues{ list-style:none; margin:0 0 18px; padding:0; counter-reset:clue; }
.dailyClues li{ position:relative; padding:12px 14px 12px 52px; border:1px solid var(--hairline); border-radius:8px; background:var(--paper-2); margin-bottom:8px; font-size:15px; line-height:1.5; counter-increment:clue; }
.dailyClues li::before{ content:counter(clue); position:absolute; left:14px; top:11px; width:26px; height:26px; border-radius:50%; background:var(--ink); color:var(--paper); font-family:"IBM Plex Mono",monospace; font-size:12px; font-weight:600; display:flex; align-items:center; justify-content:center; }
.dailyClues li .swatch{ margin:0 2px 0 0; }
.dailyForm{ display:flex; gap:10px; margin:0 0 18px; }
.dailyForm[hidden], .dailyResult[hidden]{ display:none; }
.dailyForm input{ flex:1; min-width:0; min-height:44px; padding:0 14px; font:inherit; font-size:16px; border:1px solid var(--hairline-strong); border-radius:4px; background:var(--paper); color:var(--ink); }
.dailyForm input:focus{ outline:2px solid var(--brass); outline-offset:1px; }
.dailyGuesses{ list-style:none; margin:0 0 18px; padding:0; }
.dailyGuesses li{ display:flex; flex-wrap:wrap; gap:4px 12px; align-items:baseline; padding:9px 12px; border-left:3px solid var(--hairline-strong); background:var(--paper-2); margin-bottom:6px; font-size:14.5px; }
.dailyGuesses li .mono{ font-size:11.5px; color:var(--ink-soft); }
.dailyGuesses li.hit{ border-left-color:var(--good); background:var(--good-bg); }
.dailyGuesses li.hit .mono{ color:var(--good-text); }
.dailyGuesses li.miss{ border-left-color:var(--bad); }
.dailyResult{ margin:18px 0; padding:18px 20px; border:1px solid var(--brass-line); border-radius:8px; background:var(--paper-2); }
.dailyAnswer{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:24px; margin:0 0 14px; }
.dailyAnswer a{ color:var(--brass-text); text-decoration:none; }

.recordUnit{ font-size:11px; font-weight:500; color:var(--ink-soft); letter-spacing:.04em; margin-left:2px; }
.recordMain .playerTeam{ font-family:"IBM Plex Mono",monospace; font-weight:400; font-size:11px; color:var(--ink-soft); margin-left:8px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; flex:0 1 auto; }
.recordMain > a{ flex:none; }
.tlDot.miniDot{ display:inline-flex; vertical-align:middle; width:26px; height:26px; border-width:2px; font-size:8px; box-shadow:0 0 0 1px var(--hairline-strong); margin-right:6px; }
.tlDot.miniDot .dotInit{ font-size:7.5px; letter-spacing:0; }
.tlDot.miniDot .teamLogo{ width:22px; height:22px; }
.miniRow > span:has(.miniDot){ display:flex; align-items:center; gap:2px; min-width:0; }
.teamPlate .pageTitle a{ color:inherit; text-decoration:none; border-bottom:2px solid color-mix(in srgb, var(--team-ink) 45%, transparent); }
.teamPlate .pageTitle a:hover{ border-bottom-color:var(--team-ink); }

/* ---------- team page ---------- */
.teamPlate{ margin-top:24px; padding:30px 32px 28px; border-radius:6px; background:linear-gradient(160deg, var(--team) 0%, color-mix(in srgb, var(--team) 84%, black) 100%); color:var(--team-ink); display:flex; flex-direction:column; gap:22px; box-shadow:var(--shadow); }
.teamPlateRow{ display:flex; align-items:center; gap:18px; }
.teamPlate .kicker{ color:var(--team-accent); display:block; margin-bottom:6px; }
.teamPlate .pageTitle{ margin:0; color:inherit; font-size:clamp(34px,5vw,56px); }
.teamPlate .heroStats .n{ color:var(--team-accent); }
.teamPlate .heroStats .l{ color:color-mix(in srgb, var(--team-ink) 65%, transparent); }
@media (max-width:600px){ .teamPlate{ padding:22px 20px; } }
.teamPageHead{ display:flex; align-items:center; gap:12px; border-bottom:3px solid; padding-bottom:10px; margin-top:22px; }
.posterLink{ display:inline-block; margin-left:6px; font-size:12.5px; color:var(--brass-text); text-decoration:none; border-bottom:1px dotted var(--brass); white-space:nowrap; }
.posterLink:hover{ border-bottom-style:solid; }
.teamReignList{ display:flex; flex-direction:column; gap:10px; margin-top:20px; }
.teamReignCard{ padding:14px 18px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:8px; }
.teamReignHead{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; }
.teamReignDates{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:16px; }
.teamReignDuration{ font-size:13px; color:var(--brass-text); font-weight:600; }
.teamReignDuration a{ color:inherit; text-decoration:none; border-bottom:1px dotted var(--brass-line); }
.teamReignDuration a:hover{ border-bottom-style:solid; }
table.reignsTable td.num a{ color:inherit; text-decoration:none; border-bottom:1px dotted var(--ink-soft); }
table.reignsTable td.num a:hover{ color:var(--brass-text); border-bottom-color:var(--brass); }
.teamReignMeta{ display:flex; gap:16px; flex-wrap:wrap; margin-top:6px; font-size:13px; color:var(--ink-soft); }
.teamReignMeta a{ color:inherit; text-decoration:underline; text-decoration-color:var(--brass); }

/* ---------- map page ---------- */
.mapWrap{ margin:22px 0 6px; background:var(--paper-2); border:1px solid var(--hairline); border-radius:10px; padding:14px; }
.mapSvg{ width:100%; height:auto; display:block; }
.mapState{ fill:var(--paper); stroke:var(--hairline); stroke-width:1; }
.mapState--1{ fill:var(--map-1); stroke:var(--brass-line); }
.mapState--2{ fill:var(--map-2); stroke:var(--brass-line); }
.mapState--3{ fill:var(--map-3); stroke:var(--brass-line); }
.mapState:hover{ stroke:var(--brass-bright); stroke-width:2; }
.mapLegend{ display:flex; flex-direction:column; margin:18px 0 8px; }
.mapLegendRow{ display:grid; grid-template-columns:110px auto 1fr; column-gap:14px; row-gap:2px; padding:9px 0; border-bottom:1px solid var(--hairline); font-size:13px; align-items:baseline; }
.mapLegendRow:last-child{ border-bottom:none; }
.mapLegendState{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:14.5px; }
.mapLegendState a{ color:inherit; text-decoration:none; border-bottom:1px dotted var(--brass-line); }
.mapLegendState a:hover{ color:var(--brass-text); border-bottom-style:solid; }
.mapLegendCount{ color:var(--brass-text); font-weight:600; font-size:12.5px; white-space:nowrap; }
.mapLegendTeams{ color:var(--ink-soft); grid-column:1 / 4; }
@media (min-width:640px){ .mapLegendTeams{ grid-column:3; } }

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

.controls{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
.sortToggle{ display:flex; border:1px solid var(--hairline); border-radius:20px; overflow:hidden; background:var(--paper-2); }
.sortToggle button{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.03em; padding:8px 14px; border:none; background:transparent; color:var(--ink-soft); cursor:pointer; white-space:nowrap; }
.sortToggle button.active{ background:var(--ink); color:var(--paper); font-weight:600; }
.sortToggle button:not(.active):hover{ color:var(--ink); }

/* Losers Belt scope switcher (Combined/FBS/FCS) -- same pill look as
   .sortToggle above but for <a>/<span>, deliberately a separate class so
   it can never be picked up by .sortToggle .sortBtn's sort-order JS. */
.scopeSwitch{ display:inline-flex; border:1px solid var(--hairline); border-radius:20px; overflow:hidden; background:var(--paper-2); }
.scopeBtn{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.03em; padding:8px 14px; color:var(--ink-soft); text-decoration:none; white-space:nowrap; }
.scopeBtn.active{ background:var(--ink); color:var(--paper); font-weight:600; }
.scopeBtn:not(.active):hover{ color:var(--ink); background:var(--paper); }

.dateSelect{ display:flex; gap:8px; }
.dateSelect select{
  font-family:"IBM Plex Mono",monospace; font-size:13px; padding:8px 12px;
  border:1px solid var(--hairline); border-radius:20px; background:var(--paper-2); color:var(--ink);
}
.todayBtn{
  font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.03em;
  padding:8px 14px; border:1px solid var(--hairline); border-radius:20px;
  background:var(--paper-2); color:var(--ink-soft); cursor:pointer;
}
.todayBtn:hover{ color:var(--ink); border-color:var(--brass); }

.records{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:26px 0 34px; }
@media (max-width:820px){ .records{ grid-template-columns:1fr; } }
.record-card{ padding:14px 16px; background:var(--paper-2); border-radius:2px 8px 8px 2px; border-left:3px solid var(--brass); }
.record-card .l{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); margin-bottom:5px; }
.record-card .v{ font-family:"Big Shoulders Display",sans-serif; font-weight:800; font-size:19px; }
.record-card .sub{ font-size:12.5px; color:var(--ink-soft); margin-top:2px; }

.tableScroll{ overflow-x:auto; }
table.reignsTable{ width:100%; border-collapse:collapse; font-size:14.5px; min-width:560px; }
table.reignsTable th{ text-align:left; font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-soft); font-weight:600; padding:9px 10px; border-bottom:1px solid var(--brass-line); position:sticky; top:0; background:var(--paper); }
table.reignsTable td{ padding:10px; border-bottom:1px solid var(--hairline); vertical-align:middle; }
table.reignsTable td.num{ font-family:"IBM Plex Mono",monospace; color:var(--ink-soft); font-size:12.5px; }
table.reignsTable td.teamCell{ font-family:"Big Shoulders Display",sans-serif; font-weight:700; font-size:16px; white-space:nowrap; }
table.reignsTable td.teamCell a{ text-decoration:none; }
@media (max-width:640px){ table.reignsTable td a{ display:inline-block; padding:5px 0; } }
table.reignsTable td.teamCell a:hover{ text-decoration:underline; text-decoration-color:var(--brass); }
table.reignsTable td.won a, table.reignsTable td.lost a{ text-decoration:none; border-bottom:1px dotted var(--ink-soft); }
table.reignsTable td.won a:hover, table.reignsTable td.lost a:hover{ border-bottom-color:var(--brass); color:var(--brass-bright); }
table.reignsTable td.tabular a{ text-decoration:none; color:inherit; border-bottom:1px dotted var(--ink-soft); }
table.reignsTable td.tabular a:hover{ border-bottom-color:var(--brass); color:var(--brass-bright); }
table.reignsTable td.dates, table.reignsTable td.won, table.reignsTable td.lost{ font-size:12.5px; color:var(--ink-soft); }
table.reignsTable td.tabular{ text-align:right; font-family:"IBM Plex Mono",monospace; white-space:nowrap; }
table.reignsTable tr.current{ background: color-mix(in srgb, var(--brass) 10%, transparent); }
table.reignsTable tr.current td.teamCell{ color:var(--brass-text); }
table.reignsTable tr.hiddenRow{ display:none; }
.reignChip{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:9px; vertical-align:middle; border:1px solid var(--hairline); }
.noResults{ padding:34px 0; text-align:center; color:var(--ink-soft); font-style:italic; display:none; }

/* Reign-history pagination (long Losers Belt tables split at 100 rows/page
   -- see generate_losers_belt_page()). Same pill look as .scopeSwitch, kept
   as its own class for the same reason .scopeBtn is separate from
   .sortToggle: no shared JS should ever end up toggling these by mistake. */
.pagerRow{ display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px 20px; margin:14px 0 4px; }
.pagerInfo{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--ink-soft); }
.pagerInfo a{ color:inherit; border-bottom:1px dotted var(--ink-soft); text-decoration:none; }
.pagerInfo a:hover{ color:var(--brass-text); border-bottom-color:var(--brass); }
.reignsPager{ display:inline-flex; flex-wrap:wrap; border:1px solid var(--hairline); border-radius:20px; overflow:hidden; background:var(--paper-2); }
.pagerBtn{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.03em; padding:8px 14px; color:var(--ink-soft); text-decoration:none; white-space:nowrap; border-left:1px solid var(--hairline); }
.pagerBtn:first-child{ border-left:none; }
.pagerBtn.active{ background:var(--ink); color:var(--paper); font-weight:600; }
.pagerBtn:not(.active):hover{ color:var(--ink); background:var(--paper); }
.pagerBtn.ellipsis{ cursor:default; }
.pagerBtn.ellipsis:hover{ background:transparent; color:var(--ink-soft); }

.gameNav{ display:flex; gap:12px; margin:26px 0 0; font-family:"IBM Plex Mono",monospace; font-size:12px; }
.gameNav a, .gameNav span.disabled{ text-decoration:none; color:var(--ink-soft); border:1px solid var(--hairline); border-radius:20px; padding:7px 16px; flex:1; }
.gameNav a:hover{ color:var(--ink); border-color:var(--brass); }
.gameNav a.next, .gameNav span.next{ text-align:right; }
.gameNav a.disabled, .gameNav span.disabled{ color:var(--ink-soft); border-style:dashed; pointer-events:none; }

/* all-games log page */
table.reignsTable td.matchup{ font-size:13.5px; }
table.reignsTable td.matchup a{ text-decoration:none; color:inherit; }
table.reignsTable td.matchup a:hover{ text-decoration:underline; text-decoration-color:var(--brass); }
table.reignsTable td.result{ font-size:12.5px; color:var(--ink-soft); white-space:nowrap; }
table.reignsTable td.result .win{ color:var(--brass-text); font-weight:700; }
table.reignsTable tr.titleChange{ background: color-mix(in srgb, var(--brass) 7%, transparent); }
.viewToggle{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); margin:2px 0 0; }
.viewToggle a{ color:var(--brass-text); text-decoration:none; border-bottom:1px dotted var(--brass); }
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


def minify_css(css):
    """A small, deliberately conservative CSS minifier: strips /* ... */
    comments and collapses/removes whitespace, but never touches anything
    inside a quoted string ("Big Shoulders Display", content:"+ ", the
    @import url('...') itself) -- font names and content-property strings
    that depend on their exact spacing stay byte-for-byte intact. This
    isn't a full CSS parser; it only removes whitespace immediately next
    to `{ } ; ,` and drops a now-redundant trailing `;` before `}` -- safe
    with this stylesheet's syntax (no unquoted url(), no comments inside
    strings) without needing a real tokenizer. Lighthouse's unminified-css
    audit is what this exists to satisfy."""
    out = []
    i, n = 0, len(css)
    in_string = None
    pending_space = False
    while i < n:
        c = css[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                i += 1
                out.append(css[i])
            elif c == in_string:
                in_string = None
            i += 1
            continue
        if c in ('"', "'"):
            if pending_space and out and out[-1] not in "{;,":
                out.append(" ")
            pending_space = False
            in_string = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and css[i + 1] == "*":
            j = css.find("*/", i + 2)
            i = (j + 2) if j != -1 else n
            continue
        if c in " \t\r\n":
            pending_space = True
            i += 1
            continue
        if pending_space:
            if out and not (out[-1] in "{;," or c in "{};,"):
                out.append(" ")
            pending_space = False
        out.append(c)
        i += 1
    return "".join(out).replace(";}", "}").strip() + "\n"


def esc(s):
    return html.escape(str(s), quote=True)


def json_ld(data):
    """A <script type="application/ld+json"> block for one schema.org
    object -- ensure_ascii=False keeps team names/accents readable in view-
    source instead of \\uXXXX escapes; safe inside HTML since JSON has no
    </script>-closing sequence risk here (no user-authored strings contain
    raw "<" this site doesn't already esc() elsewhere)."""
    return f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'


def game_json_ld(g, home, away, home_score, away_score):
    """SportsEvent structured data for one belt game -- lets search engines
    understand the page as a specific sporting event instead of just prose.
    No eventStatus claim (schema.org's EventStatusType has no "completed"
    value, only scheduled/postponed/etc., so asserting one here would be
    inventing a fact), no venue (not tracked for historical games -- only
    the upcoming game has one, see fetch_weather.py)."""
    data = {
        "@context": "https://schema.org",
        "@type": "SportsEvent",
        "name": f"{away} at {home}",
        "startDate": g["date"],
        "sport": "American Football",
        "homeTeam": {"@type": "SportsTeam", "name": home},
        "awayTeam": {"@type": "SportsTeam", "name": away},
        "description": (f"{home} {home_score}, {away} {away_score} \u2014 Belt Game "
                         f"{g['game_number']} of the lineal College Football Belt, since 1869."),
        "url": f"{SITE_URL}/games/{g['game_id']}.html",
    }
    return json_ld(data)


def team_json_ld(team):
    data = {
        "@context": "https://schema.org",
        "@type": "SportsTeam",
        "name": team,
        "sport": "American Football",
        "url": f"{SITE_URL}/teams/{team_slug(team)}.html",
    }
    return json_ld(data)


def ics_escape(text):
    """RFC 5545 TEXT escaping for one field of a .ics VEVENT."""
    return (text.replace("\\", "\\\\").replace(",", "\\,")
                .replace(";", "\\;").replace("\n", "\\n"))


def build_calendar_links(next_game):
    """'Add to Calendar' row for the preview page -- a Google Calendar link
    plus a downloadable .ics (Apple Calendar/Outlook/everything else),
    built entirely server-side from next_game's UTC kickoff (raw_date) and
    venue fields already captured by build_lineage.py's find_next_game() --
    no JS, no extra API call. Returns "" when there's no usable kickoff
    timestamp (raw_date missing/unparseable)."""
    raw = next_game.get("raw_date")
    if not raw:
        return ""
    try:
        start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = start + timedelta(hours=3, minutes=30)  # typical broadcast window

    def fmt(dt):
        return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    holder, opponent = next_game["team"], next_game["opponent"]
    if next_game.get("neutral"):
        title = f"{holder} vs. {opponent} \u2014 belt on the line"
    elif next_game.get("is_home"):
        title = f"{opponent} at {holder} \u2014 belt on the line"
    else:
        title = f"{holder} at {opponent} \u2014 belt on the line"

    loc_bits = [b for b in (next_game.get("venue_name"), next_game.get("venue_city"),
                             next_game.get("venue_state")) if b]
    location = ", ".join(loc_bits)
    details = (f"The College Football Belt is on the line: {holder} defends against "
               f"{opponent}. Full preview: {SITE_URL}/preview.html")

    gcal_url = "https://www.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": title, "dates": f"{fmt(start)}/{fmt(end)}",
        "details": details, "location": location,
    })

    ics_lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//College Football Belt//collegefootballbelt.com//EN",
        "BEGIN:VEVENT",
        f"UID:{fmt(start)}-{team_slug(holder)}-{team_slug(opponent)}@collegefootballbelt.com",
        f"DTSTAMP:{fmt(datetime.now(timezone.utc))}",
        f"DTSTART:{fmt(start)}", f"DTEND:{fmt(end)}",
        f"SUMMARY:{ics_escape(title)}",
        f"DESCRIPTION:{ics_escape(details)}",
    ]
    if location:
        ics_lines.append(f"LOCATION:{ics_escape(location)}")
    ics_lines += ["END:VEVENT", "END:VCALENDAR"]
    ics_data_uri = "data:text/calendar;charset=utf8," + quote("\r\n".join(ics_lines))

    return f'''
  <div class="calendarLinks">
    <a class="calBtn" href="{esc(gcal_url)}" target="_blank" rel="noopener">+ Google Calendar</a>
    <a class="calBtn" href="{ics_data_uri}" download="belt-game-{team_slug(holder)}-vs-{team_slug(opponent)}.ics">&darr; Download .ics (Apple / Outlook)</a>
  </div>'''


# ---------------------------------------------------------------- headline

def build_headline(g):
    home, away = g["home"], g["away"]
    home_score, away_score = (int(x) for x in g["score"].split("-"))
    outcome = g["outcome"]
    new_holder = g["new_holder"]
    neutral = g["neutral"]
    new_holder_side = "home" if new_holder == home else "away"
    other_team = away if new_holder_side == "home" else home

    if outcome == "established":
        belt_tag = "Belt established"
        ns_score = home_score if new_holder_side == "home" else away_score
        other_score = away_score if new_holder_side == "home" else home_score
        if neutral:
            loc = f"at a neutral site over {esc(other_team)}"
        elif new_holder_side == "home":
            loc = f"at home over {esc(other_team)}"
        else:
            loc = f"on the road at {esc(other_team)}"
        headline = (f'{esc(new_holder)} <span class="win">establishes the belt</span>, '
                    f'{ns_score}–{other_score}, {loc} &mdash; the first game to '
                    f'ever have it on the line.')
        return headline, belt_tag

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


# --------------------------------------------------------------- player stats

# Skill-position categories shown by default (lower-cased CFBD category names);
# everything else CFBD returns (defense, kicking, punting, returns, etc.)
# is folded into a collapsible "view more" section instead.
SKILL_STAT_CATEGORIES = ["passing", "rushing", "receiving"]

CATEGORY_LABELS = {
    "passing": "Passing",
    "rushing": "Rushing",
    "receiving": "Receiving",
    "defensive": "Defense",
    "fumbles": "Fumbles",
    "interceptions": "Interceptions",
    "kicking": "Kicking",
    "punting": "Punting",
    "kickreturns": "Kick Returns",
    "puntreturns": "Punt Returns",
}


def category_label(name):
    return CATEGORY_LABELS.get(name.lower(), name.replace("_", " ").title())


# athlete id -> the slug of the player page that was actually written, so
# every link to a player across the site matches the file even when CFBD
# spells the name differently from one box score to the next
PLAYER_SLUG_BY_ID = {}


def player_page_slug(pid, name):
    """The slug a link should use for this player: the page's own slug when
    the pages have been indexed (main() does that first), else the
    deterministic guess."""
    if PLAYER_SLUG_BY_ID:
        return PLAYER_SLUG_BY_ID.get(pid)
    return player_slug(pid, name)


def _player_link(row):
    """A player's name, linked to their own page when CFBD gave us a
    stable athlete id for them (see fetch_game_details.py), plain text
    otherwise -- e.g. a game whose box score was cached before player_id
    started being captured, until that season's stats get refetched."""
    name = row.get("player", "")
    pid = row.get("player_id")
    slug = player_page_slug(pid, name) if pid else None
    if not slug:
        return esc(name)
    return f'<a href="../players/{esc(slug)}.html">{esc(name)}</a>'


def _team_stat_table(columns, rows):
    if not rows:
        return '<p class="noStatsForTeam">No recorded stats in this category.</p>'
    head_cells = "".join(f"<th>{esc(c)}</th>" for c in columns)
    body_rows = ""
    for r in rows:
        stats = r.get("stats", {})
        cells = ""
        for c in columns:
            val = stats.get(c)
            cells += f'<td class="tabular">{"—" if val is None else esc(str(val))}</td>'
        body_rows += f'<tr><td class="teamCell">{_player_link(r)}</td>{cells}</tr>'
    return f'''
      <table class="playerStatsTable">
        <thead><tr><th class="teamCell">Player</th>{head_cells}</tr></thead>
        <tbody>{body_rows}</tbody>
      </table>'''


def render_stat_table(cat_name, cat_data, home_team, away_team):
    columns = cat_data.get("columns", [])
    rows = cat_data.get("rows", [])
    if not columns or not rows:
        return ""

    home_rows = [r for r in rows if r.get("team") == home_team]
    away_rows = [r for r in rows if r.get("team") == away_team]
    # Anything that matched neither team name exactly -- shouldn't normally
    # happen, but CFBD team-name drift (a mid-season rename, a mascot
    # change) is a real thing -- still gets shown rather than silently
    # dropped, folded in with the away side.
    leftover = [r for r in rows if r.get("team") not in (home_team, away_team)]
    away_rows += leftover

    return f'''
    <div class="statCategory">
      <h3>{esc(category_label(cat_name))}</h3>
      <div class="statCategoryTeams">
        <div class="statTeamBlock">
          <h4 class="statTeamName">{esc(home_team)}</h4>
          {_team_stat_table(columns, home_rows)}
        </div>
        <div class="statTeamBlock">
          <h4 class="statTeamName">{esc(away_team)}</h4>
          {_team_stat_table(columns, away_rows)}
        </div>
      </div>
    </div>'''


def render_player_stats(g):
    ps = g.get("player_stats")
    if not ps:
        return ""
    home = g["home"]
    away = g["away"]

    skill_keys = [k for k in ps if k.lower() in SKILL_STAT_CATEGORIES]
    skill_keys.sort(key=lambda k: SKILL_STAT_CATEGORIES.index(k.lower()))
    other_keys = [k for k in ps if k.lower() not in SKILL_STAT_CATEGORIES]

    skill_html = "".join(render_stat_table(k, ps[k], home, away) for k in skill_keys)
    more_html = "".join(render_stat_table(k, ps[k], home, away) for k in other_keys)

    if not skill_html and not more_html:
        return ""

    more_block = ""
    if more_html:
        n = len(other_keys)
        label = f"Show more stats ({n} more categor{'y' if n == 1 else 'ies'})"
        more_block = f'''
    <details class="moreStats">
      <summary>{esc(label)}</summary>
      {more_html}
    </details>'''

    if not skill_html:
        skill_html = ('<p class="noteBox">No passing, rushing, or receiving stats recorded '
                       'for this game — see below for what CFBD does have.</p>')

    return f'''
  <section>
    <div class="sectionHead withTag">
      <span class="tag">Box Score</span>
      <span class="rule"></span>
      <h2>Player stats</h2>
      <span class="sourceTag">CFBD player stats</span>
    </div>
    {skill_html}
    {more_block}
    <p class="noteBox">Full player stats from CFBD’s <span class="mono">/games/players</span> data, split out by team. This section only appears for belt games from 2003 onward — CFBD doesn’t have player-level stats that far back. Linked player names go to that player’s stat line in every other belt game they’ve appeared in.</p>
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
        loss and a tie, since a tie means the holder keeps the belt).
        The very first belt game has no defending holder at all (holder is
        None) -- there was no belt yet for anyone to defend."""
        if g["holder"] is None:
            return "First Champion" if g["new_holder"] == team else "First Opponent"
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
            return f'<span class="disabled {cls}">{text}</span>'
        matchup = f"{esc(game['away'])} at {esc(game['home'])}"
        arrow = "&larr; " if arrow_first else " &rarr;"
        text = f"{arrow}{matchup}" if arrow_first else f"{matchup}{arrow}"
        return f'<a class="{cls}" href="{game["game_id"]}.html">{text}</a>'

    game_nav = (f'<div class="gameNav">{nav_link(prev_game, "prev", True)}'
                f'{nav_link(next_game, "next", False)}</div>')

    crumb = (f'<a href="../index.html">Belt</a> <span class="sep">/</span> '
             f'<a href="../season-{g["season"]}.html">{g["season"]} season</a> <span class="sep">/</span> '
             f'<a href="../reigns/{g["reign_number"]}.html">Reign #{g["reign_number"]}</a> &middot; Game {g["game_number"]:,} of {total_games:,}')
    footer_note = ("Part of the lineage since 1869. Score"
                   + (" and line score" if g.get("line_score") else "")
                   + " sourced from the College Football Data API.")

    body = f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>{title}</title>
<link rel="stylesheet" href="../styles.css?v={STYLES_VERSION}">
{head_extras('../')}
{game_json_ld(g, home, away, home_score, away_score)}
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

{site_header('../', None, crumb=crumb)}

<main class="wrap">
  <div class="gameMeta">
    {' '.join(meta_bits)}
  </div>
  <h1 class="matchup">{headline}</h1>

  <div class="scoreboard">
    <div class="teamPanel home">
      <div class="panelTop">
        <span class="side">Home &middot; {home_defender}</span>
        {logo_chip(colors, home, 30)}
      </div>
      <span class="name">{esc(home)}</span>
      <span class="pts tabular">{home_score}</span>
    </div>
    <div class="vs">AT</div>
    <div class="teamPanel away">
      <div class="panelTop">
        <span class="side">Away &middot; {away_defender}</span>
        {logo_chip(colors, away, 30)}
      </div>
      <span class="name">{esc(away)}</span>
      <span class="pts tabular">{away_score}</span>
    </div>
  </div>
{render_recap(g)}
{render_line_score(g)}
{render_team_stats(g)}
{render_key_plays(g)}
{render_player_stats(g)}
{game_nav}
</main>

{site_footer('../', footer_note)}
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


def render_on_this_day(belt_games, today):
    """Belt games that happened on this exact month+day in a past year --
    free, computed entirely from data already on hand. One half of the
    homepage's two-up row, so it always renders: on a date with no belt game
    in 150+ years it says so and points at the full On This Day page instead
    of leaving a hole in the layout."""
    matches = [g for g in belt_games
               if date.fromisoformat(g["date"]).month == today.month
               and date.fromisoformat(g["date"]).day == today.day
               and date.fromisoformat(g["date"]) != today]
    matches.sort(key=lambda g: g["date"], reverse=True)

    rows = ""
    for g in matches[:4]:
        year = g["date"][:4]
        h, a = (int(x) for x in g["score"].split("-"))
        if g["outcome"] in ("changed", "established"):
            tag = '<span class="otdTag changed">Changed hands</span>'
        elif h == a:
            tag = '<span class="otdTag">Tie, holder kept it</span>'
        else:
            tag = '<span class="otdTag">Defended</span>'
        loc_word = "vs." if g["neutral"] else "at"
        winner = g["new_holder"] if h != a else None
        away_html = f"<strong>{esc(g['away'])}</strong>" if winner == g["away"] else esc(g["away"])
        home_html = f"<strong>{esc(g['home'])}</strong>" if winner == g["home"] else esc(g["home"])
        rows += f'''
      <a class="otdRow" href="games/{g["game_id"]}.html">
        <span class="otdYear tabular">{year}</span>
        <span class="otdMatchup">{away_html} {loc_word} {home_html} <span class="tabular">{a}&ndash;{h}</span></span>
        {tag}
      </a>'''

    n = len(matches)
    count_txt = f"{n} belt game{'s' if n != 1 else ''} since 1869" if n else "Never, in 150+ years"
    if rows:
        body = f'<div class="otdList">{rows}\n    </div>'
    else:
        body = (f'<p class="lede" style="margin-top:10px">The belt has never once been on the line on '
                f'{esc(fmt_month_day(today))}. Every other date is a click away.</p>')
    return f'''
  <section id="onthisday">
    <div class="sectionHead">
      <span class="tag">{esc(fmt_month_day(today))}</span>
      <h2>On this day</h2>
      <span class="sectionLink">{count_txt}</span>
    </div>
    {body}
    <p class="moreLink"><a href="on-this-day.html">Browse another date &rarr;</a></p>
  </section>'''


def generate_on_this_day_page(belt_games, reigns):
    """Standalone version of the homepage's "On this day" widget -- every
    belt game ever, tagged with its month/day, filtered entirely
    client-side against the VISITOR's own local date (not the build
    server's), with a month/day picker to browse any other date in belt
    history. Same data as everywhere else on the site, just reshaped --
    no new fetches.

    Also embeds "Who held the belt when you were born?" -- wishlist item
    #6, 2026-09-16, Bob: "A date picker on the On This Day page that
    answers this plus 'and it's changed hands 41 times since.' Cheap to
    build, very shareable, and it's a natural gift from the map-scrubber
    data you already have." That data is `reigns` -- every reign already
    covers a continuous start_date/end_date span, so "who held it on date
    D" is just finding the one reign whose span contains D, and "changed
    hands N times since" is just counting how many reigns started after
    D. The full reigns list is tiny (a few hundred rows, {{team,start,end}}
    each) next to belt_games' 1,600+, so it's embedded whole -- an exact
    answer for ANY date typed in, computed instantly in the browser, no
    server round-trip."""
    reigns_payload = [{"team": r["team"], "start": r["start_date"], "end": r.get("end_date")}
                       for r in reigns]
    first_date = reigns[0]["start_date"] if reigns else None
    rows = ""
    for g in sorted(belt_games, key=lambda g: g["date"], reverse=True):
        d = date.fromisoformat(g["date"])
        year = g["date"][:4]
        h, a = (int(x) for x in g["score"].split("-"))
        if g["outcome"] in ("changed", "established"):
            tag = '<span class="otdTag changed">Belt changed hands</span>'
        else:
            tag = '<span class="otdTag">Title defended</span>'
        loc_word = "vs." if g["neutral"] else "at"
        rows += f'''
      <a class="otdRow" data-month="{d.month}" data-day="{d.day}" href="games/{g["game_id"]}.html">
        <span class="otdYear tabular">{year}</span>
        <span class="otdMatchup">{esc(g["away"])} {loc_word} {esc(g["home"])} <span class="tabular">{a}&ndash;{h}</span></span>
        {tag}
      </a>'''

    month_options = "".join(f'<option value="{i}">{name}</option>' for i, name in enumerate(MONTH_NAMES, 1))
    day_options = "".join(f'<option value="{d}">{d}</option>' for d in range(1, 32))

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>On This Day — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'on-this-day')}

<main class="wrap">
  <p class="kicker pageKicker">Belt history by date</p>
  <h1 class="pageTitle">On This Day</h1>

  <div class="sectionHead">
    <span class="tag">Just For You</span>
    <span class="rule"></span>
    <h2>Who Held the Belt When You Were Born?</h2>
  </div>
  <div class="birthdayPicker">
    <input type="date" id="birthdayInput" aria-label="Your birth date" min="{esc(first_date or '1869-11-06')}">
    <button type="button" id="birthdayGo">Find out</button>
  </div>
  <div class="birthdayResult" id="birthdayResult" hidden></div>

  <div class="sectionHead">
    <span class="tag">Browse</span>
    <span class="rule"></span>
    <h2>Every Game On a Date</h2>
  </div>
  <p class="lede" id="otdLede">Every belt game that&rsquo;s ever happened on this date, across
    all 158 years of belt history.</p>

  <div class="historyTop">
    <div class="controls">
      <div class="dateSelect">
        <select id="monthSelect" aria-label="Month">{month_options}</select>
        <select id="daySelect" aria-label="Day">{day_options}</select>
      </div>
      <button type="button" class="todayBtn" id="todayBtn">Jump to today</button>
    </div>
  </div>

  <div class="otdList" id="otdList">{rows}
  </div>
  <p class="noResults" id="noResults">No belt games have ever landed on this date &mdash; the
    season only runs August&ndash;January, so a lot of the calendar is quiet.</p>
</main>

{site_footer('', 'Every belt game computed from the College Football Data API.')}

<script type="application/json" id="birthdayReigns">{json.dumps(reigns_payload, ensure_ascii=False)}</script>
<script>
(function(){{
  var reigns = JSON.parse(document.getElementById('birthdayReigns').textContent);
  var input = document.getElementById('birthdayInput');
  var goBtn = document.getElementById('birthdayGo');
  var resultEl = document.getElementById('birthdayResult');

  function fmtDate(iso) {{
    var d = new Date(iso + 'T00:00:00Z');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, {{ year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC' }});
  }}

  function lookup() {{
    var picked = input.value;  // 'YYYY-MM-DD' or ''
    resultEl.hidden = false;
    if (!picked) {{
      resultEl.innerHTML = '<p>Pick a date above to see who held the belt that day.</p>';
      return;
    }}
    if (reigns.length && picked < reigns[0].start) {{
      resultEl.innerHTML = '<p>The belt didn&rsquo;t exist yet on ' + fmtDate(picked) +
        ' &mdash; it wasn&rsquo;t put up until ' + fmtDate(reigns[0].start) + '.</p>';
      return;
    }}
    var todayIso = new Date().toISOString().slice(0, 10);
    if (picked > todayIso) {{
      resultEl.innerHTML = '<p>That date hasn&rsquo;t happened yet &mdash; check back once it has.</p>';
      return;
    }}
    var match = null;
    for (var i = 0; i < reigns.length; i++) {{
      var r = reigns[i];
      if (r.start <= picked && (!r.end || picked < r.end)) {{ match = r; break; }}
    }}
    if (!match) {{
      resultEl.innerHTML = '<p>Couldn&rsquo;t place that date in belt history &mdash; try another.</p>';
      return;
    }}
    var changesSince = 0;
    for (var j = 0; j < reigns.length; j++) {{
      if (reigns[j].start > picked) changesSince++;
    }}
    var heldText = match.end
      ? ('held it from ' + fmtDate(match.start) + ' until losing it on ' + fmtDate(match.end))
      : ('has held it since ' + fmtDate(match.start) + ' &mdash; and holds it right now');
    resultEl.innerHTML = '<p class="birthdayAnswer"><strong>' + match.team + '</strong> held the belt on ' +
      fmtDate(picked) + '.</p><p>' + match.team + ' ' + heldText + '. The belt has changed hands ' +
      changesSince + (changesSince === 1 ? ' time' : ' times') + ' since ' + fmtDate(picked) + '.</p>';
  }}

  goBtn.addEventListener('click', lookup);
  input.addEventListener('keydown', function(e){{ if (e.key === 'Enter') lookup(); }});
}})();
</script>

<script>
(function(){{
  var monthSelect = document.getElementById('monthSelect');
  var daySelect = document.getElementById('daySelect');
  var todayBtn = document.getElementById('todayBtn');
  var rows = Array.prototype.slice.call(document.querySelectorAll('.otdRow'));
  var noResults = document.getElementById('noResults');
  var lede = document.getElementById('otdLede');
  var MONTHS = {json.dumps(MONTH_NAMES)};

  function applyFilter(){{
    var m = parseInt(monthSelect.value, 10);
    var d = parseInt(daySelect.value, 10);
    var shown = 0;
    rows.forEach(function(r){{
      var match = parseInt(r.getAttribute('data-month'), 10) === m && parseInt(r.getAttribute('data-day'), 10) === d;
      r.classList.toggle('hiddenRow', !match);
      if (match) shown++;
    }});
    noResults.style.display = shown === 0 ? 'block' : 'none';
    lede.textContent = shown + (shown === 1 ? ' belt game has' : ' belt games have') +
      ' happened on ' + MONTHS[m - 1] + ' ' + d + ' since 1869.';
  }}

  monthSelect.addEventListener('change', applyFilter);
  daySelect.addEventListener('change', applyFilter);
  todayBtn.addEventListener('click', function(){{
    var now = new Date();
    monthSelect.value = String(now.getMonth() + 1);
    daySelect.value = String(now.getDate());
    applyFilter();
  }});

  var now = new Date();
  monthSelect.value = String(now.getMonth() + 1);
  daySelect.value = String(now.getDate());
  applyFilter();
}})();
</script>
'''


def generate_homepage(lineage, colors, belt_games, next_game=None, upcoming_games=None, belt_risk=None, gameday=None):
    """The homepage (2026-09-16 redesign): the holder's colors paint a
    full-bleed hero with one call to action (the next belt game), the chain
    of custody is a real timeline, and the long tail of pages lives in an
    Explore section instead of a 15-link header."""
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
    team_reign_num = sum(1 for r in reigns if r["team"] == holder)
    years_span = today.year - 1869 + 1
    holder_url = f"teams/{team_slug(holder)}.html"

    won_score, lost_score = _reign_win_score(current, change_index)
    won_from = current.get("won_from")
    if won_from and won_score is not None:
        lede = (f"Took the belt from {esc(won_from)}, {won_score}–{lost_score}, "
                f"on {fmt_date(current['start_date'])}.")
        share_lede = (f"Won the belt from {won_from}, {won_score}–{lost_score}, "
                      f"on {fmt_date(current['start_date'])}.")
    else:
        lede = share_lede = f"Holds the belt since {fmt_date(current['start_date'])}."
    if defenses:
        words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}
        lede += f" {words.get(defenses, defenses)} defense{'s' if defenses != 1 else ''} since."
        share_lede += f" {defenses} defense{'s' if defenses != 1 else ''} since."

    # ---- game-day mode (wishlist #3): a live score + SAFE/IN DANGER banner
    # while fetch_gameday_status.py sees the holder's game in progress ----
    gameday_html = ""
    if gameday and gameday.get("status") == "in_progress" and gameday.get("holder") == holder:
        hs = gameday.get("holder_score")
        opp_score = gameday.get("opponent_score")
        opp = gameday.get("opponent") or ""
        safe = gameday.get("safe")
        state_class = "gamedaySafe" if safe else "gamedayDanger"
        state_txt = "SAFE" if safe else "IN DANGER"
        clock_bits = []
        if gameday.get("period"):
            clock_bits.append(f'Q{gameday["period"]}')
        if gameday.get("clock"):
            clock_bits.append(gameday["clock"])
        clock_txt = f' &middot; {" ".join(clock_bits)}' if clock_bits else ""
        if hs is not None and opp_score is not None:
            score_txt = f'{esc(holder)} {hs}&ndash;{opp_score} {esc(opp)}'
        else:
            score_txt = f'{esc(holder)} vs. {esc(opp)}'
        gameday_html = f'''
      <a class="gamedayBanner {state_class}" href="preview.html">
        <span class="gamedayTag">Live</span>
        <span class="gamedayScore">{score_txt}</span>
        <span class="gamedayClock">{clock_txt}</span>
        <span class="gamedayState">{state_txt}</span>
      </a>'''

    # ---- the Up Next card: the one call to action on the page ----
    if next_game and next_game.get("date"):
        opponent = next_game["opponent"]
        is_neutral = bool(next_game.get("neutral"))
        is_home = bool(next_game.get("is_home"))
        vs_word = "vs" if (is_home or is_neutral) else "at"
        soon = ""
        try:
            game_date = date.fromisoformat(next_game["date"])
            days_until = (game_date - today).days
            if days_until == 0:
                soon = "Today"
            elif days_until == 1:
                soon = "Tomorrow"
            elif days_until > 1:
                soon = f"In {days_until} days"
        except ValueError:
            game_date = None
        # next_game["date"] is already the venue-local calendar date (see
        # build_lineage.py), so the weekday comes from it -- not from the UTC
        # kickoff, which can roll a Saturday-night game into "Sun".
        when_bits = []
        if game_date is not None:
            when_bits.append(f"{game_date:%a %b} {game_date.day}")
        else:
            when_bits.append(fmt_date(next_game["date"]))
        where = next_game.get("venue_city") or next_game.get("venue_name")
        if where:
            when_bits.append(esc(where) + (" (neutral site)" if is_neutral else ""))
        when_txt = " &middot; ".join(when_bits)
        odds_html = ""
        if belt_risk and belt_risk.get("next_game", {}).get("opponent") == opponent:
            defend_prob = belt_risk["next_game"].get("defend_prob")
            holds_prob = belt_risk.get("season", {}).get("holds_into_offseason_prob")
            bits = []
            if defend_prob is not None:
                bits.append(f'{round(defend_prob * 100)}% to defend')
            if holds_prob is not None:
                has_outlook = bool(belt_risk.get("season", {}).get("end_of_season"))
                hold_txt = f'{round(holds_prob * 100)}% to hold into the offseason'
                bits.append(f'<a href="outlook.html">{hold_txt}</a>' if has_outlook else hold_txt)
            if bits:
                odds_html = f'<p class="upNextOdds">{" &middot; ".join(bits)}</p>'
        # a short lookahead past the very next game ("Belt Watch"), same free
        # schedule data -- only when there's more than one upcoming game
        watch_html = ""
        later_games = (upcoming_games or [])[1:3]
        if later_games:
            rows = ""
            for g in later_games:
                loc = "vs" if (g.get("is_home") or g.get("neutral")) else "at"
                rows += (f'<a class="watchRow" href="season-{esc(g.get("season", today.year))}.html">'
                         f'<span><span class="kicker">Belt watch</span>{loc} <strong>{esc(g["opponent"])}</strong></span>'
                         f'<span class="when">{fmt_month_day(date.fromisoformat(g["date"]))}</span></a>')
            watch_html = f'<div class="beltWatch">{rows}</div>'
        holder_chip = logo_chip(colors, holder, 40)
        opp_chip = logo_chip(colors, opponent, 40)
        up_next_html = f'''
      <aside class="upNext" aria-label="Next belt game">
        <div class="upNextHead"><span class="kicker">Belt on the line</span>{f'<span class="soonChip">{soon}</span>' if soon else ''}</div>
        <div class="upNextMatch">
          {holder_chip}
          <span class="vs">{vs_word}</span>
          {opp_chip}
          <div class="upNextWho"><span class="team">{esc(opponent)}</span><span class="when">{when_txt}</span></div>
        </div>
        {odds_html}
        <div class="btnRow">
          <a class="btn grow" href="preview.html">Read the preview</a>
          <a class="btn ghost" href="preview.html#calendar">+ Calendar</a>
        </div>
        {watch_html}
      </aside>'''
    else:
        up_next_html = '''
      <aside class="upNext" aria-label="Next belt game">
        <div class="upNextHead"><span class="kicker">Belt on the line</span></div>
        <p class="lede" style="margin:0">The holder&rsquo;s next game isn&rsquo;t on the schedule yet. The belt waits.</p>
        <div class="btnRow"><a class="btn ghost" href="seasons.html">Season by season</a><a class="btn ghost" href="my-team.html">My team&rsquo;s path</a></div>
      </aside>'''

    # ---- chain of custody: the last CHAIN_LEN reigns as a timeline ----
    chain_reigns = reigns[-CHAIN_LEN:]
    hidden_count = len(reigns) - len(chain_reigns)
    items_html = ""
    for r in chain_reigns:
        is_current = r is current
        w, l = _reign_win_score(r, change_index)
        beat = f"def. {esc(r['won_from'])} {w}–{l}" if (r.get("won_from") and w is not None) else "Established the belt"
        win_game = change_index.get((r["start_date"], r["team"]))
        href = f'games/{win_game["game_id"]}.html' if win_game else f'teams/{team_slug(r["team"])}.html'
        start = date.fromisoformat(r["start_date"])
        if is_current:
            meta = f"Holding &middot; {days_held:,} days"
        else:
            d = r.get("defenses", 0)
            meta = f"{fmt_month_day(start)}, {start.year} &middot; {d} def."
        items_html += f'''
        <a class="tlItem{" current" if is_current else ""}" href="{href}">
          {team_dot(colors, r["team"], 40)}
          <span class="tlText"><span class="tlRow"><span class="tlTeam">{esc(r["team"])}</span></span><span class="tlBeat">{beat}</span><span class="tlMeta">{meta}</span></span>
        </a>'''
    earlier_html = ""
    if hidden_count > 0:
        earlier_html = (f'<div class="tlEarlierRow"><a class="tlEarlier" href="lineage.html">&uarr; {hidden_count:,} earlier '
                        f'reign{"s" if hidden_count != 1 else ""} since 1869</a></div>')

    share_desc = esc(f"{share_lede} {years_span} years, {totals.get('reigns', '')} reigns.".strip())
    holder_kicker = f'Current holder &middot; {ordinal(team_reign_num)} reign'
    thesis = ("A lineal title passed hand to hand, on the field, since Rutgers beat Princeton 6&ndash;4 on "
              "November&nbsp;6, 1869. No committee, no poll &mdash; you have to take it from whoever&rsquo;s holding it.")
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>The College Football Belt</title>
<meta name="description" content="{share_desc}">
<meta property="og:title" content="The College Football Belt">
<meta property="og:description" content="{share_desc}">
<meta property="og:image" content="{SITE_URL}/share.png">
<meta property="og:url" content="{SITE_URL}/">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="The College Football Belt">
<meta name="twitter:description" content="{share_desc}">
<meta name="twitter:image" content="{SITE_URL}/share.png">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}
<style>
  :root{{
    --holder:{primary}; --holder-alt:{accent}; --holder-ink:{ink};
  }}
</style>

{site_header('', 'home')}

<main>
  <section class="heroPlate" aria-labelledby="holderName">
    <div class="wrap heroGrid">
      <div class="heroCopy">
        {gameday_html}
        <p class="heroKicker"><a href="{holder_url}">{holder_kicker}</a></p>
        <h1 class="heroName" id="holderName"><a href="{holder_url}">{esc(holder)}</a></h1>
        <p class="heroLede">{lede}</p>
        <div class="heroStats">
          <div><span class="n tabular">{days_held:,}</span><span class="l">Days held</span></div>
          <div><span class="n tabular">{defenses}</span><span class="l">Defense{"s" if defenses != 1 else ""}</span></div>
          <div><span class="n tabular">{team_reign_num}</span><span class="l"><a href="{holder_url}">Career reign{"s" if team_reign_num != 1 else ""}</a></span></div>
        </div>
      </div>
      {up_next_html}
    </div>
  </section>

  <div class="wrap">
    <div class="thesis">
      <p>{thesis}</p>
      <a class="sectionLink" href="ruleset.html">How the belt works &rarr;</a>
    </div>
  </div>

  <section class="wrap" id="lineage">
    <div class="sectionHead">
      <span class="tag">Chain of custody</span>
      <h2>How the belt got here</h2>
      <a class="sectionLink" href="lineage.html">All {len(reigns)} reigns &rarr;</a>
    </div>
    <div class="timeline">
      {earlier_html}
      <div class="timelineGrid">{items_html}
      </div>
    </div>
  </section>

  <div class="wrap twoUp">
{render_on_this_day(belt_games, today)}
    <section id="ruleset">
      <div class="sectionHead">
        <span class="tag">The ruleset</span>
        <h2>Four rules. No asterisks.</h2>
      </div>
      <ul class="rulesList">
        <li><h3>Won on the field</h3><p>Beat the holder, take the belt. Every other result leaves it exactly where it was.</p></li>
        <li><h3>Ties: holder retains</h3><p>Standard lineal convention. A tie isn&rsquo;t a loss, so it isn&rsquo;t treated like one.</p></li>
        <li><h3>Idle holder, belt carries</h3><p>A bye, a canceled season, a bowl opt-out &mdash; the belt just waits for the next game.</p></li>
        <li><h3>Computed, not researched</h3><p>Every reign is derived mechanically from the full game record &mdash; no editorial judgment per game.</p></li>
      </ul>
      <p class="moreLink"><a href="ruleset.html">Full ruleset, with sourcing notes &rarr;</a></p>
    </section>
  </div>

  <section id="numbers" class="band">
    <div class="wrap">
      <div class="bandGrid">
        <div><div class="n tabular">{totals["belt_games"]:,}</div><div class="l">Belt games</div></div>
        <div><div class="n tabular">{totals["reigns"]:,}</div><div class="l">Reigns</div></div>
        <div><div class="n tabular">{totals["distinct_teams"]:,}</div><div class="l">Programs</div></div>
        <div><div class="n tabular">{years_span}</div><div class="l">Years, 1869&ndash;present</div></div>
      </div>
    </div>
  </section>

  <section class="wrap" id="explore">
    <div class="sectionHead">
      <span class="tag">Go deeper</span>
      <h2>Explore the belt</h2>
    </div>
    <div class="explore">
      <a class="exploreCard" href="map.html">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 18 C7 18 7 6 12 6 C17 6 17 18 21 18"/><circle cx="3" cy="18" r="1.5"/><circle cx="21" cy="18" r="1.5"/></svg>
        <h3>Animated map</h3><p>Scrub through {years_span} years of the belt&rsquo;s journey, state by state.</p>
      </a>
      <a class="exploreCard" href="records.html">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 20 V10 M10 20 V4 M16 20 V13 M22 20 V8"/></svg>
        <h3>Records</h3><p>Longest reigns, most defenses, total days held, longest droughts.</p>
      </a>
      <a class="exploreCard" href="outlook.html">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 17 L9 11 L13 15 L21 6"/><path d="M15 6 H21 V12"/></svg>
        <h3>Season outlook</h3><p>Every program&rsquo;s odds of holding the belt when the season ends, from the remaining schedule.</p>
      </a>
      <a class="exploreCard" href="my-team.html">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 6h16M4 12h10M4 18h6"/><path d="M17 15l3 3 4-5" transform="translate(-4 -1)"/></svg>
        <h3>My Team</h3><p>Pick your program and see when you could get a shot at the belt.</p>
      </a>
      <a class="exploreCard" href="stories.html">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 6 H14 M4 12 H20 M4 18 H11"/></svg>
        <h3>Stories</h3><p>{len(STORIES)} long-form pieces computed live from the lineage &mdash; they can&rsquo;t go stale.</p>
      </a>
      <a class="exploreCard" href="daily.html">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/><path d="M9 15l2 2 4-4"/></svg>
        <h3>The Daily Belt</h3><p>One former holder, six guesses, a new clue after every miss. Same puzzle for everyone each day.</p>
      </a>
      <a class="exploreCard" href="timeline.html">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 6h7M12 6h9M3 12h4M9 12h12M3 18h11M16 18h5"/></svg>
        <h3>Timeline</h3><p>All {totals["reigns"]:,} reigns as one strip of colored bars, a row per decade.</p>
      </a>
      <a class="exploreCard" href="rivalries/index.html">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 4l16 16M20 4L4 20"/><circle cx="12" cy="12" r="3"/></svg>
        <h3>Rivalries</h3><p>The pairs that keep meeting with the belt on the line, and who leads.</p>
      </a>
    </div>
    <div class="chipRow">
      <a class="chipLink" href="all-games.html">All {totals["belt_games"]:,} games</a>
      <a class="chipLink" href="seasons.html">Season by season</a>
      <a class="chipLink" href="reigns/{totals["reigns"]}.html">The current reign</a>
      <a class="chipLink" href="leaders.html">Belt-game leaders</a>
      <a class="chipLink" href="heartbreak.html">Heartbreak list</a>
      <a class="chipLink" href="states/index.html">States</a>
      <a class="chipLink" href="decades/index.html">Decades</a>
      <a class="chipLink" href="conferences/index.html">Conference belts</a>
      <a class="chipLink" href="lean.html">The lean&rsquo;s ledger</a>
      <a class="chipLink" href="compare.html">Compare teams</a>
      <a class="chipLink" href="trivia.html">Trivia</a>
      <a class="chipLink" href="defend-or-dethrone.html">Defend or Dethrone</a>
      <a class="chipLink" href="losers-belt.html">The Losers Belt</a>
      <a class="chipLink" href="about.html">About</a>
      <a class="chipLink" href="api.html">API</a>
    </div>
  </section>

  <div class="wrap">
    <div class="followStrip" id="alerts">
      <div>
        <h2>Know the moment it changes hands</h2>
        <p>Title changes only &mdash; no weekly noise.</p>
      </div>
      <div class="btnRow">
        <a class="btn" href="https://blogtrottr.com/?subscribe={SITE_URL}/feed.xml" target="_blank" rel="noopener">Email alerts</a>
        <a class="btn ghost" href="feed.xml">RSS</a>
        <a class="btn ghost" href="https://x.com/CollegeFBBelt" target="_blank" rel="noopener">@CollegeFBBelt</a>
      </div>
      <details>
        <summary>How email alerts work</summary>
        <p style="margin-top:10px">Every time the belt changes hands it hits the feed below the moment the site rebuilds.
          Blogtrottr (or any RSS-to-email service) emails you when it happens &mdash; nothing to sign up for here, no account needed on this end.</p>
        <div class="feedUrlBox"><code class="feedUrlText">{SITE_URL}/feed.xml</code><a class="btn ghost" style="min-height:36px" href="feed.xml">View feed</a></div>
      </details>
    </div>
  </div>
</main>

{site_footer('', 'Colors on this page are the current holder&rsquo;s &mdash; the site recolors itself with every change of hands.')}
'''


# ---------------------------------------------------------- full history page

# Championship-belt scope switcher (Combined/FBS-only/FCS-only), same
# pattern as LOSERS_BELT_FILENAMES/LOSERS_BELT_SWITCHER_LABELS above --
# "combined" keeps the original unsuffixed lineage.html/all-games.html
# URLs. Applies only to these two pages (Full History, All Games), per
# Bob's 2026-09-15 request -- the rest of the site (homepage, records,
# team pages, etc.) stays on the combined/real belt, unchanged.
CHAMPIONSHIP_LINEAGE_FILENAMES = {"combined": "lineage.html", "fbs": "lineage_fbs.html",
                                   "fcs": "lineage_fcs.html"}
CHAMPIONSHIP_ALL_GAMES_FILENAMES = {"combined": "all-games.html", "fbs": "all-games_fbs.html",
                                     "fcs": "all-games_fcs.html"}
CHAMPIONSHIP_SWITCHER_LABELS = {"combined": "Combined (No Restriction)", "fbs": "FBS Only",
                                 "fcs": "FCS Only"}
CHAMPIONSHIP_SCOPE_TITLE_SUFFIX = {"combined": "", "fbs": " (FBS)", "fcs": " (FCS)"}
CHAMPIONSHIP_SCOPE_INTRO = {
    "combined": "",
    "fbs": " Restricted to FBS programs only &mdash; both sides of every game "
        "have to be FBS, so the belt can never cross down into FCS.",
    "fcs": " Restricted to FCS programs only &mdash; both sides of every game "
        "have to be FCS, so the belt can never cross up into FBS.",
}


def _championship_switcher_html(filenames, scope, available_scopes):
    if len(available_scopes) <= 1:
        return ""
    pills = []
    for s in ("combined", "fbs", "fcs"):
        if s not in available_scopes:
            continue
        label = esc(CHAMPIONSHIP_SWITCHER_LABELS[s])
        if s == scope:
            pills.append(f'<span class="scopeBtn active" aria-current="page">{label}</span>')
        else:
            pills.append(f'<a class="scopeBtn" href="{filenames[s]}">{label}</a>')
    return f'''
    <div class="scopeSwitch" role="group" aria-label="Which programs count" style="margin-top:10px">{"".join(pills)}</div>'''


def generate_lineage_page(lineage, colors, belt_games, scope="combined", available_scopes=("combined",)):
    """`scope` is one of build_lineage.py's SCOPES ("combined"/"fbs"/"fcs").
    Only "combined" has real games/<id>.html detail pages to link to (those
    are only ever generated from the combined lineage's own belt_games in
    main()) -- an fbs/fcs game_id may not have a page at all, so those two
    scopes render plain text instead of a link, same as the Losers Belt
    already does for every scope."""
    reigns = lineage["reigns"]
    totals = lineage["totals"]
    current = reigns[-1]
    change_index = build_change_game_index(belt_games) if scope == "combined" else {}
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
        num_html = f'<a href="reigns/{i}.html" title="Reign #{i}: every game of it">{i}</a>' if scope == "combined" else str(i)
        dur_html = fmt_duration(*reign_dates(r, today))
        if scope == "combined":
            dur_html = f'<a href="reigns/{i}.html">{dur_html}</a>'
        rows_html += f'''
        <tr class="{cls.strip()}" data-team="{esc(team.lower())}">
          <td class="num">{num_html}</td>
          <td class="teamCell"><span class="reignChip" style="background:{p}"></span>{team_html}</td>
          <td class="dates">{fmt_date(r["start_date"])} &ndash; {end_txt}</td>
          <td class="tabular">{dur_html}</td>
          <td class="tabular">{r["defenses"]}</td>
          <td class="won">{won_txt}</td>
          <td class="lost">{lost_txt}</td>
        </tr>'''

    title_suffix = CHAMPIONSHIP_SCOPE_TITLE_SUFFIX[scope]
    scope_intro = CHAMPIONSHIP_SCOPE_INTRO[scope]
    switcher_html = _championship_switcher_html(CHAMPIONSHIP_LINEAGE_FILENAMES, scope, available_scopes)
    all_games_href = CHAMPIONSHIP_ALL_GAMES_FILENAMES[scope]
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Full History{title_suffix} — The College Football Belt</title>
<meta name="description" content="Every reign of the College Football Belt{title_suffix}, the lineal college football championship, from Rutgers in 1869 to today: who won it, who they beat and how long they held it.">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'history')}

<main class="wrap">
  <p class="kicker pageKicker">Full history</p>
  <h1 class="pageTitle">The Full History{title_suffix}</h1>
  <p class="lede">Every reign since Rutgers beat Princeton on November&nbsp;6, 1869 &mdash;
    {totals["reigns"]:,} of them, computed from {totals["belt_games"]:,} belt games across
    {totals["distinct_teams"]} programs. Type a team name to filter; tap a team to jump to the
    game that won it.{scope_intro}</p>
  {switcher_html}

  <div class="historyTop">
    <div class="historyStats">
      <div><span class="n tabular">{totals["reigns"]:,}</span><span class="l">Reigns</span></div>
      <div><span class="n tabular">{totals["belt_games"]:,}</span><span class="l">Belt Games</span></div>
      <div><span class="n tabular">{totals["distinct_teams"]}</span><span class="l">Programs</span></div>
    </div>
    <div class="controls">
      <div class="sortToggle" role="group" aria-label="Sort order">
        <button type="button" class="sortBtn active" data-order="asc">Oldest First</button>
        <button type="button" class="sortBtn" data-order="desc">Newest First</button>
      </div>
      <div class="searchBox">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/></svg>
        <input id="teamSearch" type="search" placeholder="Filter by team&hellip;" aria-label="Filter by team" autocomplete="off">
      </div>
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
    not just who won each reign? <a href="{all_games_href}">See the full game log &rarr;</a></p>
</main>

{site_footer('', 'Every reign computed from the College Football Data API.')}

<script>
(function(){{
  var input = document.getElementById('teamSearch');
  var tbody = document.querySelector('table.reignsTable tbody');
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

  var STORAGE_KEY = 'cfbBelt:lineageSortOrder';
  var sortBtns = Array.prototype.slice.call(document.querySelectorAll('.sortToggle .sortBtn'));
  function applyOrder(order){{
    var ordered = order === 'desc' ? rows.slice().reverse() : rows.slice();
    ordered.forEach(function(r){{ tbody.appendChild(r); }});
    sortBtns.forEach(function(b){{ b.classList.toggle('active', b.getAttribute('data-order') === order); }});
    try {{ localStorage.setItem(STORAGE_KEY, order); }} catch(e) {{}}
  }}
  sortBtns.forEach(function(b){{
    b.addEventListener('click', function(){{ applyOrder(b.getAttribute('data-order')); }});
  }});
  var savedOrder = null;
  try {{ savedOrder = localStorage.getItem(STORAGE_KEY); }} catch(e) {{}}
  if (savedOrder === 'desc') applyOrder('desc');
}})();
</script>
'''


# Three parallel Losers Belt pages, one per build_losers_lineage.py SCOPE.
# "combined" keeps the original unsuffixed filename -- it's the page
# that's already live and indexed. Order here is also switcher order.
LOSERS_BELT_FILENAMES = {"combined": "losers-belt.html", "fbs": "losers-belt-fbs.html",
                          "fcs": "losers-belt-fcs.html"}
LOSERS_BELT_SWITCHER_LABELS = {"combined": "Combined (FBS + FCS)", "fbs": "FBS Only",
                                "fcs": "FCS Only"}

# Full chain-of-custody tables get long -- FCS-scope alone is 7,500+ reigns,
# which was slow enough to load that it prompted this pagination in the
# first place. Any scope whose reign count exceeds this gets split across
# multiple physical pages instead of one giant table, so this also quietly
# protects Combined/FBS if their histories keep growing.
LOSERS_BELT_PAGE_SIZE = 100


def losers_belt_page_filename(scope, page):
    """Page 1 keeps the scope's normal filename (so every existing link to
    losers-belt.html/-fbs.html/-fcs.html keeps working unchanged); page 2+
    gets "-2"/"-3"/... spliced in before the extension."""
    base = LOSERS_BELT_FILENAMES[scope]
    if page <= 1:
        return base
    stem = base[:-len(".html")]
    return f"{stem}-{page}.html"


def losers_belt_data_filename(scope):
    """The full-history JSON companion each Losers Belt page fetches in the
    background so search and Oldest/Newest sort can work across every page,
    not just whichever 100 rows happen to be server-rendered on the one the
    visitor landed on. One file per scope, not per page -- every page of a
    given scope shares the same full dataset."""
    base = LOSERS_BELT_FILENAMES[scope]
    return base[:-len(".html")] + "-data.json"


def build_losers_belt_rows(reigns, current, change_index, today):
    """Shared row data for a Losers Belt lineage -- used both to render each
    page's server-side <tr> markup and to build the full-history JSON that
    page fetches for client-side cross-page search/sort (see
    losers_belt_data_filename()). Kept as plain data (no HTML) here so the
    JSON side never needs escaping; _losers_belt_row_html() below is the one
    place that turns a row into markup, whether it came fresh off this
    function or back out of the JSON on the client."""
    rows = []
    for i, r in enumerate(reigns, 1):
        is_current = r is current
        team = r["team"]
        w, l = _reign_win_score(r, change_index)

        if r.get("reclaimed_after"):
            caught = f"reverted after {r['reclaimed_after']} stopped playing"
        elif r.get("won_from") and w is not None:
            caught = f"lost to {r['won_from']} {w}–{l}"
        else:
            caught = "Established it (first-ever loss)"

        passed = None
        if is_current:
            end_txt = "Present"
        elif r.get("vacated"):
            passed = "vacated — stopped playing football"
            end_txt = fmt_date(r["end_date"])
        elif r.get("lost_to"):
            passed = f"beat {r['lost_to']}"
            end_txt = fmt_date(r["end_date"])
        else:
            end_txt = fmt_date(r["end_date"]) if r.get("end_date") else "—"

        rows.append({
            "n": i,
            "team": team,
            "teamLower": team.lower(),
            "dates": f'{fmt_date(r["start_date"])} – {end_txt}',
            "len": fmt_duration(*reign_dates(r, today)),
            "losses": r["defenses"],
            "caught": caught,
            "passed": passed,
            "current": is_current,
        })
    return rows


def _losers_belt_row_html(row):
    """Render one build_losers_belt_rows() row as a <tr> -- the same markup
    generate_losers_belt_page() always produced, just sourced from the
    shared row dict instead of recomputed inline."""
    cls = " current" if row["current"] else ""
    if row["current"]:
        passed_html = '<span class="mono">— present —</span>'
    else:
        passed_html = esc(row["passed"]) if row["passed"] else "—"
    return f'''
        <tr class="{cls.strip()}" data-team="{esc(row["teamLower"])}">
          <td class="num">{row["n"]}</td>
          <td class="teamCell">{esc(row["team"])}</td>
          <td class="dates">{esc(row["dates"])}</td>
          <td class="tabular">{esc(row["len"])}</td>
          <td class="tabular">{row["losses"]}</td>
          <td class="won">{esc(row["caught"])}</td>
          <td class="lost">{passed_html}</td>
        </tr>'''


def generate_losers_belt_page(lineage, scope="combined", available_scopes=("combined",), page=1, all_rows=None):
    """The Losers Belt page -- current holder + full reign history, in the
    same spirit as generate_lineage_page() but deliberately lighter: no
    per-game detail pages exist for Losers Belt games (only the real belt
    gets those), so nothing here links out to a games/ or teams/ page --
    a losers-belt-only team may never have earned a real-belt team page,
    and this has no way to know without risking a broken link. lineage
    here is belt_data/losers_lineage*.json, same shape as lineage.json.

    `scope` is one of build_losers_lineage.py's SCOPES ("combined"/"fbs"/
    "fcs") -- which of the three independently-computed lineages this
    particular page renders. `available_scopes` is whichever of the three
    actually have data yet (each is bootstrapped independently, so e.g.
    right after this shipped only "combined" would be available) -- the
    switcher only links to scopes that are in it, so this never renders a
    link to a page that doesn't exist yet.

    The full reign table is paginated at LOSERS_BELT_PAGE_SIZE rows/page
    (some scopes -- FCS especially -- run into the thousands of reigns,
    which was slow enough to load as one giant table that it prompted this).
    `page` selects which slice of the table this call renders; everything
    ABOVE the table (hero, records, totals) always reflects the FULL
    history regardless of page, since those are all-time stats, not
    page-scoped. Call this once per page (1..total_pages, computed from
    len(reigns) and LOSERS_BELT_PAGE_SIZE) and write each result to
    losers_belt_page_filename(scope, page).
    """
    SCOPE_INTRO = {
        "combined": "This one folds FBS and FCS together, exactly how this belt "
            "has always worked here.",
        "fbs": "This one is restricted to FBS programs only &mdash; both sides of "
            "every game have to be FBS, so the belt can never cross down into FCS. "
            "This is the scope that matches the &ldquo;official&rdquo; College "
            "Football Loser's Belt that's been tracked on r/CFB since 2014 and "
            "still goes viral there today.",
        "fcs": "This one is restricted to FCS programs only &mdash; both sides of "
            "every game have to be FCS, so the belt can never cross up into FBS.",
    }
    SCOPE_TITLE_SUFFIX = {"combined": "", "fbs": " (FBS)", "fcs": " (FCS)"}
    SCOPE_META_NOTE = {
        "combined": "Combines FBS and FCS.",
        "fbs": "Restricted to FBS programs -- matches the version tracked on r/CFB since 2014.",
        "fcs": "Restricted to FCS programs.",
    }

    switcher_html = ""
    if len(available_scopes) > 1:
        pills = []
        for s in ("combined", "fbs", "fcs"):
            if s not in available_scopes:
                continue
            label = esc(LOSERS_BELT_SWITCHER_LABELS[s])
            if s == scope:
                pills.append(f'<span class="scopeBtn active" aria-current="page">{label}</span>')
            else:
                pills.append(f'<a class="scopeBtn" href="{LOSERS_BELT_FILENAMES[s]}">{label}</a>')
        switcher_html = f'''
  <div class="scopeSwitch" role="group" aria-label="Which programs count" style="margin-top:18px">{"".join(pills)}</div>'''

    reigns = lineage["reigns"]
    totals = lineage["totals"]
    current = reigns[-1]
    belt_games = lineage["belt_games"]
    change_index = build_change_game_index(belt_games)
    today = date.today()
    if all_rows is None:
        all_rows = build_losers_belt_rows(reigns, current, change_index, today)

    since_date = date.fromisoformat(current["start_date"])
    days_held = (today - since_date).days
    defenses = current["defenses"]
    won_score, lost_score = _reign_win_score(current, change_index)
    won_from = current.get("won_from")
    reclaimed_after = current.get("reclaimed_after")

    if reclaimed_after:
        lede = (f"{esc(reclaimed_after)} caught it but stopped fielding a football team "
                 f"altogether &mdash; since the belt can only pass by losing, it reverted "
                 f"back to {esc(current['team'])} on {fmt_date(current['start_date'])}.")
    elif won_from and won_score is not None:
        lede = (f"Caught it by losing to {esc(won_from)}, {won_score}&ndash;{lost_score}, "
                f"on {fmt_date(current['start_date'])}.")
    else:
        lede = f"Has held it since {fmt_date(current['start_date'])}."
    if defenses:
        lede += (f" Lost {defenses} more game{'s' if defenses != 1 else ''} since &mdash; "
                 f"still the reigning worst team in the country.")

    durations = [(r, reign_duration_days(r, today)) for r in reigns]
    longest_reign, longest_days = max(durations, key=lambda p: p[1])
    most_defended = max(reigns, key=lambda r: r["defenses"])
    reign_counts = Counter(r["team"] for r in reigns)
    most_reigns_team, most_reigns_n = reign_counts.most_common(1)[0]

    total_reigns = len(reigns)
    total_pages = max(1, math.ceil(total_reigns / LOSERS_BELT_PAGE_SIZE))
    page = max(1, min(page, total_pages))
    page_start_idx = (page - 1) * LOSERS_BELT_PAGE_SIZE
    page_reigns = reigns[page_start_idx:page_start_idx + LOSERS_BELT_PAGE_SIZE]
    range_start = page_start_idx + 1
    range_end = page_start_idx + len(page_reigns)

    def _page_href(p):
        return losers_belt_page_filename(scope, p)

    def _pager_btn(p, label, active=False, extra_cls=""):
        classes = "pagerBtn" + (" active" if active else "") + (f" {extra_cls}" if extra_cls else "")
        if active:
            return f'<span class="{classes}" aria-current="page">{label}</span>'
        return f'<a class="{classes}" href="{esc(_page_href(p))}" data-page="{p}">{label}</a>'

    pager_nav = ""
    if total_pages > 1:
        window = 2
        page_numbers = sorted(set(
            [1, total_pages] +
            [p for p in range(page - window, page + window + 1) if 1 <= p <= total_pages]))
        links = []
        if page > 1:
            links.append(_pager_btn(page - 1, "&lsaquo; Prev"))
        prev_n = None
        for n in page_numbers:
            if prev_n is not None and n - prev_n > 1:
                links.append('<span class="pagerBtn ellipsis">&hellip;</span>')
            links.append(_pager_btn(n, f"{n}", active=(n == page)))
            prev_n = n
        if page < total_pages:
            links.append(_pager_btn(page + 1, "Next &rsaquo;"))
        pager_nav = f'<nav class="reignsPager" aria-label="Reign history pages">{"".join(links)}</nav>'

    jump_html = ""
    if page != total_pages:
        jump_html = f' &middot; <a href="{esc(_page_href(total_pages))}" class="jumpLink" data-page="{total_pages}">Jump to current holder &rarr;</a>'
    elif page != 1:
        jump_html = f' &middot; <a href="{esc(_page_href(1))}" class="jumpLink" data-page="1">Jump to the beginning &rarr;</a>'

    # Rendered server-side so the page works with no JS at all (real links to
    # the real physical pages); data-losers-pager marks these two containers
    # so the script below can find and replace them once the full-history
    # JSON (losers_belt_data_filename()) has loaded, at which point paging,
    # sorting, and search all become client-side and span every reign, not
    # just whichever 100 happen to be server-rendered on this one file.
    pager_html = f'''
  <div class="pagerRow" data-losers-pager>
    <div class="pagerInfo">Reigns {range_start:,}&ndash;{range_end:,} of {total_reigns:,} &middot; page {page} of {total_pages}{jump_html}</div>
    {pager_nav}
  </div>'''
    pager_html_bottom = pager_html.replace('aria-label="Reign history pages"', 'aria-label="Reign history pages (bottom)"')

    records_html = f'''
    <div class="record-card">
      <div class="l">Longest Reign</div>
      <div class="v">{esc(longest_reign["team"])} &mdash; {fmt_duration(*reign_dates(longest_reign, today))}</div>
      <div class="sub">{fmt_date(longest_reign["start_date"])} &ndash; {fmt_date(longest_reign["end_date"]) if longest_reign.get("end_date") else "present"}</div>
    </div>
    <div class="record-card">
      <div class="l">Most Losses, One Reign</div>
      <div class="v">{esc(most_defended["team"])} &mdash; {most_defended["defenses"]}</div>
      <div class="sub">starting {fmt_date(most_defended["start_date"])}</div>
    </div>
    <div class="record-card">
      <div class="l">Most Reigns, All-Time</div>
      <div class="v">{esc(most_reigns_team)} &mdash; {most_reigns_n} separate reign{"s" if most_reigns_n != 1 else ""}</div>
      <div class="sub">caught it back {most_reigns_n - 1} time{"s" if most_reigns_n - 1 != 1 else ""} after passing it on</div>
    </div>'''

    rows_html = "".join(_losers_belt_row_html(row) for row in all_rows[page_start_idx:page_start_idx + LOSERS_BELT_PAGE_SIZE])

    title_suffix = SCOPE_TITLE_SUFFIX[scope]
    meta_note = SCOPE_META_NOTE[scope]
    page_title_suffix = f" — Page {page} of {total_pages}" if total_pages > 1 else ""
    canonical_href = _page_href(page)
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>The Losers Belt{title_suffix}{page_title_suffix} — The College Football Belt</title>
<meta name="description" content="A mirror-image lineage: the belt passes to whoever LOSES to the holder, not whoever beats them. {meta_note} Currently held by {esc(current["team"])}.{' Reign history, page ' + str(page) + ' of ' + str(total_pages) + '.' if total_pages > 1 else ''}">
<link rel="canonical" href="{SITE_URL}/{canonical_href}">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'losers')}

<main class="wrap">
  <p class="kicker pageKicker">A companion lineage</p>
  <h1 class="pageTitle">The Losers Belt{title_suffix}</h1>
  <p class="lede">The real belt passes to whoever BEATS the holder. This one is its
    mirror image: it passes to whoever LOSES to the holder &mdash; you catch it the way
    you&rsquo;d catch a cold, by losing to the team that currently has it. Lose again, and
    you keep it (you&rsquo;re still the reigning worst team in the country). Win, and
    whoever you just beat catches it from you. It starts the same place the real belt
    does: Princeton, who lost the very first college football game ever played, 6&ndash;4 to
    Rutgers on November&nbsp;6, 1869. {SCOPE_INTRO[scope]}</p>
{switcher_html}

  <h2 class="srOnly">How the Losers Belt works</h2>
  <div class="rules">
    <div class="rule-card">
      <h3>Lost to the holder? It's yours.</h3>
      <p>The holder wins a game, and the team that just lost to them catches the Losers Belt.</p>
    </div>
    <div class="rule-card">
      <h3>Lose again? Still yours.</h3>
      <p>The holder loses again, nothing changes &mdash; still the reigning worst team.</p>
    </div>
    <div class="rule-card">
      <h3>Ties: holder retains</h3>
      <p>Same convention as the real belt &mdash; a tie changes nothing either way.</p>
    </div>
    <div class="rule-card">
      <h3>Computed, not curated</h3>
      <p>Same mechanical, no-editorial-judgment approach as the real belt &mdash; just run in reverse.</p>
    </div>
  </div>

  <p class="lede" style="margin-top:18px">One wrinkle: this belt can only pass by losing, so a holder that
    stops fielding a football team at all would hold it forever under the literal rule &mdash; and a lot of
    this belt&rsquo;s holders are exactly the kind of small or historic programs that don&rsquo;t exist
    anymore. When a holder goes roughly two full seasons without playing a single game, it&rsquo;s treated
    as having discontinued football, and the belt reverts to whoever it last caught it from.</p>

  <section class="hero" style="margin-top:32px">
    <div>
      <h2 style="font-family:'Big Shoulders Display',sans-serif;font-weight:800;font-size:clamp(22px,3.4vw,30px);margin:0 0 8px">{esc(current["team"])} holds the Losers Belt.</h2>
      <p class="lede">{lede}</p>
      <div class="heroFacts">
        <div><span class="n tabular">{days_held:,}</span><span class="l">Days Held</span></div>
        <div><span class="n tabular">{defenses}</span><span class="l">Losses Since</span></div>
        <div><span class="n tabular">1869</span><span class="l">Belt Established</span></div>
      </div>
    </div>
  </section>

  <div class="historyTop" style="margin-top:36px">
    <div class="historyStats">
      <div><span class="n tabular">{totals["reigns"]:,}</span><span class="l">Reigns</span></div>
      <div><span class="n tabular">{totals["belt_games"]:,}</span><span class="l">Belt Games</span></div>
      <div><span class="n tabular">{totals["distinct_teams"]}</span><span class="l">Programs</span></div>
    </div>
    <div class="controls">
      <div class="sortToggle" role="group" aria-label="Sort order">
        <button type="button" class="sortBtn active" data-order="asc">Oldest First</button>
        <button type="button" class="sortBtn" data-order="desc">Newest First</button>
      </div>
      <div class="searchBox">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/></svg>
        <input id="teamSearch" type="search" placeholder="Filter by team&hellip;" aria-label="Filter by team" autocomplete="off">
      </div>
      <span class="pagerInfo" id="dataStatus" aria-live="polite"></span>
    </div>
  </div>

  <div class="records">{records_html}
  </div>
{pager_html}

  <div class="tableScroll">
    <table class="reignsTable">
      <thead>
        <tr>
          <th>#</th><th>Team</th><th>Reign</th><th style="text-align:right">Length</th>
          <th style="text-align:right">Losses</th><th>Caught it</th><th>Passed it on</th>
        </tr>
      </thead>
      <tbody>{rows_html}
      </tbody>
    </table>
    <p class="noResults" id="noResults">No reigns match &ldquo;<span id="noResultsTerm"></span>.&rdquo;</p>
  </div>
{pager_html_bottom}
</main>

{site_footer('', 'Every reign computed from the College Football Data API, same source as the real belt.')}

<script>
(function(){{
  var DATA_URL = {json.dumps(losers_belt_data_filename(scope))};
  var PAGE_SIZE = {LOSERS_BELT_PAGE_SIZE};
  var INITIAL_PAGE = {page};

  var searchInput = document.getElementById('teamSearch');
  var sortBtns = Array.prototype.slice.call(document.querySelectorAll('.sortToggle .sortBtn'));
  var tbody = document.querySelector('table.reignsTable tbody');
  var noResults = document.getElementById('noResults');
  var noResultsTerm = document.getElementById('noResultsTerm');
  var pagerRows = Array.prototype.slice.call(document.querySelectorAll('[data-losers-pager]'));
  var statusEl = document.getElementById('dataStatus');

  // Fallback while the full-history JSON hasn't loaded yet (or failed to):
  // the same page-scoped filter this table used before pagination existed,
  // over just the rows this one physical page server-rendered.
  var staticRows = Array.prototype.slice.call(document.querySelectorAll('table.reignsTable tbody tr'));
  function staticSearch(){{
    var q = searchInput.value.trim().toLowerCase();
    var shown = 0;
    staticRows.forEach(function(r){{
      var name = r.getAttribute('data-team') || '';
      var match = !q || name.indexOf(q) !== -1;
      r.classList.toggle('hiddenRow', !match);
      if (match) shown++;
    }});
    noResultsTerm.textContent = searchInput.value.trim();
    noResults.style.display = (shown === 0 && q) ? 'block' : 'none';
  }}

  var state = {{ rows: null, order: 'asc', query: '', page: INITIAL_PAGE }};

  function escapeHtml(s){{
    return String(s).replace(/[&<>"']/g, function(c){{
      return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c];
    }});
  }}

  function fmtNum(n){{
    try {{ return n.toLocaleString('en-US'); }} catch(e){{ return String(n); }}
  }}

  function rowHtml(row){{
    var cls = row.current ? ' current' : '';
    var passedHtml = row.current
      ? '<span class="mono">\\u2014 present \\u2014</span>'
      : escapeHtml(row.passed || '\\u2014');
    return '<tr class="' + cls.trim() + '" data-team="' + escapeHtml(row.teamLower) + '">' +
      '<td class="num">' + row.n + '</td>' +
      '<td class="teamCell">' + escapeHtml(row.team) + '</td>' +
      '<td class="dates">' + escapeHtml(row.dates) + '</td>' +
      '<td class="tabular">' + escapeHtml(row.len) + '</td>' +
      '<td class="tabular">' + row.losses + '</td>' +
      '<td class="won">' + escapeHtml(row.caught) + '</td>' +
      '<td class="lost">' + passedHtml + '</td>' +
    '</tr>';
  }}

  function pagerLink(p, label, active){{
    if (active) return '<span class="pagerBtn active" aria-current="page">' + label + '</span>';
    return '<a href="#" class="pagerBtn" data-goto="' + p + '">' + label + '</a>';
  }}

  function render(){{
    var rows = state.rows;
    if (!rows) return;
    var q = state.query.trim().toLowerCase();
    var filtered = q ? rows.filter(function(r){{ return r.teamLower.indexOf(q) !== -1; }}) : rows;
    var ordered = state.order === 'desc' ? filtered.slice().reverse() : filtered;
    var totalCount = ordered.length;
    var totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));
    var page = Math.min(Math.max(1, state.page), totalPages);
    state.page = page;
    var startIdx = (page - 1) * PAGE_SIZE;
    var pageRows = ordered.slice(startIdx, startIdx + PAGE_SIZE);

    if (totalCount === 0) {{
      tbody.innerHTML = '';
      noResultsTerm.textContent = state.query.trim();
      noResults.style.display = 'block';
    }} else {{
      noResults.style.display = 'none';
      tbody.innerHTML = pageRows.map(rowHtml).join('');
    }}

    // Where the current holder sits under this order/filter, for the jump link.
    var jumpPage = null, jumpLabel = null;
    for (var i = 0; i < ordered.length; i++) {{
      if (ordered[i].current) {{
        jumpPage = Math.floor(i / PAGE_SIZE) + 1;
        jumpLabel = state.order === 'asc' ? 'Jump to current holder' : 'Jump to the beginning';
        break;
      }}
    }}

    var rangeStart = totalCount === 0 ? 0 : startIdx + 1;
    var rangeEnd = startIdx + pageRows.length;
    var noun = q ? 'Matches' : 'Reigns';
    var infoHtml = noun + ' ' + fmtNum(rangeStart) + '\\u2013' + fmtNum(rangeEnd) + ' of ' +
      fmtNum(totalCount) + ' \\u00b7 page ' + page + ' of ' + totalPages;
    if (jumpPage !== null && jumpPage !== page) {{
      infoHtml += ' \\u00b7 <a href="#" class="jumpLink" data-goto="' + jumpPage + '">' + jumpLabel + ' \\u2192</a>';
    }}

    var navHtml = '';
    if (totalPages > 1) {{
      var winSize = 2;
      var nums = [1, totalPages];
      for (var n = page - winSize; n <= page + winSize; n++) {{ if (n >= 1 && n <= totalPages) nums.push(n); }}
      nums = nums.filter(function(v, idx){{ return nums.indexOf(v) === idx; }}).sort(function(a,b){{ return a - b; }});
      var parts = [];
      if (page > 1) parts.push(pagerLink(page - 1, '&lsaquo; Prev'));
      var prevN = null;
      nums.forEach(function(n){{
        if (prevN !== null && n - prevN > 1) parts.push('<span class="pagerBtn ellipsis">&hellip;</span>');
        parts.push(pagerLink(n, String(n), n === page));
        prevN = n;
      }});
      if (page < totalPages) parts.push(pagerLink(page + 1, 'Next &rsaquo;'));
      navHtml = '<nav class="reignsPager" aria-label="Reign history pages">' + parts.join('') + '</nav>';
    }}

    var fullHtml = '<div class="pagerInfo">' + infoHtml + '</div>' + navHtml;
    pagerRows.forEach(function(el, idx){{ el.innerHTML = idx ? fullHtml.replace('aria-label="Reign history pages"', 'aria-label="Reign history pages (bottom)"') : fullHtml; }});
  }}

  // Pager/jump links are rebuilt fresh on every render(), so bind the click
  // handler once on each stable container (event delegation) rather than on
  // the links themselves.
  pagerRows.forEach(function(el){{
    el.addEventListener('click', function(e){{
      var target = e.target.closest ? e.target.closest('[data-goto]') : null;
      if (!target) return;
      e.preventDefault();
      state.page = parseInt(target.getAttribute('data-goto'), 10) || 1;
      render();
    }});
  }});

  sortBtns.forEach(function(b){{
    b.addEventListener('click', function(){{
      state.order = b.getAttribute('data-order');
      state.page = 1;
      sortBtns.forEach(function(x){{ x.classList.toggle('active', x === b); }});
      render();
    }});
  }});

  var searchDebounce = null;
  searchInput.addEventListener('input', function(){{
    if (!state.rows) {{ staticSearch(); return; }}
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(function(){{
      state.query = searchInput.value;
      state.page = 1;
      render();
    }}, 80);
  }});

  if (statusEl) statusEl.textContent = 'Loading full history for sitewide search\\u2026';
  fetch(DATA_URL).then(function(resp){{
    if (!resp.ok) throw new Error('bad status');
    return resp.json();
  }}).then(function(data){{
    state.rows = data;
    if (statusEl) statusEl.textContent = '';
    render();
  }}).catch(function(){{
    if (statusEl) statusEl.textContent = 'Full history failed to load \\u2014 search and sort are limited to this page.';
  }});
}})();
</script>
'''


# ---------------------------------------------------------- conference belts

def generate_conference_belt_page(lineage, slug):
    """One page per FBS/FCS conference (build_conference_lineage.py) --
    current holder + full chain of custody, deliberately as lightweight as
    the Losers Belt page: no per-game detail pages exist for these games
    either (only the real belt's combined scope gets those), so nothing
    here links out to games/ or teams/. `lineage` is
    belt_data/conferences/<slug>_lineage.json's already-loaded contents."""
    conference = lineage["conference"]
    classification = lineage.get("classification", "fbs")
    reigns = lineage["reigns"]
    totals = lineage["totals"]
    current = reigns[-1]
    today = date.today()

    since_date = date.fromisoformat(current["start_date"])
    defenses = current["defenses"]
    won_from = current.get("won_from")
    reclaimed_after = current.get("reclaimed_after")

    if reclaimed_after:
        lede = (f"{esc(reclaimed_after)} caught it but left {esc(conference)} &mdash; since "
                 f"this belt only passes among {esc(conference)} members, it reverted back to "
                 f"{esc(current['team'])} on {fmt_date(current['start_date'])}.")
    elif won_from:
        lede = (f"Caught it by beating {esc(won_from)} on {fmt_date(current['start_date'])}.")
    else:
        lede = f"Has held it since {fmt_date(current['start_date'])}."
    if defenses:
        lede += (f" Defended it {defenses} more time{'s' if defenses != 1 else ''} since.")

    durations = [(r, reign_duration_days(r, today)) for r in reigns]
    longest_reign, longest_days = max(durations, key=lambda p: p[1])
    most_defended = max(reigns, key=lambda r: r["defenses"])
    reign_counts = Counter(r["team"] for r in reigns)
    most_reigns_team, most_reigns_n = reign_counts.most_common(1)[0]

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
      <div class="sub">{totals["distinct_teams"]} {esc(conference)} programs have held it</div>
    </div>'''

    rows_html = ""
    for i, r in enumerate(reigns, 1):
        is_current = r is current
        team = r["team"]

        if r.get("reclaimed_after"):
            caught_txt = f"reverted after {esc(r['reclaimed_after'])} left {esc(conference)}"
        elif r.get("won_from"):
            caught_txt = f"beat {esc(r['won_from'])}"
        else:
            caught_txt = "Established it (first game on record)"

        if is_current:
            passed_txt = '<span class="mono">— present —</span>'
            end_txt = "Present"
        elif r.get("vacated"):
            passed_txt = f"vacated — left {esc(conference)}"
            end_txt = fmt_date(r["end_date"])
        elif r.get("lost_to"):
            passed_txt = f"lost to {esc(r['lost_to'])}"
            end_txt = fmt_date(r["end_date"])
        else:
            passed_txt = "—"
            end_txt = fmt_date(r["end_date"]) if r.get("end_date") else "—"

        cls = " current" if is_current else ""
        rows_html += f'''
        <tr class="{cls.strip()}" data-team="{esc(team.lower())}">
          <td class="num">{i}</td>
          <td class="teamCell">{esc(team)}</td>
          <td class="dates">{fmt_date(r["start_date"])} &ndash; {end_txt}</td>
          <td class="tabular">{fmt_duration(*reign_dates(r, today))}</td>
          <td class="tabular">{r["defenses"]}</td>
          <td class="won">{caught_txt}</td>
          <td class="lost">{passed_txt}</td>
        </tr>'''

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>The {esc(conference)} Belt — The College Football Belt</title>
<meta name="description" content="The {esc(conference)} Belt: a lineal conference title that passes to whoever beats the holder in a game between two {esc(conference)} members. Held by {esc(current["team"])}.">
<link rel="stylesheet" href="../styles.css?v={STYLES_VERSION}">
{head_extras('../')}

{site_header('../', 'conferences')}

<main class="wrap">
  <p class="kicker pageKicker">{classification.upper()} conference belt</p>
  <h1 class="pageTitle">The {esc(conference)} Belt</h1>
  <p class="lede">A companion lineage restricted to {esc(conference)}: the belt passes to
    whoever beats the holder, exactly like the real belt, but only games between two
    {esc(conference)} members COUNT &mdash; and only for the seasons both sides were actually
    in {esc(conference)} at the time. {lede}</p>

  <div class="historyTop">
    <div class="historyStats">
      <div><span class="n tabular">{totals["reigns"]:,}</span><span class="l">Reigns</span></div>
      <div><span class="n tabular">{totals["belt_games"]:,}</span><span class="l">Belt Games</span></div>
      <div><span class="n tabular">{totals["distinct_teams"]}</span><span class="l">Programs</span></div>
    </div>
    <div class="searchBox">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/></svg>
      <input id="teamSearch" type="search" placeholder="Filter by team&hellip;" aria-label="Filter by team" autocomplete="off">
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
  <p class="viewToggle"><a href="index.html">&larr; See every conference belt</a></p>
</main>

{site_footer('../', 'Every reign computed from the College Football Data API.')}

<script>
(function(){{
  var input = document.getElementById('teamSearch');
  var tbody = document.querySelector('table.reignsTable tbody');
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


def generate_conferences_index_page(conference_lineages):
    """Hub page linking every bootstrapped conference belt
    (conferences/<slug>.html), grouped FBS then FCS, each row showing the
    current holder at a glance. `conference_lineages` is a
    {slug: lineage_dict} map of whatever's actually been bootstrapped so
    far (see build_conference_lineage.py) -- a conference simply doesn't
    appear here until its own one-time historical build has run.

    2026-09-16, Bob: "move the conferences that no longer exist to the
    bottom of their own section." Within each classification (FBS/FCS),
    conferences with a qualifying game within the last ~2 seasons sort
    first (alphabetically, as before); anything older -- SIAA, the old
    Big 8/Southwest/Pac-8/Pac-10, Skyline, Yankee, and the like, whose
    membership dissolved or merged into today's conferences -- drops into
    a "No longer active" subsection below them, still alphabetical, still
    inside that same FBS/FCS section. See build_conference_lineage.py's
    write_outputs() for where last_game_date comes from: it's the full
    merged history's last entry, not just this run's fetch window, so this
    works correctly even on an ordinary incremental run."""
    current_year = date.today().year

    def is_active(lineage):
        last = lineage.get("last_game_date")
        return bool(last) and int(last[:4]) >= current_year - 1

    def card_for(slug, lineage, active):
        conference = lineage["conference"]
        current = lineage["reigns"][-1]
        totals = lineage["totals"]
        last = lineage.get("last_game_date")
        sub = (f"since {fmt_date(current['start_date'])} &middot; {totals['reigns']} reigns all-time" if active
               else f"last played as a conference in {last[:4]} &middot; {totals['reigns']} reigns all-time")
        return f'''
    <a class="record-card" href="{slug}.html" style="display:block;text-decoration:none;color:inherit">
      <div class="l">{esc(conference)}</div>
      <div class="v">{esc(current["team"])}</div>
      <div class="sub">{sub}</div>
    </a>'''

    def section_html(classification):
        items = sorted((s, l) for s, l in conference_lineages.items() if l.get("classification") == classification)
        if not items:
            return '<p class="lede">None built yet.</p>'
        active_items = [(s, l) for s, l in items if is_active(l)]
        defunct_items = [(s, l) for s, l in items if not is_active(l)]
        html_parts = ['<div class="records">']
        html_parts.append("".join(card_for(s, l, True) for s, l in active_items))
        html_parts.append("\n  </div>")
        if defunct_items:
            html_parts.append('''
  <h3 class="eyebrow" style="display:block;margin:28px 0 12px">No Longer Active</h3>
  <div class="records">''')
            html_parts.append("".join(card_for(s, l, False) for s, l in defunct_items))
            html_parts.append("\n  </div>")
        return "".join(html_parts)

    fbs_html = section_html("fbs")
    fcs_html = section_html("fcs")

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Conference Belts — The College Football Belt</title>
<meta name="description" content="A lineal championship belt for every FBS and FCS conference: the same beat-the-holder rule as the real belt, counting only games between two members of that conference.">
<link rel="stylesheet" href="../styles.css?v={STYLES_VERSION}">
{head_extras('../')}

{site_header('../', 'conferences')}

<main class="wrap">
  <p class="kicker pageKicker">One per conference</p>
  <h1 class="pageTitle">Conference Belts</h1>
  <p class="lede">The same lineal rule as the real belt &mdash; you catch it by beating the
    holder &mdash; run separately for every FBS and FCS conference, counting only games
    between two members of that ONE conference, at the time they actually played (so
    realignment moves a team's games with it, the way it should).</p>

  <h2>FBS Conferences</h2>
  {fbs_html}

  <h2>FCS Conferences</h2>
  {fcs_html}
</main>

{site_footer('../', 'Every reign computed from the College Football Data API.')}
'''


# -------------------------------------------------------------- all games page

def generate_all_games_page(lineage, colors, belt_games, scope="combined", available_scopes=("combined",)):
    """Every belt game, one row each -- title changes AND defenses, unlike
    the Full History page above which only has one row per reign (the game
    where it STARTED). Reuses the same reignsTable/searchBox/historyTop
    CSS and search-filter JS as generate_lineage_page for a consistent look,
    just with a different (game-shaped, not reign-shaped) column set.

    `scope`/`available_scopes`: same meaning as generate_lineage_page. Only
    "combined" games reliably have a games/<id>.html detail page (see that
    function's own docstring), so fbs/fcs render each row's matchup/score
    as plain text instead of a link."""
    totals = lineage["totals"]
    linkable = scope == "combined"
    # "established" (the very first belt game, which put the title up in the
    # first place) is folded into title_changes for this stat band -- it's
    # not a "defense" of anything, and folding it in keeps title_changes +
    # defenses_total == len(belt_games) exactly, and title_changes == the
    # total reign count on the Full History page (328, not 327).
    title_changes = sum(1 for g in belt_games if g["outcome"] in ("changed", "established"))
    defenses_total = len(belt_games) - title_changes

    rows_html = ""
    defense_no = 0
    for g in belt_games:
        home, away = g["home"], g["away"]
        home_score, away_score = (int(x) for x in g["score"].split("-"))
        outcome = g["outcome"]
        is_last = g is belt_games[-1]

        if outcome == "established":
            defense_no = 0
            result_html = f'<span class="win">Belt established: {esc(g["new_holder"])}</span>'
        elif outcome == "changed":
            defense_no = 0
            result_html = f'<span class="win">New champion: {esc(g["new_holder"])}</span>'
        else:
            defense_no += 1
            tie_note = " (tie)" if outcome == "retained (tie)" else ""
            result_html = f'Defended{tie_note} &middot; #{defense_no}'

        loc_word = "vs." if g["neutral"] else "at"
        matchup_text = f'{esc(away)} {loc_word} {esc(home)}'
        matchup_html = (f'<a href="games/{g["game_id"]}.html">{matchup_text}</a>'
                         if linkable else matchup_text)
        score_html = (f'<a href="games/{g["game_id"]}.html">{away_score}&ndash;{home_score}</a>'
                      if linkable else f'{away_score}&ndash;{home_score}')

        cls_bits = []
        if outcome in ("changed", "established"):
            cls_bits.append("titleChange")
        if is_last:
            cls_bits.append("current")
        cls = " ".join(cls_bits)

        rows_html += f'''
        <tr class="{cls}" data-team="{esc(home.lower())} {esc(away.lower())}">
          <td class="num">{g["game_number"]:,}</td>
          <td class="dates">{fmt_date(g["date"])}</td>
          <td class="matchup">{matchup_html}</td>
          <td class="tabular">{score_html}</td>
          <td class="result">{result_html}</td>
        </tr>'''

    title_suffix = CHAMPIONSHIP_SCOPE_TITLE_SUFFIX[scope]
    scope_intro = CHAMPIONSHIP_SCOPE_INTRO[scope]
    switcher_html = _championship_switcher_html(CHAMPIONSHIP_ALL_GAMES_FILENAMES, scope, available_scopes)
    lineage_href = CHAMPIONSHIP_LINEAGE_FILENAMES[scope]
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>All Games{title_suffix} — The College Football Belt</title>
<meta name="description" content="Searchable list of every game played for the College Football Belt{title_suffix} since 1869: dates, scores, title changes, defenses and ties.">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'all-games')}

<main class="wrap">
  <p class="kicker pageKicker">Every belt game</p>
  <h1 class="pageTitle">Every Belt Game{title_suffix}</h1>
  <p class="lede">Every game with the belt on the line since Rutgers beat Princeton on
    November&nbsp;6, 1869 &mdash; {len(belt_games):,} of them: {title_changes:,} title changes
    and {defenses_total:,} successful defenses, across {totals["distinct_teams"]} programs.{scope_intro}
    Type a team name to filter; tap any game to open its page.</p>
  {switcher_html}

  <div class="historyTop">
    <div class="historyStats">
      <div><span class="n tabular">{len(belt_games):,}</span><span class="l">Total Games</span></div>
      <div><span class="n tabular">{title_changes:,}</span><span class="l">Title Changes</span></div>
      <div><span class="n tabular">{defenses_total:,}</span><span class="l">Defenses</span></div>
    </div>
    <div class="controls">
      <div class="sortToggle" role="group" aria-label="Sort order">
        <button type="button" class="sortBtn active" data-order="asc">Oldest First</button>
        <button type="button" class="sortBtn" data-order="desc">Newest First</button>
      </div>
      <div class="searchBox">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/></svg>
        <input id="teamSearch" type="search" placeholder="Filter by team&hellip;" aria-label="Filter by team" autocomplete="off">
      </div>
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
    <a href="{lineage_href}">See the Full History page &rarr;</a></p>
</main>

{site_footer('', 'Every game computed from the College Football Data API.')}

<script>
(function(){{
  var input = document.getElementById('teamSearch');
  var tbody = document.querySelector('table.reignsTable tbody');
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

  var STORAGE_KEY = 'cfbBelt:allGamesSortOrder';
  var sortBtns = Array.prototype.slice.call(document.querySelectorAll('.sortToggle .sortBtn'));
  function applyOrder(order){{
    var ordered = order === 'desc' ? rows.slice().reverse() : rows.slice();
    ordered.forEach(function(r){{ tbody.appendChild(r); }});
    sortBtns.forEach(function(b){{ b.classList.toggle('active', b.getAttribute('data-order') === order); }});
    try {{ localStorage.setItem(STORAGE_KEY, order); }} catch(e) {{}}
  }}
  sortBtns.forEach(function(b){{
    b.addEventListener('click', function(){{ applyOrder(b.getAttribute('data-order')); }});
  }});
  var savedOrder = null;
  try {{ savedOrder = localStorage.getItem(STORAGE_KEY); }} catch(e) {{}}
  if (savedOrder === 'desc') applyOrder('desc');
}})();
</script>
'''


# ------------------------------------------------------------ preview page

def render_recent_form(team, games):
    if not games:
        return f'<p class="emptyNote">No recent completed games on record for {esc(team)}.</p>'
    items = ""
    for g in games:
        cls = "t" if g.get("tied") else ("w" if g["won"] else "l")
        letter = "T" if g.get("tied") else ("W" if g["won"] else "L")
        loc = "vs" if g["home"] else "at"
        date_txt = fmt_date(g["date"][:10]) if g.get("date") else ""
        items += f'''
        <li>
          <span class="formBadge {cls}">{letter}</span>
          <span class="formScore">{g["score_for"]}&ndash;{g["score_against"]}</span>
          <span class="formOpp">{loc} {esc(g["opponent"])}</span>
          <span class="formDate">{date_txt}</span>
        </li>'''
    return f'<ul class="formList">{items}\n      </ul>'


def render_head_to_head(holder, opponent, h2h):
    if not h2h:
        return '<p class="emptyNote">No head-to-head data available.</p>'
    t1w = h2h.get("team1_wins", 0) or 0
    t2w = h2h.get("team2_wins", 0) or 0
    ties = h2h.get("ties", 0) or 0
    if t1w + t2w + ties == 0:
        return (f'<p class="emptyNote">{esc(holder)} and {esc(opponent)} have '
                f'no recorded meetings in CFBD&rsquo;s data.</p>')

    tie_txt = f"&ndash;{ties}" if ties else ""
    if t1w > t2w:
        headline = f"{esc(holder)} leads the series {t1w}&ndash;{t2w}{tie_txt}"
    elif t2w > t1w:
        headline = f"{esc(opponent)} leads the series {t2w}&ndash;{t1w}{tie_txt}"
    else:
        headline = f"Series tied {t1w}&ndash;{t2w}{tie_txt}"
    since = f" (since {h2h['start_year']})" if h2h.get("start_year") else ""

    rows = ""
    for m in (h2h.get("games") or [])[:8]:
        season = m.get("season") or "&mdash;"
        home_t, away_t = m.get("home_team") or "?", m.get("away_team") or "?"
        hs, as_ = m.get("home_score"), m.get("away_score")
        score_txt = f"{as_}&ndash;{hs}" if (hs is not None and as_ is not None) else "&mdash;"
        loc_word = "vs." if m.get("neutral") else "at"
        rows += f'''
        <tr>
          <td class="num">{season}</td>
          <td class="teamCell">{esc(away_t)} {loc_word} {esc(home_t)}</td>
          <td class="tabular">{score_txt}</td>
        </tr>'''

    table_html = ""
    if rows:
        table_html = f'''
    <div class="tableScroll">
      <table class="reignsTable">
        <thead><tr><th>Season</th><th>Matchup</th><th style="text-align:right">Score</th></tr></thead>
        <tbody>{rows}
        </tbody>
      </table>
    </div>'''

    return f'<p class="h2hHeadline">{headline}{since}</p>{table_html}'


def render_weather(weather):
    """A small kickoff-forecast card -- gracefully renders nothing when
    fetch_weather.py had no venue coordinates, the game's too far out for
    Open-Meteo's forecast window, or the fetch itself failed."""
    if not weather or weather.get("temp_f") is None:
        return ""
    temp = round(weather["temp_f"])
    feels = weather.get("feels_like_f")
    feels_bit = f" (feels {round(feels)}&deg;)" if feels is not None and round(feels) != temp else ""
    condition = weather.get("condition") or ""
    wind = weather.get("wind_mph")
    precip = weather.get("precip_chance")
    where = weather.get("venue_name")
    where_bit = f" at {esc(where)}" if where else ""

    bits = []
    if wind is not None:
        bits.append(f"Wind {round(wind)} mph")
    if precip is not None:
        bits.append(f"{round(precip)}% chance of precipitation")
    sub = " &middot; ".join(bits)

    return f'''
  <div class="sectionHead withTag">
    <span class="tag">Forecast</span>
    <span class="rule"></span>
    <h2>Kickoff Weather</h2>
  </div>
  <div class="weatherCard">
    <span class="weatherTemp tabular">{temp}&deg;F{feels_bit}</span>
    <span class="weatherCond">{esc(condition)}{where_bit}</span>
    {f'<span class="weatherSub">{sub}</span>' if sub else ''}
  </div>
  <p class="emptyNote">Forecast as of {weather.get("fetched", "recently")} &mdash; weather this far out can change; treat it as a rough guide, not a promise.</p>'''


def belt_meetings(belt_games, team_a, team_b):
    """Every belt game these two programs have played against each other,
    newest first -- straight out of the lineage, no extra data."""
    out = []
    for g in belt_games:
        if {g["home"], g["away"]} == {team_a, team_b}:
            out.append(g)
    out.sort(key=lambda g: g["date"], reverse=True)
    return out


def team_belt_summary(reigns, team, today):
    """(reigns, total days held, last held year or None) for one program."""
    mine = [r for r in reigns if r["team"] == team]
    if not mine:
        return 0, 0, None
    days = sum(reign_duration_days(r, today) for r in mine)
    last = mine[-1]
    last_year = None if last.get("end_date") is None else last["end_date"][:4]
    return len(mine), days, last_year


def generate_preview_page(next_game, matchup, ai_preview, weather, colors, belt_risk=None,
                          lineage=None, belt_games=None):
    """The next-belt-game preview (2026-09-16 redesign): a split header in
    both teams' colors, a stakes strip that answers "what happens if each
    side wins" before any prose, the AI-written preview as a column with the
    prediction boxed and labeled, and a sidebar of the belt history between
    the two programs."""
    header = site_header('', 'preview')
    footer = site_footer('', 'Recent form and head-to-head from the College Football Data API; belt history from the lineage itself.')

    if not next_game:
        return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Up Next — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{header}

<main class="wrap">
  <div class="pageIntro">
    <span class="kicker">Up next</span>
    <h1 class="pageTitle">No upcoming game yet</h1>
    <p class="lede">The current holder&rsquo;s next game hasn&rsquo;t shown up in CollegeFootballData&rsquo;s
      records yet &mdash; check back soon.</p>
  </div>
</main>

{footer}
'''

    holder = next_game["team"]
    opponent = next_game["opponent"]
    today = date.today()
    reigns = (lineage or {}).get("reigns") or []
    belt_games = belt_games or []
    current = reigns[-1] if reigns else None
    if next_game.get("neutral"):
        side_word, side_full = "vs.", "faces (neutral site)"
    elif next_game.get("is_home"):
        side_word, side_full = "vs.", "hosts"
    else:
        side_word, side_full = "at", "travels to"
    title = f"{esc(holder)} {side_word} {esc(opponent)}"

    # ---- colors for the split header (contrast-checked, like game pages) ----
    h_primary, h_alt = team_color(colors, holder)
    o_primary, o_alt = team_color(colors, opponent)
    h_ink, h_accent = panel_colors(h_primary, h_alt)
    o_ink, o_accent = panel_colors(o_primary, o_alt)

    # ---- matchup data (fetch_matchup_preview.py) ----
    matchup = matchup or {}
    recent = matchup.get("recent_form") or {}
    h_form = recent.get(holder, [])
    o_form = recent.get(opponent, [])

    def record(form):
        w = sum(1 for g in form if g.get("won") and not g.get("tied"))
        l = sum(1 for g in form if not g.get("won") and not g.get("tied"))
        t = sum(1 for g in form if g.get("tied"))
        if not form:
            return ""
        return f"{w}&ndash;{l}" + (f"&ndash;{t}" if t else "") + " in the last " + str(len(form))

    def last_result(form):
        if not form:
            return ""
        g = form[0]
        verb = "tied" if g.get("tied") else ("def." if g["won"] else "lost to")
        return f'Last: {verb} {esc(g["opponent"])} {g["score_for"]}&ndash;{g["score_against"]}'

    holder_form_html = render_recent_form(holder, h_form)
    opp_form_html = render_recent_form(opponent, o_form)
    h2h_html = render_head_to_head(holder, opponent, matchup.get("head_to_head"))

    # ---- stakes (all from the lineage) ----
    defenses = current["defenses"] if current else 0
    days_held = reign_duration_days(current, today) if current else 0
    game_date = date.fromisoformat(next_game["date"])
    days_at_kick = (game_date - date.fromisoformat(current["start_date"])).days if current else 0
    holder_reign_num = sum(1 for r in reigns if r["team"] == holder)
    opp_reigns, opp_days, opp_last = team_belt_summary(reigns, opponent, today)
    meetings = belt_meetings(belt_games, holder, opponent)
    h_wins = sum(1 for g in meetings if g["new_holder"] == holder and g["outcome"] != "retained (tie)")
    o_wins = sum(1 for g in meetings if g["new_holder"] == opponent and g["outcome"] != "retained (tie)")
    if meetings:
        lead = (f"{esc(holder)} leads {h_wins}&ndash;{o_wins}" if h_wins > o_wins else
                f"{esc(opponent)} leads {o_wins}&ndash;{h_wins}" if o_wins > h_wins else f"Even at {h_wins}&ndash;{o_wins}")
        h2h_belt = f'{lead} &middot; last met {meetings[0]["date"][:4]}'
    else:
        h2h_belt = "First belt game between these two"
    if opp_reigns:
        if_opp = f"Belt changes hands &middot; {esc(opponent)}&rsquo;s {ordinal(opp_reigns + 1)} reign"
        opp_kicker = f"Challenger &middot; last held {opp_last}" if opp_last else "Challenger"
    else:
        if_opp = f"Belt changes hands &middot; {esc(opponent)}&rsquo;s first reign ever"
        opp_kicker = "Challenger &middot; has never held it"
    if_holder = f"{ordinal(defenses + 1)} defense &middot; reign reaches {days_at_kick:,} days"

    weather_cell = ""
    if weather and weather.get("temp_f") is not None:
        bits = [f"{round(weather['temp_f'])}&deg;F"]
        if weather.get("condition"):
            bits.append(esc(weather["condition"]).lower())
        if weather.get("wind_mph") is not None:
            bits.append(f"wind {round(weather['wind_mph'])} mph")
        if weather.get("precip_chance"):
            bits.append(f"{round(weather['precip_chance'])}% rain")
        fetched = weather.get("fetched")
        as_of = f' <span class="stakeSub">forecast as of {esc(fetched)}</span>' if fetched else ""
        weather_cell = f'''
      <div class="stakeCell"><span class="kicker">Kickoff weather</span><span class="stakeVal">{" &middot; ".join(bits)}{as_of}</span></div>'''
    else:
        weather_cell = '''
      <div class="stakeCell"><span class="kicker">Kickoff weather</span><span class="stakeVal">Forecast arrives closer to kickoff</span></div>'''

    # ---- odds (fetch_belt_odds.py), staleness-guarded to this opponent ----
    odds_html = ""
    if belt_risk and belt_risk.get("next_game", {}).get("opponent") == opponent:
        defend_prob = belt_risk["next_game"].get("defend_prob")
        holds_prob = belt_risk.get("season", {}).get("holds_into_offseason_prob")
        bits = []
        if defend_prob is not None:
            source = belt_risk["next_game"].get("source")
            source_txt = "CFBD&rsquo;s pregame model" if source == "cfbd_pregame_wp" else "our Elo estimate"
            bits.append(f'<strong>{round(defend_prob * 100)}%</strong> to defend, per {source_txt}')
        if holds_prob is not None:
            has_outlook = bool(belt_risk.get("season", {}).get("end_of_season"))
            hold_txt = f'<strong>{round(holds_prob * 100)}%</strong> to hold the belt into the offseason'
            bits.append(hold_txt + (' (<a href="outlook.html">full season outlook</a>)' if has_outlook else ''))
        if bits:
            odds_html = f'<p class="previewOdds">{" &middot; ".join(bits)}</p>'

    # ---- AI-written preview (generate_ai_preview.py) ----
    ai_preview = ai_preview or {}
    overview = ai_preview.get("overview") or ""
    key_matchups = ai_preview.get("key_matchups") or []
    betting = ai_preview.get("betting_angles") or ""
    predicted_winner = ai_preview.get("predicted_winner") or ""
    predicted_score = ai_preview.get("predicted_score") or ""
    prediction_writeup = ai_preview.get("prediction_writeup") or ""
    preview_html = ""
    if overview or key_matchups or betting:
        matchups_html = "".join(f"<li>{esc(m)}</li>" for m in key_matchups)
        matchups_block = f'<p class="kicker" style="margin:22px 0 6px">Worth watching</p><ul class="keyMatchups">{matchups_html}</ul>' if matchups_html else ""
        betting_html = f'<p class="kicker" style="margin:22px 0 6px">The numbers</p><p>{esc(betting)}</p>' if betting else ""
        preview_html = f'''
      <div class="sectionHead">
        <span class="tag">The preview &middot; AI-written</span>
        <h2>{esc(holder)} {side_full} {esc(opponent)} with the belt on the line</h2>
      </div>
      <div class="editorial">
        <p>{esc(overview)}</p>
        {matchups_block}
        {betting_html}
      </div>'''
    lean_html = ""
    if predicted_winner or prediction_writeup:
        call_line = esc(predicted_score) if predicted_score else (f"{esc(predicted_winner)} to win" if predicted_winner else "No clear pick")
        lean_html = f'''
      <div class="leanBox">
        <span class="kicker">The lean</span>
        <p class="leanCall">{call_line}</p>
        <p>{esc(prediction_writeup)}</p>
        <p class="leanNote">Written by Claude from the stats and forecast on this page &mdash; a for-fun editorial call, not betting advice or a guarantee. If it stops being fun, the National Problem Gambling Helpline is 1-800-522-4700.</p>
      </div>'''
    if not preview_html and not lean_html:
        preview_html = '''
      <div class="sectionHead">
        <span class="tag">The preview</span>
        <h2>The belt is on the line</h2>
      </div>
      <p class="lede">A written preview lands here once the pipeline&rsquo;s next run has the matchup stats in hand.</p>'''

    calendar_html = build_calendar_links(next_game)

    meetings_rows = ""
    for g in meetings[:6]:
        h_s, a_s = (int(x) for x in g["score"].split("-"))
        winner = g["new_holder"]
        w_s, l_s = (h_s, a_s) if winner == g["home"] else (a_s, h_s)
        if g["outcome"] in ("changed", "established"):
            tag = "Changed hands"
        elif h_s == a_s:
            tag = "Tie"
        else:
            tag = "Defended"
        meetings_rows += (f'<a class="miniRow" href="games/{g["game_id"]}.html"><span>{g["date"][:4]} &middot; '
                          f'<strong>{esc(winner)}</strong> {w_s}&ndash;{l_s}</span><span class="miniTag">{tag}</span></a>')
    meetings_html = (f'<div class="miniList">{meetings_rows}</div>' if meetings_rows else
                     f'<p class="emptyNote">These two have never met with the belt on the line.</p>')
    opp_stats = f'''
        <div class="miniStats">
          <div><span class="n tabular">{opp_reigns}</span><span class="l">Reign{"s" if opp_reigns != 1 else ""}</span></div>
          <div><span class="n tabular">{opp_days:,}</span><span class="l">Days held</span></div>
          <div><span class="n">{opp_last or "&mdash;"}</span><span class="l">Last held</span></div>
        </div>'''
    opp_link = (f'<a class="moreLink" href="teams/{team_slug(opponent)}.html">Team page &amp; poster &rarr;</a>'
                if opp_reigns else f'<a class="moreLink" href="all-games.html?q={quote(opponent)}">Every {esc(opponent)} belt game &rarr;</a>')

    kickoff_local = ""
    day_abbr = game_date.strftime("%a").upper()
    when_line = fmt_date(next_game["date"])
    raw = next_game.get("raw_date") or ""
    venue_bits = [b for b in (next_game.get("venue_name"), next_game.get("venue_city"), next_game.get("venue_state")) if b]
    venue_txt = esc(", ".join(venue_bits[:2])) if venue_bits else ("Neutral site" if next_game.get("neutral") else "")

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>{title} Preview — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}
<style>
  :root{{
    --home:{h_primary}; --home-ink:{h_ink}; --home-accent:{h_accent};
    --away:{o_primary}; --away-ink:{o_ink}; --away-accent:{o_accent};
  }}
</style>

{header}

<main class="wrap">
  <div class="crumbRow" style="padding-inline:0"><a href="index.html">Belt</a> <span class="sep">/</span> <a href="season-{next_game.get("season", game_date.year)}.html">{next_game.get("season", game_date.year)} season</a> <span class="sep">/</span> Up next</div>
  <h1 class="srOnly">Up next: {title}, {when_line} &mdash; the belt is on the line</h1>

  <div class="matchHead">
    <div class="matchSide home">
      <span class="kicker">Holder &middot; {ordinal(holder_reign_num)} reign &middot; {defenses} defense{"s" if defenses != 1 else ""}</span>
      <div class="matchTeam">{logo_chip(colors, holder, 56)}<span class="matchName"><a href="teams/{team_slug(holder)}.html">{esc(holder)}</a></span></div>
      <span class="matchRecord">{record(h_form)}{" &middot; " if record(h_form) and last_result(h_form) else ""}{last_result(h_form)}</span>
    </div>
    <div class="matchCenter">
      <span class="kicker">Belt on the line</span>
      <span class="matchDay">{day_abbr}</span>
      <span class="matchWhen">{when_line}<br>{venue_txt}</span>
      <span class="matchLocal" id="kickoffLocal" data-utc="{esc(raw)}" hidden></span>
    </div>
    <div class="matchSide away">
      <span class="kicker">{opp_kicker}</span>
      <div class="matchTeam">{logo_chip(colors, opponent, 56)}<span class="matchName">{f'<a href="teams/{team_slug(opponent)}.html">{esc(opponent)}</a>' if opp_reigns else esc(opponent)}</span></div>
      <span class="matchRecord">{record(o_form)}{" &middot; " if record(o_form) and last_result(o_form) else ""}{last_result(o_form)}</span>
    </div>
  </div>
  <div class="stakes">
    <div class="stakeCell"><span class="kicker">If {esc(holder)} wins</span><span class="stakeVal">{if_holder}</span></div>
    <div class="stakeCell"><span class="kicker">If {esc(opponent)} wins</span><span class="stakeVal">{if_opp}</span></div>
    <div class="stakeCell"><span class="kicker">Head to head, belt games</span><span class="stakeVal">{h2h_belt}</span></div>{weather_cell}
  </div>
  {odds_html}
  <script>
  (function(){{
    var el = document.getElementById('kickoffLocal');
    var raw = el && el.getAttribute('data-utc');
    if (!raw) return;
    var d = new Date(raw);
    if (isNaN(d.getTime())) return;
    try {{
      var fmt = new Intl.DateTimeFormat(undefined, {{ hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }});
      el.textContent = fmt.format(d) + ' your time';
      el.hidden = false;
    }} catch (e) {{}}
  }})();
  </script>

  <div class="previewGrid">
    <div class="previewMain">
      {preview_html}
      {lean_html}
      <div class="sectionHead">
        <span class="tag">Recent form</span>
        <h2>Last {max(len(h_form), len(o_form)) or 5} games</h2>
      </div>
      <div class="formGrid">
        <div class="formCol">
          <h3>{logo_img(colors, holder, "teamLogo", 22)}{esc(holder)}</h3>
          {holder_form_html}
        </div>
        <div class="formCol">
          <h3>{logo_img(colors, opponent, "teamLogo", 22)}{esc(opponent)}</h3>
          {opp_form_html}
        </div>
      </div>
      <div class="sectionHead">
        <span class="tag">Head-to-head</span>
        <h2>All-time series</h2>
      </div>
      {h2h_html}
    </div>
    <aside class="previewSide">
      <div class="sideCard" id="calendar">
        <span class="kicker">Don&rsquo;t miss it</span>
        {calendar_html if calendar_html else '<p class="emptyNote">Kickoff time to be announced.</p>'}
        <a class="btn ghost" href="index.html#alerts">Email me if it changes hands</a>
      </div>
      <div class="sideCard">
        <span class="kicker">Belt history between these two</span>
        {meetings_html}
        <a class="moreLink" href="compare.html">Full comparison &rarr;</a>
      </div>
      <div class="sideCard">
        <span class="kicker">{esc(opponent)} &amp; the belt</span>
        {opp_stats}
        {opp_link}
      </div>
    </aside>
  </div>
</main>

{footer}
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

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Ruleset — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'ruleset')}

<main class="wrap">
  <p class="kicker pageKicker">The ruleset</p>
  <h1 class="pageTitle">The Ruleset</h1>
  <div class="proseBlock">{intro_html}</div>
{sections_html}
</main>

{site_footer('', 'Every belt game sourced from the College Football Data API and computed against the rules on this page &mdash; no editorial judgment per game.')}
'''


# --------------------------------------------------------------- records page

def _record_row(rank, swatch_color, main_html, value_html, sub_html, href=None):
    dot = f'<span class="swatch" style="background:{swatch_color}"></span>' if swatch_color else ""
    body = f'<span class="recordMain">{dot}{main_html}</span><span class="recordValue tabular">{value_html}</span>'
    if sub_html:
        body += f'<span class="recordSub">{sub_html}</span>'
    tag = "a" if href else "div"
    href_attr = f' href="{href}"' if href else ""
    return f'<{tag} class="recordRow"{href_attr}><span class="recordRank">{rank}</span>{body}</{tag}>'


def generate_records_page(lineage, colors, belt_games, coaches=None):
    """Ten record boards, computed straight from data already on hand --
    no new API calls, no AI, except the by-coach board which needs the
    separately-fetched, entirely optional belt_data/coaches.json (see
    fetch_coaches.py; pass None/omit to just skip that one card, same
    no-op-when-unset pattern as everything else on this site). Ties
    aren't broken (a team a few days short of another's reign length
    still shows up if it's genuinely top-5)."""
    reigns = lineage["reigns"]
    today = date.today()
    change_index = build_change_game_index(belt_games)

    def start_game_href(r):
        g = change_index.get((r["start_date"], r["team"]))
        return f'games/{g["game_id"]}.html' if g else None

    def team_swatch(name):
        primary, _ = team_color(colors, name)
        return primary

    # ---- longest reigns, by days held (current reign counts through today) ----
    longest = sorted(reigns, key=lambda r: reign_duration_days(r, today), reverse=True)[:5]
    longest_rows = ""
    for i, r in enumerate(longest, 1):
        start, end = reign_dates(r, today)
        is_current = r is reigns[-1]
        sub = f'{fmt_date(r["start_date"])} &ndash; {"present" if is_current else fmt_date(r["end_date"])}'
        if is_current:
            sub += ' <span class="currentTag">current</span>'
        longest_rows += _record_row(i, team_swatch(r["team"]), esc(r["team"]),
                                     fmt_duration(start, end), sub, start_game_href(r))

    # ---- most total days held, all-time -- summed across every reign a
    # program has ever had, not just its longest one. A different ranking
    # from "Longest Reigns" above: a program with several shorter reigns
    # can outrank one with a single long one. ----
    total_days_by_team = {}
    reign_count_by_team = {}
    for r in reigns:
        total_days_by_team[r["team"]] = total_days_by_team.get(r["team"], 0) + reign_duration_days(r, today)
        reign_count_by_team[r["team"]] = reign_count_by_team.get(r["team"], 0) + 1
    most_total_days = sorted(total_days_by_team.items(), key=lambda kv: kv[1], reverse=True)[:5]
    total_days_rows = ""
    for i, (team, days) in enumerate(most_total_days, 1):
        n = reign_count_by_team[team]
        sub = f'across {n} reign{"s" if n != 1 else ""}'
        total_days_rows += _record_row(i, team_swatch(team), esc(team),
                                        f'{days:,}', sub, f'teams/{team_slug(team)}.html')

    # ---- most reigns held by one program ----
    reign_counts = {}
    for r in reigns:
        reign_counts[r["team"]] = reign_counts.get(r["team"], 0) + 1
    most_reigns = sorted(reign_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    most_reigns_rows = ""
    for i, (team, n) in enumerate(most_reigns, 1):
        most_reigns_rows += _record_row(i, team_swatch(team), esc(team),
                                         f'{n}&times;', "reigns held",
                                         f'teams/{team_slug(team)}.html')

    # ---- longest current droughts -- programs that have held the belt
    # before but don't right now, ranked by how long it's been since their
    # most recent reign ended (2026-09-16 wishlist, task #84). `reigns` is
    # chronological (relied on elsewhere in this function too, e.g. `r is
    # reigns[-1]` for "is this the current reign"), so the LAST entry seen
    # per team below is that team's true most recent reign -- a team that
    # held the belt, lost it, and later reclaimed it (so its most recent
    # reign is the still-open current one) is correctly excluded, unlike
    # naively skipping only open reigns as they're encountered, which
    # would wrongly surface that team's earlier, superseded reign instead. ----
    most_recent_reign_by_team = {}
    for r in reigns:
        most_recent_reign_by_team[r["team"]] = r
    droughts = sorted(
        (r for r in most_recent_reign_by_team.values() if r.get("end_date") is not None),
        key=lambda r: date.fromisoformat(r["end_date"]), reverse=True)[:5]
    drought_rows = ""
    for i, r in enumerate(droughts, 1):
        end = date.fromisoformat(r["end_date"])
        drought_rows += _record_row(i, team_swatch(r["team"]), esc(r["team"]),
                                     fmt_duration(end, today),
                                     f'last held it {fmt_date(r["end_date"])}',
                                     f'teams/{team_slug(r["team"])}.html')

    # ---- most days held under one head coach -- attributes each reign's
    # FULL duration to whichever coach was in charge at that reign's START
    # (the season of the game that won it, per change_index), not a
    # game-by-game split across a coaching change mid-reign -- most reigns
    # are short enough that this is the reign's coach in every practical
    # sense, and the alternative (prorating a reign across coaches) adds a
    # lot of complexity for a card that's meant to be a fun leaderboard,
    # not a rigorous attribution. A reign whose team/season CFBD has no
    # coach on file for (belt_data/coaches.json, from fetch_coaches.py --
    # entirely optional) is simply left out, same as every other
    # optional/partial data source on this site. ----
    coach_days = {}
    coach_teams = {}
    coach_reign_count = {}
    if coaches:
        for r in reigns:
            g = change_index.get((r["start_date"], r["team"]))
            season = g["season"] if g else None
            if season is None:
                continue
            team_seasons = coaches.get(r["team"])
            if not team_seasons:
                continue
            coach = next((s["coach"] for s in team_seasons if s["year"] == season), None)
            if not coach:
                continue
            coach_days[coach] = coach_days.get(coach, 0) + reign_duration_days(r, today)
            coach_teams.setdefault(coach, set()).add(r["team"])
            coach_reign_count[coach] = coach_reign_count.get(coach, 0) + 1
    most_days_by_coach = sorted(coach_days.items(), key=lambda kv: kv[1], reverse=True)[:5]
    coach_rows = ""
    for i, (coach, days) in enumerate(most_days_by_coach, 1):
        teams = sorted(coach_teams[coach])
        n = coach_reign_count[coach]
        swatch = team_swatch(teams[0]) if len(teams) == 1 else None
        href = f'teams/{team_slug(teams[0])}.html' if len(teams) == 1 else None
        sub = f'{" & ".join(esc(t) for t in teams)} &middot; {n} reign{"s" if n != 1 else ""}'
        coach_rows += _record_row(i, swatch, esc(coach), f'{days:,}', sub, href)

    # ---- most defenses in a single reign ----
    most_defended = sorted(reigns, key=lambda r: r.get("defenses", 0), reverse=True)[:5]
    most_defended_rows = ""
    for i, r in enumerate(most_defended, 1):
        is_current = r is reigns[-1]
        sub = fmt_date(r["start_date"])
        if is_current:
            sub += ' <span class="currentTag">current</span>'
        most_defended_rows += _record_row(i, team_swatch(r["team"]), esc(r["team"]),
                                           f'{r.get("defenses", 0)}', sub, start_game_href(r))

    # ---- biggest blowouts in any belt game ----
    def margin(g):
        h, a = (int(x) for x in g["score"].split("-"))
        return abs(h - a)
    blowouts = sorted(belt_games, key=margin, reverse=True)[:5]
    blowout_rows = ""
    for i, g in enumerate(blowouts, 1):
        h, a = (int(x) for x in g["score"].split("-"))
        winner = g["home"] if h > a else g["away"]
        loser = g["away"] if h > a else g["home"]
        win_score, lose_score = max(h, a), min(h, a)
        blowout_rows += _record_row(i, team_swatch(winner), f'{esc(winner)} over {esc(loser)}',
                                     f'+{margin(g)}', f'{win_score}&ndash;{lose_score} &middot; {fmt_date(g["date"])}',
                                     f'games/{g["game_id"]}.html')

    # ---- closest calls: narrowest defenses, narrowest upsets, biggest upsets ----
    defenses_only = [g for g in belt_games if g["holder"] and g["new_holder"] == g["holder"]]
    changes_only = [g for g in belt_games if g["holder"] and g["new_holder"] != g["holder"]]

    def close_call_row(rank, g, verb):
        h, a = (int(x) for x in g["score"].split("-"))
        tie = h == a
        value = "Tie" if tie else f'+{margin(g)}'
        return _record_row(
            rank, team_swatch(g["new_holder"]),
            f'{esc(g["new_holder"])} {"tied" if tie else verb} {esc(g["holder"])}',
            value, f'{max(h, a)}&ndash;{min(h, a)} &middot; {fmt_date(g["date"])}',
            f'games/{g["game_id"]}.html')

    narrowest_defense_rows = "".join(
        close_call_row(i, g, "over") for i, g in enumerate(sorted(defenses_only, key=margin)[:5], 1))
    narrowest_change_rows = "".join(
        close_call_row(i, g, "took it from") for i, g in enumerate(sorted(changes_only, key=margin)[:5], 1))
    biggest_upset_rows = "".join(
        close_call_row(i, g, "routed") for i, g in enumerate(sorted(changes_only, key=margin, reverse=True)[:5], 1))

    cards = [
        ("Total Days Held", "All-time, summed across every reign a program has had", total_days_rows),
        ("Longest Reigns", "By days holding the belt", longest_rows),
        ("Most Reigns", "By program, across all 158 years", most_reigns_rows),
        ("Most Defended", "Consecutive defenses in a single reign", most_defended_rows),
        ("Biggest Blowouts", "Largest margin of victory in any belt game", blowout_rows),
        ("Narrowest Defenses", "Closest the holder has come to losing it and didn't", narrowest_defense_rows),
        ("Narrowest Upsets", "The belt changed hands by the barest possible margin", narrowest_change_rows),
        ("Biggest Upsets", "The belt changed hands in an outright rout", biggest_upset_rows),
        ("Longest Droughts", "Programs that have held it before, and how long it's been", drought_rows),
    ]
    if coach_rows:
        cards.append(("Belt Held By Coach", "Total days held, all attributed to the coach at reign's start", coach_rows))
    cards_html = "".join(f'''
    <section class="recordCard">
      <h2>{esc(title)}</h2>
      <p class="recordCardSub">{esc(sub)}</p>
      <div class="recordList">{rows}</div>
    </section>''' for title, sub, rows in cards)

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Records — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'records')}

<main class="wrap">
  <p class="kicker pageKicker">Leaderboards</p>
  <h1 class="pageTitle">Records</h1>
  <p class="lede">Superlatives computed straight from the lineage &mdash; no editorial
    judgment, same as everything else on this site. Ties aren&rsquo;t broken; a
    program just short of the cutoff simply isn&rsquo;t shown.</p>
  <div class="chipRow" style="margin:6px 0 4px">
    <a class="chipLink" href="leaders.html">Belt-game career leaders</a>
    <a class="chipLink" href="heartbreak.html">The heartbreak list</a>
    <a class="chipLink" href="timeline.html">Every reign on one timeline</a>
    <a class="chipLink" href="rivalries/index.html">Rivalries</a>
  </div>

  <div class="recordsGrid">{cards_html}
  </div>
</main>

{site_footer('', 'Computed from the full belt lineage &mdash; recalculated fresh every run.')}
'''


# --------------------------------------------------------------------- stories

def _story_nav_footer(active_href=None):
    """Shared header/footer chrome for the stories hub + article pages."""
    header = site_header('', 'stories')
    footer = site_footer('', 'Every fact on this page is computed from the belt lineage, recalculated fresh every run.')
    return header, footer


def _reign_start_line(r, change_index):
    """One clause describing how a reign began, real data only -- no
    hardcoded team names or records, since which reign is longest/most
    defended can shift as the lineage grows."""
    if r.get("won_from"):
        won_score, lost_score = _reign_win_score(r, change_index)
        win_game = change_index.get((r["start_date"], r["team"]))
        if win_game and won_score is not None:
            return (f'took the belt from <a href="teams/{team_slug(r["won_from"])}.html">{esc(r["won_from"])}</a>, '
                    f'<a href="games/{win_game["game_id"]}.html">{won_score}&ndash;{lost_score}</a>, '
                    f'on {fmt_date(r["start_date"])}')
    return f'established the belt outright on {fmt_date(r["start_date"])}'


def generate_story_longest_reigns(lineage, belt_games):
    """A data-driven deep dive on the ten longest reigns in belt history,
    by days held -- one narrated chapter per reign, every date/score/
    opponent pulled live from the lineage so nothing here can go stale."""
    reigns = lineage["reigns"]
    today = date.today()
    change_index = build_change_game_index(belt_games)
    loss_index = build_loss_game_index(belt_games)

    longest = sorted(reigns, key=lambda r: reign_duration_days(r, today), reverse=True)[:10]
    top = longest[0]
    top_start, top_end = reign_dates(top, today)

    chapters = []
    for i, r in enumerate(longest, 1):
        start, end = reign_dates(r, today)
        duration = fmt_duration(start, end)
        is_current = r is reigns[-1]
        team_link = f'<a href="teams/{team_slug(r["team"])}.html">{esc(r["team"])}</a>'
        defenses = r.get("defenses", 0)
        defense_word = f'{defenses} time{"s" if defenses != 1 else ""}'
        start_clause = _reign_start_line(r, change_index)

        if is_current:
            close = (f'{team_link} still holds the belt today &mdash; {duration} and counting, with '
                     f'{defenses} defense{"s" if defenses != 1 else ""} logged so far and no end in sight yet.')
        else:
            loss_game = loss_index.get((r["end_date"], r["team"])) if r.get("lost_to") else None
            if loss_game:
                h, a = (int(x) for x in loss_game["score"].split("-"))
                their_score, our_score = (h, a) if loss_game["home"] == r["lost_to"] else (a, h)
                close = (f'The run ended on {fmt_date(r["end_date"])}, when '
                         f'<a href="teams/{team_slug(r["lost_to"])}.html">{esc(r["lost_to"])}</a> won it '
                         f'<a href="games/{loss_game["game_id"]}.html">{their_score}&ndash;{our_score}</a>, '
                         f'closing out {defense_word} on the line.')
            else:
                close = f'The reign ended on {fmt_date(r["end_date"])}.'

        chapters.append(f'''
  <article class="storyChapter">
    <h2><span class="storyRank">No. {i}</span> {esc(r["team"])} &mdash; {duration}</h2>
    <p>{team_link} {start_clause}. {close}</p>
  </article>''')

    lede = (f'Every belt reign since 1869, ranked by how long the holder kept it. The longest of '
            f'them all belongs to <a href="teams/{team_slug(top["team"])}.html">{esc(top["team"])}</a>, '
            f'who held on for {fmt_duration(top_start, top_end)} straight'
            f'{" and counting" if top is reigns[-1] else ""}. Here are the ten longest runs the belt has ever seen.')

    header, footer = _story_nav_footer()
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>The Longest Reigns in Belt History — The College Football Belt</title>
<meta name="description" content="A data-driven look at the ten longest reigns in College Football Belt history, ranked by days held.">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{header}

<main class="wrap storyArticle">
  <p class="storyKicker">Stories</p>
  <h1 class="pageTitle">The Longest Reigns in Belt History</h1>
  <p class="lede">{lede}</p>
  {"".join(chapters)}
  <p class="storyBackLink"><a href="stories.html">&larr; Back to Stories</a></p>
</main>

{footer}
'''


def generate_story_most_defended(lineage, belt_games):
    """A deep dive on the single most-defended reign in belt history --
    whichever program that actually is, computed fresh, never hardcoded.
    Cites the opening game, a spread of real defenses along the way, and
    however the reign actually ended (or that it's still ongoing)."""
    reigns = lineage["reigns"]
    today = date.today()
    change_index = build_change_game_index(belt_games)
    loss_index = build_loss_game_index(belt_games)

    top = max(reigns, key=lambda r: r.get("defenses", 0))
    defenses = top.get("defenses", 0)
    is_current = top is reigns[-1]
    start, end = reign_dates(top, today)
    duration = fmt_duration(start, end)
    team = top["team"]
    team_link = f'<a href="teams/{team_slug(team)}.html">{esc(team)}</a>'

    # Every belt game `team` defended during this specific reign, in order.
    reign_defenses = sorted(
        (g for g in belt_games
         if g["holder"] == team and g["new_holder"] == team
         and g["date"] >= top["start_date"]
         and (not top.get("end_date") or g["date"] <= top["end_date"])),
        key=lambda g: g["date"])

    def defense_line(g):
        h, a = (int(x) for x in g["score"].split("-"))
        our_score, their_score = (h, a) if g["home"] == team else (a, h)
        return (f'<a href="games/{g["game_id"]}.html">beat {esc(g["opponent"])} '
                f'{our_score}&ndash;{their_score} on {fmt_date(g["date"])}</a>')

    sample_html = ""
    if reign_defenses:
        picks = [reign_defenses[0]]
        if len(reign_defenses) > 2:
            picks.append(reign_defenses[len(reign_defenses) // 2])
        if len(reign_defenses) > 1:
            picks.append(reign_defenses[-1])
        items = "".join(f'<li>{defense_line(g)}</li>' for g in picks)
        sample_html = f'<ul class="storyList">{items}</ul>'

    start_clause = _reign_start_line(top, change_index)

    if is_current:
        close = (f'That defense count is still climbing &mdash; {team_link} has now held the belt for '
                 f'{duration} with no successful challenge yet.')
    else:
        loss_game = loss_index.get((top["end_date"], team)) if top.get("lost_to") else None
        if loss_game:
            h, a = (int(x) for x in loss_game["score"].split("-"))
            their_score, our_score = (h, a) if loss_game["home"] == top["lost_to"] else (a, h)
            close = (f'The streak finally snapped on {fmt_date(top["end_date"])}, when '
                     f'<a href="teams/{team_slug(top["lost_to"])}.html">{esc(top["lost_to"])}</a> won it '
                     f'<a href="games/{loss_game["game_id"]}.html">{their_score}&ndash;{our_score}</a>, after '
                     f'{defenses} defense{"s" if defenses != 1 else ""} across {duration}.')
        else:
            close = f'The reign ended on {fmt_date(top["end_date"])}, after {defenses} defenses.'

    header, footer = _story_nav_footer()
    title = f'How {esc(team)} Defended the Belt {defenses} Times'
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>{title} — The College Football Belt</title>
<meta name="description" content="The most-defended reign in College Football Belt history: {esc(team)} held the belt through {defenses} defenses.">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{header}

<main class="wrap storyArticle">
  <p class="storyKicker">Stories</p>
  <h1 class="pageTitle">{title}</h1>
  <p class="lede">No program has defended the belt more times without losing it than {team_link}, whose
    reign starting {fmt_date(top["start_date"])} is the most-defended single run in the belt&rsquo;s
    {today.year - 1869}-year history.</p>

  <p class="storyStat"><span class="storyStatN">{defenses}</span> defenses in a row{"" if is_current else f', over {duration}'}</p>

  <article class="storyChapter">
    <h2>How it started</h2>
    <p>{team_link} {start_clause}.</p>
  </article>

  <article class="storyChapter">
    <h2>Along the way</h2>
    <p>A sample of the {defenses} defense{"s" if defenses != 1 else ""} {team_link} racked up before anyone
      could take it back:</p>
    {sample_html}
  </article>

  <article class="storyChapter">
    <h2>{"Still going" if is_current else "How it ended"}</h2>
    <p>{close}</p>
  </article>

  <p class="storyBackLink"><a href="stories.html">&larr; Back to Stories</a></p>
</main>

{footer}
'''


STORIES = [
    # (file, title, blurb, section) -- {team}/{n} placeholders are filled from
    # the live most-defended reign so the card never goes stale
    ("story-longest-reigns.html", "The Longest Reigns in Belt History",
     "The ten longest-held reigns in belt history, ranked and narrated.", "Reigns"),
    ("story-most-defended.html", "How {team} Defended the Belt {n} Times",
     "The single most-defended reign the belt has ever seen, chapter by chapter.", "Reigns"),
    ("story-one-week-wonders.html", "One-Week Wonders",
     "The reigns that were over the Saturday after they began, and the programs that keep doing it.", "Reigns"),
    ("story-giant-killers.html", "Giant Killers",
     "Who ended the longest reigns, with what score, and what they did with the belt afterward.", "Reigns"),
    ("story-shutouts.html", "Not a Single Point",
     "The reigns that won the belt and every defense by shutout, and the last time anyone took it without allowing a score.", "Reigns"),
    ("story-wire-to-wire.html", "Wire to Wire",
     "The seasons one program carried the belt in, defended it every week, and carried it out.", "Seasons"),
    ("story-wildest-seasons.html", "The Wildest Seasons",
     "The years the belt could not sit still: the most title changes in a season, and the chains it followed.", "Seasons"),
    ("story-opening-day.html", "The Opening-Day Curse",
     "Reigns that survived an entire offseason and died in the first game back, and whether the curse is real.", "Seasons"),
    ("story-bowl-season.html", "Bowl Season",
     "Every postseason game the belt has been on the line for, and the reigns that started in January.", "Seasons"),
    ("story-new-years.html", "New Year's Holders",
     "Who has the belt when the calendar turns, and how often they still have it a year later.", "Seasons"),
    ("story-long-way-back.html", "The Long Way Back",
     "The longest waits between one reign and a program's next, and the quickest returns.", "Programs"),
    ("story-vanished.html", "The Vanished",
     "The programs that held the belt and then dropped out of its story entirely, some of them the dynasties that built it.", "Programs"),
    ("story-coast-to-coast.html", "Coast to Coast",
     "How the belt left the Northeast, when it first reached each region, and where it has never been.", "Eras, places and the rule"),
    ("story-four-ages.html", "The Four Ages of the Belt",
     "Belt history cut into four eras: who owned each one, how long reigns lasted, and how often it moved.", "Eras, places and the rule"),
    ("story-changing-hands.html", "Where the Belt Changes Hands",
     "Home or road, one point or a mile, regular season or bowl: how title changes actually happen.", "Eras, places and the rule"),
    ("story-ties.html", "When Nobody Won",
     "Every tie with the belt on the line, the reigns a tie saved, and why the holder keeps it on a draw.", "Eras, places and the rule"),
]


def generate_stories_hub(lineage, belt_games):
    """The Stories index -- a small, growing collection of data-driven
    long-form pieces (as opposed to the reference tables everywhere else
    on the site). STORIES above is deliberately hand-listed rather than
    auto-discovered, so a future new story just needs one line added here
    and one generate_story_* function; the {team}/{n} placeholders in its
    title are filled in from the live top-defended reign so the card text
    never goes stale even if a future reign overtakes it."""
    reigns = lineage["reigns"]
    top_defended = max(reigns, key=lambda r: r.get("defenses", 0))

    sections = []
    for section in dict.fromkeys(sec for _, _, _, sec in STORIES):
        cards = []
        for href, title_tpl, desc, sec in STORIES:
            if sec != section:
                continue
            title = title_tpl.format(team=esc(top_defended["team"]), n=top_defended.get("defenses", 0))
            cards.append(f'''
    <a class="storyCard" href="{href}">
      <h3>{title}</h3>
      <p>{esc(desc)}</p>
      <span class="storyCardLink">Read the story &rarr;</span>
    </a>''')
        sections.append(f'''
  <section class="storySection">
    <div class="sectionHead"><h2>{esc(section)}</h2><span class="sectionMeta">{len(cards)} {_plural(len(cards), "story", "stories")}</span></div>
    <div class="storyGrid">{"".join(cards)}
    </div>
  </section>''')

    header, footer = _story_nav_footer()
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Stories — The College Football Belt</title>
<meta name="description" content="{len(STORIES)} data-driven longreads from the College Football Belt's lineage: the longest reigns, the wildest seasons, the ties, the bowl games and the programs the belt forgot.">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{header}

<main class="wrap">
  <p class="kicker pageKicker">Data-driven longreads</p>
  <h1 class="pageTitle">Stories</h1>
  <p class="lede">The reference tables tell you what happened. These {len(STORIES)} pieces dig into the more
    interesting corners of belt history in depth &mdash; every number in them is computed from the same lineage
    the rest of the site runs on, so they rewrite themselves as the belt moves.</p>
{"".join(sections)}
</main>

{footer}
'''


# ------------------------------------------------------------------ team pages

def _team_reign_row(r, today, change_index, loss_index, is_current, reign_no=None):
    start, end = reign_dates(r, today)
    dates = f'{fmt_date(r["start_date"])} &ndash; {"present" if is_current else fmt_date(r["end_date"])}'
    duration = fmt_duration(start, end)
    if reign_no:
        duration = f'<a href="../reigns/{reign_no}.html" title="Reign #{reign_no} of the belt, game by game">{duration}</a>'
    defenses = r.get("defenses", 0)

    won_score, lost_score = _reign_win_score(r, change_index)
    won_from = r.get("won_from")
    if won_from and won_score is not None:
        win_game = change_index.get((r["start_date"], r["team"]))
        won_line = (f'Won from <a href="../games/{win_game["game_id"]}.html">'
                    f'{esc(won_from)}, {won_score}&ndash;{lost_score}</a>')
    else:
        won_line = "Established the belt"

    lost_line = ""
    if not is_current and r.get("lost_to"):
        loss_game = loss_index.get((r["end_date"], r["team"]))
        if loss_game:
            h, a = (int(x) for x in loss_game["score"].split("-"))
            my_score, their_score = (h, a) if loss_game["home"] == r["team"] else (a, h)
            lost_line = (f'Lost to <a href="../games/{loss_game["game_id"]}.html">'
                         f'{esc(r["lost_to"])}, {their_score}&ndash;{my_score}</a>')

    current_badge = ' <span class="currentTag">current</span>' if is_current else ""
    return f'''
    <div class="teamReignCard">
      <div class="teamReignHead">
        <span class="teamReignDates">{dates}{current_badge}</span>
        <span class="teamReignDuration tabular">{duration}</span>
      </div>
      <div class="teamReignMeta">
        <span>{won_line}</span>
        {f'<span>{lost_line}</span>' if lost_line else ''}
        <span>{defenses} defense{"s" if defenses != 1 else ""}</span>
      </div>
    </div>'''


def generate_team_pages(lineage, colors, belt_games, teams_dir):
    """One page per program that has ever held the belt -- every reign it
    ever had, newest first, how each one started and (if it's over) ended.
    Every team that's ever HELD the belt gets a page here; a team that's
    only ever challenged and lost doesn't have reigns to show, so it
    doesn't get a page -- same "no editorial judgment" computed approach
    as the rest of the site."""
    reigns = lineage["reigns"]
    today = date.today()
    change_index = build_change_game_index(belt_games)
    loss_index = build_loss_game_index(belt_games)
    current_reign = reigns[-1]

    by_team = {}
    for r in reigns:
        by_team.setdefault(r["team"], []).append(r)

    os.makedirs(teams_dir, exist_ok=True)
    written = 0
    for team, team_reigns in by_team.items():
        team_reigns_sorted = sorted(team_reigns, key=lambda r: r["start_date"])
        total_days = sum(reign_duration_days(r, today) for r in team_reigns_sorted)
        total_defenses = sum(r.get("defenses", 0) for r in team_reigns_sorted)
        is_holder_now = team_reigns_sorted[-1] is current_reign

        primary, alt = team_color(colors, team)
        ink, accent = panel_colors(primary, alt)

        reign_no = {id(r): i for i, r in enumerate(reigns, 1)}
        rows_html = "".join(
            _team_reign_row(r, today, change_index, loss_index, r is current_reign, reign_no.get(id(r)))
            for r in reversed(team_reigns_sorted))

        n = len(team_reigns_sorted)
        holder_line = (f'{esc(team)} currently holds the belt.' if is_holder_now else
                        f'{esc(team)} last held the belt {fmt_date(team_reigns_sorted[-1]["end_date"])}.')
        team_reign_sorted_last_year = "" if is_holder_now else team_reigns_sorted[-1]["end_date"][:4]

        team_share_desc = esc(f"{n} reign{'s' if n != 1 else ''}, {total_days:,} total days held, "
                               f"{total_defenses} total defense{'s' if total_defenses != 1 else ''}.")
        team_share_img = f"{SITE_URL}/team-share/{team_slug(team)}.png"
        team_page_url = f"{SITE_URL}/teams/{team_slug(team)}.html"

        page = f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>{esc(team)} — The College Football Belt</title>
<meta name="description" content="{team_share_desc}">
<meta property="og:title" content="{esc(team)} — The College Football Belt">
<meta property="og:description" content="{team_share_desc}">
<meta property="og:image" content="{team_share_img}">
<meta property="og:url" content="{team_page_url}">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(team)} — The College Football Belt">
<meta name="twitter:description" content="{team_share_desc}">
<meta name="twitter:image" content="{team_share_img}">
<link rel="stylesheet" href="../styles.css?v={STYLES_VERSION}">
{head_extras('../')}
{team_json_ld(team)}
<style>
  :root{{ --team:{primary}; --team-ink:{ink}; --team-accent:{accent}; }}
</style>

{site_header('../', None)}

<main class="wrap">
  <section class="teamPlate">
    <div class="teamPlateRow">
      {logo_chip(colors, team, 56)}
      <div>
        <p class="kicker">Program &middot; {"holds the belt now" if is_holder_now else "last held it " + team_reign_sorted_last_year}</p>
        <h1 class="pageTitle">{esc(team)}</h1>
      </div>
    </div>
    <div class="heroStats">
      <div><span class="n tabular">{n}</span><span class="l">Reign{"s" if n != 1 else ""}</span></div>
      <div><span class="n tabular">{total_days:,}</span><span class="l">Days held</span></div>
      <div><span class="n tabular">{total_defenses}</span><span class="l">Defense{"s" if total_defenses != 1 else ""}</span></div>
    </div>
  </section>
  <p class="lede" style="margin-top:18px">{holder_line}
    <a class="posterLink" href="../posters/{team_slug(team)}.png">Download a poster of this history &darr;</a></p>

  <div class="sectionHead">
    <span class="tag">Every reign</span>
    <h2>{esc(team)}&rsquo;s belt history</h2>
    <a class="sectionLink" href="../compare.html">Compare with another team &rarr;</a>
  </div>
  <div class="teamReignList">{rows_html}
  </div>
</main>

{site_footer('../', 'Every reign computed from the College Football Data API.')}
'''
        with open(os.path.join(teams_dir, f"{team_slug(team)}.html"), "w", encoding="utf-8") as f:
            f.write(page)
        written += 1

    return written, [team_slug(t) for t in by_team]


# ------------------------------------------------------------ player pages

def _format_stat_line(line):
    return ", ".join(f"{c} {v}" for c, v in line.items())


def _player_game_row(g, team, categories):
    opp = g["away"] if team == g["home"] else g["home"]
    cats_html = "".join(
        f'<div class="playerGameCat"><span class="catLabel">{esc(category_label(cat))}</span>'
        f'<span class="catLine">{esc(_format_stat_line(line))}</span></div>'
        for cat, line in categories.items())
    return f'''
    <tr>
      <td><a href="../games/{g["game_id"]}.html">{esc(fmt_date(g["date"]))}</a></td>
      <td>{esc(team)} vs {esc(opp)}</td>
      <td class="tabular">{esc(g["score"])}</td>
      <td>{cats_html}</td>
    </tr>'''


def collect_players(belt_games, details):
    """player_id -> {"name", "teams", "games": [(game, team, {cat: {col: val}})]}
    for every player CFBD gave a stable athlete id to in a belt-game box
    score. Keyed by id (not name) since two players can share a name; a
    player without an id is skipped (their name just isn't linked)."""
    players = {}
    for g in belt_games:
        ps = (details.get(str(g["game_id"])) or {}).get("player_stats")
        if not ps:
            continue
        # player_id -> {"name":, "team":, "categories": {cat: {col: val}}}
        per_game = {}
        for cat_name, cat in ps.items():
            columns = cat.get("columns", [])
            for row in cat.get("rows", []):
                pid = row.get("player_id")
                if not pid:
                    continue
                entry = per_game.setdefault(pid, {
                    "name": row.get("player", ""), "team": row.get("team", ""),
                    "categories": {},
                })
                stats = row.get("stats", {})
                line = {c: stats[c] for c in columns if stats.get(c) not in (None, "")}
                if line:
                    entry["categories"][cat_name] = line

        for pid, entry in per_game.items():
            if not entry["categories"]:
                continue
            p = players.setdefault(pid, {"name": entry["name"], "teams": set(), "games": []})
            p["teams"].add(entry["team"])
            p["games"].append((g, entry["team"], entry["categories"]))
    return players


def generate_player_pages(belt_games, details, players_dir):
    """One page per player CFBD gave a stable athlete id to in a belt
    game's box score (2003 onward -- see render_player_stats()) -- their
    full recorded stat line in every belt game they've appeared in, newest
    first (see collect_players for the keying). Returns (written, slugs)."""
    os.makedirs(players_dir, exist_ok=True)
    players = collect_players(belt_games, details)
    PLAYER_SLUG_BY_ID.update({pid: player_slug(pid, p["name"]) for pid, p in players.items()})

    written = 0
    slugs = []
    for pid, p in players.items():
        slug = player_slug(pid, p["name"])
        slugs.append(slug)
        games_sorted = sorted(p["games"], key=lambda t: t[0]["date"], reverse=True)
        n = len(games_sorted)
        teams_bit = " / ".join(sorted(p["teams"]))
        rows_html = "".join(_player_game_row(g, team, cats) for g, team, cats in games_sorted)

        page = f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>{esc(p["name"])} — The College Football Belt</title>
<meta name="description" content="{esc(p['name'])}&#8217;s recorded stat line in every College Football Belt game on file.">
<link rel="stylesheet" href="../styles.css?v={STYLES_VERSION}">
{head_extras('../')}

{site_header('../', None)}

<main class="wrap">
  <p class="kicker pageKicker">Player</p>
  <h1 class="pageTitle">{esc(p["name"])}</h1>
  <p class="lede">{esc(teams_bit)} &middot; a recorded stat line in {n} belt game{"s" if n != 1 else ""} on file.</p>

  <table class="playerGameLog">
    <thead><tr><th>Date</th><th>Matchup</th><th>Score</th><th>Stat line</th></tr></thead>
    <tbody>{rows_html}
    </tbody>
  </table>
  <p class="noteBox">Only covers belt games from 2003 onward, and only the stat categories CFBD recorded for this player in each one &mdash; see a game&rsquo;s own page for its full box score.</p>
</main>

{site_footer('../', 'Every reign computed from the College Football Data API.')}
'''
        with open(os.path.join(players_dir, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(page)
        written += 1

    return written, slugs


# --------------------------------------------------------------- belt map

HISTORICAL_DIR = "historical_data"
STATE_SHAPES_PATH = os.path.join(HISTORICAL_DIR, "us_state_shapes.json")

# Standard USGS Albers Equal-Area Conic parameters for the contiguous US --
# the same standard parallels/origin behind EPSG:5070 -- applied on a unit
# sphere. That's plenty accurate for a small decorative map; it's not meant
# for real measurement. Alaska and Hawaii are skipped rather than inset,
# since no belt-holding program has ever been based in either.
_ALBERS_PHI1 = math.radians(29.5)
_ALBERS_PHI2 = math.radians(45.5)
_ALBERS_PHI0 = math.radians(23.0)
_ALBERS_LON0 = math.radians(-96.0)
_ALBERS_N = (math.sin(_ALBERS_PHI1) + math.sin(_ALBERS_PHI2)) / 2
_ALBERS_C = math.cos(_ALBERS_PHI1) ** 2 + 2 * _ALBERS_N * math.sin(_ALBERS_PHI1)
_ALBERS_RHO0 = math.sqrt(_ALBERS_C - 2 * _ALBERS_N * math.sin(_ALBERS_PHI0)) / _ALBERS_N


def albers_project(lon, lat):
    phi = math.radians(lat)
    lam = math.radians(lon)
    theta = _ALBERS_N * (lam - _ALBERS_LON0)
    rho = math.sqrt(_ALBERS_C - 2 * _ALBERS_N * math.sin(phi)) / _ALBERS_N
    return rho * math.sin(theta), _ALBERS_RHO0 - rho * math.cos(theta)


def load_state_shapes():
    if not os.path.exists(STATE_SHAPES_PATH):
        return None
    with open(STATE_SHAPES_PATH) as f:
        return json.load(f)


def _state_rings(entry):
    """Split a state's flat lons/lats (NaN-separated for exclaves like Long
    Island) into a list of point-lists."""
    rings, ring = [], []
    for lon, lat in zip(entry["lons"], entry["lats"]):
        if lon is None or lat is None or lon != lon or lat != lat:  # NaN check, no numpy needed
            if ring:
                rings.append(ring)
                ring = []
            continue
        ring.append((lon, lat))
    if ring:
        rings.append(ring)
    return rings


def build_state_paths(shapes):
    """Project every state's ring(s) through Albers, then scale/flip the
    whole set into one shared SVG coordinate space. Returns
    ({abbr: {"name":..., "d": "<path d>"}}, (viewbox_w, viewbox_h))."""
    projected = {}
    all_x, all_y = [], []
    for abbr, entry in shapes.items():
        rings = []
        for ring in _state_rings(entry):
            proj_ring = [albers_project(lon, lat) for lon, lat in ring]
            rings.append(proj_ring)
            all_x.extend(x for x, _ in proj_ring)
            all_y.extend(y for _, y in proj_ring)
        projected[abbr] = rings

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    pad = 0.02 * max(max_x - min_x, max_y - min_y)
    min_x, max_x = min_x - pad, max_x + pad
    min_y, max_y = min_y - pad, max_y + pad

    width, height = 1000.0, 620.0
    scale = min(width / (max_x - min_x), height / (max_y - min_y))
    off_x = (width - (max_x - min_x) * scale) / 2
    off_y = (height - (max_y - min_y) * scale) / 2

    def to_svg(x, y):
        sx = (x - min_x) * scale + off_x
        sy = height - ((y - min_y) * scale + off_y)  # SVG y grows downward
        return sx, sy

    out = {}
    for abbr, rings in projected.items():
        parts = []
        for ring in rings:
            pts = [to_svg(x, y) for x, y in ring]
            parts.append("M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts) + " Z")
        out[abbr] = {"name": shapes[abbr]["name"], "d": " ".join(parts)}
    return out, (width, height)


def build_state_belt_history(lineage, colors):
    """Every reign, joined against team_colors.json's "state" field, into a
    per-state tally: how many reigns started there, and which team(s)."""
    by_state = {}
    for r in lineage["reigns"]:
        team = r["team"]
        state = (colors.get(team) or {}).get("state")
        if not state:
            continue
        entry = by_state.setdefault(state, {"reigns": 0, "teams": {}})
        entry["reigns"] += 1
        year = int(r["start_date"][:4])
        t = entry["teams"].setdefault(team, {"count": 0, "first": year, "last": year})
        t["count"] += 1
        t["first"] = min(t["first"], year)
        t["last"] = max(t["last"], year)
    return by_state


def _team_bits(teams):
    return ", ".join(
        f'{esc(name)} ({t["count"]}&times;)' if t["count"] > 1 else esc(name)
        for name, t in sorted(teams.items(), key=lambda kv: -kv[1]["count"]))


def build_belt_journey(lineage, colors):
    """Every reign that has a resolvable state, in chronological order, as
    {abbr, team, start, end, ongoing} -- the same state field
    build_state_belt_history tallies, just kept as a sequence instead of a
    running total. Feeds the map page's animated scrubber."""
    today_iso = date.today().isoformat()
    timeline = []
    for r in lineage["reigns"]:
        abbr = (colors.get(r["team"]) or {}).get("state")
        if not abbr:
            continue
        timeline.append({
            "abbr": abbr, "team": r["team"], "start": r["start_date"],
            "end": r.get("end_date") or today_iso, "ongoing": r.get("end_date") is None,
        })
    return timeline


def generate_map_page(lineage, colors):
    """A US map shaded by how many belt reigns have started in each state
    -- every figure computed straight from lineage.json + team_colors.json,
    no new API calls. Returns None (and build_site.py skips writing the
    page) if historical_data/us_state_shapes.json isn't present."""
    shapes = load_state_shapes()
    if not shapes:
        return None
    paths, (vb_w, vb_h) = build_state_paths(shapes)
    by_state = build_state_belt_history(lineage, colors)
    max_reigns = max((v["reigns"] for v in by_state.values()), default=0)
    journey = build_belt_journey(lineage, colors)

    def tint_class(n):
        if n == 0 or max_reigns == 0:
            return ""
        frac = n / max_reigns
        if frac > 0.66 or max_reigns <= 1:
            return " mapState--3"
        if frac > 0.33:
            return " mapState--2"
        return " mapState--1"

    path_svg = []
    for abbr in sorted(paths):
        info = paths[abbr]
        st = by_state.get(abbr)
        n = st["reigns"] if st else 0
        title = esc(info["name"])
        if st:
            title += f": {_team_bits(st['teams'])}"
        path_svg.append(f'<path class="mapState{tint_class(n)}" data-abbr="{abbr}" '
                         f'd="{info["d"]}"><title>{title}</title></path>')

    legend_rows = ""
    for abbr, st in sorted(by_state.items(), key=lambda kv: -kv[1]["reigns"]):
        state_name = paths.get(abbr, {}).get("name", abbr)
        n = st["reigns"]
        legend_rows += f'''
    <div class="mapLegendRow">
      <span class="mapLegendState"><a href="states/{abbr.lower()}.html">{esc(state_name)}</a></span>
      <span class="mapLegendCount tabular">{n} reign{"s" if n != 1 else ""}</span>
      <span class="mapLegendTeams">{_team_bits(st["teams"])}</span>
    </div>'''

    n_states = len(by_state)

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Map — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'map')}

<main class="wrap">
  <p class="kicker pageKicker">Everywhere the belt has lived</p>
  <h1 class="pageTitle">Everywhere the Belt Has Lived</h1>
  <p class="lede">{n_states} state{"s" if n_states != 1 else ""} ha{"ve" if n_states != 1 else "s"} produced
    a College Football Belt holder since 1869. Shading shows how many separate reigns
    started there &mdash; darker means more; hover a state (or check the list below) for who.</p>

  <div class="mapWrap" id="mapWrap">
    <svg class="mapSvg" viewBox="0 0 {vb_w:.0f} {vb_h:.0f}" role="img" aria-label="Map of US states that have held the College Football Belt">{"".join(path_svg)}
    </svg>
  </div>

  <div class="journeyBar">
    <button class="journeyBtn" id="journeyPrev" type="button" aria-label="Previous reign">&larr;</button>
    <button class="journeyBtn" id="journeyPlay" type="button">&#9654; Play the Belt&rsquo;s Journey</button>
    <button class="journeyBtn" id="journeyNext" type="button" aria-label="Next reign">&rarr;</button>
    <input class="journeySlider" id="journeySlider" type="range" min="0" value="0" aria-label="Reign in belt history">
    <span class="journeyLabel" id="journeyLabel"></span>
  </div>
  <script type="application/json" id="beltJourneyData">{json.dumps(journey, ensure_ascii=False)}</script>
  <script>
  (function(){{
    var dataEl = document.getElementById('beltJourneyData');
    if (!dataEl) return;
    var timeline;
    try {{ timeline = JSON.parse(dataEl.textContent); }} catch (e) {{ return; }}
    if (!timeline.length) return;

    var mapWrap = document.getElementById('mapWrap');
    var slider = document.getElementById('journeySlider');
    var label = document.getElementById('journeyLabel');
    var playBtn = document.getElementById('journeyPlay');
    var prevBtn = document.getElementById('journeyPrev');
    var nextBtn = document.getElementById('journeyNext');
    var pathsByAbbr = {{}};
    Array.prototype.slice.call(document.querySelectorAll('.mapSvg path[data-abbr]')).forEach(function(p){{
      var a = p.getAttribute('data-abbr');
      (pathsByAbbr[a] = pathsByAbbr[a] || []).push(p);
    }});

    slider.max = String(timeline.length - 1);
    var timer = null;
    var active = false;

    function fmtDate(iso) {{
      var d = new Date(iso + 'T00:00:00Z');
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleDateString(undefined, {{ year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' }});
    }}

    function render(idx) {{
      var r = timeline[idx];
      if (!active) {{
        active = true;
        mapWrap.classList.add('journeyMode');
      }}
      Array.prototype.slice.call(mapWrap.querySelectorAll('.mapState--active')).forEach(function(p){{
        p.classList.remove('mapState--active');
      }});
      (pathsByAbbr[r.abbr] || []).forEach(function(p){{ p.classList.add('mapState--active'); }});
      var range = r.ongoing ? (fmtDate(r.start) + ' — present') : (fmtDate(r.start) + ' – ' + fmtDate(r.end));
      label.textContent = r.team + ' · ' + range;
      slider.value = String(idx);
    }}

    function stop() {{
      if (timer) {{ clearInterval(timer); timer = null; }}
      playBtn.innerHTML = '&#9654; Play the Belt&rsquo;s Journey';
    }}

    function step(delta) {{
      var next = Math.min(timeline.length - 1, Math.max(0, parseInt(slider.value, 10) + delta));
      render(next);
      if (next === timeline.length - 1 || next === 0) stop();
    }}

    slider.addEventListener('input', function(){{ stop(); render(parseInt(slider.value, 10)); }});
    prevBtn.addEventListener('click', function(){{ stop(); step(-1); }});
    nextBtn.addEventListener('click', function(){{ stop(); step(1); }});
    playBtn.addEventListener('click', function(){{
      if (timer) {{ stop(); return; }}
      if (parseInt(slider.value, 10) >= timeline.length - 1) render(0);
      playBtn.innerHTML = '&#10074;&#10074; Pause';
      timer = setInterval(function(){{ step(1); }}, 650);
    }});

    label.textContent = timeline.length + ' reigns · drag the slider or press play';
  }})();
  </script>

  <div class="mapLegend">{legend_rows}
  </div>
</main>

{site_footer('', "Every reign's state comes from the belt-holding team's CFBD-listed home state.")}
'''


# --------------------------------------------------------- badge / embed / compare

def generate_badge_svg(lineage, colors):
    """A tiny embeddable SVG badge naming the current belt holder --
    regenerated fresh on every pipeline run, so a fan site's plain <img>
    tag always shows who holds the belt right now with zero work (and zero
    API calls) on their end. Shields.io-style two-tone pill."""
    holder = lineage["reigns"][-1]["team"]
    primary, alt = team_color(colors, holder)
    ink, accent = panel_colors(primary, alt)
    label, value = "COLLEGE FOOTBALL BELT", holder.upper()
    label_w = 148
    value_w = max(84, 20 + len(value) * 7)
    total_w = label_w + value_w
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="24" \
role="img" aria-label="College Football Belt: {esc(holder)}">
  <linearGradient id="sheen" x2="0" y2="100%">
    <stop offset="0" stop-color="#fff" stop-opacity=".09"/>
    <stop offset="1" stop-opacity=".09"/>
  </linearGradient>
  <clipPath id="round"><rect width="{total_w}" height="24" rx="4" fill="#fff"/></clipPath>
  <g clip-path="url(#round)">
    <rect width="{label_w}" height="24" fill="#211a12"/>
    <rect x="{label_w}" width="{value_w}" height="24" fill="{primary}"/>
    <rect width="{total_w}" height="24" fill="url(#sheen)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{label_w / 2:.0f}" y="16" fill="#e7e2d5">{esc(label)}</text>
    <text x="{label_w + value_w / 2:.0f}" y="16" fill="{ink}" font-weight="bold">{esc(value)}</text>
  </g>
</svg>'''


def generate_embed_page(lineage, colors):
    """A page for other sites to grab a copy-pasteable 'current belt
    holder' badge -- plain HTML and Markdown snippets in read-only text
    boxes (select-all-and-copy; no clipboard JS/permissions needed). Free
    backlinks/exposure for the site at essentially no build cost, since
    it's just serving badge.svg, which build_site.py already regenerates
    every run."""
    holder = lineage["reigns"][-1]["team"]
    html_snippet = (f'<a href="{SITE_URL}/"><img src="{SITE_URL}/badge.svg" '
                     f'alt="College Football Belt: current holder"></a>')
    md_snippet = f'[![College Football Belt]({SITE_URL}/badge.svg)]({SITE_URL}/)'

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Embed — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'embed')}

<main class="wrap">
  <p class="kicker pageKicker">Free badge</p>
  <h1 class="pageTitle">Embed the Belt</h1>
  <p class="lede">Run a fan site, blog, or forum signature? Drop this badge in and it&rsquo;ll always
    show who currently holds the belt &mdash; it&rsquo;s a live image, regenerated every time the
    site updates, so there&rsquo;s nothing to keep in sync yourself.</p>

  <div class="embedPreview">
    <img src="badge.svg" alt="College Football Belt: {esc(holder)}" width="232" height="24">
  </div>

  <p class="embedLabel">HTML</p>
  <textarea class="embedCode" rows="2" readonly aria-label="HTML embed code" onclick="this.select()">{esc(html_snippet)}</textarea>

  <p class="embedLabel">Markdown</p>
  <textarea class="embedCode" rows="2" readonly aria-label="Markdown embed code" onclick="this.select()">{esc(md_snippet)}</textarea>

  <p class="embedLabel">Direct badge URL</p>
  <textarea class="embedCode" rows="1" readonly aria-label="Badge image URL" onclick="this.select()">{SITE_URL}/badge.svg</textarea>
</main>

{site_footer('', 'The badge is a plain SVG, rebuilt from live data on every deploy &mdash; no tracking, no script tag required.')}
'''


def generate_privacy_page():
    """A plain-language privacy policy -- required by Google before it will
    approve an AdSense application (and generally good practice regardless).
    Written to reflect the site's actual, current data practices rather
    than a boilerplate template: no accounts/logins exist anywhere on the
    site, so there's no user data collection to describe beyond page-view
    analytics; the Advertising section is the only part that changes
    behavior based on ADSENSE_PUBLISHER_ID, so it always describes reality
    at build time instead of a forward-looking promise."""
    if ADSENSE_PUBLISHER_ID:
        ads_section = '''<p>This site displays advertising served by Google AdSense. Google and its advertising
      partners may use cookies, device identifiers, or similar technologies to show ads based on your
      visits to this and other websites, and to measure ad performance. This site does not control
      how those third parties use that information.</p>
    <p>You can see and adjust how Google personalizes ads to you at
      <a href="https://adssettings.google.com/" rel="noopener">adssettings.google.com</a>, and read
      Google's own explanation of how it uses data from sites that use its services at
      <a href="https://policies.google.com/technologies/partner-sites" rel="noopener">policies.google.com/technologies/partner-sites</a>.
      If you are in the EU/EEA or UK, you may be shown a consent prompt controlling ad
      personalization before ads appear.</p>'''
    else:
        ads_section = '''<p>This site does not currently display third-party advertising. If that changes, this
      section will be updated to disclose exactly what's shown and what data it involves.</p>'''

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Privacy Policy — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', None)}

<main class="wrap">
  <p class="kicker pageKicker">Legal</p>
  <h1 class="pageTitle">Privacy Policy</h1>
  <p class="lede">This site is a hobby project with no accounts, no logins, and nothing to sign up
    for &mdash; there isn&rsquo;t much data to collect in the first place. Here&rsquo;s exactly
    what happens anyway.</p>

  <section>
    <div class="sectionHead">
      <span class="tag">01</span>
      <span class="rule"></span>
      <h2>Information We Collect</h2>
    </div>
    <div class="proseBlock">
      <p>Browsing this site doesn&rsquo;t require creating an account or submitting any personal
        information. The only place you can voluntarily send us anything is the Contact link in
        the footer, which opens your own email client &mdash; whatever you choose to write there
        is between you and us, sent directly to hello@collegefootballbelt.com.</p>
      <p>This site is hosted on GitHub Pages, which (like any web host) automatically logs basic
        technical request data &mdash; IP address, browser type, page requested &mdash; for
        security and operational purposes. That logging is handled entirely by GitHub, not by
        this site; see <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement" rel="noopener">GitHub&rsquo;s own privacy statement</a> for details.</p>
    </div>
  </section>

  <section>
    <div class="sectionHead">
      <span class="tag">02</span>
      <span class="rule"></span>
      <h2>Analytics</h2>
    </div>
    <div class="proseBlock">
      <p>Page-view counts are collected using <a href="https://www.goatcounter.com/" rel="noopener">GoatCounter</a>,
        a privacy-focused analytics tool that does not use cookies and does not track individuals
        across sites. It records aggregate numbers &mdash; which pages get visited, roughly how
        often &mdash; and nothing that identifies you personally.</p>
    </div>
  </section>

  <section>
    <div class="sectionHead">
      <span class="tag">03</span>
      <span class="rule"></span>
      <h2>Advertising</h2>
    </div>
    <div class="proseBlock">
      {ads_section}
    </div>
  </section>

  <section>
    <div class="sectionHead">
      <span class="tag">04</span>
      <span class="rule"></span>
      <h2>Third-Party Links &amp; Data</h2>
    </div>
    <div class="proseBlock">
      <p>Game data, scores, and statistics shown across this site are sourced from the
        <a href="https://collegefootballdata.com/" rel="noopener">College Football Data API</a>.
        Links to other sites (news sources, social media, the API provider) are provided for
        convenience; once you leave this site, that site&rsquo;s own privacy policy applies.</p>
    </div>
  </section>

  <section>
    <div class="sectionHead">
      <span class="tag">05</span>
      <span class="rule"></span>
      <h2>Changes to This Policy</h2>
    </div>
    <div class="proseBlock">
      <p>This page may be updated from time to time as the site changes &mdash; for example, if
        advertising or new features are added. Since the site has no accounts or email list tied
        to it, changes are simply reflected here; there&rsquo;s no separate notice to send.</p>
    </div>
  </section>

  <section>
    <div class="sectionHead">
      <span class="tag">06</span>
      <span class="rule"></span>
      <h2>Contact</h2>
    </div>
    <div class="proseBlock">
      <p>Questions about this policy or how the site works can go to
        <a href="mailto:hello@collegefootballbelt.com">hello@collegefootballbelt.com</a>.</p>
    </div>
  </section>
</main>

{site_footer('', 'The College Football Belt &mdash; lineal championship, since 1869.')}
'''


def generate_compare_page(lineage, colors, belt_games):
    """Pick any two teams that have ever held the belt and see their combined
    belt-era stats plus every belt game the two have played against each
    other -- entirely client-side against one embedded JSON blob (same
    order of magnitude as what all-games.html already renders server-side
    as HTML rows), so no new data file or API call. Scoped deliberately to
    BELT games specifically, not a true all-time series (CFBD's full
    non-belt head-to-head is only ever fetched for the one upcoming
    opponent, by fetch_matchup_preview.py -- not for arbitrary pairs)."""
    reigns = lineage["reigns"]
    today = date.today()
    current_holder = reigns[-1]["team"]

    by_team = {}
    for r in reigns:
        by_team.setdefault(r["team"], []).append(r)

    team_stats = {}
    for team, team_reigns in by_team.items():
        team_stats[team] = {
            "reigns": len(team_reigns),
            "days": sum(reign_duration_days(r, today) for r in team_reigns),
            "defenses": sum(r.get("defenses", 0) for r in team_reigns),
            "slug": team_slug(team),
        }

    games_payload = []
    for g in belt_games:
        home, away = g["home"], g["away"]
        try:
            hs, aws = (int(x) for x in g["score"].split("-"))
        except (KeyError, ValueError):
            continue
        games_payload.append({
            "id": g["game_id"], "date": g["date"], "home": home, "away": away,
            "hs": hs, "as": aws,
        })

    teams_sorted = sorted(by_team.keys())
    default_b = None
    if len(reigns) >= 2:
        prev_team = reigns[-2]["team"]
        if prev_team != current_holder:
            default_b = prev_team
    if default_b is None:
        default_b = next((t for t in teams_sorted if t != current_holder), current_holder)

    payload = json.dumps({
        "teams": teams_sorted, "stats": team_stats, "games": games_payload,
    }, ensure_ascii=False)

    options_html = "".join(f'<option value="{esc(t)}">{esc(t)}</option>' for t in teams_sorted)

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Compare Teams — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'compare')}

<main class="wrap">
  <p class="kicker pageKicker">Head to head</p>
  <h1 class="pageTitle">Compare Two Belt Holders</h1>
  <p class="lede">Pick any two programs that have ever held the belt &mdash; see their combined
    reign stats and every belt game the two have played against each other. This is BELT
    games specifically, not a full all-time series.</p>

  <div class="compareForm">
    <select id="compareA" aria-label="First team">{options_html}</select>
    <span class="compareVs">vs.</span>
    <select id="compareB" aria-label="Second team">{options_html}</select>
  </div>

  <h2 class="srOnly">Belt history side by side</h2>
  <div class="compareStats" id="compareStats"></div>
  <div class="compareGamesHead" id="compareGamesHead"></div>
  <p class="compareRecord" id="compareRecord"></p>
  <div id="compareGames"></div>

  <script type="application/json" id="compareData">{payload}</script>
  <script>
  (function(){{
    var dataEl = document.getElementById('compareData');
    var data = JSON.parse(dataEl.textContent);
    var selA = document.getElementById('compareA');
    var selB = document.getElementById('compareB');
    var statsEl = document.getElementById('compareStats');
    var headEl = document.getElementById('compareGamesHead');
    var recordEl = document.getElementById('compareRecord');
    var gamesEl = document.getElementById('compareGames');

    function slugUrl(team) {{
      var s = (data.stats[team] || {{}}).slug;
      return s ? 'teams/' + s + '.html' : '#';
    }}

    function statCard(team) {{
      var s = data.stats[team] || {{ reigns: 0, days: 0, defenses: 0 }};
      return '<div class="compareStatCard"><h3><a href="' + slugUrl(team) + '">' + team + '</a></h3>' +
        '<div class="compareStatRow"><span>Reigns</span><span class="val">' + s.reigns + '</span></div>' +
        '<div class="compareStatRow"><span>Total Days Held</span><span class="val">' + s.days.toLocaleString() + '</span></div>' +
        '<div class="compareStatRow"><span>Total Defenses</span><span class="val">' + s.defenses + '</span></div></div>';
    }}

    function fmtDate(iso) {{
      var d = new Date(iso + 'T00:00:00Z');
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleDateString(undefined, {{ year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' }});
    }}

    function render() {{
      var a = selA.value, b = selB.value;
      statsEl.innerHTML = statCard(a) + statCard(b);
      if (a === b) {{
        headEl.textContent = '';
        recordEl.textContent = 'Pick two different teams to see the belt games between them.';
        gamesEl.innerHTML = '';
        return;
      }}
      var matches = data.games.filter(function(g){{
        return (g.home === a && g.away === b) || (g.home === b && g.away === a);
      }}).sort(function(x, y){{ return x.date < y.date ? -1 : 1; }});

      headEl.textContent = 'Belt Games: ' + a + ' vs. ' + b;
      if (!matches.length) {{
        recordEl.textContent = 'These two have never met with the belt on the line.';
        gamesEl.innerHTML = '';
        return;
      }}
      var winsA = 0, winsB = 0, ties = 0;
      var rows = matches.map(function(g){{
        var homeIsA = g.home === a;
        var winner = g.hs === g.as ? null : (g.hs > g.as ? g.home : g.away);
        if (winner === a) winsA++; else if (winner === b) winsB++; else ties++;
        return '<a class="compareGameRow" href="games/' + g.id + '.html">' +
          '<span class="compareGameDate mono">' + fmtDate(g.date) + '</span>' +
          '<span class="compareMatchup">' + g.away + ' at ' + g.home + '</span>' +
          '<span class="compareScore">' + g.hs + '–' + g.as + '</span></a>';
      }});
      var recordTxt = a + ' ' + winsA + ', ' + b + ' ' + winsB;
      if (ties) recordTxt += ', ' + ties + ' tie' + (ties !== 1 ? 's' : '');
      recordEl.textContent = matches.length + ' belt game' + (matches.length !== 1 ? 's' : '') + ' — ' + recordTxt;
      gamesEl.innerHTML = rows.join('');
    }}

    selA.value = {json.dumps(current_holder)};
    selB.value = {json.dumps(default_b)};
    selA.addEventListener('change', render);
    selB.addEventListener('change', render);
    render();
  }})();
  </script>
</main>

{site_footer('', 'Belt-game results only &mdash; computed straight from belt_data/lineage.json, no extra API call.')}
'''


def generate_my_team_page(team_paths, colors):
    """"My team and the path to the belt" -- wishlist item #1, 2026-09-16,
    Bob: "Let me pick a team once (localStorage, no account needed) and
    then answer the question every fan actually has: when could we get a
    shot at it?" Pick-once-and-remember via localStorage (no account,
    matches the theme-toggle/PWA pattern already used elsewhere on the
    site); the actual per-team scenario data is entirely precomputed at
    build time by build_lineage.py's compute_team_paths() (team_paths.json)
    off the current holder's + every team's remaining schedule, so this
    page needs no extra API call and no per-visit computation beyond a
    dict lookup.

    `team_paths` is None if build_lineage.py hasn't written team_paths.json
    yet (the fetch failed, or an older belt_data/ snapshot) -- caller skips
    rendering this page entirely in that case, same no-op-when-unset
    pattern as every other optional feature on this site."""
    holder = team_paths["holder"]
    teams = team_paths["teams"]
    teams_sorted = sorted(teams.keys())

    logos = {t: team_logo(colors, t) for t in teams_sorted if team_logo(colors, t)}
    payload = json.dumps({"holder": holder, "teams": teams, "logos": logos}, ensure_ascii=False)
    options_html = "".join(f'<option value="{esc(t)}">{esc(t)}</option>' for t in teams_sorted)

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>My Team and the Path to the Belt — The College Football Belt</title>
<meta name="description" content="Pick your team and see exactly how it could get a shot at the College Football Belt this season -- straight off {esc(holder)}'s remaining schedule.">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'my-team')}

<main class="wrap">
  <p class="kicker pageKicker">Path to the belt</p>
  <h1 class="pageTitle">My Team and the Path to the Belt</h1>
  <p class="lede" id="myTeamLede">Pick your team once &mdash; we'll remember it on this device &mdash; and we'll tell you
    exactly how {esc(holder)}'s remaining schedule could put the belt in front of you this season.</p>

  <div class="myTeamPicker" id="myTeamPicker" hidden>
    <select id="myTeamSelect" aria-label="Choose your team">{options_html}</select>
    <button type="button" id="myTeamSave">Save my team</button>
  </div>

  <div id="myTeamResult" hidden>
    <div class="myTeamHeader">
      <img id="myTeamLogo" class="teamLogo" alt="" hidden>
      <h2 id="myTeamName" class="pageTitle" style="font-size:32px;margin:0;"></h2>
      <button type="button" class="myTeamChangeLink" id="myTeamChange">Not your team? Pick another</button>
    </div>
    <div id="myTeamCards"></div>
  </div>

  <script type="application/json" id="myTeamData">{payload}</script>
  <script>
  (function(){{
    var data = JSON.parse(document.getElementById('myTeamData').textContent);
    var STORAGE_KEY = 'cfbbelt_my_team';
    var picker = document.getElementById('myTeamPicker');
    var select = document.getElementById('myTeamSelect');
    var saveBtn = document.getElementById('myTeamSave');
    var resultEl = document.getElementById('myTeamResult');
    var nameEl = document.getElementById('myTeamName');
    var logoEl = document.getElementById('myTeamLogo');
    var cardsEl = document.getElementById('myTeamCards');
    var changeBtn = document.getElementById('myTeamChange');
    var lede = document.getElementById('myTeamLede');

    function fmtDate(iso) {{
      var d = new Date(iso + 'T00:00:00Z');
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleDateString(undefined, {{ weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' }});
    }}

    function getSaved() {{
      try {{ return window.localStorage.getItem(STORAGE_KEY); }} catch (e) {{ return null; }}
    }}
    function setSaved(team) {{
      try {{ window.localStorage.setItem(STORAGE_KEY, team); }} catch (e) {{}}
    }}
    function clearSaved() {{
      try {{ window.localStorage.removeItem(STORAGE_KEY); }} catch (e) {{}}
    }}

    function showPicker() {{
      resultEl.hidden = true;
      picker.hidden = false;
      lede.hidden = false;
    }}

    function cardHtml(kicker, text, isHolder) {{
      return '<div class="myTeamCard' + (isHolder ? ' holder' : '') + '"><div class="kicker">' + kicker + '</div><p>' + text + '</p></div>';
    }}

    function render(team) {{
      var entry = data.teams[team];
      if (!entry) {{ showPicker(); return; }}
      picker.hidden = true;
      lede.hidden = true;
      resultEl.hidden = false;
      nameEl.textContent = team;
      var logo = data.logos[team];
      if (logo) {{ logoEl.src = logo; logoEl.alt = team + ' logo'; logoEl.hidden = false; }}
      else {{ logoEl.hidden = true; }}

      var html = '';
      if (entry.is_holder) {{
        html += cardHtml('Right now', team + ' already holds the College Football Belt. Defend it and it stays right here.', true);
      }} else if (entry.direct.length) {{
        entry.direct.forEach(function(g) {{
          html += cardHtml('Your shot', 'You play ' + data.holder + (g.is_home ? ' at home' : ' on the road') +
            ' on ' + fmtDate(g.date) + '. Win, and the belt is yours.');
        }});
      }} else if (entry.indirect.length) {{
        html += '<p class="myTeamEmpty" style="margin-bottom:14px;">' + team + ' doesn\\'t play ' + data.holder +
          ' this season, but here\\'s how it could still reach you:</p>';
        entry.indirect.slice(0, 5).forEach(function(p) {{
          html += cardHtml('If the belt moves', 'If ' + p.via + ' beats ' + data.holder + ' on ' + fmtDate(p.via_date) +
            ' and holds onto it, you play them' + (p.your_is_home ? ' at home' : ' on the road') +
            ' on ' + fmtDate(p.your_date) + ' &mdash; that\\'s your game.');
        }});
      }} else {{
        html += '<p class="myTeamEmpty">No path to the belt visible on ' + team + '\\'s schedule right now &mdash; ' +
          data.holder + ' would need to lose it to someone ' + team + ' plays later, and that game isn\\'t on the board yet. Check back as the schedule fills in.</p>';
      }}
      cardsEl.innerHTML = html;
    }}

    saveBtn.addEventListener('click', function() {{
      var team = select.value;
      setSaved(team);
      render(team);
    }});
    changeBtn.addEventListener('click', function() {{
      clearSaved();
      showPicker();
    }});

    var saved = getSaved();
    if (saved && data.teams[saved]) {{
      render(saved);
    }} else {{
      showPicker();
    }}
  }})();
  </script>
</main>

{site_footer('', 'Your team choice is saved only in this browser &mdash; no account, nothing sent to us.')}
'''


def generate_season_page(season_year, season_games, reign_by_start, all_seasons, remaining_schedule=None):
    """A season page -- wishlist item #4, 2026-09-16, Bob: "Everything on
    the site is 'all time' or 'right now.' I'd want 'the 2026 belt season'
    as its own page: every belt game so far in order, who took it from
    whom, days each held it, and the remaining schedule below. Then the
    same page for every past season, which becomes an evergreen archive
    Google will love."

    `season_year`: the CFBD season label (the year the season started --
    e.g. a January 2027 CFP game is still season 2026) -- one page per
    distinct value already present in belt_games, no new API call, since
    every belt game already carries its own "season" field.
    `season_games`: this season's belt_games, chronological (a sub-slice
    of the same list compute_sequence() already numbered, so game_number/
    reign_number are already on each row).
    `reign_by_start`: {(team, start_date): reign} across ALL of lineage's
    reigns -- built once by the caller -- so "days each held it" can look
    up a reign that started this season even if it's still open (current)
    or ended in a LATER season (a reign can outlive the season it started
    in).
    `all_seasons`: every season year on file, sorted ascending -- for the
    prev/next-season links that make this an actually-browsable archive
    rather than 150 orphaned pages.
    `remaining_schedule`: the holder's full remaining schedule (only
    passed for the current/latest season -- every other season is closed
    history with nothing left to play)."""
    is_current = season_year == all_seasons[-1]
    idx = all_seasons.index(season_year)
    prev_year = all_seasons[idx - 1] if idx > 0 else None
    next_year = all_seasons[idx + 1] if idx < len(all_seasons) - 1 else None

    title_changes = sum(1 for g in season_games if g["outcome"] in ("changed", "established"))
    defenses_total = len(season_games) - title_changes

    opening_team = season_games[0]["holder"]  # None only for 1869, the belt's own first game
    closing_team = season_games[-1]["new_holder"]  # always the post-game holder, defended or not

    if opening_team and opening_team != closing_team:
        summary = (f"Opened the season with {esc(opening_team)} holding the belt; "
                    f"{esc(closing_team)} {'holds it now' if is_current else 'closed it out'}.")
    elif opening_team:
        summary = f"{esc(closing_team)} held the belt the entire season -- {defenses_total} defense{'s' if defenses_total != 1 else ''}."
    else:
        summary = f"The belt itself was established this season, by {esc(closing_team)}."

    today = date.today()
    rows_html = ""
    defense_no = 0
    for g in season_games:
        home, away = g["home"], g["away"]
        home_score, away_score = (int(x) for x in g["score"].split("-"))
        outcome = g["outcome"]
        is_last_overall = is_current and g is season_games[-1]

        if outcome == "established":
            defense_no = 0
            result_html = f'<span class="win">Belt established: {esc(g["new_holder"])}</span>'
        elif outcome == "changed":
            defense_no = 0
            result_html = f'<span class="win">New champion: {esc(g["new_holder"])}</span>'
        else:
            defense_no += 1
            tie_note = " (tie)" if outcome == "retained (tie)" else ""
            result_html = f'Defended{tie_note} &middot; #{defense_no}'

        loc_word = "vs." if g["neutral"] else "at"
        matchup_html = f'<a href="games/{g["game_id"]}.html">{esc(away)} {loc_word} {esc(home)}</a>'
        score_html = f'<a href="games/{g["game_id"]}.html">{away_score}&ndash;{home_score}</a>'

        cls_bits = []
        if outcome in ("changed", "established"):
            cls_bits.append("titleChange")
        if is_last_overall:
            cls_bits.append("current")
        cls = " ".join(cls_bits)

        rows_html += f'''
        <tr class="{cls}">
          <td class="num">{g["game_number"]:,}</td>
          <td class="dates">{fmt_date(g["date"])}</td>
          <td class="matchup">{matchup_html}</td>
          <td class="tabular">{score_html}</td>
          <td class="result">{result_html}</td>
        </tr>'''

    # ---- "days each held it": every reign that STARTED this season,
    # whether it's since ended (even in a later season) or is still open.
    reign_rows_html = ""
    for g in season_games:
        if g["outcome"] not in ("changed", "established", "lost (tie)"):
            continue
        team = g["new_holder"]
        r = reign_by_start.get((team, g["date"]))
        if not r:
            continue
        start = date.fromisoformat(r["start_date"])
        if r.get("end_date"):
            end = date.fromisoformat(r["end_date"])
            days_txt = f'{(end - start).days:,} days'
        else:
            days_txt = f'{(today - start).days:,} days &mdash; and counting'
        won_from_html = (f'took it from {esc(r["won_from"])}' if r.get("won_from")
                          else 'the belt&rsquo;s inaugural holder')
        reign_rows_html += f'''
        <tr>
          <td class="teamCell"><a href="teams/{team_slug(team)}.html">{esc(team)}</a></td>
          <td class="dates">{fmt_date(r["start_date"])}</td>
          <td>{won_from_html}</td>
          <td class="tabular">{days_txt}</td>
        </tr>'''

    reign_section_html = ""
    if reign_rows_html:
        reign_section_html = f'''
  <div class="sectionHead">
    <span class="tag">Season Detail</span>
    <span class="rule"></span>
    <h2>Reigns That Started This Season</h2>
  </div>
  <div class="tableScroll">
    <table class="reignsTable">
      <thead><tr><th>Team</th><th>Since</th><th>How</th><th style="text-align:right">Held It</th></tr></thead>
      <tbody>{reign_rows_html}
      </tbody>
    </table>
  </div>'''

    remaining_html = ""
    if is_current and remaining_schedule:
        chips = ""
        for g in remaining_schedule:
            loc = "vs." if (g.get("is_home") or g.get("neutral")) else "at"
            chips += (f'<li class="watchChip">{loc} <strong>{esc(g["opponent"])}</strong> '
                      f'&middot; {fmt_date(g["date"])}</li>')
        remaining_html = f'''
  <div class="sectionHead">
    <span class="tag">Ahead</span>
    <span class="rule"></span>
    <h2>Remaining Schedule</h2>
  </div>
  <ul class="remainingSchedule">{chips}
  </ul>'''

    prev_next_html = '<p class="viewToggle">'
    bits = []
    if prev_year:
        bits.append(f'&larr; <a href="season-{prev_year}.html">The {prev_year} season</a>')
    bits.append('<a href="seasons.html">All seasons</a>')
    if next_year:
        bits.append(f'<a href="season-{next_year}.html">The {next_year} season</a> &rarr;')
    prev_next_html += ' &middot; '.join(bits) + '</p>'

    title_word = "The Current Season" if is_current else f"The {season_year} Season"
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>The {season_year} Belt Season — The College Football Belt</title>
<meta name="description" content="Every College Football Belt game of the {season_year} season: who held the lineal title, each defense and title change, and who carried the belt out of {season_year}.">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'seasons')}

<main class="wrap">
  <p class="kicker pageKicker">Season by season</p>
  <h1 class="pageTitle">{title_word}: {season_year}</h1>
  <p class="lede">{summary} {len(season_games)} belt game{'s' if len(season_games) != 1 else ''} this season:
    {title_changes} title change{'s' if title_changes != 1 else ''} and {defenses_total} defense{'s' if defenses_total != 1 else ''}.</p>

  <div class="tableScroll">
    <table class="reignsTable">
      <thead>
        <tr><th>#</th><th>Date</th><th>Matchup</th><th style="text-align:right">Score</th><th>Result</th></tr>
      </thead>
      <tbody>{rows_html}
      </tbody>
    </table>
  </div>
  {reign_section_html}
  {remaining_html}
  {prev_next_html}
</main>

{site_footer('', 'Every game computed from the College Football Data API.')}
'''


def generate_seasons_index_page(seasons_summary):
    """The season archive's landing page -- one row per season, newest
    first, each linking to its own season-<year>.html. `seasons_summary`
    is [{"year":, "games":, "title_changes":, "opening_team":,
    "closing_team":, "is_current":}], already sorted ascending by the
    caller (this reverses it for newest-first display, the natural order
    for an archive people mostly come to check "this year")."""
    rows_html = ""
    for s in reversed(seasons_summary):
        label = "Current Season" if s["is_current"] else str(s["year"])
        cls = "current" if s["is_current"] else ""
        rows_html += f'''
        <tr class="{cls}">
          <td class="teamCell"><a href="season-{s["year"]}.html">{label}</a></td>
          <td class="tabular">{s["games"]}</td>
          <td class="tabular">{s["title_changes"]}</td>
          <td class="teamCell">{esc(s["closing_team"])}</td>
        </tr>'''

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Every Belt Season — The College Football Belt</title>
<meta name="description" content="Season-by-season history of the College Football Belt since 1869: belt games per year, how often the title changed hands, and who held it at season&rsquo;s end.">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'seasons')}

<main class="wrap">
  <p class="kicker pageKicker">Season by season</p>
  <h1 class="pageTitle">Every Belt Season</h1>
  <p class="lede">Everything else on this site is all-time or right-now &mdash; this is the season-by-season
    archive, {len(seasons_summary)} of them back to 1869. Tap any season for its full game-by-game story.</p>

  <div class="tableScroll">
    <table class="reignsTable">
      <thead>
        <tr><th>Season</th><th class="tabular">Games</th><th class="tabular">Title Changes</th><th>Closed With</th></tr>
      </thead>
      <tbody>{rows_html}
      </tbody>
    </table>
  </div>
</main>

{site_footer('', 'Every season computed from the College Football Data API.')}
'''


def generate_defend_or_dethrone_page(next_game, recent_belt_games):
    """"Defend or Dethrone" -- wishlist item #5, 2026-09-16, Bob: "A weekly
    pick -- does the belt change hands or not -- with a streak stored
    locally and a Wordle-style share card ('Belt streak: 7
    \U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9').
    No accounts, no backend, and it's the thing that gets people posting
    the site's name on X every week."

    Entirely client-side, same localStorage pick-and-remember pattern as
    My Team -- the only wrinkle is GRADING a pick, since there's no
    backend to tell the browser who won. The trick: this page embeds the
    holder's upcoming game (to pick on) AND the last several ALREADY-
    DECIDED belt games (to grade against) in the same small JSON payload.
    A pick is stored as {{holder, opponent, date, pick}} -- the exact
    matchup it was about -- and every time the page loads, it checks
    whether that exact matchup now appears in the recent-games list (i.e.
    the pipeline has since run again after the game finished); if so, it
    grades the pick right there in the browser and updates the streak.
    Ten games of lookback comfortably covers "came back after a bye week
    or two"; anyone away longer than that just doesn't get that one pick
    graded -- their streak simply picks back up with the next one, no
    harm done.

    `next_game`: belt_data/next_game.json's dict, or None.
    `recent_belt_games`: the last ~10 entries of belt_games (chronological,
    oldest first) -- always available (this feature needs no optional
    upstream data at all beyond what build_lineage.py always writes)."""
    payload_next = None
    if next_game:
        payload_next = {
            "holder": next_game["team"], "opponent": next_game["opponent"],
            "date": next_game["date"], "is_home": bool(next_game.get("is_home")),
            "neutral": bool(next_game.get("neutral")),
        }
    payload_recent = [
        {"holder": g["holder"], "opponent": g["opponent"], "date": g["date"],
         "defended": g["outcome"] in ("retained", "retained (tie)")}
        for g in recent_belt_games if g.get("holder")  # skips the one "established" game, which was never a pick
    ]
    payload = json.dumps({"next_game": payload_next, "recent_games": payload_recent}, ensure_ascii=False)

    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Defend or Dethrone — The College Football Belt</title>
<meta name="description" content="Pick it every week: does the belt holder survive, or does the belt change hands? Build a streak, no account needed.">
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'dod')}

<main class="wrap">
  <p class="kicker pageKicker">Weekly pick</p>
  <h1 class="pageTitle">Defend or Dethrone</h1>
  <p class="lede">Every week the belt&rsquo;s on the line, make the call before kickoff: does the holder
    survive, or does the belt change hands? Right or wrong, we&rsquo;ll remember your streak on this device.</p>

  <div class="dodStreakBar">
    <div><span class="n tabular" id="dodStreakNum">0</span><span class="l">Current Streak</span></div>
    <div><span class="n tabular" id="dodBestNum">0</span><span class="l">Best Streak</span></div>
    <div class="dodHistoryWrap"><span class="l">Recent</span><span id="dodHistory" class="dodHistory"></span></div>
  </div>

  <div class="dodPicker" id="dodPicker" hidden>
    <p class="dodMatchup" id="dodMatchup"></p>
    <div class="dodChoices">
      <button type="button" class="dodChoice dodDefend" id="dodDefend">Defend<span>Holder keeps it</span></button>
      <button type="button" class="dodChoice dodDethrone" id="dodDethrone">Dethrone<span>Belt changes hands</span></button>
    </div>
  </div>

  <div class="dodPending" id="dodPending" hidden>
    <p id="dodPendingText"></p>
  </div>

  <div class="dodPending" id="dodNone" hidden>
    <p>No upcoming belt game on file right now &mdash; check back once the holder&rsquo;s next game is scheduled.</p>
  </div>

  <button type="button" class="dodShareBtn" id="dodShare">Share my streak</button>

  <script type="application/json" id="dodData">{payload}</script>
  <script>
  (function(){{
    var data = JSON.parse(document.getElementById('dodData').textContent);
    var STORAGE_KEY = 'cfbbelt_dod';
    var nextGame = data.next_game;
    var recentGames = data.recent_games || [];

    function fmtDate(iso) {{
      var d = new Date(iso + 'T00:00:00Z');
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleDateString(undefined, {{ weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' }});
    }}

    function getState() {{
      var raw = null;
      try {{ raw = window.localStorage.getItem(STORAGE_KEY); }} catch (e) {{}}
      if (!raw) return {{ streak: 0, best: 0, history: [], pending: null }};
      try {{
        var s = JSON.parse(raw);
        return {{ streak: s.streak || 0, best: s.best || 0, history: s.history || [], pending: s.pending || null }};
      }} catch (e) {{ return {{ streak: 0, best: 0, history: [], pending: null }}; }}
    }}
    function setState(s) {{
      try {{ window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); }} catch (e) {{}}
    }}
    function sameGame(a, b) {{
      return !!a && !!b && a.holder === b.holder && a.opponent === b.opponent && a.date === b.date;
    }}

    var state = getState();

    // Grade a pending pick the moment its game shows up in recent_games --
    // i.e. the pipeline has run again since the game ended.
    if (state.pending) {{
      var decided = null;
      for (var i = 0; i < recentGames.length; i++) {{
        if (sameGame(recentGames[i], state.pending)) {{ decided = recentGames[i]; break; }}
      }}
      if (decided) {{
        var actual = decided.defended ? 'defend' : 'dethrone';
        var correct = actual === state.pending.pick;
        state.streak = correct ? (state.streak + 1) : 0;
        state.best = Math.max(state.best, state.streak);
        state.history = state.history.concat([correct]);
        if (state.history.length > 20) state.history = state.history.slice(-20);
        state.pending = null;
        setState(state);
      }}
    }}

    var pickerEl = document.getElementById('dodPicker');
    var pendingEl = document.getElementById('dodPending');
    var noneEl = document.getElementById('dodNone');
    var matchupEl = document.getElementById('dodMatchup');
    var pendingTextEl = document.getElementById('dodPendingText');
    var streakEl = document.getElementById('dodStreakNum');
    var bestEl = document.getElementById('dodBestNum');
    var historyEl = document.getElementById('dodHistory');
    var shareBtn = document.getElementById('dodShare');
    var defendBtn = document.getElementById('dodDefend');
    var dethroneBtn = document.getElementById('dodDethrone');

    function historyText() {{
      return state.history.slice(-14).map(function(c) {{ return c ? '\\uD83D\\uDFE9' : '\\uD83D\\uDFE5'; }}).join('');
    }}

    function showState() {{
      streakEl.textContent = state.streak;
      bestEl.textContent = state.best;
      historyEl.textContent = historyText();

      if (!nextGame) {{
        pickerEl.hidden = true;
        pendingEl.hidden = true;
        noneEl.hidden = false;
        return;
      }}
      noneEl.hidden = true;
      var loc = (nextGame.is_home || nextGame.neutral) ? 'vs.' : 'at';
      if (state.pending && sameGame(nextGame, state.pending)) {{
        pickerEl.hidden = true;
        pendingEl.hidden = false;
        pendingTextEl.textContent = 'You picked \\u201c' + (state.pending.pick === 'defend' ? 'Defend' : 'Dethrone') +
          '\\u201d for ' + nextGame.holder + ' ' + loc + ' ' + nextGame.opponent + ' on ' + fmtDate(nextGame.date) +
          '. Check back after the game.';
      }} else {{
        pendingEl.hidden = true;
        pickerEl.hidden = false;
        matchupEl.textContent = nextGame.holder + ' ' + loc + ' ' + nextGame.opponent + ' \\u00b7 ' + fmtDate(nextGame.date);
      }}
    }}

    function pick(choice) {{
      if (!nextGame) return;
      state.pending = {{ holder: nextGame.holder, opponent: nextGame.opponent, date: nextGame.date, pick: choice }};
      setState(state);
      showState();
    }}

    if (defendBtn) defendBtn.addEventListener('click', function(){{ pick('defend'); }});
    if (dethroneBtn) dethroneBtn.addEventListener('click', function(){{ pick('dethrone'); }});

    if (shareBtn) {{
      shareBtn.addEventListener('click', function() {{
        var text = 'Belt streak: ' + state.streak + ' ' + historyText() + '\\ncollegefootballbelt.com/defend-or-dethrone.html';
        if (navigator.share) {{
          navigator.share({{ text: text }}).catch(function(){{}});
        }} else if (navigator.clipboard) {{
          navigator.clipboard.writeText(text).then(function() {{
            var original = shareBtn.textContent;
            shareBtn.textContent = 'Copied!';
            setTimeout(function(){{ shareBtn.textContent = original; }}, 1800);
          }}).catch(function(){{}});
        }}
      }});
    }}

    showState();
  }})();
  </script>
</main>

{site_footer('', 'Your picks and streak are saved only in this browser &mdash; no account, nothing sent to us.')}
'''


# --------------------------------------------------------------- trivia game

QUIZ_LEN = 10


def _trivia_never_held_candidates(lineage, belt_games):
    """Teams that have PLAYED a belt game but never actually held the belt
    -- computed, not curated: any team appearing on either side of a belt
    game that's never in reigns' own team set."""
    holder_teams = {r["team"] for r in lineage["reigns"]}
    challengers = set()
    for g in belt_games:
        for side in (g.get("home"), g.get("away")):
            if side and side not in holder_teams:
                challengers.add(side)
    return sorted(challengers)


def build_trivia_pool(lineage, belt_games, rng):
    """A pool of multiple-choice questions, generated straight from
    lineage.json + belt_games -- there's no hand-written trivia file to
    keep in sync as new games happen. Called with an UNSEEDED rng, so the
    exact pool (and which subset of it a visitor gets) naturally reshuffles
    on the site's own weekly rebuild cadence instead of ever going stale."""
    today = date.today()
    reigns = lineage["reigns"]
    questions = []

    # ---- longest single reign, among 4 real teams' best reigns ----
    best_by_team = {}
    for r in reigns:
        d = reign_duration_days(r, today)
        if r["team"] not in best_by_team or d > best_by_team[r["team"]]:
            best_by_team[r["team"]] = d
    ranked = sorted(best_by_team.items(), key=lambda kv: -kv[1])
    if len(ranked) >= 4:
        for _ in range(6):
            lo = rng.randint(0, max(0, len(ranked) - 4))
            window = ranked[lo:lo + max(4, min(24, len(ranked) - lo))]
            if len(window) < 4:
                continue
            chosen = rng.sample(window, 4)
            chosen.sort(key=lambda kv: -kv[1])
            correct = chosen[0][0]
            options = [c[0] for c in chosen]
            rng.shuffle(options)
            questions.append({
                "q": "Which of these teams had the single longest belt reign in history?",
                "choices": options, "answer": options.index(correct),
            })

    # ---- who they took the belt from ----
    changes = [g for g in belt_games if g.get("outcome") == "changed" and g.get("holder")]
    all_holders_at_change = list({g["holder"] for g in changes})
    for g in rng.sample(changes, min(8, len(changes))):
        distractors = [h for h in all_holders_at_change if h != g["holder"]]
        if len(distractors) < 3:
            continue
        options = [g["holder"]] + rng.sample(distractors, 3)
        rng.shuffle(options)
        questions.append({
            "q": f'On {fmt_date(g["date"])}, {esc(g["new_holder"])} took the belt from&hellip;?',
            "choices": options, "answer": options.index(g["holder"]),
        })

    # ---- total defenses, a real number against 3 other real teams' totals ----
    totals_by_team = {}
    for r in reigns:
        totals_by_team[r["team"]] = totals_by_team.get(r["team"], 0) + r.get("defenses", 0)
    teams_with_totals = list(totals_by_team.items())
    if len(teams_with_totals) >= 4:
        for _ in range(6):
            team, correct_total = rng.choice(teams_with_totals)
            distractor_pool = list({v for t, v in teams_with_totals if t != team and v != correct_total})
            if len(distractor_pool) < 3:
                continue
            options = [str(correct_total)] + [str(v) for v in rng.sample(distractor_pool, 3)]
            if len(set(options)) < 4:
                continue
            rng.shuffle(options)
            questions.append({
                "q": f"How many total defenses does {esc(team)} have across all its belt reigns?",
                "choices": options, "answer": options.index(str(correct_total)),
            })

    # ---- which of these four never held the belt ----
    never_held = _trivia_never_held_candidates(lineage, belt_games)
    holder_names = list({r["team"] for r in reigns})
    if never_held and len(holder_names) >= 3:
        for _ in range(5):
            impostor = rng.choice(never_held)
            options = rng.sample(holder_names, 3) + [impostor]
            rng.shuffle(options)
            questions.append({
                "q": "Three of these four teams have held the College Football Belt at some point. "
                     "Which one never has?",
                "choices": options, "answer": options.index(impostor),
            })

    rng.shuffle(questions)
    return questions


def generate_trivia_page(pool):
    payload = json.dumps(pool, ensure_ascii=False)
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Belt Trivia — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'trivia')}

<main class="wrap">
  <p class="kicker pageKicker">Quiz</p>
  <h1 class="pageTitle">Belt Trivia</h1>
  <p class="lede">{QUIZ_LEN} questions pulled straight from 150+ years of real belt history &mdash;
    every question and every wrong answer is an actual fact from the lineage, not hand-written.
    The pool rotates with every site update, so it&rsquo;s never quite the same quiz twice.</p>

  <div id="triviaRoot"></div>

  <script type="application/json" id="triviaData">{payload}</script>
  <script>
  (function(){{
    var pool = JSON.parse(document.getElementById('triviaData').textContent);
    var QUIZ_LEN = {QUIZ_LEN};
    var root = document.getElementById('triviaRoot');

    function shuffle(arr) {{
      for (var i = arr.length - 1; i > 0; i--) {{
        var j = Math.floor(Math.random() * (i + 1));
        var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
      }}
      return arr;
    }}

    var quiz = [], idx = 0, score = 0;

    function newQuiz() {{
      quiz = shuffle(pool.slice()).slice(0, Math.min(QUIZ_LEN, pool.length));
      idx = 0; score = 0;
    }}

    function renderQuestion() {{
      var q = quiz[idx];
      var answered = false;
      var choicesHtml = q.choices.map(function(c, i){{
        return '<button class="triviaChoice" data-i="' + i + '">' + c + '</button>';
      }}).join('');
      root.innerHTML =
        '<div class="triviaProgress">Question ' + (idx + 1) + ' of ' + quiz.length + ' &middot; Score: ' + score + '</div>' +
        '<div class="triviaQ">' + q.q + '</div>' +
        '<div class="triviaChoices">' + choicesHtml + '</div>' +
        '<div class="triviaFeedback" id="triviaFeedback"></div>';
      Array.prototype.slice.call(root.querySelectorAll('.triviaChoice')).forEach(function(btn){{
        btn.addEventListener('click', function(){{
          if (answered) return;
          answered = true;
          var i = parseInt(btn.getAttribute('data-i'), 10);
          var correct = i === q.answer;
          if (correct) score++;
          Array.prototype.slice.call(root.querySelectorAll('.triviaChoice')).forEach(function(b, bi){{
            b.disabled = true;
            if (bi === q.answer) b.classList.add('triviaChoice--right');
            else if (bi === i) b.classList.add('triviaChoice--wrong');
          }});
          var fb = document.getElementById('triviaFeedback');
          fb.textContent = correct ? 'Correct!' : ('Not quite \\u2014 it was "' + q.choices[q.answer] + '".');
          fb.className = 'triviaFeedback ' + (correct ? 'triviaFeedback--right' : 'triviaFeedback--wrong');
          var nextBtn = document.createElement('button');
          nextBtn.className = 'calBtn';
          nextBtn.style.marginTop = '16px';
          nextBtn.textContent = (idx === quiz.length - 1) ? 'See Results' : 'Next Question';
          nextBtn.addEventListener('click', function(){{
            idx++;
            if (idx >= quiz.length) renderResults(); else renderQuestion();
          }});
          root.appendChild(nextBtn);
        }});
      }});
    }}

    function renderResults() {{
      var pct = quiz.length ? Math.round((score / quiz.length) * 100) : 0;
      var msg;
      if (pct === 100) msg = 'Perfect score \\u2014 you know this belt cold.';
      else if (pct >= 70) msg = 'Strong showing.';
      else if (pct >= 40) msg = 'Not bad \\u2014 the lineage has some deep cuts.';
      else msg = '150+ years runs deep. Give it another go.';
      var shareText = 'I scored ' + score + '/' + quiz.length +
        ' on College Football Belt trivia \\u2014 collegefootballbelt.com/trivia.html';
      root.innerHTML =
        '<div class="triviaResults">' +
        '<div class="triviaScore tabular">' + score + ' / ' + quiz.length + '</div>' +
        '<p>' + msg + '</p>' +
        '<textarea class="embedCode" rows="2" readonly aria-label="Share text" onclick="this.select()">' + shareText + '</textarea>' +
        '<button class="calBtn" id="triviaReplay" style="margin-top:14px">Play Again</button>' +
        '</div>';
      document.getElementById('triviaReplay').addEventListener('click', function(){{
        newQuiz(); renderQuestion();
      }});
    }}

    newQuiz();
    if (quiz.length) {{ renderQuestion(); }}
    else {{ root.innerHTML = '<p class="emptyNote">Not enough belt history yet for a quiz \\u2014 check back soon.</p>'; }}
  }})();
  </script>
</main>

{site_footer('', 'Every question computed from belt_data/lineage.json &mdash; nothing here is hand-written.')}
'''


# ------------------------------------------------------------------ public API

API_DIR = "api"


def generate_api_files(lineage, belt_games, next_game):
    """Three plain JSON files -- current.json (live snapshot), reigns.json,
    and games.json -- straight dumps of data build_site.py already has in
    memory, no extra computation. For developers/fans who want to build
    their own bot, widget, or stat page off the same data this site uses."""
    current = lineage["reigns"][-1]
    today = date.today()
    team_reign_num = sum(1 for r in lineage["reigns"] if r["team"] == current["team"])

    current_payload = {
        "holder": current["team"],
        "since": current["start_date"],
        "days_held": reign_duration_days(current, today),
        "defenses": current.get("defenses", 0),
        "team_reign_number": team_reign_num,
        "next_game": next_game,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "site": SITE_URL,
    }
    return {
        "current.json": current_payload,
        "reigns.json": {"reigns": lineage["reigns"], "generated_at": current_payload["generated_at"]},
        "games.json": {"belt_games": belt_games, "generated_at": current_payload["generated_at"]},
    }


def generate_api_docs_page():
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>API — The College Football Belt</title>
<link rel="stylesheet" href="styles.css?v={STYLES_VERSION}">
{head_extras()}

{site_header('', 'api')}

<main class="wrap">
  <p class="kicker pageKicker">Developers</p>
  <h1 class="pageTitle">Data API</h1>
  <p class="lede">Three plain, unauthenticated JSON files, regenerated on every site update &mdash;
    the same data this site itself is built from. Free to build on; a link back to
    collegefootballbelt.com is appreciated but not required.</p>

  <p class="embedLabel">GET {SITE_URL}/api/current.json</p>
  <p class="lede" style="margin-top:0">The current holder, since when, days held, defenses,
    that team&rsquo;s own reign number, and the next scheduled belt game (or <code class="mono">null</code>).</p>
  <textarea class="embedCode" rows="3" readonly aria-label="Example JSON response" onclick="this.select()">{{
  "holder": "Notre Dame", "since": "2025-11-29", "days_held": 660,
  "defenses": 2, "team_reign_number": 9, "next_game": {{ ... }} | null,
  "generated_at": "2026-01-01T00:00:00Z", "site": "{SITE_URL}"
}}</textarea>

  <p class="embedLabel">GET {SITE_URL}/api/reigns.json</p>
  <p class="lede" style="margin-top:0">Every reign in belt history: team, start/end dates,
    who it was won from, the score, and defenses. Same shape as
    <code class="mono">belt_data/lineage.json</code>&rsquo;s own <code class="mono">reigns</code> array.</p>

  <p class="embedLabel">GET {SITE_URL}/api/games.json</p>
  <p class="lede" style="margin-top:0">Every belt game ever played: date, teams, score, and outcome.
    Same shape as <code class="mono">belt_data/lineage.json</code>&rsquo;s own
    <code class="mono">belt_games</code> array.</p>

  <p class="noteBox">GitHub Pages doesn&rsquo;t reliably send CORS headers on a custom domain, so a
    plain client-side <code class="mono">fetch()</code> from another site may get blocked by the
    browser. These work fine server-side (curl, a script, a bot) or from the same origin; a
    browser-based cross-origin widget may need a small proxy on your end.</p>
</main>

{site_footer('', 'Sourced from the College Football Data API; this site&rsquo;s own derived data is free to reuse.')}
'''


# -------------------------------------------------------------------- main

# --------------------------------------------------- sitemap / robots / feed / 404

def generate_search_index(lineage, belt_games, team_slugs, seasons, conference_lineages):
    """The header search box's data: every program in belt history (its own
    page if it ever held the belt, otherwise the All Games filter), every
    season, every conference belt, and the section pages -- fetched once,
    lazily, the first time a visitor focuses the box (see head_extras())."""
    holders = set(team_slugs)
    entries = []
    for name in sorted({t for g in belt_games for t in (g["home"], g["away"])}):
        slug = team_slug(name)
        if slug in holders:
            entries.append({"n": name, "u": f"teams/{slug}.html", "t": "Team"})
        else:
            entries.append({"n": name, "u": f"all-games.html?q={quote(name)}", "t": "Belt games", "k": "challenger"})
    for y in seasons:
        entries.append({"n": f"{y} season", "u": f"season-{y}.html", "t": "Season", "k": str(y)})
    for slug, conf in sorted(conference_lineages.items()):
        entries.append({"n": f"{conf.get('conference', slug)} belt", "u": f"conferences/{slug}.html",
                        "t": "Conference belt", "k": conf.get("classification", "")})
    for key, href, label in NAV_PRIMARY[1:] + [x for _, items in NAV_MORE for x in items]:
        if href.startswith(("http", "mailto:")):
            continue
        entries.append({"n": label, "u": href, "t": "Page"})
    return entries


# ====================================================================
# 2026-09-16: the "cool pages" batch -- season outlook, reign pages,
# rivalry pages, About, the all-time timeline strip, belt-game career
# leaders, the heartbreak list, the lean's ledger, the daily puzzle,
# state pages, decade pages, and six more data-driven stories. Every one
# of these is computed from data build_site.py already loads (the
# lineage, box scores, team colors, belt_risk.json, the AI-preview
# ledger); none of them adds an API call.
# ====================================================================

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "Washington, D.C.",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def page_head(title, description, rel="", extra=""):
    """The <head> boilerplate every page in this batch shares."""
    return (f'<!doctype html>\n<html lang="en">\n<meta charset="UTF-8">\n<title>{esc(title)}</title>\n'
            f'<meta name="description" content="{esc(description)}">\n'
            f'<link rel="stylesheet" href="{rel}styles.css?v={STYLES_VERSION}">\n{head_extras(rel)}\n{extra}')


def page_intro(kicker, title, lede_html=""):
    lede = f'\n    <p class="lede">{lede_html}</p>' if lede_html else ""
    return (f'<div class="pageIntro">\n    <span class="kicker">{kicker}</span>\n'
            f'    <h1 class="pageTitle">{title}</h1>{lede}\n  </div>')


def team_link(name, rel="", holders=None):
    """A program's name linked to its team page when it has one (only
    programs that have held the belt do), plain text otherwise."""
    if holders is not None and name not in holders:
        return esc(name)
    return f'<a href="{rel}teams/{team_slug(name)}.html">{esc(name)}</a>'


def game_score_winner_first(g):
    """(winner, loser, winner_pts, loser_pts) for a belt game; a tie
    returns (holder, opponent, pts, pts)."""
    h, a = (int(x) for x in g["score"].split("-"))
    if h == a:
        return g["new_holder"], (g["away"] if g["new_holder"] == g["home"] else g["home"]), h, a
    if h > a:
        return g["home"], g["away"], h, a
    return g["away"], g["home"], a, h


def reign_games(belt_games):
    """{reign_number: [belt games in that reign, in order]} -- the
    title-winning game is the first game of its reign (compute_sequence's
    boxing convention), so a reign's defenses are games[1:] for every
    reign but the first, and the game that ENDED reign n is the first game
    of reign n+1."""
    by_reign = {}
    for g in belt_games:
        by_reign.setdefault(g["reign_number"], []).append(g)
    return by_reign


# ---------------------------------------------------------- season outlook

def generate_outlook_page(belt_risk, lineage, colors, next_game=None):
    """outlook.html -- who's most likely to hold the belt when the season's
    games run out, from fetch_belt_odds.py's full-season walk."""
    reigns = lineage["reigns"]
    holder = reigns[-1]["team"]
    holders = {r["team"] for r in reigns}
    season = (belt_risk or {}).get("season") or {}
    rows = sorted((r for r in (season.get("end_of_season") or []) if r.get("team") and r.get("prob")),
                  key=lambda r: (-r["prob"], r["team"]))
    generated = (belt_risk or {}).get("generated") or ""
    year = (next_game or {}).get("season") or date.today().year

    if not rows:
        body = ('<p class="lede">The outlook is computed on the pipeline&rsquo;s next run once a belt game is '
                'on the schedule &mdash; check back after the next update.</p>')
    else:
        top = rows[0]["prob"]
        items = ""
        for i, row in enumerate(rows, 1):
            team = row["team"]
            pct = row["prob"] * 100
            pct_txt = f"{pct:.0f}%" if pct >= 1 else f"{pct:.1f}%"
            is_holder = team == holder
            width = max(1.5, row["prob"] / top * 100)
            note = '<span class="soonChip">Holds it now</span>' if is_holder else ""
            items += f'''
      <li class="outlookRow{' isHolder' if is_holder else ''}">
        <span class="outlookRank tabular">{i}</span>
        {team_dot(colors, team, 28, "tlDot outlookDot")}
        <span class="outlookTeam">{team_link(team, "", holders)} {note}</span>
        <span class="outlookBar"><span class="outlookFill" style="width:{width:.1f}%"></span></span>
        <span class="outlookPct tabular">{pct_txt}</span>
      </li>'''
        holder_prob = next((r["prob"] for r in rows if r["team"] == holder), 0)
        chance_n = season.get("teams_with_a_chance") or len(rows)
        body = f'''
  <div class="outlookLead">
    <div class="miniStats">
      <div><span class="n tabular">{holder_prob * 100:.0f}%</span><span class="l">{esc(holder)} still holding at season&rsquo;s end</span></div>
      <div><span class="n tabular">{chance_n}</span><span class="l">Programs that finish with it in at least one simulated season</span></div>
      <div><span class="n tabular">{season.get("season_games_modeled", 0):,}</span><span class="l">Unplayed games walked, {season.get("trials", 0):,} times</span></div>
    </div>
  </div>
  <ol class="outlookList">{items}
  </ol>
  <p class="noteBox">How it works: every remaining game on every schedule is simulated {season.get("trials", 0):,} times using
    CFBD&rsquo;s current Elo ratings (with a modest home-field bump). When the holder loses, the belt moves to the winner and the
    simulation keeps walking down <em>that</em> team&rsquo;s schedule, exactly the way the real lineage works &mdash; so a program
    that never plays the current holder can still show up here through a chain of results. Bowl and playoff pairings only count once they
    are actually on the schedule. Programs below 0.05% are left off. Refreshed every pipeline run; a for-fun estimate, not a betting product.</p>'''

    return f'''{page_head("Season Outlook — Who Holds the Belt on New Year's Day?",
                     f"{year} College Football Belt season outlook: every program's odds of holding the lineal title when the games run out, simulated from the remaining schedule.")}

{site_header('', 'outlook')}

<main class="wrap">
  {page_intro(f"Season outlook &middot; {year}", "Who holds the belt when the season ends?",
              f'Every program&rsquo;s chance of owning the belt once the {year} games run out, simulated from the remaining schedule'
              + (f' as of {esc(generated)}' if generated else '') + '. <a href="my-team.html">Your team&rsquo;s actual path is here.</a>')}
  {body}
</main>

{site_footer('', 'Elo ratings from the College Football Data API; the simulation is this site&rsquo;s own.')}
'''


# ------------------------------------------------------------- reign pages

def generate_reign_pages(lineage, colors, belt_games, reigns_dir):
    """One page per reign, chronological: reigns/<n>.html for reign #n --
    the game that won it, every defense, the game that ended it, and where
    the reign ranks all-time. Returns the number written."""
    reigns = lineage["reigns"]
    today = date.today()
    by_reign = reign_games(belt_games)
    total = len(reigns)
    by_len = sorted(range(total), key=lambda i: reign_duration_days(reigns[i], today), reverse=True)
    len_rank = {i: k + 1 for k, i in enumerate(by_len)}
    by_def = sorted(range(total), key=lambda i: reigns[i].get("defenses", 0), reverse=True)
    def_rank = {i: k + 1 for k, i in enumerate(by_def)}
    team_counts = {}
    os.makedirs(reigns_dir, exist_ok=True)
    written = 0
    for i, r in enumerate(reigns):
        n = i + 1
        team = r["team"]
        team_counts[team] = team_counts.get(team, 0) + 1
        nth = team_counts[team]
        is_current = i == total - 1
        start, end = reign_dates(r, today)
        days = (end - start).days
        games = by_reign.get(n, [])
        win_game = games[0] if games and games[0]["outcome"] in ("changed", "established") else None
        defenses = [g for g in games if g is not win_game]
        end_game = (by_reign.get(n + 1) or [None])[0]
        primary, alt = team_color(colors, team)
        ink, accent = panel_colors(primary, alt)

        if win_game and win_game["outcome"] == "established":
            how = (f'Won the very first game ever played &mdash; <a href="../games/{win_game["game_id"]}.html">'
                   f'{esc(team)} {win_game["score"].replace("-", "&ndash;")} {esc(win_game["opponent"])}</a> on '
                   f'{fmt_date(win_game["date"])} &mdash; and with it the title everything since has traced back to.')
        elif win_game:
            w, l, wp, lp = game_score_winner_first(win_game)
            loc = "at a neutral site" if win_game["neutral"] else ("at home" if win_game["home"] == team else "on the road")
            how = (f'Took the belt from {team_link(win_game["holder"], "../")} '
                   f'<a href="../games/{win_game["game_id"]}.html">{wp}&ndash;{lp}</a> {loc} on {fmt_date(win_game["date"])}.')
        else:
            how = f'Reign began {fmt_date(r["start_date"])}.'

        if is_current:
            ending = f'<p><strong>Still holding.</strong> {days:,} days and counting.</p>'
        elif end_game:
            w, l, wp, lp = game_score_winner_first(end_game)
            loc = "at a neutral site" if end_game["neutral"] else ("at home" if end_game["home"] == team else "on the road")
            ending = (f'<p>Lost the belt to {team_link(w, "../")} <a href="../games/{end_game["game_id"]}.html">'
                      f'{lp}&ndash;{wp}</a> {loc} on {fmt_date(end_game["date"])}, after {days:,} days.</p>')
        else:
            ending = f'<p>Reign ended {fmt_date(r["end_date"])}.</p>'

        def_rows = ""
        for g in defenses:
            w, l, wp, lp = game_score_winner_first(g)
            h, a = (int(x) for x in g["score"].split("-"))
            tag = "Tie, belt kept" if h == a else "Defended"
            loc = "vs." if (g["neutral"] or g["home"] == team) else "at"
            def_rows += (f'<a class="miniRow" href="../games/{g["game_id"]}.html"><span>{fmt_date(g["date"])} &middot; '
                         f'{loc} <strong>{esc(g["opponent"])}</strong> {wp}&ndash;{lp}</span><span class="miniTag">{tag}</span></a>')
        def_block = (f'<div class="miniList">{def_rows}</div>' if def_rows else
                     '<p class="emptyNote">No defenses &mdash; the belt was back on the line the very next time out and left.</p>')

        prev_link = f'<a class="prev" href="{n - 1}.html">&larr; Reign #{n - 1}: {esc(reigns[i - 1]["team"])}</a>' if i > 0 else '<span class="disabled prev">&larr; Start of the lineage</span>'
        next_link = f'<a class="next" href="{n + 1}.html">Reign #{n + 1}: {esc(reigns[i + 1]["team"])} &rarr;</a>' if i < total - 1 else '<span class="disabled next">Present day &rarr;</span>'
        span_txt = f'{start.year}' if start.year == end.year else f'{start.year}&ndash;{end.year if not is_current else "present"}'
        title = f'{team} reign #{nth}, {span_txt.replace("&ndash;", "–")} — Belt reign {n} of {total}'
        desc = (f"{team}'s {ordinal(nth)} College Football Belt reign: {fmt_date(r['start_date'])} to "
                f"{'present' if is_current else fmt_date(r['end_date'])}, {days:,} days, {len(defenses)} defense{'s' if len(defenses) != 1 else ''}.")
        crumb = (f'<a href="../index.html">Belt</a> <span class="sep">/</span> <a href="../lineage.html">Full history</a> '
                 f'<span class="sep">/</span> Reign #{n} of {total}')
        html_out = f'''{page_head(title, desc, "../", f'<style>:root{{ --team:{primary}; --team-ink:{ink}; --team-accent:{accent}; }}</style>')}

{site_header('../', 'history', crumb=crumb)}

<main class="wrap">
  <section class="teamPlate">
    <div class="teamPlateRow">
      {logo_chip(colors, team, 56)}
      <div>
        <p class="kicker">Reign #{n} of {total} &middot; {possessive(team)} {ordinal(nth)} reign{" &middot; current holder" if is_current else ""}</p>
        <h1 class="pageTitle">{team_link(team, "../")}, {span_txt}</h1>
      </div>
    </div>
    <div class="heroStats">
      <div><span class="n tabular">{days:,}</span><span class="l">Days held</span></div>
      <div><span class="n tabular">{len(defenses)}</span><span class="l">Defense{"s" if len(defenses) != 1 else ""}</span></div>
      <div><span class="n tabular">#{len_rank[i]}</span><span class="l">Longest of {total}</span></div>
      <div><span class="n tabular">#{def_rank[i]}</span><span class="l">Most defended of {total}</span></div>
    </div>
  </section>

  <div class="sectionHead"><span class="tag">How it started</span><h2>{fmt_date(r["start_date"])}</h2></div>
  <p class="editorial" style="font-size:17px">{how}</p>

  <div class="sectionHead"><span class="tag">Defenses</span><h2>The belt on the line, {len(defenses)} time{"s" if len(defenses) != 1 else ""}</h2></div>
  {def_block}

  <div class="sectionHead"><span class="tag">How it ended</span><h2>{"Still holding" if is_current else fmt_date(r["end_date"])}</h2></div>
  <div class="editorial" style="font-size:17px">{ending}</div>

  <div class="gameNav">{prev_link}{next_link}</div>
</main>

{site_footer('../', 'Every reign computed from the College Football Data API.')}
'''
        with open(os.path.join(reigns_dir, f"{n}.html"), "w", encoding="utf-8") as f:
            f.write(html_out)
        written += 1
    return written


# ----------------------------------------------------------- rivalry pages

RIVALRY_MIN_GAMES = 3


def rivalry_pairs(belt_games):
    """{(teamA, teamB) sorted: [games]} for every pair that has met with
    the belt on the line, games chronological."""
    pairs = {}
    for g in belt_games:
        key = tuple(sorted((g["home"], g["away"])))
        pairs.setdefault(key, []).append(g)
    return pairs


def rivalry_slug(a, b):
    return f"{team_slug(a)}-vs-{team_slug(b)}"


def _rivalry_summary(a, b, games):
    wins = {a: 0, b: 0}
    ties = 0
    changes = 0
    for g in games:
        h, aw = (int(x) for x in g["score"].split("-"))
        if h == aw:
            ties += 1
        else:
            wins[g["home"] if h > aw else g["away"]] += 1
        if g["outcome"] == "changed":
            changes += 1
    return wins, ties, changes


def generate_rivalry_pages(lineage, colors, belt_games, rivalries_dir):
    """rivalries/<a>-vs-<b>.html for every pair with RIVALRY_MIN_GAMES+ belt
    meetings, plus rivalries/index.html. Returns the list of (slug, a, b)."""
    holders = {r["team"] for r in lineage["reigns"]}
    pairs = {k: v for k, v in rivalry_pairs(belt_games).items() if len(v) >= RIVALRY_MIN_GAMES}
    os.makedirs(rivalries_dir, exist_ok=True)
    index_rows = []
    written = []
    for (a, b), games in sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        wins, ties, changes = _rivalry_summary(a, b, games)
        slug = rivalry_slug(a, b)
        leader = a if wins[a] > wins[b] else (b if wins[b] > wins[a] else None)
        if leader:
            trailer = b if leader == a else a
            record_txt = f'{esc(leader)} leads {wins[leader]}&ndash;{wins[trailer]}' + (f'&ndash;{ties}' if ties else '')
        else:
            record_txt = f'Even at {wins[a]}&ndash;{wins[b]}' + (f'&ndash;{ties}' if ties else '')
        first, last = games[0], games[-1]
        span_years = last["date"][:4] if first["date"][:4] == last["date"][:4] else f'{first["date"][:4]}&ndash;{last["date"][:4]}'
        rows = ""
        for g in reversed(games):
            w, l, wp, lp = game_score_winner_first(g)
            h, aw = (int(x) for x in g["score"].split("-"))
            if g["outcome"] in ("changed", "established"):
                tag = "Changed hands"
            elif h == aw:
                tag = "Tie, holder kept it"
            else:
                tag = "Defended"
            where = "neutral site" if g["neutral"] else f'at {esc(g["home"])}'
            rows += (f'<a class="miniRow" href="../games/{g["game_id"]}.html"><span>{fmt_date(g["date"])} &middot; '
                     f'<strong>{esc(w)}</strong> {wp}&ndash;{lp} &middot; {where}</span><span class="miniTag">{tag}</span></a>')
        gaps = [(date.fromisoformat(games[k + 1]["date"]) - date.fromisoformat(games[k]["date"])).days for k in range(len(games) - 1)]
        longest_gap = max(gaps) if gaps else 0
        a_p, a_alt = team_color(colors, a)
        b_p, b_alt = team_color(colors, b)
        a_ink, a_acc = panel_colors(a_p, a_alt)
        b_ink, b_acc = panel_colors(b_p, b_alt)
        title = f"{a} vs. {b} — College Football Belt rivalry"
        desc = (f"All {len(games)} College Football Belt games between {a} and {b}, {span_years.replace('&ndash;', '–')}: "
                f"{record_txt.replace('&ndash;', '–')} with the belt on the line, {changes} title change{'s' if changes != 1 else ''}.")
        html_out = f'''{page_head(title, desc, "../", f'<style>:root{{ --home:{a_p}; --home-ink:{a_ink}; --home-accent:{a_acc}; --away:{b_p}; --away-ink:{b_ink}; --away-accent:{b_acc}; }}</style>')}

{site_header('../', 'rivalries', crumb=f'<a href="../index.html">Belt</a> <span class="sep">/</span> <a href="index.html">Rivalries</a> <span class="sep">/</span> {esc(a)} vs. {esc(b)}')}

<main class="wrap">
  <h1 class="srOnly">{esc(a)} vs. {esc(b)}: every belt game</h1>
  <div class="matchHead" style="margin-top:22px">
    <div class="matchSide home">
      <span class="kicker">{wins[a]} belt win{"s" if wins[a] != 1 else ""} in the series</span>
      <div class="matchTeam">{logo_chip(colors, a, 56)}<span class="matchName">{team_link(a, "../", holders)}</span></div>
    </div>
    <div class="matchCenter">
      <span class="kicker">Belt games</span>
      <span class="matchDay">{len(games)}</span>
      <span class="matchWhen">{span_years}<br>{record_txt}</span>
    </div>
    <div class="matchSide away">
      <span class="kicker">{wins[b]} belt win{"s" if wins[b] != 1 else ""} in the series</span>
      <div class="matchTeam">{logo_chip(colors, b, 56)}<span class="matchName">{team_link(b, "../", holders)}</span></div>
    </div>
  </div>
  <div class="stakes">
    <div class="stakeCell"><span class="kicker">Title changes</span><span class="stakeVal">The belt changed hands {changes} time{"s" if changes != 1 else ""} in this series</span></div>
    <div class="stakeCell"><span class="kicker">First meeting</span><span class="stakeVal">{fmt_date(first["date"])}</span></div>
    <div class="stakeCell"><span class="kicker">Most recent</span><span class="stakeVal">{fmt_date(last["date"])}</span></div>
    <div class="stakeCell"><span class="kicker">Longest gap</span><span class="stakeVal">{fmt_duration(date(1900, 1, 1), date(1900, 1, 1) + timedelta(days=longest_gap)) if longest_gap else "&mdash;"} between belt meetings</span></div>
  </div>

  <div class="sectionHead"><span class="tag">Every meeting with the belt on the line</span><h2>{len(games)} games, newest first</h2><a class="sectionLink" href="../compare.html">Compare the programs &rarr;</a></div>
  <div class="miniList">{rows}</div>
  <p class="noteBox">Only games where the belt was actually at stake are counted here &mdash; the two programs may have met many more times without it on the line. The all-time series lives on the <a href="../compare.html">compare page</a> and in CFBD&rsquo;s own data.</p>
</main>

{site_footer('../', 'Every belt game sourced from the College Football Data API.')}
'''
        with open(os.path.join(rivalries_dir, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(html_out)
        written.append((slug, a, b))
        index_rows.append(f'''
      <tr>
        <td class="teamCell"><a href="{slug}.html">{esc(a)} vs. {esc(b)}</a></td>
        <td class="tabular">{len(games)}</td>
        <td class="won">{record_txt}</td>
        <td class="tabular">{changes}</td>
        <td class="dates">{span_years}</td>
      </tr>''')

    index_html = f'''{page_head("College Football Belt Rivalries — Who Keeps Meeting for the Belt",
                              f"Every pair of programs that has met {RIVALRY_MIN_GAMES} or more times with the College Football Belt at stake: series records, title changes and every game.", "../")}

{site_header('../', 'rivalries')}

<main class="wrap">
  {page_intro("Rivalries", "The pairs that keep meeting with the belt on the line",
              f"Every pairing with {RIVALRY_MIN_GAMES} or more belt games between them &mdash; {len(written)} rivalries, sorted by how often the belt was at stake when they met.")}
  <div class="tableScroll">
    <table class="reignsTable">
      <thead><tr><th>Rivalry</th><th style="text-align:right">Belt games</th><th>Belt-game record</th><th style="text-align:right">Title changes</th><th>Span</th></tr></thead>
      <tbody>{"".join(index_rows)}
      </tbody>
    </table>
  </div>
</main>

{site_footer('../', 'Every belt game sourced from the College Football Data API.')}
'''
    with open(os.path.join(rivalries_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    return written


# ---------------------------------------------------------------- about page

def generate_about_page(lineage, belt_games):
    reigns = lineage["reigns"]
    totals = lineage["totals"]
    holder = reigns[-1]["team"]
    years = date.today().year - 1869 + 1
    return f'''{page_head("About the College Football Belt",
                     "What the College Football Belt is, how the lineal title is computed from every game since 1869, who runs the site and what is written by software.")}

{site_header('', 'about')}

<main class="wrap storyArticle">
  {page_intro("About", "What this is, and how it works",
              "A lineal championship for college football: one title, passed hand to hand on the field since the first game ever played, and a site that computes every step of it.")}

  <article class="storyChapter">
    <h2>The idea</h2>
    <p>Boxing has lineal champions: to be the champ, you beat the champ. College football never had one, so this site made one. The belt starts with Rutgers, who won the first college football game ever played, 6&ndash;4 over Princeton on November 6, 1869. From that day on, whoever beats the holder takes it. That is the entire rule. No polls, no committees, no rankings &mdash; {years} years and {totals["reigns"]:,} reigns later, {esc(holder)} holds it.</p>
    <p>Because the rule is so simple, the belt goes places the polls never would. It spends years in the Ivy League, wanders through programs that no longer field teams, disappears into the middle of a conference for a decade, and then a single September upset sends it somewhere new. The <a href="lineage.html">full history</a> is all {totals["belt_games"]:,} games it was ever on the line for.</p>
  </article>

  <article class="storyChapter">
    <h2>How it&rsquo;s computed</h2>
    <p>Nothing on this site is researched by hand. A script walks every college football game on record, in date order, from the <a href="https://collegefootballdata.com/" target="_blank" rel="noopener">College Football Data</a> API, applying the <a href="ruleset.html">ruleset</a> to each one: if the holder is playing and loses, the belt moves. Ties stay with the holder. A bye, a canceled season or a bowl opt-out just means the belt waits. Every reign, record, map and story on the site is regenerated from that walk several times a week during the season, usually within a few hours of the holder&rsquo;s game going final.</p>
    <p>That also means the site can be wrong exactly one way: if the underlying game record is wrong. When CFBD corrects a score, the next build corrects the lineage. If you spot something that looks off, <a href="mailto:hello@collegefootballbelt.com">email hello@collegefootballbelt.com</a> with the game and we&rsquo;ll trace it.</p>
  </article>

  <article class="storyChapter">
    <h2>What&rsquo;s written by software</h2>
    <p>Three kinds of text on the site are generated rather than written by a person, and each is labeled where it appears: the <a href="preview.html">next-game preview</a> and its prediction (&ldquo;the lean&rdquo;); the short recaps on game pages that have a full box score; and the brief historical notes on older games, which are built only from the matchup, date, score and what it meant for the belt. They are written by Claude, an AI model, strictly from the facts shown on the same page &mdash; a plain-English reading of the numbers, not reporting, and never an invented play, player or crowd. The prediction is a for-fun editorial call, never betting advice, and every pick is graded against the real result on <a href="lean.html">the lean&rsquo;s ledger</a> so you can see how it&rsquo;s doing.</p>
    <p>The <a href="stories.html">stories</a> are a fourth thing: their structure is written by a person, but every name, date and number in them is filled in live from the lineage, so they can&rsquo;t drift out of date as new reigns happen.</p>
  </article>

  <article class="storyChapter">
    <h2>Who runs it</h2>
    <p>collegefootballbelt.com is an independent fan project. It first went online in 2018 and was rebuilt from the ground up in 2026 as the automated site you&rsquo;re reading now. It has no affiliation with any school, conference, the NCAA or the College Football Data project. The site carries a small number of ads to cover hosting and data costs; the data itself is free &mdash; see the <a href="api.html">API</a> if you want to build on it, and the <a href="embed.html">badge</a> if you want the current holder on your own page.</p>
    <p>Follow along at <a href="https://x.com/CollegeFBBelt" target="_blank" rel="noopener">@CollegeFBBelt on X</a> and <a href="https://www.instagram.com/collegefbbelt/" target="_blank" rel="noopener">Instagram</a>, or get an email only when the belt changes hands via the <a href="feed.xml">feed</a>. Privacy details are <a href="privacy.html">here</a>.</p>
  </article>
</main>

{site_footer('', 'An independent fan project. Every belt game sourced from the College Football Data API.')}
'''


# ------------------------------------------------------- the timeline strip

def generate_timeline_page(lineage, colors, belt_games):
    """timeline.html -- every reign since 1869 as a colored block scaled to
    its length, one row per decade, in inline SVG."""
    reigns = lineage["reigns"]
    today = date.today()
    holders_days = {}
    for r in reigns:
        holders_days[r["team"]] = holders_days.get(r["team"], 0) + reign_duration_days(r, today)
    first_year = int(reigns[0]["start_date"][:4])
    last_year = today.year
    dec_start = first_year - first_year % 10
    decades = list(range(dec_start, last_year + 1, 10))
    W, ROW, GAP, LABEL_W, TOP = 1000, 34, 14, 46, 22
    height = TOP + len(decades) * (ROW + GAP)
    parts = []
    for row_i, d0 in enumerate(decades):
        y = TOP + row_i * (ROW + GAP)
        row_start = date(d0, 1, 1)
        row_end = date(min(d0 + 10, last_year + 1), 1, 1)
        span = (row_end - row_start).days
        parts.append(f'<text x="0" y="{y + ROW - 11}" class="tlLabel">{d0}s</text>')
        parts.append(f'<rect x="{LABEL_W}" y="{y}" width="{W - LABEL_W}" height="{ROW}" class="tlTrack"/>')
        for yr in range(d0, d0 + 10):
            if yr < first_year or yr > last_year:
                continue
            x = LABEL_W + (W - LABEL_W) * (date(yr, 1, 1) - row_start).days / span
            parts.append(f'<line x1="{x:.1f}" y1="{y}" x2="{x:.1f}" y2="{y + ROW}" class="tlTick"/>')
        for i, r in enumerate(reigns):
            s, e = reign_dates(r, today)
            cs, ce = max(s, row_start), min(e, row_end)
            if ce <= cs:
                continue
            x = LABEL_W + (W - LABEL_W) * (cs - row_start).days / span
            w = max(0.8, (W - LABEL_W) * (ce - cs).days / span)
            primary, _ = team_color(colors, r["team"])
            days = (e - s).days
            tip = f'{r["team"]} · {fmt_date(r["start_date"])} – {"present" if i == len(reigns) - 1 else fmt_date(r["end_date"])} · {days:,} days'
            parts.append(f'<a href="reigns/{i + 1}.html"><rect x="{x:.2f}" y="{y}" width="{w:.2f}" height="{ROW}" fill="{primary}"><title>{esc(tip)}</title></rect></a>')
    svg = (f'<svg class="tlSvg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" role="group" '
           f'aria-label="Every belt reign since {first_year} as a colored bar scaled to its length, one row per decade">'
           + "".join(parts) + '</svg>')
    top_holders = sorted(holders_days.items(), key=lambda kv: -kv[1])[:10]
    legend = "".join(
        f'<li><span class="swatch" style="background:{team_color(colors, t)[0]};width:14px;height:14px"></span>'
        f'<a href="teams/{team_slug(t)}.html">{esc(t)}</a> <span class="mono" style="color:var(--ink-soft);font-size:12px">{d:,} days</span></li>'
        for t, d in top_holders)
    longest_i = max(range(len(reigns)), key=lambda i: reign_duration_days(reigns[i], today))
    longest = reigns[longest_i]
    return f'''{page_head("The Belt Timeline, 1869 to Today — Every Reign at a Glance",
                     f"Every College Football Belt reign since {first_year} on one timeline: {len(reigns)} reigns, {len(holders_days)} programs, each bar scaled to how long they held the title.")}

{site_header('', 'timeline')}

<main class="wrap">
  {page_intro("The whole belt on one page", f"{first_year} to today, reign by reign",
              f"Each bar is one reign, in that program&rsquo;s color, as wide as it lasted; each row is a decade and each tick is a New Year&rsquo;s Day. Hover or tap a bar for the reign, click through for its page. The widest bar you&rsquo;ll find is <a href='reigns/{longest_i + 1}.html'>{possessive(longest['team'])} {fmt_duration(*reign_dates(longest, today))}</a>.")}
  <div class="tlWrap">{svg}</div>
  <div class="sectionHead"><span class="tag">Most time with the belt</span><h2>Ten programs, {sum(d for _, d in top_holders):,} days between them</h2><a class="sectionLink" href="records.html">All records &rarr;</a></div>
  <ul class="tlLegend">{legend}</ul>
</main>

{site_footer('', 'Every reign computed from the College Football Data API; bars are drawn to the day.')}
'''


# ----------------------------------------------------- belt-game leaders

def _num(v):
    try:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if "/" in s or "-" in s[1:]:
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


LEADER_BOARDS = [
    # (title, category, column, label, per-game?)
    ("Passing yards", "passing", "YDS", "yards"),
    ("Passing touchdowns", "passing", "TD", "TD"),
    ("Rushing yards", "rushing", "YDS", "yards"),
    ("Rushing touchdowns", "rushing", "TD", "TD"),
    ("Receiving yards", "receiving", "YDS", "yards"),
    ("Receptions", "receiving", "REC", "catches"),
    ("Tackles", "defensive", "TOT", "tackles"),
    ("Sacks", "defensive", "SACKS", "sacks"),
    ("Interceptions", "interceptions", "INT", "INT"),
]


def generate_leaders_page(belt_games, details, colors):
    """leaders.html -- career totals in belt games only, from the box
    scores on file (2003 onward), plus single-game bests."""
    games_with = [g for g in belt_games if details.get(str(g["game_id"]), {}).get("player_stats")]
    players = {}   # key -> {name, id, teams:set, games:set, totals:{(cat,col): n}, bests:{(cat,col): (n, game)}}
    for g in games_with:
        ps = details[str(g["game_id"])]["player_stats"]
        for cat, data in ps.items():
            cat_l = cat.lower()
            for row in data.get("rows", []):
                name = row.get("player") or ""
                if not name:
                    continue
                pid = row.get("player_id")
                key = f"id:{pid}" if pid else f"name:{name}|{row.get('team')}"
                p = players.setdefault(key, {"name": name, "id": pid, "teams": set(), "games": set(), "totals": {}, "bests": {}})
                p["teams"].add(row.get("team") or "")
                p["games"].add(g["game_id"])
                for col, val in (row.get("stats") or {}).items():
                    n = _num(val)
                    if n is None:
                        continue
                    k = (cat_l, col.upper())
                    p["totals"][k] = p["totals"].get(k, 0) + n
                    if n > p["bests"].get(k, (0, None))[0]:
                        p["bests"][k] = (n, g)
    first_year = games_with[0]["date"][:4] if games_with else "2003"

    def link(p):
        slug = player_page_slug(p["id"], p["name"]) if p["id"] else None
        if slug:
            return f'<a href="players/{esc(slug)}.html">{esc(p["name"])}</a>'
        return esc(p["name"])

    def fmt_n(n):
        return f"{n:,.0f}" if float(n).is_integer() else f"{n:,.1f}"

    boards = ""
    for title, cat, col, label in LEADER_BOARDS:
        k = (cat, col)
        ranked = sorted((p for p in players.values() if p["totals"].get(k)), key=lambda p: -p["totals"][k])[:10]
        if not ranked:
            continue
        rows = ""
        for i, p in enumerate(ranked, 1):
            teams = ", ".join(sorted(t for t in p["teams"] if t))
            games_n = len(p["games"])
            best_n, best_g = p["bests"].get(k, (0, None))
            best = (f'best {fmt_n(best_n)} <a href="games/{best_g["game_id"]}.html">vs {esc(best_g["away"] if best_g["home"] in p["teams"] else best_g["home"])}, {best_g["date"][:4]}</a>'
                    if best_g else "")
            rows += _record_row(i, team_color(colors, next(iter(p["teams"]), ""))[0], f'{link(p)} <span class="playerTeam">{esc(teams)}</span>',
                                f'{fmt_n(p["totals"][k])} <span class="recordUnit">{label}</span>',
                                f'{games_n} belt game{"s" if games_n != 1 else ""} &middot; {best}')
        boards += f'''
    <section class="recordCard">
      <h2>{title}</h2>
      <p class="recordCardSub">Career totals in belt games</p>
      <div class="recordList">{rows}</div>
    </section>'''
    most_games = sorted(players.values(), key=lambda p: -len(p["games"]))[:10]
    rows = ""
    for i, p in enumerate(most_games, 1):
        teams = ", ".join(sorted(t for t in p["teams"] if t))
        rows += _record_row(i, team_color(colors, next(iter(p["teams"]), ""))[0], f'{link(p)} <span class="playerTeam">{esc(teams)}</span>',
                            f'{len(p["games"])} <span class="recordUnit">{_plural(len(p["games"]), "game")}</span>', "appeared in the box score")
    games_board = f'''
    <section class="recordCard">
      <h2>Most belt games played</h2>
      <p class="recordCardSub">Appearances in a belt-game box score</p>
      <div class="recordList">{rows}</div>
    </section>''' if rows else ""

    return f'''{page_head("Belt-Game Career Leaders — The College Football Belt",
                     f"Career leaders in College Football Belt games since {first_year}: passing, rushing and receiving yards, touchdowns, tackles, sacks and interceptions with the title at stake.")}

{site_header('', 'leaders')}

<main class="wrap">
  {page_intro("Leaders", "The belt-game career leaders",
              f"Totals from every belt game with a box score on file ({first_year} onward, {len(games_with):,} games, {len(players):,} players) &mdash; only the games where the belt was actually on the line count. Nobody else keeps this stat, because nobody else has the lineage.")}
  <div class="recordsGrid">{games_board}{boards}
  </div>
  <p class="noteBox">Box scores come from CFBD&rsquo;s <span class="mono">/games/players</span> data, which starts in 2003, so a career here is a career in the belt-game era &mdash; the belt itself is older than every player on this page by a century. Names link to that player&rsquo;s stat line in every belt game they appeared in.</p>
</main>

{site_footer('', 'Player stats from the College Football Data API, belt games only.')}
'''


# ------------------------------------------------------------- heartbreak

def generate_heartbreak_page(lineage, colors, belt_games):
    """heartbreak.html -- the programs that have played for the belt the
    most without ever winning it, and how close they came."""
    holders = {r["team"] for r in lineage["reigns"]}
    stats = {}
    for g in belt_games:
        if g["outcome"] in ("changed", "established"):
            continue
        challenger = g["opponent"]
        h, a = (int(x) for x in g["score"].split("-"))
        margin = abs(h - a)
        st = stats.setdefault(challenger, {"games": 0, "losses": 0, "ties": 0, "closest": None, "last": None, "first": None})
        st["games"] += 1
        if h == a:
            st["ties"] += 1
        else:
            st["losses"] += 1
        if h != a and (st["closest"] is None or margin < st["closest"][0]):
            st["closest"] = (margin, g)
        st["last"] = g
        st["first"] = st["first"] or g
    never = {t: s for t, s in stats.items() if t not in holders}
    most = sorted(never.items(), key=lambda kv: (-kv[1]["games"], kv[0]))[:15]
    closest = sorted((s["closest"] + (t,) for t, s in never.items() if s["closest"]), key=lambda x: (x[0], x[1]["date"]))[:15]
    all_losses = sorted(stats.items(), key=lambda kv: (-kv[1]["losses"], kv[0]))[:10]

    def row_most(i, t, s):
        sub = f'{s["losses"]} loss{"es" if s["losses"] != 1 else ""}' + (f', {s["ties"]} tie{"s" if s["ties"] != 1 else ""}' if s["ties"] else "")
        sub += f' &middot; {s["first"]["date"][:4]}&ndash;{s["last"]["date"][:4]}'
        if s["closest"]:
            m, g = s["closest"]
            sub += f' &middot; closest: <a href="games/{g["game_id"]}.html">{m} point{"s" if m != 1 else ""}, {g["date"][:4]}</a>'
        return _record_row(i, team_color(colors, t)[0], esc(t), f'{s["games"]} <span class="recordUnit">belt games</span>', sub)

    most_rows = "".join(row_most(i, t, s) for i, (t, s) in enumerate(most, 1))
    close_rows = ""
    for i, (m, g, t) in enumerate(closest, 1):
        w, l, wp, lp = game_score_winner_first(g)
        close_rows += _record_row(i, team_color(colors, t)[0], f'{esc(t)} <span class="playerTeam">{g["date"][:4]}</span>',
                                  f'{m} <span class="recordUnit">point{"s" if m != 1 else ""}</span>',
                                  f'lost {lp}&ndash;{wp} to {esc(w)}', href=f'games/{g["game_id"]}.html')
    loss_rows = ""
    for i, (t, s) in enumerate(all_losses, 1):
        held = "has held it" if t in holders else "never held it"
        loss_rows += _record_row(i, team_color(colors, t)[0], team_link(t, "", holders), f'{s["losses"]} <span class="recordUnit">losses</span>',
                                 f'{s["games"]} belt games as the challenger &middot; {held}')
    n_never = len(never)
    total_never_games = sum(s["games"] for s in never.values())
    return f'''{page_head("The Heartbreak List — Programs That Never Won the Belt",
                     f"The {n_never} programs that have played for the College Football Belt without ever winning it: who came closest, who tried the most and who has lost the most belt games.")}

{site_header('', 'heartbreak')}

<main class="wrap">
  {page_intro("Heartbreak", "Played for it. Never held it.",
              f"{n_never} programs have lined up against the belt holder &mdash; {total_never_games:,} games between them &mdash; and walked off without it every time. The mirror image of the <a href='records.html'>records page</a>.")}
  <div class="recordsGrid">
    <section class="recordCard">
      <h2>Most belt games without ever winning it</h2>
      <p class="recordCardSub">Programs that have never held the belt, by attempts</p>
      <div class="recordList">{most_rows}</div>
    </section>
    <section class="recordCard">
      <h2>Closest calls</h2>
      <p class="recordCardSub">Narrowest losses by programs that still haven&rsquo;t won it</p>
      <div class="recordList">{close_rows}</div>
    </section>
    <section class="recordCard">
      <h2>Most belt games lost, all programs</h2>
      <p class="recordCardSub">Losses as the challenger, holders included</p>
      <div class="recordList">{loss_rows}</div>
    </section>
  </div>
  <p class="noteBox">A belt game the challenger loses (or ties, since a tie keeps the belt with the holder) counts as an attempt. Programs that have held the belt at any point are excluded from the first two lists &mdash; their waiting is measured on the records page as a drought, not here as heartbreak.</p>
</main>

{site_footer('', 'Every belt game sourced from the College Football Data API.')}
'''


# ---------------------------------------------------------- the lean's ledger

LEDGER_PATH = os.path.join("ai_preview_cache", "ledger.json")


def load_ledger():
    if not os.path.exists(LEDGER_PATH):
        return []
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _parse_predicted_score(txt, holder, opponent):
    """'Notre Dame 42, Michigan State 17' -> (42, 17) in holder/opponent
    order, or None when it can't be read."""
    if not txt:
        return None
    found = {}
    for part in txt.split(","):
        m = re.search(r"(.+?)\s+(\d+)\s*$", part.strip())
        if m:
            found[m.group(1).strip()] = int(m.group(2))
    if holder in found and opponent in found:
        return found[holder], found[opponent]
    return None


def grade_ledger(ledger, belt_games, today):
    """Match each pick to the real game and grade it. Returns rows newest
    first plus the record."""
    by_pair = {}
    for g in belt_games:
        by_pair.setdefault(frozenset((g["home"], g["away"])), []).append(g)
    rows = []
    wins = losses = 0
    margins = []
    for e in ledger:
        holder, opp = e.get("holder"), e.get("opponent")
        d = e.get("date") or ""
        game = None
        for g in by_pair.get(frozenset((holder, opp)), []):
            try:
                if abs((date.fromisoformat(g["date"]) - date.fromisoformat(d)).days) <= 8:
                    game = g
                    break
            except ValueError:
                continue
        pick = e.get("predicted_winner") or ""
        pred = _parse_predicted_score(e.get("predicted_score"), holder, opp)
        if game:
            w, l, wp, lp = game_score_winner_first(game)
            h, a = (int(x) for x in game["score"].split("-"))
            tie = h == a
            actual_winner = None if tie else w
            correct = (not tie) and pick == actual_winner
            if not tie:
                if correct:
                    wins += 1
                else:
                    losses += 1
            if pred and not tie:
                actual_margin = (h if game["home"] == holder else a) - (a if game["home"] == holder else h)
                margins.append(abs((pred[0] - pred[1]) - actual_margin))
            rows.append({"e": e, "game": game, "status": "tie" if tie else ("hit" if correct else "miss"),
                         "result": f'{esc(w)} {wp}&ndash;{lp}' if not tie else f'Tie, {h}&ndash;{a}'})
        else:
            try:
                pending = date.fromisoformat(d) >= today - timedelta(days=1)
            except ValueError:
                pending = True
            rows.append({"e": e, "game": None, "status": "pending" if pending else "unmatched", "result": ""})
    rows.sort(key=lambda r: r["e"].get("date") or "", reverse=True)
    avg_margin = (sum(margins) / len(margins)) if margins else None
    return rows, wins, losses, avg_margin


def generate_lean_page(lineage, belt_games, colors):
    ledger = load_ledger()
    today = date.today()
    holders = {r["team"] for r in lineage["reigns"]}
    rows, wins, losses, avg_margin = grade_ledger(ledger, belt_games, today)
    graded = wins + losses
    if not ledger:
        body = ('<p class="lede">The ledger starts with the next preview: every pick the lean makes from here on is logged '
                'the moment it&rsquo;s written and graded against the real score once the game goes final. Nothing gets revised after the fact.</p>')
    else:
        pct = f"{wins / graded * 100:.0f}%" if graded else "&mdash;"
        margin_txt = f"{avg_margin:.1f}" if avg_margin is not None else "&mdash;"
        items = ""
        for r in rows:
            e = r["e"]
            status = r["status"]
            label = {"hit": "Hit", "miss": "Miss", "tie": "Tie", "pending": "Pending", "unmatched": "No result on file"}[status]
            # older ledger entries may predate the is_home field: only say "at" when we know it was a road game
            loc = "at" if (e.get("is_home") is False and not e.get("neutral")) else "vs"
            game_link = f'<a href="games/{r["game"]["game_id"]}.html">{r["result"]}</a>' if r["game"] else ('Kicks off ' + fmt_date(e["date"]) if status == "pending" else "&mdash;")
            items += f'''
      <div class="ledgerRow {status}">
        <span class="ledgerDate mono">{fmt_date(e["date"])}</span>
        <span class="ledgerGame">{team_link(e["holder"], "", holders)} {loc} {team_link(e["opponent"], "", holders)}</span>
        <span class="ledgerPick">Pick: <strong>{esc(e.get("predicted_winner") or "—")}</strong>{(" &middot; " + esc(e["predicted_score"])) if e.get("predicted_score") else ""}</span>
        <span class="ledgerResult">{game_link}</span>
        <span class="ledgerTag">{label}</span>
      </div>'''
        body = f'''
  <div class="miniStats" style="margin:10px 0 26px">
    <div><span class="n tabular">{wins}&ndash;{losses}</span><span class="l">Record on graded picks</span></div>
    <div><span class="n tabular">{pct}</span><span class="l">Hit rate</span></div>
    <div><span class="n tabular">{margin_txt}</span><span class="l">Avg. points off the predicted margin</span></div>
    <div><span class="n tabular">{len(ledger)}</span><span class="l">Picks logged</span></div>
  </div>
  <div class="ledger">{items}
  </div>'''
    return f'''{page_head("The Lean's Ledger — How the Belt Preview's Picks Have Done",
                     "Every pick the College Football Belt's AI-written game preview has made, logged when it was written and graded against the real result: the running record.")}

{site_header('', 'lean')}

<main class="wrap">
  {page_intro("Keeping score on ourselves", "The lean&rsquo;s ledger",
              "Every prediction the <a href='preview.html'>preview page</a> makes is logged the moment it&rsquo;s written &mdash; the pick and the score &mdash; and graded here once the game goes final. It&rsquo;s a for-fun editorial call, so the least we can do is keep the receipts.")}
  {body}
  <p class="noteBox">A pick is graded on the winner only; the predicted score is shown so you can judge the margin yourself. A tie (rare, and impossible since 1996) is neither a hit nor a miss. If a game was moved and the preview named the original date, it&rsquo;s matched to the real game within a week either way. Not betting advice &mdash; if it stops being fun, the National Problem Gambling Helpline is 1-800-522-4700.</p>
</main>

{site_footer('', 'Predictions are written by Claude from the stats on the preview page; results from the College Football Data API.')}
'''


# ------------------------------------------------------------ the daily puzzle

DAILY_EPOCH = date(2026, 9, 16)  # puzzle #1


def generate_daily_page(lineage, colors, belt_games):
    """daily.html -- one program to guess per day from a fixed, shuffled
    order of every belt holder; six guesses, a new clue after each miss,
    comparison hints (more/fewer reigns, earlier/later first reign, same
    state), a local streak, and a share line. Entirely client-side."""
    reigns = lineage["reigns"]
    today = date.today()
    holders = sorted({r["team"] for r in reigns})
    by_team = {}
    for r in reigns:
        by_team.setdefault(r["team"], []).append(r)
    pool = []
    for t in holders:
        rs = by_team[t]
        first = int(rs[0]["start_date"][:4])
        last = rs[-1]
        last_year = None if last.get("end_date") is None else int(last["end_date"][:4])
        days = sum(reign_duration_days(r, today) for r in rs)
        defenses = sum(r.get("defenses", 0) for r in rs)
        st = (colors.get(t) or {}).get("state")
        primary, alt = team_color(colors, t)
        pool.append({"n": t, "s": STATE_NAMES.get(st, st or ""), "r": len(rs), "f": first, "l": last_year,
                     "d": days, "df": defenses, "c": primary, "a": alt, "slug": team_slug(t)})
    random.Random(1869).shuffle(pool)
    payload = json.dumps(pool, ensure_ascii=False)
    options = "".join(f'<option value="{esc(t)}">' for t in holders)
    return f'''{page_head("The Daily Belt — Guess Today's Program",
                     "A daily College Football Belt puzzle: guess which program that has held the lineal title fits the clues, in six tries. New puzzle every day; keep your streak.")}

{site_header('', 'daily')}

<main class="wrap">
  {page_intro("Daily puzzle", "The Daily Belt",
              "One program that has held the belt, six guesses, a new clue after every miss. Same puzzle for everyone each day; your streak stays on this device.")}
  <div class="dailyWrap" id="daily">
    <div class="dodStreakBar">
      <div><span class="n" id="dailyNo">#1</span><span class="l">Puzzle</span></div>
      <div><span class="n" id="dailyStreak">0</span><span class="l">Current streak</span></div>
      <div><span class="n" id="dailyBest">0</span><span class="l">Best streak</span></div>
      <div><span class="n" id="dailySolved">0</span><span class="l">Solved</span></div>
    </div>
    <ol class="dailyClues" id="dailyClues"></ol>
    <form class="dailyForm" id="dailyForm" autocomplete="off">
      <label class="srOnly" for="dailyGuess">Your guess</label>
      <input id="dailyGuess" list="dailyTeams" placeholder="Type a program&hellip;" enterkeyhint="go">
      <datalist id="dailyTeams">{options}</datalist>
      <button class="btn" type="submit">Guess</button>
    </form>
    <ol class="dailyGuesses" id="dailyGuesses"></ol>
    <div class="dailyResult" id="dailyResult" hidden></div>
    <p class="emptyNote" id="dailyNoJs">This puzzle needs JavaScript.</p>
  </div>
</main>

{site_footer('', 'Every clue computed from the belt lineage.')}

<script>
(function(){{
  var POOL = {payload};
  var EPOCH = Date.UTC({DAILY_EPOCH.year}, {DAILY_EPOCH.month - 1}, {DAILY_EPOCH.day});
  var MAX = 6;
  var now = new Date();
  var todayUtc = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  var dayNo = Math.floor((todayUtc - EPOCH) / 86400000) + 1;
  if (dayNo < 1) dayNo = 1;
  var target = POOL[(dayNo - 1) % POOL.length];
  var KEY = 'cfbBelt:daily';
  var state = {{ streak: 0, best: 0, solved: 0, lastDay: 0, lastResult: null, guesses: [], day: 0 }};
  try {{ var s = JSON.parse(localStorage.getItem(KEY) || 'null'); if (s) state = Object.assign(state, s); }} catch (e) {{}}
  if (state.day !== dayNo) {{ state.guesses = []; state.day = dayNo; state.done = false; }}
  document.getElementById('dailyNoJs').hidden = true;
  var byName = {{}};
  POOL.forEach(function(p){{ byName[p.n.toLowerCase()] = p; }});
  var CLUES = [
    function(t){{ return 'Has held the belt <strong>' + t.r + '</strong> time' + (t.r === 1 ? '' : 's') + ', for <strong>' + t.d.toLocaleString() + '</strong> days in all.'; }},
    function(t){{ return 'First held it in <strong>' + t.f + '</strong>.'; }},
    function(t){{ return t.l === null ? 'It is the <strong>current holder</strong>.' : 'Last held it in <strong>' + t.l + '</strong>.'; }},
    function(t){{ return 'Home state: <strong>' + (t.s || 'not on file') + '</strong>.'; }},
    function(t){{ return 'Team colors: <span class="swatch" style="background:' + t.c + ';width:14px;height:14px"></span><span class="swatch" style="background:' + t.a + ';width:14px;height:14px"></span> and it has defended the belt <strong>' + t.df + '</strong> time' + (t.df === 1 ? '' : 's') + ' in total.'; }},
    function(t){{ return 'The name starts with <strong>' + t.n.charAt(0) + '</strong> and has <strong>' + t.n.replace(/[^A-Za-z]/g, '').length + '</strong> letters.'; }}
  ];
  function save(){{ try {{ localStorage.setItem(KEY, JSON.stringify(state)); }} catch (e) {{}} }}
  function cmp(a, b){{ return a === b ? '=' : (a < b ? '\\u2191' : '\\u2193'); }}
  function hintFor(g){{
    var bits = [];
    bits.push(g.r === target.r ? 'the same number of reigns' : (g.r < target.r ? 'more reigns' : 'fewer reigns'));
    bits.push(g.f === target.f ? 'a first reign in the same year' : (g.f < target.f ? 'a later first reign' : 'an earlier first reign'));
    bits.push(g.s && g.s === target.s ? 'the same home state' : 'a different home state');
    return bits.join(' \\u00b7 ');
  }}
  function render(){{
    document.getElementById('dailyNo').textContent = '#' + dayNo;
    document.getElementById('dailyStreak').textContent = state.streak;
    document.getElementById('dailyBest').textContent = state.best;
    document.getElementById('dailySolved').textContent = state.solved;
    var shown = Math.min(CLUES.length, state.guesses.length + 1);
    if (state.done) shown = CLUES.length;
    var cl = document.getElementById('dailyClues');
    cl.innerHTML = '';
    for (var i = 0; i < shown; i++) {{ var li = document.createElement('li'); li.innerHTML = CLUES[i](target); cl.appendChild(li); }}
    var gl = document.getElementById('dailyGuesses');
    gl.innerHTML = '';
    state.guesses.forEach(function(name){{
      var g = byName[name.toLowerCase()];
      var li = document.createElement('li');
      var ok = g && g.n === target.n;
      li.className = ok ? 'hit' : 'miss';
      li.innerHTML = '<strong>' + name.replace(/</g, '&lt;') + '</strong> <span class="mono">' + (ok ? 'that\\u2019s it' : (g ? 'the answer has ' + hintFor(g) : 'not a belt holder')) + '</span>';
      gl.appendChild(li);
    }});
    var form = document.getElementById('dailyForm');
    var res = document.getElementById('dailyResult');
    if (state.done) {{
      form.hidden = true;
      var won = state.lastResult === 'won';
      var n = state.guesses.length;
      var squares = state.guesses.map(function(name, i){{ return (i === n - 1 && won) ? '\\uD83D\\uDFE9' : '\\u2B1B'; }}).join('');
      res.hidden = false;
      res.innerHTML = '<p class="dailyAnswer">' + (won ? 'Got it in ' + n + '.' : 'Not today.') + ' The answer was <a href="teams/' + target.slug + '.html">' + target.n + '</a>.</p>' +
        '<div class="btnRow"><button type="button" class="btn ghost" id="dailyShare">Share your result</button><a class="btn ghost" href="teams/' + target.slug + '.html">Their belt history</a></div>' +
        '<p class="emptyNote" id="dailyCopied" hidden>Copied to your clipboard.</p>';
      document.getElementById('dailyShare').addEventListener('click', function(){{
        var text = 'The Daily Belt #' + dayNo + ' ' + (won ? n + '/' + MAX : 'X/' + MAX) + '\\n' + squares + '\\ncollegefootballbelt.com/daily.html';
        var done = function(){{ var c = document.getElementById('dailyCopied'); if (c) c.hidden = false; }};
        if (navigator.share) {{ navigator.share({{ text: text }}).catch(function(){{}}); }}
        else if (navigator.clipboard) {{ navigator.clipboard.writeText(text).then(done, done); }}
      }});
    }} else {{ form.hidden = false; res.hidden = true; }}
  }}
  document.getElementById('dailyForm').addEventListener('submit', function(e){{
    e.preventDefault();
    var input = document.getElementById('dailyGuess');
    var name = input.value.trim();
    if (!name || state.done) return;
    input.value = '';
    state.guesses.push(name);
    var g = byName[name.toLowerCase()];
    if (g && g.n === target.n) {{
      state.done = true; state.lastResult = 'won'; state.solved += 1;
      state.streak = (state.lastDay === dayNo - 1 || state.lastDay === dayNo) ? state.streak + 1 : 1;
      if (state.streak > state.best) state.best = state.streak;
      state.lastDay = dayNo;
    }} else if (state.guesses.length >= MAX) {{
      state.done = true; state.lastResult = 'lost'; state.streak = 0; state.lastDay = dayNo;
    }}
    save(); render();
  }});
  render();
}})();
</script>
'''


# --------------------------------------------------------- state pages

def generate_state_pages(lineage, colors, belt_games, states_dir):
    """states/<code>.html for every state a belt holder calls home, plus
    states/index.html. Returns the list of codes written."""
    reigns = lineage["reigns"]
    today = date.today()
    by_state = {}
    for i, r in enumerate(reigns):
        st = (colors.get(r["team"]) or {}).get("state")
        if not st:
            continue
        by_state.setdefault(st, []).append((i, r))
    os.makedirs(states_dir, exist_ok=True)
    summary = []
    for code, items in by_state.items():
        name = STATE_NAMES.get(code, code)
        teams = {}
        days_total = 0
        for i, r in items:
            d = reign_duration_days(r, today)
            days_total += d
            t = teams.setdefault(r["team"], {"reigns": 0, "days": 0})
            t["reigns"] += 1
            t["days"] += d
        has_holder = reigns[-1]["team"] in teams
        summary.append((code, name, len(items), days_total, len(teams), has_holder))
        team_rows = "".join(
            f'<a class="miniRow" href="../teams/{team_slug(t)}.html"><span>{team_dot(colors, t, 22, "tlDot miniDot")} <strong>{esc(t)}</strong></span>'
            f'<span class="miniTag">{v["reigns"]} reign{"s" if v["reigns"] != 1 else ""} &middot; {v["days"]:,} days</span></a>'
            for t, v in sorted(teams.items(), key=lambda kv: -kv[1]["days"]))
        reign_rows = ""
        for i, r in reversed(items):
            s, e = reign_dates(r, today)
            is_cur = i == len(reigns) - 1
            reign_rows += (f'<a class="miniRow" href="../reigns/{i + 1}.html"><span><strong>{esc(r["team"])}</strong> &middot; '
                           f'{fmt_date(r["start_date"])} &ndash; {"present" if is_cur else fmt_date(r["end_date"])}</span>'
                           f'<span class="miniTag">{fmt_duration(s, e)}</span></a>')
        first_i, first_r = items[0]
        title = f"The College Football Belt in {name}"
        desc = (f"Every College Football Belt reign by a {name} program: {len(items)} reign{'s' if len(items) != 1 else ''} across "
                f"{len(teams)} program{'s' if len(teams) != 1 else ''}, {days_total:,} total days with the lineal title.")
        html_out = f'''{page_head(title, desc, "../")}

{site_header('../', 'states', crumb=f'<a href="../index.html">Belt</a> <span class="sep">/</span> <a href="index.html">States</a> <span class="sep">/</span> {esc(name)}')}

<main class="wrap">
  {page_intro("The belt in", esc(name),
              f"{len(items)} reign{'s' if len(items) != 1 else ''} by {len(teams)} {name} program{'s' if len(teams) != 1 else ''}, {days_total:,} days with the belt in all"
              + (f", starting with {esc(first_r['team'])} in {first_r['start_date'][:4]}" if items else "")
              + (". The belt lives here right now." if has_holder else "."))}
  <div class="twoUp">
    <section>
      <div class="sectionHead"><span class="tag">Programs</span><h2>Who has held it here</h2></div>
      <div class="miniList">{team_rows}</div>
    </section>
    <section>
      <div class="sectionHead"><span class="tag">Reigns</span><h2>Every {esc(name)} reign, newest first</h2></div>
      <div class="miniList">{reign_rows}</div>
    </section>
  </div>
  <p class="moreLink"><a href="../map.html">See the whole map &rarr;</a></p>
</main>

{site_footer('../', 'Home states from the College Football Data API team directory.')}
'''
        with open(os.path.join(states_dir, f"{code.lower()}.html"), "w", encoding="utf-8") as f:
            f.write(html_out)
    summary.sort(key=lambda x: -x[3])
    rows = ""
    for c, n, r, d, t, hh in summary:
        cls = ' class="current"' if hh else ""
        rows += (f'<tr{cls}><td class="teamCell"><a href="{c.lower()}.html">{esc(n)}</a></td><td class="tabular">{r}</td>'
                 f'<td class="tabular">{t}</td><td class="tabular">{d:,}</td><td class="dates">{"Holds it now" if hh else ""}</td></tr>')
    index_html = f'''{page_head("The College Football Belt by State",
                              f"All {len(summary)} states that have been home to a College Football Belt holder, ranked by days with the lineal title, with every reign and program for each.", "../")}

{site_header('../', 'states')}

<main class="wrap">
  {page_intro("Geography", "The belt, state by state",
              f"{len(summary)} states have been home to the belt. Ranked by total days; the <a href='../map.html'>animated map</a> shows the same story in motion.")}
  <div class="tableScroll">
    <table class="reignsTable">
      <thead><tr><th>State</th><th style="text-align:right">Reigns</th><th style="text-align:right">Programs</th><th style="text-align:right">Days held</th><th><span class="srOnly">Current holder</span></th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</main>

{site_footer('../', 'Home states from the College Football Data API team directory.')}
'''
    with open(os.path.join(states_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    return [c for c, *_ in summary]


# --------------------------------------------------------- decade pages

def generate_decade_pages(lineage, colors, belt_games, decades_dir):
    """decades/<1990s>.html for every decade of belt history, plus an
    index. Returns the list of decade slugs written."""
    reigns = lineage["reigns"]
    today = date.today()
    first_year = int(reigns[0]["start_date"][:4])
    decades = list(range(first_year - first_year % 10, today.year + 1, 10))
    os.makedirs(decades_dir, exist_ok=True)
    summary = []
    for d0 in decades:
        d_start, d_end = date(d0, 1, 1), date(d0 + 10, 1, 1)
        games = [g for g in belt_games if d0 <= int(g["date"][:4]) < d0 + 10]
        started = [(i, r) for i, r in enumerate(reigns) if d0 <= int(r["start_date"][:4]) < d0 + 10]
        changes = sum(1 for g in games if g["outcome"] == "changed")
        # days held within the decade, clipped
        held = {}
        for i, r in enumerate(reigns):
            s, e = reign_dates(r, today)
            cs, ce = max(s, d_start), min(e, d_end)
            if ce > cs:
                held[r["team"]] = held.get(r["team"], 0) + (ce - cs).days
        if not games and not held:
            continue
        slug = f"{d0}s"
        top = sorted(held.items(), key=lambda kv: -kv[1])[:8]
        longest = max(started, key=lambda ir: reign_duration_days(ir[1], today)) if started else None
        top_rows = "".join(
            f'<a class="miniRow" href="../teams/{team_slug(t)}.html"><span>{team_dot(colors, t, 22, "tlDot miniDot")} <strong>{esc(t)}</strong></span>'
            f'<span class="miniTag">{d:,} days</span></a>' for t, d in top)
        reign_rows = "".join(
            f'<a class="miniRow" href="../reigns/{i + 1}.html"><span><strong>{esc(r["team"])}</strong> &middot; {fmt_date(r["start_date"])}</span>'
            f'<span class="miniTag">{fmt_duration(*reign_dates(r, today))} &middot; {r.get("defenses", 0)} def.</span></a>'
            for i, r in started)
        label = f"{d0}s"
        summary.append((slug, label, len(games), changes, len(held), top[0][0] if top else ""))
        longest_txt = (f'The longest reign to begin in the decade was <a href="../reigns/{longest[0] + 1}.html">{esc(longest[1]["team"])}&rsquo;s, '
                       f'{fmt_duration(*reign_dates(longest[1], today))}</a>.' if longest else "")
        html_out = f'''{page_head(f"The College Football Belt in the {label}",
                              f"The College Football Belt in the {label}: {len(games)} belt games, {changes} title changes, and the {len(held)} programs that held the lineal title during the decade.", "../")}

{site_header('../', 'decades', crumb=f'<a href="../index.html">Belt</a> <span class="sep">/</span> <a href="index.html">Decades</a> <span class="sep">/</span> The {label}')}

<main class="wrap">
  {page_intro("Decade by decade", f"The belt in the {label}",
              f"{len(games)} belt game{'s' if len(games) != 1 else ''}, {changes} title change{'s' if changes != 1 else ''}, {len(held)} program{'s' if len(held) != 1 else ''} with the belt in hand at some point. {longest_txt}")}
  <div class="twoUp">
    <section>
      <div class="sectionHead"><span class="tag">Most days with the belt</span><h2>Who owned the {label}</h2></div>
      <div class="miniList">{top_rows}</div>
    </section>
    <section>
      <div class="sectionHead"><span class="tag">Reigns that began this decade</span><h2>{len(started)} reign{'s' if len(started) != 1 else ''}</h2></div>
      <div class="miniList">{reign_rows or '<p class="emptyNote">No new reign began in this decade &mdash; one holder carried it straight through.</p>'}</div>
    </section>
  </div>
  <p class="moreLink"><a href="../seasons.html">Season by season &rarr;</a></p>
</main>

{site_footer('../', 'Every belt game sourced from the College Football Data API.')}
'''
        with open(os.path.join(decades_dir, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(html_out)
    rows = "".join(
        f'<tr><td class="teamCell"><a href="{s}.html">The {l}</a></td><td class="tabular">{g}</td><td class="tabular">{c}</td><td class="tabular">{p}</td><td class="dates">{esc(t)}</td></tr>'
        for s, l, g, c, p, t in summary)
    index_html = f'''{page_head("The Belt Decade by Decade — 1860s to Today",
                              "The College Football Belt one decade at a time: belt games, title changes and the programs that held the lineal title in each decade since 1869.", "../")}

{site_header('../', 'decades')}

<main class="wrap">
  {page_intro("Decade by decade", "The belt, ten years at a time",
              "Every decade since the first game, with the program that spent the most of it holding the belt.")}
  <div class="tableScroll">
    <table class="reignsTable">
      <thead><tr><th>Decade</th><th style="text-align:right">Belt games</th><th style="text-align:right">Title changes</th><th style="text-align:right">Programs</th><th>Most days held</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</main>

{site_footer('../', 'Every belt game sourced from the College Football Data API.')}
'''
    with open(os.path.join(decades_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    return [s for s, *_ in summary]


# ------------------------------------------------------------- more stories
#
# Six more data-driven longreads (2026-09-16, Bob: "add many more stories
# to the stories page, with more detail and more thoughtfully written").
# Same contract as the first two: the prose is authored, every name, date,
# score and count in it is computed from the lineage at build time, so the
# stories stay true as new reigns happen.

def _story_page(title, description, kicker, h1, lede, chapters_html, stat_html=""):
    header, footer = _story_nav_footer()
    return f'''{page_head(title, description)}

{header}

<main class="wrap storyArticle">
  <p class="storyKicker">{kicker}</p>
  <h1 class="pageTitle">{h1}</h1>
  <p class="lede">{lede}</p>
  {stat_html}
  {chapters_html}
  <p class="storyBackLink"><a href="stories.html">&larr; Back to Stories</a></p>
</main>

{footer}
'''


def _chapter(title, paragraphs, rank=None):
    rank_html = f'<span class="storyRank">{rank}</span> ' if rank else ""
    body = "".join(f"<p>{p}</p>" for p in paragraphs if p)
    return f'\n  <article class="storyChapter">\n    <h2>{rank_html}{title}</h2>\n    {body}\n  </article>'


def _stat(n, label):
    return f'<div class="storyStat"><span class="storyStatN">{n}</span>{label}</div>'


def _plural(n, one, many=None):
    return one if n == 1 else (many or one + "s")


def _years_words(days):
    y = days // 365
    if y >= 2:
        return f"{y} years"
    if y == 1:
        return "a year"
    return f"{days} days"


def _game_link(g, text):
    return f'<a href="games/{g["game_id"]}.html">{text}</a>'


def _reign_link(i, text):
    return f'<a href="reigns/{i + 1}.html">{text}</a>'


# ---- 1. One-week wonders -------------------------------------------------

def generate_story_one_week_wonders(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    by_reign = reign_games(belt_games)
    total = len(reigns)
    closed = [(i, r) for i, r in enumerate(reigns) if r.get("end_date")]
    zero = [(i, r) for i, r in closed if r.get("defenses", 0) == 0]
    shortest = sorted(closed, key=lambda ir: (reign_duration_days(ir[1], today), ir[1]["start_date"]))[:8]
    per_team = {}
    for i, r in zero:
        per_team[r["team"]] = per_team.get(r["team"], 0) + 1
    most_zero = sorted(per_team.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    only_wonders = sorted(t for t in per_team if all(rr.get("defenses", 0) == 0 and rr.get("end_date") for rr in reigns if rr["team"] == t))
    share = zero and round(100 * len(zero) / len(closed))
    median_days = sorted(reign_duration_days(r, today) for _, r in closed)[len(closed) // 2]

    chapters = []
    for k, (i, r) in enumerate(shortest, 1):
        s, e = reign_dates(r, today)
        days = (e - s).days
        games = by_reign.get(i + 1, [])
        win = games[0] if games else None
        end_game = (by_reign.get(i + 2) or [None])[0]
        paras = []
        if win and win["outcome"] == "changed":
            w, l, wp, lp = game_score_winner_first(win)
            paras.append(f'{_reign_link(i, esc(r["team"]))} took the belt from {esc(win["holder"])} '
                         f'{_game_link(win, f"{wp}–{lp}")} on {fmt_date(win["date"])}'
                         + (" at a neutral site" if win["neutral"] else (" at home" if win["home"] == r["team"] else " on the road")) + ".")
        else:
            paras.append(f'{_reign_link(i, esc(r["team"]))} came into the belt on {fmt_date(r["start_date"])}.')
        if win and win["outcome"] == "changed":
            prev = reigns[i - 1] if i > 0 else None
            if prev:
                ps, pe = reign_dates(prev, today)
                pd = (pe - ps).days
                if pd >= 300 or prev.get("defenses", 0) >= 5:
                    paras[-1] += (f' That ended a {esc(prev["team"])} reign of {fmt_duration(ps, pe)} and {prev.get("defenses", 0)} '
                                  f'{_plural(prev.get("defenses", 0), "defense")}, which makes the week that followed stranger still.')
        if end_game:
            w, l, wp, lp = game_score_winner_first(end_game)
            nxt = reigns[i + 1]
            nxt_days = reign_duration_days(nxt, today)
            if nxt.get("defenses", 0) == 0 and nxt.get("end_date"):
                after = f' {esc(w)} would not do much better with it.'
            elif nxt.get("end_date"):
                after = f' {esc(w)} made more of it: {fmt_duration(*reign_dates(nxt, today))} and {nxt.get("defenses", 0)} {_plural(nxt.get("defenses", 0), "defense")}.'
            else:
                after = f' {esc(w)} still has it.'
            paras.append(f'{days} {_plural(days, "day")} later it was gone: {esc(w)} won {_game_link(end_game, f"{wp}–{lp}")} on '
                         f'{fmt_date(end_game["date"])}, and the belt moved on without a single defense.' + after)
        own = [(j, rr) for j, rr in enumerate(reigns) if rr["team"] == r["team"]]
        nth = next(n for n, (j, _) in enumerate(own, 1) if j == i)
        best = max(own, key=lambda jr: reign_duration_days(jr[1], today))
        if len(own) == 1:
            paras.append(f'It remains {possessive(r["team"])} only reign.')
        elif best[0] != i and reign_duration_days(best[1], today) > 30:
            bs_, be_ = reign_dates(best[1], today)
            paras.append(f'It was the {ordinal(nth)} of {possessive(r["team"])} {len(own)} reigns; the longest of them, '
                         f'{_reign_link(best[0], fmt_duration(bs_, be_))} starting in {best[1]["start_date"][:4]}, shows it can be done.')
        chapters.append(_chapter(f'{esc(r["team"])} &mdash; {days} {_plural(days, "day")}, {r["start_date"][:4]}', paras, f"No. {k}"))

    team_paras = []
    if most_zero:
        t, n = most_zero[0]
        team_paras.append(f'Nobody has done it more often than {team_link(t)}: {n} of its reigns ended at the very next game. '
                          + (f'Close behind: ' + ", ".join(f'{team_link(tt)} ({nn})' for tt, nn in most_zero[1:4]) + '.' if len(most_zero) > 1 else ""))
    if only_wonders:
        shown = only_wonders[:12]
        team_paras.append(f'And {len(only_wonders)} {_plural(len(only_wonders), "program")} have never held the belt any other way &mdash; every reign they have ever had ended the following week: '
                          + ", ".join(team_link(t) for t in shown) + ("." if len(only_wonders) <= 12 else f", and {len(only_wonders) - 12} more."))
    chapters.append(_chapter("The programs that specialize in it", team_paras))

    closing = (f'There is a lesson in all this, and it is not really about the teams. The belt is easier to take than to keep, because taking it '
               f'asks for one good Saturday and keeping it asks for every Saturday after. The typical reign lasts {median_days} days &mdash; '
               f'half of them are shorter than that &mdash; and the ones on this page are the extreme of a rule that never gives anyone a week off.')
    chapters.append(_chapter("What it says about the belt", [closing]))

    lede = (f'Of the {len(closed)} completed reigns in belt history, {len(zero)} ended at the very next game &mdash; {share}% of them. '
            f'The belt was won on a Saturday and gone the Saturday after. These are the shortest of those, and the programs that keep doing it.')
    stat = _stat(f"{len(zero)}", f"of {len(closed)} completed reigns never managed a single defense")
    return _story_page("One-Week Wonders: The Shortest Reigns in Belt History",
                       f"The shortest reigns in College Football Belt history: {len(zero)} of {len(closed)} completed reigns ended without a defense. The teams that lost it a week later, ranked.",
                       "Stories", "One-week wonders", lede, "".join(chapters), stat)


# ---- 2. Coast to coast ---------------------------------------------------

REGIONS = {
    "Northeast": {"NJ", "NY", "PA", "MA", "CT", "RI", "NH", "VT", "ME", "DE", "MD", "DC"},
    "South": {"VA", "WV", "NC", "SC", "GA", "FL", "AL", "MS", "TN", "KY", "LA", "AR"},
    "Midwest": {"OH", "IN", "IL", "MI", "WI", "MN", "IA", "MO", "KS", "NE", "ND", "SD"},
    "Southwest": {"TX", "OK", "NM", "AZ"},
    "West": {"CA", "OR", "WA", "CO", "UT", "NV", "ID", "MT", "WY", "HI", "AK"},
}


def generate_story_coast_to_coast(lineage, colors, belt_games):
    reigns = lineage["reigns"]
    today = date.today()

    def state_of(team):
        return (colors.get(team) or {}).get("state")

    def region_of(st):
        for name, codes in REGIONS.items():
            if st in codes:
                return name
        return None

    first_state = {}
    first_region = {}
    for i, r in enumerate(reigns):
        st = state_of(r["team"])
        if not st:
            continue
        if st not in first_state:
            first_state[st] = (i, r)
        reg = region_of(st)
        if reg and reg not in first_region:
            first_region[reg] = (i, r)
    days_by_state = {}
    for r in reigns:
        st = state_of(r["team"])
        if st:
            days_by_state[st] = days_by_state.get(st, 0) + reign_duration_days(r, today)
    # longest unbroken stay in one state (consecutive reigns)
    best_run = None
    run_start = 0
    for i in range(1, len(reigns) + 1):
        if i == len(reigns) or state_of(reigns[i]["team"]) != state_of(reigns[run_start]["team"]) or not state_of(reigns[i]["team"]):
            st = state_of(reigns[run_start]["team"])
            if st:
                s = date.fromisoformat(reigns[run_start]["start_date"])
                e = reign_dates(reigns[i - 1], today)[1]
                if best_run is None or (e - s).days > best_run[0]:
                    best_run = ((e - s).days, st, run_start, i - 1)
            run_start = i
    first_i, first_r = 0, reigns[0]
    ne_left = None
    for i, r in enumerate(reigns):
        st = state_of(r["team"])
        if st and region_of(st) != "Northeast":
            ne_left = (i, r)
            break
    chapters = []
    if ne_left:
        i, r = ne_left
        yrs = int(r["start_date"][:4]) - int(first_r["start_date"][:4])
        chapters.append(_chapter("The first years never left the Northeast", [
            f'The belt began in New Jersey, with {team_link(first_r["team"])} beating Princeton in the first game anyone ever played, and for '
            f'{yrs} years it did not leave the Northeast at all. Reigns passed between the same handful of programs &mdash; the Ivies, the small '
            f'Pennsylvania colleges, a service academy &mdash; because those were the teams that played each other, and the belt can only travel '
            f'along the games that actually happen.',
            f'The first time it crossed out was {fmt_date(r["start_date"])}, when {_reign_link(i, esc(r["team"]))} carried it to '
            f'{STATE_NAMES.get(state_of(r["team"]), state_of(r["team"]))}. From there the map starts to fill in.']))
    for reg in sorted((reg for reg in ("Midwest", "South", "Southwest", "West") if reg in first_region),
                      key=lambda reg: first_region[reg][1]["start_date"]):
        if True:
            i, r = first_region[reg]
            st = state_of(r["team"])
            same = [(ii, rr) for ii, rr in enumerate(reigns) if state_of(rr["team"]) and region_of(state_of(rr["team"])) == reg]
            days = sum(reign_duration_days(rr, today) for _, rr in same)
            teams = {rr["team"] for _, rr in same}
            chapters.append(_chapter(f'First into the {reg}: {esc(r["team"])}, {r["start_date"][:4]}', [
                f'{_reign_link(i, esc(r["team"]))} brought the belt to {STATE_NAMES.get(st, st)} on {fmt_date(r["start_date"])} &mdash; the first {reg} program to hold it. '
                f'The region has had it {len(same)} {_plural(len(same), "time")} since, across {len(teams)} {_plural(len(teams), "program")}, for {days:,} days in all.']))
    top_states = sorted(days_by_state.items(), key=lambda kv: -kv[1])[:5]
    single = sorted(st for st, d in days_by_state.items() if sum(1 for r in reigns if state_of(r["team"]) == st) == 1)
    paras = [
        f'{len(days_by_state)} states have been home to the belt. ' +
        ", ".join(f'{STATE_NAMES.get(st, st)} ({d:,} days)' for st, d in top_states) + ' have held it longest.'
    ]
    if best_run:
        d, st, a, b = best_run
        paras.append(f'The longest it has ever stayed in one state without leaving is {_years_words(d)} in {STATE_NAMES.get(st, st)}: '
                     f'{b - a + 1} consecutive {_plural(b - a + 1, "reign")}, from {_reign_link(a, esc(reigns[a]["team"]))} in {reigns[a]["start_date"][:4]} '
                     f'to {_reign_link(b, esc(reigns[b]["team"]))}' + (", still holding it" if b == len(reigns) - 1 else f' in {reigns[b]["end_date"][:4]}') + '.')
    if single:
        paras.append(f'{len(single)} {_plural(len(single), "state has", "states have")} had the belt exactly once: '
                     + ", ".join(f'<a href="states/{st.lower()}.html">{STATE_NAMES.get(st, st)}</a>' for st in single) + '.')
    chapters.append(_chapter("The map today", paras))
    missing = sorted(set(STATE_NAMES) - set(days_by_state) - {"DC"})
    chapters.append(_chapter("Where it has never been", [
        f'{len(missing)} states are still waiting for their first reign' + (": " + ", ".join(STATE_NAMES[s] for s in missing[:14]) + ("." if len(missing) <= 14 else f", and {len(missing) - 14} more.") if missing else "."),
        'That is the geography of the rule, not of talent: a state gets the belt when one of its programs happens to be scheduled against the holder '
        'on the right Saturday, and nothing else. The <a href="my-team.html">My Team</a> page shows how far away each program is right now.']))
    lede = (f'The belt can only move along games that were actually played, so its map is a history of who played whom. '
            f'{len(days_by_state)} states in, this is the route it took.')
    stat = _stat(f"{len(days_by_state)}", "states have been home to the belt")
    return _story_page("Coast to Coast: How the Belt Spread Across the Map",
                       f"The geography of the College Football Belt: how the lineal title left the Northeast, when it reached each region, and the {len(days_by_state)} states that have held it.",
                       "Stories", "Coast to coast", lede, "".join(chapters), stat)


# ---- 3. Giant killers ----------------------------------------------------

def generate_story_giant_killers(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    by_reign = reign_games(belt_games)
    closed = [(i, r) for i, r in enumerate(reigns) if r.get("end_date")]
    longest = sorted(closed, key=lambda ir: -reign_duration_days(ir[1], today))[:10]
    ended_days = {}
    ended_count = {}
    for i, r in closed:
        killer = r.get("lost_to")
        if killer:
            ended_days[killer] = ended_days.get(killer, 0) + reign_duration_days(r, today)
            ended_count[killer] = ended_count.get(killer, 0) + 1
    top_killers = sorted(ended_days.items(), key=lambda kv: -kv[1])[:8]
    chapters = []
    for k, (i, r) in enumerate(longest, 1):
        end_game = (by_reign.get(i + 2) or [None])[0]
        s, e = reign_dates(r, today)
        paras = []
        if end_game:
            w, l, wp, lp = game_score_winner_first(end_game)
            nxt = reigns[i + 1]
            kept = reign_duration_days(nxt, today)
            where = "at a neutral site" if end_game["neutral"] else ("at home" if end_game["home"] == w else "on the road")
            paras.append(f'{_reign_link(i, esc(r["team"]))} had held the belt for {fmt_duration(s, e)} and defended it {r.get("defenses", 0)} '
                         f'{_plural(r.get("defenses", 0), "time")} when {team_link(w)} ended it {_game_link(end_game, f"{wp}–{lp}")} {where} on {fmt_date(end_game["date"])}.')
            if nxt.get("end_date"):
                paras.append(f'{esc(w)} kept the prize for {fmt_duration(*reign_dates(nxt, today))}' +
                             (f' before losing it to {esc(nxt["lost_to"])}.' if nxt.get("lost_to") else '.') +
                             (' A one-week wonder that toppled a giant.' if nxt.get("defenses", 0) == 0 else ''))
            else:
                paras.append(f'{esc(w)} still holds it today, {kept:,} days on.')
        chapters.append(_chapter(f'{esc(r.get("lost_to") or "")} ends {possessive(r["team"])} {fmt_duration(s, e)}', paras, f"No. {k}"))
    paras = [f'Add up the length of every reign a program has ended and you get a different kind of leaderboard &mdash; not who held the belt longest, but who took the most away.']
    paras.append(" ".join(f'{team_link(t)} has ended {ended_count[t]} {_plural(ended_count[t], "reign")} worth {d:,} days.' for t, d in top_killers[:5]))
    chapters.append(_chapter("The career giant killers", paras))
    lede = (f'Every long reign ends the same way: someone wins on a Saturday nobody expected. These are the ten longest reigns ever ended, and the programs that ended them.')
    top_i, top_r = longest[0]
    stat = _stat(f"{reign_duration_days(top_r, today):,}", f"days &mdash; the longest reign ever ended, {esc(top_r['team'])}&rsquo;s, by {esc(top_r.get('lost_to') or '')}")
    return _story_page("Giant Killers: Who Ended the Longest Belt Reigns",
                       "The programs that ended the longest reigns in College Football Belt history, the games that did it, and a leaderboard of who has taken the most belt-days away.",
                       "Stories", "Giant killers", lede, "".join(chapters), stat)


# ---- 4. The long way back ------------------------------------------------

def generate_story_long_way_back(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    by_team = {}
    for i, r in enumerate(reigns):
        by_team.setdefault(r["team"], []).append((i, r))
    gaps = []
    for t, items in by_team.items():
        for (i1, r1), (i2, r2) in zip(items, items[1:]):
            gap = (date.fromisoformat(r2["start_date"]) - date.fromisoformat(r1["end_date"])).days
            gaps.append((gap, t, i1, r1, i2, r2))
    longest = sorted(gaps, key=lambda x: -x[0])[:8]
    quickest = sorted(gaps, key=lambda x: x[0])[:5]
    chapters = []
    for k, (gap, t, i1, r1, i2, r2) in enumerate(longest, 1):
        paras = [
            f'{_reign_link(i1, esc(t))} lost the belt on {fmt_date(r1["end_date"])}' + (f' to {esc(r1["lost_to"])}' if r1.get("lost_to") else "") +
            f' and did not touch it again for {_years_words(gap)}. When it finally came back, on {fmt_date(r2["start_date"])}, it came from '
            f'{esc(r2.get("won_from") or "")}' + (f' &mdash; and the {_reign_link(i2, "reign that followed")} lasted {fmt_duration(*reign_dates(r2, today))}.' if r2.get("end_date") else f' &mdash; and {esc(t)} still has it.')
        ]
        chapters.append(_chapter(f'{esc(t)}: {_years_words(gap)} between reigns', paras, f"No. {k}"))
    q_paras = ['At the other end of the scale, some programs barely let go.']
    for gap, t, i1, r1, i2, r2 in quickest:
        q_paras.append(f'{team_link(t)} lost it to {esc(r1.get("lost_to") or "")} and won it back from {esc(r2.get("won_from") or "")} '
                       f'{gap} {_plural(gap, "day")} later, in {r2["start_date"][:4]}.')
    chapters.append(_chapter("The quickest returns", q_paras))
    waiting = sorted(((today - date.fromisoformat(items[-1][1]["end_date"])).days, t) for t, items in by_team.items() if items[-1][1].get("end_date"))
    waiting.sort(reverse=True)
    w_paras = [f'{len(waiting)} programs that have held the belt are waiting to hold it again. The longest active waits belong to '
               + ", ".join(f'{team_link(t)} ({_years_words(d)})' for d, t in waiting[:5]) + '. The <a href="records.html">records page</a> keeps the full drought table.']
    chapters.append(_chapter("Still waiting", w_paras))
    lede = (f'{len(by_team)} programs have held the belt; {sum(1 for items in by_team.values() if len(items) > 1)} of them have held it more than once. '
            f'Between one reign and the next, the wait has been as short as a week and as long as most of a century.')
    top = longest[0]
    stat = _stat(_years_words(top[0]), f"&mdash; the longest gap between two reigns, {esc(top[1])}&rsquo;s")
    return _story_page("The Long Way Back: The Longest Waits Between Belt Reigns",
                       "The longest gaps between one College Football Belt reign and a program's next, the quickest returns, and the programs still waiting to hold it again.",
                       "Stories", "The long way back", lede, "".join(chapters), stat)


# ---- 5. Where the belt changes hands ------------------------------------

def generate_story_changing_hands(lineage, belt_games):
    changes = [g for g in belt_games if g["outcome"] == "changed"]
    ties = [g for g in belt_games if g["outcome"] == "retained (tie)"]
    n = len(changes)
    home = sum(1 for g in changes if not g["neutral"] and g["home"] == g["new_holder"])
    road = sum(1 for g in changes if not g["neutral"] and g["away"] == g["new_holder"])
    neutral = n - home - road
    post = [g for g in changes if g["season_type"] == "postseason"]
    margins = []
    for g in changes:
        w, l, wp, lp = game_score_winner_first(g)
        margins.append((wp - lp, g, w, l, wp, lp))
    one_point = [m for m in margins if m[0] == 1]
    one_score = sum(1 for m in margins if m[0] <= 8)
    blowouts = sorted(margins, key=lambda m: -m[0])[:5]
    by_month = {}
    for g in changes:
        m = int(g["date"][5:7])
        by_month[m] = by_month.get(m, 0) + 1
    top_month = max(by_month.items(), key=lambda kv: kv[1]) if by_month else None
    if ties:
        tg = ties[-1]
        tie_txt = (f'{len(ties)} {_plural(len(ties), "time")} the holder was held to a tie and kept the belt under the holder-retains rule, most recently '
                   f'{_game_link(tg, esc(tg["home"]) + " and " + esc(tg["away"]) + ", " + tg["score"].replace("-", "–"))} in {tg["date"][:4]}. '
                   f'Overtime ended ties in 1996, so that number is final.')
    else:
        tie_txt = 'No reign has ever been saved by a tie.'
    chapters = [
        _chapter("Mostly on the road", [
            f'Of the {n} title changes in belt history, {road} happened with the challenger on the road, {home} at home and {neutral} on a neutral field. '
            f'That is {round(100 * road / n)}% road takeovers &mdash; the belt is usually won in someone else&rsquo;s stadium, which makes sense: the holder '
            f'is more often the better team, and the better team is more often at home.',
        ]),
        _chapter("One score, and sometimes one point", [
            f'{one_score} of the {n} changes ({round(100 * one_score / n)}%) came in one-score games. {len(one_point)} of them were decided by a single point'
            + (': ' + "; ".join(f'{team_link(w)} over {esc(l)} {_game_link(g, f"{wp}–{lp}")} in {g["date"][:4]}' for _, g, w, l, wp, lp in sorted(one_point, key=lambda m: m[1]["date"], reverse=True)[:6]) + '.' if one_point else '.'),
        ]),
        _chapter("And sometimes by a mile", [
            'The biggest margins a belt ever changed hands by: ' + "; ".join(f'{team_link(w)} {_game_link(g, f"{wp}–{lp}")} over {esc(l)}, {g["date"][:4]}' for _, g, w, l, wp, lp in blowouts) + '.',
        ]),
        _chapter("Ties that saved a reign", [tie_txt]),
        _chapter("When it changes hands", [
            (f'{len(post)} {_plural(len(post), "change")} came in the postseason &mdash; bowls and playoff games &mdash; the rest in the regular season. '
             + (f'By month, {["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][top_month[0]]} has seen the most, {top_month[1]}, ' if top_month else '')
             + 'which is mostly a story about how many games get played then.'),
        ]),
    ]
    lede = f'{n} times the belt has changed hands. Where, by how much, and how often a tie got in the way.'
    stat = _stat(f"{round(100 * road / n)}%", "of all title changes were won on the road")
    return _story_page("Where the Belt Changes Hands: Home, Road, One Point or a Mile",
                       f"How the College Football Belt changes hands: {n} title changes by home and road, margin of victory, postseason games and the ties that kept a holder on top.",
                       "Stories", "Where the belt changes hands", lede, "".join(chapters), stat)


# ---- 6. New Year's holders ----------------------------------------------

def generate_story_new_years(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    first_year = int(reigns[0]["start_date"][:4])
    holder_on = {}
    for y in range(first_year + 1, today.year + 1):
        d = date(y, 1, 1)
        for i, r in enumerate(reigns):
            s, e = reign_dates(r, today)
            if s <= d < e or (i == len(reigns) - 1 and s <= d):
                holder_on[y] = (i, r["team"])
                break
    counts = {}
    for y, (i, t) in holder_on.items():
        counts[t] = counts.get(t, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    # longest run of consecutive New Year's Days
    best = None
    years = sorted(holder_on)
    k = 0
    while k < len(years):
        j = k
        while j + 1 < len(years) and years[j + 1] == years[j] + 1 and holder_on[years[j + 1]][1] == holder_on[years[k]][1]:
            j += 1
        if best is None or j - k + 1 > best[0]:
            best = (j - k + 1, holder_on[years[k]][1], years[k], years[j])
        k = j + 1
    same_next = sum(1 for y in years if y + 1 in holder_on and holder_on[y][1] == holder_on[y + 1][1])
    pairs = sum(1 for y in years if y + 1 in holder_on)
    chapters = [
        _chapter("Who rings in the most new years", [
            'The programs that have held the belt on the most January firsts: ' + "; ".join(f'{team_link(t)} ({c})' for t, c in top) + '.',
            'It rewards a particular kind of program &mdash; not the one that grabs the belt in September, but the one that is still holding it when the calendar turns, after the rivalry weeks and the bowl.',
        ]),
    ]
    if best:
        n_run, t, y0, y1 = best
        chapters.append(_chapter(f'{possessive(t)} {n_run} straight', [
            f'The longest run of consecutive New Year&rsquo;s Days with the same holder belongs to {team_link(t)}: every January 1 from {y0} through {y1}, {n_run} in a row.'
        ]))
    # the most recent back-to-back, and the programs that rang it in exactly once
    last_repeat = max((y for y in years if y + 1 in holder_on and holder_on[y][1] == holder_on[y + 1][1]), default=None)
    once = sorted(t for t, c in counts.items() if c == 1)
    chapters.append(_chapter("How sticky is it?", [
        f'A New Year&rsquo;s holder was still the holder the following New Year&rsquo;s Day {same_next} {_plural(same_next, "time")} out of {pairs} &mdash; '
        f'{round(100 * same_next / pairs) if pairs else 0}%. Most years, the belt that starts the year is not the belt that ends it.'
        + (f' The last program to ring in two in a row was {team_link(holder_on[last_repeat][1])}, on January 1 of {last_repeat} and {last_repeat + 1}.' if last_repeat else ''),
        f'This January 1, the holder was {team_link(holder_on[today.year][1])}.' if today.year in holder_on else '',
    ]))
    recent = [y for y in years if y > today.year - 25]
    if recent:
        # compress consecutive years with the same holder: "2019–2020 LSU"
        runs = []
        for y in recent:
            t = holder_on[y][1]
            if runs and runs[-1][2] == t and runs[-1][1] == y - 1:
                runs[-1][1] = y
            else:
                runs.append([y, y, t])
        run_txt = "; ".join((f"{y0}&ndash;{y1} " if y1 > y0 else f"{y0} ") + team_link(t) for y0, y1, t in runs)
        chapters.append(_chapter(f"The last {len(recent)} January firsts", [
            f'Year by year, who had the belt when the ball dropped: {run_txt}.',
            f'{len({holder_on[y][1] for y in recent})} different programs in {len(recent)} years &mdash; '
            + ('the belt has not rung in consecutive new years with the same holder in this stretch.' if not any(y1 > y0 for y0, y1, _ in runs)
               else 'only ' + _join_words(f"{team_link(t)} ({y0}&ndash;{y1})" for y0, y1, t in runs if y1 > y0) + ' managed back-to-back.'),
        ]))
    if once:
        chapters.append(_chapter("Rung in once", [
            f'{len(once)} {_plural(len(once), "program")} have held the belt on exactly one New Year&rsquo;s Day: '
            + _list_more((team_link(t) for t in once), 16) + '. For most of them it was the high-water mark of a reign that did not survive September.',
        ]))
    lede = (f'{len(holder_on)} January firsts since the belt began, and {len(counts)} different programs holding it when the ball dropped. '
            f'The New Year&rsquo;s holder is a decent proxy for &ldquo;who ended the season with it&rdquo; &mdash; here is who does that most.')
    stat = _stat(f"{top[0][1]}", f"New Year&rsquo;s Days with the belt for {esc(top[0][0])}, the most of anyone") if top else ""
    return _story_page("New Year's Holders: Who Has the Belt When the Calendar Turns",
                       "Which programs have held the College Football Belt on the most New Year's Days, the longest streak of them, and how often the holder makes it to the next one.",
                       "Stories", "New Year&rsquo;s holders", lede, "".join(chapters), stat)



# ---- shared bits for the season/era stories ------------------------------

def _season_games(belt_games):
    """{season: [belt games that season, in order]} (belt_games is already
    chronological)."""
    by_season = {}
    for g in belt_games:
        by_season.setdefault(g["season"], []).append(g)
    return by_season


def _holder_entering(by_season, season):
    """Who carried the belt INTO `season`: the new_holder of the last belt
    game of the most recent earlier season with any belt games (the belt
    waits through canceled years), or None for the very first season."""
    earlier = [s for s in by_season if s < season]
    if not earlier:
        return None
    return by_season[max(earlier)][-1]["new_holder"]


def _score_wf(g, tie_note=True):
    """'Yale 6–0 Princeton'-style winner-first score text for a belt game."""
    w, l, wp, lp = game_score_winner_first(g)
    if wp == lp and tie_note:
        return f"{esc(w)} {wp}&ndash;{lp} {esc(l)} (tie)"
    return f"{esc(w)} {wp}&ndash;{lp} {esc(l)}"


def _list_more(items, limit, noun="more"):
    """'a, b, c, and d' when the list fits, else 'a, b, c, and N more'."""
    items = list(items)
    if len(items) <= limit:
        return _join_words(items)
    return ", ".join(items[:limit]) + f", and {len(items) - limit} {noun}"


def _season_complete(by_season, season, today):
    """A season counts as finished once a later season has belt games or
    the calendar is past the end of its postseason."""
    return season < max(by_season) or today >= date(season + 1, 2, 1)


def _full_decades(seasons, today):
    """Decades that are entirely in the past (1870s onward)."""
    return [d for d in sorted({s // 10 * 10 for s in seasons}) if d >= 1870 and d + 10 <= today.year]


def _join_words(items, final="and"):
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {final} {items[1]}"
    return ", ".join(items[:-1]) + f", {final} " + items[-1]


def _venue_phrase(g, team):
    """'at home' / 'on the road' / 'on a neutral field' from `team`'s view."""
    if g.get("neutral"):
        return "on a neutral field"
    return "at home" if g["home"] == team else "on the road"


# ---- 7. The four ages of the belt ------------------------------------------

BELT_ERAS = [
    ("The founding age", 1869, 1899,
     "Football is barely a sport yet &mdash; the rules are still being argued over between games &mdash; and the belt "
     "lives inside a tiny circle of eastern colleges that play each other every November."),
    ("The wandering years", 1900, 1945,
     "The forward pass arrives, the game spreads west and south, and the belt starts turning up at small colleges and "
     "service academies whose names have since dropped out of the sport&rsquo;s memory."),
    ("The national belt", 1946, 1991,
     "Television, the wire-service polls and the bowl system make college football a national argument. The belt "
     "settles into the big conferences and, for the first time, the same few programs keep taking it back."),
    ("The modern era", 1992, None,
     "Overtime ends ties, conferences realign every few years, and a playoff replaces the polls. The belt keeps doing "
     "what it always did &mdash; moving one Saturday at a time &mdash; while everything around it changes."),
]


def generate_story_four_ages(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    holders_so_far = set()
    chapters = []
    era_summaries = []
    for name, y0, y1, framing in BELT_ERAS:
        y1_eff = y1 or today.year
        era_start = date(y0, 1, 1)
        era_end = date(y1_eff, 12, 31) if y1 else today
        games = [g for g in belt_games if y0 <= g["season"] <= y1_eff]
        changes = [g for g in games if g["outcome"] == "changed"]
        # days held per program inside the era (reigns clipped to the era)
        days = {}
        for r in reigns:
            s, e = reign_dates(r, today)
            s2, e2 = max(s, era_start), min(e, era_end)
            if e2 > s2:
                days[r["team"]] = days.get(r["team"], 0) + (e2 - s2).days
        era_days = max(1, (era_end - era_start).days)
        top = sorted(days.items(), key=lambda kv: -kv[1])
        started = [(i, r) for i, r in enumerate(reigns) if y0 <= int(r["start_date"][:4]) <= y1_eff]
        new_programs = []
        for i, r in started:
            if r["team"] not in holders_so_far:
                holders_so_far.add(r["team"])
                new_programs.append(r["team"])
        longest = max(started, key=lambda ir: reign_duration_days(ir[1], today)) if started else None
        closed = [r for _, r in started if r.get("end_date")]
        avg_len = (sum(reign_duration_days(r, today) for r in closed) / len(closed)) if closed else 0
        top3_share = sum(d for _, d in top[:3]) / era_days if top else 0
        ties = sum(1 for g in games if g["outcome"] == "retained (tie)")
        paras = [framing]
        if games:
            span_txt = f"{y0}&ndash;{y1}" if y1 else f"{y0} to today"
            paras.append(
                f"{span_txt}: {len(games):,} belt games, {len(changes)} title changes, {len(set(r['team'] for _, r in started))} different holders"
                + (f", {ties} ties" if ties else "")
                + f". {len(new_programs)} {_plural(len(new_programs), 'program')} held the belt for the first time in this era"
                + (f" &mdash; {_list_more((team_link(t) for t in new_programs), 6)}." if new_programs else "."))
        if top:
            t0, d0 = top[0]
            paras.append(
                f"The era belonged to {team_link(t0)}, which held the belt for {d0:,} of its {era_days:,} days ({d0 / era_days * 100:.0f}%)"
                + (f"; {_join_words(f'{team_link(t)} ({d:,} days)' for t, d in top[1:4])} were next" if len(top) > 1 else "")
                + f". The top three programs between them had it {top3_share * 100:.0f}% of the time.")
        if longest:
            li, lr = longest
            ls, le = reign_dates(lr, today)
            paras.append(
                f"The longest reign that began in the era was {_reign_link(li, possessive(lr['team']) + ' ' + fmt_duration(ls, le))}"
                f" ({lr.get('defenses', 0)} {_plural(lr.get('defenses', 0), 'defense')}), starting {fmt_date(lr['start_date'])}."
                + (f" The typical reign lasted {avg_len:.0f} days." if closed else ""))
        chapters.append(_chapter(f"{name}, {y0}&ndash;{y1 or 'today'}", paras))
        era_summaries.append((name, y0, y1, len(changes), len(games)))
    # closing comparison
    rates = [(name, (ch / gm) if gm else 0) for name, y0, y1, ch, gm in era_summaries]
    steadiest = min(rates, key=lambda x: x[1])
    wildest = max(rates, key=lambda x: x[1])
    chapters.append(_chapter("What changed, and what didn&rsquo;t", [
        f"Put the four ages side by side and the belt gets harder to hold as the sport grows up. In {steadiest[0].lower()} the belt "
        f"changed hands in {steadiest[1] * 100:.0f}% of the games it was on the line for; in {wildest[0].lower()} it was {wildest[1] * 100:.0f}%. "
        f"More teams, more parity, longer schedules and more games against real opponents all point the same way.",
        "What did not change is the rule. Every one of these eras was scored the same way: whoever beats the holder takes it. The Ivy "
        "monopolies, the Depression-era wanderings, the bowl-era dynasties and the playoff-era chaos are all just what that one sentence "
        "looks like when you apply it to whatever football happened to be that year.",
    ]))
    lede = (f"The belt has been on the line {len(belt_games):,} times across {today.year - 1869 + 1} seasons. Cut that history into four "
            f"ages and each one has its own character &mdash; who held it, how long they kept it, and how far it traveled.")
    stat = ""
    return _story_page("The Four Ages of the Belt — How Belt History Breaks Into Eras",
                       "College Football Belt history in four eras, from the founding age to the modern era: who held it in each one, how long reigns lasted and how often it moved.",
                       "Stories", "The four ages of the belt", lede, "".join(chapters), stat)


# ---- 8. Wire to wire ----------------------------------------------------------

WIRE_MIN_GAMES = 5


def generate_story_wire_to_wire(lineage, belt_games):
    today = date.today()
    by_season = _season_games(belt_games)
    seasons = sorted(by_season)
    wire = []      # (season, holder, games)
    for s in seasons:
        games = by_season[s]
        entering = _holder_entering(by_season, s)
        if entering is None:
            continue
        if all(g["outcome"] != "changed" for g in games) and _season_complete(by_season, s, today):
            wire.append((s, entering, games))
    ranked = sorted((w for w in wire if len(w[2]) >= WIRE_MIN_GAMES), key=lambda w: (-len(w[2]), w[0]))[:8]
    by_team = {}
    for s, t, games in wire:
        by_team.setdefault(t, []).append(s)
    most = sorted(by_team.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:6]
    latest = wire[-1] if wire else None
    chapters = []
    for k, (s, t, games) in enumerate(ranked, 1):
        ties = [g for g in games if g["outcome"] == "retained (tie)"]
        shut = sum(1 for g in games if min(int(x) for x in g["score"].split("-")) == 0)
        margins = []
        closest = None
        for g in games:
            h, a = (int(x) for x in g["score"].split("-"))
            m = abs(h - a)
            margins.append(m)
            if h != a and (closest is None or m < closest[0]):
                closest = (m, g)
        opp_txt = _list_more((_game_link(g, _score_wf(g)) for g in games), 6)
        # what happened next
        later = [ss for ss in seasons if ss > s]
        nxt = None
        if later:
            ng = by_season[later[0]]
            first = ng[0]
            if first["holder"] == t:
                nxt = (later[0], first)
        paras = [
            f"{team_link(t)} started {s} already holding the belt and finished it the same way, defending it {len(games)} times"
            + (f" &mdash; including {len(ties)} {_plural(len(ties), 'tie')} that kept it under the holder-retains rule" if ties else "")
            + f". Average margin {sum(margins) / len(margins):.1f} points"
            + (f", {shut} {_plural(shut, 'shutout')}" if shut else "") + ".",
            f"The games: {opp_txt}."
            + (f" The closest call was {_game_link(closest[1], _score_wf(closest[1]))}." if closest and closest[0] <= 7 else ""),
        ]
        if nxt:
            ns, ng0 = nxt
            if ng0["outcome"] == "changed":
                paras.append(f"It did not last: the first belt game of {ns} went to {team_link(ng0['new_holder'])}, "
                             f"{_game_link(ng0, _score_wf(ng0))}, and the run was over.")
            else:
                paras.append(f"The run carried into {ns}, too &mdash; the {ns} opener was {_game_link(ng0, _score_wf(ng0))}.")
        chapters.append(_chapter(f"{esc(t)}, {s} &mdash; {len(games)} defenses", paras, f"No. {k}"))
    if most:
        chapters.append(_chapter("The programs that do it most", [
            "Count every season the belt went wire to wire &mdash; carried in, never lost, carried out &mdash; and a short list does most of it: "
            + _join_words(f"{team_link(t)} ({len(ss)}: {', '.join(str(x) for x in ss[:5])}{'…' if len(ss) > 5 else ''})" for t, ss in most) + ".",
        ]))
    if latest:
        s, t, games = latest
        chapters.append(_chapter(f"The most recent: {esc(t)}, {s}", [
            f"The last time a season passed without the belt changing hands was {s}, when {team_link(t)} carried it through "
            f"{len(games)} {_plural(len(games), 'belt game')}."
            + " Every completed season since has seen at least one title change."
        ]))
    n_seasons = len([s for s in seasons if _holder_entering(by_season, s) is not None and _season_complete(by_season, s, today)])
    n_qualifying = len([w for w in wire if len(w[2]) >= WIRE_MIN_GAMES])
    chapters.append(_chapter("Why it is so rare", [
        f"Only {len(wire)} of {n_seasons} completed seasons ({len(wire) / max(1, n_seasons) * 100:.0f}%) have gone wire to wire, and {n_qualifying} of those "
        f"involved at least {WIRE_MIN_GAMES} belt games. Holding the belt for a season is a harder trick than going undefeated, because the holder "
        f"does not get to schedule its way around anyone: every game on the calendar is a belt game, including the bowl, and one loss anywhere ends it.",
        "It is also why the list skews old. Early schedules were short and the field was small; a modern holder has to survive twelve or thirteen "
        "games, a conference title game and the postseason with the belt on the line every single week.",
    ]))
    lede = (f"A wire-to-wire season: start the year holding the belt, end the year holding it, never lose in between. It has happened {len(wire)} "
            f"times in {n_seasons} completed seasons. These are the ones with the most defenses along the way.")
    stat = _stat(f"{len(ranked[0][2])}", f"defenses in a single wire-to-wire season, {possessive(ranked[0][1])} {ranked[0][0]}") if ranked else ""
    return _story_page("Wire to Wire — Seasons the Belt Never Changed Hands",
                       "The College Football Belt's wire-to-wire seasons: every year one program carried the lineal title in, defended it every week and carried it out, ranked.",
                       "Stories", "Wire to wire", lede, "".join(chapters), stat)


# ---- 9. The wildest seasons ----------------------------------------------------

def generate_story_wildest_seasons(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    by_season = _season_games(belt_games)
    seasons = sorted(by_season)
    rows = []
    for s in seasons:
        games = by_season[s]
        changes = [g for g in games if g["outcome"] == "changed"]
        if not changes:
            continue
        entering = _holder_entering(by_season, s) or games[0]["new_holder"]
        chain = [entering] + [g["new_holder"] for g in changes]
        rows.append((s, changes, chain, games))
    ranked = sorted(rows, key=lambda r: (-len(r[1]), -r[0]))[:8]
    total_changes = sum(len(r[1]) for r in rows)
    avg = total_changes / max(1, len(seasons))
    none = len(seasons) - len(rows)
    # shortest reign inside each ranked season, for color
    start_index = {r["start_date"]: (i, r) for i, r in enumerate(reigns)}
    chapters = []
    for k, (s, changes, chain, games) in enumerate(ranked, 1):
        distinct = len(set(chain))
        chain_txt = " &rarr; ".join(team_link(t) for t in chain)
        short = None
        for g in changes:
            ir = start_index.get(g["date"])
            if ir and ir[1].get("end_date"):
                d = reign_duration_days(ir[1], today)
                if short is None or d < short[0]:
                    short = (d, ir[0], ir[1])
        biggest = max(changes, key=lambda g: abs(int(g["score"].split("-")[0]) - int(g["score"].split("-")[1])))
        closest = min(changes, key=lambda g: abs(int(g["score"].split("-")[0]) - int(g["score"].split("-")[1])))
        returned = [t for t in set(chain) if chain.count(t) > 1]
        paras = [
            f"{len(changes)} title changes in {len(games)} belt games, {distinct} different holders. The belt went {chain_txt}.",
            (f"The shortest reign of the year was {_reign_link(short[1], possessive(short[2]['team']) + ' ' + fmt_duration(*reign_dates(short[2], today)))}. " if short else "")
            + f"The closest title change was {_game_link(closest, _score_wf(closest))}; the widest, {_game_link(biggest, _score_wf(biggest))}."
            + (f" {_join_words(team_link(t) for t in sorted(returned))} lost the belt and won it back within the same season." if returned else ""),
        ]
        chapters.append(_chapter(f"{s}: {len(changes)} title changes", paras, f"No. {k}"))
    # the calmest recent decades vs wildest
    by_decade = {d: 0 for d in _full_decades(seasons, today)}
    for s, changes, chain, games in rows:
        if s // 10 * 10 in by_decade:
            by_decade[s // 10 * 10] += len(changes)
    dec_sorted = sorted(by_decade.items(), key=lambda kv: (-kv[1], kv[0]))
    chapters.append(_chapter("The shape of a normal year", [
        f"Across {len(seasons)} seasons the belt has changed hands {total_changes} times &mdash; {avg:.1f} per season &mdash; and {none} "
        f"{_plural(none, 'season')} saw no change at all. The busiest decade was the {dec_sorted[0][0]}s with {dec_sorted[0][1]} changes; "
        f"the quietest full decade was the {dec_sorted[-1][0]}s with {dec_sorted[-1][1]}.",
        "A wild year usually needs two ingredients: a holder that is good enough to keep scheduling real opponents but not good enough to beat them "
        "all, and a run of takers who are each slightly worse than the last. The belt then falls down the standings one upset at a time until it "
        "lands with someone who can hold it through a bye week.",
    ]))
    lede = (f"Some years the belt barely moves. Other years it can&rsquo;t sit still &mdash; it changes hands in September, again in October, and "
            f"twice in November. These are the seasons with the most title changes, and the chains the belt followed.")
    stat = _stat(f"{len(ranked[0][1])}", f"title changes in {ranked[0][0]}, the most in any single season") if ranked else ""
    return _story_page("The Wildest Seasons — Most Belt Title Changes in a Year",
                       "The seasons with the most College Football Belt title changes: how often the belt moved, the chain of holders it followed and the shortest reigns along the way.",
                       "Stories", "The wildest seasons", lede, "".join(chapters), stat)


# ---- 10. When nobody won (the ties) -------------------------------------------

def generate_story_ties(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    holders = {r["team"] for r in reigns}
    ties = [g for g in belt_games if g["outcome"] == "retained (tie)"]
    by_reign = reign_games(belt_games)
    chapters = []
    if not ties:
        chapters.append(_chapter("No ties on record", ["The lineage has never recorded a tie with the belt on the line."]))
    else:
        last = ties[-1]
        first = ties[0]
        scoreless = [g for g in ties if g["score"] == "0-0"]
        by_decade = {}
        for g in ties:
            by_decade[g["season"] // 10 * 10] = by_decade.get(g["season"] // 10 * 10, 0) + 1
        top_dec = max(by_decade.items(), key=lambda kv: kv[1])
        held = {}
        for g in ties:
            held[g["holder"]] = held.get(g["holder"], 0) + 1
        challengers = {}
        for g in ties:
            challengers[g["opponent"]] = challengers.get(g["opponent"], 0) + 1
        never = sorted(((t, n) for t, n in challengers.items() if t not in holders), key=lambda kv: (-kv[1], kv[0]))
        reign_ties = []
        for rn, games in by_reign.items():
            n = sum(1 for g in games if g["outcome"] == "retained (tie)")
            if n:
                reign_ties.append((n, rn, games))
        reign_ties.sort(key=lambda x: (-x[0], x[1]))
        chapters.append(_chapter(f"The last one: {fmt_date(last['date'])}", [
            f"The final tie in belt history was {_game_link(last, _score_wf(last, tie_note=False))}, in {last['season']}. {team_link(last['holder'])} kept the belt because "
            f"the rule says the holder keeps it unless someone beats them, and nobody did.",
            "College football adopted overtime for every game in 1996, so that number will never grow. Every belt game since has had a winner &mdash; "
            "which also means every reign since has ended the honest way, with a loss.",
        ]))
        chapters.append(_chapter("The first one, and the busiest decade", [
            f"The first tie with the belt on the line was {_game_link(first, _score_wf(first, tie_note=False))}, in {first['season']}. The {top_dec[0]}s saw the most, "
            f"{top_dec[1]} of the {len(ties)}, back when a 6&ndash;6 finish was a normal afternoon rather than a novelty.",
            (f"{len(scoreless)} of the ties were scoreless: " + _list_more((_game_link(g, f"{esc(g['holder'])}&ndash;{esc(g['opponent'])} ({g['season']})") for g in scoreless), 8)
             + ". Sixty minutes, no points, belt stays put.") if scoreless else "",
        ]))
        if reign_ties:
            n, rn, games = reign_ties[0]
            r = reigns[rn - 1]
            s, e = reign_dates(r, today)
            tie_txt = _join_words(_game_link(g, f"{esc(g['opponent'])} ({g['score'].replace('-', '&ndash;')}, {g['season']})") for g in games if g["outcome"] == "retained (tie)")
            chapters.append(_chapter("The reign that leaned on ties", [
                f"No reign was saved by a tie more often than {_reign_link(rn - 1, possessive(r['team']) + ' ' + fmt_duration(s, e))}, which started "
                f"{fmt_date(r['start_date'])} and was tied {n} {_plural(n, 'time')}: {tie_txt}. Under a rule where a tie moved the belt, or where the "
                f"visitor won ties, that reign ends {n} {_plural(n, 'time')} over.",
                (f"Other reigns with more than one tie: " + _join_words(
                    _reign_link(rn2 - 1, f"{esc(reigns[rn2 - 1]['team'])} ({reigns[rn2 - 1]['start_date'][:4]}, {n2} ties)") for n2, rn2, _ in reign_ties[1:6] if n2 > 1) + ".")
                if any(n2 > 1 for n2, _, _ in reign_ties[1:6]) else "",
            ]))
        top_held = sorted(held.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        chapters.append(_chapter("Who kept it on a tie", [
            "The holders that got the most mileage out of the rule: " + _join_words(f"{team_link(t)} ({n})" for t, n in top_held) + ".",
            (f"And the challengers who tied the holder but never took the belt at all: " + _join_words(f"{esc(t)} ({n})" for t, n in never[:8]) +
             ". A tie was the closest any of them ever came.") if never else "",
        ]))
        chapters.append(_chapter("Why the holder keeps it", [
            "The belt borrows its tie rule from boxing: a draw does not move the title, because a challenger has to actually beat the champion. "
            "It is the only judgment call in the whole ruleset, and it was made once, at the beginning, and never revisited &mdash; which is the "
            "point. A lineal title is only worth anything if the rule never bends to a result anyone would have preferred.",
            f"The counterfactual is worth a sentence: {len(ties)} times the belt could have moved and did not. Trace a different rule through those "
            f"games and the entire lineage after each one changes. This is the one that happened.",
        ]))
    lede = (f"{len(ties)} times the holder was held to a tie with the belt on the line, and {len(ties)} times the belt stayed put. Ties have been "
            f"impossible since overtime arrived in 1996, so this is a closed book &mdash; here is what is in it.")
    stat = _stat(f"{len(ties)}", "ties with the belt on the line, every one of them a successful defense") if ties else ""
    return _story_page("When Nobody Won — Every Tie With the Belt on the Line",
                       "Every tie in College Football Belt history: the last one before overtime, the scoreless ones, the reigns a tie saved and why the holder keeps it on a draw.",
                       "Stories", "When nobody won", lede, "".join(chapters), stat)


# ---- 11. The opening-day curse -------------------------------------------------

def generate_story_opening_day(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    by_season = _season_games(belt_games)
    seasons = sorted(by_season)
    start_index = {(r["start_date"], r["team"]): (i, r) for i, r in enumerate(reigns)}
    carried = []   # (season, holder, first game)
    for s in seasons:
        entering = _holder_entering(by_season, s)
        if entering is None:
            continue
        carried.append((s, entering, by_season[s][0]))
    lost_opener = [(s, t, g) for s, t, g in carried if g["outcome"] == "changed"]
    # the reign that ended in that opener, for length
    def reign_for(g):
        # the reign that ENDED with game g: the reign whose end_date == g date and team == holder
        for i, r in enumerate(reigns):
            if r.get("end_date") == g["date"] and r["team"] == g["holder"]:
                return i, r
        return None, None
    painful = []
    for s, t, g in lost_opener:
        i, r = reign_for(g)
        if r:
            painful.append((reign_duration_days(r, today), s, t, g, i, r))
    painful.sort(key=lambda x: (-x[0], x[1]))
    week_changes = {}
    for g in belt_games:
        if g["outcome"] == "changed":
            wk = g.get("week") or 0
            week_changes[wk] = week_changes.get(wk, 0) + 1
    sept = sum(1 for g in belt_games if g["outcome"] == "changed" and g["date"][5:7] == "09")
    aug = sum(1 for g in belt_games if g["outcome"] == "changed" and g["date"][5:7] == "08")
    total_changes = sum(1 for g in belt_games if g["outcome"] == "changed")
    reg_changes = [g for g in belt_games if g["outcome"] == "changed" and g.get("season_type") == "regular" and int(g["date"][5:7]) >= 8]
    earliest = min(reg_changes, key=lambda g: (g["date"][5:], g["date"])) if reg_changes else None
    repeat = {}
    for s, t, g in lost_opener:
        repeat[t] = repeat.get(t, 0) + 1
    repeat_sorted = sorted(repeat.items(), key=lambda kv: (-kv[1], kv[0]))
    chapters = []
    for k, (days, s, t, g, i, r) in enumerate(painful[:8], 1):
        s0, e0 = reign_dates(r, today)
        paras = [
            f"{team_link(t)} carried a reign of {_reign_link(i, fmt_duration(s0, e0))} &mdash; {r.get('defenses', 0)} {_plural(r.get('defenses', 0), 'defense')}, "
            f"won from {esc(r.get('won_from') or 'the start of the lineage')} on {fmt_date(r['start_date'])} &mdash; through the whole {s - 1} offseason, "
            f"and lost it in the first belt game of {s}: {_game_link(g, _score_wf(g))}, {_venue_phrase(g, t)}, {fmt_date(g['date'])}.",
        ]
        # what the taker did with it
        ti, tr = start_index.get((g["date"], g["new_holder"]), (None, None))
        if tr:
            if tr.get("end_date"):
                paras.append(f"{team_link(g['new_holder'])} kept it {fmt_duration(*reign_dates(tr, today))} ({tr.get('defenses', 0)} {_plural(tr.get('defenses', 0), 'defense')}) "
                             f"before losing it to {esc(tr.get('lost_to') or '')}.")
            else:
                paras.append(f"{team_link(g['new_holder'])} still has it.")
        chapters.append(_chapter(f"{esc(t)}, {s} &mdash; {fmt_duration(s0, e0)} with the belt, gone in week one", paras, f"No. {k}"))
    rate = len(lost_opener) / max(1, len(carried))
    overall = total_changes / len(belt_games)
    if rate < overall * 0.8:
        verdict = (f"That is actually <em>lower</em> than the belt&rsquo;s overall change rate per game ({overall * 100:.0f}%), which is the honest "
                   f"finding: there is no curse. A program good enough to carry the belt through a bowl season is usually good enough to win its opener.")
    elif rate > overall * 1.25:
        verdict = (f"That is well above the belt&rsquo;s overall change rate per game ({overall * 100:.0f}%), so there may be something to it: "
                   f"a long layoff, a new roster, and an opener against someone who has been circling the date.")
    else:
        verdict = (f"That is about the same as the belt&rsquo;s overall change rate per game ({overall * 100:.0f}%), which is the honest finding: "
                   f"there is no curse, just a long layoff and a new roster.")
    chapters.append(_chapter("How often it happens", [
        f"The belt has been carried across an offseason {len(carried)} times, and {len(lost_opener)} of those holders ({rate * 100:.0f}%) lost it in "
        f"their very first belt game back. {verdict}",
        (f"It has happened to {_join_words(f'{team_link(t)} {n} times' for t, n in repeat_sorted[:4] if n > 1)}." if repeat_sorted and repeat_sorted[0][1] > 1 else ""),
        f"By the calendar, {sept + aug} of the {total_changes} title changes ({(sept + aug) / total_changes * 100:.0f}%) came in August or September."
        + (f" The earliest point in a regular season the belt has ever moved is {_game_link(earliest, fmt_date(earliest['date']))}, {esc(earliest['new_holder'])} over {esc(earliest['holder'])}." if earliest else ""),
    ]))
    chapters.append(_chapter("The long wait", [
        "An offseason is the strangest stretch of a reign. Nothing can happen to the belt for eight months, and yet the team that owns it turns "
        "over: seniors leave, coaches leave, a quarterback transfers. The program that lines up in September holding the belt is often not "
        "the one that won it. The belt does not know that, and it does not care &mdash; it was won by a name on a jersey, and it stays with the name.",
        "That is why the ones on this list sting. A season&rsquo;s worth of defenses, a bowl win, a winter of holding it, and then sixty minutes in "
        "week one and it is gone to a team that had nothing to do with any of that.",
    ]))
    lede = (f"Hold the belt through a whole offseason and you get to defend it on opening day. Of the {len(carried)} holders that have done that, "
            f"{len(lost_opener)} lost it right there. These are the longest reigns that ended in the first belt game of a new season.")
    stat = _stat(f"{rate * 100:.0f}%", "of holders who carried the belt through an offseason lost it in their first game back") if carried else ""
    return _story_page("The Opening-Day Curse — Belt Reigns Lost in Week One",
                       "The longest College Football Belt reigns that survived an offseason and ended in the first game of the next season, and how often opening day takes the belt.",
                       "Stories", "The opening-day curse", lede, "".join(chapters), stat)


# ---- 12. Bowl season ------------------------------------------------------------

def generate_story_bowl_season(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    post = [g for g in belt_games if g.get("season_type") == "postseason"]
    changes = [g for g in post if g["outcome"] == "changed"]
    start_index = {(r["start_date"], r["team"]): (i, r) for i, r in enumerate(reigns)}
    chapters = []
    if not post:
        chapters.append(_chapter("Not yet", ["The belt has never been on the line in a postseason game."]))
    else:
        first = post[0]
        last = post[-1]
        chapters.append(_chapter(f"The first one: {fmt_date(first['date'])}", [
            f"The belt&rsquo;s first trip to the postseason was {_game_link(first, _score_wf(first))}, with {team_link(first['holder'])} holding it. "
            + ("The holder kept it." if first["outcome"] != "changed" else f"{team_link(first['new_holder'])} took it home."),
            f"Since then it has been on the line in {len(post)} postseason games and changed hands in {len(changes)} of them "
            f"({len(changes) / len(post) * 100:.0f}%, against {sum(1 for g in belt_games if g['outcome'] == 'changed') / len(belt_games) * 100:.0f}% for belt games overall). "
            f"Bowls pair the holder with someone good, which is the whole point of a bowl and a bad day for a belt.",
        ]))
        items = []
        for g in changes:
            ti, tr = start_index.get((g["date"], g["new_holder"]), (None, None))
            keep = ""
            if tr:
                keep = f" &mdash; kept it {fmt_duration(*reign_dates(tr, today))}" if tr.get("end_date") else " &mdash; still holding it"
            items.append(f"<li>{_game_link(g, fmt_date(g['date']))}: {_score_wf(g)}{keep}</li>")
        chapters.append(_chapter("Every postseason title change", [
            f"All {len(changes)} of them, oldest first. A bowl takeover is a peculiar kind of reign: it starts in January and cannot be defended until "
            f"September, so every one of these holders spent its first eight months doing nothing at all.",
            f'<ul class="storyList">{"".join(items)}</ul>',
        ]))
        survived = [g for g in post if g["outcome"] != "changed"]
        by_holder = {}
        for g in survived:
            by_holder[g["holder"]] = by_holder.get(g["holder"], 0) + 1
        top_holders = sorted(by_holder.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        by_loser = {}
        for g in changes:
            by_loser[g["holder"]] = by_loser.get(g["holder"], 0) + 1
        top_losers = sorted(by_loser.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        chapters.append(_chapter("Holding it through January", [
            f"{len(survived)} times the holder walked out of a bowl still holding the belt. The programs that have done it most: "
            + _join_words(f"{team_link(t)} ({n})" for t, n in top_holders) + ".",
            (f"And the ones that have lost it in a bowl most often: " + _join_words(f"{team_link(t)} ({n})" for t, n in top_losers) + ".") if top_losers else "",
        ]))
        playoff = [g for g in post if g["season"] >= 2014]
        if playoff:
            pc = [g for g in playoff if g["outcome"] == "changed"]
            chapters.append(_chapter("The playoff era", [
                f"Since the College Football Playoff began with the 2014 season the belt has been on the line in {len(playoff)} postseason {_plural(len(playoff), 'game')}"
                + (f" and moved in {len(pc)} of them: " + _join_words(_game_link(g, f"{_score_wf(g)} ({g['season']} season)") for g in pc) + "." if pc else " and never moved."),
                "A 12-team bracket means the holder is likelier than ever to keep playing into January &mdash; and every playoff game is a belt game if the holder is in it. "
                "The belt has always been a lineal title; the playoff just made it a lineal title with a bracket.",
            ]))
        chapters.append(_chapter(f"The latest: {fmt_date(last['date'])}", [
            f"The most recent postseason belt game was {_game_link(last, _score_wf(last))}. "
            + ("The belt stayed." if last["outcome"] != "changed" else f"The belt went home with {team_link(last['new_holder'])}."),
        ]))
    lede = (f"Bowl games and playoff games are belt games too, whenever the holder is in one. {len(post)} times the belt has gone into the postseason on the line, "
            f"and {len(changes)} times it came out with a new owner.")
    stat = _stat(f"{len(changes)}", "postseason title changes, each one a reign that started in the dead of winter") if post else ""
    return _story_page("Bowl Season — The Belt in the Postseason",
                       "Every bowl and playoff game the College Football Belt has been on the line for: the first one, every postseason title change, and who held it through January.",
                       "Stories", "Bowl season", lede, "".join(chapters), stat)


# ---- 13. The vanished ------------------------------------------------------------

VANISHED_BEFORE = 1960


def generate_story_vanished(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    holders = {r["team"] for r in reigns}
    last_game = {}
    games_played = {}
    for g in belt_games:
        for t in (g["home"], g["away"]):
            if t in holders:
                last_game[t] = g
                games_played[t] = games_played.get(t, 0) + 1
    resume = {}
    for i, r in enumerate(reigns):
        d = resume.setdefault(r["team"], {"reigns": 0, "days": 0, "defenses": 0, "best": None, "first": r["start_date"]})
        d["reigns"] += 1
        d["days"] += reign_duration_days(r, today)
        d["defenses"] += r.get("defenses", 0)
        dd = reign_duration_days(r, today)
        if d["best"] is None or dd > d["best"][0]:
            d["best"] = (dd, i, r)
    vanished = [(t, last_game[t]) for t in holders if int(last_game[t]["date"][:4]) < VANISHED_BEFORE]
    vanished.sort(key=lambda tg: tg[1]["date"])
    ranked = sorted(vanished, key=lambda tg: (-resume[tg[0]]["days"], tg[1]["date"]))[:8]
    chapters = []
    for k, (t, g) in enumerate(ranked, 1):
        d = resume[t]
        best_days, bi, br = d["best"]
        yrs = today.year - int(g["date"][:4])
        paras = [
            f"{team_link(t)} held the belt {d['reigns']} {_plural(d['reigns'], 'time')} for {d['days']:,} days in all, with {d['defenses']} "
            f"{_plural(d['defenses'], 'defense')}; the best of it was {_reign_link(bi, fmt_duration(*reign_dates(br, today)))} starting {fmt_date(br['start_date'])}"
            + (f", taken from {esc(br['won_from'])}" if br.get("won_from") else "") + ".",
            f"Its last belt game of any kind was {_game_link(g, _score_wf(g))} on {fmt_date(g['date'])} &mdash; {yrs} years ago. "
            + (f"It has played for the belt {games_played[t]} times." if games_played.get(t, 0) > d["reigns"] else ""),
        ]
        chapters.append(_chapter(f"{esc(t)} &mdash; last seen {g['date'][:4]}", paras, f"No. {k}"))
    chapters.append(_chapter("The full roll", [
        f"{len(vanished)} of the {len(holders)} programs that have ever held the belt have not played a belt game since before {VANISHED_BEFORE}. "
        f"The oldest goodbyes: " + _list_more((f"{team_link(t)} ({g['date'][:4]})" for t, g in vanished), 10) + ".",
        "Some of these programs still play, a few divisions down; some dropped football in the Depression or the war years; a couple no longer exist as "
        "institutions. The lineage does not distinguish. A reign is a reign, and every current holder traces its title straight back through these names.",
    ]))
    chapters.append(_chapter("Why the belt remembers them", [
        "This is the part of the belt that no poll or playoff can reproduce. A ranking starts over every August; a lineal title cannot. The reason "
        "the belt is where it is today is a chain of specific Saturdays, and dozens of the links in that chain belong to programs the modern sport "
        "has forgotten. Take any one of them out and the belt is somewhere else.",
        f"The nearest any of these has come to a return is the schedule: none of them has played a current belt holder in decades. The "
        f"<a href=\"my-team.html\">My Team</a> page will tell you how far away each one is, which for most of them is &ldquo;not this century.&rdquo;",
    ]))
    lede = (f"{len(vanished)} programs won the belt, held it, and then dropped out of its story &mdash; no belt game since before {VANISHED_BEFORE}. "
            f"Some of them are the dynasties that built it. Ranked by how much of the belt&rsquo;s history they own.")
    stat = _stat(f"{len(vanished)}", f"former holders with no belt game since before {VANISHED_BEFORE}")
    return _story_page("The Vanished — The Belt Holders the Sport Forgot",
                       f"The College Football Belt holders with no belt game since before {VANISHED_BEFORE}: how long they held it, their best reign and the last time the belt saw them.",
                       "Stories", "The vanished", lede, "".join(chapters), stat)


# ---- 14. Not a single point ----------------------------------------------------

def generate_story_shutouts(lineage, belt_games):
    reigns = lineage["reigns"]
    today = date.today()
    by_reign = reign_games(belt_games)

    def allowed(g, team):
        h, a = (int(x) for x in g["score"].split("-"))
        return a if g["home"] == team else h

    perfect = []   # (games, rn, reign, points scored)
    most_shut = []
    for rn, games in by_reign.items():
        r = reigns[rn - 1]
        t = r["team"]
        pts_allowed = [allowed(g, t) for g in games]
        shut = sum(1 for p in pts_allowed if p == 0)
        scored = sum(int(g["score"].split("-")[0 if g["home"] == t else 1]) for g in games)
        if games and all(p == 0 for p in pts_allowed) and len(games) >= 3:
            perfect.append((len(games), rn, r, scored))
        most_shut.append((shut, len(games), rn, r))
    perfect.sort(key=lambda x: (-x[0], x[1]))
    most_shut.sort(key=lambda x: (-x[0], -x[1], x[2]))
    shutout_games = [g for g in belt_games if min(int(x) for x in g["score"].split("-")) == 0]
    shutout_changes = [g for g in shutout_games if g["outcome"] == "changed"]
    last_change = shutout_changes[-1] if shutout_changes else None
    last_any = shutout_games[-1] if shutout_games else None
    biggest = max(shutout_games, key=lambda g: max(int(x) for x in g["score"].split("-"))) if shutout_games else None
    by_decade = {}
    for g in shutout_games:
        by_decade[g["season"] // 10 * 10] = by_decade.get(g["season"] // 10 * 10, 0) + 1
    chapters = []
    for k, (n, rn, r, scored) in enumerate(perfect[:8], 1):
        s, e = reign_dates(r, today)
        games = by_reign[rn]
        def _line(g):
            opp_name = g["away"] if g["home"] == r["team"] else g["home"]
            own = int(g["score"].split("-")[0 if g["home"] == r["team"] else 1])
            return _game_link(g, f"{esc(opp_name)} {own}&ndash;0")
        opp = _list_more((_line(g) for g in games), 8)
        ended = ""
        if r.get("end_date"):
            nxt = by_reign.get(rn + 1)
            if nxt:
                ended = f" The first points anyone scored on it ended it: {_game_link(nxt[0], _score_wf(nxt[0]))}."
        chapters.append(_chapter(f"{esc(r['team'])}, {r['start_date'][:4]} &mdash; {n} games, 0 points allowed", [
            f"{_reign_link(rn - 1, fmt_duration(s, e))} with the belt, {n} belt {_plural(n, 'game')} counting the one that won it, {scored} points scored and none allowed. "
            f"The scores: {opp}.{ended}",
        ], f"No. {k}"))
    if most_shut and most_shut[0][0] > 0:
        shut, n, rn, r = most_shut[0]
        s, e = reign_dates(r, today)
        chapters.append(_chapter("Most shutouts in one reign", [
            f"{_reign_link(rn - 1, possessive(r['team']) + ' ' + fmt_duration(s, e))} reign from {fmt_date(r['start_date'])} recorded {shut} shutouts in {n} belt games"
            + (f" &mdash; the rest of the top five: " + _join_words(_reign_link(rn2 - 1, f"{esc(r2['team'])} {r2['start_date'][:4]} ({s2} in {n2})") for s2, n2, rn2, r2 in most_shut[1:5]) + "." if len(most_shut) > 1 else "."),
        ]))
    if last_change:
        chapters.append(_chapter(f"The last shutout takeover: {last_change['season']}", [
            f"The most recent time a program took the belt without allowing a point was {_game_link(last_change, _score_wf(last_change))}, {fmt_date(last_change['date'])}."
            + (f" The last shutout of any kind with the belt on the line was {_game_link(last_any, _score_wf(last_any))} in {last_any['season']}." if last_any and last_any is not last_change else ""),
            f"Shutouts by decade tell the story of the sport: " + ", ".join(f"{d}s {n}" for d, n in sorted(by_decade.items())) + ". "
            f"Scoring caught up with defense somewhere around the forward pass, and the belt&rsquo;s scorelines followed.",
        ]))
    if biggest:
        chapters.append(_chapter("The biggest", [
            f"The largest shutout with the belt on the line was {_game_link(biggest, _score_wf(biggest))} in {biggest['season']}"
            + (", a title change." if biggest["outcome"] == "changed" else ", a defense.")
            + f" {len(shutout_games)} of the {len(belt_games):,} belt games ended with one side scoreless, and {len(shutout_changes)} of those moved the belt.",
        ]))
    lede = (f"{len(shutout_games)} belt games have ended with one team scoreless. A few reigns went further than that: the belt was won and defended "
            f"without the holder allowing a single point, start to finish.")
    stat = _stat(f"{perfect[0][0]}", f"belt games without allowing a point, {possessive(perfect[0][2]['team'])} {perfect[0][2]['start_date'][:4]} reign") if perfect else ""
    return _story_page("Not a Single Point — The Reigns That Never Allowed a Score",
                       "College Football Belt reigns won and defended entirely by shutout, the most shutouts in one reign, and the last and biggest shutouts with the belt at stake.",
                       "Stories", "Not a single point", lede, "".join(chapters), stat)


def generate_sitemap(urls):
    """A plain sitemap.xml -- every URL, one <lastmod> for all of them
    (today's build date; nothing here tracks true per-page last-changed
    dates, and search engines treat a same-day sitemap-wide date as fine).
    """
    today = date.today().isoformat()
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        parts.append(f"  <url><loc>{esc(u)}</loc><lastmod>{today}</lastmod></url>")
    parts.append("</urlset>")
    return "\n".join(parts) + "\n"


def generate_robots_txt():
    return f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n"


def generate_ads_txt(publisher_id):
    """The IAB-standard authorized-sellers file AdSense requires at the
    domain root -- without it, ads stay disabled even with a valid publisher
    ID and loader script live on every page. Uses the bare "pub-XXXX" form
    (NOT the "ca-pub-XXXX" form the <script> tag/meta tag use). "DIRECT"
    means this site deals directly with Google (not through a reseller);
    the trailing ID is Google's own fixed certification authority ID for
    AdSense, the same for every publisher. Only written when
    ADSENSE_PUBLISHER_ID is set."""
    return f"google.com, {publisher_id}, DIRECT, f08c47fec0942fa0\n"


def generate_404_page():
    # GitHub Pages serves this one file for EVERY missing URL, nested paths
    # included (/games/nope.html, /conferences/x), so everything here has to
    # be root-absolute -- a relative styles.css would 404 too from a subfolder.
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Page Not Found — The College Football Belt</title>
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/styles.css?v={STYLES_VERSION}">
{head_extras('/')}

{site_header('/', None)}

<main class="wrap">
  <div class="pageIntro">
    <span class="kicker">404</span>
    <h1 class="pageTitle">Fumbled.</h1>
    <p class="lede">Whatever you were looking for isn&rsquo;t here &mdash; it might have moved,
      or it might never have existed. Either way, no need to punt.</p>
  </div>
  <div class="btnRow" style="margin-top:18px">
    <a class="btn" href="/">Current belt holder</a>
    <a class="btn ghost" href="/lineage.html">Full history</a>
    <a class="btn ghost" href="/all-games.html">Every belt game</a>
  </div>
</main>

{site_footer('/', 'The College Football Belt &mdash; lineal championship, since 1869.')}
'''


def generate_manifest_json():
    """Web app manifest -- lets a mobile (or desktop) visitor "Add to Home
    Screen"/"Install" the site as a standalone app. Icons are the same
    belt-buckle glyph generate_share_image.py already draws for the
    favicon, just rendered bigger (see generate_favicon())."""
    return {
        "name": "The College Football Belt",
        "short_name": "CFB Belt",
        "description": "The lineal college football championship, tracked on the field since 1869.",
        "start_url": "/?utm_source=pwa",
        "id": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": PAPER_LIGHT,
        "theme_color": "#8a6a34",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }


def generate_service_worker():
    """A small network-first service worker: an online visitor always gets
    a fresh fetch (belt data changes, so nobody should ever be served a
    stale cached page while a network is available); a successful
    same-origin GET response is cached afterward so a visitor who goes
    offline -- or loses signal mid-read -- can still reopen pages they've
    already visited. offline.html is the fallback for a page that was
    never cached. Cache name is tied to STYLES_VERSION so an actual
    deploy (any CSS change) clears out the old cache; an unrelated data
    rebuild that doesn't touch styles.css keeps it."""
    return f'''const CACHE_NAME = "cfb-belt-{STYLES_VERSION}";
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", (event) => {{
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.add(OFFLINE_URL)));
  self.skipWaiting();
}});

self.addEventListener("activate", (event) => {{
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
}});

self.addEventListener("fetch", (event) => {{
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {{
        if (response && response.status === 200) {{
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }}
        return response;
      }})
      .catch(() =>
        caches.match(event.request).then((cached) => {{
          if (cached) return cached;
          if (event.request.mode === "navigate") return caches.match(OFFLINE_URL);
          return Response.error();
        }})
      )
  );
}});
'''


def generate_offline_page():
    # Served by the service worker for any un-cached page while offline,
    # from whatever path was requested -- so links are root-absolute here
    # too, and the header is a plain brand line (no search/menu, since
    # nothing they point at is reachable offline anyway).
    return f'''<!doctype html>
<html lang="en">
<meta charset="UTF-8">
<title>Offline — The College Football Belt</title>
<meta name="robots" content="noindex">
<link rel="stylesheet" href="/styles.css?v={STYLES_VERSION}">
{head_extras('/')}

<header class="siteHead">
  <div class="wrap siteHeadRow">
    <a class="brand" href="/">{BELT_MARK_SVG}<span class="brandName">The College Football Belt</span></a>
  </div>
</header>

<main class="wrap">
  <div class="pageIntro">
    <span class="kicker">No connection</span>
    <h1 class="pageTitle">You&rsquo;re offline.</h1>
    <p class="lede">This page hasn&rsquo;t been saved for offline viewing yet &mdash; reconnect and
      try again, or open a page you&rsquo;ve already visited on this device; those stay
      available without a connection.</p>
  </div>
</main>
'''


def generate_feed(belt_games, recaps):
    """RSS 2.0 feed of belt CHANGES only (not every defense) -- newest
    first, so an RSS reader or RSS-to-email service can notify someone the
    moment the belt actually changes hands without them checking the site.
    Capped at the most recent 30 changes; a reader only ever cares about
    what's new anyway, and 327-some entries would be a wall for no benefit.
    """
    changes = [g for g in belt_games if g.get("outcome") == "changed"]
    changes.sort(key=lambda g: g["date"], reverse=True)

    items = []
    for g in changes[:30]:
        try:
            dt = datetime.strptime(g["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        recap = recaps.get(str(g["game_id"])) or {}
        # holder is always set here -- the very first ("established") game
        # has no defending holder and outcome "established", not "changed",
        # so it's never in `changes` above.
        home_score, away_score = (int(x) for x in g["score"].split("-"))
        winner_score, loser_score = ((home_score, away_score) if g["new_holder"] == g["home"]
                                      else (away_score, home_score))
        desc = recap.get("recap") or f'{g["new_holder"]} def. {g["holder"]} {winner_score}–{loser_score}.'
        link = f'{SITE_URL}/games/{g["game_id"]}.html'
        items.append(f'''
    <item>
      <title>{esc(g["new_holder"])} takes the belt from {esc(g["holder"])}</title>
      <link>{link}</link>
      <guid isPermaLink="true">{link}</guid>
      <pubDate>{format_datetime(dt)}</pubDate>
      <description>{esc(desc)}</description>
    </item>''')

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>The College Football Belt</title>
    <link>{SITE_URL}/</link>
    <description>Every time the lineal College Football Belt changes hands, on the field.</description>
    <language>en-us</language>{''.join(items)}
  </channel>
</rss>
'''


def main():
    (lineage, details, colors, next_game, upcoming_games, matchup,
     ai_preview, weather, recaps, historical_notes, game_plays,
     losers_lineages, championship_lineages, conference_lineages,
     team_paths, belt_risk, gameday, coaches) = load_data()
    belt_games = lineage["belt_games"]
    compute_sequence(belt_games)

    games_dir = os.path.join(OUT_DIR, "games")
    os.makedirs(games_dir, exist_ok=True)
    # index the player pages first so every player link on a game page
    # points at the file that will actually be written
    PLAYER_SLUG_BY_ID.update({pid: player_slug(pid, p["name"]) for pid, p in collect_players(belt_games, details).items()})

    minified_css = minify_css(STYLES_CSS)
    with open(os.path.join(OUT_DIR, "styles.css"), "w", encoding="utf-8") as f:
        f.write(minified_css)

    with open(os.path.join(OUT_DIR, "CNAME"), "w", encoding="utf-8") as f:
        f.write(CUSTOM_DOMAIN + "\n")

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
        merged["player_stats"] = d.get("player_stats")
        merged["recap"] = recaps.get(str(gid))
        merged["historical_note"] = historical_notes.get(str(gid))
        merged["key_plays"] = game_plays.get(str(gid))

        prev_game = belt_games[i - 1] if i > 0 else None
        next_belt_game = belt_games[i + 1] if i < len(belt_games) - 1 else None

        html_out = render_page(merged, colors, prev_game, next_belt_game, len(belt_games))
        with open(os.path.join(games_dir, f"{gid}.html"), "w", encoding="utf-8") as f:
            f.write(html_out)
        written += 1

    homepage_html = generate_homepage(lineage, colors, belt_games, next_game, upcoming_games, belt_risk, gameday)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(homepage_html)

    # Championship belt Full History / All Games, in each available scope
    # (build_lineage.py's own SCOPES -- Combined/FBS-only/FCS-only, see
    # that module's docstring). "combined" is always available (it's
    # `lineage`/`belt_games` above, already loaded); "fbs"/"fcs" only once
    # their own one-time historical bootstrap has run. Only these two
    # pages get the scope treatment for now -- the rest of the site
    # (homepage, records, team pages) stays on the combined/real belt.
    championship_available_scopes = tuple(
        s for s in ("combined", "fbs", "fcs") if championship_lineages[s] is not None)
    championship_belt_games = {"combined": belt_games}
    for scope in championship_available_scopes:
        if scope == "combined":
            continue
        scope_belt_games = championship_lineages[scope]["belt_games"]
        compute_sequence(scope_belt_games)
        championship_belt_games[scope] = scope_belt_games

    for scope in championship_available_scopes:
        lineage_html = generate_lineage_page(
            championship_lineages[scope], colors, championship_belt_games[scope],
            scope, championship_available_scopes)
        with open(os.path.join(OUT_DIR, CHAMPIONSHIP_LINEAGE_FILENAMES[scope]), "w", encoding="utf-8") as f:
            f.write(lineage_html)
    for scope in ("fbs", "fcs"):
        if scope not in championship_available_scopes:
            warnings.append(f"{DATA_DIR}/lineage_{scope}.json not found -- skipped "
                             f"{CHAMPIONSHIP_LINEAGE_FILENAMES[scope]}/"
                             f"{CHAMPIONSHIP_ALL_GAMES_FILENAMES[scope]} (run build_lineage.py's "
                             f"one-time bootstrap for that scope to enable it)")

    # Each of the three Losers Belt scopes (build_losers_lineage.py's
    # SCOPES) is bootstrapped independently, so any subset of them may
    # have data at a given point -- only "combined" is guaranteed once
    # any Losers Belt data exists at all (it's the original scope, and
    # keeps the original unsuffixed filename/URL).
    available_scopes = tuple(s for s in ("combined", "fbs", "fcs") if losers_lineages[s] is not None)
    losers_belt_total_pages = {}
    for scope in available_scopes:
        scope_lineage = losers_lineages[scope]
        scope_reigns = scope_lineage["reigns"]
        scope_current = scope_reigns[-1]
        scope_change_index = build_change_game_index(scope_lineage["belt_games"])
        scope_today = date.today()
        # Computed once per scope (not once per page) -- every page of a
        # scope shares the same full-history rows, both for slicing into
        # each page's <tr> markup and for the JSON companion file the page
        # fetches client-side for search/sort across every reign, not just
        # whichever 100 happen to be server-rendered on that one file.
        all_rows = build_losers_belt_rows(scope_reigns, scope_current, scope_change_index, scope_today)
        data_path = os.path.join(OUT_DIR, losers_belt_data_filename(scope))
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(all_rows, f, separators=(",", ":"))
        total_reigns = len(scope_reigns)
        total_pages = max(1, math.ceil(total_reigns / LOSERS_BELT_PAGE_SIZE))
        losers_belt_total_pages[scope] = total_pages
        for page in range(1, total_pages + 1):
            page_html = generate_losers_belt_page(scope_lineage, scope, available_scopes, page, all_rows=all_rows)
            filename = losers_belt_page_filename(scope, page)
            with open(os.path.join(OUT_DIR, filename), "w", encoding="utf-8") as f:
                f.write(page_html)
    for scope in ("combined", "fbs", "fcs"):
        if scope not in available_scopes:
            warnings.append(f"{DATA_DIR}/losers_lineage{'' if scope == 'combined' else '_' + scope}.json "
                             f"not found -- skipped {LOSERS_BELT_FILENAMES[scope]} (run "
                             f"build_losers_lineage.py's one-time bootstrap to enable it)")

    for scope in championship_available_scopes:
        all_games_html = generate_all_games_page(
            championship_lineages[scope], colors, championship_belt_games[scope],
            scope, championship_available_scopes)
        with open(os.path.join(OUT_DIR, CHAMPIONSHIP_ALL_GAMES_FILENAMES[scope]), "w", encoding="utf-8") as f:
            f.write(all_games_html)

    # Conference belts (build_conference_lineage.py) -- one page per
    # conference that's actually been bootstrapped, plus an index hub.
    # Same no-op-when-unset pattern as everything else here: a conference
    # with no lineage file yet simply doesn't get a page, no error.
    conferences_out_dir = os.path.join(OUT_DIR, "conferences")
    if conference_lineages:
        os.makedirs(conferences_out_dir, exist_ok=True)
        for slug, conf_lineage in conference_lineages.items():
            conf_html = generate_conference_belt_page(conf_lineage, slug)
            with open(os.path.join(conferences_out_dir, f"{slug}.html"), "w", encoding="utf-8") as f:
                f.write(conf_html)
        index_html = generate_conferences_index_page(conference_lineages)
        with open(os.path.join(conferences_out_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(index_html)
        print(f"Wrote {len(conference_lineages)} conference belt page(s) + index to "
              f"{conferences_out_dir}/")
    else:
        warnings.append(f"no {DATA_DIR}/conferences/*_lineage.json found -- skipped every "
                         f"conference belt page (run build_conference_lineage.py's one-time "
                         f"bootstrap to enable them; the nav's \"Conferences\" link will 404 "
                         f"until at least one exists)")

    preview_html = generate_preview_page(next_game, matchup, ai_preview, weather, colors, belt_risk,
                                         lineage=lineage, belt_games=belt_games)
    with open(os.path.join(OUT_DIR, "preview.html"), "w", encoding="utf-8") as f:
        f.write(preview_html)

    records_html = generate_records_page(lineage, colors, belt_games, coaches)
    with open(os.path.join(OUT_DIR, "records.html"), "w", encoding="utf-8") as f:
        f.write(records_html)

    with open(os.path.join(OUT_DIR, "story-longest-reigns.html"), "w", encoding="utf-8") as f:
        f.write(generate_story_longest_reigns(lineage, belt_games))

    with open(os.path.join(OUT_DIR, "story-most-defended.html"), "w", encoding="utf-8") as f:
        f.write(generate_story_most_defended(lineage, belt_games))

    with open(os.path.join(OUT_DIR, "stories.html"), "w", encoding="utf-8") as f:
        f.write(generate_stories_hub(lineage, belt_games))
    for fname, html_out in (
        ("story-one-week-wonders.html", generate_story_one_week_wonders(lineage, belt_games)),
        ("story-giant-killers.html", generate_story_giant_killers(lineage, belt_games)),
        ("story-coast-to-coast.html", generate_story_coast_to_coast(lineage, colors, belt_games)),
        ("story-long-way-back.html", generate_story_long_way_back(lineage, belt_games)),
        ("story-changing-hands.html", generate_story_changing_hands(lineage, belt_games)),
        ("story-new-years.html", generate_story_new_years(lineage, belt_games)),
        ("story-four-ages.html", generate_story_four_ages(lineage, belt_games)),
        ("story-wire-to-wire.html", generate_story_wire_to_wire(lineage, belt_games)),
        ("story-wildest-seasons.html", generate_story_wildest_seasons(lineage, belt_games)),
        ("story-ties.html", generate_story_ties(lineage, belt_games)),
        ("story-opening-day.html", generate_story_opening_day(lineage, belt_games)),
        ("story-bowl-season.html", generate_story_bowl_season(lineage, belt_games)),
        ("story-vanished.html", generate_story_vanished(lineage, belt_games)),
        ("story-shutouts.html", generate_story_shutouts(lineage, belt_games)),
    ):
        with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
            f.write(html_out)

    on_this_day_html = generate_on_this_day_page(belt_games, lineage["reigns"])
    with open(os.path.join(OUT_DIR, "on-this-day.html"), "w", encoding="utf-8") as f:
        f.write(on_this_day_html)

    teams_dir = os.path.join(OUT_DIR, "teams")
    teams_written, team_slugs = generate_team_pages(lineage, colors, belt_games, teams_dir)

    # ---- the 2026-09-16 batch: reigns, rivalries, states, decades, and the
    # single pages (outlook, timeline, leaders, heartbreak, lean, daily, about)
    reigns_written = generate_reign_pages(lineage, colors, belt_games, os.path.join(OUT_DIR, "reigns"))
    rivalries_written = generate_rivalry_pages(lineage, colors, belt_games, os.path.join(OUT_DIR, "rivalries"))
    state_codes = generate_state_pages(lineage, colors, belt_games, os.path.join(OUT_DIR, "states"))
    decade_slugs = generate_decade_pages(lineage, colors, belt_games, os.path.join(OUT_DIR, "decades"))
    for fname, html_out in (
        ("outlook.html", generate_outlook_page(belt_risk, lineage, colors, next_game)),
        ("timeline.html", generate_timeline_page(lineage, colors, belt_games)),
        ("leaders.html", generate_leaders_page(belt_games, details, colors)),
        ("heartbreak.html", generate_heartbreak_page(lineage, colors, belt_games)),
        ("lean.html", generate_lean_page(lineage, belt_games, colors)),
        ("daily.html", generate_daily_page(lineage, colors, belt_games)),
        ("about.html", generate_about_page(lineage, belt_games)),
    ):
        with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
            f.write(html_out)
    print(f"Wrote {reigns_written} reign pages, {len(rivalries_written)} rivalry pages, {len(state_codes)} state pages, "
          f"{len(decade_slugs)} decade pages, and outlook/timeline/leaders/heartbreak/lean/daily/about to {OUT_DIR}/")

    players_dir = os.path.join(OUT_DIR, "players")
    players_written, player_slugs = generate_player_pages(belt_games, details, players_dir)

    map_html = generate_map_page(lineage, colors)
    wrote_map = map_html is not None
    if wrote_map:
        with open(os.path.join(OUT_DIR, "map.html"), "w", encoding="utf-8") as f:
            f.write(map_html)
    else:
        warnings.append(f"{STATE_SHAPES_PATH} not found -- skipped map.html")

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

    badge_svg = generate_badge_svg(lineage, colors)
    with open(os.path.join(OUT_DIR, "badge.svg"), "w", encoding="utf-8") as f:
        f.write(badge_svg)

    embed_html = generate_embed_page(lineage, colors)
    with open(os.path.join(OUT_DIR, "embed.html"), "w", encoding="utf-8") as f:
        f.write(embed_html)

    privacy_html = generate_privacy_page()
    with open(os.path.join(OUT_DIR, "privacy.html"), "w", encoding="utf-8") as f:
        f.write(privacy_html)

    if ADSENSE_PUBLISHER_ID:
        with open(os.path.join(OUT_DIR, "ads.txt"), "w", encoding="utf-8") as f:
            f.write(generate_ads_txt(ADSENSE_PUBLISHER_ID))

    compare_html = generate_compare_page(lineage, colors, belt_games)
    with open(os.path.join(OUT_DIR, "compare.html"), "w", encoding="utf-8") as f:
        f.write(compare_html)

    wrote_my_team = False
    if team_paths:
        my_team_html = generate_my_team_page(team_paths, colors)
        with open(os.path.join(OUT_DIR, "my-team.html"), "w", encoding="utf-8") as f:
            f.write(my_team_html)
        wrote_my_team = True
    else:
        warnings.append("belt_data/team_paths.json not found -- skipped my-team.html "
                         "(build_lineage.py writes it every run; only missing on a "
                         "very first run before that's ever completed once)")

    # ---- season pages (wishlist #4, 2026-09-16) -- one page per distinct
    # season already present in belt_games, plus an index -- no new data
    # or API call, just a different cut of what load_data() already read.
    seasons_summary = []
    wrote_seasons = False
    if belt_games:
        seasons_map = {}
        for g in belt_games:
            seasons_map.setdefault(g["season"], []).append(g)
        all_seasons = sorted(seasons_map)
        reign_by_start = {(r["team"], r["start_date"]): r for r in lineage["reigns"]}
        for season_year in all_seasons:
            season_games = seasons_map[season_year]
            is_current = season_year == all_seasons[-1]
            season_html = generate_season_page(
                season_year, season_games, reign_by_start, all_seasons,
                remaining_schedule=upcoming_games if is_current else None,
            )
            with open(os.path.join(OUT_DIR, f"season-{season_year}.html"), "w", encoding="utf-8") as f:
                f.write(season_html)
            title_changes = sum(1 for g in season_games if g["outcome"] in ("changed", "established"))
            seasons_summary.append({
                "year": season_year, "games": len(season_games),
                "title_changes": title_changes,
                "closing_team": season_games[-1]["new_holder"],
                "is_current": is_current,
            })
        seasons_index_html = generate_seasons_index_page(seasons_summary)
        with open(os.path.join(OUT_DIR, "seasons.html"), "w", encoding="utf-8") as f:
            f.write(seasons_index_html)
        wrote_seasons = True

    # ---- Defend or Dethrone (wishlist #5) -- always written (needs no
    # optional upstream data, just next_game.json + the always-present
    # belt_games tail); grading happens entirely client-side.
    dod_html = generate_defend_or_dethrone_page(next_game, belt_games[-10:])
    with open(os.path.join(OUT_DIR, "defend-or-dethrone.html"), "w", encoding="utf-8") as f:
        f.write(dod_html)

    trivia_pool = build_trivia_pool(lineage, belt_games, random.Random())
    trivia_html = generate_trivia_page(trivia_pool)
    with open(os.path.join(OUT_DIR, "trivia.html"), "w", encoding="utf-8") as f:
        f.write(trivia_html)

    api_dir = os.path.join(OUT_DIR, API_DIR)
    os.makedirs(api_dir, exist_ok=True)
    for name, payload in generate_api_files(lineage, belt_games, next_game).items():
        with open(os.path.join(api_dir, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    api_docs_html = generate_api_docs_page()
    with open(os.path.join(OUT_DIR, "api.html"), "w", encoding="utf-8") as f:
        f.write(api_docs_html)

    search_index = generate_search_index(lineage, belt_games, team_slugs,
                                         [s["year"] for s in seasons_summary], conference_lineages)
    search_index += [{"n": f"{a} vs. {b}", "u": f"rivalries/{slug}.html", "t": "Rivalry", "k": "belt games"} for slug, a, b in rivalries_written]
    search_index += [{"n": STATE_NAMES.get(c, c), "u": f"states/{c.lower()}.html", "t": "State", "k": c} for c in state_codes]
    search_index += [{"n": f"The {s}", "u": f"decades/{s}.html", "t": "Decade"} for s in decade_slugs]
    with open(os.path.join(OUT_DIR, "search-index.json"), "w", encoding="utf-8") as f:
        json.dump(search_index, f, ensure_ascii=False, separators=(",", ":"))

    sitemap_urls = [f"{SITE_URL}/", f"{SITE_URL}/lineage.html", f"{SITE_URL}/all-games.html",
                     f"{SITE_URL}/records.html", f"{SITE_URL}/preview.html",
                     f"{SITE_URL}/on-this-day.html", f"{SITE_URL}/embed.html",
                     f"{SITE_URL}/compare.html", f"{SITE_URL}/trivia.html", f"{SITE_URL}/api.html",
                     f"{SITE_URL}/stories.html", f"{SITE_URL}/story-longest-reigns.html",
                     f"{SITE_URL}/story-most-defended.html", f"{SITE_URL}/privacy.html"]
    if wrote_ruleset:
        sitemap_urls.append(f"{SITE_URL}/ruleset.html")
    if wrote_map:
        sitemap_urls.append(f"{SITE_URL}/map.html")
    if wrote_my_team:
        sitemap_urls.append(f"{SITE_URL}/my-team.html")
    if wrote_seasons:
        sitemap_urls.append(f"{SITE_URL}/seasons.html")
        sitemap_urls += [f"{SITE_URL}/season-{s['year']}.html" for s in seasons_summary]
    sitemap_urls.append(f"{SITE_URL}/defend-or-dethrone.html")
    sitemap_urls += [f"{SITE_URL}/{p}" for p in ("outlook.html", "timeline.html", "leaders.html", "heartbreak.html",
                                                  "lean.html", "daily.html", "about.html", "rivalries/index.html",
                                                  "states/index.html", "decades/index.html",
                                                  "story-one-week-wonders.html", "story-giant-killers.html",
                                                  "story-coast-to-coast.html", "story-long-way-back.html",
                                                  "story-changing-hands.html", "story-new-years.html")]
    sitemap_urls += [f"{SITE_URL}/rivalries/{slug}.html" for slug, _, _ in rivalries_written]
    sitemap_urls += [f"{SITE_URL}/states/{c.lower()}.html" for c in state_codes]
    sitemap_urls += [f"{SITE_URL}/decades/{s}.html" for s in decade_slugs]
    sitemap_urls += [f"{SITE_URL}/reigns/{n}.html" for n in range(1, reigns_written + 1)]
    for scope in available_scopes:
        sitemap_urls.append(f"{SITE_URL}/{LOSERS_BELT_FILENAMES[scope]}")
    for scope in championship_available_scopes:
        if scope == "combined":
            continue  # lineage.html/all-games.html already in the base list above
        sitemap_urls.append(f"{SITE_URL}/{CHAMPIONSHIP_LINEAGE_FILENAMES[scope]}")
        sitemap_urls.append(f"{SITE_URL}/{CHAMPIONSHIP_ALL_GAMES_FILENAMES[scope]}")
    if conference_lineages:
        sitemap_urls.append(f"{SITE_URL}/conferences/index.html")
        sitemap_urls += [f"{SITE_URL}/conferences/{slug}.html" for slug in conference_lineages]
    sitemap_urls += [f"{SITE_URL}/teams/{slug}.html" for slug in team_slugs]
    sitemap_urls += [f"{SITE_URL}/players/{slug}.html" for slug in player_slugs]
    sitemap_urls += [f"{SITE_URL}/games/{g['game_id']}.html" for g in belt_games]

    with open(os.path.join(OUT_DIR, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(generate_sitemap(sitemap_urls))

    with open(os.path.join(OUT_DIR, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(generate_robots_txt())

    with open(os.path.join(OUT_DIR, "404.html"), "w", encoding="utf-8") as f:
        f.write(generate_404_page())

    with open(os.path.join(OUT_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(generate_feed(belt_games, recaps))

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(generate_manifest_json(), f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUT_DIR, "sw.js"), "w", encoding="utf-8") as f:
        f.write(generate_service_worker())

    with open(os.path.join(OUT_DIR, "offline.html"), "w", encoding="utf-8") as f:
        f.write(generate_offline_page())

    print(f"Wrote {written} game pages to {games_dir}/")
    saved_pct = round(100 * (1 - len(minified_css) / len(STYLES_CSS)), 1) if STYLES_CSS else 0
    print(f"Wrote shared stylesheet to {OUT_DIR}/styles.css "
          f"(minified {len(STYLES_CSS):,} -> {len(minified_css):,} bytes, {saved_pct}% smaller)")
    print(f"Wrote {OUT_DIR}/CNAME ({CUSTOM_DOMAIN})")
    print(f"Wrote homepage to {OUT_DIR}/index.html")
    print(f"Wrote full-history page to {OUT_DIR}/lineage.html")
    for scope in available_scopes:
        n_pages = losers_belt_total_pages[scope]
        page_note = f" ({n_pages} pages)" if n_pages > 1 else ""
        print(f"Wrote Losers Belt ({scope}) page to {OUT_DIR}/{LOSERS_BELT_FILENAMES[scope]}{page_note}")
    print(f"Wrote all-games page to {OUT_DIR}/all-games.html")
    print(f"Wrote preview page to {OUT_DIR}/preview.html")
    print(f"Wrote records page to {OUT_DIR}/records.html")
    print(f"Wrote stories.html and {len(STORIES)} story articles to {OUT_DIR}/")
    print(f"Wrote On This Day page to {OUT_DIR}/on-this-day.html")
    print(f"Wrote {teams_written} team pages to {teams_dir}/")
    print(f"Wrote {players_written} player pages to {players_dir}/")
    if wrote_map:
        print(f"Wrote map page to {OUT_DIR}/map.html")
    if wrote_ruleset:
        print(f"Wrote ruleset page to {OUT_DIR}/ruleset.html")
    print(f"Wrote badge.svg, embed.html, privacy.html, and compare.html to {OUT_DIR}/")
    if ADSENSE_PUBLISHER_ID:
        print(f"Wrote ads.txt to {OUT_DIR}/ (AdSense publisher {ADSENSE_PUBLISHER_ID})")
    print(f"Wrote trivia.html ({len(trivia_pool)} question(s) in the pool) to {OUT_DIR}/")
    print(f"Wrote api.html and {API_DIR}/current.json, reigns.json, games.json to {OUT_DIR}/")
    print(f"Wrote sitemap.xml ({len(sitemap_urls)} URLs), robots.txt, 404.html, and feed.xml "
          f"({min(len([g for g in belt_games if g.get('outcome') == 'changed']), 30)} item(s)) to {OUT_DIR}/")
    print(f"Wrote manifest.json, sw.js, and offline.html to {OUT_DIR}/ (PWA/offline support)")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings[:20]:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
