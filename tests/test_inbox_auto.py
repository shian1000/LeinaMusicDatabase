"""The automatic pass over new requests (utils/database/inbox_auto.py) and what
it relies on: a dead link replaced only when Leina Radio's suggestion and our
search agree and the video plays; a field correction saved only from a user
with the apply_corrections permission and when nothing conflicts; the rest
moved to review/ with a note and its YouTube check (reloaded while fresh);
the report main.py shows; the app lock (utils/database/app_lock.py) and
has_pending_migrations(). Temp-file SQLite; YouTube is faked."""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import albums as al
from utils.database import app_lock
from utils.database import inbox_auto as ia
from utils.database import inbox_field_corrections as fc
from utils.database import inbox_requests as pr
from utils.database import migrations
from utils.database.datatables import Artist, Base, Song

REQUEST_SQL = """
CREATE TABLE request (format INTEGER NOT NULL, created_at TEXT NOT NULL, user_name TEXT, app TEXT NOT NULL);
CREATE TABLE dead_youtube_links (
  song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, dead_video_id TEXT NOT NULL,
  detected_at TEXT NOT NULL, replacement_video_id TEXT, replacement_title TEXT, replacement_channel TEXT,
  replacement_found_at TEXT);
CREATE TABLE field_corrections (
  table_name TEXT NOT NULL, row_id INTEGER NOT NULL, column_name TEXT NOT NULL, old_value, new_value,
  song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, corrected_at TEXT NOT NULL);
"""
WHEN = "2026-10-14T09:00:00+02:00"


@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox = tmp_path / "Inbox"
    (inbox / "new").mkdir(parents=True)
    monkeypatch.setattr(pr, "INBOX_DIR", inbox)
    monkeypatch.setattr(pr, "inbox_available", lambda: True)
    monkeypatch.setattr(pr, "load_changes", lambda: [])
    monkeypatch.setattr(fc, "load_changes", lambda: [])

    users = tmp_path / "Users"
    for name, permissions in [("ania", ["apply_corrections"]), ("bob", ["edit_global_moods"])]:
        (users / name).mkdir(parents=True)
        (users / name / "account.json").write_text(json.dumps({"permissions": permissions}))
    monkeypatch.setattr(ia.Settings, "users_dir", users)

    # YouTube: our search finds `ours[dead id]`; `plays[video id]` (default True)
    youtube = {"ours": {}, "plays": {}, "calls": 0}

    def fake_check(cases, on_case=None):
        youtube["calls"] += 1
        for case in cases:
            case.our_video_id = youtube["ours"].get(case.dead_video_id or case.song_id)
            ids = [r.video_id for r in case.replacements] + ([case.our_video_id] if case.our_video_id else [])
            case.plays = {v: youtube["plays"].get(v, True) for v in ids}
            case.checked_at = pr.now_iso()
        return {"api": True, "blocked": 0}
    monkeypatch.setattr(ia, "check_link_cases", fake_check)

    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    al.register_album_sync(factory)
    session = factory()
    artist = Artist(id=10, name="Elise Trouw")
    session.add_all([artist, Artist(id=11, name="Vulfpeck")])
    session.commit()
    songs = [Song(id=1, title="Chromatic", artist=artist, youtube_video_id="dead1", year=2019),
             Song(id=2, title="Losing Sleep", artist=artist, youtube_video_id="dead2", year=2019)]
    session.add_all(songs)
    session.commit()
    return {"inbox": inbox, "session": session, "songs": songs, "youtube": youtube}


def _request(inbox: Path, name: str, links=(), corrections=(), folder: str = "new") -> Path:
    path = inbox / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(REQUEST_SQL)
        conn.execute("INSERT INTO request VALUES (1, ?, NULL, 'Leina Radio')", (WHEN,))
        for row in links:
            conn.execute("INSERT INTO dead_youtube_links VALUES (?, ?, 'Elise Trouw', ?, ?, ?, 't', 'c', NULL)",
                         (row[0], row[1], row[2], WHEN, row[3]))
        for row in corrections:
            conn.execute("INSERT INTO field_corrections VALUES (?, ?, ?, ?, ?, ?, ?, 'Elise Trouw', ?)", (*row, WHEN))
    conn.close()
    return path


def _year(song_id=1, title="Chromatic", old=2019, new=2020):
    return ("songs", song_id, "year", old, new, song_id, title)


def _run(env):
    return ia.auto_resolve(env["session"], pr.read_new_requests())


def _names(inbox: Path, folder: str) -> list:
    directory = inbox / folder
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


