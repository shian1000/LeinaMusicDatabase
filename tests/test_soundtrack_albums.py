"""A soundtrack's album is "<work> OST": reading the work from a name
(utils/common/soundtrack_names.py), the guesses for artist names without a
soundtrack word and the bringing of stored spellings to that form
(utils/database/soundtrack_albums.py, albums.unify_album_titles()) and both
reviews (utils/ui/soundtrack_review.py). In-memory SQLite with the album
listener on; prompts mocked."""

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
from config.constants import ALBUM_TYPE_SOUNDTRACK  # noqa: E402
from utils.common.soundtrack_names import soundtrack_work, weak_soundtrack_work, work_key  # noqa: E402
from utils.common.text_utils import is_soundtrack_artist  # noqa: E402
from utils.database.albums import register_album_sync  # noqa: E402
from utils.database.datatables import Album, AlbumCover, Artist, Base, Song  # noqa: E402
from utils.database.soundtrack_albums import (  # noqa: E402
    apply_soundtrack_variants,
    find_soundtrack_variants,
    guess_soundtrack_album,
    known_soundtrack_works,
)
from utils.discoveries import discoveries_manager as dm  # noqa: E402
from utils.ui import soundtrack_review as review  # noqa: E402

fill_albums = importlib.import_module(
    "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums")


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    register_album_sync(factory)
    s = factory()
    yield s
    s.close()


def _song(session, artist_name, title, album=None):
    artist = session.query(Artist).filter_by(name=artist_name).first() or Artist(name=artist_name)
    song = Song(title=title, artist=artist, album=album)
    session.add(song)
    session.flush()
    return song


def _variants(session, title):
    return next(v for v in find_soundtrack_variants(session) if v.title == title)


def _answer(monkeypatch, pick):
    """The checkbox answers pick(choices); the choices shown are returned."""
    shown = []

    def checkbox(_message, choices):
        shown.extend(choices)
        return SimpleNamespace(ask=lambda: pick(choices))

    monkeypatch.setattr(review.questionary, "checkbox", checkbox)
    return shown


@pytest.mark.parametrize("name, work", [
    ("Tekken 5 OST", "Tekken 5"),
    ("Tekken 5 Soundtrack", "Tekken 5"),
    ("Cyberpunk: Edgerunners [OST]", "Cyberpunk: Edgerunners"),
    ("Final Fantasy VII O.S.T.", "Final Fantasy VII"),
    ("Fallout 4 (Original Game Soundtrack)", "Fallout 4"),
    ("SILENT HILL 3 ORIGINAL SOUNDTRACKS", "SILENT HILL 3"),
    ("Hotline Miami - Official Soundtrack", "Hotline Miami"),
    ("Charmed: The Soundtrack", "Charmed"),
    ("Call Me By Your Name (Original Motion Picture Soundtrack)", "Call Me By Your Name"),
    # Not at the end: what follows tells apart several albums of one work
    ("Noir OST I", None),
    ("Cyberpunk Edgerunners Soundtrack Vol.1 (Ep1+2)", None),
    # Nothing left to name a work
    ("Soundtrack", None),
    ("Original Soundtrack", None),
    # Not soundtrack words
    ("Ghost", None),
    ("O.S.T.R.", None),
    (None, None),
])
def test_soundtrack_work(name, work):
    assert soundtrack_work(name) == work


@pytest.mark.parametrize("name, work", [
    ("Shin Megami Tensei III: Nocturne Music", "Shin Megami Tensei III: Nocturne"),
    ("Chrono Cross Music", "Chrono Cross"),
    ("Mother 3 Game Music", "Mother 3"),
    ("Mother 3 Video Game Music", "Mother 3"),
    ("Ys BGM", "Ys"),
    ("Music", None),
    ("C+C Music Factory", None),
])
def test_weak_soundtrack_work(name, work):
    assert weak_soundtrack_work(name) == work


def test_spellings_of_one_work_share_a_key():
    assert work_key("The Rick and Morty") == work_key("Rick and Morty")
    assert work_key("DuckTales") == work_key("Duck Tales")
    assert work_key("Tekken 5") != work_key("Tekken 4")


