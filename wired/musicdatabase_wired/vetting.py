"""Checking a request file from a phone before MDHS ever sees it (ADR-0006).

The file comes from a stranger, and SQLite runs whatever its schema says. So it's opened
read-only with everything but reading denied and a time limit, only known tables and columns
pass, and every row goes through a Pydantic model. What's handed on is a new file built from
those rows: nothing else of the original survives. Whether a correction is right is the
owner's call in MDHS; this only says a request is well formed and safe.

A request kind this version doesn't know comes from a newer app, not an attacker: it's held
until MDW is updated, not refused, or the phone would never send it again.
"""

import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, ValidationError

from musicdatabase_core.request_format import CORRECTABLE_FIELDS, REQUEST_NAME_RE

REQUEST_FORMAT = 1
MAX_ROWS = 20_000
MAX_TEXT = 1000
MAX_VALUE_TEXT = 4000
READ_SECONDS = 5
SONG_REQUEST_SOURCES = {"youtube", "spotify", "phone_file"}
PLATFORM_TABLES = {"android_metadata"}
REFUSED_LOG = "refused.jsonl"
REFUSED_LOG_MAX_BYTES = 1024 * 1024

ACCEPTED, REFUSED, HELD, EMPTY = "accepted", "refused", "held", "empty"


def _no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL in text")
    return value


Text = Annotated[StrictStr, Field(max_length=MAX_TEXT), AfterValidator(_no_nul)]
Stamp = Annotated[StrictStr, Field(min_length=1, max_length=64), AfterValidator(_no_nul)]
VideoId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{11}$")]
SpotifyId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9]{22}$")]
RowId = Annotated[StrictInt, Field(ge=1)]
Value = Annotated[StrictStr, Field(max_length=MAX_VALUE_TEXT), AfterValidator(_no_nul)] | StrictInt | StrictFloat | None


