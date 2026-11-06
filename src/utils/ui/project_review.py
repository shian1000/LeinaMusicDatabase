"""Manage database -> "Projects": the dictionary of series recordings were made
for (utils/database/projects.py), the stored songs naming one in one checkbox
list, and Song actions -> "Set project"."""
import questionary

from utils.common.output import error, review_header, say
from utils.database.database_sessions import require_global_database_sessions, submit_global_database_session
from utils.database.datatables import Project, Song
from utils.database.projects import (
    add_project,
    apply_project_proposal,
    delete_project,
    forget_known_projects,
    project_proposals,
    project_song_counts,
    set_song_project,
)
from utils.ui.display_utils import display_songs
from utils.ui.prompts import ask_yes_no
from utils.ui.song_colors import song_tokens

_BACK = "Back"
_ADD = "Add a project"
_FIND = "Find stored songs that name a project"
_SHOW = "Show its songs"
_NAME = "Change the name"
_ALIASES = "Change its other spellings"
_CHANNELS = "Change the YouTube channels publishing it"
_DELETE = "Remove from the dictionary"
_NEW = "A new project"
_NONE = "No project"
_CANCEL = "Cancel"


def _save_dictionary() -> None:
    submit_global_database_session()
    forget_known_projects()


def _ask_new_project(session) -> Project | None:
    name = (questionary.text("Project's name:").ask() or "").strip()
    if not name:
        return None
    aliases = (questionary.text("Other spellings, comma-separated (optional):").ask() or "").strip()
    channels = (questionary.text("YouTube channels publishing it that aren't its artists, comma-separated (optional):")
                .ask() or "").strip()
    try:
        project = add_project(session, name, aliases, channels)
    except ValueError as exc:
        error(str(exc))
        return None
    _save_dictionary()
    return project


def _edit_project(session, project: Project) -> None:
    while True:
        print(f"\n{project.name}\n  Other spellings: {project.aliases or '—'}\n  Channels: {project.channels or '—'}")
        choice = questionary.select("What do you want to do?",
                                    choices=[_SHOW, _NAME, _ALIASES, _CHANNELS, _DELETE, _BACK]).ask()
        if choice in (None, _BACK):
            return
        if choice == _SHOW:
            songs = session.query(Song).filter(Song.project_id == project.id).all()
            display_songs(songs)
            if not songs:
                say("No songs yet.")
        elif choice == _DELETE:
            if ask_yes_no(f'Remove "{project.name}" from the dictionary? Its songs stay, with no project.'):
                say(f"{delete_project(session, project)} song(s) no longer have a project.")
                _save_dictionary()
                return
        else:
            attribute = {_NAME: "name", _ALIASES: "aliases", _CHANNELS: "channels"}[choice]
            value = (questionary.text("New value:", default=getattr(project, attribute) or "").ask() or "").strip()
            if value or attribute != "name":
                setattr(project, attribute, value or None)
                _save_dictionary()


def review_project_proposals() -> None:
    """The stored songs naming a project, in one checkbox list. The ones the
    names settle are ticked; those read by a guess aren't."""
    session, _ = require_global_database_sessions()
    proposals = project_proposals(session)
    if not proposals:
        say("No stored song names a project from the dictionary.")
        return
    review_header(f"{len(proposals)} song(s) name a project",
                  "Ticked ones get the project, and the artist and title shown after the arrow.",
                  "Unticked: the artist is only a guess, or the names don't say who it is.")
    choices = []
    for i, proposal in enumerate(proposals):
        note = "artist unknown" if proposal.artist_unknown else "whole set" if proposal.whole else None
        title = [*song_tokens(proposal.song.artist.name, proposal.song.title), ("", "  ->  "),
                 *song_tokens(proposal.artist, proposal.title, note, proposal.project.name)]
        choices.append(questionary.Choice(title=title, value=i, checked=not proposal.guessed))
    picked = questionary.checkbox("Which songs do you want to mark?", choices=choices).ask()
    if not picked:
        say("Nothing picked.")
        return
    for i in picked:
        apply_project_proposal(session, proposals[i])
    submit_global_database_session()
    say(f"Marked {len(picked)} song(s).")


def manage_projects() -> None:
    session, _ = require_global_database_sessions()
    while True:
        counts = project_song_counts(session)
        projects = sorted(session.query(Project).all(), key=lambda p: p.name.lower())
        choice = questionary.select("Projects", choices=[
            *[questionary.Choice(f"{p.name} ({counts.get(p.id, 0)})", value=p) for p in projects],
            questionary.Choice(_ADD, value=_ADD), questionary.Choice(_FIND, value=_FIND),
            questionary.Choice(_BACK, value=_BACK)]).ask()
        if choice in (None, _BACK):
            return
        if choice == _ADD:
            _ask_new_project(session)
        elif choice == _FIND:
            review_project_proposals()
        else:
            _edit_project(session, choice)


def set_project_of_songs(songs: list) -> None:
    """Song actions -> "Set project", and the song editor for one song. The
    title stays as it is: names are cleaned from the list of proposals."""
    session, _ = require_global_database_sessions()
    display_songs(songs)
    projects = sorted(session.query(Project).all(), key=lambda p: p.name.lower())
    choice = questionary.select(f"Which project are these {len(songs)} song(s) from?", choices=[
        *[questionary.Choice(p.name, value=p) for p in projects],
        questionary.Choice(_NEW, value=_NEW), questionary.Choice(_NONE, value=_NONE),
        questionary.Choice(_CANCEL, value=_CANCEL)]).ask()
    if choice in (None, _CANCEL):
        say("Aborted")
        return
    project = _ask_new_project(session) if choice == _NEW else None if choice == _NONE else choice
    if choice == _NEW and project is None:
        return
    set_song_project(songs, project)
    submit_global_database_session()
    say(f"{len(songs)} song(s): {project.name if project else 'no project'}.")
