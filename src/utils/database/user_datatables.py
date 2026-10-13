"""SQLAlchemy models of a user's private user.db (utils/database/users.py).
The schema itself comes from migrations/user/ - these mirror it and must be
kept in step with each new file there."""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

UserBase = declarative_base()


class Playlist(UserBase):
    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    owner = Column(String)
    source = Column(String)     # USER_PLAYLIST_SOURCE_* in constants.py
    source_id = Column(String)  # e.g. the YouTube playlist id or the folder imported
    source_owner = Column(String)  # author of an imported YouTube/Spotify playlist
    created_at = Column(Integer, nullable=False)  # Unix ms, like the phone
    updated_at = Column(Integer, nullable=False)  # the changing device's clock; decides sync conflicts
    # Sync with the phones (playlist_sync.py, which does every write to these)
    uid = Column(String, unique=True)
    revision = Column(Integer, nullable=False, default=0)
    deleted_at = Column(Integer)  # Unix ms; NULL = live, else a marker without tracks
    song_ids_checked_to = Column(Integer)

    tracks = relationship(
        "PlaylistTrack", back_populates="playlist", order_by="PlaylistTrack.position",
        cascade="all, delete-orphan",
    )


class PlaylistTrack(UserBase):
    __tablename__ = "playlist_tracks"

    playlist_id = Column(Integer, ForeignKey("playlists.id"), primary_key=True)
    position = Column(Integer, primary_key=True)  # 0-based
    song_id = Column(Integer, index=True)  # music.db songs.id; NULL = not (or no longer) in MusicDatabase
    title = Column(String, nullable=False)
    artist = Column(String)
    youtube_video_id = Column(String)
    spotify_track_id = Column(String)
    youtube_removed = Column(Integer, nullable=False, default=0)  # 1 = video gone, phone finds another
    duration_s = Column(Integer)

    playlist = relationship("Playlist", back_populates="tracks")


class PrivateTag(UserBase):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


class PrivateSongTag(UserBase):
    __tablename__ = "song_tags"
    __table_args__ = (UniqueConstraint("song_id", "tag_id", name="uix_song_tag"),)

    id = Column(Integer, primary_key=True)
    song_id = Column(Integer, nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False)


@contextmanager
def user_db_session(db_path: Path):
    """A session on one user.db; its engine is disposed afterwards, so no
    connection to the file on T7 stays open. The caller commits."""
    engine = create_engine(f"sqlite:///{db_path}")
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
