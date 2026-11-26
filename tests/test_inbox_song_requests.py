"""Songs Leina Radio asked to have added (utils/database/inbox_song_requests.py):
reading the song_requests table, grouping by artist + title, settling what is
in the database already or was rejected before (artist + title, YouTube video
or Spotify track), rejecting into music.db's rejected_song_requests, the
metadata handed to the import, and the automatic pass never adding a song.
Temp-file SQLite; the local song matcher is faked."""

import json
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
from utils.database.datatables import Artist, Base, RejectedSongRequest, SettledSongRequest, Song, SongIdChange
from utils.discoveries.import_engine import LocalSongMatcher as RealLocalSongMatcher

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
    ("Some Uploads", "Бумбокс", ("Бумбокс", "Some Uploads")),  # the whole title is the artist: swapped
    ("Some Uploads", "BoomBox", ("Бумбокс", "Some Uploads")),  # a synonym, as the database spells it
    ("Okean Elzy", "Бумбокс", None),                           # both known
    ("Release", "Бумбокс", None),                              # a generic Topic channel
    ("Some Uploads", "Air", None),                             # a short lone name is a likely title
    ("Some Uploads", "Бумбокс і", None),
    ("Some Uploads", "Air Безодня", None),                     # a short lone name starts many titles
    ("Some Uploads", "Бумбоксы Безодня", None),                # not exactly the name
    ("Some Uploads", "Безодня Бумбокс", None),
    # only a bracket left for the title: the song's title was the channel
    ("I Follow Rivers", "Бумбокс (La Vie d'Adèle)", ("Бумбокс", "I Follow Rivers")),
    ("Some Uploads", "Бумбокс (Original)", ("Бумбокс", "Some Uploads")),  # brackets aside: swapped
    ("Some Uploads", "Бумбокс (Live)", ("Бумбокс", "Some Uploads (Live)")),  # a version goes to the title
    ("Some Uploads", "Бумбокс Безодня (Official Video)", ("Бумбокс", "Безодня (Official Video)")),
])
def test_a_title_without_a_separator_starting_with_known_artists_suggests_them(env, channel, title, expected):
    env["matcher"].artists.append(Artist(id=40, name="Air"))
    _request(env["inbox"], "2026-10-19_18-21-45_037.db", [{"title": title, "artist": channel}])

    _, [case] = _cases(env)

    assert case.suggested == expected


@pytest.mark.parametrize("channel, title, expected", [
    (None, "Безодня Бумбокс", ("Бумбокс", "Безодня")),
    (None, "Безодня BoomBox", ("Бумбокс", "Безодня")),                 # a synonym
    (None, "Безодня (Live) Okean Elzy", ("Okean Elzy", "Безодня (Live)")),
    (None, "Безодня, Бумбокс", ("Бумбокс", "Безодня")),
    ("Some Uploads", "Безодня Бумбокс", None),  # a channel is the likelier artist
    (None, "Бумбокс", None),                    # nothing left for the title
    (None, "(Live) Бумбокс", None),
    (None, "Безодня Air", None),                # a short lone name ends many titles
    (None, "Безодня Бумбоксы", None),           # not exactly the name
])
def test_a_title_with_no_artist_ending_with_a_known_artist_suggests_it(env, channel, title, expected):
    env["matcher"].artists.append(Artist(id=40, name="Air"))
    _request(env["inbox"], "2026-11-02_00-06-09_071.db", [{"title": title, "artist": channel, "source": "file"}])

    _, [case] = _cases(env)

    assert case.suggested == expected


@pytest.mark.parametrize("artist, title, expected", [
    # Spotify lists the title's guest or remixer among the artists too
    ("Petite Meller, Oslo Green", "Watson (feat. Oslo Green)", ("Petite Meller", "Watson (feat. Oslo Green)")),
    ("Petite Meller, Marlon Hoffstadt", "High Forever - Marlon Hoffstadt Remix",
     ("Petite Meller", "High Forever - Marlon Hoffstadt Remix")),
    ("A, B, C", "Song (feat. C)", ("A, B", "Song (feat. C)")),
    ("Petite Meller, Kalman Chodakiewitz, Eli Shiffman", "In Love With Myself", None),  # all main artists
    ("Oslo Green, Petite Meller", "Watson (feat. Oslo Green)", None),  # the first artist always stays
    # Nothing read out of the title: Spotify's artist is a real credit
    ("Some Uploads", "Бумбокс", None),
    ("Some Uploads", "Бумбокс Безодня", None),
    ("Petite Meller", "Бумбокс - Безодня", None),
])
def test_a_spotify_request_suggests_only_its_artists_without_the_titles_guests(env, artist, title, expected):
    _request(env["inbox"], "2026-11-21_13-43-55_158.db",
             [{"title": title, "artist": artist, "spotify_track_id": "sp5", "source": "spotify"}])

    _, [case] = _cases(env)

    assert case.suggested == expected
    assert case.suggested_from == (sr.SUGGESTED_FROM_SPOTIFY if expected else None)


def test_a_spotify_request_puts_its_track_page_on_the_song(env):
    from utils.discoveries.import_engine import create_song_entry
    env["session"].get(Song, 2).spotify_url = "https://open.spotify.com/album/kept"
    _request(env["inbox"], "2026-11-21_14-01-58_163.db", [
        {"title": "Chromatic", "artist": "Elise Trouw", "spotify_track_id": "sp7", "source": "spotify"},
        {"title": "Вахтёрам", "artist": "Бумбокс", "spotify_track_id": "sp8", "source": "spotify"},
        {"title": "Unseen", "artist": "Elise Trouw", "spotify_track_id": "sp9", "source": "spotify"}])

    _, cases = _cases(env)

    # Found in the database: on the song when it has none, never over another
    assert env["session"].get(Song, 1).spotify_url == "https://open.spotify.com/track/sp7"
    assert env["session"].get(Song, 2).spotify_url == "https://open.spotify.com/album/kept"
    # Added: the new song gets it
    [unseen] = [c for c in cases if c.pending]
    song = create_song_entry(unseen.metadata(), env["session"].get(Artist, 10), env["session"])
    assert song.spotify_url == "https://open.spotify.com/track/sp9"
    # Found by the import (a corrected name): filled in the same way
    from utils.discoveries.import_engine import _backfill_youtube_link
    env["session"].get(Song, 1).spotify_url = None
    _backfill_youtube_link(cases[0].metadata(), env["session"].get(Song, 1))
    assert env["session"].get(Song, 1).spotify_url == "https://open.spotify.com/track/sp7"


def test_a_title_ending_with_its_artist_finds_the_song_in_the_database(env):
    _request(env["inbox"], "2026-11-02_00-06-09_071.db",
             [{"title": "Вахтёрам Бумбокс", "artist": None, "source": "file"}])

    _, [case] = _cases(env)

    assert case.status == sr.ALREADY_IN_DATABASE
    assert case.existing.id == 2


@pytest.mark.parametrize("title, found", [
    # Leina Radio cut the artist off at the first dash; the second starts a
    # description ("K. Pronko - Jestes Lekiem Na Cale Zlo - a great Polish song")
    ("Chromatic - a great song", True),
    # ... but a version is a song of its own: not the plain one
    ("Chromatic - Live", False),
    ("Chromatic - Acoustic Version", False),
])
def test_a_title_with_a_description_after_a_dash_finds_the_song(env, title, found):
    _request(env["inbox"], "2026-11-19_09-40-07_141.db", [{"title": title, "artist": "Elise Trouw"}])

    _, [case] = _cases(env)

    assert (case.status, case.existing and case.existing.id) == (
        (sr.ALREADY_IN_DATABASE, 1) if found else (pr.OPEN, None))


