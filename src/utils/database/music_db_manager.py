# src/music_db_manager.py

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from settings import Settings
from utils.database.datatables import Base, Song, Artist  # Song, Artist: imported from here by resolve_duplicates
from utils.database.albums import register_album_sync
from utils.database.song_references import register_song_reference_sync

# --------------------
# Engine & Session
# --------------------

BASE_DIR = Settings.database_dir
DB_PATH = BASE_DIR / "music.db"

ENGINE = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=ENGINE)
# songs.album_id follows songs.album on every flush - see utils/database/albums.py
register_album_sync(SessionLocal)
# Deleted song ids are logged for tag.db / users' DBs and never reused - see utils/database/song_references.py
register_song_reference_sync(SessionLocal)


# --------------------
# Public API
# --------------------

def create_music_db():
    """
    Create music.db with all tables.
    """
    os.makedirs(DB_PATH.parent, exist_ok=True)
    Base.metadata.create_all(ENGINE)


def get_music_session():
    """
    Get a new SQLAlchemy session for music.db.
    """
    return SessionLocal()
