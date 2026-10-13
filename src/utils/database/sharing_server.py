"""
Minimal HTTP server for the MusicDatabaseApp mobile client: serves exactly
<dir>/music.db, <dir>/tag.db and <dir>/files.db (the music library index),
nothing else (no directory listing, any
other path is a 404), on this machine's Tailscale address and, optionally,
on one named LAN interface. With --inbox it also takes requests from the
phone: `PUT /inbox/<name>.db` stores one request file in <inbox>/new/ for
Manage database -> "Resolve new requests" (utils/database/inbox_requests.py).

Run by the systemd user service that sharing.install_sharing_service()
installs. Deliberately standard-library only and independent of the rest of
the app (no settings.py / venv imports), so it runs on the system python3
with every path passed on the command line.

    python3 sharing_server.py --dir DIR --mount MOUNT --port 8002 [--lan-interface enp2s0] [--inbox DIR]

- Tailscale: waits (retrying forever) until `tailscale ip -4` returns an
  address and it can bind to it - right after boot Tailscale may not have
  one yet.
- LAN (only with --lan-interface): the phone uses this address on the home
  Wi-Fi and falls back to Tailscale when it doesn't answer. Widening beyond
  Tailscale-only was a deliberate decision (2026-10-10): anyone on the home
  network can download the shared files. The interface is picked by *name*
  because docker0 / br-* bridges also carry private addresses and must never
  be exposed. Same wait-and-retry loop, in its own thread, so a missing LAN
  never delays or breaks the Tailscale socket (or vice versa).
- Never binds to 0.0.0.0, and never to an address other than those two.
- If MOUNT (the external drive holding DIR) isn't mounted, requests for the
  databases get a 503 instead of the server exiting, so the phone shows an
  error and simply retries the next day.
- Uploads (anyone on the home network can send one, like anyone can
  download): only file names of the request format, only SQLite files, at
  most --max-request-bytes each, and nothing once <inbox>/new/ holds
  --inbox-limit-bytes - processing a request moves it out of new/ and frees
  the space. Never overwrites: the same file sent again is a 200, another
  file under a taken name a 409. Answers:
  201 stored, 200 already here (new/ or processed/), 400 bad name or not
  SQLite, 409 name taken, 411 no Content-Length, 413 too big, 507 inbox
  full, 503 drive not mounted.
"""

import argparse
import ipaddress
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SHARED_FILES = ("music.db", "tag.db", "files.db")
RETRY_SECONDS = 10
TAILSCALE_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")

# [<user>_]<YYYY-MM-DD>_<HH-MM-SS>_<number>.db - the phone's request file names
REQUEST_NAME_RE = re.compile(r"^(?:[A-Za-z0-9-]{1,40}_)?\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{3,9}\.db$")
SQLITE_HEADER = b"SQLite format 3\x00"
INBOX_PREFIX = "/inbox/"
INBOX_NEW = "new"
INBOX_PROCESSED = "processed"
DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_INBOX_LIMIT_BYTES = 100 * 1024 * 1024
# A phone that stops sending mid-upload mustn't hold a server thread forever
UPLOAD_SOCKET_TIMEOUT = 60
# Partial uploads start with a dot; the inbox reader skips them
_PART_PREFIX = "."
_PART_SUFFIX = ".part"


def _dir_size(path: str) -> int:
    total = 0
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
    return total


def _same_content(path: str, data: bytes) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(data) + 1) == data
    except OSError:
        return False


