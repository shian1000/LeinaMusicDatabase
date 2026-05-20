# src/music_db_manager.py

import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
from settings import Settings
from utils.database.datatables import Base, Song, Artist
import time
from utils.common.debug import slog, mlog

# --------------------
# Engine & Session
# --------------------

BASE_DIR = Settings.database_dir
DB_PATH = BASE_DIR / "music.db"

slog(BASE_DIR)

ENGINE = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=ENGINE)
mlog("Music DB Manager")
slog(SessionLocal)


# --------------------
# Custom Exceptions
# --------------------

class DatabaseError(Exception):
    """Base exception for database operations."""
    pass


class ArtistNotFoundError(DatabaseError):
    """Raised when an artist is not found in the database."""
    pass


class SongNotFoundError(DatabaseError):
    """Raised when a song is not found in the database."""
    pass


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

def rename_artist(old_name: str, new_name: str) -> None:
    """
    Rename an artist in music.db (case-insensitive lookup).
    
    Args:
        old_name: Current artist name to find
        new_name: New artist name to set
    
    Raises:
        ArtistNotFoundError: If artist with old_name is not found
        ValueError: If new_name is identical to old_name
    """
    session = get_music_session()

    try:
        artist = (
            session.query(Artist)
            .filter(func.lower(Artist.name) == old_name.lower())
            .first()
        )

        if not artist:
            raise ArtistNotFoundError(f"Artist '{old_name}' not found in database.")

        if artist.name == new_name:
            raise ValueError(f"Artist is already named '{new_name}'. No changes needed.")

        artist.name = new_name
        session.commit()
        slog(f"✓ Renamed artist '{old_name}' → '{new_name}'")

    finally:
        session.close()


def delete_artist_and_songs(artist_name: str) -> int:
    """
    Delete an artist and ALL their songs from music.db.
    
    WARNING: This operation is permanent and cannot be undone.
    
    Args:
        artist_name: Name of artist to delete (case-insensitive)
    
    Returns:
        int: Number of songs that were deleted
    
    Raises:
        ArtistNotFoundError: If artist is not found
    """
    session = get_music_session()

    try:
        artist = (
            session.query(Artist)
            .filter(func.lower(Artist.name) == artist_name.lower())
            .first()
        )
        
        if not artist:
            raise ArtistNotFoundError(f"Artist '{artist_name}' not found in database.")

        # Delete songs first
        deleted_songs = (
            session.query(Song)
            .filter(Song.artist_id == artist.id)
            .delete(synchronize_session=False)
        )

        # Delete artist
        session.delete(artist)
        session.commit()
        
        slog(f"✓ Deleted artist '{artist_name}' and {deleted_songs} songs")
        return deleted_songs

    finally:
        session.close()


def delete_song(song_title: str, artist_name: str) -> None:
    """
    Delete a song by title and artist name (case-insensitive).
    
    WARNING: This operation is permanent and cannot be undone.
    
    Args:
        song_title: Title of song to delete
        artist_name: Name of artist (case-insensitive)
    
    Raises:
        SongNotFoundError: If song is not found
    """
    session = get_music_session()

    try:
        song = (
            session.query(Song)
            .join(Artist)
            .filter(
                func.lower(Song.title) == song_title.lower(),
                func.lower(Artist.name) == artist_name.lower(),
            )
            .first()
        )

        if not song:
            raise SongNotFoundError(
                f"Song '{song_title}' by '{artist_name}' not found in database."
            )

        session.delete(song)
        session.commit()
        slog(f"✓ Deleted song '{song_title}' by '{artist_name}'")

    finally:
        session.close()





