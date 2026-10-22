import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import settings as sharing_settings
from utils.database import sharing, sharing_server

SQLITE_HEADER = b"SQLite format 3\x00"


def _make_db(path: Path, value: int) -> None:
    conn = sqlite3.connect(str(path))
    with conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.close()


def _read_value(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT x FROM t").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def share_env(tmp_path, monkeypatch):
    """Source DBs + a fake external drive; `mounted` toggles whether it counts as mounted."""
    src = tmp_path / "src"
    src.mkdir()
    _make_db(src / "music.db", 1)
    _make_db(src / "tag.db", 1)
    mount = tmp_path / "data-drive"
    mount.mkdir()
    share_dir = mount / "Shared" / "Music" / "Database"
    state = {"mounted": True}

    monkeypatch.setattr(sharing, "DB_FILES", {"music": src / "music.db", "tag": src / "tag.db"})
    monkeypatch.setattr(sharing, "SHARING_DRIVE_MOUNT", mount)
    monkeypatch.setattr(sharing, "SHARING_DIR", share_dir)
    monkeypatch.setattr(sharing.os.path, "ismount", lambda p: state["mounted"] and Path(p) == mount)
    return {"src": src, "mount": mount, "share_dir": share_dir, "state": state}


# ==================== copying ====================

def test_share_copies_both_databases_as_valid_sqlite(share_env):
    published = sharing.share_databases()

    share_dir = share_env["share_dir"]
    assert published == [share_dir / "music.db", share_dir / "tag.db"]
    assert sorted(p.name for p in share_dir.iterdir()) == ["music.db", "tag.db"]  # no *.tmp left
    for path in published:
        assert path.read_bytes()[:16] == SQLITE_HEADER
        assert _read_value(path) == 1


def test_share_replaces_previous_copy(share_env):
    sharing.share_databases()
    conn = sqlite3.connect(str(share_env["src"] / "music.db"))
    with conn:
        conn.execute("UPDATE t SET x = 2")
    conn.close()

    sharing.share_databases()

    assert _read_value(share_env["share_dir"] / "music.db") == 2


def test_unmounted_drive_raises_and_creates_nothing(share_env):
    share_env["state"]["mounted"] = False

    with pytest.raises(sharing.SharingUnavailableError):
        sharing.share_databases()

    # mkdir under an unmounted mount point would fill the system disk instead.
    assert not (share_env["mount"] / "Shared").exists()


# ==================== no external drive (SHARING_DRIVE_MOUNT empty) ====================

@pytest.fixture
def system_disk_env(share_env, monkeypatch):
    """SHARING_DIR on the system disk: no mount point to check."""
    monkeypatch.setattr(sharing, "SHARING_DRIVE_MOUNT", None)
    share_env["state"]["mounted"] = False  # nothing is a mount point
    return share_env


def test_without_a_drive_an_existing_folder_is_shared(system_disk_env):
    share_dir = system_disk_env["share_dir"]
    share_dir.mkdir(parents=True)

    published = sharing.share_databases()

    assert published == [share_dir / "music.db", share_dir / "tag.db"]
    assert _read_value(share_dir / "music.db") == 1


def test_without_a_drive_a_missing_folder_is_never_created(system_disk_env):
    with pytest.raises(sharing.SharingUnavailableError, match="doesn't exist"):
        sharing.share_databases()

    # A typo in SHARING_DIR mustn't turn into a folder
    assert not (system_disk_env["mount"] / "Shared").exists()


def test_without_a_drive_a_read_only_folder_is_unavailable(system_disk_env, monkeypatch):
    system_disk_env["share_dir"].mkdir(parents=True)
    monkeypatch.setattr(sharing_settings.os, "access", lambda path, mode: False)

    with pytest.raises(sharing.SharingUnavailableError):
        sharing.share_databases()


def test_sharing_dir_available_rules(tmp_path, monkeypatch):
    folder = tmp_path / "shared"
    mount = tmp_path / "drive"
    mount.mkdir()
    monkeypatch.setattr(sharing_settings.os.path, "ismount", lambda p: Path(p) == mount)

    assert not sharing_settings.sharing_dir_available(None, None)
    assert not sharing_settings.sharing_dir_available(None, folder)  # missing
    folder.mkdir()
    assert sharing_settings.sharing_dir_available(None, folder)
    # With a drive only the mount counts, folder there or not
    assert sharing_settings.sharing_dir_available(mount, mount / "not-yet")
    assert not sharing_settings.sharing_dir_available(tmp_path / "unplugged", folder)


def test_unavailable_messages_name_the_cause(tmp_path):
    assert "isn't set" in sharing_settings.drive_unavailable_message(None, None)
    assert "doesn't exist" in sharing_settings.drive_unavailable_message(None, tmp_path / "x")
    assert "not mounted" in sharing_settings.drive_unavailable_message(tmp_path, tmp_path / "x")


def test_users_and_inbox_work_without_a_drive(tmp_path, monkeypatch):
    from utils.database import inbox_requests, users

    shared = tmp_path / "shared"
    monkeypatch.setattr(users, "USERS_DRIVE_MOUNT", None)
    monkeypatch.setattr(users, "USERS_SHARING_DIR", shared)
    monkeypatch.setattr(inbox_requests, "INBOX_DRIVE_MOUNT", None)
    monkeypatch.setattr(inbox_requests, "INBOX_SHARING_DIR", shared)
    monkeypatch.setattr(inbox_requests, "INBOX_DIR", shared / "Inbox")
    assert not users.users_available() and not inbox_requests.inbox_available()
    assert "doesn't exist" in users.users_unavailable_message()
    assert "doesn't exist" in inbox_requests.inbox_unavailable_message()

    shared.mkdir()

    assert users.users_available() and inbox_requests.inbox_available()


def test_service_unit_without_a_drive_has_no_mount_flag(monkeypatch):
    monkeypatch.setattr(sharing, "SHARING_DRIVE_MOUNT", None)

    unit = sharing._service_unit("/usr/bin/python3")

    assert "--mount" not in unit
    assert f"--dir {sharing.SHARING_DIR}" in unit


def test_quiet_variant_swallows_errors(share_env, monkeypatch):
    share_env["state"]["mounted"] = False
    assert sharing.share_databases_quietly() == []

    share_env["state"]["mounted"] = True
    monkeypatch.setattr(sharing, "_copy_to_tmp", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert sharing.share_databases_quietly() == []


def test_failed_second_copy_keeps_old_pair_and_cleans_temp_files(share_env, monkeypatch):
    sharing.share_databases()
    conn = sqlite3.connect(str(share_env["src"] / "music.db"))
    with conn:
        conn.execute("UPDATE t SET x = 2")
    conn.close()

    real_copy = sharing._copy_to_tmp

    def copy_failing_on_tag(src_path, tmp_path):
        if src_path.name == "tag.db":
            raise OSError("disk full")
        real_copy(src_path, tmp_path)

    monkeypatch.setattr(sharing, "_copy_to_tmp", copy_failing_on_tag)
    with pytest.raises(OSError):
        sharing.share_databases()

    share_dir = share_env["share_dir"]
    # music.db must not be swapped in alone - the app replaces the two as a pair.
    assert _read_value(share_dir / "music.db") == 1
    assert sorted(p.name for p in share_dir.iterdir()) == ["music.db", "tag.db"]


def test_temp_files_are_never_named_like_shared_files():
    for name in sharing_server.SHARED_FILES:
        assert name + sharing._TMP_SUFFIX not in sharing_server.SHARED_FILES


# ==================== HTTP server ====================

@pytest.fixture
def serve(tmp_path):
    servers = []

    def start(share_dir: Path, mount: str, address: str = "127.0.0.1", port: int = 0, handler=None) -> str:
        handler = handler or sharing_server.make_handler(str(share_dir), mount)
        server = ThreadingHTTPServer((address, port), handler)
        server.RequestHandlerClass.log_message = lambda *a: None
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        servers.append(server)
        return f"http://{address}:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _request(url: str, method: str = "GET"):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method)) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


@pytest.fixture
def share_dir(tmp_path):
    d = tmp_path / "share"
    d.mkdir()
    _make_db(d / "music.db", 1)
    _make_db(d / "tag.db", 1)
    _make_db(d / "files.db", 1)
    (d / "secret.txt").write_text("not for the phone")
    (d / "music.db.tmp").write_bytes(b"half a file")
    return d


@pytest.mark.parametrize("name", ["music.db", "tag.db", "files.db"])
def test_server_serves_shared_files(serve, share_dir, name):
    base = serve(share_dir, "/")

    status, headers, body = _request(f"{base}/{name}")

    assert status == 200
    assert body == (share_dir / name).read_bytes()
    assert body[:16] == SQLITE_HEADER
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Length"] == str(len(body))


def test_server_head_sends_headers_only(serve, share_dir):
    base = serve(share_dir, "/")

    status, headers, body = _request(f"{base}/music.db", method="HEAD")

    assert status == 200
    assert body == b""
    assert headers["Content-Length"] == str((share_dir / "music.db").stat().st_size)


@pytest.mark.parametrize("path", [
    "/", "/secret.txt", "/music.db.tmp", "/music.db/", "/foo/music.db", "/..%2fmusic.db", "/MUSIC.DB",
])
def test_server_returns_404_for_anything_else(serve, share_dir, path):
    base = serve(share_dir, "/")

    status, _, body = _request(base + path)

    assert status == 404
    assert b"not for the phone" not in body


def test_server_returns_404_for_missing_file(serve, share_dir):
    (share_dir / "tag.db").unlink()
    base = serve(share_dir, "/")

    assert _request(f"{base}/tag.db")[0] == 404


def test_server_returns_503_when_drive_unmounted(serve, share_dir, tmp_path):
    base = serve(share_dir, str(tmp_path / "not-a-mount-point"))

    assert _request(f"{base}/music.db")[0] == 503


def test_server_without_a_mount_serves_while_the_folder_exists(serve, share_dir, tmp_path):
    base = serve(share_dir, None)
    assert _request(f"{base}/music.db")[0] == 200

    missing = serve(tmp_path / "gone", None)
    assert _request(f"{missing}/music.db")[0] == 503


def test_server_command_line_takes_no_mount(monkeypatch):
    seen = {}
    monkeypatch.setattr(sharing_server, "make_handler", lambda *a, **kw: seen.setdefault("args", a))
    monkeypatch.setattr(sharing_server, "address_sources", lambda *a, **k: [])

    # No address at all is an error exit, after the handler is made
    assert sharing_server.main(["--dir", "/srv/shared", "--port", "8002"]) == 2
    assert seen["args"][:2] == ("/srv/shared", None)


def test_second_socket_on_same_port_serves_the_same_files(serve, share_dir):
    # Tailscale + LAN = one handler on two addresses sharing a port; 127.0.0.2
    # stands in for the second address (all of 127/8 is loopback on Linux).
    handler = sharing_server.make_handler(str(share_dir), "/")
    first = serve(share_dir, "/", handler=handler)
    port = int(first.rsplit(":", 1)[1])
    second = serve(share_dir, "/", address="127.0.0.2", port=port, handler=handler)

    for name in sharing_server.SHARED_FILES:
        assert _request(f"{first}/{name}")[2] == _request(f"{second}/{name}")[2] == (share_dir / name).read_bytes()
    assert _request(f"{second}/")[0] == 404
    assert _request(f"{second}/secret.txt")[0] == 404


# ==================== inbox uploads ====================

REQUEST_NAME = "2026-10-12_23-44-05_001.db"


def _put(url: str, data: bytes | None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method="PUT", headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def _sqlite_bytes(tmp_path: Path, value: int = 1) -> bytes:
    path = tmp_path / f"req{value}.db"
    if not path.exists():
        _make_db(path, value)
    return path.read_bytes()


@pytest.fixture
def inbox_server(serve, share_dir, tmp_path):
    inbox = tmp_path / "Inbox"

    def start(mount="/", **limits):
        handler = sharing_server.make_handler(str(share_dir), mount, str(inbox), **limits)
        return serve(share_dir, mount, handler=handler)

    return start, inbox


def test_upload_is_stored_in_new(inbox_server, tmp_path):
    start, inbox = inbox_server
    base = start()
    data = _sqlite_bytes(tmp_path)

    assert _put(f"{base}/inbox/{REQUEST_NAME}", data)[0] == 201
    assert (inbox / "new" / REQUEST_NAME).read_bytes() == data
    assert [p.name for p in (inbox / "new").iterdir()] == [REQUEST_NAME]  # no .part left


def test_upload_with_user_name(inbox_server, tmp_path):
    start, inbox = inbox_server
    base = start()

    assert _put(f"{base}/inbox/ola-2_{REQUEST_NAME}", _sqlite_bytes(tmp_path))[0] == 201


def test_same_upload_again_is_ok_and_another_under_its_name_is_a_conflict(inbox_server, tmp_path):
    start, inbox = inbox_server
    base = start()
    data = _sqlite_bytes(tmp_path, 1)
    _put(f"{base}/inbox/{REQUEST_NAME}", data)

    assert _put(f"{base}/inbox/{REQUEST_NAME}", data)[0] == 200
    assert _put(f"{base}/inbox/{REQUEST_NAME}", _sqlite_bytes(tmp_path, 2))[0] == 409
    assert (inbox / "new" / REQUEST_NAME).read_bytes() == data


def test_upload_already_processed_is_ok(inbox_server, tmp_path):
    start, inbox = inbox_server
    base = start()
    data = _sqlite_bytes(tmp_path)
    (inbox / "processed").mkdir(parents=True)
    (inbox / "processed" / REQUEST_NAME).write_bytes(data)

    assert _put(f"{base}/inbox/{REQUEST_NAME}", data)[0] == 200
    assert not (inbox / "new" / REQUEST_NAME).exists()


def test_upload_waiting_for_review_is_ok_and_review_counts_towards_the_limit(inbox_server, tmp_path):
    start, inbox = inbox_server
    first, second = _sqlite_bytes(tmp_path, 1), _sqlite_bytes(tmp_path, 2)
    base = start(inbox_limit_bytes=len(first) + len(second) - 1)
    (inbox / "review").mkdir(parents=True)
    (inbox / "review" / REQUEST_NAME).write_bytes(first)

    # The automatic pass left it for the owner: a resend is still "already here"
    assert _put(f"{base}/inbox/{REQUEST_NAME}", first)[0] == 200
    # ...and it still takes room until it's settled
    assert _put(f"{base}/inbox/2026-10-12_23-45-00_002.db", second)[0] == 507
    assert not (inbox / "new").exists() or not any((inbox / "new").iterdir())


@pytest.mark.parametrize("name", [
    "music.db", "2026-10-12_23-44-05_01.db", "../2026-10-12_23-44-05_001.db", "Ola_2026-10-12_23-44-05_001.db",
    "a_2026-10-12_23-44-05_001.db", "_ola_2026-10-12_23-44-05_001.db",
    "2026-10-12_23-44-05_001.db.part", "%2e%2e%2f2026-10-12_23-44-05_001.db", "ola_2026-10-12_23-44-05_001.sqlite",
])
def test_upload_with_a_bad_name_is_refused(inbox_server, tmp_path, name):
    start, inbox = inbox_server
    base = start()

    assert _put(f"{base}/inbox/{name}", _sqlite_bytes(tmp_path))[0] == 400
    assert not (inbox / "new").exists() or not any((inbox / "new").iterdir())


def test_upload_that_isnt_sqlite_is_refused(inbox_server):
    start, inbox = inbox_server
    base = start()

    assert _put(f"{base}/inbox/{REQUEST_NAME}", b"#!/bin/sh\nrm -rf /\n")[0] == 400


def test_upload_over_the_size_limit_is_refused(inbox_server, tmp_path):
    start, inbox = inbox_server
    data = _sqlite_bytes(tmp_path)
    base = start(max_request_bytes=len(data) - 1)

    assert _put(f"{base}/inbox/{REQUEST_NAME}", data)[0] == 413


def test_full_inbox_refuses_new_requests_until_one_is_processed(inbox_server, tmp_path):
    start, inbox = inbox_server
    first, second = _sqlite_bytes(tmp_path, 1), _sqlite_bytes(tmp_path, 2)
    base = start(inbox_limit_bytes=len(first) + len(second) - 1)
    other = "2026-10-12_23-45-00_002.db"

    assert _put(f"{base}/inbox/{REQUEST_NAME}", first)[0] == 201
    assert _put(f"{base}/inbox/{other}", second)[0] == 507
    assert not (inbox / "new" / other).exists()

    # Processing moves it out of new/, which frees the room
    (inbox / "processed").mkdir()
    (inbox / "new" / REQUEST_NAME).rename(inbox / "processed" / REQUEST_NAME)
    assert _put(f"{base}/inbox/{other}", second)[0] == 201


def test_upload_without_length_is_refused(inbox_server, tmp_path):
    start, _ = inbox_server
    base = start()
    host, port = base.removeprefix("http://").split(":")
    import http.client
    conn = http.client.HTTPConnection(host, int(port))
    conn.putrequest("PUT", f"/inbox/{REQUEST_NAME}")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    assert conn.getresponse().status == 411
    conn.close()


def test_upload_when_drive_unmounted(inbox_server, tmp_path):
    start, inbox = inbox_server
    base = start(mount=str(tmp_path / "not-a-mount-point"))

    assert _put(f"{base}/inbox/{REQUEST_NAME}", _sqlite_bytes(tmp_path))[0] == 503
    assert not inbox.exists()  # nothing created on the system disk


def test_without_inbox_uploads_are_404(serve, share_dir, tmp_path):
    base = serve(share_dir, "/")

    assert _put(f"{base}/inbox/{REQUEST_NAME}", _sqlite_bytes(tmp_path))[0] == 404
    assert _put(f"{base}/music.db", _sqlite_bytes(tmp_path))[0] == 404


def test_inbox_files_are_never_downloadable(inbox_server, tmp_path):
    start, _ = inbox_server
    base = start()
    _put(f"{base}/inbox/{REQUEST_NAME}", _sqlite_bytes(tmp_path))

    assert _request(f"{base}/inbox/{REQUEST_NAME}")[0] == 404


# ==================== login and signed uploads ====================

@pytest.fixture
def login_server(serve, share_dir, tmp_path):
    """A server with --inbox and --users; one user 'ola_k' (password 'secret1')."""
    from utils.database import account_auth

    users_dir = tmp_path / "Users"
    (users_dir / "ola_k").mkdir(parents=True)
    salt = b"0" * 16
    digest = hashlib.scrypt(b"secret1", salt=salt, n=2 ** 14, r=8, p=1)
    (users_dir / "ola_k" / account_auth.ACCOUNT_FILENAME).write_text(
        f'{{"username": "ola_k", "password": "scrypt$16384$8$1${salt.hex()}${digest.hex()}"}}')
    inbox = tmp_path / "Inbox"

    def start(mount="/", clock=None):
        limiter = sharing_server.LoginLimiter(clock) if clock else None
        handler = sharing_server.make_handler(str(share_dir), mount, str(inbox),
                                              users_dir=str(users_dir), limiter=limiter)
        return serve(share_dir, mount, handler=handler)

    return start, users_dir, inbox


def _post(url: str, payload=None, headers: dict | None = None, raw: bytes | None = None):
    data = raw if raw is not None else (None if payload is None else json.dumps(payload).encode())
    headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data if data is not None else b"", method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def _login(base, username="ola_k", password="secret1"):
    return _post(f"{base}/login", {"username": username, "password": password})


def test_login_returns_normalized_username_and_a_token_stored_only_as_a_hash(login_server):
    start, users_dir, _ = login_server
    base = start()

    status, headers, body = _login(base, "  Ola_K ")

    assert status == 200 and headers["Content-Type"] == "application/json"
    answer = json.loads(body)
    assert answer["username"] == "ola_k"
    assert len(answer["token"]) >= 40
    stored = (users_dir / "ola_k" / "tokens.json").read_text()
    assert answer["token"] not in stored
    assert hashlib.sha256(answer["token"].encode()).hexdigest() in stored


@pytest.mark.parametrize("username, password", [("ola_k", "wrong"), ("nobody", "secret1"), ("../ola_k", "secret1")])
def test_wrong_login_is_401(login_server, username, password):
    start, users_dir, _ = login_server
    assert _login(start(), username, password)[0] == 401
    assert not (users_dir / "ola_k" / "tokens.json").exists()


@pytest.mark.parametrize("raw", [b"not json", b'["ola_k", "secret1"]', b'{"username": "ola_k"}'])
def test_malformed_login_is_400(login_server, raw):
    start, _, _ = login_server
    assert _post(f"{start()}/login", raw=raw)[0] == 400


def test_login_when_drive_unmounted_is_503(login_server, tmp_path):
    start, _, _ = login_server
    assert _login(start(mount=str(tmp_path / "not-a-mount-point")))[0] == 503


def test_without_users_there_is_no_login(inbox_server):
    start, _ = inbox_server
    assert _login(start())[0] == 404


def test_repeated_failed_logins_lock_out_for_a_while(login_server):
    start, _, _ = login_server
    now = [1000.0]
    base = start(clock=lambda: now[0])

    for _ in range(sharing_server.LOGIN_MAX_FAILURES):
        assert _login(base, password="wrong")[0] == 401
    status, headers, _ = _login(base)  # even the right password
    assert status == 429
    assert int(headers["Retry-After"]) == sharing_server.LOGIN_LOCKOUT_SECONDS

    now[0] += sharing_server.LOGIN_LOCKOUT_SECONDS
    assert _login(base)[0] == 200


def test_failures_spread_out_dont_lock_out(login_server):
    start, _, _ = login_server
    now = [1000.0]
    base = start(clock=lambda: now[0])

    for _ in range(sharing_server.LOGIN_MAX_FAILURES * 2):
        assert _login(base, password="wrong")[0] == 401
        now[0] += sharing_server.LOGIN_FAILURE_WINDOW_SECONDS / (sharing_server.LOGIN_MAX_FAILURES - 1)


def test_logout_revokes_the_token(login_server, tmp_path):
    start, _, inbox = login_server
    base = start()
    token = json.loads(_login(base)[2])["token"]
    other = json.loads(_login(base)[2])["token"]

    assert _post(f"{base}/logout", headers={"Authorization": f"Bearer {token}"})[0] == 204
    assert _post(f"{base}/logout", headers={"Authorization": "Bearer unknown"})[0] == 204
    assert _post(f"{base}/logout")[0] == 204

    name = f"ola_k_{REQUEST_NAME}"
    assert _put(f"{base}/inbox/{name}", _sqlite_bytes(tmp_path), {"Authorization": f"Bearer {token}"})[0] == 401
    assert _put(f"{base}/inbox/{name}", _sqlite_bytes(tmp_path), {"Authorization": f"Bearer {other}"})[0] == 201


def test_signed_uploads(login_server, tmp_path):
    start, users_dir, inbox = login_server
    base = start()
    token = json.loads(_login(base)[2])["token"]
    data = _sqlite_bytes(tmp_path)
    auth = {"Authorization": f"Bearer {token}"}

    # A user part needs that user's token
    assert _put(f"{base}/inbox/ola_k_{REQUEST_NAME}", data)[0] == 401
    assert _put(f"{base}/inbox/ola_k_{REQUEST_NAME}", data, {"Authorization": "Bearer nope"})[0] == 401
    assert _put(f"{base}/inbox/ola_k_{REQUEST_NAME}", data, {"Authorization": token})[0] == 401
    assert _put(f"{base}/inbox/ania_{REQUEST_NAME}", data, auth)[0] == 403
    assert not (inbox / "new").exists() or not any((inbox / "new").iterdir())
    assert _put(f"{base}/inbox/ola_k_{REQUEST_NAME}", data, auth)[0] == 201
    # No user part: accepted with or without a token (made before logging in)
    assert _put(f"{base}/inbox/{REQUEST_NAME}", data)[0] == 201
    assert _put(f"{base}/inbox/2026-10-12_23-45-00_002.db", data, auth)[0] == 201
    # ...but a bad token is still a bad token
    assert _put(f"{base}/inbox/2026-10-12_23-46-00_003.db", data, {"Authorization": "Bearer nope"})[0] == 401


def test_signed_upload_when_drive_unmounted_is_503_not_401(login_server, tmp_path):
    start, _, _ = login_server
    base = start(mount=str(tmp_path / "not-a-mount-point"))
    assert _put(f"{base}/inbox/ola_k_{REQUEST_NAME}", _sqlite_bytes(tmp_path),
                {"Authorization": "Bearer whatever"})[0] == 503


# ==================== Tailscale / LAN binding ====================

@pytest.mark.parametrize("stdout, expected", [
    ("100.64.0.10\n", "100.64.0.10"),
    ("", None),
    ("192.0.2.10\n", None),  # not a Tailscale (CGNAT) address
    ("garbage\n100.64.0.1\n", "100.64.0.1"),
])
def test_tailscale_ipv4_parsing(monkeypatch, stdout, expected):
    monkeypatch.setattr(
        sharing_server.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=stdout, stderr=""),
    )
    assert sharing_server.tailscale_ipv4() == expected


def test_tailscale_ipv4_without_tailscale_installed(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("tailscale")

    monkeypatch.setattr(sharing_server.subprocess, "run", missing)
    assert sharing_server.tailscale_ipv4() is None


def test_bind_waits_for_tailscale_address_and_never_falls_back(monkeypatch):
    addresses = iter([None, None, "100.64.0.10"])
    sleeps = []
    bound = []
    monkeypatch.setattr(sharing_server, "tailscale_ipv4", lambda: next(addresses))
    monkeypatch.setattr(sharing_server.time, "sleep", sleeps.append)
    monkeypatch.setattr(sharing_server, "ThreadingHTTPServer", lambda addr, handler: bound.append(addr) or addr)

    result = sharing_server.bind_when_ready(object, 8002)

    assert result == ("100.64.0.10", 8002)
    assert bound == [("100.64.0.10", 8002)]
    assert len(sleeps) == 2


def test_bind_retries_when_address_not_bindable_yet(monkeypatch):
    attempts = []

    def fake_server(addr, handler):
        attempts.append(addr)
        if len(attempts) == 1:
            raise OSError(99, "Cannot assign requested address")
        return addr

    monkeypatch.setattr(sharing_server, "tailscale_ipv4", lambda: "100.64.0.10")
    monkeypatch.setattr(sharing_server.time, "sleep", lambda s: None)
    monkeypatch.setattr(sharing_server, "ThreadingHTTPServer", fake_server)

    sharing_server.bind_when_ready(object, 8002)

    assert attempts == [("100.64.0.10", 8002)] * 2


IP_ADDR_ENP2S0 = (
    "2: enp2s0    inet 192.0.2.10/24 brd 192.0.2.255 scope global dynamic noprefixroute enp2s0\\"
    "       valid_lft 2944sec preferred_lft 2944sec\n"
)


@pytest.mark.parametrize("stdout, expected", [
    (IP_ADDR_ENP2S0, "192.0.2.10"),
    ("", None),  # interface has no address yet
    ("2: eth0    inet 8.8.8.8/24 scope global eth0\n", None),  # public
    ("3: tailscale0    inet 100.64.0.10/32 scope global tailscale0\n", None),  # Tailscale range
    ("2: eth0    inet 8.8.8.8/24 scope global eth0\n2: eth0    inet 10.0.0.5/8 scope global eth0\n", "10.0.0.5"),
])
def test_interface_ipv4_parsing(monkeypatch, stdout, expected):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(sharing_server.subprocess, "run", fake_run)

    assert sharing_server.interface_ipv4("enp2s0") == expected
    # Asks about the named interface only - never "any private address", which
    # would also match docker0 / br-* bridges.
    assert commands == [["ip", "-4", "-o", "addr", "show", "dev", "enp2s0"]]


def test_interface_ipv4_without_ip_command(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("ip")

    monkeypatch.setattr(sharing_server.subprocess, "run", missing)
    assert sharing_server.interface_ipv4("enp2s0") is None


def test_bind_uses_the_given_address_getter(monkeypatch):
    monkeypatch.setattr(sharing_server, "tailscale_ipv4", lambda: pytest.fail("Tailscale queried for the LAN socket"))
    monkeypatch.setattr(sharing_server, "ThreadingHTTPServer", lambda addr, handler: addr)

    assert sharing_server.bind_when_ready(object, 8002, lambda: "192.0.2.10", "LAN") == ("192.0.2.10", 8002)


class _FakeServer:
    def __init__(self, address, port):
        self.server_address = (address, port)

    def serve_forever(self):
        pass


def _run_main(monkeypatch, extra_args):
    bound = []
    addresses = {"Tailscale": "100.64.0.10", "LAN enp2s0": "192.0.2.10"}

    def fake_bind(handler, port, get_address, label):
        bound.append(label)
        return _FakeServer(addresses[label], port)

    monkeypatch.setattr(sharing_server, "bind_when_ready", fake_bind)
    sharing_server.main(["--dir", "/d", "--mount", "/m", "--port", "8002", *extra_args])
    return bound


def test_without_lan_interface_only_tailscale_is_served(monkeypatch, capsys):
    assert _run_main(monkeypatch, []) == ["Tailscale"]
    assert [label for label, _ in sharing_server.address_sources(None)] == ["Tailscale"]
    assert "192.0.2.10" not in capsys.readouterr().out


def test_lan_interface_adds_a_second_socket(monkeypatch, capsys):
    assert sorted(_run_main(monkeypatch, ["--lan-interface", "enp2s0"])) == ["LAN enp2s0", "Tailscale"]
    out = capsys.readouterr().out
    assert "http://100.64.0.10:8002/" in out
    assert "http://192.0.2.10:8002/" in out


def test_without_tailscale_only_the_chosen_addresses_are_served(monkeypatch, capsys):
    bound = []

    def fake_bind(handler, port, get_address, label):
        bound.append((label, get_address()))
        return _FakeServer(get_address(), port)

    monkeypatch.setattr(sharing_server, "bind_when_ready", fake_bind)
    monkeypatch.setattr(sharing_server, "interface_ipv4", lambda name: "192.0.2.10")
    monkeypatch.setattr(sharing_server, "tailscale_ipv4", lambda: pytest.fail("Tailscale asked with --no-tailscale"))
    sharing_server.main(["--dir", "/d", "--port", "8002", "--no-tailscale", "--lan-interface", "enp2s0",
                         "--address", "198.51.100.7"])
    assert sorted(bound) == [("LAN enp2s0", "192.0.2.10"), ("address 198.51.100.7", "198.51.100.7")]
    assert "http://198.51.100.7:8002/" in capsys.readouterr().out


def test_a_vpn_interface_of_any_provider_is_served(monkeypatch):
    bound = []

    def fake_bind(handler, port, get_address, label):
        bound.append((label, get_address()))
        return _FakeServer(get_address(), port)

    monkeypatch.setattr(sharing_server, "bind_when_ready", fake_bind)
    monkeypatch.setattr(sharing_server, "vpn_interface_ipv4", lambda name: {"wg0": "10.8.0.2"}[name])
    monkeypatch.setattr(sharing_server, "tailscale_ipv4", lambda: pytest.fail("Tailscale asked with --no-tailscale"))
    sharing_server.main(["--dir", "/d", "--port", "8002", "--no-tailscale", "--vpn-interface", "wg0"])
    assert bound == [("VPN wg0", "10.8.0.2")]


@pytest.mark.parametrize("stdout, expected", [
    ("5: wg0    inet 10.8.0.2/24 scope global wg0", "10.8.0.2"),
    ("7: tailscale0    inet 100.64.0.10/32 scope global tailscale0", "100.64.0.10"),
    ("9: ppp0    inet 8.8.8.8/32 scope global ppp0", None),  # public: never
    ("", None),
])
def test_vpn_interface_ipv4_takes_private_or_cgnat_addresses_only(monkeypatch, stdout, expected):
    class _Out:
        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(sharing_server.subprocess, "run", lambda *a, **k: _Out(stdout))
    assert sharing_server.vpn_interface_ipv4("x") == expected


def test_no_address_at_all_is_an_error(monkeypatch):
    monkeypatch.setattr(sharing_server, "bind_when_ready", lambda *a: pytest.fail("bound something"))
    assert sharing_server.main(["--dir", "/d", "--port", "8002", "--no-tailscale"]) == 2


def test_address_must_be_ipv4():
    with pytest.raises(SystemExit):
        sharing_server.main(["--dir", "/d", "--port", "8002", "--address", "home-pc"])


def test_lan_bind_waits_independently_of_tailscale(monkeypatch):
    """A LAN interface without an address must not hold up the Tailscale socket."""
    lan_has_address = threading.Event()
    tailscale_bound = threading.Event()
    monkeypatch.setattr(sharing_server, "tailscale_ipv4", lambda: "100.64.0.10")
    monkeypatch.setattr(sharing_server, "interface_ipv4", lambda name: "192.0.2.10" if lan_has_address.is_set() else None)
    monkeypatch.setattr(sharing_server.time, "sleep", lambda s: lan_has_address.wait(0.01))

    def fake_server(addr, handler):
        if addr[0] == "100.64.0.10":
            tailscale_bound.set()
        return _FakeServer(*addr)

    monkeypatch.setattr(sharing_server, "ThreadingHTTPServer", fake_server)
    runner = threading.Thread(
        target=sharing_server.main,
        args=(["--dir", "/d", "--mount", "/m", "--port", "8002", "--lan-interface", "enp2s0"],),
    )
    runner.start()
    try:
        assert tailscale_bound.wait(2), "Tailscale socket waited for the LAN address"
    finally:
        lan_has_address.set()
        runner.join(2)
    assert not runner.is_alive()


# ==================== systemd installer ====================

@pytest.mark.parametrize("library, expected_flag", [
    ("/mnt/data-drive/Music/Library/", "--library /mnt/data-drive/Music/Library"),
    ("file:///srv/Music/", "--library /srv/Music"),
    ("smb://user:pw@host/Shared/Music/", None),
])
def test_service_unit_library_flag(monkeypatch, library, expected_flag):
    from settings import Settings
    monkeypatch.setattr(Settings, "local_library_dir_str", library)

    unit = sharing._service_unit("/usr/bin/python3")

    if expected_flag:
        assert expected_flag in unit
    else:
        assert "--library" not in unit and "smb://" not in unit


@pytest.mark.parametrize("lan_interface, expected_flag", [("enp2s0", "--lan-interface enp2s0"), ("", None)])
def test_service_unit_lan_interface_flag(monkeypatch, lan_interface, expected_flag):
    monkeypatch.setattr(sharing, "SHARING_LAN_INTERFACE", lan_interface)

    unit = sharing._service_unit("/usr/bin/python3")

    if expected_flag:
        assert expected_flag in unit
    else:
        assert "--lan-interface" not in unit


def test_service_unit_vpn_and_address_flags(monkeypatch):
    # The defaults add nothing, so a unit written before these settings stays the same
    unit = sharing._service_unit("/usr/bin/python3")
    assert "--no-tailscale" not in unit and "--vpn-interface" not in unit and "--address" not in unit

    monkeypatch.setattr(sharing, "SHARING_VPN", "off")
    monkeypatch.setattr(sharing, "SHARING_ADDRESSES", ["198.51.100.7", "0.0.0.0"])
    unit = sharing._service_unit("/usr/bin/python3")
    assert " --no-tailscale" in unit and "--vpn-interface" not in unit
    assert "--address 198.51.100.7 --address 0.0.0.0" in unit

    # Another provider's VPN replaces Tailscale
    monkeypatch.setattr(sharing, "SHARING_VPN", "wg0")
    unit = sharing._service_unit("/usr/bin/python3")
    assert " --no-tailscale --vpn-interface wg0" in unit


def test_settings_read_the_vpn_and_addresses():
    from settings import listen_addresses, vpn_choice

    assert vpn_choice("") == vpn_choice(" Tailscale ") == "tailscale"
    assert vpn_choice("OFF") == "off" and vpn_choice("wg0") == "wg0"
    assert listen_addresses(" 198.51.100.7, ,10.8.0.2 ") == ["198.51.100.7", "10.8.0.2"]


def test_install_is_idempotent_and_writes_expected_units(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sharing, "SYSTEMD_USER_DIR", tmp_path / "systemd" / "user")
    monkeypatch.setattr(sharing, "_systemctl", lambda *args: calls.append(args))
    monkeypatch.setattr(sharing, "SHARING_DRIVE_MOUNT", tmp_path / "data-drive")

    sharing.install_sharing_service()
    first = sorted((p.name, p.read_text()) for p in sharing.SYSTEMD_USER_DIR.iterdir())
    sharing.install_sharing_service()
    second = sorted((p.name, p.read_text()) for p in sharing.SYSTEMD_USER_DIR.iterdir())

    assert first == second
    assert [name for name, _ in second] == ["musicdatabase-inbox.service", "musicdatabase-inbox.timer",
                                           "musicdatabase-share.service", "musicdatabase-share.timer"]
    inbox_service, inbox_timer, service, timer = (text for _, text in second)
    assert f"--port {sharing.SHARING_HTTP_PORT}" in service
    assert f"--dir {sharing.SHARING_DIR}" in service
    assert f"--inbox {sharing.PHONE_INBOX_DIR}" in service
    assert f"--users {sharing.USERS_DIR}" in service
    assert f"--mood-changes {sharing.GLOBAL_MOOD_CHANGES_DIR}" in service
    assert "--inbox-limit-bytes 104857600" in service
    assert f"--mount {sharing.SHARING_DRIVE_MOUNT}" in service
    assert str(sharing.SERVER_SCRIPT) in service
    assert "Restart=always" in service
    assert "OnStartupSec=1min" in timer
    assert "WantedBy=timers.target" in timer
    # The automatic request check: the venv's python (and yt-dlp on PATH), every 15 minutes
    assert f"ExecStart={sys.executable} {sharing.INBOX_AUTO_SCRIPT}" in inbox_service
    assert f"Environment=PATH={Path(sys.executable).parent}:" in inbox_service
    assert "Type=oneshot" in inbox_service
    assert "OnUnitActiveSec=15min" in inbox_timer and "WantedBy=timers.target" in inbox_timer
    assert calls == [
        ("daemon-reload",), ("enable", "musicdatabase-share.timer"), ("restart", "musicdatabase-share.service"),
        ("enable", "--now", "musicdatabase-inbox.timer"),
    ] * 2


# ==================== playlists ====================

@pytest.fixture
def playlist_server(serve, share_dir, tmp_path):
    """--users with 'ola_k' (a migrated user.db) and 'bob' (no user.db);
    the shared music.db has song 4, with 2 merged into it and 5 deleted."""
    from utils.database import account_auth
    from utils.database.migrations import initialize_user_db

    users_dir = tmp_path / "Users"
    for name in ("ola_k", "bob"):
        (users_dir / name).mkdir(parents=True)
        (users_dir / name / account_auth.ACCOUNT_FILENAME).write_text(f'{{"username": "{name}"}}')
    initialize_user_db(users_dir / "ola_k" / "user.db")
    tokens = {name: account_auth.issue_token(str(users_dir), name) for name in ("ola_k", "bob")}
    music = sqlite3.connect(share_dir / "music.db")
    music.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY)")
    music.execute("CREATE TABLE song_id_changes (id INTEGER PRIMARY KEY, old_song_id INTEGER, new_song_id INTEGER)")
    music.execute("INSERT INTO songs VALUES (4)")
    music.executemany("INSERT INTO song_id_changes VALUES (?, ?, ?)", [(1, 2, 4), (2, 5, None)])
    music.commit()
    music.close()

    def start(mount="/", **kw):
        handler = sharing_server.make_handler(str(share_dir), mount, users_dir=str(users_dir), **kw)
        return serve(share_dir, mount, handler=handler)

    return start, users_dir, tokens


def _status_of_oversized_body(method, url, token, limit):
    """The answer to a body of limit + 1 bytes, announced but never sent: the
    server refuses it on the header alone and closes, so a client still
    sending a real one could get a broken pipe instead of the 413."""
    import http.client
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port)
    try:
        conn.putrequest(method, parts.path)
        conn.putheader("Authorization", f"Bearer {token}")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(limit + 1))
        conn.endheaders()
        return conn.getresponse().status
    finally:
        conn.close()


def _call(method, url, token=None, payload=None, raw=None):
    data = raw if raw is not None else (None if payload is None else json.dumps(payload).encode())
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body.startswith(b"{") else body
    except urllib.error.HTTPError as err:
        body = err.read()
        return err.code, json.loads(body) if body.startswith(b"{") else body


UID = "8f0c1a2b-0000-4000-8000-000000000001"


def _body(base_revision=0, updated_at=1000, **extra):
    return {"name": "Mix", "source": "youtube", "source_id": "PL1", "source_owner": "Someone",
            "created_at": 900, "updated_at": updated_at, "base_revision": base_revision,
            "tracks": [{"song_id": 2, "title": "merged", "artist": "A", "youtube_video_id": "v",
                        "spotify_track_id": None, "youtube_removed": False, "duration_s": 215},
                       {"song_id": 5, "title": "deleted"}, {"song_id": 4, "title": "kept"}], **extra}


def test_playlist_round_trip(playlist_server):
    start, users_dir, tokens = playlist_server
    base = f"{start()}/users/ola_k/playlists"
    token = tokens["ola_k"]

    assert _call("GET", base, token) == (200, {"revision": 0, "playlists": []})
    status, stored = _call("PUT", f"{base}/{UID}", token, _body())
    assert status == 200 and stored["revision"] == 1 and stored["uid"] == UID
    # Song ids resolved against the shared music.db: merged -> kept, deleted -> NULL
    assert [(t["song_id"], t["title"]) for t in stored["tracks"]] == [(4, "merged"), (None, "deleted"), (4, "kept")]
    assert stored["tracks"][0]["duration_s"] == 215 and stored["source_owner"] == "Someone"
    assert stored["excluded_source_tracks"] == []
    assert _call("GET", f"{base}?since=0", token) == (200, {"revision": 1, "playlists": [stored]})
    assert _call("GET", f"{base}?since=1", token) == (200, {"revision": 1, "playlists": []})
    status, stored = _call("PUT", f"{base}/{UID}", token, _body(1, 1100, excluded_source_tracks=["vX"]))
    assert status == 200 and stored["excluded_source_tracks"] == ["vX"]
    assert _call("GET", f"{base}?since=1", token) == (200, {"revision": 2, "playlists": [stored]})

    status, deleted = _call("DELETE", f"{base}/{UID}?base_revision=2&deleted_at=1791839796631", token)
    assert (status, deleted) == (200, {"uid": UID, "revision": 3, "updated_at": 1791839796631, "deleted": True})
    (archived,) = (users_dir / "ola_k" / "archive" / "playlists").iterdir()
    assert json.loads(archived.read_text())["tracks"][2]["title"] == "kept"
    assert _call("GET", f"{base}?since=2", token) == (200, {"revision": 3, "playlists": [deleted]})
    # Deleting again, or something never seen, is done
    assert _call("DELETE", f"{base}/{UID}?base_revision=0&deleted_at=1", token) == (200, deleted)
    db = sqlite3.connect(users_dir / "ola_k" / "user.db")
    assert db.execute("SELECT song_ids_checked_to FROM playlists").fetchone() == (None,)


@pytest.mark.parametrize("collection", ["playlists", "moods", "folders", "saved-songs", "later-links"])
def test_only_get_put_and_delete_reach_a_synced_collection(playlist_server, collection):
    start, users_dir, tokens = playlist_server
    base = f"{start()}/users/ola_k/playlists"
    token = tokens["ola_k"]
    assert _call("PUT", f"{base}/{UID}", token, _body())[0] == 200

    url = f"{base.rsplit('/', 1)[0]}/{collection}/{UID}?base_revision=1&deleted_at=1791839796631"
    status, _ = _call("POST", url, token, payload={})
    assert status == 405
    assert _call("GET", f"{base}?since=0", token)[1]["playlists"][0]["deleted"] is False


def _raw_status(url, method="GET", headers=None):
    """The status line of the answer, or None when the server closed without one."""
    import http.client
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.putrequest(method, f"{parts.path}?{parts.query}" if parts.query else parts.path)
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.endheaders()
        return conn.getresponse().status
    except (http.client.RemoteDisconnected, ConnectionError):
        return None
    finally:
        conn.close()


@pytest.mark.parametrize("since", ["%C2%B2", "99999999999999999999", "-1", "1.5"])
def test_a_malformed_number_in_a_query_is_400(playlist_server, since):
    start, users_dir, tokens = playlist_server
    url = f"{start()}/users/ola_k/playlists?since={since}"
    assert _raw_status(url, headers={"Authorization": f"Bearer {tokens['ola_k']}"}) == 400


def test_a_malformed_content_length_is_411(playlist_server):
    start, users_dir, tokens = playlist_server
    url = f"{start()}/users/ola_k/playlists/{UID}"
    headers = {"Authorization": f"Bearer {tokens['ola_k']}", "Content-Length": "²"}
    assert _raw_status(url, "PUT", headers) == 411


def test_an_unexpected_failure_still_gets_an_answer(playlist_server):
    start, users_dir, tokens = playlist_server
    base = f"{start()}/users/ola_k/playlists"
    token = tokens["ola_k"]
    assert _call("PUT", f"{base}/{UID}", token, _body())[0] == 200

    # A timestamp past the year 9999: the archive's datetime() fails
    url = f"{base}/{UID}?base_revision=1&deleted_at={10 ** 17}"
    assert _raw_status(url, "DELETE", {"Authorization": f"Bearer {token}"}) == 500


def test_playlist_conflicts_are_409_with_the_stored_playlist(playlist_server):
    start, _, tokens = playlist_server
    url = f"{start()}/users/ola_k/playlists/{UID}"
    token = tokens["ola_k"]
    _call("PUT", url, token, _body(updated_at=1000))
    _, newer = _call("PUT", url, token, _body(base_revision=1, updated_at=3000, name="Newer"))
    status, stored = _call("PUT", url, token, _body(base_revision=1, updated_at=2000, name="Older"))
    assert (status, stored) == (409, newer)
    status, stored = _call("DELETE", f"{url}?base_revision=1&deleted_at=2500", token)
    assert (status, stored) == (409, newer)
    # The resend of an applied change: 409 with what it sent
    assert _call("PUT", url, token, _body(base_revision=1, updated_at=3000, name="Newer")) == (409, newer)


def test_server_stored_playlist_remembers_the_log_position_it_was_checked_against(playlist_server):
    start, users_dir, tokens = playlist_server
    _call("PUT", f"{start()}/users/ola_k/playlists/{UID}", tokens["ola_k"], _body())
    db = sqlite3.connect(users_dir / "ola_k" / "user.db")
    assert db.execute("SELECT song_ids_checked_to FROM playlists").fetchone() == (2,)


@pytest.mark.parametrize("token_of, status", [(None, 401), ("revoked", 401), ("bob", 403)])
def test_playlists_need_that_users_token(playlist_server, token_of, status):
    start, _, tokens = playlist_server
    base = f"{start()}/users/ola_k/playlists"
    token = {"revoked": "not-a-token", None: None}.get(token_of, tokens.get(token_of))
    assert _call("GET", base, token)[0] == status
    assert _call("PUT", f"{base}/{UID}", token, _body())[0] == status
    assert _call("DELETE", f"{base}/{UID}?base_revision=0&deleted_at=1", token)[0] == status


def test_playlists_unavailable(playlist_server, tmp_path):
    start, _, tokens = playlist_server
    unmounted = start(mount=str(tmp_path / "not-a-mount-point"))
    assert _call("GET", f"{unmounted}/users/ola_k/playlists", tokens["ola_k"])[0] == 503
    # bob's user.db doesn't exist / isn't migrated: never migrated by the server
    assert _call("GET", f"{start()}/users/bob/playlists", tokens["bob"])[0] == 503


@pytest.mark.parametrize("path, payload", [
    (f"/users/ola_k/playlists/{UID}", {"name": "x"}),
    (f"/users/ola_k/playlists/{UID}", {**_body(), "base_revision": -1}),
    (f"/users/ola_k/playlists/{UID}", {**_body(), "uid": "another-uid-1"}),
    ("/users/ola_k/playlists/bad%20uid", _body()),
])
def test_bad_playlist_requests_are_400(playlist_server, path, payload):
    start, _, tokens = playlist_server
    assert _call("PUT", f"{start()}{path}", tokens["ola_k"], payload)[0] == 400


def test_bad_playlist_parameters_and_bodies(playlist_server):
    start, _, tokens = playlist_server
    base = start()
    url = f"{base}/users/ola_k/playlists"
    token = tokens["ola_k"]
    assert _call("PUT", f"{url}/{UID}", token, raw=b"{not json")[0] == 400
    assert _call("PUT", f"{url}/{UID}", token, _body(excluded_source_tracks="v1"))[0] == 400
    assert _call("GET", f"{url}?since=-1", token)[0] == 400
    assert _call("DELETE", f"{url}/{UID}?deleted_at=5", token)[0] == 400
    assert _call("GET", f"{url}/{UID}", token)[0] == 404
    assert _call("DELETE", url, token)[0] == 404
    small = start(max_playlist_bytes=100)
    assert _call("PUT", f"{small}/users/ola_k/playlists/{UID}", token, _body())[0] == 413

    import http.client
    host, port = base.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port))
    conn.putrequest("PUT", f"/users/ola_k/playlists/{UID}")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    assert conn.getresponse().status == 411
    conn.close()


