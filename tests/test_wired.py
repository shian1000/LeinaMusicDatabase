"""What MusicDatabaseWired does beyond the shared contract (tests/contract/), from the
outside, since this venv doesn't have its packages: limits on silent connections,
what a public server refuses, the catalog's checks and accounts."""

import hashlib
import http.client
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / "wired/venv/bin/python"
MDW = REPO / "wired/mdw.py"

pytestmark = pytest.mark.skipif(not PYTHON.exists(), reason="wired/venv isn't installed here")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def wired(tmp_path):
    processes = []

    def start(*options):
        port = _free_port()
        log = open(tmp_path / "server.log", "w")
        process = subprocess.Popen(
            [PYTHON, MDW, "serve", "--catalog", tmp_path / "catalog", "--users", tmp_path / "users",
             "--inbox", tmp_path / "inbox", "--port", str(port), "--address", "127.0.0.1", "--no-tls",
             "--no-catalog-updates", *options], cwd=tmp_path, stdout=log, stderr=subprocess.STDOUT)
        processes.append((process, log))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert process.poll() is None, (tmp_path / "server.log").read_text()
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                return port
            except OSError:
                time.sleep(0.05)
        raise AssertionError("the server didn't start")

    yield start
    for process, log in processes:
        process.terminate()
        process.wait(timeout=10)
        log.close()


def _status(port: int, method: str, path: str, **options) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, **options)
        return connection.getresponse().status
    finally:
        connection.close()


def _closes_within(sock: socket.socket, seconds: float) -> bool:
    sock.settimeout(seconds)
    try:
        while sock.recv(4096):
            pass
        return True
    except TimeoutError:
        return False


@pytest.mark.parametrize("sent", [b"", b"GET /music.db HTTP/1.1\r\nHost: 127.",
                                  b"PUT /login HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 50\r\n\r\nabc"])
def test_a_silent_connection_gives_its_place_back(wired, sent):
    port = wired("--request-timeout", "1")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(sent)
        assert _closes_within(sock, 5)


