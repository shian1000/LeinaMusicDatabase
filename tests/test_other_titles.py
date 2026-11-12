"""Other titles of a song (songs.other_titles, migration 0023;
utils/database/other_titles.py): storing them, search and matching by them,
merges keeping both songs'. In-memory SQLite, no mocking of SQL."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import albums as al
from utils.database import database_getter as g
from utils.database import other_titles as ot
from utils.database.datatables import Artist, Base, Song
from utils.database.latin_names import song_titles
from utils.database.song_duplicates import merge_songs
from utils.discoveries import import_engine as ie


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    al.register_album_sync(factory)
    s = factory()
    monkeypatch.setattr(g, "require_global_database_sessions", lambda: (s, None))
    monkeypatch.setattr(ie, "open_and_set_global_database_sessions", lambda: (s, None))
    yield s
    s.close()


def _song(session, title, artist_name="Пётр Ильич Чайковский", **kw):
    artist = session.query(Artist).filter_by(name=artist_name).first() or Artist(name=artist_name)
    song = Song(title=title, artist=artist, **kw)
    session.add(song)
    session.flush()
    return song


def test_set_other_titles_keeps_each_once_without_the_own_title(session):
    song = _song(session, "Херувимская песнь", title_latin="Kheruvimskaya pesn'")
    ot.set_other_titles(song, ["Hymn of the Cherubim", " hymn of the cherubim ", "Херувимская песнь",
                               "Kheruvimskaya pesn'", "", "Cherubic Hymn; sung"])
    assert song.other_titles == "Hymn of the Cherubim; Cherubic Hymn, sung"
    assert ot.other_titles(song) == ["Hymn of the Cherubim", "Cherubic Hymn, sung"]
    ot.set_other_titles(song, [])
    assert song.other_titles is None


def test_add_other_titles_appends(session):
    song = _song(session, "Na jednej z dzikich plaż", "Sztywny Pal Azji")
    ot.add_other_titles(song, "Lubiła tańczyć")
    ot.add_other_titles(song, "Lubiła tańczyć", "Dzikie plaże")
    assert ot.other_titles(song) == ["Lubiła tańczyć", "Dzikie plaże"]
    assert "Lubiła tańczyć" in song_titles(song)


def test_search_finds_a_song_by_another_title(session):
    _song(session, "Херувимская песнь", other_titles="Hymn of the Cherubim; Cherubic Hymn")
    _song(session, "Щелкунчик")
    session.commit()
    assert [s.title for s in g.get_songs_from_db_session("title", "cherubic")] == ["Херувимская песнь"]
    song = session.query(Song).filter_by(title="Херувимская песнь").one()
    assert "hymn of the cherubim" in g.SongNormalizedFields.title(song)


def test_import_matches_a_song_by_another_title(session):
    song = _song(session, "Na jednej z dzikich plaż", "Sztywny Pal Azji", other_titles="Lubiła tańczyć")
    session.commit()
    assert ie.find_existing_song("Lubiła tańczyć", [song]) is song
    assert ie.LocalSongMatcher(session).match(
        {"artist_name": "Sztywny Pal Azji", "title": "Lubiła tańczyć (Official Video)"}) is song


def test_merge_keeps_both_songs_other_titles(session):
    kept = _song(session, "Херувимская песнь", other_titles="Hymn of the Cherubim")
    dropped = _song(session, "Херувимская песня", other_titles="Cherubic Hymn; Hymn of the Cherubim")
    merge_songs(session, kept, dropped)
    session.flush()
    assert ot.other_titles(kept) == ["Hymn of the Cherubim", "Cherubic Hymn"]
