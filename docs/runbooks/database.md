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
  no-store` is set. The other files it serves are the library's audio files, below.
- **Library files (2026-10-14):** `GET`/`HEAD /library/<files.id>` serves one file of the music
  library, so Leina Radio can play a song from the server (its source "Serwer"). The service
  gets `--library` = `Settings.local_library_dir` (`files_index.local_library_path()`; left out
  for an `smb://` library, and then every `/library/...` is a 404). The id is looked up in the
  **shared** `files.db` copy, the one the phone downloaded, opened read-only and `immutable` per
  request. No token, the same reach as the databases (the owner's choice, 2026-10-14).
  - Safety: the path never comes from the request. A row's path is resolved with `realpath`
    and must stay under the library's real path, so a `..`, an absolute path or a symlink out
    of the library is a 404 and is never opened. Only `LIBRARY_AUDIO_EXTENSIONS` (the server's own
    copy, kept equal by a test) and only regular files. No directory listing.
  - One byte range per request (`bytes=a-b`, `a-`, `-n`) → 206 + `Content-Range`. Several
    ranges, or a malformed header, → 200 with the whole file. A range starting past the end →
    416 with `Content-Range: bytes */<size>`. Copied in 64 KiB chunks.
  - 404 unknown id, not an id, file gone / not audio / outside the library, no shared `files.db`;
    503 T7 not mounted or the library folder missing. `Cache-Control: no-cache`, since a row keeps
    its id when the file's contents change.
  - The player drops the connection on every seek or skip; a hang-up mid-body ends that request
    quietly (nothing in the journal) and a stalled reader is cut off after 60 s.
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
- **Inbox limit:** while the files in `new/` and `review/` total `PHONE_INBOX_LIMIT_BYTES` (100 MiB,
  the owner's choice) nothing more is taken; the phone keeps the request and retries later. Settling
  a file moves it to `processed/`, which frees the room; `processed/` doesn't count. A resend of a
  file in any of the three folders is a 200.
- Anyone on the home network can send a request, the same trust as downloading. That's why the
  name, size and header checks are there, and why a request only ever reaches the database through
  the review below, or through the automatic pass in the narrow cases it may settle.
- **Automatic pass (2026-10-14)** — `utils/database/inbox_auto.py`, run every `INBOX_AUTO_INTERVAL`
  (15 min) by the `musicdatabase-inbox` systemd user timer (`auto_resolve_requests.py` at the repo
  root, the venv's python with `venv/bin` on PATH for yt-dlp/deno; installed with the sharing
  service by `install_sharing_service()`), and by "Resolve new requests" itself before asking.
  It takes the files in `new/`: settles what needs no answer, replaces a dead link only when the
  phone's suggestion and our own search found the same video and it plays here, and saves a field
  correction only when the newest report came from a logged-in user with the `apply_corrections`
  permission (Users → Permissions; the file name's user part, which the server verified) and
  nothing needs a judgement (`field_correction_verdict()`: storable, unchanged since the phone's
  copy, no other value asked for, the song still has that title / artist / album, no artist or
  album twin, no "rename the whole album" alternative). Everything else gets a note (why) in
  `Inbox/auto_checks.json`, together with its YouTube check, and its file goes to `Inbox/review/`;
  the review shows the note and reuses the check for `INBOX_CHECK_MAX_AGE_HOURS` (24 h). What it
  changed is in `Inbox/auto_report.json`; `main.py` prints the entries not shown yet at startup, and
  the review screen lists them all. `decisions.json` entries it wrote carry `"by": "auto"`.
  The `apply_corrections` permission is a user's (account.json), not a device's. Leina Radio
  shows nothing for it. It stores permission names it doesn't know as text and checks only the
  ones it knows (its tests cover `apply_corrections`). A new permission therefore needs no change
  in the app unless the app should act on it (confirmed 2026-10-14).
- **The pass never runs while the app is open:** both write music.db, and the open app's ORM
  objects would go stale (`utils/database/app_lock.py`: `main.py` holds an `flock` on
  `<database_dir>/.musicdatabase.lock` shared for its whole run, the pass needs it exclusive and
  skips the run otherwise; an app started during a run waits for it). It also skips while T7 isn't
  mounted or music.db has a pending migration (`migrations.has_pending_migrations()`), and copies
  the databases for the phones after any change. Log: `journalctl --user -u musicdatabase-inbox`.
- Manage database → "Resolve new requests (N)" (`utils/ui/inbox_request_review.py`, logic in
  `utils/database/inbox_requests.py`; `main.py` prints a line at startup when some are waiting; N
  counts `new/` + `review/`). It shows how many files are new (unchecked) and how many wait for
  review, then runs the automatic pass over `new/` and asks about everything in `review/`.
  For `dead_youtube_links`: follows `song_id` through `song_id_changes`, settles without asking a
  report whose song is gone or whose link already changed (another id or "N/A"), groups repeats of
  one (song, dead video), checks with yt-dlp whether the dead link and the phone's video play and
  runs our own `search_video_ytdlp()` (`check_link_cases()`: one batched Data API call for
  every id, yt-dlp only for candidates the API didn't rule out, the dead link not replayed), then
  asks per song: keep the suggested link / keep the
  newfound link (ours) / paste a link / "N/A" / keep the current link / skip. Both are offered even
  when the check says they don't play. The check is `video_playability()`: True / False / None, where
  None means YouTube blocked it (HTTP 429, "confirm you're not a bot") and is shown as "couldn't
  check", never as dead — on 2026-10-13 a video that played fine on the phone looked dead this way. Answers go to `Inbox/decisions.json`; only "kept" is read
  back (it changes nothing in the database, so a pending or repeated report would otherwise be asked
  again, unless it was detected after the answer).
- For `field_corrections` (`utils/database/inbox_field_corrections.py`, app handoff
  `musicdatabase-field-corrections.md`, answer [`docs/handoffs/field-corrections-for-musicdatabaseapp.md`](../handoffs/field-corrections-for-musicdatabaseapp.md)):
  rows are grouped by (table, current row, column), the newest `corrected_at` offered. Settled
  without asking: the row is gone (a song through `song_id_changes`; an artist or album has no
  such log, so a merged one just counts as gone), the database already has the value, or the
  owner answered that field after the newest report (any answer is in `decisions.json` under
  `field:<table>:<id>:<column>`, since a rejection changes nothing). Shown per field: phone's old
  value, current value (flagged when it changed since), correction; then save / edit / keep the
  current value / skip. Extras: a `songs.album` correction can rename the song's whole album
  instead of moving just that song (`albums.rename_album()`); an `albums.title` that equals
  another album of the artist merges the two (`merge_albums()`, the album with a cover, else the
  older id, kept) and always rewrites the songs' `album` text; an `artists.name` that is another
  artist's name offers merging into it (`merge_artists_in_db()`). A value that can't be stored
  (not a number, an unknown album type, an empty title) can only be edited. Applied through the
  ORM, so the album listener does what it does after any edit. Latin forms (`songs.title_latin`,
  `artists.name_latin`, `albums.title_latin`, 2026-10-14, app handoff
  `musicdatabase-latin-names.md`, answer [`docs/handoffs/latin-names-for-musicdatabaseapp.md`](../handoffs/latin-names-for-musicdatabaseapp.md))
  are plain text fields; the review shows the name and a suggestion next to them. A title / name
  correction on a row that has a Latin form is flagged (the form stays), and the automatic pass
  leaves it for review.
  A song's genre (`songs.genre`, 2026-10-16, app handoff `musicdatabase-genres.md`, answer
  [`docs/handoffs/genres-for-musicdatabaseapp.md`](../handoffs/genres-for-musicdatabaseapp.md)) is
  a text field stored in the database's spelling (`genres.clean_genres()`: "Synthpop , Indie Pop"
  is offered and saved as "synth-pop, indie pop"); empty clears it.
  A song's kind (`songs.kind`, migration 0014, 2026-10-16, handoff
  [`docs/handoffs/song-kind-for-musicdatabaseapp.md`](../handoffs/song-kind-for-musicdatabaseapp.md)):
  `full_album` or empty (a song); applied through `song_kinds.set_song_kind()`, which files a full
  album with no album under its own title.
  YouTube links set by hand (2026-10-15, app handoff `musicdatabase-youtube-links-by-hand.md`,
  answer [`docs/handoffs/youtube-links-by-hand-for-musicdatabaseapp.md`](../handoffs/youtube-links-by-hand-for-musicdatabaseapp.md)):
  `songs.youtube_video_id` (a video id, `"N/A"` or NULL = search again) and
  `songs.music_video_youtube_id` (a video id or NULL, migration 0012). Leina Radio plays / opens
  its user's link at once and keeps it while music.db still has the `old_value`, so a rejected one
  leaves that copy on the user's link (the review says so). Accepting a `youtube_video_id` is a
  manual pick: the video comes off `bad_youtube_videos`, and the review asks (default no) whether
  the replaced video goes on it as `wrong_for_song`. `check_correction_videos()` asks YouTube
  whether the new video plays (Data API, then yt-dlp); the automatic pass saves a link from a user
  with `apply_corrections` only when it plays and isn't in `bad_youtube_videos` for the song
  ("N/A" / NULL need no check), and the review reuses that check. A newer `youtube_video_id`
  correction of a song settles older dead link / found link reports of it in the same batch
  (`inbox_requests.links_set_by_hand()`). Taking a correction back (any column: a newer report
  with `new_value` = `old_value`) is "already set"; that's recorded in `decisions.json` as of the
  newest report, so an older report of the field waiting in another file is answered too, never
  offered on its own.
- For `found_youtube_links` (2026-10-14, `utils/database/inbox_found_links.py`, app handoff
  `musicdatabase-found-youtube-links.md`, answer [`docs/handoffs/found-links-for-musicdatabaseapp.md`](../handoffs/found-links-for-musicdatabaseapp.md)):
  a video Leina Radio found and plays for a song music.db has no link for (NULL or `""`, not "N/A").
  One case per song (through `song_id_changes`), every video offered, the newest `found_at` first.
  Settled without asking: the song is gone, it has a link or "N/A" now, or every video named is in
  `bad_youtube_videos` (below). The automatic pass saves a link by the dead-link rule (our search found
  the same video and it plays here; `link_verdict()`), settles the case when none of the videos plays
  here (Leina Radio then finds another and reports it), and leaves the rest for review: keep Leina
  Radio's video / our newfound one / paste / "N/A" / "Wrong video" (the song stays without a link,
  the videos are remembered as wrong for it) / skip.
