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

That means a reign's length in days always includes the offseason: a team
that holds the belt on New Year's Day gets 200-plus quiet days added to its
reign before anyone can take it. That is the lineal convention and it stays,
but the site also measures every reign in belt games — the game that won it
plus every defense — and the Full History and conference pages let you
switch between the two. A reader, Elliot, suggested the switch.

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

If the holder loses to an FCS program, the belt goes with it, exactly as it
would against any other opponent — the FBS/FCS line is a modern
administrative label, not a rule of the game the belt is based on. The same
went for the club, athletic-association and military service teams of the
pre-modern era, and still does: before the 1978 split, every game on record
counts.

From 1978 on, though, only a game between two Division I teams (FBS or FCS,
as they were classified that season) can move the belt. This is a limit of
the record, not a judgment about the football: the data source has no
schedules below Division I before 2021, so a belt that went to a Division
II, III or NAIA team could not be followed — it would simply vanish until
that team next met a Division I opponent, years later. A Reddit reader
found exactly that on the What if? page in September 2026 (a flipped
Baylor–Wofford 2013 sent the belt to Division II UNC Pembroke, whose next
game on record was in 2021). So a holder's loss to a lower-division team is
not a belt game, and the holder keeps the belt. In the real lineage this has
never come up — no holder has met a team below Division I since 1978 — so
the rule changes nothing about the history; it governs the live seasons,
the What if? page and the alternate universes.

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

## Companion belts: FBS-only, FCS-only and the conference belts

Alongside the real belt the site runs companion belts under the same rule,
each restricted to a smaller world of games. They are derived the same way
— mechanically, from the game record — with three additions the real belt
never needs:

- **Which games count is judged game by game, in the season the game was
  played.** A conference belt counts a game only if both teams were members
  of that conference at the time. The FBS-only and FCS-only belts count a
  game only if both teams belonged to a conference of that subdivision that
  season, so a program that moves up or down takes its games with it. The
  subdivisions themselves date from 1978, when Division I split into I-A
  (now FBS) and I-AA (now FCS): the FBS belt is simply the real belt through
  the 1977 season and diverges only after that, and the FCS belt cannot
  start before 1978. In practice it starts in 2003, the first season the
  data source has any real coverage of I-AA games; it has almost none
  between 1978 and 2002.
- **A holder that leaves vacates the belt; one that stays keeps it.**
  Membership is read season by season from every game a team played, not
  just the ones that count, so an independent that goes years without
  meeting another independent is still an independent and simply keeps the
  belt until it meets one — the belt waits, as the real belt does. A holder
  that actually left — it changed conferences, moved between subdivisions,
  or stopped fielding a team while its old world went on — vacates the belt
  as of its last game as a member, and the belt reverts to the most recent
  earlier holder that is a member of the season it left for. The belt never
  rests with a team that is no longer part of its world.
- **A belt whose world ends retires.** When a conference dissolves there is
  no one left to inherit its belt, so the final reign closes on the date the
  league formally dissolved (or the day after its last game, when no formal
  date is known) and the belt is retired with that holder. A league that only
  changed its name keeps one continuous belt under its current name; one
  that stopped sponsoring football and later resumed starts a fresh lineage
  with its next game. A silence that crosses seasons the data source does
  not cover (I-AA play before 2003) is treated the same way — the belt
  retires on the last game we can see and a fresh lineage starts with the
  next one on record — rather than pretending nothing happened in between.

These rules replaced an earlier version in September 2026 after Elliot, a
reader, pointed out that the FCS belt listed a run of one-day reigns, that
North Dakota State had somehow never held it, and that defunct conferences
showed their last holder as still reigning "to the present." He was right
on every count; the last one was a genuine bug, and the others came from
judging every game by each program's classification today rather than in
the season it was played. Days later a Reddit reader caught the next flaw:
the FBS Independents belt showed Notre Dame "leaving" twice and the belt
retiring with UConn, when both are still independents — the site had been
inferring "left" from "hasn't played another member lately." Membership is
now read from the games themselves.

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
