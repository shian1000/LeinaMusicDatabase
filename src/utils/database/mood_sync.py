"""
Syncing a user's own moods ("Nastroje" on the phone) between their user.db
and their phones, with the sharing server as the hub - the playlist rules of
playlist_sync.py, applied to moods (docs/runbooks/database.md -> "Moods on
the phone"; the app's docs/handoffs/musicdatabase-moods.md):

- A mood is a private tag (`tags` row, kind 'mood') and its songs are its
  `song_tags` rows: music.db song ids only, no order, each song once. The
  unit is a whole mood (name + every song id), identified everywhere by its
  `uid`; every change gives it the next number of the user's `mood_revision`
  counter (separate from playlists').
- Conflicts as for playlists: a change based on the stored revision is
  applied; one based on an older revision only if its `updated_at` is later,
  otherwise the stored mood is answered back (409).
- Names are unique among a user's live moods. A mood sent under a new uid
  with the name of a live mood (two phones made "Siłownia" before syncing)
  is merged into it: the union of the songs, and the answer carries the
  existing uid for the phone to adopt. A mood renamed to the name of another
  live mood is merged into that one the same way and becomes a deletion
  marker with `merged_into`.
- A deleted mood keeps its row without songs, archived first to
  Users/<name>/archive/moods/<uid>.<YYMMDD-HHMM>.json. A mood the owner
  promoted to a global one (utils/database/moods.py) is deleted the same
  way, with `promoted_to` (the global mood's tag.db id and name) so the phone
  can switch to it. A promoted or merged mood is never brought back: a later
  PUT gets the marker (409).
- A mood's `cover` (covers.py) is part of it, as for playlists. A merge by
  name keeps the newer side's cover, or the other's when the newer has none.
  Promotion turns the cover into the user's private cover of the global mood.
- Private covers of global moods: one row per tag.db mood id in
  global_mood_covers, synced the same way with its own counter
  (global_mood_cover_revision); a removed cover stays as a row with NULL.

Standard-library only, like playlist_sync.py (which it builds on): the
sharing server imports it as well as the app (moods.py, song_references.py).
"""

import sqlite3
from pathlib import Path

try:
    from . import covers, playlist_sync  # imported as utils.database.mood_sync (the app, tests)
except ImportError:
    import covers  # run by the sharing server as a script (its folder is on sys.path)
    import playlist_sync

# Must match config.constants.TAG_KIND_MOOD (tests/test_mood_sync.py checks)
MOOD_KIND = "mood"
REQUIRED_MIGRATION = "0005_moods.sql"
ARCHIVE_SUBDIR = Path("archive") / "moods"

# One error type for every body the server can't store (-> 400)
BadMoodError = playlist_sync.BadPlaylistError
NotMigratedError = playlist_sync.NotMigratedError
_transaction = playlist_sync._transaction
_int = playlist_sync._int


def connect(db_path) -> sqlite3.Connection:
    """A connection to a user.db with the mood (and cover) columns. Raises
    NotMigratedError."""
    return playlist_sync.connect(db_path)


# --------------------
# Revisions
# --------------------

_REVISIONS = playlist_sync.RevisionCounter("mood_revision", "tags")
current_revision = _REVISIONS.current
next_revision = _REVISIONS.next


def mark_changed(conn, tag_ids) -> None:
    """A new revision for each of these tags that is a mood, inside the
    caller's transaction - for song_references.apply_song_id_changes(),
    which changes song ids without going through store_mood()."""
    for tag_id in sorted(set(tag_ids)):
        if conn.execute("SELECT 1 FROM tags WHERE id = ? AND kind = ?", (tag_id, MOOD_KIND)).fetchone():
            conn.execute("UPDATE tags SET revision = ? WHERE id = ?", (next_revision(conn), tag_id))


# --------------------
# Reading
# --------------------

def song_ids(conn, tag_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT song_id FROM song_tags WHERE tag_id = ? ORDER BY song_id",
                                       (tag_id,))]


