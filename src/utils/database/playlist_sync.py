"""
Syncing a user's playlists between their user.db and their phones, with the
sharing server as the hub (docs/runbooks/database.md -> "Playlists on the
phone"; the app's docs/handoffs/musicdatabase-playlists.md):

- The unit is a whole playlist with all its tracks, identified everywhere by
  its `uid`. Every change - from a phone, the menu, or a song merge/deletion
  reaching its tracks - gives it the next number of the user's
  `playlist_revision` counter; a phone asks for "revision > N".
- Conflicts: a change based on the stored revision is applied; one based on
  an older revision is applied only if its `updated_at` (the device's clock)
  is later than the stored one, otherwise the stored playlist is answered
  back (409). A deletion is a change like any other, with `deleted_at` as its
  time.
- A deleted playlist keeps its row (uid, revision, updated_at) without
  tracks, so every phone hears about it. Before its tracks go, the whole
  playlist is written to Users/<name>/archive/playlists/<uid>.<YYMMDD-HHMM>.json
  (never overwritten, never read by code - for recovering by hand), and its
  cover picture to archive/covers/ (covers.py).
- A cover is part of the playlist: its `cover` is the sha256 of a picture
  stored by covers.py. A PUT without the key keeps the stored one.

Standard-library only and free of the app's config, because the sharing
server (sharing_server.py, run on the system python3) imports it as well as
utils/database/user_playlists.py (the menu's writes) and song_references.py.
Callers hand in an sqlite3 connection from connect(); every write function
runs in one BEGIN IMMEDIATE transaction of its own, so the server and the app
writing the same user.db never interleave half a change.
"""

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    from . import covers  # imported as utils.database.playlist_sync (the app, tests)
except ImportError:
    import covers  # run by the sharing server as a script (its folder is on sys.path)

# Must match config.constants (tests/test_playlist_sync.py checks they do)
USER_DB_FILENAME = "user.db"
SOURCES = ("folder", "youtube", "spotify", "spotifyAlbum")
# The migration that added the sync columns; without it the server answers
# 503 instead of migrating (that stays the app's job at startup)
REQUIRED_MIGRATION = "0004_playlist_sync.sql"
ARCHIVE_SUBDIR = Path("archive") / "playlists"
# Same stamp as the database backups (backup.py)
ARCHIVE_STAMP_FORMAT = "%y%m%d-%H%M"
BUSY_TIMEOUT_SECONDS = 15
UID_RE = re.compile(r"^[0-9A-Za-z-]{8,64}$")


# A PUT body without the "cover" key: keep the stored cover (an older app
# version must not wipe covers it doesn't know about); null removes it
KEEP_COVER = object()


class NotMigratedError(Exception):
    """The user.db is missing or hasn't got REQUIRED_MIGRATION yet."""


class BadPlaylistError(ValueError):
    """A playlist body that can't be stored (missing or mistyped fields)."""


def now_ms() -> int:
    return int(time.time() * 1000)


def new_uid() -> str:
    return str(uuid.uuid4())


def connect(db_path, required_migration: str = covers.REQUIRED_MIGRATION) -> sqlite3.Connection:
    """A connection to a user.db that has `required_migration` (by default
    the newest one the sync code reads - every column of playlists, moods
    and covers), in autocommit mode (the write functions open their own transactions).
    Raises NotMigratedError."""
    if not os.path.exists(db_path):
        raise NotMigratedError(f"{db_path} doesn't exist")
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    if not is_migrated(conn, required_migration):
        conn.close()
        raise NotMigratedError(f"{db_path} hasn't got {required_migration} yet")
    return conn


def is_migrated(conn, required_migration: str = REQUIRED_MIGRATION) -> bool:
    try:
        return conn.execute("SELECT 1 FROM schema_version WHERE filename = ?",
                            (required_migration,)).fetchone() is not None
    except sqlite3.Error:
        return False


@contextmanager
def _transaction(conn):
    # IMMEDIATE: take the write lock before reading what the decision rests on
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --------------------
# Revisions
# --------------------

def current_revision(conn) -> int:
    row = conn.execute("SELECT last_revision FROM playlist_revision WHERE id = 1").fetchone()
    return row[0] if row else 0


