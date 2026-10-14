"""Links the phone found for songs without one (utils/database/inbox_found_links.py)
and the videos found bad (music.db's bad_youtube_videos, utils/database/inbox_requests.py):
reading the found_youtube_links table, settling stale rows (song gone, link or
"N/A" now, only bad videos), the newest video first, answers, the automatic
pass's rule (our search agrees and it plays; none playing settles it), and a
video found bad turned down later - for every song, or per song - also as a
dead link's suggestion; the table's trigger moving per-song rows with a merge
and dropping them with a deletion. Temp-file SQLite; YouTube is faked."""

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import inbox_auto as ia
from utils.database import inbox_field_corrections as fc
from utils.database import inbox_found_links as fl
from utils.database import inbox_requests as pr
from config.constants import (
    BAD_VIDEO_REMOVED,
    BAD_VIDEO_REPLACED_DEAD_LINK,
    BAD_VIDEO_UNPLAYABLE,
    BAD_VIDEO_WRONG_FOR_SONG,
    BAD_VIDEO_REASONS,
)
from utils.database.datatables import Artist, BadYoutubeVideo, Base, Song, SongIdChange

REQUEST_SQL = """
CREATE TABLE request (format INTEGER NOT NULL, created_at TEXT NOT NULL, user_name TEXT, app TEXT NOT NULL);
CREATE TABLE found_youtube_links (
  song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, video_id TEXT NOT NULL,
  video_title TEXT, video_channel TEXT, found_at TEXT NOT NULL, PRIMARY KEY (song_id, video_id));
CREATE TABLE dead_youtube_links (
  song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, dead_video_id TEXT NOT NULL,
  detected_at TEXT NOT NULL, replacement_video_id TEXT, replacement_title TEXT, replacement_channel TEXT,
  replacement_found_at TEXT);
"""
WHEN = "2026-10-14T09:00:00+02:00"


@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox = tmp_path / "Inbox"
    (inbox / "new").mkdir(parents=True)
    monkeypatch.setattr(pr, "INBOX_DIR", inbox)
    monkeypatch.setattr(pr, "inbox_available", lambda: True)
    changes = []
    monkeypatch.setattr(pr, "load_changes", lambda: list(changes))
    monkeypatch.setattr(fc, "load_changes", lambda: list(changes))

    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    artist = Artist(id=10, name="Elise Trouw")
    songs = [Song(id=1, title="Chromatic", artist=artist, youtube_video_id=None),
             Song(id=2, title="Losing Sleep", artist=artist, youtube_video_id=""),
             Song(id=3, title="Obsession", artist=artist, youtube_video_id="dead3")]
    session.add_all(songs)
    session.commit()
    return {"inbox": inbox, "session": session, "songs": songs, "changes": changes}


def _request(inbox: Path, name: str, found=(), dead=(), folder: str = "new") -> Path:
    """found: (song_id, title, video_id[, found_at]); dead: (song_id, title, dead id, replacement)."""
    path = inbox / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(REQUEST_SQL)
        conn.execute("INSERT INTO request VALUES (1, ?, NULL, 'Leina Radio')", (WHEN,))
        for row in found:
            song_id, title, video_id, *at = row
            conn.execute("INSERT INTO found_youtube_links VALUES (?, ?, 'Elise Trouw', ?, 'vt', 'vc', ?)",
                         (song_id, title, video_id, at[0] if at else WHEN))
        for row in dead:
            conn.execute("INSERT INTO dead_youtube_links VALUES (?, ?, 'Elise Trouw', ?, ?, ?, 't', 'c', NULL)",
                         (row[0], row[1], row[2], WHEN, row[3]))
    conn.close()
    return path


def _cases(env, folder="new"):
    requests = pr.read_new_requests() if folder == "new" else pr.read_review_requests()
    return requests, fl.collect_found_link_cases(env["session"], requests)


def _names(inbox: Path, folder: str) -> list:
    directory = inbox / folder
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def _bad(env) -> dict:
    """{"gone": {video: reason}, "wrong": {song id: {video: reason}}}"""
    bad = {"gone": {}, "wrong": {}}
    for row in env["session"].query(BadYoutubeVideo):
        if row.song_id is None:
            bad["gone"][row.video_id] = row.reason
        else:
            bad["wrong"].setdefault(row.song_id, {})[row.video_id] = row.reason
    return bad


def _mark(env, ids, reason, song_id=None):
    pr.mark_bad_videos(env["session"], ids, reason, song_id)
    env["session"].commit()


# ==================== reading, cases ====================

