"""The search layer (src/utils/database/database_getter.py) on an
in-memory music.db: searching artists by id, songs by artist id and year,
and an unknown category."""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import database_getter as g
from utils.database.datatables import Artist, Base, Song


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    monkeypatch.setattr(g, "require_global_database_sessions", lambda: (s, None))
    yield s
    s.close()


def _artists(session, count):
    session.add_all(Artist(id=i, name=f"Artist {i}") for i in range(1, count + 1))
    session.commit()


def test_artist_search_by_id_finds_exactly_that_artist(session):
    _artists(session, 15)

    assert [a.name for a in g.get_artists_from_db_session("artist id", "5")] == ["Artist 5"]
    assert [a.name for a in g.get_artists_from_db_session("artist id", " 15 ")] == ["Artist 15"]
    assert g.get_artists_from_db_session("artist id", "99") == []


@pytest.mark.parametrize("query", ["five", "5a", "²", "-1"])
def test_artist_search_by_id_refuses_what_isnt_a_number(session, query, capsys):
    _artists(session, 5)

    assert g.get_artists_from_db_session("artist id", query) == []
    assert "isn't an artist id" in capsys.readouterr().out


def test_song_search_by_artist_id_never_finds_other_ids_containing_it(session):
    # DB-04: artist 5 has no songs - the substring fallback used to return the songs of 15 and 50
    _artists(session, 5)
    session.add_all([Artist(id=15, name="Artist 15"), Artist(id=50, name="Artist 50")])
    session.add_all([Song(title="Fifteen", artist_id=15), Song(title="Fifty", artist_id=50)])
    session.commit()

    assert g.get_songs_from_db_session("artist id", "5") == []
    assert [s.title for s in g.get_songs_from_db_session("artist id", 15)] == ["Fifteen"]


def test_song_search_by_year_is_exact_and_refuses_what_isnt_a_number(session, capsys):
    # DB-03: "199x" ended in a ValueError
    _artists(session, 1)
    session.add_all([Song(title="Old", artist_id=1, year=1999), Song(title="New", artist_id=1, year=2019)])
    session.commit()

    assert [s.title for s in g.get_songs_from_db_session("year", "1999")] == ["Old"]
    assert g.get_songs_from_db_session("year", "99") == []
    for query in ("199x", "lata 80", "²"):
        assert g.get_songs_from_db_session("year", query) == []
        assert "isn't a year" in capsys.readouterr().out


def test_an_unknown_category_finds_nothing_instead_of_a_keyerror(session, capsys):
    # DB-22: _validate_category() printed the valid options and returned None,
    # which every caller used as a dict key
    _artists(session, 1)
    session.add(Song(title="Old", artist_id=1))
    session.commit()

    assert g.get_songs_from_db_session("colour", "red") == []
    assert g.get_artists_from_db_session("colour", "red") == []
    assert g.get_songs_with_empty_category("colour") == []
    assert capsys.readouterr().out.count("Invalid category 'colour'") == 3

