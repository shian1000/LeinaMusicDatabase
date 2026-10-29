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
from utils.database import inbox_field_corrections as fc
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
    """LocalSongMatcher by exact (artist, title); artists by name or synonym,
    a "A / B" name by its first artist (as the real one's split proposal)."""

    def __init__(self, songs, artists=()):
        self.songs = songs
        self.artists = [*{s.artist.id: s.artist for s in songs}.values(), *artists]

    def artist(self, name):
        name = name.split(" / ")[0]
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
    monkeypatch.setattr(fc, "load_changes", lambda: [])

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
    descriptions = {}
    monkeypatch.setattr(sr, "fetch_video_description", lambda video_id: descriptions.get(video_id))
    monkeypatch.setattr(sr, "_credits_by_video", {})
    videos = {}
    monkeypatch.setattr(sr, "fetch_video_info", lambda video_id: videos.get(video_id))
    monkeypatch.setattr(sr, "_channel_readings", {})
    return {"inbox": inbox, "session": session, "matcher": matcher, "descriptions": descriptions,
            "videos": videos}


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


def test_a_known_channel_with_a_title_naming_no_known_artist_suggests_nothing(env):
    _request(env["inbox"], "2026-10-19_17-50-39_001.db", [{"title": "Nobody - Song", "artist": "familyboombox"}])

    _, [case] = _cases(env)

    assert case.suggested is None


@pytest.mark.parametrize("channel, expected", [
    ("familyboombox", ("Бумбокс", "Ангела (feat. Крихітка)")),  # the artist's synonym
    ("Бумбокс", None),                                          # already its name
    ("Okean Elzy", None),
    ("Some Uploads", None),                                     # a channel the database doesn't know
    (None, None),
])
def test_a_title_naming_no_artist_suggests_the_artist_a_synonym_stands_for(env, channel, expected):
    _request(env["inbox"], "2026-10-19_18-12-20_035.db", [{"title": "Ангела (feat. Крихітка)", "artist": channel}])

    _, [case] = _cases(env)

    assert case.pending
    assert case.suggested == expected


@pytest.mark.parametrize("channel, title, expected", [
    # A label's channel, "<artist>: <title>", an artist the database doesn't know yet
    ("Fueled By Ramen", "Travie McCoy: Billionaire ft. Bruno Mars", ("Travie McCoy", "Billionaire ft. Bruno Mars")),
    ("Some Uploads", "Nobody - Song", ("Nobody", "Song")),
    (None, "Nobody - Song", ("Nobody", "Song")),
    ("Some Uploads", "BoomBox: Вахтёрам", ("Бумбокс", "Вахтёрам")),        # spelt as the database does
    ("familyboombox", "Бумбокс: Твiй на 100%", ("Бумбокс", "Твiй на 100%")),
    ("Okean Elzy", "Nobody: Song", None),                                 # a known artist's channel
    ("Some Uploads", "Live at the Royal Albert Hall: Song", None),       # too long to be an artist
    ("Some Uploads", "Song 12:30", None),                                 # no space after the colon
    ("Some Uploads", "Song", None),
])
def test_an_unknown_channel_suggests_the_artist_from_the_title_even_a_new_one(env, channel, title, expected):
    _request(env["inbox"], "2026-10-21_19-26-51_001.db", [{"title": title, "artist": channel}])

    _, [case] = _cases(env)

    assert case.suggested == expected


@pytest.mark.parametrize("channel, title, expected", [
    # A fan channel naming the second artist, the database knowing the first
    ("фан Тіна кароль", "Бумбокс і Тіна Кароль Безодня", ("Бумбокс & Тіна Кароль", "Безодня")),
    ("Some Uploads", "Бумбокс і Okean Elzy Безодня", ("Бумбокс & Okean Elzy", "Безодня")),  # both known
    ("Some Uploads", "BoomBox x Okean Elzy Безодня", ("Бумбокс & Okean Elzy", "Безодня")),  # a synonym
    ("Some Uploads", "Бумбокс, Okean Elzy Безодня", ("Бумбокс & Okean Elzy", "Безодня")),
    ("Some Uploads", "Бумбокс Безодня", ("Бумбокс", "Безодня")),
    (None, "Бумбокс Безодня", ("Бумбокс", "Безодня")),
    ("familyboombox", "Бумбокс Безодня", ("Бумбокс", "Безодня")),  # the artist's own channel
    ("Some Uploads", "Бумбокс і Тіна Кароль Безодня", None),  # where "Тіна Кароль" ends is a guess
    ("фан Тіна кароль", "Тіна Кароль Безодня", None),          # only the channel knows it
    ("Okean Elzy", "Бумбокс Безодня", None),                   # another known artist's channel
    ("Some Uploads", "Бумбокс", None),                         # nothing left for the title
    ("Some Uploads", "Бумбокс і", None),
    ("Some Uploads", "Air Безодня", None),                     # a short lone name starts many titles
    ("Some Uploads", "Бумбоксы Безодня", None),                # not exactly the name
    ("Some Uploads", "Безодня Бумбокс", None),
    ("I Follow Rivers", "Бумбокс (La Vie d'Adèle)", None),     # only a bracket left for the title
    ("Some Uploads", "Бумбокс Безодня (Official Video)", ("Бумбокс", "Безодня (Official Video)")),
])
def test_a_title_without_a_separator_starting_with_known_artists_suggests_them(env, channel, title, expected):
    env["matcher"].artists.append(Artist(id=40, name="Air"))
    _request(env["inbox"], "2026-10-19_18-21-45_037.db", [{"title": title, "artist": channel}])

    _, [case] = _cases(env)

    assert case.suggested == expected