def test_reads_the_table_and_finishes_the_file_only_when_settled(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(1, "Chromatic", "vid1")])

    requests, [case] = _cases(env)

    [report] = requests[0].found_links
    assert (report.song_id, report.video_id, report.video_title, report.video_channel) == (1, "vid1", "vt", "vc")
    assert requests[0].unknown_tables == []
    assert case.status == fl.OPEN and case.dead_video_id is None and case.key == "found:1"
    moved, left = pr.finish_request_files(requests, [case])
    assert moved == [] and len(left) == 1


def test_settles_stale_rows(env):
    env["songs"][2].youtube_video_id = "N/A"
    env["session"].commit()
    env["changes"].append((1, 99, None))  # deleted song
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[
        (1, "Chromatic", "v1"), (2, "Losing Sleep", "v2"), (3, "Obsession", "v3"), (99, "Gone", "v9")])
    env["songs"][0].youtube_video_id = "fixed"
    env["session"].commit()

    requests, cases = _cases(env)

    assert {(c.song_id, c.status) for c in cases} == {
        (1, pr.LINK_ALREADY_CHANGED), (2, fl.OPEN), (3, pr.LINK_ALREADY_CHANGED), (None, pr.SONG_DELETED)}
    moved, _ = pr.finish_request_files(requests, cases)
    assert moved == []  # song 2 is still open


def test_several_videos_newest_first_and_merges_followed(env):
    env["changes"].append((1, 500, 1))
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(500, "Chromatic (live)", "old", "2026-10-13T10:00:00+02:00")])
    _request(env["inbox"], "2026-10-14_10-00-00_002.db", found=[(1, "Chromatic", "new", "2026-10-14T10:00:00+02:00")])

    _, [case] = _cases(env)

    assert case.song_id == 1 and case.title_differs
    assert [r.video_id for r in case.replacements] == ["new", "old"]


# ==================== videos found bad ====================

def test_a_video_found_bad_is_turned_down_without_youtube(env):
    _mark(env, ["gone1"], BAD_VIDEO_REMOVED)
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(1, "Chromatic", "gone1")])

    requests, [case] = _cases(env)

    assert case.status == fl.ONLY_BAD_VIDEOS and case.bad == {"gone1": BAD_VIDEO_REASONS[BAD_VIDEO_REMOVED]}
    moved, _ = pr.finish_request_files(requests, [case])
    assert len(moved) == 1


def test_wrong_for_a_song_is_only_for_that_song_and_follows_merges(env):
    s = env["session"]
    s.add(Song(id=500, title="Chromatic (live)", artist_id=10))
    s.commit()
    _mark(env, ["wrong1", "both"], BAD_VIDEO_WRONG_FOR_SONG, song_id=500)
    _mark(env, ["both"], BAD_VIDEO_WRONG_FOR_SONG, song_id=1)
    s.add(SongIdChange(old_song_id=500, new_song_id=1))  # merged into song 1
    s.commit()
    assert _bad(env)["wrong"] == {1: {"wrong1": BAD_VIDEO_WRONG_FOR_SONG, "both": BAD_VIDEO_WRONG_FOR_SONG}}
    _request(env["inbox"], "2026-10-14_10-00-00_001.db",
             found=[(1, "Chromatic", "wrong1"), (2, "Losing Sleep", "wrong1")])

    _, cases = _cases(env)

    assert {c.song_id: c.status for c in cases} == {1: fl.ONLY_BAD_VIDEOS, 2: fl.OPEN}


def test_only_the_bad_one_of_several_is_left_out(env):
    _mark(env, ["gone1"], BAD_VIDEO_REMOVED)
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(1, "Chromatic", "gone1"), (1, "Chromatic", "ok1")])

    _, [case] = _cases(env)

    assert case.status == fl.OPEN
    assert [r.video_id for r in case.replacements] == ["ok1"]
    assert [r.video_id for r in case.bad_replacements] == ["gone1"]


# ==================== answers ====================

def test_using_a_video_sets_the_link_and_clears_it_from_the_bad_list(env):
    _mark(env, ["v1"], BAD_VIDEO_UNPLAYABLE)
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(1, "Chromatic", "v1")])
    requests, [case] = _cases(env)

    fl.apply_found_link_decision(case, pr.USE_VIDEO, "v1")
    env["session"].commit()

    assert env["songs"][0].youtube_video_id == "v1"
    assert _bad(env)["gone"] == {}
    assert pr.load_decisions()["found:1:v1"]["decision"] == pr.USE_VIDEO
    moved, _ = pr.finish_request_files(requests, [case])
    assert len(moved) == 1


