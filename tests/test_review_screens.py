"""Review screens no test reached (UI-12), prompts answered by the test:
Fill missing data -> Genres (utils/ui/genre_review.py: the lookup's three
outcomes, songs that had nothing before skipped, the artist-level list, typing
the rest, Set genre), Find full albums (utils/ui/full_album_review.py) and
Import a request package (utils/ui/request_package_import.py). In-memory
music.db; MusicBrainz, YouTube and the inbox faked."""
from types import SimpleNamespace

import pytest
import questionary
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.constants import GENRE_LEVEL_ALBUM, GENRE_LEVEL_ARTIST, GENRE_LEVEL_SONG
from utils.database.datatables import Artist, Base, Song
from utils.database.request_packages import ImportResult, PackageError
from utils.ui import full_album_review as far
from utils.ui import genre_review as gr
from utils.ui import request_package_import as rpi


def _answer(value):
    return SimpleNamespace(ask=lambda: value)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _songs(session, *titles, genre=None):
    artist = Artist(name="Hey")
    session.add(artist)
    songs = [Song(title=t, artist=artist, genre=genre) for t in titles]
    session.add_all(songs)
    session.commit()
    return songs


# ==================== genres ====================

@pytest.fixture
def genres(session, monkeypatch):
    state = SimpleNamespace(misses=set(), saved_misses=None, results={}, max_songs=100)
    monkeypatch.setattr(gr, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(gr, "submit_global_database_session", session.commit)
    monkeypatch.setattr(gr, "load_misses", lambda: set(state.misses))
    monkeypatch.setattr(gr, "save_misses", lambda ids: setattr(state, "saved_misses", set(ids)))
    monkeypatch.setattr(gr, "load_max_songs_per_fetch", lambda: state.max_songs)

    class FakeLookup:
        def __init__(self, known):
            pass

        def lookup(self, song):
            found = state.results.get(song.title)
            return None if found is None else SimpleNamespace(genres=found[0], level=found[1])
    monkeypatch.setattr(gr, "GenreLookup", FakeLookup)
    return state


def test_genres_found_for_the_song_or_album_are_saved_at_once(session, genres, monkeypatch):
    a, b, c = _songs(session, "Teksański", "Cisza", "Nowy")
    genres.results = {"Teksański": ("rock, Alternative Rock", GENRE_LEVEL_SONG),
                      "Cisza": ("rock", GENRE_LEVEL_ALBUM)}
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)  # don't type the rest

    gr.fill_missing_genres()
    assert (a.genre, b.genre, c.genre) == ("rock, alternative rock", "rock", None)
    assert genres.saved_misses == {c.id}  # nothing on MusicBrainz: skipped next time


def test_songs_that_had_nothing_before_are_skipped(session, genres, monkeypatch, capsys):
    a, b = _songs(session, "Teksański", "Cisza")
    genres.misses = {b.id}
    asked = []
    monkeypatch.setattr(gr.GenreLookup, "lookup", lambda self, song: asked.append(song.title) or None)
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)

    gr.fill_missing_genres()
    assert asked == ["Teksański"]
    assert "1 of them had nothing on MusicBrainz before" in capsys.readouterr().out


def test_only_max_songs_per_fetch_are_looked_up(session, genres, monkeypatch):
    _songs(session, "A", "B", "C")
    genres.max_songs = 2
    asked = []
    monkeypatch.setattr(gr.GenreLookup, "lookup", lambda self, song: asked.append(song.title) or None)
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)

    gr.fill_missing_genres()
    assert asked == ["A", "B"]


def test_artist_level_genres_are_saved_only_for_the_ticked_songs(session, genres, monkeypatch):
    a, b = _songs(session, "Teksański", "Cisza")
    genres.results = {"Teksański": ("rock", GENRE_LEVEL_ARTIST), "Cisza": ("rock", GENRE_LEVEL_ARTIST)}
    lists = []
    monkeypatch.setattr(questionary, "checkbox",
                        lambda message, choices: lists.append([c.checked for c in choices]) or _answer([1]))

    gr.fill_missing_genres()
    assert lists == [[True, True]]          # all ticked to start with
    # listed by artist, then title: "Cisza" (b) is first, the ticked index 1 is "Teksański" (a)
    assert (a.genre, b.genre) == ("rock", None)