@pytest.mark.parametrize("emade_has_it", [False, True])
def test_known_artists_side_by_side_find_the_song_of_one_of_them(env, emade_has_it):
    """"Fisz Emade" (a Leina Radio request, 2026-11-20) is Fisz and Emade:
    Fisz's song of that title - unless both have one."""
    fisz, emade = Artist(id=40, name="Fisz"), Artist(id=50, name="Emade")
    songs = [Song(id=3, title="30 cm ponad chodnikami", artist=fisz),
             *([Song(id=4, title="30 cm ponad chodnikami", artist=emade)] if emade_has_it else [])]
    env["session"].add_all([*songs, emade])
    env["session"].commit()
    env["matcher"].songs += songs
    env["matcher"].artists += [fisz, emade]
    _request(env["inbox"], "2026-11-20_11-13-48_001.db",
             [{"title": "30 cm ponad chodnikami", "artist": "Fisz Emade"}])

    _, [case] = _cases(env)

    assert (case.status, case.existing and case.existing.id) == (
        (pr.OPEN, None) if emade_has_it else (sr.ALREADY_IN_DATABASE, 3))


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

    sr.mark_song_added(env["session"], case, "Elise Trouw", "Unseen (Live)", 4601)

    assert case.status == pr.DECIDED
    assert case.key not in pr.load_decisions()  # only once the song is committed
    env["session"].commit()
    decision = pr.load_decisions()[case.key]
    assert decision["decision"] == sr.ADD
    assert decision["added_as"] == {"artist": "Elise Trouw", "title": "Unseen (Live)", "song_id": 4601}
    # Leina Radio recognises the track it asked about as that song
    [row] = env["session"].query(SettledSongRequest).all()
    assert (row.artist, row.title, row.youtube_video_id, row.song_id) == ("Elise Trouw", "Unseen", None, 4601)


def test_a_song_found_in_the_database_is_recorded_and_settles_later_requests(env):
    _request(env["inbox"], "2026-10-18_10-00-00_001.db", [
        {"title": "Chromatic", "artist": "Elise Trouw", "youtube_video_id": "v9"},
        {"title": "CHROMATIC", "artist": "elise trouw", "spotify_track_id": "sp9", "source": "spotify"},
    ])
    _, [case] = _cases(env)
    env["session"].commit()

    assert case.status == sr.ALREADY_IN_DATABASE
    rows = env["session"].query(SettledSongRequest).order_by(SettledSongRequest.id).all()
    assert [(r.artist, r.title, r.youtube_video_id, r.spotify_track_id, r.song_id) for r in rows] == [
        ("Elise Trouw", "Chromatic", "v9", None, 1), ("elise trouw", "CHROMATIC", None, "sp9", 1)]

    # The same request again adds no row; names the matcher can't reconcile,
    # with that video or Spotify track, are the song too
    _request(env["inbox"], "2026-10-18_11-00-00_002.db", [
        {"title": "Chromatic", "artist": "Elise Trouw", "youtube_video_id": "v9"},
        {"title": "[1-01] Chromatic (Main Theme)", "artist": None, "youtube_video_id": "v9"},
        {"title": "Chromatic - 2020 Remaster", "artist": "Elise", "spotify_track_id": "sp9", "source": "spotify"},
        {"title": "Other", "artist": "Elise Trouw"},
    ])
    _, cases = _cases(env)
    env["session"].commit()

    assert [(c.status, c.existing.id if c.existing else None) for c in cases] == \
        [(sr.ALREADY_IN_DATABASE, 1)] * 3 + [(pr.OPEN, None)]
    assert env["session"].query(SettledSongRequest).count() == 4


def test_settled_requests_follow_merged_and_deleted_songs(env):
    session = env["session"]
    session.add_all([SettledSongRequest(title="a", song_id=1, settled_at="x"),
                     SettledSongRequest(title="b", song_id=2, settled_at="x")])
    session.flush()
    session.add_all([SongIdChange(old_song_id=1, new_song_id=7),
                     SongIdChange(old_song_id=2, new_song_id=None)])
    session.commit()

    assert [(r.title, r.song_id) for r in session.query(SettledSongRequest)] == [("a", 7)]


def test_the_backfill_records_requests_settled_before_the_table(env):
    session = env["session"]
    added = Song(id=3, title="Cave Story", artist=Artist(id=40, name="Daisuke Amaya"))
    session.add(added)
    session.commit()
    env["matcher"].songs.append(added)
    env["matcher"].artists.append(added.artist)
    path = _request(env["inbox"], "2026-10-18_10-00-00_001.db", [
        {"title": "Chromatic", "artist": "Elise Trouw"},  # in the database by its names
        {"title": "[1-01] Cave Story (Main Theme)", "artist": None, "source": "phone_file"},  # added as corrected
        {"title": "Вахтёрам Live", "artist": "Бумбокс"},  # added, the song id recorded
        {"title": "Unwanted", "artist": "Elise Trouw"},  # rejected
        {"title": "Skipped", "artist": "Elise Trouw"},  # never decided
    ], folder="processed")
    decisions = {
        "song:" + sr.song_key(None, "[1-01] Cave Story (Main Theme)"):
            {"decision": sr.ADD, "added_as": {"artist": "Daisuke Amaya", "title": "Cave Story"}},
        "song:" + sr.song_key("Бумбокс", "Вахтёрам Live"):
            {"decision": sr.ADD, "added_as": {"artist": "Бумбокс", "title": "Вахтёрам (Live)", "song_id": 2}},
        "song:" + sr.song_key("Elise Trouw", "Unwanted"): {"decision": sr.REJECT},
    }

    assert sr.backfill_settled_requests(session, [pr.read_request_file(path)], decisions, env["matcher"]) == 3
    session.commit()

    assert sorted((r.title, r.song_id) for r in session.query(SettledSongRequest)) == [
        ("Chromatic", 1), ("[1-01] Cave Story (Main Theme)", 3), ("Вахтёрам Live", 2)]


# ---- requested songs that get deleted ---------------------------------------------------------
# The real case (2026-11-24): two songs requests added on 2026-11-22, deleted by the owner
# with "Delete it anyway" - their settled rows went with them, and Leina Radio, which asks
# for a song only once, showed both tracks as outside the database for good.

NIZZA = ("Зе Інтерв'юер", "5'NIZZA. Анонс. Скоро", "D6Jc9-uyQCc")
CURE = ("Kawa i Awangarda", "The Cure", "GMmTW6I8vjI")
REAL_FILE = "shianman_2026-11-22_12-26-15_013.db"


def _logged_session(env):
    """A session whose deletions are logged in song_id_changes (firing the
    triggers), as music.db's."""
    from utils.database.song_references import register_song_reference_sync

    factory = sessionmaker(bind=env["session"].get_bind())
    register_song_reference_sync(factory)
    return factory()


