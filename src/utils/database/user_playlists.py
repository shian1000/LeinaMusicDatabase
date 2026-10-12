"""A user's playlists in their private user.db (utils/database/users.py,
models in user_datatables.py). Tracks keep music.db's song id and also the
title/artist/video they had, so a track survives its song being merged or
deleted (utils/database/song_references.py)."""

import time

from utils.database.datatables import Song
from utils.database.user_datatables import Playlist, PlaylistTrack, user_db_session
from config.constants import USER_DB_FILENAME
from utils.database.users import user_dir


def _now_ms() -> int:
    return int(time.time() * 1000)


def save_playlist(username: str, name: str, tracks: list, source: str | None = None,
                  source_id: str | None = None) -> int:
    """Store a new playlist; `tracks` (in order) need title, artist, song_id,
    youtube_video_id and duration_s attributes. Returns its id."""
    now = _now_ms()
    with user_db_session(user_dir(username) / USER_DB_FILENAME) as session:
        playlist = Playlist(name=name, owner=username, source=source, source_id=source_id,
                            created_at=now, updated_at=now)
        playlist.tracks = [
            PlaylistTrack(position=i, song_id=t.song_id, title=t.title, artist=t.artist,
                          youtube_video_id=t.youtube_video_id, duration_s=t.duration_s)
            for i, t in enumerate(tracks)
        ]
        session.add(playlist)
        session.commit()
        return playlist.id


def list_playlists(username: str) -> list[tuple]:
    """(id, name, track count, tracks without a song in the database), newest first."""
    with user_db_session(user_dir(username) / USER_DB_FILENAME) as session:
        return [
            (p.id, p.name, len(p.tracks), sum(1 for t in p.tracks if t.song_id is None))
            for p in session.query(Playlist).order_by(Playlist.updated_at.desc()).all()
        ]


def playlist_songs(music_session, username: str, playlist_id: int) -> tuple[list, list]:
    """(songs, missing) for one playlist, in its order: the music.db Song of
    every track that has one (a song listed twice comes twice), and the
    PlaylistTrack rows without one - never matched, or their song deleted
    since - which still carry their own title/artist."""
    with user_db_session(user_dir(username) / USER_DB_FILENAME) as session:
        tracks = (
            session.query(PlaylistTrack)
            .filter(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position)
            .all()
        )
        session.expunge_all()
    ids = {t.song_id for t in tracks if t.song_id is not None}
    found = {s.id: s for s in music_session.query(Song).filter(Song.id.in_(ids)).all()} if ids else {}
    songs = [found[t.song_id] for t in tracks if t.song_id in found]
    missing = [t for t in tracks if t.song_id not in found]
    return songs, missing
