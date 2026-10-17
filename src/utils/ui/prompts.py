"""Shared prompts. Every yes/no question goes through ask_yes_no(), so a bare
Enter means yes everywhere in the app - not questionary.confirm() directly."""

import questionary


def ask_yes_no(message: str) -> bool | None:
    """Ask a yes/no question; Enter alone answers yes. None when cancelled
    (Ctrl+C), which callers treat like no."""
    return questionary.confirm(message, default=True).ask()
