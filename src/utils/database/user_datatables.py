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
    cover_sha256 = Column(String)  # the phone-picked cover (covers.py); NULL = none
    # JSON list of source ids (video / Spotify track) the user removed; NULL = none
    excluded_source_tracks = Column(String)

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
    """A private tag; with kind 'mood', one of the user's moods (migration
    0005, synced by mood_sync.py, which does every write to moods)."""
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)  # unique among live rows (deleted_at IS NULL)
    kind = Column(String, nullable=False, default="marker")
    uid = Column(String, unique=True)
    revision = Column(Integer, nullable=False, default=0)  # the user's mood_revision counter
    created_at = Column(Integer)  # Unix ms
    updated_at = Column(Integer)
    deleted_at = Column(Integer)  # NULL = live, else a marker without songs
    song_ids_checked_to = Column(Integer)
    promoted_to = Column(Integer)  # tag.db id of the global mood it was moved into
    promoted_to_name = Column(String)
    merged_into = Column(String)  # uid of the mood it was merged into
    cover_sha256 = Column(String)  # the phone-picked cover (covers.py); NULL = none


class GlobalMoodCover(UserBase):
    """The user's private cover of a global mood (tag.db's tags.id), synced
    by mood_sync.py (migration 0006)."""
    __tablename__ = "global_mood_covers"

    mood_id = Column(Integer, primary_key=True)
    cover_sha256 = Column(String)  # NULL = removed (the row stays for the phones)
    revision = Column(Integer, nullable=False)  # the user's global_mood_cover_revision counter
    updated_at = Column(Integer, nullable=False)


class Folder(UserBase):
    """A folder of the phone's Biblioteka (migration 0008), synced by
    folder_sync.py, which does every write to folders."""
    __tablename__ = "folders"

    id = Column(Integer, primary_key=True)
    uid = Column(String, unique=True, nullable=False)
    name = Column(String)  # NULL on a deleted folder
    items = Column(String)  # JSON list of refs, as the phone sent it
    revision = Column(Integer, nullable=False, default=0)  # the user's folder_revision counter
    created_at = Column(Integer)  # Unix ms
    updated_at = Column(Integer, nullable=False)
    deleted_at = Column(Integer)  # NULL = live, else a marker without items


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
