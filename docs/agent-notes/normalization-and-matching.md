# String normalization & fuzzy matching (`src/utils/common/`)

Scope: `normalizer.py` (canonicalising messy strings), `split_artist_title()` /
`extract_unknown_data()` in the same file (splitting "Artist - Title" text -
from a filename stem, or any other string), and the similarity helpers in
`text_utils.py` (comparing the canonicalised strings).

## One normalizer, extended — never a second one

`normalizer.py` is the **single, centralized** string-normalization
implementation: `normalize()` and `compare()`, handling diacritics, Unicode
scripts, apostrophes, and punctuation. `text_utils.normalize_text()` and other
call sites delegate to it.

Do not write a new ad-hoc normalize/compare function anywhere else — extend this
one.

## Matching across alphabets (`script_utils.py`)

The DB sometimes holds a Latin transliteration of a title the sources only know
in its own script: "Tam, de ty ye" for Ani Lorak's "Там, де ти є", "Neformat"
for TNMK's "НєФормат". `title_resembles()` and the fetch manager's artist check
fall back to `resembles_across_scripts()` when either side has non-Latin
letters. It runs both sides through `script_utils.matching_key()`, a lossy,
language-agnostic romanization with y/j -> i, kh -> h and ts -> c folds, because
transliteration schemes disagree exactly there. Latin-only pairs never take
this looser path. Years and album lookups also get one extra search stage with
`cyrillic_search_variant()`, a rough Russian-style Latin -> Cyrillic spelling
("Neformat" -> "Неформат"). It's only used when the query is Latin-only but the
rest of the entry (artist, other titles) has Cyrillic, and search engines
tolerate the rest ("НєФормат"). Album lookups also go the other way: a title
in another alphabet gets one more search in Latin letters, since some sources
list it only that way (MusicBrainz has N.R.M.'s "Фабрыка" only as "Fabryka").
That query is `title_latin` if set, else `romanize.suggest_latin()`'s guess
(never stored), not `matching_key()`, whose folds would spoil a search. The scope was deliberately narrowed: Cyrillic,
Greek, Georgian, Hangul and kana are covered without new dependencies.
Kanji/hanzi (pykakasi/pypinyin) and Arabic wait for a first real case.
The owner's stored Latin forms (`title_latin` / `name_latin`, `latin_names.py`) are a separate,
exact layer on top: search and matching treat them as one more name, no key involved. Their
*suggestions* come from `romanize.suggest_latin()`, which is a different tool from
`matching_key()` on purpose: it aims at what a person would write (scheme per language, case
kept, "Alyona" not "alona"), so it can't serve as a comparison key, and `matching_key()`'s folds
make it unfit to show. Its Cyrillic language guess goes language field → letters only one
alphabet has (ў / жы-шэ pairs = Belarusian, ї є ґ = Ukrainian, і with ы/э/ё = Belarusian, і with
и/щ = Ukrainian) → artist origin → і alone Ukrainian, else Russian. Bare "і without и" was tried
as a Belarusian signal and misread Ukrainian titles ("Зелені очі"). `name_key()` is one such extension: an artist-name key for exact-equality
checks ("The S.O.S. Band" == "SOS Band"), see `discovery-modules.md`.

Invisible format characters (Unicode category `Cf`: zero-width space U+200B,
soft hyphen, joiners, direction marks) are *dropped*, not turned into a space
like other punctuation. Text copied from Bandcamp carries U+200B inside words
("Cz\u200bô\u200brny Ritm Z\u200bł\u200boté Serce"). As spaces, that
normalized to "cz o rny ritm z l ote serce", so two copies of one album never
compared equal and Years' "year already in the DB" step missed it. The same removal is
`strip_format_characters()`. "Clean names" uses it
(`remove_invisible_characters()`) to clean stored titles, albums and artist
names, with `keep_joiner=True` so the zero-width joiner inside emoji sequences
survives.

The marks ™ ® ℠ © ℗ are removed before NFKD, which would otherwise expand "™"
into the letters "TM" ("Cope™" -> "copetm"). Fetched album titles do carry them
(Freeland's "Cope™" on both MusicBrainz and Spotify). Relatedly,
`text_utils.title_resembles(query, candidate)` is the title check the fetch
manager and MusicBrainz's year lookup share. Equality after `normalize()`
always passes, and otherwise it's the usual `similarity()` against
`scaled_similarity_threshold()`. Without that first step, a short title with
one stray mark scored 0.89 and missed the 0.92 short-string threshold. With the
default `allow_subtitle=True` it also accepts a candidate that only adds a
bracketed subtitle ("Berjozkele (Kołysanki Jidysz – Yiddish Lullabies)" for the
stored "Berjozkele", similarity 0.34). A caller choosing among several candidates
should prefer `allow_subtitle=False` matches first, as MusicBrainz's album loop
does, so that "AM (Live)" can't date "AM".

## `split_artist_title(name)` — three ordered fallback stages

The actual "Artist - Title" splitter — three stages, each tried only if the
previous one found no separator. **Keep them in this order and don't merge them
into one greedier regex** — each is a fallback for when the stricter,
less-error-prone stage before it fails. `extract_unknown_data(filepath)` is a
thin wrapper around this (`split_artist_title(filepath.stem)`) for deriving
artist/title from an mp3 filename when ID3 tags are missing or empty; it was
factored apart so `utils/youtube/import_from_playlist.py` could reuse the same
splitting logic directly on a YouTube video title (not backed by a file at all)
— see `docs/agent-notes/youtube-search-matching.md`'s "Importing FROM a YouTube
playlist" section.

1. **Spaced separator required** — ` - `, ` – ` (en dash, U+2013), ` — ` (em
   dash, U+2014), or ` _ `. En dash and em dash are visually near-identical but
   distinct codepoints, and real-world filenames use both (e.g. `Zdob și Zdub —
   La mijloc...`).
2. **Bare `-` / `_` / `~`, no surrounding spaces required** — recovers
   `Emade-Pierwszy stopień wtajemniczenia.mp3`, `Die Ärzte _Unrockbar_.mp3`,
   `Haiku Garden ~ Avalanš.mp3`; strips stray `-` / `_` / `~` / spaces off both
   halves. Intentionally permissive and a genuine last resort: a title with an
   incidental hyphen and no real artist (`Re-Animator.mp3`) misparses into
   artist `Re` / title `Animator` here rather than being reported unparseable —
   acceptable only because stage 1 already failed. Real case: an untagged
   `Svefn-g-englar.mp3` (Sigur Rós) became artist `Svefn` / title `g-englar`,
   and the folder playlist added it as a new song. So `read_file_metadata()`
   marks a name read through stage 2 or 3 with `unsplit_title` (the whole stem),
   and `LocalSongMatcher` also reads that as a title alone: `match_similar()` (a
   suggestion that is asked about, never taken in `match()`), `_readings()` for
   scores, and auto-accept only when `is_distinctive_title()`. The filename gets
   split in two places, `read_file_metadata()` (tags present but empty) and
   `mp3_utils.extract_metadata_with_fallback()` (no ID3 at all), so the marker
   is set afterwards, by comparing with `extract_unknown_data()`, not in either
   branch.
3. **Split on 2+ consecutive spaces** — recovers filenames with no punctuation
   separator at all, e.g. `Akiko Yano  Iroha Ni Konpeitou.mp3`, where the
   artist/title boundary is just a double space.

## `text_utils` similarity helpers — three functions, not one with three signatures

- `similarity(a, b)` — the plain two-string comparator, 0–1 ratio backed by
  `difflib.SequenceMatcher`. Use this when comparing two raw strings.
- `are_song_entries_similar(db_object, title_query, artist_query, threshold)` and
  `are_artists_entries_similar(db_object, artist_query, threshold)` — these
  require an actual **DB object** as the first arg (they read `.title` /
  `.artist.name` / `.name`), not a second plain string. Passing two plain strings
  raises `TypeError` — this broke `discovery_modules/genius_fetcher.py`'s
  `title_matches_url()` until fixed.

Comparing two strings → `similarity()`. Only reach for `are_*_entries_similar`
when you actually have a DB row to compare a query against.

## `SequenceMatcher` ratio is unreliable on short strings

Two different names that share a first letter and a common suffix can still score
high just because most characters line up in a short string — e.g.
`similarity("A.Mia", "Armia")` is `0.8`.

When comparing a similarity score against a match threshold **for artist names**,
use `scaled_similarity_threshold(a, b, base_threshold)` rather than comparing
against `base_threshold` directly. It raises the required ratio for short strings
(≤5 chars → 0.92, ≤8 chars → 0.85) before falling back to `base_threshold` for
longer names.

`are_artists_entries_similar()` applies this internally.
`import_data_from_mp3_tags.resolve_artist()` and `check_artist_spelling()` call
it explicitly at their fuzzy-match checks.
`discoveries_manager._validate_result()` uses it for its title/artist cross-check
too.

## A shared bracketed suffix inflates `similarity()` on otherwise-different titles

Two genuinely different song titles that happen to share a `(prod. X)` /
`(feat. Y)`-style bracketed credit can score above the usual 0.7 threshold on
the bracket text alone — e.g. `similarity("Adieu (prod. Rumak)", "Nostalgia
(prod. Rumak)")` is `0.76`, because roughly 15 of the ~20-24 characters being
compared are the identical `(prod. Rumak)` suffix.

`import_engine.find_similar_song()`'s local-DB duplicate check strips bracketed
content with `remove_brackets()` from both titles before calling `similarity()`
for exactly this reason. Any other duplicate-song check comparing raw titles
should do the same rather than comparing `song.title` strings directly.

## The local artist lookup's fuzzy step only sees substring candidates

`import_engine._match_artist_locally()` (behind `find_matching_artist()`) runs
`similarity()` only over what `get_artists_from_db_session()` returned for the
name, and that search is substring-based. A typo inside the name ("Liiquid
Tension Experiment" vs "Liquid Tension Experiment") therefore yields no
candidates at all, and the fuzzy step never runs - however close the spelling.
The import survives this by falling back to MusicBrainz; local-only lookups
don't. The same goes for a leading "The" ("The caesars" never finds "Caesars",
which silently broke the swapped-tags check below and let a folder playlist add
"The caesars" by "Jerk it out" as a new song), so a name starting with "the"
that finds nothing is searched again without it, taking only an artist equal
by `normalizer.name_key()`; that equality also wins before the fuzzy step
("Caesars" -> "The Caesars", which `similarity()` rates too low). The search does find artists by their synonyms, so the fuzzy step (and
its length pre-filter) compares the name *and* `artist_match_synonyms()` -
before that, a tag naming an artist by a synonym ("I nad kropką", Konkret's)
was found by the search and then thrown away, so nothing local matched it. `LocalSongMatcher.match_similar()` works around it from the other side:
songs with the same title core, filtered to artists whose name / Latin form /
synonym - or one part of a joined name not split yet, via
`propose_artist_split()` ("College" of "College & Electric Youth") - passes
`scaled_similarity_threshold()`, accepted only when exactly one song fits.
Before that it tries the tags with artist and title swapped ("N.I.B." /
"Black Sabbath"), and when the artist *is* found but no title matches, a
whole-title similarity (bracket text kept) of `FILE_TITLE_SIMILARITY_THRESHOLD`
among that artist's songs ("Losing TimeGrande Finale" ~ "Losing Time (Grand
Finale)") or else the title the file's starts with, whole words (junk no list
knows trailing it: "Tubthumping  Officiel official Soundtrack World Cup 98"
scores only ~0.26 as a whole title, far below any threshold); a file with no artist at all gets the one song with its title core
by anyone. `match()` (files.db, cover import) takes these guesses silently
*except* those two by title alone (`title_guesses=False`): within one artist
the similar title would happily pick "Pt. 2" for a "Pt. 1" file, and a
nameless "Intro" could be anyone's, so they run only where a user confirms (the folder
playlist offers every `match_similar()` result for confirmation, except
the misspelt-artist case at or above the user's "Auto-accept similar artist
names" setting, which `match_confident()` takes without asking; it compares
whole names only, never parts of joined ones, and short names need more as
usual via `scaled_similarity_threshold()`). Note that `files.db` rematches
old unmatched rows only when the catalog fingerprint changes, so a matching
improvement like this reaches those rows only after the next catalog change.