def _added_on_2026_11_22(env, session) -> dict:
    """Songs #6969 / #6970 as the requests added them, their settled rows, the
    request file in processed/ and the answers in decisions.json; the songs."""
    ze, kawa = Artist(id=6001, name="Зе Інтерв'юер"), Artist(id=6002, name="Kawa i Awangarda")
    songs = {6969: Song(id=6969, title="5'NIZZA. Анонс", artist=ze, youtube_video_id=NIZZA[2]),
             6970: Song(id=6970, title="Muzyka na jesień (The Cure, Talk Talk, Foghorn)", artist=kawa,
                        youtube_video_id=CURE[2])}
    session.add_all(songs.values())
    decisions = {}
    for (artist, title, video), song in zip((NIZZA, CURE), songs.values()):
        session.add(SettledSongRequest(artist=artist, title=title, youtube_video_id=video, song_id=song.id,
                                       settled_at="2026-11-22T15:05:37+01:00"))
        decisions[f"song:{sr.song_key(artist, title)}"] = {
            "decision": sr.ADD, "artist": artist, "title": title, "decided_at": "2026-11-22T15:05:37+01:00",
            "files": [REAL_FILE], "added_as": {"artist": artist, "title": song.title, "song_id": song.id}}
    session.commit()
    _request(env["inbox"], REAL_FILE, [
        {"title": NIZZA[1], "artist": NIZZA[0], "youtube_video_id": NIZZA[2], "duration_s": 223,
         "requested_at": "2026-11-22T12:26:24+01:00"},
        {"title": CURE[1], "artist": CURE[0], "youtube_video_id": CURE[2], "duration_s": 523,
         "requested_at": "2026-11-22T12:26:30+01:00"},
        {"title": "Chromatic", "artist": "Elise Trouw"},  # settled as a song still there
    ], folder="processed")
    (env["inbox"] / pr.DECISIONS_FILE).write_text(json.dumps(decisions, ensure_ascii=False))
    return songs


def _rejected(session) -> list:
    return [(r.artist, r.title, r.youtube_video_id)
            for r in session.query(RejectedSongRequest).order_by(RejectedSongRequest.id)]


def test_deleting_a_requested_song_can_reject_its_requests(env):
    session = _logged_session(env)
    songs = _added_on_2026_11_22(env, session)

    for song_id in (6969, 6970):
        assert sr.reject_settled_requests(session, song_id) == 1
        session.delete(songs[song_id])
    session.commit()

    # The rows reject_song_request() writes, as Leina Radio named them; no settled row left
    assert _rejected(session) == [NIZZA, CURE]
    assert session.query(SettledSongRequest).count() == 0
    assert sorted((c.old_song_id, c.new_song_id) for c in session.query(SongIdChange)) == \
        [(6969, None), (6970, None)]
    decisions = pr.load_decisions()
    nizza = decisions[f"song:{sr.song_key(*NIZZA[:2])}"]
    assert (nizza["decision"], nizza["files"], nizza["deleted_song_id"]) == (sr.REJECT, [REAL_FILE], 6969)
    assert nizza["was_added_as"] == {"artist": NIZZA[0], "title": "5'NIZZA. Анонс", "song_id": 6969}
    # Nothing left to list as deleted, and the video asked for again is rejected before
    assert sr.deleted_song_requests(session) == []
    _request(env["inbox"], "2026-11-25_10-00-00_001.db",
             [{"title": "5'NIZZA. Анонс", "artist": "Зе Інтервʼюер", "youtube_video_id": NIZZA[2]}])
    _, [case] = _cases(env)
    assert case.status == sr.REJECTED_BEFORE
    session.close()


def test_requests_of_songs_deleted_earlier_are_found_and_can_be_rejected(env):
    session = _logged_session(env)
    songs = _added_on_2026_11_22(env, session)
    # Deleted plainly ("Delete it anyway"): the trigger dropped the settled rows
    for song in songs.values():
        session.delete(song)
    session.commit()
    assert session.query(SettledSongRequest).count() == 0
    # Not listed: one merged into a song still there, an older answer without the song id,
    # one rejected since
    decisions = pr.load_decisions()
    decisions["song:" + sr.song_key("Elise Trouw", "Chromatic (Live)")] = {
        "decision": sr.ADD, "added_as": {"artist": "Elise Trouw", "title": "Chromatic (Live)", "song_id": 77}}
    decisions["song:" + sr.song_key("Elise Trouw", "Old")] = {
        "decision": sr.ADD, "added_as": {"artist": "Elise Trouw", "title": "Old"}}
    decisions["song:" + sr.song_key("Elise Trouw", "Unwanted")] = {
        "decision": sr.ADD, "artist": "Elise Trouw", "title": "Unwanted", "added_as": {"song_id": 78}}
    session.add_all([SongIdChange(old_song_id=77, new_song_id=1), SongIdChange(old_song_id=78, new_song_id=None),
                     RejectedSongRequest(artist="Elise Trouw", title="Unwanted", rejected_at="x")])
    session.commit()

    items = sr.deleted_song_requests(session, decisions)

    assert [(i.song_id, i.case.latest.artist, i.case.latest.title, i.case.youtube_video_ids) for i in items] == [
        (6969, NIZZA[0], NIZZA[1], [NIZZA[2]]), (6970, CURE[0], CURE[1], [CURE[2]])]
    assert items[0].added_as["title"] == "5'NIZZA. Анонс"
    assert {r.file_name for i in items for r in i.case.reports} == {REAL_FILE}

    for item in items:
        sr.reject_deleted_song_request(session, item)
    session.commit()

    assert _rejected(session)[1:] == [NIZZA, CURE]
    assert pr.load_decisions()[items[1].case.key]["decision"] == sr.REJECT
    assert pr.load_decisions()[items[1].case.key]["files"] == [REAL_FILE]
    assert sr.deleted_song_requests(session) == []
    session.close()


def test_a_deleted_songs_request_without_its_file_is_listed_by_its_names(env):
    decisions = {"song:" + sr.song_key(*CURE[:2]): {
        "decision": sr.ADD, "artist": CURE[0], "title": CURE[1], "decided_at": "2026-11-22T15:05:37+01:00",
        "files": ["gone_2026-11-22_12-26-15_013.db"], "added_as": {"song_id": 6970}}}

    [item] = sr.deleted_song_requests(env["session"], decisions)

    assert (item.case.latest.artist, item.case.latest.title, item.case.youtube_video_ids) == (CURE[0], CURE[1], [])


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


def test_the_automatic_pass_adds_no_song_without_a_rule_of_the_owners(env):
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
    assert case.auto_note.startswith("adding a song waits for your approval")


@pytest.mark.parametrize("threshold, imports, added", [(0.9, True, True), (0.99, True, False),
                                                       (0.9, False, False)])
