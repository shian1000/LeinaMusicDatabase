"""Projects (songs.project_id, migration 0019; utils/common/project_names.py,
utils/database/projects.py, utils/ui/project_review.py): the series a recording
was made for. Reading a project out of real names, an entry matching only songs
of its own project, duplicates per project, the import's step and its switch, the
list of stored songs (the artist credited, a shared row never renamed), the
dictionary, and what a project changes for YouTube, file names and albums.
Temp-file SQLite."""

import importlib
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import menu.main_menu  # noqa: F401,E402 - menu.song_actions' circular import (AGENTS.md)
from config.constants import SONG_KIND_FULL_ALBUM  # noqa: E402
from settings import Settings  # noqa: E402
from utils.common import sanitize as s  # noqa: E402
from utils.common.project_names import (  # noqa: E402
    ProjectName,
    cut_project,
    read_project,
    search_title,
    title_with_project,
)
from utils.common.song_entry import SongEntry  # noqa: E402
from utils.database import projects as pr  # noqa: E402
from utils.database.albums import register_album_sync  # noqa: E402
from utils.database.datatables import Artist, Base, Project, Song  # noqa: E402
from utils.database.song_duplicates import (  # noqa: E402
    find_cross_artist_groups,
    find_duplicate_groups,
    merge_candidates,
    spelling_key,
)
from utils.database.song_kinds import songs_for_entry  # noqa: E402
from utils.database.song_references import register_song_reference_sync  # noqa: E402
from utils.discoveries.import_engine import (  # noqa: E402
    LocalSongMatcher,
    create_song_entry,
    find_existing_song,
    find_song_loosely,
)
from utils.discoveries.mp3_utils import song_file_name  # noqa: E402
from utils.ui import project_review  # noqa: E402
from utils.ui.song_colors import as_ansi, song_tokens  # noqa: E402
from utils.youtube import import_from_playlist as ifp  # noqa: E402
from utils.youtube.yt_cache import _song_entry  # noqa: E402

KAUNAS = ProjectName("Kaunas Acoustics")
TINY_DESK = ProjectName("Tiny Desk", ("NPR Tiny Desk Concert", "NPR Music Tiny Desk Concert", "Tiny Desk Concert"),
                        ("NPR Music",))
LIKE_A_VERSION = ProjectName("Like a Version", ("triple j Like a Version", "tripple j Like a Version"), ("triple j",))
DICTIONARY = [KAUNAS, TINY_DESK, LIKE_A_VERSION]


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    register_album_sync(factory)
    register_song_reference_sync(factory)
    music_session = factory()
    music_session.add_all(Project(name=p.name, aliases=",".join(p.aliases) or None, channels=",".join(p.channels) or None)
                          for p in DICTIONARY)
    music_session.commit()
    yield music_session
    music_session.close()


@pytest.fixture
def dictionary(monkeypatch):
    """What sanitize's step reads (it has no session)."""
    monkeypatch.setattr(pr, "known_projects", lambda: DICTIONARY)


def _project(session, name):
    return session.query(Project).filter_by(name=name).one()


def _song(session, artist, title, project=None, **fields):
    row = session.query(Artist).filter_by(name=artist).first() or Artist(name=artist)
    song = Song(title=title, artist=row, project=_project(session, project) if project else None, **fields)
    session.add(song)
    session.commit()
    return song


# ==================== Reading names ====================

