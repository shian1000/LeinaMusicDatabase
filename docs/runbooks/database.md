# Database: backup, restore, and migrations

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
