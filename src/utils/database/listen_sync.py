"""
Syncing a user's plays ("listens", for Leina Radio's listening stats)
between their user.db and their phones, with the sharing server as the hub
(docs/runbooks/database.md -> "Listening stats on the phone"; the app's
docs/handoffs/musicdatabase-listening-stats.md). MusicDatabase only stores
and hands back the plays; the stats are computed on the phone.

- A play is identified by its `uid` (made on the phone). Plays are only
  added, or reported again with a longer `listened_ms` (the song still
  playing, then its final time): the longer one wins, `duration_ms` is filled
  in if it was unknown, nothing else changes. No deletions.
- Every added or updated play gets the next number of the user's
  `listen_revision` counter (separate from playlists', moods' and folders').
  The phone pages through `changes_since()` by revision.
- Fields are stored as sent and never checked against music.db or tag.db
  (`song_id` may come from an older or newer music.db). Keys this module
  doesn't know are kept in `extra` and handed back, for later app versions.
- A batch is all or nothing: one bad play and nothing is stored.

Standard-library only, like playlist_sync.py (which it builds on): the
sharing server imports it.
"""

import json
import re

try:
    from . import playlist_sync  # imported as utils.database.listen_sync (the app, tests)
except ImportError:
    import playlist_sync  # run by the sharing server as a script (its folder is on sys.path)

REQUIRED_MIGRATION = "0009_listens.sql"
UID_RE = re.compile(r"^[0-9A-Za-z-]{1,64}$")
DEFAULT_LIMIT = 1000
MAX_LIMIT = 1000

BadListenError = playlist_sync.BadPlaylistError
NotMigratedError = playlist_sync.NotMigratedError
_transaction = playlist_sync._transaction
_int = playlist_sync._int
_text = playlist_sync._text

# The columns of a play besides uid / listened_ms / duration_ms, with their
# check: (field, "int" | "text", required)
_FIELDS = (
    ("started_at", "int", True),
    ("title", "text", True),
    ("artist", "text", False),
    ("album", "text", False),
    ("song_id", "int", False),
    ("youtube_video_id", "text", False),
    ("spotify_track_id", "text", False),
    ("source", "text", False),
    ("playlist_uid", "text", False),
    ("playlist_name", "text", False),
    ("mood_uid", "text", False),
    ("global_mood_id", "int", False),
    ("mood_name", "text", False),
)
_COLUMNS = ("uid", "listened_ms", "duration_ms") + tuple(f for f, _, _ in _FIELDS)
# Keys never kept in `extra`: our own answer's `revision` if a phone echoes it
_IGNORED_KEYS = {"revision"}


def connect(db_path):
    """A connection to a user.db with the listens table. Raises
    NotMigratedError."""
    return playlist_sync.connect(db_path, REQUIRED_MIGRATION)


# --------------------
# Revisions
# --------------------

def current_revision(conn) -> int:
    row = conn.execute("SELECT last_revision FROM listen_revision WHERE id = 1").fetchone()
    return row[0] if row else 0


def next_revision(conn) -> int:
    """The next number of the user's listen counter. Inside a transaction."""
    if conn.execute("UPDATE listen_revision SET last_revision = last_revision + 1 WHERE id = 1").rowcount == 0:
        conn.execute("INSERT INTO listen_revision (id, last_revision) "
                     "SELECT 1, COALESCE(MAX(revision), 0) + 1 FROM listens")
    return current_revision(conn)


# --------------------
# Reading
# --------------------

def listen_json(row) -> dict:
    """The phone's JSON of one listens row: the keys it sent in `extra`
    first, the columns over them, and its revision."""
    play = json.loads(row["extra"]) if row["extra"] else {}
    play.update({column: row[column] for column in _COLUMNS})
    play["revision"] = row["revision"]
    return play


def changes_since(conn, since: int, limit: int = DEFAULT_LIMIT) -> dict:
    """GET /users/<name>/listens?since=N&limit=L: at most L plays changed
    after revision N, by revision. With more to come, "revision" is the last
    listed play's (the phone asks again from there), else the counter."""
    limit = max(1, min(limit, MAX_LIMIT))
    conn.execute("BEGIN")
    try:
        rows = conn.execute("SELECT * FROM listens WHERE revision > ? ORDER BY revision LIMIT ?",
                            (since, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        revision = rows[-1]["revision"] if more else current_revision(conn)
        return {"revision": revision, "listens": [listen_json(r) for r in rows], "more": more}
    finally:
        conn.execute("COMMIT")


def listen_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM listens").fetchone()[0]


# --------------------
# Checking a phone's body
# --------------------

def parse_listen(play) -> dict:
    """One play checked and reduced to its columns plus `extra` (the other
    keys, as a JSON text or None). Raises BadListenError."""
    if not isinstance(play, dict):
        raise BadListenError("expected a JSON object")
    uid = play.get("uid")
    if not isinstance(uid, str) or not UID_RE.match(uid):
        raise BadListenError("uid: expected 1-64 characters [A-Za-z0-9-]")
    parsed = {
        "uid": uid,
        "listened_ms": _int(play.get("listened_ms"), "listened_ms"),
        "duration_ms": _int(play.get("duration_ms"), "duration_ms", optional=True),
    }
    for field, kind, required in _FIELDS:
        value = play.get(field)
        if kind == "int":
            # Ids and times as sent: any integer (song ids aren't checked)
            parsed[field] = _int(value, field, minimum=0 if field == "started_at" else None,
                                 optional=not required)
        else:
            parsed[field] = _text(value, field, optional=not required)
    extra = {k: v for k, v in play.items() if k not in _COLUMNS and k not in _IGNORED_KEYS}
    parsed["extra"] = json.dumps(extra, ensure_ascii=False, separators=(",", ":")) if extra else None
    return parsed


def parse_listens(body) -> list[dict]:
    """A POST body {"listens": [...]} checked as a whole. Raises
    BadListenError naming the first bad play."""
    if not isinstance(body, dict) or not isinstance(body.get("listens"), list):
        raise BadListenError("expected {\"listens\": [...]}")
    plays = []
    for i, play in enumerate(body["listens"]):
        try:
            plays.append(parse_listen(play))
        except BadListenError as exc:
            raise BadListenError(f"listens[{i}]: {exc}") from None
    return plays


# --------------------
# Writing
# --------------------

def store_listens(conn, plays: list[dict]) -> dict:
    """Store plays from parse_listens() in one transaction: a new uid is
    added, a known one updated only when its listened_ms is larger. Returns
    {"revision": the counter, "added": n, "updated": n}."""
    added = updated = 0
    with _transaction(conn):
        for play in plays:
            row = conn.execute("SELECT id, listened_ms FROM listens WHERE uid = ?", (play["uid"],)).fetchone()
            if row is None:
                columns = _COLUMNS + ("extra", "revision")
                values = [play[c] for c in _COLUMNS] + [play["extra"], next_revision(conn)]
                conn.execute(f"INSERT INTO listens ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                             values)
                added += 1
            elif play["listened_ms"] > row["listened_ms"]:
                conn.execute("UPDATE listens SET listened_ms = ?, duration_ms = COALESCE(duration_ms, ?), "
                             "revision = ? WHERE id = ?",
                             (play["listened_ms"], play["duration_ms"], next_revision(conn), row["id"]))
                updated += 1
        return {"revision": current_revision(conn), "added": added, "updated": updated}
