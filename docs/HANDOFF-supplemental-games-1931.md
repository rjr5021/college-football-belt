# Supplemental games: the 1931 Olympic Club correction (2026-09-17)

## Why

Ray (rutgersstartedthis.com) emailed to point out that the site has the Olympic Club holding the belt from Nov. 7, 1931 until Stanford beat them on Sept. 17, 1932. The Olympic Club actually lost 13–0 at Loyola (CA) on Nov. 21, 1931. CFBD doesn't have that game, and it's also missing five of the games that follow it in the chain.

## Corrected chain

| # | Holder | Won | From / score | Defenses | Lost |
|---|---|---|---|---|---|
| 65 | Olympic Club | 1931-11-07 | Saint Mary's (CA), 10–0 | 0 | 1931-11-21 |
| 66 | Loyola Marymount | 1931-11-21 | Olympic Club, 13–0 * | 0 | 1931-11-29 |
| 67 | San Diego Marines | 1931-11-29 | Loyola, 7–0 * | 2 * (Santa Barbara AC 9/12, West Coast Army 9/18) | 1932-09-24 |
| 68 | Fresno State | 1932-09-24 | Marines, 12–0 * | 0 | 1932-10-01 |
| 69 | West Coast Army | 1932-10-01 | Fresno State, 7–6 * | 1 (San Francisco 10/8) | 1932-10-15 |
| 70 | Stanford | 1932-10-15 | West Coast Army, 26–0 | 0 | 1932-10-22 |
| 71 | USC | 1932-10-22 | unchanged from here on | | |

\* = game missing from CFBD, added with citations. Totals change from 1,621 to 1,624 historical belt games and from 321 to 325 closed reigns. **Reigns #66 and later are renumbered +4**, which changes the `reigns/N.html` URLs.

## What's in `0004-supplemental-games-1931-olympic-club.patch`

- `historical_data/supplemental_games.json`: the six games, each with newspaper and Wikipedia sources. Ids are in the reserved range 90,000,001–90,000,006.
- `supplemental_games.py`: validates the file and merges its games at read time. If CFBD later adds the same game, CFBD's copy wins.
- `apply_supplemental_games.py`: re-walks the archive into `baseline.json`. It refuses to write unless the archive alone reproduces the current baseline. `--dry-run` previews the changes. It also drops the cached historical note for game 18370, whose belt outcome changed.
- Hooks in `build_lineage.py` (fetched seasons, so `--full-refetch` stays correct) and `build_alternate_lineages.py`.
- `build_site.py` (two small hunks in `render_page`, one in `inline_md`, plus one import): the Sources strip shows citations for hand-added games. The patch also applies cleanly to the redesign copy.
- `ruleset.md`: credits Ray (first name only) with a link to rutgersstartedthis.com. It also adds new sections "Club and service teams can hold the belt" and "Games missing from the data source", plus edits to the intro, the Non-FBS section, Sourcing, and Open items.
- `build_site.py` `inline_md()`: the ruleset page now renders `[label](https://...)` links. Only http(s) links are converted, and they are escaped first. Before this, the ruleset page couldn't show links at all.
- `README.md` section and `test_supplemental_games.py`.

## To ship

```
git apply 0004-supplemental-games-1931-olympic-club.patch
python3 apply_supplemental_games.py --dry-run   # review
python3 apply_supplemental_games.py             # writes baseline.json, trims historical_notes.json
python3 test_supplemental_games.py
git add -A && git commit && git push            # the normal pipeline rebuilds the site
```

`build_site.py` is shared with the merch and redesign sessions. Pull first and log the edit in the coordination note.

## Verified (against `main` @ b7a4f47)

- The archive re-walk reproduces the current baseline byte for byte, both before and after the change. The script is idempotent.
- The chain above is exactly what the re-walk produces, and the open reign (Florida) is unchanged.
- All 7 existing tests plus the new one pass.
- A local `build_site.py` build from the patched baseline (with placeholder colors and details) works. Games 90000001–6 and reigns 66–70 render correctly with citations, and `ruleset.html` shows the new sections.
- `build_alternate_lineages.py` runs with the merge in place.

## Follow-ups

- Colors and logos for San Diego Marines, West Coast Army, and Loyola Marymount: check after the first real build. They may need placeholders like Olympic Club's.
- Two dates are uncertain by a day (Marines vs. Santa Barbara AC, Marines vs. West Coast Army). Neither affects the lineage, and both are listed under Open items.
- Unrelated, but noticed: some cached historical notes misread scores. Note 18224 says Oregon State won 27–0, but Stanford won 27–0 (the score is stored home–away). This is worth a pass through `generate_historical_notes.py`'s prompt.
- The Losers Belt and conference belts are not re-derived here. None of these games is between current D1 conference members, and the Losers Belt walk is separate.
