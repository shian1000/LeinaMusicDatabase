"""The menu's global sessions (utils/database/database_sessions.py): a failed
commit is rolled back, so the menu keeps working; closing them always closes
and unsets them. In-memory SQLite."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from utils.database import database_sessions
from utils.database.datatables import Artist, Base, Song
from utils.database.tag_db_manager import Base as TagBase
from utils.ui import menu_utils


@pytest.fixture
def sessions(monkeypatch):
    music_engine, tag_engine = create_engine("sqlite:///:memory:"), create_engine("sqlite:///:memory:")
    Base.metadata.create_all(music_engine)
    TagBase.metadata.create_all(tag_engine)
    music, tags = sessionmaker(bind=music_engine)(), sessionmaker(bind=tag_engine)()
    monkeypatch.setattr(database_sessions, "_GLOBAL_DATABASE_SESSION", (music, tags))
    monkeypatch.setattr(database_sessions, "apply_song_id_changes", lambda **kw: None)
    yield music
    music.close()
    tags.close()


def test_a_failed_commit_is_rolled_back_and_the_session_keeps_working(sessions):
    sessions.add(Song(title="No artist"))  # songs.artist_id is NOT NULL

    with pytest.raises(database_sessions.DatabaseCommitError):
        database_sessions.submit_global_database_session()

    sessions.add(Song(title="Waterloo", artist=Artist(name="ABBA")))
    database_sessions.submit_global_database_session()
    assert [s.title for s in sessions.query(Song)] == ["Waterloo"]


def test_the_menu_shows_a_failed_save_and_goes_on(sessions, monkeypatch, capsys):
    answers = iter(["Save", "Save", "Back"])
    monkeypatch.setattr(menu_utils.questionary, "select",
                        lambda *a, **k: type("Q", (), {"ask": lambda self: next(answers)})())
    monkeypatch.setattr(menu_utils, "clear_screen", lambda: None)
    titles = iter([None, "Waterloo"])

    def save():
        title = next(titles)
        sessions.add(Song(title=title or "No artist", artist=None if title is None else Artist(name="ABBA")))
        database_sessions.submit_global_database_session()

    menu_utils.execute_menu_item("Test", {"Save": save})

    assert "Saving to the database failed" in capsys.readouterr().out
    assert [s.title for s in sessions.query(Song)] == ["Waterloo"]


def test_closing_without_commit_rolls_back_and_closes(sessions):
    # DB-06: commit=False only dropped the global reference - the sessions
    # stayed open with their unsaved changes
    tags = database_sessions.get_global_database_sessions()[1]
    sessions.add(Song(title="Waterloo", artist=Artist(name="ABBA")))
    sessions.flush()
    database_sessions.close_global_database_sessions(commit=False)
    assert database_sessions.get_global_database_sessions() is None
    assert not sessions.in_transaction() and not tags.in_transaction()
    assert sessions.query(Song).count() == 0


def test_a_failed_commit_on_close_still_closes_and_unsets(sessions):
    # DB-06: a failing commit left the sessions open and set globally
    sessions.add(Song(title="No artist"))  # songs.artist_id is NOT NULL
    with pytest.raises(database_sessions.DatabaseCommitError):
        database_sessions.close_global_database_sessions(commit=True)
    assert database_sessions.get_global_database_sessions() is None
    assert not sessions.in_transaction() and len(sessions.identity_map) == 0 and not sessions.new