- **Videos found bad** (music.db's `bad_youtube_videos`, migration 0010, owner's request
  2026-10-14; `load_bad_videos()` / `mark_bad_videos()` / `unmark_bad_video()` in
  `inbox_requests.py`). Leina Radio reads the table too (app handoff `musicdatabase-bad-videos.md`,
  answer [`docs/handoffs/bad-videos-for-musicdatabaseapp.md`](../handoffs/bad-videos-for-musicdatabaseapp.md))
  and stops using those videos. `song_id` NULL = bad for every song: `removed` per the Data API,
  `unplayable` per yt-dlp (only a `False`, never a blocked `None`), `replaced_dead_link` (a dead link
  replaced or set to "N/A"). Otherwise wrong for that song: `wrong_for_song` (the owner chose "Wrong
  video") or `no_video_for_song` ("N/A" while Leina Radio had videos). The codes are
  `constants.BAD_VIDEO_REASONS`. A trigger on `song_id_changes` moves per-song rows to the kept song
  of a merge and drops them with a deleted song, so `song_id` is always a current id. The check
  commits what it finds, and the automatic pass copies the databases for sharing when it found any. A found link naming one is turned
  down without asking YouTube; a dead link's suggestion naming one is left out (shown dimmed in the
  review, still choosable) and our own search never offers one. Choosing a video, or keeping a link
  reported dead, takes it off the list. Delete a row (app closed) to give a video another chance.
