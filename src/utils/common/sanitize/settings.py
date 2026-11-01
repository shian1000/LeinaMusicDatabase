"""Settings -> "Name cleanup": which of sanitize's steps are on - each one
for every source at once - in one JSON file (SANITIZE_CONFIG_FILE). Only
what differs from a step's default is stored, so a step whose default
changes later follows it unless the owner set it. Read once per process:
the app reads it at the first cleanup, the request timer at each run."""
import json

from upath import UPath

from config.constants import SANITIZE_CONFIG_FILE
from utils.common.debug import flog

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
    path = UPath(CONFIG_PATH)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        flog(f"Name cleanup settings unreadable, using the defaults: {e}")
        return {}
    steps = data.get("steps") if isinstance(data, dict) else None
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
    UPath(CONFIG_PATH).write_text(json.dumps({"steps": steps}, indent=2, ensure_ascii=False))
    _cache = steps


def forget_cache() -> None:
    """Read the file again at the next lookup."""
    global _cache
    _cache = None
