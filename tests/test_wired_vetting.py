"""MusicDatabaseWired's check of a request (ADR-0006, phase 4), through the real server:
hostile and malformed files refused, what a newer app sends held, and what's handed on
being a new file that MDHS's own reader takes."""

import json
import os
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

from config import constants
from musicdatabase_core import accounts
from musicdatabase_core.request_format import CORRECTABLE_FIELDS
from test_wired import MDW, PYTHON, REPO, _status, wired  # noqa: F401
from utils.database import inbox_field_corrections, inbox_requests

pytestmark = pytest.mark.skipif(not PYTHON.exists(), reason="wired/venv isn't installed here")

NAME = "ala_2026-11-06_10-00-00_001.db"
VIDEO = "abcdefghijk"
SPOTIFY = "a" * 22
REQUEST = """CREATE TABLE request (format INTEGER NOT NULL, created_at TEXT NOT NULL, user_name TEXT, app TEXT NOT NULL);
INSERT INTO request VALUES (1, '2026-11-06T10:00:00+01:00', 'someone-else', 'Leina Player');"""
SONGS = """CREATE TABLE song_requests (title TEXT NOT NULL, artist TEXT, album TEXT, year INTEGER, duration_s INTEGER,
    youtube_video_id TEXT, spotify_track_id TEXT, source TEXT NOT NULL, requested_at TEXT NOT NULL);"""
GOOD_SONG = "INSERT INTO song_requests VALUES ('Tytuł', 'Ktoś', NULL, 1999, 215, 'abcdefghijk', NULL, 'youtube', 'now');"
DEAD = """CREATE TABLE dead_youtube_links (song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL,
    dead_video_id TEXT NOT NULL, detected_at TEXT NOT NULL, replacement_video_id TEXT, replacement_title TEXT,
    replacement_channel TEXT, replacement_found_at TEXT, PRIMARY KEY (song_id, dead_video_id));"""
CORRECTIONS = """CREATE TABLE field_corrections (table_name TEXT NOT NULL, row_id INTEGER NOT NULL,
    column_name TEXT NOT NULL, old_value, new_value, song_id INTEGER NOT NULL, title TEXT NOT NULL,
    artist TEXT NOT NULL, corrected_at TEXT NOT NULL, PRIMARY KEY (table_name, row_id, column_name));"""
FOUND = """CREATE TABLE found_youtube_links (song_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL,
    video_id TEXT NOT NULL, video_title TEXT, video_channel TEXT, found_at TEXT NOT NULL,
    PRIMARY KEY (song_id, video_id));"""


def request_bytes(tmp_path: Path, script: str) -> bytes:
    path = tmp_path / "sent.db"
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(script)
    conn.close()
    return path.read_bytes()


@pytest.fixture
def server(wired, tmp_path):  # noqa: F811
    """A running MDW with a catalog and a logged-in user 'ala'."""
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    conn = sqlite3.connect(catalog / "music.db")
    conn.executescript("""CREATE TABLE songs (id INTEGER PRIMARY KEY);
        CREATE TABLE song_id_changes (id INTEGER PRIMARY KEY, old_song_id INTEGER, new_song_id INTEGER);
        CREATE TABLE rejected_song_requests (id INTEGER PRIMARY KEY, artist TEXT, title TEXT, youtube_video_id TEXT,
                                             spotify_track_id TEXT, rejected_at TEXT);
        INSERT INTO rejected_song_requests (artist, title) VALUES ('Nie', 'Chciany');""")
    conn.close()
    (tmp_path / "users").mkdir()
    accounts.create_account(tmp_path / "users", "ala", "secret-pass", lambda: 0)
    port = wired()
    import http.client
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("POST", "/login", body=json.dumps({"username": "ala", "password": "secret-pass"}))
    token = json.loads(connection.getresponse().read())["token"]
    connection.close()

    def put(data: bytes, name: str = NAME) -> int:
        return _status(port, "PUT", f"/inbox/{name}", body=data, headers={"Authorization": f"Bearer {token}"})

    return put, tmp_path / "inbox"


def _stored(inbox: Path, name: str = NAME) -> Path:
    return inbox / "valid" / name


