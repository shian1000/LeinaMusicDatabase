"""Users -> Enter user database (utils/discoveries/folder_playlist.py,
utils/database/user_playlists.py) and managing users (users.py rename /
change password / delete): a folder becomes a playlist in alphabetical path
order, matched to existing songs by import_engine.LocalSongMatcher (also
through bracket/junk annotations and "A ft. B" artist names), unmatched files
reported, the playlist saved to the user's own user.db, and read back for
"Show playlists" (playlist_songs()). In-memory music
DB, temp user folders."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import song_references, users
from utils.database.database_sessions import set_global_database_sessions
from utils.database.datatables import Artist, Base, Song
from utils.database.user_playlists import list_playlists, save_playlist
from utils.discoveries import folder_playlist as fp


@pytest.fixture(autouse=True)
def file_matching_config(tmp_path, monkeypatch):
    """Never the user's own file_matching_config.json: the default applies."""
    from utils.discoveries import file_matching_settings
    monkeypatch.setattr(file_matching_settings, "FILE_MATCHING_CONFIG_FILE", str(tmp_path / "file_matching_config.json"))


@pytest.fixture(autouse=True)
def quiet_review_screen(monkeypatch):
    """The folder review clears the screen per file and may wait for a key:
    neither in tests (the clear would hit the real terminal, the key wait
    would block on stdin)."""
    import importlib
    menu = importlib.import_module("menu.main_menu.users")
    monkeypatch.setattr(menu, "clear_screen", lambda: None)
    monkeypatch.setattr(menu.questionary, "press_any_key_to_continue", lambda *a, **k: _Answer(None))


@pytest.fixture
def music_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    hey, afina = Artist(name="Hey"), Artist(name="Afina")
    session.add_all([
        Song(title="Karma", artist=hey, youtube_video_id="vid1"),
        Song(title="Teksański", artist=hey),
        Song(title="Pay Your Respect", artist=afina),
    ])
    session.commit()
    set_global_database_sessions((session, None))
    yield session
    set_global_database_sessions(None)
    session.close()


@pytest.fixture
def users_env(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", tmp_path / "Users")
    monkeypatch.setattr(users, "DELETED_USERS_DIR", tmp_path / "archive" / "deleted-users")
    monkeypatch.setattr(users, "users_available", lambda: True)
    monkeypatch.setattr(song_references, "latest_change_id", lambda: 0)
    return tmp_path / "Users"


def _folder(tmp_path, tags: dict) -> Path:
    """mp3 files (empty - their tags come from the mocked read_file_metadata)."""
    folder = tmp_path / "music"
    for relative in tags:
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_bytes(b"")
    return folder


def test_folder_becomes_playlist_in_alphabetical_order(tmp_path, music_session, monkeypatch):
    tags = {
        "b/Hey - Teksanski.mp3": ("Hey", "Teksański"),
        "a/z.mp3": ("Hey", "Karma (Official Video)"),
        "A/y.mp3": ("Afina ft. CJ Pitts", "Pay Your Respect (Official Lyric Video)"),
        "c.mp3": ("Nobody", "Unknown song"),
    }
    folder = _folder(tmp_path, tags)
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(
        zip(("artist_name", "title"), tags[str(f.relative_to(folder))])))

    playlist = fp.build_folder_playlist(music_session, folder)

    assert [str(t.file.relative_to(folder)) for t in playlist.tracks] == [
        "A/y.mp3", "a/z.mp3", "b/Hey - Teksanski.mp3", "c.mp3"]
    assert [(t.title, t.artist) for t in playlist.tracks] == [
        ("Pay Your Respect", "Afina"), ("Karma", "Hey"), ("Teksański", "Hey"), ("Unknown song", "Nobody")]
    assert playlist.tracks[1].youtube_video_id == "vid1"
    assert [t.file.name for t in playlist.unmatched] == ["c.mp3"]
    assert playlist.unmatched[0].song_id is None


def test_saved_playlist_keeps_order_and_fallback_text(users_env, tmp_path, music_session, monkeypatch):
    users.create_user("ania", "secret1")
    tags = {"1.mp3": ("Hey", "Teksański"), "2.mp3": ("Hey", "Karma")}
    folder = _folder(tmp_path, tags)
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(
        zip(("artist_name", "title"), tags[f.name])))
    playlist = fp.build_folder_playlist(music_session, folder)

    playlist_id = save_playlist("ania", "Mix", playlist.tracks, source="folder", source_id=str(folder))
    assert list_playlists("ania") == [(playlist_id, "Mix", 2, 0, False)]

    import sqlite3
    rows = sqlite3.connect(users_env / "ania" / "user.db").execute(
        "SELECT position, song_id, title, artist FROM playlist_tracks ORDER BY position").fetchall()
    assert [r[2] for r in rows] == ["Teksański", "Karma"]
    assert all(r[1] is not None for r in rows)


def test_rename_change_password_and_delete(users_env, tmp_path):
    users.create_user("ania", "secret1")

    users.rename_user("ania", "Anna")
    assert users.list_usernames() == ["anna"]
    assert users.read_account("anna")["username"] == "anna"
    assert users.verify_user("anna", "secret1")
    with pytest.raises(users.InvalidUserError):
        users.rename_user("nobody", "x1")

    users.change_password("anna", "better2")
    assert users.verify_user("anna", "better2") and not users.verify_user("anna", "secret1")
    with pytest.raises(users.InvalidUserError):
        users.change_password("anna", "123")

    archived = users.delete_user("anna")
    assert users.list_usernames() == []
    assert (archived / "user.db").exists() and archived.parent == tmp_path / "archive" / "deleted-users"


def test_playlist_songs_keeps_order_repeats_and_lists_missing(users_env, music_session):
    from types import SimpleNamespace
    from utils.database.user_playlists import playlist_songs

    users.create_user("ania", "secret1")
    karma, teksanski = (music_session.query(Song).filter(Song.title == t).one() for t in ("Karma", "Teksański"))
    track = lambda song_id, title: SimpleNamespace(song_id=song_id, title=title, artist="Hey",
                                                   youtube_video_id=None, duration_s=None)
    playlist_id = save_playlist("ania", "Mix", [
        track(teksanski.id, "Teksański"), track(None, "Never matched"), track(karma.id, "Karma"),
        track(9999, "Deleted since"), track(teksanski.id, "Teksański"),
    ])

    songs, missing = playlist_songs(music_session, "ania", playlist_id)
    assert [s.title for s in songs] == ["Teksański", "Karma", "Teksański"]
    assert [(t.position, t.title) for t in missing] == [(1, "Never matched"), (3, "Deleted since")]


def test_find_songs_by_title_across_artists(music_session):
    from utils.discoveries.import_engine import find_songs_by_title

    assert [s.title for s in find_songs_by_title(music_session, "karma (official video)")] == ["Karma"]
    # Contained either way, from 4 characters
    assert [s.title for s in find_songs_by_title(music_session, "Pay Your Respect - live")] == ["Pay Your Respect"]
    assert find_songs_by_title(music_session, "Ka") == []
    assert find_songs_by_title(music_session, "") == []


def _id3_file(path: Path, **tags) -> Path:
    from mutagen.easyid3 import EasyID3
    path.write_bytes(b"")
    if tags:
        easy = EasyID3()
        for key, value in tags.items():
            easy[key] = value
        easy.save(path)
    return path


def test_write_song_tags_copies_database_data(tmp_path, music_session):
    from mutagen.easyid3 import EasyID3
    from utils.discoveries.mp3_utils import write_song_tags

    karma = music_session.query(Song).filter(Song.title == "Karma").one()
    karma.album, karma.year = "Karma", 2003
    bare = _id3_file(tmp_path / "bare.mp3")  # no ID3 header at all
    assert write_song_tags(bare, karma)
    assert {k: EasyID3(bare)[k] for k in ("title", "artist", "album", "date")} == {
        "title": ["Karma"], "artist": ["Hey"], "album": ["Karma"], "date": ["2003"]}
    assert not write_song_tags(bare, karma)  # nothing differs any more

    single = music_session.query(Song).filter(Song.title == "Teksański").one()
    single.album = "Singles"
    tagged = _id3_file(tmp_path / "single.mp3", title="teksanski", album="Real Album")
    assert write_song_tags(tagged, single)
    assert EasyID3(tagged)["title"] == ["Teksański"]
    assert EasyID3(tagged)["album"] == ["Real Album"]  # the "Singles" sentinel isn't written


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


def test_unmatched_file_picked_by_title_then_saved_and_tagged(users_env, tmp_path, music_session, monkeypatch):
    from mutagen.easyid3 import EasyID3
    menu = __import__("importlib").import_module("menu.main_menu.users")

    users.create_user("ania", "secret1")
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "1.mp3", artist="Hey", title="Karma")
    _id3_file(folder / "Smuglanka.mp3")  # no tags, no separator in the name
    tags = {"1.mp3": {"artist_name": "Hey", "title": "Karma"},
            "Smuglanka.mp3": {"artist_name": None, "title": None}}
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(tags[f.name]))

    teksanski = music_session.query(Song).filter(Song.title == "Teksański").one()
    selects = iter([menu._UNMATCHED_BY_TITLE, teksanski, menu._CONFIRM_SONG])
    monkeypatch.setattr(menu, "open_file_browser_terminal", lambda start: folder)
    monkeypatch.setattr(menu, "open_and_set_global_database_sessions", lambda: (music_session, None))
    monkeypatch.setattr(menu.questionary, "text", lambda message, default="": _Answer(
        "Mix" if message.startswith("Playlist") else "Teksański"))
    monkeypatch.setattr(menu.questionary, "select", lambda *a, **k: _Answer(next(selects)))
    monkeypatch.setattr(menu.questionary, "confirm", lambda *a, **k: _Answer(True))

    menu._create_playlist_from_folder("ania")

    ((playlist_id, name, count, missing, _),) = list_playlists("ania")
    assert (name, count, missing) == ("Mix", 2, 0)
    from utils.database.user_playlists import playlist_songs
    songs, _ = playlist_songs(music_session, "ania", playlist_id)
    assert [s.title for s in songs] == ["Karma", "Teksański"]
    # The file picked by hand was renamed and re-tagged ("Fix the names and tags")
    assert not (folder / "Smuglanka.mp3").exists()
    assert EasyID3(folder / "Hey - Teksański.mp3")["title"] == ["Teksański"]
    assert EasyID3(folder / "Hey - Teksański.mp3")["artist"] == ["Hey"]


