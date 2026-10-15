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
