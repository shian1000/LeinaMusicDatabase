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
from the terminal UI: Settings → Database location → Change database folder (browse via
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

`backup_databases(reason)` copies every existing DB file into `src/database/archive/`, named
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

From the app: Settings → "Back up database now" (`backup_database_now()` in
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
`<url>/music.db` and `<url>/tag.db`. It rejects anything that doesn't start with the
`SQLite format 3` header (keeping its previous copy) and swaps the two files as a pair. That
contract (port `SHARING_HTTP_PORT` = 8002, both file names) is relied on by the app, so agree any
change with the app side first. Port 8001 is taken by the APK share.

**Copying** (`utils/database/sharing.py`) puts both files in `Settings.sharing_dir`
(`/media/owner/T7/Shared/Music/Database` by default):

- Automatically, right after a *successful* daily backup in `main.py` (`share_databases_quietly()`,
  which logs and swallows every failure). The pre-migration backup does not trigger it.
- On demand: Settings → "Submit database for sharing" (`share_databases()`), which prints where
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
`~/.config/systemd/user/`. Settings → "Setup this PC for database sharing"
(`install_sharing_service()`) writes them; re-running it overwrites the units and restarts the
server. Tests: `tests/test_database_sharing.py`.

- Only `GET`/`HEAD /music.db` and `/tag.db` are served. Everything else is a 404 with no
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

Check: `systemctl --user status musicdatabase-share`, `curl -I http://$(tailscale ip -4):8002/music.db`
(200), `/` and any other path (404), the same for `http://192.0.2.13:8002/`, and
`ss -ltnp 'sport = :8002'`, which should list exactly two addresses: Tailscale and `192.0.2.13`.
Disable: `systemctl --user disable --now musicdatabase-share.timer musicdatabase-share.service`,
then delete the two unit files and run `systemctl --user daemon-reload`.

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
