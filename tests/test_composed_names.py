"""Names kept in Unicode NFC: the models compose every write
(datatables.COMPOSED_FIELDS), Clean names composes rows stored decomposed
before (database_management.decomposed_names(),
clean_names.compose_decomposed_letters()), and the album / year lookups hand
the modules composed text. In-memory SQLite with the album listener on; old
decomposed rows are inserted with raw SQL, since the ORM would compose them."""

import importlib
import sys
import unicodedata
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.common.normalizer import composed
from utils.database.albums import register_album_sync
from utils.database.datatables import Album, Artist, Base, Song
from utils.database import database_management
from utils.discoveries import discoveries_manager as dm

DECOMPOSED = "Abrásame"          # "a" + U+0301
COMPOSED = "Abrásame"
DECOMPOSED_ARTIST = "Pete Rodríguez"


def _is_nfc(value):
    return unicodedata.is_normalized("NFC", value)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    register_album_sync(factory)
    s = factory()
    yield s
    s.close()


def _insert_decomposed(session):
    """An artist, a song and an album stored decomposed, as before the validators."""
    session.execute(text("INSERT INTO artists (id, name, synonyms) VALUES (1, :n, :syn)"),
                    {"n": DECOMPOSED_ARTIST, "syn": "Bumboks, Búmboks"})
    session.execute(text("INSERT INTO albums (id, title, artist_id, type) VALUES (7, :t, 1, 'single')"), {"t": DECOMPOSED})
    session.execute(text("INSERT INTO songs (id, title, album, artist_id, album_id) VALUES (3, :t, 'Singles', 1, 7)"),
                    {"t": DECOMPOSED})
    session.commit()


def test_composed_leaves_empty_values_alone():
    assert composed(DECOMPOSED) == COMPOSED
    assert composed(None) is None and composed("") == ""


def test_models_compose_every_write(session):
    artist = Artist(name=DECOMPOSED_ARTIST, origin="México", name_latin=DECOMPOSED)
    song = Song(title=DECOMPOSED, album=DECOMPOSED, genre="Séga", artist=artist)
    session.add(song)
    session.flush()
    album = Album(title=DECOMPOSED, title_latin=DECOMPOSED)
    session.add(album)
    session.commit()

    values = [artist.name, artist.origin, artist.name_latin, song.title, song.album, song.genre,
              album.title, album.title_latin]
    assert all(_is_nfc(v) for v in values)
    assert session.execute(text("SELECT title FROM songs")).scalar() == COMPOSED

    song.title = DECOMPOSED  # a later edit too
    assert song.title == COMPOSED


def test_decomposed_names_lists_rows_stored_before(session):
    _insert_decomposed(session)

    found = {(type(row).__name__, field): (old, new)
             for row, field, old, new in database_management.decomposed_names(session)}

    assert set(found) == {("Artist", "name"), ("Artist", "synonyms"), ("Song", "title"), ("Album", "title")}
    assert found[("Song", "title")] == (DECOMPOSED, COMPOSED)
    assert all(_is_nfc(new) for _, new in found.values())


def _clean_names_module(monkeypatch, session):
    import menu.main_menu  # noqa: F401  (import order, see AGENTS.md)
    # the package's clean_names() function shadows the submodule
    cn = importlib.import_module("menu.main_menu.enter_database.manage_database.clean_names")
    monkeypatch.setattr(cn, "require_global_database_sessions", lambda: (session, None))
    return cn


def test_clean_names_composes_stored_names_without_moving_anything(session, monkeypatch):
    _insert_decomposed(session)
    cn = _clean_names_module(monkeypatch, session)
    monkeypatch.setattr("utils.ui.artist_change.require_global_database_sessions", lambda: (session, None))
    held = {}

    cn.compose_decomposed_letters(held)
    session.commit()

    assert held == {}
    assert database_management.decomposed_names(session) == []
    song = session.get(Song, 3)
    assert song.title == COMPOSED and song.album_id == 7 and song.artist_id == 1
    assert session.get(Artist, 1).name == "Pete Rodríguez"
    assert session.get(Album, 7).title == COMPOSED
    assert session.query(Song).count() == 1 and session.query(Album).count() == 1


