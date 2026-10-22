# AGENTS.md

Single source of truth for AI agents working in this repo. Persistent knowledge lives in exactly
two places: **this file** (curated, always-read, edited in place — never a dated changelog) and
**`docs/agent-notes/<topic>.md`** (deeper per-subsystem rationale). The `finalize-change` skill
maintains both.

Durable content that used to sprawl across root-level session reports (`OPTIMIZATION_*.md`,
`PERFORMANCE_*.md`, `LOCAL_DB_OPTIMIZATION.md`, `REFACTORING_NOTES.md`, `METADATA_FALLBACK*.md`,
`NORMALIZATION_GUIDE.md`) has been folded into these two places. Those reports were deleted in
2026-08 — see `git log` / `git show` if you ever need the originals. Don't recreate that pattern.

## Project

- Purpose: manage and enrich a local music database from local files and external metadata sources.
- Primary implementation: Python, SQLite via SQLAlchemy, an interactive terminal menu UI. Not a
  server — `main.py` is a desktop app the user runs and clicks through.
- A mobile client exists: MusicDatabaseApp (separate Flutter repo, `../MusicDatabaseApp`). It
  downloads `music.db` / `tag.db` / `files.db` from the sharing server and reads them directly, may work out
  missing data (so far: YouTube links) with its own simplified logic kept only on the phone, and
  will later submit phone-found data here for review (not designed yet) —
  [ADR-0003](docs/decisions/0003-phone-client-fills-gaps.md). MusicDatabase's values always win.
  Keep SQL and matching/domain logic in `src/utils/*`, not in the `src/menu/` handlers, so a
  future review intake can reuse it.

## Read order

1. This file.
2. `.agents/local.md` if it exists — gitignored, machine-specific facts and per-developer
   workflow permissions (template: `.agents/local.example.md`).
3. For unfamiliar territory: [`docs/index.md`](docs/index.md) → [`docs/systems/index.md`](docs/systems/index.md)
   to find which subsystem owns the area, then [`docs/architecture.md`](docs/architecture.md) /
   [`docs/data-model.md`](docs/data-model.md) if you need layout or schema.
4. The `docs/agent-notes/` topic note(s) for that subsystem — index at the bottom of this file.
5. Verify behaviour in the source before editing. The codebase is ~7k lines; read the actual
   function rather than trusting a summary.

## Sources of truth

1. Source code. There is no migration tool yet — the SQLAlchemy models are authoritative for
   schema: `src/utils/database/datatables.py` for `music.db`, `src/utils/database/create_tag_db.py`
   for `tag.db`.
2. Tests and `requirements.txt` (pinned).
3. This file.
4. `docs/agent-notes/`.
5. Plans and conversation notes.

When this file disagrees with the code, trust the code and fix this file.

## Standard commands

- Bootstrap: `python3 -m venv venv` (Python ≥ 3.13; README.md → Requirements for older systems) →
  `source venv/bin/activate` → `pip install -r requirements.txt`. The first `python main.py`
  without databases asks to download the public catalog or start empty
  (`utils/database/first_run.py`; runbook → "First start without databases").
- Run the app: `python main.py`
- The automatic pass over the phone's requests, by hand: `venv/bin/python auto_resolve_requests.py`
  (normally the `musicdatabase-inbox` timer runs it; it does nothing while the app is open)
- Test tools (pytest): `pip install -r requirements-dev.txt` — kept out of `requirements.txt`,
  which lists only what the app itself needs (every package pinned, the transitive ones too; a
  package nothing imports any more goes, with the pins only it needed - check in a throwaway venv)
- Focused tests: `venv/bin/python -m pytest tests/test_<area>.py`
- Full suite: `venv/bin/python -m pytest` — ~1,250 tests, under a minute, fully mocked (see Testing & verification)
- Manual diagnostic/timing scripts: `python tests/manual/<script>.py` (excluded from pytest
  collection; they work on temporary copies of the databases - `tests/manual/_sandbox.py`)
- Before any out-of-band DB write: `ps aux | grep main.py` (see Database safety)

Use the venv and the pinned versions. Don't install globally or bump dependencies unless the task
requires it. The project is GPLv3 (`LICENSE`, user's choice 2026-10-21, same as the phone app): a new
dependency must be GPL-compatible (MIT, BSD, Apache 2.0, LGPL, GPLv2-or-later, GPLv3 — not
GPLv2-only or proprietary).

## Module map

One line per module — what it owns, and where the depth lives. Read the linked topic note before
non-trivial work in that area.

- `src/settings.py` — runtime paths and env-based config (SMB creds — see Secrets; this machine's
  folders — see Machine settings). `database_dir`
  defaults to `src/database/` but can be overridden via Settings → Server → Database location in the menu
  (persisted by `utils/database/database_location.py`); takes effect after a restart, not
  immediately — why: `docs/runbooks/database.md`. `local_library_dir` is the mp3 collection
  (`MUSIC_LIBRARY_DIR`), on the data drive since 2026-10-12 (an older copy on another disk is no
  longer the source). Its old
  `.mp3_index.sqlite3` (made by some external tool; still used by "Copy songs from storage" and
  `tags_fetcher`) stores local paths on the data drive; `files.db` below is meant to replace it.
- `src/utils/database/files_index.py` — `files.db`, the music library index: one row per audio
  file under `local_library_dir` (path relative to it), its `songs.id` matched with
  `read_file_metadata()` + `LocalSongMatcher`, artist/title from tags as a fallback. Settings →
  Server → "Index music library", and once a day at startup after the backup, right before the
  databases are copied for the phone (which downloads `files.db` too). Only new/changed files are
  re-read, and old unmatched ones are rematched only when the catalog fingerprint changes — the
  shared artist search is slow (~65 ms per lookup). Schema only from `migrations/files/`;
  merges/deletions reach it through `song_references.apply_song_id_changes()`. Details:
  `docs/data-model.md`, `docs/runbooks/database.md`.
- `src/config/constants.py` — every magic number / threshold / menu label. Add new ones here;
  don't inline them. In particular the `"Singles"` album sentinel ("no real parent album" — the
  fetchers, the singles-marker fallback, Years' per-title lookup and duplicate resolution all
  agree on it) is `SINGLES_ALBUM`; never write the literal.
