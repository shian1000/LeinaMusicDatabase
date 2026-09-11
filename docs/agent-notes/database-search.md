# Database search / getter layer (`src/utils/database/`)

Scope: `database_getter.py` (the query API the menus call to turn a user's search
string into `Song` / `Artist` rows) and the song/artist **category** system in
`datatables.py`. Schema itself is just the SQLAlchemy models — `datatables.py`
for `music.db` (`artists`, `songs`), `create_tag_db.py` for `tag.db` (`tags`,
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
trusted (non-ASCII queries) or returns nothing.

That logic lives in exactly one place: `database_getter._normalized_python_filter(
candidates, words, get_field)`. Any new fallback-filtering call site in this file
should call it, not re-inline the word/casefold loop.

## `search_only_categories` is song-only — passing it to artist search crashes

Categories are split three ways in `datatables.py`:

- `song_categories` — `"title"`, `"artist name"`, `"album"`, `"year"`,
  `"language"`, `"artist origin"`, `"tag"`, `"artist id"`
- `search_only_categories` — currently just `"name"` (a combined title+artist
  search)
- `artist_categories` — `"artist name"`, `"artist origin"`, `"artist id"`

`get_songs_from_db_session` accepts `song_categories + search_only_categories`.
`get_artists_from_db_session` accepts **only** `artist_categories` — `"name"` is
not valid there. Passing it anyway fails category validation; the resulting
`None` is then used as a dict key and the call crashes with `KeyError: None`.

This exact bug broke the "Fetch artists" menu: `fetch_songs.fetch_artists()` had
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