def test_clean_names_holds_an_artist_composed_onto_another_artists_name(session, monkeypatch):
    _insert_decomposed(session)
    session.add(Artist(name="Pete Rodríguez"))
    session.commit()
    cn = _clean_names_module(monkeypatch, session)
    monkeypatch.setattr("utils.ui.artist_change.require_global_database_sessions", lambda: (session, None))
    held = {}

    cn.compose_decomposed_letters(held)

    artist = session.get(Artist, 1)
    assert artist.name == DECOMPOSED_ARTIST
    assert held == {1: (artist, "Pete Rodríguez")}


def test_album_lookup_hands_modules_composed_text(monkeypatch):
    calls = []

    class Module:
        @staticmethod
        def get_album_name(artist, title):
            calls.append((artist, title))
            return None

        @staticmethod
        def get_release_year(artist, query, is_single):
            calls.append((artist, query))
            return None

    monkeypatch.setattr(dm, "record_invocation", lambda module_id: None)
    monkeypatch.setattr(dm, "record_year_invocation", lambda module_id: None)
    dm._call_module_match(Module, "m", "Module", DECOMPOSED_ARTIST, DECOMPOSED)
    dm._call_year_module(Module, "m", "Module", DECOMPOSED_ARTIST, DECOMPOSED, True)

    assert calls == [("Pete Rodríguez", COMPOSED)] * 2


def test_clean_names_repairs_polish_letters_and_merges_the_album_twin(session, monkeypatch):
    """"S³oneczna strona miasta" (Windows-1250 read as Latin-1) is the
    artist's "Słoneczna strona miasta": one album row afterwards, both songs
    on it; a song title is repaired too, real accents ("Efêmera") stay."""
    yugo = Artist(name="Yugopolis")
    broken = Song(title="Gdzie są przyjaciele moi?", album="S³oneczna strona miasta", artist=yugo)
    right = Song(title="Czy pamiętasz", album="Słoneczna strona miasta", artist=yugo)
    title = Song(title="¯ycie jest jak tramwaj", album="Singles", artist=yugo)
    fine = Song(title="Efêmera", album="Efêmera", artist=yugo)
    session.add_all([broken, right, title, fine])
    session.commit()
    assert session.query(Album).filter(Album.title.like("S%")).count() == 2
    cn = _clean_names_module(monkeypatch, session)

    cn.repair_polish_mojibake(session.query(Song).all(), {})
    session.commit()

    albums = session.query(Album).filter(Album.title.like("S%oneczna%")).all()
    assert [a.title for a in albums] == ["Słoneczna strona miasta"]
    assert broken.album == "Słoneczna strona miasta" and broken.album_id == right.album_id == albums[0].id
    assert title.title == "Życie jest jak tramwaj"
    assert fine.title == fine.album == "Efêmera"


def test_clean_names_repairs_every_field_of_a_misread_song(session, monkeypatch):
    """The title's "¯" shows the whole tag was mis-read, so the album's
    "Mêskie" (no sure sign of its own) is repaired with it; elsewhere a
    "Mêskie" is repaired only because "Męskie" is a word the database has."""
    various, lao = Artist(name="Various Artists"), Artist(name="Lao Che")
    misread = Song(title="Lao Che / ¯ycie jest jak tramwaj", album="Mêskie Granie 2011-1", artist=various)
    other = Song(title="Warszawa", album="Mêskie Granie 2012", artist=lao)
    known = Song(title="Kto przepłynie", album="Męskie Granie", artist=lao)
    fine = Song(title="Efêmera", album="Efêmera", artist=lao)
    session.add_all([misread, other, known, fine])
    session.commit()
    cn = _clean_names_module(monkeypatch, session)

    cn.repair_polish_mojibake(session.query(Song).all(), {})
    session.commit()

    assert (misread.title, misread.album) == ("Lao Che / Życie jest jak tramwaj", "Męskie Granie 2011-1")
    assert other.album == "Męskie Granie 2012"
    assert fine.title == fine.album == "Efêmera"
    assert session.get(Album, misread.album_id).title == "Męskie Granie 2011-1"
