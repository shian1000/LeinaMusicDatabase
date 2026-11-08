"""What the contract tests share besides the fixtures in conftest.py (not imported
from there: tests/conftest.py has the same module name)."""

import sqlite3
from pathlib import Path

PASSWORD = "secret-pass"
LIBRARY_FILE = bytes(range(256)) * 4


def request_file(folder: Path, number: int = 1) -> bytes:
    """A request file as the phone makes it, with one song request."""
    path = folder / f"request-{number}.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE request (format INTEGER NOT NULL, created_at TEXT NOT NULL, user_name TEXT, app TEXT NOT NULL);
        INSERT INTO request VALUES (1, '2026-11-06T10:00:00', NULL, 'Leina Player');
        CREATE TABLE song_requests (title TEXT NOT NULL, artist TEXT, album TEXT, year INTEGER, duration_s INTEGER,
                                    youtube_video_id TEXT, spotify_track_id TEXT, source TEXT NOT NULL,
                                    requested_at TEXT NOT NULL);
    """)
    conn.execute("INSERT INTO song_requests (title, artist, source, requested_at) VALUES (?, 'A', 'phone_file', 'now')",
                 (f"Song {number}",))
    conn.commit()
    conn.close()
    return path.read_bytes()


def answer_to_a_body_never_sent(server, method: str, path: str, headers: dict | None = None,
                                length: int = 1024) -> int:
    """Declares a body and doesn't send it: only an answer on the headers alone arrives in time."""
    connection = server.connection(timeout=5)
    try:
        connection.putrequest(method, path)
        for key, value in (headers or {}).items():
            connection.putheader(key, value)
        connection.putheader("Content-Length", str(length))
        connection.endheaders()
        return connection.getresponse().status
    finally:
        connection.close()
