import os
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from utils.common.debug import slog
import re

Base = declarative_base()



# --------------------
# Artist table
# --------------------
class Artist(Base):
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    origin = Column(String)
    synonyms = Column(String)

    songs = relationship("Song", back_populates="artist")
    additional_song_links = relationship(
        "AdditionalSongArtist", back_populates="artist", cascade="all, delete-orphan"
    )


# --------------------
# Song table
# --------------------
class Song(Base):
    __tablename__ = "songs"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    album = Column(String)   # NEW
    year = Column(Integer)
    language = Column(String)
    youtube_video_id = Column(String)  # NEW — manual override, see search_video()

    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=False)

    nostalgic = Column(Integer)
    melancholic = Column(Integer)
    party = Column(Integer)

    artist = relationship("Artist", back_populates="songs")
    # Only multi-artist songs have rows here - `artist` stays the primary one.
    # Use utils.database.song_artists helpers rather than reading this directly.
    additional_artist_links = relationship(
        "AdditionalSongArtist", back_populates="song", cascade="all, delete-orphan"
    )


# --------------------
# Additional artists of a multi-artist song (migration 0003)
# --------------------
class AdditionalSongArtist(Base):
    __tablename__ = "additional_song_artists"
    __table_args__ = (
        CheckConstraint("role IN ('main', 'feat')", name="ck_additional_song_artists_role"),
        Index("ix_additional_song_artists_artist_id", "artist_id"),
    )

    song_id = Column(Integer, ForeignKey("songs.id"), primary_key=True)
    artist_id = Column(Integer, ForeignKey("artists.id"), primary_key=True)
    role = Column(String, nullable=False)  # ADDITIONAL_ARTIST_ROLES in constants.py

    song = relationship("Song", back_populates="additional_artist_links")
    artist = relationship("Artist", back_populates="additional_song_links")

song_categories = [
    "title",
    "artist name",
    "album",
    "year",
    "language",
    "artist origin",
    "tag",
    "artist id"
]

search_only_categories = [
    "name"
]

artist_categories = [
    "artist name",
    "artist origin",
    "artist id"
]

