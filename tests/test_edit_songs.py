"""The song / artist editor (src/menu/song_actions/edit_songs.py) and
edit_db_entry() (utils/database/database_management.py): only fields that
can be edited are offered (DB-05, UI-04), and "Add song" goes through the
import (UI-05), and an artist's synonyms are edited cleaned
(set_artist_synonyms()). In-memory music.db, mocked prompts."""
import importlib

import pytest
import questionary
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import menu.main_menu  # noqa: F401 - imported first, as main.py does (menu.song_actions alone is circular)
from utils.database import database_management as dm
from utils.database.datatables import Artist, Base, Song
from utils.database.song_artists import set_artist_synonyms

edit_songs = importlib.import_module("menu.song_actions.edit_songs")


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def song(session):
    song = Song(title="Roxanne", artist=Artist(name="The Police", origin="UK"))
    session.add(song)
    session.commit()
    return song


@pytest.mark.parametrize("category", ["tag", "name", "full album", "artist id "])
def test_edit_db_entry_reports_what_it_cant_edit(song, category, capsys):
    before = (song.title, song.album, song.year, song.language)
    if category.strip() == "artist id":
        with pytest.raises(ValueError):  # the artist's, never the song's
            dm.edit_db_entry(song, category, "5")
        return
    assert dm.edit_db_entry(song, category, "x") is False
    out = capsys.readouterr().out
    assert "Can't edit" in out and "Updated" not in out
    assert (song.title, song.album, song.year, song.language) == before


def test_edit_db_entry_sets_songs_and_artists_fields(song, capsys):
    assert dm.edit_db_entry(song, "year", " 1978 ") is True
    assert song.year == 1978
    assert dm.edit_db_entry(song, "year", "197x") is False and song.year == 1978
    assert dm.edit_db_entry(song, "album", "Outlandos d'Amour") and song.album == "Outlandos d'Amour"
    assert dm.edit_db_entry(song.artist, "artist origin", "GB") and song.artist.origin == "GB"
    assert dm.edit_db_entry(song.artist, "artist id", "7") is False
    assert "'United Kingdom' --> 'GB'" in capsys.readouterr().out


def test_song_editor_offers_only_editable_fields(song, monkeypatch):
    offered = []

    def select(message, choices):
        offered.extend(choices)
        return _Answer(next(c for c in choices if c.startswith("year")))

    monkeypatch.setattr(edit_songs.questionary, "select", select)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1978")
    monkeypatch.setattr(edit_songs, "submit_global_database_session", lambda: None)

    edit_songs.edit_entry_menu(db_object=song)

    assert not [c for c in offered if c.startswith(("tag", "artist id", "artist origin", "artist name"))]
    assert offered[:2] == ["title (Roxanne)", "artist (The Police)"]
    assert song.year == 1978


def _add_song(monkeypatch, answers):
    imported, validators = [], {}

    def text(question, validate):
        validators[question] = validate
        return _Answer(answers.pop(0))

    monkeypatch.setattr(edit_songs.questionary, "text", text)
    monkeypatch.setattr(edit_songs, "run_import_batch", lambda entries: imported.extend(entries) or [])
    edit_songs.add_songs_menu()
    return imported, validators


def test_add_song_goes_through_the_import(monkeypatch):
    imported, validators = _add_song(monkeypatch, [" Roxanne ", "The Police", "", "1978", "", "UK"])

    assert imported == [{"title": "Roxanne", "artist_name": "The Police", "album": None, "year": 1978,
                         "language": None, "origin": "UK"}]
    assert validators["Title:"]("   ") == "Can't be blank"
    assert validators["Artist:"]("") == "Can't be blank"
    assert validators["Album (optional):"]("") is True
    assert validators["Year (optional):"]("197x") == "Type a whole number"


def test_add_song_cancelled_adds_nothing(monkeypatch, capsys):
    imported, _ = _add_song(monkeypatch, ["Roxanne", None])

    assert imported == []
    assert "Aborted" in capsys.readouterr().out


# ---- Manage database → Divide artists (UI-03) ----

def _divide_menu():
    return importlib.import_module("menu.main_menu.enter_database.manage_database.merge_divide_menu")


def test_divide_artists_back_does_nothing(song, monkeypatch):
    menu = _divide_menu()
    monkeypatch.setattr(menu, "ask_for_entries_list", lambda mode: [song.artist])
    monkeypatch.setattr(menu, "pick_from_db_objects", lambda objects, question: None)
    monkeypatch.setattr(menu, "divide_artist", lambda artist: pytest.fail("divided"))

    menu.divide_artists_menu()  # used to be an AttributeError on None.id


