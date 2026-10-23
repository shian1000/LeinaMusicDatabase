"""The song / artist editor (src/menu/song_actions/edit_songs.py) and
edit_db_entry() (utils/database/database_management.py): only fields that
can be edited are offered (DB-05, UI-04), and "Add song" goes through the
import (UI-05). In-memory music.db, mocked prompts."""
import importlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import menu.main_menu  # noqa: F401 - imported first, as main.py does (menu.song_actions alone is circular)
from utils.database import database_management as dm
from utils.database.datatables import Artist, Base, Song

edit_songs = importlib.import_module("menu.song_actions.edit_songs")


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def song(session):
    song = Song(title="Roxanne", artist=Artist(name="The Police", origin="UK"))
    session.add(song)
    session.commit()
    return song


@pytest.mark.parametrize("category", ["tag", "name", "full album", "artist id "])
def test_edit_db_entry_reports_what_it_cant_edit(song, category, capsys):
    before = (song.title, song.album, song.year, song.language)
    if category.strip() == "artist id":
        with pytest.raises(ValueError):  # the artist's, never the song's
            dm.edit_db_entry(song, category, "5")
        return
    assert dm.edit_db_entry(song, category, "x") is False
    out = capsys.readouterr().out
    assert "Can't edit" in out and "Updated" not in out
    assert (song.title, song.album, song.year, song.language) == before


def test_edit_db_entry_sets_songs_and_artists_fields(song, capsys):
    assert dm.edit_db_entry(song, "year", " 1978 ") is True
    assert song.year == 1978
    assert dm.edit_db_entry(song, "year", "197x") is False and song.year == 1978
    assert dm.edit_db_entry(song, "album", "Outlandos d'Amour") and song.album == "Outlandos d'Amour"
    assert dm.edit_db_entry(song.artist, "artist origin", "GB") and song.artist.origin == "GB"
    assert dm.edit_db_entry(song.artist, "artist id", "7") is False
    assert "'UK' --> 'GB'" in capsys.readouterr().out


def test_song_editor_offers_only_editable_fields(song, monkeypatch):
    offered = []

    def select(message, choices):
        offered.extend(choices)
        return _Answer(next(c for c in choices if c.startswith("year")))

    monkeypatch.setattr(edit_songs.questionary, "select", select)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1978")
    monkeypatch.setattr(edit_songs, "submit_global_database_session", lambda: None)

    edit_songs.edit_entry_menu(db_object=song)

    assert not [c for c in offered if c.startswith(("tag", "artist id", "artist origin", "artist name"))]
    assert offered[:2] == ["title (Roxanne)", "artist (The Police)"]
    assert song.year == 1978


def _add_song(monkeypatch, answers):
    imported, validators = [], {}

    def text(question, validate):
        validators[question] = validate
        return _Answer(answers.pop(0))

    monkeypatch.setattr(edit_songs.questionary, "text", text)
    monkeypatch.setattr(edit_songs, "run_import_batch", lambda entries: imported.extend(entries) or [])
    edit_songs.add_songs_menu()
    return imported, validators


def test_add_song_goes_through_the_import(monkeypatch):
    imported, validators = _add_song(monkeypatch, [" Roxanne ", "The Police", "", "1978", "", "UK"])

    assert imported == [{"title": "Roxanne", "artist_name": "The Police", "album": None, "year": 1978,
                         "language": None, "origin": "UK"}]
    assert validators["Title:"]("   ") == "Can't be blank"
    assert validators["Artist:"]("") == "Can't be blank"
    assert validators["Album (optional):"]("") is True
    assert validators["Year (optional):"]("197x") == "Type a whole number"


def test_add_song_cancelled_adds_nothing(monkeypatch, capsys):
    imported, _ = _add_song(monkeypatch, ["Roxanne", None])

    assert imported == []
    assert "Aborted" in capsys.readouterr().out


# ---- Manage database → Divide artists (UI-03) ----

def _divide_menu():
    return importlib.import_module("menu.main_menu.enter_database.manage_database.merge_divide_menu")


def test_divide_artists_back_does_nothing(song, monkeypatch):
    menu = _divide_menu()
    monkeypatch.setattr(menu, "ask_for_entires_list", lambda mode: [song.artist])
    monkeypatch.setattr(menu, "pick_from_db_objects", lambda objects, question: None)
    monkeypatch.setattr(menu, "divide_artist", lambda artist: pytest.fail("divided"))

    menu.divide_artists_menu()  # used to be an AttributeError on None.id


def test_divide_artists_lists_the_artists_songs(session, song, monkeypatch, capsys):
    menu = _divide_menu()
    session.add(Song(title="Message in a Bottle", artist=song.artist, year=1979))
    session.commit()
    monkeypatch.setattr(dm, "get_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(menu, "ask_for_entires_list", lambda mode: [song.artist])
    monkeypatch.setattr(menu, "pick_from_db_objects", lambda objects, question: objects[0])
    monkeypatch.setattr(menu, "ask_yes_no", lambda q: True)

    menu.divide_artists_menu()

    out = capsys.readouterr().out
    assert "Found 2 song(s)" in out and "Message in a Bottle" in out
    assert session.query(Artist).filter_by(name="The Police").count() == 2
