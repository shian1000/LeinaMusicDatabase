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
from upath import UPath
from utils.common.json_file import read_json, write_json
from config.constants import (
    DISCOVERY_MODULES_CONFIG_FILE,
    DEFAULT_DISCOVERY_MODULE_ORDER,
    DISCOVERY_MODULES_YEAR_CONFIG_FILE,
    DEFAULT_DISCOVERY_MODULE_YEAR_ORDER,
    DISCOVERY_MODULES_NEEDING_API_KEY,
)


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
    no longer available (deleted/renamed, or - for the Years config - no
    longer implementing get_release_year()) are dropped, and new ones are
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