def test_a_rule_of_the_owners_adds_a_requested_song_unattended(env, monkeypatch, threshold, imports, added):
    from utils.common import automation_settings
    from utils.discoveries import import_engine
    from utils.ui import prompts

    automation_settings.save_user_rules([{
        "name": "MusicBrainz agrees", "question": "song_request", "on": True,
        "conditions": [{"fact": "musicbrainz_title_similarity", "op": ">=", "value": threshold},
                       {"fact": "artist_known", "op": "is", "value": True}]}])
    monkeypatch.setattr("utils.common.text_utils.check_spelling", lambda artist, title: {
        "found": True, "title_similarity": 0.95, "artist_similarity": 1.0, "mb_score": 100})
    monkeypatch.setattr(type(env["matcher"]), "best_guesses", lambda self, entry, limit, min_score: [],
                        raising=False)
    imported = []

    def fake_import(entries):
        assert prompts.unattended_now()  # nobody is asked during the automatic pass
        imported.extend((e.artist, e.title) for e in entries)
        for entry in entries:
            entry.song_id = 4700 if imports else None  # imports=False: the import left it out
    monkeypatch.setattr(import_engine, "run_import_batch", fake_import)
    _request(env["inbox"], "2026-10-18_10-00-00_001.db", [{"title": "Unseen", "artist": "Elise Trouw"}])

    result = ia.auto_resolve(env["session"], pr.read_new_requests())

    assert imported == ([("Elise Trouw", "Unseen")] if threshold == 0.9 else [])
    assert result.to_review == (0 if added else 1)
    if added:
        assert 'your rule "MusicBrainz agrees"' in result.changes[0]["text"]
        assert result.changes[0]["song_id"] == 4700
        assert pr.load_decisions()["song:" + sr.song_key("Elise Trouw", "Unseen")]["by"] == "auto"
    else:
        [request] = pr.read_review_requests()
        [case] = sr.collect_song_request_cases(env["session"], [request], matcher=env["matcher"])
        pr.load_case_checks([case], 24)
        assert ("no rule of yours" if imports else "the import left it out") in case.auto_note
    automation_settings.CONFIG_PATH.unlink()


def test_closest_song_of_artist_score_compares_only_the_artists_own_songs(env, monkeypatch):
    monkeypatch.setattr("utils.common.text_utils.check_spelling", lambda artist, title: {"found": False})
    monkeypatch.setattr(type(env["matcher"]), "best_guesses", lambda self, entry, limit, min_score: [],
                        raising=False)
    _request(env["inbox"], "2026-10-18_10-00-00_001.db", [{"title": "Chromatic II", "artist": "Elise Trouw"},
                                                         {"title": "Chromatic", "artist": "Okean Elzy"},
                                                         {"title": "Chromatic", "artist": "Nobody Known"}])
    _, cases = _cases(env)
    scores = {(c.latest.artist, c.latest.title): sr.song_request_facts(
        c, c.latest.artist, c.latest.title, {}, env["matcher"], False)["closest_song_of_artist_score"]
        for c in cases}

    # Voo Voo's own "Czas pomyka II" against a requested "czas pomyka" (2026-11-21)
    assert scores[("Elise Trouw", "Chromatic II")] > 0.85
    # Another artist's song of the same title doesn't count, nor an unknown artist
    assert scores[("Okean Elzy", "Chromatic")] == 0.0
    assert scores[("Nobody Known", "Chromatic")] == 0.0


def _musicbrainz_knows(monkeypatch, answers: dict) -> list:
    """check_spelling() answering `answers` ((artist, title) -> result,
    found True added), "found": False for any other pair; the pairs asked."""
    asked = []

    def check_spelling(artist, title):
        asked.append((artist, title))
        answer = answers.get((artist, title))
        return {"found": True, "mb_score": 100, **answer} if answer else {
            "found": False, "corrected_artist": artist, "corrected_title": title}
    monkeypatch.setattr("utils.common.text_utils.check_spelling", check_spelling)
    return asked


def _review_cases(env, rows):
    """The cases as the review shows them: each given MusicBrainz's suggestion
    right before it's shown (suggest_from_musicbrainz())."""
    _request(env["inbox"], "2026-11-25_10-00-00_001.db", rows)
    cases = sr.collect_song_request_cases(env["session"], pr.read_new_requests(), matcher=env["matcher"],
                                          fetch=False)
    lookups = sr.MusicBrainzLookups()
    for case in cases:
        sr.suggest_from_musicbrainz(case, env["matcher"], lookups)
    return cases


def test_musicbrainz_suggests_a_swap_of_artists_the_database_doesnt_know(env, monkeypatch):
    # 2026-11-25: "Moya - Godspeed You! Black Emperor" on cosmicguitars came swapped
    _musicbrainz_knows(monkeypatch, {("Godspeed You! Black Emperor", "Moya"): {
        "artist_similarity": 1.0, "title_similarity": 1.0,
        "mb_artist": "Godspeed You! Black Emperor", "mb_title": "Moya"}})

    [case] = _review_cases(env, [{"title": "Godspeed You! Black Emperor", "artist": "Moya",
                                  "youtube_video_id": "gybeMoya001"}])

    assert case.suggested == ("Godspeed You! Black Emperor", "Moya")
    assert case.suggested_from == sr.SUGGESTED_FROM_MUSICBRAINZ_SWAP
    assert case.pending  # a suggestion only, nothing settled by it


@pytest.mark.parametrize("cached_recording", [True, False])
def test_musicbrainz_suggests_the_artist_a_title_starts_with(env, monkeypatch, cached_recording):
    # 2026-11-25: "Behnam37" (a channel) / "Sylvie Vartan L'amour c'est comme
    # une cigarette"; check_spelling() found the title, nothing used its artist.
    # cached_recording False: an answer cached before it kept mb_artist
    title = "Sylvie Vartan L'amour c'est comme une cigarette"
    joined = {"artist_similarity": 0.2, "title_similarity": 0.82,
              "corrected_artist": "Behnam37", "corrected_title": "L’amour c’est comme une cigarette"}
    if cached_recording:
        joined |= {"mb_artist": "Sylvie Vartan", "mb_title": "L’amour c’est comme une cigarette"}
    asked = _musicbrainz_knows(monkeypatch, {
        ("Behnam37", title): joined,
        ("Sylvie Vartan", "L'amour c'est comme une cigarette"): {"artist_similarity": 1.0, "title_similarity": 1.0}})

    [case] = _review_cases(env, [{"title": title, "artist": "Behnam37"}])

    assert case.suggested == ("Sylvie Vartan", "L'amour c'est comme une cigarette")
    assert case.suggested_from == sr.SUGGESTED_FROM_MUSICBRAINZ_TITLE
    if cached_recording:  # no lookup but the request's own (and the swap's)
        assert ("Sylvie Vartan", "L'amour c'est comme une cigarette") not in asked


def test_musicbrainz_suggests_nothing_when_it_has_the_request_as_asked_or_only_unlike(env, monkeypatch):
    asked = _musicbrainz_knows(monkeypatch, {
        ("Still Corners", "The Trip"): {"artist_similarity": 1.0, "title_similarity": 1.0,
                                        "mb_artist": "Still Corners", "mb_title": "The Trip"},
        # Swapped, but only a similar name: no suggestion
        ("Some Long Title", "Nobody"): {"artist_similarity": 0.7, "title_similarity": 1.0}})

    cases = _review_cases(env, [{"title": "The Trip", "artist": "Still Corners"},
                                {"title": "Some Long Title", "artist": "Nobody"}])

    assert [c.suggested for c in cases] == [None, None]
    assert ("The Trip", "Still Corners") not in asked  # as asked: no swap asked about


def test_only_the_review_asks_musicbrainz_for_a_suggestion(env, monkeypatch):
    asked = _musicbrainz_knows(monkeypatch, {})
    _request(env["inbox"], "2026-11-25_10-00-00_001.db", [{"title": "Godspeed You! Black Emperor", "artist": "Moya"}])

    [case] = sr.collect_song_request_cases(env["session"], pr.read_new_requests(), matcher=env["matcher"], fetch=False)

    assert case.suggested is None and asked == []


