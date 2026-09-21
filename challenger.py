"""What the week's challenger has to do with the belt, computed.

Every belt game has a team on the other side of it, and that team's own
belt history is the most interesting thing on the site for exactly one
week -- the week its fans are looking. Before this, the preview page had
the challenger's reign count and days held in a sidebar and nothing else,
and the X preview post had the betting line. Neither gives anyone a
reason to read further.

So: one function that reads the game record and returns the facts about a
challenger worth saying out loud, as finished sentences, in two orders --
`lines` reads logically, for a page; `ranked` puts the most surprising
fact first, for a 280-character post. Nothing here is written by hand or
by a model; it is all computed from lineage.json's belt_games and reigns,
so it costs nothing at build time and updates itself the moment the belt
moves.

build_site.py puts the lines on the homepage and the preview page;
post_to_x.py takes the first ranked one for the preview post.
"""

from datetime import date

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def _score(g):
    """(home points, away points) for a belt game."""
    h, a = (int(x) for x in g["score"].split("-"))
    return h, a


def _winner_margin(g):
    """(winner, winner score, loser score); winner is None for a tie."""
    h, a = _score(g)
    if h == a:
        return None, h, a
    return (g["home"], h, a) if h > a else (g["away"], a, h)


def _times(n):
    """"once" / "twice" / "7 times" -- "2 times" reads like a robot wrote it."""
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _all_of(n):
    """"Both" / "All 3" -- leading a sentence about n things."""
    return "Both" if n == 2 else f"All {n}"


def _day_phrase(iso):
    """'September 28' from an ISO date."""
    _, m, d = (int(x) for x in iso.split("-"))
    return f"{MONTHS[m - 1]} {d}"


def _reign_days(r, today):
    end = r.get("end_date") or today.isoformat()
    y1, m1, d1 = (int(x) for x in r["start_date"].split("-"))
    y2, m2, d2 = (int(x) for x in end.split("-"))
    return (date(y2, m2, d2) - date(y1, m1, d1)).days


