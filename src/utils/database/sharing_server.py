"""
Minimal HTTP server for the MusicDatabaseApp mobile client: serves exactly
<dir>/music.db, <dir>/tag.db and <dir>/files.db (the music library index),
nothing else (no directory listing, any
other path is a 404), on this machine's Tailscale address (unless
--no-tailscale), optionally on one named LAN interface and one named VPN
interface (--vpn-interface, any provider), and on any exact addresses given
with --address. With --inbox it also takes requests from the
phone: `PUT /inbox/<name>.db` stores one request file in <inbox>/new/ for
Manage database -> "Resolve new requests" (utils/database/inbox_requests.py).
With --users the phone can also log in as a MusicDatabase user (POST /login,
POST /logout) and sign its uploads with the token it gets.

Run by the systemd user service that sharing.install_sharing_service()
installs. Deliberately standard-library only and independent of the rest of
the app (no settings.py / venv imports), so it runs on the system python3
with every path passed on the command line.

    python3 sharing_server.py --dir DIR [--mount MOUNT] --port 8002 [--lan-interface enp2s0] [--no-tailscale]
                              [--vpn-interface wg0] [--address ADDR ...] [--inbox DIR] [--users DIR]
                              [--mood-changes DIR] [--library DIR] [--tls-dir DIR]

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
- --vpn-interface (2026-10-22): the address of a VPN's interface named by
  the owner (wg0, tun0, tailscale0...), whatever the provider - private or
  in 100.64.0.0/10, never public; same wait-and-retry loop. The unit passes
  it with --no-tailscale, so the VPN is one setting (SHARING_VPN).
- --address (2026-10-21, for a PC without Tailscale or with another VPN):
  exactly that address, same wait-and-retry loop (the interface may come up
  later). 0.0.0.0 only when given there - the owner's explicit choice.
- Never binds to 0.0.0.0 on its own, and never to an address other than those.
- HTTPS (--tls-dir, 2026-11-04): every socket serves TLS with one
  certificate the server makes for itself on first start (openssl, EC P-256,
  10 years; cert.pem + key.pem in that folder, the key readable by this user
  only). It's signed by no one: Leina Radio shows its code (certificate_code(),
  printed at start) for the owner to compare once and then pins it. The code
  is the SHA-256 of the certificate (DER) in base32, the first 12 characters
  as XXXX-XXXX-XXXX - the app works it out the same way, keep the two alike.
  The handshake runs in the connection's own thread, with a time limit, so a
  client that never finishes it holds nothing else up. Without --tls-dir the
  sockets serve plain HTTP, as before.
- Host header (2026-11-03): only an IP address, "localhost", this machine's
  host name or its Tailscale MagicDNS name (read at run time) - anything
  else is a 421 before any handler runs.
- Connections (2026-11-04): one that sends no request within
  REQUEST_SOCKET_TIMEOUT is closed, and at most MAX_CONNECTIONS are served at
  once over every socket together (CappedHTTPServer) - one more is closed
  at once, without a thread. A web page that points its own
  domain at this server (DNS rebinding) sends that domain, so a browser on
  the home network or the tailnet can't be used to read or write here.
- If MOUNT (the external drive holding DIR) isn't mounted, requests for the
  databases get a 503 instead of the server exiting, so the phone shows an
  error and simply retries the next day. Without --mount (DIR on the system
  disk) the same 503 means DIR itself is missing.
- Uploads (anyone on the home network can send one, like anyone can
  download): only file names of the request format, only SQLite files, at
  most --max-request-bytes each, and nothing once <inbox>/new/ and review/
  (left to the owner by the app's automatic pass) hold --inbox-limit-bytes -
  settling a request moves it to processed/ and frees the space. Never
  overwrites: the same file sent again is a 200, another file under a taken
  name a 409. A name without a user (unsigned) is limited to
  ANONYMOUS_UPLOADS_PER_WINDOW per client address an hour (UploadLimiter,
  in memory), and such files may fill at most ANONYMOUS_INBOX_SHARE of the
  limit, so logged-in users always have room. Answers:
  201 stored, 200 already here (new/, review/ or processed/), 400 bad name or not
  SQLite, 409 name taken, 411 no Content-Length, 413 too big, 429 too many
  unsigned uploads (Retry-After), 507 inbox full, 503 drive not mounted.
- Logging in (only with --users; password and token checks in
  account_auth.py, the one module shared with the app): POST /login with
  JSON {"username", "password"} and optionally "device" (the phone's model
  name, stored with the token - account_auth.clean_device_name(); a
  non-string is ignored) -> 200 {"username": <normalized>, "token"},
  401 wrong username or password (never says which), 429 locked out (5 failed
  logins within 15 minutes from one address or for one username lock both
  for 5 minutes - in memory, a restart clears it), 503 drive not mounted.
  POST /logout with "Authorization: Bearer <token>" revokes it; always 204.
- Signed uploads (only with --users): the user part of a request file name
  is always a verified sender. A name with one needs that user's token (none
  or unknown/revoked -> 401, the phone logs in again; another user's -> 403);
  a name without one is accepted with or without a token, like before login.
  The token is checked before the body is read, here and on every route
  below: a 401 / 403 comes on the headers alone and closes the connection.
- Playlists (only with --users; the rules in playlist_sync.py, shared with
  the app): GET /users/<name>/playlists?since=<revision>,
  PUT /users/<name>/playlists/<uid> (JSON body, base_revision),
  DELETE /users/<name>/playlists/<uid>?base_revision=<n>&deleted_at=<ms>.
  Every request needs <name>'s token (none/unknown -> 401, another user's ->
  403). A playlist carries "cover" and "excluded_source_tracks"; a PUT
  without either keeps the stored one. 200 / 409 answer with JSON; 400 bad body or parameters, 411 no
  Content-Length, 413 body over --max-playlist-bytes, 503 drive not mounted
  or that user.db not migrated yet (the server never migrates). It writes
  user.db itself, one BEGIN IMMEDIATE transaction per request, and resolves
  incoming song ids against <dir>/music.db's song_id_changes.
- Moods (only with --users; rules in mood_sync.py): the same three calls and
  answers under /users/<name>/moods[/<uid>], a mood being a name + song ids.
- Folders of the phone's Biblioteka (only with --users; rules in
  folder_sync.py): the same three calls and answers under
  /users/<name>/folders[/<uid>], a folder being a name + items (refs to
  playlists / moods, stored as sent). A uid is 1-64 characters [A-Za-z0-9-];
  a body over FOLDER_MAX_BODY_BYTES -> 413.
- Saved songs and links (only with --users; rules in saved_sync.py): the
  same three calls and answers under /users/<name>/saved-songs[/<uid>]
  (GET -> {"revision", "songs"}) and /users/<name>/later-links[/<uid>]
  (GET -> {"revision", "links"}), one item = one song on one list (liked /
  "Na później") or one link, stored verbatim. A DELETE of a uid never seen
  stores the marker too. A body over SAVED_MAX_BODY_BYTES -> 413.
- Plays for the phone's listening stats (only with --users; rules in
  listen_sync.py): GET /users/<name>/listens?since=<revision>&limit=<n> ->
  {"revision", "listens", "more"}, POST /users/<name>/listens with
  {"listens": [...]} -> {"revision"}; a play is added, or updated when its
  listened_ms is longer. The same token rules and errors as folders; a batch
  with one bad play -> 400, nothing stored.
  GET /users/<name> -> {"username", "permissions"} with that user's token
  (POST /login answers the permissions too), so a revoked permission reaches
  the phone without logging in again.
- Global moods (only with --users and --mood-changes): PUT / DELETE
  /global-moods/<mood id>/songs/<song id> adds / removes a song, for users
  whose account.json grants "edit_global_moods" (otherwise 403 with JSON
  {"error": "permission", "permission": "edit_global_moods"}). The mood and
  song are checked against the shared copies in --dir (404 with JSON
  {"error": "mood"} / {"error": "song"}; 503 when the shared tag.db can't be
  read, so a mood isn't taken for gone); the change is only recorded in
  --mood-changes/pending/ (202) - MusicDatabase applies it to the original
  tag.db later (global_mood_log.py). The server never writes tag.db.
- Covers (only with --users; user_covers.py): PUT /users/<name>/covers/<sha256>
  stores a PNG/JPEG picture under the SHA-256 of its bytes (201 new, 200
  already stored, 400 hash mismatch, 413 over 4 MiB, 415 another type or
  bytes that aren't one), GET answers it (404 unknown, cacheable forever).
  Playlists and moods carry the hash in "cover" (a PUT without the key keeps
  the stored one; a hash with no blob -> 400 {"error": "cover_missing"}).
  GET /users/<name>/global-mood-covers?since=<revision> and
  PUT /users/<name>/global-mood-covers/<mood id> sync the user's private
  covers of global moods like moods. After every applied write, blobs the
  user's rows no longer use are removed once a day old.
- Every path above that needs --users answers only its own methods
  (USER_ROUTES); another method -> 405 with Allow.
- Library files (only with --library): GET / HEAD /library/<files.id> serve
  one audio file of the music library, looked up in <dir>/files.db - the
  copy the phone downloaded, so its ids are the ones the phone knows - and
  read from <library>/<files.path>. No token, like the databases. Never a
  path from the request, never a directory listing, only
  LIBRARY_AUDIO_EXTENSIONS, and only a file whose real path (symlinks
  resolved) is under the library's. One byte range per request
  (bytes=a-b, a-, -n) -> 206 with Content-Range; several ranges -> 200 with
  the whole file. Answers: 200/206, 404 unknown id / not an id / a row whose
  file is gone, isn't audio or lies outside the library / no --library,
  416 a range past the end, 503 drive not mounted or library folder
  missing. Cache-Control: no-cache - a row keeps its id when the file's
  contents change. The player drops connections on every seek or skip, so
  a client hanging up mid-body is normal and not logged.
"""

