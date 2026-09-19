#!/usr/bin/env python3
"""
Season-accurate FBS / FCS classification, from the conference each side
was in when the game was played (2026-09-19).

Why this exists: the FBS-only and FCS-only companion belts used to sort
programs by CFBD's CURRENT classification (one /teams call, applied to
every season back to 1869). That was wrong in both directions -- Yale was
an "FCS school" in 1946, thirty years before Division I-AA existed, and
programs CFBD's current list had no classification for silently vanished
from the FCS belt altogether (North Dakota State's 2015 national title win
never counted, and South Dakota State's 2023 title run was cut off the day
it started). A reader, Elliot, spotted all of that in September 2026.

The data source already reports, for every game, the conference each side
belonged to AT THE TIME (build_alternate_lineages.py keeps it in the
archive; build_lineage.py's normalize() keeps it for the live seasons).
Conferences are what Division I is actually split by, so a conference's
subdivision in a given season is a much better proxy than a program's
status today -- and it moves with the program the way realignment does
(James Madison is an FCS game in 2021 and an FBS game in 2022, because
that is when its conference changed).

The split itself dates from the 1978 season, when Division I football was
divided into I-A (today's FBS) and I-AA (FCS). Before 1978 there was one
Division I, so:

  - the FBS belt is identical to the real belt through the 1977 season and
    then counts only games between two I-A / FBS conferences' members;
  - the FCS belt starts with the first I-AA game of the 1978 season.

CONFERENCES below is a hand-kept map: conference name (as the data source
spells it) -> classification, or a list of (first_season, last_season,
classification) eras for the handful of conferences that changed
subdivision. Anything not listed (every Division II / III / NAIA
conference, and games with no conference on record) is unclassified and
belongs to neither companion belt. Known approximations are noted inline.
"""

FCS_FIRST_SEASON = 1978   # Division I-AA's first season
FBS = "fbs"
FCS = "fcs"

# name -> "fbs" | "fcs" | [(first_season, last_season_or_None, classification), ...]
CONFERENCES = {
    # ---- I-A / FBS the whole time (since 1978) ----
    "SEC": FBS, "Big Ten": FBS, "ACC": FBS, "Big 12": FBS, "Pac-12": FBS, "Pac-10": FBS,
    "Pac-8": FBS, "Big 8": FBS, "Big 7": FBS, "Big 6": FBS, "Southwest": FBS, "Big East": FBS,
    "American Athletic": FBS, "Mid-American": FBS, "Conference USA": FBS, "Mountain West": FBS,
    "Sun Belt": FBS, "Big West": FBS, "PCAA": FBS, "FBS Independents": FBS,
    # The Missouri Valley sponsored I-A football through 1985 (Tulsa, Wichita
    # State, West Texas State, New Mexico State ...). Approximation: a few of
    # its members (Drake, Illinois State, Indiana State, Southern Illinois)
    # had reclassified to I-AA by 1982-85 while still playing an MVC
    # schedule; their games in those seasons count as I-A here.
    "Missouri Valley": [(1978, 1985, FBS)],
    # The WAC was I-A until it dropped football after 2012, then sponsored
    # FCS football again in 2021-22 before its members formed the UAC.
    "Western Athletic": [(1978, 2012, FBS), (2021, 2022, FCS)],

    # ---- conferences that moved from I-A to I-AA after the 1978 split ----
    "Ivy": [(1978, 1981, FBS), (1982, None, FCS)],
    "Southern": [(1978, 1981, FBS), (1982, None, FCS)],
    "Southland": [(1978, 1981, FBS), (1982, None, FCS)],

    # ---- I-AA / FCS ----
    "Big Sky": FCS, "MVFC": FCS, "Gateway Football": FCS, "Gateway Collegiate Athletic": FCS,
    "CAA": FCS, "Coastal Athletic": FCS, "Atlantic 10": FCS, "Yankee": FCS,
    "Patriot": FCS, "Colonial": FCS, "OVC": FCS, "Big South": FCS, "Big South-OVC": FCS,
    "SWAC": FCS, "NEC": FCS, "Pioneer": FCS, "FCS Independents": FCS, "Great West": FCS,
    "Metro Atlantic Athletic": FCS, "Gulf Star": FCS, "American West": FCS, "UAC": FCS,
    "Atlantic Sun": FCS,
    # The MEAC moved up from Division II to I-AA in 1980; the Mid-Continent
    # Conference's football members did the same in 1981.
    "MEAC": [(1980, None, FCS)],
    "Mid-Continent": [(1981, None, FCS)],
}

