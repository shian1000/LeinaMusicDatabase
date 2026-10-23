"""Song actions (src/menu/song_actions/__init__.py) with mocked prompts and
an in-memory tag.db."""
import pytest
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import menu.main_menu  # noqa: F401 - imported first, as main.py does (menu.song_actions alone is circular)
import menu.song_actions as song_actions
from utils.database import tags_management
from utils.database.tag_db_manager import Base, SongTag, Tag


@pytest.fixture
def tag_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(tags_management, "get_global_database_sessions", lambda: (None, session))
    monkeypatch.setattr(song_actions, "submit_global_database_session", session.commit)
    yield session
    session.close()


def _songs(*ids):
    return [SimpleNamespace(id=i, title=f"Song {i}", artist=SimpleNamespace(name="Artist")) for i in ids]


def test_add_tags_tags_every_song(tag_session, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "  workout ")
    song_actions.add_tags_menu(_songs(1, 2))

    assert [t.name for t in tag_session.query(Tag)] == ["workout"]
    assert sorted(st.song_id for st in tag_session.query(SongTag)) == [1, 2]
    assert "Added 'workout' to 2 song(s)" in capsys.readouterr().out


def test_add_tags_refuses_an_empty_tag(tag_session, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "   ")
    song_actions.add_tags_menu(_songs(1))

    assert tag_session.query(Tag).count() == 0
    assert "Aborted" in capsys.readouterr().out


# ---- Copy songs from storage: the library index (COM-01) ----

def _set_library(monkeypatch, library: str):
    """The one settings object file_management and the copy menu both read."""
    from upath import UPath
    from settings import settings
    for name, value in (("local_library_dir_str", library), ("local_library_dir", UPath(library)),
                        ("smb_username", "user"), ("smb_password", "secret")):
        monkeypatch.setattr(settings, name, value)


def test_copy_from_local_storage_finds_the_index_inside_the_library(tmp_path, monkeypatch):
    import importlib
    copy_menu = importlib.import_module("menu.song_actions.copy_songs_from_storage")
    (tmp_path / ".mp3_index.sqlite3").write_bytes(b"")
    _set_library(monkeypatch, f"{tmp_path}/")
    monkeypatch.setattr(copy_menu, "load_recent_dirs", lambda: [])
    asked = []

    def select(message, choices):
        asked.append(message)
        return SimpleNamespace(ask=lambda: "Back")
    monkeypatch.setattr(copy_menu.questionary, "select", select)

    copy_menu.copy_songs_from_storage([("Artist", "Song")])

    assert asked == ["Where do you want the files to be copied into: "]


@pytest.mark.parametrize("library, expected", [
    ("/media/x/Music/", "/media/x/Music/.mp3_index.sqlite3"),
    ("/media/x/Music", "/media/x/Music/.mp3_index.sqlite3"),
    ("smb://nas/share/Music/", "smb://user:secret@nas/share/Music/.mp3_index.sqlite3"),
    ("smb://nas/share/Music", "smb://user:secret@nas/share/Music/.mp3_index.sqlite3"),
])
def test_library_index_uri_is_inside_the_library_folder(library, expected, monkeypatch):
    from utils.common import file_management as fm
    _set_library(monkeypatch, library)

    assert fm.library_index_uri() == expected


def test_copy_without_a_library_index_says_so_and_asks_nothing(tmp_path, monkeypatch, capsys):
    import importlib
    copy_menu = importlib.import_module("menu.song_actions.copy_songs_from_storage")
    _set_library(monkeypatch, str(tmp_path))

    def select(*args, **kwargs):
        raise AssertionError("nothing to ask without an index")
    monkeypatch.setattr(copy_menu.questionary, "select", select)

    assert copy_menu.copy_songs_from_storage([("Artist", "Song")]) == "Back"
    out = capsys.readouterr().out
    assert "No library index (.mp3_index.sqlite3)" in out
    assert "not implemented" not in out and "TODO" not in out


# ---- No menu entry that does nothing (UI-29) ----

PLACEHOLDER_LABELS = {"Fetch songs from txt file", "Artists and title", "Make TXT file"}


def _menu_labels(monkeypatch, module, call):
    labels = []
    monkeypatch.setattr(module, "execute_menu_item", lambda title, action_map, **kw: labels.extend(action_map))
    call()
    assert labels
    return set(labels)


def test_song_actions_menu_has_no_placeholder_entries(monkeypatch):
    labels = _menu_labels(monkeypatch, song_actions, lambda: song_actions.song_actions(_songs(1)))

    assert "Copy songs from local storage" in labels
    assert labels.isdisjoint(PLACEHOLDER_LABELS)


def test_database_menus_have_no_placeholder_entries(monkeypatch):
    import importlib
    enter_database = importlib.import_module("menu.main_menu.enter_database")
    fill_missing = importlib.import_module(
        "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data")
    monkeypatch.setattr(enter_database, "open_and_set_global_database_sessions", lambda: None)
    monkeypatch.setattr(enter_database, "close_global_database_sessions", lambda commit: None)

    database_labels = _menu_labels(monkeypatch, enter_database, enter_database.enter_database)
    fill_labels = _menu_labels(monkeypatch, fill_missing, fill_missing.fill_missing_data)

    assert {"Fetch songs", "Download YT song"} <= database_labels
    assert fill_labels == {"Albums", "Years", "Genres"}
    assert (database_labels | fill_labels).isdisjoint(PLACEHOLDER_LABELS)