@pytest.mark.parametrize("artist, title, album, expected", [
    # (project, artist, title, whole), the real rows that started this
    ("Kaunas Acoustics : Liūdni slibinai", "Pieno Fabrikėlis", None,
     ("Kaunas Acoustics", "Liūdni slibinai", "Pieno Fabrikėlis", False)),
    ("NPR Music", "yeule: Tiny Desk Concert", "yeule: Tiny Desk Concert",
     ("Tiny Desk", "yeule", "Tiny Desk Concert", True)),
    ("NPR Music", "Nduduzo Makhathini: Tiny Desk Concert", "Singles",
     ("Tiny Desk", "Nduduzo Makhathini", "Tiny Desk Concert", True)),
    ("Wolf Alice", "Boys (Charli XCX) Like a Version", "tripple j: Like a Version 14",
     ("Like a Version", "Wolf Alice", "Boys (Charli XCX)", False)),
    ("Wolf Alice", "Boys (Like a Version)", None, ("Like a Version", "Wolf Alice", "Boys", False)),
    ("Wolf Alice", "Boys [Live at Tiny Desk]", None, ("Tiny Desk", "Wolf Alice", "Boys", False)),
    ("Daughter", "Song (cover, Like a Version)", None, ("Like a Version", "Daughter", "Song (cover)", False)),
    # An album naming it is enough, and it stays the title's business
    ("Wolf Alice", "Boys", "tripple j: Like a Version 14", ("Like a Version", "Wolf Alice", "Boys", False)),
    # A real artist with only the project as a title: their whole set
    ("Daughter", "Tiny Desk Concert", None, ("Tiny Desk", "Daughter", "Tiny Desk Concert", True)),
    # A channel with "Artist - Title" left in the title
    ("NPR Music", "Daughter - Youth | Tiny Desk Concert", None, ("Tiny Desk", "Daughter", "Youth", False)),
])
def test_names_read_as_a_project(artist, title, album, expected):
    reading = read_project(artist, title, DICTIONARY, album=album)
    assert (reading.project, reading.artist, reading.title, reading.whole) == expected
    assert not reading.guessed


@pytest.mark.parametrize("artist, title", [
    ("Sting", "Englishman in New York"),
    ("Sting", "Desk"),
    ("Sting", "Tiny Deskmate"),  # only whole words
    ("Daughter", "Live Session"),  # general words aren't a project
])
def test_other_names_read_as_no_project(artist, title):
    assert read_project(artist, title, DICTIONARY) is None


def test_no_dictionary_reads_nothing():
    assert read_project("Wolf Alice", "Boys (Like a Version)", []) is None


def test_the_longest_spelling_wins_and_the_publisher_leaves_a_whole_sets_title():
    assert cut_project("NPR Music Tiny Desk Concert", DICTIONARY)[2] == "NPR Music Tiny Desk Concert"
    reading = read_project("NPR Music Tiny Desk Concert", "Daughter", DICTIONARY, guess=True)
    assert (reading.artist, reading.title) == ("Daughter", "Tiny Desk Concert")


def test_an_artist_that_is_only_the_project_is_guessed_only_when_asked():
    reading = read_project("NPR Music Tiny Desk Concert", "Daughter", DICTIONARY, guess=True)
    assert reading.guessed and reading.whole
    # The import has no one to ask: the names stay, the project is marked
    reading = read_project("Kaunas Acoustics", "Pieno Fabrikėlis", DICTIONARY)
    assert (reading.project, reading.artist, reading.title) == ("Kaunas Acoustics", "Kaunas Acoustics", "Pieno Fabrikėlis")
    assert reading.artist_unknown and not reading.guessed


def test_a_channel_with_nothing_else_leaves_the_artist_unknown():
    reading = read_project("NPR Music", "NPR Music Tiny Desk Concert", DICTIONARY)
    assert (reading.artist, reading.title) == ("NPR Music", "Tiny Desk Concert")
    assert reading.artist_unknown and reading.guessed


def test_only_a_channel_named_like_the_project_says_it_is_its_recording():
    assert read_project("Wolf Alice", "Boys", DICTIONARY, channel="Kaunas Acoustics").project == "Kaunas Acoustics"
    # NPR Music also publishes other things
    assert read_project("Wolf Alice", "Boys", DICTIONARY, channel="NPR Music") is None


def test_the_read_text_counts_when_the_cleanup_cut_the_project_off():
    reading = read_project("Daughter", "Song", DICTIONARY, raw_text="Daughter - Song | Kaunas Acoustics")
    assert (reading.project, reading.artist, reading.title) == ("Kaunas Acoustics", "Daughter", "Song")


# ==================== Matching ====================

@pytest.fixture
def versions(session):
    studio = _song(session, "Wolf Alice", "Boys")
    recording = _song(session, "Wolf Alice", "Boys", "Like a Version")
    return studio, recording