def test_a_known_artist_gets_no_musicbrainz_suggestion(env, monkeypatch):
    asked = _musicbrainz_knows(monkeypatch, {})

    [case] = _review_cases(env, [{"title": "Okean Elzy Something", "artist": "Elise Trouw"}])

    assert case.suggested is None and asked == []


def test_musicbrainz_not_answering_leaves_its_facts_unknown(env, monkeypatch):
    # 2026-11-25: AURORA's "Forgotten Love" got musicbrainz_found False from
    # a 503, so "MusicBrainz agrees" couldn't hold; unknown says so instead
    from utils.common import automation_settings
    from utils.discoveries import import_engine
    from utils.common.text_utils import MUSICBRAINZ_UNREACHABLE

    automation_settings.save_user_rules([{
        "name": "Not on MusicBrainz", "question": "song_request", "on": True,
        "conditions": [{"fact": "musicbrainz_found", "op": "is", "value": False}]}])
    monkeypatch.setattr("utils.common.text_utils.check_spelling", lambda artist, title: {
        "found": None, "error": MUSICBRAINZ_UNREACHABLE, "corrected_artist": artist,
        "corrected_title": title, "corrected": False})
    monkeypatch.setattr(type(env["matcher"]), "best_guesses", lambda self, entry, limit, min_score: [],
                        raising=False)
    monkeypatch.setattr(import_engine, "run_import_batch", lambda entries: pytest.fail("imported"))
    _request(env["inbox"], "2026-11-25_10-00-00_001.db", [
        {"title": "Forgotten Love", "artist": "AURORA", "youtube_video_id": "TPUFd7cv9q8"}])

    result = ia.auto_resolve(env["session"], pr.read_new_requests())

    assert result.to_review == 1
    [case] = sr.collect_song_request_cases(env["session"], pr.read_review_requests(), matcher=env["matcher"])
    facts = sr.song_request_facts(case, "AURORA", "Forgotten Love", {}, env["matcher"], False)
    assert [facts[f] for f in ("musicbrainz_found", "musicbrainz_title_similarity",
                               "musicbrainz_artist_similarity", "musicbrainz_score")] == [None] * 4
    pr.load_case_checks([case], 24)
    assert "MusicBrainz couldn't be reached" in case.auto_note
    automation_settings.CONFIG_PATH.unlink()


def test_a_title_all_in_lower_case_is_added_as_musicbrainz_spells_it(env, monkeypatch):
    from utils.common import automation_settings
    from utils.discoveries import import_engine

    automation_settings.save_user_rules([{
        "name": "MusicBrainz agrees", "question": "song_request", "on": True,
        "conditions": [{"fact": "musicbrainz_title_similarity", "op": ">=", "value": 0.9},
                       {"fact": "closest_song_of_artist_score", "op": "<=", "value": 0.84}]}])
    # "Ócean Eyes": an accent apart, MusicBrainz still agrees on the title as
    # stored; "Ocean Eyes (Remix)" would not (see the "(∆) Breezeblocks" test)
    spellings = {"unseen": "Unseen", "ocean eyes": "Ócean Eyes"}
    monkeypatch.setattr("utils.common.text_utils.check_spelling", lambda artist, title: {
        "found": True, "title_similarity": 1.0, "artist_similarity": 1.0, "mb_score": 100,
        "corrected_title": spellings.get(title, title)})
    monkeypatch.setattr(type(env["matcher"]), "best_guesses", lambda self, entry, limit, min_score: [],
                        raising=False)
    imported = []

    def fake_import(entries):
        imported.extend((e.artist, e.title) for e in entries)
        for entry in entries:
            entry.song_id = 4700
    monkeypatch.setattr(import_engine, "run_import_batch", fake_import)
    _request(env["inbox"], "2026-10-18_10-00-00_001.db", [{"title": "unseen", "artist": "Elise Trouw"},
                                                         {"title": "ocean eyes", "artist": "Elise Trouw"},
                                                         {"title": "Chromatic II", "artist": "Elise Trouw"}])

    result = ia.auto_resolve(env["session"], pr.read_new_requests())

    # Only the case differs: MusicBrainz's spelling; more than that (an accent): as asked.
    # "Chromatic II" is too like the artist's own "Chromatic": left for review.
    assert sorted(imported) == [("Elise Trouw", "Unseen"), ("Elise Trouw", "ocean eyes")]
    assert result.to_review == 1
    automation_settings.CONFIG_PATH.unlink()


@pytest.mark.parametrize("migration, table, insert, stamp", [
    ("0015_rejected_song_requests.sql", "rejected_song_requests", "(title) VALUES ('t')", "rejected_at"),
    ("0024_settled_song_requests.sql", "settled_song_requests", "(title, song_id) VALUES ('t', 1)", "settled_at"),
])
def test_migration_matches_the_model(tmp_path, migration, table, insert, stamp):
    """migrations/music/0015 and 0024 give the same columns as create_all()."""
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    if table == "settled_song_requests":  # its trigger is on song_id_changes (0009)
        conn.executescript((repo_root / "migrations" / "music" / "0009_song_id_changes.sql").read_text())
    conn.executescript((repo_root / "migrations" / "music" / migration).read_text())
    migrated = {row[1]: (row[2], bool(row[3])) for row in conn.execute(f"PRAGMA table_info({table})")}
    conn.execute(f"INSERT INTO {table} {insert}")
    assert conn.execute(f"SELECT {stamp} FROM {table}").fetchone()[0]
    conn.close()

    engine = create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    Base.metadata.create_all(engine)
    modelled = {c["name"]: (str(c["type"]), not c["nullable"])
                for c in inspect(engine).get_columns(table)}
    assert {name: nullable for name, (_, nullable) in migrated.items() if name != "id"} == \
        {name: nullable for name, (_, nullable) in modelled.items() if name != "id"}


# ==================== "Preuzeti (2018.05)", 2026-11-22 ====================

@pytest.mark.parametrize("artist, title, song_id", [
    ("The Paper Kites", "Bloom lyrics", 3758),
    ("Pokahontaz ft. Bob One", "Czarne lustra prod. White House", 3742),
    ("dylan.pl", "dylan.pl: Adam dał imiona zwierzętom (Bob Dylan's 'Man Gave Names to All the Animals')", 6971),
    ("Pezet", "Pezet: Szósty zmysł", 3800),
])
def test_a_title_with_request_junk_is_found_in_the_database(env, artist, title, song_id):
    session = env["session"]
    kites, pokahontaz, dylan, pezet = (Artist(name=n) for n in ("The Paper Kites", "Pokahontaz", "dylan.pl", "Pezet"))
    session.add_all([Song(id=3758, title="Bloom", artist=kites), Song(id=3742, title="Czarne lustra", artist=pokahontaz),
                     Song(id=6971, title="Adam dał imiona zwierzętom", artist=dylan),
                     Song(id=3800, title="Szósty zmysł", artist=pezet)])
    session.commit()
    _request(env["inbox"], "2026-11-22_12-26-15_013.db", [{"title": title, "artist": artist}])

    [case] = sr.collect_song_request_cases(session, pr.read_new_requests(),
                                           matcher=RealLocalSongMatcher(session), fetch=False)

    assert case.status == sr.ALREADY_IN_DATABASE and case.existing.id == song_id


