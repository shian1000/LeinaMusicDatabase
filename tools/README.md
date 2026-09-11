# tools/

Reusable scripts for operations that were repeated by hand enough times to be worth documenting.
Not a place for one-off commands — see `AGENTS.md` → Tools before adding another one here.

## `db_inspect.py`

**What it does:** prints a read-only report of both SQLite databases — file path, size, row
counts for the key tables, which migrations (`schema_version`) have been applied, whether
`main.py` is currently running, and how many backups exist in `src/database/archive/`.

**When to use it:** before any direct, out-of-band read/write against `music.db` or `tag.db` —
exactly the moment `AGENTS.md` → Database safety says to confirm the exact DB state and check for
a live `main.py` process. Replaces retyping an ad-hoc `sqlite3 ... "SELECT COUNT(*) ..."` each
time.

**Invocation:**

```bash
python tools/db_inspect.py
```

**Files or data it may change:** none. Every database connection is opened read-only (SQLite's
`mode=ro` URI) — it cannot mutate either database even by accident.

**Dry-run / backup behavior:** not applicable; this tool never writes.

**Known limitations:** row/migration counts only — it doesn't run `PRAGMA integrity_check`,
validate foreign keys, or inspect what's actually inside `archive/` snapshots beyond counting
them. Detecting whether `main.py` is running relies on `ps`, so it's Linux/macOS-only as written.
