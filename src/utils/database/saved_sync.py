"""
Syncing Leina Radio's saved songs and links between a user's user.db and
their phones, with the sharing server as the hub (docs/runbooks/database.md
-> "Saved songs and links on the phone"; the app's
docs/handoffs/musicdatabase-saved-songs.md). Two collections, the folder
rules of folder_sync.py applied to each:

- saved-songs: one song on one list ("liked" = Polubione utwory, "later" =
  Na później, or a list a later app version adds), `uid` a hash of the list
  and the song, so the same song liked on two phones is one item.
- later-links: one link someone sent, saved "Na później".

- The unit is one item, identified everywhere by its `uid`; every change
  gives it the next number of its collection's counter (saved_song_revision
  / later_link_revision, separate from each other and from playlists').
- Conflicts as for playlists and folders: a change based on the stored
  revision is applied; one based on an older revision only if its
  `updated_at` is later, otherwise the stored item is answered back (409). A
  PUT with base_revision 0 for a uid that exists is normal (the same song
  liked on another phone, a list uploaded again after a reinstall) - the
  same rule decides. A PUT newer than a deletion brings the item back.
- The item is stored verbatim: every key but uid / revision / updated_at /
  deleted / base_revision, unknown ones included. A song is never checked
  against music.db (it may be a YouTube video or a Spotify track music.db
  hasn't got) and a link's URL never fetched.
- A deleted item keeps its row without data; a DELETE of a uid never seen
  stores the marker too, so the other phones drop it.
- follow_song_merges() points saved songs at the song theirs was merged
  into (song_references.py), each under a new revision.

Standard-library only, like playlist_sync.py (which it builds on): the
sharing server imports it.
"""

import json
import re
from dataclasses import dataclass
from typing import Callable

try:
    from . import playlist_sync  # imported as utils.database.saved_sync (the app, tests)
except ImportError:
    import playlist_sync  # run by the sharing server as a script (its folder is on sys.path)

REQUIRED_MIGRATION = "0010_saved_songs.sql"
UID_RE = re.compile(r"^[0-9A-Za-z-]{1,64}$")
# Never part of the stored data: the server's own fields and the request's
NOT_STORED_KEYS = frozenset({"uid", "revision", "updated_at", "deleted", "deleted_at", "base_revision"})

BadItemError = playlist_sync.BadPlaylistError
NotMigratedError = playlist_sync.NotMigratedError
_transaction = playlist_sync._transaction
_int = playlist_sync._int


def connect(db_path):
    """A connection to a user.db with both tables. Raises NotMigratedError."""
    return playlist_sync.connect(db_path, REQUIRED_MIGRATION)


# --------------------
# Checking a phone's body
# --------------------

def _check_song_item(data: dict) -> None:
    if not isinstance(data.get("list"), str) or not data["list"]:
        raise BadItemError("list: expected a non-empty string")
    _int(data.get("added_at"), "added_at")
    song = data.get("song")
    if not isinstance(song, dict):
        raise BadItemError("song: expected an object")
    if not isinstance(song.get("title"), str):
        raise BadItemError("song.title: expected a string")


def _check_link_item(data: dict) -> None:
    if not isinstance(data.get("url"), str) or not data["url"].strip():
        raise BadItemError("url: expected a non-empty string")
    _int(data.get("saved_at"), "saved_at")


@dataclass(frozen=True)
class Collection:
    path: str          # the URL segment: /users/<name>/<path>[/<uid>]
    table: str
    counter: str       # its revision counter's table
    list_key: str      # the list's key in GET's answer
    check: Callable[[dict], None]
    label: str         # for the server's log

    @property
    def revisions(self) -> "playlist_sync.RevisionCounter":
        return playlist_sync.RevisionCounter(self.counter, self.table)


SAVED_SONGS = Collection("saved-songs", "saved_songs", "saved_song_revision", "songs", _check_song_item,
                         "Saved song")
LATER_LINKS = Collection("later-links", "later_links", "later_link_revision", "links", _check_link_item,
                         "Saved link")
COLLECTIONS = {c.path: c for c in (SAVED_SONGS, LATER_LINKS)}


def parse_item(collection: Collection, body) -> dict:
    """A PUT body checked and reduced to {"updated_at", "data"}, `data`
    being every key that's stored verbatim. Raises BadItemError."""
    if not isinstance(body, dict):
        raise BadItemError("expected a JSON object")
    data = {k: v for k, v in body.items() if k not in NOT_STORED_KEYS}
    collection.check(data)
    return {"updated_at": _int(body.get("updated_at"), "updated_at"), "data": data}


def _song_id(data: dict):
    song_id = data.get("song", {}).get("song_id")
    return song_id if isinstance(song_id, int) and not isinstance(song_id, bool) else None


# --------------------
# Revisions
# --------------------

def current_revision(conn, collection: Collection) -> int:
    return collection.revisions.current(conn)


def next_revision(conn, collection: Collection) -> int:
    """The next number of the collection's counter. Inside a transaction."""
    return collection.revisions.next(conn)