def test_without_users_there_are_no_playlists(inbox_server):
    start, _ = inbox_server
    assert _call("GET", f"{start()}/users/ola_k/playlists")[0] == 404


# ==================== permissions and moods ====================

@pytest.fixture
def mood_server(playlist_server, share_dir, tmp_path):
    """playlist_server plus a shared tag.db with global mood 1 'Siłownia' and
    marker 2 'spellchecked', and a --mood-changes folder; 'ola_k' may edit
    global moods, 'bob' may not."""
    start_playlists, users_dir, tokens = playlist_server
    tag = sqlite3.connect(share_dir / "tag.db")
    tag.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE, "
                "kind VARCHAR NOT NULL DEFAULT 'marker')")
    tag.executemany("INSERT INTO tags VALUES (?, ?, ?)", [(1, "Siłownia", "mood"), (2, "spellchecked", "marker")])
    tag.commit()
    tag.close()
    (users_dir / "ola_k" / "account.json").write_text('{"username": "ola_k", "permissions": ["edit_global_moods"]}')
    changes = tmp_path / "MoodChanges"

    def start(mount="/", **kw):
        kw.setdefault("mood_changes_dir", str(changes))
        return start_playlists(mount=mount, **kw)

    return start, users_dir, tokens, changes


def test_login_and_profile_carry_the_permissions(login_server):
    start, users_dir, _ = login_server
    base = start()
    account = users_dir / "ola_k" / "account.json"
    status, _, body = _login(base)
    assert status == 200 and json.loads(body)["permissions"] == []
    token = json.loads(body)["token"]
    account.write_text(account.read_text()[:-1] + ', "permissions": ["edit_global_moods"]}')
    assert _call("GET", f"{base}/users/ola_k", token) == (
        200, {"username": "ola_k", "permissions": ["edit_global_moods"]})
    assert json.loads(_login(base)[2])["permissions"] == ["edit_global_moods"]
    # Revoked: the next GET shows it, no new login needed
    account.write_text(account.read_text().replace('"edit_global_moods"', ""))
    assert _call("GET", f"{base}/users/ola_k", token) == (200, {"username": "ola_k", "permissions": []})
    assert _call("GET", f"{base}/users/ola_k")[0] == 401
    assert _call("GET", f"{base}/users/someone_else", token)[0] == 403


