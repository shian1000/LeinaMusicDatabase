"""Songs Leina Radio asked to have added (utils/database/inbox_song_requests.py):
reading the song_requests table, grouping by artist + title, settling what is
in the database already or was rejected before (artist + title, YouTube video
or Spotify track), rejecting into music.db's rejected_song_requests, the
metadata handed to the import, and the automatic pass never adding a song.
Temp-file SQLite; the local song matcher is faked."""

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import inbox_auto as ia
from utils.database import inbox_requests as pr
from utils.database import inbox_song_requests as sr
from utils.database.datatables import Artist, Base, RejectedSongRequest, Song

REQUEST_SQL = """
CREATE TABLE request (format INTEGER NOT NULL, created_at TEXT NOT NULL, user_name TEXT, app TEXT NOT NULL);
CREATE TABLE song_requests (
  title TEXT NOT NULL, artist TEXT, album TEXT, year INTEGER, duration_s INTEGER,
  youtube_video_id TEXT, spotify_track_id TEXT, source TEXT NOT NULL, requested_at TEXT NOT NULL);
"""
WHEN = "2026-10-18T09:00:00+02:00"


class FakeMatcher:
    """LocalSongMatcher by exact (artist, title); artists by name or synonym."""

    def __init__(self, songs, artists=()):
        self.songs = songs
        self.artists = [*{s.artist.id: s.artist for s in songs}.values(), *artists]

    def artist(self, name):
        return next((a for a in self.artists
                     if name in (a.name, *[n.strip() for n in (a.synonyms or "").split(",")])), None)

    def match(self, metadata):
        artist = self.artist(metadata["artist_name"] or "")
        return next((s for s in self.songs if artist is not None and s.artist.id == artist.id
                     and s.title == metadata["title"]), None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox = tmp_path / "Inbox"
    (inbox / "new").mkdir(parents=True)
    monkeypatch.setattr(pr, "INBOX_DIR", inbox)
    monkeypatch.setattr(pr, "inbox_available", lambda: True)
    monkeypatch.setattr(pr, "load_changes", lambda: [])

    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    artist = Artist(id=10, name="Elise Trouw")
    boombox = Artist(id=20, name="Бумбокс", synonyms="familyboombox, BoomBox")
    other = Artist(id=30, name="Okean Elzy")
    songs = [Song(id=1, title="Chromatic", artist=artist, youtube_video_id="chrom"),
             Song(id=2, title="Вахтёрам", artist=boombox)]
    session.add_all([*songs, other])
    session.commit()
    matcher = FakeMatcher(songs, [other])
    import utils.discoveries.import_engine as ie
    monkeypatch.setattr(ie, "LocalSongMatcher", lambda _session: matcher)
    return {"inbox": inbox, "session": session, "matcher": matcher}


def _request(inbox: Path, name: str, rows, folder: str = "new") -> Path:
    """rows: dicts with title and any of artist, album, year, duration_s,
    youtube_video_id, spotify_track_id, source, requested_at."""
    path = inbox / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(REQUEST_SQL)
        conn.execute("INSERT INTO request VALUES (1, ?, NULL, 'Leina Radio')", (WHEN,))
        for row in rows:
            row = {"source": "youtube", "requested_at": WHEN, **row}
            conn.execute(f"INSERT INTO song_requests ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                         list(row.values()))
    conn.close()
    return path


def _cases(env):
    requests = pr.read_new_requests()
    return requests, sr.collect_song_request_cases(env["session"], requests, matcher=env["matcher"])


def _names(inbox: Path, folder: str) -> list:
    directory = inbox / folder
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def test_reads_the_table_and_groups_one_song_from_several_files(env):
    _request(env["inbox"], "2026-10-18_10-00-00_001.db",
             [{"title": "Unseen", "artist": "Elise Trouw", "youtube_video_id": "v1"}])
    _request(env["inbox"], "2026-10-18_11-00-00_002.db",
             [{"title": "unseen", "artist": "ELISE TROUW", "spotify_track_id": "sp1", "source": "spotify",
               "album": "Unseen", "year": 2020, "requested_at": "2026-10-18T11:00:00+02:00"}])

    requests, [case] = _cases(env)

    assert all(r.supported and not r.unknown_tables for r in requests)
    assert case.pending
    assert case.youtube_video_ids == ["v1"] and case.spotify_track_ids == ["sp1"]
    assert case.sources == ["YouTube", "Spotify"]
    assert case.latest.title == "unseen"
    metadata = case.metadata()
    assert metadata["artist_name"] == "ELISE TROUW" and metadata["title"] == "unseen"
    assert metadata["album"] == "Unseen" and metadata["year"] == 2020
    assert metadata["youtube_video_id"] == "v1"
    assert case.metadata("Elise Trouw", "Unseen")["artist_name"] == "Elise Trouw"


def test_a_song_in_the_database_already_is_settled(env):
    _request(env["inbox"], "2026-10-18_10-00-00_001.db", [
        {"title": "Chromatic", "artist": "Elise Trouw"},
        {"title": "Some upload title", "artist": None, "youtube_video_id": "chrom"},
    ])

    requests, cases = _cases(env)

    assert [c.status for c in cases] == [sr.ALREADY_IN_DATABASE] * 2
    assert all(c.existing.id == 1 for c in cases)
    moved, left = pr.finish_request_files(requests, cases)
    assert len(moved) == 1 and not left


@pytest.mark.parametrize("channel, expected", [
    ("familyboombox", ("Бумбокс", "Твiй на 100%")),   # the artist's synonym
    ("Some Uploads", ("Бумбокс", "Твiй на 100%")),    # a channel the database doesn't know
    (None, ("Бумбокс", "Твiй на 100%")),
    ("Okean Elzy", None),                              # another artist's channel: left as asked
])
def test_a_channel_and_video_title_suggest_the_artist_from_the_title(env, channel, expected):
    _request(env["inbox"], "2026-10-19_17-50-39_001.db", [{"title": "Бумбокс - Твiй на 100%", "artist": channel}])

    _, [case] = _cases(env)

    assert case.pending
    assert case.suggested == expected


def test_a_title_naming_no_known_artist_suggests_nothing(env):
    _request(env["inbox"], "2026-10-19_17-50-39_001.db", [{"title": "Nobody - Song", "artist": "familyboombox"}])

    _, [case] = _cases(env)

    assert case.suggested is None


def test_the_suggested_split_finds_a_song_in_the_database(env):
    _request(env["inbox"], "2026-10-19_17-50-39_001.db", [{"title": "Бумбокс - Вахтёрам", "artist": "familyboombox"}])

    _, [case] = _cases(env)

    assert case.status == sr.ALREADY_IN_DATABASE
    assert case.existing.id == 2


def test_rejecting_publishes_it_and_settles_later_requests(env):
    _request(env["inbox"], "2026-10-18_10-00-00_001.db",
             [{"title": "Unseen", "artist": "Elise Trouw", "youtube_video_id": "v1", "spotify_track_id": "sp1"}])
    requests, [case] = _cases(env)

    sr.reject_song_request(env["session"], case)
    env["session"].commit()

    assert case.status == pr.DECIDED
    [row] = env["session"].query(RejectedSongRequest).all()
    assert (row.artist, row.title, row.youtube_video_id, row.spotify_track_id) == \
        ("Elise Trouw", "Unseen", "v1", "sp1")
    assert row.rejected_at
    assert pr.load_decisions()[case.key]["decision"] == sr.REJECT
    pr.finish_request_files(requests, [case])
    assert _names(env["inbox"], "processed") == ["2026-10-18_10-00-00_001.db"]

    # The same song again: by name, by its video, by its Spotify track
    _request(env["inbox"], "2026-10-18_12-00-00_002.db", [
        {"title": "UNSEEN", "artist": "elise trouw"},
        {"title": "Unseen (Official Video)", "artist": "EliseTrouwVEVO", "youtube_video_id": "v1"},
        {"title": "Unseen - Remastered", "artist": "Elise Trouw", "spotify_track_id": "sp1"},
        {"title": "Other", "artist": "Elise Trouw"},
    ])
    _, cases = _cases(env)

    assert [c.status for c in cases] == [sr.REJECTED_BEFORE] * 3 + [pr.OPEN]


def test_marking_added_records_the_answer(env):
    _request(env["inbox"], "2026-10-18_10-00-00_001.db", [{"title": "Unseen", "artist": "Elise Trouw"}])
    _, [case] = _cases(env)

    sr.mark_song_added(case, "Elise Trouw", "Unseen (Live)")

    assert case.status == pr.DECIDED
    decision = pr.load_decisions()[case.key]
    assert decision["decision"] == sr.ADD
    assert decision["added_as"] == {"artist": "Elise Trouw", "title": "Unseen (Live)"}


def test_a_file_missing_a_required_column_is_rejected(env):
    path = env["inbox"] / "new" / "2026-10-18_10-00-00_001.db"
    conn = sqlite3.connect(str(path))
    with conn:
        conn.execute("CREATE TABLE request (format INTEGER, created_at TEXT, user_name TEXT, app TEXT)")
        conn.execute("INSERT INTO request VALUES (1, ?, NULL, 'Leina Radio')", (WHEN,))
        conn.execute("CREATE TABLE song_requests (title TEXT, artist TEXT)")
    conn.close()

    [request] = pr.read_new_requests()

    assert request.error == "song_requests lacks source, requested_at"


def test_the_automatic_pass_never_adds_a_song(env):
    _request(env["inbox"], "2026-10-18_10-00-00_001.db", [{"title": "Unseen", "artist": "Elise Trouw"}])
    _request(env["inbox"], "2026-10-18_10-00-01_002.db", [{"title": "Chromatic", "artist": "Elise Trouw"}])

    songs_before = env["session"].query(Song).count()
    result = ia.auto_resolve(env["session"], pr.read_new_requests())

    assert not result.changes
    assert result.to_review == 1
    assert _names(env["inbox"], "review") == ["2026-10-18_10-00-00_001.db"]
    assert _names(env["inbox"], "processed") == ["2026-10-18_10-00-01_002.db"]
    assert env["session"].query(Song).count() == songs_before
    [request] = pr.read_review_requests()
    [case] = sr.collect_song_request_cases(env["session"], [request], matcher=env["matcher"])
    pr.load_case_checks([case], 24)
    assert case.auto_note == "adding a song waits for your approval"


def test_migration_matches_the_model(tmp_path):
    """migrations/music/0015 gives the same columns as create_all()."""
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    conn.executescript((repo_root / "migrations" / "music" / "0015_rejected_song_requests.sql").read_text())
    migrated = {row[1]: (row[2], bool(row[3])) for row in conn.execute("PRAGMA table_info(rejected_song_requests)")}
    conn.execute("INSERT INTO rejected_song_requests (title) VALUES ('t')")
    assert conn.execute("SELECT rejected_at FROM rejected_song_requests").fetchone()[0]
    conn.close()

    engine = create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    Base.metadata.create_all(engine)
    modelled = {c["name"]: (str(c["type"]), not c["nullable"])
                for c in inspect(engine).get_columns("rejected_song_requests")}
    assert {name: nullable for name, (_, nullable) in migrated.items() if name != "id"} == \
        {name: nullable for name, (_, nullable) in modelled.items() if name != "id"}
