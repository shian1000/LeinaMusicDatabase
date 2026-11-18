"""Manage database -> "Soundtrack works": the list of films, games and anime a
song may be credited to instead of who sings it ("Nana"), with no "OST" in the
name saying so (utils/database/soundtrack_albums.py). The matchers then find a
song credited to one among every artist's songs whose album names it, and
Fill missing data -> Albums offers "<work> OST" for a song of an artist of
that name."""
import questionary

from utils.common.output import error, say
from utils.database.database_sessions import require_global_database_sessions, submit_global_database_session
from utils.database.datatables import SoundtrackWork
from utils.database.soundtrack_albums import add_soundtrack_work, soundtrack_work_named
from utils.ui.prompts import ask_yes_no, choose

_BACK = "Back"
_ADD = "Add a film, game or anime"
_NAME = "Change the name"
_ALIASES = "Change its other spellings"
_DELETE = "Remove from the list"


def manage_soundtrack_works() -> None:
    session, _ = require_global_database_sessions()
    while True:
        works = sorted(session.query(SoundtrackWork).all(), key=lambda w: w.name.lower())
        choice = choose("Soundtrack works", [
            *[questionary.Choice(f"{w.name}" + (f" ({w.aliases})" if w.aliases else ""), value=w) for w in works],
            questionary.Choice(_ADD, value=_ADD)], back=_BACK)
        if choice is None:
            return
        if choice == _ADD:
            _ask_new_work(session)
        else:
            _edit_work(session, choice)


def _ask_new_work(session) -> SoundtrackWork | None:
    name = (questionary.text("The film's, game's or anime's name:").ask() or "").strip()
    if not name:
        return None
    aliases = (questionary.text("Other spellings, comma-separated (optional):").ask() or "").strip()
    try:
        work = add_soundtrack_work(session, name, aliases)
    except ValueError as exc:
        error(str(exc))
        return None
    submit_global_database_session()
    say(f'"{work.name}" is on the list.')
    return work


def _edit_work(session, work: SoundtrackWork) -> None:
    while True:
        print(f"\n{work.name}\n  Other spellings: {work.aliases or '—'}")
        choice = choose("What do you want to do?", [_NAME, _ALIASES, _DELETE], back=_BACK)
        if choice is None:
            return
        if choice == _DELETE:
            if ask_yes_no(f'Remove "{work.name}" from the list? Songs and albums stay as they are.'):
                session.delete(work)
                submit_global_database_session()
                return
            continue
        attribute = "name" if choice == _NAME else "aliases"
        value = (questionary.text("New value:", default=getattr(work, attribute) or "").ask() or "").strip()
        if attribute == "name" and not value:
            continue
        if attribute == "name" and (other := soundtrack_work_named(session, value)) not in (None, work):
            error(f"'{value}' is a spelling of \"{other.name}\" already")
            continue
        setattr(work, attribute, value or None)
        submit_global_database_session()
