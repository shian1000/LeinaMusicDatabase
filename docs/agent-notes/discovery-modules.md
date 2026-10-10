# Discovery module fetchers (`src/utils/discoveries/`)

`discoveries_manager.py` dynamically loads the *enabled* files in `discovery_modules/` and tries
each one in turn via `get_album_name(artist, title)`, in the user-configured order, until one
returns an album for the song being looked up.

Fetch priority used to be pinned by numeric filename prefixes (`1music_brainz_fetcher.py`, etc.);
that's gone. Order and enabled/disabled state now live in `discovery_settings.py`, persisted to
`discovery_modules_config.json` (gitignored, repo root) keyed by filename stem, and are editable
at runtime via Settings -> Discovery modules in the main menu
(`src/menu/main_menu/settings/__init__.py`). `discovery_settings.reconcile_discovery_config()`
auto-appends any new file dropped into `discovery_modules/` (enabled by default) and drops stale
entries for deleted/renamed files — so adding a fetcher is just adding the file, no naming scheme
to maintain.

## The Settings menu reads `MODULE_NAME` without importing the module

Two loaders in `discoveries_manager.py`, deliberately asymmetric:

- `load_discovery_modules()` — the actual "Fill missing data -> Albums" path. Skips disabled
  modules *before* importing them, so a disabled module's import-time code never runs.
- `load_all_discovery_modules_metadata()` — the Settings enable/disable + reorder screens. Needs
  every module's display name (enabled or not), but does **not** import them. `_read_module_name()`
  statically parses each file with `ast` and pulls the `MODULE_NAME` value out of the syntax tree.

The reason it doesn't just import them: the fetchers do real work at import time — `selenium`,
`requests`, `musicbrainzngs.set_useragent(...)`, `import wikipedia` — and importing all five
(including disabled ones) just to read one string each is pure waste every time someone opens the
Settings menu.

Consequence for anyone adding or renaming a fetcher: `MODULE_NAME` **must be a plain top-level
string-literal assignment** (`MODULE_NAME = "Foo fetcher"`). An f-string, a concatenation, or a
computed value is invisible to the static parser — the Settings menu will silently fall back to
showing the filename stem (the runtime path still works, since `load_discovery_modules()` reads it
via `getattr` on the real module). Keep it a literal.

## Title/artist truncation before querying, and the untruncated fallback

Before calling any module, `discover_album_name()` runs the artist and title through
`truncate_at_word()` (`text_utils.py`), which cuts the string off at the first occurrence of a
stop word (`feat`, `&`, `ft.`, `, `, etc.) — meant to strip trailing collaborator credits like
"Song Title feat. Other Artist" down to just "Song Title" before searching, since most fetchers
match better without them. The list also holds the Polish guest credit "gościnnie"/"goscinnie",
usually written after a dash ("Pochodnia - gościnnie Kasia Sienkiewicz"), so when truncation cuts
something it also strips a dangling trailing ` -`/`–`/`—` — otherwise the query becomes
"Pochodnia -" and every fetcher misses. Real titles that confused this step are pinned in
`tests/test_album_discovery_queries.py` (`ALBUM_QUERY_REGRESSION_CASES` — the first query sent to
a fetcher); add a new problematic song there as one more row.

The catch: the bare `", "` stop word matches *any* mid-string comma-space, not just a trailing
credits list, so it also truncates legitimate titles/artists that happen to contain one — e.g.
"Bang, Bang" becomes just "Bang" before it ever reaches a fetcher's query. Rather than special-case
the stop-word list (which is inherently ambiguous — a comma could be a real credits separator or
part of the title), `discover_album_name()` compensates with a fallback: if a module's truncated
query comes up empty *and* truncation actually changed something, it retries that same module once
more with the original, untruncated (but still parenthetical-stripped) artist/title before moving
on. This runs before the synonym-retry step. Because it's implemented in the manager rather than
per-module, every fetcher gets the fallback for free.

A third stage exists for the same reason, one level up: every attempt above — truncated *and*
"untruncated" — still runs both artist and title through `.split("(")[0]`, which assumes
parenthesised text is always disposable (a "(feat. X)" credit, a "(Radio Edit)" annotation). That
assumption breaks when the parenthesised part is the actual title, e.g. Merk & Kremont's
"Sad Story (Out of Luck)": `.split("(")[0]` reduces it to just "Sad Story", and `truncate_at_word`
separately reduces the artist "Merk & Kremont" to "Merk" on the bare `&` stop word. Neither the
first attempt nor the "untruncated" retry ever queries with the real title, so the search comes up
empty in every fetcher, not just one — confirmed live on music.youtube.com, which has a clean,
non-blacklisted match for the exact, fully-raw "Merk & Kremont" / "Sad Story (Out of Luck)" query
and nothing usable for "Merk" / "Sad Story" alone. If splitting on `"("` actually removed something
from either string, `discover_album_name()` retries once more with the fully raw, nothing-stripped
artist/title before falling through to the synonym-retry step.

