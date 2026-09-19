# College Football Belt — project folder

Status as of 2026-09-14, tracking the brief's build order:

1. ✅ **Lineage built and verified.** `build_lineage.py` ran successfully;
   `belt_data/lineage.json` matches the reference site (328 reigns, 101
   teams, 1,633 belt games; Notre Dame holding since 2025-11-29, 2 defenses).
2. ✅ **Ruleset locked and written up** — see `ruleset.md`. One item is still
   open (see below).
3. ✅ **Team colors done — all 101 teams have a usable color.** CFBD's team
   database is more complete than expected; even early-era clubs like
   Carlisle and the Olympic Club are in there. `belt_data/team_colors.json`
   is complete, with one data-quality fix applied and the 4 remaining gaps
   filled by hand (see below — 2 sourced, 2 flagged as placeholders).
4. ✅ **Site build done — homepage, all 1,633 game pages, and the ruleset
   page are all real and data-driven.** `build_site.py` generates the whole
   thing from `belt_data/*.json` + `ruleset.md` in one run (below).
5. ✅ **Weekly updates: one command, and now cheap.** `update_all.py` runs
   the whole pipeline (lineage → team colors → box scores → site) in
   order. Still manual to *run* — you (or a Windows scheduled task) have
   to trigger it — but it's one command instead of four, and (see
   "CFBD's call budget" below) a normal run is now ~15-30 CFBD calls, not
   ~600+.
6. ✅ **Deployment: GitHub Actions + GitHub Pages, live.** The repo, Pages,
   and secrets are set up and the workflow has run successfully end to
   end, including the AI-written upcoming-game preview.
7. ✅ **Past the original brief: AI game recaps, a Records page, an "On
   This Day" widget, per-team pages, a map of everywhere the belt has
   lived, and an auto-generated social share image.** All computed from
   data already on hand except the recaps (one Claude call per historical
   game, a well-under-$10 one-time backfill — see "Game recaps" below) and
   the map (real state outlines, no new API cost — see "The map" below).
8. ✅ **Live on collegefootballbelt.com, with real HTTPS.** DNS points at
   GitHub Pages (see "Point your domain at it" below) and GitHub's
   auto-issued certificate is active — no separate hosting to pay for.
9. ✅ **Discoverability, a way to follow along, and a forecast + prediction
   for the upcoming game.** `sitemap.xml` + `robots.txt` so search engines
   can actually find the site's 1,600+ pages, a real favicon, a custom 404
   page, an RSS feed of belt changes (`feed.xml`), optional free
   privacy-friendly analytics (GoatCounter), and a kickoff-weather forecast
   + AI-predicted winner on the upcoming-game preview page. See "SEO,
   analytics, and the RSS feed" and "Weather + prediction for the upcoming
   game" below.
10. ✅ **A second expansion pass: three more Records boards, a full "On
    This Day" page, team logos, kickoff time in your own timezone, and a
    downloadable poster for every team's belt history.** Again, all
    computed from data already on hand — no new fetches, no new API calls.
    See "Records, On This Day, and team pages" and "The share image"
    below.

## Team colors: a data quirk, already fixed

Your first `team_colors.json` had every team "matched", but 11 of them had
the literal string `"#null"` as their color or alternate color instead of a
real value — that's how CFBD represents "we don't actually have this" for
some smaller/historic programs, rather than leaving the field out. Used
as-is, that string would have rendered as an invalid CSS color. I fixed
`fetch_team_colors.py` to normalize that placeholder to a real `null`, and
cleaned your existing `team_colors.json` the same way (no rerun needed —
already updated in this folder). If you rerun the script later it'll do
this automatically, and its summary now separately flags anyone still
missing a **primary** color (the one the color-shift feature actually
needs) versus just an alternate.

**The 4 teams with no CFBD color data are now filled in** (each entry also
carries a new `color_note` field explaining where the color came from):

- **Swarthmore** — `#a11833` / white. Sourced: Swarthmore's current official
  Garnet/White colors, and matches "Garnet Tide," this team's own mascot in
  the data. High confidence.
- **Saint Mary's (CA)** — `#d80024` / `#06315b`. Sourced: the college's
  current official red/blue. Moderate-high confidence — the school still
  exists, though this football program doesn't.
- **Carlisle** — `#8a6d00` / `#a32638`. **Unsourced placeholder** — no
  citation for this program's actual colors turned up (checked Wikipedia
  and the Carlisle Indian School Digital Resource Center). Treat as a guess
  to replace, not a fact to publish.
- **Olympic Club** — `#5b5b5b` / white. **Unsourced placeholder** — a
  private athletic club, not a school; no color identity found. Neutral
  gray as a stand-in.

Two of these are real, cited colors; two are flagged placeholders that
still need a genuine source (or a deliberate decision) before publishing.

**7 more teams are missing only an alternate color** (Carnegie Mellon,
Chicago, Geneva, Grove City, Muhlenberg, Washington & Jefferson, West
Virginia Wesleyan) — lower priority, since the color-shift feature is
described as keying off the current holder's primary color.

## ruleset.md

The full published ruleset, written from the decisions in the project
brief plus what running the actual data confirmed (e.g., non-FBS opponents
already flow through correctly because the CFBD pull isn't filtered to any
one classification — confirmed against CFBD's own API docs, not just
assumed). This is meant to go on the site close to as-is.

One item is still open in it: cross-checking the 1869 origin point against
the pre-2018 collegefootballbelt.com site via the Wayback Machine. I
attempted this and the Internet Archive was reporting a full outage at the
time (not a blocked request) — worth retrying later, but nothing in the
current build depends on it.

## Earlier history (build_lineage.py)

`build_lineage.py` needed two fixes before its dates were fully correct —
both already applied, no action needed:

- CFBD gives kickoff times in UTC; a late West Coast game can fall on the
  wrong side of midnight UTC if you just truncate the timestamp. The script
  now converts to the game venue's local time first.
- The venue's local time is derived from its latitude/longitude (via the
  `timezonefinder` package) rather than CFBD's own `timezone` field on a
  venue, which turns out to be null for most venues in practice.

