"""
CFB-4 (2026-09-29 audit): the four pages the sister sites have that this one
didn't -- the belt vs. the national champion (champions.html), the belt in the
postseason (postseason.html), home/road/neutral splits (splits.html), the
belt vs. the season's best record (standings.html) and a search results page
(search.html?q=..., over the header search's own search-index.json, so the
sister sites' network search can link here). Everything is computed from
data already on hand: the lineage, the historical game archive and the AP poll
cache (historical_data/rankings.json). No new API calls.

build_site.main() calls build(B, ...) with B = the build_site module, so these
pages use the site's own page shell and helpers.
"""

import gzip
import json
import os
from collections import defaultdict
from datetime import date

import classification

PAGES = ("champions.html", "postseason.html", "splits.html", "standings.html", "search.html")


def _season_end_holders(belt_games):
    """{season: holder after that season's last belt game}."""
    out = {}
    for g in belt_games:
        out[g["season"]] = g["new_holder"]
    return out


def _held_in_season(belt_games):
    """{season: set of teams that held the belt at some point that season}: the
    holder going into its first belt game plus everyone who took it."""
    held = defaultdict(set)
    for g in belt_games:
        if g.get("holder"):
            held[g["season"]].add(g["holder"])
        held[g["season"]].add(g["new_holder"])
    return held


def _in_progress_season(today=None):
    """The season still being played (August through the January bowls), else None."""
    today = today or date.today()
    return today.year if today.month >= 8 else (today.year - 1 if today.month == 1 else None)


def _ap_champions():
    """{season: final AP No. 1} from the poll cache: the postseason poll when
    the season has one (1936 on), never the current season's in-progress poll."""
    p = os.path.join("historical_data", "rankings.json")
    try:
        with open(p) as f:
            seasons = json.load(f).get("seasons") or {}
    except (OSError, ValueError):
        return {}
    out = {}
    for y, v in seasons.items():
        weeks = (v or {}).get("weeks") or []
        final = [w for w in weeks if w.get("st") == "postseason" and w.get("ap")]
        if not final:
            continue
        no1 = [t for t, rk in final[-1]["ap"] if rk == 1]
        if len(no1) == 1:
            out[int(y)] = no1[0]
    return out


def _archive():
    """Every game in historical_data/all_games.json.gz as dicts (the fields the
    classification helpers read)."""
    p = os.path.join("historical_data", "all_games.json.gz")
    try:
        with gzip.open(p, "rt") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for rows in (d.get("seasons") or {}).values():
        for r in rows:
            gid, day, season, week, st, home, away, hp, ap, neutral, hconf, aconf = r[:12]
            if hp is None or ap is None:
                continue
            out.append({"id": gid, "date": day, "season": season, "season_type": st, "home": home, "away": away,
                        "home_points": hp, "away_points": ap, "neutral": neutral,
                        "home_conference": hconf, "away_conference": aconf})
    return out


def _records(games):
    """{season: {team: [w, l, t]}} over FBS-scope games (every game through
    1977, then FBS vs. FBS)."""
    rec = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for g in classification.scope_games(games, "fbs"):
        s, h, a, hp, ap = g["season"], g["home"], g["away"], g["home_points"], g["away_points"]
        if hp > ap:
            rec[s][h][0] += 1
            rec[s][a][1] += 1
        elif ap > hp:
            rec[s][a][0] += 1
            rec[s][h][1] += 1
        else:
            rec[s][h][2] += 1
            rec[s][a][2] += 1
    return rec


def _pct(r):
    n = sum(r)
    return (r[0] + r[2] / 2) / n if n else 0


def _wlt(r):
    return f"{r[0]}&ndash;{r[1]}" + (f"&ndash;{r[2]}" if r[2] else "")


def _shell(B, key, title, desc, kicker, heading, lede, body, stat=""):
    return f'''{B.page_head(title, desc, "", B.share_meta(key, heading, kicker, stat))}

{B.site_header('', key)}

<main class="wrap">
  {B.page_intro(kicker, heading, lede)}
{body}
</main>

{B.site_footer('', 'Every belt game sourced from the College Football Data API; polls from the AP.')}
'''


