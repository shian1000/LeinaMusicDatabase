"""The owner's menu. The server runs on its own, as a service; this only changes the
files it reads, so it can be opened and closed at any time."""

import socket

import questionary

from musicdatabase_core.accounts import InvalidAccountError
from musicdatabase_core.certificates import tls_certificate_code

from . import admin, vetting
from .config import SYNC_ADDON, Config

BACK = "Back"


def _ask_user(config: Config) -> str | None:
    users = admin.list_users(config)
    if not users:
        print("There are no users yet.")
        return None
    choice = questionary.select("Which user?", choices=[*users, BACK]).ask()
    return None if choice in (None, BACK) else choice


def _ask_new_password() -> str | None:
    password = questionary.password("Password:").ask()
    if password is None:
        return None
    if questionary.password("The same again:").ask() != password:
        print("The passwords differ.")
        return None
    return password


def _create_user(config: Config) -> None:
    username = questionary.text("Username:").ask()
    if not username:
        return
    password = _ask_new_password()
    if password is None:
        return
    print(f"Created {admin.create_user(config, username, password)}.")


def _change_password(config: Config) -> None:
    username = _ask_user(config)
    if username is None:
        return
    password = _ask_new_password()
    if password is None:
        return
    admin.change_password(config, username, password)
    print(f"Changed. Every phone of {username} has to log in again.")


def _delete_user(config: Config) -> None:
    username = _ask_user(config)
    if username is None:
        return
    if questionary.confirm(f"Delete {username}? Their data moves to the archive.", default=False).ask():
        print(f"Moved to {admin.delete_user(config, username)}.")


def _devices(config: Config) -> None:
    username = _ask_user(config)
    if username is None:
        return
    devices = admin.devices(config, username)
    if not devices:
        print(f"{username} isn't logged in anywhere.")
        return
    choices = [questionary.Choice(f"{device.name}, logged in {device.logged_in_at}", value=device.token_hash)
               for device in devices]
    choice = questionary.select("Log out which phone?", choices=[*choices, BACK]).ask()
    if choice not in (None, BACK):
        admin.revoke_device(config, username, choice)
        print("Logged out.")


def _users_menu(config: Config) -> None:
    actions = {"Create a user": _create_user, "Change a password": _change_password,
               "See and log out phones": _devices, "Delete a user": _delete_user}
    while True:
        print("Users:", ", ".join(admin.list_users(config)) or "none")
        choice = questionary.select("Users", choices=[*actions, BACK]).ask()
        if choice in (None, BACK):
            return
        try:
            actions[choice](config)
        except (InvalidAccountError, OSError) as exc:
            print(exc)


def _is_listening(address: str, port: int) -> bool:
    try:
        socket.create_connection((address, port), timeout=1).close()
        return True
    except OSError:
        return False


def _show_server(config: Config) -> None:
    scheme = "https" if config.tls else "http"
    if not config.addresses:
        print("No address set: WIRED_ADDRESSES in .env.")
    for address in config.addresses:
        state = "answering" if _is_listening(address, config.port) else "not answering"
        print(f"{scheme}://{address}:{config.port}/ - {state}")
    if config.tls:
        code = tls_certificate_code(str(config.tls_dir))
        print(f"Certificate code to compare in Leina Player: {code or 'made at the first start'}")
    print(f"Data: {config.users_dir.parent}")


def _catalog_menu(config: Config) -> None:
    from .addons.catalog import CatalogError, catalog_state, update_catalog

    print(f"Catalog: {catalog_state(config)}, from {config.catalog_repo}")
    if questionary.confirm("Look for a newer one now?", default=True).ask():
        try:
            print("Downloaded a newer catalog." if update_catalog(config) else "It's up to date.")
        except CatalogError as exc:
            print(f"Couldn't update the catalog: {exc}")


def _requests_menu(config: Config) -> None:
    folders = admin.request_folders_of(config)
    counts = folders.counts()
    print(f"Checked and ready for the home server: {counts['valid']}")
    print(f"Held until MDW understands them: {counts['held']}")
    print(f"Failed the check: {counts['rejected']}")
    for entry in vetting.recent_refusals(config.inbox_dir):
        print(f"Refused {entry['at']}: {entry['name']} from {entry['sender']} - {entry['reason']}")
    if counts["held"] and questionary.confirm("Check the held ones again?", default=True).ask():
        print(folders.recheck_held())


def run_menu(config: Config) -> int:
    print("MusicDatabaseWired")
    while True:
        catalog = ["Catalog"] if SYNC_ADDON in config.addons else []
        choice = questionary.select("Main menu", choices=["Users", "Requests", "Server", *catalog, "Exit"]).ask()
        if choice in (None, "Exit"):
            return 0
        if choice == "Users":
            _users_menu(config)
        elif choice == "Requests":
            _requests_menu(config)
        elif choice == "Server":
            _show_server(config)
        else:
            _catalog_menu(config)
