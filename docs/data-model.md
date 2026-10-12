# Data model

Two shared SQLite databases (plus one private `user.db` per user, see the end), each its own SQLAlchemy `Base` / engine / session — see
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
                                 spotify_url
                                 album_id       FK -> Album.id (NULL = see below)
                                 track_number   NULL = unknown
                                 disc_number    NULL = 1 / unknown

Album  (table albums, migration 0005)    AlbumCover  (table album_covers, migration 0008)
-----                                    ----------
id            PK                         album_id    PK, FK -> Album.id
title         NOT NULL                   source      PK, 'file'|'itunes'|'deezer'|'musicbrainz'|'youtube'|'manual'
artist_id     FK -> Artist.id, NULL =    url         remote sources
              various artists            path        'file'/'manual': relative to <database_dir>/covers/
year                                     width, height, bytes
type          NULL | 'album' | 'ep' |    status      NOT NULL, 'found' | 'missing' (checked, none there)
              'single' | 'compilation'   checked_at  NOT NULL, default datetime('now')
              | 'soundtrack'
musicbrainz_release_group_id, itunes_collection_id, deezer_album_id, spotify_album_id
              (external ids for later fetchers; nothing fills them yet)

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
  is already an artist in the DB ("Coldplay X BTS"). "Group (A, B)" is a group plus its lineup
  ("Męskie Granie Orkiestra 2026 (Igor Herbut, Zalia, Vito Bambino)") and is proposed as all
  main, never pre-selected, since members are sometimes co-performers and sometimes guests. A
  split that would cut through a bracket ("X (A" + "B)") is never proposed. Earlier imports
  stored names like "Męskie Granie Orkiestra 2020 (Daria Zawiałow" that way. Why this shape and not extra columns:
  [ADR-0002](decisions/0002-multiple-artists-per-song.md).
- Albums (migrations 0005–0008, `src/utils/database/albums.py`). `Song.album` (free text) is
  kept **and kept filled**: the MusicDatabaseApp phone client reads it, so it can only go once the
  app has moved to `album_id`. `Song.album_id` follows it automatically. A `before_flush` listener
  (`register_album_sync()`, installed on `music_db_manager.SessionLocal`) finds or creates the album
  row whenever a song is added or its `album` text or primary artist changes. No writer of
  `songs.album` needs to do it itself; never set `album_id` by hand. The rules:
  - Albums match on `normalize(title)` plus the album artist, so "Ray of Light" and "Ray Of Light"
    by Madonna are one row.
  - A title only one main artist uses becomes that artist's album automatically, `year` = the
    songs' most frequent year, `type` NULL (unknown).
  - `SINGLES_ALBUM` ("Singles") is a sentinel, not an album. Each such song gets its own one-song
    album, `type = 'single'`, titled like the song (a single has its own cover) and kept in step
    with the song's title, year and artist. The song's text stays "Singles".
  - A title several main artists use (a compilation or soundtrack, or two unrelated albums that
    share a name) is **not** decided automatically. Those songs keep `album_id` NULL until the user
    settles them in Manage database → "Resolve albums with several artists"
    (`utils/ui/album_resolution_review.py`), which is also offered at the end of an import that
    creates such a case. The exceptions: if a compilation (artist NULL) with that title already
    exists, a new song joins it, and a song whose own artist already has that album joins that.
  - Album ids are **stable**, because the phone keys its cover cache on them: rows are never
    renumbered or rebuilt. Merging two albums (`merge_albums()`: the resolver's compilation choice,
    or an artist merge where both artists had the album) keeps one id and moves songs, covers and
    external ids onto it. An album no song points at any more is kept, so a typo fix and back
    finds the same row and cover.
  - Artist merges change `songs.artist_id` with raw SQL, which the listener never sees, so they
    call `reassign_artist_albums()` (both `merge_artists_in_db()` and
    `resolve_duplicates.remove_duplicate_artists()` do). When only a song's artist changes through
    the ORM (an artist split), the album row moves with it if none of its other songs stays with
    the old artist.
  - Existing songs were linked by `link_unassigned_songs()` right after migration 0006 was applied
    (`main.py`). It groups every title before deciding, so no artist gets a shared title ahead of
    the others. Safe to re-run.
- Cover images are **never stored in `music.db`**, because the phone downloads the whole file. They
  live in `<database_dir>/covers/` (`albums.covers_dir()`, gitignored like the DBs): `<album_id>.jpg`
  for source `'file'`, `<album_id>-<source>.jpg` for any other stored source
  (`albums.cover_filename()`). Each image is at most 600 px, JPEG quality 85. Only the mp3 import
  writes covers so far (`utils/discoveries/mp3_covers.py`). The sharing server doesn't serve them
  yet.
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
  The MusicDatabaseApp phone client reads this column too: an id = linked, `"N/A"` = confirmed no
  video (the app won't search), NULL or `""` = unknown (the phone may search YouTube itself and
  keep its answer locally). Changing those meanings affects the app —
  [ADR-0003](decisions/0003-phone-client-fills-gaps.md).

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

