"""
Minimal HTTP server for the MusicDatabaseApp mobile client: serves exactly
<dir>/music.db, <dir>/tag.db and <dir>/files.db (the music library index),
nothing else (no directory listing, any
other path is a 404), on this machine's Tailscale address and, optionally,
on one named LAN interface.

Run by the systemd user service that sharing.install_sharing_service()
installs. Deliberately standard-library only and independent of the rest of
the app (no settings.py / venv imports), so it runs on the system python3
with every path passed on the command line.

    python3 sharing_server.py --dir DIR --mount MOUNT --port 8002 [--lan-interface enp2s0]

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
"""

import argparse
import ipaddress
import os
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


def make_handler(share_dir: str, mount: str):
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
                self._error(HTTPStatus.NOT_FOUND)
                return
            if not os.path.ismount(mount):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                # Files are swapped in with os.replace(), so the opened file
                # stays one complete version even if it's replaced mid-download.
                f = open(os.path.join(share_dir, name), "rb")
            except FileNotFoundError:
                self._error(HTTPStatus.NOT_FOUND)
                return
            except OSError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE)
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

        def _error(self, status: HTTPStatus):
            body = f"{status.value} {status.phrase}\n".encode()
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
    args = parser.parse_args(argv)

    handler = make_handler(args.dir, args.mount)

    def bind_and_serve(label, get_address):
        server = bind_when_ready(handler, args.port, get_address, label)
        host, port = server.server_address[:2]
        print(f"Serving {', '.join(SHARED_FILES)} from {args.dir} on http://{host}:{port}/ ({label})", flush=True)
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