def _mood_body(base_revision=0, updated_at=1000, **extra):
    return {"name": "Siłownia", "created_at": 900, "updated_at": updated_at, "base_revision": base_revision,
            "song_ids": [2, 5, 4], **extra}


def test_mood_round_trip(mood_server):
    start, users_dir, tokens, _ = mood_server
    base = f"{start()}/users/ola_k/moods"
    token = tokens["ola_k"]

    assert _call("GET", base, token) == (200, {"revision": 0, "moods": []})
    status, stored = _call("PUT", f"{base}/{UID}", token, _mood_body())
    # 2 merged into 4, 5 deleted: a mood holds only songs that exist
    assert (status, stored) == (200, {"uid": UID, "revision": 1, "name": "Siłownia", "created_at": 900,
                                      "updated_at": 1000, "deleted": False, "song_ids": [4], "cover": None})
    assert _call("GET", f"{base}?since=0", token) == (200, {"revision": 1, "moods": [stored]})
    db = sqlite3.connect(users_dir / "ola_k" / "user.db")
    assert db.execute("SELECT song_ids_checked_to FROM tags").fetchone() == (2,)

    # Another phone made a mood with the same name: merged, the existing uid comes back
    other_uid = "8f0c1a2b-0000-4000-8000-000000000002"
    status, merged = _call("PUT", f"{base}/{other_uid}", token, _mood_body(song_ids=[4]))
    assert status == 200 and merged["uid"] == UID

    stale = _call("PUT", f"{base}/{UID}", token, _mood_body(base_revision=1, updated_at=500, name="Old"))
    assert stale == (409, merged)
    status, deleted = _call("DELETE", f"{base}/{UID}?base_revision=2&deleted_at=5000", token)
    assert (status, deleted) == (200, {"uid": UID, "revision": 3, "updated_at": 5000, "deleted": True})
    assert len(list((users_dir / "ola_k" / "archive" / "moods").iterdir())) == 1
    # Moods don't touch the playlists
    assert _call("GET", f"{start()}/users/ola_k/playlists", token) == (200, {"revision": 0, "playlists": []})