@pytest.mark.parametrize("title, expected", [
    ("Bloom lyrics", "Bloom"),
    ("Czarne lustra prod. White House", "Czarne lustra"),
    ("Sam (prod. Gibbs)", "Sam"),
    ("Adam dał imiona zwierzętom (Bob Dylan's 'Man Gave Names to All the Animals')", "Adam dał imiona zwierzętom"),
    # Left alone: a version, a word that only starts like "prod", nothing left
    ("Song (Live)", "Song (Live)"),
    ("Prodigy Rising", "Prodigy Rising"),
    ("Lyrics", "Lyrics"),
])
def test_title_without_request_junk(title, expected):
    assert sr.title_without_request_junk(title, "Someone") == expected


def _song_rule(env, monkeypatch, spellings):
    """The owner's "MusicBrainz agrees" rule, check_spelling() answering
    `spellings` ((artist, title) -> its result), and a fake import; the
    (artist, title) pairs imported."""
    from utils.common import automation_settings
    from utils.discoveries import import_engine

    automation_settings.save_user_rules([{
        "name": "MusicBrainz agrees", "question": "song_request", "on": True,
        "conditions": [{"fact": "musicbrainz_title_similarity", "op": ">=", "value": 0.99},
                       {"fact": "musicbrainz_artist_similarity", "op": ">=", "value": 0.99},
                       {"fact": "closest_song_of_artist_score", "op": "<=", "value": 0.84}]}])
    monkeypatch.setattr("utils.common.text_utils.check_spelling", lambda artist, title: {
        "found": True, "mb_score": 100, **spellings[(artist, title)]})
    monkeypatch.setattr(type(env["matcher"]), "best_guesses", lambda self, entry, limit, min_score: [],
                        raising=False)
    imported = []

    def fake_import(entries):
        imported.extend((e.artist, e.title) for e in entries)
        for entry in entries:
            entry.song_id = 4700
    monkeypatch.setattr(import_engine, "run_import_batch", fake_import)
    return imported


def test_the_artist_is_added_as_musicbrainz_spells_it_with_the_title(env, monkeypatch):
    # "nina simone" was added as a new artist in lower case, the title as MusicBrainz spells it
    from utils.common import automation_settings
    imported = _song_rule(env, monkeypatch, {
        ("nina simone", "my baby just cares for me"): {
            "title_similarity": 1.0, "artist_similarity": 1.0,
            "corrected_artist": "Nina Simone", "corrected_title": "My Baby Just Cares for Me"},
        ("elise trouw", "unseen"): {"title_similarity": 1.0, "artist_similarity": 1.0,
                                    "corrected_artist": "Elise Trouw", "corrected_title": "Unseen"},
        ("alt-J", "Breezeblocks"): {"title_similarity": 1.0, "artist_similarity": 1.0,
                                    "corrected_artist": "alt‐J", "corrected_title": "Breezeblocks"}})
    _request(env["inbox"], "2026-11-22_12-26-15_013.db", [
        {"title": "my baby just cares for me", "artist": "nina simone", "youtube_video_id": "eYSbUOoq4Vg"},
        {"title": "unseen", "artist": "elise trouw"},
        {"title": "Breezeblocks", "artist": "alt-J"}])

    ia.auto_resolve(env["session"], pr.read_new_requests())

    assert sorted(imported) == [("Elise Trouw", "Unseen"),  # as the database spells it
                                ("Nina Simone", "My Baby Just Cares for Me"),
                                ("alt-J", "Breezeblocks")]  # not MusicBrainz's Unicode hyphen
    automation_settings.CONFIG_PATH.unlink()


def test_a_title_musicbrainz_has_without_its_symbols_waits_for_the_owner(env, monkeypatch):
    # "(∆) Breezeblocks" rated 1.0 against MusicBrainz's "Breezeblocks"
    # (normalized forms) and was added with the "(∆)"
    from utils.common import automation_settings
    imported = _song_rule(env, monkeypatch, {
        ("alt-J", "(∆) Breezeblocks"): {"title_similarity": 1.0, "artist_similarity": 1.0,
                                        "corrected_artist": "alt‐J", "corrected_title": "Breezeblocks"}})
    _request(env["inbox"], "2026-11-22_12-26-15_013.db", [
        {"title": "(∆) Breezeblocks", "artist": "alt-J", "youtube_video_id": "rVeMiVU77wo"}])

    result = ia.auto_resolve(env["session"], pr.read_new_requests())

    assert imported == [] and result.to_review == 1
    [case] = sr.collect_song_request_cases(env["session"], pr.read_review_requests(), matcher=env["matcher"])
    facts = sr.song_request_facts(case, "alt-J", "(∆) Breezeblocks", {}, env["matcher"], False)
    assert facts["musicbrainz_title_similarity"] < 0.9
    automation_settings.CONFIG_PATH.unlink()


def test_the_automatic_pass_keeps_a_file_whose_other_songs_it_added_for_the_one_left(env, monkeypatch):
    # The pass added 4 of shianman_2026-11-25_13-00-00_014.db's songs; the
    # file went on to review/ for the rest, as it must
    from utils.common import automation_settings
    from utils.discoveries import import_engine

    automation_settings.save_user_rules([{
        "name": "MusicBrainz agrees", "question": "song_request", "on": True,
        "conditions": [{"fact": "musicbrainz_title_similarity", "op": ">=", "value": 0.99}]}])
    monkeypatch.setattr("utils.common.text_utils.check_spelling", lambda artist, title: {
        "found": True, "title_similarity": 0.5 if title == "Opposites" else 1.0, "artist_similarity": 1.0,
        "mb_score": 100})
    monkeypatch.setattr(type(env["matcher"]), "best_guesses", lambda self, entry, limit, min_score: [],
                        raising=False)

    def fake_import(entries):
        for i, entry in enumerate(entries):
            entry.song_id = 4700 + i
    monkeypatch.setattr(import_engine, "run_import_batch", fake_import)
    _request(env["inbox"], "shianman_2026-11-25_13-00-00_014.db",
             [{"title": "Plesem sama", "artist": "Detour"}, {"title": "Opposites", "artist": "Mandalay"},
              {"title": "Ništa", "artist": "Sassja"}])

    result = ia.auto_resolve(env["session"], pr.read_new_requests())

    assert len([c for c in result.changes if c["kind"] == "song_request"]) == 2
    assert result.to_review == 1
    assert _names(env["inbox"], "review") == ["shianman_2026-11-25_13-00-00_014.db"]
    assert _names(env["inbox"], "processed") == []
    automation_settings.CONFIG_PATH.unlink()


# ==================== 2026-11-26 review round ====================

def _reject(env, name, rows, when="2026-11-25T18:41:04+01:00"):
    requests = [pr.read_request_file(_request(env["inbox"], name, rows, folder="valid"))]
    cases = sr.collect_song_request_cases(env["session"], requests, matcher=env["matcher"], fetch=False)
    for case in cases:
        sr.reject_song_request(env["session"], case)
    env["session"].commit()
    pr.finish_request_files(requests, cases)
    decisions = json.loads((env["inbox"] / pr.DECISIONS_FILE).read_text())
    for case in cases:
        decisions[case.key]["decided_at"] = when
    (env["inbox"] / pr.DECISIONS_FILE).write_text(json.dumps(decisions))
    return cases


