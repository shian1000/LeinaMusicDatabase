"""Manage database -> "Resolve new requests" (utils/ui/inbox_request_review.py),
driven end to end through resolve_new_requests() with request files in the
inbox's review/ folder, a temp-file music.db and scripted answers: the start
menu (no inbox, nothing to do, the automatic pass's history), each part's
answers reaching music.db and decisions.json (dead links, links found for
songs without one, field corrections, songs to add), pasting a link, editing
a value, Skip keeping the file and Stop / Esc leaving the later parts unasked,
songs approved before a stop still imported in one batch, and unreadable
files moved to rejected/. YouTube and the import are faked."""

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import database_sessions
from utils.database import inbox_field_corrections as fc
from utils.database import inbox_requests as pr
from utils.database import inbox_song_requests as sr
from utils.database.datatables import Artist, Base, RejectedSongRequest, Song
from utils.discoveries import import_engine as ie
from utils.ui import inbox_request_review as review

REQUEST_SQL = """
CREATE TABLE request (format INTEGER NOT NULL, created_at TEXT NOT NULL, user_name TEXT, app TEXT NOT NULL);
CREATE TABLE dead_youtube_links (
  song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, dead_video_id TEXT NOT NULL,
  detected_at TEXT NOT NULL, replacement_video_id TEXT, replacement_title TEXT, replacement_channel TEXT,
  replacement_found_at TEXT);
CREATE TABLE found_youtube_links (
  song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, video_id TEXT NOT NULL,
  video_title TEXT, video_channel TEXT, found_at TEXT NOT NULL);
CREATE TABLE field_corrections (
  table_name TEXT NOT NULL, row_id INTEGER NOT NULL, column_name TEXT NOT NULL, old_value, new_value,
  song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, corrected_at TEXT NOT NULL);
CREATE TABLE song_requests (
  title TEXT NOT NULL, artist TEXT, album TEXT, year INTEGER, duration_s INTEGER,
  youtube_video_id TEXT, spotify_track_id TEXT, source TEXT NOT NULL, requested_at TEXT NOT NULL);
"""
WHEN = "2026-10-14T09:00:00+02:00"
REVIEW_LABEL = "Review the requests"


class _NoTagSession:
    """tag.db isn't part of these screens; submit_global_database_session() commits it too."""

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _title(choice) -> str:
    if isinstance(choice, str):
        return choice
    title = choice.title
    return "".join(text for _, text in title) if isinstance(title, list) else title


class Answers:
    """Scripted questionary answers. A select answer is a piece of the
    choice's title (the first choice containing it is picked), or None for
    Esc / Ctrl+C; a text answer is the typed string. Every select is kept
    as (question, [choice titles])."""

    def __init__(self):
        self.script = []
        self.selects = []
        self.texts = []

    def add(self, *answers):
        self.script.extend(answers)

    def select(self, question, choices, **kwargs):
        titles = [_title(c) for c in choices]
        self.selects.append((question, titles))
        wanted = self.script.pop(0)
        if wanted is None:
            return SimpleNamespace(ask=lambda: None)
        matches = [c for c, t in zip(choices, titles) if wanted in t]
        assert matches, f"no choice with {wanted!r} in {titles}"
        value = matches[0] if isinstance(matches[0], str) else matches[0].value
        return SimpleNamespace(ask=lambda: value)

    def text(self, question, default="", **kwargs):
        self.texts.append(question)
        typed = self.script.pop(0)
        return SimpleNamespace(ask=lambda: typed)


@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox = tmp_path / "Inbox"
    (inbox / pr.REVIEW).mkdir(parents=True)
    for module in (pr, review):
        monkeypatch.setattr(module, "INBOX_DIR", inbox)
        monkeypatch.setattr(module, "inbox_available", lambda: True)
    monkeypatch.setattr(pr, "load_changes", lambda: [])
    monkeypatch.setattr(fc, "load_changes", lambda: [])

    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    artist = Artist(id=10, name="Elise Trouw")
    songs = [Song(id=4601, title="Chromatic", artist=artist, youtube_video_id="dead0", year=2023),
             Song(id=4602, title="Losing Sleep", artist=artist, youtube_video_id=None, year=2023),
             Song(id=4603, title="Obsession", artist=artist, youtube_video_id="dead2", year=2023)]
    session.add_all(songs)
    session.commit()
    monkeypatch.setattr(database_sessions, "_GLOBAL_DATABASE_SESSION", (session, _NoTagSession()))
    monkeypatch.setattr(database_sessions, "apply_song_id_changes", lambda **kw: None)

    checked = []
    monkeypatch.setattr(review, "check_link_cases",
                        lambda cases, on_case=None: checked.extend(cases) or {"api": True, "blocked": 0})
    monkeypatch.setattr(review, "check_correction_videos", lambda cases: {"api": True, "blocked": 0})
    imported = []
    monkeypatch.setattr(ie, "run_import_batch", lambda entries: imported.extend(entries) or [])

    answers = Answers()
    monkeypatch.setattr(review.questionary, "select", answers.select)
    monkeypatch.setattr(review.questionary, "text", answers.text)
    yes_no = []
    monkeypatch.setattr(review, "ask_yes_no", lambda question: yes_no.pop(0))
    yield {"inbox": inbox, "session": session, "songs": {s.id: s for s in songs}, "answers": answers,
           "checked": checked, "imported": imported, "yes_no": yes_no}
    session.close()


