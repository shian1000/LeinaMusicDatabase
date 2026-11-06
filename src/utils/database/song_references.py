"""
Keeping references to songs.id valid in the files SQLite can't enforce
them across: tag.db's song_tags (global tags), every user's user.db
(playlist tracks, private tags, saved songs) and files.db (the music library index).

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
   unplugged data drive is simply caught up later, which is why it's a log with
   cursors and not an update made on the spot.
   Leina Radio's saved songs (saved_sync.py) follow merges only, from the
   whole log rather than the cursor; a deleted song's id stays on them.
   In a user.db every playlist whose tracks changed, and every mood whose
   songs changed, gets a new revision (playlist_sync / mood_sync
   .mark_changed()), so the phones fetch the new ids. Tracks and mood songs
   the sharing server stored were resolved against the *shared copy* of
   music.db, which may be a day behind: such a playlist or mood carries
   song_ids_checked_to, and the log entries between that and the cursor are
   replayed for it alone first.
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

import sqlite3
import weakref
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import event, func, select, text

from config.constants import FILE_MATCH_AUTO, TAG_KIND_MOOD
from musicdatabase_core import song_id_replay
from utils.common.debug import flog, mlog
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


def existing_song_ids() -> set:
    with _music_engine().connect() as conn:
        return {r[0] for r in conn.execute(select(Song.id))}


def _raw(session):
    session.flush()  # the pending changes the statements below must see
    return session.connection().connection.driver_connection


def _cursor(session):
    return song_id_replay.cursor(_raw(session))


def set_cursor(session, change_id: int) -> None:
    song_id_replay.set_cursor(_raw(session), change_id)


def _replay(session, changes: list, **what) -> int:
    """song_id_replay.replay() in this session's transaction. Caller commits."""
    return song_id_replay.replay(_raw(session), changes, auto_match_source=FILE_MATCH_AUTO, **what)


def _replay_user_db(session, changes: list) -> int:
    return song_id_replay.replay_user_db(_raw(session), changes)


def _replay_tag_db(tag_session, changes: list) -> int:
    first_time = _cursor(tag_session) is None
    touched = _replay(tag_session, changes, has_playlists=False)
    if first_time:
        existing = existing_song_ids()
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
    lock - else a session of its own. Returns {file label: rows changed}.
    A file that fails (a locked user.db, a broken files.db) is rolled back,
    reported and left out; the others are still updated, and the next call
    tries again even without `force` (every replay is idempotent - each file
    has its own cursor)."""
    global _changes_pending
    if not (force or _changes_pending):
        return {}
    _changes_pending = False
    changes = load_changes()
    results = {}
    failed = []

    def replay(label, session, apply):
        try:
            results[label] = apply(session)
            session.commit()
        except Exception as exc:
            session.rollback()
            failed.append(label)
            flog(f"Song id changes: {label} not updated: {exc!r}")
            print(f"Warning: {label} couldn't follow the merged/deleted songs ({exc}) - tried again later.")

    # Imported here: database_sessions imports this module
    from utils.database.database_sessions import tag_session_for
    with tag_session_for(tag_session) as session:
        replay("tag.db", session, lambda session: _replay_tag_db(session, changes))

    if FILES_DB.exists():
        from utils.database.files_index import files_db_session
        with files_db_session(FILES_DB) as session:
            replay("files.db", session, lambda session: _replay(session, changes, has_tags=False, has_files=True))

    for username, path in user_db_files().items():
        with user_db_session(path) as session:
            replay(f"user {username}", session, lambda session: _replay_user_db(session, changes))
    if failed:
        _changes_pending = True
    return results


# --------------------
# Where a song is used
# --------------------

@dataclass
class SongUses:
    """Where a song's id is used outside music.db: users' playlists (each
    counted once), moods (users' and global), library files."""
    playlists: int = 0
    moods: int = 0
    files: int = 0

    def __bool__(self) -> bool:
        return bool(self.playlists or self.moods or self.files)


def _count(path, query: str, song_id: int) -> int:
    """`query`'s count (parameters: the song id, then TAG_KIND_MOOD if it asks)
    in the SQLite file `path`, read-only; 0 when it can't be read."""
    try:
        conn = sqlite3.connect(f"{Path(path).absolute().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            params = (song_id, TAG_KIND_MOOD)[:query.count("?")]
            return conn.execute(query, params).fetchone()[0] or 0
        finally:
            conn.close()
    except sqlite3.Error as exc:
        flog(f"Counting the uses of song {song_id} in {Path(path).name} failed: {exc}")
        return 0


def song_uses(song_id: int, user_dbs=None, tag_db=None, files_db=None) -> SongUses:
    """What deleting song `song_id` would leave pointing at nothing:
    playlist tracks keep their title with song_id NULL, mood entries and
    library matches go. Merging instead (song_duplicates.merge_songs())
    moves them all to the kept song. Read-only; the paths default to every
    user's user.db, tag.db and files.db."""
    uses = SongUses()
    for path in (user_dbs if user_dbs is not None else user_db_files().values()):
        uses.playlists += _count(path, "SELECT COUNT(DISTINCT t.playlist_id) FROM playlist_tracks t "
                                       "JOIN playlists p ON p.id = t.playlist_id "
                                       "WHERE t.song_id = ? AND p.deleted_at IS NULL", song_id)
        uses.moods += _count(path, "SELECT COUNT(*) FROM song_tags st JOIN tags t ON t.id = st.tag_id "
                                   "WHERE st.song_id = ? AND t.kind = ? AND t.deleted_at IS NULL", song_id)
    tag_db = tag_db or DB_FILES["tag"]
    if Path(tag_db).exists():
        uses.moods += _count(tag_db, "SELECT COUNT(*) FROM song_tags st JOIN tags t ON t.id = st.tag_id "
                                     "WHERE st.song_id = ? AND t.kind = ?", song_id)
    files_db = files_db or FILES_DB
    if Path(files_db).exists():
        uses.files += _count(files_db, "SELECT COUNT(*) FROM files WHERE song_id = ?", song_id)
    return uses
