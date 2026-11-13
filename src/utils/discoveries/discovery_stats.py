"""Persisted invocation/success counters for discovery modules, keyed by
module id (filename stem, stable across MODULE_NAME renames -- see
discovery_settings.py). Powers the Statistics menu.

A module is "invoked" every time discoveries_manager.py calls its
get_album_name() -- including retries with untruncated text or an artist
synonym, since each of those is a real call. It "succeeds" when that result
survives _validate_result()'s sanity checks, i.e. it actually contributed a
usable album rather than being a crash, an empty/blacklisted value, or a
confident match for the wrong song.

Year (get_release_year()) and lyrics (get_lyrics()) lookups are counted the
same way, each kind in a file of its own (discovery_settings.KINDS), so a
module's year success rate doesn't get mixed into its album success rate in
the Statistics menu.
"""
from upath import UPath
from utils.common.json_file import read_json, write_json
from utils.discoveries.discovery_settings import ALBUMS, KINDS

# Per kind, loaded once per process and kept in memory so a busy import (many
# invocations per song) doesn't re-read the file on every single bump; every
# mutation is still written straight back to disk.
_caches: dict = {}


def _stats_path(kind: str = ALBUMS) -> UPath:
    return UPath(str(KINDS[kind].stats_file))


def load_discovery_stats(kind: str = ALBUMS) -> dict:
    """Read the persisted per-module counters, falling back to an empty dict
    if the file is missing or unreadable."""
    return read_json(_stats_path(kind), {}, f"Discovery fetcher stats ({kind})")


def save_discovery_stats(stats: dict, kind: str = ALBUMS) -> None:
    write_json(_stats_path(kind), stats)


def _bump(module_id: str, field: str, kind: str) -> None:
    if kind not in _caches:
        _caches[kind] = load_discovery_stats(kind)
    entry = _caches[kind].setdefault(module_id, {"invocations": 0, "successes": 0})
    entry[field] += 1
    save_discovery_stats(_caches[kind], kind)


def record_invocation(module_id: str, kind: str = ALBUMS) -> None:
    """Call each time a module's lookup function of that kind is about to run."""
    _bump(module_id, "invocations", kind)


def record_success(module_id: str, kind: str = ALBUMS) -> None:
    """Call each time a module's result survives validation."""
    _bump(module_id, "successes", kind)


def reset_discovery_stats() -> None:
    """Zero the counters of every module and kind, so the Statistics menu
    starts counting from scratch. The in-memory caches are replaced too --
    otherwise the next bump in this process would write the old counts
    straight back to disk."""
    for kind in KINDS:
        _caches[kind] = {}
        save_discovery_stats(_caches[kind], kind)
