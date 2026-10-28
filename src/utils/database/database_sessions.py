from utils.database.music_db_manager import get_music_session
from utils.database.tag_db_manager import get_tag_session
from sqlalchemy.orm import Session
from typing import Tuple, Optional
from contextlib import contextmanager
import threading
from utils.database.song_references import apply_song_id_changes

def open_database_sessions() -> Tuple[Session, Session]:
    music_session = get_music_session()
    tag_session = get_tag_session()
    return music_session, tag_session

    

_GLOBAL_DATABASE_SESSION: Optional[Tuple[Session, Session]] = None
_GLOBAL_DB_LOCK = threading.Lock()

def set_global_database_sessions(sessions: Tuple[Session, Session]) -> None:
    global _GLOBAL_DATABASE_SESSION
    with _GLOBAL_DB_LOCK:
        _GLOBAL_DATABASE_SESSION = sessions

def get_global_database_sessions() -> Optional[Tuple[Session, Session]]:
    return _GLOBAL_DATABASE_SESSION

def require_global_database_sessions() -> Tuple[Session, Session]:
    """The menu's (music, tag) sessions, for code that only runs once they're
    open - a clear error instead of unpacking None (TypeError)."""
    sessions = _GLOBAL_DATABASE_SESSION
    if sessions is None:
        raise RuntimeError("The database sessions aren't open - call open_and_set_global_database_sessions() first")
    return sessions

@contextmanager
def tag_session_for(tag_session: Optional[Session] = None):
    """`tag_session`, else the global tag session if one is open (a second
    connection could wait on its lock), else a session of its own, closed
    on exit. Committing stays with the caller."""
    if tag_session is None:
        sessions = _GLOBAL_DATABASE_SESSION
        tag_session = sessions[1] if sessions else None
    if tag_session is not None:
        yield tag_session
        return
    own = get_tag_session()
    try:
        yield own
    finally:
        own.close()

def open_and_set_global_database_sessions() -> Tuple[Session, Session]:
    global _GLOBAL_DATABASE_SESSION
    with _GLOBAL_DB_LOCK:
        if _GLOBAL_DATABASE_SESSION is None:
            _GLOBAL_DATABASE_SESSION = open_database_sessions()
        return _GLOBAL_DATABASE_SESSION
    
class DatabaseCommitError(Exception):
    """Saving the menu's changes failed; they were rolled back, so the
    sessions keep working (no PendingRollbackError until a restart)."""


def _commit(session: Session) -> None:
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise DatabaseCommitError(f"Saving to the database failed, the unsaved changes were undone: {exc}") from exc


def close_global_database_sessions(commit: bool = True) -> None:
    """Commit (or, with commit=False, roll back) and close both global
    sessions. They are closed and unset even when the commit fails - the
    DatabaseCommitError still reaches the caller - so no connection or
    half-done change is left behind."""
    global _GLOBAL_DATABASE_SESSION
    with _GLOBAL_DB_LOCK:
        if _GLOBAL_DATABASE_SESSION is None:
            return
        music_session, tag_session = _GLOBAL_DATABASE_SESSION
        _GLOBAL_DATABASE_SESSION = None
        try:
            if commit:
                _commit(music_session)
                _commit(tag_session)
                apply_song_id_changes(tag_session=tag_session)
            else:
                music_session.rollback()
                tag_session.rollback()
        finally:
            music_session.close()
            tag_session.close()

def submit_global_database_session() -> None:
    """Commit both global sessions; DatabaseCommitError (after a rollback) when that fails."""
    global _GLOBAL_DATABASE_SESSION
    with _GLOBAL_DB_LOCK:
        if _GLOBAL_DATABASE_SESSION is None:
            return
        music_session, tag_session = _GLOBAL_DATABASE_SESSION
        _commit(music_session)
        _commit(tag_session)
        # Deleted/merged songs: their tags and users' playlist entries follow
        apply_song_id_changes(tag_session=tag_session)