def test_a_feat_credit_leaves_the_title_for_the_imports_feat_artists(env):
    _request(env["inbox"], "2026-10-21_19-26-51_001.db",
             [{"title": "Travie McCoy: Billionaire ft. Bruno Mars", "artist": "Fueled By Ramen"}])

    _, [case] = _cases(env)

    metadata = case.metadata(*case.suggested)
    assert (metadata["artist_name"], metadata["title"]) == ("Travie McCoy", "Billionaire")
    assert metadata["feat_artists"] == ["Bruno Mars"]
    assert case.metadata("Elise Trouw", "Unseen")["feat_artists"] == []


def test_a_dash_wins_over_a_colon(env):
    _request(env["inbox"], "2026-10-21_19-26-51_001.db",
             [{"title": "Бумбокс - Live: Твiй на 100%", "artist": "Some Uploads"}])

    _, [case] = _cases(env)

    assert case.suggested == ("Бумбокс", "Live: Твiй на 100%")


def test_the_suggested_split_finds_a_song_in_the_database(env):
    _request(env["inbox"], "2026-10-19_17-50-39_001.db", [{"title": "Бумбокс - Вахтёрам", "artist": "familyboombox"}])

    _, [case] = _cases(env)

    assert case.status == sr.ALREADY_IN_DATABASE
    assert case.existing.id == 2


SNIADANIE_DESCRIPTION = """Provided to YouTube by Grand Imperial / Dwa ognie

Śniadanie · Natalia Przybysz · Fisz Emade Tworzywo

25

Auto-generated by YouTube."""


def _fisz_emade_tworzywo(env, songs=("Ballady", "Radar")):
    """Fisz (with `songs`), Emade, Tworzywo Sztuczne ("Tworzywo"), the duo
    Fisz Emade and Natalia Przybysz (one song) in the database."""
    session, matcher = env["session"], env["matcher"]
    fisz, natalia = Artist(id=40, name="Fisz"), Artist(id=44, name="Natalia Przybysz")
    artists = [fisz, Artist(id=41, name="Emade"), Artist(id=42, name="Tworzywo Sztuczne", synonyms="Tworzywo"),
               Artist(id=43, name="Fisz Emade"), natalia]
    new_songs = [Song(id=40 + i, title=title, artist=fisz) for i, title in enumerate(songs)]
    new_songs.append(Song(id=49, title="Kot", artist=natalia))
    session.add_all([*artists, *new_songs])
    session.commit()
    matcher.artists.extend(artists)
    matcher.songs.extend(new_songs)
    env["descriptions"]["sn1"] = SNIADANIE_DESCRIPTION


@pytest.mark.parametrize("channel", ["Release", "Release - Topic", "Various Artists"])
def test_a_generic_topic_channel_suggests_the_artists_its_description_credits(env, channel):
    _fisz_emade_tworzywo(env)
    _request(env["inbox"], "2026-10-26_16-26-30_031.db",
             [{"title": "Śniadanie", "artist": channel, "youtube_video_id": "sn1"}])

    _, [case] = _cases(env)

    assert case.pending
    assert case.suggested_from == sr.SUGGESTED_FROM_DESCRIPTION
    # Fisz has more songs: first, though the description names Natalia Przybysz first
    assert case.suggestions == [
        ("Fisz / Emade / Tworzywo Sztuczne", "Śniadanie (feat. Natalia Przybysz)"),
        ("Natalia Przybysz", "Śniadanie (feat. Fisz / Emade / Tworzywo Sztuczne)"),
    ]
    metadata = case.metadata(*case.suggested)
    assert metadata["title"] == "Śniadanie" and metadata["feat_artists"] == ["Natalia Przybysz"]
    assert metadata["youtube_video_id"] == "sn1"


