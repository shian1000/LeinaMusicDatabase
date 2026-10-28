"""Field corrections from the phone (utils/database/inbox_field_corrections.py):
reading the field_corrections table, grouping repeats (newest wins), settling
what needs no answer (row gone, value already there, answered earlier),
following song merges, flags (changed since, artist no longer on the song),
value checks, applying answers (song album text vs renaming the whole album,
album title twins merged, artist merge) and finishing the files. Temp-file
SQLite with the album listener on, no mocking of SQL."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import albums as al
from utils.database import inbox_field_corrections as fc
from utils.database import inbox_requests as pr
from utils.database.datatables import Album, Artist, Base, Song

REQUEST_SQL = """
CREATE TABLE request (format INTEGER NOT NULL, created_at TEXT NOT NULL, user_name TEXT, app TEXT NOT NULL);
CREATE TABLE field_corrections (
  table_name TEXT NOT NULL, row_id INTEGER NOT NULL, column_name TEXT NOT NULL, old_value, new_value,
  song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, corrected_at TEXT NOT NULL,
  PRIMARY KEY (table_name, row_id, column_name));
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox = tmp_path / "Inbox"
    (inbox / "new").mkdir(parents=True)
    monkeypatch.setattr(pr, "INBOX_DIR", inbox)
    monkeypatch.setattr(pr, "inbox_available", lambda: True)
    changes = []
    monkeypatch.setattr(fc, "load_changes", lambda: list(changes))
    monkeypatch.setattr(pr, "load_changes", lambda: list(changes))

    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    al.register_album_sync(factory)
    session = factory()
    artist = Artist(id=10, name="Elise Trouw", origin="USA")
    other = Artist(id=11, name="Vulfpeck")
    # Artists first: the album listener looks a pending artist's explicit id up in the DB
    session.add_all([artist, other])
    session.commit()
    songs = [Song(id=4601 + i, title=t, artist=artist, album="Unravel", year=2023)
             for i, t in enumerate(["Chromatic", "Losing Sleep"])]
    songs.append(Song(id=4603, title="Dean Town", artist=other, album="The Beautiful Game", year=2016))
    session.add_all(songs)
    session.commit()
    return {"inbox": inbox, "session": session, "songs": songs, "changes": changes,
            "album": songs[0].album_entry}


def _request(inbox: Path, name: str, rows, extra_sql: str = "") -> Path:
    path = inbox / "new" / name
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(REQUEST_SQL + extra_sql)
        conn.execute("INSERT INTO request VALUES (1, '2026-10-14T10:00:00+02:00', 'shian', 'Leina Radio')")
        for row in rows:
            row = {"song_id": 4601, "title": "Chromatic", "artist": "Elise Trouw",
                   "corrected_at": "2026-10-01T09:00:00+02:00", **row}
            conn.execute(
                "INSERT INTO field_corrections VALUES (:table_name, :row_id, :column_name, :old_value, "
                ":new_value, :song_id, :title, :artist, :corrected_at)", row)
    conn.close()
    return path


def _row(table="songs", row_id=4601, column="year", old=2023, new=2022, **extra):
    return {"table_name": table, "row_id": row_id, "column_name": column, "old_value": old,
            "new_value": new, **extra}


def _cases(env):
    requests = pr.read_new_requests()
    return requests, fc.collect_field_correction_cases(env["session"], requests)


# ==================== reading ====================

def test_reads_corrections_and_knows_the_table(env):
    _request(env["inbox"], "shian_2026-10-14_10-00-00_001.db", [_row(), _row("artists", 10, "origin", "USA", None)])

    [request] = pr.read_new_requests()

    assert request.supported and request.unknown_tables == [] and request.dead_links == []
    assert [(c.table_name, c.column_name, c.old_value, c.new_value) for c in request.field_corrections] == [
        ("songs", "year", 2023, 2022), ("artists", "origin", "USA", None)]