# Pure renames of the same football conference (old name -> current name),
# so a belt follows the league through its name changes instead of
# "retiring" every time the letterhead changed. Mergers into a genuinely
# new conference (Big 8 + Southwest -> Big 12, Big South + OVC -> Big
# South-OVC) are NOT renames and are deliberately not listed.
CONFERENCE_RENAMES = {
    # the Pacific Coast Conference (1915-58) -> AAWU (1959-67) -> Pac-8 -> Pac-10 -> Pac-12:
    # the conference itself dates its history to 1915
    "Pacific": "Pac-12", "AAWU": "Pac-12", "Pac-8": "Pac-12", "Pac-10": "Pac-12",
    # the Big Ten was the Western Conference until 1953 in the data source's labels
    "Western": "Big Ten",
    "Big 6": "Big 8", "Big 7": "Big 8",
    # the Mountain States Conference (1938-47) was nicknamed the Skyline from 1948
    "Mountain State": "Skyline",
    # the data source's label for pre-1956 independents
    "Pre-classification Independents": "FBS Independents",
    # the same football association, spelled two ways across seasons
    "OVC-Big South": "Big South-OVC",
    "PCAA": "Big West",
    "Gateway Collegiate Athletic": "MVFC", "Gateway Football": "MVFC",
    "Colonial": "Patriot",
    # Yankee Conference football became Atlantic 10 football (1997), which
    # became CAA Football (2007), renamed Coastal Athletic in 2023.
    "Yankee": "Coastal Athletic", "Atlantic 10": "Coastal Athletic", "CAA": "Coastal Athletic",
    # The football members of the old Big East continued as the American
    # Athletic Conference in 2013 (the same legal entity; the "Catholic 7"
    # took the Big East name with them).
    "Big East": "American Athletic",
}

# Conferences from before the 1978 split that get a belt of their own (the
# data source's labels): the major conferences of their day. Everything
# listed in CONFERENCES qualifies before 1978 as well.
PRE_SPLIT_CONFERENCES = {
    "SIAA", "Rocky Mountain", "Border", "Skyline", "South Atlantic Intercollegiate Athletic",
    "Texas Intercollegiate Athletic", "Colorado Football", "Virginia Conference",
    "Middle Atlantic University", "Eastern Virginia Intercollegiate Athletic",
    "Intercollegiate Athletic Association of the Northwest",
}

# Conferences that stopped sponsoring football for good, and the date the
# league formally dissolved (or played its last game as that league) --
# the final reign of that conference's belt ends here instead of running
# to the present. Where no formal date is known the belt simply ends the
# day after the conference's last recorded game.
CONFERENCE_DISSOLVED = {
    "Big 8": ("1996-08-30", "the Big Eight formally dissolved on August 30, 1996; its members began Big 12 play that fall"),
    "Southwest": ("1996-06-30", "the Southwest Conference disbanded after the 1995 season"),
    "Big West": ("2001-06-30", "the Big West stopped sponsoring football after the 2000 season"),
    "Western Athletic": ("2023-06-30", "the WAC's football members formed the United Athletic Conference in 2023"),
    "Great West": ("2013-06-30", "the Great West Conference dissolved in 2013"),
    "Metro Atlantic Athletic": ("2008-06-30", "the MAAC stopped sponsoring football after the 2007 season"),
    "Mid-Continent": ("1985-06-30", "the Mid-Continent Conference's football members moved to the Gateway in 1985"),
    "Missouri Valley": ("1986-06-30", "the Missouri Valley stopped sponsoring football after the 1985 season"),
    "Gulf Star": ("1987-06-30", "the Gulf Star Conference dissolved in 1987"),
    "American West": ("1994-06-30", "the American West Conference dissolved after one season of football"),
    "OVC": ("2023-06-30", "OVC football merged into the Big South-OVC Football Association in 2023"),
    "Big South": ("2023-06-30", "Big South football merged into the Big South-OVC Football Association in 2023"),
    "Atlantic Sun": ("2023-06-30", "ASUN football lasted one season before its members formed the UAC in 2023"),
    "Skyline": ("1962-06-30", "the Skyline Conference disbanded in 1962; most of its members founded the WAC"),
    "Border": ("1962-06-30", "the Border Conference disbanded in 1962"),
    "SIAA": ("1942-06-30", "the SIAA, reduced to small colleges after the Southern Conference split off in 1921, disbanded in 1942"),
    "Rocky Mountain": ("1938-06-30", "the Rocky Mountain Conference's major programs left to form the Mountain States (Skyline) Conference in 1938"),
    "South Atlantic Intercollegiate Athletic": ("1921-06-30", "the SAIAA dissolved when its members joined the Southern Conference in 1921"),
    "Texas Intercollegiate Athletic": ("1932-06-30", "the TIAA dissolved in 1932"),
    "Colorado Football": ("1909-06-30", "the Colorado Football Association gave way to the Rocky Mountain Conference in 1909"),
    "Virginia Conference": ("1936-06-30", "the Virginia Conference dissolved in 1936"),
    "Middle Atlantic University": ("1970-06-30", "the Middle Atlantic Conference's university division was dissolved after the 1969 season"),
    "Eastern Virginia Intercollegiate Athletic": ("1918-06-30", "the EVIAA disbanded during the First World War"),
    "Intercollegiate Athletic Association of the Northwest": ("1894-06-30", "the association played two seasons, 1892 and 1893"),
}