def test_songs_for_entry_takes_only_the_entrys_project(versions):
    studio, recording = versions
    assert songs_for_entry([studio, recording], "Boys") == [studio]
    assert songs_for_entry([studio, recording], "Boys", project="Like a Version") == [recording]
    assert songs_for_entry([studio, recording], "Boys", project="Tiny Desk") == []


@pytest.mark.parametrize("find", [find_existing_song, find_song_loosely])
def test_finding_a_song_keeps_the_versions_apart(versions, find):
    studio, recording = versions
    assert find("Boys", [studio, recording]) is studio
    assert find("Boys", [recording, studio], project="Like a Version") is recording
    assert find("Boys", [studio], project="Like a Version") is None
    assert find("Boys", [recording]) is None


def test_a_project_set_is_found_by_its_title_even_though_it_is_a_full_album(session):
    concert = _song(session, "yeule", "Tiny Desk Concert", "Tiny Desk", kind=SONG_KIND_FULL_ALBUM)
    assert find_existing_song("Tiny Desk Concert", [concert], project="Tiny Desk") is concert


def test_local_matcher_keeps_the_versions_apart(session, versions):
    studio, recording = versions
    matcher = LocalSongMatcher(session)
    assert matcher.match(SongEntry({"artist_name": "Wolf Alice", "title": "Boys"})) is studio
    assert matcher.match(SongEntry({"artist_name": "Wolf Alice", "title": "Boys", "project": "Like a Version"})) is recording
    assert matcher.match(SongEntry({"artist_name": "Wolf Alice", "title": "Boys", "project": "Tiny Desk"})) is None


def test_the_closest_songs_offered_are_of_the_entrys_project_too(session, versions):
    studio, recording = versions
    matcher = LocalSongMatcher(session)
    plain = SongEntry({"artist_name": "Wolf Alice", "title": "Boys"})
    assert [song for song, _ in matcher.best_guesses(plain, 3, 0.6)] == [studio]
    marked = SongEntry({"artist_name": "Wolf Alice", "title": "Boys", "project": "Like a Version"})
    assert [song for song, _ in matcher.best_guesses(marked, 3, 0.6)] == [recording]


def test_an_entry_without_an_artist_matches_within_its_project(session, versions):
    _, recording = versions
    matcher = LocalSongMatcher(session)
    assert matcher.match_similar(SongEntry({"title": "Boys", "project": "Like a Version"})) is recording


# ==================== Duplicates ====================

def test_a_recording_is_no_duplicate_of_the_studio_song(session, versions):
    assert find_duplicate_groups(session) == []
    assert find_duplicate_groups(session, spelling_key) == []
    studio, recording = versions
    assert merge_candidates(session, studio) == []
    assert merge_candidates(session, recording) == []


def test_two_rows_of_one_project_are_duplicates(session):
    first = _song(session, "Wolf Alice", "Boys", "Like a Version")
    second = _song(session, "Wolf Alice", "boys", "Like a Version")
    assert {s.id for group in find_duplicate_groups(session) for s in group} == {first.id, second.id}


def test_the_same_title_of_different_artists_in_different_projects_isnt_one_song(session):
    _song(session, "A", "Boys", "Like a Version", youtube_video_id="same")
    _song(session, "B", "Boys", youtube_video_id="same")
    assert find_cross_artist_groups(session) == []


# ==================== Reading an import's entry ====================

@pytest.mark.parametrize("video, expected", [
    ({"title": "Wolf Alice - Boys (Charli XCX cover) | Like A Version", "channel": "triple j"},
     ("Like a Version", "Wolf Alice", "Boys (Charli XCX cover)", None)),
    ({"title": "yeule: Tiny Desk Concert", "channel": "NPR Music"}, ("Tiny Desk", "yeule", "Tiny Desk Concert", True)),
    ({"title": "Liūdni slibinai - Pieno Fabrikėlis | Kaunas Acoustics", "channel": "Kaunas Acoustics"},
     ("Kaunas Acoustics", "Liūdni slibinai", "Pieno Fabrikėlis", None)),
    ({"title": "Sting - Englishman In New York (Official Video)", "channel": "Sting"},
     (None, "Sting", "Englishman In New York", None)),
])
def test_a_youtube_video_is_read_with_its_project(dictionary, video, expected):
    entry = ifp.build_metadata_from_item({"video_id": "v", **video})
    assert (entry.project, entry.artist, entry.title, entry.project_whole) == expected


