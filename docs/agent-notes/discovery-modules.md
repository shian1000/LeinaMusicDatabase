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
per-module, every fetcher gets the fallback for free. The synonym retry needs the same pair. It
tries the truncated title first and then the untruncated one, once per synonym (`split_synonyms()`:
the column is a comma-separated list, and sending it whole, "Taras Chubai, Тарас Чубай", matched
nothing). Before, it only ever sent the stub,
so Ani Lorak's album "Там, де ти є" went to Spotify as "Ani Lorak" + "Там" and never matched,
though the synonym is the only name Spotify knows her by. The last stage of
`discover_release_year_result()` retries with `alt_script_query`, the query in its own script (see
normalization-and-matching.md → "Matching across alphabets"), first with the artist and then with
its synonyms. `discover_album_name()` has the same last stage for a song title. There the
artist's synonyms also count as Cyrillic context: "Ariel - V Krayu Magnoliy" has no Cyrillic at
all until the artist gets the synonym "Ариэль", and MusicBrainz then finds "Ариэль - В краю
магнолий" only with the Cyrillic artist too.

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

## Fallbacks when every fetcher is empty (YouTube link, soundtrack artist, singles markers)

First, `album_from_stored_youtube_link()` reads the album off the song's own `youtube_video_id`
(`manage_youtube_playlists.get_video_album()`, yt-dlp's `album`/`track`/`artist` fields). Only
official "Provided to YouTube" uploads carry them, but those name albums no service's search
knows ("Злипні - Глюки" -> "Ґрунт", credited "Zlypni, Данило Грицак"). It's the last resort and
not a step before the modules because it costs a yt-dlp call (seconds) per song, and is cheap only
where everything else failed. Validated like a fetcher result, retried with each artist synonym as
the queried name; not counted in `discovery_stats` (not a module). yt-dlp must be on `PATH` (the
venv's, as for the Years lookups) - a script that runs the venv's python without activating it
gets nothing here.

Game/anime soundtracks are often stored with the album in the artist field ("Tekken 5 OST -
Ground Zero Funk"), and no fetcher knows the individual track. If every module (with all its
retries) comes up empty and `text_utils.is_soundtrack_artist()` matches one of
`SOUNDTRACK_ARTIST_MARKERS` (`constants.py`) as a whole word in the artist name, `discover_album_name()`
returns "<work> OST", the one form of a soundtrack album (`utils/common/soundtrack_names.py`; the artist name
as it is when the marker isn't at its end, "Noir OST I"). A name without a marker ("Chrono Cross
Music", or the start of it being a work the catalog has an OST of, `soundtrack_albums.guess_soundtrack_album()`)
is only offered, unticked, at the end of Albums. Manage database -> "Resolve soundtrack album names"
brings stored spellings to that form. Deliberately only after the loop — a fetcher may still
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

## A song with its artist and title swapped

Stored as "Dobro Te Čuvaju" - "Džoni Midnajt Trio" (a YouTube title in "Title - Artist" order), a song
is searched for under an artist nobody knows, so every module comes up empty. When `discover_album()`
finds nothing, `fill_missing_albums.py` asks `artist_identity.may_be_swapped()`: an artist with another
song in the database counts as known and is not tried, nor is a multi-artist song or one named like
its artist. Otherwise `discover_album_after_swap()` asks each module the other way round, with the
usual `_validate_result()` and counted in the statistics, but without the last-resort fallbacks, which
say nothing about the order of the names.

A find changes nothing yet. `utils/ui/album_swap_review.py` lists them in one checkbox list at the end,
before the manual fill-in, none ticked, since a real song may read the other way round too ("Rain -
Madonna"). A ticked song gets `swap_song_names()` (a row named like its title takes it, else its own
row is renamed) and then the album; an unticked one stays as it was and goes on to the manual prompt.
The extra lookups cost up to one more pass over the modules per missed song with a one-song artist.

## An album only Google knew

Google's panel gives the recording's "first album", which may be a compilation, and neither the
album's artist nor the matched title, so the manager can check only the artist and the various-artists
filter of Spotify / iTunes can't apply ("Folk Hit Maraton 4"). Its result is therefore
`needs_review`: `discover_album()` keeps asking the remaining modules and any sure result wins, then
the last-resort fallbacks (a "cover" still goes to Singles), and only then the guess is returned.
`fill_missing_albums.py` saves nothing for it until `utils/ui/album_guess_review.py` (none ticked);
an unticked song goes on to the manual prompt.

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
    track_number: Optional[int] = None    # the song's place on that album
    disc_number: Optional[int] = None     # None also = a one-disc release
    load_tracks: Optional[Callable[[], list[AlbumTrack]]] = None
    needs_review: bool = False
    year: Optional[int] = None            # that release's year (the album's, a single's own)
    album_type: Optional[str] = None      # constants.DISCOVERY_ALBUM_TYPES only
    album_ids: dict = field(default_factory=dict)  # albums column -> the release's id there
```

A fetcher's `get_album_name()` can return this instead of a bare `str`. `matched_title` /
`matched_artist` must be whatever the module actually found on the page/response it scraped the
album from — never an echo of the input query, since echoing the input would always "match" and
defeats the whole point. Leave a field `None` if the module has no reliable way to extract it.

`discoveries_manager._validate_result()` then, for every module call (upgraded or not):
- Wraps it in try/except — a crashing module is logged and skipped, not fatal to the whole run.
  (Before this, fetcher exceptions propagated uncaught all the way to the top-level crash handler;
  they accounted for a large share of `debug.log`'s crash entries.)
- Rejects non-string, empty, or blacklisted albums (`src/config/blacklist.txt`, the owner's list:
  `[exact]` = the whole title, `[substring]` = whole words anywhere in it). A common word, a place
  or a name belongs in `[exact]` or nowhere, never in `[substring]`: "rock", "paris", "morrison"
  there rejected "Rock or Bust", "Paris", "Morrison Hotel" (2026-10, `tests/test_albums.py` keeps
  a list of real titles that must pass). Compilation series go in by their series name
  ("party hard", "megahits", "now yearbook"), not by a "name + year" shape: the catalog's real
  albums have it too ("Anawa 2020", "Acoustic 1993", "Demo 2004", every "Męskie Granie 20xx";
  checked 2026-11-12). Check a new `[substring]` entry against every stored album title first.
  Compilation words go in for every language of the catalog, not just English: old ex-Yugoslav
  songs got "Kompilacija Bijelo Dugme" (iTunes), "Najbolje Od Tajči" (YouTube Music), "50
  Originalnih Pjesama", "SingStar Party" (a karaoke game's songs, typed a plain Album; 2026-11-19; also "antologija", "reizdanje" = reissue, "przeboje",
  "коллекция", "grandes éxitos", ...). A span of years ("Najbolje pesme 1980-1988", "Zvuk
  osamdesetih 1980-1989") is rejected by `is_blacklisted_album()` itself, not by an entry: every
  stored title with one is a retrospective. Such a name already stored for the artist still
  passes (the `artist_has_album()` exception below) until the owner corrects it.
- If `matched_title`/`matched_artist` were provided, independently checks their similarity against
  the *query* — not the module's own opinion of whether it matched — using
  `scaled_similarity_threshold()` (the same short-string-aware threshold used elsewhere in the
  app), and discards the result if it doesn't resemble what was actually searched for.

Plain `str` returns still work (they just skip the similarity cross-check), so an un-upgraded
module degrades gracefully rather than breaking.

### Track positions and the per-run tracklist cache

`discover_album()` returns an `AlbumMatch` (album + track/disc number); `discover_album_name()` is
its text-only wrapper. Fill missing data → Albums writes the position into `songs.track_number` /
`disc_number` only when the song has none, so a hand-set number is never overwritten, and never
on "Singles". MusicBrainz (the release's `medium-list` holds just that track), iTunes and the
Spotify API report it, free from the search hit.

### Everything else the album's page says

A fetcher reports whatever else the answer it found the album in carries, at no extra request
(owner's request, 2026-11-17: once we're on the page, take what's sure there): `year`,
`album_type`, `album_ids`. The manager drops an implausible year, a type outside
`DISCOVERY_ALBUM_TYPES` and ids of unknown columns; the `AlbumMatch` keeps the module id
(`source`). Fill missing data → Albums (`_save_album_details()`, after every save, one flush so
the songs are on their album rows) fills only empty fields:
- **The year** goes onto the song only from a module enabled for Years too
  (`enabled_module_ids(YEARS)`, owner's choice) - and the album row's year wins over it, as Years'
  `get_known_album_year()` would. Years then skips those songs, so it no longer opens the same
  pages again. Who gives one: MusicBrainz (the earliest date among the hits' releases of the
  picked release group - the picked edition may be a remaster, and the search hit has no
  `first-release-date`), the Spotify API (`album.release_date`), Spotify's track page (its
  `release-date`, the one Years reads too). iTunes doesn't: it has no `get_release_year()`, and its
  `releaseDate` is often a reissue's.
- **Type and ids** go onto the album row, never for "Singles" (its one-song row isn't the release
  the source named). Never `ALBUM_TYPE_SINGLE`: a source's single is a release titled after the
  song, stored as an ordinary album, while single-type rows are what title matching skips.
  iTunes' " - EP" suffix is cut off the name and gives the EP type.

`load_tracks` is lazy on purpose: a tracklist is one more request (`get_release_by_id`, ~1 s with
the rate limit). `AlbumTrackCache` calls it only while another song of the same artist id still
waits in the run. Before that, the user's log showed every song of one album being searched module
by module, synonyms included. The following songs of that artist are then answered from the
tracklist without calling any module. MusicBrainz offers a loader only for an Album/EP (a single's
other tracks are B-sides, not the album). Rules that keep the cache from giving a wrong album:
- **Equal `_track_key()` only, never similarity.** The key is the duplicate-song key plus remaster
  annotations dropped, so "Song (Remix)" stays apart from "Song". Two titles can be close and still
  name different songs on one album: "The Wired World" vs "Lain: The Wired World".
- **The track's own credit must resemble the artist or a synonym.** A soundtrack's tracks by
  others are left out.
- **It lives for one run only and stores nothing.** Spotify / iTunes could fill `load_tracks` the
  same way from their album pages; they don't yet. Tests: `tests/test_album_track_cache.py`.

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
reorder screens and `load_discovery_modules(YEARS)` never import a module that doesn't have it (or
a disabled one) just to check. Consequence for anyone adding this to a new fetcher: it must be a
plain top-level `def`, not something assigned dynamically or wrapped — the static parser won't see it.

**One config and one stats file per kind of lookup.** `discovery_settings.KINDS` lists the kinds
(`ALBUMS` → `get_album_name`, `YEARS` → `get_release_year`, `LYRICS` → `get_lyrics`), each with its
own config file (`discovery_modules_config[_years|_lyrics].json`, gitignored) and stats file
(`discovery_fetcher_stats[_years|_lyrics].json`). `load/save/reconcile_discovery_config(...,
kind)`, `record_invocation/record_success(module_id, kind)` and `load_discovery_modules(kind)` take
the kind (default albums). A module takes part in a kind only when it defines the kind's function,
also for albums since 2026-11-12 (a lyrics-only module mustn't be called for an album). Kept apart
on purpose: enabling a fetcher for Albums shouldn't toggle it for Years, and a fetcher's year
success rate shouldn't blend into its album one. A new kind is a row of `KINDS` plus its constants,
never a third copy of the functions. `tests/conftest.py` points every kind's files at its temp
folder: a test that patched the old module-level constants once rewrote the owner's real album
config (2026-11-12).

**`fill_missing_years.py` batch-writes a year to every song sharing an album — unlike
`fill_missing_albums.py`'s one-write-per-song.** It looks at every song without a year (until
2026-11-20 only those with an album; a song without one is now looked up as a single), groups the
ones with a real album (not the `"Singles"` sentinel, `constants.SINGLES_ALBUM` — see below) by
`(artist.name, album)`, and calls `discover_release_year_result()` once per group; every song in that
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

**A project's recording is looked up by its project, never by its title.** Albums skips
projects' recordings on purpose, so they have no album, and their title alone finds the studio
original's year: measured 2026-11-20 on the catalog's 10 songs without a year, all such -
"Liūdni slibinai - Pieno Fabrikėlis" (Kaunas Acoustics) got the studio song's 2012, "Lao Che -
Automaty" (Męskie Granie) Google's 2019; the title plus the project gave Spotify's 2007 "Unedited"
reissue for Nirvana's MTV Unplugged. The project as the album query gave the edition: "Nirvana -
MTV Unplugged" 1994 (Google), "Lao Che - Męskie Granie" 2013 (MusicBrainz's "Męskie Granie 2013",
via the artist's recordings on it). So a project's recording with no album (or "Singles") is
grouped by artist + project and looked up in album mode with the project's name, `held=False`
(`year_after_the_song()` would call the edition a reissue of the studio song), and the year always
goes to the unsure-years review (an artist may have played several editions; fact `project`).
Before that, offline, an artist naming its edition ("Męskie Granie Orkiestra 2019", 7 of the 10)
gives that year at once (`_fill_years_known_in_database()`).

**Before any module runs, years already in the DB are reused.** `_fill_years_known_in_database()`
groups the missing-year songs by `(artist_id, normalize(album))`, skipping `SINGLES_ALBUM`, and asks
`database_getter.get_known_album_year()` for a year recorded on another song of the same album. If
the album has several different years, the most common one wins, and the earliest on a tie.
A soundtrack album (a `SOUNDTRACK_ARTIST_MARKERS` word in the *album* name, e.g. "Persona 5 Strikers
OST") matches across all artists, because each of its tracks tends to sit under a different
"artist" (composer, game title, YouTube uploader) while the album name is specific. Every other
album keeps the same-artist rule, since "Greatest Hits" or "Persona" (Selah Sue) by two artists
aren't one album. With no song of that album dated, "<work> OST" falls back on the songs of an artist
named exactly like the work (`soundtrack_work()` + `work_key()`); only exactly, since a fan's
"Serial Experiments レイン" (2024) starts like "Serial Experiments Lain" (1998). Accepted side effect: a cover filed under the original OST's album name gets the
OST's year. Those
years are written without review. This runs before the per-fetch cap, so it never uses up a slot
meant for a network lookup. Later, the manual
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
`title_resembles()` check as `_validate_year_result()`. Exact titles are tried before ones that
only add a bracketed subtitle. Without this, a compilation
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
accepting any same-named "Various Artists" release group wouldn't. The artist may only be a guest there
("Miuosh x Zespół Śląsk feat. Ralph Kamiński" on Miuosh's "Pieśni Współczesne"). So
`_credited_name_like()` reports the credited name closest to the queried artist, not the first
credit, which would fail the manager's artist check. The plain title is again tried before a
subtitled edition ("… (live at Cavatina Hall)", a year later).

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
`get_release_year()` doesn't search music.youtube.com like `get_album_name()` does. It runs the
normal DB → YouTube `search_video_ytdlp()` matching (yt-dlp, no API quota) and reads
the date with `get_video_release_year()`, which prefers yt-dlp's `release_date`/`release_year`
(YouTube Music's "Released on") over `upload_date`. An upload date is often not the release date (a
live session, a reupload), so it returns `YearDiscoveryResult(needs_review=True, source_url=...)`.
`discover_release_year_result()` passes that flag through. `fill_missing_years.py` doesn't write flagged years straight away:
`_review_unsure_years()` lists them all at the end in one checkbox prompt, all ticked, and like
every other checkbox review the ticked years are written. An unticked one asks for the right year
(1900 to next year), and an empty answer leaves the song without one. The same review takes a year an imported title named ("Legenda (1991)", from the import's `SongEntry` hints) when no module found one - only when Years runs with the import's `entries` (`fill_missing_years(songs, entries)`). Albums are looked up too (one track's video date says little about
the album, but the year goes through that same review anyway, so a wrong guess costs one tick). It's last in `DEFAULT_DISCOVERY_MODULE_YEAR_ORDER`, and an existing config
gets it appended at the end by `_reconcile_config()`. It reuses a stored `Song.youtube_video_id`
first: `_call_year_module()` passes the songs being looked up (the single, or the album's songs
via `discover_release_year_result(album_songs=...)`) to any `get_release_year()` that declares a
`songs` keyword, checked with `inspect.signature`, so the other modules keep the plain 3-argument
contract. For an album the first stored link that resolves wins. A stored link that no longer
resolves falls back to a search (for an album: "artist + album name"), and a single marked
`NO_VIDEO_SENTINEL` is skipped.

## Lyrics lookups ("Fill missing data -> Lyrics") and `get_lyrics()`

```python
def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]
```

Unlike albums and years, a lyrics module hands back **every** candidate it found (a list; one result
or None also work) and `discoveries_manager.discover_lyrics()` picks: LRCLIB's search order isn't by
fit. A module that **can't be asked** (no network, rate limited) must **raise**: `discover_lyrics()`
returns a `LyricsSearch` with `match`, `missed` (modules that answered without a fitting text -
remembered per song and module in lyrics.db) and `failed` (raised - remembered nowhere, asked again
next run). On 2026-11-12 LRCLIB answered 429 to searches every 0.3 s and the module returned [],
so ~25 songs it knows were stored as "not found"; it now waits out a 429 (`Retry-After`) and
searches once a second.

A candidate counts when its `matched_title` is one of `song_titles(song)` (other titles, the Latin
form and the version's marked title included) and its `matched_artist` passes
`_matched_artist_resembles()` (synonyms, credited artists): that's **sure**, saved at once. A
title that only resembles one (`title_resembles()`, a bracketed subtitle allowed) is for the
owner's review at the end (`utils/ui/lyrics_review._review_unsure()`, equal titles ticked). So is
a text mostly (`LYRICS_OTHER_SCRIPT_SHARE`) in a script the song's stored language isn't written in
(`language_names.written_otherwise()`, `LyricsMatch.other_script`, unticked there): LRCLIB's
"Fisz Emade - Serce" held Jandro's Russian "Из Детства" under the right names, so the next
module's sure text wins. Latin never counts (romanized lyrics are common), nor an unknown language.
Measured on 2026-11-20: 2 of the 5,230 stored texts with a language - that one, and Punjabi written
in Devanagari; langdetect's reading instead of the script flagged 110, nearly all Croatian / Serbian /
Bosnian or close-language confusions. The
length (ours: `youtube_video_durations` by the song's link, else its longest `files.db` file -
`lyrics_store.song_durations()`) does **not** decide whether a text is the song's: it's mostly a
music video's length (AC/DC "Touch Too Much": video 326 s, the recording 266 s), and the words are
the same. It decides only `synced_fits` (within `LYRICS_DURATION_TOLERANCE_S` = 3 s, or a length
unknown): otherwise the synced lines are dropped and the plain text kept. Ranking: sure, then a
text over an "instrumental" flag (LRCLIB flagged a vocal remix of "Crookers - Jump Up" so), then
fitting synced lines, then the closest length. The first sure match with words ends the search; a source's "instrumental" doesn't (LRCLIB's for John Legend's "Green Light" hid Genius's words until 2026-11-18). Before any check a found text is read for what it is
(`_read_as_meant()`): only "Instrumental" is a claim of none, only addresses, signs and times (a download
site's tag in an mp3's lyrics frame - "www.t.me/pmedia_music" stood as 10 songs' lyrics -, LRCLIB's blank lines)
no text at all. Each module
is asked `_lyrics_queries()` in turn until a candidate fits: the artist's name, each synonym, the
parts of a joined name ("Alice Cooper & Bee Gees": Genius has "Alice Cooper") and the song's main
artists, each with the title, the title without its brackets and each other title ("Studzianki" is
"W moim ogródecku" on Tekstowo); for a cover, last, the covered artist with the original's title
(`covered_title`, else the title) - its words, and since the recording differs, rarely its times.
A translated cover (`songs.cover_translated`, migration 0026) is never asked so, nor matched by
`covered_title`: "Dav1d - It's My Life", a Russian cover, got Bon Jovi's English text that way, and
the owner had to reject every module for it.
A duet is also asked as the sites write it, its main artists joined by "&" ("Anna Maria Jopek &
Branford Marsalis" - Fajnanuta's address holds both), and last a medley's songs one by one
("Steel for Humans / Lazare"); a text found for one of them is only reviewed, being part of ours.
On the title without its brackets: a credit like "ARMANI TOP (prod.bovskey)" finds nothing on LRCLIB or Genius, which have
"Armani Top" (40 such titles on 2026-11-12). Titles are compared with and without brackets on
either side too - a version's marker isn't in its title any more (`songs.version_kind`), so what's
left in brackets is a credit or a note, not other words.

`from_song_file=True` (the `library_tags_fetcher`, USLT frames of the song's own mp3 files via
`files.db`) needs no check: the file is already matched to the song. A version with nothing found
gets its original's plain text (`lyrics_store.original_lyrics()`, never the synced lines). A
candidate with `instrumental` (LRCLIB and Genius both say so) isn't saved at once: the run ends
with a checkbox list of those (ticked) and the songs nothing had words for (unticked), and the
ticked ones get `language` "Instrumental", never looked up again. MusicBrainz was tried for this
and doesn't help (instrumental recordings mostly have no work at all); Spotify's
"instrumentalness" (audio features) is closed to new apps since November 2024.

Modules: `library_tags_fetcher` (local, first), `lrclib_fetcher` (`/api/search`, no key, synced
lines and lengths), `genius_lyrics_fetcher` (Genius's own site search `genius.com/api/search/song`
- JSON, no key, unlike `genius_api_fetcher` - then the lyrics page of at most
`GENIUS_LYRICS_PAGES_MAX` hits resembling the song: `div[data-lyrics-container]` without its
`data-exclude-from-selection` header and the "[Verse 1]" labels; a title's appended Latin
spelling, "Гасі (Hasi)", dropped; with no resembling hit, searched again as "Artist (POL) Title",
then "Artist (PL) Title", the country from `artists.origin` as ISO alpha-3, then alpha-2 - Genius
writes either ("Arab (POL)", "Dorina (HU)") to tell apart artists of one name:
"Arab Moon" finds only "Arab Money"), `musixmatch_fetcher` (the page `musixmatch.com/lyrics/<artist>/<title>`,
names as `slug()` makes them - letters of any alphabet kept - then without diacritics, the site
redirecting a near spelling; the text and the names in `__NEXT_DATA__`'s `trackInfo`; its search
answers 403, so there is none) and `lyricstranslate_fetcher` (`lyricstranslate.com/en/<Artist>-<Title>-lyrics.html`,
no diacritics, words capitalized; a song with only a translation has no such page, so then
`<artist>-<title>-english` in lowercase, whose `#original-lyrics` holds the original; names from
`og:title`, stanzas from `#song-body div.par`; strong
for Balkan / Slavic songs; non-Latin titles get transliterated slugs there, which it can't guess).
None of the scraped ones has lengths or synced lines; their terms don't allow reading the pages -
added at the owner's choice (2026-11-12) for the private catalog. `tekstowo_fetcher`
(`tekstowo.pl/<artist>/<title>`, lowercase, no diacritics, "ł" -> "l"; names from its `h1`, the
text from `#songText .inner-text`; a Cyrillic name also as the site writes it in Latin letters,
`latin_slug()`: "Крутится Земля" -> "krutitsa-zemla" - its search needs JavaScript) is the Polish one. `fajnanuta_fetcher`
(`fajnanuta.pl/tekst-piosenki-tlumaczenie/<artists>-<title>.html`, Tekstowo's slugs; names from
`<title>`; the text runs through every `div.arttxt`, an advert between them, without the "… TEKST
PIOSENKI" headings and up to the first `<p>` / `<div>` inside - the page's own blurb "<title> w
wykonaniu …", all a page without lyrics has, which the first block alone saved as lyrics for 9
songs until 2026-11-17; its note "Ta piosenka jest utworem instrumentalnym" / "Instrumental" is an
instrumental claim, not words) has Polish songs
Tekstowo lacks. `cuspajz_fetcher` (`cuspajz.com/tekstovi-pjesama/pjesma/<artist>/<title>.html`,
Tekstowo's slugs with "đ" as "d"; a page it lacks redirects to its index, which has no `<title>` "A — T —
Tekst", so that counts as nothing found; the text in `article p.text`) is for the songs of the former
Yugoslavia. Not added: Boomplay (403 without a browser). For what no module finds, Song actions → "Lyrics" takes a pasted text, or reads a page the owner
found (`lyrics_pages.lyrics_from_page()`): a site a module reads goes through that module's
`page_lyrics()` / `track_data()`, any other the page's block holding the most lines with no block
inside it (`<br>` and newlines alike: lyricsondemand's `div.lcontent`, altwall's Windows-1251 `<p>`,
eggs.mu's `whitespace-pre-wrap` paragraph) - a one-off, never a site the lookup asks. Cloudflare's
wall (versuri.ro) and lyricfind (403) can't be read without a browser: paste those. The
page-reading modules share `fetch_page()` / `text_lines()`; the lookup also asks the title's Latin
form (LyricsTranslate's addresses are Latin), and a found title's dashed note ("... – szanta
narciarska") counts like a bracket - unless it names a version the song isn't (`read_version()`: "最果て
(Instrumental)" isn't "最果て"). A page of one `<p>` per stanza (spiewnikniepodleglosci.pl) gives the
largest block with the blocks of its kind right beside it. A poem of one `<div>` per line, an empty one between stanzas
(poetryfoundation.org), is read as that block's lines; the longer of the two readings wins. Sites whose page the generic reading gets wrong have a reader of their
own in `lyrics_pages._readers()`: pesna.org (`div[id^=lang-]`), sonichits.com (`#dLyrics`),
teksty-pesenok.pro (`#music__text`, its translation beside it), alternatifim.com (`.sarkisozu`), tekstovi.net
(`section.lyrics`) and any
Blogger blog (`blogspot.com`: `.post-body`, else the sidebar's tag cloud wins) - keyed by the domain a
site is under (`_reader_for()`); their `<br>`s become new lines
(`_text_of()`), and a text whose every "stanza" is one line was only spaced out
(`text_lines()` joins it). In a 40-song sample (2026-11-12)
LRCLIB knew 29 (27 synced); the misses were game soundtracks (instrumental), niche Ukrainian /
Polish songs and data errors (a swapped artist and title).

## Language lookups ("Fill missing data -> Languages") and `get_language()`

The fourth kind (`discovery_settings.LANGUAGES`), `LanguageDiscoveryResult`: the language as
stored (`language_names.clean_language()`), `confidence` (a guess, 0-1) or None when the source
says it outright, `from_song` when read from the song's own data, `never_sure` for a source too
often wrong to save without the owner. Four modules:

- `stored_lyrics_fetcher` (`LOCAL = True`, no network): the song's text in lyrics.db read by
  langdetect (seeded so a text always reads the same) with the owner's rules
  (`language_names.song_language()`, agreed in a review of the automation's first run,
  2026-11-19). The alphabet and the letters narrow the field - they don't decide alone (a Russian
  text may come in Latin letters): each script with `LANGUAGE_SECOND_SCRIPT_SHARE` (0.4 - 0.2 gave
  "Japanese / English" to songs the owner marks Japanese) of the letters is read on its own (a
  hangul / kana / kanji character counts as two letters), Cyrillic only among Cyrillic languages
  with `romanize.cyrillic_language()`'s letters (ў, ї/є/ґ, ђ/ћ), ѓ/ќ/ѕ Macedonian, ө/ү Mongolian
  (Kazakh with ә/ғ/қ/ң), Hebrew script with Yiddish marks Yiddish; in Latin ŭ is Belarusian, ś
  beside several č/š/ž (no ą/ę/ż: a Polish text holds names with č) Belarusian with ł, Montenegrin
  with đ. Credit lines ("Lời:", "Writer(s):") are dropped, under 80 letters tells nothing. Then
  the song: kana / kanji in its names or an artist singing mostly Japanese make a romaji
  misreading Japanese; `LANGUAGE_RARELY_RIGHT` (Somali, Swahili...) is never sure, nor is a reading in a
  language the title's script doesn't write (`may_be_translation()`: LRCLIB and Genius held English
  translations of Ado's "ビバリウム" and Brutto's "Капитал"; the review list says so). Serbian /
  Croatian / Bosnian are politics more than form (the owner): `YUGOSLAV_COUNTRIES` - made in
  Yugoslavia = Serbo-Croatian, after the country left (Croatia 1991, Bosnia 1992, Serbia 2006) its
  language, Montenegro's = Serbian unless its letters (ś, ź, с́, з́) say Montenegrin; the country
  from the artist's origin, else its other songs' language, else ekavian words (Serbia); no
  country and up to 1990 = Serbo-Croatian. Neither known: nothing is guessed
  (`needs_origin`) - the screen lists those songs at the end and asks to type them now or look
  them up again once the artists' origins are known; a site's Serbian / Croatian / Bosnian answer
  goes through the same rule (`language_review._country_rule()`). Measured on the owner's 2,551 songs with a language and
  lyrics: 95.4% right, 97.2% of the sure ones (langdetect alone: 94.5% / 96.8%; `lingua` was 95.5%
  for 292 MB, not worth it).
  Second round (2026-11-20, after a review of 378 of the automation's 2,626 readings: no mistake
  in English or Polish, 8 in rare languages), each rule measured on the owner's labels: a text
  holding a translation isn't read (`looks_like_translation()`: "(перевод)" in the first lines,
  or 90% of 12+ lines alternating script / ASCII-vs-accented Latin - LRCLIB's "Бумбокс - Eva",
  Vietnamese under "Samurai - Never Fade Away"; none in the catalog's texts once those went);
  each line counts once (a chorus doesn't outweigh the verses; a text left under 80 letters is read
  whole but never sure - "I'm just a kid" ×20 had been Estonian); Latin and Cyrillic parts are
  also read stanza by stanza - a language most of the letters are in replaces the whole text's
  reading, unsure ("Sassja - Sad Tune"'s English intro), one with 40% adds itself ("Градусы -
  Режиссёр"'s English verse) -, a stanza misread as a neighbour of the main language
  (`LANGUAGE_NEIGHBOURS`: Slovenian in Serbo-Croatian) counting for it, and a Cyrillic stanza voting
  only with its language's letters (`_CYRILLIC_PROOF`: a short Belarusian stanza without і / ў
  read as Russian); `LANGUAGE_MARKS` for languages langdetect lacks (Kashubian's ò ù ë - sure -,
  Crimean Tatar's q, Cape Verdean Creole's words - unsure); a Latin "i" in Cyrillic words is
  Cyrillic (SUNSAY); song_language(): romaji alone read as an African language is Japanese
  (`LANGUAGE_ROMAJI_ONLY_MISREADINGS`, 75% romaji words, unsure), Slovenian by a Croatian artist
  Kajkavian (unsure). Result: 95.6% / 97.3% (sure); of the 29 songs it changes against a label, 20
  better and 9 "worse" that are mostly real mixes the owner labelled with one language (Youssou
  N'Dour - 7 Seconds as "English / French") or wrong labels (Бумбокс - Этажи is Russian). Invented
  languages (Era, Adiemus, Vangelis) are never read: no rule tells them (owner).
  Third round (2026-11-21, after the owner reviewed a dry run of the 807 songs without a language:
  of 108 sure answers 14 wrong), measured the same way - 97.05% / 98.41% before (the labels had
  been corrected since), 97.17% / 98.49% after, 3 better, 1 more unsure, none worse: a line
  holding its translation after "^" is read without it (`LANGUAGE_INLINE_TRANSLATION_MARK`, LRCLIB's
  Bad Bunny - NUEVAYoL as English); `LANGUAGE_MARKS` may take any reading (neighbour None) and a
  pattern - Icelandic's þ (read as Hungarian / Norwegian), Breton's c'h (as Indonesian / Welsh),
  Guadeloupean Creole's words in a French reading (unsure); `LANGUAGE_PROOF_WORDS`: a reading in
  Hungarian, Slovenian, Indonesian, Tagalog or Romanian - where langdetect puts languages it lacks
  (Altai, Kashmiri, Punjabi, pseudo-Latin) - is sure only with that language's own words, and
  Slovenian without them (by a non-Croatian artist) is Serbo-Croatian, so the country-and-year rule
  decides (Milan Stanković - Ovo je Balkan); `may_be_translation()` also catches a Cyrillic title
  with letters the language lacks (і / ї / є in a Russian one), which also keeps such a proposal
  unticked in the review. Real mixes read with one language (L'ENTOURLOOP's English chorus over
  Hungarian verses) stay so: the owner chose 40% for a second language.
- `musixmatch_fetcher`: the page's `lyrics.language` / `languageDescription` - what Shazam shows,
  its lyrics are Musixmatch's - or `isInstrumental`.
- `music_brainz_fetcher`: the recording's work (`inc=work-rels`) `languages` ("zxx" = no words),
  through `mb_get()`, not musicbrainzngs; not every recording is linked to its work, so up to three
  titled like the song are looked up. `never_sure` since 2026-11-21: 18 of its 22 sure answers in
  the owner's dry run were right, 23 of 26 on his labels - the language is the work's, so an
  instrumental cover (Lindsey Stirling - Phantom of the Opera) or a song another work shares a
  title with gets it (Madonna - Faz Gostoso as English, Bregović's Romani "Ederlezi" as Greek).
- `youtube_language_fetcher` (last, owner's choice 2026-11-20, for the ~625 songs with neither
  lyrics nor a language): `snippet.defaultAudioLanguage` of the song's own video
  (`songs.youtube_video_id`, none for NULL / "" / "N/A") through
  `manage_youtube_playlists.audio_languages_via_api()` - only with `YOUTUBE_API_KEY`, never the
  OAuth token (no key = the module quietly answers nothing). BCP-47 codes, the primary subtag
  through `code_name()` ("pt-BR" -> Portuguese, "zxx" -> Instrumental, "und" -> nothing). Measured
  that day on 500 songs with a known language and 431 without lyrics or language: about a third
  of the videos carry it, ~94% of those agreed (wrong: a Ukrainian song "ru", Polish and Arabic
  ones "en", a Norwegian one "el", an English one "ja"), so it's `never_sure`: its answers go to
  the review list, ticked like an unsure lyrics reading (the artist sings it in half its songs),
  and a Serbian / Croatian / Bosnian one goes through the country-and-year rule like a site's.
  `BY_SONG`: asked once a song, whatever its names. Its `prepare(songs)`, which `_look_up()`
  calls for any module that has one, asks about every song's video before the loop - 50 a
  request, one quota unit; a failing request makes it raise (asked again next run).

`discover_language()` asks each module the first `LANGUAGE_MAX_QUERIES` names of
`_lyrics_queries()` (a local one once), checks title and artist like the lyrics, and keeps the
first sure answer. The screen (`utils/ui/language_review.py`) runs the local modules over every
song first, then the sites for max-songs-per-fetch of those left. A sure reading of the lyrics in a
language none of an artist's (3+) known songs has is still asked about, unticked
(`_foreign()`), as are unsure readings and YouTube's answers (ticked when the artist sings it in
half its songs; the list's `module` fact says which module) and,
for a song every module was asked about, the language nearly all its artist's songs have
(`LANGUAGE_ARTIST_MIN_SONGS` / `_SHARE`, ticked). It isn't in `LOOKUP_STEPS` yet: the owner reviews
the rules first. A first unattended run on 2026-11-19 (it had been put there unfinished) saved
2,658 songs' languages; the review of them with the owner gave the rules above, about 80 were
corrected by hand, and a random sample of 60 of the 1,495 nobody had a value to compare with
all looked right on reading. The lyrics turned out to hold a few English translations (LRCLIB, Genius) -
LyricsTranslate's pages, "-english" addresses too, are the originals.

## Multi-artist songs (`additional_song_artists`)

Fetchers still get one artist string; nothing in a module changed. `discover_album_name()` (and
`discover_release_year_result()` for a single, via its `song=` kwarg) query the **primary artist first** —
one clean name finds a collab on most services better than a joined string — and only as a last
per-module retry the main-artist label "A x B" (feats excluded). Album lookups in Years stay keyed
on the primary artist alone: a guest on one track isn't the album's artist.

Validation had to change with it: querying "Sw@da" while the service credits "Sw@da, Maxim &
Niczos" fails plain similarity, so `_matched_artist_resembles()` also accepts a matched field that
contains any of the song's credited artists as whole words (after `normalize()`), or that passes
these same checks against one of them: services name one artist of a collab, their first, spelt
their way (Spotify and YouTube Music's "Ann' Sannat" for "Alizbar - Idje Sen", stored as
"Ann`Sannat"; `normalize()` makes those "ann sannat" and "annsannat"). Only for
multi-artist songs. Separately, for every song, a matched field that `propose_artist_split()` splits
into names is accepted when one of the names resembles the query ("Paulina Przybysz, Ten Typ Mes,
Albo Inaczej" for a song stored under Paulina Przybysz alone) — name by name, never substring, so
"Ariel Pink" doesn't pass for "Ariel".

One more rule in `_matched_artist_resembles()`, for every song: a matched artist with the same
`normalizer.name_key()` as the query is accepted. That key is `normalize()` with anything from the
first `(` on, a leading "the" and all spaces dropped. Services write Japanese/Chinese acts with
punctuation and a native-script name in brackets ("Cody・Lee(李)" for "Cody Lee"), and acronym bands
with dots and an article ("The S.O.S. Band" for "SOS Band"). Plain similarity rates those
0.70–0.74, below the threshold, so correct matches from MusicBrainz, Spotify and Google were
discarded. It's an exact key match, so "Lee" or "Cody Lee Band" still don't pass.

## Which fetchers report matches

- `music_brainz_fetcher.py` — reports the title/artist of the MusicBrainz *recording* that the
  chosen release came from.
- `itunes_fetcher.py` — reports the Search API's `trackName` (before any "(") and `artistName`.
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
- `youtube_fetcher.py` — reports the title/first artist of the matched row in the "Songs" search.
- `wikipedia_fetcher.py` never reports a matched title: the page is picked because its title
  resembles the query (song page) or the tracklist text holds it (artist/album page), so the page
  title would only echo the query. On a song page it does report `matched_artist`, the infobox's
  "Single by X" (`_infobox_artist()`): `title_resembles()` takes "Teenage Love Affair" for
  "Love Affair", so the search for TwoForty's song landed on Alicia Keys' page and filed it under
  "As I Am" until the manager could check the artist. Of a joint credit ("Queen and David Bowie")
  the fetcher reports the name equal to the query, since `_matched_artist_resembles()` doesn't
  split at "and". Artist/album pages still report no artist. Any other page is turned down first
  (`_page_is_about_artist()`: its title names the artist, or its infobox says "by X"), since a TV
  show's tables of songs gave the album "Week 3: Krzysztof Krawczyk Week". Another song's page
  (`_is_song_page()`: "(song)" in its title or a "Single by" infobox) is turned down too, even when
  its title names the artist: "Heart - Of Glass" got "Heart of Glass (song)"'s heading "Track
  listings and formats". Past that the tracklist
  scan is the only guard, and it can fire on the wrong section: the manager's first query cuts a title at "&" ("Initial
  D - Night & Day" → "Night"), Wikipedia answers with the franchise page "Initial D", and its
  "See also" list held "Wangan Midnight" — "See also" came back as the album. So the scan skips
  `NON_TRACK_HEADINGS` (see also, references, music videos, charts, ... - a discography's "Music
  videos" table gave Depeche Mode's "Heroes" that album) and matches the title as
  whole words (`_contains_words()`), never as a substring.

## MusicBrainz: which release becomes the album, and what it can't find

`_pick_release()` drops compilations/live/remix releases, release groups with no type at all (often
a compilation nobody tagged: "Radio Woodstock 100.1 WDST 25th Anniversary - Disc 2" for Deep Blue
Something) and releases credited to another artist
(by MusicBrainz artist id - an Australian edition of Dido's "No Angel" carries four LeAnn Rimes
songs; "Various Artists" passes), takes the earliest studio Album - the artist's own from any of the
hits before a "Various Artists" one: a film soundtrack is typed Album + Soundtrack, not
Compilation, so it passes the filter, and MusicBrainz listed "Windkracht 10 Soundtrack" ahead of
"Victory" on Daan's "Housewife" - and otherwise ranks EP over Single, earliest first (rule and reason in its docstring; pinned by
`tests/test_music_brainz_fetcher.py`).

**Other modules' years are held against MusicBrainz's first appearance.** A store's date is its
edition's: Spotify has Anawa's 1973 album (Andrzej Zaucha, "Stwardnieje ci łza") only as a 2014
reissue - its "℗ 2015" line says no more - and MusicBrainz credits the album to the band, so the
release-group search by Zaucha finds nothing. But MusicBrainz has his song on a 2004 compilation,
so 2014 can't be its year. `discoveries_manager.year_after_the_song(year, artist, title, modules,
source)` asks a module among `modules` with `first_appearance_year()` (MusicBrainz, one request)
and calls a year more than the margin later a reissue's. Years drops such a result and asks the
next module (an album's first song is held against); Albums leaves the year out of
`_save_album_details()`. Not asked about MusicBrainz's own single-mode or album answers, which
left reissues out already. Without MusicBrainz enabled nothing is checked. A song nothing can
date stays without a year (the manual prompt, or a miss unattended), never with a reissue's.

**Stored years, checked the same way** - Years' second step (`fill_missing_years._check_stored_years()`;
until 2026-11-20 a menu entry of its own, "Check stored years"): those rules only stop new reissue
years, so the ones stored before are held against `first_appearance_year(..., raise_errors=True)`
too, the songs not `year_checked` yet, in what step 1 left of max-songs-per-fetch (whole groups, at
least one), and only for the whole catalog (an import's Years skips it). Grouped like step 1 -
artist + album, a single or a song without an album alone. An album whose songs share one stored
year gets one request (its first song asked) and a ticked change written to every song of it, so
it keeps one year; one with several years is checked song by song, each proposal changing only its
song, since then the album text is usually wrong for some of them (a full run, 2026-11-21:
Kasabian's "Club Foot" 2004 under "West Ryder Pauper Lunatic Asylum" with "Fire" and "Runaway"
2009, Walk off the Earth's "Vol. 4", Calvin Harris' "96 Months" - asked by one song, the whole
group got its year). Only a stored year later than MusicBrainz's first
release by more than the margin is offered, unticked (unattended only an owner's rule on the
`stored_years` facts takes one; no built-in rule, a proposal left is not asked again for
`AUTOMATION_LOOKUP_RETRY_DAYS`): a trial on 200 songs gave 6, about half
right (Aerosmith 2001 -> 1998, Agnes Obel 2021 -> 2016, Akira Yamaoka 2016 -> 2003); MusicBrainz's
first year was also a live take's on an edition dated as the original album (3 Doors Down, now
left out by the recording's disambiguation), a re-recording's original (ASIAN KUNG-FU
GENERATION's "Re:Re:", 2016 for an anime) and, for Alabama Shakes' "Sound and Color" and Alanis
Morissette's "Citizen of the Planet", earlier than the songs - not looked into yet. A random sample of 150 stored years (2026-11-20):
67% agreed with MusicBrainz, 3% were later (très.b's "Ola" with Męskie Granie's 2013, The HU's
"Wolf Totem" with a Metal Hammer compilation's 2021 - what this fixes), 5% earlier (MusicBrainz
lacks the original or its search shows only compilations for a big hit: Gloria Gaynor's "I Will
Survive" first 1993, Maanam's 1981 single "Cykady na Cykladach" not there at all), 24% unknown to
it (recent niche singles, game and anime soundtracks, junk in titles). The earlier and unknown
ones are where another source - Discogs' master release years - would help, mostly for the ~1,100
songs before 2000.

**Earliest, not first listed, and never a reissue** (2026-11-19, old ex-Yugoslav songs whose
year decides their language). Search order isn't release order: Tajči's "Hajde da ludujemo"
(1990) listed "Bube u glavi" (1991) first, Josipa Lisac's "O jednoj mladosti" (1973) a 2018
studio session. So the Albums are ranked by their release group's earliest year among the hits
(`_release_group_year()`), those of a recording titled like the query first (the manager drops
any other's album anyway), result order on a tie. And a release more than
`MUSICBRAINZ_REISSUE_AFTER_YEARS` (2) after the recording - every hit of the same normalized
title - first appeared on *any* release, compilations and other artists' included
(`_first_appearances()`), is a reissue (`_is_reissue()`), taken only when no other release
is left (Albums, then the EP/Single fallback, both without reissues first) and then without a
year: Denis & Denis - "Soba 23" (1984) is on "Program tvog kompjutera", a 1995 best-of typed a
plain Album, while the 1985 mini-album "Ja sam lažljiva" (MusicBrainz: an EP, 1988) already had
it, so the EP wins. Not dropped altogether, because an album may be among the hits only by a later
edition. The margin keeps a lead single a year or two before its album from making the album a
reissue. Further rules, each from a real miss (2026-11-19, second round):
- **Undated** counts as a reissue once the song is known from dated releases: the first 5 hits
  of Maryla Rodowicz's "Sing Sing" held only the undated "Wola 2" (a re-recording) besides
  compilations; "Sing-Sing" (1976) is a later hit.
- **A "Various Artists" album** (a soundtrack) of any year after the song first appeared only
  collected it: "Will & Grace: Let the Music Out!" (2004) for Dido's "White Flag" (2003). A
  film's own song still gets its soundtrack - it's the first appearance.
- **A version's recording isn't the song** (`_is_queried_song()`: `read_version()` +
  `sure_version()`, unless the query is a version too): "Losing Grip (Demo)" is on a 2001 "Let
  Go: Demo Tracks". **Bootleg** releases never count.
- **A lone early compilation** more than the margin before every other release doesn't set the
  first appearance (`_first_appearances()`): MusicBrainz lists Dido's box "Life for Rent / No
  Angel" as 1999. A lone EP or album does (Denis & Denis). For the *year* the album answer
  reports, the lone compilation counts after all (`lone_compilations=True`): Czesław Niemen's
  "Dziwny jest ten świat" (1967) shows up only as its 1991 CD after a 1979 best-of, so it's the
  album with no year, and Years asks the release-group search, which has the group's first date.
- **`_search_album()` asks for 25 hits** whenever the first 5 gave no non-reissue Album (it
  stops when fewer hits came back than asked for: that was all).
- **`first_appearance_year()`**: the same earliest year as one request, for the manager's
  `year_after_the_song()` (below). A re-check of the 68 songs with a
MusicBrainz album on 2026-11-19 changed only reissues and compilations ("CDN", 2017, for Hey's
1990s songs; "ZKM Live"; a chart) besides the cases above. The single-mode year
(`get_release_year(is_single=True)`) never takes a reissue's year and takes the earliest one, not
the first listed. A compilation wrongly typed plain Album still wins ("Amstel
Pulse: Modern Rock Playlist" for "Breakfast at Tiffany's", whose first 25 hits are almost all
compilations, without the real album). Two known misses are by design, not bugs to chase:

- **A song released only on a soundtrack/compilation** (e.g. an anime theme like Yoshimi Iwasaki
  "Hoshi no Silhouette", only on the *Touch* OST `タッチ`) gets no album from MusicBrainz, because
  the compilation filter drops every release it has.
- **Japanese titles in romaji.** MusicBrainz stores Japanese recordings in the original script
  (`星のシルエット`). The artist has a Latin sort-name/alias, but the title doesn't, so a romaji
  `recording:"..."` query returns nothing. Even a kanji romanizer (none yet — see `matching_key()`
  in AGENTS.md) wouldn't save katakana loanwords: シルエット romanizes to "shiruetto", not
  "silhouette". What works instead: store the title and artist in the original script and give
  the artist its Latin name as a synonym (岩崎良美 + "Yoshimi Iwasaki"). Western services credit the
  romaji name, which the manager can't match to kanji (Google's "Yoshimi Iwasaki" was discarded),
  but the synonym retry queries with it, and YouTube Music then finds `タッチ`.

## Don't remove the per-candidate blacklist checks

`music_brainz_fetcher.py` and `wikipedia_fetcher.py` both call `is_blacklisted_album()` *inside*
a loop over many candidate releases/headings, to filter out bad options while picking the best one
(batch filtering). That is a different job from the manager's blacklist check on the *final*
returned album (a last-resort net). Don't treat the per-candidate calls as redundant with the
manager's check and remove them — they serve a genuinely different purpose and are still needed.

Every album-lookup call passes the queried artist: `is_blacklisted_album(title, artist)` lets a
blacklisted title through when the database already has it as that artist's album (name or
synonym, `albums.artist_has_album()`, reads `songs.album`). The substring list is broad ("rock",
"music", "dance", "gold", "album" — ~90 real artist/album pairs in the DB trip it, e.g. "Death By
Rock and Roll", "Confessions on a Dance Floor", "Czerwony album"), and the user preferred this
exception over editing it. It has to reach the per-candidate checks too, not only the manager's:
otherwise MusicBrainz skips the album and falls back to another release. Limits: it helps from
the second song of an album on (the first has nothing in the DB yet), and it also re-admits junk
already stored for that artist. Clean names (`clean_names.py`) calls it
*without* an artist on purpose — every stored album trivially exists for its own artist. Tests
never touch the real DB here: `tests/conftest.py` stubs `artist_has_album()` for every test.

## The shared headless browser (`src/utils/common/selenium_sessions.py`)

`open_global_driver()` / `close_global_driver()` manage **one** process-wide headless Chrome
instance, reused across `google_search_fetcher.py`, `genius_fetcher.py` and `spotify_fetcher.py` via
`get_global_driver()` rather than each fetcher opening its own. The first `get_global_driver()` starts
Chrome, so a run where MusicBrainz, iTunes or YouTube Music answer everything never starts it. A curl test
(2026-11) showed why these three remain: Spotify's search page has no results without JavaScript, Genius
answers 403 and Google a redirect page.

Any workflow that runs the fetchers **must** call `close_global_driver()` in a `finally`.
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
`utils.common.debug`'s global `sys.excepthook`. `get_global_driver()` catches it, tells the user once
and returns `None` until `close_global_driver()`, so every browser fetcher must handle `None`; the
lookups without a browser carry on.

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
being logged in, so it needs no credentials. The official API has its own module,
`spotify_api_fetcher.py` (see "Three kinds of fetcher").

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
3. Unless the album's name ends in a soundtrack word, open its `/album/<id>` page and refuse it when
   it's a various-artists compilation (`_is_various_artists_album()`): "FSB - Sled 10 godini" had got
   "Zlatni Hitove Na Bulgarskata Estradna Pesen" this way. The track page doesn't say the album's
   kind. The rule needs both no `creator-link` and several track artists, so a markup change can't
   refuse every album; the "Compilation" label is a plain span with hashed classes and localized,
   so it isn't used. A page that doesn't load keeps the album. One artist's compilation stays.

The API version and `itunes_fetcher.py` apply the same rule through
`text_utils.is_various_artists_compilation()` (the album's own artist, a soundtrack kept). A fix in
one fetcher alone is not enough: iTunes had the same compilation and was next in line. Soundtracks
are recognised by the name's ending only, so "Moulin Rouge! Music From ..." is refused. Google,
Genius and Wikipedia don't have this rule.

All of `tracklist-row`, `track-page`, `entityTitle`, and `creator-link` are Spotify's own
`data-testid` attributes (not hashed Encore CSS classes), which tend to be more stable across
frontend deploys than class names — but this is still unofficial markup Spotify owes no
compatibility guarantee for. If this fetcher starts returning `None` for everything, check
`driver.page_source` for those attributes still existing before assuming the matching logic broke.

The two pure-parsing helpers (`_find_matching_track`, `_extract_album_from_track_page`) are unit
tested against static HTML fixtures in `tests/test_spotify_fetcher.py` — no live network/Selenium
in the test, consistent with the rest of `tests/` — but that only locks in the parsing logic
against the *fixture*, not against Spotify's real markup drifting out from under it.

## `itunes_fetcher.py` — Apple's public iTunes Search API

One `GET https://itunes.apple.com/search?entity=song` per lookup: JSON, no key, no browser. It replaced
scraping music.apple.com (two search-results layouts, then the song's page), whose markup had already
changed under it once. Apple documents about 20 requests a minute, hence
`ITUNES_SEARCH_MIN_INTERVAL` (`RequestPacer`, one per fetcher module).

`_find_track()` needs the queried artist inside `artistName` and the title before any "(" to clear
`SPELLING_CHECK_THRESHOLD` (Apple bakes "(feat. X)" into titles). Of the results that fit, the one
closest to the full title wins, so the plain title beats "(Live ...)", and at equal title a real album
beats a single. A release whose `collectionName` ends in `" - Single"` is reported as `SINGLES_ALBUM`,
the sentinel `wikipedia_fetcher.py` also uses. Blacklisted albums and various-artists compilations
(`collectionArtistName`, see the Spotify section) are skipped per candidate, and so is a version
the query isn't (`read_version()` + `sure_version()`: "(Live ...)", "(Remix)"): the title is
compared before its "(", so Maanam's "Cykady Na Cykladach (Live 1985)" gave its live album
"Totalski No Problemski" to the studio song.
Tests: `tests/test_itunes_fetcher.py`.

## `youtube_fetcher.py` — music.youtube.com's JSON search, not the Data API

Deliberately does **not** use the official YouTube Data API (needs an API key and is quota-limited).
`get_album_name()` POSTs to `youtubei/v1/search`, the endpoint the web player itself calls, with the
"Songs" filter (`YOUTUBE_MUSIC_SONGS_FILTER`). No browser, cookies or consent page are involved. The
endpoint and its answer are undocumented, so if every lookup comes back `None`, check `debug.log` for
`[YouTube] Search failed` and then the shape below. `clientVersion` is built from today's date because a
frozen one would age out.

Each row (`musicResponsiveListItemRenderer`) has the title in flex column 0 and, in column 1, the runs
for artists and album, told apart by `pageType` (`MUSIC_PAGE_TYPE_ARTIST` / `_ALBUM`), not by position.
A multi-artist row has one run per artist, so the query artist is compared against each of them *and*
against them joined back together (`" x "`, `", "`, `" & "`). The joined form is what makes a collab
stored in the DB as one artist string match: "Sw@da x Maxim x Niczos" scores only ~0.4 against any single
artist. The best row whose title *and* artist both clear `SPELLING_CHECK_THRESHOLD` wins.
Tests: `tests/test_youtube_fetcher.py`, `tests/test_scraping_fetchers.py`.

## Three kinds of fetcher, and the API versions

`MODULE_NAME` ends in what a module needs, shown in Settings and Statistics: `(Curl)` plain HTTP,
`(Curl + API)` plain HTTP with a key from `.env`, `(Selenium)` the shared Chrome. "Curl" is the label
only: the code uses `requests` or a client library.

`spotify_api_fetcher.py` and `genius_api_fetcher.py` sit beside the Selenium versions as separate modules,
so each can be ordered, switched and counted on its own. Modules listed in
`constants.DISCOVERY_MODULES_NEEDING_API_KEY` are off in a new config and when `_reconcile_config()` adds
them to an existing one, at the end of its order. A module switched on without its key warns once and
returns `None`. A new module needing a key goes into that set.

Spotify: client-credentials token, kept until `SPOTIFY_API_TOKEN_MARGIN` before it expires. The app's owner
needs Premium since February 2026. Search is capped at `SPOTIFY_API_SEARCH_LIMIT` = 10 for Development Mode
apps. A stored `Song.spotify_url` is still read by `spotify_fetcher.get_release_year_from_url()`, so with a
browser. Genius: a search, then `/songs/<id>` for the album, which the hit doesn't carry; no
`get_release_year()`, for the reason given under Years above. Both were written from the public docs and
checked against mocked answers only, until someone runs them with real keys.

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
on every mutation, so nothing is lost if the process is killed mid-run. The flip side: never clear
the stats by just deleting/emptying the JSON files while the app runs. The next bump would write
the stale cached counts straight back. Use `reset_discovery_stats()` instead (Settings → "Reset
statistics records"). It replaces both the album and the year caches and files together.

The main menu's "Statistics" option (`src/menu/main_menu/statistics/__init__.py`) reads the
counters plus `load_all_discovery_modules_metadata()` (same source the Settings enable/disable
screen uses, so disabled modules still show their historical numbers) and prints invocations,
successes, and success rate per fetcher — no aggregation logic lives in the menu layer beyond
formatting, matching how `settings_menu` already merges config with module metadata for display.