import argparse
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from urllib.parse import parse_qs, urlsplit

# Run as a script by the systemd service: src/ isn't on sys.path then
_SRC = str(Path(__file__).resolve().parents[2])
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from musicdatabase_core import (account_auth, global_mood_log, inbox_files, listen_sync,  # noqa: E402
                                mood_sync, playlist_sync, server_rules, sync_collections, user_covers)
from musicdatabase_core.file_tools import connect_read_only  # noqa: E402
from musicdatabase_core.certificates import (TLS_CERT_FILE, TLS_KEY_FILE, certificate_code,  # noqa: E402
                                             ensure_tls_certificate, tls_certificate_code)
from musicdatabase_core.inbox_files import (DEFAULT_INBOX_LIMIT_BYTES, DEFAULT_MAX_REQUEST_BYTES,  # noqa: E402
                                            INBOX_KEPT_FOLDERS, INBOX_NEW, INBOX_PROCESSED, INBOX_REVIEW)
from musicdatabase_core.request_format import REQUEST_NAME_RE, SQLITE_HEADER  # noqa: E402
from musicdatabase_core.server_rules import (ANONYMOUS_INBOX_SHARE, ANONYMOUS_UPLOAD_WINDOW_SECONDS,  # noqa: E402
                                             ANONYMOUS_UPLOADS_PER_WINDOW, COVER_CACHE_CONTROL,
                                             DEFAULT_MAX_PLAYLIST_BYTES, FOLDER_MAX_BODY_BYTES,
                                             GLOBAL_MOOD_COVER_MAX_BODY_BYTES, LISTEN_MAX_BODY_BYTES,
                                             LOGIN_FAILURE_WINDOW_SECONDS, LOGIN_LOCKOUT_SECONDS,
                                             LOGIN_MAX_BODY_BYTES, LOGIN_MAX_FAILURES, MAX_CONNECTIONS,
                                             REQUEST_SOCKET_TIMEOUT, SAVED_MAX_BODY_BYTES, LoginLimiter,
                                             UploadLimiter, host_allowed, plain_int)

SHARED_FILES = ("music.db", "tag.db", "files.db")
RETRY_SECONDS = 10
# A client that connects and never finishes the TLS handshake gives up its thread after this
TLS_HANDSHAKE_TIMEOUT = 15
TAILSCALE_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")

INBOX_PREFIX = "/inbox/"
INBOX_ANSWERS = {
    inbox_files.STORED: (HTTPStatus.CREATED, "stored"),
    inbox_files.ALREADY_HERE: (HTTPStatus.OK, "already received"),
    inbox_files.NAME_TAKEN: (HTTPStatus.CONFLICT, "another request has this name"),
    inbox_files.FULL: (HTTPStatus.INSUFFICIENT_STORAGE, "inbox full"),
    inbox_files.FULL_FOR_UNSIGNED: (HTTPStatus.INSUFFICIENT_STORAGE, "inbox full for requests without logging in"),
}
# A phone that stops sending mid-upload mustn't hold a server thread forever
UPLOAD_SOCKET_TIMEOUT = 60
# /users/<name>/<collection>[/<uid>]: playlists, moods, folders, saved-songs, later-links
COLLECTION_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/(?P<collection>"
                                + "|".join(map(re.escape, sync_collections.BY_PATH)) + r")(?:/(?P<uid>[^/]+))?$")
