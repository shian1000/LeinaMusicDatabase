"""The search layer (src/utils/database/database_getter.py) on an
in-memory music.db: searching artists by id."""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import database_getter as g
from utils.database.datatables import Artist, Base


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    monkeypatch.setattr(g, "get_global_database_sessions", lambda: (s, None))
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