def test_divide_artists_lists_the_artists_songs(session, song, monkeypatch, capsys):
    menu = _divide_menu()
    session.add(Song(title="Message in a Bottle", artist=song.artist, year=1979))
    session.commit()
    monkeypatch.setattr(dm, "get_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(dm, "require_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(menu, "ask_for_entries_list", lambda mode: [song.artist])
    monkeypatch.setattr(menu, "pick_from_db_objects", lambda objects, question: objects[0])
    monkeypatch.setattr(menu, "ask_yes_no", lambda q: True)
    monkeypatch.setattr(menu, "submit_global_database_session", session.commit)

    menu.divide_artists_menu()

    out = capsys.readouterr().out
    assert "Found 2 song(s)" in out and "Message in a Bottle" in out
    assert session.query(Artist).filter_by(name="The Police").count() == 2


def test_divide_artist_leaves_the_commit_to_the_caller(session, song, monkeypatch):
    session.add(Song(title="Message in a Bottle", artist=song.artist, year=1979))
    session.commit()
    monkeypatch.setattr(dm, "require_global_database_sessions", lambda: (session, None))

    dm.divide_artist(song.artist)
    session.rollback()

    assert session.query(Artist).filter_by(name="The Police").count() == 1


# ==================== an artist's synonyms ====================


@pytest.mark.parametrize("stored, names, expected", [
    # the broken spelling unticked, the good one kept
    ("Tiesto, DJ TI¨STO", ["Tiesto"], "Tiesto"),
    # the wrong person removed, nothing left = NULL
    ("David Byrne", [], None),
    # trimmed, empties and repeats (case, accents) dropped, the first spelling kept
    (None, ["  Tiësto ", "", " ", "TIESTO", "tiesto", "DJ Tiesto Official"], "Tiësto, DJ Tiesto Official"),
    # the artist's own name and its Latin form are no synonyms
    (None, ["dj tiesto", "Kino", "Tijs Verwest"], "Tijs Verwest"),
    # a typed comma-separated text is several names
    ("Tiesto", ["Tiesto", "Tijs Verwest,  Allure ,"], "Tiesto, Tijs Verwest, Allure"),
])
def test_set_artist_synonyms_cleans_the_list(session, stored, names, expected):
    artist = Artist(name="DJ Tiesto", name_latin="Kino", synonyms=stored)
    session.add(artist)
    session.commit()

    assert set_artist_synonyms(artist, names) == stored
    session.commit()
    session.expire_all()
    assert artist.synonyms == expected


@pytest.fixture
def editor(session, monkeypatch):
    """Runs the artist editor's "synonyms" entry with these answers: the
    ticked synonyms (indices, None = Ctrl+C) and the typed text."""
    def run(artist, ticked, typed):
        asked = {"select": [], "checkbox": [], "text": []}

        def select(message, choices):
            asked["select"].extend(choices)
            return _Answer(next(c for c in choices if c.startswith("synonyms")))

        def checkbox(message, choices):
            asked["checkbox"].append((message, [(c.title, c.checked) for c in choices]))
            return _Answer(ticked)

        def text(message, **kwargs):
            asked["text"].append(message)
            return _Answer(typed)

        monkeypatch.setattr(edit_songs.questionary, "select", select)
        monkeypatch.setattr(questionary, "checkbox", checkbox)
        monkeypatch.setattr(edit_songs.questionary, "text", text)
        monkeypatch.setattr(edit_songs, "submit_global_database_session", session.commit)
        edit_songs.edit_entry_menu(db_object=artist)
        return asked
    return run



def _artist(session, name, synonyms):
    artist = Artist(name=name, synonyms=synonyms)
    session.add(artist)
    session.commit()
    return artist


def test_artist_editor_offers_synonyms(session, editor):
    artist = _artist(session, "Paramore", None)
    asked = editor(artist, None, "")
    assert asked["select"] == ["artist name (Paramore)", "artist origin (None)", "name in Latin letters (—)",
                               "synonyms (—)", "Delete from database", "back"]
    assert asked["checkbox"] == []  # nothing to keep or drop
    assert asked["text"] == ["Add synonyms (comma-separated, empty = none):"]
    assert artist.synonyms is None


def test_artist_editor_drops_an_unticked_synonym(session, editor, capsys):
    artist = _artist(session, "Paramore", "David Byrne")
    asked = editor(artist, [], "")
    session.expire_all()
    assert artist.synonyms is None
    assert asked["checkbox"] == [("Which to keep?", [("David Byrne", True)])]
    out = capsys.readouterr().out
    assert "Synonyms of Paramore:\n  David Byrne\n" in out
    assert "Updated synonyms for 'Paramore': 'David Byrne' --> '---'" in out


def test_artist_editor_keeps_ticked_and_adds_typed(session, editor, capsys):
    artist = _artist(session, "DJ Tiesto", "Tiesto, DJ TI¨STO")
    asked = editor(artist, [0], " Tiësto , Tijs Verwest, dj tiesto")
    session.expire_all()
    assert asked["checkbox"] == [("Which to keep?", [("Tiesto", True), ("DJ TI¨STO", True)])]
    assert artist.synonyms == "Tiesto, Tijs Verwest"
    assert "Updated synonyms for 'DJ Tiesto': 'Tiesto, DJ TI¨STO' --> 'Tiesto, Tijs Verwest'" in capsys.readouterr().out


@pytest.mark.parametrize("ticked, typed", [(None, ""), ([0], None)])
def test_artist_editor_cancelled_changes_nothing(session, editor, ticked, typed, capsys):
    artist = _artist(session, "DJ Tiesto", "Tiesto, DJ TI¨STO")
    editor(artist, ticked, typed)
    assert artist.synonyms == "Tiesto, DJ TI¨STO"
    assert "Aborted" in capsys.readouterr().out


def test_artist_editor_unchanged_synonyms_are_said_so(session, editor, capsys):
    artist = _artist(session, "DJ Tiesto", "Tiesto")
    editor(artist, [0], "tiesto")
    assert artist.synonyms == "Tiesto"
    assert "unchanged" in capsys.readouterr().out


def test_artist_actions_add_and_remove_synonym_clean_the_list(session, monkeypatch, capsys):
    import menu.artist_actions as artist_actions
    artist = _artist(session, "DJ Tiesto", "Tiesto")
    monkeypatch.setattr(artist_actions, "submit_global_database_session", session.commit)

    monkeypatch.setattr("builtins.input", lambda prompt="": " DJ Tiesto ")
    artist_actions.add_synonym_menu([artist])  # its own name
    assert artist.synonyms == "Tiesto" and "already" in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda prompt="": " Tijs Verwest ")
    artist_actions.add_synonym_menu([artist])
    assert artist.synonyms == "Tiesto, Tijs Verwest"

    monkeypatch.setattr(artist_actions.questionary, "checkbox", lambda message, choices: _Answer(["Tiesto", "Tijs Verwest"]))
    artist_actions.remove_synonym_menu([artist])
    assert artist.synonyms is None