# /users/<name>/listens
LISTENS_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/listens$")
# /users/<name>: who the token is and what they may do
USER_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)$")
# /users/<name>/covers/<sha256>
COVERS_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/covers/(?P<sha>[^/]+)$")
# /users/<name>/global-mood-covers[/<mood id>]
GLOBAL_MOOD_COVERS_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/global-mood-covers(?:/(?P<mood>[^/]+))?$")
# /global-moods/<mood id>/songs/<song id>
GLOBAL_MOOD_SONG_PATH_RE = re.compile(r"^/global-moods/(?P<mood>[^/]+)/songs/(?P<song>[^/]+)$")
# /library/<files.id>
LIBRARY_PATH_RE = re.compile(r"^/library/(?P<id>[1-9][0-9]{0,17})$")
# The synced collections answer only these
ITEM_METHODS = ("GET", "PUT", "DELETE")


# The endpoints that need --users: (path, the methods it answers, handler
# method called with (method, match)). A path of these with another method is
# 405 - checked here once, never in a handler, which once read a POST as a DELETE
USER_ROUTES = (
    (COLLECTION_PATH_RE, ITEM_METHODS, "_collection"),
    (LISTENS_PATH_RE, ("GET", "POST"), "_listens"),
    (COVERS_PATH_RE, ("GET", "PUT"), "_cover_blob"),
    (GLOBAL_MOOD_COVERS_PATH_RE, ("GET", "PUT"), "_global_mood_covers"),
    (USER_PATH_RE, ("GET",), "_profile"),
    (GLOBAL_MOOD_SONG_PATH_RE, ("PUT", "DELETE"), "_global_mood_song"),
)
# Copy of config.constants.LIBRARY_AUDIO_EXTENSIONS (this script can't import
# it; a test keeps the two equal). Compared lowercased.
LIBRARY_AUDIO_EXTENSIONS = (".mp3",)
LIBRARY_CONTENT_TYPES = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4",
                         ".ogg": "audio/ogg", ".opus": "audio/ogg"}
LIBRARY_CHUNK_BYTES = 64 * 1024
# One range only; anything with a comma (several ranges) doesn't match
BYTE_RANGE_RE = re.compile(r"^\s*bytes\s*=\s*(?P<first>[0-9]*)\s*-\s*(?P<last>[0-9]*)\s*$", re.IGNORECASE)
# A player that stops reading mustn't hold a server thread forever
LIBRARY_SOCKET_TIMEOUT = 60
# The same for a phone downloading music.db / tag.db / files.db
SHARED_FILE_SOCKET_TIMEOUT = 60


class RangeNotSatisfiable(Exception):
    """A Range header none of whose bytes exist (-> 416)."""


def parse_byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    """The inclusive (first, last) byte of a single-range "bytes=a-b",
    "bytes=a-" or "bytes=-n" header for a file of `size` bytes, or None for
    the whole file: no header, another unit, several ranges or a malformed
    one (ignored, as RFC 9110 allows). RangeNotSatisfiable when it starts past
    the end or asks for the last 0 bytes."""
    match = BYTE_RANGE_RE.match(value or "")
    if match is None or not (match.group("first") or match.group("last")):
        return None
    first, last = match.group("first"), match.group("last")
    if not first:
        suffix = int(last)
        if suffix == 0 or size == 0:
            raise RangeNotSatisfiable
        return max(0, size - suffix), size - 1
    start = int(first)
    if last and int(last) < start:
        return None
    if start >= size:
        raise RangeNotSatisfiable
    return start, min(int(last), size - 1) if last else size - 1


def library_file_path(files_db: str, library_dir: str, file_id: int) -> str | None:
    """The real path of files.db row `file_id`'s audio file under
    library_dir, or None: no such row, a path leading outside the library
    (via "..", an absolute path or a symlink), or not an audio extension.
    sqlite3.Error when files.db can't be read."""
    # The shared copy is only ever swapped in with os.replace(), never
    # written in place: immutable, so nothing (-shm, locks) is created beside it
    conn = connect_read_only(files_db, immutable=True)
    try:
        row = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
    finally:
        conn.close()
    if row is None or not isinstance(row[0], str) or not row[0]:
        return None
    root = os.path.realpath(library_dir)
    target = os.path.realpath(os.path.join(root, row[0]))
    if os.path.commonpath([root, target]) != root or target == root:
        return None
    if os.path.splitext(target)[1].lower() not in LIBRARY_AUDIO_EXTENSIONS:
        return None
    return target


