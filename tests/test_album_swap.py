"""Fill missing data → Albums on a song stored with its artist and title
swapped: who is tried the other way round (artist_identity.may_be_swapped()),
the lookup (discoveries_manager.discover_album_after_swap()), the swap itself
and the review at the end (utils/ui/album_swap_review.py). In-memory SQLite
with the album listener on; modules, prompts and the browser mocked."""

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
from config.constants import ADDITIONAL_ARTIST_ROLE_FEAT  # noqa: E402
from utils.database.albums import register_album_sync  # noqa: E402
from utils.database.artist_identity import may_be_swapped, swap_song_names  # noqa: E402
from utils.database.datatables import Artist, Base, Song  # noqa: E402
from utils.database.song_artists import add_additional_artist  # noqa: E402
from utils.discoveries import discoveries_manager as dm  # noqa: E402
from utils.discoveries.discovery_result import DiscoveryResult  # noqa: E402
from utils.ui import album_swap_review as review  # noqa: E402

fill_albums = importlib.import_module(
    "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums")

SWAPPED = ("Dobro Te Čuvaju", "Džoni Midnajt Trio")
RIGHT = ("Džoni Midnajt Trio", "Dobro Te Čuvaju")


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    register_album_sync(factory)
    s = factory()
    yield s
    s.close()


def _song(session, artist_name, title):
    artist = session.query(Artist).filter_by(name=artist_name).first() or Artist(name=artist_name)
    song = Song(title=title, artist=artist)
    session.add(song)
    session.flush()
    return song


def test_an_artist_with_another_song_is_known_and_not_tried(session):
    song = _song(session, "Styx", "Renegade")
    _song(session, "Styx", "Boat on a River")
    assert not may_be_swapped(session, song)


def test_a_one_song_artist_is_tried(session):
    assert may_be_swapped(session, _song(session, *SWAPPED))


def test_a_song_named_like_its_artist_is_not_tried(session):
    assert not may_be_swapped(session, _song(session, "Venture", "Venture"))


def test_a_multi_artist_song_is_not_tried(session):
    song = _song(session, "Sw@da", "Hit")
    guest = Artist(name="Niczos")
    session.add(guest)
    session.flush()
    add_additional_artist(song, guest, ADDITIONAL_ARTIST_ROLE_FEAT)
    session.flush()
    assert not may_be_swapped(session, song)


def test_swap_renames_the_lone_artist_row(session):
    song = _song(session, *SWAPPED)
    artist_id = song.artist_id
    swap_song_names(session, song)
    assert (song.artist.name, song.title) == RIGHT
    assert song.artist_id == artist_id


def test_swap_moves_the_song_to_an_existing_artist_and_removes_the_old_row(session):
    _song(session, "Styx", "Boat on a River")
    song = _song(session, "Renegade", "Styx")
    swap_song_names(session, song)
    assert (song.artist.name, song.title) == ("Styx", "Renegade")
    assert session.query(Artist).filter_by(name="Renegade").first() is None
    assert session.query(Artist).filter_by(name="Styx").count() == 1


def _module(known):
    """A discovery module answering only the (artist, title) pairs in `known`."""
    calls = []

    def get_album_name(artist, title):
        calls.append((artist, title))
        return known.get((artist, title))
    return SimpleNamespace(get_album_name=get_album_name), calls


@pytest.fixture
def no_stats(monkeypatch):
    monkeypatch.setattr(dm, "record_invocation", lambda _: None)
    monkeypatch.setattr(dm, "record_success", lambda _: None)


def _stored_song(artist_name, title):
    return SimpleNamespace(title=title, artist=SimpleNamespace(name=artist_name))


def test_lookup_asks_the_modules_the_other_way_round(no_stats):
    found = DiscoveryResult(album="Po Sećanju", matched_title="DOBRO TE ČUVAJU", matched_artist="Džoni Midnajt Trio")
    module, calls = _module({RIGHT: found})
    match = dm.discover_album_after_swap(_stored_song(*SWAPPED), [("m", "M", module)])
    assert match.album == "Po Sećanju"
    assert calls == [RIGHT]


def test_lookup_goes_on_to_the_next_module(no_stats):
    found = DiscoveryResult(album="Po Sećanju", matched_title="Dobro Te Čuvaju", matched_artist="Džoni Midnajt Trio")
    empty, _ = _module({})
    module, _ = _module({RIGHT: found})
    match = dm.discover_album_after_swap(_stored_song(*SWAPPED), [("a", "A", empty), ("b", "B", module)])
    assert match.album == "Po Sećanju"


def test_lookup_validates_what_a_module_found(no_stats):
    wrong = DiscoveryResult(album="Po Sećanju", matched_title="Something Else Entirely", matched_artist="Other Band")
    module, _ = _module({RIGHT: wrong})
    assert dm.discover_album_after_swap(_stored_song(*SWAPPED), [("m", "M", module)]) is None