@pytest.mark.parametrize("token_of, status", [(None, 401), ("revoked", 401), ("bob", 403)])
def test_moods_need_that_users_token(mood_server, token_of, status):
    start, _, tokens, _ = mood_server
    base = f"{start()}/users/ola_k/moods"
    token = {"revoked": "not-a-token", None: None}.get(token_of, tokens.get(token_of))
    assert _call("GET", base, token)[0] == status
    assert _call("PUT", f"{base}/{UID}", token, _mood_body())[0] == status
    assert _call("DELETE", f"{base}/{UID}?base_revision=0&deleted_at=1", token)[0] == status


def test_bad_or_unavailable_mood_requests(mood_server, tmp_path):
    start, _, tokens, _ = mood_server
    base = start()
    url = f"{base}/users/ola_k/moods"
    token = tokens["ola_k"]
    assert _call("PUT", f"{url}/{UID}", token, {"name": "x"})[0] == 400
    assert _call("PUT", f"{url}/{UID}", token, _mood_body(song_ids=[0]))[0] == 400
    assert _call("PUT", f"{url}/{UID}", token, _mood_body(uid="another-uid-1"))[0] == 400
    assert _call("PUT", f"{url}/bad%20uid", token, _mood_body())[0] == 400
    assert _call("DELETE", f"{url}/{UID}?deleted_at=5", token)[0] == 400
    assert _call("GET", f"{url}/{UID}", token)[0] == 404
    assert _call("GET", f"{base}/users/bob/moods", tokens["bob"])[0] == 503  # no user.db
    unmounted = start(mount=str(tmp_path / "not-a-mount-point"))
    assert _call("GET", f"{unmounted}/users/ola_k/moods", token)[0] == 503