def next_revision(conn) -> int:
    """The next number of the user's counter. Inside a transaction."""
    if conn.execute("UPDATE playlist_revision SET last_revision = last_revision + 1 WHERE id = 1").rowcount == 0:
        conn.execute("INSERT INTO playlist_revision (id, last_revision) "
                     "SELECT 1, COALESCE(MAX(revision), 0) + 1 FROM playlists")
    return current_revision(conn)


def mark_changed(conn, playlist_ids) -> None:
    """Give each playlist a new revision, inside the caller's transaction -
    for writers that change tracks without going through store_playlist()
    (song_references.apply_song_id_changes())."""
    for playlist_id in sorted(set(playlist_ids)):
        conn.execute("UPDATE playlists SET revision = ? WHERE id = ?", (next_revision(conn), playlist_id))


# --------------------
# JSON
# --------------------

def _tracks_json(conn, playlist_id: int) -> list:
    rows = conn.execute(
        "SELECT song_id, title, artist, youtube_video_id, spotify_track_id, youtube_removed, duration_s "
        "FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist_id,))
    return [{"song_id": r[0], "title": r[1], "artist": r[2], "youtube_video_id": r[3],
             "spotify_track_id": r[4], "youtube_removed": bool(r[5]), "duration_s": r[6]} for r in rows]


def playlist_json(conn, row, with_tracks: bool = True) -> dict:
    """The phone's JSON of one playlists row; a deleted one has no tracks."""
    if row["deleted_at"] is not None:
        return {"uid": row["uid"], "revision": row["revision"], "updated_at": row["updated_at"], "deleted": True}
    payload = {
        "uid": row["uid"], "revision": row["revision"], "name": row["name"],
        "source": row["source"], "source_id": row["source_id"], "source_owner": row["source_owner"],
        "created_at": row["created_at"], "updated_at": row["updated_at"], "deleted": False,
        "cover": row["cover_sha256"],
    }
    if with_tracks:
        payload["tracks"] = _tracks_json(conn, row["id"])
    return payload


def _row_by_uid(conn, uid: str):
    return conn.execute("SELECT * FROM playlists WHERE uid = ?", (uid,)).fetchone()


def changes_since(conn, since: int) -> dict:
    """GET /users/<name>/playlists?since=N: every playlist, deleted ones
    included, changed after revision N, and the counter to ask with next time."""
    conn.execute("BEGIN")  # one consistent snapshot of the counter and the rows
    try:
        rows = conn.execute("SELECT * FROM playlists WHERE revision > ? ORDER BY revision", (since,)).fetchall()
        return {"revision": current_revision(conn), "playlists": [playlist_json(conn, r) for r in rows]}
    finally:
        conn.execute("COMMIT")


# --------------------
# Checking a phone's body
# --------------------

def _int(value, field: str, minimum: int | None = 0, optional: bool = False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
        raise BadPlaylistError(f"{field}: expected an integer" + (f" >= {minimum}" if minimum is not None else ""))
    return value


def _text(value, field: str, optional: bool = True):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise BadPlaylistError(f"{field}: expected a string")
    return value


def parse_cover(body: dict, required: bool = False):
    """The body's "cover": a sha256, None (remove), or KEEP_COVER when the
    key is missing (and not `required`)."""
    if "cover" not in body:
        if required:
            raise BadPlaylistError("cover: missing")
        return KEEP_COVER
    value = body["cover"]
    if value is not None and not covers.is_sha256(value):
        raise BadPlaylistError("cover: expected a lowercase hex sha256 or null")
    return value


def parse_playlist(body) -> dict:
    """A PUT body (or the menu's playlist) checked and reduced to what's
    stored. Raises BadPlaylistError."""
    if not isinstance(body, dict):
        raise BadPlaylistError("expected a JSON object")
    name = _text(body.get("name"), "name", optional=False)
    if not name.strip():
        raise BadPlaylistError("name: empty")
    updated_at = _int(body.get("updated_at"), "updated_at")
    tracks = body.get("tracks")
    if not isinstance(tracks, list):
        raise BadPlaylistError("tracks: expected a list")
    parsed_tracks = []
    for i, t in enumerate(tracks):
        if not isinstance(t, dict):
            raise BadPlaylistError(f"tracks[{i}]: expected an object")
        removed = t.get("youtube_removed", False)
        if not isinstance(removed, bool):
            raise BadPlaylistError(f"tracks[{i}].youtube_removed: expected true/false")
        parsed_tracks.append({
            "song_id": _int(t.get("song_id"), f"tracks[{i}].song_id", minimum=1, optional=True),
            "title": _text(t.get("title"), f"tracks[{i}].title", optional=False),
            "artist": _text(t.get("artist"), f"tracks[{i}].artist"),
            "youtube_video_id": _text(t.get("youtube_video_id"), f"tracks[{i}].youtube_video_id"),
            "spotify_track_id": _text(t.get("spotify_track_id"), f"tracks[{i}].spotify_track_id"),
            "youtube_removed": removed,
            "duration_s": _int(t.get("duration_s"), f"tracks[{i}].duration_s", optional=True),
        })
    return {
        "name": name,
        "source": _text(body.get("source"), "source"),
        "source_id": _text(body.get("source_id"), "source_id"),
        "source_owner": _text(body.get("source_owner"), "source_owner"),
        "created_at": _int(body.get("created_at"), "created_at", optional=True),
        "updated_at": updated_at,
        "tracks": parsed_tracks,
        "cover": parse_cover(body),
    }


def parse_base_revision(value) -> int:
    return _int(value, "base_revision")


# --------------------
# Song ids from a phone's older music.db
# --------------------

def resolve_song_ids(tracks: list, music_db_path) -> int:
    """Run every track's song_id through song_id_changes of `music_db_path`
    (the shared copy the phone downloads): a merged song becomes the song it
    was merged into (following chains), a deleted one NULL - the title/artist
    stay. An id neither in songs nor in the log is dropped too (no phone got
    it from a music.db). Changes `tracks` in place. Returns the last log entry
    checked against, for playlists.song_ids_checked_to - 0 when the copy
    can't be read, so the app replays the whole log for this playlist."""
    try:
        conn = sqlite3.connect(f"file:{music_db_path}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    except sqlite3.Error:
        return 0
    try:
        changes = conn.execute("SELECT id, old_song_id, new_song_id FROM song_id_changes ORDER BY id").fetchall()
        wanted = {t["song_id"] for t in tracks if t["song_id"] is not None}
        existing = set()
        if wanted:
            placeholders = ",".join("?" * len(wanted))
            existing = {r[0] for r in conn.execute(f"SELECT id FROM songs WHERE id IN ({placeholders})",
                                                   tuple(wanted))}
    except sqlite3.Error:
        return 0
    finally:
        conn.close()

    moved = {old: new for _, old, new in changes}
    for track in tracks:
        song_id = track["song_id"]
        seen = set()
        while song_id in moved:
            if song_id in seen:  # a loop in the log - can't happen, but never spin
                song_id = None
                break
            seen.add(song_id)
            song_id = moved[song_id]
        # A merge target from the log is trusted; an id the phone sent that
        # is neither a song nor in the log never came from a music.db
        if song_id is not None and not seen and song_id not in existing:
            song_id = None
        track["song_id"] = song_id
    return changes[-1][0] if changes else 0


# --------------------
# Writing
# --------------------

def _write_tracks(conn, playlist_id: int, tracks: list) -> None:
    conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
    conn.executemany(
        "INSERT INTO playlist_tracks (playlist_id, position, song_id, title, artist, youtube_video_id, "
        "spotify_track_id, youtube_removed, duration_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(playlist_id, i, t["song_id"], t["title"], t["artist"], t["youtube_video_id"],
          t["spotify_track_id"], int(t["youtube_removed"]), t["duration_s"]) for i, t in enumerate(tracks)])


def _wins(row, base_revision: int, updated_at: int) -> bool:
    """Decision 3: a change based on the stored revision is applied; one
    based on an older one only if it's newer than what's stored."""
    return base_revision == row["revision"] or updated_at > row["updated_at"]


def store_playlist(conn, username: str, uid: str, playlist: dict, base_revision: int,
                   song_ids_checked_to: int | None = None, merge_same_source: bool = True) -> tuple[bool, dict]:
    """Create or replace a playlist (`playlist` from parse_playlist()).
    Returns (applied, the stored playlist as JSON) - not applied = a conflict
    the stored one won. An unknown uid is created, except that with
    `merge_same_source` a live playlist with the same source + source_id
    (two phones imported the same YouTube/Spotify playlist) is updated
    instead, keeping its uid. Its cover: the newer side's, or the other
    side's when the newer has none. A `cover` must already be stored as a
    blob (the caller checks, covers.require_blob())."""
    incoming_cover = playlist.get("cover", KEEP_COVER)
    with _transaction(conn):
        row = _row_by_uid(conn, uid)
        same_source = False
        if row is None and merge_same_source and playlist["source"] and playlist["source_id"]:
            row = conn.execute(
                "SELECT * FROM playlists WHERE source = ? AND source_id = ? AND deleted_at IS NULL "
                "ORDER BY id LIMIT 1", (playlist["source"], playlist["source_id"])).fetchone()
            same_source = row is not None
        if row is not None and not _wins(row, base_revision, playlist["updated_at"]):
            if same_source and row["cover_sha256"] is None and incoming_cover not in (None, KEEP_COVER):
                # The same import on two phones: the newer side's cover, or
                # the other's when the newer one has none
                conn.execute("UPDATE playlists SET cover_sha256 = ?, revision = ? WHERE id = ?",
                             (incoming_cover, next_revision(conn), row["id"]))
                row = _row_by_uid(conn, row["uid"])
            return False, playlist_json(conn, row)

        if incoming_cover is KEEP_COVER or (same_source and incoming_cover is None):
            cover = row["cover_sha256"] if row is not None else None
        else:
            cover = incoming_cover
        revision = next_revision(conn)
        values = (playlist["name"], username, playlist["source"], playlist["source_id"], playlist["source_owner"],
                  playlist["updated_at"], revision, song_ids_checked_to, cover)
        if row is None:
            created_at = playlist["created_at"] if playlist["created_at"] is not None else playlist["updated_at"]
            playlist_id = conn.execute(
                "INSERT INTO playlists (name, owner, source, source_id, source_owner, updated_at, revision, "
                "song_ids_checked_to, cover_sha256, uid, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values + (uid, created_at)).lastrowid
        else:
            playlist_id = row["id"]
            conn.execute(
                "UPDATE playlists SET name = ?, owner = ?, source = ?, source_id = ?, source_owner = ?, "
                "updated_at = ?, revision = ?, song_ids_checked_to = ?, cover_sha256 = ?, deleted_at = NULL, "
                "created_at = COALESCE(?, created_at) WHERE id = ?",
                values + (playlist["created_at"], playlist_id))
        _write_tracks(conn, playlist_id, playlist["tracks"])
        stored = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return True, playlist_json(conn, stored)


def _archive(user_folder, payload: dict, deleted_at: int, subdir: Path = ARCHIVE_SUBDIR) -> Path:
    """Write a deleted playlist (or mood) to <user folder>/<subdir>/<uid>.<stamp>.json."""
    folder = Path(user_folder) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(deleted_at / 1000).strftime(ARCHIVE_STAMP_FORMAT)
    data = json.dumps({**payload, "deleted_at": deleted_at}, ensure_ascii=False, indent=2) + "\n"
    suffix = 1
    while True:
        target = folder / f"{payload['uid']}.{stamp}{'' if suffix == 1 else f'_{suffix}'}.json"
        try:
            # "x": never overwrite an archived file
            with open(target, "x", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            return target
        except FileExistsError:
            suffix += 1


def delete_playlist(conn, user_folder, uid: str, base_revision: int,
                    deleted_at: int) -> tuple[bool, dict, Path | None]:
    """Delete a playlist everywhere: archive it, drop its tracks, keep the
    row as a marker with a new revision. Returns (applied, the stored
    playlist as JSON, the archive file). An unknown or already deleted uid
    counts as done (applied, nothing archived); a conflict the stored
    playlist won is (False, it, None)."""
    with _transaction(conn):
        row = _row_by_uid(conn, uid)
        if row is None:
            return True, {"uid": uid, "revision": 0, "updated_at": deleted_at, "deleted": True}, None
        if row["deleted_at"] is not None:
            return True, playlist_json(conn, row), None
        if not _wins(row, base_revision, deleted_at):
            return False, playlist_json(conn, row), None

        # Written before anything is dropped; if the commit fails after it,
        # an extra archive file is all that's left
        archived = _archive(user_folder, {**playlist_json(conn, row), "owner": row["owner"]}, deleted_at)
        covers.archive_blob(user_folder, row["cover_sha256"])
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (row["id"],))
        conn.execute("UPDATE playlists SET deleted_at = ?, updated_at = ?, revision = ?, "
                     "song_ids_checked_to = NULL, cover_sha256 = NULL WHERE id = ?",
                     (deleted_at, deleted_at, next_revision(conn), row["id"]))
        return True, playlist_json(conn, _row_by_uid(conn, uid)), archived
