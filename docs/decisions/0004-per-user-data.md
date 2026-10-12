# ADR-0004: Per-user private data lives in one user.db per user, next to the shared databases

- Status: Accepted (built on this side: user folders, the schema, backups, login and user
  management in the terminal menu, playlists from a folder, song id changes reaching user DBs;
  nothing on the phone side yet)
- Date: 2026-10-12

## Context

`music.db` and `tag.db` are global: every listener sees the same catalog and the same tags. The
owner wants each user of the MusicDatabaseApp phone client (Leina Radio) to eventually send their
own data to the server, kept there and visible to that user only. Playlists come first, private
tags second, more later. The data isn't sensitive. Users know the admin can read it, so nothing
is encrypted.

Today the phone keeps its playlists only in its own `local.db`. It can only import them from
YouTube (`playlists` + `playlist_tracks`, ordered by `position`, a track pointing at
`songs.id` as `main_db_id` when recognised). It has no tags of its own. There were no users
anywhere.

## Decision

1. **One folder per user** under `Settings.users_dir` =
   `/media/owner/T7/Shared/Music/Database/Users/<username>/` (the T7 SSD, next to the shared
   `music.db` / `tag.db`). It holds `user.db` (the user's data) and `account.json` (username,
   password hash, creation time). Called "Users", not "Private": it's per-user, not hidden from
   the admin. A separate file per user makes "only this user sees it" a matter of which file is
   served, and makes deleting or exporting a user trivial.
2. **`user.db` references `music.db`, with a fallback copy.** A playlist track stores `song_id`
   (`songs.id`) *and* the title / artist / `youtube_video_id` it had. A playlist can hold songs
   MusicDatabase doesn't have (YouTube-only videos). A song id can also disappear: duplicate
   resolution and merges delete rows, and SQLite can't enforce a reference across two files.
   Order is `position`, and the same song may appear twice. The shape matches the phone's own
   tables, so they can be sent unchanged.
3. **Private tags reuse tag.db's shape** (`tags`, `song_tags`) inside `user.db`. Which file they
   live in is what makes them private. `tag.db`'s tables keep their names: the phone downloads
   and reads that file. In the UI they are "global tags" vs. "private tags". A private tag may
   share a name with a global one.
4. **Username + password**, created in main menu → Users → "Create a new user"
   (`utils/database/users.py`). Usernames are lowercase (`USERNAME_PATTERN`), because exFAT
   ignores case. Passwords are stored only as salted `hashlib.scrypt` hashes, in `account.json`
   rather than `user.db`, because `user.db` may be sent to the phone one day. Enough for a
   household. If this ever grows, the login can change without touching the data.
5. **The originals live on T7**, unlike `music.db`/`tag.db` there, which are copies. So the
   daily and manual backups include every user's `user.db`
   (`archive/user-<name>.<timestamp>.db`), and `migrations/user/` is applied to each of them at
   startup, after a backup. While T7 isn't mounted, users are skipped, never created on the
   system disk.
6. **Global data the phone finds goes through a review inbox, not straight in** (planned): a
   separate small database of requests (who, what, payload, status) that MusicDatabase reads and
   the user accepts or rejects. That's the "come back for review" of
   [ADR-0003](0003-phone-client-fills-gaps.md). Private data needs no review: it goes straight
   into that user's `user.db`.

## Not done yet

- Nothing sends, receives or serves `user.db`. The sharing server still serves only
  `music.db`/`tag.db` (everything else is a 404, including `Users/`). Uploading, logging in from
  the phone and the inbox each need agreeing with the MusicDatabaseApp side first.

Since built: song merges and deletions reach users' playlists and private tags, and tag.db, via
an id-change log in `music.db` replayed by each file
([data model](../data-model.md#references-to-songsid-from-other-files)). Main menu → Users can
log in, list, rename, re-password and delete users.

## Consequences

- Each new kind of private data is a new `migrations/user/NNNN_*.sql` file, applied to every
  existing user's `user.db` at the next startup with T7 mounted.
- A new user's `user.db` gets its whole schema from those files (`initialize_user_db()`). The
  SQLAlchemy models in `src/utils/database/user_datatables.py` mirror them and must be updated
  with each new file.
