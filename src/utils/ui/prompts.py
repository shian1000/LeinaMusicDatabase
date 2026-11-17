"""Shared prompts. Every yes/no question goes through ask_yes_no(), so a bare
Enter means yes everywhere in the app - not questionary.confirm() directly.
The answers of ask_yes_no() and tick_items() go into the decision log.

unattended(): the automation timer runs the clean-up steps with nobody to ask.
A checkbox list then takes the items it would tick at first when its built-in
`rule` is on in Settings -> "Automation", and any item whose `facts` one of
the owner's own rules for that question (`rule`) holds for; a yes/no question
is answered yes the same two ways, else no; typing and picking ask nothing.
The facts also go into the decision log with the owner's answers.
A step marking proposals as looked at marks only decided() ones, so the rest
is still asked the next time someone runs it. A long loop (a lookup song by
song) asks stop_requested() before each item and stops, keeping what it has,
once its time_limit() is up or MusicDatabase is waiting to start."""

import time
from contextlib import contextmanager
from contextvars import ContextVar

import questionary

from utils.common import decision_log
from utils.common.automation_settings import rule_on, user_rule_taking

# The items taken without asking, as (step, text), while unattended(); else None
_unattended = ContextVar("prompts_unattended", default=None)
# time.monotonic() after which an unattended loop stops (time_limit())
_deadline = ContextVar("prompts_deadline", default=None)


@contextmanager
def unattended():
    """Inside, nothing is asked (see the module docstring). Yields the list of
    what checkbox lists took, (step, label text) each, as it fills."""
    taken = []
    token = _unattended.set(taken)
    try:
        yield taken
    finally:
        _unattended.reset(token)


def unattended_now() -> bool:
    return _unattended.get() is not None


@contextmanager
def time_limit(seconds: float):
    """Unattended loops inside stop once `seconds` are up (stop_requested())."""
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def app_is_waiting() -> bool:
    """Unattended: MusicDatabase is starting and waits for the automation."""
    from utils.database.app_lock import app_waiting
    return _unattended.get() is not None and app_waiting()


def stop_requested() -> bool:
    """Whether an unattended loop should stop now, keeping what it has: its
    time_limit() is up, or MusicDatabase waits to start. Never with someone
    there - they stop with Ctrl+C."""
    if _unattended.get() is None:
        return False
    deadline = _deadline.get()
    return (deadline is not None and time.monotonic() >= deadline) or app_is_waiting()


def decided(items: list, picked: list) -> list:
    """The items answered: all of them when someone answered the list, only
    the `picked` ones when nobody did (unattended())."""
    return items if _unattended.get() is None else list(picked)


def _taken_by(rule: str | None, facts: dict | None, built_in: bool) -> str | None:
    """Unattended: what takes an item - "" for the built-in rule `rule`
    (`built_in`: the item is one it takes), the name of the owner's rule
    holding for `facts`, or None."""
    if rule is None:
        return None
    if built_in and rule_on(rule):
        return ""
    return user_rule_taking(rule, facts) if facts else None


def _report(label: str, by: str) -> tuple:
    return decision_log.current_step(), f"{label}  [rule: {by}]" if by else label


def ask_yes_no(message: str, rule: str | None = None, about: str | None = None,
               facts: dict | None = None) -> bool | None:
    """Ask a yes/no question; Enter alone answers yes. None when cancelled
    (Ctrl+C), which callers treat like no. Unattended: yes when the built-in
    `rule` is on or one of the owner's rules holds for `facts`, reported as
    `about` (what it changes; else the question)."""
    taken = _unattended.get()
    if taken is not None:
        by = _taken_by(rule, facts, True)
        if by is not None:
            taken.append(_report(about or message, by))
        return by is not None
    answer = questionary.confirm(message, default=True).ask()
    if answer is not None:
        decision_log.record("yes_no", question=message, answer=answer, **({"rule": rule} if rule else {}),
                            **({"facts": facts} if facts else {}))
    return answer


def tick_items(question: str, items: list, label, checked=False, rule: str | None = None,
               facts=None) -> list | None:
    """One checkbox list of `items`, each shown as `label(item)` (text or
    prompt_toolkit tokens) and ticked at first when `checked` - a bool, or a
    function of the item. Returns the ticked items in their order, None when
    cancelled (Ctrl+C). Unattended: those ticked at first when the built-in
    automation rule `rule` is on, and those `facts(item)` (a dict of
    constants.AUTOMATION_QUESTIONS[rule]'s facts) make one of the owner's
    rules hold for."""
    is_checked = checked if callable(checked) else lambda _item: checked
    taken = _unattended.get()
    if taken is not None:
        picked = []
        for item in items:
            by = _taken_by(rule, facts(item) if facts else None, is_checked(item))
            if by is not None:
                picked.append(item)
                taken.append(_report(decision_log.plain(label(item)), by))
        return picked
    choices = [questionary.Choice(title=label(item), value=i, checked=is_checked(item))
               for i, item in enumerate(items)]
    picked = questionary.checkbox(question, choices=choices).ask()
    if picked is None:
        return None
    decision_log.record("tick", question=question, **({"rule": rule} if rule else {}), items=[
        {"label": decision_log.plain(choice.title), "proposed": bool(choice.checked), "picked": choice.value in picked,
         **({"facts": facts(items[choice.value])} if facts else {})}
        for choice in choices])
    return [items[i] for i in picked]


def type_each(items: list, describe, question: str, save, default=None) -> int:
    """One text question per item, below `describe(item)` (rich markup) and
    its position; `save(item, typed)` for each answer - empty skips that item,
    Ctrl+C stops. Returns how many were saved. `default(item)` fills it in."""
    from utils.common.output import show
    if _unattended.get() is not None:
        return 0
    saved = 0
    for position, item in enumerate(items, 1):
        show(f"\n[{position}/{len(items)}] {describe(item)}")
        typed = questionary.text(question, default=default(item) if default else "").ask()
        if typed is None:
            break
        if typed.strip():
            save(item, typed)
            saved += 1
    return saved


def ask_typed(label: str, question: str, shown: str | None = None) -> str | None:
    """Show `shown` (else `label`), put `label` on the clipboard to search
    for and ask for a value; None, after saying so, when nothing is typed."""
    from utils.common.text_utils import copy_to_clipboard
    if _unattended.get() is not None:
        return None
    print(shown or label)
    copy_to_clipboard(label)
    value = input(question).strip()
    if not value:
        print(f"Skipped {label}")
    return value or None


def choose(question: str, choices: list, back: str = "Back"):
    """questionary.select with `back` as the last entry; the picked value, or
    None for `back` and Esc / Ctrl+C. `choices`: texts or questionary.Choice."""
    if _unattended.get() is not None:
        return None
    # The same kind of entry as the rest: a text among texts, else a Choice
    back_entry = back if all(isinstance(c, str) for c in choices) else questionary.Choice(title=back, value=back)
    picked = questionary.select(question, choices=[*choices, back_entry]).ask()
    return None if picked in (None, back) else picked
