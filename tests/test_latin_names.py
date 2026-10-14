"""Latin forms of names in other alphabets (songs.title_latin,
artists.name_latin, albums.title_latin; utils/database/latin_names.py):
suggestions (utils/common/romanize.py), migration 0011, what's still
missing, search and matching using them, merges keeping them, a "Singles"
album copying its song's form, and the YouTube retry with the Latin title.
In-memory SQLite with the album listener on, no mocking of SQL."""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config.constants import SINGLES_ALBUM
from utils.common.romanize import suggest_latin
from utils.database import albums as al
from utils.database import database_getter as g
from utils.database import latin_names as ln
from utils.database import song_artists as sa
from utils.database.datatables import Album, Artist, Base, Song
from utils.discoveries import import_engine as ie
from utils.youtube import manage_youtube_playlists as m

# See test_resolve_duplicates.py for why this needs importlib
rd = importlib.import_module("menu.main_menu.enter_database.manage_database.resolve_duplicates")


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    al.register_album_sync(factory)
    return factory


@pytest.fixture
def session(session_factory, monkeypatch):
    s = session_factory()
    monkeypatch.setattr(g, "get_global_database_sessions", lambda: (s, None))
    monkeypatch.setattr(ie, "open_and_set_global_database_sessions", lambda: (s, None))
    yield s
    s.close()


def _artist(session, name, **kw):
    artist = Artist(name=name, **kw)
    session.add(artist)
    session.flush()
    return artist


def _song(session, title, artist, **kw):
    song = Song(title=title, artist=artist, **kw)
    session.add(song)
    session.flush()
    return song


# ---- suggestions ------------------------------------------------------------

@pytest.mark.parametrize("text, language, origin, expected", [
    # English-style Russian, as the owner spelled names before
    ("Кино", None, None, "Kino"),
    ("Группа крови", "Russian", None, "Gruppa krovi"),
    ("Земфира", None, None, "Zemfira"),
    ("Алёна", None, None, "Alyona"),
    ("ЖУКИ", None, None, "ZHUKI"),
    ("Чайковский", None, None, "Chaykovsky"),
    ("Елена", None, None, "Yelena"),
    # Ukrainian national system: y- forms only at the start of a word, зг -> zgh
    ("Океан Ельзи", "Ukrainian", None, "Okean Elzy"),
    ("Мене вже немає", None, None, "Mene vzhe nemaie"),
    ("Їжак Згода", None, None, "Yizhak Zghoda"),
    ("Зелені очі", None, None, "Zeleni ochi"),
    # Belarusian Lacinka: by the language field, its own letters or the origin
    ("Іголкі", "Belarusian", None, "Ihołki"),
    ("Шэры камень", None, None, "Šery kamień"),
    ("Ціха", None, "Belarus - White Ruthenia - Minsk", "Cicha"),
    ("Ціха", None, None, "Tsikha"),  # і alone: Ukrainian unless the origin says otherwise
    # Russian words of a Belarusian band fall back to Russian for и
    ("Ляпис Трубецкой", "Belarusian", None, "Lapis Trubieckoj"),
    ("Бумбокс", None, "Ukraine", "Bumboks"),
    ("Ђорђе Балашевић", "Serbian", None, "Đorđe Balašević"),
    ("Μπουζούκι", None, None, "Bouzouki"),
    ("ქართული", None, None, "Kartuli"),
    ("방탄소년단", None, None, "Bangtansonyeondan"),
    ("きゃりーぱみゅぱみゅ", None, None, "Kyaripamyupamyu"),
    ("ちょっと", None, None, "Chotto"),
    ("ファンタジー", None, None, "Fantaji"),
])
def test_suggests_a_readable_latin_form(text, language, origin, expected):
    assert suggest_latin(text, language, origin) == expected


@pytest.mark.parametrize("text", ["君の名は", "حبيبي", "Rock", "Bôa", "", None])
def test_no_suggestion_for_kanji_arabic_or_latin_names(text):
    assert suggest_latin(text) is None


# ---- migration ----------------------------------------------------------------

def test_migration_adds_the_model_columns():
    conn = sqlite3.connect(":memory:")
    for path in sorted((repo_root / "migrations" / "music").glob("*.sql")):
        if path.name < "0011":
            conn.executescript(path.read_text())
    conn.executescript((repo_root / "migrations" / "music" / "0011_latin_names.sql").read_text())

    for table, model in (("songs", Song), ("artists", Artist), ("albums", Album)):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert columns == {c.name for c in model.__table__.columns}, table


