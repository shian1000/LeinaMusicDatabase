# Data model

Two separate SQLite databases, each its own SQLAlchemy `Base` / engine / session — see
`src/utils/database/datatables.py` (`music.db`) and `src/utils/database/create_tag_db.py`
(`tag.db`). Both live under `src/database/` (gitignored — runtime data, not fixtures; see
`AGENTS.md` → Database safety before writing to either directly).

`datatables.py`/`create_tag_db.py` are authoritative for the *current* shape; schema history is
tracked by the migration runner (`src/utils/database/migrations.py` — see
[`runbooks/database.md`](runbooks/database.md) before adding a column). If this page and the code
disagree, trust the code and fix this page.

## `music.db`

```
Artist                          Song
------                          ----
id            PK                id             PK
name          NOT NULL          title          NOT NULL
origin                          album
synonyms                        year
                                 language
                                 artist_id      FK -> Artist.id, NOT NULL
                                 nostalgic
                                 melancholic
                                 party
                                 youtube_video_id
```

- `Artist.name` has **no uniqueness constraint** — the DB will happily hold two rows with the same
  name. Preventing accidental duplicates is an application-layer concern (fuzzy matching before
  insert), not a schema one: see
  [`agent-notes/database-search.md`](agent-notes/database-search.md) and
  [`agent-notes/normalization-and-matching.md`](agent-notes/normalization-and-matching.md).
- `Artist.synonyms` — comma-separated alternate names for the same artist (e.g. a fan channel using
  a translated name). Consumed by the YouTube matching layer; see
  [`agent-notes/youtube-search-matching.md`](agent-notes/youtube-search-matching.md).
- `Song.nostalgic` / `melancholic` / `party` — integer mood flags, set through the terminal UI.
- `Song.artist_id` is a real SQLAlchemy `ForeignKey`, enforced within `music.db`.
- `Song.youtube_video_id` — persistent cache of the song's resolved video. Set manually for a song
  whose correct video YouTube's own search excludes from results entirely (e.g. age-restricted
  content — confirmed true even for an authenticated Data API request, not just anonymous
  scraping), or automatically by `create_yt_playlist()` itself the moment a fresh search succeeds,
  so a song only ever needs to be found once. Either way, the YouTube sync flow re-validates it
  (`is_video_id_valid()`, no API quota) before reusing it, falling back to a fresh search if it's
  gone stale (deleted/private) rather than trusting it blindly; see
  [`agent-notes/youtube-search-matching.md`](agent-notes/youtube-search-matching.md).

## `tag.db`

```
Tag                             SongTag
---                              -------
id            PK                id             PK
name          UNIQUE, NOT NULL  song_id        NOT NULL (references Song.id in music.db)
                                 tag_id         NOT NULL (references Tag.id, this db)
                                 UNIQUE(song_id, tag_id)
```

- `Tag.name` **is** unique — unlike `Artist.name`, duplicate tags are rejected at the schema level.
- `SongTag.song_id` is a **plain integer**, not a `ForeignKey` — SQLite can't enforce a foreign key
  across two separate database files. Referential integrity between `tag.db` and `music.db` (e.g. a
  `SongTag` row surviving the deletion of its `Song`) is not enforced by either schema; if you write
  code that deletes songs, check `src/utils/database/tags_management.py` for whether it cleans up
  orphaned `SongTag` rows.

## Categories: the search vocabulary, not schema columns

The menu's search/filter UI is built on a category system (`song_categories`,
`search_only_categories`, `artist_categories` in `datatables.py`) that's a query-time concept
layered on top of these columns, not a schema feature. The category split has real gotchas (which
categories are valid for which entity, non-obvious result ordering) — see
[`agent-notes/database-search.md`](agent-notes/database-search.md) before adding a new searchable
field or category.

## Identifier & normalization policy

There's no canonicalized "identifier" beyond the SQLite `id` primary keys — songs and artists are
resolved by fuzzy string matching against `name`/`title`, not a stable external ID (no MusicBrainz
MBID stored, for instance). All comparison goes through the single centralized normalizer before
matching. See [`agent-notes/normalization-and-matching.md`](agent-notes/normalization-and-matching.md)
for how strings are canonicalized and compared, and why short strings need a stricter threshold.