def test_playlist_stopped_with_a_file_undecided_is_not_saved(users_env, tmp_path, music_session, monkeypatch):
    menu = __import__("importlib").import_module("menu.main_menu.users")
    users.create_user("ania", "secret1")
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "x.mp3")
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: {"artist_name": None, "title": None})
    monkeypatch.setattr(menu, "open_file_browser_terminal", lambda start: folder)
    monkeypatch.setattr(menu, "open_and_set_global_database_sessions", lambda: (music_session, None))
    monkeypatch.setattr(menu.questionary, "text", lambda message, default="": _Answer("Mix"))
    monkeypatch.setattr(menu.questionary, "select", lambda *a, **k: _Answer(menu._UNMATCHED_STOP))

    menu._create_playlist_from_folder("ania")
    assert list_playlists("ania") == []


def test_title_pick_can_be_rejected_for_a_manual_search(users_env, tmp_path, music_session, monkeypatch):
    menu = __import__("importlib").import_module("menu.main_menu.users")
    users.create_user("ania", "secret1")
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "x.mp3")
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: {"artist_name": None, "title": "Karma"})

    karma = music_session.query(Song).filter(Song.title == "Karma").one()
    teksanski = music_session.query(Song).filter(Song.title == "Teksański").one()
    # Title search offers Karma, the user says it's wrong and picks Teksański by hand
    selects = iter([menu._UNMATCHED_BY_TITLE, karma, menu._SEARCH_INSTEAD])
    confirms = iter([True, False])  # "Use <song>?" yes; then no tag update
    monkeypatch.setattr(menu, "open_file_browser_terminal", lambda start: folder)
    monkeypatch.setattr(menu, "open_and_set_global_database_sessions", lambda: (music_session, None))
    monkeypatch.setattr(menu, "search_and_pick_db_object", lambda *a, **k: teksanski)
    monkeypatch.setattr(menu.questionary, "text", lambda message, default="": _Answer(
        "Mix" if message.startswith("Playlist") else default))
    monkeypatch.setattr(menu.questionary, "select", lambda *a, **k: _Answer(next(selects)))
    monkeypatch.setattr(menu.questionary, "confirm", lambda *a, **k: _Answer(next(confirms)))

    menu._create_playlist_from_folder("ania")

    ((playlist_id, _, _, _, _),) = list_playlists("ania")
    from utils.database.user_playlists import playlist_songs
    songs, _ = playlist_songs(music_session, "ania", playlist_id)
    assert [s.title for s in songs] == ["Teksański"]


def _add_liquid_tension(music_session) -> Song:
    acid_rain = Song(title="Acid Rain", artist=Artist(name="Liquid Tension Experiment"))
    music_session.add(acid_rain)
    music_session.commit()
    return acid_rain


def test_misspelt_tag_artist_is_only_suggested(tmp_path, music_session, monkeypatch):
    """"Liiquid tension experiment" isn't even a candidate for the substring
    artist search; the title plus a resembling artist find the song, but as a
    suggestion the user confirms, never as a match."""
    from utils.discoveries.import_engine import LocalSongMatcher

    acid_rain = _add_liquid_tension(music_session)
    matcher = LocalSongMatcher(music_session)
    typo = {"artist_name": "Liiquid tension experiment", "title": "Acid Rain"}
    assert matcher.match_exact(typo) is None
    assert matcher.match_similar(typo) is acid_rain
    assert matcher.match(typo) is acid_rain  # files.db / covers take it as is
    assert matcher.match_similar({"artist_name": "Totally Other", "title": "Acid Rain"}) is None
    # An artist that exists is the exact path's business, not a guess
    assert matcher.match_similar({"artist_name": "Hey", "title": "Acid Rain"}) is None

    tags = {"Acid Rain - dscom.it.mp3": ("Liiquid tension experiment", "Acid Rain")}
    folder = _folder(tmp_path, tags)
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(zip(("artist_name", "title"), tags[f.name])))
    playlist = fp.build_folder_playlist(music_session, folder)
    (track,) = playlist.unmatched
    assert track.song_id is None and track.similar_song is acid_rain


