"""Main menu -> Users -> Moods and -> Permissions: the owner's view of the
moods ("Nastroje" on the phone) and of what each user may do. The logic is in
utils/database/moods.py; the owner sees every user's moods here without their
password (docs/runbooks/database.md -> "Moods on the phone")."""

from datetime import datetime

import questionary
from utils.ui.prompts import ask_yes_no
from rich.console import Console
from rich.table import Table

from config.constants import PERMISSION_LABELS
from settings import settings
from utils.common.debug import slog
from utils.database import global_mood_log
from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.database.datatables import Song
from utils.database.mood_sync import NotMigratedError
from utils.database.moods import (
    MoodError,
    add_song_to_global_mood,
    apply_global_mood_changes,
    create_global_mood,
    delete_global_mood,
    global_mood_history,
    global_mood_song_ids,
    list_global_moods,
    list_user_moods,
    pending_global_mood_changes,
    promote_user_mood,
    remove_song_from_global_mood,
    rename_global_mood,
    user_mood_song_ids,
)
from utils.database.song_artists import song_artist_label
from utils.database.song_references import apply_song_id_changes
from utils.database.users import (
    InvalidUserError,
    UsersUnavailableError,
    list_usernames,
    set_permissions,
    user_permissions,
    users_available,
    users_unavailable_message,
)
from utils.ui.display_utils import display_song_links, display_songs
from utils.ui.menu_utils import execute_menu_item, search_and_pick_db_object

RED = "\033[91m"
RESET = "\033[0m"
_BACK = "Back"
_CREATE = "Create a global mood"
# How many of the phones' changes "Changes from the phones" lists
_HISTORY_SHOWN = 100


def _error(message) -> None:
    print(f"{RED}{message}{RESET}")


def _pick_user(question: str) -> str | None:
    if not users_available():
        _error(f"The users folder isn't available: {users_unavailable_message()}")
        return None
    names = list_usernames()
    if not names:
        print('No users yet - create one with "Create a new user".')
        return None
    return questionary.select(question, choices=names + [_BACK]).ask()


def _songs(music_session, song_ids) -> list:
    """The Song rows of these ids, by artist and title; ids no longer in
    music.db are left out."""
    if not song_ids:
        return []
    songs = music_session.query(Song).filter(Song.id.in_(list(song_ids))).all()
    return sorted(songs, key=lambda s: (song_artist_label(s).lower(), (s.title or "").lower()))


def _song_label(song) -> str:
    return f"{song_artist_label(song)} - {song.title}"


def _show_songs(song_ids, show) -> None:
    music_session, _ = open_and_set_global_database_sessions()
    songs = _songs(music_session, song_ids)
    if not songs:
        print("No songs in this mood yet.")
        return
    show(songs)


def _apply_phone_changes(tag_session) -> None:
    """Phones' pending changes first, so the menu shows what they'll see."""
    try:
        applied = apply_global_mood_changes(tag_session)
    except Exception as exc:
        slog(f"Applying global mood changes failed: {exc}", priority=3)
        _error(f"Couldn't apply Leina Radio's changes to global moods: {exc}")
        return
    if applied:
        print(f"Applied {len(applied)} change(s) from Leina Radio - see \"Changes from Leina Radio\".")


# --------------------
# Global moods
# --------------------

def _add_song(mood_id: int) -> None:
    _, tag_session = open_and_set_global_database_sessions()
    song = search_and_pick_db_object("Song", pick_question="Which song should be in this mood?")
    if song is None:
        print("Nothing picked.")
        return
    added = add_song_to_global_mood(tag_session, mood_id, song.id)
    print(f"Added {_song_label(song)}." if added else f"{_song_label(song)} is already in this mood.")


def _remove_songs(mood_id: int) -> None:
    music_session, tag_session = open_and_set_global_database_sessions()
    songs = _songs(music_session, global_mood_song_ids(tag_session, mood_id))
    if not songs:
        print("No songs in this mood yet.")
        return
    picked = questionary.checkbox(
        "Tick the songs to remove from this mood:",
        choices=[questionary.Choice(title=_song_label(s), value=s.id) for s in songs],
    ).ask()
    if not picked:
        print("Nothing removed.")
        return
    for song_id in picked:
        remove_song_from_global_mood(tag_session, mood_id, song_id)
    print(f"Removed {len(picked)} song(s).")


