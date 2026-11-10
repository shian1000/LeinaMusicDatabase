"""Fill missing data → Albums on an album only an unsure source knew (the real
"Folk Hit Maraton 4", Google's "first album" of a Payner upload): the Google
fetcher's flag, discover_album() preferring anything surer, and the review at
the end (utils/ui/album_guess_review.py). In-memory SQLite with the album
listener on; modules, prompts and the browser mocked."""

import questionary
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
from utils.database.datatables import Artist, Base, Song  # noqa: E402
from utils.discoveries import discoveries_manager as dm  # noqa: E402
from utils.discoveries.discovery_modules import google_search_fetcher as google  # noqa: E402
from utils.discoveries.discovery_result import DiscoveryResult  # noqa: E402
from utils.ui import album_guess_review as review  # noqa: E402

fill_albums = importlib.import_module(
    "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums")

ARTIST = "Milko Kalaydzhiev"
GUESS = "Folk Hit Maraton 4"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    register_album_sync(factory)
    s = factory()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def no_stats(monkeypatch):
    monkeypatch.setattr(dm, "record_invocation", lambda *a: None)
    monkeypatch.setattr(dm, "record_success", lambda *a: None)
    monkeypatch.setattr(dm, "album_from_stored_youtube_link", lambda *a: None)


def _song(session, title, artist_name=ARTIST):
    artist = session.query(Artist).filter_by(name=artist_name).first() or Artist(name=artist_name)
    song = Song(title=title, artist=artist)
    session.add(song)
    session.flush()
    return song


def _module(album, needs_review=False):
    def get_album_name(artist, title):
        return DiscoveryResult(album=album, matched_artist=artist, needs_review=needs_review) if album else None
    return SimpleNamespace(get_album_name=get_album_name)


def _modules(*modules):
    return [(f"m{i}", f"Module {i}", module) for i, module in enumerate(modules)]


def test_google_marks_its_album_as_a_guess(monkeypatch):
    monkeypatch.setattr(google, "_fetch_knowledge_panel_info",
                        lambda artist, song: {"artists": "Milko Kalaidjiev", "album": GUESS})

    result = google.get_album_name(ARTIST, "Kade si, batko")

    assert (result.album, result.needs_review) == (GUESS, True)


def test_a_module_after_the_guess_is_still_asked_and_wins(session):
    match = dm.discover_album(_song(session, "Kade si"), _modules(_module(GUESS, needs_review=True), _module("Real")))

    assert (match.album, match.needs_review) == ("Real", False)


def test_the_guess_is_returned_flagged_when_nothing_else_knows(session):
    match = dm.discover_album(_song(session, "Kade si"), _modules(_module(GUESS, needs_review=True), _module(None)))

    assert (match.album, match.needs_review) == (GUESS, True)


def test_the_first_of_two_guesses_is_kept(session):
    match = dm.discover_album(_song(session, "Kade si"),
                              _modules(_module(GUESS, needs_review=True), _module("Other", needs_review=True)))

    assert match.album == GUESS


def test_a_last_resort_fallback_beats_the_guess(session):
    match = dm.discover_album(_song(session, "Kade si cover"), _modules(_module(GUESS, needs_review=True)))

    assert (match.album, match.needs_review) == (SINGLES_ALBUM, False)


@pytest.fixture
def run_fill(session, monkeypatch):
    """Fill missing data -> Albums where every song gets the guess, with
    `picked` (indexes) ticked in the review."""
    def run(songs, picked):
        monkeypatch.setattr(fill_albums, "close_global_driver", lambda: None)
        monkeypatch.setattr(fill_albums, "load_max_songs_per_fetch", lambda: 100)
        monkeypatch.setattr(fill_albums, "load_discovery_modules", lambda: [])
        monkeypatch.setattr(fill_albums, "discover_album",
                            lambda *a: dm.AlbumMatch(GUESS, needs_review=True))
        monkeypatch.setattr(fill_albums, "submit_global_database_session", lambda: None)
        monkeypatch.setattr(fill_albums, "get_songs_with_empty_category", lambda category: songs)
        monkeypatch.setattr(fill_albums, "edit_db_entry",
                            lambda song, category, value: setattr(song, category, value))
        questions, listed = [], []

        def ask_yes_no(question):
            questions.append(question)
            return False

        def checkbox(message, choices):
            listed.extend(choices)
            return SimpleNamespace(ask=lambda: picked)

        monkeypatch.setattr(fill_albums, "ask_yes_no", ask_yes_no)
        monkeypatch.setattr(questionary, "checkbox", checkbox)
        fill_albums.fill_missing_albums()
        return SimpleNamespace(questions=questions, listed=listed)
    return run


def test_only_the_ticked_album_is_saved(session, run_fill):
    ticked, left = _song(session, "Kade si"), _song(session, "GSM")

    run = run_fill([left, ticked], [1])

    assert ticked.album == GUESS
    assert left.album is None
    assert any("Unable to find 1 album" in question for question in run.questions)


def test_the_list_offers_the_album_with_nothing_ticked(session, run_fill):
    song = _song(session, "Kade si")

    run = run_fill([song], [])

    assert song.album is None
    assert [choice.checked for choice in run.listed] == [False]
    assert GUESS in "".join(text for _, text in run.listed[0].title)