def _request(inbox: Path, name: str, dead=(), found=(), fields=(), songs=()) -> Path:
    """dead: (song_id, title, dead id, replacement); found: (song_id, title,
    video id); fields: (row_id, column, old, new) on songs; songs: (artist, title)."""
    path = inbox / pr.REVIEW / name
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(REQUEST_SQL)
        conn.execute("INSERT INTO request VALUES (1, ?, NULL, 'Leina Radio')", (WHEN,))
        for song_id, title, dead_id, replacement in dead:
            conn.execute("INSERT INTO dead_youtube_links VALUES (?, ?, 'Elise Trouw', ?, ?, ?, NULL, NULL, NULL)",
                         (song_id, title, dead_id, WHEN, replacement))
        for song_id, title, video_id in found:
            conn.execute("INSERT INTO found_youtube_links VALUES (?, ?, 'Elise Trouw', ?, 'vt', 'vc', ?)",
                         (song_id, title, video_id, WHEN))
        for row_id, column, old, new in fields:
            conn.execute("INSERT INTO field_corrections VALUES ('songs', ?, ?, ?, ?, ?, 'Chromatic', "
                         "'Elise Trouw', ?)", (row_id, column, old, new, row_id, WHEN))
        for artist, title in songs:
            conn.execute("INSERT INTO song_requests (title, artist, source, requested_at) "
                         "VALUES (?, ?, 'youtube', ?)", (title, artist, WHEN))
    conn.close()
    return path


def _folder(env, folder: str) -> list:
    directory = env["inbox"] / folder
    return sorted(p.name for p in directory.iterdir() if p.suffix == ".db") if directory.exists() else []


def _decisions(env) -> dict:
    return json.loads((env["inbox"] / pr.DECISIONS_FILE).read_text())


def _questions(env) -> list:
    return [q for q, _ in env["answers"].selects]


# ==================== the start menu ====================

def test_an_unavailable_inbox_asks_nothing(env, monkeypatch, capsys):
    monkeypatch.setattr(review, "inbox_available", lambda: False)
    monkeypatch.setattr(review, "inbox_unavailable_message", lambda: "the data drive isn't mounted")

    review.resolve_new_requests()

    assert "The inbox isn't available: the data drive isn't mounted" in capsys.readouterr().out
    assert env["answers"].selects == []


def test_an_empty_inbox_asks_nothing(env, capsys):
    review.resolve_new_requests()

    assert "No new requests in the inbox." in capsys.readouterr().out
    assert env["answers"].selects == []


def test_the_automatic_history_is_shown_and_marked_seen(env, capsys):
    pr.add_auto_report([{"text": "Chromatic: dead link replaced", "at": "2026-10-14T09:30:00+02:00"}])
    env["answers"].add("Show what the automatic check changed (1)")

    review.resolve_new_requests()

    out = capsys.readouterr().out
    assert "2026-10-14 09:30" in out and "Chromatic: dead link replaced" in out
    assert pr.auto_report(unshown_only=True) == []
    assert env["answers"].selects[0][1] == ["Show what the automatic check changed (1)", "Back"]


def test_back_leaves_the_files_alone(env):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", dead=[(4601, "Chromatic", "dead0", "new0")])
    env["answers"].add("Back")

    review.resolve_new_requests()

    assert _folder(env, pr.REVIEW) == ["2026-10-14_09-00-00_001.db"]
    assert env["songs"][4601].youtube_video_id == "dead0"


def test_an_unreadable_file_is_moved_to_rejected(env, capsys):
    (env["inbox"] / pr.REVIEW / "2026-10-14_09-00-00_001.db").write_bytes(b"garbage")
    env["answers"].add(REVIEW_LABEL)

    review.resolve_new_requests()

    assert _folder(env, pr.REJECTED) == ["2026-10-14_09-00-00_001.db"]
    assert "isn't a readable request" in capsys.readouterr().out


