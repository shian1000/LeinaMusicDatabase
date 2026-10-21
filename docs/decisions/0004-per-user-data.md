# ADR-0004: Per-user private data lives in one user.db per user, next to the shared databases

- Status: Accepted (built on this side: user folders, the schema, backups, login and user
  management in the terminal menu, playlists from a folder, song id changes reaching user DBs;
  on the phone since 2026-10-13: logging in as a user through the sharing server and sending
  inbox requests signed with that user's token — MusicDatabaseApp's ADR 0006; playlists sync
  with the user's phones through the sharing server since 2026-10-13 on this side, the phone's
  part is the app's ADR 0007; moods - private tags with kind 'mood' - sync the same way since
  2026-10-13 on this side, and permissions the owner grants live in account.json; other
  private tags aren't sent anywhere)
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
   `<SHARING_DIR>/Users/<username>/` (on the external data drive, next to the shared
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
5. **The originals live on the data drive**, unlike `music.db`/`tag.db` there, which are copies. So the
   daily and manual backups include every user's `user.db`
   (`archive/user-<name>.<timestamp>.db`), and `migrations/user/` is applied to each of them at
   startup, after a backup. While the data drive isn't mounted, users are skipped, never created on the
   system disk.
6. **Global data the phone finds goes through a review inbox, not straight in**: requests that
   MusicDatabase reads and the user accepts or rejects. Built 2026-10-13 as one SQLite file per
   request uploaded to the sharing server's inbox and reviewed in Manage database → "Resolve new
   requests" ([runbooks/database.md](../runbooks/database.md)) rather than one shared database. That's the "come back for review" of
   [ADR-0003](0003-phone-client-fills-gaps.md). Private data needs no review: it goes straight
   into that user's `user.db`.

## Not done yet

- `user.db` itself is never served: the sharing server serves only
  `music.db`/`tag.db`/`files.db` (everything else is a 404, including `Users/`). Private tags
  don't reach the phone yet; they'll follow the playlists' pattern.

Since built (2026-10-13): playlists sync between `user.db` and the user's phones, one whole
playlist at a time, through `GET/PUT/DELETE /users/<name>/playlists` on the sharing server (the
hub), the newer change winning a conflict and deletions archived first
([runbooks/database.md](../runbooks/database.md) → "Playlists on the phone"). This is the "may
be sent to the phone one day" above, done per playlist rather than by sending the file.

Since built (2026-10-13): the phone logs in as one of these users through the sharing server
(`POST /login` → a token, kept as a hash in `Users/<name>/tokens.json`) and signs its inbox
requests with it — [runbooks/database.md](../runbooks/database.md) → "Logging in from the phone".

Since built (2026-10-13): moods. A user's own moods are private tags with kind `'mood'`, synced
through `/users/<name>/moods` like playlists. Global moods are `tag.db` tags with kind `'mood'`;
users the owner gave the `edit_global_moods` permission (kept in `account.json`, because it's the
owner's trust and not the user's data) change them from the phone without review, through a log the
app applies — [runbooks/database.md](../runbooks/database.md) → "Moods on the phone".

Since built: song merges and deletions reach users' playlists and private tags, and tag.db, via
an id-change log in `music.db` replayed by each file
([data model](../data-model.md#references-to-songsid-from-other-files)). Main menu → Users can
log in, list, rename, re-password and delete users.

## Consequences

- Each new kind of private data is a new `migrations/user/NNNN_*.sql` file, applied to every
  existing user's `user.db` at the next startup with the data drive mounted.
- A new user's `user.db` gets its whole schema from those files (`initialize_user_db()`). The
  SQLAlchemy models in `src/utils/database/user_datatables.py` mirror them and must be updated
  with each new file.
