# The College Football Belt: Ruleset

*Every belt game behind this site is computed from the full game-by-game
record, never hand-assembled; the rare game missing from that record is
added from cited sources (see "Games missing from the data source"). This
page is the complete set of rules that computation runs on. If a divergence ever shows up between this site and
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
the pre-modern era a club, athletic-association or military service team —
the belt goes with it, exactly as it would against any other opponent.
Division/classification is a modern administrative label; it isn't a rule of the game the belt is
based on. (Mechanically, this is already how the underlying data pull
works: games are pulled across every classification with no FBS-only
filter, so a belt-relevant upset by a lower-division team would already be
caught — it doesn't require a separate rule to implement, only to state.)

## Club and service teams can hold the belt

The same logic runs in both directions. A club or service team that wins
the belt holds it like anyone else: its later games are belt games, it can
defend against other non-college teams, and it only loses the belt by
losing on the field. This has happened: the Olympic Club of San Francisco
took the belt from Saint Mary's in November 1931, and the San Diego Marines
and West Coast Army both held it before Stanford won it on October 15, 1932.

Who suits up doesn't change this either. In November 1931 the Olympic Club
split its squad, sending half to Hawaii while the other half played a
scheduled game at Loyola (Los Angeles). That half lost, and the belt went
to Loyola. An official game on a team's schedule counts no matter who
played in it, the same way a college's loss with its starters injured
still counts.

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
mechanically from the College Football Data API's game-by-game
record, not researched or hand-entered. The only exception is the short,
cited list of games that record is missing (next section), and those are
walked through the same rules as everything else. Given the rules on this
page and every final score in order, the result is fully determined; there
is no editorial judgment applied game to game. Game dates reflect the local
kickoff time at the game's venue, not a UTC timestamp, so a late West Coast
night game is dated the evening it was actually played rather than the
following morning UTC.

## Games missing from the data source

The College Football Data API's record is close to complete for college
teams, but it has gaps in the pre-war era, mostly games involving club,
service and small-college teams. When a gap changes who held the belt,
the missing game is added by hand, and only under these conditions:

- The final score and date come from a contemporary source, normally a
  newspaper report from the day after the game, and that citation is shown
  on the game's page.
- The game is added exactly as played. Its result goes through the same
  rules as every other game, so every reign, defense and total after it is
  recomputed, not edited.
- Added games are kept in their own file, separate from the data source.
  If the data source later adds the same game, its copy is used instead.
- A game is added because it was played, never to make the lineage come
  out a particular way. Whatever follows from it follows.

The first correction came in September 2026 from Ray, who runs
[rutgersstartedthis.com](https://rutgersstartedthis.com), another lineal
belt tracker. The data source
was missing Loyola's 13–0 win over the Olympic Club on November 21, 1931,
so this site had the Olympic Club holding the belt until September 1932.
Adding that game, and the five other missing games the belt then passed
through, added four reigns (Loyola, the San Diego Marines, Fresno State
and West Coast Army) and shortened the Olympic Club's reign to two weeks.
The lineage rejoins the previous chain when USC beat Stanford on October
22, 1932, so nothing after that date changed.

*If you know of a missing game that affects the belt, email
hello@collegefootballbelt.com with a source.*

## Open items

- **Origin cross-check against the pre-2018 collegefootballbelt.com site**,
  pending Internet Archive access.
- **Two dates in the 1932 correction are uncertain by a day**: the San
  Diego Marines' wins over the Santa Barbara Athletic Club (listed as
  September 12, possibly played the 11th) and West Coast Army (September 18,
  listed by one source as the 17th). Neither changes the lineage.
- **Pre-1900 inclusion boundary**: the current rule includes any game with a
  final score, which is the maximally inclusive reading. If the original
  site drew a narrower line, the cross-check above may prompt a revision.
