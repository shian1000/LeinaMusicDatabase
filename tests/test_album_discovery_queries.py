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


def _only_in_cyrillic(artist, title):
    # MusicBrainz knows "Ariel - V Krayu Magnoliy" only as "Ариэль - В краю магнолий"
    return "Дорога длиной в 35…" if (artist, title) == ("Ариэль", "В Краю Магнолий") else None


def test_transliterated_title_is_retried_in_cyrillic_with_a_cyrillic_synonym():
    song = SimpleNamespace(artist=SimpleNamespace(name="Ariel", synonyms="Ариэль"),
                           title="V Krayu Magnoliy", album=None, additional_artists=[])
    modules = [("fake_fetcher", "Fake Fetcher", SimpleNamespace(get_album_name=_only_in_cyrillic))]
    assert discoveries_manager.discover_album_name(song, modules) == "Дорога длиной в 35…"


def test_no_cyrillic_retry_without_cyrillic_context():
    queries = []

    def fake_get_album_name(artist, title):
        queries.append(title)
        return None

    song = SimpleNamespace(artist=SimpleNamespace(name="Ariel", synonyms=None),
                           title="V Krayu Magnoliy", album=None, additional_artists=[])
    modules = [("fake_fetcher", "Fake Fetcher", SimpleNamespace(get_album_name=fake_get_album_name))]
    discoveries_manager.discover_album_name(song, modules)
    assert queries == ["V Krayu Magnoliy"]


def _only_in_latin(artist, title):
    # MusicBrainz knows N.R.M.'s "Фабрыка" only as "Fabryka" ("Dom kultury")
    return "Dom kultury" if (artist, title) == ("N.R.M.", "Fabryka") else None


def test_title_in_another_alphabet_is_retried_in_latin_letters():
    song = SimpleNamespace(artist=SimpleNamespace(name="N.R.M.", synonyms=None),
                           title="Фабрыка", album=None, additional_artists=[])
    modules = [("fake_fetcher", "Fake Fetcher", SimpleNamespace(get_album_name=_only_in_latin))]
    assert discoveries_manager.discover_album_name(song, modules) == "Dom kultury"


def test_latin_retry_prefers_the_stored_latin_title():
    queries = []

    def fake_get_album_name(artist, title):
        queries.append(title)
        return None

    song = SimpleNamespace(artist=SimpleNamespace(name="N.R.M.", synonyms=None),
                           title="Маё пакаленьне", title_latin="Maje pakaleńnie", album=None,
                           additional_artists=[])
    modules = [("fake_fetcher", "Fake Fetcher", SimpleNamespace(get_album_name=fake_get_album_name))]
    discoveries_manager.discover_album_name(song, modules)
    assert queries == ["Маё пакаленьне", "Maje pakaleńnie"]


def _linked_song(video_id, artist="Злипні", synonyms=None, title="Глюки"):
    return SimpleNamespace(artist=SimpleNamespace(name=artist, synonyms=synonyms), title=title, album=None,
                           additional_artists=[], youtube_video_id=video_id)


def test_album_is_read_off_the_songs_youtube_link_when_every_fetcher_is_empty(monkeypatch):
    from utils.youtube import manage_youtube_playlists
    # The official upload credits the Latin "Zlypni" plus a guest; no service's search knows the song
    monkeypatch.setattr(manage_youtube_playlists, "get_video_album",
                        lambda video_id: ("Ґрунт", "Глюки", "Zlypni, Данило Грицак") if video_id == "TSEjLo_hlgk" else None)
    empty = [("fake_fetcher", "Fake Fetcher", SimpleNamespace(get_album_name=lambda artist, title: None))]

    assert discoveries_manager.discover_album_name(_linked_song("TSEjLo_hlgk"), empty) == "Ґрунт"
    assert discoveries_manager.discover_album_name(_linked_song(None), empty) is None


def test_youtube_link_album_for_another_song_is_discarded(monkeypatch):
    from utils.youtube import manage_youtube_playlists
    monkeypatch.setattr(manage_youtube_playlists, "get_video_album",
                        lambda video_id: ("Some Album", "Other Song", "Other Artist"))
    empty = [("fake_fetcher", "Fake Fetcher", SimpleNamespace(get_album_name=lambda artist, title: None))]

    assert discoveries_manager.discover_album_name(_linked_song("abcdefghijk"), empty) is None


def test_fetcher_album_wins_over_youtube_link(monkeypatch):
    from utils.youtube import manage_youtube_playlists
    calls = []
    monkeypatch.setattr(manage_youtube_playlists, "get_video_album", lambda video_id: calls.append(video_id))
    found = [("fake_fetcher", "Fake Fetcher", SimpleNamespace(get_album_name=lambda artist, title: "Ґрунт"))]

    assert discoveries_manager.discover_album_name(_linked_song("TSEjLo_hlgk"), found) == "Ґрунт"
    assert calls == []
