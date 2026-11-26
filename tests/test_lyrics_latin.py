"""Lyrics in Latin letters: Ukrainian's Slavic alphabet
(romanize.ukrainian_slavic()), lyrics_latin.transliterate_lyrics() line by
line, lyrics_store.refresh_lyrics_latin() keeping lyrics.db's lyrics_latin up
to date, merges dropping a merged song's rows, and the sharing server's
answer carrying them. Temp-file SQLite."""

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.constants import LYRICS_LATIN_DEFAULT, LYRICS_LATIN_UK_SLAVIC
from musicdatabase_core import song_id_replay
from utils.common.lyrics_latin import transliterate_lyrics
from utils.common.romanize import ukrainian_slavic
from utils.database import lyrics_store as store
from utils.database import sharing_server
from utils.database.datatables import Artist, Base, Song


@pytest.fixture
def lyrics_db(tmp_path):
    return store.ensure_lyrics_db(tmp_path / "lyrics.db")


@pytest.fixture
def music(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


# ---- the Slavic alphabet ----

@pytest.mark.parametrize("cyrillic, latin", [
    ("вилазити", "vylazyty"), ("глибокий", "hlybokyj"), ("наливати", "nalyvaty"),  # a hard л is l
    ("більшість", "biľšisť"), ("люди", "liudy"), ("Львів", "Ľviv"), ("ЛЬВІВ", "ĽVIV"),  # a soft one ľ / li-
    ("знаю моя твоє", "znaju moja tvoje"), ("Україна", "Ukrajina"), ("їхати", "jichaty"),
    ("п'ять", "pjať"), ("сім’я", "simja"), ("щастя", "ščastia"), ("ґанок", "ganok"),
    ("день", "deń"), ("весь", "veś"), ("мазь", "maź"), ("місяць", "misiać"), ("мідь", "miď"),  # ь: soft letters
    ("сьогодні", "siohodni"), ("зьома", "zioma"), ("тьотя", "tiotia"), ("цьому", "ciomu"),  # ьо: i-
    ("льон", "lion"), ("ньому", "niomu"), ("трьох", "trioch"),
    ("пісня", "pisnia"), ("сїк", "sik"), ("зєднання", "ziednannia"), ("сюди", "siudy"),  # iotated: i-
])
def test_ukrainian_in_the_slavic_alphabet(cyrillic, latin):
    assert ukrainian_slavic(cyrillic) == latin


# ---- lyrics line by line ----

def test_ukrainian_lyrics_get_both_methods_line_for_line_with_the_time_stamps_kept():
    result = transliterate_lyrics("Люди\n\nБільшість у Львові, ok", "[00:01.00] Люди\n[00:02.50]", "ukr")
    assert result == {
        LYRICS_LATIN_DEFAULT: ("Liudy\n\nBilshist u Lvovi, ok", "[00:01.00] Liudy\n[00:02.50]"),
        LYRICS_LATIN_UK_SLAVIC: ("Liudy\n\nBiľšisť u Ľvovi, ok", "[00:01.00] Liudy\n[00:02.50]"),
    }


def test_the_cyrillic_system_is_settled_by_the_whole_text_not_each_line():
    # "Дуже" alone could be Russian; the next line's ї makes the text Ukrainian
    plain, _ = transliterate_lyrics("Дуже\nЇжак", None)[LYRICS_LATIN_UK_SLAVIC]
    assert plain == "Duže\nJižak"


def test_russian_lyrics_have_no_slavic_alternative():
    assert transliterate_lyrics("Группа крови", None, "rus") == {LYRICS_LATIN_DEFAULT: ("Gruppa krovi", None)}


@pytest.mark.parametrize("plain", ["Hello\nworld", "Привет\nשלום", "right nowㅤ"])
def test_latin_or_half_readable_lyrics_get_no_latin_text(plain):
    assert transliterate_lyrics(plain, None) == {LYRICS_LATIN_DEFAULT: (None, None)}


# ---- lyrics.db ----

def _latin_rows(path):
    conn = sqlite3.connect(path)
    rows = {(song_id, method): (plain, source) for song_id, method, plain, source
            in conn.execute("SELECT song_id, method, plain, source FROM lyrics_latin")}
    conn.close()
    return rows


def test_refresh_works_out_new_and_changed_lyrics(music, lyrics_db):
    music.add_all([Song(id=1, title="Люди", language="ukr", artist=Artist(name="ДахаБраха")),
                   Song(id=2, title="Кино", artist=Artist(name="Кино", origin="Russia"))])
    music.commit()
    with store.lyrics_db_session(lyrics_db) as session:
        store.save_lyrics(session, 1, store.Lyrics(plain="Люди", source="m"))
        store.save_lyrics(session, 2, store.Lyrics(plain="Хочу", source="m"))
        store.save_lyrics(session, 3, store.Lyrics(instrumental=True, source="m"))
        session.commit()
        assert store.refresh_lyrics_latin(music, session) == 2
        assert store.refresh_lyrics_latin(music, session) == 0  # nothing changed since
        assert _latin_rows(lyrics_db) == {(1, LYRICS_LATIN_DEFAULT): ("Liudy", "romanize"),
                                          (1, LYRICS_LATIN_UK_SLAVIC): ("Liudy", "romanize"),
                                          (2, LYRICS_LATIN_DEFAULT): ("Khochu", "romanize")}
        store.save_lyrics(session, 1, store.Lyrics(plain="Львів", source="m"))
        session.commit()
        assert store.refresh_lyrics_latin(music, session) == 1
        assert _latin_rows(lyrics_db)[(1, LYRICS_LATIN_UK_SLAVIC)] == ("Ľviv", "romanize")
        music.get(Song, 1).language = "rus"  # the language decides the system too
        music.commit()
        store.refresh_lyrics_latin(music, session)
        assert (1, LYRICS_LATIN_UK_SLAVIC) not in _latin_rows(lyrics_db)
        store.forget_lookups(session, 1)
        session.commit()
        assert {key[0] for key in _latin_rows(lyrics_db)} == {2}


def test_a_merged_songs_latin_text_goes(lyrics_db):
    conn = sqlite3.connect(lyrics_db)
    conn.execute("INSERT INTO lyrics_latin VALUES (1, 'default', 'a', NULL, 'romanize', 'x', 'now')")
    song_id_replay.replay(conn, [(1, 1, 4)], has_tags=False, has_lyrics=True)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM lyrics_latin").fetchone() == (0,)
    conn.close()


# ---- the sharing server ----

def test_the_server_sends_the_latin_text_and_its_alternatives(tmp_path, lyrics_db):
    with store.lyrics_db_session(lyrics_db) as session:
        store.save_lyrics(session, 4, store.Lyrics(plain="Люди", synced="[00:01.00] Люди", source="lrclib_fetcher"))
        store.save_lyrics(session, 5, store.Lyrics(plain="Hello", source="lrclib_fetcher"))
        for method, latin in ((LYRICS_LATIN_DEFAULT, "Liudy"), (LYRICS_LATIN_UK_SLAVIC, "Liudy")):
            session.add(store.LyricsLatin(song_id=4, method=method, plain=latin, synced=f"[00:01.00] {latin}",
                                          source="romanize", made_at="now"))
        session.add(store.LyricsLatin(song_id=5, method=LYRICS_LATIN_DEFAULT, source="romanize", made_at="now"))
        session.commit()
    music = str(tmp_path / "none.db")
    assert sharing_server.song_lyrics(str(lyrics_db), music, 4) == {
        "song_id": 4, "plain": "Люди", "synced": "[00:01.00] Люди", "instrumental": False, "source": "lrclib_fetcher",
        "plain_latin": "Liudy", "synced_latin": "[00:01.00] Liudy", "latin_source": "romanize",
        "latin_alternatives": {"uk-slavic": {"plain": "Liudy", "synced": "[00:01.00] Liudy", "source": "romanize"}},
    }
    assert "plain_latin" not in sharing_server.song_lyrics(str(lyrics_db), music, 5)


def test_the_server_answers_from_a_lyrics_db_without_latin_texts(tmp_path, lyrics_db):
    conn = sqlite3.connect(lyrics_db)
    conn.executescript("INSERT INTO lyrics (song_id, plain, source, fetched_at) VALUES (4, 'a', 'm', 'now');"
                       "DROP TABLE lyrics_latin;")
    conn.close()
    assert sharing_server.song_lyrics(str(lyrics_db), str(tmp_path / "none.db"), 4)["plain"] == "a"


def test_the_copy_for_the_phone_refreshes_the_latin_texts_first(tmp_path, music, lyrics_db, monkeypatch):
    from utils.database import sharing

    music.add(Song(id=1, title="Люди", language="ukr", artist=Artist(name="ДахаБраха")))
    music.commit()
    with store.lyrics_db_session(lyrics_db) as session:
        store.save_lyrics(session, 1, store.Lyrics(plain="Люди", source="m"))
        session.commit()
    share_dir = tmp_path / "share"
    share_dir.mkdir()
    monkeypatch.setattr(sharing, "DB_FILES", {"music": tmp_path / "music.db", "lyrics": lyrics_db})
    monkeypatch.setattr(sharing, "SHARING_DRIVE_MOUNT", None)
    monkeypatch.setattr(sharing, "SHARING_DIR", share_dir)
    sharing.share_databases()
    assert sharing_server.song_lyrics(str(share_dir / "lyrics.db"), str(share_dir / "music.db"), 1)["plain_latin"] == "Liudy"
