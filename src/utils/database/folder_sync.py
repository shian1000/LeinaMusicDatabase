"""
Syncing the folders of a user's Biblioteka (on the phone: folders holding
playlists and moods) between their user.db and their phones, with the
sharing server as the hub - the playlist rules of playlist_sync.py, applied
to folders (docs/runbooks/database.md -> "Folders on the phone"; the app's
docs/handoffs/musicdatabase-folders.md):

- The unit is a whole folder (its name and items), identified everywhere by
  its `uid`; every change gives it the next number of the user's
  `folder_revision` counter (separate from playlists' and moods').
- Conflicts as for playlists: a change based on the stored revision is
  applied; one based on an older revision only if its `updated_at` is later,
  otherwise the stored folder is answered back (409). A PUT for a folder
  deleted earlier than its `updated_at` brings it back.
- `items` are stored exactly as a phone sent them, in its order, never
  checked against the playlists and moods they name: a folder may name a
  playlist only one phone has, or one the other phone hasn't received yet.
  Unknown item types are kept too, for later app versions.
- A deleted folder keeps its row without a name or items. Nothing is
  archived: a folder holds only references.
- When the server merges a playlist (same source on two phones) or a mood
  (same name) into another, replace_refs() points the user's folders at the
  kept one, so the phones needn't heal the refs themselves.

Folders don't nest and an item is in one folder at most - the phone enforces
that, the server doesn't. Standard-library only, like playlist_sync.py
(which it builds on): the sharing server imports it.
"""

import json
import re

try:
    from . import playlist_sync  # imported as utils.database.folder_sync (the app, tests)
except ImportError:
    import playlist_sync  # run by the sharing server as a script (its folder is on sys.path)

REQUIRED_MIGRATION = "0008_folders.sql"
# Folders made on a phone before syncing keep a short base-36 id, so not the
# playlists' 8-character minimum
UID_RE = re.compile(r"^[0-9A-Za-z-]{1,64}$")

BadFolderError = playlist_sync.BadPlaylistError
NotMigratedError = playlist_sync.NotMigratedError
_transaction = playlist_sync._transaction
_int = playlist_sync._int


def connect(db_path):
    """A connection to a user.db with the folders table. Raises
    NotMigratedError."""
    return playlist_sync.connect(db_path, REQUIRED_MIGRATION)


# --------------------
# Revisions
# --------------------

def current_revision(conn) -> int:
    row = conn.execute("SELECT last_revision FROM folder_revision WHERE id = 1").fetchone()
    return row[0] if row else 0


def next_revision(conn) -> int:
    """The next number of the user's folder counter. Inside a transaction."""
    if conn.execute("UPDATE folder_revision SET last_revision = last_revision + 1 WHERE id = 1").rowcount == 0:
        conn.execute("INSERT INTO folder_revision (id, last_revision) "
                     "SELECT 1, COALESCE(MAX(revision), 0) + 1 FROM folders")
    return current_revision(conn)


# --------------------
# Reading
# --------------------