If you ever delete `belt_data/` and rerun from scratch, you'll need
`pip install tzdata timezonefinder` again first (Windows Python doesn't
ship the IANA timezone database these rely on, and `pip` itself may need to
be invoked as `python -m pip install ...` if it's not on PATH — both were
true on this machine).

## CFBD's call budget: incremental fetching (`historical_data/`)

This is the fix behind the first GitHub Actions runs failing — worth
understanding since it shapes how `build_lineage.py` and
`fetch_game_details.py` both work now.

**The real cause wasn't a burst rate limit.** CFBD's free tier is capped
at **1,000 calls/month** (see
[collegefootballdata.com/api-tiers](https://collegefootballdata.com/api-tiers)),
not a short per-minute window. A full 1869-now history pull is ~316 calls
(158+ years × 2 season types), and pulling every 2003+ belt game's box
score is ~310 more — so the original design (full refetch, every single
run, because GitHub's runners never keep `belt_data/` between runs)
burned through the monthly budget in a small handful of runs. More patient
retry backoff — the first fix I tried — doesn't help when the real problem
is "no calls left this month," not "too many calls this second."

**The actual fix: stop refetching settled history.** 1869-2002-ish never
changes — final scores are permanent — so there's no reason to ever
refetch a season once it's fully concluded. Both scripts now keep a
small, **git-committed** baseline in `historical_data/` (a few hundred KB,
not the ~90MB of raw game data behind it) and, each run, freshly fetch
only the **current + previous season**:

- `historical_data/baseline.json` — the already-walked chain of custody
  (closed reigns + their belt games) through the end of whatever seasons
  are safely done. `build_lineage.py` resumes from this and only pulls
  ~2 seasons fresh (~4-6 calls) instead of the whole history.
- `historical_data/team_stats.json` — settled belt games' box scores.
  `fetch_game_details.py` resumes from this and only pulls the current +
  previous season's belt games' stats (~10-20 calls) instead of every
  2003+ belt game.

Both files update (and, once committed, the GitHub Actions workflow pushes
that update back to the repo) automatically as a season ages out of the
"current + previous" window — no manual maintenance. A normal run is now
**~15-30 CFBD calls total**, comfortably inside the free tier even on a
daily schedule.

**`--full-refetch`** (on either script) ignores the baseline and redoes
the whole thing from scratch — useful for a genuine full rebuild (a
tie-rule change, a suspected data issue) or to bootstrap the baseline the
first time. This repo's `historical_data/` is already seeded from your own
verified local run, so a fresh clone never needs to do this.

I verified the incremental math against your real, already-confirmed data
before shipping it: split the real 1,633-belt-game history at a dozen
different season cutoffs, fed the "live" tail back through the walk logic
in resume mode, and confirmed it reproduces the exact same belt games and
reigns as the original full computation, byte for byte, at every cutoff —
plus a synthetic multi-season simulation (with ties) comparing a full
bootstrap run against an incremental one two different ways. Both matched
exactly.

## Games CFBD doesn't have (`historical_data/supplemental_games.json`)

CFBD's pre-war record has holes, mostly club and service teams. When a
missing game changes who held the belt, it goes in
`historical_data/supplemental_games.json` with its date, score and
newspaper citations, using an id in the reserved 90,000,000-99,999,999
range (never a CFBD id, never an ESPN box-score link). The rule itself is
public in `ruleset.md` ("Games missing from the data source").

- `supplemental_games.py` validates the file and merges its games into
  the game stream at read time: `build_lineage.py` (fetched seasons only,
  so a `--full-refetch` includes them) and `build_alternate_lineages.py`.
  They are never written into `all_games.json.gz`, so an archive
  `--bootstrap` can't lose them. If CFBD later adds the same game, CFBD's
  copy wins and a note says the entry can be deleted.
- `apply_supplemental_games.py` folds them into `baseline.json`. It
  re-walks the archive from 1869, refuses to write unless the archive alone
  reproduces the current baseline exactly, prints every changed belt game
  and reign (`--dry-run` to preview), and drops cached historical notes for
  games whose belt outcome changed so they're rewritten. No CFBD calls.
  Re-run it whenever the JSON file changes.
- `build_site.py` shows the citations in a supplemental game's Sources
  strip in place of the CFBD attribution.
- `test_supplemental_games.py` checks the file, the merge, that archive +
  supplemental reproduces `baseline.json`, and the 1931-32 chain.

First entries (2026-09-17): Loyola (CA) 13, Olympic Club 0 on Nov. 21,
1931, reported by Ray of
[rutgersstartedthis.com](https://rutgersstartedthis.com), plus the five
other missing games the belt went through before Stanford won it on Oct. 15, 1932. Four reigns were
inserted after #65, so every reign from #66 on moved up by four.

## Box scores and recaps for individual games

You asked whether past belt games could show a box score and/or a summary.
Short answer: yes, with a real limit worth knowing up front.

CFBD's per-team stat lines (yards, turnovers, third-down efficiency, time
of possession, etc.) and quarter-by-quarter scoring both only exist from
roughly **2003 onward** — checked directly against `games_raw.json`
already in this folder, not just assumed: 0% of games before 2001 have a
line score, essentially 100% do from 2003 on. Applied to the actual belt
history, that means **310 of the 1,633 belt games (19%)** — the 2003-2026
stretch — can ever get a full box score. The other 81%, 1869-2002, will
only ever have the final score, because that's genuinely all CFBD has for
those eras. That's a fact about the data source, not something a rerun or
a different endpoint fixes — worth stating on the site itself rather than
quietly having some game pages richer than others with no explanation.

A written recap is a separate question: CFBD has no prose summaries to
pull, so that's either a short auto-generated blurb templated off the box
score (works for any of the 310 games with stats), or genuinely
hand-written recaps for a smaller, chosen set — a real editorial decision,
not a data question.

### Running fetch_game_details.py

New script, same pattern as before:

```powershell
cd "$env:USERPROFILE\Documents\college-football-belt"
$env:CFBD_API_KEY = "your-key-here"
python fetch_game_details.py
```

It reads the line scores straight out of your existing `games_raw.json`
(no extra API calls needed for those). For full team box-score stats, it
now fetches only the current + previous season's belt games each run (see
"CFBD's call budget" above) — a handful of calls, not ~310 — resuming from
the git-committed `historical_data/team_stats.json` for everything
already settled. Writes `belt_data/game_details.json`, one entry per belt
game keyed by CFBD's game id, and prints exactly how many games ended up
with a line score vs. a full box score, so you can confirm the ~19% figure
above rather than take it on faith.

**Fixed two bugs, in order.** Your first run hit CFBD's `HTTP 429 Too Many
Requests` after ~50-60 calls; the script's retry logic gave up too fast
(topping out around 4 seconds of backoff) and only saved progress once,
at the very end, so the crash threw away everything already fetched. That
got a patient backoff (5s → 10s → 20s → 40s → 60s, capped, over 8 tries)
and per-game incremental saving. That fix alone wasn't enough once this
ran in GitHub Actions, though: a fresh runner never has anything cached,
so *every* CI run still refetched all ~310 games — CFBD's actual limit
turned out to be a hard 1,000 calls/month, not a short burst, so repeated
full refetches (across this script and build_lineage.py both) exhausted
it outright. The real fix is the incremental design above.

Just rerun the command above with the updated script; no need to delete
anything first.

The real Notre Dame–Stanford box score numbers are now wired into the game
detail mockup (see chat) — no more placeholders there.

## Turning the mockup into all 1,633 real pages: build_site.py

New script, and the one that actually turns the single hand-built mockup
into the real site: it reads `belt_data/lineage.json`,
`belt_data/game_details.json`, and `belt_data/team_colors.json` and writes
one real HTML page per belt game, plus a shared stylesheet so the CSS
isn't repeated 1,633 times over.

```powershell
cd "$env:USERPROFILE\Documents\college-football-belt"
python build_site.py
```

No API key, no network calls — everything it needs is already in
`belt_data/` from the earlier scripts. Output goes to `site/`:

- `site/styles.css` — the shared stylesheet (fonts, layout, every
  component style), linked from every page instead of copy-pasted into
  each one.
- `site/games/<game_id>.html` — one page per belt game, 1,633 total. Each
  page's headline, score, line score (when available), and full box score
  (when available, 2003+ only) are all generated from the real data. When
  a game has no line score or no team stats, that section just doesn't
  render, rather than showing something fake.

A couple of judgment calls worth knowing about since there's no way to
hand-tune 1,633 pages individually:

- **Team colors, picked for legibility automatically.** The homepage
  mockup's color-shift idea only got hand-verified for one team pairing
  (Notre Dame/Stanford). With 101 real teams — some pale gold, some pure
  black, some with washed-out "alternate" colors — hand-picking text
  colors for each one isn't realistic, so `build_site.py` computes them:
  WCAG relative luminance/contrast ratio decides, per team per page,
  whether white or black text sits on that team's color, and whether the
  team's own accent color is legible enough to use for the score digits
  (falling back to plain white/black when it isn't). I spot-checked this
  algorithm against some deliberately hard cases — a bright gold primary
  color (Southern Miss), a black-on-black pairing (Army), an unsourced
  placeholder color (Carlisle) — before trusting it across all 101 teams.
- **No venue info.** `lineage.json` doesn't carry a stadium name or city —
  that would need a much bigger pull from CFBD's raw games data (~90MB) —
  so pages show home/away and a "Neutral site" tag when relevant instead
  of a venue line. Easy to add later if you want it.

## The real homepage: also build_site.py

`build_site.py` now writes `site/index.html` too — a real, data-driven
homepage replacing the static `site-mockup.html`, built the same way as
the game pages. Same command as above regenerates everything (games +
homepage + stylesheet) in one run.

What's real on it now: the current holder's plate (colors picked the same
contrast-safe way as the game pages), days held and defenses (computed
from today's date, so these drift correctly — rerun the script and they
update), the "chain of custody" strip (the actual last 7 reigns, pulled
live from `lineage.json`, each one linking to its real title-winning game
page), and the by-the-numbers band (1,633 / 328 / 101, plus years since
1869 computed from today's date rather than hand-typed).

Two things worth knowing:

- **Team abbreviations in the chain-of-custody chips are auto-generated,**
  not real school abbreviations — there's no data source for those across
  101 programs, so it's initials for multi-word names (Notre Dame → ND)
  and first-four-letters otherwise (Stanford → STAN). Close to the real
  thing in most cases, occasionally a little off (California → CALI
  instead of Cal). Cosmetic only.
- **No "next game" info on the plate** — the mockup had one, but there's
  no schedule data in this project yet (only played games), so I left it
  out rather than invent an opponent. Would need a small new fetch script
  against CFBD's schedule for a future team's upcoming game.

## The ruleset page: also build_site.py

`build_site.py` now writes `site/ruleset.html` too, generated straight
from `ruleset.md` — so that file stays the one place you ever edit the
actual rules; rerun the script and the page picks up any change
automatically instead of needing its prose copy-pasted a second place.
It's a small purpose-built markdown reader (not a general parser), so it
only understands this one file's shape: a title, `## ` section headings,
plain paragraphs, `- ` bullet lists (including ones that wrap across
lines), and whole-paragraph `*asides*` for the status/rationale notes,
which render the same way the "sourced" callouts do on the game pages.
Each rule gets a numbered tag ("Rule 01", "Rule 02", …); the "Open items"
section gets "Status" instead, since it isn't a rule.

## Upcoming-game preview: fetch_matchup_preview.py + generate_ai_preview.py

The homepage shows an "Up Next" chip for the current belt holder's next
scheduled game (mined for free out of data `build_lineage.py` already
fetches — CFBD returns the whole season including games it hasn't scored
yet, previously thrown away). Right below it, a "Belt Watch" strip shows
the *next two games after that* the same way — `build_lineage.py`'s
`find_upcoming_games()` just takes the same free schedule scan a bit
further (up to 3 games instead of 1), writing `belt_data/upcoming_games.json`
alongside the existing `next_game.json`. Zero extra API calls either way.

Clicking the "Up Next" chip goes to `site/preview.html`, which adds:

- **Recent form** — each team's last 5 completed games, win/loss and
  score. Also free — same already-fetched season data, just for both
  teams instead of only the belt holder.
- **All-time head-to-head** — the full series record and last several
  meetings between the two teams, from CFBD's `/teams/matchup` endpoint
  (`fetch_matchup_preview.py`, 1 API call, skipped entirely when there's
  no upcoming game).
- **An AI-written preview** (optional) — a short overview, 2-3 key
  matchups, and a betting-angles paragraph, written by the Claude API
  from the stats above (`generate_ai_preview.py`). Needs an
  `ANTHROPIC_API_KEY` (see the deployment section below; this same key
  also powers the game recaps described next); without one, this section
  just doesn't appear — everything else on the page still does.

The AI preview is deliberately cached to `ai_preview_cache/` (committed
to git, same reasoning as `historical_data/`): regenerating it costs a
real, if small, amount of money, and the pipeline can run more than once
before the actual matchup changes. A fresh Claude call only happens when
the two teams and date no longer match what's already cached.

## Game recaps: fetch_game_plays.py + generate_recaps.py

This answers the open question left in "Box scores and recaps for
individual games" above — every belt game since 2003 now gets a real,
specific AI-written recap instead of a generic templated blurb, because it
has real play-by-play to write from, not just box-score totals.

`fetch_game_plays.py` is a new incremental fetcher, same committed-cache
pattern as `fetch_game_details.py`, against CFBD's `/plays` endpoint (one
quirk worth knowing: unlike the box-score endpoints, `/plays` doesn't take
a game id directly — it's queried by season + week + team, one call per
belt game, same cost as the other per-game fetches). It doesn't keep every
play, only the ones that actually matter for a recap: any scoring play,
any turnover, or any play that gained at least 20 yards. That keeps both
the committed cache (`historical_data/game_plays.json`) and the AI prompt
a reasonable size. These also render directly on each game's own page now,
as a sourced "Key Plays" table — no AI involved, just CFBD's own
play-by-play, the same way the box score above it is sourced.

`generate_recaps.py` writes the actual prose: 3-5 sentences plus 2-4 named
"key moments," referencing specific plays from the list above rather than
just the final score, for every belt game that has a box score on file.
Same optional/cached design as `generate_ai_preview.py` — no
`ANTHROPIC_API_KEY` means no recaps, just the sourced data everyone
already had; a committed `recap_cache/recaps.json` means each game is only
ever generated once, no matter how many times the pipeline reruns.

**The one-time cost.** The first time this runs with a key set, it
backfills every eligible historical belt game at once — as of this
writing, that's **297 belt games since 2003, 284 of which have a full box
score** and are eligible (a few 2003+ games are missing stats in CFBD's
own data — a coverage gap, not a bug here). At `claude-haiku-4-5`'s
pricing (\$1/MTok in, \$5/MTok out) and roughly 500-900 tokens per game
including the notable-plays list, that backfill runs **well under \$10,
one time** — after that, it's one new recap a week at most, a fraction of
a cent each, same as the upcoming-game preview. `update_all.py` paces
these calls (a short delay between each) and saves the cache every 10
games, so a rate limit or an interrupted run never loses progress already
made — just rerun the same command and it picks up where it left off.

## Records, On This Day, and team pages: all just build_site.py

Three more pages, all computed straight from data already sitting in
`belt_data/lineage.json` — no new fetch script, no new API calls, nothing
that can go stale independently of the lineage itself:

- **`site/records.html`** — seven top-5 boards: the original four (Longest
  Reigns, Most Reigns, Most Defended, Biggest Blowouts) plus three "closest
  calls" boards — Narrowest Defenses (the closest the holder's ever come to
  losing it and didn't, ties included), Narrowest Upsets (the belt
  changing hands by the smallest possible margin), and Biggest Upsets (the
  belt changing hands in the most lopsided rout). Each is a plain sort over
  the existing reign/game data. Ties aren't broken, same "no editorial
  judgment" approach as everything else here.
