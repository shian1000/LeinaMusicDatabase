"""Latin forms of names per transliteration method (music.db's
name_transliterations, migration 0027; utils/database/transliterations.py):
the migration against the model (triggers included), what romanize gives
per method (both Ukrainian ones), the refresh keeping the owner's rows,
renames / deletions / merges, the duplicate additional names and their
review, and search / matching finding artists by a transliteration.
In-memory SQLite, no mocking of SQL."""

import sqlite3
import sys
from pathlib import Path

import pytest
import questionary
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config.constants import TRANSLITERATION_SOURCE_OWNER, TRANSLITERATION_SOURCE_ROMANIZE
from utils.database import albums as al
from utils.database import database_getter as g
from utils.database import song_artists as sa
from utils.database import transliterations as tr
from utils.database.datatables import Artist, Base, NameTransliteration, Song
from utils.database.latin_names import artist_match_synonyms
from utils.discoveries import import_engine as ie
from utils.ui import synonym_duplicate_review as review


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


def _artist(session, name, **kw):
    artist = Artist(name=name, **kw)
    session.add(artist)
    session.flush()
    return artist


def _forms(session, artist) -> dict:
    session.expire(artist, ["transliterations"])
    return {t.method: (t.value, t.source) for t in artist.transliterations}


def _owner_row(session, artist, method, value):
    session.add(NameTransliteration(table_name="artists", row_id=artist.id, column_name="name", method=method,
                                    value=value, source=TRANSLITERATION_SOURCE_OWNER))
    session.flush()


# ---- the migration ---------------------------------------------------------------

