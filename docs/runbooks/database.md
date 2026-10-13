# Database: backup, restore, migrations, and sharing

Engine: SQLite via SQLAlchemy, two separate database files under `src/database/` (gitignored —
see `AGENTS.md` → Database safety before writing to either directly). Why two files and their
schemas: [`../data-model.md`](../data-model.md). Which layer is allowed to execute SQL:
`AGENTS.md` → Architecture boundaries (`src/utils/database/` only).

## Why this exists

The schema changed by hand at least 3 times in the project's history (`album`, `origin`,
`synonyms` columns were all added to already-existing tables — visible as `# NEW` comments in
`datatables.py`'s git history) with no record of which `.db` copies got which change. Meanwhile
`src/database/archive/` accumulated 200+ manually-made timestamped backups with no automation
behind them — a purely manual habit. This page describes the two pieces that replace both of
those with something repeatable: automatic backups (`src/utils/database/backup.py`) and a minimal
forward-only migration runner (`src/utils/database/migrations.py`).

## Database location

`Settings.database_dir` (`src/settings.py`) defaults to `src/database/`, but can be overridden
from the terminal UI: Settings → Server → Database location → Change database folder (browse via
`open_file_browser_terminal()` or type a path). The override is persisted to
`database_location_config.json` at the project root by `utils/database/database_location.py`;
"Reset to default" deletes that file.

**The change only takes effect after restarting the app, never immediately.** This isn't a UI
shortcoming — it falls out of the import graph: `music_db_manager.py`, `tag_db_manager.py`,
`backup.py`, and `migrations.py` all snapshot `Settings.database_dir` into module-level constants
(`BASE_DIR`, `DB_PATH`, `ARCHIVE_DIR`, `DB_FILES`, `ENGINE`) the instant they're first imported —
and that import already happens while `menu.main_menu` is being imported to build the main menu's
action map, i.e. before `main()` even runs, regardless of which menu item the user ends up
picking. Deferring those imports so they only happen inside "Enter database" was considered and
rejected: Python caches imported modules, so it would only work the *first* time in a session
(before "Enter database" has ever been visited) and would silently skip the daily backup/migration
run on any session that never opens the database menu at all.

## Backups

`backup_databases(reason)` copies every existing DB file (plus every user's `user.db`, see
Users' databases below) into `src/database/archive/`, named
`<name>.<YYMMDD-HHMM>.db` (or `_2`, `_3`, ... if that exact name is already taken — two backups
can legitimately happen a few milliseconds apart, see below). It uses SQLite's **online backup
API** (`sqlite3.Connection.backup()`), not a plain file copy, so it's safe even while the app
holds an open connection to the source file.

Cost: measured at ~2ms per database at this project's current sizes (a few hundred KB each) — the
whole thing is called synchronously and unconditionally; there was no need to make it async or
background it.

Two automatic triggers, both wired into `main.py`:

1. **Once per calendar day, at startup** — `backup_if_needed_today()`. Checks `archive/` for a
   file matching today's date before doing anything; a no-op on every startup after the first one
   that day. Failures here are logged and swallowed — a backup problem must never block the user
   from opening the app.
2. **Immediately before applying any pending migration** — inside
   `migrations.run_pending_migrations()`, unconditionally, even if today's backup already ran.
   Failures here are **not** swallowed: if the pre-migration backup fails, the migration does not
   proceed.

There is no retention/pruning yet — the archive only grows. At current DB sizes and one
backup/day this is on the order of tens of MB/year, which isn't worth solving pre-emptively; if it
becomes a real problem, add pruning as its own small tool rather than baking a retention policy
into the backup call.

### Manual backup

From the app: Settings → Server → "Back up database now" (`backup_database_now()` in
`src/menu/main_menu/settings/__init__.py`), which just calls `backup_databases(reason="manual")`.

From a shell/script:

```python
from utils.database.backup import backup_databases
backup_databases(reason="manual")
```

### Restore

There's no restore tool (a destructive operation like this benefits from a human doing it
deliberately, not a script). To restore an archived snapshot:

