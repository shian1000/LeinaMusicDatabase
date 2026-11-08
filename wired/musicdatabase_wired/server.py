"""Running the API: uvicorn with what it doesn't do by itself - a cap on connections
and a limit on silent ones. Both use uvicorn's internals, pinned in requirements.txt;
tests/contract/ and tests/test_wired.py notice if an upgrade breaks them."""

import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from musicdatabase_core import user_schema
from musicdatabase_core.accounts import USER_DB_FILENAME
from musicdatabase_core.certificates import TLS_CERT_FILE, TLS_KEY_FILE, ensure_tls_certificate, tls_certificate_code
from musicdatabase_core.server_rules import MAX_CONNECTIONS

from .app import create_app
from .config import SYNC_ADDON, Config
from .request_folders import NotPrivateError, RequestFolders

# Ciphers every phone since Android 5 offers, without the old CBC ones
TLS_CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20"
CATALOG_CHECK_SECONDS = 24 * 60 * 60


def capped_protocol(request_timeout: float) -> type[H11Protocol]:
    class CappedProtocol(H11Protocol):
        """Over MAX_CONNECTIONS a new connection is closed at once, and one that goes
        silent gives its place back, whether it sent nothing, stopped mid-request or
        stopped reading a download. Idle clients can't use the server up."""

        refused = False
        idle_timer = None
        last_seen = 0.0
        last_buffered = 0

        def connection_made(self, transport):
            if len(self.connections) >= MAX_CONNECTIONS:
                self.refused = True
                transport.abort()
                return
            super().connection_made(transport)
            self.last_seen = self.loop.time()
            self.idle_timer = self.loop.call_later(request_timeout, self.check_silence)

        def connection_lost(self, exc):
            if self.refused:
                return
            if self.idle_timer is not None:
                self.idle_timer.cancel()
            super().connection_lost(exc)

        def data_received(self, data):
            if self.refused:
                return
            self.last_seen = self.loop.time()
            super().data_received(data)

        def check_silence(self):
            if self.transport.is_closing():
                return
            buffered = self.transport.get_write_buffer_size()
            if buffered != self.last_buffered:
                self.last_seen = self.loop.time()  # the client is reading a download
            self.last_buffered = buffered
            silent_for = self.loop.time() - self.last_seen
            working = self.cycle is not None and not self.cycle.response_complete and buffered == 0 \
                and self.cycle.response_started
            if silent_for >= request_timeout and not working:
                self.transport.close()
                return
            self.idle_timer = self.loop.call_later(max(request_timeout - silent_for, 0.05), self.check_silence)

    return CappedProtocol


def latest_user_schema(config: Config) -> None:
    """Nobody else migrates here. A copy of each user.db stays next to it first."""
    for path in sorted(config.users_dir.glob(f"*/{USER_DB_FILENAME}")):
        conn = sqlite3.connect(path)
        try:
            user_schema.ensure_schema_version_table(conn)
            applied = user_schema.applied_filenames(conn)
        finally:
            conn.close()
        if {f.name for f in user_schema.migration_files()} - applied:
            shutil.copy2(path, path.with_suffix(f".before-migration-{int(time.time())}"))
            print(f"{path.parent.name}: applied {user_schema.apply_user_migrations(path)}", flush=True)


def _update_catalog_daily(config: Config) -> None:
    from .addons.catalog import CatalogError, update_catalog

    while True:
        try:
            if update_catalog(config):
                print("Downloaded a newer catalog", flush=True)
        except CatalogError as exc:
            print(f"Couldn't update the catalog: {exc}", flush=True)
        time.sleep(CATALOG_CHECK_SECONDS)


def _listening_socket(address: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((address, port))
    sock.set_inheritable(True)
    return sock


def run(config: Config) -> int:
    if not config.addresses:
        print("No address to serve on: set WIRED_ADDRESSES in .env or pass --address.", file=sys.stderr, flush=True)
        return 2
    # Every file this process makes is the account's alone: users' data and requests above all
    os.umask(0o077)
    Path(config.users_dir).mkdir(parents=True, exist_ok=True)
    if SYNC_ADDON in config.addons:
        Path(config.catalog_dir).mkdir(parents=True, exist_ok=True)
    latest_user_schema(config)
    requests = RequestFolders(config.inbox_dir, config.catalog_dir / "music.db")
    try:
        requests.make_private()
    except NotPrivateError as exc:
        print(exc, file=sys.stderr, flush=True)
        return 2
    if leftovers := requests.sort_leftovers():
        print(f"Checked {leftovers} request(s) left in new/ by the last stop", flush=True)

    tls_options = {}
    if config.tls:
        try:
            ensure_tls_certificate(str(config.tls_dir))
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Couldn't make the server's certificate: {exc}", file=sys.stderr, flush=True)
            return 2
        print(f"HTTPS certificate code (compare it in Leina Player): {tls_certificate_code(str(config.tls_dir))}",
              flush=True)
        tls_options = {"ssl_certfile": str(config.tls_dir / TLS_CERT_FILE),
                       "ssl_keyfile": str(config.tls_dir / TLS_KEY_FILE),
                       "ssl_ciphers": TLS_CIPHERS}
    try:
        sockets = [_listening_socket(address, config.port) for address in config.addresses]
    except OSError as exc:
        print(f"Can't listen on {config.addresses} port {config.port}: {exc}", file=sys.stderr, flush=True)
        return 2

    if SYNC_ADDON in config.addons and config.catalog_updates:
        threading.Thread(target=_update_catalog_daily, args=(config,), name="catalog", daemon=True).start()

    scheme = "https" if config.tls else "http"
    for address in config.addresses:
        print(f"Serving on {scheme}://{address}:{config.port}/", flush=True)
    server = uvicorn.Server(uvicorn.Config(
        create_app(config), http=capped_protocol(config.request_timeout), ws="none", lifespan="off",
        # This server is reached directly: a forwarded header is only what a client claims,
        # and the login limits go by the address
        proxy_headers=False, server_header=False, access_log=False, log_level="warning",
        timeout_keep_alive=int(config.request_timeout) or 1, timeout_graceful_shutdown=5, **tls_options))
    server.run(sockets=sockets)
    return 0