@pytest.mark.parametrize("confirmed", [True, False])
def test_deleting_a_requested_song_from_the_editor_rejects_its_request_only_once_confirmed(
        monkeypatch, tmp_path, confirmed):
    # "Delete from database" -> "Delete it and reject the request" (2026-11-24, #6970)
    from utils.database import database_sessions
    from utils.database import inbox_requests as pr
    from utils.database.datatables import RejectedSongRequest, SettledSongRequest
    from utils.database.song_references import SongUses, register_song_reference_sync
    review = importlib.import_module("utils.ui.song_delete_review")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    register_song_reference_sync(factory)
    session = factory()
    song = Song(id=6970, title="Muzyka na jesień (The Cure, Talk Talk, Foghorn)",
                artist=Artist(name="Kawa i Awangarda"), youtube_video_id="GMmTW6I8vjI")
    session.add(song)
    session.flush()
    session.add(SettledSongRequest(artist="Kawa i Awangarda", title="The Cure", youtube_video_id="GMmTW6I8vjI",
                                   song_id=6970, settled_at="x"))
    session.commit()
    monkeypatch.setattr(pr, "INBOX_DIR", tmp_path)
    monkeypatch.setattr(pr, "inbox_available", lambda: True)
    monkeypatch.setattr(database_sessions, "_GLOBAL_DATABASE_SESSION", (session, None))
    monkeypatch.setattr(edit_songs, "submit_global_database_session", session.commit)
    monkeypatch.setattr(review, "song_uses", lambda song_id: SongUses(requests=1))
    monkeypatch.setattr(review.questionary, "select", lambda message, choices: _Answer(review._DELETE_AND_REJECT))
    monkeypatch.setattr(edit_songs, "ask_yes_no", lambda question: confirmed)

    edit_songs._delete_entry(song, "Song")
    session.rollback()  # what wasn't committed

    rejected = [(r.artist, r.title, r.youtube_video_id) for r in session.query(RejectedSongRequest)]
    if confirmed:
        assert session.get(Song, 6970) is None
        assert rejected == [("Kawa i Awangarda", "The Cure", "GMmTW6I8vjI")]
        assert pr.load_decisions()["song:kawa i awangarda|the cure"]["decision"] == "rejected"
    else:
        assert session.get(Song, 6970) is not None and rejected == []
        assert session.query(SettledSongRequest).count() == 1
    session.close()