def test_permitted_user_changes_a_global_mood(mood_server):
    from utils.database import global_mood_log

    start, _, tokens, changes = mood_server
    base = start()
    token = tokens["ola_k"]
    assert _call("PUT", f"{base}/global-moods/1/songs/2", token) == (
        202, {"mood_id": 1, "song_id": 4, "action": "add"})  # 2 was merged into 4
    assert _call("DELETE", f"{base}/global-moods/1/songs/4", token)[0] == 202
    records = [r for _, r in global_mood_log.pending_changes(changes)]
    assert [(r["user"], r["action"], r["mood_id"], r["mood_name"], r["song_id"], r["requested_song_id"])
            for r in records] == [("ola_k", "add", 1, "Siłownia", 4, 2), ("ola_k", "remove", 1, "Siłownia", 4, 4)]
    assert records[0]["song_ids_checked_to"] == 2


def test_global_mood_changes_are_checked(mood_server, tmp_path):
    start, users_dir, tokens, changes = mood_server
    base = start()
    token = tokens["ola_k"]
    # No permission: 403 with a body the phone can tell from "another user's token"
    assert _call("PUT", f"{base}/global-moods/1/songs/4", tokens["bob"]) == (
        403, {"error": "permission", "permission": "edit_global_moods"})
    assert _call("PUT", f"{base}/global-moods/1/songs/4")[0] == 401
    assert _call("PUT", f"{base}/global-moods/2/songs/4", token) == (404, {"error": "mood", "mood_id": 2})
    assert _call("PUT", f"{base}/global-moods/9/songs/4", token)[0] == 404
    assert _call("PUT", f"{base}/global-moods/1/songs/5", token) == (404, {"error": "song", "song_id": 5})
    assert _call("PUT", f"{base}/global-moods/1/songs/77", token)[0] == 404
    assert _call("PUT", f"{base}/global-moods/x/songs/4", token)[0] == 400
    assert _call("PUT", f"{base}/global-moods/1/songs/0", token)[0] == 400
    assert _call("GET", f"{base}/global-moods/1/songs/4", token)[0] == 404
    unmounted = start(mount=str(tmp_path / "not-a-mount-point"))
    assert _call("PUT", f"{unmounted}/global-moods/1/songs/4", token)[0] == 503
    assert _call("PUT", f"{start(mood_changes_dir=None)}/global-moods/1/songs/4", token)[0] == 404
    assert not (changes / "pending").exists() or not list((changes / "pending").iterdir())
    # Revoked: refused on the next request
    (users_dir / "ola_k" / "account.json").write_text('{"username": "ola_k", "permissions": []}')
    assert _call("PUT", f"{base}/global-moods/1/songs/4", token)[0] == 403


