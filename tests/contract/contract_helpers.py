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
        CREATE TABLE request (format INTEGER, created_at TEXT, user_name TEXT, app TEXT);
        INSERT INTO request VALUES (1, '2026-11-06T10:00:00', NULL, 'Leina Player');
    """)
    conn.execute("CREATE TABLE note (value INTEGER)")
    conn.execute("INSERT INTO note VALUES (?)", (number,))
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