def test_missing_column_is_an_unreadable_file(env):
    path = env["inbox"] / "new" / "2026-10-14_10-00-00_001.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("CREATE TABLE request (format, created_at, user_name, app);"
                       "INSERT INTO request VALUES (1, 'x', NULL, 'a');"
                       "CREATE TABLE field_corrections (table_name, row_id);")
    conn.close()

    [request] = pr.read_new_requests()

    assert "field_corrections lacks" in request.error


# ==================== cases ====================

def test_settles_what_needs_no_answer(env):
    env["changes"].append((1, 9999, None))
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [
        _row(),                                                   # open
        _row(row_id=4602, old=2023, new=2023),                    # already set
        _row(row_id=9999, old=1999, new=2000),                    # song deleted
        _row("artists", 77, "name", "Gone", "Gone!"),             # artist merged or deleted
        _row("songs", 4601, "mood", "x", "y"),                    # not a correctable field
    ])

    _, cases = _cases(env)

    assert sorted((c.table_name, c.row_id, c.status) for c in cases if c.row_id is not None) == [
        ("artists", 77, fc.ROW_GONE), ("songs", 4601, fc.UNSUPPORTED), ("songs", 4601, fc.OPEN),
        ("songs", 4602, fc.ALREADY_SET)]
    assert [c.status for c in cases if c.row_id is None] == [fc.ROW_GONE]


def test_follows_song_merges(env):
    env["changes"].extend([(1, 500, 501), (2, 501, 4601)])
    _request(env["inbox"], "2026-10-14_10-00-00_001.db",
             [_row(row_id=500, song_id=500, title="Chromatic (live)")])

    [case] = _cases(env)[1]

    assert case.row_id == 4601 and case.row is env["songs"][0] and case.status == fc.OPEN
    assert case.title_differs


def test_newest_correction_wins_and_repeats_are_one_case(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [_row(new=2019, corrected_at="2026-10-01T09:00:00+02:00")])
    _request(env["inbox"], "2026-10-13_10-00-00_001.db", [_row(new=2022, corrected_at="2026-10-01T11:00:00+02:00")])

    [case] = _cases(env)[1]

    assert len(case.reports) == 2
    assert case.new_value == 2022 and case.earlier_values == [2019]


def test_flags_changed_since_and_an_artist_no_longer_on_the_song(env):
    env["songs"][0].year = 2021
    env["songs"][0].artist = env["session"].get(Artist, 11)
    env["session"].commit()
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [_row(), _row("artists", 10, "origin", "USA", "US")])

    year, origin = _cases(env)[1]

    assert year.changed_since and not origin.changed_since
    assert origin.not_on_song and not year.not_on_song


@pytest.mark.parametrize("table, column, value, expected, error", [
    ("songs", "year", "1999", 1999, None),
    ("songs", "year", 1999.0, 1999, None),
    ("songs", "year", "199x", None, "whole number"),
    ("songs", "title", "  ", None, "can't be empty"),
    ("songs", "album", "  ", None, None),
    ("albums", "type", "ep", "ep", None),
    ("albums", "type", "LP", None, "isn't one of"),
    ("songs", "youtube_video_id", "dQw4w9WgXcQ", "dQw4w9WgXcQ", None),
    ("songs", "youtube_video_id", "https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ", None),
    ("songs", "youtube_video_id", "N/A", "N/A", None),
    ("songs", "youtube_video_id", None, None, None),
    ("songs", "youtube_video_id", "not a video", None, "isn't a YouTube video"),
    ("songs", "music_video_youtube_id", "dQw4w9WgXcQ", "dQw4w9WgXcQ", None),
    ("songs", "music_video_youtube_id", "N/A", None, "isn't a YouTube video"),
    ("songs", "music_video_youtube_id", "", None, None),
    ("songs", "genre", " Synthpop ;indie Pop, synth pop ", "synth-pop, indie pop", None),
    ("songs", "genre", " , ", None, None),
])
def test_values_are_checked(table, column, value, expected, error):
    stored, problem = fc.coerce_field_value(table, column, value)

    assert stored == expected
    assert (problem is None) if error is None else (error in problem)