def test_ambiguous_similar_title_suggests_nothing(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher

    _add_liquid_tension(music_session)
    music_session.add(Song(title="Acid Rain", artist=Artist(name="Liquid Tension Experiments")))
    music_session.commit()
    typo = {"artist_name": "Liiquid tension experiment", "title": "Acid Rain"}
    assert LocalSongMatcher(music_session).match_similar(typo) is None


def test_similar_song_confirmed_then_file_renamed_and_tagged(users_env, tmp_path, music_session, monkeypatch):
    from mutagen.easyid3 import EasyID3
    menu = __import__("importlib").import_module("menu.main_menu.users")

    users.create_user("ania", "secret1")
    acid_rain = _add_liquid_tension(music_session)
    acid_rain.album, acid_rain.year = "Liquid Tension Experiment 2", 1999
    music_session.commit()
    folder = tmp_path / "music"
    folder.mkdir()
    old = _id3_file(folder / "Acid Rain - liquid tension experiment 2 - dscom.it.mp3",
                    artist="Liiquid tension experiment", title="Acid Rain")
    monkeypatch.setattr(fp, "read_file_metadata",
                        lambda f: {"artist_name": "Liiquid tension experiment", "title": "Acid Rain"})

    questions = []
    monkeypatch.setattr(menu, "open_file_browser_terminal", lambda start: folder)
    monkeypatch.setattr(menu, "open_and_set_global_database_sessions", lambda: (music_session, None))
    monkeypatch.setattr(menu.questionary, "text", lambda message, default="": _Answer("Mix"))
    monkeypatch.setattr(menu, "load_auto_accept_artist_similarity", lambda: 1.0)  # always ask
    monkeypatch.setattr(menu, "load_closest_auto_accept_score", lambda: 1.1)
    monkeypatch.setattr(menu.questionary, "select", lambda message, **k: _Answer(menu._CONFIRM_SONG))
    monkeypatch.setattr(menu.questionary, "confirm",
                        lambda message, **k: questions.append(message) or _Answer(True))

    menu._create_playlist_from_folder("ania")

    ((playlist_id, _, count, missing, _),) = list_playlists("ania")
    assert (count, missing) == (1, 0)
    # Only the fix question - no file is left for the general tag update
    assert questions == ["Fix the names and tags of these mp3 files?"]
    new = folder / "Liquid Tension Experiment - Acid Rain.mp3"
    assert not old.exists() and new.exists()
    assert EasyID3(new)["artist"] == ["Liquid Tension Experiment"]
    assert EasyID3(new)["album"] == ["Liquid Tension Experiment 2"]


def test_similar_song_rejected_shows_the_other_options(users_env, tmp_path, music_session, monkeypatch):
    menu = __import__("importlib").import_module("menu.main_menu.users")

    users.create_user("ania", "secret1")
    _add_liquid_tension(music_session)
    folder = tmp_path / "music"
    folder.mkdir()
    old = _id3_file(folder / "x.mp3")
    monkeypatch.setattr(fp, "read_file_metadata",
                        lambda f: {"artist_name": "Liiquid tension experiment", "title": "Acid Rain"})
    monkeypatch.setattr(menu, "load_auto_accept_artist_similarity", lambda: 1.0)  # always ask
    monkeypatch.setattr(menu, "load_closest_auto_accept_score", lambda: 1.1)
    selects = iter([menu._SIMILAR_OTHER, menu._UNMATCHED_STOP])
    monkeypatch.setattr(menu, "open_file_browser_terminal", lambda start: folder)
    monkeypatch.setattr(menu, "open_and_set_global_database_sessions", lambda: (music_session, None))
    monkeypatch.setattr(menu.questionary, "text", lambda message, default="": _Answer("Mix"))
    monkeypatch.setattr(menu.questionary, "select", lambda *a, **k: _Answer(next(selects)))

    menu._create_playlist_from_folder("ania")

    assert list_playlists("ania") == []
    assert old.exists()


def test_rename_to_song_cleans_the_name_and_never_overwrites(tmp_path, music_session):
    from utils.discoveries.mp3_utils import rename_to_song, song_file_name

    karma = music_session.query(Song).filter(Song.title == "Karma").one()
    karma.title = 'Karma: what? "AC/DC"'
    assert song_file_name(karma) == "Hey - Karma_ what_ _AC_DC_.mp3"

    teksanski = music_session.query(Song).filter(Song.title == "Teksański").one()
    taken = _id3_file(tmp_path / "Hey - Teksański.mp3")
    other = _id3_file(tmp_path / "other.mp3")
    with pytest.raises(FileExistsError):
        rename_to_song(other, teksanski)
    assert rename_to_song(taken, teksanski) == taken  # already named so
    assert other.exists()


def test_rename_to_song_never_overwrites_a_file_differing_only_in_case(tmp_path, music_session):
    from utils.discoveries.mp3_utils import rename_to_song

    teksanski = music_session.query(Song).filter(Song.title == "Teksański").one()
    right = tmp_path / "Hey - Teksański.mp3"
    right.write_bytes(b"the right one")
    lower = tmp_path / "hey - teksański.mp3"
    lower.write_bytes(b"another file")

    with pytest.raises(FileExistsError):
        rename_to_song(lower, teksanski)
    assert right.read_bytes() == b"the right one"
    assert lower.read_bytes() == b"another file"

    right.unlink()
    assert rename_to_song(lower, teksanski) == right
    assert right.read_bytes() == b"another file" and not lower.exists()


def test_rename_to_song_renames_one_file_seen_under_two_spellings(tmp_path, music_session, monkeypatch):
    # A case-insensitive filesystem: the new spelling "exists" and is the same file
    import pathlib
    from utils.discoveries.mp3_utils import rename_to_song

    teksanski = music_session.query(Song).filter(Song.title == "Teksański").one()
    lower = tmp_path / "hey - teksański.mp3"
    lower.write_bytes(b"the song")
    right = tmp_path / "Hey - Teksański.mp3"
    real_exists = pathlib.Path.exists
    monkeypatch.setattr(pathlib.Path, "exists", lambda p, **kw: p == right or real_exists(p, **kw))
    monkeypatch.setattr(pathlib.Path, "samefile", lambda p, other: {p, pathlib.Path(other)} == {lower, right})

    assert rename_to_song(lower, teksanski) == right
    assert right.read_bytes() == b"the song"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Hey - Teksański.mp3"]


@pytest.mark.parametrize("title, expected", [
    ("T03: Mimiga Town", "Mimiga Town"), ("03. Song", "Song"), ("03 - Song", "Song"),
    ("Track 5) Song", "Song"),
    # Numbers that belong to the title
    ("1979", "1979"), ("7 Rings", "7 Rings"), ("2:00 AM", "2:00 AM"), ("99 Luftballons", "99 Luftballons"),
    ("01 - ", "01 -"),  # nothing left: kept
])
def test_strip_track_number_prefix(title, expected):
    from utils.common.text_utils import strip_track_number_prefix
    assert strip_track_number_prefix(title) == expected


def test_track_number_in_tag_title_still_matches(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher, find_songs_by_title

    mimiga = Song(title="Mimiga Town", artist=Artist(name="Cave Story OST"))
    music_session.add(mimiga)
    music_session.commit()
    tag = {"artist_name": "Cave Story OST", "title": "T03: Mimiga Town"}
    assert LocalSongMatcher(music_session).match_exact(tag) is mimiga
    assert find_songs_by_title(music_session, "T03: Mimiga Town") == [mimiga]


def _menu_for_folder(monkeypatch, music_session, folder, tags: dict, selects: list, texts=None, confirms=None):
    """The users menu with mocked prompts: `tags` by file name, `selects` /
    `confirms` answered in order, `texts` by message (default: the default),
    every confirm message recorded in the returned list."""
    menu = __import__("importlib").import_module("menu.main_menu.users")
    selects, asked = iter(selects), []
    confirms = iter(confirms) if confirms is not None else None
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(tags[f.name]))
    monkeypatch.setattr(menu, "open_file_browser_terminal", lambda start: folder)
    monkeypatch.setattr(menu, "open_and_set_global_database_sessions", lambda: (music_session, None))
    monkeypatch.setattr(menu, "submit_global_database_session", music_session.commit)
    monkeypatch.setattr(menu.questionary, "text", lambda message, default="": _Answer(
        "Mix" if message.startswith("Playlist") else (texts or {}).get(message, default)))
    monkeypatch.setattr(menu.questionary, "select", lambda *a, **k: _Answer(next(selects)))
    monkeypatch.setattr(menu.questionary, "confirm", lambda message, **k: asked.append(message) or _Answer(
        next(confirms) if confirms is not None else True))
    return menu, asked


def test_file_kept_without_a_song_and_file_left_out(users_env, tmp_path, music_session, monkeypatch):
    from utils.database.user_playlists import playlist_songs

    users.create_user("ania", "secret1")
    folder = tmp_path / "music"
    folder.mkdir()
    for name in ("1.mp3", "2.mp3", "3.mp3"):
        _id3_file(folder / name)
    tags = {"1.mp3": {"artist_name": "Hey", "title": "Karma"},
            "2.mp3": {"artist_name": None, "title": "The Top 25 Final Fantasy Songs of All Time"},
            "3.mp3": {"artist_name": None, "title": "Intro"}}
    menu, asked = _menu_for_folder(monkeypatch, music_session, folder, tags,
                                   ["Keep it in the playlist without a song", "Leave it out of the playlist"],
                                   confirms=[False])

    menu._create_playlist_from_folder("ania")

    ((playlist_id, _, count, missing, _),) = list_playlists("ania")
    assert (count, missing) == (2, 1)
    songs, without = playlist_songs(music_session, "ania", playlist_id)
    assert [s.title for s in songs] == ["Karma"]
    assert [t.title for t in without] == ["The Top 25 Final Fantasy Songs of All Time"]
    # Only Karma's tags are offered; the files kept/left out are never touched
    assert asked == ["Update the mp3 tags of these 1 file(s) with the data from the database?"]
    assert (folder / "2.mp3").exists() and (folder / "3.mp3").exists()


def test_every_file_left_out_saves_nothing(users_env, tmp_path, music_session, monkeypatch):
    users.create_user("ania", "secret1")
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "x.mp3")
    menu, _ = _menu_for_folder(monkeypatch, music_session, folder, {"x.mp3": {"artist_name": None, "title": None}},
                               ["Leave it out of the playlist"])
    menu._create_playlist_from_folder("ania")
    assert list_playlists("ania") == []


def test_file_added_to_the_database_as_a_new_song(users_env, tmp_path, music_session, monkeypatch):
    from mutagen.easyid3 import EasyID3

    users.create_user("ania", "secret1")
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "The Top 25 Final Fantasy Songs of All Time.mp3")
    tags = {"The Top 25 Final Fantasy Songs of All Time.mp3":
            {"artist_name": None, "title": "The Top 25 Final Fantasy Songs of All Time"}}
    menu, asked = _menu_for_folder(monkeypatch, music_session, folder, tags,
                                   ["Add it to the database as a new song"],
                                   texts={"Artist:": "Final Fantasy OST"})

    menu._create_playlist_from_folder("ania")

    song = music_session.query(Song).filter(Song.title == "The Top 25 Final Fantasy Songs of All Time").one()
    assert song.artist.name == "Final Fantasy OST"
    assert asked == ["Add this song to the database?", "Fix the names and tags of these mp3 files?"]
    ((_, _, count, missing, _),) = list_playlists("ania")
    assert (count, missing) == (1, 0)
    new = folder / "Final Fantasy OST - The Top 25 Final Fantasy Songs of All Time.mp3"
    assert EasyID3(new)["artist"] == ["Final Fantasy OST"]


def test_adding_a_song_reuses_the_artist_and_an_existing_song(users_env, tmp_path, music_session, monkeypatch):
    from utils.discoveries.folder_playlist import add_song_for_file, song_for_file_plan

    hey = music_session.query(Artist).filter(Artist.name == "Hey").one()
    karma = music_session.query(Song).filter(Song.title == "Karma").one()
    assert song_for_file_plan(music_session, "hey", "Karma (Official Video)") == (hey, karma)
    assert add_song_for_file(music_session, "hey", "Karma (Official Video)") is karma
    assert song_for_file_plan(music_session, "Hey", "Moja i twoja nadzieja") == (hey, None)
    new = add_song_for_file(music_session, "Hey", "Moja i twoja nadzieja")
    assert new.id is not None and new.artist is hey
    assert music_session.query(Artist).filter(Artist.name == "Hey").count() == 1


def test_and_in_tag_title_matches_ampersand_in_database(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher, find_existing_song

    angel = Song(title="Angel of Small Death & the Codeine Scene", artist=Artist(name="Hozier"))
    music_session.add(angel)
    music_session.commit()
    tag = {"artist_name": "Hozier", "title": "Angel Of Small Death And The Codeine Scene"}
    assert LocalSongMatcher(music_session).match_exact(tag) is angel
    # Only the loose match: the import's own "already exists?" check stays strict
    assert find_existing_song(tag["title"], [angel]) is None


def test_swapped_artist_and_title_in_tags_is_suggested(tmp_path, music_session, monkeypatch):
    from utils.discoveries.import_engine import LocalSongMatcher

    nib = Song(title="N.I.B.", artist=Artist(name="Black Sabbath"))
    music_session.add(nib)
    music_session.commit()
    matcher = LocalSongMatcher(music_session)
    swapped = {"artist_name": "N.I.B.", "title": "Black Sabbath"}
    assert matcher.match_exact(swapped) is None
    assert matcher.match_similar(swapped) is nib

    tags = {"Black Sabath - N.I.B.- HD Sound.mp3": ("N.I.B.", "Black Sabbath")}
    folder = _folder(tmp_path, tags)
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(zip(("artist_name", "title"), tags[f.name])))
    (track,) = fp.build_folder_playlist(music_session, folder).unmatched
    assert track.similar_song is nib  # asked about, then offered "Fix the names and tags"