# ==================== dead links ====================

def test_using_the_suggested_link_saves_it_and_finishes_the_file(env, capsys):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", dead=[(4601, "Chromatic", "dead0", "new0")])
    env["answers"].add(REVIEW_LABEL, "Use the suggested link (new0)")

    review.resolve_new_requests()

    assert env["songs"][4601].youtube_video_id == "new0"
    assert _decisions(env)["4601:dead0"]["decision"] == pr.USE_VIDEO
    assert _folder(env, pr.REVIEW) == [] and _folder(env, pr.PROCESSED) == ["2026-10-14_09-00-00_001.db"]
    assert [c.song.id for c in env["checked"]] == [4601]  # looked up before the questions
    titles = env["answers"].selects[1][1]
    assert review._KEEP in titles and review._NO_VIDEO in titles and review._PASTE in titles


def test_no_video_stores_the_sentinel(env):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", dead=[(4601, "Chromatic", "dead0", None)])
    env["answers"].add(REVIEW_LABEL, review._NO_VIDEO)

    review.resolve_new_requests()

    assert env["songs"][4601].youtube_video_id == "N/A"


def test_a_pasted_link_is_used_once_it_is_a_youtube_link(env, capsys):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", dead=[(4601, "Chromatic", "dead0", "new0")])
    env["answers"].add(REVIEW_LABEL,
                       review._PASTE, "",  # nothing typed: back to the list
                       review._PASTE, "not-a-link",
                       review._PASTE, "https://www.youtube.com/watch?v=Zm4-hmcJk3A")

    review.resolve_new_requests()

    assert env["songs"][4601].youtube_video_id == "Zm4-hmcJk3A"
    assert "'not-a-link' isn't a YouTube video link." in capsys.readouterr().out
    assert len(env["answers"].selects) == 4  # the menu, then the same case three times


def test_skip_keeps_the_file_for_next_time(env, capsys):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db",
             dead=[(4601, "Chromatic", "dead0", "new0"), (4603, "Obsession", "dead2", "new2")])
    env["answers"].add(REVIEW_LABEL, review._SKIP, "Use the suggested link (new2)")

    review.resolve_new_requests()

    assert env["songs"][4601].youtube_video_id == "dead0"
    assert env["songs"][4603].youtube_video_id == "new2"
    assert _folder(env, pr.REVIEW) == ["2026-10-14_09-00-00_001.db"]
    assert "1 request file(s) stay in the inbox for next time." in capsys.readouterr().out


@pytest.mark.parametrize("stop", [review._STOP, None])  # the Stop answer, or Esc
def test_stop_leaves_the_rest_and_the_later_parts_unasked(env, stop):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db",
             dead=[(4601, "Chromatic", "dead0", "new0"), (4603, "Obsession", "dead2", "new2")],
             found=[(4602, "Losing Sleep", "found1")], fields=[(4601, "year", 2023, 2022)],
             songs=[("Vulfpeck", "Dean Town")])
    env["answers"].add(REVIEW_LABEL, stop)

    review.resolve_new_requests()

    assert len(env["answers"].selects) == 2
    assert (env["songs"][4601].youtube_video_id, env["songs"][4603].youtube_video_id) == ("dead0", "dead2")
    assert env["songs"][4602].youtube_video_id is None and env["songs"][4601].year == 2023
    assert env["imported"] == []
    assert _folder(env, pr.REVIEW) == ["2026-10-14_09-00-00_001.db"]


# ==================== found links ====================

def test_a_found_link_is_saved(env):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", found=[(4602, "Losing Sleep", "found1")])
    env["answers"].add(REVIEW_LABEL, "Use Leina Radio's link (found1)")

    review.resolve_new_requests()

    assert env["songs"][4602].youtube_video_id == "found1"
    assert _folder(env, pr.PROCESSED) == ["2026-10-14_09-00-00_001.db"]


def test_a_wrong_found_link_leaves_the_song_without_one(env):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", found=[(4602, "Losing Sleep", "found1")])
    env["answers"].add(REVIEW_LABEL, "Wrong video")

    review.resolve_new_requests()

    assert env["songs"][4602].youtube_video_id is None
    assert _folder(env, pr.PROCESSED) == ["2026-10-14_09-00-00_001.db"]


def test_after_the_dead_links_the_found_links_are_asked(env):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", dead=[(4601, "Chromatic", "dead0", "new0")],
             found=[(4602, "Losing Sleep", "found1")])
    env["answers"].add(REVIEW_LABEL, "Use the suggested link (new0)", "Use Leina Radio's link (found1)")

    review.resolve_new_requests()

    assert (env["songs"][4601].youtube_video_id, env["songs"][4602].youtube_video_id) == ("new0", "found1")