- A file moves to `Inbox/processed/` once every row is settled. It stays in `review/` while a row is
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
- **Tracks removed from an imported playlist (2026-10-15):** `excluded_source_tracks`, a JSON list of
  the ids in the playlist's source (YouTube video ids, Spotify track ids) of tracks the user removed,
  so importing the playlist again on any phone leaves them out. It is part of the playlist, like the
  cover: user migration 0007 (`playlists.excluded_source_tracks`, NULL = `[]`), always in the answers,
  a PUT without the key keeps the stored list, anything but a list of non-empty strings is 400. The
  whole list goes with the winning side (also in the same-import merge), never merged. A deleted
  playlist drops it; the archive JSON keeps it. The app's request:
  `docs/handoffs/musicdatabase-excluded-tracks.md`; the answer:
  [`docs/handoffs/excluded-tracks-for-musicdatabaseapp.md`](../handoffs/excluded-tracks-for-musicdatabaseapp.md).
  The menu only makes folder playlists, so it never writes the list; a future menu re-import of a
  YouTube/Spotify playlist into an existing one must leave those ids out too.
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

**Moods on the phone (2026-10-13).** Moods ("Nastroje" in the app) are sets of songs with the same
feel, without order; the phone plays one shuffled. The owner's decisions are in the app's
`docs/handoffs/musicdatabase-moods.md`; the answer, with the final contract, is
[`docs/handoffs/moods-for-musicdatabaseapp.md`](../handoffs/moods-for-musicdatabaseapp.md). Only songs in
`music.db` can be in a mood (song ids, no title fallback).

