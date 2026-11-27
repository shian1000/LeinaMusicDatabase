"""Review screens no test reached (UI-12), prompts answered by the test:
Fill missing data -> Genres (utils/ui/genre_review.py: the lookup's
outcomes, each module skipping the songs it had nothing for, the list of
unsure genres, typing the rest, Set genre), Find full albums (utils/ui/full_album_review.py) and
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
    state = SimpleNamespace(misses={}, saved_misses=None, results={}, max_songs=100, asked=[])
    monkeypatch.setattr(gr, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(gr, "submit_global_database_session", session.commit)
    monkeypatch.setattr(gr, "load_misses", lambda: {m: set(ids) for m, ids in state.misses.items()})
    monkeypatch.setattr(gr, "save_misses", lambda misses: setattr(state, "saved_misses", misses))
    monkeypatch.setattr(gr, "load_max_songs_per_fetch", lambda: state.max_songs)
    monkeypatch.setattr(gr, "load_discovery_modules",
                        lambda kind: [("mb", "MusicBrainz", None), ("wiki", "Wikipedia", None)])

    def discover(song, modules):
        state.asked.append((song.title, [m[0] for m in modules]))
        found = state.results.get(song.title)
        if found is None:
            return SimpleNamespace(match=None, missed=[m[0] for m in modules], failed=[])
        genres, level, module, sure = (*found, "mb", True)[:4] if len(found) == 2 else found
        sure = sure and level != GENRE_LEVEL_ARTIST
        result = SimpleNamespace(level=level, never_sure=module != "mb")
        return SimpleNamespace(match=SimpleNamespace(genres=genres, sure=sure, module_id=module, result=result),
                               missed=[], failed=[])
    monkeypatch.setattr(gr, "discover_genre", discover)
    return state


def test_genres_found_for_the_song_or_album_are_saved_at_once(session, genres, monkeypatch):
    a, b, c = _songs(session, "Teksański", "Cisza", "Nowy")
    genres.results = {"Teksański": ("rock, Alternative Rock", GENRE_LEVEL_SONG),
                      "Cisza": ("rock", GENRE_LEVEL_ALBUM)}
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)  # don't type the rest
    monkeypatch.setattr(questionary, "checkbox", lambda message, choices: _answer(None))  # the artist's genres

    gr.fill_missing_genres()
    assert (a.genre, b.genre, c.genre) == ("rock, alternative rock", "rock", None)
    # nothing in either module: neither asks about it next time
    assert genres.saved_misses == {"mb": {c.id}, "wiki": {c.id}}


def test_each_module_skips_the_songs_it_had_nothing_for(session, genres, monkeypatch, capsys):
    a, b, c = _songs(session, "Teksański", "Cisza", "Nowy")
    genres.misses = {"mb": {b.id, c.id}, "wiki": {c.id}}
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)

    gr.fill_missing_genres()
    # Nowy: every module had nothing; Cisza: only Wikipedia is asked
    assert genres.asked == [("Cisza", ["wiki"]), ("Teksański", ["mb", "wiki"])]
    assert "1 of them had nothing in any genre module before" in capsys.readouterr().out


def test_a_module_failing_three_times_in_a_row_is_dropped_for_the_run(session, genres, monkeypatch):
    _songs(session, "A", "B", "C", "D", "E")
    asked = []

    def discover(song, modules):
        asked.append([m[0] for m in modules])
        return SimpleNamespace(match=None, missed=["mb"], failed=["wiki"])
    monkeypatch.setattr(gr, "discover_genre", discover)
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)

    gr.fill_missing_genres()
    assert asked == [["mb", "wiki"]] * 3 + [["mb"]] * 2
    assert "wiki" not in genres.saved_misses  # a failure is no miss: asked again next run


def test_the_old_misses_file_was_musicbrainzs(tmp_path, monkeypatch):
    path = tmp_path / "misses.json"
    monkeypatch.setattr(gr, "GENRE_LOOKUP_MISSES_FILE", path)
    assert gr.load_misses() == {}
    path.write_text("[3, 1]")
    assert gr.load_misses() == {"music_brainz_fetcher": {1, 3}}
    gr.save_misses({"wiki": {2}, "mb": set()})
    assert gr.load_misses() == {"wiki": {2}}


def test_no_genre_modules_on(session, genres, monkeypatch, capsys):
    monkeypatch.setattr(gr, "load_discovery_modules", lambda kind: [])
    gr.fill_missing_genres()
    assert "No genre modules are on" in capsys.readouterr().out


def test_only_max_songs_per_fetch_are_looked_up(session, genres, monkeypatch):
    _songs(session, "A", "B", "C")
    genres.max_songs = 2
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)

    gr.fill_missing_genres()
    assert [title for title, _ in genres.asked] == ["A", "B"]


def test_unsure_genres_ticked_only_for_musicbrainzs_artist_level(session, genres, monkeypatch):
    a, b, c = _songs(session, "Teksański", "Cisza", "Nowy")
    genres.results = {"Teksański": ("rock", GENRE_LEVEL_ARTIST), "Cisza": ("rock", GENRE_LEVEL_ARTIST),
                      "Nowy": ("pop", GENRE_LEVEL_SONG, "wiki", False)}
    lists = []
    monkeypatch.setattr(questionary, "checkbox",
                        lambda message, choices: lists.append([c.checked for c in choices]) or _answer([1, 2]))
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)

    gr.fill_missing_genres()
    # by artist, then title: Cisza, Nowy, Teksański; Wikipedia's not ticked
    assert lists == [[True, False, True]]
    assert (a.genre, b.genre, c.genre) == ("rock", None, "pop")
    assert genres.saved_misses["mb"] == {b.id}  # unticked: MusicBrainz's answer turned down


def test_songs_no_module_knows_get_their_artists_genres_from_the_database(session, genres, monkeypatch):
    a, b, c, d = _songs(session, "A", "B", "C", "Nowy")
    for song in (a, b, c):
        song.genre = "rock"
    session.commit()
    genres.misses = {"mb": {d.id}, "wiki": {d.id}}  # every module had nothing
    lists = []
    monkeypatch.setattr(questionary, "checkbox",
                        lambda message, choices: lists.append([(c.title, c.checked) for c in choices]) or _answer([]))
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)

    gr.fill_missing_genres()
    assert len(lists) == 1 and lists[0][0][1] is True  # 3/3 songs: ticked
    assert "the artist: 3/3 songs" in str(lists[0][0][0])
    assert d.genre is None and genres.saved_misses["artist_songs"] == {d.id}  # unticked: not offered again


def test_the_artists_genres_saved_when_ticked(session, genres, monkeypatch):
    a, b, d = _songs(session, "A", "B", "Nowy")
    a.genre, b.genre = "rock, punk", "rock"
    session.commit()
    genres.misses = {"mb": {d.id}, "wiki": {d.id}}
    monkeypatch.setattr(questionary, "checkbox", lambda message, choices: _answer([0]))
    monkeypatch.setattr(gr, "ask_yes_no", lambda q: False)

    gr.fill_missing_genres()
    assert d.genre == "rock, punk"


def test_a_cancelled_list_remembers_nothing(session, genres, monkeypatch):
    a, = _songs(session, "Teksański")
    genres.results = {"Teksański": ("pop", GENRE_LEVEL_SONG, "wiki", False)}
    monkeypatch.setattr(questionary, "checkbox", lambda message, choices: _answer(None))

    gr.fill_missing_genres()
    assert a.genre is None and "wiki" not in genres.saved_misses


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
                           title_says_so=title_says_so, cleaned_title=cleaned or song.title,
                           file_duration_s=None, video_duration_s=None)


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
    monkeypatch.setattr(far, "ask_yes_no", lambda q, **k: state.answers.pop(0))
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
    monkeypatch.setattr(rpi, "resolve_new_requests", lambda after_import=None: state.resolved.append(True))
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
    songs = {name: SimpleNamespace(title=name, project_id=None) for name in ("a", "b", "c")}
    written = []
    monkeypatch.setattr(fmy, "edit_db_entry", lambda song, field, value: written.append((song.title, value)))
    monkeypatch.setattr("utils.common.text_utils.copy_to_clipboard", lambda text: None)
    shown = []
    monkeypatch.setattr(questionary, "checkbox",
                        lambda message, choices: shown.append([c.checked for c in choices]) or _answer([0]))
    typed = iter(["20245", "1987"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))

    reviews = [("A", 2016, [songs["a"]], "u1", None), ("B", 2020, [songs["b"]], "u2", None),
               ("C", 2021, [songs["c"]], "u3", None)]
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
    monkeypatch.setattr(md, "ask_yes_no", lambda q, **k: False)

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


def test_album_review_lists_read_artist_then_title(monkeypatch):
    # Every other list reads "Artist - Title"; the Albums lists had the two swapped (2026-11-24)
    from utils.ui import album_guess_review as agr
    song = SimpleNamespace(title="The Last Time", artist=SimpleNamespace(name="The Rolling Stones"))
    labels = []
    monkeypatch.setattr(agr, "review_header", lambda *notes: None)
    monkeypatch.setattr(agr, "tick_items", lambda question, items, label: labels.extend(label(i) for i in items) or [])
    monkeypatch.setattr(agr, "say", lambda text: None)
    agr.tick_album_pairs([(song, SimpleNamespace(album="The Last Time"))], ["note"], "Which?", "None.")
    text = "".join(part for _, part in labels[0])
    assert text.startswith("The Rolling Stones - The Last Time")