def test_a_connection_after_an_answer_is_closed_too(wired):
    port = wired("--request-timeout", "1")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(b"GET /nothing HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        assert sock.recv(4096).startswith(b"HTTP/1.1 404")
        assert _closes_within(sock, 5)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_the_api_describes_nothing_about_itself(wired, path):
    assert _status(wired(), "GET", path) == 404


@pytest.mark.parametrize("method, path", [
    ("GET", "/library/1"), ("GET", "/files.db"), ("PUT", "/global-moods/1/songs/1"), ("GET", "/music.db"),
    ("GET", "/tag.db"), ("GET", "/users/ala"), ("GET", "/users/ala/playlists"), ("GET", "/users/ala/listens"),
    ("PUT", "/users/ala/covers/abc"),
])
def test_the_base_only_logs_in_and_takes_requests(wired, method, path):
    assert _status(wired(), method, path) == 404


def test_the_sync_addon_is_loaded_only_when_named(wired):
    assert _status(wired("--addon", "sync"), "GET", "/users/ala") == 401


def test_a_request_without_a_login_is_refused(wired):
    port = wired()
    assert _status(port, "PUT", "/inbox/2026-11-06_10-00-00_001.db", body=b"SQLite format 3\x00") == 401


def test_a_claimed_address_doesnt_get_round_the_login_limit(wired, tmp_path):
    port = wired()
    body = json.dumps({"username": "nobody", "password": "wrong-pass"})
    for number in range(5):
        assert _status(port, "POST", "/login", body=body,
                       headers={"X-Forwarded-For": f"203.0.113.{number}"}) == 401
    assert _status(port, "POST", "/login", body=json.dumps({"username": "other", "password": "x"}),
                   headers={"X-Forwarded-For": "203.0.113.200"}) == 429


def test_without_an_address_it_doesnt_start_and_says_why(tmp_path):
    result = subprocess.run([PYTHON, MDW, "serve", "--catalog", tmp_path / "c"], capture_output=True, text=True,
                            timeout=30, env={"WIRED_DATA_DIR": str(tmp_path)}, cwd=tmp_path)
    assert result.returncode == 2
    assert "No address" in result.stderr


def test_it_runs_with_only_its_own_folder_and_the_core(tmp_path):
    """Someone may install MDW alone: nothing of MDHS may be needed."""
    copy = tmp_path / "repo"
    shutil.copytree(REPO / "wired", copy / "wired", ignore=shutil.ignore_patterns("venv", "data", "__pycache__", ".env"))
    shutil.copytree(REPO / "src/musicdatabase_core", copy / "src/musicdatabase_core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "migrations/user", copy / "migrations/user")
    script = ("import sys; sys.path[:0] = [sys.argv[1] + '/wired', sys.argv[1] + '/src'];"
              "from musicdatabase_wired import admin, app, server, menu;"
              "from musicdatabase_wired.config import load_config;"
              "config = load_config(); server.create_app(config);"
              "print(admin.create_user(config, 'ala', 'secret-pass'), admin.list_users(config))")
    result = subprocess.run([PYTHON, "-c", script, str(copy)], capture_output=True, text=True, timeout=60,
                            cwd=tmp_path, env={"WIRED_DATA_DIR": str(tmp_path / "data")})
    assert result.returncode == 0, result.stderr
    assert "ala ['ala']" in result.stdout


# ---- the catalog ----

def _sqlite_file(path: Path, value: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()


def _publish(folder: Path, value: int, **overrides) -> None:
    folder.mkdir(exist_ok=True)
    files = {}
    for name in ("music.db", "tag.db"):
        (folder / name).unlink(missing_ok=True)
        _sqlite_file(folder / name, value)
        data = (folder / name).read_bytes()
        files[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (folder / "version.json").write_text(json.dumps({"format": 1, "published_at": "now", "files": files, **overrides}))


@pytest.fixture
def release(tmp_path):
    folder = tmp_path / "release"
    _publish(folder, 1)

    class Quiet(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(folder), **kwargs)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    Thread(target=server.serve_forever, daemon=True).start()
    yield folder, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _update(tmp_path: Path, base_url: str) -> subprocess.CompletedProcess:
    script = ("import sys; sys.path[:0] = [sys.argv[1] + '/wired', sys.argv[1] + '/src'];"
              "from musicdatabase_wired.addons.catalog import update_catalog, CatalogError;"
              "from musicdatabase_wired.config import load_config;\n"
              "try:\n    print('updated' if update_catalog(load_config(), sys.argv[2]) else 'same')\n"
              "except CatalogError as exc:\n    print('refused:', exc)")
    return subprocess.run([PYTHON, "-c", script, str(REPO), base_url], capture_output=True, text=True, timeout=60,
                          cwd=tmp_path, env={"WIRED_DATA_DIR": str(tmp_path / "data")})


def _value(tmp_path: Path) -> int:
    conn = sqlite3.connect(tmp_path / "data/catalog/music.db")
    try:
        return conn.execute("SELECT x FROM t").fetchone()[0]
    finally:
        conn.close()


def test_the_catalog_downloads_once_and_again_when_it_changes(tmp_path, release):
    folder, url = release
    assert _update(tmp_path, url).stdout.strip() == "updated"
    assert _value(tmp_path) == 1
    assert _update(tmp_path, url).stdout.strip() == "same"
    _publish(folder, 2)
    assert _update(tmp_path, url).stdout.strip() == "updated"
    assert _value(tmp_path) == 2


@pytest.mark.parametrize("damage", ["sha", "size", "format", "not sqlite"])
def test_a_bad_catalog_leaves_the_old_one(tmp_path, release, damage):
    folder, url = release
    _update(tmp_path, url)
    _publish(folder, 2)
    version = json.loads((folder / "version.json").read_text())
    if damage == "sha":
        version["files"]["tag.db"]["sha256"] = "0" * 64
    elif damage == "size":
        version["files"]["tag.db"]["size"] += 1
    elif damage == "format":
        version["format"] = 99
    else:
        data = b"plain text, not a database"
        (folder / "tag.db").write_bytes(data)
        version["files"]["tag.db"] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (folder / "version.json").write_text(json.dumps(version))

    assert _update(tmp_path, url).stdout.startswith("refused:")
    assert _value(tmp_path) == 1
    assert not list((tmp_path / "data/catalog").glob("*.partial"))


# ---- accounts ----

def _admin(tmp_path: Path, code: str) -> str:
    script = ("import sys; sys.path[:0] = [sys.argv[1] + '/wired', sys.argv[1] + '/src'];"
              "from musicdatabase_wired import admin;"
              "from musicdatabase_core import account_auth;"
              "from musicdatabase_wired.config import load_config;"
              "config = load_config();" + code)
    result = subprocess.run([PYTHON, "-c", script, str(REPO)], capture_output=True, text=True, timeout=60,
                            cwd=tmp_path, env={"WIRED_DATA_DIR": str(tmp_path / "data")})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_accounts_from_making_one_to_archiving_it(tmp_path):
    assert _admin(tmp_path, "print(admin.create_user(config, 'Ala', 'secret-pass'))") == "ala"
    token_code = "t = account_auth.issue_token(config.users_dir, 'ala', 'Pixel');"
    assert _admin(tmp_path, token_code + "print(account_auth.token_user(config.users_dir, t))") == "ala"
    assert _admin(tmp_path, "print([d.name for d in admin.devices(config, 'ala')])") == "['Pixel']"
    assert _admin(tmp_path, "admin.revoke_device(config, 'ala', admin.devices(config, 'ala')[0].token_hash);"
                            "print(admin.devices(config, 'ala'))") == "[]"
    login = "print(account_auth.check_login(config.users_dir, 'ala', 'new-password'))"
    assert _admin(tmp_path, "admin.change_password(config, 'ala', 'new-password');" + login) == "ala"
    moved = _admin(tmp_path, "print(admin.delete_user(config, 'ala'))")
    assert Path(moved, "user.db").exists()
    assert _admin(tmp_path, "print(admin.list_users(config))") == "[]"


def test_a_user_db_from_before_a_migration_catches_up_at_the_start(wired, tmp_path):
    folder = tmp_path / "users/ala"
    folder.mkdir(parents=True)
    conn = sqlite3.connect(folder / "user.db")
    conn.executescript((REPO / "migrations/user/0001_playlists.sql").read_text())
    conn.close()
    wired()
    conn = sqlite3.connect(folder / "user.db")
    applied = {row[0] for row in conn.execute("SELECT filename FROM schema_version")}
    conn.close()
    assert applied == {path.name for path in (REPO / "migrations/user").glob("*.sql")}
    assert list(folder.glob("user.before-migration-*"))