def test_a_rejection_reopened_is_asked_about_again(env):
    # The owner's ids 10-12 and the Mik4000 mix, rejected on 2026-11-25/26 by
    # mistake (mixes are added since): undone from the menu
    _reject(env, "shianman_2026-11-23_17-16-56_213.db",
            [{"title": "Masayoshi Takanaka mix", "artist": "babotokki", "youtube_video_id": "ldzbvjLbz3Y"}])
    _reject(env, "shianman_2026-11-23_18-19-37_214.db",
            [{"title": "Cigarettes & Materia", "artist": "RRR", "youtube_video_id": "rIm8UvzdDHk"}],
            when="2026-11-25T18:41:10+01:00")
    session = env["session"]
    # A row no decision names (written by hand, say) is listed on its own
    session.add(RejectedSongRequest(artist="Nobody", title="Lone Row", youtube_video_id="lone0000001",
                                    rejected_at="2026-11-01T10:00:00+01:00"))
    session.commit()

    items = sr.rejected_requests(session)

    assert [(i.artist, i.title, i.youtube_video_ids) for i in items] == [
        ("RRR", "Cigarettes & Materia", ["rIm8UvzdDHk"]),
        ("babotokki", "Masayoshi Takanaka mix", ["ldzbvjLbz3Y"]),
        ("Nobody", "Lone Row", ["lone0000001"])]
    assert items[0].rejected_at.startswith("2026-11-25T18:41:10")

    moved = sr.reopen_rejected_requests(session, [items[1], items[2]], today="2026-11-26")

    assert moved == ["shianman_2026-11-23_17-16-56_213.db"]
    assert [(r.artist, r.title) for r in session.query(RejectedSongRequest)] == [("RRR", "Cigarettes & Materia")]
    decisions = pr.load_decisions()
    assert "song:babotokki|masayoshi takanaka mix" not in decisions
    assert decisions["song:rrr|cigarettes materia"]["decision"] == sr.REJECT
    reopened = json.loads((env["inbox"] / "decisions_reopened_2026-11-26.json").read_text())
    assert reopened["song:babotokki|masayoshi takanaka mix"]["decision"] == sr.REJECT
    assert reopened["song:babotokki|masayoshi takanaka mix"]["reopened_at"]
    assert _names(env["inbox"], "review") == ["shianman_2026-11-23_17-16-56_213.db"]
    assert _names(env["inbox"], "processed") == ["shianman_2026-11-23_18-19-37_214.db"]

    # The next review asks about it; nothing settles it as rejected before -
    # nor a later request of its video the automatic pass reads
    [case] = sr.collect_song_request_cases(session, pr.read_review_requests(), matcher=env["matcher"], fetch=False)
    assert case.pending
    _request(env["inbox"], "2026-11-26_12-00-00_300.db",
             [{"title": "takanaka mix", "artist": "babotokki", "youtube_video_id": "ldzbvjLbz3Y"}])
    result = ia.auto_resolve(session, pr.read_new_requests())
    assert result.to_review == 1 and _names(env["inbox"], "review") == [
        "2026-11-26_12-00-00_300.db", "shianman_2026-11-23_17-16-56_213.db"]


def test_full_album_offer_for_a_request_as_long_as_a_mix(env):
    _request(env["inbox"], "2026-11-26_10-00-00_001.db", [
        {"title": "WINDOWS XP", "artist": "Mik4000", "duration_s": 3600},
        {"title": "Masayoshi Takanaka mix", "artist": "babotokki"},  # no length: offered, not first
        {"title": "Lofi Remix", "artist": "babotokki"},
        {"title": "Short Mix", "artist": "babotokki", "duration_s": 240},
        {"title": "Unseen", "artist": "Elise Trouw"},
    ])
    _, cases = _cases(env)

    assert [sr.full_album_offer(c) for c in cases] == [True, None, False, False, False]
    metadata = cases[1].metadata(full_album=True)
    assert metadata["full_album"] is True and metadata.title == "Masayoshi Takanaka mix"
    assert "full_album" not in cases[1].metadata()


def _mb_artists(monkeypatch, known: set) -> list:
    from utils.common import musicbrainz_artists
    asked = []
    monkeypatch.setattr(musicbrainz_artists, "artist_on_musicbrainz", lambda name: asked.append(name) or name in known)
    monkeypatch.setattr(musicbrainz_artists, "cached_answer", lambda name: None)
    return asked


def test_a_work_title_with_a_colon_under_real_artists_is_not_split(env, monkeypatch):
    # 2026-11-26: "Sandfall Interactive and Lorien Testard" / "Clair Obscur:
    # Expedition 33" was suggested as "Clair Obscur - Expedition 33" ("the
    # title starts with the artist"); MusicBrainz's Clair Obscur is another band
    asked = _musicbrainz_knows(monkeypatch, {("Clair Obscur", "Expedition 33"): {
        "artist_similarity": 1.0, "title_similarity": 0.3, "mb_artist": "Clair Obscur", "mb_title": "Lumière"}})
    _mb_artists(monkeypatch, set())

    [case] = _review_cases(env, [{"title": "Clair Obscur: Expedition 33",
                                  "artist": "Sandfall Interactive and Lorien Testard"}])

    assert case.suggested is None
    assert ("Sandfall Interactive and Lorien Testard", "Clair Obscur: Expedition 33") in asked
    # A label's or a channel's name still has the colon read: "Travie McCoy: Billionaire"
    assert sr.suggested_artist_title("Fueled By Ramen", "Travie McCoy: Billionaire", env["matcher"]) == (
        "Travie McCoy", "Billionaire")


def test_a_colon_split_under_real_artists_needs_musicbrainz_to_have_the_recording(env, monkeypatch):
    _musicbrainz_knows(monkeypatch, {("Lorien Testardo", "Lumière"): {
        "artist_similarity": 1.0, "title_similarity": 1.0}})
    _mb_artists(monkeypatch, set())

    [case] = _review_cases(env, [{"title": "Lorien Testardo: Lumière", "artist": "Sandfall Interactive and Friends"}])

    assert case.suggested == ("Lorien Testardo", "Lumière")
    assert case.suggested_from == sr.SUGGESTED_FROM_MUSICBRAINZ_TITLE


def test_a_dash_prefix_and_a_label_suffix_dont_make_a_suggestion(env, monkeypatch):
    # 2026-11-26: "Ella Baila Sola" / "- Eslabon Armado y Peso Pluma - DEL
    # Records 2023" was suggested as artist "- Eslabon Armado y Peso Pluma",
    # title "DEL Records 2023"; it is the two artists' "Ella Baila Sola"
    session = env["session"]
    request = {"title": "- Eslabon Armado y Peso Pluma - DEL Records 2023", "artist": "Ella Baila Sola"}
    assert sr.without_label_parts(request["title"]) == "Eslabon Armado y Peso Pluma"
    assert sr.without_label_parts("APT.") == "APT."

    # music.db knowing neither: MusicBrainz has the swap
    _musicbrainz_knows(monkeypatch, {("Eslabon Armado y Peso Pluma", "Ella Baila Sola"): {
        "artist_similarity": 0.93, "title_similarity": 1.0}})
    _mb_artists(monkeypatch, set())
    [case] = _review_cases(env, [request])
    assert case.suggested == ("Eslabon Armado y Peso Pluma", "Ella Baila Sola")
    assert case.suggested_from == sr.SUGGESTED_FROM_MUSICBRAINZ_SWAP

    # Knowing both artists, the swap comes from music.db, joined by "&"
    for artist_id, name in ((5080, "Eslabon Armado"), (5081, "Peso Pluma")):
        artist = Artist(id=artist_id, name=name)
        session.add(artist)
        env["matcher"].artists.append(artist)
    session.commit()
    assert sr.suggested_artist_title(request["artist"], request["title"], env["matcher"]) == (
        "Eslabon Armado & Peso Pluma", "Ella Baila Sola")