def folder_json(row) -> dict:
    """The phone's JSON of one folders row; a deleted one has no name or items."""
    if row["deleted_at"] is not None:
        return {"uid": row["uid"], "revision": row["revision"], "updated_at": row["updated_at"], "deleted": True}
    return {"uid": row["uid"], "revision": row["revision"], "name": row["name"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "deleted": False,
            "items": json.loads(row["items"] or "[]")}


def _row_by_uid(conn, uid: str):
    return conn.execute("SELECT * FROM folders WHERE uid = ?", (uid,)).fetchone()


def changes_since(conn, since: int) -> dict:
    """GET /users/<name>/folders?since=N: every folder, deleted ones
    included, changed after revision N, and the counter to ask with next time."""
    conn.execute("BEGIN")
    try:
        rows = conn.execute("SELECT * FROM folders WHERE revision > ? ORDER BY revision", (since,)).fetchall()
        return {"revision": current_revision(conn), "folders": [folder_json(r) for r in rows]}
    finally:
        conn.execute("COMMIT")


def live_folders(conn) -> list[dict]:
    """Every live folder as the phone's JSON, by name - for the menu."""
    return [folder_json(r) for r in conn.execute(
        "SELECT * FROM folders WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE")]


# --------------------
# Checking a phone's body
# --------------------

def parse_folder(body) -> dict:
    """A PUT body checked and reduced to what's stored. Each item must be a
    JSON object with a string "type"; everything else in it is kept as
    sent. Raises BadFolderError."""
    if not isinstance(body, dict):
        raise BadFolderError("expected a JSON object")
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadFolderError("name: expected a non-empty string")
    items = body.get("items")
    if not isinstance(items, list):
        raise BadFolderError("items: expected a list")
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise BadFolderError(f"items[{i}]: expected an object with a string \"type\"")
    return {
        "name": name,
        "created_at": _int(body.get("created_at"), "created_at", optional=True),
        "updated_at": _int(body.get("updated_at"), "updated_at"),
        "items": items,
    }


def _items_text(items: list) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


# --------------------
# Writing
# --------------------

def store_folder(conn, uid: str, folder: dict, base_revision: int) -> tuple[bool, dict]:
    """Create or replace a folder (`folder` from parse_folder()). Returns
    (applied, the stored folder as JSON) - not applied = a conflict the
    stored one won."""
    with _transaction(conn):
        row = _row_by_uid(conn, uid)
        if row is not None and not playlist_sync._wins(row, base_revision, folder["updated_at"]):
            return False, folder_json(row)
        revision = next_revision(conn)
        if row is None:
            created_at = folder["created_at"] if folder["created_at"] is not None else folder["updated_at"]
            conn.execute("INSERT INTO folders (uid, name, items, revision, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (uid, folder["name"], _items_text(folder["items"]), revision, created_at,
                          folder["updated_at"]))
        else:
            conn.execute("UPDATE folders SET name = ?, items = ?, revision = ?, updated_at = ?, deleted_at = NULL, "
                         "created_at = COALESCE(?, created_at) WHERE id = ?",
                         (folder["name"], _items_text(folder["items"]), revision, folder["updated_at"],
                          folder["created_at"], row["id"]))
        return True, folder_json(_row_by_uid(conn, uid))


def delete_folder(conn, uid: str, base_revision: int, deleted_at: int) -> tuple[bool, dict]:
    """Delete a folder everywhere, keeping the row as a marker. Returns
    (applied, the stored folder as JSON). Unknown or already deleted counts
    as done; a conflict the stored folder won is (False, it)."""
    with _transaction(conn):
        row = _row_by_uid(conn, uid)
        if row is None:
            return True, {"uid": uid, "revision": 0, "updated_at": deleted_at, "deleted": True}
        if row["deleted_at"] is not None:
            return True, folder_json(row)
        if not playlist_sync._wins(row, base_revision, deleted_at):
            return False, folder_json(row)
        conn.execute("UPDATE folders SET name = NULL, items = NULL, deleted_at = ?, updated_at = ?, revision = ? "
                     "WHERE id = ?", (deleted_at, deleted_at, next_revision(conn), row["id"]))
        return True, folder_json(_row_by_uid(conn, uid))


def replace_refs(conn, item_type: str, old_uid: str, new_uid: str) -> None:
    """Point every live folder's `item_type` item naming `old_uid` at
    `new_uid` instead (a playlist or mood the server merged into another),
    each changed folder under a new revision. An item that would then repeat
    one already in the folder is dropped. Inside the caller's transaction;
    nothing happens while this user.db has no folders table yet."""
    if old_uid == new_uid or not playlist_sync.is_migrated(conn, REQUIRED_MIGRATION):
        return
    rows = conn.execute("SELECT id, items FROM folders WHERE deleted_at IS NULL AND items LIKE ?",
                        (f"%{old_uid}%",)).fetchall()
    for row in rows:
        items = json.loads(row["items"] or "[]")
        if not any(_names(item, item_type, old_uid) for item in items):
            continue
        kept, has_new = [], False
        for item in items:
            if _names(item, item_type, old_uid):
                item = {**item, "uid": new_uid}
            if _names(item, item_type, new_uid):
                if has_new:
                    continue
                has_new = True
            kept.append(item)
        conn.execute("UPDATE folders SET items = ?, revision = ? WHERE id = ?",
                     (_items_text(kept), next_revision(conn), row["id"]))


def _names(item, item_type: str, uid: str) -> bool:
    return isinstance(item, dict) and item.get("type") == item_type and item.get("uid") == uid
