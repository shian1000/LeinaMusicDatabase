"""Shared prompts. Every yes/no question goes through ask_yes_no(), so a bare
Enter means yes everywhere in the app - not questionary.confirm() directly."""

import questionary


def ask_yes_no(message: str) -> bool | None:
    """Ask a yes/no question; Enter alone answers yes. None when cancelled
    (Ctrl+C), which callers treat like no."""
    return questionary.confirm(message, default=True).ask()


def tick_items(question: str, items: list, label, checked=False) -> list | None:
    """One checkbox list of `items`, each shown as `label(item)` (text or
    prompt_toolkit tokens) and ticked at first when `checked` - a bool, or a
    function of the item. Returns the ticked items in their order, None when
    cancelled (Ctrl+C)."""
    is_checked = checked if callable(checked) else lambda _item: checked
    choices = [questionary.Choice(title=label(item), value=i, checked=is_checked(item))
               for i, item in enumerate(items)]
    picked = questionary.checkbox(question, choices=choices).ask()
    return None if picked is None else [items[i] for i in picked]


def ask_typed(label: str, question: str, shown: str | None = None) -> str | None:
    """Show `shown` (else `label`), put `label` on the clipboard to search
    for and ask for a value; None, after saying so, when nothing is typed."""
    from utils.common.text_utils import copy_to_clipboard
    print(shown or label)
    copy_to_clipboard(label)
    value = input(question).strip()
    if not value:
        print(f"Skipped {label}")
    return value or None