def test_one_part_of_a_joined_artist_name_is_suggested(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher

    hero = Song(title="A Real Hero", artist=Artist(name="College & Electric Youth"))
    music_session.add(hero)
    music_session.commit()
    tag = {"artist_name": "College", "title": "A Real Hero (feat. Electric Youth)"}
    matcher = LocalSongMatcher(music_session)
    assert matcher.match_exact(tag) is None
    assert matcher.match_similar(tag) is hero
    assert matcher.match_similar({"artist_name": "Hey", "title": "A Real Hero"}) is None


def test_similar_title_of_the_tag_artist_is_only_suggested(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher

    dream = Artist(name="Dream Theater")
    losing = Song(title="Losing Time (Grand Finale)", artist=dream)
    music_session.add_all([losing, Song(title="The Spirit Carries On", artist=dream)])
    music_session.commit()
    tag = {"artist_name": "Dream Theater", "title": "Losing TimeGrande Finale"}
    matcher = LocalSongMatcher(music_session)
    assert matcher.match_exact(tag) is None
    assert matcher.match_similar(tag) is losing
    # files.db / covers take match() without asking: no title guess there
    assert matcher.match(tag) is None
    assert matcher.match_similar({"artist_name": "Dream Theater", "title": "Closing Time"}) is None


def test_two_similar_titles_suggest_nothing(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher

    dream = Artist(name="Dream Theater")
    music_session.add_all([Song(title="Metropolis Pt. 1", artist=dream), Song(title="Metropolis Pt. 2", artist=dream)])
    music_session.commit()
    assert LocalSongMatcher(music_session).match_similar(
        {"artist_name": "Dream Theater", "title": "Metropolis Pt 3"}) is None


def test_file_without_artist_is_suggested_the_one_song_with_its_title(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher

    top25 = Song(title="The Top 25 Final Fantasy Songs of All Time", artist=Artist(name="Unknown Artist"))
    hey = music_session.query(Artist).filter(Artist.name == "Hey").one()
    music_session.add_all([top25, Song(title="Intro", artist=hey),
                           Song(title="Intro", artist=Artist(name="Afina"))])
    music_session.commit()
    matcher = LocalSongMatcher(music_session)
    no_artist = {"artist_name": None, "title": "The Top 25 Final Fantasy Songs of All Time"}
    assert matcher.match_similar(no_artist) is top25
    assert matcher.match(no_artist) is None  # files.db / covers: no guess by title alone
    assert matcher.match_similar({"artist_name": None, "title": "Intro"}) is None  # two of them


def test_auto_accept_artist_similarity_setting(tmp_path, monkeypatch):
    from utils.discoveries import file_matching_settings as fms

    assert fms.load_auto_accept_artist_similarity() == 0.9  # default, no file yet
    fms.save_auto_accept_artist_similarity(0.95)
    assert fms.load_auto_accept_artist_similarity() == 0.95
    for bad in (0.5, 1.2):
        with pytest.raises(ValueError):
            fms.save_auto_accept_artist_similarity(bad)
    (tmp_path / "file_matching_config.json").write_text("not json")
    assert fms.load_auto_accept_artist_similarity() == 0.9


def test_confidently_misspelt_artist_is_matched_without_asking(tmp_path, music_session, monkeypatch):
    acid_rain = _add_liquid_tension(music_session)
    hero = Song(title="A Real Hero", artist=Artist(name="College & Electric Youth"))
    music_session.add(hero)
    music_session.commit()
    tags = {"1.mp3": ("Liiquid tension experiment", "Acid Rain"),
            "2.mp3": ("College", "A Real Hero (feat. Electric Youth)"),  # a part of a name: only suggested
            "3.mp3": ("Hej", "Karma")}  # short names need more than 0.9
    folder = _folder(tmp_path, tags)
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(zip(("artist_name", "title"), tags[f.name])))

    playlist = fp.build_folder_playlist(music_session, folder, 0.9)
    first = playlist.tracks[0]
    assert first.song_id == acid_rain.id and first.picked  # offered the name/tag fix
    assert playlist.auto_accepted == [(first, "Liiquid tension experiment")]
    assert [t.file.name for t in playlist.unmatched] == ["2.mp3", "3.mp3"]
    assert playlist.unmatched[0].similar_song is hero

    # 1 = always ask; the default argument never auto-accepts
    for kwargs in ({"min_artist_similarity": 1.0}, {}):
        playlist = fp.build_folder_playlist(music_session, folder, **kwargs)
        assert playlist.auto_accepted == [] and playlist.unmatched[0].similar_song is acid_rain


def test_auto_accepted_file_is_listed_and_offered_the_fix(users_env, tmp_path, music_session, monkeypatch, capsys):
    users.create_user("ania", "secret1")
    _add_liquid_tension(music_session)
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "Acid Rain - dscom.it.mp3")
    menu, asked = _menu_for_folder(monkeypatch, music_session, folder,
                                   {"Acid Rain - dscom.it.mp3": {"artist_name": "Liiquid tension experiment",
                                                                 "title": "Acid Rain"}},
                                   selects=[])  # no "Is this the right song?" asked

    menu._create_playlist_from_folder("ania")

    assert "matched automatically" in capsys.readouterr().out
    assert asked == ["Fix the names and tags of these mp3 files?"]
    assert (folder / "Liquid Tension Experiment - Acid Rain.mp3").exists()
    ((_, _, count, missing, _),) = list_playlists("ania")
    assert (count, missing) == (1, 0)


def test_file_with_no_tags_and_no_separator_is_matched_by_its_name(tmp_path, music_session, monkeypatch):
    """read_file_metadata() gives no title at all for such a file; its name
    is the title (as the user is shown it), so the one song with it is
    suggested."""
    top25 = Song(title="The Top 25 Final Fantasy Songs of All Time", artist=Artist(name="Unknown Artist"))
    music_session.add(top25)
    music_session.commit()
    folder = _folder(tmp_path, {"The Top 25 Final Fantasy Songs of All Time.mp3": None})
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: {"artist_name": None, "title": None})

    (track,) = fp.build_folder_playlist(music_session, folder).unmatched
    assert track.title == "The Top 25 Final Fantasy Songs of All Time"
    assert track.similar_song is top25


def test_tag_artist_that_is_a_synonym_finds_the_artist(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher, find_matching_artist

    konkret = Artist(name="Konkret", synonyms="I nad kropką")
    ocean = Song(title="Ocean Drive (Duke Dumont cover)", artist=konkret)
    music_session.add(ocean)
    music_session.commit()
    assert find_matching_artist("I nad kropką") is konkret
    matcher = LocalSongMatcher(music_session)
    assert matcher.match_exact({"artist_name": "I nad kropką", "title": "Ocean Drive (Duke Dumont cover)"}) is ocean
    # A typo in the title too: only suggested
    typo = {"artist_name": "I nad kropką", "title": "Ocea Drive (Duke Dumont cover)"}
    assert matcher.match_exact(typo) is None and matcher.match_similar(typo) is ocean


def test_html_entities_in_tags_are_read_as_characters(tmp_path, music_session):
    """Some download sites write "&amp;" into tags and file names; the real
    read_file_metadata() turns it back into "&"."""
    lean_on = Song(title="Lean On", artist=Artist(name="Major Lazer"))
    music_session.add(lean_on)
    music_session.commit()
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "Major Lazer &amp; DJ Snake - Lean On (feat. MØ).mp3",
              artist="Major Lazer &amp; DJ Snake", title="Lean On")
    _id3_file(folder / "Ty &amp; ja.mp3")  # no tags, no separator: the name is the title

    playlist = fp.build_folder_playlist(music_session, folder)
    assert playlist.tracks[0].song_id == lean_on.id
    assert playlist.unmatched[0].title == "Ty & ja"


def test_file_name_split_on_a_bare_hyphen_is_also_read_whole_as_the_title(tmp_path, music_session):
    """"Re-Animator.mp3" with no tags is read as "Re" - "Animator"; the
    whole name may be the title, so that song is suggested (asked, never
    taken by match()), with the whole name's score - also when an artist
    "Re" already exists."""
    from utils.discoveries.import_data_from_mp3_tags import read_file_metadata
    from utils.discoveries.import_engine import LocalSongMatcher

    animator = Song(title="Re-Animator", artist=Artist(name="Richard Band"))
    music_session.add(animator)
    music_session.commit()
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "Re-Animator.mp3")
    _id3_file(folder / "Hey - Karma.mp3")  # spaced separator: no whole-name reading

    metadata = read_file_metadata(folder / "Re-Animator.mp3")
    assert (metadata["artist_name"], metadata["title"], metadata["unsplit_title"]) == ("Re", "Animator", "Re-Animator")
    assert "unsplit_title" not in read_file_metadata(folder / "Hey - Karma.mp3")

    matcher = LocalSongMatcher(music_session)
    assert matcher.match_similar(metadata) is animator
    assert matcher.match(metadata) is None
    assert matcher.match_score(metadata, animator) == 1.0
    assert matcher.match_similar({**metadata, "unsplit_title": None}) is None