- **The homepage's "On This Day" widget**, plus its own standalone
  **`site/on-this-day.html`** page linked from it — every belt game, any
  year, that happened on a given month/day, with a tag for whether it was a
  title change. The full page adds a month/day picker (so you can browse
  any date in belt history, not just today) and a "Jump to today" button;
  filtering is entirely client-side against the *visitor's* local date —
  same one-page-many-rows-toggled-by-a-CSS-class pattern as the Full
  History/All Games search boxes, just filtering by date instead of text.
- **`site/teams/<slug>.html`** — one page per program that's ever held the
  belt (currently 101), every reign it ever had, how each one started and
  (if over) ended, linked from that team's name wherever it appears
  site-wide. A team that's only ever challenged and lost doesn't get a
  page — it has no reigns to list. Each team page also links a downloadable
  belt-history poster (see below).

Team logos (from the same CFBD `/teams` call `fetch_team_colors.py` already
makes for colors — `logos[0]`, zero extra cost) now render next to team
names on game-page scoreboards, team-page headers, and the preview page's
recent-form columns, via a shared `team_logo()` / `logo_img()` helper in
`build_site.py`. Logos load straight from CFBD's CDN with
`onerror="this.remove()"`, so a team with no logo on file just shows its
name with no broken-image icon.

On the preview page, the upcoming game's kickoff time is also converted to
the *visitor's* own local timezone client-side (`Intl.DateTimeFormat`, no
geolocation, no server-side lookup) and shown under the matchup header —
useful since the site itself has no visitor timezone to build the page
around.

