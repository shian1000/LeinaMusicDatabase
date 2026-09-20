import questionary
from upath import UPath
from utils.ui.menu_utils import execute_menu_item, clear_screen, open_file_browser_terminal
from utils.discoveries.discoveries_manager import load_all_discovery_modules_metadata
from utils.discoveries.discovery_settings import load_discovery_config, save_discovery_config
from utils.database.database_location import load_database_dir_override, save_database_dir_override
from settings import settings


def settings_menu():
    action_map = {
        "Discovery modules": discovery_modules_menu,
        "Database location": database_location_menu,
    }
    execute_menu_item("Settings", action_map, exit_label="Back")


def discovery_modules_menu():
    action_map = {
        "Enable/disable modules": toggle_discovery_modules,
        "Set modules order": reorder_discovery_modules,
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


def database_location_menu():
    action_map = {
        "Change database folder": change_database_location,
        "Reset to default": reset_database_location,
    }
    execute_menu_item("Database location", action_map, exit_label="Back")


def change_database_location():
    """Set the folder music.db and tag.db are read from and written to.

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