def test_a_file_name_with_a_hyphen_inside_a_word_is_read_as_the_title(tmp_path, music_session):
    """"Svefn-g-englar.mp3" with no tags: a hyphen tying a single letter to a
    word isn't a separator (it used to read artist "Svefn" / title
    "g-englar"), so the whole name is the title and Sigur Rós's song is
    suggested by its title alone - never taken by match()."""
    from utils.discoveries.import_data_from_mp3_tags import read_file_metadata
    from utils.discoveries.import_engine import LocalSongMatcher

    svefn = Song(title="Svefn-G-Englar", artist=Artist(name="Sigur Rós"))
    music_session.add(svefn)
    music_session.commit()
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "Svefn-g-englar.mp3")

    metadata = read_file_metadata(folder / "Svefn-g-englar.mp3")
    assert (metadata["artist_name"], metadata["title"]) == (None, "Svefn-g-englar")

    matcher = LocalSongMatcher(music_session)
    assert matcher.match_similar(metadata) is svefn
    assert matcher.match(metadata) is None

    (track,) = fp.build_folder_playlist(music_session, folder, auto_accept_score=0.99).unmatched
    # "svefn g englar" isn't distinctive enough to take without asking
    assert track.similar_song is svefn

    music_session.add(Song(title="Other", artist=Artist(name="Svefn")))
    music_session.commit()
    assert LocalSongMatcher(music_session).match_similar(metadata) is svefn


def _yt_compare_env(tmp_path, music_session, monkeypatch, tags: dict, items: list, select):
    """Folder + mocked YouTube playlist for "Create playlist from folder and
    compare with YT playlist"; the files' tags come from `tags`."""
    menu = __import__("importlib").import_module("menu.main_menu.users")
    users.create_user("ania", "secret1")
    folder = tmp_path / "music"
    folder.mkdir()
    for name in tags:
        (folder / name).write_bytes(b"")
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(zip(("artist_name", "title"), tags[f.name])))
    monkeypatch.setattr(menu, "get_playlist_items", lambda playlist_input: items)
    monkeypatch.setattr(menu, "open_file_browser_terminal", lambda start: folder)
    monkeypatch.setattr(menu, "open_and_set_global_database_sessions", lambda: (music_session, None))
    monkeypatch.setattr(menu.questionary, "text", lambda message, default="": _Answer(
        "https://www.youtube.com/playlist?list=PLx" if message.startswith("YouTube") else "Mix"))
    monkeypatch.setattr(menu.questionary, "select", lambda message, choices, **k: _Answer(select(message, choices)))
    monkeypatch.setattr(menu.questionary, "confirm", lambda *a, **k: _Answer(False))
    return menu


def _saved_tracks(username: str) -> list:
    from utils.database.user_datatables import Playlist, user_db_session
    ((playlist_id, *_),) = list_playlists(username)
    with user_db_session(users.user_dir(username) / "user.db") as session:
        return [(t.title, t.song_id, t.youtube_video_id, t.duration_s)
                for t in session.get(Playlist, playlist_id).tracks]


def test_folder_compared_with_youtube_playlist(users_env, tmp_path, music_session, monkeypatch):
    tags = {"1.mp3": ("Hey", "Karma"), "2.mp3": ("Hey", "Teksanski"),
            "3.mp3": ("Nobody", "Unknown song"), "4.mp3": ("Extra", "Extra file")}
    items = [
        {"video_id": "ytB", "title": "Hey - Teksański (Official Video)", "channel": "Hey"},  # by name
        {"video_id": "vid1", "title": "Karma", "channel": "Hey - Topic"},                    # same song
        {"video_id": "ytU", "title": "Something odd", "channel": "Some channel"},           # paired by hand
        {"video_id": "ytAfina", "title": "Afina - Pay Your Respect", "channel": "Afina", "duration": 200.4},
        {"video_id": "del", "title": "[Deleted video]", "channel": ""},
    ]
    asked = []

    def select(message, choices):
        asked.append(message)
        menu = __import__("importlib").import_module("menu.main_menu.users")
        if message.startswith("Which video"):
            videos = [c.value for c in choices if hasattr(c, "value") and not isinstance(c.value, str)]
            if asked.count(message) == 1:
                # Only the unpaired videos are offered, never the deleted one
                assert sorted(v.video_id for v in videos) == ["ytAfina", "ytU"]
                return next(v for v in videos if v.video_id == "ytU")
            return menu._PAIR_NO_LINK
        if message.startswith("No file is this video"):
            return menu._VIDEO_ADD
        if message.startswith("What should happen"):
            return menu._UNMATCHED_KEEP
        raise AssertionError(message)

    menu = _yt_compare_env(tmp_path, music_session, monkeypatch, tags, items, select)
    menu._create_playlist_from_folder_and_youtube("ania")

    karma, teksanski, pay = (music_session.query(Song).filter(Song.title == t).one().id
                             for t in ("Karma", "Teksański", "Pay Your Respect"))
    # The YouTube playlist's order, the file without a video last; the
    # video without a file matched to the database like a file
    assert _saved_tracks("ania") == [
        ("Teksański", teksanski, "ytB", None),
        ("Karma", karma, "vid1", None),
        ("Unknown song", None, "ytU", None),
        ("Pay Your Respect", pay, "ytAfina", 200),
        ("Extra file", None, None, None),
    ]


def test_youtube_compare_suggests_the_videos_song_and_stops_unsaved(users_env, tmp_path, music_session, monkeypatch):
    from utils.discoveries import folder_youtube_playlist as fyp
    from utils.discoveries.import_engine import LocalSongMatcher

    # A file the database doesn't know, paired with a video it does: the
    # video's song is only suggested, and its link wins over the song's
    music_session.query(Song).filter(Song.title == "Pay Your Respect").one().youtube_video_id = "songs-own"
    music_session.commit()
    track = fp.FolderTrack(file=Path("x.mp3"), title="Pay Your Respekt", artist="Afina")
    (entry,) = fyp.youtube_entries(LocalSongMatcher(music_session),
                                   [{"video_id": "ytK", "title": "Afina - Pay Your Respect", "channel": "Afina"}])
    assert fyp.pair_automatically([track], [entry]) == [(track, entry)]
    fyp.link_track(track, entry, LocalSongMatcher(music_session))
    assert (track.song_id, track.similar_song.title, track.youtube_video_id) == (None, "Pay Your Respect", "ytK")
    assert track.scores[track.similar_song.id] > 0.9  # shown with the suggestion, like the file's own
    fp.assign_song(track, track.similar_song)
    assert track.youtube_video_id == "ytK"

    menu = _yt_compare_env(tmp_path, music_session, monkeypatch, {"1.mp3": ("Nobody", "Unknown song")},
                           [{"video_id": "ytU", "title": "Something odd", "channel": "Some channel"}],
                           lambda message, choices: "Stop (don't save the playlist)")
    menu._create_playlist_from_folder_and_youtube("ania")
    assert list_playlists("ania") == []


def test_file_without_artist_is_offered_the_songs_sharing_its_title(users_env, tmp_path, music_session, monkeypatch):
    from utils.discoveries.import_engine import LocalSongMatcher

    users.create_user("ania", "secret1")
    bach = Song(title="Toccata and Fugue in d minor", artist=Artist(name="Johann Sebastian Bach"))
    mae = Song(title="Toccata and Fugue in D Minor", artist=Artist(name="Vanessa-Mae"))
    music_session.add_all([bach, mae])
    music_session.commit()
    name = "Toccata and Fugue in D Minor (Best Version Ever).mp3"
    tag = {"artist_name": None, "title": "Toccata and Fugue in D Minor (Best Version Ever)"}
    matcher = LocalSongMatcher(music_session)
    assert matcher.match_similar(tag) is None
    assert matcher.title_candidates(tag, 5) == [bach, mae]  # by artist
    assert matcher.title_candidates(tag, 1) == []  # more than the limit: search by title instead
    assert matcher.title_candidates({**tag, "artist_name": "Hey"}, 5) == []  # only for files with no artist

    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / name)
    menu, asked = _menu_for_folder(monkeypatch, music_session, folder, {name: tag}, [mae])
    menu._create_playlist_from_folder("ania")

    from utils.database.user_playlists import playlist_songs
    ((playlist_id, _, _, _, _),) = list_playlists("ania")
    assert [s.id for s in playlist_songs(music_session, "ania", playlist_id)[0]] == [mae.id]
    assert asked == ["Fix the names and tags of these mp3 files?"]
    assert (folder / "Vanessa-Mae - Toccata and Fugue in D Minor.mp3").exists()


def test_none_of_the_title_candidates_shows_the_other_options(users_env, tmp_path, music_session, monkeypatch):
    menu = __import__("importlib").import_module("menu.main_menu.users")
    users.create_user("ania", "secret1")
    music_session.add_all([Song(title="Intro", artist=Artist(name="Afina")),
                           Song(title="Intro", artist=music_session.query(Artist).filter(Artist.name == "Hey").one())])
    music_session.commit()
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "Intro.mp3")
    menu, _ = _menu_for_folder(monkeypatch, music_session, folder, {"Intro.mp3": {"artist_name": None, "title": "Intro"}},
                               [menu._NONE_OF_THESE, "Keep it in the playlist without a song"])
    menu._create_playlist_from_folder("ania")
    ((_, _, count, missing, _),) = list_playlists("ania")
    assert (count, missing) == (1, 1)