def mood_json(conn, row) -> dict:
    """The phone's JSON of one mood row; a deleted one has no name or songs."""
    if row["deleted_at"] is not None:
        payload = {"uid": row["uid"], "revision": row["revision"], "updated_at": row["updated_at"],
                   "deleted": True}
        if row["promoted_to"] is not None:
            payload["promoted_to"] = row["promoted_to"]
            payload["promoted_to_name"] = row["promoted_to_name"]
        if row["merged_into"] is not None:
            payload["merged_into"] = row["merged_into"]
        return payload
    return {"uid": row["uid"], "revision": row["revision"], "name": row["name"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "deleted": False,
            "song_ids": song_ids(conn, row["id"]), "cover": row["cover_sha256"]}


def _row_by_uid(conn, uid: str):
    return conn.execute("SELECT * FROM tags WHERE uid = ? AND kind = ?", (uid, MOOD_KIND)).fetchone()


def _row_by_id(conn, tag_id: int):
    return conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()


def _live_by_name(conn, name: str):
    return conn.execute("SELECT * FROM tags WHERE name = ? AND deleted_at IS NULL", (name,)).fetchone()


def changes_since(conn, since: int) -> dict:
    """GET /users/<name>/moods?since=N: every mood, deleted ones included,
    changed after revision N, and the counter to ask with next time."""
    conn.execute("BEGIN")
    try:
        rows = conn.execute("SELECT * FROM tags WHERE kind = ? AND revision > ? ORDER BY revision",
                            (MOOD_KIND, since)).fetchall()
        return {"revision": current_revision(conn), "moods": [mood_json(conn, r) for r in rows]}
    finally:
        conn.execute("COMMIT")


def live_moods(conn) -> list:
    """(id, uid, name, song count, cover sha256 or None) of every live mood,
    by name - for the menu."""
    return [tuple(r) for r in conn.execute(
        "SELECT t.id, t.uid, t.name, (SELECT COUNT(*) FROM song_tags st WHERE st.tag_id = t.id), t.cover_sha256 "
        "FROM tags t WHERE t.kind = ? AND t.deleted_at IS NULL ORDER BY t.name", (MOOD_KIND,))]


# --------------------
# Checking a phone's body
# --------------------

def parse_mood(body) -> dict:
    """A PUT body checked and reduced to what's stored. The name is trimmed,
    the song ids deduplicated. Raises BadMoodError."""
    if not isinstance(body, dict):
        raise BadMoodError("expected a JSON object")
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadMoodError("name: expected a non-empty string")
    ids = body.get("song_ids")
    if not isinstance(ids, list):
        raise BadMoodError("song_ids: expected a list")
    return {
        "name": name.strip(),
        "created_at": _int(body.get("created_at"), "created_at", optional=True),
        "updated_at": _int(body.get("updated_at"), "updated_at"),
        "song_ids": sorted({_int(v, f"song_ids[{i}]", minimum=1) for i, v in enumerate(ids)}),
        "cover": playlist_sync.parse_cover(body),
    }


def resolve_song_ids(ids: list, music_db_path) -> tuple[list, int]:
    """playlist_sync.resolve_song_ids() for a mood's ids: merged songs
    followed to the kept one, deleted or unknown ones dropped (a mood holds
    only songs that exist - the owner's decision 1). Returns (the ids, the
    last log entry checked against)."""
    tracks = [{"song_id": i} for i in ids]
    checked_to = playlist_sync.resolve_song_ids(tracks, music_db_path)
    return sorted({t["song_id"] for t in tracks if t["song_id"] is not None}), checked_to


# --------------------
# Writing
# --------------------

def _write_songs(conn, tag_id: int, ids) -> None:
    conn.execute("DELETE FROM song_tags WHERE tag_id = ?", (tag_id,))
    conn.executemany("INSERT INTO song_tags (song_id, tag_id) VALUES (?, ?)", [(i, tag_id) for i in sorted(set(ids))])


def _is_final(row) -> bool:
    """Promoted or merged: the phone must switch to the other mood instead."""
    return row["deleted_at"] is not None and (row["promoted_to"] is not None or row["merged_into"] is not None)


def _earliest(*values):
    known = [v for v in values if v is not None]
    return min(known) if known else None


def _retire(conn, user_folder, row, at: int, **marker) -> Path | None:
    """Archive a live mood, drop its songs and keep the row as a deletion
    marker (with `promoted_to`/`promoted_to_name` or `merged_into`) under a
    new revision. Inside the caller's transaction."""
    archived = None
    if row["deleted_at"] is None:
        archived = playlist_sync._archive(user_folder, mood_json(conn, row), at, subdir=ARCHIVE_SUBDIR)
        covers.archive_blob(user_folder, row["cover_sha256"])
    conn.execute("DELETE FROM song_tags WHERE tag_id = ?", (row["id"],))
    conn.execute(
        "UPDATE tags SET deleted_at = ?, updated_at = ?, revision = ?, song_ids_checked_to = NULL, "
        "promoted_to = ?, promoted_to_name = ?, merged_into = ?, cover_sha256 = NULL WHERE id = ?",
        (at, at, next_revision(conn), marker.get("promoted_to"), marker.get("promoted_to_name"),
         marker.get("merged_into"), row["id"]))
    return archived


def store_mood(conn, user_folder, uid: str, mood: dict, base_revision: int,
               song_ids_checked_to: int | None = None) -> tuple[bool, dict]:
    """Create or replace a mood (`mood` from parse_mood()). Returns
    (applied, the stored mood as JSON) - not applied = a conflict the stored
    one won, or a promoted/merged mood. When the name belongs to another live
    mood, the songs are merged into that one and the answer is it. A `cover`
    must already be stored as a blob (the caller checks)."""
    with _transaction(conn):
        row = _row_by_uid(conn, uid)
        if row is not None and (_is_final(row) or not playlist_sync._wins(row, base_revision, mood["updated_at"])):
            return False, mood_json(conn, row)
        cover = mood.get("cover", playlist_sync.KEEP_COVER)
        if cover is playlist_sync.KEEP_COVER:
            cover = row["cover_sha256"] if row is not None else None

        other = _live_by_name(conn, mood["name"])
        if other is not None and (row is None or other["id"] != row["id"]):
            if other["kind"] != MOOD_KIND:
                raise BadMoodError(f"name: '{mood['name']}' is taken by a private tag that isn't a mood")
            _write_songs(conn, other["id"], set(song_ids(conn, other["id"])) | set(mood["song_ids"]))
            if mood["updated_at"] > (other["updated_at"] or 0):
                cover = cover or other["cover_sha256"]
            else:
                cover = other["cover_sha256"] or cover
            conn.execute("UPDATE tags SET updated_at = ?, revision = ?, song_ids_checked_to = ?, cover_sha256 = ? "
                         "WHERE id = ?",
                         (max(other["updated_at"] or 0, mood["updated_at"]), next_revision(conn),
                          _earliest(other["song_ids_checked_to"], song_ids_checked_to), cover, other["id"]))
            if row is not None and row["deleted_at"] is None:
                _retire(conn, user_folder, row, mood["updated_at"], merged_into=other["uid"])
            playlist_sync.replace_folder_refs(conn, "mood", uid, other["uid"])
            return True, mood_json(conn, _row_by_id(conn, other["id"]))

        revision = next_revision(conn)
        if row is None:
            created_at = mood["created_at"] if mood["created_at"] is not None else mood["updated_at"]
            tag_id = conn.execute(
                "INSERT INTO tags (name, kind, uid, revision, created_at, updated_at, song_ids_checked_to, "
                "cover_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mood["name"], MOOD_KIND, uid, revision, created_at, mood["updated_at"],
                 song_ids_checked_to, cover)).lastrowid
        else:
            tag_id = row["id"]
            conn.execute(
                "UPDATE tags SET name = ?, revision = ?, updated_at = ?, song_ids_checked_to = ?, "
                "cover_sha256 = ?, deleted_at = NULL, created_at = COALESCE(?, created_at) WHERE id = ?",
                (mood["name"], revision, mood["updated_at"], song_ids_checked_to, cover, mood["created_at"],
                 tag_id))
        _write_songs(conn, tag_id, mood["song_ids"])
        return True, mood_json(conn, _row_by_id(conn, tag_id))


