"""Persisted invocation/success counters for discovery modules, keyed by
module id (filename stem, stable across MODULE_NAME renames -- see
discovery_settings.py). Powers the Statistics menu.

A module is "invoked" every time discoveries_manager.py calls its
get_album_name() -- including retries with untruncated text or an artist
synonym, since each of those is a real call. It "succeeds" when that result
survives _validate_result()'s sanity checks, i.e. it actually contributed a
usable album rather than being a crash, an empty/blacklisted value, or a
confident match for the wrong song.

Year-lookup (get_release_year()) counters are tracked the same way but kept
in a separate file (DISCOVERY_STATS_YEAR_FILE) via the *_year_stats
functions below, so a module's year success rate doesn't get mixed into its
album success rate in the Statistics menu.
"""
from upath import UPath
from utils.common.json_file import read_json, write_json
from config.constants import DISCOVERY_STATS_FILE, DISCOVERY_STATS_YEAR_FILE

_cache: dict | None = None
_year_cache: dict | None = None


def _stats_path() -> UPath:
    return UPath(DISCOVERY_STATS_FILE)


def _year_stats_path() -> UPath:
    return UPath(DISCOVERY_STATS_YEAR_FILE)


def load_discovery_stats() -> dict:
    """Read the persisted per-module counters, falling back to an empty dict
    if the file is missing or unreadable."""
    return read_json(_stats_path(), {}, "Discovery fetcher stats")


def save_discovery_stats(stats: dict) -> None:
    write_json(_stats_path(), stats)


def load_year_discovery_stats() -> dict:
    return read_json(_year_stats_path(), {}, "Year discovery fetcher stats")


def save_year_discovery_stats(stats: dict) -> None:
    write_json(_year_stats_path(), stats)


def _bump(module_id: str, field: str, year: bool = False) -> None:
    # Loaded once per process and kept in memory so a busy import (many
    # invocations per song) doesn't re-read the file on every single bump;
    # every mutation is still written straight back to disk.
    global _cache, _year_cache
    if year:
        if _year_cache is None:
            _year_cache = load_year_discovery_stats()
        stats, save = _year_cache, save_year_discovery_stats
    else:
        if _cache is None:
            _cache = load_discovery_stats()
        stats, save = _cache, save_discovery_stats
    entry = stats.setdefault(module_id, {"invocations": 0, "successes": 0})
    entry[field] += 1
    save(stats)


def record_invocation(module_id: str) -> None:
    """Call each time a module's get_album_name() is about to run."""
    _bump(module_id, "invocations")


def record_success(module_id: str) -> None:
    """Call each time a module's result survives validation."""
    _bump(module_id, "successes")


def record_year_invocation(module_id: str) -> None:
    """Call each time a module's get_release_year() is about to run."""
    _bump(module_id, "invocations", year=True)


def record_year_success(module_id: str) -> None:
    """Call each time a module's release-year result survives validation."""
    _bump(module_id, "successes", year=True)


def reset_discovery_stats() -> None:
    """Zero the album and year counters of every module, so the Statistics
    menu starts counting from scratch. The in-memory caches are replaced too
    -- otherwise the next bump in this process would write the old counts
    straight back to disk."""
    global _cache, _year_cache
    _cache = {}
    _year_cache = {}
    save_discovery_stats(_cache)
    save_year_discovery_stats(_year_cache)