# ==================== folders ====================

FOLDER_ITEMS = [{"type": "playlist", "uid": "a1c4"}, {"type": "mood", "uid": "77de"},
                {"type": "global_mood", "id": 5}, {"type": "future_kind", "anything": [1, {"x": "ą"}]}]


def _folder_body(base_revision=0, updated_at=1000, **extra):
    return {"name": "Do auta", "created_at": 900, "updated_at": updated_at, "base_revision": base_revision,
            "deleted": False, "items": FOLDER_ITEMS, **extra}


def test_folder_round_trip(playlist_server):
    start, _, tokens = playlist_server
    base = f"{start()}/users/ola_k/folders"
    token = tokens["ola_k"]

    assert _call("GET", base, token) == (200, {"revision": 0, "folders": []})
    status, stored = _call("PUT", f"{base}/k3x", token, _folder_body())  # a short base-36 uid
    # Items verbatim: refs to nothing the server knows, and an unknown type
    assert (status, stored) == (200, {"uid": "k3x", "revision": 1, "name": "Do auta", "created_at": 900,
                                      "updated_at": 1000, "deleted": False, "items": FOLDER_ITEMS})
    assert _call("GET", f"{base}?since=0", token) == (200, {"revision": 1, "folders": [stored]})
    assert _call("GET", f"{base}?since=1", token) == (200, {"revision": 1, "folders": []})

    stale = _call("PUT", f"{base}/k3x", token, _folder_body(updated_at=500, name="Old"))
    assert stale == (409, stored)
    assert _call("DELETE", f"{base}/k3x?base_revision=0&deleted_at=600", token) == (409, stored)
    status, deleted = _call("DELETE", f"{base}/k3x?base_revision=1&deleted_at=5000", token)
    assert (status, deleted) == (200, {"uid": "k3x", "revision": 2, "updated_at": 5000, "deleted": True})
    assert _call("GET", f"{base}?since=1", token) == (200, {"revision": 2, "folders": [deleted]})
    # Folders don't touch playlists
    assert _call("GET", f"{start()}/users/ola_k/playlists", token) == (200, {"revision": 0, "playlists": []})


@pytest.mark.parametrize("token_of, status", [(None, 401), ("revoked", 401), ("bob", 403)])
def test_folders_need_that_users_token(playlist_server, token_of, status):
    start, _, tokens = playlist_server
    base = f"{start()}/users/ola_k/folders"
    token = {"revoked": "not-a-token", None: None}.get(token_of, tokens.get(token_of))
    assert _call("GET", base, token)[0] == status
    assert _call("PUT", f"{base}/k3x", token, _folder_body())[0] == status
    assert _call("DELETE", f"{base}/k3x?base_revision=0&deleted_at=1", token)[0] == status


def test_bad_or_unavailable_folder_requests(playlist_server, tmp_path):
    start, _, tokens = playlist_server
    base = start()
    url = f"{base}/users/ola_k/folders"
    token = tokens["ola_k"]
    assert _call("PUT", f"{url}/k3x", token, {"name": "x"})[0] == 400
    assert _call("PUT", f"{url}/k3x", token, _folder_body(items=["a1c4"]))[0] == 400
    assert _call("PUT", f"{url}/k3x", token, _folder_body(uid="another"))[0] == 400
    assert _call("PUT", f"{url}/k3x", token, raw=b"not json")[0] == 400
    assert _call("PUT", f"{url}/bad_uid", token, _folder_body())[0] == 400
    assert _call("PUT", f"{url}/{'x' * 65}", token, _folder_body())[0] == 400
    assert _status_of_oversized_body("PUT", f"{url}/k3x", token, sharing_server.FOLDER_MAX_BODY_BYTES) == 413
    assert _call("DELETE", f"{url}/k3x?deleted_at=5", token)[0] == 400
    assert _call("GET", f"{url}/k3x", token)[0] == 404
    assert _call("GET", f"{base}/users/bob/folders", tokens["bob"])[0] == 503  # no user.db
    unmounted = start(mount=str(tmp_path / "not-a-mount-point"))
    assert _call("GET", f"{unmounted}/users/ola_k/folders", token)[0] == 503


# ==================== saved songs and links ====================

SAVED_UID = "3f2b0c" + "0" * 34
SAVED_SONG = {"song_id": 1234, "title": "Группа крови", "artist": "Кино", "video_id": "abcdefghijk",
              "playlist_video_id": "pv", "spotify_track_id": "sp"}


def _saved_song_body(base_revision=0, updated_at=1000, **extra):
    return {"list": "liked", "added_at": updated_at, "updated_at": updated_at, "deleted": False,
            "base_revision": base_revision, "song": SAVED_SONG, **extra}


def test_saved_songs_round_trip(playlist_server):
    start, _, tokens = playlist_server
    base = f"{start()}/users/ola_k/saved-songs"
    token = tokens["ola_k"]

    assert _call("GET", base, token) == (200, {"revision": 0, "songs": []})
    # The song verbatim (id 1234 isn't in the shared music.db), an unknown key kept
    status, stored = _call("PUT", f"{base}/{SAVED_UID}", token, _saved_song_body(future={"x": "ą"}))
    assert (status, stored) == (200, {"list": "liked", "added_at": 1000, "song": SAVED_SONG, "future": {"x": "ą"},
                                      "uid": SAVED_UID, "revision": 1, "updated_at": 1000, "deleted": False})
    assert _call("GET", f"{base}?since=0", token) == (200, {"revision": 1, "songs": [stored]})
    assert _call("GET", f"{base}?since=1", token) == (200, {"revision": 1, "songs": []})

    # base_revision 0 on an existing uid: older -> 409, newer -> applied
    assert _call("PUT", f"{base}/{SAVED_UID}", token, _saved_song_body(updated_at=500)) == (409, stored)
    assert _call("DELETE", f"{base}/{SAVED_UID}?base_revision=0&deleted_at=600", token) == (409, stored)
    status, deleted = _call("DELETE", f"{base}/{SAVED_UID}?base_revision=1&deleted_at=5000", token)
    assert (status, deleted) == (200, {"uid": SAVED_UID, "revision": 2, "updated_at": 5000, "deleted": True})
    assert _call("GET", f"{base}?since=1", token) == (200, {"revision": 2, "songs": [deleted]})
    status, revived = _call("PUT", f"{base}/{SAVED_UID}", token, _saved_song_body(updated_at=7000))
    assert status == 200 and revived["revision"] == 3 and revived["deleted"] is False
    # Saved songs don't touch links or folders
    assert _call("GET", f"{start()}/users/ola_k/later-links", token) == (200, {"revision": 0, "links": []})
    assert _call("GET", f"{start()}/users/ola_k/folders", token) == (200, {"revision": 0, "folders": []})


