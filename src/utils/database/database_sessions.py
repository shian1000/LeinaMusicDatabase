from utils.database.music_db_manager import get_music_session
from utils.database.tag_db_manager import get_tag_session
from sqlalchemy.orm import Session
from typing import Tuple

def open_database_sessions() -> Tuple[Session, Session]:
    music_session = get_music_session()
    tag_session = get_tag_session()
    return music_session, tag_session


def close_database_sessions(sessions: Tuple[Session, Session]):
    music_session, tag_session = sessions
    music_session.close()
    tag_session.close()

def submit_and_close_database_sessions(sessions: Tuple[Session, Session]):
    music_session, tag_session = sessions
    music_session.commit()
    tag_session.commit()
    music_session.close()
    tag_session.close()