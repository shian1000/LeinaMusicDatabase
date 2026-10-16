"""A song's genres (songs.genre, migration 0013; utils/database/genres.py):
one spelling per genre (MusicBrainz's list, synonyms, the spellings music.db
already uses), merges keeping a genre, and the MusicBrainz lookup for Fill
missing data -> Genres (utils/discoveries/genre_lookup.py) with mb_get()
mocked - song tags, then the album's release group, then the artist.
In-memory SQLite, no mocking of SQL."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config.constants import GENRE_LEVEL_ALBUM, GENRE_LEVEL_ARTIST, GENRE_LEVEL_SONG
from utils.database import genres as gn
from utils.database.datatables import Artist, Base, Song
from utils.discoveries import genre_lookup as gl


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# ==================== spelling ====================

@pytest.mark.parametrize("typed, stored", [
    ("Synthpop", "synth-pop"),
    ("synth pop", "synth-pop"),
    ("Hip-Hop", "hip hop"),
    ("RnB", "r&b"),
    ("Rhythm & Blues", "r&b"),
    ("drum'n'bass", "drum and bass"),
    ("Rock & Roll", "rock and roll"),
    ("K-Pop", "k-pop"),
    ("Szanty  Morskie", "szanty morskie"),  # unknown: lowercased as typed
])
def test_one_spelling_per_genre(typed, stored):
    assert gn.canonical_genre(typed) == stored


def test_clean_genres_keeps_order_and_drops_repeats():
    assert gn.clean_genres(" Indie Pop , synthpop; indie-pop,, Synth-Pop ") == "indie pop, synth-pop"
    assert gn.clean_genres("  ") is None
    assert gn.clean_genres(None) is None


def test_the_databases_spelling_wins_over_musicbrainz(session):
    artist = Artist(name="A")
    session.add_all([Song(title="1", artist=artist, genre="Synth Pop, poezja śpiewana"),
                     Song(title="2", artist=artist, genre="Synth Pop")])
    song = Song(title="3", artist=artist)
    session.add(song)
    session.flush()

    old = gn.set_song_genre(session, song, "synthpop, Poezja Spiewana, pop")

    assert old is None
    assert song.genre == "Synth Pop, poezja śpiewana, pop"


def test_one_song_may_respell_a_genre_only_it_has(session):
    song = Song(title="1", artist=Artist(name="A"), genre="Synth Pop")
    session.add(song)
    session.flush()

    gn.set_song_genre(session, song, "synthpop")

    assert song.genre == "synth-pop"


def test_merge_keeps_a_genre(session):
    keep, drop = Song(title="x", genre=None), Song(title="x", genre="pop")
    gn.keep_genre_on_merge(keep, drop)
    assert keep.genre == "pop"
    keep.genre, drop.genre = "rock", "pop"
    gn.keep_genre_on_merge(keep, drop)
    assert keep.genre == "rock"


def test_songs_missing_genre(session):
    artist = Artist(name="A")
    session.add_all([Song(title="b", artist=artist), Song(title="a", artist=artist, genre=""),
                     Song(title="c", artist=artist, genre="pop")])
    session.flush()

    assert [s.title for s in gn.songs_missing_genre(session)] == ["a", "b"]


# ==================== MusicBrainz lookup ====================

def _song(title="Enjoy the Silence", artist="Depeche Mode", album="Violator", synonyms=None):
    return SimpleNamespace(title=title, title_latin=None, album=album,
                           artist=SimpleNamespace(name=artist, synonyms=synonyms, name_latin=None))


def _recording(title="Enjoy the Silence", artist="Depeche Mode", tags=(), groups=(), **kw):
    return {"id": f"rec-{title}", "title": title, "tags": [{"name": n, "count": c} for n, c in tags],
            "artist-credit": [{"name": artist, "artist": {"id": "art-1", "name": artist}}],
            "releases": [{"title": g.get("title"), "release-group": g} for g in groups], **kw}


@pytest.fixture
def mb(monkeypatch):
    """Answers keyed by URL ending; records what was asked."""
    answers, asked = {}, []

    def fake(url, params):
        asked.append((url, params))
        for key, value in answers.items():
            if url.endswith(key):
                return value(params) if callable(value) else value
        return None

    monkeypatch.setattr(gl, "_get_json", fake)
    return SimpleNamespace(answers=answers, asked=asked)


def test_song_tags_that_are_genres_win(mb):
    mb.answers["recording/"] = {"recordings": [
        _recording(tags=[("synth-pop", 5), ("new wave", 3), ("seen live", 9), ("electronic", 1)]),
        _recording(tags=[("Synthpop", 2)]),
        _recording(title="Policy of Truth", tags=[("rock", 50)]),
        _recording(tags=[("rock", 50)], disambiguation="live, 1990"),
    ]}

    result = gl.GenreLookup().lookup(_song())

    # "seen live" isn't a genre, "electronic" is under 30 % of the top's votes
    assert (result.genres, result.level) == ("synth-pop, new wave", GENRE_LEVEL_SONG)
    assert 'release:"Violator"' in mb.asked[0][1]["query"]


def test_then_the_albums_release_group(mb):
    mb.answers["recording/"] = {"recordings": [_recording(groups=[
        {"id": "rg-single", "title": "Enjoy the Silence", "primary-type": "Single"},
        {"id": "rg-live", "title": "Violator", "primary-type": "Album", "secondary-types": ["Live"]},
        {"id": "rg-album", "title": "Violator", "primary-type": "Album"},
    ])]}
    mb.answers["release-group/rg-album"] = {"genres": [{"name": "synth-pop", "count": 4}]}

    result = gl.GenreLookup().lookup(_song())

    assert (result.genres, result.level) == ("synth-pop", GENRE_LEVEL_ALBUM)


def test_then_the_artist_once_per_run(mb):
    mb.answers["recording/"] = {"recordings": [_recording()]}
    mb.answers["artist/art-1"] = {"genres": [{"name": "electronic", "count": 28}, {"name": "synth-pop", "count": 25}]}
    lookup = gl.GenreLookup()

    first = lookup.lookup(_song())
    lookup.lookup(_song())

    assert (first.genres, first.level) == ("electronic, synth-pop", GENRE_LEVEL_ARTIST)
    assert sum(url.endswith("artist/art-1") for url, _ in mb.asked) == 1


def test_artist_search_when_no_recording_matches(mb):
    mb.answers["recording/"] = {"recordings": [_recording(artist="Someone Else")]}
    mb.answers["artist/"] = {"artists": [{"id": "art-9", "name": "Кино"}]}
    mb.answers["artist/art-9"] = {"genres": [{"name": "post-punk", "count": 3}]}

    result = gl.GenreLookup().lookup(_song(title="Группа крови", artist="Kino", album="Singles", synonyms="Кино"))

    assert (result.genres, result.level) == ("post-punk", GENRE_LEVEL_ARTIST)


def test_nothing_found(mb):
    assert gl.GenreLookup().lookup(_song()) is None


def test_misses_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "_misses_path", lambda: tmp_path / "data" / "misses.json")
    assert gl.load_misses() == set()
    gl.save_misses({3, 1})
    assert gl.load_misses() == {1, 3}


# ==================== migration ====================

def test_migration_adds_the_column(tmp_path):
    import sqlite3
    sql = (repo_root / "migrations" / "music" / "0013_song_genre.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY, title VARCHAR)")
    conn.executescript(sql)
    assert "genre" in [row[1] for row in conn.execute("PRAGMA table_info(songs)")]
