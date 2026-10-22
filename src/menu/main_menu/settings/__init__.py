import os
import subprocess
from datetime import datetime

import questionary
from utils.ui.prompts import ask_yes_no
from upath import UPath
from utils.ui.menu_utils import execute_menu_item, clear_screen, open_file_browser_terminal
from utils.discoveries.discoveries_manager import (
    load_all_discovery_modules_metadata,
    load_all_year_discovery_modules_metadata,
)
from utils.discoveries.discovery_settings import (
    load_discovery_config,
    save_discovery_config,
    load_year_discovery_config,
    save_year_discovery_config,
)
from utils.common import startup_settings
from utils.common.env_file import MACHINE_KEYS, read_env_value, save_env_value
from utils.common.sharing_setup import (
    lan_interfaces,
    listening_plan,
    address_candidates,
    phone_addresses,
    value_warning,
)
from utils.ui.sharing_wizard import offer_to_create_sharing_dir, run_sharing_wizard
from utils.database.database_location import load_database_dir_override, save_database_dir_override
from utils.database.backup import backup_databases
from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.database.files_index import FILES_DB_PATH, LibraryUnavailableError, scan_library
from utils.database.users import users_available, users_unavailable_message
from utils.database.moods import apply_global_mood_changes_quietly
from utils.database.sharing import (
    SharingUnavailableError,
    install_sharing_service,
    share_databases,
    sharing_service_disable_hint,
)
from utils.discoveries.discovery_stats import reset_discovery_stats
from utils.database.fetch_data_settings import load_max_songs_per_fetch, save_max_songs_per_fetch
from utils.discoveries.file_matching_settings import (
    load_auto_accept_artist_similarity,
    load_best_guess_min_score,
    load_closest_auto_accept_score,
    save_auto_accept_artist_similarity,
    save_best_guess_min_score,
    save_closest_auto_accept_score,
)
from config.constants import BEST_GUESS_MIN_SCORE_RANGE, CLOSEST_AUTO_ACCEPT_SCORE_RANGE, MIN_AUTO_ACCEPT_ARTIST_SIMILARITY, SHARING_HTTP_PORT
from settings import drive_unavailable_message, listen_addresses, settings, sharing_dir_available
from menu.main_menu.settings.public_database import public_database_menu


def settings_menu():
    action_map = {
        "Server": server_menu,
        "Discovery modules": discovery_modules_menu,
        "Max songs to process per fetch": set_max_songs_per_fetch,
        "Auto-accept similar artist names": set_auto_accept_artist_similarity,
        "Closest songs: minimum score": set_best_guess_min_score,
        "Closest songs: auto-accept score": set_closest_auto_accept_score,
        "Reset statistics records": reset_statistics_records,
        "Start-up tasks": startup_tasks_menu,
    }
    execute_menu_item("Settings", action_map, exit_label="Back")


_GITHUB_PUBLISH = "__github__"


def startup_tasks_menu():
    """Which of the start-up tasks run (all on by default). The GitHub one
    is the daily publish's own switch, shared with Public database on GitHub."""
    from utils.database import public_database

    print("What MusicDatabase does when it starts - all on by default, a change counts from the next start:\n")
    for switch in startup_settings.SWITCHES:
        print(f"\033[1m{switch.label}\033[0m - {switch.explanation}")
    print("\033[1mDaily publish to GitHub\033[0m - once a day, when the catalog changed: the public copy "
          "phones without the server download (Settings -> Server -> Public database on GitHub).")
    print(f"\nAlways done, not switchable: {startup_settings.ALWAYS}.\n")
    choices = [questionary.Choice(title=switch.label, value=switch.key, checked=startup_settings.enabled(switch))
               for switch in startup_settings.SWITCHES]
    choices.append(questionary.Choice(title="Daily publish to GitHub", value=_GITHUB_PUBLISH,
                                      checked=public_database.daily_publish_enabled()))
    picked = questionary.checkbox("Start-up tasks (space to tick or untick, Enter to save):", choices=choices).ask()
    if picked is None:
        print("Cancelled, nothing changed.")
        return
    changed = [switch.label for switch in startup_settings.save_choices(picked)]
    github = _GITHUB_PUBLISH in picked
    if github != public_database.daily_publish_enabled():
        public_database.set_daily_publish(github)
        changed.append("Daily publish to GitHub")
    if not changed:
        print("Nothing changed.")
        return
    print(f"Changed: {', '.join(changed)}.")
    if not startup_settings.enabled(startup_settings.BACKUP):
        print(f"\033[93mThe daily backup is off: {startup_settings.BACKUP.explanation}\033[0m")