def test_a_real_artist_with_dots_is_not_a_soundtrack():
    assert not is_soundtrack_artist("O.S.T.R.")
    assert is_soundtrack_artist("Final Fantasy VII O.S.T.")
    assert is_soundtrack_artist("Final Fantasy VII O.S.T")


@pytest.mark.parametrize("artist, album", [
    ("Tekken 5 Soundtrack", "Tekken 5 OST"),
    ("Cyberpunk: Edgerunners [OST]", "Cyberpunk: Edgerunners OST"),
    # The marker isn't at the end: the name stays as it was
    ("Noir OST I", "Noir OST I"),
    ("O.S.T.R.", None),
])
def test_album_when_every_fetcher_is_empty(artist, album):
    modules = [("fake", "Fake", SimpleNamespace(get_album_name=lambda artist, title: None))]
    song = SimpleNamespace(artist=SimpleNamespace(name=artist, synonyms=None), title="Track", album=None)

    assert dm.discover_album_name(song, modules) == album


WORKS = {"persona 1": "Persona 1", "persona 3 reload": "Persona 3 Reload", "persona 5": "Persona 5",
         "persona 5 strikers": "Persona 5 Strikers", "chrono cross": "CHRONO CROSS", "rio": "Rio"}


@pytest.mark.parametrize("artist, album", [
    ("Shin Megami Tensei III: Nocturne Music", "Shin Megami Tensei III: Nocturne OST"),
    # The catalog's spelling of a work it knows
    ("Chrono Cross Music", "CHRONO CROSS OST"),
    ("Persona 3 Reload", "Persona 3 Reload OST"),
    ("Persona 1 (PSP)", "Persona 1 OST"),
    # The longest work the name starts with
    ("Persona 5 Strikers Arranged", "Persona 5 Strikers OST"),
    # A one-word work would read too many artists as it
    ("Rio Reiser", None),
    ("Rio", None),
    # Only whole words
    ("Persona 10", None),
    ("Metallica", None),
])
def test_guess_soundtrack_album(artist, album):
    assert guess_soundtrack_album(artist, WORKS) == album


def test_known_works_come_from_album_titles_but_not_singles(session):
    _song(session, "Tekken 4 Soundtrack", "Ghost Dance", "Tekken 4 OST")
    _song(session, "Someone", "Cave Story OST", "Singles")  # a one-song row titled after the song
    _song(session, "Metallica", "One", "...And Justice for All")

    assert known_soundtrack_works(session) == {"tekken 4": "Tekken 4"}


def test_variants_leave_out_soundtracks_already_in_the_form_and_other_albums(session):
    _song(session, "Tekken 2 OST", "A", "Tekken 2 OST")
    _song(session, "Metallica", "One", "...And Justice for All")
    _song(session, "Someone", "Single", "Singles")
    _song(session, "Noir OST I", "Canta", "Noir OST I")

    assert find_soundtrack_variants(session) == []


def test_variants_several_spellings_come_first_with_the_most_used_one(session):
    _song(session, "Zero 2", "Lone", "Zero 2 soundtrack")
    _song(session, "Final Fantasy X OST", "A", "Final Fantasy X OST")
    _song(session, "Final Fantasy X OST", "B", "Final Fantasy X OST")
    _song(session, "Final Fantasy X OST", "C", "FINAL FANTASY X Original Soundtrack")

    found = find_soundtrack_variants(session)

    assert [v.title for v in found] == ["Final Fantasy X OST", "Zero 2 OST"]
    assert found[0].titles == ["FINAL FANTASY X Original Soundtrack", "Final Fantasy X OST"]
    assert len(found[0].songs) == 3


def test_a_tie_of_spellings_is_not_settled_by_capitals(session):
    _song(session, "A", "One", "SILENT HILL 3 ORIGINAL SOUNDTRACKS")
    _song(session, "B", "Two", "Silent Hill 3 OST")

    assert [v.title for v in find_soundtrack_variants(session)] == ["Silent Hill 3 OST"]