# ==================== answers ====================

def test_accepting_changes_the_value_and_finishes_the_file(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [_row()])
    requests, [case] = _cases(env)

    fc.apply_field_correction(env["session"], case, fc.ACCEPT)
    env["session"].commit()
    moved, left = pr.finish_request_files(requests, [case])

    assert env["songs"][0].year == 2022
    assert len(moved) == 1 and left == []
    decision = json.loads((env["inbox"] / "decisions.json").read_text())["field:songs:4601:year"]
    assert decision["decision"] == fc.ACCEPT and decision["value"] == 2022


def test_rejected_is_not_asked_again_unless_corrected_later(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [_row(), _row(row_id=4602)])
    requests, cases = _cases(env)
    fc.apply_field_correction(env["session"], cases[0], fc.REJECT)
    env["session"].commit()
    pr.finish_request_files(requests, cases)  # stays: the second row is unanswered

    _, cases = _cases(env)
    assert [c.status for c in cases] == [fc.ANSWERED_EARLIER, fc.OPEN]
    assert env["songs"][0].year == 2023

    _request(env["inbox"], "2099-01-01_00-00-00_001.db", [_row(new=2020, corrected_at="2099-01-01T00:00:00+00:00")])
    _, cases = _cases(env)
    assert [c.status for c in cases if c.row_id == 4601] == [fc.OPEN]


def test_an_invalid_value_can_only_be_edited(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [_row(new="two thousand")])
    _, [case] = _cases(env)

    assert case.error and case.status == fc.OPEN
    with pytest.raises(ValueError):
        fc.apply_field_correction(env["session"], case, fc.ACCEPT)
    fc.apply_field_correction(env["session"], case, fc.EDIT, "2000")
    assert env["songs"][0].year == 2000


def test_song_album_moves_just_that_song(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [_row(column="album", old="Unravel", new="Unravel (Deluxe)")])
    _, [case] = _cases(env)
    album = env["album"]

    assert fc.song_album_to_rename(case) is album
    fc.apply_field_correction(env["session"], case, fc.ACCEPT)
    env["session"].commit()

    first, second = env["songs"][:2]
    assert first.album == "Unravel (Deluxe)" and first.album_entry is not album
    assert second.album_entry is album and album.title == "Unravel"


def test_song_album_can_rename_the_whole_album(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [_row(column="album", old="Unravel", new="Unravel EP")])
    _, [case] = _cases(env)
    album = env["album"]

    fc.apply_field_correction(env["session"], case, fc.RENAME_ALBUM)
    env["session"].commit()

    assert album.title == "Unravel EP"
    assert [(s.album, s.album_entry) for s in env["songs"][:2]] == [("Unravel EP", album)] * 2


def test_album_title_renames_its_songs_and_merges_a_twin(env):
    s = env["session"]
    album = env["album"]
    twin = Album(title="Unravel EP", artist_id=10)
    s.add(twin)
    s.commit()
    assert twin.id > album.id
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [_row("albums", album.id, "title", "Unravel", "Unravel EP")])
    _, [case] = _cases(env)

    assert fc.album_title_twin(s, case, "unravel ep") is twin
    fc.apply_field_correction(s, case, fc.ACCEPT)
    s.commit()

    assert s.get(Album, twin.id) is None  # the older id is kept
    assert album.title == "Unravel EP"
    assert all(song.album == "Unravel EP" and song.album_entry is album for song in env["songs"][:2])


def test_artist_name_twin_can_be_merged(env, monkeypatch):
    from utils.database import database_management as dm
    s = env["session"]
    monkeypatch.setattr(dm, "get_global_database_sessions", lambda: (s, None))
    monkeypatch.setattr(dm, "require_global_database_sessions", lambda: (s, None))
    _request(env["inbox"], "2026-10-14_10-00-00_001.db",
             [_row("artists", 11, "name", "Vulfpeck", "Elise trouw", song_id=4603, title="Dean Town")])
    _, [case] = _cases(env)

    [twin] = fc.artist_twins(s, case, case.new_value)
    assert twin.id == 10
    fc.apply_field_correction(s, case, fc.MERGE_ARTIST, twin)
    s.commit()
    s.expire_all()

    assert s.get(Artist, 11) is None
    assert s.get(Song, 4603).artist_id == 10