def server_menu():
    """Where the databases live, their backups, and sharing them with the
    phone app."""
    action_map = {
        "Back up database now": backup_database_now,
        "Database location": database_location_menu,
        "Folders and network": machine_settings_menu,
        "Index music library": index_music_library,
        "Submit database for sharing": submit_database_for_sharing,
        "Setup this PC for database sharing": setup_database_sharing,
        "Public database on GitHub": public_database_menu,
    }
    execute_menu_item("Server", action_map, exit_label="Back")


def set_max_songs_per_fetch():
    """Set how many songs a single 'Fetch database data' run (e.g. Fill
    missing albums) processes at once."""
    current = load_max_songs_per_fetch()
    print(f"Current max songs to process per fetch: {current}\n")

    typed = questionary.text("New max songs per fetch:").ask()
    if not typed:
        print("Cancelled.")
        return

    try:
        value = int(typed)
    except ValueError:
        print(f"\033[91m'{typed}' is not a valid number.\033[0m")
        return

    if value <= 0:
        print("\033[91mMust be a positive number.\033[0m")
        return

    save_max_songs_per_fetch(value)
    print(f"Max songs per fetch set to: {value}")


def set_auto_accept_artist_similarity():
    """How similar a file's tag artist must be to the song's artist (the
    title matching exactly) for "Create playlist from folder" to match it
    without asking."""
    current = load_auto_accept_artist_similarity()
    print("Create playlist from folder: a file whose title matches a song but whose artist is spelt")
    print('differently ("Liiquid tension experiment" = 0.98 like "Liquid Tension Experiment") is matched')
    print("without asking from this similarity up; below it you're asked. 1 = always ask.")
    print(f"Current: {current}\n")

    typed = questionary.text(f"New value ({MIN_AUTO_ACCEPT_ARTIST_SIMILARITY}-1):").ask()
    if not typed:
        print("Cancelled.")
        return
    try:
        value = float(typed.replace(",", "."))
        save_auto_accept_artist_similarity(value)
    except ValueError:
        print(f"\033[91m'{typed}' is not a number between {MIN_AUTO_ACCEPT_ARTIST_SIMILARITY} and 1.\033[0m")
        return
    print(f"Auto-accept similar artist names from: {value}")


def set_best_guess_min_score():
    """The lowest score of the closest songs "Create playlist from folder"
    offers for a file nothing else matched."""
    lowest, highest = BEST_GUESS_MIN_SCORE_RANGE
    print("Create playlist from folder: for a file nothing else found a song for, up to 3 of the")
    print("closest songs are offered, with how well they fit (title, and the artist when the file has")
    print("one). Only songs from this score up are offered - higher means fewer, surer guesses.")
    print(f"Current: {load_best_guess_min_score()}\n")

    typed = questionary.text(f"New minimum score ({lowest}-{highest}):").ask()
    if not typed:
        print("Cancelled.")
        return
    try:
        value = float(typed.replace(",", "."))
        save_best_guess_min_score(value)
    except ValueError:
        print(f"\033[91m'{typed}' is not a number between {lowest} and {highest}.\033[0m")
        return
    print(f"Closest songs offered from score: {value}")