## The map: fetch_team_colors.py (state) + build_site.py + historical_data/us_state_shapes.json

`site/map.html` shades every US state by how many belt reigns have
started there, with a legend listing which team(s) and how many times.
Getting this right (real state outlines, not a guessed tile-grid) took two
things:

1. **Each team's home state**, added to `team_colors.json` by
   `fetch_team_colors.py` — free, since CFBD's `/teams` endpoint already
   returns a `location.state` field in the same call that fetches colors,
   nothing extra to request.
2. **Real state boundary shapes.** These come from `historical_data/us_state_shapes.json`,
   a committed, static (never refetched) file — see the note in
   `.gitignore` for why it lives there instead of being treated as a
   growing cache like the rest of `historical_data/`. It was extracted
   once from the `bokeh_sampledata` package's US Census-derived state
   outlines and isn't a runtime dependency at all — `build_site.py` just
   reads the plain JSON and projects it (a standard Albers Equal-Area
   Conic, the same projection/parameters behind EPSG:5070, implemented
   directly in `build_site.py` with nothing but the standard library's
   `math` module) into an inline SVG `<path>` per state. No image library,
   no headless browser, no network call at build time — Alaska and Hawaii
   are left out entirely rather than inset, since no belt-holding program
   has ever been based in either.

## The share image: generate_share_image.py

