# College Football Belt — Project Brief

Handoff document for collegefootballbelt.com. Prepared 2026-09-13.
Attach this to a new desktop-linked session to pick up without re-deriving context.

## Goal

Revive collegefootballbelt.com as the reference tracker for college football's
lineal "belt" — the title that passes from the 1869 Rutgers–Princeton game
forward, changing hands whenever the holder loses.

## Current state of the domain

collegefootballbelt.com currently serves a **default WordPress installation** —
Twenty Seventeen theme, one "Hello world!" post dated October 5, 2018. Nothing
was ever built on it. Decide early whether to build on that WordPress or replace
it with a static site; the recommendation below assumes replacing it.

## Product direction (decided)

- **Depth and sourcing.** Every belt game cited and verifiable. The ruleset
  written out and published explicitly — none of the existing trackers do this,
  and it is the clearest available differentiator.
- **Live and social.** Previews of upcoming belt games ("who can take it this
  week"), recaps of prior ones where available, shareable output.
- **Visual.** Appealing and easy to use. **The site's color scheme shifts based
  on who currently holds the belt**, and possibly on the given week's matchup.
  This is a signature feature — it should be designed in from the start, which
  means the dataset needs team color data alongside the lineage.
- **Modest monetization**, eventually: ad sales, possibly merch for teams that
  have held the belt. Goal is covering hosting costs, not revenue.

## Competitive landscape

Three active trackers already exist. All use the same 1869 origin.

| Site | Notes |
|---|---|
| cfbbelt.com | "The Lineal Title Tracker." Minimal published rules. |
| cfb-belt.com | Publishes totals: 328 reigns, 101 teams, 1,636 belt games, 157 years. |
| hatchrankings.com/lineal | Table format, marks the "Incumbent." |

There is also an @cfb_belt account on X.

None of the three publishes a real ruleset covering edge cases. That gap plus
presentation quality is the opening.

## Current belt state (verified 2026-09-13)

- **Notre Dame holds the belt.** Took it beating Stanford 49–20 on 2025-11-29.
- Notre Dame missed the 2025 playoff and **opted out of a bowl game**, so the
  belt sat frozen through the entire offseason — no belt game between
  2025-11-29 and the 2026 opener.
- 2026 defenses so far: Wisconsin 41–13 (2026-09-06), Rice 52–0 (2026-09-12).
- **Next belt game: Michigan State.**

## Ruleset — decisions still needed

The chain is deterministic once these are fixed. Write them down before
computing, and publish them on the site.

1. **Origin.** 1869-11-06, Rutgers 6–4 Princeton. Rutgers is the first holder.
   (Confirm against the original site if a Wayback snapshot can be recovered.)
2. **Ties.** Pre-overtime college football had many. Standard lineal convention:
   **holder retains on a tie.** Needs an explicit decision.
3. **Holder doesn't play.** Belt carries over. Already exercised by the Notre
   Dame 2025 bowl opt-out.
4. **Cancelled/shortened seasons.** 1918 (influenza), WWII years. Belt carries.
5. **Forfeits and vacated wins.** Decide whether the belt follows the on-field
   result or the official record. Recommendation: **on-field result**, since the
   belt's whole premise is that it's won on the field. Document the choice.
6. **Non-FBS opponents.** If the holder loses to a lower-division team, does the
   belt go with it? Recommendation: yes — on-field result is the premise.
7. **Level of play in the early era.** Pre-1900 schedules include club and
   athletic-association teams. Decide the inclusion boundary.

## Data plan

The lineage is **computed, not researched**. Given every game in chronological
order, the chain follows mechanically. Do not hand-assemble it.

Source: the College Football Data API (api.collegefootballdata.com), which covers
1869 to present. Free tier requires an API key from collegefootballdata.com.

**This must be run from your own machine.** The cloud session's egress policy
blocks api.collegefootballdata.com, archive.org, and direct Wikipedia access.

`build_lineage.py` (delivered alongside this brief) does the whole job: pulls
every season, walks the chain, and writes the dataset. It has **not been
executed or tested** — no network access where it was written. Expect to debug
the API field names on first run.

### Verification targets

An independently computed chain should land near cfb-belt.com's published
figures: ~328 reigns, ~101 distinct teams, ~1,636 belt games. Large divergence
means a ruleset difference (most likely the tie rule) — worth tracking down,
since it's also a content angle.

## Known blockers

- **The original site's conventions are unrecovered.** archive.org is blocked
  from the cloud session. Check the Wayback Machine in a browser for a pre-2018
  snapshot of collegefootballbelt.com and note its origin point and any stated
  rules, so the revived site stays continuous with the old one.

## Suggested build order

1. Get a CFBD API key; run `build_lineage.py`; sanity-check against the targets.
2. Lock and write up the ruleset; regenerate.
3. Add team color data keyed to the lineage (needed for the color-shift feature).
4. Build the site off the generated dataset.
5. Wire up weekly updates so the belt game each week refreshes automatically.