1. Confirm the app isn't running: `ps aux | grep main.py`. Close it first if it is.
2. Copy the chosen file over the live one, e.g.:
   `cp src/database/archive/music.260910-1200.db src/database/music.db`
3. If the snapshot predates a migration that's since landed, the next `python main.py` run will
   detect and apply the pending migration(s) automatically (and back up again first) — you don't
   need to replay them by hand.

## Sharing with the mobile app

The MusicDatabaseApp phone client (separate Flutter repo, `../MusicDatabaseApp`, its
`docs/decisions/0002-database-download.md`) downloads the databases once a day over Tailscale.
The user enters a **folder URL** (`http://<tailscale ip>:8002/`) and the app fetches exactly
`<url>/music.db` and `<url>/tag.db` (and, from 2026-10-12, `<url>/files.db` — the music library
index; the app side is being told). It rejects anything that doesn't start with the
`SQLite format 3` header (keeping its previous copy) and swaps the two files as a pair. That
contract (port `SHARING_HTTP_PORT` = 8002, both file names) is relied on by the app, so agree any
change with the app side first. Port 8001 is taken by the APK share.

The app also reads the files' contents directly: the `songs` / `artists` /
`additional_song_artists` tables and `songs.youtube_video_id` (video id = linked, `"N/A"` =
confirmed no video, NULL or `""` = unknown). For songs with no value it may find a YouTube link
itself; that stays on the phone and is dropped once a downloaded `music.db` has its own value. So
when adding a migration that touches those tables, or changing what `youtube_video_id` can hold,
check the app side too. Phone-found data comes back as requests through the server's inbox (below)
and is reviewed here, never applied blindly. See [ADR-0003](../decisions/0003-phone-client-fills-gaps.md).

**Copying** (`utils/database/sharing.py`) puts every file in `backup.DB_FILES` (music, tag,
files) in `Settings.sharing_dir`
(`/media/owner/T7/Shared/Music/Database` by default):

- Automatically once a day in `main.py`: after a *successful* daily backup, the startup
  migrations and the library scan (`scan_library_quietly()`, then `share_databases_quietly()`;
  both log and swallow every failure). The pre-migration backup does not trigger it.
- On demand: Settings → Server → "Submit database for sharing" (`share_databases()`), which prints where
  and when, or the error.
- It uses the online backup API into `music.db.tmp` / `tag.db.tmp` in the same folder. Both temp
  copies are made first, then both are `os.replace()`d in, so a download in progress never gets
  half a file and the pair comes from one moment. Temp names must never be `music.db`/`tag.db`.
- T7 is an exFAT external drive, automounted at login and not in fstab. If
  `Settings.sharing_drive_mount` isn't a mount point, the copy raises `SharingUnavailableError`
  and never `mkdir`s. Creating the folder under an empty mount point would fill the system disk
  instead of T7.

**Serving** (`utils/database/sharing_server.py`) is a stdlib-only script run on the system
`python3` by the systemd user units `musicdatabase-share.service` + `.timer` in
`~/.config/systemd/user/`. Settings → Server → "Setup this PC for database sharing"
(`install_sharing_service()`) writes them; re-running it overwrites the units and restarts the
server. Tests: `tests/test_database_sharing.py`.

- Only `GET`/`HEAD /music.db`, `/tag.db` and `/files.db` are served (`SHARED_FILES`). A change
  to that list needs `systemctl --user restart musicdatabase-share` — the running server read
  the script at start. Everything else is a 404 with no
  directory listing; this is why plain `python -m http.server` is not used. `Cache-Control:
  no-store` is set.