def canonical_conference(name):
    """The current name of a renamed conference, else the name itself."""
    return CONFERENCE_RENAMES.get(name, name) if name else name


def classify(conference, season):
    """'fbs', 'fcs', or None for one side of a game: which subdivision that
    conference belonged to in that season. None before the 1978 split (see
    the module docstring), for unlisted conferences, and for games with no
    conference on record."""
    if not conference or season is None or season < FCS_FIRST_SEASON:
        return None
    entry = CONFERENCES.get(conference)
    if entry is None:
        return None
    if isinstance(entry, str):
        return entry
    for first, last, cls in entry:
        if season >= first and (last is None or season <= last):
            return cls
    return None


def game_classes(g):
    """(home classification, away classification) for a normalized game."""
    season = g.get("season")
    return classify(g.get("home_conference"), season), classify(g.get("away_conference"), season)


def scope_games(games, scope):
    """The games a companion belt counts. 'fbs': every game through the 1977
    season (one undivided Division I), then only games between two FBS
    members; 'fcs': games between two FCS members, 1978 on."""
    out = []
    for g in games:
        if scope == "fbs" and (g.get("season") or 0) < FCS_FIRST_SEASON:
            out.append(g)
            continue
        h, a = game_classes(g)
        if h == scope and a == scope:
            out.append(g)
    return out


def conference_counts(name, season):
    """True if a game between two members of `name` in `season` counts for
    that conference's belt: a Division I conference of its day -- FBS or
    FCS by this map from 1978 on, or one of the pre-split conferences
    before that. Division II/III leagues never count."""
    if season is None:
        return False
    if season < FCS_FIRST_SEASON:
        return name in CONFERENCES or name in PRE_SPLIT_CONFERENCES
    return classify(name, season) in (FBS, FCS)


def conference_classification(name, seasons):
    """The classification a conference's belt page is filed under: the
    subdivision it spent the most seasons in (ties go to the latest)."""
    counts = {}
    latest = {}
    for s in seasons:
        c = classify(name, s)
        if c:
            counts[c] = counts.get(c, 0) + 1
            latest[c] = max(latest.get(c, 0), s)
    if not counts:
        return FBS if name in PRE_SPLIT_CONFERENCES or name in CONFERENCES else None   # a pre-split major conference
    return max(counts, key=lambda c: (counts[c], latest[c]))


def division1_game(g, known_d1=None):
    """Whether a game can move the belt at all (2026-09-19, the "Division I
    rule"): every game on record before the 1978 split -- the pre-modern
    record includes the club and service teams that held the belt in the
    1930s -- and, from 1978 on, only a game between two Division I teams
    (FBS or FCS) that season. A Reddit reader flipped Baylor-Wofford 2013 on
    the what-if page and watched the belt fall to Division II UNC Pembroke,
    whose next game on record was in 2021: the data source has no schedules
    below Division I before 2021, so a belt that goes there cannot be
    followed, and a loss to a lower-division team is not a belt game -- the
    holder keeps the belt. Judged by the data source's per-game
    classification when it is present (the live seasons), else by the
    conference each side was in that season; a side with neither is
    Division I only if `known_d1` (see division1_evidence) says the team
    was that season."""
    season = g.get("season")
    if season is None or season < FCS_FIRST_SEASON:
        return True
    for side in ("home", "away"):
        div = g.get(f"{side}_division")
        if div:
            if str(div).lower() in (FBS, FCS):
                continue
            return False
        label = g.get(f"{side}_conference")
        if classify(canonical_conference(label), season) in (FBS, FCS):
            continue
        if not label and known_d1 is not None and (g.get(side), season) in known_d1:
            continue      # no conference on this row, but the team is Division I elsewhere that season
        return False
    return True


def division1_evidence(games):
    """{(team, season)} for every side that any game shows to be Division I
    that season -- the fallback division1_game uses for a row that carries
    no conference at all, so a stray unlabeled game between two Division I
    teams is not thrown away (it matters most for the real belt's live
    seasons, where a missed game means a wrong holder)."""
    known = set()
    for g in games:
        season = g.get("season")
        if season is None or season < FCS_FIRST_SEASON:
            continue
        for side in ("home", "away"):
            div = g.get(f"{side}_division")
            if (div and str(div).lower() in (FBS, FCS)) or \
                    classify(canonical_conference(g.get(f"{side}_conference")), season) in (FBS, FCS):
                known.add((g.get(side), season))
    return known


def fcs_first_season(games, min_games=100):
    """The first season the data source actually covers FCS-vs-FCS play
    (it has almost no I-AA results before 2003 even though the subdivision
    dates from 1978): the first season with at least `min_games` games
    between two FCS-conference members. None if there is no such season."""
    counts = {}
    for g in games:
        if game_classes(g) == (FCS, FCS):
            counts[g["season"]] = counts.get(g["season"], 0) + 1
    for season in sorted(counts):
        if counts[season] >= min_games:
            return season
    return None