@pytest.mark.parametrize("name, script", [
    ("a view", REQUEST + SONGS + GOOD_SONG + "CREATE VIEW v AS SELECT * FROM song_requests;"),
    ("a trigger", REQUEST + SONGS + GOOD_SONG
     + "CREATE TRIGGER t AFTER INSERT ON song_requests BEGIN DELETE FROM request; END;"),
    ("an index of its own", REQUEST + SONGS + GOOD_SONG + "CREATE INDEX i ON song_requests (title);"),
    ("no request table", SONGS + GOOD_SONG),
    ("two request rows", REQUEST + REQUEST.split(";")[1] + ";" + SONGS + GOOD_SONG),
    ("no rows in request", REQUEST.split(";")[0] + ";" + SONGS + GOOD_SONG),
    ("a computed column", REQUEST + """CREATE TABLE song_requests (title TEXT NOT NULL, artist TEXT, album TEXT,
        year INTEGER GENERATED ALWAYS AS (1) VIRTUAL, duration_s INTEGER, youtube_video_id TEXT,
        spotify_track_id TEXT, source TEXT NOT NULL, requested_at TEXT NOT NULL);
        INSERT INTO song_requests (title, source, requested_at) VALUES ('x', 'youtube', 'now');"""),
    ("text as a year", REQUEST + SONGS + "INSERT INTO song_requests VALUES ('x', NULL, NULL, 'soon', NULL, NULL, NULL, 'youtube', 'now');"),
    ("a blob as a title", REQUEST + SONGS + "INSERT INTO song_requests VALUES (x'00', NULL, NULL, NULL, NULL, NULL, NULL, 'youtube', 'now');"),
    ("a bad video id", REQUEST + SONGS + "INSERT INTO song_requests VALUES ('x', NULL, NULL, NULL, NULL, 'not valid', NULL, 'youtube', 'now');"),
    ("a bad spotify id", REQUEST + SONGS + "INSERT INTO song_requests VALUES ('x', NULL, NULL, NULL, NULL, NULL, '../x', 'spotify', 'now');"),
    ("a far too long title", REQUEST + SONGS + f"INSERT INTO song_requests VALUES ('{'x' * 1001}', NULL, NULL, NULL, NULL, NULL, NULL, 'youtube', 'now');"),
    ("a negative year", REQUEST + SONGS + "INSERT INTO song_requests VALUES ('x', NULL, NULL, -1, NULL, NULL, NULL, 'youtube', 'now');"),
    ("a song id of zero", REQUEST + DEAD + "INSERT INTO dead_youtube_links (song_id, title, artist, dead_video_id, detected_at) VALUES (0, 't', 'a', 'abcdefghijk', 'now');"),
    ("a required column missing", REQUEST + "CREATE TABLE song_requests (title TEXT); INSERT INTO song_requests VALUES ('x');"),
])
def test_a_bad_file_is_refused_and_logged_without_its_contents(server, tmp_path, name, script):
    put, inbox = server
    assert put(request_bytes(tmp_path, script)) == 400, name
    assert not _stored(inbox).exists()
    assert not (inbox / "held" / NAME).exists() and not (inbox / "new" / NAME).exists()
    assert (inbox / "rejected" / NAME).exists()
    entries = [json.loads(line) for line in (inbox / "refused.jsonl").read_text().splitlines()]
    assert entries[-1]["name"] == NAME and entries[-1]["sender"] == "ala"
    assert "xxxx" not in entries[-1]["reason"]
    assert not list((inbox / ".checking").iterdir())


def test_a_damaged_file_is_refused(server, tmp_path):
    put, inbox = server
    data = bytearray(request_bytes(tmp_path, REQUEST + SONGS + GOOD_SONG))
    page_size = int.from_bytes(data[16:18], "big")
    data[page_size:page_size + 100] = b"\xff" * 100  # the second page's header
    assert put(bytes(data)) == 400
    assert not _stored(inbox).exists()


