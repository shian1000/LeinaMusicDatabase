"""
Moods ("Nastroje" on the phone): sets of songs with the same feel, without
order. Built on the tags (docs/data-model.md):

- Global moods are tags in tag.db with kind 'mood'; every user sees them.
  Created, renamed and deleted only here (main menu -> Users -> Moods).
  Users with the "edit_global_moods" permission may add and remove songs
  from the phone; the sharing server records those changes and
  apply_global_mood_changes() puts them into tag.db (global_mood_log.py).
- A user's own moods are private tags in their user.db, synced with their
  phones (mood_sync.py). The owner sees every user's moods here without
  logging in as them, and can promote one to a global mood: its songs are
  moved into the global mood of that name (made if missing, merged if it
  exists) and the user's mood is deleted with `promoted_to`.

Only songs in music.db can be in a mood: every member is a songs.id.
tags.name is unique per file, so a global mood can't take the name of a
marker such as "spellchecked" - that's refused, never merged.

The caller commits the tag session (AGENTS.md -> sessions). Only
promote_user_mood() and apply_global_mood_changes() commit themselves: each
writes a file outside tag.db (a user.db, the phones' change files) right
after, and that write must follow a saved tag.db, never a rolled-back one.
"""

from contextlib import closing
from pathlib import Path

from sqlalchemy import func

from config.constants import TAG_KIND_MOOD, USER_DB_FILENAME
from settings import Settings
from utils.common.debug import mlog, slog
from utils.database import global_mood_log, mood_sync, playlist_sync
from utils.database.database_sessions import tag_session_for
from utils.database.tag_db_manager import SongTag, Tag
from utils.database.users import user_dir, users_available

GLOBAL_MOOD_CHANGES_DIR = Path(str(Settings.global_mood_changes_dir)) if Settings.global_mood_changes_dir else None


class MoodError(ValueError):
    """A mood operation that can't be done (name taken, mood gone, ...)."""


# --------------------
# Global moods (tag.db)
# --------------------

def list_global_moods(tag_session) -> list[tuple[int, str, int]]:
    """(id, name, song count) of every global mood, by name. Markers never."""
    counts = (tag_session.query(SongTag.tag_id, func.count(SongTag.id).label("n"))
              .group_by(SongTag.tag_id).subquery())
    rows = (tag_session.query(Tag.id, Tag.name, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.tag_id == Tag.id)
            .filter(Tag.kind == TAG_KIND_MOOD).order_by(Tag.name).all())
    return [tuple(r) for r in rows]


def global_mood_song_ids(tag_session, mood_id: int) -> list[int]:
    return [r[0] for r in tag_session.query(SongTag.song_id).filter(SongTag.tag_id == mood_id)
            .order_by(SongTag.song_id)]


def _global_mood(tag_session, mood_id: int) -> Tag:
    tag = tag_session.get(Tag, mood_id)
    if tag is None or tag.kind != TAG_KIND_MOOD:
        raise MoodError(f"There's no global mood #{mood_id}.")
    return tag


def _check_free_name(tag_session, name: str, exclude_id: int | None = None) -> str:
    name = (name or "").strip()
    if not name:
        raise MoodError("A mood needs a name.")
    taken = tag_session.query(Tag).filter(Tag.name == name).first()
    if taken is not None and taken.id != exclude_id:
        if taken.kind == TAG_KIND_MOOD:
            raise MoodError(f"There's already a global mood '{name}'.")
        raise MoodError(f"'{name}' is the name of a technical tag ({taken.kind}) - choose another name.")
    return name


def create_global_mood(tag_session, name: str) -> int:
    tag = Tag(name=_check_free_name(tag_session, name), kind=TAG_KIND_MOOD)
    tag_session.add(tag)
    tag_session.flush()
    return tag.id


def rename_global_mood(tag_session, mood_id: int, new_name: str) -> None:
    tag = _global_mood(tag_session, mood_id)
    tag.name = _check_free_name(tag_session, new_name, exclude_id=mood_id)


def delete_global_mood(tag_session, mood_id: int) -> int:
    """Delete a global mood and its song links. Returns how many songs it had."""
    tag = _global_mood(tag_session, mood_id)
    removed = tag_session.query(SongTag).filter(SongTag.tag_id == mood_id).delete(synchronize_session=False)
    tag_session.delete(tag)
    return removed


def _add_songs(tag_session, mood_id: int, song_ids) -> int:
    """Link songs to a tag, skipping ones it has. Returns how many were new."""
    have = set(global_mood_song_ids(tag_session, mood_id))
    new = sorted(set(song_ids) - have)
    tag_session.add_all([SongTag(song_id=s, tag_id=mood_id) for s in new])
    return len(new)


def add_song_to_global_mood(tag_session, mood_id: int, song_id: int) -> bool:
    """True if the song wasn't in the mood yet."""
    _global_mood(tag_session, mood_id)
    added = _add_songs(tag_session, mood_id, [song_id])
    return bool(added)


def remove_song_from_global_mood(tag_session, mood_id: int, song_id: int) -> bool:
    """True if the song was in the mood."""
    _global_mood(tag_session, mood_id)
    removed = (tag_session.query(SongTag).filter(SongTag.tag_id == mood_id, SongTag.song_id == song_id)
               .delete(synchronize_session=False))
    return bool(removed)


# --------------------
# Users' moods (read here without logging in - the owner's decision 2)
# --------------------

def _user_db(username: str) -> Path:
    return user_dir(username) / USER_DB_FILENAME


def list_user_moods(username: str) -> list[tuple[int, str, str, int]]:
    """(id, uid, name, song count) of a user's live moods. Raises
    mood_sync.NotMigratedError while their user.db lacks the mood columns."""
    with closing(mood_sync.connect(_user_db(username))) as conn:
        return mood_sync.live_moods(conn)


