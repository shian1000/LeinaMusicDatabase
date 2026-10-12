"""Users -> Enter user database (utils/discoveries/folder_playlist.py,
utils/database/user_playlists.py) and managing users (users.py rename /
change password / delete): a folder becomes a playlist in alphabetical path
order, matched to existing songs by import_engine.LocalSongMatcher (also
through bracket/junk annotations and "A ft. B" artist names), unmatched files
reported, and the playlist saved to the user's own user.db. In-memory music
DB, temp user folders."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import song_references, users
from utils.database.database_sessions import set_global_database_sessions
from utils.database.datatables import Artist, Base, Song
from utils.database.user_playlists import list_playlists, save_playlist
from utils.discoveries import folder_playlist as fp


@pytest.fixture
def music_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    hey, afina = Artist(name="Hey"), Artist(name="Afina")
    session.add_all([
        Song(title="Karma", artist=hey, youtube_video_id="vid1"),
        Song(title="Teksański", artist=hey),
        Song(title="Pay Your Respect", artist=afina),
    ])
    session.commit()
    set_global_database_sessions((session, None))
    yield session
    set_global_database_sessions(None)
    session.close()


@pytest.fixture
def users_env(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", tmp_path / "Users")
    monkeypatch.setattr(users, "DELETED_USERS_DIR", tmp_path / "archive" / "deleted-users")
    monkeypatch.setattr(users, "users_available", lambda: True)
    monkeypatch.setattr(song_references, "latest_change_id", lambda: 0)
    return tmp_path / "Users"


def _folder(tmp_path, tags: dict) -> Path:
    """mp3 files (empty - their tags come from the mocked read_file_metadata)."""
    folder = tmp_path / "music"
    for relative in tags:
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_bytes(b"")
    return folder


def test_folder_becomes_playlist_in_alphabetical_order(tmp_path, music_session, monkeypatch):
    tags = {
        "b/Hey - Teksanski.mp3": ("Hey", "Teksański"),
        "a/z.mp3": ("Hey", "Karma (Official Video)"),
        "A/y.mp3": ("Afina ft. CJ Pitts", "Pay Your Respect (Official Lyric Video)"),
        "c.mp3": ("Nobody", "Unknown song"),
    }
    folder = _folder(tmp_path, tags)
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(
        zip(("artist_name", "title"), tags[str(f.relative_to(folder))])))

    playlist = fp.build_folder_playlist(music_session, folder)

    assert [str(t.file.relative_to(folder)) for t in playlist.tracks] == [
        "A/y.mp3", "a/z.mp3", "b/Hey - Teksanski.mp3", "c.mp3"]
    assert [(t.title, t.artist) for t in playlist.tracks] == [
        ("Pay Your Respect", "Afina"), ("Karma", "Hey"), ("Teksański", "Hey"), ("Unknown song", "Nobody")]
    assert playlist.tracks[1].youtube_video_id == "vid1"
    assert [t.file.name for t in playlist.unmatched] == ["c.mp3"]
    assert playlist.unmatched[0].song_id is None


def test_saved_playlist_keeps_order_and_fallback_text(users_env, tmp_path, music_session, monkeypatch):
    users.create_user("ania", "secret1")
    tags = {"1.mp3": ("Hey", "Teksański"), "2.mp3": ("Hey", "Karma")}
    folder = _folder(tmp_path, tags)
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(
        zip(("artist_name", "title"), tags[f.name])))
    playlist = fp.build_folder_playlist(music_session, folder)

    playlist_id = save_playlist("ania", "Mix", playlist.tracks, source="folder", source_id=str(folder))
    assert list_playlists("ania") == [(playlist_id, "Mix", 2, 0)]

    import sqlite3
    rows = sqlite3.connect(users_env / "ania" / "user.db").execute(
        "SELECT position, song_id, title, artist FROM playlist_tracks ORDER BY position").fetchall()
    assert [r[2] for r in rows] == ["Teksański", "Karma"]
    assert all(r[1] is not None for r in rows)


def test_rename_change_password_and_delete(users_env, tmp_path):
    users.create_user("ania", "secret1")

    users.rename_user("ania", "Anna")
    assert users.list_usernames() == ["anna"]
    assert users.read_account("anna")["username"] == "anna"
    assert users.verify_user("anna", "secret1")
    with pytest.raises(users.InvalidUserError):
        users.rename_user("nobody", "x1")

    users.change_password("anna", "better2")
    assert users.verify_user("anna", "better2") and not users.verify_user("anna", "secret1")
    with pytest.raises(users.InvalidUserError):
        users.change_password("anna", "123")

    archived = users.delete_user("anna")
    assert users.list_usernames() == []
    assert (archived / "user.db").exists() and archived.parent == tmp_path / "archive" / "deleted-users"
