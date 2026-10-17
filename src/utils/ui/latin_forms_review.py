"""Manage database -> "Fill in Latin forms": every artist / album / song
whose name has non-Latin letters and no Latin form yet (utils/database/
latin_names.py). First one checkbox list of the automatic suggestions to
accept, then, if wanted, the rest one by one - kanji and the like have no
suggestion and need typing. Nothing is stored that the owner didn't tick
or type."""

import questionary
from utils.ui.prompts import ask_yes_no
from rich import print
from rich.markup import escape

from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.latin_names import (
    ALBUM,
    ARTIST,
    SONG,
    LatinCandidate,
    latin_of,
    missing_latin_forms,
    name_of,
    set_latin_form,
    suggest_latin_for,
)

_KIND_LABELS = {ARTIST: "Artists", ALBUM: "Albums", SONG: "Songs"}


def _describe(candidate: LatinCandidate) -> str:
    return f"{candidate.name}  ->  {candidate.suggestion}   ({candidate.kind}, {candidate.context})"


def _pick_kinds(session) -> tuple | None:
    counts = {kind: len(missing_latin_forms(session, (kind,))) for kind in _KIND_LABELS}
    if not any(counts.values()):
        return ()
    choices = [questionary.Choice(f"{label} ({counts[kind]} without one)", value=kind, checked=bool(counts[kind]))
               for kind, label in _KIND_LABELS.items()]
    picked = questionary.checkbox("Which names do you want to go through?", choices=choices).ask()
    return None if picked is None else tuple(picked)


def _accept_suggestions(candidates: list[LatinCandidate]) -> set:
    """One checkbox list of the suggestions; returns the indexes saved."""
    offered = [i for i, c in enumerate(candidates) if c.suggestion]
    if not offered:
        return set()
    print("\n" + "=" * 70)
    print(f"{len(offered)} name(s) have a suggested Latin form. Tick the ones to save")
    print("(space toggles, a toggles all, i inverts, enter confirms). The rest can be typed next.")
    print("=" * 70)
    choices = [questionary.Choice(title=_describe(candidates[i]), value=i) for i in offered]
    selected = questionary.checkbox("Save these Latin forms:", choices=choices).ask()
    if not selected:
        return set()
    for i in selected:
        set_latin_form(candidates[i].row, candidates[i].suggestion)
    submit_global_database_session()
    print(f"Saved {len(selected)} Latin form(s).")
    return set(selected)


def _type_the_rest(candidates: list[LatinCandidate]) -> int:
    """One by one, each saved as soon as it's typed. Returns how many."""
    print("Type each Latin form (the suggestion, if any, is filled in). "
          "Leave it empty to skip that one, Ctrl+C to stop.")
    saved = 0
    for position, candidate in enumerate(candidates, 1):
        print(f"\n[{position}/{len(candidates)}] [yellow]{escape(candidate.name)}[/yellow]  "
              f"[dim]({candidate.kind}, {escape(candidate.context)})[/dim]")
        typed = questionary.text("Latin form:", default=candidate.suggestion or "").ask()
        if typed is None:
            break
        if not typed.strip():
            continue
        set_latin_form(candidate.row, typed)
        submit_global_database_session()
        saved += 1
    return saved


def fill_latin_forms() -> None:
    session, _ = open_and_set_global_database_sessions()
    kinds = _pick_kinds(session)
    if kinds is None:
        return
    candidates = missing_latin_forms(session, kinds) if kinds else []
    if not candidates:
        print("Every name in another alphabet already has a Latin form.")
        return

    accepted = _accept_suggestions(candidates)
    rest = [c for i, c in enumerate(candidates) if i not in accepted]
    if not rest:
        return
    without = sum(1 for c in rest if not c.suggestion)
    if not ask_yes_no(f"Go through the other {len(rest)} one by one ({without} without a suggestion)?"):
        return
    saved = _type_the_rest(rest)
    print(f"Saved {saved} Latin form(s).")


def edit_latin_form(obj) -> None:
    """Set or clear one Song / Artist / Album's Latin form from an editor.
    Caller shows the row; this asks and commits."""
    name, current = name_of(obj), latin_of(obj)
    start = current or suggest_latin_for(obj) or ""
    typed = questionary.text(f'Latin form of "{name}" (empty = none):', default=start).ask()
    if typed is None:
        print("Aborted")
        return
    old = set_latin_form(obj, typed)
    submit_global_database_session()
    print(f'Latin form of "{escape(name)}": {escape(old or "---")} --> {escape(latin_of(obj) or "---")}')
