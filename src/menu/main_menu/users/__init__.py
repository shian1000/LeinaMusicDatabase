"""Main menu -> Users: creating users, logging into a user's private
database, and managing the users themselves (utils/database/users.py)."""

from pathlib import Path

import questionary

from config.constants import USER_PLAYLIST_SOURCE_FOLDER
from settings import settings
from utils.common.debug import slog
from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.database.song_references import apply_song_id_changes
from utils.database.user_playlists import list_playlists, save_playlist
from utils.database.users import (
    InvalidUserError,
    UsersUnavailableError,
    change_password,
    create_user,
    delete_user,
    list_usernames,
    normalize_username,
    read_account,
    rename_user,
    users_available,
    verify_user,
)
from utils.discoveries.folder_playlist import build_folder_playlist
from utils.ui.menu_utils import execute_menu_item, open_file_browser_terminal

RED = "\033[91m"
RESET = "\033[0m"


def _error(message) -> None:
    print(f"{RED}{message}{RESET}")


def _users_or_explain() -> list[str] | None:
    if not users_available():
        _error(f"The users folder {settings.users_dir} is on a drive that isn't mounted "
               "- plug in the external drive first.")
        return None
    names = list_usernames()
    if not names:
        print('No users yet - create one with "Create a new user".')
        return None
    return names


def _pick_user(question: str) -> str | None:
    names = _users_or_explain()
    if not names:
        return None
    return questionary.select(question, choices=names).ask()


def _ask_new_password(question: str = "New password:") -> str | None:
    password = questionary.password(question).ask()
    if password is None:
        return None
    if questionary.password("Repeat the password:").ask() != password:
        _error("The passwords don't match.")
        return None
    return password


# --------------------
# Enter user database
# --------------------

def _create_playlist_from_folder(username: str) -> None:
    folder = open_file_browser_terminal(settings.export_dir)
    if not folder:
        return
    folder = Path(str(folder))
    name = questionary.text("Playlist name:", default=folder.name).ask()
    if not name:
        print("Cancelled.")
        return

    music_session, _ = open_and_set_global_database_sessions()
    print(f"Matching the mp3 files in {folder} with the database...")
    playlist = build_folder_playlist(music_session, folder)
    if not playlist.tracks:
        print("No mp3 files in that folder.")
        return

    if playlist.unmatched:
        print(f"\n{len(playlist.unmatched)} of {len(playlist.tracks)} file(s) have no song in the database.")
        choice = questionary.select("What should happen with them?", choices=["Show"]).ask()
        if choice == "Show":
            for track in playlist.unmatched:
                print(f"  {track.file.relative_to(folder)}   ({track.artist or '?'} - {track.title})")
        # No other option yet: the playlist isn't saved while songs are missing
        print("\nPlaylist not saved.")
        return

    playlist_id = save_playlist(username, name, playlist.tracks,
                                source=USER_PLAYLIST_SOURCE_FOLDER, source_id=str(folder))
    print(f"Saved playlist '{name}' (#{playlist_id}) with {len(playlist.tracks)} track(s).")


def _show_playlists(username: str) -> None:
    playlists = list_playlists(username)
    if not playlists:
        print("No playlists yet.")
        return
    for playlist_id, name, tracks, missing in playlists:
        note = f", {missing} not in the database" if missing else ""
        print(f"  #{playlist_id} {name} - {tracks} track(s){note}")


def enter_user_database() -> None:
    if not _users_or_explain():
        return
    username = questionary.text("Username:").ask()
    if not username:
        return
    password = questionary.password("Password:").ask()
    if password is None or not verify_user(username, password):
        _error("Wrong username or password.")
        return
    username = username.strip().lower()

    # Catch up on songs merged or deleted while this user's DB was out of reach
    try:
        apply_song_id_changes(force=True)
    except Exception as exc:
        slog(f"Updating song references failed: {exc}", priority=3)

    action_map = {
        "Create playlist from folder": lambda: _create_playlist_from_folder(username),
        "Show playlists": lambda: _show_playlists(username),
    }
    execute_menu_item(f"User database: {username}", action_map, exit_label="Log out")


# --------------------
# Managing users
# --------------------

def create_new_user() -> None:
    """Make a user folder with an empty private user.db (playlists, private
    tags) and a password."""
    if not users_available():
        _error(f"The users folder {settings.users_dir} is on a drive that isn't mounted "
               "- plug in the external drive first.")
        return
    existing = list_usernames()
    print(f"Users are kept in {settings.users_dir}")
    print(f"Existing users: {', '.join(existing) if existing else 'none yet'}\n")

    username = questionary.text("Username (lowercase letters, digits, '-' or '_'):").ask()
    if not username:
        print("Cancelled.")
        return
    try:
        username = normalize_username(username)
    except InvalidUserError as exc:
        _error(exc)
        return
    if username in existing:
        _error(f"User '{username}' already exists.")
        return

    password = _ask_new_password("Password:")
    if password is None:
        print("Nothing created.")
        return
    try:
        folder = create_user(username, password)
    except (InvalidUserError, UsersUnavailableError) as exc:
        _error(exc)
        return
    print(f"Created user '{username}': {folder}")


def list_all_users() -> None:
    names = _users_or_explain()
    if not names:
        return
    print(f"Users in {settings.users_dir}:")
    for name in names:
        created = read_account(name).get("created_at", "?")
        print(f"  {name}  (created {created})")


def rename_user_menu() -> None:
    old = _pick_user("Which user should be renamed?")
    if not old:
        return
    new = questionary.text("New username (lowercase letters, digits, '-' or '_'):").ask()
    if not new:
        print("Cancelled.")
        return
    try:
        folder = rename_user(old, new)
    except (InvalidUserError, UsersUnavailableError, OSError) as exc:
        _error(exc)
        return
    print(f"Renamed '{old}' to '{folder.name}'.")


def change_password_menu() -> None:
    username = _pick_user("Whose password should be changed?")
    if not username:
        return
    password = _ask_new_password()
    if password is None:
        print("Password not changed.")
        return
    try:
        change_password(username, password)
    except (InvalidUserError, UsersUnavailableError) as exc:
        _error(exc)
        return
    print(f"Password of '{username}' changed.")


def delete_user_menu() -> None:
    username = _pick_user("Which user should be deleted?")
    if not username:
        return
    print(f"This removes '{username}' with all their playlists and private tags.")
    print("Their folder is moved to the archive, not erased, so it can be put back by hand.")
    typed = questionary.text(f"Type '{username}' to confirm:").ask()
    if typed != username:
        print("Cancelled.")
        return
    try:
        archived = delete_user(username)
    except (InvalidUserError, UsersUnavailableError, OSError) as exc:
        _error(exc)
        return
    print(f"Deleted '{username}'. Their data is now in {archived}")


def users_menu() -> None:
    action_map = {
        "Create a new user": create_new_user,
        "Enter user database": enter_user_database,
        "List all users": list_all_users,
        "Rename user": rename_user_menu,
        "Change password": change_password_menu,
        "Delete user": delete_user_menu,
    }
    execute_menu_item("Users", action_map, exit_label="Back")