- It listens on at most two addresses, one socket each, with the same handler and port 8002:
  - **Tailscale:** the address from `tailscale ip -4`. The phone uses this one away from home.
  - **LAN:** the private IPv4 address of the interface named in `Settings.sharing_lan_interface`
    (`enp2s0`, passed as `--lan-interface`). The phone uses it on the home Wi-Fi and silently falls
    back to Tailscale when it doesn't answer. An empty setting means Tailscale only.
  - The server was Tailscale-only at first. Adding the LAN address on 2026-10-10 was a deliberate
    widening, not a workaround: anyone on the home network can now download the two files
    (read-only, nothing else).
  - The interface is chosen by *name*, not as "the first private address", because `docker0` /
    `br-*` bridges also have private addresses and must never be exposed. A public or
    Tailscale-range address on that interface is rejected.
  - Each socket waits and retries every 10 s in its own thread until its address exists. A
    missing LAN address never delays the Tailscale socket, or the other way round.
  - It never binds to `0.0.0.0`.
  - The phone has the LAN address typed in (`192.0.2.13`, from DHCP). If the router hands out a
    different one, local access quietly stops working; the fix is a DHCP reservation on the router.
  - `ufw` is installed but disabled (`ENABLED=no`). If it is ever enabled, open 8002 to
    `192.0.2.0/24` only.
- If T7 is unmounted it keeps running and answers 503, so the phone retries the next day.
- `Restart=always`, not `on-failure`: on 2026-10-10 a stray SIGTERM (something killing whatever
  was on port 8002) stopped it for good, because `on-failure` treats a clean SIGTERM as success.
  `systemctl --user stop` still stops it normally.
- The timer uses `OnStartupSec=1min`, counted from the user's systemd manager start. With
  `Linger=no` (the owner chose this on 2026-10-10) that means one minute after login, not after
  boot. That's fine here because the machine logs into GNOME, and T7 is only mounted after login
  anyway.

**Requests from the phone (inbox, 2026-10-13).** The phone keeps an outbox of requests (app ADR
0005, format in the app's `docs/handoffs/musicdatabase-outbox.md`) and sends each as one SQLite file:
`PUT /inbox/[<user>_]YYYY-MM-DD_HH-MM-SS_NNN.db` on the same port and addresses. The service runs
with `--inbox` = `Settings.phone_inbox_dir` (`.../Database/Inbox`):

- The server stores it in `Inbox/new/`, written as a dot-named `.part` file and `os.replace()`d
  in. It never overwrites and never serves inbox files back (a GET is a 404). Answers: 201 stored,
  200 the same file is already in `new/` or `processed/` (a resend), 409 another file under that
  name, 400 bad name or not SQLite, 411 no `Content-Length`, 413 over `PHONE_REQUEST_MAX_BYTES`
  (1 MiB), 507 inbox full, 503 T7 not mounted (nothing is created then).
- **Inbox limit:** while the files in `new/` total `PHONE_INBOX_LIMIT_BYTES` (100 MiB, the owner's
  choice) nothing more is taken; the phone keeps the request and retries later. Processing moves
  files out of `new/`, which frees the room; `processed/` doesn't count.
- Anyone on the home network can send a request, the same trust as downloading. That's why the
  name, size and header checks are there, and why a request only ever reaches the database through
  the review below.
- Manage database → "Resolve new requests (N)" (`utils/ui/inbox_request_review.py`, logic in
  `utils/database/inbox_requests.py`; `main.py` prints a line at startup when some are waiting).
  For `dead_youtube_links`: follows `song_id` through `song_id_changes`, settles without asking a
  report whose song is gone or whose link already changed (another id or "N/A"), groups repeats of
  one (song, dead video), checks with yt-dlp whether the dead link and the phone's video play and
  runs our own `search_video_ytdlp()` (`check_dead_link_cases()`: one batched Data API call for
  every id, yt-dlp only for candidates the API didn't rule out, the dead link not replayed), then
  asks per song: keep the suggested link / keep the
  newfound link (ours) / paste a link / "N/A" / keep the current link / skip. Both are offered even
  when the check says they don't play. The check is `video_playability()`: True / False / None, where
  None means YouTube blocked it (HTTP 429, "confirm you're not a bot") and is shown as "couldn't
  check", never as dead — on 2026-10-13 a video that played fine on the phone looked dead this way. Answers go to `Inbox/decisions.json`; only "kept" is read
  back (it changes nothing in the database, so a pending or repeated report would otherwise be asked
  again, unless it was detected after the answer).
- A file moves to `Inbox/processed/` once every row is settled. It stays in `new/` while a row is
  skipped, while it has a table this version doesn't know (a future request type), or while its
  `request.format` isn't 1. An unreadable file goes to `Inbox/rejected/`.

**Logging in from the phone (2026-10-13).** The phone (app ADR 0006) logs in as a MusicDatabase
user (main menu → Users) and keeps a token, never the password. The service runs with `--users` =
`Settings.users_dir`; the checks are in `utils/database/account_auth.py`, standard-library only
because the server imports it too (as a sibling module), and `users.py` uses it as well.

- `POST /login`, JSON `{"username", "password"}` (username as typed; trimmed and lowercased) →
  200 `{"username": <normalized>, "token"}`, 401 wrong username or password (never says which; an
  unknown user costs the same scrypt time), 400 malformed body, 503 T7 not mounted (hashes and
  tokens only live there — decided not to cache them elsewhere).
- Tokens: 32 random bytes; only their SHA-256 is stored, in `Users/<name>/tokens.json` (not in
  `user.db`, which may be sent to the phone one day). No expiry. `POST /logout` with
  `Authorization: Bearer <token>` revokes one (always 204). `change_password()`, `rename_user()`
  and `delete_user()` drop all of a user's tokens (a renamed user's file names change too).
- Lockout: 5 failed logins within 15 minutes from one client address *or* for one username lock
  that address/username out for 5 minutes (429 + `Retry-After`). In memory only.
- Signed uploads: the user part of a request file name is always a verified sender, so "Resolve
  new requests" can trust it without an extra record. A name with a user part needs that user's
  token (none, unknown or revoked → 401, which the phone takes as "log in again"; another user's →
  403). A name without one is accepted with or without a token (old phones, requests made before
  logging in), but a token that is sent must be valid. Auth is checked after the mount check, so a
  missing T7 is a 503, never a 401. The name pattern's user part is `USERNAME_PATTERN` (may hold
  `_`; the fixed-shape date keeps the split unambiguous).