# ---- storing and the synonym view ---------------------------------------------

def test_set_latin_form_trims_and_empties_to_null(session):
    artist = _artist(session, "Кино")
    assert ln.set_latin_form(artist, "  Kino ") is None
    assert artist.name_latin == "Kino"
    assert ln.set_latin_form(artist, "   ") == "Kino"
    assert artist.name_latin is None


def test_latin_form_is_the_first_synonym_for_matching(session):
    artist = _artist(session, "Бумбокс", synonyms="familyboombox, Bumboks", name_latin="Boombox")
    assert ln.artist_match_synonyms(artist) == "Boombox, familyboombox, Bumboks"
    artist.name_latin = "bumboks"  # already a synonym (normalized): not repeated
    assert ln.artist_match_synonyms(artist) == "familyboombox, Bumboks"
    assert ln.with_latin_synonym(None, "Kino") == "Kino"
    assert ln.with_latin_synonym("a, b", None) == "a, b"


def test_credits_carry_the_latin_form_as_a_synonym(session):
    kino, guest = _artist(session, "Кино", name_latin="Kino"), _artist(session, "Guest")
    song = _song(session, "Звезда", kino)
    sa.add_additional_artist(song, guest, "feat")
    session.flush()
    assert sa.song_artist_credits(song)["main"][0] == ["Кино", "Kino"]


# ---- what's still missing -------------------------------------------------------

def test_missing_latin_forms_lists_non_latin_names_without_a_form(session):
    kino = _artist(session, "Кино")
    done = _artist(session, "Земфира", name_latin="Zemfira")
    _artist(session, "Vulfpeck")
    _song(session, "Группа крови", kino, album="Группа крови", language="Russian")
    _song(session, "Хочу перемен", kino, album=SINGLES_ALBUM)  # its single album copies the song's form
    _song(session, "Искала", done, title_latin="Iskala", album="14 недель тишины")
    _song(session, "君の名は", done)
    session.flush()

    found = ln.missing_latin_forms(session)

    assert [(c.kind, c.name, c.suggestion) for c in found] == [
        (ln.ARTIST, "Кино", "Kino"),
        (ln.ALBUM, "14 недель тишины", "14 nedel tishiny"),  # by artist: Земфира before Кино
        (ln.ALBUM, "Группа крови", "Gruppa krovi"),
        (ln.SONG, "君の名は", None),
        (ln.SONG, "Группа крови", "Gruppa krovi"),
        (ln.SONG, "Хочу перемен", "Khochu peremen"),
    ]
    assert [c.name for c in ln.missing_latin_forms(session, (ln.ARTIST,))] == ["Кино"]


# ---- search ---------------------------------------------------------------------

def test_search_finds_an_artist_and_its_songs_by_the_latin_form(session):
    kino = _artist(session, "Кино", name_latin="Kino")
    _artist(session, "Kinoteka")
    _song(session, "Звезда по имени Солнце", kino)
    session.commit()

    assert {a.name for a in g.get_artists_from_db_session("artist name", "Kino")} == {"Кино", "Kinoteka"}
    assert [s.title for s in g.get_songs_from_db_session("artist name", "kino")] == ["Звезда по имени Солнце"]
    assert [s.title for s in g.get_songs_from_db_session("name", "Kino")] == ["Звезда по имени Солнце"]


def test_search_finds_songs_by_latin_title_and_album(session):
    kino = _artist(session, "Кино")
    song = _song(session, "Группа крови", kino, title_latin="Gruppa krovi", album="Группа крови")
    song.album_entry.title_latin = "Gruppa krovi (album)"
    _song(session, "Другая", kino, album="Другое")
    session.commit()

    assert [s.title for s in g.get_songs_from_db_session("title", "gruppa krovi")] == ["Группа крови"]
    assert [s.title for s in g.get_songs_from_db_session("name", "Gruppa")] == ["Группа крови"]
    assert [s.title for s in g.get_songs_from_db_session("album", "(album)")] == ["Группа крови"]


