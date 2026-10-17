"""Crediting songs to another artist row instead of renaming the shared one
(utils/database/artist_reassignment.py) and the questions around it
(utils/ui/artist_change.py), with mocked prompts. In-memory SQLite with the
album listener on."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database.albums import register_album_sync
from utils.database.datatables import Album, Artist, Base, Song
from utils.database import artist_reassignment as ar
from utils.database import database_management
from utils.database.song_artists import add_additional_artist, additional_artist_role, song_artist_label
from utils.ui import artist_change as ac


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    register_album_sync(factory)
    s = factory()
    yield s
    s.close()


def _artist(session, name, **kw):
    artist = Artist(name=name, **kw)
    session.add(artist)
    session.flush()
    return artist


def _song(session, title, artist, **kw):
    song = Song(title=title, artist=artist, **kw)
    session.add(song)
    session.flush()
    return song


def _names(session):
    return sorted(a.name for a in session.query(Artist))


# ---- artist_reassignment ------------------------------------------------------

def test_set_primary_artist_moves_only_that_song(session):
    sting, typo = _artist(session, "Sting"), _artist(session, "Stingg")
    fields = _song(session, "Fields of Gold", typo)
    rose = _song(session, "Desert Rose", typo)

    ar.set_primary_artist(session, fields, sting)

    assert fields.artist is sting and rose.artist is typo
    assert typo.name == "Stingg"  # the row isn't renamed
    assert not ar.is_unused(session, typo)


def test_set_primary_artist_refuses_the_same_artist(session):
    sting = _artist(session, "Sting")
    song = _song(session, "Fields of Gold", sting)
    with pytest.raises(ValueError):
        ar.set_primary_artist(session, song, sting)


def test_new_primary_artist_loses_its_additional_link_old_one_kept_as_asked(session):
    police, sting = _artist(session, "Police"), _artist(session, "Sting")
    song = _song(session, "Roxanne", police)
    add_additional_artist(song, sting, "feat")
    session.flush()

    ar.set_primary_artist(session, song, sting, keep_old_as="main")

    assert song_artist_label(song) == "Sting x Police"
    assert additional_artist_role(song, sting) is None


def test_old_primary_artist_dropped_when_not_kept(session):
    police, sting = _artist(session, "Police"), _artist(session, "Sting")
    song = _song(session, "Roxanne", police)
    add_additional_artist(song, sting, "feat")
    session.flush()

    ar.set_primary_artist(session, song, sting)

    assert song_artist_label(song) == "Sting"
    assert ar.is_unused(session, police)


def test_album_title_shared_by_two_artists_gets_a_row_once_one_is_left(session):
    sting, typo = _artist(session, "Sting"), _artist(session, "Stingg")
    # One flush: added one by one, the first would get the album alone
    moved = Song(title="Fields of Gold", artist=typo, album="Ten Summoner's Tales")
    stayed = Song(title="Shape of My Heart", artist=sting, album="Ten Summoner's Tales")
    session.add_all([moved, stayed])
    session.flush()
    assert moved.album_id is None and stayed.album_id is None  # left for the review

    ar.set_primary_artist(session, moved, sting)

    assert moved.album_id is not None and moved.album_id == stayed.album_id
    assert session.get(Album, moved.album_id).artist is sting


def test_remove_unused_artist_hands_albums_over_and_keeps_names_on_request(session):
    sting, typo = _artist(session, "Sting"), _artist(session, "Stingg", name_latin=None)
    song = _song(session, "Fields of Gold", typo, album="Ten Summoner's Tales")
    album_id = song.album_id
    empty = Album(title="Leftover", artist=typo)
    session.add(empty)
    session.flush()
    ar.set_primary_artist(session, song, sting)
    with pytest.raises(ValueError):
        ar.remove_unused_artist(session, sting, typo, keep_names=False)

    ar.remove_unused_artist(session, typo, sting, keep_names=True)

    assert _names(session) == ["Sting"]
    assert sting.synonyms == "Stingg"
    assert empty.artist is sting
    assert song.album_id == album_id


def test_remove_unused_artist_without_names(session):
    sting, typo = _artist(session, "Sting"), _artist(session, "Stingg")
    ar.remove_unused_artist(session, typo, sting, keep_names=False)
    assert _names(session) == ["Sting"] and sting.synonyms is None


def test_swap_names_of_artist_renames_the_row_and_swaps_latin_forms(session):
    artist = _artist(session, "Кукушка", name_latin="Kukushka")
    song = _song(session, "Кино", artist, title_latin="Kino")

    ar.swap_names_of_artist(song)

    assert (artist.name, artist.name_latin) == ("Кино", "Kino")
    assert (song.title, song.title_latin) == ("Кукушка", "Kukushka")


def test_move_swapped_song_to_a_new_artist_leaves_the_other_songs(session):
    zvezda = _artist(session, "Zvezda", name_latin=None)
    swapped = _song(session, "Кино", zvezda, title_latin="Kino")
    other = _song(session, "Another", zvezda)

    artist, removed = ar.move_swapped_song(session, swapped)

    assert removed is None
    assert (artist.name, artist.name_latin) == ("Кино", "Kino")
    assert swapped.artist is artist and swapped.title == "Zvezda"
    assert other.artist is zvezda and zvezda.name == "Zvezda"


def test_move_swapped_song_to_an_existing_artist_deletes_the_emptied_one(session):
    styx = _artist(session, "Styx")
    _song(session, "Come Sail Away", styx)
    renegade = _artist(session, "Renegade")
    swapped = _song(session, "Styx", renegade)

    artist, removed = ar.move_swapped_song(session, swapped, styx)

    assert artist is styx and removed is renegade
    assert swapped.title == "Renegade"
    assert _names(session) == ["Styx"]


def test_artists_named_matches_name_or_latin_form_normalized(session):
    kino = _artist(session, "Кино", name_latin="Kino")
    other = _artist(session, "KINO!")
    _artist(session, "Kino Band")

    assert ar.artists_named(session, "kino") == [kino, other]
    assert ar.artists_named(session, "kino", exclude=kino) == [other]
    assert ar.artists_named(session, "  ") == []


# ---- artist_change (mocked prompts) -------------------------------------------

class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


@pytest.fixture
def ui(session, monkeypatch):
    """Answers to select() are matched by the start of the choice label;
    confirm() always says yes; input() pops `inputs`."""
    answers, inputs, asked = [], [], []

    def select(message, choices):
        want = answers.pop(0)
        pick = next(c for c in choices if c.startswith(want))
        asked.append((message, pick))
        return _Answer(pick)

    def confirm(message, **kwargs):
        asked.append((message, True))
        return _Answer(True)

    monkeypatch.setattr(ac.questionary, "select", select)
    monkeypatch.setattr(ac.questionary, "confirm", confirm)
    monkeypatch.setattr("builtins.input", lambda prompt="": inputs.pop(0))
    monkeypatch.setattr(ac, "get_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(database_management, "get_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(ac, "submit_global_database_session", session.commit)
    monkeypatch.setattr(ac, "display_songs", lambda songs: None)
    monkeypatch.setattr(ac, "pick_from_db_objects", lambda objects, **kw: objects[0])

    def find_artists(category, name):
        return ar.artists_named(session, name)

    monkeypatch.setattr(ac, "get_artists_from_db_session", find_artists)
    return answers, inputs, asked


def test_change_artist_of_several_songs_and_merge_the_emptied_one(session, ui):
    answers, inputs, _ = ui
    sting, typo = _artist(session, "Sting"), _artist(session, "Stingg")
    songs = [_song(session, "Fields of Gold", typo), _song(session, "Desert Rose", typo)]
    inputs.append("Sting")
    answers.append("Merge")

    ac.change_songs_artist(songs)

    assert all(s.artist is sting for s in songs)
    assert _names(session) == ["Sting"] and sting.synonyms == "Stingg"


def test_change_artist_keeps_the_emptied_artist_when_asked(session, ui):
    answers, inputs, _ = ui
    sting, typo = _artist(session, "Sting"), _artist(session, "Stingg")
    song = _song(session, "Fields of Gold", typo)
    inputs.append("Sting")
    answers.append("Keep")

    ac.change_songs_artist([song])

    assert song.artist is sting and _names(session) == ["Sting", "Stingg"]


def test_change_artist_asks_about_the_old_one_when_the_new_one_is_credited(session, ui):
    answers, inputs, asked = ui
    police, sting = _artist(session, "Police"), _artist(session, "Sting")
    song = _song(session, "Roxanne", police)
    add_additional_artist(song, sting, "feat")
    session.flush()
    inputs.append("Sting")
    answers.append('"Police" stays as a guest')

    ac.change_songs_artist([song])

    assert song_artist_label(song) == "Sting feat. Police"
    assert len(asked) == 1  # Police still has the song: no clean-up question


def test_change_artist_skipping_the_song_changes_nothing(session, ui):
    answers, inputs, _ = ui
    police, sting = _artist(session, "Police"), _artist(session, "Sting")
    song = _song(session, "Roxanne", police)
    add_additional_artist(song, sting, "feat")
    session.flush()
    inputs.append("Sting")
    answers.append("Skip")

    ac.change_songs_artist([song])

    assert song_artist_label(song) == "Police feat. Sting"


def test_change_artist_to_a_new_artist(session, ui):
    answers, inputs, _ = ui
    typo = _artist(session, "Stingg")
    _artist(session, "Police")
    song = _song(session, "Fields of Gold", typo)
    _song(session, "Desert Rose", typo)
    inputs.append("Sting")

    ac.change_songs_artist([song])

    assert song.artist.name == "Sting" and song.artist is not typo
    assert _names(session) == ["Police", "Sting", "Stingg"]


def test_swap_single_song_artist_renames_it(session, ui):
    artist = _artist(session, "Bad Romance")
    song = _song(session, "Lady Gaga", artist)

    ac.swap_titles_and_artist_names([song])

    assert artist.name == "Lady Gaga" and song.title == "Bad Romance"


def test_swap_single_song_artist_offers_an_existing_artist_named_like_the_title(session, ui):
    answers, _, _ = ui
    styx = _artist(session, "Styx")
    renegade = _artist(session, "Renegade")
    song = _song(session, "Styx", renegade)
    answers.append("Credit it to the existing")

    ac.swap_titles_and_artist_names([song])

    assert song.artist is styx and song.title == "Renegade"
    assert _names(session) == ["Styx"]


def test_swap_with_other_songs_can_move_only_this_song(session, ui):
    answers, _, _ = ui
    zvezda = _artist(session, "Zvezda")
    swapped = _song(session, "Kino", zvezda)
    other = _song(session, "Another", zvezda)
    answers.append("Move only this song")

    ac.swap_titles_and_artist_names([swapped])

    assert swapped.artist.name == "Kino" and swapped.title == "Zvezda"
    assert other.artist is zvezda and zvezda.name == "Zvezda"


def test_swap_with_other_songs_renames_for_all_once_per_artist(session, ui):
    answers, _, _ = ui
    artist = _artist(session, "Title A")
    first = _song(session, "Band", artist)
    second = _song(session, "Band2", artist)
    answers.append("Rename")

    ac.swap_titles_and_artist_names([first, second])

    assert artist.name == "Band"
    assert first.title == "Title A"
    assert second.title == "Band2"  # skipped: its artist was already renamed


def test_swap_skipped_changes_nothing(session, ui):
    answers, _, _ = ui
    artist = _artist(session, "Title A")
    first = _song(session, "Band", artist)
    _song(session, "Band2", artist)
    answers.append("Skip")

    ac.swap_titles_and_artist_names([first])

    assert artist.name == "Title A" and first.title == "Band"


def test_rename_artist_onto_an_existing_name_offers_a_merge(session, ui):
    answers, _, _ = ui
    sting, typo = _artist(session, "Sting"), _artist(session, "Stingg")
    song = _song(session, "Fields of Gold", typo)
    answers.append("Merge")

    assert ac.rename_artist(typo, "sting") is True

    session.expire_all()
    assert _names(session) == ["Sting"] and song.artist_id == sting.id


def test_rename_artist_without_a_twin_or_when_renaming_anyway(session, ui):
    answers, _, _ = ui
    _artist(session, "Sting")
    typo = _artist(session, "Stingg")

    assert ac.rename_artist(typo, "Gordon Sumner") is False
    answers.append("Rename anyway")
    assert ac.rename_artist(typo, "Sting") is False
    answers.append("Cancel")
    assert ac.rename_artist(typo, "Sting") is True
    assert _names(session) == ["Sting", "Stingg"]