@pytest.mark.parametrize("script", [
    REQUEST.replace("(1, ", "(2, ") + SONGS + GOOD_SONG,
    REQUEST + "CREATE TABLE future_reports (x); INSERT INTO future_reports VALUES (1);",
    REQUEST + SONGS.replace("requested_at TEXT NOT NULL", "requested_at TEXT NOT NULL, mood TEXT") + GOOD_SONG.replace("'now')", "'now', NULL)"),
    REQUEST + SONGS + GOOD_SONG.replace("'youtube'", "'tidal'"),
    REQUEST + CORRECTIONS + "INSERT INTO field_corrections VALUES ('songs', 1, 'mood_color', NULL, 'x', 1, 't', 'a', 'now');",
])
def test_what_a_newer_app_sends_is_held_not_lost(server, tmp_path, script):
    put, inbox = server
    data = request_bytes(tmp_path, script)
    assert put(data) == 201
    assert not _stored(inbox).exists()
    assert (inbox / "held" / NAME).read_bytes() == data
    assert not (inbox / "new" / NAME).exists()
    assert put(data) == 200


def test_held_requests_are_checked_again_once_understood(server, tmp_path):
    put, inbox = server
    data = request_bytes(tmp_path, REQUEST + SONGS + GOOD_SONG.replace("'youtube'", "'tidal'"))
    assert put(data) == 201
    held = inbox / "held" / NAME
    conn = sqlite3.connect(held)
    conn.execute("UPDATE song_requests SET source = 'youtube'")
    conn.commit()
    conn.close()
    script = ("import sys; sys.path[:0] = [sys.argv[1] + '/wired', sys.argv[1] + '/src'];"
              "from dataclasses import replace;"
              "from pathlib import Path;"
              "from musicdatabase_wired import admin;"
              "from musicdatabase_wired.config import load_config;"
              "root = Path(sys.argv[2]);"
              "config = replace(load_config(), inbox_dir=root / 'inbox', catalog_dir=root / 'catalog');"
              "folders = admin.request_folders_of(config);"
              "print(folders.recheck_held()['accepted'], folders.counts())")
    result = subprocess.run([PYTHON, "-c", script, str(REPO), str(tmp_path)], capture_output=True, text=True,
                            timeout=60, cwd=tmp_path, env={"WIRED_DATA_DIR": str(tmp_path / "data")})
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("1 ") and "'valid': 1" in result.stdout and "'held': 0" in result.stdout
    assert _stored(inbox).exists() and not held.exists()


def test_the_file_handed_on_is_new_and_mdhs_reads_all_four_kinds(server, tmp_path):
    put, inbox = server
    script = REQUEST + DEAD + CORRECTIONS + FOUND + SONGS + GOOD_SONG + f"""
        INSERT INTO dead_youtube_links VALUES (7, 't', 'a', '{VIDEO}', 'now', '{VIDEO[::-1]}', 'new', 'channel', 'then');
        INSERT INTO field_corrections VALUES ('songs', 7, 'year', 1998, '1999', 7, 't', 'a', 'now');
        INSERT INTO found_youtube_links VALUES (8, 't', 'a', '{VIDEO}', NULL, NULL, 'now');
        CREATE TABLE android_metadata (locale TEXT); INSERT INTO android_metadata VALUES ('pl_PL');
        DELETE FROM song_requests WHERE title = 'nothing';"""
    assert put(request_bytes(tmp_path, script)) == 201
    report = inbox_requests.read_request_file(_stored(inbox))
    assert report.error is None and report.unknown_tables == []
    assert report.format == constants.PHONE_REQUEST_FORMAT
    assert report.user_name == "ala"  # the token's owner, not what the file claimed
    assert report.dead_links[0].replacement_video_id == VIDEO[::-1]
    assert (report.field_corrections[0].old_value, report.field_corrections[0].new_value) == (1998, "1999")
    assert report.found_links[0].video_title is None
    assert (report.song_requests[0].title, report.song_requests[0].year) == ("Tytuł", 1999)
    conn = sqlite3.connect(_stored(inbox))
    assert "android_metadata" not in {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    conn.close()


def test_nothing_hidden_in_the_original_survives(server, tmp_path):
    put, inbox = server
    secret = "HIDDEN-LEFTOVER-" * 200
    path = tmp_path / "sent.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA secure_delete=OFF")
    conn.executescript(REQUEST + SONGS + GOOD_SONG)
    conn.execute("INSERT INTO song_requests VALUES (?, NULL, NULL, NULL, NULL, NULL, NULL, 'youtube', 'now')", (secret,))
    conn.execute("DELETE FROM song_requests WHERE title = ?", (secret,))
    conn.commit()
    conn.close()
    data = path.read_bytes()
    assert b"HIDDEN-LEFTOVER" in data
    assert put(data) == 201
    assert b"HIDDEN-LEFTOVER" not in _stored(inbox).read_bytes()