- `src/utils/database/` — DB access, sessions, and the search/getter layer + song/artist
  **category** system. Non-obvious ordering and category gotchas (alphabetical-not-best-match
  results, `search_only_categories` is song-only, shared fallback filter):
  `docs/agent-notes/database-search.md`. `backup.py` / `migrations.py` / `database_location.py` —
  automatic backups, schema migrations, and the database folder override, all resolved at
  startup: `docs/runbooks/database.md`. `fetch_data_settings.py` — persisted cap on how many songs
  a single "Fetch database data" run processes (Settings → Max songs to process per fetch),
  shared across every "Fill missing data" category (Albums, Years, ...), not just albums.
  `sharing.py` / `sharing_server.py` copy the DBs for the MusicDatabaseApp phone client and serve
  them. The copy runs after each daily backup and from Settings → Server → "Submit database for sharing";
  it goes to `Settings.sharing_dir` on the data drive. The server is a systemd user service
  installed from Settings → Server → "Setup this PC for database sharing". It serves only `music.db` /
  `tag.db` / `files.db` and, with `--library`, the library's audio files by `files.id`
  (`GET`/`HEAD /library/<id>`, looked up in the shared `files.db`, path checked to stay inside
  the library, byte ranges for the player, no token), and takes the phone's requests (`PUT /inbox/<name>.db` → `Settings.phone_inbox_dir`/`new/`, signed with a login token when the name carries a user,
  at most 1 MiB each, nothing while `new/` holds 100 MiB), syncs a logged-in user's playlists
  (`/users/<name>/playlists`, below), port 8002, only on the named LAN interface from `Settings.sharing_lan_interface`, the VPN of `SHARING_VPN`
  (empty / `tailscale` = the Tailscale IP, `off`, or any provider's interface by name → `--vpn-interface`)
  and the exact `SHARING_ADDRESSES`. It never binds to `0.0.0.0` unless
  that's one of them, and the interface is chosen by
  name because docker bridges also have private IPs. The app depends on that contract, so don't change
  the port or file names unilaterally (nor `/library/<id>`, nor the inbox URL, name pattern or answers, nor the playlist / mood / global-mood / cover / listens / saved-songs / later-links / `GET /users/<name>`
  endpoints and their JSON, nor the folder endpoints, nor the permission names).
  `inbox_requests.py` reads that inbox and settles dead YouTube link reports;
  `inbox_field_corrections.py` does the same for the phone's field corrections (title, year, an
  artist's origin, an album's title, a YouTube link or music video set by hand, ...; the song's
  artist, `songs.artist_id`, comes as a name and credits only that song to the artist of that name
  via `set_primary_artist()`, never renaming the shared row; an `artists.name` rename to a name not
  resembling the old one is offered as crediting only its song first, and never saved by the
  automatic pass), `inbox_found_links.py` for links the phone found for
  songs without one, and `inbox_song_requests.py` for songs the phone played that music.db doesn't
  have (`song_requests`): never added automatically - the owner adds them (through
  `run_import_batch()`), skips or rejects them; a rejected one goes to music.db's
  `rejected_song_requests` (migration 0015), which the phone reads so it doesn't ask again. Videos found bad (gone, unplayable, or wrong for a song) are remembered in
  music.db's `bad_youtube_videos` (migration 0010, `inbox_requests.mark_bad_videos()`), so a later
  request naming one is turned down without asking YouTube again, and the phone, which reads the
  table, stops using them. A trigger on `song_id_changes` keeps its `song_id` current. `inbox_auto.py` is the automatic pass: every 15 min a
  systemd user timer (`musicdatabase-inbox`, `auto_resolve_requests.py` at the repo root, installed
  with the sharing service) takes `Inbox/new/`. It replaces a dead link, or fills a missing one the phone
  found, only when the phone's and our search agree and the video plays. It saves a correction only from a user with the
  `apply_corrections` permission and when nothing conflicts. The rest goes to `Inbox/review/` with
  a note why. Manage database → "Resolve new requests" (`utils/ui/inbox_request_review.py`) runs the
  same pass, then asks about `review/`. Otherwise a phone's value never lands without review. The
  pass never runs while the app is open (`app_lock.py`: `main.py` holds an flock shared, the pass
  needs it exclusive). What it changed is printed at the next start (`Inbox/auto_report.json`).
  Details: `docs/runbooks/database.md` → "Requests from the phone". The app also depends on the `songs` / `artists` /
  `additional_song_artists` schema and on `songs.youtube_video_id`'s values (video id = linked,
  `"N/A"` = confirmed no video, NULL/`""` = unknown, which the phone may search for itself) —
  coordinate schema or sentinel changes with the app
  ([ADR-0003](docs/decisions/0003-phone-client-fills-gaps.md)). The service also lives outside
  the repo, so record changes to it in `~/system-docs/README.md` too. Details: `docs/runbooks/database.md`.
  `latin_names.py` — Latin forms of names in other alphabets (`songs.title_latin`,
  `artists.name_latin`, `albums.title_latin`, migration 0011; "Кино" → "Kino"), which Leina Radio
  shows as "Кино (Kino)" and sends as field corrections. Only the owner writes them (song/artist
  editor, Manage database → "Fill in Latin forms of names in other alphabets" =
  `utils/ui/latin_forms_review.py`, or a reviewed correction); suggestions come from
  `utils/common/romanize.py`. Search treats them as names, matching as synonyms / second titles:
  code that hands an artist's synonyms to matching passes `artist_match_synonyms(artist)`, not
  `artist.synonyms`. Details: `docs/data-model.md`.
  `genres.py` — `songs.genre` (migration 0013), read by Leina Radio's song radio: genres
  comma-separated, main one first, **one spelling per genre** across the database (the phone only
  matches equal names). Every write goes through `clean_genres()` / `set_song_genre()`
  (MusicBrainz's genre list `src/config/musicbrainz_genres.txt`, `GENRE_SYNONYMS`, then the
  spelling music.db already uses); never assign `song.genre` raw text.
  `song_kinds.py` — `songs.kind` (migration 0014, `'full_album'` = a whole album in one recording),
  written only by `set_song_kind()`; `songs_for_entry()` is the rule every song matcher applies so
  a track never matches the full album of its name. Details: `docs/data-model.md`,
  `docs/agent-notes/normalization-and-matching.md`. Filled by Fill missing data
  → "Genres" (`utils/discoveries/genre_lookup.py`, MusicBrainz: song tags → album → artist, the
  artist level ticked by the owner first; `utils/ui/genre_review.py`), the editor, Song actions →
  "Set genre", or a reviewed correction. Details: `docs/data-model.md`.
  `song_duplicates.py` — duplicate songs of one artist (titles differing only in spelling, a
  "feat." credit or a junk bracket like "(Visualiser)"; remix / cover / live brackets are songs of
  their own) and `merge_songs()`, the one way to fold a duplicate: the kept row (most data) gets
  the other's missing fields and extra artists, references follow via `record_song_merge()`.
  "Resolve duplicated artists" folds spelling-only ones silently (`remove_duplicate_songs()`,
  with `remove_duplicate_artists()`, the pair repeated only while a round still merged something);
  Manage database → Resolve duplicates → "Resolve duplicated songs"
  (`utils/ui/song_duplicate_review.py`) asks in one checkbox list, version-looking pairs unticked,
  then in a second one whether to credit the featured artists only a folded title named
  (`missing_feat_artists()` → `credit_feat_artists()`, role `feat`, an existing artist reused).
  `artist_reassignment.py` — crediting a song to another artist row (`set_primary_artist()`)
  instead of renaming the shared row, removing an artist left without songs (album rows go to its
  heir, so album ids stay stable), and the swapped "Title - Artist" fixes; see Coding conventions.
  `song_artists.py` — the only way to read/query a song's artists (primary `songs.artist_id` +
  `additional_song_artists`, role `main`/`feat`): labels, search filters, YouTube credits, and
  `reassign_additional_artist_links()` plus `merge_artist_synonyms()`, which every artist merge must
  call (the latter keeps the deleted row's synonyms and name, otherwise lost with it).
  `albums.py` owns `albums` / `album_covers` and keeps `songs.album_id` in step with the
  `songs.album` text through a `before_flush` listener on the music session factory. Change the
  text and the row follows; never set `album_id` by hand. Raw-SQL artist merges bypass the listener,
  so every artist merge must also call `reassign_artist_albums()`. The listener only relinks the
  songs being flushed: when a song leaves a title it shared with another artist, the songs left
  behind keep `album_id` NULL until someone calls `link_songs()` on them (`artist_reassignment`'s
  `_link_album_title()` does). Titles several main artists
  share stay NULL for Manage database → "Resolve albums with several artists"
  (`utils/ui/album_resolution_review.py`), which first offers to merge one artist spelt twice and
  to fix swapped artist/title songs (`artist_identity.py`), the usual reason a title looks shared. Rules, the "Singles" one-song albums, stable ids:
  `docs/data-model.md`. `users.py` — users and their private data (main menu → Users): one folder per user under `Settings.users_dir` on the data drive holding `user.db` (playlists,
  private tags; schema only from `migrations/user/`) and `account.json` (salted scrypt password
  hash). Those files are originals, so backups and startup migrations cover every user. A drive
  that isn't mounted means users are skipped, never created on the system disk. Main menu → Users
  (`src/menu/main_menu/users/`) creates users, logs into one (`verify_user()`), and lists /
  renames / re-passwords / deletes them. Delete moves the folder to `archive/deleted-users/`, it doesn't erase it.
  The phone logs in as a user through the sharing server (`POST /login` → a token; `account_auth.py`,
  standard-library only because `sharing_server.py` imports it too). Tokens are kept only as hashes in
  `Users/<name>/tokens.json`; re-password / rename / delete must keep dropping them
  (`revoke_all_tokens()`). A request file name's user part is always a verified sender. Details:
  `docs/runbooks/database.md` → "Logging in from the phone". Permissions the owner grants
  (Users → Permissions, `users.set_permissions()`) live in `account.json`, not `user.db`; a new one
  goes into `account_auth.PERMISSIONS` + `constants.PERMISSION_LABELS`. Leina Radio ignores names it
  doesn't know, so adding one needs no app change; renaming or removing one does. The server reads them per
  request (`/login` and `GET /users/<name>` answer them).
  `user_datatables.py` has the `user.db` models (keep them in step with `migrations/user/`),
  `user_playlists.py` saves / deletes playlists and reads them back (`playlist_songs()`:
  the music.db `Song` rows in order, plus the tracks with no song). "Show playlists" shows them
  through the same `display_songs()` / `display_song_links()` tables as Fetch songs, and lists the
  tracks without a song underneath, since those tables need `Song` rows. Playlists sync with the
  user's phones through the sharing server (`GET/PUT/DELETE /users/<name>/playlists`); the rules
  (uid, per-user revision counter, newer `updated_at` wins, deletion = archived JSON + marker row,
  song ids resolved against the shared music.db) are in `playlist_sync.py`, standard-library only
  because the server imports it. Every write to `playlists` / `playlist_tracks` must go through
  it, or bump the revision with `mark_changed()`, or the phones never see the change. Details:
  `docs/runbooks/database.md` → "Playlists on the phone".
  Covers of playlists and moods (picked on the phone): `covers.py` (standard-library) stores the
  pictures as files `Users/<name>/covers/<sha256>.png|.jpg`, never in a DB; rows hold only
  `cover_sha256` (user migration 0006). Every sync `connect()` requires the newest user migration
  the sync code reads (`playlist_sync.EXCLUDED_TRACKS_MIGRATION`, 0007: `playlists.excluded_source_tracks`,
  the tracks removed from an imported playlist; a PUT without it keeps the stored list); bump that
  constant when a new one adds a synced column, or the server writes a column a user.db lacks. Served and
  uploaded at `/users/<name>/covers/<sha256>`; private covers of global moods sync through
  `/users/<name>/global-mood-covers` (`mood_sync.py`). A PUT without `cover` keeps the stored one.
  Unreferenced blobs go only after 24 h (the phone uploads before the PUT naming it). Details:
  `docs/runbooks/database.md` → "Covers on the phone".
  Moods ("Nastroje" on the phone): `tags.kind` (`'mood'` / `'marker'`) in tag.db and user.db.
  `spellchecked` / `album_checked` are markers and must never show up as moods. Names are unique per
  file across kinds. A user's own moods sync like playlists through `mood_sync.py`
  (standard-library, built on `playlist_sync.py`, own `mood_revision` counter, same-name moods
  merged). `moods.py` owns global moods (tag.db), promoting a user's mood into one, and applying
  the phones' global-mood changes. Those come from `PUT/DELETE /global-moods/<id>/songs/<id>` by users
  with `edit_global_moods` and are only *recorded* by the server (`global_mood_log.py`, files in
  `Settings.global_mood_changes_dir` on the data drive): the server never writes tag.db. The app applies them at
  startup, before every sharing copy and in the Moods menu. Owner's menu: Users → Moods
  (`menu/main_menu/users/moods.py`). Details: `docs/runbooks/database.md` → "Moods on the phone".
  Folders of Leina Radio's Biblioteka (holding playlists and moods) sync the same way through
  `folder_sync.py` (standard-library, user migration 0008, own `folder_revision` counter,
  `/users/<name>/folders`): items are stored exactly as sent, never checked, unknown types kept.
  A playlist or mood merge on the server rewrites folder refs to the kept uid
  (`playlist_sync.replace_folder_refs()`); a new merge path must call it too. Folder calls need
  0008, playlists/moods don't. The menu doesn't show folders. Details: `docs/runbooks/database.md`
  → "Folders on the phone".
  Plays for Leina Radio's listening stats sync through `listen_sync.py` (standard-library, user
  migration 0009, own `listen_revision` counter, `GET/POST /users/<name>/listens`): only stored and
  handed back, a play only added or lengthened (the longer `listened_ms` wins), fields kept as sent
  (unknown keys in `extra`), `song_id` never checked nor remapped by `song_references`. Details:
  `docs/runbooks/database.md` → "Listening stats on the phone".
  Leina Radio's liked songs, songs and links saved "Na później" sync through `saved_sync.py`
  (standard-library, user migration 0010, `/users/<name>/saved-songs` and `/later-links`, one
  counter each): the folder rules, one item = one song on one list or one link, stored verbatim
  (JSON `data`, never checked); a DELETE of a uid never seen stores the marker. Saved songs follow
  song merges (`follow_song_merges()` from `song_references`, the whole log, a deleted id kept).
  Details: `docs/runbooks/database.md` → "Saved songs and links on the phone".
  `song_references.py` keeps every reference to
  `songs.id` from another file valid: tag.db's tags, files.db's library files, and users' playlist
  tracks, private tags and saved songs (a mood whose songs change gets a new revision too).
  music.db logs each id that stops existing in `song_id_changes`. Deletions are logged by a
  flush listener; merges must call `record_song_merge()` and bulk `query().delete()` must call
  `record_song_deletions()`. Each referencing file replays the log past its own cursor in
  `apply_song_id_changes()`. A deleted id is never handed to a new song (the same listener numbers
  new songs). Rules and why: the module docstring and `docs/data-model.md`. Session-factory
  listeners (this one, `albums.register_album_sync()`) are guarded by a `WeakSet` of registered
  factories, not `event.contains()`: SQLAlchemy keys that by `id()`, so a new factory reusing a
  collected one's address looked registered and never logged (flaky in tests, one factory each). `artist_splitting.py` —
  splitting a joined name ("A feat. B", "A x B") into separate artists, always behind the
  `review_artist_splits()` batch review (Manage database → "Split joined artist names", and at the
  end of every `run_import_batch()` for the artists it created); rejected names persist in
  `data/artist_split_ignore.json`. Schema and split rules: `docs/data-model.md`.