# --------------------
# Reading
# --------------------

def item_json(row) -> dict:
    """The phone's JSON of one row; a deleted one has no data."""
    if row["deleted_at"] is not None:
        return {"uid": row["uid"], "revision": row["revision"], "updated_at": row["updated_at"], "deleted": True}
    return {**json.loads(row["data"] or "{}"), "uid": row["uid"], "revision": row["revision"],
            "updated_at": row["updated_at"], "deleted": False}


def _row_by_uid(conn, collection: Collection, uid: str):
    return conn.execute(f"SELECT * FROM {collection.table} WHERE uid = ?", (uid,)).fetchone()


def changes_since(conn, collection: Collection, since: int) -> dict:
    """GET /users/<name>/<collection>?since=N: every item, deleted ones
    included, changed after revision N, and the counter to ask with next time."""
    conn.execute("BEGIN")
    try:
        rows = conn.execute(f"SELECT * FROM {collection.table} WHERE revision > ? ORDER BY revision",
                            (since,)).fetchall()
        return {"revision": current_revision(conn, collection), collection.list_key: [item_json(r) for r in rows]}
    finally:
        conn.execute("COMMIT")


# --------------------
# Writing
# --------------------

def _data_text(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _write(conn, collection: Collection, row_id, uid: str, data: dict | None, revision: int,
           updated_at: int, deleted_at: int | None) -> None:
    columns = {"data": None if data is None else _data_text(data), "revision": revision,
               "updated_at": updated_at, "deleted_at": deleted_at}
    if collection is SAVED_SONGS:
        columns["list"] = None if data is None else data["list"]
        columns["song_id"] = None if data is None else _song_id(data)
    if row_id is None:
        names = ["uid", *columns]
        conn.execute(f"INSERT INTO {collection.table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                     (uid, *columns.values()))
    else:
        conn.execute(f"UPDATE {collection.table} SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ?",
                     (*columns.values(), row_id))


def store_item(conn, collection: Collection, uid: str, item: dict, base_revision: int) -> tuple[bool, dict]:
    """Create or replace an item (`item` from parse_item()). Returns
    (applied, the stored item as JSON) - not applied = a conflict the stored
    one won."""
    with _transaction(conn):
        row = _row_by_uid(conn, collection, uid)
        if row is not None and not playlist_sync._wins(row, base_revision, item["updated_at"]):
            return False, item_json(row)
        _write(conn, collection, None if row is None else row["id"], uid, item["data"],
               next_revision(conn, collection), item["updated_at"], None)
        return True, item_json(_row_by_uid(conn, collection, uid))


def delete_item(conn, collection: Collection, uid: str, base_revision: int, deleted_at: int) -> tuple[bool, dict]:
    """Delete an item everywhere, keeping the row as a marker - also for a
    uid never seen, so the other phones drop it. Returns (applied, the
    stored item as JSON); already deleted counts as done, a conflict the
    stored item won is (False, it)."""
    with _transaction(conn):
        row = _row_by_uid(conn, collection, uid)
        if row is not None:
            if row["deleted_at"] is not None:
                return True, item_json(row)
            if not playlist_sync._wins(row, base_revision, deleted_at):
                return False, item_json(row)
        _write(conn, collection, None if row is None else row["id"], uid, None,
               next_revision(conn, collection), deleted_at, deleted_at)
        return True, item_json(_row_by_uid(conn, collection, uid))


def follow_song_merges(conn, changes) -> int:
    """Point every live saved song whose song_id was merged into another
    song at the song it ended up in (following chains), each under a new
    revision (updated_at untouched), so the phones fetch it. `changes` is
    the whole song_id_changes log as (id, old_song_id, new_song_id). A
    deleted song's id is left as it is: the item still has its title and
    video, and a deleted id is never given to another song.

    Replays the whole log, not a cursor's worth: ids are never reused, so
    following a merge twice changes nothing, and an item the server stored
    from a phone's older music.db is caught up too. Inside the caller's
    transaction; nothing happens while this user.db hasn't got the table.
    Returns how many items changed."""
    if not playlist_sync.is_migrated(conn, REQUIRED_MIGRATION):
        return 0
    moved = {old: new for _, old, new in changes}
    if not any(new is not None for new in moved.values()):
        return 0
    rows = conn.execute("SELECT id, song_id, data FROM saved_songs "
                        "WHERE deleted_at IS NULL AND song_id IS NOT NULL").fetchall()
    changed = 0
    for row_id, old_id, data_text in rows:
        song_id = playlist_sync.follow_song_id_changes(old_id, moved, keep_deleted=True)
        if song_id is None or song_id == old_id:
            continue
        data = json.loads(data_text)
        data["song"]["song_id"] = song_id
        conn.execute("UPDATE saved_songs SET song_id = ?, data = ?, revision = ? WHERE id = ?",
                     (song_id, _data_text(data), next_revision(conn, SAVED_SONGS), row_id))
        changed += 1
    return changed