def test_rejecting_marks_the_videos_wrong_for_the_song(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(1, "Chromatic", "v1")])
    _, [case] = _cases(env)

    fl.apply_found_link_decision(case, fl.REJECTED)

    assert env["songs"][0].youtube_video_id is None
    env["session"].commit()
    assert _bad(env)["wrong"] == {1: {"v1": BAD_VIDEO_WRONG_FOR_SONG}}
    # Another report of it (any phone) is turned down; the same video for another song isn't
    _request(env["inbox"], "2026-10-15_10-00-00_001.db", found=[(1, "Chromatic", "v1"), (2, "Losing Sleep", "v1")])
    _, cases = _cases(env)
    assert {c.song_id: c.status for c in cases} == {1: fl.ONLY_BAD_VIDEOS, 2: fl.OPEN}


def test_no_video_sets_the_sentinel(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(1, "Chromatic", "v1")])
    _, [case] = _cases(env)

    fl.apply_found_link_decision(case, pr.NO_VIDEO)
    env["session"].commit()

    assert env["songs"][0].youtube_video_id == "N/A"
    assert "v1" in _bad(env)["wrong"][1]


def test_dead_link_answers_update_the_bad_list(env):
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", dead=[(3, "Obsession", "dead3", "new3")])
    [case] = pr.collect_dead_link_cases(env["session"], pr.read_new_requests())

    pr.apply_dead_link_decision(case, pr.USE_VIDEO, "new3")
    env["session"].commit()
    assert _bad(env)["gone"] == {"dead3": BAD_VIDEO_REPLACED_DEAD_LINK}

    pr.apply_dead_link_decision(case, pr.KEEP)
    env["session"].commit()
    assert "dead3" not in _bad(env)["gone"]


def test_a_dead_links_bad_suggestion_is_left_out(env):
    _mark(env, ["bad3"], BAD_VIDEO_REMOVED)
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", dead=[(3, "Obsession", "dead3", "bad3")])

    [case] = pr.collect_dead_link_cases(env["session"], pr.read_new_requests())

    assert case.status == pr.OPEN and case.replacements == [] and list(case.bad) == ["bad3"]
    case.checked_at = pr.now_iso()
    assert ia.link_verdict(case) == (None, "Leina Radio suggested only videos found bad before")


# ==================== checking with YouTube ====================

@pytest.fixture
def youtube(monkeypatch):
    from utils.youtube import manage_youtube_playlists as m
    state = {"found": {}, "api": {}, "ytdlp": {}, "api_calls": []}
    monkeypatch.setattr(m, "search_video_ytdlp", lambda artist, title, **k: state["found"].get(title))

    def api(ids):
        ids = list(ids)
        state["api_calls"].append(ids)
        return {v: state["api"].get(v, True) for v in ids}

    monkeypatch.setattr(m, "videos_exist_via_api", api)
    monkeypatch.setattr(m, "video_playability", lambda video_id, timeout=20: state["ytdlp"].get(video_id, True))
    return state


def test_check_remembers_what_is_gone_and_never_offers_a_bad_video_as_ours(env, youtube):
    _mark(env, ["wrong2"], BAD_VIDEO_WRONG_FOR_SONG, song_id=2)
    _request(env["inbox"], "2026-10-14_10-00-00_001.db",
             found=[(1, "Chromatic", "gone1"), (2, "Losing Sleep", "noplay2")])
    youtube["found"] = {"Chromatic": "ours1", "Losing Sleep": "wrong2"}
    youtube["api"] = {"gone1": False}
    youtube["ytdlp"] = {"noplay2": False}
    _, cases = _cases(env)

    stats = pr.check_link_cases(cases)

    assert set(youtube["api_calls"][0]) == {"gone1", "ours1", "noplay2"}  # no dead link to ask about
    assert [c.our_video_id for c in cases] == ["ours1", None]
    # Committed by the check itself
    env["session"].rollback()
    assert _bad(env)["gone"] == {"gone1": BAD_VIDEO_REMOVED, "noplay2": BAD_VIDEO_UNPLAYABLE}
    assert stats["marked"] == 2


# ==================== the automatic pass ====================