# ==================== field corrections ====================

def test_a_field_correction_is_saved(env):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", fields=[(4601, "year", 2023, 2022)])
    env["answers"].add(REVIEW_LABEL, "Save the correction (2022)")

    review.resolve_new_requests()

    assert env["songs"][4601].year == 2022
    assert _folder(env, pr.PROCESSED) == ["2026-10-14_09-00-00_001.db"]


def test_keeping_the_current_value_changes_nothing(env):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", fields=[(4601, "year", 2023, 2022)])
    env["answers"].add(REVIEW_LABEL, "Keep the current value (2023)")

    review.resolve_new_requests()

    assert env["songs"][4601].year == 2023
    assert _folder(env, pr.PROCESSED) == ["2026-10-14_09-00-00_001.db"]


def test_an_edited_value_is_checked_before_it_is_saved(env, capsys):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", fields=[(4601, "year", 2023, 2022)])
    env["answers"].add(REVIEW_LABEL,
                       "Edit the value before saving", "nineteen",
                       "Edit the value before saving", None,  # Esc in the text: back to the list
                       "Edit the value before saving", "2021")

    review.resolve_new_requests()

    assert env["songs"][4601].year == 2021
    assert len(env["answers"].texts) == 3
    assert len(env["answers"].selects) == 4


# ==================== songs to add ====================

def test_approved_songs_go_through_one_import_even_after_a_stop(env):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db",
             songs=[("Vulfpeck", "Dean Town"), ("Cory Wong", "Golden")])
    env["answers"].add(REVIEW_LABEL, "Add it to the database (", review._STOP)

    review.resolve_new_requests()

    [entry] = env["imported"]
    asked_first = env["answers"].selects[1][1][0]
    assert f"{entry['artist_name']} - {entry['title']}" in asked_first
    decisions = [d for d in _decisions(env).values() if d.get("decision") == sr.ADD]
    assert [d["added_as"] for d in decisions] == [{"artist": entry["artist_name"], "title": entry["title"]}]
    assert _folder(env, pr.REVIEW) == ["2026-10-14_09-00-00_001.db"]


def test_a_corrected_song_needs_both_the_artist_and_the_title(env, capsys):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", songs=[("Vulfpek", "Dean Town")])
    env["answers"].add(REVIEW_LABEL,
                       "Correct the artist / title", "  ", "Dean Town",
                       "Correct the artist / title", " Vulfpeck ", " Dean Town ")

    review.resolve_new_requests()

    assert "Both the artist and the title are needed." in capsys.readouterr().out
    [entry] = env["imported"]
    assert (entry["artist_name"], entry["title"]) == ("Vulfpeck", "Dean Town")
    assert _folder(env, pr.PROCESSED) == ["2026-10-14_09-00-00_001.db"]


def test_a_rejected_song_is_published_for_leina_radio(env):
    _request(env["inbox"], "2026-10-14_09-00-00_001.db", songs=[("Vulfpeck", "Dean Town")])
    env["answers"].add(REVIEW_LABEL, "Reject")

    review.resolve_new_requests()

    [rejected] = env["session"].query(RejectedSongRequest).all()
    assert (rejected.artist, rejected.title) == ("Vulfpeck", "Dean Town")
    assert env["imported"] == []
    assert _folder(env, pr.PROCESSED) == ["2026-10-14_09-00-00_001.db"]


def test_a_generic_channel_offers_each_credited_artist_and_never_itself(env, monkeypatch, capsys):
    monkeypatch.setattr(sr, "_description_credits", lambda case: ["Natalia Przybysz", "Fisz Emade Tworzywo"])
    _request(env["inbox"], "2026-10-26_16-26-30_031.db", songs=[("Release", "Śniadanie")])
    env["answers"].add(REVIEW_LABEL, "Add it as Fisz Emade Tworzywo")

    review.resolve_new_requests()

    out = capsys.readouterr().out
    assert "Looks like:" in out and "credited in the YouTube video's description" in out
    offered = env["answers"].selects[1][1]
    assert offered[:2] == ["Add it as Natalia Przybysz - Śniadanie (feat. Fisz Emade Tworzywo)",
                           "Add it as Fisz Emade Tworzywo - Śniadanie (feat. Natalia Przybysz)"]
    assert not any("Release" in title for title in offered)
    [entry] = env["imported"]
    assert (entry["artist_name"], entry["title"], entry["feat_artists"]) == \
        ("Fisz Emade Tworzywo", "Śniadanie", ["Natalia Przybysz"])
