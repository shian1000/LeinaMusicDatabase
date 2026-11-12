"""Debug utilities for logging and exception handling.

This module provides debugging functions that can log to console and/or file.
Debug output is controlled by a `.debug` file in the project root, which
Settings -> "Debug log" writes (read_debug_settings() / save_debug_settings()).
"""

from typing import Any, Optional
import inspect
from pathlib import Path
import linecache
import ast
import re
import sys
import traceback
from datetime import datetime
from types import TracebackType

from config.constants import PROJECT_ROOT


# "verbosity = N" (0-3) and "console = on|off"; written by Settings -> Debug log
DEBUG_FILE: Path = PROJECT_ROOT / ".debug"
MAX_VERBOSITY: int = 3


def read_debug_settings(path: Optional[Path] = None) -> tuple[int, bool]:
    """(verbosity, console) from the .debug file: (0, False) without one. A
    file without a "console" line prints to the console - what a bare .debug
    always did."""
    path = path or DEBUG_FILE
    try:
        text = path.read_text()
    except OSError:
        return 0, False
    verbosity = re.search(r"^\s*verbosity\s*=\s*(\d+)", text, re.MULTILINE)
    console = re.search(r"^\s*console\s*=\s*(\w+)", text, re.MULTILINE)
    level = min(int(verbosity.group(1)), MAX_VERBOSITY) if verbosity else 0
    return level, not (console and console.group(1).lower() in ("off", "no", "false", "0"))


# DEBUG_ENABLED: diagnostics are printed on the console too. slog()/mlog()
# pass a message on when priority >= 3 - VERBOSITY_LEVEL: with verbosity 0
# (no .debug, or one without a "verbosity = N" line) only priority 3 gets
# through, with verbosity 3 everything does
VERBOSITY_LEVEL, DEBUG_ENABLED = read_debug_settings()


def save_debug_settings(verbosity: int, console: bool, path: Optional[Path] = None) -> None:
    """Write the .debug file (removed for verbosity 0 without the console)
    and use the settings in this process right away; another process (the
    inbox timer) reads them when it starts."""
    global VERBOSITY_LEVEL, DEBUG_ENABLED
    if not 0 <= verbosity <= MAX_VERBOSITY:
        raise ValueError(f"verbosity must be 0-{MAX_VERBOSITY}")
    path = path or DEBUG_FILE
    if verbosity == 0 and not console:
        path.unlink(missing_ok=True)
    else:
        path.write_text(f"verbosity = {verbosity}\nconsole = {'on' if console else 'off'}\n")
    VERBOSITY_LEVEL, DEBUG_ENABLED = verbosity, console


def debug_on(priority: int) -> bool:
    """Would slog()/mlog() at `priority` log anything? For a message that
    costs something to build."""
    return priority >= 3 - VERBOSITY_LEVEL


def _stamp() -> str:
    return f"{datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3]

DEBUG_TO_FILE_ENABLED: bool = True
DEBUG_LOG_FILE: Path = PROJECT_ROOT / "debug.log"

# debug.log (and youtube_link_cache.log, through logging's
# RotatingFileHandler) is rotated at this size: debug.log -> debug.log.1 ...
# up to LOG_BACKUP_COUNT old files, the oldest dropped
LOG_MAX_BYTES: int = 2 * 1024 * 1024
LOG_BACKUP_COUNT: int = 3

_write_failure_reported: bool = False


def _handle_exception(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Optional[TracebackType]) -> None:
    """
    Global exception handler for uncaught exceptions.
    
    Logs exception traceback to console and file. KeyboardInterrupt
    is not logged (allows normal Ctrl+C handling).
    
    Args:
        exc_type: Exception type
        exc_value: Exception value/message
        exc_tb: Exception traceback object
    """
    # Let KeyboardInterrupt pass through normally (Ctrl+C shouldn't be logged as a crash)
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    tb_text = "".join(tb_lines)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = redact_secrets(f"***Debug*** [CRASH @ {timestamp}]\n{tb_text}")

    print(message, file=sys.stderr)
    _log(message, echo=False)


sys.excepthook = _handle_exception


# A secret in a URL's query: googleapiclient's HttpError text holds the
# request URL with "key=<YOUTUBE_API_KEY>", and flog(f"... {exc}") or a crash
# would write it to debug.log (AGENTS.md -> Secrets)
_SECRET_QUERY = re.compile(r"\b((?:key|access_token|refresh_token|client_secret|token)=)[^&\s\"'<>]+", re.I)


def redact_secrets(text: str) -> str:
    return _SECRET_QUERY.sub(r"\1REDACTED", text)


