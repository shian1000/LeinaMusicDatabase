"""Main menu -> Users: creating users, logging into a user's private
database, managing the users themselves (utils/database/users.py), and the
owner's view of moods and permissions (moods.py next to this file)."""

from pathlib import Path

import questionary

from config.constants import USER_PLAYLIST_SOURCE_FOLDER
from settings import settings
from utils.common.debug import slog
from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.database.song_references import apply_song_id_changes
from utils.database.user_playlists import delete_playlist, list_playlists, playlist_songs, save_playlist
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
from utils.database.datatables import Song
from menu.main_menu.users.moods import moods_menu, permissions_menu
from utils.database.song_artists import song_artist_label
from utils.discoveries.folder_playlist import assign_song, build_folder_playlist
from utils.discoveries.import_engine import find_songs_by_title
from utils.discoveries.mp3_utils import write_song_tags
from utils.ui.display_utils import display_song_links, display_songs
from utils.ui.menu_utils import execute_menu_item, open_file_browser_terminal, search_and_pick_db_object

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

# Per-file choices for a file with no song in the database
_UNMATCHED_BY_TITLE = "Search by title (any artist)"
_UNMATCHED_SEARCH = "Search the database"
_UNMATCHED_NEXT = "Next (leave it without a song)"
_UNMATCHED_STOP = "Stop"
_CONFIRM_SONG = "Yes, this is the song"
_SEARCH_INSTEAD = "No - search the database by hand"
_BACK_TO_OPTIONS = "Back to the options for this file"


def _song_choice_label(song) -> str:
    details = ", ".join(str(v) for v in (song.album, song.year) if v)
    return f"{song_artist_label(song)} - {song.title}" + (f"  ({details})" if details else "")


def _pick_by_title(music_session, track):
    """Songs of any artist with this title: the user picks one, then has the
    final say - confirm it, or search the database by hand instead."""
    query = questionary.text("Title to look for:", default=track.title or track.file.stem).ask()
    if not query:
        return None
    songs = find_songs_by_title(music_session, query)
    if not songs:
        print("No song with that title in the database.")
        if questionary.confirm("Search the database by hand instead?", default=True).ask():
            return _pick_by_search()
        return None

    choices = [questionary.Choice(title=_song_choice_label(s), value=s) for s in songs]
    picked = questionary.select("Which song is this file?",
                                choices=choices + [_SEARCH_INSTEAD, _BACK_TO_OPTIONS]).ask()
    if picked is None or picked == _BACK_TO_OPTIONS:
        return None
    if picked == _SEARCH_INSTEAD:
        return _pick_by_search()

    print(f"  File:  {track.file.name}")
    print(f"  Song:  {_song_choice_label(picked)}")
    verdict = questionary.select("Is this the right song?",
                                 choices=[_CONFIRM_SONG, _SEARCH_INSTEAD, _BACK_TO_OPTIONS]).ask()
    if verdict == _CONFIRM_SONG:
        return picked
    if verdict == _SEARCH_INSTEAD:
        return _pick_by_search()
    return None


def _pick_by_search():
    song = search_and_pick_db_object("Song", pick_question="Which song is this file?")
    if song is None:
        print("Nothing picked.")
        return None
    if not questionary.confirm(f"Use \"{_song_choice_label(song)}\"?", default=True).ask():
        return None
    return song


def _review_unmatched_files(music_session, folder: Path, unmatched: list) -> None:
    """One file at a time: its name, folder and what was read from it, then
    the options for it. A song picked for a file goes into its track; a file
    left without one stays in `unmatched` - the caller won't save then."""
    for i, track in enumerate(list(unmatched), 1):
        print(f"\n[{i}/{len(unmatched)}] {track.file.name}")
        print(f"  Folder: {track.file.parent.relative_to(folder) if track.file.parent != folder else '.'}")
        print(f"  Read as: {track.artist or '?'} - {track.title or '?'}")
        while True:
            choice = questionary.select(
                "What should happen with this file?",
                choices=[_UNMATCHED_BY_TITLE, _UNMATCHED_SEARCH, _UNMATCHED_NEXT, _UNMATCHED_STOP],
            ).ask()
            if choice is None or choice == _UNMATCHED_STOP:
                return
            if choice == _UNMATCHED_NEXT:
                break
            song = _pick_by_title(music_session, track) if choice == _UNMATCHED_BY_TITLE else _pick_by_search()
            if song is not None:
                assign_song(track, song)
                unmatched.remove(track)
                print(f"  -> {_song_choice_label(song)}")
                break