def set_closest_auto_accept_score():
    """The score from which "Create playlist from folder" takes a suggested
    song without asking."""
    lowest, highest = CLOSEST_AUTO_ACCEPT_SCORE_RANGE
    print("Create playlist from folder: a suggested song (similar song, closest song, ...) whose score")
    print("reaches this is taken without asking - when it's the only one that does - and listed after")
    print("matching as auto-accepted. A file with no artist only for a long title one song has. 1 = always ask.")
    print(f"Current: {load_closest_auto_accept_score()}\n")

    typed = questionary.text(f"New auto-accept score ({lowest}-{highest}):").ask()
    if not typed:
        print("Cancelled.")
        return
    try:
        value = float(typed.replace(",", "."))
        save_closest_auto_accept_score(value)
    except ValueError:
        print(f"\033[91m'{typed}' is not a number between {lowest} and {highest}.\033[0m")
        return
    print(f"Closest songs auto-accepted from score: {value}")


def reset_statistics_records():
    """Zero the fetchers' invocation/success counters shown in the Statistics
    menu (album and year lookups), so they start being collected anew."""
    if not ask_yes_no("Reset all fetcher statistics (albums and years) to zero? This can't be undone."):
        print("Cancelled.")
        return
    reset_discovery_stats()
    print("Fetcher statistics reset.")


def backup_database_now():
    """Manually trigger an immediate snapshot of music.db, tag.db and every
    user's user.db into archive/."""
    created = backup_databases(reason="manual")
    if created:
        print("Backup created:")
        for path in created:
            print(f"  {path}")
    else:
        print("No database files found to back up.")
    if not users_available():
        print(f"Users' databases skipped: {users_unavailable_message()}")


def index_music_library():
    """Scan the music library folder into files.db: new, changed and removed
    files, and which song each file is. Also runs once a day at startup;
    the phone gets files.db with the next "Submit database for sharing"."""
    music_session, _ = open_and_set_global_database_sessions()
    print(f"Indexing {settings.local_library_dir_str} ...")
    try:
        report = scan_library(music_session, progress=lambda done, total: print(f"  {done}/{total} files read"))
    except LibraryUnavailableError as exc:
        print(f"\033[91mNot indexed: {exc}\033[0m")
        return
    print(f"\n{report.files} audio file(s) in the library: {report.added} new, "
          f"{report.changed} changed, {report.removed} removed since the last scan.")
    matched = report.files - len(report.unmatched)
    print(f"{matched} file(s) have a song in the database ({report.matched_now} matched now), "
          f"{len(report.unmatched)} don't.")
    if report.unmatched and ask_yes_no("Show the files without a song?"):
        for path, artist, title in report.unmatched:
            print(f"  {path}   ({artist or '?'} - {title})")
    print(f"Index saved to {FILES_DB_PATH}")


def submit_database_for_sharing():
    """Copy music.db, tag.db and files.db to the folder the mobile app downloads from,
    right now instead of waiting for the next daily backup."""
    # Phones' changes to global moods go into tag.db first, so this copy has them
    applied = apply_global_mood_changes_quietly()
    if applied:
        print(f"Applied {len(applied)} change(s) to global moods from Leina Radio.")
    try:
        published = share_databases()
    except SharingUnavailableError as exc:
        print(f"\033[91mNot shared: {exc}\033[0m")
        return
    except Exception as exc:
        print(f"\033[91mSharing failed: {exc}\033[0m")
        return
    if not published:
        print("No database files found to share.")
        return
    print(f"Shared at {datetime.now():%Y-%m-%d %H:%M}:")
    for path in published:
        print(f"  {path}")