**Playlists on the phone (2026-10-13).** A logged-in phone syncs the user's playlists with their
`user.db` through the server, which is the hub; phones never talk to each other. The owner's
decisions and the phone's side are in the app's `docs/handoffs/musicdatabase-playlists.md`; the
answer, with the final contract, is [`docs/handoffs/playlists-for-musicdatabaseapp.md`](../handoffs/playlists-for-musicdatabaseapp.md).
The rules live in `utils/database/playlist_sync.py`, standard-library only like `account_auth.py`,
because the server and the app's own writes (`user_playlists.py`) both use it.

- `GET /users/<name>/playlists?since=<revision>`, `PUT /users/<name>/playlists/<uid>` (whole
  playlist + `base_revision`), `DELETE /users/<name>/playlists/<uid>?base_revision=&deleted_at=`.
  Every call needs `<name>`'s token (401 none/unknown, 403 another user's); 503 when T7 isn't
  mounted or that `user.db` hasn't got migration `user/0004_playlist_sync.sql` (the server never
  migrates; the app does at startup). Bodies up to `--max-playlist-bytes` (8 MiB).
- **Revisions:** every change to a playlist, from any writer, gives it the next number of the
  user's `playlist_revision` counter: a phone, the menu (create, delete), and
  `apply_song_id_changes()` when a merge or deletion changes a track's `song_id`.