def test_best_guesses_rank_by_score_and_need_the_title_to_reach_it(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher

    garbage, radiohead = Artist(name="Garbage"), Artist(name="Radiohead")
    paranoid = Song(title="I Think I'm Paranoid", artist=garbage)
    music_session.add_all([paranoid, Song(title="Stupid Girl", artist=garbage),
                           Song(title="Paranoid Android", artist=radiohead)])
    music_session.commit()
    matcher = LocalSongMatcher(music_session)

    guesses = matcher.best_guesses({"artist_name": "Garbadge", "title": "I think im paranoyd"}, 3, 0.6)
    assert guesses[0][0] is paranoid and guesses[0][1] > 0.9
    assert matcher.match_score({"artist_name": "Garbadge", "title": "I think im paranoyd"}, paranoid) == guesses[0][1]
    # A perfect artist doesn't bring a different title over the bar
    assert matcher.best_guesses({"artist_name": "Garbage", "title": "Queer"}, 3, 0.6) == []
    assert matcher.best_guesses({"artist_name": "Nobody", "title": "Completely Unknown"}, 3, 0.6) == []
    # No artist: the title alone
    (top, score), *_ = matcher.best_guesses({"artist_name": None, "title": "Paranoid Androyd"}, 3, 0.6)
    assert top.title == "Paranoid Android" and score > 0.9
    assert len(matcher.best_guesses({"artist_name": None, "title": "Paranoid"}, 1, 0.3)) == 1  # the limit


def test_closest_song_picked_with_its_score_shown(users_env, tmp_path, music_session, monkeypatch, capsys):
    from utils.discoveries.file_matching_settings import save_best_guess_min_score

    users.create_user("ania", "secret1")
    paranoid = Song(title="I Think I'm Paranoid", artist=Artist(name="Garbage"))
    music_session.add(paranoid)
    music_session.commit()
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "x.mp3")
    menu, asked = _menu_for_folder(monkeypatch, music_session, folder,
                                   {"x.mp3": {"artist_name": "Garbadge", "title": "I think im paranoyd"}},
                                   [paranoid])
    defaults = []
    select = menu.questionary.select
    monkeypatch.setattr(menu.questionary, "select", lambda message, **k: defaults.append(k.get("default")) or select(message, **k))

    menu._create_playlist_from_folder("ania")

    out = capsys.readouterr().out
    assert "Closest songs in the database:" in out
    assert defaults == [menu._NONE_OF_THESE]  # a reflex Enter picks nothing
    assert asked == ["Fix the names and tags of these mp3 files?"]
    assert (folder / "Garbage - I Think I'm Paranoid.mp3").exists()

    # Score shown next to every suggestion; a higher minimum offers nothing
    track = fp.FolderTrack(file=None, title="x", artist=None, scores={paranoid.id: 0.874})
    from utils.ui.song_colors import ARTIST_STYLE, DETAIL_STYLE, TITLE_STYLE, as_ansi
    tokens = menu._scored_tokens(track, paranoid)
    # Coloured as questionary tokens (ANSI codes would show as garbage in a choice)
    assert tokens[0] == (ARTIST_STYLE, "Garbage") and tokens[2] == (TITLE_STYLE, "I Think I'm Paranoid")
    assert tokens[-1] == (DETAIL_STYLE, "(87%)")
    assert as_ansi(tokens) == "\033[96mGarbage\033[0m - \033[93mI Think I'm Paranoid\033[0m  \033[90m(87%)\033[0m"
    save_best_guess_min_score(0.95)
    menu, _ = _menu_for_folder(monkeypatch, music_session, folder, {
        "Garbage - I Think I'm Paranoid.mp3": {"artist_name": "Garbadge", "title": "I think im paranoyd x"}}, [])
    playlist = fp.build_folder_playlist(music_session, folder, 1.1, 0.95)
    assert playlist.unmatched[0].best_guesses == []


def test_file_matching_settings_keep_each_other(tmp_path):
    from utils.discoveries import file_matching_settings as fms

    assert fms.load_best_guess_min_score() == 0.6
    fms.save_auto_accept_artist_similarity(0.95)
    fms.save_best_guess_min_score(0.7)
    assert (fms.load_auto_accept_artist_similarity(), fms.load_best_guess_min_score()) == (0.95, 0.7)
    for bad in (0.1, 0.99):
        with pytest.raises(ValueError):
            fms.save_best_guess_min_score(bad)


def test_title_starting_with_a_song_of_the_tag_artist_is_suggested(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher

    chumba, blur = Artist(name="Chumbawamba"), Artist(name="Blur")
    tub = Song(title="Tubthumping", artist=chumba)
    song, song2 = Song(title="Song", artist=blur), Song(title="Song 2", artist=blur)
    music_session.add_all([tub, song, song2])
    music_session.commit()
    matcher = LocalSongMatcher(music_session)
    tag = {"artist_name": "Chumbawamba",
           "title": "Tubthumping  Officiel official Soundtrack World Cup 98 Coupe du monde 98"}
    assert matcher.match_exact(tag) is None
    assert matcher.match_similar(tag) is tub
    assert matcher.match(tag) is None  # a title guess: never taken unconfirmed
    # The longest title the file's starts with wins; whole words only
    assert matcher.match_similar({"artist_name": "Blur", "title": "Song 2 Blur Remastered Wembley 1999"}) is song2
    assert matcher.match_similar({"artist_name": "Blur", "title": "Songbird remix"}) is None


def test_suggestion_scoring_at_least_the_auto_accept_score_is_taken(tmp_path, music_session, monkeypatch):
    acid_rain = _add_liquid_tension(music_session)
    music_session.add(Song(title="Intro", artist=Artist(name="Afina")))
    music_session.commit()
    tags = {"1.mp3": ("Liiquid tension experiment", "Acid Rain"),  # similar song, ~0.99
            "2.mp3": (None, "Intro"),                             # the one "Intro": 1.0 on the title alone
            "3.mp3": ("Garbadge", "Acid Rain")}                   # another artist: well below 0.99
    folder = _folder(tmp_path, tags)
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(zip(("artist_name", "title"), tags[f.name])))

    playlist = fp.build_folder_playlist(music_session, folder, 1.1, 0.6, 0.99)
    first = playlist.tracks[0]
    assert first.song_id == acid_rain.id and first.picked
    ((track, read_as, score),) = playlist.auto_accepted_scored
    assert track is first and read_as == ("Liiquid tension experiment", "Acid Rain") and score >= 0.99
    # No artist in the tags: asked, however high the title scores
    assert [t.file.name for t in playlist.unmatched][0] == "2.mp3"
    assert playlist.unmatched[0].scores[playlist.unmatched[0].similar_song.id] == 1.0
    # 1.1 (the default, above any score) never auto-accepts
    assert fp.build_folder_playlist(music_session, folder, 1.1, 0.6).auto_accepted_scored == []


def test_auto_accepted_by_score_is_listed(users_env, tmp_path, music_session, monkeypatch, capsys):
    users.create_user("ania", "secret1")
    _add_liquid_tension(music_session)
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "x.mp3")
    menu, asked = _menu_for_folder(monkeypatch, music_session, folder,
                                   {"x.mp3": {"artist_name": "Liiquid tension experiment", "title": "Acid Rain"}}, [])
    monkeypatch.setattr(menu, "load_auto_accept_artist_similarity", lambda: 1.0)  # not the artist rule

    menu._create_playlist_from_folder("ania")

    out = capsys.readouterr().out
    assert "1 file(s) auto-accepted, their closest song scoring at least 99%" in out
    assert "Acid Rain" in out and "%)" in out
    assert asked == ["Fix the names and tags of these mp3 files?"]


def test_closest_auto_accept_setting_defaults_to_99_percent():
    from utils.discoveries import file_matching_settings as fms

    assert fms.load_closest_auto_accept_score() == 0.99
    fms.save_closest_auto_accept_score(1.0)
    assert fms.load_closest_auto_accept_score() == 1.0
    assert fms.load_best_guess_min_score() == 0.6  # the other settings untouched
    with pytest.raises(ValueError):
        fms.save_closest_auto_accept_score(0.5)


def test_review_clears_the_screen_per_file_and_shows_the_previous_decision(
        users_env, tmp_path, music_session, monkeypatch, capsys):
    users.create_user("ania", "secret1")
    folder = tmp_path / "music"
    folder.mkdir()
    for name in ("1.mp3", "2.mp3", "3.mp3"):
        _id3_file(folder / name)
    tags = {"1.mp3": {"artist_name": "Hey", "title": "Karma"},
            "2.mp3": {"artist_name": "Nobody", "title": "Unknown one"},
            "3.mp3": {"artist_name": "Nobody", "title": "Unknown two"}}
    menu, _ = _menu_for_folder(monkeypatch, music_session, folder, tags,
                               ["Leave it out of the playlist", "Keep it in the playlist without a song"],
                               confirms=[False])
    clears, waits = [], []
    monkeypatch.setattr(menu, "clear_screen", lambda: clears.append(1))
    monkeypatch.setattr(menu.questionary, "press_any_key_to_continue",
                        lambda *a, **k: waits.append(1) or _Answer(None))

    menu._create_playlist_from_folder("ania")

    out = capsys.readouterr().out
    assert len(clears) == 2  # one screen per file without a song
    assert "Files without a song in the database (2 of 3)" in out
    assert "Previous: 2.mp3  ->  left out of the playlist" in out
    assert waits == []  # nothing auto-accepted to read first
    ((_, _, count, missing, _),) = list_playlists("ania")
    assert (count, missing) == (2, 1)


def test_auto_accepted_list_is_read_before_the_review_clears_it(users_env, tmp_path, music_session, monkeypatch):
    users.create_user("ania", "secret1")
    _add_liquid_tension(music_session)
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "1.mp3")
    _id3_file(folder / "2.mp3")
    tags = {"1.mp3": {"artist_name": "Liiquid tension experiment", "title": "Acid Rain"},
            "2.mp3": {"artist_name": "Nobody", "title": "Unknown one"}}
    menu, _ = _menu_for_folder(monkeypatch, music_session, folder, tags,
                               ["Keep it in the playlist without a song"], confirms=[False])
    waits = []
    monkeypatch.setattr(menu.questionary, "press_any_key_to_continue",
                        lambda *a, **k: waits.append(a) or _Answer(None))

    menu._create_playlist_from_folder("ania")
    assert len(waits) == 1


