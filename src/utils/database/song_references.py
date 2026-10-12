"""
Keeping references to songs.id valid in the files SQLite can't enforce
them across: tag.db's song_tags (global tags), every user's user.db
(playlist tracks, private tags) and files.db (the music library index).

1. music.db logs every song id that stops existing in `song_id_changes`
   (migration 0009). A merge is logged by the code that merges
   (record_song_merge(): old id -> the kept song). A deletion is logged by
   itself: a before_flush listener on music sessions (register_song_reference_sync())
   adds a "deleted" entry for every Song the session deletes. Bulk
   query(...).delete() skips the ORM and must call record_song_deletions().
2. Each referencing file keeps its own cursor (`applied_song_changes`,
   tag migration 0002 / user migration 0003 / files migration 0001) and
   replays the log past it in apply_song_id_changes():
   - merged: tags move to the kept song (a tag it already has is dropped),
     playlist tracks and library files point at the kept song;
   - deleted: tags go; playlist tracks keep their title/artist/video with
     song_id NULL, so the playlist still shows them; library files lose
     their song too and the next scan tries to match them again.
   It runs at startup, on logging into a user database, and after commits
   that logged something (database_sessions.submit/close). A user.db on an
   unplugged T7 is simply caught up later, which is why it's a log with
   cursors and not an update made on the spot.
3. A deleted id is never given to a new song: the same listener numbers new
   songs above every id songs or the log have ever held. songs.id has no
   AUTOINCREMENT, so SQLite would otherwise reuse the id of the newest song
   after it's deleted - and every tag or playlist entry still pointing at it
   would land on an unrelated song. That also makes replaying the log twice
   harmless.

The first replay into tag.db also drops song_tags rows whose song no longer
exists - rows left over from before this log (143 on 2026-10-12, all
"spellchecked"/"album_checked" markers).
"""

import weakref

from sqlalchemy import event, func, select, text

from config.constants import FILE_MATCH_AUTO
from utils.common.debug import mlog
from utils.database.backup import DB_FILES
from utils.database.datatables import Song, SongIdChange
from utils.database.user_datatables import user_db_session
from utils.database.users import user_db_files

# Set when a committed transaction logged a change, so the frequent
# submit_global_database_session() only replays when there's something new
_changes_pending = False
_LOGGED_KEY = "song_id_changes_logged"
# Ids record_song_merge() logged in this transaction. Kept on the session,
# not looked up in session.new: deleting the song loads its relationships,
# which can autoflush the merge entry before the delete is flushed.
_MERGED_KEY = "song_ids_merged"
# The music library index; replayed only once a scan has created it
FILES_DB = DB_FILES["files"]


def record_song_merge(session, from_song_id: int, to_song_id: int) -> None:
    """Log that `from_song_id` was merged into `to_song_id`; call it in the
    same session that deletes the merged song. Caller commits."""
    session.add(SongIdChange(old_song_id=from_song_id, new_song_id=to_song_id))
    session.info.setdefault(_MERGED_KEY, set()).add(from_song_id)
    session.info[_LOGGED_KEY] = True


def record_song_deletions(session, song_ids) -> None:
    """Log deletions done with a bulk query(...).delete(), which the flush
    listener never sees. Caller commits."""
    for song_id in song_ids:
        session.add(SongIdChange(old_song_id=song_id, new_song_id=None))
    session.info[_LOGGED_KEY] = True


def _next_song_id(session) -> int:
    with session.no_autoflush:
        highest = max(
            session.execute(select(func.max(Song.id))).scalar() or 0,
            session.execute(select(func.max(SongIdChange.old_song_id))).scalar() or 0,
            max((c.old_song_id for c in session.new if isinstance(c, SongIdChange)), default=0),
        )
    return highest + 1


def _before_flush(session, flush_context, instances) -> None:
    logged = {c.old_song_id for c in session.new if isinstance(c, SongIdChange)}
    logged |= session.info.get(_MERGED_KEY, set())
    for obj in list(session.deleted):
        if isinstance(obj, Song) and obj.id is not None and obj.id not in logged:
            session.add(SongIdChange(old_song_id=obj.id, new_song_id=None))
            logged.add(obj.id)
    if logged:
        session.info[_LOGGED_KEY] = True

    new_songs = [o for o in session.new if isinstance(o, Song) and o.id is None]
    if new_songs:
        next_id = _next_song_id(session)
        for song in new_songs:
            song.id = next_id
            next_id += 1


def _after_commit(session) -> None:
    global _changes_pending
    session.info.pop(_MERGED_KEY, None)
    if session.info.pop(_LOGGED_KEY, False):
        _changes_pending = True


def _after_rollback(session) -> None:
    session.info.pop(_LOGGED_KEY, None)
    session.info.pop(_MERGED_KEY, None)


# Factories already registered. Not event.contains(): SQLAlchemy's registry
# is keyed by id(), so a new sessionmaker that reuses a collected one's
# address looked registered and silently never logged anything (seen in the
# tests, where every test makes its own factory).
_registered_factories = weakref.WeakSet()