`site/share.png` is the image link previews show when someone shares
`collegefootballbelt.com` — a 1200×630 image (the standard Open Graph /
Twitter Card size) with the current holder's name over a gradient of
their own team colors, plus how long they've held the belt. Rendered with
[Pillow](https://pypi.org/project/pillow/) (a plain image-drawing library,
the one new dependency this adds to `requirements.txt`) straight from
`belt_data/lineage.json` + `team_colors.json` — no network call, no
headless browser, and it regenerates every run so it's always current.
`build_site.py`'s homepage template points `og:image` / `twitter:image` at
it with an absolute URL (social platforms fetch these server-side, so a
relative path wouldn't resolve); other pages don't get their own share
image yet, just the homepage.

Font handling is deliberately defensive: it looks for DejaVu/Liberation
TrueType fonts at their usual Linux paths (present on GitHub Actions'
`ubuntu-latest` runner and in most dev environments) and falls back to
Pillow's own built-in font if none are found, so a missing font file can
never fail the build — worst case, a plainer-looking image still gets
made.

Also in this same script: `site/favicon.png` (32×32) and
`site/apple-touch-icon.png` (180×180), a simple belt-buckle glyph drawn
with Pillow's shape primitives (no image asset needed), and a downloadable
**belt-history poster** — `site/posters/<slug>.png`, one per program,
linked from that team's own page — a portrait (1200px wide) timeline of
every reign that team's ever had, newest first, in the team's own colors,
capped at the 9 most recent reigns with a "+N earlier reigns" note below
for a team with more than that (USC, at 13, is the current max). A team
with only one short reign doesn't get a mostly-empty poster: the canvas
height is computed from how many rows actually get drawn (measured on a
throwaway 1×1 surface before the real image is created), floored at a
minimum height rather than fixed at one tall size for every team.

## SEO, analytics, and the RSS feed

All four of these are `build_site.py` writing plain files into `site/` —
no new dependencies, no accounts required to get the first three:

- **`sitemap.xml`** lists every real page on the site (home, the full
  history/all-games/records/ruleset/map pages, every team page, every one
  of the 1,600+ game pages) so search engines can actually find them all,
  instead of only ever discovering whatever's linked from the homepage.
  **`robots.txt`** just points at it.
- **`favicon.png` / `apple-touch-icon.png`** are a small belt-and-buckle
  glyph (drawn with Pillow, same tool as the share image) in the *current
  holder's* team colors — regenerated by `generate_share_image.py` every
  run, so it stays on-brand as the belt changes hands.
- **`404.html`** replaces GitHub's generic "404" with something that
  actually looks like the site and links back to the homepage.
- **`feed.xml`** is a standard RSS 2.0 feed of belt *changes* (not every
  defense — just the ~327 times it's actually changed hands), newest
  first. Point any RSS reader, or an RSS-to-email service like
  [Blogtrottr](https://blogtrottr.com/), at
  `https://collegefootballbelt.com/feed.xml` to get notified the moment
  the belt changes hands without checking the site.

**Analytics are opt-in and need a one-time account you set up yourself** —
`build_site.py` can't sign up for anything on your behalf. This project
uses [GoatCounter](https://www.goatcounter.com/) (free, privacy-friendly,
no cookie banner needed): create a free account, pick a site code (you'll
be assigned `your-code.goatcounter.com`), then open `build_site.py` and
set:

```python
GOATCOUNTER_CODE = "your-code"   # near the top of the file, empty by default
```

Leave it blank and every page just quietly omits the tracking snippet —
same no-op-when-unset pattern as the `ANTHROPIC_API_KEY` features below.

## Weather + prediction for the upcoming game

The "Up Next" preview page (`site/preview.html`) now shows a kickoff
forecast and an AI-predicted winner, in addition to the recent-form/
head-to-head stats it already had:

- **`fetch_weather.py`** fetches the forecast for the venue and kickoff
  hour of the belt holder's next game from
  [Open-Meteo](https://open-meteo.com/) — a free weather API that needs no
  key and no signup at all. The venue's latitude/longitude comes for free
  out of `build_lineage.py`'s existing `/venues` call (added there
  alongside the timezone lookup it already did), so this makes zero extra
  CFBD calls. Skips itself cleanly (writes `weather.json = null`) when
  there's no upcoming game, no venue coordinates on file, or the game is
  more than ~15 days out (Open-Meteo's free forecast only covers about 16
  days ahead) — a later run picks it up automatically once it's in range.
- **`generate_ai_preview.py`** now also asks Claude for a predicted winner,
  a predicted score, and a short write-up explaining the pick — folding in
  the weather forecast when one's available, so a windy/cold/rainy kickoff
  can actually factor into the reasoning instead of being ignored. This
  reuses the same optional `ANTHROPIC_API_KEY` and committed cache
  (`ai_preview_cache/cache.json`) as the rest of the AI preview — no new
  cost beyond what that feature already had, since it's still one Claude
  call per matchup, not a separate call for the prediction.

Both are clearly labeled on the page as a for-fun editorial call, not
betting advice, with a responsible-gambling helpline note — same framing
as the existing "Betting Angles" text.

## Weekly updates: update_all.py

New script that chains the whole pipeline in the right order so you don't
have to run four commands by hand every week:

```powershell
cd "$env:USERPROFILE\Documents\college-football-belt"
$env:CFBD_API_KEY = "your-key-here"
python update_all.py
```

It runs, in order: `build_lineage.py`, `fetch_game_details.py`, and
`fetch_game_plays.py` (all three incremental — see "CFBD's call budget"
above, only the current + previous season gets refetched once the
historical backfill is done), `fetch_team_colors.py` (cheap regardless,
one call, also picks up each team's home state for the map),
`fetch_matchup_preview.py` (one call, for the upcoming game's head-to-head
record — see "Upcoming-game preview" above), `fetch_weather.py` (no CFBD
call, no key — the kickoff forecast, see "Weather + prediction" above),
then `generate_ai_preview.py` and `generate_recaps.py` (both optional —
Claude API calls, only for whatever isn't already cached), then
`build_site.py` (no network calls, regenerates every page including the
records/On This Day/team/map pages, plus `sitemap.xml`/`robots.txt`/
`404.html`/`feed.xml` — see "SEO, analytics, and the RSS feed" above), and
finally `generate_share_image.py` (no network call either, just renders
`site/share.png` and the favicon from whatever `build_site.py` just used).
A normal week-to-week run is ~15-30 CFBD calls total; the very first run
after adding the recap feature is a one-time exception (see "Game recaps"
above). If any stage fails, it stops right there instead of rebuilding the
site from a half-updated data set — fix whatever broke and rerun the same
command; every stage already knows how to resume from where it left off.

This makes "run one command" the whole weekly routine, but running that
command is still on you (or something you schedule) — it's not
automatic yet. If you want it fully hands-off, the standard way on
Windows is a Task Scheduler entry that runs the command above on a
schedule (e.g., weekly, or after Saturday's games finish). I didn't set
that up myself — creating a scheduled task is a change to your machine's
own settings, worth doing deliberately rather than as a side effect of a
script — but it's a short, well-documented `schtasks` setup if you want
to go that route later.

## Game day, live: the belt-game scoreboard, and where to watch

The homepage and `preview.html` carry a live score bar for the holder's
next game that the **visitor's browser** keeps current — the site itself
still only rebuilds on the pipeline's schedule, so this covers the hours
in between. From 45 minutes before kickoff it reads ESPN's public
scoreboard feed (the same `site.api.espn.com` / `sports.core.api.espn.com`
endpoints ESPN's own pages use; they allow cross-origin reads and need no
key): one `summary` call to identify the game and both teams, then a
handful of tiny `status` / `score` / `situation` reads every minute while
the game is live, with the belt's own framing — SAFE / IN DANGER / TIED,
possession, down and distance, the last play — and at the final either
"X takes the belt" or "X defends the belt, Nth defense" plus a note that
the site updates within a few hours. It holds the final for a day, makes
no request outside that window, pauses while the tab is hidden, and
degrades to nothing if ESPN is unreachable. `build_lineage.py` now writes
each upcoming game's CFBD id (the same number ESPN uses) into
`next_game.json` / `upcoming_games.json` for it; with no id on file the
bar falls back to searching that day's scoreboard by team name.
`fetch_gameday_status.py`'s hourly CFBD snapshot still prefills the bar
when a build runs mid-game.

Where to watch: `build_lineage.py` makes one `/games/media?year=` call
per season on the holder's schedule (normally one call) and stamps `tv`
("NBC"), `stream` ("Peacock") and `watch` (whichever is set) onto every
upcoming game, plus `start_time_tbd`; the whole season's listings go to
`belt_data/media.json`, which `fetch_belt_odds.py` joins to the schedule
page's games by id. The homepage card, the preview page's center panel,
the schedule page and the belt tree show the network and the kickoff in
Eastern time, restated in the visitor's own zone by a few lines of JS.

## Four more pages (2026-09-18): belt on any date, belt degrees, the belt's journey, the belt tree

- **`belt-on.html`** — pick any date since November 6, 1869 and see who
  held the belt that day: which reign, how deep into it, defenses so far,
  the belt games on either side, a line to share. Answered in the browser
  from the embedded chain of custody (the holder on any day is whoever won
  it most recently before then); the date lives in the URL hash
  (`#d=1988-10-16`) so a birthday can be linked.
- **`degrees.html`** — six degrees of the belt: every holder is joined to
  every program it has taken the belt from or lost it to, and the page
  finds the shortest chain of real changes of hands between any two (a
  breadth-first search over the embedded graph, each link the most recent
  game between the pair). Build-time boards: most connected, center of
  the web, busiest corridors, the farthest-apart pairs.
- **`journey.html`** — the belt's journey, campus to campus: every change
  of hands measured as the great-circle distance between the two
  programs' home fields, totaled (with laps around the Earth), the longest
  and shortest hops, miles by decade, the days-weighted center of gravity,
  and an animated replay of every hop on the same Albers map as
  `map.html`. Coordinates come from CFBD's `/teams` `location` (kept by
  `fetch_team_colors.py` as `lat` / `lon` / `city` in `team_colors.json`,
  same call as the colors); the four historic programs CFBD no longer
  tracks are placed by hand in `HISTORIC_CAMPUSES`, and anything else
  without coordinates falls back to its state's centroid and is flagged
  approximate.
- **`belt-tree.html`** — the next four belt games as an exact branching
  tree: at every node the holder keeps the belt (the branch follows its
  schedule) or the challenger takes it (the branch follows the
  challenger's), with the Elo win probability on each fork, computed by
  `fetch_belt_odds.py` (`BELT_TREE_DEPTH`, `season.belt_tree` in
  `belt_risk.json`). "Who holds the belt after k belt games" is the sum
  over the leaves, no sampling. The page is skipped (and left out of the
  nav) until the tree exists.

## The companion belts: FBS-only, FCS-only and one per conference

`build_conference_lineage.py` (stage 3) builds every companion belt --
`lineage_fbs.json`, `lineage_fcs.json` and `conferences/<slug>_lineage.json`
-- from scratch on every run, from the committed archive of every game
since 1869 (`historical_data/all_games.json.gz`) plus the run's own
`games_raw.json`. No API calls, no committed baselines or vacancy files,
nothing to bootstrap: ~105,000 games walk in a few seconds. Which games
count is decided game by game from the conference each side was in that
season (`classification.py`: a hand-kept conference -> FBS/FCS map with
era rules, pure renames folded together, dissolution dates for leagues
that folded). The FBS belt is the real belt through 1977 and FBS-vs-FBS
from 1978; the FCS belt starts with the data source's first real FCS
coverage (2003). Who belongs to each belt's world is read season by season
from the conference label on every game a team played
(`belt_engine.Membership`), and `belt_engine.resolve_vacancies_by_membership`
does the rest: a holder that is still a member keeps the belt through any
stretch without a qualifying game (an independent that goes years without
meeting another independent is still an independent), a holder that left
(realignment, a move between subdivisions, a program gone for good) vacates
it as of its last game as a member and the belt reverts to the most recent
earlier holder that is a member of the season it left for, and a belt
nobody can inherit retires with its last holder -- never a placeholder
reign. A silence across seasons the archive does not cover (FCS before
2003) retires the belt on the last game we can see and re-establishes it
with the next. The rules are written up in `ruleset.md` ("Companion
belts"); the rewrite followed a reader's (Elliot's) report of one-day
reigns, a missing North Dakota State and defunct conferences held "to the
present," and the membership rule followed a Reddit reader's catch that
the Independents belt had Notre Dame "leaving" twice.

## The Division I rule

`classification.division1_game(g)` decides whether a game can move the
belt at all: every game before the 1978 split (the pre-modern record
includes the club and service teams that held the belt in the 1930s), and
from 1978 on only a game between two Division I teams -- FBS or FCS that
season, by the data source's per-game classification when present, else
by the conference each side was in. A loss to a Division II/III/NAIA team
is not a belt game; the holder keeps the belt. The reason is coverage, not
philosophy: the data source has no schedules below Division I before
2021, so a belt that went there could not be followed (a Reddit reader
flipped Baylor-Wofford 2013 on the what-if page and watched the belt sit
with UNC Pembroke for eight years). `build_lineage.py` applies it to the
live seasons and to Belt Watch's upcoming games, `build_alternate_lineages.py`
to the what-if record and every universe; the real lineage has never met a
lower-division team since 1978, so nothing historical changed.

## Merch shop (Fourthwall)

`site/shop.html` is a native page in the site's own design — products
grouped by school, with a jump-to-team filter bar — that links out to the
Fourthwall store (`college-football-belt-shop.fourthwall.com`) only at the
final "buy" click. It is built from the store's own catalog, not from a
list kept in the code: **`fetch_shop_products.py`**, a no-key stage that
runs just before `build_site.py`, reads every public product off the
storefront (name, price, availability, current mockup — via the
storefront's `/sitemap.xml` and `/products/<slug>.js`) into
`historical_data/shop_catalog.json` (git-committed, so the last good
catalog survives a run where Fourthwall is unreachable) and renders a
600px WebP thumbnail of each mockup into `belt_data/shop_thumbs/`, which
`build_site.py` copies to `site/merch/`. Adding, hiding, repricing or
redesigning a product in the Fourthwall dashboard shows up on the next run
with no code change. `SHOP_ENABLED` near the top of `build_site.py` is the
master switch (`False` keeps the page out of the build, the nav, the
footer, search and the sitemap), and the team page of every school that
has gear links to its slice of the shop.

## Deployment: GitHub Actions + GitHub Pages

This solves weekly updates and hosting in one move, instead of two
separate things. Rather than relying on your own computer being on for
`update_all.py` to run, `.github/workflows/update-and-deploy.yml` runs
the *exact same pipeline* on GitHub's own servers — on a schedule (four
check-ins a week, aimed at catching the belt holder's own game shortly
after it ends rather than waiting until the next morning — see the
schedule comment at the top of the workflow file for the exact times and
why there are four instead of one), whenever you push a change, or on
demand from a button — and publishes the result straight to GitHub
Pages. No server to rent, no separate host account, and it's free for a
repo this size.

I set up the workflow file and a `requirements.txt`/`.gitignore` for it,
but I didn't create the GitHub repo, touch any account settings, or
change your domain's DNS myself — those are yours to do deliberately.
Here's the whole path, if you want to take it:

**1. Get the project into a GitHub repo.** If you don't already have
[git](https://git-scm.com/download/win) installed, grab it, then:

```powershell
cd "$env:USERPROFILE\Documents\college-football-belt"
git init
git add .
git commit -m "College Football Belt: lineage, site build, weekly-update pipeline"
git branch -M main
```

Create an empty repository on GitHub (github.com → New repository — don't
initialize it with a README, so it stays empty for your push), then:

```powershell
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

**2. Turn on Pages.** In the new repo: Settings → Pages → under "Build
and deployment", set Source to **GitHub Actions**. (Just the toggle —
the workflow file already in the repo handles the rest.)

**3. Add your API key as a secret.** Settings → Secrets and variables →
Actions → New repository secret → name it `CFBD_API_KEY`, value is your
real CollegeFootballData key. This is what lets the workflow fetch data
without your key ever being visible in the repo itself.

**3b. (Optional) Add an Anthropic key for the AI writing.** Same place,
another secret, name it `ANTHROPIC_API_KEY`, value is a key from
[console.anthropic.com](https://console.anthropic.com/settings/keys).
This powers both the AI-written preview (key matchups, betting angles) on
the upcoming-game page and the AI-written recap on every settled belt
game's own page. Skip this entirely if you don't want it —
`generate_ai_preview.py` and `generate_recaps.py` both detect the missing
key and just skip themselves; every other page on the site works fine
without it. When it *is* set: the preview is only regenerated when the
belt holder's next game actually changes, and each recap is only ever
generated once (see "Game recaps" above for the one-time backfill cost the
very first run) — see each script's docstring for how its cache works.

**4. Trigger the first run.** Actions tab → "Update and deploy" → Run
workflow. The very first run ever (no `historical_data/` baseline exists
yet) does a full history pull — ~10-20 minutes, mostly `build_lineage.py`
— and publishes the site. Every run after that is incremental (a couple
minutes, ~15-30 CFBD calls) and re-runs automatically four times a week
(see the schedule comment in the workflow file), on push, or on demand.
The workflow also has permission to commit
`historical_data/` back to the repo when a season ages into the frozen
baseline (rare — at most a few times a year), so the baseline stays
current without you doing anything.

*Status as of this writing:* the repo, Pages, and secret are all set up;
the first two automatic runs failed on the old full-refetch design (a real
429 rate-limit bug, then the deeper monthly-quota issue above) before this
incremental fix shipped. The next run should succeed and publish for the
first time — worth checking the Actions tab once it's gone through.

**5. Point your domain at it.** Once the first deploy succeeds, GitHub
gives you a working `https://<your-username>.github.io/<repo-name>/`
URL immediately — worth checking before touching DNS at all. For the
real domain: repo Settings → Pages → "Custom domain" → enter
`collegefootballbelt.com` → Save. Then at your domain registrar, add:

- Four `A` records for the apex domain (`@`), pointing at GitHub Pages'
  four IPs: `185.199.108.153`, `185.199.109.153`, `185.199.110.153`,
  `185.199.111.153`.
- One `CNAME` record for `www`, pointing at
  `<your-username>.github.io` (no `https://`, no trailing slash), if you
  want `www.collegefootballbelt.com` to work too.
- Remove/replace whatever `A`/`CNAME` records were already there for the
  apex domain (e.g. from a previous host) — leaving old ones in place
  alongside the new ones is what causes a domain to intermittently show
  the wrong site.

[GitHub's own docs](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site)
are the accurate source for registrar-specific steps rather than me
guessing your registrar's UI. DNS changes can take anywhere from a few
minutes to (rarely) a day to propagate; Settings → Pages shows a
checkmark once GitHub can see the correct records, and that's also where
"Enforce HTTPS" shows up (grey out until DNS verifies, then turn it on —
GitHub auto-provisions the certificate, nothing to buy).

One important gotcha specific to how this repo deploys: GitHub Pages
reads the custom domain from a `CNAME` file *in the published output*,
not only from the Settings UI — and since `site/` is wiped and rebuilt
from scratch every single run, a `CNAME` file placed there by hand would
get silently deleted the very next time the pipeline runs, quietly
breaking the custom domain until someone noticed. `build_site.py` writes
`site/CNAME` itself on every run for exactly this reason (see
`CUSTOM_DOMAIN` near the top of the file) — so once you've entered the
domain in Settings once, it stays configured through every future
automated deploy without you having to touch that setting again.

One nice property already true of the generated site: every link in it
(game pages, homepage, ruleset) is relative, not absolute — so it works
identically whether it's served from a domain root or from a GitHub
Pages project subpath. Nothing to adjust either way.

If you'd rather not deal with GitHub at all, everything from the earlier
conversation still holds too: `update_all.py` on your own machine (by
hand or via Windows Task Scheduler) plus any static host — Netlify,
Vercel, Cloudflare Pages — works exactly as well. GitHub Actions just
happens to fold the "where does it run" question and the "where does it
live" question into the same free answer.

## Automatic X posts: post_to_x.py (+ post_on_this_day_to_x.py)

Everything @CollegeFBBelt posts on X is automated, and all of it runs
inside the workflows above (nothing posts from your own computer). The
four X secrets from step 4 of the workflow comment are the only setup;
without them every posting script just prints a note and exits. What
goes out, and when:

- **Result posts** — one for every belt game, change *or* successful
  defense, from whichever `update-and-deploy.yml` run first sees the final
  score (the Saturday check-ins land within about four hours of any
  kickoff window). `social_cache/x_last_posted.json` remembers the last
  game posted, so nothing posts twice.
- **Friday preview + poll** — the Friday 2 PM ET run (`X_POST_PREVIEW`)
  posts the upcoming game with the site's prediction and defend odds,
  then a "does the belt stay put?" poll that runs until kickoff. Once per
  game, whatever else triggers a run that day.
- **Game-day post (10 AM ET)** — added 2026-09-19. On the morning of every
  belt game: "🏈 GAME DAY — the belt is on the line", the matchup (with
  AP/CFP ranks), kickoff time, TV and venue, what the holder is defending
  (which defense of which reign, how many days), the site's lean and
  defend odds, and the preview link — everything that fits in 280
  characters, dropping the kickoff forecast, then the wordier stakes
  line, first. It comes from a daily 10 AM ET check-in the workflow runs
  all season (every day, because bowls and weeknight games land on any
  day), which also rebuilds the site with that morning's odds and TV
  listings. GitHub's cron can't follow daylight-saving time, so the
  workflow has both a 14:00 UTC and a 15:00 UTC line where the season
  needs them and `post_to_x.py` posts only from the one that is really
  10:00 AM Eastern on that date (`X_FIRED_CRON`). Once per game. To send
  it by hand — say the 10 AM run failed — Actions → "Update and deploy"
  → Run workflow → tick **post_gameday**: it posts only if today is the
  holder's game day and the post hasn't gone out. `test_gameday_post.py`
  covers the wording, the 280-character fitting and the DST tick logic
  with synthetic data (no `belt_data/` or network needed).
- **On This Day** — its own daily workflow (`post-on-this-day.yml`),
  reading the site's public `api/games.json`; see that file's comment.
- **Bio sync** — whenever the holder changes, the account bio's "Current
  champion" line follows (`sync_bio()` in `post_to_x.py`).

`post_to_instagram.py` mirrors the result and Friday preview posts to
Instagram with the same wording (its own `social_cache/ig_last_posted.json`);
the game-day post is X-only for now.
