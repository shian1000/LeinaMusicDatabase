"""Settings -> "Name cleanup": which of sanitize's cleanup switches are
on - each one for everything at once: the mp3 and YouTube imports, file
names, Leina Radio's requests and Clean names on the database. Shown in the
order the steps run; saved at once (utils/common/sanitize/settings.py),
used from the next cleanup on."""

import questionary

from config.constants import SANITIZE_SWITCH_LABELS
from utils.common import sanitize
from utils.common.output import BOLD, RESET


def name_cleanup_menu() -> None:
    print("How names (artists, titles, albums) are cleaned before they're saved or compared - each step the")
    print("same for the imports, Leina Radio's requests and Clean names. A name typed by hand is never changed.")
    group = None
    for switch in sanitize.SWITCHES:
        heading, label, explanation = SANITIZE_SWITCH_LABELS[switch]
        if heading != group:
            group = heading
            print(f"\n{group}")
        print(f"  {BOLD}{label}{RESET} - {explanation}")
    print()
    choices = [questionary.Choice(title=SANITIZE_SWITCH_LABELS[switch][1], value=switch,
                                  checked=sanitize.switch_on(switch)) for switch in sanitize.SWITCHES]
    picked = questionary.checkbox("Name cleanup steps (space to tick or untick, Enter to save):", choices=choices).ask()
    if picked is None:
        print("Cancelled, nothing changed.")
        return
    turned_on = [switch for switch in sanitize.SWITCHES if switch in picked and not sanitize.switch_on(switch)]
    turned_off = [switch for switch in sanitize.SWITCHES if switch not in picked and sanitize.switch_on(switch)]
    if not turned_on and not turned_off:
        print("Nothing changed.")
        return
    for switch in turned_on:
        sanitize.set_switch_on(switch, True)
    for switch in turned_off:
        sanitize.set_switch_on(switch, False)
    for heading, changed in (("On", turned_on), ("Off", turned_off)):
        if changed:
            print(f"{heading}: {', '.join(SANITIZE_SWITCH_LABELS[switch][1] for switch in changed)}.")
    if turned_on:
        print("The database's names get it at the next Enter database -> Manage database -> \"Clean names\".")
