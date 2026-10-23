"""
Changes to global moods (tag.db, kind 'mood') sent from the phone by users
the owner allowed to make them (permission "edit_global_moods"). Nobody
reviews them - the permission is the trust - but every one is recorded, so
the owner can see who added or removed which song in which mood, and undo it
by hand (docs/runbooks/database.md -> "Global moods from the phone").

The original tag.db is on the system disk and main.py may have it open, so
the sharing server never writes it. Instead, in Settings.global_mood_changes_dir
on the data drive:

    pending/<received>_<id>.json  one change, written by the server
                                  (dot-named temp file, then os.replace)
    history.jsonl                 every change applied, one JSON per line,
                                  appended by MusicDatabase

MusicDatabase (utils/database/moods.apply_global_mood_changes()) applies the
pending files to tag.db in the order they arrived - at startup, before every
copy for the phones, and when the menu shows global moods - then appends them
to history.jsonl and deletes them. The other phones get the change with the
next copy of tag.db (the daily one at startup, or Settings -> Server ->
"Submit database for sharing").

A record: {"id", "received_at" (Unix ms, server clock), "user", "action"
("add" / "remove"), "mood_id", "mood_name", "song_id" (resolved against the
shared music.db), "requested_song_id" (as the phone sent it),
"song_ids_checked_to"}; history adds "applied_at" (Unix ms), "result"
(RESULT_*) and the final "song_id".

Standard-library only: the sharing server imports it.
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

PENDING_SUBDIR = "pending"
HISTORY_FILENAME = "history.jsonl"
ACTION_ADD = "add"
ACTION_REMOVE = "remove"
ACTIONS = (ACTION_ADD, ACTION_REMOVE)

# What applying a change did
RESULT_ADDED = "added"
RESULT_REMOVED = "removed"
RESULT_UNCHANGED = "unchanged"  # the song was already in (add) / not in (remove) the mood
RESULT_MOOD_GONE = "mood_gone"  # deleted (or made a marker) before the change was applied
RESULT_SONG_GONE = "song_gone"  # deleted from music.db before the change was applied

_TMP_PREFIX = "."

# received_at only ever grows within the process (the threaded server may get
# two changes in one millisecond), so the file names keep the arrival order
# instead of leaving it to the random uuid after them
_received_lock = threading.Lock()
_last_received_at = 0


def _next_received_at() -> int:
    global _last_received_at
    with _received_lock:
        _last_received_at = max(int(time.time() * 1000), _last_received_at + 1)
        return _last_received_at


def record_change(changes_dir, user: str, action: str, mood_id: int, mood_name: str,
                  song_id: int, requested_song_id: int, song_ids_checked_to: int) -> dict:
    """Store one change in pending/ for MusicDatabase to apply. Returns the record."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    received_at = _next_received_at()
    record = {"id": str(uuid.uuid4()), "received_at": received_at, "user": user, "action": action,
              "mood_id": mood_id, "mood_name": mood_name, "song_id": song_id,
              "requested_song_id": requested_song_id, "song_ids_checked_to": song_ids_checked_to}
    pending = Path(changes_dir) / PENDING_SUBDIR
    pending.mkdir(parents=True, exist_ok=True)
    # Zero-padded so the file names sort in the order the changes arrived
    name = f"{received_at:015d}_{record['id']}.json"
    tmp = pending / f"{_TMP_PREFIX}{name}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, pending / name)
    return record


def pending_changes(changes_dir) -> list[tuple[Path, dict]]:
    """(file, record) of every pending change, oldest first. An unreadable
    file is skipped (and stays)."""
    pending = Path(changes_dir) / PENDING_SUBDIR
    try:
        files = sorted(p for p in pending.iterdir() if p.suffix == ".json" and not p.name.startswith(_TMP_PREFIX))
    except OSError:
        return []
    changes = []
    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            changes.append((path, record))
    return changes


def append_history(changes_dir, records: list) -> None:
    if not records:
        return
    path = Path(changes_dir) / HISTORY_FILENAME
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_history(changes_dir) -> list[dict]:
    """Every applied change, oldest first; a damaged line is skipped."""
    try:
        lines = (Path(changes_dir) / HISTORY_FILENAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def global_mood_name(tag_db_path, mood_id: int) -> str | None:
    """The name of global mood `mood_id` in this tag.db (the shared copy the
    phone downloads), or None when there's no such mood - unknown, a marker,
    or a copy from before tags.kind. Raises sqlite3.Error when the copy can't
    be read (missing, locked, damaged): the server answers 503 then, not "no
    such mood", or the phone would drop a mood that still exists."""
    conn = sqlite3.connect(f"file:{tag_db_path}?mode=ro", uri=True, timeout=15)
    try:
        if "kind" not in {row[1] for row in conn.execute("PRAGMA table_info(tags)")}:
            return None
        row = conn.execute("SELECT name FROM tags WHERE id = ? AND kind = 'mood'", (mood_id,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None