def user_mood_song_ids(username: str, mood_id: int) -> list[int]:
    with closing(mood_sync.connect(_user_db(username))) as conn:
        return mood_sync.song_ids(conn, mood_id)


def promote_user_mood(tag_session, username: str, mood_id: int) -> tuple[int, str, bool, int]:
    """Move a user's mood into the global mood of the same name: made if
    there's none, merged (union of the songs) if there is one. Refused when
    the name is a marker's. The user's mood is then deleted with promoted_to,
    so their phones switch to the global one. Returns (global id, name,
    whether it merged into an existing mood, songs added to it).

    Commits tag_session (with whatever else it holds) before the user.db
    step: if that step fails, the global mood already has the songs and
    promoting again only repeats the union."""
    folder = user_dir(username)
    with closing(mood_sync.connect(folder / USER_DB_FILENAME)) as conn:
        row = conn.execute("SELECT id, name, kind, deleted_at FROM tags WHERE id = ?", (mood_id,)).fetchone()
        if row is None or row["kind"] != TAG_KIND_MOOD or row["deleted_at"] is not None:
            raise MoodError(f"{username} has no mood #{mood_id}.")
        name, song_ids = row["name"], mood_sync.song_ids(conn, mood_id)

    existing = tag_session.query(Tag).filter(Tag.name == name).first()
    if existing is not None and existing.kind != TAG_KIND_MOOD:
        raise MoodError(f"'{name}' is the name of a technical tag ({existing.kind}) in tag.db - "
                        "rename the user's mood or that tag first.")
    merged = existing is not None
    if existing is None:
        existing = Tag(name=name, kind=TAG_KIND_MOOD)
        tag_session.add(existing)
        tag_session.flush()
    added = _add_songs(tag_session, existing.id, song_ids)
    tag_session.commit()

    with closing(mood_sync.connect(folder / USER_DB_FILENAME)) as conn:
        mood_sync.mark_promoted(conn, folder, mood_id, existing.id, existing.name)
    return existing.id, existing.name, merged, added


# --------------------
# Changes to global moods from the phone (global_mood_log.py)
# --------------------

def _resolve(song_id: int, checked_to: int, changes: list) -> int | None:
    """Follow the song through the log entries after `checked_to` (what the
    server resolved it against): merged -> the kept song, deleted -> None."""
    for change_id, old_id, new_id in changes:
        if change_id > checked_to and old_id == song_id:
            song_id = new_id
            if song_id is None:
                return None
    return song_id


def apply_global_mood_changes(tag_session=None, changes_dir: Path | None = None) -> list[dict]:
    """Apply every pending change to tag.db in the order they arrived, then
    append them to history.jsonl and delete them. Returns the history
    records written ([] when there's nothing, or the data drive isn't mounted).
    Commits the tag session before the files go, so a change is never
    deleted unsaved; a crash between the commit and deleting the files
    re-applies them next time, which ends in the same state."""
    from utils.database.song_references import existing_song_ids, load_changes

    changes_dir = changes_dir or GLOBAL_MOOD_CHANGES_DIR
    if changes_dir is None or changes_dir == GLOBAL_MOOD_CHANGES_DIR and not users_available():
        return []
    pending = global_mood_log.pending_changes(changes_dir)
    if not pending:
        return []
    log = load_changes()
    existing = existing_song_ids()
    applied_at = playlist_sync.now_ms()
    history = []
    with tag_session_for(tag_session) as session:
        for _, record in pending:
            result = dict(record)
            song_id = _resolve(record.get("song_id"), record.get("song_ids_checked_to") or 0, log)
            mood_id = record.get("mood_id")
            mood = session.get(Tag, mood_id) if isinstance(mood_id, int) else None
            if mood is None or mood.kind != TAG_KIND_MOOD:
                result["result"] = global_mood_log.RESULT_MOOD_GONE
            elif song_id is None or song_id not in existing:
                result["result"] = global_mood_log.RESULT_SONG_GONE
            elif record.get("action") == global_mood_log.ACTION_ADD:
                result["result"] = (global_mood_log.RESULT_ADDED if _add_songs(session, mood.id, [song_id])
                                    else global_mood_log.RESULT_UNCHANGED)
                session.flush()
            else:
                removed = (session.query(SongTag).filter(SongTag.tag_id == mood.id, SongTag.song_id == song_id)
                           .delete(synchronize_session=False))
                result["result"] = global_mood_log.RESULT_REMOVED if removed else global_mood_log.RESULT_UNCHANGED
            result["song_id"] = song_id
            result["applied_at"] = applied_at
            history.append(result)
        session.commit()
    global_mood_log.append_history(changes_dir, history)
    for path, _ in pending:
        path.unlink(missing_ok=True)
    mlog(f"Applied {len(history)} change(s) to global moods from Leina Radio")
    return history


def apply_global_mood_changes_quietly() -> list[dict]:
    """apply_global_mood_changes() for startup and before sharing: a failure
    is logged and the changes stay pending for the next try."""
    try:
        return apply_global_mood_changes()
    except Exception as exc:
        slog(f"Applying global mood changes failed (non-fatal): {exc}", priority=3)
        return []


def global_mood_history(changes_dir: Path | None = None) -> list[dict]:
    """Every applied change from the phones, newest first."""
    changes_dir = changes_dir or GLOBAL_MOOD_CHANGES_DIR
    return list(reversed(global_mood_log.read_history(changes_dir))) if changes_dir else []


def pending_global_mood_changes(changes_dir: Path | None = None) -> list[dict]:
    changes_dir = changes_dir or GLOBAL_MOOD_CHANGES_DIR
    return [r for _, r in global_mood_log.pending_changes(changes_dir)] if changes_dir else []

