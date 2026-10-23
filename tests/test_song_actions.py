"""Song actions (src/menu/song_actions/__init__.py) with mocked prompts and
an in-memory tag.db; also Copy songs from storage and the Fetch songs /
artists category list."""
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


def test_a_failed_tag_write_is_rolled_back_and_tagging_goes_on(tag_session, capsys):
    # DB-20: the error was swallowed (slog only) and the tag session left
    # needing a rollback, so every later tag and the next commit failed too
    tag_session.add(Tag(name=None))  # tags.name is NOT NULL: the next autoflush fails

    assert not tags_management.add_tag_to_song(_songs(1)[0], "workout")
    assert "failed, unsaved tag changes were undone" in capsys.readouterr().out

    assert tags_management.add_tag_to_song(_songs(2)[0], "workout")
    tag_session.commit()
    assert [t.name for t in tag_session.query(Tag)] == ["workout"]
    assert [st.song_id for st in tag_session.query(SongTag)] == [2]
    assert tags_management.has_tag_on_song(_songs(2)[0], "workout")


def test_songs_with_tags_shows_the_given_songs_and_leaves_the_sessions_open(tag_session, monkeypatch, capsys):
    # UI-15: display_songs_with_tags() ignored its songs, queried every song
    # itself and closed both global sessions, detaching the menu's objects
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from utils.database.datatables import Artist, Base as MusicBase, Song
    from utils.ui import display_utils

    engine = create_engine("sqlite:///:memory:")
    MusicBase.metadata.create_all(engine)
    music = sessionmaker(bind=engine)()
    abba = Artist(name="ABBA")
    music.add_all([Song(title="Waterloo", artist=abba, year=1974), Song(title="SOS", artist=abba)])
    music.commit()
    monkeypatch.setattr(tags_management, "get_global_database_sessions", lambda: (music, tag_session))
    waterloo, sos = music.query(Song).order_by(Song.title.desc()).all()
    tag_session.add_all([Tag(id=1, name="workout"), Tag(id=2, name="classic")])
    tag_session.add_all([SongTag(song_id=waterloo.id, tag_id=1), SongTag(song_id=waterloo.id, tag_id=2),
                         SongTag(song_id=sos.id, tag_id=1)])
    tag_session.commit()

    display_utils.display_songs_with_tags([waterloo])

    out = capsys.readouterr().out
    assert "Waterloo" in out and "classic, workout" in out and "SOS" not in out
    assert waterloo in music  # still attached: the session wasn't closed
    assert waterloo.title == "Waterloo" and tag_session.query(Tag).count() == 2
    music.close()


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
    ("smb://nas/share/Music/", "smb://nas/share/Music/.mp3_index.sqlite3"),
    ("smb://nas/share/Music", "smb://nas/share/Music/.mp3_index.sqlite3"),
])
def test_library_index_uri_is_inside_the_library_folder(library, expected, monkeypatch):
    from utils.common import file_management as fm
    _set_library(monkeypatch, library)

    assert fm.library_index_uri() == expected


def test_smb_login_goes_to_upath_options_never_into_the_uri(monkeypatch):
    # COM-11: the password was pasted into the URI, which ended up in
    # debug.log with any smb error
    from utils.common import file_management as fm
    _set_library(monkeypatch, "smb://nas/share/Music")

    index = fm.library_index_path()

    assert "secret" not in fm.get_proper_uri() and "secret" not in fm.library_index_uri()
    assert "secret" not in str(index) and "secret" not in repr(index)
    assert (index.storage_options["username"], index.storage_options["password"]) == ("user", "secret")
    assert index.name == ".mp3_index.sqlite3"


def test_smb_library_without_a_login_says_so(tmp_path, monkeypatch, capsys):
    # COM-11: with SMB_* unset the URI said "None:None@" and a login was tried
    import importlib
    from settings import settings
    copy_menu = importlib.import_module("menu.song_actions.copy_songs_from_storage")
    _set_library(monkeypatch, "smb://nas/share/Music")
    monkeypatch.setattr(settings, "smb_password", None)

    def select(*args, **kwargs):
        raise AssertionError("nothing to ask without a login")
    monkeypatch.setattr(copy_menu.questionary, "select", select)

    assert copy_menu.copy_songs_from_storage([("Artist", "Song")]) == "Back"
    assert "SMB_USERNAME / SMB_PASSWORD aren't set" in capsys.readouterr().out


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


@pytest.mark.parametrize("choice, typed", [("Open file manager", None), (None, None),
                                           ("Type path manually", "  ")])
def test_copy_destination_cancelled_or_first_use_copies_nothing(tmp_path, monkeypatch, choice, typed):
    # UI-09: "Open file manager" with no recent folder was load_recent_dirs()[0]
    # (IndexError); Esc in the browser or the list, or an empty path, went on
    # to copy into UPath(None) / UPath("")
    import importlib
    from settings import settings
    copy_menu = importlib.import_module("menu.song_actions.copy_songs_from_storage")
    (tmp_path / ".mp3_index.sqlite3").write_bytes(b"")
    _set_library(monkeypatch, str(tmp_path))
    monkeypatch.setattr(copy_menu, "load_recent_dirs", lambda: [])
    monkeypatch.setattr(copy_menu.questionary, "select", lambda *a, **k: SimpleNamespace(ask=lambda: choice))
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)
    started_in = []
    monkeypatch.setattr(copy_menu, "open_file_browser_terminal", lambda start: started_in.append(start))

    def must_not_run(*args):
        raise AssertionError("nothing chosen, nothing saved or copied")
    monkeypatch.setattr(copy_menu, "save_recent_dirs", must_not_run)
    monkeypatch.setattr(copy_menu, "copy_songs_using_index", must_not_run)

    assert copy_menu.copy_songs_from_storage([("Artist", "Song")]) == "Back"
    assert started_in == ([str(settings.export_dir)] if choice == "Open file manager" else [])


# ---- Fetch songs / artists: Esc in the category list (UI-08) ----

@pytest.mark.parametrize("menu", ["fetch_songs", "fetch_artists"])
def test_esc_in_the_search_category_goes_back(monkeypatch, menu):
    import importlib
    fetch = importlib.import_module("menu.main_menu.enter_database.fetch_songs")
    monkeypatch.setattr(fetch.questionary, "select", lambda *a, **k: SimpleNamespace(ask=lambda: None))

    def must_not_ask(*args, **kwargs):
        raise AssertionError("Esc means back - nothing more is asked or searched")
    monkeypatch.setattr("builtins.input", must_not_ask)
    monkeypatch.setattr(fetch, "get_songs_from_db_session", must_not_ask)
    monkeypatch.setattr(fetch, "get_artists_from_db_session", must_not_ask)

    assert getattr(fetch, menu)() is None


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
