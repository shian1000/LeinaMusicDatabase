from utils.database.music_db_manager import get_music_session
from utils.database.tag_db_manager import get_tag_session
from sqlalchemy.orm import Session
from typing import Tuple, Optional
import threading

def open_database_sessions() -> Tuple[Session, Session]:
    music_session = get_music_session()
    tag_session = get_tag_session()
    return music_session, tag_session


# def close_database_sessions(sessions: Tuple[Session, Session]):
#     music_session, tag_session = sessions
#     music_session.close()
#     tag_session.close()

# def submit_and_close_database_sessions(sessions: Tuple[Session, Session]):
#     music_session, tag_session = sessions
#     music_session.commit()
#     tag_session.commit()
#     music_session.close()
#     tag_session.close()

# def submit_database_sessions(sessions: Tuple[Session, Session]):
#     music_session, tag_session = sessions
#     music_session.commit()
#     tag_session.commit()

    

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
    
def close_global_database_sessions(commit: bool = True) -> None:
    global _GLOBAL_DATABASE_SESSION
    with _GLOBAL_DB_LOCK:
        if _GLOBAL_DATABASE_SESSION is None:
            return
        music_session, tag_session = _GLOBAL_DATABASE_SESSION
        if commit:
            music_session.commit()
            tag_session.commit()
            music_session.close()
            tag_session.close()
        _GLOBAL_DATABASE_SESSION = None

def submit_global_database_session() -> None:
    global _GLOBAL_DATABASE_SESSION
    with _GLOBAL_DB_LOCK:
        if _GLOBAL_DATABASE_SESSION is None:
            return
        music_session, tag_session = _GLOBAL_DATABASE_SESSION
        music_session.commit()
        tag_session.commit()





