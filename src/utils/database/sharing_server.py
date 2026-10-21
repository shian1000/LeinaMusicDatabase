"""
Minimal HTTP server for the MusicDatabaseApp mobile client: serves exactly
<dir>/music.db, <dir>/tag.db and <dir>/files.db (the music library index),
nothing else (no directory listing, any
other path is a 404), on this machine's Tailscale address and, optionally,
on one named LAN interface. With --inbox it also takes requests from the
phone: `PUT /inbox/<name>.db` stores one request file in <inbox>/new/ for
Manage database -> "Resolve new requests" (utils/database/inbox_requests.py).
With --users the phone can also log in as a MusicDatabase user (POST /login,
POST /logout) and sign its uploads with the token it gets.

Run by the systemd user service that sharing.install_sharing_service()
installs. Deliberately standard-library only and independent of the rest of
the app (no settings.py / venv imports), so it runs on the system python3
with every path passed on the command line.

    python3 sharing_server.py --dir DIR --mount MOUNT --port 8002 [--lan-interface enp2s0] [--inbox DIR] [--users DIR]
                              [--mood-changes DIR] [--library DIR]

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
  most --max-request-bytes each, and nothing once <inbox>/new/ and review/
  (left to the owner by the app's automatic pass) hold --inbox-limit-bytes -
  settling a request moves it to processed/ and frees the space. Never
  overwrites: the same file sent again is a 200, another file under a taken
  name a 409. Answers:
  201 stored, 200 already here (new/, review/ or processed/), 400 bad name or not
  SQLite, 409 name taken, 411 no Content-Length, 413 too big, 507 inbox
  full, 503 drive not mounted.
- Logging in (only with --users; password and token checks in
  account_auth.py, the one module shared with the app): POST /login with
  JSON {"username", "password"} -> 200 {"username": <normalized>, "token"},
  401 wrong username or password (never says which), 429 locked out (5 failed
  logins within 15 minutes from one address or for one username lock both
  for 5 minutes - in memory, a restart clears it), 503 drive not mounted.
  POST /logout with "Authorization: Bearer <token>" revokes it; always 204.
- Signed uploads (only with --users): the user part of a request file name
  is always a verified sender. A name with one needs that user's token (none
  or unknown/revoked -> 401, the phone logs in again; another user's -> 403);
  a name without one is accepted with or without a token, like before login.
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
  {"error": "mood"} / {"error": "song"}); the change is only recorded in
  --mood-changes/pending/ (202) - MusicDatabase applies it to the original
  tag.db later (global_mood_log.py). The server never writes tag.db.
- Covers (only with --users; covers.py): PUT /users/<name>/covers/<sha256>
  stores a PNG/JPEG picture under the SHA-256 of its bytes (201 new, 200
  already stored, 400 hash mismatch, 413 over 4 MiB, 415 another type or
  bytes that aren't one), GET answers it (404 unknown, cacheable forever).
  Playlists and moods carry the hash in "cover" (a PUT without the key keeps
  the stored one; a hash with no blob -> 400 {"error": "cover_missing"}).
  GET /users/<name>/global-mood-covers?since=<revision> and
  PUT /users/<name>/global-mood-covers/<mood id> sync the user's private
  covers of global moods like moods. After every applied write, blobs the
  user's rows no longer use are removed once a day old.
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
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib.parse
from urllib.parse import parse_qs, urlsplit

try:
    from . import (account_auth, covers, folder_sync, global_mood_log, listen_sync, mood_sync,  # as utils.database.sharing_server
                   playlist_sync, saved_sync)
except ImportError:
    import account_auth  # run as a script by the systemd service (its folder is on sys.path)
    import covers
    import folder_sync
    import global_mood_log
    import listen_sync
    import mood_sync
    import playlist_sync
    import saved_sync

SHARED_FILES = ("music.db", "tag.db", "files.db")
RETRY_SECONDS = 10
TAILSCALE_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")

# [<user>_]<YYYY-MM-DD>_<HH-MM-SS>_<number>.db - the phone's request file names.
# The user part is a username (account_auth.USERNAME_PATTERN, which may hold
# "_"); the date after it has a fixed shape, so the split stays unambiguous.
REQUEST_NAME_RE = re.compile(
    r"^(?:(?P<user>[a-z0-9][a-z0-9_-]{1,31})_)?\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{3,9}\.db$")
SQLITE_HEADER = b"SQLite format 3\x00"
INBOX_PREFIX = "/inbox/"
INBOX_NEW = "new"
INBOX_REVIEW = "review"
INBOX_PROCESSED = "processed"
DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_INBOX_LIMIT_BYTES = 100 * 1024 * 1024
# A phone that stops sending mid-upload mustn't hold a server thread forever
UPLOAD_SOCKET_TIMEOUT = 60
# Partial uploads start with a dot; the inbox reader skips them
_PART_PREFIX = "."
_PART_SUFFIX = ".part"
LOGIN_MAX_BODY_BYTES = 4096
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 5 * 60
# A playlist may have up to 5000 tracks (the app's limit)
DEFAULT_MAX_PLAYLIST_BYTES = 8 * 1024 * 1024
# /users/<name>/playlists[/<uid>]
PLAYLISTS_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/playlists(?:/(?P<uid>[^/]+))?$")
# /users/<name>/moods[/<uid>]
MOODS_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/moods(?:/(?P<uid>[^/]+))?$")
# /users/<name>/folders[/<uid>]
FOLDERS_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/folders(?:/(?P<uid>[^/]+))?$")
# A folder holds only refs; the app has no limit, this is generous
FOLDER_MAX_BODY_BYTES = 1024 * 1024
# /users/<name>/saved-songs[/<uid>], /users/<name>/later-links[/<uid>]
SAVED_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/(?P<collection>saved-songs|later-links)(?:/(?P<uid>[^/]+))?$")
# One song or link with a few names; generous
SAVED_MAX_BODY_BYTES = 64 * 1024
# /users/<name>/listens
LISTENS_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/listens$")
# The phone sends up to 500 plays, ~250 KB of JSON
LISTEN_MAX_BODY_BYTES = 1024 * 1024
# /users/<name>: who the token is and what they may do
USER_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)$")
# /users/<name>/covers/<sha256>
COVERS_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/covers/(?P<sha>[^/]+)$")
# /users/<name>/global-mood-covers[/<mood id>]
GLOBAL_MOOD_COVERS_PATH_RE = re.compile(r"^/users/(?P<user>[^/]+)/global-mood-covers(?:/(?P<mood>[^/]+))?$")
GLOBAL_MOOD_COVER_MAX_BODY_BYTES = 4096
# A hash never changes its picture
COVER_CACHE_CONTROL = "private, max-age=31536000, immutable"
# /global-moods/<mood id>/songs/<song id>
GLOBAL_MOOD_SONG_PATH_RE = re.compile(r"^/global-moods/(?P<mood>[^/]+)/songs/(?P<song>[^/]+)$")
# /library/<files.id>
LIBRARY_PATH_RE = re.compile(r"^/library/(?P<id>[1-9][0-9]{0,17})$")
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


class LoginLimiter:
    """Failed logins per client address and per username: LOGIN_MAX_FAILURES
    within the window lock that key out for LOGIN_LOCKOUT_SECONDS. In memory
    only."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[tuple, list[float]] = {}
        self._locked_until: dict[tuple, float] = {}

    def retry_after(self, keys) -> int:
        """Seconds until every key may try again; 0 = not locked."""
        now = self._clock()
        with self._lock:
            until = max((self._locked_until.get(k, 0.0) for k in keys), default=0.0)
        return max(0, int(until - now + 0.999))

    def failed(self, keys) -> None:
        now = self._clock()
        with self._lock:
            for key in keys:
                recent = [t for t in self._failures.get(key, []) if now - t < LOGIN_FAILURE_WINDOW_SECONDS]
                recent.append(now)
                if len(recent) >= LOGIN_MAX_FAILURES:
                    self._locked_until[key] = now + LOGIN_LOCKOUT_SECONDS
                    recent = []
                self._failures[key] = recent

    def succeeded(self, keys) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


