import os
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
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

    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=False)

    nostalgic = Column(Integer)
    melancholic = Column(Integer)
    party = Column(Integer)

    artist = relationship("Artist", back_populates="songs")

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