def make_handler(share_dir: str, mount: str | None, inbox_dir: str | None = None,
                 max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
                 inbox_limit_bytes: int = DEFAULT_INBOX_LIMIT_BYTES,
                 users_dir: str | None = None, limiter: LoginLimiter | None = None,
                 max_playlist_bytes: int = DEFAULT_MAX_PLAYLIST_BYTES, mood_changes_dir: str | None = None,
                 library_dir: str | None = None, host_names: set[str] | None = None,
                 upload_limiter: UploadLimiter | None = None):
    # Names the Host header may carry besides an IP address; main() adds the
    # MagicDNS name once a socket is up, so it's read on every request
    host_names = host_names if host_names is not None else set()
    # Storing a blob, checking a cover exists + the write naming it, and the
    # clean-up never interleave, so a clean-up can't remove a blob between
    # the check and the commit
    covers_lock = threading.Lock()
    # --max-playlist-bytes covers playlists and moods; the rest keep their own limit
    body_limits = {"playlists": max_playlist_bytes, "moods": max_playlist_bytes}
    limiter = limiter or LoginLimiter()
    upload_limiter = upload_limiter or UploadLimiter()
    inbox = inbox_files.Inbox(inbox_dir, inbox_limit_bytes) if inbox_dir else None

    def data_available() -> bool:
        # On an external drive (--mount): only while it's mounted. Without
        # one, everything is on the system disk: --dir just has to be there.
        return os.path.ismount(mount) if mount else os.path.isdir(share_dir)

    class ShareHandler(BaseHTTPRequestHandler):
        server_version = "MusicDatabaseShare"
        sys_version = ""
        # Set on the socket in setup(): a client that connects and sends
        # nothing gives its thread back. Downloads, streaming and uploads set
        # their own (equal) timeouts later.
        timeout = REQUEST_SOCKET_TIMEOUT
        # The user a valid token showed on this request, for the request's log line
        log_user = None

        def parse_request(self):
            # Connections are reused: one request's user mustn't reach the next line
            self.log_user = None
            return super().parse_request()

        def log_message(self, format, *args):
            # The base class's line, with the token's user in the "authuser" place
            message = "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in format % args)
            print(f"{self.address_string()} - {self.log_user or '-'} [{self.log_date_time_string()}] {message}",
                  file=sys.stderr)

        def send_response(self, code, message=None):
            self._responded = True
            super().send_response(code, message)

        def handle_one_request(self):
            # Whatever a handler didn't expect gets an answer (500) and the
            # traceback in the log - not a connection dropped without one
            self._responded = False
            self._body_read = False
            try:
                super().handle_one_request()
            except Exception:
                print(f"{self.command} {urlsplit(self.path or '').path}: unexpected error", flush=True)
                traceback.print_exc()
                self.close_connection = True
                if not self._responded:
                    try:
                        self._reply(HTTPStatus.INTERNAL_SERVER_ERROR)
                    except OSError:
                        pass

        def parse_request(self):
            if not super().parse_request():
                return False
            if host_allowed(self.headers.get("Host"), host_names):
                return True
            print(f"{self.client_address[0]} {self.command} rejected: Host {self.headers.get('Host')!r}", flush=True)
            self.close_connection = True
            self._reply(HTTPStatus.MISDIRECTED_REQUEST, "unknown host name")
            return False

        def do_HEAD(self):
            self._serve(send_body=False)

        def _route_users(self, method: str) -> bool:
            """The endpoints that need --users; False if the path is none of them."""
            if users_dir is None:
                return False
            path = urlsplit(self.path).path
            for path_re, methods, handler in USER_ROUTES:
                match = path_re.match(path)
                if match is None:
                    continue
                if method in methods:
                    getattr(self, handler)(method, match)
                else:
                    self._reply(HTTPStatus.METHOD_NOT_ALLOWED, headers={"Allow": ", ".join(methods)})
                return True
            return False

        def do_GET(self):
            if not self._route_users("GET"):
                self._serve(send_body=True)

        def do_DELETE(self):
            if not self._route_users("DELETE"):
                self._reply(HTTPStatus.NOT_FOUND)

        def _serve(self, send_body: bool):
            path = self.path.split("?", 1)[0]
            if path.startswith("/library/"):
                self._library_file(path, send_body)
                return
            name = path.lstrip("/")
            if name not in SHARED_FILES:
                self._reply(HTTPStatus.NOT_FOUND)
                return
            if not data_available():
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
                self.connection.settimeout(SHARED_FILE_SOCKET_TIMEOUT)
                try:
                    self.end_headers()
                    if send_body:
                        shutil.copyfileobj(f, self.wfile)
                except (ConnectionError, TimeoutError):
                    # The phone lost the network mid-download; it asks again
                    print(f"{name}: download cut off", flush=True)
                    self.close_connection = True

        # ---- library files ----

        def _library_file(self, path: str, send_body: bool):
            match = LIBRARY_PATH_RE.match(path)
            if library_dir is None or match is None:
                self._reply(HTTPStatus.NOT_FOUND)
                return
            if not data_available() or not os.path.isdir(library_dir):
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            files_db = os.path.join(share_dir, "files.db")
            if not os.path.isfile(files_db):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            try:
                target = library_file_path(files_db, library_dir, int(match.group("id")))
            except sqlite3.Error as exc:
                print(f"Reading {files_db} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if target is None:
                self._reply(HTTPStatus.NOT_FOUND)
                return
            try:
                f = open(target, "rb")
            except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            except OSError as exc:
                print(f"Opening library file {match.group('id')} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            with f:
                st = os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode):
                    self._reply(HTTPStatus.NOT_FOUND)
                    return
                size = st.st_size
                try:
                    byte_range = parse_byte_range(self.headers.get("Range"), size)
                except RangeNotSatisfiable:
                    self._reply(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                                headers={"Content-Range": f"bytes */{size}"})
                    return
                start, end = byte_range or (0, size - 1)
                length = end - start + 1
                self.send_response(HTTPStatus.PARTIAL_CONTENT if byte_range else HTTPStatus.OK)
                self.send_header("Content-Type", LIBRARY_CONTENT_TYPES.get(
                    os.path.splitext(target)[1].lower(), "application/octet-stream"))
                self.send_header("Content-Length", str(length))
                if byte_range:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
                self.send_header("Cache-Control", "no-cache")
                self.connection.settimeout(LIBRARY_SOCKET_TIMEOUT)
                try:
                    self.end_headers()
                    if send_body:
                        f.seek(start)
                        while length > 0:
                            chunk = f.read(min(LIBRARY_CHUNK_BYTES, length))
                            if not chunk:
                                break  # truncated while being read; the client sees a short body
                            self.wfile.write(chunk)
                            length -= len(chunk)
                except (ConnectionError, TimeoutError):
                    # Seek, skip, crossfade: the player hangs up all the time
                    self.close_connection = True

        def _bearer_token(self) -> str | None:
            return server_rules.bearer_token(self.headers.get("Authorization"))

        def do_POST(self):
            if self._route_users("POST"):
                return
            path = self.path.split("?", 1)[0]
            if users_dir is None or path not in ("/login", "/logout"):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            if path == "/logout":
                self._logout()
            else:
                self._login()

        def _login(self):
            data = self._read_body(LOGIN_MAX_BODY_BYTES)
            if data is None:
                return
            try:
                body = json.loads(data)
                username, password = body["username"], body["password"]
            except (ValueError, TypeError, KeyError):
                self._reply(HTTPStatus.BAD_REQUEST, "expected JSON {\"username\", \"password\"}")
                return
            if not data_available():
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            keys = [("address", self.client_address[0]),
                    ("user", username.strip().lower() if isinstance(username, str) else "")]
            wait = limiter.retry_after(keys)
            if wait:
                self._reply(HTTPStatus.TOO_MANY_REQUESTS, "too many failed logins",
                            headers={"Retry-After": str(wait)})
                return
            name = account_auth.check_login(users_dir, username, password)
            if name is None:
                limiter.failed(keys)
                print(f"Failed login from {self.client_address[0]}", flush=True)
                self._reply(HTTPStatus.UNAUTHORIZED, "wrong username or password")
                return
            limiter.succeeded(keys[1:])
            try:
                # Optional, so old phones log in as before; a bad value is just left out
                device = account_auth.clean_device_name(body.get("device"))
                token = account_auth.issue_token(users_dir, name, device)
            except OSError as exc:
                print(f"Storing a token for {name} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self.log_user = name
            on_device = f" ({device})" if device else ""
            print(f"User {name} logged in from {self.client_address[0]}{on_device}", flush=True)
            self._reply_json(HTTPStatus.OK, {"username": name, "token": token,
                                             "permissions": account_auth.user_permissions(users_dir, name)})

        def _logout(self):
            token = self._bearer_token()
            if token and data_available():
                try:
                    account_auth.revoke_token(users_dir, token)
                except OSError as exc:
                    print(f"Revoking a token failed: {exc}", flush=True)
            # Best effort, and an unknown token says nothing about the others
            self._reply(HTTPStatus.NO_CONTENT)

        def _check_sender(self, user_part: str | None) -> bool:
            """Signed uploads: False (after replying) when the name's user
            part isn't vouched for by the token."""
            if users_dir is None:
                return True
            token = self._bearer_token()
            if token is None and user_part is None:
                return True
            sender = self.log_user = account_auth.token_user(users_dir, token)
            if sender is None:
                self._reply(HTTPStatus.UNAUTHORIZED, "log in again")
                return False
            if user_part is not None and user_part != sender:
                self._reply(HTTPStatus.FORBIDDEN, "the file name names another user")
                return False
            return True

        def _sender(self) -> str | None:
            """The user the token belongs to, or None after replying (503
            drive not mounted - checked first, tokens live there - or 401)."""
            if not data_available():
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return None
            sender = self.log_user = account_auth.token_user(users_dir, self._bearer_token())
            if sender is None:
                self._reply(HTTPStatus.UNAUTHORIZED, "log in again")
            return sender

        def _authorize(self, username: str, forbidden: str) -> bool:
            """True when the token is `username`'s; else replies 503/401/403."""
            sender = self._sender()
            if sender is None:
                return False
            if sender != username:
                self._reply(HTTPStatus.FORBIDDEN, forbidden)
                return False
            return True

        def _open_user_db(self, connect: Callable, username: str, log_name: str):
            """`connect` (a sync module's) to `username`'s user.db, or None
            after replying 503: not migrated yet (the server never migrates)
            or unreadable."""
            try:
                return connect(os.path.join(users_dir, username, playlist_sync.USER_DB_FILENAME))
            except playlist_sync.NotMigratedError as exc:
                print(f"{log_name}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE, "this user's database isn't ready yet")
            except (OSError, sqlite3.Error) as exc:
                print(f"{log_name}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
            return None

        def _profile(self, method: str, match: re.Match):
            username = match.group("user")
            if self._authorize(username, "another user"):
                self._reply_json(HTTPStatus.OK, {"username": username,
                                                 "permissions": account_auth.user_permissions(users_dir, username)})

        # ---- playlists ----

        def _read_body(self, limit: int) -> bytes | None:
            """The whole body, or None after replying 411 (no or a malformed
            Content-Length) / 413 (over `limit`), or after closing the
            connection when it doesn't arrive whole (the phone gave up)."""
            length = plain_int(self.headers.get("Content-Length"))
            if length is None:
                self._reply(HTTPStatus.LENGTH_REQUIRED)
                return None
            if length > limit:
                # Refused on the header alone: the body is never read, so the
                # connection can't serve another request
                self.close_connection = True
                self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"at most {limit} bytes")
                return None
            self.connection.settimeout(UPLOAD_SOCKET_TIMEOUT)
            try:
                data = self.rfile.read(length)
            except OSError:
                data = None
            if data is None or len(data) != length:
                self.close_connection = True
                return None
            self._body_read = True
            return data

        def _read_json_body(self, limit: int):
            """The parsed body, or None after replying (411/413/400; see _read_body)."""
            data = self._read_body(limit)
            if data is None:
                return None
            try:
                return json.loads(data)
            except ValueError:
                self._reply(HTTPStatus.BAD_REQUEST, "not JSON")
                return None

        # ---- the collections synced item by item (sync_collections) ----

        def _collection(self, method: str, match: re.Match):
            url = urlsplit(self.path)
            username, uid = match.group("user"), match.group("uid")
            spec = sync_collections.BY_PATH[match.group("collection")]
            if (method == "GET") != (uid is None):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            # The token before the body: a stranger's upload is never read
            if not self._authorize(username, f"another user's {spec.label}"):
                return
            body = None
            if method == "PUT":
                body = self._read_json_body(body_limits.get(spec.path, spec.max_body))
                if body is None:
                    return
            if uid is not None and not spec.uid_re.match(uid):
                self._reply(HTTPStatus.BAD_REQUEST, f"not a uid of {spec.label}")
                return
            query = parse_qs(url.query)
            try:
                if method == "GET":
                    since = self._query_int(query, "since", default=0)
                elif method == "PUT":
                    parsed = spec.parse(body)
                    base_revision = playlist_sync.parse_base_revision(body.get("base_revision"))
                    if body.get("uid", uid) != uid:
                        raise spec.bad_error("uid: differs from the one in the path")
                else:
                    base_revision = self._query_int(query, "base_revision")
                    deleted_at = self._query_int(query, "deleted_at")
            except spec.bad_error as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return

            folder = os.path.join(users_dir, username)
            # Before the transaction: it reads another file
            checked_to = spec.resolve(parsed, os.path.join(share_dir, "music.db")) \
                if method == "PUT" and spec.resolve else None
            log_name = f"{spec.label} of {username}"
            conn = self._open_user_db(spec.connect, username, log_name)
            if conn is None:
                return
            archived = None
            try:
                if method == "GET":
                    self._reply_json(HTTPStatus.OK, spec.changes_since(conn, since))
                    return
                with covers_lock:
                    if method == "PUT":
                        if spec.uses_covers:
                            self._require_cover(folder, parsed["cover"])
                        applied, stored = spec.store(conn, folder, username, uid, parsed, base_revision, checked_to)
                    else:
                        applied, stored, *rest = spec.delete(conn, folder, uid, base_revision, deleted_at)
                        archived = rest[0] if rest else None
                    if spec.uses_covers:
                        user_covers.clean_up_quietly(conn, folder, username)
            except user_covers.CoverMissingError as exc:
                self._reply_json(HTTPStatus.BAD_REQUEST, {"error": "cover_missing", "cover": exc.cover})
                return
            except spec.bad_error as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"{log_name}: {method} {uid} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            finally:
                conn.close()
            if applied:
                if stored.get("deleted"):
                    what = f"deleted (archived in {archived})" if archived else "deleted"
                else:
                    key, noun = spec.counted or (None, None)
                    what = f"stored ({len(stored.get(key, []))} {noun})" if key else "stored"
                print(f"{log_name}: {stored['uid']} {what}, revision {stored['revision']}", flush=True)
            self._reply_json(HTTPStatus.OK if applied else HTTPStatus.CONFLICT, stored)

        # ---- listens (plays, for the phone's listening stats) ----

        def _listens(self, method: str, match: re.Match):
            url = urlsplit(self.path)
            username = match.group("user")
            if not self._authorize(username, "another user's listens"):
                return
            body = None
            if method == "POST":
                body = self._read_json_body(LISTEN_MAX_BODY_BYTES)
                if body is None:
                    return
            query = parse_qs(url.query)
            try:
                if method == "GET":
                    since = self._query_int(query, "since", default=0)
                    limit = self._query_int(query, "limit", default=listen_sync.DEFAULT_LIMIT)
                    if limit < 1:
                        raise listen_sync.BadListenError("limit: expected an integer >= 1")
                else:
                    plays = listen_sync.parse_listens(body)
            except listen_sync.BadListenError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return

            conn = self._open_user_db(listen_sync.connect, username, f"Listens of {username}")
            if conn is None:
                return
            try:
                if method == "GET":
                    self._reply_json(HTTPStatus.OK, listen_sync.changes_since(conn, since, limit))
                    return
                result = listen_sync.store_listens(conn, plays)
            except (OSError, sqlite3.Error) as exc:
                print(f"Listens of {username}: {method} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            finally:
                conn.close()
            if result["added"] or result["updated"]:
                print(f"Listens of {username}: {result['added']} added, {result['updated']} updated, "
                      f"revision {result['revision']}", flush=True)
            self._reply_json(HTTPStatus.OK, {"revision": result["revision"]})

        # ---- covers ----

        @staticmethod
        def _require_cover(folder: str, cover) -> None:
            if cover is not playlist_sync.KEEP_COVER:
                user_covers.require_blob(folder, cover)

        def _cover_blob(self, method: str, match: re.Match):
            username, sha = match.group("user"), match.group("sha")
            if not self._authorize(username, "another user's covers"):
                return
            data = None
            if method == "PUT":
                data = self._read_body(user_covers.MAX_COVER_BYTES)
                if data is None:
                    return
            if not user_covers.is_sha256(sha):
                self._reply(HTTPStatus.BAD_REQUEST, "expected the lowercase hex sha256 of the picture")
                return
            folder = os.path.join(users_dir, username)
            if method == "GET":
                path = user_covers.blob_path(folder, sha)
                try:
                    body = path.read_bytes() if path is not None else None
                except OSError:
                    body = None  # removed by a clean-up just now
                if body is None:
                    self._reply(HTTPStatus.NOT_FOUND)
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", user_covers.content_type_of(path))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", COVER_CACHE_CONTROL)
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                with covers_lock:
                    created = user_covers.store_blob(folder, sha, data, self.headers.get("Content-Type"))
            except user_covers.HashMismatchError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except user_covers.UnsupportedImageError as exc:
                self._reply(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc))
                return
            except OSError as exc:
                print(f"Storing cover {sha} of {username} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if created:
                print(f"Cover {sha} of {username} stored ({len(data)} bytes)", flush=True)
            self._reply(HTTPStatus.CREATED if created else HTTPStatus.OK, "stored" if created else "already stored")

        def _global_mood_covers(self, method: str, match: re.Match):
            """The user's private covers of global moods (mood_sync.py)."""
            url = urlsplit(self.path)
            username, mood_id = match.group("user"), match.group("mood")
            if (method == "GET") != (mood_id is None):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            if not self._authorize(username, "another user's covers"):
                return
            body = None
            if method == "PUT":
                body = self._read_json_body(GLOBAL_MOOD_COVER_MAX_BODY_BYTES)
                if body is None:
                    return
            try:
                if method == "GET":
                    since = self._query_int(parse_qs(url.query), "since", default=0)
                else:
                    if (plain_int(mood_id) or 0) < 1:
                        raise mood_sync.BadMoodError("expected /global-mood-covers/<mood id>")
                    mood_id = int(mood_id)
                    change = mood_sync.parse_global_cover(body)
                    if body.get("mood_id", mood_id) != mood_id:
                        raise mood_sync.BadMoodError("mood_id: differs from the one in the path")
            except mood_sync.BadMoodError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return
            folder = os.path.join(users_dir, username)
            conn = self._open_user_db(mood_sync.connect, username, f"Covers of {username}")
            if conn is None:
                return
            try:
                if method == "GET":
                    self._reply_json(HTTPStatus.OK, mood_sync.global_covers_since(conn, since))
                    return
                with covers_lock:
                    user_covers.require_blob(folder, change["cover"])
                    applied, stored = mood_sync.store_global_cover(conn, mood_id, change["cover"],
                                                                   change["base_revision"], change["updated_at"])
                    user_covers.clean_up_quietly(conn, folder, username)
            except user_covers.CoverMissingError as exc:
                self._reply_json(HTTPStatus.BAD_REQUEST, {"error": "cover_missing", "cover": exc.cover})
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"Covers of {username}: PUT global mood {mood_id} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            finally:
                conn.close()
            if applied:
                print(f"Cover of global mood {mood_id} for {username}: {stored['cover'] or 'removed'}, "
                      f"revision {stored['revision']}", flush=True)
            self._reply_json(HTTPStatus.OK if applied else HTTPStatus.CONFLICT, stored)

        # ---- global moods (tag.db), only with the permission ----

        def _global_mood_song(self, method: str, match: re.Match):
            """PUT adds a song to a global mood, DELETE removes it. Recorded
            in --mood-changes for MusicDatabase to apply (global_mood_log.py)."""
            if mood_changes_dir is None:
                self._reply(HTTPStatus.NOT_FOUND)
                return
            sender = self._sender()
            if sender is None:
                return
            permission = account_auth.PERMISSION_EDIT_GLOBAL_MOODS
            if permission not in account_auth.user_permissions(users_dir, sender):
                # JSON, unlike "another user's token", so the phone can tell them apart
                self._reply_json(HTTPStatus.FORBIDDEN, {"error": "permission", "permission": permission})
                return
            mood_id, requested = match.group("mood"), match.group("song")
            if plain_int(mood_id) is None or (plain_int(requested) or 0) < 1:
                self._reply(HTTPStatus.BAD_REQUEST, "expected /global-moods/<mood id>/songs/<song id>")
                return
            mood_id, requested = int(mood_id), int(requested)
            # Both checked against the copies the phone downloads: a mood or
            # song it knows is in them
            try:
                mood_name = global_mood_log.global_mood_name(os.path.join(share_dir, "tag.db"), mood_id)
            except sqlite3.Error as exc:
                print(f"Reading the shared tag.db for global mood {mood_id} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if mood_name is None:
                self._reply_json(HTTPStatus.NOT_FOUND, {"error": "mood", "mood_id": mood_id})
                return
            # A merged song becomes the kept one; deleted or unknown -> 404
            ids, checked_to = mood_sync.resolve_song_ids([requested], os.path.join(share_dir, "music.db"))
            if not ids:
                self._reply_json(HTTPStatus.NOT_FOUND, {"error": "song", "song_id": requested})
                return
            song_id = ids[0]
            action = global_mood_log.ACTION_ADD if method == "PUT" else global_mood_log.ACTION_REMOVE
            try:
                global_mood_log.record_change(mood_changes_dir, sender, action, mood_id, mood_name,
                                              song_id, requested, checked_to)
            except OSError as exc:
                print(f"Recording a global mood change by {sender} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            print(f"{sender}: {action} song {song_id} {'to' if action == 'add' else 'from'} global mood "
                  f"{mood_id} ({mood_name}), pending", flush=True)
            self._reply_json(HTTPStatus.ACCEPTED, {"mood_id": mood_id, "song_id": song_id, "action": action})

        @staticmethod
        def _query_int(query: dict, name: str, default: int | None = None) -> int:
            values = query.get(name)
            if not values:
                if default is None:
                    raise playlist_sync.BadPlaylistError(f"{name}: missing")
                return default
            value = plain_int(values[0])
            if value is None:
                raise playlist_sync.BadPlaylistError(f"{name}: expected an integer >= 0")
            return value

        # ---- inbox ----

        def do_PUT(self):
            if self._route_users("PUT"):
                return
            name = self.path.split("?", 1)[0]
            if inbox_dir is None or not name.startswith(INBOX_PREFIX):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            name = name[len(INBOX_PREFIX):]
            match = REQUEST_NAME_RE.match(name)
            if not match:
                self._reply(HTTPStatus.BAD_REQUEST, "not a request file name")
                return
            if not data_available():
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            # After the mount check: tokens live on the drive too, and a
            # missing drive mustn't look like "log in again". Before the
            # body: a bad token's upload is never read.
            if not self._check_sender(match.group("user")):
                return
            unsigned = match.group("user") is None
            if unsigned:
                wait = upload_limiter.take(self.client_address[0])
                if wait:
                    print(f"{self.client_address[0]}: too many unsigned requests", flush=True)
                    self._reply(HTTPStatus.TOO_MANY_REQUESTS, "too many requests without logging in",
                                headers={"Retry-After": str(wait)})
                    return
            data = self._read_body(max_request_bytes)
            if data is None:
                return
            if not data.startswith(SQLITE_HEADER):
                self._reply(HTTPStatus.BAD_REQUEST, "not an SQLite file")
                return
            self._store(name, data, unsigned)

        def _store(self, name: str, data: bytes, unsigned: bool = False):
            try:
                outcome = inbox.store(name, data, unsigned)
            except OSError as exc:
                print(f"Storing request {name} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if outcome == inbox_files.STORED:
                print(f"Received request {name} ({len(data)} bytes)", flush=True)
            self._reply(*INBOX_ANSWERS[outcome])

        def _reply(self, status: HTTPStatus, detail: str | None = None, headers: dict | None = None):
            if status == HTTPStatus.NO_CONTENT:
                self._send(status, b"", None, headers)
                return
            body = f"{status.value} {status.phrase}{f' - {detail}' if detail else ''}\n".encode()
            self._send(status, body, "text/plain; charset=utf-8", headers)

        def _reply_json(self, status: HTTPStatus, payload):
            self._send(status, json.dumps(payload).encode(), "application/json", None)

        def _body_left_unread(self) -> bool:
            headers = getattr(self, "headers", None)
            if self._body_read or headers is None:
                return False
            length = headers.get("Content-Length")
            return (length is not None and length.strip() != "0") or "Transfer-Encoding" in headers

        def _send(self, status: HTTPStatus, body: bytes, content_type: str | None, headers: dict | None):
            self.send_response(status)
            if self._body_left_unread():
                # Answered without reading the body (a bad token, too big):
                # the connection can't serve another request, and nothing
                # waits for the rest of it
                self.send_header("Connection", "close")
            if content_type:
                self.send_header("Content-Type", content_type)
            if status != HTTPStatus.NO_CONTENT:
                self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

    return ShareHandler


def tailscale_dns_name() -> str | None:
    """This machine's Tailscale MagicDNS name ("host.tailnet.ts.net"), or None."""
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json", "--peers=false"], capture_output=True, text=True, timeout=10
        ).stdout
        name = json.loads(out)["Self"]["DNSName"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None
    return name.rstrip(".").lower() if isinstance(name, str) and name else None


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
    return next((str(a) for a in _interface_addresses(interface)
                 if a.is_private and not a.is_loopback and a not in TAILSCALE_NETWORK), None)


def vpn_interface_ipv4(interface: str) -> str | None:
    """
    The IPv4 address of a named VPN interface (e.g. "wg0", "tun0"), or None
    if it has none (yet): a private one or one from 100.64.0.0/10 (where
    Tailscale and other VPNs hand theirs out) - never a public or loopback one.
    """
    return next((str(a) for a in _interface_addresses(interface)
                 if not a.is_loopback and (a.is_private or a in TAILSCALE_NETWORK)), None)


def _interface_addresses(interface: str) -> list:
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", interface],
            capture_output=True, text=True, timeout=10,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return []
    # `ip -o` prints one line per address: "2: enp2s0    inet 192.0.2.10/24 brd ..."
    found = []
    for i, token in enumerate(out[:-1]):
        if token != "inet":
            continue
        try:
            found.append(ipaddress.IPv4Interface(out[i + 1]).ip)
        except ValueError:
            continue
    return found


def tls_context(tls_dir: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(os.path.join(tls_dir, TLS_CERT_FILE), os.path.join(tls_dir, TLS_KEY_FILE))
    return context


# Shared by every socket main() serves on, so MAX_CONNECTIONS is per process
CONNECTION_SLOTS = threading.BoundedSemaphore(MAX_CONNECTIONS)


class CappedHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer serving at most MAX_CONNECTIONS connections at
    once (`slots`): one more is closed right away, without a thread - idle or
    slow clients can't use up the machine's threads."""

    def __init__(self, address, handler, slots: threading.BoundedSemaphore | None = None):
        super().__init__(address, handler)
        self.slots = slots if slots is not None else CONNECTION_SLOTS
        self._refusing = False

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            if not self._refusing:  # once per run of refusals, not per connection
                print(f"Too many connections, closing new ones (first from {client_address[0]})", flush=True)
            self._refusing = True
            self.shutdown_request(request)
            return
        self._refusing = False
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()  # the thread never started
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class SharingServer(CappedHTTPServer):
    """CappedHTTPServer that, with a TLS context, does each handshake in the
    connection's own thread (accepting stays quick) and doesn't print a
    traceback for a client that fails it - a plain-HTTP or impatient client."""

    def __init__(self, address, handler, tls: ssl.SSLContext | None = None,
                 slots: threading.BoundedSemaphore | None = None):
        super().__init__(address, handler, slots)
        self.tls = tls
        if tls is not None:
            self.socket = tls.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)

    def finish_request(self, request, client_address):
        if isinstance(request, ssl.SSLSocket):
            request.settimeout(TLS_HANDSHAKE_TIMEOUT)
            try:
                request.do_handshake()
            except (ssl.SSLError, OSError):
                return
            request.settimeout(None)
        super().finish_request(request, client_address)


def bind_when_ready(handler, port: int, get_address=None, label: str = "Tailscale",
                    tls: ssl.SSLContext | None = None) -> CappedHTTPServer:
    get_address = get_address or tailscale_ipv4
    while True:
        address = get_address()
        if address is None:
            print(f"No {label} address yet, retrying in {RETRY_SECONDS}s", flush=True)
        else:
            try:
                if tls is not None:
                    return SharingServer((address, port), handler, tls)
                return CappedHTTPServer((address, port), handler)
            except OSError as exc:
                print(f"Can't bind {address}:{port} ({exc}), retrying in {RETRY_SECONDS}s", flush=True)
        time.sleep(RETRY_SECONDS)


def _ipv4(value: str) -> str:
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an IPv4 address: {value}")


def address_sources(lan_interface: str | None, tailscale: bool = True, addresses=(),
                    vpn_interface: str | None = None) -> list[tuple[str, Callable[[], str | None]]]:
    """(label, address getter) for every socket to serve on."""
    sources = [("Tailscale", tailscale_ipv4)] if tailscale else []
    if lan_interface:
        sources.append((f"LAN {lan_interface}", lambda: interface_ipv4(lan_interface)))
    if vpn_interface:
        sources.append((f"VPN {vpn_interface}", lambda: vpn_interface_ipv4(vpn_interface)))
    for address in addresses:
        sources.append((f"address {address}", lambda address=address: address))
    return sources


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dir", required=True, help="folder holding music.db, tag.db and files.db")
    parser.add_argument("--mount", help="mount point of the external drive holding --dir "
                                        "(leave out when --dir is on the system disk)")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--lan-interface", help="also serve on this interface's private IPv4 address")
    parser.add_argument("--no-tailscale", action="store_true", help="don't serve on the Tailscale address")
    parser.add_argument("--vpn-interface", help="also serve on this VPN interface's address (any provider)")
    parser.add_argument("--address", action="append", default=[], type=_ipv4,
                        help="also serve on exactly this IPv4 address (repeatable)")
    parser.add_argument("--inbox", help="accept requests from Leina Radio into this folder (on --mount)")
    parser.add_argument("--max-request-bytes", type=int, default=DEFAULT_MAX_REQUEST_BYTES)
    parser.add_argument("--inbox-limit-bytes", type=int, default=DEFAULT_INBOX_LIMIT_BYTES)
    parser.add_argument("--users", help="log in as the users in this folder (on --mount) and sync their playlists")
    parser.add_argument("--max-playlist-bytes", type=int, default=DEFAULT_MAX_PLAYLIST_BYTES)
    parser.add_argument("--mood-changes", help="with --users: record permitted users' changes to global moods "
                                               "in this folder (on --mount)")
    parser.add_argument("--library", help="serve the music library files indexed in --dir/files.db from this folder")
    parser.add_argument("--tls-dir", help="serve HTTPS with the certificate in this folder, made there on first start")
    args = parser.parse_args(argv)

    tls = None
    if args.tls_dir:
        try:
            ensure_tls_certificate(args.tls_dir)
            tls = tls_context(args.tls_dir)
        except (OSError, subprocess.SubprocessError, ssl.SSLError) as exc:
            print(f"Can't use the HTTPS certificate in {args.tls_dir}: {exc}", file=sys.stderr, flush=True)
            return 2
        print(f"HTTPS certificate code (compare it in Leina Radio): {tls_certificate_code(args.tls_dir)}", flush=True)
    scheme = "https" if tls else "http"

    host_names = {socket.gethostname().lower()}
    handler = make_handler(args.dir, args.mount, args.inbox, args.max_request_bytes, args.inbox_limit_bytes,
                           users_dir=args.users, max_playlist_bytes=args.max_playlist_bytes,
                           mood_changes_dir=args.mood_changes, library_dir=args.library, host_names=host_names)

    def bind_and_serve(label, get_address):
        server = bind_when_ready(handler, args.port, get_address, label, tls)
        # Tailscale has a name by the time it has an address; asked by every
        # socket, since a VPN interface may be Tailscale's too
        dns_name = tailscale_dns_name()
        if dns_name:
            host_names.add(dns_name)
        host, port = server.server_address[:2]
        inbox = f", requests into {args.inbox}" if args.inbox else ""
        inbox += f", logins, playlists, moods, folders, saved songs and links, listens and covers for {args.users}" if args.users else ""
        inbox += f", global mood changes into {args.mood_changes}" if args.users and args.mood_changes else ""
        inbox += f", library files from {args.library}" if args.library else ""
        print(f"Serving {', '.join(SHARED_FILES)} from {args.dir}{inbox} on {scheme}://{host}:{port}/ ({label})", flush=True)
        server.serve_forever()

    sources = address_sources(args.lan_interface, tailscale=not args.no_tailscale, addresses=args.address,
                              vpn_interface=args.vpn_interface)
    if not sources:
        print("No address to serve on: --no-tailscale without --lan-interface, --vpn-interface or --address",
              file=sys.stderr, flush=True)
        return 2
    # One socket per address, each bound and served independently; the
    # process lives as long as any of them does.
    threads = [threading.Thread(target=bind_and_serve, args=source, name=source[0]) for source in sources]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
