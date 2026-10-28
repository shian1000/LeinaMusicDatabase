"""One change applied to many mp3 files, where one bad file mustn't stop the
rest: Users -> playlist from a folder (fix names and tags, update tags) and
Manage database -> "Fix names and tags for mp3 files"."""

from pathlib import Path
from typing import Any, Callable, Iterable

from utils.common.debug import flog
from utils.common.output import error


def apply_to_files(items: Iterable, apply: Callable[[Any], Any],
                   file_of: Callable[[Any], Path]) -> tuple[list, list[tuple[Path, Exception]]]:
    """apply(item) for each item. Returns (what each successful call
    returned, [(file, exception)] for the failed ones); every failure is also
    in debug.log, with the file and the exception's type."""
    results, failed = [], []
    for item in items:
        try:
            results.append(apply(item))
        except Exception as exc:
            file = file_of(item)
            flog(f"Couldn't change {file}: {type(exc).__name__}: {exc}")
            failed.append((file, exc))
    return results, failed


def report_failures(failed: list[tuple[Path, Exception]], what: str = "fix") -> None:
    """One red line per file apply_to_files() couldn't change."""
    for file, exc in failed:
        error(f"  Couldn't {what} {file}: {exc}")