def test_lookup_tries_the_whole_names_after_the_truncated_ones(no_stats):
    found = DiscoveryResult(album="Po Sećanju", matched_title="Bang, Bang", matched_artist="Band")
    module, calls = _module({("Band", "Bang, Bang"): found})
    match = dm.discover_album_after_swap(_stored_song("Bang, Bang", "Band"), [("m", "M", module)])
    assert match.album == "Po Sećanju"
    assert calls == [("Band", "Bang"), ("Band", "Bang, Bang")]


def test_lookup_has_no_last_resort_fallbacks(no_stats):
    # "cover" would file it under Singles in discover_album(), the soundtrack
    # marker under its own name; neither says which way round the names are
    empty, _ = _module({})
    assert dm.discover_album_after_swap(_stored_song("Song Cover", "Game OST"), [("m", "M", empty)]) is None


@pytest.fixture
def run_fill(monkeypatch, session):
    """Runs fill_missing_albums() with nothing found the normal way and
    "Po Sećanju" (track 5) found after a swap; `picked` is what the checkbox
    list answers."""
    def run(songs, picked):
        monkeypatch.setattr(fill_albums, "open_global_driver", lambda: None)
        monkeypatch.setattr(fill_albums, "close_global_driver", lambda: None)
        monkeypatch.setattr(fill_albums, "load_max_songs_per_fetch", lambda: 100)
        monkeypatch.setattr(fill_albums, "load_discovery_modules", lambda: [])
        monkeypatch.setattr(fill_albums, "discover_album", lambda *a: None)
        monkeypatch.setattr(fill_albums, "submit_global_database_session", lambda: None)
        monkeypatch.setattr(fill_albums, "require_global_database_sessions", lambda: (session, None))
        monkeypatch.setattr(review, "require_global_database_sessions", lambda: (session, None))
        monkeypatch.setattr(fill_albums, "get_songs_with_empty_category", lambda category: songs)
        monkeypatch.setattr(fill_albums, "edit_db_entry",
                            lambda song, category, value: setattr(song, category, value))
        looked_up, questions, listed = [], [], []

        def after_swap(song, modules):
            looked_up.append(song.title)
            return dm.AlbumMatch("Po Sećanju", track_number=5)

        def ask_yes_no(question):
            questions.append(question)
            return False

        def checkbox(message, choices):
            listed.extend(choices)
            return SimpleNamespace(ask=lambda: picked)

        monkeypatch.setattr(fill_albums, "discover_album_after_swap", after_swap)
        monkeypatch.setattr(fill_albums, "ask_yes_no", ask_yes_no)
        monkeypatch.setattr(review.questionary, "checkbox", checkbox)
        fill_albums.fill_missing_albums()
        return SimpleNamespace(looked_up=looked_up, questions=questions, listed=listed)
    return run


def test_a_ticked_song_is_swapped_and_gets_its_album(session, run_fill):
    song = _song(session, *SWAPPED)
    result = run_fill([song], [0])
    assert (song.artist.name, song.title) == RIGHT
    assert (song.album, song.track_number) == ("Po Sećanju", 5)
    assert not any("Unable to find" in question for question in result.questions)


def test_the_list_offers_the_swapped_names_with_nothing_ticked(session, run_fill):
    result = run_fill([_song(session, *SWAPPED)], [])
    assert len(result.listed) == 1
    assert result.listed[0].checked is False
    text = "".join(part for _, part in result.listed[0].title)
    assert text.startswith("Džoni Midnajt Trio - Dobro Te Čuvaju")
    assert "Po Sećanju" in text


def test_an_unticked_song_stays_and_goes_on_to_the_manual_question(session, run_fill):
    song = _song(session, *SWAPPED)
    result = run_fill([song], [])
    assert (song.artist.name, song.title, song.album) == (*SWAPPED, None)
    assert any("Unable to find 1 " in question for question in result.questions)


def test_a_cancelled_list_changes_nothing(session, run_fill):
    song = _song(session, *SWAPPED)
    run_fill([song], None)
    assert (song.artist.name, song.title, song.album) == (*SWAPPED, None)


def test_only_the_ticked_one_of_several_is_swapped(session, run_fill):
    first = _song(session, "Title One", "Band One")
    second = _song(session, "Title Two", "Band Two")
    result = run_fill([first, second], [1])
    assert (first.artist.name, first.title, first.album) == ("Title One", "Band One", None)
    assert (second.artist.name, second.title, second.album) == ("Band Two", "Title Two", "Po Sećanju")
    assert any("Unable to find 1 " in question for question in result.questions)


def test_a_known_artist_is_neither_looked_up_nor_listed(session, run_fill):
    song = _song(session, "Styx", "Renegade")
    _song(session, "Styx", "Boat on a River")
    result = run_fill([song], [])
    assert result.looked_up == []
    assert result.listed == []