A different, still-open gap in the same stop-word list, found while smoke-testing
`spotify_fetcher.py` against a real "Artist1 + Artist2" collab (`MRFY + Laibach - Poskočna`): `+`
isn't a stop word, so a plus-joined artist credit is never truncated and reaches every fetcher's
artist-similarity check as one unbroken string. Spotify credits that track to two separate artists
(`MRFY`, `Laibach`), and neither one alone — nor any fetcher's combined-artist string comparison —
resembles the full `"MRFY + Laibach"` query closely enough to clear `SPELLING_CHECK_THRESHOLD`, so
the album lookup fails across the board, not just on Spotify. This is a `truncate_at_word()` /
manager-level gap (the same class of problem as the comma case above, just a different separator),
not something `spotify_fetcher.py` itself can fix — left as-is rather than special-cased locally.

## Fallbacks when every fetcher is empty (soundtrack artist, singles markers)

Game/anime soundtracks are often stored with the album in the artist field ("Tekken 5 OST -
Ground Zero Funk"), and no fetcher knows the individual track. If every module (with all its
retries) comes up empty and `text_utils.is_soundtrack_artist()` matches one of
`SOUNDTRACK_ARTIST_MARKERS` (`constants.py`) as a whole word in the artist name, `discover_album_name()`
returns the full artist name as the album. Deliberately only after the loop — a fetcher may still
know the proper album name ("Tekken 5 Original Soundtrack") — and not recorded as any module's
success in `discovery_stats`. Whole-word matching uses `(?<!\w)…(?!\w)` rather than `\b`, since
`\b` fails after the trailing dot of "O.S.T."; and it's what keeps "Ghost" / "Post Malone" out.
Knock-on effect: "Fill missing data -> Years" groups by album, so all songs of one OST artist then
share one year lookup.