def test_an_mp3s_tags_are_read_with_their_project_and_the_cut_is_kept(dictionary):
    entry = s.sanitize_metadata(SongEntry({"artist_name": "Wolf Alice", "title": "Boys (Like a Version)"}), s.SOURCE_MP3)
    assert (entry.project, entry.artist, entry.title) == ("Like a Version", "Wolf Alice", "Boys")
    assert any(piece.step == s.PROJECT_NAME for piece in entry.removed)


def test_cleaning_an_entry_again_keeps_what_was_read(dictionary):
    entry = s.sanitize_metadata(SongEntry({"artist_name": "NPR Music", "title": "yeule: Tiny Desk Concert"}), s.SOURCE_MP3)
    again = s.sanitize_metadata(entry, s.SOURCE_MP3)
    assert (again.project, again.artist, again.title, again.project_whole) == ("Tiny Desk", "yeule", "Tiny Desk Concert", True)


def test_the_junk_cleanup_cant_take_the_project_along(dictionary):
    entry = s.sanitize_metadata(SongEntry({"artist_name": "Wolf Alice", "title": "Boys (Live at Tiny Desk) (Official Video)"}),
                                s.SOURCE_MP3)
    assert (entry.project, entry.title) == ("Tiny Desk", "Boys")


def test_the_switch_turns_it_off(dictionary, monkeypatch):
    monkeypatch.setattr(s.settings, "load_overrides", lambda: {"projects": False})
    entry = s.sanitize_metadata(SongEntry({"artist_name": "Wolf Alice", "title": "Boys (Like a Version)"}), s.SOURCE_MP3)
    assert entry.project is None and entry.title == "Boys (Like a Version)"


def test_a_stored_name_is_never_changed_by_the_step(dictionary):
    assert s.sanitize_field("Boys (Like a Version)", s.TITLE, s.SOURCE_STORED, artist="Wolf Alice") == "Boys (Like a Version)"
    assert s.PROJECT_NAME not in [name for name, _, _ in
                                  s.sanitize_changes("Boys (Like a Version)", s.TITLE, s.SOURCE_STORED, artist="Wolf Alice")]


def test_a_title_cleaned_with_nowhere_to_keep_the_project_keeps_it(dictionary):
    assert s.sanitize_field("Boys (Like a Version)", s.TITLE, s.SOURCE_MP3, artist="Wolf Alice") == "Boys (Like a Version)"


# ==================== Adding a song ====================

def test_a_new_song_gets_its_project(session):
    artist = Artist(name="Wolf Alice")
    session.add(artist)
    session.flush()
    song = create_song_entry(SongEntry({"title": "Boys", "project": "Like a Version"}), artist, session)
    assert song.project.name == "Like a Version" and song.kind is None


def test_a_new_whole_set_is_a_full_album_filed_under_no_album(session):
    artist = Artist(name="yeule")
    session.add(artist)
    session.flush()
    song = create_song_entry(SongEntry({"title": "Tiny Desk Concert", "project": "Tiny Desk", "project_whole": True}),
                             artist, session)
    session.flush()
    assert song.kind == SONG_KIND_FULL_ALBUM and not song.album and song.album_id is None


# ==================== Stored songs ====================

def _proposal(session, song):
    return next(p for p in pr.project_proposals(session) if p.song is song)


def test_proposals_list_only_songs_naming_a_project(session, versions):
    named = _song(session, "Daughter", "Youth (Tiny Desk Concert)")
    assert [p.song for p in pr.project_proposals(session)] == [named]