def test_the_description_suggestion_finds_a_song_in_the_database(env):
    _fisz_emade_tworzywo(env, songs=("Śniadanie",))
    _request(env["inbox"], "2026-10-26_16-26-30_031.db",
             [{"title": "Śniadanie", "artist": "Release", "youtube_video_id": "sn1"}])

    _, [case] = _cases(env)

    assert case.status == sr.ALREADY_IN_DATABASE
    assert case.existing.id == 40


def test_a_rejected_video_needs_no_description(env, monkeypatch):
    _fisz_emade_tworzywo(env)
    env["session"].add(RejectedSongRequest(artist="Release", title="Śniadanie", youtube_video_id="sn1",
                                           rejected_at=WHEN))
    env["session"].commit()
    monkeypatch.setattr(sr, "fetch_video_description", lambda video_id: pytest.fail("fetched"))
    _request(env["inbox"], "2026-10-26_16-26-30_031.db",
             [{"title": "Sniadanie", "artist": "Release - Topic", "youtube_video_id": "sn1"}])

    _, [case] = _cases(env)

    assert case.status == sr.REJECTED_BEFORE


@pytest.mark.parametrize("artist, title", [
    ("Natalia Przybysz", "Śniadanie"),   # not a generic channel: no fetch
    ("Release", "Obiad"),                # the description credits another title
])
def test_the_description_counts_only_for_a_generic_channel_and_the_same_title(env, artist, title):
    _fisz_emade_tworzywo(env)
    _request(env["inbox"], "2026-10-26_16-26-30_031.db",
             [{"title": title, "artist": artist, "youtube_video_id": "sn1"}])

    _, [case] = _cases(env)

    assert case.pending and case.suggestions == []


GLOW = {"title": "Glow - Lyric Video (English / French)", "channel": "Attic Ocean"}


@pytest.mark.parametrize("row", [
    {"artist": "Glow", "title": "Lyric Video (English / French)"},   # Leina Radio's own split
    {"artist": None, "title": "Glow - Lyric Video (English / French)"},
])
def test_a_junk_only_title_suggests_the_videos_channel_and_title(env, row):
    env["videos"]["QrS48ruO-8U"] = GLOW
    _request(env["inbox"], "2026-10-27_21-09-10_042.db", [{**row, "youtube_video_id": "QrS48ruO-8U"}])

    _, [case] = _cases(env)

    assert case.pending
    assert case.suggested_from == sr.SUGGESTED_FROM_CHANNEL
    assert case.suggestions == [("Attic Ocean", "Glow")]
    assert case.metadata(*case.suggested)["youtube_video_id"] == "QrS48ruO-8U"


def test_the_channel_suggestion_finds_a_song_in_the_database(env):
    env["videos"]["chrom2"] = {"title": "Chromatic - Official Video", "channel": "Elise Trouw"}
    _request(env["inbox"], "2026-10-27_21-09-10_042.db",
             [{"artist": "Chromatic", "title": "Official Video", "youtube_video_id": "chrom2"}])

    _, [case] = _cases(env)

    assert case.status == sr.ALREADY_IN_DATABASE and case.existing.id == 1


def test_a_real_title_needs_no_video_data(env, monkeypatch):
    monkeypatch.setattr(sr, "fetch_video_info", lambda video_id: pytest.fail("fetched"))
    _request(env["inbox"], "2026-10-27_21-09-10_042.db",
             [{"artist": "Attic Ocean", "title": "Glow", "youtube_video_id": "QrS48ruO-8U"}])

    _, [case] = _cases(env)

    assert case.pending and case.suggestions == []


def test_video_data_that_cant_be_had_leaves_the_request_as_it_is(env):
    _request(env["inbox"], "2026-10-27_21-09-10_042.db",
             [{"artist": "Glow", "title": "Lyric Video (English / French)", "youtube_video_id": "gone"}])

    _, [case] = _cases(env)

    assert case.pending and case.suggestions == []


def test_a_name_known_whole_is_not_split(env):
    _fisz_emade_tworzywo(env)

    assert sr._database_spelling("Fisz Emade", env["matcher"]) == "Fisz Emade"
    assert sr._database_spelling("Fisz Unknown", env["matcher"]) == "Fisz Unknown"


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