- **Kinds of tags.** `tags.kind` (tag migration 0003, user migration 0005) is `'mood'` or
  `'marker'`. Every older tag, including `spellchecked` / `album_checked`, is a marker and is never
  listed or sent as a mood. `tags.name` is unique per file, so a global mood can't take a marker's
  name; creating, renaming or promoting onto one is refused (`utils/database/moods.py`).
- **Global moods** are `tag.db` tags with kind `'mood'`; every user sees them in the downloaded
  `tag.db`. Only the owner creates, renames and deletes them: main menu → Users → Moods → "Global
  moods", which also adds and removes songs.
- **A user's own moods** are private tags in their `user.db`, synced exactly like playlists
  (`utils/database/mood_sync.py`, built on `playlist_sync.py`): `GET /users/<name>/moods?since=`,
  `PUT /users/<name>/moods/<uid>`, `DELETE …?base_revision=&deleted_at=`, same codes, own
  `mood_revision` counter, song ids resolved against the shared `music.db` (a deleted or unknown
  id is dropped, not kept as NULL), `song_ids_checked_to` caught up by `apply_song_id_changes()`.
  Names are unique among a user's live moods: a new uid with a live mood's name is merged into it
  (union of songs, the answer carries the existing uid), and a rename onto another mood's name
  merges into that one and leaves a marker with `merged_into`. Deleted moods are archived to
  `Users/<name>/archive/moods/<uid>.<YYMMDD-HHMM>.json`. The table was rebuilt by migration 0005
  so a deleted mood's name is free again (unique index over live rows only).
- **The owner sees every user's moods** without their password: Users → Moods → "Users' moods".
  "Promote to a global mood" moves one into `tag.db`: a new global mood of that name, or merged
  (union) into an existing one; refused if a marker has the name. The user's mood then becomes a
  marker with `promoted_to` / `promoted_to_name`, so their phones switch to the global one. A
  promoted or merged mood is never revived by a later PUT (409 with the marker).
