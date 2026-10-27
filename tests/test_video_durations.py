"""music.db's youtube_video_durations (migration 0017,
utils/database/video_durations.py): lengths stored by video id, the YouTube
API asked only about the rest, what came before an API error kept, a live
stream's 0 never stored. Leina Radio reads the table. Temp-file SQLite, the
API faked."""

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import video_durations as vd
from utils.database.datatables import Base, YoutubeVideoDuration


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _stored(session):
    return dict(session.query(YoutubeVideoDuration.video_id, YoutubeVideoDuration.duration_s))


def test_only_unknown_videos_reach_the_api_and_are_stored(session, monkeypatch):
    vd.store_video_durations(session, {"known000000": 2700})
    asked = []

    def api(ids, on_found=None):
        asked.append(list(ids))
        found = {"new00000000": 3723, "live0000000": 0}
        on_found(found)
        return found
    monkeypatch.setattr(vd, "video_durations_via_api", api)

    result = vd.video_durations(session, ["known000000", "new00000000", "live0000000", "gone0000000", "N/A", None])

    assert result == {"known000000": 2700, "new00000000": 3723, "live0000000": 0}
    assert asked == [["new00000000", "live0000000", "gone0000000"]]
    # The live stream's 0 and the video YouTube didn't list are asked again next time
    assert _stored(session) == {"known000000": 2700, "new00000000": 3723}


def test_all_known_needs_no_api(session, monkeypatch):
    vd.store_video_durations(session, {"known000000": 2700})
    monkeypatch.setattr(vd, "video_durations_via_api", lambda *a, **k: pytest.fail("API asked"))

    assert vd.video_durations(session, ["known000000", "known000000"]) == {"known000000": 2700}


def test_no_api_with_unknown_videos_is_none(session, monkeypatch):
    monkeypatch.setattr(vd, "video_durations_via_api", lambda ids, on_found=None: None)
    assert vd.video_durations(session, ["new00000000"]) is None


def test_what_came_before_an_api_error_is_kept(session, monkeypatch):
    def api(ids, on_found=None):
        on_found({"first000000": 1500})
        return None  # the next batch failed
    monkeypatch.setattr(vd, "video_durations_via_api", api)

    assert vd.video_durations(session, ["first000000", "later000000"]) is None
    assert _stored(session) == {"first000000": 1500}


def test_a_length_is_replaced_not_duplicated(session):
    vd.store_video_durations(session, {"video000000": 100})
    vd.store_video_durations(session, {"video000000": 120})
    assert _stored(session) == {"video000000": 120}


def test_many_videos_are_read_in_chunks(session):
    many = {f"v{i:010d}": i + 1 for i in range(1200)}
    vd.store_video_durations(session, many)
    assert vd.stored_video_durations(session, list(many)) == many


def test_the_schema_refuses_a_zero_length(session):
    session.add(YoutubeVideoDuration(video_id="live0000000", duration_s=0))
    with pytest.raises(IntegrityError):
        session.commit()


def test_migration_matches_the_model(tmp_path):
    """migrations/music/0017 gives the same columns as create_all()."""
    conn = sqlite3.connect(tmp_path / "m.db")
    conn.executescript((repo_root / "migrations" / "music" / "0017_youtube_video_durations.sql").read_text())
    migrated = {row[1]: (bool(row[3]), bool(row[5])) for row in conn.execute("PRAGMA table_info(youtube_video_durations)")}
    conn.execute("INSERT INTO youtube_video_durations (video_id, duration_s) VALUES ('v', 60)")
    assert conn.execute("SELECT fetched_at FROM youtube_video_durations").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO youtube_video_durations (video_id, duration_s) VALUES ('w', 0)")
    conn.close()

    engine = create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    Base.metadata.create_all(engine)
    modelled = {c["name"]: (not c["nullable"], bool(c["primary_key"]))
                for c in inspect(engine).get_columns("youtube_video_durations")}
    assert migrated == modelled
