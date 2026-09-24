import importlib
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

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
    monkeypatch.setattr(rd, "get_music_session", lambda: SessionLocal())
    return SessionLocal


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