- **Permissions** live in `account.json` (`"permissions": [...]`, `account_auth.PERMISSIONS`),
  not in `user.db`: they're what the owner trusts a user with. Users → Permissions ticks them per
  user (`users.set_permissions()`); the first is `edit_global_moods`, more go into
  `PERMISSIONS` + `PERMISSION_LABELS`. `POST /login` answers them, and `GET /users/<name>` (that
  user's token) returns `{"username", "permissions"}`. The server reads the file on every request,
  so a revoked permission works at once.

**Global moods from the phone.** A user with `edit_global_moods` adds or removes songs with
`PUT` / `DELETE /global-moods/<mood id>/songs/<song id>` (no body). Nothing is reviewed — the
permission is the trust — but everything is recorded (`utils/database/global_mood_log.py`):

- The server checks the token (401), the permission (403 with JSON
  `{"error": "permission", "permission": "edit_global_moods"}`, which the phone tells apart from
  the plain-text "another user" 403), that the mood is a mood in the *shared* `tag.db` (404
  `{"error": "mood"}`) and resolves the song against the shared `music.db` (merged → kept song;
  deleted or unknown → 404 `{"error": "song"}`). It then writes one JSON file into
  `Settings.global_mood_changes_dir/pending/` (`--mood-changes`, `.../Database/MoodChanges`) and
  answers 202 `{"mood_id", "song_id", "action"}`. **It never writes `tag.db`**: the original is on
  the system disk and `main.py` may hold it open.
- MusicDatabase applies the pending files in arrival order (`moods.apply_global_mood_changes()`):
  at startup (after `apply_song_id_changes()`), before every copy for the phones (startup and
  "Submit database for sharing"), and when the Moods menu shows global moods or the log. Song ids
  are followed through `song_id_changes` past what the server checked. Each change is appended to
  `MoodChanges/history.jsonl` with `applied_at` and a result (`added`, `removed`, `unchanged`,
  `mood_gone`, `song_gone`), then its file is deleted. A crash in between re-applies the same
  changes next time, which ends in the same state.
- **Delay:** other phones get the change with the next copy of `tag.db` — the daily one at
  MusicDatabase's startup, or Settings → Server → "Submit database for sharing". The phone that made
  it keeps it locally until its downloaded `tag.db` has it.
- **Seeing and undoing:** Users → Moods → "Changes from Leina Radio" lists who added or removed which
  song in which mood, newest first (100 shown; the whole log is `history.jsonl`). Undo by hand in
  "Global moods" → the mood → "Add a song" / "Remove songs". `MoodChanges/` isn't backed up; tag.db is.

**Covers on the phone (2026-10-13).** A playlist or mood can have a cover the user picked from their
photos on the phone. It syncs with the user's account like the rest of the playlist/mood. The owner's
decisions are in the app's `docs/handoffs/musicdatabase-covers.md`; the answer, with the final
contract, is [`docs/handoffs/covers-for-musicdatabaseapp.md`](../handoffs/covers-for-musicdatabaseapp.md).
MusicDatabase only stores covers; the menu can't set them, it just adds ", cover" to the playlist
and mood lists.

- **Pictures are files, not rows** (`utils/database/covers.py`): `Users/<name>/covers/<sha256>.png|.jpg`,
  named by the SHA-256 of the exact bytes. `PUT /users/<name>/covers/<sha256>` (body = the image,
  `image/png` or `image/jpeg`, at most 4 MiB, signature checked, never decoded) answers 201 new /
  200 already stored, 400 hash mismatch, 413, 415; `GET` serves it with `Cache-Control: immutable`.
  Same token rules as playlists.
- **References** are `cover_sha256` on `playlists`, on mood `tags` rows and in
  `global_mood_covers` (user migration 0006; until a user.db has it - now 0007, see "Playlists on
  the phone" - every playlist / mood / cover call for that user is 503). Playlist and mood JSON carry `"cover"`: a PUT without the key
  keeps the stored one (older app versions), `null` removes it, a hash with no blob is 400
  `{"error": "cover_missing", "cover": …}`. Merges: the side with the newer `updated_at` keeps its
  cover, or the other side's when it has none (same import on two phones, same mood name).
- **Private covers of global moods** (`mood_sync.py`): `GET /users/<name>/global-mood-covers?since=`
  and `PUT …/global-mood-covers/<tag.db mood id>` with `base_revision`, `updated_at`, `cover`.
  Own counter (`global_mood_cover_revision`), the playlist conflict rule, removal = `cover: null`
  (the row stays). Promoting a user's mood moves its cover here.
- **Deleting** a playlist or mood copies its blob to `Users/<name>/archive/covers/` next to the
  archived JSON and clears the reference. **Clean-up** (`covers.clean_up()`) runs on the server
  after every applied write of that user and removes blobs nothing refers to once their file is
  24 hours old (mtime; a re-upload touches it). The grace period is there because the phone uploads
  the blob before the PUT that names it. Blob writes, the "blob exists" check plus its write, and
  the clean-up share one lock in the server.
- `covers/` lives in the user's folder on T7, which the backups don't copy (they copy `user.db`).
  A restored user.db may name blobs the folder no longer has; the phone then re-uploads on
  `cover_missing`, and a GET of a missing blob is 404.

**Folders on the phone (2026-10-16).** Leina Radio's Biblioteka has folders holding playlists and
moods; they sync between the user's phones like playlists. Only the folders: the sort order and
"recently opened" stay on each phone. The owner's decisions are in the app's
`docs/handoffs/musicdatabase-folders.md` (app ADR 0011); the answer, with the final contract, is
[`docs/handoffs/folders-for-musicdatabaseapp.md`](../handoffs/folders-for-musicdatabaseapp.md).

- `GET /users/<name>/folders?since=`, `PUT /users/<name>/folders/<uid>` (`name`, `items`,
  `created_at`, `updated_at`, `base_revision`), `DELETE …?base_revision=&deleted_at=`. The same
  token rules, answers and conflict rule as playlists, own `folder_revision` counter, bodies up to
  1 MiB. A uid is 1–64 characters `[A-Za-z0-9-]` (folders made before syncing keep a short base-36
  id). 503 until that `user.db` has user migration 0008 (`folders`); playlists and moods don't need it.
- **Items are stored as sent** (`folders.items`, JSON, in the user's order), never checked: a
  folder may name a playlist only one phone has, and unknown item types are kept for later app
  versions. Each item only has to be an object with a string `type`. Private, no review, no
  permission; folders don't nest and the phone keeps an item in one folder at most.
- **Deleting** keeps the row as a marker without name or items; nothing is archived (a folder
  holds only references). A later PUT whose `updated_at` is newer brings it back.
- **Merges follow:** when the server merges a playlist (same import on two phones) or a mood (same
  name, or a rename onto another's name) into another, `folder_sync.replace_refs()` points the
  user's live folders at the kept uid in the same transaction, each changed folder under a new
  revision, dropping a ref that would then appear twice. A promoted mood's refs are left alone
  (the phone switches via `promoted_to`); a playlist deleted from the menu leaves its ref too.
- The rules live in `utils/database/folder_sync.py` (standard-library, built on `playlist_sync.py`).
  MusicDatabase's menu doesn't show or edit folders.

**Listening stats on the phone (2026-10-16).** Leina Radio records each song it plays with how long
it was really heard ("a play") and computes Spotify-like stats from them; MusicDatabase only stores
and hands back the plays, so both of the owner's phones count together. The owner's decisions are in
the app's `docs/handoffs/musicdatabase-listening-stats.md` (app ADR 0012); the answer, with the
final contract, is
[`docs/handoffs/listening-stats-for-musicdatabaseapp.md`](../handoffs/listening-stats-for-musicdatabaseapp.md).

- `GET /users/<name>/listens?since=&limit=` (limit default and cap 1000) →
  `{"revision", "listens", "more"}`; with `more` the revision is the last listed play's.
  `POST /users/<name>/listens` with `{"listens": [...]}` → `{"revision"}`. Same token rules and
  errors as folders, bodies up to 1 MiB, own `listen_revision` counter. 503 until that `user.db` has
  user migration 0009 (`listens`).
- **Only added or lengthened:** a known `uid` is updated (new revision) only when its `listened_ms`
  is larger, `duration_ms` then filled in if it was NULL; nothing else changes, no deletions. A batch
  with one bad play is 400 and nothing is stored.
- **Stored as sent:** `song_id`, `global_mood_id`, playlist/mood uids are never checked, and
  `song_references.apply_song_id_changes()` deliberately doesn't remap `listens.song_id` (that
  would change plays behind the phones' backs); a reader resolves old ids through music.db's
  `song_id_changes`. Keys the server doesn't know are kept in `listens.extra` and served back.
- The rules live in `utils/database/listen_sync.py` (standard-library, built on `playlist_sync.py`).
  Private, no review, no permission. MusicDatabase's menu doesn't show plays yet.

Check: `systemctl --user status musicdatabase-share`, `curl -I http://$(tailscale ip -4):8002/music.db`
(200), `/` and any other path (404), `curl -X POST -d '{"username":"x","password":"y"}' …/login` (401), `curl …/users/<name>/playlists`, `…/moods`, `…/folders` and `…/listens` (401 without a token), the same for `http://192.0.2.13:8002/`, and
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
- **Not served as files.** The sharing server serves only `music.db` / `tag.db` / `files.db` and
  the library's audio files by id, so `Users/` and everything in it is a 404. Playlists, moods and folders reach the logged-in user's phones
  through their endpoints above; other private tags (kind `'marker'`) don't go anywhere.
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
- The sharing server serves the files themselves at `/library/<files.id>` (see "Sharing with the
  mobile app"), using the ids in the shared copy. Ids are never reused, so a stale copy on the
  phone at worst gets a 404 for a file that moved, never another song's file.

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
   that folder. A **trigger** (or anything else a model can't declare) must also go into the model
   as a `DDL` on an `after_create` event. `create_all()` builds the test databases and a fresh
   install, and the bootstrap marks every migration applied there without running it, so a
   trigger only in the `.sql` file would silently never exist there. Hang it on the table it fires
   *on*: `bad_youtube_videos_follow_songs` hangs on `song_id_changes` in `datatables.py`, since
   creating a trigger on a table that doesn't exist yet fails.
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