def _dir_size(path: str) -> int:
    total = 0
    if not os.path.isdir(path):
        return 0
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
    return total


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
    uri = "file:" + urllib.parse.quote(os.path.abspath(files_db)) + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
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


def _same_content(path: str, data: bytes) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(data) + 1) == data
    except OSError:
        return False


def make_handler(share_dir: str, mount: str, inbox_dir: str | None = None,
                 max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
                 inbox_limit_bytes: int = DEFAULT_INBOX_LIMIT_BYTES,
                 users_dir: str | None = None, limiter: LoginLimiter | None = None,
                 max_playlist_bytes: int = DEFAULT_MAX_PLAYLIST_BYTES, mood_changes_dir: str | None = None,
                 library_dir: str | None = None):
    # One lock for "is there room / is the name free" + the write, so two
    # uploads at once can't both squeeze past the limit or take one name.
    inbox_lock = threading.Lock()
    # Storing a blob, checking a cover exists + the write naming it, and the
    # clean-up never interleave, so a clean-up can't remove a blob between
    # the check and the commit
    covers_lock = threading.Lock()
    limiter = limiter or LoginLimiter()

    class ShareHandler(BaseHTTPRequestHandler):
        server_version = "MusicDatabaseShare"
        sys_version = ""

        def do_HEAD(self):
            self._serve(send_body=False)

        def _route_users(self, method: str) -> bool:
            """The endpoints that need --users; False if the path is none of them."""
            if users_dir is None:
                return False
            path = urlsplit(self.path).path
            if PLAYLISTS_PATH_RE.match(path):
                self._playlists(method)
            elif MOODS_PATH_RE.match(path):
                self._moods(method)
            elif FOLDERS_PATH_RE.match(path):
                self._folders(method)
            elif SAVED_PATH_RE.match(path):
                self._saved(method)
            elif method in ("GET", "POST") and LISTENS_PATH_RE.match(path):
                self._listens(method)
            elif method in ("GET", "PUT") and COVERS_PATH_RE.match(path):
                self._cover_blob(method)
            elif GLOBAL_MOOD_COVERS_PATH_RE.match(path):
                self._global_mood_covers(method)
            elif method == "GET" and USER_PATH_RE.match(path):
                self._profile(USER_PATH_RE.match(path).group("user"))
            elif method in ("PUT", "DELETE") and GLOBAL_MOOD_SONG_PATH_RE.match(path):
                self._global_mood_song(method)
            else:
                return False
            return True

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

        # ---- library files ----

        def _library_file(self, path: str, send_body: bool):
            match = LIBRARY_PATH_RE.match(path)
            if library_dir is None or match is None:
                self._reply(HTTPStatus.NOT_FOUND)
                return
            if not os.path.ismount(mount) or not os.path.isdir(library_dir):
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
            value = self.headers.get("Authorization")
            if value is None:
                return None
            scheme, _, token = value.partition(" ")
            # A malformed header is a bad token (401), not "no token"
            return token.strip() if scheme.lower() == "bearer" else ""

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
            length = self.headers.get("Content-Length")
            if length is None or not length.isdigit():
                self._reply(HTTPStatus.LENGTH_REQUIRED)
                return
            if int(length) > LOGIN_MAX_BODY_BYTES:
                self.close_connection = True
                self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            self.connection.settimeout(UPLOAD_SOCKET_TIMEOUT)
            try:
                body = json.loads(self.rfile.read(int(length)))
                username, password = body["username"], body["password"]
            except (OSError, ValueError, TypeError, KeyError):
                self._reply(HTTPStatus.BAD_REQUEST, "expected JSON {\"username\", \"password\"}")
                return
            if not os.path.ismount(mount):
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
                token = account_auth.issue_token(users_dir, name)
            except OSError as exc:
                print(f"Storing a token for {name} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            print(f"User {name} logged in from {self.client_address[0]}", flush=True)
            self._reply_json(HTTPStatus.OK, {"username": name, "token": token,
                                             "permissions": account_auth.user_permissions(users_dir, name)})

        def _logout(self):
            token = self._bearer_token()
            if token and os.path.ismount(mount):
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
            sender = account_auth.token_user(users_dir, token)
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
            if not os.path.ismount(mount):
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return None
            sender = account_auth.token_user(users_dir, self._bearer_token())
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

        def _profile(self, username: str):
            if self._authorize(username, "another user"):
                self._reply_json(HTTPStatus.OK, {"username": username,
                                                 "permissions": account_auth.user_permissions(users_dir, username)})

        # ---- playlists ----

        def _read_json_body(self, limit: int):
            """The parsed body, or None after replying (411/413/400)."""
            length = self.headers.get("Content-Length")
            if length is None or not length.isdigit():
                self._reply(HTTPStatus.LENGTH_REQUIRED)
                return None
            if int(length) > limit:
                self.close_connection = True
                self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"at most {limit} bytes")
                return None
            self.connection.settimeout(UPLOAD_SOCKET_TIMEOUT)
            try:
                data = self.rfile.read(int(length))
            except OSError:
                self.close_connection = True
                return None
            try:
                return json.loads(data)
            except ValueError:
                self._reply(HTTPStatus.BAD_REQUEST, "not JSON")
                return None

        def _playlists(self, method: str):
            url = urlsplit(self.path)
            match = PLAYLISTS_PATH_RE.match(url.path)
            username, uid = match.group("user"), match.group("uid")
            if (method == "GET") != (uid is None):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            body = None
            if method == "PUT":
                body = self._read_json_body(max_playlist_bytes)
                if body is None:
                    return
            if not self._authorize(username, "another user's playlists"):
                return
            if uid is not None and not playlist_sync.UID_RE.match(uid):
                self._reply(HTTPStatus.BAD_REQUEST, "not a playlist uid")
                return
            query = parse_qs(url.query)
            try:
                if method == "GET":
                    since = self._query_int(query, "since", default=0)
                elif method == "PUT":
                    playlist = playlist_sync.parse_playlist(body)
                    base_revision = playlist_sync.parse_base_revision(body.get("base_revision"))
                    if body.get("uid", uid) != uid:
                        raise playlist_sync.BadPlaylistError("uid: differs from the one in the path")
                else:
                    base_revision = self._query_int(query, "base_revision")
                    deleted_at = self._query_int(query, "deleted_at")
            except playlist_sync.BadPlaylistError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return

            folder = os.path.join(users_dir, username)
            try:
                if method == "PUT":
                    # Before the transaction: it reads another file
                    checked_to = playlist_sync.resolve_song_ids(playlist["tracks"],
                                                                os.path.join(share_dir, "music.db"))
                conn = playlist_sync.connect(os.path.join(folder, playlist_sync.USER_DB_FILENAME))
            except playlist_sync.NotMigratedError as exc:
                print(f"Playlists of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE, "this user's database isn't ready yet")
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"Playlists of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                if method == "GET":
                    self._reply_json(HTTPStatus.OK, playlist_sync.changes_since(conn, since))
                    return
                with covers_lock:
                    if method == "PUT":
                        self._require_cover(folder, playlist["cover"])
                        applied, stored = playlist_sync.store_playlist(conn, username, uid, playlist, base_revision,
                                                                       song_ids_checked_to=checked_to)
                        archived = None
                    else:
                        applied, stored, archived = playlist_sync.delete_playlist(conn, folder, uid, base_revision,
                                                                                  deleted_at)
                    self._clean_up_covers(conn, folder, username)
            except covers.CoverMissingError as exc:
                self._reply_json(HTTPStatus.BAD_REQUEST, {"error": "cover_missing", "cover": exc.cover})
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"Playlists of {username}: {method} {uid} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            finally:
                conn.close()
            if applied and (method == "PUT" or archived):
                what = "deleted (archived in " + str(archived) + ")" if archived else \
                    f"stored ({len(stored.get('tracks', []))} tracks)"
                print(f"Playlist {stored['uid']} of {username} {what}, revision {stored['revision']}", flush=True)
            self._reply_json(HTTPStatus.OK if applied else HTTPStatus.CONFLICT, stored)

        # ---- moods (a user's own) ----

        def _moods(self, method: str):
            url = urlsplit(self.path)
            match = MOODS_PATH_RE.match(url.path)
            username, uid = match.group("user"), match.group("uid")
            if (method == "GET") != (uid is None):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            body = None
            if method == "PUT":
                body = self._read_json_body(max_playlist_bytes)
                if body is None:
                    return
            if not self._authorize(username, "another user's moods"):
                return
            if uid is not None and not playlist_sync.UID_RE.match(uid):
                self._reply(HTTPStatus.BAD_REQUEST, "not a mood uid")
                return
            query = parse_qs(url.query)
            try:
                if method == "GET":
                    since = self._query_int(query, "since", default=0)
                elif method == "PUT":
                    mood = mood_sync.parse_mood(body)
                    base_revision = playlist_sync.parse_base_revision(body.get("base_revision"))
                    if body.get("uid", uid) != uid:
                        raise mood_sync.BadMoodError("uid: differs from the one in the path")
                else:
                    base_revision = self._query_int(query, "base_revision")
                    deleted_at = self._query_int(query, "deleted_at")
            except mood_sync.BadMoodError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return

            folder = os.path.join(users_dir, username)
            try:
                if method == "PUT":
                    mood["song_ids"], checked_to = mood_sync.resolve_song_ids(mood["song_ids"],
                                                                              os.path.join(share_dir, "music.db"))
                conn = mood_sync.connect(os.path.join(folder, playlist_sync.USER_DB_FILENAME))
            except mood_sync.NotMigratedError as exc:
                print(f"Moods of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE, "this user's database isn't ready yet")
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"Moods of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                if method == "GET":
                    self._reply_json(HTTPStatus.OK, mood_sync.changes_since(conn, since))
                    return
                archived = None
                with covers_lock:
                    if method == "PUT":
                        self._require_cover(folder, mood["cover"])
                        applied, stored = mood_sync.store_mood(conn, folder, uid, mood, base_revision,
                                                               song_ids_checked_to=checked_to)
                    else:
                        applied, stored, archived = mood_sync.delete_mood(conn, folder, uid, base_revision,
                                                                          deleted_at)
                    self._clean_up_covers(conn, folder, username)
            except covers.CoverMissingError as exc:
                self._reply_json(HTTPStatus.BAD_REQUEST, {"error": "cover_missing", "cover": exc.cover})
                return
            except mood_sync.BadMoodError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"Moods of {username}: {method} {uid} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            finally:
                conn.close()
            if applied and (method == "PUT" or archived):
                what = "deleted (archived in " + str(archived) + ")" if archived else \
                    f"stored ({len(stored.get('song_ids', []))} songs)"
                print(f"Mood {stored['uid']} of {username} {what}, revision {stored['revision']}", flush=True)
            self._reply_json(HTTPStatus.OK if applied else HTTPStatus.CONFLICT, stored)

        # ---- folders (the phone's Biblioteka) ----

        def _folders(self, method: str):
            url = urlsplit(self.path)
            match = FOLDERS_PATH_RE.match(url.path)
            username, uid = match.group("user"), match.group("uid")
            if (method == "GET") != (uid is None):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            body = None
            if method == "PUT":
                body = self._read_json_body(FOLDER_MAX_BODY_BYTES)
                if body is None:
                    return
            if not self._authorize(username, "another user's folders"):
                return
            if uid is not None and not folder_sync.UID_RE.match(uid):
                self._reply(HTTPStatus.BAD_REQUEST, "not a folder uid")
                return
            query = parse_qs(url.query)
            try:
                if method == "GET":
                    since = self._query_int(query, "since", default=0)
                elif method == "PUT":
                    folder = folder_sync.parse_folder(body)
                    base_revision = playlist_sync.parse_base_revision(body.get("base_revision"))
                    if body.get("uid", uid) != uid:
                        raise folder_sync.BadFolderError("uid: differs from the one in the path")
                else:
                    base_revision = self._query_int(query, "base_revision")
                    deleted_at = self._query_int(query, "deleted_at")
            except folder_sync.BadFolderError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return

            try:
                conn = folder_sync.connect(os.path.join(users_dir, username, playlist_sync.USER_DB_FILENAME))
            except folder_sync.NotMigratedError as exc:
                print(f"Folders of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE, "this user's database isn't ready yet")
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"Folders of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                if method == "GET":
                    self._reply_json(HTTPStatus.OK, folder_sync.changes_since(conn, since))
                    return
                if method == "PUT":
                    applied, stored = folder_sync.store_folder(conn, uid, folder, base_revision)
                else:
                    applied, stored = folder_sync.delete_folder(conn, uid, base_revision, deleted_at)
            except (OSError, sqlite3.Error) as exc:
                print(f"Folders of {username}: {method} {uid} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            finally:
                conn.close()
            if applied:
                what = "deleted" if stored["deleted"] else f"stored ({len(stored['items'])} items)"
                print(f"Folder {stored['uid']} of {username} {what}, revision {stored['revision']}", flush=True)
            self._reply_json(HTTPStatus.OK if applied else HTTPStatus.CONFLICT, stored)

        # ---- saved songs and links (liked / "Na później") ----

        def _saved(self, method: str):
            url = urlsplit(self.path)
            match = SAVED_PATH_RE.match(url.path)
            username, uid = match.group("user"), match.group("uid")
            collection = saved_sync.COLLECTIONS[match.group("collection")]
            if (method == "GET") != (uid is None):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            body = None
            if method == "PUT":
                body = self._read_json_body(SAVED_MAX_BODY_BYTES)
                if body is None:
                    return
            if not self._authorize(username, f"another user's {collection.path}"):
                return
            if uid is not None and not saved_sync.UID_RE.match(uid):
                self._reply(HTTPStatus.BAD_REQUEST, "not an item uid")
                return
            query = parse_qs(url.query)
            try:
                if method == "GET":
                    since = self._query_int(query, "since", default=0)
                elif method == "PUT":
                    item = saved_sync.parse_item(collection, body)
                    base_revision = playlist_sync.parse_base_revision(body.get("base_revision"))
                    if body.get("uid", uid) != uid:
                        raise saved_sync.BadItemError("uid: differs from the one in the path")
                else:
                    base_revision = self._query_int(query, "base_revision")
                    deleted_at = self._query_int(query, "deleted_at")
            except saved_sync.BadItemError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return

            log_name = f"{collection.path} of {username}"
            try:
                conn = saved_sync.connect(os.path.join(users_dir, username, playlist_sync.USER_DB_FILENAME))
            except saved_sync.NotMigratedError as exc:
                print(f"{log_name}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE, "this user's database isn't ready yet")
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"{log_name}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                if method == "GET":
                    self._reply_json(HTTPStatus.OK, saved_sync.changes_since(conn, collection, since))
                    return
                if method == "PUT":
                    applied, stored = saved_sync.store_item(conn, collection, uid, item, base_revision)
                else:
                    applied, stored = saved_sync.delete_item(conn, collection, uid, base_revision, deleted_at)
            except (OSError, sqlite3.Error) as exc:
                print(f"{log_name}: {method} {uid} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            finally:
                conn.close()
            if applied:
                print(f"{collection.label} {stored['uid']} of {username} "
                      f"{'deleted' if stored['deleted'] else 'stored'}, revision {stored['revision']}", flush=True)
            self._reply_json(HTTPStatus.OK if applied else HTTPStatus.CONFLICT, stored)

        # ---- listens (plays, for the phone's listening stats) ----

        def _listens(self, method: str):
            url = urlsplit(self.path)
            username = LISTENS_PATH_RE.match(url.path).group("user")
            body = None
            if method == "POST":
                body = self._read_json_body(LISTEN_MAX_BODY_BYTES)
                if body is None:
                    return
            if not self._authorize(username, "another user's listens"):
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

            try:
                conn = listen_sync.connect(os.path.join(users_dir, username, playlist_sync.USER_DB_FILENAME))
            except listen_sync.NotMigratedError as exc:
                print(f"Listens of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE, "this user's database isn't ready yet")
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"Listens of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
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
                covers.require_blob(folder, cover)

        @staticmethod
        def _clean_up_covers(conn, folder: str, username: str) -> None:
            """Inside covers_lock. A failure here never fails the request."""
            try:
                removed = covers.clean_up(conn, folder)
            except (OSError, sqlite3.Error) as exc:
                print(f"Cleaning up the covers of {username} failed: {exc}", flush=True)
                return
            if removed:
                print(f"Removed {len(removed)} unused cover(s) of {username}", flush=True)

        def _cover_blob(self, method: str):
            match = COVERS_PATH_RE.match(urlsplit(self.path).path)
            username, sha = match.group("user"), match.group("sha")
            data = None
            if method == "PUT":
                length = self.headers.get("Content-Length")
                if length is None or not length.isdigit():
                    self._reply(HTTPStatus.LENGTH_REQUIRED)
                    return
                if int(length) > covers.MAX_COVER_BYTES:
                    self.close_connection = True
                    self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"at most {covers.MAX_COVER_BYTES} bytes")
                    return
                self.connection.settimeout(UPLOAD_SOCKET_TIMEOUT)
                try:
                    data = self.rfile.read(int(length))
                except OSError:
                    self.close_connection = True
                    return
                if len(data) != int(length):
                    self.close_connection = True
                    return
            if not self._authorize(username, "another user's covers"):
                return
            if not covers.is_sha256(sha):
                self._reply(HTTPStatus.BAD_REQUEST, "expected the lowercase hex sha256 of the picture")
                return
            folder = os.path.join(users_dir, username)
            if method == "GET":
                path = covers.blob_path(folder, sha)
                try:
                    body = path.read_bytes() if path is not None else None
                except OSError:
                    body = None  # removed by a clean-up just now
                if body is None:
                    self._reply(HTTPStatus.NOT_FOUND)
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", covers.content_type_of(path))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", COVER_CACHE_CONTROL)
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                with covers_lock:
                    created = covers.store_blob(folder, sha, data, self.headers.get("Content-Type"))
            except covers.HashMismatchError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except covers.UnsupportedImageError as exc:
                self._reply(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc))
                return
            except OSError as exc:
                print(f"Storing cover {sha} of {username} failed: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if created:
                print(f"Cover {sha} of {username} stored ({len(data)} bytes)", flush=True)
            self._reply(HTTPStatus.CREATED if created else HTTPStatus.OK, "stored" if created else "already stored")

        def _global_mood_covers(self, method: str):
            """The user's private covers of global moods (mood_sync.py)."""
            url = urlsplit(self.path)
            match = GLOBAL_MOOD_COVERS_PATH_RE.match(url.path)
            username, mood_id = match.group("user"), match.group("mood")
            if method not in ("GET", "PUT") or (method == "GET") != (mood_id is None):
                self._reply(HTTPStatus.NOT_FOUND)
                return
            body = None
            if method == "PUT":
                body = self._read_json_body(GLOBAL_MOOD_COVER_MAX_BODY_BYTES)
                if body is None:
                    return
            if not self._authorize(username, "another user's covers"):
                return
            try:
                if method == "GET":
                    since = self._query_int(parse_qs(url.query), "since", default=0)
                else:
                    if not mood_id.isdigit() or int(mood_id) < 1:
                        raise mood_sync.BadMoodError("expected /global-mood-covers/<mood id>")
                    mood_id = int(mood_id)
                    change = mood_sync.parse_global_cover(body)
                    if body.get("mood_id", mood_id) != mood_id:
                        raise mood_sync.BadMoodError("mood_id: differs from the one in the path")
            except mood_sync.BadMoodError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, str(exc))
                return
            folder = os.path.join(users_dir, username)
            try:
                conn = mood_sync.connect(os.path.join(folder, playlist_sync.USER_DB_FILENAME))
            except mood_sync.NotMigratedError as exc:
                print(f"Covers of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE, "this user's database isn't ready yet")
                return
            except (OSError, sqlite3.Error) as exc:
                print(f"Covers of {username}: {exc}", flush=True)
                self._reply(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                if method == "GET":
                    self._reply_json(HTTPStatus.OK, mood_sync.global_covers_since(conn, since))
                    return
                with covers_lock:
                    covers.require_blob(folder, change["cover"])
                    applied, stored = mood_sync.store_global_cover(conn, mood_id, change["cover"],
                                                                   change["base_revision"], change["updated_at"])
                    self._clean_up_covers(conn, folder, username)
            except covers.CoverMissingError as exc:
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

        def _global_mood_song(self, method: str):
            """PUT adds a song to a global mood, DELETE removes it. Recorded
            in --mood-changes for MusicDatabase to apply (global_mood_log.py)."""
            match = GLOBAL_MOOD_SONG_PATH_RE.match(urlsplit(self.path).path)
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
            if not (mood_id.isdigit() and requested.isdigit() and int(requested) >= 1):
                self._reply(HTTPStatus.BAD_REQUEST, "expected /global-moods/<mood id>/songs/<song id>")
                return
            mood_id, requested = int(mood_id), int(requested)
            # Both checked against the copies the phone downloads: a mood or
            # song it knows is in them
            mood_name = global_mood_log.global_mood_name(os.path.join(share_dir, "tag.db"), mood_id)
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
            if not values[0].isdigit():
                raise playlist_sync.BadPlaylistError(f"{name}: expected an integer >= 0")
            return int(values[0])

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
            # After the mount check: tokens live on the drive too, and a
            # missing drive mustn't look like "log in again"
            if not self._check_sender(match.group("user")):
                return
            self._store(name, data)

        def _store(self, name: str, data: bytes):
            new_dir = os.path.join(inbox_dir, INBOX_NEW)
            target = os.path.join(new_dir, name)
            with inbox_lock:
                try:
                    os.makedirs(new_dir, exist_ok=True)
                    # Sent again (e.g. the answer to the first try got lost)
                    for folder in (INBOX_NEW, INBOX_REVIEW, INBOX_PROCESSED):
                        existing = os.path.join(inbox_dir, folder, name)
                        if os.path.exists(existing):
                            if _same_content(existing, data):
                                self._reply(HTTPStatus.OK, "already received")
                            else:
                                self._reply(HTTPStatus.CONFLICT, "another request has this name")
                            return
                    waiting = _dir_size(new_dir) + _dir_size(os.path.join(inbox_dir, INBOX_REVIEW))
                    if waiting + len(data) > inbox_limit_bytes:
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

        def _reply(self, status: HTTPStatus, detail: str | None = None, headers: dict | None = None):
            if status == HTTPStatus.NO_CONTENT:
                self._send(status, b"", None, headers)
                return
            body = f"{status.value} {status.phrase}{f' - {detail}' if detail else ''}\n".encode()
            self._send(status, body, "text/plain; charset=utf-8", headers)

        def _reply_json(self, status: HTTPStatus, payload):
            self._send(status, json.dumps(payload).encode(), "application/json", None)

        def _send(self, status: HTTPStatus, body: bytes, content_type: str | None, headers: dict | None):
            self.send_response(status)
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
    # `ip -o` prints one line per address: "2: enp2s0    inet 192.0.2.10/24 brd ..."
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
    parser.add_argument("--inbox", help="accept requests from Leina Radio into this folder (on --mount)")
    parser.add_argument("--max-request-bytes", type=int, default=DEFAULT_MAX_REQUEST_BYTES)
    parser.add_argument("--inbox-limit-bytes", type=int, default=DEFAULT_INBOX_LIMIT_BYTES)
    parser.add_argument("--users", help="log in as the users in this folder (on --mount) and sync their playlists")
    parser.add_argument("--max-playlist-bytes", type=int, default=DEFAULT_MAX_PLAYLIST_BYTES)
    parser.add_argument("--mood-changes", help="with --users: record permitted users' changes to global moods "
                                               "in this folder (on --mount)")
    parser.add_argument("--library", help="serve the music library files indexed in --dir/files.db from this folder")
    args = parser.parse_args(argv)

    handler = make_handler(args.dir, args.mount, args.inbox, args.max_request_bytes, args.inbox_limit_bytes,
                           users_dir=args.users, max_playlist_bytes=args.max_playlist_bytes,
                           mood_changes_dir=args.mood_changes, library_dir=args.library)

    def bind_and_serve(label, get_address):
        server = bind_when_ready(handler, args.port, get_address, label)
        host, port = server.server_address[:2]
        inbox = f", requests into {args.inbox}" if args.inbox else ""
        inbox += f", logins, playlists, moods, folders, saved songs and links, listens and covers for {args.users}" if args.users else ""
        inbox += f", global mood changes into {args.mood_changes}" if args.users and args.mood_changes else ""
        inbox += f", library files from {args.library}" if args.library else ""
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