Next, if `text_utils.has_singles_marker()` finds a `SINGLES_MARKER` word ("cover", "covered",
"unplugged", ...) as a whole word in the title *or* the artist, the song is filed under
`SINGLES_ALBUM` — a cover/unplugged version no fetcher knows is almost always a standalone upload.
Both fields are checked because such songs often land with artist/title swapped ("serial
heartbreaker" / "fletcher loop cover"). The soundtrack fallback runs first because it names a more
specific album. Known, accepted false positive: a real title containing the word ("Twinz (Deep
Cover '98)") — the user decided it doesn't matter, since this path only ever runs for songs that
have no album at all and that every fetcher has already missed. Same last-resort / no-stats rules
as the soundtrack fallback. Cases for both fallbacks are pinned in
`tests/test_album_discovery_queries.py` (`ALBUM_EMPTY_FETCHERS_CASES`).

## Why the manager validates results itself instead of trusting modules

Each fetcher scrapes/searches its own source and, historically, did its own match verification
(comparing a candidate result's title/artist to the query before accepting it) — the manager just
took whatever a module returned at face value.

That trust broke in practice: `genius_fetcher.py` had a loop that correctly searched Genius
results for one whose URL slug matched the query title, but a leftover line right after the loop
(`song_url = link.get_attribute("href")`) unconditionally overwrote the verified match with
whichever search result the loop happened to end on — silently discarding its own verification and
scraping an unrelated song's page whenever no real match was found. That's common for exactly the
songs likely to have a missing album in the first place: niche tracks like game soundtracks,
covers, and live versions that Genius doesn't index. The observed symptom was a real user's
"Fill missing data -> Albums" run confidently writing one song's actual album into a *different*,
unrelated song's DB record. (It was not an indexing bug in `fill_missing_albums.py` — that pairing
logic was verified clean via an isolated repro against a copy of the real DB before looking
elsewhere.)

The point bug is fixed, but a fetcher module's own verification logic having a bug like this isn't
something the manager can prevent from happening again in a *different* module by fixing this one.
So verification was moved to be defense-in-depth at the manager level, independent of whether a
given module's internal logic is correct.

## The contract: `DiscoveryResult`

`discovery_result.py` defines:

```python
@dataclass
class DiscoveryResult:
    album: str
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
```

A fetcher's `get_album_name()` can return this instead of a bare `str`. `matched_title` /
`matched_artist` must be whatever the module actually found on the page/response it scraped the
album from — never an echo of the input query, since echoing the input would always "match" and
defeats the whole point. Leave a field `None` if the module has no reliable way to extract it.

`discoveries_manager._validate_result()` then, for every module call (upgraded or not):
- Wraps it in try/except — a crashing module is logged and skipped, not fatal to the whole run.
  (Before this, fetcher exceptions propagated uncaught all the way to the top-level crash handler;
  they accounted for a large share of `debug.log`'s crash entries.)
- Rejects non-string, empty, or blacklisted albums.
- If `matched_title`/`matched_artist` were provided, independently checks their similarity against
  the *query* — not the module's own opinion of whether it matched — using
  `scaled_similarity_threshold()` (the same short-string-aware threshold used elsewhere in the
  app), and discards the result if it doesn't resemble what was actually searched for.

Plain `str` returns still work (they just skip the similarity cross-check), so an un-upgraded
module degrades gracefully rather than breaking.

## Release-year lookups ("Fill missing data -> Years") and `get_release_year()`

A second, independent duck-typed contract alongside `get_album_name()`: a module can optionally
define

```python
def get_release_year(artist: str, query: str, is_single: bool) -> int | YearDiscoveryResult | None
```

`query` is an album title (`is_single=False`) or a song title (`is_single=True`) — never a `Song`
object, since the caller (`fill_missing_years.py`) looks a year up once per *album*, not once per
song (see below). `YearDiscoveryResult` (`discovery_result.py`) mirrors `DiscoveryResult` exactly
(`year`, `matched_title`, `matched_artist`), validated by `discoveries_manager._validate_year_result()`
the same way `_validate_result()` validates albums, plus a plausible-range check
(`MIN_PLAUSIBLE_RELEASE_YEAR`..current year + 1, in `config/constants.py`) since a bare `int`/wrong
year can't be caught by a blacklist the way a bad album string can.

Currently implemented by `music_brainz_fetcher.py`, `google_search_fetcher.py`, `spotify_fetcher.py`
and `youtube_fetcher.py` (singles only, see below). `spotify_fetcher.py` searches `/tracks` for a
single and `/albums` for an album (cards matched on `cardTitle` + artist links), then opens the
track/album page. Either page's header (`entity-header`) has a `data-testid="release-date"` span,
with `entityTitle`/`creator-link` reported back as the match. On a track page that date is the
release date of the track's album. Its search query is a URL *path* segment, so `_search_url()` escapes "/" too:
`quote()` leaves it alone by default, and a title like "Jidyszland / Yiddishland" split the path,
so the search page never loaded. `genius_fetcher.py` deliberately has no `get_release_year()`.
Genius album pages are user-made and include unreleased ones: Geezer's "G1*" page (an
unreviewed bio saying "will come out sometime in 2026", leaked or snippet tracks, no release date)
is the only source that knows that album at all. A Genius year lookup would have to tell released
from announced. Add one only if real released cases turn up that only Genius dates, and then flag
it `needs_review` like the YouTube one. A module
without `get_release_year` is simply never tried for Years — nothing elsewhere needs updating to
add or drop one.

**Capability is detected statically, like `MODULE_NAME`, for the same reason**: `discoveries_manager._module_defines_function()`
parses each file's AST looking for a top-level `def get_release_year`, so Settings' Years toggle/
reorder screens and `load_year_discovery_modules()` never import a module that doesn't have it (or
a disabled one) just to check. Consequence for anyone adding this to a new fetcher: it must be a
plain top-level `def`, not something assigned dynamically or wrapped — the static parser won't see it.

**Separate config and stats from the album fetchers.** `discovery_settings.py`'s
`load/save/reconcile_year_discovery_config()` persist to `discovery_modules_config_years.json`
(gitignored, like the album one) instead of sharing `discovery_modules_config.json` — deliberate,
since the set of year-capable modules differs from the album-capable set, and enabling/disabling a
fetcher for Albums shouldn't silently also toggle it for Years. Same story for stats:
`record_year_invocation()`/`record_year_success()` write to `discovery_fetcher_stats_years.json`,
kept apart from `discovery_fetcher_stats.json` so a fetcher's year success rate doesn't blend into
its album success rate in the Statistics menu.

**`fill_missing_years.py` batch-writes a year to every song sharing an album — unlike
`fill_missing_albums.py`'s one-write-per-song.** It only looks at songs that already have an
`album` filled in (a real album, or the `"Singles"` sentinel, `constants.SINGLES_ALBUM` — see below), groups them by
`(artist.name, album)`, and calls `discover_release_year()` once per group; every song in that
group gets `edit_db_entry(song, "year", str(year))`. This is a real asymmetry from the album flow
worth remembering if you're porting logic between the two: album lookups are inherently per-song
(each song can have a different album), but a release year is a property of the album, not the
individual track, so grouping avoids redundant lookups and guarantees every track on the same
album gets the same year. Songs whose `album == "Singles"` are excluded from grouping and looked
up individually by their own title (`is_single=True`) — grouping them under the literal string
`"Singles"` would be wrong, since that sentinel doesn't denote a real shared album. A group of one
song whose album equals its own title (`normalizer.compare()`, e.g. "Venture" / "Venture") is moved
to the singles too (also when they match only once a "ft./feat." guest credit is cut off the title: "Lottery" /
"Lottery ft. LU KALA". That's only the guest credit, not `truncate_at_word()`, which would also
cut at a comma and make "Dziękuję, że jesteś" a single from the album "Dziękuję"). That's how a single released under its own title is usually stored (~300 such
songs lacked a year when this was added), and it would otherwise miss the single-only lookups:
`youtube_fetcher.py` and the "A x B" collab retry.