def test_later_links_round_trip(playlist_server):
    start, _, tokens = playlist_server
    base = f"{start()}/users/ola_k/later-links"
    token = tokens["ola_k"]
    link = {"url": "https://youtu.be/abcdefghijk", "service": "youtube", "kind": "Film z YouTube", "person": "Ola",
            "saved_at": 900, "opened_at": None, "title": None, "artist": None}
    uid = "a1c40000-0000-4000-8000-000000000001"
    status, stored = _call("PUT", f"{base}/{uid}", token, {**link, "updated_at": 1000, "base_revision": 0})
    assert (status, stored) == (200, {**link, "uid": uid, "revision": 1, "updated_at": 1000, "deleted": False})
    # A DELETE of a uid never seen stores the marker
    status, deleted = _call("DELETE", f"{base}/never-seen?base_revision=0&deleted_at=2000", token)
    assert (status, deleted) == (200, {"uid": "never-seen", "revision": 2, "updated_at": 2000, "deleted": True})
    assert _call("GET", f"{base}?since=0", token) == (200, {"revision": 2, "links": [stored, deleted]})


@pytest.mark.parametrize("collection", ["saved-songs", "later-links"])
@pytest.mark.parametrize("token_of, status", [(None, 401), ("revoked", 401), ("bob", 403)])
def test_saved_items_need_that_users_token(playlist_server, collection, token_of, status):
    start, _, tokens = playlist_server
    base = f"{start()}/users/ola_k/{collection}"
    token = {"revoked": "not-a-token", None: None}.get(token_of, tokens.get(token_of))
    assert _call("GET", base, token)[0] == status
    assert _call("PUT", f"{base}/k3x", token, _saved_song_body())[0] == status
    assert _call("DELETE", f"{base}/k3x?base_revision=0&deleted_at=1", token)[0] == status


def test_bad_or_unavailable_saved_requests(playlist_server, tmp_path):
    start, _, tokens = playlist_server
    base = start()
    url = f"{base}/users/ola_k/saved-songs"
    token = tokens["ola_k"]
    assert _call("PUT", f"{url}/k3x", token, {"list": "liked"})[0] == 400
    assert _call("PUT", f"{url}/k3x", token, _saved_song_body(song="Kino"))[0] == 400
    assert _call("PUT", f"{url}/k3x", token, _saved_song_body(uid="another"))[0] == 400
    assert _call("PUT", f"{url}/k3x", token, _saved_song_body(base_revision=None))[0] == 400
    assert _call("PUT", f"{base}/users/ola_k/later-links/k3x", token, _saved_song_body())[0] == 400  # no url
    assert _call("PUT", f"{url}/k3x", token, raw=b"not json")[0] == 400
    assert _call("PUT", f"{url}/bad_uid", token, _saved_song_body())[0] == 400
    assert _call("PUT", f"{url}/{'x' * 65}", token, _saved_song_body())[0] == 400
    assert _status_of_oversized_body("PUT", f"{url}/k3x", token, sharing_server.SAVED_MAX_BODY_BYTES) == 413
    assert _call("DELETE", f"{url}/k3x?deleted_at=5", token)[0] == 400
    assert _call("GET", f"{url}/k3x", token)[0] == 404
    assert _call("GET", f"{base}/users/ola_k/saved-songs-x", token)[0] == 404
    assert _call("GET", f"{base}/users/bob/saved-songs", tokens["bob"])[0] == 503  # no user.db
    unmounted = start(mount=str(tmp_path / "not-a-mount-point"))
    assert _call("GET", f"{unmounted}/users/ola_k/later-links", token)[0] == 503


# ==================== listens ====================

def _listen(uid, listened_ms=183400, **extra):
    return {"uid": uid, "started_at": 1791839796631, "listened_ms": listened_ms, "duration_ms": 214000,
            "title": "Gruppa krovi", "artist": "Kino", "album": None, "song_id": 1234,
            "youtube_video_id": "abc123", "spotify_track_id": None, "source": "youtube",
            "playlist_uid": None, "playlist_name": None, "mood_uid": None, "global_mood_id": 5,
            "mood_name": "Do biegania", **extra}


def test_listens_round_trip(playlist_server):
    start, _, tokens = playlist_server
    url = f"{start()}/users/ola_k/listens"
    token = tokens["ola_k"]

    assert _call("GET", url, token) == (200, {"revision": 0, "listens": [], "more": False})
    plays = [_listen("a", source="car-radio", future={"x": 1}), _listen("b"), _listen("c")]
    assert _call("POST", url, token, {"listens": plays}) == (200, {"revision": 3})
    status, got = _call("GET", f"{url}?since=0", token)
    # Song ids as sent (1234 isn't in the shared music.db); unknown keys and sources kept
    assert (status, got) == (200, {"revision": 3, "more": False,
                                   "listens": [{**p, "revision": i} for i, p in enumerate(plays, 1)]})
    assert _call("GET", f"{url}?since=1&limit=1", token) == \
        (200, {"revision": 2, "listens": [{**plays[1], "revision": 2}], "more": True})
    assert _call("GET", f"{url}?since=2&limit=1", token)[1]["more"] is False
    # The song still playing, then its final time: the longer one wins
    assert _call("POST", url, token, {"listens": [_listen("b", listened_ms=200000)]}) == (200, {"revision": 4})
    assert _call("POST", url, token, {"listens": [_listen("b", listened_ms=5)]}) == (200, {"revision": 4})
    assert _call("GET", f"{url}?since=3", token)[1]["listens"] == [{**_listen("b", listened_ms=200000), "revision": 4}]
    # Listens don't touch playlists or folders
    assert _call("GET", f"{start()}/users/ola_k/folders", token) == (200, {"revision": 0, "folders": []})


@pytest.mark.parametrize("token_of, status", [(None, 401), ("revoked", 401), ("bob", 403)])
def test_listens_need_that_users_token(playlist_server, token_of, status):
    start, _, tokens = playlist_server
    url = f"{start()}/users/ola_k/listens"
    token = {"revoked": "not-a-token", None: None}.get(token_of, tokens.get(token_of))
    assert _call("GET", url, token)[0] == status
    assert _call("POST", url, token, {"listens": [_listen("a")]})[0] == status


def test_bad_or_unavailable_listen_requests(playlist_server, tmp_path):
    start, _, tokens = playlist_server
    base = start()
    url = f"{base}/users/ola_k/listens"
    token = tokens["ola_k"]
    # One bad play: 400, nothing stored
    assert _call("POST", url, token, {"listens": [_listen("a"), _listen("b", listened_ms=-1)]})[0] == 400
    assert _call("POST", url, token, {"listens": [{"uid": "c", "started_at": 1, "listened_ms": 0}]})[0] == 400
    assert _call("POST", url, token, {"listens": [_listen("bad_uid")]})[0] == 400
    assert _call("GET", url, token) == (200, {"revision": 0, "listens": [], "more": False})
    assert _call("POST", url, token, {"plays": []})[0] == 400
    assert _call("POST", url, token, raw=b"not json")[0] == 400
    assert _status_of_oversized_body("POST", url, token, sharing_server.LISTEN_MAX_BODY_BYTES) == 413
    assert _call("GET", f"{url}?since=x", token)[0] == 400
    assert _call("GET", f"{url}?limit=0", token)[0] == 400
    assert _call("GET", f"{url}/a", token)[0] == 404
    assert _call("PUT", url, token, {"listens": []})[0] == 404
    assert _call("GET", f"{base}/users/bob/listens", tokens["bob"])[0] == 503  # no user.db
    unmounted = start(mount=str(tmp_path / "not-a-mount-point"))
    assert _call("GET", f"{unmounted}/users/ola_k/listens", token)[0] == 503

    import http.client
    host, port = base.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port))
    conn.putrequest("POST", "/users/ola_k/listens")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    assert conn.getresponse().status == 411
    conn.close()


def test_without_users_there_are_no_listens(inbox_server):
    start, _ = inbox_server
    assert _call("GET", f"{start()}/users/ola_k/listens")[0] == 404
    assert _call("POST", f"{start()}/users/ola_k/listens", payload={"listens": []})[0] == 404


# ==================== covers ====================

COVER_PNG = b"\x89PNG\r\n\x1a\n" + b"cover"
COVER_SHA = hashlib.sha256(COVER_PNG).hexdigest()


def _put_blob(url, token, data, content_type="image/png"):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


def test_cover_blob_round_trip(playlist_server):
    start, users_dir, tokens = playlist_server
    base = start()
    url = f"{base}/users/ola_k/covers/{COVER_SHA}"
    token = tokens["ola_k"]
    assert _call("GET", url, token)[0] == 404
    assert _put_blob(url, token, COVER_PNG) == 201
    assert _put_blob(url, token, COVER_PNG) == 200  # a retry is harmless
    assert (users_dir / "ola_k" / "covers" / f"{COVER_SHA}.png").read_bytes() == COVER_PNG
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        assert resp.read() == COVER_PNG
        assert resp.headers["Content-Type"] == "image/png"
        assert "immutable" in resp.headers["Cache-Control"]


def test_bad_cover_blobs(playlist_server, tmp_path):
    start, _, tokens = playlist_server
    base = start()
    url = f"{base}/users/ola_k/covers/{COVER_SHA}"
    token = tokens["ola_k"]
    other = hashlib.sha256(b"other").hexdigest()
    assert _put_blob(f"{base}/users/ola_k/covers/{other}", token, COVER_PNG) == 400   # hash mismatch
    assert _put_blob(f"{base}/users/ola_k/covers/{COVER_SHA.upper()}", token, COVER_PNG) == 400
    assert _put_blob(url, token, COVER_PNG, "image/gif") == 415
    gif = b"GIF89a"
    assert _put_blob(f"{base}/users/ola_k/covers/{hashlib.sha256(gif).hexdigest()}", token, gif) == 415
    # Over 4 MiB: refused on the header alone, before the body is read
    import http.client
    host, port = base.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port))
    conn.putrequest("PUT", f"/users/ola_k/covers/{COVER_SHA}")
    conn.putheader("Content-Type", "image/png")
    conn.putheader("Content-Length", str(4 * 1024 * 1024 + 1))
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()
    assert _put_blob(url, None, COVER_PNG) == 401
    assert _put_blob(url, tokens["bob"], COVER_PNG) == 403
    assert _call("GET", url, tokens["bob"])[0] == 403
    unmounted = start(mount=str(tmp_path / "not-a-mount-point"))
    assert _put_blob(f"{unmounted}/users/ola_k/covers/{COVER_SHA}", token, COVER_PNG) == 503


