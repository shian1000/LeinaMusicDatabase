import os
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

ALBUM_TITLE_BLACKLIST = {
    # Exact matches (lowercased)
    "now that's what i call music",
    "greatest hits",
    "best of",
    "the best of",
    "very best of",
    "the very best of",
    "essential",
    "the essential",
    "collection",
    "the collection",
    "gold",
    "platinum",
    "anthology",
    "retrospective",
    "definitive collection",
    "complete collection"
}

ALBUM_TITLE_BLACKLIST_SUBSTRINGS = {
    # Partial matches — if any of these appear in the title, skip it
    "greatest hits",
    "best of",
    "collection",
    "anthology",
    "retrospective",
    "compilation",
    "now that's what i call",
    "the essential",
    "pop party",
    "itunes",
    "remix",
    "germany",
    "festival",
    "United Palace Theatre",
    "1996-2011",
    "radio",
    "spotify",
    "paris",
    "session",
    "ultimate",
    "hits",
    "edition",
    "awards",
    "przebojów",
    "valentine's day",
    "exercises",
    "new orleans",
    "morrison",
    "2014",
    "women in music",
    "london, uk",
    "england",
    "collecion",
    "youtube",
    "essential",
    "live",
    "хит",
    "concert",
    "hottest",
    "liverpool",
    "volume"
}

# --------------------
# Artist table
# --------------------
class Artist(Base):
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    origin = Column(String)  # NEW

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
    "tag"
]

search_only_categories = [
    "name"
]

artist_categories = [
    "artist name",
    "artist origin"
]