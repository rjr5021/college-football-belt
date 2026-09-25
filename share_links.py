"""One place to tag the links the site's automated posts publish.

Why this exists: on 2026-09-21 the traffic baseline (claude/analytics-
baseline-2026-09-21.md in the project notes) showed t.co -- every click
from X, of any kind -- sending four visits in the site's first eight days,
against roughly 3,650 arrivals overall. Four is small enough that it could
mean "the posts don't work", "the posts work but only one kind of post
does", or "the posts work and the measurement is wrong". Untagged links
cannot tell those apart: every post type arrives as the same anonymous
t.co referrer.

So every link that leaves in a post gets ?utm_source=...&utm_medium=...
&utm_campaign=<post type>. GoatCounter reads utm_campaign into its
Campaigns widget, and because every page on the site carries a
<link rel="canonical">, GoatCounter's script reports the canonical (clean)
path for the pageview and sends the query string separately -- so the
tags show up as campaigns without splitting /preview.html into a dozen
near-duplicate rows in the Pages list.

Keep CAMPAIGNS stable once a name is in the wild: renaming one starts a
new row in the dashboard and orphans the history under the old name.
"""

from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

# The post types, by the script that sends them. The value is the campaign
# name exactly as it will appear in GoatCounter.
CAMPAIGNS = {
    "result": "x-result",            # post_to_x.py -- the belt changed hands (or was defended)
    "preview": "x-preview",          # post_to_x.py -- the week's upcoming belt game
    "poll": "x-poll",                # post_to_x.py -- the "does it stay put?" poll
    "gameday": "x-gameday",          # post_to_x.py -- game-day morning
    "live-kickoff": "x-live-kickoff",    # post_live_game.py
    "live-score": "x-live-score",        # post_live_game.py
    "live-half": "x-live-half",          # post_live_game.py
    "live-final": "x-live-final",        # post_live_game.py
    "state": "x-state",              # post_to_x.py -- Sunday, where the belt sits
    "challenger": "x-challenger",    # post_to_x.py -- Thursday, who is coming for it
    "on-this-day": "x-on-this-day",  # post_on_this_day_to_x.py -- Tuesday, from the archive
    "instagram": "ig-post",          # post_to_instagram.py (bio/caption links)
}


def tag(url, kind, source="x", medium="social", bust=None):
    """Return `url` with the UTM parameters for post type `kind`.

    Unknown kinds pass through untagged rather than inventing a campaign
    name -- a typo should cost a measurement, not send traffic to a row
    nobody is watching. Existing query parameters are preserved, and a
    URL that already carries utm_campaign is left exactly as it is.

    `bust` adds &g=<bust>, and exists because of what happened on
    2026-09-24: the Thursday challenger post linked /preview.html and X
    unfurled it with LAST week's title, "Notre Dame vs. Michigan State
    Preview". The page was correct; X was showing a link-preview card it
    had scraped days earlier and cached. /preview.html is one URL whose
    contents change every week, so every post that links it and carries
    no image of its own is at the mercy of that cache. Passing the game
    id makes each week's link a distinct URL to the scraper and it
    fetches afresh. The page's rel=canonical still points at the clean
    path, so Google sees one page and GoatCounter still reports the
    canonical path with the query string recorded separately.
    """
    campaign = CAMPAIGNS.get(kind)
    if not campaign or not url:
        return url
    parts = urlparse(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "utm_campaign" in params:
        return url
    params.update({"utm_source": source, "utm_medium": medium, "utm_campaign": campaign})
    if bust:
        params["g"] = str(bust)
    return urlunparse(parts._replace(query=urlencode(params)))