- **Conflicts:** the newer change wins. A change based on the stored revision is applied; one
  based on an older revision only if its `updated_at` (the device's clock, stored as sent) is
  later, otherwise the answer is 409 with the stored playlist. A deletion uses `deleted_at` the
  same way. A resend after a lost answer gets a 409 that matches what it sent.
- **Same import on two phones:** a PUT of an unknown uid whose `source` + `source_id` match a live
  playlist updates that one instead, and the answer carries the existing uid.
- **Song ids** from a phone's older `music.db` are run through `song_id_changes` of the *shared
  copy* in `--dir` before they're stored (merged → kept song, chains followed, deleted → NULL; an
  id that's neither a song nor in the log → NULL). That copy can be a day behind, so the playlist
  keeps `song_ids_checked_to` = the last log entry checked, and the next `apply_song_id_changes()`
  replays the entries between that and the file's cursor for that playlist only.
- **Deleting** (phone or Users → "Show playlists" → "Delete playlist") keeps the row as a marker
  without tracks, so every phone hears about it. Before the tracks go, the whole playlist is written
  to `Users/<name>/archive/playlists/<uid>.<YYMMDD-HHMM>.json` (the deletion time; `_2`, `_3` …
  if the name is taken; never overwritten). Nothing reads that folder: it's for recovering a
  playlist by hand. It isn't part of the backups; those files are already the backup.
- The server writes `user.db` with `sqlite3`, one `BEGIN IMMEDIATE` transaction per request with
  a 15 s busy timeout, so a request and the app's own writes queue up instead of interleaving.

Check: `systemctl --user status musicdatabase-share`, `curl -I http://$(tailscale ip -4):8002/music.db`
(200), `/` and any other path (404), `curl -X POST -d '{"username":"x","password":"y"}' …/login` (401), `curl …/users/<name>/playlists` (401 without a token), the same for `http://192.0.2.13:8002/`, and
`ss -ltnp 'sport = :8002'`, which should list exactly two addresses: Tailscale and `192.0.2.13`.
Disable: `systemctl --user disable --now musicdatabase-share.timer musicdatabase-share.service`,
then delete the two unit files and run `systemctl --user daemon-reload`.

## Users' databases

Users are managed from the main menu → Users (create, log in, list, rename, change password,
delete). Each user has `Settings.users_dir/<username>/` = `/media/owner/T7/Shared/Music/Database/Users/<username>/`
with `user.db` (their playlists and private tags) and `account.json` (password hash). The logic is in `utils/database/users.py`, and why it works
this way is in [ADR-0004](../decisions/0004-per-user-data.md). Notes:

- **These are the only copies.** Backups (daily, manual and pre-migration) archive each one as
  `archive/user-<name>.<YYMMDD-HHMM>.db` next to `music.*.db`. Restore it like the others: copy
  it back to the user's folder while the app is closed.
- **Migrations:** `migrations/user/NNNN_*.sql`, applied to every user's `user.db` at startup
  (`run_pending_user_migrations()`, one backup first). A new user gets every file at creation
  (`initialize_user_db()`), with no bootstrap step as in `music.db`'s adoption below.
- **T7 not mounted:** users are skipped by backups and migrations (the next startup with T7
  mounted catches up on migrations), and "Create a new user" refuses. Nothing is ever created
  under an empty mount point. The daily backup still counts as done for that day, so a day
  with T7 unplugged at startup has no user backup.
- **Deleting a user** moves their folder to `src/database/archive/deleted-users/<name>.<YYMMDD-HHMM>/`
  (on the system disk). To undo, move it back into `Users/` under its name.
- **Not served as files.** The sharing server's whitelist is `music.db` / `tag.db` / `files.db`, so
  `Users/` and everything in it is a 404. Playlists reach the logged-in user's phones through the
  playlist endpoints above; private tags don't go anywhere yet.
- **Deleted playlists** are copied to `Users/<name>/archive/playlists/<uid>.<YYMMDD-HHMM>.json`
  first (see "Playlists on the phone" above). To recover one, read its tracks from that file.

## Music library index (`files.db`)

The mp3 collection is at `Settings.local_library_dir` (`/media/owner/T7/Shared/Music/Library/`
since 2026-10-12). `files.db` (beside `music.db`, schema in [data-model.md](../data-model.md))
maps each file in it to its song:

- **Scanning:** Settings → Server → "Index music library", and once a day at startup after the
  backup. Only new or changed files (size/mtime) are read; an unchanged library takes ~3 s, the
  first scan of ~4300 files ~3 min. Files without a song are listed on request and in
  `debug.log` (`flog`).
- **Backed up and migrated** like tag.db (`backup.DB_FILES`, `migrations.DB_FILES`); everything
  but a future hand-set match can be rebuilt by deleting the file and scanning again.
- T7 not mounted → the library folder is missing → the scan is skipped
  (`LibraryUnavailableError`), the index is left as it was.
- Audio files themselves are **not served** yet; the phone only learns which songs have a file
  and its relative path. Serving them is a separate step that widens the server's contract.

## Album covers directory

`<database_dir>/covers/` (`albums.covers_dir()`) holds the album cover images, which never go
into `music.db`. The folder is beside the DB files and gitignored with them, and
`album_covers.path` is relative to it. It's written by Fetch database data → "Import covers from
mp3 tags" (`utils/discoveries/mp3_covers.py`). That defaults to
`Settings.local_library_dir_str` and re-encodes to ≤600 px JPEG (~60–80 MB for the whole
library rather than ~300 MB of originals). Notes:

- **Backups don't include it.** `backup_databases()` copies only the DB files. Every image
  can be regenerated by re-running the import.
- **Sharing doesn't include it yet.** The sharing copy and server only handle `music.db` /
  `tag.db`. Serving covers to the phone (e.g. `GET /covers/<album_id>.jpg`) is a separate,
  not-yet-agreed step, because it widens the server's contract (above).
- If you restore an old `music.db` whose album ids differ, the images no longer match their rows.
  Re-run the cover import.

## Migrations

`migrations/<db_name>/NNNN_description.sql` — one numbered SQL file per schema change, `db_name`
is `music` or `tag`. Applied in filename order by `run_pending_migrations(db_name)`
(`run_all_pending_migrations()` runs both), tracked in a `schema_version` table inside that same
database file (`filename`, `applied_at`). Called once at every `main.py` startup — cheap to check
(one query + a directory listing) when there's nothing pending, which is the common case.

### Adopting this on an existing installation

The first time the runner sees a DB file with no `schema_version` table yet, it records every
`.sql` file **currently on disk** as already applied, without executing any of them — the
assumption is that an existing installation's tables already match the current model (that's what
`0001_baseline.sql` in each track documents: a snapshot of the schema at the moment migrations
were adopted, for reference, not something that runs for real). Only files added *after* that
bootstrap moment actually execute.

