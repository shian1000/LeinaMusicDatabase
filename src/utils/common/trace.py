"""Where a long operation (an import) spends its time, for debug.log.

Counts the slow calls - yt-dlp runs (manage_youtube_playlists.run_ytdlp()),
YouTube Data API requests (api_call()) and SQL statements of every engine -
next to MusicBrainz's own MBStats. phase() logs a step's time with what it
called, so a log shows which step asked what how often. The counting always
runs (it's cheap); what's logged follows Settings -> "Debug log":

- TRACE (verbosity 1): phases, every external call, one line per entry
- DETAIL (verbosity 2): why - cache hits, matches, slow SQL statements

A phase that asks the user includes the time spent waiting for answers.
"""

import time
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine

from config.constants import DEBUG_SLOW_SQL_SECONDS
from utils.common.debug import debug_on, mlog
from utils.common.musicbrainz_client import MBStats

TRACE = 2
DETAIL = 1


class Calls:
    """Process-wide counters, never reset: phase() and snapshot() take differences."""

    ytdlp_runs = 0
    ytdlp_seconds = 0.0
    api_requests = 0
    api_seconds = 0.0
    sql_statements = 0
    sql_seconds = 0.0


def snapshot() -> dict:
    return {
        "sql": Calls.sql_statements, "sql_s": Calls.sql_seconds,
        "ytdlp": Calls.ytdlp_runs, "ytdlp_s": Calls.ytdlp_seconds,
        "api": Calls.api_requests, "api_s": Calls.api_seconds,
        # MBStats is reset by every import batch: a negative difference
        # below then means "since the reset"
        "mb": MBStats.requests_made, "mb_s": MBStats.total_request_seconds, "mb_wait": MBStats.total_wait_seconds,
        "hits": MBStats.cache_hits, "misses": MBStats.cache_misses,
    }


def describe(before: dict) -> str:
    """What was called since `before` (a snapshot()), the parts that happened."""
    now = snapshot()

    def diff(key):
        value = now[key] - before[key]
        return now[key] if value < 0 else value

    parts = [f"SQL {diff('sql')} ({diff('sql_s'):.2f}s)"]
    if diff("ytdlp"):
        parts.append(f"yt-dlp {diff('ytdlp')} ({diff('ytdlp_s'):.2f}s)")
    if diff("api"):
        parts.append(f"YouTube API {diff('api')} ({diff('api_s'):.2f}s)")
    if diff("mb"):
        parts.append(f"MusicBrainz {diff('mb')} ({diff('mb_s'):.2f}s + {diff('mb_wait'):.2f}s waiting)")
    if diff("hits") or diff("misses"):
        parts.append(f"spell-check cache {diff('hits')} hit / {diff('misses')} miss")
    return ", ".join(parts)


class Phase:
    """A step being timed: logged when started, and by end() with the time
    it took and what it called."""

    def __init__(self, name: str):
        self.name, self.before, self.started = name, snapshot(), time.monotonic()
        mlog(f"[Phase] {name} - start", TRACE)

    def end(self) -> None:
        mlog(f"[Phase] {self.name} - {time.monotonic() - self.started:.2f}s: {describe(self.before)}", TRACE)


def start_phase(name: str) -> Phase:
    """For a step spanning a long stretch of code; phase() for a block."""
    return Phase(name)


@contextmanager
def phase(name: str):
    current = Phase(name)
    try:
        yield
    finally:
        current.end()


def api_call(request, what: str):
    """`request.execute()` for a YouTube Data API request, counted and
    logged with its time (`what`: the endpoint and its arguments)."""
    started = time.monotonic()
    outcome = "failed"
    try:
        response = request.execute()
        outcome = f"{len(response.get('items', []))} item(s)" if isinstance(response, dict) else "ok"
        return response
    finally:
        elapsed = time.monotonic() - started
        Calls.api_requests += 1
        Calls.api_seconds += elapsed
        mlog(f"[YouTube API] {elapsed:.2f}s {outcome}: {what}", TRACE)


def short(text, limit: int = 300) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + " ..."


def _before_execute(conn, cursor, statement, parameters, context, executemany):
    if context is not None:
        context._trace_started = time.perf_counter()


def _after_execute(conn, cursor, statement, parameters, context, executemany):
    started = getattr(context, "_trace_started", None)
    if started is None:
        return
    elapsed = time.perf_counter() - started
    Calls.sql_statements += 1
    Calls.sql_seconds += elapsed
    if elapsed >= DEBUG_SLOW_SQL_SECONDS and debug_on(DETAIL):
        mlog(f"[SQL] slow, {elapsed:.3f}s: {short(statement)}", DETAIL)


# Every engine (music.db, tag.db, files.db, users' user.db)
if not event.contains(Engine, "before_cursor_execute", _before_execute):
    event.listen(Engine, "before_cursor_execute", _before_execute)
    event.listen(Engine, "after_cursor_execute", _after_execute)
