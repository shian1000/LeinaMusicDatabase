"""Settings -> "Run all: steps": which steps of Manage database -> "Run all
clean-ups and lookups in order" are ticked at first. Only the steps turned
off are saved (by key of constants.RUN_ALL_STEP_LABELS), so a new step starts
turned on."""
from upath import UPath
from utils.common.json_file import read_json, write_json
from config.constants import RUN_ALL_CONFIG_FILE, RUN_ALL_STEP_LABELS

CONFIG_PATH = UPath(RUN_ALL_CONFIG_FILE)


def steps_turned_off() -> set[str]:
    """The keys of the steps turned off; unknown ones (a step since removed) left out."""
    saved = read_json(CONFIG_PATH, {}, "Run all config").get("turned_off", [])
    return {key for key in saved if isinstance(key, str) and key in RUN_ALL_STEP_LABELS} if isinstance(saved, list) else set()


def step_on(key: str) -> bool:
    return key not in steps_turned_off()


def save_steps_turned_off(keys) -> None:
    write_json(CONFIG_PATH, {"turned_off": [key for key in RUN_ALL_STEP_LABELS if key in set(keys)]})