def _offer_tag_update(music_session, tracks: list) -> None:
    """After a playlist is saved: copy the database's title/artist/album/year
    into each file's tags, if the user wants."""
    if not questionary.confirm(
        f"Update the mp3 tags of these {len(tracks)} file(s) with the data from the database?",
        default=False,
    ).ask():
        return
    changed, failed = 0, []
    for track in tracks:
        song = music_session.get(Song, track.song_id)
        try:
            changed += write_song_tags(track.file, song)
        except Exception as exc:
            failed.append((track.file, exc))
    print(f"Tags updated in {changed} file(s); {len(tracks) - changed - len(failed)} already matched.")
    for file, exc in failed:
        _error(f"  Couldn't write {file}: {exc}")


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
        _review_unmatched_files(music_session, folder, playlist.unmatched)
        if playlist.unmatched:
            # A playlist is only saved complete, every file with its song
            print(f"\n{len(playlist.unmatched)} file(s) still without a song - playlist not saved.")
            return

    playlist_id = save_playlist(username, name, playlist.tracks,
                                source=USER_PLAYLIST_SOURCE_FOLDER, source_id=str(folder))
    print(f"Saved playlist '{name}' (#{playlist_id}) with {len(playlist.tracks)} track(s).")
    _offer_tag_update(music_session, playlist.tracks)


_BACK = "Back"


def _show_playlist(username: str, playlist_id: int, show) -> None:
    """One playlist through a song table (display_songs / display_song_links);
    tracks with no song in the database can't be in it, so they're listed
    underneath with the title/artist the playlist kept."""
    music_session, _ = open_and_set_global_database_sessions()
    songs, missing = playlist_songs(music_session, username, playlist_id)
    show(songs)
    if missing:
        print(f"\n{len(missing)} track(s) not in the database:")
        for track in missing:
            video = f"  https://www.youtube.com/watch?v={track.youtube_video_id}" if track.youtube_video_id else ""
            print(f"  {track.position + 1}. {track.artist or '?'} - {track.title}{video}")


def _delete_playlist(username: str, playlist_id: int, name: str) -> str | None:
    """Returns _BACK once deleted, which closes the playlist's menu."""
    print(f"This deletes '{name}' here and on {username}'s phones (at their next sync).")
    if not questionary.confirm("Delete it?", default=False).ask():
        return None
    archived = delete_playlist(username, playlist_id)
    print(f"Deleted '{name}'." + (f" A copy is kept in {archived}" if archived else ""))
    return _BACK


def _playlist_menu(username: str, playlist_id: int, name: str) -> None:
    action_map = {
        "Show playlist contents": lambda: _show_playlist(username, playlist_id, display_songs),
        "Show playlist links": lambda: _show_playlist(username, playlist_id, display_song_links),
        "Delete playlist": lambda: _delete_playlist(username, playlist_id, name),
    }
    execute_menu_item(f"Playlist: {name}", action_map, exit_label=_BACK)


def _show_playlists(username: str) -> None:
    """Pick a playlist, then what to do with it; Back returns to the list."""
    while True:
        playlists = list_playlists(username)
        if not playlists:
            print("No playlists yet.")
            return
        choices = [
            questionary.Choice(
                title=f"{name} - {tracks} track(s)" + (f", {missing} not in the database" if missing else "")
                + (", cover" if cover else ""),
                value=(playlist_id, name),
            )
            for playlist_id, name, tracks, missing, cover in playlists
        ]
        picked = questionary.select("Playlists:", choices=choices + [_BACK]).ask()
        if picked is None or picked == _BACK:
            return
        _playlist_menu(username, *picked)


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
        "Moods": moods_menu,
        "Permissions": permissions_menu,
    }
    execute_menu_item("Users", action_map, exit_label="Back")
