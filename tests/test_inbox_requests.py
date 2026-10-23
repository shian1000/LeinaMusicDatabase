"""Requests from the phone (utils/database/inbox_requests.py): reading request
files from the inbox (format 1, unknown tables, broken files), settling dead
link reports that need no answer (song deleted, link already changed, link
kept earlier), following merges, grouping repeats, applying answers and
moving finished files to processed/. Temp-file SQLite; YouTube is never asked."""

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

from utils.database import inbox_requests as pr
from utils.database.datatables import Artist, Base, Song

REQUEST_SQL = """
CREATE TABLE request (format INTEGER NOT NULL, created_at TEXT NOT NULL, user_name TEXT, app TEXT NOT NULL);
CREATE TABLE dead_youtube_links (
  song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, dead_video_id TEXT NOT NULL,
  detected_at TEXT NOT NULL, replacement_video_id TEXT, replacement_title TEXT, replacement_channel TEXT,
  replacement_found_at TEXT, PRIMARY KEY (song_id, dead_video_id));
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox = tmp_path / "Inbox"
    (inbox / "new").mkdir(parents=True)
    monkeypatch.setattr(pr, "INBOX_DIR", inbox)
    monkeypatch.setattr(pr, "inbox_available", lambda: True)
    changes = []
    monkeypatch.setattr(pr, "load_changes", lambda: list(changes))

    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    artist = Artist(name="Elise Trouw")
    songs = [Song(id=4601 + i, title=t, artist=artist, youtube_video_id=f"dead{i}")
             for i, t in enumerate(["Chromatic", "Losing Sleep", "Obsession"])]
    session.add_all(songs)
    session.commit()
    return {"inbox": inbox, "session": session, "songs": songs, "changes": changes}


def _request(inbox: Path, name: str, rows, fmt: int = 1, extra_sql: str = "") -> Path:
    path = inbox / "new" / name
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(REQUEST_SQL + extra_sql)
        conn.execute("INSERT INTO request VALUES (?, '2026-10-12T23:44:05+02:00', NULL, 'Leina Radio')", (fmt,))
        for row in rows:
            row = {"detected_at": "2026-10-12T23:40:00+02:00", "replacement_video_id": None,
                   "replacement_title": None, "replacement_channel": None, **row}
            conn.execute(
                "INSERT INTO dead_youtube_links (song_id, title, artist, dead_video_id, detected_at, "
                "replacement_video_id, replacement_title, replacement_channel) VALUES "
                "(:song_id, :title, :artist, :dead_video_id, :detected_at, :replacement_video_id, "
                ":replacement_title, :replacement_channel)", row)
    conn.close()
    return path


def _row(song_id=4601, title="Chromatic", dead="dead0", replacement=None, **extra):
    return {"song_id": song_id, "title": title, "artist": "Elise Trouw", "dead_video_id": dead,
            "replacement_video_id": replacement, **extra}


def _cases(env):
    requests = pr.read_new_requests()
    return requests, pr.collect_dead_link_cases(env["session"], requests)


# ==================== reading ====================

def test_reads_a_request_file(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db",
             [_row(replacement="new0", replacement_title="Chromatic (Official)", replacement_channel="Elise Trouw")])
    (env["inbox"] / "new" / ".2026-10-12_23-44-05_002.db.part").write_bytes(b"half")

    [request] = pr.read_new_requests()

    assert request.supported and request.unknown_tables == [] and request.app == "Leina Radio"
    [report] = request.dead_links
    assert (report.song_id, report.dead_video_id, report.replacement_video_id) == (4601, "dead0", "new0")
    assert report.replacement_channel == "Elise Trouw"
    assert pr.count_new_requests() == 1


def test_unknown_table_is_read_but_noted(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row()], extra_sql="CREATE TABLE missing_years (x);")

    [request] = pr.read_new_requests()

    assert request.supported
    assert request.unknown_tables == ["missing_years"]
    assert len(request.dead_links) == 1


def test_android_metadata_table_is_not_unknown(env):
    # Android's SQLiteDatabase adds it to every file; it must not keep the file in the inbox
    _request(env["inbox"], "2026-10-13_00-08-40_001.db", [_row()],
             extra_sql="CREATE TABLE android_metadata (locale TEXT);")

    [request] = pr.read_new_requests()

    assert request.supported and request.unknown_tables == []


def test_newer_format_is_not_read(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row()], fmt=2)

    [request] = pr.read_new_requests()

    assert request.error is None and not request.supported and request.dead_links == []


@pytest.mark.parametrize("content", [b"SQLite format 3\x00" + b"\x00" * 100, b"garbage"])
def test_broken_file_gets_an_error_not_an_exception(env, content):
    (env["inbox"] / "new" / "2026-10-12_23-44-05_001.db").write_bytes(content)

    [request] = pr.read_new_requests()

    assert request.error
    target = pr.reject_request_file(request)
    assert target == env["inbox"] / "rejected" / "2026-10-12_23-44-05_001.db"
    assert pr.count_new_requests() == 0


def test_unmounted_inbox_has_no_requests(env, monkeypatch):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row()])
    monkeypatch.setattr(pr, "inbox_available", lambda: False)

    assert pr.count_new_requests() == 0


def test_no_sharing_folder_means_no_inbox_and_no_report(monkeypatch):
    # A fresh install without SHARING_DIR: main.py's startup reads both
    monkeypatch.setattr(pr, "INBOX_DIR", None)
    monkeypatch.setattr(pr, "INBOX_SHARING_DIR", None)

    assert not pr.inbox_available()
    assert pr.count_new_requests() == 0
    assert pr.auto_report(unshown_only=True) == []


# ==================== cases ====================

def test_settles_what_needs_no_answer(env):
    s = env["session"]
    songs = env["songs"]
    songs[1].youtube_video_id = "fixed1"
    songs[2].youtube_video_id = "N/A"
    s.commit()
    env["changes"].append((1, 9999, None))  # deleted song
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [
        _row(),
        _row(4602, "Losing Sleep", "dead1"),
        _row(4603, "Obsession", "dead2"),
        _row(9999, "Gone", "dead9"),
    ])

    _, cases = _cases(env)

    assert {(c.song_id, c.status) for c in cases} == {
        (4601, pr.OPEN), (4602, pr.LINK_ALREADY_CHANGED), (4603, pr.LINK_ALREADY_CHANGED), (None, pr.SONG_DELETED),
    }


def test_cleared_link_stays_open(env):
    env["songs"][0].youtube_video_id = None
    env["session"].commit()
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(replacement="new0")])

    [case] = _cases(env)[1]

    assert case.status == pr.OPEN


def test_follows_merges_and_flags_another_title(env):
    env["changes"].extend([(1, 500, 501), (2, 501, 4601)])
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(500, "Chromatic (live)")])

    [case] = _cases(env)[1]

    assert case.song_id == 4601 and case.status == pr.OPEN
    assert case.title_differs


def test_repeats_from_two_files_are_one_case(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(replacement="new0")])
    _request(env["inbox"], "2026-10-13_08-00-00_002.db", [_row(replacement="other0")])

    requests, [case] = _cases(env)

    assert len(case.reports) == 2
    assert [r.video_id for r in case.replacements] == ["new0", "other0"]


# ==================== answers ====================

def test_using_a_video_changes_the_link_and_finishes_the_file(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(replacement="new0")])
    requests, [case] = _cases(env)

    pr.apply_dead_link_decision(case, pr.USE_VIDEO, "new0")
    env["session"].commit()
    moved, left = pr.finish_request_files(requests, [case])

    assert env["songs"][0].youtube_video_id == "new0"
    assert [r.name for r in moved] == ["2026-10-12_23-44-05_001.db"] and left == []
    assert (env["inbox"] / "processed" / "2026-10-12_23-44-05_001.db").exists()
    assert pr.count_new_requests() == 0
    decision = json.loads((env["inbox"] / "decisions.json").read_text())["4601:dead0"]
    assert decision["decision"] == pr.USE_VIDEO and decision["video_id"] == "new0"


def test_no_video_sets_the_sentinel(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row()])
    _, [case] = _cases(env)

    pr.apply_dead_link_decision(case, pr.NO_VIDEO)

    assert env["songs"][0].youtube_video_id == "N/A"


def test_skipped_row_keeps_the_file_in_the_inbox(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(), _row(4602, "Losing Sleep", "dead1")])
    requests, cases = _cases(env)

    pr.apply_dead_link_decision(cases[0], pr.NO_VIDEO)
    moved, left = pr.finish_request_files(requests, cases)

    assert moved == [] and len(left) == 1
    assert pr.count_new_requests() == 1


def test_kept_link_is_not_asked_again_unless_reported_later(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(), _row(4602, "Losing Sleep", "dead1")])
    requests, cases = _cases(env)
    pr.apply_dead_link_decision(cases[0], pr.KEEP)
    env["session"].commit()
    pr.finish_request_files(requests, cases)  # file stays: second row unanswered

    _, cases = _cases(env)
    assert [c.status for c in cases] == [pr.ALREADY_KEPT, pr.OPEN]
    assert env["songs"][0].youtube_video_id == "dead0"

    # Another phone reports it again tomorrow: it may have died since
    _request(env["inbox"], "2099-01-01_00-00-00_001.db", [_row(detected_at="2099-01-01T00:00:00+00:00")])
    _, cases = _cases(env)
    assert [c.status for c in cases if c.song_id == 4601] == [pr.OPEN]


def test_file_with_an_unknown_table_stays_even_when_answered(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row()], extra_sql="CREATE TABLE missing_years (x);")
    requests, [case] = _cases(env)

    pr.apply_dead_link_decision(case, pr.NO_VIDEO)
    moved, left = pr.finish_request_files(requests, [case])

    assert moved == [] and len(left) == 1


def test_file_with_only_settled_reports_is_finished(env):
    env["songs"][0].youtube_video_id = "fixed0"
    env["session"].commit()
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row()])
    requests, cases = _cases(env)

    moved, _ = pr.finish_request_files(requests, cases)

    assert len(moved) == 1


# ==================== checking with YouTube ====================

@pytest.fixture
def youtube(monkeypatch):
    """Fake search / Data API / yt-dlp, recording what YouTube was asked."""
    from utils.youtube import manage_youtube_playlists as m
    state = {"found": {}, "api": {}, "ytdlp": {}, "ytdlp_calls": [], "api_calls": []}
    monkeypatch.setattr(m, "search_video_ytdlp", lambda artist, title, **k: state["found"].get(title))

    def api(ids):
        ids = list(ids)
        state["api_calls"].append(ids)
        return None if state["api"] is None else {v: state["api"].get(v, True) for v in ids}

    def ytdlp(video_id, timeout=20):
        state["ytdlp_calls"].append(video_id)
        return state["ytdlp"].get(video_id, True)

    monkeypatch.setattr(m, "videos_exist_via_api", api)
    monkeypatch.setattr(m, "video_playability", ytdlp)
    return state


def test_checks_ask_youtube_as_little_as_possible(env, youtube):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [
        _row(replacement="sugg0"),
        _row(4602, "Losing Sleep", "dead1", replacement="gone1"),
    ])
    youtube["found"] = {"Chromatic": "sugg0", "Losing Sleep": "ours1"}
    youtube["api"] = {"dead0": False, "gone1": False}
    _, cases = _cases(env)

    stats = pr.check_link_cases(cases)

    # One API call for every id; yt-dlp never for the dead links, an id the
    # API ruled out, or the same id twice
    assert len(youtube["api_calls"]) == 1
    assert set(youtube["api_calls"][0]) == {"dead0", "dead1", "sugg0", "gone1", "ours1"}
    assert sorted(youtube["ytdlp_calls"]) == ["ours1", "sugg0"]
    first, second = cases
    assert first.dead_still_plays is False and second.dead_still_plays is None
    assert first.plays == {"sugg0": True} and first.our_video_id == "sugg0"
    assert second.plays == {"gone1": False, "ours1": True}
    assert stats == {"api": True, "blocked": 0, "marked": 2}  # dead0 and gone1 gone


def test_blocked_ytdlp_falls_back_to_the_api_listing(env, youtube):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(replacement="sugg0")])
    youtube["ytdlp"] = {"sugg0": None}
    _, [case] = _cases(env)

    assert pr.check_link_cases([case]) == {"api": True, "blocked": 1, "marked": 0}
    assert case.plays == {"sugg0": True}


def test_without_the_api_a_blocked_check_stays_unknown(env, youtube):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(replacement="sugg0")])
    youtube["api"] = None
    youtube["ytdlp"] = {"sugg0": None}
    _, [case] = _cases(env)

    assert pr.check_link_cases([case]) == {"api": False, "blocked": 1, "marked": 0}
    assert case.plays == {"sugg0": None} and case.dead_still_plays is None


def test_ytdlp_saying_gone_beats_the_api_listing(env, youtube):
    # The API lists some videos YouTube won't play (UNPLAYABLE Topic uploads)
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(replacement="sugg0")])
    youtube["ytdlp"] = {"sugg0": False}
    _, [case] = _cases(env)

    pr.check_link_cases([case])

    assert case.plays == {"sugg0": False}


# ==================== unreadable state files are never overwritten ====================

@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_an_unreadable_decisions_file_is_never_overwritten(env, content):
    path = env["inbox"] / pr.DECISIONS_FILE
    path.write_text(content)

    assert pr.load_decisions() == {}  # read-only use carries on
    with pytest.raises(pr.InboxFileError):
        pr.save_decision("dead0", {"decision": "keep"})
    assert path.read_text() == content


def test_an_unreadable_report_is_never_overwritten(env):
    path = env["inbox"] / pr.REPORT_FILE
    path.write_text("[{\"text\": \"changed\", \"shown\": false}")  # cut short

    assert pr.auto_report() == []
    with pytest.raises(pr.InboxFileError):
        pr.add_auto_report([{"text": "another change"}])
    with pytest.raises(pr.InboxFileError):
        pr.mark_auto_report_shown()
    assert path.read_text().startswith("[{\"text\": \"changed\"")


def test_a_missing_decisions_file_is_started(env):
    pr.save_decision("dead0", {"decision": "keep"})
    assert pr.load_decisions() == {"dead0": {"decision": "keep"}}


# ==================== decisions follow the commit (DB-17) ====================

def test_a_decision_is_recorded_only_when_music_db_commits(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(replacement="new0")])
    _, [case] = _cases(env)

    pr.apply_dead_link_decision(case, pr.USE_VIDEO, "new0")
    assert pr.load_decisions() == {}  # not committed yet

    env["session"].commit()
    assert pr.load_decisions()["4601:dead0"]["decision"] == pr.USE_VIDEO


def test_a_rolled_back_decision_leaves_the_case_open(env):
    """Before: decisions.json said "use video" while music.db never got the
    link, and the next pass dropped the report as answered earlier."""
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(replacement="new0")])
    _, [case] = _cases(env)

    pr.apply_dead_link_decision(case, pr.USE_VIDEO, "new0")
    env["session"].rollback()
    env["session"].commit()  # a later, unrelated commit doesn't bring it back

    assert pr.load_decisions() == {}
    _, [case] = _cases(env)
    assert case.status == pr.OPEN
    assert env["songs"][0].youtube_video_id == "dead0"


def test_an_unreadable_decisions_file_stops_before_any_change(env):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(replacement="new0")])
    (env["inbox"] / "decisions.json").write_text("{broken")
    _, [case] = _cases(env)

    with pytest.raises(pr.InboxFileError):
        pr.apply_dead_link_decision(case, pr.USE_VIDEO, "new0")
    assert (env["inbox"] / "decisions.json").read_text() == "{broken"


# ==================== ids stored as text (DB-16) ====================

@pytest.fixture
def untyped_ids(monkeypatch):
    """A phone file whose song_id column has no type: SQLite then keeps
    "4601" as text (an INTEGER column would convert it)."""
    monkeypatch.setitem(globals(), "REQUEST_SQL", REQUEST_SQL.replace("song_id INTEGER NOT NULL", "song_id NOT NULL"))


def test_an_id_stored_as_text_is_read_as_its_number(env, untyped_ids):
    """"4601" as text used to match no song, and the report was closed as
    "song deleted"."""
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(song_id=" 4601")])

    requests, [case] = _cases(env)

    assert requests[0].error is None and requests[0].dead_links[0].song_id == 4601
    assert case.status == pr.OPEN and case.song is env["songs"][0]


def test_an_id_that_isnt_a_number_makes_the_file_unreadable(env, untyped_ids):
    _request(env["inbox"], "2026-10-12_23-44-05_001.db", [_row(song_id="Chromatic")])

    [request] = pr.read_new_requests()

    assert request.error == "dead_youtube_links.song_id 'Chromatic' isn't a whole number"