def belt_story(team, holder, lineage, today=None):
    """Facts about `team`'s history with the belt, and with `holder`.

    `lines` is the page version (logical order); `ranked` is the same
    sentences with the most surprising first. A team that has never
    played a belt game still returns a usable dict -- short, never wrong.
    """
    today = today or date.today()
    games = lineage.get("belt_games") or []
    reigns = lineage.get("reigns") or []

    mine = [g for g in games if team in (g["home"], g["away"])]
    wins = [g for g in games if g["outcome"] in ("changed", "established") and g["new_holder"] == team]
    as_holder = [g for g in games if g["holder"] == team]
    defended = [g for g in as_holder if g["outcome"] == "retained"]
    lost = [g for g in as_holder if g["outcome"] == "changed" and g["new_holder"] != team]
    my_reigns = [r for r in reigns if r["team"] == team]

    days = sum(_reign_days(r, today) for r in my_reigns)
    longest_reign = max(my_reigns, key=lambda r: _reign_days(r, today), default=None)
    longest = _reign_days(longest_reign, today) if longest_reign else 0

    vs = [g for g in mine if holder in (g["home"], g["away"])]
    vs_wins = [g for g in vs if _winner_margin(g)[0] == team]
    vs_losses = [g for g in vs if _winner_margin(g)[0] == holder]
    vs_takes = [g for g in vs if g["outcome"] == "changed" and g["new_holder"] == team]

    out = []           # (priority, text) -- lower priority = more surprising

    # has it ever held the thing?
    if not my_reigns:
        tries = [g for g in mine if g["holder"] != team]
        if tries:
            last = tries[-1]
            played = ("played one game with the belt on the line and lost it"
                      if len(tries) == 1 else
                      f"played {len(tries)} games with the belt on the line and lost every one")
            out.append((1, f"{team} has never held the belt. It has {played}, most recently to "
                           f"{last['holder']} in {last['date'][:4]}."))
        else:
            out.append((1, f"{team} has never played a game with the belt on the line."))
    else:
        n = len(my_reigns)
        last_end = my_reigns[-1].get("end_date")
        when = "and holds it now" if last_end is None else f"most recently in {last_end[:4]}"
        out.append((2, f"{team} has held the belt {_times(n)} for {days:,} days in total, {when}."))
        if n > 1:
            out.append((4, f"Its longest reign ran {longest:,} {'day' if longest == 1 else 'days'}, "
                           f"starting in {longest_reign['start_date'][:4]}."))

    # what it does once it has it
    if as_holder:
        w, l = len(defended), len(lost)
        out.append((3, f"With the belt in hand it is {w}–{l}: {w} successful "
                       f"{'defense' if w == 1 else 'defenses'} and {l} "
                       f"{'loss' if l == 1 else 'losses'} that handed it straight back."))

    # this week's opponent, specifically
    if vs:
        ties = len(vs) - len(vs_wins) - len(vs_losses)
        record = (f"{len(vs_wins)}–{len(vs_losses)}–{ties}" if ties
                  else f"{len(vs_wins)}–{len(vs_losses)}")
        tie_note = " (the ties left it where it was)" if ties else ""
        out.append((2, f"{team} and {holder} have met {_times(len(vs))} with the belt at stake, "
                       f"{record} to {team}{tie_note}."))
        if vs_takes:
            g = vs_takes[-1]
            _, ws, ls = _winner_margin(g)
            # Every one of those wins on the same calendar day, or failing
            # that in the same month, is the kind of thing worth leading
            # with -- so when it holds, it replaces the plain version rather
            # than following it, and reads on its own in a post.
            same_day = {g2["date"][5:] for g2 in vs_takes}
            same_month = {g2["date"][5:7] for g2 in vs_takes}
            years = [g2["date"][:4] for g2 in vs_takes]
            when = f"{', '.join(years[:-1])} and {years[-1]}"
            took = f"{team} has taken the belt off {holder} {_times(len(vs_takes))}"
            if len(vs_takes) > 1 and len(same_day) == 1:
                out.append((0, f"{took}, and every time it happened on "
                               f"{_day_phrase(g['date'])} — {when}."))
            elif len(vs_takes) > 1 and len(same_month) == 1:
                out.append((0, f"{took}, and every time in "
                               f"{MONTHS[int(g['date'][5:7]) - 1]} — {when}."))
            else:
                out.append((1, f"{took}, last on {_day_phrase(g['date'])}, "
                               f"{g['date'][:4]}, {ws}–{ls}."))
    elif mine:
        out.append((2, f"{team} and {holder} have never met with the belt on the line."))

    # the biggest thing it ever did with it
    if len(wins) > 1:
        best = max(wins, key=lambda g: (lambda t: t[1] - t[2])(_winner_margin(g)))
        _, ws, ls = _winner_margin(best)
        if ws - ls >= 14:
            out.append((4, f"Its biggest belt win: {ws}–{ls} over {best['holder']} on "
                           f"{_day_phrase(best['date'])}, {best['date'][:4]}."))

    lines = [t for _, t in out]
    ranked = [t for _, t in sorted(out, key=lambda x: x[0])]
    return {
        "team": team,
        "holder": holder,
        "reigns": len(my_reigns),
        "days": days,
        "longest_days": longest,
        "wins": len(wins),
        "defenses": len(defended),
        "losses_as_holder": len(lost),
        "last_held": (my_reigns[-1].get("end_date") or "")[:4] if my_reigns else None,
        "vs_meetings": len(vs),
        "vs_record": [len(vs_wins), len(vs_losses)],
        "vs_takes": len(vs_takes),
        "lines": lines,
        "ranked": ranked,
        "headline": ranked[0] if ranked else "",
    }


def short_line(story, limit=150):
    """The one fact for a post: the most surprising that fits `limit`."""
    for line in story.get("ranked") or []:
        if len(line) <= limit:
            return line
    return ""