def test_fix_names_and_tags_plans_then_applies(tmp_path, music_session, monkeypatch):
    from mutagen.easyid3 import EasyID3
    from utils.discoveries.file_fixes import apply_file_fix, plan_file_fixes

    acid_rain = _add_liquid_tension(music_session)
    folder = tmp_path / "music"
    (folder / "sub").mkdir(parents=True)
    _id3_file(folder / "Acid Rain - dscom.it.mp3", artist="Liiquid tension experiment", title="Acid Rain")
    _id3_file(folder / "sub" / "Hey - Karma.mp3", artist="Hey", title="Karma")  # already right
    _id3_file(folder / "Hey - Teksanski.mp3", artist="Hey", title="Teksański")  # tags right, name not
    _id3_file(folder / "unknown.mp3", artist="Nobody", title="Unknown")
    tags = {"Acid Rain - dscom.it.mp3": ("Liiquid tension experiment", "Acid Rain"),
            "Hey - Karma.mp3": ("Hey", "Karma"), "Hey - Teksanski.mp3": ("Hey", "Teksański"),
            "unknown.mp3": ("Nobody", "Unknown")}
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(zip(("artist_name", "title"), tags[f.name])))

    plan = plan_file_fixes(music_session, folder, 0.9, 0.99)
    by_name = {fix.file.name: fix for fix in plan.fixes}
    assert set(by_name) == {"Acid Rain - dscom.it.mp3", "Hey - Teksanski.mp3"}
    assert by_name["Acid Rain - dscom.it.mp3"].song is acid_rain
    assert by_name["Acid Rain - dscom.it.mp3"].new_name == "Liquid Tension Experiment - Acid Rain.mp3"
    assert by_name["Acid Rain - dscom.it.mp3"].tags_differ
    assert (by_name["Hey - Teksanski.mp3"].new_name, by_name["Hey - Teksanski.mp3"].tags_differ) == ("Hey - Teksański.mp3", False)
    assert plan.up_to_date == 1 and [p.name for p in plan.not_in_database] == ["unknown.mp3"]
    # Planning wrote nothing
    assert EasyID3(folder / "Acid Rain - dscom.it.mp3")["artist"] == ["Liiquid tension experiment"]

    for fix in plan.fixes:
        apply_file_fix(fix)
    assert EasyID3(folder / "Liquid Tension Experiment - Acid Rain.mp3")["artist"] == ["Liquid Tension Experiment"]
    assert (folder / "Hey - Teksański.mp3").exists() and (folder / "unknown.mp3").exists()


def test_fix_names_and_tags_menu_asks_once(tmp_path, music_session, monkeypatch, capsys):
    import questionary
    from utils.ui import file_fix_review as ffr

    _add_liquid_tension(music_session)
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "a.mp3", artist="Liiquid tension experiment", title="Acid Rain")
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: {"artist_name": "Liiquid tension experiment",
                                                             "title": "Acid Rain"})
    monkeypatch.setattr(ffr, "open_file_browser_terminal", lambda start: folder)
    monkeypatch.setattr(ffr, "open_and_set_global_database_sessions", lambda: (music_session, None))
    asked = []
    monkeypatch.setattr(questionary, "confirm", lambda message, **k: asked.append(message) or _Answer(False))

    ffr.fix_file_names_and_tags()
    assert asked == ["Fix the names and tags of these mp3 files?"]
    assert "-> Liquid Tension Experiment - Acid Rain.mp3, tags" in capsys.readouterr().out
    assert (folder / "a.mp3").exists()  # declined: nothing changed


def test_pairing_by_hand_lists_the_closest_videos_and_searches_the_rest(users_env, tmp_path, music_session,
                                                                        monkeypatch):
    """A long YouTube playlist: only FILE_PAIR_VIDEO_CHOICES_MAX videos are
    listed (a taller list shows as blank lines), the others by search."""
    import questionary
    from config.constants import FILE_PAIR_VIDEO_CHOICES_MAX
    from utils.discoveries import folder_youtube_playlist as fyp

    items = [{"video_id": f"v{i}", "title": f"Band {i} - Song number {i}", "channel": f"Band {i}"}
             for i in range(30)]
    items.append({"video_id": "vR", "title": "Leon Bridges - River (Official Video)", "channel": "LeonBridgesVEVO"})
    seen = []

    def select(message, choices):
        if message.startswith("No file is this video"):
            return "Leave it out of the playlist"
        if message.startswith("What should happen with this file"):  # not in the database
            return "Keep it in the playlist without a song"
        videos = [c.value for c in choices if isinstance(c, questionary.Choice)]
        seen.append((len(videos), "Search the videos by title..." in choices))
        if len(seen) == 1:
            return "Search the videos by title..."
        return next(v for v in videos if v.video_id == "vR")

    menu = _yt_compare_env(tmp_path, music_session, monkeypatch, {"1.mp3": ("Leon Bridges", "Coming Home")},
                           items, select)
    text = menu.questionary.text
    monkeypatch.setattr(menu.questionary, "text", lambda message, default="": _Answer("bridges river")
                        if message.startswith("Part of") else text(message, default))
    menu._create_playlist_from_folder_and_youtube("ania")

    assert seen == [(FILE_PAIR_VIDEO_CHOICES_MAX, True), (1, True)]
    assert [t[2] for t in _saved_tracks("ania")] == ["vR"]
    entries = fyp.youtube_entries(fyp.LocalSongMatcher(music_session), items[-1:])
    assert fyp.search_entries(entries, "LEON  riVer") == entries  # every word, any case
    assert fyp.search_entries(entries, "leon ocean") == fyp.search_entries(entries, "") == []


def test_videos_without_artist_or_title_are_skipped(music_session):
    from utils.discoveries import folder_youtube_playlist as fyp

    items = [{"video_id": "ok", "title": "Afina - Pay Your Respect", "channel": "Afina"},
             {"video_id": "empty", "title": "", "channel": ""},
             {"video_id": "none", "title": None, "channel": "Some channel"},
             {"video_id": "gone", "title": "[Deleted video]", "channel": ""}]
    entries = fyp.youtube_entries(fyp.LocalSongMatcher(music_session), items)
    assert [e.video_id for e in entries] == ["ok"]


def test_file_without_artist_auto_accepted_only_for_a_distinctive_unique_title(tmp_path, music_session, monkeypatch):
    from utils.discoveries.import_engine import LocalSongMatcher, is_distinctive_title

    medieval = Song(title="Dragostea Din Tei (Numa Numa) (Medieval Cover)", artist=Artist(name="Middle Ages"))
    ozone = Song(title="Dragostea din tei", artist=Artist(name="O-Zone"))
    music_session.add_all([medieval, ozone, Song(title="Intro", artist=Artist(name="Afina"))])
    music_session.commit()
    assert is_distinctive_title("Dragostea Din Tei (Numa Numa) (Medieval Cover)")
    assert not is_distinctive_title("Intro") and not is_distinctive_title("Ocean Drive")
    # Same title without brackets: the whole title tells the two apart
    assert LocalSongMatcher(music_session).match_similar(
        {"artist_name": None, "title": "Dragostea Din Tei (Numa Numa) (Medieval Cover)"}) is medieval

    tags = {"1.mp3": (None, "Dragostea Din Tei (Numa Numa) (Medieval Cover)"),
            "2.mp3": (None, "Intro")}  # the one "Intro", 100% - but anyone's could be called so
    folder = _folder(tmp_path, tags)
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(zip(("artist_name", "title"), tags[f.name])))
    playlist = fp.build_folder_playlist(music_session, folder, 1.1, 0.6, 0.96)
    assert [t.song_id for t in playlist.tracks] == [medieval.id, None]
    assert [t.file.name for t, _, _ in playlist.auto_accepted_scored] == ["1.mp3"]


def test_every_yes_no_question_defaults_to_yes(monkeypatch):
    """ask_yes_no() is the one yes/no prompt: a bare Enter means yes, and no
    module asks questionary.confirm() directly any more."""
    import ast
    from utils.ui import prompts

    calls = []
    monkeypatch.setattr(prompts.questionary, "confirm", lambda message, **k: calls.append(k) or _Answer(True))
    assert prompts.ask_yes_no("Go on?") is True and calls == [{"default": True}]

    src = Path(__file__).resolve().parents[1] / "src"
    direct = [f"{p.relative_to(src)}:{n.lineno}" for p in src.rglob("*.py") if p.name != "prompts.py"
              for n in ast.walk(ast.parse(p.read_text()))
              if isinstance(n, ast.Attribute) and n.attr == "confirm" and getattr(n.value, "id", None) == "questionary"]
    assert direct == []


