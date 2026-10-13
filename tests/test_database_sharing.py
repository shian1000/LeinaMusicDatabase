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
    mount = tmp_path / "T7"
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
    ("192.0.2.13\n", None),  # not a Tailscale (CGNAT) address
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
    "2: enp2s0    inet 192.0.2.13/24 brd 192.0.2.255 scope global dynamic noprefixroute enp2s0\\"
    "       valid_lft 2944sec preferred_lft 2944sec\n"
)


@pytest.mark.parametrize("stdout, expected", [
    (IP_ADDR_ENP2S0, "192.0.2.13"),
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

    assert sharing_server.bind_when_ready(object, 8002, lambda: "192.0.2.13", "LAN") == ("192.0.2.13", 8002)


class _FakeServer:
    def __init__(self, address, port):
        self.server_address = (address, port)

    def serve_forever(self):
        pass


def _run_main(monkeypatch, extra_args):
    bound = []
    addresses = {"Tailscale": "100.64.0.10", "LAN enp2s0": "192.0.2.13"}

    def fake_bind(handler, port, get_address, label):
        bound.append(label)
        return _FakeServer(addresses[label], port)

    monkeypatch.setattr(sharing_server, "bind_when_ready", fake_bind)
    sharing_server.main(["--dir", "/d", "--mount", "/m", "--port", "8002", *extra_args])
    return bound


def test_without_lan_interface_only_tailscale_is_served(monkeypatch, capsys):
    assert _run_main(monkeypatch, []) == ["Tailscale"]
    assert [label for label, _ in sharing_server.address_sources(None)] == ["Tailscale"]
    assert "192.0.2.13" not in capsys.readouterr().out


def test_lan_interface_adds_a_second_socket(monkeypatch, capsys):
    assert sorted(_run_main(monkeypatch, ["--lan-interface", "enp2s0"])) == ["LAN enp2s0", "Tailscale"]
    out = capsys.readouterr().out
    assert "http://100.64.0.10:8002/" in out
    assert "http://192.0.2.13:8002/" in out


def test_lan_bind_waits_independently_of_tailscale(monkeypatch):
    """A LAN interface without an address must not hold up the Tailscale socket."""
    lan_has_address = threading.Event()
    tailscale_bound = threading.Event()
    monkeypatch.setattr(sharing_server, "tailscale_ipv4", lambda: "100.64.0.10")
    monkeypatch.setattr(sharing_server, "interface_ipv4", lambda name: "192.0.2.13" if lan_has_address.is_set() else None)
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

@pytest.mark.parametrize("lan_interface, expected_flag", [("enp2s0", "--lan-interface enp2s0"), ("", None)])
def test_service_unit_lan_interface_flag(monkeypatch, lan_interface, expected_flag):
    monkeypatch.setattr(sharing, "SHARING_LAN_INTERFACE", lan_interface)

    unit = sharing._service_unit("/usr/bin/python3")

    if expected_flag:
        assert expected_flag in unit
    else:
        assert "--lan-interface" not in unit


def test_install_is_idempotent_and_writes_expected_units(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sharing, "SYSTEMD_USER_DIR", tmp_path / "systemd" / "user")
    monkeypatch.setattr(sharing, "_systemctl", lambda *args: calls.append(args))

    sharing.install_sharing_service()
    first = sorted((p.name, p.read_text()) for p in sharing.SYSTEMD_USER_DIR.iterdir())
    sharing.install_sharing_service()
    second = sorted((p.name, p.read_text()) for p in sharing.SYSTEMD_USER_DIR.iterdir())

    assert first == second
    assert [name for name, _ in second] == ["musicdatabase-share.service", "musicdatabase-share.timer"]
    service, timer = (text for _, text in second)
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
    assert calls == [
        ("daemon-reload",), ("enable", "musicdatabase-share.timer"), ("restart", "musicdatabase-share.service"),
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
    assert _call("GET", f"{base}?since=0", token) == (200, {"revision": 1, "playlists": [stored]})
    assert _call("GET", f"{base}?since=1", token) == (200, {"revision": 1, "playlists": []})

    status, deleted = _call("DELETE", f"{base}/{UID}?base_revision=1&deleted_at=1791839796631", token)
    assert (status, deleted) == (200, {"uid": UID, "revision": 2, "updated_at": 1791839796631, "deleted": True})
    (archived,) = (users_dir / "ola_k" / "archive" / "playlists").iterdir()
    assert json.loads(archived.read_text())["tracks"][2]["title"] == "kept"
    assert _call("GET", f"{base}?since=1", token) == (200, {"revision": 2, "playlists": [deleted]})
    # Deleting again, or something never seen, is done
    assert _call("DELETE", f"{base}/{UID}?base_revision=0&deleted_at=1", token) == (200, deleted)
    db = sqlite3.connect(users_dir / "ola_k" / "user.db")
    assert db.execute("SELECT song_ids_checked_to FROM playlists").fetchone() == (None,)


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
