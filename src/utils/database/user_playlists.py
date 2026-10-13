"""A user's playlists in their private user.db (utils/database/users.py,
models in user_datatables.py). Tracks keep music.db's song id and also the
title/artist/video they had, so a track survives its song being merged or
deleted (utils/database/song_references.py).

Every write goes through playlist_sync.py, shared with the sharing server:
the menu's playlists sync to the user's phones like the phones' own, so each
change needs a uid, the next revision, and (for a deletion) an archive copy."""

from contextlib import closing
from pathlib import Path

from utils.database import playlist_sync
from utils.database.datatables import Song
from utils.database.user_datatables import Playlist, PlaylistTrack, user_db_session
from config.constants import USER_DB_FILENAME
from utils.database.users import user_dir


def save_playlist(username: str, name: str, tracks: list, source: str | None = None,
                  source_id: str | None = None) -> int:
    """Store a new playlist; `tracks` (in order) need title, artist, song_id,
    youtube_video_id and duration_s attributes. Returns its id."""
    now = playlist_sync.now_ms()
    playlist = playlist_sync.parse_playlist({
        "name": name, "source": source, "source_id": source_id, "created_at": now, "updated_at": now,
        "tracks": [{"song_id": t.song_id, "title": t.title, "artist": t.artist,
                    "youtube_video_id": t.youtube_video_id, "duration_s": t.duration_s} for t in tracks],
    })
    uid = playlist_sync.new_uid()
    with closing(playlist_sync.connect(user_dir(username) / USER_DB_FILENAME)) as conn:
        # Song ids come from music.db itself here, so nothing to resolve; and
        # a second import of the same folder is a playlist of its own
        playlist_sync.store_playlist(conn, username, uid, playlist, base_revision=0, merge_same_source=False)
        return conn.execute("SELECT id FROM playlists WHERE uid = ?", (uid,)).fetchone()[0]


def delete_playlist(username: str, playlist_id: int) -> Path | None:
    """Delete a playlist here and on the user's phones (they get the marker
    at their next sync). Returns the archive file it was copied to first."""
    folder = user_dir(username)
    with closing(playlist_sync.connect(folder / USER_DB_FILENAME)) as conn:
        row = conn.execute("SELECT uid, revision FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if row is None:
            return None
        _, _, archived = playlist_sync.delete_playlist(conn, folder, row["uid"], row["revision"],
                                                       playlist_sync.now_ms())
        return archived


def list_playlists(username: str) -> list[tuple]:
    """(id, name, track count, tracks without a song in the database), newest
    first; deleted playlists (kept only as markers for the phones) are left out."""
    with user_db_session(user_dir(username) / USER_DB_FILENAME) as session:
        return [
            (p.id, p.name, len(p.tracks), sum(1 for t in p.tracks if t.song_id is None))
            for p in session.query(Playlist).filter(Playlist.deleted_at.is_(None))
            .order_by(Playlist.updated_at.desc()).all()
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
