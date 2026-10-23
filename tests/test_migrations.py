"""The schema migration runner itself (utils/database/migrations.py): adopting
a database that predates it, applying a new file after a backup, stopping at
a failing file, and a file plus its schema_version row being all or nothing."""

import sqlite3

import pytest

from utils.database import migrations


@pytest.fixture
def track(tmp_path, monkeypatch):
    """A "music" track in a temp folder with its own database; the backups
    taken before a migration are recorded instead of made."""
    root = tmp_path / "migrations"
    (root / "music").mkdir(parents=True)
    db = tmp_path / "music.db"
    sqlite3.connect(str(db)).close()
    backups = []
    monkeypatch.setattr(migrations, "MIGRATIONS_ROOT", root)
    monkeypatch.setattr(migrations, "DB_FILES", {"music": db})
    monkeypatch.setattr(migrations, "backup_databases", lambda reason: backups.append(reason))
    return root / "music", db, backups


def _write(folder, name, sql):
    (folder / name).write_text(sql)


def _applied(db):
    conn = sqlite3.connect(str(db))
    try:
        return [r[0] for r in conn.execute("SELECT filename FROM schema_version ORDER BY id")]
    finally:
        conn.close()


def _columns(db, table):
    conn = sqlite3.connect(str(db))
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_an_old_database_adopts_the_files_without_running_them(track):
    folder, db, backups = track
    _write(folder, "0001_songs.sql", "CREATE TABLE songs (id INTEGER PRIMARY KEY);")

    assert migrations.run_pending_migrations("music") == []
    assert _applied(db) == ["0001_songs.sql"]
    assert _columns(db, "songs") == []  # recorded, not executed
    assert backups == []


def test_a_new_file_runs_after_a_backup(track):
    folder, db, backups = track
    _write(folder, "0001_songs.sql", "CREATE TABLE songs (id INTEGER PRIMARY KEY);")
    migrations.run_pending_migrations("music")
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    _write(folder, "0002_genre.sql", "-- a comment last, no newline\nALTER TABLE songs ADD COLUMN genre TEXT; -- end")

    assert migrations.run_pending_migrations("music") == ["0002_genre.sql"]
    assert backups == ["pre-migration (music.db)"]
    assert _columns(db, "songs") == ["id", "genre"]
    assert _applied(db) == ["0001_songs.sql", "0002_genre.sql"]
    assert migrations.run_pending_migrations("music") == []
    assert backups == ["pre-migration (music.db)"]


def test_a_failing_file_leaves_nothing_of_itself_and_stops(track):
    folder, db, _ = track
    _write(folder, "0001_songs.sql", "CREATE TABLE songs (id INTEGER PRIMARY KEY);")
    migrations.run_pending_migrations("music")
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    _write(folder, "0002_half.sql", "ALTER TABLE songs ADD COLUMN genre TEXT;\nALTER TABLE nowhere ADD COLUMN x TEXT;")
    _write(folder, "0003_after.sql", "ALTER TABLE songs ADD COLUMN kind TEXT;")

    with pytest.raises(sqlite3.OperationalError):
        migrations.run_pending_migrations("music")

    # The first statement of the failing file is rolled back with it, and
    # the file after it never ran
    assert _columns(db, "songs") == ["id"]
    assert _applied(db) == ["0001_songs.sql"]


def test_a_file_that_failed_runs_cleanly_once_fixed(track):
    """Before the fix: the ALTER stayed applied without its schema_version
    row, so the next start died on "duplicate column name"."""
    folder, db, _ = track
    _write(folder, "0001_songs.sql", "CREATE TABLE songs (id INTEGER PRIMARY KEY);")
    migrations.run_pending_migrations("music")
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    _write(folder, "0002_genre.sql", "ALTER TABLE songs ADD COLUMN genre TEXT;\nSELECT no_such_function();")
    with pytest.raises(sqlite3.OperationalError):
        migrations.run_pending_migrations("music")

    _write(folder, "0002_genre.sql", "ALTER TABLE songs ADD COLUMN genre TEXT;")
    assert migrations.run_pending_migrations("music") == ["0002_genre.sql"]
    assert _columns(db, "songs") == ["id", "genre"]


def test_the_runner_answers_whether_anything_is_pending(track):
    folder, db, _ = track
    _write(folder, "0001_songs.sql", "CREATE TABLE songs (id INTEGER PRIMARY KEY);")
    assert migrations.has_pending_migrations("music")  # not adopted yet
    migrations.run_pending_migrations("music")
    assert not migrations.has_pending_migrations("music")
    _write(folder, "0002_x.sql", "CREATE TABLE x (id INTEGER);")
    assert migrations.has_pending_migrations("music")


@pytest.mark.parametrize("track_name", ["music", "tag", "user", "files"])
def test_every_real_migration_runs_inside_a_transaction(tmp_path, monkeypatch, track_name):
    """Each real file, applied through _apply_one() on an empty database
    after the ones before it, must not end the transaction itself: that
    would split the file from its schema_version row again. music/tag's
    0001 baselines are adopted on real installs, but run fine too."""
    db = tmp_path / "x.db"
    conn = sqlite3.connect(str(db))
    try:
        migrations._ensure_schema_version_table(conn)
        for f in migrations._migration_files(track_name):
            migrations._apply_one(conn, f)
            assert not conn.in_transaction
        assert _applied(db) == [f.name for f in migrations._migration_files(track_name)]
    finally:
        conn.close()
