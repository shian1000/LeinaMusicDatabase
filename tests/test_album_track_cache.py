"""Fill missing data -> Albums: once a song's album is found, the other songs
of that artist waiting in the run are taken from its tracklist
(discoveries_manager.AlbumTrackCache) with their track numbers, instead of
asking every module again. Modules, MusicBrainz and DB writes mocked."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import menu.main_menu  # noqa: F401,E402  (import order, see AGENTS.md)
from utils.discoveries import discoveries_manager as dm  # noqa: E402
from utils.discoveries.discovery_result import AlbumTrack, DiscoveryResult  # noqa: E402

LAIN = "Lain: The Wired World (Remastered)"
LAIN_TRACKS = [
    AlbumTrack("Akira (REMASTERED)", 2, None, "Serial Experiments, Maurizio Gambardella, Matteo Tundo"),
    AlbumTrack("Dust Of Harmonies (REMASTERED)", 7, None, "Serial Experiments, Maurizio Gambardella, Matteo Tundo"),
    AlbumTrack("Iwakura Rein (REMASTERED)", 12, None, "Serial Experiments, Maurizio Gambardella, Matteo Tundo"),
    AlbumTrack("Lain: The Wired World (REMASTERED)", 14, None, "Serial Experiments, Maurizio Gambardella, Matteo Tundo"),
]


@pytest.fixture(autouse=True)
def no_side_lookups(monkeypatch):
    for name in ("record_invocation", "record_success"):
        monkeypatch.setattr(dm, name, lambda module_id: None)
    monkeypatch.setattr(dm, "artist_match_synonyms", lambda artist: artist.synonyms)
    monkeypatch.setattr(dm, "song_all_artists", lambda song: [song.artist])
    monkeypatch.setattr(dm, "song_main_artists", lambda song: [song.artist])
    monkeypatch.setattr(dm, "album_from_stored_youtube_link", lambda song, credited=None: None)


def _artist(name="Serial Experiments Lain", synonyms="Serial Experiments", artist_id=1):
    return SimpleNamespace(id=artist_id, name=name, synonyms=synonyms, origin=None)


def _songs(*titles, artist=None):
    artist = artist or _artist()
    return [SimpleNamespace(id=f"{artist.id}-{title}", title=title, album=None, artist=artist,
                            artist_id=artist.id, title_latin=None, language=None, youtube_video_id=None)
            for title in titles]


class _Fetcher:
    """A module finding LAIN for the titles it knows, counting every call
    and every tracklist load."""

    def __init__(self, known=("Dust of Harmonies",), tracks=LAIN_TRACKS):
        self.known, self.tracks = known, tracks
        self.queries, self.loads = [], 0

    def get_album_name(self, artist, title):
        self.queries.append((artist, title))
        if title not in self.known:
            return None
        return DiscoveryResult(album=LAIN, matched_title=f"{title} (REMASTERED)", matched_artist=artist,
                               track_number=7, load_tracks=self._load)

    def _load(self):
        self.loads += 1
        return self.tracks

    def modules(self):
        return [("fake", "Fake fetcher", self)]


@pytest.mark.parametrize("title, key", [
    ("Dust Of Harmonies (REMASTERED)", "dust of harmonies"),
    ("Song (2011 Remaster)", "song"),
    ("Song [Digital Remaster]", "song"),
    ("Song - Remastered 2009", "song"),
    ("Song (feat. Guest)", "song"),
    ("Song (Remix)", "song remix"),  # a remix is a song of its own
    ("Lain: The Wired World (REMASTERED)", "lain the wired world"),
])
def test_track_key(title, key):
    assert dm._track_key(title) == key


def test_the_other_songs_of_the_artist_come_from_the_tracklist_with_their_numbers():
    fetcher = _Fetcher()
    songs = _songs("Dust of Harmonies", "Iwakura Rein", "Akira")
    cache = dm.AlbumTrackCache(songs)

    matches = [dm.discover_album(song, fetcher.modules(), cache) for song in songs]

    assert [(m.album, m.track_number, m.disc_number) for m in matches] == [
        (LAIN, 7, None), (LAIN, 12, None), (LAIN, 2, None)]
    assert fetcher.loads == 1
    assert [title for _artist_name, title in fetcher.queries] == ["Dust of Harmonies"]  # no more lookups


def test_a_title_not_on_the_tracklist_is_still_looked_up():
    # "The Wired World" isn't "Lain: The Wired World" - only an equal key counts
    fetcher = _Fetcher()
    songs = _songs("Dust of Harmonies", "The Wired World")
    cache = dm.AlbumTrackCache(songs)

    assert dm.discover_album(songs[0], fetcher.modules(), cache).album == LAIN
    assert dm.discover_album(songs[1], fetcher.modules(), cache) is None
    assert ("Serial Experiments Lain", "The Wired World") in fetcher.queries


def test_no_tracklist_is_loaded_when_no_other_song_of_the_artist_waits():
    fetcher = _Fetcher()
    other_artist_song = _songs("Akira", artist=_artist("Someone Else", None, artist_id=2))
    songs = _songs("Dust of Harmonies") + other_artist_song
    cache = dm.AlbumTrackCache(songs)

    assert dm.discover_album(songs[0], fetcher.modules(), cache).track_number == 7
    assert fetcher.loads == 0
    # Another artist's "Akira" never gets the album from it
    assert cache.lookup(other_artist_song[0]) is None


def test_an_already_looked_up_song_no_longer_counts_as_waiting():
    fetcher = _Fetcher(known=("Iwakura Rein", "Dust of Harmonies"))
    songs = _songs("Iwakura Rein", "Dust of Harmonies")
    cache = dm.AlbumTrackCache(songs)

    dm.discover_album(songs[0], fetcher.modules(), cache)  # loads: "Dust of Harmonies" still waits
    assert fetcher.loads == 1
    assert dm.discover_album(songs[1], fetcher.modules(), cache).track_number == 7
    assert len(fetcher.queries) == 1 and fetcher.loads == 1  # the second one only from the cache


def test_a_soundtracks_tracks_by_other_artists_are_left_out():
    tracks = [AlbumTrack("Dust of Harmonies", 1, None, "Serial Experiments"),
              AlbumTrack("Akira", 2, None, "Geinoh Yamashirogumi"),
              AlbumTrack("Iwakura Rein", 3, None, None)]  # no credit known: taken
    fetcher = _Fetcher(tracks=tracks)
    songs = _songs("Dust of Harmonies", "Akira", "Iwakura Rein")
    cache = dm.AlbumTrackCache(songs)

    dm.discover_album(songs[0], fetcher.modules(), cache)

    assert cache.lookup(songs[1]) is None
    assert cache.lookup(songs[2]).track_number == 3


def test_a_remix_on_the_tracklist_doesnt_give_the_plain_song_its_album():
    fetcher = _Fetcher(tracks=[AlbumTrack("Dust of Harmonies", 1), AlbumTrack("Akira (Remix)", 2)])
    songs = _songs("Dust of Harmonies", "Akira")
    cache = dm.AlbumTrackCache(songs)

    dm.discover_album(songs[0], fetcher.modules(), cache)

    assert cache.lookup(songs[1]) is None


def test_a_crashing_tracklist_loader_only_loses_the_cache():
    fetcher = _Fetcher()

    def boom():
        raise RuntimeError("tracklist down")
    fetcher._load = boom
    songs = _songs("Dust of Harmonies", "Akira")
    cache = dm.AlbumTrackCache(songs)

    assert dm.discover_album(songs[0], fetcher.modules(), cache).album == LAIN
    assert cache.lookup(songs[1]) is None


def test_a_modules_unbelievable_track_number_is_dropped():
    module = SimpleNamespace(get_album_name=lambda artist, title: DiscoveryResult(
        album=LAIN, track_number=0, disc_number=True, load_tracks="not callable"))

    match = dm._call_module_match(module, "fake", "Fake", "Serial Experiments Lain", "Akira")

    assert (match.album, match.track_number, match.disc_number, match.load_tracks) == (LAIN, None, None, None)


def test_the_track_position_is_saved_only_where_the_song_has_none():
    module = importlib.import_module(
        "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums")
    empty = SimpleNamespace(track_number=None, disc_number=None)
    numbered = SimpleNamespace(track_number=5, disc_number=None)
    unknown_place = SimpleNamespace(track_number=None, disc_number=None)

    module._set_track_position(empty, dm.AlbumMatch(LAIN, 3, 2))
    module._set_track_position(numbered, dm.AlbumMatch(LAIN, 3, 2))
    module._set_track_position(unknown_place, dm.AlbumMatch(LAIN, None, 2))
    module._set_track_position(unknown_place, None)

    assert (empty.track_number, empty.disc_number) == (3, 2)
    assert (numbered.track_number, numbered.disc_number) == (5, None)  # a hand-set number stays
    assert (unknown_place.track_number, unknown_place.disc_number) == (None, None)
