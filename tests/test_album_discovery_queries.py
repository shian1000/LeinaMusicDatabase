"""Regression catalog of real songs whose DB artist/title confused the album
fetchers ("Fill missing data -> Albums").

Each case pins the *first* (artist, title) query `discover_album_name()` hands
to a fetcher. That first query is the cleaned-up one (credits and parentheses
stripped), and it's the one the fetchers actually find - the later untruncated/
raw retries exist only as a fallback. Add a new problematic song here as one
more row in ALBUM_QUERY_REGRESSION_CASES.

ALBUM_EMPTY_FETCHERS_CASES pins what discover_album_name() returns when every
fetcher comes up empty - None, except for a soundtrack-like artist ("Tekken 5
OST"), whose name is used as the album, and a cover/unplugged version (any
SINGLES_MARKER word), filed under "Singles".
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.discoveries import discoveries_manager


# (id, db_artist, db_title, expected_first_query_artist, expected_first_query_title)
ALBUM_QUERY_REGRESSION_CASES = [
    # Polish guest credit after a dash, plus a parenthesised band name - every
    # fetcher returned nothing until "gościnnie" was a truncate_at_word() stop word.
    ("tlove_pochodnia_goscinnie", "T.Love",
     "Pochodnia - goscinnie Kasia Sienkiewicz (Kwiat Jabloni)", "T.Love", "Pochodnia"),
    ("goscinnie_with_diacritics", "T.Love",
     "Pochodnia - gościnnie Kasia Sienkiewicz", "T.Love", "Pochodnia"),
    ("goscinnie_in_parentheses", "T.Love",
     "Pochodnia (gościnnie Kasia Sienkiewicz)", "T.Love", "Pochodnia"),
]


@pytest.fixture(autouse=True)
def no_stats(monkeypatch):
    # Keep the Statistics menu's real counters file out of this.
    monkeypatch.setattr(discoveries_manager, "record_invocation", lambda module_id: None)
    monkeypatch.setattr(discoveries_manager, "record_success", lambda module_id: None)


@pytest.mark.parametrize(
    "db_artist, db_title, expected_artist, expected_title",
    [case[1:] for case in ALBUM_QUERY_REGRESSION_CASES],
    ids=[case[0] for case in ALBUM_QUERY_REGRESSION_CASES],
)
def test_album_discovery_first_query(db_artist, db_title, expected_artist, expected_title):
    queries = []

    def fake_get_album_name(artist, title):
        queries.append((artist, title))
        return None

    song = SimpleNamespace(
        artist=SimpleNamespace(name=db_artist, synonyms=None),
        title=db_title,
        album=None,
    )
    modules = [("fake_fetcher", "Fake Fetcher", SimpleNamespace(get_album_name=fake_get_album_name))]

    discoveries_manager.discover_album_name(song, modules)

    assert queries[0] == (expected_artist, expected_title)


# (id, db_artist, db_title, expected_album) - every fetcher returns nothing.
ALBUM_EMPTY_FETCHERS_CASES = [
    ("tekken5_ost_ground_zero_funk", "Tekken 5 OST", "Ground Zero Funk", "Tekken 5 OST"),
    ("dotted_ost_marker", "Final Fantasy VII O.S.T.", "One-Winged Angel", "Final Fantasy VII O.S.T."),
    ("soundtrack_word", "Cowboy Bebop Original Soundtrack", "Tank!", "Cowboy Bebop Original Soundtrack"),
    # "ost" inside a word isn't a soundtrack marker.
    ("ghost_is_not_ost", "Ghost", "Mary on a Cross", None),
    ("post_malone_is_not_ost", "Post Malone", "Circles", None),
    # Covers/unplugged versions no fetcher knows are filed under Singles; the
    # marker may sit in the title or (artist/title swapped) the artist.
    ("serial_heartbreaker_fletcher_loop_cover", "serial heartbreaker", "fletcher loop cover", "Singles"),
    ("cover_in_parentheses", "AURORA", "Believer (Imagine Dragons cover)", "Singles"),
    ("cover_marker_in_artist", "Stampede violin cover", "Alexander Jean ft. Lindsey Stirling", "Singles"),
    ("covered_by", "なんでもないや", "Nandemonaiya ( covered by Rina Aoi )", "Singles"),
    ("unplugged_version", "Nirvana", "About a Girl (Unplugged)", "Singles"),
    ("recover_is_not_cover", "CHVRCHES", "Recover", None),
    # Soundtrack fallback names a more specific album, so it wins over the cover one.
    ("soundtrack_cover_keeps_soundtrack_album", "Tekken 5 OST", "Ground Zero Funk (piano cover)", "Tekken 5 OST"),
]


def _song(db_artist, db_title):
    return SimpleNamespace(
        artist=SimpleNamespace(name=db_artist, synonyms=None),
        title=db_title,
        album=None,
    )


@pytest.mark.parametrize(
    "db_artist, db_title, expected_album",
    [case[1:] for case in ALBUM_EMPTY_FETCHERS_CASES],
    ids=[case[0] for case in ALBUM_EMPTY_FETCHERS_CASES],
)
def test_album_discovery_when_every_fetcher_is_empty(db_artist, db_title, expected_album):
    modules = [("fake_fetcher", "Fake Fetcher", SimpleNamespace(get_album_name=lambda artist, title: None))]

    assert discoveries_manager.discover_album_name(_song(db_artist, db_title), modules) == expected_album


def test_fetcher_album_wins_over_soundtrack_artist_fallback():
    modules = [("fake_fetcher", "Fake Fetcher",
                SimpleNamespace(get_album_name=lambda artist, title: "Tekken 5 Original Soundtrack"))]

    album = discoveries_manager.discover_album_name(_song("Tekken 5 OST", "Ground Zero Funk"), modules)

    assert album == "Tekken 5 Original Soundtrack"


def test_fetcher_album_wins_over_cover_fallback():
    modules = [("fake_fetcher", "Fake Fetcher",
                SimpleNamespace(get_album_name=lambda artist, title: "Surfer Rosa"))]

    album = discoveries_manager.discover_album_name(_song("Alicia Widar", "Where is my Mind (Pixies cover)"), modules)

    assert album == "Surfer Rosa"
