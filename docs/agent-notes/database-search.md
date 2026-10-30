# Database search / getter layer (`src/utils/database/`)

Scope: `database_getter.py` (the query API the menus call to turn a user's search
string into `Song` / `Artist` rows) and the song/artist **category** system in
`datatables.py`. Schema itself is just the SQLAlchemy models — `datatables.py`
for `music.db` (`artists`, `songs`), `tag_db_manager.py` for `tag.db` (`tags`,
`song_tags`).

## `get_artists_from_db_session()` returns alphabetical order, not best match

`database_getter.get_artists_from_db_session(category, query)` runs a SQL
substring `contains()` search on `query` — **not** an exact match — and returns
the hits **ordered alphabetically by name**. So `results[0]` is whichever
matching name sorts first, not the closest match to the query.

The trap: calling it with an already-resolved artist's name just to re-fetch that
same object. The substring search can silently return a *different* artist whose
name merely contains the query and happens to sort earlier. This is exactly how
an exact-matched artist named **"Sting"** got silently resolved to **"POLKADOT
STINGRAY"** in `import_data_from_mp3_tags.resolve_artist()` — "Sting" is a
substring of "POLKADOT STINGRAY", which sorts before "Sting".

Rule: if you already hold the matched `Artist` object (from an exact-match query
or a similarity match that already succeeded), **use it directly** — never
round-trip through `get_artists_from_db_session()` to "get it again".

## The shared Python-side fallback filter

`get_songs_from_db_session` and `get_artists_from_db_session` both fall back to a
Python-side filter — normalize each word, casefold, require every word to be a
substring of the normalized field — when DB-level `LIKE` filtering can't be
trusted (non-ASCII queries) or returns nothing. Not for the song categories
`"year"` and `"artist id"` (`_WHOLE_NUMBER_CATEGORIES`): those are an equal whole
number only, the SQL result even when empty - the substring fallback found
artist id 5 in 15, 50, 105 - and text that isn't a number gets a message.

That logic lives in exactly one place: `database_getter._normalized_python_filter(
candidates, words, get_field)`. Any new fallback-filtering call site in this file
should call it, not re-inline the word/casefold loop.

The one sanctioned twin is `ArtistPool.matching()` (same rule, fields normalized
once): the artist fallback loads and normalizes all ~4k artists per call, ~90 ms,
which made the library index's rematch of a few hundred unmatched files take
~30 s. A caller doing a read-only pass over many names passes
`artist_pool=ArtistPool(session)` (`LocalSongMatcher(load_artists_once=True)`;
only `files_index.scan_library()` so far). It is a snapshot, so it stays off in
interactive flows (folder playlist, cover import), where an artist added
mid-run must still be found by the fallback. Change the filter rule in both.

## "artist name" and "name" searches also match synonyms

An artist stored in its own script ("Океан Ельзи") is only findable by its Latin
spelling through `artists.synonyms`, so artist search, the song "artist name"
category and the combined "name" search all match a word in the name *or* the
synonyms (`song_artists.artist_name_or_synonym_contains()`, also inside
`additional_artist_name_contains()` and the Python fallback's
`_all_artist_names()`). Import's artist resolution is unaffected in practice:
it uses this search only to gather candidates and then compares similarity
against `Artist.name`, so a synonym-only hit is filtered out there.

## `search_only_categories` is song-only — passing it to artist search crashes

Categories are split three ways in `datatables.py`:

- `song_categories` — `"title"`, `"artist name"`, `"album"`, `"year"`,
  `"language"`, `"artist origin"`, `"tag"`, `"artist id"`
- `search_only_categories` — currently just `"name"` (a combined title+artist
  search)
- `artist_categories` — `"artist name"`, `"artist origin"`, `"artist id"`

`get_songs_from_db_session` accepts `song_categories + search_only_categories`.
`get_artists_from_db_session` accepts **only** `artist_categories` — `"name"` is
not valid there. Passing it anyway fails category validation: `_validate_category()`
prints the valid options and returns `None`, and every caller then returns `[]`
(it used to use that `None` as a dict key and crash with `KeyError: None`), so a
wrong list now looks like "Nothing found" rather than a crash.

This exact bug once broke the "Fetch artists" menu: `fetch_songs.fetch_artists()` had
copy-pasted `search_only_categories + artist_categories` from its sibling
`fetch_songs()`, where `"name"` *is* valid. **Never build an artist-search
category list that includes `search_only_categories`.**

## Reuse `search_and_pick_db_object()` for "search then resolve to one row"

`src/utils/ui/menu_utils.py` — `search_and_pick_db_object(mode, ...)` is the
shared flow: prompt for a query → look up `Song` / `Artist` rows → auto-pick if
there's exactly one match, else show `pick_from_db_objects`. Used by
`edit_entry_menu` and `remove_song_menu` in `edit_songs.py`.

Reuse it for any new "search then resolve to a single DB row" flow instead of
re-duplicating the pattern. The previous hand-rolled copies had drifted and were
passing stale extra arguments that crashed both callers. It searches songs via
`search_only_categories[0]` and artists via `artist_categories[0]`, so it already
respects the song/artist split above.