def test_artists_before_the_title_and_a_trailing_feat_are_ignored(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher, title_after_artists

    solovij = Artist(name="Христина Соловій")
    habit = Song(title="Шкідлива звичка feat. YanGo", artist=solovij)
    pluto = Artist(name="Unlike Pluto")
    original, cover = Song(title="No Scrubs", artist=pluto), Song(title="No Scrubs ft. Joanna Jones (Cover)", artist=pluto)
    music_session.add_all([habit, cover, original])  # the cover first: it mustn't win by order
    music_session.commit()
    matcher = LocalSongMatcher(music_session)
    assert matcher.match_exact({"artist_name": "Христина Соловій",
                                "title": "Христина Соловій & YanGo — Шкідлива звичка"}) is habit
    # Keeping the "ft." first tells an original from the feat. version beside it
    assert matcher.match_exact({"artist_name": "Unlike Pluto", "title": "No Scrubs (Official Video)"}) is original
    assert matcher.match_exact({"artist_name": "Unlike Pluto", "title": "No Scrubs ft. Joanna Jones HD"}) is cover

    assert title_after_artists("AC-DC - Back in Black", "AC-DC") == "Back in Black"
    assert title_after_artists("Yugopolis & Malenczuk / Gdzie sa przyjaciele moi?", "Yugopolis") == \
        "Gdzie sa przyjaciele moi?"
    assert title_after_artists("Love - Me Do", "Beatles") is None  # the artist isn't before the dash
    assert title_after_artists("Karma", "Hey") is None


def test_score_reads_swapped_tags_too(tmp_path, music_session, monkeypatch):
    from utils.discoveries.import_engine import LocalSongMatcher

    song = Song(title="Ne Bucuram In Ciuda Lor", artist=Artist(name="Carla's Dreams"))
    radiohead = Song(title="Paranoid Android", artist=Artist(name="Radiohead"))
    music_session.add_all([song, radiohead])
    music_session.commit()
    matcher = LocalSongMatcher(music_session)
    swapped = {"artist_name": "Ne Bucuram In Ciuda Lor", "title": "Carla's Dreams"}
    assert matcher.match_similar(swapped) is song
    assert matcher.match_score(swapped, song) == 1.0  # not the ~0.2 of the tags as written
    (guess, score), = matcher.best_guesses({"artist_name": "Paranoid Androyd", "title": "Radiohed"}, 3, 0.6)
    assert guess is radiohead and score > 0.9

    # At 100% it's auto-accepted like any sure suggestion
    folder = _folder(tmp_path, {"x.mp3": None})
    monkeypatch.setattr(fp, "read_file_metadata", lambda f: dict(swapped))
    playlist = fp.build_folder_playlist(music_session, folder, 1.1, 0.6, 0.96)
    assert playlist.tracks[0].song_id == song.id and playlist.auto_accepted_scored


@pytest.mark.parametrize("text, expected", [
    ("y2meta.com - David Dean Burkhart Playlist _ September 2021 (128 kbps)",
     "David Dean Burkhart Playlist _ September 2021 (128 kbps)"),
    ("Angel Of Small Death - Hozier (128 kbps) (greenmp3.com)", "Angel Of Small Death - Hozier (128 kbps)"),
    ("Acid Rain - liquid tension experiment 2 - dscom.it", "Acid Rain - liquid tension experiment 2"),
    ("will.i.am - Scream & Shout", "will.i.am - Scream & Shout"),  # not a site
    ("y2meta.com", "y2meta.com"),  # never emptied
    # The junk words of src/config/file_name_junk.txt, and Y2Mate's id/bitrate/timestamp suffix
    ("Y2Mate.is - Hugh Laurie - Saint James Infirmary (Let Them Talk)-AzEBH6DZJVk-160k-1645418932557",
     "Hugh Laurie - Saint James Infirmary (Let Them Talk)"),
    ("Hugh Laurie - Y2Mate.is - Saint James Infirmary", "Hugh Laurie - Saint James Infirmary"),
    ("Song (y2mate)", "Song"),
    ("Mylo - In My Arms", "Mylo - In My Arms"),
])
def test_strip_site_names(text, expected):
    from utils.common.normalizer import strip_site_names
    assert strip_site_names(text) == expected


def test_site_names_are_dropped_before_reading_a_file(tmp_path):
    """The real read_file_metadata(): a site name in the file name doesn't
    become the artist, and a tag holding only one counts as missing."""
    from mutagen.easyid3 import EasyID3
    from utils.discoveries.import_data_from_mp3_tags import read_file_metadata

    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413  # real MPEG frames, so mutagen reads the tags
    named = tmp_path / "y2meta.com - David Dean Burkhart Playlist _ September 2021 (128 kbps).mp3"
    named.write_bytes(frame * 20)
    tagged = tmp_path / "Hey - Karma.mp3"
    tagged.write_bytes(frame * 20)
    tags = EasyID3()
    tags["artist"], tags["title"] = "y2meta.com", "Karma (greenmp3.com)"
    tags.save(tagged)

    metadata = read_file_metadata(named)
    assert (metadata["artist_name"], metadata["title"]) == ("David Dean Burkhart Playlist", "September 2021 (128 kbps)")
    metadata = read_file_metadata(tagged)
    assert (metadata["artist_name"], metadata["title"]) == ("Hey", "Karma")  # the artist tag was only a site


def test_punctuation_only_title_matches_only_itself(music_session):
    """normalize(":/") is "", which is "in" every title: such a title must
    be spelt the same, or the import would skip it as already there."""
    from utils.discoveries.import_engine import LocalSongMatcher, find_existing_song

    mistmorn = Artist(name="Mistmorn")
    other = Song(title="Дідько, я у розпачі", artist=mistmorn)
    music_session.add(other)
    music_session.commit()
    assert find_existing_song(":/", [other]) is None
    assert LocalSongMatcher(music_session).match_exact({"artist_name": "Mistmorn", "title": ":/"}) is None
    face = Song(title=":/", artist=mistmorn)
    music_session.add(face)
    music_session.commit()
    assert find_existing_song(" :/ ", [other, face]) is face
    assert find_existing_song(":(", [other, face]) is None


def test_new_song_added_after_rejecting_the_one_found(users_env, tmp_path, music_session, monkeypatch):
    users.create_user("ania", "secret1")
    hey = music_session.query(Artist).filter(Artist.name == "Hey").one()
    folder = tmp_path / "music"
    folder.mkdir()
    _id3_file(folder / "x.mp3")
    # "Karma (live)" loosely is the "Karma" already there - but it's another recording
    menu, asked = _menu_for_folder(monkeypatch, music_session, folder,
                                   {"x.mp3": {"artist_name": "Nobody", "title": "Unknown"}},
                                   ["Add it to the database as a new song"],
                                   texts={"Artist:": "Hey", "Title:": "Karma (live)"},
                                   confirms=[False, True, False])
    menu._create_playlist_from_folder("ania")

    assert asked[:2] == ["Use this song?", "Add it as a new song instead?"]
    assert sorted(s.title for s in music_session.query(Song).filter(Song.artist_id == hey.id)) == [
        "Karma", "Karma (live)", "Teksański"]


def test_trailing_year_in_the_tag_title_is_ignored(music_session):
    from utils.discoveries.import_engine import LocalSongMatcher, _title_core

    fish = Artist(name="Fish Go Deep")
    cure = Song(title="The Cure & The Cause (Dennis Ferrer remix)", artist=fish)
    party = Song(title="Party Like It's 1999", artist=fish)
    music_session.add_all([cure, party])
    music_session.commit()
    matcher = LocalSongMatcher(music_session)
    assert matcher.match_exact({"artist_name": "Fish Go Deep",
                                "title": "The Cure & The Cause (Dennis Ferrer Remix) [Full Length] 2006"}) is cure
    # A year that is the title, or ends it on both sides, still matches itself
    assert matcher.match_exact({"artist_name": "Fish Go Deep", "title": "Party Like It's 1999 (Official Video)"}) is party
    assert _title_core("1979") == "1979"


def test_polish_tags_misread_as_latin1_are_repaired(tmp_path):
    """Tags written in Windows-1250 come out as "Ma³pa"; read_file_metadata()
    re-reads them, but leaves genuine Latin-1 letters ("España") alone."""
    from mutagen.easyid3 import EasyID3
    from utils.common.normalizer import fix_cp1250_mojibake
    from utils.discoveries.import_data_from_mp3_tags import read_file_metadata

    misread = lambda text: text.encode("cp1250").decode("latin-1")
    assert fix_cp1250_mojibake(misread("Żółć gęślą jaźń")) == "Żółć gęślą jaźń"
    # Read as Windows-1252 ("œ" for ś, not in Latin-1), and a leading "Ż" ("¯")
    assert fix_cp1250_mojibake("Wasza Wspania³oœæ") == "Wasza Wspaniałość"
    assert fix_cp1250_mojibake("¯ycie jest jak tramwaj") == "Życie jest jak tramwaj"
    # No sure sign: only forced, or when the re-read word is one the database has
    from utils.common.normalizer import fix_cp1250_mojibake_by_words
    assert fix_cp1250_mojibake("Mêskie Granie") == "Mêskie Granie"
    assert fix_cp1250_mojibake("Mêskie Granie", force=True) == "Męskie Granie"
    assert fix_cp1250_mojibake("Męskie Granie", force=True) == "Męskie Granie"  # correct Polish stays
    assert fix_cp1250_mojibake_by_words("Mêskie Granie 2011-1", {"męskie", "granie"}) == "Męskie Granie 2011-1"
    assert fix_cp1250_mojibake_by_words("Efêmera", {"męskie"}) == "Efêmera"
    for fine in ("España", "¿Dónde estás?", "Crème brûlée", "Hey"):
        assert fix_cp1250_mojibake(fine) == fine

    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    file = tmp_path / "Bajm - Małpa i ja.mp3"
    file.write_bytes(frame * 20)
    tags = EasyID3()
    tags["artist"], tags["title"] = "Bajm", misread("Małpa i ja")
    tags.save(file)
    assert read_file_metadata(file)["title"] == "Małpa i ja"
