"""music.db's additional_youtube_videos (migration 0018,
utils/database/additional_videos.py): a song's YouTube videos other than its
own link, for Leina Radio to recognise a played video. What a playlist
compared with YouTube offers (own link for a song without one, else an
additional video, conflicts left out), merges keeping the dropped song's link,
a video found wrong for a song dropping out, and the folder tracks' sureness.
Temp-file SQLite."""

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config.constants import (ADDITIONAL_VIDEO_FOLDER_PLAYLIST, ADDITIONAL_VIDEO_SONG_MERGE,
                              BAD_VIDEO_WRONG_FOR_SONG, NO_VIDEO_SENTINEL)
from utils.database import additional_videos as av
from utils.database.datatables import AdditionalYoutubeVideo, Artist, Base, BadYoutubeVideo, Song
from utils.database.inbox_requests import mark_bad_videos
from utils.database.song_duplicates import merge_songs
from utils.database.song_references import register_song_reference_sync
from utils.discoveries.folder_playlist import FolderTrack
from utils.discoveries.folder_youtube_playlist import video_links


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    register_song_reference_sync(factory)  # logs deletions in song_id_changes
    s = factory()
    yield s
    s.close()


def _songs(session, *links):
    artist = Artist(name="Hey")
    songs = [Song(title=f"Song {i}", artist=artist, youtube_video_id=link) for i, link in enumerate(links)]
    session.add_all(songs)
    session.commit()
    return songs


def _rows(session):
    return {v: (s, src) for v, s, src in session.query(AdditionalYoutubeVideo.video_id, AdditionalYoutubeVideo.song_id,
                                                      AdditionalYoutubeVideo.source)}


def test_plan_gives_own_links_and_additional_videos(session):
    none, linked, no_video, other = _songs(session, None, "own", NO_VIDEO_SENTINEL, "theirs")
    session.add(BadYoutubeVideo(video_id="wrong", song_id=linked.id, reason=BAD_VIDEO_WRONG_FOR_SONG))
    session.commit()
    plan = av.plan_video_links(session, [
        (none.id, "a", True), (none.id, "b", False),  # the first becomes its own link
        (linked.id, "own", True),                     # already its own: left out silently
        (linked.id, "c", False),
        (no_video.id, "d", True),                     # "N/A" stays, the video is additional
        (linked.id, "theirs", True),                  # another song's link
        (linked.id, "wrong", True),                   # found wrong for this song
        (other.id, "c", True),                        # paired with two songs
        (None, "e", True),
    ])
    assert [(l.song.id, l.video_id, l.main, l.sure) for l in plan.links] == [
        (none.id, "a", True, True), (none.id, "b", False, False), (linked.id, "c", False, False),
        (no_video.id, "d", False, True)]
    assert [(s.id, v) for s, v, _ in plan.conflicts] == [(linked.id, "theirs"), (linked.id, "wrong"), (other.id, "c")]

    assert av.apply_video_links(session, plan.links, ADDITIONAL_VIDEO_FOLDER_PLAYLIST) == (1, 3)
    session.commit()
    assert (none.youtube_video_id, no_video.youtube_video_id) == ("a", NO_VIDEO_SENTINEL)
    assert _rows(session) == {v: (s, ADDITIONAL_VIDEO_FOLDER_PLAYLIST)
                              for v, s in (("b", none.id), ("c", linked.id), ("d", no_video.id))}
    # Saved once: a second look offers nothing
    assert av.plan_video_links(session, [(linked.id, "c", True)]).links == []


def test_a_songs_own_link_wins(session):
    first, second = _songs(session, "x", None)
    session.add(AdditionalYoutubeVideo(video_id="x", song_id=second.id, source=ADDITIONAL_VIDEO_FOLDER_PLAYLIST))
    session.add(AdditionalYoutubeVideo(video_id="y", song_id=second.id, source=ADDITIONAL_VIDEO_FOLDER_PLAYLIST))
    session.commit()
    assert av.song_for_video(session, "x") is first
    assert av.song_for_video(session, "y") is second
    assert av.song_for_video(session, NO_VIDEO_SENTINEL) is None
    assert av.add_additional_video(session, second, "x", ADDITIONAL_VIDEO_FOLDER_PLAYLIST) is False
    assert av.additional_video_ids(session, second.id) == ["x", "y"]