Title cores also drop a trailing unbracketed "feat. X" ("Шкідлива звичка feat.
YanGo"), but `find_song_loosely()` tries the core *with* it first: one artist
can have both "No Scrubs" and "No Scrubs ft. Joanna Jones (Cover)" (seven such
pairs in the real database when added, several of them plain duplicates), and
without the first pass a file "No Scrubs (Official Video)" would take
whichever came first. A tag title holding the artists before a dash ("A & B —
Title") is retried as "Title" (`title_after_artists()`), only after the whole
title found nothing.

Title cores (`import_engine._title_core()`) also drop a leading track number
(`strip_track_number_prefix()`: "T03: ", "03. ", "03 - ", "Track 5) "). It
needs a separator *and* a space after the digits so titles that are numbers
("1979", "7 Rings", "2:00 AM", "99 Luftballons") stay whole. They also drop
the words in `TITLE_AMPERSAND_WORDS` ("and", "i", "und", "et", "y", "n"):
`normalize()` removes "&" as punctuation but keeps "And", so "Angel of Small
Death & the Codeine Scene" never met "... And The Codeine Scene". This lives in
`_title_core()` only, not in `normalize()` - changing the canonical normalizer
would shift every search and cache key in the app. When added, no two songs of
one artist in the real database collapsed into the same core.

## Closest songs by score - the last resort

When no rule above finds anything, the folder playlist offers the top 3 of
`LocalSongMatcher.best_guesses()`, scored by `match_score()`: the title's
similarity (whole and core, the better), weighted 0.65 against the artist's
when the file has one - and the better of the tags as written and swapped:
`match_similar()` already finds a swapped-tags song, and scoring only the
written order showed it at ~20% next to a 100% match. Two choices that aren't obvious:

- The **title alone** must reach the minimum score, not just the weighted
  sum. Otherwise a perfect artist (0.35) needs a title of only ~0.38 to pass
  0.6, and every file whose song isn't in the database gets a random song of
  that artist ("Hymn to Virgil" for Hozier's "Take Me to Church").
- A best song always exists, so these guesses are offered with the cursor on
  "None of these" and never taken by `match()`. The one exception is the
  user's "Closest songs: auto-accept score" (default 0.99), which applies to
  any suggestion's score. A file with no artist is scored on its title
  alone, so the one "Intro" in the database would be 100% for every nameless
  "Intro": such a file is auto-accepted only for the one song with its title
  and only when the title is distinctive (`is_distinctive_title()`: 3+ words,
  15+ characters without brackets). Songs sharing a bracket-less title core
  ("Dragostea Din Tei (Numa Numa) (Medieval Cover)" vs O-Zone's "Dragostea din
  tei") are told apart by the whole title before giving up. A misspelt artist with an exact
  title ("Liiquid tension experiment") scores ~0.993, so at 0.99 this also
  takes what the artist auto-accept setting would have asked about.

It compares against every song (~5.7k), so it uses
`text_utils.similarity_at_least()` (SequenceMatcher's cheap upper bounds
first, no `slog()` per call) and caches each song's normalized titles and
artist names per matcher: ~0.1-0.5 s per unmatched file on the real database.

## A title of punctuation only normalizes to ""

`normalize(":/")` is `""`, and `"" in anything` is True: `find_existing_song()`'s
last "contained in" step used to call ":/" the artist's first song, so the
import skipped such songs as already there and "Add it as a new song" offered
the wrong one. A title that normalizes to nothing is now compared by its own
characters only (case and spaces aside). Any new "key in other_key" check
needs the same empty-key guard.

## Duplicate songs: a stricter key than the file matcher's

`song_duplicates.duplicate_key()` is deliberately *not* `_title_core()`.
Dropping every bracket, as file matching does, proposed 29 "duplicates" on
the real database, nearly all remixes, covers, acoustic or long versions -
songs of their own. The key drops only featured-artist credits (bracketed or
not) and junk-only brackets (`TITLE_JUNK_SUFFIXES` +
`DUPLICATE_TITLE_EXTRA_JUNK`: "Visualiser", "Teledysk", ...), which left 9
groups, 8 real. A title naming a version word the others don't
(`DUPLICATE_VERSION_WORDS`) still gets proposed - "No Scrubs" vs "No Scrubs
ft. Joanna Jones (Cover)" - but unticked. Only spelling-identical titles
(`spelling_key()`) are folded without asking, by "Resolve duplicated artists".

Every fold goes through `merge_songs()`: before it, the silent pass kept the
lowest id and lost the dropped row's link, album, year and extra artists.
It also runs on the global music session - it used to open its own, leaving
the deleted song alive in the session the rest of the app holds.

## Polish tags read as Latin-1

Old taggers wrote ID3 text in Windows-1250 while declaring Latin-1, so mutagen
returns "Ma³pa i ja" for "Małpa i ja" (ł and ³ share byte 0xB3). It scored
94% instead of matching. `read_file_metadata()` runs
`normalizer.fix_cp1250_mojibake()`, which re-reads the whole text as
Windows-1250 only when it holds a character real text never has: "³", "¹",
a control character 0x80-0x9F (ś, ź, Ś, Ź), or "¿" inside a word (ż). It
can't simply re-read everything: "ñ", "ê", "ó" are fine Latin-1 letters
("España") that Windows-1250 would turn into "ń", "ę", "ó".

## Tags can carry HTML entities and site names

Files from some download sites have `&amp;` (and the like) literally in their
ID3 tags and names: "Major Lazer &amp; DJ Snake". `normalize()` would keep the
"amp" as a word, so `read_file_metadata()` - shared by the mp3 import, the
cover import, `files.db` and folder playlists - runs `html.unescape()` on the
artist, title and album it returns; the folder playlist does the same for a
file name used as the title. The same pass drops download-site names
(`normalizer.strip_site_names()`: a word ending in a `SITE_NAME_TLDS` domain,
"y2meta.com - ...", "(greenmp3.com)", "- dscom.it"), the words listed in
`src/config/file_name_junk.txt` (any site whose domain isn't in the list,
"Y2Mate.is"; edit that file to add more - it's read once per run) and
Y2Mate's "-<11-char video id>-160k-<ms timestamp>" suffix, and *before* deciding
whether to read the file name: a tag artist that was only "y2meta.com" counts
as missing. `extract_unknown_data()` strips them from the file name before
`split_artist_title()`, which otherwise made the site the artist.

## Full albums vs. tracks of the same name

A full album (`songs.kind = 'full_album'`) is usually titled like its album,
and so like its title track: "Migration" the album and "Migration" the song.
`_title_core()` also drops "(Full Album)" with the other brackets. Without a
rule, a track's file would be matched to the album row (and the import would
skip a genuinely new track as "already there"). So every matching entry point
in `import_engine` (`find_existing_song()`, `find_song_loosely()`, every
`LocalSongMatcher` path, and `find_similar_song()` — else the import's
end-of-batch "use the song already in the database?" offered a track the
album of its name) first passes the candidates through
`song_kinds.songs_for_entry(songs, title, duration_s)`:

- an entry that doesn't look like an album (no `FULL_ALBUM_TITLE_MARKERS`
  in its title, and shorter than 20 min **or of unknown length**) never
  matches a full album;
- one that does tries full albums (and rows whose own title has a marker)
  first, then the rest — a long file may be a mix kept as an ordinary song.

The length comes in the metadata as `duration_s`: `read_file_metadata()`
reads it from the mp3, `files.db` passes its stored one, the YouTube
playlist import takes yt-dlp's flat-playlist `duration`. Unknown length
counts as a track on purpose: an import of "Bonobo - Migration" must create
the track even when the album row exists. `find_existing_song()` also
matches "Migration (Full Album)" to the full album "Migration" (the title
with the marker cut out, `strip_full_album_marker()`).