def delete_mood(conn, user_folder, uid: str, base_revision: int,
                deleted_at: int) -> tuple[bool, dict, Path | None]:
    """Delete a mood everywhere: archive it, drop its songs, keep the row as
    a marker. Returns (applied, the stored mood as JSON, the archive file).
    Unknown or already deleted counts as done; a conflict the stored mood
    won is (False, it, None)."""
    with _transaction(conn):
        row = _row_by_uid(conn, uid)
        if row is None:
            return True, {"uid": uid, "revision": 0, "updated_at": deleted_at, "deleted": True}, None
        if row["deleted_at"] is not None:
            return True, mood_json(conn, row), None
        if not playlist_sync._wins(row, base_revision, deleted_at):
            return False, mood_json(conn, row), None
        archived = _retire(conn, user_folder, row, deleted_at)
        return True, mood_json(conn, _row_by_id(conn, row["id"])), archived


def mark_promoted(conn, user_folder, tag_id: int, global_id: int, global_name: str,
                  at: int | None = None) -> Path | None:
    """The owner moved this mood into the global mood `global_id` of tag.db
    (moods.promote_user_mood(), which has already copied the songs there):
    it becomes a deletion marker with promoted_to, and its cover becomes the
    user's private cover of the global mood. Returns the archive file."""
    at = at if at is not None else playlist_sync.now_ms()
    with _transaction(conn):
        row = _row_by_id(conn, tag_id)
        if row is None or row["kind"] != MOOD_KIND or row["deleted_at"] is not None:
            return None
        if row["cover_sha256"] is not None:
            _write_global_cover(conn, global_id, row["cover_sha256"], at)
        return _retire(conn, user_folder, row, at, promoted_to=global_id, promoted_to_name=global_name)


