"""Settings -> "Name cleanup": which of sanitize's steps are on - each one
for every source at once - in one JSON file (SANITIZE_CONFIG_FILE). Only
what differs from a step's default is stored, so a step whose default
changes later follows it unless the owner set it. Read once per process:
the app reads it at the first cleanup, the request timer at each run."""

from upath import UPath

from config.constants import SANITIZE_CONFIG_FILE
from utils.common.json_file import read_json, write_json

CONFIG_PATH = SANITIZE_CONFIG_FILE

_cache: dict | None = None


def load_overrides() -> dict[str, bool]:
    """{step name: on} the owner set, {} when nothing (or the file can't be
    read - then the defaults apply)."""
    global _cache
    if _cache is None:
        _cache = _read()
    return _cache


def _read() -> dict:
    steps = read_json(UPath(CONFIG_PATH), {}, "Name cleanup settings").get("steps")
    if not isinstance(steps, dict):
        return {}
    found = {}
    for name, value in steps.items():
        if isinstance(value, bool):
            found[name] = value
        elif isinstance(value, dict) and any(isinstance(v, bool) for v in value.values()):
            # Saved when a step was switched per source: on where it was on anywhere
            found[name] = any(v is True for v in value.values())
    return found


def save_overrides(overrides: dict[str, bool]) -> None:
    """Store `overrides` and use them from now on."""
    global _cache
    steps = {name: on for name, on in overrides.items() if isinstance(on, bool)}
    write_json(UPath(CONFIG_PATH), {"steps": steps})
    _cache = steps


def forget_cache() -> None:
    """Read the file again at the next lookup."""
    global _cache
    _cache = None