def test_genres_typed_by_hand_for_what_musicbrainz_didnt_know(session, genres, monkeypatch):
    a, b, c = _songs(session, "A", "B", "C")
    typed = iter(["Synth-Pop", "", None])  # a genre, skip one, Ctrl+C
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: True)
    monkeypatch.setattr(questionary, "text", lambda message, **k: _answer(next(typed)))

    gr.fill_missing_genres()
    assert (a.genre, b.genre, c.genre) == ("synth-pop", None, None)


def test_every_song_with_a_genre_asks_nothing(session, genres, capsys):
    _songs(session, "A", genre="rock")
    gr.fill_missing_genres()
    assert "Every song has a genre." in capsys.readouterr().out


def test_set_genre_of_several_songs_and_clearing_it(session, monkeypatch):
    a, b = _songs(session, "A", "B")
    monkeypatch.setattr(gr, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(gr, "submit_global_database_session", session.commit)
    answers = iter(["Rock, Pop", "", None])
    monkeypatch.setattr(questionary, "text", lambda message, default="": _answer(next(answers)))

    gr.set_genre_of_songs([a, b])
    assert (a.genre, b.genre) == ("rock, pop", "rock, pop")
    gr.set_genre_of_songs([a])
    assert a.genre is None
    gr.set_genre_of_songs([b])  # Ctrl+C: unchanged
    assert b.genre == "rock, pop"


# ==================== full albums ====================

def _candidate(song, title_says_so, cleaned=None):
    return SimpleNamespace(song=song, reasons=["title"] if title_says_so else ["long video"],
                           title_says_so=title_says_so, cleaned_title=cleaned or song.title)


@pytest.fixture
def albums(session, monkeypatch):
    state = SimpleNamespace(candidates=[], kinds=[], ignored=[], answers=[])
    monkeypatch.setattr(far, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(far, "submit_global_database_session", session.commit)
    monkeypatch.setattr(far, "long_song_files", lambda minimum: {})
    monkeypatch.setattr(far, "unmarked_video_ids", lambda s: [])
    monkeypatch.setattr(far, "video_durations", lambda s, ids: {})
    monkeypatch.setattr(far, "find_full_album_candidates", lambda s, files, videos: state.candidates)
    monkeypatch.setattr(far, "set_song_kind", lambda songs, kind: state.kinds.append([s.title for s in songs]) or [])
    monkeypatch.setattr(far, "add_to_full_album_ignore_list", state.ignored.extend)
    monkeypatch.setattr(far, "ask_yes_no", lambda q: state.answers.pop(0))
    monkeypatch.setattr(far, "song_artist_label", lambda song: song.artist.name)
    return state


def test_ticked_songs_are_marked_renamed_and_the_rest_remembered(session, albums, monkeypatch):
    karma, live = _songs(session, "Karma (Full Album)", "Live in Opole")
    albums.candidates = [_candidate(karma, True, "Karma"), _candidate(live, False)]
    shown = []
    monkeypatch.setattr(questionary, "checkbox",
                        lambda message, choices: shown.append([c.checked for c in choices]) or _answer([0]))
    albums.answers = [True, True]  # change the titles, remember the unticked

    assert far.review_full_albums() == 1
    assert shown == [[True, False]]   # pre-ticked only where the title says so
    assert albums.kinds == [["Karma"]]
    assert albums.ignored == [live.id]


def test_titles_stay_when_the_change_is_declined(session, albums, monkeypatch):
    karma, = _songs(session, "Karma (Full Album)")
    albums.candidates = [_candidate(karma, True, "Karma")]
    monkeypatch.setattr(questionary, "checkbox", lambda message, choices: _answer([0]))
    albums.answers = [False]

    far.review_full_albums()
    assert karma.title == "Karma (Full Album)"


def test_ctrl_c_in_the_list_marks_nothing(session, albums, monkeypatch):
    karma, = _songs(session, "Karma (Full Album)")
    albums.candidates = [_candidate(karma, True, "Karma")]
    monkeypatch.setattr(questionary, "checkbox", lambda message, choices: _answer(None))

    assert far.review_full_albums() == 0
    assert albums.kinds == [] and albums.ignored == []


def test_no_candidates_asks_nothing(albums, capsys):
    assert far.review_full_albums() == 0
    assert "No songs look like full albums." in capsys.readouterr().out


# ==================== request packages ====================

@pytest.fixture
def package(tmp_path, monkeypatch):
    state = SimpleNamespace(imported=[], result=ImportResult(), resolved=[], answers=[])
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(rpi.Path, "home", classmethod(lambda cls: tmp_path))

    def fake_import(path):
        state.imported.append(path)
        if isinstance(state.result, Exception):
            raise state.result
        return state.result
    monkeypatch.setattr(rpi, "import_package", fake_import)
    monkeypatch.setattr(rpi, "resolve_new_requests", lambda: state.resolved.append(True))
    monkeypatch.setattr(rpi, "ask_yes_no", lambda q: state.answers.pop(0))
    state.downloads = downloads
    return state


def test_the_newest_zip_in_downloads_is_offered_and_resolved_on_yes(package, monkeypatch):
    import os
    old, new = package.downloads / "old.zip", package.downloads / "new.zip"
    for i, p in enumerate((old, new)):
        p.write_bytes(b"zip")
        os.utime(p, (1000 + i, 1000 + i))
    (package.downloads / "notes.txt").write_text("x")
    offered = []
    monkeypatch.setattr(questionary, "select",
                        lambda message, choices: offered.append([c.title for c in choices]) or _answer(new))
    package.result = ImportResult(added=["a.db"], already_there=["b.db"])
    package.answers = [True]

    rpi.import_request_package()
    assert offered == [["new.zip", "old.zip", rpi._TYPE_PATH, "Back"]]
    assert package.imported == [new] and package.resolved == [True]


def test_a_typed_path_that_isnt_a_file_imports_nothing(package, monkeypatch, capsys):
    monkeypatch.setattr(questionary, "path", lambda message, default="": _answer("~/nowhere.zip"))

    rpi.import_request_package()
    assert package.imported == []
    assert "isn't a file" in capsys.readouterr().out


@pytest.mark.parametrize("error", [PackageError("not a request package"), OSError("permission denied")])
def test_a_refused_package_says_why_and_resolves_nothing(package, monkeypatch, capsys, error):
    zip_path = package.downloads / "p.zip"
    zip_path.write_bytes(b"zip")
    monkeypatch.setattr(questionary, "select", lambda message, choices: _answer(zip_path))
    package.result = error

    rpi.import_request_package()
    assert f"Package not imported: {error}" in capsys.readouterr().out
    assert package.resolved == []


def test_nothing_new_in_the_package_doesnt_offer_resolving(package, monkeypatch):
    zip_path = package.downloads / "p.zip"
    zip_path.write_bytes(b"zip")
    monkeypatch.setattr(questionary, "select", lambda message, choices: _answer(zip_path))
    package.result = ImportResult(already_there=["a.db"])

    rpi.import_request_package()  # package.answers is empty: asking would fail
    assert package.resolved == []


def test_back_imports_nothing(package, monkeypatch):
    (package.downloads / "p.zip").write_bytes(b"zip")
    monkeypatch.setattr(questionary, "select", lambda message, choices: _answer(None))

    rpi.import_request_package()
    assert package.imported == []


# ==================== unsure years ====================

def test_unsure_years_start_ticked_and_ticked_ones_are_saved(monkeypatch, capsys):
    # UI-40: ticked used to mean "change" and unticked was saved - the opposite of every other list
    import menu.main_menu  # noqa: F401 - imported first, as main.py does
    import importlib
    # The package's fill_missing_years() function shadows the module of that name
    fmy = importlib.import_module(
        "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_years")
    songs = {name: SimpleNamespace(title=name) for name in ("a", "b", "c")}
    written = []
    monkeypatch.setattr(fmy, "edit_db_entry", lambda song, field, value: written.append((song.title, value)))
    monkeypatch.setattr(fmy, "copy_to_clipboard", lambda text: None)
    shown = []
    monkeypatch.setattr(questionary, "checkbox",
                        lambda message, choices: shown.append([c.checked for c in choices]) or _answer([0]))
    typed = iter(["20245", "1987"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))

    reviews = [("A", 2016, [songs["a"]], "u1"), ("B", 2020, [songs["b"]], "u2"), ("C", 2021, [songs["c"]], "u3")]
    touched = fmy._review_unsure_years(reviews)

    assert shown == [[True, True, True]]
    assert written == [("a", "2016"), ("c", "1987")]   # B: "20245" isn't a year, left without one
    assert [s.title for s in touched] == ["a", "c"]
    assert "'20245' is not a year between 1900" in capsys.readouterr().out


# ==================== Clean names: junk album ====================

@pytest.mark.parametrize("typed, album", [("", "Unknown Album"), ("-", ""), ("Ho!", "Ho!")])
def test_an_empty_answer_keeps_a_junk_album_and_a_dash_clears_it(monkeypatch, typed, album):
    # UI-33: an empty answer cleared the album, while for a title it meant "strip the brackets"
    import menu.main_menu  # noqa: F401 - imported first, as main.py does
    import importlib
    cn = importlib.import_module("menu.main_menu.enter_database.manage_database.clean_names")  # not the function
    song = SimpleNamespace(id=1, title="Teksański", album="Unknown Album", artist=SimpleNamespace(name="Hey"))
    monkeypatch.setattr(cn, "is_blacklisted_album", lambda value, *a: value == "Unknown Album")
    monkeypatch.setattr(cn, "add_tag_to_song", lambda song, tag: True)
    monkeypatch.setattr(cn, "copy_to_clipboard", lambda text: None)
    monkeypatch.setattr(cn, "ask_yes_no", lambda q: True)
    monkeypatch.setattr(cn, "edit_db_entry", lambda s, field, value: setattr(s, field, value))
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)

    cn.seek_nonsense_names([song])
    assert song.album == album


# ==================== Divide artists, picking a user ====================

def test_divide_artists_shows_square_brackets_in_titles(monkeypatch, capsys):
    # UI-30: rich's print took "[live]" for markup and dropped it
    import menu.main_menu  # noqa: F401 - imported first, as main.py does
    import importlib
    md = importlib.import_module("menu.main_menu.enter_database.manage_database.merge_divide_menu")
    artist = SimpleNamespace(id=5, name="Hey")
    monkeypatch.setattr(md, "ask_for_entries_list", lambda mode: [artist])
    monkeypatch.setattr(md, "pick_from_db_objects", lambda objects, question: artist)
    monkeypatch.setattr(md, "artist_primary_songs", lambda a: [
        SimpleNamespace(id=1, title="Teksański [live]", album="[sic]", year=None),
        SimpleNamespace(id=2, title="Cisza", album=None, year=1999)])
    monkeypatch.setattr(md, "ask_yes_no", lambda q: False)

    md.divide_artists_menu()
    out = capsys.readouterr().out
    assert "Teksański [live] (album: [sic])" in out


def test_picking_a_user_offers_back_in_both_menus(monkeypatch):
    # UI-55: Users had no "Back", Users -> Moods had one
    import menu.main_menu  # noqa: F401
    import importlib
    from utils.ui import user_picker
    users_menu = importlib.import_module("menu.main_menu.users")
    moods_menu = importlib.import_module("menu.main_menu.users.moods")
    monkeypatch.setattr(user_picker.users_db, "users_available", lambda: True)
    monkeypatch.setattr(user_picker.users_db, "list_usernames", lambda: ["ania", "bob"])
    offered = []
    answers = iter(["Back", "bob"])
    monkeypatch.setattr(questionary, "select",
                        lambda question, choices: offered.append(choices) or _answer(next(answers)))

    assert users_menu._pick_user("Which user?") is None
    assert moods_menu._pick_user("Whose moods?") == "bob"
    assert offered == [["ania", "bob", "Back"]] * 2