def _rename(mood_id: int, name: str) -> str | None:
    _, tag_session = open_and_set_global_database_sessions()
    new_name = questionary.text("New name:", default=name).ask()
    if not new_name or new_name.strip() == name:
        print("Not renamed.")
        return None
    try:
        rename_global_mood(tag_session, mood_id, new_name)
    except MoodError as exc:
        _error(exc)
        return None
    print(f"Renamed to '{new_name.strip()}'. Leina Radio sees it after the next copy for sharing.")
    return _BACK


def _delete(mood_id: int, name: str) -> str | None:
    _, tag_session = open_and_set_global_database_sessions()
    count = len(global_mood_song_ids(tag_session, mood_id))
    print(f"This deletes the global mood '{name}' with its {count} song link(s), for every user "
          "(after the next copy for sharing). The songs themselves stay.")
    if not ask_yes_no("Delete it?"):
        return None
    delete_global_mood(tag_session, mood_id)
    print(f"Deleted '{name}'.")
    return _BACK


def _global_mood_menu(mood_id: int, name: str) -> None:
    def ids():
        return global_mood_song_ids(open_and_set_global_database_sessions()[1], mood_id)

    action_map = {
        "Show songs": lambda: _show_songs(ids(), display_songs),
        "Show song links": lambda: _show_songs(ids(), display_song_links),
        "Add a song": lambda: _add_song(mood_id),
        "Remove songs": lambda: _remove_songs(mood_id),
        "Rename": lambda: _rename(mood_id, name),
        "Delete": lambda: _delete(mood_id, name),
    }
    execute_menu_item(f"Global mood: {name}", action_map, exit_label=_BACK)


def _create() -> None:
    _, tag_session = open_and_set_global_database_sessions()
    name = questionary.text("Name of the new global mood:").ask()
    if not name:
        print("Cancelled.")
        return
    try:
        create_global_mood(tag_session, name)
    except MoodError as exc:
        _error(exc)
        return
    print(f"Created '{name.strip()}'. Leina Radio sees it after the next copy for sharing.")


def global_moods() -> None:
    _, tag_session = open_and_set_global_database_sessions()
    _apply_phone_changes(tag_session)
    while True:
        moods = list_global_moods(tag_session)
        choices = [questionary.Choice(title=f"{name} - {count} song(s)", value=(mood_id, name))
                   for mood_id, name, count in moods]
        if not moods:
            print("No global moods yet.")
        picked = questionary.select("Global moods:", choices=choices + [_CREATE, _BACK]).ask()
        if picked is None or picked == _BACK:
            return
        if picked == _CREATE:
            _create()
            continue
        _global_mood_menu(*picked)


# --------------------
# Users' moods
# --------------------

def _promote(username: str, mood_id: int, name: str, count: int) -> str | None:
    _, tag_session = open_and_set_global_database_sessions()
    # The user's song ids must be current before they're copied into tag.db
    try:
        apply_song_id_changes(force=True)
    except Exception as exc:
        slog(f"Updating song references failed: {exc}", priority=3)
    existing = {n: c for _, n, c in list_global_moods(tag_session)}
    if name in existing:
        print(f"There's already a global mood '{name}' ({existing[name]} songs): {username}'s {count} "
              "song(s) are merged into it.")
    else:
        print(f"This makes a new global mood '{name}' with {username}'s {count} song(s).")
    print(f"The mood leaves {username}'s own moods; their Leina Radio switches to the global one at its next sync.")
    if not ask_yes_no("Promote it?"):
        return None
    try:
        global_id, global_name, merged, added = promote_user_mood(tag_session, username, mood_id)
    except (MoodError, NotMigratedError) as exc:
        _error(exc)
        return None
    print(f"'{global_name}' (#{global_id}) is now a global mood"
          + (f"; {added} new song(s) merged in." if merged else f" with {added} song(s)."))
    return _BACK


def _user_mood_menu(username: str, mood_id: int, name: str, count: int) -> None:
    action_map = {
        "Show songs": lambda: _show_songs(user_mood_song_ids(username, mood_id), display_songs),
        "Show song links": lambda: _show_songs(user_mood_song_ids(username, mood_id), display_song_links),
        "Promote to a global mood": lambda: _promote(username, mood_id, name, count),
    }
    execute_menu_item(f"{username}'s mood: {name}", action_map, exit_label=_BACK)