def setup_database_sharing():
    """Install the systemd user service that serves the shared databases to
    the mobile app, starting shortly after every login - or walk through
    everything it needs with the setup wizard."""
    print("This sets this PC up as the server Leina Radio downloads the databases from.")
    print("The step-by-step setup explains every setting it needs (the sharing folder, an external")
    print("drive, the addresses to listen on, your music library) and then checks the first copy, users")
    print("and the service. Nothing is done without asking; safe to stop at any step and to run again.\n")
    wizard = ask_yes_no("Run the step-by-step setup? (No = just install the service with the current settings)")
    if wizard is None:
        return
    if wizard:
        run_sharing_wizard()
        return
    if settings.sharing_dir is None:
        print(f"\033[91mCan't set up sharing: {drive_unavailable_message(None)}\033[0m")
        return
    if not sharing_dir_available(settings.sharing_drive_mount, settings.sharing_dir):
        # The server waits for it (answers 503 meanwhile), so this is no reason to stop
        print(f"\033[93m{drive_unavailable_message(settings.sharing_drive_mount, settings.sharing_dir)}\033[0m")
    plan = listening_plan()
    if not plan:
        print("\033[91mWith the current settings the server has no address to listen on - set the LAN interface "
              "or an address to listen on (Settings -> Server -> Folders and network).\033[0m")
        return
    print("This installs a systemd user service that serves only music.db, tag.db and files.db")
    print(f"from {settings.sharing_dir}, port {SHARING_HTTP_PORT}, on:")
    for what, address in plan:
        print(f"  {what}: {'every address of this PC' if address == '0.0.0.0' else address or 'no address right now'}")
    print("A phone must be able to reach one of them.\n")
    if not ask_yes_no("Install (or reinstall) it now?"):
        print("Cancelled.")
        return
    try:
        steps = install_sharing_service()
    except subprocess.CalledProcessError as exc:
        print(f"\033[91mSetup failed: {' '.join(exc.cmd)}\n{exc.stderr.strip()}\033[0m")
        return
    except OSError as exc:
        print(f"\033[91mSetup failed: {exc}\033[0m")
        return
    for step in steps:
        print(f"  {step}")
    print("\nLeina Radio -> Ustawienia -> Serwer, an address the phone can reach:")
    for field, address in phone_addresses(plan):
        print(f"  {field}: {address}")
    print("To turn it off:")
    print(sharing_service_disable_hint())
    # A fresh setup has nothing to serve until the first copy
    if ask_yes_no("Copy the databases to the sharing folder now (Submit database for sharing)?"):
        submit_database_for_sharing()


def discovery_modules_menu():
    action_map = {
        "Albums: Enable/disable modules": toggle_discovery_modules,
        "Albums: Set modules order": reorder_discovery_modules,
        "Years: Enable/disable modules": toggle_year_discovery_modules,
        "Years: Set modules order": reorder_year_discovery_modules,
    }
    execute_menu_item("Discovery modules", action_map, exit_label="Back")


def toggle_discovery_modules():
    """Tick/untick which discovery modules are used when searching for songs."""
    modules = load_all_discovery_modules_metadata()  # [(id, display_name), ...] in configured order
    config = load_discovery_config()

    choices = [
        questionary.Choice(
            title=display_name,
            value=module_id,
            checked=config["enabled"].get(module_id, True),
        )
        for module_id, display_name in modules
    ]

    selected_ids = questionary.checkbox(
        "Select modules to enable (space to toggle, enter to confirm)",
        choices=choices,
    ).ask()

    if selected_ids is None:  # cancelled (e.g. Ctrl+C)
        return

    config["enabled"] = {module_id: module_id in selected_ids for module_id, _ in modules}
    save_discovery_config(config)
    print("Discovery module settings saved.")


