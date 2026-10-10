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

AdditionalSongArtist  (table additional_song_artists, migration 0003)
--------------------
song_id       PK, FK -> Song.id
artist_id     PK, FK -> Artist.id
role          NOT NULL, 'main' | 'feat'
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
- Multi-artist songs: `Song.artist_id` is always the **primary** artist; each extra one is a row in
  `additional_song_artists` (`role` `'main'` = co-headliner "A x B", `'feat'` = guest "A feat. B").
  Single-artist songs have no rows there. No ordering column on purpose — labels sort extra artists
  by name. Read/query it only through `src/utils/database/song_artists.py` (`song_artist_label()`,
  `song_all_artists()`, `song_has_artist_id()`, ...), and when merging artists call
  `reassign_additional_artist_links()` after reassigning `songs.artist_id`. Older collabs are still
  stored as one joined `Artist` row ("Sw@da x Maxim x Niczos") until split via Manage database →
  "Split joined artist names" (`utils/database/artist_splitting.py`): the first main name becomes
  `songs.artist_id`, an existing artist is reused per part (exact `normalize()`d name or synonym),
  missing parts are created with the joined row's origin, and the joined row is deleted.
  Splitting is always a user decision in one batch review — "&"/"," can't tell "Sw@da & Maxim"
  from "Simon & Garfunkel". The " x " separator is lowercase only, because an uppercase X is
  usually part of a name ("Final Fantasy X OST"). An uppercase " X " splits only when every part
  is already an artist in the DB ("Coldplay X BTS"). Why this shape and not extra columns:
  [ADR-0002](decisions/0002-multiple-artists-per-song.md).
- `Song.spotify_url` — a Spotify album or track page set by hand (Song actions → "Set Spotify
  link", stored normalized by `spotify_fetcher.normalize_spotify_url()`), for a release Spotify's
  own search never surfaces (Cypis' 2016 "Sprawdzian Z Chemii", buried under dozens of newer
  singles). "Fill missing data -> Years" reads the release date straight off it before trying any
  module: `discoveries_manager.release_year_from_stored_link()`. Migration `0004`.
- `Song.youtube_video_id` — persistent cache of the song's resolved video. Set manually (Song
  actions → "Set YouTube link", id parsed by `manage_youtube_playlists.extract_video_id()`) for a song
  whose correct video YouTube's own search excludes from results entirely (e.g. age-restricted
  content — confirmed true even for an authenticated Data API request, not just anonymous
  scraping), or automatically by `create_yt_playlist()` itself the moment a fresh search succeeds,
  so a song only ever needs to be found once. Either way, the YouTube sync flow re-validates it
  (`is_video_id_valid()`, no API quota) before reusing it, falling back to a fresh search if it's
  gone stale (deleted/private) rather than trusting it blindly — and if that fallback search also
  comes up empty, the dead link is cleared from the DB (`save_video_id_to_song(song, None)`) rather
  than left to fail validation again on every future run. Can also be set to the literal string
  `"N/A"` (`manage_youtube_playlists.NO_VIDEO_SENTINEL`) — a human's record that no video for this
  song exists on YouTube at all (as opposed to existing but excluded from search).
  `create_yt_playlist()` normalizes it to "no stored link" and searches normally, same as an empty
  field. The only code that skips on it is Years' `youtube_fetcher.get_release_year()`. Set via the
  songs menu's "Report no YouTube video". See
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