- `src/utils/database/public_export.py` / `public_database.py` / `github_release.py` — the public
  copy of music.db / tag.db for Leina Radio phones without the sharing server, as the assets of one
  GitHub Release (fixed tag `database` in `GITHUB_DB_REPO`; `music.db`, `tag.db`, then
  `version.json` last), daily after the backup when changed (switch in Settings → Server → "Public
  database on GitHub") and from that menu. **Allow-list:** a table or column reaches the public
  file only when listed in `MUSIC_SPEC` / `TAG_SPEC` (other columns stay, NULLed — same schema as
  the server's copy); a new migration's data stays private until someone lists it on purpose, and
  `check_public_database()` refuses the publish otherwise (user / files.db tables, withheld values,
  local paths, integrity). tag.db: moods only. The tag, asset names and `version.json` are the
  app's contract. `request_packages.py` (Manage database → "Import a request package") puts a
  hand-sent `.zip` of the phone's outbox into `Inbox/new/` under the server's own checks; its files
  are recorded in `Inbox/packages.json` and their user part is a typed name, never a login
  (`RequestFile.sender` None — no permissions). Details: `docs/runbooks/database.md` → "Public
  database on GitHub and request packages". The same release is also the starting catalog of a
  fresh install: `first_run.py` (asked by `utils/ui/first_run_prompt.py` from `main.py` while
  music.db is missing) downloads it, checks size + SHA-256 against `version.json` and SQLite
  integrity, and moves both files in only together; or creates empty databases through the
  models. So `version.json`'s `files.<name>.size` / `sha256` and `format` are this repo's own
  contract too. Details: `docs/runbooks/database.md` → "First start without databases".
- `src/utils/discoveries/` — external-metadata fetchers, the MP3-tag import (metadata-building only
  now — the actual resolve/create/conflict-review engine moved to `import_engine.py`, shared with
  `utils/youtube/import_from_playlist.py`). `import_engine.LocalSongMatcher` is the read-only
  "which existing song is this file?" lookup, shared by the cover import and playlists from a
  folder. It's deliberately looser than the import's own `find_existing_song()` check: it matches
  the main artist of "A ft. B", a tag title that starts with the artists ("A & B — Title",
  `import_engine.title_after_artists()`), and titles with brackets, trailing junk, a trailing
  unbracketed "feat. X" (tried after the title with it, so an original beats its "ft." version), a
  trailing year ("... [Full Length] 2006", on both sides, so "Party Like It's 1999" still meets itself), a leading track number
  ("T03: ", `text_utils.strip_track_number_prefix()`) and words standing for "&"
  (`TITLE_AMPERSAND_WORDS`: "and", "i", "und", ...; `normalize()` drops the "&" itself) stripped. Don't swap one
  for the other: a loose "already exists" in the import would skip genuinely new songs.
  `folder_playlist.py` is Users → Enter user database → "Create playlist from folder". It puts
  every mp3 in alphabetical path order. Files with no song are walked through one by one (name,
  folder, what was read). For each, the user can search by title across all artists
  (`import_engine.find_songs_by_title()`, for files with no usable artist). Picking a candidate
  isn't final: the user confirms it or switches to the manual search. They can also search the
  database (`search_and_pick_db_object()`; one file per cleared screen, the previous file's
  decision on top, a key press first when auto-accepted files were listed), add it as a new song (`folder_playlist.add_song_for_file()`,
  the existing artist/song reused), keep it in the playlist without a song (a track with
  `song_id` NULL), or leave it out of the playlist (the file itself is never touched). Only "Stop"
  leaves the playlist unsaved. A file whose tag artist the artist search can't find (a typo: "Liiquid tension
  experiment" — the search is substring-based, so it gets no candidates at all) gets
  `LocalSongMatcher.match_similar()`'s suggestion first — unless it's the same title with an artist
  spelt at least `load_auto_accept_artist_similarity()` alike (Settings → "Auto-accept similar
  artist names", `utils/discoveries/file_matching_settings.py`, default 0.9, 1 = always ask;
  `match_confident()`), which is matched without asking, listed after matching and offered the
  name/tag fix — (the tags' artist and title swapped, else a
  similar title of the tag artist — `FILE_TITLE_SIMILARITY_THRESHOLD`, "Losing TimeGrande Finale",
  or one the file's title starts with, `FILE_TITLE_PREFIX_MIN_LENGTH`, "Tubthumping  Officiel …",
  never taken by `match()` since nobody confirms there; for a file with no artist, the one song
  with its title, also not in `match()`, and when 2–5 songs share it, a list to pick from
  (`title_candidates()`, `FILE_TITLE_CANDIDATES_MAX`) — else
  same title by a resembling artist — its name, a synonym, or one part of a joined name not split
  yet, "College" of "College & Electric Youth" — only when exactly one song fits), which the user confirms.
  When none of those fits, up to 3 closest songs by `LocalSongMatcher.best_guesses()` /
  `match_score()` are offered (title, and the artist when the file has one, the tags also read with
  artist and title swapped; the title alone must
  reach Settings → "Closest songs: minimum score", default 0.6, kept with the auto-accept setting in
  `file_matching_settings.py`), the cursor on "None of these". Every suggestion shows its score; the
  only one reaching Settings → "Closest songs: auto-accept score" (default 0.99) is taken without
  asking and listed as auto-accepted — for a file with no artist (scored on its title alone) only the
  one song with its title, and only a distinctive title (`import_engine.is_distinctive_title()`,
  `NAMELESS_AUTO_ACCEPT_MIN_WORDS` / `_CHARS`: "Intro" always asks). `match()` (files.db, covers) takes
  `match_similar()`'s safe guesses without asking, never the title-only ones nor these. After saving, files whose song was confirmed or picked are offered "Fix the names and
  tags" (`mp3_utils.rename_to_song()` → "Artist - Title.mp3", never overwriting), the rest a plain
  tag update (`mp3_utils.write_song_tags()`, which never writes the "Singles" sentinel as an album).
  `file_fixes.py` is Manage database → "Fix names and tags for mp3 files that are in the database"
  (`utils/ui/file_fix_review.py`): a folder's files matched by `build_folder_playlist()` without
  asking (no closest-song guesses), renamed / re-tagged after their song on one confirm; files in
  the library folder get a warning (a renamed file is new to files.db).
  `folder_youtube_playlist.py` is "Create playlist from folder and compare with YT playlist": the
  same folder playlist, each file paired with a video of a YouTube playlist (same database song, the
  song's own link, else resembling artist + title; the rest by hand from the unpaired ones — only the
  `FILE_PAIR_VIDEO_CHOICES_MAX` most alike listed plus a search, since a `questionary` list taller
  than the terminal draws its hidden part as blank lines), whose
  link becomes the track's (`FolderTrack.youtube_link`, which `assign_song()` keeps). A video
  without a file can be a track with `file` None. Deleted / private videos and those giving neither
  an artist nor a title are skipped (`youtube_entries()`), only counted. Saved in the YouTube playlist's order, unpaired
  files last, with source `folder`, not `youtube`: Leina Radio re-imports `youtube` playlists from
  their source.
  `mp3_covers.py` is Fetch database data → "Import covers from mp3 tags": embedded APIC pictures → one ≤600 px JPEG per album in
  `<database_dir>/covers/`, matched to songs like the tag import (`read_file_metadata()`,
  `find_existing_song()`), non-square video frames skipped; see its docstring and
  `docs/runbooks/database.md`. `docs/agent-notes/discovery-modules.md` (fetcher
  loading, result validation, shared browser, scraping failure modes) and
  `docs/agent-notes/import-pipeline.md` (why import is slow, the shared engine).
- `src/utils/common/normalizer.py` — the one canonical string `normalize()` / `compare()`. Never
  write a second one. `fix_cp1250_mojibake()` repairs Polish tags misread as Latin-1 ("Ma³pa"),
  only when a never-genuine character shows it. `strip_site_names()` drops download-site junk from mp3 file names and tags
  before they're read (domains in `SITE_NAME_TLDS`, the words in `src/config/file_name_junk.txt` —
  the owner's list, add entries there — and Y2Mate's id/bitrate/timestamp suffix). `normalize()` is `lru_cache`d (pure function of its arguments): the artist
  search re-normalizes every artist per lookup, so keep it free of side effects and global state. `docs/agent-notes/normalization-and-matching.md`.
- `src/utils/common/text_utils.py` — similarity helpers and `check_spelling()`. Signature and
  short-string-threshold pitfalls: `docs/agent-notes/normalization-and-matching.md`. The
  `check_spelling()` cost path and return shape: `docs/agent-notes/import-pipeline.md`.
  `copy_to_clipboard()` falls back to an OSC 52 terminal escape sequence when `pyperclip` finds no
  clipboard mechanism — the normal case when the app is launched via the root `MusicDatabase-
  Remote.sh` script, which runs the whole program on a remote host over `ssh -t` (no local
  display for xclip/xsel/wl-clipboard to attach to). OSC 52 asks the local terminal emulator
  itself to set the clipboard, so it works headless; the plain "Clipboard unavailable" warning
  only prints if that also fails (e.g. non-interactive stdout, or a terminal that doesn't support
  OSC 52 writes).
- `src/utils/common/script_utils.py` — matching across alphabets: `matching_key()` romanizes both
  sides of a comparison (Cyrillic incl. uk/be letters, Greek, Georgian, Hangul, kana; no
  kanji/hanzi/Arabic yet, which would need new dependencies), used by `text_utils.title_resembles()`
  and the fetch manager's artist check. `cyrillic_search_variant()` gives a Latin-transliterated
  query a Cyrillic spelling for one more search when the rest of the entry is Cyrillic. Separate
  from `utils/youtube/transliteration.py`, the precise Belarusian Lacinka used for YouTube search.
  `docs/agent-notes/normalization-and-matching.md`.
- `src/utils/common/romanize.py` — `suggest_latin()`: a readable Latin form to *offer* for a
  name (English-style Russian "Alyona"/"Zhuki", Ukrainian national system, Belarusian Lacinka
  via `transliteration.py`, Serbian Latin, Greek, Georgian, Hangul, Hepburn kana; kanji/Arabic →
  None). Never stored without the owner accepting it. Not a matching key — that's
  `script_utils.matching_key()`.
- `src/utils/common/selenium_sessions.py` — one process-wide headless Chrome shared by the
  scraping fetchers. Lifecycle rules (close in `finally`): `docs/agent-notes/discovery-modules.md`.
- `src/utils/common/musicbrainz_client.py` — the single MusicBrainz HTTP entry point (`mb_get()`):
  shared session, process-wide ~1 req/s limiter, hard timeout, split retry policy. Don't call
  `requests.get` against `musicbrainz.org` from anywhere else. Known unmigrated exception:
  `music_brainz_fetcher.py` still goes through the raw `musicbrainzngs` library instead — see
  `docs/agent-notes/import-pipeline.md`.
- `src/utils/common/spellcheck_cache.py` — disk cache for `check_spelling()` results at
  `data/spellcheck_cache.json`. `docs/agent-notes/import-pipeline.md`.
- `src/utils/common/debug.py` — use `slog(var)` / `mlog(message)`, not bare `print()`. Console
  output is gated by a `.debug` file (`verbosity = N`) at the repo root, and so is what reaches
  `debug.log` (gitignored): without `.debug`, only `slog(..., priority=3)` gets there. Exception:
  `flog(message)` - *why a lookup came up empty* (a fetcher returning nothing, a result the manager
  discarded, a yt-dlp ERROR line, a Spotify page that didn't load, which is then saved to
  `debug_spotify.html`) - is always appended with a timestamp, so an intermittent failure can be
  diagnosed afterwards. Grep `debug.log` for `***Lookup***`. Use `flog()` for new failure paths
  in fetchers/searches, not `slog()`.
- `src/utils/youtube/` — YouTube playlist/download helpers, `score_result()` match ranking
  (DB → YouTube), and `import_from_playlist.py` (YouTube → DB, "Import data from YouTube
  playlist"). `docs/agent-notes/youtube-search-matching.md`. Whether a video plays:
  `video_playability()` (yt-dlp: True / False / None = YouTube blocked the check — rate limiting,
  "not a bot" wall — never treat None as dead) and `videos_exist_via_api()` (Data API, batched,
  no bot wall; a region block in `YOUTUBE_REGION` counts as gone; only its False is final). `ytdlp_update.py` — once a day after the backup `main.py`
  offers a newer yt-dlp from PyPI (`utils/ui/ytdlp_update_prompt.py`) and re-pins
  requirements.txt. yt-dlp is installed as `yt-dlp[default]` (with `yt-dlp-ejs`) plus the pinned
  `deno` package as its JS runtime, all in the venv; without deno YouTube blocks it much sooner.
- `src/utils/ui/menu_utils.py` — shared menu flows, including `search_and_pick_db_object()`. Reuse
  it for "search then resolve to one DB row" rather than re-duplicating.
  `docs/agent-notes/database-search.md`.
- `src/menu/` — the terminal menu tree (presentation + workflow wiring). Main menu → Help
  (`menu/main_menu/help.py`) explains the menus to a newcomer: a renamed or new menu entry it
  quotes needs its text updated (`tests/test_help_menu.py` checks every quoted label exists). Its
  "Libraries" topic (`LIBRARIES`) describes every library the program imports itself, with the
  same text in README.md → Libraries: a new direct dependency gets a line in both (the test fails
  otherwise); "What happens at start-up" is built from `startup_settings` with the switches' state. "Background services (Linux)" explains the four systemd units; its
  option (`TOPIC_ACTIONS`) prints them via `sharing.unit_files()` — read-only, from disk or from
  the same `_service_unit()` / `_timer_unit()` / `_inbox_*` the setup writes with: a change to a
  unit updates that topic's text too. Known fragility:
  importing `menu.song_actions` before `menu.main_menu` triggers a circular import
  (`song_actions/__init__.py` ↔ `main_menu/enter_database/fetch_songs/__init__.py`) — doesn't
  affect `main.py` itself (its own import order avoids it), but is why `tests/test_scripts.py` is
  excluded from pytest collection (`tests/conftest.py`). Not yet fixed; not a reason to avoid
  importing `menu.song_actions` normally through `main.py`'s own path. Separately,
  `manage_database/resolve_duplicates.py` (the submodule) is shadowed by
  `manage_database/__init__.py`'s own `resolve_duplicates()` *function* of the same name — a plain
  `from ...manage_database import resolve_duplicates` binds the function, not the submodule; tests
  that need the submodule (e.g. to monkeypatch its `get_music_session`) must
  `importlib.import_module("menu.main_menu.enter_database.manage_database.resolve_duplicates")`
  instead (see `tests/test_resolve_duplicates.py`).
- `src/menu/main_menu/enter_database/manage_database/clean_names.py` (Manage database → "Clean names") — per-field song
  cleanup rules all go through `_apply_field_cleanup(...)`. Add rules via that helper, not another
  per-field loop. `seek_nonsense_names()` first auto-strips a known trailing junk annotation off the
  title via `strip_trailing_junk()` (plain suffix and bracket-wrapped, e.g. "Song Music Video" or
  "Song 《Official Music Video》" -> "Song" — any bracket pair, since uploaders also use CJK ones)
  with no confirmation, then queues whatever's still blacklisted (album/title) and asks about it in
  one batch at the end (see the Coding conventions entry above). Its exact-phrase list,
  `TITLE_JUNK_SUFFIXES` (`constants.py`, applied by `text_utils.strip_trailing_junk_suffix()`),
  is shared with the YouTube playlist import, which uses it for *unbracketed* trailing junk
  ("Golden Official Lyric Video" -> "Golden"). The import's bracket-only lists
  (`YOUTUBE_TITLE_JUNK_PHRASES`/`*_MARKER_WORDS`, see `docs/agent-notes/youtube-search-matching.md`)
  stay separate on purpose: they also match marker words anywhere in a bracket, which is too loose
  to auto-apply to existing data. A new exact phrase goes in `TITLE_JUNK_SUFFIXES` once and is
  caught in both places. `resolve_unknown_artist()` still applies its
  artist/title split unconditionally (no yes/no there) but prints one summary instead of pausing
  per song.
- `tests/` — regression tests, catalogued in [`tests/README.md`](tests/README.md) (what each file
  covers — keep it in sync with the tests). `tests/test_scripts.py` is an explicit scratch file
  (its own docstring: "meant to be a mess"), excluded from collection via `tests/conftest.py`.
  `tests/manual/` holds manual diagnostic scripts (real imports + live network), excluded from
  collection via its own `conftest.py`. Each calls `_sandbox.enter_sandbox()` before importing the
  app: the databases are copied into a temp folder and the app (database folder, `.env`, `debug.log`,
  `youtube_link_cache.log`, verbosity) is pointed there - a new script must do the same.

## Architecture boundaries

- `src/menu/` drives workflow and presentation. Keep SQL and matching logic in `src/utils/*`, not
  in menu handlers — a future API (e.g. the intake for phone-submitted data) would call the same
  `src/utils/*` layer rather than repurposing `src/menu/`. Why this is a recorded decision, not
  just a convention: [ADR-0001](docs/decisions/0001-ui-independent-business-logic.md).
- The MusicDatabaseApp phone client reads the shared SQLite files directly, so `music.db`'s schema
  and stored values are an interface too: migrations touching `songs` / `artists` /
  `additional_song_artists`, or a change to what `youtube_video_id` may hold (especially
  `NO_VIDEO_SENTINEL`), need coordinating with the app. The same goes for `albums`: the app keys
  its cover cache on album ids, so they must stay stable (merge, never renumber). It also still
  reads `songs.album`, so that text column stays filled until the user says the app has moved to
  `album_id`. It also reads `bad_youtube_videos` (`song_id` NULL = bad for every song, else
  wrong for that song; `reason` codes from `constants.BAD_VIDEO_REASONS`, only shown there), and
  the Latin forms `songs.title_latin` / `artists.name_latin` / `albums.title_latin`, and
  `songs.music_video_youtube_id` (migration 0012: the official clip, a video id or NULL, only a
  link to open — never played, matched, put in playlists or in `bad_youtube_videos`), and
  `songs.genre` (migration 0013, its radio; keep one spelling per genre, see `genres.py`), and
  `songs.kind` (migration 0014: NULL = a song, `'full_album'` = a whole album in one recording,
  kept as a song row; only `song_kinds.set_song_kind()` writes it, and the app leaves full albums
  out of its radio), and `rejected_song_requests` (migration 0015: songs the owner didn't want
  added, by the artist / title / YouTube video / Spotify track the request named). Matching keeps a track apart from the full album of the same name
  (`song_kinds.songs_for_entry()`, entries carry `duration_s`); candidates are reviewed in Manage
  database → "Find full albums".
  [ADR-0003](docs/decisions/0003-phone-client-fills-gaps.md).
- One implementation per cross-cutting concern: normalize/compare → `normalizer.py`; MusicBrainz
  HTTP → `musicbrainz_client.mb_get()`; headless browser → `selenium_sessions`; diagnostics →
  `debug.py`. Don't add a parallel local version of any of these.
- Constants and config load from `constants.py` / `settings.py`; don't scatter literals.
- SQL goes through SQLAlchemy and stays parameterized — never build a query by string-concatenating
  user input.

## Database safety

- Two SQLite DBs under `src/database/` (gitignored): `music.db` (catalog: `artists`, `songs`) and
  `tag.db` (`tags`, `song_tags` — a many-to-many onto songs). `music.db` also has
  `additional_song_artists` (extra artists of multi-artist songs; `songs.artist_id` stays the
  primary one) — see the `src/utils/database/` Module map entry — and `albums` / `album_covers`
  (migrations 0005–0008). Cover *images* are never stored in a DB file: they're in
  `<database_dir>/covers/`, which isn't backed up (the cover import regenerates them). Each
  user's private `user.db` is a third kind, on the data drive (`Settings.users_dir`), with its own migration
  track `migrations/user/` — see the `users.py` Module map entry. `files.db` (the music library
  index, `migrations/files/`, next to music.db) is a fourth. Those files and tag.db point at
  `songs.id` with plain integers, so deleting or merging a song must go through the ORM (or log
  itself via `song_references.py`), and `songs.id` must never be set or reused by hand. `datatables.py` /
  `create_tag_db.py` are the schema's source of truth for the *current* shape; schema history is
  tracked by the migration runner in `src/utils/database/migrations.py`. Full procedure (backups,
  restore, adding a migration): [`docs/runbooks/database.md`](docs/runbooks/database.md).
- Backups mostly happen automatically: `main.py` takes one at startup (once per calendar day) and
  another, unconditionally, immediately before applying any pending migration. Don't skip either
  when changing this code path — a schema change without a fresh backup right before it is exactly
  the failure mode this mechanism exists to remove. The daily one is the owner's to switch off
  (Settings → Start-up tasks, `STARTUP_BACKUP=off`); then every start warns once the newest backup
  is over 7 days old. The pre-migration one never is.
- Start-up (`utils/ui/startup_tasks.py`, called by `main.py`): switchable, one `.env` key each
  (`utils/common/startup_settings.py`, empty = on, `off` = skipped, read from the file at start):
  the daily backup, applying phones' global mood changes, the daily library index, the daily copy
  for phones, the daily yt-dlp offer, the start-up messages; the daily GitHub publish keeps its own
  switch in `public_database.py`'s state, shown on the same screen. With the backup off, "the day's
  first start" comes from `data/startup_state.json`. Never switchable: the app lock wait, the
  fresh-install database choice, migrations, the album backfill after its migration, applying song
  id changes, resuming the sharing setup — a new start-up step says which kind it is. Settings → Server → "Back up database now" also lets the
  user trigger `backup_databases()` on demand, for a snapshot right before something risky.
- `main.py` may be writing to `music.db` right now. Before any direct/out-of-band SQL write, check
  `ps aux | grep main.py`; prefer the app's own edit flow, or ask the user to pause it. A direct
  write has already been caught racing a live session mid-edit (title cleanup) and clobbering
  in-progress work.
- Add a schema change via a new file in `migrations/<db_name>/`, never by hand-editing a DB file or
  relying on `Base.metadata.create_all()` (which only creates missing tables — it never alters an
  existing one). See the runbook before adding one.

## Data and paths that are not fixtures

- `import/` — the user's real personal MP3 collection, used as the import staging folder. Don't
  move, rename, bulk-delete, or commit its contents.
- `data/` and `src/database/` — runtime/user data, not fixtures (both gitignored).

## Secrets

- `SMB_USERNAME` / `SMB_PASSWORD` load from `.env` (gitignored) into `src/settings.py`, used to
  build `smb://` URIs for the local library. Never hardcode, print, or log these.
- `YOUTUBE_API_KEY` (optional, `.env`) — a read-only YouTube Data API key for
  `videos_exist_via_api()`. Same rules. The OAuth token in `.secrets/token.json` (playlists) is a
  separate credential and expires; without the key and a valid token the API check is skipped.
- `GITHUB_DB_TOKEN` (`.env`) — fine-grained GitHub token, Contents: read and write on
  `GITHUB_DB_REPO` only, for the public database release. Same rules; GitHub errors are
  reported without it.
- `.secrets/client_secret.json` (Desktop-app OAuth client, Google Cloud project `<gcp-project>`
  since 2026-10-13) + `.secrets/token.json`, both `chmod 600`. `get_youtube_service()` discards a
  token issued to another client and logs in again in the browser. An OAuth app left in "Testing"
  gets its refresh token revoked after 7 days (`invalid_grant`); deleting the project behind the
  client gives "Project … has been deleted" (403).

## Machine settings

- This machine's folders and network interface load from `.env` (gitignored) into
  `src/settings.py`, every key documented in `.env.example`: `MUSIC_LIBRARY_DIR` (the mp3
  collection), `SHARING_DIR` (the shared folder, which also holds `Users/`, `Inbox/` and
  `MoodChanges/`) + `SHARING_DRIVE_MOUNT` (the external drive it's on; empty = the system disk,
  where the folder only has to exist — `settings.sharing_dir_available()` is the one rule),
  `SHARING_LAN_INTERFACE`, `SHARING_VPN` (empty = `tailscale`, `off`, or a VPN interface of any
  provider) + `SHARING_ADDRESSES` (what else the server listens on), and the
  `REMOTE_*` keys read by `MusicDatabase-Remote.sh`. A missing key never falls back to someone's
  real path: the feature is skipped with a message naming the key, as for an unplugged drive
  (`settings.drive_unavailable_message()`; the drive-path `Settings` fields are then `None`).
- Settings → Server → "Folders and network" sets the folder and LAN keys in `.env`
  (`utils/common/env_file.py`: `MACHINE_KEYS`, `save_env_value()` keeps every other line), warning
  about a folder that isn't there, a mount point that isn't mounted or a sharing folder off the
  drive. `Settings` reads `.env` once at startup, so a change needs a restart.
- The sharing server's own paths are in its systemd unit (written by "Setup this PC for database
  sharing" from these settings), so changing `.env` reaches the server only after that setup is
  re-run.
- "Setup this PC for database sharing" first offers the step-by-step setup for a newcomer
  (`utils/ui/sharing_wizard.py`; validation, suggestions and the read-only checks in
  `utils/common/sharing_setup.py`, which "Folders and network" shares — `value_warning()`,
  `lan_interfaces()`): each key explained with REQUIRED / OPTIONAL, stopping at any step safe.
  Nothing happens without a yes (owner's rule): saving a value, creating the folder, the restart,
  the first copy, a user, the service, lingering — a "no" is listed under "Not done" in the
  summary; only reading and checking go unasked. It never recommends Tailscale (or any way of
  reaching the PC): it lists the addresses the server will listen on (`listening_plan()`). After a
  change it offers to restart the app in place (`os.execv`, `main.py --continue-sharing-setup` →
  straight into the checks). A new machine
  setting the server needs goes into its `SETTING_STEPS` too.

## Coding conventions

- Prefer small, focused changes in existing modules. Preserve the current CLI flow unless a
  redesign is explicitly requested.
- Reuse the shared helpers listed in the Module map rather than reintroducing local equivalents.
- Comments for non-obvious constraints, not line-by-line narration.
- `questionary` prompts (`confirm()`, `select()`, etc.) don't interpret raw ANSI color codes
  embedded in their `message`/`instruction` strings — it builds its own `(style_class, text)`
  tokens and renders them through prompt_toolkit's formatted-text machinery, so a literal
  `\033[93m...\033[0m` shows up as garbage instead of color. To highlight something in a prompt,
  `print()` a plain colored line above the `questionary` call instead (see `seek_nonsense_names()`
  in `clean_names.py` for the pattern). A `questionary.Choice` title can be coloured,
  though, given as prompt_toolkit `(style, text)` tokens: `utils/ui/song_colors.py` builds artist
  (cyan) / title (yellow) labels that way and turns them into ANSI only for `print()`.
- Every yes/no question goes through `utils/ui/prompts.ask_yes_no()` (a bare Enter means yes,
  everywhere — the owner's choice), never `questionary.confirm()` directly; a test checks that.
- If a change touches setup, entry points, commands, or architecture, update this file in place.
- In user-facing text (prints, menu labels, prompts, review notes) the mobile client is "Leina
  Radio", its name — never "the phone". Comments and docstrings may say phone. Permissions belong
  to a MusicDatabase user, not to a device.
- A loop that finds several things needing a yes/no decision (a near-duplicate, a spelling
  correction, a junk-looking field) should queue them and ask in one batch after the loop, not
  interrupt per item. Eight places already do this: `run_import_batch()` (shared by every
  importer — mp3-tag and YouTube-playlist import both feed it, see below),
  `check_spelling_menu()`, `seek_nonsense_names()`, and `import_from_playlist.py`'s
  `_review_artist_title_swaps()` (flags a parsed title that matches an existing artist, then asks
  Add/Swap/Don't add — see `docs/agent-notes/youtube-search-matching.md`),
  `_review_missing_artists()` (same file; asks for the artist of every entry the parser left
  without one, e.g. a `"Release - Topic"` upload whose description credits nobody) — these two
  are held back and asked only after `run_import_batch()`'s slow main loop, through its
  `late_entries` callback, so the user isn't kept waiting for the import to start; do the same
  for any new pre-import question — and `review_artist_splits()`
  (`utils/ui/artist_split_review.py`, one checkbox list of joined artist names to split), and
  `fill_missing_years.py`'s `_review_unsure_years()` (one checkbox list of years a module flagged
  `needs_review`, e.g. YouTube upload dates; tick the ones to change), and
  `offer_album_resolution()` (`utils/ui/album_resolution_review.py`, end of `run_import_batch()`:
  the album titles its new songs share with other artists) — see
  `docs/agent-notes/import-pipeline.md` for the fullest write-up (dedup-by-shared-object gotcha
  included) and follow the same shape for a new one rather than inventing another. That's for
  candidates *discovered* by scanning; a bulk `song_actions` op invoked directly on an
  already-selected list (e.g. `swap_titles_and_artist_names()`) instead asks one upfront confirm for the
  whole batch, since there's nothing to discover — but still needs the same
  dedup-by-`song.artist.id` gotcha if it mutates the shared `Artist` row (first song to hit a given
  artist wins, later ones by the same artist are skipped with a message, not silently re-applied).
  An `Artist` row is shared by all its songs, so the song side never renames it: the song editor
  (`edit_entry_menu()` in `edit_songs.py`) and Song actions offer "artist" / "Change artist"
  (`utils/ui/artist_change.change_songs_artist()` → `artist_reassignment.set_primary_artist()`:
  the song is credited to another, found or new, artist; an artist left without songs can be
  merged into it or deleted), while the artist's name / origin / Latin form are edited only from
  Artist actions → Edit (renaming onto an existing name offers a merge). "Swap title and artist
  names" (`swap_titles_and_artist_names()`, the single-song editor and Song actions share it)
  renames the artist row only when it has no other song, or when the user picks "for all its
  songs"; otherwise the song moves to an artist named like its old title
  (`artist_reassignment.move_swapped_song()`, also behind `artist_identity.fix_swapped_song()`).
  The editor's "additional artists" entry (`edit_additional_artists_menu()`) is the only UI for
  multi-artist credits so far.

## Testing & verification

- `python -m pytest` runs the full suite (config in `pyproject.toml`; pytest from `requirements-dev.txt`) — ~1,250 tests, under a minute, all
  mocked, no real network calls. `python -m pytest tests/<file>.py` for one file while iterating.
  See [`tests/README.md`](tests/README.md) for what each file covers.
- `tests/conftest.py` cuts every test off the real databases and `.env` (`database_dir` → a missing
  folder, `.env` → an empty temp one), so a test that forgets to patch a DB engine fails every time
  with "unable to open database file". It also sends `debug.log` and `youtube_link_cache.log` to that
  temp folder. Give it temp-file databases; never undo the isolation. A function taking the `.env`
  path defaults to `None` (resolved to `env_file.ENV_FILE` when it runs), never `= ENV_FILE`: a
  default bound at import would keep pointing at the real `.env`.
- Don't claim a check passed unless you ran it in this workspace.
- Ask before creating or changing regression tests unless the task explicitly requires it.
- If the user is mid-import or mid-spellcheck, don't start the full suite alongside it.
- If you rename or remove something a test covers, update that test (and its row in
  `tests/README.md`) in the same change. A test can silently stop being collected or start
  asserting against an API that no longer exists, and nothing else will catch it —
  `test_import_mp3.py` sat broken and undetected for ~3.5 months this way before being deleted.

## Performance notes

- Importing MP3 tags is bottlenecked on MusicBrainz network calls, not local work. Full picture:
  `docs/agent-notes/import-pipeline.md`.
- Both the import path and the spell-check menu try the local DB first, and only fall back to
  MusicBrainz when there's no good local match.
- `check_spelling()` results — including "no match" answers — are cached on disk
  (`spellcheck_cache.py` → `data/spellcheck_cache.json`), so a re-import only pays network cost
  for genuinely new `(artist, title)` pairs. Delete the file to force fresh lookups.
- MusicBrainz tunables (`MUSICBRAINZ_API_TIMEOUT`, `MUSICBRAINZ_API_MIN_INTERVAL`,
  `MUSICBRAINZ_SPELLCHECK_USE_FALLBACK`, `SPELLCHECK_CACHE_FILE`) live in `constants.py`. An
  import prints an `MBStats` summary (requests, cache hits/misses, 429/503 counts, time throttling
  vs. in requests) at the end.

## Tools

- Check `tools/README.md` before writing a new reusable script; extend an existing tool if it
  already owns that operation family rather than adding a near-duplicate.
- `tools/db_inspect.py` — read-only report of both DBs (row counts, applied migrations, whether
  `main.py` is running). Run it before any direct, out-of-band DB read/write instead of retyping
  an ad-hoc `sqlite3` one-liner.
- Don't create a tool for a one-line command — see `tools/README.md`'s own header for the bar.

## Git policy

- Preserve unrelated working-tree changes. Don't discard, rewrite, or overwrite the user's changes.
- Keep generated files and local DBs out of version control.
- Don't commit, push, or tag unless explicitly asked. Prefer small, single-purpose commits.
- The repo is public on GitHub. Nothing committed may carry this machine's or a user's private
  data — the Tailscale or LAN address, local paths (`/home/…`, `/media/…`), drive or host names,
  account names, the home Wi-Fi's name: write `<tailscale-ip>`, "the data drive", "the owner's
  account", documentation addresses in tests (`192.0.2.10`, `100.64.0.10`), or read the value at
  run time from `.env` (Machine settings). Such values live only in git-ignored local files
  (`.env`, `.agents/local.md`) (user's rule, 2026-10-21); what stays on this computer only
  (`~/system-docs`) may keep them.

## Topic notes (`docs/agent-notes/`)

- [database-search.md](docs/agent-notes/database-search.md) — the `database_getter` search API and
  category system: why `get_artists_from_db_session()` returns alphabetical order rather than best
  match (the "Sting" → "POLKADOT STINGRAY" bug), the shared `_normalized_python_filter` fallback,
  why `search_only_categories` must never reach artist search (`KeyError: None`), and the shared
  `search_and_pick_db_object()` flow.
- [normalization-and-matching.md](docs/agent-notes/normalization-and-matching.md) — the one
  canonical `normalizer`, the three ordered fallback stages of `split_artist_title()` (used for mp3
  filename parsing via `extract_unknown_data()`, and directly for YouTube video titles), the three
  distinct `text_utils` similarity functions (and the `TypeError` from
  confusing them), why artist-name matching uses `scaled_similarity_threshold()` for short
  strings, and why a typo inside an artist name defeats the local fuzzy lookup (substring-only
  candidates) and how `LocalSongMatcher.match_similar()` works around it, and why the duplicate-song
  key keeps non-junk brackets (remixes and covers are songs of their own).
- [discovery-modules.md](docs/agent-notes/discovery-modules.md) — why `discoveries_manager.py`
  re-validates every fetcher's result instead of trusting each module, the `DiscoveryResult`
  contract, why the Settings menu reads `MODULE_NAME` by static parsing, the shared headless-Chrome
  lifecycle (including the snap-Chromium `DevToolsActivePort` gotcha and `ChromeDriverLaunchError`),
  the last-resort fallbacks when every fetcher is empty (soundtrack-like artist "Tekken 5 OST" →
  artist name as album; a `SINGLES_MARKER` word like "cover"/"unplugged" → `SINGLES_ALBUM`), `google_search_fetcher.py`'s two look-alike failure modes (cookie consent vs. CAPTCHA — don't
  try to evade the latter), why `spotify_fetcher.py` and `youtube_fetcher.py` scrape their public
  web players' markup instead of the official (credential-requiring) APIs, how
  `discovery_stats.py` counts per-fetcher invocations/successes for the Statistics menu, and the
  separate `get_release_year()` contract/config/stats for "Fill missing data -> Years" (including
  why it batch-writes a year to every song sharing an album, unlike the per-song album fetchers,
  why a hand-set `Song.spotify_url` is read before any module,
  and why `youtube_fetcher.py`'s YouTube-date years are flagged `needs_review` and reviewed by the user).
- [import-pipeline.md](docs/agent-notes/import-pipeline.md) — the MusicBrainz cost path shared by
  every importer via `utils/discoveries/import_engine.py`'s `run_import_batch()` (mp3-tag import
  and YouTube-playlist import are both just metadata builders feeding the same engine now — see
  youtube-search-matching.md for the YouTube-side builder): why MusicBrainz `check_spelling()`
  dominates, the layered defenses (DB-first shortcut, disk-backed cache, process-wide rate limiter /
  timeout / split retry in `musicbrainz_client`), why the fielded query gets only one attempt, the
  `check_spelling()` return shape, the `MBStats` run summary, why ambiguous-artist and similar-song
  matches are both queued (`pending_artist_conflicts`, `deferred_entries`) and asked about in two
  end-of-batch passes — artists then songs — instead of interrupting the import per entry, and the
  `Song.youtube_video_id` backfill an import can silently apply to an existing (not newly-added) row.
  The "Spell check existing data" menu (`check_spelling_menu()`) batches its own artist/title
  correction prompts the same way, with a `song.artist.id`-keyed dict to dedupe the shared-`Artist`-
  object rename question.
- [youtube-search-matching.md](docs/agent-notes/youtube-search-matching.md) — covers both
  directions under `src/utils/youtube/`. **Import FROM a playlist** (`import_from_playlist.py`,
  "Import data from YouTube playlist" menu item): why playlist fetching must use yt-dlp's
  `--flat-playlist` (without it, a large playlist blows past the subprocess timeout and looks like
  a hang), the Topic-channel/channel-name artist-detection fallback, the three extensible title
  cleanup word lists (`YOUTUBE_TITLE_JUNK_PHRASES`, `YOUTUBE_TITLE_JUNK_MARKER_WORDS`,
  `YOUTUBE_TITLE_JUNK_MARKER_WORDS_ANYWHERE`) and diacritic-insensitive `strip_artist_from_title()`,
  why album-name scraping was investigated but not implemented, `SPECIAL_DESCRIPTION_CHANNELS`'
  reverse-search resolution (`resolve_special_channel_metadata()`) for channels whose titles carry
  no "Artist - Title" structure at all, and a since-fixed debug video limiter. **Search matching**
  (DB → YouTube, the older
  half of the file): why
  `manage_youtube_playlists.score_result()` is built the way it is (title-only relevance,
  containment matching, dynamic thresholds, Topic-channel/official-release awareness via `track`
  metadata, popularity/tags signals, typo tolerance, the `artists.synonyms` column for real name
  changes, a `MIN_ARTIST_RELEVANCE` floor, and `Song.youtube_video_id` as both a manual escape
  hatch for a video excluded from search results entirely and a self-populating cache — written by
  `save_video_id_to_song()` after every fresh search hit, re-validated by `is_video_id_valid()`
  before reuse, and cleared back to `None` if a stale link fails both validation and a fallback
  re-search, both logged to `youtube_link_cache.log`; see also `NO_VIDEO_SENTINEL`, the separate
  `"N/A"` human annotation for "confirmed no video exists at all," settable via the songs menu's
  "Report no YouTube video"; playlists still treat it as an empty field, only Years' YouTube
  date lookup and the album lookup's last-resort read of the stored link skip the song),
  `transliteration.py`'s alternate-script
  retry for a title only findable under the other alphabet (Cyrillic↔Lacinka only so far, triggered
  when the winning pick isn't a confirmed official release, not by a relevance floor), why a collab
  track credited in the DB solely to a featured/guest artist defeats matching entirely (no scoring
  fix can help — it's a `songs.artist_id` data problem), a regression checklist of real wrong-match
  bugs it fixes, and remaining open limitations (non-Cyrillic scripts). Regression tests in
  `tests/test_youtube_search.py` are pinned to the exact expected video per song via two mirrored
  parametrized suites over the same ~36 real cases —
  `test_regression_suite_resolves_via_fresh_search` (no DB involved at all) and
  `test_regression_suite_resolves_via_db_reference` (resolving via `Song.youtube_video_id`
  instead) — so a resolution change in either path is a test failure to review, not a silent
  update. Also: the MusicDatabaseApp phone app has its own, intentionally simpler matcher for
  songs with no link here — not a port, not kept in sync.
