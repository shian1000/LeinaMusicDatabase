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