def test_several_artists_end_on_one_various_artists_soundtrack(session):
    first = _song(session, "Tekken 5 Soundtrack", "Ground Zero Funk", "Tekken 5 Soundtrack")
    second = _song(session, "Tekken 5 OST", "Moonlit Wilderness", "Tekken 5 OST")
    old_ids = {first.album_id, second.album_id}
    assert None not in old_ids and len(old_ids) == 2

    album = apply_soundtrack_variants(session, _variants(session, "Tekken 5 OST"))
    session.commit()

    assert album.id in old_ids  # a row kept: the phone's cover cache keys on album ids
    assert album.artist_id is None and album.type == ALBUM_TYPE_SOUNDTRACK
    assert (first.album, first.album_id) == ("Tekken 5 OST", album.id)
    assert (second.album, second.album_id) == ("Tekken 5 OST", album.id)
    assert session.query(Album).filter(Album.title.like("Tekken 5%")).count() == 1


def test_one_artist_keeps_the_album_as_their_own(session):
    first = _song(session, "Square", "To Zanarkand", "Final Fantasy X OST")
    second = _song(session, "Square", "Besaid", "Final Fantasy X Original Soundtrack")

    album = apply_soundtrack_variants(session, _variants(session, "Final Fantasy X OST"))
    session.commit()

    assert album.artist_id == first.artist_id
    assert first.album_id == second.album_id == album.id


def test_the_row_with_a_cover_is_kept(session):
    older = _song(session, "A", "One", "Tekken 3 Soundtrack")
    newer = _song(session, "A", "Two", "Tekken 3 OST")
    newer.album_entry.covers.append(AlbumCover(source="manual", status="found"))
    session.flush()
    kept_id = newer.album_id
    assert older.album_id < kept_id

    album = apply_soundtrack_variants(session, _variants(session, "Tekken 3 OST"))
    session.commit()

    assert album.id == kept_id and older.album_id == kept_id


def test_songs_left_without_a_row_join_the_album(session):
    first = _song(session, "A", "One", "Gothic 3 OST")
    second = _song(session, "B", "Two", "Gothic 3 OST")
    third = _song(session, "C", "Three", "Gothic 3 Soundtrack")
    assert first.album_id is not None and second.album_id is None  # the title's second artist waits

    album = apply_soundtrack_variants(session, _variants(session, "Gothic 3 OST"))
    session.commit()

    assert {first.album_id, second.album_id, third.album_id} == {album.id}


def test_a_later_edit_of_a_merged_song_stays_on_the_album(session):
    first = _song(session, "A", "One", "Bully Soundtrack")
    _song(session, "B", "Two", "Bully OST")
    album = apply_soundtrack_variants(session, _variants(session, "Bully OST"))
    session.commit()

    first.title = "One (renamed)"
    session.commit()

    assert first.album_id == album.id


