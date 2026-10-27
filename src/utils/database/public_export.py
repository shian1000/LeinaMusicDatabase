"""
The public copy of music.db and tag.db (published as a GitHub Release by
public_database.py, app ADR 0014): built in a temporary folder from the
current databases, never touching them, then checked before anything leaves
this computer.

Allow-list, not deny-list: a table is kept only when it's named in
MUSIC_SPEC / TAG_SPEC, and in a kept table only the listed columns keep their
values. Every other column stays in the schema (the phone reads the public
files and the sharing server's copies with the same code) but is emptied to
NULL; a new NOT NULL column nobody listed fails the export. So whatever a
later migration adds stays out until someone lists it here on purpose.

check_public_database() opens a built file again and fails it when a table
isn't allowed, a user's or the library index's table is there, a withheld
column holds a value, a kept table holds rows its filter drops, a value looks
like a local path, or SQLite's integrity check fails. publish only after it
comes back empty.
"""

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from settings import Settings

MIGRATIONS_ROOT = Path(__file__).resolve().parents[3] / "migrations"


@dataclass(frozen=True)
class TableSpec:
    columns: tuple[str, ...]  # the columns whose values are published
    keep_rows: str | None = None  # SQL condition; rows not matching it are deleted


@dataclass(frozen=True)
class DatabaseSpec:
    name: str  # the published file's name
    tables: dict  # table -> TableSpec, in the order the row filters run


MUSIC_SPEC = DatabaseSpec("music.db", {
    "songs": TableSpec(("id", "title", "title_latin", "album", "year", "language", "youtube_video_id",
                        "spotify_url", "music_video_youtube_id", "genre", "kind", "artist_id", "album_id",
                        "track_number", "disc_number")),
    "artists": TableSpec(("id", "name", "origin", "synonyms", "name_latin")),
    "additional_song_artists": TableSpec(("song_id", "artist_id", "role")),
    "albums": TableSpec(("id", "title", "title_latin", "artist_id", "year", "type",
                         "musicbrainz_release_group_id", "itunes_collection_id", "deezer_album_id",
                         "spotify_album_id")),
    # path is a file in <database_dir>/covers/ - withheld
    "album_covers": TableSpec(("album_id", "source", "url", "width", "height", "bytes", "status", "checked_at")),
    "song_id_changes": TableSpec(("id", "old_song_id", "new_song_id", "changed_at")),
    "bad_youtube_videos": TableSpec(("id", "video_id", "song_id", "reason", "marked_at")),
    "rejected_song_requests": TableSpec(("id", "artist", "title", "youtube_video_id", "spotify_track_id",
                                         "rejected_at")),
    "youtube_video_durations": TableSpec(("video_id", "duration_s", "fetched_at")),
    "schema_version": TableSpec(("id", "filename", "applied_at")),
    "sqlite_sequence": TableSpec(("name", "seq")),
})

# Global moods only: the markers (spellchecked, album_checked) are the owner's
# work state, not catalog data
TAG_SPEC = DatabaseSpec("tag.db", {
    "tags": TableSpec(("id", "name", "kind"), keep_rows="kind = 'mood'"),
    "song_tags": TableSpec(("id", "song_id", "tag_id"), keep_rows="tag_id IN (SELECT id FROM tags)"),
    "applied_song_changes": TableSpec(("id", "last_change_id")),
    "schema_version": TableSpec(("id", "filename", "applied_at")),
    "sqlite_sequence": TableSpec(("name", "seq")),
})

# Text that looks like a path on some computer. Song titles do have slashes
# ("AC/DC"), so only well-known roots, URI schemes and drive letters count.
_PATH_PATTERNS = [
    re.compile(r"(^|[^A-Za-z0-9])/(home|media|mnt|run/media|Users|root|tmp|var|srv|Volumes)/"),
    re.compile(r"\b(smb|file|nfs|sftp):/", re.IGNORECASE),
    re.compile(r"(^|[^A-Za-z])[A-Za-z]:[\\/]"),
    re.compile(r"^\\\\[^\\]+\\"),
    re.compile(r"(^|[\s\"'(])~/"),
]


class ExportError(Exception):
    """The public copy can't be built, or it failed the check - publish nothing."""


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(conn) -> list[str]:
    return [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]


def _columns(conn, table: str) -> list[tuple[str, bool]]:
    """(name, NOT NULL) of every column."""
    return [(row[1], bool(row[3]) or bool(row[5])) for row in conn.execute(f"PRAGMA table_info({_q(table)})")]


def _private_table_names() -> set[str]:
    """Tables of users' user.db and of files.db (from their migrations), so a
    new one is caught without being listed here."""
    names = set()
    for track in ("user", "files"):
        for path in sorted((MIGRATIONS_ROOT / track).glob("*.sql")):
            names.update(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+\"?(\w+)", path.read_text(), re.IGNORECASE))
    return names