def test_sending_the_same_file_again_is_harmless_and_another_is_a_conflict(server, tmp_path):
    put, inbox = server
    data = request_bytes(tmp_path, REQUEST + SONGS + GOOD_SONG)
    assert put(data) == 201
    assert put(data) == 200
    assert put(request_bytes(tmp_path, REQUEST + SONGS + GOOD_SONG.replace("Tytuł", "Inny"))) == 409


def test_songs_the_owner_rejected_arent_passed_on(server, tmp_path):
    put, inbox = server
    rejected = GOOD_SONG.replace("Tytuł", "chciany").replace("Ktoś", "NIE")
    assert put(request_bytes(tmp_path, REQUEST + SONGS + rejected + GOOD_SONG)) == 201
    assert [row.title for row in inbox_requests.read_request_file(_stored(inbox)).song_requests] == ["Tytuł"]


def test_a_file_with_only_rejected_songs_counts_as_sent(server, tmp_path):
    put, inbox = server
    rejected = GOOD_SONG.replace("Tytuł", "Chciany").replace("Ktoś", "Nie")
    assert put(request_bytes(tmp_path, REQUEST + SONGS + rejected)) == 201
    assert not _stored(inbox).exists()


def test_only_the_account_running_the_server_can_read_the_folders(server, tmp_path):
    put, inbox = server
    assert put(request_bytes(tmp_path, REQUEST + SONGS + GOOD_SONG)) == 201
    assert not list((inbox / "new").iterdir())
    for folder in (inbox, inbox / "new", inbox / "valid", inbox / "held", inbox / "rejected"):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder
    assert stat.S_IMODE(_stored(inbox).stat().st_mode) == 0o600


def test_a_request_left_in_new_by_a_stop_is_checked_at_the_next_start(wired, tmp_path):  # noqa: F811
    (tmp_path / "inbox/new").mkdir(parents=True)
    (tmp_path / "inbox/new" / NAME).write_bytes(request_bytes(tmp_path, REQUEST + SONGS + GOOD_SONG))
    wired()
    assert not (tmp_path / "inbox/new" / NAME).exists()
    assert _stored(tmp_path / "inbox").exists()
    assert inbox_requests.read_request_file(_stored(tmp_path / "inbox")).user_name == "ala"


@pytest.mark.parametrize("folder", ["review", "processed"])
def test_a_request_mdhs_already_took_is_already_here(server, tmp_path, folder):
    put, inbox = server
    data = request_bytes(tmp_path, REQUEST + SONGS + GOOD_SONG)
    assert put(data) == 201
    (inbox / folder).mkdir()
    _stored(inbox).rename(inbox / folder / NAME)
    assert put(data) == 200
    assert not _stored(inbox).exists()


def test_it_wont_start_where_folders_cant_be_private(tmp_path):
    (tmp_path / "inbox").mkdir(mode=0o755)
    os.chmod(tmp_path / "inbox", 0o755)
    script = ("import os, sys; sys.path[:0] = [sys.argv[1] + '/wired', sys.argv[1] + '/src'];"
              "from musicdatabase_wired.request_folders import RequestFolders, NotPrivateError;"
              "from pathlib import Path;"
              "os.chmod = lambda *a, **k: None;"
              "folders = RequestFolders(Path(sys.argv[2]) / 'inbox', Path(sys.argv[2]) / 'music.db');\n"
              "try:\n    folders.make_private(); print('started')\n"
              "except NotPrivateError as exc:\n    print('refused', exc)")
    result = subprocess.run([PYTHON, "-c", script, str(REPO), str(tmp_path)], capture_output=True, text=True,
                            timeout=60, cwd=tmp_path)
    assert result.stdout.startswith("refused"), result.stdout + result.stderr


def test_correctable_fields_are_mdhs_fields():
    assert CORRECTABLE_FIELDS == set(inbox_field_corrections.FIELDS)
    assert MDW.exists()