@pytest.mark.parametrize("channel, base", [
    ("PomplamooseMusic", "Pomplamoose"), ("OfficialClutch", "Clutch"), ("Clutch Official", "Clutch"),
    ("Official Clutch", "Clutch"), ("AdeleVEVO", "Adele"), ("Pomplamoose Music", "Pomplamoose"),
    ("Clutch", None), ("Musician", None), ("Officially", None), ("Music", None), ("TV", None)])
def test_channel_base_name(channel, base):
    assert sr.channel_base_name(channel) == base


def test_a_known_artists_channel_with_an_affix_suggests_the_artist(env, monkeypatch):
    # 2026-11-26: "PomplamooseMusic" and "OfficialClutch" weren't recognised
    session = env["session"]
    for artist_id, name in ((2963, "Pomplamoose"), (5075, "Clutch")):
        artist = Artist(id=artist_id, name=name)
        session.add(artist)
        env["matcher"].artists.append(artist)
    session.commit()

    assert sr.suggested_artist_title("PomplamooseMusic", "Mister Sandman", env["matcher"]) == (
        "Pomplamoose", "Mister Sandman")
    assert sr.suggested_artist_title("OfficialClutch", "Electric Worry", env["matcher"]) == ("Clutch", "Electric Worry")
    # A channel of no known artist: only MusicBrainz (the review) may tell
    asked = _mb_artists(monkeypatch, {"Vulfpeck"})
    _musicbrainz_knows(monkeypatch, {})
    [case] = _review_cases(env, [{"title": "Dean Town", "artist": "VulfpeckMusic"}])
    assert case.suggested == ("Vulfpeck", "Dean Town")
    assert case.suggested_from == sr.SUGGESTED_FROM_MUSICBRAINZ_CHANNEL
    assert asked == ["Vulfpeck"]


def test_musicbrainz_lookups_are_capped_per_case_and_stop_when_it_doesnt_answer(env, monkeypatch):
    from utils.common.text_utils import MUSICBRAINZ_UNREACHABLE
    asked = []
    monkeypatch.setattr("utils.common.text_utils.check_spelling", lambda artist, title: asked.append(
        (artist, title)) or {"found": False, "corrected_artist": artist, "corrected_title": title})
    _mb_artists(monkeypatch, set())
    title = "One Two Three Four Five Six Seven Eight"

    cases = _review_cases(env, [{"title": title, "artist": "SomeChannel1"}])

    assert cases[0].suggested is None and len(asked) == sr.SONG_REQUEST_MUSICBRAINZ_MAX_LOOKUPS

    asked.clear()
    monkeypatch.setattr("utils.common.text_utils.check_spelling", lambda artist, title: asked.append(
        (artist, title)) or {"found": None, "error": MUSICBRAINZ_UNREACHABLE})
    lookups = sr.MusicBrainzLookups()
    for case in cases * 3:
        case.suggested = None
        sr.suggest_from_musicbrainz(case, env["matcher"], lookups)
    assert len(asked) == 1 and lookups.unreachable


def _rule_adds_everything(monkeypatch, env):
    from utils.common import automation_settings
    automation_settings.save_user_rules([{
        "name": "Everything", "question": "song_request", "on": True,
        "conditions": [{"fact": "requests", "op": ">=", "value": 1}]}])
    monkeypatch.setattr("utils.common.text_utils.check_spelling", lambda artist, title: {
        "found": True, "artist_similarity": 1.0, "title_similarity": 1.0, "mb_score": 100,
        "corrected_artist": artist, "corrected_title": title})
    monkeypatch.setattr(type(env["matcher"]), "best_guesses", lambda self, entry, limit, min_score: [],
                        raising=False)
    imported = []
    from utils.discoveries import import_engine

    def run_import_batch(entries):
        for entry in entries:
            entry.song_id = 7018 + len(imported)
            imported.append(entry)
    monkeypatch.setattr(import_engine, "run_import_batch", run_import_batch)
    return imported


def test_one_pass_doesnt_add_a_song_twice_under_a_bands_two_names(env, monkeypatch):
    # 2026-11-26 10:19: #7018 "Bajaga i Instruktori - Moji drugovi" and #7019
    # "Bajaga - Moji drugovi", both added by "MusicBrainz agrees" in one run
    from utils.common import automation_settings
    session = env["session"]
    for artist_id, name in ((4201, "Bajaga i Instruktori"), (4872, "Bajaga")):
        artist = Artist(id=artist_id, name=name)
        session.add(artist)
        env["matcher"].artists.append(artist)
    session.commit()
    imported = _rule_adds_everything(monkeypatch, env)
    _request(env["inbox"], "shianman_2026-11-25_23-02-17_249.db", [
        {"title": "Moji drugovi -", "artist": "Bajaga i Instruktori", "youtube_video_id": "lQTd3-YvBaY"}])
    _request(env["inbox"], "shianman_2026-11-25_23-02-17_250.db", [
        {"title": "Moji drugovi", "artist": "Bajaga", "youtube_video_id": "B9y7UX3wUow"}])

    result = ia.auto_resolve(session, pr.read_new_requests())

    assert [(e.artist, e.title) for e in imported] == [("Bajaga i Instruktori", "Moji drugovi")]
    assert result.to_review == 1 and _names(env["inbox"], "review") == ["shianman_2026-11-25_23-02-17_250.db"]
    [case] = sr.collect_song_request_cases(session, pr.read_review_requests(), matcher=env["matcher"], fetch=False)
    pr.load_case_checks([case], 24)
    assert "another request of this run" in case.auto_note
    automation_settings.CONFIG_PATH.unlink()


def test_the_pass_doesnt_add_a_title_an_artist_of_a_related_name_has(env, monkeypatch):
    from utils.common import automation_settings
    session = env["session"]
    band = Artist(id=4201, name="Bajaga i Instruktori")
    session.add_all([band, Song(id=7018, title="Moji drugovi", artist=band)])
    session.commit()
    env["matcher"].artists.append(band)
    imported = _rule_adds_everything(monkeypatch, env)
    _request(env["inbox"], "2026-11-26_11-00-00_001.db", [
        {"title": "Moji drugovi", "artist": "Bajaga", "youtube_video_id": "B9y7UX3wUow"},
        {"title": "Ruski voz", "artist": "Bajaga"}])

    result = ia.auto_resolve(session, pr.read_new_requests())

    assert [(e.artist, e.title) for e in imported] == [("Bajaga", "Ruski voz")]
    assert result.to_review == 1
    automation_settings.CONFIG_PATH.unlink()


def test_related_artist_names():
    assert sr.related_artist_names("Bajaga", "Bajaga i Instruktori")
    assert sr.related_artist_names("BAJAGA I INSTRUKTORI", "bajaga")
    assert not sr.related_artist_names("Bajaga", "Bajagan")
    assert not sr.related_artist_names("Air", "Air Supply")  # too short to tell
    assert not sr.related_artist_names("Bajaga", "Ruski voz")


def test_a_label_part_needs_its_year():
    assert sr.without_label_parts("Jimmy Eat World - Broken Records") == "Jimmy Eat World - Broken Records"
    assert sr.without_label_parts("Song - Fueled By Ramen Records (2008)") == "Song"