@pytest.fixture
def auto(env, monkeypatch):
    """Our search finds youtube[song id]; plays[video id] (default True)."""
    youtube = {"ours": {}, "plays": {}}

    def fake_check(cases, on_case=None):
        for case in cases:
            case.our_video_id = youtube["ours"].get(case.song_id)
            ids = [r.video_id for r in case.replacements] + ([case.our_video_id] if case.our_video_id else [])
            case.plays = {v: youtube["plays"].get(v, True) for v in ids}
            case.checked_at = pr.now_iso()
        return {"api": True, "blocked": 0}
    monkeypatch.setattr(ia, "check_link_cases", fake_check)
    return youtube


def _run(env):
    return ia.auto_resolve(env["session"], pr.read_new_requests())


def test_auto_saves_a_link_our_search_agrees_with(env, auto):
    auto["ours"][1] = "v1"
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(1, "Chromatic", "v1")])

    result = _run(env)

    assert env["songs"][0].youtube_video_id == "v1"
    [change] = result.changes
    assert change["kind"] == "found_link" and "-> v1" in change["text"]
    assert pr.load_decisions()["found:1:v1"]["by"] == "auto"
    assert _names(env["inbox"], "processed") == ["2026-10-14_10-00-00_001.db"]


def test_auto_leaves_a_disagreement_for_review_with_a_note(env, auto):
    auto["ours"][1] = "ours1"
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(1, "Chromatic", "v1")])

    result = _run(env)

    assert env["songs"][0].youtube_video_id is None and result.to_review == 1
    assert _names(env["inbox"], "review") == ["2026-10-14_10-00-00_001.db"]
    _, [case] = _cases(env, "review")
    pr.load_case_checks([case], max_age_hours=24)
    assert "different videos" in case.auto_note and case.our_video_id == "ours1"


def test_auto_settles_a_link_that_does_not_play(env, auto):
    auto["ours"][1] = "v1"
    auto["plays"]["v1"] = False
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", found=[(1, "Chromatic", "v1")])

    result = _run(env)

    assert env["songs"][0].youtube_video_id is None and result.changes == [] and result.to_review == 0
    assert _names(env["inbox"], "processed") == ["2026-10-14_10-00-00_001.db"]


def test_auto_dead_link_fixed_first_settles_a_found_link_for_the_same_song(env, auto):
    # A cleared link with both a dead report and a found link
    env["songs"][2].youtube_video_id = None
    env["session"].commit()
    auto["ours"][3] = "new3"
    _request(env["inbox"], "2026-10-14_10-00-00_001.db",
             dead=[(3, "Obsession", "dead3", "new3")], found=[(3, "Obsession", "other3")])

    result = _run(env)

    assert env["songs"][2].youtube_video_id == "new3" and len(result.changes) == 1
    assert _names(env["inbox"], "processed") == ["2026-10-14_10-00-00_001.db"]


def test_a_deleted_song_takes_its_wrong_videos_along(env):
    _mark(env, ["v1"], BAD_VIDEO_WRONG_FOR_SONG, song_id=1)
    _mark(env, ["g1"], BAD_VIDEO_REMOVED)
    env["session"].add(SongIdChange(old_song_id=1, new_song_id=None))
    env["session"].commit()

    assert _bad(env) == {"gone": {"g1": BAD_VIDEO_REMOVED}, "wrong": {}}


def test_marking_twice_keeps_one_row(env):
    _mark(env, ["g1"], BAD_VIDEO_REMOVED)
    _mark(env, ["g1"], BAD_VIDEO_UNPLAYABLE)
    _mark(env, ["g1"], BAD_VIDEO_WRONG_FOR_SONG, song_id=2)

    assert env["session"].query(BadYoutubeVideo).count() == 2
    assert _bad(env)["gone"] == {"g1": BAD_VIDEO_REMOVED}


def test_migration_matches_the_model(tmp_path):
    """migrations/music/0010 gives the same table, index and trigger as create_all()."""
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    conn.executescript((repo_root / "migrations" / "music" / "0009_song_id_changes.sql").read_text())
    conn.executescript((repo_root / "migrations" / "music" / "0010_bad_youtube_videos.sql").read_text())
    conn.execute("INSERT INTO bad_youtube_videos (video_id, song_id, reason) VALUES ('v', 5, 'wrong_for_song')")
    conn.execute("INSERT INTO song_id_changes (old_song_id, new_song_id) VALUES (5, 6)")
    assert conn.execute("SELECT song_id FROM bad_youtube_videos").fetchall() == [(6,)]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO bad_youtube_videos (video_id, song_id, reason) VALUES ('v', 6, 'x')")
    conn.execute("INSERT INTO bad_youtube_videos (video_id, reason) VALUES ('g', 'removed')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO bad_youtube_videos (video_id, reason) VALUES ('g', 'removed')")
    conn.close()