def test_a_file_with_dead_links_and_corrections_waits_for_both(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [_row()], extra_sql="""
        CREATE TABLE dead_youtube_links (song_id, title, artist, dead_video_id, detected_at);
        INSERT INTO dead_youtube_links VALUES (4602, 'Losing Sleep', 'Elise Trouw', 'dead1', '2026-10-14T09:00:00+02:00');
    """)
    requests = pr.read_new_requests()
    dead = pr.collect_dead_link_cases(env["session"], requests)
    [case] = fc.collect_field_correction_cases(env["session"], requests)

    fc.apply_field_correction(env["session"], case, fc.ACCEPT)
    moved, left = pr.finish_request_files(requests, dead + [case])

    assert moved == [] and len(left) == 1


# ==================== Latin forms ====================

def test_latin_forms_are_correctable_and_can_be_emptied(env):
    album = env["album"]
    album.title_latin = "Old form"
    env["session"].commit()
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [
        # sent before music.db had the columns: old_value NULL
        _row("songs", 4601, "title_latin", None, "  Chromatic (Latin) "),
        _row("artists", 10, "name_latin", None, "Elise"),
        _row("albums", album.id, "title_latin", "Old form", ""),
    ])
    requests, cases = _cases(env)

    assert [c.status for c in cases] == [fc.OPEN] * 3
    assert not any(c.changed_since for c in cases)
    assert [c.label for c in cases] == [
        "song title in Latin letters", "artist's name in Latin letters", "album title in Latin letters"]
    for case in cases:
        fc.apply_field_correction(env["session"], case, fc.ACCEPT)
    env["session"].commit()

    assert env["songs"][0].title_latin == "Chromatic (Latin)"
    assert env["session"].get(Artist, 10).name_latin == "Elise"
    assert album.title_latin is None
    moved, left = pr.finish_request_files(requests, cases)
    assert len(moved) == 1 and left == []


def test_a_renamed_row_points_at_its_latin_form(env):
    env["songs"][0].title_latin = "Khromatik"
    env["session"].commit()
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [
        _row("songs", 4601, "title", "Chromatic", "Chromatica"),
        _row("songs", 4602, "title", "Losing Sleep", "Losing Sleep!"),  # no form: nothing to point at
    ])
    _, [with_form, without] = _cases(env)

    assert fc.latin_form_to_check(with_form, with_form.new_value) == "Khromatik"
    assert fc.latin_form_to_check(with_form, "Chromatic") is None  # unchanged value
    assert fc.latin_form_to_check(without, without.new_value) is None

    fc.apply_field_correction(env["session"], with_form, fc.ACCEPT)
    assert env["songs"][0].title_latin == "Khromatik"  # kept as it was


# ==================== YouTube links set by hand ====================

def test_youtube_link_correction_is_a_manual_pick(env):
    from utils.database.datatables import BadYoutubeVideo
    session = env["session"]
    env["songs"][0].youtube_video_id = "oldVideo123"
    session.add_all([BadYoutubeVideo(video_id="newVideo123", song_id=None, reason="unplayable", marked_at="x")])
    session.commit()
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [
        _row("songs", 4601, "youtube_video_id", "oldVideo123", "newVideo123"),
        _row("songs", 4601, "music_video_youtube_id", None, "clipVideo12"),
    ])
    _, [link, clip] = _cases(env)

    assert link.label == "YouTube video" and clip.label == "music video"
    assert link.video_id == "newVideo123" and link.bad  # found bad before, shown to the owner
    assert fc.replaced_video(link, link.new_value) == "oldVideo123"
    assert fc.replaced_video(clip, clip.new_value) is None
    fc.apply_field_correction(session, link, fc.ACCEPT, old_video_wrong=True)
    fc.apply_field_correction(session, clip, fc.ACCEPT)
    session.commit()

    assert env["songs"][0].youtube_video_id == "newVideo123"
    assert env["songs"][0].music_video_youtube_id == "clipVideo12"
    assert [(b.video_id, b.song_id, b.reason) for b in session.query(BadYoutubeVideo)] == [
        ("oldVideo123", 4601, "wrong_for_song")]