## References to `songs.id` from other files

`tag.db`'s `song_tags`, `files.db`'s `files` and every `user.db`'s `playlist_tracks` / `song_tags`
point at `songs.id` with plain integers, which SQLite can't enforce across files. `src/utils/database/song_references.py`
keeps them valid:

- **`song_id_changes`** (music.db, migration 0009) is an append-only log of ids that stopped
  existing: `old_song_id` → `new_song_id` (the song a merge kept) or NULL (deleted). Deletions
  through the ORM log themselves (a `before_flush` listener). Merges call `record_song_merge()`
  (today only `resolve_duplicates.remove_duplicate_songs()`, which keeps the lowest id), and
  bulk `query().delete()` calls `record_song_deletions()`.
- **Each referencing file replays it** past its own cursor (`applied_song_changes`: tag migration
  0002, user migration 0003, files migration 0001) in `apply_song_id_changes()`. Merged: tags move
  to the kept song (one it already has is dropped), playlist tracks and library files point at it.
  Deleted: tags go, playlist tracks keep their title/artist/video with `song_id` NULL, so the
  playlist still shows them, and library files get `song_id` NULL (`match_source` back to
  `auto`) so the next scan can match them again.
  It runs at startup, at user login, and after any commit that logged something
  (`database_sessions` submit/close). A user.db on an unplugged T7 catches up later. A new user
  starts at the end of the log.
- **Ids are never reused.** `songs.id` has no AUTOINCREMENT, so SQLite would give the next new
  song the id of the newest one just deleted, and its old tags and playlist entries would land on
  that new song. The same listener numbers new songs above every id `songs` or the log has ever
  held. That also makes replaying the log twice harmless.
- tag.db before this: deleting a song left its tags behind and merging duplicates dropped the
  deleted copy's tags (143 such rows on 2026-10-12, all `spellchecked`/`album_checked`
  markers). The first replay into tag.db deletes rows whose song no longer exists.

## `files.db` (the music library index)

`<database_dir>/files.db`, built by `src/utils/database/files_index.py` (Settings → Server →
"Index music library", and once a day at startup). Schema only from `migrations/files/` (created
by `migrations.initialize_db()`, no bootstrap); `LibraryFile` mirrors it. The phone downloads it
with `music.db` / `tag.db`.

- `files`: one row per audio file under `Settings.local_library_dir`. `id` is AUTOINCREMENT, never
  reused (a moved or renamed file is a new row). `path` is relative to the library folder with
  `/` separators — never an absolute server path. `size` + `mtime` decide whether a scan re-reads
  the file. `song_id` = `songs.id` or NULL; `artist` / `title` from the ID3 tags (else the
  filename) as a fallback; `duration_s`; `indexed_at` (UTC). Several files may share a song.
- `match_source`: `auto` rows without a song are matched again (`LocalSongMatcher`, local only);
  `manual` rows are never touched by a scan. Nothing sets `manual` yet.
- `index_state` (key/value): `catalog_fingerprint`, a hash of artist names/synonyms and song
  titles. Old unmatched files are rematched only when it changes (~45 s for ~700 files, because
  the shared artist search reloads every artist per lookup); new or changed files always are.
- `applied_song_changes`: the replay cursor, as in tag.db.

## `user.db` (one per user)

`Settings.users_dir/<username>/user.db`: a user's private data. The schema comes only from
`migrations/user/` (there's no SQLAlchemy model yet), and the password hash is kept outside it in
`account.json`. Why one file per user: [ADR-0004](decisions/0004-per-user-data.md).

```
playlists                         playlist_tracks
---------                         ---------------
id          PK                    playlist_id      PK, -> playlists.id
name        NOT NULL              position         PK (0-based order; a song may repeat)
owner                             song_id          music.db songs.id, NULL = not in MusicDatabase
source, source_id ('youtube' + id) title            NOT NULL
created_at, updated_at  (Unix ms) artist, youtube_video_id, duration_s

tags (private)                    song_tags (private)
----                              ---------
id          PK                    id PK, song_id NOT NULL (music.db), tag_id -> tags.id
name        UNIQUE, NOT NULL      UNIQUE(song_id, tag_id)
```

- Same shapes as the phone's own playlist tables and as `tag.db`'s tags. Living in `user.db`
  is what makes them private; `tag.db`'s tags are the global ones.
- `song_id` is a plain integer across files, like `SongTag.song_id`. A track also keeps its
  title / artist / video id, so it survives its song being deleted. Merges and deletions reach it
  through the log above. Models: `src/utils/database/user_datatables.py`.
- `playlists.source` is `'folder'` (Users → "Create playlist from folder", `source_id` = the
  folder) or the phone's `'youtube'`.

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