def test_playlist_and_mood_covers(mood_server):
    start, users_dir, tokens, _ = mood_server
    base = start()
    token = tokens["ola_k"]
    playlist_url = f"{base}/users/ola_k/playlists/{UID}"
    # The blob first, else cover_missing
    assert _call("PUT", playlist_url, token, _body(cover=COVER_SHA)) == (
        400, {"error": "cover_missing", "cover": COVER_SHA})
    _put_blob(f"{base}/users/ola_k/covers/{COVER_SHA}", token, COVER_PNG)
    status, stored = _call("PUT", playlist_url, token, _body(cover=COVER_SHA))
    assert status == 200 and stored["cover"] == COVER_SHA
    # An app without covers keeps it; null removes it
    assert _call("PUT", playlist_url, token, _body(base_revision=1, updated_at=2000))[1]["cover"] == COVER_SHA
    assert _call("PUT", playlist_url, token, _body(base_revision=2, updated_at=3000, cover=None))[1]["cover"] is None

    mood_url = f"{base}/users/ola_k/moods/{UID}"
    status, stored = _call("PUT", mood_url, token, _mood_body(cover=COVER_SHA))
    assert status == 200 and stored["cover"] == COVER_SHA
    status, _ = _call("DELETE", f"{mood_url}?base_revision=1&deleted_at=5000", token)
    assert status == 200
    assert (users_dir / "ola_k" / "archive" / "covers" / f"{COVER_SHA}.png").exists()
    # Unreferenced now, but fresh: still there
    assert (users_dir / "ola_k" / "covers" / f"{COVER_SHA}.png").exists()


def test_global_mood_covers(mood_server):
    start, _, tokens, _ = mood_server
    base = start()
    token = tokens["ola_k"]
    url = f"{base}/users/ola_k/global-mood-covers"
    assert _call("GET", url, token) == (200, {"revision": 0, "covers": []})
    body = {"base_revision": 0, "updated_at": 1000, "cover": COVER_SHA}
    assert _call("PUT", f"{url}/1", token, body) == (400, {"error": "cover_missing", "cover": COVER_SHA})
    _put_blob(f"{base}/users/ola_k/covers/{COVER_SHA}", token, COVER_PNG)
    status, stored = _call("PUT", f"{url}/1", token, body)
    assert (status, stored) == (200, {"mood_id": 1, "revision": 1, "cover": COVER_SHA, "updated_at": 1000})
    assert _call("PUT", f"{url}/1", token, {**body, "updated_at": 500, "cover": None}) == (409, stored)
    status, removed = _call("PUT", f"{url}/1", token, {"base_revision": 1, "updated_at": 2000, "cover": None})
    assert (status, removed["cover"], removed["revision"]) == (200, None, 2)
    assert _call("GET", f"{url}?since=1", token) == (200, {"revision": 2, "covers": [removed]})
    # Any id >= 1, even one no tag.db has
    assert _call("PUT", f"{url}/777", token, {**body, "cover": None})[0] == 200
    assert _call("PUT", f"{url}/0", token, body)[0] == 400
    assert _call("PUT", f"{url}/1", token, {"base_revision": 0, "updated_at": 1})[0] == 400  # cover required
    assert _call("GET", f"{url}/1", token)[0] == 404
    assert _call("DELETE", f"{url}/1", token)[0] == 404
    assert _call("GET", url, tokens["bob"])[0] == 403
    assert _call("GET", f"{base}/users/bob/global-mood-covers", tokens["bob"])[0] == 503  # no user.db


# ==================== library files ====================

AUDIO = bytes(range(256)) * 40  # 10240 bytes


@pytest.fixture
def library_server(serve, tmp_path):
    """--library with a shared files.db: 1 = a.mp3, 2 = gone.mp3 (missing),
    3 = ../outside.mp3, 4 = link.mp3 (symlink out of the library), 5 = notes.txt,
    6 = a folder named like audio, 7 = an absolute path."""
    share = tmp_path / "share"
    share.mkdir()
    library = tmp_path / "Library"
    (library / "Artist").mkdir(parents=True)
    (library / "Artist" / "a.mp3").write_bytes(AUDIO)
    (tmp_path / "outside.mp3").write_bytes(b"secret")
    (library / "link.mp3").symlink_to(tmp_path / "outside.mp3")
    (library / "notes.txt").write_text("not audio")
    (library / "dir.mp3").mkdir()
    rows = [(1, "Artist/a.mp3"), (2, "Artist/gone.mp3"), (3, "../outside.mp3"), (4, "link.mp3"),
            (5, "notes.txt"), (6, "dir.mp3"), (7, str(tmp_path / "outside.mp3"))]
    conn = sqlite3.connect(share / "files.db")
    with conn:
        conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, size INTEGER, song_id INTEGER)")
        conn.executemany("INSERT INTO files (id, path, size, song_id) VALUES (?, ?, 1, 1)", rows)
    conn.close()

    def start(mount: str = "/", library_dir=library):
        handler = sharing_server.make_handler(str(share), mount, library_dir=str(library_dir) if library_dir else None)
        return serve(share, mount, handler=handler)

    return start, share, library


def _get(url: str, method: str = "GET", headers: dict | None = None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method, headers=headers or {})) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


@pytest.mark.parametrize("header, expected", [
    (None, None),
    ("bytes=0-99", (0, 99)),
    ("bytes=100-", (100, 999)),
    ("bytes=-100", (900, 999)),
    ("bytes=-5000", (0, 999)),
    ("bytes=990-5000", (990, 999)),
    ("bytes=0-0", (0, 0)),
    ("BYTES = 5-6", (5, 6)),
    ("bytes=0-9,20-29", None),   # several ranges: the whole file
    ("bytes=9-0", None),         # malformed: ignored
    ("bytes=-", None),
    ("bytes=a-b", None),
    ("bytes=١-٢", None),         # non-ASCII digits
    ("items=0-9", None),
])
def test_parse_byte_range(header, expected):
    assert sharing_server.parse_byte_range(header, 1000) == expected


@pytest.mark.parametrize("header, size", [("bytes=1000-", 1000), ("bytes=1000-2000", 1000), ("bytes=-0", 1000),
                                          ("bytes=0-", 0), ("bytes=-5", 0)])
def test_parse_byte_range_past_the_end(header, size):
    with pytest.raises(sharing_server.RangeNotSatisfiable):
        sharing_server.parse_byte_range(header, size)


def test_audio_extensions_match_the_app():
    from config.constants import LIBRARY_AUDIO_EXTENSIONS
    assert sharing_server.LIBRARY_AUDIO_EXTENSIONS == LIBRARY_AUDIO_EXTENSIONS


def test_library_file_whole(library_server):
    start, _, library = library_server
    base = start()

    status, headers, body = _get(f"{base}/library/1")

    assert (status, body) == (200, AUDIO)
    assert headers["Content-Type"] == "audio/mpeg"
    assert headers["Content-Length"] == str(len(AUDIO))
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Cache-Control"] == "no-cache"
    assert headers["Last-Modified"]
    assert "Content-Range" not in headers


def test_library_file_head_sends_no_body(library_server):
    base = library_server[0]()

    status, headers, body = _get(f"{base}/library/1", method="HEAD")

    assert (status, body) == (200, b"")
    assert headers["Content-Length"] == str(len(AUDIO))
    status, headers, body = _get(f"{base}/library/1", method="HEAD", headers={"Range": "bytes=0-99"})
    assert (status, body, headers["Content-Length"]) == (206, b"", "100")


@pytest.mark.parametrize("header, first, last", [("bytes=0-99", 0, 99), ("bytes=10000-", 10000, 10239),
                                                 ("bytes=-40", 10200, 10239), ("bytes=10200-99999", 10200, 10239)])
def test_library_file_range(library_server, header, first, last):
    base = library_server[0]()

    status, headers, body = _get(f"{base}/library/1", headers={"Range": header})

    assert status == 206
    assert body == AUDIO[first:last + 1]
    assert headers["Content-Range"] == f"bytes {first}-{last}/{len(AUDIO)}"
    assert headers["Content-Length"] == str(last - first + 1)


def test_library_file_several_ranges_get_the_whole_file(library_server):
    base = library_server[0]()

    status, _, body = _get(f"{base}/library/1", headers={"Range": "bytes=0-9,20-29"})

    assert (status, body) == (200, AUDIO)


def test_library_file_range_past_the_end_is_416(library_server):
    base = library_server[0]()

    status, headers, _ = _get(f"{base}/library/1", headers={"Range": f"bytes={len(AUDIO)}-"})

    assert status == 416
    assert headers["Content-Range"] == f"bytes */{len(AUDIO)}"


@pytest.mark.parametrize("path", [
    "/library/2",       # file gone
    "/library/3",       # ".." out of the library
    "/library/4",       # symlink out of the library
    "/library/5",       # not audio
    "/library/6",       # a folder
    "/library/7",       # absolute path
    "/library/99",      # unknown id
    "/library/abc", "/library/0", "/library/01", "/library/-1", "/library/1/", "/library/",
    "/library/1/../2", "/library/%31",
])
def test_library_file_404(library_server, path):
    base = library_server[0]()

    status, _, body = _get(base + path)

    assert status == 404
    assert b"secret" not in body


def test_library_path_escaping_the_root_is_never_opened(library_server, monkeypatch):
    _, share, library = library_server
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: opened.append(p) or real_open(p, *a, **k))

    for file_id in (3, 4, 7):
        assert sharing_server.library_file_path(str(share / "files.db"), str(library), file_id) is None
    assert opened == []


def test_library_without_library_flag_is_404(library_server):
    base = library_server[0](library_dir=None)

    assert _get(f"{base}/library/1")[0] == 404


def test_library_drive_unmounted_or_folder_missing_is_503(library_server, tmp_path):
    start, _, _ = library_server
    assert _get(f"{start(mount=str(tmp_path / 'not-a-mount-point'))}/library/1")[0] == 503
    assert _get(f"{start(library_dir=tmp_path / 'no-library')}/library/1")[0] == 503


def test_library_without_shared_files_db_is_404(library_server):
    start, share, _ = library_server
    (share / "files.db").unlink()

    assert _get(f"{start()}/library/1")[0] == 404


def test_library_client_hanging_up_mid_body_leaves_the_server_serving(library_server, monkeypatch, capsys):
    import socket
    from urllib.parse import urlsplit
    monkeypatch.setattr(sharing_server, "LIBRARY_CHUNK_BYTES", 16)
    base = library_server[0]()
    url = urlsplit(base)

    for _ in range(3):
        sock = socket.create_connection((url.hostname, url.port))
        sock.sendall(b"GET /library/1 HTTP/1.0\r\n\r\n")
        sock.recv(64)
        # RST instead of FIN, like a player dropping the stream
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        sock.close()

    assert _get(f"{base}/library/1")[2] == AUDIO
    assert "Traceback" not in capsys.readouterr().err
