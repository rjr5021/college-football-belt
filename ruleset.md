# The College Football Belt: Ruleset

*Every belt game behind this site is cited and computed from play-by-play
records, never hand-assembled. This page is the complete set of rules that
computation runs on. If a divergence ever shows up between this site and
another tracker, it almost always traces back to one of the decisions below.*

## Origin

The belt begins with college football's first game: Rutgers over Princeton,
6–4, on November 6, 1869, in New Brunswick, New Jersey. Rutgers is the
belt's first holder.

*Status: locked, pending one open check. The original collegefootballbelt.com
site (dormant since 2018) may have stated its own origin point or house
rules, and we'd like to confirm this site stays continuous with it via a
Wayback Machine snapshot — but the Internet Archive was unreachable when we
last checked (it was reporting a full outage, not a blocked request), so
that comparison is still pending. Nothing here depends on the outcome; 1869
is the standard origin every existing lineal tracker uses.*

## How the belt changes hands

The belt is won on the field. Whoever beats the current holder in a game
takes it; every other result leaves it where it was. This one sentence is
the entire engine — everything below is just how it resolves specific
situations.

## Ties

Before overtime rules existed, college football had plenty of tied games.
This site's rule: **the holder retains the belt on a tie.** A tie is not a
loss, so it is not treated like one.

*Why this over the alternative: this is the standard lineal-title
convention (see boxing's lineal titles, or the "you have to beat the man"
principle generally), and it's also the version that reproduces the
reference trackers' published totals almost exactly — the other convention
would move noticeably more distinct teams through the belt than any tracker
reports holding it.*

## When the holder doesn't play

If the reigning holder has no game in a given week — a bye, an opted-out
bowl, a canceled game — the belt simply carries over untouched. It only
moves when the holder plays and loses. This already came up for real:
Notre Dame won the belt on November 29, 2025, then missed the 2025 playoff
and opted out of a bowl game, so the belt sat frozen through the entire
offseason with no belt game until the 2026 opener.

## Interrupted seasons

Seasons that were canceled or drastically shortened league-wide — 1918
(influenza pandemic), and the 1943–44 seasons for many programs during
World War II — are treated the same way as any other stretch where the
holder didn't play: the belt carries over. No special-case handling; it
falls directly out of the rule above.

## Forfeits and vacated wins

Some games are later stripped from the official record — a vacated win due
to NCAA sanctions, for instance. This site follows the **on-field result**,
not the retroactive record. The belt's entire premise is that it is won and
lost on the field on a given day; a sanction imposed years later doesn't
change what happened during the game, so it doesn't change who left with
the belt.

## Non-FBS (or non-modern-classification) opponents

If the holder loses to a team from a lower division — an FCS program, or in
the pre-modern era a club or athletic-association team — the belt goes with
it, exactly as it would against any other opponent. Division/classification
is a modern administrative label; it isn't a rule of the game the belt is
based on. (Mechanically, this is already how the underlying data pull
works: games are pulled across every classification with no FBS-only
filter, so a belt-relevant upset by a lower-division team would already be
caught — it doesn't require a separate rule to implement, only to state.)

## The early era (pre-1900)

Football in the 1870s–1890s was often played by athletic-club and
university-association teams that don't map cleanly onto today's program
structure. This site includes any game that shows up in the historical
record with a completed score, regardless of whether the participants would
be recognized as "college teams" by today's boundaries — consistent with
following on-field results rather than modern classification, per the rule
above.

## Sourcing and methodology

The full chain — every reign, every belt game, every score — is computed
mechanically from the College Football Data API's complete game-by-game
record, not researched or hand-entered. Given the rules on this page and
every final score in order, the result is fully determined; there is no
editorial judgment applied game to game. Game dates reflect the local
kickoff time at the game's venue, not a UTC timestamp, so a late West Coast
night game is dated the evening it was actually played rather than the
following morning UTC.

## Open items

- **Origin cross-check against the pre-2018 collegefootballbelt.com site**,
  pending Internet Archive access.
- **Pre-1900 inclusion boundary**: the current rule includes any game with a
  final score, which is the maximally inclusive reading. If the original
  site drew a narrower line, the cross-check above may prompt a revision.
