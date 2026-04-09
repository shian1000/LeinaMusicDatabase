from utils.database.music_db_manager import get_music_session
from utils.database.tag_db_manager import get_tag_session

def open_database_sessions():
    music_session = get_music_session()
    tag_session = get_tag_session()
    return music_session, tag_session


def close_database_sessions(sessions):
    music_session, tag_session = sessions
    music_session.close()
    tag_session.close()