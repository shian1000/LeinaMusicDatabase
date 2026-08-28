"""
Shared MusicBrainz HTTP client.

Every MusicBrainz web-service call in this project should go through ``mb_get()``
so that a single process-wide rate limit is respected instead of each call site
hammering the API independently. MusicBrainz throttles anonymous clients to
roughly one request per second and answers 503 above that; importing MP3 tags is
almost entirely bottlenecked on this endpoint, so the important properties here
are:

* one shared :class:`requests.Session` with a real ``User-Agent``,
* a global minimum interval between outgoing requests,
* a hard ``(connect, read)`` timeout on every request,
* retry with linear backoff on 429/503,
* lightweight counters (:class:`MBStats`) so a run can report where the time went.
"""

import threading
import time
from typing import Optional

import requests

from utils.common.debug import slog
from config.constants import (
    MUSICBRAINZ_API_USER_AGENT,
    MUSICBRAINZ_API_TIMEOUT,
    MUSICBRAINZ_API_MIN_INTERVAL,
)

_session = requests.Session()
_session.headers.update({"User-Agent": MUSICBRAINZ_API_USER_AGENT})

_rate_lock = threading.Lock()
_last_request_ts = 0.0


class MBStats:
    """Process-wide counters for MusicBrainz traffic. Call :meth:`reset` at the
    start of a batch job and :meth:`format_summary` at the end."""

    requests_made = 0
    cache_hits = 0
    cache_misses = 0
    http_429 = 0
    http_503 = 0
    other_errors = 0
    total_wait_seconds = 0.0
    total_request_seconds = 0.0

    @classmethod
    def reset(cls) -> None:
        cls.requests_made = 0
        cls.cache_hits = 0
        cls.cache_misses = 0
        cls.http_429 = 0
        cls.http_503 = 0
        cls.other_errors = 0
        cls.total_wait_seconds = 0.0
        cls.total_request_seconds = 0.0

    @classmethod
    def format_summary(cls) -> str:
        return "\n".join([
            "MusicBrainz request stats:",
            f"   HTTP requests sent:       {cls.requests_made}",
            f"   Spell-check cache hits:   {cls.cache_hits}",
            f"   Spell-check cache misses: {cls.cache_misses}",
            f"   429 (rate limited):       {cls.http_429}",
            f"   503 (unavailable):        {cls.http_503}",
            f"   Other request errors:     {cls.other_errors}",
            f"   Time spent throttling:    {cls.total_wait_seconds:.1f}s",
            f"   Time spent in requests:   {cls.total_request_seconds:.1f}s",
        ])


def _throttle() -> None:
    """Block until at least ``MUSICBRAINZ_API_MIN_INTERVAL`` seconds have passed
    since the previous request started (process-wide)."""
    global _last_request_ts
    with _rate_lock:
        wait = MUSICBRAINZ_API_MIN_INTERVAL - (time.monotonic() - _last_request_ts)
        if wait > 0:
            MBStats.total_wait_seconds += wait
            time.sleep(wait)
        _last_request_ts = time.monotonic()


def mb_get(url: str, params: dict, retries: int = 2, backoff: float = 5.0) -> Optional[requests.Response]:
    """GET a MusicBrainz endpoint honoring the global rate limit and timeout.

    Retries on 429/503 with linear backoff (``backoff * attempt`` seconds), and
    retries plain connection/timeout errors once with a short pause (those
    usually mean the network or the server hiccuped, not that we need to cool
    off). Returns the successful :class:`requests.Response`, or ``None`` if every
    attempt failed.
    """
    net_backoff = 2.0
    for attempt in range(1, retries + 1):
        _throttle()
        started = time.monotonic()
        try:
            resp = _session.get(url, params=params, timeout=MUSICBRAINZ_API_TIMEOUT)
            MBStats.requests_made += 1
            MBStats.total_request_seconds += time.monotonic() - started
            resp.raise_for_status()
            return resp

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 429:
                MBStats.http_429 += 1
            elif status == 503:
                MBStats.http_503 += 1
            else:
                MBStats.other_errors += 1

            if status in (429, 503) and attempt < retries:
                wait = backoff * attempt
                slog(f"[MB] HTTP {status} on attempt {attempt}/{retries}, retrying in {wait}s")
                MBStats.total_wait_seconds += wait
                time.sleep(wait)
                continue

            slog(f"[MB] HTTP error after {attempt} attempt(s): {e}")
            return None

        except requests.exceptions.RequestException as e:
            MBStats.other_errors += 1
            MBStats.total_request_seconds += time.monotonic() - started
            slog(f"[MB] request failed on attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                MBStats.total_wait_seconds += net_backoff
                time.sleep(net_backoff)
                continue
            return None

    return None