def users_moods() -> None:
    username = _pick_user("Whose moods?")
    if not username or username == _BACK:
        return
    while True:
        try:
            moods = list_user_moods(username)
        except NotMigratedError:
            _error(f"{username}'s database hasn't got the moods yet - restart MusicDatabase with the data drive plugged in.")
            return
        if not moods:
            print(f"{username} has no moods yet.")
            return
        choices = [questionary.Choice(title=f"{name} - {count} song(s)" + (", cover" if cover else ""),
                                      value=(mood_id, name, count))
                   for mood_id, _, name, count, cover in moods]
        picked = questionary.select(f"{username}'s moods:", choices=choices + [_BACK]).ask()
        if picked is None or picked == _BACK:
            return
        _user_mood_menu(username, *picked)


# --------------------
# The phones' changes to global moods
# --------------------

def _when(ms) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M") if isinstance(ms, int) else "?"


_RESULT_LABELS = {
    global_mood_log.RESULT_ADDED: "added",
    global_mood_log.RESULT_REMOVED: "removed",
    global_mood_log.RESULT_UNCHANGED: "no change",
    global_mood_log.RESULT_MOOD_GONE: "mood gone - skipped",
    global_mood_log.RESULT_SONG_GONE: "song gone - skipped",
}


def show_phone_changes() -> None:
    """Who added or removed which song in which global mood, newest first -
    to undo one, use Global moods -> the mood -> Add a song / Remove songs."""
    if not users_available():
        _error(f"The changes are kept with the users, which aren't available: {users_unavailable_message()}")
        return
    music_session, tag_session = open_and_set_global_database_sessions()
    _apply_phone_changes(tag_session)
    pending = pending_global_mood_changes()
    if pending:
        print(f"{len(pending)} change(s) couldn't be applied yet and are still waiting.")
    history = global_mood_history()
    if not history:
        print("No changes from Leina Radio yet.")
        return
    shown = history[:_HISTORY_SHOWN]
    ids = {r.get("song_id") for r in shown} | {r.get("requested_song_id") for r in shown}
    songs = {s.id: s for s in music_session.query(Song).filter(Song.id.in_([i for i in ids if i])).all()}
    moods = {mood_id: name for mood_id, name, _ in list_global_moods(tag_session)}

    table = Table(title=f"Changes to global moods from Leina Radio (newest first, {len(shown)} of {len(history)})")
    for column, style in (("Sent", None), ("Applied", None), ("User", "cyan"), ("Change", None),
                          ("Mood", "green"), ("Song", "magenta"), ("Result", None)):
        table.add_column(column, style=style)
    for r in shown:
        song = songs.get(r.get("song_id")) or songs.get(r.get("requested_song_id"))
        song_text = f"{_song_label(song)} (#{song.id})" if song else f"#{r.get('song_id') or r.get('requested_song_id')}"
        mood_text = moods.get(r.get("mood_id"), f"{r.get('mood_name')} (deleted)")
        table.add_row(_when(r.get("received_at")), _when(r.get("applied_at")), str(r.get("user")),
                      str(r.get("action")), mood_text, song_text,
                      _RESULT_LABELS.get(r.get("result"), str(r.get("result"))))
    Console().print(table)
    print(f"Full log: {global_mood_log.HISTORY_FILENAME} in {settings.global_mood_changes_dir}")


def moods_menu() -> None:
    action_map = {
        "Global moods": global_moods,
        "Users' moods": users_moods,
        "Changes from Leina Radio": show_phone_changes,
    }
    execute_menu_item("Moods", action_map, exit_label=_BACK)


# --------------------
# Permissions
# --------------------

def permissions_menu() -> None:
    """Tick what a user may do; it takes effect on their phone's next request."""
    username = _pick_user("Whose permissions?")
    if not username or username == _BACK:
        return
    try:
        current = set(user_permissions(username))
        picked = questionary.checkbox(
            f"Permissions of {username} (space ticks, enter saves):",
            choices=[questionary.Choice(title=label, value=name, checked=name in current)
                     for name, label in PERMISSION_LABELS.items()],
        ).ask()
        if picked is None:
            print("Nothing changed.")
            return
        saved = set_permissions(username, picked)
    except (InvalidUserError, UsersUnavailableError, OSError) as exc:
        _error(exc)
        return
    granted, revoked = set(saved) - current, current - set(saved)
    if not granted and not revoked:
        print("Nothing changed.")
        return
    for name in sorted(granted):
        print(f"Granted: {PERMISSION_LABELS[name]}")
    for name in sorted(revoked):
        print(f"Revoked: {PERMISSION_LABELS[name]}")