def build_public_database(source: Path, dest: Path, spec: DatabaseSpec) -> None:
    """Copy `source` into `dest` (SQLite's backup API, safe while the app has
    it open) and cut it down to `spec`. `source` is only read."""
    dest.unlink(missing_ok=True)
    src_conn = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(conn)
        src_conn.close()
        conn.execute("PRAGMA journal_mode = DELETE")
        with conn:
            for name, in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
                conn.execute(f"DROP VIEW {_q(name)}")
            for table in _tables(conn):
                if table not in spec.tables:
                    conn.execute(f"DROP TABLE {_q(table)}")
            for table, table_spec in spec.tables.items():
                if table not in _tables(conn):
                    continue
                for column, not_null in _columns(conn, table):
                    if column in table_spec.columns:
                        continue
                    if not_null:
                        raise ExportError(f"{spec.name}: {table}.{column} is NOT NULL but not on the "
                                          "public allow-list - add it to public_export.py or leave it out")
                    conn.execute(f"UPDATE {_q(table)} SET {_q(column)} = NULL")
                if table_spec.keep_rows:
                    conn.execute(f"DELETE FROM {_q(table)} WHERE NOT ({table_spec.keep_rows})")
        conn.execute("VACUUM")
    except sqlite3.Error as exc:
        raise ExportError(f"{spec.name}: {exc}") from exc
    finally:
        conn.close()
        src_conn.close()


def _local_path_strings() -> list[str]:
    """This machine's own folders, as text a value mustn't contain."""
    values = [str(Path.home())]
    for value in (Settings.database_dir, Settings.local_library_dir, Settings.sharing_dir,
                  Settings.sharing_drive_mount):
        if value:
            values.append(str(value).removeprefix("file://"))
    return [v.rstrip("/") for v in values if len(v.rstrip("/")) > 1]


def looks_like_local_path(value: str, local_paths=()) -> bool:
    return any(p.search(value) for p in _PATH_PATTERNS) or any(p in value for p in local_paths)


def check_public_database(path: Path, spec: DatabaseSpec, local_paths=None) -> list[str]:
    """Why the built file mustn't be published; [] when it may be."""
    if local_paths is None:
        local_paths = _local_path_strings()
    problems = []
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return [f"{spec.name}: can't open: {exc}"]
    try:
        result = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if result != ["ok"]:
            problems.append(f"{spec.name}: integrity check failed: {'; '.join(result[:5])}")
        private = _private_table_names()
        for kind, name, table in conn.execute(
                "SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('table', 'view', 'trigger')"):
            if kind == "view":
                problems.append(f"{spec.name}: view {name} isn't allowed")
            elif kind == "trigger" and table not in spec.tables:
                problems.append(f"{spec.name}: trigger {name} on {table}, which isn't allowed")
            elif kind == "table" and name not in spec.tables:
                problems.append(f"{spec.name}: table {name} isn't on the allow-list"
                                + (" (a user's or the library index's table)" if name in private else ""))
        for table, table_spec in spec.tables.items():
            if table not in _tables(conn):
                continue
            columns = [c for c, _ in _columns(conn, table)]
            for column in columns:
                if column in table_spec.columns:
                    continue
                count = conn.execute(f"SELECT COUNT(*) FROM {_q(table)} WHERE {_q(column)} IS NOT NULL").fetchone()[0]
                if count:
                    problems.append(f"{spec.name}: {table}.{column} is withheld but has {count} value(s)")
            if table_spec.keep_rows:
                count = conn.execute(f"SELECT COUNT(*) FROM {_q(table)} WHERE NOT ({table_spec.keep_rows})").fetchone()[0]
                if count:
                    problems.append(f"{spec.name}: {table} has {count} row(s) that should have been left out")
            for column in columns:
                for value, in conn.execute(
                        f"SELECT DISTINCT {_q(column)} FROM {_q(table)} WHERE typeof({_q(column)}) = 'text'"):
                    if looks_like_local_path(value, local_paths):
                        # The value itself isn't logged - it may be the private path
                        problems.append(f"{spec.name}: {table}.{column} has a value that looks like a local path")
                        break
    except sqlite3.Error as exc:
        problems.append(f"{spec.name}: unreadable: {exc}")
    finally:
        conn.close()
    return problems


def content_digest(path: Path) -> str:
    """SHA-256 of the schema and every row, in a fixed order: the same for
    the same data however the file's pages are laid out, so "changed since
    the last publish" doesn't depend on SQLite writing identical bytes."""
    digest = hashlib.sha256()
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        for kind, name, sql in conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"):
            digest.update(repr((kind, name, sql)).encode())
        for table in _tables(conn):
            n = len(_columns(conn, table))
            order = ", ".join(str(i) for i in range(1, n + 1))
            digest.update(repr(("table", table)).encode())
            for row in conn.execute(f"SELECT * FROM {_q(table)} ORDER BY {order}"):
                digest.update(repr(row).encode())
    finally:
        conn.close()
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class PublicFile:
    name: str
    path: Path
    size: int
    sha256: str
    content_sha256: str


@dataclass
class PublicExport:
    files: list[PublicFile] = field(default_factory=list)


def export_public_databases(sources: dict, folder: Path) -> PublicExport:
    """Build and check the public music.db and tag.db in `folder`.
    `sources` maps "music" / "tag" to the original files. Raises
    ExportError (with every problem found) when anything fails."""
    export = PublicExport()
    problems = []
    for key, spec in (("music", MUSIC_SPEC), ("tag", TAG_SPEC)):
        source = Path(sources[key])
        if not source.exists():
            raise ExportError(f"{source.name} doesn't exist")
        dest = folder / spec.name
        build_public_database(source, dest, spec)
        found = check_public_database(dest, spec)
        if found:
            problems.extend(found)
            continue
        export.files.append(PublicFile(spec.name, dest, dest.stat().st_size, file_sha256(dest), content_digest(dest)))
    if problems:
        raise ExportError("; ".join(problems))
    return export
