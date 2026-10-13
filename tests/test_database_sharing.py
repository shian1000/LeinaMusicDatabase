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
    "music.db", "2026-10-12_23-44-05_01.db", "../2026-10-12_23-44-05_001.db", "a_b_2026-10-12_23-44-05_001.db",
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
    assert "--inbox-limit-bytes 104857600" in service
    assert f"--mount {sharing.SHARING_DRIVE_MOUNT}" in service
    assert str(sharing.SERVER_SCRIPT) in service
    assert "Restart=always" in service
    assert "OnStartupSec=1min" in timer
    assert "WantedBy=timers.target" in timer
    assert calls == [
        ("daemon-reload",), ("enable", "musicdatabase-share.timer"), ("restart", "musicdatabase-share.service"),
    ] * 2