def reorder_discovery_modules():
    """Rearrange the priority order discovery modules are tried in."""
    modules = load_all_discovery_modules_metadata()
    config = load_discovery_config()
    order = [module_id for module_id, _ in modules]
    names = dict(modules)

    while True:
        clear_screen()
        print("Set modules order (top = tried first)\n")
        for i, module_id in enumerate(order, start=1):
            marker = "" if config["enabled"].get(module_id, True) else " (disabled)"
            print(f"{i}. {names[module_id]}{marker}")
        print()

        pick_choices = [questionary.Choice(title=names[m], value=m) for m in order]
        pick_choices.append(questionary.Choice(title="Done", value="__done__"))

        selected_id = questionary.select(
            "Pick a module to move",
            choices=pick_choices,
        ).ask()

        if selected_id is None or selected_id == "__done__":
            break

        direction = questionary.select(
            f"Move '{names[selected_id]}' where?",
            choices=["Move up", "Move down", "Move to top", "Move to bottom", "Cancel"],
        ).ask()

        idx = order.index(selected_id)
        if direction == "Move up" and idx > 0:
            order[idx - 1], order[idx] = order[idx], order[idx - 1]
        elif direction == "Move down" and idx < len(order) - 1:
            order[idx + 1], order[idx] = order[idx], order[idx + 1]
        elif direction == "Move to top":
            order.insert(0, order.pop(idx))
        elif direction == "Move to bottom":
            order.append(order.pop(idx))
        # "Cancel" or a dismissed prompt: no change

    config["order"] = order
    save_discovery_config(config)
    print("Module order saved.")


def toggle_year_discovery_modules():
    """Tick/untick which discovery modules are used when searching for a
    release year (only modules that implement get_release_year())."""
    modules = load_all_year_discovery_modules_metadata()
    config = load_year_discovery_config()

    if not modules:
        print("No discovery modules implement release-year lookups yet.")
        return

    choices = [
        questionary.Choice(
            title=display_name,
            value=module_id,
            checked=config["enabled"].get(module_id, True),
        )
        for module_id, display_name in modules
    ]

    selected_ids = questionary.checkbox(
        "Select modules to enable for Years (space to toggle, enter to confirm)",
        choices=choices,
    ).ask()

    if selected_ids is None:  # cancelled (e.g. Ctrl+C)
        return

    config["enabled"] = {module_id: module_id in selected_ids for module_id, _ in modules}
    save_year_discovery_config(config)
    print("Year discovery module settings saved.")


def reorder_year_discovery_modules():
    """Rearrange the priority order release-year discovery modules are tried in."""
    modules = load_all_year_discovery_modules_metadata()
    config = load_year_discovery_config()

    if not modules:
        print("No discovery modules implement release-year lookups yet.")
        return

    order = [module_id for module_id, _ in modules]
    names = dict(modules)

    while True:
        clear_screen()
        print("Set modules order for Years (top = tried first)\n")
        for i, module_id in enumerate(order, start=1):
            marker = "" if config["enabled"].get(module_id, True) else " (disabled)"
            print(f"{i}. {names[module_id]}{marker}")
        print()

        pick_choices = [questionary.Choice(title=names[m], value=m) for m in order]
        pick_choices.append(questionary.Choice(title="Done", value="__done__"))

        selected_id = questionary.select(
            "Pick a module to move",
            choices=pick_choices,
        ).ask()

        if selected_id is None or selected_id == "__done__":
            break

        direction = questionary.select(
            f"Move '{names[selected_id]}' where?",
            choices=["Move up", "Move down", "Move to top", "Move to bottom", "Cancel"],
        ).ask()

        idx = order.index(selected_id)
        if direction == "Move up" and idx > 0:
            order[idx - 1], order[idx] = order[idx], order[idx - 1]
        elif direction == "Move down" and idx < len(order) - 1:
            order[idx + 1], order[idx] = order[idx], order[idx + 1]
        elif direction == "Move to top":
            order.insert(0, order.pop(idx))
        elif direction == "Move to bottom":
            order.append(order.pop(idx))
        # "Cancel" or a dismissed prompt: no change

    config["order"] = order
    save_year_discovery_config(config)
    print("Year module order saved.")


def database_location_menu():
    action_map = {
        "Change database folder": change_database_location,
        "Reset to default": reset_database_location,
    }
    execute_menu_item("Database location", action_map, exit_label="Back")


def machine_settings_menu():
    """This machine's library and sharing folders and LAN interface, saved in
    .env. Read at startup, so a change needs a restart - and a new "Setup this
    PC for database sharing" before the sharing server uses it."""
    while True:
        choices = [questionary.Choice(title=f"{key.label}: {read_env_value(key.name) or '(not set)'}", value=key)
                   for key in MACHINE_KEYS]
        choices.append(questionary.Choice(title="Back", value="__back__"))
        key = questionary.select("Folders and network (saved in .env):", choices=choices).ask()
        if key in (None, "__back__"):
            return
        _change_machine_key(key)


