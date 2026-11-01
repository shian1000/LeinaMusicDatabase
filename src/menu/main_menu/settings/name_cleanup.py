"""Settings -> "Name cleanup": which of sanitize.py's cleanup steps each
source runs - the mp3 and YouTube imports, file names, Leina Radio's
requests and Clean names on the database. Saved at once
(utils/common/sanitize_settings.py), used from the next cleanup on."""

import questionary

from config.constants import SANITIZE_SOURCE_LABELS, SANITIZE_STEP_LABELS
from utils.common import sanitize
from utils.common.output import BOLD, RESET
from utils.ui.prompts import ask_yes_no

_RESET = "__reset__"
_BACK = "__back__"
# Sources that bring new names in: a step turned on for one of them and not
# for the database leaves the names already stored as they were
_IMPORT_SOURCES = (sanitize.SOURCE_MP3, sanitize.SOURCE_FILE_NAME, sanitize.SOURCE_YOUTUBE, sanitize.SOURCE_REQUEST)


def _sources_text(name: str) -> str:
    on = [SANITIZE_SOURCE_LABELS[src] for src in sanitize.configurable_sources(name)
          if src in sanitize.step_sources(name)]
    return ", ".join(on) if on else "off"


def name_cleanup_menu() -> None:
    print("How names (artists, titles, albums) are cleaned before they're saved or compared, step by step,")
    print("for each place they come from. A name typed by hand is never changed.\n")
    while True:
        choices = [questionary.Choice(title=f"{SANITIZE_STEP_LABELS[name][0]}  [{_sources_text(name)}]", value=name)
                   for name in sanitize.step_names()]
        choices.append(questionary.Choice(title="Reset all to the defaults", value=_RESET))
        choices.append(questionary.Choice(title="Back", value=_BACK))
        picked = questionary.select("Name cleanup step:", choices=choices).ask()
        if picked in (None, _BACK):
            return
        if picked == _RESET:
            if ask_yes_no("Every step back to its defaults?"):
                sanitize.reset_step_sources()
                print("Every step is back to its defaults.")
            continue
        _change_step(picked)


def _change_step(name: str) -> None:
    label, explanation = SANITIZE_STEP_LABELS[name]
    print(f"\n{BOLD}{label}{RESET} - {explanation}")
    if name in sanitize.STEPS and sanitize.STEPS[name].mode == sanitize.MODE_REVIEW:
        print("Only offered in a list to tick, never applied without asking.")
    before = sanitize.step_sources(name)
    choices = [questionary.Choice(title=SANITIZE_SOURCE_LABELS[src], value=src, checked=src in before)
               for src in sanitize.configurable_sources(name)]
    picked = questionary.checkbox("Run it for (space to tick or untick, Enter to save):", choices=choices).ask()
    if picked is None:
        print("Cancelled, nothing changed.")
        return
    wanted = set(picked)
    newly_on = wanted - before
    stored = sanitize.SOURCE_STORED
    if (any(src in _IMPORT_SOURCES for src in newly_on) and stored not in wanted
            and stored in sanitize.configurable_sources(name)):
        # The names already in the database would keep the old form, and a new
        # name would no longer be compared with the same spelling
        if ask_yes_no("Also clean the names already in the database with it (in Clean names)?"):
            wanted.add(stored)
    if wanted == before:
        print("Nothing changed.")
        return
    sanitize.set_step_sources(name, wanted)
    print(f"Saved: {label} - {_sources_text(name)}.")
    if stored in wanted - before:
        print("The database's names get it at the next Enter database -> Manage database -> \"Clean names\".")