def test_a_merge_keeps_the_dropped_songs_link_and_videos(session):
    kept, dropped = _songs(session, "kept-own", "dropped-own")
    session.add(AdditionalYoutubeVideo(video_id="extra", song_id=dropped.id, source=ADDITIONAL_VIDEO_FOLDER_PLAYLIST))
    session.commit()
    merge_songs(session, kept, dropped)
    session.commit()
    assert kept.youtube_video_id == "kept-own"
    assert _rows(session) == {"dropped-own": (kept.id, ADDITIONAL_VIDEO_SONG_MERGE),
                              "extra": (kept.id, ADDITIONAL_VIDEO_FOLDER_PLAYLIST)}


def test_a_deleted_songs_videos_go(session):
    song, = _songs(session, None)
    session.add(AdditionalYoutubeVideo(video_id="v", song_id=song.id, source=ADDITIONAL_VIDEO_FOLDER_PLAYLIST))
    session.commit()
    session.delete(song)
    session.commit()
    assert _rows(session) == {}


def test_a_video_found_wrong_for_the_song_stops_being_its(session):
    song, other = _songs(session, None, None)
    session.add_all([AdditionalYoutubeVideo(video_id=v, song_id=s.id, source=ADDITIONAL_VIDEO_FOLDER_PLAYLIST)
                     for v, s in (("v", song), ("w", other))])
    session.commit()
    mark_bad_videos(session, ["v", "w"], BAD_VIDEO_WRONG_FOR_SONG, song.id)
    session.commit()
    assert _rows(session) == {"w": (other.id, ADDITIONAL_VIDEO_FOLDER_PLAYLIST)}


def test_only_certain_pairings_are_sure():
    by_name = FolderTrack(file=Path("a.mp3"), title="A", artist="X", song_id=1, youtube_link="v1")
    by_hand = FolderTrack(file=Path("b.mp3"), title="B", artist="X", song_id=2, youtube_link="v2", link_sure=True)
    videos_song = FolderTrack(file=Path("c.mp3"), title="C", artist="X", song_id=3, youtube_link="v3", link_song_id=3)
    picked = FolderTrack(file=None, title="D", artist="X", song_id=4, youtube_link="v4", picked=True)
    no_song = FolderTrack(file=Path("e.mp3"), title="E", artist="X", youtube_link="v5")
    no_video = FolderTrack(file=Path("f.mp3"), title="F", artist="X", song_id=6)
    assert video_links([by_name, by_hand, videos_song, picked, no_song, no_video]) == [
        (1, "v1", False), (2, "v2", True), (3, "v3", True), (4, "v4", True)]


def test_migration_matches_the_model(tmp_path):
    """migrations/music/0018 gives the same table and trigger as create_all()."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE song_id_changes (id INTEGER PRIMARY KEY, old_song_id INTEGER NOT NULL, "
                 "new_song_id INTEGER, changed_at TEXT)")
    conn.executescript((repo_root / "migrations" / "music" / "0018_additional_youtube_videos.sql").read_text())
    migrated = {row[1]: (bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(additional_youtube_videos)")}
    conn.execute("INSERT INTO additional_youtube_videos (video_id, song_id, source) VALUES ('v', 1, 's')")
    conn.execute("INSERT INTO song_id_changes (old_song_id, new_song_id) VALUES (1, 2)")
    assert conn.execute("SELECT song_id, added_at IS NOT NULL FROM additional_youtube_videos").fetchone() == (2, 1)

    engine = create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    Base.metadata.create_all(engine)
    modelled = {c["name"]: (not c["nullable"], bool(c["primary_key"]))
                for c in inspect(engine).get_columns("additional_youtube_videos")}
    assert migrated == modelled
