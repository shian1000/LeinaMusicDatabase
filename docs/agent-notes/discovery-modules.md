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
match better without them.

The catch: the bare `", "` stop word matches *any* mid-string comma-space, not just a trailing
credits list, so it also truncates legitimate titles/artists that happen to contain one — e.g.
"Bang, Bang" becomes just "Bang" before it ever reaches a fetcher's query. Rather than special-case
the stop-word list (which is inherently ambiguous — a comma could be a real credits separator or
part of the title), `discover_album_name()` compensates with a fallback: if a module's truncated
query comes up empty *and* truncation actually changed something, it retries that same module once
more with the original, untruncated (but still parenthetical-stripped) artist/title before moving
on. This runs before the synonym-retry step. Because it's implemented in the manager rather than
per-module, every fetcher gets the fallback for free.

A different, still-open gap in the same stop-word list, found while smoke-testing
`spotify_fetcher.py` against a real "Artist1 + Artist2" collab (`MRFY + Laibach - Poskočna`): `+`
isn't a stop word, so a plus-joined artist credit is never truncated and reaches every fetcher's
artist-similarity check as one unbroken string. Spotify credits that track to two separate artists
(`MRFY`, `Laibach`), and neither one alone — nor any fetcher's combined-artist string comparison —
resembles the full `"MRFY + Laibach"` query closely enough to clear `SPELLING_CHECK_THRESHOLD`, so
the album lookup fails across the board, not just on Spotify. This is a `truncate_at_word()` /
manager-level gap (the same class of problem as the comma case above, just a different separator),
not something `spotify_fetcher.py` itself can fix — left as-is rather than special-cased locally.

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

## Which fetchers report matches

- `music_brainz_fetcher.py` — reports the title/artist of the MusicBrainz *recording* that the
  chosen release came from.
- `itunes_fetcher.py` — reports the JSON-LD `audio.name` / `byArtist[0].name` it already scrapes
  to verify the match internally.
- `genius_fetcher.py` — reports the song-title portion of the matched URL slug (artist prefix
  stripped best-effort by `_title_portion_of_slug()`, since Genius slugs are `artist-title-lyrics`
  with no clean delimiter).
- `google_search_fetcher.py` — reports the artist field from the Google Knowledge Panel when
  present; no reliably-extractable matched title from that source.
- `spotify_fetcher.py` — reports the track title/artist scraped from the matched track's own page
  (`entityTitle` / `creator-link`), falling back to what it already matched on the search-results
  row if the track page's markup doesn't have them.
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
instance, reused across `google_search_fetcher.py`, `itunes_fetcher.py`, `genius_fetcher.py`, and
`spotify_fetcher.py` via `get_global_driver()` rather than each fetcher opening its own.

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