class _Row(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class RequestRow(_Row):
    format: StrictInt
    created_at: Stamp
    user_name: Text | None = None
    app: Text


class DeadLink(_Row):
    song_id: RowId
    title: Text
    artist: Text
    dead_video_id: VideoId
    detected_at: Stamp
    replacement_video_id: VideoId | None = None
    replacement_title: Text | None = None
    replacement_channel: Text | None = None
    replacement_found_at: Stamp | None = None


class FieldCorrection(_Row):
    table_name: Text
    row_id: RowId
    column_name: Text
    old_value: Value = None
    new_value: Value = None
    song_id: RowId
    title: Text
    artist: Text
    corrected_at: Stamp


class FoundLink(_Row):
    song_id: RowId
    title: Text
    artist: Text
    video_id: VideoId
    video_title: Text | None = None
    video_channel: Text | None = None
    found_at: Stamp


class SongRequest(_Row):
    title: Text
    artist: Text | None = None
    album: Text | None = None
    year: Annotated[StrictInt, Field(ge=0, le=9999)] | None = None
    duration_s: Annotated[StrictInt, Field(ge=0)] | None = None
    youtube_video_id: VideoId | None = None
    spotify_track_id: SpotifyId | None = None
    source: Text
    requested_at: Stamp


# The app's schemas (outbox.dart): MDHS reads these tables by name
_REQUEST_SCHEMA = """CREATE TABLE request (format INTEGER NOT NULL, created_at TEXT NOT NULL, user_name TEXT,
    app TEXT NOT NULL)"""
REPORT_TABLES = {
    "dead_youtube_links": (DeadLink, """CREATE TABLE dead_youtube_links (song_id INTEGER NOT NULL, title TEXT NOT NULL,
        artist TEXT NOT NULL, dead_video_id TEXT NOT NULL, detected_at TEXT NOT NULL, replacement_video_id TEXT,
        replacement_title TEXT, replacement_channel TEXT, replacement_found_at TEXT,
        PRIMARY KEY (song_id, dead_video_id))"""),
    "field_corrections": (FieldCorrection, """CREATE TABLE field_corrections (table_name TEXT NOT NULL,
        row_id INTEGER NOT NULL, column_name TEXT NOT NULL, old_value, new_value, song_id INTEGER NOT NULL,
        title TEXT NOT NULL, artist TEXT NOT NULL, corrected_at TEXT NOT NULL,
        PRIMARY KEY (table_name, row_id, column_name))"""),
    "found_youtube_links": (FoundLink, """CREATE TABLE found_youtube_links (song_id INTEGER NOT NULL,
        title TEXT NOT NULL, artist TEXT NOT NULL, video_id TEXT NOT NULL, video_title TEXT, video_channel TEXT,
        found_at TEXT NOT NULL, PRIMARY KEY (song_id, video_id))"""),
    "song_requests": (SongRequest, """CREATE TABLE song_requests (title TEXT NOT NULL, artist TEXT, album TEXT,
        year INTEGER, duration_s INTEGER, youtube_video_id TEXT, spotify_track_id TEXT, source TEXT NOT NULL,
        requested_at TEXT NOT NULL)"""),
}
_ALLOWED_PRAGMAS = {"table_xinfo", "quick_check", "trusted_schema"}
_AUTOINDEX_RE = re.compile(r"^sqlite_autoindex_[A-Za-z_]+_[0-9]+$")


@dataclass(frozen=True)
class Vetted:
    state: str
    reason: str | None = None
    clean: bytes | None = None


class _Refuse(Exception):
    pass


class _Hold(Exception):
    pass


def _only_reading(action, first, second, database, trigger):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first in _ALLOWED_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _open_untrusted(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    deadline = time.monotonic() + READ_SECONDS
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024 + 1024)
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.set_authorizer(_only_reading)
    return conn


def _check_schema(conn: sqlite3.Connection) -> list[str]:
    tables = []
    for kind, name, sql in conn.execute("SELECT type, name, sql FROM sqlite_master"):
        if kind == "table":
            # Not CREATE VIRTUAL TABLE, and only names we'll read by name
            if not isinstance(sql, str) or not sql.lstrip().upper().startswith("CREATE TABLE"):
                raise _Refuse("a table that isn't plain")
            tables.append(name)
        elif kind == "index" and sql is None and _AUTOINDEX_RE.match(name):
            continue
        else:
            raise _Refuse(f"a {kind} in the file")
    return tables


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    columns = []
    for row in conn.execute(f'PRAGMA table_xinfo("{table}")'):
        if row[6] != 0:  # generated or hidden: computed by the file, not stored in it
            raise _Refuse(f"{table}: a computed column")
        columns.append(row[1])
    return columns


def _read_rows(conn: sqlite3.Connection, table: str, model: type[_Row], budget: list[int]) -> list[_Row]:
    known = list(model.model_fields)
    columns = _columns(conn, table)
    unknown = [column for column in columns if column not in known]
    if unknown:
        raise _Hold(f"{table}: a column this version doesn't know")
    selected = ", ".join(f'"{column}"' for column in columns)
    rows = []
    for values in conn.execute(f'SELECT {selected} FROM "{table}"'):
        budget[0] -= 1
        if budget[0] < 0:
            raise _Refuse(f"over {MAX_ROWS} rows")
        try:
            rows.append(model.model_validate(dict(zip(columns, values))))
        except ValidationError as exc:
            # Never the value itself: this goes to a log and to the phone
            first = exc.errors()[0]
            raise _Refuse(f"{table}.{'.'.join(map(str, first['loc']))}: {first['type']}") from None
    return rows


def _check_kinds(table: str, rows: list[_Row]) -> None:
    for row in rows:
        if table == "song_requests" and row.source not in SONG_REQUEST_SOURCES:
            raise _Hold("a song request source this version doesn't know")
        if table == "field_corrections" and (row.table_name, row.column_name) not in CORRECTABLE_FIELDS:
            raise _Hold("a corrected field this version doesn't know")


def _already_rejected(catalog_db: Path) -> tuple[set, set, set]:
    """What the owner turned down, from the catalog: the app asks for none of it again."""
    names, videos, tracks = set(), set(), set()
    try:
        conn = sqlite3.connect(f"{catalog_db.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return names, videos, tracks
    try:
        query = "SELECT artist, title, youtube_video_id, spotify_track_id FROM rejected_song_requests"
        for artist, title, video, track in conn.execute(query):
            if title:
                names.add(((artist or "").casefold(), title.casefold()))
            if video:
                videos.add(video)
            if track:
                tracks.add(track)
    except sqlite3.Error:
        pass  # a catalog from before that table
    finally:
        conn.close()
    return names, videos, tracks


def _not_rejected(rows: list[SongRequest], catalog_db: Path) -> list[SongRequest]:
    names, videos, tracks = _already_rejected(catalog_db)
    return [row for row in rows
            if ((row.artist or "").casefold(), row.title.casefold()) not in names
            and row.youtube_video_id not in videos and row.spotify_track_id not in tracks]


def _rewrite(path: Path, request: RequestRow, sender: str | None, reports: dict[str, list[_Row]]) -> bytes:
    """A new file from the checked rows alone, the same for the same rows: a phone
    sending a file again is then told it's already here."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(_REQUEST_SCHEMA)
        conn.execute("INSERT INTO request VALUES (?, ?, ?, ?)",
                     (request.format, request.created_at, sender, request.app))
        for table, rows in reports.items():
            model, schema = REPORT_TABLES[table]
            conn.execute(schema)
            columns = list(model.model_fields)
            marks = ", ".join("?" for _ in columns)
            conn.executemany(f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({marks})",
                             [tuple(getattr(row, column) for column in columns) for row in rows])
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def vet_request(data: bytes, sender: str | None, catalog_db: Path, work_dir: Path) -> Vetted:
    """sender: the account whose token sent it, written into the new file."""
    work_dir.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=".vet-", dir=work_dir))
    try:
        original = folder / "original.db"
        original.write_bytes(data)
        conn = None
        try:
            conn = _open_untrusted(original)
            if conn.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise _Refuse("a damaged file")
            tables = [table for table in _check_schema(conn) if table not in PLATFORM_TABLES]
            if "request" not in tables:
                raise _Refuse("no request table")
            budget = [MAX_ROWS]
            unknown = [table for table in tables if table != "request" and table not in REPORT_TABLES]
            if unknown:
                raise _Hold("a request kind this version doesn't know")
            request_columns = _columns(conn, "request")
            if "format" not in request_columns:
                raise _Refuse("no format")
            formats = conn.execute('SELECT "format" FROM "request" LIMIT 2').fetchall()
            if len(formats) != 1:
                raise _Refuse("the request table must have one row")
            if formats[0][0] != REQUEST_FORMAT and isinstance(formats[0][0], int):
                raise _Hold("a format this version doesn't know")
            request_rows = _read_rows(conn, "request", RequestRow, budget)
            reports = {}
            for table in tables:
                if table != "request":
                    reports[table] = _read_rows(conn, table, REPORT_TABLES[table][0], budget)
                    _check_kinds(table, reports[table])
        finally:
            if conn is not None:
                conn.close()
        if "song_requests" in reports:
            reports["song_requests"] = _not_rejected(reports["song_requests"], catalog_db)
        reports = {table: rows for table, rows in reports.items() if rows}
        if not reports:
            return Vetted(EMPTY)
        return Vetted(ACCEPTED, clean=_rewrite(folder / "clean.db", request_rows[0], sender, reports))
    except _Refuse as exc:
        return Vetted(REFUSED, reason=str(exc))
    except _Hold as exc:
        return Vetted(HELD, reason=str(exc))
    except sqlite3.Error as exc:
        return Vetted(REFUSED, reason=f"unreadable: {type(exc).__name__}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def remove_work_folder(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def log_refusal(inbox_dir: Path, name: str, sender: str | None, reason: str) -> None:
    path = Path(inbox_dir) / REFUSED_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > REFUSED_LOG_MAX_BYTES:
        os.replace(path, path.with_suffix(".jsonl.old"))
    entry = {"at": datetime.now().isoformat(timespec="seconds"), "name": name, "sender": sender, "reason": reason}
    with open(path, "a") as file:
        file.write(json.dumps(entry) + "\n")


def recent_refusals(inbox_dir: Path, count: int = 20) -> list[dict]:
    try:
        lines = (Path(inbox_dir) / REFUSED_LOG).read_text().splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines[-count:]]


def sender_of_name(name: str) -> str | None:
    match = REQUEST_NAME_RE.match(name)
    return match.group("user") if match else None