### Adding a migration

1. Edit the model in `datatables.py` (music) or `create_tag_db.py` (tag) — this file is still the
   schema's source of truth for the *current* shape.
2. Add `migrations/<db_name>/NNNN_description.sql` with the matching DDL, e.g.
   `ALTER TABLE songs ADD COLUMN rating INTEGER;`. Use the next number after whatever's already in
   that folder.
3. **One logical change per file.** The runner applies a file with `executescript()`, which can
   partially execute a multi-statement script before failing — there's no automatic rollback of a
   half-applied file. The real safety net is the mandatory backup taken right before the file
   runs, not transactional rollback: keep files small enough that "partially applied" isn't a
   state that can happen.
4. Never edit a migration file after it might have been applied anywhere (your own machine counts
   — once you've run it once, it's shipped). Add a new file for any further change, even a typo
   fix in a comment inside an old one that's already been applied.
5. Run `python main.py` once — the pending migration is picked up, backed up for, and applied
   automatically. No separate command to remember.

A migration that needs data work SQL can't do (anything using `normalize()`) gets a Python step
that `main.py` runs right after `run_all_pending_migrations()` reports that file as applied. So
far there is one: `0006_song_album_id.sql` → `albums.run_album_backfill()`, which links every
existing song to its album row. On the 2026-10-11 data (5480 songs with album text) it made 4131
albums (405 of them singles) and left 372 songs on 127 titles shared by several artists for the
resolver. It took a few seconds.

## Test database

There isn't one. The test suite (`tests/`) mocks the DB layer rather than exercising a real SQLite
file — see `AGENTS.md` → Testing & verification. If a test ever needs a real database, point
`create_music_db()` / `create_tag_db()` at a temporary path rather than reusing the dev DB.

## Known gap

`create_music_db()` / `create_tag_db()` (the `Base.metadata.create_all()` wrappers that would
create a brand-new, empty database) are currently **not called anywhere** in the running app — a
fresh clone with no existing `src/database/*.db` files has nothing to open a session against. This
predates the backup/migration work on this page and wasn't in scope to fix here; flagging it as a
known gap rather than leaving it silently undiscovered.
