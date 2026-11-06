"""The sharing API as Leina Player relies on it (ADR-0006, phase 2): the
specification MDHS's server and MusicDatabaseWired must both pass.

Every test talks to a server in a process of its own, over HTTP only, so nothing
here may import a server's code. What a test may know about a server is in
`ContractServer`: how to start one and the folders it is given. CONTRACT_SERVER
picks the server (only "mdhs" so far); a test for something a server may lack
says so with @pytest.mark.requires("feature").
"""

import http.client
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from contract_helpers import LIBRARY_FILE, PASSWORD
from musicdatabase_core import accounts

REPO = Path(__file__).resolve().parents[2]
START_TIMEOUT_SECONDS = 20


def _mdhs_command(server: "ContractServer") -> list[str]:
    command = [sys.executable, str(REPO / "src/utils/database/sharing_server.py"), "--dir", str(server.share),
               "--port", str(server.port), "--no-tailscale", "--address", "127.0.0.1",
               "--inbox", str(server.inbox), "--users", str(server.users),
               "--mood-changes", str(server.mood_changes), "--library", str(server.library)]
    if server.tls:
        command += ["--tls-dir", str(server.tls_dir)]
    return command


TARGETS = {
    "mdhs": (_mdhs_command, {"library", "unsigned_uploads", "global_moods", "plain_http"}),
}


def _target():
    return TARGETS[os.environ.get("CONTRACT_SERVER", "mdhs")]


def pytest_configure(config):
    config.addinivalue_line("markers", "requires(feature): the test needs something a server may lack")


def pytest_runtest_setup(item):
    for marker in item.iter_markers("requires"):
        if marker.args[0] not in _target()[1]:
            pytest.skip(f"this server has no {marker.args[0]}")


@dataclass
class Answer:
    status: int
    headers: http.client.HTTPMessage
    body: bytes

    @property
    def json(self):
        """Only with a JSON Content-Type: the phone ignores the body otherwise."""
        assert self.headers.get_content_type() == "application/json", self.headers.get("Content-Type")
        return json.loads(self.body)


def _make_share(folder: Path) -> None:
    """Merged song 2 -> 4, deleted song 5, songs 1, 3, 4 and 6; global mood 1."""
    folder.mkdir()
    music = sqlite3.connect(folder / "music.db")
    music.executescript("""
        CREATE TABLE songs (id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE song_id_changes (id INTEGER PRIMARY KEY, old_song_id INTEGER, new_song_id INTEGER,
                                      changed_at TEXT);
        INSERT INTO songs (id, title) VALUES (1, 'a'), (3, 'c'), (4, 'kept'), (6, 'f');
        INSERT INTO song_id_changes (id, old_song_id, new_song_id) VALUES (1, 2, 4), (2, 5, NULL);
    """)
    music.close()
    tag = sqlite3.connect(folder / "tag.db")
    tag.executescript("""
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, kind TEXT);
        CREATE TABLE song_tags (id INTEGER PRIMARY KEY, song_id INTEGER, tag_id INTEGER);
        INSERT INTO tags (id, name, kind) VALUES (1, 'Do biegania', 'mood'), (2, 'spellchecked', 'marker');
    """)
    tag.close()
    files = sqlite3.connect(folder / "files.db")
    files.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        INSERT INTO files (id, path) VALUES (1, 'song.mp3'), (2, 'gone.mp3'), (3, 'notes.txt'), (4, '../outside.mp3');
    """)
    files.close()
    (folder / "secret.txt").write_text("not for the phone")


class ContractServer:
    def __init__(self, folder: Path, tls: bool = False):
        self.folder = folder
        self.tls = tls
        self.share = folder / "share"
        self.users = folder / "users"
        self.inbox = folder / "inbox"
        self.mood_changes = folder / "mood-changes"
        self.library = folder / "library"
        self.tls_dir = folder / "tls"
        _make_share(self.share)
        for path in (self.users, self.inbox, self.mood_changes, self.library, self.tls_dir):
            path.mkdir()
        (self.library / "song.mp3").write_bytes(LIBRARY_FILE)
        (self.library / "notes.txt").write_text("not audio")
        (folder / "outside.mp3").write_bytes(b"outside")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self._user_count = 0
        self.log = open(folder / "server.log", "w")
        self.process = subprocess.Popen(_target()[0](self), cwd=folder, stdout=self.log, stderr=subprocess.STDOUT)
        self._wait_until_up()

    def _wait_until_up(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            assert self.process.poll() is None, (self.folder / "server.log").read_text()
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("the server didn't start")

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.log.close()

    def connection(self, timeout: float = 10) -> http.client.HTTPConnection:
        if self.tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return http.client.HTTPSConnection("127.0.0.1", self.port, timeout=timeout, context=context)
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)

    def call(self, method: str, path: str, token: str | None = None, payload=None, raw: bytes | None = None,
             headers: dict | None = None) -> Answer:
        """Like the phone: JSON with a Content-Length, a Bearer token."""
        sent = dict(headers or {})
        body = raw
        if payload is not None:
            body = json.dumps(payload).encode()
            sent.setdefault("Content-Type", "application/json")
        if token:
            sent["Authorization"] = f"Bearer {token}"
        connection = self.connection()
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            return Answer(response.status, response.headers, response.read())
        finally:
            connection.close()

    def add_user(self, name: str | None = None, permissions: tuple = ()) -> str:
        """An account in the layout both programs share. Returns its name."""
        self._user_count += 1
        name = name or f"user{self._user_count}"
        accounts.create_account(self.users, name, PASSWORD, lambda: 2)
        if permissions:
            account = self.users / name / "account.json"
            account.write_text(json.dumps({**json.loads(account.read_text()), "permissions": list(permissions)}))
        return name

    def login(self, name: str) -> str:
        answer = self.call("POST", "/login", payload={"username": name, "password": PASSWORD, "device": "Pixel 7a"})
        assert answer.status == 200, answer.body
        return answer.json["token"]

    def new_user(self, permissions: tuple = ()) -> tuple[str, str]:
        name = self.add_user(permissions=permissions)
        return name, self.login(name)


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """Shared by the tests: anything that counts per address gets `fresh_server`."""
    running = ContractServer(tmp_path_factory.mktemp("contract"))
    yield running
    running.stop()


@pytest.fixture
def fresh_server(tmp_path):
    running = ContractServer(tmp_path)
    yield running
    running.stop()


@pytest.fixture
def tls_server(tmp_path):
    running = ContractServer(tmp_path, tls=True)
    yield running
    running.stop()
