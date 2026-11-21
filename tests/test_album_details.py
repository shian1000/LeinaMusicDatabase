"""Fill missing data -> Albums keeps what else the album's source said (year,
album type, its ids there): the manager's checks on a module's values, the
run's tracklist cache passing them on, and the save into empty fields only -
the year only from a module enabled for Years too. In-memory SQLite with the
album listener on; modules mocked."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import menu.main_menu  # noqa: F401,E402  (import order, see AGENTS.md)
from config.constants import SINGLES_ALBUM  # noqa: E402
from utils.database.albums import register_album_sync  # noqa: E402
from utils.database.datatables import Album, Artist, Base, Song  # noqa: E402
from utils.discoveries import discoveries_manager as dm  # noqa: E402
from utils.discoveries.discovery_result import AlbumTrack, DiscoveryResult  # noqa: E402

fill_albums = importlib.import_module(
    "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums")

YEARS_MODULE, OTHER_MODULE = "music_brainz_fetcher", "itunes_fetcher"
GROUP_ID = {"musicbrainz_release_group_id": "rg-1"}


@pytest.fixture(autouse=True)
def no_stats(monkeypatch):
    monkeypatch.setattr(dm, "record_invocation", lambda *a: None)
    monkeypatch.setattr(dm, "record_success", lambda *a: None)


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    register_album_sync(factory)
    s = factory()
    monkeypatch.setattr(fill_albums, "require_global_database_sessions", lambda: (s, None))
    monkeypatch.setattr(fill_albums, "enabled_module_ids", lambda kind: {YEARS_MODULE})
    yield s
    s.close()


def _module(result):
    return SimpleNamespace(get_album_name=lambda artist, title: result)


def test_the_manager_keeps_believable_values_and_the_module_id():
    result = DiscoveryResult("Dark", "Song", "Artist", year=1973, album_type="ep",
                             album_ids={"spotify_album_id": "x", "itunes_collection_id": 5})

    match = dm._call_module_match(_module(result), "spotify_api_fetcher", "Spotify", "Artist", "Song")

    assert (match.year, match.album_type, match.source) == (1973, "ep", "spotify_api_fetcher")
    assert match.album_ids == {"spotify_album_id": "x", "itunes_collection_id": 5}


def test_the_manager_discards_a_bad_year_type_and_unknown_or_empty_ids():
    result = DiscoveryResult("Dark", "Song", "Artist", year=3000, album_type="single",
                             album_ids={"bogus_id": 1, "itunes_collection_id": "", "deezer_album_id": True})

    match = dm._call_module_match(_module(result), "m", "M", "Artist", "Song")

    assert match.album == "Dark"
    assert (match.year, match.album_type, match.album_ids) == (None, None, {})


def test_a_song_found_on_a_loaded_tracklist_gets_the_albums_details():
    artist = SimpleNamespace(id=1, name="Artist", synonyms=None)
    songs = [SimpleNamespace(id=n, title=t, artist=artist, artist_id=1) for n, t in ((1, "One"), (2, "Two"))]
    cache = dm.AlbumTrackCache(songs)
    cache.lookup(songs[0])
    match = dm.AlbumMatch("Dark", 1, load_tracks=lambda: [AlbumTrack("One", 1), AlbumTrack("Two", 2)],
                          year=1973, album_type="album", album_ids=GROUP_ID, source=YEARS_MODULE)

    cache.remember(songs[0], match)
    cached = cache.lookup(songs[1])

    assert (cached.track_number, cached.year, cached.album_type) == (2, 1973, "album")
    assert (cached.album_ids, cached.source) == (GROUP_ID, YEARS_MODULE)


def _song(session, title, album, **fields):
    artist = session.query(Artist).first() or Artist(name="Artist")
    song = Song(title=title, artist=artist, album=album, **fields)
    session.add(song)
    session.flush()
    return song


def _match(album, source=YEARS_MODULE, year=1973, **fields):
    return dm.AlbumMatch(album, 3, year=year, album_type="album", album_ids=GROUP_ID, source=source, **fields)


def test_the_year_type_and_ids_go_into_the_empty_fields(session):
    song = _song(session, "Song", "Dark")

    fill_albums._save_album_details([(song, _match("Dark"))])

    album = song.album_entry
    assert (song.year, album.year, album.type, album.musicbrainz_release_group_id) == (1973, 1973, "album", "rg-1")


def test_a_year_from_a_module_not_on_for_years_is_left_out(session):
    song = _song(session, "Song", "Dark")

    fill_albums._save_album_details([(song, _match("Dark", source=OTHER_MODULE))])

    assert (song.year, song.album_entry.year, song.album_entry.type) == (None, None, "album")


def test_the_album_rows_year_wins_over_the_sources(session):
    _song(session, "Earlier", "Dark", year=1973)
    song = _song(session, "Song", "Dark")

    fill_albums._save_album_details([(song, _match("Dark", year=2011))])

    assert (song.year, song.album_entry.year) == (1973, 1973)


def test_values_already_there_are_kept(session):
    song = _song(session, "Song", "Dark", year=1980)
    album = song.album_entry
    album.type, album.musicbrainz_release_group_id = "soundtrack", "rg-mine"

    fill_albums._save_album_details([(song, _match("Dark"))])

    assert (song.year, album.year, album.type, album.musicbrainz_release_group_id) == (1980, 1980, "soundtrack",
                                                                                         "rg-mine")


def test_a_single_gets_the_year_but_no_place_type_or_ids(session):
    song = _song(session, "Song", SINGLES_ALBUM)
    match = _match(SINGLES_ALBUM, year=1999)

    fill_albums._set_track_position(song, match)
    fill_albums._save_album_details([(song, match)])

    album = session.get(Album, song.album_id)
    assert (song.year, song.track_number) == (1999, None)
    assert (album.year, album.type, album.musicbrainz_release_group_id) == (1999, "single", None)


def test_nothing_is_flushed_for_matches_without_details(session, monkeypatch):
    monkeypatch.setattr(fill_albums, "require_global_database_sessions",
                        lambda: pytest.fail("no details, no session needed"))

    fill_albums._save_album_details([(SimpleNamespace(), SimpleNamespace(album="Dark")),
                                     (SimpleNamespace(), None)])


def _first_appearance_module(first, asked=None):
    def first_appearance_year(artist, title):
        if asked is not None:
            asked.append((artist, title))
        return first
    return SimpleNamespace(first_appearance_year=first_appearance_year)


def test_a_sources_year_later_than_the_song_first_came_out_is_left_out(session, monkeypatch):
    # Andrzej Zaucha - "Stwardnieje ci łza": Spotify has Anawa's 1973 album only
    # as a 2014 reissue; MusicBrainz has the song on a 2004 compilation already
    song = _song(session, "Stwardnieje ci łza", "Anawa")
    asked = []
    modules = [(YEARS_MODULE, "MusicBrainz", _first_appearance_module(2004, asked))]
    monkeypatch.setattr(fill_albums, "enabled_module_ids", lambda kind: {YEARS_MODULE, "spotify_fetcher"})

    fill_albums._save_album_details([(song, _match("Anawa", source="spotify_fetcher", year=2014))], modules)

    assert (song.year, song.album_entry.year) == (None, None)
    assert asked == [("Artist", "Stwardnieje ci łza")]


def test_a_year_close_to_the_first_release_stays_and_musicbrainz_isnt_asked_about_its_own(session):
    song = _song(session, "Song", "Dark")
    asked = []
    modules = [(YEARS_MODULE, "MusicBrainz", _first_appearance_module(1972, asked))]

    fill_albums._save_album_details([(song, _match("Dark"))], modules)

    assert song.year == 1973
    assert asked == []  # MusicBrainz's own answer already left reissues out


def test_the_manager_tries_the_next_module_after_a_reissues_year(monkeypatch):
    monkeypatch.setattr(dm, "record_invocation", lambda *a: None)
    monkeypatch.setattr(dm, "record_success", lambda *a: None)
    mb = _first_appearance_module(2004)
    mb.get_release_year = lambda artist, query, is_single: None
    spotify = SimpleNamespace(get_release_year=lambda artist, query, is_single: 2014)
    other = SimpleNamespace(get_release_year=lambda artist, query, is_single: 2005)
    song = SimpleNamespace(title="Stwardnieje ci łza")
    modules = [(YEARS_MODULE, "MusicBrainz", mb), ("spotify_fetcher", "Spotify", spotify)]

    assert dm.discover_release_year_result("Andrzej Zaucha", "Anawa", False, modules, album_songs=[song]) is None
    result = dm.discover_release_year_result("Andrzej Zaucha", "Anawa", False,
                                             [*modules, ("other", "Other", other)], album_songs=[song])
    assert result.year == 2005
    # A project's recording (held=False) is years after its studio song by nature
    assert dm.discover_release_year_result("Andrzej Zaucha", "Męskie Granie", False, modules, album_songs=[song],
                                           held=False).year == 2014
