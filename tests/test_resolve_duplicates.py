import importlib
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import song_references as sr
from utils.database.datatables import Base, Artist, Song

# The package's own __init__.py defines a *function* also named
# resolve_duplicates(), which shadows the submodule of the same name in the
# package namespace once __init__.py finishes running - a plain `from
# menu...manage_database import resolve_duplicates` would bind that function,
# not this module. Fetch the actual submodule from sys.modules instead.
rd = importlib.import_module("menu.main_menu.enter_database.manage_database.resolve_duplicates")


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    global_session = SessionLocal()
    monkeypatch.setattr(rd, "open_and_set_global_database_sessions", lambda: (global_session, None))
    monkeypatch.setattr(rd, "submit_global_database_session", global_session.commit)
    yield SessionLocal
    global_session.close()


def test_remove_duplicate_artists_merges_artist_matching_a_synonym(session_factory):
    SessionLocal = session_factory
    session = SessionLocal()
    canonical = Artist(name="Akute", synonyms="akuteofficial")
    duplicate = Artist(name="akuteofficial")
    session.add_all([canonical, duplicate])
    session.commit()
    duplicate_song = Song(title="Song A", artist_id=duplicate.id)
    session.add(duplicate_song)
    session.commit()
    canonical_id, duplicate_id = canonical.id, duplicate.id
    session.close()

    rd.remove_duplicate_artists()

    session = SessionLocal()
    artists = session.query(Artist).all()
    assert [a.id for a in artists] == [canonical_id]

    songs = session.query(Song).all()
    assert [s.artist_id for s in songs] == [canonical_id]
    session.close()


def test_remove_duplicate_artists_matches_synonym_case_insensitively(session_factory):
    SessionLocal = session_factory
    session = SessionLocal()
    canonical = Artist(name="Akute", synonyms="AkuteOfficial")
    duplicate = Artist(name="akuteofficial")
    session.add_all([canonical, duplicate])
    session.commit()
    canonical_id = canonical.id
    session.close()

    rd.remove_duplicate_artists()

    session = SessionLocal()
    artists = session.query(Artist).all()
    assert [a.id for a in artists] == [canonical_id]
    session.close()


def test_remove_duplicate_artists_leaves_unrelated_artists_alone(session_factory):
    SessionLocal = session_factory
    session = SessionLocal()
    session.add_all([
        Artist(name="Akute", synonyms="akuteofficial"),
        Artist(name="Some Other Band"),
    ])
    session.commit()
    session.close()

    rd.remove_duplicate_artists()

    session = SessionLocal()
    names = sorted(a.name for a in session.query(Artist).all())
    assert names == ["Akute", "Some Other Band"]
    session.close()


def test_remove_duplicate_artists_still_merges_exact_name_duplicates(session_factory):
    SessionLocal = session_factory
    session = SessionLocal()
    session.add_all([
        Artist(name="Akute"),
        Artist(name="akute"),
    ])
    session.commit()
    session.close()

    rd.remove_duplicate_artists()

    session = SessionLocal()
    artists = session.query(Artist).all()
    assert len(artists) == 1
    session.close()


@pytest.fixture
def both_passes(tmp_path, monkeypatch):
    """A file database reached the way the app reaches it: both passes
    through the long-lived global session - the artists pass's raw SQL
    merges must not leave the songs pass reading what that session loaded
    before them."""
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    sr.register_song_reference_sync(SessionLocal)
    global_session = SessionLocal()
    monkeypatch.setattr(rd, "open_and_set_global_database_sessions", lambda: (global_session, None))
    monkeypatch.setattr(rd, "submit_global_database_session", global_session.commit)
    yield SessionLocal, global_session
    global_session.close()


def _count_rounds(monkeypatch):
    rounds = []
    songs_pass, artists_pass = rd.remove_duplicate_songs, rd.remove_duplicate_artists
    monkeypatch.setattr(rd, "remove_duplicate_songs", lambda *a: rounds.append("songs") or songs_pass(*a))
    monkeypatch.setattr(rd, "remove_duplicate_artists", lambda *a: rounds.append("artists") or artists_pass(*a))
    return rounds


def test_resolve_duplicated_artists_runs_one_round_when_nothing_is_merged(both_passes, monkeypatch, capsys):
    SessionLocal, _ = both_passes
    session = SessionLocal()
    session.add_all([Song(title="Song A", artist=Artist(name="Akute")), Song(title="Song B", artist=Artist(name="Other"))])
    session.commit()
    session.close()
    rounds = _count_rounds(monkeypatch)

    rd.resolve_duplicated_artists()

    assert rounds == ["songs", "artists"]
    out = capsys.readouterr().out
    assert "No duplicate songs found." in out and "No duplicate artists found." in out


def test_resolve_duplicated_artists_stops_after_a_round_without_changes(both_passes, monkeypatch, capsys):
    SessionLocal, _ = both_passes
    session = SessionLocal()
    session.add_all([Artist(name="Akute"), Artist(name="akute")])
    session.commit()
    session.close()
    rounds = _count_rounds(monkeypatch)

    rd.resolve_duplicated_artists()

    assert rounds == ["songs", "artists", "songs", "artists"]
    session = SessionLocal()
    assert session.query(Artist).count() == 1
    session.close()
    # The checking round stays quiet instead of reporting "nothing found" again
    out = capsys.readouterr().out
    assert out.count("No duplicate songs found.") == 1 and "No duplicate artists found." not in out


def test_resolve_duplicated_artists_catches_song_duplicates_an_artist_merge_creates(both_passes, monkeypatch):
    SessionLocal, global_session = both_passes
    session = SessionLocal()
    session.add_all([Song(title="Savana", artist=Artist(name="Sassja")),
                     Song(title="savana", artist=Artist(name="sassja"), youtube_video_id="v1")])
    session.commit()
    session.close()
    loaded = global_session.query(Song).all()  # what the app's global session holds already
    rounds = _count_rounds(monkeypatch)

    rd.resolve_duplicated_artists()

    session = SessionLocal()
    [song] = session.query(Song).all()
    assert song.youtube_video_id == "v1"
    assert session.query(Artist).count() == 1
    session.close()
    assert rounds == ["songs", "artists", "songs", "artists", "songs", "artists"]
    assert len(loaded) == 2