def register_song_reference_sync(session_factory) -> None:
    """Log deletions and keep new song ids unique, for sessions made by
    `session_factory` (music.db's sessionmaker)."""
    if session_factory in _registered_factories:
        return
    for name, fn in (("before_flush", _before_flush), ("after_commit", _after_commit),
                     ("after_rollback", _after_rollback)):
        event.listen(session_factory, name, fn)
    _registered_factories.add(session_factory)


# --------------------
# Replaying the log
# --------------------

def _music_engine():
    from utils.database.music_db_manager import ENGINE
    return ENGINE


def load_changes() -> list:
    """The whole log, oldest first, as (id, old_song_id, new_song_id)."""
    with _music_engine().connect() as conn:
        return [tuple(r) for r in conn.execute(
            select(SongIdChange.id, SongIdChange.old_song_id, SongIdChange.new_song_id)
            .order_by(SongIdChange.id)
        )]


def latest_change_id() -> int:
    with _music_engine().connect() as conn:
        return conn.execute(select(func.max(SongIdChange.id))).scalar() or 0


def _existing_song_ids() -> set:
    with _music_engine().connect() as conn:
        return {r[0] for r in conn.execute(select(Song.id))}


def _cursor(session):
    return session.execute(text("SELECT last_change_id FROM applied_song_changes WHERE id = 1")).scalar()


def set_cursor(session, change_id: int) -> None:
    session.execute(
        text("INSERT INTO applied_song_changes (id, last_change_id) VALUES (1, :c) "
             "ON CONFLICT (id) DO UPDATE SET last_change_id = :c"),
        {"c": change_id},
    )


def _replay(session, changes: list, has_playlists: bool = False, has_tags: bool = True,
            has_files: bool = False) -> int:
    """Apply the log entries past this file's cursor. Returns how many rows
    changed. Caller commits."""
    last = _cursor(session) or 0
    touched = 0
    for change_id, old_id, new_id in changes:
        if change_id <= last:
            continue
        params = {"old": old_id, "new": new_id, "auto": FILE_MATCH_AUTO}
        if new_id is not None:
            if has_tags:
                touched += session.execute(
                    text("UPDATE OR IGNORE song_tags SET song_id = :new WHERE song_id = :old"), params).rowcount
                session.execute(text("DELETE FROM song_tags WHERE song_id = :old"), params)
            if has_playlists:
                touched += session.execute(
                    text("UPDATE playlist_tracks SET song_id = :new WHERE song_id = :old"), params).rowcount
            if has_files:
                touched += session.execute(
                    text("UPDATE files SET song_id = :new WHERE song_id = :old"), params).rowcount
        else:
            if has_tags:
                touched += session.execute(text("DELETE FROM song_tags WHERE song_id = :old"), params).rowcount
            if has_playlists:
                touched += session.execute(
                    text("UPDATE playlist_tracks SET song_id = NULL WHERE song_id = :old"), params).rowcount
            if has_files:
                # Even a hand-set match: its song is gone, so let a scan find another
                touched += session.execute(
                    text("UPDATE files SET song_id = NULL, match_source = :auto WHERE song_id = :old"),
                    params).rowcount
        last = change_id
    set_cursor(session, last)
    return touched


def _replay_tag_db(tag_session, changes: list) -> int:
    first_time = _cursor(tag_session) is None
    touched = _replay(tag_session, changes, has_playlists=False)
    if first_time:
        existing = _existing_song_ids()
        stale = [r[0] for r in tag_session.execute(text("SELECT DISTINCT song_id FROM song_tags"))
                 if r[0] not in existing]
        for song_id in stale:
            touched += tag_session.execute(
                text("DELETE FROM song_tags WHERE song_id = :old"), {"old": song_id}).rowcount
        if stale:
            mlog(f"tag.db: dropped tags of {len(stale)} song(s) that no longer exist")
    return touched


def apply_song_id_changes(tag_session=None, force: bool = False) -> dict:
    """Bring tag.db, files.db and every reachable user.db up to date with the log.
    Without `force` it does nothing unless a commit has logged something
    since the last run. Uses (and commits) `tag_session`, else the global
    tag session if one is open - a second connection could wait on its
    lock - else a session of its own. Returns {file label: rows changed}."""
    global _changes_pending
    if not (force or _changes_pending):
        return {}
    _changes_pending = False
    changes = load_changes()
    results = {}

    own_session = None
    if tag_session is None:
        from utils.database.database_sessions import get_global_database_sessions
        sessions = get_global_database_sessions()
        tag_session = sessions[1] if sessions else None
    if tag_session is None:
        from utils.database.tag_db_manager import get_tag_session
        tag_session = own_session = get_tag_session()
    try:
        results["tag.db"] = _replay_tag_db(tag_session, changes)
        tag_session.commit()
    finally:
        if own_session is not None:
            own_session.close()

    if FILES_DB.exists():
        from utils.database.files_index import files_db_session
        with files_db_session(FILES_DB) as session:
            results["files.db"] = _replay(session, changes, has_tags=False, has_files=True)
            session.commit()

    for username, path in user_db_files().items():
        with user_db_session(path) as session:
            results[f"user {username}"] = _replay(session, changes, has_playlists=True)
            session.commit()
    return results
