"""Persisted user settings for discovery modules: which are enabled, and in
what order they get tried (see discoveries_manager.py for how they're run).

Modules are identified by their filename stem (e.g. "wikipedia_fetcher"),
which is stable across renames of the display name (MODULE_NAME).
"""
import json
from upath import UPath
from utils.common.debug import slog
from config.constants import DISCOVERY_MODULES_CONFIG_FILE, DEFAULT_DISCOVERY_MODULE_ORDER


def _config_path() -> UPath:
    return UPath(DISCOVERY_MODULES_CONFIG_FILE)


def _default_config() -> dict:
    return {
        "order": list(DEFAULT_DISCOVERY_MODULE_ORDER),
        "enabled": {module_id: True for module_id in DEFAULT_DISCOVERY_MODULE_ORDER},
    }


def load_discovery_config() -> dict:
    """Read the persisted order/enabled config, falling back to defaults if
    the file is missing or unreadable."""
    path = _config_path()
    if not path.exists():
        return _default_config()

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        slog(f"Failed to read discovery modules config, using defaults: {e}")
        return _default_config()

    data.setdefault("order", [])
    data.setdefault("enabled", {})
    return data


def save_discovery_config(config: dict) -> None:
    path = _config_path()
    path.write_text(json.dumps(config, indent=2))


def reconcile_discovery_config(available_module_ids) -> dict:
    """Sync the persisted config against the modules actually present on
    disk: modules that were deleted/renamed are dropped, and new ones found
    in discovery_modules/ are appended (enabled by default). Persists the
    result if it changed, so this only needs to run once per discovery.
    """
    available = list(available_module_ids)
    available_set = set(available)
    config = load_discovery_config()

    order = [module_id for module_id in config["order"] if module_id in available_set]
    known = set(order)
    for module_id in available:
        if module_id not in known:
            order.append(module_id)
            known.add(module_id)

    enabled = {module_id: config["enabled"].get(module_id, True) for module_id in order}

    reconciled = {"order": order, "enabled": enabled}
    if reconciled != config:
        save_discovery_config(reconciled)
    return reconciled