def _names_review(monkeypatch, session):
    monkeypatch.setattr(review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(review, "submit_global_database_session", session.commit)


def test_names_review_ticks_only_soundtracks_spelt_several_ways(monkeypatch, session):
    _names_review(monkeypatch, session)
    several = _song(session, "A", "One", "Yakuza Soundtrack")
    _song(session, "A", "Two", "Yakuza OST")
    lone = _song(session, "B", "Three", "Hohokum Soundtrack")
    shown = _answer(monkeypatch, lambda choices: [c.value for c in choices if c.checked])

    assert review.review_soundtrack_names() == 1

    assert [bool(c.checked) for c in shown] == [True, False]
    assert several.album == "Yakuza OST"
    assert lone.album == "Hohokum Soundtrack"


@pytest.mark.parametrize("answer", [[], None])
def test_names_review_changes_nothing_without_a_tick(monkeypatch, session, answer):
    _names_review(monkeypatch, session)
    song = _song(session, "A", "One", "Hohokum Soundtrack")
    _answer(monkeypatch, lambda choices: answer)

    assert review.review_soundtrack_names() == 0
    assert song.album == "Hohokum Soundtrack"


def test_names_review_with_nothing_to_do_asks_nothing(monkeypatch, session):
    _names_review(monkeypatch, session)
    shown = _answer(monkeypatch, lambda choices: pytest.fail("asked"))

    assert review.review_soundtrack_names() == 0
    assert shown == []


def test_guess_review_ticks_nothing_and_returns_only_what_was_picked(monkeypatch, session):
    first = _song(session, "Chrono Cross Music", "Scars of Time")
    second = _song(session, "Amiga Music", "Track")
    found = [(first, "Chrono Cross OST"), (second, "Amiga OST")]
    shown = _answer(monkeypatch, lambda choices: [choices[0].value])

    assert review.review_soundtrack_guesses(found) == [found[0]]
    assert not any(c.checked for c in shown)


def test_guess_review_without_guesses_asks_nothing(monkeypatch):
    _answer(monkeypatch, lambda choices: pytest.fail("asked"))

    assert review.review_soundtrack_guesses([]) == []


@pytest.fixture
def run_fill(monkeypatch, session):
    """Runs fill_missing_albums() with no module finding anything; the
    checkbox answers `pick`, `swapped` is what the lookup with the names the
    other way round finds. Returns the questions asked."""
    def run(songs, pick, swapped=None):
        monkeypatch.setattr(fill_albums, "open_global_driver", lambda: None)
        monkeypatch.setattr(fill_albums, "close_global_driver", lambda: None)
        monkeypatch.setattr(fill_albums, "load_max_songs_per_fetch", lambda: 100)
        monkeypatch.setattr(fill_albums, "load_discovery_modules", lambda: [])
        monkeypatch.setattr(fill_albums, "discover_album", lambda *a: None)
        monkeypatch.setattr(fill_albums, "submit_global_database_session", lambda: None)
        monkeypatch.setattr(fill_albums, "require_global_database_sessions", lambda: (session, None))
        monkeypatch.setattr(fill_albums, "get_songs_with_empty_category", lambda category: songs)
        monkeypatch.setattr(fill_albums, "edit_db_entry",
                            lambda song, category, value: setattr(song, category, value))
        monkeypatch.setattr(fill_albums, "may_be_swapped", lambda s, song: swapped is not None)
        monkeypatch.setattr(fill_albums, "discover_album_after_swap", lambda song, modules: swapped)
        monkeypatch.setattr(fill_albums, "review_swapped_albums", lambda found: [])
        questions = []

        def ask_yes_no(question):
            questions.append(question)
            return False

        monkeypatch.setattr(fill_albums, "ask_yes_no", ask_yes_no)
        _answer(monkeypatch, pick)
        fill_albums.fill_missing_albums()
        return questions
    return run


def test_albums_saves_a_ticked_guess_and_counts_it_as_found(session, run_fill):
    song = _song(session, "Shin Megami Tensei III: Nocturne Music", "Normal Battle (Town)")

    questions = run_fill([song], lambda choices: [choices[0].value])

    assert song.album == "Shin Megami Tensei III: Nocturne OST"
    assert not any("Unable to find" in q for q in questions)


def test_albums_leaves_an_unticked_guess_without_an_album(session, run_fill):
    song = _song(session, "Amiga Music", "Track")

    questions = run_fill([song], lambda choices: [])

    assert song.album is None
    assert any("Unable to find 1 album" in q for q in questions)


def test_albums_takes_the_guess_from_a_work_the_catalog_knows(session, run_fill):
    _song(session, "Persona 3 OST", "Mass Destruction", "Persona 3 OST")
    song = _song(session, "Persona 3 (PSP)", "Burn My Dread")

    run_fill([song], lambda choices: [choices[0].value])

    assert song.album == "Persona 3 OST"


def test_albums_does_not_guess_for_a_song_found_swapped(session, run_fill):
    song = _song(session, "Some Music", "Artist")

    run_fill([song], lambda choices: pytest.fail("asked"), swapped=dm.AlbumMatch("Album"))

    assert song.album is None
