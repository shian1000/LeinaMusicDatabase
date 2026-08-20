import questionary
from utils.ui.menu_utils import execute_menu_item, clear_screen
from utils.discoveries.discoveries_manager import load_all_discovery_modules_metadata
from utils.discoveries.discovery_settings import load_discovery_config, save_discovery_config


def settings_menu():
    action_map = {
        "Discovery modules": discovery_modules_menu,
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