def make_handler(share_dir: str, mount: str, inbox_dir: str | None = None,
                 max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
                 inbox_limit_bytes: int = DEFAULT_INBOX_LIMIT_BYTES):
    # One lock for "is there room / is the name free" + the write, so two
    # uploads at once can't both squeeze past the limit or take one name.
    inbox_lock = threading.Lock()

    class ShareHandler(BaseHTTPRequestHandler):
        server_version = "MusicDatabaseShare"
        sys_version = ""

        def do_HEAD(self):
            self._serve(send_body=False)

        def do_GET(self):
            self._serve(send_body=True)

        def _serve(self, send_body: bool):
            name = self.path.split("?", 1)[0].lstrip("/")
            if name not in SHARED_FILES:
                self._reply(HTTPStatus.NOT_FOUND)
                return
            if not os.path.ismount(mount):
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                # Files are swapped in with os.replace(), so the opened file
                # stays one complete version even if it's replaced mid-download.
                f = open(os.path.join(share_dir, name), "rb")
            except FileNotFoundError:
                self._reply(HTTPStatus.NOT_FOUND)
                return
            except OSError:
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            with f:
                st = os.fstat(f.fileno())
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/vnd.sqlite3")
                self.send_header("Content-Length", str(st.st_size))
                self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if send_body:
                    shutil.copyfileobj(f, self.wfile)

        def do_PUT(self):
            name = self.path.split("?", 1)[0]
            if inbox_dir is None or not name.startswith(INBOX_PREFIX):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            name = name[len(INBOX_PREFIX):]
            if not REQUEST_NAME_RE.match(name):
                self._reply(HTTPStatus.BAD_REQUEST, "not a request file name")
                return
            length = self.headers.get("Content-Length")
            if length is None or not length.isdigit():
                self._reply(HTTPStatus.LENGTH_REQUIRED)
                return
            length = int(length)
            if length > max_request_bytes:
                self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"at most {max_request_bytes} bytes")
                return
            self.connection.settimeout(UPLOAD_SOCKET_TIMEOUT)
            try:
                data = self.rfile.read(length)
            except OSError:
                self.close_connection = True
                return
            if len(data) != length:
                self.close_connection = True
                return
            if not data.startswith(SQLITE_HEADER):
                self._reply(HTTPStatus.BAD_REQUEST, "not an SQLite file")
                return
            if not os.path.ismount(mount):
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._store(name, data)

        def _store(self, name: str, data: bytes):
            new_dir = os.path.join(inbox_dir, INBOX_NEW)
            target = os.path.join(new_dir, name)
            with inbox_lock:
                try:
                    os.makedirs(new_dir, exist_ok=True)
                    # Sent again (e.g. the answer to the first try got lost)
                    for folder in (INBOX_NEW, INBOX_PROCESSED):
                        existing = os.path.join(inbox_dir, folder, name)
                        if os.path.exists(existing):
                            if _same_content(existing, data):
                                self._reply(HTTPStatus.OK, "already received")
                            else:
                                self._reply(HTTPStatus.CONFLICT, "another request has this name")
                            return
                    if _dir_size(new_dir) + len(data) > inbox_limit_bytes:
                        self._reply(HTTPStatus.INSUFFICIENT_STORAGE, "inbox full")
                        return
                    part = os.path.join(new_dir, f"{_PART_PREFIX}{name}{_PART_SUFFIX}")
                    try:
                        with open(part, "wb") as f:
                            f.write(data)
                            f.flush()
                            os.fsync(f.fileno())
                        os.replace(part, target)
                    finally:
                        if os.path.exists(part):
                            os.unlink(part)
                except OSError as exc:
                    print(f"Storing request {name} failed: {exc}", flush=True)
                    self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                    return
            print(f"Received request {name} ({len(data)} bytes)", flush=True)
            self._reply(HTTPStatus.CREATED, "stored")

        def _reply(self, status: HTTPStatus, detail: str | None = None):
            body = f"{status.value} {status.phrase}{f' - {detail}' if detail else ''}\n".encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    return ShareHandler


def tailscale_ipv4() -> str | None:
    """This machine's Tailscale IPv4 address, or None if Tailscale has none (yet)."""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=10
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    for candidate in out:
        try:
            # Tailscale hands out addresses from the CGNAT range only.
            if ipaddress.IPv4Address(candidate) in TAILSCALE_NETWORK:
                return candidate
        except ValueError:
            continue
    return None


def interface_ipv4(interface: str) -> str | None:
    """
    The private IPv4 address of a named network interface (e.g. "enp2s0"),
    or None if it has none (yet). A public or Tailscale-range address is
    rejected - this socket is meant for the home LAN only.
    """
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", interface],
            capture_output=True, text=True, timeout=10,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    # `ip -o` prints one line per address: "2: enp2s0    inet 192.0.2.13/24 brd ..."
    for i, token in enumerate(out[:-1]):
        if token != "inet":
            continue
        try:
            address = ipaddress.IPv4Interface(out[i + 1]).ip
        except ValueError:
            continue
        if address.is_private and not address.is_loopback and address not in TAILSCALE_NETWORK:
            return str(address)
    return None


def bind_when_ready(handler, port: int, get_address=None, label: str = "Tailscale") -> ThreadingHTTPServer:
    get_address = get_address or tailscale_ipv4
    while True:
        address = get_address()
        if address is None:
            print(f"No {label} address yet, retrying in {RETRY_SECONDS}s", flush=True)
        else:
            try:
                return ThreadingHTTPServer((address, port), handler)
            except OSError as exc:
                print(f"Can't bind {address}:{port} ({exc}), retrying in {RETRY_SECONDS}s", flush=True)
        time.sleep(RETRY_SECONDS)


def address_sources(lan_interface: str | None) -> list[tuple[str, Callable[[], str | None]]]:
    """(label, address getter) for every socket to serve on."""
    sources = [("Tailscale", tailscale_ipv4)]
    if lan_interface:
        sources.append((f"LAN {lan_interface}", lambda: interface_ipv4(lan_interface)))
    return sources


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dir", required=True, help="folder holding music.db, tag.db and files.db")
    parser.add_argument("--mount", required=True, help="mount point of the drive holding --dir")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--lan-interface", help="also serve on this interface's private IPv4 address")
    parser.add_argument("--inbox", help="accept requests from the phone into this folder (on --mount)")
    parser.add_argument("--max-request-bytes", type=int, default=DEFAULT_MAX_REQUEST_BYTES)
    parser.add_argument("--inbox-limit-bytes", type=int, default=DEFAULT_INBOX_LIMIT_BYTES)
    args = parser.parse_args(argv)

    handler = make_handler(args.dir, args.mount, args.inbox, args.max_request_bytes, args.inbox_limit_bytes)

    def bind_and_serve(label, get_address):
        server = bind_when_ready(handler, args.port, get_address, label)
        host, port = server.server_address[:2]
        inbox = f", requests into {args.inbox}" if args.inbox else ""
        print(f"Serving {', '.join(SHARED_FILES)} from {args.dir}{inbox} on http://{host}:{port}/ ({label})", flush=True)
        server.serve_forever()

    # One socket per address, each bound and served independently; the
    # process lives as long as any of them does.
    threads = [
        threading.Thread(target=bind_and_serve, args=source, name=source[0])
        for source in address_sources(args.lan_interface)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