# ==================== dead links ====================

def test_dead_link_replaced_when_both_searches_agree_and_it_plays(env):
    env["youtube"]["ours"]["dead1"] = "good1"
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", links=[(1, "Chromatic", "dead1", "good1")])

    result = _run(env)

    assert env["songs"][0].youtube_video_id == "good1"
    assert result.to_review == 0 and _names(env["inbox"], "processed") == ["2026-10-14_10-00-00_001.db"]
    [change] = result.changes
    assert "dead1 -> good1" in change["text"] and change["song_id"] == 1
    assert pr.load_decisions()["1:dead1"]["by"] == "auto"
    # The next start shows it once
    assert [e["text"] for e in pr.auto_report(unshown_only=True)] == [change["text"]]
    pr.mark_auto_report_shown()
    assert pr.auto_report(unshown_only=True) == [] and len(pr.auto_report()) == 1


@pytest.mark.parametrize("ours, suggested, plays, note", [
    ("ours1", "good1", True, "found different videos"),
    (None, "good1", True, "our search found nothing"),
    ("ours1", None, True, "suggested no video, only our search found one"),
    ("good1", "good1", None, "couldn't check whether the video plays"),
    ("good1", "good1", False, "doesn't play here"),
])
def test_dead_link_left_for_review_with_a_note(env, ours, suggested, plays, note):
    env["youtube"]["ours"]["dead1"] = ours
    env["youtube"]["plays"]["good1"] = plays
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", links=[(1, "Chromatic", "dead1", suggested)])

    result = _run(env)

    assert env["songs"][0].youtube_video_id == "dead1" and result.changes == []
    assert _names(env["inbox"], "review") == ["2026-10-14_10-00-00_001.db"] and _names(env["inbox"], "new") == []
    # The review gets the note and the check back without asking YouTube again
    [case] = pr.collect_dead_link_cases(env["session"], pr.read_review_requests())
    pr.load_case_checks([case], max_age_hours=24)
    assert note in case.auto_note
    assert case.checked_at and case.our_video_id == ours


def test_an_old_check_is_not_reused(env):
    env["youtube"]["ours"]["dead1"] = "ours1"
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", links=[(1, "Chromatic", "dead1", "good1")])
    _run(env)
    checks = json.loads((env["inbox"] / pr.CHECKS_FILE).read_text())
    checks["1:dead1"]["checked_at"] = (datetime.now().astimezone() - timedelta(hours=25)).isoformat()
    (env["inbox"] / pr.CHECKS_FILE).write_text(json.dumps(checks))

    [case] = pr.collect_dead_link_cases(env["session"], pr.read_review_requests())
    pr.load_case_checks([case], max_age_hours=24)

    assert case.checked_at is None and case.our_video_id is None
    assert "different videos" in case.auto_note  # the note stays


def test_settled_reports_need_no_youtube(env):
    env["songs"][0].youtube_video_id = "fixed"
    env["session"].commit()
    _request(env["inbox"], "2026-10-14_10-00-00_001.db", links=[(1, "Chromatic", "dead1", "good1")])

    result = _run(env)

    assert env["youtube"]["calls"] == 0 and result.changes == []
    assert _names(env["inbox"], "processed") == ["2026-10-14_10-00-00_001.db"]


# ==================== field corrections ====================

def test_correction_from_a_permitted_user_is_saved(env):
    _request(env["inbox"], "ania_2026-10-14_10-00-00_001.db", corrections=[_year()])

    result = _run(env)

    assert env["songs"][0].year == 2020
    [change] = result.changes
    assert "year 2019 -> 2020 (corrected by ania)" in change["text"]
    assert pr.load_decisions()["field:songs:1:year"]["by"] == "auto"
    assert _names(env["inbox"], "processed") == ["ania_2026-10-14_10-00-00_001.db"]


@pytest.mark.parametrize("name, row, note", [
    ("bob_2026-10-14_10-00-00_001.db", _year(), "bob has no permission"),
    ("2026-10-14_10-00-00_001.db", _year(), "without logging in"),
    ("ania_2026-10-14_10-00-00_001.db", _year(old=2018), "changed since"),
    ("ania_2026-10-14_10-00-00_001.db", _year(new="soon"), "can't be stored"),
    ("ania_2026-10-14_10-00-00_001.db", _year(title="Chromatic (Live)"), "another title"),
    ("ania_2026-10-14_10-00-00_001.db", ("artists", 10, "name", "Elise Trouw", "Vulfpeck", 1, "Chromatic"),
     "another artist already has that name"),
])
def test_correction_left_for_review_with_a_note(env, name, row, note):
    _request(env["inbox"], name, corrections=[row])

    result = _run(env)

    assert result.changes == [] and env["songs"][0].year == 2019
    assert env["session"].get(Artist, 10).name == "Elise Trouw"
    assert _names(env["inbox"], "review") == [name]
    cases = fc.collect_field_correction_cases(env["session"], pr.read_review_requests())
    pr.load_case_checks(cases, max_age_hours=24)
    assert note in cases[0].auto_note