def test_a_stored_song_keeps_its_album_when_it_is_a_real_one(session):
    song = _song(session, "Wolf Alice", "Boys (Charli XCX) Like a Version", album="tripple j: Like a Version 14")
    pr.apply_project_proposal(session, _proposal(session, song))
    session.commit()
    assert (song.project.name, song.title, song.album) == ("Like a Version", "Boys (Charli XCX)", "tripple j: Like a Version 14")
    assert song.artist.name == "Wolf Alice"


def test_a_whole_set_loses_an_album_that_only_named_the_project(session):
    song = _song(session, "NPR Music", "yeule: Tiny Desk Concert", album="yeule: Tiny Desk Concert")
    pr.apply_project_proposal(session, _proposal(session, song))
    session.commit()
    assert (song.artist.name, song.title, song.kind, song.album) == ("yeule", "Tiny Desk Concert", SONG_KIND_FULL_ALBUM, None)


def test_a_song_is_credited_to_the_artist_never_renaming_the_shared_row(session):
    moved = _song(session, "NPR Music", "yeule: Tiny Desk Concert")
    stays = _song(session, "NPR Music", "NPR Music Tiny Desk Concert")
    existing = _song(session, "yeule", "Softcars").artist
    pr.apply_project_proposal(session, _proposal(session, moved))
    session.commit()
    assert moved.artist is existing
    assert stays.artist.name == "NPR Music"


def test_an_artist_left_without_songs_goes(session):
    song = _song(session, "Kaunas Acoustics : Liūdni slibinai", "Pieno Fabrikėlis")
    pr.apply_project_proposal(session, _proposal(session, song))
    session.commit()
    assert session.query(Artist).filter_by(name="Kaunas Acoustics : Liūdni slibinai").first() is None
    assert song.artist.name == "Liūdni slibinai"


def _answer_checkbox(monkeypatch):
    shown = []

    def checkbox(_message, choices):
        shown.extend(choices)
        return SimpleNamespace(ask=lambda: [i for i, c in enumerate(choices) if c.checked])

    monkeypatch.setattr(project_review.questionary, "checkbox", checkbox)
    monkeypatch.setattr(project_review, "require_global_database_sessions", lambda: (None, None))
    monkeypatch.setattr(project_review, "submit_global_database_session", lambda: None)
    return shown


def test_the_review_ticks_what_the_names_settle_and_leaves_guesses(session, monkeypatch):
    sure = _song(session, "NPR Music", "yeule: Tiny Desk Concert")
    guess = _song(session, "NPR Music Tiny Desk Concert", "Daughter")
    shown = _answer_checkbox(monkeypatch)
    monkeypatch.setattr(project_review, "require_global_database_sessions", lambda: (session, None))
    project_review.review_project_proposals()
    assert sorted(c.checked for c in shown) == [False, True]
    assert sure.project.name == "Tiny Desk"
    assert guess.project is None and guess.artist.name == "NPR Music Tiny Desk Concert"


# ==================== The dictionary ====================

def test_known_projects_reads_the_dictionary_once(tmp_path, monkeypatch):
    connection = sqlite3.connect(tmp_path / "music.db")
    connection.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, aliases TEXT, channels TEXT)")
    connection.execute("INSERT INTO projects VALUES (1, 'Tiny Desk', 'Tiny Desk Concert, NPR Tiny Desk', 'NPR Music')")
    connection.commit()
    monkeypatch.setattr(Settings, "database_dir", tmp_path)
    pr.forget_known_projects()
    assert pr.known_projects() == [ProjectName("Tiny Desk", ("Tiny Desk Concert", "NPR Tiny Desk"), ("NPR Music",))]

    connection.execute("INSERT INTO projects VALUES (2, 'KEXP', NULL, NULL)")
    connection.commit()
    connection.close()
    assert len(pr.known_projects()) == 1
    pr.forget_known_projects()
    assert [p.name for p in pr.known_projects()] == ["Tiny Desk", "KEXP"]
    pr.forget_known_projects()