**Before any module runs, years already in the DB are reused.** `_fill_years_known_in_database()`
groups the missing-year songs by `(artist_id, normalize(album))`, skipping `SINGLES_ALBUM`, and asks
`database_getter.get_known_album_year()` for a year recorded on another song of the same album. If
the album has several different years, the most common one wins, and the earliest on a tie. Those
years are written without review. This runs before the per-fetch cap, so it never uses up a slot
meant for a network lookup, and it's committed even if Chrome fails to start. Later, the manual
fill-in prompt prints up to `MANUAL_YEAR_SAMPLE_SONGS` random titles next to an album's label as a
hint which album it is.

**MusicBrainz picks a different API call depending on `is_single`.** For an album query it calls
`musicbrainzngs.search_release_groups()` (release groups carry `first-release-date`, which
recordings don't expose directly) filtered to `releasegroup:"<query>" AND artist:"<artist>"`. For a
single it reuses the `search_recordings()` shape `get_album_name()` already uses, reading
`date`/`first-release-date` off the chosen recording's release. If you add year support to another
fetcher, decide up front whether it can search by album title at all (some scraped sources' search
boxes behave differently for an album vs. a song query) — this isn't automatic.

**A stored `Song.spotify_url` beats every module.** `fill_missing_years.py` first calls
`release_year_from_stored_link(songs)` for each album group or single. It opens the stored page
via `spotify_fetcher.get_release_year_from_url()` and trusts it apart from the plausible-range
check, because the user picked the page. Why it exists: Spotify's search sometimes never shows a
release at all, and walking the artist's discography instead was tried and dropped. That page
lazy-loads as you scroll, has no grid view, and for a prolific artist (Cypis) still hadn't reached
2016 after 60–70 s of scrolling. The `artist:`/`album:` field filters in Spotify search returned
nothing.

**The album-mode year lookup in MusicBrainz skips by title, not by blacklist.** It walks the
release groups and skips any whose title doesn't resemble the queried album, using the same
`scaled_similarity_threshold()` check as `_validate_year_result()`. Without this, a compilation
ranked first would be returned, rejected by the manager, and hide the real album further down the
list. The album blacklist isn't applied here: the album name is already stored, so a blacklisted
word in it ("pop" in "SODA POP FANCLUB 4") says nothing. The single (recording) path still uses
the blacklist, to skip compilations and live albums the track also appears on. If no release group under the artist
matches, `_release_year_from_artist_recordings()` searches the artist's recordings on a release of
that name (`release:"…" AND artist:"…"`). That finds various-artists compilations such as
DakhaBrakha on the festival CD "TFF Rudolstadt 2011", which is credited to "Various Artists" and
so never shows up keyed on the artist. The album
path reports the *main* artist's credited-as name (`artist-credit[0]["name"]`, e.g. "Freeland") as
the matched artist. It doesn't use the artist entity's own name ("Adam Freeland"), because the
credited name is what the DB holds. It doesn't use the whole `artist-credit-phrase` either: its
guests ("Latto feat. LU KALA") failed the artist check. It also proves the artist is on the release, which simply
accepting any same-named "Various Artists" release group wouldn't.

**`google_search_fetcher.py`'s album-query handling is an unverified assumption.** The existing
code only ever searched `"{artist} - {song}"` and parsed a `music/recording_cluster` Knowledge
Panel. For `get_release_year()` on an album query, the attrid filter was widened to also accept
`music/album` (guessing Google's Knowledge Panel uses an analogous attrid namespace for an album
page, by the same naming convention as `music/recording_cluster`) — this has **not** been confirmed
against a live album Knowledge Panel. If Years lookups via this fetcher come back empty specifically
for real albums (but work for `is_single=True` singles, which still hit the already-confirmed
`recording_cluster` panel), check `debug.html` for the actual `data-attrid` value on an album query
before assuming the similarity/validation logic is at fault.

**`youtube_fetcher.py`'s years are only guesses, so the user reviews them.** Its
`get_release_year()` doesn't scrape music.youtube.com like `get_album_name()` does. It runs the
normal DB → YouTube `search_video_ytdlp()` matching (yt-dlp, no Selenium, no API quota) and reads
the date with `get_video_release_year()`, which prefers yt-dlp's `release_date`/`release_year`
(YouTube Music's "Released on") over `upload_date`. An upload date is often not the release date (a
live session, a reupload), so it returns `YearDiscoveryResult(needs_review=True, source_url=...)`.
`discover_release_year_result()` passes that flag through (`discover_release_year()` is the same
call reduced to just the int). `fill_missing_years.py` doesn't write flagged years straight away:
`_review_unsure_years()` lists them all at the end in one checkbox prompt, and the user ticks the
ones to change. Unticked years are written. A ticked one asks for a year, and an empty answer
leaves the song without one. It returns None for albums, because one track's video date says
little about the album. It's last in `DEFAULT_DISCOVERY_MODULE_YEAR_ORDER`, and an existing config
gets it appended at the end by `_reconcile_config()`. It reuses a stored `Song.youtube_video_id`
first: `_call_year_module()` passes the `Song` (singles only) to any `get_release_year()` that
declares a `song` keyword, checked with `inspect.signature`, so the other modules keep the plain
3-argument contract. A stored link that no longer resolves falls back to a search, and a song
marked `NO_VIDEO_SENTINEL` is skipped.

## Multi-artist songs (`additional_song_artists`)

Fetchers still get one artist string; nothing in a module changed. `discover_album_name()` (and
`discover_release_year()` for a single, via its `song=` kwarg) query the **primary artist first** —
one clean name finds a collab on most services better than a joined string — and only as a last
per-module retry the main-artist label "A x B" (feats excluded). Album lookups in Years stay keyed
on the primary artist alone: a guest on one track isn't the album's artist.

Validation had to change with it: querying "Sw@da" while the service credits "Sw@da, Maxim &
Niczos" fails plain similarity, so `_matched_artist_resembles()` also accepts a matched field that
contains any of the song's credited artists as whole words (after `normalize()`). Only for
multi-artist songs — single-artist validation is unchanged.

One more rule in `_matched_artist_resembles()`, for every song: a matched artist that is *equal*
to the query after `normalize()`, with anything from the first `(` on dropped, is accepted.
Services write Japanese/Chinese acts with punctuation and a native-script name in brackets
("Cody・Lee(李)" for "Cody Lee"), and plain similarity rates that 0.74, below the threshold, so
correct matches from MusicBrainz and Spotify were discarded. It's an exact match after
normalization, so "Lee" or "Cody Lee Band" still don't pass.

## Which fetchers report matches

- `music_brainz_fetcher.py` — reports the title/artist of the MusicBrainz *recording* that the
  chosen release came from.
- `itunes_fetcher.py` — reports the tracklist title / first credited artist it already scrapes
  from the song's own page to verify the match internally (see dedicated section below).
- `genius_fetcher.py` — reports the song-title portion of the matched URL slug (artist prefix
  stripped best-effort by `_title_portion_of_slug()`, since Genius slugs are `artist-title-lyrics`
  with no clean delimiter). The same stripped slug is what `title_matches_url()` scores the query
  title against when picking a search result: scoring against the full slug let the artist words
  dilute SequenceMatcher's ratio, so a short title by a long-named artist failed even on an exact
  hit ("cruisin" vs "childish gambino cruisin" = 0.45 < 0.6). A slug by a *different* artist isn't
  stripped, so it still scores low — that's what rejects same-title songs by other artists. On the
  song page, don't take the first `a[href*='/albums/']`: hidden nav/recommendation links (often
  other artists' albums) come first and have empty `.text` in Selenium, so the fetcher takes the
  first one with visible text.
- `google_search_fetcher.py` — reports the artist field from the Google Knowledge Panel when
  present; no reliably-extractable matched title from that source.
- `spotify_fetcher.py` — reports the track title/artist scraped from the matched track's own page
  (`entityTitle` / `creator-link`), falling back to what it already matched on the search-results
  row if the track page's markup doesn't have them.
- `youtube_fetcher.py` — reports the title/artist scraped straight from the matched row in the
  filtered "Songs" search results (no separate track page to visit; see below).
- `wikipedia_fetcher.py` was **not** upgraded — it already requires the query title to appear in
  the page/container text before accepting a result, so the marginal benefit was low, and its
  extraction logic wasn't touched.

## Don't remove the per-candidate blacklist checks

`music_brainz_fetcher.py` and `wikipedia_fetcher.py` both call `is_blacklisted_album()` *inside*
a loop over many candidate releases/headings, to filter out bad options while picking the best one
(batch filtering). That is a different job from the manager's blacklist check on the *final*
returned album (a last-resort net). Don't treat the per-candidate calls as redundant with the
manager's check and remove them — they serve a genuinely different purpose and are still needed.

## The shared headless browser (`src/utils/common/selenium_sessions.py`)

`open_global_driver()` / `close_global_driver()` manage **one** process-wide headless Chrome
instance, reused across `google_search_fetcher.py`, `itunes_fetcher.py`, `genius_fetcher.py`,
`spotify_fetcher.py`, and `youtube_fetcher.py` via `get_global_driver()` rather than each fetcher
opening its own.

Any call site that opens the driver **must** call `close_global_driver()` in a `finally`.
`fill_missing_albums.py` originally called it as a plain last statement after its fetch loop, so
an exception — or the Chrome process itself crashing — skipped cleanup and left an orphaned
headless `chromedriver` / `chrome` running indefinitely. This isn't just a resource nit: on the
user's machine one leaked instance's memory footprint contributed to a system-wide OOM that
crashed an unrelated desktop app.

Defense-in-depth already added (not a substitute for the `try/finally` at new call sites):
`close_global_driver()` clears its module reference in a `finally` so a `.quit()` failure on an
already-crashed browser can't wedge it, and an `atexit` hook calls it as a last-resort net.

### Chrome launch failures: snap Chromium, and `ChromeDriverLaunchError`

On Ubuntu, a snap-packaged `chromium`/`chromium-browser` runs Chrome inside its own mount
namespace that gives it a **private `/tmp`** (bind-mounted from
`/tmp/snap-private-tmp/snap.chromium/tmp` on the host). Chrome itself starts fine and DevTools
does come up, but it writes the `DevToolsActivePort` file into that private `/tmp` — invisible to
`chromedriver`, which polls for it from outside the snap sandbox. Selenium never sees the file,
waits out its timeout, and raises `SessionNotCreatedException: ... DevToolsActivePort file
doesn't exist`, even though nothing is actually wrong with Chrome. This only reproduces on a
machine where Chrome/Chromium is snap-installed (the common case on stock Ubuntu, since
`chromium-driver` was dropped from apt); it won't reproduce by reasoning about the code.

Fix: `_find_chrome_binary()` in `selenium_sessions.py` prefers a real, non-snap
`google-chrome-stable`/`google-chrome` binary (found via `shutil.which`) and pins
`options.binary_location` to it, falling back to `chromium-browser`/`chromium` (i.e. the snap)
only if Chrome isn't installed at all. If this bug resurfaces, check `which google-chrome-stable`
first before re-diagnosing from scratch.

Separately, `_build_driver()` wraps the Chrome/chromedriver launch and re-raises any
`WebDriverException` as `ChromeDriverLaunchError` — a driver-launch failure (missing browser,
this snap issue, etc.) used to be an uncaught exception that crashed the whole program via
`utils.common.debug`'s global `sys.excepthook`. Any call site that calls `open_global_driver()`
directly (not just `get_global_driver()`) should catch `ChromeDriverLaunchError` and fail that one
operation gracefully instead of letting it escape — see `fill_missing_albums.py` for the pattern.

## `google_search_fetcher.py` — two different failure modes that look identical

The fetcher depends on Google rendering a `music/recording_cluster` Knowledge Panel for the
query. A fresh automated browser session can fail to reach it for two *different* reasons that
both surface as the same "no album found" output but need different handling:

1. **EU cookie-consent interstitial** ("Before you continue to Google Search") — worked around by
   `selenium_sessions._build_driver()` preemptively setting a `CONSENT` cookie on the driver
   before any search runs.
2. **Bot-detection CAPTCHA** — Google redirects to `google.com/sorry/...` when the request
   pattern looks automated. Detected explicitly (`"/sorry/" in driver.current_url` right after
   the page loads); the fetcher bails with a clear log message instead of falling through to the
   misleading generic "no Knowledge Panel found" path.

Do **not** try to solve or evade the CAPTCHA (rotating IPs, mimicking human timing, auto-solving
the challenge) — that's bypassing Google's anti-bot system, not fixing a bug, regardless of how
the request is framed. If this fetcher starts failing consistently, check which of these two
states it's hitting *before* assuming the CSS selectors (`LrzXr` / `w8qArf`, hardcoded in
`_extract_value()`) rotted.

