"""Picking one of Leina Radio's users - one version for Users and Users ->
Moods, so the same question behaves the same in both menus."""

import questionary

from utils.database import users as users_db
from utils.common.output import RED, RESET

BACK = "Back"


def show_error(message) -> None:
    print(f"{RED}{message}{RESET}")


def usernames_or_explain() -> list[str] | None:
    """Every username, or None after saying why there are none (the users
    folder unavailable, or no users yet)."""
    if not users_db.users_available():
        show_error(f"The users folder isn't available: {users_db.users_unavailable_message()}")
        return None
    names = users_db.list_usernames()
    if not names:
        print('No users yet - create one with "Create a new user".')
        return None
    return names


def pick_user(question: str) -> str | None:
    """A username, or None for Back, Esc / Ctrl+C or no users."""
    names = usernames_or_explain()
    if not names:
        return None
    picked = questionary.select(question, choices=names + [BACK]).ask()
    return None if picked in (None, BACK) else picked
