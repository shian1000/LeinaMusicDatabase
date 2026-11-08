"""What a sharing server must do alike, whichever program runs it: limits, the
Host check and telling plain numbers from the rest. The app's contract is
tests/contract/."""

import ipaddress
import re
import threading
import time
from urllib.parse import urlsplit

# One that sends no request (or stops mid-headers) gives up its connection after this
REQUEST_SOCKET_TIMEOUT = 60
# Connections served at once, over every socket of the process: one more is
# closed unanswered
MAX_CONNECTIONS = 64
LOGIN_MAX_BODY_BYTES = 4096
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 5 * 60
# Unsigned uploads (no user in the request name) per client address, then 429
ANONYMOUS_UPLOADS_PER_WINDOW = 30
ANONYMOUS_UPLOAD_WINDOW_SECONDS = 60 * 60
# Unsigned files may fill at most this share of the inbox's limit, so
# logged-in users always have room
ANONYMOUS_INBOX_SHARE = 0.5
# A playlist may have up to 5000 tracks (the app's limit)
DEFAULT_MAX_PLAYLIST_BYTES = 8 * 1024 * 1024
# A folder holds only refs; the app has no limit, this is generous
FOLDER_MAX_BODY_BYTES = 1024 * 1024
# One song or link with a few names; generous
SAVED_MAX_BODY_BYTES = 64 * 1024
# The phone sends up to 500 plays, ~250 KB of JSON
LISTEN_MAX_BODY_BYTES = 1024 * 1024
GLOBAL_MOOD_COVER_MAX_BODY_BYTES = 4096
# A hash never changes its picture
COVER_CACHE_CONTROL = "private, max-age=31536000, immutable"
# A number in a query or a Content-Length: ASCII digits only (str.isdigit()
# also takes "²"), at most 18 - an SQLite integer
PLAIN_INT_RE = re.compile(r"[0-9]{1,18}")


def plain_int(text: str | None) -> int | None:
    """`text` as a non-negative integer, None when it isn't a plain one."""
    return int(text) if text is not None and PLAIN_INT_RE.fullmatch(text) else None


def host_allowed(host: str | None, names: set[str]) -> bool:
    """
    Whether a Host header is one this server answers: any IP address (DNS
    rebinding needs a domain name, and the sockets only take their own
    addresses anyway), "localhost", or one of `names` (lowercase), with or
    without a port. No header at all (an HTTP/1.0 client) passes too - a
    browser always sends one.
    """
    if host is None:
        return True
    if not host:
        return False
    try:
        hostname = urlsplit(f"//{host}").hostname
    except ValueError:
        return False
    if not hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    return hostname == "localhost" or hostname in names


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
            self._forget_old(now)
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

    def _forget_old(self, now: float) -> None:
        """Drop failures older than the window and lockouts that ran out - a
        made-up name per try must not grow these dicts for as long as the
        server runs."""
        for key in [k for k, times in self._failures.items()
                    if not any(now - t < LOGIN_FAILURE_WINDOW_SECONDS for t in times)]:
            del self._failures[key]
        for key in [k for k, until in self._locked_until.items() if until <= now]:
            del self._locked_until[key]


class UploadLimiter:
    """Unsigned inbox uploads per client address: ANONYMOUS_UPLOADS_PER_WINDOW
    within the window, then none until the oldest leaves it. In memory only."""

    def __init__(self, limit: int = ANONYMOUS_UPLOADS_PER_WINDOW,
                 window: float = ANONYMOUS_UPLOAD_WINDOW_SECONDS, clock=time.monotonic):
        self._limit, self._window, self._clock = limit, window, clock
        self._lock = threading.Lock()
        self._uploads: dict[str, list[float]] = {}

    def take(self, key: str) -> int:
        """0 after counting an upload from `key`; else the seconds until it may send one."""
        now = self._clock()
        with self._lock:
            # Every address's old uploads go, so the dict can't grow for as long as the server runs
            for k in list(self._uploads):
                recent = [t for t in self._uploads[k] if now - t < self._window]
                if recent:
                    self._uploads[k] = recent
                else:
                    del self._uploads[k]
            recent = self._uploads.setdefault(key, [])
            if len(recent) >= self._limit:
                return max(1, int(recent[0] + self._window - now + 0.999))
            recent.append(now)
            return 0
