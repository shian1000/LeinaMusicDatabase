"""Persisted user settings for discovery modules: which are enabled, and in
what order they get tried (see discoveries_manager.py for how they're run).

Modules are identified by their filename stem (e.g. "wikipedia_fetcher"),
which is stable across renames of the display name (MODULE_NAME).

One config per kind of lookup (KINDS): the album fetchers (get_album_name()),
"Fill missing data -> Years" (get_release_year()) and "-> Lyrics"
(get_lyrics()). They're kept apart because a module may implement any of the
three, so each kind's available modules (and a sensible order) differ.
"""
from dataclasses import dataclass

from upath import UPath
from utils.common.json_file import read_json, write_json
from config.constants import (
    DISCOVERY_MODULES_CONFIG_FILE,
    DEFAULT_DISCOVERY_MODULE_ORDER,
    DISCOVERY_MODULES_YEAR_CONFIG_FILE,
    DEFAULT_DISCOVERY_MODULE_YEAR_ORDER,
    DISCOVERY_MODULES_LYRICS_CONFIG_FILE,
    DEFAULT_DISCOVERY_MODULE_LYRICS_ORDER,
    DISCOVERY_MODULES_NEEDING_API_KEY,
    DISCOVERY_STATS_FILE,
    DISCOVERY_STATS_YEAR_FILE,
    DISCOVERY_STATS_LYRICS_FILE,
)

ALBUMS, YEARS, LYRICS = "albums", "years", "lyrics"


@dataclass(frozen=True)
class DiscoveryKind:
    function: str  # what a module defines to take part (found statically, never imported for it)
    config_file: object
    default_order: list
    stats_file: object  # discovery_stats.py


KINDS = {
    ALBUMS: DiscoveryKind("get_album_name", DISCOVERY_MODULES_CONFIG_FILE, DEFAULT_DISCOVERY_MODULE_ORDER,
                          DISCOVERY_STATS_FILE),
    YEARS: DiscoveryKind("get_release_year", DISCOVERY_MODULES_YEAR_CONFIG_FILE,
                         DEFAULT_DISCOVERY_MODULE_YEAR_ORDER, DISCOVERY_STATS_YEAR_FILE),
    LYRICS: DiscoveryKind("get_lyrics", DISCOVERY_MODULES_LYRICS_CONFIG_FILE,
                          DEFAULT_DISCOVERY_MODULE_LYRICS_ORDER, DISCOVERY_STATS_LYRICS_FILE),
}


def _on_by_default(module_id: str) -> bool:
    return module_id not in DISCOVERY_MODULES_NEEDING_API_KEY


def _default_config(default_order) -> dict:
    return {
        "order": list(default_order),
        "enabled": {module_id: _on_by_default(module_id) for module_id in default_order},
    }


def _load_config(config_file: str, default_order) -> dict:
    """Read a persisted order/enabled config, falling back to defaults if
    the file is missing or unreadable."""
    data = read_json(UPath(config_file), {}, "Discovery modules config")
    if not data:
        return _default_config(default_order)
    data.setdefault("order", [])
    data.setdefault("enabled", {})
    return data


def _save_config(config: dict, config_file: str) -> None:
    write_json(UPath(config_file), config)


def _reconcile_config(available_module_ids, config_file: str, default_order) -> dict:
    """Sync a persisted config against the modules actually available: ones
    no longer available (deleted/renamed, or no longer implementing the
    kind's function) are dropped, and new ones are
    appended (enabled, unless they need an API key). Persists the result if it changed, so
    this only needs to run once per discovery.
    """
    available = list(available_module_ids)
    available_set = set(available)
    config = _load_config(config_file, default_order)

    order = [module_id for module_id in config["order"] if module_id in available_set]
    known = set(order)
    for module_id in available:
        if module_id not in known:
            order.append(module_id)
            known.add(module_id)

    enabled = {module_id: config["enabled"].get(module_id, _on_by_default(module_id)) for module_id in order}

    reconciled = {"order": order, "enabled": enabled}
    if reconciled != config:
        _save_config(reconciled, config_file)
    return reconciled


def load_discovery_config(kind: str = ALBUMS) -> dict:
    return _load_config(KINDS[kind].config_file, KINDS[kind].default_order)


def save_discovery_config(config: dict, kind: str = ALBUMS) -> None:
    _save_config(config, KINDS[kind].config_file)


def reconcile_discovery_config(available_module_ids, kind: str = ALBUMS) -> dict:
    return _reconcile_config(available_module_ids, KINDS[kind].config_file, KINDS[kind].default_order)
