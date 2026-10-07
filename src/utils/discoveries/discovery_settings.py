"""Persisted user settings for discovery modules: which are enabled, and in
what order they get tried (see discoveries_manager.py for how they're run).

Modules are identified by their filename stem (e.g. "wikipedia_fetcher"),
which is stable across renames of the display name (MODULE_NAME).

Two independent configs share the logic below: the album fetchers
(DISCOVERY_MODULES_CONFIG_FILE) and the subset of modules that also
implement get_release_year() for "Fill missing data -> Years"
(DISCOVERY_MODULES_YEAR_CONFIG_FILE) - kept separate because not every album
fetcher supports year lookups, so their available-module sets differ.
"""
import json
from upath import UPath
from utils.common.debug import slog
from config.constants import (
    DISCOVERY_MODULES_CONFIG_FILE,
    DEFAULT_DISCOVERY_MODULE_ORDER,
    DISCOVERY_MODULES_YEAR_CONFIG_FILE,
    DEFAULT_DISCOVERY_MODULE_YEAR_ORDER,
)


def _default_config(default_order) -> dict:
    return {
        "order": list(default_order),
        "enabled": {module_id: True for module_id in default_order},
    }


def _load_config(config_file: str, default_order) -> dict:
    """Read a persisted order/enabled config, falling back to defaults if
    the file is missing or unreadable."""
    path = UPath(config_file)
    if not path.exists():
        return _default_config(default_order)

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        slog(f"Failed to read discovery modules config '{config_file}', using defaults: {e}")
        return _default_config(default_order)

    data.setdefault("order", [])
    data.setdefault("enabled", {})
    return data


def _save_config(config: dict, config_file: str) -> None:
    UPath(config_file).write_text(json.dumps(config, indent=2))


def _reconcile_config(available_module_ids, config_file: str, default_order) -> dict:
    """Sync a persisted config against the modules actually available: ones
    no longer available (deleted/renamed, or - for the Years config - no
    longer implementing get_release_year()) are dropped, and new ones are
    appended (enabled by default). Persists the result if it changed, so
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

    enabled = {module_id: config["enabled"].get(module_id, True) for module_id in order}

    reconciled = {"order": order, "enabled": enabled}
    if reconciled != config:
        _save_config(reconciled, config_file)
    return reconciled


# -- Album fetchers --------------------------------------------------------

def load_discovery_config() -> dict:
    return _load_config(DISCOVERY_MODULES_CONFIG_FILE, DEFAULT_DISCOVERY_MODULE_ORDER)


def save_discovery_config(config: dict) -> None:
    _save_config(config, DISCOVERY_MODULES_CONFIG_FILE)


def reconcile_discovery_config(available_module_ids) -> dict:
    return _reconcile_config(available_module_ids, DISCOVERY_MODULES_CONFIG_FILE, DEFAULT_DISCOVERY_MODULE_ORDER)


# -- Year fetchers ----------------------------------------------------------

def load_year_discovery_config() -> dict:
    return _load_config(DISCOVERY_MODULES_YEAR_CONFIG_FILE, DEFAULT_DISCOVERY_MODULE_YEAR_ORDER)


def save_year_discovery_config(config: dict) -> None:
    _save_config(config, DISCOVERY_MODULES_YEAR_CONFIG_FILE)


def reconcile_year_discovery_config(available_module_ids) -> dict:
    return _reconcile_config(available_module_ids, DISCOVERY_MODULES_YEAR_CONFIG_FILE, DEFAULT_DISCOVERY_MODULE_YEAR_ORDER)