def _change_machine_key(key) -> None:
    current = read_env_value(key.name)
    if key.is_path:
        methods = ["Open file manager", "Type path manually"]
    elif key.name == "SHARING_TAILSCALE":
        methods = [value for value in ("on", "off") if value != current]
    elif key.name == "SHARING_ADDRESSES":
        methods = ["Add an address of this PC", "Type them manually (comma-separated)"]
    else:
        methods = ["Pick a network interface", "Type the name manually"]
    if current:
        methods.append("Clear")
    methods.append("Cancel")
    method = questionary.select(f"{key.name} (now: {current or 'not set'}):", choices=methods).ask()
    if method in (None, "Cancel"):
        return
    if method == "Open file manager":
        picked = open_file_browser_terminal(current or os.path.expanduser("~"))
        value = str(picked) if picked else None
    elif method == "Pick a network interface":
        value = _pick_lan_interface()
    elif method in ("on", "off"):
        value = method
    elif method == "Add an address of this PC":
        added = _pick_local_address()
        value = ",".join(listen_addresses(current) + [added]) if added else None
    elif method == "Clear":
        value = ""
    else:
        value = questionary.text(f"{key.name}:", default=current).ask()
    if value is None:
        print("Cancelled.")
        return
    value = value.strip()
    warning = value_warning(key.name, value)
    if warning:
        print(f"\033[93m{warning}\033[0m")
        if not ask_yes_no("Save it anyway?"):
            print("Cancelled.")
            return
    save_env_value(key.name, value)
    print(f"{key.name} {'set to ' + value if value else 'cleared'} in .env.")
    if key.name == "SHARING_DIR":
        offer_to_create_sharing_dir(value)
    print('Restart the app for this to take effect, then run "Setup this PC for database sharing"'
          " again so the sharing server uses it too.")


def _pick_local_address() -> str | None:
    choices = [questionary.Choice(title=f"{address} ({name})", value=address) for name, address in address_candidates()]
    if not choices:
        print("No other IPv4 address on this PC.")
        return None
    choices.append(questionary.Choice(title="Cancel", value="__cancel__"))
    picked = questionary.select("Which address should the server also listen on?", choices=choices).ask()
    return None if picked == "__cancel__" else picked


def _pick_lan_interface() -> str | None:
    choices = [questionary.Choice(title=f"{name} ({address})", value=name) for name, address in lan_interfaces()]
    if not choices:
        print("No network interface with a private LAN address found.")
        return None
    choices.append(questionary.Choice(title="Cancel", value="__cancel__"))
    picked = questionary.select("Which interface is on the home network?", choices=choices).ask()
    return None if picked == "__cancel__" else picked


def change_database_location():
    """Set the folder music.db, tag.db and files.db are read from and written to.

    Only updates the persisted override -- the app reads Settings.database_dir
    once at startup, so this takes effect after a restart, not immediately.
    """
    current = load_database_dir_override() or str(settings.database_dir)
    print(f"Current database folder: {current}\n")

    method = questionary.select(
        "How do you want to set the new database folder?",
        choices=["Open file manager", "Type path manually", "Cancel"],
    ).ask()

    if method == "Open file manager":
        new_dir = open_file_browser_terminal(current)
    elif method == "Type path manually":
        typed = questionary.text("Type path:").ask()
        new_dir = UPath(typed) if typed else None
    else:
        return

    if new_dir is None:
        print("Cancelled.")
        return

    save_database_dir_override(str(new_dir))
    print(f"Database folder set to: {new_dir}")
    print("Restart the app for this to take effect.")


def reset_database_location():
    """Clear the override, reverting to the default database folder."""
    save_database_dir_override(None)
    print("Database folder reset to default.")
    print("Restart the app for this to take effect.")
