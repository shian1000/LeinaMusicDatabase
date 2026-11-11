"""Settings -> "Run all: steps": which steps Manage database -> "Run all
clean-ups and lookups in order" runs - ticked there at first; a step can
still be left out (or taken in) for one run from that list."""

from config.constants import RUN_ALL_STEP_LABELS
from utils.common.run_all_settings import save_steps_turned_off, steps_turned_off
from utils.ui.prompts import tick_items


def run_all_steps_menu() -> None:
    turned_off = steps_turned_off()
    picked = tick_items('Steps of "Run all clean-ups and lookups in order" (space toggles, enter saves):',
                        list(RUN_ALL_STEP_LABELS), RUN_ALL_STEP_LABELS.get,
                        checked=lambda key: key not in turned_off)
    if picked is None:
        print("Cancelled, nothing changed.")
        return
    now_off = set(RUN_ALL_STEP_LABELS) - set(picked)
    if now_off == turned_off:
        print("Nothing changed.")
        return
    save_steps_turned_off(now_off)
    for heading, changed in (("On", turned_off - now_off), ("Off", now_off - turned_off)):
        if changed:
            print(f"{heading}: {', '.join(RUN_ALL_STEP_LABELS[key] for key in RUN_ALL_STEP_LABELS if key in changed)}.")