# --------------------
# Private covers of global moods
# --------------------

_COVER_REVISIONS = playlist_sync.RevisionCounter("global_mood_cover_revision", "global_mood_covers")
current_cover_revision = _COVER_REVISIONS.current
_next_cover_revision = _COVER_REVISIONS.next


def global_cover_json(row) -> dict:
    return {"mood_id": row["mood_id"], "revision": row["revision"], "cover": row["cover_sha256"],
            "updated_at": row["updated_at"]}


def _global_cover_row(conn, mood_id: int):
    return conn.execute("SELECT * FROM global_mood_covers WHERE mood_id = ?", (mood_id,)).fetchone()


def global_covers_since(conn, since: int) -> dict:
    """GET /users/<name>/global-mood-covers?since=N."""
    conn.execute("BEGIN")
    try:
        rows = conn.execute("SELECT * FROM global_mood_covers WHERE revision > ? ORDER BY revision",
                            (since,)).fetchall()
        return {"revision": current_cover_revision(conn), "covers": [global_cover_json(r) for r in rows]}
    finally:
        conn.execute("COMMIT")


def global_covers(conn) -> dict[int, str]:
    """{global mood id: cover sha256} of every private cover - for the menu."""
    return dict(conn.execute("SELECT mood_id, cover_sha256 FROM global_mood_covers "
                             "WHERE cover_sha256 IS NOT NULL").fetchall())


def parse_global_cover(body) -> dict:
    """A PUT body: base_revision, updated_at and cover (sha256 or null) all
    required. Raises BadMoodError."""
    if not isinstance(body, dict):
        raise BadMoodError("expected a JSON object")
    return {"base_revision": playlist_sync.parse_base_revision(body.get("base_revision")),
            "updated_at": _int(body.get("updated_at"), "updated_at"),
            "cover": playlist_sync.parse_cover(body, required=True)}


def _write_global_cover(conn, mood_id: int, cover: str | None, updated_at: int) -> None:
    conn.execute("INSERT INTO global_mood_covers (mood_id, cover_sha256, revision, updated_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (mood_id) DO UPDATE SET cover_sha256 = excluded.cover_sha256, "
                 "revision = excluded.revision, updated_at = excluded.updated_at",
                 (mood_id, cover, _next_cover_revision(conn), updated_at))


def store_global_cover(conn, mood_id: int, cover: str | None, base_revision: int,
                       updated_at: int) -> tuple[bool, dict]:
    """Set (or with None remove) the user's private cover of a global mood.
    Conflicts as for moods. Returns (applied, the stored row as JSON). Any
    mood id >= 1 is taken: the phone's tag.db may be older or newer."""
    with _transaction(conn):
        row = _global_cover_row(conn, mood_id)
        if row is not None and not playlist_sync._wins(row, base_revision, updated_at):
            return False, global_cover_json(row)
        _write_global_cover(conn, mood_id, cover, updated_at)
        return True, global_cover_json(_global_cover_row(conn, mood_id))