def test_python_fallback_fields_include_the_latin_forms(session):
    kino = _artist(session, "Кино", name_latin="Kinó")
    song = _song(session, "Группа крови", kino, title_latin="Gruppa krovi", album="Группа крови")
    song.album_entry.title_latin = "Gruppa krovi LP"
    session.flush()

    assert "gruppa krovi" in g.SongNormalizedFields.title(song)
    assert "lp" in g.SongNormalizedFields.album(song)
    assert "kino" in g.SongNormalizedFields.artist_name(song)
    assert "gruppa" in g.SongNormalizedFields.general_search(song)


# ---- matching -------------------------------------------------------------------

def test_import_matches_an_artist_by_its_latin_form(session):
    kino = _artist(session, "Кино", name_latin="Kino")
    session.commit()
    assert ie.find_matching_artist("kino") is kino
    assert ie.find_matching_artist("KINO") is kino


def test_import_matches_a_song_by_its_latin_title(session):
    kino = _artist(session, "Кино", name_latin="Kino")
    song = _song(session, "Группа крови", kino, title_latin="Gruppa krovi")
    session.commit()

    assert ie.find_existing_song("Gruppa Krovi", [song]) is song
    assert ie.LocalSongMatcher(session).match({"artist_name": "Kino", "title": "Gruppa krovi (Official Video)"}) is song
    assert ie.find_songs_by_title(session, "gruppa krovi") == [song]


def test_youtube_search_retries_with_the_latin_title(monkeypatch):
    tried = []

    def once(artist, title, *args):
        tried.append(title)
        return ("latin-vid", 1.0, True) if title == "Gruppa krovi" else (None, 0.0, False)
    monkeypatch.setattr(m, "_search_video_ytdlp_once", once)

    assert m.search_video_ytdlp("Кино", "Группа крови", title_latin="Gruppa krovi") == "latin-vid"
    assert tried == ["Группа крови", "Gruppa krovi"]


def test_alternative_titles_skip_repeats():
    assert m._alternative_titles("Ihołki", "Belarusian", "Ihołki") == ["Іголкі"]
    assert m._alternative_titles("Song", None, " ") == []


# ---- merges and singles -----------------------------------------------------------

def test_artist_merge_keeps_a_latin_form(session):
    keep, drop = _artist(session, "Кино"), _artist(session, "КИНО", name_latin="Kino")
    sa.merge_artist_synonyms(keep, drop)
    assert keep.name_latin == "Kino" and keep.synonyms is None  # "КИНО" is the same name

    other = _artist(session, "Кіно", name_latin="Kino Band")
    sa.merge_artist_synonyms(keep, other)
    assert keep.name_latin == "Kino"  # the kept one's stays, the other's becomes a synonym
    assert keep.synonyms == "Кіно, Kino Band"

    same = _artist(session, "Kinofilm", name_latin="Kinó")  # same as the kept form once normalized
    sa.merge_artist_synonyms(keep, same)
    assert keep.synonyms == "Кіно, Kino Band, Kinofilm"


def test_album_merge_keeps_a_latin_form(session, monkeypatch, tmp_path):
    monkeypatch.setattr(al, "covers_dir", lambda: tmp_path)
    kino = _artist(session, "Кино")
    keep = Album(title="Группа крови", artist=kino)
    drop = Album(title="Группа Крови", artist=kino, title_latin="Gruppa krovi")
    session.add_all([keep, drop])
    session.flush()

    al.merge_albums(session, keep, drop)

    assert keep.title_latin == "Gruppa krovi"


def test_song_dedup_keeps_a_latin_form(session_factory, monkeypatch):
    monkeypatch.setattr(rd, "get_music_session", lambda: session_factory())
    monkeypatch.setattr(rd, "apply_song_id_changes", lambda: None)
    s = session_factory()
    kino = _artist(s, "Кино")
    kept = _song(s, "Кукушка", kino)
    _song(s, "Кукушка", kino, title_latin="Kukushka")
    s.commit()
    kept_id = kept.id
    s.close()

    rd.remove_duplicate_songs()

    s = session_factory()
    [song] = s.query(Song).all()
    assert song.id == kept_id and song.title_latin == "Kukushka"
    s.close()


def test_single_album_copies_its_songs_latin_form(session):
    kino = _artist(session, "Кино")
    song = _song(session, "Кукушка", kino, album=SINGLES_ALBUM)
    session.flush()
    assert song.album_entry.title_latin is None

    song.title_latin = "Kukushka"
    session.flush()

    assert song.album_entry.title_latin == "Kukushka"
