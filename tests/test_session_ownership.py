"""Who commits (AGENTS.md -> "Sessions and commits"): the data layer works
in the session it is given and never commits - moods.py's simple operations,
add_db_entry(), add_song_for_file() - so a rollback undoes them; the menu,
the import batch and the two mood steps that write files outside tag.db do.
Lookups use the caller's session, not the global one (find_matching_artist(),
LocalSongMatcher, credit_feat_artists(), get_artists_from_db_session()),
and tag_session_for() picks the tag session for moods.py and
apply_song_id_changes(). In-memory SQLite."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.constants import SONG_KIND_FULL_ALBUM
from utils.database import database_getter, database_management, database_sessions, moods
from utils.database.datatables import Artist, Base, Song
from utils.database.song_artists import song_all_artists
from utils.database.tag_db_manager import Base as TagBase, Tag


def _session(base=Base):
    engine = create_engine("sqlite:///:memory:")
    base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def music():
    session = _session()
    yield session
    session.close()


@pytest.fixture
def other_global(monkeypatch):
    """A global music session holding a different database than the one
    a test passes - a lookup that reads it finds the wrong artists."""
    session = _session()
    session.add(Artist(name="Hey"))
    session.commit()
    monkeypatch.setattr(database_sessions, "_GLOBAL_DATABASE_SESSION", (session, None))
    yield session
    session.close()


# ---- moods.py: the caller commits ------------------------------------------------

def test_mood_operations_leave_the_commit_to_the_caller():
    tag = _session(TagBase)
    mood_id = moods.create_global_mood(tag, "Gym")
    moods.add_song_to_global_mood(tag, mood_id, 7)
    tag.rollback()
    assert tag.query(Tag).count() == 0

    mood_id = moods.create_global_mood(tag, "Gym")
    tag.commit()
    moods.rename_global_mood(tag, mood_id, "Siłownia")
    moods.add_song_to_global_mood(tag, mood_id, 7)
    tag.rollback()
    assert [t.name for t in tag.query(Tag)] == ["Gym"]
    assert moods.global_mood_song_ids(tag, mood_id) == []
    tag.close()


def test_tag_session_for_prefers_the_given_then_the_global_session(monkeypatch):
    given, global_tag = object(), object()
    monkeypatch.setattr(database_sessions, "_GLOBAL_DATABASE_SESSION", (None, global_tag))
    with database_sessions.tag_session_for(given) as session:
        assert session is given
    with database_sessions.tag_session_for() as session:
        assert session is global_tag

    own = SimpleNamespace(closed=False)
    own.close = lambda: setattr(own, "closed", True)
    monkeypatch.setattr(database_sessions, "_GLOBAL_DATABASE_SESSION", None)
    monkeypatch.setattr(database_sessions, "get_tag_session", lambda: own)
    with database_sessions.tag_session_for() as session:
        assert session is own and not own.closed
    assert own.closed


# ---- add_db_entry() and the import ------------------------------------------------

def test_add_db_entry_flushes_in_the_given_session_without_committing(music, other_global):
    artist = database_management.add_db_entry(Artist(name="ABBA"), music)
    song = database_management.add_db_entry(Song(title="Waterloo", artist_id=artist.id), music)
    assert artist.id and song.id
    music.rollback()
    assert music.query(Artist).count() == 0
    assert other_global.query(Artist).count() == 1  # untouched


def test_add_song_for_file_marks_a_full_album_and_leaves_the_commit(music, other_global):
    from utils.discoveries.folder_playlist import add_song_for_file

    song = add_song_for_file(music, "Bonobo", "Migration (Full Album)")
    assert (song.kind, song.artist.name) == (SONG_KIND_FULL_ALBUM, "Bonobo")
    music.rollback()
    assert music.query(Song).count() == 0 and music.query(Artist).count() == 0


# ---- lookups in the caller's session -----------------------------------------------

def test_artist_lookups_use_the_callers_session(music, other_global):
    from utils.discoveries.import_engine import LocalSongMatcher, find_matching_artist

    hey = Artist(name="Hey")
    karma = Song(title="Karma", artist=hey)
    music.add(karma)
    music.commit()

    assert find_matching_artist("Hey", music) is hey
    assert find_matching_artist("hey ", music) is hey  # through the fuzzy search
    assert database_getter.get_artists_from_db_session("artist name", "He", session=music) == [hey]
    assert LocalSongMatcher(music).match({"artist_name": "Hey", "title": "Karma"}) is karma


def test_credit_feat_artists_reuses_the_artist_of_the_songs_session(music, other_global):
    from utils.database.song_duplicates import credit_feat_artists

    bruno = Artist(name="Bruno Mars")
    song = Song(title="Billionaire", artist=Artist(name="Travie McCoy"))
    music.add_all([bruno, song])
    music.commit()

    credit_feat_artists(music, song, ["Bruno Mars", "Hey"])
    music.commit()

    assert [a.name for a in song_all_artists(song)] == ["Travie McCoy", "Bruno Mars", "Hey"]
    assert music.query(Artist).filter(Artist.name == "Bruno Mars").count() == 1
    assert other_global.query(Artist).count() == 1  # "Hey" made here, not taken from there


def test_get_artists_always_returns_a_list(music):
    music.add(Artist(name="ABBA"))
    music.commit()
    assert database_getter.get_artists_from_db_session("artist name", session=music) == []
    assert database_getter.get_artists_from_db_session("artist name", "  ", session=music) == []
    assert [a.name for a in database_getter.get_artists_from_db_session(session=music)] == ["ABBA"]


# ---- the menu commits ------------------------------------------------------------------

def test_removing_a_song_by_hand_is_saved(music, monkeypatch):
    from menu.song_actions import edit_songs

    song = Song(title="Waterloo", artist=Artist(name="ABBA"))
    music.add(song)
    music.commit()
    saved = []
    monkeypatch.setattr(database_sessions, "_GLOBAL_DATABASE_SESSION", (music, None))
    monkeypatch.setattr(edit_songs, "search_and_pick_db_object", lambda mode: song)
    monkeypatch.setattr(edit_songs, "ask_yes_no", lambda question: True)
    monkeypatch.setattr(edit_songs, "submit_global_database_session", lambda: saved.append(True) or music.commit())

    edit_songs.remove_song_menu("Song")

    assert saved and music.query(Song).count() == 0


def test_the_mood_menu_saves_a_new_mood(monkeypatch):
    import importlib
    menu = importlib.import_module("menu.main_menu.users.moods")
    tag = _session(TagBase)
    saved = []
    monkeypatch.setattr(menu, "open_and_set_global_database_sessions", lambda: (None, tag))
    monkeypatch.setattr(menu, "submit_global_database_session", lambda: saved.append(True) or tag.commit())
    monkeypatch.setattr(menu.questionary, "text", lambda *a, **k: SimpleNamespace(ask=lambda: "Gym"))

    menu._create()

    tag.rollback()
    assert saved and [t.name for t in tag.query(Tag)] == ["Gym"]
    tag.close()
