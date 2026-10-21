import os
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, CheckConstraint, Index, text, DDL, event, func
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
    name_latin = Column(String)  # Latin form of a non-Latin name (migration 0011), see latin_names.py

    songs = relationship("Song", back_populates="artist")
    albums = relationship("Album", back_populates="artist")
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
    title_latin = Column(String)  # Latin form of a non-Latin title (migration 0011)
    album = Column(String)   # NEW
    year = Column(Integer)
    language = Column(String)
    youtube_video_id = Column(String)  # NEW — manual override, see search_video()
    spotify_url = Column(String)  # album/track page set by hand, read by Years - see spotify_fetcher
    # The official music video, only a link to open (migration 0012) - never played or matched
    music_video_youtube_id = Column(String)
    # Genres, comma-separated, main one first (migration 0013) - see utils/database/genres.py
    genre = Column(String)
    # NULL = a song, else constants.SONG_KINDS (migration 0014) - see utils/database/song_kinds.py
    kind = Column(String)

    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=False)
    # Album row of `album` (migrations 0006/0007). Kept in step with `album` by
    # utils.database.albums - don't set it by hand, change `album` instead.
    album_id = Column(Integer, ForeignKey("albums.id"), index=True)
    track_number = Column(Integer)  # position on the album, NULL = unknown
    disc_number = Column(Integer)   # NULL = 1 / unknown

    artist = relationship("Artist", back_populates="songs")
    album_entry = relationship("Album", back_populates="songs")
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

# --------------------
# Albums (migration 0005) and where their covers come from (0008)
# --------------------
class Album(Base):
    __tablename__ = "albums"
    __table_args__ = (
        CheckConstraint(
            "type IN ('album', 'ep', 'single', 'compilation', 'soundtrack')",
            name="ck_albums_type",
        ),
    )

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    title_latin = Column(String)  # Latin form of a non-Latin title (migration 0011)
    artist_id = Column(Integer, ForeignKey("artists.id"), index=True)  # NULL = various artists
    year = Column(Integer)
    type = Column(String)  # ALBUM_TYPES in constants.py, NULL = not known
    # External ids, so a later fetcher finds covers / track lists without searching again
    musicbrainz_release_group_id = Column(String)
    itunes_collection_id = Column(Integer)
    deezer_album_id = Column(Integer)
    spotify_album_id = Column(String)

    artist = relationship("Artist", back_populates="albums")
    songs = relationship("Song", back_populates="album_entry")
    covers = relationship("AlbumCover", back_populates="album", cascade="all, delete-orphan")


class AlbumCover(Base):
    __tablename__ = "album_covers"
    __table_args__ = (
        CheckConstraint(
            "source IN ('file', 'itunes', 'deezer', 'musicbrainz', 'youtube', 'manual')",
            name="ck_album_covers_source",
        ),
        CheckConstraint("status IN ('found', 'missing')", name="ck_album_covers_status"),
    )

    album_id = Column(Integer, ForeignKey("albums.id"), primary_key=True)
    source = Column(String, primary_key=True)  # ALBUM_COVER_SOURCES in constants.py
    url = Column(String)   # remote sources
    path = Column(String)  # 'file'/'manual': relative to the covers directory
    width = Column(Integer)
    height = Column(Integer)
    bytes = Column(Integer)
    status = Column(String, nullable=False)
    checked_at = Column(String, nullable=False, server_default=text("(datetime('now'))"))

    album = relationship("Album", back_populates="covers")

# --------------------
# Song ids that stopped existing (migration 0009) - see utils/database/song_references.py
# --------------------
class SongIdChange(Base):
    __tablename__ = "song_id_changes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    old_song_id = Column(Integer, nullable=False, index=True)
    new_song_id = Column(Integer)  # the song kept by a merge; NULL = deleted
    changed_at = Column(String, nullable=False, server_default=text("(datetime('now'))"))

# --------------------
# YouTube videos found bad (migration 0010), read by the phone client too -
# see utils/database/inbox_requests.py
# --------------------
class BadYoutubeVideo(Base):
    __tablename__ = "bad_youtube_videos"

    id = Column(Integer, primary_key=True)
    video_id = Column(String, nullable=False)
    song_id = Column(Integer, index=True)  # NULL = bad for every song
    reason = Column(String, nullable=False)  # constants.BAD_VIDEO_REASONS
    marked_at = Column(String, nullable=False, server_default=text("(datetime('now'))"))

Index("ux_bad_youtube_videos", BadYoutubeVideo.video_id, func.ifnull(BadYoutubeVideo.song_id, 0), unique=True)

# Same trigger as migration 0010, so a database made by create_all() keeps
# song_id current through merges and deletions too. Hung on song_id_changes,
# the table it fires on, which has to exist first.
event.listen(SongIdChange.__table__, "after_create", DDL("""
CREATE TRIGGER IF NOT EXISTS bad_youtube_videos_follow_songs
AFTER INSERT ON song_id_changes
BEGIN
	UPDATE OR IGNORE bad_youtube_videos SET song_id = NEW.new_song_id
		WHERE song_id = NEW.old_song_id AND NEW.new_song_id IS NOT NULL;
	DELETE FROM bad_youtube_videos WHERE song_id = NEW.old_song_id;
END
"""))

# --------------------
# Songs Leina Radio asked to have added that the owner turned down (migration
# 0015), read by the phone client too - see utils/database/inbox_song_requests.py
# --------------------
class RejectedSongRequest(Base):
    __tablename__ = "rejected_song_requests"

    id = Column(Integer, primary_key=True)
    artist = Column(String)  # as requested; NULL = the request had none
    title = Column(String, nullable=False)
    youtube_video_id = Column(String)
    spotify_track_id = Column(String)
    rejected_at = Column(String, nullable=False, server_default=text("(datetime('now'))"))


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
    "name",
    "full album",  # rows with songs.kind = full_album; the query may be empty
]

artist_categories = [
    "artist name",
    "artist origin",
    "artist id"
]