def _stats(items):
    return '<div class="miniStats" style="margin:10px 0 30px">' + "".join(
        f'<div><span class="n tabular">{v}</span><span class="l">{k}</span></div>' for v, k in items) + "</div>"


def _table(head, rows, min_width=560):
    right = ' style="text-align:right"'
    th = "".join(f'<th{right if r else ""}>{h}</th>' for h, r in head)
    return (f'<div class="tableScroll"><table class="reignsTable" style="min-width:{min_width}px"><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _season_link(y, seasons_with_pages):
    return f'<a href="season-{y}.html">{y}</a>' if y in seasons_with_pages else str(y)


# ------------------------------------------------------------ champions --

def champions_page(B, lineage, belt_games, seasons_with_pages):
    champs = _ap_champions()
    end = _season_end_holders(belt_games)
    held = _held_in_season(belt_games)
    played = defaultdict(set)
    for g in belt_games:
        played[g["season"]] |= {g["holder"], g["opponent"]}
    holders = {r["team"] for r in lineage["reigns"]}
    rows, n, finished, held_some, played_some = [], 0, 0, 0, 0
    live = _in_progress_season()
    for y in sorted(champs, reverse=True):
        c = champs[y]
        if y not in end or y == live:
            continue
        n += 1
        fin = end[y] == c
        h = c in held[y]
        p = c in played[y]
        finished += fin
        held_some += h
        played_some += p
        verdict = ("Finished the season with the belt" if fin else "Held it, then lost it" if h else
                   "Played for it, never held it" if p else "Never played for it")
        rows.append(f'<tr><td class="num">{_season_link(y, seasons_with_pages)}</td><td>{B.team_link(c, "", holders)}</td>'
                    f'<td>{B.team_link(end[y], "", holders)}</td><td>{verdict}</td></tr>')
    if not n:
        return None
    body = _stats([(f"{finished} of {n}", "Seasons the AP champion finished with the belt"),
                   (f"{held_some}", "Seasons the champion held it at some point"),
                   (f"{played_some}", "Seasons the champion played a belt game"),
                   (f"{n - played_some}", "Seasons the champion never played for it")])
    body += '<div class="sectionHead"><span class="tag">Every season</span><h2>The champion and the belt, since the AP poll began</h2></div>'
    body += _table([("Season", False), ("AP national champion", False), ("Belt at season&rsquo;s end", False), ("The champion and the belt", False)], rows)
    body += ('<p class="noteBox">The national champion here is the No. 1 team in the final AP poll, the one continuous poll back to 1936 '
             '(before 1968 it was taken before the bowls). &ldquo;Belt at season&rsquo;s end&rdquo; is whoever held it after the season&rsquo;s '
             'last belt game. See also <a href="polls.html">the belt vs. the polls</a>, week by week.</p>')
    return _shell(B, "champions", "The Belt vs. the National Champion",
                  f"How often the AP national champion finished the season holding the College Football Belt: {finished} of {n} seasons since 1936, season by season.",
                  "Champions", "The belt vs. the national champion",
                  "The poll crowns a champion; the belt goes to whoever beat the last holder. Here is how often the two agree, every season since the AP poll began.",
                  body, f"{finished} of {n} seasons")


# ----------------------------------------------------------- postseason --

def postseason_page(B, lineage, belt_games, seasons_with_pages):
    post = [g for g in belt_games if g.get("season_type") == "postseason"]
    if not post:
        return None
    holders = {r["team"] for r in lineage["reigns"]}
    changes = [g for g in post if g["outcome"] == "changed"]
    ties = [g for g in post if g["score"].split("-")[0] == g["score"].split("-")[1]]
    carried = len({g["season"] for g in post})
    rows = []
    for g in reversed(post):
        w, l, wp, lp = B.game_score_winner_first(g)
        res = "Changed hands" if g["outcome"] == "changed" else ("Tie, holder kept it" if wp == lp else "Defended")
        rows.append(f'<tr><td class="num">{_season_link(g["season"], seasons_with_pages)}</td><td>{B.fmt_date(g["date"])}</td>'
                    f'<td><a href="games/{g["game_id"]}.html">{B.esc(w)} {wp}&ndash;{lp} {B.esc(l)}</a></td>'
                    f'<td>{B.team_link(g["holder"], "", holders)}</td><td>{res}</td></tr>')
    rate_all = sum(1 for g in belt_games if g["outcome"] == "changed") / len(belt_games)
    body = _stats([(len(post), "Postseason belt games"), (carried, "Seasons with a postseason belt game"),
                   (len(changes), "Title changes in the postseason"), (f"{len(changes) / len(post) * 100:.0f}%", f"Changed hands (vs. {rate_all * 100:.0f}% overall)")])
    body += '<div class="sectionHead"><span class="tag">Every one</span><h2>Postseason belt games, newest first</h2></div>'
    body += _table([("Season", False), ("Date", False), ("Game", False), ("Holder", False), ("Result", False)], rows, 640)
    body += ('<p class="noteBox">Bowl games, conference title games and the playoff all count the same as any other game: '
             'beat the holder and the belt is yours. The story of it is in <a href="story-bowl-season.html">Bowl Season</a>.'
             + (f' {len(ties)} postseason belt game{"s" if len(ties) != 1 else ""} ended in a tie; the holder kept it.' if ties else "") + '</p>')
    return _shell(B, "postseason", "The Belt in the Postseason — Bowls and the Playoff",
                  f"Every College Football Belt game played in the postseason: {len(post)} bowl, title and playoff games, {len(changes)} of them title changes.",
                  "Postseason", "The belt in bowl season",
                  f"The belt has been on the line after the regular season in {carried} seasons: bowls, conference title games and the playoff. The holder usually gets a good opponent and a neutral field, and it shows.",
                  body, f"{len(post)} postseason belt games")


# --------------------------------------------------------------- splits --

def splits_page(B, lineage, belt_games, ot_story=True):
    buckets = {"home": [0, 0, 0], "road": [0, 0, 0], "neutral": [0, 0, 0]}
    by_decade = defaultdict(lambda: {"home": [0, 0, 0], "road": [0, 0, 0], "neutral": [0, 0, 0]})
    for g in belt_games:
        if g["outcome"] == "established" or not g.get("holder"):
            continue
        where = "neutral" if g.get("neutral") else ("home" if g["home"] == g["holder"] else "road")
        h, a = (int(x) for x in g["score"].split("-"))
        mine, theirs = (h, a) if g["home"] == g["holder"] else (a, h)
        k = 0 if mine > theirs else (1 if mine < theirs else 2)
        buckets[where][k] += 1
        by_decade[g["season"] // 10 * 10][where][k] += 1
    total = [sum(buckets[w][i] for w in buckets) for i in range(3)]
    if not sum(total):
        return None
    cards = "".join(
        f'<section class="recordCard"><h2>{lab}</h2><p class="recordCardSub">{sum(r):,} belt games &middot; held on in {_pct(r) * 100:.0f}%</p>'
        f'<div class="miniStats"><div><span class="n tabular">{r[0]:,}</span><span class="l">Defenses</span></div>'
        f'<div><span class="n tabular">{r[1]:,}</span><span class="l">Title changes</span></div>'
        f'<div><span class="n tabular">{r[2]:,}</span><span class="l">Ties (holder keeps it)</span></div></div></section>'
        for lab, r in (("At home", buckets["home"]), ("On the road", buckets["road"]), ("Neutral site", buckets["neutral"])))
    rows = []
    for dec in sorted(by_decade, reverse=True):
        x = by_decade[dec]
        cells = "".join(f'<td class="num">{(_wlt(x[w]) + f" <small>({_pct(x[w]) * 100:.0f}%)</small>") if sum(x[w]) else "&mdash;"}</td>'
                        for w in ("home", "road", "neutral"))
        rows.append(f'<tr><td>{dec}s</td>{cells}</tr>')
    body = _stats([(f"{_pct(buckets['home']) * 100:.0f}%", "Holder keeps it at home"),
                   (f"{_pct(buckets['road']) * 100:.0f}%", "Holder keeps it on the road"),
                   (f"{_pct(buckets['neutral']) * 100:.0f}%", "Holder keeps it on a neutral field"),
                   (f"{sum(total):,}", "Belt games with a holder")])
    body += f'<div class="recordsGrid">{cards}</div>'
    body += '<div class="sectionHead"><span class="tag">By decade</span><h2>The holder&rsquo;s record, home, road and neutral</h2></div>'
    body += _table([("Decade", False), ("Home", True), ("Road", True), ("Neutral", True)], rows, 480)
    body += ('<p class="noteBox">Records are the holder&rsquo;s: a win or tie keeps the belt, a loss hands it over.'
             + (' Overtime belt games (box scores from 2003 on) have <a href="story-overtime.html">their own story</a>.' if ot_story else '')
             + '</p>')
    return _shell(B, "splits", "Home, Road and Neutral — The Belt's Splits",
                  f"How often the College Football Belt holder keeps it at home ({_pct(buckets['home']) * 100:.0f}%), on the road ({_pct(buckets['road']) * 100:.0f}%) and on a neutral field, by decade.",
                  "Splits", "Home, road and neutral",
                  "Home field matters more when the belt is on the line. The holder&rsquo;s record by where the game was played, since 1869.",
                  body, f"{_pct(buckets['home']) * 100:.0f}% at home")


# ------------------------------------------------------------ standings --

def standings_page(B, lineage, belt_games, seasons_with_pages, archive=None):
    games = archive if archive is not None else _archive()
    if not games:
        return None
    rec = _records(games)
    end = _season_end_holders(belt_games)
    holders = {r["team"] for r in lineage["reigns"]}
    live = _in_progress_season()
    rows, n, best_n, unbeaten_n = [], 0, 0, 0
    for y in sorted(end, reverse=True):
        if y == live or y not in rec:
            continue
        teams = rec[y]
        most = max(sum(r) for r in teams.values())
        pool = {t: r for t, r in teams.items() if sum(r) >= max(3, most * 0.6)}
        if not pool:
            continue
        top = max(_pct(r) for r in pool.values())
        best = sorted(t for t, r in pool.items() if _pct(r) == top)
        h = end[y]
        hr = teams.get(h)
        n += 1
        is_best = h in best
        best_n += is_best
        unbeaten = bool(hr) and hr[1] == 0
        unbeaten_n += unbeaten
        best_txt = ", ".join(B.team_link(t, "", holders) for t in best[:3]) + (f" +{len(best) - 3}" if len(best) > 3 else "")
        rows.append(f'<tr><td class="num">{_season_link(y, seasons_with_pages)}</td><td>{B.team_link(h, "", holders)}</td>'
                    f'<td class="num">{_wlt(hr) if hr else "&mdash;"}</td><td>{best_txt} <small>({_wlt(pool[best[0]])})</small></td>'
                    f'<td>{"Yes" if is_best else ""}</td></tr>')
    if not n:
        return None
    body = _stats([(f"{best_n} of {n}", "Seasons the holder at season&rsquo;s end had the best record"),
                   (unbeaten_n, "Seasons it ended with an unbeaten holder"),
                   (f"{best_n / n * 100:.0f}%", "Belt and best record agree")])
    body += '<div class="sectionHead"><span class="tag">Every season</span><h2>Who ended with the belt, and who had the best record</h2></div>'
    body += _table([("Season", False), ("Belt at season&rsquo;s end", False), ("Their record", True), ("Best record", False), ("Same team?", False)], rows, 640)
    body += ('<p class="noteBox">Records count the games the belt itself could count: every game on record through 1977, and from the 1978 '
             'split on, games between two FBS (I-A) teams. &ldquo;Best record&rdquo; is the best winning percentage among teams that played at '
             'least 60% as many of those games as the busiest team that season (ties count half). The season in progress is left out.</p>')
    return _shell(B, "standings", "The Belt vs. the Standings — Best Record by Season",
                  f"Did the College Football Belt holder have the season's best record? {best_n} of {n} seasons, from 1869 to last season.",
                  "Standings", "The belt vs. the standings",
                  "The belt rewards beating the right team on the right day. The standings reward beating everyone. How often they finish in the same place:",
                  body, f"{best_n} of {n} seasons")


# --------------------------------------------------------------- search --

SEARCH_JS = r"""<script>
(function(){
  var q = new URLSearchParams(location.search).get('q') || '', input = document.getElementById('sq'),
      out = document.getElementById('sres'), more = document.getElementById('smore'), idx = null;
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function fold(s){ return s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, ''); }
  function run(){
    var v = fold(input.value.trim());
    more.innerHTML = v ? '<a href="all-games.html?q=' + encodeURIComponent(input.value.trim()) + '">Every belt game matching &ldquo;' + esc(input.value.trim()) + '&rdquo; &rarr;</a>' : '';
    if (!v || !idx) { out.innerHTML = ''; return; }
    var hits = [];
    idx.forEach(function(it){
      var n = fold(it.n), s = -1;
      if (n === v) s = 0; else if (n.indexOf(v) === 0) s = 1; else if (n.indexOf(' ' + v) !== -1) s = 2; else if (n.indexOf(v) !== -1) s = 3;
      else if (it.k && fold(it.k).indexOf(v) !== -1) s = 4;
      if (s >= 0) hits.push([s, it]);
    });
    hits.sort(function(a, b){ return a[0] - b[0] || a[1].n.localeCompare(b[1].n); });
    out.innerHTML = hits.length ? hits.slice(0, 60).map(function(h){ var it = h[1];
      return '<a class="recordRow" href="' + esc(it.u) + '"><span class="recordMain">' + esc(it.n) + '</span><span class="recordValue">' + esc(it.t || '') + '</span></a>'; }).join('')
      : '<p class="emptyNote">Nothing by that name. Try a school, a year or a conference.</p>';
  }
  input.value = q;
  input.addEventListener('input', function(){ history.replaceState(null, '', '?q=' + encodeURIComponent(input.value.trim())); run(); });
  fetch('search-index.json').then(function(r){ return r.json(); }).then(function(j){ idx = j; run(); }).catch(function(){});
  run();
})();
</script>"""


def search_page(B):
    body = ('<form class="searchPage" role="search" onsubmit="return false"><label class="srOnly" for="sq">Search</label>'
            '<input id="sq" type="search" placeholder="A school, a year, a conference" autofocus '
            'style="width:100%;font:inherit;font-size:1.1rem;padding:.7em .9em;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink)"></form>'
            '<p id="smore" class="noteBox" style="margin:14px 0"></p><div class="recordList" id="sres"></div>' + SEARCH_JS)
    return f'''{B.page_head("Search the College Football Belt", "Find any program, season, conference belt or page on the College Football Belt.", "", '<meta name="robots" content="noindex">')}

{B.site_header('', 'search')}

<main class="wrap">
  {B.page_intro("Search", "Search the belt", "Every program that has held the belt, every season since 1869, every conference belt and every page on the site.")}
{body}
</main>

{B.site_footer('', '')}
'''


def build(B, lineage, belt_games, out_dir, seasons_with_pages=()):
    """Write the four pages; returns the file names written (for the sitemap)."""
    swp = set(seasons_with_pages)
    made = []
    for name, html in (("champions.html", champions_page(B, lineage, belt_games, swp)),
                       ("postseason.html", postseason_page(B, lineage, belt_games, swp)),
                       ("splits.html", splits_page(B, lineage, belt_games, os.path.exists(os.path.join(out_dir, "story-overtime.html")))),
                       ("standings.html", standings_page(B, lineage, belt_games, swp)),
                       ("search.html", search_page(B))):
        if not html:
            continue
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(html)
        made.append(name)
    return made