def test_migration_matches_the_model(tmp_path):
    conn = sqlite3.connect(":memory:")
    for path in sorted((repo_root / "migrations" / "music").glob("*.sql")):
        conn.executescript(path.read_text())
    migrated = {row[1]: (bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(name_transliterations)")}
    triggers = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' "
                                               "AND name LIKE 'name_transliterations%'")}

    engine = create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    Base.metadata.create_all(engine)
    modelled = {c["name"]: (not c["nullable"], bool(c["primary_key"]))
                for c in inspect(engine).get_columns("name_transliterations")}
    with engine.connect() as model:
        model_triggers = {row[0] for row in model.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'name_transliterations%'")}
    assert migrated == modelled
    assert triggers == model_triggers and len(triggers) == 6


def test_migration_triggers_follow_deletions_and_renames():
    conn = sqlite3.connect(":memory:")
    for path in sorted((repo_root / "migrations" / "music").glob("*.sql")):
        conn.executescript(path.read_text())
    conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Кино'), (2, 'Ленинград')")
    rows = [("artists", 1, "name", "ru-english", "Kino", "romanize"), ("artists", 1, "name", "x", "Kinó", "owner"),
            ("artists", 2, "name", "ru-english", "Leningrad", "romanize")]
    conn.executemany("INSERT INTO name_transliterations (table_name, row_id, column_name, method, value, source) "
                     "VALUES (?, ?, ?, ?, ?, ?)", rows)
    with pytest.raises(sqlite3.IntegrityError):  # one row per method
        conn.execute("INSERT INTO name_transliterations (table_name, row_id, column_name, method, value, source) "
                     "VALUES ('artists', 1, 'name', 'x', 'Kino', 'owner')")

    conn.execute("UPDATE artists SET origin = 'Russia' WHERE id = 1")  # not a rename
    conn.execute("UPDATE artists SET name = 'КИНО' WHERE id = 1")
    conn.execute("DELETE FROM artists WHERE id = 2")
    assert conn.execute("SELECT row_id, value FROM name_transliterations").fetchall() == [(1, "Kinó")]


# ---- what romanize gives -----------------------------------------------------------

@pytest.mark.parametrize("name, language, origin, expected", [
    ("Океан Ельзи", None, "Ukraine", {"uk-national": "Okean Elzy", "uk-slavic": "Okean Eľzy"}),
    ("Тінь Сонця", None, None, {"uk-national": "Tin Sontsia", "uk-slavic": "Tiń Soncia"}),  # і settles it
    ("Кино", "rus", None, {"ru-english": "Kino"}),
    ("Ляпис Трубецкой", "bel", None, {"be-lacinka": "Lapis Trubieckoj"}),
    ("宇多田ヒカル", None, None, {"ja-hepburn": "Utada Hikaru"}),
    ("정국", None, None, {"ko-revised": "Jeongguk"}),
    ("حبيبي", "ara", None, {}),  # Arabic's vowels are a guess
    ("Kino", None, None, {}),
])
def test_worked_out_forms(name, language, origin, expected):
    assert tr.worked_out_forms(name, language, origin) == expected


def test_refresh_fills_non_latin_artists_and_is_idempotent(session):
    okean = _artist(session, "Океан Ельзи", origin="Ukraine")
    kino = _artist(session, "Кино", origin="Russia")
    _artist(session, "Kino Latin")

    assert tr.refresh_artist_transliterations(session) == (3, 0, 0)
    assert tr.refresh_artist_transliterations(session) == (0, 0, 0)
    assert _forms(session, okean) == {"uk-national": ("Okean Elzy", "romanize"),
                                      "uk-slavic": ("Okean Eľzy", "romanize")}
    assert _forms(session, kino) == {"ru-english": ("Kino", "romanize")}


def test_refresh_keeps_the_owners_rows_and_drops_stale_automatic_ones(session):
    artist = _artist(session, "Океан Ельзи", origin="Ukraine")
    _owner_row(session, artist, "uk-slavic", "Okean Elzy (owner)")
    tr.refresh_artist_transliterations(session)
    assert _forms(session, artist)["uk-slavic"] == ("Okean Elzy (owner)", TRANSLITERATION_SOURCE_OWNER)

    artist.origin, artist.name = "Russia", "Кино"  # no longer Ukrainian
    session.flush()
    assert tr.refresh_artist_transliterations(session) == (1, 0, 0)  # the rename trigger took uk-national
    assert _forms(session, artist) == {"ru-english": ("Kino", TRANSLITERATION_SOURCE_ROMANIZE),
                                       "uk-slavic": ("Okean Elzy (owner)", TRANSLITERATION_SOURCE_OWNER)}


def test_deleting_an_artist_drops_its_rows(session):
    artist = _artist(session, "Кино")
    tr.refresh_artist_transliterations(session)
    session.delete(artist)
    session.flush()
    assert session.query(NameTransliteration).count() == 0


def test_artist_merge_carries_the_owners_forms(session):
    keep, drop = _artist(session, "Кино"), _artist(session, "КИНО")
    _owner_row(session, drop, "ru-english", "Kinó")
    _owner_row(session, drop, "be-lacinka", "Kino BY")
    tr.refresh_artist_transliterations(session)
    assert _forms(session, keep) == {"ru-english": ("Kino", "romanize")}

    sa.merge_artist_synonyms(keep, drop)
    session.delete(drop)
    session.flush()
    assert _forms(session, keep) == {"be-lacinka": ("Kino BY", "owner"), "ru-english": ("Kinó", "owner")}
    assert session.query(NameTransliteration).count() == 2


# ---- search and matching -----------------------------------------------------------

def test_search_and_matching_find_an_artist_by_a_transliteration(session):
    okean = _artist(session, "Океан Ельзи", origin="Ukraine")
    song = Song(title="Обійми", artist=okean)
    session.add(song)
    tr.refresh_artist_transliterations(session)
    session.commit()

    assert [a.name for a in g.get_artists_from_db_session("artist name", "Eľzy")] == ["Океан Ельзи"]
    assert [s.title for s in g.get_songs_from_db_session("artist name", "okean elzy")] == ["Обійми"]
    assert "okean elzy" in g.SongNormalizedFields.artist_name(song)
    assert ie.find_matching_artist("Okean Eľzy") is okean
    # "Okean Eľzy" is "Okean Elzy" once normalized: one is enough
    assert artist_match_synonyms(okean) == "Okean Elzy"
    khochu = _artist(session, "Хочу ЩЕ!", origin="Ukraine", synonyms="Khochu SHCHE, KhSh")
    tr.refresh_artist_transliterations(session)
    assert artist_match_synonyms(khochu) == "Khochu SHCHE, KhSh, Choču ŠČE!"


# ---- duplicate additional names -------------------------------------------------------

def test_duplicate_synonyms_exact_and_alike(session):
    okean = _artist(session, "Океан Ельзи", origin="Ukraine",
                    synonyms="Okean Elzy, OE, Океан Ельзи, OE, Okean Elzi, okean ELZY")
    _artist(session, "Rosalía", synonyms="ROSALÍA, Rosalia")
    tr.refresh_artist_transliterations(session)

    found = [(d.artist.name, d.synonym, d.of, d.exact) for d in tr.duplicate_synonyms(session)]
    assert found == [
        ("Rosalía", "ROSALÍA", "its name", False),
        ("Rosalía", "Rosalia", "its name", False),
        ("Океан Ельзи", "Okean Elzy", "uk-national", True),
        ("Океан Ельзи", "Океан Ельзи", "its name", True),
        ("Океан Ельзи", "OE", "another additional name", True),
        ("Океан Ельзи", "okean ELZY", "uk-national", False),
    ]
    assert tr.remove_synonyms([d for d in tr.duplicate_synonyms(session) if d.exact]) == 3
    assert okean.synonyms == "OE, Okean Elzi, okean ELZY"


def test_review_removes_exact_repeats_and_only_the_ticked_alike_ones(session, monkeypatch, capsys):
    okean = _artist(session, "Океан Ельзи", origin="Ukraine", synonyms="Okean Elzy, OKEAN ELZY")
    rosalia = _artist(session, "Rosalía", synonyms="ROSALÍA")
    monkeypatch.setattr(review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(review, "submit_global_database_session", session.commit)
    asked = {}

    class _Checkbox:
        def __init__(self, question, choices):
            asked["choices"] = choices

        def ask(self):
            return [c.value for c in asked["choices"] if "OKEAN" in str(c.title)]

    monkeypatch.setattr(questionary, "checkbox", _Checkbox)
    assert review.review_duplicate_synonyms() == 2
    assert not any(c.checked for c in asked["choices"])  # nothing ticked at first
    assert okean.synonyms is None and rosalia.synonyms == "ROSALÍA"
    assert "Transliterations of artists' names: 2 added" in capsys.readouterr().out


def test_the_sharing_copy_refreshes_them_first(tmp_path, monkeypatch):
    from utils.database import sharing

    music_db = tmp_path / "music.db"
    conn = sqlite3.connect(music_db)
    for path in sorted((repo_root / "migrations" / "music").glob("*.sql")):
        conn.executescript(path.read_text())
    conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Кино')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(sharing, "DB_FILES", {"music": music_db})

    sharing._refresh_transliterations()
    conn = sqlite3.connect(music_db)
    assert conn.execute("SELECT row_id, method, value, source FROM name_transliterations").fetchall() == \
        [(1, "ru-english", "Kino", "romanize")]
    conn.close()