@pytest.mark.parametrize("make", [
    lambda path: None,  # no music.db
    lambda path: sqlite3.connect(path).execute("CREATE TABLE songs (id INTEGER)").connection.close(),  # not migrated
])
def test_a_database_without_the_dictionary_reads_no_projects(tmp_path, monkeypatch, make):
    make(tmp_path / "music.db")
    monkeypatch.setattr(Settings, "database_dir", tmp_path)
    pr.forget_known_projects()
    assert pr.known_projects() == []
    pr.forget_known_projects()


def test_a_project_cant_take_anothers_spelling(session):
    with pytest.raises(ValueError):
        pr.add_project(session, "tiny desk concert")
    with pytest.raises(ValueError):
        pr.add_project(session, " ... ")
    assert pr.add_project(session, "KEXP").id is not None
    assert pr.project_named(session, "NPR Tiny Desk Concert").name == "Tiny Desk"
    assert pr.project_named(session, "Session") is None


def test_removing_a_project_keeps_its_songs(session, versions):
    _, recording = versions
    assert pr.project_song_counts(session) == {_project(session, "Like a Version").id: 1}
    assert pr.delete_project(session, _project(session, "Like a Version")) == 1
    session.commit()
    assert recording.project_id is None and recording.title == "Boys"


def test_migration_matches_the_model_and_fills_the_dictionary(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY, title TEXT)")
    conn.executescript((repo_root / "migrations" / "music" / "0019_song_projects.sql").read_text())
    migrated = {row[1]: (bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(projects)")}
    assert "project_id" in {row[1] for row in conn.execute("PRAGMA table_info(songs)")}
    assert {row[0] for row in conn.execute("SELECT name FROM projects")} == {"Kaunas Acoustics", "Tiny Desk", "Like a Version"}

    engine = create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    Base.metadata.create_all(engine)
    modelled = {c["name"]: (not c["nullable"], bool(c["primary_key"])) for c in inspect(engine).get_columns("projects")}
    assert migrated == modelled


# ==================== Shown, searched and written ====================

def test_a_song_is_shown_project_first():
    tokens = song_tokens("Wolf Alice", "Boys", None, "Like a Version")
    assert "".join(text for _, text in tokens) == "Like a Version: Wolf Alice - Boys"
    assert "\033[95mLike a Version\033[0m" in as_ansi(tokens)
    assert "".join(text for _, text in song_tokens("Wolf Alice", "Boys")) == "Wolf Alice - Boys"


def test_the_youtube_search_names_the_project(session, versions):
    studio, recording = versions
    assert search_title(studio) == "Boys"
    assert search_title(recording) == "Boys Like a Version"
    assert _song_entry(recording)["search_title"] == "Boys Like a Version"
    assert _song_entry(recording)["title"] == "Boys"
    assert _song_entry(studio)["search_title"] is None


def test_a_whole_set_isnt_searched_for_as_a_full_album(session):
    concert = _song(session, "yeule", "Tiny Desk Concert", "Tiny Desk", kind=SONG_KIND_FULL_ALBUM)
    album = _song(session, "Bonobo", "Migration", kind=SONG_KIND_FULL_ALBUM)
    assert _song_entry(concert)["full_album"] is False
    assert _song_entry(album)["full_album"] is True
    assert search_title(concert) == "Tiny Desk Concert"


def test_a_file_name_reads_back_into_the_same_song(session, dictionary, versions):
    studio, recording = versions
    assert song_file_name(studio) == "Wolf Alice - Boys.mp3"
    assert song_file_name(recording) == "Wolf Alice - Boys (Like a Version).mp3"
    entry = s.sanitize_metadata(SongEntry({"artist_name": "Wolf Alice", "title": title_with_project(recording)}), s.SOURCE_MP3)
    assert LocalSongMatcher(session).match(entry) is recording


def test_fill_missing_albums_leaves_a_projects_songs(session, monkeypatch, capsys):
    albums = importlib.import_module(
        "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums")
    monkeypatch.setattr(albums, "open_global_driver", lambda: pytest.fail("no lookup for a project's song"))
    albums.fill_missing_albums([_song(session, "Wolf Alice", "Boys", "Like a Version")])
    assert "No songs with missing albums" in capsys.readouterr().out