def test_conflicting_reports_are_left_for_review(env):
    _request(env["inbox"], "ania_2026-10-14_10-00-00_001.db", corrections=[_year(new=2020)])
    _request(env["inbox"], "ania_2026-10-14_10-00-00_002.db", corrections=[_year(new=2021)])

    result = _run(env)

    assert result.changes == [] and result.to_review == 1
    assert len(_names(env["inbox"], "review")) == 2


def test_a_file_waits_for_its_last_open_row(env):
    env["youtube"]["ours"]["dead1"] = "good1"
    _request(env["inbox"], "bob_2026-10-14_10-00-00_001.db",
             links=[(1, "Chromatic", "dead1", "good1")], corrections=[_year(song_id=2, title="Losing Sleep")])

    result = _run(env)

    assert env["songs"][0].youtube_video_id == "good1" and env["songs"][1].year == 2019
    assert _names(env["inbox"], "review") == ["bob_2026-10-14_10-00-00_001.db"]
    # In the review the replaced link is settled, only the correction is asked
    requests = pr.read_review_requests()
    assert [c.status for c in pr.collect_dead_link_cases(env["session"], requests)] == [pr.LINK_ALREADY_CHANGED]
    assert result.to_review == 1


# ==================== files ====================

def test_unreadable_file_is_rejected(env):
    (env["inbox"] / "new" / "2026-10-14_10-00-00_001.db").write_bytes(b"SQLite format 3\x00garbage")

    result = _run(env)

    assert [r.name for r in result.rejected] == ["2026-10-14_10-00-00_001.db"]
    assert _names(env["inbox"], "rejected") == ["2026-10-14_10-00-00_001.db"]


def test_finishing_a_file_already_in_review_keeps_its_name(env):
    _request(env["inbox"], "bob_2026-10-14_10-00-00_001.db", corrections=[_year()], folder="review")
    requests = pr.read_review_requests()
    cases = fc.collect_field_correction_cases(env["session"], requests)

    pr.finish_request_files(requests, cases, left_to=pr.REVIEW)

    assert _names(env["inbox"], "review") == ["bob_2026-10-14_10-00-00_001.db"]


def test_count_and_sender(env):
    _request(env["inbox"], "ania_2026-10-14_10-00-00_001.db")
    _request(env["inbox"], "2026-10-14_10-00-00_002.db", folder="review")

    assert pr.count_new_requests() == 2
    assert [r.sender for r in pr.read_new_requests() + pr.read_review_requests()] == ["ania", None]


# ==================== app lock, migrations ====================

def test_pass_cant_lock_while_an_app_is_open(tmp_path, monkeypatch):
    monkeypatch.setattr(app_lock, "LOCK_FILE", tmp_path / ".musicdatabase.lock")
    monkeypatch.setattr(app_lock, "_held", [])

    first = app_lock.try_exclusive_lock()
    assert first is not None
    assert app_lock.try_exclusive_lock() is None  # one pass at a time
    first.close()

    app_lock.hold_app_lock()
    app_lock.hold_app_lock()  # two apps open at once is fine
    assert app_lock.try_exclusive_lock() is None
    for handle in app_lock._held:
        handle.close()
    assert app_lock.try_exclusive_lock() is not None


def test_has_pending_migrations(tmp_path, monkeypatch):
    (tmp_path / "migrations" / "music").mkdir(parents=True)
    (tmp_path / "migrations" / "music" / "0001_a.sql").write_text("SELECT 1;")
    db = tmp_path / "music.db"
    monkeypatch.setattr(migrations, "MIGRATIONS_ROOT", tmp_path / "migrations")
    monkeypatch.setattr(migrations, "DB_FILES", {"music": db})

    assert not migrations.has_pending_migrations("music")  # no database yet
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, filename TEXT, applied_at TEXT)")
    conn.commit()
    assert migrations.has_pending_migrations("music")
    conn.execute("INSERT INTO schema_version (filename) VALUES ('0001_a.sql')")
    conn.commit()
    conn.close()
    assert not migrations.has_pending_migrations("music")
