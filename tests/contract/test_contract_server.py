"""What the phone relies on before any account: the shared files, addressing and HTTPS."""

import base64
import hashlib
import http.client
import socket
import ssl
import time

import pytest


@pytest.mark.parametrize("name", ["music.db", "tag.db", "files.db"])
def test_shared_files_download_with_their_headers(server, name):
    answer = server.call("GET", f"/{name}")
    assert answer.status == 200
    assert answer.body == (server.share / name).read_bytes()
    assert answer.headers["Content-Type"] == "application/vnd.sqlite3"
    assert int(answer.headers["Content-Length"]) == len(answer.body)
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.headers["Last-Modified"]


def test_head_sends_only_the_headers(server):
    answer = server.call("HEAD", "/music.db")
    assert answer.status == 200 and answer.body == b""
    assert int(answer.headers["Content-Length"]) == (server.share / "music.db").stat().st_size


@pytest.mark.parametrize("path", ["/", "/secret.txt", "/music.db.tmp", "/../music.db", "/share/music.db", "/inbox/"])
def test_nothing_else_is_served(server, path):
    assert server.call("GET", path).status == 404


def test_an_unknown_host_name_is_refused(server):
    assert server.call("GET", "/music.db", headers={"Host": "evil.example.com"}).status == 421


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_an_address_as_host_is_served(server, host):
    assert server.call("GET", "/music.db", headers={"Host": f"{host}:{server.port}"}).status == 200


def test_a_missing_files_db_is_a_404(fresh_server):
    """The phone goes on without it; another missing file stops the download."""
    (fresh_server.share / "files.db").unlink()
    assert fresh_server.call("GET", "/files.db").status == 404


def test_a_missing_share_folder_is_a_503(fresh_server):
    """The phone shows an error and retries the next day."""
    fresh_server.share.rename(fresh_server.folder / "moved")
    assert fresh_server.call("GET", "/music.db").status == 503


def test_https_with_a_certificate_of_its_own(tls_server):
    assert tls_server.call("GET", "/music.db").status == 200
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", tls_server.port), timeout=5) as raw:
        with context.wrap_socket(raw) as wrapped:
            der = wrapped.getpeercert(binary_form=True)
    # The app works the code out of the certificate by this rule to pin it
    code = base64.b32encode(hashlib.sha256(der).digest()).decode()[:12]
    assert f"{code[:4]}-{code[4:8]}-{code[8:]}" in (tls_server.folder / "server.log").read_text()


def test_https_is_refused_without_trusting_the_certificate(tls_server):
    context = ssl.create_default_context()
    context.check_hostname = False
    connection = http.client.HTTPSConnection("127.0.0.1", tls_server.port, timeout=5, context=context)
    with pytest.raises(ssl.SSLCertVerificationError):
        connection.request("GET", "/music.db")
    connection.close()


def test_https_keeps_serving_after_plain_http(tls_server):
    plain = http.client.HTTPConnection("127.0.0.1", tls_server.port, timeout=5)
    try:
        plain.request("GET", "/music.db")
        plain.getresponse()
    except (http.client.HTTPException, OSError):
        pass
    plain.close()
    assert tls_server.call("GET", "/music.db").status == 200


def test_connections_over_the_cap_are_closed_and_the_rest_keep_working(fresh_server):
    held = [socket.create_connection(("127.0.0.1", fresh_server.port), timeout=5) for _ in range(64)]
    try:
        with socket.create_connection(("127.0.0.1", fresh_server.port), timeout=5) as extra:
            assert extra.recv(1) == b""
    finally:
        for connection in held:
            connection.close()
    for _ in range(100):
        try:
            if fresh_server.call("GET", "/music.db").status == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.05)
    pytest.fail("the server didn't serve again once the connections were gone")