def test_no_video_and_no_link_are_values(env):
    env["songs"][0].youtube_video_id = "oldVideo123"
    env["session"].commit()
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [
        _row("songs", 4601, "youtube_video_id", "oldVideo123", "N/A"),
        _row("songs", 4602, "youtube_video_id", None, None),  # already empty
    ])
    _, [na, empty] = _cases(env)

    assert na.status == fc.OPEN and na.error is None and na.video_id is None
    assert empty.status == fc.ALREADY_SET


def test_a_taken_back_correction_settles_the_older_one_too(env):
    older = _request(env["inbox"], "2026-10-14_10-00-00_001.db",
                     [_row(new=2022, corrected_at="2026-10-01T09:00:00+02:00")])
    _request(env["inbox"], "2026-10-14_11-00-00_001.db",
             [_row(old=2023, new=2023, corrected_at="2026-10-02T09:00:00+02:00")])

    requests, [case] = _cases(env)

    assert case.status == fc.ALREADY_SET and len(case.reports) == 2
    moved, left = pr.finish_request_files(requests, [case])
    assert len(moved) == 2 and left == []

    # The older one read again on its own (e.g. it waited in review/ while
    # the takeback was settled from new/): answered, not offered
    review = env["inbox"] / "review"
    review.mkdir()
    again = review / older.name
    (env["inbox"] / "processed" / older.name).rename(again)
    [case] = fc.collect_field_correction_cases(env["session"], [pr.read_request_file(again)])
    assert case.status == fc.ANSWERED_EARLIER
    assert env["songs"][0].year == 2023


def test_genre_is_correctable_in_the_databases_spelling(env):
    env["songs"][2].genre = "Funk Rock"  # a spelling music.db already uses
    env["session"].commit()
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", [
        _row("songs", 4601, "genre", None, "funk rock, R&B"),
        _row("songs", 4602, "genre", None, "", song_id=4602, title="Losing Sleep"),
    ])
    requests, cases = _cases(env)

    assert [c.label for c in cases] == ["song genre", "song genre"]
    # an empty value for a song with no genre is nothing to do
    assert [c.status for c in cases] == [fc.OPEN, fc.ALREADY_SET]
    fc.apply_field_correction(env["session"], cases[0], fc.ACCEPT)
    env["session"].commit()

    assert env["songs"][0].genre == "Funk Rock, r&b"


def test_artist_merge_happens_in_the_session_given(env, monkeypatch):
    """DB-15: the merge used the global session whatever the caller passed,
    so committing the caller's session didn't commit the merge."""
    from utils.database import database_management as dm
    s = env["session"]
    monkeypatch.setattr(dm, "get_global_database_sessions",
                        lambda: pytest.fail("the merge must use the session it was given"))
    monkeypatch.setattr(dm, "require_global_database_sessions",
                        lambda: pytest.fail("the merge must use the session it was given"))
    _request(env["inbox"], "2026-10-14_10-00-00_001.db",
             [_row("artists", 11, "name", "Vulfpeck", "Elise trouw", song_id=4603, title="Dean Town")])
    _, [case] = _cases(env)
    [twin] = fc.artist_twins(s, case, case.new_value)

    fc.apply_field_correction(s, case, fc.MERGE_ARTIST, twin)
    s.commit()

    assert s.get(Artist, 11) is None
    assert s.get(Song, 4603).artist_id == 10