def _rotate_log(path: Path, incoming: int) -> None:
    """Shift path -> path.1 -> ... -> path.LOG_BACKUP_COUNT when `incoming`
    more bytes would take path over LOG_MAX_BYTES, dropping the oldest (like
    logging's RotatingFileHandler). Done here rather than with a logging
    handler so that a test or the sandbox can point DEBUG_LOG_FILE elsewhere
    at any time."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    if size == 0 or size + incoming <= LOG_MAX_BYTES:
        return
    for n in range(LOG_BACKUP_COUNT - 1, 0, -1):
        older = path.with_name(f"{path.name}.{n}")
        if older.exists():
            older.replace(path.with_name(f"{path.name}.{n + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def _log(message: str, echo: bool = True) -> None:
    """
    Print `message` when .debug says so (DEBUG_ENABLED, unless not `echo`) and
    append it to the debug log file, rotating it first when it's full.

    A failed write (full disk, read-only folder) doesn't stop the app; it's
    reported once on stderr, so a missing log doesn't go unnoticed.
    """
    global _write_failure_reported
    message = redact_secrets(message)
    if echo and DEBUG_ENABLED:
        print(message)
    if not DEBUG_TO_FILE_ENABLED:
        return
    try:
        line = message + "\n"
        _rotate_log(DEBUG_LOG_FILE, len(line.encode("utf-8")))
        with DEBUG_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        if not _write_failure_reported:
            _write_failure_reported = True
            print(f"Can't write {DEBUG_LOG_FILE}: {exc}", file=sys.stderr)


def slog(var: Any = None, name: Optional[str] = None, priority: int = 0) -> None:
    """
    Log a variable's value with type and source location information.
    
    Smart logging function that automatically extracts the variable name
    from the calling code and includes file/line information. A message
    that passes the verbosity check goes to debug.log, and to the console
    too when .debug says so (DEBUG_ENABLED).
    
    Args:
        var: Variable to log (can be any type)
        name: Optional custom name for the variable (auto-detected if None)
        priority: 0-3, higher = shown sooner. Logged only when
            priority >= 3 - VERBOSITY_LEVEL: 3 always, 0 (the default)
            only with "verbosity = 3" in .debug - so by default nothing,
            not even to debug.log. Use flog() for a lookup that came up empty.
    
    Example:
        >>> x = 42
        >>> slog(x)  # Logs: [filename:lineno] x (int): 42
    """
    if not DEBUG_ENABLED and not DEBUG_TO_FILE_ENABLED:
        return

    if not debug_on(priority):
        return

    file_info: str = "unknown:0"

    frame = inspect.currentframe()
    try:
        if frame is not None:
            caller = frame.f_back
            if caller is not None:
                filename: str = Path(caller.f_code.co_filename).name
                lineno: int = caller.f_lineno
                file_info = f"{filename}:{lineno}"

                # Try to extract variable name from source code
                if name is None:
                    try:
                        full_path: str = caller.f_code.co_filename
                        call_line: str = linecache.getline(full_path, lineno).strip()
                        tree = ast.parse(call_line, mode='eval')
                        call_node = tree.body
                        if isinstance(call_node, ast.Call) and call_node.args:
                            name = ast.unparse(call_node.args[0])
                    except (SyntaxError, ValueError):
                        # a call spread over several lines - no name, "<unknown>"
                        pass
    finally:
        # explicit deletion to remove cyclic references
        del frame

    if name is None:
        name = "<unknown>"

    _log(f"***Debug*** [{_stamp()}] [{file_info}] {name} ({type(var).__name__}): {repr(var)}")


def mlog(message: str, priority: int = 0) -> None:
    """Log a message with a timestamp when `priority` passes the verbosity
    check (the same scale as slog(): 3 always, 0 only at verbosity 3). The
    import's trace (utils.common.trace: phases, every yt-dlp / YouTube API /
    MusicBrainz call) goes through here."""
    if debug_on(priority):
        _log(f"***Debug*** [{_stamp()}] {message.strip(chr(10))}")


def flog(message: str) -> None:
    """Log something that has to be diagnosable after the fact: why a lookup
    (fetcher, YouTube search, scrape) came up empty, or why an operation
    failed where the user may not see it (a backup, sharing, a migration, a
    caught exception the app carries on after).

    Unlike slog()/mlog() this ignores verbosity: it's always appended to
    debug.log with a timestamp (and printed only when .debug says so),
    so an intermittent failure - a page that didn't load in time, a yt-dlp
    error - can still be diagnosed later. slog() is for looking at values
    while debugging, mlog() for progress notes; neither is for the user."""
    _log(f"***Lookup*** [{datetime.now():%Y-%m-%d %H:%M:%S}] {message}")
