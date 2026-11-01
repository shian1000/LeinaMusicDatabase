"""Settings -> "Name cleanup": which of sanitize.py's steps each source runs,
in one JSON file (SANITIZE_CONFIG_FILE). Only what differs from a step's
defaults is stored, so a step whose defaults change later follows them
unless the owner set it. Read once per process: the app reads it at the
first cleanup, the request timer at each run."""
import json

from upath import UPath

from config.constants import SANITIZE_CONFIG_FILE
from utils.common.debug import flog

CONFIG_PATH = SANITIZE_CONFIG_FILE

_cache: dict | None = None


def load_overrides() -> dict[str, dict[str, bool]]:
    """{step name: {source: on}} the owner set, {} when nothing (or the file
    can't be read - then the defaults apply)."""
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
    return {name: {source: on for source, on in sources.items() if isinstance(on, bool)}
            for name, sources in steps.items() if isinstance(sources, dict)}


def save_overrides(overrides: dict[str, dict[str, bool]]) -> None:
    """Store `overrides` (empty ones dropped) and use them from now on."""
    global _cache
    steps = {name: sources for name, sources in overrides.items() if sources}
    UPath(CONFIG_PATH).write_text(json.dumps({"steps": steps}, indent=2, ensure_ascii=False))
    _cache = steps


def forget_cache() -> None:
    """Read the file again at the next lookup."""
    global _cache
    _cache = None
