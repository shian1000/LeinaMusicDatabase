from utils.database.music_db_manager import get_music_session
from utils.database.tag_db_manager import get_tag_session
from sqlalchemy.orm import Session
from typing import Tuple, Optional
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
    global _GLOBAL_DATABASE_SESSION
    with _GLOBAL_DB_LOCK:
        if _GLOBAL_DATABASE_SESSION is None:
            return
        music_session, tag_session = _GLOBAL_DATABASE_SESSION
        if commit:
            _commit(music_session)
            _commit(tag_session)
            apply_song_id_changes(tag_session=tag_session)
            music_session.close()
            tag_session.close()
        _GLOBAL_DATABASE_SESSION = None

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