`google_search_fetcher.py` also writes the raw HTML response to `debug.html` at the repo root —
a large disposable scrape dump (gitignored, like `debug.log`). Not documentation; don't read it
for context or hand-edit it.

## `spotify_fetcher.py` — scrapes the public web player, not the official Web API

Deliberately does **not** use Spotify's official Web API (which needs a registered app's
`client_id`/`client_secret` and an access-token exchange). Instead it scrapes
`open.spotify.com/search/.../tracks` and the matched track's own `open.spotify.com/track/<id>`
page with the shared Selenium driver, the same way `itunes_fetcher.py` and `genius_fetcher.py`
scrape their sources — searching and viewing a track/album on open.spotify.com doesn't require
being logged in. This was a deliberate choice to get something working without first setting up
API credentials; swapping in the official API later (better data quality guarantees, no frontend-
markup fragility) would only mean changing this file's internals — `get_album_name(artist, title)`
and `MODULE_NAME` are the whole contract.

The page is a fully client-rendered SPA (a plain `requests.get()` returns an empty shell with no
song/album data — confirmed by inspecting the raw response), so this fetcher, like the other
Selenium-based ones, can only work by waiting for React to hydrate and then parsing
`driver.page_source`.

Two-step flow, mirroring `itunes_fetcher.py`'s search-then-follow-link pattern:

1. Load `.../search/<query>/tracks`, wait for `[data-testid="tracklist-row"]` rows, and pick the
   best-scoring row whose title *and* artist both clear `SPELLING_CHECK_THRESHOLD` against the
   query (`_find_matching_track()`). Live/alternate-version results with the same artist (e.g. "...
   - Live at Wembley Stadium") lose to the plain studio title purely because the studio title's
   title-similarity score is higher — there's no separate live/remix penalty like the YouTube
   matcher has.
2. Follow that track's `/track/<id>` page, wait for `[data-testid="track-page"]`, and read the
   album from the first `a[href^="/album/"]` inside it (`_extract_album_from_track_page()`), plus
   `entityTitle`/`creator-link` for the `DiscoveryResult` match-report fields.

All of `tracklist-row`, `track-page`, `entityTitle`, and `creator-link` are Spotify's own
`data-testid` attributes (not hashed Encore CSS classes), which tend to be more stable across
frontend deploys than class names — but this is still unofficial markup Spotify owes no
compatibility guarantee for. If this fetcher starts returning `None` for everything, check
`driver.page_source` for those attributes still existing before assuming the matching logic broke.

The two pure-parsing helpers (`_find_matching_track`, `_extract_album_from_track_page`) are unit
tested against static HTML fixtures in `tests/test_spotify_fetcher.py` — no live network/Selenium
in the test, consistent with the rest of `tests/` — but that only locks in the parsing logic
against the *fixture*, not against Spotify's real markup drifting out from under it.

## `itunes_fetcher.py` — scrapes music.apple.com's web player; rewritten 2026-09 when the JSON-LD it relied on disappeared

Originally parsed a `<script id="schema:song" type="application/ld+json">` tag on the song's own
page for a clean, structured `audio.name`/`byArtist`/`inAlbum.name`. Apple stopped shipping that
tag entirely (confirmed by inspecting a live page — it's just gone, not renamed), and the search
page's old `.track-lockup__title` CSS class was also gone, so the fetcher was silently returning
`None` for everything before this rewrite. It now reads stable `data-testid` attributes instead:
`non-editable-product-title` (album), `product-subtitles` (artist link(s)), and `track-title`
(tracklist rows) on the song's own page.

**Apple serves two different search-results DOM layouts for the same URL/query**, and this
fetcher has to handle both (`_iter_song_candidates()`):
- `track-lockup` / `track-lockup-title` / `track-lockup-subtitle` — a dedicated "Songs" section,
  closer to the old markup. Observed when the request's geo doesn't match the `/us/...` storefront
  in the URL (e.g. scraping from a non-US IP, which is the common case for this app's actual
  users) — Apple falls back to this layout instead of (or alongside) a region-mismatch prompt.
- `top-search-list-result` / `top-search-list-result-title` / `top-search-list-result-subtitle` —
  a mixed "Top Results" list (songs, albums, artists, videos interleaved); a row's type is only
  knowable from its subtitle text ("Song · Artist" vs "Album · Artist" / "Artist" / "Music Video ·
  Artist"). Observed when geo matches the storefront.
Which one a given run gets isn't something the fetcher controls, so both are tried every time
rather than picking one based on an assumption about the caller's network. If this fetcher starts
returning `None` for everything again, check which (if either) of these `data-testid` sets is
still in `driver.page_source` before assuming the matching logic broke.

**Apple now bakes "(feat. X)" credits straight into the visible track title** on the song's own
page (the old JSON-LD kept `audio.name` clean of them). `extract_from_itunes_soup()` compares
against the part before the parenthetical — the same `.split("(")[0].strip()` convention
`discover_album_name()` already uses to clean the query — otherwise a plain query like "Say So"
would never clear `SPELLING_CHECK_THRESHOLD` against an on-page title like "Say So (feat. Nicki
Minaj)".

**Single-only releases report no real album.** Apple appends `" - Single"` to the product title of
a release that never had a parent album (e.g. "Say So (feat. Nicki Minaj) - Single"). Rather than
inventing a new field to carry that fact through `DiscoveryResult`/`discoveries_manager`/the DB
(which would need a schema change), `extract_from_itunes_soup()` reports the album as the literal
string `"Singles"` (`constants.SINGLES_ALBUM`) — the same sentinel `wikipedia_fetcher.py` already returns when a song is found
under a Wikipedia discography's "Singles" heading (see `_find_song_under_heading()`). This needed
no changes anywhere else: `"Singles"` isn't on the album blacklist, and `matched_title`/
`matched_artist` are unaffected, so `discoveries_manager._validate_result()`'s cross-check still
runs against the real scraped title/artist, not the sentinel.

`_find_song_link()` / `_iter_song_candidates()` and `extract_from_itunes_soup()` are unit tested
against static HTML fixtures (both search-results layouts, a single, a multi-artist single, a
regular album track, and the mismatch/missing-data cases) in `tests/test_itunes_fetcher.py` — no
live Selenium/network in the test, same approach as `tests/test_spotify_fetcher.py` /
`tests/test_youtube_fetcher.py`. That only locks in the parsing logic against the *fixtures*,
not against Apple's real markup drifting out from under it again.

## `youtube_fetcher.py` — scrapes the public music.youtube.com web player, not the Data API

Deliberately does **not** use the official YouTube Data API (needs an API key and is quota-limited).
Instead it scrapes `music.youtube.com/search`, filtered to the "Songs" tab, with the shared Selenium
driver — the same public-web-player approach as `spotify_fetcher.py`. Unlike Spotify, the filtered
Songs list already renders artist *and* album inline per row, so there's no second "open the track's
own page" step: `_find_matching_song()` reads everything it needs straight out of the search results.

Flow:

1. Load `.../search?q=<query>`. A cookie-less driver gets redirected to a `consent.youtube.com`
   interstitial the first time any `youtube.com` page loads in that Chrome profile;
   `_reject_consent_if_present()` detects that redirect and clicks "Reject all" (the privacy-
   preserving choice), which sends YouTube back to the originally requested URL — re-encoded with
   `+` instead of `%20` for spaces, which is why the fetcher re-issues `driver.get(search_url)`
   with its own exact URL afterward rather than trusting the redirect target. This only costs an
   extra round trip the first time the shared driver visits YouTube in a given process; the
   resulting cookie makes every later call skip straight through.
2. Click the "Songs" filter chip (`ytmusic-chip-cloud-chip-renderer` containing a
   `yt-formatted-string` reading exactly "Songs") and wait for
   `ytmusic-shelf-renderer ytmusic-responsive-list-item-renderer` rows. The chip has no filterable
   URL/query-param of its own — YouTube Music drives it entirely with a client-side click handler —
   so this can't be short-circuited by requesting a different URL. Skipping this step and parsing
   the unfiltered page instead was tried and rejected: the default mixed results page wraps every
   individual hit (songs, videos, unrelated channel uploads, community posts) in its own
   `ytmusic-item-section-renderer`, with no reliable per-row way to tell a song apart from a video
   essay that happens to match on title text.
3. Parse each row (`_find_matching_song()`): title from the `.title` element's `title` attribute
   (not truncated by CSS ellipsis, unlike its rendered text), artist(s) from every
   `a[href^="channel/"]`, album from `a[href^="browse/"]` — YouTube Music always orders a row's
   links as artist(s) first, then album, so the href prefix alone (not position) is what
   distinguishes them. A multi-artist row (e.g. "NIGHTMARE" by "Cody Ko & Young Nut") renders as
   separate `channel/` links, one per artist, so the query artist is compared against each of them
   *and* against them joined back together (`" x "`, `", "`, `" & "`). The joined form is what makes
   a collab stored in the DB as one artist string match: "Sw@da x Maxim x Niczos" scores only
   ~0.4 against any single linked artist, far under the threshold, so before the joined candidates
   existed every such collab silently came back `None`. Joining also tolerates YouTube crediting
   fewer artists than the DB does ("Sw@da, Niczos" still clears the threshold against
   "Sw@da x Maxim x Niczos"). Pick the best-scoring row whose title *and* artist both clear
   `SPELLING_CHECK_THRESHOLD` against the query, same scoring approach as `spotify_fetcher.py`'s
   `_find_matching_track()`.

If this fetcher starts returning `None` for everything, check that `ytmusic-responsive-list-item-
renderer` and the `channel/` / `browse/` href prefixes still exist in `driver.page_source` before
assuming the matching logic broke — these are internal custom-element/routing names, not a
documented contract, so YouTube Music's frontend can change them without notice.

The pure-parsing helper (`_find_matching_song`) is unit tested against a static HTML fixture in
`tests/test_youtube_fetcher.py`, same no-live-network approach as `tests/test_spotify_fetcher.py`.

## Per-fetcher invocation/success stats (Statistics menu)

`discovery_stats.py` persists two counters per module id (filename stem, same key as
`discovery_settings.py` for the same rename-stability reason) to `discovery_fetcher_stats.json`
(gitignored, repo root): `invocations` and `successes`. `discoveries_manager._call_module()` bumps
`invocations` right before every `get_album_name()` call and `successes` only when
`_validate_result()` accepts what came back — so a module that returns a mismatched or blacklisted
album is invoked but not counted as successful. A module retried with untruncated text or an
artist synonym for the same song counts as a separate invocation each time, since each is a real
call.

Counters are loaded into an in-memory cache on first use per process (avoids re-reading the file on
every single invocation during a large "Fill missing data" run) but written straight back to disk
on every mutation, so nothing is lost if the process is killed mid-run.

The main menu's "Statistics" option (`src/menu/main_menu/statistics/__init__.py`) reads the
counters plus `load_all_discovery_modules_metadata()` (same source the Settings enable/disable
screen uses, so disabled modules still show their historical numbers) and prints invocations,
successes, and success rate per fetcher — no aggregation logic lives in the menu layer beyond
formatting, matching how `settings_menu` already merges config with module metadata for display.
